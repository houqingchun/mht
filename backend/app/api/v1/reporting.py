from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.schemas.reporting import ReportCreateRequest, ReportDraftRequest, ReportExportRequest
from app.security.permissions import PROFESSIONAL_REPORT_EDIT, PROFESSIONAL_REPORT_PUBLISH, PROFESSIONAL_REPORT_READ, SCHOOL, SCOPED, require_capability
from app.services.audit_service import write_audit
from app.services.export_service import EXPORT_TYPE_PROFESSIONAL_REPORT, MASK_LEVEL_MASKED, create_export_job, serialize_export_jobs
from app.services.reporting_service import create_report, get_report, list_reports, new_version, publish, report_document, save_draft, serialize

router = APIRouter(tags=["professional-reports"])
Reader = Annotated[UserAccount, Depends(require_capability(PROFESSIONAL_REPORT_READ, allow={SCOPED, SCHOOL}))]
Editor = Annotated[UserAccount, Depends(require_capability(PROFESSIONAL_REPORT_EDIT, allow={SCOPED}))]
Publisher = Annotated[UserAccount, Depends(require_capability(PROFESSIONAL_REPORT_PUBLISH, allow={SCOPED}))]

@router.post("/professional-reports")
def create(payload: ReportCreateRequest, request: Request, user: Editor, db: Annotated[Session, Depends(get_db)]):
    report = create_report(db, user, payload); write_audit(db, action="创建专业报告", resource_type="PROFESSIONAL_REPORT", resource_id=str(report.id), actor=user, request=request); db.commit(); return ok(serialize(db, report, include_versions=True))

@router.get("/professional-reports")
def listing(request: Request, user: Reader, db: Annotated[Session, Depends(get_db)]):
    """§7.5：「所有接口必须执行 authentication / capability / data scope / validation / audit」——
    这两个 GET 此前一条审计都不写。列表这一条没有单一 resource_id，所以把可见份数写进 detail。

    `db.commit()` 不能省，也不能指望别处替你提交：`db/session.py` 的 `get_db` 是
    `finally: db.close()`，从不提交——只读路由从前不需要它，而这一次它**变成了写**
    （CLAUDE.md §24 / §25 那条「路由写了但没提交，生产上丢、测试里全绿」）。"""
    items = list_reports(db, user)
    write_audit(db, action="查看专业报告列表", resource_type="PROFESSIONAL_REPORT", actor=user, request=request, detail=f"可见 {len(items)} 份")
    db.commit()
    return ok({"items": items})

@router.get("/professional-reports/{report_id}")
def detail(report_id: int, request: Request, user: Reader, db: Annotated[Session, Depends(get_db)]):
    """同 §7.5：读单个报告也写审计——它给出的是这批学生的聚合统计。

    次序：先 `get_report`（不可见时它抛 404），**被拒时不写审计**（§9）。
    """
    payload = get_report(db, user, report_id)
    write_audit(db, action="查看专业报告", resource_type="PROFESSIONAL_REPORT", resource_id=str(report_id), actor=user, request=request, detail=f"report_no={payload.get('report_no')}; version={payload.get('current_version')}")
    db.commit()
    return ok(payload)

@router.put("/professional-reports/{report_id}/draft")
def draft(report_id: int, payload: ReportDraftRequest, request: Request, user: Editor, db: Annotated[Session, Depends(get_db)]):
    report = save_draft(db, user, report_id, payload); write_audit(db, action="保存专业报告草稿", resource_type="PROFESSIONAL_REPORT", resource_id=str(report.id), actor=user, request=request); db.commit(); return ok(serialize(db, report, include_versions=True))

@router.post("/professional-reports/{report_id}/new-version")
def version(report_id: int, request: Request, user: Editor, db: Annotated[Session, Depends(get_db)]):
    report = new_version(db, user, report_id); write_audit(db, action="创建专业报告版本", resource_type="PROFESSIONAL_REPORT", resource_id=str(report.id), actor=user, request=request, detail=f"version={report.current_version}"); db.commit(); return ok(serialize(db, report, include_versions=True))

@router.post("/professional-reports/{report_id}/publish")
def publish_report(report_id: int, request: Request, user: Publisher, db: Annotated[Session, Depends(get_db)]):
    report = publish(db, user, report_id); write_audit(db, action="发布专业报告", resource_type="PROFESSIONAL_REPORT", resource_id=str(report.id), actor=user, request=request, detail=f"version={report.current_version}"); db.commit(); return ok(serialize(db, report, include_versions=True))

@router.post("/professional-reports/{report_id}/export-jobs")
def export(report_id: int, payload: ReportExportRequest, request: Request, user: Reader, db: Annotated[Session, Depends(get_db)]):
    report, document = report_document(db, user, report_id, payload.version_no)
    version_no = payload.version_no or report.current_version
    job = create_export_job(db, user, export_type=EXPORT_TYPE_PROFESSIONAL_REPORT, purpose=payload.purpose, document=document, mask_level=MASK_LEVEL_MASKED)
    # §8.4 要求这条轨迹答得出**报告编号**与**文件格式**，而不只是「谁导了一份报告」——
    # 同一份报告的不同版本、同一批作业里的不同文件，只靠 job id 读不出是哪一份。
    # 生成时间与下载时间不在这里另记：它们是**作业行自己的列**（`created_at` /
    # `downloaded_at`），审计再抄一份就是第二个出处（§3 那条「表在 labels.ts 里而
    # 没人从那儿取也算没接上」的同族：同一个事实两处定义，漂了看不出来）。
    write_audit(db, action="导出专业报告", resource_type="PROFESSIONAL_REPORT", resource_id=str(report.id), actor=user, request=request, purpose=payload.purpose, detail=f"report_no={report.report_no}; version={version_no}; format=csv; job={job.id}")
    db.commit()
    return ok(serialize_export_jobs(db, user, [job])[0])
