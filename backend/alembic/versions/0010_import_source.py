"""assessment source: in-system vs imported

学校在**其他平台**做了心理普查，结果需要导入本系统分析（数据中心 → MHT测评记录导入）。
一份导入的记录与一份本系统内的记录，如果形状完全一样，那么没人能回答这个问题：
「这条重点关注是学生在本系统里测出来的，还是外部平台的结果？」

所以 `assessment_task` 与 `assessment_session` 各加一列来源标记：

* `IN_SYSTEM` —— 学生在本系统里作答（所有既有行，`seed` 与 `seed_demo` 都是）；
* `IMPORTED` —— 学校把外部平台的普查结果导进来。

用 `server_default="IN_SYSTEM"` 回填既有行，因此这一列对现有数据是「无改动」的。
`server_default` 而不是 app 侧 `default`：这个值要由 **MySQL** 补，不是由 ORM 补——
迁移回填、手写 SQL、以及任何绕过 ORM 的写入都拿得到它。模型那边写了同样的
`server_default`，而 2026-09-19 起有测试盯着这一致性：表由 `alembic upgrade head`
建，`test_the_migrated_database_matches_the_models_exactly` 逐列比模型与迁移。

`String(16)` 装得下两个码，留了一点余量给未来可能的第三种来源（如「纸质录入」）。

Revision ID: 0010_import_source
Revises: 0009_student_profile_duration
Create Date: 2026-09-16

`revision` 必须在 32 字符以内：`alembic_version.version_num` 是 VARCHAR(32)，
超长不会在 ADD COLUMN 处报错——DDL 先提交（MySQL 的 DDL 不在事务里），版本戳的
UPDATE 才失败，库变成「已迁移但仍记在上一个版本」。本 id 17 字符。
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_import_source"
down_revision = "0009_student_profile_duration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_task",
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="IN_SYSTEM"
        ),
    )
    op.add_column(
        "assessment_session",
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default="IN_SYSTEM"
        ),
    )


def downgrade() -> None:
    op.drop_column("assessment_session", "source")
    op.drop_column("assessment_task", "source")
