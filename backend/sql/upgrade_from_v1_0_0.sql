-- ==============================================================================
-- 心晴 · 数据库增量升级脚本
--
-- 从  V1.0.0（迁移 0012_drop_care_case_unique）
-- 到  V1.1.3（迁移 0019_total_includes_validity）
--
-- 由 deploy/build_migration_sql.py 生成，**不要手工编辑**：它的数据源是
-- 链上 7 条迁移各自的 PRECHECKS 常量与 alembic 的离线渲染。
-- 改了迁移就重跑一次 `make db-upgrade-sql`。
-- ==============================================================================

-- 【怎么执行】
--
--   第一步 · 先备份。这一步没有替代品：
--       mysqldump -h HOST -u USER -p --default-character-set=utf8mb4 \
--         --single-transaction DB > backup_$(date +%Y%m%d).sql
--
--   第二步 · 把整个文件交给 mysql 执行。文件按迁移分成 7 条，每条都是
--       「先【检查】、后【DDL】」两小段：
--             第 1 条 / 共 7 条：0013_xxx
--               【检查】…   ← 每一条都必须返回 Empty set
--               【DDL】…    ← 这条迁移真正动手的地方
--       ★ 一定要**按这个次序**：后面的迁移会读前面刚加上去的列，
--         把所有检查提到最前面会撞 `1054 Unknown column`。
--       ★ 一共 13 条检查，下面几条会替你拦住：任何一条检查打出数据，
--         客户端都会当场中断，那时**一行 DDL 都还没跑**，你的库还是原样。
--         看到 `ERROR 1644 (45000)` 就说明拦住了——把上面那条 SELECT 打出来的数据
--         反馈给维护者，不要往下执行。它说明这个库里存在 DDL 挡不住的数据形状，
--         而 MySQL 的 DDL 不在事务里，硬跑下去会留下「改了一半」的库。
--       ★ 用 mysql 命令行客户端执行，并在第一条出错的语句处停下
--         （命令行默认就是这样；图形工具要确认它没有开「出错继续」）。
--              mysql -h HOST -u USER -p --default-character-set=utf8mb4 DB < 本文件
--
--   第三步 · 核对：
--       SELECT version_num FROM alembic_version;
--       应当是 0019_total_includes_validity
--
--   注意三件事：
--   ① 这个文件是 UTF-8、含中文注释，**必须**带 --default-character-set=utf8mb4，
--      否则中文会按连接编码解成乱码（注释无所谓，但你要读的就是它）。
--   ② 【只该执行一次】。跑第二遍会撞 Duplicate column / Duplicate key name 之类的错，
--      那是正常的，不是文件坏了。
--   ③ 开头建了一个临时用的存储过程（就是下面那道门），结尾删掉。它需要
--      CREATE ROUTINE 权限（root 有）；建不出来时脚本会停在那几行——
--      那是安全的一侧，因为它一行都还没执行。
-- ==============================================================================

-- 【那道门是什么】
--
--   每条【检查】后面都跟着两行：一句 `SELECT EXISTS(…) INTO @xlp_hits`
--   与一次 `CALL xlp_check_empty(@xlp_hits, '…')`。
--
--   检查本身只是一句 SELECT：它把可疑的数据**打出来给你看**，而 mysql 客户端不会
--   因为你看见了就停下——没有这道门时，脚本会带着这些问题继续往下跑 DDL，等到
--   某条 ALTER 真正撞上时才报一句与真正原因无关的英文错误，而那时库已经改了一半
--   （MySQL 的 DDL 不在事务里，中途失败不会回滚）。
--
--   所以紧跟的这两行把**同一条检查**再问一遍「有没有」——检查 SQL 原样套进
--   `EXISTS(...)`，一个字不改——有就 SIGNAL，客户端当场中断，一行 DDL 都还没跑。
-- ------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS xlp_check_empty;
DELIMITER //
CREATE PROCEDURE xlp_check_empty(IN p_hits INT, IN p_what VARCHAR(255))
BEGIN
  IF p_hits > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = p_what;
  END IF;
END //
DELIMITER ;
-- ------------------------------------------------------------------------------


-- ==============================================================================
-- 第 1 条 / 共 7 条：0013_v12_expand
-- ==============================================================================

-- 【检查】1 条。**每一条都必须返回 Empty set**。
-- 这些 SQL 与迁移里的 PRECHECKS 是同一份（同一个常量，由生成器搬过来），
-- 迁移自己在真跑之前也会做同样这一遍。提前摆在这里，是为了让你在动手之前看到结果。
-- 每条检查后面紧跟一次 CALL：有数据就当场中断，你会看到 `ERROR 1644 (45000)`
-- 与检查的编号——那时一行 DDL 都还没跑，库还是原样。

-- ------------------------------------------------------------------------------
-- 检查 [1/1]
-- risk_event.risk_type 里有认不出的值，signal_type 回填不出来（0014 会把它收紧成 NOT NULL，那时就晚了）
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT id, risk_type, trigger_rule
FROM risk_event
WHERE risk_type NOT IN (
    'MANUAL_REVIEW_REQUIRED', 'KEY_QUESTION_TRIGGERED',
    'RETEST_RECOMMENDED', 'HIGH_TOTAL_SCORE',
    'HIGH_DIMENSION_SCORE', 'SCREENING_SIGNAL')
LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT id, risk_type, trigger_rule
FROM risk_event
WHERE risk_type NOT IN (
    'MANUAL_REVIEW_REQUIRED', 'KEY_QUESTION_TRIGGERED',
    'RETEST_RECOMMENDED', 'HIGH_TOTAL_SCORE',
    'HIGH_DIMENSION_SCORE', 'SCREENING_SIGNAL')
LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [1/1] 未通过：见上面这条 SELECT 打出来的数据');

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0013_v12_expand。
-- ------------------------------------------------------------------------------
-- Running upgrade 0012_drop_care_case_unique -> 0013_v12_expand

ALTER TABLE assessment_session ADD CONSTRAINT uq_session_id_student UNIQUE (id, student_id);

ALTER TABLE assessment_task ADD CONSTRAINT uq_assessment_task_school_id UNIQUE (id, school_id);

ALTER TABLE class_group ADD CONSTRAINT uq_class_school_id UNIQUE (school_id, id);

ALTER TABLE grade ADD CONSTRAINT uq_grade_school_id UNIQUE (school_id, id);

ALTER TABLE student ADD CONSTRAINT uq_student_school_id UNIQUE (school_id, id);

ALTER TABLE student_care_case ADD CONSTRAINT uq_care_case_id_student UNIQUE (id, student_id);

CREATE TABLE auth_session (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    user_id INTEGER NOT NULL, 
    jti CHAR(36) NOT NULL, 
    token_type VARCHAR(16) NOT NULL DEFAULT 'ACCESS', 
    session_token_hash CHAR(64) NOT NULL, 
    issued_at DATETIME NOT NULL, 
    expires_at DATETIME NOT NULL, 
    revoked_at DATETIME, 
    revoked_reason VARCHAR(255), 
    last_seen_at DATETIME, 
    ip VARCHAR(64), 
    user_agent VARCHAR(255), 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT auth_session_ibfk_1 FOREIGN KEY(user_id) REFERENCES user_account (id), 
    CONSTRAINT uq_auth_session_jti UNIQUE (jti), 
    CONSTRAINT uq_auth_session_token_hash UNIQUE (session_token_hash)
);

CREATE INDEX expires_at ON auth_session (expires_at);

CREATE INDEX revoked_at ON auth_session (revoked_at);

CREATE INDEX user_id ON auth_session (user_id);

CREATE TABLE export_job (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    job_no VARCHAR(64) NOT NULL, 
    export_type VARCHAR(64) NOT NULL, 
    requested_by INTEGER NOT NULL, 
    purpose VARCHAR(255) NOT NULL, 
    scope_snapshot JSON NOT NULL, 
    field_policy JSON NOT NULL, 
    mask_level VARCHAR(32) NOT NULL DEFAULT 'MASKED', 
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING', 
    file_uri VARCHAR(1000), 
    file_sha256 CHAR(64), 
    row_count INTEGER, 
    download_count INTEGER NOT NULL DEFAULT '0', 
    expires_at DATETIME, 
    downloaded_at DATETIME, 
    revoked_at DATETIME, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    updated_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT export_job_ibfk_1 FOREIGN KEY(requested_by) REFERENCES user_account (id), 
    CONSTRAINT uq_export_job_no UNIQUE (job_no)
);

CREATE INDEX ix_export_job_expires_at ON export_job (expires_at);

CREATE INDEX ix_export_job_requester_status ON export_job (requested_by, status);

CREATE TABLE student_roster_import_batch (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    batch_no VARCHAR(64) NOT NULL, 
    school_id INTEGER NOT NULL, 
    file_name VARCHAR(255) NOT NULL, 
    file_sha256 CHAR(64) NOT NULL, 
    imported_by INTEGER NOT NULL, 
    status VARCHAR(32) NOT NULL DEFAULT 'PREVIEW', 
    total_rows INTEGER NOT NULL DEFAULT '0', 
    created_rows INTEGER NOT NULL DEFAULT '0', 
    updated_rows INTEGER NOT NULL DEFAULT '0', 
    skipped_rows INTEGER NOT NULL DEFAULT '0', 
    error_rows INTEGER NOT NULL DEFAULT '0', 
    created_at DATETIME NOT NULL DEFAULT now(), 
    updated_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT student_roster_import_batch_fk_operator FOREIGN KEY(imported_by) REFERENCES user_account (id), 
    CONSTRAINT student_roster_import_batch_fk_school FOREIGN KEY(school_id) REFERENCES school (id), 
    CONSTRAINT uq_roster_import_batch_no UNIQUE (batch_no)
);

CREATE INDEX ix_roster_import_operator ON student_roster_import_batch (imported_by);

CREATE INDEX ix_roster_import_school_created ON student_roster_import_batch (school_id, created_at);

CREATE TABLE assessment_import_batch (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    batch_no VARCHAR(64) NOT NULL, 
    school_id INTEGER NOT NULL, 
    file_name VARCHAR(255) NOT NULL, 
    file_sha256 CHAR(64) NOT NULL, 
    source_system VARCHAR(128) NOT NULL DEFAULT 'UNKNOWN', 
    source_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC', 
    tested_at DATETIME, 
    import_mode VARCHAR(32) NOT NULL DEFAULT 'EXTERNAL_FULL_ANSWER', 
    conflict_policy VARCHAR(32) NOT NULL DEFAULT 'REQUIRE_REVIEW', 
    out_of_scope_policy VARCHAR(32) NOT NULL DEFAULT 'REJECT', 
    allow_age_overwrite BOOL NOT NULL DEFAULT '0', 
    file_size_bytes BIGINT, 
    schema_version VARCHAR(64), 
    parser_version VARCHAR(64), 
    duplicate_of_batch_id INTEGER, 
    resolution VARCHAR(32) NOT NULL DEFAULT 'NONE', 
    total_rows INTEGER NOT NULL DEFAULT '0', 
    created_rows INTEGER NOT NULL DEFAULT '0', 
    updated_rows INTEGER NOT NULL DEFAULT '0', 
    skipped_rows INTEGER NOT NULL DEFAULT '0', 
    error_rows INTEGER NOT NULL DEFAULT '0', 
    status VARCHAR(32) NOT NULL DEFAULT 'PREVIEW', 
    imported_by INTEGER NOT NULL, 
    task_id INTEGER, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    updated_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT assessment_import_batch_ibfk_4 FOREIGN KEY(duplicate_of_batch_id) REFERENCES assessment_import_batch (id), 
    CONSTRAINT assessment_import_batch_ibfk_2 FOREIGN KEY(imported_by) REFERENCES user_account (id), 
    CONSTRAINT assessment_import_batch_ibfk_1 FOREIGN KEY(school_id) REFERENCES school (id), 
    CONSTRAINT assessment_import_batch_fk_task_school FOREIGN KEY(task_id, school_id) REFERENCES assessment_task (id, school_id), 
    CONSTRAINT assessment_import_batch_ibfk_3 FOREIGN KEY(task_id) REFERENCES assessment_task (id), 
    CONSTRAINT uq_assessment_import_batch_no UNIQUE (batch_no)
);

CREATE INDEX imported_by ON assessment_import_batch (imported_by);

CREATE INDEX ix_assessment_import_school_created ON assessment_import_batch (school_id, created_at);

CREATE INDEX ix_import_duplicate_of ON assessment_import_batch (duplicate_of_batch_id);

CREATE INDEX ix_import_file_hash ON assessment_import_batch (source_system, file_sha256);

CREATE INDEX task_id ON assessment_import_batch (task_id);

CREATE INDEX tested_at ON assessment_import_batch (tested_at);

CREATE TABLE assessment_task_scope (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    task_id INTEGER NOT NULL, 
    scope_type VARCHAR(32) NOT NULL, 
    school_id INTEGER NOT NULL, 
    grade_id INTEGER, 
    class_id INTEGER, 
    student_id INTEGER, 
    created_by INTEGER NOT NULL, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT assessment_task_scope_ibfk_4 FOREIGN KEY(class_id) REFERENCES class_group (id), 
    CONSTRAINT assessment_task_scope_ibfk_6 FOREIGN KEY(created_by) REFERENCES user_account (id), 
    CONSTRAINT assessment_task_scope_ibfk_3 FOREIGN KEY(grade_id) REFERENCES grade (id), 
    CONSTRAINT assessment_task_scope_fk_school_class FOREIGN KEY(school_id, class_id) REFERENCES class_group (school_id, id), 
    CONSTRAINT assessment_task_scope_fk_school_grade FOREIGN KEY(school_id, grade_id) REFERENCES grade (school_id, id), 
    CONSTRAINT assessment_task_scope_fk_school_student FOREIGN KEY(school_id, student_id) REFERENCES student (school_id, id), 
    CONSTRAINT assessment_task_scope_ibfk_2 FOREIGN KEY(school_id) REFERENCES school (id), 
    CONSTRAINT assessment_task_scope_ibfk_5 FOREIGN KEY(student_id) REFERENCES student (id), 
    CONSTRAINT assessment_task_scope_fk_task_school FOREIGN KEY(task_id, school_id) REFERENCES assessment_task (id, school_id), 
    CONSTRAINT assessment_task_scope_ibfk_1 FOREIGN KEY(task_id) REFERENCES assessment_task (id)
);

CREATE INDEX ix_task_scope_class ON assessment_task_scope (class_id);

CREATE INDEX ix_task_scope_grade ON assessment_task_scope (grade_id);

CREATE INDEX ix_task_scope_school ON assessment_task_scope (school_id);

CREATE INDEX ix_task_scope_student ON assessment_task_scope (student_id);

CREATE INDEX ix_task_scope_task ON assessment_task_scope (task_id);

CREATE TABLE student_roster_import_row (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    batch_id BIGINT NOT NULL, 
    row_no INTEGER NOT NULL, 
    student_id INTEGER, 
    student_no VARCHAR(64), 
    name VARCHAR(64), 
    grade_name VARCHAR(64), 
    class_name VARCHAR(64), 
    gender VARCHAR(16), 
    age INTEGER, 
    processing_status VARCHAR(32) NOT NULL DEFAULT 'PENDING', 
    conflict_code VARCHAR(64), 
    message VARCHAR(1000), 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT student_roster_import_row_fk_batch FOREIGN KEY(batch_id) REFERENCES student_roster_import_batch (id), 
    CONSTRAINT student_roster_import_row_fk_student FOREIGN KEY(student_id) REFERENCES student (id), 
    CONSTRAINT uq_roster_import_row_no UNIQUE (batch_id, row_no)
);

CREATE INDEX ix_roster_import_row_student ON student_roster_import_row (student_id);

CREATE TABLE assessment_import_row (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    batch_id INTEGER NOT NULL, 
    row_no INTEGER NOT NULL, 
    student_id INTEGER, 
    raw_student_no VARCHAR(64), 
    raw_name VARCHAR(128), 
    raw_grade_name VARCHAR(64), 
    raw_class_name VARCHAR(64), 
    raw_age INTEGER, 
    normalized_name VARCHAR(128), 
    normalized_grade_name VARCHAR(64), 
    normalized_class_name VARCHAR(64), 
    matched_school_id INTEGER, 
    matched_grade_id INTEGER, 
    matched_class_id INTEGER, 
    match_status VARCHAR(32) NOT NULL DEFAULT 'PENDING', 
    match_confidence NUMERIC(5, 4), 
    candidate_student_ids JSON, 
    tested_at DATETIME, 
    source_timezone VARCHAR(64), 
    processing_status VARCHAR(32) NOT NULL DEFAULT 'PENDING', 
    conflict_code VARCHAR(64), 
    resolution VARCHAR(32), 
    out_of_scope_reason VARCHAR(255), 
    age_before INTEGER, 
    age_after INTEGER, 
    age_resolution VARCHAR(32), 
    resolved_by INTEGER, 
    resolved_at DATETIME, 
    session_id INTEGER, 
    external_result_record_id BIGINT, 
    message VARCHAR(1000), 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT assessment_import_row_ibfk_1 FOREIGN KEY(batch_id) REFERENCES assessment_import_batch (id), 
    CONSTRAINT assessment_import_row_fk_matched_school_class FOREIGN KEY(matched_school_id, matched_class_id) REFERENCES class_group (school_id, id), 
    CONSTRAINT assessment_import_row_fk_matched_school_grade FOREIGN KEY(matched_school_id, matched_grade_id) REFERENCES grade (school_id, id), 
    CONSTRAINT assessment_import_row_ibfk_4 FOREIGN KEY(resolved_by) REFERENCES user_account (id), 
    CONSTRAINT assessment_import_row_fk_session_student FOREIGN KEY(session_id, student_id) REFERENCES assessment_session (id, student_id), 
    CONSTRAINT assessment_import_row_ibfk_3 FOREIGN KEY(session_id) REFERENCES assessment_session (id), 
    CONSTRAINT assessment_import_row_ibfk_2 FOREIGN KEY(student_id) REFERENCES student (id), 
    CONSTRAINT uq_import_row_batch_no UNIQUE (batch_id, row_no)
);

CREATE INDEX ix_import_row_external_record ON assessment_import_row (external_result_record_id);

CREATE INDEX ix_import_row_match_status ON assessment_import_row (batch_id, match_status);

CREATE INDEX ix_import_row_matched_grade_class ON assessment_import_row (matched_school_id, matched_grade_id, matched_class_id);

CREATE INDEX ix_import_row_normalized_match ON assessment_import_row (normalized_grade_name, normalized_class_name, normalized_name);

CREATE INDEX session_id ON assessment_import_row (session_id);

CREATE INDEX student_id ON assessment_import_row (student_id);

CREATE TABLE care_case_event (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    care_case_id INTEGER NOT NULL, 
    student_id INTEGER NOT NULL, 
    event_type VARCHAR(64) NOT NULL, 
    from_status VARCHAR(32), 
    to_status VARCHAR(32), 
    operator_id INTEGER, 
    reason VARCHAR(255), 
    confirmed_facts TEXT, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT care_case_event_fk_case_student FOREIGN KEY(care_case_id, student_id) REFERENCES student_care_case (id, student_id), 
    CONSTRAINT care_case_event_ibfk_1 FOREIGN KEY(care_case_id) REFERENCES student_care_case (id), 
    CONSTRAINT care_case_event_ibfk_3 FOREIGN KEY(operator_id) REFERENCES user_account (id), 
    CONSTRAINT care_case_event_ibfk_2 FOREIGN KEY(student_id) REFERENCES student (id)
);

CREATE INDEX care_case_id ON care_case_event (care_case_id);

CREATE INDEX event_type_created_at ON care_case_event (event_type, created_at);

CREATE INDEX operator_id ON care_case_event (operator_id);

CREATE INDEX student_id ON care_case_event (student_id);

CREATE TABLE assessment_external_result (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    batch_id INTEGER NOT NULL, 
    row_id INTEGER NOT NULL, 
    school_id INTEGER NOT NULL, 
    task_id INTEGER, 
    student_id INTEGER NOT NULL, 
    source_system VARCHAR(128) NOT NULL, 
    external_result_id VARCHAR(128), 
    source_type VARCHAR(32) NOT NULL DEFAULT 'EXTERNAL_SUMMARY', 
    scale_code VARCHAR(64), 
    scale_version VARCHAR(64), 
    rule_version VARCHAR(64), 
    tested_at DATETIME NOT NULL, 
    validity_score INTEGER, 
    total_score INTEGER, 
    dimension_scores_json JSON, 
    result_payload_json JSON, 
    verification_status VARCHAR(32) NOT NULL DEFAULT 'PENDING', 
    applied_session_id INTEGER, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT assessment_external_result_fk_session_student FOREIGN KEY(applied_session_id, student_id) REFERENCES assessment_session (id, student_id), 
    CONSTRAINT assessment_external_result_fk_session FOREIGN KEY(applied_session_id) REFERENCES assessment_session (id), 
    CONSTRAINT assessment_external_result_fk_batch FOREIGN KEY(batch_id) REFERENCES assessment_import_batch (id), 
    CONSTRAINT assessment_external_result_fk_row FOREIGN KEY(row_id) REFERENCES assessment_import_row (id), 
    CONSTRAINT assessment_external_result_fk_student_school FOREIGN KEY(school_id, student_id) REFERENCES student (school_id, id), 
    CONSTRAINT assessment_external_result_fk_school FOREIGN KEY(school_id) REFERENCES school (id), 
    CONSTRAINT assessment_external_result_fk_student FOREIGN KEY(student_id) REFERENCES student (id), 
    CONSTRAINT assessment_external_result_fk_task_school FOREIGN KEY(task_id, school_id) REFERENCES assessment_task (id, school_id), 
    CONSTRAINT assessment_external_result_fk_task FOREIGN KEY(task_id) REFERENCES assessment_task (id), 
    CONSTRAINT uq_external_result_batch_row UNIQUE (batch_id, row_id), 
    CONSTRAINT uq_external_result_id_student UNIQUE (id, student_id), 
    CONSTRAINT uq_external_result_source_key UNIQUE (source_system, external_result_id)
);

CREATE INDEX ix_external_result_task_student ON assessment_external_result (task_id, student_id);

CREATE INDEX ix_external_result_verification ON assessment_external_result (verification_status);

CREATE TABLE student_age_change_log (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    student_id INTEGER NOT NULL, 
    roster_import_batch_id BIGINT, 
    assessment_import_batch_id INTEGER, 
    assessment_import_row_id INTEGER, 
    old_age INTEGER, 
    new_age INTEGER NOT NULL, 
    reason VARCHAR(128) NOT NULL, 
    changed_by INTEGER NOT NULL, 
    changed_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT student_age_change_log_fk_assessment_batch FOREIGN KEY(assessment_import_batch_id) REFERENCES assessment_import_batch (id), 
    CONSTRAINT student_age_change_log_fk_assessment_row FOREIGN KEY(assessment_import_row_id) REFERENCES assessment_import_row (id), 
    CONSTRAINT student_age_change_log_fk_operator FOREIGN KEY(changed_by) REFERENCES user_account (id), 
    CONSTRAINT student_age_change_log_fk_roster_batch FOREIGN KEY(roster_import_batch_id) REFERENCES student_roster_import_batch (id), 
    CONSTRAINT student_age_change_log_fk_student FOREIGN KEY(student_id) REFERENCES student (id)
);

CREATE INDEX ix_age_change_assessment_row ON student_age_change_log (assessment_import_row_id);

CREATE INDEX ix_age_change_student_time ON student_age_change_log (student_id, changed_at);

ALTER TABLE assessment_import_row ADD CONSTRAINT assessment_import_row_fk_external_record FOREIGN KEY(external_result_record_id) REFERENCES assessment_external_result (id);

ALTER TABLE assessment_session ADD COLUMN tested_at DATETIME;

ALTER TABLE assessment_session ADD COLUMN tested_at_source VARCHAR(32) NOT NULL DEFAULT 'PENDING_VERIFICATION';

ALTER TABLE assessment_session ADD COLUMN import_batch_id INTEGER;

ALTER TABLE assessment_session ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT '1';

ALTER TABLE assessment_session ADD COLUMN school_id INTEGER;

ALTER TABLE assessment_session ADD COLUMN source_type VARCHAR(32) NOT NULL DEFAULT 'ONLINE';

ALTER TABLE assessment_session ADD COLUMN external_source_system VARCHAR(128);

ALTER TABLE assessment_session ADD COLUMN external_result_id VARCHAR(128);

ALTER TABLE assessment_session ADD COLUMN conflict_status VARCHAR(32) NOT NULL DEFAULT 'NONE';

ALTER TABLE assessment_session ADD COLUMN is_effective BOOL NOT NULL DEFAULT '1';

ALTER TABLE assessment_session ADD COLUMN supersedes_session_id INTEGER;

ALTER TABLE assessment_session ADD COLUMN age_at_test INTEGER;

ALTER TABLE assessment_session ADD COLUMN answer_snapshot_hash CHAR(64);

ALTER TABLE assessment_session ADD COLUMN answer_hash_algorithm VARCHAR(32);

ALTER TABLE assessment_session ADD COLUMN calculation_status VARCHAR(32) NOT NULL DEFAULT 'PENDING';

ALTER TABLE assessment_session ADD COLUMN calculation_error VARCHAR(1000);

CREATE INDEX ix_session_calculation_status ON assessment_session (calculation_status);

CREATE INDEX ix_session_conflict_status ON assessment_session (conflict_status);

CREATE INDEX ix_session_external_result ON assessment_session (external_source_system, external_result_id);

CREATE INDEX ix_session_import_batch_id ON assessment_session (import_batch_id);

CREATE INDEX ix_session_school_student ON assessment_session (school_id, student_id);

CREATE INDEX ix_session_student_tested_at ON assessment_session (student_id, tested_at);

ALTER TABLE assessment_target ADD COLUMN school_id_snapshot INTEGER;

ALTER TABLE assessment_target ADD COLUMN student_no_snapshot VARCHAR(64);

ALTER TABLE assessment_target ADD COLUMN student_name_snapshot VARCHAR(64);

ALTER TABLE assessment_target ADD COLUMN grade_name_snapshot VARCHAR(64);

ALTER TABLE assessment_target ADD COLUMN class_name_snapshot VARCHAR(64);

ALTER TABLE assessment_target ADD COLUMN gender_snapshot VARCHAR(16);

ALTER TABLE assessment_target ADD COLUMN age_snapshot INTEGER;

ALTER TABLE assessment_target ADD COLUMN participation_disposition VARCHAR(32) NOT NULL DEFAULT 'REQUIRED';

ALTER TABLE assessment_target ADD COLUMN disposition_reason VARCHAR(128);

ALTER TABLE assessment_target ADD COLUMN disposition_note VARCHAR(1000);

ALTER TABLE assessment_target ADD COLUMN marked_by INTEGER;

ALTER TABLE assessment_target ADD COLUMN marked_at DATETIME;

ALTER TABLE assessment_target ADD COLUMN target_source VARCHAR(32) NOT NULL DEFAULT 'TASK_SCOPE';

ALTER TABLE assessment_target ADD COLUMN supplemented_from_batch_id INTEGER;

ALTER TABLE assessment_target ADD COLUMN effective_session_id INTEGER;

ALTER TABLE assessment_target ADD COLUMN effective_external_result_id BIGINT;

CREATE INDEX ix_target_effective_external ON assessment_target (effective_external_result_id);

CREATE INDEX ix_target_effective_session ON assessment_target (effective_session_id);

CREATE INDEX ix_target_participation ON assessment_target (task_id, participation_disposition);

CREATE INDEX ix_target_task_status ON assessment_target (task_id, status);

ALTER TABLE audit_log ADD COLUMN event_id CHAR(36);

ALTER TABLE audit_log ADD COLUMN actor_account_snapshot VARCHAR(128);

ALTER TABLE audit_log ADD COLUMN request_id VARCHAR(64);

ALTER TABLE audit_log ADD COLUMN detail_json JSON;

ALTER TABLE audit_log ADD COLUMN result_code VARCHAR(64);

ALTER TABLE audit_log ADD COLUMN audit_hash CHAR(64);

CREATE INDEX ix_audit_created_actor_action ON audit_log (created_at, actor_role, action);

CREATE INDEX ix_audit_request_id ON audit_log (request_id);

ALTER TABLE family_contact_record ADD COLUMN care_case_id INTEGER;

CREATE INDEX ix_family_contact_case ON family_contact_record (care_case_id);

CREATE INDEX ix_family_contact_next_date ON family_contact_record (next_contact_date);

ALTER TABLE follow_up_record ADD COLUMN care_case_id INTEGER;

CREATE INDEX ix_follow_up_case_date ON follow_up_record (care_case_id, next_follow_up_date);

CREATE INDEX ix_follow_up_status_date ON follow_up_record (status, next_follow_up_date);

ALTER TABLE manual_review ADD COLUMN care_case_id INTEGER;

ALTER TABLE manual_review ADD COLUMN student_id INTEGER;

CREATE INDEX ix_manual_review_care_case ON manual_review (care_case_id);

CREATE INDEX ix_manual_review_student ON manual_review (student_id);

ALTER TABLE retest_plan ADD COLUMN care_case_id INTEGER;

ALTER TABLE retest_plan ADD COLUMN completed_at DATETIME;

ALTER TABLE retest_plan ADD COLUMN cancelled_at DATETIME;

ALTER TABLE retest_plan ADD COLUMN cancel_reason VARCHAR(255);

CREATE INDEX ix_retest_case_status_date ON retest_plan (care_case_id, status, planned_date);

ALTER TABLE risk_event ADD COLUMN signal_type VARCHAR(64);

ALTER TABLE risk_event ADD COLUMN requires_manual_review BOOL NOT NULL DEFAULT '0';

ALTER TABLE risk_event ADD COLUMN rule_version VARCHAR(64) NOT NULL DEFAULT 'LEGACY_UNKNOWN';

CREATE INDEX ix_risk_event_status_created_at ON risk_event (status, created_at);

CREATE INDEX ix_scale_rule_status ON scale_rule (scale_id, status);

ALTER TABLE student_care_case ADD COLUMN closed_by INTEGER;

ALTER TABLE student_care_case ADD COLUMN reopened_by INTEGER;

ALTER TABLE student_care_case ADD COLUMN reopen_reason VARCHAR(255);

ALTER TABLE student_care_case ADD COLUMN last_reviewed_at DATETIME;

ALTER TABLE student_care_case ADD COLUMN case_version INTEGER NOT NULL DEFAULT '1';

CREATE INDEX ix_care_case_status_owner_updated ON student_care_case (status, owner_id, updated_at);

UPDATE assessment_session
    SET source_type = CASE WHEN source = 'IMPORTED' THEN 'EXTERNAL_FULL_ANSWER' ELSE 'ONLINE' END
    WHERE source_type = 'ONLINE';

UPDATE assessment_session s
    JOIN student st ON st.id = s.student_id
    SET s.school_id = st.school_id,
        s.age_at_test = COALESCE(s.age_at_test, st.age)
    WHERE s.school_id IS NULL;

UPDATE assessment_target t
    JOIN student st ON st.id = t.student_id
    SET t.school_id_snapshot = st.school_id
    WHERE t.school_id_snapshot IS NULL;

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
    WHERE signal_type IS NULL;

UPDATE manual_review mr
    JOIN risk_event re ON re.id = mr.risk_event_id
    SET mr.student_id = re.student_id
    WHERE mr.student_id IS NULL;

UPDATE alembic_version SET version_num='0013_v12_expand' WHERE alembic_version.version_num = '0012_drop_care_case_unique';


-- ==============================================================================
-- 第 2 条 / 共 7 条：0014_v12_enforce
-- ==============================================================================

-- 【检查】12 条。**每一条都必须返回 Empty set**。
-- 这些 SQL 与迁移里的 PRECHECKS 是同一份（同一个常量，由生成器搬过来），
-- 迁移自己在真跑之前也会做同样这一遍。提前摆在这里，是为了让你在动手之前看到结果。
-- 每条检查后面紧跟一次 CALL：有数据就当场中断，你会看到 `ERROR 1644 (45000)`
-- 与检查的编号——那时一行 DDL 都还没跑，库还是原样。

-- ------------------------------------------------------------------------------
-- 检查 [1/12]
-- assessment_session.school_id 还有 NULL（回填只覆盖得到名册上还有的学生）
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT id, student_id FROM assessment_session WHERE school_id IS NULL LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT id, student_id FROM assessment_session WHERE school_id IS NULL LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [1/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [2/12]
-- assessment_target.school_id_snapshot 还有 NULL
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT id, student_id FROM assessment_target WHERE school_id_snapshot IS NULL LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT id, student_id FROM assessment_target WHERE school_id_snapshot IS NULL LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [2/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [3/12]
-- risk_event.signal_type 还有 NULL（认不出的 risk_type 在 0013 就该中止了）
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT id, risk_type FROM risk_event WHERE signal_type IS NULL LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT id, risk_type FROM risk_event WHERE signal_type IS NULL LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [3/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [4/12]
-- 同一个任务里同一个学生有多份 attempt_no 相同的卷子
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT task_id, student_id, attempt_no, COUNT(*) AS n
FROM assessment_session WHERE task_id IS NOT NULL
GROUP BY task_id, student_id, attempt_no HAVING n > 1 LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT task_id, student_id, attempt_no, COUNT(*) AS n
FROM assessment_session WHERE task_id IS NOT NULL
GROUP BY task_id, student_id, attempt_no HAVING n > 1 LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [4/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [5/12]
-- 同一个任务里同一个学生有多份 is_effective = 1 的卷子（uq_session_effective_task_student 要挡的就是这个）
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT task_id, student_id, COUNT(*) AS n
FROM assessment_session
WHERE task_id IS NOT NULL AND is_effective = 1
GROUP BY task_id, student_id HAVING n > 1 LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT task_id, student_id, COUNT(*) AS n
FROM assessment_session
WHERE task_id IS NOT NULL AND is_effective = 1
GROUP BY task_id, student_id HAVING n > 1 LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [5/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [6/12]
-- 同一名学生有多份在办档案——uq_care_case_one_active_per_student 装不上去，而 care_service.reopen_case 目前是无条件把 status 设成 FOLLOWING 的
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT student_id, COUNT(*) AS n
FROM student_care_case WHERE status <> 'CLOSED'
GROUP BY student_id HAVING n > 1 LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT student_id, COUNT(*) AS n
FROM student_care_case WHERE status <> 'CLOSED'
GROUP BY student_id HAVING n > 1 LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [6/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [7/12]
-- risk_event 的 (session_id, trigger_rule, rule_version) 有重复
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT session_id, trigger_rule, rule_version, COUNT(*) AS n
FROM risk_event
GROUP BY session_id, trigger_rule, rule_version HAVING n > 1 LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT session_id, trigger_rule, rule_version, COUNT(*) AS n
FROM risk_event
GROUP BY session_id, trigger_rule, rule_version HAVING n > 1 LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [7/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [8/12]
-- scale_rule 的 (scale_id, rule_version) 有重复
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT scale_id, rule_version, COUNT(*) AS n
FROM scale_rule GROUP BY scale_id, rule_version HAVING n > 1 LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT scale_id, rule_version, COUNT(*) AS n
FROM scale_rule GROUP BY scale_id, rule_version HAVING n > 1 LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [8/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [9/12]
-- 学生的年级 / 班级不属于他所在的那所学校
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT st.id, st.school_id, g.school_id, cg.school_id
FROM student st
LEFT JOIN grade g ON g.id = st.grade_id
LEFT JOIN class_group cg ON cg.id = st.class_id
WHERE (g.id IS NOT NULL AND g.school_id <> st.school_id)
   OR (cg.id IS NOT NULL AND cg.school_id <> st.school_id)
LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT st.id, st.school_id, g.school_id, cg.school_id
FROM student st
LEFT JOIN grade g ON g.id = st.grade_id
LEFT JOIN class_group cg ON cg.id = st.class_id
WHERE (g.id IS NOT NULL AND g.school_id <> st.school_id)
   OR (cg.id IS NOT NULL AND cg.school_id <> st.school_id)
LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [9/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [10/12]
-- 班级的年级不属于它所在的那所学校
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT cg.id, cg.school_id, g.school_id
FROM class_group cg JOIN grade g ON g.id = cg.grade_id
WHERE g.school_id <> cg.school_id LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT cg.id, cg.school_id, g.school_id
FROM class_group cg JOIN grade g ON g.id = cg.grade_id
WHERE g.school_id <> cg.school_id LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [10/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [11/12]
-- 复测计划指向了**别的学生**的答卷
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
SELECT rp.id, rp.student_id, s1.student_id, s2.student_id
FROM retest_plan rp
LEFT JOIN assessment_session s1 ON s1.id = rp.source_session_id
LEFT JOIN assessment_session s2 ON s2.id = rp.completed_session_id
WHERE (s1.id IS NOT NULL AND s1.student_id <> rp.student_id)
   OR (s2.id IS NOT NULL AND s2.student_id <> rp.student_id)
LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT rp.id, rp.student_id, s1.student_id, s2.student_id
FROM retest_plan rp
LEFT JOIN assessment_session s1 ON s1.id = rp.source_session_id
LEFT JOIN assessment_session s2 ON s2.id = rp.completed_session_id
WHERE (s1.id IS NOT NULL AND s1.student_id <> rp.student_id)
   OR (s2.id IS NOT NULL AND s2.student_id <> rp.student_id)
LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [11/12] 未通过：见上面这条 SELECT 打出来的数据');

-- ------------------------------------------------------------------------------
-- 检查 [12/12]
-- 关怀链路里 care_case_id 与 student_id 指向了不同的学生（`care_case_id` 在历史行上全是 NULL，所以这条今天是空转的；留着的理由是它一旦被回填就立刻成立）
-- 期望结果：0 行（Empty set）
-- ------------------------------------------------------------------------------
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
LIMIT 5;
-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。
SELECT EXISTS(SELECT 'manual_review' AS src, x.id FROM manual_review x
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
LIMIT 5) INTO @xlp_hits;
CALL xlp_check_empty(@xlp_hits, '检查 [12/12] 未通过：见上面这条 SELECT 打出来的数据');

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0014_v12_enforce。
-- ------------------------------------------------------------------------------
-- Running upgrade 0013_v12_expand -> 0014_v12_enforce

ALTER TABLE assessment_session ADD COLUMN effective_task_student_key VARCHAR(96) GENERATED ALWAYS AS (CASE WHEN `is_effective` = 1 AND `task_id` IS NOT NULL THEN CONCAT(`task_id`, ':', `student_id`) ELSE NULL END) STORED;

ALTER TABLE student_care_case ADD COLUMN active_student_id INTEGER GENERATED ALWAYS AS (CASE WHEN `status` <> 'CLOSED' THEN `student_id` ELSE NULL END) STORED;

CREATE INDEX ix_care_case_active_student ON student_care_case (active_student_id);

ALTER TABLE assessment_session MODIFY school_id INTEGER NOT NULL;

ALTER TABLE assessment_target MODIFY school_id_snapshot INTEGER NOT NULL;

ALTER TABLE risk_event MODIFY signal_type VARCHAR(64) NOT NULL;

ALTER TABLE assessment_session ADD CONSTRAINT uq_session_task_student_attempt UNIQUE (task_id, student_id, attempt_no);

DROP INDEX uq_session_task_student ON assessment_session;

ALTER TABLE assessment_session ADD CONSTRAINT uq_session_effective_task_student UNIQUE (effective_task_student_key);

ALTER TABLE assessment_session ADD CONSTRAINT uq_session_external_source_result UNIQUE (external_source_system, external_result_id);

ALTER TABLE audit_log ADD CONSTRAINT uq_audit_event_id UNIQUE (event_id);

ALTER TABLE risk_event ADD CONSTRAINT uq_risk_event_session_trigger_rule UNIQUE (session_id, trigger_rule, rule_version);

ALTER TABLE scale_rule ADD CONSTRAINT uq_scale_rule_version UNIQUE (scale_id, rule_version);

ALTER TABLE student_care_case ADD CONSTRAINT uq_care_case_one_active_per_student UNIQUE (active_student_id);

ALTER TABLE assessment_session ADD CONSTRAINT assessment_session_fk_task_school FOREIGN KEY(task_id, school_id) REFERENCES assessment_task (id, school_id);

ALTER TABLE assessment_session ADD CONSTRAINT assessment_session_fk_supersedes FOREIGN KEY(supersedes_session_id) REFERENCES assessment_session (id);

ALTER TABLE assessment_session ADD CONSTRAINT assessment_session_fk_student_school FOREIGN KEY(school_id, student_id) REFERENCES student (school_id, id);

ALTER TABLE assessment_session ADD CONSTRAINT assessment_session_ibfk_4 FOREIGN KEY(import_batch_id) REFERENCES assessment_import_batch (id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_effective_session FOREIGN KEY(effective_session_id) REFERENCES assessment_session (id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_effective_external FOREIGN KEY(effective_external_result_id) REFERENCES assessment_external_result (id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_supplement_batch FOREIGN KEY(supplemented_from_batch_id) REFERENCES assessment_import_batch (id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_task_school FOREIGN KEY(task_id, school_id_snapshot) REFERENCES assessment_task (id, school_id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_effective_external_student FOREIGN KEY(effective_external_result_id, student_id) REFERENCES assessment_external_result (id, student_id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_student_school FOREIGN KEY(school_id_snapshot, student_id) REFERENCES student (school_id, id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_marked_by FOREIGN KEY(marked_by) REFERENCES user_account (id);

ALTER TABLE assessment_target ADD CONSTRAINT assessment_target_fk_effective_session_student FOREIGN KEY(effective_session_id, student_id) REFERENCES assessment_session (id, student_id);

ALTER TABLE class_group ADD CONSTRAINT class_group_ibfk_3 FOREIGN KEY(school_id, grade_id) REFERENCES grade (school_id, id);

ALTER TABLE family_contact_record ADD CONSTRAINT family_contact_record_ibfk_3 FOREIGN KEY(care_case_id) REFERENCES student_care_case (id);

ALTER TABLE family_contact_record ADD CONSTRAINT family_contact_record_fk_case_student FOREIGN KEY(care_case_id, student_id) REFERENCES student_care_case (id, student_id);

ALTER TABLE follow_up_record ADD CONSTRAINT follow_up_record_ibfk_3 FOREIGN KEY(care_case_id) REFERENCES student_care_case (id);

ALTER TABLE follow_up_record ADD CONSTRAINT follow_up_record_fk_case_student FOREIGN KEY(care_case_id, student_id) REFERENCES student_care_case (id, student_id);

ALTER TABLE manual_review ADD CONSTRAINT manual_review_fk_case_student FOREIGN KEY(care_case_id, student_id) REFERENCES student_care_case (id, student_id);

ALTER TABLE manual_review ADD CONSTRAINT manual_review_ibfk_3 FOREIGN KEY(care_case_id) REFERENCES student_care_case (id);

ALTER TABLE manual_review ADD CONSTRAINT manual_review_ibfk_4 FOREIGN KEY(student_id) REFERENCES student (id);

ALTER TABLE retest_plan ADD CONSTRAINT retest_plan_fk_source_student FOREIGN KEY(source_session_id, student_id) REFERENCES assessment_session (id, student_id);

ALTER TABLE retest_plan ADD CONSTRAINT retest_plan_fk_completed_student FOREIGN KEY(completed_session_id, student_id) REFERENCES assessment_session (id, student_id);

ALTER TABLE retest_plan ADD CONSTRAINT retest_plan_ibfk_5 FOREIGN KEY(care_case_id) REFERENCES student_care_case (id);

ALTER TABLE retest_plan ADD CONSTRAINT retest_plan_fk_case_student FOREIGN KEY(care_case_id, student_id) REFERENCES student_care_case (id, student_id);

ALTER TABLE student ADD CONSTRAINT student_ibfk_5 FOREIGN KEY(school_id, class_id) REFERENCES class_group (school_id, id);

ALTER TABLE student ADD CONSTRAINT student_ibfk_4 FOREIGN KEY(school_id, grade_id) REFERENCES grade (school_id, id);

ALTER TABLE student_care_case ADD CONSTRAINT student_care_case_ibfk_4 FOREIGN KEY(reopened_by) REFERENCES user_account (id);

ALTER TABLE student_care_case ADD CONSTRAINT student_care_case_ibfk_3 FOREIGN KEY(closed_by) REFERENCES user_account (id);

UPDATE alembic_version SET version_num='0014_v12_enforce' WHERE alembic_version.version_num = '0013_v12_expand';


-- ==============================================================================
-- 第 3 条 / 共 7 条：0015_calc_status_backfill
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0015_calc_status_backfill。
-- ------------------------------------------------------------------------------
-- Running upgrade 0014_v12_enforce -> 0015_calc_status_backfill

UPDATE assessment_session AS s JOIN assessment_result AS r ON r.session_id = s.id SET s.calculation_status = 'CALCULATED' WHERE s.calculation_status = 'PENDING';

UPDATE alembic_version SET version_num='0015_calc_status_backfill' WHERE alembic_version.version_num = '0014_v12_enforce';


-- ==============================================================================
-- 第 4 条 / 共 7 条：0016_import_batch_name
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0016_import_batch_name。
-- ------------------------------------------------------------------------------
-- Running upgrade 0015_calc_status_backfill -> 0016_import_batch_name

ALTER TABLE assessment_import_batch ADD COLUMN batch_name VARCHAR(128) NOT NULL DEFAULT '';

UPDATE alembic_version SET version_num='0016_import_batch_name' WHERE alembic_version.version_num = '0015_calc_status_backfill';


-- ==============================================================================
-- 第 5 条 / 共 7 条：0017_json_null_normalize
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0017_json_null_normalize。
-- ------------------------------------------------------------------------------
-- Running upgrade 0016_import_batch_name -> 0017_json_null_normalize

UPDATE assessment_import_row SET candidate_student_ids = NULL WHERE JSON_TYPE(candidate_student_ids) = 'NULL';

UPDATE assessment_external_result SET dimension_scores_json = NULL WHERE JSON_TYPE(dimension_scores_json) = 'NULL';

UPDATE assessment_external_result SET result_payload_json = NULL WHERE JSON_TYPE(result_payload_json) = 'NULL';

UPDATE alembic_version SET version_num='0017_json_null_normalize' WHERE alembic_version.version_num = '0016_import_batch_name';


-- ==============================================================================
-- 第 6 条 / 共 7 条：0018_row_conflict_resolution
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0018_row_conflict_resolution。
-- ------------------------------------------------------------------------------
-- Running upgrade 0017_json_null_normalize -> 0018_row_conflict_resolution

ALTER TABLE assessment_import_row ADD COLUMN conflict_resolution VARCHAR(32);

UPDATE alembic_version SET version_num='0018_row_conflict_resolution' WHERE alembic_version.version_num = '0017_json_null_normalize';


-- ==============================================================================
-- 第 7 条 / 共 7 条：0019_total_includes_validity
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0019_total_includes_validity。
-- ------------------------------------------------------------------------------
-- Running upgrade 0018_row_conflict_resolution -> 0019_total_includes_validity

CREATE TEMPORARY TABLE xlp_rule_bump AS SELECT r.id AS old_id,        r.scale_id AS scale_id,        CASE          WHEN LOCATE('.', r.rule_version) > 1           AND SUBSTRING_INDEX(r.rule_version, '.', -1) REGEXP '^[0-9]+$'          THEN CONCAT(                LEFT(r.rule_version,                      CHAR_LENGTH(r.rule_version)                      - CHAR_LENGTH(SUBSTRING_INDEX(r.rule_version, '.', -1))),                 CAST(SUBSTRING_INDEX(r.rule_version, '.', -1) AS UNSIGNED) + 1)          ELSE CONCAT(r.rule_version, '-2')        END AS new_version,        CASE          WHEN JSON_LENGTH(r.config_json, '$.total_levels') IS NULL            OR JSON_LENGTH(r.config_json, '$.total_levels') = 0          THEN r.config_json          ELSE JSON_SET(                r.config_json,                 CONCAT('$.total_levels[',                        JSON_LENGTH(r.config_json, '$.total_levels') - 1, '].max'),                 COALESCE(CAST(JSON_EXTRACT(r.config_json, '$.question_count') AS UNSIGNED), 100))        END AS new_config FROM scale_rule AS r WHERE r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE';

DELETE b FROM xlp_rule_bump AS b WHERE EXISTS (SELECT 1 FROM scale_rule AS s               WHERE s.scale_id = b.scale_id AND s.rule_version = b.new_version);

INSERT INTO scale_rule (scale_id, rule_version, rule_type, config_json, status) SELECT b.scale_id, b.new_version, 'MHT_SCORING', b.new_config, 'ACTIVE' FROM xlp_rule_bump AS b;

UPDATE scale_rule SET status = 'RETIRED' WHERE id IN (SELECT old_id FROM xlp_rule_bump);

DROP TEMPORARY TABLE xlp_rule_bump;

UPDATE alembic_version SET version_num='0019_total_includes_validity' WHERE alembic_version.version_num = '0018_row_conflict_resolution';


-- ==============================================================================
-- 到这里就结束了。核对一句：
--   SELECT version_num FROM alembic_version;
-- 应当是 0019_total_includes_validity。
--
-- 程序文件那一侧照常走一键安装包（升级模式不会重跑 seed、不会重置管理员密码、
-- 不会碰数据库里的数据，只更新程序文件并再跑一次迁移——那时这一步已经是空转的）。
-- ------------------------------------------------------------------------------
-- 把开头建的那个临时存储过程删掉（它只在这一趟里有意义）。
-- 上一次执行被 SIGNAL 中断时这一句跑不到，所以开头还有一次 DROP ... IF EXISTS。
DROP PROCEDURE IF EXISTS xlp_check_empty;
-- ==============================================================================
