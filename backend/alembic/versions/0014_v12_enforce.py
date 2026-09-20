"""V1.2 对齐（第二步 · 收紧）：`NOT NULL`、生成列、唯一键、复合外键

上面那条（`0013_v12_expand`）把列与表都加好了，这条把它们**变成约束**。
所以这条里的每一条都可能在既有数据上失败，这就是它的全部意义。

**所有前置校验排在下面前所有 DDL 之前**，不是风格问题：MySQL 的 DDL 不在事务里，
中途撞 `1062`（重复）/ `1452`（外键找不到父行）会留下一个一半收紧、一半没收的库，
而且报出来的是一句英文 MySQL 错，离真正的原因很远。校验失败时**改不动任何东西**。

十二条校验里，真正会在真实数据上失败的是这几条（其余是「由既有约束保证、
但换了约束就没人保证」的断言，便宜到值得留着）：

- `student_care_case` 里同一名学生有**多份在办档案**。`uq_care_case_one_active_per_student`
  要把它变成数据库约束，而 `care_service.reopen_case` 目前是**无条件**把 `status`
  设成 `FOLLOWING`——它撞上这条键会给用户一个 500。这条校验会在迁移那一刻就先喊停。
- `retest_plan` / `manual_review` / `follow_up_record` / `family_contact_record` /
  `care_case_event` 上 `care_case_id` 与 `student_id` 指向**不同的学生**。
  复合外键要挡的正是这个，而历史数据是在没有这条约束的时候写进去的。
- 学生的年级 / 班级不属于他所在的那所学校；班级的年级不属于它所在的学校。
- `risk_event` 的 `(session_id, trigger_rule, rule_version)` 重复：`rule_version`
  是 0013 按「该会话的 `assessment_result.rule_version`，取不到就 `LEGACY_UNKNOWN`」
  回填的，所以「同一会话同一规则出现两次」在新键下是重复。

**两条在这一步一起换**：`uq_session_task_student`（`task_id` + `student_id`）让位给
`uq_session_task_student_attempt`（再加一个 `attempt_no`），中间不留窗口——
先删后加会开出「一场任务里谁都能开第二份卷子」的一段，并发的
`create_or_get_session` 正好在那个窗口里重复插入。

**回退**：这条的 `downgrade` 会把 `uq_session_task_student` 加回来，而它只在库里没有
重复 `(task_id, student_id)` 时能成功——`attempt_no` 已经是 2 的那些行会挡路。
这是有意的取舍：回退是给「刚迁上去、还没产生新数据」那几分钟用的，与 `0012` 同一条口径。

Revision ID: 0014_v12_enforce
Revises: 0013_v12_expand
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0014_v12_enforce"
down_revision = "0013_v12_expand"
branch_labels = None
depends_on = None


PRECHECKS = (
    (
        "assessment_session.school_id 还有 NULL（回填只覆盖得到名册上还有的学生）",
        "SELECT id, student_id FROM assessment_session WHERE school_id IS NULL LIMIT 5",
    ),
    (
        "assessment_target.school_id_snapshot 还有 NULL",
        "SELECT id, student_id FROM assessment_target WHERE school_id_snapshot IS NULL LIMIT 5",
    ),
    (
        "risk_event.signal_type 还有 NULL（认不出的 risk_type 在 0013 就该中止了）",
        "SELECT id, risk_type FROM risk_event WHERE signal_type IS NULL LIMIT 5",
    ),
    (
        "同一个任务里同一个学生有多份 attempt_no 相同的卷子",
        """
        SELECT task_id, student_id, attempt_no, COUNT(*) AS n
        FROM assessment_session WHERE task_id IS NOT NULL
        GROUP BY task_id, student_id, attempt_no HAVING n > 1 LIMIT 5
        """,
    ),
    (
        "同一个任务里同一个学生有多份 is_effective = 1 的卷子"
        "（uq_session_effective_task_student 要挡的就是这个）",
        """
        SELECT task_id, student_id, COUNT(*) AS n
        FROM assessment_session
        WHERE task_id IS NOT NULL AND is_effective = 1
        GROUP BY task_id, student_id HAVING n > 1 LIMIT 5
        """,
    ),
    (
        "同一名学生有多份在办档案——uq_care_case_one_active_per_student 装不上去，"
        "而 care_service.reopen_case 目前是无条件把 status 设成 FOLLOWING 的",
        """
        SELECT student_id, COUNT(*) AS n
        FROM student_care_case WHERE status <> 'CLOSED'
        GROUP BY student_id HAVING n > 1 LIMIT 5
        """,
    ),
    (
        "risk_event 的 (session_id, trigger_rule, rule_version) 有重复",
        """
        SELECT session_id, trigger_rule, rule_version, COUNT(*) AS n
        FROM risk_event
        GROUP BY session_id, trigger_rule, rule_version HAVING n > 1 LIMIT 5
        """,
    ),
    (
        "scale_rule 的 (scale_id, rule_version) 有重复",
        """
        SELECT scale_id, rule_version, COUNT(*) AS n
        FROM scale_rule GROUP BY scale_id, rule_version HAVING n > 1 LIMIT 5
        """,
    ),
    (
        "学生的年级 / 班级不属于他所在的那所学校",
        """
        SELECT st.id, st.school_id, g.school_id, cg.school_id
        FROM student st
        LEFT JOIN grade g ON g.id = st.grade_id
        LEFT JOIN class_group cg ON cg.id = st.class_id
        WHERE (g.id IS NOT NULL AND g.school_id <> st.school_id)
           OR (cg.id IS NOT NULL AND cg.school_id <> st.school_id)
        LIMIT 5
        """,
    ),
    (
        "班级的年级不属于它所在的那所学校",
        """
        SELECT cg.id, cg.school_id, g.school_id
        FROM class_group cg JOIN grade g ON g.id = cg.grade_id
        WHERE g.school_id <> cg.school_id LIMIT 5
        """,
    ),
    (
        "复测计划指向了**别的学生**的答卷",
        """
        SELECT rp.id, rp.student_id, s1.student_id, s2.student_id
        FROM retest_plan rp
        LEFT JOIN assessment_session s1 ON s1.id = rp.source_session_id
        LEFT JOIN assessment_session s2 ON s2.id = rp.completed_session_id
        WHERE (s1.id IS NOT NULL AND s1.student_id <> rp.student_id)
           OR (s2.id IS NOT NULL AND s2.student_id <> rp.student_id)
        LIMIT 5
        """,
    ),
    (
        "关怀链路里 care_case_id 与 student_id 指向了不同的学生"
        "（`care_case_id` 在历史行上全是 NULL，所以这条今天是空转的；"
        "留着的理由是它一旦被回填就立刻成立）",
        """
        SELECT 'manual_review' AS src, x.id FROM manual_review x
          JOIN student_care_case c ON c.id = x.care_case_id
          WHERE c.student_id <> x.student_id
        UNION ALL
        SELECT 'follow_up_record', x.id FROM follow_up_record x
          JOIN student_care_case c ON c.id = x.care_case_id
          WHERE c.student_id <> x.student_id
        UNION ALL
        SELECT 'family_contact_record', x.id FROM family_contact_record x
          JOIN student_care_case c ON c.id = x.care_case_id
          WHERE c.student_id <> x.student_id
        UNION ALL
        SELECT 'retest_plan', x.id FROM retest_plan x
          JOIN student_care_case c ON c.id = x.care_case_id
          WHERE c.student_id <> x.student_id
        UNION ALL
        SELECT 'care_case_event', x.id FROM care_case_event x
          JOIN student_care_case c ON c.id = x.care_case_id
          WHERE c.student_id <> x.student_id
        LIMIT 5
        """,
    ),
)


def _precheck() -> None:
    """撞上就中止，**在任何 DDL 之前**。

    MySQL 的 DDL 不在事务里，所以「先改一半再发现不行」是不可回退的：
    报出来的还会是一句英文的 1062 / 1452，离真正的原因（哪一行数据不对）很远。
    """
    bind = op.get_bind()
    for what, sql in PRECHECKS:
        rows = bind.execute(sa.text(sql)).fetchall()
        if rows:
            raise RuntimeError(
                f"[{revision}] 中止：{what}。前 5 行：{rows}"
            )


def upgrade() -> None:
    _precheck()

    # 一、两个生成列。**0013 里没有它们**（见那边第 4 条）：它们的用途就是承载
    # 下面那两条唯一键，而旧写入方必须被那两条键挡住，所以它们属于「收紧」这一半。
    # `Computed(..., persisted=True)` 在 MySQL 上出 `GENERATED ALWAYS AS (…) STORED`。
    op.add_column('assessment_session', sa.Column('effective_task_student_key', sa.String(length=96), sa.Computed("CASE WHEN `is_effective` = 1 AND `task_id` IS NOT NULL THEN CONCAT(`task_id`, ':', `student_id`) ELSE NULL END", persisted=True), nullable=True))
    op.add_column('student_care_case', sa.Column('active_student_id', sa.Integer(), sa.Computed("CASE WHEN `status` <> 'CLOSED' THEN `student_id` ELSE NULL END", persisted=True), nullable=True))

    # `ix_care_case_active_student` 是生成列上的那条普通索引，跟着它一起在这里建。
    # SQLAlchemy 的 MySQL 方言**不会**把 Index 内联进 CREATE TABLE（它先建表、
    # 再单独 CREATE INDEX），所以生成列与它的索引必须各自显式写一遍。
    op.create_index('ix_care_case_active_student', 'student_care_case', ['active_student_id'], unique=False)

    # 二、三个 NOT NULL。回填在 0013 里做完了，这一步只是把承诺写进表结构。
    # `existing_type` 必须给：MySQL 的 `MODIFY COLUMN` 要一整份列定义。
    op.alter_column('assessment_session', 'school_id',
                existing_type=sa.Integer(), nullable=False)
    op.alter_column('assessment_target', 'school_id_snapshot',
                existing_type=sa.Integer(), nullable=False)
    op.alter_column('risk_event', 'signal_type',
                existing_type=sa.String(length=64), nullable=False)

    # 三、**「一场任务一人一份卷子」在这一步换**：老的那条按
    # (task_id, student_id)，新的加上 attempt_no。
    #
    # **先加新的、再删旧的**，不是反过来：`uq_session_task_student` 兼作
    # `fk_session_task`（`task_id`）的索引，MySQL 会以 1553 拒绝删一条外键
    # 正在用的索引。手工那份 DDL 把「删旧的 + 加新的」写在**同一条**
    # `ALTER TABLE` 里，那样是成立的（MySQL 在整条语句结束时才校一遍索引需求）；
    # 而 Alembic 发的是两条语句，所以只能按「先加后删」排——这条次序换来的
    # 正好是更强的性质：任何一刻都有一把键在管着「一场任务一人一份卷子」。
    op.create_unique_constraint('uq_session_task_student_attempt', 'assessment_session', ['task_id', 'student_id', 'attempt_no'])
    op.drop_index(op.f('uq_session_task_student'), table_name='assessment_session')

    # 其余的键。父表那六个不在这里——它们在 0013。
    op.create_unique_constraint('uq_session_effective_task_student', 'assessment_session', ['effective_task_student_key'])
    op.create_unique_constraint('uq_session_external_source_result', 'assessment_session', ['external_source_system', 'external_result_id'])
    op.create_unique_constraint('uq_audit_event_id', 'audit_log', ['event_id'])
    op.create_unique_constraint('uq_risk_event_session_trigger_rule', 'risk_event', ['session_id', 'trigger_rule', 'rule_version'])
    op.create_unique_constraint('uq_scale_rule_version', 'scale_rule', ['scale_id', 'rule_version'])
    op.create_unique_constraint('uq_care_case_one_active_per_student', 'student_care_case', ['active_student_id'])

    # 四、外键。复合的那些都指着 0013 里加好的父键。
    # **全库没有 ondelete=**（CLAUDE.md §1）：每一个外键都是 RESTRICT。
    op.create_foreign_key('assessment_session_fk_task_school', 'assessment_session', 'assessment_task', ['task_id', 'school_id'], ['id', 'school_id'])
    op.create_foreign_key('assessment_session_fk_supersedes', 'assessment_session', 'assessment_session', ['supersedes_session_id'], ['id'])
    op.create_foreign_key('assessment_session_fk_student_school', 'assessment_session', 'student', ['school_id', 'student_id'], ['school_id', 'id'])
    op.create_foreign_key('assessment_session_ibfk_4', 'assessment_session', 'assessment_import_batch', ['import_batch_id'], ['id'])
    op.create_foreign_key('assessment_target_fk_effective_session', 'assessment_target', 'assessment_session', ['effective_session_id'], ['id'])
    op.create_foreign_key('assessment_target_fk_effective_external', 'assessment_target', 'assessment_external_result', ['effective_external_result_id'], ['id'])
    op.create_foreign_key('assessment_target_fk_supplement_batch', 'assessment_target', 'assessment_import_batch', ['supplemented_from_batch_id'], ['id'])
    op.create_foreign_key('assessment_target_fk_task_school', 'assessment_target', 'assessment_task', ['task_id', 'school_id_snapshot'], ['id', 'school_id'])
    op.create_foreign_key('assessment_target_fk_effective_external_student', 'assessment_target', 'assessment_external_result', ['effective_external_result_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('assessment_target_fk_student_school', 'assessment_target', 'student', ['school_id_snapshot', 'student_id'], ['school_id', 'id'])
    op.create_foreign_key('assessment_target_fk_marked_by', 'assessment_target', 'user_account', ['marked_by'], ['id'])
    op.create_foreign_key('assessment_target_fk_effective_session_student', 'assessment_target', 'assessment_session', ['effective_session_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('class_group_ibfk_3', 'class_group', 'grade', ['school_id', 'grade_id'], ['school_id', 'id'])
    op.create_foreign_key('family_contact_record_ibfk_3', 'family_contact_record', 'student_care_case', ['care_case_id'], ['id'])
    op.create_foreign_key('family_contact_record_fk_case_student', 'family_contact_record', 'student_care_case', ['care_case_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('follow_up_record_ibfk_3', 'follow_up_record', 'student_care_case', ['care_case_id'], ['id'])
    op.create_foreign_key('follow_up_record_fk_case_student', 'follow_up_record', 'student_care_case', ['care_case_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('manual_review_fk_case_student', 'manual_review', 'student_care_case', ['care_case_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('manual_review_ibfk_3', 'manual_review', 'student_care_case', ['care_case_id'], ['id'])
    op.create_foreign_key('manual_review_ibfk_4', 'manual_review', 'student', ['student_id'], ['id'])
    op.create_foreign_key('retest_plan_fk_source_student', 'retest_plan', 'assessment_session', ['source_session_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('retest_plan_fk_completed_student', 'retest_plan', 'assessment_session', ['completed_session_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('retest_plan_ibfk_5', 'retest_plan', 'student_care_case', ['care_case_id'], ['id'])
    op.create_foreign_key('retest_plan_fk_case_student', 'retest_plan', 'student_care_case', ['care_case_id', 'student_id'], ['id', 'student_id'])
    op.create_foreign_key('student_ibfk_5', 'student', 'class_group', ['school_id', 'class_id'], ['school_id', 'id'])
    op.create_foreign_key('student_ibfk_4', 'student', 'grade', ['school_id', 'grade_id'], ['school_id', 'id'])
    op.create_foreign_key('student_care_case_ibfk_4', 'student_care_case', 'user_account', ['reopened_by'], ['id'])
    op.create_foreign_key('student_care_case_ibfk_3', 'student_care_case', 'user_account', ['closed_by'], ['id'])


def downgrade() -> None:
    # 四、外键先走。顺序不是排版：复合的那几条
    # （`manual_review_fk_case_student` …）指着 `uq_care_case_id_student`，
    # 而 MySQL 会以 1553 拒绝删一条外键正在用的索引。
    op.drop_constraint('student_care_case_ibfk_3', 'student_care_case', type_='foreignkey')
    op.drop_constraint('student_care_case_ibfk_4', 'student_care_case', type_='foreignkey')
    # `student_ibfk_4` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引，写在同一条 ALTER 里。
    op.execute("ALTER TABLE student DROP FOREIGN KEY student_ibfk_4, DROP INDEX student_ibfk_4")
    # `student_ibfk_5` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引，写在同一条 ALTER 里。
    op.execute("ALTER TABLE student DROP FOREIGN KEY student_ibfk_5, DROP INDEX student_ibfk_5")
    # `retest_plan_fk_case_student` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引、把 V1.0 那条加回来，写在同一条 ALTER 里。
    op.execute("ALTER TABLE retest_plan DROP FOREIGN KEY retest_plan_fk_case_student, DROP INDEX retest_plan_fk_case_student, ADD INDEX student_id (student_id)")
    op.drop_constraint('retest_plan_ibfk_5', 'retest_plan', type_='foreignkey')
    # `retest_plan_fk_completed_student` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引、把 V1.0 那条加回来，写在同一条 ALTER 里。
    op.execute("ALTER TABLE retest_plan DROP FOREIGN KEY retest_plan_fk_completed_student, DROP INDEX retest_plan_fk_completed_student, ADD INDEX completed_session_id (completed_session_id)")
    # `retest_plan_fk_source_student` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引、把 V1.0 那条加回来，写在同一条 ALTER 里。
    op.execute("ALTER TABLE retest_plan DROP FOREIGN KEY retest_plan_fk_source_student, DROP INDEX retest_plan_fk_source_student, ADD INDEX source_session_id (source_session_id)")
    op.drop_constraint('manual_review_ibfk_4', 'manual_review', type_='foreignkey')
    op.drop_constraint('manual_review_ibfk_3', 'manual_review', type_='foreignkey')
    op.drop_constraint('manual_review_fk_case_student', 'manual_review', type_='foreignkey')
    # `follow_up_record_fk_case_student` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引、把 V1.0 那条加回来，写在同一条 ALTER 里。
    op.execute("ALTER TABLE follow_up_record DROP FOREIGN KEY follow_up_record_fk_case_student, DROP INDEX follow_up_record_fk_case_student, ADD INDEX student_id (student_id)")
    op.drop_constraint('follow_up_record_ibfk_3', 'follow_up_record', type_='foreignkey')
    # `family_contact_record_fk_case_student` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引、把 V1.0 那条加回来，写在同一条 ALTER 里。
    op.execute("ALTER TABLE family_contact_record DROP FOREIGN KEY family_contact_record_fk_case_student, DROP INDEX family_contact_record_fk_case_student, ADD INDEX student_id (student_id)")
    op.drop_constraint('family_contact_record_ibfk_3', 'family_contact_record', type_='foreignkey')
    # `class_group_ibfk_3` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引，写在同一条 ALTER 里。
    op.execute("ALTER TABLE class_group DROP FOREIGN KEY class_group_ibfk_3, DROP INDEX class_group_ibfk_3")
    # `assessment_target_fk_effective_session_student` 的索引不会被 `DROP FOREIGN KEY` 带走，而它顶掉的正是
    # V1.0 那条按列名命名的索引 —— 这一步删外键、删它留下的索引、把 V1.0 那条加回来，写在同一条 ALTER 里。
    op.execute("ALTER TABLE assessment_target DROP FOREIGN KEY assessment_target_fk_effective_session_student, DROP INDEX assessment_target_fk_effective_session_student, ADD INDEX student_id (student_id)")
    op.drop_constraint('assessment_target_fk_marked_by', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_target_fk_student_school', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_target_fk_effective_external_student', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_target_fk_task_school', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_target_fk_supplement_batch', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_target_fk_effective_external', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_target_fk_effective_session', 'assessment_target', type_='foreignkey')
    op.drop_constraint('assessment_session_ibfk_4', 'assessment_session', type_='foreignkey')
    op.drop_constraint('assessment_session_fk_student_school', 'assessment_session', type_='foreignkey')
    op.drop_constraint('assessment_session_fk_supersedes', 'assessment_session', type_='foreignkey')
    op.drop_constraint('assessment_session_fk_task_school', 'assessment_session', type_='foreignkey')

    # 唯一键。
    op.drop_constraint('uq_care_case_one_active_per_student', 'student_care_case', type_='unique')
    op.drop_constraint('uq_scale_rule_version', 'scale_rule', type_='unique')
    # `risk_event.session_id` 上那条按列名的索引，是刚才被 `uq_risk_event_session_trigger_rule` 顶掉的，
    # 而 `risk_event` 上还有外键指着这一列 —— 不与 DROP 写在同一条 ALTER 里
    # 就是 errno 1553。
    op.execute("ALTER TABLE risk_event DROP INDEX uq_risk_event_session_trigger_rule, ADD INDEX session_id (session_id)")
    op.drop_constraint('uq_audit_event_id', 'audit_log', type_='unique')
    op.drop_constraint('uq_session_external_source_result', 'assessment_session', type_='unique')
    op.drop_constraint('uq_session_effective_task_student', 'assessment_session', type_='unique')

    # 普通索引。
    op.drop_index('ix_care_case_active_student', table_name='student_care_case')

    # 三、把「一场任务一人一份卷子」那把键加回来，同样**先加后删**
    # （理由与 upgrade 那里同一条：1553）。
    # **它只在库里没有重复 (task_id, student_id) 时能成功**——`attempt_no` 已经是 2
    # 的那些行会挡路。这是有意的取舍：回退是给「刚迁上去、还没产生新数据」那几分钟用的。
    op.create_index(op.f('uq_session_task_student'), 'assessment_session', ['task_id', 'student_id'], unique=True)
    op.drop_constraint('uq_session_task_student_attempt', 'assessment_session', type_='unique')

    # 二、三个 NOT NULL 放回可空。
    op.alter_column('risk_event', 'signal_type',
                existing_type=sa.String(length=64), nullable=True)
    op.alter_column('assessment_target', 'school_id_snapshot',
                existing_type=sa.Integer(), nullable=True)
    op.alter_column('assessment_session', 'school_id',
                existing_type=sa.Integer(), nullable=True)

    # 一、两个生成列。
    op.drop_column('student_care_case', 'active_student_id')
    op.drop_column('assessment_session', 'effective_task_student_key')
