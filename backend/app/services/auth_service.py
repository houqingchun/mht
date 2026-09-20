import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.account import AuthSession, UserAccount, UserScope
from app.models.common import now_local_naive
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School
from app.schemas.auth import AccountScopeInput, LoginRequest
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token
from app.services.settings_service import get_namespace


ROLE_ACCOUNT_TYPE = {
    RoleCode.STUDENT: AccountType.STUDENT_NO,
    RoleCode.COUNSELOR: AccountType.MOBILE,
    RoleCode.LEADER: AccountType.MOBILE,
    RoleCode.ADMIN: AccountType.ADMIN_USERNAME,
}

#: 「账号与权限」里可以新建的角色。**学生不在这里，这是有意的。**
#:
#: 学生账号与名册行是一体的：`student_import_service` 在同一笔事务里写 `Student`、
#: 写 `UserAccount`、再补一条 `STUDENT` 范围行。单独建出来的学生账号没有学籍、
#: 没有班级，登录进去连自己那一场测评都看不到——那不是「少一个功能」，
#: 是造出一个坏账号，所以入口这一侧直接不收。
STAFF_ROLES = (RoleCode.COUNSELOR, RoleCode.LEADER, RoleCode.ADMIN)

#: 账号形状。`MOBILE` 那一支是登录页已经写明的承诺（「心理老师 · 手机号」），
#: 所以这里也照手机号校验：管理员少打一位数，那位老师此后永远登不上来，
#: 而系统不会因此报任何错——一句「请填 11 位手机号」比一个打不开的账号便宜得多。
_MOBILE_PATTERN = re.compile(r"^1\d{10}$")
_ADMIN_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")

#: 员工账号能配的范围。`STUDENT` 不在其中：那一条是学生账号自己的范围，
#: 由名册导入写，员工账号配上它等于把「哪几个学生归我管」当成手工配置。
SCOPE_INPUT_TYPES = {
    "SCHOOL": ScopeType.SCHOOL,
    "GRADE": ScopeType.GRADE,
    "CLASS": ScopeType.CLASS,
}


def db_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- 服务端会话（V1.2 §16.5） -------------------------------------------------
#
# 签发一个 token 与**记下这次会话**是同一件事的两个动作，所以它们住在同一个函数里
# （`authenticate`）：分开写就会出现一个只有 token 没有会话行的登录，而那种 token
# 撤销不掉——正是这一层要消灭的东西。
#
# 两个摘要长度是钉死的：`str(uuid.uuid4())` 恰好 36 字符 ↔ `CHAR(36)`，
# `sha256().hexdigest()` 恰好 64 字符 ↔ `CHAR(64)`。改动其中一个就要跟着改 DDL。


def session_token_hash(token: str) -> str:
    """令牌的摘要——**存摘要不存原文**。

    它与 `password_hash` 是同一条道理，只是没有加盐与迭代：这一列存在的意义是
    「这个请求带回来的 token 是不是这一行记的那一个」，而不是「原文是什么」。
    库被读走时，拿到摘要也换不来一个能用的 token。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _client_ip(request: Request | None) -> str | None:
    """尽量拿到真实来源。两个字段都按 `String(64)` / `String(255)` 截断——
    超长在 MySQL 严格模式上是一句 `1406 Data too long for column`，说得比
    「这次请求的头有异常」远（§18 那条「写入方与读取方对『空』的理解」的同类：
    超长也要由写入方保证）。
    """
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    raw = (forwarded.split(",")[0].strip() if forwarded else None) or (
        request.client.host if request.client else None
    )
    return raw[:64] if raw else None


def _client_user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    agent = request.headers.get("user-agent")
    return agent[:255] if agent else None


def create_auth_session(
    db: Session,
    user: UserAccount,
    *,
    token: str,
    jti: str,
    request: Request | None = None,
) -> AuthSession:
    """把这次登录记成一行可撤销的会话。

    `last_seen_at` **只在这里写一次**，不每请求更新。理由值得写下来，因为
    「最后活跃时间」这个词本身在邀请人每请求写一遍：本项目所有的读路径都
    `db.commit()` 不了（`get_db` 只在 `finally` 里 `close`），所以「顺手更新一下
    last_seen_at」实际要在一个纯粹读的请求里引入一次写事务——为一个展示用的列
    把每个 GET 都变成写，代价与收益不成比例。它今天的语义是「签发时刻」。
    """
    settings = get_settings()
    issued_at = now_local_naive()
    session = AuthSession(
        user_id=user.id,
        jti=jti,
        session_token_hash=session_token_hash(token),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=settings.access_token_expire_minutes),
        last_seen_at=issued_at,
        ip=_client_ip(request),
        user_agent=_client_user_agent(request),
    )
    db.add(session)
    db.flush()
    return session


def revoke_session(db: Session, session: AuthSession, reason: str) -> bool:
    """撤销一条会话。**已经撤销过的不再写第二次**——`revoked_at` 回答的是
    「它是什么时候失效的」，被后来的某次操作刷新一遍就等于把那个时刻改掉了。

    返回「这次是否真的改变了什么」，调用方据此决定写不写审计（§9 那条
    「被拒的读取不写审计」的同族：一次什么都没做的点击不该留一行轨迹）。
    """
    if session.revoked_at is not None:
        return False
    session.revoked_at = now_local_naive()
    session.revoked_reason = reason[:255]
    db.flush()
    return True


def revoke_all_sessions(
    db: Session,
    user_id: int,
    reason: str,
    *,
    except_session_id: int | None = None,
) -> int:
    """把一个账号的会话整批撤销，返回撤销了几条。

    `except_session_id` 是给「改自己的密码」用的：那一次要把别的设备踢下线，
    但当前这台机器上的自己得留着，否则用户改完密码当场被登出。
    管理员重置别人的密码时不传它——那一次是**全部**撤销（§16.5 逐字要求）。
    """
    conditions = [AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)]
    if except_session_id is not None:
        conditions.append(AuthSession.id != except_session_id)
    sessions = db.scalars(select(AuthSession).where(*conditions)).all()
    now = now_local_naive()
    for session in sessions:
        session.revoked_at = now
        session.revoked_reason = reason[:255]
    if sessions:
        db.flush()
    return len(sessions)


def list_sessions(db: Session, user_id: int) -> list[AuthSession]:
    """一个账号的会话，新→旧。**按 `id` 倒序，不按 `issued_at`**：
    截断到整秒的墙上时钟在同一秒内可以并列（§20 那条生产改动的另一面），
    而 `id` 是单调的，同一秒内签发的两条会话也能排出确定的先后。
    """
    return list(
        db.scalars(
            select(AuthSession).where(AuthSession.user_id == user_id).order_by(AuthSession.id.desc())
        ).all()
    )


def effective_session_status(session: AuthSession, *, now: datetime | None = None) -> str:
    """会话状态**现算**，与 `task_service.effective_task_status` 同一口径（§12）：
    库里只存「撤销」这一个真实发生过的动作，过期由 `expires_at` 推出来。

    把 `EXPIRED` 写成一列的话，它会在没有任何人碰它的情况下变陈旧——一条
    昨天还活跃的会话今天仍然是 `ACTIVE`，直到某个后台任务想起来去改它。
    本项目没有后台任务，所以那个字面量永远等不到人来改。
    """
    if session.revoked_at is not None:
        return "REVOKED"
    if session.expires_at <= (now or now_local_naive()):
        return "EXPIRED"
    return "ACTIVE"


def serialize_sessions(db: Session, user_id: int, *, current_session_id: int | None = None) -> list[dict]:
    """一次算好 `now` 再逐行序列化。

    逐个 `serialize_session` 各自取时间是看不出来的错：一屏会话里两条的状态
    由两个相差几毫秒的时刻判定，边界上会出现「上面那条已过期、下面那条还活跃，
    而它们签发时间一样」。一条 SELECT 取出来的一次判断没有这个问题。
    """
    now = now_local_naive()
    return [
        serialize_session(row, current_session_id=current_session_id, now=now)
        for row in list_sessions(db, user_id)
    ]


def serialize_session(
    session: AuthSession,
    *,
    current_session_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    """`is_current` 由服务端算，不让前端猜。

    前端没有别的办法知道「这一行就是我自己」——它能拿到的只有「我现在用的这个
    token」，而 token 的摘要在响应里（也不该在）。猜错的后果是用户把自己踢下线，
    然后在登录页上想不明白刚才点的是什么。
    """
    return {
        "id": session.id,
        "token_type": session.token_type,
        "status": effective_session_status(session, now=now),
        "issued_at": session.issued_at,
        "expires_at": session.expires_at,
        "last_seen_at": session.last_seen_at,
        "revoked_at": session.revoked_at,
        "revoked_reason": session.revoked_reason,
        "ip": session.ip,
        "user_agent": session.user_agent,
        "is_current": session.id == current_session_id,
    }


def authenticate(
    db: Session,
    payload: LoginRequest,
    request: Request | None = None,
) -> tuple[UserAccount, str]:
    role = RoleCode(payload.role)
    account_type = ROLE_ACCOUNT_TYPE[role]
    user = db.scalar(
        select(UserAccount).where(
            UserAccount.account == payload.account,
            UserAccount.account_type == account_type,
            UserAccount.role_code == role,
        )
    )
    if not user or not user.active:
        raise AppError("AUTH_INVALID", "账号、角色或密码不正确", 401)

    if user.locked_until and user.locked_until > db_utc_now():
        raise AppError("ACCOUNT_LOCKED", "账号已锁定，请稍后再试", 423)

    if not verify_password(payload.password, user.password_hash):
        settings = get_settings()
        user.failed_attempts += 1
        if user.failed_attempts >= settings.password_lock_threshold:
            user.locked_until = db_utc_now() + timedelta(minutes=settings.password_lock_minutes)
        db.flush()
        raise AppError("AUTH_INVALID", "账号、角色或密码不正确", 401)

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = db_utc_now()
    # ★ 这一行不是可有可无的，它挡的是一次**实测到过**的死锁（2026-09-19，V1.2 阶段 8）。
    #
    # `auth_session_ibfk_1` 指着 `user_account`，而 InnoDB 在**插入子行时会先在父行上
    # 取一把共享锁**做外键检查。下面 `create_auth_session` 里的那次 `db.flush()` 会把
    # 「UPDATE user_account」与「INSERT auth_session」放进**同一个 flush**，而
    # SQLAlchemy 的 unit of work 在两张**只有表级外键、没有 `relationship()`** 的表
    # 之间**不保证**先发 UPDATE——它没有那条依赖边可排。顺序一旦反过来，两个并发登录
    # 同一个账号的请求就各持一把锁互等：
    #
    #   T1 INSERT auth_session（拿到父行 S 锁）→ T1 UPDATE user_account（要 X 锁，等）
    #   T2 INSERT auth_session（S 锁与 S 锁兼容，拿到）→ T2 UPDATE user_account（等）
    #
    # → MySQL **1213 Deadlock**，用户拿到 500。它只在「同一个账号被同时登录」时出现，
    # 而那不是罕见场景：5 个并发的 e2e worker 全用同几个种子账号，全量跑一遍必现一次
    # （后端日志里 `/auth/login` 的那几个 500 就是它）；真实学校里管理员账号被两台机器
    # 同时登录也是常事。
    #
    # **方向是量出来的，不是推出来的**：探针（独立跑三遍）里把这一行去掉，SQLAlchemy
    # **每次都先发 INSERT、再发 UPDATE**——它在这两个 mapper 之间没有依赖边，次序就是
    # 注册次序，而 `AuthSession` 恰好排在 `UserAccount` 前面。所以这不是「有可能反过来」，
    # 是**当时就是反的**；`test_auth_sessions.py` 里那条断言次序的用例把它钉住。
    #
    # 这一行让父行的 X 锁**先**拿到，后面子行插入取的 S 锁是同一个事务自己已经持有的
    # 那把，于是**每一次的顺序都一样**。与 §27 `USE_EXTERNAL` 那次「先让在线那一场退位、
    # 再建新场」（中间那次 `db.flush()` 同样是这次序的执行形式）是同一条教训：
    # **两次写入之间有先后要求时，顺序要由书写者保证，不能指望 unit of work。**
    db.flush()
    jti = str(uuid4())
    token = create_access_token(user.id, user.role_code.value, jti)
    create_auth_session(db, user, token=token, jti=jti, request=request)
    return user, token


def _school_display_names(db: Session) -> dict[int, str]:
    """`school.id → 界面上要显示的校名`。

    **校名有两个家，这里必须选对的那一个**（2026-09-17 用户报的 bug）：

    | 家 | 谁写的 | 界面上出现过吗 |
    |---|---|---|
    | `school.name` | `db/seed.py`、学生导入（`school_for_import`，缺口 2） | **从来没有** |
    | `system_setting` 的 `org.school_name` | 管理员在「账号与权限 → 系统配置」里改 | 登录页、顶栏、正是用户改的那一格 |

    两处会不一致——用户把校名改成「第十三中学」，而这一页的范围写着「青禾实验学校」，
    因为此前读的是前者。用户看得见、改得动的只有后者，所以范围这一列也读后者：
    同一个东西在一屏上不能有两个名字（§9 那条「口径要写进界面」的同一条道理）。

    **不是**把配置值写回 `school.name`：那一行是种子/导入建的，被 `student.school_id`、
    `grade.school_id` 引用着，而且**没有任何界面在编辑它**——把用户改的校名写进去，
    等于让一个只读列去追一个可写配置，下次改配置又要再写一次。

    多校部署时这里得回落到「每所学校各自的显示名」，那时 `school.name` 才真正成为
    该读的那一侧（缺口 2 记着单校写死的那几处）。查不到配置就回落到 `school.name`，
    绝不返回空校名——空串会渲染成「全校 · 」，看起来像一个真的名字。
    """
    configured = (get_namespace(db, "org").get("school_name") or "").strip()
    return {
        row.id: (configured or row.name) for row in db.scalars(select(School)).all()
    }


def resolve_scope_names(db: Session) -> dict[str, dict[int, str]]:
    """`id → 名称` 的三张小表，给范围行拼可读的一行字用。

    学校 / 年级 / 班级三张表都很小（一所学校加起来几十行），所以整表读进来、
    在内存里查。**不是逐行回查**：账号列表一页 10 行，每行两三条范围，
    逐个 `db.get()` 就是几十次查询，而这里只要三次。

    前端拿的是**名称**而不是拼好的中文（`labels.ts` 是编码 → 中文的唯一映射层，
    §3）：范围类型那两个字归前端，这里只负责回答「id 7 叫什么」。
    """
    return {
        # 校名走配置，不读 `school.name`——见 `_school_display_names`。
        "SCHOOL": _school_display_names(db),
        "GRADE": {row.id: row.name for row in db.scalars(select(Grade)).all()},
        "CLASS": {row.id: row.name for row in db.scalars(select(ClassGroup)).all()},
    }


def _scope_name(names: dict[str, dict[int, str]], scope: UserScope) -> str | None:
    if scope.scope_type == ScopeType.SCHOOL:
        return names["SCHOOL"].get(scope.school_id)
    if scope.scope_type == ScopeType.GRADE:
        return names["GRADE"].get(scope.grade_id)
    if scope.scope_type == ScopeType.CLASS:
        return names["CLASS"].get(scope.class_id)
    # STUDENT 范围没有可拼的名字：它是隐私，而这一列只是给管理员看组织归属。
    return None


def serialize_user(
    db: Session,
    user: UserAccount,
    scope_names: dict[str, dict[int, str]] | None = None,
) -> dict:
    scopes = db.scalars(select(UserScope).where(UserScope.user_id == user.id)).all()
    names = scope_names if scope_names is not None else resolve_scope_names(db)
    return {
        "id": user.id,
        "account": user.account,
        "account_type": user.account_type.value,
        "display_name": user.display_name,
        "role_code": user.role_code.value,
        "must_change_password": user.must_change_password,
        "active": user.active,
        "scopes": [
            {
                "scope_type": scope.scope_type.value,
                "school_id": scope.school_id,
                "grade_id": scope.grade_id,
                "class_id": scope.class_id,
                "student_id": scope.student_id,
                # 名称是可读的补充，id 仍然是事实来源。查不到名称时给 `None`
                # 而不是空串：空串会渲染成「班级 · 」，看起来像一个真的班级。
                "name": _scope_name(names, scope),
            }
            for scope in scopes
        ],
    }


def reset_password(db: Session, user: UserAccount, new_password: str) -> None:
    """重置密码：写哈希、置「下次登录必须改」、顺手解锁。

    **返回 `None`，不返回明文密码，是有意的**（§16.5：重置密码「不返回或记录明文
    密码」）。此前这里是 `new_password = temporary_password or token_urlsafe(9)`，
    也就是说服务端在没人给密码时自己生成一个——而自己生成就必须回传，否则没人知道
    它是什么。那两件事凑在一起与规格直接冲突，所以去掉的是生成那一半：密码由操作员
    写进请求体（他自己的浏览器里，不经过日志、不经过响应）。

    调用方仍全部显式传值（界面表单、安装脚本、测试），所以这一改动不动任何既有行为。
    """
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.failed_attempts = 0
    user.locked_until = None
    # 与 `authenticate` 里那一行同一个理由（那里写着完整的推导）：父行的 UPDATE 要
    # **先于**任何引用它的子行写入发出去。这里后面跟着的是 `revoke_all_sessions`
    # （UPDATE `auth_session`）与审计（INSERT `audit_log`，`audit_log_ibfk_1` 也指着
    # `user_account`）。
    #
    # **这一行与登录那一行的处境不同，区别要写清楚**：探针量过（2026-09-19），这两条
    # 路上的次序**今天恰好是对的**——`AuditLog` 这个 mapper 排在 `UserAccount` 后面，
    # 而登录那条路上 `AuthSession` 排在它**前面**，于是同一个机制在那里就出事了。
    # 也就是说这里靠的是**注册次序**这个没人保证的巧合，而不是一条判据。
    # 这一行把它变成明写的顺序：代价是一次极罕见的往返，收益是这条路上不再有
    # 「次序对不对取决于谁先被 import」这种东西。它**没有守卫**（今天就复现不出来），
    # 这条注释就是它存在的全部记录。
    db.flush()


# --- 账号的创建与维护（2026-09-17 补） ---------------------------------------
#
# 在此之前系统里**没有任何创建账号的接口**：心理老师与德育领导的账号只能来自
# `db/seed.py`，学生账号来自名册导入。于是一所学校拿到这套系统之后，加不了第二位
# 心理老师——用户报的正是这一条（「心理老师、德育领导没有添加人员的入口」）。
#
# 三件事在这一层定死，因为它们共用同一个约束（`security/data_scope.py` 的
# fail-closed 语义）：
#
# 1. **范围必须一起写**。没有 `user_scope` 行的账号登录得进来，却看不到任何学生
#    ——谓词在「用户没有范围行」时返回恒假。所以创建与编辑都把范围当必填项，
#    而不是「以后再配」。创建出一个看不到人的账号不是漏配，是造了一个坏账号。
# 2. **临时密码由操作员给**，与「重置密码」同一形状：不给默认值，落库即
#    `must_change_password = True`，首次登录必须自己改。
# 3. **学生不在这里建**（`STAFF_ROLES`）。


def _validate_account_shape(account_type: AccountType, account: str) -> None:
    if not account:
        raise AppError("VALIDATION_ERROR", "登录账号不能为空", 422)
    if account_type == AccountType.MOBILE and not _MOBILE_PATTERN.match(account):
        raise AppError(
            "VALIDATION_ERROR",
            "心理老师与德育领导用手机号登录，请填 11 位手机号（如 13800000001）",
            422,
        )
    if account_type == AccountType.ADMIN_USERNAME and not _ADMIN_USERNAME_PATTERN.match(account):
        raise AppError(
            "VALIDATION_ERROR",
            "管理员账号请用 3–32 位字母、数字、下划线、点或短横线（如 admin2）",
            422,
        )


def _duplicate_message(account_type: AccountType, account: str) -> str:
    if account_type == AccountType.MOBILE:
        # 唯一约束是 (account, account_type)，心理老师与德育领导共用 MOBILE 那一个
        # 命名空间，所以这里说的是「手机号」而不是「心理老师账号」。
        return f"手机号 {account} 已经有一个员工账号在用了（同一手机号只能有一个）"
    return f"管理员账号 {account} 已存在"


def _resolve_scope(db: Session, item: AccountScopeInput) -> UserScope:
    """把一条「范围选择」变成一行 `user_scope`，四个维度按类型只填自己那一列。

    school_id 在 GRADE / CLASS 两支里一并填上，是**信息**不是判据：
    `student_scope_predicate` 按类型只看自己那一列（§9），所以多填的那一列不会
    收紧或放宽匹配；它的用处是让「这个账号属于哪所学校」在库里答得出来。
    """
    scope_type = SCOPE_INPUT_TYPES.get(item.scope_type)
    if scope_type is None:
        raise AppError(
            "VALIDATION_ERROR",
            f"未知的数据范围：{item.scope_type}（可选：全校 / 年级 / 班级）",
            422,
        )
    missing = "选择的数据范围已经不存在了，请重新打开这一页"
    if scope_type == ScopeType.SCHOOL:
        school = db.get(School, item.scope_id)
        if not school:
            raise AppError("VALIDATION_ERROR", missing, 422)
        return UserScope(scope_type=scope_type, school_id=school.id)
    if scope_type == ScopeType.GRADE:
        grade = db.get(Grade, item.scope_id)
        if not grade:
            raise AppError("VALIDATION_ERROR", missing, 422)
        return UserScope(scope_type=scope_type, school_id=grade.school_id, grade_id=grade.id)
    class_group = db.get(ClassGroup, item.scope_id)
    if not class_group:
        raise AppError("VALIDATION_ERROR", missing, 422)
    return UserScope(
        scope_type=scope_type,
        school_id=class_group.school_id,
        grade_id=class_group.grade_id,
        class_id=class_group.id,
    )


def scope_options(db: Session) -> list[dict]:
    """「新建账号」那一格能选的范围：全校 / 各年级 / 各班级。

    选项自带 `scope_id`，所以创建时不需要再去猜「是哪所学校」——那个猜法在单校
    部署里是 `School.code == "QH"`（缺口 2 的两处硬编码），多校部署要先改。
    把学校本身做成一个选项之后，这条链路上没有第三种写死的地方。

    校名走 `_school_display_names`（即配置里的 `org.school_name`），不是
    `school.name`：这一格与账号列表的范围列、登录页、顶栏必须是同一个名字。
    """
    display_names = _school_display_names(db)
    items = [
        {
            "scope_type": "SCHOOL",
            "scope_id": school.id,
            "name": display_names.get(school.id, school.name),
        }
        for school in db.scalars(select(School).order_by(School.id)).all()
    ]
    items += [
        {"scope_type": "GRADE", "scope_id": grade.id, "name": grade.name}
        for grade in db.scalars(select(Grade).order_by(Grade.sort_order, Grade.id)).all()
    ]
    items += [
        {"scope_type": "CLASS", "scope_id": class_group.id, "name": class_group.name}
        for class_group in db.scalars(
            select(ClassGroup)
            .join(Grade, Grade.id == ClassGroup.grade_id)
            .order_by(Grade.sort_order, Grade.id, ClassGroup.name)
        ).all()
    ]
    return items


def create_account(
    db: Session,
    *,
    role_code: str,
    display_name: str,
    account: str,
    temporary_password: str,
    scopes: list[AccountScopeInput],
) -> UserAccount:
    try:
        role = RoleCode(role_code)
    except ValueError:
        raise AppError("VALIDATION_ERROR", f"未知的角色：{role_code}", 422)
    if role not in STAFF_ROLES:
        raise AppError(
            "VALIDATION_ERROR",
            "学生账号不在这里创建：学生跟着名册一起生成（组织学生 → 学生信息导入），"
            "在那里他才有学号、班级和测评记录。",
            422,
        )

    account_type = ROLE_ACCOUNT_TYPE[role]
    account = account.strip()
    display_name = display_name.strip()
    if not display_name:
        raise AppError("VALIDATION_ERROR", "姓名不能为空", 422)
    _validate_account_shape(account_type, account)
    exists = db.scalar(
        select(UserAccount).where(
            UserAccount.account == account, UserAccount.account_type == account_type
        )
    )
    if exists:
        raise AppError("VALIDATION_ERROR", _duplicate_message(account_type, account), 422)

    # 范围先解析再落账号：范围填错时不该留下一个没有范围的半成品账号。
    rows = [_resolve_scope(db, item) for item in scopes]

    user = UserAccount(
        account=account,
        account_type=account_type,
        display_name=display_name,
        password_hash=hash_password(temporary_password),
        role_code=role,
        # 操作员给的临时密码，所以第一次登录必须改掉——与 `reset_password` 同一约定。
        must_change_password=True,
        active=True,
    )
    db.add(user)
    db.flush()
    for row in rows:
        row.user_id = user.id
        db.add(row)
    db.flush()
    return user


def _scope_signature(rows: list[UserScope]) -> set[tuple]:
    return {(row.scope_type, row.school_id, row.grade_id, row.class_id) for row in rows}


def update_account(
    db: Session,
    user: UserAccount,
    *,
    display_name: str | None = None,
    scopes: list[AccountScopeInput] | None = None,
    active: bool | None = None,
) -> list[str]:
    """改姓名 / 换范围 / 停用启用。返回**实际动过的字段**，供调用方写审计。

    角色与账号本身不可改：`authenticate` 是按「账号 + 账号类型 + 角色」三样一起匹配的
    （`ROLE_ACCOUNT_TYPE` 把角色映射成账号类型），所以改角色等于改这个账号的身份，
    而 `admin_username` 与 `mobile` 两个命名空间之间的迁移没有任何可以沿用的东西。
    录错了角色就停用那一个、另建一个——那一步会留下两条审计，比一条静默改写的轨迹
    更接近发生过的事。
    """
    changed: list[str] = []

    if display_name is not None:
        cleaned = display_name.strip()
        if not cleaned:
            raise AppError("VALIDATION_ERROR", "姓名不能为空", 422)
        if cleaned != user.display_name:
            user.display_name = cleaned
            changed.append("display_name")

    if active is not None and active != user.active:
        # `get_current_user` 每次请求都查 `active`，所以停用**立即**生效：
        # 那个账号已经拿到手的 token 下一个请求就会被拒。
        user.active = active
        # 停用顺手清掉锁定计数：重新启用时不该让他先等完上一轮的锁定期。
        if not active:
            user.failed_attempts = 0
            user.locked_until = None
        changed.append("active")

    if scopes is not None:
        rows = [_resolve_scope(db, item) for item in scopes]
        existing = db.scalars(select(UserScope).where(UserScope.user_id == user.id)).all()
        # 逐行比对而不是「收到就记一笔」：前端只在操作员真的动过那一格时才会发 scopes，
        # 但接口本身收得下「原样发回来」——那种请求不该在审计里留下一次没有发生的修改。
        if _scope_signature(rows) != _scope_signature(list(existing)):
            # `user_scope` 是叶子表（没有任何表引用它），所以替换是安全的：
            # 全库没有 `ondelete=`（§1），删父行会 1451，但这里删的是子行。
            for row in existing:
                db.delete(row)
            db.flush()
            for row in rows:
                row.user_id = user.id
                db.add(row)
            changed.append("scopes")

    db.flush()
    return changed


def scope_audit_detail(db: Session, user: UserAccount) -> str:
    """审计 `detail` 里的范围摘要。用编码，不用中文。

    与 `api/v1/auth.py` 的「更新权限配置」同一形状（那里写的是
    `role:capability=level`）：审计的 `detail` 是**机器可读的那一份**，
    界面上怎么写中文是 `labels.ts` 的事（§3）。密码永远不进这里。
    """
    scopes = db.scalars(
        select(UserScope).where(UserScope.user_id == user.id).order_by(UserScope.id)
    ).all()
    parts = []
    for scope in scopes:
        if scope.scope_type == ScopeType.SCHOOL:
            parts.append(f"SCHOOL={scope.school_id}")
        elif scope.scope_type == ScopeType.GRADE:
            parts.append(f"GRADE={scope.grade_id}")
        elif scope.scope_type == ScopeType.CLASS:
            parts.append(f"CLASS={scope.class_id}")
        else:
            parts.append(f"{scope.scope_type.value}={scope.student_id}")
    return f"role={user.role_code.value}; scopes={','.join(parts) or '无'}"
