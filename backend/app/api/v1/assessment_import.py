"""数据中心 → MHT测评记录导入的路由。

权限：照 `scales.py` 的 `ScaleImporter` 用角色依赖（管理员 + 心理老师），不新增第六类能力。
这条链路写的是**测评事实**，不建账号、不动组织结构——与学生信息导入（组织与账号治理，
仅管理员）的风险性质完全不同。反过来说，它也不改判定标准：导入的记录用当前的量表规则
评分，改规则仍然是管理员的事。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.schemas.assessment_import import AssessmentImportCommitRequest
from app.services.assessment_import_service import (
    commit_assessment_import,
    decode_preview_token,
    parse_assessment_import,
    preview_assessment_import,
    resolution_label,
    template_csv,
)
from app.services.audit_service import write_audit

router = APIRouter(tags=["assessment-import"])

AssessmentImporter = Annotated[
    UserAccount, Depends(require_role(RoleCode.ADMIN, RoleCode.COUNSELOR))
]


@router.get("/assessment-import/template")
def assessment_import_template(_: AssessmentImporter):
    """模板。

    与「学生信息导入」的模板不同，这份模板是后端生成的：题号列有 100 个，题干由
    量表决定，前端再抄一遍就会漂移。性别与答案的取值（`1/2`、`1/0`）也是外部平台的
    约定，写在文件里比写在说明里更不容易丢。
    """
    return Response(content=template_csv(), media_type="text/csv; charset=utf-8")


@router.post("/assessment-import/preview")
async def assessment_import_preview(
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    batch_name: str = Form(""),
    tested_on: str = Form(""),
):
    """逐行校验并把能定位到的学生指出来，一行都不写库。"""
    content = await file.read()
    parsed = parse_assessment_import(file.filename or "assessment.csv", content)
    return ok(
        preview_assessment_import(
            db,
            parsed,
            batch_name=batch_name.strip(),
            tested_on=parse_tested_on(tested_on),
            actor=current_user,
        )
    )


@router.post("/assessment-import/commit")
def assessment_import_commit(
    payload: AssessmentImportCommitRequest,
    request: Request,
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
):
    decoded = decode_preview_token(payload.preview_token)
    result = commit_assessment_import(db, decoded, current_user, payload.resolution)
    write_audit(
        db,
        action="导入测评记录",
        resource_type="ASSESSMENT_TASK",
        # 整份文件都被放弃时没有任务可指（`commit_assessment_import` 不建空批次），
        # 这一行于是只有 `detail` 里的那几个数说得清发生了什么
        resource_id=str(result["task_id"]) if result["task_id"] else None,
        actor=current_user,
        request=request,
        # 只记批次名称，**不记原始文件名**：文件名是操作员电脑上的东西（常常就是
        # 「2026年心理普查1.csv」，但也可以是任何东西），而批次名称是经过确认的、
        # 也会出现在任务列表上的那个标签。与之无关的信息不进审计。
        #
        # `updated` / `resolution` 必须记：同一批记录导两遍，一遍是「新建一批」、
        # 一遍是「覆盖上次」，两者的 action / resource_type / resource_id 逐字相同，
        # 只有这几个数能回答「那几条去哪了」（同 §8 导出必须记遮蔽模式的道理）。
        # `age_updated` 指向的是**另一张表**（名册）的改动，这里只记条数，
        # 改了谁、从多少改到多少写在 `更新学生年龄` 那些行上。
        detail=(
            f"batch={decoded['batch'].get('name')}, tested_on={decoded['batch'].get('tested_on')}, "
            f"created={result['created']}, updated={result['updated']}, "
            f"skipped={result['skipped']}, age_updated={result['age_updated']}, "
            f"处置={resolution_label(payload.resolution)}"
        ),
    )
    db.commit()
    return ok(result)


def parse_tested_on(value: str) -> date:
    """测评日期：表单字段，不是文件里的一格，所以格式错直接拒掉整次请求。

    不用 `Form(date)` 让 FastAPI 自己解析，是因为那会返回框架自带的 422 结构，
    绕过统一的响应封装——前端拿不到 `error.message`，只能显示一句笼统的失败。
    """
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "测评日期格式应为 2026-09-16", 422) from exc
    return parsed
