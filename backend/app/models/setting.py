from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SystemSetting(Base):
    """Operator-editable configuration, one row per (namespace, key).

    Stored per-key rather than one blob per namespace so the audit trail can
    name exactly what changed.

    The table is optional by design: anything absent falls back to
    `app/services/settings_service.py::DEFAULTS`, and a failed read falls back
    too. An empty table therefore behaves exactly like the shipped defaults —
    the same fail-safe pattern as the capability matrix.
    """

    __tablename__ = "system_setting"
    __table_args__ = (UniqueConstraint("namespace", "key", name="uq_setting_namespace_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
