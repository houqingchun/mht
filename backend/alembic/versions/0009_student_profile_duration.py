"""student profile fields and assessment duration

Two gaps the school asked to close:

* `student` carried 姓名/年级/班级 but no 性别 or 年龄. Age is stored as
  `birth_date` and derived on read — a stored integer age is wrong the moment the
  student has a birthday, the same reason `masked_name` must not be backfilled.
* `assessment_session` recorded when a task was opened (`started_at`) and when it
  was submitted, but never how long the student actually spent answering. That
  duration is a data-quality signal: a 100-question MHT finished in under a
  minute warrants a closer look during manual review. Measured from the student's
  FIRST ANSWER to submit, and written once at submit time.

All three columns are nullable with no server_default: the roster and the existing
sessions predate them, and the student import treats both fields as optional.
Rows written before this migration keep NULL in all three.

`birth_date` 后来被 `0011_student_age` 删掉了（学校名册上只有年龄这一个数，没有可用来现算的
输入），所以上面那条「存出生日期、读取时现算」的理由**只在 0009 与 0010 之间成立**。
读这一份时别把它当成现行设计——现在存的是整数年龄。

Revision ID: 0009_student_profile_duration
Revises: 0008_system_setting
Create Date: 2026-09-16

`revision` must stay within 32 characters: alembic's own
`alembic_version.version_num` is VARCHAR(32), and an id one character longer does
not fail at ADD COLUMN — the DDL runs and commits (MySQL DDL is not transactional),
and the version stamp UPDATE fails after it. The database ends up migrated but
still recorded at the previous revision, so the next upgrade re-runs the DDL and
dies on "Duplicate column name". This id was 33 characters the first time.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_student_profile_duration"
down_revision = "0008_system_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student", sa.Column("gender", sa.String(length=16), nullable=True))
    op.add_column("student", sa.Column("birth_date", sa.Date(), nullable=True))
    op.add_column(
        "assessment_session", sa.Column("duration_seconds", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("assessment_session", "duration_seconds")
    op.drop_column("student", "birth_date")
    op.drop_column("student", "gender")
