"""system setting table

Operator-editable configuration. The table starts empty on purpose: reads fall
back to DEFAULTS in app/services/settings_service.py, so an empty table behaves
exactly like the shipped defaults and only deviations are ever stored.

Revision ID: 0008_system_setting
Revises: 0007_role_permission
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_system_setting"
down_revision = "0007_role_permission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("user_account.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("namespace", "key", name="uq_setting_namespace_key"),
    )


def downgrade() -> None:
    op.drop_table("system_setting")
