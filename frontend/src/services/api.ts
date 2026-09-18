const API_BASE = '/api/v1'
const TOKEN_KEY = 'xlp_access_token'

export type Role = 'student' | 'counselor' | 'leader' | 'admin'

export interface CurrentUser {
  id: number
  account: string
  account_type: string
  display_name: string
  role_code: Role
  must_change_password: boolean
  active: boolean
  /**
   * 数据范围（§9）。`/auth/me` 一直在发它，此前**没有类型所以没有读者**——
   * 于是那些「这个数是我范围内的，不是全校的」的口径只能靠注释交代，
   * 界面上写不出来。「全部学生」那一页第一个用上了它。
   *
   * 可空：老会话/别的调用方可能拿不到，读的时候按「不知道」处理，不要当空范围。
   */
  scopes?: Array<{
    scope_type: string
    school_id: number | null
    grade_id: number | null
    class_id: number | null
    student_id: number | null
    /**
     * 范围指向的那一行叫什么（学校 / 年级 / 班级的名字）。
     * 服务端在 `auth_service.resolve_scope_names` 里整表读一次，内存里查，不是逐行回查。
     * 可空：`STUDENT` 范围没有可拼的名字（它是隐私），查不到的 id 也给 `null`。
     * 读的时候按「不知道」处理，不要拼出「班级 · 」这种半句话。
     */
    name?: string | null
  }>
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  const body = await response.json()
  if (!response.ok || !body.success) {
    throw new Error(body.error?.message || '请求失败')
  }
  return body.data as T
}

export async function login(role: Role, account: string, password: string): Promise<CurrentUser> {
  const data = await apiRequest<{ access_token: string; user: CurrentUser }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ role, account, password })
  })
  setToken(data.access_token)
  return data.user
}

export async function getMe(): Promise<CurrentUser> {
  return apiRequest<CurrentUser>('/auth/me')
}

/**
 * Rotate the signed-in user's own password. Also clears `must_change_password`,
 * which is what releases the forced-rotation gate in AppLayout.
 */
export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  await apiRequest('/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
  })
}

export async function logout(): Promise<void> {
  try {
    await apiRequest('/auth/logout', { method: 'POST' })
  } finally {
    clearToken()
  }
}

export interface StudentTask {
  id: number
  task_no: string
  name: string
  status: string
  target_status: string
  start_at: string | null
  end_at: string | null
  session_id: number | null
  answered_count: number
  completed_at: string | null
}

export interface AssessmentSession {
  id: number
  task_id: number | null
  student_id: number
  scale_id: number
  scale_version: string
  status: string
  current_question_no: number | null
  answered_count: number
  answers: Record<string, 'YES' | 'NO'>
}

export async function getStudentTasks(): Promise<StudentTask[]> {
  const data = await apiRequest<{ items: StudentTask[] }>('/student/tasks')
  return data.items
}

export interface StudentAssessmentHistoryItem {
  task_no: string
  task_name: string
  status: string
  answered_count: number
  submitted_at: string | null
  /** 首次作答 → 交卷，单位为秒。迁移前的会话与 /reset 之后的会话是 null。 */
  duration_seconds: number | null
  /** IN_SYSTEM / IMPORTED，或 null（这一行没有会话）。 */
  source: string | null
}

export async function getStudentAssessmentHistory(): Promise<StudentAssessmentHistoryItem[]> {
  const data = await apiRequest<{ items: StudentAssessmentHistoryItem[] }>('/student/assessment-history')
  return data.items
}

export async function createAssessmentSession(taskId: number): Promise<AssessmentSession> {
  return apiRequest<AssessmentSession>('/assessment-sessions', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId })
  })
}

export async function getAssessmentSession(sessionId: number): Promise<AssessmentSession> {
  return apiRequest<AssessmentSession>(`/assessment-sessions/${sessionId}`)
}

/** Question stems for the session's scale version. No dimension or key-question flags. */
export async function getSessionQuestions(
  sessionId: number
): Promise<Array<{ question_no: number; question_text: string }>> {
  const data = await apiRequest<{ items: Array<{ question_no: number; question_text: string }> }>(
    `/assessment-sessions/${sessionId}/questions`
  )
  return data.items
}

export async function saveAssessmentAnswer(
  sessionId: number,
  questionNo: number,
  answer: 'YES' | 'NO'
): Promise<AssessmentSession> {
  return apiRequest<AssessmentSession>(`/assessment-sessions/${sessionId}/answers/${questionNo}`, {
    method: 'PUT',
    body: JSON.stringify({ answer })
  })
}

export async function submitAssessmentSession(sessionId: number): Promise<void> {
  await apiRequest(`/assessment-sessions/${sessionId}/submit`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': `submit-${sessionId}`
    }
  })
}

export interface CounselorWorkbench {
  pending_review: number
  following: number
  pending_risk_events: number
  completion_rate: number
}

export interface CareCaseItem {
  case_id: number
  student_id: number
  student_no: string
  student_name: string
  grade: string
  class_name: string
  case_status: string
  owner_id: number | null
  owner_name: string | null
  total_level: string | null
  validity_status: string | null
  pending_risk_events: number
  next_follow_up_date: string | null
  overdue: boolean
  opened_at: string | null
  updated_at: string | null
}

export interface AssignableOwner {
  id: number
  display_name: string
}

export interface CareCaseDetail {
  case_id: number
  student: {
    id: number
    student_no: string
    name: string
    grade: string
    class_name: string
    /** MALE / FEMALE，或 null（名册早于迁移 0009，或学校没填）。 */
    gender: string | null
    /** 名册上存下来的整数年龄，学校没填时为 null。 */
    age: number | null
  }
  case_status: string
  assessment: {
    session_id: number | null
    submitted_at: string | null
    duration_seconds: number | null
    total_level: string | null
    validity_status: string | null
    rule_version: string | null
    /** IN_SYSTEM / IMPORTED，或 null（还没有任何一场测评）。 */
    source: string | null
  }
  risk_events: Array<{
    id: number
    risk_type: string
    risk_level: string
    trigger_rule: string
    status: string
    created_at: string | null
  }>
  follow_ups: Array<{
    id: number
    record_type: string
    confirmed_facts: string
    next_follow_up_date: string
    status: string
    created_at: string | null
  }>
  family_contacts: Array<{
    id: number
    contact_date: string
    contact_person: string
    channel: string
    result: string
    support_status: string
    confirmed_facts: string
    next_contact_date: string | null
  }>
  retest_plans: Array<{
    id: number
    planned_date: string
    reason: string
    status: string
  }>
  dimensions: Array<{
    dimension_code: string
    score: number
    level: string
    interpretation: string
    /** Item count behind this dimension — the denominator for `score`. */
    max_score: number
  }>
  /** 历次测评，**从旧到新**（后端按施测时间升序排，见 `session_history_order`）。 */
  history: Array<{
    session_id: number
    submitted_at: string | null
    duration_seconds: number | null
    total_score: number | null
    total_level: string | null
    validity_status: string | null
    /**
     * 这一场自己的八维度分。`max_score` 是画图用的分母：各维度题数不等（10 或 15），
     * 不归一化会让 15 题的身体症状在图上凭空压过 10 题的孤独倾向。
     */
    dimensions: Array<{
      dimension_code: string
      score: number
      max_score: number
    }>
  }>
  audit_logs: Array<{
    id: number
    created_at: string | null
    actor_name: string | null
    actor_role: string | null
    action: string
    result: string
    purpose: string | null
  }>
}

export async function getCounselorWorkbench(): Promise<CounselorWorkbench> {
  return apiRequest<CounselorWorkbench>('/counselor/workbench')
}

export async function getCareCases(): Promise<CareCaseItem[]> {
  const data = await apiRequest<{ items: CareCaseItem[] }>('/care-cases')
  return data.items
}

export async function getCareCaseDetail(studentId: number): Promise<CareCaseDetail> {
  return apiRequest<CareCaseDetail>(`/care-cases/${studentId}`)
}

/**
 * 这名学生最近一场的维度得分，与班级、年级当前水平的对照。
 *
 * 独立于 `getCareCaseDetail` 取：档案页打开时这两个请求并发，对照表晚到一会儿
 * 不该拖住整页；而且每次读都会写一条审计，合并进详情接口会让「看了一次对照」
 * 与「打开了一次档案」在轨迹里分不出来。
 */
export async function getClassComparison(studentId: number): Promise<ClassComparison> {
  return apiRequest<ClassComparison>(`/care-cases/${studentId}/comparison`)
}

export async function createManualReview(
  caseId: number,
  payload: {
    risk_event_id: number
    review_result: string
    confirmed_facts: string
    next_action?: string
    next_follow_up_date?: string
  }
): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/reviews`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function createFollowUp(
  caseId: number,
  payload: {
    record_type: string
    confirmed_facts: string
    next_follow_up_date: string
  }
): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/follow-ups`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function createFamilyContact(
  caseId: number,
  payload: {
    contact_date: string
    contact_person: string
    channel: string
    result: string
    support_status: string
    confirmed_facts: string
    next_contact_date?: string
  }
): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/family-contacts`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function createRetestPlan(caseId: number, payload: { planned_date: string; reason: string }): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/retests`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function closeCareCase(
  caseId: number,
  payload: { close_reason: string; close_note: string; confirm_follow_up_checked: boolean }
): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/close`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function reopenCareCase(caseId: number, reason: string): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/reopen`, {
    method: 'POST',
    body: JSON.stringify({ reason })
  })
}

export interface AnalyticsOverview {
  total_targets: number
  completed_targets: number
  completion_rate: number
  /** 已测评人数（每人最近一场）。比率的**分母**，不再是结果行数。 */
  assessed_count: number
  attention_count: number
  /** `null` = 样本过小，不给比率（后端 `MIN_COHORT_FOR_AGGREGATE`）。0 是「一个都没有」。 */
  attention_rate: number | null
  /** 三档分布：一般 / 需关注 / 重点关注。 */
  level_counts: Record<string, number>
  key_attention_count: number
  case_status_counts: Record<string, number>
  planned_retests: number
}

/** 完成情况之外，年级/班级行还带关注人数与关注率。 */
export interface CohortAnalytics {
  assessed_count: number
  attention_count: number
  key_attention_count: number
  attention_rate: number | null
  cohort_too_small: boolean
}

export interface GradeAnalytics extends CohortAnalytics {
  grade_id: number
  grade: string
  total_targets: number
  completed_targets: number
  completion_rate: number
}

export interface ClassAnalytics extends CohortAnalytics {
  class_id: number
  grade: string
  class_name: string
  total_targets: number
  completed_targets: number
  completion_rate: number
}

export interface ComparisonItem {
  dimension_code: string
  score: number
  level: string
  max_score: number
  /** `null` = 同班样本太小，扣住不给（见后端 `MIN_COHORT_FOR_AGGREGATE`）。 */
  class_average: number | null
  class_size: number
  grade_average: number | null
  grade_size: number
}

export interface ClassComparison {
  grade_name: string | null
  class_name: string | null
  session_id: number | null
  submitted_at: string | null
  items: ComparisonItem[]
}

export interface LeaderProgressItem {
  case_id: number
  student_id: number
  student_name: string
  student_no: string
  grade: string
  class_name: string
  case_status: string
  total_level: string | null
  validity_status: string | null
  owner_id: number | null
  owner_name: string | null
  next_follow_up_date: string | null
  overdue: boolean
  opened_at: string | null
  updated_at: string | null
}

export async function getAnalyticsOverview(): Promise<AnalyticsOverview> {
  return apiRequest<AnalyticsOverview>('/analytics/overview')
}

export async function getAnalyticsByGrade(): Promise<GradeAnalytics[]> {
  const data = await apiRequest<{ items: GradeAnalytics[] }>('/analytics/by-grade')
  return data.items
}

export async function getAnalyticsByClass(): Promise<ClassAnalytics[]> {
  const data = await apiRequest<{ items: ClassAnalytics[] }>('/analytics/by-class')
  return data.items
}

export interface DimensionDistributionItem {
  dimension_code: string
  assessed_count: number
  high_count: number
  high_rate: number
  average_score: number | null
  /** Item count behind this dimension — the denominator for average_score. */
  max_score: number
}

export async function getDimensionDistribution(): Promise<DimensionDistributionItem[]> {
  const data = await apiRequest<{ items: DimensionDistributionItem[] }>('/analytics/dimensions')
  return data.items
}

export interface ReminderItem {
  kind: string
  when: string
  overdue: boolean
  title: string
  desc: string
  student_id: number
}

/**
 * 工作台「近期提醒」。
 *
 * 这里的封装函数**不拆成裸数组**——与 `getAuditLogs` 同一种形状（CLAUDE.md §10 的
 * 第二个例外，2026-09-17 加）。每个来源在服务端封顶 20 条，所以 `items.length`
 * 回答不了「我有多少待办」：一个老师手上 40 条时它也说 20。`total` 是服务端
 * 另算的一次 COUNT，`truncated` 由两者比出来。面板据此说「另有 N 项未显示」。
 */
export interface ReminderPage {
  items: ReminderItem[]
  total: number
  truncated: boolean
}

export async function getCounselorReminders(): Promise<ReminderPage> {
  const data = await apiRequest<ReminderPage>('/counselor/reminders')
  return { items: data.items, total: data.total, truncated: data.truncated }
}

export async function getLeaderProgress(): Promise<LeaderProgressItem[]> {
  const data = await apiRequest<{ items: LeaderProgressItem[] }>('/leader/progress')
  return data.items
}

// --- 测评任务 ---
export interface AssessmentTaskItem {
  id: number
  task_no: string
  name: string
  status: string
  scope_type: string
  start_at: string | null
  end_at: string | null
  total_targets: number
  completed_targets: number
  completion_rate: number
  /** IN_SYSTEM / IMPORTED。批次任务是 IMPORTED，只有它需要打标。 */
  source: string
}

export interface TaskCompletionItem {
  student_no: string
  student_name: string
  grade: string
  class_name: string
  gender: string | null
  age: number | null
  status: string
  assigned_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  /**
   * **这一场**的结果，不是「每人最近一场」（CLAUDE.md §11 的「按场」口径）。
   * 同一名学生落在两个批次里时，两边的明细各显示各的——一份批次报表不该说别的
   * 批次的事。`null` = 他这一场还没交卷，界面出「未测评」/「—」。
   */
  total_level: string | null
  total_score: number | null
}

export async function getAssessmentTasks(): Promise<AssessmentTaskItem[]> {
  const data = await apiRequest<{ items: AssessmentTaskItem[] }>('/assessment-tasks')
  return data.items
}

export async function createAssessmentTask(payload: {
  name: string
  start_at: string
  end_at: string
}): Promise<{ id: number; task_no: string }> {
  return apiRequest<{ id: number; task_no: string }>('/assessment-tasks', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function updateAssessmentTask(
  taskId: number,
  payload: { name?: string; start_at?: string; end_at?: string }
): Promise<{ id: number; name: string }> {
  return apiRequest<{ id: number; name: string }>(`/assessment-tasks/${taskId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload)
  })
}

export async function getTaskCompletion(taskId: number): Promise<TaskCompletionItem[]> {
  const data = await apiRequest<{ items: TaskCompletionItem[] }>(`/assessment-tasks/${taskId}/completion`)
  return data.items
}

// --- 量表版本 ---
export interface ScaleVersion {
  id: number
  code: string
  name: string
  version: string
  status: string
  published_at: string | null
  total_questions: number
  content_questions: number
  validity_questions: number
  key_questions: number
  rule_version: string | null
}

export async function getScaleVersions(): Promise<ScaleVersion[]> {
  const data = await apiRequest<{ items: ScaleVersion[] }>('/scales/versions')
  return data.items
}

// --- 量表评分规则 ---
export interface ScoreBand {
  code: string
  min: number
  max: number
}

export interface ScaleRuleConfig {
  question_count: number
  yes_value: string
  no_value: string
  validity_questions: number[]
  key_questions: number[]
  validity_retest_threshold: number
  total_levels: ScoreBand[]
  dimension_levels: ScoreBand[]
  interpretations: Record<string, string>
}

export interface ScaleRuleSnapshot {
  scale_id: number
  scale_version: string
  scale_status: string
  rule_version: string | null
  rule_status: string | null
  /** True only while nothing has been scored against this rule. */
  editable_in_place: boolean
  config: ScaleRuleConfig
  defaults: ScaleRuleConfig
}

/** Promote a draft version to PUBLISHED. Administrator only. */
export async function publishScaleVersion(
  scaleId: number
): Promise<{ version: string; status: string; archived_versions: string[] }> {
  return apiRequest(`/scales/versions/${scaleId}/publish`, { method: 'POST' })
}

export async function getScaleRule(scaleId: number): Promise<ScaleRuleSnapshot> {
  return apiRequest<ScaleRuleSnapshot>(`/scales/versions/${scaleId}/rule`)
}

export async function updateScaleRule(
  scaleId: number,
  payload: { validity_retest_threshold?: number; total_levels?: ScoreBand[]; dimension_levels?: ScoreBand[] }
): Promise<{ rule_version: string; created_new_version: boolean; config: ScaleRuleConfig }> {
  return apiRequest(`/scales/versions/${scaleId}/rule`, {
    method: 'PUT',
    body: JSON.stringify(payload)
  })
}

export async function resetScaleRule(
  scaleId: number
): Promise<{ rule_version: string; created_new_version: boolean }> {
  return apiRequest(`/scales/versions/${scaleId}/rule/reset`, { method: 'POST' })
}

// --- 系统配置 ---
export type SettingsNamespace = 'org' | 'care' | 'export' | 'cadence' | 'ui'

export interface SystemSettings {
  org: {
    school_name: string
    brand_name: string
    brand_subtitle: string
    counselling_room: string
    counselling_hours: string
    counselling_contact: string
  }
  care: {
    follow_up_types: string[]
    contact_channels: string[]
    contact_results: string[]
    support_statuses: string[]
    close_reasons: string[]
    retest_reasons: string[]
    review_results: string[]
    review_actions: string[]
    case_owner_fallback: string
  }
  export: {
    purposes: string[]
    key_question_reasons: string[]
  }
  cadence: {
    follow_up_days: number
    family_contact_days: number
    retest_days: number
    task_duration_days: number
    reminder_horizon_days: number
  }
  ui: {
    low_completion_threshold: number
    dimension_high_threshold: number
    seconds_per_question: number
    page_size_default: number
  }
}

export interface SettingsResponse {
  values: SystemSettings
  /** Shipped defaults, so the UI can mark and revert customised values. */
  defaults: SystemSettings
}

export interface Branding {
  school_name: string
  brand_name: string
  brand_subtitle: string
  /** 显示用的版本标签（`V1.0`）。服务端算的，前端不写死：部署包里 `frontend/dist`
   *  是预构建的，写死会造出「后端升了、界面还说旧版本」的静默分岔。 */
  version: string
}

/** Branding only — readable before login, because the login screen shows it. */
export async function getBranding(): Promise<Branding> {
  return apiRequest<Branding>('/public/branding')
}

export async function getSystemSettings(): Promise<SettingsResponse> {
  return apiRequest<SettingsResponse>('/admin/settings')
}

export async function updateSystemSettings<K extends SettingsNamespace>(
  namespace: K,
  values: Partial<SystemSettings[K]>
): Promise<{ namespace: string; values: SystemSettings[K] }> {
  return apiRequest<{ namespace: string; values: SystemSettings[K] }>(
    `/admin/settings/${namespace}`,
    { method: 'PUT', body: JSON.stringify({ values }) }
  )
}

export async function resetSystemSettings(
  namespace: SettingsNamespace
): Promise<{ namespace: string; values: SystemSettings[SettingsNamespace] }> {
  return apiRequest(`/admin/settings/${namespace}/reset`, { method: 'POST' })
}

// --- 角色权限矩阵 ---
export interface PermissionMatrixRow {
  capability_key: string
  label: string
  roles: Record<string, string>
}

export interface PermissionMatrix {
  items: PermissionMatrixRow[]
  scope_labels: Record<string, string>
  /** 每项能力**能用**的等级，从紧到松排列（下标即宽松度，前端据此判断提权）。 */
  capability_levels: Record<string, string[]>
  /** 每个等级在这项能力下的中文释义，与 capability_levels 一一对应。 */
  level_descriptions: Record<string, Record<string, string>>
  /** 出厂矩阵。`items` 给的是**生效值**，分不出哪一格是学校自己配的、哪一格是
   *  回退来的；标「已改」和「恢复默认」都要靠这一份。 */
  defaults: Record<string, Record<string, string>>
}

export async function getPermissionMatrix(): Promise<PermissionMatrix> {
  return apiRequest<PermissionMatrix>('/admin/permissions')
}

export async function updatePermissionMatrix(
  entries: Array<{ role_code: string; capability_key: string; scope_level: string }>
): Promise<PermissionMatrix> {
  return apiRequest<PermissionMatrix>('/admin/permissions', {
    method: 'PUT',
    body: JSON.stringify({ entries })
  })
}

export interface AccountItem extends CurrentUser {}

/**
 * 「新建账号 / 编辑账号」里那一个数据范围选项。
 *
 * 服务端把**学校本身**也做成一个选项（而不是让创建接口去猜是哪所学校）——单校部署里
 * 那就是「全校」那一行。界面把选中的这一项**原样发回去**，中间不做翻译。
 */
export interface ScopeOption {
  scope_type: string
  scope_id: number
  name: string
}

export interface AccountScopeInput {
  scope_type: string
  scope_id: number
}

export interface CreateAccountPayload {
  role_code: string
  display_name: string
  account: string
  temporary_password: string
  scopes: AccountScopeInput[]
}

/** 三样都可以单独发：只改姓名时**不要**带上 `scopes`（见 `AdminSystemPage` 的编辑）。 */
export interface UpdateAccountPayload {
  display_name?: string
  scopes?: AccountScopeInput[]
  active?: boolean
}

export interface StudentItem {
  id: number
  student_no: string
  name: string
  grade: string
  class_name: string
  gender: string | null
  age: number | null
  status: string
}

/**
 * 名册冲突 —— 这一行本身没问题，只是它说的学号已经在名册上了。
 *
 * 与 `errors` 分开：错误只能改文件重导，冲突有一条以上的出路（覆盖 / 放弃），
 * 所以要由操作员拍板。2026-09-17 之前「重复学号」是错误，于是学号撞了的行
 * 只能整行作废、没有任何办法把它改成「更新这名学生」。
 */
export interface StudentImportConflict {
  type: 'STUDENT_NO_EXISTS'
  message: string
  /** 库里那一份的姓名——面板要写出「覆盖会把他从 X 改成 Y」，只说「冲突」等于没说。 */
  existing_name: string
  existing_student_id: number
}

export interface StudentImportPreviewRow {
  row_no: number
  student_no: string
  name: string
  grade: string
  class_name: string
  /** 选填列。老模板没有它们，预览行里也就没有这两个键。 */
  gender?: string
  age?: string
  errors: string[]
  conflicts: StudentImportConflict[]
}

export interface StudentImportPreview {
  total: number
  valid_count: number
  conflict_count: number
  error_count: number
  rows: StudentImportPreviewRow[]
  preview_token: string | null
}

/** 与测评导入共用两个码（后端也是同一套常量）。含义不同：那边「覆盖」替换上次导入的那一场。 */
export type StudentImportResolution = 'overwrite' | 'skip'

export async function getAccounts(): Promise<AccountItem[]> {
  const data = await apiRequest<{ items: AccountItem[] }>('/admin/accounts')
  return data.items
}

export async function resetAccountPassword(accountId: number, temporaryPassword: string, purpose: string): Promise<string> {
  const data = await apiRequest<{ temporary_password: string }>(`/admin/accounts/${accountId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ temporary_password: temporaryPassword, purpose })
  })
  return data.temporary_password
}

export async function getScopeOptions(): Promise<ScopeOption[]> {
  const data = await apiRequest<{ items: ScopeOption[] }>('/admin/accounts/scope-options')
  return data.items
}

/**
 * 新建员工账号（心理老师 / 德育领导 / 系统管理员）。
 *
 * 临时密码由操作员给定，**服务端不回传**它——它在弹层里显示的仍是操作员自己敲进去的
 * 那一个。学生账号不在这里建：那条路是「组织学生 → 学生信息导入」，
 * 学生账号与名册行是同一笔事务里的两行（服务端会把 `student` 挡回来）。
 */
export async function createAccount(payload: CreateAccountPayload): Promise<AccountItem> {
  return apiRequest<AccountItem>('/admin/accounts', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

/** 改姓名 / 换范围 / 停用启用。「账号」与「角色」不可改，所以这份请求里没有它们。 */
export async function updateAccount(accountId: number, payload: UpdateAccountPayload): Promise<AccountItem> {
  return apiRequest<AccountItem>(`/admin/accounts/${accountId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload)
  })
}

export async function getStudents(): Promise<StudentItem[]> {
  const data = await apiRequest<{ items: StudentItem[] }>('/students')
  return data.items
}

/**
 * 名册上每一名学生 + 他**最近一场**测评的结果（`GET /students/results`）。
 *
 * 与 `StudentItem` 是**两个不同的东西**，别合并：`/students` 是名册（没有测评结果，
 * 只有身份列），`/students/results` 是名册加一列「他最近测出了什么」。
 * 合并的话，所有只需要名册的调用方都会跟着拿到等级列，而那两个端点的权限门槛不同
 * ——这一个是双门槛（名册 + 心理详情），见 CLAUDE.md §4。
 *
 * `total_level` 为 `null` 表示**未测评**（没交过卷），界面必须显示「未测评」而不是
 * 空单元格，也不能当成「一般观察」（`levelLabel` 已按这条约定处理 `null`）。
 * `source` 跟着结果走：没有结果时它是 `null`，不是 `IN_SYSTEM`
 * ——「没有任何测评」与「测评是系统内做的」不是一回事。
 */
export interface StudentResultItem {
  student_id: number
  student_no: string
  student_name: string
  grade: string
  class_name: string
  gender: string | null
  age: number | null
  total_level: string | null
  total_score: number | null
  submitted_at: string | null
  source: string | null
  /** 有档案才有值；界面上的「查看档案」只在它非空时渲染。已关闭的档案也算有。 */
  case_id: number | null
  case_status: string | null
}

export async function getStudentResults(): Promise<StudentResultItem[]> {
  const data = await apiRequest<{ items: StudentResultItem[] }>('/students/results')
  return data.items
}

export async function previewStudentImport(file: File): Promise<StudentImportPreview> {
  const form = new FormData()
  form.append('file', file)
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/students/import/preview`, { method: 'POST', headers, body: form })
  const body = await response.json()
  if (!response.ok || !body.success) throw new Error(body.error?.message || '导入预览失败')
  return body.data as StudentImportPreview
}

export async function commitStudentImport(
  previewToken: string,
  resolution?: StudentImportResolution
): Promise<{ created: number; updated: number; skipped: number }> {
  return apiRequest<{ created: number; updated: number; skipped: number }>('/students/import/commit', {
    method: 'POST',
    // `resolution` 只在文件里真的有冲突时才必需；没有它时后端不会问，
    // 传 `undefined` 会被 JSON.stringify 整个丢掉，正是要的。
    body: JSON.stringify({ preview_token: previewToken, resolution })
  })
}

/**
 * 外部平台的 MHT 普查结果导入（数据中心 → MHT测评记录导入）。
 *
 * 与另两个导入同形：预览（`preview_token` + 逐行 errors/warnings），确认后提交令牌。
 * 批次名称与测评日期是**请求级**的字段，不在文件里，所以随预览一起提交，
 * 由后端签进令牌——确认导入时只发令牌，操作员改不了已在预览里确认过的那两项。
 */
/**
 * 待确认项 —— 记录本身是好的，难的是「以文件为准还是以库里为准」。
 *
 * 两类：`AGE_MISMATCH`（文件里的年龄与名册不符）与 `DUPLICATE`（同一个月里这个
 * 学生已经有导入记录）。一行可能同时带两条，一次处置（覆盖 / 放弃）同时回答它们。
 */
export interface AssessmentImportConflict {
  type: 'AGE_MISMATCH' | 'DUPLICATE'
  message: string
  /** 名册上那个数与文件里那个数（AGE_MISMATCH）。 */
  roster_age?: number
  file_age?: number
  /** 上一次导入的那一场（DUPLICATE）。 */
  existing_session_id?: number
  existing_tested_on?: string | null
}

export interface AssessmentImportPreviewRow {
  row_no: number
  name: string
  grade: string
  class_name: string
  /** 文件里的 1/2 已归一化成 MALE / FEMALE；填错或没填时为 null。 */
  gender: string | null
  age: number | null
  duration_seconds: number | null
  matched_student_no: string | null
  matched_name: string | null
  errors: string[]
  warnings: string[]
  conflicts: AssessmentImportConflict[]
}

export interface AssessmentImportPreview {
  batch: { name: string; tested_on: string }
  total: number
  /** 不需要任何决定的行。有冲突的行**不**算在里面。 */
  valid_count: number
  /** 需要操作员选「覆盖」或「放弃」的行数。 */
  conflict_count: number
  error_count: number
  warning_count: number
  /** 列级/批次级问题。非空时不发预览令牌，逐行的 errors 也就无从谈起。 */
  global_errors: string[]
  rows: AssessmentImportPreviewRow[]
  preview_token: string | null
}

/** 导入的处置方式：覆盖上次 / 放弃这几条。整份文件共用一次选择。 */
export type AssessmentImportResolution = 'overwrite' | 'skip'

export async function previewAssessmentImport(
  file: File,
  batchName: string,
  testedOn: string
): Promise<AssessmentImportPreview> {
  const form = new FormData()
  form.append('file', file)
  form.append('batch_name', batchName)
  form.append('tested_on', testedOn)
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/assessment-import/preview`, { method: 'POST', headers, body: form })
  const body = await response.json()
  if (!response.ok || !body.success) throw new Error(body.error?.message || '导入预览失败')
  return body.data as AssessmentImportPreview
}

export interface AssessmentImportResult {
  /**
   * `null` 表示这次**一条都没写进去**（全部冲突行都选了「放弃」），此时后端不建批次任务
   * ——空任务在列表上永远显示「进行中」，还会被同月的下一次导入复用。
   */
  task_id: number | null
  /** 同上：没有批次任务时是空串，所以别写死「（批次 ${task_no}）」。 */
  task_no: string
  created: number
  /** 「覆盖上次」就地重写的条数（`conflicts` 里有 DUPLICATE 的那些）。 */
  updated: number
  skipped: number
  /** 按文件的年龄写回名册的条数（`conflicts` 里有 AGE_MISMATCH 的那些）。 */
  age_updated: number
}

/**
 * `resolution` 只在文件里**真的有冲突**时才是必需的（后端会以 422 回一句中文，
 * 前端把它原样弹出来）。没有冲突的文件照旧一次提交——要求 99% 的正常导入先回答
 * 一个不该问的问题，只会让人乱点。
 */
export async function commitAssessmentImport(
  previewToken: string,
  resolution?: AssessmentImportResolution
): Promise<AssessmentImportResult> {
  return apiRequest<AssessmentImportResult>('/assessment-import/commit', {
    method: 'POST',
    body: JSON.stringify(
      resolution ? { preview_token: previewToken, resolution } : { preview_token: previewToken }
    )
  })
}

/**
 * 模板由服务端生成：106 列的列名与取值约定（`1/2`、`1/0`）都归它一处定义，
 * 前端再抄一遍就会漂移——而这份文件是学校填表的依据，抄错等于教错。
 */
export async function downloadAssessmentTemplate(): Promise<void> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/assessment-import/template`, { headers })
  if (!response.ok) throw new Error('模板下载失败')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'mht-assessment-import-template.csv'
  anchor.click()
  URL.revokeObjectURL(url)
}

export interface ScaleImportPreviewRow {
  row_no: number
  question_no: number
  question_text: string
  dimension_code: string
  is_validity_question: boolean
  is_key_question: boolean
  errors: string[]
}

export interface ScaleImportPreview {
  total: number
  valid: boolean
  global_errors: string[]
  rows: ScaleImportPreviewRow[]
  preview_token: string | null
}

export async function previewScaleImport(file: File): Promise<ScaleImportPreview> {
  const form = new FormData()
  form.append('file', file)
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/scales/import/preview`, { method: 'POST', headers, body: form })
  const body = await response.json()
  if (!response.ok || !body.success) throw new Error(body.error?.message || '题库预览失败')
  return body.data as ScaleImportPreview
}

export async function createScaleDraft(
  previewToken: string,
  version: string
): Promise<{ scale_id: number; version: string; status: string }> {
  return apiRequest<{ scale_id: number; version: string; status: string }>('/scales/drafts', {
    method: 'POST',
    body: JSON.stringify({ preview_token: previewToken, version, name: '中学生心理健康测验' })
  })
}

export interface AuditLogItem {
  id: number
  actor_user_id: number | null
  actor_role: string | null
  /** 操作人的姓名与账号，服务端按 actor_user_id **读取时**现取。未登录的行为
   *  （登录失败）两者都是 null——被尝试的账号在 resource_id 上，不在这一列。 */
  actor_name: string | null
  actor_account: string | null
  action: string
  resource_type: string
  resource_id: string | null
  purpose: string | null
  result: string
  created_at: string | null
}

export interface AuditLogPage {
  items: AuditLogItem[]
  total: number
}

export interface AuditLogQuery {
  limit?: number
  offset?: number
  sort?: string
  order?: 'asc' | 'desc'
  q?: string
  actorRole?: string
}

/**
 * Server-paged: the audit trail is append-only and unbounded.
 *
 * Every filter is sent to the server so `total` describes the filtered set —
 * filtering on the client would only ever filter the page in hand.
 */
export async function getAuditLogs(query: AuditLogQuery = {}): Promise<AuditLogPage> {
  const params = new URLSearchParams()
  if (query.limit != null) params.set('limit', String(query.limit))
  if (query.offset != null) params.set('offset', String(query.offset))
  if (query.sort) params.set('sort', query.sort)
  if (query.order) params.set('order', query.order)
  if (query.q) params.set('q', query.q)
  if (query.actorRole) params.set('actor_role', query.actorRole)
  const suffix = params.toString() ? `?${params}` : ''
  const data = await apiRequest<AuditLogPage>(`/audit-logs${suffix}`)
  return { items: data.items, total: data.total }
}

async function downloadCsv(path: string, filename: string, payload: unknown): Promise<void> {
  const headers = new Headers()
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload)
  })
  if (!response.ok) {
    const body = await response.json()
    throw new Error(body.error?.message || '导出失败')
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

/**
 * 任务完成明细的 CSV。
 *
 * 这个端点一直在（`GET /assessment-tasks/{id}/completion/export`），但界面从来没有
 * 入口——于是一所 1000 人学校的普查明细只能在 340px 高的弹层里滚。明细表格因此
 * 只渲染前若干行（见 `TasksPage` 的 `DETAIL_RENDER_LIMIT`），而「全部」的出路就是
 * 这里：截断必须有一条出路，否则就是静默丢数据。
 *
 * 与 `downloadCsv` 分开写是因为那个 helper 走 POST 带 body，而这一个是 GET。
 */
export async function downloadTaskCompletionCsv(taskId: number, filename: string): Promise<void> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/assessment-tasks/${taskId}/completion/export`, {
    headers
  })
  if (!response.ok) {
    const body = await response.json()
    throw new Error(body.error?.message || '导出失败')
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export interface ExportOptions {
  purpose: string
  /** Defaults to true server-side. When false the CSV keeps full names. */
  maskNames?: boolean
  /** When true the CSV adds the MHT total score column. */
  includeScore?: boolean
  /** Restrict the export to these students. Omit to export the whole cohort. */
  studentIds?: number[]
}

/** Counselors list the assignable owners for the batch-assign dialog. */
export async function getAssignableOwners(): Promise<AssignableOwner[]> {
  const data = await apiRequest<{ items: AssignableOwner[] }>('/care-cases/assignable-owners')
  return data.items
}

export async function batchAssignOwner(caseIds: number[], ownerId: number): Promise<{ updated: number }> {
  return apiRequest<{ updated: number }>('/care-cases/batch-assign', {
    method: 'POST',
    body: JSON.stringify({ case_ids: caseIds, owner_id: ownerId })
  })
}

export async function exportCareCases(options: ExportOptions): Promise<void> {
  const { studentIds, ...rest } = options
  if (studentIds && studentIds.length > 0) {
    await downloadCsv('/care-cases/export', 'care-cases.csv', { ...rest, student_ids: studentIds })
  } else {
    await downloadCsv('/care-cases/export', 'care-cases.csv', rest)
  }
}

export async function exportHighRiskCareCases(options: ExportOptions): Promise<void> {
  const { studentIds: _ignored, ...rest } = options
  await downloadCsv('/care-cases/high-risk/export', 'high-risk-care-cases.csv', rest)
}

/** Single-student controlled export — the prototype's `exportOne`. */
export async function exportCareCase(studentId: number, options: ExportOptions): Promise<void> {
  const { studentIds: _ignored, ...rest } = options
  await downloadCsv(`/care-cases/${studentId}/export`, 'care-case.csv', rest)
}

/**
 * Key-question answers (MHT 85 / 97). Highest sensitivity tier: the server
 * requires a stated purpose and writes an audit row before returning.
 */
export async function getKeyQuestionAnswers(
  studentId: number,
  purpose: string
): Promise<Array<{ question_no: number; answer: string }>> {
  const data = await apiRequest<{ items: Array<{ question_no: number; answer: string }> }>(
    `/students/${studentId}/key-questions?purpose=${encodeURIComponent(purpose)}`
  )
  return data.items
}
