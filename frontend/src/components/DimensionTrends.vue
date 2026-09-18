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
      last: percents[percents.length - 1]
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
      <div class="spark-title">{{ item.label }}</div>
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
