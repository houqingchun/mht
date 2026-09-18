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
  TASK_STATUS_LABELS,
  TASK_STATUS_ORDER,
  genderLabel,
  labelOf,
  levelLabel,
  levelTone,
  scopeTypeLabel,
  sourceLabel,
  targetStatusLabel,
  targetStatusTone
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
  downloadTaskCompletionCsv,
  type AssessmentTaskItem,
  type TaskCompletionItem
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
// 正是 §3 说的「不得在各视图里重复定义」。映射回唯一的那一层。
function statusLabel(status: string) {
  return labelOf(TASK_STATUS_LABELS, status)
}

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
 * （`GET /assessment-tasks/{id}/completion/export` 一直都在，界面此前没有入口）。
 * **截断必须自己说出来并指向那条出路**，否则就是静默丢数据。
 */
const DETAIL_RENDER_LIMIT = 200
const detailQuery = ref('')
const exportingDetail = ref(false)

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

async function taskDetail(task: AssessmentTaskItem) {
  const token = latest.begin()
  detailTask.value = task
  showDetail.value = true
  detailLoading.value = true
  detailRows.value = []
  detailQuery.value = ''
  detailError.value = ''
  try {
    const rows = await getTaskCompletion(task.id)
    // 关了 A 的明细、马上点开 B：A 的那一份晚到也不能落到 B 的表上——
    // 弹层标题写着 B，行却是 A 的人（2026-09-17 补，见 `services/latest-request.ts`）。
    if (!latest.isCurrent(token)) return
    detailRows.value = rows
  } catch (err) {
    if (!latest.isCurrent(token)) return
    // 不再弹 toast：这一条要留在弹层里（见 `detailError`），飘一条同样的句子
    // 会让人以为它们是两件事。
    detailError.value = err instanceof Error ? err.message : '读取完成明细失败'
  } finally {
    if (latest.isCurrent(token)) detailLoading.value = false
  }
}

async function exportDetail() {
  const task = detailTask.value
  if (!task) return
  exportingDetail.value = true
  try {
    await downloadTaskCompletionCsv(task.id, `${task.task_no}-完成明细.csv`)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  } finally {
    exportingDetail.value = false
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
            <span :class="['pill', statusTone(row.status)]">{{ statusLabel(row.status) }}</span>
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
        <div class="detail-grid">
          <div class="detail-row"><span>发放人数</span><b>{{ detailTask.total_targets }}</b></div>
          <div class="detail-row"><span>已完成</span><b>{{ detailTask.completed_targets }}</b></div>
          <div class="detail-row"><span>未完成</span><b>{{ detailTask.total_targets - detailTask.completed_targets }}</b></div>
          <div class="detail-row"><span>完成率</span><b>{{ detailTask.completion_rate }}%</b></div>
        </div>
        <p v-if="detailLoading" style="margin-top:14px">正在加载明细</p>
        <!-- 读失败就走这一支，**不落到**下面那个空表行上：`暂无完成明细` 是一句关于
             数据的话，用在读取失败上等于把一次故障说成一场空考试。 -->
        <ErrorState
          v-else-if="detailError"
          :message="detailError"
          :on-retry="() => detailTask && taskDetail(detailTask)"
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
            <button class="btn small" :disabled="exportingDetail" @click="exportDetail">
              {{ exportingDetail ? '正在导出…' : '导出CSV' }}
            </button>
          </div>
          <p v-if="detailQuery && !filteredDetailRows.length" class="muted tiny" style="margin-top:8px">
            没有匹配「{{ detailQuery }}」的记录，共 {{ detailRows.length }} 条。
          </p>
          <div class="table-wrap" style="margin-top:14px;max-height:340px">
            <table>
              <thead>
                <tr>
                  <th>学号</th><th>学生</th><th>年级</th><th>班级</th><th>性别</th><th>年龄</th>
                  <th>状态</th><th>关注等级</th><th>总分</th><th>完成时间</th><th>用时</th>
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
                  <td colspan="11"><div class="empty">暂无完成明细</div></td>
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
      </template>
    </Modal>

  </div>
</template>
