"""给导入行加「这一条来源冲突是怎么裁的」

§18.8 给「同一场任务里两条来源事实（学生自己在线答的 + 学校导入的外部结果）」
定了四种处置：`KEEP_ONLINE` / `USE_EXTERNAL` / `REJECT_EXTERNAL` /
`KEEP_BOTH_BUT_ONE_EFFECTIVE`。它们**不能挤进既有的 `resolution`**：

| 列 | 回答 | 取值 |
|---|---|---|
| `resolution` | 这一行**写不写进去** | `overwrite` / `skip` |
| `conflict_resolution` | 写进去时**以哪一份为准**、另一份留不留 | 上面那四个 |

两列是**两次选择、两个问题**，与 `age_resolution` 是同一个形状（那一列的注释里
写着同样的推理）。合成一列的直接后果是：整批的「覆盖」会顺手成为一种来源裁决，
而 §20#11 要的正是「已提交在线答卷**不能被外部导入静默覆盖**」——一次点击把几份
学生本人作答的卷子作废，不该是整批动作能做出来的事。

**只有冲突行（`match_status = CONFLICT`）会用到这一列**，所以它可空：其余行留空是
「这个问题没问过」，不是「选了某个默认」（与 `age_resolution` 逐字同一条）。

`downgrade` 直接删列。它带走的是那些人做过的**来源裁决**记录——会话上的
`is_effective` / `supersedes_session_id` / `external_result.verification_status`
都还在，所以回退之后「哪一份有效」仍然答得上来，丢的只是「当初为什么这么裁」。

Revision ID: 0018_row_conflict_resolution
Revises: 0017_json_null_normalize
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_row_conflict_resolution"
down_revision = "0017_json_null_normalize"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_import_row",
        sa.Column("conflict_resolution", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assessment_import_row", "conflict_resolution")
