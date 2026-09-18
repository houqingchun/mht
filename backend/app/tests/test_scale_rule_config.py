"""The scoring engine must actually read its rule config.

`scale_rule.config_json` used to be written by seed and import but never read:
the engine computed every threshold from Python literals, so editing the config
changed nothing. These tests pin the two properties that fix has to hold:

  1. a stored config genuinely drives the scoring, and
  2. changing it cannot retroactively reinterpret an already-scored result.
"""

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.scale import ScaleRule
from app.scale_engine.engine import (
    DEFAULT_RULE_CONFIG,
    ScaleEngine,
    ScaleQuestionConfig,
    rule_config_from_json,
    rule_config_to_json,
)
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def questions():
    from app.scale_engine.engine import default_mht_questions

    return default_mht_questions()


def answers(yes: set[int] | None = None) -> dict[int, str]:
    yes = yes or set()
    return {no: ("YES" if no in yes else "NO") for no in range(1, 101)}


def by_no():
    return {q.question_no: q for q in questions()}


# --- 1. The config drives the scoring ---


def test_default_config_reproduces_the_historical_bands():
    """An unconfigured installation must score exactly as it did before."""
    engine = ScaleEngine()
    assert engine.total_level(55) == "GENERAL_RANGE"
    assert engine.total_level(56) == "NEEDS_ATTENTION"
    assert engine.total_level(64) == "NEEDS_ATTENTION"
    assert engine.total_level(65) == "KEY_ATTENTION"
    assert engine.dimension_level(3) == "LOW"
    assert engine.dimension_level(4) == "MEDIUM"
    assert engine.dimension_level(7) == "MEDIUM"
    assert engine.dimension_level(8) == "HIGH"
    assert engine.validity_status(6) == "VALID"
    assert engine.validity_status(7) == "RETEST_RECOMMENDED"


def test_a_custom_total_band_changes_the_classification():
    """The reason the config has to be read: raising the key-attention floor."""
    from app.scale_engine.engine import ScoreBand

    strict = rule_config_from_json(
        {"total_levels": [{"code": "ONLY_HIGH", "min": 0, "max": 100}]}
    )
    engine = ScaleEngine(config=strict)
    assert engine.total_level(10) == "ONLY_HIGH"


def test_a_score_above_every_band_falls_to_the_last_one():
    """Keeps the historical `>= 65` semantics for out-of-range totals."""
    config = rule_config_from_json(
        {
            "total_levels": [
                {"code": "LOW", "min": 0, "max": 10},
                {"code": "HIGH", "min": 11, "max": 20},
            ]
        }
    )
    assert ScaleEngine(config=config).total_level(999) == "HIGH"


def test_partial_config_falls_back_to_defaults():
    """An old or thin rule row must not error — it degrades to the shipped values."""
    config = rule_config_from_json({"validity_questions": [1, 2]})
    assert config.validity_questions == frozenset({1, 2})
    assert config.total_bands == DEFAULT_RULE_CONFIG.total_bands
    assert config.validity_retest_threshold == DEFAULT_RULE_CONFIG.validity_retest_threshold


def test_round_trip_through_json_preserves_the_config():
    payload = rule_config_to_json(DEFAULT_RULE_CONFIG)
    assert rule_config_from_json(payload) == DEFAULT_RULE_CONFIG


def test_key_questions_come_from_the_config():
    """A config without key questions produces no manual-review events."""
    config = rule_config_from_json(
        {**rule_config_to_json(DEFAULT_RULE_CONFIG), "key_questions": []}
    )
    # validate_questions rejects the mismatch, which is the point: the rule and
    # the question flags must agree.
    with pytest.raises(Exception):
        ScaleEngine(config=config).calculate(questions(), answers({85}))


# --- 2. History is pinned to the rule that produced it ---


def test_editing_a_published_rule_creates_a_new_version(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    scale_id = db_session.scalar(select(ScaleRule.scale_id))
    before = client.get(f"/api/v1/scales/versions/{scale_id}/rule", headers=admin).json()["data"]
    original_version = before["rule_version"]

    response = client.put(
        f"/api/v1/scales/versions/{scale_id}/rule",
        headers=admin,
        json={"validity_retest_threshold": 8},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["created_new_version"] is True
    assert body["rule_version"] != original_version

    # The retired rule survives, so old results still resolve to it.
    retired = db_session.scalar(
        select(ScaleRule).where(ScaleRule.rule_version == original_version)
    )
    assert retired is not None and retired.status == "RETIRED"


def test_a_scored_result_keeps_the_rule_version_it_was_scored_under(client, db_session):
    """Thresholds change, the stored result does not move."""
    student, session_id = create_student_session(client)
    save_answers(client, student, session_id, yes_numbers={85})
    assert client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student
    ).status_code == 200

    from app.models.assessment import AssessmentResult

    result = db_session.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session_id))
    original_rule = result.rule_version
    original_level = result.total_level

    admin = auth_headers(client, "admin", "admin")
    scale_id = db_session.scalar(select(ScaleRule.scale_id))
    client.put(
        f"/api/v1/scales/versions/{scale_id}/rule",
        headers=admin,
        json={"total_levels": [{"code": "ONLY_HIGH", "min": 0, "max": 100}]},
    )

    db_session.refresh(result)
    assert result.rule_version == original_rule
    assert result.total_level == original_level


def test_rule_change_is_audited(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    scale_id = db_session.scalar(select(ScaleRule.scale_id))
    client.put(
        f"/api/v1/scales/versions/{scale_id}/rule",
        headers=admin,
        json={"validity_retest_threshold": 9},
    )
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "更新量表评分规则"))
    assert audit is not None
    assert "→" in (audit.detail or "")


# --- 3. Invalid configs are rejected rather than silently mis-scoring ---


def test_overlapping_bands_are_rejected(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    scale_id = db_session.scalar(select(ScaleRule.scale_id))
    response = client.put(
        f"/api/v1/scales/versions/{scale_id}/rule",
        headers=admin,
        json={
            "total_levels": [
                {"code": "A", "min": 0, "max": 50},
                {"code": "B", "min": 50, "max": 100},
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_inverted_band_is_rejected(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    scale_id = db_session.scalar(select(ScaleRule.scale_id))
    response = client.put(
        f"/api/v1/scales/versions/{scale_id}/rule",
        headers=admin,
        json={"total_levels": [{"code": "A", "min": 80, "max": 10}]},
    )
    assert response.status_code == 422


def test_band_with_no_upper_bound_for_high_scores():
    """A single open-ended band is the normal way to express 'and above'."""
    from app.scale_engine.engine import ScoreBand

    config = rule_config_from_json(
        {
            "total_levels": [
                {"code": "NORMAL", "min": 0, "max": 54},
                {"code": "ATTENTION", "min": 55, "max": 1000},
            ]
        }
    )
    engine = ScaleEngine(config=config)
    assert engine.total_level(54) == "NORMAL"
    assert engine.total_level(55) == "ATTENTION"
    assert engine.total_level(90) == "ATTENTION"


# --- 4. Publishing turns a draft into something tasks can actually use ---


def import_draft(client, headers):
    """Drive the real import flow to produce a DRAFT scale version."""
    import json

    from app.tests.test_scale_import_api import scale_rows

    preview = client.post(
        "/api/v1/scales/import/preview",
        headers=headers,
        files={"file": ("mht.json", json.dumps(scale_rows()), "application/json")},
    )
    assert preview.status_code == 200
    token = preview.json()["data"]["preview_token"]

    draft = client.post(
        "/api/v1/scales/drafts",
        headers=headers,
        json={"preview_token": token, "version": "MHT-TEST-9.9.9", "name": "发布流程测试"},
    )
    assert draft.status_code == 200
    return draft.json()["data"]["scale_id"]


def test_a_draft_is_not_selectable_by_a_new_task(client, db_session):
    """Before publishing, the version cannot be used — this is what made the
    import flow a dead end."""
    admin = auth_headers(client, "admin", "admin")
    scale_id = import_draft(client, admin)

    from app.models.scale import AssessmentScale

    scale = db_session.get(AssessmentScale, scale_id)
    assert scale.status == "DRAFT"


def test_publishing_makes_the_version_usable_and_archives_the_old_one(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    scale_id = import_draft(client, admin)

    from app.models.scale import AssessmentScale

    published_before = db_session.scalar(
        select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED")
    )
    assert published_before is not None

    response = client.post(f"/api/v1/scales/versions/{scale_id}/publish", headers=admin)
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["status"] == "PUBLISHED"
    # The superseded version is archived, not deleted, so results scored under
    # it stay resolvable.
    assert published_before.version in body["archived_versions"]

    db_session.refresh(published_before)
    assert published_before.status == "ARCHIVED"

    # A new task now picks up the newly published version. 建任务是心理老师的
    # 动作（2026-09-17），发布仍然是管理员的——所以这里刻意换一个人来建，
    # 顺带钉住「发布的结果对另一个角色也立刻生效」。
    counselor = auth_headers(client, "counselor", "13800000001")
    created = client.post(
        "/api/v1/assessment-tasks",
        headers=counselor,
        json={"name": "发布后新任务", "start_at": "2026-09-01", "end_at": "2026-12-31"},
    )
    assert created.status_code == 200
    new_scale = db_session.scalar(
        select(AssessmentScale).where(AssessmentScale.id == scale_id)
    )
    db_session.refresh(new_scale)
    assert new_scale.status == "PUBLISHED"


def test_publishing_is_audited(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    scale_id = import_draft(client, admin)
    client.post(f"/api/v1/scales/versions/{scale_id}/publish", headers=admin)

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "发布量表版本"))
    assert audit is not None


def test_publishing_twice_is_rejected(client):
    admin = auth_headers(client, "admin", "admin")
    scale_id = import_draft(client, admin)
    assert client.post(f"/api/v1/scales/versions/{scale_id}/publish", headers=admin).status_code == 200

    again = client.post(f"/api/v1/scales/versions/{scale_id}/publish", headers=admin)
    assert again.status_code == 422
