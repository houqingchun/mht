"""V2.0.0 P0：测评任务与筛查信号作废元数据。

Revision ID: 0021_v2_task_governance
Revises: 0020_total_excludes_validity
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_v2_task_governance"
down_revision = "0020_total_excludes_validity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assessment_task", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessment_task", sa.Column("voided_by", sa.Integer(), nullable=True))
    op.add_column("assessment_task", sa.Column("void_reason", sa.String(500), nullable=True))
    op.create_foreign_key("assessment_task_fk_voided_by", "assessment_task", "user_account", ["voided_by"], ["id"])
    op.add_column("risk_event", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("risk_event", sa.Column("voided_by", sa.Integer(), nullable=True))
    op.add_column("risk_event", sa.Column("void_reason", sa.String(500), nullable=True))
    op.create_foreign_key("risk_event_fk_voided_by", "risk_event", "user_account", ["voided_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("risk_event_fk_voided_by", "risk_event", type_="foreignkey")
    op.drop_column("risk_event", "void_reason")
    op.drop_column("risk_event", "voided_by")
    op.drop_column("risk_event", "voided_at")
    op.drop_constraint("assessment_task_fk_voided_by", "assessment_task", type_="foreignkey")
    op.drop_column("assessment_task", "void_reason")
    op.drop_column("assessment_task", "voided_by")
    op.drop_column("assessment_task", "voided_at")
