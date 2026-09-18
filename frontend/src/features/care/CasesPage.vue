<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import {
  CASE_STATUS_ORDER,
  levelLabel,
  levelTone,
  LEVEL_ORDER,
  SOURCE_ORDER,
  sourceLabel,
  statusLabel,
  statusTone,
  userScopeLabel
} from '../../services/labels'
import {
  getMe,
  getCareCases,
  getStudentResults,
  exportCareCases,
  exportHighRiskCareCases,
  batchAssignOwner,
  getAssignableOwners,
  type AssignableOwner,
  type CareCaseItem,
  type CurrentUser,
  type StudentResultItem
} from '../../services/api'

const route = useRoute()
const router = useRouter()
const { settings } = useSettings()
const cases = ref<CareCaseItem[]>([])
const owners = ref<AssignableOwner[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')
const riskFilter = ref('all')
const queueFilter = ref('all')
const selected = ref<Set<number>>(new Set())

// The workbench metric tiles deep-link here with ?filter=<queue-tab key>.
// Validate against the known keys: an unrecognised value used to fall through
// to the "all" branch, which looked like the filter had simply been ignored.
// ?tab= is validated the same way, for the same reason.
onMounted(() => {
  const incoming = route.query.filter
  if (typeof incoming === 'string' && QUEUE_TABS.some(tab => tab.key === incoming)) {
    queueFilter.value = incoming
  }
  const incomingTab = route.query.tab
  if (typeof incomingTab === 'string' && TOP_TABS.some(tab => tab.key === incomingTab)) {
    activeTab.value = incomingTab
  }
})

const filtered = computed(() => {
  return cases.value.filter(c => {
    const matchQuery = !query.value || `${c.student_name}${c.student_no}${c.grade}${c.class_name}`
      .toLowerCase().includes(query.value.toLowerCase())
    const matchRisk = riskFilter.value === 'all' || c.total_level === riskFilter.value
    const matchQueue =
      queueFilter.value === 'all' ||
      (queueFilter.value === 'overdue' ? c.overdue === true : c.case_status === queueFilter.value)
    return matchQuery && matchRisk && matchQueue
  })
})

// ---------------------------------------------------------------------------
// 「全部学生」页签：名册 + 每人最近一场测评结果
//
// 与上面那个队列是**两个层次**，不要合并：队列回答「我手上有哪些案子要办」，
// 这一个回答「这名学生测出的是什么」——**没有案子的学生也在里面**。用户 2026-09-17
// 报的正是后者缺失：「当前没有一个视角查看所有学生的测试结果」，他刚导入的那批普查里
// 被判「一般观察」的学生一个都查不到，因为它们全都不会开档案，而工作台 / 重点学生 /
// 学生档案三页都以档案为入口（见 analytics_service.student_result_list 的注释）。
// ---------------------------------------------------------------------------

const TOP_TABS = [
  { key: 'cases', label: '重点学生' },
  { key: 'students', label: '全部学生' }
]

const activeTab = ref('cases')
const studentRows = ref<StudentResultItem[]>([])
/**
 * 初值是 **true**，不是 false：这一格在数据到位之前**不许**出现一张看起来是空的表。
 *
 * `ref(false)` 时，走到这一页签的头几帧里 `studentsLoading` 还是 false、`studentRows`
 * 还是空数组，于是那张表**连表头一起**渲染出来了（表头是可点的），随后
 * `loadStudents()` 把 `studentsLoading` 置真 → 骨架屏替换掉整张表 → 组件被重建、
 * 排序状态跟着没了。用户看到的是**一次没反应的点击**。
 * 这是 e2e 在整套里跑时才暴露的（单独跑时数据先到了）：`app.spec.ts` 那条枚举排序
 * 用例先点表头再断言，点在了这个窗口里，排序被静默丢掉（2026-09-17）。
 *
 * 同一页签上的工具条「N 人」也吃到这个初值的保护：加载中不报「0 人」——
 * 那个数会被读成「这所学校一个学生都没有」（§9 那条教训的同一形状）。
 * 队列那一半的 `loading` 一直是 `ref(true)`，这里此前是与它不一致的那一个。
 *
 * **这一条没有测试钉住**，是有意的：窗口只有几十毫秒，能钉住它的断言要么假绿
 * （在数据已经就位的机器上永远看不见那一帧）、要么自己就是 flaky 的。
 * e2e 那边改为**先等行出现再点表头**，那是测试本来的义务，不是这条修复的判据。
 */
const studentsLoading = ref(true)
const studentsError = ref('')
// 首次切到该页签才拉数据：默认路径不为它付代价，且权限被拒时只有这一个页签
// 显示红条，队列照常可用（那个端点是双门槛的，见 CLAUDE.md §4）。
let studentsLoaded = false

const studentQuery = ref('')
const studentGrade = ref('all')
const studentClass = ref('all')
const assessedOnly = ref(false)

/**
 * 这一页列的**不是全校**，是调用者数据范围内的学生（§9 的谓词在服务端加进 WHERE）。
 *
 * 口径要写在界面上，否则「全部学生」这四个字自己会把读者带向「全校」——
 * 一个只带 1 个班范围的心理老师会以为这所学校只有 40 个学生。§9 那句
 * 「范围数字必须在 UI 上写明口径，否则它冒充全校数字，比不给数字更糟」
 * 说的是同一件事，只是这次冒充全校的不是数字，是列表标题。
 *
 * `scopes` 一直在 `/auth/me` 的响应里，只是此前没有类型所以没人读（见 `api.ts`）。
 * 多行范围是并集（`or_`），所以并列出来；取不到就什么也不说，不猜一个「全校」。
 */
const me = ref<CurrentUser | null>(null)
const scopeText = computed(() => {
  const types = [...new Set((me.value?.scopes ?? []).map(s => s.scope_type))]
  if (!types.length) return ''
  return types.map(userScopeLabel).join('、')
})

/** 年级/班级的选项从**已加载的行**里派生——名册是这一页的数据源，不必再问一次接口。 */
const gradeOptions = computed(() => [...new Set(studentRows.value.map(r => r.grade))])
const classOptions = computed(() => [...new Set(studentRows.value.map(r => r.class_name))])

const filteredStudents = computed(() => {
  return studentRows.value.filter(r => {
    const matchQuery = !studentQuery.value ||
      `${r.student_name}${r.student_no}${r.grade}${r.class_name}`
        .toLowerCase().includes(studentQuery.value.toLowerCase())
    const matchGrade = studentGrade.value === 'all' || r.grade === studentGrade.value
    const matchClass = studentClass.value === 'all' || r.class_name === studentClass.value
    // 「已测评」= 有等级。用它来把「还没测」的那批摘出去，剩下的就是这一页真正
    // 要看的东西；不勾时全部列出（默认整个名册，用户明确要的口径）。
    const matchAssessed = !assessedOnly.value || r.total_level !== null
    return matchQuery && matchGrade && matchClass && matchAssessed
  })
})

const studentColumns: Column[] = [
  { key: 'student_no', label: '学号', sortable: true },
  { key: 'student_name', label: '学生', sortable: true },
  { key: 'grade', label: '年级', sortable: true },
  { key: 'class_name', label: '班级', sortable: true },
  { key: 'total_level', label: '关注等级', sortable: true, order: LEVEL_ORDER },
  { key: 'total_score', label: '总分', sortable: true, align: 'right' },
  { key: 'submitted_at', label: '最近测评', sortable: true },
  { key: 'source', label: '来源', sortable: true, order: SOURCE_ORDER },
  { key: 'case_status', label: '档案阶段', sortable: true, order: CASE_STATUS_ORDER },
  { key: 'actions', label: '操作' }
]

async function selectTab(key: string) {
  activeTab.value = key
  // 深链可分享（?tab=students），但默认页签不往地址栏里塞参数。
  const nextQuery = { ...route.query }
  if (key === TOP_TABS[0].key) {
    delete nextQuery.tab
  } else {
    nextQuery.tab = key
  }
  await router.replace({ query: nextQuery })
  if (key === 'students' && !studentsLoaded) {
    await loadStudents()
  }
}

async function loadStudents() {
  studentsLoading.value = true
  studentsError.value = ''
  try {
    studentRows.value = await getStudentResults()
    studentsLoaded = true
  } catch (err) {
    // 单独的错误状态：这一个页签被拒（403）或加载失败时，上面的队列仍然可用。
    studentsError.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    studentsLoading.value = false
  }
}

/** 只有已建档的学生才给链接：没有档案的行点进去是 404，而前端拿不到状态码（§2）。 */
function openStudentCase(studentId: number) {
  router.push(`/counselor/cases/${studentId}`)
}

/** 表格列定义：可排序列在表头点击切换升降序。 */
const columns: Column[] = [
  { key: 'student_name', label: '学生', sortable: true },
  { key: 'total_level', label: '关注等级', sortable: true, order: LEVEL_ORDER },
  { key: 'case_status', label: '当前阶段', sortable: true, order: CASE_STATUS_ORDER },
  { key: 'owner_name', label: '负责人', sortable: true },
  { key: 'next_follow_up_date', label: '下次跟进', sortable: true },
  { key: 'actions', label: '操作' }
]

// Queue tabs filter on the backend's status codes; 'overdue' is a derived flag.
// 关注等级筛选的三个码，从重到轻（与列表里药丸的严重程度顺序一致）。
// 中文一律由 `levelLabel` 出，这里只放码——`KEY_ATTENTION` 曾在这个下拉里
// 写死成「高度关注」，而同一屏每一行的药丸写的是「重点关注」，
// 筛选器和它筛出来的行给同一个等级两个名字（CLAUDE.md §3 那张表的第二面）。
const LEVEL_FILTER_CODES = ['KEY_ATTENTION', 'NEEDS_ATTENTION', 'GENERAL_RANGE']

const QUEUE_TABS = [
  { key: 'all', label: '全部' },
  { key: 'PENDING_REVIEW', label: '待复核' },
  { key: 'FOLLOWING', label: '跟进中' },
  { key: 'overdue', label: '已逾期' },
  { key: 'OBSERVING', label: '观察中' },
  { key: 'CLOSED', label: '已关闭' }
]


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
let confirmResolve: ((value: boolean) => void) | null = null

const showExportModal = ref(false)
const exportPurpose = ref('')
const exportHighRisk = ref(false)
// Consent is its own ref — binding it to exportPurpose (as before) overwrote the purpose value.
const exportConsent = ref(false)
const exportMask = ref('masked')
const exportFields = ref('minimum')

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

function showConfirmation(title: string, message: string, danger = false): Promise<boolean> {
  confirmTitle.value = title
  confirmMessage.value = message
  confirmDanger.value = danger
  showConfirm.value = true
  return new Promise((resolve) => { confirmResolve = resolve })
}

function onConfirm() {
  if (confirmResolve) confirmResolve(true)
}

function onCancelConfirm() {
  if (confirmResolve) confirmResolve(false)
}

async function load() {
  loading.value = true
  error.value = ''
  // 先清空再取（2026-09-17 补）：`load()` 不只在挂载时跑，批量分配成功之后也跑一次。
  // 那一次要是失败，旧列表会留在屏幕上与红条同屏——而它已经过期了：
  // 刚刚分配过的那几个人还挂着旧的负责人。看不到数据好过看一份错的。
  cases.value = []
  try {
    const currentUser = await getMe()
    if (currentUser.role_code !== 'counselor') {
      await router.push('/login')
      return
    }
    // 留着给「全部学生」那一页写范围口径用（见 `scopeText`）。
    me.value = currentUser
    cases.value = await getCareCases()
    try {
      owners.value = await getAssignableOwners()
    } catch {
      // Non-fatal: the assign dialog falls back to a free-text owner id.
      owners.value = []
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
  // 深链直接落到「全部学生」时也要拉一次。放在角色检查**之后**：非心理老师
  // 在上面就被弹回登录页了（那个 return 连这块一起跳过），不该先打一个必然 403
  // 的请求再跳转。
  //
  // 2026-09-17 修：这一段此前在**上面那个 try 里面**，于是 `getCareCases()` 一失败
  // 就永远走不到这里，`studentsLoading` / `studentsError` 保持初始值——
  // 切过去看到的是一张「没有符合条件的学生」的空表加一句「0 人」，
  // 而真正的失败写在另一个页签的红条里（那一条 `v-if` 带 `activeTab === 'cases'`）。
  // 两个页签各自拉自己的数据，一个失败不该让另一个连请求都不发。
  if (activeTab.value === 'students' && !studentsLoaded) {
    await loadStudents()
  }
}

function openDetail(studentId: number) {
  router.push(`/counselor/cases/${studentId}`)
}

function toggleSelect(studentId: number) {
  if (selected.value.has(studentId)) {
    selected.value.delete(studentId)
  } else {
    selected.value.add(studentId)
  }
}

function toggleSelectAll() {
  if (selected.value.size === filtered.value.length) {
    selected.value.clear()
  } else {
    filtered.value.forEach(c => selected.value.add(c.student_id))
  }
}

function clearSelection() {
  selected.value.clear()
  showToast('info', '已清除选择')
}

async function batchAssign() {
  if (selected.value.size === 0) {
    showToast('error', '请先选择至少一名学生')
    return
  }
  const values = await showFormDialog('批量分配负责人', [
    {
      key: 'owner_id',
      label: '负责人',
      type: 'select',
      required: true,
      placeholder: '请选择负责人',
      options: owners.value.map(o => ({ value: String(o.id), label: o.display_name }))
    }
  ], '确认分配')

  const ownerId = Number(values.owner_id)
  if (!ownerId) return

  try {
    const result = await batchAssignOwner([...selected.value], ownerId)
    showToast('success', `负责人已更新（${result.updated} 人）`)
    selected.value.clear()
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '批量分配失败')
  }
}

async function batchExport() {
  if (selected.value.size === 0) {
    showToast('error', '请先选择至少一名学生')
    return
  }
  openExport()
}

function openExport(highRisk = false) {
  exportHighRisk.value = highRisk
  exportPurpose.value = ''
  exportConsent.value = false
  exportMask.value = 'masked'
  exportFields.value = 'minimum'
  showExportModal.value = true
}

/**
 * Number of records the pending export covers — the selection, or the whole
 * cohort for high-risk.
 *
 * 高度关注那一边要**按人去重**：后端是逐学生写行的（`export_service.py:130-134`），
 * 而 `cases` 一行一份档案——一名学生可以既有已关闭的旧档案、又有在办的新档案
 * （CLAUDE.md §1 那条时序），按档案数会把这个数字报大。
 */
const exportCount = computed(() =>
  exportHighRisk.value
    ? new Set(
        cases.value.filter(c => c.total_level === 'KEY_ATTENTION').map(c => c.student_id)
      ).size
    : selected.value.size
)

async function confirmExport() {
  if (!exportPurpose.value.trim()) {
    showToast('error', '请选择导出用途')
    return
  }
  if (!exportConsent.value) {
    showToast('error', '请确认导出责任')
    return
  }
  try {
    const options = {
      purpose: exportPurpose.value,
      maskNames: exportMask.value === 'masked',
      includeScore: exportFields.value === 'score'
    }
    if (exportHighRisk.value) {
      await exportHighRiskCareCases(options)
    } else {
      await exportCareCases({ ...options, studentIds: [...selected.value] })
    }
    showToast('success', '受控导出已完成并记录审计')
    showExportModal.value = false
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
        <div class="eyebrow">学生关注中心</div>
        <h1>{{ activeTab === 'students' ? '全部学生测评结果' : '重点学生与长期跟踪' }}</h1>
        <!-- 口径写在页面上，不写读者就会把它当成别的东西：这一个列出**整个名册**，
             每人一行、取最近一场；未测评的行也在（§9 的同一条道理）。 -->
        <p v-if="activeTab === 'students'" class="page-desc">
          每人最近一场测评；未测评的学生也列出。导入的校外普查结果在这里可以逐名查到。
        </p>
        <!-- 「全部学生」这四个字会把人带向「全校」。范围不是全校，是这个人自己
             的数据范围（§9 的谓词在服务端加进 WHERE），所以要写出来。
             拿不到 `scopes` 时（老会话/异常）整句不出现——宁可不说，也不要猜一个「全校」。 -->
        <p v-if="activeTab === 'students' && scopeText" class="muted tiny" style="margin-top:6px">
          范围：{{ scopeText }}（你数据范围内的学生，由授权范围决定）
        </p>
        <p v-else class="page-desc">从发现、复核、跟进、家庭回访、复测到关闭形成连续档案。</p>
      </div>
      <!-- Selection-dependent actions sit together and stay disabled until the
           selection is non-empty — previously they were clickable and only
           surfaced an error afterwards. They belong to the queue tab only:
           the student list has no selection and no export. -->
      <div v-if="activeTab === 'cases'" class="actions">
        <div v-if="selected.size > 0" class="selection-actions">
          <span class="selection-count">已选 {{ selected.size }} 人</span>
          <button class="btn" @click="batchAssign">批量分配</button>
          <button class="btn" @click="batchExport">导出选中</button>
          <button class="btn small" @click="clearSelection">清除</button>
        </div>
        <button class="btn primary" @click="openExport(true)">高度关注导出</button>
      </div>
    </div>

    <div class="tabs">
      <button
        v-for="tab in TOP_TABS"
        :key="tab.key"
        :class="['tab', { active: activeTab === tab.key }]"
        @click="selectTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <SkeletonBlock v-if="loading && activeTab === 'cases'" variant="table" :rows="5" />
    <ErrorState v-if="error && !loading && activeTab === 'cases'" :message="error" :on-retry="load" />

    <template v-if="!loading && activeTab === 'cases'">
      <div class="card pad" style="margin-bottom: 16px">
        <div class="queue-tabs">
          <button
            v-for="tab in QUEUE_TABS"
            :key="tab.key"
            :class="['queue-tab', { active: queueFilter === tab.key }]"
            @click="queueFilter = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
        <div class="toolbar" style="margin-top:12px">
          <div class="search-box">
            <input v-model="query" placeholder="搜索姓名、学号、年级或班级" />
          </div>
          <!-- 三个选项从 `LEVEL_LABELS` 渲染，不在这里抄中文：`KEY_ATTENTION` 曾经在这
               写死成「高度关注」，而同一屏每一行的等级药丸写的是「重点关注」——筛选器与
               它筛出来的行给同一个等级两个名字（CLAUDE.md §3 那张表的第二面）。 -->
          <select v-model="riskFilter" class="select">
            <option value="all">全部关注等级</option>
            <option v-for="code in LEVEL_FILTER_CODES" :key="code" :value="code">
              {{ levelLabel(code) }}
            </option>
          </select>
          <label class="row-select">
            <input
              type="checkbox"
              :checked="filtered.length > 0 && selected.size === filtered.length"
              :indeterminate.prop="selected.size > 0 && selected.size < filtered.length"
              @change="toggleSelectAll"
            />
            <span class="muted tiny">{{ filtered.length }} 人 · 已选 {{ selected.size }} 人</span>
          </label>
        </div>
      </div>

      <div class="card">
        <div class="card-body">
          <DataTable
            :columns="columns"
            :rows="filtered"
            row-key="case_id"
            :page-size="20"
            empty-text="没有符合条件的学生"
          >
            <template #student_name="{ row }">
              <label class="row-select">
                <input
                  type="checkbox"
                  :checked="selected.has(row.student_id)"
                  @change="toggleSelect(row.student_id)"
                />
                <span class="student-cell">
                  <span class="student-avatar" aria-hidden="true">{{ row.student_name[0] }}</span>
                  <span>
                    <strong>{{ row.student_name }}</strong>
                    <span class="muted tiny">{{ row.student_no }} · {{ row.grade }}{{ row.class_name }}</span>
                  </span>
                </span>
              </label>
            </template>
            <template #total_level="{ row }">
              <span :class="['pill', levelTone(row.total_level)]">{{ levelLabel(row.total_level) }}</span>
            </template>
            <template #case_status="{ row }">
              <span :class="['pill', statusTone(row.case_status)]">{{ statusLabel(row.case_status) }}</span>
            </template>
            <template #owner_name="{ row }">{{ row.owner_name || '未分配' }}</template>
            <template #next_follow_up_date="{ row }">
              <span v-if="row.overdue" class="pill red">已逾期</span>
              <span v-else>{{ row.next_follow_up_date || '—' }}</span>
            </template>
            <template #actions="{ row }">
              <button class="btn small" @click="openDetail(row.student_id)">查看档案</button>
            </template>
          </DataTable>
        </div>
      </div>
    </template>

    <!-- 全部学生：整个名册 + 每人最近一场。数据首次切到该页签才拉（`loadStudents`）。 -->
    <template v-if="activeTab === 'students'">
      <div class="card pad" style="margin-bottom: 16px">
        <div class="toolbar">
          <div class="search-box">
            <input v-model="studentQuery" placeholder="搜索姓名、学号、年级或班级" />
          </div>
          <select v-model="studentGrade" class="select">
            <option value="all">全部年级</option>
            <option v-for="g in gradeOptions" :key="g" :value="g">{{ g }}</option>
          </select>
          <select v-model="studentClass" class="select">
            <option value="all">全部班级</option>
            <option v-for="c in classOptions" :key="c" :value="c">{{ c }}</option>
          </select>
          <label class="row-select">
            <input v-model="assessedOnly" type="checkbox" />
            <span class="muted tiny">只看已测评</span>
          </label>
          <!-- 失败时不报「0 人」：那个数会被读成「这所学校没有学生」。 -->
          <span v-if="!studentsLoading && !studentsError" class="muted tiny">{{ filteredStudents.length }} 人</span>
        </div>
      </div>

      <SkeletonBlock v-if="studentsLoading" variant="table" :rows="5" />
      <ErrorState
        v-if="studentsError && !studentsLoading"
        :message="studentsError"
        :on-retry="loadStudents"
      />

      <div v-if="!studentsLoading && !studentsError" class="card">
        <div class="card-body">
          <DataTable
            :columns="studentColumns"
            :rows="filteredStudents"
            row-key="student_id"
            :page-size="20"
            empty-text="没有符合条件的学生"
          >
            <template #student_name="{ row }">
              <span class="student-cell">
                <span class="student-avatar" aria-hidden="true">{{ row.student_name[0] }}</span>
                <strong>{{ row.student_name }}</strong>
              </span>
            </template>
            <template #total_level="{ row }">
              <span :class="['pill', levelTone(row.total_level)]">{{ levelLabel(row.total_level) }}</span>
            </template>
            <!-- 数值列的界面约定是「—」而不是空（§3；CSV 那一侧才留空）。 -->
            <template #total_score="{ row }">{{ row.total_score ?? '—' }}</template>
            <template #submitted_at="{ row }">{{ row.submitted_at?.slice(0, 10) || '—' }}</template>
            <!-- 没有会话时是「—」而不是「系统内作答」：没有测过与「测评是系统内做的」
                 不是一回事（`export_service` 的「来源」列同一条约定）。 -->
            <template #source="{ row }">
              <span :class="['pill', row.source === 'IMPORTED' ? 'blue' : 'gray']">
                {{ row.source ? sourceLabel(row.source) : '—' }}
              </span>
            </template>
            <template #case_status="{ row }">
              <span v-if="row.case_status" :class="['pill', statusTone(row.case_status)]">
                {{ statusLabel(row.case_status) }}
              </span>
              <span v-else class="muted tiny">未建档</span>
            </template>
            <template #actions="{ row }">
              <!-- 只有已建档的才有链接：没有档案的行点进去是 404，而前端拿不到
                   状态码（§2），用户看到的是一个红条。不渲染，不是 disabled。 -->
              <button v-if="row.case_id" class="btn small" @click="openStudentCase(row.student_id)">
                查看档案
              </button>
              <span v-else class="muted tiny">—</span>
            </template>
          </DataTable>
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
      confirm-text="确认"
      cancel-text="取消"
      @confirm="onConfirm"
      @cancel="onCancelConfirm"
      @update:open="showConfirm = $event"
    />

    <Modal
      :model-value="showExportModal"
      :title="exportHighRisk ? '高度关注导出' : '受控导出学生摘要'"
      size="lg"
      @update:model-value="showExportModal = $event"
    >
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
        <div class="form-two">
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
          <input v-model="exportConsent" type="checkbox" />
          <span>我确认该导出用于授权工作范围，并接受审计记录</span>
        </label>
      </div>
      <template #footer>
        <div class="modal-actions">
          <button class="btn" @click="showExportModal = false">取消</button>
          <button class="btn primary" @click="confirmExport">确认导出</button>
        </div>
      </template>
    </Modal>

  </div>
</template>
