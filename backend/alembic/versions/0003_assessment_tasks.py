"""assessment tasks

Revision ID: 0003_assessment_tasks
Revises: 0002_scale_engine
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_assessment_tasks"
down_revision = "0002_scale_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_no", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("scale_id", sa.Integer(), sa.ForeignKey("assessment_scale.id"), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("school.id"), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "assessment_target",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("assessment_task.id"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="NOT_STARTED"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", "student_id", name="uq_target_task_student"),
    )
    op.create_foreign_key(
        "fk_session_task",
        "assessment_session",
        "assessment_task",
        ["task_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_session_task", "assessment_session", type_="foreignkey")
    op.drop_table("assessment_target")
    op.drop_table("assessment_task")
