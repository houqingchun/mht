import csv
import io
import json
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import (
    DEFAULT_RULE_CONFIG,
    ScaleEngine,
    ScaleQuestionConfig,
    rule_config_to_json,
)
from app.services.scale_rule_service import rule_version_for


VALIDITY_QUESTIONS = {82, 84, 86, 88, 90, 92, 94, 96, 98, 100}
KEY_QUESTIONS = {85, 97}
DIMENSION_CODES = {
    "LEARNING_ANXIETY",
    "INTERPERSONAL_ANXIETY",
    "LONELINESS",
    "SELF_BLAME",
    "SENSITIVITY",
    "PHYSICAL_SYMPTOMS",
    "PHOBIC_TENDENCY",
    "IMPULSIVE_TENDENCY",
}


def parse_scale_import(filename: str, content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig")
    if filename.lower().endswith(".json"):
        rows = json.loads(text)
        if not isinstance(rows, list):
            raise AppError("VALIDATION_ERROR", "JSON必须是数组", 422)
        return [normalize_question(row) for row in rows]
    reader = csv.DictReader(io.StringIO(text))
    return [normalize_question(row) for row in reader]


def normalize_question(row: dict[str, Any]) -> dict[str, Any]:
    question_no_raw = row.get("question_no") or row.get("题号") or row.get("no")
    try:
        question_no = int(question_no_raw)
    except (TypeError, ValueError):
        question_no = 0
    return {
        "question_no": question_no,
        "question_text": str(row.get("question_text") or row.get("题目文本") or row.get("text") or "").strip(),
        "dimension_code": str(row.get("dimension_code") or row.get("维度") or row.get("dimension") or "").strip(),
        "is_validity_question": parse_bool(row.get("is_validity_question") or row.get("是否效度题")),
        "is_key_question": parse_bool(row.get("is_key_question") or row.get("是否重点题")),
    }


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y", "是"}


def preview_scale_import(rows: list[dict[str, Any]]) -> dict:
    preview_rows = []
    seen: set[int] = set()
    for index, row in enumerate(rows, start=2):
        errors = []
        question_no = row["question_no"]
        if question_no < 1 or question_no > 100:
            errors.append("题号必须在1-100之间")
        if question_no in seen:
            errors.append("重复题号")
        seen.add(question_no)
        if not row["question_text"]:
            errors.append("缺少题干")
        if question_no not in VALIDITY_QUESTIONS and row["dimension_code"] not in DIMENSION_CODES:
            errors.append("维度映射缺失或不正确")
        preview_rows.append({**row, "row_no": index, "errors": errors})

    complete_numbers = set(range(1, 101))
    global_errors = []
    if len(rows) != 100:
        global_errors.append("题目总数必须为100")
    if {row["question_no"] for row in rows} != complete_numbers:
        global_errors.append("题号必须完整覆盖1-100")
    validity_numbers = {row["question_no"] for row in rows if row["is_validity_question"]}
    if validity_numbers != VALIDITY_QUESTIONS:
        global_errors.append("效度题必须为82、84、86、88、90、92、94、96、98、100")
    key_numbers = {row["question_no"] for row in rows if row["is_key_question"]}
    if key_numbers != KEY_QUESTIONS:
        global_errors.append("重点题必须为85和97")

    valid = not global_errors and all(not row["errors"] for row in preview_rows)
    if valid:
        ScaleEngine().validate_questions(
            [
                ScaleQuestionConfig(
                    question_no=row["question_no"],
                    dimension_code=row["dimension_code"] or None,
                    is_validity_question=row["is_validity_question"],
                    is_key_question=row["is_key_question"],
                )
                for row in rows
            ]
        )
    return {
        "total": len(rows),
        "valid": valid,
        "global_errors": global_errors,
        "rows": preview_rows,
        "preview_token": create_preview_token(rows) if valid else None,
    }


def create_preview_token(rows: list[dict[str, Any]]) -> str:
    settings = get_settings()
    return jwt.encode({"rows": rows, "kind": "scale_import_preview"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_preview_token(token: str) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("VALIDATION_ERROR", "题库预览已失效，请重新预览", 422) from exc
    if payload.get("kind") != "scale_import_preview":
        raise AppError("VALIDATION_ERROR", "题库预览无效", 422)
    return payload.get("rows", [])


def create_scale_draft(db: Session, rows: list[dict[str, Any]], *, version: str, name: str, created_by: int) -> dict:
    if db.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT", AssessmentScale.version == version)):
        raise AppError("VALIDATION_ERROR", "该量表版本已存在", 422)
    scale = AssessmentScale(code="MHT", name=name, version=version, status="DRAFT", created_by=created_by)
    db.add(scale)
    db.flush()
    db.add_all(
        [
            ScaleQuestion(
                scale_id=scale.id,
                question_no=row["question_no"],
                question_text=row["question_text"],
                dimension_code=row["dimension_code"] or None,
                is_validity_question=row["is_validity_question"],
                is_key_question=row["is_key_question"],
                status="ACTIVE",
            )
            for row in rows
        ]
    )
    db.add(
        ScaleRule(
            scale_id=scale.id,
            # Named off the scale version, not off the draft-ness of it: this row
            # is still the active rule after the version is published, and the
            # admin panel renders the identifier next to the status — anything
            # lifecycle-shaped in here outlives the lifecycle.
            rule_version=rule_version_for(scale.code, scale.version),
            rule_type="MHT_SCORING",
            status="ACTIVE",
            # A complete config: a draft still needs thresholds, otherwise the
            # operator edits values the engine never reads.
            config_json={**rule_config_to_json(DEFAULT_RULE_CONFIG), "status": "DRAFT"},
        )
    )
    db.flush()
    return {"scale_id": scale.id, "version": scale.version, "status": scale.status}

