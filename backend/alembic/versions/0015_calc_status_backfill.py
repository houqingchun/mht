"""把「已经有结果」的历史场次标成 `CALCULATED`

V1.2 对齐（`0013`）给 `assessment_session` 加了 `calculation_status`，`server_default`
是 `PENDING`（诚实的默认值：加这一列的时候，没有任何东西知道那些行算过没有）。于是
**每一场历史测评都顶着「待计算」**——包括那些库里明明躺着 `assessment_result` 的场次。
升级到这一版之后：

- 个案详情上那一行「评分状态」会对这些场次说「待计算」；
- 而新加的「重算评分」按钮**修不好它**：`retry_calculation` 看到已经有结果就直接返回
  （`recalculated: False`），它拦的是真正的「没算成」，不是这一列没说对。

所以这条迁移要做的是一件**从既有事实推导**的事，与 `0013` 里那三处回填同一个性质：
`assessment_result` 那一行在，就说明它算过了。这不是替历史编一个值——「有没有结果行」
是库里本来就有的、可被任何人复核的事实。

**刻意不回填的两处**（与 CLAUDE.md §21 那条口径一致，不是漏了）：

- `answer_snapshot_hash` / `answer_hash_algorithm`：哈希要能证明「结果行是按现在这份答卷
  算出来的」，而历史场次的答卷在那之后可能被 `reset` 重答、或者被重导覆盖过（原始答卷层
  不保留版本）。**今天重新算一遍得到的哈希，证明的只是「今天的答卷与今天的结果对得上」，
  而那正是它唯一证明不了的事。** 所以历史行留 NULL，如实地说「这一列对它们不适用」。
- `tested_at` / `tested_at_source`：导入的会话没人知道真实的测评日（文件里那一列是后加的），
  拿 `created_at` 或 `submitted_at` 填上是给它编一个看起来像事实的值。第三档
  `PENDING_VERIFICATION` 是这两列唯一诚实的状态，留着。

`downgrade` 把这次改动**原样撤回**，判据是 `answer_snapshot_hash IS NULL`：新代码算出来的
行一定会带哈希（`score_session` 把两列一起写），所以「有结果 + 没有哈希」恰好就是这条迁移
动过的那一批。不带这个判据的话，回退会把升级之后新算出来的场次一起说成「待计算」。

Revision ID: 0015_calc_status_backfill
Revises: 0014_v12_enforce
Create Date: 2026-09-19
"""

from alembic import op

revision = "0015_calc_status_backfill"
down_revision = "0014_v12_enforce"
branch_labels = None
depends_on = None


#: 一次 JOIN 回填。`calculation_status = 'PENDING'` 这个条件不是防御性的：`CALCULATING`
#: 只可能存在于一个正在跑的事务里（`score_session` 进门就置它、同一个事务里落结果），
#: 所以真库里不会遇到；写上是为了万一遇到时**不覆盖**那个更具体的事实。
BACKFILL = (
    "UPDATE assessment_session AS s "
    "JOIN assessment_result AS r ON r.session_id = s.id "
    "SET s.calculation_status = 'CALCULATED' "
    "WHERE s.calculation_status = 'PENDING'"
)

REVERT = (
    "UPDATE assessment_session AS s "
    "JOIN assessment_result AS r ON r.session_id = s.id "
    "SET s.calculation_status = 'PENDING' "
    "WHERE s.calculation_status = 'CALCULATED' AND s.answer_snapshot_hash IS NULL"
)


def upgrade() -> None:
    op.execute(BACKFILL)


def downgrade() -> None:
    op.execute(REVERT)
