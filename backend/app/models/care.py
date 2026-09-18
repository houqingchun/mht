from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class StudentCareCase(Base):
    __tablename__ = "student_care_case"
    # 这里原本有一个 `UniqueConstraint("student_id", "status")`，2026-09-17 去掉
    # （迁移 `0012`）。它把「一名学生一种状态只能有一条」当成了不变量，而**已关闭**的
    # 档案恰恰会累积：上学期关掉的是一次完整的关怀过程，这学期再出状况是一条新的过程，
    # 两条都该留着——这正是「关闭档案不得删除历史记录」那条约定要保住的东西。
    # 约束在库里表现为真：第二次关闭撞 UNIQUE，`db.flush()` 抛 IntegrityError，
    # 用户拿到 500，而这是学校每年都会遇到的时序（秋季关档、春季再关一次）。
    # 「同时只有一条在办」由 `assessment_service.open_or_reuse_care_case` 在写入侧保证
    # （先找非 CLOSED 的那条，找不到才新建），不靠数据库约束。

    id: Mapped[int] = mapped_column(primary_key=True)
    # `index=True` 与迁移 `0012` 配对：那条唯一索引本来兼作 `student_id` 外键的索引，
    # 删除它之前必须先建这条普通索引顶上（MySQL 会以 1553 拒绝删一条外键正在用的索引）。
    # 两边都写出来，模型与库才不会各说各话——而这件事**测试查不出来**
    # （内存 sqlite 由 `Base.metadata.create_all` 建表，完全不跑 Alembic）。
    # 默认名 `ix_student_care_case_student_id` 正好是迁移里用的那个。
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_REVIEW")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    close_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ManualReview(TimestampMixin, Base):
    __tablename__ = "manual_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_event_id: Mapped[int] = mapped_column(ForeignKey("risk_event.id"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    review_result: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_follow_up_date: Mapped[date | None] = mapped_column(Date(), nullable=True)


class FollowUpRecord(TimestampMixin, Base):
    __tablename__ = "follow_up_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False)
    next_follow_up_date: Mapped[date] = mapped_column(Date(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")


class FamilyContactRecord(TimestampMixin, Base):
    __tablename__ = "family_contact_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    contact_date: Mapped[date] = mapped_column(Date(), nullable=False)
    contact_person: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(128), nullable=False)
    support_status: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False)
    next_contact_date: Mapped[date | None] = mapped_column(Date(), nullable=True)


class RetestPlan(TimestampMixin, Base):
    __tablename__ = "retest_plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    source_session_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_session.id"), nullable=True)
    planned_date: Mapped[date] = mapped_column(Date(), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PLANNED")
    created_by: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    completed_session_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_session.id"), nullable=True)
