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
import PrivacyNote from '../analytics/components/PrivacyNote.vue'
import ReportPageHeader from '../analytics/components/ReportPageHeader.vue'
import { useRovingFocus } from '../../composables/useRovingFocus'
import { useSettings } from '../../composables/useSettings'
import { formatDateTime } from '../../services/dates'
import { analysisModeLabel, reportStatusTone, reportVersionLabel } from '../../services/labels'
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
 * 左栏每一行那枚状态药丸的色调。
 *
 * 取法与心理老师那份「我的报告」列表（`ProfessionalReportList.vue` 的 `statusOf`）**同源**：
 * 版本行的状态优先、报告头兜底。两处各写一套判断时，同一个东西在两个角色屏幕上会是
 * 两种颜色，而两边看起来都正常——这正是这一页此前「同一个报告列表两副面孔」的成因之一。
 */
function itemTone(report: ProfessionalReportListItem) {
  return reportStatusTone(report.content?.status ?? report.status)
}

/**
 * 右侧拿不到正文时那张卡说什么（§14：空态是一句关于数据的话，不是占位）。
 *
 * 两态，判据是**左栏有没有报告**：「学校还没发布过」与「这一份没有可读的正文版本」
 * 是两句不同的话。不做第三态——`load()` 已经保证选中项一定落在列表里，
 * 为「选中的那一份消失了」写一句文案只会是一段走不到的死代码。
 */
const detailEmptyText = computed(() =>
  reports.value.length
    ? '这一份报告暂时没有可读的正文版本。'
    : '学校还没有发布过报告，这里暂时没有可读的正文。'
)

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
  <!-- 只读边界要**说得出**，不只体现在「一个编辑按钮都没有」（§5.13.5 与 §5.15.4 第 6 条）。
       用 `.notice` 而不是 `p[role="status"]`：后者是全站「操作回执」的定位器
       （`e2e/app.spec.ts` 的 `pageNotice` 与 `getByRole('status')`），一条常驻的说明
       落在那里会把那一族断言一起污染。形状照 `analytics/components/PrivacyNote.vue`。 -->
  <div class="notice readonly-note">
    <strong>本页只读</strong>
    <div>
      下面读到的正文与统计，是心理老师<strong>发布当时冻结</strong>的快照，不随当前数据变化。
      领导端不提供专业解读编辑、重新发布与个体答卷入口；要更新内容，请心理老师在「我的报告」里
      建新版本并重新发布。
    </div>
  </div>
  <!-- 失败那一支排在空态**之前**：`v-else-if` 的次序就是这条约定（§14）。 -->
  <ErrorState v-if="error" :message="error" :on-retry="load"/>
  <div v-else-if="loading" class="loading" aria-busy="true" aria-live="polite">加载中…</div>
  <!-- 包一层 `<template v-else>` 而不是把 `v-else` 留在 `<div>` 上：这一块下面还要
       再放一条 `<PrivacyNote>`（与另外三张分析页同一个位置、同一个组件），而一个元素
       只能带一个 `v-else`，它的兄弟节点会掉到条件之外去。
       **里面刻意不再缩进一层**：这次改动是结构性的（多一个分支），不是重排整块——
       纯空白重排会让 diff 从 3 行变成 140 行，而它承载的信息是零。 -->
  <template v-else>
  <div class="report-layout">
    <section class="card report-picker">
      <div class="picker-head">
        <h2 class="section-title">已发布报告</h2>
        <span v-if="reports.length" class="muted">共 {{ reports.length }} 份</span>
      </div>
      <!-- 口径写进界面（§9）：这里列的是**已发布**的那些，且同一份报告有多版时展示的是
           最新发布的那一版——领导读到的正文就是它，这句话要让他读得到。 -->
      <p class="muted tiny picker-note">只列已发布的报告；同一份报告有多版时，显示最新发布的那一版。</p>
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
            <!-- 两列行，与心理老师那份「我的报告」（`ProfessionalReportList.vue` 的
                 `.row` / `.row-main` / `.row-side`）**同形**：左列标题与副行，右列状态药丸
                 与时间。同一个「报告列表」在两处长得不一样，是这一页此前最硬的一处
                 「不符合整体规范」——也是这一段类名逐字对着那一份写的理由。 -->
            <span class="item-main">
              <span class="item-title">{{ item.title }}</span>
              <span class="item-sub">{{ item.report_no }} · {{ rangeSummary(item) }}</span>
            </span>
            <span class="item-side">
              <span class="pill" :class="itemTone(item)">{{ versionLabel(item) }}</span>
              <span class="item-time">{{ releasedText(item) }}</span>
            </span>
          </button>
        </li>
      </ul>
    </section>

    <!-- 这一页**唯一**的主工作区（§5.15 UX-FINAL-01）。判据是「谁在干活谁是主工作区」：
         这一页只有这一处能动手（「导出这一版」），左栏那份列表是选择器、不是工作区。
         **一页最多一个 Primary**，所以左栏留在默认的二级层（§5.15.2 第 3 条）。
         与 `ClassPortraitPage.vue` 那条「不给 tier-primary」的判据不冲突：那一条的两点
         理由（落在窄栏 / 产物是本机草稿）在这里**都不成立**——这一张落在宽栏
         （`.report-layout` 的右列），而它的产物是一份走导出治理的文件。 -->
    <section v-if="selected?.content" class="card tier-primary report-detail">
      <h2 class="section-title">{{ selected.title }}</h2>
      <!-- 状态条：左半是版本药丸 + 一句这句正文的口径，右半是能动手的那一枚按钮。
           形状逐字照 `ReportExportPage.vue` 的 `.open-bar`（同一个「版本状态 + 口径 + 动作」
           的条），**但类名不叫 open-bar**：那边它自己就是一张 `<section class="card open-bar">`，
           这里它是这张卡**内部**的一条——同名会让人以为是同一个东西，而两边的
           定位与上下间距本来就不同。
           药丸读 `selected.content.status`，与左栏每一行同一取法（见 `itemTone`）。 -->
      <div class="status-bar">
        <div class="status-bar-main">
          <span class="pill" :class="reportStatusTone(selected.content.status)">{{ versionLabel(selected) }}</span>
          <span class="status-bar-text">下面四段正文来自这一版<strong>发布当时冻结</strong>的快照，不随当前实时统计变化。</span>
        </div>
        <div class="status-bar-side">
          <button class="btn" :disabled="exporting || selectedVersion === null" @click="askExport">
            导出这一版
          </button>
        </div>
      </div>
      <!-- 回执行仍是 `role="status"`：它是**操作回执**，与上面那条常驻的只读说明不是一件事
           （上一段那个 `.notice` 刻意不带 role 就是为了不与它抢同一个定位器）。
           用 `<p>` 而不是 `<span>`：全站的操作回执都是 `.hint` 那一种形状
           （`ClassPortraitPage.vue` 的保存草稿回执同形），e2e 的 `pageNotice` 读的是
           `p[role="status"]`。 -->
      <p v-if="notice" class="hint" role="status">{{ notice }}</p>
      <!-- 六行元信息走全站的 `.detail-grid` / `.detail-row`（13 个文件共用的那一套，
           `styles.css`），不再自己维护一份 `dl.meta` 的两列网格——同一个「详情区」
           两套长相，正是这一页此前「不符合整体规范」的一部分。
           **`meta` 这个类名要保留**：它是 e2e 的锚点（断言 `.meta` 里含「已发布 · V1」）。 -->
      <div class="detail-grid meta">
        <div class="detail-row"><span>报告编号</span><span>{{ selected.report_no }}</span></div>
        <div class="detail-row"><span>版本</span><span>{{ versionLabel(selected) }}</span></div>
        <div class="detail-row">
          <span>发布时间</span><span>{{ formatDateTime(selected.latest_published_at) }}</span>
        </div>
        <!-- 发布人取**版本级**的发布人：`created_by` 是「谁建的」，不是「谁发布的」。 -->
        <div class="detail-row">
          <span>发布人</span><span>{{ selected.latest_published_by_name || '—' }}</span>
        </div>
        <div class="detail-row"><span>任务范围</span><span>{{ rangeSummary(selected) }}</span></div>
        <div class="detail-row">
          <span>可评价样本数</span><span>{{ evaluableText(selected) }}</span>
        </div>
        <!-- 「统计口径」是这一版冻结快照**按什么算出来的**那一句，此前界面上没有。
             值域由服务端收口（`analytics_service.ANALYSIS_MODES`，非法值落不了库），
             所以这里照实渲染、不兜底成某个默认口径——兜底会把「不知道」变成一句假话。
             它同时给 `labels.ts` 的 `analysisModeLabel` 补上**第一个读者**：
             一张表放在那里而没人从那儿取，也算没接上（§3 第一面那条的反面）。 -->
        <div class="detail-row">
          <span>统计口径</span><span>{{ analysisModeLabel(selected.analysis_mode) }}</span>
        </div>
      </div>
      <div class="prose-blocks">
        <!-- 循环变量叫 `block` 而不是 `section`：外层元素就叫 `<section>`，同名会让人读成
             「这一段就是上面那张卡」。第一个 `.prose` 必须是「整体情况说明」——e2e 拿它
             当「正文真的渲染出来了」的判据（`PUBLISHED_TEXT`），次序不能动。 -->
        <section v-for="block in sections" :key="block.key" class="prose-block">
          <h3 class="section-label">{{ block.label }}</h3>
          <p class="prose">{{ selected.content[block.key] || '—' }}</p>
        </section>
      </div>
    </section>
    <!-- 右侧拿不到正文时**不留一片空白**（§14：空态是一句关于数据的话）。
         判据与文案在 `detailEmptyText` 那一段里。 -->
    <section v-else class="card report-detail">
      <p class="empty">{{ detailEmptyText }}</p>
    </section>
  </div>
  <!-- 页脚那条隐私说明改用组件（`analytics/components/PrivacyNote.vue`），不再自己写
       一句 `.hint`：另外三张分析页（总览 / 年级 / 八维度 / 班级画像）用的都是它，
       而这一页此前是**同一个意思、另一种长相**——两句话并存时，改一处不会带动另一处。
       这里走 slot 而不是默认文案，是因为这一页要说的话比通用那句多一条：领导读到的是
       一份**已发布摘要**。原文照搬，一个字没改。 -->
  <PrivacyNote>
    仅展示已发布的聚合摘要，不包含原始答卷、重点题具体答案、家庭回访正文或私密记录。统计结果仅用于心理健康教育的参考，不筛查诊断。
  </PrivacyNote>
  </template>

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
/* 左栏放宽到 420px：两列行（标题/副行 + 药丸/时间）比原来那四行堆叠文本要宽一些，
   而这一页的左栏是**选择器**、不是工作区，它宽出来的部分正是从右栏那张卡的
   最大宽度上让的——右栏仍然是最宽的一列。 */
.report-layout { display: grid; grid-template-columns: 420px 1fr; gap: 16px; align-items: start }
/* 与另外四张分析页**逐字相同**的一条（`ClassPortraitPage.vue:214`、`DimensionsPage.vue:262`、
   `GradesPage.vue:165`、`OverviewPage.vue:206`、`ReportExportPage.vue:979`）：
   这一页此前**没有**它，于是两张卡的内容贴着边框渲染（零内边距）——这是用户说的
   「不符合整体规范」里最直白的一条。 */
section.card { min-width: 0; padding: 18px; overflow: hidden }
.picker-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px }
.picker-note { margin: 6px 0 0 }
.filter-field { display: grid; gap: 5px; margin-top: 12px; color: var(--muted); font-size: var(--font-caption); font-weight: 600 }
.rows { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 8px }
/* 两列行，逐字照 `ProfessionalReportList.vue` 的 `.row` 一套（只换类名）：
   同一条「报告列表」在心理老师那儿与领导这儿长得一样，是这个类名对着那一份写的理由。 */
.report-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; text-align: left; padding: 10px 12px; border: 1px solid #e2eaf4; border-radius: 8px; background: #fbfdff; cursor: pointer; font: inherit }
.report-item:hover { border-color: #b9d3f0; background: #f6faff }
.report-item.active { border-color: #0876d9; background: #f0f7ff; box-shadow: inset 3px 0 0 #0876d9 }
.item-main { display: grid; gap: 3px; min-width: 0 }
.item-title { font-weight: 650; overflow-wrap: anywhere }
.item-sub { color: #617994; font-size: 12.5px; overflow-wrap: anywhere }
.item-side { display: grid; gap: 4px; justify-items: end; flex-shrink: 0 }
.item-time { color: var(--muted); font-size: 12.5px }
/* 这一条只补上边距：两列网格本身归全局的 `.detail-grid` / `.detail-row`（`styles.css`，
   13 个文件共用的那一套）。`meta` 这个类名不能删——它是 e2e 的锚点（断言里面含
   「已发布 · V1」），而它同时说明「这一块是元信息区」。 */
.meta { margin: 14px 0 0 }
/* `.status-bar` 一套，布局值与 `ReportExportPage.vue` 的 `.open-bar` **逐字相同**
   （同一个「版本状态 + 口径 + 动作」的条）。**类名不叫 open-bar**：那边那一条自己就是
   一张 `.card`（自带底色与边框），这里它是这张卡内部的一条。底色照 `.card` 那一档取。 */
.status-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; padding: 12px 16px; border: 1px solid #e6ecf2; border-radius: 10px; background: var(--surface2) }
.status-bar-main { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0 }
.status-bar-text { color: #4d6580; font-size: 12.5px; line-height: 1.7 }
.status-bar-side { display: flex; gap: 8px; flex-wrap: wrap }
/* 四段正文各成一块、由一条极淡的分隔线断开：此前是四组 `h3 + p` 平铺连排，
   四段之间没有任何东西说得出「这是四段」还是「这是一段很长的话」。 */
.prose-blocks { display: grid; margin-top: 6px }
.prose-block { border-top: 1px solid #eef2f6; padding-top: 4px }
.prose-block:first-child { border-top: 0 }
.section-label { margin: 14px 0 0; font-size: 14px }
.prose { white-space: pre-wrap; margin: 6px 0; line-height: 1.8 }
.empty { padding: 24px 4px; text-align: center; color: var(--muted); font-size: 13px }
.readonly-note { margin-bottom: 16px }
/* 与另外三张分析页同一条 scoped 覆盖（`ClassPortraitPage.vue:233` 等）。 */
.privacy { margin-top: 16px }
@media(max-width: 900px) { .report-layout { grid-template-columns: 1fr } }
@media(max-width: 600px) {
  .report-item { align-items: flex-start; flex-direction: column }
  .item-side { justify-items: start }
  .status-bar { align-items: flex-start; flex-direction: column }
}
</style>
