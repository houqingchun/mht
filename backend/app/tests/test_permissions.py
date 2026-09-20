"""Capability-layer tests for the admin permission matrix.

The matrix is additive to `require_role`, so the critical properties are:
  1. an empty table behaves exactly like the old hard-coded guards (fail-safe),
  2. revoking a capability really does block the endpoint,
  3. an admin cannot lock themselves out of the matrix they are editing.
"""

import pytest
from sqlalchemy import select

from app.models.permission import RolePermission
from app.security.permissions import (
    CAPABILITY_DEFAULTS,
    CAPABILITY_LEVELS,
    KEY_QUESTIONS,
    MANAGE,
    NONE,
    ORG_ACCOUNT,
    STUDENT_PSYCH_DETAIL,
    level_is_valid,
    resolve_scope,
    scope_allows,
)
from app.models.enums import RoleCode
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def create_risk_case(client):
    """Drive a student through submission so one care case exists."""
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert response.status_code == 200
    return auth_headers(client, "counselor", "13800000001")


# --- 1. Defaults reproduce the pre-existing behaviour ---


def test_defaults_match_existing_role_guards(db_session):
    """With no rows configured, the defaults must mirror require_role exactly.

    Note the distinction that matters for 学生心理详情: a leader's SUMMARY is a
    permit for aggregate views but NOT for case detail, so the endpoint-level
    `allow={SCOPED}` set is what decides. Asserting the raw scopes keeps both
    halves visible.
    """
    assert resolve_scope(db_session, RoleCode.COUNSELOR, STUDENT_PSYCH_DETAIL) == "SCOPED"
    assert resolve_scope(db_session, RoleCode.LEADER, STUDENT_PSYCH_DETAIL) == "SUMMARY"
    assert resolve_scope(db_session, RoleCode.ADMIN, STUDENT_PSYCH_DETAIL) == NONE
    assert resolve_scope(db_session, RoleCode.STUDENT, STUDENT_PSYCH_DETAIL) == NONE

    # Case detail requires SCOPED specifically.
    assert scope_allows(db_session, RoleCode.COUNSELOR, STUDENT_PSYCH_DETAIL, allow={"SCOPED"})
    assert not scope_allows(db_session, RoleCode.LEADER, STUDENT_PSYCH_DETAIL, allow={"SCOPED"})

    # Aggregate stats accept both scoped and school-wide permits.
    assert scope_allows(db_session, RoleCode.COUNSELOR, "AGGREGATE_STATS", allow={"SCOPED", "SCHOOL"})
    assert scope_allows(db_session, RoleCode.LEADER, "AGGREGATE_STATS", allow={"SCOPED", "SCHOOL"})
    assert not scope_allows(db_session, RoleCode.ADMIN, "AGGREGATE_STATS", allow={"SCOPED", "SCHOOL"})


def test_counselor_reaches_case_detail_under_defaults(client):
    headers = create_risk_case(client)
    response = client.get("/api/v1/care-cases", headers=headers)
    assert response.status_code == 200


def test_leader_is_denied_case_detail_under_defaults(client):
    """A leader holds SUMMARY for psych detail — aggregate only, never case access."""
    create_risk_case(client)
    leader_headers = auth_headers(client, "leader", "13800000002")
    response = client.get("/api/v1/care-cases", headers=leader_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


# --- 2. Revoking a capability actually blocks the endpoint ---


def test_revoking_psych_detail_blocks_counselor(client, db_session):
    headers = create_risk_case(client)
    assert client.get("/api/v1/care-cases", headers=headers).status_code == 200

    db_session.add(
        RolePermission(
            role_code=RoleCode.COUNSELOR.value,
            capability_key=STUDENT_PSYCH_DETAIL,
            scope_level=NONE,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/care-cases", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_unknown_row_falls_back_to_default(client, db_session):
    """A row for a different role must not disturb the unconfigured ones."""
    headers = create_risk_case(client)
    db_session.add(
        RolePermission(
            role_code=RoleCode.LEADER.value,
            capability_key=STUDENT_PSYCH_DETAIL,
            scope_level=NONE,
        )
    )
    db_session.commit()
    # Counselor has no row, so it resolves from CAPABILITY_DEFAULTS.
    assert client.get("/api/v1/care-cases", headers=headers).status_code == 200


def test_granting_scope_opens_access(client, db_session):
    """Raising a role's scope to an accepted level grants the endpoint."""
    create_risk_case(client)
    leader_headers = auth_headers(client, "leader", "13800000002")
    assert client.get("/api/v1/care-cases", headers=leader_headers).status_code == 403

    db_session.add(
        RolePermission(
            role_code=RoleCode.LEADER.value,
            capability_key=STUDENT_PSYCH_DETAIL,
            scope_level="SCOPED",
        )
    )
    db_session.commit()

    assert client.get("/api/v1/care-cases", headers=leader_headers).status_code == 200


def test_revoking_psych_detail_also_blocks_the_task_detail_endpoints(client, db_session):
    """§4 那句「受控导出不能成为绕过心理详情的旁路」，在**三份任务级明细**上各验一次。

    为什么这里要单独一条：这三处的门槛是**三道**，默认角色能各自挡住一个——德育领导
    被心理详情挡、系统管理员被受控导出与任务读者挡（`test_task_participation.py` 与
    `test_task_roles.py` 里各有逐角色的用例）。但那些用例**构造不出**
    「有导出许可、没有心理详情明细」这个组合，而它恰恰是这句承诺要挡的那一格：
    一个心理老师手上拿着 `CONTROLLED_EXPORT`，把 `STUDENT_PSYCH_DETAIL` 降成 `NONE`
    之后还导得动逐行印着姓名的文件，那这条旁路就是通的。

    **第三处（完成明细导出）是 2026-09-20 补的**（缺口 12）：它此前**一道能力门槛都
    没有**，连 `CONTROLLED_EXPORT` 都不查，而它的载荷比前两份还多两列逐人等级。
    同一处还有它的**读**法（`GET …/completion`），那一条没有导出许可这一档，
    所以由 `test_task_roles.py::test_the_leader_reads_the_task_but_not_the_people_in_it`
    与下一节那条提级用例钉住。

    先断一次**能拿到**（否则下面三个 403 在一个端点根本没实现的库上也成立），
    再断降级之后三条都 403——`NONE` 在 `scope_allows` 里比 `allow` 还早一步拒绝，
    所以这一条同时钉住那句「先拒 `NONE`」。

    三处都指向**同一场真实任务**（`/assessment-tasks` 现取，不写死 id）：被拒的判据
    必须是权限，而一个错的 task_id 会给出 404——那与 403 在屏幕上不是一回事。
    """
    headers = auth_headers(client, "counselor", "13800000001")
    items = client.get("/api/v1/assessment-tasks", headers=headers).json()["data"]["items"]
    assert items, "种子里那场任务应该读得到"
    task_id = items[0]["id"]
    exports = (
        f"/api/v1/assessment-tasks/{task_id}/non-participants/export",
        f"/api/v1/assessment-tasks/{task_id}/unmatched-import-rows/export",
        f"/api/v1/assessment-tasks/{task_id}/completion/export",
    )

    for path in exports:
        allowed = client.post(path, headers=headers, json={"purpose": "权限矩阵用例"})
        assert allowed.status_code == 200, f"{path} 现在应该拿得到：{allowed.text}"

    db_session.add(
        RolePermission(
            role_code=RoleCode.COUNSELOR.value,
            capability_key=STUDENT_PSYCH_DETAIL,
            scope_level=NONE,
        )
    )
    db_session.commit()

    for path in exports:
        response = client.post(path, headers=headers, json={"purpose": "权限矩阵用例"})
        assert response.status_code == 403, f"{path} 应该被心理详情那一档挡住"
        assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_the_completion_gate_is_a_capability_not_a_hardcoded_role(client, db_session):
    """缺口 12 加的那道门是**能力矩阵**上的格子，不是「领导一律不行」写死在代码里。

    这条与上面那条是同一个形状的两面，缺一条就不完整：上面证明「撤掉就进不来」，
    这一条证明「授上就进得来」。少了下半句，一个把判据写成
    `if user.role_code == LEADER: raise` 的实现**照样全绿**——而那种写法会让学校
    在权限页上给领导开了这一格之后什么都不发生，却看不出是为什么。

    德育领导的默认值是 `SUMMARY`（聚合，不是逐人明细），所以两个端点都 403；
    把它提到 `SCOPED` 之后两条都放行。**导出那一条还会过 `CONTROLLED_EXPORT`**，
    而领导的默认值是 `PROGRESS_SUMMARY`，本来就在 `allow` 里——所以这一条断的是
    「多出来的那一道心理详情」按矩阵走。
    """
    leader = auth_headers(client, "leader", "13800000002")
    items = client.get("/api/v1/assessment-tasks", headers=leader).json()["data"]["items"]
    assert items, "种子里那场任务应该读得到"
    task_id = items[0]["id"]

    completion = f"/api/v1/assessment-tasks/{task_id}/completion"
    completion_export = f"{completion}/export"

    assert client.get(completion, headers=leader).status_code == 403
    assert (
        client.post(
            completion_export, headers=leader, json={"purpose": "权限矩阵用例"}
        ).status_code
        == 403
    )

    db_session.add(
        RolePermission(
            role_code=RoleCode.LEADER.value,
            capability_key=STUDENT_PSYCH_DETAIL,
            scope_level="SCOPED",
        )
    )
    db_session.commit()

    assert client.get(completion, headers=leader).status_code == 200
    assert (
        client.post(
            completion_export, headers=leader, json={"purpose": "权限矩阵用例"}
        ).status_code
        == 200
    )


# --- 3. Matrix endpoints ---


def test_admin_can_read_matrix(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.get("/api/v1/admin/permissions", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    keys = {row["capability_key"] for row in data["items"]}
    assert keys == set(CAPABILITY_DEFAULTS)
    # Every row carries a scope for all four roles.
    for row in data["items"]:
        assert set(row["roles"]) == {r.value for r in RoleCode}


# --- 3b. 等级的定义域 ---


def test_every_default_is_a_level_its_capability_accepts(db_session):
    """默认矩阵本身必须落在合法等级里。

    这条守的是两张表的一致性：`CAPABILITY_LEVELS` 是写入口收窄出来的定义域，
    `CAPABILITY_DEFAULTS` 是表空时的回退。回退值一旦不在定义域里，权限页会渲染出
    一个下拉框里选不中的格子——而且这一格还偏偏是所有学校的出厂状态。
    """
    for capability_key, per_role in CAPABILITY_DEFAULTS.items():
        assert capability_key in CAPABILITY_LEVELS, f"{capability_key} 没有定义可选等级"
        for role, scope in per_role.items():
            assert level_is_valid(capability_key, scope), f"{capability_key}/{role.value} 的默认值 {scope} 不在可选集里"


def test_matrix_payload_carries_the_levels_and_their_meanings(client):
    """界面的下拉框和释义都从接口来，不在前端另写一份。"""
    headers = auth_headers(client, "admin", "admin")
    data = client.get("/api/v1/admin/permissions", headers=headers).json()["data"]

    assert set(data["capability_levels"]) == set(CAPABILITY_DEFAULTS)
    assert set(data["level_descriptions"]) == set(CAPABILITY_DEFAULTS)

    for capability_key, levels in data["capability_levels"].items():
        assert NONE in levels, f"{capability_key} 必须能配成「无」，否则撤不掉"
        # 每个可选等级都要有中文释义，否则界面上那一档写着「无说明」。
        assert set(levels) <= set(data["level_descriptions"][capability_key])
        # 顺序是前端判断「比默认更宽」的依据，必须从紧到松。
        assert levels == CAPABILITY_LEVELS[capability_key]


def test_a_level_of_another_capability_is_rejected(client):
    """`管理` 是「组织与账号」的等级，不是「聚合统计」的。

    这条就是那 20 格 × 12 级别的旧界面放出来的东西：能存、但是没有任何端点认它。
    """
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "student", "capability_key": "AGGREGATE_STATS", "scope_level": "MANAGE"}
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_the_api_does_not_second_guess_which_role_holds_a_level(client, db_session):
    """定义域按**能力**收窄，不按角色。这条划的是边界本身。

    一个学生能不能看名册是学校的决定，接口不替学校做主——它只保证「查看必要信息」
    确实是这项能力的一个等级，而不是把别的能力的词搬到这一格来。
    """
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "student", "capability_key": ORG_ACCOUNT, "scope_level": "READ_BASIC"}
            ]
        },
    )
    assert response.status_code == 200
    row = next(r for r in response.json()["data"]["items"] if r["capability_key"] == ORG_ACCOUNT)
    assert row["roles"]["student"] == "READ_BASIC"


def test_unknown_capability_key_is_rejected(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "leader", "capability_key": "NOT_A_CAPABILITY", "scope_level": NONE}
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_a_bad_cell_keeps_the_whole_batch_out_of_the_database(client, db_session):
    """一格不合法，整批不落库（先整批校验，再整批写）。

    权限是「一次说明一组角色的边界」这件事，半批生效会让库里停在一个谁都没
    设计过的中间状态——而操作员看到的是一句报错，以为什么都没变。
    """
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "leader", "capability_key": "AGGREGATE_STATS", "scope_level": NONE},
                {"role_code": "leader", "capability_key": KEY_QUESTIONS, "scope_level": "SCHOOL"},
            ]
        },
    )
    assert response.status_code == 422

    stored = db_session.scalars(
        select(RolePermission).where(RolePermission.role_code == "leader")
    ).all()
    assert stored == []


def test_unknown_role_is_rejected(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "head_teacher", "capability_key": ORG_ACCOUNT, "scope_level": MANAGE}
            ]
        },
    )
    # 「班主任」是被冻结掉的角色，不能从这个口子回来。
    assert response.status_code == 422


def test_non_admin_cannot_read_matrix(client):
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.get("/api/v1/admin/permissions", headers=headers)
    assert response.status_code == 403


def test_updating_matrix_persists_and_audits(client, db_session):
    from app.models.audit import AuditLog

    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "leader", "capability_key": "AGGREGATE_STATS", "scope_level": NONE}
            ]
        },
    )
    assert response.status_code == 200

    stored = db_session.scalar(
        select(RolePermission).where(
            RolePermission.role_code == "leader",
            RolePermission.capability_key == "AGGREGATE_STATS",
        )
    )
    assert stored is not None and stored.scope_level == NONE

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "更新权限配置"))
    assert audit is not None


def test_admin_cannot_revoke_own_org_permission(client):
    """Lockout guard: revoking ADMIN's ORG_ACCOUNT would make the matrix unreachable."""
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "admin", "capability_key": ORG_ACCOUNT, "scope_level": NONE}
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("downgrade", [NONE, "READ_BASIC", "READ_SUMMARY"])
def test_admin_org_permission_guard_rejects_any_downgrade(client, downgrade):
    """The guard must reject every value below MANAGE, not just NONE.

    Only MANAGE passes the endpoint's own `allow={MANAGE}` set, so downgrading to
    READ_BASIC would lock admins out just as surely as revoking outright.
    """
    headers = auth_headers(client, "admin", "admin")
    response = client.put(
        "/api/v1/admin/permissions",
        headers=headers,
        json={
            "entries": [
                {"role_code": "admin", "capability_key": ORG_ACCOUNT, "scope_level": downgrade}
            ]
        },
    )
    assert response.status_code == 422


def test_org_account_capability_is_enforced(client, db_session):
    """组织与账号 is not display-only: revoking it must block the account list."""
    admin_headers = auth_headers(client, "admin", "admin")
    assert client.get("/api/v1/admin/accounts", headers=admin_headers).status_code == 200

    counselor_headers = auth_headers(client, "counselor", "13800000001")
    # READ_BASIC grants the roster but not account administration.
    assert client.get("/api/v1/students", headers=counselor_headers).status_code == 200
    assert client.get("/api/v1/admin/accounts", headers=counselor_headers).status_code == 403

    db_session.add(
        RolePermission(
            role_code=RoleCode.COUNSELOR.value,
            capability_key=ORG_ACCOUNT,
            scope_level=NONE,
        )
    )
    db_session.commit()
    assert client.get("/api/v1/students", headers=counselor_headers).status_code == 403


def test_empty_entries_rejected(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.put("/api/v1/admin/permissions", headers=headers, json={"entries": []})
    assert response.status_code == 422
