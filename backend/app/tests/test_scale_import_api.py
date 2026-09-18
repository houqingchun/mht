import json

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import default_mht_questions
from app.services.scale_rule_service import rule_version_for
from app.tests.conftest import auth_headers


def scale_rows():
    return [
        {
            "question_no": question.question_no,
            "question_text": f"题目{question.question_no}",
            "dimension_code": question.dimension_code,
            "is_validity_question": question.is_validity_question,
            "is_key_question": question.is_key_question,
        }
        for question in default_mht_questions()
    ]


def test_admin_can_preview_and_create_scale_draft(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    preview = client.post(
        "/api/v1/scales/import/preview",
        headers=headers,
        files={"file": ("mht.json", json.dumps(scale_rows()), "application/json")},
    )
    assert preview.status_code == 200
    data = preview.json()["data"]
    assert data["valid"] is True
    assert data["preview_token"]

    draft = client.post(
        "/api/v1/scales/drafts",
        headers=headers,
        json={"preview_token": data["preview_token"], "version": "MHT-1.0.1", "name": "中学生心理健康测验"},
    )
    assert draft.status_code == 200
    assert draft.json()["data"]["status"] == "DRAFT"

    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.version == "MHT-1.0.1"))
    assert scale is not None
    assert db_session.scalars(select(ScaleQuestion).where(ScaleQuestion.scale_id == scale.id)).all()
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "导入题库草稿版本")) is not None


def test_scale_preview_rejects_missing_question_and_wrong_key_questions(client):
    headers = auth_headers(client, "admin", "admin")
    rows = scale_rows()[:-1]
    rows[84]["is_key_question"] = False
    preview = client.post(
        "/api/v1/scales/import/preview",
        headers=headers,
        files={"file": ("mht.json", json.dumps(rows), "application/json")},
    )
    assert preview.status_code == 200
    data = preview.json()["data"]
    assert data["valid"] is False
    assert data["preview_token"] is None
    assert "题目总数必须为100" in data["global_errors"]


def test_counselor_may_prepare_a_draft(client):
    """Importing produces a DRAFT, which changes nothing until an administrator
    publishes it — so preparing one is open to the counselor who owns the
    instrument's professional content."""
    headers = auth_headers(client, "counselor", "13800000001")
    preview = client.post(
        "/api/v1/scales/import/preview",
        headers=headers,
        files={"file": ("mht.json", json.dumps(scale_rows()), "application/json")},
    )
    assert preview.status_code == 200
    assert preview.json()["data"]["valid"] is True


def test_student_cannot_preview_scale_import(client):
    headers = auth_headers(client, "student", "S001")
    preview = client.post(
        "/api/v1/scales/import/preview",
        headers=headers,
        files={"file": ("mht.json", json.dumps(scale_rows()), "application/json")},
    )
    assert preview.status_code == 403
    assert preview.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_imported_draft_rule_is_named_like_every_other_rule(client, db_session):
    """导入的草稿规则标识必须是 `MHT-RULE-x.y.z`，形如 seed 写的那一个。

    这条规则行比"草稿"活得久：量表发布之后它仍是生效中的规则，而管理端的评分规则
    面板把标识和状态并排渲染。所以标识里带生命周期词（曾经是 `MHT-1.1.0-RULE-DRAFT`）
    会长期显示成 `MHT-1.1.0-RULE-DRAFT · 生效中`——一个自相矛盾的标签，而且让
    `_bump_version` 这种从尾部取数字的解析器读出 `...-RULE-DRAFT-2`。
    """
    headers = auth_headers(client, "admin", "admin")
    preview = client.post(
        "/api/v1/scales/import/preview",
        headers=headers,
        files={"file": ("mht.json", json.dumps(scale_rows()), "application/json")},
    ).json()["data"]
    draft = client.post(
        "/api/v1/scales/drafts",
        headers=headers,
        json={"preview_token": preview["preview_token"], "version": "MHT-1.0.1", "name": "中学生心理健康测验"},
    )
    assert draft.status_code == 200

    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.version == "MHT-1.0.1"))
    rule = db_session.scalar(select(ScaleRule).where(ScaleRule.scale_id == scale.id))
    assert rule.rule_version == "MHT-RULE-1.0.1"
    assert "DRAFT" not in rule.rule_version


def test_rule_version_for_accepts_a_bare_version_number():
    """版本号是导入弹窗里手填的，两种写法都要落到同一个标识上。"""
    assert rule_version_for("MHT", "MHT-1.1.0") == "MHT-RULE-1.1.0"
    assert rule_version_for("MHT", "1.1.0") == "MHT-RULE-1.1.0"


def test_counselor_cannot_publish_a_version(client):
    """Publishing changes the instrument every future assessment is scored
    against, so it stays with the administrator."""
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.post("/api/v1/scales/versions/1/publish", headers=headers)
    assert response.status_code == 403

