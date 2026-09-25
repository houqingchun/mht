-- =============================================================================
-- 心晴 · 中学生心理测评与关怀平台 —— 数据库结构（MySQL 8）
-- =============================================================================
--
-- 与 alembic 的关系
-- -----------------
-- **这份文件不是 schema 的来源，`alembic upgrade head` 才是。**
-- 它是那份 schema 的一张快照，给两种人用：
--
--   1. 装了 `schema_prepared` 分工的安装（`install.ps1` 第 1 步选 3）——
--      操作员自己建库建表，安装器只做 `ensure_schema` 校对 + 迁移；
--   2. 需要把结构过一遍 DBA / 走一次评审的人。
--
-- 建出来的库与 `make migrate` 建出来的库**逐列、逐索引、逐外键、逐约束名相同**，
-- 所以两者可以互换、可以互相接着跑迁移。这条等价性**是在真 MySQL 8 上逐表
-- `SHOW CREATE TABLE` 比对过的**，不是推的；守卫在
-- `app/tests/test_sql_schema_matches_models.py`。
--
--
-- 三条不能改的约定
-- ----------------
-- **① 约束名照抄 MySQL 自动生成的 `<表>_ibfk_<N>`，不要改成有意义的名字。**
--    读起来更顺的代价是与真库**不一致**：迁移 `0012` 已经在按名字
--    `op.drop_constraint("uq_care_case_student_status", …)`，将来任何一条
--    `op.drop_constraint("grade_ibfk_1", …)` 都会在这个文件建的库上失败，
--    而失败得看不出来（那条迁移在这一版里是绿的，因为 schema_prepared 的库
--    此刻已经是这个文件建的、只是还没跑过后续迁移）。
--    **文件里出现的约束名，与 `SHOW CREATE TABLE` 的输出逐字相同。**
--
-- **② 只建不删：没有 `DROP TABLE IF EXISTS`。** 手滑跑了第二次必须**当场报错**，
--    而不是先删掉再看。这是一个学校正在用的库，不是开发机的草稿。
--    真要重建，先 `mysqldump` 备份，再自己 `DROP DATABASE`。
--
-- **③ 建表次序是父先子后，所以不需要 `SET FOREIGN_KEY_CHECKS=0`。**
--    关掉外键检查会让次序错误变得不可发现（脚本照样跑完，表照样建出来），
--    而次序错了本该在这一步就停住。
--
--    **这个文件是在 `FOREIGN_KEY_CHECKS=1` 下整份跑通过的。** 第一版把次序排错了
--    （`risk_event` 排在 `assessment_session` 前面），MySQL 报
--    `1824 Failed to open the referenced table 'assessment_session'`，
--    而**没有任何文本检查看得见它** —— 当时测试跑在内存 sqlite 上、不检查外键
--    （CLAUDE.md 缺口 3 已关：2026-09-19 起测试在真 MySQL 上由迁移建表），
--    仓库里那套测试全绿。所以「在真库上开着外键检查整份跑一遍」不是一道工序，
--    是这个文件唯一的正确性证据。守卫按 `Base.metadata` 的外键图把次序重推一遍。
--
-- **④ 七条外键排在所有 `CREATE TABLE` 之后，是 `ALTER TABLE … ADD CONSTRAINT`。**
--    V1.2 的外键图里有一个**环**（`assessment_import_row` ↔
--    `assessment_external_result` 互相指着），环线性化不了；另有五条是从 V1.0 的
--    老表（`assessment_target` / `assessment_session`）指向新的导入批次表，
--    而老表排在文件前面。所以「所有外键都写在表体里」这个格式对这七条做不到。
--    它们集中放在全部建表之后，那一段的每一行都是 `ALTER TABLE … ADD CONSTRAINT`。
--    「父先子后」这条判据对那一段**豁免**（ALTER 排在最后，父表必然已存在）——
--    `test_tables_are_created_parent_before_child` 里记着这件事。
--    **那一段不能省**：少一条外键不会有任何症状，直到某天删父行时它没有拦住。
--
-- 这份快照对应 **V1.2**（alembic `0014_v12_enforce`）：
--   34 张业务表 / 438 列 / 271 条索引记录 / 120 个外键
--   （都不含 Alembic 自己的 `alembic_version` —— 那张不由这里建）。
--   `0015_calc_status_backfill` 只写数据（把已有结果的场次标成 `CALCULATED`），
--   所以结构仍然等同于这份快照；由这份文件建出来的库是**空**库，没有可回填的行。
--
--
-- 用法
-- ----
-- ```bash
-- # 库要先存在，字符集必须是 utf8mb4（见下面的 CREATE DATABASE 说明）
-- mysql -h HOST -u USER -p DB < schema_mysql8.sql
-- ```
--
-- 建完**必须还要跑一次迁移**（`ensure_schema` 会补 `alembic_version` 那一行，
-- 或者由 `alembic upgrade head` 自己记账）。少了那一步，库是"结构对但记不住
-- 自己是什么版本"，下一次安装器会判成「有表、没有 alembic_version」。
--
--
-- MySQL 版本下限：**8.0.13**
-- --------------------------
-- `DEFAULT (now())` 这个**带括号**的形式是表达式默认值语法，8.0.13 才支持。真库是 8.4.4，
-- SQLAlchemy 的 `server_default=func.now()` 落下来就是这个形状，所以这里照抄。
-- 8.0.12 及更早、或 5.7 上要换成 `DEFAULT CURRENT_TIMESTAMP`（语义等价）。
-- `utf8mb4_0900_ai_ci` 是 MySQL 8 专有排序规则，5.7 与 MariaDB 都没有。
--
-- 注意 `updated_at` 那几列**没有** `ON UPDATE`：`onupdate=func.now()` 是
-- SQLAlchemy **客户端**实现的（它在 UPDATE 语句里显式写一列），不是数据库行为。
-- 手工往里插数据的人要知道这一点。
--
-- =============================================================================

-- 库本身不由这份脚本创建（那是安装器 / `app.db.create_database` 的事）。
-- 手工建库时字符集要与下面每张表的表级声明一致，否则将来手工加的列会退回库默认值
-- （表级声明的优先级高于库级，所以这个文件本身不受影响）。
--
--   CREATE DATABASE `xinliceping`
--     DEFAULT CHARACTER SET utf8mb4
--     DEFAULT COLLATE utf8mb4_0900_ai_ci;

SET NAMES utf8mb4;


-- =============================================================================
-- 一、身份与组织
-- =============================================================================
--
-- **这一节里的三张表各多了一条看着多余的唯一键**（V1.2 加的）：
--   `grade.uq_grade_school_id (school_id, id)`
--   `class_group.uq_class_school_id (school_id, id)`
--   `student.uq_student_school_id (school_id, id)`
-- `id` 本来就是主键、本来就唯一，所以「(school_id, id) 唯一」这话是白说的 ——
-- 它的用途**只有一个：当复合外键的目标**。MySQL 要求外键指向的列组上有索引
-- （唯一索引或普通索引都行），而 `student.ibfk_4 (school_id, grade_id)
-- → grade (school_id, id)` 这类边要保证的是**"这个年级属于这所学校"**，
-- 不是"这个 id 存在"。少了它，跨校的数据能连进来，而**看不到任何症状**。
--
-- 代价是索引多占一点空间；换来的是"学校"这一维在**每一条跨表引用上**都成立。

-- 学校。全库的根，`user_scope` 的 SCHOOL 级、`assessment_task`、`student` 都指它。
-- 当前实现是单校写死的（学生导入链路硬编码 `code = 'QH'`），但 schema 已经为多校留了余量：
-- 学号唯一键是 `(school_id, student_no)` 而不是 `(student_no)`。
CREATE TABLE `school` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(64) NOT NULL,
  `name` varchar(128) NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'ACTIVE',
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 年级。`sort_order` 决定下拉框与列表里的先后（初一在初二前面），
-- 与 `name` 的字典序无关 —— 中文年级名按字典序排出来是乱的。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 学生名册。
--
-- `masked_name` **不是隐私控制**：三条写入路径（seed / seed_demo / 学生导入）写的都是真名，
-- 这一列在生产里和 `name` 一样实。它的用途只是花名册与关怀队列的**展示名**。
-- 导出遮蔽由 `export_service.mask_student_name` 在读取时**现算**（姓 + 「同学」），
-- 现算永远是对的，回填列会再次漂移。
--
-- `age` 是**存下来的整数**，不是现算的（迁移 0011 把 `birth_date` 换成了它）。
-- 学校手上的名册只有年龄，现算没有输入可算。代价是知情的：**这一列不会自己变**，
-- 不重导名册，界面上的年龄就停在去年。写入方有两个：学生信息导入，以及
-- MHT 测评记录导入在年龄冲突选了「覆盖」时的 `_update_roster_age`。
--
-- `gender` 只被测评导入用来**消歧**（同名同班时），从不写回。它是 varchar 不是 enum：
-- 两条导入链路各有各的约定（学生导入认 `MALE`/`FEMALE`/`男`/`女`，测评导入认 `2`/`1`）。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 账号。**全库的第二个根**，`user_scope`、`role_permission`、四张专业工作表、
-- `audit_log` 都指它，所以它**没有删除接口**：删一行会让轨迹与工作记录全部悬空
-- （库里没有任何 `ondelete=`，每个外键都是 RESTRICT）。停用是唯一的退役方式，
-- 而停用立即生效 —— `get_current_user` **每一个请求**都查一次 `active`。
--
-- 唯一键是 `(account, account_type)` 而不是 `(account)`：同一个字符串（手机号）
-- 可以既是心理老师又是德育领导，两条账号行各自独立。`authenticate` 是拿
-- `account + account_type + role_code` **三个一起**匹配的。
--
-- `role_code` 与 `account_type` 是**全库仅有的两个 enum 列**，其余状态类列都是
-- varchar(32)。区别在于这两个的取值集合是**冻结**的（四类角色不得增删），
-- 而 `status` 那类列将来可能加码，varchar 加码不需要 DDL。这是有意的取舍。
--
-- 密码一律 `bcrypt`（`security/passwords.py` 直调，不经 passlib），
-- 输出固定 60 字符，255 是留给换算法的余量。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 数据范围。**行级权限的唯一来源**，与 `role_permission` 互补：
-- 能力回答「心理老师能不能看个案详情」，范围回答「这位心理老师能不能看**这名**学生」。
--
-- 四个可空列是 `scope_type` 的一行一义：SCHOOL 只填 `school_id`，GRADE 只填 `grade_id`，
-- 以此类推。**半填的行是这一层唯一要防的东西** —— `student_scope_predicate` 里
-- 每一条分支都带 `is not None`，否则一个 GRADE 类型但 grade_id 为 NULL 的行
-- 会匹配上 grade_id 也为 NULL 的学生（SQLAlchemy 的 `==` 对 `None` 会翻成 `IS NULL`）。
--
-- 没有任何东西引用这张表（它是叶子表），所以删了重插是安全的。
-- **这条结论不要推广到别的表** —— 全库其余每一张表都被 RESTRICT 指着。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 二、权限与配置
-- =============================================================================

-- 能力矩阵的**偏离项**。五个能力 × 四个角色 = 20 格，表空时回退
-- `security/permissions.py` 的 `CAPABILITY_DEFAULTS`。
-- 写入时若值等于默认值则删除该行，所以表里只留偏离项
-- —— 这解释了「全新库 0 行 / 开发库 20 行」这个差别，别照着开发库去断言它。
--
-- `scope_level` 是 varchar 不是 enum：合法等级由 `CAPABILITY_LEVELS` 按能力分别定义
-- （同一串等级名在不同能力下意思不同），五个能力的合法集合不一样，一个 enum 表达不了。
-- 界面的下拉框从那张表渲染，写入口因此收窄到定义域内。
--
-- `updated_by` 可空：清理脚本会把「谁动过它」的出处置 NULL —— 重新指到 admin 上
-- 会是一句谎话。`role_permission` 整表保留，所以那条外键只能靠置 NULL 解，调顺序解不了。
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

-- 系统配置的**偏离项**，与 `role_permission` 同一个形状：
-- 表空 / 读不到 / 读取抛异常时一律回退 `settings_service.DEFAULTS`，
-- **绝不因为读不到配置就把界面清空**。所以全新库 0 行是正常的。
--
-- `value_json` 是 JSON 列：配置值有字符串、数组、对象三种（校名 vs 跟进方式列表 vs 机构标识整组）。
--
-- **这张表刻意不是评分阈值的家。** 总分分段、维度分段、效度重测阈值随
-- `scale_rule.config_json` 走 —— 因为 `assessment_result.rule_version` 要能回答
-- 「这条结果当时按什么标准判定」，放通用配置表会让一次误改追溯改写所有历史结果的解释。
--
-- `key` 是 MySQL 的非保留关键字，照抄真库的写法加反引号。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 三、量表与评分规则
-- =============================================================================

-- 量表版本。**全库唯一一张没有 `updated_at` 的业务表** —— 一个版本一旦发布就不再改它，
-- 改已发布版本 = 生成新版本（`1.0.0 → 1.0.1`），旧的标 RETIRED 保留。
-- 所以它只有 `created_at` 与 `published_at`，这是一个刻意的形状不是遗漏。
--
-- 只创建 DRAFT 的版本，不参与评分，新建任务也不会选中它；
-- 只有 `POST /scales/versions/{id}/publish` 把它变成 PUBLISHED（同时把同量器上一个
-- 已发布版本转为 ARCHIVED，保留不删除）。导入开放给管理员 + 心理老师，
-- 发布与改规则**只归管理员**。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 题库。100 道题（MHT），`data/mht_scale.json` 是题干与维度归属的**唯一出处**。
--
-- `dimension_code` 可空：10 道效度题不属于任何维度。维度是英文编码
-- （LEARNING_ANXIETY 等八个），**不是 A–H 单字母** —— 与 `labels.ts` 的
-- `DIMENSION_LABELS` 必须一致，`test_status_vocabulary.py` 把守这条。
--
-- `is_validity_question` 与 `is_key_question` 是 tinyint(1) 而不是布尔：
-- MySQL 的 BOOLEAN 本来就是 tinyint(1) 的别名，SHOW CREATE TABLE 出来就是这个样子。
--
-- `status` 存在但当前全是 ACTIVE（题库没有"停用某题"的界面）。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 评分规则。`config_json` 的形状由 `scale_engine.engine.ScaleRuleConfig` 定义：
-- question_count / validity_questions / key_questions / validity_retest_threshold /
-- total_bands / dimension_bands / interpretations。
--
-- **没有单独的阈值表 —— 这是"阈值属于规则版本"那条约定的物理形式。**
--
-- `rule_version` 的标识统一由 `scale_rule_service.rule_version_for(code, version)` 生成
-- （`MHT` + `MHT-1.1.0` → `MHT-RULE-1.1.0`），种子 / 规则编辑 / 题库导入三个创建方
-- 都走它。**标识里不得出现生命周期词**：规则行比草稿活得久，量表发布之后它仍然是
-- ACTIVE 的那一行，而 `MHT-1.1.0-RULE-DRAFT · 生效中` 是自相矛盾的；
-- 而且 `_bump_version` 取**尾部**数字，以词结尾的名字会 bump 成 `...-RULE-DRAFT-2`，
-- 从此不再像版本号。
--
-- V1.2 加了一条唯一键 `uq_scale_rule_version(scale_id, rule_version)`：
-- 同一量表的同一个版本只能有一条规则行。此前这条约束靠写入侧的自律
-- （`rule_version_for` 生成的名字唯一，所以没撞过），而**名字唯一是生成规则的
-- 副作用，不是被保证的不变量** —— 两个创建方各自拼一个字符串，撞不撞全看它们
-- 是不是都调了那个函数。这条键把它变成事实。它同时是
-- `risk_event.uq_risk_event_session_trigger_rule` 之外第二处"版本要参与唯一性"的地方。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 四、测评任务、会话与作答
-- =============================================================================
--
-- 这一节横跨第③层（学校管理事实）与第①层（原始答题事实），是**故意放在一起的**：
-- `assessment_session` 既指 `assessment_task`，又是 `risk_event` /
-- `assessment_result` / `retest_plan` 的父行。把它再往后排，下面几节里的表就会
-- 引用到还不存在的表 —— 第一版正是这么排错的。
-- **建表次序是按外键图定的，不是按分层定的**；分层体现在每一节的标题与注释里。

-- 测评任务。
--
-- **`status` 这一列是"人写的那个值"，不是界面上显示的状态。** 界面上显示的是
-- `task_service.effective_task_status()` **现算**出来的（还没到 start_at → 未开始；
-- 过了 end_at → 已结束，不管还有没有人没答；目标行全完成 → 已结束；其余 → 进行中）。
-- 这一列保留的意义是「人可以显式把任务标成 DRAFT / PAUSED / CLOSED，
-- 而人写的值不可被推导盖过」。此前没有任何代码读它或改它，于是每一行永远显示
-- 「进行中」——一批 3/3 全收齐的导入、一场过了截止的普查，长得一模一样。
--
-- `source` 标记这一场是系统内作答还是外部平台导入（迁移 0010）。
--
-- 权限上**测评任务不是一个能力，是角色**：写归心理老师，读归心理老师 + 德育领导，
-- **系统管理员读写都不包含它** —— 它是学校业务而不是系统级配置。
--
-- `uq_assessment_task_school_id (id, school_id)` 与上面那三张表同族：
-- 它白说（`id` 本来就唯一），唯一的用途是当 V1.2 那几条复合外键的目标
-- （`assessment_target_fk_task_school`、`assessment_import_batch_fk_task_school`、
-- `assessment_external_result_fk_task_school`），让"这批数据属于这场任务、
-- 这场任务属于这所学校"在同一条边上成立。
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
  `voided_at` datetime DEFAULT NULL,
  `voided_by` int DEFAULT NULL,
  `void_reason` varchar(500) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `task_no` (`task_no`),
  UNIQUE KEY `uq_assessment_task_school_id` (`id`,`school_id`),
  KEY `scale_id` (`scale_id`),
  KEY `school_id` (`school_id`),
  KEY `created_by` (`created_by`),
  KEY `assessment_task_fk_voided_by` (`voided_by`),
  CONSTRAINT `assessment_task_ibfk_1` FOREIGN KEY (`scale_id`) REFERENCES `assessment_scale` (`id`),
  CONSTRAINT `assessment_task_ibfk_2` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_task_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `assessment_task_fk_voided_by` FOREIGN KEY (`voided_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 目标行：这一场要测谁。**发放时按创建者的数据范围固化成行**，所以：
--   - 之后名册新增的学生**不会**自动加进已有任务；
--   - 「导入全了名单，能查出谁没参加测评吗」的答案是**能，但要在任务发放之后导入**。
--
-- 完成率 = 已完成目标行 / 目标行总数，**按场**计算，不加范围谓词
-- （状态与 name / start_at 一样是任务自身的属性，跟着范围走的是完成率）。
--
-- 没有 `created_at`/`updated_at`，用 `assigned_at` 代替 —— 它的"出生时间"
-- 就是分配时间，再写一个 created_at 是同一个事实的两个名字。
--
-- V1.2 加了快照、处置与"哪一条算数"三组列（**都追加在表尾**，与迁移的实际产物一致）：
--
--   **快照七列**（`school_id_snapshot` NOT NULL + 学号 / 姓名 / 年级 / 班级 / 性别 / 年龄）：
--   发放那一刻把这些**固化下来**。名册是会变的（学生转班、改名、离校），
--   而"这次普查发给了初一的谁"是历史事实 —— 现查名册会让一份报表说出另一天的事。
--   `school_id_snapshot` 参与三条复合外键（`→ assessment_task (id, school_id)`、
--   `→ student (school_id, id)`、`→ assessment_external_result (id, student_id)`），
--   **它必须与那三张表里的学校一致**，跨校的数据连不进来。
--
--   **处置**（`participation_disposition` / `_reason` / `_note` / `marked_by` / `marked_at`）：
--   免测、请假之类的"这次他不测"，与 `status`（测没测）是两个维度。
--
--   **哪一条算数**（`target_source` / `supplemented_from_batch_id` /
--   `effective_session_id` / `effective_external_result_id`）：`target_source` 区分
--   这一行是按任务范围发的还是后来**补发**的（补发那一支记回批次）；
--   `effective_*` 两列是"这次任务这个人最终采信哪一条记录"的落点。
--
-- **这三组列目前只建好了形状**：读写它们的业务逻辑属于 V1.2 后续阶段，
-- 这一版只做结构对齐。所以此刻它们的默认值（`REQUIRED` / `TASK_SCOPE`）
-- 就是每一行的实际取值 —— 别从"列在"推出"功能在"。
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
  CONSTRAINT `assessment_target_fk_marked_by` FOREIGN KEY (`marked_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `assessment_target_fk_student_school` FOREIGN KEY (`school_id_snapshot`, `student_id`) REFERENCES `student` (`school_id`, `id`),
  CONSTRAINT `assessment_target_fk_task_school` FOREIGN KEY (`task_id`, `school_id_snapshot`) REFERENCES `assessment_task` (`id`, `school_id`),
  CONSTRAINT `assessment_target_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_target_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 测评会话：一名学生的一张卷子。（**第①层：原始答题事实**）
--
-- **"一场任务一人一卷"由两个唯一键合成**（V1.2 把原来那条一拆为二）：
-- `uq_session_task_student_attempt(task_id, student_id, attempt_no)` 描述事实
-- ——同一场任务的同一次尝试只有一张卷子；`uq_session_effective_task_student
-- (effective_task_student_key)` 描述**当前有效**的那一张。后者建在那列生成列上
-- （`is_effective=1 且 task_id 非空` 时才有值），所以复测可以并存，
-- 但同一时刻**只有一张是有效卷**。「有效」是 §18.8 那条口径的落点：
-- 在线答卷已提交时默认拒绝被外部结果覆盖，要人工选一条。
-- `task_id` 可空 —— NULL 在 MySQL 唯一索引里**不参与去重**，所以脱离任务单独开的
-- 会话不互相冲突；生成列在 `task_id IS NULL` 时也是 NULL，同理。
--
-- **`attempt_no` 默认 1**：V1.0 的数据回填成 1，所以旧数据的唯一性判据与从前一字不差。
--
-- `scale_version` 与 `rule_version` 分开存：前者是题干的那一版，后者是评分规则的那一版。
-- 一次交卷同时钉住两者，"这条结果当时按什么标准判定"才有答案。
--
-- `duration_seconds` 是**交卷时算出来存下的**（submitted_at - started_at），
-- 不是每次读时算 —— started_at 可空，而且"用时"是一个应当被固定下来的事实。
--
-- `source`（迁移 0010）：IN_SYSTEM / IMPORTED。导入的会话按系统内作答**同一口径**判定，
-- 同样开风险提示与关怀档案 —— 学校要的正是"把校外测的结果拿进来一起看"。
--
-- `idempotency_key`：提交带 `Idempotency-Key: submit-<id>` 头，重复提交返回既有结果，
-- 不产生重复风险事件。
--
-- V1.2 在这个表上加了 15 列，四组各回答一件事（**分组见 §16.1 的六段式迁移**）：
--
--   ① **这条会话属于谁**：`school_id`（NOT NULL，从 `student.school_id` 回填）、
--      `age_at_test`（测评当时的年龄，与名册上的 `student.age` 分开存 ——
--      名册那一列不会自己变，所以"当时几岁"只能在这里留）。
--   ② **这条会话从哪来**：`source_type`（ONLINE / EXTERNAL_FULL_ANSWER / …，
--      比 `source` 更细，`source` 保留不动）、`external_source_system` /
--      `external_result_id`（外部平台那一条结果的身份）、`import_batch_id`。
--      后两列上有 `uq_session_external_source_result`，是外部导入的**幂等**落点。
--   ③ **哪一条算数**：`is_effective` / `conflict_status` / `supersedes_session_id`
--      —— 复测、覆盖、放弃，三种处置的结果都落在这一组里。
--   ④ **算出来没有**：`calculation_status` / `calculation_error` /
--      `answer_snapshot_hash` / `answer_hash_algorithm`。
--      哈希是**答卷原文的指纹**，用于回答"这条结果是从哪一份答卷算出来的"。
--
-- **`tested_at` 与 `tested_at_source` 是两个都要小心的列**：`tested_at` 是
-- "这场测评发生在什么时候"，与 `started_at`（什么时候开的卷子）**不是同一件事**
-- ——导入的记录没有"开卷"这个动作。`tested_at_source` 目前只有
-- `PENDING_VERIFICATION` 这一个值被写进去过，是**已知的死标志**（CLAUDE.md 缺口）；
-- 两条可信的校内路径没有回填它。
--
-- **时间戳的两种来源（这个表上同时有）**：`created_at`/`updated_at` 是**数据库**写的
-- （`DEFAULT (now())`，MySQL 会话时区 = SYSTEM = UTC+8），而 `started_at`/`submitted_at`
-- 是 **Python** 写的（`assessment_service.now_utc_naive()` = UTC）。
-- **同一行上差 8 小时。** `DateTime(timezone=True)` 在 MySQL 上是空操作。
-- 没有业务逻辑跨这两类列比较时间，所以这不是 bug，但排查时间问题时会被它带偏 ——
-- 先跑一次 `SELECT now(), utc_timestamp()` 再下结论。
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
  CONSTRAINT `fk_session_task` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 逐题作答。**第①层，一次写入，永不修改** —— 人工复核不得改它。
-- 交卷后 `SESSION_LOCKED` 挡住后续写入。
--
-- `answer` 是 varchar(8) 不是 enum：MHT 的选项是 YES / NO，其余量表可能是别的短码。
-- `score` 是**当时由引擎算出的分并存下来**，不是每次读时重算 —— 这样即使规则改了，
-- 这一行的分仍然是当时那个规则算的。同一条道理让 `interpretation` 也存在结果行上。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 五、量表计算事实（第②层）
-- =============================================================================

-- 总分结果，**一份答卷一个**（`UNIQUE (session_id)` 是唯一的匿名唯一键，
-- 没有名字 —— 真库就是这么建的，照抄）。
--
-- `validity_score` 命中几道效度题，与 `validity_retest_threshold`(=7) 比出
-- `validity_status`。效度异常的含义是"这份答卷的回答模式本身可疑"，
-- 处置是**建议复测**，不是任何形式的结论。
--
-- `total_score` 上限是 90 不是 100：**10 道效度题不计入总分**，
-- 所以分段的上界是 90（100 − 10）。
--
-- `rule_version` 记「这条结果当时按什么标准判定」。**阈值因此不能放 system_setting** ——
-- 放通用配置表会让一次误改追溯改写所有历史结果的解释。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 八维度结果。`rule_version` **在这里也存一份** —— 不只挂在总分那一行上。
-- 这让"只看维度结果"的查询也能自己回答"这条维度分按什么标准判的"，
-- 不必回到 assessment_result 去 join。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 六、学校管理事实（第③层）
-- =============================================================================

-- 风险提示。
--
-- 一条 = 引擎的**一次命中**，而引擎对**每一道命中的重点题各写一行**
-- （MHT 有两道：85 / 97）。所以"一个人两道都中了"在这个表里是**两行** ——
-- 工作台那张卡的单位因此是「条」不是「人」。
--
-- 这与 `student_care_case` 是两件事不是一件事的两半：
--   risk_event        一条 = 一次命中，一名学生一次交卷可能两条；
--   student_care_case 一条 = 一名学生的一段关怀过程，同一时间最多一条在办。
--
-- `trigger_rule` 记录"哪一条规则被触发了"（`KEY_QUESTION_85`、
-- `TOTAL_BAND_KEY_ATTENTION`），`risk_level` 是关注档位。**筛查口径，不是诊断口径。**
--
-- 重导一次外部测评时会**收回 PENDING 且无人复核的**行（避免留下两条同名待办），
-- 但心理老师写过的复核是工作记录，不会因为重导一次就消失。
--
-- V1.2 加了三列与一条唯一键（**都追加在表尾**）：
--   `signal_type`    这条提示是**哪一类信号**触发的（NOT NULL，V1.0 的行按
--                    `risk_type` 分档回填）。它与 `risk_type` 的关系是"一类 / 一条规则"：
--                    规则会增删，分类不会。
--   `requires_manual_review`  这一条**必须有人看过**才算完。默认 0，
--                    回填时只有 `MANUAL_REVIEW_REQUIRED` 那一档置 1。
--   `rule_version`   触发它的评分规则版本（默认 `LEGACY_UNKNOWN`，
--                    V1.0 的行答不上来就叫这个名字，不猜）。
--   `uq_risk_event_session_trigger_rule(session_id, trigger_rule, rule_version)`：
--   同一场会话的同一条规则、同一版规则只留一条提示。
--   **`rule_version` 必须在这个键里**：换一版规则重新算同一份答卷会命中不同的规则，
--   少了它，第二次计算会撞 1062 而不是留下第二条提示。
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
  `voided_at` datetime DEFAULT NULL,
  `voided_by` int DEFAULT NULL,
  `void_reason` varchar(500) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_risk_event_session_trigger_rule` (`session_id`,`trigger_rule`,`rule_version`),
  KEY `student_id` (`student_id`),
  KEY `reviewed_by` (`reviewed_by`),
  KEY `ix_risk_event_status_created_at` (`status`,`created_at`),
  KEY `risk_event_fk_voided_by` (`voided_by`),
  CONSTRAINT `risk_event_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `risk_event_ibfk_2` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `risk_event_ibfk_3` FOREIGN KEY (`reviewed_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `risk_event_fk_voided_by` FOREIGN KEY (`voided_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 关怀档案。
--
-- **一个学生有多条档案是正常状态，不是脏数据。** 上学期关掉的是一次完整的关怀过程，
-- 这学期再出状况是一条新的过程 —— 秋季关档、春季再关一次是学校每年都会遇到的时序。
--
-- 这里**曾经有一个 `UniqueConstraint(student_id, status)`**（迁移 0012 删掉），
-- 它把「同一时间只有一条在办」当成了数据库约束。约束还在时第二次关闭撞 UNIQUE、
-- `db.flush()` 抛 IntegrityError，用户拿到 500。**那条约束错在判据**：它连
-- 两条**都关掉了**的档案也禁止（status 相同即冲突），而秋季关档、春季再关
-- 正是学校每年都会遇到的时序。
--
-- 于是 `student_id` 上是**普通索引 `ix_student_care_case_student_id`，不是唯一索引**。
-- 这个索引有第二个身份：它兼作 `student_id` 外键的索引。删它之前必须先建它
-- —— MySQL 会以 **1553** 拒绝删一条外键正在用的索引。
--
-- V1.2 把「同一时间只有一条在办」换了个**判据**做回数据库约束：
-- `active_student_id` 是生成列（`status <> 'CLOSED'` 时等于 `student_id`，
-- 否则为 NULL），`uq_care_case_one_active_per_student` 建在它上面。
-- MySQL 的 UNIQUE **把多个 NULL 当成互不相同**，所以关掉的档案想有几条有几条，
-- 而在办的最多一条 —— 判据从"状态相同"改成了"**在办的**才算数"。
-- V1.0 的数据回填成什么？不用回填：生成列自己算。
--
-- **代价与出路（CLAUDE.md 已知缺口）**：`care_service.reopen_case` 无条件把
-- `status` 设成 `FOLLOWING`，所以「重开一份已关闭档案、而该学生还有另一份在办档案」
-- 会撞 1062 拿到 500；`assessment_service.open_or_reuse_care_case` 也有读后写的竞态。
-- 修它要么让 `reopen_case` 复用/收编另一条，要么走带锁的 find-or-create，
-- 属于 V1.2 后续阶段，这一版只对齐结构。
--
-- 三个列表对 CLOSED 的取舍各不相同，各自的理由写在该处：
--   get_care_case             不过滤（关闭之后这一页还得能看）
--   list_care_cases           不过滤（它有「已关闭」页签）
--   leader_progress           过滤  （那一页叫「重点进展」，CLOSED 没有进展可看）
-- **这不违反「关闭档案不得删除历史记录」** —— 那条说的是别删行，不是每条列表都得列出来。
--
-- V1.2 在表尾加了四个出处列与一个版本号：`closed_by` / `reopened_by` /
-- `reopen_reason`（谁关的、谁重开的、为什么）与 `case_version`（默认 1，
-- 供乐观锁用）。V1.0 的 `opened_at`/`closed_at`/`reopened_at` 只记了时间，
-- 没有记人 —— 而"这条档案是谁关的"在交接班时是一个真实的问题。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 七、专业工作事实（第④层）
-- =============================================================================
--
-- 这四张表有一个共同形状：**每一张都有一个 `confirmed_facts TEXT NOT NULL`**。
-- 那是这一层的语义要求 —— 每一件事都必须写清「确认了什么事实」。
-- `NOT NULL` 是有意的：一条没有内容的跟进记录不是记录。
--
-- 它们**都挂在 `student_id` 上**，而 V1.2 又各自加了一个 `care_case_id`
-- ——两个都留，不是重复：
--
--   `student_id`  一名学生的历史记录在档案关闭之后**仍然属于他**，
--                 所以这四张表都不受「关闭」影响，按人查走这一列；
--   `care_case_id` 这条记录属于**哪一段关怀过程**。按人查得到全部历史，
--                 按档案查得到"这一轮我们做了什么"，两者回答的不是同一个问题。
--
-- 两条边合起来由 `..._fk_case_student` 那条**复合外键**保证：
-- `(care_case_id, student_id) → student_care_case (id, student_id)`。
-- 它挡住的是一类很难发现的数据：跟进记录挂到了**别的学生**的档案上。
-- 单列的两条外键各自只能保证"档案在""学生在"，合起来才保证"是这名学生的这份档案"。
-- **`care_case_id` 可空**（V1.0 的行没有档案可挂），而 MySQL 的复合外键在
-- 任一列为 NULL 时不检查——这正是想要的：老数据留空，新写的按规矩来。

-- 人工复核。**不得修改原始答卷** —— 它只写自己的判断与下一步。
-- 一条复核写完之后，对应的 risk_event 从 PENDING 转走，不再是待办。
--
-- `student_id` 是 V1.2 补的（回填自它对应的 `risk_event.student_id`）：
-- 复核记录本身不存学生，学生本来要从风险事件推两步才拿得到，
-- 而「按人查这条学生被复核过几次」是一条要天天用的查询。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 跟进记录。`next_follow_up_date` 是 NOT NULL —— 一条跟进必须有下一步，
-- 没有下一步的跟进在工作台上是看不见的，那等于没做。
-- 工作台的「逾期跟进」卡点进去的 `/counselor/cases?filter=overdue`
-- 判的就是它（`c.overdue === true`）。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 家庭回访。`contact_date` 与 `next_contact_date` 是 **DATE 不是 DATETIME** ——
-- 家庭回访是"哪一天做的"，不是"哪一秒做的"。
--
-- `channel` / `result` / `support_status` 的候选值是配置项
-- （`settings_service.DEFAULTS` 的 `care` 那一组），**不是枚举表** ——
-- 学校可以自己改这几个下拉框里的选项，所以这里是 varchar 不是 enum。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 复测计划。**两个外键指向同一张表** `assessment_session`：
--   source_session_id    因哪一场而计划复测（可空 —— 手工建的复测没有来源）
--   completed_session_id 哪一场完成了它（可空 —— 还没做）
-- 这两条让"复测闭环"可以被验证：completed_session_id 非空且那一场已交卷时，
-- 这条计划真的被兑现了。
--
-- 计划一旦建立，它指向的那一场（source_session_id）**不能被删除** ——
-- 这是外部测评导入"就地改写而不删了重建"的四个理由之一。
--
-- V1.2 加了三个状态列（都追加在表尾）：`completed_at` / `cancelled_at` /
-- `cancel_reason`。此前"这条计划后来怎么样了"只能靠 `completed_session_id`
-- 有没有值来推，而"取消"这个结局在当时**无处可写** —— 一条作废的计划与一条
-- 还没做的计划在库里长得一模一样。`status` 那一列回答的是同一件事的另一半，
-- 两个都要留着：状态是给人筛的，时间戳是给轨迹查的。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 八、横切：审计
-- =============================================================================

-- 审计轨迹。
--
-- **`actor_user_id` 与 `actor_role` 都可空** —— 未登录的行为（登录失败、系统事件）
-- 没有操作人。这正是界面「操作人」列显示 `—` 的原因；导出时**留空**，
-- 因为 `—` 是界面占位符，写进 CSV 会让整列被当成文本。
--
-- 审计行要能回答**「谁做的」，不只是「哪个角色做的」** —— `actor_role` 回答不了
-- 「一所学校里三位心理老师，是谁看的」。所以查询时按那一批 actor_user_id
-- **一次查出来**（不是逐行查）拼上姓名与账号。
--
-- `student_id` 可空，**范围过滤的判据就在这一列上**：命名了学生的行按数据范围过滤，
-- 未命名学生的组织级事件（登录 / 导出 / 导入）保留。
--
-- `ix_audit_log_student_id` 是显式建的（迁移 0006），不是 `index=True` 的自动名 ——
-- 这样"这一列需要索引"是一个**决定**，不是一次自动推导。
--
-- `detail` 是 TEXT，**当前没有任何接口能读它**（`GET /audit-logs` 的序列化里没有它，
-- 界面也不渲染）。所以遮蔽模式、导入的 created/updated/skipped 与 resolution、
-- 年龄覆盖的旧新值 —— 全部写进了库，没有任何接口或界面能读出来。
-- 这是「已记录」与「可追溯」之间的差距，是有意留的：补它要给 /audit-logs 加字段，
-- 并想清楚它该不该受权限约束（同一张表里既有"登录失败"也有"查看了谁的档案"）。
--
-- **不在日志里打印完整答卷、重点题回答或家庭回访正文。**
--
-- V1.2 在表尾加了六列（`event_id` / `actor_account_snapshot` / `request_id` /
-- `detail_json` / `result_code` / `audit_hash`），落点是**这条轨迹本身能不能被验证**：
--
--   `event_id`（UUID，`uq_audit_event_id` 唯一）  跨系统的关联标识；
--   `actor_account_snapshot`  **当时**那个人的账号。`actor_user_id` 是外键，
--                             而账号**可以被停用、改名**（不能删，见「停用 ≠ 删除」），
--                             所以"当时是谁"要留一份快照 —— 与 `assessment_target`
--                             那七列快照是同一条道理。
--   `request_id`              一次请求可能在库里留几行，靠它串起来。
--   `detail_json`             结构化的 detail。`detail` 那个 TEXT 保留不动
--                             （它是给人读的），这一列是给机器读的。
--   `result_code`             结果码（`result` 是 SUCCESS / FAILED 那一层）。
--   `audit_hash`              这一行的指纹，用于回答"这条轨迹有没有被改过"。
--
-- **这六列目前都没有写入方**：V1.2 的审计增强属于后续阶段，这一版只对齐结构。
-- 于是除了 `event_id` 是唯一的（多行 NULL 不冲突），其余五列此刻全是 NULL ——
-- 别从"列在"推出"功能在"。
--
-- 上一段说的「`detail` 没有任何接口能读它」**依然成立**，`detail_json` 也一样。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================

-- =============================================================================
-- 九、导入与外部平台结果
-- =============================================================================

-- 名册导入批次（一次上传 = 一行）。
--
-- 与 `assessment_import_batch`（校外测评记录导入）是**两条互不相干的链路**，
-- 各有各的批次表：前者改的是名册（姓名 / 年级 / 班级 / 年级 / 年龄），
-- 后者改的是测评记录。两条链路各管各的常量、各报各的冲突，别把它们合并。
--
-- `file_sha256` 是**整份文件**的指纹：同一份名册传第二遍时，批次行照样新建
-- （那是两次真实的操作），但它的行级明细可以拿来比对「这次与上次差在哪」。
--
-- 五个计数列（total / created / updated / skipped / error）在提交那一刻写死，
-- 与 `audit_log.detail` 里那份是同一批数字的两个去处 —— 这里给界面查，
-- 那里给轨迹查。
--
-- `status`：PREVIEW（已解析、明细已落库、名册还没动）/ COMMITTED。**预览与提交是两次请求**，
-- 而预览这一次就把批次与逐行明细写进来了 —— 提交时带的是**批次 id**，不是一份凭据：
-- 「拿着 A 的预览去提交 B」在这里不成立，因为没有第二份可以拿错。
-- 这条路换来的是**可查的导入历史**（导入批次卡）：一份文件留下了哪几行、被怎么处置，
-- 事后看得见；代价是预览会往库里写行。同一操作者 + 同一 `file_sha256` + 仍是 PREVIEW
-- 的那一批会被**就地重写**（删掉旧明细、重新解析），所以反复预览不累积垃圾。
-- （`FAILED` 不在写入方里：解析在落库之前就整份被挡下，那一批根本没有行可留。）
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 名册导入的**逐行**明细。一行 = 文件里的一行，`row_no` 是它在文件里的行号
-- （从 **2** 数起 —— 第 1 行是表头。与老师用 Excel 打开时看到的行号对得上，
-- 报错时说的就是它）。
--
-- 这一行同时是「文件里的值」与「处理结果」的并置：`student_no` / `name` /
-- `grade_name` / `class_name` / `gender` / `age` 是这一行要落进名册的值，
-- `student_id` / `processing_status` / `conflict_code` / `message` 是处理出来的。
-- 前六列存的是**规范化之后**的值，不是文件里的原始字节：`gender` 是
-- `MALE` / `FEMALE`（界面用 `genderLabel` 翻回中文），`age` 是整数。
-- （`age` 是唯一一个装不下原始写法的：「13岁」那种原文只活在错误文案里。）
--
-- `processing_status`：PENDING / CREATED / UPDATED / SKIPPED / ERROR。
-- `ERROR` 在**预览**时就定下来（缺列、班级与年级对不上 —— 没有任何处置方式能救），
-- 其余四个在**提交**时才写。`conflict_code` 记这一行撞上过什么
-- （`STUDENT_NO_EXISTS`），与错误分开：冲突要人拍板，错误不用。
-- 选「放弃」的那一行也留着它 —— 那件事确实发生过，只是被处置掉了。
--
-- 年龄覆盖会写审计（`更新学生年龄`）并由 `student_age_change_log` 留旧值 ——
-- 这张表只记「这一行被怎么处理了」，不记名册那一列变成什么。
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

-- 校外测评记录导入批次（一次上传 = 一行）。
--
-- **判重按「月」为单位**：同一名学生、同一个自然月，只能有一次外部导入
-- （`tested_on` 落在哪个月由文件里的测评日期决定，不是由今天决定）。
-- 所以 `duplicate_of_batch_id` 记的是「这一批与哪一批撞了」——
-- 学校把一次普查分两批导（先初一、隔几天再初二）时，第二批不会被挡在门外，
-- 而**同一个月重复导同一份文件**会走冲突流程。
--
-- `tested_at` / `source_timezone`：外部平台给的时间与它的时区。
-- `tested_at` 在库里一律按**外部平台的墙上时间**存，不换算 ——
-- 换算回 UTC 会让「9 月 3 日那次普查」在跨时区展示时跳成 9 月 2 日，
-- 而学校脑子里的日期只有一个。
--
-- 四个策略列（`import_mode` / `conflict_policy` / `out_of_scope_policy` /
-- `allow_age_overwrite`）是**这一批当时按什么口径处理**的记录。
-- 它们必须落在批次行上而不是读全局配置：一次导入发生在某一天，
-- 「那天我们按什么规矩收的」是历史事实，配置一改就会追溯改变它。
--
-- `resolution`（overwrite / skip）是**文件级**的一次选择，不是逐行 ——
-- 一次普查两百行逐行点会在第 20 行开始乱点。`NONE` 与「选了放弃」必须长得不一样。
--
-- `parser_version` / `schema_version`：解析器换了版本之后，
-- 「这一批当时是谁读的」要答得上来 —— 题号映射的规则就在解析器里。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 校外测评导入的**逐行**明细。一行 = 外部文件里的一名学生。
--
-- 三组列各自回答一个问题，**不互相覆盖**：
--   `raw_*`        文件里是什么（原样抄下来，报错时说的就是它）；
--   `normalized_*` 归一化之后是什么（去空格、全角转半角、年级名对齐）；
--   `matched_*`    我们把它认成了谁（学校 / 年级 / 班级 / 学生）。
-- 三组都留着，是因为「认错了」这件事只有在能并排看这三行时才看得出来。
--
-- `match_status` / `match_confidence` / `candidate_student_ids`：同名同班时
-- 归一到哪个学生靠年龄消歧（±1 岁容错），消不了就落进候选。
-- **判据与容错范围的说明在 `services/assessment_import_service`**，
-- 这一列只记录结论。
--
-- `age_before` / `age_after` / `age_resolution`：文件里的年龄与名册不一致时，
-- 这两列记的是「改之前 / 改之后」，旧值另有一份在 `student_age_change_log`。
-- **不设容差**：差一岁也要问（名册年龄本来就不会自己变，差一岁恰恰是最常见的
-- 那种不一致）。它与 `locate_student` 的 ±1 岁**不是同一件事**：
-- 那个用于「这一行说的是哪个学生」，要能容忍文件是去年那次普查。
--
-- `conflict_resolution`：同一场任务里两条来源事实（学生自己在线答的 + 学校导入的
-- 外部结果）撞上时，人从四档里选的那一档（`KEEP_ONLINE` / `USE_EXTERNAL` /
-- `REJECT_EXTERNAL` / `KEEP_BOTH_BUT_ONE_EFFECTIVE`，常量在
-- `services/assessment_import_service`）。**它与 `resolution` 是两次选择、两个问题**：
-- 那一列回答「这一行写不写进去」，这一列回答「写的时候以哪一份为准、另一份留不留」。
-- 合成一列会让整批的「覆盖」顺手成为一种来源裁决，而那一列的注释里写着为什么不行。
-- 只有 `match_status = CONFLICT` 的行会用到它，所以可空——留空是「这个问题没问过」。
--
-- `out_of_scope_reason`：名册里没有这个学生（或不在本批次辖区）时写这里，
-- 与 `conflict_code` 分开 —— 前者是「我们不收」，后者是「要人拍板」。
--
-- `session_id` 指向真正落库的那一场会话；`external_result_record_id` 指向
-- `assessment_external_result` 里那条原始结果。两条边互相指着（见文件末尾那条
-- `ALTER TABLE`），所以后者只能建成外键而不能写在表体里。
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
  `conflict_resolution` varchar(32) DEFAULT NULL,
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
  KEY `assessment_import_row_fk_matched_school_class` (`matched_school_id`,`matched_class_id`),
  KEY `assessment_import_row_ibfk_4` (`resolved_by`),
  KEY `assessment_import_row_fk_session_student` (`session_id`,`student_id`),
  KEY `ix_import_row_external_record` (`external_result_record_id`),
  KEY `ix_import_row_match_status` (`batch_id`,`match_status`),
  KEY `ix_import_row_matched_grade_class` (`matched_school_id`,`matched_grade_id`,`matched_class_id`),
  KEY `ix_import_row_normalized_match` (`normalized_grade_name`,`normalized_class_name`,`normalized_name`),
  KEY `session_id` (`session_id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `assessment_import_row_fk_matched_school_class` FOREIGN KEY (`matched_school_id`, `matched_class_id`) REFERENCES `class_group` (`school_id`, `id`),
  CONSTRAINT `assessment_import_row_fk_matched_school_grade` FOREIGN KEY (`matched_school_id`, `matched_grade_id`) REFERENCES `grade` (`school_id`, `id`),
  CONSTRAINT `assessment_import_row_fk_session_student` FOREIGN KEY (`session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`),
  CONSTRAINT `assessment_import_row_ibfk_1` FOREIGN KEY (`batch_id`) REFERENCES `assessment_import_batch` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_3` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `assessment_import_row_ibfk_4` FOREIGN KEY (`resolved_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- **外部平台报的那份结果原文**，与「我们据此算出来的那一场」分开存。
--
-- 这是 V1.2 里最要紧的一条分层：`assessment_session` / `assessment_result`
-- 是我们自己的事实（第①②层），而这一张是**别人说的话**。
-- 两者放在一起，是因为「校外的记录」与「校内的记录」要说得出各自的出处 ——
-- 把外部结果直接写成一场校内会话，事后就再也分不开哪一条是平台给的、
-- 哪一条是我们的引擎算的。
--
-- `verification_status`（PENDING / VERIFIED / REJECTED）**只描述这份外部结果**，
-- 不描述学生。与 `assessment_session.conflict_status` 一起构成§18.8 那条口径：
-- 在线答卷已提交时默认拒绝覆盖，要人工选一条有效结果。
--
-- `applied_session_id` 指着「这一份被采用到了哪一场」；没被采用时为空。
--
-- `result_payload_json` 是**整包原文**。它不受任何接口的读取，只有排查时才看 ——
-- 但正因为外部平台的字段随时会变，留一份原文是唯一能回答
-- 「当时它到底说了什么」的办法。
--
-- `uq_external_result_source_key`（source_system + external_result_id）：
-- 同一个平台的同一条结果只能进库一次，这是**幂等**的落点。
-- 外部平台不给 ID 时那一列为空，此约束不生效（MySQL 的 UNIQUE 把多个 NULL
-- 当作互不相同），此时靠 `uq_external_result_batch_row` 兜底。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- =============================================================================
-- 十、登录会话、导出任务与其余新增
-- =============================================================================

-- 登录会话（一行 = 一次签发）。JWT 或不透明 Token 都携带这里的 `jti`。
--
-- **它是「撤销」这个能力的落点**，而 JWT 本身撤销不了：`get_current_user`
-- 每一个请求都查一次 `active` 与这一行的 `revoked_at` / `expires_at`，
-- 所以登出、强制下线、停用账号都不需要等 token 过期。
-- 这与「停用 ≠ 删除」是同一条：停用是立即生效的，因为它每次请求都查。
--
-- `session_token_hash` 存的是**哈希**，不是 token 本身 —— 库里的一份拷贝
-- 不该是一份能直接拿来用的凭据。与「密码一律不进审计 detail」同源。
--
-- 这张表是**会长的**：每次登录一行。清理窗口（过期多久之后可以删）属于运维策略，
-- 这一版没有定，所以没有任何东西删它。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 受控导出的一次作业（一行 = 一次导出请求）。
--
-- 重点学生导出**必须**经过这一行，不得只生成一次性临时下载链接 ——
-- 那是「这份文件被谁、按什么口径、什么时候导出去的」唯一能查的地方。
--
-- `scope_snapshot` 与 `field_policy` 是**当时**的范围与字段口径，
-- 存快照而不是存一个指向配置的外键：配置一改就会追溯改变这份文件的解释，
-- 与「阈值随规则版本走」（§6）是同一条道理。
--
-- `mask_level` 记遮蔽模式（MASKED / FULL）。**这一列是必须的**：
-- 同样的 action、同样的 resource_type、同样的 purpose，遮蔽与实名两条审计行
-- 长得一模一样，轨迹就答不上「这份文件是不是实名的」。
-- `purpose` 是自由文本，担不起这个字段。
--
-- `download_count` + `revoked_at` + `expires_at`：导出物是**可撤销**的。
-- 这也是「导出」与「查看」的区别 —— 查看属于审计，导出属于管控。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 年龄覆盖的**旧新值轨迹**（一行 = 一次改动）。
--
-- `student.age` 是存下来的整数（迁移 0011），**不会自己变** ——
-- 不重导名册，界面上的年龄就停在去年。而名册上的这个数一旦被改，
-- 「原来是多少」就只剩这里能答。所以覆盖年龄的路径**必须**写这一行。
--
-- 两个来源各自留自己的上下文，都指回引起这次改动的那一行：
--   `roster_import_batch_id`         学生信息导入（整份名册）
--   `assessment_import_batch_id` + `assessment_import_row_id`
--                                    校外测评导入时选了「覆盖」（只改这一名学生）
-- 后者是用户明确要求的那条（「如果发现不一致应该询问是否覆盖更新还是放弃导入」），
-- 而它与「性别只用来消歧、从不写回名册」不同：年龄这一列**有一个写入方在导入路径上**。
--
-- `reason` 是自由文本的人话，`changed_by` / `changed_at` 是出处。
-- 与审计行并存的理由：审计页按「谁做了什么」检索，而这里回答的是
-- 「这个学生的年龄改过几次、每次从多少到多少」—— 按人而不是按操作查。
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

-- 任务**设计时**选择的范围（学校 / 年级 / 班级 / 学生）。
--
-- **与 `assessment_target` 不可混用**：这一张是「这场任务打算发给谁」，
-- 那一张是「实际发放到了谁」。任务创建之后新增的名册学生**不会自动进入**
-- 历史任务，必须通过「补发目标学生」显式加入 —— 补发写的是 `assessment_target`，
-- 不写这里。两者分开正是为了这一条：范围是设计，目标是事实。
--
-- `scope_type` 与 `user_scope.scope_type` 是同一套编码（§3 的口径写进界面的那条），
-- 但**读法不同**：任务范围说「按年级」，用户范围说「你负责的年级」。
-- 所以 `labels.ts` 里是两张表，仍归那唯一的映射层。
--
-- 四列（grade_id / class_id / student_id）按 `scope_type` 只有一列有值，
-- 其余为 NULL —— 与 `user_scope` 同一形状，边界靠 `is not None` 判，
-- **不能靠「NULL 等于 NULL」**：半填的行会因此授予一切。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 关怀档案的**过程事件**（一行 = 一次状态变更或一次关键动作）。
--
-- `student_care_case` 身上只有「现在是什么状态」，这一张记的是**怎么走到这个状态的** ——
-- 开档、复核、跟进、家庭回访、复测、关闭、重开各是一次事件。
-- 「关闭档案不进删除历史记录」在这里有了具体的形状：档案行还在，
-- 而这一条链路把过程完整地留着。
--
-- `from_status` / `to_status` 是变更的两端（`reason` 是人写的原因）；
-- `confirmed_facts` 是**当时已确认的事实**，与 `reason`（人的判断）分开 ——
-- 复盘的判据是前者，而后者会随记忆变化。
--
-- 与 `audit_log` 的分工：审计回答「谁在什么时候做了什么」，
-- 这一张回答「这个学生的关怀过程经过哪几步」。同一件事会在两边各留一行，
-- 那不是重复 —— 一边按操作查，一边按人查。
--
-- 复合外键 `(care_case_id, student_id)` 指着 `student_care_case (id, student_id)`：
-- 它保证事件**不会挂到别的学生的档案上**。单列的两条边（`ibfk_1` / `ibfk_2`）
-- 各自保证「档案在」「学生在」，合起来才保证「是这名学生的这份档案」。
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `professional_report` (
  `id` int NOT NULL AUTO_INCREMENT,
  `report_no` varchar(64) NOT NULL,
  `school_id` int NOT NULL,
  `report_type` varchar(32) NOT NULL,
  `title` varchar(255) NOT NULL,
  `status` varchar(32) NOT NULL,
  `task_scope_json` json NOT NULL,
  `analysis_mode` varchar(32) NOT NULL,
  `statistics_snapshot_json` json NOT NULL,
  `current_version` int NOT NULL,
  `created_by` int NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  `updated_by` int NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT (now()),
  `published_by` int DEFAULT NULL,
  `published_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_professional_report_no` (`report_no`),
  KEY `ix_professional_report_school_status` (`school_id`,`status`),
  KEY `professional_report_fk_created_by` (`created_by`),
  KEY `professional_report_fk_updated_by` (`updated_by`),
  KEY `professional_report_fk_published_by` (`published_by`),
  CONSTRAINT `professional_report_fk_school` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `professional_report_fk_created_by` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `professional_report_fk_updated_by` FOREIGN KEY (`updated_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `professional_report_fk_published_by` FOREIGN KEY (`published_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `professional_report_version` (
  `id` int NOT NULL AUTO_INCREMENT,
  `report_id` int NOT NULL,
  `version_no` int NOT NULL,
  `overall_summary` text NOT NULL,
  `dimension_interpretation` text NOT NULL,
  `sample_validity_note` text NOT NULL,
  `support_plan` text NOT NULL,
  `statistics_snapshot_json` json NOT NULL,
  `created_by` int NOT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_professional_report_version` (`report_id`,`version_no`),
  KEY `professional_report_version_fk_created_by` (`created_by`),
  CONSTRAINT `professional_report_version_fk_report` FOREIGN KEY (`report_id`) REFERENCES `professional_report` (`id`),
  CONSTRAINT `professional_report_version_fk_created_by` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- =============================================================================
-- 环上的外键，以及老表指向新表的那些
-- =============================================================================
--
-- 上面每一张表的外键**都写在表体里**，只有下面这几条例外。两种原因：
--
--   ① **环**：`assessment_import_row` 与 `assessment_external_result` 互相指着
--      （前者 `external_result_record_id → 后者.id`，后者 `row_id → 前者.id`），
--      两条 `CREATE TABLE` 谁先谁后都建不出来；
--   ② **方向相反**：V1.0 的老表（`assessment_session` / `assessment_target`）
--      指着新建的导入批次表，而老表在文件前面。
--
-- 两条出路都只有「等表建完再 ALTER」。它排在**所有 `CREATE TABLE` 之后**，
-- 所以下面每一条 `REFERENCES` 的目标都必然已经存在 —— 「父先子后」那条判据
-- 对这一段豁免（`test_tables_are_created_parent_before_child` 里记着这件事）。
--
-- **这一段是必需的，不是排版**：少了它，这份文件建出来的库会缺这几条外键，
-- 而缺一条外键**不会有任何症状** —— 直到某天删父行时它没有拦住、
-- 或者 `ensure_schema` 比对时才发现对不上。

ALTER TABLE `assessment_target`
  ADD CONSTRAINT `assessment_target_fk_effective_external` FOREIGN KEY (`effective_external_result_id`) REFERENCES `assessment_external_result` (`id`);

ALTER TABLE `assessment_target`
  ADD CONSTRAINT `assessment_target_fk_effective_external_student` FOREIGN KEY (`effective_external_result_id`, `student_id`) REFERENCES `assessment_external_result` (`id`, `student_id`);

ALTER TABLE `assessment_target`
  ADD CONSTRAINT `assessment_target_fk_effective_session` FOREIGN KEY (`effective_session_id`) REFERENCES `assessment_session` (`id`);

ALTER TABLE `assessment_target`
  ADD CONSTRAINT `assessment_target_fk_effective_session_student` FOREIGN KEY (`effective_session_id`, `student_id`) REFERENCES `assessment_session` (`id`, `student_id`);

ALTER TABLE `assessment_target`
  ADD CONSTRAINT `assessment_target_fk_supplement_batch` FOREIGN KEY (`supplemented_from_batch_id`) REFERENCES `assessment_import_batch` (`id`);

ALTER TABLE `assessment_session`
  ADD CONSTRAINT `assessment_session_ibfk_4` FOREIGN KEY (`import_batch_id`) REFERENCES `assessment_import_batch` (`id`);

ALTER TABLE `assessment_import_row`
  ADD CONSTRAINT `assessment_import_row_fk_external_record` FOREIGN KEY (`external_result_record_id`) REFERENCES `assessment_external_result` (`id`);


-- 36 张业务表到此为止（另有一张 Alembic 自己的 alembic_version，不由这里建）。
--
-- 建完之后必须再跑一次：
--   python -m app.db.ensure_schema      # 校对 + 补 alembic_version
--   python -m alembic upgrade head      # 或直接这一条，由它自己记账
--
-- 然后是基础数据（**admin 的初始密码是 123456**，出路是安装目录里的
-- 「重置管理员密码.bat」）：
--   python -m app.db.seed
-- =============================================================================
