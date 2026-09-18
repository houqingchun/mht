"""表结构校对（`app/db/ensure_schema.py`）。

这个模块只在**真库**上跑（内存 sqlite 不跑 Alembic，缺口 3），所以这里测的是两件事：

1. `compare_schema` —— 纯函数，拿**两份临时的小 metadata** 在内存 sqlite 上逐种差集钉它。
   刻意不碰真实那 24 张表：那样每加一张表、每加一列都要回来改这里的期望值，而这条
   测试要钉的是「差集算得对不对」，不是「这张表长什么样」。
2. `decide` —— 那张判据表。**这是这个文件里最值钱的一半**：判据一旦写反，安装器会在
   「表结构是旧的」的库上盖一个 head 的版本戳，而之后每一处读新列的地方都 500，屏幕上
   写着「安装完成」。那些状态在真库上很难摆出来，在这里是一个参数。

`alembic_config()` 那两条是**跨目录**的：它在 `monkeypatch.chdir(tmp_path)` 下跑，
所以「不依赖当前工作目录」是被证过的，不是注释里的一句声明。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db import ensure_schema
from app.db.ensure_schema import (
    ABSENT,
    BLOCK,
    BUILD,
    EMPTY,
    KNOWN,
    PASS_,
    STAMP,
    UNKNOWN,
    SchemaDiff,
    compare_schema,
    decide,
)

# 两份小 metadata 里那张表和它的列。**`age` 是刻意挑的**：0011 迁移给 `student` 加的就是
# 它、同时 drop 了 `birth_date`——「缺列」与「多列」这两个方向在这一张表上都有真事对应。
EXPECTED_COLUMNS = ("id", "name", "age")

# `app/tests/test_ensure_schema.py` → parents[2] 就是 `backend/`。
# **不取 `ensure_schema.BACKEND_DIR`**：那是被测对象自己的坐标，拿它当子进程的 cwd
# 就成了「拿嫌疑人的证词给嫌疑人作证」——它错了这条守卫跟着一起错。
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _expected() -> MetaData:
    """这一版程序**要**的形状。"""
    metadata = MetaData()
    Table("school", metadata, Column("id", Integer, primary_key=True), Column("name", String(64)))
    Table(
        "student",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(64)),
        Column("age", Integer),
    )
    return metadata


def _actual(*, columns: tuple[str, ...] = EXPECTED_COLUMNS, tables: tuple[str, ...] = ("school", "student")) -> MetaData:
    """库里**实际**的形状。默认与 `_expected()` 一致。"""
    metadata = MetaData()
    if "school" in tables:
        Table("school", metadata, Column("id", Integer, primary_key=True), Column("name", String(64)))
    if "student" in tables:
        student_columns = {
            "id": Column("id", Integer, primary_key=True),
            "name": Column("name", String(64)),
            "age": Column("age", Integer),
            "birth_date": Column("birth_date", String(32)),  # 0011 删掉的那一列
        }
        Table("student", metadata, *(student_columns[name] for name in columns))
    return metadata


def _engine(metadata: MetaData) -> Engine:
    # StaticPool：`sqlite://` 是内存库，换一条连接就是另一个库了，而 `inspect` 会自己开连接。
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(engine)
    return engine


def _diff(*, columns: tuple[str, ...] = EXPECTED_COLUMNS, tables: tuple[str, ...] = ("school", "student")):
    engine = _engine(_actual(columns=columns, tables=tables))
    try:
        return compare_schema(inspect(engine), _expected())
    finally:
        engine.dispose()


# ---------------------------------------------------------------- compare_schema


def test_an_identical_schema_has_no_difference_at_all():
    diff = _diff()
    assert diff.missing_tables == []
    assert diff.missing_columns == {}
    assert diff.extra_columns == {}
    assert diff.present_tables == 2
    assert diff.has_missing is False
    assert diff.exactly_matches is True


def test_a_table_that_is_not_there_yet_is_reported():
    diff = _diff(tables=("school",))
    assert diff.missing_tables == ["student"]
    assert diff.present_tables == 1, "对上了几张表要数对——`decide` 靠它区分「空库」与「缺了几张」"
    assert diff.has_missing is True


def test_a_column_that_is_not_there_yet_is_reported():
    """缺列 = 这个库是**更早**的版本。0006/0009/0010/0011 四份迁移全是加列，所以
    「表结构是旧的」在列名这一层一定看得见。"""
    diff = _diff(columns=("id", "name"))
    assert diff.missing_columns == {"student": ["age"]}
    assert diff.missing_tables == []
    assert diff.exactly_matches is False


def test_a_column_that_is_not_in_this_version_any_more_is_reported():
    """多列 = 反面。0011 就 drop 过 `student.birth_date`。

    **`has_missing` 是 False 而 `exactly_matches` 是 False**——这一对是「盖章」那条判据
    的全部内容：拿 `has_missing` 去判会把这 0010 形态的表当成「对得上」。
    """
    diff = _diff(columns=("id", "name", "age", "birth_date"))
    assert diff.extra_columns == {"student": ["birth_date"]}
    assert diff.has_missing is False
    assert diff.exactly_matches is False


@pytest.mark.parametrize("module", ["app.db.ensure_schema", "app.db.check_empty"])
def test_the_module_sees_the_models_in_an_interpreter_of_its_own(module):
    """★ 这条**必须在子进程里**跑，不能在本进程里断言 `Base.metadata`。

    `Base.metadata` 是一个全局对象，而 `conftest.py` 里那句 `from app.models import *`
    在**整个 pytest 会话**里已经把它填满了。所以本进程里无论被 import 的模块顶部
    有没有那句 star import，这里都看得见 24 张表——第一版就是这么写的，变异验证
    （删掉那一行）实测仍绿：**一个不可能失败的断言**。

    而它在生产里不是一个可以忽略的差别：`python -m app.db.xxx` 是**只有这个模块被
    import** 的进程（没有 conftest、没有 `app.main`）。

    - `ensure_schema` 少了它：`Base.metadata` 空 → `present_tables` 是 0 → `decide` 判
      `BUILD` → `alembic upgrade head` 冲着手写的表去，撞 `Duplicate column name`
      ——正是这个模块存在的理由。
    - `check_empty` 少了它：`Base.metadata.tables['school']` 当场 `KeyError`，而外层那句
      `except Exception` 把它报成「读不动这个库：KeyError: 'school'」。
      **这一条不是推演出来的**：2026-09-18 在真 MySQL 上跑状态 G 时就是这么炸的，
      而当时本文件里那个只查 `ensure_schema` 的版本一个字都没红。
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {module};"
            " from app.db.base import Base;"
            " print(len(Base.metadata.sorted_tables))",
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    count = int(result.stdout.strip())
    assert count >= 20, (
        f"在一个只有 `{module}` 被 import 的解释器里，`Base.metadata` 只有 {count} 张表"
        "——顶部那句 `from app.models import *` 八成被删了"
    )


# ---------------------------------------------------------------- read_version_state
#
# 这一段拿**真的** `ScriptDirectory`（读的就是仓库里那 12 份迁移）在内存 sqlite 上跑。
# `decide` 那组用例把它当成一个入参，所以它自己的分支一条都覆盖不到——而 2026-09-18
# 真机上炸的正是其中一条：`script.get_revision()` 认不出时**抛 CommandError**，
# 于是 UNKNOWN 这个状态根本到不了，报出来是一句「连不上数据库」。


def _version_table_engine(*rows: str):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"CREATE TABLE {ensure_schema.VERSION_TABLE} (version_num VARCHAR(32) NOT NULL)")
        for row in rows:
            connection.exec_driver_sql(f"INSERT INTO {ensure_schema.VERSION_TABLE} VALUES (?)", (row,))
    return engine


def _read_state(engine):
    script = ScriptDirectory.from_config(ensure_schema.alembic_config())
    assert script.get_current_head() == HEAD, "HEAD 这个常量跟着迁移漂了，改它"
    with engine.connect() as connection:
        return ensure_schema.read_version_state(connection, script)


def test_a_missing_version_table_is_absent():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        assert _read_state(engine) == (ABSENT, None)
    finally:
        engine.dispose()


def test_an_empty_version_table_is_empty():
    engine = _version_table_engine()
    try:
        assert _read_state(engine) == (EMPTY, None)
    finally:
        engine.dispose()


def test_a_revision_this_version_knows_is_known():
    engine = _version_table_engine(HEAD, "0010_import_source")
    try:
        assert _read_state(engine) == (KNOWN, HEAD), "表里有多行时取第一行——alembic 自己的表也只有一行"
    finally:
        engine.dispose()


def test_a_revision_this_version_does_not_know_is_a_state_not_an_exception():
    """★ 真机上撞过一次：`get_revision` 认不出时抛 `CommandError`，不是返回 `None`。

    写成 `if script.get_revision(revision) is None` 的话 `UNKNOWN` 永远到不了，异常一路
    冒到 `main()` 的 `except Exception`，屏幕上出现的是
    「连不上数据库或者读不动表结构：CommandError: Can't locate revision identified by …」
    ——把「这份库来自另一个版本」说成了「连不上库」，而操作员会去查密码。
    """
    engine = _version_table_engine("0099_from_the_future")
    try:
        assert _read_state(engine) == (UNKNOWN, "0099_from_the_future")
    finally:
        engine.dispose()


def test_the_older_revision_is_the_real_one_in_the_repo():
    """`decide` 那组用例里的 `OLDER` 得真的是一个存在的迁移，否则那些用例证的是别的事。"""
    engine = _version_table_engine(OLDER)
    try:
        assert _read_state(engine) == (KNOWN, OLDER)
        assert OLDER != HEAD
    finally:
        engine.dispose()


def test_the_two_kinds_of_trouble_get_different_advice(capsys):
    """屏幕上那两种「停下」的出路必须**互斥**，不能一段话套两种情况。

    第一版把两段并在一起，于是 EMPTY 的屏幕上出现了自相矛盾的两句：
    「手动 INSERT 一行」紧跟着「别往 DDL 里塞 alembic_version」。
    """
    # 那一段「怎么改成这一版的表结构」的开头，只属于结构对不上的两种状态。
    # **判据取这个句子，不取 `--ignore-table`**：第一版取的是后者，于是「把那一段的开头
    # 换成别的一句、命令留在后面」这种走法它看不见（变异验证实测仍绿）。
    structural_marker = "第一条 ALTER TABLE"

    ensure_schema._print_block(EMPTY, None, HEAD, SchemaDiff())
    empty_text = capsys.readouterr().out
    assert "DROP TABLE" in empty_text
    assert structural_marker not in empty_text, "版本账坏了跟表结构无关，不该给「改成这一版的表结构」那段"

    ensure_schema._print_block(UNKNOWN, "0099_from_the_future", HEAD, SchemaDiff())
    unknown_text = capsys.readouterr().out
    assert "DROP TABLE" in unknown_text
    assert structural_marker not in unknown_text

    ensure_schema._print_block(
        ABSENT, None, HEAD, SchemaDiff(missing_columns={"student": ["age"]}, present_tables=1)
    )
    structural = capsys.readouterr().out
    assert "student 缺列：age" in structural
    assert structural_marker in structural
    assert "DROP TABLE" not in structural, "缺列该改的是表结构，让它去删版本表是南辕北辙"
    assert "--ignore-table" in structural, (
        "★ 补救命令少了 `--ignore-table` 就会把 `alembic_version` 的**空表**一起导进来"
        "——真机上验过：那样导出来的库落到 EMPTY 状态，照着手册做的人会再撞一次墙"
    )


# ---------------------------------------------------------------- decide
#
# 判据表。每一行都在 `decide` 里对应一条分支，改判据先来改这里。


def _match() -> SchemaDiff:
    return SchemaDiff(present_tables=2)


def _missing() -> SchemaDiff:
    return SchemaDiff(missing_columns={"student": ["age"]}, present_tables=2)


def _extra_only() -> SchemaDiff:
    return SchemaDiff(extra_columns={"student": ["birth_date"]}, present_tables=2)


def _nothing_yet() -> SchemaDiff:
    return SchemaDiff(missing_tables=["school", "student"], present_tables=0)


HEAD = "0012_drop_care_case_unique"
OLDER = "0010_import_source"


def test_a_database_without_any_table_is_left_to_the_migration():
    assert decide(ABSENT, None, HEAD, _nothing_yet()) == BUILD


def test_a_hand_written_schema_that_matches_gets_the_version_stamp():
    """这条路就是操作员要的那个「我自己写 DDL」：形状对得上，差的只是那本账。"""
    assert decide(ABSENT, None, HEAD, _match()) == STAMP


def test_a_hand_written_schema_that_does_not_match_is_never_stamped():
    """★ 最重要的一条。

    判据写成「缺列才算不一致」的话，一份 **0010 形态**的库（有 `birth_date`、没有 `age`）
    会被判「没缺东西」→ 盖章成 head → 之后每一处读 `student.age` 都 500，而屏幕上写着
    「安装完成」。多出来的列同样是版本不一致的证据。
    """
    assert decide(ABSENT, None, HEAD, _missing()) == BLOCK
    assert decide(ABSENT, None, HEAD, _extra_only()) == BLOCK


def test_an_empty_version_table_never_gets_passed_through():
    """版本表在而 0 行：放行的话 alembic 会从 0001 重跑，撞 `Table already exists`。"""
    assert decide(EMPTY, None, HEAD, _match()) == BLOCK


def test_a_revision_this_version_does_not_know_is_never_passed_through():
    """alembic 自己会报 `Can't locate revision identified by '…'`——英文，且它不知道
    「这份库来自更新的一个版本」这件事该怎么跟操作员说。"""
    assert decide(UNKNOWN, "0099_from_the_future", HEAD, _match()) == BLOCK


def test_the_version_table_saying_head_is_not_enough_on_its_own():
    """★ 判据顺序的另一半。

    「版本表说 head 就放行」会漏掉这一种：alembic 在这里一步都不会走，它是**唯一**
    帮不上忙的场合，而 `seed` 会在这个错的表结构上跑完。
    """
    assert decide(KNOWN, HEAD, HEAD, _missing()) == BLOCK
    assert decide(KNOWN, HEAD, HEAD, _match()) == PASS_


def test_a_version_table_with_no_tables_at_all_is_a_half_finished_import():
    assert decide(KNOWN, HEAD, HEAD, _nothing_yet()) == BLOCK
    assert decide(KNOWN, OLDER, HEAD, _nothing_yet()) == BLOCK


def test_an_older_revision_is_left_to_the_migration():
    """**正常升级路径，不许拿 head 的形状去判它死刑**——那样会误杀每一次真的升级。"""
    assert decide(KNOWN, OLDER, HEAD, _missing()) == PASS_
    assert decide(KNOWN, OLDER, HEAD, _match()) == PASS_


def test_only_the_head_uses_the_strict_column_comparison():
    """多出来的列在「版本比 head 旧」时只警告：更早的版本一定缺列，
    所以「只多不少」不可能是一次版本不一致造成的，多半是操作员自己加的列。"""
    assert decide(KNOWN, HEAD, HEAD, _extra_only()) == PASS_
    assert decide(KNOWN, OLDER, HEAD, _extra_only()) == PASS_


# ---------------------------------------------------------------- alembic 接入


def test_the_alembic_config_does_not_depend_on_the_current_directory(monkeypatch, tmp_path):
    """`script_location = alembic` 是相对**当前工作目录**解析的。安装脚本恰好把工作目录
    设成了 `backend\\`，所以 `Config("alembic.ini")` 也能跑——但那是别人的实现细节。
    这里换个目录跑，证明我们是从 `__file__` 推的。
    """
    monkeypatch.chdir(tmp_path)
    config = ensure_schema.alembic_config()
    from alembic.script import ScriptDirectory

    assert ScriptDirectory.from_config(config).get_current_head(), "解析不出 head"
    assert not (tmp_path / "alembic").exists(), "这条用例没有真的换出那个目录"


def test_a_password_with_a_percent_sign_survives_into_the_alembic_config(monkeypatch):
    """`.env` 里的口令是百分号编码过的（`p@ss` → `p%40ss`），而 ConfigParser 默认走
    `BasicInterpolation`：直接 `set_main_option` 会抛
    `ValueError: invalid interpolation syntax`——报出来与真正的原因毫无关系，而且它撞的是
    **任何含符号的 MySQL 口令**，不是罕见形状。
    """
    url = "mysql+pymysql://root:p%40ss%25word@127.0.0.1:3306/xinliceping"
    monkeypatch.setattr(
        ensure_schema, "get_settings", lambda: Settings(database_url=url)
    )
    config = ensure_schema.alembic_config()
    assert config.get_main_option("sqlalchemy.url") == url, "转义之后取回来必须与原来逐字相同"


def _escapes_percent(node: ast.AST) -> bool:
    """这个表达式里有没有 `%` → `%%` 那一次替换。

    判的是**语法结构**不是文本：`.replace("%", "%%")` 与 `.replace('%', '%%')` 是同一件事，
    而 `ast.unparse` 会把两者的引号统一成单引号——第一版就是按文本找带双引号的写法，
    于是两个**正确**的调用点同时被判成漏了转义。
    """
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        if not (isinstance(inner.func, ast.Attribute) and inner.func.attr == "replace"):
            continue
        args = [ast.literal_eval(arg) for arg in inner.args if isinstance(arg, ast.Constant)]
        if args[:2] == ["%", "%%"]:
            return True
    return False


def test_every_place_that_hands_a_url_to_alembic_escapes_the_percent_sign():
    """上面那条证的是**这个写法**是对的；这条证的是**没有哪个地方漏了它**。

    `env.py` 里还有第二处 `set_main_option("sqlalchemy.url", …)`——那是 `alembic upgrade
    head` 真正走的那一条（`ensure_schema` 那份只喂给 `stamp`）。两处漏掉任何一处，
    含符号口令的机器都会在安装中途撞一句与原因无关的
    `invalid interpolation syntax`，而**没有任何别的测试看得见它**：`make test` 不跑
    Alembic（缺口 3），内存 sqlite 的连接串里也没有 `%`。
    """
    sites: list[tuple[str, str, bool]] = []
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "set_main_option"):
                continue
            if not node.args or ast.unparse(node.args[0]) != "'sqlalchemy.url'":
                continue
            source = ast.unparse(node.args[1]) if len(node.args) > 1 else "<没有第二个实参>"
            sites.append((str(path.relative_to(BACKEND_DIR)), source, _escapes_percent(node)))

    assert len(sites) >= 2, f"只扫到 {len(sites)} 处 —— 扫描本身坏了，这条守卫是空转"
    unescaped = [(where, source) for where, source, ok in sites if not ok]
    assert not unescaped, (
        "这几处把连接串交给 Alembic 时没有把 `%` 转义成 `%%`："
        + "；".join(f"{where} → {source}" for where, source in unescaped)
    )
