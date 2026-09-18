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
 * MHT 导入的**待确认项** —— 后端 `assessment_import_service` 的冲突类型码。
 *
 * 它们不落库（预览时现算，见 CLAUDE.md §12 的同一思路），所以没有后端测试盯着
 * 「发出来的码前端认不认」；能守的是 `e2e/vocabulary.spec.ts` 那份编码清单——
 * 界面上出现 `AGE_MISMATCH` 这串字就是没走这张表。
 */
export const IMPORT_CONFLICT_LABELS: Record<string, string> = {
  AGE_MISMATCH: '年龄与名册不符',
  DUPLICATE: '本月已有一次导入'
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

/** 任务目标（某学生在某任务里的完成情况）—— AssessmentTarget.status。 */
export function targetStatusLabel(code: string | null | undefined) {
  return labelOf(TARGET_STATUS_LABELS, code)
}

export function targetStatusTone(code: string | null | undefined): Tone {
  if (code === 'COMPLETED') return 'green'
  if (code === 'IN_PROGRESS') return 'blue'
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


/** 已废止的规则要显眼——它还在页面上，但已经不参与评分了。 */
export function ruleStatusTone(code: string | null | undefined): Tone {
  return code === 'ACTIVE' ? 'green' : 'gray'
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
