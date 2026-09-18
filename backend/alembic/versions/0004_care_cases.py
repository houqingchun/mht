"""care cases

Revision ID: 0004_care_cases
Revises: 0003_assessment_tasks
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_care_cases"
down_revision = "0003_assessment_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_care_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(128), nullable=True),
        sa.Column("close_note", sa.Text(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("student_id", "status", name="uq_care_case_student_status"),
    )
    op.create_table(
        "manual_review",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_event_id", sa.Integer(), sa.ForeignKey("risk_event.id"), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("review_result", sa.String(128), nullable=False),
        sa.Column("confirmed_facts", sa.Text(), nullable=False),
        sa.Column("next_action", sa.String(128), nullable=True),
        sa.Column("next_follow_up_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "follow_up_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("record_type", sa.String(64), nullable=False),
        sa.Column("confirmed_facts", sa.Text(), nullable=False),
        sa.Column("next_follow_up_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("follow_up_record")
    op.drop_table("manual_review")
    op.drop_table("student_care_case")
