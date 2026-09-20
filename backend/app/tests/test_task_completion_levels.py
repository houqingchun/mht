"""任务完成明细带**这一场**的关注等级与总分——以及它不是「每人最近一场」。

用户 2026-09-17 报的第一件事是「当前没有一个视角查看所有学生的测试结果」。
第二件事是「我是刚刚导入了一个测评，但查不到这里的记录，比如张三」——
即使他找到那个导入批次、点开「查看明细」，那张表也只有 `状态=已完成` 和用时，
**没有等级、没有总分**。这一批人测出了什么，界面上答不出来。

补这两列时口径是**唯一的岔路**，所以这个文件主要钉口径：

| 口径 | 用在哪 | 语义 |
|---|---|---|
| 按人取最近一场（§11） | 个案详情、重点学生、受控导出、全部学生页签 | 「他现在是什么状态」 |
| **按场**（本文件） | 任务完成明细、完成率 | 「这一批人**在这次**测出了什么」 |

一处改动就能把两者搞混：把这里的 `AssessmentResult` 外连接换成
`latest_result_subquery`（按人取最近一场）。代码照样跑、测试大多照样绿，但九月十六日
那个批次的明细里会印出九月十七日的分——一份批次报表开始说别的批次的事，
而它旁边那列「完成率」还是按场算的。`test_each_task_shows_its_own_sitting` 就是为
这一条写的，它让同一名学生在两个批次里各有一场、分数不同。

导入的两批各显示各的也正是**实情**：张三在两份文件里分别是 2 分和 24 分，
他不该被抹成同一个数。
"""

import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
)
from app.models.organization import School, Student
from app.models.scale import AssessmentScale
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting, make_target

COUNSELOR = ("counselor", "13800000001")


def make_task(db, task_no: str, days_ago: int = 0) -> AssessmentTask:
    """只发给 S001 的一场任务。窗口按今天现算——写死日期会让用例自己过期。"""
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    school = db.scalar(select(School).where(School.code == "QH"))
    student = db.scalar(select(Student).where(Student.student_no == "S001"))
    now = datetime.now(UTC)
    task = AssessmentTask(
        task_no=task_no,
        name=f"批次 {task_no}",
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        start_at=now - timedelta(days=days_ago + 7),
        end_at=now - timedelta(days=days_ago),
        status="ACTIVE",
    )
    db.add(task)
    db.flush()
    make_target(db, student, task)
    db.flush()
    return task


def add_result(
    db,
    task: AssessmentTask,
    *,
    level: str,
    total_score: int,
    days_ago: int,
    completed: bool = True,
) -> AssessmentSession:
    """给这名学生在**这一场**任务里加一场已交卷的测评。"""
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    student = db.scalar(select(Student).where(Student.student_no == "S001"))
    session = make_sitting(
        db,
        student,
        scale=scale,
        task_id=task.id,
        submitted_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=0,
            validity_status="VALID",
            total_score=total_score,
            total_level=level,
            rule_version=scale.version,
        )
    )
    target = db.scalar(select(AssessmentTarget).where(AssessmentTarget.task_id == task.id))
    if completed:
        target.status = "COMPLETED"
        target.completed_at = datetime.now(UTC) - timedelta(days=days_ago)
    db.commit()
    return session


def completion(client, task_id: int, account: str = COUNSELOR[1]) -> list[dict]:
    response = client.get(
        f"/api/v1/assessment-tasks/{task_id}/completion",
        headers=auth_headers(client, COUNSELOR[0], account),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def completion_csv(client, task_id: int) -> list[list[str]]:
    """建作业 → 取文件（2026-09-19 阶段 8 起导出是两跳，见 `test_audit_export_api`）。"""
    headers = auth_headers(client, *COUNSELOR)
    created = client.post(
        f"/api/v1/assessment-tasks/{task_id}/completion/export",
        headers=headers,
        json={"purpose": "完成情况核对"},
    )
    assert created.status_code == 200, created.text
    response = client.get(
        f"/api/v1/export-jobs/{created.json()['data']['id']}/download", headers=headers
    )
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_each_task_shows_its_own_sitting(client, db_session):
    """同一名学生落在两个批次里，两边的明细各说各的场。

    这是本文件存在的理由：夹具里必须有**同一个人测过两场**，否则「按场」与
    「按人取最近一场」在两个批次上给出同样的答案，测不出差别
    （`test_analytics_basis` 的同一条要点）。
    """
    older = make_task(db_session, "TASK-BATCH-SEPT16", days_ago=1)
    newer = make_task(db_session, "TASK-BATCH-SEPT17", days_ago=0)
    add_result(db_session, older, level="GENERAL_RANGE", total_score=2, days_ago=1)
    add_result(db_session, newer, level="NEEDS_ATTENTION", total_score=24, days_ago=0)

    old_row = completion(client, older.id)[0]
    new_row = completion(client, newer.id)[0]

    # 九月十六日那批显示 2 分，九月十七日那批显示 24 分——**各显示各的**。
    # 若换成「每人最近一场」，两行都会是 24（更晚的那一场），这条随即变红。
    assert (old_row["total_level"], old_row["total_score"]) == ("GENERAL_RANGE", 2)
    assert (new_row["total_level"], new_row["total_score"]) == ("NEEDS_ATTENTION", 24)


def test_a_student_who_has_not_taken_it_yet_has_no_level(client, db_session):
    """这一场还没交卷：等级与总分是 `None`，不是「一般观察」也不是 0。

    界面上按两条不同的既有约定渲染它们：等级列出「未测评」（`levelLabel` 认 `null`），
    总分列（数值列）出「—」；导出时等级出「未测评」、总分**留空**。`None` 不是 `0`
    （§11）——0 分是一个测出来的结果，没测过不是。
    """
    task = make_task(db_session, "TASK-EMPTY-BATCH")

    row = completion(client, task.id)[0]

    assert row["status"] == "NOT_STARTED"
    assert row["total_level"] is None
    assert row["total_score"] is None


def test_the_csv_header_carries_both_new_columns(client, db_session):
    task = make_task(db_session, "TASK-CSV-HEADER")
    add_result(db_session, task, level="KEY_ATTENTION", total_score=88, days_ago=0)

    header = completion_csv(client, task.id)[0]

    assert "关注等级" in header
    assert "MHT总分" in header


def test_the_csv_follows_each_column_types_own_missing_value_rule(client, db_session):
    """两种缺值约定在同一个文件里并存，且**必须**并存（§3）。

    等级那一列写「未测评」——它认得这个码，一格空白会让人以为是漏了；
    总分那一列留空——写「—」会让整列被表格软件当成文本，那一列就再也排不了序、
    求不了和。`test_export_labels_match_frontend` 守的是前者的措辞与前端一致。
    """
    task = make_task(db_session, "TASK-CSV-BLANK")

    rows = completion_csv(client, task.id)
    header, row = rows[0], rows[1]

    assert row[header.index("关注等级")] == "未测评"
    assert row[header.index("MHT总分")] == ""
    # 状态列仍是翻译过的中文，不是 `NOT_STARTED`（后端拼的 CSV 拿不到 labels.ts，
    # 走的是 export_labels 那份镜像）。
    assert row[header.index("状态")] == "未开始"


def test_the_csv_translates_the_level_it_does_have(client, db_session):
    """有结果的那一行，等级列写的是界面上那几个词，不是 `KEY_ATTENTION`。"""
    task = make_task(db_session, "TASK-CSV-LEVEL")
    add_result(db_session, task, level="KEY_ATTENTION", total_score=88, days_ago=0)

    rows = completion_csv(client, task.id)
    header, row = rows[0], rows[1]

    assert row[header.index("关注等级")] == "重点关注"
    assert row[header.index("MHT总分")] == "88"
