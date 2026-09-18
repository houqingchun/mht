"""scale engine schema

Revision ID: 0002_scale_engine
Revises: 0001_phase1_identity
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_scale_engine"
down_revision = "0001_phase1_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_scale",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", "version", name="uq_scale_code_version"),
    )
    op.create_table(
        "scale_question",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scale_id", sa.Integer(), sa.ForeignKey("assessment_scale.id"), nullable=False),
        sa.Column("question_no", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("dimension_code", sa.String(64), nullable=True),
        sa.Column("is_validity_question", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_key_question", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.UniqueConstraint("scale_id", "question_no", name="uq_scale_question_no"),
    )
    op.create_table(
        "scale_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scale_id", sa.Integer(), sa.ForeignKey("assessment_scale.id"), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("rule_type", sa.String(64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "assessment_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("scale_id", sa.Integer(), sa.ForeignKey("assessment_scale.id"), nullable=False),
        sa.Column("scale_version", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "student_id", name="uq_session_task_student"),
    )
    op.create_table(
        "assessment_answer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("assessment_session.id"), nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("scale_question.id"), nullable=False),
        sa.Column("answer", sa.String(8), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("session_id", "question_id", name="uq_answer_session_question"),
    )
    op.create_table(
        "assessment_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("assessment_session.id"), nullable=False, unique=True),
        sa.Column("validity_score", sa.Integer(), nullable=False),
        sa.Column("validity_status", sa.String(32), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("total_level", sa.String(32), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "dimension_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("assessment_session.id"), nullable=False),
        sa.Column("dimension_code", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("interpretation", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.UniqueConstraint("session_id", "dimension_code", name="uq_dimension_session_code"),
    )
    op.create_table(
        "risk_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("assessment_session.id"), nullable=False),
        sa.Column("risk_type", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(64), nullable=False),
        sa.Column("trigger_rule", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("risk_event")
    op.drop_table("dimension_result")
    op.drop_table("assessment_result")
    op.drop_table("assessment_answer")
    op.drop_table("assessment_session")
    op.drop_table("scale_rule")
    op.drop_table("scale_question")
    op.drop_table("assessment_scale")
