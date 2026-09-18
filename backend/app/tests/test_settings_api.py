"""System settings: defaults, overrides, and the fail-safe contract.

The property that matters is that an empty or unreadable table behaves exactly
like the shipped defaults — the UI must never blank out because configuration
has not been written yet. This mirrors the capability matrix's design.
"""

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.setting import SystemSetting
from app.services.settings_service import DEFAULTS, get_namespace
from app.tests.conftest import auth_headers


def test_defaults_apply_when_nothing_is_stored(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    response = client.get("/api/v1/admin/settings", headers=headers)
    assert response.status_code == 200

    data = response.json()["data"]
    assert set(data["values"]) == set(DEFAULTS)
    # Nothing stored -> values equal the shipped defaults exactly.
    for namespace, defaults in DEFAULTS.items():
        assert data["values"][namespace] == defaults


def test_readings_are_merged_over_defaults(client, db_session):
    db_session.add(SystemSetting(namespace="org", key="school_name", value_json="示例中学"))
    db_session.commit()

    headers = auth_headers(client, "admin", "admin")
    values = client.get("/api/v1/admin/settings", headers=headers).json()["data"]["values"]

    assert values["org"]["school_name"] == "示例中学"
    # Untouched keys still come from the defaults.
    assert values["org"]["brand_name"] == DEFAULTS["org"]["brand_name"]


def test_update_persists_and_audits(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/settings/org",
        headers=headers,
        json={"values": {"school_name": "某某中学", "counselling_room": "教学楼201"}},
    )
    assert response.status_code == 200
    assert response.json()["data"]["values"]["school_name"] == "某某中学"

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "更新系统配置"))
    assert audit is not None
    assert "school_name" in (audit.detail or "")


def test_setting_a_value_back_to_default_removes_the_row(client, db_session):
    """The table should only hold deviations, not rows that say nothing."""
    headers = auth_headers(client, "admin", "admin")
    client.put(
        "/api/v1/admin/settings/org",
        headers=headers,
        json={"values": {"school_name": "临时校名"}},
    )
    assert db_session.scalar(select(SystemSetting).where(SystemSetting.key == "school_name"))

    client.put(
        "/api/v1/admin/settings/org",
        headers=headers,
        json={"values": {"school_name": DEFAULTS["org"]["school_name"]}},
    )
    db_session.expire_all()
    assert db_session.scalar(select(SystemSetting).where(SystemSetting.key == "school_name")) is None


def test_unknown_namespace_and_key_are_rejected(client):
    headers = auth_headers(client, "admin", "admin")

    bad_namespace = client.put(
        "/api/v1/admin/settings/not_a_namespace", headers=headers, json={"values": {"x": 1}}
    )
    assert bad_namespace.status_code == 422

    bad_key = client.put(
        "/api/v1/admin/settings/org", headers=headers, json={"values": {"nonexistent_key": 1}}
    )
    assert bad_key.status_code == 422
    assert bad_key.json()["error"]["code"] == "VALIDATION_ERROR"


def test_reset_restores_defaults(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    client.put(
        "/api/v1/admin/settings/ui",
        headers=headers,
        json={"values": {"low_completion_threshold": 60}},
    )
    assert get_namespace(db_session, "ui")["low_completion_threshold"] == 60

    reset = client.post("/api/v1/admin/settings/ui/reset", headers=headers)
    assert reset.status_code == 200
    assert reset.json()["data"]["values"]["low_completion_threshold"] == (
        DEFAULTS["ui"]["low_completion_threshold"]
    )


def test_list_vocabulary_round_trips(client):
    """Business vocabularies are lists; they must survive the JSON column intact."""
    headers = auth_headers(client, "admin", "admin")
    codes = ["电话", "到校会面", "线上沟通", "家访"]
    response = client.put(
        "/api/v1/admin/settings/care",
        headers=headers,
        json={"values": {"contact_channels": codes}},
    )
    assert response.status_code == 200
    assert response.json()["data"]["values"]["contact_channels"] == codes


def test_any_authenticated_user_can_read_settings(client):
    """Reads drive what every role sees, so gating them on MANAGE left
    non-admins silently falling back to hardcoded literals."""
    for role, account in (("counselor", "13800000001"), ("leader", "13800000002"), ("student", "S001")):
        headers = auth_headers(client, role, account)
        response = client.get("/api/v1/admin/settings", headers=headers)
        assert response.status_code == 200, role
        assert "org" in response.json()["data"]["values"]


def test_unauthenticated_cannot_read_settings(client):
    assert client.get("/api/v1/admin/settings").status_code == 401


def test_non_admin_cannot_write_settings(client):
    for role, account in (("counselor", "13800000001"), ("leader", "13800000002"), ("student", "S001")):
        headers = auth_headers(client, role, account)
        assert client.put(
            "/api/v1/admin/settings/org", headers=headers, json={"values": {"school_name": "x"}}
        ).status_code == 403, role


def test_partial_update_leaves_other_keys_alone(client):
    headers = auth_headers(client, "admin", "admin")
    client.put(
        "/api/v1/admin/settings/cadence",
        headers=headers,
        json={"values": {"follow_up_days": 3}},
    )
    values = client.get("/api/v1/admin/settings", headers=headers).json()["data"]["values"]
    assert values["cadence"]["follow_up_days"] == 3
    assert values["cadence"]["retest_days"] == DEFAULTS["cadence"]["retest_days"]
