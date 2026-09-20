from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_session, get_current_user, require_role
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import AuthSession, UserAccount
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
    revoke_all_sessions,
    revoke_session,
    serialize_sessions,
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
        # `request` 一路传进去是给 `auth_session` 记来源用的（IP / 浏览器）——
        # 「这条会话是哪台机器开的」是会话管理页上唯一能帮用户判断的东西。
        user, token = authenticate(db, payload, request)
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
def logout(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
):
    """退出登录：**服务端把这一条会话撤销掉**。

    此前这里只写一条审计——被偷走的 token 照样用到过期，「强制下线」这件事根本
    做不到（`auth_session` 的模型 docstring 说的就是这个）。现在它同时是
    「这条会话到此为止」：下一个带上同一个 token 的请求会在 `get_auth_context`
    被拒。

    动作码「退出」**一个字没改**：审计是已经落库的历史，用户在审计页按眼睛看到的
    名字搜（§3 那条教训）。
    """
    revoke_session(db, session, "用户主动退出")
    write_audit(
        db,
        action="退出",
        resource_type="USER_ACCOUNT",
        resource_id=str(current_user.id),
        actor=current_user,
        detail=f"撤销会话 #{session.id}",
    )
    db.commit()
    return ok()


@router.get("/sessions")
def list_my_sessions(
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
):
    """本人的登录设备清单。**只看得到自己的**——它逐行印出 IP 与浏览器，
    那是账号持有者本人该看见的信息，不是管理员的（管理员要踢人走的是
    「重置密码」那条路，那一次会撤销全部会话）。
    """
    return ok({"items": serialize_sessions(db, current_user.id, current_session_id=session.id)})


# 这两条的顺序不能换：`/sessions/revoke-others` 必须排在 `/sessions/{session_id}/revoke`
# 前面，否则 FastAPI 先把 `revoke-others` 当成一个 `session_id` 去转 int，回 422 而不是
# 执行到这个端点。加新路径时先看一眼有没有更宽的模式排在它前面。
@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    request: Request,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
):
    """「退出其它所有设备」——**当前这一条留着**，否则用户点完就被自己登出，
    还得再登一次才能确认操作成功。
    """
    revoked = revoke_all_sessions(
        db, current_user.id, "用户主动撤销其它登录", except_session_id=session.id
    )
    write_audit(
        db,
        action="撤销登录会话",
        resource_type="USER_ACCOUNT",
        resource_id=str(current_user.id),
        actor=current_user,
        request=request,
        detail=f"撤销其它会话 {revoked} 个",
    )
    db.commit()
    return ok({"revoked_sessions": revoked})


@router.post("/sessions/{session_id}/revoke")
def revoke_my_session(
    session_id: int,
    request: Request,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
):
    target = db.get(AuthSession, session_id)
    # 「不是你的」与「不存在」回同一句话同一个码：`session_id` 是客户端传来的，
    # 分开报就等于确认了某个 id 存在（§24 那条「不可分辨」）。
    if target is None or target.user_id != current_user.id:
        raise AppError("NOT_FOUND", "登录设备不存在", 404)
    if target.id == session.id:
        # 不是「不行」，是「你要的那件事在别处」：用户想退出，而退出按钮就在右上角。
        # 让他在这里把当前设备撤销掉也可以，但下一个请求才失败的登出是最难理解的那种
        # ——点了「撤销」，页面看起来还好好的。
        raise AppError(
            "VALIDATION_ERROR", "这是你当前正在使用的设备。要退出，请用右上角的「退出」", 422
        )
    revoke_session(db, target, "用户主动撤销会话")
    write_audit(
        db,
        action="撤销登录会话",
        resource_type="USER_ACCOUNT",
        resource_id=str(current_user.id),
        actor=current_user,
        request=request,
        detail=f"撤销会话 #{target.id}",
    )
    db.commit()
    return ok()


@router.get("/me")
def me(current_user: Annotated[UserAccount, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]):
    return ok(serialize_user(db, current_user))


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    current_user: Annotated[UserAccount, Depends(get_current_user)],
    session: Annotated[AuthSession, Depends(get_current_session)],
    db: Annotated[Session, Depends(get_db)],
):
    """改自己的密码，**顺手把其它设备上的我踢下线**。

    改密码这件事本身就是在说「我怀疑别人拿到了我的密码」，所以留着别处的会话
    等于没改。但**当前这一条留着**：用户改完密码还要继续用，把他一起登出会让他
    以为改失败了（他手上那份 token 下一个请求才开始被拒）。

    这里**不撤销**当前会话，与 `admin_reset_password` 那边**撤销全部**是有意的
    差别：管理员重置的是别人的密码，那个人手上所有的 token（包括他此刻正在用
    的那一份）都该立刻失效。
    """
    if not verify_password(payload.old_password, current_user.password_hash):
        raise AppError("AUTH_INVALID", "原密码不正确", 422)
    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    # 与 `auth_service.authenticate` 里那一行同一个理由：父行的 UPDATE 必须先发出去，
    # 否则下面 `revoke_all_sessions` 与 `write_audit` 写的子行（`auth_session` /
    # `audit_log`，两张都有指向 `user_account` 的外键）可能在同一个 flush 里抢在它前面
    # ——那是 MySQL 1213 的形状。完整的推导与实测写在 `authenticate` 那一行上面，
    # 「这里今天恰好是对的、靠的是注册次序」那一句写在 `reset_password` 里。
    db.flush()
    revoked = revoke_all_sessions(
        db, current_user.id, "本人修改密码", except_session_id=session.id
    )
    write_audit(
        db,
        action="修改本人密码",
        resource_type="USER_ACCOUNT",
        resource_id=str(current_user.id),
        actor=current_user,
        detail=f"撤销其它会话 {revoked} 个",
    )
    db.commit()
    return ok({"revoked_sessions": revoked})


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
    """管理员重置别人的密码：**强制改密 + 旧会话全部失效 + 不回传明文**。

    三件事都是 §16.5 逐字要求的，而它们各自堵住一种失败：

    - **强制改密**（`must_change_password`）：临时密码是管理员口头/短信给的，
      它必须只活一次登录。下一个请求它就会被 `change_password` 换掉。
    - **撤销全部会话**（含那个人此刻正在用的那一条）：重置密码最常见的原因就是
      「他的账号可能被别人用了」，留一条活动会话等于把这件事做了一半。
    - **不回传明文**：`reset_password` 从此返回 `None`。此前这个端点的响应体里
      带着 `temporary_password`，而统一响应封装会把整份 payload 发回浏览器——
      规格禁的是「返回或记录明文密码」，这条正好两条都踩。

    审计记的是**目标账号 / 原因 / 结果**，不记密码（§16.6：审计明细不得保存密码
    或 Token）。`purpose` 是管理员填的原因，它进 `purpose` 那一列，与全站其余
    入口一致。
    """
    user = db.get(UserAccount, account_id)
    if not user:
        raise AppError("NOT_FOUND", "账号不存在", 404)
    reset_password(db, user, payload.temporary_password)
    revoked = revoke_all_sessions(db, user.id, "管理员重置密码")
    write_audit(
        db,
        action="重置密码",
        resource_type="USER_ACCOUNT",
        resource_id=str(user.id),
        actor=current_user,
        purpose=payload.purpose,
        request=request,
        detail=f"账号 {user.account}；撤销登录会话 {revoked} 个；要求下次登录修改密码",
    )
    db.commit()
    return ok({"must_change_password": True, "revoked_sessions": revoked})


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

