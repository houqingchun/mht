<script setup lang="ts">
/** 分组柱形图（内联 SVG）。 */
import { computed } from 'vue'
const props = defineProps<{
  labels: string[]
  seriesA: number[]
  seriesB: number[]
  max?: number
  suffix?: string
  labelA?: string
  labelB?: string
  colorA?: string
  colorB?: string
}>()
const maximum = computed(() => props.max || Math.max(...props.seriesA, ...props.seriesB, 1) * 1.18)
const W = 720, H = 270, PL = 56, PR = 18, PT = 22, PB = 58
const plot = W - PL - PR
/** `group` / `bw` 随 labels 数量响应式变化——如果图表在运行时接收不同数量的 labels，
 *  柱宽不会停留在初始值。当前报表页的 labels 数量在加载后不变，这是防御性修复。 */
const group = computed(() => plot / props.labels.length)
const bw = computed(() => Math.min(22, group.value * 0.26))
</script>
<template>
  <div class="chart" role="img" aria-label="分组柱形图">
    <svg :viewBox="`0 0 ${W} ${H}`">
      <line v-for="t in [0,.25,.5,.75,1]" :key="t"
        :y1="PT+(H-PT-PB)*(1-t)" :y2="PT+(H-PT-PB)*(1-t)"
        :x1="PL" :x2="W-PR" stroke="#e7eef8"/>
      <text v-for="t in [0,.25,.5,.75,1]" :key="'v'+t"
        :y="PT+(H-PT-PB)*(1-t)+4" :x="PL-7" text-anchor="end" font-size="11" fill="#8193a9">
        {{ (maximum*t).toFixed(suffix==='%'?0:1) }}{{ suffix }}
      </text>
      <template v-for="(label,i) in labels" :key="label">
        <rect :x="PL+(i+.5)*group-bw-2"
          :y="H-PB-seriesA[i]/maximum*(H-PT-PB)"
          :width="bw" :height="seriesA[i]/maximum*(H-PT-PB)" rx="3"
          :fill="colorA||'#3988ee'"/>
        <rect :x="PL+(i+.5)*group+2"
          :y="H-PB-seriesB[i]/maximum*(H-PT-PB)"
          :width="bw" :height="seriesB[i]/maximum*(H-PT-PB)" rx="3"
          :fill="colorB||'#f2a149'"/>
        <text :x="PL+(i+.5)*group" :y="H-8"
          font-size="10" text-anchor="middle" fill="#667b91">{{ label }}</text>
      </template>
      <g :transform="`translate(${W-178},8)`">
        <rect width="9" height="9" rx="2" :fill="colorA||'#3988ee'"/><text x="14" y="9" font-size="11" fill="#58708a">{{ labelA||'本组' }}</text>
        <rect x="74" width="9" height="9" rx="2" :fill="colorB||'#f2a149'"/><text x="88" y="9" font-size="11" fill="#58708a">{{ labelB||'对比组' }}</text>
      </g>
    </svg>
  </div>
</template>

<style scoped>
.chart { width: 100%; min-width: 0; overflow: hidden }
.chart svg { width: 100%; height: auto; display: block; overflow: hidden }
@media(max-width: 600px) {
  .chart { overflow-x: auto; scrollbar-width: thin }
  .chart svg { width: 620px; max-width: none }
}
</style>
