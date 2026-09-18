"""role permission matrix

Creates `role_permission` for the admin capability matrix. The table starts
empty on purpose: `app/security/permissions.py` falls back to
CAPABILITY_DEFAULTS, which reproduce the behaviour the code enforced before
this table existed. Seeding rows is only necessary once an operator changes
something from the default.

Revision ID: 0007_role_permission
Revises: 0006_audit_student_id
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_role_permission"
down_revision = "0006_audit_student_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_permission",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role_code", sa.String(32), nullable=False),
        sa.Column("capability_key", sa.String(64), nullable=False),
        sa.Column("scope_level", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("role_code", "capability_key", name="uq_role_capability"),
    )


def downgrade() -> None:
    op.drop_table("role_permission")
