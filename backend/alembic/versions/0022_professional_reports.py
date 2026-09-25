"""professional report persistence"""
from alembic import op
import sqlalchemy as sa

revision = "0022_professional_reports"
down_revision = "0021_v2_task_governance"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("professional_report",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("report_no", sa.String(64), nullable=False),
        sa.Column("school_id", sa.Integer(), nullable=False), sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("task_scope_json", sa.JSON(), nullable=False), sa.Column("analysis_mode", sa.String(32), nullable=False),
        sa.Column("statistics_snapshot_json", sa.JSON(), nullable=False), sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("published_by", sa.Integer()), sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["school_id"], ["school.id"], name="professional_report_fk_school"),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], name="professional_report_fk_created_by"),
        sa.ForeignKeyConstraint(["updated_by"], ["user_account.id"], name="professional_report_fk_updated_by"),
        sa.ForeignKeyConstraint(["published_by"], ["user_account.id"], name="professional_report_fk_published_by"),
        sa.UniqueConstraint("report_no", name="uq_professional_report_no"))
    op.create_index("ix_professional_report_school_status", "professional_report", ["school_id", "status"])
    op.create_table("professional_report_version",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("overall_summary", sa.Text(), nullable=False),
        sa.Column("dimension_interpretation", sa.Text(), nullable=False), sa.Column("sample_validity_note", sa.Text(), nullable=False),
        sa.Column("support_plan", sa.Text(), nullable=False), sa.Column("statistics_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["professional_report.id"], name="professional_report_version_fk_report"),
        sa.ForeignKeyConstraint(["created_by"], ["user_account.id"], name="professional_report_version_fk_created_by"),
        sa.UniqueConstraint("report_id", "version_no", name="uq_professional_report_version"))


def downgrade():
    # 子表先走（professional_report_version 有指向 report 的外键）。
    #
    # **不许在 drop_table 之前先 drop_index**：`ix_professional_report_school_status`
    # 的第一列就是 `school_id`，而 `professional_report_fk_school` 正是拿它当索引用的。
    # MySQL 对「删一条外键正在使用的索引」回 1553（`Cannot drop index ...: needed in a
    # foreign key constraint`）——CLAUDE.md §1 里 0012 删那条唯一索引时踩的是同一个坑。
    # 删表本身会把它的外键与索引一并带走，那一行 `drop_index` 既多余又致命。
    # 实测：2026-09-25 在 xlp_upgrade_test（开发库的忠实克隆：36 张表 / 15716 行）上
    # `alembic downgrade 0020` 撞的就是这一句；而 MySQL 的 DDL 不在事务里，所以那次降级停在
    # 「版本表没了、版本戳还是 0022」的半截状态上。
    op.drop_table("professional_report_version")
    op.drop_table("professional_report")
