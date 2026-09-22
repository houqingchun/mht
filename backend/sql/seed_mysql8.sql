-- ===========================================================================
-- 心晴：数据库**基础数据**脚本（只有管理员账号与量表，不含任何演示数据）
-- ===========================================================================
--
-- 版本：V1.1（1.1.4）
-- 生成：`make db-seed-sql`（`deploy/build_seed_sql.py`）。**不要手改这个文件**——
--       改了下次重跑就没了，而且 `backend/app/tests/test_seed_sql.py` 会红。
--
-- ---------------------------------------------------------------------------
-- ★ 这份文件里只有 INSERT，一句 DDL 都没有：它不建库、也不建表。
-- ---------------------------------------------------------------------------
--
-- 表结构归 `alembic upgrade head`，或者包里的 `backend\sql\schema_mysql8.sql`。所以
-- 顺序是：
--
--     mysql … < schema_mysql8.sql     ← 建表（或者在有 Python 的机器上跑 alembic）
--     mysql … < seed_mysql8.sql       ← 本文件，写基础数据
--
-- **本文件不写 `alembic_version`。** 那张表是 Alembic 自己的版本记录，只由它创建；
-- 在一份 `schema_mysql8.sql` 建出来的库上它还不存在，在一份迁移建出来的库上它已经
-- 有一行——两种情况下往里面 INSERT 都是一个错（1146 / 1062），而它们看起来都像
-- 「这个文件坏了」。补那一行的正确做法是在安装目录里跑：
--
--     runtime\venv\Scripts\python.exe -m app.db.ensure_schema
--
-- 它认得出「表建好了但没有版本戳」，补上之后再 `alembic upgrade head` 就无事可做。
--
-- ---------------------------------------------------------------------------
-- 它写了哪些表（这一块由生成器从**实际数据**里数出来，不是手写的）
-- ---------------------------------------------------------------------------
--
--   school                      1 行   一所学校：admin 的 user_scope 要指它，学生导入链路也按 code 取它
--   user_account                1 行   管理员账号（account = admin，初始口令见文件头「管理员账号」一节）
--   assessment_scale            1 行   已发布的 MHT 量表版本：新建测评任务时选的就是它
--   scale_question            100 行   题库：题干来自 data/mht_scale.json（与题库导入读的是同一个文件）
--   scale_rule                  1 行   评分规则：总分 / 维度分段与效度阈值都随这一行走（CLAUDE.md §6）
--   user_scope                  1 行   admin 的范围行：§9 里没有范围行的账号，每个列表都是空的
--
-- 其余 28 张业务表一行都不写：名册、员工账号、测评任务、
-- 一切测评与关怀记录全空。开通之后的第一件事是「组织学生 → 学生信息导入」
-- （学生账号跟着名册一起生成，不在「账号与权限」里建），不是往这个文件里加行。
--
-- `system_setting` 与 `role_permission` 也一行都不写：全新库上它们本来就是 0 行
-- （配置回退 `settings_service.DEFAULTS`、权限回退 `CAPABILITY_DEFAULTS`，
-- CLAUDE.md §4 / §5 的 fail-safe）。一个库上它们有几行，取决于那个库的操作员
-- 配过什么、管理员保存过几次权限矩阵。
--
-- 全部 6 张表、105 行，止于 `python -m app.db.reset_to_baseline --yes`
-- 的终点——也就是说：跑完 `python -m app.db.seed` 再做一遍清理之后**剩下来的那些行**。
-- 这个终点不在这里定义，由那个脚本独家保证，所以它变了这个文件自动跟上。
--
-- ---------------------------------------------------------------------------
-- 管理员账号
-- ---------------------------------------------------------------------------
--
--     account = 'admin'      初始口令 = 123456
--     不强制下次登录改密（`must_change_password = false`）
--
-- 改密码的出路有两条：在界面上自己改（右上角「修改密码」），或者在安装目录里双击
-- 「重置管理员密码.bat」。**重装一遍不会重置它**——升级只更新程序文件与表结构，
-- 凡是你已经配好、正在用的东西它都不碰。
--
-- ---------------------------------------------------------------------------
-- 三处「每次都会变」的值，在这里是固定下来的
-- ---------------------------------------------------------------------------
--
-- 这一节是**必须读**的：下面这些值不是真实值，是为了让这份文件可复现而固定的。
--
--   user_account.password_hash         `hash_password()` 用的是 `bcrypt.gensalt()`，盐是随机的，所以每次跑 seed 出来的哈希都不一样。不固定的话这份文件就没有身份：每生成一次 git diff 都变，而「它是不是过期的」也判不出来。值验过：verify_password('123456', <它>) 为真。
--   assessment_scale.published_at      `seed.py` 写的是 datetime.now(UTC)，跑一次变一次。固定成常量——**它不是真实发布时间**，只是这份交付文件里那条量表版本的发布时间。这一列在界面上有读者（量表页的「发布时间」），所以文件头要把它说明白。
--
--   （各表的 created_at / updated_at 另属一类：它们根本没有出现在 INSERT 里，
--     由你那个库的 `DEFAULT (now())` 填。语义上这样才对——那两列回答的是
--     「这一行什么时候落进**这个**库」，而不是「它当初是在谁的机器上生成的」。）
--
-- ---------------------------------------------------------------------------
-- 怎么跑
-- ---------------------------------------------------------------------------
--
--     mysql --default-character-set=utf8mb4 -h 127.0.0.1 -u root -p 你的库名 \
--           < seed_mysql8.sql
--
-- `--default-character-set=utf8mb4` 是必须的：这个文件里有中文（校名、题干、
-- 规则里的说明），客户端按别的编码读会得到一串乱码，而它**看起来像导入成功**。
--
-- 整个过程是一个事务：中间任何一条报错，客户端就会停下并断开，事务随之回滚。
-- **不要**加 `--force`——它会跳过错误继续跑，那正是这里最不能要的行为。
--
-- **重复执行会报错，不会重复写。** 用的是普通 `INSERT`，不是 `INSERT IGNORE`、
-- 也不是 `REPLACE`：第二遍会在第一张表上撞 `ERROR 1062 Duplicate entry`。这是有意的
-- ——静默跳过会让人以为「跑过了」，而 `REPLACE` 会删掉旧行再插一行新的，把一串
-- 外键指着的主键 id 换掉。报出来的**行号**就在这个文件里（每行一条 INSERT）。
--
-- 想要一份「清空重来」的脚本，那是另一个文件：`backend\sql\reset_to_baseline.sql`。
--
-- 跑完之后应当能：admin 登录 → 建测评任务时选得到 MHT 量表 → 组织学生里导入名册。
-- ===========================================================================

START TRANSACTION;


-- ---------------------------------------------------------------------------
-- school · 1 行 —— 一所学校：admin 的 user_scope 要指它，学生导入链路也按 code 取它
-- ---------------------------------------------------------------------------
INSERT INTO school (id, code, name, status) VALUES (1, 'QH', '青禾实验学校', 'ACTIVE');


-- ---------------------------------------------------------------------------
-- user_account · 1 行 —— 管理员账号（account = admin，初始口令见文件头「管理员账号」一节）
-- ---------------------------------------------------------------------------
INSERT INTO user_account (id, account, account_type, display_name, password_hash, role_code, must_change_password, failed_attempts, locked_until, active, last_login_at) VALUES (4, 'admin', 'ADMIN_USERNAME', '系统管理员', '$2b$12$gdRtQy2WVTKXQuO3siSuHerKmjipd8law6F4GoJv6yNueP1HRlWNe', 'ADMIN', false, 0, NULL, true, NULL);


-- ---------------------------------------------------------------------------
-- assessment_scale · 1 行 —— 已发布的 MHT 量表版本：新建测评任务时选的就是它
-- ---------------------------------------------------------------------------
INSERT INTO assessment_scale (id, code, name, version, status, published_at, created_by) VALUES (1, 'MHT', '中学生心理健康测验', 'MHT-1.1.0', 'PUBLISHED', '2026-09-20 00:00:00', NULL);


-- ---------------------------------------------------------------------------
-- scale_question · 100 行 —— 题库：题干来自 data/mht_scale.json（与题库导入读的是同一个文件）
-- ---------------------------------------------------------------------------
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (1, 1, 1, '你晚上要睡觉时，是否总想着明天的功课？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (2, 1, 2, '老师向全班提问时，你是否会觉得是在问自己而感到不安？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (3, 1, 3, '你是否一听说“要考试”心里就紧张。', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (4, 1, 4, '你考试成绩不好时，心里是否感到很不快？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (5, 1, 5, '你学习成绩不好时，是否总是提心吊胆？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (6, 1, 6, '你考试时，想不起原先掌握的知识时，是否会感到紧张不安？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (7, 1, 7, '你考试后，在没有知道成绩之前，是否总是放心不下？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (8, 1, 8, '你是否一遇到考试，就担心会考坏？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (9, 1, 9, '你是否希望每次考试都能顺利？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (10, 1, 10, '你在没有完成任务之前，是否总担心完不成任务？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (11, 1, 11, '你当着大家面朗读课文时，是否总是怕读错？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (12, 1, 12, '你是否认为学校里得到的学习成绩总是不大可靠？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (13, 1, 13, '你是否认为你比别人更担心学习？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (14, 1, 14, '你是否做过考试考坏了的梦？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (15, 1, 15, '你是否做过学习成绩不好时，受到爸爸妈妈或老师训斥的梦？', 'LEARNING_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (16, 1, 16, '你是否经常觉得有同学在背后说你的坏话？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (17, 1, 17, '你受到父母批评后，是否总是想不开，放在心上？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (18, 1, 18, '你在游戏或与别人的竞争中输给了对方，是否就不想再干了？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (19, 1, 19, '人家在背后议论你，你是否感到讨厌？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (20, 1, 20, '你在大家面前或被老师提问时，是否会脸红？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (21, 1, 21, '你是否很担心叫你担任班级工作？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (22, 1, 22, '你是否总是觉得好像有人在注意你？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (23, 1, 23, '你在工作或学习时，如果有人在注意你，你心里是否会紧张？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (24, 1, 24, '你受到批评时，心情是否不愉快？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (25, 1, 25, '你受到老师批评时，心里是否总是不安？', 'INTERPERSONAL_ANXIETY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (26, 1, 26, '同学们在笑时，你是否也不大会笑？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (27, 1, 27, '你是否觉得到同学家里去玩时不如在自己家里玩？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (28, 1, 28, '你和大家在一起时，是否也觉得自己是孤单的一个人？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (29, 1, 29, '你是否觉得和同学一起玩，不如自己一个人玩？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (30, 1, 30, '同学们在交谈时，你是否不想加入？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (31, 1, 31, '你和大家在一起时，是否觉得自己是多余的人？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (32, 1, 32, '你是否讨厌参加运动会和文艺演出？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (33, 1, 33, '你的朋友是否很少？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (34, 1, 34, '你是否不喜欢同别人谈话？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (35, 1, 35, '在人多的地方，你是否觉得很怕？', 'LONELINESS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (36, 1, 36, '你在参加排球、篮球等集体比赛输了时，心里是否一直认为自己没做好？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (37, 1, 37, '你受到批评后，是否总认为是自己不好？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (38, 1, 38, '别人笑你的时候，你是否会认为是自己做错了什么事？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (39, 1, 39, '你学习成绩不好时，是否总是认为是自己不用功的缘故？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (40, 1, 40, '你做事失败的时候，是否总是认为是自己的责任？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (41, 1, 41, '大家受到责备时，你是否认为主要是自己的过错？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (42, 1, 42, '你参加乒乓球、羽毛球、广播操等体育比赛时，是否一出错就特别留神？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (43, 1, 43, '碰到为难的事情时，你是否认为自己难以应付？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (44, 1, 44, '你是否有时会后悔：“那件事不做就好了”？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (45, 1, 45, '你和同学吵架以后，是否总是认为是自己的错？', 'SELF_BLAME', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (46, 1, 46, '你心里是否总想为班级做点好事？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (47, 1, 47, '你学习的时候，思想是否经常开小差？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (48, 1, 48, '你把东西借给别人时，是否担心别人会把东西弄坏？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (49, 1, 49, '碰到不顺利的事情时，你心里是否很烦躁？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (50, 1, 50, '你是否非常担心家里有人生病或死去？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (51, 1, 51, '你是否在梦里见到过死去的人？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (52, 1, 52, '你对收音机和汽车的声音是否特别敏感？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (53, 1, 53, '你心里是否总觉得好像有什么事没有做好？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (54, 1, 54, '你是否总担心会发生什么意外的事？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (55, 1, 55, '你在决定要做什么事时，是否总是犹豫不决？', 'SENSITIVITY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (56, 1, 56, '你手上是否经常出汗？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (57, 1, 57, '你害羞时是否会脸红？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (58, 1, 58, '你是否经常头痛？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (59, 1, 59, '你被老师提问时，心里是否总是很紧张？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (60, 1, 60, '你没有参加运动，心脏是否经常扑腾扑腾地跳？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (61, 1, 61, '你是否很容易疲劳？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (62, 1, 62, '你是否很不愿吃药？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (63, 1, 63, '夜里你是否很难入睡？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (64, 1, 64, '你是否总觉得身体好像有什么毛病？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (65, 1, 65, '你是否经常认为自己的体型和面孔比别人难看？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (66, 1, 66, '你是否经常觉得肠胃不好？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (67, 1, 67, '你是否经常咬指甲？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (68, 1, 68, '你是否经常舔手指头？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (69, 1, 69, '你是否经常感到呼吸困难？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (70, 1, 70, '你去厕所的次数是否比别人多？', 'PHYSICAL_SYMPTOMS', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (71, 1, 71, '你是否很怕到高的地方去？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (72, 1, 72, '你是否害怕很多东西？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (73, 1, 73, '你是否经常做噩梦？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (74, 1, 74, '你胆子是否很小？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (75, 1, 75, '夜里，你是否很怕一个人在房间里睡觉？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (76, 1, 76, '你乘车穿过隧道或路过高桥时，是否很怕？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (77, 1, 77, '你是否喜欢整夜开着灯睡觉？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (78, 1, 78, '你听到打雷声是否非常害怕？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (79, 1, 79, '你是否非常害怕黑暗？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (80, 1, 80, '你是否经常感到后面有人跟着你？', 'PHOBIC_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (81, 1, 81, '你是否经常生气？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (82, 1, 82, '你是否不想得到好的成绩？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (83, 1, 83, '你是否经常会突然想哭？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (84, 1, 84, '你以前是否说过谎话？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (85, 1, 85, '最近两周，你会不会觉得活着很辛苦，希望一切就此结束？', 'IMPULSIVE_TENDENCY', false, true, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (86, 1, 86, '你是否一次也没有失约过？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (87, 1, 87, '你是否经常想大声喊叫？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (88, 1, 88, '你是否能保密别人不让说的事？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (89, 1, 89, '你有时是否想过自己一个人到远的地方去？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (90, 1, 90, '你是否总是很有礼貌？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (91, 1, 91, '你被人说了坏话，是否想立即采取报复行动？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (92, 1, 92, '老师或父母说的话，你是否都照办？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (93, 1, 93, '你心里不开心，是否会乱丢、乱砸东西？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (94, 1, 94, '你是否发过怒？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (95, 1, 95, '你想要的东西，是否就一定要拿到手？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (96, 1, 96, '你不喜欢的功课老师提前下课，你是否会感到特别高兴？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (97, 1, 97, '最近两周，遇到不顺心时，我很想立刻把心里的不快宣泄出来。', 'IMPULSIVE_TENDENCY', false, true, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (98, 1, 98, '你是否无论对谁都很亲热？', NULL, true, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (99, 1, 99, '你是否会经常急躁得坐立不安？', 'IMPULSIVE_TENDENCY', false, false, 'ACTIVE');
INSERT INTO scale_question (id, scale_id, question_no, question_text, dimension_code, is_validity_question, is_key_question, status) VALUES (100, 1, 100, '对不认识的人，你是否会都喜欢？', NULL, true, false, 'ACTIVE');


-- ---------------------------------------------------------------------------
-- scale_rule · 1 行 —— 评分规则：总分 / 维度分段与效度阈值都随这一行走（CLAUDE.md §6）
-- ---------------------------------------------------------------------------
INSERT INTO scale_rule (id, scale_id, rule_version, rule_type, config_json, status) VALUES (1, 1, 'MHT-RULE-1.1.1', 'MHT_SCORING', '{"no_value": "NO", "yes_value": "YES", "total_levels": [{"max": 55, "min": 0, "code": "GENERAL_RANGE"}, {"max": 64, "min": 56, "code": "NEEDS_ATTENTION"}, {"max": 100, "min": 65, "code": "KEY_ATTENTION"}], "key_questions": [85, 97], "question_count": 100, "interpretations": {"LOW": "该维度当前处于一般范围。", "HIGH": "该维度得分较高，建议由授权心理老师结合事实进一步了解。", "MEDIUM": "该维度提示可能存在需要进一步了解的倾向。"}, "dimension_levels": [{"max": 3, "min": 0, "code": "LOW"}, {"max": 7, "min": 4, "code": "MEDIUM"}, {"max": 15, "min": 8, "code": "HIGH"}], "validity_questions": [82, 84, 86, 88, 90, 92, 94, 96, 98, 100], "validity_retest_threshold": 7, "todo_business_confirmation": ["VALID 与 QUESTIONABLE 的效度阈值尚未冻结；当前仅实现 validity_score >= 7 为 RETEST_RECOMMENDED。"]}', 'ACTIVE');


-- ---------------------------------------------------------------------------
-- user_scope · 1 行 —— admin 的范围行：§9 里没有范围行的账号，每个列表都是空的
-- ---------------------------------------------------------------------------
INSERT INTO user_scope (id, user_id, scope_type, school_id, grade_id, class_id, student_id) VALUES (4, 4, 'SCHOOL', 1, NULL, NULL, NULL);


COMMIT;

-- ---------------------------------------------------------------------------
-- 跑完看一眼
-- ---------------------------------------------------------------------------
--
-- 预期：上面那几行有数（校名 / 题干 / 规则各一条起），其余全 0。
-- 这张表里**没有 `alembic_version`**：在一份按 `schema_mysql8.sql` 建出来的库上
-- 它还不存在，一条 `UNION ALL` 引到它就会 1146——而这条查询排在 COMMIT **之后**，
-- 那个错会带着「数据已经写进去了」一起出现。补版本戳的是 `app.db.ensure_schema`。
--
SELECT 'school' AS 表, COUNT(*) AS 行数 FROM school
UNION ALL SELECT 'user_account' AS 表, COUNT(*) AS 行数 FROM user_account
UNION ALL SELECT 'assessment_scale' AS 表, COUNT(*) AS 行数 FROM assessment_scale
UNION ALL SELECT 'auth_session' AS 表, COUNT(*) AS 行数 FROM auth_session
UNION ALL SELECT 'export_job' AS 表, COUNT(*) AS 行数 FROM export_job
UNION ALL SELECT 'grade' AS 表, COUNT(*) AS 行数 FROM grade
UNION ALL SELECT 'role_permission' AS 表, COUNT(*) AS 行数 FROM role_permission
UNION ALL SELECT 'student_roster_import_batch' AS 表, COUNT(*) AS 行数 FROM student_roster_import_batch
UNION ALL SELECT 'system_setting' AS 表, COUNT(*) AS 行数 FROM system_setting
UNION ALL SELECT 'assessment_task' AS 表, COUNT(*) AS 行数 FROM assessment_task
UNION ALL SELECT 'class_group' AS 表, COUNT(*) AS 行数 FROM class_group
UNION ALL SELECT 'scale_question' AS 表, COUNT(*) AS 行数 FROM scale_question
UNION ALL SELECT 'scale_rule' AS 表, COUNT(*) AS 行数 FROM scale_rule
UNION ALL SELECT 'assessment_import_batch' AS 表, COUNT(*) AS 行数 FROM assessment_import_batch
UNION ALL SELECT 'student' AS 表, COUNT(*) AS 行数 FROM student
UNION ALL SELECT 'assessment_session' AS 表, COUNT(*) AS 行数 FROM assessment_session
UNION ALL SELECT 'assessment_task_scope' AS 表, COUNT(*) AS 行数 FROM assessment_task_scope
UNION ALL SELECT 'audit_log' AS 表, COUNT(*) AS 行数 FROM audit_log
UNION ALL SELECT 'student_care_case' AS 表, COUNT(*) AS 行数 FROM student_care_case
UNION ALL SELECT 'student_roster_import_row' AS 表, COUNT(*) AS 行数 FROM student_roster_import_row
UNION ALL SELECT 'user_scope' AS 表, COUNT(*) AS 行数 FROM user_scope
UNION ALL SELECT 'assessment_answer' AS 表, COUNT(*) AS 行数 FROM assessment_answer
UNION ALL SELECT 'assessment_import_row' AS 表, COUNT(*) AS 行数 FROM assessment_import_row
UNION ALL SELECT 'assessment_result' AS 表, COUNT(*) AS 行数 FROM assessment_result
UNION ALL SELECT 'care_case_event' AS 表, COUNT(*) AS 行数 FROM care_case_event
UNION ALL SELECT 'dimension_result' AS 表, COUNT(*) AS 行数 FROM dimension_result
UNION ALL SELECT 'family_contact_record' AS 表, COUNT(*) AS 行数 FROM family_contact_record
UNION ALL SELECT 'follow_up_record' AS 表, COUNT(*) AS 行数 FROM follow_up_record
UNION ALL SELECT 'retest_plan' AS 表, COUNT(*) AS 行数 FROM retest_plan
UNION ALL SELECT 'risk_event' AS 表, COUNT(*) AS 行数 FROM risk_event
UNION ALL SELECT 'assessment_external_result' AS 表, COUNT(*) AS 行数 FROM assessment_external_result
UNION ALL SELECT 'manual_review' AS 表, COUNT(*) AS 行数 FROM manual_review
UNION ALL SELECT 'student_age_change_log' AS 表, COUNT(*) AS 行数 FROM student_age_change_log
UNION ALL SELECT 'assessment_target' AS 表, COUNT(*) AS 行数 FROM assessment_target;
