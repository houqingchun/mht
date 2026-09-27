<script setup lang="ts">
/**
 * 八维度各自的迷你折线（2×4 小倍数）。
 *
 * 为什么必须归一化：八个维度题数不等（两个 15 题、其余 10 题），原始分并排放会让
 * 15 题的身体症状凭空压过 10 题的孤独倾向。每个格子都画 0–100%，分母是**该维度自己的**
 * 题数（后端随每个点下发 `max_score`），格子之间才可比。
 *
 * 复用 `dimensionPercent` 而不是自己算百分比：它的兜底（`max_score` 缺失或为 0 时按 15 题）
 * 已经在条形图上用了很久，另写一份迟早会出现 `NaN%` 或者两处口径不一致。
 */
import { computed } from 'vue'
import { dimensionLabel, dimensionPercent } from '../services/labels'
import { deltaTone } from '../services/trend'

interface HistoryEntry {
  session_id: number
  submitted_at: string | null
  dimensions: Array<{ dimension_code: string; score: number; max_score: number }>
}

const props = defineProps<{ history: HistoryEntry[] }>()

interface Series {
  code: string
  label: string
  percents: number[]
  first: number
  last: number
  /**
   * 首次 → 最近的变化量。只有一次时是 `null`（比不了），**不是 `0`**（CLAUDE.md §11）。
   *
   * 与 `services/trend.ts` 的 `latestScoreDelta` 不是同一个量：那个是「最近两次」之差
   * （给总分那条摘要用），这个是「首次 → 最近」（跟着这一格的 `首次 x% / 最近 y%` 走）。
   * 两者都不是随便挑的——各自与自己那两行文案同源。
   */
  delta: number | null
}

/**
 * 按维度把各场串起来。维度顺序取**首次出现**的顺序（后端按 `dimension_code` 升序下发，
 * 所以八格每次的排列都一样，不会因为某场缺一个维度就整体错位）。
 */
const series = computed<Series[]>(() => {
  const order: string[] = []
  const byCode = new Map<string, number[]>()
  for (const entry of props.history) {
    for (const dimension of entry.dimensions || []) {
      if (!byCode.has(dimension.dimension_code)) {
        byCode.set(dimension.dimension_code, [])
        order.push(dimension.dimension_code)
      }
      byCode.get(dimension.dimension_code)!.push(dimensionPercent(dimension.score, dimension.max_score))
    }
  }
  return order.map(code => {
    const percents = byCode.get(code)!
    return {
      code,
      label: dimensionLabel(code),
      percents,
      first: percents[0],
      last: percents[percents.length - 1],
      delta: percents.length > 1 ? percents[percents.length - 1] - percents[0] : null
    }
  })
})

const W = 100
const H = 34
const TOP = 5
const BOTTOM = 29

function xOf(index: number, count: number) {
  return count <= 1 ? W / 2 : (index * W) / (count - 1)
}

function yOf(percent: number) {
  // 0% 落底、100% 落顶；上下各留一点，线不会被裁掉
  return BOTTOM - (Math.min(Math.max(percent, 0), 100) / 100) * (BOTTOM - TOP)
}

function pointsOf(percents: number[]) {
  return percents.map((percent, index) => ({ x: xOf(index, percents.length), y: yOf(percent) }))
}

function lineOf(percents: number[]) {
  return pointsOf(percents)
    .map(point => `${point.x},${point.y}`)
    .join(' ')
}
</script>

<template>
  <div v-if="!series.length" class="empty">暂无维度趋势数据</div>
  <div v-else class="spark-grid">
    <div v-for="item in series" :key="item.code" class="spark">
      <div class="spark-title">
        <span>{{ item.label }}</span>
        <!-- 「首次 → 最近」的变化量。此前脚下印着两个百分比，差值要读者心算八次，
             而这一页要回答的恰恰是「哪个维度在往哪边走」。**只上色，不配「好转 /
             恶化」这类词**——一次分值的升降不构成疗效结论（产品边界）。颜色方向与
             总分那张卡同源：都在 `services/trend.ts` 的 `deltaTone` 里判。 -->
        <span
          v-if="item.delta !== null"
          class="spark-delta"
          :class="`delta-${deltaTone(item.delta)}`"
        >{{ item.delta > 0 ? '+' : '' }}{{ item.delta }}%</span>
      </div>
      <svg class="chart" :viewBox="`0 0 ${W} ${H}`" role="img" :aria-label="`${item.label} 历次变化`">
        <polyline v-if="item.percents.length > 1" class="spark-line" :points="lineOf(item.percents)" />
        <circle
          v-for="(point, index) in pointsOf(item.percents)"
          :key="index"
          class="spark-dot"
          :cx="point.x"
          :cy="point.y"
          r="2.6"
        />
      </svg>
      <div class="spark-foot">
        <!-- 只有一次时没有「首次 → 最近」可比，写「仅 1 次」而不是把同一个数印两遍 -->
        <span v-if="item.percents.length > 1">首次 {{ item.first }}%</span>
        <span v-else>仅 1 次</span>
        <span>最近 {{ item.last }}%</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 标题行：维度名在左、变化量在右（2026-09-27，§5.15.9 UX-FINAL-02）。
 *
 * 这个文件此前**没有样式块**——八个格子的长相全部来自全局 `styles.css` 的 `.spark-*`
 * 那一节。这里只加**一条**覆盖：那一节的 `.spark-title` 是纯文本（只有 `font-size` 与
 * `font-weight`），而 Δ 要贴在右端，所以把它变成一个两端对齐的 flex 行。
 * `justify-content: space-between` 而不是给 Δ 加 `margin-left: auto`：前者在**没有 Δ**
 * 的那些格子（只测过一次的学生）里也成立——一个孤零零的维度名仍然靠左。
 *
 * 全局那一节一个字没动，其余几个类（`.spark-grid` / `.spark` / `.spark-line` /
 * `.spark-dot` / `.spark-foot`）照旧——**只写这一条，不借着这次机会把整节搬进来**。
 *
 * Δ 的颜色（`.delta-red` / `.delta-green` / `.delta-gray`）住在全局，与个案详情那张
 * 总分卡共用同一处定义：两处各写一份就是两个定义，而它们漂了不会有任何东西报错。 */
.spark-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

/* Δ 比维度名弱一档——它是注解，不是第二个标题。`tabular-nums` 让八个格子里的数字
   等宽：`+8%` 与 `-12%` 在各自格子右端对齐时，不会一个宽一个窄。 */
.spark-delta {
  font-size: 0.72rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
</style>

