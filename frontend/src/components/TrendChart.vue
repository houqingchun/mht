<script setup lang="ts">
/**
 * 历次测评的 MHT 总分折线。**内联 SVG，不引图表库**（与仓库无 axios / 无 Pinia 同一取舍）。
 *
 * 三件事是刻意这么做的：
 *
 * 1. **不画 56/65 分段参考线。** 总分分段阈值属于量表规则版本（CLAUDE.md §6），
 *    随 `scale_rule.config_json` 走、按 `rule_version` 逐场不同，前端根本拿不到。
 *    在这里写死两个数字，等于把「当时按什么标准判的」抄成一份会过期的副本。
 *    分段信息改由**每个点自己的颜色**携带：`total_level` 是后端按该场的规则版本算好的。
 * 2. **y 轴上限不写死。** 取观测到的最高分向上取整到 10 的倍数，下限 10——
 *    一个最高 12 分的学生不该被画在一张 0–100 的图底部、挤成一条线。
 * 3. **x 轴用施测日期**（`submitted_at` 的日期部分），不猜「2026 秋季」这种学期名：
 *    学期名是学校口径，系统里没有这个字段，猜错比不写更糟。
 *
 * 图表里每一段可见文字都必须走 `levelLabel`——`e2e/vocabulary.spec.ts` 扫的是页面的
 * `innerText`，而 SVG 的 `<text>` 也在 innerText 里，漏一个码就会让它变红。
 */
import { computed } from 'vue'
import { levelLabel, levelTone } from '../services/labels'

interface HistoryPoint {
  session_id: number
  submitted_at: string | null
  total_score: number | null
  total_level: string | null
}

interface ScoredPoint extends HistoryPoint {
  total_score: number
}

const props = defineProps<{ history: HistoryPoint[] }>()

/**
 * 只画**有分数的**那些场：没有结果行的会话（迁移前只剩会话行）画成 0 分是在撒谎。
 * 缺值直接不画点，让空白自己说明那一场没有结果——比编一个 0 分诚实。
 */
const points = computed<ScoredPoint[]>(() =>
  props.history.filter((entry): entry is ScoredPoint => entry.total_score !== null)
)

const W = 560
const H = 224
const PAD = { top: 20, right: 20, bottom: 36, left: 40 }

const plotW = W - PAD.left - PAD.right
const plotH = H - PAD.top - PAD.bottom

const yMax = computed(() =>
  Math.max(10, Math.ceil(Math.max(...points.value.map(entry => entry.total_score), 1) / 10) * 10)
)

function xOf(index: number) {
  const count = points.value.length
  if (count <= 1) return PAD.left + plotW / 2
  return PAD.left + (index * plotW) / (count - 1)
}

function yOf(score: number) {
  return PAD.top + plotH * (1 - score / yMax.value)
}

const ticks = computed(() =>
  [0, yMax.value / 2, yMax.value].map(value => ({
    value,
    y: yOf(value),
    // 半刻度可能是小数（yMax=10 时是 5），去掉尾随的 .0
    label: String(Number(value.toFixed(1)))
  }))
)

const polyline = computed(() =>
  points.value.map((entry, index) => `${xOf(index)},${yOf(entry.total_score)}`).join(' ')
)

const areaPath = computed(() => {
  if (points.value.length < 2) return ''
  const baseline = PAD.top + plotH
  const inner = points.value.map((entry, index) => `L ${xOf(index)} ${yOf(entry.total_score)}`).join(' ')
  return `M ${xOf(0)} ${baseline} ${inner} L ${xOf(points.value.length - 1)} ${baseline} Z`
})

/** 点多了日期会挤在一起，只留首尾两个刻度；六个以内全标。 */
const labelledX = computed(() => {
  const count = points.value.length
  if (count <= 6) return new Set(points.value.map((_, index) => index))
  return new Set([0, count - 1])
})

/** 图例只列**这张图上真的出现过**的分类，顺序按首次出现。 */
const legend = computed(() => {
  const seen: string[] = []
  for (const entry of points.value) {
    if (entry.total_level && !seen.includes(entry.total_level)) seen.push(entry.total_level)
  }
  return seen.map(level => ({ level, label: levelLabel(level), tone: levelTone(level) }))
})

function dateOf(value: string | null) {
  return value ? value.slice(0, 10) : '—'
}
</script>

<template>
  <div v-if="!points.length" class="empty">暂无历次测评数据</div>
  <div v-else>
    <svg
      class="chart"
      :viewBox="`0 0 ${W} ${H}`"
      role="img"
      :aria-label="`历次测评总分折线，共 ${points.length} 次`"
    >
      <g>
        <line
          v-for="tick in ticks"
          :key="`grid-${tick.value}`"
          class="chart-grid-line"
          :x1="PAD.left"
          :x2="PAD.left + plotW"
          :y1="tick.y"
          :y2="tick.y"
        />
        <text
          v-for="tick in ticks"
          :key="`tick-${tick.value}`"
          class="chart-tick"
          :x="PAD.left - 8"
          :y="tick.y + 4"
          text-anchor="end"
        >
          {{ tick.label }}
        </text>
        <line
          class="chart-baseline"
          :x1="PAD.left"
          :x2="PAD.left + plotW"
          :y1="PAD.top + plotH"
          :y2="PAD.top + plotH"
        />
      </g>

      <path v-if="points.length > 1" class="chart-area" :d="areaPath" />
      <polyline v-if="points.length > 1" class="chart-line" :points="polyline" />

      <g v-for="(entry, index) in points" :key="entry.session_id">
        <circle
          :class="['chart-dot', levelTone(entry.total_level)]"
          :cx="xOf(index)"
          :cy="yOf(entry.total_score)"
          r="5.5"
        />
        <text
          class="chart-point-value"
          :x="xOf(index)"
          :y="yOf(entry.total_score) - 12"
          text-anchor="middle"
        >
          {{ entry.total_score }}
        </text>
        <text
          v-if="labelledX.has(index)"
          class="chart-tick"
          :x="xOf(index)"
          :y="PAD.top + plotH + 20"
          text-anchor="middle"
        >
          {{ dateOf(entry.submitted_at) }}
        </text>
      </g>
    </svg>

    <div class="chart-legend">
      <span v-for="item in legend" :key="item.level" class="chart-legend-item">
        <span :class="['chart-legend-swatch', item.tone]"></span>{{ item.label }}
      </span>
    </div>
  </div>
</template>
