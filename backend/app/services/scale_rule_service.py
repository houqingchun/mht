"""Read and edit a scale version's scoring rule.

Editing thresholds is not the same as editing a label: the bands decide which
students get flagged, and `assessment_result.rule_version` records the rules that
produced each stored result. So a change to a PUBLISHED rule creates a new rule
version rather than mutating the old one in place — matching the frozen rule that
"已发布版本不可原地修改". A DRAFT rule is edited in place, because nothing has
been scored against it yet.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import (
    DEFAULT_RULE_CONFIG,
    ScaleRuleConfig,
    rule_config_from_json,
    rule_config_to_json,
)

RULE_TYPE = "MHT_SCORING"


def active_rule(db: Session, scale_id: int) -> ScaleRule | None:
    return db.scalar(
        select(ScaleRule)
        .where(ScaleRule.scale_id == scale_id, ScaleRule.rule_type == RULE_TYPE, ScaleRule.status == "ACTIVE")
        .order_by(ScaleRule.id.desc())
    )


def current_config(db: Session, scale_id: int) -> ScaleRuleConfig:
    rule = active_rule(db, scale_id)
    return rule_config_from_json(rule.config_json if rule else None)


def rule_version_for(code: str, scale_version: str) -> str:
    """`MHT` + `MHT-1.1.0` -> `MHT-RULE-1.1.0`, the shape `seed.py` writes.

    Every rule identifier is derived here so the three creators (seed, the rule
    editor,题库导入) cannot drift apart. Two properties matter and both were broken
    by the import path, which used to write `MHT-1.1.0-RULE-DRAFT`:

    * `_bump_version` can parse it. That one reads the *trailing* number, so a
      name ending in a word bumps to `...-RULE-DRAFT-2` and stops being a version.
    * It carries no lifecycle word. A rule row outlives the draft it was created
      with — publishing the scale leaves the row, and its name, exactly where it
      was — so an embedded `DRAFT` ends up rendered to administrators as
      `MHT-1.1.0-RULE-DRAFT · 生效中`, a label that contradicts itself.

    Accepts either `MHT-1.1.0` or a bare `1.1.0`; the version field is typed by
    hand in the import dialog.
    """
    prefix = f"{code}-"
    suffix = scale_version[len(prefix):] if scale_version.startswith(prefix) else scale_version
    return f"{code}-RULE-{suffix}"


def _bump_version(current: str) -> str:
    """`MHT-RULE-1.0.0` -> `MHT-RULE-1.0.1`; anything unparseable gets a suffix."""
    prefix, _, tail = current.rpartition(".")
    if prefix and tail.isdigit():
        return f"{prefix}.{int(tail) + 1}"
    return f"{current}-2"


def rule_snapshot(db: Session, scale_id: int) -> dict:
    scale = db.get(AssessmentScale, scale_id)
    if not scale:
        raise AppError("NOT_FOUND", "量表版本不存在", 404)
    rule = active_rule(db, scale_id)
    return {
        "scale_id": scale.id,
        "scale_version": scale.version,
        "scale_status": scale.status,
        "rule_version": rule.rule_version if rule else None,
        "rule_status": rule.status if rule else None,
        # Editable only while nothing has been scored against it.
        "editable_in_place": bool(rule and rule.status == "DRAFT"),
        "config": rule_config_to_json(rule_config_from_json(rule.config_json if rule else None)),
        "defaults": rule_config_to_json(DEFAULT_RULE_CONFIG),
    }


def update_rule(
    db: Session,
    user: UserAccount,
    scale_id: int,
    *,
    config: ScaleRuleConfig,
) -> dict:
    """Apply a threshold change, creating a new rule version when published."""
    scale = db.get(AssessmentScale, scale_id)
    if not scale:
        raise AppError("NOT_FOUND", "量表版本不存在", 404)

    # The rule must stay consistent with the questions it scores.
    question_numbers = _question_numbers(db, scale_id)
    _validate_config(config, question_numbers)

    payload = rule_config_to_json(config)
    rule = active_rule(db, scale_id)
    created = False

    if rule is None:
        rule = ScaleRule(
            scale_id=scale_id,
            rule_version=rule_version_for(scale.code, scale.version),
            rule_type=RULE_TYPE,
            status="ACTIVE",
            config_json=payload,
        )
        db.add(rule)
        created = True
    elif rule.status == "DRAFT":
        rule.config_json = payload
    else:
        # Published: retire the old rule and activate a new version, so every
        # stored result keeps pointing at the thresholds that produced it.
        rule.status = "RETIRED"
        rule = ScaleRule(
            scale_id=scale_id,
            rule_version=_bump_version(rule.rule_version),
            rule_type=RULE_TYPE,
            status="ACTIVE",
            config_json=payload,
        )
        db.add(rule)
        created = True

    db.flush()
    return {
        "scale_id": scale_id,
        "rule_version": rule.rule_version,
        "created_new_version": created,
        "config": payload,
    }


def publish_scale(db: Session, scale_id: int) -> dict:
    """Promote a draft version to PUBLISHED.

    Without this the import flow was a dead end: it produced a DRAFT that no task
    could ever select (`create_school_assessment_task` only accepts PUBLISHED),
    and nothing in the codebase could change that — `PUBLISHED` was only ever
    written by the seed.

    Publishing archives the previously published version of the same instrument
    rather than deleting it, so results scored under it stay resolvable.
    """
    scale = db.get(AssessmentScale, scale_id)
    if not scale:
        raise AppError("NOT_FOUND", "量表版本不存在", 404)
    if scale.status == "PUBLISHED":
        raise AppError("VALIDATION_ERROR", "该版本已是发布状态", 422)

    question_count = db.scalar(
        select(func.count(ScaleQuestion.id)).where(ScaleQuestion.scale_id == scale_id)
    )
    if not question_count:
        raise AppError("VALIDATION_ERROR", "该版本没有任何题目，不能发布", 422)

    rule = active_rule(db, scale_id)
    if rule is None:
        raise AppError("VALIDATION_ERROR", "该版本没有评分规则，不能发布", 422)

    superseded: list[str] = []
    previously_published = db.scalars(
        select(AssessmentScale).where(
            AssessmentScale.code == scale.code,
            AssessmentScale.status == "PUBLISHED",
            AssessmentScale.id != scale.id,
        )
    ).all()
    for old in previously_published:
        old.status = "ARCHIVED"
        superseded.append(old.version)

    scale.status = "PUBLISHED"
    scale.published_at = datetime.now(UTC).replace(tzinfo=None)
    # The rule becomes live with the scale — keep the two in step.
    rule.status = "ACTIVE"
    db.flush()

    return {
        "scale_id": scale.id,
        "version": scale.version,
        "status": scale.status,
        "rule_version": rule.rule_version,
        "archived_versions": superseded,
    }


def _question_numbers(db: Session, scale_id: int) -> set[int]:
    return set(
        db.scalars(select(ScaleQuestion.question_no).where(ScaleQuestion.scale_id == scale_id)).all()
    )


def _validate_config(config: ScaleRuleConfig, question_numbers: set[int]) -> None:
    """Reject a config the engine could not apply to this scale."""
    if not config.total_bands:
        raise AppError("VALIDATION_ERROR", "总分分段不能为空", 422)
    if not config.dimension_bands:
        raise AppError("VALIDATION_ERROR", "维度分段不能为空", 422)

    for label, bands in (("总分", config.total_bands), ("维度", config.dimension_bands)):
        ordered = sorted(bands, key=lambda band: band.min)
        for index, band in enumerate(ordered):
            if band.min > band.max:
                raise AppError("VALIDATION_ERROR", f"{label}分段「{band.code}」的起始值大于结束值", 422)
            if index and ordered[index - 1].max >= band.min:
                raise AppError("VALIDATION_ERROR", f"{label}分段存在重叠：{ordered[index - 1].code} 与 {band.code}", 422)

    # The rule only makes sense against the questions this scale actually has.
    if question_numbers:
        unknown_validity = config.validity_questions - question_numbers
        unknown_key = config.key_questions - question_numbers
        if unknown_validity:
            raise AppError(
                "VALIDATION_ERROR",
                f"效度题不在该量表中：{sorted(unknown_validity)}",
                422,
            )
        if unknown_key:
            raise AppError("VALIDATION_ERROR", f"重点题不在该量表中：{sorted(unknown_key)}", 422)

    if config.validity_retest_threshold < 1:
        raise AppError("VALIDATION_ERROR", "效度重测阈值必须大于 0", 422)
