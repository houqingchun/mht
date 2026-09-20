from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.assessment import AssessmentAnswer, AssessmentSession
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import ScaleQuestion
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.security.permissions import (
    GRANTED_WITH_AUDIT,
    KEY_QUESTIONS,
    MANAGE,
    ORG_ACCOUNT,
    READ_BASIC,
    SCOPED,
    STUDENT_PSYCH_DETAIL,
    require_capability,
)
from app.services.analytics_service import student_result_list
from app.services.assessment_service import latest_session
from app.services.audit_service import write_audit

router = APIRouter(tags=["students"])

# 组织与账号：管理员可管理，心理老师可查看必要信息，德育领导仅看汇总，学生仅本人。
OrgAccountReader = Annotated[
    UserAccount, Depends(require_capability(ORG_ACCOUNT, allow={MANAGE, READ_BASIC}))
]

# 重点题/原始答卷：最高敏感级别，仅心理老师经二次授权后可见，且每次读取写审计。
KeyQuestionReader = Annotated[
    UserAccount,
    Depends(require_capability(KEY_QUESTIONS, allow={GRANTED_WITH_AUDIT})),
]
# 学生心理详情：只有 SCOPED 能读**个体的**等级与档案正文，`SUMMARY`（德育领导）是
# 聚合视图，不能借这一层拿到逐行明细（§4：描述性等级不可互换）。与 `care.py` 的那个同形。
PsychDetailScopedReader = Annotated[
    UserAccount,
    Depends(require_capability(STUDENT_PSYCH_DETAIL, allow={SCOPED})),
]


@router.get("/students")
def list_students(
    current_user: OrgAccountReader,
    db: Annotated[Session, Depends(get_db)],
):
    """The roster, narrowed to the caller's data scope.

    `student_no` is identifying even with the name masked, so this is a scoped
    read rather than a public list. The capability check above is unchanged — it
    answers "may this role read the roster at all", which is a different question
    from "which students are on it".
    """
    rows = db.execute(
        select(Student, Grade, ClassGroup)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .where(student_scope_predicate(db, current_user))
        .order_by(Student.id)
    ).all()
    return ok(
        {
            "items": [
                {
                    "id": student.id,
                    "student_no": student.student_no,
                    "name": student.masked_name,
                    "grade": grade.name,
                    "class_name": class_group.name,
                    # Gender goes out as a code; 年龄 out as the stored integer
                    # (migration 0011 — the roster only ever had the age). Both are
                    # NULL for rows that predate these columns; the UI must render
                    # those as 「—」 rather than 0/「未知」.
                    "gender": student.gender,
                    "age": student.age,
                    "status": student.status,
                }
                for student, grade, class_group in rows
            ]
        }
    )


@router.get("/students/results")
def list_student_results(
    current_user: PsychDetailScopedReader,
    db: Annotated[Session, Depends(get_db)],
    _roster: OrgAccountReader,
):
    """名册上每一名学生 + 他最近一场测评的结果（没测过的也在）。

    两个依赖是**两道门槛，缺一不可**，理由与角色对照表见
    `analytics_service.ensure_student_result_reader`：这个端点的响应里既有身份列
    （学号/姓名/年级/班级）也有等级列（关注等级/总分），前者属「组织与账号」、
    后者属「学生心理详情」。它因此比同一个界面上的 `GET /care-cases` 严一档，那是
    有意的——心理详情不得成为读组织名册的旁路，正如受控导出不得成为读心理详情的旁路。

    声明位置必须在任何 `/students/{...}` 之前：`/students/{student_id}/...` 一旦先注册，
    这个字面量路径会被当成路径参数（`care.py` 里 `assignable-owners` 那一处记着同一个坑）。
    """
    return ok({"items": student_result_list(db, current_user)})


@router.get("/students/{student_id}/key-questions")
def key_question_answers(
    student_id: int,
    request: Request,
    purpose: str,
    current_user: KeyQuestionReader,
    db: Annotated[Session, Depends(get_db)],
):
    """Key-question responses — the highest sensitivity tier.

    A stated purpose is mandatory and the read is audited before the answer is
    returned, mirroring the prototype's two-step "二次查看重点题" gate.

    Scope is checked before the audit is written: recording "查看重点题" for a
    read we are about to refuse would put a false entry in the student's access
    trail, which is the one thing this trail exists to answer.
    """
    if not purpose.strip():
        raise AppError("PURPOSE_REQUIRED", "查看重点题必须填写查看原因", 422)

    ensure_student_in_scope(db, current_user, student_id)

    # 「最近一次施测」的口径与个案详情一致（施测时间优先，不是 id 最大的一场）——见
    # `assessment_service.latest_session_order`。重点题看的是这一次的答卷。
    sitting = latest_session(db, student_id)
    items: list[dict] = []
    if sitting:
        rows = db.execute(
            select(ScaleQuestion.question_no, AssessmentAnswer.answer)
            .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
            .where(
                AssessmentAnswer.session_id == sitting.id,
                ScaleQuestion.is_key_question.is_(True),
            )
            .order_by(ScaleQuestion.question_no)
        ).all()
        items = [{"question_no": no, "answer": answer} for no, answer in rows]

    write_audit(
        db,
        action="查看重点题",
        resource_type="STUDENT",
        resource_id=str(student_id),
        purpose=purpose,
        actor=current_user,
        request=request,
        student_id=student_id,
    )
    db.commit()
    return ok({"items": items})
