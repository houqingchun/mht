from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin
from app.models.enums import AccountType, RoleCode, ScopeType


class UserAccount(TimestampMixin, Base):
    __tablename__ = "user_account"
    __table_args__ = (UniqueConstraint("account", "account_type", name="uq_user_account_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(128), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_code: Mapped[RoleCode] = mapped_column(Enum(RoleCode), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserScope(Base):
    __tablename__ = "user_scope"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    scope_type: Mapped[ScopeType] = mapped_column(Enum(ScopeType), nullable=False)
    school_id: Mapped[int | None] = mapped_column(ForeignKey("school.id"), nullable=True)
    grade_id: Mapped[int | None] = mapped_column(ForeignKey("grade.id"), nullable=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("class_group.id"), nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("student.id"), nullable=True)
    # `scope_type` 与非空列必须一一对应（SCHOOL 只允许 school_id、CLASS 只允许 class_id…）。
    # DDL 第 751-753 行明确说这件事**不能靠 CHECK**：项目要兼容 MySQL 8.0.13，
    # 而 CHECK 约束在 8.0.16 才真正生效——一句在旧版本上被静默忽略的 CHECK 比没有更糟。
    # 所以这条判据留在服务层（`auth_service` 的两处校验），本阶段只对齐结构。


class AuthSession(Base):
    """服务端的会话 / 撤销状态（V1.2 增量 DDL 第五节）。

    JWT 仍然是访问令牌，但**服务端留着这一行**：没有它，登出只是一个客户端动作
    （把 token 丢掉），被偷走的那个 token 照样用到过期——强制下线这件事根本做不到。
    `jti` 是 JWT 自身的 id，`session_token_hash` 是令牌的摘要；
    `revoked_at` 一旦写上就**立刻生效**：判据在 `api/deps.py::get_auth_context`，
    它挡在**每一个**鉴权端点前面（比 §16.5 说的「敏感接口」更严一档，理由写在
    那个函数里）。所以「撤销」不需要等 token 过期，与「停用账号」同一层。

    **历史令牌全部失效**：DDL 第 788 行明说「为已有用户创建 auth_session 不需要」。
    这不是省事——这一版的 token 里没有 `jti`，凭空给它们造一行等于把一批
    无从核对的会话说成合法的。
    """

    __tablename__ = "auth_session"
    __table_args__ = (
        UniqueConstraint("jti", name="uq_auth_session_jti"),
        UniqueConstraint("session_token_hash", name="uq_auth_session_token_hash"),
        Index("user_id", "user_id"),
        # 这两条索引不是「顺手加的性能优化」，是**两个保留期的查询入口**：
        # 过期的会话（`expires_at`）与已撤销的会话（`revoked_at`）各要能被扫出来清理。
        Index("expires_at", "expires_at"),
        Index("revoked_at", "revoked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 索引名是 `user_id`（列名），不是 SQLAlchemy 默认的 `ix_auth_session_user_id`：
    # DDL 里显式写的就是 `KEY user_id (user_id)`，外键复用了它。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", name="auth_session_ibfk_1"), nullable=False
    )
    jti: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    token_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ACCESS", server_default="ACCESS")
    session_token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    # `issued_at` / `expires_at` 是 Python 写的（与 `assessment_session.started_at` 同一族），
    # 所以没有 `server_default`——只有 `created_at` 是数据库写的。见 `models/common.py`。
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

