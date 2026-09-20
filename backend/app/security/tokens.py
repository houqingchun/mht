from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.core.errors import AppError


def create_access_token(user_id: int, role: str, jti: str) -> str:
    """Sign an access token that names the `auth_session` row it belongs to.

    `jti` is required, not optional. A token without one cannot be revoked —
    logout would be a client-side gesture, and a stolen token would stay valid
    until `exp`. Making the parameter mandatory is what keeps every issuance
    site (there is exactly one, `auth_service.authenticate`) unable to forget it.

    `exp` stays in UTC while `auth_session.expires_at` is written by
    `now_local_naive()`: they express the same instant on two different bases.
    That is safe only because nothing ever compares them numerically — `exp` is
    checked by PyJWT against another UTC instant, and `expires_at` by our own
    query against a local one.
    """
    settings = get_settings()
    expire_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user_id), "role": role, "jti": jti, "exp": expire_at}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("AUTH_REQUIRED", "请先登录", 401) from exc

