"""Demo dataset for exercising the UI.

Separate from `seed.py`, which creates the minimal baseline (one school, one
student, one task). This one fills the screens: several grades and classes, a
cohort of students, submitted assessments spanning every score band, and care
cases at every stage of the follow-up lifecycle.

Everything is produced through the real service layer — `save_answer` /
`submit_session` / the care services — so scoring, risk events and case-opening
follow exactly the same rules as a real submission. Nothing is written directly
into the result tables.

Idempotent: re-running tops up what is missing rather than duplicating.

One exception, worth knowing before wondering why a number looks wrong: 答卷用时 is
written once, at submit, from the answer rows' own timestamps. A re-run skips
students who already submitted (see the `already` check), so it will not recompute
their duration — and it must not, because rewriting `assessment_answer.answered_at`
would falsify the one table that records what actually happened. Baseline data
(性别/年龄) *is* topped up in place, since a roster attribute is not an answer
fact. To see demo 用时 values, start from an empty assessment dataset:
`make reset-db`.

A second flavour of the same thing, which is the one that actually bites: S001's
session is the one the 学生答题 E2E group resets and re-answers, so it commonly sits
at IN_PROGRESS holding a couple of answers stamped at whenever E2E last ran. The
next `seed-demo` then fills in the remaining questions and submits — the two that
already existed keep their old stamp (`answered_at` is write-once), so the honest
first-answer-to-submit duration for that session is the gap between the two runs,
hours rather than minutes. That is the definition reporting truthfully about a
session that really was answered in two sittings hours apart, not a seeder defect;
it resolves itself on a `make reset-db` re-basing. Do not "fix" it by rewriting
those two `answered_at` values — falsifying them is what the column exists to
prevent.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import UserAccount, UserScope
from app.models.assessment import AssessmentSession, AssessmentTarget, AssessmentTask, RiskEvent
from app.models.care import StudentCareCase
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale, ScaleRule
from app.scale_engine.engine import DEFAULT_RULE_CONFIG, rule_config_to_json
from app.schemas.care import (
    CloseCaseRequest,
    FamilyContactRequest,
    FollowUpRequest,
    ManualReviewRequest,
    RetestPlanRequest,
)
from app.security.passwords import hash_password
from app.services import assessment_import_service, assessment_service, care_service
from app.services.scale_rule_service import rule_version_for

DEFAULT_PASSWORD = "123456"
SEED = 20260916

# 上学期那次普查。它是一次**已经发生过**的测评（走导入路径落库，`source=IMPORTED`），
# 存在的理由是让「历次趋势」有第二个点可画；见 `_last_semester_rows`。
LAST_SEMESTER_SURVEY = "2026春季MHT心理健康筛查（外部平台导入）"
LAST_SEMESTER_DAYS_AGO = 120
# 上学期被普查到的是**现在的初二与初三**：现在的初一那时候还在小学，学校那份普查
# 根本没有他们。见 `_last_semester_rows` 里那段关于 S001 的说明。
SURVEYED_LAST_SEMESTER = ("初二", "初三")

# 8 个维度各对应一段题号；见 scale_engine.dimension_for_question
CONTENT_QUESTIONS = [no for no in range(1, 101) if no not in DEFAULT_RULE_CONFIG.validity_questions]

# 姓名池：足够生成一个年级规模的虚构学生
SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = ["子涵", "雨桐", "浩然", "欣怡", "梓萱", "俊杰", "思远", "佳怡", "宇轩", "诗涵",
         "若曦", "天佑", "梦琪", "嘉懿", "依诺", "晨曦", "博文", "雅静", "书瑶", "睿泽"]

# (年级, 班级列表)
GRADES = [("初一", ["1班", "2班"]), ("初二", ["1班", "2班", "3班"]), ("初三", ["1班", "2班"])]

# 4 students per class keeps the cohort realistic without making the seeder slow
# (each student runs a full submission through the engine).
STUDENTS_PER_CLASS = 4


class DemoStudent(NamedTuple):
    grade_order: int
    grade_name: str
    class_name: str
    student_no: str


def demo_roster() -> list[DemoStudent]:
    """演示名册：由 `GRADES` 推导出的 (年级, 班级, 学号)。

    这是"哪些学生是演示数据"的唯一定义，`_create_cohort` 与
    `purge.purge_demo_data` 都从这里取。库里**没有任何一列**标记演示数据，
    清理程序只能靠这份名册认人；两处各写一份的话，动了 `GRADES` 就会漏删
    （把演示学生留成"真实学生"）或误删。

    学号是按序号生成的，`S001` 是 `seed.py` 的基线学生，不属演示名册——
    但它仍占掉序号 1，所以名册从 `S002` 起。
    """
    roster: list[DemoStudent] = []
    index = 0
    for grade_order, (grade_name, class_names) in enumerate(GRADES, start=1):
        for class_name in class_names:
            for _slot in range(STUDENTS_PER_CLASS):
                index += 1
                student_no = f"S{index:03d}"
                if student_no == "S001":
                    continue  # 基线种子已创建
                roster.append(DemoStudent(grade_order, grade_name, class_name, student_no))
    return roster


def _get_or_create(db: Session, model, defaults: dict | None = None, **filters):
    instance = db.scalar(select(model).filter_by(**filters))
    if instance:
        return instance, False
    instance = model(**filters, **(defaults or {}))
    db.add(instance)
    db.flush()
    return instance, True


def _student_yes_set(profile: str, rng: random.Random) -> set[int]:
    """Answer pattern producing a target score band.

    Key questions 85/97 are what open a care case, so only the `key` profiles
    include them — which is why only those students get a follow-up file.
    """
    if profile == "general":
        return set(rng.sample(CONTENT_QUESTIONS, rng.randint(8, 45)))
    if profile == "attention":
        return set(CONTENT_QUESTIONS[: rng.randint(56, 64)])
    if profile == "key_one":
        return set(CONTENT_QUESTIONS[: rng.randint(62, 70)]) | {85}
    # key_both
    return set(CONTENT_QUESTIONS[: rng.randint(64, 74)]) | {85, 97}


def _last_semester_rows(db: Session, students: list[Student], main_task: AssessmentTask) -> list[dict]:
    """上学期那次普查的导入行。

    名单是**现在的初二与初三**：上学期他们分别在初一与初二，学校做过一次普查。
    现在的初一不在里面——上学期他们还在小学。这条规则同时挡掉一件会咬人的事：
    `S001` 正是初一 1 班的学生，而「学生答题」那组 E2E 会反复重置并重答它的会话；
    给他多挂一条往期记录，`/student/tasks` 里就会多出一个带 `session_id` 的任务行，
    那组用例挑中的可能是一条**已提交的导入会话**。

    答案按学号播种，与主循环同一个约定：同一个学生无论第几次、以什么顺序处理，
    取到的答案都一样，重跑不会多出一批记录。
    """
    surveyed_grades = set(
        db.scalars(select(Grade.id).where(Grade.name.in_(SURVEYED_LAST_SEMESTER))).all()
    )
    # 只补「这学期也答了」的学生：上学期做过、这学期没做的那些，趋势页签本来也看不到
    # 第二个点，而多出来的结果行却会计入关注率与维度分布。
    answered = set(
        db.scalars(
            select(AssessmentSession.student_id).where(AssessmentSession.task_id == main_task.id)
        ).all()
    )
    # 到这一步关怀档案已经开好了（主循环的 `submit_session` 走的就是那条链路），所以
    # 「现在有档案」可以用来挑上学期那份的分数段：这些学生上学期落在「需关注」，
    # 本学期才升到重点关注——折线因此是往上走的，而不是两条随机的线。
    case_student_ids = set(db.scalars(select(StudentCareCase.student_id)).all())

    rows: list[dict] = []
    for student in students:
        if student.grade_id not in surveyed_grades or student.id not in answered:
            continue
        rng = random.Random(f"last-semester:{student.student_no}:{SEED}")
        profile = "attention" if student.id in case_student_ids else "general"
        # **刻意避开重点题 85/97。** 导入路径按规则会开风险事件与关怀档案
        # （2026-09-17 起与系统内作答完全一致），而这一批是补历史：真让它开出档案，
        # 演示库里就会凭空多出一批待复核、未分配的档案，工作台的队列形状随之改变，
        # 后面按 `position % 5` 排出来的档案阶段也整体错位。
        yes_set = _student_yes_set(profile, rng) - {85, 97}
        rows.append(
            {
                "student_id": student.id,
                "answers": ["YES" if number in yes_set else "NO" for number in range(1, 101)],
                # 一份文件里记录的是「哪一天」和「总共用了多久」，两者都要像样
                "duration_seconds": rng.randint(7 * 60, 25 * 60),
            }
        )
    return rows


def _create_cohort(db: Session, school: School, rng: random.Random) -> list[Student]:
    students: list[Student] = []
    used_names: set[str] = set()
    grades: dict[str, Grade] = {}
    classes: dict[tuple[str, str], ClassGroup] = {}

    for entry in demo_roster():
        if entry.grade_name not in grades:
            grade, _ = _get_or_create(
                db, Grade, {"sort_order": entry.grade_order},
                school_id=school.id, name=entry.grade_name,
            )
            grades[entry.grade_name] = grade
        grade = grades[entry.grade_name]

        class_key = (entry.grade_name, entry.class_name)
        if class_key not in classes:
            class_group, _ = _get_or_create(
                db, ClassGroup, None,
                school_id=school.id, grade_id=grade.id, name=entry.class_name,
            )
            classes[class_key] = class_group
        class_group = classes[class_key]

        student_no = entry.student_no
        # 性别与年龄按学号播种，和这里其他一次性生成一样与运行次数无关。
        # 年龄跟着年级走，再上下浮动一岁，免得一个年级所有人的年龄是同一个数。
        # 也是相对今年算的（`date.today()`），所以演示数据的年龄不会随年月变得离谱。
        profile_rng = random.Random(f"{student_no}:{SEED}")
        gender = profile_rng.choice(["MALE", "FEMALE"])
        age = date.today().year - (2014 - entry.grade_order) + profile_rng.choice([0, 0, 0, -1, 1])
        existing = db.scalar(select(Student).where(Student.student_no == student_no))
        if existing:
            # 补齐在 0009 迁移之前建好的行。名册属性不是原始答卷事实，
            # 就地补写不违反「四层事实模型」。
            if existing.gender is None:
                existing.gender = gender
            if existing.age is None:
                existing.age = age
            students.append(existing)
            continue

        while True:
            name = rng.choice(SURNAMES) + rng.choice(GIVEN)
            if name not in used_names:
                used_names.add(name)
                break

        student = Student(
            student_no=student_no,
            name=name,
            masked_name=name,
            school_id=school.id,
            grade_id=grade.id,
            class_id=class_group.id,
            gender=gender,
            age=age,
        )
        db.add(student)
        db.flush()
        students.append(student)
    return students


def _ensure_student_account(db: Session, school: School, student: Student) -> UserAccount:
    account, _ = _get_or_create(
        db,
        UserAccount,
        {
            "account_type": AccountType.STUDENT_NO,
            "display_name": student.name,
            "password_hash": hash_password(DEFAULT_PASSWORD),
            "role_code": RoleCode.STUDENT,
            "must_change_password": False,
        },
        account=student.student_no,
    )
    _get_or_create(
        db, UserScope, None, user_id=account.id, scope_type=ScopeType.STUDENT,
        school_id=school.id, student_id=student.id,
    )
    return account


def _ensure_task(db: Session, school: School, scale: AssessmentScale, admin_id: int,
                 task_no: str, name: str, start: datetime, end: datetime, status: str):
    task, created = _get_or_create(
        db, AssessmentTask,
        {
            "name": name, "scale_id": scale.id, "school_id": school.id, "scope_type": "SCHOOL",
            "start_at": start, "end_at": end, "status": status, "created_by": admin_id,
        },
        task_no=task_no,
    )
    return task, created


def seed_demo_data(db: Session) -> dict:
    """Create the demo dataset. Returns a summary of what was written."""
    rng = random.Random(20260916)  # 仅用于姓名等一次性生成
    school = db.scalar(select(School).where(School.code == "QH"))
    scale = db.scalar(
        select(AssessmentScale)
        .where(AssessmentScale.code == "MHT", AssessmentScale.status == "PUBLISHED")
        .order_by(AssessmentScale.id.desc())
    )
    admin = db.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    counselor = db.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    if not (school and scale and admin and counselor):
        raise RuntimeError("请先运行 python -m app.db.seed 初始化基础数据")

    if not db.scalar(select(ScaleRule).where(ScaleRule.scale_id == scale.id)):
        # 兜底：种子/导入都会给量表建规则，这里只是防止一个没有规则的表量把演示数据卡住。
        # 标识必须由 `rule_version_for` 推出——量表是动态查的（最新已发布版本），
        # 写死 `MHT-RULE-1.0.0` 会给出一个与该量表版本无关的名字（§6）。
        db.add(ScaleRule(
            scale_id=scale.id,
            rule_version=rule_version_for(scale.code, scale.version),
            rule_type="MHT_SCORING",
            status="ACTIVE",
            config_json=rule_config_to_json(DEFAULT_RULE_CONFIG),
        ))
        db.flush()

    # 基线学生 S001 也纳入演示数据
    base_student = db.scalar(select(Student).where(Student.student_no == "S001"))
    if base_student and (base_student.gender is None or base_student.age is None):
        # `seed.py` 现在会带上这两项，但先于 0009 迁移建好的库不会——补上，
        # 免得演示数据里混着一个没有性别年龄的学生。取值与 seed.py 一致。
        base_student.gender = base_student.gender or "MALE"
        base_student.age = base_student.age or 13
    students = ([base_student] if base_student else []) + _create_cohort(db, school, rng)

    today = datetime.now(UTC).replace(tzinfo=None)
    main_task, _ = _ensure_task(
        db, school, scale, admin.id, "TASK-2026-FALL-MHT", "2026秋季MHT心理健康筛查",
        today - timedelta(days=20), today + timedelta(days=10), "ACTIVE",
    )
    retest_task, _ = _ensure_task(
        db, school, scale, admin.id, "TASK-2026-GRADE9-RETEST", "初三年级复测任务",
        today + timedelta(days=20), today + timedelta(days=35), "ACTIVE",
    )

    # 只有主任务面向全体；复测任务只发给初三
    grade9 = db.scalar(select(Grade).where(Grade.name == "初三"))
    # 每个学生的答案模式由「学号」单独播种，而不是从一条全局随机流里取。
    # 全局流会让结果依赖处理顺序：已提交的学生提前 continue 就不再消耗随机数，
    # 后面学生的取舍会跟着变，于是重跑一次就会多出一批提交。按学号播种后，
    # 同一个学生无论第几次、以什么顺序处理，得到的结果都一样。
    profiles = ["general"] * 6 + ["attention"] * 3 + ["key_one"] * 2 + ["key_both"]

    summary = {"students": 0, "submitted": 0, "earlier_sittings": 0, "cases": 0, "reviews": 0,
               "follow_ups": 0, "family_contacts": 0, "retests": 0, "closed": 0}

    for student in students:
        account = _ensure_student_account(db, school, student)
        summary["students"] += 1

        for task in (main_task, retest_task):
            if task is retest_task and student.grade_id != grade9.id:
                continue
            _get_or_create(db, AssessmentTarget, {"status": "NOT_STARTED"},
                           task_id=task.id, student_id=student.id)

        # 主任务：大部分学生已完成
        already = db.scalar(
            select(AssessmentSession).where(
                AssessmentSession.task_id == main_task.id,
                AssessmentSession.student_id == student.id,
                AssessmentSession.status.in_(["CALCULATED", "QUESTIONABLE"]),
            )
        )
        if already:
            continue
        # 每个学生的取舍与答案都由学号决定，与运行次数无关
        student_rng = random.Random(f"{student.student_no}:{SEED}")
        if student_rng.random() < 0.15:
            continue  # 留一部分未完成，让完成率不是 100%

        profile = profiles[student_rng.randrange(len(profiles))]
        yes_set = _student_yes_set(profile, student_rng)

        session = assessment_service.create_or_get_session(db, account, main_task.id)
        # 把 100 题摊在一个像样的作答窗口里（7~25 分钟），否则整个演示库的
        # 「用时」都是 0 秒，看着像功能坏了。`answered_at` 是写一次的列，
        # 只能在这里显式给——种子直接 UPDATE 原始答卷表会违背本模块
        # 「一切经 service 层」的约定。
        sitting_seconds = student_rng.randint(7 * 60, 25 * 60)
        sitting_start = today - timedelta(seconds=sitting_seconds)
        for offset, question_no in enumerate(range(1, 101)):
            answer = "YES" if question_no in yes_set else "NO"
            assessment_service.save_answer(
                db,
                account,
                session.id,
                question_no,
                answer,
                answered_at=sitting_start + timedelta(seconds=sitting_seconds * offset // 100),
            )
        assessment_service.submit_session(db, account, session.id, f"demo-{session.id}")
        summary["submitted"] += 1
        db.flush()

    # ---- 上学期那次普查（让「历次趋势」有第二个点） ----
    #
    # MHT 是**每学期一次**的普查，所以一个学生名下天然就有多次记录，而「历次趋势」
    # 页签要看的正是这条线。只种本学期一场的话，那个页签上永远只有一个点、折线画不
    # 出来，E2E 也就只能退化成一个恒绿的断言（CLAUDE.md 测试注意里那条教训）。
    #
    # 走**导入**路径而不是再跑一遍 `create_or_get_session`/`submit_session`，有三个理由：
    #   1. 它天然把 `submitted_at` 写成测评日期。`submit_session` 写的是「现在」，
    #      要在种子层再 UPDATE 回去，就得往原始事实表里写第二次。
    #   2. 这正是导入功能存在的场景：上学期学校用的是别的平台，这学期换到本系统，
    #      把那份结果导进来一起看。演示数据里因此第一次出现 `source=IMPORTED` 记录。
    #   3. 它与系统内作答**走同一套判定**（`maybe_raise_risk_events`），所以演示库也
    #      顺带跑一遍那条 2026-09-17 才对齐的链路。
    earlier_rows = _last_semester_rows(db, students, main_task)
    if earlier_rows and not db.scalar(
        select(AssessmentTask).where(AssessmentTask.name == LAST_SEMESTER_SURVEY)
    ):
        assessment_import_service.commit_assessment_import(
            db,
            {
                "rows": earlier_rows,
                "batch": {
                    "name": LAST_SEMESTER_SURVEY,
                    "tested_on": (today - timedelta(days=LAST_SEMESTER_DAYS_AGO)).date().isoformat(),
                },
            },
            counselor,
        )
        summary["earlier_sittings"] = len(earlier_rows)

    # ---- 关怀档案闭环 ----
    cases = db.scalars(
        select(StudentCareCase).order_by(StudentCareCase.id)
    ).all()
    for position, case in enumerate(cases):
        summary["cases"] += 1
        stage = position % 5

        # 大部分档案分配负责人，留少数「未分配」——它本身是需要被看到的信号
        if position % 4 != 0 and case.owner_id is None:
            care_service.batch_assign_owner(db, counselor, [case.id], counselor.id)

        pending = db.scalars(
            select(RiskEvent).where(RiskEvent.student_id == case.student_id, RiskEvent.status == "PENDING")
        ).all()

        if stage == 0:
            continue  # 停在待复核
        if not pending:
            continue

        care_service.create_manual_review(db, counselor, case.id, ManualReviewRequest(
            risk_event_id=pending[0].id,
            review_result="建立持续关注档案",
            confirmed_facts="已完成首次访谈，学生愿意继续沟通，记录了近期作息与情绪变化。",
            next_action="安排下次跟进",
            next_follow_up_date=date.today() + timedelta(days=7),
        ))
        summary["reviews"] += 1

        if stage == 1:
            continue  # 已复核，尚未跟进

        care_service.create_follow_up(db, counselor, case.id, FollowUpRequest(
            record_type="心理老师访谈",
            confirmed_facts="完成一次支持性沟通，约定下次见面的时间与话题。",
            next_follow_up_date=date.today() + timedelta(days=7),
        ))
        summary["follow_ups"] += 1

        if stage == 2:
            continue

        care_service.create_family_contact(db, counselor, case.id, FamilyContactRequest(
            contact_date=date.today() - timedelta(days=3),
            contact_person="母亲",
            channel="电话",
            result="已联系",
            support_status="愿意配合",
            confirmed_facts="监护人了解近期情况，约定共同关注作息与沟通方式的变化。",
            next_contact_date=date.today() + timedelta(days=14),
        ))
        summary["family_contacts"] += 1

        if stage == 3:
            continue

        care_service.create_retest_plan(db, counselor, case.id, RetestPlanRequest(
            planned_date=date.today() + timedelta(days=30),
            reason="重点维度趋势复测",
        ))
        summary["retests"] += 1

        if stage == 4 and position % 2 == 0:
            care_service.close_case(db, counselor, case.id, CloseCaseRequest(
                close_reason="完成阶段跟进并进入一般观察",
                close_note="阶段闭环，转入一般观察，保留历史记录。",
                confirm_follow_up_checked=True,
            ))
            summary["closed"] += 1

    db.commit()
    return summary


if __name__ == "__main__":
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        result = seed_demo_data(session)
        print("演示数据已写入：")
        for key, value in result.items():
            print(f"  {key}: {value}")
