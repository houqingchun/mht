"""测评任务的状态是**现算的**，不是 `assessment_task.status` 那一列的原文。

2026-09-17 之前那一列是写入时定死的一个字面量：建任务写 `ACTIVE`，导入批次也写
`ACTIVE`，然后再没有一行代码读它或改它。于是列表上每一行永远显示「进行中」——
一批 3/3 全收齐的外部导入、一场已经过了截止日期的普查、一场还没到开始日期的复测，
长得一模一样。用户报的就是这个（「都是在进行中，即使完成率显示100%也是一样」）。

判据在 `task_service.effective_task_status`，两个消费者：列表（`list_assessment_tasks`）
与「学生能不能新开一张卷子」（`assessment_service.create_or_get_session`）。
两者用同一个函数，所以界面说「已结束」时那个端点就真的开不了，不会各说各话。
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.assessment import AssessmentTarget, AssessmentTask
from app.models.enums import ScopeType
from app.models.organization import School, Student
from app.models.scale import AssessmentScale
from app.services.task_service import effective_task_status
from app.tests.conftest import auth_headers
from app.tests.factories import make_target
from app.tests.test_data_scope import (
    make_same_school_student_in_another_class,
    replace_scopes,
    seed_counselor,
    seed_student,
)

# 日期一律相对今天算。写死日期会让这条用例在某个具体的一天自己变红，
# 而红的原因不是功能坏了（CLAUDE.md 测试注意里那条教训）。
NOW = datetime.now()


def _make_task(
    db,
    *,
    start_at=datetime | None,
    end_at=datetime | None,
    status="ACTIVE",
    task_no="TASK-STATUS-1",
):
    """建一场只发给 S001 的任务。

    直接用 ORM 建，是为了把窗口摆到任意位置——`POST /assessment-tasks` 只收
    前端传来的日期字符串，摆不出「一分钟前刚过期」这种边界。
    """
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    school = db.scalar(select(School).where(School.code == "QH"))
    student = db.scalar(select(Student).where(Student.student_no == "S001"))
    task = AssessmentTask(
        task_no=task_no,
        name="任务状态用例",
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        start_at=start_at,
        end_at=end_at,
        status=status,
    )
    db.add(task)
    db.flush()
    make_target(db, student, task)
    db.commit()
    return task


def _set_target_status(db, task_id: int, status: str, *, student_no: str = "S001") -> None:
    """把**指名道姓**那一名学生的目标行改掉。

    `student_no` 是必给的（默认 S001，也就是 `_make_task` 发的那个人）：`_make_task`
    只发一个人时「按 task_id 取第一行」恰好就是它，而 `test_the_status_is_not_scoped_
    to_the_reader` 会给同一场任务再发一个人——那时「第一行是谁」由物理次序决定，
    用例会随存储引擎的不同而绿或红。判据写成「哪个人」就不依赖那次序。
    """
    target = db.scalar(
        select(AssessmentTarget)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(AssessmentTarget.task_id == task_id, Student.student_no == student_no)
    )
    target.status = status
    db.commit()


def _status_from_list(client, task_id: int) -> dict:
    """心理老师视角下这一行的状态与完成率。"""
    response = client.get("/api/v1/assessment-tasks", headers=auth_headers(client, "counselor", "13800000001"))
    assert response.status_code == 200
    row = next(item for item in response.json()["data"]["items"] if item["id"] == task_id)
    return row


# --- 从事实推出来的四种状态 ---------------------------------------------------


def test_a_task_inside_its_window_with_stragglers_reads_as_active(client, db_session):
    """种子基线：窗口还没到、有人没答 —— 这才是「进行中」该有的样子。"""
    task = db_session.scalar(select(AssessmentTask).where(AssessmentTask.task_no == "TASK-2026-FALL-MHT"))
    row = _status_from_list(client, task.id)
    assert row["status"] == "ACTIVE"
    assert row["completed_targets"] < row["total_targets"]


def test_a_task_whose_targets_are_all_done_reads_as_closed(client, db_session):
    """完成率 100% 就该收尾——用户报的那一条。"""
    task = _make_task(db_session, start_at=NOW - timedelta(days=7), end_at=NOW + timedelta(days=7))
    _set_target_status(db_session, task.id, "COMPLETED")

    row = _status_from_list(client, task.id)
    assert row["completion_rate"] == 100
    assert row["status"] == "CLOSED"


def test_a_task_past_its_deadline_reads_as_closed_even_with_stragglers(client, db_session):
    """窗口是学校自己定的，过期就是过期。

    还有没人答**不影响**这一条：「28 人里 23 人交了」由完成率那两列说，
    状态列不必再说一遍。放了这个判据，`PATCH /assessment-tasks/{id}` 改 `end_at`
    就是「还有几个人没答，延一周」的正常出路。
    """
    task = _make_task(db_session, start_at=NOW - timedelta(days=30), end_at=NOW - timedelta(minutes=1))

    row = _status_from_list(client, task.id)
    assert row["completed_targets"] == 0
    assert row["status"] == "CLOSED"


def test_a_task_that_has_not_started_reads_as_not_started(client, db_session):
    """`seed_demo` 的「初三年级复测任务」就是这一种：窗口在未来。"""
    task = _make_task(db_session, start_at=NOW + timedelta(days=3), end_at=NOW + timedelta(days=17))
    assert _status_from_list(client, task.id)["status"] == "NOT_STARTED"


def test_a_batch_without_a_deadline_closes_on_completion(client, db_session):
    """外部导入的批次没有截止日期（`commit_batch` 刻意留空），
    只能由「全都答完了」收尾——否则它会永远停在「进行中」。"""
    task = _make_task(db_session, start_at=NOW - timedelta(days=1), end_at=None)
    _set_target_status(db_session, task.id, "COMPLETED")
    assert _status_from_list(client, task.id)["status"] == "CLOSED"


# --- 边界 ---------------------------------------------------------------------


def test_a_task_with_no_targets_is_not_closed(client, db_session):
    """空集的「全部完成」是真命题，但一次还没发出去的任务不该一建出来就是「已结束」。"""
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    school = db_session.scalar(select(School).where(School.code == "QH"))
    task = AssessmentTask(
        task_no="TASK-STATUS-EMPTY",
        name="还没发放的任务",
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=1),
        status="ACTIVE",
    )
    db_session.add(task)
    db_session.commit()

    assert _status_from_list(client, task.id)["status"] == "ACTIVE"


def test_a_status_written_by_a_person_wins_over_the_derived_one(db_session):
    """库里的那一列不是 `ACTIVE` 时是**人做的裁决**，推出来的事实不能盖过它。

    今天没有写入方（四个码里只有 `ACTIVE` 被写过），但词汇表里有它们，
    读的一侧就该按它们的意思读——否则将来接上一个「暂停」按钮的人会先撞上这个。
    """
    task = _make_task(db_session, start_at=NOW - timedelta(days=1), end_at=NOW + timedelta(days=1))
    task.status = "PAUSED"
    db_session.commit()
    # 即使目标行全部完成（推导会说 CLOSED），也以库里的 PAUSED 为准
    assert (
        effective_task_status(task, total_targets=1, completed_targets=1)
        == "PAUSED"
    )


# --- 口径：状态是任务自身的事实，不跟读者范围走 --------------------------------


def test_the_status_is_not_scoped_to_the_reader(client, db_session):
    """一个只带 CLASS 范围的心理老师，把他自己那一个学生看全了，
    **不等于**这场全校普查结束了。

    状态与 `name` / `start_at` 一样是任务自身的属性；跟范围走的是完成率（§9）。
    不分开的话，同一行里会出现「状态：已结束 / 完成率：50%」这种自相矛盾的画面，
    而它比不给数字更糟——它会让心理老师以为自己不用再催了。
    """
    seeded = seed_student(db_session)  # S001，青禾 1班
    other_class = make_same_school_student_in_another_class(db_session)  # 同校 2班
    task = _make_task(db_session, start_at=NOW - timedelta(days=7), end_at=NOW + timedelta(days=7))
    make_target(db_session, other_class, task)
    _set_target_status(db_session, task.id, "COMPLETED")  # S001 那条已完成

    user = seed_counselor(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.CLASS, "class_id": seeded.class_id})

    row = _status_from_list(client, task.id)
    # 他范围内：1 / 1，100%
    assert (row["completed_targets"], row["total_targets"], row["completion_rate"]) == (1, 1, 100)
    # 整场任务：1 / 2 —— 所以状态不是「已结束」
    assert row["status"] == "ACTIVE"


def test_the_student_gets_the_same_derived_status(client, db_session):
    """学生端拿到的 `status` 也是推出来的那个。

    他现在不显示它（`StudentHomePage.vue` 按 `target_status` 判断自己的进度），
    但同一个后端不该给两个人两种「这场测评的状态」——将来学生端要显示它的时候，
    手里那个值不该是写入时定死的字面量。
    """
    task = _make_task(
        db_session,
        start_at=NOW - timedelta(days=3),
        end_at=NOW + timedelta(days=3),
        task_no="TASK-STATUS-STUDENT",
    )
    headers = auth_headers(client, "student", "S001")

    def status_now() -> str:
        items = client.get("/api/v1/student/tasks", headers=headers).json()["data"]["items"]
        return next(item for item in items if item["id"] == task.id)["status"]

    assert status_now() == "ACTIVE"
    task.end_at = NOW - timedelta(minutes=1)
    db_session.commit()
    assert status_now() == "CLOSED"


# --- 同一个判据也是「能不能新开一张卷子」的那道门 ------------------------------


def test_a_student_cannot_open_a_sheet_before_the_window_opens(client, db_session):
    task = _make_task(db_session, start_at=NOW + timedelta(days=3), end_at=NOW + timedelta(days=17))
    headers = auth_headers(client, "student", "S001")
    response = client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": task.id})
    assert response.status_code == 404


def test_a_student_cannot_open_a_sheet_after_the_deadline(client, db_session):
    task = _make_task(db_session, start_at=NOW - timedelta(days=30), end_at=NOW - timedelta(minutes=1))
    headers = auth_headers(client, "student", "S001")
    response = client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": task.id})
    assert response.status_code == 404


def test_a_student_already_answering_keeps_their_sheet_after_the_deadline(client, db_session):
    """**窗口关掉不该把人从卷子上踢出去**（这条钉住 `create_or_get_session` 里的顺序）。

    截止日期管的是「还能不能再开一份新卷子」，不是「手里这份还算不算数」。
    原先把状态检查写在会话查询**之前**，一旦状态能推出 CLOSED，一个答到一半的学生
    在窗口关闭那一刻会直接拿到 404——连答案都接着存不下去。所以那道门现在排在
    「已有会话直接返回」之后：
    """
    task = _make_task(db_session, start_at=NOW - timedelta(days=3), end_at=NOW + timedelta(days=3))
    headers = auth_headers(client, "student", "S001")
    opened = client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": task.id})
    assert opened.status_code == 200
    session_id = opened.json()["data"]["id"]

    # 窗口在他答题期间关掉了
    task.end_at = NOW - timedelta(minutes=1)
    db_session.commit()

    again = client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": task.id})
    assert again.status_code == 200
    assert again.json()["data"]["id"] == session_id
    # 而且他还能接着答
    assert (
        client.put(
            f"/api/v1/assessment-sessions/{session_id}/answers/1",
            headers=headers,
            json={"answer": "YES"},
        ).status_code
        == 200
    )
