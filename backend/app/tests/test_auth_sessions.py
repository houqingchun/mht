"""可撤销会话：JWT 的 `jti` 与 `auth_session` 行（§16.5，阶段 8）。

2026-09-19 之前「退出登录」只是前端把 token 丢掉——服务端那边那条令牌照样用到
`exp` 为止。「强制下线」这件事根本做不到，而它恰恰是重置密码那条路上的一半：
管理员重置密码最常见的原因就是「他的账号可能被别人用了」。

这一层现在是**每请求**校验（`api/deps.get_auth_context`），比 §16.5 那句「每次
敏感接口访问都要检查」严一档。理由写在那个函数的 docstring 里，其中最实在的一条是：
「这个接口算不算敏感」本身是一条要维护的判据，而它一错就是静默的。

**历史令牌全部失效是有意的**：更早版本签的 token 里没有 `jti`，库里没有与它对应的
会话行，我们无从核对它是否被撤销过——所以它必须被拒，而不是「先当作有效的用着」。
"""

from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import event, select

from app.core.config import get_settings
from app.models.account import AuthSession, UserAccount
from app.models.audit import AuditLog
from app.models.common import now_local_naive
from app.schemas.auth import LoginRequest
from app.services.auth_service import authenticate
from app.tests.conftest import auth_headers

COUNSELOR = ("counselor", "13800000001")
ADMIN = ("admin", "admin")


def login(client, role: str, account: str, password: str = "123456") -> str:
    response = client.post(
        "/api/v1/auth/login", json={"role": role, "account": account, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_records_a_row_that_the_token_names(client, db_session):
    """登录写一行 `auth_session`，而令牌里那个 `jti` 就是它的。

    这一条是整块机制的地基：`session_token_hash` 摘要的是**这一枚令牌**，所以
    「同一个账号的另一条会话」不会被误判成这一条——下面
    `test_revoking_one_device_does_not_touch_the_other` 断的就是这件事。
    """
    token = login(client, *COUNSELOR)
    payload = jwt.decode(
        token, get_settings().jwt_secret, algorithms=[get_settings().jwt_algorithm]
    )
    assert payload["jti"]

    row = db_session.scalar(select(AuthSession).where(AuthSession.jti == payload["jti"]))
    assert row is not None
    assert row.revoked_at is None
    assert row.expires_at > now_local_naive()
    # `session_token_hash` 摘的是**令牌那串字**，而明文一个字节都不落库：
    # 库里存一份原文等于把凭据复制一份，而这一列的全部用途就是「比对是不是这一枚」。
    assert len(row.session_token_hash) == 64
    assert token not in row.session_token_hash


def test_a_token_without_a_jti_is_rejected(client):
    """历史令牌（没有 `jti`）一律失效——**这是有意的，不是漏了兼容**。

    给它补一行会话等于把一批从未被登记过的令牌当成合格的凭据；而它能不能被撤销
    恰恰是这一层唯一要回答的问题。所以判据是「库里有没有这一行」，而那一行只可能
    由 `authenticate` 写。
    """
    settings = get_settings()
    stale = jwt.encode(
        {
            "sub": "2",
            "role": "counselor",
            "exp": datetime.now(UTC) + timedelta(minutes=30),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    response = client.get("/api/v1/auth/me", headers=bearer(stale))
    assert response.status_code == 401
    # 与「会话被撤销」分开说：这两件事导向的动作不同（一个重新登录就行，另一个要
    # 先问一句「是不是在别处登出过」）。
    assert "请先登录" in response.json()["error"]["message"]


def test_logging_out_kills_the_token_immediately(client):
    """退出登录之后，**同一枚令牌的下一个请求就被拒**。

    此前这里是「前端把 token 丢掉」：被偷走的那一份照样用到过期为止（`auth_session`
    的模型 docstring 说的就是这个）。所以这一条断的是**服务端**的状态，不是响应码。
    """
    token = login(client, *COUNSELOR)
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 200

    assert client.post("/api/v1/auth/logout", headers=bearer(token)).status_code == 200

    after = client.get("/api/v1/auth/me", headers=bearer(token))
    assert after.status_code == 401
    assert "撤销" in after.json()["error"]["message"]


def test_revoking_one_device_does_not_touch_the_other(client):
    """两台设备：撤销其中一台，另一台照常。

    两种失败都在这一条里：撤销**没生效**（第一台还能用）与撤销**撤过头**（第二台
    也被踢了）。只断一半的话，一个「撤销时把该账号全部会话一起撤掉」的实现会通过。
    """
    laptop = login(client, *COUNSELOR)
    phone = login(client, *COUNSELOR)

    listed = client.get("/api/v1/auth/sessions", headers=bearer(phone)).json()["data"]["items"]
    assert len(listed) == 2
    current = next(item for item in listed if item["is_current"])
    other = next(item for item in listed if not item["is_current"])
    assert current["is_current"] is True, "服务端自己算「哪一条是我」，不让前端猜"

    revoked = client.post(
        f"/api/v1/auth/sessions/{other['id']}/revoke", headers=bearer(phone)
    )
    assert revoked.status_code == 200

    assert client.get("/api/v1/auth/me", headers=bearer(laptop)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(phone)).status_code == 200


def test_revoking_your_own_current_device_points_at_the_logout_button(client):
    """想撤销当前这一条时给 **422 与一句指路的话**，不是照做。

    照做的下一步才失败是最难理解的那种：用户点了「撤销」，页面看起来还好好的。
    """
    token = login(client, *COUNSELOR)
    listed = client.get("/api/v1/auth/sessions", headers=bearer(token)).json()["data"]["items"]
    current = next(item for item in listed if item["is_current"])

    response = client.post(f"/api/v1/auth/sessions/{current['id']}/revoke", headers=bearer(token))
    assert response.status_code == 422
    assert "退出" in response.json()["error"]["message"]
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 200


def test_revoke_others_keeps_the_device_i_am_using(client):
    """「退出其它所有设备」——当前这一条留着，否则用户点完就被自己登出。

    返回值里的 `revoked_sessions` 也一起断：界面上那句「已撤销 N 台设备」就是它。
    """
    laptop = login(client, *COUNSELOR)
    phone = login(client, *COUNSELOR)
    tablet = login(client, *COUNSELOR)

    response = client.post("/api/v1/auth/sessions/revoke-others", headers=bearer(tablet))
    assert response.status_code == 200
    assert response.json()["data"]["revoked_sessions"] == 2

    assert client.get("/api/v1/auth/me", headers=bearer(tablet)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(laptop)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(phone)).status_code == 401


def test_a_session_row_belonging_to_someone_else_is_a_404(client, db_session):
    """别人的会话 id 与不存在的 id 回**同一句话同一个码**。

    `session_id` 是客户端传来的，分开报就等于确认了某个 id 存在——正是 §24 / §26
    那条「不可分辨」。这里先证明「那个 id 确实存在」，否则下面那半句在没建过第二台
    设备的库上也是绿的。
    """
    mine = login(client, *COUNSELOR)
    other = login(client, *COUNSELOR)
    sessions = client.get("/api/v1/auth/sessions", headers=bearer(other)).json()["data"]["items"]
    theirs_id = next(item["id"] for item in sessions if not item["is_current"])
    assert db_session.get(AuthSession, theirs_id) is not None

    admin = login(client, *ADMIN)
    theirs = client.post(
        f"/api/v1/auth/sessions/{theirs_id}/revoke", headers=bearer(admin)
    )
    missing = client.post("/api/v1/auth/sessions/999999/revoke", headers=bearer(admin))

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["error"] == missing.json()["error"]
    assert client.get("/api/v1/auth/me", headers=bearer(mine)).status_code == 200


def test_an_expired_session_is_refused_even_though_the_token_is_still_signed(client, db_session):
    """会话过期由 `expires_at` 推出来，与令牌自己的 `exp` 是两口钟。

    把 `expires_at` 推到过去，令牌本身仍然是合法签名的——所以这一条证明的是
    「判据落在服务端那一行上」，而不是「PyJWT 说它过期了」。
    """
    token = login(client, *COUNSELOR)
    sessions = client.get("/api/v1/auth/sessions", headers=bearer(token)).json()["data"]["items"]
    row = db_session.get(AuthSession, sessions[0]["id"])
    row.expires_at = now_local_naive() - timedelta(seconds=1)
    db_session.flush()

    after = client.get("/api/v1/auth/me", headers=bearer(token))
    assert after.status_code == 401
    assert "过期" in after.json()["error"]["message"]


# --- 管理员重置密码（§16.5） --------------------------------------------------


def test_resetting_a_password_requires_a_reason(client, db_session):
    """§16.5：**管理员重置密码必须填写原因**。

    原因进的是审计的 `purpose` 那一列，而它回答的是「这个人为什么动别人的账号」。
    缺了它，轨迹上那条记录与一次随手的操作长得一样。
    """
    admin = auth_headers(client, *ADMIN)
    user = db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))

    without = client.post(
        f"/api/v1/admin/accounts/{user.id}/reset-password",
        headers=admin,
        json={"temporary_password": "reset-pw-1"},
    )
    assert without.status_code == 422

    blank = client.post(
        f"/api/v1/admin/accounts/{user.id}/reset-password",
        headers=admin,
        json={"temporary_password": "reset-pw-1", "purpose": ""},
    )
    assert blank.status_code == 422


def test_resetting_a_password_never_returns_or_records_the_plaintext(client, db_session):
    """§16.5 逐字：**不返回或记录明文密码**。

    两条路各断一次，因为它们各自能单独成立：

    - **不回传**：此前响应体里带着 `temporary_password`，而统一响应封装会把整份
      payload 发回浏览器。所以断言的是「那几个字不在响应里」——不是「某个键不在」，
      一个改名后的 `temp_pwd` 照样是它。
    - **不记录**：审计的 `detail` 与 `purpose` 里都不许出现。`detail` 是会被导出的
      文本，写进去等于把凭据复制一份（§16.6）。
    """
    admin = auth_headers(client, *ADMIN)
    user = db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    secret = "reset-pw-should-not-be-loggable"

    response = client.post(
        f"/api/v1/admin/accounts/{user.id}/reset-password",
        headers=admin,
        json={"temporary_password": secret, "purpose": "用户遗忘密码"},
    )
    assert response.status_code == 200
    assert secret not in response.text

    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "重置密码", AuditLog.resource_id == str(user.id))
    )
    assert audit is not None
    assert audit.purpose == "用户遗忘密码"
    assert secret not in (audit.detail or "")
    # 记的是**结果**：目标账号、撤销了几条会话、要求下次登录改密。
    assert user.account in audit.detail
    assert "修改密码" in audit.detail


def test_resetting_a_password_revokes_every_session_including_the_one_in_use(client, db_session):
    """§16.5：重置之后**旧会话全部撤销**，含那个人此刻正在用的那一条。

    与「改自己的密码」有意不同（那一处保留当前会话，否则用户改完密码当场被登出）。
    管理员重置的是**别人的**密码，而重置最常见的原因就是「他的账号可能被别人用了」
    ——留一条活动会话等于把这件事做了一半。
    """
    user = db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    laptop = login(client, *COUNSELOR)
    phone = login(client, *COUNSELOR)
    assert client.get("/api/v1/auth/me", headers=bearer(laptop)).status_code == 200

    admin = auth_headers(client, *ADMIN)
    response = client.post(
        f"/api/v1/admin/accounts/{user.id}/reset-password",
        headers=admin,
        json={"temporary_password": "reset-pw-2", "purpose": "疑似账号被盗"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"must_change_password": True, "revoked_sessions": 2}

    for token in (laptop, phone):
        after = client.get("/api/v1/auth/me", headers=bearer(token))
        assert after.status_code == 401
        assert "撤销" in after.json()["error"]["message"]

    # 新密码能登进来，而它带着「下次登录必须改」。
    fresh = client.post(
        "/api/v1/auth/login",
        json={"role": "counselor", "account": "13800000001", "password": "reset-pw-2"},
    )
    assert fresh.status_code == 200
    assert fresh.json()["data"]["user"]["must_change_password"] is True


def test_changing_your_own_password_keeps_the_device_you_are_on(client, db_session):
    """改自己的密码保留**当前**这一条，撤销其余——与上面那条是有意的差别。

    两种失败都断了：当前会话被一起撤销（用户改完密码当场被登出，会以为改失败了），
    以及其余会话没被撤销（改密码这件事本身就是在说「我怀疑别人拿到了我的密码」）。
    """
    laptop = login(client, *COUNSELOR)
    phone = login(client, *COUNSELOR)

    changed = client.post(
        "/api/v1/auth/change-password",
        headers=bearer(phone),
        json={"old_password": "123456", "new_password": "chosen-by-user"},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["revoked_sessions"] == 1

    assert client.get("/api/v1/auth/me", headers=bearer(phone)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(laptop)).status_code == 401


def test_the_parent_row_is_updated_before_the_session_row_is_inserted(db_session):
    """登录这一次 flush 里，`user_account` 的 UPDATE 必须排在 `auth_session` 的 INSERT 之前。

    **这不是风格问题，是一次实测到过的死锁**（2026-09-19，阶段 8 的全量 e2e 里
    `POST /api/v1/auth/login` 报 500，后端日志里是 `1213 Deadlock found when trying to
    get lock`，落点正是 `create_auth_session` 里那次 `db.flush()`）。成因是两个确定的
    事实叠起来：

    - `auth_session_ibfk_1` 指着 `user_account`，而 InnoDB 在**插入子行时会先在父行上
      取一把共享锁**做外键检查；
    - 那两件事在**同一个 flush** 里（`authenticate` 先标脏 `user`，`create_auth_session`
      自己 `flush()`），而 `AuthSession` 与 `UserAccount` 之间**只有表级外键、没有
      `relationship()`**——unit of work 没有那条依赖边可排，次序不保证。

    于是两个并发登录同一个账号的请求可以各持一把锁互等：一方 INSERT 拿到父行 S 锁、
    再去要 UPDATE 的 X 锁；另一方同理，它的 INSERT 与对方的 S 锁**兼容**，于是也拿到了、
    也去要 X 锁。两边都等对方先放手 → MySQL 1213。

    **实测（探针，独立跑三遍，结果一致）：去掉 `authenticate` 里那一行 `db.flush()`，
    SQLAlchemy 每次都先发 INSERT 再发 UPDATE。** 所以次序是碰运气，而运气在 5 个并发的
    e2e worker 上必输一次（它们全用同几个种子账号）；真实学校里管理员账号被两台机器
    同时登录也是常事。

    **为什么不写并发用例**：那样的用例只能靠运气复现（`1213` 不是每次都发生），而一条
    时红时绿的守卫很快会被人关掉。这一条断言的是那件**确定的事**——语句的先后，
    而它正是上面那个不确定性的唯一来源。变异验证：把 `authenticate` 里那一行
    `db.flush()` 删掉，下面两个下标当场反过来。
    """
    statements: list[str] = []

    def _capture(conn, cursor, statement, params, context, executemany):
        normalized = " ".join(statement.split())
        if normalized.split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            statements.append(normalized)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _capture)
    try:
        authenticate(
            db_session,
            LoginRequest(account=COUNSELOR[1], password="123456", role=COUNSELOR[0]),
            None,
        )
    finally:
        event.remove(bind, "before_cursor_execute", _capture)
        db_session.rollback()

    update_at = next(
        (i for i, s in enumerate(statements) if s.startswith("UPDATE user_account ")), None
    )
    insert_at = next(
        (i for i, s in enumerate(statements) if s.startswith("INSERT INTO auth_session ")), None
    )
    # 先证明两条都真的发出去了：只写后半句的话，一个「两条语句一条都没发」的实现也能过。
    assert update_at is not None, f"登录没有更新 user_account：{statements}"
    assert insert_at is not None, f"登录没有写 auth_session：{statements}"
    assert update_at < insert_at, (
        "父行的 UPDATE 排在了子行 INSERT 后面——两个并发登录会撞 MySQL 1213 死锁："
        f"{statements}"
    )
