/**
 * Display labels for backend codes.
 *
 * The API deliberately returns stable machine codes (KEY_ATTENTION,
 * PENDING_REVIEW, LEARNING_ANXIETY, …) so that wording can change without a
 * data migration. Translation to Chinese happens here and nowhere else — these
 * maps were previously duplicated across four views and had already drifted.
 */

export const LEVEL_LABELS: Record<string, string> = {
  KEY_ATTENTION: '重点关注',
  NEEDS_ATTENTION: '需要关注',
  GENERAL_RANGE: '一般观察'
}

/**
 * 枚举列在表格里的**中文序** —— 由上面那些映射表的**键序**生成，加新码时它自己跟上。
 *
 * 存在的理由：`DataTable` 的默认排序拿 `row[column.key]` 去 `localeCompare`，
 * 那排的是**编码的字母序**。于是点一下「关注等级」得到的是
 * 一般观察 → 重点关注 → 需要关注（`GENERAL_RANGE` / `KEY_ATTENTION` / `NEEDS_ATTENTION`
 * 的字母序），与任何一个读者心里的顺序都不像——而它看起来只是个「升序」，
 * 没人会怀疑这一列排错了。可排序的枚举列因此要显式声明 `order`（`DataTable` 的 `Column`）。
 *
 * **这几张表的键序是有意义的，不是随手写的**（从重到轻 / 按流程先后）。
 * 加新码时想清楚它排在哪一端，不要顺手追加到末尾。
 */
export const LEVEL_ORDER: readonly string[] = Object.keys(LEVEL_LABELS)
// Keys must match the backend's StudentCareCase.status values exactly
// (see app/services/care_service.py): PENDING_REVIEW / FOLLOWING / OBSERVING / CLOSED.
// 顺序 = 一份档案的**流程先后**（新开的在待复核，办完的已关闭），见 `CASE_STATUS_ORDER`。
export const STATUS_LABELS: Record<string, string> = {
  PENDING_REVIEW: '待复核',
  FOLLOWING: '跟进中',
  OBSERVING: '观察中',
  CLOSED: '已关闭'
}

export const CASE_STATUS_ORDER: readonly string[] = Object.keys(STATUS_LABELS)

/** MHT 八维度 —— 编码与后端 scale_engine.dimension_for_question 一致。 */
export const DIMENSION_LABELS: Record<string, string> = {
  LEARNING_ANXIETY: '学习焦虑',
  INTERPERSONAL_ANXIETY: '对人焦虑',
  LONELINESS: '孤独倾向',
  SELF_BLAME: '自责倾向',
  SENSITIVITY: '过敏倾向',
  PHYSICAL_SYMPTOMS: '身体症状',
  PHOBIC_TENDENCY: '恐怖倾向',
  IMPULSIVE_TENDENCY: '冲动倾向'
}

// 键序 = 账号表「角色」列的中文序（员工按职责，学生最后）。`ROLE_ORDER` 取它。
export const ROLE_LABELS: Record<string, string> = {
  counselor: '心理老师',
  leader: '德育领导',
  admin: '系统管理员',
  student: '学生'
}

export const ROLE_ORDER: readonly string[] = Object.keys(ROLE_LABELS)

/**
 * 测评任务状态 —— 后端 `AssessmentTask.status` 那一列 + 后端现算出来的两个码。
 *
 * `ACTIVE` / `DRAFT` / `PAUSED` / `CLOSED` 是那一列自己的词表；`NOT_STARTED` 不是它写进去的，
 * 是 `task_service.effective_task_status` 在读取时推出来的（还没到开始日期）。所以这张表
 * 同时服务两件事，改的时候两边都要认。
 */
// 键序 = 一场任务的生命周期（草稿 → 未开始 → 进行中 → 已暂停 → 已结束），
// `TASK_STATUS_ORDER` 取它。2026-09-17 由「ACTIVE 打头」调成这个顺序：
// 那一版是按重要性列的，而这里要服务排序。
export const TASK_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  NOT_STARTED: '未开始',
  ACTIVE: '进行中',
  PAUSED: '已暂停',
  CLOSED: '已结束'
}

export const TASK_STATUS_ORDER: readonly string[] = Object.keys(TASK_STATUS_LABELS)

// 键序 = 一个版本的发布流程（草稿 → 已发布 → 已归档），`SCALE_STATUS_ORDER` 取它。
export const SCALE_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  PUBLISHED: '已发布',
  ARCHIVED: '已归档'
}

export const SCALE_STATUS_ORDER: readonly string[] = Object.keys(SCALE_STATUS_LABELS)

export const TARGET_STATUS_LABELS: Record<string, string> = {
  COMPLETED: '已完成',
  IN_PROGRESS: '进行中',
  NOT_STARTED: '未开始'
}

/**
 * 参与状态 —— 后端 `AssessmentTarget.participation_disposition`。
 *
 * `REQUIRED` 打头（它是默认值、也是绝大多数行），后三个是 §18.10 那条算式里的
 * 三个减项，**顺序就是算式的顺序**（`PARTICIPATION_EXCLUDED_DISPOSITIONS` 按同一序
 * 排，后端 `expected_participation_predicate` 取的就是那三个）。
 *
 * 它们说的是「这名学生**应不应测**」，与 `targetStatusLabel`（他做完了没有）是两个
 * 问题：请假的学生那一场永远做不完，而不算进完成率的分母里。
 */
export const PARTICIPATION_DISPOSITION_LABELS: Record<string, string> = {
  REQUIRED: '应测',
  LEAVE: '请假',
  EXEMPT: '免测',
  EXCLUDED: '已排除'
}

/** 测评任务的对象范围 —— 后端 AssessmentTask.scope_type。 */
export const SCOPE_TYPE_LABELS: Record<string, string> = {
  SCHOOL: '全校',
  GRADE: '按年级',
  CLASS: '按班级',
  STUDENT: '指定学生'
}

/**
 * 效度状态 —— 后端 scale_engine.validity_status。
 *
 * QUESTIONABLE 目前引擎不产出（阈值尚未冻结，见 db/seed.py 的说明），但先把
 * 词条留在这里：阈值一冻结，缺的就是这一条。
 */
export const VALIDITY_LABELS: Record<string, string> = {
  VALID: '有效',
  QUESTIONABLE: '存疑',
  RETEST_RECOMMENDED: '建议重测'
}

/** 学生状态 —— 后端 Student.status。 */
export const STUDENT_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '在读',
  INACTIVE: '已离校'
}

export const STUDENT_STATUS_ORDER: readonly string[] = Object.keys(STUDENT_STATUS_LABELS)

/**
 * 性别 —— 后端 Student.gender。
 *
 * 学生导入同时接受 男/女 两种写法，但落库一律是编码（见
 * `student_import_service.parse_gender`），所以这里只需要认编码。
 * 未填是 NULL 而不是 'UNKNOWN'：名册早于这两列，学校也没填。
 */
export const GENDER_LABELS: Record<string, string> = {
  MALE: '男',
  FEMALE: '女'
}

/**
 * 测评来源 —— 后端 AssessmentSession.source / AssessmentTask.source。
 *
 * IMPORTED 是学校把**外部平台**的普查结果导入进来的（数据中心 → MHT测评记录导入），
 * IN_SYSTEM 是学生在本系统里作答。这不是内部细节：一份导入的记录没有本系统的作答过程，
 * 它的用时是那个平台自己报的数，而它与系统内的记录在分析里会一起被统计。
 */
// 键序 = 先自家、后外部（系统内作答 → 外部导入），`SOURCE_ORDER` 取它；
// 按编码排的话 IMPORTED 会跑到 IN_SYSTEM 前面——「外部导入的排在自家记录之上」
// 是字母序的偶然，不是谁的决定。
export const SOURCE_LABELS: Record<string, string> = {
  IN_SYSTEM: '系统内作答',
  IMPORTED: '外部导入'
}

export const SOURCE_ORDER: readonly string[] = Object.keys(SOURCE_LABELS)

/**
 * 目标学生的**来源** —— 后端 `AssessmentTarget.target_source`（V1.2 §18.2）。
 *
 * `TASK_SCOPE` 是建任务时按对象范围发放的那一批，`SUPPLEMENT` 是发放之后才进到
 * 名册上、由「补发」补进来的那一批。它与上面那张表长得像而**不是同一件事**：
 * `SOURCE_LABELS` 说「这份答卷是在哪测的」（系统内 / 外部平台），这一张说
 * 「这个人是怎么进到这场测评的名单里的」。
 *
 * 键序 = 先任务范围、后补发（先本来就在名单上的、后后来补进来的）——按编码排的话
 * `SUPPLEMENT` 会跑到 `TASK_SCOPE` 前面。**这张表现在没有 `*_ORDER`**：目标学生那一屏
 * 是普通 `<table>`，没有可排序列（`Column.order` 是给 `DataTable` 用的，§3 第四面）。
 * 哪天它变成可排序的，照 §3 加 `TARGET_SOURCE_ORDER = Object.keys(...)` 就行——
 * 键序已经是有意义的了。**先别加没人读的那一行**：§3 那条「表在 `labels.ts` 里而没人
 * 从那儿取，也算没接上」说的是同一件事的另一面。
 */
export const TARGET_SOURCE_LABELS: Record<string, string> = {
  TASK_SCOPE: '任务范围',
  SUPPLEMENT: '补发'
}

/**
 * 答卷的**评分状态** —— 后端 `AssessmentSession.calculation_status`（V1.2 §17(d)）。
 *
 * 它回答的是「算出来了没有」，与「算出来是什么」（`assessment_result` 那一行、
 * 关注等级那些列）是两个问题。此前这一列恒为 `PENDING`——**交完卷、分数就在屏幕上，
 * 而库里那一列说还没算**，所以「没算」与「算了」在界面上一模一样。
 *
 * `CALCULATION_FAILED` 是**一个需要人动手的状态**，不是一次失败的提示：答卷与交卷时间
 * 都原样留着（§1 的四层事实模型），心理老师在个案详情里点「重算」。
 *   PENDING 还没算过（历史行、以及两条校内路径回填之前留下的）
 *   CALCULATING 正在算（同一时刻只该有一个请求看得到它）
 *   CALCULATED 算出来了，结果在 `assessment_result` 里
 *   CALCULATION_FAILED 算失败了，失败原因在 `calculation_error` 里
 *
 * 键序 = 一次计算的流程先后（待计算 → 计算中 → 已计算 → 计算失败）。失败放在末端，
 * 与 `CASE_STATUS_ORDER` 把「已关闭」放末端同一个读法：它是这条流程的终点之一。
 * **这张表没有 `*_ORDER`**：读它的那一屏（个案详情的测评概览）是描述性的一行字，
 * 不是 `DataTable` 的可排序列，没有 `Column.order` 的读者（§3 第四面）。
 */
export const CALCULATION_STATUS_LABELS: Record<string, string> = {
  PENDING: '待计算',
  CALCULATING: '计算中',
  CALCULATED: '已计算',
  CALCULATION_FAILED: '计算失败'
}

/**
 * **测评日期是从哪来的** —— 后端 `AssessmentSession.tested_at_source`（V1.2 §17(d)）。
 *
 * 与 `SOURCE_LABELS` 是**两个轴**，别看混：`source` 说这份答卷是在哪测的
 * （系统内作答 / 外部平台），这一张说**那个日期是谁给的**。一份 `IN_SYSTEM` 的答卷
 * 它的 `tested_at` 就是学生交卷那一刻；一份 `IMPORTED` 的答卷那个日期来自文件里的
 * 「测评日期」列，而它未必等于文件被导入的那一天——一份上个月的普查这个月才导进来，
 * 趋势要按**上个月**排（`session_history_order` 排的就是这个字段的排序结果）。
 *
 * `PENDING_VERIFICATION` 是 0013 回填时给历史行的默认值：那些行谁也不知道真实测评日，
 * 用 `created_at` 填上是给它编一个看起来像事实的值（§21）。所以它是一个**诚实的
 * 「不知道」**，不是「还没核实」这件待办——界面上照实说。
 *
 * 它同样没有 `*_ORDER`（理由同上一条）。
 */
export const TESTED_AT_SOURCE_LABELS: Record<string, string> = {
  ONLINE_SUBMIT: '学生交卷时间',
  IMPORT_FILE: '导入文件的测评日期',
  PENDING_VERIFICATION: '待核实'
}

/**
 * MHT 测评记录导入的**冲突码** —— 后端 `assessment_import_row.conflict_code`。
 *
 * V1.2 第 4 期起它**落库了**（此前只在预览返回值里现算，令牌一过期就没了），
 * 所以现在它是一个能被反复读出来的事实：「这一行当时撞上了什么」。
 *
 * 三个码与 `MATCH_STATUS_LABELS` **不是一一对应**，别看混：那一张说的是「匹配到了
 * 什么程度」（`CONFLICT` 是其中一档），这一张说的是「撞上的具体是哪一类」。今天
 * 前两个与 `AGE_CONFLICT` / `DUPLICATE` 两档同进同出，第三个（系统内已有一份答卷）
 * 是新加的——它与 `CONFLICT` 同进同出。逐行明细上两个都渲染：药丸说结论，这一格
 * 说结论是从哪来的。
 *
 * 与 `ROSTER_CONFLICT_LABELS` 是**两个不同的东西**：那一张是名册导入的
 * （`STUDENT_NO_EXISTS`，问的是「要不要更新这名学生」），这一张问的是
 * 「要不要盖掉库里那一场测评」。英文码是同一种形状，中文各写各的。
 */
export const IMPORT_CONFLICT_LABELS: Record<string, string> = {
  AGE_MISMATCH: '年龄与名册不符',
  DUPLICATE: '本月已有一次导入',
  IN_SYSTEM_RESULT: '本场已有系统内提交的答卷'
}

/**
 * MHT 测评记录导入的**逐行匹配结论** —— 后端 `assessment_import_service.MATCH_*`（§18.4）。
 *
 * 八个码一个不多一个不少，外加初始值 `PENDING`（对齐阶段 DDL 的默认值，它不是匹配
 * 结论；批量插入之后立刻逐行匹配，所以正常情况下没有一行停在它上面）。留着它是为了
 * 让「有一行卡住了」**看得见**，而不是让它冒充一个结论。
 *
 * 键序是**从「能进」到「进不去」**，与后端那三张集合（`MATCH_STATUSES_IMPORTABLE` /
 * `NEEDING_RESOLUTION` / `UNIMPORTABLE`）排的是同一个次序：先已匹配，再要拍板的三档，
 * 最后任何选择都救不回来的四档。与 `ROSTER_ROW_STATUS_LABELS` 那张表的键序同理——
 * 界面上这一列今天没有可排序的表头（明细是普通 `<table>`），但键序是有意义的，
 * **加新码时想清楚它落在哪一档，不要顺手追加到末尾**。
 *
 * 中文读法的分寸：这一列是给操作员**照着做**的，所以每一句都写「接下来该干什么」的
 * 那个方向，而不是「系统判了什么」。`AMBIGUOUS` 尤其不能写成「匹配失败」——它是
 * 「名册上同名同班的几个人分不开」，出路是去核对名册，而这一期没有逐行选择的入口
 * （后端 `MATCH_STATUSES_UNIMPORTABLE` 那段注释记着为什么）。
 */
export const MATCH_STATUS_LABELS: Record<string, string> = {
  PENDING: '待匹配',
  MATCHED: '已匹配',
  AGE_CONFLICT: '年龄与名册不符',
  DUPLICATE: '本月已导过一次',
  CONFLICT: '本场已有答卷',
  INVALID_ROW: '这一行有错误',
  NOT_FOUND: '名册上没有',
  OUT_OF_SCOPE: '不在本场任务里',
  AMBIGUOUS: '同名分不开'
}

/**
 * 「**要拍板才能进**」的那三档 —— 后端 `MATCH_STATUSES_NEEDING_RESOLUTION`。
 *
 * 它决定逐行明细里哪些行长得出一组单选项。放在这张表旁边而不是在视图里就地写三个
 * 字面量：同一批码的第三种写法只会在这里悄悄漂移，而漂移的后果是**该拍板的行没有
 * 单选项**——那一行于是永远进不去，而屏幕上看起来一切正常（§3 那条「表在 `labels.ts`
 * 里而没人从那儿取也算没接上」的反面：这里有一张表，就该只有一个出处）。
 *
 * **另一档（`UNIMPORTABLE`：`INVALID_ROW` / `NOT_FOUND` / `OUT_OF_SCOPE` /
 * `AMBIGUOUS`）刻意不镜像**：那四档「任何选择都救不回来」，所以界面上不给它们任何
 * 可点的东西才是对的——给一个按了也没用的按钮，比不给按钮更糟。
 */
export const MATCH_STATUSES_NEEDING_RESOLUTION = ['AGE_CONFLICT', 'DUPLICATE', 'CONFLICT']

/**
 * 「这场任务还有谁缺着」那一屏的读法 —— 它比 `MATCH_STATUS_LABELS` **只多改一条**。
 *
 * 那一屏的行有两个来源（后端 `_unmatched_row_conditions` 的并集）：匹配不上（四档
 * 「进不去」），**或者**匹配上了而在提交时被放弃了。被放弃的那些行的 `match_status`
 * 是**要拍板的那三档**（`AGE_CONFLICT` / `DUPLICATE` / `CONFLICT`）——`_row_will_be_written`
 * 只对它们认「放弃」，`MATCHED` 落在最后那句 `return True` 上，整批选放弃也照写。
 * 所以**下面那条 `MATCHED` 覆盖今天造不出来**（2026-09-20 撞出来的，见 §29）。
 *
 * 留着它是因为它读的那句话仍然是对的：哪一天匹配上的行也能被放弃（或后端换了判据），
 * 照原表渲染就会在这一屏上印出一个绿色的「已匹配」，而它所在的表叫「没进得去」——
 * 两句话各自都对，摆在一起自相矛盾。**它是这个状态的读法，不是这个状态存在的证据**：
 * 别拿它推出「这一屏会有已匹配的行」（那正是第一版夹具写错的地方）。
 *
 * 其余八个码**逐字沿用**那张表（不是抄一遍）：两张表说的是同一件事的两种读者，
 * 抄一遍就会在某次改中文时只改一处。没有 `*_ORDER`——那一屏是普通 `<table>`，
 * 没有可排序的表头（§3 第四面）。
 */
export const UNMATCHED_REASON_LABELS: Record<string, string> = {
  ...MATCH_STATUS_LABELS,
  MATCHED: '已匹配但被放弃'
}

/**
 * 导入批次的**处置方式** —— 后端 `assessment_import_batch.resolution`。
 *
 * 取值是**小写的 `overwrite` / `skip`**（请求体里就是这两个字，`commit_batch` 原样
 * 存进那一列），外加 `NONE`（文件里没有冲突时后端写进去的那个值）。三种与「还没提交」
 * （`null`）必须长得不一样：`NONE` 是「问过了，没有要拍板的」，`null` 是「问都没问」。
 * 这与审计里 `resolution_label` 的 `None` / `未涉及（无冲突）` 是同一件事的两种粒度。
 */
export const ASSESSMENT_RESOLUTION_LABELS: Record<string, string> = {
  NONE: '无需处置',
  overwrite: '覆盖上次导入的那一场',
  skip: '放弃冲突行'
}

/**
 * 导入**形态** —— 后端 `assessment_import_batch.import_mode`（§18.9）。
 *
 * 与 `SOURCE_LABELS` 是**两个轴**，别看混：`source` 长在**会话**上，说的是「这份答卷
 * 是在哪测的」（系统内作答 / 外部平台）；`import_mode` 长在**批次**上，说的是「这份
 * 文件里装的是什么」——一百道题的逐题答案，还是只有总分与八个维度分的汇总。两种形态
 * 在 `source` 上都是 `IMPORTED`，所以只看 `source` 分不出它们。
 *
 * **这一列决定后果，所以界面上必须说出来**：汇总档没有逐题答案，于是它不产生答卷、
 * 不算「已测评」（`assessment_result` 上没有这一场）——按结果说话的页面（个案详情、
 * 重点学生、关注率）看不到它，而任务完成率会把它算成已完成。这不是缺陷，是「只有分数、
 * 没有答案」这件事的下场；但读的人不会自己想到，所以那一屏要写一句。
 *
 * **没有 `*_ORDER`**：批次历史是普通 `<table>`，没有可排序的表头（§3 第四面）。
 */
export const IMPORT_MODE_LABELS: Record<string, string> = {
  EXTERNAL_FULL_ANSWER: '逐题答卷',
  EXTERNAL_SUMMARY: '只有分数'
}

/**
 * **年龄冲突**的处置 —— 后端 `assessment_import_service.AGE_RESOLUTIONS`（§18.5）。
 *
 * 只有 `conflict_code === 'AGE_MISMATCH'` 的行才问这一栏，所以它不是每一行都有的。
 * `overwrite` 与整批的 `resolution` 是**同一个字**（后端
 * `AGE_RESOLUTION_OVERWRITE = RESOLUTION_OVERWRITE`），另两个是小写短语，不是笔误。
 *
 * 三个选项的分别落在**两列**上，而界面上容易只说得清一列：「名册上这个学生的年龄要不要
 * 改」与「这一场测评按哪个年龄记」（`assessment_session.age_at_test`）。外部文件 13 岁、
 * 名册 12 岁时：
 *
 * | 选项 | `student.age` | 这一场记的年龄 |
 * |---|---|---|
 * | `keep_roster` | 12（不动） | **12** |
 * | `overwrite` | 13（改写） | 13 |
 * | `session_only` | 12（不动） | **13** |
 *
 * `keep_roster` 与 `session_only` 的**唯一**差别就是这一场记哪个数——这是用户
 * 2026-09-19 的裁决（「无论……都必须保存外部年龄」那一条让步）。另一种读法（保留了
 * 名册年龄，就连这一场也按名册记）会让外部平台测出来的 13 岁在库里彻底消失，
 * 所以三个中文都要把这一列说出来，不能都只写「要不要改名册」。
 *
 * 键序 = 提示语里的次序（后端 `AGE_RESOLUTION_HINT` 逐字同序），也就是界面上三个选项
 * 的先后：推荐值排第一。**没有 `*_ORDER`**（这一栏是单选，不是可排序的列）。
 */
export const AGE_RESOLUTION_LABELS: Record<string, string> = {
  keep_roster: '保留系统年龄（名册不改，这一场也按名册的年龄记）',
  overwrite: '覆盖学生当前年龄（名册改成本次测评的年龄）',
  session_only: '只保存本次测评年龄（名册不改，这一场按文件里的年龄记）'
}

/**
 * 「不在本场任务里」的**两种** —— 后端 `assessment_import_row.out_of_scope_reason`（§18.7）。
 *
 * `match_status` 只有一个 `OUT_OF_SCOPE`，而这一列把两种「任务外」分开，因为**出路
 * 完全不同**：前者确认后可以补发目标学生（`POST /assessment-tasks/{id}/targets/supplement`），
 * 后者什么都不用做。合并成一个码时这两种人就只能共用一句话，而那句话要么对着其中
 * 一半的人说错，要么说得两种都做不了——这与 §14 那条「空态 / 错误态 / 加载中必须长得
 * 不一样」是同一条道理，只是这里分开的是两个**都不是错误**的结论。
 *
 * 两者都**不进完成率**，也不生成任何测评结果或风险事件（它们没有目标行，结构上进不去）。
 *
 * **没有 `*_ORDER`**：逐行明细是普通 `<table>`，没有可排序的表头（§3 第四面）。
 */
export const OUT_OF_SCOPE_REASON_LABELS: Record<string, string> = {
  SUPPLEMENT_CANDIDATE: '属于本场测评的范围，当时不在名单里',
  NOT_IN_TASK_SCOPE: '本来就不归这场测评管'
}

/**
 * **来源冲突**的四档处置 —— 后端 `assessment_import_service.CONFLICT_RESOLUTIONS`（§18.8）。
 *
 * 只有 `match_status === 'CONFLICT'` 的行才问这一栏：一名学生**自己在本系统里答过**
 * 这一场，学校又导进来一份同一场的外部结果。两个都不是错误，所以它看起来与「要拍板」
 * 那一档的其余几种一样，而处置却完全不同——另外几种（年龄不符 / 本月已导过）问的是
 * 「这一行写不写进去」，这一问的是「写的时候以哪一份为准、另一份留不留」。
 *
 * **四档各自落在哪里，中文必须说得出区别**（后端 `test_import_conflict_resolution.py`
 * 逐档钉住库里的落点）。`KEEP_BOTH_BUT_ONE_EFFECTIVE` 那一条尤其容易读成「两份都算数」：
 * 它建那场外部会话，但**算作当前结果的仍然是在线那一场**，外部的只是留着可查。
 *
 * **没有 `*_ORDER`**：这一栏是单选（与 `AGE_RESOLUTION_LABELS` 同形），不是可排序的列。
 * 键序 = 界面上的选项次序（后端 `CONFLICT_RESOLUTION_HINT` 的推荐次序）。
 *
 * **只有第二面能保护它，而第二面在这一张表上今天是空的**——与第 5 期的
 * `AGE_RESOLUTION_LABELS` / `OUT_OF_SCOPE_REASON_LABELS` 是同一处取舍（§26）：
 * 四个中文只渲染在 MHT 明细弹层的冲突行上，而 e2e 里 **`CONFLICT` 这一档到不了**
 * （演示名册的班级叫 `1班`，文件里那一列按学校编号必须写数字，于是任何一行都匹配
 * 不上学生，走不到「以哪一份为准」）。要覆盖它得先让共享演示库长出一个叫 `704` 的
 * 班级，而那会改动别的用例看到的名册（§测试注意：跑 e2e 不许改数据）。后端那一侧
 * 是钉住的（`test_import_conflict_resolution.py` 逐档断言），漏的是「视图有没有调用
 * 标签函数」。
 *
 * **这四个码仍然列进 `UNTRANSLATED_CODES`**（`e2e/vocabulary.spec.ts`），与 §3 里
 * `IMPORTED` / `SUPPLEMENT` 那两个同一条：它们是 `SCREAMING_SNAKE` 的码，不会在别的
 * 文案里当普通词出现（`overwrite` 被排除出那份清单**正是**因为这个），而今天没有数据
 * 不代表以后没有——列在那里，哪天演示数据里真有一条冲突行，它自己就开始生效。
 * 反面是 §24 里 `STUDENT` 那条：它被移除不是因为零覆盖，是因为它**会无故变红**
 * （审计页只显示最新 20 行，而另一个 spec 并发地往那 20 行里写）。这两个判据别混。
 */
export const CONFLICT_RESOLUTION_LABELS: Record<string, string> = {
  KEEP_ONLINE: '保留系统内作答（外部那份只留档，不建测评记录）',
  USE_EXTERNAL: '改用外部结果（系统内那一场作废保留，不删除）',
  REJECT_EXTERNAL: '不采纳外部结果（明确否掉，系统内那一场保持有效）',
  KEEP_BOTH_BUT_ONE_EFFECTIVE: '两份都留（外部那份留档，仍以系统内作答为准）'
}

/**
 * 导入**批次**的状态 —— 名册导入与 MHT 测评记录导入共用（两条链路同一套码）。
 *
 * 2026-09-19 从 `ROSTER_BATCH_STATUS_LABELS` 改名而来并**合成一张**：两条链路的批次
 * 状态机是同一个（上传落 `PREVIEW`，确认导入改成 `COMMITTED`），词汇也是同一套。
 * 各写一张的写法此前只覆盖了名册那一条，第二个读者出现时最省事的做法是再抄一份
 * ——而两份同义的表迟早会在某次改中文时只改一处（§3 第一面的反面，
 * `ScalePage.vue` 那份自抄的 `STATUS_LABELS` 就是这么坏的）。
 *
 * 两个取值都会真的出现，所以它不是给未来留的格子。`PREVIEW` 读作「预览中」而不是
 * 「未完成」——它是「有人上传过这份文件、看过校验结果、还没有拍板」，而那一行正是
 * 操作员回到这一页时要找的（他上传完被叫走了，回来接着提交的就是它。MHT 那一条
 * 链路上这一点由 V1.2 第 4 期实现：批次与逐行明细落库，所以「接着提交」真的接得上）。
 *
 * **没有 `*_ORDER`**：两条链路的批次历史都是普通 `<table>`（行数有界，客户端分页），
 * 没有 `Column.order` 的读者（§3 第四面）。
 */
export const IMPORT_BATCH_STATUS_LABELS: Record<string, string> = {
  PREVIEW: '预览中',
  COMMITTED: '已导入'
}

/**
 * 导入**逐行**的处理结果 —— 两条链路共用的 `processing_status`。
 *
 * 键序是流程先后（这一行在这一批里走到了哪一步），与 `CASE_STATUS_ORDER` 同一个读法。
 *   - `PENDING` 只出现在**预览过、还没提交**的批次里 —— 提交之前没人知道这一行会
 *     新建还是更新；
 *   - `SKIPPED` 是「选了放弃」：这一行撞上了库里已有的东西，而操作员决定这次不动它。
 *     它不是失败，所以界面上不能用红色（与 `LEVEL_ORDER` 那种从重到轻的排序无关）。
 *
 * `ERROR` **只有名册导入那一侧会写**：MHT 测评记录导入的「进不去」不在这一列上，
 * 而在 `match_status`（`INVALID_ROW` / `NOT_FOUND` / `OUT_OF_SCOPE` / `AMBIGUOUS`）——
 * 那些行**从来没有被提交过**，所以它们的 `processing_status` 一直停在 `PENDING`。
 * 留着这一条是因为它是同一套码的一部分，而把它摘掉会让两条链路各有一张表。
 *
 * `NOT_APPLIED`（2026-09-19 第 6 期加）是**来源冲突**那一档的产物：冲突行选了
 * 「保留系统内作答」或「不采纳外部结果」时，这一批**刻意不建任何测评记录**（学生本人
 * 答的那一份已经在库里了），而这一行也不是失败——它处理过了，只是没有东西可落。
 * 所以它的中文不能说成「跳过 / 忽略」，那会把「两次选择、两个问题」压成一个：
 * `SKIPPED` 回答的是「这一行写不写」，`NOT_APPLIED` 回答的是「写的时候以哪一份为准，
 * 而这一次的答案是不写外部那一份」（见 `CONFLICT_RESOLUTION_LABELS`）。
 *
 * 同样没有 `*_ORDER`（理由见上一条）。
 */
export const IMPORT_ROW_STATUS_LABELS: Record<string, string> = {
  PENDING: '待导入',
  CREATED: '新增',
  UPDATED: '更新',
  NOT_APPLIED: '未落成测评',
  SKIPPED: '已放弃',
  ERROR: '有错误'
}

/**
 * 名册导入的**冲突码** —— 后端 `student_import_service.CONFLICT_STUDENT_NO`。
 *
 * 与 `IMPORT_CONFLICT_LABELS` 是两个不同的东西，别看混：那一张是**测评记录导入**的
 * 待确认项（年龄不符 / 本月已导过），这一张是**名册导入**的（学号已在名册上）。
 * 两处的「覆盖 / 放弃」共用同一套英文码（后端也是同一套常量），但**问的问题不同**，
 * 所以中文标签各写各的。
 *
 * 逐行明细那一屏同时渲染 `conflict_code`（这一格）与 `message`（后端写的中文原因）：
 * 前者说这一行撞上了什么，后者说这次拿它怎么办。**只有第二面（`vocabulary.spec.ts`）
 * 能保护它**，而第二面在这一格上是空的——明细在弹层里，要走到那里得先有一批导入过。
 * 这与 `IMPORT_CONFLICT_LABELS` 是同一种取舍，不是忘了（CLAUDE.md §3 记着）。
 */
export const ROSTER_CONFLICT_LABELS: Record<string, string> = {
  STUDENT_NO_EXISTS: '学号已在名册上'
}

/** 风险事件状态 —— 后端 RiskEvent.status。 */
export const RISK_EVENT_STATUS_LABELS: Record<string, string> = {
  PENDING: '待复核',
  REVIEWED: '已复核'
}

/** 跟进记录状态 —— 后端 FollowUpRecord.status。 */
export const FOLLOW_UP_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '进行中',
  CLOSED: '已结束'
}

/** 复测计划状态 —— 后端 RetestPlan.status。 */
export const RETEST_STATUS_LABELS: Record<string, string> = {
  PLANNED: '已计划',
  DONE: '已完成',
  CANCELLED: '已取消'
}

/**
 * 提醒条目的类别 —— `/counselor/reminders` 的 `kind`。
 *
 * 它此前**不是一张表**，而是工作台里的一句内联三元：
 * `r.kind === 'RETEST' ? '复测' : '跟进'`。那是一处 §3 意义上的第二定义，
 * 而它的失败方式与「漏码」不同：else 那一支把**任何**认不出的码都读成「跟进」，
 * 于是将来加第三类提醒（比如家庭回访到期）时，屏幕上会多出一批写着「跟进」的
 * 别的东西——不报错，只是把一件事说成了另一件。
 *
 * `labelOf` 的兜底是**原样回退**（认不出的码照原样显示），这与 §3 那条
 * 「漏码要看得见」一致：宁可让操作员看到 `FAMILY_CONTACT`，也不要让他看到
 * 一个言之凿凿的「跟进」。
 */
export const REMINDER_KIND_LABELS: Record<string, string> = {
  FOLLOW_UP: '跟进',
  RETEST: '复测'
}

/**
 * 评分规则状态 —— 后端 ScaleRule.status。
 *
 * 与 SCALE_STATUS_LABELS 是两套词汇，不能合并：前者说「这版规则在不在用」
 * （ACTIVE / RETIRED），后者说「这个量表版本发没发布」（PUBLISHED / ARCHIVED）。
 * 同一个用例下，`/scales/versions` 返回前者映射后的 PUBLISHED，而
 * `/scales/versions/{id}/rule` 返回原始的 rule_status，两者都不是同一个字段。
 */
export const RULE_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '生效中',
  DRAFT: '草稿',
  RETIRED: '已废止'
}

/**
 * 登录会话的状态 —— 后端 `auth_service.effective_session_status`（阶段 8）。
 *
 * **`EXPIRED` 是现算的，`REVOKED` 是库里真发生过的动作**（§12 那条口径的又一处）：
 * 库里只写 `revoked_at`，过期由 `expires_at` 与此刻比出来。所以这一列会
 * 「自己」变——一条昨天还活跃的会话今天读到 `EXPIRED`，而它的行一个字都没动。
 *
 * 「已过期」与「已撤销」必须长得不一样，因为它们导向的动作不同：过期是正常的
 * （登进来放了太久），撤销是有人做了决定（本人点了退出、换了密码、或者管理员
 * 重置了密码）。把两者都说成「失效」的话，用户看到「退出其它设备」之后自己
 * 那一台变成什么就说不清了。
 *
 * **没有 `*_ORDER`**：会话列表是普通 `<table>`（每个人手上就几台设备），没有
 * `Column.order` 的读者（§3 第四面）。
 */
export const AUTH_SESSION_STATUS_LABELS: Record<string, string> = {
  ACTIVE: '活跃',
  REVOKED: '已撤销',
  EXPIRED: '已过期'
}

/**
 * 导出作业的**类型** —— 后端 `export_service.EXPORT_TYPE_*`。
 *
 * 六种取值全部可达（五个入口加「重点学生」那一支的高度关注导出）。它与文件的
 * 列清单是两件事：同一份「关注档案摘要」按遮蔽等级不同列是一样的，而
 * 「任务完成统计」与「未参与名单」是同一场任务的两份不同文件。
 *
 * **没有 `*_ORDER`**：导出中心那一列不做排序（有用的是状态与时间，见下）。
 */
export const EXPORT_TYPE_LABELS: Record<string, string> = {
  CARE_CASES: '关注档案摘要',
  HIGH_RISK_CASES: '高度关注摘要',
  SINGLE_CASE: '个案档案',
  TASK_COMPLETION: '任务完成明细',
  NON_PARTICIPANTS: '未参与名单',
  UNMATCHED_IMPORT_ROWS: '未匹配行清单'
}

/**
 * 导出文件的**遮蔽等级** —— 后端 `export_service.MASK_LEVEL_*`（§16.3）。
 *
 * 这一列与 `purpose` 分开存是有原因的（§8）：用途是自由文本、担不起任何机器
 * 判据，而「这份文件是不是实名的」恰恰是导出审计唯一要回答的问题。所以在界面上
 * 它也必须是一眼能看出来的那一格——排在用途旁边、带着颜色。
 *
 * 中文写成「姓名已遮蔽 / 实名」，不是「脱敏 / 不脱敏」：后者听起来是一个开关的
 * 两档，而这里要回答的是**这份文件里能不能认出人**。
 *
 * **没有 `*_ORDER`**：不做排序（两档分成两组看不出什么）。
 */
export const MASK_LEVEL_LABELS: Record<string, string> = {
  MASKED: '姓名已遮蔽',
  IDENTIFIED: '实名'
}

/**
 * 导出作业的**状态** —— 后端 `export_service.effective_export_status`。
 *
 * 键序 = **判据次序**（`effective_export_status` 里的先后）：先看有没有被撤销，
 * 再看有没有过期，都不是才是可下载。这不是随手排的——一份先被撤销、后又跨过
 * 有效期的文件，它的故事是「有人叫停了它」，而不是「它自己到期了」。所以
 * `EXPORT_JOB_STATUS_ORDER` 取它，在导出中心那一列上排序时，两端分别是
 * 「还能用的」与「已经没了的」。
 *
 * `PENDING` / `FAILED` 两种取值**不在表里**：导出是同步的（作业行与文件在同一个
 * 请求里成型），所以它们在库里不可达——模型上有默认值是 DDL 对齐阶段的形状。
 * 留着词条等于给一个永远不会出现的格子写中文。
 */
export const EXPORT_JOB_STATUS_LABELS: Record<string, string> = {
  READY: '可下载',
  REVOKED: '已撤销',
  EXPIRED: '已过期'
}

export const EXPORT_JOB_STATUS_ORDER: readonly string[] = Object.keys(EXPORT_JOB_STATUS_LABELS)

export type Tone = 'red' | 'amber' | 'green' | 'blue' | 'gray'

/** Unmapped codes fall through to the raw value rather than rendering blank. */
export function labelOf(map: Record<string, string>, code: string | null | undefined, fallback = '—') {
  if (!code) return fallback
  return map[code] || code
}

export function levelLabel(level: string | null | undefined) {
  return level ? LEVEL_LABELS[level] || level : '未测评'
}

export function statusLabel(status: string) {
  return STATUS_LABELS[status] || status
}

export function dimensionLabel(code: string) {
  return DIMENSION_LABELS[code] || code
}

export function levelTone(level: string | null | undefined): Tone {
  if (level === 'KEY_ATTENTION') return 'red'
  if (level === 'NEEDS_ATTENTION') return 'amber'
  return 'gray'
}

export function statusTone(status: string): Tone {
  if (status === 'CLOSED') return 'green'
  if (status === 'FOLLOWING') return 'blue'
  if (status === 'PENDING_REVIEW') return 'red'
  return 'amber'
}

export function dimensionTone(level: string): string {
  if (level === 'HIGH') return 'high'
  if (level === 'MEDIUM') return 'medium'
  return ''
}

export function scopeTypeLabel(code: string | null | undefined) {
  return labelOf(SCOPE_TYPE_LABELS, code)
}

/**
 * **用户自己的**数据范围（`/auth/me` 的 `scopes[].scope_type`）。
 *
 * 编码与 `SCOPE_TYPE_LABELS` 是同一批，但**不是同一个读法**，所以是第二张表而不是
 * 复用第一张：任务的对象范围说「按年级」，是在说这份任务发给哪些人；用户的范围说
 * 「你负责的年级」，因为读这句话的人正是那个范围的主人。写成「按年级」的话，
 * 放在「全部学生」那一页上读起来像个筛选条件，而不是「你只能看到这些」。
 *
 * 多行范围由 `student_scope_predicate` 用 `or_` 合并（§9），所以界面上是并列，
 * 语义是并集——数字比其中任何一行都大是正确的。
 */
export const USER_SCOPE_LABELS: Record<string, string> = {
  SCHOOL: '全校',
  GRADE: '你负责的年级',
  CLASS: '你负责的班级',
  STUDENT: '你负责的学生'
}

export function userScopeLabel(code: string | null | undefined) {
  return labelOf(USER_SCOPE_LABELS, code)
}

/**
 * **给别人配**的数据范围（「账号与权限」里的那一列与那个下拉框）。
 *
 * 第三张表，因为这是第三种读法：这里既不描述一份任务发给谁（`SCOPE_TYPE_LABELS`
 * 的「按年级」），也不描述读者自己能看到什么（`USER_SCOPE_LABELS` 的「你负责的
 * 年级」），而是**给一段范围起个名字**——读它的人是管理员，他要在一行里回答
 * 「这个账号管到哪一层」。所以是中性词，和后面的名字拼起来读：
 * 「年级 · 初一」「班级 · 701」。
 *
 * 名字本身（初一 / 701）由服务端随范围行一起发（`scopes[].name`），
 * 因为那要查库；这两个字是编码的中文，所以留在这里。
 */
export const ACCOUNT_SCOPE_LABELS: Record<string, string> = {
  SCHOOL: '全校',
  GRADE: '年级',
  CLASS: '班级',
  STUDENT: '学生'
}

export function accountScopeLabel(code: string | null | undefined) {
  return labelOf(ACCOUNT_SCOPE_LABELS, code)
}

export function validityLabel(code: string | null | undefined) {
  return labelOf(VALIDITY_LABELS, code, '未测评')
}

/** 效度是「这份答卷可不可信」的判断，所以重测建议要比存疑更重。 */
export function validityTone(code: string | null | undefined): Tone {
  if (code === 'VALID') return 'green'
  if (code === 'RETEST_RECOMMENDED') return 'red'
  if (code === 'QUESTIONABLE') return 'amber'
  return 'gray'
}

export function studentStatusLabel(code: string | null | undefined) {
  return labelOf(STUDENT_STATUS_LABELS, code)
}

/** 只有「在读」该是绿的；未知状态不该冒充正常。 */
export function studentStatusTone(code: string | null | undefined): Tone {
  return code === 'ACTIVE' ? 'green' : 'gray'
}

export function genderLabel(code: string | null | undefined) {
  return labelOf(GENDER_LABELS, code)
}

/**
 * 年龄 —— 名册上存下来的整数（0011 之后不再是后端按出生日期现算），没填就是 null。
 * 与其他 label 一样，缺失显示「—」而不是 0。
 */
export function ageLabel(age: number | null | undefined) {
  return age === null || age === undefined ? '—' : `${age} 岁`
}

export function riskEventStatusLabel(code: string | null | undefined) {
  return labelOf(RISK_EVENT_STATUS_LABELS, code)
}

export function riskEventStatusTone(code: string | null | undefined): Tone {
  return code === 'PENDING' ? 'amber' : 'green'
}

export function followUpStatusLabel(code: string | null | undefined) {
  return labelOf(FOLLOW_UP_STATUS_LABELS, code)
}

/** 提醒条目的类别（跟进 / 复测）—— 工作台「本周提醒」那一行前缀。 */
export function reminderKindLabel(code: string | null | undefined) {
  return labelOf(REMINDER_KIND_LABELS, code)
}

/** 任务自身状态（这场测评开没开）—— `effective_task_status` 现算出来的那一列。
 *
 * 与下面那个 `targetStatusLabel` 是**两个不同的问题**：那个说的是「这名学生在这
 * 场测评里做完没有」，这个说的是「这场测评本身现在开着没有」。学生首页此前只读
 * 前者，于是「还没开始」的那场也长着一个点得动的「开始作答」——而后端那道门
 * （`create_or_get_session`）判的正是这个函数背后的状态，点了只会拿到 404。
 */
export function taskStatusLabel(code: string | null | undefined) {
  return labelOf(TASK_STATUS_LABELS, code)
}

/** 任务目标（某学生在某任务里的完成情况）—— AssessmentTarget.status。 */
export function targetStatusLabel(code: string | null | undefined) {
  return labelOf(TARGET_STATUS_LABELS, code)
}

export function targetStatusTone(code: string | null | undefined): Tone {
  if (code === 'COMPLETED') return 'green'
  if (code === 'IN_PROGRESS') return 'blue'
  return 'gray'
}

/** 参与状态（应测 / 请假 / 免测 / 已排除）—— AssessmentTarget.participation_disposition。 */
export function participationLabel(code: string | null | undefined) {
  return labelOf(PARTICIPATION_DISPOSITION_LABELS, code)
}

export function participationTone(code: string | null | undefined): Tone {
  return code === 'REQUIRED' ? 'gray' : 'amber'
}

export function exportTypeLabel(code: string | null | undefined) {
  return labelOf(EXPORT_TYPE_LABELS, code)
}

export function maskLevelLabel(code: string | null | undefined) {
  return labelOf(MASK_LEVEL_LABELS, code)
}

/**
 * 实名那一份给琥珀色，不是红色。
 *
 * 实名导出**不是错误**——心理老师要跟进就得看得见名字，那是他的正常工作。红色
 * 会把它说成一次事故，而它每天都会发生。琥珀色的意思是「注意这一格」：这一列
 * 存在的全部理由就是让读列表的人一眼看出「哪些文件里能认出人」。
 */
export function maskLevelTone(code: string | null | undefined): Tone {
  return code === 'IDENTIFIED' ? 'amber' : 'gray'
}

export function exportJobStatusLabel(code: string | null | undefined) {
  return labelOf(EXPORT_JOB_STATUS_LABELS, code)
}

/**
 * 三档各自的颜色回答的是「现在该怎么办」：
 *   - **可下载**（绿）：还能用；
 *   - **已撤销**（红）：有人主动叫停了它——这条轨迹值得被看见，数据外泄时管理员
 *     就是照这一批找过去的；
 *   - **已过期**（灰）：正常的，放太久了。灰色而不是红色，因为「昨天导的文件今天
 *     点不动」不该看起来像出事了。
 */
export function exportJobStatusTone(code: string | null | undefined): Tone {
  if (code === 'READY') return 'green'
  if (code === 'REVOKED') return 'red'
  return 'gray'
}

export function authSessionStatusLabel(code: string | null | undefined) {
  return labelOf(AUTH_SESSION_STATUS_LABELS, code)
}

/** 与上面同一条读法：撤销是有人做的决定（红），过期是时间到了（灰）。 */
export function authSessionStatusTone(code: string | null | undefined): Tone {
  if (code === 'ACTIVE') return 'green'
  if (code === 'REVOKED') return 'red'
  return 'gray'
}

export function retestStatusLabel(code: string | null | undefined) {
  return labelOf(RETEST_STATUS_LABELS, code)
}

export function ruleStatusLabel(code: string | null | undefined) {
  return labelOf(RULE_STATUS_LABELS, code)
}

/**
 * 量表版本状态（`ScaleVersion.status`）。
 *
 * 2026-09-17 补：`SCALE_STATUS_LABELS` 这张表一直在这里，却**没有取用它的函数**，
 * 于是题库页在自己的 `<script setup>` 里又抄了一份同名的 `STATUS_LABELS`
 * （`ScalePage.vue`，键序还是另一个顺序）。那是 §3 那条约定的反面——
 * 一张表两个定义，改中文时改一处、漏一处，而漏的那处界面上照旧显示旧词。
 * 现在这张表有了自己的读者。
 */
export function scaleStatusLabel(code: string | null | undefined) {
  return labelOf(SCALE_STATUS_LABELS, code)
}

/** 只有「已发布」是生效的那一版；草稿与归档都不该冒充正常。 */
export function scaleStatusTone(code: string | null | undefined): Tone {
  if (code === 'PUBLISHED') return 'green'
  if (code === 'DRAFT') return 'amber'
  return 'gray'
}

/** 测评来源。只有外部导入的那些才有必要在界面上标出来，所以调用方一般会先判断。 */
export function importConflictLabel(code: string | null | undefined) {
  return labelOf(IMPORT_CONFLICT_LABELS, code)
}

export function sourceLabel(code: string | null | undefined) {
  return labelOf(SOURCE_LABELS, code)
}

/** 导入形态。它总有一个值（那一列 NOT NULL 带默认值 `EXTERNAL_FULL_ANSWER`）。 */
export function importModeLabel(code: string | null | undefined) {
  return labelOf(IMPORT_MODE_LABELS, code)
}

/**
 * 年龄冲突的处置。回退值是**空串**：绝大多数行没有年龄冲突（`age_resolution` 是 `null`），
 * 让每一行都印一个 `—` 只会把这一列变成噪声。认不出的码仍然**原样回退**，漏译看得见。
 */
export function ageResolutionLabel(code: string | null | undefined) {
  return labelOf(AGE_RESOLUTION_LABELS, code, '')
}

/**
 * 来源冲突的处置。
 *
 * 回退值**与上面那几个不一样，是「未处置」而不是空串**：这一格坐在冲突行的处置面板
 * 旁边，而那一行在提交之前**本来**就没有值——空着是对的（没人问过），所以不能学
 * `ageResolutionLabel` 那样回空串，那样界面上分不出「没有这一栏」与「你还没选」。
 * 认不出的码仍然原样回退，漏译看得见（`labelOf` 的既有约定）。
 */
export function conflictResolutionLabel(code: string | null | undefined) {
  return labelOf(CONFLICT_RESOLUTION_LABELS, code, '未处置')
}

/**
 * 「不在本场任务里」的原因。回退值同样是**空串**（只有 `OUT_OF_SCOPE` 的行才有这一列，
 * 而它在九个 `match_status` 里只占一档）。
 */
export function outOfScopeReasonLabel(code: string | null | undefined) {
  return labelOf(OUT_OF_SCOPE_REASON_LABELS, code, '')
}

/** 评分状态。它总有一个值（那一列 NOT NULL 带默认值 `PENDING`）。 */
export function calculationStatusLabel(code: string | null | undefined) {
  return labelOf(CALCULATION_STATUS_LABELS, code)
}

/**
 * 评分状态的语气。
 *
 * 「算出来了」是正常的（绿），「计算中 / 待计算」不是故障（灰），**只有失败要说出来
 * 而且要说成需要人动手**（红）——它与此前那种「分数不在，因为还没算」长得一样，
 * 而两者的出路完全不同：一个等，一个要看一眼原因再点重算。
 */
export function calculationStatusTone(code: string | null | undefined): Tone {
  if (code === 'CALCULATED') return 'green'
  if (code === 'CALCULATION_FAILED') return 'red'
  return 'gray'
}

/** 测评日期的来源。历史行是 `PENDING_VERIFICATION`，照实说「不知道」。 */
export function testedAtSourceLabel(code: string | null | undefined) {
  return labelOf(TESTED_AT_SOURCE_LABELS, code)
}

/** 目标学生来源。`target_source` 是 NOT NULL 带默认值，所以它总有一个值可读。 */
export function targetSourceLabel(code: string | null | undefined) {
  return labelOf(TARGET_SOURCE_LABELS, code)
}


/** 已废止的规则要显眼——它还在页面上，但已经不参与评分了。 */
export function ruleStatusTone(code: string | null | undefined): Tone {
  return code === 'ACTIVE' ? 'green' : 'gray'
}

/** 导入批次的状态。`PREVIEW` 留着灰色——它是一件还没办完的事，不是故障。 */
export function importBatchStatusLabel(code: string | null | undefined) {
  return labelOf(IMPORT_BATCH_STATUS_LABELS, code)
}

export function importBatchStatusTone(code: string | null | undefined): Tone {
  return code === 'COMMITTED' ? 'green' : 'gray'
}

/**
 * 导入逐行的处理结果。
 *
 * `ERROR` 才用红色。「放弃」是操作员拍板的结果（这一行撞上的东西他决定这次不动），
 * 把它标红等于把一次决定说成一次故障，而它会一直挂在这一批的明细里。
 */
export function importRowStatusLabel(code: string | null | undefined) {
  return labelOf(IMPORT_ROW_STATUS_LABELS, code)
}

export function importRowStatusTone(code: string | null | undefined): Tone {
  if (code === 'ERROR') return 'red'
  // 「已放弃」与「未落成测评」都是灰的，而且理由相同：这一行**没有写进任何记录**，
  // 而那是人做过决定之后的结果，不是故障。两者分开的只是**答的是哪个问题**——
  // `SKIPPED` 答「这一行写不写」，`NOT_APPLIED` 答「以哪一份为准，而这一次不写外部
  // 那一份」。所以给后者绿色（读成「写成功了」）会把一件刻意没写的事说成写过。
  if (code === 'SKIPPED' || code === 'NOT_APPLIED') return 'gray'
  return 'green'
}

/**
 * 逐行匹配结论。
 *
 * 语气按**这一行还能不能进**分三档，与后端那三张集合逐档对应：
 *   - `MATCHED` 绿（唯一候选、无冲突，什么都不用做）；
 *   - 要拍板的三档（年龄不符 / 本月重复 / 本场已有答卷）琥珀——**不是故障**，
 *     是「等你拿个主意」，与名册导入那边「放弃」不用红色的道理同源；
 *   - 进不去的四档红：这一行**现在**写不进去，而操作员得去改别的东西（改文件、
 *     补名册、补发目标）才救得回来。
 *   - `PENDING` 灰：还没匹配过，不是结论。
 *
 * 这三档**不能混**：把一档从琥珀挪到红色（或反过来）等于告诉操作员一件错的事——
 * 要么让他去找一个不存在的错误，要么让他以为选「覆盖」就能把这一行写进去。
 */
export function matchStatusLabel(code: string | null | undefined) {
  return labelOf(MATCH_STATUS_LABELS, code)
}

export function matchStatusTone(code: string | null | undefined): Tone {
  if (code === 'MATCHED') return 'green'
  if (code === 'AGE_CONFLICT' || code === 'DUPLICATE' || code === 'CONFLICT') return 'amber'
  if (code === 'INVALID_ROW' || code === 'NOT_FOUND' || code === 'OUT_OF_SCOPE' || code === 'AMBIGUOUS') {
    return 'red'
  }
  return 'gray'
}

/**
 * 「这场任务还有谁缺着」那一屏的匹配结论（见 `UNMATCHED_REASON_LABELS`）。
 *
 * 语气只与上面差一档，而那一档正是这一屏存在的理由：`MATCHED` 在这里读作
 * 「匹配上了、提交时被放弃」，那是一次**决定**，不是一次失败——所以它是灰的，
 * 与 `IMPORT_ROW_STATUS_LABELS` 里 `SKIPPED` 用灰是同一条道理（§3：放弃了不是错）。
 * 用绿色的话，一屏待办里最上面那几行会看起来像已经处理好了。
 */
export function unmatchedReasonLabel(code: string | null | undefined) {
  return labelOf(UNMATCHED_REASON_LABELS, code)
}

export function unmatchedReasonTone(code: string | null | undefined): Tone {
  if (code === 'MATCHED') return 'gray'
  return matchStatusTone(code)
}

/**
 * 一批的处置方式。
 *
 * 回退值是**空串**而不是 `labelOf` 默认的 `—`：这一格只在「已经提交过」时才渲染，
 * 而 `null`（还没提交）由调用方整句不显示——那时留一个 `—` 会让人以为这一列漏了值。
 * 认不出的码仍然**原样回退**（`labelOf` 的第二个分支），漏译照样看得见。
 */
export function assessmentResolutionLabel(code: string | null | undefined) {
  return labelOf(ASSESSMENT_RESOLUTION_LABELS, code, '')
}

/**
 * 名册导入的冲突码。
 *
 * 回退值是**空串**而不是 `labelOf` 默认的 `—`：绝大多数行的 `conflict_code` 就是
 * `null`（没撞上任何东西），让每一行都印一个 `—` 会把这一列变成噪声。而认不出的码
 * 仍然**原样回退**（`labelOf` 的第二个分支），漏译照样看得见——两件事分别落在两个
 * 分支上，所以「留白」只发生在真的没有值的时候（§3 那条既有约定）。
 */
export function rosterConflictLabel(code: string | null | undefined) {
  return labelOf(ROSTER_CONFLICT_LABELS, code, '')
}

/**
 * Bar width for a dimension score, relative to that dimension's OWN item count.
 *
 * Dimensions are not uniformly sized — two carry 15 items, the rest 10 — so a
 * fixed denominator either leaves most bars permanently under-filled or makes
 * them incomparable. Pass the dimension's `max_score`; 15 is only a fallback for
 * callers that don't have it.
 */
export const DIMENSION_FALLBACK_MAX = 15

export function dimensionPercent(score: number, maxScore?: number) {
  const denominator = maxScore && maxScore > 0 ? maxScore : DIMENSION_FALLBACK_MAX
  return Math.min(Math.round((score / denominator) * 100), 100)
}

/**
 * **关怀档案的生命周期事件** —— 后端 `care_case_event.event_type`
 * （`services/care_events.py` 的码表，§16.4）。
 *
 * 它与 `STATUS_LABELS`（档案状态）是**两个轴**：状态说的是「**现在**是什么」，
 * 事件说的是「**怎么走到这儿**的」。同一份档案的 `CASE_CLOSED` 与 `CASE_REOPENED`
 * 会同时在时间线上，而它当前的状态只有一个。
 *
 * **与审计不是一回事**（后端模型的 docstring 写着同一条）：审计回答「谁在什么时候
 * 调了哪个接口」，这张表回答「这份档案经历了什么」。同一次操作两条都写，而读者不同
 * ——审计是系统级轨迹，事件是**这份档案的病历**。所以个案详情上是两个页签，
 * 不是一张合并的表。
 *
 * 键序 = **流程先后**（开档在最前、关档与重开在最后），与 `CASE_STATUS_ORDER`
 * 把「已关闭」放末端同一个读法。**没有 `*_ORDER`**：读者是时间线上的一行字，
 * 不是 `DataTable` 的可排序列（§3 第四面）。
 */
export const CARE_EVENT_LABELS: Record<string, string> = {
  CASE_OPENED: '开档',
  MANUAL_REVIEWED: '人工复核',
  FOLLOW_UP_ADDED: '新增跟进',
  FAMILY_CONTACT_ADDED: '家庭回访',
  RETEST_PLANNED: '安排复测',
  CASE_CLOSED: '关闭档案',
  CASE_REOPENED: '重新打开',
  OWNER_ASSIGNED: '转派负责人'
}

/**
 * 时间线上那一行事件的名字。
 *
 * 认不出的码**原样回退**（`labelOf` 的第二个分支，§3 那条既有约定：
 * 「漏码要看得见」）。这里特别不能改成留白：`e2e/vocabulary.spec.ts` 的
 * **第二面**（视图有没有调用标签函数）扫的就是正文里的编码——一个漏译的事件
 * 渲染成 `—` 之后，那条守卫再也看不见它了。
 *
 * 后端 `record_case_event` 对认不出的码当场抛，所以库里不会有第三个来源的码；
 * 真正可能漏的是**这里少写一条词条**（后端加了第九个事件码而这张表没跟上），
 * 而那正是原样回退要暴露的东西。
 */
export function careEventLabel(code: string | null | undefined) {
  return labelOf(CARE_EVENT_LABELS, code)
}

/**
 * 事件的语气。
 *
 * 三档，判据是**这个动作对这份档案做了什么**：
 *   - 关档绿：这一条生命走到了一个了结（与 `statusTone('CLOSED')` 的绿同一个意思，
 *     不是「好」的意思）；
 *   - 开档 / 重开红：这两件都是「又出状况了」——开档是系统判出需要关注，重开是
 *     已经了结的档案又回来了。它们是同一条线上最该被看见的两个点；
 *   - 其余（复核 / 跟进 / 回访 / 复测 / 转派）蓝：都是**办过的事**，与
 *     `statusTone('FOLLOWING')` 的蓝同源。
 *
 * 认不出的码灰——但见 `careEventLabel` 那条：那时名字已经是 `—` 了。
 */
export function careEventTone(code: string | null | undefined): Tone {
  if (code === 'CASE_CLOSED') return 'green'
  if (code === 'CASE_OPENED' || code === 'CASE_REOPENED') return 'red'
  if (code && code in CARE_EVENT_LABELS) return 'blue'
  return 'gray'
}
