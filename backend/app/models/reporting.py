from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProfessionalReport(Base):
    __tablename__ = "professional_report"
    __table_args__ = (
        UniqueConstraint("report_no", name="uq_professional_report_no"),
        Index("ix_professional_report_school_status", "school_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_no: Mapped[str] = mapped_column(String(64), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id", name="professional_report_fk_school"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False, default="PROFESSIONAL")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    task_scope_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    analysis_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="ALL_CALCULATED")
    statistics_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("user_account.id", name="professional_report_fk_created_by"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[int] = mapped_column(ForeignKey("user_account.id", name="professional_report_fk_updated_by"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id", name="professional_report_fk_published_by"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProfessionalReportVersion(Base):
    __tablename__ = "professional_report_version"
    __table_args__ = (UniqueConstraint("report_id", "version_no", name="uq_professional_report_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("professional_report.id", name="professional_report_version_fk_report"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    overall_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dimension_interpretation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sample_validity_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    support_plan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    statistics_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("user_account.id", name="professional_report_version_fk_created_by"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
