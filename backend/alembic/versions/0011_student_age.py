"""student.age replaces student.birth_date

学校的名册上只有年龄，没有出生日期，所以「学生信息导入」的最后一列从出生日期改成年龄
（`27025160101,示例学生,初一,701,男,12`）。

0009 当初存的是 `birth_date`、读取时现算年龄，理由是「存下来的整数年龄在学生过完生日那一刻
就是错的」。2026-09-17 按学校要求反过来：名册里只有年龄这一个数，现算就没有输入可算。
代价是知情的：**这一列不会自己变**，过一年不重导名册，界面上的年龄就停在去年。所以
`assessment_import_service` 按年龄消歧时保留 ±1 岁的容错——那份文件可能是去年那次普查。

既有行按 `TIMESTAMPDIFF(YEAR, birth_date, CURDATE())` 回填，只精确到整年：原件里的月日就此
丢掉，downgrade 也只能按整年倒推（`CURDATE() - INTERVAL age YEAR`），回不到真实的生日。
这两句是 MySQL 方言——本项目的库是 MySQL（README 与 `core/config.py` 都是），而测试
完全不跑 Alembic（CLAUDE.md 已知缺口 3：内存 sqlite 由 `Base.metadata.create_all` 建表），
所以这段回填只在真库上生效。**回填不能省**：不加这一步，全库的年龄会一次性变成 NULL。

Revision ID: 0011_student_age
Revises: 0010_import_source
Create Date: 2026-09-17

`revision` 必须在 32 字符以内（`alembic_version.version_num` 是 VARCHAR(32)）：超长不会在
ADD COLUMN 处报错，DDL 先提交、版本戳的 UPDATE 才失败，库变成「已迁移但仍记在上一个版本」。
本 id 16 字符。
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_student_age"
down_revision = "0010_import_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student", sa.Column("age", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE student SET age = TIMESTAMPDIFF(YEAR, birth_date, CURDATE()) "
        "WHERE birth_date IS NOT NULL"
    )
    op.drop_column("student", "birth_date")


def downgrade() -> None:
    op.add_column("student", sa.Column("birth_date", sa.Date(), nullable=True))
    op.execute(
        "UPDATE student SET birth_date = DATE_SUB(CURDATE(), INTERVAL age YEAR) "
        "WHERE age IS NOT NULL"
    )
    op.drop_column("student", "age")
