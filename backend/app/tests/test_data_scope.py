"""Row-level data scope: a counselor may only touch students inside their range.

Why a separate file: the rest of the suite is a *zero-signal* net for this. The
`db_session` fixture seeds exactly one school, one grade, one class and one
student, and every account's scope row covers all of it — so any predicate,
correct, inverted, or missing entirely, passes all of the existing tests.

Each test below therefore builds a second school (云海中学) whose students the
seeded counselor's SCHOOL/青禾实验学校 scope cannot reach.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.account import UserAccount, UserScope
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    DimensionResult,
    RiskEvent,
)
from app.models.audit import AuditLog
from app.models.care import FollowUpRecord, RetestPlan, StudentCareCase
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale
from app.security.passwords import hash_password
from app.tests.conftest import auth_headers
from app.tests.test_care_api import create_risk_case

OTHER_SCHOOL_CODE = "YH"


def make_other_school_student(db) -> Student:
    """A student at 云海中学 — outside the seeded counselor's 青禾 scope."""
    school = School(code=OTHER_SCHOOL_CODE, name="云海中学")
    db.add(school)
    db.flush()
    grade = Grade(school_id=school.id, name="初一", sort_order=0)
    db.add(grade)
    db.flush()
    class_group = ClassGroup(school_id=school.id, grade_id=grade.id, name="1班")
    db.add(class_group)
    db.flush()
    student = Student(
        student_no="Y001",
        name="别校学生",
        masked_name="别**",
        school_id=school.id,
        grade_id=grade.id,
        class_id=class_group.id,
    )
    db.add(student)
    db.flush()
    return student


def make_other_school_case(db) -> tuple[StudentCareCase, int]:
    """An open case at 云海中学, plus the session and pending risk event the
    review path needs.

    Building the risk event for real is the point: with a dangling id the review
    endpoint would 403 on the *risk-event* lookup whether or not the scope guard
    exists, so the test would pass for the wrong reason and prove nothing.
    """
    student = make_other_school_student(db)
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED"))
    session = AssessmentSession(
        student_id=student.id,
        scale_id=scale.id,
        scale_version=scale.version,
        status="SUBMITTED",
    )
    db.add(session)
    db.flush()
    risk_event = RiskEvent(
        student_id=student.id,
        session_id=session.id,
        risk_type="MANUAL_REVIEW_REQUIRED",
        risk_level="HIGH_SENSITIVITY",
        trigger_rule="KEY_QUESTION_85_YES",
        status="PENDING",
    )
    db.add(risk_event)
    care_case = StudentCareCase(student_id=student.id, status="PENDING_REVIEW")
    db.add(care_case)
    db.commit()
    return care_case, risk_event.id


def seed_student(db) -> Student:
    return db.scalar(select(Student).where(Student.student_no == "S001"))


def make_same_school_student_in_another_class(db) -> Student:
    """青禾实验学校的另一个班的学生：**同一所学校**，但在班级范围之外。

    和 `make_other_school_student` 的区别是刻意的。要证明「建任务时发放的范围」
    收在创建者的范围里，光有一所外校的学生证明不了什么——`create_school_assessment_task`
    本来就写着 `Student.school_id == school.id`，外校学生**没有那条谓词也进不来**，
    测试会为错误的原因变绿。同校、他班才是那条谓词唯一能挡住的人。
    """
    school = db.scalar(select(School).where(School.code == "QH"))
    grade = db.scalar(select(Grade).where(Grade.school_id == school.id))
    class_group = ClassGroup(school_id=school.id, grade_id=grade.id, name="2班")
    db.add(class_group)
    db.flush()
    student = Student(
        student_no="S900",
        name="同校他班",
        masked_name="同**",
        school_id=school.id,
        grade_id=grade.id,
        class_id=class_group.id,
    )
    db.add(student)
    db.flush()
    return student


def seed_counselor(db) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))


def make_other_school_population(db) -> Student:
    """A 云海中学 student with a row at every stage the scoped queries read.

    A bare `Student` would prove nothing about the query filters: with no target,
    session, result, case, follow-up or retest attached, it would be missing from
    every list whether or not those queries were scoped — the absence assertions
    would pass for the wrong reason. So this builds the whole record set, one row
    per query under test.

    Dates are relative to today because the reminder and retest panels only look
    a fixed horizon ahead; a hard-coded date would start failing on its own.
    """
    student = make_other_school_student(db)
    counselor = seed_counselor(db)
    today = datetime.now(UTC).date()

    task = db.scalar(select(AssessmentTask))
    if task:
        db.add(AssessmentTarget(task_id=task.id, student_id=student.id, status="COMPLETED"))

    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED"))
    session = AssessmentSession(
        student_id=student.id,
        scale_id=scale.id,
        scale_version=scale.version,
        status="SUBMITTED",
    )
    db.add(session)
    db.flush()
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=0,
            validity_status="VALID",
            total_score=90,
            total_level="KEY_ATTENTION",
            rule_version=scale.version,
        )
    )
    db.add(
        DimensionResult(
            session_id=session.id,
            dimension_code="LEARNING_ANXIETY",
            score=15,
            level="HIGH",
            interpretation="需关注",
            rule_version=scale.version,
        )
    )
    db.add(
        RiskEvent(
            student_id=student.id,
            session_id=session.id,
            risk_type="MANUAL_REVIEW_REQUIRED",
            risk_level="HIGH_SENSITIVITY",
            trigger_rule="KEY_QUESTION_85_YES",
            status="PENDING",
        )
    )
    db.add(StudentCareCase(student_id=student.id, status="FOLLOWING"))
    db.add(
        FollowUpRecord(
            student_id=student.id,
            operator_id=counselor.id,
            record_type="心理老师访谈",
            confirmed_facts="别校记录。",
            next_follow_up_date=today + timedelta(days=3),
            status="ACTIVE",
        )
    )
    db.add(
        RetestPlan(
            student_id=student.id,
            planned_date=today + timedelta(days=7),
            reason="阶段性复测",
            status="PLANNED",
            created_by=counselor.id,
        )
    )
    # A trail row that names this student — the kind the audit endpoint must not
    # hand to a counselor who cannot reach them.
    db.add(
        AuditLog(
            action="查看重点题",
            resource_type="STUDENT",
            resource_id=str(student.id),
            result="SUCCESS",
            actor_user_id=counselor.id,
            actor_role=RoleCode.COUNSELOR,
            student_id=student.id,
        )
    )
    db.commit()
    return student


def other_school_grade_id(db) -> int:
    return db.scalar(select(Grade.id).join(School, School.id == Grade.school_id).where(School.code == OTHER_SCHOOL_CODE))


def replace_scopes(db, user: UserAccount, *rows: dict) -> None:
    """Give the user exactly these scope rows, replacing whatever the seed wrote."""
    db.query(UserScope).filter(UserScope.user_id == user.id).delete()
    for row in rows:
        db.add(UserScope(user_id=user.id, **row))
    db.commit()


# --- the seven id-keyed entry points -----------------------------------------

# Payloads are deliberately well-formed: `_scoped_case` runs before any other
# validation, so a 403 here can only come from the scope check and not from a
# malformed request sneaking past it.
MUTATIONS = [
    (
        "reviews",
        {
            "risk_event_id": 1,
            "review_result": "建立持续关注档案",
            "confirmed_facts": "已与学生完成初步沟通。",
            "next_action": "安排下次跟进",
            "next_follow_up_date": str(date(2026, 9, 23)),
        },
    ),
    (
        "follow-ups",
        {
            "record_type": "心理老师访谈",
            "confirmed_facts": "本次跟进记录已确认事实。",
            "next_follow_up_date": str(date(2026, 9, 30)),
        },
    ),
    (
        "family-contacts",
        {
            "contact_date": str(date(2026, 9, 20)),
            "contact_person": "母亲",
            "channel": "电话",
            "result": "已联系",
            "support_status": "愿意配合",
            "confirmed_facts": "仅记录已确认事实。",
            "next_contact_date": str(date(2026, 9, 27)),
        },
    ),
    ("retests", {"planned_date": str(date(2026, 10, 15)), "reason": "阶段性复测"}),
    (
        "close",
        {
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "已检查后续安排。",
            "confirm_follow_up_checked": True,
        },
    ),
    ("reopen", {"reason": "出现新的已确认事实，需要重新跟进。"}),
]


@pytest.mark.parametrize("endpoint,payload", MUTATIONS)
def test_case_mutation_rejects_out_of_scope_case(client, db_session, endpoint, payload):
    care_case, risk_event_id = make_other_school_case(db_session)
    counselor = auth_headers(client, "counselor", "13800000001")

    if endpoint == "reviews":
        # A risk event that genuinely belongs to this case, so that without the
        # guard the review would succeed rather than tripping a later 403.
        payload = {**payload, "risk_event_id": risk_event_id}

    response = client.post(
        f"/api/v1/care-cases/{care_case.id}/{endpoint}", headers=counselor, json=payload
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    # Nothing was half-applied on the way to the refusal.
    db_session.refresh(care_case)
    assert care_case.status == "PENDING_REVIEW"
    assert care_case.owner_id is None


def test_case_detail_rejects_out_of_scope_student(client, db_session):
    care_case, _ = make_other_school_case(db_session)
    student = db_session.get(Student, care_case.student_id)
    counselor = auth_headers(client, "counselor", "13800000001")

    response = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"


def test_batch_assign_rejects_the_whole_batch_if_any_case_is_out_of_scope(client, db_session):
    """All-or-nothing. Assigning only the reachable subset would report a count
    smaller than the caller asked for, with no way to tell which ids were dropped."""
    counselor = create_risk_case(client)
    owner_id = seed_counselor(db_session).id
    reachable_case = db_session.scalar(select(StudentCareCase))
    other_case, _ = make_other_school_case(db_session)

    response = client.post(
        "/api/v1/care-cases/batch-assign",
        headers=counselor,
        json={"case_ids": [reachable_case.id, other_case.id], "owner_id": owner_id},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    db_session.refresh(reachable_case)
    assert reachable_case.owner_id is None


def test_key_questions_rejects_out_of_scope_student(client, db_session):
    student = make_other_school_student(db_session)
    db_session.commit()
    counselor = auth_headers(client, "counselor", "13800000001")

    response = client.get(
        f"/api/v1/students/{student.id}/key-questions",
        headers=counselor,
        params={"purpose": "复核重点关注学生"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    # No access-trail entry: the read never happened, and recording it would make
    # the trail assert the opposite of the truth.
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "查看重点题")) is None


def test_single_student_export_rejects_out_of_scope_student(client, db_session):
    """The export path takes a raw student id too, and was missed by the sweep
    that fixed the case endpoints — same class, same severity."""
    care_case, _ = make_other_school_case(db_session)
    student = db_session.get(Student, care_case.student_id)
    counselor = auth_headers(client, "counselor", "13800000001")

    response = client.post(
        f"/api/v1/care-cases/{student.id}/export",
        headers=counselor,
        json={"purpose": "阶段工作统计"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "导出单个学生摘要")) is None


# --- the guard must not over-block -------------------------------------------


@pytest.mark.parametrize(
    "scope_type,target_attr",
    [
        (ScopeType.SCHOOL, "school_id"),
        (ScopeType.GRADE, "grade_id"),
        (ScopeType.CLASS, "class_id"),
        (ScopeType.STUDENT, None),
    ],
)
def test_each_scope_level_reaches_a_student_it_covers(client, db_session, scope_type, target_attr):
    """Every documented scope level must actually grant access. A helper that
    only implemented SCHOOL would sail through the denial tests above while
    silently locking out grade- and class-scoped counselors."""
    counselor = create_risk_case(client)
    student = seed_student(db_session)
    user = seed_counselor(db_session)

    if target_attr:
        replace_scopes(db_session, user, {"scope_type": scope_type, target_attr: getattr(student, target_attr)})
    else:
        replace_scopes(db_session, user, {"scope_type": scope_type, "student_id": student.id})

    response = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor)
    assert response.status_code == 200


def test_single_student_export_still_works_for_a_student_in_scope(client, db_session):
    """The counterpart to the guard: narrowing must not break the export its
    competent callers rely on."""
    counselor = create_risk_case(client)
    student = seed_student(db_session)

    response = client.post(
        f"/api/v1/care-cases/{student.id}/export",
        headers=counselor,
        json={"purpose": "阶段工作统计"},
    )

    assert response.status_code == 200


def test_scope_row_for_another_school_does_not_match(client, db_session):
    counselor = create_risk_case(client)
    student = seed_student(db_session)
    user = seed_counselor(db_session)
    other_school = School(code=OTHER_SCHOOL_CODE, name="云海中学")
    db_session.add(other_school)
    db_session.flush()
    replace_scopes(db_session, user, {"scope_type": ScopeType.SCHOOL, "school_id": other_school.id})

    response = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor)
    assert response.status_code == 403


def test_account_with_no_scope_row_is_denied(client, db_session):
    """Fail-safe: a missing scope row withholds access rather than granting it.
    Every account-creation path writes a scope row, so an account without one
    has no granted range at all."""
    counselor = create_risk_case(client)
    student = seed_student(db_session)
    db_session.add(
        UserAccount(
            account="13900000009",
            account_type=AccountType.MOBILE,
            display_name="无范围心理老师",
            password_hash=hash_password("123456"),
            role_code=RoleCode.COUNSELOR,
            must_change_password=False,
        )
    )
    db_session.commit()

    scopeless = auth_headers(client, "counselor", "13900000009")
    response = client.get(f"/api/v1/care-cases/{student.id}", headers=scopeless)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"


# --- import preview agrees with import commit --------------------------------


def test_import_preview_ignores_duplicates_held_by_other_schools(client, db_session):
    """`student_no` is unique per school, not globally. Matching it across every
    school made preview reject a valid row because a *different* school already
    held that number — and the per-row response then disclosed which numbers
    other schools have."""
    other = make_other_school_student(db_session)
    db_session.commit()
    admin = auth_headers(client, "admin", "admin")

    csv = f"student_no,name,grade,class_name\n{other.student_no},新同学,初一,701\n"
    response = client.post(
        "/api/v1/students/import/preview",
        headers=admin,
        files={"file": ("students.csv", csv.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid_count"] == 1
    assert data["rows"][0]["errors"] == []


def test_import_preview_still_flags_a_same_school_duplicate(client, db_session):
    """The other half of the fix: narrowing the check must not stop it catching
    the duplicates it was written for.

    2026-09-17: 同一所学校里的重复从 `errors` 挪到了 `conflicts`（用户要求把它
    变成「覆盖 / 放弃」的选择），但这条用例要钉的那一点没变——**青禾自己的**
    S001 必须被认出来。范围收窄成「只比同一所学校」之后，漏掉自己学校的重复
    会变成静默多建一个学生，正是上面那条注释警告的事。
    """
    admin = auth_headers(client, "admin", "admin")

    csv = "student_no,name,grade,class_name\nS001,林同学,初一,701\n"
    response = client.post(
        "/api/v1/students/import/preview",
        headers=admin,
        files={"file": ("students.csv", csv.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["valid_count"] == 0
    assert data["conflict_count"] == 1
    assert data["rows"][0]["conflicts"][0]["type"] == "STUDENT_NO_EXISTS"


# --- query-level filtering: the set-based counterpart ------------------------
#
# The guard above refuses an id the caller does not own. These cover the other
# half: a list or aggregate that never had an id to refuse because it derived its
# rows from a query. Assertions are written against the *set* returned rather
# than against absence alone — `{"S001"}` catches both the other school leaking
# in and the query collapsing to nothing, where `"Y001" not in ...` would only
# catch the first.


def test_care_case_queue_excludes_other_schools(client, db_session):
    """The queue shows 姓名 and 学号, so before this it was a roster of every
    student the school had a case for."""
    counselor = create_risk_case(client)
    make_other_school_population(db_session)

    items = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"]

    assert {item["student_no"] for item in items} == {"S001"}


def test_workbench_tiles_exclude_other_schools(client, db_session):
    """The tiles sit directly above the queue and must describe the same
    population — a tile reading 12 over a list of three is a contradiction the
    page cannot explain away."""
    counselor = create_risk_case(client)
    make_other_school_population(db_session)

    metrics = client.get("/api/v1/counselor/workbench", headers=counselor).json()["data"]

    # 云海中学 contributes a FOLLOWING case and a PENDING risk event; unscoped
    # these two would read 1 and 2.
    assert metrics["following"] == 0
    assert metrics["pending_risk_events"] == 1


def test_analytics_overview_denominator_follows_scope(client, db_session):
    counselor = create_risk_case(client)
    make_other_school_population(db_session)

    overview = client.get("/api/v1/analytics/overview", headers=counselor).json()["data"]

    # 青禾 holds one target; 云海 adds a second, which must not enter the
    # denominator. 云海 also contributes a FOLLOWING case, a planned retest and a
    # KEY_ATTENTION result — unscoped these read 1, 1 and 1.
    assert overview["total_targets"] == 1
    assert overview["case_status_counts"].get("FOLLOWING", 0) == 0
    assert overview["planned_retests"] == 0
    assert overview["attention_count"] == 0


def test_analytics_rows_hide_out_of_scope_grades_and_classes(client, db_session):
    """Out-of-range rows are dropped, not zeroed — a row of zeroes still tells
    the reader that something exists which they are not allowed to see."""
    counselor = create_risk_case(client)
    make_other_school_population(db_session)
    other_grade_id = other_school_grade_id(db_session)

    grades = client.get("/api/v1/analytics/by-grade", headers=counselor).json()["data"]["items"]
    classes = client.get("/api/v1/analytics/by-class", headers=counselor).json()["data"]["items"]

    assert grades, "青禾自己的年级行必须还在"
    assert other_grade_id not in {row["grade_id"] for row in grades}
    assert classes, "青禾自己的班级行必须还在"
    assert len(classes) == 1


def test_dimension_distribution_excludes_other_schools(client, db_session):
    counselor = create_risk_case(client)
    make_other_school_population(db_session)

    items = client.get("/api/v1/analytics/dimensions", headers=counselor).json()["data"]["items"]

    learning = next(row for row in items if row["dimension_code"] == "LEARNING_ANXIETY")
    # 云海中学 has a HIGH result in this dimension, which would make it 2 and drag
    # the average towards it.
    assert learning["assessed_count"] == 1


def test_reminders_exclude_other_schools(client, db_session):
    """Each reminder's title is 「跟进 <姓名>」, so this panel is a roster of
    students in difficulty rather than an aggregate."""
    counselor = create_risk_case(client)
    make_other_school_population(db_session)

    data = client.get("/api/v1/counselor/reminders", headers=counselor).json()["data"]

    # 云海中学 has a follow-up and a retest both inside the horizon; unscoped this
    # would be two entries naming another school's student.
    assert data["items"] == []
    # `total` 也要按范围算。它当初漏了 `join(Student)`，那两张表就笛卡尔积起来、
    # 范围谓词退化成常量——`items` 仍然干净，`total` 却数了全校的待办。
    assert data["total"] == 0


def test_student_roster_excludes_other_schools(client, db_session):
    """`student_no` identifies a student even with the name masked."""
    make_other_school_population(db_session)
    counselor = auth_headers(client, "counselor", "13800000001")

    items = client.get("/api/v1/students", headers=counselor).json()["data"]["items"]

    assert {item["student_no"] for item in items} == {"S001"}


def test_task_list_completion_follows_scope(client, db_session):
    make_other_school_population(db_session)
    counselor = auth_headers(client, "counselor", "13800000001")

    tasks = client.get("/api/v1/assessment-tasks", headers=counselor).json()["data"]["items"]

    # Unscoped the task would carry 云海中学's target in its denominator too.
    assert [task["total_targets"] for task in tasks] == [1]


def test_task_targets_are_issued_within_the_creators_scope(client, db_session):
    """§9 的**写侧**：建任务时发出去的目标行也按创建者的范围收敛。

    2026-09-17 写权从 ADMIN 转到 COUNSELOR 之后才出现这个分岔。在此之前唯一能建
    任务的角色是管理员，而 seed 给它的范围是全校——「发给全体在读学生」与「发给
    自己管辖的全体」恰好是同一句话，所以 `create_school_assessment_task` 里少了
    这条谓词很久，没有任何东西能发现它。写权一落到心理老师身上就分岔了：一个只带
    CLASS 范围的心理老师，凭这个端点能给**全校**每个学生写下一行「你被安排了这次
    测评」，而目标行本身就是管理事实，他随后还能在自己的完成明细里读到它的进度。

    断言的是**目标行集合**而不是接口返回：接口返回的只有任务号与名称，看不见发了
    给谁——那正是这个洞藏身的地方。
    """
    seeded = seed_student(db_session)  # S001，青禾 1班
    other_class = make_same_school_student_in_another_class(db_session)
    user = seed_counselor(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.CLASS, "class_id": seeded.class_id})
    counselor = auth_headers(client, "counselor", "13800000001")

    created = client.post(
        "/api/v1/assessment-tasks",
        headers=counselor,
        json={"name": "班级普查", "start_at": "2026-09-01", "end_at": "2026-12-31"},
    )
    assert created.status_code == 200
    task_id = created.json()["data"]["id"]

    issued = set(
        db_session.scalars(
            select(AssessmentTarget.student_id).where(AssessmentTarget.task_id == task_id)
        ).all()
    )
    assert issued == {seeded.id}
    assert other_class.id not in issued


def test_task_completion_excludes_other_schools(client, db_session):
    make_other_school_population(db_session)
    counselor = auth_headers(client, "counselor", "13800000001")
    task = db_session.scalar(select(AssessmentTask))

    items = client.get(
        f"/api/v1/assessment-tasks/{task.id}/completion", headers=counselor
    ).json()["data"]["items"]

    assert {row["student_no"] for row in items} == {"S001"}


def test_bulk_export_excludes_other_schools(client, db_session):
    """The widest read in the system: no ids, no per-student guard, one row per
    case in the school with its 关注等级. The per-id guard on the single-student
    export would be worth little if the bulk path still shipped everything."""
    counselor = create_risk_case(client)
    make_other_school_population(db_session)

    response = client.post(
        "/api/v1/care-cases/export", headers=counselor, json={"purpose": "阶段工作统计"}
    )

    assert response.status_code == 200
    assert "S001" in response.text
    assert "Y001" not in response.text


def test_audit_logs_hide_rows_naming_out_of_scope_students(client, db_session):
    """Scope reaches the trail itself. 「查看重点题 / 学生 N」 records exactly what
    the key-question guard withholds, so leaving those rows unfiltered would hand
    the same fact back through a different door.

    Rows that name no student are organisation-level events — 登录, 导出, 导入,
    改配置 — and stay visible: they are not student data, and dropping them would
    blank the page for a counselor whose range happens to be small.

    The three rows use three distinct actions on purpose. `resource_id` is
    overloaded — a student row stores the student id, a login row stores the user
    id — and the seeded counselor's user id happens to equal 云海's student id, so
    asserting on it would fail for a reason that has nothing to do with scope.
    """
    make_other_school_population(db_session)
    in_scope = seed_student(db_session)
    db_session.add(
        AuditLog(
            action="查看关注档案",
            resource_type="STUDENT",
            resource_id=str(in_scope.id),
            result="SUCCESS",
            student_id=in_scope.id,
        )
    )
    db_session.add(AuditLog(action="导出关注档案摘要", resource_type="EXPORT", result="SUCCESS"))
    db_session.commit()
    counselor = auth_headers(client, "counselor", "13800000001")

    items = client.get("/api/v1/audit-logs", headers=counselor).json()["data"]["items"]
    actions = {row["action"] for row in items}

    assert "查看关注档案" in actions, "范围内学生的访问记录必须还在"
    assert "导出关注档案摘要" in actions, "未命名学生的组织级事件不该被过滤掉"
    assert "查看重点题" not in actions, "范围外学生的访问记录必须消失"


def test_scope_filter_does_not_empty_an_in_scope_view(client):
    """The other direction: narrowing must not be so eager that a counselor with
    a real range sees nothing. The seeded counselor is scoped to 青禾 and the
    seeded student is 青禾's, so every one of these must still answer."""
    counselor = create_risk_case(client)

    for path in [
        "/api/v1/care-cases",
        "/api/v1/students",
        "/api/v1/counselor/workbench",
        "/api/v1/analytics/overview",
        "/api/v1/analytics/by-grade",
        "/api/v1/analytics/by-class",
        "/api/v1/analytics/dimensions",
        "/api/v1/assessment-tasks",
    ]:
        assert client.get(path, headers=counselor).status_code == 200, path

    cases = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"]
    assert len(cases) == 1

