"""V2.0.0 那两条迁移（`0021_v2_task_governance` / `0022_professional_reports`）：两个方向。

## 为什么单列这一条

`test_migrations_build_the_models.py` 守的是**升级**那一个方向（空库跑到 head 之后与
`Base.metadata` 逐项相同），而 `downgrade()` 里写的那些语句**没有任何东西执行过**。
2026-09-25 做 V2.0.0 的升级预演时，在一个忠实克隆上跑 `alembic downgrade 0020`（也就是
「从 V2.0.0 退回 V1.1.6」这个动作）当场撞出一条：

```
pymysql.err.OperationalError: (1553, "Cannot drop index
'ix_professional_report_school_status': needed in a foreign key constraint")
```

`0022` 的 downgrade 原本在 `drop_table` 之前先 `drop_index("ix_professional_report_school_status")`，
而那个索引的第一列就是 `school_id`、正是 `professional_report_fk_school` 拿来当索引用的那一条。
**删表本身会把它的外键与索引一并带走**，所以那一行既多余又致命。这与 CLAUDE.md §1 里
`0012` 删那条唯一索引时踩的是同一个坑（那条是必须先补建 `ix_student_care_case_student_id`）。

**它是结构层面的检查，与数据无关**——空库上照样红，所以这条守卫不需要夹具。

## 网眼（它守不住什么）

- 它**只走 `0020 → head` 这一段**。更早的那些迁移的 downgrade 这条不碰，所以
  「`0013` / `0014` 降得回去吗」在这里没有答案。
- 它不比对结构，只比对「那三列 / 那两张表在不在」。结构那一层归
  `test_migrations_build_the_models.py`。
- **它证明不了生产库降得回去**：真数据上会不会撞别的约束（比如某个 NOT NULL 有反例）
  要拿真库跑，与 CLAUDE.md §30 那份增量 SQL 的「真机那一趟」是同一类。
"""

from __future__ import annotations

from sqlalchemy import text

from app.tests.mysql_support import downgrade_to, engine_for, run_migrations, throwaway_database

#: V1.1.6 停在这一条（0021 的 down_revision）。
V16_REVISION = "0020_total_excludes_validity"

#: `0021` 给 `assessment_task` / `risk_event` 各加的三列。
VOID_COLUMNS = {"voided_at", "voided_by", "void_reason"}

#: `0022` 新建的两张表。
NEW_TABLES = {"professional_report", "professional_report_version"}


def _columns(url, table: str) -> set[str]:
    with engine_for(url).connect() as connection:
        return {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
                ),
                {"table": table},
            )
        }


def _tables(url) -> set[str]:
    with engine_for(url).connect() as connection:
        return {
            row[0]
            for row in connection.execute(
                text("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
            )
        }


def test_the_two_v2_migrations_run_both_ways_without_touching_old_rows():
    with throwaway_database(with_schema=False) as url:
        run_migrations(url, V16_REVISION)
        assert _columns(url, "assessment_task") & VOID_COLUMNS == set()
        assert _columns(url, "risk_event") & VOID_COLUMNS == set()
        assert not (_tables(url) & NEW_TABLES)

        # 在 V1.1.6 的结构上放一条最普通的旧数据：一所学校、一份量表、一场已经结束的普查。
        # 只写必填列（`school` 只要 id/code/name，`assessment_scale` 只要 id/code/name/version），
        # 其余都由 DDL 的默认值兜着——这条用例要的是「升级动没动它」，不是数据有多全。
        with engine_for(url).begin() as connection:
            connection.execute(text("INSERT INTO school (id, code, name) VALUES (1, 'OLD', '旧学校')"))
            connection.execute(
                text(
                    "INSERT INTO assessment_scale (id, code, name, version) "
                    "VALUES (1, 'MHT', 'MHT心理健康量表', '1.1.0')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assessment_task (id, task_no, name, scale_id, school_id, scope_type, status) "
                    "VALUES (1, 'TASK-OLD-1', '上学期普查', 1, 1, 'SCHOOL', 'CLOSED')"
                )
            )

        run_migrations(url)  # 0020 → head
        assert _columns(url, "assessment_task") & VOID_COLUMNS == VOID_COLUMNS
        assert _columns(url, "risk_event") & VOID_COLUMNS == VOID_COLUMNS
        assert NEW_TABLES <= _tables(url)

        # ↓ 这一步就是那条 1553：0022 的 downgrade 曾经在这里把外键正在用的索引删掉。
        downgrade_to(url, V16_REVISION)
        assert _columns(url, "assessment_task") & VOID_COLUMNS == set()
        assert _columns(url, "risk_event") & VOID_COLUMNS == set()
        assert not (_tables(url) & NEW_TABLES)

        # 再升一次：确认这两条迁移可重复执行（跑过一次 head 的库上重来一遍仍然成立）。
        run_migrations(url)
        assert _columns(url, "assessment_task") & VOID_COLUMNS == VOID_COLUMNS
        assert NEW_TABLES <= _tables(url)

        # 一来一回之后，那条旧数据的**每一个旧列**都必须还是原样。
        # 三列作废元数据不在此列——downgrade 是真删了它们，值回不来，这是降级的本义。
        with engine_for(url).connect() as connection:
            row = connection.execute(
                text("SELECT task_no, name, school_id, scope_type, status FROM assessment_task WHERE id = 1")
            ).one()
        assert row == ("TASK-OLD-1", "上学期普查", 1, "SCHOOL", "CLOSED")
