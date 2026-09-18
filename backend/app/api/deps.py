from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.security.tokens import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{get_settings().api_prefix}/auth/login")


def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: Annotated[Session, Depends(get_db)]) -> UserAccount:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    user = db.get(UserAccount, int(user_id)) if user_id else None
    if not user or not user.active:
        raise AppError("AUTH_REQUIRED", "请先登录", 401)
    return user


def require_role(*roles: RoleCode):
    def checker(current_user: Annotated[UserAccount, Depends(get_current_user)]) -> UserAccount:
        if current_user.role_code not in roles:
            raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
        return current_user

    return checker

