"""空库提醒（`app/db/check_empty.py`）。

这个模块保护的是**一件不可逆的事**：安装器在第 4 步会先 `app.db.seed`，再
`app.db.reset_to_baseline --yes`——后者把这个库清成「只有 admin + 量表与基本配置」。
指向一个正在用的库时，学生的名册、测评、关怀档案、审计行会一起没。

**退出码 1 = 非空**这件事必须是一条能被断言的事实（安装器用 `-AllowFailure` 收下它再
自己说一句 WARN，见 `install.ps1`），所以这里两条用例各钉一个方向。

用**文件型**的临时 sqlite，不是内存库：`main()` 自己建引擎，而 `sqlite://` 换一条连接
就是另一个库，探针会对着一个空库说话。
"""

from __future__ import annotations

from sqlalchemy import create_engine

from app.core.config import Settings
from app.db import check_empty
from app.db.base import Base
from app.models import School

ROOT_TABLES = ("school", "user_account", "student")


def _probe_url(tmp_path) -> str:
    """一个真的建了表、但一行数据都没有的 sqlite 文件。"""
    url = f"sqlite:///{tmp_path / 'probe.db'}"
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return url


def _point_at(monkeypatch, url: str) -> None:
    monkeypatch.setattr(check_empty, "get_settings", lambda: Settings(database_url=url))


def test_count_rows_counts_each_root_table_separately(tmp_path):
    url = _probe_url(tmp_path)
    engine = create_engine(url)
    try:
        assert check_empty.count_rows(engine) == {"school": 0, "user_account": 0, "student": 0}
        with engine.begin() as connection:
            connection.execute(School.__table__.insert(), [{"code": "QH", "name": "青禾实验学校"}])
        assert check_empty.count_rows(engine) == {"school": 1, "user_account": 0, "student": 0}, (
            "`school` 有行不该让另外两张表也报非零——报出来的那几个数是要给人对名册用的"
        )
    finally:
        engine.dispose()


def test_an_empty_database_says_so_and_exits_zero(tmp_path, monkeypatch, capsys):
    _point_at(monkeypatch, _probe_url(tmp_path))
    assert check_empty.main([]) == 0
    output = capsys.readouterr().out
    assert "[OK]" in output
    assert "空" in output


def test_a_database_with_any_row_is_reported_as_occupied(tmp_path, monkeypatch, capsys):
    """★ 这条就是那次「别把正在用的库清掉」的全部机制。

    断言的是一个**非零**退出码，不是某句文案：安装器判的是退出码（`-AllowFailure` 收下它
    再自己说一句）。文案改一个字不该让保护失效，判据改一个数应该让它失效。
    """
    url = _probe_url(tmp_path)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(School.__table__.insert(), [{"code": "QH", "name": "青禾实验学校"}])
    finally:
        engine.dispose()

    _point_at(monkeypatch, url)
    assert check_empty.main([]) == 1
    output = capsys.readouterr().out
    assert "school：1 行" in output, "非空时要把**是哪张表、有多少行**念出来，不然那句提醒没法核对"


def test_a_database_with_no_tables_at_all_says_so_in_words(tmp_path, monkeypatch, capsys):
    """一个**连表都没有**的库。

    安装流程里这一步排在 `ensure_schema` 与迁移之后，所以它不该发生；但
    `python -m app.db.check_empty` 是个能单独跑的命令，2026-09-18 在真 MySQL 上跑了
    一次：`count_rows` 一路撞到驱动上，报出来是一整段
    `ProgrammingError (1146, "Table 'x.school' doesn't exist")` 加 SQLAlchemy 的英文链接。
    **那句话是错的**——它说的是「读不动这个库」，而真相是「这个库还没建表」。

    判据是「有没有说出表结构没建出来」，不是某句文案：这一条要挡的是
    「把一句能照着办的话换成一段英文 traceback」这件事本身。
    """
    url = f"sqlite:///{tmp_path / 'bare.db'}"
    create_engine(url).dispose()  # 建出那个文件，但一张表都不建
    _point_at(monkeypatch, url)

    assert check_empty.main([]) == 1
    output = capsys.readouterr().out
    assert "school" in output and "表结构" in output
    assert "读不动这个库" not in output, "这不是「读不动」，是这个库还没有表"


def test_the_table_list_stays_the_three_roots(tmp_path):
    """`ROOT_TABLES` 是这一层唯一的判据面。

    三张「根」表够用的理由是**其余每一张表都直接或间接指着它们**（全库没有 `ondelete=`，
    全是 RESTRICT，CLAUDE.md §1），所以往里加表不会让判据更准，只会让这句话越来越难读。
    真要加，先回来看一眼这一段。
    """
    assert check_empty.ROOT_TABLES == ROOT_TABLES
    for name in ROOT_TABLES:
        assert name in Base.metadata.tables, f"{name} 不在 Base.metadata 里，`count_rows` 会 KeyError"
