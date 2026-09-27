<script setup lang="ts">
/**
 * 「我的报告」——心理老师自己那些专业报告的入口（§5.13 Phase A）。
 *
 * 在这个区域出现之前，这一页只有一份**当前正在编辑**的报告握在内存里：刷新一次就没了，
 * 而库里那份草稿既看不见也回不去（`reportId` 是页面自己的一个 `ref`）。所以「打开既有
 * 草稿」这件事在界面上从来没有落点——列表就是那个落点。
 *
 * 六列都在一张行里，顺序按老师找东西的顺序排：**先认出是哪一份**（标题 + 编号 +
 * 任务范围），**再看它是什么状态、能不能改**（状态 · Vn + 更新时间）。
 *
 * 选中态用 `aria-pressed` 而不是只换底色：这是「在一组里选一个」，读屏软件要知道
 * 当前选的是哪一个（与登录页角色页签同一条，CLAUDE.md §15）。
 *
 * ## 键盘：整个列表只占**一个** Tab 停靠点（2026-09-26）
 *
 * 这一份列表有一百多行，而每一行此前都是普通 `<button>` ——也就是**每一个 Tab 停靠点**。
 * 处置是 roving tabindex，理由、形状与「焦点与选中为什么是两件事」都写在
 * `composables/useRovingFocus.ts` 里（同一份逻辑领导的报告列表也要用，所以只有那一个定义）。
 *
 * ## 筛选与分页：一页二十行，方向键翻页（2026-09-27）
 *
 * 用户在共享演示库上看到的是一份**两百多行、没有任何筛选与分页**的列表：要找一份上个月
 * 的报告只能一路滚，而「哪些还是草稿」这个问题在界面上答不出来。现在三件事一起有：
 * 关键词筛选（标题或报告编号）、状态筛选（草稿 / 已发布 / 已归档）、每页 20 行。
 *
 * **筛选与分页都在客户端做**，不新增后端接口（§5.15.5 第 9 条；领导端那一份的筛选
 * 是同一条口径）。这不只是取舍，还是**父组件的要求**：`ReportExportPage.vue` 有两处
 * （打开指定 id 的那一份、以及「当前任务范围下有没有别的草稿」）要拿这份数组去 `find`，
 * 服务端分页会让那两处只看得到当前页——「找不到就新建一份」于是会建出重复的草稿。
 *
 * **方向键到页边界要翻页，不能停住。** roving tabindex 把整个列表压成**一个** Tab 停靠
 * 点，方向键是列表内部唯一的移动方式；到第 20 行按 ↓ 什么都不发生时，键盘用户拿到的是一句
 * 无声的「到头了」，而他还得自己想到「Tab 出去、找到分页按钮、回车、Tab 回来」——四步，
 * 且屏幕上没有任何东西提示他。所以 ↓ 在末行翻下一页、↑ 在首行翻上一页，焦点跟到新页的
 * 首行（↑ 则到末行），方向键可以一直走下去。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRovingFocus } from '../../../composables/useRovingFocus'
import { formatDateTime } from '../../../services/dates'
import { REPORT_STATUS_LABELS, reportStatusTone, reportVersionLabel } from '../../../services/labels'
import type { ProfessionalReportListItem } from '../../../services/api'

const props = defineProps<{
  reports: ProfessionalReportListItem[]
  selectedId: number | null
  /** 任务 id → 名称，用来把 `task_scope.task_ids` 说成人看得懂的范围摘要。 */
  taskNames: Record<number, string>
  loading?: boolean
  /**
   * 读取失败时的那句话。**它与「还没有报告」是两件事**（§14）：此前失败走的是
   * `reports.value = []`，于是「你还没有保存过专业报告」在一次网络故障时也照样出现
   * ——而它是一句关于数据的断言，不是关于这一次读取的。
   */
  error?: string
}>()
const emit = defineEmits<{
  select: [id: number]
  /** 失败之后那条出路。`ErrorState` 的写法（带重试），与全站一致。 */
  retry: []
}>()

/** 每页条数。与 `DataTable` 的默认档一致（20）——全站分页读起来是同一个密度。 */
const PAGE_SIZE = 20

const keyword = ref('')
/** 状态筛选，空串 = 全部。取值是 `REPORT_STATUS_LABELS` 的键（**编码**，不是中文）。 */
const statusFilter = ref('')
const page = ref(1)

/**
 * 行数传 getter：列表是异步加载的，传值会拿到旧的那个（见 `useRovingFocus`）。
 *
 * **传的是当前页的行数，不是筛选结果的总数**：`onRowKeydown` 用
 * `closest('ul').querySelectorAll('button')[next]` 定位下一行，那个索引是**页内 DOM
 * 序号**。传总数的话，第 20 行按 ↓ 会去找 DOM 里不存在的第 21 个按钮（什么都不会发生）。
 */
const rowsRef = ref<HTMLUListElement | null>(null)
const { activeIndex, tabbableIndex, noteFocus, onRowKeydown } = useRovingFocus(
  () => pagedReports.value.length
)

/**
 * 一份报告此刻该按哪个状态读。
 *
 * 抽成函数是因为**筛选也要用它**：标签与筛选判据必须是同一个来源，各写一份
 * `?? report.status` 时，两处漂了不会有任何东西看得见（§11）。
 */
function statusCode(report: ProfessionalReportListItem) {
  return report.current_version_status ?? report.status
}

/**
 * 客户端筛选，不新增后端接口（§5.15.5 第 9 条）。空条件即全量。
 *
 * 关键词匹配标题或报告编号——与领导端那一份同一句话（同一个筛选在两边说同一句话）。
 */
const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  const status = statusFilter.value
  return props.reports.filter(item => {
    if (status && statusCode(item) !== status) return false
    if (!query) return true
    return (
      item.title.toLowerCase().includes(query) || item.report_no.toLowerCase().includes(query)
    )
  })
})

const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
/**
 * 构造上不越界（`Math.min`）。直接切片的话，在第 3 页上把关键词改窄会得到空数组
 * ——而屏幕上会说「共 3 份」却一份都不显示。
 */
const currentPage = computed(() => Math.min(page.value, pageCount.value))
const pagedReports = computed(() =>
  filtered.value.slice((currentPage.value - 1) * PAGE_SIZE, currentPage.value * PAGE_SIZE)
)

/** 翻页之后把 DOM 焦点真的搬到新页的第一行（键盘用户看不见鼠标）。 */
function focusRow(index: number) {
  activeIndex.value = index
  rowsRef.value?.querySelectorAll<HTMLButtonElement>('button')[index]?.focus()
}

// 筛选条件一变就回第一页：停在第 3 页上看一份只有 5 条的结果，那一页是空的。
watch([keyword, statusFilter], () => {
  page.value = 1
  activeIndex.value = 0
})

function goToPage(next: number) {
  page.value = next
  activeIndex.value = 0
}

/**
 * 方向键到页边界就翻页（理由见文件头）。翻页是异步的（`nextTick`），所以那一行的
 * 焦点由 `focusRow` 在新页渲染完之后搬过去。
 */
function onRowKeydownPaged(event: KeyboardEvent, index: number) {
  const lastRow = pagedReports.value.length - 1
  if (event.key === 'ArrowDown' && index === lastRow && currentPage.value < pageCount.value) {
    event.preventDefault()
    page.value = currentPage.value + 1
    void nextTick(() => focusRow(0))
    return
  }
  if (event.key === 'ArrowUp' && index === 0 && currentPage.value > 1) {
    event.preventDefault()
    page.value = currentPage.value - 1
    void nextTick(() => focusRow(pagedReports.value.length - 1))
    return
  }
  onRowKeydown(event, index)
}

/**
 * 任务范围摘要。
 *
 * 取不到名字时回「共 N 个测评任务」而**不是留空**：留空与「这份报告没有范围」分不开
 * （§24：留空与「没问过」分不开）。反过来，`task_scope` 真的为空是另一句话——
 * 那是历史行的形状，不是「有范围但名字查不到」。
 */
function rangeSummary(report: ProfessionalReportListItem) {
  const ids = report.task_scope?.task_ids || []
  if (!ids.length) return '未记录任务范围'
  const names = ids.map(id => props.taskNames[id]).filter(Boolean)
  if (!names.length) return `共 ${ids.length} 个测评任务`
  return names.length === 1 ? names[0] : `${names[0]} 等 ${ids.length} 个任务`
}

/** 列表里的状态读的是**版本级**字段：报告头在 V2 草稿期间已经指回 DRAFT 了。 */
function statusOf(report: ProfessionalReportListItem) {
  return reportVersionLabel(statusCode(report), report.current_version)
}
</script>

<template>
  <section class="card report-list">
    <div class="list-head">
      <h2 class="section-title">我的报告</h2>
      <!-- 筛选生效时报「筛选出几份」，否则只报总数。两句话都写在这一行里，不另起一段。 -->
      <span v-if="keyword || statusFilter" class="muted">
        筛选出 {{ filtered.length }} 份 · 共 {{ reports.length }} 份
      </span>
      <span v-else-if="reports.length" class="muted">共 {{ reports.length }} 份</span>
    </div>

    <div v-if="loading" class="list-loading" aria-busy="true" aria-live="polite">正在加载报告…</div>
    <!-- 失败那一支排在空态**之前**：`v-else-if` 的次序就是这条约定（§14）。 -->
    <div v-else-if="error" class="list-error">
      <span>{{ error }}</span>
      <button type="button" class="link-btn" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="!reports.length" class="list-empty">
      你还没有保存过专业报告。填写下方四段专业解读并保存后，它会出现在这里。
    </div>
    <template v-else>
      <!--
        筛选走全站的 `.search-box > input` 与 `.select`，不自己写一套（CLAUDE.md §17：
        一个裸 `<input>` 拿到的是浏览器默认样式——另一个物种的形状）。
      -->
      <div class="toolbar list-tools">
        <label class="search-box">
          <span class="sr-only">按标题或报告编号筛选</span>
          <input v-model="keyword" type="search" placeholder="按标题或报告编号筛选" />
        </label>
        <select v-model="statusFilter" class="select" aria-label="按状态筛选">
          <option value="">全部状态</option>
          <option v-for="(label, code) in REPORT_STATUS_LABELS" :key="code" :value="code">
            {{ label }}
          </option>
        </select>
      </div>

      <!-- 第二句空态是**关于筛选**的，与上面那句「还没有报告」不是一件事（§14）。 -->
      <p v-if="!filtered.length" class="list-empty">当前筛选无匹配报告。</p>
      <ul v-else ref="rowsRef" class="rows">
        <li v-for="(item, index) in pagedReports" :key="item.id">
          <button
            type="button"
            class="row"
            :class="{ active: item.id === selectedId }"
            :tabindex="index === tabbableIndex ? 0 : -1"
            :aria-pressed="item.id === selectedId"
            @focus="noteFocus(index)"
            @keydown="onRowKeydownPaged($event, index)"
            @click="emit('select', item.id)"
          >
            <span class="row-main">
              <span class="row-title">{{ item.title }}</span>
              <span class="row-sub">{{ item.report_no }} · {{ rangeSummary(item) }}</span>
            </span>
            <span class="row-side">
              <span class="pill" :class="reportStatusTone(statusCode(item))">
                {{ statusOf(item) }}
              </span>
              <span class="row-time">{{ formatDateTime(item.updated_at) }}</span>
            </span>
          </button>
        </li>
      </ul>

      <!-- 只有一页时整个分页器不出现：一个「第 1 / 1 页」加两枚灰按钮只是噪音。 -->
      <div v-if="pageCount > 1" class="table-pager">
        <span class="muted tiny">第 {{ currentPage }} / {{ pageCount }} 页</span>
        <div class="pager-controls">
          <button
            class="btn small"
            type="button"
            :disabled="currentPage <= 1"
            @click="goToPage(currentPage - 1)"
          >
            上一页
          </button>
          <button
            class="btn small"
            type="button"
            :disabled="currentPage >= pageCount"
            @click="goToPage(currentPage + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.report-list { margin-bottom: 16px }
.list-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px }
.list-loading, .list-empty { padding: 18px 4px; color: var(--muted); font-size: 13px }
.list-error { display: flex; align-items: center; gap: 8px; padding: 14px 4px; color: #b42318; font-size: 13px }
.link-btn { border: 0; padding: 0 2px; background: none; color: #0876d9; font: inherit; font-weight: 600; text-decoration: underline; cursor: pointer }
.list-tools { margin-top: 12px }
.rows { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 8px }
.row { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; text-align: left; padding: 10px 12px; border: 1px solid #e2eaf4; border-radius: 8px; background: #fbfdff; cursor: pointer; font: inherit }
.row:hover { border-color: #b9d3f0; background: #f6faff }
.row.active { border-color: #0876d9; background: #f0f7ff; box-shadow: inset 3px 0 0 #0876d9 }
.row-main { display: grid; gap: 3px; min-width: 0 }
.row-title { font-weight: 650; overflow-wrap: anywhere }
.row-sub { color: #617994; font-size: 12.5px; overflow-wrap: anywhere }
.row-side { display: grid; gap: 4px; justify-items: end; flex-shrink: 0 }
.row-time { color: var(--muted); font-size: 12.5px }
/* 分页器复用全站那一条（`DataTable` 的形状），只把横向内边距收掉：这张卡片自己已经有
   18px 内边距（父组件的 scoped 规则），照搬 `12px 20px` 会让它再缩进一格，而那条上边框
   也跨不满整张卡片。 */
.table-pager { margin-top: 12px; padding: 10px 0 0 }
@media(max-width: 600px) {
  .row { align-items: flex-start; flex-direction: column }
  .row-side { justify-items: start }
}
</style>
