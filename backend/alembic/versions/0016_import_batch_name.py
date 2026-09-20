"""给导入批次加「批次名称」

`assessment_import_batch` 上原来只有 `file_name`，而导入这条链路上一直有**两个**名字：

| 名字 | 回答 | 谁填 |
|---|---|---|
| 批次名称 | 这一批**叫什么** | 操作员在上传时填（界面上的那一格） |
| `file_name` | 这份数据**从哪个文件来** | 上传的文件自己的名字 |

它们不能合成一个。学校的导出文件叫 `结果(3).csv` 是常态，而批次名称会成为那场批次任务的
名字、出现在任务列表上给全校看——`_task_for_month` 拿 `file_name` 当任务名时，任务列表上
印的就是 `结果(3).csv`。

V1.0 那一版用的正是批次名称（`commit_assessment_import` 的 `batch.name`），而 V1.2 的
增量 DDL 在重建这张表时只保留了 `file_name`。所以这条迁移补回的是**一直在界面上、
一直有校验**（`_batch_errors` 至今要求它非空、不超 128 字）却无处落库的那一项，
不是新加一个概念。

`server_default=""` 是给**升级**用的：这条迁移跑在已经有批次的库上时（第 4 期之前的开发库
里可能有），新列必须先有一行空串才能 NOT NULL。空串在这里是诚实的「这一批没记名字」——
不拿 `file_name` 去填：那会把「不知道」变成一句看起来像事实的话，而这两列的分工正是
这条迁移要固定的东西。

`downgrade` 直接删列。它带走的只有名字，`file_name` 与其余全部列都不动——所以回退不会
让任何一批数据变得不可解释。

Revision ID: 0016_import_batch_name
Revises: 0015_calc_status_backfill
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_import_batch_name"
down_revision = "0015_calc_status_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_import_batch",
        sa.Column("batch_name", sa.String(length=128), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("assessment_import_batch", "batch_name")
