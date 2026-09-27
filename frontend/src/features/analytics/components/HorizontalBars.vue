<script setup lang="ts">
/**
 * 横向条形图（内联 SVG）。
 *
 * **`values` 里允许 `null`，而 `null` 与 `0` 必须长得不一样**（§5.20.4）。`0` 是
 * 「有可评价样本、算出来确实是 0」；`null` 是「服务端按 `_report_rate` 的抑制规则
 * **没有发**这个数」（分母 < `MIN_COHORT_FOR_AGGREGATE` 时，CLAUDE.md §11）。
 * 此前这里收的是 `number[]`，唯一的调用方只好写 `d.high_score_rate || 0` 把两者
 * 抹成一个——于是一个被抑制的维度画成一根 0 长条、旁边印一个 `0%`，屏幕上看起来
 * 像「这个维度一个高分都没有」。措辞取 `ScoreBandBars` 那一处既有的「样本不足」，
 * 不另造一个说法（同一件事在屏幕上只有一种写法）。
 */
import { computed } from 'vue'
const props = defineProps<{
  labels: string[]
  values: Array<number | null>
  max?: number
  suffix?: string
  color?: string
}>()
/**
 * 刻度上限只拿**有值的那些**算：`Math.max(...[null])` 得 `NaN`，而 `NaN` 参与的任何
 * 宽度计算都会让整张图一个像素都画不出来（`<rect>` 的 width 是 `NaN` 时浏览器直接跳过它）。
 * 一个都有值时它就等于从前的写法，所以既有调用方逐像素不变。
 */
const maximum = computed(() => props.max || Math.max(...props.values.filter((v): v is number => v != null), 1) * 1.15)
/** `null` 的条长按 0 画——它没有长度可画；**但数字那一格不印 0**，见模板最后一段。 */
function barValue(v: number | null) { return v == null ? 0 : v }
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
        :width="Math.max(0, barValue(values[i])/maximum*(W-137))" :height="(H-34)/labels.length*.62" rx="4"
        :fill="color||'#3e8df2'"/>
      <text v-for="(_,i) in labels" :key="'n'+i"
        :x="Math.min(W-38, 102+barValue(values[i])/maximum*(W-137)+7)"
        :y="12+i*(H-34)/labels.length+(H-34)/labels.length*.65"
        font-size="12" fill="#254d83">{{ values[i] == null ? '样本不足' : barValue(values[i]) + (suffix || '') }}</text>
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
