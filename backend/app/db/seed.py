import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import AssessmentTarget, AssessmentTask
from app.models.account import UserAccount, UserScope
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import DEFAULT_RULE_CONFIG, default_mht_questions, rule_config_to_json
from app.security.passwords import hash_password
from app.services.scale_rule_service import rule_version_for

# The version the seed ships. Kept in one place because three things have to
# agree on it: the guard that decides whether to create the scale at all, the
# scale row itself, and the task's lookup — a task whose scale_id cannot be
# resolved is silently unusable, so a mismatch here is not a cosmetic drift.
# `load_scale_questions()` reads the real question bank from data/mht_scale.json;
# this string is what ties that bank to a version number.
MHT_SCALE_VERSION = "MHT-1.1.0"


def seed_development_data(db: Session) -> None:
    if not db.scalar(select(School).where(School.code == "QH")):
        seed_identity_data(db)
    seed_mht_scale(db)
    seed_assessment_task(db)


def seed_identity_data(db: Session) -> None:
    if db.scalar(select(School).where(School.code == "QH")):
        return

    school = School(code="QH", name="青禾实验学校")
    db.add(school)
    db.flush()

    grade = Grade(school_id=school.id, name="初一", sort_order=1)
    db.add(grade)
    db.flush()

    class_group = ClassGroup(school_id=school.id, grade_id=grade.id, name="1班")
    db.add(class_group)
    db.flush()

    # 性别/年龄 are set on S001 so the non-NULL path is exercised by every test
    # that starts from the seed, not just by the ones written for this feature.
    # The age is a literal like any other roster value: it is stored (migration 0011)
    # and therefore goes stale the same way the school's own roster does — nothing
    # may read it as "his age as of today", and nothing may assert a year.
    student = Student(
        student_no="S001",
        name="林同学",
        masked_name="林同学",
        school_id=school.id,
        grade_id=grade.id,
        class_id=class_group.id,
        gender="MALE",
        age=13,
    )
    db.add(student)
    db.flush()

    password_hash = hash_password("123456")
    # `must_change_password` defaults to True at the model level — a safe default
    # for any account someone else created. These four are the documented initial
    # credentials, so they opt out; accounts produced by an admin password reset
    # (auth_service.reset_password) still get True and hit the forced-rotation gate.
    common = {"password_hash": password_hash, "must_change_password": False}
    users = [
        UserAccount(
            account="S001",
            account_type=AccountType.STUDENT_NO,
            display_name="林同学",
            role_code=RoleCode.STUDENT,
            **common,
        ),
        UserAccount(
            account="13800000001",
            account_type=AccountType.MOBILE,
            display_name="心理老师",
            role_code=RoleCode.COUNSELOR,
            **common,
        ),
        UserAccount(
            account="13800000002",
            account_type=AccountType.MOBILE,
            display_name="德育领导",
            role_code=RoleCode.LEADER,
            **common,
        ),
        UserAccount(
            account="admin",
            account_type=AccountType.ADMIN_USERNAME,
            display_name="系统管理员",
            role_code=RoleCode.ADMIN,
            **common,
        ),
    ]
    db.add_all(users)
    db.flush()

    db.add_all(
        [
            UserScope(user_id=users[0].id, scope_type=ScopeType.STUDENT, school_id=school.id, student_id=student.id),
            UserScope(user_id=users[1].id, scope_type=ScopeType.SCHOOL, school_id=school.id),
            UserScope(user_id=users[2].id, scope_type=ScopeType.SCHOOL, school_id=school.id),
            UserScope(user_id=users[3].id, scope_type=ScopeType.SCHOOL, school_id=school.id),
        ]
    )


def mht_rule_config() -> dict:
    """The scoring-rule payload a newly created MHT rule carries.

    Shared with `db/purge.py`, which rebuilds a scale's rule rows after a
    development clean-up: the seeded row and the rebuilt row have to be the same
    row, otherwise a purged database and a fresh one disagree about thresholds.
    """
    return {
        **rule_config_to_json(DEFAULT_RULE_CONFIG),
        "todo_business_confirmation": [
            "VALID 与 QUESTIONABLE 的效度阈值尚未冻结；当前仅实现 validity_score >= 7 为 RETEST_RECOMMENDED。",
        ],
    }


def load_scale_questions() -> list[dict]:
    """Question stems for the seeded scale.

    Prefers `data/mht_scale.json` — the same file the admin import screen accepts,
    so the seeded scale and an imported one are structurally identical. Falls back
    to the engine's generated placeholders if the file is absent, which keeps
    `seed` working in environments that ship only the backend.
    """
    data_file = Path(__file__).resolve().parents[3] / "data" / "mht_scale.json"
    if data_file.exists():
        with data_file.open(encoding="utf-8") as handle:
            rows = json.load(handle)
        return [
            {
                "question_no": row["question_no"],
                "question_text": row["question_text"],
                "dimension_code": row["dimension_code"],
                "is_validity_question": row["is_validity_question"],
                "is_key_question": row["is_key_question"],
            }
            for row in rows
        ]
    return [
        {
            "question_no": q.question_no,
            "question_text": f"MHT题目 {q.question_no:03d}（开发占位，正式题干通过题库导入）",
            "dimension_code": q.dimension_code,
            "is_validity_question": q.is_validity_question,
            "is_key_question": q.is_key_question,
        }
        for q in default_mht_questions()
    ]


def seed_mht_scale(db: Session) -> None:
    if db.scalar(
        select(AssessmentScale).where(
            AssessmentScale.code == "MHT", AssessmentScale.version == MHT_SCALE_VERSION
        )
    ):
        return

    scale = AssessmentScale(
        code="MHT",
        name="中学生心理健康测验",
        version=MHT_SCALE_VERSION,
        status="PUBLISHED",
        published_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(scale)
    db.flush()

    db.add_all(
        [
            ScaleQuestion(
                scale_id=scale.id,
                question_no=question["question_no"],
                question_text=question["question_text"],
                dimension_code=question["dimension_code"],
                is_validity_question=question["is_validity_question"],
                is_key_question=question["is_key_question"],
                status="ACTIVE",
            )
            for question in load_scale_questions()
        ]
    )
    db.add(
        ScaleRule(
            scale_id=scale.id,
            rule_version=rule_version_for(scale.code, scale.version),
            rule_type="MHT_SCORING",
            status="ACTIVE",
            # The engine reads this at scoring time, so it must be complete.
            config_json=mht_rule_config(),
        )
    )


def seed_assessment_task(db: Session) -> None:
    scale = db.scalar(
        select(AssessmentScale).where(
            AssessmentScale.code == "MHT", AssessmentScale.version == MHT_SCALE_VERSION
        )
    )
    school = db.scalar(select(School).where(School.code == "QH"))
    student = db.scalar(select(Student).where(Student.student_no == "S001"))
    if not scale or not school or not student:
        return
    if db.scalar(select(AssessmentTask).where(AssessmentTask.task_no == "TASK-2026-FALL-MHT")):
        return

    task = AssessmentTask(
        task_no="TASK-2026-FALL-MHT",
        name="2026秋季MHT心理健康筛查",
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        # 窗口按**今天**算，不写死日期（2026-09-17 改）。写死的窗口会自己过期，而这份
        # 种子数据是全后端测试与 e2e 的起点：`task_service.effective_task_status` 现在
        # 会读 `end_at`（过期即「已结束」），`create_or_get_session` 用的是同一个判据——
        # 于是 `datetime(2026, 9, 30)` 一到，`test_assessment_api.py` 里每一条
        # 「学生开一份卷子」的用例都会拿到 404，红的原因却不是功能坏了。
        # `seed_demo._ensure_task` 早就是相对日期（today ± N 天），这里与它对齐。
        # 注意 `_get_or_create` 语义的差别：`seed_demo` 只在任务不存在时创建，
        # 所以已经建过库的开发者看到的是当初那一天算出来的窗口，这没关系。
        start_at=datetime.now() - timedelta(days=20),
        end_at=datetime.now() + timedelta(days=10),
        status="ACTIVE",
    )
    db.add(task)
    db.flush()
    db.add(AssessmentTarget(task_id=task.id, student_id=student.id, status="NOT_STARTED"))


if __name__ == "__main__":
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        seed_development_data(session)
        session.commit()
