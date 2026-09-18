"""care case closure

Revision ID: 0005_care_case_closure
Revises: 0004_care_cases
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_care_case_closure"
down_revision = "0004_care_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "family_contact_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("contact_date", sa.Date(), nullable=False),
        sa.Column("contact_person", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("result", sa.String(128), nullable=False),
        sa.Column("support_status", sa.String(128), nullable=False),
        sa.Column("confirmed_facts", sa.Text(), nullable=False),
        sa.Column("next_contact_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "retest_plan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("source_session_id", sa.Integer(), sa.ForeignKey("assessment_session.id"), nullable=True),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PLANNED"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("completed_session_id", sa.Integer(), sa.ForeignKey("assessment_session.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("retest_plan")
    op.drop_table("family_contact_record")
