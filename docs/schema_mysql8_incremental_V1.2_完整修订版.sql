-- ============================================================================
-- 心晴·中学生心理测评与关怀平台
-- MySQL 8 增量DDL V1.2（完整修订版）
-- ============================================================================
--
-- 适用对象：已经执行 schema_mysql8.sql / Alembic 0012 的现有数据库
-- 目标：在初始schema_mysql8.sql基础上补齐任务范围、名册批次、无学号匹配、年龄覆盖、
--       分批外部导入、在线/外部冲突、任务外学生、有效结果选择、导出和迁移过渡能力
--
-- 注意：本文件是完整评审版参考增量，不是空库脚本，也不建议生产环境一条长SQL直接执行。
-- DDL会隐式提交，执行前必须完成数据库备份。
-- 本脚本不是全量建库脚本，不要在空库上单独执行。
-- 正式项目必须将本脚本拆分为Alembic migration，并在测试库和真MySQL 8上验证。
-- 推荐MySQL版本：8.0.16及以上；8.0.13可以执行主要结构，但CHECK约束不应作为唯一校验来源。
-- 所有新增状态和枚举仍必须由应用层统一校验。
--
-- 迁移顺序：
-- 1. 预检查
-- 2. 新增学生名册批次、外部导入批次、任务范围、导出任务、认证会话、关怀事件表
-- 3. 新增历史追溯字段
-- 4. 新增生命周期和风险信号字段
-- 5. 新增外部导入匹配、年龄覆盖和在线/外部结果冲突模型
-- 6. 新增关怀档案复合一致性约束
-- 7. 新增跨组织关系约束和索引
-- 8. 应用层回填、回归测试和Alembic版本登记
-- ============================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';
SET FOREIGN_KEY_CHECKS = 1;

-- ============================================================================
-- 一、迁移前预检查
-- ============================================================================

-- 1. 同一学生存在多条未关闭档案时，不能直接创建唯一索引。
SELECT student_id, COUNT(*) AS active_case_count
FROM student_care_case
WHERE status <> 'CLOSED'
GROUP BY student_id
HAVING COUNT(*) > 1;

-- 2. 同一会话同一触发规则存在重复风险事件时，不能直接创建唯一索引。
SELECT session_id, trigger_rule, COUNT(*) AS duplicate_count
FROM risk_event
GROUP BY session_id, trigger_rule
HAVING COUNT(*) > 1;

-- 3. 检查组织层级是否跨学校关联。
SELECT c.id AS class_id, c.school_id AS class_school_id, g.id AS grade_id, g.school_id AS grade_school_id
FROM class_group c
JOIN grade g ON g.id = c.grade_id
WHERE c.school_id <> g.school_id;

SELECT s.id AS student_id, s.school_id, g.school_id AS grade_school_id, c.school_id AS class_school_id
FROM student s
JOIN grade g ON g.id = s.grade_id
JOIN class_group c ON c.id = s.class_id
WHERE s.school_id <> g.school_id OR s.school_id <> c.school_id OR c.grade_id <> s.grade_id;

-- 4. 任务、目标、会话的学校归属必须可回填；否则不得开启复合外键。
SELECT t.id AS target_id, t.task_id, t.student_id
FROM assessment_target t
JOIN assessment_task at ON at.id = t.task_id
JOIN student st ON st.id = t.student_id
WHERE at.school_id <> st.school_id;

SELECT s.id AS session_id, s.task_id, s.student_id
FROM assessment_session s
JOIN assessment_task at ON at.id = s.task_id
JOIN student st ON st.id = s.student_id
WHERE at.school_id <> st.school_id;

-- 5. 检查现有答卷是否存在非法答案或分值。
SELECT id, session_id, question_id, answer, score
FROM assessment_answer
WHERE answer NOT IN ('YES', 'NO') OR score NOT IN (0, 1);

-- 6. 规则版本重复时，唯一索引创建会失败。
SELECT scale_id, rule_version, COUNT(*) AS duplicate_count
FROM scale_rule
GROUP BY scale_id, rule_version
HAVING COUNT(*) > 1;

-- 7. 未知历史风险类型不得静默归类为普通筛查信号。
SELECT DISTINCT risk_type
FROM risk_event
WHERE risk_type NOT IN (
  'MANUAL_REVIEW_REQUIRED',
  'RETEST_RECOMMENDED',
  'HIGH_TOTAL_SCORE',
  'HIGH_DIMENSION_SCORE',
  'KEY_QUESTION_TRIGGERED',
  'SCREENING_SIGNAL'
);

-- 8. 复测来源/完成答卷必须属于同一学生。
SELECT r.id AS retest_plan_id
FROM retest_plan r
JOIN assessment_session s ON s.id IN (r.source_session_id, r.completed_session_id)
WHERE s.student_id <> r.student_id;

-- 以上任一查询返回数据时，迁移执行器必须中止，不得继续执行后续ALTER。
-- 本SQL中的SELECT仅用于诊断；生产迁移必须由Alembic precheck以异常方式中止。

-- ============================================================================
-- 二、学生名册导入批次
-- ============================================================================

CREATE TABLE `student_roster_import_batch` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `batch_no` varchar(64) NOT NULL,
  `school_id` int NOT NULL,
  `file_name` varchar(255) NOT NULL,
  `file_sha256` char(64) NOT NULL,
  `imported_by` int NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'PREVIEW',
  `total_rows` int NOT NULL DEFAULT 0,
  `created_rows` int NOT NULL DEFAULT 0,
  `updated_rows` int NOT NULL DEFAULT 0,
  `skipped_rows` int NOT NULL DEFAULT 0,
  `error_rows` int NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_roster_import_batch_no` (`batch_no`),
  KEY `ix_roster_import_school_created` (`school_id`,`created_at`),
  KEY `ix_roster_import_operator` (`imported_by`),
  CONSTRAINT `student_roster_import_batch_fk_school` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `student_roster_import_batch_fk_operator` FOREIGN KEY (`imported_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `student_roster_import_row` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `batch_id` bigint NOT NULL,
  `row_no` int NOT NULL,
  `student_id` int DEFAULT NULL,
  `student_no` varchar(64) DEFAULT NULL,
  `name` varchar(64) DEFAULT NULL,
  `grade_name` varchar(64) DEFAULT NULL,
  `class_name` varchar(64) DEFAULT NULL,
  `gender` varchar(16) DEFAULT NULL,
  `age` int DEFAULT NULL,
  `processing_status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `conflict_code` varchar(64) DEFAULT NULL,
  `message` varchar(1000) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_roster_import_row_no` (`batch_id`,`row_no`),
  KEY `ix_roster_import_row_student` (`student_id`),
  CONSTRAINT `student_roster_import_row_fk_batch` FOREIGN KEY (`batch_id`) REFERENCES `student_roster_import_batch` (`id`),
  CONSTRAINT `student_roster_import_row_fk_student` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================================
-- 三、外部测评导入批次
-- ============================================================================

CREATE TABLE `assessment_import_batch` (
  `id` int NOT NULL AUTO_INCREMENT,
  `batch_no` varchar(64) NOT NULL,
  `school_id` int NOT NULL,
  `file_name` varchar(255) NOT NULL,
  `file_sha256` char(64) NOT NULL,
  `source_system` varchar(128) NOT NULL DEFAULT 'UNKNOWN',
  `source_timezone` varchar(64) NOT NULL DEFAULT 'UTC',
  `tested_at` datetime DEFAULT NULL,
  `import_mode` varchar(32) NOT NULL DEFAULT 'EXTERNAL_FULL_ANSWER',
  `conflict_policy` varchar(32) NOT NULL DEFAULT 'REQUIRE_REVIEW',
  `out_of_scope_policy` varchar(32) NOT NULL DEFAULT 'REJECT',
  `allow_age_overwrite` tinyint(1) NOT NULL DEFAULT '0',
  `file_size_bytes` bigint DEFAULT NULL,
  `schema_version` varchar(64) DEFAULT NULL,
  `parser_version` varchar(64) DEFAULT NULL,
  `duplicate_of_batch_id` int DEFAULT NULL,
  `resolution` varchar(32) NOT NULL DEFAULT 'NONE',
  `total_rows` int NOT NULL DEFAULT 0,
  `created_rows` int NOT NULL DEFAULT 0,
  `updated_rows` int NOT NULL DEFAULT 0,
  `skipped_rows` int NOT NULL DEFAULT 0,
  `error_rows` int NOT NULL DEFAULT 0,
  `status` varchar(32) NOT NULL DEFAULT 'PREVIEW',
  `imported_by` int NOT NULL,
  `task_id` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_assessment_import_batch_no` (`batch_no`),
  KEY `ix_assessment_import_school_created` (`school_id`,`created_at`),
  KEY `imported_by` (`imported_by`),
  KEY `task_id` (`task_id`),
  KEY `tested_at` (`tested_at`),
  KEY `ix_import_file_hash` (`source_system`,`file_sha256`),
  KEY `ix_import_duplicate_of` (`duplicate_of_batch_id`),
  CONSTRAINT `assessment_import_batch_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_import_batch_ibfk_2` FOREIGN KEY (`imported_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `assessment_import_batch_ibfk_3` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_import_batch_ibfk_4` FOREIGN KEY (`duplicate_of_batch_id`) REFERENCES `assessment_import_batch` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `assessment_import_row` (
  `id` int NOT NULL AUTO_INCREMENT,
  `batch_id` int NOT NULL,
  `row_no` int NOT NULL,
  `student_id` int DEFAULT NULL,
  `raw_student_no` varchar(64) DEFAULT NULL,
  `raw_name` varchar(128) DEFAULT NULL,
  `raw_grade_name` varchar(64) DEFAULT NULL,
  `raw_class_name` varchar(64) DEFAULT NULL,
  `raw_age` int DEFAULT NULL,
  `normalized_name` varchar(128) DEFAULT NULL,
  `normalized_grade_name` varchar(64) DEFAULT NULL,
  `normalized_class_name` varchar(64) DEFAULT NULL,
  `matched_school_id` int DEFAULT NULL,
  `matched_grade_id` int DEFAULT NULL,
  `matched_class_id` int DEFAULT NULL,
  `match_status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `match_confidence` decimal(5,4) DEFAULT NULL,
  `candidate_student_ids` json DEFAULT NULL,
  `tested_at` datetime DEFAULT NULL,
  `source_timezone` varchar(64) DEFAULT NULL,
  `processing_status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `conflict_code` varchar(64) DEFAULT NULL,
  `resolution` varchar(32) DEFAULT NULL,
  `out_of_scope_reason` varchar(255) DEFAULT NULL,
  `age_before` int DEFAULT NULL,
  `age_after` int DEFAULT NULL,
  `age_resolution` varchar(32) DEFAULT NULL,
  `resolved_by` int DEFAULT NULL,
  `resolved_at` datetime DEFAULT NULL,
  `session_id` int DEFAULT NULL,
  `external_result_record_id` bigint DEFAULT NULL,
  `message` varchar(1000) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_import_row_batch_no` (`batch_id`,`row_no`),
  KEY `student_id` (`student_id`),
  KEY `session_id` (`session_id`),
  KEY `ix_import_row_match_status` (`batch_id`,`match_status`),
  KEY `ix_import_row_normalized_match` (`normalized_grade_name`,`normalized_class_name`,`normalized_name`),
  KEY `ix_import_row_matched_grade_class` (`matched_school_id`,`matched_grade_id`,`matched_class_id`),
  KEY `ix_import_row_external_record` (`external_result_record_id`),
  CONSTRAINT `assessment_import_row_ibfk_1` FOREIGN KEY (`batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_3` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_4` FOREIGN KEY (`resolved_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `assessment_external_result` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `batch_id` int NOT NULL,
  `row_id` int NOT NULL,
  `school_id` int NOT NULL,
  `task_id` int DEFAULT NULL,
  `student_id` int NOT NULL,
  `source_system` varchar(128) NOT NULL,
  `external_result_id` varchar(128) DEFAULT NULL,
  `source_type` varchar(32) NOT NULL DEFAULT 'EXTERNAL_SUMMARY',
  `scale_code` varchar(64) DEFAULT NULL,
  `scale_version` varchar(64) DEFAULT NULL,
  `rule_version` varchar(64) DEFAULT NULL,
  `tested_at` datetime NOT NULL,
  `validity_score` int DEFAULT NULL,
  `total_score` int DEFAULT NULL,
  `dimension_scores_json` json DEFAULT NULL,
  `result_payload_json` json DEFAULT NULL,
  `verification_status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `applied_session_id` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_external_result_source_key` (`source_system`,`external_result_id`),
  UNIQUE KEY `uq_external_result_batch_row` (`batch_id`,`row_id`),
  UNIQUE KEY `uq_external_result_id_student` (`id`,`student_id`),
  KEY `ix_external_result_task_student` (`task_id`,`student_id`),
  KEY `ix_external_result_verification` (`verification_status`),
  CONSTRAINT `assessment_external_result_fk_batch` FOREIGN KEY (`batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `assessment_external_result_fk_row` FOREIGN KEY (`row_id`) REFERENCES `assessment_import_row` (`id`),
  CONSTRAINT `assessment_external_result_fk_school` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_external_result_fk_task` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_external_result_fk_student` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_external_result_fk_session` FOREIGN KEY (`applied_session_id`) REFERENCES `assessment_session` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- result_payload_json可能含外部平台原始敏感字段；生产实现必须按敏感字段策略加密或最小化保存，
-- 查询和导出仍受心理老师数据范围、查看原因和AuditLog约束。

ALTER TABLE `assessment_import_row`
  ADD CONSTRAINT `assessment_import_row_fk_external_record`
    FOREIGN KEY (`external_result_record_id`) REFERENCES `assessment_external_result` (`id`);

-- 年龄覆盖必须保留审计事实：student.age是当前名册年龄，age_at_test是本次测评时年龄。
CREATE TABLE `student_age_change_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `roster_import_batch_id` bigint DEFAULT NULL,
  `assessment_import_batch_id` int DEFAULT NULL,
  `assessment_import_row_id` int DEFAULT NULL,
  `old_age` int DEFAULT NULL,
  `new_age` int NOT NULL,
  `reason` varchar(128) NOT NULL,
  `changed_by` int NOT NULL,
  `changed_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_age_change_student_time` (`student_id`,`changed_at`),
  KEY `ix_age_change_assessment_row` (`assessment_import_row_id`),
  CONSTRAINT `student_age_change_log_fk_student` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `student_age_change_log_fk_roster_batch` FOREIGN KEY (`roster_import_batch_id`) REFERENCES `student_roster_import_batch` (`id`),
  CONSTRAINT `student_age_change_log_fk_assessment_batch` FOREIGN KEY (`assessment_import_batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `student_age_change_log_fk_assessment_row` FOREIGN KEY (`assessment_import_row_id`) REFERENCES `assessment_import_row` (`id`),
  CONSTRAINT `student_age_change_log_fk_operator` FOREIGN KEY (`changed_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================================
-- 四、任务范围和受控导出
-- ============================================================================

-- 保存任务设计时选择的范围；实际发放对象仍以assessment_target快照为准。
CREATE TABLE `assessment_task_scope` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_id` int NOT NULL,
  `scope_type` varchar(32) NOT NULL,
  `school_id` int NOT NULL,
  `grade_id` int DEFAULT NULL,
  `class_id` int DEFAULT NULL,
  `student_id` int DEFAULT NULL,
  `created_by` int NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_task_scope_task` (`task_id`),
  KEY `ix_task_scope_school` (`school_id`),
  KEY `ix_task_scope_grade` (`grade_id`),
  KEY `ix_task_scope_class` (`class_id`),
  KEY `ix_task_scope_student` (`student_id`),
  CONSTRAINT `assessment_task_scope_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_2` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_3` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_4` FOREIGN KEY (`class_id`) REFERENCES `class_group` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_5` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_6` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 新表创建后再检查历史回填/重放数据，生产迁移器应在此处失败即停止。
SELECT ts.id AS task_scope_id
FROM assessment_task_scope ts
LEFT JOIN grade g ON g.id = ts.grade_id
LEFT JOIN class_group cg ON cg.id = ts.class_id
LEFT JOIN student st ON st.id = ts.student_id
WHERE (g.id IS NOT NULL AND g.school_id <> ts.school_id)
   OR (cg.id IS NOT NULL AND cg.school_id <> ts.school_id)
   OR (st.id IS NOT NULL AND st.school_id <> ts.school_id);

-- 导出采用任务化方式，保留用途、范围、字段策略、过期时间和下载审计。
CREATE TABLE `export_job` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `job_no` varchar(64) NOT NULL,
  `export_type` varchar(64) NOT NULL,
  `requested_by` int NOT NULL,
  `purpose` varchar(255) NOT NULL,
  `scope_snapshot` json NOT NULL,
  `field_policy` json NOT NULL,
  `mask_level` varchar(32) NOT NULL DEFAULT 'MASKED',
  `status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `file_uri` varchar(1000) DEFAULT NULL,
  `file_sha256` char(64) DEFAULT NULL,
  `row_count` int DEFAULT NULL,
  `download_count` int NOT NULL DEFAULT 0,
  `expires_at` datetime DEFAULT NULL,
  `downloaded_at` datetime DEFAULT NULL,
  `revoked_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_export_job_no` (`job_no`),
  KEY `ix_export_job_requester_status` (`requested_by`,`status`),
  KEY `ix_export_job_expires_at` (`expires_at`),
  CONSTRAINT `export_job_ibfk_1` FOREIGN KEY (`requested_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================================
-- 五、认证会话
-- ============================================================================

-- JWT仍可作为访问令牌，但服务端保留会话/撤销状态，支持正式登出、强制下线和审计。
CREATE TABLE `auth_session` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `jti` char(36) NOT NULL,
  `token_type` varchar(16) NOT NULL DEFAULT 'ACCESS',
  `session_token_hash` char(64) NOT NULL,
  `issued_at` datetime NOT NULL,
  `expires_at` datetime NOT NULL,
  `revoked_at` datetime DEFAULT NULL,
  `revoked_reason` varchar(255) DEFAULT NULL,
  `last_seen_at` datetime DEFAULT NULL,
  `ip` varchar(64) DEFAULT NULL,
  `user_agent` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_auth_session_jti` (`jti`),
  UNIQUE KEY `uq_auth_session_token_hash` (`session_token_hash`),
  KEY `user_id` (`user_id`),
  KEY `expires_at` (`expires_at`),
  KEY `revoked_at` (`revoked_at`),
  CONSTRAINT `auth_session_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ============================================================================
-- 六、关怀档案生命周期事件
-- ============================================================================

CREATE TABLE `care_case_event` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `care_case_id` int NOT NULL,
  `student_id` int NOT NULL,
  `event_type` varchar(64) NOT NULL,
  `from_status` varchar(32) DEFAULT NULL,
  `to_status` varchar(32) DEFAULT NULL,
  `operator_id` int DEFAULT NULL,
  `reason` varchar(255) DEFAULT NULL,
  `confirmed_facts` text,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `care_case_id` (`care_case_id`),
  KEY `student_id` (`student_id`),
  KEY `operator_id` (`operator_id`),
  KEY `event_type_created_at` (`event_type`,`created_at`),
  CONSTRAINT `care_case_event_ibfk_1` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`),
  CONSTRAINT `care_case_event_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `care_case_event_ibfk_3` FOREIGN KEY (`operator_id`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

SELECT 'care_case_event' AS source_table, e.id
FROM care_case_event e
JOIN student_care_case c ON c.id = e.care_case_id
WHERE c.student_id <> e.student_id;

-- ============================================================================
-- 七、答卷和目标行历史追溯字段
-- ============================================================================

ALTER TABLE `assessment_session`
  DROP INDEX `uq_session_task_student`,
  ADD COLUMN `tested_at` datetime DEFAULT NULL AFTER `submitted_at`,
  ADD COLUMN `tested_at_source` varchar(32) NOT NULL DEFAULT 'PENDING_VERIFICATION' AFTER `tested_at`,
  ADD COLUMN `import_batch_id` int DEFAULT NULL AFTER `source`,
  ADD COLUMN `attempt_no` int NOT NULL DEFAULT 1 AFTER `task_id`,
  ADD COLUMN `school_id` int DEFAULT NULL AFTER `student_id`,
  ADD COLUMN `source_type` varchar(32) NOT NULL DEFAULT 'ONLINE' AFTER `source`,
  ADD COLUMN `external_source_system` varchar(128) DEFAULT NULL AFTER `source_type`,
  ADD COLUMN `external_result_id` varchar(128) DEFAULT NULL AFTER `external_source_system`,
  ADD COLUMN `conflict_status` varchar(32) NOT NULL DEFAULT 'NONE' AFTER `external_result_id`,
  ADD COLUMN `is_effective` tinyint(1) NOT NULL DEFAULT '1' AFTER `conflict_status`,
  ADD COLUMN `supersedes_session_id` int DEFAULT NULL AFTER `is_effective`,
  ADD COLUMN `age_at_test` int DEFAULT NULL AFTER `import_batch_id`,
  ADD COLUMN `answer_snapshot_hash` char(64) DEFAULT NULL AFTER `age_at_test`,
  ADD COLUMN `answer_hash_algorithm` varchar(32) DEFAULT NULL AFTER `answer_snapshot_hash`,
  ADD COLUMN `calculation_status` varchar(32) NOT NULL DEFAULT 'PENDING' AFTER `answer_hash_algorithm`,
  ADD COLUMN `calculation_error` varchar(1000) DEFAULT NULL AFTER `calculation_status`,
  ADD COLUMN `effective_task_student_key` varchar(96) GENERATED ALWAYS AS (
    CASE WHEN `is_effective` = 1 AND `task_id` IS NOT NULL
      THEN CONCAT(`task_id`, ':', `student_id`) ELSE NULL END
  ) STORED AFTER `calculation_error`,
  ADD KEY `ix_session_student_tested_at` (`student_id`,`tested_at`),
  ADD KEY `ix_session_import_batch_id` (`import_batch_id`),
  ADD KEY `ix_session_calculation_status` (`calculation_status`),
  ADD KEY `ix_session_school_student` (`school_id`,`student_id`),
  ADD KEY `ix_session_conflict_status` (`conflict_status`),
  ADD KEY `ix_session_external_result` (`external_source_system`,`external_result_id`),
  ADD UNIQUE KEY `uq_session_id_student` (`id`,`student_id`),
  ADD UNIQUE KEY `uq_session_task_student_attempt` (`task_id`,`student_id`,`attempt_no`),
  ADD UNIQUE KEY `uq_session_effective_task_student` (`effective_task_student_key`),
  ADD UNIQUE KEY `uq_session_external_source_result` (`external_source_system`,`external_result_id`),
  ADD CONSTRAINT `assessment_session_ibfk_4` FOREIGN KEY (`import_batch_id`) REFERENCES `assessment_import_batch` (`id`),
  ADD CONSTRAINT `assessment_session_fk_supersedes` FOREIGN KEY (`supersedes_session_id`) REFERENCES `assessment_session` (`id`);

UPDATE `assessment_session`
SET `source_type` = CASE
  WHEN `source` = 'IMPORTED' THEN 'EXTERNAL_FULL_ANSWER'
  ELSE 'ONLINE'
END
WHERE `source_type` = 'ONLINE';

UPDATE `assessment_session` s
JOIN `student` st ON st.id = s.student_id
SET s.school_id = st.school_id,
    s.age_at_test = COALESCE(s.age_at_test, st.age)
WHERE s.school_id IS NULL;

-- 一个任务学生可以保留多条来源事实（attempt_no），但effective_task_student_key保证同一任务学生只有一条is_effective=1。
-- external_source_system + external_result_id用于外部平台结果幂等；没有外部编号时由应用按学生、测评时间和量表版本二次判重。

ALTER TABLE `assessment_target`
  ADD COLUMN `school_id_snapshot` int DEFAULT NULL AFTER `student_id`,
  ADD COLUMN `student_no_snapshot` varchar(64) DEFAULT NULL AFTER `student_id`,
  ADD COLUMN `student_name_snapshot` varchar(64) DEFAULT NULL AFTER `student_no_snapshot`,
  ADD COLUMN `grade_name_snapshot` varchar(64) DEFAULT NULL AFTER `student_name_snapshot`,
  ADD COLUMN `class_name_snapshot` varchar(64) DEFAULT NULL AFTER `grade_name_snapshot`,
  ADD COLUMN `gender_snapshot` varchar(16) DEFAULT NULL AFTER `class_name_snapshot`,
  ADD COLUMN `age_snapshot` int DEFAULT NULL AFTER `gender_snapshot`,
  ADD COLUMN `participation_disposition` varchar(32) NOT NULL DEFAULT 'REQUIRED' AFTER `age_snapshot`,
  ADD COLUMN `disposition_reason` varchar(128) DEFAULT NULL AFTER `participation_disposition`,
  ADD COLUMN `disposition_note` varchar(1000) DEFAULT NULL AFTER `disposition_reason`,
  ADD COLUMN `marked_by` int DEFAULT NULL AFTER `disposition_note`,
  ADD COLUMN `marked_at` datetime DEFAULT NULL AFTER `marked_by`,
  ADD COLUMN `target_source` varchar(32) NOT NULL DEFAULT 'TASK_SCOPE' AFTER `marked_at`,
  ADD COLUMN `supplemented_from_batch_id` int DEFAULT NULL AFTER `target_source`,
  ADD COLUMN `effective_session_id` int DEFAULT NULL AFTER `supplemented_from_batch_id`,
  ADD COLUMN `effective_external_result_id` bigint DEFAULT NULL AFTER `effective_session_id`,
  ADD KEY `ix_target_task_status` (`task_id`,`status`);

UPDATE `assessment_target` t
JOIN `student` st ON st.id = t.student_id
SET t.school_id_snapshot = st.school_id
WHERE t.school_id_snapshot IS NULL;

ALTER TABLE `assessment_target`
  ADD KEY `ix_target_participation` (`task_id`,`participation_disposition`),
  ADD KEY `ix_target_effective_session` (`effective_session_id`),
  ADD KEY `ix_target_effective_external` (`effective_external_result_id`),
  ADD CONSTRAINT `assessment_target_fk_marked_by` FOREIGN KEY (`marked_by`) REFERENCES `user_account` (`id`),
  ADD CONSTRAINT `assessment_target_fk_supplement_batch` FOREIGN KEY (`supplemented_from_batch_id`) REFERENCES `assessment_import_batch` (`id`),
  ADD CONSTRAINT `assessment_target_fk_effective_session` FOREIGN KEY (`effective_session_id`) REFERENCES `assessment_session` (`id`),
  ADD CONSTRAINT `assessment_target_fk_effective_external` FOREIGN KEY (`effective_external_result_id`) REFERENCES `assessment_external_result` (`id`);

-- 现有会话的tested_at由应用迁移服务按来源回填；不能简单用created_at替代真实测评时间。
-- 新建会话必须显式提供tested_at，未能回填的历史记录标记为待核验。

-- ============================================================================
-- 八、风险信号模型
-- ============================================================================

ALTER TABLE `risk_event`
  ADD COLUMN `signal_type` varchar(64) DEFAULT NULL AFTER `risk_type`,
  ADD COLUMN `requires_manual_review` tinyint(1) NOT NULL DEFAULT '0' AFTER `signal_type`,
  ADD COLUMN `rule_version` varchar(64) NOT NULL DEFAULT 'LEGACY_UNKNOWN' AFTER `trigger_rule`,
  ADD KEY `ix_risk_event_status_created_at` (`status`,`created_at`);

UPDATE `risk_event`
SET `signal_type` = CASE
  WHEN `risk_type` = 'MANUAL_REVIEW_REQUIRED'
    OR `risk_type` = 'KEY_QUESTION_TRIGGERED'
    OR `trigger_rule` IN ('KEY_QUESTION_85','KEY_QUESTION_97') THEN 'MANUAL_REVIEW_REQUIRED'
  WHEN `risk_type` = 'RETEST_RECOMMENDED' THEN 'RETEST_RECOMMENDED'
  WHEN `risk_type` IN ('HIGH_TOTAL_SCORE','HIGH_DIMENSION_SCORE','SCREENING_SIGNAL') THEN 'SCREENING_SIGNAL'
  ELSE NULL
END,
`requires_manual_review` = CASE
  WHEN `risk_type` = 'MANUAL_REVIEW_REQUIRED'
    OR `risk_type` = 'KEY_QUESTION_TRIGGERED'
    OR `trigger_rule` IN ('KEY_QUESTION_85','KEY_QUESTION_97') THEN 1
  WHEN `risk_type` IN ('RETEST_RECOMMENDED','HIGH_TOTAL_SCORE','HIGH_DIMENSION_SCORE','SCREENING_SIGNAL') THEN 0
  ELSE NULL
END,
`rule_version` = COALESCE(
  (SELECT ar.rule_version FROM assessment_result ar WHERE ar.session_id = risk_event.session_id LIMIT 1),
  'LEGACY_UNKNOWN'
)
WHERE `signal_type` IS NULL;

-- 对历史重点题事件统一纠正为人工复核信号；它们不是普通维度筛查信号。
UPDATE `risk_event`
SET `signal_type` = 'MANUAL_REVIEW_REQUIRED',
    `requires_manual_review` = 1
WHERE `risk_type` = 'KEY_QUESTION_TRIGGERED'
   OR `trigger_rule` IN ('KEY_QUESTION_85','KEY_QUESTION_97');

ALTER TABLE `risk_event`
  MODIFY COLUMN `signal_type` varchar(64) NOT NULL,
  ADD UNIQUE KEY `uq_risk_event_session_trigger_rule` (`session_id`,`trigger_rule`,`rule_version`);

-- ============================================================================
-- 九、关怀档案关联和状态信息
-- ============================================================================

ALTER TABLE `student_care_case`
  ADD COLUMN `closed_by` int DEFAULT NULL AFTER `close_note`,
  ADD COLUMN `reopened_by` int DEFAULT NULL AFTER `reopened_at`,
  ADD COLUMN `reopen_reason` varchar(255) DEFAULT NULL AFTER `reopened_by`,
  ADD COLUMN `last_reviewed_at` datetime DEFAULT NULL AFTER `reopen_reason`,
  ADD COLUMN `case_version` int NOT NULL DEFAULT 1 AFTER `last_reviewed_at`,
  ADD COLUMN `active_student_id` int GENERATED ALWAYS AS (
    CASE WHEN `status` <> 'CLOSED' THEN `student_id` ELSE NULL END
  ) STORED,
  ADD KEY `ix_care_case_status_owner_updated` (`status`,`owner_id`,`updated_at`),
  ADD KEY `ix_care_case_active_student` (`active_student_id`),
  ADD UNIQUE KEY `uq_care_case_id_student` (`id`,`student_id`),
  ADD CONSTRAINT `student_care_case_ibfk_3` FOREIGN KEY (`closed_by`) REFERENCES `user_account` (`id`),
  ADD CONSTRAINT `student_care_case_ibfk_4` FOREIGN KEY (`reopened_by`) REFERENCES `user_account` (`id`),
  ADD UNIQUE KEY `uq_care_case_one_active_per_student` (`active_student_id`);

ALTER TABLE `manual_review`
  ADD COLUMN `care_case_id` int DEFAULT NULL AFTER `risk_event_id`,
  ADD COLUMN `student_id` int DEFAULT NULL AFTER `care_case_id`,
  ADD KEY `ix_manual_review_care_case` (`care_case_id`),
  ADD KEY `ix_manual_review_student` (`student_id`),
  ADD CONSTRAINT `manual_review_ibfk_3` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`),
  ADD CONSTRAINT `manual_review_ibfk_4` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`);

ALTER TABLE `follow_up_record`
  ADD COLUMN `care_case_id` int DEFAULT NULL AFTER `student_id`,
  ADD KEY `ix_follow_up_case_date` (`care_case_id`,`next_follow_up_date`),
  ADD KEY `ix_follow_up_status_date` (`status`,`next_follow_up_date`),
  ADD CONSTRAINT `follow_up_record_ibfk_3` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`);

ALTER TABLE `family_contact_record`
  ADD COLUMN `care_case_id` int DEFAULT NULL AFTER `student_id`,
  ADD KEY `ix_family_contact_case` (`care_case_id`),
  ADD KEY `ix_family_contact_next_date` (`next_contact_date`),
  ADD CONSTRAINT `family_contact_record_ibfk_3` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`);

ALTER TABLE `retest_plan`
  ADD COLUMN `care_case_id` int DEFAULT NULL AFTER `student_id`,
  ADD COLUMN `completed_at` datetime DEFAULT NULL AFTER `completed_session_id`,
  ADD COLUMN `cancelled_at` datetime DEFAULT NULL AFTER `completed_at`,
  ADD COLUMN `cancel_reason` varchar(255) DEFAULT NULL AFTER `cancelled_at`,
  ADD KEY `ix_retest_case_status_date` (`care_case_id`,`status`,`planned_date`),
  ADD CONSTRAINT `retest_plan_ibfk_5` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`);

-- 新增care_case_id字段后再检查历史记录是否跨学生关联。
SELECT 'follow_up_record' AS source_table, f.id
FROM follow_up_record f
JOIN student_care_case c ON c.id = f.care_case_id
WHERE c.student_id <> f.student_id
UNION ALL
SELECT 'family_contact_record', f.id
FROM family_contact_record f
JOIN student_care_case c ON c.id = f.care_case_id
WHERE c.student_id <> f.student_id
UNION ALL
SELECT 'retest_plan', r.id
FROM retest_plan r
JOIN student_care_case c ON c.id = r.care_case_id
WHERE c.student_id <> r.student_id;

-- 历史人工复核先从风险事件回填学生；无法回填的记录必须由迁移程序标记待人工归档。
UPDATE `manual_review` mr
JOIN `risk_event` re ON re.id = mr.risk_event_id
SET mr.student_id = re.student_id
WHERE mr.student_id IS NULL;

-- 复合外键防止care_case_id与student_id指向不同学生。
ALTER TABLE `care_case_event`
  ADD CONSTRAINT `care_case_event_fk_case_student`
    FOREIGN KEY (`care_case_id`,`student_id`) REFERENCES `student_care_case` (`id`,`student_id`);

ALTER TABLE `manual_review`
  ADD CONSTRAINT `manual_review_fk_case_student`
    FOREIGN KEY (`care_case_id`,`student_id`) REFERENCES `student_care_case` (`id`,`student_id`);

ALTER TABLE `follow_up_record`
  ADD CONSTRAINT `follow_up_record_fk_case_student`
    FOREIGN KEY (`care_case_id`,`student_id`) REFERENCES `student_care_case` (`id`,`student_id`);

ALTER TABLE `family_contact_record`
  ADD CONSTRAINT `family_contact_record_fk_case_student`
    FOREIGN KEY (`care_case_id`,`student_id`) REFERENCES `student_care_case` (`id`,`student_id`);

ALTER TABLE `retest_plan`
  ADD CONSTRAINT `retest_plan_fk_case_student`
    FOREIGN KEY (`care_case_id`,`student_id`) REFERENCES `student_care_case` (`id`,`student_id`),
  ADD CONSTRAINT `retest_plan_fk_source_student`
    FOREIGN KEY (`source_session_id`,`student_id`) REFERENCES `assessment_session` (`id`,`student_id`),
  ADD CONSTRAINT `retest_plan_fk_completed_student`
    FOREIGN KEY (`completed_session_id`,`student_id`) REFERENCES `assessment_session` (`id`,`student_id`);

-- ============================================================================
-- 十、组织一致性约束
-- ============================================================================

-- 先建立复合唯一键，再建立复合外键，确保年级、班级和学生属于同一学校。
SELECT 'assessment_target' AS source_table, t.id
FROM assessment_target t
WHERE t.school_id_snapshot IS NULL;

SELECT 'assessment_session' AS source_table, s.id
FROM assessment_session s
WHERE s.school_id IS NULL;

ALTER TABLE `assessment_task`
  ADD UNIQUE KEY `uq_assessment_task_school_id` (`id`,`school_id`);

ALTER TABLE `grade`
  ADD UNIQUE KEY `uq_grade_school_id` (`school_id`,`id`);

ALTER TABLE `class_group`
  ADD UNIQUE KEY `uq_class_school_id` (`school_id`,`id`),
  ADD CONSTRAINT `class_group_ibfk_3` FOREIGN KEY (`school_id`,`grade_id`) REFERENCES `grade` (`school_id`,`id`);

ALTER TABLE `student`
  ADD UNIQUE KEY `uq_student_school_id` (`school_id`,`id`),
  ADD CONSTRAINT `student_ibfk_4` FOREIGN KEY (`school_id`,`grade_id`) REFERENCES `grade` (`school_id`,`id`),
  ADD CONSTRAINT `student_ibfk_5` FOREIGN KEY (`school_id`,`class_id`) REFERENCES `class_group` (`school_id`,`id`);

ALTER TABLE `assessment_task_scope`
  ADD CONSTRAINT `assessment_task_scope_fk_task_school`
    FOREIGN KEY (`task_id`,`school_id`) REFERENCES `assessment_task` (`id`,`school_id`),
  ADD CONSTRAINT `assessment_task_scope_fk_school_grade`
    FOREIGN KEY (`school_id`,`grade_id`) REFERENCES `grade` (`school_id`,`id`),
  ADD CONSTRAINT `assessment_task_scope_fk_school_class`
    FOREIGN KEY (`school_id`,`class_id`) REFERENCES `class_group` (`school_id`,`id`),
  ADD CONSTRAINT `assessment_task_scope_fk_school_student`
    FOREIGN KEY (`school_id`,`student_id`) REFERENCES `student` (`school_id`,`id`);

-- 批次、任务、目标、会话必须属于同一学校；任务外学生只能进入外部原始事实表，不能静默成为任务完成者。
ALTER TABLE `assessment_import_batch`
  ADD CONSTRAINT `assessment_import_batch_fk_task_school`
    FOREIGN KEY (`task_id`,`school_id`) REFERENCES `assessment_task` (`id`,`school_id`);

ALTER TABLE `assessment_external_result`
  ADD CONSTRAINT `assessment_external_result_fk_task_school`
    FOREIGN KEY (`task_id`,`school_id`) REFERENCES `assessment_task` (`id`,`school_id`),
  ADD CONSTRAINT `assessment_external_result_fk_student_school`
    FOREIGN KEY (`school_id`,`student_id`) REFERENCES `student` (`school_id`,`id`),
  ADD CONSTRAINT `assessment_external_result_fk_session_student`
    FOREIGN KEY (`applied_session_id`,`student_id`) REFERENCES `assessment_session` (`id`,`student_id`);

ALTER TABLE `assessment_import_row`
  ADD CONSTRAINT `assessment_import_row_fk_session_student`
    FOREIGN KEY (`session_id`,`student_id`) REFERENCES `assessment_session` (`id`,`student_id`),
  ADD CONSTRAINT `assessment_import_row_fk_matched_school_grade`
    FOREIGN KEY (`matched_school_id`,`matched_grade_id`) REFERENCES `grade` (`school_id`,`id`),
  ADD CONSTRAINT `assessment_import_row_fk_matched_school_class`
    FOREIGN KEY (`matched_school_id`,`matched_class_id`) REFERENCES `class_group` (`school_id`,`id`);

ALTER TABLE `assessment_target`
  MODIFY COLUMN `school_id_snapshot` int NOT NULL,
  ADD CONSTRAINT `assessment_target_fk_task_school`
    FOREIGN KEY (`task_id`,`school_id_snapshot`) REFERENCES `assessment_task` (`id`,`school_id`),
  ADD CONSTRAINT `assessment_target_fk_student_school`
    FOREIGN KEY (`school_id_snapshot`,`student_id`) REFERENCES `student` (`school_id`,`id`),
  ADD CONSTRAINT `assessment_target_fk_effective_session_student`
    FOREIGN KEY (`effective_session_id`,`student_id`) REFERENCES `assessment_session` (`id`,`student_id`),
  ADD CONSTRAINT `assessment_target_fk_effective_external_student`
    FOREIGN KEY (`effective_external_result_id`,`student_id`) REFERENCES `assessment_external_result` (`id`,`student_id`);

ALTER TABLE `assessment_session`
  MODIFY COLUMN `school_id` int NOT NULL,
  ADD CONSTRAINT `assessment_session_fk_task_school`
    FOREIGN KEY (`task_id`,`school_id`) REFERENCES `assessment_task` (`id`,`school_id`),
  ADD CONSTRAINT `assessment_session_fk_student_school`
    FOREIGN KEY (`school_id`,`student_id`) REFERENCES `student` (`school_id`,`id`);

SELECT 'assessment_target' AS source_table, t.id
FROM assessment_target t
WHERE t.school_id_snapshot IS NULL
UNION ALL
SELECT 'assessment_session', s.id
FROM assessment_session s
WHERE s.school_id IS NULL;

-- user_scope仍需应用层验证scope_type与非空列的一致性：
-- SCHOOL只允许school_id，GRADE只允许grade_id，CLASS只允许class_id，STUDENT只允许student_id。
-- 由于项目兼容MySQL 8.0.13，不能把CHECK作为唯一安全来源，服务层必须保留校验。

-- ============================================================================
-- 十一、规则版本和审计增强
-- ============================================================================

ALTER TABLE `scale_rule`
  ADD UNIQUE KEY `uq_scale_rule_version` (`scale_id`,`rule_version`),
  ADD KEY `ix_scale_rule_status` (`scale_id`,`status`);

ALTER TABLE `audit_log`
  ADD COLUMN `event_id` char(36) DEFAULT NULL AFTER `id`,
  ADD COLUMN `actor_account_snapshot` varchar(128) DEFAULT NULL AFTER `actor_role`,
  ADD COLUMN `request_id` varchar(64) DEFAULT NULL AFTER `user_agent`,
  ADD COLUMN `detail_json` json DEFAULT NULL AFTER `detail`,
  ADD COLUMN `result_code` varchar(64) DEFAULT NULL AFTER `result`,
  ADD COLUMN `audit_hash` char(64) DEFAULT NULL AFTER `result_code`,
  ADD UNIQUE KEY `uq_audit_event_id` (`event_id`),
  ADD KEY `ix_audit_created_actor_action` (`created_at`,`actor_role`,`action`),
  ADD KEY `ix_audit_request_id` (`request_id`);

-- detail_json只保存可追溯的业务元数据，例如：
-- {"mask_level":"MASKED","resolution":"overwrite","created_rows":10}
-- 禁止写入完整答卷、重点题回答和家庭回访正文。

-- ============================================================================
-- 十二、建议的应用回填任务
-- ============================================================================

-- 以下不是自动执行SQL，而是迁移完成后必须由应用迁移脚本执行的任务：
-- 1. 回填assessment_target的六个快照字段；
-- 2. 回填assessment_session.tested_at和tested_at_source；无法确认的记录标记PENDING_VERIFICATION；
-- 3. 将已有人工复核、跟进、家庭回访、复测记录关联到可确定的care_case_id；
-- 4. 为现有risk_event补充signal_type和requires_manual_review；
-- 5. 为已有care_case生成OPENED/CLOSED历史事件；
-- 6. 为已有用户创建auth_session不需要，历史Token全部失效；
-- 7. 校验跨学校关系和同一学生多条在办档案；
-- 8. 回填manual_review.student_id；无法匹配的记录不得进入新写入路径；
-- 9. 通过后再启用所有新增唯一索引和复合外键相关的写入逻辑。

-- 新写入约束（由应用服务强制，MySQL 8.0.13不依赖CHECK作为唯一来源）：
-- * assessment_task_scope的scope_type与非空列必须一一对应；
-- * 新建manual_review/follow_up/family_contact/retest_plan必须有care_case_id；
-- * care_case状态变更必须携带case_version并使用乐观锁；
-- * export_job的field_policy只能来自后端白名单；
-- * JWT中的jti必须对应auth_session.id或jti，撤销后敏感接口拒绝访问；
-- * audit_log只允许追加，detail_json不得包含原始答卷、重点题答案和家庭回访正文。

-- ============================================================================
-- 十三、迁移完成验证
-- ============================================================================

SHOW COLUMNS FROM `assessment_session` LIKE 'tested_at';
SHOW COLUMNS FROM `risk_event` LIKE 'signal_type';
SHOW COLUMNS FROM `student_care_case` LIKE 'active_student_id';
SHOW COLUMNS FROM `audit_log` LIKE 'detail_json';

SELECT COUNT(*) AS active_case_duplicate_count
FROM (
  SELECT student_id
  FROM student_care_case
  WHERE status <> 'CLOSED'
  GROUP BY student_id
  HAVING COUNT(*) > 1
) t;

SELECT COUNT(*) AS mismatched_care_record_count
FROM (
  SELECT e.id
  FROM care_case_event e
  JOIN student_care_case c ON c.id = e.care_case_id
  WHERE e.student_id <> c.student_id
  UNION ALL
  SELECT f.id
  FROM follow_up_record f
  JOIN student_care_case c ON c.id = f.care_case_id
  WHERE f.student_id <> c.student_id
  UNION ALL
  SELECT f.id
  FROM family_contact_record f
  JOIN student_care_case c ON c.id = f.care_case_id
  WHERE f.student_id <> c.student_id
  UNION ALL
  SELECT r.id
  FROM retest_plan r
  JOIN student_care_case c ON c.id = r.care_case_id
  WHERE r.student_id <> c.student_id
) t;

SELECT COUNT(*) AS mismatched_retest_session_count
FROM retest_plan r
JOIN assessment_session s
  ON s.id IN (r.source_session_id, r.completed_session_id)
WHERE s.student_id <> r.student_id;

-- 迁移结束后必须执行：
-- 1. Alembic/迁移版本登记；
-- 2. Scale Engine单元测试；
-- 3. 权限越权测试；
-- 4. 外部导入覆盖/跳过测试；
-- 5. 重点题风险事件幂等测试；
-- 6. 真MySQL 8开启FOREIGN_KEY_CHECKS=1完整执行测试。
-- 7. 迁移执行器登记版本号、文件SHA-256和实际执行人。
-- 8. 不允许把本参考脚本重复执行；重复执行必须由Alembic版本检查拦截。
