"""导出中心：作业列表、下载、撤销（§16.3 / §17(b)，2026-09-19 第 8 期）。

**导出在这个系统里是两跳**：创建作业的端点住在各自的功能模块里（`audit.py` 的三个
受控导出、`tasks.py` 的完成明细与非参与者），它们**只返回作业载荷、不发文件**；文件
一律从这里出去。那条规则的三个理由写在 `export_service` 的导出作业区开头，其中最
直接的一条是：`download_count` / `downloaded_at` 只有在下载**非走这道门不可**时才
说得上是真的。

三个端点的门槛都只有一道——`CONTROLLED_EXPORT` 非 NONE（不传 `allow`，因为这一
能力的四个等级都是「有某种导出许可」，此处要问的是「这个人有没有导出过东西」）。
**逐份作业的可见性另有一层**，落在 `export_service.load_export_job` 里：本人 + 管理
员，其余人 404。所以这里的依赖不重复表达归属，只表达能力。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.schemas.export import RevokeExportRequest
from app.security.permissions import CONTROLLED_EXPORT, require_capability
from app.services.audit_service import write_audit
from app.services.export_service import (
    download_export_job,
    list_export_jobs,
    load_export_job,
    revoke_export_job,
    serialize_export_jobs,
)

router = APIRouter(tags=["exports"])

# 不传 `allow`：`CONTROLLED_EXPORT` 的四个等级（NONE / BASE_ONLY / PROGRESS_SUMMARY /
# PSYCH_SUMMARY）都是「允许某种导出」，而这一页要问的正是「你有没有导出过东西」。
# 具体能不能导**某一份**内容，由创建作业的那个端点自己收窄（例如管理员是 BASE_ONLY，
# 他在 `audit.py` 里被 `allow={PSYCH_SUMMARY, PROGRESS_SUMMARY}` 挡住）——能力层
# 「描述性等级不可互换」那条在这里不适用，因为这一页不发任何学生数据。
ExportReader = Annotated[UserAccount, Depends(require_capability(CONTROLLED_EXPORT))]


@router.get("/export-jobs")
def export_jobs(
    current_user: ExportReader,
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
):
    """导出中心那一张表。心理老师看自己的，管理员看全校的。

    `limit` 有上限（200）而**没有**分页：与 `getCounselorReminders` 那类不同，这一
    页没有「还有多少没显示」的徽章——超过 200 份导出已经远不是一个人会翻的量，而
    真到了那一步该做的是按时间收窄，不是加一个 offset（§10：截断要自己说出来，
    所以这句话写在响应之外的这里，界面上的出路是「导出中心按时间倒序」）。

    载荷里的 `downloadable` 与下载端点共用同一个判据（`_is_downloadable`），所以
    按钮亮不亮和点下去会不会成功是同一句话的两个说法（§11）。
    """
    jobs = list_export_jobs(db, current_user, limit=limit)
    return ok({"items": serialize_export_jobs(db, current_user, jobs)})


@router.get("/export-jobs/{job_id}/download")
def download_export_job_file(
    job_id: int,
    request: Request,
    current_user: ExportReader,
    db: Annotated[Session, Depends(get_db)],
):
    """取回文件字节。**审计在返回数据之前写入**（§16.6 / CLAUDE.md §8）。

    四道拒绝各有各的话（不可见 404 / 不是本人 403 / 已撤销或已过期 410 / 盘上那份
    对不上 409），判据全在 `export_service.download_export_job` 里，这里只负责
    「先记轨迹、再发文件」。

    审计的 `purpose` 取**作业上那一个**，不是让下载的人再填一次：导出与下载是同一次
    操作的下一跳，两次各填一个用途会让同一个文件的轨迹里出现两句不同的意图，而它们
    本该是一句话。`resource_id` 是给人念的编号（`job_no`），操作员报故障时说的就是它。
    """
    job, data = download_export_job(db, current_user, job_id)
    write_audit(
        db,
        action="下载导出文件",
        resource_type="EXPORT",
        resource_id=job.job_no,
        purpose=job.purpose,
        actor=current_user,
        request=request,
        detail=f"{job.export_type} · {job.mask_level} · 第 {job.download_count} 次下载",
    )
    db.commit()
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{job.job_no}.csv"'},
    )


@router.post("/export-jobs/{job_id}/revoke")
def revoke_export_job_route(
    job_id: int,
    payload: RevokeExportRequest,
    request: Request,
    current_user: ExportReader,
    db: Annotated[Session, Depends(get_db)],
):
    """关掉一份导出的下载口。**本人或管理员**（`load_export_job` 那一层判的）。

    管理员能撤销**别人**的作业，这是他在这条链路上唯一的动作，也是数据外泄时的紧急
    开关；但他下不下来（`download_export_job` 里那句 403）——「能叫停」不需要、也不
    该顺带给出「能取走」。
    """
    job = load_export_job(db, current_user, job_id)
    if revoke_export_job(db, job):
        write_audit(
            db,
            action="撤销导出文件",
            resource_type="EXPORT",
            resource_id=job.job_no,
            purpose=job.purpose,
            actor=current_user,
            request=request,
            # 没填原因时写「未填写」而不是留空——**留空与「没问过」分不开**
            # （`assessment_import_service` 的 `resolution` 那一处同一条）。
            detail=f"撤销原因：{payload.reason.strip() if payload.reason else '未填写'}",
        )
    db.commit()
    return ok(serialize_export_jobs(db, current_user, [job])[0])
