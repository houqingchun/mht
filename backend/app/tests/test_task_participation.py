"""这场测评他该不该参加：标记、应测口径、两份清单导出（§18.10 / §18.11 / §20#14，阶段 8）。

§18.10 那句 `应测人数 = 任务目标人数 - 请假 - 免测 - 已排除` 在这三个端点里变成
可执行的东西：`GET .../participation` 报那六个数、`PATCH .../participation` 是那三个
减项的**唯一写入方**、`POST .../non-participants/export` 是那些数离开这栋楼的那条路。

文件末尾那一组是**第二份清单**（`POST .../unmatched-import-rows/export`，§20#14 的
「导出」那一半）：它导的不是「应测没完成的学生」，而是「这场任务下没进得去的导入行」，
性质完全不同（一段行记录，不是一批人），门槛却与未参与名单**逐字同一对**
（`ensure_detail_exporter`）——所以两组并排放，读者自己去比对。

这一层最容易做错的一处是**分子与分母来自同一个集合**：「一名被标记免测的学生后来
还是答了卷」时，按 `status` 数分子、按 `expected` 数分母会给出一个超过 100% 的完成率。
`expected_participation_predicate` 那个函数把两者收在一条谓词里，下面
`test_a_student_who_was_excused_stays_out_of_both_the_top_and_the_bottom` 钉住它。

**标记不动任何答题事实**（§1 的四层事实模型）：`status`、会话、答卷、结果一个不碰
——「该不该参加」与「参没参加」是两个维度。
"""

import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    PARTICIPATION_EXEMPT,
    PARTICIPATION_LEAVE,
    PARTICIPATION_REQUIRED,
)
from app.models.audit import AuditLog
from app.models.enums import ScopeType
from app.models.organization import ClassGroup, Student
from app.models.scale import AssessmentScale
from app.services.assessment_import_service import UNMATCHED_ROW_LIMIT
from app.tests.conftest import auth_headers
from app.tests.factories import make_target
from app.tests.test_assessment_import_api import (
    _commit,
    _csv,
    _preview_data,
    _row,
    _student_scoped_counselor,
    _task_with_targets,
    add_students,
)
from app.tests.test_data_scope import (
    make_same_school_student_in_another_class,
    replace_scopes,
    seed_counselor,
    seed_student,
)
from app.tests.test_export_jobs import download

COUNSELOR = ("counselor", "13800000001")
LEADER = ("leader", "13800000002")
ADMIN = ("admin", "admin")

NOW = datetime.now()


def _task_with(db, *students, task_no="TASK-PARTICIPATION-1", **overrides) -> AssessmentTask:
    """建一场把这几名学生都发进去的任务，并返回它。

    直接用 ORM 而不是 `POST /assessment-tasks`：那个端点只发给「创建者范围里所有
    在读学生」，造不出「这一场只发给这三个人」这种形状。
    """
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    school_id = students[0].school_id if students else seed_student(db).school_id
    task = AssessmentTask(
        task_no=task_no,
        name="参与口径用例",
        scale_id=scale.id,
        school_id=school_id,
        scope_type="SCHOOL",
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=1),
        status="ACTIVE",
        **overrides,
    )
    db.add(task)
    db.flush()
    for student in students:
        make_target(db, student, task)
    db.commit()
    return task


def _target_of(db, task_id: int, student_no: str) -> AssessmentTarget:
    return db.scalar(
        select(AssessmentTarget)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(AssessmentTarget.task_id == task_id, Student.student_no == student_no)
    )


def _counts(client, headers, task_id: int) -> dict:
    response = client.get(f"/api/v1/assessment-tasks/{task_id}/participation", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _mark(client, headers, task_id: int, target_id: int, **payload):
    return client.patch(
        f"/api/v1/assessment-tasks/{task_id}/targets/{target_id}/participation",
        headers=headers,
        json=payload,
    )


def _csv_rows(response) -> list[list[str]]:
    """把一份导出的 CSV 解析成行（丢掉表头，也丢掉空行）。

    走 `csv.reader` 而不是 `line.split(",")`：下面「未匹配行清单」那张表有一列是
    服务端拼好的**整句话**（`_row_message`），它里面嵌着文件里的原值——`csv.writer`
    会给带逗号的字段加引号，而按逗号切会把它切成两列，症状是某一行的列数莫名其妙
    多一个（或者那句中文被截成两半）。这个 helper 一开始是两行的写法，加了那一列
    之后它就是错的了。
    """
    return [
        row
        for row in csv.reader(io.StringIO(response.content.decode("utf-8-sig")))
        if row
    ][1:]


def _audits(db, action: str) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())
        ).all()
    )


# --- 应测口径：一条谓词同时管分子与分母 ---------------------------------------


def test_the_counts_split_the_target_list_into_expected_and_excluded(client, db_session):
    """六个数各自是什么，以及**标记之前它们是什么**。

    先断一次标记之前的三个数，再断标记之后的——只断后半句的话，一个把
    「已排除」写死成 0 的实现也能通过（初始状态下它恰好是 0）。
    """
    student = seed_student(db_session)
    other = make_same_school_student_in_another_class(db_session)
    task = _task_with(db_session, student, other)
    counselor = auth_headers(client, *COUNSELOR)

    before = _counts(client, counselor, task.id)
    assert before["total_targets"] == 2
    assert before["excluded_targets"] == 0
    assert before["expected_targets"] == 2

    assert _mark(
        client, counselor, task.id, _target_of(db_session, task.id, "S900").id,
        disposition=PARTICIPATION_LEAVE, reason="骨折在家休养",
    ).status_code == 200

    after = _counts(client, counselor, task.id)
    # 目标行还在，一行都没删——「排除」不是「删掉」（§1）。
    assert after["total_targets"] == 2
    assert after["excluded_targets"] == 1
    assert after["expected_targets"] == 1
    # 没进名单的人不在分子里，而这一场的完成率因此是「1 人里的 0 人」。
    assert after["completed_targets"] == 0
    assert after["completion_rate"] == 0


def test_a_student_who_was_excused_stays_out_of_both_the_top_and_the_bottom(
    client, db_session
):
    """**分子与分母来自同一个集合。** 这是这一层最容易做错的一处。

    一名被标记免测的学生**后来又答了卷**（学校标晚了、或者他自己补上了）：那一行
    仍然是 `COMPLETED`（完成明细里照旧列着他、照旧有分），但他不进应测名单，所以
    他既不进分子也不进分母。按 `status` 数分子、按 `expected` 数分母会给出
    `1 / 1 = 100%`，而按 `expected` 数分子得到 `0 / 1 = 0%`——两者都能自圆其说，
    只有后者不会在某一天变成 `2 / 1 = 200%`。
    """
    student = seed_student(db_session)
    other = make_same_school_student_in_another_class(db_session)
    task = _task_with(db_session, student, other)
    counselor = auth_headers(client, *COUNSELOR)

    # 两个人**都**答完了——这一场看起来是 100%。
    for student_no in ("S001", "S900"):
        _target_of(db_session, task.id, student_no).status = "COMPLETED"
    db_session.commit()
    assert _counts(client, counselor, task.id)["completion_rate"] == 100

    # 然后其中一个人被改判成免测。
    assert _mark(
        client, counselor, task.id, _target_of(db_session, task.id, "S900").id,
        disposition=PARTICIPATION_EXEMPT, reason="医院证明免于参加",
    ).status_code == 200

    counts = _counts(client, counselor, task.id)
    assert counts["total_targets"] == 2
    assert counts["expected_targets"] == 1
    assert counts["completed_targets"] == 1, "剩下那一名应测的学生确实完成了"
    assert counts["completion_rate"] == 100

    # 而他那一行**什么都没变**：完成明细里他仍然是 COMPLETED，分数也还在。
    assert _target_of(db_session, task.id, "S900").status == "COMPLETED"


def test_a_missing_task_is_a_404(client):
    """`task_id` 是客户端传来的，不存在的给 404 而不是一份全 0 的统计。"""
    response = client.get(
        "/api/v1/assessment-tasks/999999/participation", headers=auth_headers(client, *COUNSELOR)
    )
    assert response.status_code == 404
    assert "不存在" in response.json()["error"]["message"]


# --- 标记：三个码、一个必填原因、不动答题事实 ---------------------------------


def test_marking_outside_the_required_set_needs_a_reason(client, db_session):
    """非 `REQUIRED` 必须给原因，`REQUIRED` 不给。

    两个方向都在这一条里：漏填与只填空格都要挡（`  ` 与 `""` 在界面上是同一件事），
    而「恢复成应测」**不该**被那句规则连坐——它是一次取消标记，没有理由可写。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)
    counselor = auth_headers(client, *COUNSELOR)
    target_id = _target_of(db_session, task.id, "S001").id

    missing = _mark(client, counselor, task.id, target_id, disposition=PARTICIPATION_LEAVE)
    assert missing.status_code == 422
    assert "原因" in missing.json()["error"]["message"]

    blank = _mark(
        client, counselor, task.id, target_id, disposition=PARTICIPATION_LEAVE, reason="   "
    )
    assert blank.status_code == 422

    assert _mark(client, counselor, task.id, target_id, disposition=PARTICIPATION_REQUIRED).status_code == 200


def test_the_disposition_is_validated_against_the_documented_vocabulary(client, db_session):
    """未知的码 422——**取值域只有服务端那一份定义**（`PARTICIPATION_DISPOSITIONS`）。

    请求模型里刻意没有 `pattern`：一张在请求模型里再抄一遍的码表就是第二个定义，
    而它与服务端那张漂移之后，屏幕上会先通过校验、再撞一句英文 422。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)
    counselor = auth_headers(client, *COUNSELOR)

    response = _mark(
        client, counselor, task.id, _target_of(db_session, task.id, "S001").id,
        disposition="SICK", reason="随便写一个不在码表里的",
    )
    assert response.status_code == 422
    assert "参与状态" in response.json()["error"]["message"]


def test_going_back_to_required_clears_the_reason_and_still_records_who_did_it(
    client, db_session
):
    """恢复成 `REQUIRED` 等于取消标记：原因清掉，而 `marked_by` / `marked_at`
    **两个方向都写**。

    它们回答的是「谁最后动了这一行」，所以一行从「请假」改回「应测」之后仍然看得出
    是谁改的。只写一个方向的话，轨迹上最后那一次改动没有主人。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)
    counselor = auth_headers(client, *COUNSELOR)
    target_id = _target_of(db_session, task.id, "S001").id

    _mark(
        client, counselor, task.id, target_id,
        disposition=PARTICIPATION_LEAVE, reason="骨折在家休养", note="家长已告知",
    )
    row = _target_of(db_session, task.id, "S001")
    assert row.disposition_reason == "骨折在家休养"
    assert row.disposition_note == "家长已告知"
    assert row.marked_at is not None
    marked_by = row.marked_by

    assert _mark(
        client, counselor, task.id, target_id, disposition=PARTICIPATION_REQUIRED
    ).status_code == 200
    row = _target_of(db_session, task.id, "S001")
    assert row.participation_disposition == PARTICIPATION_REQUIRED
    assert row.disposition_reason is None, "取消标记要把原因一起清掉"
    assert row.marked_by == marked_by
    assert row.marked_at is not None


def test_marking_records_the_transition_not_just_the_new_value(client, db_session):
    """审计的 `detail` 记 **before → after**，因为只记新值的话，「从请假改回应测」
    那一条与「本来就是应测」长得一模一样。

    第一次标记的 before 是 `REQUIRED` 而不是空：那一列是 `NOT NULL DEFAULT 'REQUIRED'`
    （`models/assessment.py`），「没标记过」与「标记成应测」在库里本来就同值——所以
    轨迹上要回答「这一行原本是什么」，只能靠这里记下的那一个词。
    `disposition_reason` 反过来真的会是 `None`，那才是 `NONE` 出现的地方（第二条断言）。
    `student_id` 也要有——那一列才让这次改动出现在这名学生的敏感访问记录里。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)
    counselor = auth_headers(client, *COUNSELOR)
    target_id = _target_of(db_session, task.id, "S001").id

    _mark(
        client, counselor, task.id, target_id,
        disposition=PARTICIPATION_EXEMPT, reason="医院证明免于参加",
    )
    first = _audits(db_session, "标记参与状态")[0]
    assert "REQUIRED → EXEMPT" in first.detail
    assert "医院证明免于参加" in first.detail
    assert first.student_id == student.id
    assert first.resource_id == str(task.id)

    _mark(client, counselor, task.id, target_id, disposition=PARTICIPATION_REQUIRED)
    second = _audits(db_session, "标记参与状态")[0]
    assert "EXEMPT → REQUIRED" in second.detail
    # 原因清掉之后记 `NONE`——它说的是「这一行现在没有原因」，而不是「没问过」。
    assert "原因=NONE" in second.detail


def test_a_target_of_another_task_is_a_404(client, db_session):
    """「不是这场任务的」与「不存在」回**同一句话同一个码**。

    `target_id` 是客户端传来的，分开报就等于确认了某个 id 存在（§24）。这里先证明
    「那个 target 确实存在、只是属于别的一场」，否则后半句在没建过第二个 target 的
    库上也是绿的。
    """
    student = seed_student(db_session)
    first = _task_with(db_session, student, task_no="TASK-PARTICIPATION-A")
    second = _task_with(db_session, student, task_no="TASK-PARTICIPATION-B")
    counselor = auth_headers(client, *COUNSELOR)
    foreign_id = _target_of(db_session, first.id, "S001").id
    assert db_session.get(AssessmentTarget, foreign_id) is not None

    theirs = _mark(
        client, counselor, second.id, foreign_id,
        disposition=PARTICIPATION_LEAVE, reason="走错任务了",
    )
    missing = _mark(
        client, counselor, second.id, 999999,
        disposition=PARTICIPATION_LEAVE, reason="走错任务了",
    )

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["error"] == missing.json()["error"]
    assert db_session.get(AssessmentTarget, foreign_id).participation_disposition == PARTICIPATION_REQUIRED


def test_a_mark_outside_the_counselors_scope_is_refused_without_an_audit(client, db_session):
    """范围拒绝时 **403 且不写审计**（§9）。

    「他是心理老师」不说「他是**这名**学生的心理老师」：一位只带 CLASS 范围的老师
    改不动别人班上的行。不写审计的理由与拒绝读取那一条同源——给一次被拒的写入记上
    「标记参与状态」，会让访问轨迹反过来撒谎。

    **先证明那道门是范围而不是别的东西**：同一名老师、同一个端点，改自己班上的行
    是 200。少了这一半，一个「心理老师一律 403」的坏实现在这里也是绿的。
    """
    mine = seed_student(db_session)
    theirs = make_same_school_student_in_another_class(db_session)
    task = _task_with(db_session, mine, theirs)
    counselor = auth_headers(client, *COUNSELOR)
    user = seed_counselor(db_session)
    # 只带 S001 那个班——他班那位学生因此落在这位老师的范围之外。
    replace_scopes(
        db_session,
        user,
        {"scope_type": ScopeType.CLASS, "school_id": mine.school_id, "class_id": mine.class_id},
    )

    allowed = _mark(
        client, counselor, task.id, _target_of(db_session, task.id, "S001").id,
        disposition=PARTICIPATION_LEAVE, reason="骨折在家休养",
    )
    assert allowed.status_code == 200

    before = _audits(db_session, "标记参与状态")
    refused = _mark(
        client, counselor, task.id, _target_of(db_session, task.id, "S900").id,
        disposition=PARTICIPATION_LEAVE, reason="骨折在家休养",
    )
    assert refused.status_code == 403
    assert len(_audits(db_session, "标记参与状态")) == len(before)
    assert (
        _target_of(db_session, task.id, "S900").participation_disposition
        == PARTICIPATION_REQUIRED
    )


def test_marking_does_not_touch_the_students_sheet(client, db_session):
    """**标记不动任何答题事实**（§1）。

    这一条断的是「什么都没变」：目标行的 `status`、会话、答卷、结果一个不碰。
    「该不该参加」与「参没参加」是两个维度，标记为请假永远只是让这一行不进应测
    名单，不是「把他标成没完成」。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)
    counselor = auth_headers(client, *COUNSELOR)
    target_id = _target_of(db_session, task.id, "S001").id

    # 先让他真的答一场（会话 + 一条答案），这样「没被碰过」才有东西可断。
    student_headers = auth_headers(client, "student", "S001")
    session_response = client.post(
        "/api/v1/assessment-sessions", headers=student_headers, json={"task_id": task.id}
    )
    assert session_response.status_code == 200, session_response.text
    session_id = session_response.json()["data"]["id"]
    # `SaveAnswerRequest.answer` 的 pattern 是 `^(YES|NO)$`——中文「是」会被 422 挡在
    # 请求模型那一层，而 422 与「标记动了答卷」在屏幕上长得完全不一样，很容易被读成
    # 后者（实测踩过一次）。
    assert client.put(
        f"/api/v1/assessment-sessions/{session_id}/answers/1",
        headers=student_headers,
        json={"answer": "YES"},
    ).status_code == 200
    db_session.commit()

    def _snapshot() -> dict:
        row = _target_of(db_session, task.id, "S001")
        session = db_session.get(AssessmentSession, session_id)
        answers = db_session.scalar(
            select(func.count())
            .select_from(AssessmentAnswer)
            .where(AssessmentAnswer.session_id == session_id)
        )
        return {
            "target_status": row.status,
            "session_status": session.status,
            "answers": int(answers),
        }

    before = _snapshot()
    assert before["answers"] == 1, "先证明这场卷子上真有一条答案"

    assert _mark(
        client, counselor, task.id, target_id,
        disposition=PARTICIPATION_LEAVE, reason="骨折在家休养",
    ).status_code == 200

    assert _snapshot() == before


# --- 未参与名单导出：第三道门槛与「谁不在这一份里」 ---------------------------


def _third_student(db, other: Student) -> Student:
    """青禾实验学校的第三名学生（`S901`），挂在 `other` 那个班上。

    这一条要的是**三个人同时在场**（一个答完、一个该答没答、一个请假），而
    `make_same_school_student_in_another_class` 写死 `S900` 与 `2班`——再调一次
    不是「再造一名学生」，是撞 `uq_student_school_no`（`Duplicate entry '1-S900'`）。

    所以它收**已经造好的那一位**当参数，只从它身上抄学校 / 年级 / 班级，不在函数体里
    自己去造第二个人。`class_id` 直接取 `other.class_id` 就够了：`ClassGroup.id` 是
    主键，`select(...).where(id == other.class_id).id` 绕一圈读回来的是同一个数。
    """
    student = Student(
        student_no="S901",
        name="第三名同学",
        masked_name="第**",
        school_id=other.school_id,
        grade_id=other.grade_id,
        class_id=other.class_id,
    )
    db.add(student)
    db.flush()
    return student


def _export_non_participants(client, headers, task_id: int):
    return client.post(
        f"/api/v1/assessment-tasks/{task_id}/non-participants/export",
        headers=headers,
        json={"purpose": "秋季普查补测催办"},
    )


def test_the_non_participant_list_leaves_out_the_excused_and_the_finished(client, db_session):
    """这一份是**应测却没完成**的人，不是「全部非完成行」。

    请假的那一位不进这一份：他不是「没测」，是学校已经决定他这次不测——把两者混在
    一起，这份名单就从「还有谁要催」变成了「这场测评的全部非完成行」，而读者会照着
    它去找已经被豁免的学生（§11）。

    三条都摆上：一个答完的、一个该答没答的、一个请假的。**先证明表里有东西**，
    再断它只有一行——空表格上的「只有一行」恒不成立，但空表格上的「没有请假那个人」
    却恒成立，那才是这一条真正要防的假绿。
    """
    finished = seed_student(db_session)
    pending = make_same_school_student_in_another_class(db_session)
    excused = _third_student(db_session, pending)
    task = _task_with(db_session, finished, pending, excused)
    counselor = auth_headers(client, *COUNSELOR)

    _target_of(db_session, task.id, "S001").status = "COMPLETED"
    db_session.commit()
    assert _mark(
        client, counselor, task.id, _target_of(db_session, task.id, "S901").id,
        disposition=PARTICIPATION_LEAVE, reason="骨折在家休养",
    ).status_code == 200

    created = _export_non_participants(client, counselor, task.id)
    assert created.status_code == 200, created.text
    job = created.json()["data"]
    assert job["row_count"] == 1
    # 这一份写的是学号与真实姓名，所以这一列照实记 `IDENTIFIED`——它回答的是
    # 「这份文件是不是实名的」，不是「我们打不打算遮蔽」。
    assert job["mask_level"] == "IDENTIFIED"

    rows = _csv_rows(download(client, counselor, job["id"]))
    assert len(rows) == 1
    assert rows[0][0] == "S900", "剩下的是那个该答没答的"
    assert "请假" not in download(client, counselor, job["id"]).text


def test_the_non_participant_export_has_three_gates(client, db_session):
    """三道门槛，问的是三件不同的事（§18.11 第九个端点）。

    | 门槛 | 谁被挡 | 为什么 |
    |---|---|---|
    | 任务读者 | 系统管理员 | 测评任务不是能力、是角色（§4） |
    | `CONTROLLED_EXPORT` | 系统管理员 | `BASE_ONLY` 不是「能导出明细」 |
    | `STUDENT_PSYCH_DETAIL: {SCOPED}` | 德育领导 | `SUMMARY` 只是聚合，而这一份逐行给姓名 |

    **三种角色各断一次**，因为这三道门挡下来的是不同的人：只断 admin 的话，
    一个「只要不是管理员就放行」的实现会通过，而那一档恰好把德育领导放进来了。
    这里先把「心理老师能拿到」当基线——没有它，三条 403 在一个「这个端点根本
    没实现」的库上也全绿。

    被拒时**不建作业**：`export_job` 里那一行是「有一份文件出去了」的证据，给一次
    被拒的导出留一行会反过来读成「确实导出去了」（§9 那条「拒绝时不写审计」的同一个
    形状）。判据是**作业数没变**，不是「某个 id 不在列表里」——后者在一张空表上恒真。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)
    counselor = auth_headers(client, *COUNSELOR)
    jobs = "/api/v1/export-jobs"

    before = len(client.get(jobs, headers=counselor).json()["data"]["items"])

    allowed = _export_non_participants(client, counselor, task.id)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["row_count"] == 1
    assert len(client.get(jobs, headers=counselor).json()["data"]["items"]) == before + 1

    for role, account in (LEADER, ADMIN):
        response = _export_non_participants(client, auth_headers(client, role, account), task.id)
        assert response.status_code == 403, f"{role} 不该拿到这一份"
        assert len(client.get(jobs, headers=counselor).json()["data"]["items"]) == before + 1


def test_the_target_list_is_still_gated_by_the_roster_capability(client, db_session):
    """`GET .../targets` 那两道门槛**不受这一期影响**（§22 的镜像）。

    这一条放在这里是为了挡住一次很自然的「顺手合并」：既然未参与名单要心理详情
    明细，那把目标学生名单也一起收紧吧——**不行**，那会让一份「这场发给了谁」的
    名册报表变成需要个案权限，而这两件事问的不是同一个问题。德育领导读得了目标
    名单（`ORG_ACCOUNT: READ_SUMMARY`），但读不了未参与名单。
    """
    student = seed_student(db_session)
    task = _task_with(db_session, student)

    leader = auth_headers(client, *LEADER)
    assert client.get(
        f"/api/v1/assessment-tasks/{task.id}/targets", headers=leader
    ).status_code == 200
    # 而同一场测评的参与统计他也读得到——那是任务读者那一档。
    assert client.get(
        f"/api/v1/assessment-tasks/{task.id}/participation", headers=leader
    ).status_code == 200

    # 系统管理员：两道都进不去（任务不是能力、是角色）。
    admin = auth_headers(client, *ADMIN)
    assert client.get(
        f"/api/v1/assessment-tasks/{task.id}/targets", headers=admin
    ).status_code == 403
    assert client.get(
        f"/api/v1/assessment-tasks/{task.id}/participation", headers=admin
    ).status_code == 403


# --------------------------------------------------------------------------
# 第 8 期 B：未匹配行清单导出（§20#14 的「导出」那一半，第十个端点）
#
# §20#14 要的是四类进不来的导入行（未匹配 / 任务外 / 重复 / 冲突）「可以查询**和导出**」。
# 查询那一半阶段 5 交付了（`GET .../unmatched-import-rows`），导出那一半没有落点——
# `§18.11` 冻结的九个端点里那个 `non-participants/export` 导的是**应测未完成的学生**，
# 是另一批人。所以补了第十个端点。
#
# 这一组与上面未参与名单那一组**并排放**，理由就是它们共用同一对判据
# （`ensure_detail_exporter`）：两个端点的门槛必须一眼看得出是同一对，读者自己去比对
# 比读两遍注释可靠。本文件的导入列表里有 `_task_with_targets` 那些 helper，来自
# `test_assessment_import_api`——造导入行这一步的写法只有那一处，不在这里重写一份。
# --------------------------------------------------------------------------


def _export_unmatched(client, headers, task_id: int):
    return client.post(
        f"/api/v1/assessment-tasks/{task_id}/unmatched-import-rows/export",
        headers=headers,
        json={"purpose": "未匹配行复核清单"},
    )


def _unmatched(client, headers, task_id: int) -> dict:
    response = client.get(
        f"/api/v1/assessment-tasks/{task_id}/unmatched-import-rows", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _add_roster(client) -> None:
    """走**学生信息导入**把 S701 加进名册（班级 704）。

    刻意不直接写 `Student` 行：这条链路上「班级按 704 编号」这个约定由导入校验，
    手工插一行等于绕开它——而下面那几行 `_row(…, 1, 4)` 正是靠这条约定才匹配得上。
    """
    add_students(client, auth_headers(client, *ADMIN), [("S701", "赵同学", "男", 12)])


def _two_unmatched_rows(client, task) -> dict:
    """造这场任务下两行「没进得去」：一行**匹配上了但被放弃**，一行名册上没有。

    这两行是刻意挑的，它们各自钉住判据的一半：

    * 被放弃那一行走的是 `processing_status == SKIPPED` 那**第二个判据**——少了它，
      一个「选了放弃」的批次在这一页上会一条都不剩，而这一页的读者正是要找出
      「这场任务还有谁缺着」；
    * 名册上没有那一行的 `student_id` 是 NULL，于是它在**范围过滤**那一支上走的是
      「没有学生可判、照旧显示」那半句（另有用例钉它）。

    ★ 被放弃的那一行是 `AGE_CONFLICT`，**不是 `MATCHED`**。这一条是 2026-09-20 撞出来的
    第一版夹具写错的地方：`_row_will_be_written` 对 `MATCHED` 落在最后那句
    `return True` 上，所以「匹配上了」的行**整批选放弃也照写**，`MATCHED` + `SKIPPED`
    在库里造不出来（`resolve_import_row` 也会拿 422「不需要确认」挡掉对它的逐行处置）。
    第一版是照 `UNMATCHED_REASON_LABELS` 里「已匹配但被放弃」那句写的，于是夹具停在
    一个**不可达的状态**上——三个用例一起红，红的原因却不是被测代码。
    被放弃是要拍板的那三档的事（这里取 `AGE_CONFLICT`：文件里 13 岁、名册上 12 岁），
    所以这一行在文件里那一格读作「年龄与名册不符」。

    调用方要保证两件事已经就位：S701 在名册上（`_add_roster`）、而**这名学生是这场
    任务的目标**——`_task_with_targets` 是在名册之后建的，所以这一步排在它后面。
    """
    grader = auth_headers(client, *COUNSELOR)
    data = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 2, 13, 1, 4), _row("查无此人", 2, 12, 1, 4)]),
        task_id=task.id,
    )
    by_no = {row["row_no"]: row for row in data["rows"]}
    assert [by_no[2]["match_status"], by_no[3]["match_status"]] == ["AGE_CONFLICT", "NOT_FOUND"]
    # 整批选「放弃」——只有提交之后这一行才真的落在「没进得去」里
    assert _commit(client, grader, data["id"], resolution="skip").status_code == 200

    # 造完当场把夹具的**前提**证明一次：`AGE_CONFLICT` 不在「进不去」那四档里，
    # 所以这一行只能走 `processing_status == SKIPPED` 那第二个判据。少了这一句，
    # 哪天下面的用例改用了 `NOT_FOUND` 之类，夹具的两个意图会一起失效而没人知道。
    listed = _unmatched(client, grader, task.id)
    assert [item["match_status"] for item in listed["items"]] == ["AGE_CONFLICT", "NOT_FOUND"]
    assert listed["items"][0]["processing_status"] == "SKIPPED"
    return data


def test_the_unmatched_rows_export_has_the_same_gates_as_the_non_participants_one(
    client, db_session
):
    """第十个端点的四道门槛，与第九个**逐字同一对**（`ensure_detail_exporter`）。

    | 门槛 | 谁被挡 | 为什么 |
    |---|---|---|
    | 任务读者 | 系统管理员 | 测评任务不是能力、是角色（§4） |
    | `ORG_ACCOUNT` 的读权 | （今天没有角色只被这一道挡住） | 这一页逐行印出姓名，那是组织与账号那一档 |
    | `CONTROLLED_EXPORT` | 系统管理员 | `BASE_ONLY` 不是「能导出明细」 |
    | `STUDENT_PSYCH_DETAIL: {SCOPED}` | 德育领导 | `SUMMARY` 只是聚合，而这一份逐行给姓名 |

    **三种角色各断一次**：只断 admin 的话，一个「只要不是管理员就放行」的实现会通过，
    而那一档恰好把德育领导放进来了——他被挡下来的理由与 admin 完全不同。

    被拒时**不建作业**（判据是作业数没变，不是「某个 id 不在列表里」——后者在空表上
    恒真），也不写审计。被拒写审计那条同理：给一次没发生过的导出留一行「导出未匹配行」，
    轨迹反过来读成「确实导出去了」（§9）。
    """
    student = seed_student(db_session)
    _add_roster(client)
    task = _task_with_targets(db_session, [student.student_no, "S701"])
    _two_unmatched_rows(client, task)
    counselor = auth_headers(client, *COUNSELOR)
    jobs = "/api/v1/export-jobs"

    before = len(client.get(jobs, headers=counselor).json()["data"]["items"])
    audits_before = len(_audits(db_session, "导出未匹配行"))

    allowed = _export_unmatched(client, counselor, task.id)
    assert allowed.status_code == 200, allowed.text
    job = allowed.json()["data"]
    assert job["row_count"] == 2
    # 逐行印出文件里的姓名，所以这一列照实记 `IDENTIFIED`——它回答的是「这份文件是不是
    # 实名的」，不是「我们打不打算遮蔽」（与未参与名单那条同一个口径）。
    assert job["mask_level"] == "IDENTIFIED"
    assert len(client.get(jobs, headers=counselor).json()["data"]["items"]) == before + 1
    assert len(_audits(db_session, "导出未匹配行")) == audits_before + 1

    for role, account in (LEADER, ADMIN):
        response = _export_unmatched(client, auth_headers(client, role, account), task.id)
        assert response.status_code == 403, f"{role} 不该拿到这一份"
        assert len(client.get(jobs, headers=counselor).json()["data"]["items"]) == before + 1
        assert len(_audits(db_session, "导出未匹配行")) == audits_before + 1


def test_the_unmatched_export_reads_the_same_rows_and_the_same_wording_as_the_screen(
    client, db_session
):
    """文件与屏幕是**同一批行的两种出口**：条数、次序、措辞逐字相同（§11）。

    三样各挡一个错法：

    * **条数**：文件按 `limit` 截断、或多算了被放弃的行，两个数当场不等；
    * **次序**：两处各写一个 `order_by`，拿到文件的人与看着屏幕的人会报出两个「第一行」；
    * **措辞**：那一列必须是**这一屏**的读法（`unmatched_reason_label`），所以这里断的
      是中文而不是码，顺带也断了**码一个都不许漏出来**。

    被放弃那一行的中文是「年龄与名册不符」——它照 `MATCH_STATUS_LABELS` 来，
    因为 `SKIPPED` 只长在要拍板的那三档上（`AGE_CONFLICT` / `DUPLICATE` / `CONFLICT`）。
    `UNMATCHED_REASON_LABELS` 那一处 `MATCHED: '已匹配但被放弃'` 按今天的行为**不可达**
    （见 `_two_unmatched_rows` 的 docstring），所以没有一行能断到它——那是那个覆盖
    自己的事，不写一条恒绿的断言来假装它被守住了。
    """
    student = seed_student(db_session)
    _add_roster(client)
    task = _task_with_targets(db_session, [student.student_no, "S701"])
    _two_unmatched_rows(client, task)
    counselor = auth_headers(client, *COUNSELOR)

    listed = _unmatched(client, counselor, task.id)
    created = _export_unmatched(client, counselor, task.id)
    assert created.status_code == 200, created.text
    job = created.json()["data"]
    assert job["row_count"] == listed["total"]

    rows = _csv_rows(download(client, counselor, job["id"]))
    assert len(rows) == len(listed["items"]) == 2
    # 次序与列表端点逐字相同（批次从新到旧、批内按行号）
    assert [row[1] for row in rows] == [str(item["row_no"]) for item in listed["items"]]
    # 「匹配结论」是第 6 列（0 起算 5），两行各是这一屏的那句话
    assert [row[5] for row in rows] == ["年龄与名册不符", "名册上没有"]
    body = download(client, counselor, job["id"]).text
    for code in ("MATCHED", "NOT_FOUND", "SKIPPED"):
        assert code not in body, f"{code} 漏进了文件正文"
    # 表头与服务端那七列白名单逐字相同（§16.3：导出接口不得接受任意字段名）
    header = csv.reader(io.StringIO(body.lstrip("﻿"))).__next__()
    assert header == ["批次", "行号", "文件里的姓名", "年级", "班级", "匹配结论", "说明"]
    # 行号是**批内**编号，所以「批次」那一列不是装饰：一场任务下可以有好几批
    assert rows[0][0].startswith("BATCH-")


def test_the_unmatched_export_is_not_capped_even_though_the_screen_is(client, db_session):
    """屏幕上封顶 200 行、文件**一行不封**（§10：截断只许影响显示，不许影响写入）。

    造 `UNMATCHED_ROW_LIMIT + 1` 行：**先证明屏幕上确实截断了**（`total` 201 而
    `items` 200——少了这一半，一个把 `limit` 也传进导出的实现照样过），再断言文件是 201。

    这一条挂在「导出」这一侧是因为截断在这里的代价与屏幕上不同：屏幕翻页有出路，
    而一份文件截断了没有任何东西看得出来——收到的人会以为「这场就只有这 200 行」，
    而屏幕上正写着「整场共 201 行没进得去」。`unmatched_rows_csv` 因此**没有 `limit`
    参数**：不是忘了传，是它不该有。
    """
    task = _task_with_targets(db_session, [])
    grader = auth_headers(client, *COUNSELOR)
    # 空姓名 → `INVALID_ROW`（在第 2 步就返回，还没走到查名册），
    # 所以这 201 行的结论不依赖名册此刻的内容。
    data = _preview_data(
        client,
        grader,
        _csv([_row("", 2, 12, 1, 4) for _ in range(UNMATCHED_ROW_LIMIT + 1)]),
        task_id=task.id,
    )
    assert _commit(client, grader, data["id"]).status_code == 200

    listed = _unmatched(client, grader, task.id)
    assert listed["total"] == UNMATCHED_ROW_LIMIT + 1
    assert len(listed["items"]) == UNMATCHED_ROW_LIMIT

    created = _export_unmatched(client, grader, task.id)
    assert created.status_code == 200, created.text
    job = created.json()["data"]
    assert job["row_count"] == UNMATCHED_ROW_LIMIT + 1
    assert len(_csv_rows(download(client, grader, job["id"]))) == UNMATCHED_ROW_LIMIT + 1


def test_the_unmatched_export_still_filters_by_the_readers_scope(client, db_session):
    """范围在导出这一侧照旧生效，而**没匹配到学生的那一行照旧显示**。

    那半句是这一条真正的判据：「把行藏起来」会造出比藏之前**更强**的枚举——行在 = 那个班
    没这个人，一次上传问遍全校（`_unmatched_row_conditions` 记着这段）。所以范围那一支
    是 `student_id IS NULL OR 学生在范围内`，而不是裸的谓词。只断「范围外的行不在文件里」
    的话，一个把 `student_id IS NULL` 那些行一起过滤掉的实现也会通过，而它恰好把
    操作员最需要看见的那几行（名册上没有的）删掉了。
    """
    student = seed_student(db_session)
    other = make_same_school_student_in_another_class(db_session)
    _add_roster(client)
    task = _task_with_targets(db_session, [student.student_no, other.student_no, "S701"])
    _two_unmatched_rows(client, task)

    # 一名只管着**另一名学生**的心理老师（`other`）：S701 那一行在校外，
    # 而「查无此人」那一行没有学生可判。
    scoped = _student_scoped_counselor(
        client,
        db_session,
        account="13900000011",
        student_no=other.student_no,
        display_name="只管一名学生的心理老师",
    )

    listed = _unmatched(client, scoped, task.id)
    assert listed["total"] == 1
    assert [item["raw_name"] for item in listed["items"]] == ["查无此人"]

    created = _export_unmatched(client, scoped, task.id)
    assert created.status_code == 200, created.text
    job = created.json()["data"]
    assert job["row_count"] == listed["total"]
    rows = _csv_rows(download(client, scoped, job["id"]))
    assert [row[2] for row in rows] == ["查无此人"], "同一个范围，文件与屏幕必须是同一批行"
