"""生成 `backend/sql/seed_mysql8.sql`——库里「系统基础数据 + 管理员账号」的**纯 DML** 脚本。

## 它为什么存在

客户拿到安装包时，库里什么都没有。三条路走到「能用」：

    一键安装（选 1） / 安装后跑三条命令 / `schema_mysql8.sql`

第三条路上，`schema_mysql8.sql` **只建表**。建完表还得有 admin 才能登录，而
`python -m app.db.seed` 要 Python。这份文件补的正是那一断：**只有 INSERT，不建库也不建表**，
给一台装了 mysql 客户端、却没有（或不想跑）Python 的机器用。

## ★ 它不是第二个出处：数据来自 `seed.py` 的**结果**，不是它的**副本**

在 SQL 里再抄一份基线必然漂移，而一份抄错的规则 JSON 会让那个库的评分与别处不一样、
且看不出来（CLAUDE.md §6：阈值随规则版本走）。这句话逐字写在
`backend/sql/reset_to_baseline.sql` 的 docstring 里，是那个文件**只删不建**的理由；
这个文件是它的正面，所以只能用同一个办法绕开：**真的跑一遍，再从库里读出结果**。

    DROP/CREATE DATABASE <主库名>_init
      → alembic upgrade head                     子进程，env 带 XLP_DATABASE_URL
      → python -m app.db.seed                    同上
      → python -m app.db.reset_to_baseline --yes 同上
      → 读全部非空表 → 编译 INSERT
      → DROP DATABASE（finally）

数据里有 **id 引用**（`user_scope.school_id` / `scale_question.scale_id` / `scale_rule.scale_id`），
它们是 `seed.py` 里 `db.flush()` 的**执行结果**，不是它的代码——纯 Python 路要自己重新实现
「谁拿到 id 几」。题干也一样：它们躺在 `data/mht_scale.json` 里，由 seed 读进库。

**「只留 admin + 量表与规则」这个终点由 `reset_to_baseline` 独家保证**：这个文件不知道终点
是什么（它遍历 `Base.metadata.sorted_tables`，非空就 dump），所以将来终点变了，文件自动跟上。

## ★ 代价：生成它需要连一台活着的 MySQL

`build_migration_sql.py` 是纯文件操作；这一个不是。上面那一段就是原因。所以
`make db-seed-sql` 需要在有 MySQL 的开发机上跑，失败时会给一句中文而不是 traceback。

**但它不在出包时重新生成**（与 `upgrade_from_v1_0_0.sql` 不同）。那条之所以每次重生成，
是因为它的两个来源都长在**当前源码树**上；这一份多了一个**外部来源**（一个库），出包时
重生成反而会**掩盖**「有人改了 seed 却没重跑 `make db-seed-sql`」——那个信号应该由守卫红在
那次 `make test` 上。它与 `schema_mysql8.sql` 同一档：**仓库里的快照，随 `backend/` 进包，
靠守卫保鲜。**

## ★ 三处「每次都会变」的值被固定下来，判据才回到逐字节

数据段天生不可能逐字节复现。三处，各有处置，集中成下面那张 `NORMALIZED` 表（每条带理由）：

| 非确定源 | 为什么每次不同 | 处置 |
|---|---|---|
| `user_account.password_hash` | `hash_password()` = bcrypt + **随机盐** | 换成一个固定常量 |
| `assessment_scale.published_at` | `seed.py` 写 `datetime.now(UTC)` | 换成一个固定常量 |
| 各表的 `created_at` / `updated_at` | `server_default=DEFAULT (now())`，MySQL 在 seed 那一刻填 | **INSERT 里不写这几列**，交给你那个库的默认值 |

第三条的判据是「`server_default` 的文本里含 `now()` / `CURRENT_TIMESTAMP`」，**不是**
「凡是有 `server_default` 的列都不写」——后者会漏掉 `must_change_password`
（`server_default="0"` 而 seed 写 `False`）这种列，症状是客户库里的值悄悄变成默认值。
仓库里「时间戳必须是 `DEFAULT (now())`」本来就是 `schema_mysql8.sql` 的一条静态守卫
（CLAUDE.md §16），所以这个形状是准的。

规范化之后，主守卫就是 §30 那条最简最强也最便宜的形状：`compose(...)` 的产物与盘上那份
**逐字节相同**。

## 一行一条 `INSERT`

不是多行 `VALUES`（`mysqldump` 默认那种）。两个理由：多行 `VALUES` 编译出来是**一整行**，
想按行切开就得写一个懂单引号的词法器（§18 记着：半吊子词法器错的时候是无声的）；而一行
一条语句时，`ERROR 1062 Duplicate entry` 报出的**行号**直接指到本文件里那一行。代价是
106 条语句而不是 6 条——这个体量上无所谓。

## 编码

UTF-8 **不带 BOM**、LF。它由 `mysql --default-character-set=utf8mb4 < 本文件` 执行，
BOM 会让第一行变成 `\\xef\\xbb\\xbf-- …`，客户端报一句**第 1 行的语法错误**。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SQL_DIR = BACKEND / "sql"
DIST_DIR = ROOT / "dist"

# 生成器要 import `app.models`（拿 `Base.metadata.sorted_tables`）与 `app.db.mysql_url`
# （拿连接参数），两者都在 `backend/` 下。`deploy/build_package.py:57` 是同一个做法。
sys.path.insert(0, str(BACKEND))

from sqlalchemy import bindparam, create_engine, select, types as sqltypes  # noqa: E402
from sqlalchemy.dialects import mysql  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.mysql_url import parse_database_url  # noqa: E402
from app.models import *  # noqa: E402,F401,F403

OUTPUT_NAME = "seed_mysql8.sql"
INIT_SUFFIX = "_init"
CHARSET = "utf8mb4"
COLLATION = "utf8mb4_0900_ai_ci"

# 种子四个账号的口令。这不是「再抄一份哈希」——哈希来自库里那一行；这一串只用来
# **验**它（`verify_password(SEED_PASSWORD, <库里的哈希>)`），把「客户能不能登录」
# 变成一条可执行的判据，而不是一句承诺。
SEED_PASSWORD = "123456"

# ★ 这两条横线**必须自己带着 `-- ` 前缀**，不能只存一串 `=` / `-`。
# MySQL 的注释规则是「`--` 后面必须跟空白或控制字符」才算注释（`#` 没有这个要求），
# 所以一条光秃秃的 `=======` 在 `mysql` 客户端眼里是一条 **SQL 语句**，而不是分隔线：
# 整份文件会在**第 1 行**报
#
#     ERROR 1064 … near '=====…' at line 1
#
# ——离真正的原因（「文件头那条横线没注释掉」）隔着一整个文件。而它更坏的一种表现是
# **只错一半**：`---` 开头的那几条同样不是注释，但它们后面紧跟的是中文说明，
# 于是报错位置会落在某一个 `-- ✗ …` 上，看起来像那一句话写错了。
# 宽度 78 是照 `schema_mysql8.sql` 的骨架取的（它写成 `-- ` + 75 个字符）。
RULE = "-- " + "=" * 75
LINE = "-- " + "-" * 75

# 文件头与每张表的标题里都会写一句「这张表为什么留着」。缺一张表的说明时 `compose`
# 会当场报错——一份「有行却没有理由」的基线，下一个人读不懂它能不能删。
BASELINE_NOTES: dict[str, str] = {
    "school": "一所学校：admin 的 user_scope 要指它，学生导入链路也按 code 取它",
    # 这一条**不许**写成「口令见上面那一行」：它在产物里落两处（文件头那张表的
    # 第 2 行、以及这张表自己的小节标题），而两处的「上面那一行」一个是空行、
    # 一个是另一个表的标题——读者照着它找不到任何东西。指名字那一节，
    # 口令本身仍然只有一个出处（产物里「管理员账号」那一节）。
    "user_account": "管理员账号（account = admin，初始口令见文件头「管理员账号」一节）",
    "user_scope": "admin 的范围行：§9 里没有范围行的账号，每个列表都是空的",
    "assessment_scale": "已发布的 MHT 量表版本：新建测评任务时选的就是它",
    "scale_question": "题库：题干来自 data/mht_scale.json（与题库导入读的是同一个文件）",
    "scale_rule": "评分规则：总分 / 维度分段与效度阈值都随这一行走（CLAUDE.md §6）",
}

# (表, 列) → (固定下来的值, 理由)。理由会**逐字**写进产物里那一节——它同时是文档
# 和可被守卫检查的对象。
NORMALIZED: dict[tuple[str, str], tuple[Any, str]] = {
    ("user_account", "password_hash"): (
        "$2b$12$gdRtQy2WVTKXQuO3siSuHerKmjipd8law6F4GoJv6yNueP1HRlWNe",
        "`hash_password()` 用的是 `bcrypt.gensalt()`，盐是随机的，所以每次跑 seed 出来的"
        "哈希都不一样。不固定的话这份文件就没有身份：每生成一次 git diff 都变，"
        "而「它是不是过期的」也判不出来。值验过：verify_password('123456', <它>) 为真。",
    ),
    ("assessment_scale", "published_at"): (
        datetime(2026, 9, 20, 0, 0, 0),
        "`seed.py` 写的是 datetime.now(UTC)，跑一次变一次。固定成常量——**它不是真实"
        "发布时间**，只是这份交付文件里那条量表版本的发布时间。这一列在界面上有读者"
        "（量表页的「发布时间」），所以文件头要把它说明白。",
    ),
}


# ---------------------------------------------------------------------------
# 渲染（纯函数，不碰库也不落盘）
# ---------------------------------------------------------------------------


def _dialect():
    """编译 INSERT 用的方言。

    `identifier_preparer._double_percents = False` 是必须的，而且**只有这一个写法生效**：
    MySQL 的 paramstyle 是 `format`，SQLAlchemy 在编译时把 `%` 翻倍成 `%%`（那是给
    DBAPI 的占位符机制留的），而这份文本要写进文件、不会经过任何 DBAPI。不关掉它，
    题干里一个 `100%` 到了客户库上就是 `100%%`。
    `dialect._double_percents` 这个属性**不存在**，设它没有任何效果。
    """
    dialect = mysql.dialect()
    dialect.identifier_preparer._double_percents = False
    return dialect


def _is_runtime_timestamp(column) -> bool:
    """这一列的值是不是「由 MySQL 在 insert 那一刻生成」。

    判据是 `server_default` 的**文本**里含 `now()` / `current_timestamp`，不是
    「有没有 server_default」。见模块 docstring 里那张表下面的一段。
    """
    default = column.server_default
    if default is None:
        return False
    rendered = str(getattr(default, "arg", "")).lower()
    return "now()" in rendered or "current_timestamp" in rendered


def _refuse_unrenderable(table: str, column: str, value: str) -> None:
    """拒绝**渲染出来会变形**的字符串，而不是想个办法绕过去。

    MySQL 的字符串字面量处理器**不转义反斜杠**：`a\\b` 会渲染成 `'a\\b'`，而 MySQL 把
    `\\b` 读成 `b` ——静默少一个字符。（`'` 是转义的，`'a''b'`，没问题。）
    要正确转义得写 `\\\\`，而那只在目标的 `sql_mode` **不含** `NO_BACKSLASH_ESCAPES`
    时才成立——那个我们不知道，猜错的方向正是静默变形。

    换行与回车另算：它们能让一条语句跨行，于是「一行一条 INSERT」这条性质没了，
    而 `ERROR 1062` 报出来的行号（这个文件选一行一条语句的全部理由）也就不再指得准。

    今天这六张表里的值一个都没有（实测过）。真有的时候，正确的处置是回来看
    `seed.py` / `data/mht_scale.json` 那个值该不该这么写——不是在这里加一层转义。
    """
    bad = [name for name, char in (("反斜杠", "\\"), ("NUL", "\x00"), ("换行", "\n"), ("回车", "\r")) if char in value]
    if bad:
        raise SystemExit(
            f"[X] {table}.{column} 的值里有{'、'.join(bad)}，这份文件渲染不了它。\n"
            f"    MySQL 的字符串字面量处理器不转义反斜杠，换行 / 回车会破坏"
            f"「一行一条 INSERT」。\n    实测今天库里没有这样的值——所以是有人刚写进去的，"
            f"请先看那个值该不该这么写。"
        )


def _render_value(table_name: str, column, value: Any, index: int):
    """把一个 Python 值变成 INSERT 里那一格——直接给 SQLAlchemy 编译，不手写转义。"""
    key = (table_name, column.name)
    if key in NORMALIZED:
        value = NORMALIZED[key][0]
    if value is None:
        return None
    if isinstance(value, str):
        _refuse_unrenderable(table_name, column.name, value)
    if isinstance(column.type, sqltypes.JSON):
        # `literal_binds=True` 在 JSON 列上会抛 `CompileError: No literal value renderer
        # is available`。绕法是先把 Python 对象序列化成**文本**，再以 `String` 的名义
        # 交给编译器——走的仍然是 SQLAlchemy 的类型系统与引号处理，没有手写转义。
        # `ensure_ascii=False`：题干与规则里全是中文，转成 `\\uXXXX` 也能跑，但那份文件
        # 就没法读了——而「客户能读、能核对」是它存在的理由之一。
        text_ = json.dumps(value, ensure_ascii=False)
        _refuse_unrenderable(table_name, column.name, text_)
        return bindparam(f"p{index}", text_, type_=sqltypes.String())
    return value


def insert_statement(table, row: dict, index: int) -> str:
    """一行一条 `INSERT`，末尾带分号。"""
    values = {
        column.name: _render_value(table.name, column, row[column.name], index)
        for column in table.columns
        if not _is_runtime_timestamp(column)
    }
    statement = table.insert().values(**values)
    sql = str(statement.compile(dialect=_dialect(), compile_kwargs={"literal_binds": True}))
    return f"{sql};"


def _version() -> str:
    """`1.1.2`。与 `build_package.write_package_info` 读的是同一个文件。

    **不走 import**：`app/version.py` 里明写着「不许有任何 import」（setuptools 的
    `attr:` 静态读它）。正则读一行字符串没有那个风险，也不需要把 `backend/` 塞进 path。
    """
    source = (BACKEND / "app" / "version.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if found is None:
        raise SystemExit("[X] 读不出 backend/app/version.py 里的 __version__")
    return found.group(1)


def compose(version: str, blocks: dict[str, list[dict]]) -> str:
    """★ 纯函数：给两个输入，拼出整份文本。一个字节都不落盘。

    守卫必须调**它**（或下面那个只差版本号的 `render`），不能调 `build()`——`build()`
    先写快照、再写目标，所以「拿 `build(tmp)` 生成一份再与盘上那份比」是恒真的
    （CLAUDE.md §30）。

    表的清单与次序来自 `Base.metadata.sorted_tables`（父先子后的外键序），**不是**写死的
    六张：`blocks` 里有哪几张就渲染哪几张。文件头那张表也是从 `blocks` 现数出来的，
    所以它不可能与正文对不上。
    """
    tables = {table.name: table for table in Base.metadata.sorted_tables}
    unknown = sorted(set(blocks) - set(tables))
    if unknown:
        raise SystemExit(f"[X] 这些表不在模型里：{'、'.join(unknown)}——模型与库对不上了")
    missing_note = sorted(set(blocks) - set(BASELINE_NOTES))
    if missing_note:
        raise SystemExit(
            f"[X] 这几张表有数据、却没有在 BASELINE_NOTES 里说明为什么留着："
            f"{'、'.join(missing_note)}。加一句再生成——一份说不出理由的基线，"
            f"下一个人不知道它能不能删。"
        )

    parts = [_header(version, blocks)]
    for table in Base.metadata.sorted_tables:
        rows = blocks.get(table.name)
        if not rows:
            continue
        parts += ["", "", _table_section(table, rows)]
    parts += ["", "", _tail(blocks), ""]
    text = "\n".join(parts)
    refuse_fake_comments(text)
    return text


def render(blocks: dict[str, list[dict]]) -> str:
    """`compose` 的公开入口：版本号由**这一层**补上，调用方只需要给数据。

    存在的理由是守卫：它要调一个纯函数来比对产物，而版本号从哪儿取是这一层的细节
    （`_version()` 读的是 `backend/app/version.py` 那一行字符串，**不走 import**，见它
    自己的 docstring）。让守卫自己再正则读一次就是第二处定义——`version.py` 换个写法时
    两边各错各的，而产物照旧生成得出来。

    ★ 它和 `compose` 一样是**纯函数**，一个字节都不落盘。守卫走这里（或直接走
    `compose`），**不走 `build()`**——理由见 `build()` 的 docstring（CLAUDE.md §30）。
    """
    return compose(_version(), blocks)


def refuse_fake_comments(text: str) -> None:
    """★ 产物里每一行看着像注释的，都必须**真的是**注释。

    MySQL 的注释规则比它看起来严格：**`--` 后面必须跟空白或控制字符**（`#` 没有这个
    要求）。所以下面这些都以「一条 SQL 语句」的身份进到客户端：

        ==========        ← 一条没带 `-- ` 的横线（`RULE` / `LINE` 曾经就是这样）
        --（中文…         ← `--` 后面紧跟全角括号，中间没有空格
        --✗               ← 同上

    后果不是「少了一条注释」，是**整份文件跑不起来**，而且报错位置离原因很远：
    光秃秃的横线报在第 1 行（`ERROR 1064 … near '=====…'`），而 `--（` 那一种报在
    那一行自己身上（`near '--（学生账号…'`）——**看起来像那句话写错了**，于是下一个
    人会去改文案，而文案是对的。这两种形状 2026-09-21 在真机上各撞过一次。

    这条**由生成器自己保证**，不留给守卫：`compose` 是这份文件的**唯一写入方**，而
    「写出去的东西读得回来」是写入方的责任（CLAUDE.md §18 那条同源）。守卫再抄一份
    就又是两处定义。

    三条判据，各管一类：

    1. `lstrip()` 之后以 `--` 开头、且 `--` 之后既不是空白也不是行尾；
    2. `lstrip()` 之后整行**只由 `=` 或 `-` 组成**（横线，可能带一个结尾分号）——
       这类行的判据收窄到我们真的会生成的那两种字符，**不是**一条通用的「哪些行
       像注释」的词法判据：那种判据判错一次就再没人信它（CLAUDE.md §18 里那个
       剥壳器的两版自相矛盾，就是同一种半吊子词法器的下场）；
    3. `#`。它不在此列（不需要空格），但这份文件一条都不用，所以发现它也一并报
       出来，免得下一个人以为它和 `--` 一样宽松。

    **名字不带下划线是有意的**：守卫 `test_seed_sql.py` 直接调它，逐字钉住这三类
    形状还认得出来——这条检查本身也是产品的一部分（它是那次真机故障的修复），
    而一条没人能单独验的检查，改坏了不会有任何东西红。
    """
    offenders: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        rule = stripped.rstrip(";").strip()
        if stripped.startswith("--") and stripped[2:3] not in ("", " ", "\t"):
            offenders.append(f"    第 {number} 行：{line[:60]}")
        elif len(rule) >= 3 and len(set(rule)) == 1 and rule[0] in "=-":
            offenders.append(f"    第 {number} 行（一条没带 `-- ` 的横线）：{line[:60]}")
        elif stripped.startswith("#"):
            offenders.append(f"    第 {number} 行（`#` 不是本文件的注释风格）：{line[:60]}")
    if offenders:
        raise SystemExit(
            "[X] 产物里有几行「看着像注释、其实是一条 SQL」——整份文件会当场报 1064：\n"
            + "\n".join(offenders)
            + "\n    `--` 后面必须跟一个空格（MySQL 的规矩，`#` 才不需要）。"
        )


def _header(version: str, blocks: dict[str, list[dict]]) -> str:
    total_tables = len(Base.metadata.sorted_tables)
    written = len(blocks)
    rows = sum(len(items) for items in blocks.values())

    listing = [
        f"--   {name:<24} {len(blocks[name]):>4} 行   {BASELINE_NOTES[name]}"
        for name in (table.name for table in Base.metadata.sorted_tables)
        if name in blocks
    ]
    normalized = [
        f"--   {table + '.' + column:<34} {reason}"
        for (table, column), (_, reason) in NORMALIZED.items()
    ]

    body = [
        RULE,
        "-- 心晴：数据库**基础数据**脚本（只有管理员账号与量表，不含任何演示数据）",
        RULE,
        "--",
        f"-- 版本：V{'.'.join(version.split('.')[:2])}（{version}）",
        "-- 生成：`make db-seed-sql`（`deploy/build_seed_sql.py`）。**不要手改这个文件**——",
        "--       改了下次重跑就没了，而且 `backend/app/tests/test_seed_sql.py` 会红。",
        "--",
        LINE,
        "-- ★ 这份文件里只有 INSERT，一句 DDL 都没有：它不建库、也不建表。",
        LINE,
        "--",
        "-- 表结构归 `alembic upgrade head`，或者包里的 `backend\\sql\\schema_mysql8.sql`。所以",
        "-- 顺序是：",
        "--",
        "--     mysql … < schema_mysql8.sql     ← 建表（或者在有 Python 的机器上跑 alembic）",
        "--     mysql … < seed_mysql8.sql       ← 本文件，写基础数据",
        "--",
        "-- **本文件不写 `alembic_version`。** 那张表是 Alembic 自己的版本记录，只由它创建；",
        "-- 在一份 `schema_mysql8.sql` 建出来的库上它还不存在，在一份迁移建出来的库上它已经",
        "-- 有一行——两种情况下往里面 INSERT 都是一个错（1146 / 1062），而它们看起来都像",
        "-- 「这个文件坏了」。补那一行的正确做法是在安装目录里跑：",
        "--",
        "--     runtime\\venv\\Scripts\\python.exe -m app.db.ensure_schema",
        "--",
        "-- 它认得出「表建好了但没有版本戳」，补上之后再 `alembic upgrade head` 就无事可做。",
        "--",
        LINE,
        "-- 它写了哪些表（这一块由生成器从**实际数据**里数出来，不是手写的）",
        LINE,
        "--",
        *listing,
        "--",
        f"-- 其余 {total_tables - written} 张业务表一行都不写：名册、员工账号、测评任务、",
        "-- 一切测评与关怀记录全空。开通之后的第一件事是「组织学生 → 学生信息导入」",
        "-- （学生账号跟着名册一起生成，不在「账号与权限」里建），不是往这个文件里加行。",
        "--",
        "-- `system_setting` 与 `role_permission` 也一行都不写：全新库上它们本来就是 0 行",
        "-- （配置回退 `settings_service.DEFAULTS`、权限回退 `CAPABILITY_DEFAULTS`，",
        "-- CLAUDE.md §4 / §5 的 fail-safe）。一个库上它们有几行，取决于那个库的操作员",
        "-- 配过什么、管理员保存过几次权限矩阵。",
        "--",
        f"-- 全部 {written} 张表、{rows} 行，止于 `python -m app.db.reset_to_baseline --yes`",
        "-- 的终点——也就是说：跑完 `python -m app.db.seed` 再做一遍清理之后**剩下来的那些行**。",
        "-- 这个终点不在这里定义，由那个脚本独家保证，所以它变了这个文件自动跟上。",
        "--",
        LINE,
        "-- 管理员账号",
        LINE,
        "--",
        f"--     account = 'admin'      初始口令 = {SEED_PASSWORD}",
        "--     不强制下次登录改密（`must_change_password = false`）",
        "--",
        "-- 改密码的出路有两条：在界面上自己改（右上角「修改密码」），或者在安装目录里双击",
        "-- 「重置管理员密码.bat」。**重装一遍不会重置它**——升级只更新程序文件与表结构，",
        "-- 凡是你已经配好、正在用的东西它都不碰。",
        "--",
        LINE,
        "-- 三处「每次都会变」的值，在这里是固定下来的",
        LINE,
        "--",
        "-- 这一节是**必须读**的：下面这些值不是真实值，是为了让这份文件可复现而固定的。",
        "--",
        *normalized,
        "--",
        "--   （各表的 created_at / updated_at 另属一类：它们根本没有出现在 INSERT 里，",
        "--     由你那个库的 `DEFAULT (now())` 填。语义上这样才对——那两列回答的是",
        "--     「这一行什么时候落进**这个**库」，而不是「它当初是在谁的机器上生成的」。）",
        "--",
        LINE,
        "-- 怎么跑",
        LINE,
        "--",
        "--     mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p 你的库名 \\",
        "--           < seed_mysql8.sql",
        "--",
        "-- `--default-character-set=utf8mb4` 是必须的：这个文件里有中文（校名、题干、",
        "-- 规则里的说明），客户端按别的编码读会得到一串乱码，而它**看起来像导入成功**。",
        "--",
        "-- 整个过程是一个事务：中间任何一条报错，客户端就会停下并断开，事务随之回滚。",
        "-- **不要**加 `--force`——它会跳过错误继续跑，那正是这里最不能要的行为。",
        "--",
        "-- **重复执行会报错，不会重复写。** 用的是普通 `INSERT`，不是 `INSERT IGNORE`、",
        "-- 也不是 `REPLACE`：第二遍会在第一张表上撞 `ERROR 1062 Duplicate entry`。这是有意的",
        "-- ——静默跳过会让人以为「跑过了」，而 `REPLACE` 会删掉旧行再插一行新的，把一串",
        "-- 外键指着的主键 id 换掉。报出来的**行号**就在这个文件里（每行一条 INSERT）。",
        "--",
        "-- 想要一份「清空重来」的脚本，那是另一个文件：`backend\\sql\\reset_to_baseline.sql`。",
        "--",
        "-- 跑完之后应当能：admin 登录 → 建测评任务时选得到 MHT 量表 → 组织学生里导入名册。",
        RULE,
        "",
        "START TRANSACTION;",
    ]
    return "\n".join(body)


def _table_section(table, rows: list[dict]) -> str:
    return "\n".join(
        [
            LINE,
            f"-- {table.name} · {len(rows)} 行 —— {BASELINE_NOTES[table.name]}",
            LINE,
            *(insert_statement(table, row, index) for index, row in enumerate(rows, start=1)),
        ]
    )


def _tail(blocks: dict[str, list[dict]]) -> str:
    """收尾那句「跑完看一眼」。

    形状照 `reset_to_baseline.sql` 的 §七：一条 `UNION ALL` 链，一次问遍全部 34 张业务表。

    **分号只挂最后一行**：`mysql` 客户端按 `;` 切语句，给中间某一行挂上分号，那条链就断在
    那里——而剩下的几行会各自当成一条独立的 `SELECT` 继续跑，**看起来像成功**（客户端不会
    报错，只是输出比预期少了几行）。所以这里不是「每行结尾都补一个分号」的写法。
    """
    counts = [
        f"SELECT '{table.name}' AS 表, COUNT(*) AS 行数 FROM {table.name}"
        for table in Base.metadata.sorted_tables
    ]
    query = [counts[0], *(f"UNION ALL {line}" for line in counts[1:])]
    query[-1] = f"{query[-1]};"
    return "\n".join(
        [
            "COMMIT;",
            "",
            LINE,
            "-- 跑完看一眼",
            LINE,
            "--",
            "-- 预期：上面那几行有数（校名 / 题干 / 规则各一条起），其余全 0。",
            "-- 这张表里**没有 `alembic_version`**：在一份按 `schema_mysql8.sql` 建出来的库上",
            "-- 它还不存在，一条 `UNION ALL` 引到它就会 1146——而这条查询排在 COMMIT **之后**，",
            "-- 那个错会带着「数据已经写进去了」一起出现。补版本戳的是 `app.db.ensure_schema`。",
            "--",
            *query,
        ]
    )


# ---------------------------------------------------------------------------
# 取数（有副作用：建库 / 迁移 / seed / 清理 / 读 / 删）
# ---------------------------------------------------------------------------


def _run(command: list[str], url: str, label: str) -> str:
    """在 `backend/` 下起一个子进程，把库指到 `url`。

    环境变量而不是参数：`XLP_DATABASE_URL` 走的正是 `core/config.py` 那一条读法
    （pydantic-settings 里真实环境变量**优先于** `.env`），而 `backend/.env` 在
    开发机上通常不存在——`env_file` 缺失是被**静默**跳过的（CLAUDE.md §18 第 4 条）。
    `cwd=BACKEND` 两个用途：让 `.env` 找得到，让 `python -m app.…` 找得到 `app`。

    `url` 为空时**不设那个环境变量**，而不是设成空串：`XLP_DATABASE_URL=` 是一条**读得出来
    但是空的**配置，pydantic 会拿它覆盖 `.env`，于是「读数据库连接串」这一步读到的是空
    ——而它本该读的正是 `.env` 里那一份（CLAUDE.md §18：写入方与读取方对「空」的理解必须由
    写入方保证）。
    """
    import os

    env = {**os.environ}
    if url:
        env["XLP_DATABASE_URL"] = url
    done = subprocess.run(command, cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        # 库那一行走 `_masked`：连接串里带着口令，而失败信息是要贴进 issue / 日志的那一类
        # 文本（CLAUDE.md §18：口令不进日志）。它同时也是**本地开发库**的口令。
        raise SystemExit(
            f"[X] {label} 失败（退出码 {done.returncode}）：{' '.join(command)}\n"
            f"    库：{_masked(url)}\n{(done.stderr or done.stdout or '').strip()}"
        )
    return done.stdout or ""


def _masked(url: str) -> str:
    """给**人看**的连接串：口令换成 `***`。

    与 `str(URL)` 恰好相反——那个默认隐藏口令，而它藏得**太深**了：`_safety_checked_names`
    里真的拿 `str(url)` 去连过库（见那一处的 ★）。所以这里显式地做一次替换，而不是
    「不处理就等于安全」：这句话在两种写法上都成立，方向相反。
    """
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def _base_database_url() -> str:
    """库连接串从 `core/config.py` 那**唯一**一处定义拿，不在生成器里再写一份默认值。

    走子进程是因为要的是「装着 `.env` 的那个目录里跑出来的结果」——生成器自己
    `import` 的话，`env_file=".env"` 会相对**生成器**的当前工作目录解析，而那多半是
    仓库根，于是 `.env` 被静默跳过、回落成 `root:password@127.0.0.1`。
    """
    out = _run(
        [
            sys.executable,
            "-c",
            "from app.core.config import get_settings; print(get_settings().database_url)",
        ],
        url="",
        label="读数据库连接串",
    )
    url = out.strip()
    if not url:
        raise SystemExit("[X] 读出来的数据库连接串是空的")
    return url


def _safety_checked_names(base_url: str) -> tuple[str, str]:
    """算出要建 / 要删的那**一个**库名，并先把三件事断言掉。

    这个函数是这份生成器里唯一会 `DROP DATABASE` 的地方，所以判据写在它的门口而不是
    调用点：库名必须以 `_init` 结尾、必须与主库名不同、必须是 mysql。**宁可拦住**——
    指错库就是销毁一台开发机上的数据（与 `mysql_support.ensure_database` 那条
    「库名不以 `_test` 结尾就拒绝跑」是同一条口径）。
    """
    base = parse_database_url(base_url)
    if not base.database:
        raise SystemExit(f"[X] 连接串里没有库名：{_masked(base_url)}")
    init_name = f"{base.database}{INIT_SUFFIX}"
    if not init_name.endswith(INIT_SUFFIX):
        raise SystemExit(f"[X] 临时库名 {init_name!r} 不以 {INIT_SUFFIX!r} 结尾，拒绝建它")
    if init_name == base.database:
        raise SystemExit(f"[X] 临时库名与主库同名（{init_name}），拒绝")
    from sqlalchemy.engine import make_url

    # ★ `hide_password=False` **不能省**。`URL.__str__` 默认把口令渲染成 `***`
    # （`render_as_string(hide_password=True)`），那是给日志看的形状——`str(url)` 于是
    # 变成 `mysql+pymysql://root:***@…`，而下一步就是拿它去连库。送出去的**口令字面就是
    # 三个星号**，MySQL 回的是 `1045 Access denied … (using password: YES)`：
    # 与「口令配错了」逐字相同，而真正的错在「有人拿一个打印用的字符串去连了库」。
    # 这个形状在开发机上只会表现为「连不上」——排查方向会被引到 .env 与账号权限上去。
    init_url = make_url(base_url).set(database=init_name).render_as_string(hide_password=False)
    return init_name, init_url


def _recreate_database(init_url: str, init_name: str) -> None:
    """DROP + CREATE，自己写，不借 `app.db.create_database`。

    那是一个**生产**命令：往它里面加一个 `DROP DATABASE`，等于把一条破坏性语句放在
    操作员一次手滑的距离上。而 `app/tests/mysql_support.py` 里的那一份在
    `app/tests/` 下——`deploy/` 的构建脚本去 import 测试夹具是把分层倒过来。
    `alembic.env` 的写法在仓库里已经有两处各自独立实现，这里是第三处，形状一致。
    """
    import pymysql

    target = parse_database_url(init_url)
    connection = pymysql.connect(**target.connect_kwargs(with_database=False), autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{init_name}`")
            cursor.execute(
                f"CREATE DATABASE `{init_name}` "
                f"CHARACTER SET {CHARSET} COLLATE {COLLATION}"
            )
    finally:
        connection.close()


def _drop_database(init_url: str, init_name: str) -> None:
    import pymysql

    target = parse_database_url(init_url)
    connection = pymysql.connect(**target.connect_kwargs(with_database=False), autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{init_name}`")
    finally:
        connection.close()


def _read_blocks(init_url: str) -> dict[str, list[dict]]:
    """把所有非空表读出来，按主键排序（次序稳定，产物才可复现）。"""
    engine = create_engine(init_url)
    blocks: dict[str, list[dict]] = {}
    try:
        with engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                primary_key = [column.name for column in table.primary_key.columns]
                statement = select(*table.columns)
                if primary_key:
                    statement = statement.order_by(*(table.c[name] for name in primary_key))
                rows = [dict(row) for row in connection.execute(statement).mappings()]
                if rows:
                    blocks[table.name] = rows
    finally:
        engine.dispose()
    return blocks


def _verify_credentials(blocks: dict[str, list[dict]]) -> None:
    """「客户能不能登录」的可执行形式。

    两半都断：库里的那个哈希能验过 `123456`（说明 seed 写进去的是它），以及**被固定
    下来的那个常量**也能（说明渲染出去的那一份仍然能登录）。少了后半句，一次手滑改
    `NORMALIZED` 就会产出一份**谁都登不进去**的交付文件，而它长得完全正常。
    """
    rows = blocks.get("user_account", [])
    if len(rows) != 1:
        raise SystemExit(f"[X] 基线里的 user_account 应该是 1 行（admin），实际 {len(rows)} 行")

    from app.security.passwords import verify_password

    stored = rows[0]["password_hash"]
    if not verify_password(SEED_PASSWORD, stored):
        raise SystemExit("[X] 库里的 admin 哈希验不过种子口令——seed.py 的初始口令变了？")

    frozen = NORMALIZED[("user_account", "password_hash")][0]
    if not verify_password(SEED_PASSWORD, frozen):
        raise SystemExit(
            f"[X] NORMALIZED 里固定的那个哈希验不过 {SEED_PASSWORD}——"
            f"写出去的那一份会是谁都登不进去的。"
        )


def collect_seed_blocks() -> dict[str, list[dict]]:
    """真的跑一遍 seed，再把结果读回来。**这是这份生成器唯一碰库的地方。**

    临时库在 `finally` 里删掉：中途任何一步失败都不该在开发机上留一个半成品库
    （`alembic upgrade head` 之后崩掉的那一个，看起来与一个正常的库一模一样）。
    """
    base_url = _base_database_url()
    init_name, init_url = _safety_checked_names(base_url)

    _recreate_database(init_url, init_name)
    try:
        _run([sys.executable, "-m", "alembic", "upgrade", "head"], init_url, "建表（alembic upgrade head）")
        _run([sys.executable, "-m", "app.db.seed"], init_url, "写基线数据（app.db.seed）")
        # 清理这一步不是可有可无的：`seed.py` 还会建年级 / 班级 / 学生 / 三个种子员工账号 /
        # 一场测评任务，而它们**不在**用户要的那份数据里。终点由 reset_to_baseline 定义。
        _run(
            [sys.executable, "-m", "app.db.reset_to_baseline", "--yes"],
            init_url,
            "清理到基线（app.db.reset_to_baseline --yes）",
        )
        blocks = _read_blocks(init_url)
    finally:
        _drop_database(init_url, init_name)

    _verify_credentials(blocks)
    return blocks


# ---------------------------------------------------------------------------
# 落盘与命令行
# ---------------------------------------------------------------------------


def build(output_dir: Path | None = None, blocks: dict[str, list[dict]] | None = None) -> list[Path]:
    """★ 守卫**不许**调这个函数（CLAUDE.md §30）。

    它先写 `backend/sql/` 那一份、再写 `output_dir` 那一份，所以「拿 `build(tmp)` 生成一份
    再与盘上那份比」是**恒真**的——比对之前它已经把那两份变成了同一串字节。守卫走
    `compose(...)` 这个纯函数。

    `blocks` 可以传进来，是因为取一次数要跑一遍完整的建库 / 迁移 / seed / 清理（几十秒）；
    命令行那条路已经拿到了，别让它再跑一遍。
    """
    text = render(blocks if blocks is not None else collect_seed_blocks())
    targets = [SQL_DIR / OUTPUT_NAME]
    if output_dir is not None:
        targets.append(output_dir / OUTPUT_NAME)
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        # `newline="\n"`：这个文件可能被拷到 Windows 上执行，而 CRLF 在 mysql 客户端里
        # 是能跑的——但它是**生成**的，行尾跟着平台变会让「两处逐字节相同」这条判据失效。
        target.write_text(text, encoding="utf-8", newline="\n")
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成「只有系统基础数据与管理员账号」的数据库 DML 脚本",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DIST_DIR),
        help=f"除 backend/sql/ 之外再写一份到哪个目录（默认 {DIST_DIR.relative_to(ROOT)}/）",
    )
    args = parser.parse_args()

    print("这份脚本要连一台活着的 MySQL：它**真的跑一遍** seed，再从库里读结果")
    print("（在 SQL 里再抄一份基线必然漂移——见 deploy/build_seed_sql.py 的 docstring）")
    blocks = collect_seed_blocks()
    for name, rows in blocks.items():
        print(f"    {name:<24} {len(rows):>4} 行")
    print(f"  合计 {len(blocks)} 张表 / {sum(len(rows) for rows in blocks.values())} 行")
    for target in build(Path(args.output_dir), blocks):
        print(f"  {target.relative_to(ROOT)}  ({target.stat().st_size} 字节)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
