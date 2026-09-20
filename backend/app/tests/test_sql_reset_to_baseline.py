"""把守 `sql/reset_to_baseline.sql` 的三条不变量。

那份脚本要用在**这台机器之外**的环境上，那里没有人会在我改完之后重看一遍，
所以它的三条约定必须有机器判据，而不是写在头注释里等读者自觉：

1. **覆盖面对齐 `purge.py`。** 这张表要清，那张不要，是同一件事在两个地方各写了一份
   ——`ASSESSMENT_TABLES`（Python）与脚本里的 `DELETE` 清单（SQL）。两边必然漂移，
   所以这里比一次：Python 那边说该删的表，SQL 里必须都出现。
2. **顺序是子先父后。** 全库没有一个 `ondelete=`，每个外键都是 RESTRICT，父行先删
   在 MySQL 上是必然的 1451。2026-09-19 之前测试跑在内存 sqlite 上，它不检查外键
   （缺口 3），所以这个错误**只有真库会报**，而这份脚本的正确性只能在临时库上手跑一遍。
   现在测试链自己就在 MySQL 上，但**这一条仍然是静态推的**：它拿 `Base.metadata`
   的外键图去核那份 SQL 的语句次序，不需要连库。人眼核对外键会错（第一版就把
   `student` 排在了 `user_scope` 前面），`Base.metadata` 不会。
   真要连库跑，出口是 `throwaway_database()`（`mysql_support`）——那才是
   「开着 `FOREIGN_KEY_CHECKS=1` 真跑一遍」的样子，至今仍走人工。
3. **每一条 `DELETE` 都挂在 admin 那一行上。** 找不到 admin 时整份脚本必须一条都不删：
   最坏的失败不是「少删了」（再跑一次的事），而是「删完没有人能登录」。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.db.base import Base
from app.db.purge import ASSESSMENT_TABLES, CLEARED_BEFORE_DELETE

SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "reset_to_baseline.sql"

# 锚点谓词。每一条 DELETE 都要带上它，见模块 docstring 第 3 条。
ANCHOR = "@admin_id is not null"

_DELETE_RE = re.compile(r"delete\s+from\s+`?([a-z_]+)`?", re.IGNORECASE)
_UPDATE_NULL_RE = re.compile(
    r"update\s+`?([a-z_]+)`?\s+set\s+`?([a-z_]+)`?\s*=\s*null", re.IGNORECASE
)
_SET_ANCHOR_RE = re.compile(r"set\s+@admin_id\s*:=", re.IGNORECASE)


def sql_without_comments() -> str:
    """剥掉 `--` 行注释。

    非做不可：这份脚本的注释里写着反例（「第一版把 student 排在了 user_scope 前面」
    那一类），不剥的话守卫会去读注释里的表名，把说明当成代码。
    """
    return "\n".join(
        line for line in SQL_PATH.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )


def statements() -> list[str]:
    return [s.strip() for s in sql_without_comments().split(";") if s.strip()]


def delete_statements() -> list[tuple[int, str]]:
    """`(语句序号, 表名)`，按脚本顺序，重复的表保留重复。

    序号用的是**语句**坐标而不是「第几条 DELETE」——`nulled_columns()` 报的也是这个
    坐标，两个判断要在同一个坐标系里比大小，否则比的是两个不相干的东西（写到这一版
    之前就是这么错的：`assessment_scale.created_by` 明明在第 14 条被置空，
    却因为坐标错位一直被读成「没置空」）。
    """
    found: list[tuple[int, str]] = []
    for index, statement in enumerate(statements()):
        found.extend((index, match.group(1)) for match in _DELETE_RE.finditer(statement))
    return found


def deleted_tables() -> list[str]:
    return [table for _, table in delete_statements()]


def nulled_columns() -> dict[tuple[str, str], int]:
    """`UPDATE <表> SET <列> = NULL` 的 (表, 列) → 它是第几条语句。

    这是脚本的第二条出路：`assessment_scale.created_by` / `role_permission.updated_by` /
    `system_setting.updated_by` 都是「指向某个员工的出处列」，删账号之前必须先摘掉，
    否则 RESTRICT 当场 1451——而 `role_permission` / `system_setting` **本来就整表保留**，
    它们那两条外键靠调顺序是解不掉的，只能置 NULL。
    """
    statements_ = statements()
    found: dict[tuple[str, str], int] = {}
    for index, statement in enumerate(statements_):
        match = _UPDATE_NULL_RE.search(statement)
        if match:
            found.setdefault((match.group(1).lower(), match.group(2).lower()), index)
    return found


def test_the_script_exists_where_the_docstring_says() -> None:
    assert SQL_PATH.is_file(), f"找不到 {SQL_PATH}"


def test_it_deletes_every_table_purge_py_deletes() -> None:
    """覆盖面与 `purge.py` 的 `ASSESSMENT_TABLES` 对齐。

    单向断言（Python 那边是全集）是刻意的：SQL 多删几张（审计、名册、账号）是这份
    脚本**自己的**职责——它比 `purge-demo` 走得更远。缺一张才是错的：那意味着
    `purge.py` 新加了一张表，而新环境上跑 SQL 会留下一批删不掉的残渣。
    """
    expected = {model.__tablename__ for model in ASSESSMENT_TABLES}
    missing = expected - set(deleted_tables())
    assert not missing, f"purge.py 要删、而这个脚本没删的表：{sorted(missing)}"


def test_it_clears_every_column_purge_py_clears() -> None:
    """覆盖面与 `purge.py` 的 `CLEARED_BEFORE_DELETE` 对齐（同一条判据的第二半）。

    「先摘指针再删行」这件事也在两处各写了一份，所以也在这里比一次。
    单向断言的理由与上面那条相同：SQL 多摘几列（三处「谁动过这条配置」的出处列，
    `purge.py` 那两条路都不删那些父行，所以它不需要）是它自己的职责，少一列才是错的
    —— `purge.py` 那边新加一处指针而这份脚本没跟上，新环境上那一条 DELETE 就是 1451。

    顺序由 `test_delete_order_is_child_before_parent` 把关（它认这条出路），
    这里只管**有没有**。
    """
    nulled = set(nulled_columns())
    expected = {(model.__tablename__, column) for model, column in CLEARED_BEFORE_DELETE}
    missing = expected - nulled
    assert not missing, f"purge.py 要摘、而这个脚本没摘的列：{sorted(missing)}"


def test_delete_order_is_child_before_parent() -> None:
    """每一条「子 → 父」外键，要么子先删，要么那条出处列先被置空。

    判据取**第一次**出现：`scale_rule` 在这个脚本里出现两次（先跟丢掉非已发布量表，
    最后再清 RETIRED 残留），第二次那批行指向的是**保留**的量表，与这条无关。

    两条出路各有各的用处，缺一不可：调顺序解决得了
    `user_scope.student_id → student`（两张都删），解决不了
    `role_permission.updated_by → user_account`（前者整表保留，只能置 NULL）。
    所以这里两种都认，但**必须在这一层认出来**——把置 NULL 那几条 UPDATE 删掉，
    调顺序救不了它，这一条会红，而真库上它当场 1451。

    变异验证：把 `DELETE FROM student` 挪到 `DELETE FROM user_scope` 前面 —— 红
    （真库上 `user_scope.student_id` 是 RESTRICT，会以 1451 拒绝执行）。
    """
    positions = delete_statements()
    first: dict[str, int] = {}
    for index, table in positions:  # setdefault：留下的是**第一次**出现的序号
        first.setdefault(table, index)
    nulled = nulled_columns()
    unreachable = len(statements())  # 「永远没被置空」的序位，比任何一条都靠后

    offenders: list[str] = []
    for table in Base.metadata.tables.values():
        if table.name not in first:
            continue
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            if parent not in first:
                continue  # 指向被保留的表（school / 量表 / alembic_version）
            if first[table.name] < first[parent]:
                continue  # 子先删，这条外键从头到尾没有活着的引用
            if nulled.get((table.name, fk.parent.name), unreachable) < first[parent]:
                continue  # 父行被删之前，这一列已经摘干净了
            offenders.append(
                f"{table.name}.{fk.parent.name} → {parent}：{table.name} 排在第 "
                f"{first[table.name] + 1} 条、{parent} 在第 {first[parent] + 1} 条，"
                f"而且这一列没有在删 {parent} 之前被置 NULL"
            )
    assert not offenders, "删除顺序不是子先父后：" + "；".join(offenders)


def test_every_delete_is_anchored_on_the_admin_row() -> None:
    """没有一条 `DELETE` 能在「找不到 admin」时生效。"""
    unanchored = [
        " ".join(statement.split())[:90]
        for statement in statements()
        if statement.upper().startswith("DELETE") and ANCHOR not in statement.lower()
    ]
    assert not unanchored, "这些 DELETE 没挂 @admin_id 判据：" + "；".join(unanchored)


def test_the_anchor_is_set_before_the_first_write() -> None:
    """`SET @admin_id := …` 必须排在**任何**会写库的语句之前。

    它排在后面的话，前面那几条 DELETE 会读到 NULL 而**静默地什么都不删**——
    脚本看起来跑完了，库里还是原样，而 `SET` 那一条本身不会报错。

    判据是**赋值**（`SET @admin_id := …`）而不是「preamble 里出现过 `@admin_id`」：
    后者一开始就是这条测试的写法，而它是绿的——因为 `SELECT IF(@admin_id IS NULL, …)`
    那条预告本身就含这三个字，于是「赋值挪到第一条 DELETE 后面」这种改法照样通过。
    变异验证时才发现，写在这里免得下次又松回去。
    """
    for index, statement in enumerate(statements()):
        if statement.upper().startswith(("DELETE", "UPDATE")):
            break
    else:  # pragma: no cover - 脚本里当然有 DELETE，这一支只是把话说全
        raise AssertionError("脚本里没有 DELETE，判据无从谈起")
    assigned = [s for s in statements()[:index] if _SET_ANCHOR_RE.search(s)]
    assert assigned, "第一条会写库的语句之前没有 SET @admin_id := …"
