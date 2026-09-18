"""Server-side paging and filtering for the audit trail.

The audit log is the one table that grows without bound, so it is paged and
filtered on the server. The property that matters — and that a client-side
filter cannot provide — is that `total` and the page contents describe the
*filtered* set, not the page in hand.
"""

from app.tests.conftest import auth_headers


def seed_audit_events(client):
    """Generate a mix of roles so a filter has something to select."""
    for role, account in (("admin", "admin"), ("counselor", "13800000001"), ("leader", "13800000002")):
        headers = auth_headers(client, role, account)
        client.post("/api/v1/auth/logout", headers=headers)


def test_paging_returns_total_and_slices(client):
    seed_audit_events(client)
    headers = auth_headers(client, "admin", "admin")

    first = client.get("/api/v1/audit-logs?limit=2&offset=0", headers=headers).json()["data"]
    assert len(first["items"]) == 2
    assert first["total"] >= 3
    assert first["limit"] == 2 and first["offset"] == 0

    second = client.get("/api/v1/audit-logs?limit=2&offset=2", headers=headers).json()["data"]
    # A different slice, not a repeat of the first page.
    assert [i["id"] for i in first["items"]] != [i["id"] for i in second["items"]]


def test_offset_past_the_end_returns_empty_not_error(client):
    headers = auth_headers(client, "admin", "admin")
    page = client.get("/api/v1/audit-logs?limit=10&offset=100000", headers=headers).json()["data"]
    assert page["items"] == []
    assert page["total"] >= 0


def test_role_filter_applies_before_paging(client):
    """`total` must describe the filtered set — the whole reason the filter is
    server-side rather than applied to the page in hand."""
    seed_audit_events(client)
    headers = auth_headers(client, "admin", "admin")

    everything = client.get("/api/v1/audit-logs?limit=1", headers=headers).json()["data"]
    counselors = client.get(
        "/api/v1/audit-logs?limit=1&actor_role=counselor", headers=headers
    ).json()["data"]

    assert counselors["total"] <= everything["total"]
    assert all(item["actor_role"] == "counselor" for item in counselors["items"])


def test_role_filter_combines_with_search(client):
    seed_audit_events(client)
    headers = auth_headers(client, "admin", "admin")

    page = client.get(
        "/api/v1/audit-logs?actor_role=admin&q=登录", headers=headers
    ).json()["data"]
    for item in page["items"]:
        assert item["actor_role"] == "admin"
        assert "登录" in item["action"]


def test_unknown_role_filter_is_rejected(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.get("/api/v1/audit-logs?actor_role=wizard", headers=headers)
    assert response.status_code == 422


def test_sorting_applies_to_the_filtered_set(client):
    seed_audit_events(client)
    headers = auth_headers(client, "admin", "admin")

    ascending = client.get(
        "/api/v1/audit-logs?limit=50&sort=action&order=asc", headers=headers
    ).json()["data"]["items"]
    actions = [item["action"] for item in ascending]
    assert actions == sorted(actions, key=lambda a: a)


def test_invalid_sort_column_is_rejected(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.get("/api/v1/audit-logs?sort=password_hash", headers=headers)
    assert response.status_code == 422
