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
from app.services.care_service import get_student_assessment_records

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


@router.get("/students/{student_id}/sessions/{session_id}/full-answers")
def session_full_answers(
    student_id: int,
    session_id: int,
    request: Request,
    purpose: str,
    current_user: KeyQuestionReader,
    db: Annotated[Session, Depends(get_db)],
):
    """完整答卷（100 道题的逐题答案）——与重点题同一敏感度层级。

    与 `key_question_answers` 共享 `KeyQuestionReader` 能力矩阵，因为完整答卷
    比重点题多 98 道题，但敏感度相同（都是原始作答内容）。
    每次读都写审计，在被拒时不写（与重点题同源）。
    """
    if not purpose.strip():
        raise AppError("PURPOSE_REQUIRED", "查看答卷必须填写查看原因", 422)

    ensure_student_in_scope(db, current_user, student_id)

    # 验证 session 属于该学生
    sitting = db.get(AssessmentSession, session_id)
    if sitting is None or sitting.student_id != student_id:
        raise AppError("NOT_FOUND", "该场次不属于这名学生", 404)

    # 取全部 100 道题的答案 + 题干（含效度题）
    rows = db.execute(
        select(ScaleQuestion.question_no, ScaleQuestion.question_text, AssessmentAnswer.answer)
        .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
        .where(AssessmentAnswer.session_id == session_id)
        .order_by(ScaleQuestion.question_no)
    ).all()
    items = [{"question_no": no, "question_text": text, "answer": answer} for no, text, answer in rows]

    write_audit(
        db,
        action="查看完整答卷",
        resource_type="STUDENT",
        resource_id=str(student_id),
        purpose=purpose,
        actor=current_user,
        request=request,
        student_id=student_id,
    )
    db.commit()
    return ok({"items": items, "session_id": session_id})


@router.get("/students/{student_id}/assessment-records")
def student_assessment_records(
    student_id: int,
    request: Request,
    current_user: PsychDetailScopedReader,
    db: Annotated[Session, Depends(get_db)],
):
    """一名学生的测评记录——**不需要他有档案**。

    这是「关注档案不存在」那条死路的第二个出口。全库唯一的开档触发点是重点题 85 / 97
    命中（见 `assessment_service.maybe_raise_risk_events` 的 docstring），所以一个被评成
    「需要关注」、甚至「重点关注」的学生**照样可能没有档案**——而在此之前
    `GET /care-cases/{student_id}` 是唯一能读到「他考过几次、每次多少分」的接口，
    它在没有档案时回 404，前端又拿不到状态码（§2），于是那一行只显示一个 `—`，
    心理老师**真的没有任何入口**看到这个学生。

    门槛与 `GET /care-cases/{student_id}` **逐字相同的一道**（能力 + 数据范围）：两条路
    给的是同一个学生的同一批列（学号 / 姓名 / 等级 / 总分 / 维度分），门槛不同就是
    「同一件事两条路径两个答案」。`/students/results` 之所以要两道，是因为它下发的是
    **整份名册**，不是单个学生——这一条不构成读名册的旁路。

    学生不存在 → 404「学生不存在」，范围外 → 403（两条都由 `ensure_student_in_scope`
    给出，不在这里另写）。**查到人但从未测评 → 200 + 空的 `assessment` 与 `history`**，
    不是 404：那是「他还没测」，与「查无此人」是两件事，而前端把两者都渲染成一条红条
    （§14：空态是一句关于数据的话）。

    每次读都写审计，在被拒时**不写**——理由与 `key-questions` 那一段逐字相同。
    不需要 `purpose`：那是**原始答卷**那一档的要求，这一条与档案详情同档。
    """
    records = get_student_assessment_records(db, current_user, student_id)
    write_audit(
        db,
        action="查看测评记录",
        # **`STUDENT` 而不是 `STUDENT_CARE_CASE`。** 这一条读的是测评记录本身，
        # 而且它存在的理由正是「这名学生没有档案」——对那样的学生写
        # `STUDENT_CARE_CASE` 是一句假话，会让按对象筛审计的人以为他开过档。
        resource_type="STUDENT",
        resource_id=str(student_id),
        actor=current_user,
        request=request,
        student_id=student_id,
    )
    db.commit()
    return ok(records)
