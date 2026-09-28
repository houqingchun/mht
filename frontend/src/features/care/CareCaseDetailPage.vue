<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import TrendChart from '../../components/TrendChart.vue'
import DimensionTrends from '../../components/DimensionTrends.vue'
import DimensionRadar from '../../components/DimensionRadar.vue'
import ClassComparisonPanel from '../../components/ClassComparisonPanel.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import { useSafeBack } from '../../composables/useSafeBack'
import { daysFromNow, formatDate, formatDateTime, formatDuration, today } from '../../services/dates'
import { deltaTone, latestScoreDelta, scoredPoints } from '../../services/trend'
import {
  VOIDED_SITTING_LABEL,
  VOIDED_TASK_SOURCE_NOTE,
  ageLabel,
  calculationStatusLabel,
  calculationStatusTone,
  careEventLabel,
  careEventTone,
  dimensionLabel,
  dimensionTone,
  genderLabel,
  levelLabel,
  levelTone,
  retestStatusLabel,
  signalTypeLabel,
  sourceLabel,
  statusLabel,
  statusTone,
  testedAtSourceLabel,
  validityLabel,
  validityTone
} from '../../services/labels'
import {
  getCareCaseDetail,
  createManualReview,
  createFollowUp,
  createFamilyContact,
  createRetestPlan,
  closeCareCase,
  reopenCareCase,
  exportCareCase,
  getKeyQuestionAnswers,
  getClassComparison,
  retrySessionCalculation,
  type CareCaseDetail,
  type ClassComparison
} from '../../services/api'

const route = useRoute()
const router = useRouter()

// 业务词表由系统配置提供：这些字段原本是自由文本输入，
// 同一概念会被写成不同字符串，无法统计。
const { settings } = useSettings()

function optionsOf(list: readonly string[]) {
  return list.map(item => ({ value: item, label: item }))
}
const detail = ref<CareCaseDetail | null>(null)
const loading = ref(true)
const error = ref('')
const activeTab = ref('overview')

// 档案已关闭时，四个登记动作全部不可用——服务端 `care_service._ensure_open`
// 对复核 / 跟进 / 家庭回访 / 复测这四条路一律回 409（CLAUDE.md §28：对一条 CLOSED
// 档案写记录等于隐式复活它，学生会撞上「同时只能有一条在办档案」那个唯一键）。
// 所以这里不是「界面先藏起来」，而是**把服务端那道门写在按钮上**：置灰 + 说得出为什么。
const caseClosed = computed(() => detail.value?.case_status === 'CLOSED')

// Modal state
const showForm = ref(false)
const formTitle = ref('')
const formFields = ref<FormField[]>([])
const formSubmitText = ref('提交')
let formResolve: ((values: Record<string, string>) => void) | null = null

const showConfirm = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmDanger = ref(false)
const confirmText = ref('确认')
let confirmResolve: ((value: boolean) => void) | null = null

// Controlled single-student export (the prototype's exportOne).
const showExport = ref(false)
const exportPurpose = ref('')
const exportMask = ref('masked')
const exportFields = ref('minimum')
const exportConsent = ref(false)

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

const tabs = [
  { key: 'overview', label: '测评概览' },
  // 「班级对照」排在历次趋势前面（2026-09-17）：它第一场测评当天就能用，而趋势
  // 至少要两场才画得出东西。MHT 每学期约一次，学生在校第一年的趋势页上只有
  // 孤零零一个点，而个案会上真正要回答的是「他相对于同龄人现在处在哪」。
  { key: 'comparison', label: '班级对照' },
  { key: 'follow', label: '跟进记录' },
  { key: 'family', label: '家庭回访' },
  // 「复测趋势」2026-09-17 改名「历次趋势」：MHT 是每学期约一次的普查，
  // 系统里没有「学生单独复测」这个场景，页签名跟着「复测计划」叫会误导人
  // （见 docs/phase0_rule_freeze.md §7）。页签里的**复测计划卡片保留**，
  // 学校仍然用它登记「下学期再看一次」。
  { key: 'trend', label: '历次趋势' },
  // 档案事件排在「访问审计」旁边：这两块都是**关于这份档案本身的记录**，
  // 而不是关于这名学生的临床内容。两者分开是有意的（§16.4）——审计回答
  // 「谁在什么时候调了哪个接口」，事件回答「这份档案经历了什么」，
  // 读者、保留期、权限都不同，所以是两个页签而不是一张合并的表。
  { key: 'events', label: '档案事件' },
  { key: 'auditStudent', label: '访问审计' }
]


/**
 * 最近一次有记录的作答用时，没有记录时返回空串、由模板显示占位。
 *
 * 单独取「最后一次」而不是像总分那样画趋势：用时是作答质量的旁证，心理老师要看的是
 * 「这次是不是明显过快」；历次用时的对比在历次趋势的图表里，不在这里重复。
 * 迁移前的会话 duration_seconds 为 NULL，会被过滤掉。
 */
const latestDuration = computed(() => {
  const recorded = (detail.value?.history || []).filter(h => h.duration_seconds !== null)
  if (!recorded.length) return null
  return recorded[recorded.length - 1].duration_seconds
})

/**
 * 历次场次里有几场所属的筛查任务已经作废（V2.0.0 §4.15 / §4.6）。
 *
 * 与「学生测评记录」页那一处同一个数、同一个理由：**作废的场次照旧留在 `history` 里**
 * （`care_service` 那一段刻意的「不加 `effective_session_predicate()`」，CLAUDE.md §27），
 * 这里只决定要不要把「它不参与当前判断」这句话说出来。零时整句不出现——没有作废场次
 * 却说「上面有 0 场已作废」，那是关于数据的一句错话（§14）。
 */
const voidedCount = computed(() => (detail.value?.history || []).filter(h => h.task_voided).length)

/* ---- 历次趋势页签的三块数据（2026-09-27，§5.15.9 UX-FINAL-02）------------------------
 *
 * 这一页原本只画一张折线，「他现在比上次好还是差」——**趋势页最该回答的那个问题**——
 * 要读者自己从两三个点上看出来。这里把三个数一次算好交给模板：
 *
 * - `scoredHistory`：有分数的那些场。判据在 `services/trend.ts`，与 `TrendChart`
 *   **同一处定义**：否则图上最后一个点与下面那个「最近一次」会在「有会话但没有结果行」
 *   的场次上分岔，而两个数印在同一张卡上（CLAUDE.md §11 那一族）。
 * - `latestSitting`：最近一次那一个点，没有就整格不显示。
 * - `latestDelta`：最近一次减上一次。**`null` 不是 `0`**——只测过一次时是「比不了」，
 *   不是「持平」（§11 那条 `rate_or_none` 的同一条）。
 *
 * 三个都是 `computed`，换学生 / 换档案时跟着 `detail` 重算，不留上一个人的数
 * （§14：一次失败的读取不许留下上一次的答案）。
 */
const scoredHistory = computed(() => scoredPoints(detail.value?.history || []))
const latestSitting = computed(() => scoredHistory.value[scoredHistory.value.length - 1] ?? null)
const latestDelta = computed(() => latestScoreDelta(scoredHistory.value))

/* ---- 个案摘要头（2026-09-27，§5.15.3 UX-FINAL-02）-----------------------------------
 *
 * 这一页有七个页签，而「这个学生现在什么状态」原本要翻进「测评概览」逐行读。摘要头
 * 把七项并成一条，全是**读**的东西——**一个字段都不新取**：下面每一格的数据都来自
 * `get_care_case` 已经在发的响应（其中 `owner_name` 与 `risk_events[].signal_type`
 * 是本次补下发的两个键，理由见 `api.ts` 里那两条 JSDoc）。
 *
 * **刻意不给 `tier-primary`**（§5.15.2）：这一页的主工作区是下面那条「下一步操作」
 * ——老师是来这儿干活的，摘要只是他动手前要看的那一眼。一页最多一个主工作区，给了
 * 摘要就会与那条 sticky 的动作条抢同一层，而全页都重 = 全页都不重。
 * 走 `tier-supporting`：它是「读的、次要的、不占工作时间的信息」那一档。
 *
 * **也不加 `role="status"` / `aria-live`**：`e2e/app.spec.ts` 的 `pageNotice` 取
 * `p[role="status"]` 定位页级提示，而这一条是常驻内容、不是提示——挂一个 live region
 * 上去会让读屏软件每次进这一页都把十格重念一遍。
 * ---------------------------------------------------------------------------------- */

/** 年级 + 班级。两段都可能为空，所以在脚本里拼而不是在模板里拼——模板里一个空值就会
 *  留下一个多余的空格，渲染出「 1班」这种看不见的脏字符。 */
const summaryClassName = computed(() => {
  const student = detail.value?.student
  if (!student) return '—'
  return [student.grade, student.class_name].filter(Boolean).join(' ') || '—'
})

/**
 * 关注来源：这份关注是从哪来的。
 *
 * 取 `risk_events[].signal_type`（三档），**不取 `risk_type`**：后者是六档的规则码，
 * 界面上从来没有它的词条，而「六 → 三」那张对照表住在 `scale_engine` 里
 * （`SIGNAL_TYPE_BY_RISK_TYPE`）——在 `labels.ts` 里再抄一份就是第二张映射表，两张
 * 必漂而漂了不会有任何东西报错（§3「唯一映射层」）。所以由服务端映射好再下发。
 *
 * 去重、保留服务端给的次序（`id.desc()`，最新在前）：同一名学生复测过两轮时，两轮
 * 可能是同一种信号，逐条印出来只会把这一格撑长。**去重只去重复的种类**——一份档案
 * 同时有「普通筛查信号」与「重点题人工复核」时两个都要说出来，只留最新那一条会让
 * 另一条看起来不曾发生过。
 */
const summarySignals = computed(() => {
  const seen: string[] = []
  for (const event of detail.value?.risk_events || []) {
    if (event.signal_type && !seen.includes(event.signal_type)) seen.push(event.signal_type)
  }
  return seen
})

/**
 * 主要关注维度：本次结果里落在 `HIGH` 的那几项。
 *
 * 判据是 `dimension.level === 'HIGH'`（`dimension_bands` 的第三档）。**这一格不印
 * 任何等级词**：`levelLabel` / `levelTone` 映射的是 `total_level`（关注等级：重点关注
 * / 需要关注 / 一般观察），与维度分档是**两个轴**——印上去会与旁边那格「当前关注等级」
 * 读成同一件事（`SCORE_DISTRIBUTION_LABELS` 那段注释记着同一个坑）。
 *
 * 只印维度分、不印 `score / max_score`：`max_score` 是这一维的题量、各维不同，它是给
 * 图表的百分比用的（`dimensionPercent`），两个数并排在这一格里反而要人去算。
 */
const summaryHighDimensions = computed(() => (detail.value?.dimensions || []).filter(d => d.level === 'HIGH'))

/** 「主要关注维度」那一格最多列几项。理由见 `summaryDimensionText`。 */
const SUMMARY_DIMENSION_LIMIT = 3

/**
 * 「主要关注维度」那一格的**值**，在这里拼而不是在模板里拼。
 *
 * **为什么只列前三项**（2026-09-27 实测后改，第一版列全）：这是十条里唯一长度不可控的
 * 一格——一名六个维度都落在 `HIGH` 的学生会把它撑成四行，而同一行其它格子只有四个字，
 * 于是**整条摘要的高度由最次要的那一格决定**，行内其余九格上下各空一大片。摘要头是
 * 「扫一眼」的东西，不是维度明细；明细在下面「八维度结果」那张雷达图上，每一项都在。
 *
 * **取前三而不按分数排序**：维度分之间不可比——各维题量不同（`max_score` 有的是 10、
 * 有的是 15），按原始分排会让题量大的维度永远占前三，那不是「主要」。这里保持服务端
 * 给的次序，也就是量表规则里那八个维度自己的次序（MHT 的标准次序），它是稳定的、有
 * 意义的，不是随机的。
 *
 * 折起来的那些**要说出来**（`等 N 项`）：只印三项而不说还有几项，读起来就是「他就这
 * 三项高」，而屏幕上正少着三项没说（§10：凡是截断，都要自己说出来）。
 */
const summaryDimensionText = computed(() => {
  const high = summaryHighDimensions.value
  if (!high.length) return '本场无'
  const head = high.slice(0, SUMMARY_DIMENSION_LIMIT)
  const text = head.map(d => dimensionLabel(d.dimension_code) + ' ' + d.score).join('、')
  return high.length > SUMMARY_DIMENSION_LIMIT ? `${text} 等 ${high.length} 项` : text
})

/** 有结果行才有维度可谈。「一场都没有维度」与「这一场一个维度都不高」是两句不同的话
 *  （§14），所以模板里分两支，不共用一句「无」。 */
const summaryHasDimensions = computed(() => (detail.value?.dimensions || []).length > 0)

/**
 * 最近一次跟进 / 下一次跟进。
 *
 * 两个都是**列表页已有的推导**，这里逐字镜像（`care_service.list_care_cases` 里
 * `next_follow_up` 那一段）：最近一次 = `follow_ups[0]`（服务端按 `id.desc()` 发，
 * 最新在前）；下一次 = 全部 `status === 'ACTIVE'` 的跟进记录里 `next_follow_up_date`
 * **最大**的那一个。
 *
 * **不在这里写第二份「今天算不算逾期」的比较**：那是 `services/careQueue.ts` 的
 * `isDueToday` 的活，而「逾期」在列表页是一枚药丸、在队列里是一个页签，两处必须
 * 同一个判据（那个模块的注释里写着同一句）。摘要头只说日期——这一格要回答的是
 * 「什么时候」，不是「还赶不赶得上」。
 *
 * 日期是 `YYYY-MM-DD`，字典序与时间序一致，所以取最大直接用 `>` 比字符串，与服务端
 * 那条 `ORDER BY … DESC` 排的是同一个字段。
 */
const summaryLastFollowUp = computed(() => detail.value?.follow_ups[0]?.created_at ?? null)

const summaryNextFollowUp = computed(() => {
  const dates = (detail.value?.follow_ups || [])
    .filter(record => record.status === 'ACTIVE' && record.next_follow_up_date)
    .map(record => record.next_follow_up_date)
  if (!dates.length) return null
  return dates.reduce((latest, current) => (current > latest ? current : latest))
})

/**
 * 最近一次测评的日期。`tested_at` 优先、历史行回落 `submitted_at`——与下面「本次测评
 * 事实」那一卡的「测评日期」是**同一句口径**，两处必须取同一个值：一处取 `tested_at`、
 * 另一处取 `submitted_at` 的话，同一屏会印出两个日期，而历史行的这两个字段都是 null
 * （0013 刻意没有回填真实测评日，CLAUDE.md §21）。
 */
const summaryLastTestedAt = computed(
  () => detail.value?.assessment.tested_at || detail.value?.assessment.submitted_at || null
)

async function load() {
  loading.value = true
  error.value = ''
  // 先清空再取（2026-09-17 补）。`load()` 在每一次写入之后都跑一遍（复核、跟进、
  // 家庭回访、复测、关闭、重开），所以「取失败」这一支是够得着的：一次网络抖动就会
  // 让红条与**改动之前的**那份档案同屏——刚关掉的档案还写着「在办」，
  // 用户没有任何办法看出这一屏是旧的。
  detail.value = null
  try {
    const studentId = Number(route.params.studentId)
    detail.value = await getCareCaseDetail(studentId)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/**
 * 重算这一场的评分 —— 「评分状态：计算失败」那一行下面的按钮。
 *
 * 三个细节都有理由：
 *
 * * **先 `await load()` 再报结果**。这一页上「评分状态」「筛查分类」「规则版本」
 *   都是**这一场**的值，重算之后它们会一起变；只弹一句 toast 而不重取，屏幕上就
 *   留着「计算失败」和一个已经算出来的结果，两句话各说各的。
 * * **`recalculated === false` 不是失败**：它说的是「已经有结果了，这一次什么都没写」
 *   （两个人同时点了，或者页面停在旧状态上）。照实说，别报成一次成功。
 * * 重算是一次**敏感读取**，后端每一次都写审计（读满整份答卷）——所以界面上的措辞
 *   不承诺「什么都没有记录」。
 */
const retrying = ref(false)

async function retryCalculation() {
  const sessionId = detail.value?.assessment.session_id
  if (!sessionId || retrying.value) return
  retrying.value = true
  try {
    const outcome = await retrySessionCalculation(sessionId)
    if (outcome.recalculated) {
      showToast('success', '已重新计算，评分结果已更新')
    } else {
      showToast('info', `这一次没有重算：这份答卷当前是「${calculationStatusLabel(outcome.calculation_status)}」`)
    }
    await load()
  } catch (err) {
    // 失败原因由后端的统一封装给出（统一封装里那句 error.message 本来就是写给用户看的）。
    showToast('error', err instanceof Error ? err.message : '重算失败')
  } finally {
    retrying.value = false
  }
}

/**
 * 班级对照。与档案详情**分开取**：它多算两条同班/同年级的聚合，晚到一会儿不该
 * 拖住整页，而且它每次读取都会单独写一条审计——合并进详情接口的话，
 * 「看了一次对照」与「打开了一次档案」在轨迹里就分不出来了。
 */
const comparison = ref<ClassComparison | null>(null)
const comparisonFailed = ref(false)

async function loadComparison() {
  comparisonFailed.value = false
  // 同 `load()`：拿不到就留空，不留下**上一次**那一份——对照表里是别人的班级，
  // 它看起来像这一页的一部分，比空白更糟。
  comparison.value = null
  try {
    comparison.value = await getClassComparison(Number(route.params.studentId))
  } catch {
    // 对照表是补充信息：它拿不到不该让整页变成错误状态（与维度分布同一条处理）。
    comparisonFailed.value = true
  }
}

/**
 * 「返回列表」。**不是裸 `router.back()`**：这一页的地址可以收藏、可以分享，
 * 直接打开时没有站内上一页，裸 back 会把用户带出应用（白屏）。
 * 兜底路径是重点学生列表——这一页的每一个入口都在那里（工作台四张指标卡深链到它）。
 * 判据与「为什么不用 `history.length`」见 `composables/useSafeBack.ts`。
 */
const goBack = useSafeBack('/counselor/cases')

/** 去这个学生的「测评记录」页——那条路不需要他有档案，所以它永远通。 */
function openStudentRecords() {
  if (!detail.value) return
  router.push(`/counselor/students/${detail.value.student.id}/records`)
}

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
}

async function addFollowUp() {
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
}

async function addFamilyContact() {
  if (!detail.value) return

  const values = await showFormDialog('新增家庭回访', [
    { key: 'contact_date', label: '联系日期', type: 'date', required: true, defaultValue: today() },
    { key: 'contact_person', label: '联系对象', type: 'text', required: true, placeholder: '如：母亲' },
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
  await load()
}

async function addRetest() {
  if (!detail.value) return

  const values = await showFormDialog('安排复测', [
    { key: 'planned_date', label: '复测日期', type: 'date', required: true, defaultValue: daysFromNow(settings.value.cadence.retest_days) },
    { key: 'reason', label: '复测原因', type: 'select', required: true, placeholder: '请选择复测原因', options: optionsOf(settings.value.care.retest_reasons) }
  ], '保存计划')

  if (!values.reason) return

  await createRetestPlan(detail.value.case_id, { planned_date: values.planned_date, reason: values.reason })
  showToast('success', '复测计划已保存')
  await load()
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
    { key: 'close_reason', label: '关闭原因', type: 'select', required: true, placeholder: '请选择关闭原因', options: optionsOf(settings.value.care.close_reasons) },
    { key: 'close_note', label: '关闭说明', type: 'textarea', required: true, placeholder: '请输入关闭说明' }
  ], '确认关闭')

  if (!values.close_note) return

  try {
    await closeCareCase(detail.value.case_id, {
      close_reason: values.close_reason,
      close_note: values.close_note,
      confirm_follow_up_checked: true,
      // 乐观锁（§16.4）：把这个页面上读到的版本带回去。别人在这中间改过
      // （复核、跟进、转派都会 +1）时服务端回 409 与一句人话，而不是改掉
      // 一个已经过时的状态。
      case_version: detail.value.case_version
    })
  } catch (err) {
    // 409 那句原文本来就是写给用户看的（§2：「服务端答了话」的那一支）。
    showToast('error', err instanceof Error ? err.message : '关闭失败')
    // 版本对不上时要重取一次：页面上那个版本号已经过期，不刷新的话
    // 用户再点一次还是同一句话。
    await load()
    return
  }
  showToast('success', '关注档案已关闭，历史记录保留')
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
    // 这里最可能的 409 不是版本冲突，而是「这名学生已经有一条在办档案」——
    // 秋季关档、春季再开是学校每年都会遇到的时序（§1），此时点开这条旧的会被
    // 明确拒绝，服务端那句话里带着现在那条在办档案的编号。原样转达给用户。
    showToast('error', err instanceof Error ? err.message : '重新打开失败')
    await load()
    return
  }
  showToast('success', '关注档案已重新打开')
  await load()
}

function exportSummary() {
  exportPurpose.value = ''
  exportConsent.value = false
  exportMask.value = 'masked'
  exportFields.value = 'minimum'
  showExport.value = true
}

async function confirmExport() {
  if (!detail.value) return
  if (!exportPurpose.value) {
    showToast('error', '请选择导出用途')
    return
  }
  if (!exportConsent.value) {
    showToast('error', '请确认导出责任')
    return
  }
  try {
    await exportCareCase(detail.value.student.id, {
      purpose: exportPurpose.value,
      maskNames: exportMask.value === 'masked',
      includeScore: exportFields.value === 'score'
    })
    showExport.value = false
    showToast('success', '受控导出已完成并记录审计')
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  }
}

// --- 重点题二次查看：先填写查看原因并写审计，再展示题目回答 ---
const showKeyReason = ref(false)
const showKeyAnswers = ref(false)
const keyReason = ref('')
const keyAnswers = ref<Array<{ question_no: number; answer: string }>>([])
const keyAnswersLoading = ref(false)

function viewKeyQuestions() {
  keyReason.value = ''
  showKeyReason.value = true
}

async function confirmKeyReason() {
  if (!detail.value) return
  if (!keyReason.value) {
    showToast('error', '请选择查看原因')
    return
  }
  keyAnswersLoading.value = true
  try {
    keyAnswers.value = await getKeyQuestionAnswers(detail.value.student.id, keyReason.value)
    showKeyReason.value = false
    showKeyAnswers.value = true
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '读取重点题失败')
  } finally {
    keyAnswersLoading.value = false
  }
}

function goReviewFromKeyAnswers() {
  showKeyAnswers.value = false
  saveReview()
}

onMounted(load)
onMounted(loadComparison)
</script>

<template>
  <div>
    <div class="page-head" v-if="detail">
      <div>
        <div class="eyebrow">学生持续关注档案</div>
        <h1>{{ detail.student.name }} · {{ detail.student.grade }} {{ detail.student.class_name }}</h1>
        <p class="page-desc">
          {{ genderLabel(detail.student.gender) }} · {{ ageLabel(detail.student.age) }} ·
          原始答案、量表结果、学校关注事件和人工工作记录均独立保存。
        </p>
      </div>
      <div class="actions">
        <button class="btn" @click="goBack">返回列表</button>
        <!-- 「测评记录」与档案页并列：这一页读的是**这份档案**（复核、跟进、复测），
             那一页读的是**这个学生的测评事实**（历次场次、维度分、班级对照）。
             两名老师在同一名学生上并行工作时，两条路都得走得通。 -->
        <button class="btn" @click="openStudentRecords">测评记录</button>
        <button class="btn" @click="exportSummary">受控导出摘要</button>
        <button
          v-if="detail.case_status !== 'CLOSED'"
          class="btn danger"
          @click="closeCase"
        >
          关闭关注档案
        </button>
        <button
          v-else
          class="btn primary"
          @click="reopenCase"
        >
          重新打开档案
        </button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <template v-if="detail && !loading">
      <!-- 个案摘要头（2026-09-27，§5.15.3 UX-FINAL-02）。

           十格并成一条，回答的是老师进这一页时的第一句话：「这个学生现在什么状态」。
           它排在页签**之上**——页签回答「你要看哪一类记录」，而摘要回答「他怎么了」，
           后者是前者的问题。七项里五项（负责人 / 最近测评 / 最近跟进 / 下次跟进 /
           关注来源）此前**在首屏一个字都没有**，要翻页签才找得到。

           走 `tier-supporting`（§5.15.2）：这一页的主工作区是下面那条 sticky 的
           「下一步操作」，摘要是读的东西，不占工作时间。**不加 `tier-primary`**——
           一页最多一个，而老师是来干活的、不是来看摘要的。

           **十格排成两行，一行五格**（列数由 `.case-summary` 的 `repeat(5, …)` 定死）：
           第一行是「他是谁、现在怎么样、谁在管」（年级班级 / 学号 / 当前关注等级 /
           当前阶段 / 负责人），第二行是「关注从哪来、时间线到哪了」（关注来源 /
           主要关注维度 / 最近测评 / 最近跟进 / 下次跟进）。**列数为什么不交给
           `auto-fit`**（2026-09-27 实测后改）：十格在 `auto-fit` 下按可用宽度自己折，
           1440px 下折成 6+4、640px 下折成 3+3+3+1，两种都会在最后一行右侧空出两三格
           ——那是「表格没填满」的长相，比任何一句文案都更像页面坏了。5 与 10 整除，
           两行都填满。

           每一格都**不条件渲染**：缺值出 `—`（或「未分配」），不留空框。一处空白
           与「这里没有这一项」在屏幕上长得一样，而前者读起来像页面坏了（§14）。 -->
      <div class="card tier-supporting case-summary">
        <div class="case-summary-cell">
          <span class="case-summary-label">年级班级</span>
          <span class="case-summary-value">{{ summaryClassName }}</span>
        </div>
        <div class="case-summary-cell">
          <!-- 学号单独一格：页头的标题只有姓名，而「同名的两名学生」在这套系统里是
               明写成立的一种情况（导入按学号判重、按性别与年龄消歧，§18.4）。 -->
          <span class="case-summary-label">学号</span>
          <span class="case-summary-value">{{ detail.student.student_no || '—' }}</span>
        </div>
        <div class="case-summary-cell">
          <span class="case-summary-label">当前关注等级</span>
          <span :class="['pill', levelTone(detail.assessment.total_level)]">{{ levelLabel(detail.assessment.total_level) }}</span>
        </div>
        <div class="case-summary-cell">
          <span class="case-summary-label">当前阶段</span>
          <span :class="['pill', statusTone(detail.case_status)]">{{ statusLabel(detail.case_status) }}</span>
        </div>
        <div class="case-summary-cell">
          <!-- 「未分配」而不是 `—`：它是这份档案**真实的**一种状态（关怀队列里就有
               一个「未分配」页签），不是缺值。 -->
          <span class="case-summary-label">负责人</span>
          <span class="case-summary-value">{{ detail.owner_name || '未分配' }}</span>
        </div>
        <div class="case-summary-cell">
          <span class="case-summary-label">关注来源</span>
          <span class="case-summary-value">{{ summarySignals.length ? summarySignals.map(signalTypeLabel).join('、') : '—' }}</span>
        </div>
        <div class="case-summary-cell">
          <span class="case-summary-label">主要关注维度</span>
          <!-- 两支分岔不是罗嗦：没有维度行（还没测 / 没算出来）与「这一场一个维度都不高」
               是两句不同的话，而共用一句「无」会让前一种读成后一种——一个还没测评的
               学生看起来像「测过了，没事」（§11 那条「还没测」与「测了没事」不是一回事）。
               值由 `summaryDimensionText` 拼（只列前三项 + 「等 N 项」，理由见那里）。 -->
          <span class="case-summary-value">
            <template v-if="!summaryHasDimensions">—</template>
            <template v-else>{{ summaryDimensionText }}</template>
          </span>
        </div>
        <!-- 三格用两个不同的函数，这不是随手写的：`最近测评` 与 `下次跟进` 是**日期**
             粒度（前者混着导入那一场恒 `00:00` 的时间部分、后者本来就是 `Date` 列），
             而 `最近跟进` 是系统记下的一个**真实时刻**（`follow_ups[0].created_at`）。
             判据是「写入方给了几分信息」（`dates.ts` 那张表）。 -->
        <div class="case-summary-cell">
          <span class="case-summary-label">最近测评</span>
          <span class="case-summary-value">{{ formatDate(summaryLastTestedAt) }}</span>
        </div>
        <div class="case-summary-cell">
          <span class="case-summary-label">最近跟进</span>
          <span class="case-summary-value">{{ formatDateTime(summaryLastFollowUp) }}</span>
        </div>
        <div class="case-summary-cell">
          <span class="case-summary-label">下次跟进</span>
          <span class="case-summary-value">{{ formatDate(summaryNextFollowUp) }}</span>
        </div>
      </div>

      <div class="tabs">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <!-- 下一步操作区（V2.0.0 §5.14.3 第 6 条）。
           这一页有七个页签，而登记复核 / 跟进 / 回访 / 复测这四个动作此前各住在自己
           那个页签里——老师得先想到「这件事属于哪个页签」才找得到入口，而他在个案会上
           要回答的是「这个学生下一步做什么」。现在四个入口集中在这一条里，并且**贴着
           顶栏停住**（`position: sticky`，样式见 `styles.css` 的 `.case-next-actions`），
           切到任何一个页签都看得见。
           **「关闭关注档案」不在这一条里**，它在页头（`.btn danger` + 危险操作确认）：
           那是一个终止性动作，与日常登记摆在一起早晚会被误点。 -->
      <div class="case-next-actions">
        <div class="case-next-head">
          <b>下一步操作</b>
          <span class="muted tiny">
            登记的内容会追加在对应页签里，已有的复核、跟进与回访记录不会被覆盖。
          </span>
        </div>
        <div
          class="toolbar"
          role="group"
          aria-label="下一步操作"
          :aria-describedby="caseClosed ? 'case-next-closed-note' : undefined"
        >
          <button class="btn" :disabled="caseClosed" @click="saveReview">人工复核</button>
          <button class="btn" :disabled="caseClosed" @click="addFollowUp">记录跟进</button>
          <button class="btn" :disabled="caseClosed" @click="addFamilyContact">家庭回访</button>
          <button class="btn" :disabled="caseClosed" @click="addRetest">安排复测</button>
        </div>
        <!-- 灰掉的按钮必须说得出为什么（CLAUDE.md §27 那条），所以这一段不是 tooltip：
           置灰时它**就显示在按钮下面**，读屏软件也能读到（那一组按钮上的
           `aria-describedby` 指着这个 id）。 -->
        <p
          v-if="caseClosed"
          id="case-next-closed-note"
          class="muted tiny case-next-closed"
        >
          这份档案已经关闭，四项登记都不能新增。要接着记录，先用页头的「重新打开档案」把它打开；
          历史记录不会被删除。
        </p>
      </div>

      <!-- 测评概览 -->
      <div v-if="activeTab === 'overview'" class="grid two" style="margin-top: 17px">
        <div class="card pad">
          <h2>本次测评事实</h2>
          <div class="detail-grid" style="margin-top:13px">
            <!-- 来源放在最前面：这份记录是不是本系统里测的，决定了后面每一个数字该怎么读
                 （外部平台用另一套题目措辞、在另一天施测）。系统内作答不渲染这一行——
                 那是绝大多数情况，一行「系统内作答」只会变成噪音。 -->
            <div v-if="detail.assessment.source === 'IMPORTED'" class="detail-row">
              <span>来源</span>
              <span class="pill">{{ sourceLabel(detail.assessment.source) }}</span>
            </div>
            <!-- 这里原本还有一行「MHT总分」，显示的却是 total_level（分类而非分数），
                 与下一行的「筛查分类」是同一个值、且标签是错的，所以删掉了那一行。

                 `assessment.total_score` 今天**是**下发的（2026-09-20 随「学生测评记录」
                 页补上——那一页的页头要写「总分 24」，正文里有一张逐场的表）。
                 这张概览卡仍然不显示它，是有意的取舍而不是接口不给：它回答的是
                 「他属于哪一档」，分数要挨着日期与来源读才读得出是哪一场的。 -->
            <div class="detail-row">
              <span>筛查分类</span>
              <span :class="['pill', levelTone(detail.assessment.total_level)]">{{
                levelLabel(detail.assessment.total_level)
              }}</span>
            </div>
            <div class="detail-row">
              <span>效度状态</span>
              <span :class="['pill', validityTone(detail.assessment.validity_status)]">{{
                validityLabel(detail.assessment.validity_status)
              }}</span>
            </div>
            <!-- 缺值就是缺值。这里原来兜底成 'MHT-1.0.0'，等于给一条没有规则版本的
                 结果编了一个版本号——而规则版本正是用来回答"这条结果当时按什么标准判的"，
                 编出来的答案比留空糟得多。相邻几行用的都是 '—'。 -->
            <div class="detail-row"><span>规则版本</span><b>{{ detail.assessment.rule_version || '—' }}</b></div>
            <div class="detail-row">
              <span>当前阶段</span>
              <span :class="['pill', statusTone(detail.case_status)]">{{ statusLabel(detail.case_status) }}</span>
            </div>
            <!-- 「提交时间」原本在这一行。它对外部导入的那一场是**错的**：那一场没有人在
                 本系统里提交过任何东西，`submitted_at` 存的是文件里的测评日期，而这一页
                 上面另有一行「来源」写着「外部导入」——同一个日期在两种读法下不叫一个名字。
                 所以改叫「测评日期」（在线作答时它就是交卷那一刻，两者本来相同），
                 并在下一行说明这个日期**是谁给的**。历史行两个字段都是 null：0013 刻意没有
                 回填真实测评日（CLAUDE.md §21），所以那时按 `submitted_at` 兜底，
                 日期来源照实说「待核实」。

                 取值与格式都走 `summaryLastTestedAt` + `formatDate()`（2026-09-27，
                 §5.15.3）。此前这里是 `tested_at || submitted_at` 的**内联副本**、印的是
                 原始 ISO 串——于是同一屏上摘要头写「09-20 13:24」、这一行写
                 「2026-09-20T13:24:00」，同一个日期两个长相。取值与格式各归一处之后，
                 两处**构造上不可能漂**。

                 按**日期**渲染（2026-09-28，§5.28）：这一列混着两个来源，外部导入那一场
                 存的是 `datetime(年,月,日)`、时间恒 `00:00`，弹出时刻等于替它编一个没发生
                 过的事件。这与上面那格「最近测评」是同一处口径、同一个函数。 -->
            <div class="detail-row">
              <span>测评日期</span>
              <b>{{ formatDate(summaryLastTestedAt) }}</b>
            </div>
            <div class="detail-row">
              <span>日期来源</span>
              <span class="pill">{{ testedAtSourceLabel(detail.assessment.tested_at_source) }}</span>
            </div>
            <!-- 评分状态不条件渲染：它对**每一场**都成立，而它与此前那几行的区别正是
                 「算出来了没有」与「算出来是什么」。评分没成功时上面三行（筛查分类 /
                 效度状态 / 规则版本）都会显示「未测评 / —」，那与「这个学生还没测」
                 长得一模一样——这一行是那两者之间唯一的字，也是下面那个按钮的判据。 -->
            <div class="detail-row">
              <span>评分状态</span>
              <span :class="['pill', calculationStatusTone(detail.assessment.calculation_status)]">{{
                calculationStatusLabel(detail.assessment.calculation_status)
              }}</span>
            </div>
            <!-- 首次作答 → 交卷的墙钟时长。明显偏快是复核时要追的信号，
                 但它说明的是作答过程，不是任何结论。 -->
            <div class="detail-row"><span>作答用时</span><b>{{ formatDuration(detail.assessment.duration_seconds) }}</b></div>
          </div>
          <!-- 评分没成功：把这件事说成一次**需要人动手**的状态，而不是一句故障提示。
               答卷与交卷时间都原样留着（四层事实模型：人工复核不得修改原始答卷，
               重算改的也只是结果那一层），所以那个动作是安全的，说清楚这一点，
               心理老师才敢点。 -->
          <div v-if="detail.assessment.calculation_status === 'CALCULATION_FAILED'" class="notice danger" style="margin-top:14px">
            <div>这份答卷已经收到并保存，但评分没有算出来，所以上面几项是空的。</div>
            <div style="margin-top:6px">重新计算不会改动学生的任何一题答案。</div>
            <div v-if="detail.assessment.calculation_error" class="muted-text" style="margin-top:6px">
              失败原因（供排查）：{{ detail.assessment.calculation_error }}
            </div>
            <!-- 按钮上的词与写进审计动作码的那个词**共用一个「重算」**
                 （服务端写的是「重算测评评分」）。审计页的搜索匹配的是动作码，
                 所以操作员照着按钮去找的时候，得搜得到——缺口 7 那一族（「把看不懂
                 换成了搜不到」）栽过三次，这里是第四次的位置，提前对齐。 -->
            <div class="toolbar" style="margin-top:12px">
              <button class="btn primary" :disabled="retrying" @click="retryCalculation">
                {{ retrying ? '正在重算评分…' : '重算评分' }}
              </button>
            </div>
          </div>
        </div>
        <div class="card pad">
          <h2>八维度结果</h2>
          <!-- 雷达图取代了原来的百分比条：八条并排的条看不出维度之间的**形状**
               （哪个维度相对突出），而那正是「八维度」想要一眼看到的东西。
               轴标签保留「维度名 + 得分/题数」——归一化是为了可读，原始分不能被抹掉。
               归一化与配色的口径写在 `DimensionRadar` 里，与原来的条形图共用
               `dimensionPercent` / `dimensionTone`，两处不会各说各话。 -->
          <div style="margin-top:13px">
            <DimensionRadar :dimensions="detail.dimensions" />
          </div>
        </div>
        <div class="card pad" style="grid-column: 1 / -1">
          <h2>人工复核提醒</h2>
          <div class="notice danger" style="margin-top:14px">
            命中学校重点关注规则，需由授权心理老师人工复核。提示不等同于诊断结论。
          </div>
          <!-- 这里此前还有一枚「记录人工复核」（2026-09-27 移到页签上方那条
               「下一步操作」里）。同一页上两枚同名的按钮不只是重复：`getByRole
               ('button', { name: '…' })` 会一次命中两个，e2e 的严格模式直接红
               （CLAUDE.md §测试注意那条「一条会无故变红的守卫很快会被人关掉」）。
               留在这一页上的只有「二次查看重点题」——它是**读**，不属于那四项登记。 -->
          <div class="actions" style="margin-top:14px">
            <button class="btn" @click="viewKeyQuestions">二次查看重点题</button>
          </div>
        </div>
      </div>

      <!-- 跟进记录 -->
      <div v-if="activeTab === 'follow'" class="card pad" style="margin-top: 17px">
        <!-- 这块标题旁此前是「新增跟进」，2026-09-27 移到页签上方那条「下一步操作」里
             （同一页两枚同名的按钮会让 `getByRole(...)` 撞上重复匹配）。这一页现在
             只管把已经登记过的记录列出来。 -->
        <h2>连续跟进时间线</h2>
        <div class="timeline" style="margin-top:21px">
          <div v-for="record in detail.follow_ups" :key="record.id" class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="muted tiny">{{ formatDateTime(record.created_at) }} · {{ record.record_type }}</div>
            <div class="timeline-title">{{ record.confirmed_facts }}</div>
            <div class="timeline-text">下次跟进：{{ formatDate(record.next_follow_up_date) }}</div>
          </div>
          <div v-if="!detail.follow_ups.length" class="empty">暂无跟进记录</div>
        </div>
      </div>

      <!-- 家庭回访 -->
      <div v-if="activeTab === 'family'" style="margin-top: 17px">
        <div class="card">
          <!-- 「新增回访」2026-09-27 移到页签上方那条「下一步操作」里（理由同「跟进记录」
               那一处）。`card-head` 保留：它的内边距与标题样式归它管。 -->
          <div class="card-head">
            <h2>家庭回访记录</h2>
          </div>
          <div class="card-body">
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>联系对象</th>
                    <th>方式</th>
                    <th>结果</th>
                    <th>家庭支持</th>
                    <th>下次联系</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="record in detail.family_contacts" :key="record.id">
                    <td>{{ formatDate(record.contact_date) }}</td>
                    <td>{{ record.contact_person }}</td>
                    <td>{{ record.channel }}</td>
                    <td>{{ record.result }}</td>
                    <td>{{ record.support_status }}</td>
                    <td>{{ formatDate(record.next_contact_date) }}</td>
                  </tr>
                  <tr v-if="!detail.family_contacts.length">
                    <td colspan="6"><div class="empty">暂无家庭回访记录</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="notice warn" style="margin-top:15px">
          家庭回访仅记录已确认事实、配合情况和约定事项，避免写入无关家庭隐私。
        </div>
      </div>

      <!-- 班级对照。面板本身在 components/ClassComparisonPanel.vue——「学生测评记录」
           页也要这一块（未建档的学生照样能看对照），口径只有那一份定义。 -->
      <div v-if="activeTab === 'comparison'" style="margin-top: 17px">
        <ClassComparisonPanel :comparison="comparison" :failed="comparisonFailed" />
      </div>

      <!-- 历次趋势 -->
      <div v-if="activeTab === 'trend'" style="margin-top: 17px">
        <div class="card pad">
          <h2>历次总分</h2>
          <!-- 折线取代了原来的「68 → 54」一行字：MHT 每学期约一次，两三次之后
               一句话报首尾就丢掉了中间那次的形状，而那往往是真正要看的。
               点的颜色 = 这一场自己的筛查分类（阈值属于量表规则版本，前端不画参考线）。 -->
          <!-- 图与「最近一次」摘要并排。折线的 viewBox 是 560×224，而 `.chart` 是
               `width:100%; height:auto`——所以在个案详情这张宽卡里它会一路撑到 300px
               以上，而下面「八维度历次变化」那八个格子每个才 34px 高。给图一个宽度上限
               （`.trend-split` 的第一列），右侧空出来的地方放摘要：不是把图缩小，
               是把那一块地方还给真正要读的那几个数。 -->
          <div class="trend-split" style="margin-top:15px">
            <div class="trend-chart">
              <TrendChart :history="detail.history" />
            </div>
            <div class="trend-facts">
              <div v-if="latestSitting" class="detail-row">
                <span>最近一次</span>
                <b class="trend-latest">
                  {{ latestSitting.total_score }} 分
                  <span class="pill" :class="levelTone(latestSitting.total_level)">
                    {{ levelLabel(latestSitting.total_level) }}
                  </span>
                </b>
              </div>
              <!-- 「较上次」是这次补上的一格：趋势页最该回答的就是「比上次升了还是降了」，
                   而此前只有一张图让人自己看。**没有配「好转 / 恶化」这类词**——一次分值
                   的升降不构成疗效结论（产品边界）；颜色只说往哪边走，方向与 `levelTone`
                   一致（分越高、档越重、越红），所以同一张卡上两处颜色不可能互相矛盾。
                   `—` 是「比不了」的界面占位符（§8 那条的同一个约定），不是 0。 -->
              <div class="detail-row">
                <span>较上次</span>
                <b v-if="latestDelta === null" class="muted">—</b>
                <b v-else :class="`delta-${deltaTone(latestDelta)}`">
                  {{ latestDelta > 0 ? '+' : '' }}{{ latestDelta }} 分
                </b>
              </div>
              <div class="detail-row"><span>测评次数</span><b>{{ detail.history.length }} 次</b></div>
              <div class="detail-row">
                <span>最近作答用时</span>
                <b>{{ latestDuration === null ? '暂无记录' : formatDuration(latestDuration) }}</b>
              </div>
            </div>
          </div>
          <!-- **恰好一场**时这一页本来是一排孤零零的点。明说它画不出一张趋势图，
               并把读者送去此刻真正答得上问题的那个页签——MHT 每学期约一次，
               学生在校第一年就落在这个分支里。
               判据是 `=== 1`，**不是 `< 2`**（2026-09-27 §5.19 修）：一场都没有时
               这句话是假话，而同屏的折线此刻正写着「暂无历次测评数据」——两句话
               在同一屏上各说各的。那种情况归折线的空态回答：「还没测过」与
               「测了一次、看不出变化」不是一件事（§11 那条 `None` vs `0` 的同一条）。 -->
          <div v-if="detail.history.length === 1" class="notice" style="margin-top:12px">
            这名学生只有一次测评记录，还看不出变化。要看他当前相对于同龄人的位置，
            请打开<b>班级对照</b>页签——那一页不需要历史数据。
          </div>
          <!-- 作废那一档的说明（V2.0.0 §4.15 / §4.6）。**两条线都不删掉那一场**：
               图表照旧画出它的点，只是把「它不参与当前判断」说出来。理由与
               `care_service` 那段刻意的「不加 `effective_session_predicate()`」逐字同源
               ——降级/作废说的是「现在以哪一份为准」，不是「那一次不算测评」。
               两句措辞都取自 `labels.ts`，视图里不另抄一份（§3）。 -->
          <!-- 文本节点写成**连续的一段**（2026-09-27，§5.15.9 UX-FINAL-02）：Vue 的
               `whitespace: 'condense'` 会把「文本 换行 文本」压成一个空格，而在中文里
               那个空格是看得见的——此前屏幕上读出来是「都还在 ——但这几场」「他现在
               怎么样」，两头各多一个不属于原句的空隙。**不是重新排版，是把换行折出来的
               空格去掉**；标点与措辞一个字没动，两个常量也照旧取自 `labels.ts`（§3）。 -->
          <div v-if="voidedCount > 0" class="notice" style="margin-top:12px">
            上面有 <b>{{ voidedCount }}</b> 场所属的筛查任务已作废（{{ VOIDED_TASK_SOURCE_NOTE }}）。它们仍然画在图上——那几场是他真实考过的，答案、用时、当天的分都还在——但这几场<b>{{ VOIDED_SITTING_LABEL }}</b>：不要把它们的分数当成他现在怎么样的依据，也不要据此比较变化。
          </div>
          <div class="notice" style="margin-top:12px">
            趋势只描述历次分值变化，不构成诊断或疗效结论。
          </div>
        </div>

        <div class="card pad" style="margin-top:17px">
          <h2>八维度历次变化</h2>
          <p class="muted tiny" style="margin:6px 0 0">
            每格已按该维度自己的题数归一化（各维度 10 或 15 题），纵轴 0–100%，格子之间才可比。
          </p>
          <div style="margin-top:15px">
            <DimensionTrends :history="detail.history" />
          </div>
        </div>

        <div class="card pad" style="margin-top:17px">
          <!-- 「安排复测」2026-09-27 移到页签上方那条「下一步操作」里。这条页签本身
               仍然叫「历次趋势」，而页签里的**复测计划卡片保留**——学校仍然用它登记
               「下学期再看一次」（见 `tabs` 里那一段注释）。 -->
          <h2>复测计划</h2>
          <div class="checklist" style="margin-top:14px">
            <div v-for="plan in detail.retest_plans" :key="plan.id" class="check-row">
              <span>
                <b>{{ formatDate(plan.planned_date) }}</b>
                <br>
                <span class="tiny muted">{{ plan.reason }}</span>
              </span>
              <span class="pill blue">{{ retestStatusLabel(plan.status) }}</span>
            </div>
            <div v-if="!detail.retest_plans.length" class="empty">暂无复测计划</div>
          </div>
        </div>
      </div>

      <!-- 档案事件（§16.4）——「这份档案经历了什么」 -->
      <div v-if="activeTab === 'events'" class="card pad" style="margin-top: 17px">
        <h2>档案事件</h2>
        <p class="muted tiny" style="margin:6px 0 0">
          这份档案从开档到现在的每一步：谁复核过、谁跟进过、什么时候关的、什么时候又打开的。
          <b>只列这一份档案</b>——这名学生更早那条已关闭档案上的事件不在这里，但那些记录本身
          一条都没删（见「跟进记录」「家庭回访」两个页签，它们按学生跨档案取全部历史）。
        </p>
        <div class="timeline" style="margin-top:21px">
          <div v-for="event in detail.events" :key="event.id" class="timeline-item">
            <div class="timeline-dot" :class="careEventTone(event.event_type)"></div>
            <div class="timeline-title">
              <span :class="['pill', careEventTone(event.event_type)]">{{ careEventLabel(event.event_type) }}</span>
              <span class="muted tiny">
                {{ event.operator_name || '系统' }} · {{ formatDateTime(event.created_at) }}
              </span>
            </div>
            <!-- 转派是这张表上唯一不涉及状态迁移的事件，它的两列都为空，这一行就不出现。 -->
            <div v-if="event.to_status" class="timeline-text">
              状态：{{ event.from_status ? statusLabel(event.from_status) : '—' }}
              → {{ statusLabel(event.to_status) }}
            </div>
            <div v-if="event.reason" class="timeline-text">{{ event.reason }}</div>
            <div v-if="event.confirmed_facts" class="timeline-text">{{ event.confirmed_facts }}</div>
          </div>
          <div v-if="!detail.events.length" class="empty">暂无档案事件</div>
        </div>
      </div>

      <!-- 访问审计 -->
      <div v-if="activeTab === 'auditStudent'" class="card pad" style="margin-top: 17px">
        <h2>该学生敏感访问记录</h2>
        <div class="table-wrap" style="margin-top: 17px">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>操作者</th>
                <th>角色</th>
                <th>行为</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in detail.audit_logs" :key="log.id">
                <td class="nowrap">{{ formatDateTime(log.created_at) }}</td>
                <td>{{ log.actor_name || '—' }}</td>
                <td>{{ log.actor_role || '—' }}</td>
                <td>
                  {{ log.action }}
                  <span v-if="log.purpose" class="muted tiny"> · {{ log.purpose }}</span>
                </td>
                <td><span class="pill green">{{ log.result }}</span></td>
              </tr>
              <tr v-if="!detail.audit_logs.length">
                <td colspan="5"><div class="empty">暂无记录</div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <FormDialog
      :open="showForm"
      :title="formTitle"
      :fields="formFields"
      :submit-text="formSubmitText"
      @submit="onFormSubmit"
      @cancel="onFormCancel"
      @update:open="showForm = $event"
    />

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

    <!-- 受控导出（单学生） -->
    <Modal
      :model-value="showExport"
      title="受控导出学生摘要"
      size="lg"
      @update:model-value="showExport = $event"
    >
      <div class="form-grid">
        <div class="notice danger">
          学生心理数据属于高敏感数据。默认不导出原始答卷、重点题、访谈正文或家庭回访正文。
        </div>
        <div class="form-two" v-if="detail">
          <div class="field">
            <label>导出对象</label>
            <input :value="`${detail.student.name} · ${detail.student.student_no}`" disabled />
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
          <input v-model="exportConsent" type="checkbox" />
          <span>我确认该导出用于授权工作范围，并接受审计记录</span>
        </label>
      </div>
      <template #footer>
        <button class="btn" @click="showExport = false">取消</button>
        <button class="btn primary" @click="confirmExport">确认导出</button>
      </template>
    </Modal>

    <!-- 重点题二次查看 · 第一段：必填查看原因 -->
    <Modal
      :model-value="showKeyReason"
      title="二次查看重点题"
      @update:model-value="showKeyReason = $event"
    >
      <div class="form-grid">
        <div class="notice danger">
          以下信息属于最高敏感级别。查看行为将写入审计日志，题目回答不等同于诊断。
        </div>
        <div class="field" style="margin-top:14px">
          <label>查看原因 <span class="required">*</span></label>
          <select v-model="keyReason">
            <option value="">请选择</option>
            <option v-for="reason in settings.export.key_question_reasons" :key="reason" :value="reason">
              {{ reason }}
            </option>
          </select>
        </div>
      </div>
      <template #footer>
        <button class="btn" @click="showKeyReason = false">取消</button>
        <button class="btn primary" :disabled="keyAnswersLoading" @click="confirmKeyReason">确认并查看</button>
      </template>
    </Modal>

    <!-- 重点题二次查看 · 第二段：展示回答 -->
    <Modal
      :model-value="showKeyAnswers"
      title="重点关注题目"
      @update:model-value="showKeyAnswers = $event"
    >
      <div class="form-grid">
        <div class="notice warn">仅展示已授权工作所需信息，不自动产生风险诊断。</div>
        <div class="detail-grid" style="margin-top:14px">
          <div v-for="item in keyAnswers" :key="item.question_no" class="detail-row">
            <span>第 {{ item.question_no }} 题</span>
            <strong :style="item.answer === 'YES' ? 'color:var(--red)' : ''">
              回答：{{ item.answer === 'YES' ? '是' : '否' }}
            </strong>
          </div>
          <div v-if="!keyAnswers.length" class="empty">该生没有重点题命中记录</div>
        </div>
      </div>
      <template #footer>
        <button class="btn" @click="showKeyAnswers = false">关闭</button>
        <button class="btn primary" @click="goReviewFromKeyAnswers">记录人工复核</button>
      </template>
    </Modal>

  </div>
</template>

<style scoped>
/* 个案摘要头（2026-09-27，§5.15.3 UX-FINAL-02）。
 *
 * 这个文件此前**没有样式块**——这一页的长相全部来自全局 `styles.css`。摘要头是这一页
 * 独有的形状（十个 label/value 格并成一条），而全局那三类网格各管各的：`.detail-grid`
 * 是两列的「标签 → 值」详情（这一页的「本次测评事实」用的就是它）、`.perm-grid` 是角色
 * 权限矩阵、`.conventions` 是取值约定对照表。没有一个是它，所以在这里新开一个 scoped
 * 块，不去动全局。
 *
 * **只写排布，不写颜色与边框**：底色、边框、圆角、阴影全部由 `.card.tier-supporting`
 * 给（§5.15.2 那条「不新增任何 Token」）——在这里再抄一遍 `--surface2` 与 `#e6ecf2`
 * 就是把同一件事写成两份，而漂了不会有任何东西报错。
 *
 * **列数定死成五列，不交给 `auto-fit`**：十格在 `auto-fit` 下按可用宽度自己折，
 * 1440px 下折成 6+4、640px 下折成 3+3+3+1，两种都会在最后一行右侧空出两三格。
 * 那是「表格没填满」的长相——它比任何一句文案都更像页面坏了，而这一条摘要的全部
 * 意义就是「扫一眼就懂」。5 与 10 整除，两行都填满；窄屏切两列（2 与 10 也整除），
 * 于是从 320px 到 2560px 没有一种宽度会留出空位。
 *
 * 中间那一段（约 700–1180px）没有单独一档是有意的：10 的约数只有 1 / 2 / 5 / 10，
 * 三列与四列都会在最后一行留白——**宁可格子宽一点，也不要一块填不满的表格**。 */
.case-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 11px 14px;
  padding: 14px 17px;
  margin: 0 0 14px;
}

.case-summary-cell {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.case-summary-label {
  color: var(--muted);
  font-size: 0.78rem;
}

/* `overflow-wrap: anywhere`：「主要关注维度」那一格可能是一串顿号连起来的长文本
   （高分维度多的时候），不给它换行的机会就会把整格顶宽、把网格挤成一行一格。 */
.case-summary-value {
  font-size: 0.88rem;
  font-weight: 650;
  overflow-wrap: anywhere;
}

/* 药丸是 `inline-flex`，作为 grid item 默认会被 `stretch` 拉成整格宽——一个通栏的
   药丸会读成「这一格是一条状态条」。靠左按内容宽。 */
.case-summary-cell .pill {
  justify-self: start;
}

/* 五列在这个宽度以下会把每格压到 130px 出头（「重点题人工复核」七个字要折两行），
   所以切两列。两列时每格约 340px，是「一列标签一列值」都能并排的宽度。 */
@media(max-width:1180px) {
  .case-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 14px;
    padding: 13px 14px;
  }
}

/* 「历次趋势」页签的折线与摘要并排（2026-09-27，§5.15.9 UX-FINAL-02）：
   `.trend-split` / `.trend-facts` / `.trend-latest` 三条**住在 `assets/styles.css`**
   （`.delta-*` 那一节后面）——2026-09-27 §5.19 测评记录页的「总分变化」卡要用同一套
   排布，而 scoped 样式出不了这个组件，各写一份就是两个定义。那里同时写着「为什么第一列
   要设上限」「第二列的下限 190px 是给谁要的」「断点为什么取 860px」。 */

/* `.delta-red` / `.delta-green` / `.delta-gray` 同样不在这个文件里：`DimensionTrends`
   也要用同一套颜色，而 scoped 样式出不了这个组件——各写一份就是两个定义。
   它们住在 `styles.css` 里 `.pill` 色调那一节后面（与上面那三条同一处）。 */
</style>
