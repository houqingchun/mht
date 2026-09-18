from fastapi import Request
from sqlalchemy.orm import Session

from app.models.account import UserAccount
from app.models.audit import AuditLog


def write_audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    result: str = "SUCCESS",
    actor: UserAccount | None = None,
    purpose: str | None = None,
    request: Request | None = None,
    detail: str | None = None,
    student_id: int | None = None,
) -> None:
    user_agent = request.headers.get("user-agent") if request else None
    ip = request.client.host if request and request.client else None
    db.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_role=actor.role_code.value if actor else None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            student_id=student_id,
            purpose=purpose,
            ip=ip,
            user_agent=user_agent[:255] if user_agent else None,
            result=result,
            detail=detail,
        )
    )

