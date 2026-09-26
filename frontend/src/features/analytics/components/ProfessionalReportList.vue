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
 */
import { useRovingFocus } from '../../../composables/useRovingFocus'
import { formatDateTime } from '../../../services/dates'
import { reportStatusTone, reportVersionLabel } from '../../../services/labels'
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

/** 行数传 getter：列表是异步加载的，传值会拿到旧的那个（见 `useRovingFocus`）。 */
const { tabbableIndex, noteFocus, onRowKeydown } = useRovingFocus(() => props.reports.length)

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
  return reportVersionLabel(
    report.current_version_status ?? report.status,
    report.current_version
  )
}
</script>

<template>
  <section class="card report-list">
    <div class="list-head">
      <h2 class="section-title">我的报告</h2>
      <span class="muted" v-if="reports.length">共 {{ reports.length }} 份</span>
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
    <ul v-else class="rows">
      <li v-for="(item, index) in reports" :key="item.id">
        <button
          type="button"
          class="row"
          :class="{ active: item.id === selectedId }"
          :tabindex="index === tabbableIndex ? 0 : -1"
          :aria-pressed="item.id === selectedId"
          @focus="noteFocus(index)"
          @keydown="onRowKeydown($event, index)"
          @click="emit('select', item.id)"
        >
          <span class="row-main">
            <span class="row-title">{{ item.title }}</span>
            <span class="row-sub">{{ item.report_no }} · {{ rangeSummary(item) }}</span>
          </span>
          <span class="row-side">
            <span class="pill" :class="reportStatusTone(item.current_version_status ?? item.status)">
              {{ statusOf(item) }}
            </span>
            <span class="row-time">{{ formatDateTime(item.updated_at) }}</span>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.report-list { margin-bottom: 16px }
.list-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px }
.list-loading, .list-empty { padding: 18px 4px; color: var(--muted); font-size: 13px }
.list-error { display: flex; align-items: center; gap: 8px; padding: 14px 4px; color: #b42318; font-size: 13px }
.link-btn { border: 0; padding: 0 2px; background: none; color: #0876d9; font: inherit; font-weight: 600; text-decoration: underline; cursor: pointer }
.rows { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 8px }
.row { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; text-align: left; padding: 10px 12px; border: 1px solid #e2eaf4; border-radius: 8px; background: #fbfdff; cursor: pointer; font: inherit }
.row:hover { border-color: #b9d3f0; background: #f6faff }
.row.active { border-color: #0876d9; background: #f0f7ff; box-shadow: inset 3px 0 0 #0876d9 }
.row-main { display: grid; gap: 3px; min-width: 0 }
.row-title { font-weight: 650; overflow-wrap: anywhere }
.row-sub { color: #617994; font-size: 12.5px; overflow-wrap: anywhere }
.row-side { display: grid; gap: 4px; justify-items: end; flex-shrink: 0 }
.row-time { color: var(--muted); font-size: 12.5px }
@media(max-width: 600px) {
  .row { align-items: flex-start; flex-direction: column }
  .row-side { justify-items: start }
}
</style>
