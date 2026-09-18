"""Forced password rotation.

`must_change_password` defaults to True at the model level, which is the safe
default for any account created on someone else's behalf. The frontend gates on
that flag, so the backend contract it relies on is:

  * seeded initial accounts are NOT flagged (they are documented credentials),
  * an admin reset DOES flag the account,
  * POST /auth/change-password clears the flag and invalidates the old password.
"""

from app.models.account import UserAccount
from app.tests.conftest import auth_headers


def test_seeded_accounts_are_not_flagged_for_rotation(client):
    """Published initial credentials shouldn't gate every login."""
    headers = auth_headers(client, "admin", "admin")
    me = client.get("/api/v1/auth/me", headers=headers).json()["data"]
    assert me["must_change_password"] is False


def test_admin_reset_flags_the_account(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    accounts = client.get("/api/v1/admin/accounts", headers=admin).json()["data"]["items"]
    counselor = next(a for a in accounts if a["account"] == "13800000001")

    response = client.post(
        f"/api/v1/admin/accounts/{counselor['id']}/reset-password",
        headers=admin,
        json={"temporary_password": "reset-pw-1", "purpose": "用户遗忘密码"},
    )
    assert response.status_code == 200

    user = db_session.get(UserAccount, counselor["id"])
    assert user.must_change_password is True


def test_change_password_clears_the_flag_and_swaps_the_secret(client, db_session):
    admin = auth_headers(client, "admin", "admin")
    accounts = client.get("/api/v1/admin/accounts", headers=admin).json()["data"]["items"]
    counselor = next(a for a in accounts if a["account"] == "13800000001")

    client.post(
        f"/api/v1/admin/accounts/{counselor['id']}/reset-password",
        headers=admin,
        json={"temporary_password": "reset-pw-1", "purpose": "用户遗忘密码"},
    )
    user = db_session.get(UserAccount, counselor["id"])
    assert user.must_change_password is True

    # The user signs in with the issued password and rotates it.
    headers = auth_headers(client, "counselor", "13800000001", "reset-pw-1")
    changed = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"old_password": "reset-pw-1", "new_password": "chosen-by-user"},
    )
    assert changed.status_code == 200

    db_session.refresh(user)
    assert user.must_change_password is False

    # Old password no longer works; the new one does.
    stale = client.post(
        "/api/v1/auth/login",
        json={"role": "counselor", "account": "13800000001", "password": "reset-pw-1"},
    )
    assert stale.status_code != 200

    fresh = client.post(
        "/api/v1/auth/login",
        json={"role": "counselor", "account": "13800000001", "password": "chosen-by-user"},
    )
    assert fresh.status_code == 200
    assert fresh.json()["data"]["user"]["must_change_password"] is False


def test_change_password_rejects_wrong_current_password(client):
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"old_password": "not-my-password", "new_password": "whatever-123"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AUTH_INVALID"


def test_change_password_rejects_too_short_new_password(client):
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"old_password": "123456", "new_password": "abc"},
    )
    assert response.status_code == 422
