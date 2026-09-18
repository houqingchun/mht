from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class AssessmentScale(Base):
    __tablename__ = "assessment_scale"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_scale_code_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ScaleQuestion(Base):
    __tablename__ = "scale_question"
    __table_args__ = (UniqueConstraint("scale_id", "question_no", name="uq_scale_question_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scale_id: Mapped[int] = mapped_column(ForeignKey("assessment_scale.id"), nullable=False)
    question_no: Mapped[int] = mapped_column(nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    dimension_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_validity_question: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_key_question: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")


class ScaleRule(TimestampMixin, Base):
    __tablename__ = "scale_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    scale_id: Mapped[int] = mapped_column(ForeignKey("assessment_scale.id"), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
