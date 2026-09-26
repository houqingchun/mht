"""`0023_report_version_publish` 的**数据回填**守卫（§5.13 缺口 1 的第二个）。

`0023` 做两件事：给 `professional_report_version` 加版本级的
`status` / `published_at` / `published_by` 三列，然后把**既有**的行按报告头的状态回填。
第二条是这份文件存在的全部理由——加列是 DDL，`test_migrations_build_the_models.py`
看得见；而回填是**数据**变更，加完列之后「版本行是什么状态」这件事没有任何静态判据
看得见，只能在一个真有旧数据的库上跑一遍。

## 为什么必须是一份**独立的、停在 0022 的**库

开发库与 `conftest.py` 那个 session 级测试库都**已经在 head 上了**，而回填的三条分支
（`current_version > 1`）在开发库上**一条都不成立**：那里 11 份报告全是
`current_version == 1`（1 份草稿、10 份已发布），所以 `current_version - 1` 与
`version_no < current_version` 这两条 UPDATE 在那边**一行都没碰到过**。真机上升级成功
只证明那两条 SQL **语法**没错，证明不了它们的结果对。

所以这里自己造一个库：`throwaway_database(with_schema=False)`（不建表）→
`alembic upgrade 0022_professional_reports`（建到**加列之前**的形状）→ 插旧数据
（**没有 status 那一列**，因为这个库上它还不存在）→ `alembic upgrade head` →
读回来逐行比。走的是操作员真机上升级时走的那条路。

**不许用今天的模型去插这些行**：`ProfessionalReportVersion` 现在有 `status` 列，
用它插出来的行是「已经带状态的新行」，那正好绕开了这段要验的东西——
同 CLAUDE.md §21 那条「不能用『当前代码 + seed』造一份 V1.0 的库」。

## 回填的真值表（与迁移 docstring 里那张逐字相同，这里是它的可执行形式）

| 报告头 | current_version | 版本 | status | published_at / published_by |
|---|---|---|---|---|
| `DRAFT` | 1 | 1 | `DRAFT` | NULL / NULL（从没发布过） |
| `PUBLISHED` | 1 | 1 | `PUBLISHED` | 抄报告头 |
| `ARCHIVED` | 2 | 2 | `PUBLISHED` | 抄报告头 |
| `ARCHIVED` | 2 | 1 | `PUBLISHED` | **NULL / NULL**（报告头只留着最近一次） |
| `DRAFT` | 3 | 2 | `PUBLISHED` | 抄报告头（`new_version()` 不清那两列，所以它们属于**上一版**） |
| `DRAFT` | 3 | 1 | `PUBLISHED` | NULL / NULL |

最后两行就是那个缺陷本来的形状：**一所学校正在改第三版**，而前两版都已经发布出去过。
那份数据在 `0023` 之前**没有别的地方表达得出来**——报告头只有一个状态、一对时间。
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from app.tests.mysql_support import engine_for, run_migrations, throwaway_database

# 加列之前的那一版。写死而不是 `head - 1`：这一份要钉的正是「0023 干了什么」，
# 用相对表达式的话，将来某条迁移插在中间时它会悄悄指到别处。
BEFORE = "0022_professional_reports"

PUBLISHED_AT = datetime(2026, 9, 20, 9, 0, 0)


def _insert_report(connection, *, report_id, report_no, title, status, current_version, user_id, published):
    connection.execute(
        text(
            """
            INSERT INTO professional_report
                (id, report_no, school_id, report_type, title, status, task_scope_json,
                 analysis_mode, statistics_snapshot_json, current_version,
                 created_by, updated_by, published_by, published_at)
            VALUES
                (:id, :report_no, 1, 'PROFESSIONAL', :title, :status, '{"task_ids": [1]}',
                 'ALL_CALCULATED', '{}', :current_version,
                 :user_id, :user_id, :published_by, :published_at)
            """
        ),
        {
            "id": report_id,
            "report_no": report_no,
            "title": title,
            "status": status,
            "current_version": current_version,
            "user_id": user_id,
            "published_by": user_id if published else None,
            "published_at": PUBLISHED_AT if published else None,
        },
    )


def _insert_version(connection, *, version_id, report_id, version_no, user_id):
    """插一行**0022 形状**的版本行——这个库上 `status` 那一列还不存在。

    正文四段各带一个可辨认的标记，回填是数据变更、不碰它们，所以顺带钉住
    「回填只动那三列」这件事：一个顺手 `UPDATE ... SET overall_summary = ''` 的实现
    会在这里红。
    """
    connection.execute(
        text(
            """
            INSERT INTO professional_report_version
                (id, report_id, version_no, overall_summary, dimension_interpretation,
                 sample_validity_note, support_plan, statistics_snapshot_json, created_by)
            VALUES
                (:id, :report_id, :version_no, :summary, '', '', '', '{}', :user_id)
            """
        ),
        {
            "id": version_id,
            "report_id": report_id,
            "version_no": version_no,
            "summary": f"第 {version_no} 版的结论。",
            "user_id": user_id,
        },
    )


def _backfilled_rows(connection):
    """回填之后所有版本行的 `(report_no, version_no) → (status, published_at, published_by)`。"""
    rows = connection.execute(
        text(
            """
            SELECT r.report_no, v.version_no, v.status, v.published_at, v.published_by, v.overall_summary
            FROM professional_report_version v
            JOIN professional_report r ON r.id = v.report_id
            """
        )
    ).all()
    return {(row[0], row[1]): (row[2], row[3], row[4], row[5]) for row in rows}


@pytest.fixture()
def upgraded():
    """一份停在 0022 的库、里面是六种历史形状，升级到 head 之后交出来。"""
    with throwaway_database(with_schema=False) as url:
        run_migrations(url, BEFORE)
        engine = engine_for(url)
        try:
            with engine.begin() as connection:
                # 报告头与版本行都指着 `user_account`（`published_by` / `created_by`），
                # 而这两张表在 0022 上是空的——先造一个作者。
                connection.execute(
                    text(
                        """
                        INSERT INTO user_account
                            (id, account, account_type, display_name, password_hash, role_code,
                             must_change_password, failed_attempts, active)
                        VALUES (1, '13800000001', 'MOBILE', '心理老师', 'x', 'counselor', 0, 0, 1)
                        """
                    )
                )
                connection.execute(text("INSERT INTO school (id, code, name) VALUES (1, 'QH', '青禾实验学校')"))

                # ① 从没发布过：报告头 DRAFT、第 1 版。
                _insert_report(connection, report_id=1, report_no="RPT-A", title="草稿一份",
                               status="DRAFT", current_version=1, user_id=1, published=False)
                _insert_version(connection, version_id=1, report_id=1, version_no=1, user_id=1)

                # ② 发布了、没有再改：报告头 PUBLISHED、第 1 版。
                _insert_report(connection, report_id=2, report_no="RPT-B", title="发布一份",
                               status="PUBLISHED", current_version=1, user_id=1, published=True)
                _insert_version(connection, version_id=2, report_id=2, version_no=1, user_id=1)

                # ③ 已归档、改过一版：报告头 ARCHIVED、第 2 版。第 2 版拿走头部的两个时间，
                #    第 1 版只回填状态——那一对值已经不在报告头上了。
                _insert_report(connection, report_id=3, report_no="RPT-C", title="归档一份",
                               status="ARCHIVED", current_version=2, user_id=1, published=True)
                for version_no in (1, 2):
                    _insert_version(connection, version_id=100 + version_no, report_id=3,
                                    version_no=version_no, user_id=1)

                # ④ **正在改第三版**：报告头 DRAFT、第 3 版是草稿，而前两版都发布过。
                _insert_report(connection, report_id=4, report_no="RPT-D", title="改第三版",
                               status="DRAFT", current_version=3, user_id=1, published=True)
                for version_no in (1, 2, 3):
                    _insert_version(connection, version_id=200 + version_no, report_id=4,
                                    version_no=version_no, user_id=1)

            run_migrations(url)  # → head，回填就在这一步
            with engine.begin() as connection:
                yield _backfilled_rows(connection)
        finally:
            engine.dispose()


def test_the_backfill_fills_the_versions_that_have_been_published(upgraded):
    """先证明有东西可查，再逐行比真值表。

    六个 (报告, 版本) 组合一个不少——少了任何一个，下面那些断言就在一个
    「这次插入没成功」的库上照样绿。
    """
    assert set(upgraded) == {
        ("RPT-A", 1),
        ("RPT-B", 1),
        ("RPT-C", 1),
        ("RPT-C", 2),
        ("RPT-D", 1),
        ("RPT-D", 2),
        ("RPT-D", 3),
    }

    # ① 从没发布过的那一份：版本行留在 DRAFT，时间与人都是 NULL。
    assert upgraded[("RPT-A", 1)][:3] == ("DRAFT", None, None)

    # ② 发布了、没改过的：三列都补齐，时间抄报告头。
    assert upgraded[("RPT-B", 1)][:3] == ("PUBLISHED", PUBLISHED_AT, 1)

    # ③ 已归档：当前版本那一行拿到头部的两个时间，更早的那一版只拿到状态。
    assert upgraded[("RPT-C", 2)][:3] == ("PUBLISHED", PUBLISHED_AT, 1)
    assert upgraded[("RPT-C", 1)][:3] == ("PUBLISHED", None, None)

    # ④ **本次缺陷本来的形状**：正改第三版时，前两版都得对领导端立得住。
    #    第 2 版拿走头部的时间（`new_version()` 不清那两列），第 3 版仍是草稿。
    assert upgraded[("RPT-D", 2)][:3] == ("PUBLISHED", PUBLISHED_AT, 1)
    assert upgraded[("RPT-D", 1)][:3] == ("PUBLISHED", None, None)
    assert upgraded[("RPT-D", 3)][:3] == ("DRAFT", None, None)


def test_the_backfill_leaves_the_text_alone(upgraded):
    """回填是一条**数据**变更，它只该碰那三列。

    正文四段在这条路上很容易被顺手带上（一次 `UPDATE ... SET` 里多写两列），而那样
    改的是**已经发布出去的专业判断**——比状态回填错更严重，且界面上看不出来。
    """
    assert upgraded[("RPT-D", 1)][3] == "第 1 版的结论。"
    assert upgraded[("RPT-D", 2)][3] == "第 2 版的结论。"
    assert upgraded[("RPT-D", 3)][3] == "第 3 版的结论。"
