-- ============================================================================
-- 心晴：把库清成「只有管理员账号 + 基本配置」的基线状态
-- ============================================================================
--
-- 用途：在一台**新环境**上，把 `make seed` / `make seed-demo` 填进去的一切删干净。
-- 剩下的只有：
--
--     school            一所学校（admin 的 scope 指着它，导入链路也按 code='QH' 取它）
--     system_setting    操作员配的机构标识
--     role_permission   权限矩阵
--     assessment_scale  已发布的量表版本（+ scale_question + scale_rule）
--     user_account      只有名为 `admin` 的那一行，以及它的 user_scope 行
--     alembic_version   迁移版本戳
--
-- `system_setting` 与 `role_permission` 是**原样保留，不在「恢复成什么」之列**：
-- 全新环境上这两张表本来就是 0 行（配置回退 `settings_service.DEFAULTS`、权限回退
-- `CAPABILITY_DEFAULTS`，§4 / §5 的 fail-safe），`seed.py` 也不写它们——一个库上它们
-- 有几行，取决于那个库的操作员配过什么、管理员保存过几次权限矩阵。脚本一行都不碰。
--
-- ---------------------------------------------------------------------------
-- 三条约定，改这个文件之前先读
-- ---------------------------------------------------------------------------
--
-- 1. **只删行，不建表、不建行。** schema 归 `alembic upgrade head`，基线行归
--    `python -m app.db.seed`（`make migrate && make seed`）。这里不抄那三样东西：
--    admin 的密码哈希、100 道题的题干、评分规则的 JSON——它们在仓库里各有**唯一**
--    出处（`app/db/seed.py` 的哈希函数、`data/mht_scale.json`、`DEFAULT_RULE_CONFIG`）。
--    在 SQL 里再抄一份必然漂移，而一份抄错的规则 JSON 会让这个库的评分与别处不一样，
--    且看不出来（CLAUDE.md §6：阈值随规则版本走）。
--
-- 2. **每一条语句都挂在 `@admin_id` 上。** 找不到 admin 时，`WHERE @admin_id IS NOT NULL`
--    恒假、`id <> NULL` 不是真，于是**一条都不删**。最坏的失败不是「少删了」——
--    那是再跑一次的事；最坏的是「删完没有人能登录」，那要找回来这个库。
--
-- 3. **顺序是子先父后，不是排版。** 全库没有一个 `ondelete=`，每个外键都是 RESTRICT，
--    父行先删在 MySQL 上是必然的 1451。这个顺序与 `app/db/purge.py` 的
--    `ASSESSMENT_TABLES` 同源，改动请两边一起改——`app/tests/test_sql_reset_to_baseline.py`
--    会按外键图逐条验它。
--
-- ---------------------------------------------------------------------------
-- 怎么跑
-- ---------------------------------------------------------------------------
--
--     mysql -h 127.0.0.1 -u root -p 你的库名 < reset_to_baseline.sql
--
-- 整个过程是一个事务：中间任何一条报错，客户端就会停下并断开，事务随之回滚，
-- **不要**加 `--force`（它会跳过错误继续跑，那正是这里最不能要的行为）。
-- 脚本可重复执行：跑第二遍什么都不会删。
--
-- 建议先看一眼自己要删什么（只读，不改库）：
--     mysql ... -e "select 'student', count(*) from student union all
--                    select 'user_account', count(*) from user_account union all
--                    select 'assessment_session', count(*) from assessment_session"
-- ----------------------------------------------------------------------------

SET @admin_id := (SELECT id FROM user_account WHERE account = 'admin' ORDER BY id LIMIT 1);

-- 锚点先报出来。NULL 就是「没找到 admin，下面一条都不会删」。
SELECT IF(@admin_id IS NULL,
          '!! 找不到名为 admin 的账号：脚本不会删除任何东西。请先 make seed',
          CONCAT('保留的 admin user_account.id = ', @admin_id)) AS anchor;

START TRANSACTION;

-- ---------------------------------------------------------------------------
-- 一、测评 / 关怀的事实
-- ---------------------------------------------------------------------------
-- 顺序与 purge.py 的 ASSESSMENT_TABLES 逐条对齐：
-- manual_review 必须在 risk_event 之前（外键 NOT NULL），
-- risk_event / 关怀三者 / audit_log 必须在 student 与 user_account 之前，
-- assessment_answer 必须在 scale_question 之前（全库唯一指向题目的那张表）。

DELETE FROM manual_review          WHERE @admin_id IS NOT NULL;
DELETE FROM risk_event             WHERE @admin_id IS NOT NULL;
DELETE FROM follow_up_record       WHERE @admin_id IS NOT NULL;
DELETE FROM family_contact_record  WHERE @admin_id IS NOT NULL;
DELETE FROM retest_plan            WHERE @admin_id IS NOT NULL;
DELETE FROM student_care_case      WHERE @admin_id IS NOT NULL;
DELETE FROM dimension_result       WHERE @admin_id IS NOT NULL;
DELETE FROM assessment_result      WHERE @admin_id IS NOT NULL;
DELETE FROM assessment_answer      WHERE @admin_id IS NOT NULL;
DELETE FROM assessment_session     WHERE @admin_id IS NOT NULL;
DELETE FROM assessment_target      WHERE @admin_id IS NOT NULL;
DELETE FROM assessment_task        WHERE @admin_id IS NOT NULL;

-- 审计行整张清掉：它记的是上面那些行「谁在什么时候动过」，
-- 被记的对象都没了，留着只会让轨迹指着不存在的东西。
DELETE FROM audit_log              WHERE @admin_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 二、账号与范围
-- ---------------------------------------------------------------------------
-- 先摘掉三处「指向某个员工的出处列」，否则删账号时会被 RESTRICT 挡下（1451）。
-- 三列都可空。置 NULL 是实情——做那次改动的人已经不在这个库里了——
-- 重新指到 admin 上会是一句谎话。
-- 这是唯一会改动**保留表内容**的地方，改的是出处，不是配置本身。

UPDATE assessment_scale SET created_by = NULL
 WHERE @admin_id IS NOT NULL AND created_by IS NOT NULL AND created_by <> @admin_id;
UPDATE role_permission  SET updated_by = NULL
 WHERE @admin_id IS NOT NULL AND updated_by IS NOT NULL AND updated_by <> @admin_id;
UPDATE system_setting   SET updated_by = NULL
 WHERE @admin_id IS NOT NULL AND updated_by IS NOT NULL AND updated_by <> @admin_id;

-- user_scope 是叶子表（没有任何东西引用它），指向账号 / 学校 / 年级 / 班级 / 学生，
-- 所以必须排在 student 与 grade / class_group 之前。
DELETE FROM user_scope   WHERE @admin_id IS NOT NULL AND user_id <> @admin_id;
DELETE FROM user_account WHERE @admin_id IS NOT NULL AND id      <> @admin_id;

-- ---------------------------------------------------------------------------
-- 三、名册
-- ---------------------------------------------------------------------------
DELETE FROM student     WHERE @admin_id IS NOT NULL;
DELETE FROM class_group WHERE @admin_id IS NOT NULL;
DELETE FROM grade       WHERE @admin_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 四、量表：丢掉「非已发布」的版本，和已发布版本上退休的规则残留
-- ---------------------------------------------------------------------------
-- `assessment_scale` 的 reachability 判据（没有任务 / 会话 / 结果指着它）在
-- 这个脚本里**已经为真**——上面刚把任务、会话、结果全删了。所以判据只剩「不是已发布」。
-- 已发布的那一个永远不动：下一个建任务的人会选它。

DELETE FROM scale_question WHERE @admin_id IS NOT NULL
  AND scale_id IN (SELECT id FROM assessment_scale WHERE status <> 'PUBLISHED');
DELETE FROM scale_rule     WHERE @admin_id IS NOT NULL
  AND scale_id IN (SELECT id FROM assessment_scale WHERE status <> 'PUBLISHED');
DELETE FROM assessment_scale WHERE @admin_id IS NOT NULL AND status <> 'PUBLISHED';

-- RETIRED 的规则行是「阈值改过一次」留下的残渣。它存在的唯一理由是让**已存下来的结果**
-- 仍然找得到当初的判据（§6）；结果刚被删光，所以它已经没有指代对象。
-- ACTIVE 的那一条一律不动——包括它已经被改过（config 不等于出厂阈值）的情况：
-- 判「有没有真改过」要拿默认 JSON 在 SQL 里再写一份，那正是第 1 条约定要避免的。
DELETE FROM scale_rule WHERE @admin_id IS NOT NULL AND status = 'RETIRED';

COMMIT;

-- ---------------------------------------------------------------------------
-- 五、跑完看一眼
-- ---------------------------------------------------------------------------
SELECT 'user_account'       AS 表, COUNT(*) AS 行数 FROM user_account
UNION ALL SELECT 'user_scope',      COUNT(*) FROM user_scope
UNION ALL SELECT 'student',         COUNT(*) FROM student
UNION ALL SELECT 'grade',           COUNT(*) FROM grade
UNION ALL SELECT 'class_group',     COUNT(*) FROM class_group
UNION ALL SELECT 'assessment_task', COUNT(*) FROM assessment_task
UNION ALL SELECT 'assessment_session', COUNT(*) FROM assessment_session
UNION ALL SELECT 'assessment_result',  COUNT(*) FROM assessment_result
UNION ALL SELECT 'student_care_case',  COUNT(*) FROM student_care_case
UNION ALL SELECT 'audit_log',       COUNT(*) FROM audit_log
UNION ALL SELECT 'school',          COUNT(*) FROM school
UNION ALL SELECT 'system_setting',  COUNT(*) FROM system_setting
UNION ALL SELECT 'role_permission', COUNT(*) FROM role_permission
UNION ALL SELECT 'assessment_scale',COUNT(*) FROM assessment_scale
UNION ALL SELECT 'scale_question',  COUNT(*) FROM scale_question
UNION ALL SELECT 'scale_rule',      COUNT(*) FROM scale_rule
UNION ALL SELECT 'alembic_version', COUNT(*) FROM alembic_version;
