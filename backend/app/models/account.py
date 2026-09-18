from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint
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

