-- MHT 总分恢复为 90 道内容题，并重算全部可重算历史结果。
-- 适用前提：当前 alembic_version = 0019_total_includes_validity，MySQL 8.0。
-- 执行前请先做整库备份。本脚本不改表结构。
--
-- 安全策略：
--   1. 任何前置条件不满足就 SIGNAL 中止，不写业务数据。
--   2. 只有外部汇总分、没有 100 道答案的数据无法反推效度题分；
--      发现这类数据时中止，不猜测。
--   3. 所有业务写入在一个事务中；失败由 EXIT HANDLER 回滚。

DELIMITER $$

DROP PROCEDURE IF EXISTS xlp_apply_total_excludes_validity$$
CREATE PROCEDURE xlp_apply_total_excludes_validity()
BEGIN
    DECLARE v_count BIGINT DEFAULT 0;
    DECLARE v_version VARCHAR(64);

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    SELECT version_num INTO v_version FROM alembic_version LIMIT 1;
    IF v_version <> '0019_total_includes_validity' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：alembic_version 必须是 0019_total_includes_validity';
    END IF;

    SELECT COUNT(*) INTO v_count
    FROM scale_rule r
    JOIN assessment_scale s ON s.id = r.scale_id
    WHERE s.code = 'MHT' AND r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE';
    IF v_count <> 1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：MHT 必须恰好有一条 ACTIVE 评分规则';
    END IF;

    SELECT COUNT(*) INTO v_count
    FROM scale_rule r
    JOIN assessment_scale s ON s.id = r.scale_id
    WHERE s.code = 'MHT' AND r.rule_version = 'MHT-RULE-1.1.2';
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：MHT-RULE-1.1.2 已存在，请先核对是否执行过本脚本';
    END IF;

    SELECT COUNT(*) INTO v_count
    FROM assessment_external_result
    WHERE source_type = 'EXTERNAL_SUMMARY' AND total_score IS NOT NULL
      AND validity_score IS NULL;
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：存在无原始答案的 EXTERNAL_SUMMARY，无法准确重算总分';
    END IF;

    SELECT COUNT(*) INTO v_count
    FROM assessment_result ar
    JOIN assessment_session ses ON ses.id = ar.session_id
    JOIN assessment_scale s ON s.id = ses.scale_id AND s.code = 'MHT'
    LEFT JOIN (
        SELECT aa.session_id, COUNT(DISTINCT aa.question_id) AS answer_count
        FROM assessment_answer aa
        GROUP BY aa.session_id
    ) a ON a.session_id = ar.session_id
    WHERE COALESCE(a.answer_count, 0) <> 100;
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '中止：有 assessment_result 没有完整的 100 道原始答案';
    END IF;

    CREATE TEMPORARY TABLE xlp_rule_exclude_validity AS
    SELECT r.id AS old_id,
           r.scale_id,
           'MHT-RULE-1.1.2' AS new_version,
           JSON_SET(
               r.config_json,
               CONCAT('$.total_levels[', JSON_LENGTH(r.config_json, '$.total_levels') - 1, '].max'),
               90
           ) AS new_config
    FROM scale_rule r
    JOIN assessment_scale s ON s.id = r.scale_id
    WHERE s.code = 'MHT' AND r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE';

    START TRANSACTION;

    INSERT INTO scale_rule (scale_id, rule_version, rule_type, config_json, status)
    SELECT scale_id, new_version, 'MHT_SCORING', new_config, 'ACTIVE'
    FROM xlp_rule_exclude_validity;

    UPDATE scale_rule
    SET status = 'RETIRED'
    WHERE id IN (SELECT old_id FROM xlp_rule_exclude_validity);

    UPDATE assessment_result ar
    JOIN assessment_session ses ON ses.id = ar.session_id
    JOIN xlp_rule_exclude_validity b ON b.scale_id = ses.scale_id
    JOIN (
        SELECT aa.session_id,
               SUM(CASE WHEN q.is_validity_question = 0 THEN aa.score ELSE 0 END) AS new_total
        FROM assessment_answer aa
        JOIN scale_question q ON q.id = aa.question_id
        GROUP BY aa.session_id
    ) scored ON scored.session_id = ar.session_id
    SET ar.total_score = scored.new_total,
        ar.total_level = CASE
            WHEN scored.new_total <= 55 THEN 'GENERAL_RANGE'
            WHEN scored.new_total <= 64 THEN 'NEEDS_ATTENTION'
            ELSE 'KEY_ATTENTION'
        END,
        ar.rule_version = b.new_version;

    UPDATE dimension_result dr
    JOIN assessment_session ses ON ses.id = dr.session_id
    JOIN xlp_rule_exclude_validity b ON b.scale_id = ses.scale_id
    SET dr.rule_version = b.new_version;

    UPDATE risk_event re
    JOIN assessment_session ses ON ses.id = re.session_id
    JOIN xlp_rule_exclude_validity b ON b.scale_id = ses.scale_id
    SET re.rule_version = b.new_version;

    UPDATE assessment_external_result
    SET total_score = GREATEST(0, total_score - validity_score),
        rule_version = 'MHT-RULE-1.1.2'
    WHERE source_type = 'EXTERNAL_SUMMARY'
      AND total_score IS NOT NULL AND validity_score IS NOT NULL;

    UPDATE alembic_version
    SET version_num = '0020_total_excludes_validity'
    WHERE version_num = '0019_total_includes_validity';

    COMMIT;
    DROP TEMPORARY TABLE xlp_rule_exclude_validity;

    SELECT
        (SELECT version_num FROM alembic_version LIMIT 1) AS alembic_version,
        (SELECT COUNT(*) FROM assessment_result WHERE rule_version = 'MHT-RULE-1.1.2') AS updated_results,
        (SELECT MIN(total_score) FROM assessment_result) AS min_total_score,
        (SELECT MAX(total_score) FROM assessment_result) AS max_total_score;
END$$

CALL xlp_apply_total_excludes_validity()$$
DROP PROCEDURE xlp_apply_total_excludes_validity$$

DELIMITER ;
