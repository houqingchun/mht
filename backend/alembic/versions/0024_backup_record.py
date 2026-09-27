"""backup_record：数据库备份的每一次尝试

V2.2 把「备份」从安装包里的 `备份数据.bat` 搬进应用管理端（CLAUDE.md §34）。
那枚按钮的失败方式是**静默**的——窗口一闪就关、失败原因随窗口一起没了——所以
新机制的第一件事就是**每一次尝试都留一行**，成功的与失败的都写。

## 为什么是一张新表，不是「扫一眼备份目录」

扫目录答得出「有哪几份备份」，答不出「失败过几次、为什么」。而自动备份恰恰是在
凌晨、没人看着、连窗口都没有的时候跑的：它的失败比那枚按钮更安静。把静默失败
搬到一个没有落点的新地方，等于什么都没做——这张表就是那个落点。

## 为什么没有 `started_at` / `finished_at`

这一行是在那次备份**结束时**写一次的，`created_at`（数据库墙钟）就是「备份时间」，
与导出中心那一列同源（`export_job` 也是这个形状）。多两个时间戳只是同一件事的三个
说法，而它们之间那一段（耗时）没有任何读者。

`status` 只有 `SUCCEEDED` / `FAILED` 两个值，**没有「进行中」**：中间态从来不落库
——手动那一路的并发由 `backup_service` 的模块级锁回答（拿不到锁回 409），而不是
写一行中间态再回来改它（那需要一套租约与回收，这个项目没有那一层，同 §23 对
`CALCULATING` 的处置）。

## `message` 一列担两种话

失败的原因（mysqldump 自己的 stderr——**那正是操作员「记不得」的那一句**），以及
「备份成功、但第二路径没拷成」的警告。由 `status` 分辨这两者。拆成两列会让其中
一列常年为空。

## 这一条迁移是 expand-only，没有 PRECHECKS

只建表，不动任何既有列，所以它在任何一份旧库上都**不可能失败**，不需要前置校验
（§21 那条判据：会失败的才归到带校验的那一类）。也因此它对
`upgrade_from_v1_0_0.sql` 与 `upgrade_from_v1_1_6.sql` **两份**渲染都自动生效——
**改完这一条迁移要跑 `make db-upgrade-sql`**，否则 `test_incremental_upgrade_sql.py`
变红。

`backend/sql/schema_mysql8.sql` 是**手写的快照**（§16），必须同步加上这张表：
它是父先子后次序里的最后一档（只指向 `user_account`），`test_sql_schema_matches_models.py`
与 `test_the_snapshot_file_builds_a_database_matching_the_models` 两处一起盯着它。

`purge.py` 与两份基线清理脚本**刻意不动**：备份记录不是「测评数据」，`purge-demo`
的职责是删演示的测评链路。顺带也让 `reset_to_baseline.sql` 那条「每一条 DELETE 都
挂在 `@admin_id` 上」的不变量不必为一个 `operator_id` 可为 NULL 的表开口子。
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_backup_record"
down_revision = "0023_report_version_publish"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "backup_record",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_sha256", sa.CHAR(64), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("operator_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["operator_id"], ["user_account.id"], name="backup_record_ibfk_1"
        ),
    )
    # 两条索引各自对应一个查询形状：`created_at` 是「最近几条，按时间倒序」（历史表
    # 与状态卡都读它），`(status, created_at)` 是「今天有没有成功过」那条去重判据。
    op.create_index("ix_backup_record_created_at", "backup_record", ["created_at"])
    op.create_index("ix_backup_record_status_created", "backup_record", ["status", "created_at"])


def downgrade():
    # 先删索引再删表：`backup_record_ibfk_1` 那个索引是外键自己建的，跟着表走。
    op.drop_index("ix_backup_record_status_created", table_name="backup_record")
    op.drop_index("ix_backup_record_created_at", table_name="backup_record")
    op.drop_table("backup_record")
