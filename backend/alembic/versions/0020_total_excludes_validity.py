"""总分口径恢复为只统计 90 道非效度题，并重算历史结果。

迁移不改表结构。它发布新规则，再从 `assessment_answer` 精确重算每份已落库
答卷的总分与等级，并将结果、维度结果和风险事件统一标记为新规则版本。

Revision ID: 0020_total_excludes_validity
Revises: 0019_total_includes_validity
"""

from alembic import context, op
import sqlalchemy as sa

revision = "0020_total_excludes_validity"
down_revision = "0019_total_includes_validity"
branch_labels = None
depends_on = None

PRECHECKS = (
    (
        "存在只有外部汇总分、没有原始答案的记录，无法准确扣除效度题分",
        "SELECT id FROM assessment_external_result "
        "WHERE source_type = 'EXTERNAL_SUMMARY' AND total_score IS NOT NULL "
        "AND validity_score IS NULL LIMIT 5",
    ),
    (
        # 判据是「会被不准确重算」，不是「答案不齐」——两者差一档，差的正是 0 答案那一档。
        # `RECALCULATE_RESULTS` 内连接 `GROUP BY assessment_answer` 的子查询，所以它只碰
        # **有答案**的会话：0 答案的结果行它压根不重算（那一行会保留原规则版本）。因此
        # 「无法准确重算」精确等于 `1 <= n <> 100`，而 `n = 0` 是**误报**。
        #
        # 那个误报会让迁移在一台正常的库上中止：`POST /assessment-sessions/{id}/reset`
        # 删答案但**刻意保留 result 行**（好让下次提交走 `submit_session` 的幂等早返回，
        # 见 `api/v1/assessment.py` 的注释），于是「会话 IN_PROGRESS、0 答案、有一份
        # 旧结果」是这套系统正常产生的状态，任何跑过学生答题 e2e 的库都有一行。
        # 它描述的那场作答已经不存在了——没有答案可算，也就没有「算得准不准」。
        "存在没有完整 100 道原始答案的 MHT 结果，无法准确重算",
        "SELECT ar.id FROM assessment_result ar "
        "JOIN assessment_session ses ON ses.id = ar.session_id "
        "JOIN assessment_scale s ON s.id = ses.scale_id AND s.code = 'MHT' "
        "LEFT JOIN (SELECT session_id, COUNT(DISTINCT question_id) AS n "
        "           FROM assessment_answer GROUP BY session_id) a ON a.session_id = ar.session_id "
        "WHERE COALESCE(a.n, 0) NOT IN (0, 100) LIMIT 5",
    ),
)


def _precheck() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    for what, sql in PRECHECKS:
        rows = bind.execute(sa.text(sql)).fetchall()
        if rows:
            raise RuntimeError(f"[{revision}] 中止：{what}。前 5 行：{rows}")

SNAPSHOT = (
    "CREATE TEMPORARY TABLE xlp_rule_exclude_validity AS "
    "SELECT r.id AS old_id, r.scale_id, "
    "       CASE "
    "         WHEN LOCATE('.', r.rule_version) > 1 "
    "          AND SUBSTRING_INDEX(r.rule_version, '.', -1) REGEXP '^[0-9]+$' "
    "         THEN CONCAT("
    "                LEFT(r.rule_version, CHAR_LENGTH(r.rule_version) "
    "                     - CHAR_LENGTH(SUBSTRING_INDEX(r.rule_version, '.', -1))), "
    "                CAST(SUBSTRING_INDEX(r.rule_version, '.', -1) AS UNSIGNED) + 1) "
    "         ELSE CONCAT(r.rule_version, '-2') "
    "       END AS new_version, "
    "       CASE "
    "         WHEN JSON_LENGTH(r.config_json, '$.total_levels') IS NULL "
    "           OR JSON_LENGTH(r.config_json, '$.total_levels') = 0 "
    "         THEN r.config_json "
    "         ELSE JSON_SET("
    "                r.config_json, "
    "                CONCAT('$.total_levels[', "
    "                       JSON_LENGTH(r.config_json, '$.total_levels') - 1, '].max'), "
    "                90) "
    "       END AS new_config "
    "FROM scale_rule AS r "
    "WHERE r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE'"
)

SKIP_TAKEN = (
    "DELETE b FROM xlp_rule_exclude_validity AS b "
    "WHERE EXISTS (SELECT 1 FROM scale_rule AS s "
    "              WHERE s.scale_id = b.scale_id AND s.rule_version = b.new_version)"
)

INSERT_NEW = (
    "INSERT INTO scale_rule (scale_id, rule_version, rule_type, config_json, status) "
    "SELECT scale_id, new_version, 'MHT_SCORING', new_config, 'ACTIVE' "
    "FROM xlp_rule_exclude_validity"
)

RETIRE_OLD = (
    "UPDATE scale_rule SET status = 'RETIRED' "
    "WHERE id IN (SELECT old_id FROM xlp_rule_exclude_validity)"
)

RECALCULATE_RESULTS = (
    "UPDATE assessment_result AS ar "
    "JOIN assessment_session AS ses ON ses.id = ar.session_id "
    "JOIN xlp_rule_exclude_validity AS b ON b.scale_id = ses.scale_id "
    "JOIN ("
    "  SELECT aa.session_id, "
    "         SUM(CASE WHEN q.is_validity_question = 0 THEN aa.score ELSE 0 END) AS new_total "
    "  FROM assessment_answer AS aa "
    "  JOIN scale_question AS q ON q.id = aa.question_id "
    "  GROUP BY aa.session_id"
    ") AS scored ON scored.session_id = ar.session_id "
    "SET ar.total_score = scored.new_total, "
    "    ar.total_level = CASE "
    "      WHEN scored.new_total <= 55 THEN 'GENERAL_RANGE' "
    "      WHEN scored.new_total <= 64 THEN 'NEEDS_ATTENTION' "
    "      ELSE 'KEY_ATTENTION' END, "
    "    ar.rule_version = b.new_version"
)

UPDATE_DIMENSION_RULE = (
    "UPDATE dimension_result AS dr "
    "JOIN assessment_session AS ses ON ses.id = dr.session_id "
    "JOIN xlp_rule_exclude_validity AS b ON b.scale_id = ses.scale_id "
    "SET dr.rule_version = b.new_version"
)

UPDATE_RISK_RULE = (
    "UPDATE risk_event AS re "
    "JOIN assessment_session AS ses ON ses.id = re.session_id "
    "JOIN xlp_rule_exclude_validity AS b ON b.scale_id = ses.scale_id "
    "SET re.rule_version = b.new_version"
)

UPDATE_EXTERNAL_SUMMARIES = (
    "UPDATE assessment_external_result AS er "
    "JOIN assessment_scale AS s ON s.code = er.scale_code "
    "JOIN xlp_rule_exclude_validity AS b ON b.scale_id = s.id "
    "SET er.total_score = GREATEST(0, er.total_score - er.validity_score), "
    "    er.rule_version = b.new_version "
    "WHERE er.source_type = 'EXTERNAL_SUMMARY' "
    "  AND er.total_score IS NOT NULL AND er.validity_score IS NOT NULL"
)

DROP_SNAPSHOT = "DROP TEMPORARY TABLE xlp_rule_exclude_validity"

SWAP_SNAPSHOT = (
    "CREATE TEMPORARY TABLE xlp_rule_exclude_swap AS "
    "SELECT r.id AS new_id, "
    "       (SELECT MAX(s.id) FROM scale_rule AS s "
    "         WHERE s.scale_id = r.scale_id AND s.rule_type = r.rule_type "
    "           AND s.status = 'RETIRED') AS prev_id "
    "FROM scale_rule AS r "
    "WHERE r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE'"
)

RETIRE_NEW = (
    "UPDATE scale_rule SET status = 'RETIRED' "
    "WHERE id IN (SELECT new_id FROM xlp_rule_exclude_swap WHERE prev_id IS NOT NULL)"
)

RESTORE_OLD = (
    "UPDATE scale_rule SET status = 'ACTIVE' "
    "WHERE id IN (SELECT prev_id FROM xlp_rule_exclude_swap WHERE prev_id IS NOT NULL)"
)

DROP_SWAP = "DROP TEMPORARY TABLE xlp_rule_exclude_swap"

RESTORE_RESULTS = (
    "UPDATE assessment_result AS ar "
    "JOIN assessment_session AS ses ON ses.id = ar.session_id "
    "JOIN xlp_rule_exclude_swap AS x ON x.new_id = ("
    "  SELECT r.id FROM scale_rule r WHERE r.scale_id = ses.scale_id "
    "  AND r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE' ORDER BY r.id DESC LIMIT 1"
    ") "
    "JOIN scale_rule AS prev ON prev.id = x.prev_id "
    "SET ar.total_score = ar.total_score + ar.validity_score, "
    "    ar.total_level = CASE "
    "      WHEN ar.total_score + ar.validity_score <= 55 THEN 'GENERAL_RANGE' "
    "      WHEN ar.total_score + ar.validity_score <= 64 THEN 'NEEDS_ATTENTION' "
    "      ELSE 'KEY_ATTENTION' END, "
    "    ar.rule_version = prev.rule_version "
    "WHERE x.prev_id IS NOT NULL"
)

RESTORE_DIMENSION_RULE = (
    "UPDATE dimension_result AS dr "
    "JOIN assessment_session AS ses ON ses.id = dr.session_id "
    "JOIN scale_rule AS current ON current.scale_id = ses.scale_id "
    "  AND current.rule_type = 'MHT_SCORING' AND current.status = 'ACTIVE' "
    "JOIN xlp_rule_exclude_swap AS x ON x.new_id = current.id "
    "JOIN scale_rule AS prev ON prev.id = x.prev_id "
    "SET dr.rule_version = prev.rule_version WHERE x.prev_id IS NOT NULL"
)

RESTORE_RISK_RULE = (
    "UPDATE risk_event AS re "
    "JOIN assessment_session AS ses ON ses.id = re.session_id "
    "JOIN scale_rule AS current ON current.scale_id = ses.scale_id "
    "  AND current.rule_type = 'MHT_SCORING' AND current.status = 'ACTIVE' "
    "JOIN xlp_rule_exclude_swap AS x ON x.new_id = current.id "
    "JOIN scale_rule AS prev ON prev.id = x.prev_id "
    "SET re.rule_version = prev.rule_version WHERE x.prev_id IS NOT NULL"
)


def upgrade() -> None:
    _precheck()
    op.execute(SNAPSHOT)
    op.execute(SKIP_TAKEN)
    op.execute(INSERT_NEW)
    op.execute(RETIRE_OLD)
    op.execute(RECALCULATE_RESULTS)
    op.execute(UPDATE_DIMENSION_RULE)
    op.execute(UPDATE_RISK_RULE)
    op.execute(UPDATE_EXTERNAL_SUMMARIES)
    op.execute(DROP_SNAPSHOT)


def downgrade() -> None:
    op.execute(SWAP_SNAPSHOT)
    op.execute(RESTORE_RESULTS)
    op.execute(RESTORE_DIMENSION_RULE)
    op.execute(RESTORE_RISK_RULE)
    op.execute(RETIRE_NEW)
    op.execute(RESTORE_OLD)
    op.execute(DROP_SWAP)
