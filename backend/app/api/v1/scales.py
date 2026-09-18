from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.schemas.scale_import import ScaleDraftRequest, ScaleRuleUpdateRequest
from app.services.audit_service import write_audit
from app.services.scale_rule_service import current_config, publish_scale, rule_snapshot, update_rule
from app.services.scale_import_service import (
    create_scale_draft,
    decode_preview_token,
    parse_scale_import,
    preview_scale_import,
)

router = APIRouter(tags=["scales"])


@router.get("/scales/versions")
def scale_versions(
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.ADMIN, RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    """Published and draft scale versions with their question counts."""
    _ = current_user
    scales = db.scalars(
        select(AssessmentScale).order_by(AssessmentScale.id.desc())
    ).all()
    items = []
    for scale in scales:
        total_questions = (
            db.scalar(select(func.count(ScaleQuestion.id)).where(ScaleQuestion.scale_id == scale.id)) or 0
        )
        validity_questions = (
            db.scalar(
                select(func.count(ScaleQuestion.id)).where(
                    ScaleQuestion.scale_id == scale.id,
                    ScaleQuestion.is_validity_question.is_(True),
                )
            )
            or 0
        )
        key_questions = (
            db.scalar(
                select(func.count(ScaleQuestion.id)).where(
                    ScaleQuestion.scale_id == scale.id,
                    ScaleQuestion.is_key_question.is_(True),
                )
            )
            or 0
        )
        active_rule = db.scalar(
            select(ScaleRule).where(ScaleRule.scale_id == scale.id, ScaleRule.status == "ACTIVE")
        )
        items.append(
            {
                "id": scale.id,
                "code": scale.code,
                "name": scale.name,
                "version": scale.version,
                "status": scale.status,
                "published_at": scale.published_at.isoformat() if scale.published_at else None,
                "total_questions": total_questions,
                "content_questions": total_questions - validity_questions,
                "validity_questions": validity_questions,
                "key_questions": key_questions,
                "rule_version": active_rule.rule_version if active_rule else None,
            }
        )
    return ok({"items": items})


# 题库导入的产物是草稿，草稿不生效、不改变任何判定，因此开放给心理老师；
# 真正需要管控的是「发布」（见 publish 端点），那一步仍限管理员。
ScaleImporter = Annotated[
    UserAccount, Depends(require_role(RoleCode.ADMIN, RoleCode.COUNSELOR))
]


@router.post("/scales/import/preview")
async def scale_import_preview(
    current_user: ScaleImporter,
    file: UploadFile = File(...),
):
    content = await file.read()
    rows = parse_scale_import(file.filename or "mht.csv", content)
    _ = current_user
    return ok(preview_scale_import(rows))


@router.post("/scales/drafts")
def create_draft(
    payload: ScaleDraftRequest,
    request: Request,
    current_user: ScaleImporter,
    db: Annotated[Session, Depends(get_db)],
):
    rows = decode_preview_token(payload.preview_token)
    result = create_scale_draft(db, rows, version=payload.version, name=payload.name, created_by=current_user.id)
    write_audit(
        db,
        action="导入题库草稿版本",
        resource_type="ASSESSMENT_SCALE",
        resource_id=str(result["scale_id"]),
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok(result)


def _build_config(current, payload: ScaleRuleUpdateRequest):
    """Merge the request over the current config, so a partial edit is possible."""
    from app.scale_engine.engine import ScoreBand

    bands = None
    if payload.total_levels is not None:
        bands = tuple(ScoreBand(row.code, row.min, row.max) for row in payload.total_levels)
    dimension_bands = None
    if payload.dimension_levels is not None:
        dimension_bands = tuple(ScoreBand(row.code, row.min, row.max) for row in payload.dimension_levels)

    return replace(
        current,
        validity_retest_threshold=payload.validity_retest_threshold or current.validity_retest_threshold,
        total_bands=bands or current.total_bands,
        dimension_bands=dimension_bands or current.dimension_bands,
    )


@router.get("/scales/versions/{scale_id}/rule")
def read_scale_rule(
    scale_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
):
    """The scoring rule in force for a version, plus the shipped defaults."""
    _ = current_user
    return ok(rule_snapshot(db, scale_id))


@router.put("/scales/versions/{scale_id}/rule")
def write_scale_rule(
    scale_id: int,
    payload: ScaleRuleUpdateRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
):
    """Edit the thresholds.

    Saving a PUBLISHED rule creates a new rule version; existing results keep the
    `rule_version` they were scored under.
    """
    before = rule_snapshot(db, scale_id)
    config = _build_config(current_config(db, scale_id), payload)
    result = update_rule(db, current_user, scale_id, config=config)
    write_audit(
        db,
        action="更新量表评分规则",
        resource_type="SCALE_RULE",
        resource_id=str(scale_id),
        actor=current_user,
        request=request,
        detail=(
            f"{before['rule_version']} → {result['rule_version']}"
            + ("（新建版本）" if result["created_new_version"] else "（就地修改草稿）")
        ),
    )
    db.commit()
    return ok({**result, "defaults": before["defaults"]})


@router.post("/scales/versions/{scale_id}/rule/reset")
def reset_scale_rule(
    scale_id: int,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
):
    """Restore the shipped thresholds.

    Creates a new ACTIVE rule rather than deleting history: superseded rules are
    what let an old `assessment_result.rule_version` be resolved back to the
    thresholds that produced it.
    """
    from app.scale_engine.engine import DEFAULT_RULE_CONFIG

    before = rule_snapshot(db, scale_id)
    result = update_rule(db, current_user, scale_id, config=DEFAULT_RULE_CONFIG)
    write_audit(
        db,
        action="恢复量表评分规则默认值",
        resource_type="SCALE_RULE",
        resource_id=str(scale_id),
        actor=current_user,
        request=request,
        detail=f"{before['rule_version']} → {result['rule_version']}",
    )
    db.commit()
    return ok(result)


@router.post("/scales/versions/{scale_id}/publish")
def publish_scale_version(
    scale_id: int,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.ADMIN))],
    db: Annotated[Session, Depends(get_db)],
):
    """Promote a draft to PUBLISHED.

    Publishing changes the instrument every future assessment is scored against,
    so it stays with the administrator even though counselors may prepare drafts.
    """
    result = publish_scale(db, scale_id)
    write_audit(
        db,
        action="发布量表版本",
        resource_type="ASSESSMENT_SCALE",
        resource_id=str(scale_id),
        actor=current_user,
        request=request,
        detail=(
            f"{result['version']} · 规则 {result['rule_version']}"
            + (f" · 归档 {', '.join(result['archived_versions'])}" if result["archived_versions"] else "")
        ),
    )
    db.commit()
    return ok(result)
