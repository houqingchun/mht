<script setup lang="ts">
/** 横向条形图（内联 SVG）。 */
import { computed } from 'vue'
const props = defineProps<{
  labels: string[]
  values: number[]
  max?: number
  suffix?: string
  color?: string
}>()
const maximum = computed(() => props.max || Math.max(...props.values, 1) * 1.15)
const W = 740, H = computed(() => Math.max(200, props.labels.length * 32 + 30))
</script>
<template>
  <div class="chart" role="img" aria-label="横向条形分析图">
    <svg :viewBox="`0 0 ${W} ${H}`">
      <line v-for="t in [0,.25,.5,.75,1]" :key="'g'+t"
        :x1="102+t*(W-137)" y1="12" :x2="102+t*(W-137)" :y2="H-22" stroke="#e7eff8"/>
      <text v-for="t in [0,.25,.5,.75,1]" :key="'gv'+t"
        :x="102+t*(W-137)" :y="H-3" font-size="11" text-anchor="middle" fill="#8193a9">
        {{ suffix === '%' ? Math.round(maximum*t)+'%' : Math.round(maximum*t) }}
      </text>
      <text v-for="(label,i) in labels" :key="label"
        :x="93" :y="12+i*(H-34)/labels.length+(H-34)/labels.length*.65"
        text-anchor="end" font-size="12" fill="#45607c">{{ label }}</text>
      <rect v-for="(_,i) in labels" :key="'r'+i"
        :x="102" :y="12+i*(H-34)/labels.length+(H-34)/labels.length*.15"
        :width="Math.max(0, values[i]/maximum*(W-137))" :height="(H-34)/labels.length*.62" rx="4"
        :fill="color||'#3e8df2'"/>
      <text v-for="(_,i) in labels" :key="'n'+i"
        :x="Math.min(W-38, 102+values[i]/maximum*(W-137)+7)"
        :y="12+i*(H-34)/labels.length+(H-34)/labels.length*.65"
        font-size="12" fill="#254d83">{{ values[i] }}{{ suffix }}</text>
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
