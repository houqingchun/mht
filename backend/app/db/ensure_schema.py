"""校对「这个库里的表」是不是这一版程序要的形状，必要时把 Alembic 的版本戳盖上。
`python -m app.db.ensure_schema`

**为什么需要它**（2026-09-18 加）：安装时那一问答到 3（「库和表我都建好了」）时，
操作员的建表语句是人写的，库里**没有 `alembic_version`**——那是 Alembic 自己的版本
记录表，只有 Alembic 会建。而安装脚本无条件跑 `alembic upgrade head`（那是刻意的，
见 `Read-DatabaseMode`：表结构归这一版程序）。任务表已经存在时，迁移会在第一条
`ALTER TABLE … ADD COLUMN` 上撞 `Duplicate column name`，报出来是一句英文 MySQL 错，
离真正的原因（「你的表是按别的版本建的」）很远。

这里做的事就是「先看一眼，再决定要不要让 Alembic 动手」。它**先看表、后看版本表**，
这个顺序是有意的（反过来会漏掉几种状态，见 `decide` 的表）。

## 判据只比表名与列名，**不**比类型

这一点是刻意的克制，不是没写完。**列名对得上不等于表结构没问题**：类型、可空性、
主外键、索引、字符集都不在判据里，所以这里说「一致」只覆盖到「列都在」。
要连着类型一起比，得用 `alembic.autogenerate.compare_metadata`，而 MySQL 的反射噪音
（`tinyint(1)` vs `BOOLEAN`、`VARCHAR(255)` vs `String(255)`、server default）会把一份
本来能用的表判成不一致——**宁可漏报，不要误杀**：误杀会把一台本来装得上的机器拦在
第 4 步，而漏报的那部分 alembic 自己会在下一步撞出来。

## 它不建表、不删行，只可能写一行 `alembic_version`

`stamp` 走的是 `alembic.command.stamp`（`MigrationContext.stamp`），没有那本账时它会
先建出那张表再写一行版本号。**裸 `INSERT INTO alembic_version` 是不行的**：那等于把
当前 head 的形状**又抄了一份**进来，而 head 每次加迁移都会变（CLAUDE.md §6 那条
「阈值随规则版本走」的同一条道理：唯一出处不许有第二份）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Engine, create_engine, inspect

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403 —— 不 import 的话 Base.metadata 是空的

# `backend/app/db/ensure_schema.py` → parents[2] 就是 `backend/`（同 `reset_to_baseline`）。
BACKEND_DIR = Path(__file__).resolve().parents[2]

VERSION_TABLE = "alembic_version"

# 版本表的四种状态。**不是布尔量**：这四种的处置各不相同，而「有没有」这一问
# 答不了「有但是空的」和「有但是个我不认识的版本」。
ABSENT = "absent"  # 表不在——手写的 DDL 都是这一种
EMPTY = "empty"  # 表在、一行都没有
UNKNOWN = "unknown"  # 表在、有版本号，但那串 revision 不在 backend/alembic/versions/ 里
KNOWN = "known"  # 认得出来

# `decide` 的结论。
BUILD = "build"  # 交给下一步的 alembic 去建表
STAMP = "stamp"  # 表对得上，但缺版本戳——给它盖一个
PASS_ = "pass"  # 什么都不用做
BLOCK = "block"  # 停下，逐条说清哪里对不上


@dataclass(frozen=True)
class SchemaDiff:
    """实际表结构与 `Base.metadata` 的差集。只到列名这一层，见模块开头。"""

    missing_tables: list[str] = field(default_factory=list)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    extra_columns: dict[str, list[str]] = field(default_factory=dict)
    # 一共对上了几张表。**「这个库还是空的」与「这个库缺了几张表」是两件事**，而
    # `missing_tables` 一个字段答不出前者——它答不了「缺的是全部还是几张」。
    present_tables: int = 0

    @property
    def has_missing(self) -> bool:
        """缺表或缺列。**任一更早的版本都必然落进这里**——0006/0009/0010/0011 四份迁移
        全是加列（`grep -n add_column alembic/versions/*.py`），所以「表结构是旧的」在
        列名这一层一定看得见。"""
        return bool(self.missing_tables or self.missing_columns)

    @property
    def exactly_matches(self) -> bool:
        """连多出来的列都没有。盖章（`stamp`）用的就是这一条：那是在**宣称**
        「你的表 == head」，而多出来的列同样是版本不一致的证据（0011 就 drop 过
        `student.birth_date`）。"""
        return not self.has_missing and not self.extra_columns


def compare_schema(inspector, metadata) -> SchemaDiff:
    """把库里实际有的表与列，和 `metadata`（= `Base.metadata`）比一比。

    纯函数：只读 `inspector`，不碰数据库。`test_ensure_schema.py` 拿两份临时的小
    metadata，在一个一次性 MySQL 库上逐种情形钉它。**这里的方言不能换**：`inspect()`
    的行为逐方言不同，而它只比表名与列名正是为了躲开 MySQL 的反射噪音
    （CLAUDE.md §18「选 3」那一节），那条判断该由 MySQL 来验。
    """
    actual_tables = set(inspector.get_table_names())
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    extra_columns: dict[str, list[str]] = {}
    present = 0

    for table in metadata.sorted_tables:
        if table.name not in actual_tables:
            missing_tables.append(table.name)
            continue
        present += 1
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        expected = {column.name for column in table.columns}
        if expected - actual:
            missing_columns[table.name] = sorted(expected - actual)
        if actual - expected:
            extra_columns[table.name] = sorted(actual - expected)

    return SchemaDiff(missing_tables, missing_columns, extra_columns, present)


def read_version_state(connection, script: ScriptDirectory) -> tuple[str, str | None]:
    """`alembic_version` 现在是哪种状态，以及认出来的是那个 revision。

    **`script.get_revision()` 找不到时抛的是 `CommandError`，不是返回 `None`**——
    按「返回 None 就是认不出」写的话，`UNKNOWN` 这个状态根本到不了（真机上撞过：
    报出来是外层那句「连不上数据库或者读不动表结构：CommandError: Can't locate
    revision identified by '0099_from_the_future'」，把「版本认不出」说成了「连不上」）。
    """
    if VERSION_TABLE not in set(inspect(connection).get_table_names()):
        return ABSENT, None
    rows = connection.exec_driver_sql(f"SELECT version_num FROM {VERSION_TABLE}").fetchall()
    if not rows:
        return EMPTY, None
    revision = str(rows[0][0])
    try:
        script.get_revision(revision)
    except CommandError:
        return UNKNOWN, revision
    return KNOWN, revision


def decide(version_state: str, revision: str | None, head: str | None, diff: SchemaDiff) -> str:
    """给「版本表的状态 × 表结构比对结果」判一个结论。

    **先看表、后看版本表**——这一条是这张表存在的理由。反过来写（「版本表在就放行」）
    会漏掉三种真实状态：版本表在而一张应用表都没有（导 DDL 导了一半）、版本表在而是空的
    （alembic 会从 0001 重跑，撞 `Table already exists`）、版本表说 head 而表结构不是
    head（alembic 空转，`seed` 在一个错的表结构上跑完，屏幕上写着「安装完成」）。
    第三种正是 `Read-DatabaseMode` 那段注释里说的「登录页打得开、别的页面 500」。
    """
    if version_state == EMPTY:
        return BLOCK  # alembic 会从 0001 重跑一遍，撞 Table already exists
    if version_state == UNKNOWN:
        return BLOCK  # alembic 报 Can't locate revision identified by '…'

    if version_state == ABSENT:
        if diff.present_tables == 0:
            return BUILD  # 一张应用表都没有：交给下一步的迁移去建
        if diff.exactly_matches:
            return STAMP  # 手写的 DDL，形状与 head 一致 —— 差的就是那本账
        return BLOCK

    # 认得出的版本号（KNOWN）。表一张都没建出来是一种自相矛盾的状态：导 DDL 导了一半。
    if diff.present_tables == 0:
        return BLOCK
    if revision == head:
        # 说 head 就得真的是 head：alembic 在这里一步都不会走，它是唯一帮不上忙的场合，
        # 而接下来 `seed` 会在这个错的表结构上跑完，屏幕上写着「安装完成」。
        return BLOCK if diff.has_missing else PASS_
    # 比 head 旧：正常升级路径，**不拿 head 的形状判它死刑**——那样会误杀每一次正常升级。
    return PASS_


def alembic_config() -> Config:
    """建一个指向本仓库 alembic 目录的 Config。

    **不用 `Config("alembic.ini")`**：`script_location = alembic` 是相对**当前工作目录**
    解析的（`ScriptDirectory.from_config` 把那个字符串直接交给 `coerce_resource_to_filename`，
    不拼 ini 所在的目录）。安装脚本恰好把工作目录设成了 `backend\\`，所以它能work——
    但那是别人的实现细节，不该由我们依赖。这里从 `__file__` 推绝对路径。

    `env.py` 自己会 `config.set_main_option("sqlalchemy.url", …)`，所以连接串来自
    `.env`，与 `alembic upgrade head` 完全同源。这里再设一次是**把 `%` 转义掉**：
    ConfigParser 默认走 `BasicInterpolation`，而 `.env` 里的口令是百分号编码过的
    （`p@ss` → `p%40ss`），一个 `%` 会让 `set_main_option` 当场抛
    `ValueError: invalid interpolation syntax`——报出来与真正的原因毫无关系。
    """
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return config


def main(argv: list[str] | None = None) -> int:
    config = alembic_config()
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()

    engine: Engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            version_state, revision = read_version_state(connection, script)
            diff = compare_schema(inspect(connection), Base.metadata)
    except Exception as exc:  # noqa: BLE001 —— 原因由驱动给，原样带出来比我猜一句准
        print(f"[X] 连不上数据库或者读不动表结构：{type(exc).__name__}: {exc}")
        print("    如果你选的是「库和表我自己建好了」，请先确认库名、账号与建表都做完了。")
        print("    《部署说明.txt》里「数据库和表我自己建好了」那一节写了这件事。")
        return 1
    finally:
        engine.dispose()

    action = decide(version_state, revision, head, diff)

    if action == BUILD:
        print(f"[OK] 这个库里还没有这套系统的表，交给下一步的迁移建出来（head = {head}）。")
        return 0

    if action == PASS_:
        if revision and head and revision != head:
            # 比 head 旧：正常升级路径。**只在这一种情形下多说一句**——它有一个真实的坑，
            # 而 alembic 自己报出来的东西解释不了它。
            if not diff.has_missing:
                print(
                    f"[!] 版本表记的是 {revision}，而库里已经有 head（{head}）要的全部列。"
                )
                print(
                    "    如果接下来「执行数据库迁移」撞了 Duplicate column name，就是这个原因："
                )
                print(
                    "    MySQL 的 DDL 不在事务里，前面几条 ALTER 已经落地而版本号没有前进——"
                )
                print("    重跑会在第一条上继续撞。那时候请人工核对表结构，别再反复重跑。")
        print(f"[OK] 表结构对得上，不用动它（{VERSION_TABLE} = {revision or '（没有版本表）'}）。")
        return 0

    if action == BLOCK:
        _print_block(version_state, revision, head, diff)
        return 1

    # STAMP：表对得上，只是缺那本账。
    try:
        command.stamp(config, "head")
    except Exception as exc:  # noqa: BLE001
        from app.db.create_database import explain

        print(f"[X] 给这个库盖版本戳失败了：{explain(exc)}")
        print(f"    它在 {VERSION_TABLE} 上写一行版本号，需要这个账号有建表/写表权限。")
        return 1
    print(f"[OK] 你的表结构与这一版对得上，只往 {VERSION_TABLE} 里写了一行（{head}）。")
    print("     那是 Alembic 自己的版本记录表，你的业务数据一行都没有动。")
    return 0


def _print_block(version_state: str, revision: str | None, head: str | None, diff: SchemaDiff) -> None:
    """停下时把「哪里对不上」逐条念出来，并给一条能照着办的出路。

    **两种「版本账坏了」与两种「表结构坏了」的出路是不同的，不能共用一段。**
    前者（表在而空、版本号认不出）要的是**把那一行/那张表清掉**，让下一次运行重新校对
    ——它们与表结构无关；后者要的是**改成这一版的表结构**。第一版把两段并在一起，
    于是屏幕上出现了自相矛盾的两句：「手动 INSERT 一行」紧接着「别往 DDL 里塞
    alembic_version」。真机上两种都验过。
    """
    if version_state in (EMPTY, UNKNOWN):
        if version_state == EMPTY:
            print(f"[X] {VERSION_TABLE} 表在，可是里面一行都没有。")
            print("    这种库跑迁移会从第 1 号迁移重来一遍，撞一句英文的 Table already exists。")
            print("    多半是这份 DDL 是从别处整份导过来的——**`mysqldump --no-data` 会把这张表")
            print("    一起导出来**，而它是空的（`--no-data` 不导行）。")
        else:
            print(f"[X] {VERSION_TABLE} 里记的版本是 {revision!r}，这一版程序里没有这个迁移。")
            print("    多半是这份库来自另一个版本（更新的一版，或者别的程序）。")
            print(f"    这一版程序认得的最新版本是 {head}。")
        print()
        print("    处置（只动 Alembic 那一本账，你的业务表一行都不碰）：")
        print(f"        DROP TABLE {VERSION_TABLE};")
        print("    然后重跑一次安装——安装器会重新校对表结构，对得上就替你把这本账写对。")
        print("    如果这个库是**别的程序**在用的，请换一个库，别在这里清。")
        return

    if version_state == KNOWN:
        print(f"[X] 这个库的表结构与这一版程序对不上（版本表说 {revision}）。")
        _print_diff(diff)
    else:
        print("[X] 这个库里的表与这一版程序要的对不上。")
        _print_diff(diff)

    print()
    print("    直接跑迁移会在第一条 ALTER TABLE 上撞一句英文的 MySQL 错（Duplicate column")
    print("    name / Table already exists）。请先把它改成这一版的表结构。**最省事的办法**：")
    print("    在另一台机器上按「选 1」装一次，用你建表时用的那个工具（MySQL Workbench /")
    print("    Navicat / phpMyAdmin / mysqldump 都行）把那个库的**表结构**导出来（只要结构、")
    print("    不要数据），再导进这个库。")
    print(f"    **别把 {VERSION_TABLE} 这张表一起导进来**——一份空的就够让安装器以为这个库的")
    print("    表已经归 Alembic 管，从此不再校对。用 mysqldump 的话就是：")
    print(f"        mysqldump --no-data --skip-add-drop-table \\")
    print(f"            --ignore-table=<库名>.{VERSION_TABLE} <那个库> > schema.sql")
    print("    《部署说明.txt》「数据库和表我自己建好了」那一节写了这件事。")


def _print_diff(diff: SchemaDiff) -> None:
    for name in diff.missing_tables:
        print(f"    缺整张表：{name}")
    for name, columns in sorted(diff.missing_columns.items()):
        print(f"    {name} 缺列：{', '.join(columns)}")
    for name, columns in sorted(diff.extra_columns.items()):
        print(f"    {name} 多出这些列：{', '.join(columns)}")
    print("    （只比了表名与列名，类型/可空性/索引不在判据里。）")


if __name__ == "__main__":
    sys.exit(main())
