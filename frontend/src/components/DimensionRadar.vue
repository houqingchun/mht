<script setup lang="ts">
/**
 * 八维度雷达图（内联 SVG，不引图表库）。
 *
 * **按百分比归一化，不画原始分。** 八个维度题数不等（两个 15 题、其余 10 题），
 * 用原始分画出来的面积里，15 题的维度天然多占一半——图上看起来「更严重」，
 * 实际只是它多问了五道题。归一化的分母是每个维度自己的 `max_score`，
 * 复用 `dimensionPercent`（条形图用了很久的那一个，含 max_score 缺失时的兜底）；
 * 轴标签同时保留「得分/题数」，读的人仍然看得到原始分。
 *
 * 顶点颜色走 `dimensionTone`，与条形图的 `.bar i` 共用同一处映射——
 * 两个图对同一个维度不可能给出两种颜色。
 */
import { computed } from 'vue'
import { dimensionLabel, dimensionPercent, dimensionTone } from '../services/labels'

interface Dimension {
  dimension_code: string
  score: number
  max_score: number
  level: string
}

const props = defineProps<{ dimensions: Dimension[] }>()

const W = 520
const H = 400
const CX = W / 2
const CY = H / 2 - 6
const R = 128
const LABEL_GAP = 14

const axes = computed(() =>
  props.dimensions.map((dimension, index) => {
    const count = props.dimensions.length
    // 从正上方开始、顺时针均分
    const angle = (-90 + (360 / count) * index) * (Math.PI / 180)
    const percent = dimensionPercent(dimension.score, dimension.max_score)
    const cos = Math.cos(angle)
    const sin = Math.sin(angle)
    return {
      code: dimension.dimension_code,
      label: dimensionLabel(dimension.dimension_code),
      score: dimension.score,
      maxScore: dimension.max_score || '—',
      tone: dimensionTone(dimension.level),
      axisX: CX + Math.cos(angle) * R,
      axisY: CY + Math.sin(angle) * R,
      valueX: CX + cos * R * (percent / 100),
      valueY: CY + sin * R * (percent / 100),
      labelX: CX + cos * (R + LABEL_GAP),
      labelY: CY + sin * (R + LABEL_GAP),
      // 正上/正下方居中对齐，左右两侧分别向内外对齐，否则标签会压到图上
      anchor: Math.abs(cos) < 0.25 ? 'middle' : cos > 0 ? 'start' : 'end',
      // 顶部那条轴的「得分/题数」要画在名字**上面**：往下走会正好压在顶点上。
      scoreDy: sin < -0.5 ? -14 : 14
    }
  })
)

const rings = [0.25, 0.5, 0.75, 1]

function ringPoints(scale: number) {
  return axes.value
    .map(axis => `${CX + (axis.axisX - CX) * scale},${CY + (axis.axisY - CY) * scale}`)
    .join(' ')
}

const shapePoints = computed(() =>
  axes.value.map(axis => `${axis.valueX},${axis.valueY}`).join(' ')
)
</script>

<template>
  <div v-if="!dimensions.length" class="empty">暂无维度结果</div>
  <svg
    v-else
    class="chart"
    :viewBox="`0 0 ${W} ${H}`"
    role="img"
    aria-label="八维度得分雷达图，各维度按自己的题数归一化"
  >
    <!-- 同心环与轴线 -->
    <polygon
      v-for="scale in rings"
      :key="`ring-${scale}`"
      :class="['radar-ring', { outer: scale === 1 }]"
      :points="ringPoints(scale)"
    />
    <line
      v-for="axis in axes"
      :key="`axis-${axis.code}`"
      class="radar-axis"
      :x1="CX"
      :y1="CY"
      :x2="axis.axisX"
      :y2="axis.axisY"
    />

    <polygon class="radar-shape" :points="shapePoints" />

    <g v-for="axis in axes" :key="`value-${axis.code}`">
      <circle :class="['radar-dot', axis.tone || 'low']" :cx="axis.valueX" :cy="axis.valueY" r="4" />
      <text class="radar-name" :x="axis.labelX" :y="axis.labelY" :text-anchor="axis.anchor">
        {{ axis.label }}
      </text>
      <text class="radar-score" :x="axis.labelX" :y="axis.labelY + axis.scoreDy" :text-anchor="axis.anchor">
        {{ axis.score }}/{{ axis.maxScore }}
      </text>
    </g>
  </svg>
</template>
