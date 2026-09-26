<script setup lang="ts">
/**
 * 德育领导的专业报告中心（§5.13 Phase C）。
 *
 * 这一页是**只读**的：领导看不到任何编辑控件，也拿不到个体答卷。四件事在这一版里落地，
 * 每一件都对着 §5.13.5 里的一条：
 *
 *  · **默认打开最新那一份**（列表按 `updated_at` 降序，服务端排好的），
 *    而不是停在「请从左侧选择」——登录进来第一眼就该看到最近一次发布的东西；
 *  · **版本号读 `content_version`，不是 `current_version`**。V2 草稿期间报告头已经指向
 *    V2 了，而领导读到的那一份正文是 V1（服务端按 `viewer` 定的，见
 *    `reporting_service.serialize`）。照 `current_version` 渲染会得到「标签写 V2、
 *    正文是 V1」——两处各说各话，而屏幕上完全看不出来；
 *  · **发布人与发布时间取版本级的 `latest_published_by_name` / `latest_published_at`**。
 *    `created_by` 是**谁建的这份报告**，不是**谁把它发布给领导的**——拿前者冒充后者，
 *    领导要找的人就错了一个；
 *  · **两类空态分开**（§14）：「学校尚未发布报告」是一句关于数据的话，「当前筛选无匹配
 *    报告」是一句关于**这次筛选**的话。合成一句会让学校在库里明明有报告时读到自己没有。
 */
import { computed, onMounted, ref } from 'vue'
import ErrorState from '../../components/ErrorState.vue'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import ReportPageHeader from '../analytics/components/ReportPageHeader.vue'
import { useRovingFocus } from '../../composables/useRovingFocus'
import { useSettings } from '../../composables/useSettings'
import { formatDateTime } from '../../services/dates'
import { reportVersionLabel } from '../../services/labels'
import {
  exportProfessionalReport,
  getAssessmentTasks,
  listProfessionalReports,
  type ProfessionalReportListItem
} from '../../services/api'

const reports = ref<ProfessionalReportListItem[]>([])
/** 选中的是 **id**，不是报告对象本身：重新加载之后对象会换一批，id 仍然指得准。 */
const selectedId = ref<number | null>(null)
const loading = ref(true)
const error = ref('')
const keyword = ref('')
/** 任务 id → 名称。取不到时退回「共 N 个测评任务」，不留空（§24：留空与「没问过」分不开）。 */
const taskNames = ref<Record<number, string>>({})
const notice = ref('')
const exporting = ref(false)

const { settings, loadSettings } = useSettings()

const sections = [
  { key: 'overall_summary', label: '整体情况说明' },
  { key: 'dimension_interpretation', label: '重点维度解释' },
  { key: 'sample_validity_note', label: '样本覆盖及效度说明' },
  { key: 'support_plan', label: '后续教育支持计划' }
] as const

/** 客户端过滤，不新增后端接口（§5.13.5）：按标题或报告编号匹配，空关键词即全量。 */
const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return reports.value
  return reports.value.filter(
    item =>
      item.title.toLowerCase().includes(query) || item.report_no.toLowerCase().includes(query)
  )
})

const selected = computed(() => reports.value.find(item => item.id === selectedId.value) ?? null)

/**
 * 键盘：整个列表只占**一个** Tab 停靠点（2026-09-26）。
 *
 * 这一份列表有一百多行，而每一行此前都是普通 `<button>`——也就是每一个 Tab 停靠点。处置是
 * roving tabindex，逻辑与理由都写在 `composables/useRovingFocus.ts` 里（这里与心理老师那份
 * 「我的报告」列表行为逐字相同，所以只有那一个定义）。行数取**筛选之后**的那个，
 * 于是筛选把列表缩小时 `tabindex=0` 会跟着夹回去，不会出现「一行都不是 Tab 停靠点」。
 */
const { tabbableIndex, noteFocus, onRowKeydown } = useRovingFocus(() => filtered.value.length)

/**
 * 领导能导出的只有**最新已发布那一版**——`content` 就是服务端按 `viewer` 选出来的那一份，
 * 所以这里不另做判断，版本号直接取它。（导出作业那道门是 `PROFESSIONAL_REPORT_READ`，
 * 与「能看」同一档：领导读得到就导得出，见 `api/v1/reporting.py` 的 `Reader`。）
 */
const selectedVersion = computed(() => selected.value?.content?.version_no ?? null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    reports.value = await listProfessionalReports()
    // 默认打开最新那一份（服务端按 `updated_at` 降序）。**保不住原来的选中**时退回第一份：
    // 报告被撤销发布之后会从这个列表里消失，而 `selectedId` 还指着它。
    if (!reports.value.some(item => item.id === selectedId.value)) {
      selectedId.value = reports.value[0]?.id ?? null
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
    reports.value = []
    selectedId.value = null
  } finally {
    loading.value = false
  }
}

/**
 * 任务名只用来把 `task_scope.task_ids` 说成人看得懂的话，**拉不到不影响这一页的主体**，
 * 所以失败是静默的（退回「共 N 个测评任务」）。这与主体那次读取的两分法是同一条
 * （§14：加载失败与空态必须分开）——那一处失败要说出来，这一处不必。
 */
async function loadTaskNames() {
  try {
    const tasks = await getAssessmentTasks()
    taskNames.value = Object.fromEntries(tasks.map(task => [task.id, task.name]))
  } catch {
    taskNames.value = {}
  }
}

function rangeSummary(report: ProfessionalReportListItem) {
  const ids = report.task_scope?.task_ids || []
  if (!ids.length) return '未记录任务范围'
  const names = ids.map(id => taskNames.value[id]).filter(Boolean)
  if (!names.length) return `共 ${ids.length} 个测评任务`
  return names.length === 1 ? names[0] : `${names[0]} 等 ${ids.length} 个任务`
}

/** 版本号与它的状态**都取自版本行自己**——不拿报告头那三个字段顶替（见文件头那段）。 */
function versionLabel(report: ProfessionalReportListItem) {
  const version = report.content
  if (!version) return '—'
  return reportVersionLabel(version.status, version.version_no)
}

/**
 * 可评价样本数来自**这一版冻结的那份快照**，不是按现在的数据重算。`0` 是合法的值。
 *
 * 列表里这一项是服务端从快照里**现取的标量**（`evaluable_count`），与 `statistics_snapshot`
 * 同源——列表不再带那整份快照了（`ProfessionalReportListItem` 的注释记着为什么）。
 * 所以这里读的是一个数，而不是自己钻那条路径去取。
 */
function evaluableText(report: ProfessionalReportListItem) {
  const count = report.evaluable_count
  return count === null || count === undefined ? '—' : String(count)
}

function releasedText(report: ProfessionalReportListItem) {
  const at = report.latest_published_at
  if (!at) return '—'
  const who = report.latest_published_by_name
  return who ? `${formatDateTime(at)} · ${who}` : formatDateTime(at)
}

/* ---- 导出：复用专业报告那一条导出作业链，不新建第二套（§5.13.5）---- */

const formOpen = ref(false)
const purposes = computed(() => settings.value.export.purposes)
const exportDescription = computed(() => {
  const report = selected.value
  if (!report) return ''
  return [
    `导出对象：${report.report_no}「${report.title}」。`,
    `版本：V${selectedVersion.value}（最新已发布那一版）。`,
    `任务范围：${rangeSummary(report)}。`,
    '格式：CSV，由服务端生成并纳入导出治理。',
    '隐私保护：强制开启，文件里不含学生姓名与个体答卷。',
    `文件生成后在导出中心保留 ${settings.value.export.job_ttl_hours} 小时，过期后不再可下载。`,
    '这一次导出会记下操作人、用途与所选版本。'
  ].join('')
})
const formFields = computed<FormField[]>(() => [
  {
    key: 'purpose',
    label: '导出用途',
    type: 'select',
    required: true,
    options: purposes.value.map(item => ({ value: item, label: item })),
    placeholder: '请选择用途',
    hint: '用途会进审计，是这份文件离楼之后的用途说明。'
  }
])

function askExport() {
  notice.value = ''
  formOpen.value = true
}

async function onExportSubmit(values: Record<string, string>) {
  const purpose = (values.purpose || '').trim()
  const report = selected.value
  const versionNo = selectedVersion.value
  if (!purpose || !report || versionNo === null) return
  exporting.value = true
  try {
    const job = await exportProfessionalReport(report.id, purpose, versionNo)
    notice.value = `已下载，可在导出中心查看（${job.job_no}）。`
  } catch (err) {
    notice.value = err instanceof Error ? err.message : '导出失败'
  } finally {
    exporting.value = false
  }
}

function onExportCancel() {
  formOpen.value = false
}
/** `Modal` 的 Escape / 遮罩点击只发 `update:open false`，不发 `cancel`（CLAUDE.md §15）。 */
function onExportOpen(value: boolean) {
  if (!value) formOpen.value = false
}

onMounted(() => {
  load()
  loadTaskNames()
  loadSettings()
})
</script>

<template>
  <!-- 标题**不在这里传**：`ReportPageHeader` 的标题唯一出处是路由的 `meta.title`
       （`routes.ts` 的 `leader/analytics/report`），组件只声明了 `description` 一个 prop。
       此前这里多传了一个 `title="学校心理工作分析摘要"`，它既没被声明也没被使用——
       Vue 会把它当成普通属性落到根 `<header>` 上，于是同一个字符串在页面上有两处定义：
       看得见的那一处来自路由，而浏览器上多出来的一个原生 tooltip 来自这一行。
       两处此刻**恰好一致**，所以看不出问题；改掉路由标题的那一刻它们才会分岔，
       而分岔的那一半（tooltip）没有任何测试看得见。删掉它，回到单一出处。 -->
  <ReportPageHeader description="查看心理老师已经发布的聚合专业摘要；本页不提供专业解读编辑或个体答卷内容。"/>
  <!-- 失败那一支排在空态**之前**：`v-else-if` 的次序就是这条约定（§14）。 -->
  <ErrorState v-if="error" :message="error" :on-retry="load"/>
  <div v-else-if="loading" class="loading" aria-busy="true" aria-live="polite">加载中…</div>
  <div v-else class="report-layout">
    <section class="card report-picker">
      <div class="picker-head">
        <h2 class="section-title">已发布报告</h2>
        <span v-if="reports.length" class="muted">共 {{ reports.length }} 份</span>
      </div>
      <!-- 输入框走全站的 `.search-box > input`，不自己写一套（CLAUDE.md §17：
           一个裸 `<input>` 拿到的是浏览器默认样式——另一个物种的形状）。 -->
      <label class="filter-field">
        <span>筛选</span>
        <span class="search-box">
          <input v-model="keyword" type="search" placeholder="按标题或报告编号筛选"/>
        </span>
      </label>
      <p v-if="!reports.length" class="empty">学校尚未发布报告</p>
      <p v-else-if="!filtered.length" class="empty">当前筛选无匹配报告</p>
      <ul v-else class="rows">
        <li v-for="(item, index) in filtered" :key="item.id">
          <!-- `aria-pressed` 而不是只换底色：这是「在一组里选一个」，读屏软件要知道
               当前选的是哪一个（CLAUDE.md §15，与 `ProfessionalReportList` 同一条）。 -->
          <button
            type="button"
            class="report-item"
            :class="{ active: item.id === selectedId }"
            :tabindex="index === tabbableIndex ? 0 : -1"
            :aria-pressed="item.id === selectedId"
            @focus="noteFocus(index)"
            @keydown="onRowKeydown($event, index)"
            @click="selectedId = item.id"
          >
            <span class="item-title">{{ item.title }}</span>
            <span class="item-meta">{{ item.report_no }} · {{ versionLabel(item) }}</span>
            <span class="item-meta">发布于 {{ releasedText(item) }}</span>
            <span class="item-meta">任务范围：{{ rangeSummary(item) }}</span>
          </button>
        </li>
      </ul>
    </section>

    <section v-if="selected?.content" class="card report-detail">
      <h2 class="section-title">{{ selected.title }}</h2>
      <dl class="meta">
        <dt>报告编号</dt><dd>{{ selected.report_no }}</dd>
        <dt>版本</dt><dd>{{ versionLabel(selected) }}</dd>
        <dt>发布时间</dt><dd>{{ formatDateTime(selected.latest_published_at) }}</dd>
        <!-- 发布人取**版本级**的发布人：`created_by` 是「谁建的」，不是「谁发布的」。 -->
        <dt>发布人</dt><dd>{{ selected.latest_published_by_name || '—' }}</dd>
        <dt>任务范围</dt><dd>{{ rangeSummary(selected) }}</dd>
        <dt>可评价样本数</dt><dd>{{ evaluableText(selected) }}</dd>
      </dl>
      <div class="toolbar">
        <button class="btn" :disabled="exporting || selectedVersion === null" @click="askExport">
          导出这一版
        </button>
        <span v-if="notice" class="hint" role="status">{{ notice }}</span>
      </div>
      <template v-for="section in sections" :key="section.key">
        <h3 class="section-label">{{ section.label }}</h3>
        <p class="prose">{{ selected.content[section.key] || '—' }}</p>
      </template>
      <p class="hint">
        仅展示已发布的聚合摘要，不包含原始答卷、重点题具体答案、家庭回访正文或私密记录。
      </p>
    </section>
  </div>

  <FormDialog
    :open="formOpen"
    title="导出专业报告"
    :description="exportDescription"
    submit-text="生成并下载"
    :fields="formFields"
    @submit="onExportSubmit"
    @cancel="onExportCancel"
    @update:open="onExportOpen"
  />
</template>

<style scoped>
.report-layout { display: grid; grid-template-columns: 380px 1fr; gap: 16px; align-items: start }
.picker-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px }
.filter-field { display: grid; gap: 5px; margin-top: 12px; color: var(--muted); font-size: var(--font-caption); font-weight: 600 }
.rows { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 8px }
.report-item { display: grid; width: 100%; gap: 3px; text-align: left; padding: 11px 12px; border: 1px solid #dce6ef; border-radius: 8px; background: #fbfdff; cursor: pointer; font: inherit }
.report-item:hover { border-color: #b9d3f0; background: #f6faff }
.report-item.active { border-color: #0876d9; background: #f0f7ff; box-shadow: inset 3px 0 0 #0876d9 }
.item-title { font-weight: 650; overflow-wrap: anywhere }
.item-meta { color: #617994; font-size: 12.5px; overflow-wrap: anywhere }
.meta { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; margin: 14px 0 0 }
.meta dt { color: var(--muted); font-size: var(--font-caption); font-weight: 600 }
.meta dd { margin: 0; overflow-wrap: anywhere }
.section-label { margin: 20px 0 0; font-size: 14px }
.prose { white-space: pre-wrap; margin: 6px 0; line-height: 1.8 }
.empty { padding: 24px 4px; text-align: center; color: var(--muted); font-size: 13px }
.toolbar { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; margin-top: 14px }
@media(max-width: 900px) { .report-layout { grid-template-columns: 1fr } }
</style>
