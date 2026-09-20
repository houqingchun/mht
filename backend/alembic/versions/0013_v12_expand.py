"""V1.2 对齐（第一步 · 扩张）：十张新表、既有表上的新列、回填

`docs/schema_mysql8_incremental_V1.2_完整修订版.sql` 是按需求说明书 §16.1 的六段式写的，
这条迁移是它的**前两段**：只加、不收。凡是**在既有数据上可能失败**的东西
（`NOT NULL`、唯一键、外键、生成列、`DROP INDEX`）都在 `0014_v12_enforce` 里，
而且它们的前置校验排在那边所有 DDL 之前——MySQL 的 DDL 不在事务里，
中途撞 1062 / 1452 会留下一半已改、一半没改的库。

两半的判据只有一句：**这条 DDL 在只读过 V1.0 数据的库上会不会失败。**
会失败的都归 0014，包括「新表上的外键指着一把新加的父键」这种看起来属于 0013 的。

四件在别处看着可疑、在这里是有意的事：

1. **三个 `NOT NULL` 且没有默认值的列先建成可空**（`assessment_session.school_id`、
   `assessment_target.school_id_snapshot`、`risk_event.signal_type`）。V1.0 的写入路径
   一个都不知道它们，直接建成 `NOT NULL` 会让这条迁移在**空库**上都要靠
   `ALTER TABLE` 的隐式默认值蒙混过去。回填在下面，收紧在 0014。

2. **父表那一侧的六个唯一键在这里加**（`uq_*_school_id` / `uq_session_id_student` /
   `uq_care_case_id_student`）。它们每一把都含主键列，所以**永远不可能重复**，
   加它们不改变任何既有业务含义；而下面那十张新表里的复合外键指着它们。
   放在 0014 的话，0013 建表时就会撞 `1822 Missing index for constraint`。

3. **环上的那条外键排到最后**（`assessment_import_row_fk_external_record`）：
   `assessment_import_row` 与 `assessment_external_result` 互相指着，两条
   `CREATE TABLE` 谁先谁后都建不出来，只能两张都建好再 `ALTER`。

4. **生成列在 0014，不在这里**。它们是纯增量的、不可能失败，本来放哪边都行；
   放在 0014 是为了让「0013 加的列，旧写入方可以全部不管」这句话成立
   ——`effective_task_student_key` 与 `active_student_id` 是新不变量的载体，
   旧写入方必须被 0014 的那两条唯一键挡住。

前置校验里那条 `risk_type` 值得单说：**它认 `risk_type`，不认 `trigger_rule`。**
DDL 自己那份认的是 `KEY_QUESTION_85` / `KEY_QUESTION_97`，而引擎实际发的是
`KEY_QUESTION_{n}_YES`（`scale_engine/engine.py:267`）——那份校验在真库上是**空转通过**的，
新规则一出现就再也不保护任何东西。

Revision ID: 0013_v12_expand
Revises: 0012_drop_care_case_unique
Create Date: 2026-09-19

`revision` 必须在 32 字符以内（`alembic_version.version_num` 是 VARCHAR(32)）。
超长不会在 `ADD COLUMN` 处报错——MySQL 的 DDL 不在事务里，DDL 先提交、
版本戳那一条 UPDATE 才失败，库从此卡在「已迁移但仍记在上一版」。
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0013_v12_expand"
down_revision = "0012_drop_care_case_unique"
branch_labels = None
depends_on = None


PRECHECKS = (
    (
        "risk_event.risk_type 里有认不出的值，signal_type 回填不出来"
        "（0014 会把它收紧成 NOT NULL，那时就晚了）",
        """
        SELECT id, risk_type, trigger_rule
        FROM risk_event
        WHERE risk_type NOT IN (
            'MANUAL_REVIEW_REQUIRED', 'KEY_QUESTION_TRIGGERED',
            'RETEST_RECOMMENDED', 'HIGH_TOTAL_SCORE',
            'HIGH_DIMENSION_SCORE', 'SCREENING_SIGNAL')
        LIMIT 5
        """,
    ),
)


def _precheck() -> None:
    """撞上就中止，**在任何 DDL 之前**。

    MySQL 的 DDL 不在事务里，所以「先改一半再发现不行」是不可回退的：
    报出来的还会是一句英文的 1062 / 1452，离真正的原因（哪一行数据不对）很远。

    **离线模式（`alembic upgrade --sql`）下这一层不跑**，因为那几条 SELECT 要读回结果，
    而离线渲染拿到的是一个 `MockConnection`——它的 `execute()` 返回 `None`，
    下一句 `.fetchall()` 当场 `AttributeError`。0013 与 0014 都会撞上（0013 也要读数据）。

    出路不是把校验丢掉：`deploy/build_migration_sql.py` 生成增量 SQL 时，会把这里的
    `PRECHECKS` **原样搬到那份文件的最前面**当第一段，由执行的人先跑一遍再看结果。
    校验的**唯一出处仍然是下面这个常量**，只是换了个执行者——
    别在这条 return 上面顺手加一句「离线就不校验了」的注释，那正是这一句要挡的事。
    """
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    for what, sql in PRECHECKS:
        rows = bind.execute(sa.text(sql)).fetchall()
        if rows:
            raise RuntimeError(
                f"[{revision}] 中止：{what}。前 5 行：{rows}"
            )


BACKFILLS = (
    # 一、会话来源。V1.0 的 `source` 只有 IN_SYSTEM / IMPORTED 两个值
    # （迁移 `0010_import_source`），所以这个映射是穷尽的。
    """
    UPDATE assessment_session
    SET source_type = CASE WHEN source = 'IMPORTED' THEN 'EXTERNAL_FULL_ANSWER' ELSE 'ONLINE' END
    WHERE source_type = 'ONLINE'
    """,
    # 二、会话的学校归属与测评时年龄。学生一定有学校（`student.school_id` 是 NOT NULL
    # 且带外键），所以这条 UPDATE 之后 `school_id` 不可能还是 NULL——
    # 0014 的第一条前置校验就是在验这句话。
    """
    UPDATE assessment_session s
    JOIN student st ON st.id = s.student_id
    SET s.school_id = st.school_id,
        s.age_at_test = COALESCE(s.age_at_test, st.age)
    WHERE s.school_id IS NULL
    """,
    # 三、任务目标的学校快照。
    """
    UPDATE assessment_target t
    JOIN student st ON st.id = t.student_id
    SET t.school_id_snapshot = st.school_id
    WHERE t.school_id_snapshot IS NULL
    """,
    # 四、风险信号类型。**按 `risk_type` 判，不按 `trigger_rule`**：引擎发的
    # `trigger_rule` 是 `KEY_QUESTION_{n}_YES`，而 DDL 那份写的是
    # `KEY_QUESTION_85` / `KEY_QUESTION_97`——它从来没有匹配上过任何一行。
    # `rule_version` 从该会话的结果取，取不到就是 `LEGACY_UNKNOWN`：
    # 那是 DDL 自己定的哨兵值，不是我编的业务码（需求说明书 §15）。
    """
    UPDATE risk_event
    SET signal_type = CASE
            WHEN risk_type IN ('MANUAL_REVIEW_REQUIRED', 'KEY_QUESTION_TRIGGERED')
                THEN 'MANUAL_REVIEW_REQUIRED'
            WHEN risk_type = 'RETEST_RECOMMENDED' THEN 'RETEST_RECOMMENDED'
            WHEN risk_type IN ('HIGH_TOTAL_SCORE', 'HIGH_DIMENSION_SCORE', 'SCREENING_SIGNAL')
                THEN 'SCREENING_SIGNAL'
        END,
        requires_manual_review = CASE
            WHEN risk_type IN ('MANUAL_REVIEW_REQUIRED', 'KEY_QUESTION_TRIGGERED') THEN 1
            WHEN risk_type IN ('RETEST_RECOMMENDED', 'HIGH_TOTAL_SCORE',
                               'HIGH_DIMENSION_SCORE', 'SCREENING_SIGNAL') THEN 0
        END,
        rule_version = COALESCE(
            (SELECT ar.rule_version FROM assessment_result ar
             WHERE ar.session_id = risk_event.session_id LIMIT 1),
            'LEGACY_UNKNOWN')
    WHERE signal_type IS NULL
    """,
    # 五、人工复核挂在谁身上。DDL 第 636 行就是这么写的：复核记录本身不存学生，
    # 学生是从它对应的风险事件推出来的。
    """
    UPDATE manual_review mr
    JOIN risk_event re ON re.id = mr.risk_event_id
    SET mr.student_id = re.student_id
    WHERE mr.student_id IS NULL
    """,
)


def upgrade() -> None:
    _precheck()

    # 二、父表那一侧的六个唯一键。它们每一把都含主键列，所以**永远不可能重复**，
    # 加它们不改变任何既有业务含义；而下面那十张新表里的复合外键指着它们，
    # 放到 0014 去加的话，0013 建表时就会撞 1822 Missing index for constraint。
    op.create_unique_constraint('uq_session_id_student', 'assessment_session', ['id', 'student_id'])
    op.create_unique_constraint('uq_assessment_task_school_id', 'assessment_task', ['id', 'school_id'])
    op.create_unique_constraint('uq_class_school_id', 'class_group', ['school_id', 'id'])
    op.create_unique_constraint('uq_grade_school_id', 'grade', ['school_id', 'id'])
    op.create_unique_constraint('uq_student_school_id', 'student', ['school_id', 'id'])
    op.create_unique_constraint('uq_care_case_id_student', 'student_care_case', ['id', 'student_id'])

    # 三、十张新表，每张表后面紧跟它自己的索引。父先子后：父表还没建就建子表，
    # MySQL 报 1824 Failed to open the referenced table。
    op.create_table('auth_session',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('jti', sa.CHAR(length=36), nullable=False),
    sa.Column('token_type', sa.String(length=16), server_default='ACCESS', nullable=False),
    sa.Column('session_token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=255), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user_account.id'], name='auth_session_ibfk_1'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('jti', name='uq_auth_session_jti'),
    sa.UniqueConstraint('session_token_hash', name='uq_auth_session_token_hash')
    )
    op.create_index('expires_at', 'auth_session', ['expires_at'], unique=False)
    op.create_index('revoked_at', 'auth_session', ['revoked_at'], unique=False)
    op.create_index('user_id', 'auth_session', ['user_id'], unique=False)
    op.create_table('export_job',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('job_no', sa.String(length=64), nullable=False),
    sa.Column('export_type', sa.String(length=64), nullable=False),
    sa.Column('requested_by', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.String(length=255), nullable=False),
    sa.Column('scope_snapshot', sa.JSON(), nullable=False),
    sa.Column('field_policy', sa.JSON(), nullable=False),
    sa.Column('mask_level', sa.String(length=32), server_default='MASKED', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='PENDING', nullable=False),
    sa.Column('file_uri', sa.String(length=1000), nullable=True),
    sa.Column('file_sha256', sa.CHAR(length=64), nullable=True),
    sa.Column('row_count', sa.Integer(), nullable=True),
    sa.Column('download_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('downloaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['requested_by'], ['user_account.id'], name='export_job_ibfk_1'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_no', name='uq_export_job_no')
    )
    op.create_index('ix_export_job_expires_at', 'export_job', ['expires_at'], unique=False)
    op.create_index('ix_export_job_requester_status', 'export_job', ['requested_by', 'status'], unique=False)
    op.create_table('student_roster_import_batch',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('batch_no', sa.String(length=64), nullable=False),
    sa.Column('school_id', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('file_sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('imported_by', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), server_default='PREVIEW', nullable=False),
    sa.Column('total_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('skipped_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['imported_by'], ['user_account.id'], name='student_roster_import_batch_fk_operator'),
    sa.ForeignKeyConstraint(['school_id'], ['school.id'], name='student_roster_import_batch_fk_school'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_no', name='uq_roster_import_batch_no')
    )
    op.create_index('ix_roster_import_operator', 'student_roster_import_batch', ['imported_by'], unique=False)
    op.create_index('ix_roster_import_school_created', 'student_roster_import_batch', ['school_id', 'created_at'], unique=False)
    op.create_table('assessment_import_batch',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_no', sa.String(length=64), nullable=False),
    sa.Column('school_id', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('file_sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('source_system', sa.String(length=128), server_default='UNKNOWN', nullable=False),
    sa.Column('source_timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('tested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('import_mode', sa.String(length=32), server_default='EXTERNAL_FULL_ANSWER', nullable=False),
    sa.Column('conflict_policy', sa.String(length=32), server_default='REQUIRE_REVIEW', nullable=False),
    sa.Column('out_of_scope_policy', sa.String(length=32), server_default='REJECT', nullable=False),
    sa.Column('allow_age_overwrite', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('file_size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('schema_version', sa.String(length=64), nullable=True),
    sa.Column('parser_version', sa.String(length=64), nullable=True),
    sa.Column('duplicate_of_batch_id', sa.Integer(), nullable=True),
    sa.Column('resolution', sa.String(length=32), server_default='NONE', nullable=False),
    sa.Column('total_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('skipped_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_rows', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=32), server_default='PREVIEW', nullable=False),
    sa.Column('imported_by', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['duplicate_of_batch_id'], ['assessment_import_batch.id'], name='assessment_import_batch_ibfk_4'),
    sa.ForeignKeyConstraint(['imported_by'], ['user_account.id'], name='assessment_import_batch_ibfk_2'),
    sa.ForeignKeyConstraint(['school_id'], ['school.id'], name='assessment_import_batch_ibfk_1'),
    sa.ForeignKeyConstraint(['task_id', 'school_id'], ['assessment_task.id', 'assessment_task.school_id'], name='assessment_import_batch_fk_task_school'),
    sa.ForeignKeyConstraint(['task_id'], ['assessment_task.id'], name='assessment_import_batch_ibfk_3'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_no', name='uq_assessment_import_batch_no')
    )
    op.create_index('imported_by', 'assessment_import_batch', ['imported_by'], unique=False)
    op.create_index('ix_assessment_import_school_created', 'assessment_import_batch', ['school_id', 'created_at'], unique=False)
    op.create_index('ix_import_duplicate_of', 'assessment_import_batch', ['duplicate_of_batch_id'], unique=False)
    op.create_index('ix_import_file_hash', 'assessment_import_batch', ['source_system', 'file_sha256'], unique=False)
    op.create_index('task_id', 'assessment_import_batch', ['task_id'], unique=False)
    op.create_index('tested_at', 'assessment_import_batch', ['tested_at'], unique=False)
    op.create_table('assessment_task_scope',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('scope_type', sa.String(length=32), nullable=False),
    sa.Column('school_id', sa.Integer(), nullable=False),
    sa.Column('grade_id', sa.Integer(), nullable=True),
    sa.Column('class_id', sa.Integer(), nullable=True),
    sa.Column('student_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['class_id'], ['class_group.id'], name='assessment_task_scope_ibfk_4'),
    sa.ForeignKeyConstraint(['created_by'], ['user_account.id'], name='assessment_task_scope_ibfk_6'),
    sa.ForeignKeyConstraint(['grade_id'], ['grade.id'], name='assessment_task_scope_ibfk_3'),
    sa.ForeignKeyConstraint(['school_id', 'class_id'], ['class_group.school_id', 'class_group.id'], name='assessment_task_scope_fk_school_class'),
    sa.ForeignKeyConstraint(['school_id', 'grade_id'], ['grade.school_id', 'grade.id'], name='assessment_task_scope_fk_school_grade'),
    sa.ForeignKeyConstraint(['school_id', 'student_id'], ['student.school_id', 'student.id'], name='assessment_task_scope_fk_school_student'),
    sa.ForeignKeyConstraint(['school_id'], ['school.id'], name='assessment_task_scope_ibfk_2'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name='assessment_task_scope_ibfk_5'),
    sa.ForeignKeyConstraint(['task_id', 'school_id'], ['assessment_task.id', 'assessment_task.school_id'], name='assessment_task_scope_fk_task_school'),
    sa.ForeignKeyConstraint(['task_id'], ['assessment_task.id'], name='assessment_task_scope_ibfk_1'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_task_scope_class', 'assessment_task_scope', ['class_id'], unique=False)
    op.create_index('ix_task_scope_grade', 'assessment_task_scope', ['grade_id'], unique=False)
    op.create_index('ix_task_scope_school', 'assessment_task_scope', ['school_id'], unique=False)
    op.create_index('ix_task_scope_student', 'assessment_task_scope', ['student_id'], unique=False)
    op.create_index('ix_task_scope_task', 'assessment_task_scope', ['task_id'], unique=False)
    op.create_table('student_roster_import_row',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('batch_id', sa.BigInteger(), nullable=False),
    sa.Column('row_no', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=True),
    sa.Column('student_no', sa.String(length=64), nullable=True),
    sa.Column('name', sa.String(length=64), nullable=True),
    sa.Column('grade_name', sa.String(length=64), nullable=True),
    sa.Column('class_name', sa.String(length=64), nullable=True),
    sa.Column('gender', sa.String(length=16), nullable=True),
    sa.Column('age', sa.Integer(), nullable=True),
    sa.Column('processing_status', sa.String(length=32), server_default='PENDING', nullable=False),
    sa.Column('conflict_code', sa.String(length=64), nullable=True),
    sa.Column('message', sa.String(length=1000), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['student_roster_import_batch.id'], name='student_roster_import_row_fk_batch'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name='student_roster_import_row_fk_student'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id', 'row_no', name='uq_roster_import_row_no')
    )
    op.create_index('ix_roster_import_row_student', 'student_roster_import_row', ['student_id'], unique=False)
    op.create_table('assessment_import_row',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('row_no', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=True),
    sa.Column('raw_student_no', sa.String(length=64), nullable=True),
    sa.Column('raw_name', sa.String(length=128), nullable=True),
    sa.Column('raw_grade_name', sa.String(length=64), nullable=True),
    sa.Column('raw_class_name', sa.String(length=64), nullable=True),
    sa.Column('raw_age', sa.Integer(), nullable=True),
    sa.Column('normalized_name', sa.String(length=128), nullable=True),
    sa.Column('normalized_grade_name', sa.String(length=64), nullable=True),
    sa.Column('normalized_class_name', sa.String(length=64), nullable=True),
    sa.Column('matched_school_id', sa.Integer(), nullable=True),
    sa.Column('matched_grade_id', sa.Integer(), nullable=True),
    sa.Column('matched_class_id', sa.Integer(), nullable=True),
    sa.Column('match_status', sa.String(length=32), server_default='PENDING', nullable=False),
    sa.Column('match_confidence', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('candidate_student_ids', sa.JSON(), nullable=True),
    sa.Column('tested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_timezone', sa.String(length=64), nullable=True),
    sa.Column('processing_status', sa.String(length=32), server_default='PENDING', nullable=False),
    sa.Column('conflict_code', sa.String(length=64), nullable=True),
    sa.Column('resolution', sa.String(length=32), nullable=True),
    sa.Column('out_of_scope_reason', sa.String(length=255), nullable=True),
    sa.Column('age_before', sa.Integer(), nullable=True),
    sa.Column('age_after', sa.Integer(), nullable=True),
    sa.Column('age_resolution', sa.String(length=32), nullable=True),
    sa.Column('resolved_by', sa.Integer(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('session_id', sa.Integer(), nullable=True),
    sa.Column('external_result_record_id', sa.BigInteger(), nullable=True),
    sa.Column('message', sa.String(length=1000), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['assessment_import_batch.id'], name='assessment_import_row_ibfk_1'),
    sa.ForeignKeyConstraint(['matched_school_id', 'matched_class_id'], ['class_group.school_id', 'class_group.id'], name='assessment_import_row_fk_matched_school_class'),
    sa.ForeignKeyConstraint(['matched_school_id', 'matched_grade_id'], ['grade.school_id', 'grade.id'], name='assessment_import_row_fk_matched_school_grade'),
    sa.ForeignKeyConstraint(['resolved_by'], ['user_account.id'], name='assessment_import_row_ibfk_4'),
    sa.ForeignKeyConstraint(['session_id', 'student_id'], ['assessment_session.id', 'assessment_session.student_id'], name='assessment_import_row_fk_session_student'),
    sa.ForeignKeyConstraint(['session_id'], ['assessment_session.id'], name='assessment_import_row_ibfk_3'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name='assessment_import_row_ibfk_2'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id', 'row_no', name='uq_import_row_batch_no')
    )
    op.create_index('ix_import_row_external_record', 'assessment_import_row', ['external_result_record_id'], unique=False)
    op.create_index('ix_import_row_match_status', 'assessment_import_row', ['batch_id', 'match_status'], unique=False)
    op.create_index('ix_import_row_matched_grade_class', 'assessment_import_row', ['matched_school_id', 'matched_grade_id', 'matched_class_id'], unique=False)
    op.create_index('ix_import_row_normalized_match', 'assessment_import_row', ['normalized_grade_name', 'normalized_class_name', 'normalized_name'], unique=False)
    op.create_index('session_id', 'assessment_import_row', ['session_id'], unique=False)
    op.create_index('student_id', 'assessment_import_row', ['student_id'], unique=False)
    op.create_table('care_case_event',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('care_case_id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('from_status', sa.String(length=32), nullable=True),
    sa.Column('to_status', sa.String(length=32), nullable=True),
    sa.Column('operator_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.String(length=255), nullable=True),
    sa.Column('confirmed_facts', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['care_case_id', 'student_id'], ['student_care_case.id', 'student_care_case.student_id'], name='care_case_event_fk_case_student'),
    sa.ForeignKeyConstraint(['care_case_id'], ['student_care_case.id'], name='care_case_event_ibfk_1'),
    sa.ForeignKeyConstraint(['operator_id'], ['user_account.id'], name='care_case_event_ibfk_3'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name='care_case_event_ibfk_2'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('care_case_id', 'care_case_event', ['care_case_id'], unique=False)
    op.create_index('event_type_created_at', 'care_case_event', ['event_type', 'created_at'], unique=False)
    op.create_index('operator_id', 'care_case_event', ['operator_id'], unique=False)
    op.create_index('student_id', 'care_case_event', ['student_id'], unique=False)
    op.create_table('assessment_external_result',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('batch_id', sa.Integer(), nullable=False),
    sa.Column('row_id', sa.Integer(), nullable=False),
    sa.Column('school_id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=True),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('source_system', sa.String(length=128), nullable=False),
    sa.Column('external_result_id', sa.String(length=128), nullable=True),
    sa.Column('source_type', sa.String(length=32), server_default='EXTERNAL_SUMMARY', nullable=False),
    sa.Column('scale_code', sa.String(length=64), nullable=True),
    sa.Column('scale_version', sa.String(length=64), nullable=True),
    sa.Column('rule_version', sa.String(length=64), nullable=True),
    sa.Column('tested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('validity_score', sa.Integer(), nullable=True),
    sa.Column('total_score', sa.Integer(), nullable=True),
    sa.Column('dimension_scores_json', sa.JSON(), nullable=True),
    sa.Column('result_payload_json', sa.JSON(), nullable=True),
    sa.Column('verification_status', sa.String(length=32), server_default='PENDING', nullable=False),
    sa.Column('applied_session_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['applied_session_id', 'student_id'], ['assessment_session.id', 'assessment_session.student_id'], name='assessment_external_result_fk_session_student'),
    sa.ForeignKeyConstraint(['applied_session_id'], ['assessment_session.id'], name='assessment_external_result_fk_session'),
    sa.ForeignKeyConstraint(['batch_id'], ['assessment_import_batch.id'], name='assessment_external_result_fk_batch'),
    sa.ForeignKeyConstraint(['row_id'], ['assessment_import_row.id'], name='assessment_external_result_fk_row'),
    sa.ForeignKeyConstraint(['school_id', 'student_id'], ['student.school_id', 'student.id'], name='assessment_external_result_fk_student_school'),
    sa.ForeignKeyConstraint(['school_id'], ['school.id'], name='assessment_external_result_fk_school'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name='assessment_external_result_fk_student'),
    sa.ForeignKeyConstraint(['task_id', 'school_id'], ['assessment_task.id', 'assessment_task.school_id'], name='assessment_external_result_fk_task_school'),
    sa.ForeignKeyConstraint(['task_id'], ['assessment_task.id'], name='assessment_external_result_fk_task'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id', 'row_id', name='uq_external_result_batch_row'),
    sa.UniqueConstraint('id', 'student_id', name='uq_external_result_id_student'),
    sa.UniqueConstraint('source_system', 'external_result_id', name='uq_external_result_source_key')
    )
    op.create_index('ix_external_result_task_student', 'assessment_external_result', ['task_id', 'student_id'], unique=False)
    op.create_index('ix_external_result_verification', 'assessment_external_result', ['verification_status'], unique=False)
    op.create_table('student_age_change_log',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('roster_import_batch_id', sa.BigInteger(), nullable=True),
    sa.Column('assessment_import_batch_id', sa.Integer(), nullable=True),
    sa.Column('assessment_import_row_id', sa.Integer(), nullable=True),
    sa.Column('old_age', sa.Integer(), nullable=True),
    sa.Column('new_age', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=128), nullable=False),
    sa.Column('changed_by', sa.Integer(), nullable=False),
    sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assessment_import_batch_id'], ['assessment_import_batch.id'], name='student_age_change_log_fk_assessment_batch'),
    sa.ForeignKeyConstraint(['assessment_import_row_id'], ['assessment_import_row.id'], name='student_age_change_log_fk_assessment_row'),
    sa.ForeignKeyConstraint(['changed_by'], ['user_account.id'], name='student_age_change_log_fk_operator'),
    sa.ForeignKeyConstraint(['roster_import_batch_id'], ['student_roster_import_batch.id'], name='student_age_change_log_fk_roster_batch'),
    sa.ForeignKeyConstraint(['student_id'], ['student.id'], name='student_age_change_log_fk_student'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_age_change_assessment_row', 'student_age_change_log', ['assessment_import_row_id'], unique=False)
    op.create_index('ix_age_change_student_time', 'student_age_change_log', ['student_id', 'changed_at'], unique=False)

    # 四、环上的那条外键，**只能排到这里**：
    # `assessment_import_row` 与 `assessment_external_result` 互相指着，
    # 两条 CREATE TABLE 谁先谁后都建不出来，只能两张都建好再 ALTER。
    op.create_foreign_key('assessment_import_row_fk_external_record',
                    'assessment_import_row', 'assessment_external_result',
                    ['external_result_record_id'], ['id'])

    # 五、既有表上的新列与新索引。**新列一律追加在表末尾**（不带 AFTER）——
    # 手工那份 DDL 用了 AFTER，所以「迁移建的库」与「手工 DDL 建的库」列序不同；
    # 列序不影响任何查询，但比对 information_schema 时要按列名关联而不是按位置。
    op.add_column('assessment_session', sa.Column('tested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('assessment_session', sa.Column('tested_at_source', sa.String(length=32), server_default='PENDING_VERIFICATION', nullable=False))
    op.add_column('assessment_session', sa.Column('import_batch_id', sa.Integer(), nullable=True))
    op.add_column('assessment_session', sa.Column('attempt_no', sa.Integer(), server_default='1', nullable=False))
    op.add_column('assessment_session', sa.Column('school_id', sa.Integer(), nullable=True))
    op.add_column('assessment_session', sa.Column('source_type', sa.String(length=32), server_default='ONLINE', nullable=False))
    op.add_column('assessment_session', sa.Column('external_source_system', sa.String(length=128), nullable=True))
    op.add_column('assessment_session', sa.Column('external_result_id', sa.String(length=128), nullable=True))
    op.add_column('assessment_session', sa.Column('conflict_status', sa.String(length=32), server_default='NONE', nullable=False))
    op.add_column('assessment_session', sa.Column('is_effective', sa.Boolean(), server_default='1', nullable=False))
    op.add_column('assessment_session', sa.Column('supersedes_session_id', sa.Integer(), nullable=True))
    op.add_column('assessment_session', sa.Column('age_at_test', sa.Integer(), nullable=True))
    op.add_column('assessment_session', sa.Column('answer_snapshot_hash', sa.CHAR(length=64), nullable=True))
    op.add_column('assessment_session', sa.Column('answer_hash_algorithm', sa.String(length=32), nullable=True))
    op.add_column('assessment_session', sa.Column('calculation_status', sa.String(length=32), server_default='PENDING', nullable=False))
    op.add_column('assessment_session', sa.Column('calculation_error', sa.String(length=1000), nullable=True))
    op.create_index('ix_session_calculation_status', 'assessment_session', ['calculation_status'], unique=False)
    op.create_index('ix_session_conflict_status', 'assessment_session', ['conflict_status'], unique=False)
    op.create_index('ix_session_external_result', 'assessment_session', ['external_source_system', 'external_result_id'], unique=False)
    op.create_index('ix_session_import_batch_id', 'assessment_session', ['import_batch_id'], unique=False)
    op.create_index('ix_session_school_student', 'assessment_session', ['school_id', 'student_id'], unique=False)
    op.create_index('ix_session_student_tested_at', 'assessment_session', ['student_id', 'tested_at'], unique=False)
    op.add_column('assessment_target', sa.Column('school_id_snapshot', sa.Integer(), nullable=True))
    op.add_column('assessment_target', sa.Column('student_no_snapshot', sa.String(length=64), nullable=True))
    op.add_column('assessment_target', sa.Column('student_name_snapshot', sa.String(length=64), nullable=True))
    op.add_column('assessment_target', sa.Column('grade_name_snapshot', sa.String(length=64), nullable=True))
    op.add_column('assessment_target', sa.Column('class_name_snapshot', sa.String(length=64), nullable=True))
    op.add_column('assessment_target', sa.Column('gender_snapshot', sa.String(length=16), nullable=True))
    op.add_column('assessment_target', sa.Column('age_snapshot', sa.Integer(), nullable=True))
    op.add_column('assessment_target', sa.Column('participation_disposition', sa.String(length=32), server_default='REQUIRED', nullable=False))
    op.add_column('assessment_target', sa.Column('disposition_reason', sa.String(length=128), nullable=True))
    op.add_column('assessment_target', sa.Column('disposition_note', sa.String(length=1000), nullable=True))
    op.add_column('assessment_target', sa.Column('marked_by', sa.Integer(), nullable=True))
    op.add_column('assessment_target', sa.Column('marked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('assessment_target', sa.Column('target_source', sa.String(length=32), server_default='TASK_SCOPE', nullable=False))
    op.add_column('assessment_target', sa.Column('supplemented_from_batch_id', sa.Integer(), nullable=True))
    op.add_column('assessment_target', sa.Column('effective_session_id', sa.Integer(), nullable=True))
    op.add_column('assessment_target', sa.Column('effective_external_result_id', sa.BigInteger(), nullable=True))
    op.create_index('ix_target_effective_external', 'assessment_target', ['effective_external_result_id'], unique=False)
    op.create_index('ix_target_effective_session', 'assessment_target', ['effective_session_id'], unique=False)
    op.create_index('ix_target_participation', 'assessment_target', ['task_id', 'participation_disposition'], unique=False)
    op.create_index('ix_target_task_status', 'assessment_target', ['task_id', 'status'], unique=False)
    op.add_column('audit_log', sa.Column('event_id', sa.CHAR(length=36), nullable=True))
    op.add_column('audit_log', sa.Column('actor_account_snapshot', sa.String(length=128), nullable=True))
    op.add_column('audit_log', sa.Column('request_id', sa.String(length=64), nullable=True))
    op.add_column('audit_log', sa.Column('detail_json', sa.JSON(), nullable=True))
    op.add_column('audit_log', sa.Column('result_code', sa.String(length=64), nullable=True))
    op.add_column('audit_log', sa.Column('audit_hash', sa.CHAR(length=64), nullable=True))
    op.create_index('ix_audit_created_actor_action', 'audit_log', ['created_at', 'actor_role', 'action'], unique=False)
    op.create_index('ix_audit_request_id', 'audit_log', ['request_id'], unique=False)
    op.add_column('family_contact_record', sa.Column('care_case_id', sa.Integer(), nullable=True))
    op.create_index('ix_family_contact_case', 'family_contact_record', ['care_case_id'], unique=False)
    op.create_index('ix_family_contact_next_date', 'family_contact_record', ['next_contact_date'], unique=False)
    op.add_column('follow_up_record', sa.Column('care_case_id', sa.Integer(), nullable=True))
    op.create_index('ix_follow_up_case_date', 'follow_up_record', ['care_case_id', 'next_follow_up_date'], unique=False)
    op.create_index('ix_follow_up_status_date', 'follow_up_record', ['status', 'next_follow_up_date'], unique=False)
    op.add_column('manual_review', sa.Column('care_case_id', sa.Integer(), nullable=True))
    op.add_column('manual_review', sa.Column('student_id', sa.Integer(), nullable=True))
    op.create_index('ix_manual_review_care_case', 'manual_review', ['care_case_id'], unique=False)
    op.create_index('ix_manual_review_student', 'manual_review', ['student_id'], unique=False)
    op.add_column('retest_plan', sa.Column('care_case_id', sa.Integer(), nullable=True))
    op.add_column('retest_plan', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('retest_plan', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('retest_plan', sa.Column('cancel_reason', sa.String(length=255), nullable=True))
    op.create_index('ix_retest_case_status_date', 'retest_plan', ['care_case_id', 'status', 'planned_date'], unique=False)
    op.add_column('risk_event', sa.Column('signal_type', sa.String(length=64), nullable=True))
    op.add_column('risk_event', sa.Column('requires_manual_review', sa.Boolean(), server_default='0', nullable=False))
    op.add_column('risk_event', sa.Column('rule_version', sa.String(length=64), server_default='LEGACY_UNKNOWN', nullable=False))
    op.create_index('ix_risk_event_status_created_at', 'risk_event', ['status', 'created_at'], unique=False)
    op.create_index('ix_scale_rule_status', 'scale_rule', ['scale_id', 'status'], unique=False)
    op.add_column('student_care_case', sa.Column('closed_by', sa.Integer(), nullable=True))
    op.add_column('student_care_case', sa.Column('reopened_by', sa.Integer(), nullable=True))
    op.add_column('student_care_case', sa.Column('reopen_reason', sa.String(length=255), nullable=True))
    op.add_column('student_care_case', sa.Column('last_reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('student_care_case', sa.Column('case_version', sa.Integer(), server_default='1', nullable=False))
    op.create_index('ix_care_case_status_owner_updated', 'student_care_case', ['status', 'owner_id', 'updated_at'], unique=False)

    # 六、回填。**排在最后**：它依赖上面每一列都已经在。
    for _statement in BACKFILLS:
        op.execute(_statement)


def downgrade() -> None:
    # 四、先摘掉环上的那条外键。
    op.drop_constraint('assessment_import_row_fk_external_record',
                   'assessment_import_row', type_='foreignkey')

    # 五、既有表上的新列与新索引。
    # 这一步之所以安全，是因为 0014 的 downgrade **先跑**（alembic 按逆序回退），
    # 那时指着这些列的外键与唯一键都已经摘掉了。
    op.drop_index('ix_care_case_status_owner_updated', table_name='student_care_case')
    op.drop_column('student_care_case', 'case_version')
    op.drop_column('student_care_case', 'last_reviewed_at')
    op.drop_column('student_care_case', 'reopen_reason')
    op.drop_column('student_care_case', 'reopened_by')
    op.drop_column('student_care_case', 'closed_by')
    # `scale_rule.scale_id` 上那条按列名的索引，是刚才被 `ix_scale_rule_status` 顶掉的，
    # 而 `scale_rule` 上还有外键指着这一列 —— 不与 DROP 写在同一条 ALTER 里
    # 就是 errno 1553。
    op.execute("ALTER TABLE scale_rule DROP INDEX ix_scale_rule_status, ADD INDEX scale_id (scale_id)")
    op.drop_index('ix_risk_event_status_created_at', table_name='risk_event')
    op.drop_column('risk_event', 'rule_version')
    op.drop_column('risk_event', 'requires_manual_review')
    op.drop_column('risk_event', 'signal_type')
    op.drop_index('ix_retest_case_status_date', table_name='retest_plan')
    op.drop_column('retest_plan', 'cancel_reason')
    op.drop_column('retest_plan', 'cancelled_at')
    op.drop_column('retest_plan', 'completed_at')
    op.drop_column('retest_plan', 'care_case_id')
    op.drop_index('ix_manual_review_student', table_name='manual_review')
    op.drop_index('ix_manual_review_care_case', table_name='manual_review')
    op.drop_column('manual_review', 'student_id')
    op.drop_column('manual_review', 'care_case_id')
    op.drop_index('ix_follow_up_status_date', table_name='follow_up_record')
    op.drop_index('ix_follow_up_case_date', table_name='follow_up_record')
    op.drop_column('follow_up_record', 'care_case_id')
    op.drop_index('ix_family_contact_next_date', table_name='family_contact_record')
    op.drop_index('ix_family_contact_case', table_name='family_contact_record')
    op.drop_column('family_contact_record', 'care_case_id')
    op.drop_index('ix_audit_request_id', table_name='audit_log')
    op.drop_index('ix_audit_created_actor_action', table_name='audit_log')
    op.drop_column('audit_log', 'audit_hash')
    op.drop_column('audit_log', 'result_code')
    op.drop_column('audit_log', 'detail_json')
    op.drop_column('audit_log', 'request_id')
    op.drop_column('audit_log', 'actor_account_snapshot')
    op.drop_column('audit_log', 'event_id')
    op.drop_index('ix_target_task_status', table_name='assessment_target')
    op.drop_index('ix_target_participation', table_name='assessment_target')
    op.drop_index('ix_target_effective_session', table_name='assessment_target')
    op.drop_index('ix_target_effective_external', table_name='assessment_target')
    op.drop_column('assessment_target', 'effective_external_result_id')
    op.drop_column('assessment_target', 'effective_session_id')
    op.drop_column('assessment_target', 'supplemented_from_batch_id')
    op.drop_column('assessment_target', 'target_source')
    op.drop_column('assessment_target', 'marked_at')
    op.drop_column('assessment_target', 'marked_by')
    op.drop_column('assessment_target', 'disposition_note')
    op.drop_column('assessment_target', 'disposition_reason')
    op.drop_column('assessment_target', 'participation_disposition')
    op.drop_column('assessment_target', 'age_snapshot')
    op.drop_column('assessment_target', 'gender_snapshot')
    op.drop_column('assessment_target', 'class_name_snapshot')
    op.drop_column('assessment_target', 'grade_name_snapshot')
    op.drop_column('assessment_target', 'student_name_snapshot')
    op.drop_column('assessment_target', 'student_no_snapshot')
    op.drop_column('assessment_target', 'school_id_snapshot')
    # `assessment_session.student_id` 上那条按列名的索引，是刚才被 `ix_session_student_tested_at` 顶掉的，
    # 而 `assessment_session` 上还有外键指着这一列 —— 不与 DROP 写在同一条 ALTER 里
    # 就是 errno 1553。
    op.execute("ALTER TABLE assessment_session DROP INDEX ix_session_student_tested_at, ADD INDEX student_id (student_id)")
    op.drop_index('ix_session_school_student', table_name='assessment_session')
    op.drop_index('ix_session_import_batch_id', table_name='assessment_session')
    op.drop_index('ix_session_external_result', table_name='assessment_session')
    op.drop_index('ix_session_conflict_status', table_name='assessment_session')
    op.drop_index('ix_session_calculation_status', table_name='assessment_session')
    op.drop_column('assessment_session', 'calculation_error')
    op.drop_column('assessment_session', 'calculation_status')
    op.drop_column('assessment_session', 'answer_hash_algorithm')
    op.drop_column('assessment_session', 'answer_snapshot_hash')
    op.drop_column('assessment_session', 'age_at_test')
    op.drop_column('assessment_session', 'supersedes_session_id')
    op.drop_column('assessment_session', 'is_effective')
    op.drop_column('assessment_session', 'conflict_status')
    op.drop_column('assessment_session', 'external_result_id')
    op.drop_column('assessment_session', 'external_source_system')
    op.drop_column('assessment_session', 'source_type')
    op.drop_column('assessment_session', 'school_id')
    op.drop_column('assessment_session', 'attempt_no')
    op.drop_column('assessment_session', 'import_batch_id')
    op.drop_column('assessment_session', 'tested_at_source')
    op.drop_column('assessment_session', 'tested_at')

    # 三、十张新表，子先父后。两两之间的外键会随表一起消失，不需要显式 drop。
    op.drop_table('student_age_change_log')
    op.drop_table('assessment_external_result')
    op.drop_table('care_case_event')
    op.drop_table('assessment_import_row')
    op.drop_table('student_roster_import_row')
    op.drop_table('assessment_task_scope')
    op.drop_table('assessment_import_batch')
    op.drop_table('student_roster_import_batch')
    op.drop_table('export_job')
    op.drop_table('auth_session')

    # 二、父表那一侧的六个唯一键，最后摘。
    op.drop_constraint('uq_care_case_id_student', 'student_care_case', type_='unique')
    op.drop_constraint('uq_student_school_id', 'student', type_='unique')
    # `grade.school_id` 上那条按列名的索引，是刚才被 `uq_grade_school_id` 顶掉的，
    # 而 `grade` 上还有外键指着这一列 —— 不与 DROP 写在同一条 ALTER 里
    # 就是 errno 1553。
    op.execute("ALTER TABLE grade DROP INDEX uq_grade_school_id, ADD INDEX school_id (school_id)")
    # `class_group.school_id` 上那条按列名的索引，是刚才被 `uq_class_school_id` 顶掉的，
    # 而 `class_group` 上还有外键指着这一列 —— 不与 DROP 写在同一条 ALTER 里
    # 就是 errno 1553。
    op.execute("ALTER TABLE class_group DROP INDEX uq_class_school_id, ADD INDEX school_id (school_id)")
    op.drop_constraint('uq_assessment_task_school_id', 'assessment_task', type_='unique')
    op.drop_constraint('uq_session_id_student', 'assessment_session', type_='unique')
