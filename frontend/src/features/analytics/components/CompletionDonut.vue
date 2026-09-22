<script setup lang="ts">
/** 完成度环形图（CSS conic-gradient）。 */
import { computed } from 'vue'
const props = defineProps<{ completed: number; target: number }>()
const pct = computed(() => props.target ? props.completed / props.target * 100 : 0)
</script>
<template>
  <div class="donut-wrap">
    <div class="donut" :style="{'--progress': pct+'%'}">
      <div><strong>{{ completed.toLocaleString('zh-CN') }}</strong><small>已完成（{{ pct.toFixed(1) }}%）</small></div>
    </div>
    <div class="donut-legend">
      <p><span class="legend-dot blue"/>已完成：{{ completed.toLocaleString('zh-CN') }}人</p>
      <p><span class="legend-dot gray"/>未完成：{{ (target - completed).toLocaleString('zh-CN') }}人</p>
      <p class="muted">目标 {{ target.toLocaleString('zh-CN') }} 人</p>
    </div>
  </div>
</template>

<style scoped>
.donut-wrap { display: flex; gap: 25px; justify-content: space-evenly; align-items: center; min-height: 230px }
.donut { position: relative; width: 180px; height: 180px; border-radius: 50%; background: conic-gradient(#3183eb 0 var(--progress), #e3ebf5 var(--progress) 100%); display: grid; place-items: center; flex-shrink: 0 }
.donut > div { width: 126px; height: 126px; background: #fff; border-radius: 50%; display: flex; flex-direction: column; align-items: center; justify-content: center }
.donut strong { font-size: 25px; font-weight: 750 }
.donut small { font-size: 11px; text-align: center; color: #667c96 }
.donut-legend { min-width: 115px; display: grid; gap: 8px }
.donut-legend p { margin: 0 }
.muted { color: #687c93; font-size: 12px }
.legend-dot { width: 10px; height: 10px; display: inline-block; border-radius: 2px; margin-right: 6px }
.legend-dot.blue { background: #378af0 }
.legend-dot.gray { background: #d8e4f3 }
@media(max-width: 520px) {
  .donut-wrap { flex-direction: column; gap: 16px; min-height: 0; padding: 12px 0 }
  .donut { width: 156px; height: 156px }
  .donut > div { width: 108px; height: 108px }
  .donut-legend { min-width: 0 }
}
</style>
