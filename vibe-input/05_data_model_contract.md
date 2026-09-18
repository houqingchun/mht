# 数据模型契约

## 1. 建模原则

- 使用MySQL 8.0。
- 所有业务表包含主键、创建时间、更新时间；需要软删除的表包含 `deleted_at`。
- 原始答卷和计算结果不可变，修改通过新版本或新事实记录完成。
- 重要结果保存 `rule_version` 和 `scale_version`。
- 敏感正文按权限读取，必要时加密或字段级脱敏。

## 2. 组织实体

### `school`

`id`, `code`, `name`, `status`, `created_at`, `updated_at`

### `grade`

`id`, `school_id`, `name`, `sort_order`, `status`, `created_at`, `updated_at`

### `class_group`

`id`, `school_id`, `grade_id`, `name`, `status`, `created_at`, `updated_at`

### `student`

`id`, `student_no`, `name`, `masked_name`, `school_id`, `grade_id`, `class_id`, `status`, `created_at`, `updated_at`

`student_no`在学校内唯一。学生表不保存测评分数和心理工作正文。

## 3. 账号实体

### `user_account`

`id`, `account`, `account_type`, `display_name`, `password_hash`, `role_code`, `must_change_password`, `failed_attempts`, `locked_until`, `active`, `last_login_at`, `created_at`, `updated_at`

账号类型：`STUDENT_NO`、`MOBILE`、`ADMIN_USERNAME`。

### `user_scope`

`id`, `user_id`, `scope_type`, `school_id`, `grade_id`, `class_id`, `student_id`

用于实现学校、年级、班级、学生四级数据权限。

## 4. 量表定义

### `assessment_scale`

`id`, `code`, `name`, `version`, `status`, `published_at`, `created_by`, `created_at`

### `scale_question`

`id`, `scale_id`, `question_no`, `question_text`, `dimension_code`, `is_validity_question`, `is_key_question`, `status`

### `scale_rule`

`id`, `scale_id`, `rule_version`, `rule_type`, `config_json`, `status`, `created_at`

题号、维度、效度题、重点题、总分和分级规则均从量表定义读取。

## 5. 测评运行

### `assessment_task`

`id`, `task_no`, `name`, `scale_id`, `school_id`, `scope_type`, `start_at`, `end_at`, `status`, `created_by`, `created_at`, `updated_at`

### `assessment_target`

`id`, `task_id`, `student_id`, `status`, `assigned_at`, `completed_at`

### `assessment_session`

`id`, `task_id`, `student_id`, `scale_id`, `scale_version`, `started_at`, `submitted_at`, `status`, `idempotency_key`, `created_at`, `updated_at`

状态：`NOT_STARTED`、`IN_PROGRESS`、`SUBMITTED`、`CALCULATED`、`QUESTIONABLE`。

### `assessment_answer`

`id`, `session_id`, `question_id`, `answer`, `score`, `answered_at`

唯一约束：`session_id + question_id`。

### `assessment_result`

`id`, `session_id`, `validity_score`, `validity_status`, `total_score`, `total_level`, `rule_version`, `calculated_at`

### `dimension_result`

`id`, `session_id`, `dimension_code`, `score`, `level`, `interpretation`, `rule_version`

唯一约束：`session_id + dimension_code`。

## 6. 学校关怀

### `risk_event`

`id`, `student_id`, `session_id`, `risk_type`, `risk_level`, `trigger_rule`, `status`, `created_at`, `reviewed_at`, `reviewed_by`

### `manual_review`

`id`, `risk_event_id`, `reviewer_id`, `review_result`, `confirmed_facts`, `next_action`, `next_follow_up_date`, `created_at`, `updated_at`

### `follow_up_record`

`id`, `student_id`, `operator_id`, `record_type`, `confirmed_facts`, `next_follow_up_date`, `status`, `created_at`, `updated_at`

### `family_contact_record`

`id`, `student_id`, `operator_id`, `contact_date`, `contact_person`, `channel`, `result`, `support_status`, `confirmed_facts`, `next_contact_date`, `created_at`, `updated_at`

### `retest_plan`

`id`, `student_id`, `source_session_id`, `planned_date`, `reason`, `status`, `created_by`, `completed_session_id`, `created_at`, `updated_at`

### `student_care_case`

`id`, `student_id`, `status`, `owner_id`, `opened_at`, `closed_at`, `close_reason`, `close_note`, `reopened_at`, `updated_at`

## 7. 审计与导出

### `audit_log`

`id`, `actor_user_id`, `actor_role`, `action`, `resource_type`, `resource_id`, `purpose`, `ip`, `user_agent`, `result`, `created_at`

### `export_job`

`id`, `requested_by`, `purpose`, `scope_json`, `fields_json`, `mask_level`, `status`, `file_uri`, `created_at`, `expired_at`

## 8. 关键关系

```text
School → Grade → ClassGroup → Student
Scale → Question / Rule
Task → Target → Session → Answer / Result / DimensionResult
Session → RiskEvent → ManualReview
Student → CareCase → FollowUp / FamilyContact / RetestPlan
UserAccount → UserScope → 审计/导出/业务操作
```
