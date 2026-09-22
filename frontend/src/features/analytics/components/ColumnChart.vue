<script setup lang="ts">
/** 柱形图（内联 SVG）。 */
import { computed } from 'vue'
const props = defineProps<{
  labels: string[]
  values: number[]
  max?: number
  suffix?: string
  colors?: string[]
}>()
const maximum = computed(() => props.max || Math.max(...props.values, 1) * 1.2)
const W = 560, H = 224
</script>
<template>
  <div class="chart" role="img" aria-label="柱形统计图">
    <svg :viewBox="`0 0 ${W} ${H}`">
      <line v-for="t in [0,.25,.5,.75,1]" :key="'g'+t"
        :y1="14+(H-56)*(1-t)" :y2="14+(H-56)*(1-t)"
        x1="36" x2="525" stroke="#e7eef8"/>
      <text v-for="t in [0,.25,.5,.75,1]" :key="'gv'+t"
        :y="14+(H-56)*(1-t)+4" x="30" text-anchor="end" font-size="11" fill="#8193a9">
        {{ Math.round(maximum*t) }}
      </text>
      <template v-for="(label,i) in labels" :key="label">
        <rect :x="36+(i+.5)*(W-56)/labels.length-(W-56)/labels.length*.26"
          :y="H-46-values[i]/maximum*(H-56)"
          :width="(W-56)/labels.length*.52"
          :height="values[i]/maximum*(H-56)" rx="4"
          :fill="colors?.[i]||'#428df1'"/>
        <circle v-if="values[i] === 0" :cx="36+(i+.5)*(W-56)/labels.length" :cy="H-46"
          r="3.5" :fill="colors?.[i]||'#428df1'"/>
        <text :x="36+(i+.5)*(W-56)/labels.length"
          :y="H-46-values[i]/maximum*(H-56)-6"
          font-size="12" text-anchor="middle" fill="#244769">
          {{ values[i] }}{{ suffix }}
        </text>
        <text :x="36+(i+.5)*(W-56)/labels.length" :y="H-8"
          font-size="11" text-anchor="middle" fill="#667b91">{{ label }}</text>
      </template>
    </svg>
  </div>
</template>

<style scoped>
.chart { width: 100%; min-width: 0; overflow: hidden }
.chart svg { width: 100%; height: auto; display: block; overflow: hidden }
@media(max-width: 600px) {
  .chart { overflow-x: auto; scrollbar-width: thin }
  .chart svg { width: 520px; max-width: none }
}
</style>
