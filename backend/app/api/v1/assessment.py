from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.schemas.assessment import CreateSessionRequest, SaveAnswerRequest
from app.services.assessment_service import (
    create_or_get_session,
    get_student_session,
    list_session_questions,
    list_student_assessment_history,
    list_student_tasks,
    save_answer,
    session_payload,
    student_for_user,
    submit_session,
)
from app.services.audit_service import write_audit

router = APIRouter(tags=["assessment"])


@router.get("/student/tasks")
def student_tasks(
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": list_student_tasks(db, current_user)})


@router.get("/student/assessment-history")
def student_assessment_history(
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    """The student's own submission history.

    Deliberately omits scores, risk labels and key-question hits — students see
    completion status only.
    """
    return ok({"items": list_student_assessment_history(db, current_user)})


@router.post("/assessment-sessions")
def create_session(
    payload: CreateSessionRequest,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    session = create_or_get_session(db, current_user, payload.task_id)
    db.commit()
    return ok(session_payload(db, session))


@router.get("/assessment-sessions/{session_id}/questions")
def session_questions(
    session_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": list_session_questions(db, current_user, session_id)})


@router.get("/assessment-sessions/{session_id}")
def get_session(
    session_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    session = get_student_session(db, current_user, session_id)
    return ok(session_payload(db, session))


@router.put("/assessment-sessions/{session_id}/answers/{question_no}")
def put_answer(
    session_id: int,
    question_no: int,
    payload: SaveAnswerRequest,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    session = save_answer(db, current_user, session_id, question_no, payload.answer)
    db.commit()
    return ok(session_payload(db, session))


@router.post("/assessment-sessions/{session_id}/submit")
def submit(
    session_id: int,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    data = submit_session(db, current_user, session_id, idempotency_key)
    write_audit(
        db,
        action="提交测评答卷",
        resource_type="ASSESSMENT_SESSION",
        resource_id=str(session_id),
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok(data)


@router.post("/assessment-sessions/{session_id}/reset")
def reset_session(
    session_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.STUDENT))],
    db: Annotated[Session, Depends(get_db)],
):
    """Clear a session's answers so it can be re-taken.

    Intended for development and E2E fixtures. Resets the whole submission
    chain: answer rows, session state, AND the task target — leaving the target
    COMPLETED while the answers are gone would make the student's own task list
    contradict the assessment page.

    `current_question_no` and `answered_count` are derived from the answers in
    `session_payload`, so there is no counter to reset.

    导入的会话（`source=IMPORTED`）不能走这条路。这个方法删答案但**保留 result 行**，
    对导入的记录来说正好是最坏的一种组合：它会留下一份没有答卷、却有评分结果的
    「重点关注」——一份学校无法复核、也无法解释的记录。外部普查的结果要改，
    只能重新导入，不能在系统里被抹掉重来。
    """
    from sqlalchemy import delete

    from app.models.assessment import AssessmentAnswer, AssessmentTarget

    session = get_student_session(db, current_user, session_id)
    if session.source == "IMPORTED":
        raise AppError("SESSION_LOCKED", "该测评由学校导入，不能在系统内重新作答", 409)
    db.execute(delete(AssessmentAnswer).where(AssessmentAnswer.session_id == session.id))
    session.status = "IN_PROGRESS"
    session.submitted_at = None
    # Cleared alongside the answers it was measured from: a surviving value would
    # describe a sitting that no longer exists. The *result* row is deliberately NOT
    # deleted, so the next submit takes submit_session's idempotent early return —
    # which is why that branch re-stamps the submission when `submitted_at` is None.
    # Clearing this without that re-stamp would leave retakes permanently unsigned.
    session.duration_seconds = None

    student = student_for_user(db, current_user)
    if session.task_id is not None:
        target = db.scalar(
            select(AssessmentTarget).where(
                AssessmentTarget.task_id == session.task_id,
                AssessmentTarget.student_id == student.id,
            )
        )
        if target is not None:
            target.status = "IN_PROGRESS"
            target.completed_at = None
    db.commit()
    return ok(session_payload(db, session))

