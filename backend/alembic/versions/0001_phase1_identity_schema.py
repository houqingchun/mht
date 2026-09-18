"""phase1 identity schema

Revision ID: 0001_phase1_identity
Revises:
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_phase1_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "school",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "grade",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("school.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "class_group",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("school.id"), nullable=False),
        sa.Column("grade_id", sa.Integer(), sa.ForeignKey("grade.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "student",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("student_no", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("masked_name", sa.String(64), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("school.id"), nullable=False),
        sa.Column("grade_id", sa.Integer(), sa.ForeignKey("grade.id"), nullable=False),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("class_group.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("school_id", "student_no", name="uq_student_school_no"),
    )
    op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account", sa.String(128), nullable=False),
        sa.Column("account_type", sa.Enum("STUDENT_NO", "MOBILE", "ADMIN_USERNAME", name="accounttype"), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_code", sa.Enum("STUDENT", "COUNSELOR", "LEADER", "ADMIN", name="rolecode"), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account", "account_type", name="uq_user_account_type"),
    )
    op.create_table(
        "user_scope",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("scope_type", sa.Enum("SCHOOL", "GRADE", "CLASS", "STUDENT", name="scopetype"), nullable=False),
        sa.Column("school_id", sa.Integer(), sa.ForeignKey("school.id"), nullable=True),
        sa.Column("grade_id", sa.Integer(), sa.ForeignKey("grade.id"), nullable=True),
        sa.Column("class_id", sa.Integer(), sa.ForeignKey("class_group.id"), nullable=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=True),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("actor_role", sa.String(32), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("purpose", sa.String(255), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("user_scope")
    op.drop_table("user_account")
    op.drop_table("student")
    op.drop_table("class_group")
    op.drop_table("grade")
    op.drop_table("school")
