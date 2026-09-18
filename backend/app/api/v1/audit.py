from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.audit import AuditLog
from app.models.enums import RoleCode
from app.models.organization import Student
from app.schemas.export import CareCaseExportRequest
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.security.permissions import CONTROLLED_EXPORT, PROGRESS_SUMMARY, PSYCH_SUMMARY, require_capability
from app.services.audit_service import write_audit
from app.services.export_service import export_care_cases_csv

router = APIRouter(tags=["audit-export"])

# 受控导出：心理老师导出心理摘要，德育领导导出进展摘要。管理员仅为 BASE_ONLY，
# 历史上就无权导出心理数据，因此这里用 allow 收窄，不因能力层而放权。
ControlledExporter = Annotated[
    UserAccount,
    Depends(require_capability(CONTROLLED_EXPORT, allow={PSYCH_SUMMARY, PROGRESS_SUMMARY})),
]


def _export_detail(mask_names: bool, scope: str) -> str:
    """Audit detail: what was exported, and whether it was identified.

    The masking mode has to be recorded. A masked and an unmasked export write
    otherwise identical rows — same action, same resource_type, same purpose —
    so the trail could not answer the one question an export audit exists to
    answer: was this file identified? `purpose` is free text and cannot carry it.
    """
    return f"{scope} · {'实名' if not mask_names else '姓名遮蔽'}"


@router.get("/audit-logs")
def audit_logs(
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.ADMIN, RoleCode.COUNSELOR, RoleCode.LEADER))],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="created_at", pattern="^(created_at|action|actor_role|resource_type)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    q: str | None = Query(default=None, max_length=128),
    actor_role: str | None = Query(default=None, pattern="^(student|counselor|leader|admin)$"),
):
    """Paged audit trail.

    The table is append-only and unbounded, so it is paged server-side rather
    than shipped whole. `total` lets the client render a page count, and every
    filter is applied before paging so the count describes the filtered set —
    filtering client-side would only ever filter the current page.

    Scope applies to the rows that name a student: the trail records who looked
    at which student, so an unscoped row like 「查看重点题 / 学生 7」 would hand
    back the very thing the scope guards. Rows with no student attached are
    organisation-level events (登录, 导出, 导入, 改配置) and stay visible — they
    are not student data, and dropping them would empty the page for a counselor
    whose range happens to be small.
    """
    filters = [
        or_(
            AuditLog.student_id.is_(None),
            AuditLog.student_id.in_(
                select(Student.id).where(student_scope_predicate(db, current_user))
            ),
        )
    ]
    if q:
        like = f"%{q}%"
        filters.append(
            or_(AuditLog.action.like(like), AuditLog.resource_type.like(like), AuditLog.resource_id.like(like))
        )
    if actor_role:
        filters.append(AuditLog.actor_role == actor_role)

    total = db.scalar(select(func.count(AuditLog.id)).where(*filters)) or 0

    sort_column = {
        "created_at": AuditLog.created_at,
        "action": AuditLog.action,
        "actor_role": AuditLog.actor_role,
        "resource_type": AuditLog.resource_type,
    }[sort]
    ordering = sort_column.desc() if order == "desc" else sort_column.asc()

    rows = db.scalars(
        select(AuditLog).where(*filters).order_by(ordering, AuditLog.id.desc()).limit(limit).offset(offset)
    ).all()

    # 操作人。`actor_role` 只回答「哪个角色做的」，回答不了「谁做的」——一所学校里
    # 心理老师有好几位，角色这一列指不回具体的人，而审计要追的正是人。
    #
    # 名字在**读取时**现取，不入库：账号改名后整条轨迹仍然指向同一个人，而落盘的名字
    # 会就此定格成一个不再存在的称呼。角色则相反，必须留在行里——它是**当时**的权限
    # 口径，一个人调岗之后，用今天的角色去解释昨天那次导出就解释错了。
    #
    # 一次批量取，不逐行查：这一页最多 200 行，N+1 会把它变成 200 条 SELECT。
    actor_ids = {row.actor_user_id for row in rows if row.actor_user_id is not None}
    actors = (
        {user.id: user for user in db.scalars(select(UserAccount).where(UserAccount.id.in_(actor_ids))).all()}
        if actor_ids
        else {}
    )
    return ok(
        {
            "items": [
                {
                    "id": row.id,
                    "actor_user_id": row.actor_user_id,
                    "actor_role": str(row.actor_role) if row.actor_role else None,
                    # 未登录的行为（登录失败）没有 actor_user_id，这里就是 None；
                    # 被尝试的账号在 resource_id 上，不往这一列里搬——「谁做的」
                    # 和「对谁做的」混成一列之后，两条轨迹就再也分不开了。
                    "actor_name": actors[row.actor_user_id].display_name if row.actor_user_id in actors else None,
                    "actor_account": actors[row.actor_user_id].account if row.actor_user_id in actors else None,
                    "action": row.action,
                    "resource_type": row.resource_type,
                    "resource_id": row.resource_id,
                    "purpose": row.purpose,
                    "result": row.result,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@router.post("/care-cases/export")
def care_case_export(
    payload: CareCaseExportRequest,
    request: Request,
    current_user: ControlledExporter,
    db: Annotated[Session, Depends(get_db)],
):
    if not payload.purpose:
        raise AppError("PURPOSE_REQUIRED", "受控导出必须填写用途", 422)
    csv_text = export_care_cases_csv(
        db,
        current_user,
        student_ids=payload.student_ids,
        mask_names=payload.mask_names,
        include_score=payload.include_score,
    )
    write_audit(
        db,
        action="导出关注档案摘要",
        resource_type="EXPORT",
        resource_id=None,
        purpose=payload.purpose,
        actor=current_user,
        request=request,
        detail=_export_detail(
            payload.mask_names,
            f"学生 {len(payload.student_ids)} 人" if payload.student_ids else "全部档案",
        ),
    )
    db.commit()
    return Response(content=csv_text, media_type="text/csv; charset=utf-8")


@router.post("/care-cases/high-risk/export")
def high_risk_export(
    payload: CareCaseExportRequest,
    request: Request,
    current_user: ControlledExporter,
    db: Annotated[Session, Depends(get_db)],
):
    if not payload.purpose:
        raise AppError("PURPOSE_REQUIRED", "高度关注导出必须填写用途", 422)
    csv_text = export_care_cases_csv(
        db,
        current_user,
        high_risk_only=True,
        mask_names=payload.mask_names,
        include_score=payload.include_score,
    )
    write_audit(
        db,
        action="导出高度关注摘要",
        resource_type="EXPORT",
        resource_id=None,
        purpose=payload.purpose,
        actor=current_user,
        request=request,
        detail=_export_detail(payload.mask_names, "高度关注"),
    )
    db.commit()
    return Response(content=csv_text, media_type="text/csv; charset=utf-8")


@router.post("/care-cases/{student_id}/export")
def single_care_case_export(
    student_id: int,
    payload: CareCaseExportRequest,
    request: Request,
    current_user: ControlledExporter,
    db: Annotated[Session, Depends(get_db)],
):
    """Single-student controlled export — the prototype's `exportOne`.

    The id comes straight from the client, so it needs the scope guard for the
    same reason the case endpoints do: holding the export capability says a
    counselor may export psych summaries, not which students' summaries.
    """
    if not payload.purpose:
        raise AppError("PURPOSE_REQUIRED", "受控导出必须填写用途", 422)
    ensure_student_in_scope(db, current_user, student_id)
    csv_text = export_care_cases_csv(
        db,
        current_user,
        student_ids=[student_id],
        mask_names=payload.mask_names,
        include_score=payload.include_score,
    )
    write_audit(
        db,
        action="导出单个学生摘要",
        resource_type="EXPORT",
        resource_id=str(student_id),
        purpose=payload.purpose,
        actor=current_user,
        request=request,
        detail=_export_detail(payload.mask_names, f"学生 {student_id}"),
        student_id=student_id,
    )
    db.commit()
    return Response(content=csv_text, media_type="text/csv; charset=utf-8")
