from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.schemas.export import ValidityRetestExportRequest
from app.security.permissions import (
    AGGREGATE_STATS,
    CONTROLLED_EXPORT,
    PROGRESS_SUMMARY,
    PSYCH_SUMMARY,
    SCHOOL,
    SCOPED,
    STUDENT_PSYCH_DETAIL,
    require_capability,
)
from app.services.analytics_service import (
    analytics_by_class,
    analytics_by_grade,
    analytics_overview,
    analytics_report,
    counselor_reminders,
    dimension_distribution,
    leader_progress,
    validity_retest_csv,
)
from app.services.audit_service import write_audit
from app.services.export_service import (
    EXPORT_TYPE_VALIDITY_RETEST,
    MASK_LEVEL_IDENTIFIED,
    create_export_job,
    serialize_export_jobs,
)

router = APIRouter(tags=["analytics"])

# 聚合统计：心理老师看授权范围，德育领导看学校范围。两者都是许可，其余角色为 NONE。
AggregateStatsReader = Annotated[
    UserAccount, Depends(require_capability(AGGREGATE_STATS, allow={SCOPED, SCHOOL}))
]

# 受控导出：心理老师导出心理摘要，德育领导导出进展摘要。管理员仅为 BASE_ONLY，
# 历史上就无权导出心理数据，因此这里用 allow 收窄，不因能力层而放权。
#
# 这一份与 `tasks.py` / `audit.py` 里那两个别名**逐字同形**，是刻意重复的：没有共享的
# 别名模块，而「受控导出要什么资格」这件事不该有两个定义——三处各写一份的代价是其中
# 一处被改宽（比如漏掉 `allow`）时不会有任何东西报错，而那一处放出去的是一份实名名册。
ControlledExporter = Annotated[
    UserAccount,
    Depends(require_capability(CONTROLLED_EXPORT, allow={PSYCH_SUMMARY, PROGRESS_SUMMARY})),
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


@router.post("/analytics/validity-retest/export")
def validity_retest_export(
    payload: ValidityRetestExportRequest,
    request: Request,
    current_user: AggregateStatsReader,
    db: Annotated[Session, Depends(get_db)],
    _controlled: ControlledExporter,
):
    """把「效度建议复测」那一格背后的人导成一份名单。**不发文件，发一份作业。**

    与其余六份导出同一条路（CLAUDE.md §29）：这里只建作业并返回作业载荷，字节从
    `GET /export-jobs/{job_id}/download` 出去——那三件事（文件短期有效、可撤销、
    能数被下载了几次）每一件都要求下载经过一道门。

    三道门槛，各管一段，缺任何一道都能单独造出一个洞：

    | 门槛 | 拦的是谁 |
    |---|---|
    | `AGGREGATE_STATS: {SCOPED, SCHOOL}` | 这个端点本来就读的是报表 |
    | `CONTROLLED_EXPORT: {PSYCH_SUMMARY, PROGRESS_SUMMARY}` | 与其余受控导出同一道 |
    | `ensure_student_result_reader`（**服务层**） | 德育领导与系统管理员 |

    第三道是这一份文件真正的门，而它**不在这一层**：它要求
    `STUDENT_PSYCH_DETAIL: {SCOPED}` **且** `ORG_ACCOUNT: {MANAGE, READ_BASIC}`，
    与 `GET /students/results` 逐字同一个判据（CLAUDE.md §4：受控导出不能成为绕过
    心理详情的旁路）。上面两道拦不住德育领导——他的 `CONTROLLED_EXPORT` 是
    `PROGRESS_SUMMARY`，而这份名单逐行印着学号、姓名、班级与效度分。

    `mask_level` 照实记 `IDENTIFIED`：这份文件本来就是**派工单**（派人去找这些学生
    重测），遮蔽了就没法派人。它实名，是因为它**必须**实名，而不是因为谁忘了遮蔽——
    遮蔽模式进审计，事后读轨迹的人要能看出这一点（§8）。

    `task_ids` 必填，且与屏幕上的筛选是同一批 id：KPI 上那个数与这份文件的行数同源，
    否则同一屏上会同时存在两个口径（§11）。
    """
    document = validity_retest_csv(db, current_user, task_ids=payload.task_ids)
    job = create_export_job(
        db,
        current_user,
        export_type=EXPORT_TYPE_VALIDITY_RETEST,
        purpose=payload.purpose or "",
        document=document,
        mask_level=MASK_LEVEL_IDENTIFIED,
    )
    write_audit(
        db,
        action="导出效度复测名单",
        resource_type="EXPORT",
        resource_id=job.job_no,
        purpose=job.purpose,
        actor=current_user,
        request=request,
        detail=f"效度复测 {job.row_count} 人 · 实名 · 作业 {job.job_no}",
    )
    db.commit()
    return ok(serialize_export_jobs(db, current_user, [job])[0])
