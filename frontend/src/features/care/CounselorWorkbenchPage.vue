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
import { daysFromNow, formatDate, today } from '../../services/dates'
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
  getDataScopeSummary,
  getMe,
  reopenCareCase,
  type AnalyticsReport,
  type CareCaseDetail,
  type CareCaseItem,
  type CounselorWorkbench,
  type DataScopeSummary,
  type ReminderItem
} from '../../services/api'
// 「今天必须处理」与「我负责的 / 未分配 / 全部」两条判据与重点学生页共用一份定义
// （§5.14.3 验收：切换后列表、数量、空态一致）。**别在本文件里再写一遍比较。**
import {
  isDueToday,
  matchesOwner,
  normalizeOwnerFilter,
  OWNER_TABS,
  type OwnerFilter
} from '../../services/careQueue'

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
 * 当前登录者的 `user_account.id`，供「我负责的」那一档比对 `owner_id`。
 *
 * 取自已有的 `getMe()`（`load()` 为了 `role_code` 本来就要调一次），所以这是一次
 * **已经在路上**的请求，不是为筛选器新加的一次往返。
 *
 * 取不到时留 `null`——`matchesOwner` 在 `null` 下让「我负责的」恒假（见
 * `services/careQueue.ts` 那段），于是那一档会显示空列表而不是把「未分配」的档案
 * 一起冒充成「我负责的」。
 */
const currentUserId = ref<number | null>(null)

/**
 * 「我负责的 / 未分配 / 全部」。
 *
 * **默认 `all`**：`owner_id` 为空的在办档案是真实存在的一档（工作台那条
 * 「未分配负责人」的聚合就是为它写的），默认收成「我负责的」会让那些档案
 * 在默认视图里消失——用户看不出是筛选器干的。
 *
 * 初始值可以来自地址栏（`?owner=mine`），因为工作台那条「查看全部」要把当前这一档
 * 带到重点学生页去，回来时（浏览器后退）也该停在原来那一档上。
 */
const ownerFilter = ref<OwnerFilter>(normalizeOwnerFilter(router.currentRoute.value.query.owner))

/**
 * 页头那行范围口径。
 *
 * `load()` 一直在调 `getMe()`——它要拿 `role_code` 判这一页该不该给这个人看——
 * 但**取到之后用完就丢**，于是这一页上没有任何一个字说明「这些数字描述的是谁的范围」。
 *
 * 而这一页每一个数都是**按数据范围**算出来的（服务端的 `student_scope_predicate`，
 * §9）：`getCareCases()` 只回调用者范围内的档案，`completion_rate` 的分母也是。
 * 一个只带 1 个班范围的心理老师看到「关注档案总数 12」，会读成「这所学校 12 份档案」。
 * §9 那句「范围数字必须在 UI 上写明口径，否则它冒充全校数字，比不给数字更糟」
 * 说的正是这一处，而心理老师那几页统计（`筛查关注概览` 等，`/counselor/analytics/*`）
 * 已经这么做了——它们的页头由 `ReportPageHeader.vue` 渲染，带一枚「当前数据范围」徽标（§5.2）。
 *
 * 这一行与「全部学生」页头那一行（`CasesPage.vue` 的 `scopeText`）现在读的是**同一个
 * 来源**：服务端的 `GET /auth/me/data-scope-summary`（§5.3）。此前两处各自把
 * `/auth/me` 的 `scopes[]` 拼一遍——那是全站第二套范围口径，而它与服务端不等价
 * （并集、`SCHOOL` 短路那两条的详细理由写在 `CasesPage.vue` 那一处）。
 * 「同一件事只许有一个定义」在 UI 上也成立：两页各拼一遍，改一处漏一处的时候，
 * 同一个人在两个屏幕上会读到两种范围。
 *
 * 取不到时**整句不出现**（`v-if`），不猜一个「全校」——一句说不清的范围比没有范围更糟，
 * 因为它会被照着安排工作。
 */
const scopeSummary = ref<DataScopeSummary | null>(null)
const scopeText = computed(() => scopeSummary.value?.displayText ?? '')

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

/**
 * 负责人档位下的档案。队列与它那两条「另有 N 份」的句子都从这里派生，
 * 所以「切到未分配之后列表 3 条、旁边还写着另有 9 份」这种各说各话不可能发生。
 *
 * 筛选是**客户端**的，输入是服务端已经按数据范围过滤过的数组（§9）：
 * 它只会把结果集收得更小，不可能放宽——「按负责人筛」不是一条越权通道。
 */
const ownerScopedCases = computed(() =>
  cases.value.filter(c => matchesOwner(c, ownerFilter.value, currentUserId.value))
)

/**
 * 优先工作队列的档位。§5.14.3 第 1 条要求首屏按这个次序固定：
 * 今天必须处理 → 已逾期 → 待人工复核 → 其他在办工作。
 *
 * 判据互斥（`isDueToday` 里排掉了已逾期的），所以次序不会因为两档同时成立而变得
 * 取决于比较顺序；`sort` 是稳定排序，同一档内保留服务端给的
 * `updated_at DESC, id DESC`——**不在这里重排同一档**，那会是同一件事的第二个
 * 排序口径（与 `dueReminders` 那条注释同源）。
 *
 * 第 3 档是「其他在办工作」：进了队列、但既没到期也没逾期的那些
 * （超出一般范围的在办档案、以及待复核里日期还没到的）。
 */
function priorityRank(c: CareCaseItem): number {
  if (isDueToday(c)) return 0
  if (c.overdue) return 1
  if (c.case_status === 'PENDING_REVIEW') return 2
  return 3
}

// 优先工作队列：未关闭，且满足其一 ——
//   1. 今天必须处理（排了跟进日期、日期不晚于今天）
//   2. 已逾期
//   3. 待人工复核（无论筛查等级，复核本身就是待办）
//   4. 超出一般范围（需要持续关注）
// 第 3 档的那一类「待人工复核」指标卡统计的正是它，二者口径必须一致，
// 否则会出现「指标说 1 条、队列却说没有」的矛盾——所以那张卡现在也从
// `cases` 现算（见 `pendingReviewCount`），不再读服务端的另一个数。
function rankedQueue(filter: OwnerFilter): CareCaseItem[] {
  const ranked = cases.value
    .filter(c => matchesOwner(c, filter, currentUserId.value))
    .filter(
      c =>
        c.case_status !== 'CLOSED' &&
        (isDueToday(c) ||
          c.overdue ||
          c.case_status === 'PENDING_REVIEW' ||
          c.total_level !== 'GENERAL_RANGE')
    )
    .map((item, order) => ({ item, order, rank: priorityRank(item) }))
  // `order` 那一段不是多余的：显式写出来，「同一档内保持服务端次序」就不再依赖
  // 引擎的稳定排序，也不依赖读的人记得它是稳定的。
  ranked.sort((a, b) => a.rank - b.rank || a.order - b.order)
  return ranked.map(entry => entry.item)
}

const priorityQueue = computed(() => rankedQueue(ownerFilter.value))

/**
 * 三个页签各自的条数（§5.14.3 验收「切换后列表、数量、空态一致」）。
 *
 * **它们是「切过去会看到几条」，不是「这个负责人名下一共有几份档案」。** 两个数
 * 不是一回事：队列是一张筛过的表（未关闭 + 四档判据之一），而页签右边那个
 * `{{ priorityQueue.length }}` 条数的正是筛完之后的数。若页签上写的是「名下一共几份」，
 * 切到「我负责的（12）」却只看到 3 行，读者会以为表格漏了 9 行。
 *
 * 所以三者共用同一个 `rankedQueue(...)`——同一份数组、同一组判据，
 * 页签上的数与切过去之后表头上那个数**是同一个函数调用**，构造上不可能不一致。
 * 代价是筛三遍（每遍几十行），换掉一整类「筛选器说 5 条、列表说 3 条」的矛盾。
 */
const ownerCounts = computed(() => {
  const counts = {} as Record<OwnerFilter, number>
  for (const tab of OWNER_TABS) counts[tab.key] = rankedQueue(tab.key).length
  return counts
})

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
  () =>
    ownerScopedCases.value.filter(c => c.case_status !== 'CLOSED').length -
    priorityQueue.value.length
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

/**
 * 「今天必须处理」指标卡的数（§5.14.3 第 1 条：它是首屏的第一档）。
 *
 * 与 `priorityQueue` 的第 0 档共用 `isDueToday` 这一个判据——那张卡点进去的
 * `/counselor/cases?filter=today` 用的是同一个函数（`CasesPage` 的 `matchQueue`），
 * 所以「卡片说 3 件、点进去 0 条」在构造上不可能。
 */
const todayCount = computed(() => cases.value.filter(isDueToday).length)

/**
 * 「待人工复核」指标卡的数。
 *
 * 它此前读 `metrics.pending_review`（服务端一个专门的 COUNT），而这张卡点进去的是
 * `/counselor/cases?filter=PENDING_REVIEW`，也就是 `cases` 里 `case_status` 等于
 * 那个码的那一批。两者**今天**是同一个集合（同一条 `student_scope_predicate`、
 * 同一个状态码），但那是两条各自的查询凑巧一致——同 §11 那条
 * 「指标卡上的数必须与它点进去的那个列表同源」，所以它现在从 `cases` 现算，
 * 与 `overdueCount` 同一个写法，构造上不可能漂。
 *
 * `metrics` 那三个字段仍然在用（`pending_risk_events` 与 `completion_rate`），
 * 没有变成死代码。
 */
const pendingReviewCount = computed(
  () => cases.value.filter(c => c.case_status === 'PENDING_REVIEW').length
)

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
 * 「一周之内」的分界（V2.0.0 §5.13.7 Phase E 第 5 条：工作台首屏优先呈现
 * 今天 / 逾期 / 待复核，未来 7–30 天**默认折叠或只展示摘要**）。
 *
 * 7 是判据的一部分，不是排版参数——下面那句摘要里写的也是「7 天之后」，
 * 两处要一起改。
 */
const REMINDER_SOON_DAYS = 7

/**
 * 首屏要看见的那一批。服务端已经按「逾期优先、再按天的远近」排过序，
 * 所以这里**只切分、不重排**——重排就是同一件事的第二个排序口径（§11 那条「同源」）。
 *
 * 判据是 `days`（负数 = 逾期，0 = 今天）而不是去解 `when` 那句中文：
 * 「已逾期 3 天」/「5 天后」的文本解析是同一个距离的第二个定义，
 * 改一次文案就静默失效（后端为此专门发了 `days`，见 `analytics_service.counselor_reminders`）。
 */
const dueReminders = computed(() => reminders.value.filter(r => r.days <= REMINDER_SOON_DAYS))

/**
 * 未来 8–30 天。**不逐条列出、也不做展开**：`assets/styles.css` 的 `.timeline`
 * 有 460px 上限，那是量出来的（2026-09-17：22 条全铺开 1805px，把工作台拉成 1941px；
 * 2026-09-20 实测可见 6 条 / 共 9 条），而第 5 条明文允许的另一支是「只展示摘要」。
 * 所以这一段只报数 + 说最近的一条在哪天 + 给出口。
 */
const laterReminders = computed(() => reminders.value.filter(r => r.days > REMINDER_SOON_DAYS))

/** 折叠那批里最近的一条在几天后。读服务端排好的次序，不在这里再排一遍。 */
const laterSoonestDays = computed(() => laterReminders.value[0]?.days ?? 0)

/**
 * 折叠那一档**按类别**分出来的数，拼成「复测 6 项」这样一句。
 *
 * 加这一条不是修辞：**首屏折叠之后，这一句是 `RETEST` 在界面上唯一的落点**。
 * 实测（2026-09-26，开发库）：20 条提醒里 `days <= 7` 的 14 条全是 `FOLLOW_UP`，
 * 6 条 `RETEST` 全在 `days = 29`——也就是说折起来之后，上面那条时间线一次都不会再
 * 渲染「复测」这两个字。而 `e2e/vocabulary.spec.ts` 正是在 `/counselor/workbench`
 * 上守 `REMINDER_KIND_LABELS` 那两个码的（2026-09-20 建的覆盖）。
 * **它是负向断言，扫不到东西照样绿**——覆盖会静默归零，所以那一句必须念出类别。
 * 换句话说：删掉这个 computed 不会让任何用例变红，只会让一个真实的守卫空转。
 *
 * 类别中文一律走 `labels.ts` 的 `reminderKindLabel`（§3 第一面），
 * 不在视图里写第二张码表——写在这里就等于把 `labelOf` 的「认不出原样回退」也一起绕过，
 * 而那正是这个守卫要抓的两种故障之一。
 */
const laterKindSummary = computed(() => {
  const counts = new Map<string, number>()
  for (const item of laterReminders.value) counts.set(item.kind, (counts.get(item.kind) ?? 0) + 1)
  return [...counts.entries()].map(([kind, count]) => `${reminderKindLabel(kind)} ${count} 项`).join('、')
})

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
    // 这一次请求本来就是为 `role_code` 发的，`id` 顺手带上——「我负责的」那一档
    // 要拿它比 `owner_id`，不另发一次 `/auth/me`。
    currentUserId.value = current.id
    // 页头那行范围口径（见 `scopeText`）。**单独 try**：这一句话读不到不该把整张
    // 工作台一起拦下——它是口径说明，不是这一页的数据。
    try {
      scopeSummary.value = await getDataScopeSummary()
    } catch {
      scopeSummary.value = null
    }
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
 *
 * **不带 `owner`**（2026-09-27 §5.18.4 改）。它此前带过，理由是「一个把队列收在
 * 『我负责的』上的老师点进来，不该看到全部负责人的档案」——那条理由只对「查看全部」
 * 一张卡成立，却加在了**五张**卡上，于是另外四张卡的数字被它们自己的目的地否证了。
 * 实测（工作台切到 `?owner=mine`）：「逾期跟进」卡读 2、点进去列表读 **1 人**；
 * 「待人工复核」卡读 3、点进去读 **2 人**。
 *
 * 判据是**卡片的数从哪来**：四张卡的计数全部从 `cases` 全域现算
 * （`todayCount` / `overdueCount` / `pendingReviewCount` / `cases.length`），
 * 一个都不套 `matchesOwner`——owner 档的 `aria-label` 是「按负责人筛选**队列**」，
 * 它只筛它下面那份队列，上面那五张卡不受它影响（那句口径就写在模板里 owner 档
 * 的下方）。**目的地不该比出发点的数字更窄。** 想按负责人看，列表页自己有那三档。
 *
 * 这与 CLAUDE.md §11 是同一条，而这一张卡此前已经犯过一次同样的错：它当时读
 * `metrics.following`（跟进中的**档案**数），而它指向的「已逾期」队列按 `c.overdue`
 * 筛——真实数据上一个读 10、一个 0 条。`setOwnerFilter` 那条 URL 往返不受影响：
 * 工作台自己的 `?owner=` 照旧写进地址栏，`:96` 在 setup 时读回来。
 */
function goCases(filter = 'all') {
  router.push({ path: '/counselor/cases', query: { filter } })
}

/**
 * 切换「我负责的 / 未分配 / 全部」。
 *
 * **写回地址栏**（`replace` 而不是 `push`）：切页签不是一次导航，把它记进历史会让
 * 「后退」变成「退回上一个筛选档」，而用户想退回的是上一页。地址栏里留着它是为了
 * 另一件事——点「查看全部」跳到重点学生页之后，`goCases` 会把当前这一档带过去
 * （见那个函数），于是两屏说的是同一件事；回来时（浏览器后退）也停在原来那一档。
 *
 * 只动了 `query`，不重新请求：三个档判的是**同一份 `cases`**（服务端已经按数据范围
 * 过滤过），换筛选不需要一次往返——而且重新请求会让列表闪一下。
 */
function setOwnerFilter(next: OwnerFilter) {
  if (ownerFilter.value === next) return
  ownerFilter.value = next
  router.replace({ query: { ...router.currentRoute.value.query, owner: next } })
}

function goTasks() {
  router.push('/counselor/tasks')
}

/* ---------- 行操作：动词按当前阶段分 ---------- */

/**
 * 一行上的按钮写什么（§5.14.3 第 4 条：「避免所有行都叫『进入档案』」）。
 *
 * 此前每一行都是「进入档案」——而这一页的存在理由是**处理队列**：
 * 一张全是「进入档案」的表，把「该做什么」这件事整个推回给读的人。三个动词
 * 各自对应这一行此刻**缺的那一步**，而词表取的是这一页自己那四个动作的名字
 * （`记录人工复核` / `新增跟进记录`），不是另起一套。
 */
function rowActionLabel(c: CareCaseItem): string {
  if (c.case_status === 'PENDING_REVIEW') return '人工复核'
  if (c.case_status === 'FOLLOWING') return '记录跟进'
  return '查看档案'
}

/**
 * 点了那一行按钮之后发生什么。**动词必须真的落到那个动作上**（§5.14.3 的验收：
 * 「不能只换文案不带筛选/上下文」）——所以前两档不是「打开档案让用户自己找」，
 * 而是打开这份档案之后**直接把那一步的表单端上来**（`saveReview` / `saveFollowUp`
 * 都是弹表单的那两个函数，与详情里的按钮调的是同一个）。
 *
 * `detail.value` 为空说明取数失败（`openDetail` 已经 toast 过一句），
 * 此时**不叠一个表单**：对着一个还没读到的档案填复核记录，填完提交才会失败，
 * 而失败的原因是另一个。
 */
async function runRowAction(c: CareCaseItem) {
  await openDetail(c.student_id)
  if (!detail.value) return
  if (c.case_status === 'PENDING_REVIEW') {
    await saveReview()
    return
  }
  if (c.case_status === 'FOLLOWING') {
    await saveFollowUp()
  }
}

/* ---------- 业务操作 ---------- */

async function saveReview() {
  if (!detail.value) return
  const risk = detail.value.risk_events.find((item) => item.status === 'PENDING') || detail.value.risk_events[0]
  if (!risk) {
    showToast('error', '没有可复核的筛查信号')
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
  <!-- `workbench` 这个类只为一件样式存在：这一页的指标卡是**五张**，而全站
       `.grid.metrics` 的基础规则是四列。它不做成全局改动，因为那条规则同时喂着
       `SkeletonBlock.vue` 与 `LeaderOverviewPage.vue`（§5.14.3 第 1 条只要求
       这一页有固定的优先顺序，没有任何一句要求别处跟着变成五列）。
       三段式的原因见 `styles.css` 里那两条媒体查询——`.workbench .grid.metrics`
       的权重比媒体查询里那条 `.grid.metrics` 高，不在查询里重写一遍的话
       五列会一路漏到 375px。 -->
  <div class="workbench">
    <div class="page-head">
      <div>
        <div class="eyebrow">心理工作中心</div>
        <h1>今日工作台</h1>
        <!-- 副标题此前写的是一句**设计说明**（「以待复核、逾期跟进和复测任务为核心，
             不默认展开高敏感内容」）——它说的是这一页是怎么设计的，而读者站在这一页上
             要回答的问题是「这些数字说的是谁」。同一句话在心理老师那几页统计上
             已经换成口径了（`/counselor/analytics/*` 的页头，`ReportPageHeader.vue`
             那枚「当前数据范围」徽标，§5.2）。
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

        <!-- 「题库导入」2026-09-26 从这一行里挪走了（§5.14.3 第 5 条：「从每日工作台
             主操作区移除或降级为数据中心入口；工作台主操作围绕当天处置」）。
             它挪到了下面「工作边界」卡里，仍然是一个 `<button>`、仍然叫「题库导入」，
             所以它没有消失、也没有变成另一个东西——只是不再和「今天要处置什么」
             挤在同一行里。

             学生导入属于账号与组织治理，仅管理员可做，此处本来就没有入口。 -->
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

    <!-- `:columns` 而不是 `:rows`：`variant="metrics"` 那一支渲染的是
         `v-for="i in columns"`（`SkeletonBlock.vue`），`rows` 对它不起作用——
         这一行此前写着 `:rows="4"`，也就是「四张卡的骨架」这件事当时**没有任何东西
         在保证**，它恰好与卡片数相同靠的是 `rows` 的默认值 4 而不是这个 prop。
         2026-09-26 加第五张卡时才发现，所以一并改成真正生效的那一个。 -->
    <SkeletonBlock v-if="loading" variant="metrics" :columns="5" />
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
        <!-- 首屏第一格是「今天必须处理」（§5.14.3 第 1 条的次序：今天 → 逾期 → 待复核）。
             它的作用不止是多一个数：队列里的第 0 档此前**没有入口**——一条排了今天
             跟进的档案在最上面一行，而没有任何地方能回答「今天一共几件」。
             与 `overdueCount` 同一个写法（从 `cases` 现算），所以它与点进去的那个
             列表同源（`CasesPage` 的 `today` 档调的是同一个 `isDueToday`）。 -->
        <article class="metric" :data-tone="metricTone(todayCount, 'red')" role="button" tabindex="0" @click="goCases('today')" @keydown.enter.prevent="goCases('today')" @keydown.space.prevent="goCases('today')">
          <div class="metric-label">今天必须处理</div>
          <div class="metric-value">{{ todayCount }}</div>
          <!-- 「今天」与「逾期」互斥（`isDueToday` 里排掉了已逾期的），所以这一格
               不需要再减一遍逾期数——那一档有它自己那张卡，在右边。
               与「逾期跟进」脚下那句一样，这里说清**它没算谁**。 -->
          <div class="metric-foot">不含已逾期 · 按本地日期</div>
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
        <!-- 第三格。它在 2026-09-26 之前排在第二格，而 §5.14.3 第 1 条给的次序是
             「今天必须处理 → 已逾期 → 待人工复核」——顺序在那一句里是**判据的一部分**：
             它说的是「先看今天要动手的、再看已经拖了的，然后才是复核」。 -->
        <article class="metric" :data-tone="metricTone(pendingReviewCount, 'red')" role="button" tabindex="0" @click="goCases('PENDING_REVIEW')" @keydown.enter.prevent="goCases('PENDING_REVIEW')" @keydown.space.prevent="goCases('PENDING_REVIEW')">
          <div class="metric-label">待人工复核</div>
          <!-- 这个数从 `cases` 现算，不再读 `metrics.pending_review`（§5.14.3 第 7 条：
               指标卡上的数必须与它点进去的那个列表同源）。服务端那个数走的是
               `count(StudentCareCase) where status == PENDING_REVIEW`，而这张卡点进去的
               `CasesPage` 是按**每一行**的 `case_status` 筛的——两者今天算的是同一批档案，
               但它们各写一遍判据，而 `overdueCount` 那一处已经证过一次这种写法会漂
               （卡片读 `metrics.following`、列表按 `c.overdue`，真实数据上一个读 10 一个 0 条）。
               所以按同一处教训处理：从 `cases` 现算，构造上不可能与列表不一致。
               `metrics.pending_review` 仍在响应里、本文件不再取用它。 -->
          <div class="metric-value">{{ pendingReviewCount }}</div>
          <!-- 单位是**条**不是人：`pending_risk_events` 数的是 `risk_event` 的行，
               而引擎对每一道**命中的重点题**各写一行（MHT 有两道：85 / 97）。
               一道是 / 两道也是的学生在这张卡上会读成 2 人，而档案只有一份。 -->
          <div class="metric-foot">重点题命中 {{ metrics?.pending_risk_events ?? 0 }} 条</div>
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
        <!-- `tier-primary`：这一页**唯一**的主工作区（§5.15.2「同一页面最多一个」）。
             判据与上面那五张指标卡、下面那四块是什么关系：指标卡回答「整体什么水平」、
             维度分布回答「集中在哪」、工作边界回答「我不做什么」——**只有这一块回答
             「现在动手做什么」**，而工作台的全部目的就是这一句话。所以强调条给它，
             其余全部留在默认的二级层。 -->
        <article class="card tier-primary">
          <div class="card-head">
            <h2>优先工作队列</h2>
            <!-- 这一块是**预览**（旁边就是「查看全部」），所以行数要写出来：
                 表被滚动区裁掉之后，「看得见的 8 行」与「一共 8 行」不再是一回事。 -->
            <span v-if="priorityQueue.length" class="pill gray">{{ priorityQueue.length }} 条</span>
            <button class="btn small" @click="goCases('all')">查看全部</button>
          </div>
          <div class="card-body">
            <!-- 「我负责的 / 未分配 / 全部」（§5.14.3 第 3 条）。
                 归在**队列这一块**而不是页头：它筛的是这份队列，不筛上面那五张卡
                 （那些是聚合，其中完成率连负责人这一维都没有）。这一条口径必须
                 写在界面上（§9），否则「完成率 75%」与「只看我负责的」会读成互相矛盾。

                 每一档后面那个数是 `ownerCounts`——**「切过去会看到几条」**，
                 与切过去之后表头那个 `{{ priorityQueue.length }}` 同源（同一个
                 `rankedQueue` 调用），所以「我负责的（3）」点进去必然就是 3 条。
                 空态与「另有 N 份」跟着一起走：三处都从筛完的数组派生。

                 样式与 `CasesPage` 的状态档共用一组声明（`styles.css` 里
                 `.queue-tab, .owner-tab { … }` 那条），但**类名不共用**：
                 `e2e/app.spec.ts` 按 `.queue-tab` 数状态档、按 `.queue-tab.active`
                 断当前档，同用一类会让那两个定位器一次命中两个元素（严格模式直接红）。 -->
            <div class="owner-tabs" role="group" aria-label="按负责人筛选队列">
            <!-- 内容与收尾标签写在**同一行**：中间换行会被 Vue 的 whitespace:condense 压成
               一个尾随空格，于是 `textContent` 是「全部（20） 」——`toHaveText(/^全部（\d+）$/)`
               这类逐字断言会因此红在一个与文案无关的地方（`getByRole` 的 accessible name
               会归一化，所以只有逐字断言看得见）。 -->
              <button
                v-for="tab in OWNER_TABS"
                :key="tab.key"
                type="button"
                :class="['owner-tab', { active: ownerFilter === tab.key }]"
                :aria-pressed="ownerFilter === tab.key"
                @click="setOwnerFilter(tab.key)"
              >{{ tab.label }}（{{ ownerCounts[tab.key] }}）</button>
            </div>
            <!-- 口径句（§9）。两件事分开说：
                 ① 这一档筛的是什么——`all` 也要说，因为「全部」这四个字会被读成
                    「全校」，而它其实是「我的数据范围内、全部负责人」；
                 ② 筛的只是这份队列，上面那五张卡不受它影响。 -->
            <p class="muted tiny" style="margin:8px 0 12px">
              <template v-if="ownerFilter === 'all'">
                范围：我的数据范围内全部负责人的在办档案（不受上方指标卡影响）。
              </template>
              <template v-else-if="ownerFilter === 'mine'">
                范围：只列负责人是我的在办档案（不受上方指标卡影响）。
              </template>
              <template v-else>
                范围：只列尚未分配负责人的在办档案——它们不会出现在任何人的「我负责的」里。
              </template>
            </p>
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
                       也会让 `getByRole('button', { name: … })` 撞上重复匹配
                       （`e2e/vocabulary.spec.ts` 正是用右边那枚按钮开档案弹层的，
                       它按 `.queue-scroll tbody tr button` 取第一枚、读出它的名字再用）。
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
                      <span v-else>{{ formatDate(c.next_follow_up_date) }}</span>
                    </td>
                    <td>
                      <!-- `@click.stop` 不能省：整行已经挂了同一个 `openDetail`，
                           不拦住冒泡就是同一个学生连着取两次详情（弹层闪一下、
                           多一个请求），而失败时还会弹两条一模一样的 toast。
                           这一处尤其不能省：`runRowAction` 会接着弹表单，冒泡上去
                           等于同一个学生取两次详情、再开两次表单。 -->
                      <button class="btn small" @click.stop="runRowAction(c)">{{ rowActionLabel(c) }}</button>
                    </td>
                  </tr>
                  <!-- 空态是一句**关于数据**的话，所以它得说清是哪一档筛出来的空
                       （§5.14.3 验收：「切换后列表、数量、空态一致」）。
                       三句话都只描述数据，不带「请稍后再试」那类别的意思——
                       失败是上面那条 `ErrorState` 的活（整个 body 在 `!error` 之下，
                       所以这里不会在读取失败时冒出来，见 `:839` 那个 `v-if`）。 -->
                  <tr v-if="!priorityQueue.length">
                    <td colspan="6">
                      <div class="empty">
                        <template v-if="ownerFilter === 'mine'">你名下没有待处理的档案。</template>
                        <template v-else-if="ownerFilter === 'unassigned'">没有未分配的待处理档案。</template>
                        <template v-else>没有待复核或高优先级的档案。</template>
                      </div>
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
              <div v-if="dueReminders.length" class="timeline">
                <div v-for="(r, i) in dueReminders" :key="i" class="timeline-item">
                  <div class="timeline-dot" :class="{ overdue: r.overdue }"></div>
                  <!-- 类别走 `labels.ts`（§3 第一面），不在视图里写三元：那句
                       `kind === 'RETEST' ? '复测' : '跟进'` 会把认不出的第三类
                       读成「跟进」。 -->
                  <div class="muted tiny">{{ r.when }} · {{ reminderKindLabel(r.kind) }}</div>
                  <div class="timeline-title">{{ r.title }}</div>
                  <div class="timeline-text">{{ r.desc }}</div>
                </div>
              </div>
              <!-- 两句的判据不同，不能合成一句：`reminders` 为空说的是「这份清单本来就是
                   空的」，而「一周内为空、更远的地方有」不是空态——它是**被折叠**，
                   下一句紧接着就说那几条在哪。合成一句会让前者替后者说话（§14）。 -->
              <div v-else class="muted tiny">
                {{ reminders.length ? '一周内没有待办跟进或复测。' : '近 30 天内没有待办跟进或复测。' }}
              </div>
              <!-- 截断必须自己说出来。没有这一句，20 条与 400 条长得一模一样，
                   而这一块正是老师排工作量的地方。 -->
              <p v-if="remindersTruncated" class="muted tiny" style="margin-top:10px">
                另有 {{ remindersHidden }} 项未显示，请到「重点关注学生」逐条处理。
              </p>
              <!-- 两支是**互斥且穷尽**的，所以「去哪处理」这句话不会两遍也不落空。
                   没有截断时这一支同样要写：`.timeline` 有 460px 的上限，19 条里
                   14 条在折线之下——**没有超过服务端的 20 条上限，所以上面那句不出现**，
                   而屏幕上看起来就是「提醒只有这五条」。它与队列那一句是同一类声明
                   （「你看到的这份清单是不是全部」），差别是这一处裁的是**高度**。
                   数的是 `dueReminders` 而不是 `reminders`：这段话说的是**上面那块**
                   （一周内那一批）有几条，说 `reminders.length` 会把折叠掉的那几条
                   也算进来——那是同一屏上第二句会撒谎的话（§14）。 -->
              <p v-else-if="dueReminders.length" class="muted tiny" style="margin-top:10px">
                上面共 {{ dueReminders.length }} 项（一周内），列表可上下滚动；逐条处理请到「重点关注学生」。
              </p>
              <!-- 未来 7–30 天那一档**只报数，不逐条列出**（Phase E 第 5 条：
                   「默认折叠或只展示摘要」，取后者——展开会让 `.timeline` 突破 460px
                   那个量出来的上限，见 `styles.css` 那两段注释）。
                   徽标写的是 30 天内的总数，而上面那块只装得下一周内的，两者之差就是这一句；
                   所以这句话**必须出现**，否则读者会以为「{{ reminderTotal }} 项」全在上面那五条里。
                   `laterKindSummary` 那一段解释了为什么这里要念出**类别**（它是 `RETEST`
                   在界面上的唯一落点），不要因为「一句话更短」把它删掉。 -->
              <p v-if="laterReminders.length" class="muted tiny" style="margin-top:6px">
                未来 7 天之后的提醒有 {{ laterReminders.length }} 项（{{ laterKindSummary }}，最近一项在
                {{ laterSoonestDays }} 天后），这里只报数、未逐条列出；要看这几条请到「重点关注学生」。
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
        <!-- `tier-supporting`：这一块是**口径与边界说明**，正是 §5.15.2 给 Supporting
             Information 举的第一个例子（「口径、隐私说明、帮助说明、次要信息」）。
             它旁边的「主要维度分布」是业务统计，留在默认的二级层——两块并排时如果
             长得一样重，读者会把「我不做什么」读成与「问题集中在哪」同级的信息。 -->
        <article class="card pad tier-supporting">
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
          <!-- 「题库导入」降级到这里（§5.14.3 第 5 条）。归在「工作边界」这一块而不是
               页头，是因为它回答的问题是关于**边界**的：导入产出的是草稿、不参与评分、
               不改变任何判定（§7「草稿不生效，发布归管理员」），所以它不属于
               「今天要处置什么」——把它和「导出优先队列」摆在同一行，会让一件
               一周做一次、做完什么都不变的准备工作读成当天的动作。

               它仍然**留在这一页**面而不是只靠侧边栏那个「数据中心」入口，理由与
               `CasesPage` 那条一致：这一页是这个角色落地的地方，一条只存在于导航里的路
               等于让每个人都得先知道它在那儿。也不在页头 `.actions` 里加小字说明
               （那行在 1280px 下会被挤到第二排）。
               按钮文案与路由都**没变**，`e2e/app.spec.ts` 那条「题库导入 → 数据中心」
               仍然按名字找得到它。 -->
          <p class="muted tiny" style="margin-top:14px">
            题库导入产出的是草稿，不参与评分、不改变任何判定（发布归管理员）：
            <button class="btn small" @click="router.push('/counselor/data')">题库导入</button>
          </p>
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
          <!-- 六枚并列的工作入口里，只有这一枚改的是档案的**存续**（其余四枚各加一条
               记录、重开是把档案推回流程）。此前六枚共用 `.detail-actions button`
               那一套白底灰边，于是「关闭档案」与「记录人工复核」长得一模一样
               （V2.0.0 §5.14.6 第 5 条：危险操作分离并确认）。
               确认那一步本来就有（`closeCase` 里的 `showConfirmation`），缺的只是
               「看得出来」这一半——在按下之前就认得出哪一枚有代价。

               `.btn.danger` 是**红字红边**而不是实心红：全站 `.btn-danger`（实心）
               只用在 ConfirmDialog 的确认键上，那才是真正下手的时刻。 -->
          <button v-if="detail.case_status !== 'CLOSED'" type="button" class="btn danger" @click="closeCase">关闭档案</button>
          <button v-else type="button" @click="reopenCase">重新打开</button>
        </div>
        <h3>筛查信号</h3>
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
            {{ record.record_type }} · 下次 {{ formatDate(record.next_follow_up_date) }}
          </li>
        </ul>
        <h3>家庭回访</h3>
        <ul>
          <li v-for="record in detail.family_contacts" :key="record.id">
            {{ formatDate(record.contact_date) }} · {{ record.channel }} · {{ record.result }}
          </li>
        </ul>
        <h3>复测计划</h3>
        <ul>
          <li v-for="plan in detail.retest_plans" :key="plan.id">
            {{ formatDate(plan.planned_date) }} · {{ retestStatusLabel(plan.status) }}
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
