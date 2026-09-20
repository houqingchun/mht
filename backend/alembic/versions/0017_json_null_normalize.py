"""把可空 JSON 列里的 JSON `null` 改写成 SQL NULL

这不是一次 schema 变更，是**数据**变更：DDL 一个字没动，改的是三列里已经写下去的
那些 `null`。

`sqlalchemy.JSON` 的默认行为（`none_as_null=False`）是：Python 的 `None` 绑定成
**JSON 字面量 `null`**，而不是 SQL NULL。于是「这一行没有候选」在库里长这样：

    JSON_TYPE(candidate_student_ids) = 'NULL'   ← 是 JSON null
    candidate_student_ids IS NULL               ← 假！

而 `assessment_import_service.list_import_rows` 的第二支正是拿 `IS NULL` 判
「没匹配到学生、候选也为空」的——**它恒假**。后果不是报错，是静默漏行：明细里
只剩下匹配到学生的那些行，而操作员最需要看见的恰恰是没匹配上的（名册上没有这个班、
没有这个人、同名分不开）。同一屏上 `row_counts` 走另一次查询说「有问题 N 行」，
列表却是空的——两个数说的是同一件事，各说各话（CLAUDE.md §11 那条）。

**为什么只有这三列**：`candidate_student_ids` 是唯一有 `IS NULL` 读者的列（本次
真踩到的那一个）；`dimension_scores_json` / `result_payload_json` 与它同表、同一类
写法，一起改免得同一个坑在同一张表里留两份。其余可空 JSON 列刻意不动，各有各的理由：

* `audit_log.detail_json` —— 今天**既没有读者也没有写入方**（`write_audit` 写的是
  字符串那一个 `detail`），改它是一次无人验证的改动。哪天它有写入方了照这条规则办。
* `system_setting.value_json` —— **反过来，它依赖 JSON null 的语义**：
  `nullable=False` 而注解显式允许 `None` 作为一个值，靠的正是「JSON null 不是 SQL
  NULL」才能把 `None` 存进一个 NOT NULL 列。给它加上 `none_as_null=True` 会在写
  `None` 时撞 NOT NULL。**这一列不许跟着改。**

`JSON_TYPE(...) = 'NULL'` 是 MySQL 8 的判据（SQL NULL 走 `JSON_TYPE` 出来也是 SQL
NULL，不会等于字符串 `'NULL'`），所以这条 UPDATE 只碰 JSON null，不碰已经是 SQL NULL
的行——重跑一次什么都不会发生。

Revision ID: 0017_json_null_normalize
Revises: 0016_import_batch_name
Create Date: 2026-09-19
"""

from alembic import op

revision = "0017_json_null_normalize"
down_revision = "0016_import_batch_name"
branch_labels = None
depends_on = None

# (表, 列)：与模型里那三个 `JSON(none_as_null=True)` 一一对应
COLUMNS = [
    ("assessment_import_row", "candidate_student_ids"),
    ("assessment_external_result", "dimension_scores_json"),
    ("assessment_external_result", "result_payload_json"),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f"UPDATE {table} SET {column} = NULL "
            f"WHERE JSON_TYPE({column}) = 'NULL'"
        )


def downgrade() -> None:
    """改回 JSON null。

    两边的行数必然相等：反过来的那批行正是 upgrade 写下去的那批（SQL NULL）。
    它**不是**在恢复什么真实事实——JSON null 与 SQL NULL 在这一列上本来就同义，
    这里只是让降级之后的行与旧版代码写出来的行长得一样。
    """
    for table, column in COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = CAST('null' AS JSON) WHERE {column} IS NULL")
