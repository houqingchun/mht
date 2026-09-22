<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import { daysFromNow, today } from '../../services/dates'
import {
  dimensionLabel,
  levelLabel,
  levelTone,
  reminderKindLabel,
  retestStatusLabel,
  riskEventStatusLabel,
  riskEventStatusTone,
  statusLabel,
  statusTone,
  userScopeLabel,
  validityLabel,
  validityTone
} from '../../services/labels'
import {
  closeCareCase,
  createFamilyContact,
  createFollowUp,
  createManualReview,
  createRetestPlan,
  exportCareCases,
  exportHighRiskCareCases,
  getAnalyticsReport,
  getAssessmentTasks,
  getCareCaseDetail,
  getCareCases,
  getCounselorWorkbench,
  getCounselorReminders,
  getMe,
  reopenCareCase,
  type AnalyticsReport,
  type CareCaseDetail,
  type CareCaseItem,
  type CounselorWorkbench,
  type CurrentUser,
  type ReminderItem
} from '../../services/api'

const router = useRouter()

// 业务词表由系统配置提供：这些字段原本是自由文本输入，
// 同一概念会被写成不同字符串，无法统计。
const { settings } = useSettings()

function optionsOf(list: readonly string[]) {
  return list.map(item => ({ value: item, label: item }))
}
const metrics = ref<CounselorWorkbench | null>(null)
const cases = ref<CareCaseItem[]>([])
const detail = ref<CareCaseDetail | null>(null)
const loading = ref(true)
const error = ref('')

/**
 * 读者自己。
 *
 * `load()` 一直在调 `getMe()`——它要拿 `role_code` 判这一页该不该给这个人看——
 * 但**取到之后用完就丢**，于是这一页上没有任何一个字说明「这些数字描述的是谁的范围」。
 *
 * 而这一页每一个数都是**按数据范围**算出来的（服务端的 `student_scope_predicate`，
 * §9）：`getCareCases()` 只回调用者范围内的档案，`completion_rate` 的分母也是。
 * 一个只带 1 个班范围的心理老师看到「关注档案总数 12」，会读成「这所学校 12 份档案」。
 * §9 那句「范围数字必须在 UI 上写明口径，否则它冒充全校数字，比不给数字更糟」
 * 说的正是这一处，而「我的范围筛查统计」那一页（`AnalyticsPage`）已经这么做了。
 *
 * 口径文字的形状与「全部学生」页头那一行同源（`CasesPage.vue` 的 `scopeText`）：
 * 读 `/auth/me` 的 `scopes[]`，**取不到就整句不出现**，不猜一个「全校」——
 * 一句说不清的范围比没有范围更糟，因为它会被照着安排工作。
 * 多行范围在服务端由 `or_` 合并，所以这里并列（并集，比任何单行都大）。
 */
const me = ref<CurrentUser | null>(null)
const scopeText = computed(() => {
  const types = [...new Set((me.value?.scopes ?? []).map(s => s.scope_type))]
  if (!types.length) return ''
  return types.map(userScopeLabel).join('、')
})

// Dialog state
const showConfirm = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmDanger = ref(false)
const confirmText = ref('确认')
let confirmResolve: ((value: boolean) => void) | null = null

const showForm = ref(false)
const formTitle = ref('')
const formFields = ref<FormField[]>([])
const formSubmitText = ref('提交')
let formResolve: ((values: Record<string, string>) => void) | null = null

const showExport = ref(false)

function showConfirmation(title: string, message: string, danger = false, text = '确认'): Promise<boolean> {
  confirmTitle.value = title
  confirmMessage.value = message
  confirmDanger.value = danger
  confirmText.value = text
  showConfirm.value = true
  return new Promise((resolve) => { confirmResolve = resolve })
}

function onConfirm() {
  if (confirmResolve) confirmResolve(true)
}

function onCancelConfirm() {
  if (confirmResolve) confirmResolve(false)
}

function showFormDialog(title: string, fields: FormField[], submitText = '提交'): Promise<Record<string, string>> {
  formTitle.value = title
  formFields.value = fields
  formSubmitText.value = submitText
  showForm.value = true
  return new Promise((resolve) => { formResolve = resolve })
}

function onFormSubmit(values: Record<string, string>) {
  if (formResolve) formResolve(values)
}

function onFormCancel() {
  if (formResolve) formResolve({})
}

// 优先工作队列：未关闭，且满足其一 ——
//   1. 待人工复核（无论筛查等级，复核本身就是待办）
//   2. 超出一般范围（需要持续关注）
//   3. 已逾期
// 「待人工复核」指标卡统计的正是第 1 类，二者口径必须一致，
// 否则会出现「指标说 1 条、队列却说没有」的矛盾。
const priorityQueue = computed(() =>
  cases.value.filter(c =>
    c.case_status !== 'CLOSED' &&
    (c.case_status === 'PENDING_REVIEW' || c.total_level !== 'GENERAL_RANGE' || c.overdue)
  )
)

/**
 * 未进入队列、但仍在观察中的档案数。
 *
 * 「优先工作队列」这四个字会把人带向「这就是我的全部在办档案」，而它其实是一张
 * **筛选过的**表：未逾期、非待复核、且未超出一般范围的记录不在里面。
 * 9 条在队列、12 份在办——差的那 3 份此前**只在这个队列为空时**才被解释一句，
 * 于是有 9 行的时候读者看到的就是「都在这里了」。
 *
 * 所以那一句从空态里搬出来了（见模板），这条注解除了解释它，也解释为什么它是
 * 「差集」而不是另算一个数：它与 `priorityQueue` 共用同一组判据，构造上不可能
 * 说「9 + 3 ≠ 12」。
 */
const observingElsewhere = computed(
  () => cases.value.filter(c => c.case_status !== 'CLOSED').length - priorityQueue.value.length
)

/** 「关注档案总数」脚下那一格的数。抽成 computed 而不是在模板里 `filter().length`：
 *  模板里的表达式每次重渲染都会重新扫一遍数组，而这是四张卡里唯一一张会随
 *  `cases` 每个字段变化重算的。 */
const closedCaseCount = computed(
  () => cases.value.filter(c => c.case_status === 'CLOSED').length
)

/**
 * 指标卡的色带只在**有事**的时候亮。
 *
 * 四条色带此前是写死的常量（红 / 琥珀 / 青 / 绿），与它上面那个数无关：于是
 * 「待人工复核 0」头上照样顶着一条红杠，「逾期跟进 0」照样一条琥珀——而那正是
 * 工作台上最刺眼的两个位置，也恰好出现在「今天没有待办」的那一天。
 *
 * 色带是这四张卡里唯一一个**不经过数字就能被看见**的信号（`metric-value` 是
 * 2rem 的字，但红杠是整张卡最外圈的图形），所以它必须与数字同源。这是 §11 那条
 * 「指标与它指向的列表同源」的另一面：这一次会漂的不是数，是颜色。
 *
 * 返回空串时 `data-tone` 不匹配任何一条 `[data-tone=…]` 规则，`--tone` 保持未设，
 * `.metric:before` 回落到 `var(--blue)`——也就是「平静」那一档。青色与绿色两张卡
 * 不参与：它们本来就不表示警报，0 份档案顶一条青杠不会说谎。
 */
function metricTone(count: number | undefined | null, tone: string) {
  return (count ?? 0) > 0 ? tone : ''
}

/**
 * 「逾期跟进」指标卡的数。
 *
 * 与上面的 `priorityQueue` 是同一条道理（指标必须说得出它指向的那批东西），
 * 但这里多一层：这张卡**点得动**，跳的是 `/counselor/cases?filter=overdue`，
 * 也就是 `CasesPage` 里 `c.overdue === true` 那一批。所以它必须从**同一个标志**
 * 算出来，不能另算一遍——改后端就又多一处会漂的口径。
 *
 * 它此前读的是 `metrics.following`（status == FOLLOWING 的档案数），
 * 那是「跟进中」而不是「逾期」：真实数据上读 10，而点进去的队列是 0 条。
 */
const overdueCount = computed(() => cases.value.filter(c => c.overdue).length)

// 主要维度分布 —— 来自报表端点的真实聚合，按高分占比降序。
// 2026-09-22 起从 /analytics/dimensions 切换到 /analytics/report.dimensions，
// 淘汰旧端点，与五个报表页共享同一数据源。
const dimensionDistribution = ref<Array<{ dimension_code: string; high_rate: number }>>([])
const dimsError = ref('')

// 本周提醒 —— 来自 /counselor/reminders，逾期优先。
const reminders = ref<ReminderItem[]>([])
/**
 * 服务端另算的总数。`reminders.length` 是**被截断后**的那一批（每个来源封顶 20），
 * 所以它答不了「我有多少待办」——40 条时它也显示 20，而读者会照 20 去安排工作。
 * 2026-09-17 修：徽标从此读 `reminderTotal`，截断时另说一句「另有 N 项未显示」。
 */
const reminderTotal = ref(0)
const remindersTruncated = ref(false)
const notesError = ref('')

/** 被截断掉、没显示出来的条数。为 0 时界面不出现那句话。 */
const remindersHidden = computed(() => Math.max(0, reminderTotal.value - reminders.value.length))

/**
 * 两个副面板各自记自己的失败。
 *
 * 2026-09-17 修：此前 `Promise.allSettled` 的两个 rejected 分支**都是空的**，
 * 于是面板落到 `v-else` 的空态上——「尚无已提交的测评数据。」「0 项」
 * 「近 30 天内没有待办跟进或复测。」这三句话都是**合法**的空态文案，
 * 一次 500 借它们说成了「学校没有数据」。红条只在最外层 catch 里出现，
 * 而副面板的失败根本走不到那里。
 *
 * 2026-09-22 修：维度分布从独立的 `/analytics/dimensions` 端点切换到
 * `/analytics/report`（与五个报表页共享同一数据源），淘汰旧端点。
 * 由于工作台没有任务选择器，自动取最新可分析任务的报表。
 *
 * 形状照抄 `CasesPage.vue` 的 `studentsLoading` / `studentsError`：
 * 失败必须落在**它自己那一块**里，而不是被邻居的空态吸收。
 */
async function loadPanels() {
  dimsError.value = ''
  notesError.value = ''
  const [report, notes] = await Promise.allSettled([
    (async () => {
      const tasks = await getAssessmentTasks()
      const task = tasks.filter(t => t.completed_targets >= 5).sort((a, b) => Date.parse(b.start_at || '') - Date.parse(a.start_at || ''))[0] || tasks[0]
      if (!task) return []
      const r = await getAnalyticsReport([task.id], 'ALL_CALCULATED')
      return r.dimensions.map(d => ({ dimension_code: d.dimension_code, high_rate: d.high_score_rate ?? 0 })).filter(d => d.high_rate > 0)
    })(),
    getCounselorReminders()
  ])
  if (report.status === 'fulfilled') {
    dimensionDistribution.value = [...report.value].sort((a, b) => b.high_rate - a.high_rate)
  } else {
    dimensionDistribution.value = []
    dimsError.value = report.reason instanceof Error ? report.reason.message : '维度分布加载失败'
  }
  if (notes.status === 'fulfilled') {
    reminders.value = notes.value.items
    reminderTotal.value = notes.value.total
    remindersTruncated.value = notes.value.truncated
  } else {
    reminders.value = []
    reminderTotal.value = 0
    remindersTruncated.value = false
    notesError.value = notes.reason instanceof Error ? notes.reason.message : '近期提醒加载失败'
  }
}

/** 排序后仅展示占比最高的若干维度，避免八条全铺满卡片。 */
const topDimensions = computed(() =>
  dimensionDistribution.value.filter(d => d.high_rate > 0).slice(0, 5)
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const current = await getMe()
    if (current.role_code !== 'counselor') {
      await router.push('/login')
      return
    }
    // 留下来的理由见 `me` 的声明：页头那行范围口径读的就是它。
    me.value = current
    metrics.value = await getCounselorWorkbench()
    cases.value = await getCareCases()
    // 副面板失败不影响工作台其余部分，所以它自带 catch，放在这里而不是外层 try 里。
    await loadPanels()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/**
 * 打开一名学生的档案弹层。
 *
 * 两条约定都在 §14 里：
 * - **换一个人时先清空**，否则取数失败之后弹层的标题写着 B、正文还是 A（上一次那份
 *   没被清掉）。判据是 `student.id !== studentId` 而不是无条件清空：写入之后那几处
 *   刷新（`saveReview` / `closeCase` 一伙）读的是**同一个人**，清了弹层会闪一下关掉
 *   再打开，而那不是「上一次的答案」，是这个人上一刻的数据。
 * - **失败要说出来**。此前它没有 try，异常直接抛到事件处理器外面（`@click` 里没人
 *   接），屏幕上什么都不会发生——而这一格现在多了整行可点这个入口（见模板），
 *   一次静默失败的面积跟着变大。
 */
async function openDetail(studentId: number) {
  if (detail.value && detail.value.student.id !== studentId) {
    detail.value = null
  }
  try {
    detail.value = await getCareCaseDetail(studentId)
  } catch (err) {
    detail.value = null
    showToast('error', err instanceof Error ? err.message : '档案详情加载失败')
  }
}

/**
 * The filter value is a QUEUE_TABS key (a status code), not a display label —
 * passing the Chinese label silently landed on an unfiltered list.
 */
function goCases(filter = 'all') {
  router.push({ path: '/counselor/cases', query: { filter } })
}

function goTasks() {
  router.push('/counselor/tasks')
}

/* ---------- 业务操作 ---------- */

async function saveReview() {
  if (!detail.value) return
  const risk = detail.value.risk_events.find((item) => item.status === 'PENDING') || detail.value.risk_events[0]
  if (!risk) {
    showToast('error', '没有可复核的风险事件')
    return
  }

  const values = await showFormDialog('记录人工复核', [
    { key: 'review_result', label: '复核结果', type: 'select', required: true, placeholder: '请选择复核结果', options: optionsOf(settings.value.care.review_results) },
    { key: 'confirmed_facts', label: '事实记录', type: 'textarea', required: true, placeholder: '记录已核实事实，不填写未经确认的诊断结论' },
    { key: 'next_action', label: '下一步安排', type: 'select', required: true, placeholder: '请选择下一步安排', options: optionsOf(settings.value.care.review_actions) },
    { key: 'next_follow_up_date', label: '下次跟进日期', type: 'date', defaultValue: daysFromNow(settings.value.cadence.follow_up_days) }
  ], '保存复核')

  if (!values.confirmed_facts) return
  await createManualReview(detail.value.case_id, {
    risk_event_id: risk.id,
    review_result: values.review_result,
    confirmed_facts: values.confirmed_facts,
    next_action: values.next_action,
    next_follow_up_date: values.next_follow_up_date
  })
  showToast('success', '人工复核已保存')
  await load()
  await openDetail(detail.value.student.id)
}

async function saveFollowUp() {
  if (!detail.value) return
  const values = await showFormDialog('新增跟进记录', [
    { key: 'record_type', label: '跟进类型', type: 'select', required: true, placeholder: '请选择跟进类型', options: optionsOf(settings.value.care.follow_up_types) },
    { key: 'confirmed_facts', label: '已确认事实', type: 'textarea', required: true, placeholder: '记录本次工作事实' },
    { key: 'next_follow_up_date', label: '下次跟进日期', type: 'date', required: true, defaultValue: daysFromNow(settings.value.cadence.follow_up_days) }
  ], '保存记录')

  if (!values.confirmed_facts) return
  await createFollowUp(detail.value.case_id, {
    record_type: values.record_type,
    confirmed_facts: values.confirmed_facts,
    next_follow_up_date: values.next_follow_up_date
  })
  showToast('success', '跟进记录已保存')
  await load()
  await openDetail(detail.value.student.id)
}

async function saveFamilyContact() {
  if (!detail.value) return
  const values = await showFormDialog('新增家庭回访', [
    { key: 'contact_date', label: '联系日期', type: 'date', required: true, defaultValue: today() },
    { key: 'contact_person', label: '联系对象', type: 'text', required: true, defaultValue: '监护人' },
    { key: 'channel', label: '回访方式', type: 'select', required: true, placeholder: '请选择回访方式', options: optionsOf(settings.value.care.contact_channels) },
    { key: 'result', label: '联系结果', type: 'select', required: true, placeholder: '请选择联系结果', options: optionsOf(settings.value.care.contact_results) },
    { key: 'support_status', label: '家庭支持情况', type: 'select', required: true, placeholder: '请选择支持情况', options: optionsOf(settings.value.care.support_statuses) },
    { key: 'confirmed_facts', label: '沟通事实', type: 'textarea', required: true, placeholder: '只记录与学生支持工作直接相关的已确认事实' },
    { key: 'next_contact_date', label: '下次联系日期', type: 'date', defaultValue: daysFromNow(settings.value.cadence.family_contact_days) }
  ], '保存回访')

  if (!values.confirmed_facts) return
  await createFamilyContact(detail.value.case_id, {
    contact_date: values.contact_date,
    contact_person: values.contact_person,
    channel: values.channel,
    result: values.result,
    support_status: values.support_status,
    confirmed_facts: values.confirmed_facts,
    next_contact_date: values.next_contact_date || undefined
  })
  showToast('success', '家庭回访已保存')
  await openDetail(detail.value.student.id)
}

async function saveRetest() {
  if (!detail.value) return
  const values = await showFormDialog('安排复测', [
    { key: 'planned_date', label: '复测日期', type: 'date', required: true, defaultValue: daysFromNow(settings.value.cadence.retest_days) },
    { key: 'reason', label: '复测原因', type: 'select', required: true, placeholder: '请选择复测原因', options: optionsOf(settings.value.care.retest_reasons) }
  ], '保存计划')

  if (!values.reason) return
  await createRetestPlan(detail.value.case_id, { planned_date: values.planned_date, reason: values.reason })
  showToast('success', '复测计划已保存')
  await load()
  await openDetail(detail.value.student.id)
}

async function closeCase() {
  if (!detail.value) return
  // The payload sets `confirm_follow_up_checked`, so the confirmation must
  // actually ask for that — otherwise the flag asserts a review nobody did.
  const confirmed = await showConfirmation(
    '关闭关注档案',
    '关闭前请确认：已复核该生的跟进记录与后续安排。关闭不会删除历史记录，发现新情况时可重新打开。',
    true,
    '已复核，关闭档案'
  )
  if (!confirmed) return

  const values = await showFormDialog('关闭原因', [
    { key: 'close_reason', label: '关闭原因', type: 'text', required: true, defaultValue: '完成阶段跟进并进入一般观察' },
    { key: 'close_note', label: '关闭说明', type: 'textarea', required: true, placeholder: '请输入关闭说明' }
  ], '确认关闭')

  if (!values.close_note) return
  try {
    await closeCareCase(detail.value.case_id, {
      close_reason: values.close_reason,
      close_note: values.close_note,
      confirm_follow_up_checked: true,
      // 乐观锁（§16.4）：把这一页读到的版本带回去。中间有人复核/跟进/转派过就回 409。
      case_version: detail.value.case_version
    })
  } catch (err) {
    // 服务端 409 那句原文就是写给用户看的（§2）。这里**不能**把它吞成一句
    // 「关闭失败」——那句话里写着别人改了什么、当前版本是几。
    showToast('error', err instanceof Error ? err.message : '关闭失败')
    detail.value = null
    await load()
    return
  }
  showToast('success', '关注档案已关闭，历史记录保留')
  detail.value = null
  await load()
}

async function reopenCase() {
  if (!detail.value) return
  const confirmed = await showConfirmation('重新打开档案', '确认重新打开此关注档案？')
  if (!confirmed) return

  const values = await showFormDialog('重新打开原因', [
    { key: 'reason', label: '原因', type: 'textarea', required: true, placeholder: '请输入重新打开原因' }
  ], '确认打开')

  if (!values.reason) return
  try {
    await reopenCareCase(detail.value.case_id, {
      reason: values.reason,
      case_version: detail.value.case_version
    })
  } catch (err) {
    // 最可能的 409 是「这名学生已经有一条在办档案」（秋季关档、春季再开，§1）——
    // 原样转达，那句话里带着现在那条在办档案的编号。
    showToast('error', err instanceof Error ? err.message : '重新打开失败')
    await load()
    await openDetail(detail.value.student.id).catch(() => {})
    return
  }
  showToast('success', '关注档案已重新打开')
  await load()
  await openDetail(detail.value.student.id)
}

/* ---------- 导出 ---------- */

const exportPurpose = ref('')
const exportMask = ref('masked')
const exportFields = ref('minimum')
const exportConfirmed = ref(false)

function openExport(highRisk = false) {
  exportHighRisk.value = highRisk
  exportPurpose.value = ''
  exportConfirmed.value = false
  showExport.value = true
}

const exportHighRisk = ref(false)

/**
 * 弹窗上写的「导出人数」必须等于**真正发出去的行数**。
 *
 * 2026-09-17 修：此前这里显示 `priorityQueue.length`（7），而请求里根本没带
 * `student_ids`，后端于是导出**范围内全部档案**（12，含已关闭的那份）——把一份
 * 人数对不上的敏感文件发了出去。现在两边同源：显示这个数组的长度，请求也发它。
 *
 * 去重是必要的：`list_care_cases` 一行一份**档案**，一名学生可以既有已关闭的旧档案、
 * 又有在办的新档案（§1 记着的那条时序），而 `export_service` 是**按学生**去重的。
 * 不去重的话数字会比 CSV 多。
 */
const exportStudentIds = computed(() => [
  ...new Set(priorityQueue.value.map(c => c.student_id))
])

/**
 * 高度关注导出走的是 `high_risk_only`：后端在调用者范围内**逐学生**取最近一场，
 * 等级为 KEY_ATTENTION 的才写一行（`export_service.py:133`）。所以这个数字也只能按人
 * 去数——队列里一名学生可能有两份档案。口径与后端同源：`cases` 的 `total_level` 就是
 * 每人的最近一场（`care_service.list_care_cases` 逐行调 `latest_session`）。
 */
const exportHighRiskCount = computed(
  () => new Set(
    cases.value.filter(c => c.total_level === 'KEY_ATTENTION').map(c => c.student_id)
  ).size
)

/** 弹层上写的人数，取决于这一份导出真正覆盖什么。 */
const exportCount = computed(() =>
  exportHighRisk.value ? exportHighRiskCount.value : exportStudentIds.value.length
)

async function confirmExport() {
  if (!exportPurpose.value) {
    showToast('error', '请选择导出用途')
    return
  }
  if (!exportConfirmed.value) {
    showToast('error', '请确认导出责任')
    return
  }
  // The mask/scope selects were previously collected but never sent.
  const options = {
    purpose: exportPurpose.value,
    maskNames: exportMask.value === 'masked',
    includeScore: exportFields.value === 'score'
  }
  try {
    // The workbench exports **它显示的那条队列**；逐学生的导出在个案详情页。
    // 高度关注那一份走另一个端点、口径本来就是全范围的 KEY_ATTENTION，故不传名单。
    if (exportHighRisk.value) {
      await exportHighRiskCareCases(options)
    } else {
      await exportCareCases({ ...options, studentIds: exportStudentIds.value })
    }
    showExport.value = false
    showToast('success', '受控导出已完成并记录审计')
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">心理工作中心</div>
        <h1>今日工作台</h1>
        <!-- 副标题此前写的是一句**设计说明**（「以待复核、逾期跟进和复测任务为核心，
             不默认展开高敏感内容」）——它说的是这一页是怎么设计的，而读者站在这一页上
             要回答的问题是「这些数字说的是谁」。同一句话在「我的范围筛查统计」那一页
             已经换成口径了（`AnalyticsPage`，e2e 按 `.page-desc` 断言它）。
             这里每一个数都按数据范围算（§9），所以这一行说的就是范围。
             取不到 `scopes` 时换一句不给数字的：仍然说的是范围，只是不说成「全校」。 -->
        <p class="page-desc">
          <template v-if="scopeText">范围：{{ scopeText }}。下面每个数字与每一条队列都在这个范围内统计。</template>
          <template v-else>下面每个数字与每一条队列都按你的授权范围统计。</template>
        </p>
      </div>
      <div class="actions">
        <!-- 三个按钮**都没有 `primary`**，这是 2026-09-20 改的，刻意如此。
             `primary` 是这一页上最强的一个视觉信号（实心蓝底），它必须落在
             「这一页为什么存在」那件事上——而这一页的存在理由是**处理队列**，
             那个队列就在下面，不需要一个按钮把它叫出来。
             「高度关注导出」此前戴着 `primary`：它是这三个里**最小众**的一个
             （一份跨全范围的、不在这张队列上的名单），却长得最响。
             不是把它降级成次要，是**这一页没有主操作**——三件事各有各的场合，
             没有一个比另外两个更该被首先点到，那就谁也别假装是。 -->

        <!-- 学生导入属于账号与组织治理，仅管理员可做，故此处不再提供入口。
             题库导入产出草稿、不改变任何判定，心理老师可在此准备。 -->
        <button class="btn" @click="router.push('/counselor/data')">题库导入</button>
        <!-- 2026-09-17 修：这里此前**只有一个**写着「受控导出」的按钮，而它传的是
             `openExport(true)`——于是它永远走**高度关注导出**那条分支：弹层标题说
             「高度关注导出」、导的是全范围的 KEY_ATTENTION、弹层上那行「导出人数」
             却写着队列的长度。同屏三句话互相矛盾，用户按哪一句理解都会错。
             现在两个入口各写各的名字、各做各的事（与重点学生页的两个按钮同一个形状）：
             队列导出走名单，全范围的走 `high_risk_only`。 -->
        <button class="btn" @click="openExport()">导出优先队列</button>
        <!-- 这一份导的是**全范围的重点关注**，不是左边那张队列——两个导出按钮挨在
             一起，唯一的差别在名字里，而「优先队列」与「高度关注」都是四个字的抽象词，
             扫一眼分不出哪个是本页的、哪个是全校的。所以这一处的边界写在弹层里
             （弹层标题、以及那行「「高度关注」就是关注等级里的**重点关注**」，
             见 §3 那条约定）。**不要在这里再加一句小字**：`.page-head` 的 `.actions`
             是一行按钮，塞进去的说明在 1280px 下会把这行挤到第二排。 -->
        <button class="btn" @click="openExport(true)">高度关注导出</button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="metrics" :rows="4" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <!-- `&& !error` 是必要的：少了它，加载失败时红条与「0 项 / 尚无已提交的测评数据」
         会同屏出现，读者会读成「这所学校什么都没测」。 -->
    <template v-if="!loading && !error">
      <!-- 指标卡。
           可点的那几张带 `role="button"` 与 Enter/Space：它们此前只有鼠标到得了
           （`<article @click>` 不可聚焦，键盘用户拿不到这四个入口中最常用的两个）。
           用 `role` 补而不是换成真正的 `<button>`：`.metric` 是网格子项，按钮的
           默认样式（字体、对齐、背景、边框）都要一条条复位，而这四张卡的版式有
           「布局完整性」用例按计算值盯着。 -->
      <div class="grid metrics">
        <article class="metric" :data-tone="metricTone(metrics?.pending_review, 'red')" role="button" tabindex="0" @click="goCases('PENDING_REVIEW')" @keydown.enter.prevent="goCases('PENDING_REVIEW')" @keydown.space.prevent="goCases('PENDING_REVIEW')">
          <div class="metric-label">待人工复核</div>
          <div class="metric-value">{{ metrics?.pending_review ?? 0 }}</div>
          <!-- 单位是**条**不是人：`pending_risk_events` 数的是 `risk_event` 的行，
               而引擎对每一道**命中的重点题**各写一行（MHT 有两道：85 / 97）。
               一道是 / 两道也是的学生在这张卡上会读成 2 人，而档案只有一份。 -->
          <div class="metric-foot">重点题命中 {{ metrics?.pending_risk_events ?? 0 }} 条</div>
        </article>
        <article class="metric" :data-tone="metricTone(overdueCount, 'amber')" role="button" tabindex="0" @click="goCases('overdue')" @keydown.enter.prevent="goCases('overdue')" @keydown.space.prevent="goCases('overdue')">
          <div class="metric-label">逾期跟进</div>
          <!-- 这个数**必须**跟点进去的那个队列同一个来源（`c.overdue`，与「已逾期」
               页签、"已逾期"药丸同一个标志）。此前它写的是 `metrics.following`，
               即 status == FOLLOWING 的档案数——那是「跟进中」，不是「逾期」：
               真实数据上它读 10，而「已逾期」队列是 0 条，卡片与它自己指向的列表
               各说各话。FOLLOWING 这个数没浪费，挪到脚注里当上下文。 -->
          <div class="metric-value">{{ overdueCount }}</div>
          <!-- 「逾期」在右边那块面板上是一个**不同的单位**：这一格数的是**档案**
               （一份档案算一次），而「近期提醒」数的是**跟进记录**（项）——同一天里
               这一格读 2、那一块读 19，两边都叫「已逾期」，而它们问的不是同一件事。
               两处此前都没有写单位，读者只能自己猜，猜错的那一半会以为其中一个是坏的。
               所以这一格把单位写出来（点击进的是档案队列，单位就是档案）。 -->
          <div class="metric-foot">跟进中 {{ metrics?.following ?? 0 }} 份 · 逾期按档案计</div>
        </article>
        <article class="metric" data-tone="teal" role="button" tabindex="0" @click="goCases('all')" @keydown.enter.prevent="goCases('all')" @keydown.space.prevent="goCases('all')">
          <div class="metric-label">关注档案总数</div>
          <div class="metric-value">{{ cases.length }}</div>
          <div class="metric-foot">其中已关闭 {{ closedCaseCount }} 份</div>
        </article>
        <article class="metric" data-tone="green" role="button" tabindex="0" @click="goTasks" @keydown.enter.prevent="goTasks" @keydown.space.prevent="goTasks">
          <!-- 口径是「我的数据范围内」，不是全校：分母跟 scope 走。原标签写「本任务」
               也不对——这个数跨任务合并，单任务完成率在测评任务页看。 -->
          <div class="metric-label">我的学生完成率</div>
          <div class="metric-value">{{ metrics?.completion_rate ?? 0 }}%</div>
          <!-- 另外三格的脚注都在给上面那个数**提供上下文**（重点题命中几条、
               跟进中几份、其中已关闭几份），只有这一格写的是「点击查看测评任务」——
               一句操作提示。卡片本身就是可点的（`role="button"` + hover 抬高），
               提示它「可以点」是四格里唯一一句不关于数据的话，而它占的正是读者用来
               判断「75% 是好还是坏」的那个位置。
               换成一个口径句：`CounselorWorkbench` 只有四个字段，这个端点不提供
               「应测人数」，所以这里给不出第二个数——**编一个没有出处的数比不给更糟**。
               这句回答的是「75% 的分母是什么」，而分母为什么是这些学生由页头那行范围说明。 -->
          <div class="metric-foot">按数据范围统计 · 覆盖全部测评任务</div>
        </article>
      </div>

      <!-- 优先工作队列 + 本周提醒 -->
      <div class="grid main-side" style="margin-top:17px">
        <article class="card">
          <div class="card-head">
            <h2>优先工作队列</h2>
            <!-- 这一块是**预览**（旁边就是「查看全部」），所以行数要写出来：
                 表被滚动区裁掉之后，「看得见的 8 行」与「一共 8 行」不再是一回事。 -->
            <span v-if="priorityQueue.length" class="pill gray">{{ priorityQueue.length }} 条</span>
            <button class="btn small" @click="goCases('all')">查看全部</button>
          </div>
          <div class="card-body">
            <div class="table-wrap queue-scroll">
              <table>
                <thead>
                  <tr>
                    <th>学生</th>
                    <th>当前阶段</th>
                    <th>关注等级</th>
                    <th>负责人</th>
                    <th>下次处理</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <!-- 整行可点：这一列是「操作」，而整行的**其余五列**才是读者真正
                       在读的东西——他看完姓名、阶段、等级、下次处理日期之后要做的
                       下一件事就是「进去看看」，而这一步此前要求把鼠标移到最右边
                       那个 74px 的按钮上（实测 1440px 下它只露出 11px，1280px 下 0px，
                       见 `styles.css` 里 `.queue-scroll table` 那一段）。

                       **不给这一行加 `role="button"`**，也不加 `tabindex`：`<tr>` 是
                       表格的行，改成按钮会让读屏软件读不出「第 3 行，共 9 行」，
                       也会让 `getByRole('button', { name: '进入档案' })` 撞上重复匹配
                       （`e2e/vocabulary.spec.ts` 正是用它开档案弹层的）。
                       键盘这条路一直有，就是右边那个按钮——**它留着**，
                       而且它现在不再是唯一的路。 -->
                  <tr v-for="c in priorityQueue" :key="c.case_id" class="queue-row" @click="openDetail(c.student_id)">
                    <td>
                      <div class="student-cell">
                        <!-- 首字母圆片是**装饰**：名字就在它右边。不加 aria-hidden 的话
                             读屏软件会把这一格念成「林 林同学」——多出来的那一个字
                             还会让人以为姓名里带点什么。 -->
                        <div class="student-avatar" aria-hidden="true">{{ c.student_name[0] }}</div>
                        <div>
                          <strong>{{ c.student_name }}</strong>
                          <div class="muted tiny">{{ c.student_no }} · {{ c.grade }}{{ c.class_name }}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span :class="['pill', statusTone(c.case_status)]">{{ statusLabel(c.case_status) }}</span>
                    </td>
                    <td>
                      <span :class="['pill', levelTone(c.total_level)]">{{ levelLabel(c.total_level) }}</span>
                    </td>
                    <td>{{ c.owner_name || '未分配' }}</td>
                    <td>
                      <span v-if="c.overdue" class="pill red">已逾期</span>
                      <span v-else>{{ c.next_follow_up_date || '—' }}</span>
                    </td>
                    <td>
                      <!-- `@click.stop` 不能省：整行已经挂了同一个 `openDetail`，
                           不拦住冒泡就是同一个学生连着取两次详情（弹层闪一下、
                           多一个请求），而失败时还会弹两条一模一样的 toast。 -->
                      <button class="btn small" @click.stop="openDetail(c.student_id)">进入档案</button>
                    </td>
                  </tr>
                  <tr v-if="!priorityQueue.length">
                    <td colspan="6">
                      <div class="empty">没有待复核或高优先级的档案。</div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <!-- 这句话此前**只长在空态里**——也就是只在队列一条都没有的时候出现。
                 于是有 9 行的时候读者看到的就是「我的在办档案都在这里了」，而实际上
                 其他仍在观察、但尚未形成工作项的档案不会进入该队列。
                 它与下面「近期提醒」那句「另有 N 项未显示」是同一类声明（§10：
                 凡是截断，都要自己说出来），区别只是那一处截的是**条数**、这一处截的是
                 **判据**——两处都在回答「你看到的这份清单是不是全部」。 -->
            <p v-if="observingElsewhere > 0" class="muted tiny" style="margin-top:10px">
              另有 {{ observingElsewhere }} 份一般观察档案不在这个队列里（未逾期、非待复核、且未超出一般范围），可在「查看全部」里查看。
            </p>
          </div>
        </article>

        <article class="card">
          <div class="card-head">
            <h2>近期提醒</h2>
            <!-- 失败时徽标不再报「0 项」：那个数会被读成「没有待办」。
                 数的是 `reminderTotal` 而不是 `reminders.length`：后者是**被截断后**
                 的那一批，每个来源封顶 20——40 条待办会显示成 20，而读者会照 20 安排工作。 -->
            <span v-if="notesError" class="pill gray">加载失败</span>
            <span v-else :class="['pill', reminderTotal ? 'amber' : 'gray']">{{ reminderTotal }} 项</span>
          </div>
          <div class="card-body">
            <ErrorState v-if="notesError" :message="notesError" :on-retry="loadPanels" />
            <template v-else>
              <div v-if="reminders.length" class="timeline">
                <div v-for="(r, i) in reminders" :key="i" class="timeline-item">
                  <div class="timeline-dot" :class="{ overdue: r.overdue }"></div>
                  <!-- 类别走 `labels.ts`（§3 第一面），不在视图里写三元：那句
                       `kind === 'RETEST' ? '复测' : '跟进'` 会把认不出的第三类
                       读成「跟进」。 -->
                  <div class="muted tiny">{{ r.when }} · {{ reminderKindLabel(r.kind) }}</div>
                  <div class="timeline-title">{{ r.title }}</div>
                  <div class="timeline-text">{{ r.desc }}</div>
                </div>
              </div>
              <div v-else class="muted tiny">近 30 天内没有待办跟进或复测。</div>
              <!-- 截断必须自己说出来。没有这一句，20 条与 400 条长得一模一样，
                   而这一块正是老师排工作量的地方。 -->
              <p v-if="remindersTruncated" class="muted tiny" style="margin-top:10px">
                另有 {{ remindersHidden }} 项未显示，请到「重点学生」逐条处理。
              </p>
              <!-- 两支是**互斥且穷尽**的，所以「去哪处理」这句话不会两遍也不落空。
                   没有截断时这一支同样要写：`.timeline` 有 460px 的上限，19 条里
                   14 条在折线之下——**没有超过服务端的 20 条上限，所以上面那句不出现**，
                   而屏幕上看起来就是「提醒只有这五条」。它与队列那一句是同一类声明
                   （「你看到的这份清单是不是全部」），差别是这一处裁的是**高度**。 -->
              <p v-else-if="reminders.length" class="muted tiny" style="margin-top:10px">
                共 {{ reminders.length }} 项，列表可上下滚动；逐条处理请到「重点学生」。
              </p>
            </template>
          </div>
        </article>
      </div>

      <!-- 维度分布 + 工作边界 -->
      <div class="grid two" style="margin-top:17px">
        <article class="card pad">
          <h2>主要维度分布</h2>
          <p class="muted tiny" style="margin-top:6px">按已测评学生中该维度达到「高分」的比例排序。</p>
          <ErrorState v-if="dimsError" :message="dimsError" :on-retry="loadPanels" />
          <div v-else-if="topDimensions.length" class="bar-list" style="margin-top:16px">
            <div v-for="d in topDimensions" :key="d.dimension_code" class="bar-row">
              <span>{{ dimensionLabel(d.dimension_code) }}</span>
              <div class="bar">
                <i
                  :class="d.high_rate >= settings.ui.dimension_high_threshold ? 'high' : d.high_rate > 0 ? 'medium' : ''"
                  :style="{ width: `${Math.max(d.high_rate, 1)}%` }"
                ></i>
              </div>
              <b>{{ d.high_rate }}%</b>
            </div>
          </div>
          <div v-else class="muted tiny" style="margin-top:16px">尚无已提交的测评数据。</div>
        </article>
        <article class="card pad">
          <h2>工作边界</h2>
          <div class="notice" style="margin-top:15px">
            量表仅用于筛查。量表结果、学校关注事件、人工复核和持续跟进分别保存，不生成医学诊断。
          </div>
          <div class="detail-grid" style="margin-top:12px">
            <div class="detail-row"><span>高敏感查看</span><b>二次进入</b></div>
            <div class="detail-row"><span>数据导出</span><b>用途确认</b></div>
            <div class="detail-row"><span>原始答卷</span><b>强审计</b></div>
            <div class="detail-row"><span>管理员</span><b>默认无权查看</b></div>
          </div>
        </article>
      </div>
    </template>

    <!-- 学生档案抽屉 -->
    <Modal
      :model-value="!!detail"
      :title="detail ? `${detail.student.name} · ${detail.student.grade}${detail.student.class_name}` : ''"
      size="lg"
      @update:model-value="detail = null"
    >
      <template v-if="detail">
        <dl>
          <div>
            <dt>档案状态</dt>
            <dd><span :class="['pill', statusTone(detail.case_status)]">{{ statusLabel(detail.case_status) }}</span></dd>
          </div>
          <div>
            <dt>筛查分类</dt>
            <dd><span :class="['pill', levelTone(detail.assessment.total_level)]">{{ levelLabel(detail.assessment.total_level) }}</span></dd>
          </div>
          <div>
            <dt>效度状态</dt>
            <dd>
              <span :class="['pill', validityTone(detail.assessment.validity_status)]">{{
                validityLabel(detail.assessment.validity_status)
              }}</span>
            </dd>
          </div>
        </dl>
        <div class="detail-actions">
          <button type="button" @click="saveReview">记录人工复核</button>
          <button type="button" @click="saveFollowUp">新增跟进</button>
          <button type="button" @click="saveFamilyContact">家庭回访</button>
          <button type="button" @click="saveRetest">安排复测</button>
          <button v-if="detail.case_status !== 'CLOSED'" type="button" @click="closeCase">关闭档案</button>
          <button v-else type="button" @click="reopenCase">重新打开</button>
        </div>
        <h3>风险事件</h3>
        <ul>
          <li v-for="event in detail.risk_events" :key="event.id">
            命中重点关注题目，建议心理老师及时人工复核。 ·
            <span :class="['pill', riskEventStatusTone(event.status)]">{{
              riskEventStatusLabel(event.status)
            }}</span>
          </li>
        </ul>
        <h3>跟进记录</h3>
        <ul>
          <li v-for="record in detail.follow_ups" :key="record.id">
            {{ record.record_type }} · 下次 {{ record.next_follow_up_date }}
          </li>
        </ul>
        <h3>家庭回访</h3>
        <ul>
          <li v-for="record in detail.family_contacts" :key="record.id">
            {{ record.contact_date }} · {{ record.channel }} · {{ record.result }}
          </li>
        </ul>
        <h3>复测计划</h3>
        <ul>
          <li v-for="plan in detail.retest_plans" :key="plan.id">
            {{ plan.planned_date }} · {{ retestStatusLabel(plan.status) }}
          </li>
        </ul>
      </template>
    </Modal>

    <!-- 导出弹窗 -->
    <Modal :model-value="showExport" :title="exportHighRisk ? '高度关注导出' : '受控导出学生摘要'" size="lg" @update:model-value="showExport = $event">
      <div class="form-grid">
        <div class="notice danger">
          学生心理数据属于高敏感数据。默认不导出原始答卷、重点题、访谈正文或家庭回访正文。
        </div>
        <!-- 「高度关注」与等级表里的「重点关注」是**同一个 `KEY_ATTENTION`**，同一个
             屏幕上压着两套叫法（按钮与弹层标题叫「高度关注」，下面的药丸叫「重点关注」）。
             名字不动的理由：审计动作码 `导出高度关注摘要` 已经落了几百行，改掉界面上的
             名字会让用户在审计页按眼睛看到的名字搜不到（缺口 7 那条「看不懂换成搜不到」
             的教训）。所以把等号写在这里——这是用户将要动手的那一处。 -->
        <p v-if="exportHighRisk" class="muted tiny">
          「高度关注」就是关注等级里的<b>重点关注</b>（同一档），这一份导出的是该档学生。
        </p>
        <div class="form-two" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px">
          <div class="field">
            <label>导出人数</label>
            <input :value="`${exportCount}人`" disabled />
          </div>
          <div class="field">
            <label>导出用途 <span class="required">*</span></label>
            <select v-model="exportPurpose">
              <option value="">请选择</option>
              <option v-for="purpose in settings.export.purposes" :key="purpose" :value="purpose">{{ purpose }}</option>
            </select>
          </div>
          <div class="field">
            <label>身份处理</label>
            <select v-model="exportMask">
              <option value="masked">姓名脱敏</option>
              <option value="full">保留姓名（需特别授权）</option>
            </select>
          </div>
          <div class="field">
            <label>字段范围</label>
            <select v-model="exportFields">
              <option value="minimum">最小必要字段</option>
              <option value="score">包含量表总分</option>
            </select>
          </div>
        </div>
        <label style="display:flex;gap:9px;margin-top:15px;align-items:flex-start">
          <input v-model="exportConfirmed" type="checkbox" />
          <span>我确认该导出用于授权工作范围，并接受审计记录</span>
        </label>
      </div>
      <template #footer>
        <button class="btn" @click="showExport = false">取消</button>
        <button class="btn primary" @click="confirmExport">确认导出</button>
      </template>
    </Modal>

    <ConfirmDialog
      :open="showConfirm"
      :title="confirmTitle"
      :message="confirmMessage"
      :danger="confirmDanger"
      :confirm-text="confirmText"
      cancel-text="取消"
      @confirm="onConfirm"
      @cancel="onCancelConfirm"
      @update:open="showConfirm = $event"
    />

    <FormDialog
      :open="showForm"
      :title="formTitle"
      :fields="formFields"
      :submit-text="formSubmitText"
      @submit="onFormSubmit"
      @cancel="onFormCancel"
      @update:open="showForm = $event"
    />

  </div>
</template>
