<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { showToast } from '../../services/toast'
import { createLatestRequest } from '../../services/latest-request'
import {
  TASK_STATUS_ORDER,
  genderLabel,
  levelLabel,
  levelTone,
  participationLabel,
  participationTone,
  scopeTypeLabel,
  sourceLabel,
  targetSourceLabel,
  targetStatusLabel,
  targetStatusTone,
  taskStatusLabel,
  unmatchedReasonLabel,
  unmatchedReasonTone
} from '../../services/labels'
import {
  DEFAULT_FAMILY_CONTACT,
  DEFAULT_FOLLOW_UP,
  DEFAULT_RETEST,
  DEFAULT_TASK_END,
  formatDuration,
  today
} from '../../services/dates'
import {
  getMe,
  getAssessmentTasks,
  createAssessmentTask,
  updateAssessmentTask,
  getTaskCompletion,
  getTaskTargets,
  getUnmatchedImportRows,
  supplementTaskTargets,
  downloadTaskCompletionCsv,
  downloadNonParticipantsCsv,
  downloadUnmatchedRowsCsv,
  getTaskParticipation,
  markTargetParticipation,
  type AssessmentTaskItem,
  type AssessmentImportRow,
  type TaskCompletionItem,
  type TaskParticipation,
  type TaskTargetItem,
  type SupplementPreview
} from '../../services/api'

const router = useRouter()

const columns: Column[] = [
  { key: 'name', label: '任务名称', sortable: true },
  { key: 'scope_type', label: '对象范围' },
  { key: 'start_at', label: '任务周期', sortable: true },
  { key: 'completion_rate', label: '完成率', sortable: true },
  { key: 'status', label: '状态', sortable: true, order: TASK_STATUS_ORDER },
  { key: 'actions', label: '操作' }
]

const tasks = ref<AssessmentTaskItem[]>([])
const loading = ref(true)
const error = ref('')
const canWrite = ref(false)
/** `/auth/me` 的角色码，只用来喂下面那条判据（它没有别的读者）。 */
const roleCode = ref('')

/**
 * 这一页上**逐人心理明细**（完成明细：学号 / 姓名 + 关注等级 / MHT总分）归谁读。
 *
 * 2026-09-20 加（CLAUDE.md 缺口 12）。服务端在这一天给完成明细的读与导出各加了一道
 * `STUDENT_PSYCH_DETAIL: {SCOPED}`（判据在 `task_service.ensure_detail_reader`），
 * 于是德育领导**仍然读得到这场测评、读不到这一个个的人**。前端跟着分岔，不是因为
 * 藏起来更安全——后端两道门是权威（§4）——而是因为一个**默认落地就是 403** 的弹层
 * 在领导那儿看起来就是坏了，而它其实是一个稳定事实（他的能力集是聚合与摘要）。
 *
 * **按 `role_code` 猜，是这个判据的已知代价。** `api.ts` 的 `CurrentUser` 没有
 * capabilities 字段（`/auth/me` 不发），所以前端只能照角色分。默认矩阵下它恰好等于
 * 「有 `STUDENT_PSYCH_DETAIL: SCOPED`」（只有心理老师），而权限矩阵是可改的——
 * 学校把 COUNSELOR 那一格降成 `NONE` 之后，这枚页签照旧出现、点下去 403。
 * 那一种不一致由服务端的话说清楚（`ErrorState` 里就是它那句原文），**不由前端假装
 * 判断得出来**。要修得让 `/auth/me` 下发能力，那是另一件事。
 *
 * `canWrite`（建任务 / 改任务）与它不是一回事，今天只是恰好重合：默认矩阵下两者
 * 都是心理老师。判据按**问题**取名，不按结果取名——写成 `canWrite` 的话，两者哪天
 * 分开了（矩阵一改就分开），这个名字就开始撒谎，而那正是
 * `ensure_non_participant_exporter` 改名那一处记着的形状。
 */
const canReadDetail = computed(() => roleCode.value === 'counselor')

// Modal state
const showForm = ref(false)
const formTitle = ref('')
const formFields = ref<FormField[]>([])
const formSubmitText = ref('提交')
let formResolve: ((values: Record<string, string>) => void) | null = null

const showDetail = ref(false)
const detailTask = ref<AssessmentTaskItem | null>(null)
const detailRows = ref<TaskCompletionItem[]>([])
const detailLoading = ref(false)
/**
 * 明细读失败与「这一场一个人都没交卷」是两件事（2026-09-17 补）。
 *
 * 此前失败只弹一条 toast，而 `detailRows` 停在 `[]`，于是表格照样落下
 * 「暂无完成明细」——那是一句**关于数据的话**，它会把一次读取失败说成一场空考试。
 * toast 一飘而过，弹层里的那句话留下来回答用户的问题。
 */
const detailError = ref('')

/**
 * 参与口径六个数（§18.10，V1.2 第 8 期）。
 *
 * 它与下面那张明细表**不是同一个口径**，而且这一条要写在界面上（§9）：这一组数是
 * **整场测评的**（服务端不套读者的数据范围），而明细表逐行给出姓名、随范围缩。
 * 一个只带 1 个班范围的心理老师会看到「应测 40 人」压着一张 12 行的表——两个数都对，
 * 但没有人告诉他口径不同时，他会以为表坏了。
 *
 * 它读的是 `/participation`，**不是**任务列表行上的 `total_targets` /
 * `completion_rate`：那三个数是**读者范围内**的（`list_assessment_tasks` 传了 scope），
 * 把它摆在这里会让「应测 40、已完成 12、完成率 30%」这一组自相矛盾地混着两个口径。
 */
const participation = ref<TaskParticipation | null>(null)
const participationError = ref('')

/** 连续点开两场任务的明细时，两个请求会同时在路上（见 `services/latest-request.ts`）。 */
const latest = createLatestRequest()

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

// 这张表原本在本文件里又抄了一份，与 labels.ts 的 TASK_STATUS_LABELS 完全重复——
// 正是 §3 说的「不得在各视图里重复定义」。映射回唯一的那一层；2026-09-19 连这个
// 本地取用函数也一并去掉，改用 `taskStatusLabel`——学生首页也要取同一张表，
// 两处各写一个 `labelOf(...)` 就是同族问题的下一次发作。
// tone 留在本地：labels.ts 的 statusTone 描述的是档案阶段（CLOSED 才是好的），
// 而任务的状态里 ACTIVE 才是进行中，两者语义不同，不能共用。
function statusTone(status: string) {
  if (status === 'ACTIVE') return 'green'
  if (status === 'DRAFT') return 'amber'
  return 'gray'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (!['counselor', 'leader'].includes(me.role_code)) {
      await router.push('/login')
      return
    }
    // 建任务与改任务是心理老师的动作（2026-09-17）：测评任务是学校业务，不是
    // 系统级配置，所以系统管理员整块退出了这个功能面——页面、导航项与这个开关
    // 一起收掉，只留下面的按钮是「隐藏」而不是安全措施（后端 POST/PATCH 只放行
    // COUNSELOR）。德育领导仍然只读：它的能力集是学校级聚合与摘要。
    canWrite.value = me.role_code === 'counselor'
    // 逐人心理明细那一档（缺口 12）。上面那条注释说明为什么它与 `canWrite` 是两个
    // 判据；这一行只是把角色码留在手边，喂那条 computed——`me` 是局部的，此前除了
    // 那两个 `=== 'counselor'` 之外没人留过它。
    roleCode.value = me.role_code
    tasks.value = await getAssessmentTasks()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function newTask() {
  const values = await showFormDialog('新建测评任务', [
    { key: 'name', label: '任务名称', type: 'text', required: true, placeholder: '如：2026秋季MHT心理健康筛查' },
    { key: 'start_at', label: '开始日期', type: 'date', required: true, defaultValue: today() },
    { key: 'end_at', label: '截止日期', type: 'date', required: true, defaultValue: DEFAULT_TASK_END() }
  ], '保存任务')

  if (!values.name) return
  if (values.end_at < values.start_at) {
    showToast('error', '截止日期不能早于开始日期')
    return
  }
  try {
    await createAssessmentTask({ name: values.name, start_at: values.start_at, end_at: values.end_at })
    showToast('success', '测评任务已创建')
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '创建任务失败')
  }
}

async function editTask(task: AssessmentTaskItem) {
  const values = await showFormDialog('编辑测评任务', [
    { key: 'name', label: '任务名称', type: 'text', required: true, defaultValue: task.name },
    { key: 'start_at', label: '开始日期', type: 'date', defaultValue: task.start_at?.slice(0, 10) || '' },
    { key: 'end_at', label: '截止日期', type: 'date', defaultValue: task.end_at?.slice(0, 10) || '' }
  ], '保存修改')

  if (!values.name) return
  if (values.end_at && values.start_at && values.end_at < values.start_at) {
    showToast('error', '截止日期不能早于开始日期')
    return
  }
  try {
    await updateAssessmentTask(task.id, {
      name: values.name,
      start_at: values.start_at || undefined,
      end_at: values.end_at || undefined
    })
    showToast('success', '测评任务已更新')
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '更新任务失败')
  }
}

/**
 * 完成明细的搜索与渲染上限。
 *
 * 一所千人的学校，一整场普查的明细是一千多行，而弹层里那个表格窗口只有 340px 高——
 * 一屏看八行，滚动条拖到底也没人知道第 400 行是谁。所以这一屏的主要动作是**搜索**：
 * 按学号、姓名、年级、班级或状态把范围缩到一个学生、一个班。
 *
 * `DETAIL_RENDER_LIMIT` 是**有意的截断**：一千多行一起进 DOM 是一万多个节点，
 * 而这个截断之所以安全，是因为「全部」有一条明确的出路——上面的「导出CSV」
 * （`POST /assessment-tasks/{id}/completion/export`）。
 * **截断必须自己说出来并指向那条出路**，否则就是静默丢数据。
 */
const DETAIL_RENDER_LIMIT = 200
const detailQuery = ref('')
const exportingDetail = ref(false)
const exportingNonParticipants = ref(false)
const exportingUnmatchedRows = ref(false)
/** 正在标记的那一行的 `target_id`；0 表示没有在标（用它disable住其余按钮）。 */
const markingTarget = ref<number | null>(null)

const filteredDetailRows = computed(() => {
  const q = detailQuery.value.trim().toLowerCase()
  if (!q) return detailRows.value
  return detailRows.value.filter((row) =>
    [row.student_no, row.student_name, row.grade, row.class_name, targetStatusLabel(row.status)]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(q))
  )
})
const visibleDetailRows = computed(() => filteredDetailRows.value.slice(0, DETAIL_RENDER_LIMIT))
const detailHidden = computed(() => Math.max(0, filteredDetailRows.value.length - DETAIL_RENDER_LIMIT))

/**
 * 取回「完成明细」那一张表。
 *
 * `silent` 是给**标记参与状态之后的重取**用的：那个动作改的是上面那一组数（应测 /
 * 完成率）与这一行的参与列，而用户此刻多半正停在某个页签、搜索框里还写着一个人名。
 * 走 `taskDetail` 重取会把页签弹回「完成明细」、把搜索词清空——那是**另一次打开**的
 * 语义，不是刷新。所以两条路分开：换一场任务是重新打开，标一行是就地刷新。
 */
async function loadCompletion(task: AssessmentTaskItem, silent = false) {
  const token = latest.begin()
  if (!silent) detailLoading.value = true
  try {
    const rows = await getTaskCompletion(task.id)
    // 关了 A 的明细、马上点开 B：A 的那一份晚到也不能落到 B 的表上——
    // 弹层标题写着 B，行却是 A 的人（2026-09-17 补，见 `services/latest-request.ts`）。
    if (!latest.isCurrent(token)) return
    detailRows.value = rows
    detailError.value = ''
  } catch (err) {
    if (!latest.isCurrent(token)) return
    // 不再弹 toast：这一条要留在弹层里（见 `detailError`），飘一条同样的句子
    // 会让人以为它们是两件事。
    detailError.value = err instanceof Error ? err.message : '读取完成明细失败'
  } finally {
    if (!silent && latest.isCurrent(token)) detailLoading.value = false
  }
}

async function taskDetail(task: AssessmentTaskItem) {
  detailTask.value = task
  showDetail.value = true
  // **落地页签跟着权限走**（2026-09-20，缺口 12）。上面那三个 `void loadXxx(task)`
  // 一起发，而完成明细从这一天起多一道 `STUDENT_PSYCH_DETAIL: {SCOPED}`——它是这三个
  // 读里**唯一**对德育领导关着的一个。没有这一行分岔时，领导每点开一场任务都落在一个
  // 注定 403 的页签上，看到的是「加载失败 / 无权限」加一枚重试按钮，而同一屏旁边两个
  // 页签明明有数据（见 `ErrorState` 那条：错误分支排在空态之前，所以它不会被说成
  // 「暂无完成明细」，但它依然是一句关于「这一次读取」的话，而这一次读取本来就不该发）。
  detailTab.value = canReadDetail.value ? 'completion' : 'targets'
  detailRows.value = []
  detailQuery.value = ''
  detailError.value = ''
  participation.value = null
  participationError.value = ''
  // 三个页签一起取。切页签时不再发请求，也就不会出现「切过去一秒钟空白」——
  // 这三个读各自独立，谁也不挡谁（见 `latestTargets` 的说明）。
  void loadTargets(task)
  void loadUnmatched(task)
  void loadParticipation(task)
  // 发不出去的请求不叫「提前取数」，它只是让上面那条错误分支多一条必然要落下的记录。
  if (canReadDetail.value) void loadCompletion(task)
}

/**
 * 导出这件事现在要问一句「为什么」。
 *
 * 阶段 8 之前它是「点一下、落一个文件」；现在每次导出建一行 `export_job`，而
 * `purpose` 在服务端是**必填**的（判据在 `create_export_job` 里，与其余受控导出
 * 同一条：导出要能回答「这份文件为什么被导出去」，而那句话不在文件里）。所以这一处
 * 必须**问**，不能替操作员编一句字面量——一句写死的原因会让整批审计看起来都一样，
 * 而审计里那一列的存在理由正是「这一次为什么导」。
 *
 * 两跳：建作业只回作业载荷，字节从 `/export-jobs/{id}/download` 出去。所以成功之后
 * 要顺带说出**作业编号**——文件名的后缀就是它，而「导出中心」里搜的也是它。
 */
async function exportDetail() {
  const task = detailTask.value
  if (!task) return
  const values = await showFormDialog(
    `导出「${task.name}」的完成明细`,
    [
      {
        key: 'purpose',
        label: '导出用途',
        type: 'text',
        required: true,
        maxLength: 255,
        placeholder: '如：交德育处存档',
        hint: '这一句会进审计，也是事后回答「这份文件为什么被导出去」的唯一依据。'
      }
    ],
    '导出'
  )
  if (!values.purpose) return
  exportingDetail.value = true
  try {
    const job = await downloadTaskCompletionCsv(task.id, task.task_no, values.purpose)
    showToast('success', `已完成导出（作业 ${job.job_no}，共 ${job.row_count} 行），可在「导出中心」重下`)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  } finally {
    exportingDetail.value = false
  }
}

/** 未参与名单 —— 应测但没完成的那批人，就是「还有谁要催」的那张表（§18.11）。 */
async function exportNonParticipants() {
  const task = detailTask.value
  if (!task) return
  const values = await showFormDialog(
    `导出「${task.name}」的未参与名单`,
    [
      {
        key: 'purpose',
        label: '导出用途',
        type: 'text',
        required: true,
        maxLength: 255,
        placeholder: '如：发给各班催交',
        hint: '这一句会进审计，也是事后回答「这份文件为什么被导出去」的唯一依据。'
      }
    ],
    '导出'
  )
  if (!values.purpose) return
  exportingNonParticipants.value = true
  try {
    const job = await downloadNonParticipantsCsv(task.id, task.task_no, values.purpose)
    showToast('success', `已导出未参与名单（作业 ${job.job_no}，共 ${job.row_count} 人）`)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  } finally {
    exportingNonParticipants.value = false
  }
}

/**
 * 未匹配行清单（§20#14 的「导出」那一半）：这场任务下没进得去的那几类导入行。
 *
 * 与上面那两份**不是同一批数据**，所以三个地方刻意不一样：
 * - 单位是「行」不是「人」——一行是一条导入记录，同一个人可能出现在好几行里；
 * - 列是「哪一批、第几行、文件里写的是谁、为什么没进来」，没有等级那一列；
 * - **服务端不封顶**（列表这一屏封顶 200 行，§10：截断只许影响显示，不许影响写入）。
 *   所以提示里报的是文件自己的行数，不是屏幕上此刻看得见的那些。
 */
async function exportUnmatchedRows() {
  const task = detailTask.value
  if (!task) return
  const values = await showFormDialog(
    `导出「${task.name}」的未匹配行清单`,
    [
      {
        key: 'purpose',
        label: '导出用途',
        type: 'text',
        required: true,
        maxLength: 255,
        placeholder: '如：交给负责补名册的老师',
        hint: '这一句会进审计，也是事后回答「这份文件为什么被导出去」的唯一依据。'
      }
    ],
    '导出'
  )
  if (!values.purpose) return
  exportingUnmatchedRows.value = true
  try {
    const job = await downloadUnmatchedRowsCsv(task.id, task.task_no, values.purpose)
    showToast('success', `已导出未匹配行清单（作业 ${job.job_no}，共 ${job.row_count} 行）`)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  } finally {
    exportingUnmatchedRows.value = false
  }
}

// --- 参与口径（V1.2 第 8 期，§18.10）---

async function loadParticipation(task: AssessmentTaskItem) {
  participationError.value = ''
  try {
    participation.value = await getTaskParticipation(task.id)
  } catch (err) {
    // 这一组的失败**不**接管弹层：下面的明细表是这一屏的主体，它自己有
    // `detailError`。所以这里只让上面那一栏退回到任务行上的数，并说一句为什么。
    participationError.value = err instanceof Error ? err.message : '参与口径读取失败'
  }
}

/**
 * 标记一名目标学生在**这一场**里该不该参加。
 *
 * 「答没答」与「该不该答」是两个维度，这个动作**一行答题事实都不动**——标记为请假
 * 只是让这一行不进应测名单（于是分母变小、完成率跟着变），他的卷子、答案、结果、
 * 档案一个不碰。
 *
 * 「原因」在服务端**只有非应测才必填**（`mark_target_participation` 里判的），而
 * `FormDialog` 的 `required` 是逐字段写死的、跟着字段走不跟着取值走。所以这里自己
 * 判一次：选「应测」是把它改回来，没有原因可写；选另外三档必须说出为什么——那句
 * 话是这场测评里「这个人为什么不算」的唯一答案（§16.10 的词表还是待确认项，
 * 所以落库的就是人写的这一句）。
 */
async function markParticipation(row: TaskCompletionItem) {
  const task = detailTask.value
  if (!task) return
  const values = await showFormDialog(
    `标记「${row.student_name}」在本场测评的参与状态`,
    [
      {
        key: 'disposition',
        label: '参与状态',
        type: 'select',
        required: true,
        defaultValue: row.participation_disposition || 'REQUIRED',
        options: [
          { value: 'REQUIRED', label: '应测（正常参加）' },
          { value: 'LEAVE', label: '请假' },
          { value: 'EXEMPT', label: '免测' },
          { value: 'EXCLUDED', label: '已排除' }
        ],
        hint: '标记为请假 / 免测 / 已排除之后，这个人不再计入应测人数，完成率的分母跟着变小。'
      },
      {
        key: 'reason',
        label: '原因',
        type: 'text',
        // 服务端那一列是 `String(128)`，前端先挡一道——超长时 MySQL 严格模式回的是一句
        // 英文的「Data too long for column 'disposition_reason'」，离原因隔着一个列名。
        maxLength: 128,
        defaultValue: row.disposition_reason || '',
        placeholder: '如：家长说明已请假一周',
        hint: '选「应测」时可以不填；选另外三项时必填——这一句是「他为什么不算」的唯一答案。'
      }
    ],
    '保存标记'
  )
  if (!values.disposition) return
  const reason = (values.reason || '').trim()
  if (values.disposition !== 'REQUIRED' && !reason) {
    showToast('error', '标记为请假 / 免测 / 已排除时必须填写原因')
    return
  }
  markingTarget.value = row.target_id
  try {
    await markTargetParticipation(task.id, row.target_id, {
      disposition: values.disposition,
      reason: reason || null
    })
    showToast('success', '参与状态已更新')
    // 参与状态一改，上面那一组数（应测 / 完成率）与这一行的参与列都变了——所以同一次里
    // 两个读都重取一遍，否则屏幕上那张卡还是改之前的数（§11：卡片与它指向的列表要同源）。
    // 明细那一侧走 `silent`：就地刷新，不把页签与搜索词一起重置。
    await Promise.all([loadCompletion(task, true), loadParticipation(task)])
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '标记失败')
  } finally {
    markingTarget.value = null
  }
}

// --- 目标学生（V1.2 第 1 期）---

/**
 * 弹层里两个页签。两个读**各有一个竞态守卫**，不是共用一个。
 *
 * 共用的话，切页签会取消另一个正在路上的请求，而它的 `finally` 因为 `isCurrent` 为假
 * 不再关 `loading`——那个页签从此停在「正在加载」。§14 那条「守卫是按页构造的」说的是
 * 别把两个不相干的页面串进一条序列，这里是同一件事换了个尺度：两个不相干的**读**。
 * （第 5 期加上第三个页签「未匹配行」，它自己有一个 `latestUnmatched`，理由同上。）
 */
const latestTargets = createLatestRequest()

const detailTab = ref<'completion' | 'targets' | 'unmatched'>('completion')
const targetRows = ref<TaskTargetItem[]>([])
/** `null` = 这场测评**没有**发放范围的记录，见 `TaskTargetList.scope_type` 的说明。 */
const targetScope = ref<string | null>(null)
const targetsLoading = ref(false)
const targetsError = ref('')
const targetQuery = ref('')

/** 与 `DETAIL_RENDER_LIMIT` 同一个理由与同一个出路（导出那条只覆盖完成明细）。 */
const TARGET_RENDER_LIMIT = 200

const filteredTargetRows = computed(() => {
  const q = targetQuery.value.trim().toLowerCase()
  if (!q) return targetRows.value
  return targetRows.value.filter((row) =>
    [row.student_no, row.student_name, row.grade, row.class_name, targetSourceLabel(row.target_source)]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(q))
  )
})
const visibleTargetRows = computed(() => filteredTargetRows.value.slice(0, TARGET_RENDER_LIMIT))
const targetHidden = computed(() => Math.max(0, filteredTargetRows.value.length - TARGET_RENDER_LIMIT))

async function loadTargets(task: AssessmentTaskItem) {
  const token = latestTargets.begin()
  targetsLoading.value = true
  // 取数之前先清空：换一场任务时，面板上不该留着上一场的人（§14）。
  targetRows.value = []
  targetScope.value = null
  targetQuery.value = ''
  targetsError.value = ''
  try {
    const data = await getTaskTargets(task.id)
    if (!latestTargets.isCurrent(token)) return
    targetRows.value = data.items
    targetScope.value = data.scope_type
  } catch (err) {
    if (!latestTargets.isCurrent(token)) return
    targetsError.value = err instanceof Error ? err.message : '读取目标学生失败'
  } finally {
    if (latestTargets.isCurrent(token)) targetsLoading.value = false
  }
}

// --- 未匹配行（V1.2 第 5 期，§18.10）---

/**
 * 「这场测评还有谁缺着」——一份文件里有几行没进去，那些行从此有了一个可查的地方。
 *
 * 第三个读，也**有自己那一个竞态守卫**（理由见 `latestTargets`：三个不相干的读，
 * 共用一个序号会让切页签把另一个请求的 `finally` 判成过期，那个页签从此停在加载中）。
 */
const latestUnmatched = createLatestRequest()

const unmatchedRows = ref<AssessmentImportRow[]>([])
/** **你看得见**的行数（服务端按数据范围过滤过）——与下面那个数**口径不同**。 */
const unmatchedVisibleTotal = ref(0)
/**
 * **整场任务**各档各有多少行（不套读者的数据范围）。
 *
 * 两个数在这一屏上都要写出来、各自标明口径（§9）：这一页回答的是「这场普查组织得
 * 怎么样」，缩进读者的范围会让只带一个班范围的老师以为问题比实际小得多；而逐行明细
 * 带着姓名与班级，必须跟着范围缩（本页与 `/targets` 同一形状的双门槛）。后端
 * `unmatched_reason_counts` 的 docstring 里记着同一条，两处不许各改一处。
 */
const unmatchedReasonCounts = ref<Record<string, number>>({})
const unmatchedLoading = ref(false)
const unmatchedError = ref('')
const unmatchedQuery = ref('')

/** 整场没进得去的行数：按原因分的那几个数加起来（它就是那几档的和，不是另一次查询）。 */
const unmatchedAllCount = computed(() =>
  Object.values(unmatchedReasonCounts.value).reduce((sum, count) => sum + count, 0)
)

/**
 * 这一屏**不做客户端截断**：服务端已经封顶 200 行（`UNMATCHED_ROW_LIMIT`），而在这里
 * 再切一刀会让「仅显示前 N 行」出现两个各自为政的 N。截断了照样要说出来——判据是
 * `items.length` 小于 `total`，而那个 N 就从 `items.length` 现取，不在前端写死 200。
 */
const unmatchedHidden = computed(() =>
  Math.max(0, unmatchedVisibleTotal.value - unmatchedRows.value.length)
)

const filteredUnmatchedRows = computed(() => {
  const q = unmatchedQuery.value.trim().toLowerCase()
  if (!q) return unmatchedRows.value
  // 搜的是**这一屏上有的东西**：文件里印的字（`raw_*`，认得出「这一行为什么这么判」
  // 靠的就是它们）、相匹配到的学号姓名，以及那句话本身（「说明」那一格）。
  return unmatchedRows.value.filter((row) =>
    [
      row.batch_no,
      String(row.row_no),
      row.raw_name,
      row.raw_grade_name,
      row.raw_class_name,
      row.matched_student_no,
      row.matched_name,
      unmatchedReasonLabel(row.match_status),
      row.message
    ]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(q))
  )
})

async function loadUnmatched(task: AssessmentTaskItem) {
  const token = latestUnmatched.begin()
  unmatchedLoading.value = true
  // 取数之前先清空：换一场任务时不该留着上一场那几行（§14）。
  unmatchedRows.value = []
  unmatchedVisibleTotal.value = 0
  unmatchedReasonCounts.value = {}
  unmatchedQuery.value = ''
  unmatchedError.value = ''
  try {
    const data = await getUnmatchedImportRows(task.id)
    if (!latestUnmatched.isCurrent(token)) return
    unmatchedRows.value = data.items
    unmatchedVisibleTotal.value = data.total
    unmatchedReasonCounts.value = data.reasonCounts
  } catch (err) {
    if (!latestUnmatched.isCurrent(token)) return
    unmatchedError.value = err instanceof Error ? err.message : '读取未匹配的行失败'
  } finally {
    if (latestUnmatched.isCurrent(token)) unmatchedLoading.value = false
  }
}

// --- 补发：先看后补（§8.1 / §16.2）---

const showSupplement = ref(false)
const supplementReason = ref('')
const supplementPreview = ref<SupplementPreview | null>(null)
const supplementBusy = ref(false)

async function startSupplement() {
  const task = detailTask.value
  if (!task) return
  const values = await showFormDialog(
    '补发目标学生',
    [
      {
        key: 'reason',
        label: '补发原因',
        type: 'text',
        required: true,
        placeholder: '如：开学后转学补入'
      }
    ],
    '查看将补发哪些人'
  )
  if (!values.reason) return
  supplementBusy.value = true
  try {
    // 这一步**一行都不写**：它只回答「会补哪些人」。据此弹出来的那份名单是要给人
    // 过目的，不是一份提交凭据——确认时服务端会重新算一遍（见 api.ts 的说明）。
    supplementPreview.value = await supplementTaskTargets(task.id, {
      reason: values.reason,
      confirm: false
    })
    supplementReason.value = values.reason
    showSupplement.value = true
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '读取补发名单失败')
  } finally {
    supplementBusy.value = false
  }
}

async function confirmSupplement() {
  const task = detailTask.value
  if (!task || !supplementPreview.value) return
  supplementBusy.value = true
  try {
    const result = await supplementTaskTargets(task.id, {
      reason: supplementReason.value,
      confirm: true
    })
    showSupplement.value = false
    // 报的是**服务端说补了几个**，不是刚才预览的那几个人数。两者理论上可能不同
    // （预览之后名册上又转进来一个学生，他会被一起补进去），所以这句话必须拿
    // 实际结果说，不能拿界面手上的那份预览说。
    showToast('success', `已补发 ${result.added} 名学生`)
    await loadTargets(task)
    await refreshTaskRow(task.id)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '补发失败')
  } finally {
    supplementBusy.value = false
  }
}

/**
 * 补发之后静默刷新那一行。
 *
 * 悄悄刷新而**不走 `load()`**：`load()` 会打开骨架屏，而弹层还开着——用户会看到
 * 背景那一页闪一下。要更新的只有「发放人数 / 完成率」这几个数，而它们都在这一行上。
 */
async function refreshTaskRow(taskId: number) {
  try {
    const fresh = await getAssessmentTasks()
    tasks.value = fresh
    const updated = fresh.find((task) => task.id === taskId)
    // 摘要那四格读的是 `detailTask`，所以它也要一起换掉，否则补发之后弹层里
    // 仍然写着补发前的发放人数。
    if (updated) detailTask.value = updated
  } catch (err) {
    // 不吞：补发是成了的，但屏幕上那几个数会停在补发前——那正是「指标卡与它指向的
    // 列表各说各话」（§11），得让人知道要重来一次。
    showToast('error', '补发已成功，但列表刷新失败，请刷新页面查看最新的完成率')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">测评组织</div>
        <h1>测评任务</h1>
        <p class="page-desc">创建、发布、暂停和跟踪任务完成情况。</p>
      </div>
      <div class="actions" v-if="canWrite">
        <button class="btn primary" @click="newTask">新建任务</button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="table" :rows="3" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <div class="card" v-if="!loading && !error">
      <div class="card-body">
        <DataTable
          :columns="columns"
          :rows="tasks"
          row-key="id"
          :page-size="10"
          empty-text="暂无测评任务"
        >
          <template #name="{ row }">
            <strong>{{ row.name }}</strong>
            <!-- 批次任务与系统内任务混在同一张表里，长得也一样。不标出来的话，
                 「5 / 5 完成」会让人以为这批学生在本系统里答过题。 -->
            <span v-if="row.source === 'IMPORTED'" class="pill" style="margin-left:6px">
              {{ sourceLabel(row.source) }}
            </span>
            <div class="muted tiny">{{ row.task_no }}</div>
          </template>
          <template #scope_type="{ row }">{{ scopeTypeLabel(row.scope_type) }}</template>
          <template #start_at="{ row }">
            {{ row.start_at?.slice(0, 10) || '—' }} — {{ row.end_at?.slice(0, 10) || '—' }}
          </template>
          <template #completion_rate="{ row }">
            <div style="min-width:145px">
              <div class="progress"><i :style="{ width: `${row.completion_rate}%` }"></i></div>
              <div class="muted tiny" style="margin-top:4px">{{ row.completed_targets }} / {{ row.total_targets }}</div>
            </div>
          </template>
          <template #status="{ row }">
            <span :class="['pill', statusTone(row.status)]">{{ taskStatusLabel(row.status) }}</span>
          </template>
          <template #actions="{ row }">
            <button class="btn small" @click="taskDetail(row)">查看明细</button>
            <button v-if="canWrite" class="btn small" style="margin-left:6px" @click="editTask(row)">编辑</button>
          </template>
        </DataTable>
      </div>
    </div>

    <FormDialog
      :open="showForm"
      :title="formTitle"
      :fields="formFields"
      :submit-text="formSubmitText"
      @submit="onFormSubmit"
      @cancel="onFormCancel"
      @update:open="showForm = $event"
    />

    <Modal
      :model-value="showDetail"
      :title="detailTask ? `测评完成明细 · ${detailTask.name}` : '测评完成明细'"
      size="lg"
      @update:model-value="showDetail = $event"
    >
      <template v-if="detailTask">
        <!-- 六格是 §18.10 那条算式的展开，**不是**把任务行上那三个数搬下来。
             任务行上的 `total_targets` / `completion_rate` 是**读者范围内**的
             （`list_assessment_tasks` 传了 scope），而这六格是**整场**的
             （`/participation` 不套范围）。一个弹层里两组数混着两个口径，读到的人
             只会得出一个错结论，所以这里六格全取整场那一组，下面写明白。 -->
        <div class="detail-grid">
          <div class="detail-row">
            <span>发放人数</span><b>{{ participation ? participation.total_targets : '—' }}</b>
          </div>
          <div class="detail-row">
            <span>请假 / 免测 / 已排除</span><b>{{ participation ? participation.excluded_targets : '—' }}</b>
          </div>
          <div class="detail-row">
            <span>应测人数</span><b>{{ participation ? participation.expected_targets : '—' }}</b>
          </div>
          <div class="detail-row">
            <span>已完成</span><b>{{ participation ? participation.completed_targets : '—' }}</b>
          </div>
          <div class="detail-row">
            <span>有效完成率</span><b>{{ participation ? `${participation.completion_rate}%` : '—' }}</b>
          </div>
          <div class="detail-row">
            <span>未匹配导入记录</span><b>{{ participation ? participation.unimported_records : '—' }}</b>
          </div>
        </div>
        <!-- 口径要写进界面（§9），而且这一处尤其要：上面写着「发放人数 300」、
             下面「完成明细」那张表却只有 40 行，不写明就是两个数互相打脸。
             最后那句「未匹配导入记录」是**不进**完成率的：那批行从来不在目标行里
             （任务外、重复、未匹配、冲突的导入记录），所以它是补充信息，不是分子或分母。 -->
        <p class="muted tiny" style="margin:8px 0 0">
          以上是<strong>整场测评</strong>的口径，不随你的数据范围变化：<strong>应测人数</strong> =
          发放人数 − 请假 − 免测 − 已排除，<strong>有效完成率</strong> = 已完成 ÷ 应测人数。
          下面各页签的名单按你的数据范围过滤，所以行数可能少于上面的人数。
          未匹配的导入记录从来不在目标行里，不计入完成率。
        </p>
        <!-- 这六格读不出来时要说出来（§14）：一排 `—` 与「这场测评一个人都没有」
             长得一模一样，而后者是一句会让人停掉整个普查的话。表格与页签不受影响，
             所以这一句只说明它自己那一块。 -->
        <p v-if="participationError" class="muted tiny" style="margin:6px 0 0">
          上面那一组数没读出来（{{ participationError }}）。
          <button class="btn small" type="button" @click="detailTask && loadParticipation(detailTask)">
            重试
          </button>
        </p>
        <!-- 三个页签回答三个问题：这一批人这次测出了什么（完成明细）／这场测评发给了谁
             （目标学生）／哪些行没能进得来（未匹配行）。分页签而不是并排三块，是因为
             它们在同一个 340px 高的窗口里，并排之后每一块都要滚动。 -->
        <div class="tabs">
          <!-- 这一枚页签只长在**读得到逐人心理明细**的人身上（2026-09-20，缺口 12）。
               它逐行印出关注等级与 MHT总分，所以服务端在那一天给它加了
               `STUDENT_PSYCH_DETAIL: {SCOPED}`；德育领导读这一页只会拿到 403。
               页签**收起来**而不是「留着、点下去报错」：一枚必然失败的页签不是一种提示，
               它是一处空白——用户会以为完成明细没数据，而不是以为自己不该看。
               下面紧跟的那一句 `v-if="!canReadDetail"` 就是把这件事说出来的地方
               （§14「空态是一句关于数据的话」的反面：这里没有空态可以说，所以要有话说）。 -->
          <button
            v-if="canReadDetail"
            :class="['tab', { active: detailTab === 'completion' }]"
            @click="detailTab = 'completion'"
          >
            完成明细
          </button>
          <button
            :class="['tab', { active: detailTab === 'targets' }]"
            @click="detailTab = 'targets'"
          >
            目标学生
          </button>
          <!-- 页签上**不带数字**是有意的：这一页上那个「整场 N 行」与下面列表的行数
               口径不同（一个是整场任务、一个随读者范围缩），而页签上那一格没有地方
               写口径——一个没有口径的数字会冒充「你能看到这么多」。两个数都写在
               面板里，各自带着自己的那半句话。 -->
          <button
            :class="['tab', { active: detailTab === 'unmatched' }]"
            @click="detailTab = 'unmatched'"
          >
            未匹配行
          </button>
        </div>

        <!-- 收起来的页签要有一句解释（§17：灰掉的按钮必须说得出为什么）。
             这句话不是道歉，它是一条**口径声明**：读它的人问的是「我为什么在这里
             看不到人」，而答案是他的能力集是学校级聚合与摘要。
             同时说出**替代落点**——不然这一句读起来就是「你也别想看了」：
             这一场测评的完成率与应测口径在旁边那两个页签里（六个数是纯计数），
             按年级看全校的完成情况在「统计分析」页。 -->
        <p v-if="!canReadDetail" class="muted tiny" style="margin:8px 0 0">
          完成明细逐行给出学号、姓名与关注等级，所以它归心理老师这一档，这里不再列出。
          上面那六格（发放 / 应测 / 已完成 / 有效完成率）不受影响，
          「目标学生」与「未匹配行」两个页签照常可查。
        </p>

        <template v-if="detailTab === 'targets'">
          <!-- 范围那一句是**口径声明**（§9）：读它的人和读「全部学生」页头那一句的人
               问的是同一个问题——这份名单覆盖到哪一层。没有记录时说的是「未记录」，
               **不是**「全校」：外部导入的批次任务在 `assessment_task` 那一列上写着
               SCHOOL，而它从来不是发给全校的（它是照着一份文件建的）。 -->
          <p class="muted tiny" style="margin-bottom:10px">
            发放范围：<b>{{ targetScope ? scopeTypeLabel(targetScope) : '未记录发放范围' }}</b>
            <span v-if="!targetScope">（这场测评建于「发放范围」这一栏存在之前）</span>
          </p>
          <p v-if="targetsLoading">正在加载目标学生</p>
          <ErrorState
            v-else-if="targetsError"
            :message="targetsError"
            :on-retry="() => detailTask && loadTargets(detailTask)"
          />
          <template v-else>
            <div class="toolbar" style="justify-content:space-between">
              <div class="search-box">
                <input v-model="targetQuery" type="search" placeholder="按学号、姓名、年级或班级筛选" />
              </div>
              <button
                v-if="canWrite"
                class="btn small"
                :disabled="supplementBusy"
                @click="startSupplement"
              >
                {{ supplementBusy ? '正在读取…' : '补发学生' }}
              </button>
            </div>
            <p
              v-if="targetQuery && !filteredTargetRows.length"
              class="muted tiny"
              style="margin-top:8px"
            >
              没有匹配「{{ targetQuery }}」的记录，共 {{ targetRows.length }} 条。
            </p>
            <div class="table-wrap" style="margin-top:14px;max-height:340px">
              <table>
                <thead>
                  <tr>
                    <th>学号</th><th>学生</th><th>年级</th><th>班级</th><th>性别</th><th>年龄</th>
                    <th>状态</th><th>来源</th><th>发放时间</th><th>完成时间</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in visibleTargetRows" :key="row.student_no">
                    <td>{{ row.student_no }}</td>
                    <td>{{ row.student_name }}</td>
                    <td>{{ row.grade }}</td>
                    <td>{{ row.class_name }}</td>
                    <td>{{ genderLabel(row.gender) }}</td>
                    <td>{{ row.age ?? '—' }}</td>
                    <td>
                      <span :class="['pill', targetStatusTone(row.status)]">{{
                        targetStatusLabel(row.status)
                      }}</span>
                    </td>
                    <!-- 「本来就在名单上」与「后来补进来的」是两个数：一份完成率报表里
                         把补发的算成原始目标，会让「这场普查的应答率」看起来比实际高。 -->
                    <td>{{ targetSourceLabel(row.target_source) }}</td>
                    <td>{{ row.assigned_at?.slice(0, 10) || '—' }}</td>
                    <td>{{ row.completed_at?.slice(0, 10) || '—' }}</td>
                  </tr>
                  <tr v-if="!visibleTargetRows.length && !targetQuery">
                    <td colspan="10"><div class="empty">暂无目标学生</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="targetHidden" class="muted tiny" style="margin-top:8px">
              仅显示前 {{ TARGET_RENDER_LIMIT }} 条，共 {{ filteredTargetRows.length }} 条。用上面的筛选缩小范围。
            </p>
          </template>
        </template>

        <p v-else-if="detailLoading" style="margin-top:14px">正在加载明细</p>
        <!-- 读失败就走这一支，**不落到**下面那个空表行上：`暂无完成明细` 是一句关于
             数据的话，用在读取失败上等于把一次故障说成一场空考试。 -->
        <ErrorState
          v-else-if="detailError"
          :message="detailError"
          :on-retry="() => detailTask && loadCompletion(detailTask)"
        />
        <template v-else>
          <!-- 一千多行的明细要能搜。这一屏只有 340px 高，靠滚动找人是找不到的。
               这个筛选框走全站的 `.search-box > input`（审计日志 / 重点学生 /
               账号与权限三处搜索框同形），不自己写一套——此前它是一个裸
               `<input type="search">` 加一句 `min-width`，拿到的是**浏览器默认样式**：
               28px 高、`2px inset #767676` 的灰框、直角、`1px 2px` 的内边距，
               与同一行里 34px 的「导出CSV」对不齐，也与一屏之外满页的圆角输入框不是一套。
               （`type="search"` 留着：它在 Chromium 上不画任何东西——量过了，同一个
               `.search-box` 下 `type=search` 与 `type=text` 逐像素相同——而
               `e2e/app.spec.ts` 的「明细能按学生筛选」正是靠 `.modal-panel input[type=search]`
               定位这个框的。）
               外层从 `class="actions"` 换成 `.toolbar`：`.actions` 只在
               `.page-head` / `.question-foot` 两个限定选择器下有规则，**没有基础规则**，
               所以此前那句 `justify-content` 与垂直对齐一直是空转的（输入框和按钮
               靠 inline 基线凑在一起）。`.toolbar` 才是全站那个 flex 行。 -->
          <div class="toolbar" style="margin-top:14px;justify-content:space-between">
            <div class="search-box">
              <input v-model="detailQuery" type="search" placeholder="按学号、姓名、年级或班级筛选" />
            </div>
            <div class="toolbar">
              <!-- 两份文件的差别是「导出来干什么」：明细是**这一场测出了什么**（结果口径，
                   给档案用），未参与名单是**还有谁要催**（名单口径，给班主任催交用）。
                   两个按钮并排，因为在这一屏上它们是同一个动作的两个对象。
                   **两枚的判据都是 `canReadDetail`，不是 `canWrite`**（2026-09-20）。
                   它们后端走的都是 `ensure_detail_exporter`（受控导出 + 逐人心理详情），
                   而「导出未参与名单」此前挂着 `canWrite`——默认矩阵下这两个值恰好一样，
                   所以那处不一致一直没露出来；判据按**问题**取名（这一枚问的是
                   「能不能导出逐人明细」），矩阵一改就不会有谁悄悄比另一个宽一档。
                   完成明细那一枚（`导出CSV`）**此前没有 `v-if`**，只有 `:disabled`——
                   缺口 12 的门加上去之后它就成了领导手上的一枚必然 403 的按钮。 -->
              <button
                v-if="canReadDetail"
                class="btn small"
                :disabled="exportingNonParticipants"
                @click="exportNonParticipants"
              >
                {{ exportingNonParticipants ? '正在导出…' : '导出未参与名单' }}
              </button>
              <button
                v-if="canReadDetail"
                class="btn small"
                :disabled="exportingDetail"
                @click="exportDetail"
              >
                {{ exportingDetail ? '正在导出…' : '导出CSV' }}
              </button>
            </div>
          </div>
          <p v-if="detailQuery && !filteredDetailRows.length" class="muted tiny" style="margin-top:8px">
            没有匹配「{{ detailQuery }}」的记录，共 {{ detailRows.length }} 条。
          </p>
          <div class="table-wrap" style="margin-top:14px;max-height:340px">
            <table>
              <thead>
                <tr>
                  <th>学号</th><th>学生</th><th>年级</th><th>班级</th><th>性别</th><th>年龄</th>
                  <th>状态</th><th>参与</th><th>关注等级</th><th>总分</th><th>完成时间</th><th>用时</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in visibleDetailRows" :key="row.student_no">
                  <td>{{ row.student_no }}</td>
                  <td>{{ row.student_name }}</td>
                  <td>{{ row.grade }}</td>
                  <td>{{ row.class_name }}</td>
                  <td>{{ genderLabel(row.gender) }}</td>
                  <td>{{ row.age ?? '—' }}</td>
                  <td>
                    <span :class="['pill', targetStatusTone(row.status)]">{{
                      targetStatusLabel(row.status)
                    }}</span>
                  </td>
                  <!-- 「参与」与左边那一格「状态」是两个问题，所以是两个控件而不是
                       一个控件两种颜色：状态说**他做完没有**，参与说**他该不该做**。
                       一名请假的学生在这一行上是「未开始 · 请假」——两句话同时为真，
                       而完成率只把他算进分母之外。 -->
                  <td>
                    <span :class="['pill', participationTone(row.participation_disposition)]">{{
                      participationLabel(row.participation_disposition)
                    }}</span>
                    <div v-if="row.disposition_reason" class="muted tiny">
                      {{ row.disposition_reason }}
                    </div>
                    <!-- 标记按钮就长在被标记的那一格旁边，不另开一列「操作」：
                         这一张表已经有十二列，多一列只为放一个按钮，横向就要滚。 -->
                    <button
                      v-if="canWrite"
                      class="btn small"
                      type="button"
                      :disabled="markingTarget !== null"
                      @click="markParticipation(row)"
                    >
                      {{ markingTarget === row.target_id ? '保存中…' : '标记' }}
                    </button>
                  </td>
                  <!-- 这两列是**这一场**的结果，不是每人最近一场：同一名学生出现在两个
                       批次里时，两边各自显示各自的分（CLAUDE.md §11 的「按场」口径）。 -->
                  <td>
                    <span :class="['pill', levelTone(row.total_level)]">{{
                      levelLabel(row.total_level)
                    }}</span>
                  </td>
                  <td>{{ row.total_score ?? '—' }}</td>
                  <td>{{ row.completed_at || '—' }}</td>
                  <td>{{ formatDuration(row.duration_seconds) }}</td>
                </tr>
                <tr v-if="!visibleDetailRows.length && !detailQuery">
                  <td colspan="12"><div class="empty">暂无完成明细</div></td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- 截断自己说出来，并指向那条出路。学校看到「只显示前 200 条」时会去找剩下的，
               而剩下的就是「导出CSV」那一份 —— 出路不写在这里，读者会以为数据丢了。 -->
          <p v-if="detailHidden" class="muted tiny" style="margin-top:8px">
            仅显示前 {{ DETAIL_RENDER_LIMIT }} 条，共 {{ filteredDetailRows.length }} 条。用上面的筛选缩小范围，或「导出CSV」取完整名单。
          </p>
        </template>

        <!-- 未匹配行（V1.2 第 5 期）。**这一个块排在最后**，不是顺手：上面那三条
             `v-if` / `v-else-if` / `v-else` 是一条链，往链条中间插一个带 `v-if` 的块，
             后面那几个 `v-else-if` 就会改挂到这个新块上——完成明细从此再也不显示，
             而报错、看不出是排版问题。 -->
        <template v-if="detailTab === 'unmatched'">
          <p v-if="unmatchedLoading" style="margin-top:14px">正在加载未匹配的行</p>
          <ErrorState
            v-else-if="unmatchedError"
            :message="unmatchedError"
            :on-retry="() => detailTask && loadUnmatched(detailTask)"
          />
          <template v-else>
            <!-- 两个数**口径不同**，所以分开写、各自带着自己那半句话（§9）：
                 整场那个数不套读者的范围（「这场普查还缺谁」是监督口径），
                 而下面那张表逐行给出姓名与班级，必须随范围缩。 -->
            <p class="muted tiny" style="margin-bottom:10px">
              整场共 <b>{{ unmatchedAllCount }}</b> 行没进得去<template v-if="unmatchedAllCount">（<span
                v-for="(count, code, index) in unmatchedReasonCounts"
                :key="code"
              ><span v-if="index">、</span>{{ unmatchedReasonLabel(code) }} {{ count }} 行</span>）</template>；其中你看得见 {{ unmatchedVisibleTotal }} 行。
            </p>
            <div class="toolbar" style="justify-content:space-between">
              <div class="search-box">
                <input v-model="unmatchedQuery" type="search" placeholder="按批次、行号、姓名、年级或班级筛选" />
              </div>
              <!-- 这枚按钮的可见性与上面「导出未参与名单」同一条判据（2026-09-20 起叫
                   `canReadDetail`，此前写的是 `canWrite`——两者默认矩阵下同值），
                   而那是**导出侧**那两道门的结果，不是这一屏读者那道门：德育领导读得
                   这一屏（任务读者 + 组织与账号的 只读汇总），却导不出这份文件——
                   逐行印着文件里的姓名，而他的心理详情是 `SUMMARY`。
                   所以按钮的判据与列表的判据**本来就不同**。
                   没有行时置灰，而灰掉的理由就在同一屏上（下面那句「没有进不去的导入行」）。 -->
              <button
                v-if="canReadDetail"
                class="btn small"
                :disabled="exportingUnmatchedRows || !unmatchedAllCount"
                @click="exportUnmatchedRows"
              >
                {{ exportingUnmatchedRows ? '正在导出…' : '导出未匹配行清单' }}
              </button>
            </div>
            <p
              v-if="unmatchedQuery && !filteredUnmatchedRows.length"
              class="muted tiny"
              style="margin-top:8px"
            >
              没有匹配「{{ unmatchedQuery }}」的记录，共 {{ unmatchedRows.length }} 行。
            </p>
            <div class="table-wrap" style="margin-top:14px;max-height:340px">
              <table>
                <thead>
                  <tr>
                    <th>批次</th><th>行号</th><th>文件里的姓名</th><th>年级</th><th>班级</th>
                    <th>匹配结论</th><th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  <!-- `row_no` 是**批内**编号，所以「批次」这一列不是装饰：一场任务下
                       可以有好几批（先初一、隔几天再初二），少了它，看表的人会以为
                       那两行 2 是重了或者丢了一行。 -->
                  <tr v-for="row in filteredUnmatchedRows" :key="row.id">
                    <td>{{ row.batch_no || '—' }}</td>
                    <td>{{ row.row_no }}</td>
                    <td>{{ row.raw_name || '—' }}</td>
                    <td>{{ row.raw_grade_name || '—' }}</td>
                    <td>{{ row.raw_class_name || '—' }}</td>
                    <td>
                      <span :class="['pill', unmatchedReasonTone(row.match_status)]">{{
                        unmatchedReasonLabel(row.match_status)
                      }}</span>
                    </td>
                    <!-- 「说明」那一格渲染的是服务端拼好的**一句话**（`row.message`），
                         不是若干个半句拼起来的：这一行为什么没进去、下一步做什么，
                         都在那一句里（`_row_message`）。 -->
                    <td>{{ row.message || '—' }}</td>
                  </tr>
                  <tr v-if="!filteredUnmatchedRows.length && !unmatchedQuery">
                    <td colspan="7"><div class="empty">没有进不去的导入行</div></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="unmatchedHidden" class="muted tiny" style="margin-top:8px">
              仅显示前 {{ unmatchedRows.length }} 行，共 {{ unmatchedVisibleTotal }} 行（最近的批次排在前面）。
              把上面这几批处理干净之后，更早那几批的行会跟着浮上来。
            </p>
            <p class="muted tiny" style="margin-top:8px">
              「说明」那一列写着每一行为什么没进去、下一步该做什么。照它处理完（补名册、补发目标学生），
              回「数据中心」把**同一份文件**重传一次——同一份文件会复用这一批，逐行结论整批重算。
            </p>
          </template>
        </template>
      </template>
    </Modal>

    <!-- 补发的第二步：先看后补。这一步**还没有写任何东西**，按钮上那句「确认补发」
         才是分界线——所以取消之后库里的目标行与补发前一行不差。 -->
    <Modal
      :model-value="showSupplement"
      title="补发目标学生"
      @update:model-value="showSupplement = $event"
    >
      <template v-if="supplementPreview">
        <!-- 「一个都补不了」是一条关于数据的话，要自己说。此前这两种情形共用一句
             「将新增 N 名学生」，于是 N=0 时屏幕上写着「将新增 0 名学生」，而紧接着
             那行又说「没有可补发的人」——**同一屏上两句话各说各的**，第一句还读起来
             像一件真会发生的事（§14：空态是一句关于数据的话，不是一次变更的占位）。 -->
        <template v-if="supplementPreview.candidates.length">
          <p>
            将新增 <b>{{ supplementPreview.total }}</b> 名学生（补发前 {{ supplementPreview.before }} 人）。
          </p>
          <p class="muted tiny" style="margin-top:6px">原因：{{ supplementReason }}</p>
          <div class="table-wrap" style="margin-top:14px;max-height:260px">
            <table>
              <thead>
                <tr><th>学号</th><th>学生</th><th>年级</th><th>班级</th></tr>
              </thead>
              <tbody>
                <tr v-for="candidate in supplementPreview.candidates" :key="candidate.student_no">
                  <td>{{ candidate.student_no }}</td>
                  <td>{{ candidate.student_name }}</td>
                  <td>{{ candidate.grade }}</td>
                  <td>{{ candidate.class_name }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- 截断了要说出来，而且要说清**补发的仍是全部**：这一屏只列前 200 条，读者看见
               「将新增 350 人」而屏幕上是 200 行，很容易以为下面那句是「只补这 200 个」。 -->
          <p v-if="supplementPreview.truncated" class="muted tiny" style="margin-top:8px">
            仅列出前 {{ supplementPreview.limit }} 人，确认后补发的是全部
            {{ supplementPreview.total }} 人。
          </p>
        </template>
        <p v-else class="muted">这场测评已经把范围内所有在读学生都发出去了，没有可补发的人。</p>
      </template>
      <template #footer>
        <button class="btn" @click="showSupplement = false">取消</button>
        <button
          class="btn primary"
          :disabled="supplementBusy || !supplementPreview?.candidates.length"
          @click="confirmSupplement"
        >
          {{ supplementBusy ? '正在补发…' : '确认补发' }}
        </button>
      </template>
    </Modal>

  </div>
</template>
