"""drop the (student_id, status) unique constraint on student_care_case

`0004_care_cases` 给 `student_care_case` 建了一个 `UniqueConstraint("student_id", "status")`，
把「一名学生一种状态只能有一条」当成了不变量。2026-09-17 去掉它：**已关闭**的档案会累积，
而且这是正常的。上学期关掉的那条是一次完整的关怀过程（复核、跟进、回访、复测都在它下面），
这学期再出状况是另一条新的过程，两条都该留着——这正是 CLAUDE.md §1
「关闭档案不得删除历史记录」要保住的东西。约束还在的时候，第二次关闭撞 UNIQUE、
`close_case` 的 `db.flush()` 抛 IntegrityError，用户拿到 500。**这是学校每年都会遇到的
时序**（秋季关档 → 春季再关一次），不是边角情况。

「同一时间只有一条在办」由写入侧保证，不靠数据库约束：
`assessment_service.open_or_reuse_care_case` 先找该生非 CLOSED 的那条，找不到才新建。

**这条迁移 2026-09-19 之前只能在真库上验**：`make test` 当时用内存 sqlite +
`Base.metadata.create_all` 建表、完全不跑 Alembic（CLAUDE.md 已知缺口 3），于是模型改了、
迁移没改，测试照样全绿，而真库上的约束还在——那个 500 一点没变。缺口 3 关闭之后
`make test` 自己就把这条迁移跑一遍（`mysql_support.run_migrations`），这一条现在是
有信号的了。

downgrade 只是把约束加回去，所以它**只在库里没有重复 (student_id, status) 对时能成功**：
一旦关过两次档，回退就会撞 UNIQUE。这是有意的取舍——回退是给「刚迁上去、还没产生新数据」
那几分钟用的。

Revision ID: 0012_drop_care_case_unique
Revises: 0011_student_age
Create Date: 2026-09-17

`revision` 必须在 32 字符以内（`alembic_version.version_num` 是 VARCHAR(32)）。本 id 26 字符。
"""
from alembic import op

revision = "0012_drop_care_case_unique"
down_revision = "0011_student_age"
branch_labels = None
depends_on = None

# 名称来自 `0004_care_cases` 里显式写死的那个（不是命名约定生成的），
# 在 MySQL 上它就是那条唯一索引的名字（`SHOW INDEX FROM student_care_case` 可见）。
CONSTRAINT = "uq_care_case_student_status"
# `student_care_case.student_id` 上有指向 `student.id` 的外键，而 MySQL 要求外键列上
# 有索引——`uq_care_case_student_status` 是**唯一**一个以 `student_id` 打头的索引，
# 所以 MySQL 会拿它去满足外键，然后拒绝删除它：
#   (1553, "Cannot drop index 'uq_care_case_student_status': needed in a foreign key constraint")
# 先建一条普通索引顶上，再去删唯一约束。顺序不能反。
# 这个错误只有 MySQL 会报——2026-09-19 之前测试跑在内存 sqlite 上（它既没有这条外键检查，
# 也根本不跑 Alembic，CLAUDE.md 已知缺口 3），所以「模型改了、迁移没改」在测试里是全绿的。
# 缺口 3 关闭之后，这条路径由 `make test` 每一次都真的走一遍。
FK_INDEX = "ix_student_care_case_student_id"


def upgrade() -> None:
    op.create_index(FK_INDEX, "student_care_case", ["student_id"])
    op.drop_constraint(CONSTRAINT, "student_care_case", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(CONSTRAINT, "student_care_case", ["student_id", "status"])
    # 唯一约束回来之后它自己就能满足外键，这条普通索引是多余的，去掉以还原原状。
    op.drop_index(FK_INDEX, table_name="student_care_case")
