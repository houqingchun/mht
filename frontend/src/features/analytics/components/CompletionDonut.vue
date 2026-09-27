<script setup lang="ts">
/**
 * 完成度环形图（CSS conic-gradient）。
 *
 * `target` 是**分母**，调用方传的是「实际应测人数」（`sample_quality.eligible_count`
 * ＝任务目标 − 请假 / 免测 / 已排除，CLAUDE.md §29），**不是任务目标人数**。
 * 传任务目标会把一名免测学生画进「尚未完成测评」那一格，而按现行规则他不该在分母里
 * （§5.20.4：「尚未完成测评」与「不可评价 / 无有效评分结果」是两件事）。
 *
 * **圆心只印人数，不印百分比**（2026-09-27 §5.20）。`pct` 仍然算着，但它只喂
 * `conic-gradient` 的弧长——那是**几何**，前端算与后端算在像素上分不出差别；
 * 而印出来的字符是两个数：服务端 `coverage_rate` 的分子是 `n_evaluable`（可评价样本），
 * 圆心这里的分子是 `completed`（已完成），两者在 `n_evaluable != completed` 时不相等。
 * 印一个自己算的百分比，就会在同一屏上与卡 3 的覆盖率各写一个数而没有任何东西会红
 * ——与 `CoverageColumns.vue` 拒绝重算覆盖率是同一条判据（那条注释记着为什么）。
 * 要读比值的读者，旁边三行里人数与分母都在，自己就能读出来。
 */
import { computed } from 'vue'
const props = defineProps<{ completed: number; target: number }>()
const pct = computed(() => props.target ? props.completed / props.target * 100 : 0)
/** 「尚未完成」＝应测里还没交卷的那些。`Math.max(0, …)` 兜的是「完成数多于应测」这种
 *  不该出现、但一旦出现就会印出一个负数人数的情形（补发 / 标记参与状态之后可能算出来）。 */
const remaining = computed(() => Math.max(0, props.target - props.completed))
</script>
<template>
  <div class="donut-wrap">
    <div class="donut" :style="{'--progress': pct+'%'}">
      <div><strong>{{ completed.toLocaleString('zh-CN') }}</strong><small>已完成<br/>实际应测 {{ target.toLocaleString('zh-CN') }} 人</small></div>
    </div>
    <div class="donut-legend">
      <p><span class="legend-dot blue"/>已完成：{{ completed.toLocaleString('zh-CN') }}人</p>
      <p><span class="legend-dot gray"/>尚未完成：{{ remaining.toLocaleString('zh-CN') }}人</p>
      <p class="muted">实际应测 {{ target.toLocaleString('zh-CN') }} 人（请假 / 免测 / 已排除不计入）</p>
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
