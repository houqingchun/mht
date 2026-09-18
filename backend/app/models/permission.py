from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RolePermission(Base):
    """Per-role capability grants shown in the admin permission matrix.

    `scope_level` is a descriptive code (NONE / SCOPED / SCHOOL / MANAGE / …)
    rather than a boolean, because the matrix the prototype displays carries
    meaning beyond allow/deny — "授权范围" and "学校范围" are both permits, but
    they scope the data differently.

    Enforcement only distinguishes NONE from everything else; the finer levels
    are display metadata for now. Rows are optional: a missing row falls back to
    the defaults in app/security/permissions.py.
    """

    __tablename__ = "role_permission"
    __table_args__ = (UniqueConstraint("role_code", "capability_key", name="uq_role_capability"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False)
    capability_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_level: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
