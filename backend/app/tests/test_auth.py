from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.account import UserAccount
from app.tests.conftest import auth_headers


def test_four_seed_accounts_can_login(client):
    cases = [
        ("student", "S001"),
        ("counselor", "13800000001"),
        ("leader", "13800000002"),
        ("admin", "admin"),
    ]
    for role, account in cases:
        response = client.post("/api/v1/auth/login", json={"role": role, "account": account, "password": "123456"})
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["user"]["role_code"] == role
        assert "password" not in body["data"]["user"]


def test_mobile_account_cannot_login_as_student(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"role": "student", "account": "13800000001", "password": "123456"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID"


def test_unauthenticated_business_request_returns_401(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_student_cannot_access_admin_accounts(client):
    headers = auth_headers(client, "student", "S001")
    response = client.get("/api/v1/admin/accounts", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_admin_reset_password_invalidates_old_password_and_writes_audit(client, db_session):
    admin_headers = auth_headers(client, "admin", "admin")
    student = db_session.scalar(select(UserAccount).where(UserAccount.account == "S001"))

    response = client.post(
        f"/api/v1/admin/accounts/{student.id}/reset-password",
        headers=admin_headers,
        json={"temporary_password": "654321", "purpose": "本地验收测试"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["must_change_password"] is True

    old_login = client.post("/api/v1/auth/login", json={"role": "student", "account": "S001", "password": "123456"})
    assert old_login.status_code == 401

    new_login = client.post("/api/v1/auth/login", json={"role": "student", "account": "S001", "password": "654321"})
    assert new_login.status_code == 200

    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "重置密码", AuditLog.resource_id == str(student.id))
    )
    assert audit is not None
    assert audit.purpose == "本地验收测试"


def test_login_failure_locks_account_after_threshold(client):
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"role": "student", "account": "S001", "password": "wrong"})
    assert response.status_code == 401

    locked = client.post("/api/v1/auth/login", json={"role": "student", "account": "S001", "password": "123456"})
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "ACCOUNT_LOCKED"

