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
import { daysFromNow, formatDuration, today } from '../../services/dates'
import {
  ageLabel,
  calculationStatusLabel,
  calculationStatusTone,
  careEventLabel,
  careEventTone,
  genderLabel,
  levelLabel,
  levelTone,
  retestStatusLabel,
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

/** 去这个学生的「测评记录」页——那条路不需要他有档案，所以它永远通。 */
function openStudentRecords() {
  if (!detail.value) return
  router.push(`/counselor/students/${detail.value.student.id}/records`)
}

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
        <button class="btn" @click="router.back()">返回列表</button>
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
                 日期来源照实说「待核实」。 -->
            <div class="detail-row">
              <span>测评日期</span>
              <b>{{ detail.assessment.tested_at || detail.assessment.submitted_at || '—' }}</b>
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
          <div class="actions" style="margin-top:14px">
            <button class="btn" @click="viewKeyQuestions">二次查看重点题</button>
            <button class="btn primary" @click="saveReview">记录人工复核</button>
          </div>
        </div>
      </div>

      <!-- 跟进记录 -->
      <div v-if="activeTab === 'follow'" class="card pad" style="margin-top: 17px">
        <!-- `.toolbar` 而不是 `.actions`：`.actions` 没有基础规则（只在 `.page-head` /
             `.question-foot` 两个限定选择器下存在），所以它是个 `display:block` 的 div，
             写在上面的 `justify-content` 一直是空转的——标题与按钮各占一行（CLAUDE.md §17）。 -->
        <div class="toolbar" style="justify-content:space-between">
          <h2>连续跟进时间线</h2>
          <button class="btn primary" @click="addFollowUp">新增跟进</button>
        </div>
        <div class="timeline" style="margin-top:21px">
          <div v-for="record in detail.follow_ups" :key="record.id" class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="muted tiny">{{ record.created_at || '—' }} · {{ record.record_type }}</div>
            <div class="timeline-title">{{ record.confirmed_facts }}</div>
            <div class="timeline-text">下次跟进：{{ record.next_follow_up_date || '—' }}</div>
          </div>
          <div v-if="!detail.follow_ups.length" class="empty">暂无跟进记录</div>
        </div>
      </div>

      <!-- 家庭回访 -->
      <div v-if="activeTab === 'family'" style="margin-top: 17px">
        <div class="card">
          <div class="card-head">
            <h2>家庭回访记录</h2>
            <button class="btn primary" @click="addFamilyContact">新增回访</button>
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
                    <td>{{ record.contact_date }}</td>
                    <td>{{ record.contact_person }}</td>
                    <td>{{ record.channel }}</td>
                    <td>{{ record.result }}</td>
                    <td>{{ record.support_status }}</td>
                    <td>{{ record.next_contact_date || '—' }}</td>
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
          <div style="margin-top:15px">
            <TrendChart :history="detail.history" />
          </div>
          <div class="detail-grid" style="margin-top:18px">
            <div class="detail-row"><span>测评次数</span><b>{{ detail.history.length }} 次</b></div>
            <div class="detail-row">
              <span>最近作答用时</span>
              <b>{{ latestDuration === null ? '暂无记录' : formatDuration(latestDuration) }}</b>
            </div>
            <div class="detail-row"><span>解释边界</span><b>仅描述分值变化</b></div>
          </div>
          <!-- 只有一场时这一页本来是一排孤零零的点。明说它画不出一张趋势图，
               并把读者送去此刻真正答得上问题的那个页签——MHT 每学期约一次，
               学生在校第一年就落在这个分支里。 -->
          <div v-if="detail.history.length < 2" class="notice" style="margin-top:12px">
            这名学生只有一次测评记录，还看不出变化。要看他当前相对于同龄人的位置，
            请打开<b>班级对照</b>页签——那一页不需要历史数据。
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
          <!-- 同上一处：换成 `.toolbar` 那句 `justify-content` 才真的生效（CLAUDE.md §17）。 -->
          <div class="toolbar" style="justify-content:space-between">
            <h2>复测计划</h2>
            <button class="btn primary" @click="addRetest">安排复测</button>
          </div>
          <div class="checklist" style="margin-top:14px">
            <div v-for="plan in detail.retest_plans" :key="plan.id" class="check-row">
              <span>
                <b>{{ plan.planned_date }}</b>
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
                {{ event.operator_name || '系统' }} · {{ event.created_at || '—' }}
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
                <td class="nowrap">{{ log.created_at || '—' }}</td>
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
