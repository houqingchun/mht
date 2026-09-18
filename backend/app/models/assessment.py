from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin


class AssessmentTask(TimestampMixin, Base):
    __tablename__ = "assessment_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scale_id: Mapped[int] = mapped_column(ForeignKey("assessment_scale.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    # 这份测评是从哪来的：IN_SYSTEM = 学生在本系统里作答，IMPORTED = 学校把外部平台的
    # 普查结果导入进来（数据中心 → MHT测评记录导入）。导入的批次任务也带这个标记，
    # 所以任务列表上能一眼看出某一批不是本系统测的。
    #
    # `server_default` 不能省（不只是 `default=`）：`tests/conftest.py` 用 metadata
    # `create_all` 建表，只写 `default=` 会让测试库与迁移库对同一列给出不同的 DDL
    # （CLAUDE.md 已知缺口 3：这种漂移没有任何测试看得见）。全库其他带默认值的
    # NOT NULL 列（本文件的 `assessment_target.status`、`models/common.py`）都是这么写的。
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="IN_SYSTEM", server_default="IN_SYSTEM"
    )


class AssessmentTarget(Base):
    __tablename__ = "assessment_target"
    __table_args__ = (UniqueConstraint("task_id", "student_id", name="uq_target_task_student"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("assessment_task.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssessmentSession(TimestampMixin, Base):
    __tablename__ = "assessment_session"
    __table_args__ = (UniqueConstraint("task_id", "student_id", name="uq_session_task_student"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_task.id"), nullable=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    scale_id: Mapped[int] = mapped_column(ForeignKey("assessment_scale.id"), nullable=False)
    scale_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How long the student took, measured from their FIRST ANSWER to submitting —
    # not from `started_at`, which is when the task was opened and so counts time
    # spent away from the page. Written once, at submit; NULL for rows that predate
    # this column and for sessions cleared by /reset.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IN_PROGRESS")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 同 `AssessmentTask.source`。导入的会话有两个后果，都在代码里挡住：
    # 学生不能再进这个答题会话（`create_or_get_session`），也不能 reset 它
    # （`reset_session`）——否则外部来源的答卷会被当成系统内作答呈现给学生。
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="IN_SYSTEM", server_default="IN_SYSTEM"
    )

    scale: Mapped["AssessmentScale"] = relationship()


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answer"
    __table_args__ = (UniqueConstraint("session_id", "question_id", name="uq_answer_session_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("scale_question.id"), nullable=False)
    answer: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[int] = mapped_column(nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AssessmentResult(Base):
    __tablename__ = "assessment_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False, unique=True)
    validity_score: Mapped[int] = mapped_column(nullable=False)
    validity_status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_score: Mapped[int] = mapped_column(nullable=False)
    total_level: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DimensionResult(Base):
    __tablename__ = "dimension_result"
    __table_args__ = (UniqueConstraint("session_id", "dimension_code", name="uq_dimension_session_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False)
    dimension_code: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_rule: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
