from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.models.permission import RolePermission
from app.schemas.auth import (
    ChangePasswordRequest,
    CreateAccountRequest,
    LoginRequest,
    PermissionMatrixRequest,
    ResetPasswordRequest,
    UpdateAccountRequest,
)
from app.security.passwords import hash_password, verify_password
from app.security.permissions import (
    CAPABILITY_DEFAULTS,
    CAPABILITY_LABELS,
    CAPABILITY_LEVELS,
    LEVEL_DESCRIPTIONS,
    MANAGE,
    ORG_ACCOUNT,
    SCOPE_LABELS,
    level_is_valid,
    levels_for,
    permission_matrix,
    require_capability,
)
from app.services.audit_service import write_audit
from app.services.auth_service import (
    authenticate,
    create_account,
    reset_password,
    resolve_scope_names,
    serialize_user,
    scope_audit_detail,
    scope_options,
    update_account,
)

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin/accounts", tags=["admin-accounts"])
permission_router = APIRouter(prefix="/admin", tags=["admin-permissions"])

# 账号管理与权限矩阵本身都归入「组织与账号 / 管理」能力，与矩阵保持一致。
OrgAccountManager = Annotated[
    UserAccount, Depends(require_capability(ORG_ACCOUNT, allow={MANAGE}))
]


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]):
    try:
        user, token = authenticate(db, payload)
        write_audit(db, action="登录成功", resource_type="USER_ACCOUNT", resource_id=str(user.id), actor=user, request=request)
        db.commit()
        return ok({"access_token": token, "token_type": "bearer", "user": serialize_user(db, user)})
    except AppError:
        write_audit(
            db,
            action="登录失败",
            resource_type="USER_ACCOUNT",
            resource_id=payload.account,
            result="FAILED",
            request=request,
            detail="账号、角色或密码不正确",
        )
        db.commit()
        raise


@router.post("/logout")
def logout(current_user: Annotated[UserAccount, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    write_audit(db, action="退出", resource_type="USER_ACCOUNT", resource_id=str(current_user.id), actor=current_user)
    db.commit()
    return ok()


@router.get("/me")
def me(current_user: Annotated[UserAccount, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    return ok(serialize_user(db, current_user))


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if not verify_password(payload.old_password, current_user.password_hash):
        raise AppError("AUTH_INVALID", "原密码不正确", 422)
    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    write_audit(db, action="修改本人密码", resource_type="USER_ACCOUNT", resource_id=str(current_user.id), actor=current_user)
    db.commit()
    return ok()


@admin_router.get("")
def list_accounts(
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    users = db.scalars(select(UserAccount).order_by(UserAccount.id)).all()
    # 名称表只读一次给它下面每一行用（见 `resolve_scope_names`）。
    scope_names = resolve_scope_names(db)
    return ok({"items": [serialize_user(db, user, scope_names) for user in users]})


@admin_router.get("/scope-options")
def list_scope_options(
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    """「新建账号 / 编辑账号」里那一个数据范围下拉框的选项。

    放在 `/admin/accounts` 之下而不是 `/{account_id}/…` 之下，是因为它不针对某个账号。
    与 `/{account_id}/reset-password` 路径段数不同，不会互相吃掉。
    """
    _ = current_user
    return ok({"items": scope_options(db)})


@admin_router.post("")
def create_account_endpoint(
    payload: CreateAccountRequest,
    request: Request,
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    """新建一个心理老师 / 德育领导 / 系统管理员账号。

    密码由操作员给定，且**不写进审计**：审计行要能回答「谁在什么时候加了谁、
    给他配了多大范围」，而密码是凭据，写进去等于把它复制到一张会被导出的表里。
    """
    user = create_account(
        db,
        role_code=payload.role_code,
        display_name=payload.display_name,
        account=payload.account,
        temporary_password=payload.temporary_password,
        scopes=payload.scopes,
    )
    write_audit(
        db,
        action="新建账号",
        resource_type="USER_ACCOUNT",
        resource_id=str(user.id),
        actor=current_user,
        request=request,
        detail=scope_audit_detail(db, user),
    )
    db.commit()
    return ok(serialize_user(db, user, resolve_scope_names(db)))


@admin_router.patch("/{account_id}")
def update_account_endpoint(
    account_id: int,
    payload: UpdateAccountRequest,
    request: Request,
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    user = db.get(UserAccount, account_id)
    if not user:
        raise AppError("NOT_FOUND", "账号不存在", 404)
    # 自锁防护，与权限矩阵那条同源：停用自己会让这一页之后谁都进不来。
    if payload.active is False and user.id == current_user.id:
        raise AppError(
            "VALIDATION_ERROR", "不能停用自己的账号：停用之后就没人能再进入这一页了", 422
        )

    changed = update_account(
        db,
        user,
        display_name=payload.display_name,
        scopes=payload.scopes,
        active=payload.active,
    )
    if "display_name" in changed or "scopes" in changed:
        write_audit(
            db,
            action="更新账号",
            resource_type="USER_ACCOUNT",
            resource_id=str(user.id),
            actor=current_user,
            request=request,
            detail=f"{'+'.join(changed)}; {scope_audit_detail(db, user)}",
        )
    # 停用与启用各写一条**自己的**动作码，而不是同一条「更新账号」：审计页按眼睛
    # 看到的名字搜（缺口 7 那条教训），而操作员搜的是「停用账号」这四个字。
    if "active" in changed:
        write_audit(
            db,
            action="启用账号" if user.active else "停用账号",
            resource_type="USER_ACCOUNT",
            resource_id=str(user.id),
            actor=current_user,
            request=request,
            detail=f"账号 {user.account}",
        )
    db.commit()
    return ok(serialize_user(db, user, resolve_scope_names(db)))


@admin_router.post("/{account_id}/reset-password")
def admin_reset_password(
    account_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    user = db.get(UserAccount, account_id)
    if not user:
        raise AppError("NOT_FOUND", "账号不存在", 404)
    temporary_password = reset_password(db, user, payload.temporary_password)
    write_audit(
        db,
        action="重置密码",
        resource_type="USER_ACCOUNT",
        resource_id=str(user.id),
        actor=current_user,
        purpose=payload.purpose,
        request=request,
    )
    db.commit()
    return ok({"temporary_password": temporary_password, "must_change_password": True})


@permission_router.get("/permissions")
def get_permissions(
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    _ = current_user
    return ok(
        {
            "items": permission_matrix(db),
            "scope_labels": SCOPE_LABELS,
            # 每一项能力**能用**哪些等级，以及每个等级的中文释义。前端照这两个
            # 字典渲染下拉框，不再给每个格子都列全 12 个等级；释义也从这里来，
            # 免得同一句话在前后端各写一份。
            "capability_levels": CAPABILITY_LEVELS,
            "level_descriptions": LEVEL_DESCRIPTIONS,
            # 出厂矩阵。界面靠它标出「已改」的格子、并知道「恢复默认」要恢复成什么——
            # 矩阵那一栏给的是**生效值**，分不出哪一格是学校自己配的、哪一格是回退来的。
            # 让前端另抄一份默认表是不行的：两处默认值迟早会对不上，而这里恰好是
            # 「看起来没改过、其实早就偏离了」最难被发现的地方。
            "defaults": {
                capability_key: {role.value: scope for role, scope in per_role.items()}
                for capability_key, per_role in CAPABILITY_DEFAULTS.items()
            },
        }
    )


@permission_router.put("/permissions")
def update_permissions(
    payload: PermissionMatrixRequest,
    request: Request,
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    """Update one or more matrix cells.

    Two guards, in this order: the cell has to be a real one, and the admin has
    to keep the capability that opens this endpoint.
    """
    # 先过定义域。少了这一关，一个手写的请求能把 `student / 组织与账号 = 管理` 存进库：
    # 它不会被任何端点认出来（`scope_allows` 只做集合成员判断，学生那一侧没有任何
    # 端点把 MANAGE 放进 allow），但权限页从此显示一条并不存在的授权。能被保存、
    # 却什么也不做的配置，比拒绝保存更难排查。
    for entry in payload.entries:
        if entry.capability_key not in CAPABILITY_LEVELS:
            raise AppError("VALIDATION_ERROR", f"未知的能力：{entry.capability_key}", 422)
        if not level_is_valid(entry.capability_key, entry.scope_level):
            options = "、".join(
                SCOPE_LABELS.get(level, level) for level in levels_for(entry.capability_key)
            )
            raise AppError(
                "VALIDATION_ERROR",
                f"{CAPABILITY_LABELS[entry.capability_key]}不支持"
                f"「{SCOPE_LABELS.get(entry.scope_level, entry.scope_level)}」，可选：{options}",
                422,
            )

    # Lockout guard: an admin may not revoke the ADMIN role's own ORG_ACCOUNT
    # capability, which is what grants access to this endpoint.
    for entry in payload.entries:
        if (
            entry.role_code == RoleCode.ADMIN.value
            and entry.capability_key == ORG_ACCOUNT
            and entry.scope_level != MANAGE
        ):
            raise AppError(
                "VALIDATION_ERROR",
                "系统管理员必须保有「组织与账号 = 管理」，否则将无法再进入权限配置",
                422,
            )

    for entry in payload.entries:
        row = db.scalar(
            select(RolePermission).where(
                RolePermission.role_code == entry.role_code,
                RolePermission.capability_key == entry.capability_key,
            )
        )
        if row is None:
            row = RolePermission(
                role_code=entry.role_code,
                capability_key=entry.capability_key,
                scope_level=entry.scope_level,
            )
            db.add(row)
        else:
            row.scope_level = entry.scope_level
        row.updated_by = current_user.id

    write_audit(
        db,
        action="更新权限配置",
        resource_type="ROLE_PERMISSION",
        resource_id=None,
        actor=current_user,
        request=request,
        detail="; ".join(
            f"{e.role_code}:{e.capability_key}={e.scope_level}" for e in payload.entries
        )[:1000],
    )
    db.commit()
    return ok({"items": permission_matrix(db)})

