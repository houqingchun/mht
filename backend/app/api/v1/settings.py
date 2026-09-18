from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.schemas.setting import SettingsUpdateRequest
from app.security.permissions import MANAGE, ORG_ACCOUNT, require_capability
from app.services.audit_service import write_audit
from app.services.settings_service import DEFAULTS, describe_changes, get_all, get_namespace, update_namespace
from app.version import VERSION_LABEL

router = APIRouter(prefix="/admin", tags=["admin-settings"])

# Configuration is part of 组织与账号 / 管理, the same capability that guards
# account administration — an operator who can create accounts can also set the
# school's name and vocabulary.
SettingsManager = Annotated[
    UserAccount, Depends(require_capability(ORG_ACCOUNT, allow={MANAGE}))
]


# Branding is the only configuration that must render BEFORE authentication —
# the login screen itself carries the school's name. Keeping this to non-sensitive
# display strings is the minimum that unblocks it; everything else stays behind
# the capability check.
#
# 2026-09-19: `version` 是第四项，**不在 `system_setting` 里**，所以它由下面的
# `read_branding` 单独拼进来，不能进这个元组（那个循环是按 `org[key]` 取的）。
# 它算「非敏感显示串」：打包器、`package-info.txt`、安装器日志、目标机上的
# `runtime\build.json` 都明写着它，这里不是新泄露。放在这个免认证端点上，
# 是为了让「账号与权限」那一页拿到它**不必新开一个端点、也不必新加一次取数**。
BRANDING_KEYS = ("school_name", "brand_name", "brand_subtitle")

public_router = APIRouter(prefix="/public", tags=["public"])


@public_router.get("/branding")
def read_branding(db: Annotated[Session, Depends(get_db)]):
    org = get_namespace(db, "org")
    # 显示的是 `V1.0` 那一串（`VERSION_LABEL`），不是规范的 `1.0.0` ——
    # 界面从 `/openapi.json` 拿不到东西，而这一格是给人念给支持听的。
    return ok({key: org[key] for key in BRANDING_KEYS} | {"version": VERSION_LABEL})


@router.get("/settings")
def read_settings(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Effective configuration plus the shipped defaults.

    Readable by ANY authenticated user, not just administrators: these values
    drive what every role sees — the counselling-room phone number a student
    dials, the follow-up vocabulary a counselor fills in, the completion
    threshold a leader reads. Gating reads on MANAGE left every non-admin
    silently falling back to hardcoded literals.

    Write access stays behind `SettingsManager`; configuration is not sensitive
    to read, but it is consequential to change.
    """
    _ = current_user
    return ok({"values": get_all(db), "defaults": DEFAULTS})


@router.put("/settings/{namespace}")
def write_settings(
    namespace: str,
    payload: SettingsUpdateRequest,
    request: Request,
    current_user: SettingsManager,
    db: Annotated[Session, Depends(get_db)],
):
    if namespace not in DEFAULTS:
        raise AppError("VALIDATION_ERROR", f"未知的配置分组：{namespace}", 422)

    before = get_namespace(db, namespace)
    after = update_namespace(db, namespace, payload.values, current_user)

    write_audit(
        db,
        action="更新系统配置",
        resource_type="SYSTEM_SETTING",
        resource_id=namespace,
        actor=current_user,
        request=request,
        detail=describe_changes(before, after) or "无变化",
    )
    db.commit()
    return ok({"namespace": namespace, "values": after})


@router.post("/settings/{namespace}/reset")
def reset_settings(
    namespace: str,
    request: Request,
    current_user: SettingsManager,
    db: Annotated[Session, Depends(get_db)],
):
    """Restore a namespace to its shipped defaults by clearing stored overrides."""
    if namespace not in DEFAULTS:
        raise AppError("VALIDATION_ERROR", f"未知的配置分组：{namespace}", 422)

    default_values: dict[str, Any] = DEFAULTS[namespace]
    update_namespace(db, namespace, default_values, current_user)
    write_audit(
        db,
        action="重置系统配置",
        resource_type="SYSTEM_SETTING",
        resource_id=namespace,
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok({"namespace": namespace, "values": get_namespace(db, namespace)})
