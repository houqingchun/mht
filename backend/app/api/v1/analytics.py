from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.security.permissions import AGGREGATE_STATS, SCHOOL, SCOPED, STUDENT_PSYCH_DETAIL, require_capability
from app.services.analytics_service import (
    analytics_by_class,
    analytics_by_grade,
    analytics_overview,
    analytics_report,
    counselor_reminders,
    dimension_distribution,
    leader_progress,
)

router = APIRouter(tags=["analytics"])

# 聚合统计：心理老师看授权范围，德育领导看学校范围。两者都是许可，其余角色为 NONE。
AggregateStatsReader = Annotated[
    UserAccount, Depends(require_capability(AGGREGATE_STATS, allow={SCOPED, SCHOOL}))
]


@router.get("/analytics/overview")
def overview(
    current_user: AggregateStatsReader,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(analytics_overview(db, current_user))


@router.get("/analytics/by-grade")
def by_grade(
    current_user: AggregateStatsReader,
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": analytics_by_grade(db, current_user)})


@router.get("/analytics/by-class")
def by_class(
    current_user: AggregateStatsReader,
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": analytics_by_class(db, current_user)})


@router.get("/analytics/dimensions")
def dimensions(
    current_user: AggregateStatsReader,
    db: Annotated[Session, Depends(get_db)],
):
    """Per-dimension aggregate for the workbench / analytics bar charts."""
    return ok({"items": dimension_distribution(db, current_user)})


@router.get("/analytics/report")
def report(
    current_user: AggregateStatsReader,
    db: Annotated[Session, Depends(get_db)],
    task_id: int | None = Query(default=None, alias="taskId", ge=1),
    task_ids: list[int] | None = Query(default=None, alias="taskIds"),
    analysis_mode: str = Query(default="ALL_CALCULATED", alias="analysisMode"),
):
    """按一个或多个测评任务返回报表快照；跨任务时每名学生只取最新结果。"""
    normalized_ids = list(dict.fromkeys(task_ids or ([] if task_id is None else [task_id])))
    if len(normalized_ids) > 20:
        from app.core.errors import AppError

        raise AppError("VALIDATION_ERROR", "一次最多合并分析20个测评任务", 422)
    return ok(analytics_report(db, current_user, task_id, analysis_mode, normalized_ids))


@router.get("/counselor/reminders")
def reminders(
    current_user: Annotated[UserAccount, Depends(require_capability(STUDENT_PSYCH_DETAIL, allow={SCOPED}))],
    db: Annotated[Session, Depends(get_db)],
):
    """Upcoming and overdue follow-ups / retests for the workbench timeline.

    返回值与其它列表接口不同：这里多一个 `total`。每个来源都封顶 `REMINDER_LIMIT`
    条，而截断了的「20 项」与真的 20 项在界面上长得一样——工作量的口径不能靠
    一个被截断的数组回答。同形状的先例是 `GET /audit-logs`（CLAUDE.md §10）。
    """
    return ok(counselor_reminders(db, current_user))


@router.get("/leader/progress")
def progress(
    current_user: Annotated[
        UserAccount, Depends(require_capability(AGGREGATE_STATS, allow={SCHOOL}))
    ],
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": leader_progress(db, current_user)})
