<!--
  数据中心：心理老师的导入台（题库导入 / MHT测评记录导入）与近期数据任务。

  **学生信息导入不在这里**（2026-09-17 删）。它曾在这页有一份 `v-if="isAdmin"`
  的副本，而这条路由的 `meta.role` 是 `counselor`、`AppLayout` 会把管理员弹回
  登录页，所以那份副本对谁都不显示——名册的导入只有「组织学生」一个入口。
  删的是副本，功能一直都在。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { useDataImport } from '../../composables/useDataImport'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import {
  AGE_RESOLUTION_LABELS,
  CONFLICT_RESOLUTION_LABELS,
  MATCH_STATUSES_NEEDING_RESOLUTION,
  ageResolutionLabel,
  assessmentResolutionLabel,
  conflictResolutionLabel,
  importBatchStatusLabel,
  importBatchStatusTone,
  importConflictLabel,
  importModeLabel,
  importRowStatusLabel,
  importRowStatusTone,
  matchStatusLabel,
  matchStatusTone,
  outOfScopeReasonLabel,
  taskStatusLabel
} from '../../services/labels'
import {
  getMe,
  getAuditLogs,
  getAssessmentImportBatches,
  getAssessmentImportRows,
  getAssessmentTasks,
  exportHighRiskCareCases,
  resolveAssessmentImportRow,
  type AssessmentImportBatch,
  type AssessmentImportRow,
  type AssessmentRowCounts,
  type AssessmentTaskItem,
  type AuditLogItem
} from '../../services/api'

const router = useRouter()
const { settings } = useSettings()

const {
  questionPreview,
  assessmentBatch,
  assessmentRows,
  assessmentTotal,
  assessmentRowCounts,
  assessmentRowsError,
  assessmentResolution,
  assessmentBatchResolutionRows,
  assessmentAgeResolution,
  importingQuestions,
  creatingDraft,
  importingAssessments,
  committingAssessments,
  submitQuestionFile,
  commitDraft,
  submitAssessmentFile,
  openAssessmentBatch,
  retryAssessmentBatch,
  resetAssessmentBatch,
  commitAssessments,
  downloadQuestionTemplate,
  downloadAssessmentImportTemplate
} = useDataImport()

const audits = ref<AuditLogItem[]>([])
const loading = ref(true)
const error = ref('')
const showExportLog = ref(false)
const selectedExportLog = ref<AuditLogItem | null>(null)

/**
 * 逐行明细弹层的数据**独立于卡片上那一批**。
 *
 * 从批次历史里翻一批已导入的旧批次看明细时，卡片上那份还没提交的工作不该被顶掉——
 * 复用同一份状态的话，操作员出于好奇点一下「查看明细」，回来发现自己刚传的那一批
 * 从屏幕上消失了（而它是可以接着提交的）。两条流程各有各的取数，互不打扰。
 */
const showAssessmentRows = ref(false)
const detailBatch = ref<AssessmentImportBatch | null>(null)
const detailRows = ref<AssessmentImportRow[]>([])
const detailTotal = ref(0)
const detailCounts = ref<AssessmentRowCounts | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

/**
 * 逐行处置的**草稿**（§18.6）：只装「打开这一批时是什么样、操作员有没有改过」。
 *
 * 起手值**取自服务端那一行**（`row.resolution` / `row.age_resolution`），所以打开明细
 * 看到的就是库里存着的样子；改过的行与没改过的行在同一张表里也分得出来
 * （`rowDraftDirty`），而不是靠一个预填的默认值冒充「操作员选过」。
 */
const rowDrafts = ref<Record<number, RowDraft>>({})
const savingRowId = ref<number | null>(null)

interface RowDraft {
  /** 空串 = 这一行还没有处置（还不该提交，`commit_batch` 会把它算进「需要确认」）。 */
  resolution: '' | 'overwrite' | 'skip'
  /** 同上：空串 = 没选。只有 `AGE_MISMATCH` 的行才问这一栏。 */
  ageResolution: string
  /**
   * 同上：空串 = 没选。只有 `CONFLICT` 的行才问这一栏（§18.8 的四档处置）。
   *
   * **它与 `resolution` 是两个问题**，所以两栏可以同时有值也可以各自为空：前者答
   * 「这一行写不写进去」，后者答「以哪一份为准、另一份留不留」。冲突行的 `resolution`
   * 实际上一直是空的（四档处置写在 `conflict_resolution` 上），前端不靠它放行——放行
   * 与否由后端在提交那一刻判。
   */
  conflictResolution: string
}

/**
 * 待确认的行必须**先拍板**：按钮不置灰的话，点下去只会拿回一句 422。
 *
 * 判据是**整批那次选择管得着的那一部分**（`assessmentBatchResolutionRows`），不是
 * `needing_resolution`：冲突行不由「覆盖 / 放弃」回答，把它们的数算进来会让一批
 * 待确认**只有**冲突行的批次，逼着操作员在两个按本页的话「管不着这些行」的选项里
 * 挑一个，才肯把按钮点亮。冲突行那一道门在服务端（未逐行处置时 422 并指名行号）。
 */
const needsAssessmentResolution = computed(
  () => assessmentBatchResolutionRows.value > 0 && !assessmentResolution.value
)

/**
 * 这一批里**一行都写不进去**（可导入 0、待确认 0）。
 *
 * 与上面那条分开：待确认是「拍个板就能进」，这一条是「这个按钮上做什么都没用」
 * ——出路在名册或任务那边。后端也会拦（「这一批里没有可导入的记录」），但那是点下去
 * 之后；先说清楚缺的是哪一步。
 */
const assessmentNothingImportable = computed(() => {
  const counts = assessmentRowCounts.value
  if (!counts) return false
  return counts.ready + counts.needing_resolution === 0
})

/**
 * 这一批里**有没有**年龄与名册不符的行——没有就不摆那一栏（摆了也没得选）。
 *
 * 判据取的是**可见的那几行**，而它有一个已知的边界：`/rows` 每次最多给 200 行
 * （`assessment_import_row` 的行数可以更多）。所以「屏幕上没有这一栏」不等于
 * 「这一批一个年龄冲突都没有」，它只等于「**你看得见的这些行里**没有」。
 * 这正是**不把它做成提交硬门槛**的理由：拿可见行去判「整批有没有」，会把一批
 * 三百行的文件里第 250 行那个冲突判成不存在，而操作员看不到任何提示。
 * 不选就走老口径（按文件里的年龄更新名册），而那一句话就写在这三个选项下面。
 */
const hasVisibleAgeConflict = computed(() =>
  assessmentRows.value.some(row => row.conflict_code === 'AGE_MISMATCH')
)

// --- MHT测评记录导入：表单字段 ---
// 批次名称与测评日期是**必填的请求字段**，不在文件里：一场普查叫什么、是哪天做的，
// 只有操作员知道。日期默认今天（跑得最多的就是「今天导昨天的那场」），
// 批次名称在选好文件后按文件名预填，可改。
const assessmentBatchName = ref('')
const assessmentTestedOn = ref(todayIso())
// 关联任务决定判重口径（§18.7 / §18.8），不选就是「历史外部结果」那条路。
const assessmentTaskId = ref<number | null>(null)
// 数据来源平台：真后端字段（取不到时后端写 `UNKNOWN`），操作员会想知道这批从哪来。
const assessmentSourceSystem = ref('')

const assessmentTasks = ref<AssessmentTaskItem[]>([])
const batchHistory = ref<AssessmentImportBatch[]>([])
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyError = ref('')
/** 正在「继续处理」的那一批（按钮上的转圈）。 */
const resumingBatchId = ref<number | null>(null)

function todayIso() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

function fileNameWithoutExtension(name: string) {
  const dot = name.lastIndexOf('.')
  return (dot > 0 ? name.slice(0, dot) : name).slice(0, 128)
}

/** 操作人按审计页那一列的写法拼「姓名 · 账号」，缺一个就只显示另一个。 */
function actorText(batch: AssessmentImportBatch) {
  if (batch.imported_by_name && batch.imported_by_account) {
    return `${batch.imported_by_name} · ${batch.imported_by_account}`
  }
  return batch.imported_by_name || batch.imported_by_account || '—'
}

function taskOptionLabel(task: AssessmentTaskItem) {
  return `${task.name}（${task.task_no} · ${taskStatusLabel(task.status)}）`
}

async function loadAssessmentTasks() {
  try {
    assessmentTasks.value = await getAssessmentTasks()
  } catch {
    // 拉不到任务列表**不是**这一页的错误：不绑任务的导入照旧能用（判重退回自然月）。
    // 所以只把下拉变成空的，不把整页变成错误页——与批次历史那一处的理由相同。
    assessmentTasks.value = []
  }
}

async function loadBatchHistory() {
  historyLoading.value = true
  historyError.value = ''
  try {
    const page = await getAssessmentImportBatches()
    batchHistory.value = page.items
    historyTotal.value = page.total
  } catch (err) {
    historyError.value = err instanceof Error ? err.message : '导入批次加载失败'
  } finally {
    historyLoading.value = false
  }
}

/** Recent data jobs are derived from the audit trail rather than a separate store. */
const recentJobs = computed(() =>
  audits.value
    .filter(a => ['导入学生', '导入题库草稿版本', '导入测评记录', '导出关注档案摘要', '导出高度关注摘要', '导出单个学生摘要'].includes(a.action))
    .slice(0, 10)
)

const IMPORT_ACTIONS = ['导入学生', '导入题库草稿版本', '导入测评记录']

function jobResult(job: AuditLogItem) {
  return job.action.startsWith('导入') ? '已写入' : '已脱敏，已审计'
}

function jobTone(job: AuditLogItem) {
  return IMPORT_ACTIONS.includes(job.action) ? 'green' : 'blue'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    // 这一页只归心理老师：路由的 `meta.role` 就是 counselor，`AppLayout` 会把
    // 别人弹回登录页，所以这里列 `['admin','counselor']` 是自欺——
    // 管理员永远到不了这一行（2026-09-17 实测）。
    if (me.role_code !== 'counselor') {
      await router.push('/login')
      return
    }
    audits.value = (await getAuditLogs({ limit: 100 })).items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function onQuestionFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  await submitQuestionFile(file)
}

async function onAssessmentFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!assessmentBatchName.value.trim()) assessmentBatchName.value = fileNameWithoutExtension(file.name)
  if (!assessmentBatchName.value.trim() || !assessmentTestedOn.value) {
    // 后端会拦，但拦在文件上传之后：先说清楚缺哪一项，别让老师白传一次
    showToast('error', '请先填写批次名称与测评日期')
    return
  }
  await submitAssessmentFile(
    file,
    assessmentBatchName.value.trim(),
    assessmentTestedOn.value,
    assessmentTaskId.value,
    assessmentSourceSystem.value.trim()
  )
  await loadBatchHistory()
}

async function commitAssessmentsNow() {
  const result = await commitAssessments()
  // 提交之后**无论成败都重拉一次**：成功时批次历史多了一行 `COMMITTED`；
  // 失败时（比如「这一批已经提交过了（可能是在另一个窗口里）」）库里那一批的状态
  // 已经变了，而屏幕上还写着「预览中」——那时重拉这一页是操作员唯一能看见真相的地方。
  await Promise.all([load(), loadBatchHistory()])
  void result
}

/**
 * 把批次历史里那一行**载入卡片**，接着处理（第 4 期的核心出路：上传完被叫走了，
 * 回来接着提交）。它读的是服务端此刻的样子，不是上传那一刻的样子。
 */
async function resumeBatch(batch: AssessmentImportBatch) {
  resumingBatchId.value = batch.id
  try {
    const result = await openAssessmentBatch(batch)
    if (!result) {
      showToast('error', '这一批的明细没能载入，请重试')
      return
    }
    // 表单里那三项跟着这一批复原：否则屏幕上写着 A 批次的名称、而卡片里处理的是 B 批次，
    // 操作员下一次「重新上传」会带着 A 的名字建出一个新批次。
    assessmentBatchName.value = batch.batch_name
    assessmentTestedOn.value = batch.tested_on || todayIso()
    assessmentTaskId.value = batch.task_id
    assessmentSourceSystem.value = batch.source_system && batch.source_system !== 'UNKNOWN'
      ? batch.source_system
      : ''
    showToast('info', `已载入批次 ${batch.batch_no}（${batch.batch_name}），可以继续处理`)
  } finally {
    resumingBatchId.value = null
  }
}

/** 换一份文件：把卡片上那一批撤下来。批次本身还在库里（批次历史里能找回来）。 */
function forgetAssessmentBatch() {
  resetAssessmentBatch()
  assessmentResolution.value = null
  assessmentAgeResolution.value = null
}

/** 这一行是不是「拍个板就能进」的那三档之一（`labels.ts` 里那张表是唯一出处）。 */
function needsRowResolution(row: AssessmentImportRow) {
  return MATCH_STATUSES_NEEDING_RESOLUTION.includes(row.match_status)
}

/** 操作员改过、还没保存的行——只用来决定「保存」按钮亮不亮，不参与提交。 */
function rowDraftDirty(row: AssessmentImportRow) {
  const draft = rowDrafts.value[row.id]
  if (!draft) return false
  return (draft.resolution || null) !== (row.resolution || null)
    || (draft.ageResolution || null) !== (row.age_resolution || null)
    || (draft.conflictResolution || null) !== (row.conflict_resolution || null)
}

/**
 * 保存一条待拍板行的处置（§18.6）。
 *
 * **它不写任何测评记录**：这一步只把「这一行该怎么办」记下来，写库发生在提交那一次。
 * 保存之后重新拉一遍明细而不是就地改本地状态——「这一行现在是什么结论」由服务端说
 * （`resolve_import_row` 会写 `resolved_by` / `resolved_at`，而那一格界面上要显示），
 * 本地猜一份的话，它与下一次打开这一页时看到的东西就会不一样。
 */
async function saveRowResolution(row: AssessmentImportRow) {
  const draft = rowDrafts.value[row.id]
  if (!draft) return
  savingRowId.value = row.id
  try {
    await resolveAssessmentImportRow(row.id, {
      resolution: draft.resolution || undefined,
      ageResolution: draft.ageResolution || undefined,
      conflictResolution: draft.conflictResolution || undefined
    })
    // 三栏各自都可以单独保存（后端也只校验传了的那一栏），所以这一句按**实际选了哪些**
    // 拼：只选年龄处置时不能让屏幕上出现一个空的「已记下处置：」。
    const done: string[] = []
    if (draft.resolution) done.push(assessmentResolutionLabel(draft.resolution))
    if (draft.ageResolution) done.push(ageResolutionLabel(draft.ageResolution))
    if (draft.conflictResolution) done.push(conflictResolutionLabel(draft.conflictResolution))
    showToast('success', `第 ${row.row_no} 行已记下处置：${done.join('；')}`)
    if (detailBatch.value) await openDetail(detailBatch.value)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '这一行的处置没有保存')
  } finally {
    savingRowId.value = null
  }
}

/**
 * 打开某一批的逐行明细。
 *
 * **取数之前先清空**（§14）：换一批时留着上一批的行，弹层里就会标题写着 B、正文是 A。
 */
async function openDetail(batch: AssessmentImportBatch) {
  detailBatch.value = batch
  detailRows.value = []
  detailTotal.value = 0
  detailCounts.value = null
  detailError.value = ''
  // 逐行草稿跟着一起清（同一条理由：换一批时留着上一批的行内选择，
  // 界面上就会标题写着 B 的第 3 行、保存的却是 A 的第 3 行）
  rowDrafts.value = {}
  showAssessmentRows.value = true
  detailLoading.value = true
  try {
    const result = await getAssessmentImportRows(batch.id)
    detailRows.value = result.items
    detailTotal.value = result.total
    detailCounts.value = result.rowCounts
    const drafts: Record<number, RowDraft> = {}
    for (const row of result.items) {
      drafts[row.id] = {
        resolution: row.resolution === 'overwrite' || row.resolution === 'skip' ? row.resolution : '',
        ageResolution: row.age_resolution || '',
        conflictResolution: row.conflict_resolution || ''
      }
    }
    rowDrafts.value = drafts
  } catch (err) {
    detailError.value = err instanceof Error ? err.message : '明细加载失败'
  } finally {
    detailLoading.value = false
  }
}

/** 明细那一条失败之后的「重试」：重开当前这一批（`detailBatch` 此刻就是它）。 */
async function retryDetail() {
  if (detailBatch.value) await openDetail(detailBatch.value)
}

async function commitDraftNow() {
  const values = await askDraftVersion()
  if (!values.version) return
  await commitDraft(values.version)
}

// --- 题库草稿：版本号必填且不预填 ---
// 这里原来是 `commitDraft('MHT-1.0.1')`——点一下就建出一个版本号的草稿，中间没有
// 任何一次确认，而那个号比线上已发布的 MHT-1.1.0 还低。版本号是发布节奏的一部分，
// 由操作员填；占位符只提示形状，不给出一个可以用默认值带过的具体号。
const showDraftVersionForm = ref(false)
const draftVersionFields = ref<FormField[]>([])
let draftResolve: ((values: Record<string, string>) => void) | null = null

function askDraftVersion(): Promise<Record<string, string>> {
  draftVersionFields.value = [
    {
      key: 'version',
      label: '版本号',
      type: 'text',
      required: true,
      defaultValue: '',
      placeholder: '如 MHT-1.1.1（须高于当前已发布版本）'
    }
  ]
  showDraftVersionForm.value = true
  return new Promise((resolve) => { draftResolve = resolve })
}

function onDraftVersionSubmit(values: Record<string, string>) {
  if (draftResolve) draftResolve(values)
}

function onDraftVersionCancel() {
  if (draftResolve) draftResolve({})
}

function openExportLog(job: AuditLogItem) {
  selectedExportLog.value = job
  showExportLog.value = true
}

// --- 受控导出：用途必填，落审计后由后端生成 CSV ---
const showExportForm = ref(false)
const exportFormFields = ref<FormField[]>([])
let exportResolve: ((values: Record<string, string>) => void) | null = null

function askExportOptions(title: string): Promise<Record<string, string>> {
  exportFormFields.value = [
    {
      key: 'purpose',
      label: '导出用途',
      type: 'select',
      required: true,
      placeholder: '请选择',
      // 导出用途由系统配置提供（此处是第四份重复副本，此前需要改四处）
      options: settings.value.export.purposes.map(item => ({ value: item, label: item }))
    }
  ]
  showExportForm.value = true
  void title
  return new Promise((resolve) => { exportResolve = resolve })
}

function onExportSubmit(values: Record<string, string>) {
  if (exportResolve) exportResolve(values)
}

function onExportCancel() {
  if (exportResolve) exportResolve({})
}

async function exportHighRisk() {
  const values = await askExportOptions('高度关注导出')
  if (!values.purpose) return
  try {
    await exportHighRiskCareCases({ purpose: values.purpose, maskNames: true })
    showToast('success', '受控导出已完成并记录审计')
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  }
}

onMounted(async () => {
  // 三件事一起发，但**只有审计那一件决定整页的错误页**：任务列表拉不到只是那一格空着
  // （不绑任务的导入照旧能用），批次历史拉不到只让那一块卡片报错（它有自己的
  // `historyError`）。把另外两件并进 `load()` 的 try 里，会让一次任务接口的抖动
  // 把整页变成「加载失败」——而这一页上还有两个能用的导入。
  await Promise.all([load(), loadAssessmentTasks(), loadBatchHistory()])
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">批量数据作业</div>
        <h1>数据中心</h1>
        <p class="page-desc">导入先校验预览再确认；敏感导出需说明用途、限定字段并自动审计。</p>
      </div>
      <div class="actions">
        <!-- 「高度关注」就是等级里的「重点关注」（同一个 `KEY_ATTENTION`）。名字不动的
             理由见 CLAUDE.md §3 第四面那条注：审计动作码已经落了几百行，改界面上的
             名字会让用户在审计页按眼睛看到的名字搜不到。 -->
        <button class="btn primary" @click="exportHighRisk">高度关注导出</button>
      </div>
    </div>

    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <div class="grid">
      <!-- 两张卡片**竖着排、各占整幅**（2026-09-20 用户报「MHT测评记录导入显示很拥挤」）。
           此前是 `.grid.two`：测评记录导入只拿到 559px 的一半栏，而那一格里要塞四个批次
           字段、一段取值约定、一行批次摘要和一块待确认面板。**宽度是这一处拥挤的根因**，
           实测：卡片 559px 时「关联测评任务」那个下拉框只有 ~252px 可用，而它的当前值
           ——任务名——量出来最长 565px，一个字都读不全。改成整幅之后卡片是 1134px，
           卡片内部的疏密是第二件事（见 `.batch-fields.four` 与 `.conventions`）。
           次序也换了：题库导入只产出草稿（发布还归管理员），是偶尔做一次的事；而每次普查
           都要做的是测评记录导入。原先两栏并排时两张卡片一样宽，看不出这个主次。 -->
      <div class="card pad">
        <h2>MHT测评记录导入</h2>
        <p class="muted tiny" style="margin-top:7px">
          CSV · 导入在其他平台完成的普查结果，按姓名、性别、年龄、年级、班级定位学生；
          定位不到的学生会跳过并逐行报错，不建学生、不建账号。
        </p>
        <!-- 四条取值约定必须写在页面上：1/2 与 1/0 是**那个外部平台**的约定，
             本系统其它任何地方都不出现这两种写法，老师无从推断。
             排成一张四格的对照表而不是一句顿号连起来的长句——这一块是要被人**回头查**的
             （「班级那一位到底是不是年级」），四格一眼扫得到，一行字每次都要从头读一遍。
             性别那一句 2026-09-17 跟着后端一起改成了 2=男、1=女——此前反着写，
             照着这段说明填的表会把同名同班的两个学生定位反。 -->
        <dl class="conventions">
          <div><dt>性别</dt><dd>2 = 男，1 = 女</dd></div>
          <div><dt>答案</dt><dd>1 = 是，0 = 否</dd></div>
          <div><dt>年级</dt><dd>1 / 2 / 3 为初一 / 初二 / 初三</dd></div>
          <div><dt>班级</dt><dd>4 表示初一 4 班</dd></div>
        </dl>
        <!-- 这里不再写「不改名册」：年龄不符时「覆盖」的含义就是改名册上的年龄。
             那句话与下面的待确认面板自相矛盾，而面板才是有选项的那一个。 -->
        <p class="muted tiny">
          年龄与名册不符、或本月已导过一次的记录会先列出来，由你选择覆盖还是放弃。
        </p>
        <div class="batch-fields four">
          <label>
            <span class="muted tiny">批次名称</span>
            <input v-model="assessmentBatchName" type="text" maxlength="128" placeholder="如 2026年秋季心理普查" />
          </label>
          <label class="field-date">
            <span class="muted tiny">测评日期</span>
            <input v-model="assessmentTestedOn" type="date" />
          </label>
          <label class="field-source">
            <span class="muted tiny">数据来源平台（选填）</span>
            <input v-model="assessmentSourceSystem" type="text" maxlength="64" placeholder="如 市中小学生心理健康平台" />
          </label>
          <!-- 「关联测评任务」排在最后、独占一整行（`.field-task` 的 `grid-column: 1 / -1`）。
               **次序不是排版喜好，是量出来的**：它的选项是任务名，`<select>` 的当前值显示的
               是整条选中项的文字，演示库里最长的一条量出来 565px——排在三个短字段后面时它
               只剩 ~425px，四个选项里三个仍然被截断（就是用户报的那一处拥挤换了个宽度复发）。
               占一整行有 1049px 可用。这与「改短 `taskOptionLabel`」是两条路，选了这条：
               砍掉任务编号是一条用户没要的产品改动，而这里只是排布。
               它也是这四格里唯一改**判重口径**的一格，占一整行与它的分量相称。
               「它为什么存在」写在这一格自己下面（而不是另起一段）：换掉判重口径之后，
               同一份文件会得到不同的结论（绑了任务时「名册上有他、但他不在这场任务里」
               是「无法导入」，不绑时他照常导入）。不写这一句，操作员会以为它是个备注字段。 -->
          <label class="field-task">
            <span class="muted tiny">关联测评任务（选填）</span>
            <select v-model="assessmentTaskId" class="select">
              <option :value="null">不关联（按自然月判重）</option>
              <option v-for="task in assessmentTasks" :key="task.id" :value="task.id">
                {{ taskOptionLabel(task) }}
              </option>
            </select>
            <span class="muted tiny field-hint">
              关联后按「这场任务里这个人有没有有效答卷」判重，不在任务名单里的学生会标成
              「不在本场任务里」（可去任务页补发目标）；不关联则按自然月判重。
            </span>
          </label>
        </div>
        <div class="import-drop">
          <strong>导入前显示逐项校验结果</strong>
          <!-- 这里此前写着「外部记录不产生风险提示与关怀档案，只带进评分结果」——
               那是 2026-09-16 的旧口径，2026-09-17 已被用户撤回：导入的记录与学生在
               系统内作答走同一个判定函数，命中重点题 85/97 一样开出待办与档案。
               页面文案没跟着改，就成了一句对着用户说的假话。 -->
          <span class="muted tiny">判定口径与系统内作答一致：命中重点题（85/97）会开出风险提示与关怀档案</span>
          <div class="actions" style="justify-content:center;margin-top:14px">
            <button class="btn" @click="downloadAssessmentImportTemplate">下载模板</button>
            <label class="btn primary" style="cursor:pointer">
              选择文件
              <input type="file" accept=".csv" hidden @change="onAssessmentFile" />
            </label>
          </div>
        </div>

        <p v-if="importingAssessments" class="muted tiny" style="margin-top:12px">正在上传校验…</p>
        <!-- 明细没载进来时也说得出来是哪一批：卡片上那一行摘要（批次号/文件名）不依赖
             明细接口，所以它照常显示，错的只是下面那一块。 -->
        <ErrorState
          v-if="assessmentRowsError"
          :message="assessmentRowsError"
          :on-retry="retryAssessmentBatch"
        />

        <!-- 当前批次摘要。三个计数走 `row_counts`（**整批**，不套读者的数据范围），
             与 `commit_batch` 拒绝提交时数的是同一个集合——所以屏幕上说的话与点下去
             之后发生的事不可能各说各的。 -->
        <template v-if="assessmentBatch">
          <div class="import-summary" style="margin-top:14px">
            <strong>本批 {{ assessmentBatch.total_rows }} 行</strong>
            <template v-if="assessmentRowCounts">
              <span>可导入 {{ assessmentRowCounts.ready }}</span>
              <span v-if="assessmentRowCounts.needing_resolution" class="status-warn">
                待确认 {{ assessmentRowCounts.needing_resolution }}
              </span>
              <span :class="assessmentRowCounts.error ? 'status-bad' : ''">
                无法导入 {{ assessmentRowCounts.error }}
              </span>
            </template>
            <!-- 进不去的那些行**选什么都不会写**（名册上没有 / 不在本场任务里 /
                 同名分不开 / 这一行坏了），所以按钮跟着置灰——不置灰的话点下去只会
                 拿回一句 422，而那时操作员已经在页面上找不到那几行在哪。 -->
            <button
              :disabled="committingAssessments || needsAssessmentResolution || assessmentNothingImportable"
              @click="commitAssessmentsNow"
            >
              {{ committingAssessments ? '正在导入…' : '确认导入' }}
            </button>
            <button v-if="assessmentRows.length" class="btn small" @click="openDetail(assessmentBatch)">
              查看明细
            </button>
            <button class="btn small" @click="forgetAssessmentBatch">换一份文件</button>
          </div>
          <p class="muted tiny" style="margin-top:6px">
            批次 {{ assessmentBatch.batch_no }} · {{ assessmentBatch.batch_name }} ·
            文件 {{ assessmentBatch.file_name }} ·
            测评日期 {{ assessmentBatch.tested_on || '—' }} ·
            导入形态 {{ importModeLabel(assessmentBatch.import_mode) }} ·
            状态 {{ importBatchStatusLabel(assessmentBatch.status) }}
          </p>
          <!-- 汇总档要**先说清后果**（§18.9 / 缺口 10）：这一档没有逐题答案，于是提交
               之后按 `assessment_result` 说话的页面（关注等级、重点学生、关注率）上
               这一场是空的。不说这一句，操作员会以为「导入成功」而那一批学生在列表里
               一个都查不到——而他会先去查名册，那里什么都没有。 -->
          <div
            v-if="assessmentBatch.import_mode === 'EXTERNAL_SUMMARY'"
            class="notice warn"
            style="margin-top:10px"
          >
            这是一份「只有分数」的汇总文件：平台算好的总分与维度分会被记下来，但<b>没有逐题答案</b>，
            所以这一场不产生答卷，也进不了「关注等级 / 重点学生」这些按结果说话的页面。
            这些学生在这一场会被算成已完成（完成率按目标行算），成绩本身要等平台给出逐题数据。
          </div>
          <p v-if="assessmentNothingImportable" class="form-error" style="margin-top:6px">
            这一批没有可导入的记录（可导入 0 条、待确认 0 条），请点「查看明细」看每一行为什么
            进不去，按提示补齐名册或任务目标后重新上传同一个文件。
          </p>
          <!-- 「处理结果」那一列在预览态下必然全是「待导入」——不说这一句，进不去的那些行
               看起来像会进。 -->
          <p v-if="assessmentBatch.status === 'PREVIEW'" class="muted tiny" style="margin-top:6px">
            这一批还没有提交，所以明细里的「处理结果」一列全是「待导入」；提交之后它才会写上是新增、
            更新还是放弃。
          </p>
        </template>

        <!-- 待确认项：**必须由操作员回答**，所以它不是一句提示，而是两行单选项加一句
             「各是什么意思」。这里的措辞要能让人不点「查看明细」也知道自己在选什么。 -->
        <div
          v-if="assessmentRowCounts?.needing_resolution"
          class="import-conflicts"
          style="margin-top:12px"
        >
          <strong>有 {{ assessmentRowCounts.needing_resolution }} 条记录需要确认</strong>
          <p class="muted tiny" style="margin-top:4px">
            这些记录本身没有问题，问题是它们与库里已有的数据对不上：年龄与名册不符、
            本月已有一次导入，或本场测评里已有系统内提交的答卷。
            <b>无法导入的那 {{ assessmentRowCounts?.error }} 条不在其中</b>——它们选什么都不会写进去。
          </p>
          <!-- 整批那一次选择**管得着**的那些行（`needing_resolution - conflict`）：只有
               真有这一类行时才摆出这两个选项。摆着一组按下面的小字「管不着这一批的记录」
               的选项、还要求必须选一个才让提交，是一句自相矛盾的话——而这一批的待确认
               全是冲突行时，那个选择对结果毫无影响。 -->
          <template v-if="assessmentBatchResolutionRows > 0">
            <label class="conflict-option">
              <input v-model="assessmentResolution" type="radio" value="overwrite" />
              <span>
                <b>覆盖</b> —— 重复的用这份文件里的结果替换上次导入的那一份；
                年龄按文件里的数更新名册（会记一条审计）
              </span>
            </label>
            <label class="conflict-option">
              <input v-model="assessmentResolution" type="radio" value="skip" />
              <span><b>放弃这几条</b> —— 只导入其余记录，库里已有的记录与名册都不动</span>
            </label>
            <!-- 年龄处置是**第二个问题**（§18.5）：上面那两条说「这一行写不写进去」，
                 这一栏说「写进去的时候，名册上那个年龄动不动、这一场按哪个年龄记」。
                 合成一个问题就是 V1.0 的做法（选了「覆盖上次」顺带把名册年龄也改了）。
                 只在**看得见的行里**真有年龄冲突时才摆出来——没有冲突却问一句，是在问一个
                 不该问的问题；而「看得见」这个边界写在上面的 computed 里。 -->
            <template v-if="hasVisibleAgeConflict">
              <p class="muted tiny" style="margin-top:10px">
                其中「年龄与名册不符」的那几条，年龄按谁记？
              </p>
              <label v-for="(label, code) in AGE_RESOLUTION_LABELS" :key="code" class="conflict-option">
                <input v-model="assessmentAgeResolution" type="radio" :value="code" />
                <span>{{ label }}</span>
              </label>
              <!-- 这一句要**跟着上面那条选择改口**：选了「放弃」还写「不选就更新名册年龄」
                   是一句假话（那几条根本不写进去），而操作员会照着它做决定。 -->
              <p v-if="assessmentResolution === 'skip'" class="muted tiny" style="margin-top:4px">
                这一栏管的是「覆盖」的那些行：选「放弃」的那几条不写进去，名册上的年龄也不动。
              </p>
              <p v-else class="muted tiny" style="margin-top:4px">
                不选就是按文件里的年龄更新名册 —— 与上面的「覆盖」同一条老口径。
              </p>
            </template>
            <p v-if="!assessmentResolution" class="form-error" style="margin-top:6px">
              请先选择覆盖或放弃，再点「确认导入」
            </p>
          </template>
          <!-- **本文档里唯一一处「这个按钮管不了那几行」**（2026-09-19 用户裁决）：
               「本场已有系统内提交的答卷」那几条只能逐行选四种处置之一，整批的
               「覆盖」对它们无效——提交时后端会指名打回（422）。这一句必须写在这里，
               而不是等那句 422 来说：操作员此刻正看着上面那句「有 N 条记录需要确认」，
               而 N 里就有那几条，他唯一能想到的动作就是在两个选项里挑一个。
               离开它原话里那个「这两个选项」在**这一句不跟着分岔**时会变成一句假话
               （上面那两个选项这时根本没摆出来），所以两支各说各的。 -->
          <p class="muted tiny" style="margin-top:6px">
            <template v-if="assessmentBatchResolutionRows > 0">
              「本场已有系统内提交的答卷」那几条<b>不在这两个选项管得着的范围里</b>：这一批里
              有几名学生在系统内已经答过一次，以哪一份为准要一条一条选，一次点击不该把
              学生本人答的卷子一起作废。请点「查看明细」，在那几行的「处置」里各选一种。
            </template>
            <template v-else>
              这一批要确认的<b>全是「本场已有系统内提交的答卷」这一类</b>，所以上面没有
              覆盖 / 放弃可选：这一批里有几名学生在系统内已经答过一次，以哪一份为准要一条
              一条选，一次点击不该把学生本人答的卷子一起作废。请点「查看明细」，在那几行的
              「处置」里各选一种。
            </template>
          </p>
        </div>
      </div>

      <div class="card pad">
        <h2>MHT题库版本导入</h2>
        <p class="muted tiny" style="margin-top:7px">校验100题、10道效度题、维度映射、重点题与重复题号</p>
        <p class="muted tiny" style="margin-top:4px">
          导入只创建草稿版本，草稿不生效；需由系统管理员发布后才会用于新的测评任务。
        </p>
        <div class="import-drop">
          <strong>导入形成新的草稿版本</strong>
          <span class="muted tiny">不会覆盖已发布题库或历史测评结果</span>
          <div class="actions" style="justify-content:center;margin-top:14px">
            <button class="btn" @click="downloadQuestionTemplate">下载模板</button>
            <label class="btn primary" style="cursor:pointer">
              选择文件
              <input type="file" accept=".csv,.json" hidden @change="onQuestionFile" />
            </label>
          </div>
        </div>

        <p v-if="importingQuestions" class="muted tiny" style="margin-top:12px">正在上传校验…</p>
        <div v-if="questionPreview" class="import-summary" style="margin-top:14px">
          <strong>总数 {{ questionPreview.total }}</strong>
          <span>{{ questionPreview.valid ? '校验通过' : '校验不通过' }}</span>
          <button
            :disabled="!questionPreview.valid || !questionPreview.preview_token || creatingDraft"
            @click="commitDraftNow"
          >
            {{ creatingDraft ? '正在创建…' : '创建草稿版本' }}
          </button>
        </div>
        <p v-if="questionPreview?.global_errors.length" class="form-error" style="margin-top:8px">
          {{ questionPreview.global_errors.join('、') }}
        </p>
      </div>
    </div>

    <!-- 导入批次历史。这是 V1.2 第 4 期给操作员的那条出路：上传完被叫走了，
         回来在这一页找到那一批接着提交（`PREVIEW` 的行有「继续处理」）。 -->
    <div class="card" style="margin-top:17px">
      <div class="card-head">
        <h2>导入批次</h2>
        <span class="muted tiny">最近 {{ historyTotal }} 批 · 最新的在前</span>
      </div>
      <div class="card-body">
        <SkeletonBlock v-if="historyLoading" variant="table" :rows="3" />
        <!-- 这一块有自己的错误：批次历史拉不到只是这一张表看不到东西，
             上面的导入卡片照旧能用——所以它不把整页变成错误页。 -->
        <ErrorState v-else-if="historyError" :message="historyError" :on-retry="loadBatchHistory" />
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>批次</th><th>名称</th><th>测评日期</th><th>状态</th><th>本批行数</th>
                <th>新增</th><th>更新</th><th>放弃</th><th>操作人</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="batch in batchHistory" :key="batch.id">
                <td class="nowrap">{{ batch.batch_no }}</td>
                <td>{{ batch.batch_name }}</td>
                <td class="nowrap">{{ batch.tested_on || '—' }}</td>
                <!-- 走 `importBatchStatusLabel` 而不是打印 `PREVIEW`：§3 第一面。 -->
                <td><span :class="['pill', importBatchStatusTone(batch.status)]">{{ importBatchStatusLabel(batch.status) }}</span></td>
                <td>{{ batch.total_rows }}</td>
                <!-- 预览态下这三个数**还没有意义**（这一趟一条都没写），所以显示 `—`
                     而不是 `0`：`0` 是「提交了，一条都没写进去」，两者不是一回事。 -->
                <template v-if="batch.status === 'COMMITTED'">
                  <td>{{ batch.created_rows }}</td>
                  <td>{{ batch.updated_rows }}</td>
                  <td>{{ batch.skipped_rows }}</td>
                </template>
                <template v-else>
                  <td class="muted">—</td>
                  <td class="muted">—</td>
                  <td class="muted">—</td>
                </template>
                <td>{{ actorText(batch) }}</td>
                <td>
                  <button class="btn small" @click="openDetail(batch)">查看明细</button>
                  <button
                    v-if="batch.status === 'PREVIEW'"
                    class="btn small"
                    :disabled="resumingBatchId === batch.id"
                    @click="resumeBatch(batch)"
                  >
                    {{ resumingBatchId === batch.id ? '载入中…' : '继续处理' }}
                  </button>
                </td>
              </tr>
              <tr v-if="!batchHistory.length">
                <td colspan="10"><div class="empty">暂无导入批次</div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:17px">
      <div class="card-head">
        <h2>近期数据任务</h2>
        <span class="muted tiny">来自审计日志</span>
      </div>
      <div class="card-body">
        <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
        <!-- `!error` 必须在 `v-else` 之前判：`recentJobs` 由审计列表派生，拉不到审计
             时它是空的，这张表会写出「暂无数据作业记录」。 -->
        <ErrorState v-else-if="error" :message="error" :on-retry="load" />
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr><th>时间</th><th>任务</th><th>结果</th><th>角色</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="job in recentJobs" :key="job.id">
                <td class="nowrap">{{ job.created_at || '—' }}</td>
                <td>{{ job.action }}</td>
                <td><span :class="['pill', jobTone(job)]">{{ jobResult(job) }}</span></td>
                <td>{{ job.actor_role || '—' }}</td>
                <td>
                  <button
                    v-if="job.action.startsWith('导出')"
                    class="btn small"
                    @click="openExportLog(job)"
                  >
                    查看记录
                  </button>
                  <!-- 这里原来直接打印 `job.resource_type`（`STUDENT` / `ASSESSMENT_TASK` /
                       `EXPORT`…）。那是后端编码，§3 不允许出现在界面上，只是要等到库里真有一条
                       「导入学生」的审计行，它才会显形——`e2e/vocabulary.spec.ts` 就是这么抓到的。
                       这一格对导入行本来也不承载信息：导的是什么已经写在「任务」列里了；
                       导出行有「查看记录」。所以不是给它配一份映射，而是不再打印编码。
                       （`AuditPage.vue` 那一列的裸编码是已知缺口 7，三件事要一起做，不在这里顺带改。） -->
                  <span v-else class="muted tiny">—</span>
                </td>
              </tr>
              <tr v-if="!recentJobs.length">
                <td colspan="5"><div class="empty">暂无数据作业记录</div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <Modal
      :model-value="showAssessmentRows"
      :title="detailBatch ? `导入明细 · ${detailBatch.batch_no}` : '导入明细'"
      size="lg"
      @update:model-value="showAssessmentRows = $event"
    >
      <div v-if="detailBatch" class="detail-grid">
        <div class="detail-row"><span>批次名称</span><b>{{ detailBatch.batch_name }}</b></div>
        <div class="detail-row"><span>文件</span><b>{{ detailBatch.file_name }}</b></div>
        <div class="detail-row"><span>测评日期</span><b>{{ detailBatch.tested_on || '—' }}</b></div>
        <!-- 导入形态是这一批的**性质**，不是结果：逐题答卷与只有分数两种在批次列表上
             长得一样（`source` 都是 `IMPORTED`），而它们提交之后能被查到的程度不同。
             所以它进了这块摘要，而不是只写在提交前的那一句提示里。 -->
        <div class="detail-row">
          <span>导入形态</span>
          <b>{{ importModeLabel(detailBatch.import_mode) }}</b>
        </div>
        <div class="detail-row">
          <span>状态</span>
          <b>
            <span :class="['pill', importBatchStatusTone(detailBatch.status)]">
              {{ importBatchStatusLabel(detailBatch.status) }}
            </span>
            <!-- 同一批导两遍时「一遍新建、一遍覆盖」在别处长得一样，只有这一格说得清。
                 `NONE`（这一批没有需要拍板的行）不显示：那一行会是「已导入 · 无需处置」，
                 而「已导入」已经把「没有例外」说完了。 -->
            <template v-if="detailBatch.status === 'COMMITTED' && detailBatch.resolution && detailBatch.resolution !== 'NONE'">
              · {{ assessmentResolutionLabel(detailBatch.resolution) }}
            </template>
          </b>
        </div>
        <div class="detail-row">
          <span>可导入</span>
          <b class="status-ok">{{ detailCounts ? detailCounts.ready : '—' }}</b>
        </div>
        <div class="detail-row">
          <span>待确认</span>
          <b :class="detailCounts?.needing_resolution ? 'status-warn' : ''">
            {{ detailCounts ? detailCounts.needing_resolution : '—' }}
          </b>
        </div>
        <div class="detail-row">
          <span>无法导入</span>
          <b class="status-bad">{{ detailCounts ? detailCounts.error : '—' }}</b>
        </div>
      </div>

      <SkeletonBlock v-if="detailLoading" variant="table" :rows="4" />
      <ErrorState v-else-if="detailError" :message="detailError" :on-retry="retryDetail" />
      <template v-else>
        <div class="table-wrap" style="margin-top:14px;max-height:340px">
          <table>
            <thead>
              <tr>
                <th>行号</th><th>文件里写的</th><th>匹配结论</th><th>匹配学号</th>
                <th>待确认</th><th>处置</th><th>处理结果</th><th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in detailRows" :key="row.id">
                <td>{{ row.row_no }}</td>
                <!-- 两套值都显示：`raw_*` 是文件里印的字，`normalized_*` 是拿去找人的值。
                     只显示一套的话，「这一行为什么匹配错了」就答不上来——而那是这条链路上
                     唯一的故障形态（§18.4：原始字段与标准化字段都要保留）。 -->
                <td>
                  <div>
                    {{ row.raw_name || '—' }}
                    <span class="muted tiny">
                      （{{ row.raw_grade_name || '—' }} {{ row.raw_class_name || '—' }}<template
                        v-if="row.raw_age !== null"
                      > · {{ row.raw_age }}岁</template>）
                    </span>
                  </div>
                  <div class="muted tiny">
                    定位用：{{ row.normalized_grade_name || '—' }}
                    {{ row.normalized_class_name || '—' }}
                  </div>
                </td>
                <!-- 走 `matchStatusLabel` / `matchStatusTone`：九个码出现在界面上就是漏了
                     一次翻译（§3 第一面，`e2e/vocabulary.spec.ts` 扫像素）。 -->
                <td>
                  <span :class="['pill', matchStatusTone(row.match_status)]">
                    {{ matchStatusLabel(row.match_status) }}
                  </span>
                  <!-- 「不在本场任务里」里面还有**两种**（§18.7），而两者的出路完全不同：
                      `SUPPLEMENT_CANDIDATE` 可以补发（给得出一个动作），
                      `NOT_IN_TASK_SCOPE` 什么都不用做。药丸只说结论，这一行说该怎么办——
                      按 `out_of_scope_reason` 分岔，**不许兜底成其中一种**（§22 那条
                      `scope_type` 的同一件事：兜底会把「不知道」变成一句言之凿凿的话）。 -->
                  <div v-if="row.out_of_scope_reason" class="muted tiny" style="margin-top:4px">
                    {{ outOfScopeReasonLabel(row.out_of_scope_reason) }}
                    <template v-if="row.out_of_scope_reason === 'SUPPLEMENT_CANDIDATE'">
                      —— 可以到「测评任务」里这一场任务的详情页点「补发学生」把他加进名单，
                      再重传这份文件
                    </template>
                  </div>
                </td>
                <td>{{ row.matched_student_no || '—' }}</td>
                <!-- 走 `importConflictLabel`，与 `match_status` 分开显示：药丸说结论，
                     这一格说结论是从哪来的（年龄差多少、上次是哪一场）。 -->
                <td class="status-warn">
                  <template v-if="row.conflict_code">{{ importConflictLabel(row.conflict_code) }}</template>
                  <span v-else class="muted">—</span>
                </td>
                <!-- 「处置」这一格有两副面孔，判据是**这一批提交了没有**：
                     - 预览中：能拍板的行长得出一组单选项（`MATCH_STATUSES_NEEDING_RESOLUTION`），
                       保存走 `PATCH /assessment-import-rows/{id}/resolve`，它**不写任何测评记录**；
                     - 已提交：读的是既成事实，所以这里渲染成**文字**而不是控件——那也正是
                       `e2e/vocabulary.spec.ts` 唯一扫得到这几张表的地方（单选按钮的 `value`
                       不进 `innerText`，只渲染控件的话第二面在这两张表上是空的）。 -->
                <td>
                  <template v-if="detailBatch?.status !== 'PREVIEW'">
                    <div v-if="row.conflict_resolution" class="muted tiny">
                      {{ conflictResolutionLabel(row.conflict_resolution) }}
                    </div>
                    <div v-if="row.age_resolution" class="muted tiny">
                      {{ ageResolutionLabel(row.age_resolution) }}
                    </div>
                    <div v-if="row.resolution" class="muted tiny">
                      {{ assessmentResolutionLabel(row.resolution) }}
                    </div>
                    <span v-if="!row.conflict_resolution && !row.age_resolution && !row.resolution" class="muted">—</span>
                  </template>
                  <!-- 进不去的那四档**不给控件**：任何处置都写不进去，给一个按了也没用的
                       按钮比不给按钮更糟（`labels.ts` 那张表旁边记着这条）。 -->
                  <!-- 来源冲突（`CONFLICT`）**有它自己的一问**，而且没有整批版本
                       （§18.8，2026-09-19 用户裁决）：这一行的问题是「以哪一份为准，
                       另一份留不留」，不是「这一行写不写进去」。所以它不摆上面那一对
                       「覆盖 / 放弃」——整批的「覆盖」对这些行无效（提交时后端会指名
                       打回），而「覆盖」写在这一行上是一句承诺做不到的话：库里那一份是
                       学生本人答的卷子，一个按行点的「覆盖」也不该把它静默作废。 -->
                  <template v-else-if="row.match_status === 'CONFLICT'">
                    <div class="muted tiny" style="margin-bottom:4px">
                      {{ row.conflict_code === 'IN_SYSTEM_IN_PROGRESS'
                        ? '这名学生正在本场测评里作答（还没交卷），以哪一份为准？'
                        : '这名学生已经在本场测评里交过卷，以哪一份为准？' }}
                    </div>
                    <label
                      v-for="(label, code) in CONFLICT_RESOLUTION_LABELS"
                      :key="code"
                      class="conflict-option"
                    >
                      <input
                        v-model="rowDrafts[row.id].conflictResolution"
                        type="radio"
                        :value="code"
                        :disabled="code === 'USE_EXTERNAL' && row.conflict_code === 'IN_SYSTEM_IN_PROGRESS'"
                      />
                      <span>{{ label }}</span>
                    </label>
                    <!-- 那句话的后半截——「改用外部结果」在他还没交卷时是灰的，
                         而灰掉的按钮必须说得出为什么（后端 §20#10 逐字同一条判据）。
                         想作废他手里那一份，出路是下面那一档。 -->
                    <div
                      v-if="row.conflict_code === 'IN_SYSTEM_IN_PROGRESS'"
                      class="muted tiny"
                      style="margin-top:4px"
                    >
                      「改用外部结果」在他交卷之前不能选：他手里那一份还没交，
                      作废它等于把他正在做的事扔掉。等他交卷之后再导入，或者选下面的
                      「这一行不要了」。
                    </div>
                    <!-- 放弃是**另一个问题**，而且它在这里有一个具体的用处（上面那句话
                         指的就是它）：这一行不导入，学生手里那一份照旧。它与四档处置
                         各写各的字段，所以两个单选组互不干扰，也允许只选其中之一。 -->
                    <label class="conflict-option" style="margin-top:6px">
                      <input v-model="rowDrafts[row.id].resolution" type="radio" value="skip" />
                      <span>或者：<b>这一行不要了</b> —— 外部这份不导入，系统内那一场不动</span>
                    </label>
                    <button
                      class="btn small"
                      :disabled="!rowDraftDirty(row) || savingRowId === row.id"
                      @click="saveRowResolution(row)"
                    >
                      {{ savingRowId === row.id ? '保存中…' : '保存这一行' }}
                    </button>
                  </template>
                  <template v-else-if="needsRowResolution(row)">
                    <label class="conflict-option">
                      <input v-model="rowDrafts[row.id].resolution" type="radio" value="overwrite" />
                      <span>覆盖</span>
                    </label>
                    <label class="conflict-option">
                      <input v-model="rowDrafts[row.id].resolution" type="radio" value="skip" />
                      <span>放弃</span>
                    </label>
                    <!-- 年龄那一栏只在**这一行会写进去**时才问（选了「覆盖」）：
                         放弃的行不写，问它年龄按谁记是一句空话。 -->
                    <template
                      v-if="row.conflict_code === 'AGE_MISMATCH' && rowDrafts[row.id]?.resolution === 'overwrite'"
                    >
                      <label v-for="(label, code) in AGE_RESOLUTION_LABELS" :key="code" class="conflict-option">
                        <input v-model="rowDrafts[row.id].ageResolution" type="radio" :value="code" />
                        <span>{{ label }}</span>
                      </label>
                    </template>
                    <button
                      class="btn small"
                      :disabled="!rowDraftDirty(row) || savingRowId === row.id"
                      @click="saveRowResolution(row)"
                    >
                      {{ savingRowId === row.id ? '保存中…' : '保存这一行' }}
                    </button>
                  </template>
                  <span v-else class="muted">—</span>
                </td>
                <td>
                  <span :class="['pill', importRowStatusTone(row.processing_status)]">
                    {{ importRowStatusLabel(row.processing_status) }}
                  </span>
                </td>
                <td class="muted">{{ row.message || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- 截断要自己说出来（§10）：`/rows` 每次最多给 200 行，比出来的那个数
             回答「还有多少没显示」。 -->
        <p v-if="detailTotal > detailRows.length" class="muted tiny" style="margin-top:10px">
          共 {{ detailTotal }} 行，当前显示前 {{ detailRows.length }} 行，另有
          {{ detailTotal - detailRows.length }} 行未显示。
        </p>
        <!-- 两种原因分开说：这一批真的一行都没有，或者它的行都不在你的数据范围内
             （逐行明细按读者的范围过滤，而批次是共享的）。合成一句会让第二种情况
             看起来像第一种——而它们要采取的行动完全不同。 -->
        <p v-else-if="!detailRows.length" class="muted tiny" style="margin-top:10px">
          这一批没有逐行记录，或它的行都不在你的数据范围内。
        </p>
        <p class="muted tiny" style="margin-top:10px">
          「待确认」的三种含义：「年龄与名册不符」是文件里的年龄与名册上那个数对不上；
          「本月已导过一次」是库里已经有一次同口径的外部导入，选「覆盖」会用这份文件里的结果
          替换它；「本场已有答卷」是<b>这名学生本人在系统里答过这一场</b>，那一条要单独回答
          「以哪一份为准」（见下面那一格，整批的「覆盖」对它们无效）。上方那三个数数的是
          <b>整批</b>，而这几十行按你的数据范围过滤，所以两边可能不等。
        </p>
        <!-- 「处置」那一列的两副面孔各写一句：预览态下它能做的事与不能做的事都要说清，
             尤其是**保存 ≠ 导入**——不说这一句，操作员会以为点了「保存这一行」这条记录
             就进去了。 -->
        <p v-if="detailBatch?.status === 'PREVIEW'" class="muted tiny" style="margin-top:6px">
          「处置」那一列只有<b>拍个板就能进</b>的那三档才有选项，保存只是把「这一行该怎么办」
          记下来，<b>真正写进库里发生在「确认导入」那一次</b>。年龄与名册不符的那些行还要再回答
          一次「这一场按哪个年龄记」；与系统内答卷冲突的那些行要回答的是第三个问题——
          <b>以哪一份为准</b>（保留系统内作答 / 改用外部结果 / 不采纳外部结果 / 两份都留），
          四档在库里留下的东西各不相同（见每一条后面的括注）。进不去的那几档（名册上没有、
          不在本场任务里、同名分不开、这一行有错误）选什么都不会写进去，所以那里没有可点的
          东西——按「说明」那一列去补名册、补发目标或改文件，然后重新上传同一个文件。
        </p>
        <!-- `v-else-if="detailBatch"` 而不是裸 `v-else`：这一句说的是「已提交的那一批」，
             没有批次时它一句话都不该说（而上面那一支是 `?.` 判出来的，null 会落到这里）。 -->
        <p v-else-if="detailBatch" class="muted tiny" style="margin-top:6px">
          「处置」那一列是这一批提交时记下的既成事实：年龄那一栏记的是这一场按谁的年龄算，
          来源冲突那一栏记的是当时以哪一份为准（几行都留下的那些都会列出来）。
        </p>
      </template>
      <template #footer>
        <button class="btn primary" @click="showAssessmentRows = false">关闭</button>
      </template>
    </Modal>

    <FormDialog
      :open="showExportForm"
      title="高度关注导出"
      :fields="exportFormFields"
      submit-text="确认导出"
      @submit="onExportSubmit"
      @cancel="onExportCancel"
      @update:open="showExportForm = $event"
    />

    <FormDialog
      :open="showDraftVersionForm"
      title="创建题库草稿"
      :fields="draftVersionFields"
      submit-text="创建草稿"
      @submit="onDraftVersionSubmit"
      @cancel="onDraftVersionCancel"
      @update:open="showDraftVersionForm = $event"
    />

    <Modal :model-value="showExportLog" title="导出记录" @update:model-value="showExportLog = $event">
      <div v-if="selectedExportLog" class="detail-grid">
        <div class="detail-row"><span>导出行为</span><b>{{ selectedExportLog.action }}</b></div>
        <div class="detail-row"><span>导出用途</span><b>{{ selectedExportLog.purpose || '—' }}</b></div>
        <div class="detail-row"><span>数据范围</span><b>{{ selectedExportLog.resource_id || '全部档案' }}</b></div>
        <div class="detail-row"><span>角色</span><b>{{ selectedExportLog.actor_role || '—' }}</b></div>
        <div class="detail-row"><span>时间</span><b>{{ selectedExportLog.created_at || '—' }}</b></div>
      </div>
      <template #footer>
        <button class="btn primary" @click="showExportLog = false">关闭</button>
      </template>
    </Modal>

  </div>
</template>
