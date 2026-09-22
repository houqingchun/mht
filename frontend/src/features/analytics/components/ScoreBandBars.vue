<script setup lang="ts">
/**
 * 关注等级分布（总分区间三档）—— **HTML/CSS 柱形图，不是 SVG**。
 *
 * 不用现成那两个图表组件（`ColumnChart` / `HorizontalBars`）的理由是这一页的图表格子在三列
 * 布局里只有 380 多像素（`@media(max-width:1400px)` 以下才塌成一列），而它们的 `viewBox`
 * 写死 560 / 740 宽——缩到 380 是 0.68 倍，12px 的字变成 8px；更要紧的是 SVG 的 `<text>`
 * **不换行**，档名连同下面那句区间文字会被图表容器上的 `overflow:hidden` 静默裁掉半截，
 * 屏幕上只是少了几个字，而没有任何东西会报错。用 HTML 元素画，字号与换行都是真的。
 *
 * **三档的中文就是关注等级那一套**（`levelLabel`：一般观察 / 需要关注 / 重点关注）。
 * 这里一度另有一张 `SCORE_BAND_LABELS`（正常 / 心理状态欠佳或有问题倾向 / 心理问题倾向较严重），
 * 2026-09-22 删掉了：同一个码在一屏上不能有两个名字（§3「高度关注」那条裁决的同一句话）。
 * 这一档说的是**分数区间**，不是给人下的判断——那句话由 `scoreBandRangeText` 取规则版本里的
 * 「N~M 分」写在名字下面回答（§6：阈值随 `scale_rule.config_json` 走，视图里不写死）。
 *
 * 柱高按「本档人数 ÷ 最高的一档 ÷ 1.2」算（与 `ColumnChart` 的 `max * 1.2` 同一个口径：
 * 最高的那条离顶还差两成，永远压不到它上面那行人数）。占比写在一旁的**文字**里，
 * 不靠高度暗示——高度只回答「哪一档人多」这个问题。
 *
 * 颜色的判据是 `levelTone`（红 / 琥珀 / 灰），与同一页上的药丸、`KpiCard` 是同一套语义色：
 * 一页之上只有一种颜色语言，所以「一般观察」在这里也是灰的，不再自成一档绿色。
 */
import { computed } from 'vue'
import { LEVEL_LABELS, levelLabel, levelTone, scoreBandRangeText } from '../../../services/labels'

const props = defineProps<{
  /** 服务端按人算好的三档计数（`overview.level_distribution`），次序已是从轻到重。 */
  items: Array<{ level_code: string; student_count: number; rate: number | null }>
  /** 这批结果**自己的**规则版本那三段区间（`scale.total_bands`），取不到时是 `null`。 */
  totals?: Array<{ code: string; min: number; max: number }> | null
  /** 分母 = `sample_quality.n_evaluable`；为 0 时整块换成空态。 */
  total: number
}>()

/** `levelTone` 的三档在这里各对应一个色值（全站没有共享的 tone→hex 表，颜色由各处自己落地）。 */
const TONE_COLORS: Record<string, string> = {
  red: '#c83c43',
  amber: '#a86600',
  gray: '#647386'
}

/**
 * 认不出的码回落到这一页的主体蓝，而**不是** `levelTone` 的灰——灰是「一般观察」的颜色，
 * 两者长得一样的话，一个漏掉的码看起来就像一档正常的结果（漏码要看得见）。
 */
function columnColor(code: string): string {
  if (!LEVEL_LABELS[code]) return '#2f6edb'
  return TONE_COLORS[levelTone(code)]
}

/** 上方留出两成：最高的那根柱子到 83%，不会贴住上沿，也压不到它上面那行人数。 */
const maximum = computed(() => Math.max(...props.items.map(i => i.student_count), 1) * 1.2)
/**
 * 空态的判据是「**有没有一个人落进这三档**」，不是「服务端发了几档」。
 *
 * 服务端**永远**发三档（`level_order = list(LEVEL_BANDS)`），所以 `items.length` 在这里恒为 3
 * ——拿它当判据的话下面那个空态是一段**不可达**的代码，而真正会出现的那一屏（选了一个还没人
 * 交卷的任务）会画出三条 0 人的空柱子，看起来与「全部一般观察」一模一样。
 * `0` 与「没有数据」必须长得不一样（§11 那条 `None` vs `0` 的同一条）。
 */
const hasAnyCount = computed(() => props.items.some(i => i.student_count > 0))
/** 有任意一档没拿到比率 → 下面那句「为什么不给百分比」要说出来（§11）。 */
const anyRateSuppressed = computed(() => props.items.some(i => i.rate == null))

const rows = computed(() => props.items.map(item => ({
  code: item.level_code,
  label: levelLabel(item.level_code),
  range: scoreBandRangeText(props.totals, item.level_code),
  count: item.student_count,
  color: columnColor(item.level_code),
  height: `${Math.round(item.student_count / maximum.value * 100)}%`,
  // 比率在分母小于 MIN_COHORT_FOR_AGGREGATE 时是 `null`，**不许 `?? 0` 把它抹平**：
  // `0.0%` 是一句「这一档一个都没有」的断言，而三四个人的分母算出来的百分比是**反推**。
  // 两句话必须长得不一样（§11），所以这里分成两支，不是同一个 0。
  rateText: item.rate == null ? '样本过小' : `${item.rate.toFixed(1)}%`
})))
</script>

<template>
  <div v-if="hasAnyCount" class="band-chart">
    <!-- 柱子与人数在**同一列**里上下排：`N 人` 是自然高度、柱子在剩下的空间里按百分比长，
         所以柱子无论多高都压不到那行字——把人数画进柱子内部才会撞上这个边界。 -->
    <div class="band-plot">
      <div v-for="row in rows" :key="row.code" class="band-col">
        <div class="band-figure"><strong>{{ row.count }}</strong> 人</div>
        <div class="band-track">
          <div
            v-if="row.count > 0"
            class="band-column"
            :style="{ height: row.height, background: row.color }"
            aria-hidden="true"/>
        </div>
      </div>
    </div>
    <!-- 轴与图共用一套列宽（同一个 `.band-col` + 同一个 `gap`），两边因此逐列对齐。 -->
    <div class="band-axis">
      <div v-for="row in rows" :key="row.code" class="band-col">
        <div class="band-label">{{ row.label }}</div>
        <div v-if="row.range" class="band-range">{{ row.range }}</div>
        <div class="band-rate" :class="{ suppressed: row.rateText === '样本过小' }">{{ row.rateText }}</div>
      </div>
    </div>
    <p class="band-foot">
      三档按人去重（每人取最近一场已计算结果），合计 {{ total }} 人，等于可评价样本；占比的分母也是它。
      <template v-if="anyRateSuppressed">分母过小时不给百分比——三五个人的比例等于点名，此处只给人数。</template>
    </p>
  </div>
  <!-- 空态是一句关于数据的话，不是一块占位（§14）：它同时回答「为什么没有」与「什么之后会有」。
       判据见 `hasAnyCount`——**三条 0 人的柱子不是空态**，它是一句「一个都没落进这三档」的
       断言，而这一屏要说的是「还没有数据」。 -->
  <div v-else class="data-empty">
    当前所选任务还没有可分档的测评结果，因此没有分档人数可展示。完成测评并生成评分之后，这里会按总分区间给出三档人数。
  </div>
</template>

<style scoped>
.band-chart { display: flex; flex-direction: column; gap: 10px }
/* 三条淡淡的横向刻度线。**没有坐标轴数字**是有意的：这一张图回答的是「哪一档人多」，
   绝对数在每根柱子上方，写一套纵轴刻度只会给出第二个说法。 */
.band-plot {
  display: flex;
  /* `stretch`（默认值，写出来是为了说明它不能改）——**不是 `flex-end`**。
     写成 `flex-end` 时每条 `.band-col` 的高度退回「内容高度」，而它的内容里那条轨道是
     `flex-basis: 0`（没有内容），于是整列塌成十几像素、轨道 0 高，柱子的百分比高度
     无处可算——**柱子一根都看不见，而内联样式里照样写着 `height: 83%`**。
     柱子贴底这件事归 `.band-track` 的 `align-items: flex-end` 管。 */
  align-items: stretch;
  gap: 8px;
  height: 150px;
  padding: 0 2px;
  background-image: repeating-linear-gradient(to top, #eef2f7 0 1px, transparent 1px 25%);
}
.band-axis { display: flex; gap: 8px; padding: 0 2px }
/* 上下两块共用这一个形状，所以列宽与间距不可能各说各话。 */
.band-col { display: flex; flex-direction: column; flex: 1 1 0; min-width: 0 }
.band-figure { font-size: 12px; color: #3e5877; text-align: center; white-space: nowrap }
.band-figure strong { color: #142b45; font-size: 15px }
/* 柱子长在剩下的空间里：`flex-basis: 0` + `min-height: 0` 让这个盒子的高度是确定的，
   里面的百分比高度才有东西可算，而所有轨道的底边就是同一条基线。 */
.band-track { flex: 1 1 0; min-height: 0; display: flex; align-items: flex-end; justify-content: center }
.band-column { width: 100%; max-width: 54px; min-height: 3px; border-radius: 5px 5px 0 0; transition: height .2s }
.band-label { color: #244769; font-size: 13px; font-weight: 650; text-align: center; line-height: 1.5 }
.band-range { color: #8193a9; font-size: 12px; text-align: center }
.band-rate { color: #687c93; font-size: 12px; text-align: center }
/* 「样本过小」不是一个小数字，它是另一种东西：靠颜色与斜体把它和百分比分开。 */
.band-rate.suppressed { color: #a86600; font-style: italic }
.band-foot { margin: 2px 0 0; color: #708198; font-size: 12px; line-height: 1.7 }
/* 与 GradesPage / OverviewPage 的 `.data-empty` 同一副长相（那是这一套页面里「关于数据的
   一句话」的既有形状），组件自带一份是因为这个组件自己决定要不要换成空态。 */
.data-empty { padding: 34px 18px; border: 1px dashed #cbd8e2; border-radius: 8px; background: #f8fafc; color: #687c93; text-align: center; line-height: 1.7 }
</style>
