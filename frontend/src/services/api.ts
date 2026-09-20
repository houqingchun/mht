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

/** 服务端统一封装的形状：`{ success, data, request_id, error }`。 */
interface Envelope {
  success?: boolean
  data?: unknown
  error?: { message?: string }
}

/**
 * 读统一封装，**响应体不是 JSON 时也说人话**。
 *
 * `response.json()` 在不合法的 JSON 上抛 `SyntaxError`，而那一支恰恰是「服务端答了话、
 * 但答的不是它约定的那种话」：后端崩在一个没有被 `AppError` 收住的异常上时，Starlette
 * 的兜底处理器回的是**纯文本** `Internal Server Error`（`content-type: text/plain`）。
 * 于是屏幕上出现的是浏览器原话 `JSON.parse: unexpected character at line 1 column 1 of
 * the JSON data` —— 它一个字都没提到真正的原因。
 *
 * §2 那条「服务端答了话」与「一个字都没收到」分得开，在这里就是这一支：`fetch` 自己
 * 失败仍然是 `TypeError`（后端没起来、网断了），走到这里说明对方确实答了，只是答得
 * 读不懂。带上的状态码是这一支唯一能给出的线索——调用方拿不到它（§2 的既有约定），
 * 所以它只能出现在这句话里。
 */
async function readEnvelope(response: Response): Promise<Envelope> {
  try {
    return (await response.json()) as Envelope
  } catch {
    throw new Error(
      `服务端返回了无法解析的内容（HTTP ${response.status}），请联系管理员查看服务端日志`
    )
  }
}

export async function apiRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  const body = await readEnvelope(response)
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
  /**
   * PENDING / CALCULATING / CALCULATED / CALCULATION_FAILED，或 null（这一行没有会话）。
   *
   * 分数仍然不下发给学生（见后端 `list_student_assessment_history` 的说明），
   * 这一列说的是「这份答卷处理完了没有」——一名交完卷的学生看到「已完成」而学校里
   * 这一场根本没算出来，他会以为一切都好。
   */
  calculation_status: string | null
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

export async function submitAssessmentSession(sessionId: number): Promise<SessionOutcome> {
  return apiRequest<SessionOutcome>(`/assessment-sessions/${sessionId}/submit`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': `submit-${sessionId}`
    }
  })
}

/**
 * 一场测评「处理到哪一步了」——交卷与重算返回的是同一个形状
 * （后端 `assessment_service.result_payload`）。
 *
 * **只列了界面真的会读的那几项**，不是这份响应的全部字段：多列一个没人读的字段，
 * 下一次改后端形状时它就成了一个假承诺。要加字段先问一句「谁读它」。
 */
export interface SessionOutcome {
  session_id: number
  /** 会话自己的状态（IN_PROGRESS / SUBMITTED …），**不是**评分状态。 */
  status: string
  calculation_status: string
  /** 失败原因原文（后端截断到 1000 字）。算成功时为 null。 */
  calculation_error: string | null
  submitted_at: string | null
  tested_at: string | null
  tested_at_source: string | null
  /**
   * 结果本身。**评分失败时它是 null，而 `calculation_status` 说为什么**——
   * 所以判断「这一场有没有分」要看这个字段，不要看 `status`：一份没算出来的答卷
   * 仍然是一份交过的答卷。
   */
  result: { total_level: string; rule_version: string } | null
}

/**
 * 重算一场「没算出来」的答卷 —— 个案详情上那个「重算评分」按钮。
 *
 * 这是心理老师的动作（后端 `require_role(COUNSELOR)`），而且是一次**敏感读取**：
 * 重算要读满整份答卷，所以每一次都写审计，并挂在这名学生名下。
 * `recalculated: false` 不是失败——它说的是「已经有结果了，这一次什么都没写」。
 */
export async function retrySessionCalculation(sessionId: number): Promise<SessionOutcome & { recalculated: boolean }> {
  return apiRequest<SessionOutcome & { recalculated: boolean }>(
    `/assessment-sessions/${sessionId}/calculate`,
    { method: 'POST' }
  )
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
  /** 乐观锁版本号（§16.4）——「批量分配」逐行带回去的就是它。 */
  case_version: number
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

/**
 * 一名学生的身份列。
 *
 * 抽出来是因为它出现在**两个**响应里：`CareCaseDetail`（有档案的学生）与
 * `StudentAssessmentRecords`（不需要他有档案）。两者说的是同一个人、同一批列，
 * 各写一份必然漂——而 TS 看不见这种漂移，它只会在某一个屏幕上少一个字段。
 * 这与后端把 `student` / `assessment` / `dimensions` / `history` 四块抽进
 * `care_service.student_assessment_records` 是同一个动作的两侧。
 */
export interface StudentIdentity {
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

/**
 * 「最近一次测评」的概览——**算数的那一场**。
 *
 * 走 `assessment_service.latest_session`：施测时间最近，且要求 `is_effective`。
 * 这与 `history` 的最后一行**是两件事**，见下面 `total_score` 那一段。
 */
export interface AssessmentOverview {
  session_id: number | null
  submitted_at: string | null
  duration_seconds: number | null
  total_level: string | null
  validity_status: string | null
  rule_version: string | null
  /** IN_SYSTEM / IMPORTED，或 null（还没有任何一场测评）。 */
  source: string | null
  /**
   * 这一场算出来了没有 —— PENDING / CALCULATING / CALCULATED / CALCULATION_FAILED。
   *
   * 与上面几项**不是一回事**：`total_level` / `validity_status` / `rule_version` 都来自
   * `assessment_result` 那一行，评分没成功时它们全是 null，而 null 在界面上长得与
   * 「还没测」一模一样。这一列是那两者之间唯一的区别，也是「重算评分」按钮的判据。
   */
  calculation_status: string | null
  /** 失败原因原文（后端已截断）。算成功或还没算时为 null。 */
  calculation_error: string | null
  /**
   * 这一场**真实发生**的日期，与 `submitted_at` 分开。
   *
   * 在线作答时两者相同（交卷那一刻就是测评那一刻）；外部导入时 `submitted_at` 也是
   * 文件里的那格日期，所以今天它们总是一样。但趋势是按这个字段排的，而
   * `tested_at_source` 回答的正是「这个日期是谁给的」——历史行两者都是 null
   * （0013 刻意没有回填，见 CLAUDE.md §21），那时界面只能照实说「待核实」。
   */
  tested_at: string | null
  /** ONLINE_SUBMIT / IMPORT_FILE / PENDING_VERIFICATION，或 null。 */
  tested_at_source: string | null
  /**
   * 这一场的原始总分。
   *
   * **档案页的概览卡不显示它**（那一页回答的是「他属于哪一档」），
   * 「学生测评记录」页的页头写「最近一次 · 一般观察 · 总分 24」用的就是它。
   *
   * **不能改成从 `history` 的最后一行取。** `history` 刻意不含
   * `effective_session_predicate()`（被 §18.8 降级的那一场仍然是他真实考过的一次，
   * 趋势图少一个点就是在抹掉一段事实），所以它的最后一行**未必是有效的那一场**——
   * 拿它填「最近一次」会在这些学生上给出另一场的分，而屏幕上一切正常。
   */
  total_score: number | null
}

/** 一个维度的得分，连同它的分母。 */
export interface DimensionScore {
  dimension_code: string
  score: number
  level: string
  interpretation: string
  /** Item count behind this dimension — the denominator for `score`. */
  max_score: number
}

/** 历次测评里的一场，含**这一场自己的**八维度分。 */
export interface AssessmentHistoryEntry {
  session_id: number
  submitted_at: string | null
  duration_seconds: number | null
  total_score: number | null
  total_level: string | null
  validity_status: string | null
  /**
   * 这一场是学生在本系统里做的，还是学校从外部平台导入的。**逐场给**：一名学生的
   * 历次记录可以一半在线、一半导入（缺口 8），而「用时 5340 秒」在两种来源下是
   * 两件事——导入的那一场没有本系统的作答过程，用时是那个平台自己报的数。
   */
  source: string | null
  /**
   * 这一场自己的八维度分。`max_score` 是画图用的分母：各维度题数不等（10 或 15），
   * 不归一化会让 15 题的身体症状在图上凭空压过 10 题的孤独倾向。
   */
  dimensions: Array<{
    dimension_code: string
    score: number
    max_score: number
  }>
}

/**
 * 「学生测评记录」的响应体 —— **不要求这名学生有档案**。
 *
 * 全库唯一的开档触发点是重点题 85 / 97 命中，所以一个被评成「需要关注」、甚至
 * 「重点关注」的学生照样可能没有档案；在此之前 `GET /care-cases/{student_id}`
 * 是唯一能读到「他考过几次、每次多少分」的接口，而它对无档案的学生回 404。
 *
 * 四个块与 `CareCaseDetail` 里同名的四个**是同一份数据**（后端同一个函数装配），
 * 所以这里直接复用那四个类型，而不是各写一份。
 */
export interface StudentAssessmentRecords {
  student: StudentIdentity
  assessment: AssessmentOverview
  dimensions: DimensionScore[]
  /** 历次测评，**从旧到新**（后端按施测时间升序排，见 `session_history_order`）。 */
  history: AssessmentHistoryEntry[]
  /**
   * 这名学生**当前**那份档案的 id，没有档案时为 `null`。
   *
   * 这一页存在的理由就是「没有档案的学生也要看得到」，所以「他到底有没有档案」是它
   * 必须自己答的一个问题：底部那句「该生尚未建档」与「查看关注档案」那枚按钮都读它。
   *
   * **不要改成「拉一次档案详情、看它是不是 404」。** 前端拿不到 HTTP 状态码（§2），
   * 而那一次请求会在轨迹里多写一条「查看学生详情」——他没看档案，只是想知道有没有，
   * 那条访问记录就成了一句假话。后端取的是 `current_care_case`，与档案页同一个定义：
   * 有档案的行点进去一定打得开，反之亦然。
   */
  case_id: number | null
}

export interface CareCaseDetail {
  case_id: number
  student: StudentIdentity
  case_status: string
  /**
   * 乐观锁版本号（§16.4）。
   *
   * **它必须出现在这个响应里**：关闭 / 重新打开都要求客户端把读到的版本带回去，
   * 而客户端唯一拿得到它的地方就是这里。列表页走 `CareCaseItem.case_version`，
   * 那是另一条路（批量分配逐行带号用）。
   */
  case_version: number
  assessment: AssessmentOverview
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
  dimensions: DimensionScore[]
  /** 历次测评，**从旧到新**（后端按施测时间升序排，见 `session_history_order`）。 */
  history: AssessmentHistoryEntry[]
  /**
   * 档案事件时间线（§16.4），**最新在前**（后端按 `id.desc()` 排）。
   *
   * 它与 `audit_logs` 是两件事，也是这一页上的两个页签——审计回答「谁在什么时候
   * 调了哪个接口」，事件回答「这份档案经历了什么」。**按 `care_case_id` 过滤，
   * 不像上面那几个列表那样按学生跨档案取**：这条时间线要读得出「这一份」的边界。
   */
  events: Array<{
    id: number
    /** `CARE_EVENT_LABELS` 的八个码之一（labels.ts）。 */
    event_type: string
    from_status: string | null
    to_status: string | null
    /** 操作人姓名；**开档那一条为 null**——它是系统自动开的（界面显示「系统」）。 */
    operator_name: string | null
    reason: string | null
    /**
     * 复核的结论原文。**只有人工复核那一条有值**：家庭回访的正文归
     * `family_contacts[].confirmed_facts`，§16.6 明令不在这里存一份副本。
     */
    confirmed_facts: string | null
    created_at: string | null
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
 * 一名学生的测评记录——**不需要他有档案**。
 *
 * `getCareCaseDetail` 对没有档案的学生回 404「关注档案不存在」，而前端拿不到 HTTP
 * 状态码（§2），于是「重点学生」列表上那一行只显示一个 `—`：心理老师真的没有入口
 * 看到这个学生。这一条补的就是那个入口。
 *
 * 门槛与档案详情**逐字相同的一道**（`STUDENT_PSYCH_DETAIL: {SCOPED}` + 数据范围），
 * 所以这两个调用对一个 403 的反应也一样。
 */
export async function getStudentAssessmentRecords(studentId: number): Promise<StudentAssessmentRecords> {
  return apiRequest<StudentAssessmentRecords>(`/students/${studentId}/assessment-records`)
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

/**
 * 关闭档案。`case_version` 是乐观锁（§16.4），取自 `CareCaseDetail.case_version`
 * ——服务端在版本对不上时回 409 与一句人话，而不是让数据库用一句英文回答用户。
 */
export async function closeCareCase(
  caseId: number,
  payload: {
    close_reason: string
    close_note: string
    confirm_follow_up_checked: boolean
    case_version: number
  }
): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/close`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function reopenCareCase(
  caseId: number,
  payload: { reason: string; case_version: number }
): Promise<void> {
  await apiRequest(`/care-cases/${caseId}/reopen`, {
    method: 'POST',
    body: JSON.stringify(payload)
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
  /**
   * 目标行的 id —— 标记参与状态要拿它去寻址（`markTargetParticipation`）。
   *
   * 「答没答」与「该不该答」是两个维度：`status` 说的是前者，下面三列说的是后者。
   * 一名请假的学生在这一行上是「未完成」，而他不进完成率的分母。
   */
  target_id: number
  /** `REQUIRED` / `LEAVE` / `EXEMPT` / `EXCLUDED`（§18.10）。 */
  participation_disposition: string
  /** 三个减项各自的原因。标回 `REQUIRED` 时服务端把它清成 null。 */
  disposition_reason: string | null
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

// --- 目标学生（V1.2 第 1 期）---

export interface TaskTargetItem {
  student_no: string
  student_name: string
  grade: string
  class_name: string
  gender: string | null
  age: number | null
  status: string
  /** TASK_SCOPE = 建任务时按对象范围发放，SUPPLEMENT = 后来补发的（CLAUDE.md §18.2）。 */
  target_source: string
  assigned_at: string | null
  completed_at: string | null
}

export interface TaskTargetList {
  task_id: number
  task_no: string
  name: string
  /**
   * 这场测评**当初是按什么范围发的**。可能是 `null`——那表示**没有这份记录**，
   * 不是「全校」：2026-09-19 之前建的任务没有这一行，外部导入的批次任务也从来不
   * 是「发给全校」的（它是照着一份文件建的）。所以调用方**不要** `?? 'SCHOOL'`
   * 兜底，而要照着 `null` 分岔显示「未记录发放范围」。
   */
  scope_type: string | null
  items: TaskTargetItem[]
}

export async function getTaskTargets(taskId: number): Promise<TaskTargetList> {
  return apiRequest<TaskTargetList>(`/assessment-tasks/${taskId}/targets`)
}

export interface SupplementPreview {
  /** 补发前 / 补发后这场测评的目标行总数。预览时只有 `before` 有值。 */
  before: number
  after?: number
  added?: number
  /** 候选（或已补发）的人数——**不是** `candidates.length`，截断时两者不等。 */
  total: number
  truncated: boolean
  limit: number
  candidates: {
    student_no: string
    student_name: string
    grade: string
    class_name: string
  }[]
}

/**
 * 补发目标学生：`confirm=false` 只看候选，`confirm=true` 才落行（§8.1 / §16.2）。
 *
 * 两次调用之间**没有状态**：`confirm=true` 时服务端会重新算一遍候选集，不依赖
 * 上一次预览的结果。所以界面上的预览只是一次「让你确认要补哪些人」，它不是一份
 * 拿在手里的清单——提交前名册上又转进来一个学生，他会被一起补进去。
 */
export async function supplementTaskTargets(
  taskId: number,
  payload: { reason: string; confirm: boolean }
): Promise<SupplementPreview> {
  return apiRequest<SupplementPreview>(`/assessment-tasks/${taskId}/targets/supplement`, {
    method: 'POST',
    body: JSON.stringify(payload)
  })
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
  /** 显示用的版本标签（形如 `V1.1`）。服务端算的，前端不写死：部署包里 `frontend/dist`
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

/** 重置密码的返回值：**只有后果，没有密码**（见 `resetAccountPassword`）。 */
export interface ResetPasswordResult {
  must_change_password: boolean
  revoked_sessions: number
}

/**
 * 一条登录会话。`status` 是服务端**现算**的（`REVOKED` 是库里真发生过的动作，
 * `EXPIRED` 由 `expires_at` 推出来），与 §12 那条「状态是现算的」同一口径。
 */
export interface SessionItem {
  id: number
  token_type: string
  status: string
  issued_at: string
  expires_at: string
  last_seen_at: string | null
  revoked_at: string | null
  revoked_reason: string | null
  ip: string | null
  user_agent: string | null
  /** 由服务端算，不让前端猜——猜错的后果是用户把自己踢下线。 */
  is_current: boolean
}

export async function getMySessions(): Promise<SessionItem[]> {
  const data = await apiRequest<{ items: SessionItem[] }>('/auth/sessions')
  return data.items
}

export async function revokeSession(sessionId: number): Promise<void> {
  await apiRequest(`/auth/sessions/${sessionId}/revoke`, { method: 'POST' })
}

export async function revokeOtherSessions(): Promise<number> {
  const data = await apiRequest<{ revoked_sessions: number }>('/auth/sessions/revoke-others', {
    method: 'POST'
  })
  return data.revoked_sessions
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

/**
 * 名册导入的预览结果（V1.2 阶段 3 起）。
 *
 * 与 V1.0 的那一份**只差一个字段**，而那个字段是整件事的关键：这里交出去的是
 * `batch_id`，不是 `preview_token`。上传时后端就把这一批（批次行 + 逐行明细）落进库，
 * 确认导入读的是库里那一批——操作员上传完被叫走、回来接着提交时，他提交的正是屏幕上
 * 那一批，而不是浏览器内存里一份可能已经过期的副本。
 *
 * `submittable_count` 是「这一批还有没有东西可提交」的**判据**，由服务端算一次原样
 * 发下来（选了覆盖时冲突行也要写、选了放弃时它们被跳过，两种情况下它们都属于「这一批
 * 要处理的行」）。前端**不要自己拿 valid_count + conflict_count 再算一遍**——两边各算
 * 一次就会漂。
 */
export interface StudentImportPreview {
  batch_id: number
  batch_no: string
  total: number
  valid_count: number
  conflict_count: number
  error_count: number
  submittable_count: number
  rows: StudentImportPreviewRow[]
}

/** 与测评导入共用两个码（后端也是同一套常量）。含义不同：那边「覆盖」替换上次导入的那一场。 */
export type StudentImportResolution = 'overwrite' | 'skip'

/**
 * 一批名册导入（`GET /student-roster/import/batches`）。
 *
 * `status` 两个取值都会真的出现：上传一份文件落一行 `PREVIEW`，确认导入改成
 * `COMMITTED`。所以这一列不能直接打印——`importBatchStatusLabel` 翻成中文。
 */
export interface RosterImportBatch {
  id: number
  batch_no: string
  file_name: string
  status: string
  total_rows: number
  created_rows: number
  updated_rows: number
  skipped_rows: number
  error_rows: number
  created_at: string
}

/** 某一批里的**一行**，记的是它落进系统时的样子与结论（`GET .../batches/{id}/rows`）。 */
export interface RosterImportBatchRow {
  row_no: number
  student_no: string | null
  name: string | null
  grade_name: string | null
  class_name: string | null
  /** 归一化之后的编码（`MALE`/`FEMALE`），界面上走 `genderLabel`。 */
  gender: string | null
  age: number | null
  processing_status: string
  /** 冲突码只在**冲突**时有值，与错误分开：冲突要人拍板，错误不用。 */
  conflict_code: string | null
  /** 需要解释时的中文原因（错误原文 / 「放弃」的理由）。 */
  message: string | null
  /** 落到了哪个学生身上；`ERROR` 与 `SKIPPED` 的行是 `null`。 */
  student_id: number | null
}

/**
 * 批次历史**不拆成裸数组**（§10 的第三个例外，与 `getAuditLogs` / `getCounselorReminders`
 * 同形）：服务端封顶 20 批，`items.length` 回答不了「我一共导过几批」。
 */
export interface RosterImportBatchPage {
  items: RosterImportBatch[]
  total: number
  truncated: boolean
}

export async function getAccounts(): Promise<AccountItem[]> {
  const data = await apiRequest<{ items: AccountItem[] }>('/admin/accounts')
  return data.items
}

/**
 * 管理员重置别人的密码。
 *
 * **返回值里没有密码**：服务端不回传明文（§16.5），它只回答「那个人的登录会话
 * 被撤销了几条」。密码是操作员在这个表单里自己敲的，界面显示的是他刚填的那一个。
 * 顺带把服务端的两个必然后果带回来给界面说清楚：那个人下次登录必须改密码，
 * 而且他此刻在别的设备上已经掉线了。
 */
export async function resetAccountPassword(accountId: number, temporaryPassword: string, purpose: string): Promise<ResetPasswordResult> {
  return apiRequest<ResetPasswordResult>(`/admin/accounts/${accountId}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ temporary_password: temporaryPassword, purpose })
  })
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

/**
 * 上传一份名册文件做校验预览（**同时落下这一批**）。
 *
 * 走裸 `fetch` 而不是 `apiRequest`：这是唯一的 multipart 上传，`apiRequest` 会给
 * 每个请求套上 `Content-Type: application/json`，而那会把 multipart 的 boundary 一起
 * 按 JSON 发出去，后端解不出文件。`assessment_import` 的两个上传同理。
 */
export async function previewStudentImport(file: File): Promise<StudentImportPreview> {
  const form = new FormData()
  form.append('file', file)
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/student-roster/import/preview`, { method: 'POST', headers, body: form })
  // `readEnvelope` 而不是裸 `response.json()`：这一支是最常撞见「服务端答了一段纯文本」
  // 的地方——一个编码读不动的文件会让后端抛在 `AppError` 之外，屏幕上的原话因此是
  // `JSON.parse: unexpected character …`（§2 那条的另一半）。
  const body = await readEnvelope(response)
  if (!response.ok || !body.success) throw new Error(body.error?.message || '导入预览失败')
  return body.data as StudentImportPreview
}

/** 提交时回的是这一批的五个计数，外加批次号（审计与提示语都要说出是哪一批）。 */
export interface StudentImportCommitResult {
  batch_id: number
  batch_no: string
  created: number
  updated: number
  skipped: number
  error_count: number
}

export async function commitStudentImport(
  batchId: number,
  resolution?: StudentImportResolution
): Promise<StudentImportCommitResult> {
  return apiRequest<StudentImportCommitResult>('/student-roster/import/commit', {
    method: 'POST',
    // `resolution` 只在文件里真的有冲突时才必需；没有它时后端不会问，
    // 传 `undefined` 会被 JSON.stringify 整个丢掉，正是要的。
    body: JSON.stringify({ batch_id: batchId, resolution })
  })
}

/** 最近导入过哪几批。逐行明细不在这里——一批是一所学校的人，那是几万行。 */
export async function getStudentRosterBatches(): Promise<RosterImportBatchPage> {
  const data = await apiRequest<RosterImportBatchPage>('/student-roster/import/batches')
  return { items: data.items, total: data.total, truncated: data.truncated }
}

/** 某一批的逐行明细。批次不存在时后端 404，不返回空列表（§14）。 */
export async function getStudentRosterBatchRows(batchId: number): Promise<RosterImportBatchRow[]> {
  const data = await apiRequest<{ items: RosterImportBatchRow[] }>(
    `/student-roster/import/batches/${batchId}/rows`
  )
  return data.items
}

/**
 * 外部平台的 MHT 普查结果导入（数据中心 → MHT测评记录导入）。
 *
 * V1.2 第 4 期起与名册导入同形：**上传即建批次**，逐行匹配结论落进
 * `assessment_import_row`，提交时只发 `batch_id`。V1.0 那个 `preview_token`
 * （把整份预览签成一个令牌）整个去掉了——令牌是死的，操作员没法在有问题的行上
 * 做任何事，唯一出路是重传一次文件；而重传拿到的是同一个批次（同操作者 + 同文件
 * 指纹 + 仍未提交的会被复用），所以令牌能表达的东西批次 id 一样表达得了，而且它
 * 还能被查、能被别人接手、「上传完被叫走了回来接着提交」也才成立。
 *
 * 三条与 V1.0 的实质差别，界面必须跟着变：
 *
 *   1. **逐行的结论是一句话，不是一个数组**。行表上没有 `errors` / `warnings` /
 *      `conflicts` 三类格子，只有 `match_status`（匹配到什么程度）、`conflict_code`
 *      （撞上了哪一类）与 `message`（后端拼好的一句人话）。V1.0 那种「三个都只有
 *      半句的碎片」在屏幕上要读者自己拼，而它们本来是一起读才成立的。
 *   2. **两个数各说各的**：`total_rows` 是这一批一共几行，`row_total` 是**读者可见**
 *      的行数（逐行明细按数据范围过滤，见 §9）。界面上分开写、各自标明口径。
 *   3. **可提交性由服务端算**（`row_counts`），前端不要自己拿几个数再算一遍。
 */

/**
 * 逐行明细里的一行（`GET /assessment-imports/{id}/rows`，以及上传那一次的响应）。
 *
 * 两套值都发：`raw_*` 是文件里印的那些字，`normalized_*` 是拿去找人的那些值。
 * 少了前者，操作员看不出「七年级一班」为什么没匹配上「701」；少了后者，他改不对。
 */
export interface AssessmentImportRow {
  id: number
  /**
   * 这一行是哪一批传上来的。
   *
   * 逐行明细那一屏（只看一批）用不到它；而**任务详情页的「未匹配行」是跨批的**
   * （一场任务下可以有好几批，先初一、隔几天再初二），而 `row_no` 是**批内**编号
   * ——少了这一列，那一屏会出现两行「第 2 行」，看起来像丢了一行或者重了一行。
   * 服务端取不到批次时是 `null`（见 `_row_payload`），界面照 `—` 渲染。
   */
  batch_id: number
  batch_no: string | null
  /** **文件里的行号**（表头是第 1 行，所以第一条数据是 2），不是数组下标。 */
  row_no: number
  raw_name: string | null
  raw_grade_name: string | null
  raw_class_name: string | null
  raw_age: number | null
  normalized_name: string | null
  normalized_grade_name: string | null
  normalized_class_name: string | null
  /** 九个码之一（含初始值 `PENDING`），走 `matchStatusLabel`。 */
  match_status: string
  match_confidence: number | null
  /** 同名候选的**可见**人数（别人班上的同名人不在里面）。 */
  candidate_count: number
  matched_student_no: string | null
  /** 展示名（`student.masked_name`），与逐行列表一致；不是遮蔽手段。 */
  matched_name: string | null
  /** `AGE_MISMATCH` / `DUPLICATE` / `IN_SYSTEM_RESULT`，走 `importConflictLabel`。 */
  conflict_code: string | null
  /**
   * 只有 `match_status === 'OUT_OF_SCOPE'` 的行才有这一列，走 `outOfScopeReasonLabel`。
   * 它把两种「任务外」分开，而两者的**出路完全不同**：`SUPPLEMENT_CANDIDATE` 可以补发
   * 目标学生，`NOT_IN_TASK_SCOPE` 什么都不用做（§18.7，中文见 `labels.ts` 那张表）。
   */
  out_of_scope_reason: string | null
  /** 提交之后这一行去哪了（PENDING / CREATED / UPDATED / SKIPPED），走 `importRowStatusLabel`。 */
  processing_status: string
  resolution: string | null
  /**
   * **来源冲突**怎么裁的（`KEEP_ONLINE` / `USE_EXTERNAL` / `REJECT_EXTERNAL` /
   * `KEEP_BOTH_BUT_ONE_EFFECTIVE`），走 `conflictResolutionLabel`。
   *
   * 只有 `match_status === 'CONFLICT'` 的行才有这一列，所以 `null` 是**「这个问题没问过」**
   * 而不是「选了某一档」——它与 `resolution` 是两个问题（那一列答「这一行写不写进去」，
   * 这一列答「以哪一份为准、另一份留不留」），界面上两格都要显示。
   */
  conflict_resolution: string | null
  /** 年龄覆盖前后的两个数（`AGE_MISMATCH` 选了覆盖时才有）。 */
  age_before: number | null
  age_after: number | null
  /** 年龄冲突怎么处置的（`keep_roster` / `overwrite` / `session_only`），走 `ageResolutionLabel`。 */
  age_resolution: string | null
  /** 文件里的用时；没写就是 `null`——`0` 秒是一次真实存在的用时（§3 数值列约定）。 */
  duration_seconds: number | null
  session_id: number | null
  /** 这一行为什么这样。**单元格里渲染的就是它**，不是若干个半句拼起来的。 */
  message: string | null
}

/**
 * 一批行**此刻**的状态分档，现算（上传那一次与 `GET .../rows` 都有）。
 *
 * 四个数对应四种动作，边界不能混：`needing_resolution` 与 `conflict` 是「有人拍板就
 * 写得进去」，`error` 是「选什么都不会写」。界面上的措辞要与后端 `batch_row_counts`
 * 的 docstring 同源，否则屏幕上那两句话会各自承诺一件做不到的事。
 *
 * **`conflict` 是 `needing_resolution` 的一个子集，不是并列的第五档**：整批那一次
 * 「覆盖 / 放弃」管得着的是 `needing_resolution - conflict`，`CONFLICT` 那几条必须
 * 逐行选四种处置之一（§18.8）。所以**那个单选项的显示与置灰判据，以及提交按钮的
 * 硬门槛，用的都是这个差额**——用 `needing_resolution` 会让一批只有冲突行的批次
 * 逼着操作员在两个按屏幕上的话「管不着这些行」的选项里挑一个，才肯点亮提交按钮。
 * 「待确认」那个**标题**仍然数 `needing_resolution`（那几条确实还需要他做点事）。
 *
 * **数的是整批，不套读者的数据范围**（与逐行明细刻意不同）：这四个数坐在提交按钮旁边，
 * 而提交时的判据是整批的。
 */
export interface AssessmentRowCounts {
  ready: number
  needing_resolution: number
  /** 其中 `CONFLICT` 那几条（`needing_resolution` 的子集，见上）。 */
  conflict: number
  error: number
}

/**
 * 一批导入 —— 上传响应、批次历史、以及「继续处理」拉回来的都是它。
 *
 * 逐行明细**只在两处**在响应里：上传那一次（`rows` + `row_total`，屏幕上要立刻显示
 * 每一行的结论，再让前端多发一次请求是白等一个往返）与 `GET .../rows`。批次历史那一页
 * 20 行，把每一批的行全带上会让它为了显示 20 行而传输几万行。
 *
 * `row_counts` 同理：批次列表那一路是 **`null`**——不是 `0`。`null` 是「这一页没算」，
 * `0` 是「算过了，一行都没有」，两者在界面上必须长得不一样（与 §11 那条 `None` ≠ `0`
 * 同源）。
 */
export interface AssessmentImportBatch {
  id: number
  batch_no: string
  /**
   * 这一批叫什么（提交后它会成为任务名）。与 `file_name` **分开**显示：只发一个的话，
   * 操作员看到 `结果(3).csv` 会以为自己在界面上填的那一格没保存住。
   */
  batch_name: string
  file_name: string
  source_system: string | null
  /** 文件里的测评日期（表单字段，不是文件里的一格）。 */
  tested_on: string | null
  /**
   * 这份文件里装的是什么（`EXTERNAL_FULL_ANSWER` / `EXTERNAL_SUMMARY`），走
   * `importModeLabel`。**与 `source` 是两个轴**：`source` 长在会话上说「在哪测的」，
   * 这一列长在批次上说「文件里有没有逐题答案」。
   *
   * 它决定后果，界面要跟着分岔：汇总档不产生答卷、不进 `assessment_result`，
   * 所以按结果说话的页面看不到它，而任务完成率会把它算成已完成。
   */
  import_mode: string
  status: string
  /** `NONE` / `overwrite` / `skip`；没提交过是 `null`。走 `assessmentResolutionLabel`。 */
  resolution: string | null
  task_id: number | null
  total_rows: number
  /**
   * 下面四列回答「提交之后写进去了几条」。预览态下 `created/updated/skipped` 全是 `0`
   * ——**`0` 与「还没提交」不是一回事**，界面按 `status` 分岔（预览态显示 `—`）。
   */
  created_rows: number
  updated_rows: number
  skipped_rows: number
  /** 这一列在**匹配时**就写好了，所以预览态下它也有值。 */
  error_rows: number
  row_counts: AssessmentRowCounts | null
  imported_by: number | null
  /** 姓名与账号分两个字段发，界面自己拼「姓名 · 账号」（与审计页第一列逐字同形），
   *  这样只有一个时还能降级显示。 */
  imported_by_name: string | null
  imported_by_account: string | null
  created_at: string | null
  /** 只有上传那一次的响应里带。 */
  rows?: AssessmentImportRow[]
  /** 同上：**读者可见**的行数（`rows.length` 就是它，除非被截断）。 */
  row_total?: number
}

/** 批次历史不拆成裸数组：服务端封顶 20 批，`items.length` 回答不了「我一共导过几批」。 */
export interface AssessmentImportBatchPage {
  items: AssessmentImportBatch[]
  total: number
}

/** 导入的处置方式：覆盖上次 / 放弃这几条。整批共用一次选择。 */
export type AssessmentImportResolution = 'overwrite' | 'skip'

/**
 * 上传一份外部测评记录（**同时落下这一批与逐行明细**）。
 *
 * `taskId` 是**选填**的，而它决定判重口径（§18.7 / §18.8）：绑定了任务就按「这场任务里
 * 这个人有没有有效卷子」判（`OUT_OF_SCOPE` / `CONFLICT` 两档只在这种情况下才可能出现），
 * 不绑定就按自然月（同一名学生同一个月只能有一次外部导入）。两种口径都在，各有各的道理，
 * 界面上要选。
 *
 * `sourceSystem` 也是选填：这批数据是从哪个平台导出来的。后端取不到时写 `UNKNOWN`。
 */
export async function previewAssessmentImport(
  file: File,
  batchName: string,
  testedOn: string,
  taskId?: number | null,
  sourceSystem?: string
): Promise<AssessmentImportBatch> {
  const form = new FormData()
  form.append('file', file)
  form.append('batch_name', batchName)
  form.append('tested_on', testedOn)
  // 空串就是「没填」——后端按 `strip() or None` 读这两项。表单字段一律 append，
  // 免得「键不在」与「键是空」在后端成为两种形状。
  form.append('task_id', taskId ? String(taskId) : '')
  form.append('source_system', sourceSystem ?? '')
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/assessment-imports/preview`, { method: 'POST', headers, body: form })
  const body = await readEnvelope(response)
  if (!response.ok || !body.success) throw new Error(body.error?.message || '导入预览失败')
  return body.data as AssessmentImportBatch
}

export interface AssessmentImportResult {
  batch_id: number
  /** 这一批唯一的名字（`BATCH-20260919-1`），审计记的也是它。 */
  batch_no: string
  /**
   * `null` 表示这次**一条都没写进去**（全部待确认的行都选了「放弃」），此时后端不建批次任务
   * ——空任务在列表上永远显示「进行中」，还会被同月的下一次导入复用。
   */
  task_id: number | null
  /** 同上：没有批次任务时是空串，所以别写死「（批次 ${task_no}）」。 */
  task_no: string
  /** 这一批一共几行（含被放弃的），不是写进去的条数。 */
  total: number
  created: number
  /** 「覆盖上次」就地重写的条数。 */
  updated: number
  skipped: number
  /**
   * 处置过了、但外部结果**没有落成一场测评**的条数（第 6 期加的）。
   *
   * 它填的是 `created + updated + skipped` **之外**的那一块——冲突行选了「保留系统内
   * 作答」或「不采纳外部结果」时，这一批刻意不建任何测评记录（学生本人答的那一份
   * 已经在库里了）。少了它，上面三个数加起来小于总行数，而屏幕上没有任何东西解释
   * 差额去哪了：**静默丢数据与「这里就只有这么多」在屏幕上一模一样**（§10）。
   */
  not_applied: number
  /** 按文件的年龄写回名册的条数。 */
  age_updated: number
  /** 覆盖时从那一场收回的、**还没人处理过**的风险待办条数（老师复核过的不动）。 */
  withdrawn_risk_events: number
}

/**
 * `resolution` 只在批次里**真的有需要拍板的行**时才是必需的（后端会以 422 回一句中文，
 * 前端把它原样弹出来）。没有冲突的文件照旧一次提交——要求 99% 的正常导入先回答
 * 一个不该问的问题，只会让人乱点。
 *
 * `ageResolution` 是**整批的年龄处置**，只在文件里有 `AGE_MISMATCH` 的行时才问得出答案
 * （§18.5）。它与 `resolution` 是两个独立的问题（「这一行要不要写进去」与「年龄按谁的记」），
 * 而**逐行处置过的行不受这两个参数影响**——逐行覆盖整批（`resolveAssessmentImportRow`）。
 */
export async function commitAssessmentImport(
  batchId: number,
  resolution?: AssessmentImportResolution,
  ageResolution?: string
): Promise<AssessmentImportResult> {
  return apiRequest<AssessmentImportResult>(`/assessment-imports/${batchId}/commit`, {
    method: 'POST',
    // 缺省时 `JSON.stringify` 会把这几个键整个丢掉，正是要的（后端按「键不在」读）。
    body: JSON.stringify({ resolution, age_resolution: ageResolution })
  })
}

/**
 * 逐行处置一条待拍板的导入行（§18.6）。
 *
 * 三个字段都可选，**缺省 = 保持这一次之前的值不变**（后端按 `is not None` 判），
 * 所以「只改年龄那一项」不必把 `resolution` 再发一遍。没有清空的写法——后端没有这一档，
 * 而它也不需要：处置是「拿个主意」，不是一个可以撤回的开关。
 *
 * **`conflictResolution` 是第三个问题**（第 6 期）：来源冲突的行（学生自己答过这一场，
 * 学校里又导进来一份同场结果）要回答的是「以哪一份为准、另一份留不留」，而它既不是
 * 「这一行写不写进去」（`resolution`），也不是「年龄按谁的记」（`ageResolution`）。
 * 后端按 `match_status !== 'CONFLICT'` 拒收它——所以界面只在冲突行上摆这一栏。
 *
 * **它不写任何测评记录**（后端 docstring 逐字如此）：真正的落库仍然全部发生在提交那一刻，
 * 所以界面上处置完那一行的 `processing_status` 仍然停在 `PENDING`——那不是没生效。
 */
export async function resolveAssessmentImportRow(
  rowId: number,
  payload: {
    resolution?: AssessmentImportResolution
    ageResolution?: string
    conflictResolution?: string
  }
): Promise<{ row_id: number; row_no: number; batch_no: string; match_status: string }> {
  return apiRequest(`/assessment-import-rows/${rowId}/resolve`, {
    method: 'PATCH',
    body: JSON.stringify({
      resolution: payload.resolution,
      age_resolution: payload.ageResolution,
      conflict_resolution: payload.conflictResolution
    })
  })
}

/** 最近导入过哪几批（最近的在前，服务端封顶 20 条）。逐行明细走 `getAssessmentImportRows`。 */
export async function getAssessmentImportBatches(): Promise<AssessmentImportBatchPage> {
  return apiRequest<AssessmentImportBatchPage>('/assessment-imports')
}

/**
 * 某一批的逐行明细，**按读者的数据范围过滤**（批次是共享的，而它逐行给出姓名与学号）。
 *
 * 返回的 `total` 是**可见**行数，`row_counts` 是**整批**的——两个数各有各的口径，
 * 界面上都写，不互相顶替。这个「不同源」是有意的：那三个数坐在提交按钮旁边，必须与
 * 提交时的整批判据一致。
 */
export async function getAssessmentImportRows(batchId: number): Promise<{
  items: AssessmentImportRow[]
  total: number
  rowCounts: AssessmentRowCounts | null
}> {
  const data = await apiRequest<{
    items: AssessmentImportRow[]
    total: number
    row_counts: AssessmentRowCounts | null
  }>(`/assessment-imports/${batchId}/rows`)
  return { items: data.items, total: data.total, rowCounts: data.row_counts }
}

/**
 * 一场任务下**没进得去**的那些导入行（§18.10）。
 *
 * 判据是**并集**：`match_status` 落在进不去的那四档里，**或者**这一行在提交时被放弃了
 * （选了「放弃」的行匹配得上，但这一批没有把它写进去——对这场任务而言它与没匹配上是
 * 同一件事：这个学生的这一场缺着）。
 *
 * `reason_counts` 与 `total` 的**口径故意不同**：前者数整场（不套读者的数据范围），
 * 后者是**你可见**的行数。两个数都要显示、各自标明口径——它们坐在同一句话里的
 * 两个位置，而读的人会以为它们在数同一件事（§9 / §11）。
 *
 * 逐行封顶 200（`UNMATCHED_ROW_LIMIT`），超出的部分界面上要说出来（§10：凡是截断，
 * 都要自己说出来）。
 */
export async function getUnmatchedImportRows(taskId: number): Promise<{
  items: AssessmentImportRow[]
  total: number
  reasonCounts: Record<string, number>
}> {
  const data = await apiRequest<{
    items: AssessmentImportRow[]
    total: number
    reason_counts: Record<string, number>
  }>(`/assessment-tasks/${taskId}/unmatched-import-rows`)
  return { items: data.items, total: data.total, reasonCounts: data.reason_counts }
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
  const body = await readEnvelope(response)
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

/**
 * 一份导出作业 —— 后端 `ExportJob`（§16.3，2026-09-19 第 8 期）。
 *
 * **导出在这个系统里是两跳**：创建作业的端点只登记一行并落一份文件，字节一律从
 * `GET /export-jobs/{id}/download` 出去。`status` 是服务端**现算**的（`EXPIRED`
 * 由 `expires_at` 推出来，`REVOKED` 是库里真发生过的动作），照 §12 那条现算口径。
 *
 * `downloadable` 由服务端算，**不让前端从状态码推**：三个判据里有一个是「文件还在
 * 不在盘上」，那是界面看不见的。按钮亮不亮和点下去会不会成功是同一句话的两个说法。
 */
export interface ExportJob {
  id: number
  job_no: string
  export_type: string
  requested_by: number
  requested_by_name: string | null
  purpose: string
  mask_level: string
  status: string
  columns: string[]
  row_count: number
  download_count: number
  downloadable: boolean
  expires_at: string | null
  downloaded_at: string | null
  revoked_at: string | null
  created_at: string | null
}

/** 建一份导出作业。**它不回文件**——字节走 `downloadExportJob`。 */
export async function createExportJob(path: string, payload: unknown): Promise<ExportJob> {
  return apiRequest<ExportJob>(path, { method: 'POST', body: JSON.stringify(payload) })
}

/** 取回一份作业的字节并让浏览器存盘。 */
export async function downloadExportJob(jobId: number, filename: string): Promise<void> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(`${API_BASE}/export-jobs/${jobId}/download`, { headers })
  if (!response.ok) {
    // 服务端的 `error.message` 本来就是写给用户看的（「这份文件已经过期」/「已被
    // 撤销」/「文件已不在服务器上，请重新导出」），照搬过来比另编一句准确（§2）。
    let message = '导出失败'
    try {
      const body = await response.json()
      message = body.error?.message || message
    } catch {
      // 响应体不是 JSON（网关那张 HTML 错误页）——保住上面那句兜底，不要在这里抛
      // 一个 SyntaxError 把真正的原因盖掉。
    }
    throw new Error(message)
  }
  saveBlob(await response.blob(), filename)
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

/**
 * 文件名带上作业编号：`care-cases-EXPORT-20260919-3.csv`。
 *
 * 作业编号是**给人念的**（操作员报故障时说的就是它，审计页搜的也是它），而浏览器
 * 下载目录里那一串 `care-cases.csv`、`care-cases(1).csv` 谁也认不出是哪一次导出。
 * 语义名留着是因为它仍然是最快能认出来的那半截。
 */
export function exportFileName(stem: string, jobNo: string): string {
  return `${stem}-${jobNo}.csv`
}

/**
 * 建作业 + 立刻把文件取回来——**界面上仍然是一次点击**。
 *
 * 两跳是服务端的事：一次点击如果变成「先建作业、再去导出中心点下载」，那是把一个
 * 记录动作变成了第二道工序，而用户要的只是一份文件。导出中心留给「再看一眼当时
 * 导了什么」和「过期前重下一次」。
 */
async function runExport(path: string, stem: string, payload: unknown): Promise<ExportJob> {
  const job = await createExportJob(path, payload)
  await downloadExportJob(job.id, exportFileName(stem, job.job_no))
  return job
}

export async function listExportJobs(): Promise<ExportJob[]> {
  const data = await apiRequest<{ items: ExportJob[] }>('/export-jobs')
  return data.items
}

export async function revokeExportJob(jobId: number, reason: string): Promise<ExportJob> {
  return apiRequest<ExportJob>(`/export-jobs/${jobId}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ reason })
  })
}

/**
 * 任务完成明细的 CSV。
 *
 * 这个端点一直在，但界面从来没有入口——于是一所 1000 人学校的普查明细只能在 340px
 * 高的弹层里滚。明细表格因此只渲染前若干行（见 `TasksPage` 的 `DETAIL_RENDER_LIMIT`），
 * 而「全部」的出路就是这里：截断必须有一条出路，否则就是静默丢数据。
 *
 * 2026-09-19 第 8 期起它从 `GET` 改成 `POST /completion/export`：这条路**不再只读**
 * （每次调用建一行 `export_job`、占一个编号、在盘上落一份文件），而 GET 的语义是
 * 「可以重复取、没有副作用」。
 */
export async function downloadTaskCompletionCsv(
  taskId: number,
  stem: string,
  purpose: string
): Promise<ExportJob> {
  return runExport(`/assessment-tasks/${taskId}/completion/export`, stem, { purpose })
}

/**
 * 未参与名单（这场测评里应测但没完成的人）。
 *
 * 三档身份（学号/姓名/年级/班级/性别/年龄）加参与状态，所以服务端有两道门槛
 * （§18.11）：`CONTROLLED_EXPORT` 与 `STUDENT_PSYCH_DETAIL: {SCOPED}`。
 */
export async function downloadNonParticipantsCsv(
  taskId: number,
  stem: string,
  purpose: string
): Promise<ExportJob> {
  return runExport(`/assessment-tasks/${taskId}/non-participants/export`, stem, { purpose })
}

/**
 * 未匹配行清单（§20#14 的「导出」那一半）：这场任务下没进得去的那几类导入行。
 *
 * 与「未参与名单」是**两批不同的人**：那一份导的是应测没完成的学生，这一份导的是
 * 压根没进到学生身上（或进来了又被放弃）的行——所以列也不一样，这里给的是
 * 「哪一批、第几行、文件里写的是谁、为什么没进来」。
 *
 * 门槛与未参与名单逐字同一对（服务端也是同一个函数），但对**德育领导**一样是 403：
 * 两种取值都要求 `STUDENT_PSYCH_DETAIL: {SCOPED}`，而它是 `SUMMARY`。
 */
export async function downloadUnmatchedRowsCsv(
  taskId: number,
  stem: string,
  purpose: string
): Promise<ExportJob> {
  return runExport(`/assessment-tasks/${taskId}/unmatched-import-rows/export`, stem, { purpose })
}

/**
 * 一场测评的参与口径六个数（§18.10）：目标 / 请假免测已排除 / 应测 / 已完成 / 完成率，
 * 外加 `unimported_records`（任务外、重复、未匹配、冲突的导入记录——它们**不进**
 * 完成率，因为那批行从来不在目标行里）。
 */
export interface TaskParticipation {
  total_targets: number
  excluded_targets: number
  expected_targets: number
  completed_targets: number
  completion_rate: number
  unimported_records: number
}

export async function getTaskParticipation(taskId: number): Promise<TaskParticipation> {
  return apiRequest<TaskParticipation>(`/assessment-tasks/${taskId}/participation`)
}

/**
 * 标记一名目标学生在**这一场**里该不该参加。
 *
 * 它一行答题事实都不动：目标行的 `status`、会话、答卷、结果一个不碰——「该不该参加」
 * 与「参没参加」是两个维度。三个减项都要填原因，服务端会挡下没填的那种。
 */
export async function markTargetParticipation(
  taskId: number,
  targetId: number,
  payload: { disposition: string; reason?: string | null; note?: string | null }
): Promise<{ participation_disposition: string; disposition_reason: string | null }> {
  return apiRequest(`/assessment-tasks/${taskId}/targets/${targetId}/participation`, {
    method: 'PATCH',
    body: JSON.stringify(payload)
  })
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

/**
 * 批量转派负责人。
 *
 * **载荷是逐行带版本号的，不是一串 case id**（§16.4：转派也要走乐观锁）。一个
 * `number[]` 只表达得了「我要改这几条」，表达不了「我读到的是这几条的哪一版」——
 * 而列表上每一行的版本各不相同（有些行是十分钟前拉的），所以号必须跟着行走。
 */
export async function batchAssignOwner(
  assignments: Array<{ case_id: number; case_version: number }>,
  ownerId: number
): Promise<{ updated: number }> {
  return apiRequest<{ updated: number }>('/care-cases/batch-assign', {
    method: 'POST',
    body: JSON.stringify({ assignments, owner_id: ownerId })
  })
}

export async function exportCareCases(options: ExportOptions): Promise<ExportJob> {
  const { studentIds, ...rest } = options
  // 勾选了几个人就只导这几个人——那一支本来就是学生 id 的集合，服务端照它筛。
  // 不勾选是「整个范围」，不是「一个都不导」，所以两个分支的载荷不同而不是同一个。
  const payload = studentIds && studentIds.length > 0 ? { ...rest, student_ids: studentIds } : rest
  return runExport('/care-cases/export', 'care-cases', payload)
}

export async function exportHighRiskCareCases(options: ExportOptions): Promise<ExportJob> {
  const { studentIds: _ignored, ...rest } = options
  return runExport('/care-cases/high-risk/export', 'high-risk-care-cases', rest)
}

/** Single-student controlled export — the prototype's `exportOne`. */
export async function exportCareCase(studentId: number, options: ExportOptions): Promise<ExportJob> {
  const { studentIds: _ignored, ...rest } = options
  return runExport(`/care-cases/${studentId}/export`, 'care-case', rest)
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
