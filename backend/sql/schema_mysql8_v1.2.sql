-- MySQL dump 10.13  Distrib 8.0.42, for macos15 (arm64)
--
-- Host: localhost    Database: xinliceping
-- ------------------------------------------------------
-- Server version	8.4.4

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `alembic_version`
--

DROP TABLE IF EXISTS `alembic_version`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `alembic_version` (
  `version_num` varchar(32) NOT NULL,
  PRIMARY KEY (`version_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_answer`
--

DROP TABLE IF EXISTS `assessment_answer`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `assessment_answer` (
  `id` int NOT NULL AUTO_INCREMENT,
  `session_id` int NOT NULL,
  `question_id` int NOT NULL,
  `answer` varchar(8) NOT NULL,
  `score` int NOT NULL,
  `answered_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_answer_session_question` (`session_id`,`question_id`),
  KEY `question_id` (`question_id`),
  CONSTRAINT `assessment_answer_ibfk_1` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_answer_ibfk_2` FOREIGN KEY (`question_id`) REFERENCES `scale_question` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=26847 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_external_result`
--

DROP TABLE IF EXISTS `assessment_external_result`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  UNIQUE KEY `uq_external_result_batch_row` (`batch_id`,`row_id`),
  UNIQUE KEY `uq_external_result_id_student` (`id`,`student_id`),
  UNIQUE KEY `uq_external_result_source_key` (`source_system`,`external_result_id`),
  KEY `assessment_external_result_fk_session_student` (`applied_session_id`,`student_id`),
  KEY `assessment_external_result_fk_row` (`row_id`),
  KEY `assessment_external_result_fk_student_school` (`school_id`,`student_id`),
  KEY `assessment_external_result_fk_student` (`student_id`),
  KEY `assessment_external_result_fk_task_school` (`task_id`,`school_id`),
  KEY `ix_external_result_task_student` (`task_id`,`student_id`),
  KEY `ix_external_result_verification` (`verification_status`),
  CONSTRAINT `assessment_external_result_fk_batch` FOREIGN KEY (`batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `assessment_external_result_fk_row` FOREIGN KEY (`row_id`) REFERENCES `assessment_import_row` (`id`),
  CONSTRAINT `assessment_external_result_fk_school` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_external_result_fk_session` FOREIGN KEY (`applied_session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_external_result_fk_session_student` FOREIGN KEY (`applied_session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`),
  CONSTRAINT `assessment_external_result_fk_student` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_external_result_fk_student_school` FOREIGN KEY (`school_id`, `student_id`) REFERENCES `student` (`school_id`, `id`),
  CONSTRAINT `assessment_external_result_fk_task` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_external_result_fk_task_school` FOREIGN KEY (`task_id`, `school_id`) REFERENCES `assessment_task` (`id`, `school_id`)
) ENGINE=InnoDB AUTO_INCREMENT=71 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_import_batch`
--

DROP TABLE IF EXISTS `assessment_import_batch`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  `total_rows` int NOT NULL DEFAULT '0',
  `created_rows` int NOT NULL DEFAULT '0',
  `updated_rows` int NOT NULL DEFAULT '0',
  `skipped_rows` int NOT NULL DEFAULT '0',
  `error_rows` int NOT NULL DEFAULT '0',
  `status` varchar(32) NOT NULL DEFAULT 'PREVIEW',
  `imported_by` int NOT NULL,
  `task_id` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `batch_name` varchar(128) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_assessment_import_batch_no` (`batch_no`),
  KEY `assessment_import_batch_fk_task_school` (`task_id`,`school_id`),
  KEY `imported_by` (`imported_by`),
  KEY `ix_assessment_import_school_created` (`school_id`,`created_at`),
  KEY `ix_import_duplicate_of` (`duplicate_of_batch_id`),
  KEY `ix_import_file_hash` (`source_system`,`file_sha256`),
  KEY `task_id` (`task_id`),
  KEY `tested_at` (`tested_at`),
  CONSTRAINT `assessment_import_batch_fk_task_school` FOREIGN KEY (`task_id`, `school_id`) REFERENCES `assessment_task` (`id`, `school_id`),
  CONSTRAINT `assessment_import_batch_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_import_batch_ibfk_2` FOREIGN KEY (`imported_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `assessment_import_batch_ibfk_3` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_import_batch_ibfk_4` FOREIGN KEY (`duplicate_of_batch_id`) REFERENCES `assessment_import_batch` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=25 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_import_row`
--

DROP TABLE IF EXISTS `assessment_import_row`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  `conflict_resolution` varchar(32) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_import_row_batch_no` (`batch_id`,`row_no`),
  KEY `assessment_import_row_fk_matched_school_class` (`matched_school_id`,`matched_class_id`),
  KEY `assessment_import_row_ibfk_4` (`resolved_by`),
  KEY `assessment_import_row_fk_session_student` (`session_id`,`student_id`),
  KEY `ix_import_row_external_record` (`external_result_record_id`),
  KEY `ix_import_row_match_status` (`batch_id`,`match_status`),
  KEY `ix_import_row_matched_grade_class` (`matched_school_id`,`matched_grade_id`,`matched_class_id`),
  KEY `ix_import_row_normalized_match` (`normalized_grade_name`,`normalized_class_name`,`normalized_name`),
  KEY `session_id` (`session_id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `assessment_import_row_fk_external_record` FOREIGN KEY (`external_result_record_id`) REFERENCES `assessment_external_result` (`id`),
  CONSTRAINT `assessment_import_row_fk_matched_school_class` FOREIGN KEY (`matched_school_id`, `matched_class_id`) REFERENCES `class_group` (`school_id`, `id`),
  CONSTRAINT `assessment_import_row_fk_matched_school_grade` FOREIGN KEY (`matched_school_id`, `matched_grade_id`) REFERENCES `grade` (`school_id`, `id`),
  CONSTRAINT `assessment_import_row_fk_session_student` FOREIGN KEY (`session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`),
  CONSTRAINT `assessment_import_row_ibfk_1` FOREIGN KEY (`batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_3` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_4` FOREIGN KEY (`resolved_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=358 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_result`
--

DROP TABLE IF EXISTS `assessment_result`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `assessment_result` (
  `id` int NOT NULL AUTO_INCREMENT,
  `session_id` int NOT NULL,
  `validity_score` int NOT NULL,
  `validity_status` varchar(32) NOT NULL,
  `total_score` int NOT NULL,
  `total_level` varchar(32) NOT NULL,
  `rule_version` varchar(64) NOT NULL,
  `calculated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `session_id` (`session_id`),
  CONSTRAINT `assessment_result_ibfk_1` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=256 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_scale`
--

DROP TABLE IF EXISTS `assessment_scale`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `assessment_scale` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `version` varchar(64) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'DRAFT',
  `published_at` datetime DEFAULT NULL,
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_scale_code_version` (`code`,`version`),
  KEY `created_by` (`created_by`),
  CONSTRAINT `assessment_scale_ibfk_1` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_session`
--

DROP TABLE IF EXISTS `assessment_session`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `assessment_session` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_id` int DEFAULT NULL,
  `student_id` int NOT NULL,
  `scale_id` int NOT NULL,
  `scale_version` varchar(64) NOT NULL,
  `started_at` datetime DEFAULT NULL,
  `submitted_at` datetime DEFAULT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'IN_PROGRESS',
  `idempotency_key` varchar(128) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `duration_seconds` int DEFAULT NULL,
  `source` varchar(16) NOT NULL DEFAULT 'IN_SYSTEM',
  `tested_at` datetime DEFAULT NULL,
  `tested_at_source` varchar(32) NOT NULL DEFAULT 'PENDING_VERIFICATION',
  `import_batch_id` int DEFAULT NULL,
  `attempt_no` int NOT NULL DEFAULT '1',
  `school_id` int NOT NULL,
  `source_type` varchar(32) NOT NULL DEFAULT 'ONLINE',
  `external_source_system` varchar(128) DEFAULT NULL,
  `external_result_id` varchar(128) DEFAULT NULL,
  `conflict_status` varchar(32) NOT NULL DEFAULT 'NONE',
  `is_effective` tinyint(1) NOT NULL DEFAULT '1',
  `supersedes_session_id` int DEFAULT NULL,
  `age_at_test` int DEFAULT NULL,
  `answer_snapshot_hash` char(64) DEFAULT NULL,
  `answer_hash_algorithm` varchar(32) DEFAULT NULL,
  `calculation_status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `calculation_error` varchar(1000) DEFAULT NULL,
  `effective_task_student_key` varchar(96) GENERATED ALWAYS AS ((case when ((`is_effective` = 1) and (`task_id` is not null)) then concat(`task_id`,_utf8mb4':',`student_id`) else NULL end)) STORED,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_session_id_student` (`id`,`student_id`),
  UNIQUE KEY `uq_session_task_student_attempt` (`task_id`,`student_id`,`attempt_no`),
  UNIQUE KEY `uq_session_effective_task_student` (`effective_task_student_key`),
  UNIQUE KEY `uq_session_external_source_result` (`external_source_system`,`external_result_id`),
  KEY `scale_id` (`scale_id`),
  KEY `ix_session_calculation_status` (`calculation_status`),
  KEY `ix_session_conflict_status` (`conflict_status`),
  KEY `ix_session_external_result` (`external_source_system`,`external_result_id`),
  KEY `ix_session_import_batch_id` (`import_batch_id`),
  KEY `ix_session_school_student` (`school_id`,`student_id`),
  KEY `ix_session_student_tested_at` (`student_id`,`tested_at`),
  KEY `assessment_session_fk_task_school` (`task_id`,`school_id`),
  KEY `assessment_session_fk_supersedes` (`supersedes_session_id`),
  CONSTRAINT `assessment_session_fk_student_school` FOREIGN KEY (`school_id`, `student_id`) REFERENCES `student` (`school_id`, `id`),
  CONSTRAINT `assessment_session_fk_supersedes` FOREIGN KEY (`supersedes_session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_session_fk_task_school` FOREIGN KEY (`task_id`, `school_id`) REFERENCES `assessment_task` (`id`, `school_id`),
  CONSTRAINT `assessment_session_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_session_ibfk_2` FOREIGN KEY (`scale_id`) REFERENCES `assessment_scale` (`id`),
  CONSTRAINT `assessment_session_ibfk_4` FOREIGN KEY (`import_batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `fk_session_task` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=257 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_target`
--

DROP TABLE IF EXISTS `assessment_target`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `assessment_target` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_id` int NOT NULL,
  `student_id` int NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'NOT_STARTED',
  `assigned_at` datetime NOT NULL DEFAULT (now()),
  `completed_at` datetime DEFAULT NULL,
  `school_id_snapshot` int NOT NULL,
  `student_no_snapshot` varchar(64) DEFAULT NULL,
  `student_name_snapshot` varchar(64) DEFAULT NULL,
  `grade_name_snapshot` varchar(64) DEFAULT NULL,
  `class_name_snapshot` varchar(64) DEFAULT NULL,
  `gender_snapshot` varchar(16) DEFAULT NULL,
  `age_snapshot` int DEFAULT NULL,
  `participation_disposition` varchar(32) NOT NULL DEFAULT 'REQUIRED',
  `disposition_reason` varchar(128) DEFAULT NULL,
  `disposition_note` varchar(1000) DEFAULT NULL,
  `marked_by` int DEFAULT NULL,
  `marked_at` datetime DEFAULT NULL,
  `target_source` varchar(32) NOT NULL DEFAULT 'TASK_SCOPE',
  `supplemented_from_batch_id` int DEFAULT NULL,
  `effective_session_id` int DEFAULT NULL,
  `effective_external_result_id` bigint DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_target_task_student` (`task_id`,`student_id`),
  KEY `student_id` (`student_id`),
  KEY `ix_target_effective_external` (`effective_external_result_id`),
  KEY `ix_target_effective_session` (`effective_session_id`),
  KEY `ix_target_participation` (`task_id`,`participation_disposition`),
  KEY `ix_target_task_status` (`task_id`,`status`),
  KEY `assessment_target_fk_supplement_batch` (`supplemented_from_batch_id`),
  KEY `assessment_target_fk_task_school` (`task_id`,`school_id_snapshot`),
  KEY `assessment_target_fk_effective_external_student` (`effective_external_result_id`,`student_id`),
  KEY `assessment_target_fk_student_school` (`school_id_snapshot`,`student_id`),
  KEY `assessment_target_fk_marked_by` (`marked_by`),
  KEY `assessment_target_fk_effective_session_student` (`effective_session_id`,`student_id`),
  CONSTRAINT `assessment_target_fk_effective_external` FOREIGN KEY (`effective_external_result_id`) REFERENCES `assessment_external_result` (`id`),
  CONSTRAINT `assessment_target_fk_effective_external_student` FOREIGN KEY (`effective_external_result_id`, `student_id`) REFERENCES `assessment_external_result` (`id`, `student_id`),
  CONSTRAINT `assessment_target_fk_effective_session` FOREIGN KEY (`effective_session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_target_fk_effective_session_student` FOREIGN KEY (`effective_session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`),
  CONSTRAINT `assessment_target_fk_marked_by` FOREIGN KEY (`marked_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `assessment_target_fk_student_school` FOREIGN KEY (`school_id_snapshot`, `student_id`) REFERENCES `student` (`school_id`, `id`),
  CONSTRAINT `assessment_target_fk_supplement_batch` FOREIGN KEY (`supplemented_from_batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `assessment_target_fk_task_school` FOREIGN KEY (`task_id`, `school_id_snapshot`) REFERENCES `assessment_task` (`id`, `school_id`),
  CONSTRAINT `assessment_target_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_target_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=378 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_task`
--

DROP TABLE IF EXISTS `assessment_task`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `assessment_task` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_no` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `scale_id` int NOT NULL,
  `school_id` int NOT NULL,
  `scope_type` varchar(32) NOT NULL,
  `start_at` datetime DEFAULT NULL,
  `end_at` datetime DEFAULT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `source` varchar(16) NOT NULL DEFAULT 'IN_SYSTEM',
  PRIMARY KEY (`id`),
  UNIQUE KEY `task_no` (`task_no`),
  UNIQUE KEY `uq_assessment_task_school_id` (`id`,`school_id`),
  KEY `scale_id` (`scale_id`),
  KEY `school_id` (`school_id`),
  KEY `created_by` (`created_by`),
  CONSTRAINT `assessment_task_ibfk_1` FOREIGN KEY (`scale_id`) REFERENCES `assessment_scale` (`id`),
  CONSTRAINT `assessment_task_ibfk_2` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_task_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=20 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `assessment_task_scope`
--

DROP TABLE IF EXISTS `assessment_task_scope`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  KEY `assessment_task_scope_ibfk_6` (`created_by`),
  KEY `assessment_task_scope_fk_school_class` (`school_id`,`class_id`),
  KEY `assessment_task_scope_fk_school_grade` (`school_id`,`grade_id`),
  KEY `assessment_task_scope_fk_school_student` (`school_id`,`student_id`),
  KEY `assessment_task_scope_fk_task_school` (`task_id`,`school_id`),
  KEY `ix_task_scope_class` (`class_id`),
  KEY `ix_task_scope_grade` (`grade_id`),
  KEY `ix_task_scope_school` (`school_id`),
  KEY `ix_task_scope_student` (`student_id`),
  KEY `ix_task_scope_task` (`task_id`),
  CONSTRAINT `assessment_task_scope_fk_school_class` FOREIGN KEY (`school_id`, `class_id`) REFERENCES `class_group` (`school_id`, `id`),
  CONSTRAINT `assessment_task_scope_fk_school_grade` FOREIGN KEY (`school_id`, `grade_id`) REFERENCES `grade` (`school_id`, `id`),
  CONSTRAINT `assessment_task_scope_fk_school_student` FOREIGN KEY (`school_id`, `student_id`) REFERENCES `student` (`school_id`, `id`),
  CONSTRAINT `assessment_task_scope_fk_task_school` FOREIGN KEY (`task_id`, `school_id`) REFERENCES `assessment_task` (`id`, `school_id`),
  CONSTRAINT `assessment_task_scope_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_2` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_3` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_4` FOREIGN KEY (`class_id`) REFERENCES `class_group` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_5` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_task_scope_ibfk_6` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=5 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `audit_log`
--

DROP TABLE IF EXISTS `audit_log`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `audit_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `actor_user_id` int DEFAULT NULL,
  `actor_role` varchar(32) DEFAULT NULL,
  `action` varchar(128) NOT NULL,
  `resource_type` varchar(64) NOT NULL,
  `resource_id` varchar(128) DEFAULT NULL,
  `purpose` varchar(255) DEFAULT NULL,
  `ip` varchar(64) DEFAULT NULL,
  `user_agent` varchar(255) DEFAULT NULL,
  `result` varchar(32) NOT NULL,
  `detail` text,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `student_id` int DEFAULT NULL,
  `event_id` char(36) DEFAULT NULL,
  `actor_account_snapshot` varchar(128) DEFAULT NULL,
  `request_id` varchar(64) DEFAULT NULL,
  `detail_json` json DEFAULT NULL,
  `result_code` varchar(64) DEFAULT NULL,
  `audit_hash` char(64) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_audit_event_id` (`event_id`),
  KEY `actor_user_id` (`actor_user_id`),
  KEY `ix_audit_log_student_id` (`student_id`),
  KEY `ix_audit_created_actor_action` (`created_at`,`actor_role`,`action`),
  KEY `ix_audit_request_id` (`request_id`),
  CONSTRAINT `audit_log_ibfk_1` FOREIGN KEY (`actor_user_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `fk_audit_log_student_id` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=8741 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_session`
--

DROP TABLE IF EXISTS `auth_session`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  KEY `expires_at` (`expires_at`),
  KEY `revoked_at` (`revoked_at`),
  KEY `user_id` (`user_id`),
  CONSTRAINT `auth_session_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2971 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `care_case_event`
--

DROP TABLE IF EXISTS `care_case_event`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  KEY `care_case_event_fk_case_student` (`care_case_id`,`student_id`),
  KEY `care_case_id` (`care_case_id`),
  KEY `event_type_created_at` (`event_type`,`created_at`),
  KEY `operator_id` (`operator_id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `care_case_event_fk_case_student` FOREIGN KEY (`care_case_id`, `student_id`) REFERENCES `student_care_case` (`id`, `student_id`),
  CONSTRAINT `care_case_event_ibfk_1` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`),
  CONSTRAINT `care_case_event_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `care_case_event_ibfk_3` FOREIGN KEY (`operator_id`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=142 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `class_group`
--

DROP TABLE IF EXISTS `class_group`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `class_group` (
  `id` int NOT NULL AUTO_INCREMENT,
  `school_id` int NOT NULL,
  `grade_id` int NOT NULL,
  `name` varchar(64) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_class_school_id` (`school_id`,`id`),
  KEY `grade_id` (`grade_id`),
  KEY `class_group_ibfk_3` (`school_id`,`grade_id`),
  CONSTRAINT `class_group_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `class_group_ibfk_2` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`),
  CONSTRAINT `class_group_ibfk_3` FOREIGN KEY (`school_id`, `grade_id`) REFERENCES `grade` (`school_id`, `id`)
) ENGINE=InnoDB AUTO_INCREMENT=46 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `dimension_result`
--

DROP TABLE IF EXISTS `dimension_result`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `dimension_result` (
  `id` int NOT NULL AUTO_INCREMENT,
  `session_id` int NOT NULL,
  `dimension_code` varchar(64) NOT NULL,
  `score` int NOT NULL,
  `level` varchar(32) NOT NULL,
  `interpretation` text NOT NULL,
  `rule_version` varchar(64) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dimension_session_code` (`session_id`,`dimension_code`),
  CONSTRAINT `dimension_result_ibfk_1` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2041 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `export_job`
--

DROP TABLE IF EXISTS `export_job`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  `download_count` int NOT NULL DEFAULT '0',
  `expires_at` datetime DEFAULT NULL,
  `downloaded_at` datetime DEFAULT NULL,
  `revoked_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_export_job_no` (`job_no`),
  KEY `ix_export_job_expires_at` (`expires_at`),
  KEY `ix_export_job_requester_status` (`requested_by`,`status`),
  CONSTRAINT `export_job_ibfk_1` FOREIGN KEY (`requested_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=72 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `family_contact_record`
--

DROP TABLE IF EXISTS `family_contact_record`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `family_contact_record` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `operator_id` int NOT NULL,
  `contact_date` date NOT NULL,
  `contact_person` varchar(64) NOT NULL,
  `channel` varchar(64) NOT NULL,
  `result` varchar(128) NOT NULL,
  `support_status` varchar(128) NOT NULL,
  `confirmed_facts` text NOT NULL,
  `next_contact_date` date DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `care_case_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `operator_id` (`operator_id`),
  KEY `ix_family_contact_case` (`care_case_id`),
  KEY `ix_family_contact_next_date` (`next_contact_date`),
  KEY `family_contact_record_fk_case_student` (`care_case_id`,`student_id`),
  CONSTRAINT `family_contact_record_fk_case_student` FOREIGN KEY (`care_case_id`, `student_id`) REFERENCES `student_care_case` (`id`, `student_id`),
  CONSTRAINT `family_contact_record_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `family_contact_record_ibfk_2` FOREIGN KEY (`operator_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `family_contact_record_ibfk_3` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=21 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `follow_up_record`
--

DROP TABLE IF EXISTS `follow_up_record`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `follow_up_record` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `operator_id` int NOT NULL,
  `record_type` varchar(64) NOT NULL,
  `confirmed_facts` text NOT NULL,
  `next_follow_up_date` date NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `care_case_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `operator_id` (`operator_id`),
  KEY `ix_follow_up_case_date` (`care_case_id`,`next_follow_up_date`),
  KEY `ix_follow_up_status_date` (`status`,`next_follow_up_date`),
  KEY `follow_up_record_fk_case_student` (`care_case_id`,`student_id`),
  CONSTRAINT `follow_up_record_fk_case_student` FOREIGN KEY (`care_case_id`, `student_id`) REFERENCES `student_care_case` (`id`, `student_id`),
  CONSTRAINT `follow_up_record_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `follow_up_record_ibfk_2` FOREIGN KEY (`operator_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `follow_up_record_ibfk_3` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=84 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `grade`
--

DROP TABLE IF EXISTS `grade`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `grade` (
  `id` int NOT NULL AUTO_INCREMENT,
  `school_id` int NOT NULL,
  `name` varchar(64) NOT NULL,
  `sort_order` int NOT NULL DEFAULT '0',
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_grade_school_id` (`school_id`,`id`),
  CONSTRAINT `grade_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=16 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `manual_review`
--

DROP TABLE IF EXISTS `manual_review`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `manual_review` (
  `id` int NOT NULL AUTO_INCREMENT,
  `risk_event_id` int NOT NULL,
  `reviewer_id` int NOT NULL,
  `review_result` varchar(128) NOT NULL,
  `confirmed_facts` text NOT NULL,
  `next_action` varchar(128) DEFAULT NULL,
  `next_follow_up_date` date DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `care_case_id` int DEFAULT NULL,
  `student_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `risk_event_id` (`risk_event_id`),
  KEY `reviewer_id` (`reviewer_id`),
  KEY `ix_manual_review_care_case` (`care_case_id`),
  KEY `ix_manual_review_student` (`student_id`),
  KEY `manual_review_fk_case_student` (`care_case_id`,`student_id`),
  CONSTRAINT `manual_review_fk_case_student` FOREIGN KEY (`care_case_id`, `student_id`) REFERENCES `student_care_case` (`id`, `student_id`),
  CONSTRAINT `manual_review_ibfk_1` FOREIGN KEY (`risk_event_id`) REFERENCES `risk_event` (`id`),
  CONSTRAINT `manual_review_ibfk_2` FOREIGN KEY (`reviewer_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `manual_review_ibfk_3` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`),
  CONSTRAINT `manual_review_ibfk_4` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=52 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `retest_plan`
--

DROP TABLE IF EXISTS `retest_plan`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `retest_plan` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `source_session_id` int DEFAULT NULL,
  `planned_date` date NOT NULL,
  `reason` varchar(255) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'PLANNED',
  `created_by` int NOT NULL,
  `completed_session_id` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `care_case_id` int DEFAULT NULL,
  `completed_at` datetime DEFAULT NULL,
  `cancelled_at` datetime DEFAULT NULL,
  `cancel_reason` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `created_by` (`created_by`),
  KEY `ix_retest_case_status_date` (`care_case_id`,`status`,`planned_date`),
  KEY `retest_plan_fk_source_student` (`source_session_id`,`student_id`),
  KEY `retest_plan_fk_completed_student` (`completed_session_id`,`student_id`),
  KEY `retest_plan_fk_case_student` (`care_case_id`,`student_id`),
  CONSTRAINT `retest_plan_fk_case_student` FOREIGN KEY (`care_case_id`, `student_id`) REFERENCES `student_care_case` (`id`, `student_id`),
  CONSTRAINT `retest_plan_fk_completed_student` FOREIGN KEY (`completed_session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`),
  CONSTRAINT `retest_plan_fk_source_student` FOREIGN KEY (`source_session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`),
  CONSTRAINT `retest_plan_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `retest_plan_ibfk_2` FOREIGN KEY (`source_session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `retest_plan_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `retest_plan_ibfk_4` FOREIGN KEY (`completed_session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `retest_plan_ibfk_5` FOREIGN KEY (`care_case_id`) REFERENCES `student_care_case` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=12 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `risk_event`
--

DROP TABLE IF EXISTS `risk_event`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `risk_event` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `session_id` int NOT NULL,
  `risk_type` varchar(64) NOT NULL,
  `risk_level` varchar(64) NOT NULL,
  `trigger_rule` varchar(128) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'PENDING',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `reviewed_at` datetime DEFAULT NULL,
  `reviewed_by` int DEFAULT NULL,
  `signal_type` varchar(64) NOT NULL,
  `requires_manual_review` tinyint(1) NOT NULL DEFAULT '0',
  `rule_version` varchar(64) NOT NULL DEFAULT 'LEGACY_UNKNOWN',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_risk_event_session_trigger_rule` (`session_id`,`trigger_rule`,`rule_version`),
  KEY `student_id` (`student_id`),
  KEY `reviewed_by` (`reviewed_by`),
  KEY `ix_risk_event_status_created_at` (`status`,`created_at`),
  CONSTRAINT `risk_event_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `risk_event_ibfk_2` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `risk_event_ibfk_3` FOREIGN KEY (`reviewed_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=92 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `role_permission`
--

DROP TABLE IF EXISTS `role_permission`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `role_permission` (
  `id` int NOT NULL AUTO_INCREMENT,
  `role_code` varchar(32) NOT NULL,
  `capability_key` varchar(64) NOT NULL,
  `scope_level` varchar(32) NOT NULL,
  `updated_by` int DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_role_capability` (`role_code`,`capability_key`),
  KEY `updated_by` (`updated_by`),
  CONSTRAINT `role_permission_ibfk_1` FOREIGN KEY (`updated_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `scale_question`
--

DROP TABLE IF EXISTS `scale_question`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `scale_question` (
  `id` int NOT NULL AUTO_INCREMENT,
  `scale_id` int NOT NULL,
  `question_no` int NOT NULL,
  `question_text` text NOT NULL,
  `dimension_code` varchar(64) DEFAULT NULL,
  `is_validity_question` tinyint(1) NOT NULL DEFAULT '0',
  `is_key_question` tinyint(1) NOT NULL DEFAULT '0',
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_scale_question_no` (`scale_id`,`question_no`),
  CONSTRAINT `scale_question_ibfk_1` FOREIGN KEY (`scale_id`) REFERENCES `assessment_scale` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=101 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `scale_rule`
--

DROP TABLE IF EXISTS `scale_rule`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `scale_rule` (
  `id` int NOT NULL AUTO_INCREMENT,
  `scale_id` int NOT NULL,
  `rule_version` varchar(64) NOT NULL,
  `rule_type` varchar(64) NOT NULL,
  `config_json` json NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_scale_rule_version` (`scale_id`,`rule_version`),
  KEY `ix_scale_rule_status` (`scale_id`,`status`),
  CONSTRAINT `scale_rule_ibfk_1` FOREIGN KEY (`scale_id`) REFERENCES `assessment_scale` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=100 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `school`
--

DROP TABLE IF EXISTS `school`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `school` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `code` (`code`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `student`
--

DROP TABLE IF EXISTS `student`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `student` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_no` varchar(64) NOT NULL,
  `name` varchar(64) NOT NULL,
  `masked_name` varchar(64) NOT NULL,
  `school_id` int NOT NULL,
  `grade_id` int NOT NULL,
  `class_id` int NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `gender` varchar(16) DEFAULT NULL,
  `age` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_student_school_no` (`school_id`,`student_no`),
  UNIQUE KEY `uq_student_school_id` (`school_id`,`id`),
  KEY `grade_id` (`grade_id`),
  KEY `class_id` (`class_id`),
  KEY `student_ibfk_5` (`school_id`,`class_id`),
  KEY `student_ibfk_4` (`school_id`,`grade_id`),
  CONSTRAINT `student_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `student_ibfk_2` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`),
  CONSTRAINT `student_ibfk_3` FOREIGN KEY (`class_id`) REFERENCES `class_group` (`id`),
  CONSTRAINT `student_ibfk_4` FOREIGN KEY (`school_id`, `grade_id`) REFERENCES `grade` (`school_id`, `id`),
  CONSTRAINT `student_ibfk_5` FOREIGN KEY (`school_id`, `class_id`) REFERENCES `class_group` (`school_id`, `id`)
) ENGINE=InnoDB AUTO_INCREMENT=194 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `student_age_change_log`
--

DROP TABLE IF EXISTS `student_age_change_log`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
  KEY `student_age_change_log_fk_assessment_batch` (`assessment_import_batch_id`),
  KEY `student_age_change_log_fk_operator` (`changed_by`),
  KEY `student_age_change_log_fk_roster_batch` (`roster_import_batch_id`),
  KEY `ix_age_change_assessment_row` (`assessment_import_row_id`),
  KEY `ix_age_change_student_time` (`student_id`,`changed_at`),
  CONSTRAINT `student_age_change_log_fk_assessment_batch` FOREIGN KEY (`assessment_import_batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `student_age_change_log_fk_assessment_row` FOREIGN KEY (`assessment_import_row_id`) REFERENCES `assessment_import_row` (`id`),
  CONSTRAINT `student_age_change_log_fk_operator` FOREIGN KEY (`changed_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `student_age_change_log_fk_roster_batch` FOREIGN KEY (`roster_import_batch_id`) REFERENCES `student_roster_import_batch` (`id`),
  CONSTRAINT `student_age_change_log_fk_student` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `student_care_case`
--

DROP TABLE IF EXISTS `student_care_case`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `student_care_case` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` int NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'PENDING_REVIEW',
  `owner_id` int DEFAULT NULL,
  `opened_at` datetime NOT NULL DEFAULT (now()),
  `closed_at` datetime DEFAULT NULL,
  `close_reason` varchar(128) DEFAULT NULL,
  `close_note` text,
  `reopened_at` datetime DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `closed_by` int DEFAULT NULL,
  `reopened_by` int DEFAULT NULL,
  `reopen_reason` varchar(255) DEFAULT NULL,
  `last_reviewed_at` datetime DEFAULT NULL,
  `case_version` int NOT NULL DEFAULT '1',
  `active_student_id` int GENERATED ALWAYS AS ((case when (`status` <> _utf8mb4'CLOSED') then `student_id` else NULL end)) STORED,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_care_case_id_student` (`id`,`student_id`),
  UNIQUE KEY `uq_care_case_one_active_per_student` (`active_student_id`),
  KEY `owner_id` (`owner_id`),
  KEY `ix_student_care_case_student_id` (`student_id`),
  KEY `ix_care_case_status_owner_updated` (`status`,`owner_id`,`updated_at`),
  KEY `ix_care_case_active_student` (`active_student_id`),
  KEY `student_care_case_ibfk_4` (`reopened_by`),
  KEY `student_care_case_ibfk_3` (`closed_by`),
  CONSTRAINT `student_care_case_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `student_care_case_ibfk_2` FOREIGN KEY (`owner_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `student_care_case_ibfk_3` FOREIGN KEY (`closed_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `student_care_case_ibfk_4` FOREIGN KEY (`reopened_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=85 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `student_roster_import_batch`
--

DROP TABLE IF EXISTS `student_roster_import_batch`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `student_roster_import_batch` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `batch_no` varchar(64) NOT NULL,
  `school_id` int NOT NULL,
  `file_name` varchar(255) NOT NULL,
  `file_sha256` char(64) NOT NULL,
  `imported_by` int NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'PREVIEW',
  `total_rows` int NOT NULL DEFAULT '0',
  `created_rows` int NOT NULL DEFAULT '0',
  `updated_rows` int NOT NULL DEFAULT '0',
  `skipped_rows` int NOT NULL DEFAULT '0',
  `error_rows` int NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_roster_import_batch_no` (`batch_no`),
  KEY `ix_roster_import_operator` (`imported_by`),
  KEY `ix_roster_import_school_created` (`school_id`,`created_at`),
  CONSTRAINT `student_roster_import_batch_fk_operator` FOREIGN KEY (`imported_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `student_roster_import_batch_fk_school` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=51 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `student_roster_import_row`
--

DROP TABLE IF EXISTS `student_roster_import_row`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
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
) ENGINE=InnoDB AUTO_INCREMENT=125 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `system_setting`
--

DROP TABLE IF EXISTS `system_setting`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `system_setting` (
  `id` int NOT NULL AUTO_INCREMENT,
  `namespace` varchar(64) NOT NULL,
  `key` varchar(64) NOT NULL,
  `value_json` json NOT NULL,
  `updated_by` int DEFAULT NULL,
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_setting_namespace_key` (`namespace`,`key`),
  KEY `updated_by` (`updated_by`),
  CONSTRAINT `system_setting_ibfk_1` FOREIGN KEY (`updated_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=236 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `user_account`
--

DROP TABLE IF EXISTS `user_account`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_account` (
  `id` int NOT NULL AUTO_INCREMENT,
  `account` varchar(128) NOT NULL,
  `account_type` enum('STUDENT_NO','MOBILE','ADMIN_USERNAME') NOT NULL,
  `display_name` varchar(128) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `role_code` enum('STUDENT','COUNSELOR','LEADER','ADMIN') NOT NULL,
  `must_change_password` tinyint(1) NOT NULL DEFAULT '1',
  `failed_attempts` int NOT NULL DEFAULT '0',
  `locked_until` datetime DEFAULT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `last_login_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_user_account_type` (`account`,`account_type`)
) ENGINE=InnoDB AUTO_INCREMENT=244 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `user_scope`
--

DROP TABLE IF EXISTS `user_scope`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_scope` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `scope_type` enum('SCHOOL','GRADE','CLASS','STUDENT') NOT NULL,
  `school_id` int DEFAULT NULL,
  `grade_id` int DEFAULT NULL,
  `class_id` int DEFAULT NULL,
  `student_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `school_id` (`school_id`),
  KEY `grade_id` (`grade_id`),
  KEY `class_id` (`class_id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `user_scope_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `user_scope_ibfk_2` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `user_scope_ibfk_3` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`),
  CONSTRAINT `user_scope_ibfk_4` FOREIGN KEY (`class_id`) REFERENCES `class_group` (`id`),
  CONSTRAINT `user_scope_ibfk_5` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=244 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-09-21 17:14:04
