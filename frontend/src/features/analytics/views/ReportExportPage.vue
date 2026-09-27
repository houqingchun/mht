<script setup lang="ts">
/**
 * RPT-09 报表专业解读与导出 —— 独立页面。
 *
 * V2.0.0 §5.13 Phase A / B 整页重写。改之前这一页只有一份**握在内存里**的报告：
 * 刷新一次就没了、库里那份草稿既看不见也回不去；发布不带版本号、导出借 `window.prompt`
 * 问用途；「专业记录」那张卡还是 `status ? '草稿' : '待审核'` 推出来的。现在它有三个来源
 * 分得开的显示态（实时分析 / 当前版本冻结快照 / 历史版本快照），每条状态都读服务端字段。
 *
 * 三件事在这一页上必须同时成立，各自的落点写在各自那一段注释里：
 *   1. **显示的统计数字是哪一版的**——`displayedReport` 三态，界面上用一句话写明（§9）；
 *   2. **哪些字还没存进库**——`draft` 与 `savedDraft` 逐段比出来的 `dirty`；
 *   3. **离开这一页会不会丢掉它们**——三条边界分开实现（`beforeChange` / 路由 / 刷新）。
 *
 * 2026-09-27（§5.15.4）：打开报告之后那条操作条上，「这是**哪一份**报告」不再要靠正文去猜
 * ——名称 / 编号 / 任务范围 / 最近发布版本四项事实与版本药丸摆在同一行；四枚动作的**主次
 * 随状态变**（`primaryAction`），蓝色只给此刻真正能往前走的那一枚。两条的理由都写在
 * 各自那一段注释里。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import ConfirmDialog from '../../../components/ConfirmDialog.vue'
import FormDialog, { type FormField } from '../../../components/FormDialog.vue'
import ProfessionalReportList from '../components/ProfessionalReportList.vue'
import ProfessionalReportVersions from '../components/ProfessionalReportVersions.vue'
import { useSettings } from '../../../composables/useSettings'
import { createLatestRequest } from '../../../services/latest-request'
import { formatDateTime } from '../../../services/dates'
import { reportStatusLabel, reportStatusTone, reportVersionLabel } from '../../../services/labels'
import {
  createProfessionalReport,
  exportProfessionalReport,
  getAnalyticsReport,
  getProfessionalReport,
  getProfessionalReportVersion,
  listProfessionalReports,
  newReportVersion,
  publishProfessionalReport,
  saveProfessionalReport,
  type AnalyticsReport,
  type AssessmentTaskItem,
  type ProfessionalReport,
  type ProfessionalReportListItem,
  type ProfessionalReportVersion,
  type ProfessionalReportVersionDetail
} from '../../../services/api'

const { settings, loadSettings } = useSettings()

/* ── 实时分析（筛选栏那条路） ─────────────────────────────────────────── */

const analysis = ref<AnalyticsReport | null>(null)
const analysisLoading = ref(false)
const analysisError = ref('')
const selectedTaskIds = ref<number[]>([])
const analysisRequest = createLatestRequest()

/* ── 我的报告 ───────────────────────────────────────────────────────── */

const reports = ref<ProfessionalReportListItem[]>([])
const reportsLoading = ref(false)
const reportsError = ref('')
/** 任务 id → 名称，把 `task_scope.task_ids` 说成人看得懂的范围摘要。 */
const taskNames = ref<Record<number, string>>({})

/* ── 打开着的那一份报告 ─────────────────────────────────────────────── */

const opened = ref<ProfessionalReport | null>(null)
const openLoading = ref(false)
const openError = ref('')
/** 正在看的历史版本快照（`null` = 跟着当前版本走，即编辑态）。 */
const versionDetail = ref<ProfessionalReportVersionDetail | null>(null)
const viewingVersion = ref<number | null>(null)
const openRequest = createLatestRequest()

/* ── 正文与脏状态 ───────────────────────────────────────────────────── */

const draft = ref<string[]>(['', '', '', ''])
/** 最后一次「服务端确认收下」的正文。`dirty` 是它与 `draft` 的逐段比较。 */
const savedDraft = ref<string[]>(['', '', '', ''])
const dirty = computed(() => draft.value.some((value, i) => value !== savedDraft.value[i]))

/* ── 提交态与提示 ───────────────────────────────────────────────────── */

const sub = ref<'interpretation' | 'export'>('interpretation')
const notice = ref('')
const noticeTone = ref<'info' | 'ok' | 'error'>('info')
const saving = ref(false)
const publishing = ref(false)
const newVersioning = ref(false)
const exporting = ref(false)
const lastExport = ref<{ at: string; jobNo: string; purpose: string; version: string } | null>(null)
/** 上一次填过的导出用途：导出失败时它留在这一页上，再点一次只需重选版本。 */
const lastPurpose = ref('')

const fields = ['整体情况说明', '重点维度解释', '样本覆盖及效度说明', '后续教育支持计划']

/* ── 派生：显示的是哪一版、能不能改 ─────────────────────────────────── */

/**
 * 界面上那些数字的**唯一来源**，三态（§5.13 Phase A）。
 *
 * 历史版本快照只有 `GET /professional-reports/{id}/versions/{n}` 给得出来——
 * 版本列表里的 `versions[]`（`_content_payload`）**不含** `statistics_snapshot`，
 * 所以打开历史版本必须真的发一次请求，不能拿列表里那一行充数。
 */
const displayedReport = computed<AnalyticsReport | null>(() => {
  if (versionDetail.value) return versionDetail.value.statistics_snapshot
  if (opened.value) return opened.value.statistics_snapshot
  return analysis.value
})

/** 当前版本（不是报告头）的状态。V2 草稿期间报告头已经是 DRAFT，而 V1 仍然是 PUBLISHED。 */
const currentVersionStatus = computed(
  () => opened.value?.current_version_status ?? opened.value?.status ?? null
)

/** 这一份报告被打开时用的是哪一组任务——筛选栏改它不改变已打开报告的口径。 */
const scopeTaskIds = computed(() => opened.value?.task_scope?.task_ids ?? selectedTaskIds.value)

/** 只读 = 正在看历史版本，或当前版本已经发布。历史版本一律只读，与它自己发没过没关系。 */
const readOnly = computed(() => viewingVersion.value !== null || currentVersionStatus.value === 'PUBLISHED')

const displayLoading = computed(() => analysisLoading.value || openLoading.value)

/**
 * 状态模型只认服务端字段（§5.13 Phase A）：`未创建 / 草稿 · Vn / 已发布 · Vn`。
 * 从前那张卡写的是 `status ? '草稿' : '待审核'`——「待审核」这个状态在服务端**不存在**。
 */
const openStateLabel = computed(() => {
  const report = opened.value
  if (!report) return '未创建'
  if (viewingVersion.value !== null) return `历史版本 · V${viewingVersion.value}`
  return reportVersionLabel(currentVersionStatus.value, report.current_version)
})

const modeNote = computed(() => {
  if (viewingVersion.value !== null) {
    return `下面显示的是 V${viewingVersion.value} 当时冻结的统计快照，服务端不会按现在的数据重算；这一版的正文只读。`
  }
  if (opened.value) {
    return `下面显示的是 ${opened.value.report_no} 的 V${opened.value.current_version} 保存时冻结的统计快照；改动上面的筛选不会改变它。`
  }
  return '还没有打开报告：下面显示的是按当前筛选条件实时算出来的统计结果，保存之后才会冻结成某一份报告的快照。'
})

const professionalHint = computed(() => {
  if (!opened.value) return '统计事实与人工判断分开'
  if (viewingVersion.value !== null) return '历史版本只读'
  if (readOnly.value) return '已发布版本已锁定，要改请先新建版本'
  return dirty.value ? '有未保存的修改' : '与服务器一致'
})

const canCreate = computed(
  () => !opened.value && selectedTaskIds.value.length > 0 && analysis.value !== null
)
const canPublish = computed(
  () => opened.value !== null && !readOnly.value && currentVersionStatus.value === 'DRAFT'
)
const canNewVersion = computed(() => {
  const report = opened.value
  if (!report) return false
  return (report.latest_published_version ?? 0) > 0 && report.current_version_status === 'PUBLISHED'
})
const canExport = computed(() => (opened.value?.versions?.length ?? 0) > 0)

/**
 * 四枚动作的主次**随状态变**（§5.15.4 第 4 条）：蓝色只给「此刻真正能往前走的那一枚」。
 *
 * 改之前 `保存草稿` 恒定是蓝色的，于是**已发布 / 历史只读**时它是一枚被置灰的蓝色按钮，
 * 而唯一有意义的那一枚（`基于当前版本继续编辑`）是白的——主按钮指着一个按不动的东西，
 * 是主次关系里最糟的一种。
 *
 * 三档：
 *  · 当前可编辑（未打开但能新建 / 已打开的草稿）→ `save`；
 *  · 只读时还有出路（当前版本已发布 → 能新建版本）→ `newVersion`；
 *  · 都不是（正看着历史版本，而当前版本是草稿；或还没报告也没选任务）→ `null`，
 *    此时**没有**主按钮：前者的出路在顶上那条「回到 Vn（可编辑）」，后者在下面那句
 *    提示里，两处都由界面自己写明了，不靠一枚点不动的蓝按钮冒充指引。
 *
 * **发布刻意不做主按钮**：它生成的是一个永久保留、不可覆盖的版本（§5.13），
 * 把一个不可逆的动作画成最显眼的那一枚，等于催着人按它。
 */
const primaryAction = computed<'save' | 'newVersion' | null>(() => {
  if (!readOnly.value) return opened.value || canCreate.value ? 'save' : null
  return canNewVersion.value ? 'newVersion' : null
})

const publishDisabledReason = computed(() => {
  if (!opened.value) return '先保存草稿，才能发布'
  if (viewingVersion.value !== null) return '正在看历史版本，回到当前版本后才能发布'
  if (readOnly.value) return '当前版本已经发布；要继续修改请先新建版本'
  return ''
})

const scopeSummary = computed(() => {
  const ids = scopeTaskIds.value
  if (!ids.length) return '尚未选择测评任务'
  const names = ids.map(id => taskNames.value[id]).filter(Boolean)
  if (!names.length) return `共 ${ids.length} 个测评任务`
  return names.length === 1 ? names[0] : `${names[0]} 等 ${ids.length} 个任务`
})

/**
 * 最近发布版本那一格（§5.15.4 第 1 条：现有数据可得时就要展示）。
 *
 * 这一列此前在界面上**一个渲染点都没有**——`latest_published_version` 有类型、只有
 * `canNewVersion` 一个读者（它判「能不能新建版本」），于是「这份报告上一次发布是哪一版、
 * 什么时候」在打开报告之后答不出来：`openStateLabel` 说的是**当前**版本。
 *
 * 拿不到版本行时只回 `Vn`（不编一个时间）：`versions` 是随详情一起下来的，
 * 真缺了说明服务端没给，而一个猜的时间戳比没有更糟（§11）。
 */
const latestPublishedLabel = computed(() => {
  const report = opened.value
  const no = report?.latest_published_version ?? 0
  if (!report || !no) return '尚未发布'
  const publishedAt = report.latest_published_at
  return publishedAt ? `V${no} · ${formatDateTime(publishedAt)}` : `V${no}`
})

/** 有效期取已有的系统配置（服务端 `settings_service.DEFAULTS` 下发），不硬编码第二个 24。 */
const ttlHours = computed(() => settings.value.export.job_ttl_hours)
const purposes = computed(() => settings.value.export.purposes)

/* ── 命令式弹层 ─────────────────────────────────────────────────────── */

const confirmOpen = ref(false)
const confirmTitle = ref('确认')
const confirmMessage = ref('')
const confirmConfirmText = ref('确认')
const confirmCancelText = ref('取消')
const confirmDanger = ref(false)
let confirmResolve: ((value: boolean) => void) | null = null

function askConfirm(
  title: string,
  message: string,
  confirmText: string,
  danger = false,
  cancelText = '取消'
): Promise<boolean> {
  confirmTitle.value = title
  confirmMessage.value = message
  confirmConfirmText.value = confirmText
  confirmCancelText.value = cancelText
  confirmDanger.value = danger
  confirmOpen.value = true
  return new Promise(resolve => {
    confirmResolve = resolve
  })
}

/**
 * 三条出路都经过它，谁都只 resolve 一次。
 *
 * **`update:open` 那一条不能省**：`Modal` 的 Escape 与遮罩点击只发
 * `update:modelValue false`，`ConfirmDialog` 把它转成 `update:open false`——它**不发**
 * `cancel`。漏了这一条，`onBeforeRouteLeave` 里的那个 Promise 就永远挂着，用户按下 Esc
 * 之后路由再也走不动。
 */
function settleConfirm(value: boolean) {
  const resolve = confirmResolve
  confirmResolve = null
  confirmOpen.value = false
  if (resolve) resolve(value)
}
function onConfirm() { settleConfirm(true) }
function onConfirmCancel() { settleConfirm(false) }
function onConfirmOpen(value: boolean) { if (!value) settleConfirm(false) }

const formOpen = ref(false)
const formTitle = ref('')
const formDescription = ref('')
const formSubmitText = ref('提交')
const formFields = ref<FormField[]>([])
let formResolve: ((values: Record<string, string>) => void) | null = null

function askForm(): Promise<Record<string, string>> {
  formOpen.value = true
  return new Promise(resolve => {
    formResolve = resolve
  })
}
function settleForm(values: Record<string, string> | null) {
  const resolve = formResolve
  formResolve = null
  formOpen.value = false
  if (resolve) resolve(values ?? {})
}
function onFormSubmit(values: Record<string, string>) { settleForm(values) }
function onFormCancel() { settleForm(null) }
function onFormOpen(value: boolean) { if (!value) settleForm(null) }

/* ── 读取 ───────────────────────────────────────────────────────────── */

/** 任务列表由 `FilterBar` 递过来——同一页上不拉第二遍（一次失败一次成功时两处会各说各话）。 */
function onTasksLoaded(list: AssessmentTaskItem[]) {
  const map: Record<number, string> = {}
  for (const task of list) map[task.id] = task.name
  taskNames.value = map
}

async function loadReports() {
  reportsLoading.value = true
  reportsError.value = ''
  try {
    reports.value = await listProfessionalReports()
  } catch (err) {
    reports.value = []
    reportsError.value = err instanceof Error ? err.message : '报告列表加载失败'
  } finally {
    reportsLoading.value = false
  }
}

async function loadAnalysis(taskIds: number[]) {
  const token = analysisRequest.begin()
  analysisLoading.value = true
  analysisError.value = ''
  // 取数之前先清空（§14）：换一组任务之后，屏幕上任何一处都不该还留着上一组的数据。
  analysis.value = null
  try {
    const data = await getAnalyticsReport([...taskIds], 'ALL_CALCULATED')
    if (!analysisRequest.isCurrent(token)) return
    analysis.value = data
  } catch (err) {
    if (!analysisRequest.isCurrent(token)) return
    analysisError.value = err instanceof Error ? err.message : '报表加载失败'
  } finally {
    // `catch` 与 `finally` 里也要判：迟到的请求不能替后来者把 loading 关掉。
    if (analysisRequest.isCurrent(token)) analysisLoading.value = false
  }
}

function retryAnalysis() {
  if (selectedTaskIds.value.length) loadAnalysis(selectedTaskIds.value)
}

/* ── 打开 / 关闭报告 ────────────────────────────────────────────────── */

function draftFrom(version?: ProfessionalReportVersion | null): string[] {
  return [
    version?.overall_summary ?? '',
    version?.dimension_interpretation ?? '',
    version?.sample_validity_note ?? '',
    version?.support_plan ?? ''
  ]
}

/**
 * 用服务端返回的整份报告替换当前显示的那一份。
 *
 * 四条写路由（新建 / 保存草稿 / 新版本 / 发布）都带 `include_versions=True`，
 * 所以响应里有完整的 `versions[]`——整份替换不会把版本时间线弄丢。
 */
function applyOpened(report: ProfessionalReport) {
  opened.value = report
  versionDetail.value = null
  viewingVersion.value = null
  draft.value = draftFrom(report.content)
  savedDraft.value = [...draft.value]
}

function closeOpened() {
  opened.value = null
  versionDetail.value = null
  viewingVersion.value = null
  openError.value = ''
  draft.value = ['', '', '', '']
  savedDraft.value = ['', '', '', '']
}

async function selectReport(id: number) {
  const target = reports.value.find(item => item.id === id)
  if (!target) return
  if (!(await confirmDiscard(`打开「${target.title}」`))) return
  const token = openRequest.begin()
  openLoading.value = true
  openError.value = ''
  // 先清空再取（§14）：标题写着 B、正文是 A 的那种屏幕，就是从「等成功再覆盖」来的。
  versionDetail.value = null
  viewingVersion.value = null
  opened.value = null
  draft.value = ['', '', '', '']
  savedDraft.value = ['', '', '', '']
  try {
    const detail = await getProfessionalReport(id)
    if (!openRequest.isCurrent(token)) return
    applyOpened(detail)
    notice.value = `已打开 ${detail.report_no}。`
    noticeTone.value = 'info'
  } catch (err) {
    if (!openRequest.isCurrent(token)) return
    openError.value = err instanceof Error ? err.message : '报告加载失败'
  } finally {
    if (openRequest.isCurrent(token)) openLoading.value = false
  }
}

async function closeOpenedWithGuard() {
  if (!(await confirmDiscard('关闭这份报告'))) return
  closeOpened()
  notice.value = '已关闭报告，回到实时分析。'
  noticeTone.value = 'info'
}

/**
 * 打开某一版看看它当时写了什么。
 *
 * 点**当前版本**那一行 = 退出预览、回到可编辑态——判据与 `ProfessionalReportVersions`
 * 的 `activeVersion` 逐字相同（没选 = 跟着当前版本走），否则会出现「当前 V2」与「V1」
 * 同时高亮这种屏幕。
 */
async function openVersion(versionNo: number) {
  const report = opened.value
  if (!report) return
  if (versionNo === report.current_version) {
    if (viewingVersion.value === null) return
    versionDetail.value = null
    viewingVersion.value = null
    draft.value = draftFrom(report.content)
    savedDraft.value = [...draft.value]
    notice.value = `回到 V${report.current_version}，可以继续编辑。`
    noticeTone.value = 'info'
    return
  }
  if (!(await confirmDiscard(`打开历史版本 V${versionNo}`))) return
  const token = openRequest.begin()
  openLoading.value = true
  versionDetail.value = null
  try {
    const detail = await getProfessionalReportVersion(report.id, versionNo)
    if (!openRequest.isCurrent(token)) return
    versionDetail.value = detail
    viewingVersion.value = versionNo
    draft.value = draftFrom(detail.content)
    savedDraft.value = [...draft.value]
    notice.value = `正在查看 V${versionNo}：正文与统计快照都是那一版冻结下来的，只读。`
    noticeTone.value = 'info'
  } catch (err) {
    if (!openRequest.isCurrent(token)) return
    // 失败就退回可编辑的当前版本，不能让面板停在一片空正文上。
    viewingVersion.value = null
    draft.value = draftFrom(report.content)
    savedDraft.value = [...draft.value]
    notice.value = err instanceof Error ? err.message : '历史版本加载失败'
    noticeTone.value = 'error'
  } finally {
    if (openRequest.isCurrent(token)) openLoading.value = false
  }
}

/* ── 筛选栏那三个边界 ───────────────────────────────────────────────── */

function sameTaskSet(a: number[], b: number[]) {
  if (a.length !== b.length) return false
  const set = new Set(a)
  return b.every(id => set.has(id))
}

function onQuery(filters: { taskIds: number[] }) {
  selectedTaskIds.value = [...filters.taskIds]
  // 换了一组任务 → 打开着的报告退场，回到实时分析。
  // 走到这里说明 `beforeChange` 那道门已经过了（用户点了「查询」并确认放弃修改）。
  //
  // 唯一绕过那道门的是 `FilterBar` 的自动加载与失败重试（`query(true)`），而它**进不到
  // 这一支**：重试只发生在任务列表拉失败之后，那时 `tasks` 是空的、下拉框里没有东西可勾，
  // 所以 `filters.taskIds` 只可能是 `loadTasks()` 自己填的那一个（与已打开的相同）。
  if (opened.value && !sameTaskSet(scopeTaskIds.value, filters.taskIds)) closeOpened()
  if (filters.taskIds.length) loadAnalysis(filters.taskIds)
}

function onReset() {
  closeOpened()
  analysis.value = null
  analysisError.value = ''
  notice.value = ''
  noticeTone.value = 'info'
  sub.value = 'interpretation'
}

/**
 * 有没保存的字就先问一句。**三种边界共用这一句**，但它们各自触发的方式不同：
 * 筛选栏走 `beforeChange`、路由离开走 `onBeforeRouteLeave`、刷新/关标签走 `beforeunload`。
 *
 * **这句话在 Phase A 之后改过一遍**，因为它此前把两件事说成了同一件。原文是「它们
 * 只在这个浏览器里，离开之后就找不回来了」——那是草稿还存在浏览器里的年代的实话：
 * 整份正文都不在服务端。现在草稿落在服务端，真正会丢的**只有还没保存的那几段修改**，
 * 而已经保存的那一版好好地在库里、随时能从「我的报告」里打开。照旧文案说，用户会
 * 以为这一段工作整个没了——于是他可能为保住它而放弃一次本来无害的切换，或者在真的
 * 丢掉之后不去找那份其实还在的草稿（§2：那句话本来就是写给用户看的）。
 */
async function confirmDiscard(action: string): Promise<boolean> {
  if (!dirty.value) return true
  return askConfirm(
    '放弃未保存的修改？',
    `「${action}」会丢掉这四段正文里还没保存的修改；已经保存到服务器的那一版不受影响，之后可以从「我的报告」里重新打开。`,
    '放弃修改',
    true
  )
}

async function beforeChange(): Promise<boolean> {
  return confirmDiscard('切换筛选条件')
}

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  return confirmDiscard('离开这一页')
})

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
  // 规范里 `returnValue` 已废弃，但除 Chromium 之外的浏览器仍然读它。
  event.returnValue = ''
}

/* ── 保存 / 发布 / 新版本 ───────────────────────────────────────────── */

function content() {
  return {
    overall_summary: draft.value[0],
    dimension_interpretation: draft.value[1],
    sample_validity_note: draft.value[2],
    support_plan: draft.value[3]
  }
}

function formatClock() {
  const now = new Date()
  return `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
}

/** 排序去重之后的任务集合，用来判「是不是同一个口径」。 */
function scopeKey(ids: number[]) {
  return [...new Set(ids)].sort((a, b) => a - b).join(',')
}

/**
 * 同一 owner + 同一口径 + 同一组任务，已经有一份**草稿**。
 *
 * 「同一 owner」由服务端保证：`listProfessionalReports` 对心理老师只回他自己建的
 * （按 `created_by` 过滤），所以这份列表里每一行都是「我的」。
 *
 * **它只是一道提示，不是不变量**（§5.13 Phase A 明写：做成硬约束要么由后端幂等保证、
 * 要么补并发测试）。这里的取舍是**不做成数据库唯一键**——产品上同一组任务允许多份报告
 * （不同侧重、不同时点），而 `task_scope` 是 JSON、要变唯一键得先派生一列 `scope_key`
 * 并在所有写入路径上维护它。所以这一句是「不静默重复创建」的落点，理由记在 PROGRESS。
 */
function findSameScopeDraft(taskIds: number[]): ProfessionalReportListItem | null {
  const key = scopeKey(taskIds)
  return reports.value.find(item =>
    (item.current_version_status ?? item.status) === 'DRAFT'
    && item.analysis_mode === 'ALL_CALCULATED'
    && scopeKey(item.task_scope?.task_ids || []) === key
  ) ?? null
}

async function save(): Promise<boolean> {
  if (saving.value || readOnly.value) return false
  const taskIds = scopeTaskIds.value
  if (!opened.value) {
    if (!taskIds.length) {
      notice.value = '请先选择测评任务'
      noticeTone.value = 'error'
      return false
    }
    const existing = findSameScopeDraft(taskIds)
    if (existing) {
      const reuse = await askConfirm(
        '已经有一份同样范围的草稿',
        `「${existing.title}」（${existing.report_no}，${formatDateTime(existing.updated_at)} 更新）用的是同一组测评任务与统计口径。接着写它可以保留上次的正文；也可以另外新建一份。`,
        '继续编辑现有草稿',
        false,
        '仍然新建一份'
      )
      if (reuse) {
        await selectReport(existing.id)
        notice.value = '已打开现有草稿，可以接着写。'
        noticeTone.value = 'info'
        return true
      }
    }
  }
  saving.value = true
  notice.value = '保存中…'
  noticeTone.value = 'info'
  let savedOk = false
  try {
    const payload = content()
    const saved = opened.value
      ? await saveProfessionalReport(opened.value.id, payload)
      : await createProfessionalReport({
          title: `${analysis.value?.task?.name || '测评任务'} 专业分析报告`,
          task_ids: taskIds,
          analysis_mode: 'ALL_CALCULATED',
          ...payload
        })
    applyOpened(saved)
    notice.value = `草稿已保存至服务器（${saved.report_no}）· ${formatClock()}`
    noticeTone.value = 'ok'
    savedOk = true
  } catch (err) {
    // **失败时不动 `draft` 与 `savedDraft`**：一次网络抖动不该让刚写的一段话从屏幕上
    // 消失——它还没进库，屏幕上那份就是唯一的副本。
    notice.value = err instanceof Error ? err.message : '保存失败'
    noticeTone.value = 'error'
  } finally {
    // 「保存中…」只描述**保存这一个请求**；列表刷新是另一件事。此前 `loadReports()`
    // 排在这个 `finally` 之前，于是刷新一慢，「保存中…」就一直挂着，**遮住**那句
    // 已经写好的「草稿已保存至服务器（…）」——模板是 `saving ? '保存中…' : notice`，
    // 后端早就 200、`noticeTone` 也已经是 ok，而屏幕上一动不动。并发下这能挂好几秒
    // （e2e 实测抓到过）。留下来的那句话会一直回答用户的问题（§14）。
    saving.value = false
  }
  // 列表刷新仍然排在 `return` 之前：调用方（以及 e2e）紧接着就要在列表里找这一行。
  if (savedOk) await loadReports()
  return savedOk
}

async function publishReport() {
  const report = opened.value
  if (!report || !canPublish.value || saving.value || publishing.value) return
  const no = report.current_version
  const go = await askConfirm(
    `发布 V${no}？`,
    [
      `发布之后 V${no} 的正文与统计快照都会被锁定，不能再修改。`,
      '德育领导会在只读的报告中心看到这一版，并且可以按这一版导出。',
      dirty.value ? '这四段正文里有还没保存的修改，发布时会先替你保存一次。' : '',
      '发布之后要继续修改，必须从这一版新建一个版本。'
    ].filter(Boolean).join(''),
    // 「确认发布 V1」而不是「发布 V1」：弹层背后的页面上就立着一枚同名按钮，
    // 两枚一模一样的按钮并排出现在同一屏里，读屏软件报出来的也是同一个名字。
    `确认发布 V${no}`
  )
  if (!go) return
  publishing.value = true
  try {
    if (dirty.value) {
      // 保存失败就什么都不发布——正文与 dirty 都还在原处（§5.13 Phase B「发布失败保留 dirty」）。
      const saved = await save()
      if (!saved) return
    }
    const target = opened.value
    if (!target) return
    const published = await publishProfessionalReport(target.id)
    applyOpened(published)
    notice.value = `V${no} 已发布，当前版本已锁定`
    noticeTone.value = 'ok'
    await loadReports()
  } catch (err) {
    notice.value = err instanceof Error ? err.message : '发布失败'
    noticeTone.value = 'error'
  } finally {
    publishing.value = false
  }
}

async function startNewVersion() {
  const report = opened.value
  if (!report || newVersioning.value) return
  const from = report.latest_published_version ?? report.current_version
  const go = await askConfirm(
    `基于 V${from} 新建版本？`,
    `新版本会复制 V${from} 的四段正文，并按当前最新数据重新生成统计快照。V${from} 不会被改动——在它被换下来之前，德育领导读到的仍然是那一版。`,
    '新建版本'
  )
  if (!go) return
  newVersioning.value = true
  try {
    const next = await newReportVersion(report.id)
    applyOpened(next)
    notice.value = `已创建 V${next.current_version} 草稿，可以继续编辑；V${from} 仍然对德育领导可见。`
    noticeTone.value = 'info'
    await loadReports()
  } catch (err) {
    notice.value = err instanceof Error ? err.message : '新建版本失败'
    noticeTone.value = 'error'
  } finally {
    newVersioning.value = false
  }
}

/* ── 导出 ───────────────────────────────────────────────────────────── */

function versionOptionLabel(item: ProfessionalReportVersion) {
  const parts = [`V${item.version_no}`, reportStatusLabel(item.status)]
  if (item.status === 'PUBLISHED' && item.published_at) {
    parts.push(`发布于 ${formatDateTime(item.published_at)}`)
  } else {
    parts.push(`创建于 ${formatDateTime(item.created_at)}`)
  }
  return parts.join(' · ')
}

function exportSummary(report: ProfessionalReport) {
  return [
    `导出对象：${report.report_no}「${report.title}」。`,
    `任务范围：${scopeSummary.value}。`,
    '格式：CSV，由服务端生成并纳入导出治理。',
    '隐私保护：强制开启，文件里不含学生姓名与个体答卷。',
    `文件生成后在导出中心保留 ${ttlHours.value} 小时，过期后不再可下载。`,
    '这一次导出会记下操作人、用途与所选版本。'
  ].join('')
}

async function exportReport() {
  const report = opened.value
  if (!report || exporting.value) return
  const versions = report.versions || []
  if (!versions.length) {
    notice.value = '这一份报告还没有可导出的版本'
    noticeTone.value = 'error'
    return
  }
  const fallback = report.latest_published_version ?? report.current_version
  formTitle.value = '导出专业报告'
  formDescription.value = exportSummary(report)
  formSubmitText.value = '生成并下载'
  formFields.value = [
    {
      key: 'purpose',
      label: '导出用途',
      type: 'select',
      required: true,
      // 用途取已有的系统配置（`export.purposes`），**不新增第二套常量**——
      // 两套会在某次改配置之后各说各话。
      options: purposes.value.map(item => ({ value: item, label: item })),
      defaultValue: purposes.value.includes(lastPurpose.value) ? lastPurpose.value : (purposes.value[0] || ''),
      placeholder: '请选择用途',
      hint: '用途会进审计，是这份文件离楼之后的用途说明。'
    },
    {
      key: 'version_no',
      label: '导出哪一版',
      type: 'select',
      required: true,
      options: versions.map(item => ({ value: String(item.version_no), label: versionOptionLabel(item) })),
      defaultValue: String(fallback),
      placeholder: '请选择版本',
      hint: '默认是最新已发布的那一版。仍在编辑的草稿版本也可以导出，用于校内核对。'
    }
  ]
  const values = await askForm()
  const purpose = (values.purpose || '').trim()
  // 走到这里只剩「取消」一种可能：没选用途会被 `FormDialog` 的必填校验挡在弹层里。
  if (!purpose) return
  const versionNo = Number(values.version_no)
  if (!versionNo) return
  lastPurpose.value = purpose
  exporting.value = true
  notice.value = '正在生成导出文件…'
  noticeTone.value = 'info'
  try {
    const job = await exportProfessionalReport(report.id, purpose, versionNo)
    lastExport.value = {
      at: new Date().toLocaleString('zh-CN', { hour12: false }),
      jobNo: job.job_no,
      purpose,
      version: `V${versionNo}`
    }
    notice.value = `已下载，可在导出中心查看（${job.job_no}）。`
    noticeTone.value = 'ok'
  } catch (err) {
    // 用途已经记在 `lastPurpose` 上，再点一次只需重选版本（§5.13 Phase B「失败可恢复」）。
    notice.value = err instanceof Error ? err.message : '导出失败'
    noticeTone.value = 'error'
  } finally {
    exporting.value = false
  }
}

/* ── 生命周期 ───────────────────────────────────────────────────────── */

onMounted(() => {
  loadSettings()
  loadReports()
  window.addEventListener('beforeunload', onBeforeUnload)
})
onUnmounted(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <ReportPageHeader description="在聚合统计基础上记录专业意见，并按授权范围生成报告。"/>
  <FilterBar
    :before-change="beforeChange"
    @query="onQuery"
    @reset="onReset"
    @tasks-loaded="onTasksLoaded"
  />

  <ProfessionalReportList
    :reports="reports"
    :selected-id="opened?.id ?? null"
    :task-names="taskNames"
    :loading="reportsLoading"
    :error="reportsError"
    @select="selectReport"
    @retry="loadReports"
  />

  <!--
    打开报告之后的操作条。它把四件事摆在同一处：**这是哪一份报告**（名称 / 编号 /
    任务范围 / 最近发布）、**这是哪一版**（状态药丸）、**下面那些数字是哪一版冻结的**
    （口径句），以及怎么回到实时分析。**口径要写进界面**（§9）——「这一页显示的是什么」
    不写出来，读者会以为改动上面的筛选会影响下面那张表。
  -->
  <section v-if="opened" class="card open-bar">
    <div class="open-bar-main">
      <div class="open-bar-head">
        <!-- 这枚药丸就是「当前版本号 + 当前版本状态」（`草稿 · V1` / `已发布 · V2` /
             `历史版本 · V1`），所以下面那张事实表里不再重复一遍。 -->
        <span class="pill" :class="reportStatusTone(currentVersionStatus)">{{ openStateLabel }}</span>
        <dl class="open-bar-facts">
          <div><dt>报告名称</dt><dd>{{ opened.title }}</dd></div>
          <div><dt>报告编号</dt><dd>{{ opened.report_no }}</dd></div>
          <div><dt>任务范围</dt><dd>{{ scopeSummary }}</dd></div>
          <div><dt>最近发布</dt><dd>{{ latestPublishedLabel }}</dd></div>
        </dl>
      </div>
      <span class="open-bar-text">{{ modeNote }}</span>
    </div>
    <div class="open-bar-side">
      <button
        v-if="viewingVersion !== null"
        class="btn small"
        type="button"
        @click="openVersion(opened.current_version)"
      >回到 V{{ opened.current_version }}（可编辑）</button>
      <button class="btn small" type="button" @click="closeOpenedWithGuard">关闭报告</button>
    </div>
  </section>

  <ErrorState v-if="openError" :message="openError" :on-retry="loadReports"/>
  <ErrorState v-if="analysisError" :message="analysisError" :on-retry="retryAnalysis"/>

  <div v-if="displayLoading" class="loading">加载中…</div>
  <template v-else-if="displayedReport">
    <div class="sub-tabs">
      <button class="sub-tab" :class="{active: sub==='interpretation'}" @click="sub='interpretation'">专业解读</button>
      <button class="sub-tab" :class="{active: sub==='export'}" @click="sub='export'">报表导出</button>
    </div>

    <div class="kpis">
      <KpiCard label="本次分析任务" :value="displayedReport.tasks?.length || 1" hint="当前选定任务"/>
      <KpiCard label="可评价人数" :value="displayedReport.sample_quality?.n_evaluable || 0" hint="来自实际测评结果" tone="green"/>
      <KpiCard label="统计维度" :value="displayedReport.dimensions.length" hint="当前报告口径"/>
      <KpiCard label="专业记录" :value="openStateLabel" :hint="professionalHint" tone="amber" icon="clipboard"/>
    </div>

    <template v-if="sub==='interpretation'">
      <div class="split">
        <section class="card">
          <div class="card-head">
            <h2 class="section-title">心理老师专业解读</h2>
            <span class="pill" :class="reportStatusTone(currentVersionStatus)">{{ openStateLabel }}</span>
          </div>
          <div class="notice">任务：{{ displayedReport.task.name }}；正式可评价样本为 {{ displayedReport.sample_quality?.n_evaluable }} 人。</div>
          <label v-for="(f, i) in fields" :key="f" class="field">{{ i + 1 }}. {{ f }}
            <textarea
              v-model="draft[i]"
              :readonly="readOnly"
              :placeholder="readOnly ? '当前版本只读' : '请据统计事实及专业判断填写'+f"
              rows="3"
              maxlength="1200"
              class="text-area"
              :class="{ locked: readOnly }"
            />
          </label>
          <div class="action-row">
            <button
              :class="['btn', { primary: primaryAction === 'save' }]"
              :disabled="(!opened && !canCreate) || readOnly || saving"
              :title="readOnly ? '当前版本只读；要继续修改请先新建版本' : (opened ? '' : '按当前筛选保存一份新报告')"
              @click="save"
            >{{ saving ? '保存中…' : '保存草稿' }}</button>
            <button
              class="btn"
              :disabled="!canPublish || saving || publishing"
              :title="publishDisabledReason"
              @click="publishReport"
            >{{ canPublish ? `发布 V${opened?.current_version}` : '发布' }}</button>
            <button
              v-if="canNewVersion"
              :class="['btn', { primary: primaryAction === 'newVersion' }]"
              :disabled="newVersioning"
              @click="startNewVersion"
            >基于当前版本继续编辑</button>
            <button class="btn" :disabled="!opened" title="先保存报告，才有可导出的版本" @click="sub='export'">进入导出设置</button>
          </div>
          <p class="hint" :class="noticeTone" role="status">{{ saving ? '保存中…' : notice }}</p>
          <p v-if="!opened" class="muted tiny">
            {{ canCreate
              ? '保存草稿会新建一份报告；「发布」要等草稿存下来之后才可用。'
              : '先在「我的报告」里打开一份草稿接着写，或者在上面选好测评任务之后再保存一份新的。' }}
          </p>
        </section>
        <section class="card">
          <h2 class="section-title">报告状态与说明</h2>
          <div class="notice"><strong>心理测评不是医学诊断</strong><br/>群体指标、样本覆盖及效度信息需共同解释。</div>
          <ul class="advice">
            <li>报告数字由统计结果生成，专业文字由心理老师审核。</li>
            <li>报告仅呈现满足隐私阈值的聚合统计，不展示个体答卷。</li>
            <li>已发布的版本永久保留、不可覆盖；要改内容请从最新已发布那一版新建版本。</li>
            <li>实际导出流程需服务端权限、用途、审计和下载有效期控制。</li>
          </ul>
        </section>
      </div>

      <ProfessionalReportVersions
        v-if="opened"
        :versions="opened.versions || []"
        :current-version="opened.current_version"
        :selected-version="viewingVersion"
        @select="openVersion"
      />
    </template>

    <template v-else>
      <div class="split">
        <section class="card">
          <div class="card-head">
            <h2 class="section-title">报表导出设置</h2>
            <span class="pill" :class="reportStatusTone(currentVersionStatus)">{{ openStateLabel }}</span>
          </div>
          <div class="notice">导出范围：{{ scopeSummary }}。</div>
          <p class="field">文件格式：CSV（由服务端生成并纳入导出治理）</p>
          <label class="checkbox-line"><input type="checkbox" checked disabled/> 启用隐私保护（强制）</label>
          <div class="action-row">
            <button
              class="btn primary"
              :disabled="!canExport || exporting"
              :title="canExport ? '' : '先保存报告，才有可导出的版本'"
              @click="exportReport"
            >{{ exporting ? '生成中…' : '生成报告' }}</button>
          </div>
          <p class="hint" :class="noticeTone" role="status">{{ notice }}</p>
          <p v-if="!canExport" class="muted tiny">还没有可导出的版本。先在「专业解读」里保存一份报告草稿；导出时服务端只把文件写进导出中心，界面不重复实现一套导出。</p>
          <p v-else class="muted tiny">导出时可以选导出哪一版，默认是最新已发布的那一版。历次导出记录在<RouterLink to="/counselor/exports">导出中心</RouterLink>。</p>
        </section>
        <section class="card">
          <h2 class="section-title">导出范围说明</h2>
          <table>
            <tbody>
              <tr><th>报告编号</th><td>{{ opened ? opened.report_no : '尚未保存' }}</td></tr>
              <tr><th>任务范围</th><td>{{ scopeSummary }}</td></tr>
              <tr><th>个人身份数据</th><td>不包含</td></tr>
              <tr><th>角色</th><td>心理老师</td></tr>
              <tr><th>当前文件</th><td>受控导出 CSV · 有效期 {{ ttlHours }} 小时</td></tr>
            </tbody>
          </table>
          <div class="notice">报告内容来自这份报告冻结的那一组任务；导出文件不包含个人身份与个体答卷数据。</div>
        </section>
      </div>
      <PrivacyNote/>
      <!--
        这一块叫「本次操作结果」，**不是「导出记录」**：它只有一行、来自本页这一次点击，
        而真正的历史（含撤销与下载次数）在导出中心。此前它靠 `lastExportAt` 冒充历史，
        标着「专业解释与导出记录」而表里永远最多一行——那是把一次操作说成一份台账。
      -->
      <section class="card audit-section">
        <h2 class="section-title">本次操作结果</h2>
        <div class="table-scroll">
          <table>
            <thead><tr><th>时间</th><th>操作</th><th>导出对象</th><th>用途</th><th>作业编号</th></tr></thead>
            <tbody v-if="lastExport">
              <tr>
                <td>{{ lastExport.at }}</td>
                <td>生成专业报告</td>
                <td>{{ lastExport.version }}</td>
                <td>{{ lastExport.purpose }}</td>
                <td>{{ lastExport.jobNo }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="!lastExport" class="data-empty">本次登录还没有导出过报告。</div>
        </div>
        <p class="muted tiny">
          这里是<strong>本次操作</strong>的结果，不是导出历史——历次导出记录在<RouterLink to="/counselor/exports">导出中心</RouterLink>（含撤销与下载次数）。
        </p>
      </section>
    </template>
  </template>
  <!--
    空态是一句**关于数据的话**，不是一句操作指导语（§14）；而且它**不能在一次读取失败时
    落下**——所以它排在 `analysisError` 的判断之后。
    这一支此前写着「请选择测评任务后点击查询」——而进这一页时会自动加载最新可分析
    任务（`FilterBar.loadTasks`），所以走到这里意味着**真的没有可用任务**，
    此时那个下拉框本身就选不出东西来。一句话把用户支使去做一件做不到的事，比不说更糟。
  -->
  <div v-else-if="!analysisError" class="empty">本学年还没有可用的测评任务</div>

  <ConfirmDialog
    :open="confirmOpen"
    :title="confirmTitle"
    :message="confirmMessage"
    :confirm-text="confirmConfirmText"
    :cancel-text="confirmCancelText"
    :danger="confirmDanger"
    @confirm="onConfirm"
    @cancel="onConfirmCancel"
    @update:open="onConfirmOpen"
  />
  <FormDialog
    :open="formOpen"
    :title="formTitle"
    :fields="formFields"
    :description="formDescription"
    :submit-text="formSubmitText"
    @submit="onFormSubmit"
    @cancel="onFormCancel"
    @update:open="onFormOpen"
  />
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
.loading { padding: 40px 0; text-align: center; color: var(--muted) }
.open-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; padding: 12px 16px }
/* 主区改成两行：上面一行是「这是哪一份报告」（药丸 + 四项事实），下面一行是口径句。
   原来的单行 flex 放不下四项事实——它们会被挤成一条读不出结构的横排。 */
.open-bar-main { display: grid; gap: 6px; min-width: 0; flex: 1 1 420px }
.open-bar-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0 }
.open-bar-facts { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; margin: 0; font-size: 12.5px }
.open-bar-facts > div { display: flex; align-items: baseline; gap: 5px; min-width: 0 }
.open-bar-facts dt { color: #708198; white-space: nowrap }
.open-bar-facts dd { margin: 0; color: #2c4257; font-weight: 650; overflow-wrap: anywhere }
.open-bar-text { color: #4d6580; font-size: 12.5px; line-height: 1.7 }
.open-bar-side { display: flex; gap: 8px; flex-wrap: wrap }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap }
.sub-tabs { display: flex; gap: 6px; margin-bottom: 16px; border-bottom: 2px solid #e8eef6 }
.sub-tab { padding: 9px 16px; border: 0; background: none; color: #617994; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent }
.sub-tab.active { border-bottom-color: #0876d9; color: #0876d9 }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.split { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px }
.split > * { min-width: 0 }
section.card { min-width: 0; padding: 18px; overflow: hidden }
.audit-section { margin-top: 16px }
.data-empty { padding: 22px 14px; color: var(--muted); text-align: center }
.field { display: grid; gap: 6px; font-weight: 650; margin-top: 12px; font-size: 13px }
.text-area { display: block; width: 100%; min-width: 0; max-width: 100%; min-height: 55px; resize: vertical; padding: 7px 8px; border: 1px solid #cbd8e2; border-radius: 6px; font: inherit }
.text-area.locked { background: #f5f8fc; color: #4d6580 }
.action-row { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin-top: 12px }
.checkbox-line { display: flex; gap: 7px; align-items: center; margin: 14px 0 }
.checkbox-line input { min-height: auto }
.advice { padding-left: 20px; color: #4d6580; line-height: 2 }
.hint { margin: 6px 0; color: #4d6580 }
.hint.ok { color: #1c7a45 }
.hint.error { color: #b3261e }
.tiny { font-size: 12px; line-height: 1.7; margin: 6px 0 0 }
table { width: 100%; border-collapse: collapse; font-size: var(--font-table) }
th { background: #f1f6fd; color: #3e5877; padding: 9px 8px; text-align: left; font-weight: 700 }
td { padding: 9px 8px; border-top: 1px solid #e6edf6 }
tbody tr:hover { background: #f8fbff }
@media(max-width:1200px) { .split { grid-template-columns: 1fr } }
@media(max-width:1100px) { .kpis { grid-template-columns: 1fr 1fr } }
@media(max-width:600px) {
  .kpis { grid-template-columns: 1fr }
  .sub-tabs { max-width: 100%; overflow-x: auto }
  .sub-tab { flex: 0 0 auto; white-space: nowrap }
  .action-row { align-items: stretch; flex-direction: column }
  .action-row .btn { width: 100% }
  .open-bar { align-items: flex-start; flex-direction: column }
}
</style>
