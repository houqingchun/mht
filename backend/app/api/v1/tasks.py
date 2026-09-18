from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.schemas.assessment import CreateAssessmentTaskRequest, UpdateAssessmentTaskRequest
from app.services.audit_service import write_audit
from app.services.task_service import (
    create_school_assessment_task,
    list_assessment_tasks,
    task_completion,
    task_completion_csv,
    update_assessment_task,
)

router = APIRouter(tags=["assessment-tasks"])

# RULE_CONFLICT (re-opened and re-resolved 2026-09-17): 测评任务是**学校业务**，
# 不是系统级配置，所以系统管理员整块退出这个功能面（读与写都不再包含 ADMIN）。
# 2026-09-16 那版裁决把读开放给三角色、写留给 ADMIN，理由是 `docs/phase0_rule_freeze.md`
# 写着「管理员管理任务」；用户随后指出那句话本身是错的——管理员只关注系统级配置。
# 写权归心理老师（业务闭环 `测评任务 → 答题 → 评分 → 风险提示 → 复核 …` 的负责人），
# 德育领导保持**只读**：它的能力集是学校级聚合与摘要（§4），是监督口径而不是运营口径。
TASK_READERS = (RoleCode.COUNSELOR, RoleCode.LEADER)


@router.get("/assessment-tasks")
def tasks(
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": list_assessment_tasks(db, current_user)})


@router.post("/assessment-tasks")
def create_task(
    payload: CreateAssessmentTaskRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    task = create_school_assessment_task(
        db,
        current_user,
        name=payload.name,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    write_audit(
        db,
        action="创建测评任务",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task.id),
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok({"id": task.id, "task_no": task.task_no})


@router.patch("/assessment-tasks/{task_id}")
def update_task(
    task_id: int,
    payload: UpdateAssessmentTaskRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    task = update_assessment_task(
        db,
        current_user,
        task_id,
        name=payload.name,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    write_audit(
        db,
        action="编辑测评任务",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task.id),
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok({"id": task.id, "task_no": task.task_no, "name": task.name})


@router.get("/assessment-tasks/{task_id}/completion")
def completion(
    task_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": task_completion(db, current_user, task_id)})


@router.get("/assessment-tasks/{task_id}/completion/export")
def completion_export(
    task_id: int,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
):
    csv_text = task_completion_csv(db, current_user, task_id)
    write_audit(
        db,
        action="导出任务完成统计",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task_id),
        actor=current_user,
        request=request,
    )
    db.commit()
    return Response(content=csv_text, media_type="text/csv; charset=utf-8")
