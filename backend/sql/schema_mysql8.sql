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
--    而**没有任何文本检查看得见它** —— 内存 sqlite 不检查外键（CLAUDE.md 缺口 3），
--    仓库里那套测试全绿。所以「在真库上开着外键检查整份跑一遍」不是一道工序，
--    是这个文件唯一的正确性证据。守卫按 `Base.metadata` 的外键图把次序重推一遍。
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
  KEY `school_id` (`school_id`),
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
  KEY `school_id` (`school_id`),
  KEY `grade_id` (`grade_id`),
  CONSTRAINT `class_group_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `class_group_ibfk_2` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`)
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
  KEY `grade_id` (`grade_id`),
  KEY `class_id` (`class_id`),
  CONSTRAINT `student_ibfk_1` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `student_ibfk_2` FOREIGN KEY (`grade_id`) REFERENCES `grade` (`id`),
  CONSTRAINT `student_ibfk_3` FOREIGN KEY (`class_id`) REFERENCES `class_group` (`id`)
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
  KEY `scale_id` (`scale_id`),
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
  KEY `scale_id` (`scale_id`),
  KEY `school_id` (`school_id`),
  KEY `created_by` (`created_by`),
  CONSTRAINT `assessment_task_ibfk_1` FOREIGN KEY (`scale_id`) REFERENCES `assessment_scale` (`id`),
  CONSTRAINT `assessment_task_ibfk_2` FOREIGN KEY (`school_id`) REFERENCES `school` (`id`),
  CONSTRAINT `assessment_task_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`)
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
CREATE TABLE `assessment_target` (
  `id` int NOT NULL AUTO_INCREMENT,
  `task_id` int NOT NULL,
  `student_id` int NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT 'NOT_STARTED',
  `assigned_at` datetime NOT NULL DEFAULT (now()),
  `completed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_target_task_student` (`task_id`,`student_id`),
  KEY `student_id` (`student_id`),
  CONSTRAINT `assessment_target_ibfk_1` FOREIGN KEY (`task_id`) REFERENCES `assessment_task` (`id`),
  CONSTRAINT `assessment_target_ibfk_2` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 测评会话：一名学生的一张卷子。（**第①层：原始答题事实**）
--
-- **`uq_session_task_student(task_id, student_id)` 是"一场任务一人一卷"的物理保证。**
-- `task_id` 可空 —— NULL 在 MySQL 唯一索引里**不参与去重**，所以脱离任务单独开的
-- 会话不互相冲突。
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
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_session_task_student` (`task_id`,`student_id`),
  KEY `student_id` (`student_id`),
  KEY `scale_id` (`scale_id`),
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
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `session_id` (`session_id`),
  KEY `reviewed_by` (`reviewed_by`),
  CONSTRAINT `risk_event_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `risk_event_ibfk_2` FOREIGN KEY (`session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `risk_event_ibfk_3` FOREIGN KEY (`reviewed_by`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 关怀档案。
--
-- **一个学生有多条档案是正常状态，不是脏数据。** 上学期关掉的是一次完整的关怀过程，
-- 这学期再出状况是一条新的过程 —— 秋季关档、春季再关一次是学校每年都会遇到的时序。
--
-- 这里**曾经有一个 `UniqueConstraint(student_id, status)`**（迁移 0012 删掉），
-- 它把「同一时间只有一条在办」当成了数据库约束。约束还在时第二次关闭撞 UNIQUE、
-- `db.flush()` 抛 IntegrityError，用户拿到 500。现在「只有一条在办」由**写入侧**保证
-- （`assessment_service.open_or_reuse_care_case` 先找非 CLOSED 的那条，找不到才新建）。
--
-- 于是 `student_id` 上是**普通索引 `ix_student_care_case_student_id`，不是唯一索引**。
-- 这个索引有第二个身份：它兼作 `student_id` 外键的索引。删它之前必须先建它
-- —— MySQL 会以 **1553** 拒绝删一条外键正在用的索引，而**这条错误只有真库会报**
-- （内存 sqlite 不跑 Alembic、不检查外键）。
--
-- 三个列表对 CLOSED 的取舍各不相同，各自的理由写在该处：
--   get_care_case             不过滤（关闭之后这一页还得能看）
--   list_care_cases           不过滤（它有「已关闭」页签）
--   leader_progress           过滤  （那一页叫「重点进展」，CLOSED 没有进展可看）
-- **这不违反「关闭档案不得删除历史记录」** —— 那条说的是别删行，不是每条列表都得列出来。
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
  PRIMARY KEY (`id`),
  KEY `owner_id` (`owner_id`),
  KEY `ix_student_care_case_student_id` (`student_id`),
  CONSTRAINT `student_care_case_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `student_care_case_ibfk_2` FOREIGN KEY (`owner_id`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 七、专业工作事实（第④层）
-- =============================================================================
--
-- 这四张表有一个共同形状：**每一张都有一个 `confirmed_facts TEXT NOT NULL`**。
-- 那是这一层的语义要求 —— 每一件事都必须写清「确认了什么事实」。
-- `NOT NULL` 是有意的：一条没有内容的跟进记录不是记录。
--
-- 它们都挂在 `student_id` 上，**不挂在档案上** —— 一名学生的历史跟进记录
-- 在档案关闭之后仍然属于他。所以这四张表都不受「关闭」影响。

-- 人工复核。**不得修改原始答卷** —— 它只写自己的判断与下一步。
-- 一条复核写完之后，对应的 risk_event 从 PENDING 转走，不再是待办。
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
  PRIMARY KEY (`id`),
  KEY `risk_event_id` (`risk_event_id`),
  KEY `reviewer_id` (`reviewer_id`),
  CONSTRAINT `manual_review_ibfk_1` FOREIGN KEY (`risk_event_id`) REFERENCES `risk_event` (`id`),
  CONSTRAINT `manual_review_ibfk_2` FOREIGN KEY (`reviewer_id`) REFERENCES `user_account` (`id`)
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
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `operator_id` (`operator_id`),
  CONSTRAINT `follow_up_record_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `follow_up_record_ibfk_2` FOREIGN KEY (`operator_id`) REFERENCES `user_account` (`id`)
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
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `operator_id` (`operator_id`),
  CONSTRAINT `family_contact_record_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `family_contact_record_ibfk_2` FOREIGN KEY (`operator_id`) REFERENCES `user_account` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 复测计划。**两个外键指向同一张表** `assessment_session`：
--   source_session_id    因哪一场而计划复测（可空 —— 手工建的复测没有来源）
--   completed_session_id 哪一场完成了它（可空 —— 还没做）
-- 这两条让"复测闭环"可以被验证：completed_session_id 非空且那一场已交卷时，
-- 这条计划真的被兑现了。
--
-- 计划一旦建立，它指向的那一场（source_session_id）**不能被删除** ——
-- 这是外部测评导入"就地改写而不删了重建"的四个理由之一。
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
  PRIMARY KEY (`id`),
  KEY `student_id` (`student_id`),
  KEY `source_session_id` (`source_session_id`),
  KEY `created_by` (`created_by`),
  KEY `completed_session_id` (`completed_session_id`),
  CONSTRAINT `retest_plan_ibfk_1` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`),
  CONSTRAINT `retest_plan_ibfk_2` FOREIGN KEY (`source_session_id`) REFERENCES `assessment_session` (`id`),
  CONSTRAINT `retest_plan_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `user_account` (`id`),
  CONSTRAINT `retest_plan_ibfk_4` FOREIGN KEY (`completed_session_id`) REFERENCES `assessment_session` (`id`)
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
  PRIMARY KEY (`id`),
  KEY `actor_user_id` (`actor_user_id`),
  KEY `ix_audit_log_student_id` (`student_id`),
  CONSTRAINT `audit_log_ibfk_1` FOREIGN KEY (`actor_user_id`) REFERENCES `user_account` (`id`),
  CONSTRAINT `fk_audit_log_student_id` FOREIGN KEY (`student_id`) REFERENCES `student` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;


-- =============================================================================
-- 24 张业务表到此为止（另有一张 Alembic 自己的 alembic_version，不由这里建）。
--
-- 建完之后必须再跑一次：
--   python -m app.db.ensure_schema      # 校对 + 补 alembic_version
--   python -m alembic upgrade head      # 或直接这一条，由它自己记账
--
-- 然后是基础数据（**admin 的初始密码是 123456**，出路是安装目录里的
-- 「重置管理员密码.bat」）：
--   python -m app.db.seed
-- =============================================================================
