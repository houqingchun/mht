from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models.account import AuthSession, UserAccount
from app.models.common import now_local_naive
from app.models.enums import RoleCode
from app.security.tokens import decode_access_token
from app.services.auth_service import session_token_hash

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{get_settings().api_prefix}/auth/login")


@dataclass(frozen=True)
class AuthContext:
    """这次请求是谁、用的是哪一条会话。"""

    user: UserAccount
    session: AuthSession


def get_auth_context(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    """解析 token 并**核对服务端的会话行**——每个请求都做。

    §16.5 写的是「每次**敏感接口**访问都要检查会话是否过期、撤销或被强制下线」。
    这里更严一档：`get_current_user` 是**所有**鉴权端点的入口，所以会话判据落在
    它身上就覆盖了全部接口。这么选的理由有三条，都不是「顺手」：

    1. 「这个接口算不算敏感」本身是一条要维护的判据，而它一错就是静默的——某个
       新端点忘了标注，被撤销的会话就还能读它，而没有任何东西会报错。全量校验
       没有这个失败模式。
    2. 判据只有一次 join，不是一次额外往返：`user_account` 本来每请求就要查一次
       （`get_current_user` 一直在做），会话行跟着同一条 SELECT 出来。
    3. 停用账号在这个项目里已经是**立即生效**的（`active` 每请求查一次，见 §4
       「停用 ≠ 删除」），撤销会话与它同类。两者放在同一层，读者不需要记
       「哪一种失效是立即的、哪一种要等 token 过期」。

    四种拒绝各有各的话，因为它们导向的动作不同：账号被停用要找管理员，会话被撤销
    （在别处登出、或管理员重置了密码）只需重新登录，会话过期也一样但要检查是不是
    自己那边时间不对。合成一句「请先登录」会把前两种也变成「重打一遍密码」。
    """
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    jti = payload.get("jti")
    if not user_id or not jti:
        # 这一版的 token 一定带 `jti`（`create_access_token` 的必填参数）。
        # 走到这里说明它是更早的版本签的——那种令牌**全部失效**是有意的：
        # 库里没有与它对应的会话行，我们无从核对它是否被撤销过。
        raise AppError("AUTH_REQUIRED", "请先登录", 401)

    try:
        account_id = int(user_id)
    except (TypeError, ValueError):
        raise AppError("AUTH_REQUIRED", "请先登录", 401) from None

    row = db.execute(
        select(UserAccount, AuthSession)
        .join(AuthSession, AuthSession.user_id == UserAccount.id)
        .where(
            UserAccount.id == account_id,
            AuthSession.jti == jti,
            AuthSession.session_token_hash == session_token_hash(token),
        )
    ).first()
    if row is None:
        raise AppError("AUTH_REQUIRED", "登录状态已失效，请重新登录", 401)

    user, session = row
    if not user.active:
        raise AppError("AUTH_REQUIRED", "账号已停用，请联系管理员", 401)
    if session.revoked_at is not None:
        raise AppError("AUTH_REQUIRED", "登录状态已被撤销，请重新登录", 401)
    if session.expires_at <= now_local_naive():
        raise AppError("AUTH_REQUIRED", "登录状态已过期，请重新登录", 401)

    return AuthContext(user=user, session=session)


def get_current_user(context: Annotated[AuthContext, Depends(get_auth_context)]) -> UserAccount:
    return context.user


def get_current_session(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthSession:
    """「我现在用的是哪一条会话」——登出、改密码、会话管理页都要它。

    与 `get_current_user` 共用 `get_auth_context`，靠 FastAPI 的按请求依赖缓存
    保证那条 join 仍然只查一次（子依赖的结果在同一个请求里被复用）。
    """
    return context.session


def require_role(*roles: RoleCode):
    def checker(current_user: Annotated[UserAccount, Depends(get_current_user)]) -> UserAccount:
        if current_user.role_code not in roles:
            raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
        return current_user

    return checker
