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
 * **两种版式、一个组件**：`layout="columns"` 是纵向三根柱，`layout="bars"` 是横向三行条。
 * 三处「关注等级分布」是**同一个业务指标，只是统计范围不同**（筛查关注概览 / 班级维度画像 /
 * 各年级对比），所以它们共用同一份等级顺序、同一套状态色、同一种「N 人 + 百分比」写法
 * ——那三样在这里各只有一处定义。另起一个 `AttentionLevelDistribution.vue` 就是给同一件事
 * 开出第二个实现（§3 那条「同一处只许有一个定义」在视图层的同一句话）。
 *
 * **★ 纵向那版今天没有调用方**（2026-09-27 §5.19）。它在 §5.17 之前是**唯一**的一版：
 * 年级对比页的「各年级关注等级分布」从写下那天起就用它，这个组件当初也是为它写的。
 * §5.17 给班级维度画像与筛查关注概览加了横向版，§5.19 让年级对比页也换过去——三处
 * 并排或并列的图现在都是横向条，于是 `columns` 这一支**一处调用都没有了**。
 * **没有删它，是有意的**：删要连带动 `maximum` computed、`ordered` 那一支、`rows` 的
 * `height`、模板里那个 `v-else`，以及六条只服务纵向版的 scoped CSS；那是一次独立的
 * 改动，不属于本节这两个优化点（§5.18「禁止为了『完成任务』而制造无必要代码变更」）。
 * 留着的代价是显式的：**它是一个能编译、能渲染、但屏幕上一条路都到不了的版式**，
 * 所以这条注释存在的意义就是让下一个看见它的人知道「不是漏删」。
 * 真要复活纵向版时，那六条 CSS 与 `maximum` 都还在，把 `layout` 去掉即可。
 *
 * **两版的档序不同，而这是有意的**：横向那版按 `LEVEL_ORDER` 排（重点关注 → 需要关注 →
 * 一般观察，**从重到轻**，§3 那张表的规范序）；纵向那版沿用服务端次序（`LEVEL_BANDS`，
 * 从轻到重）——那是它当初的读法，与它那一版条长的口径配套。今天没有调用方，所以这个
 * 差别在屏幕上只活在横向那一支上。
 *
 * **三档的中文就是关注等级那一套**（`levelLabel`：一般观察 / 需要关注 / 重点关注）。
 * 这里一度另有一张 `SCORE_BAND_LABELS`（正常 / 心理状态欠佳或有问题倾向 / 心理问题倾向较严重），
 * 2026-09-22 删掉了：同一个码在一屏上不能有两个名字（§3「高度关注」那条裁决的同一句话）。
 * 这一档说的是**分数区间**，不是给人下的判断——那句话由 `scoreBandRangeText` 取规则版本里的
 * 「N~M 分」写在名字下面回答（§6：阈值随 `scale_rule.config_json` 走，视图里不写死）。
 *
 * **纵向版的柱高**按「本档人数 ÷ 最高的一档 ÷ 1.2」算（与 `ColumnChart` 的 `max * 1.2`
 * 同一个口径：最高的那条离顶还差两成，永远压不到它上面那行人数）。占比写在一旁的**文字**
 * 里，不靠高度暗示——高度只回答「哪一档人多」这个问题。（这一支今天没有调用方，见上。）
 *
 * **横向版的条长是另一个口径：本档人数 ÷ 可评价样本**（§5.17）。它与右边那个百分比
 * **同源**——服务端 `rate` 就是 `count / total` 的一次 `round(…, 1)`——所以「条多长」与
 * 「写着几个百分点」是同一件事的两种表达，不可能各说各话。选它而不是「相对最大档」，
 * 是因为横向版会被**并排**放（班级维度画像页的「本班 / 同年级」两格，以及概览页与它）：
 * 各自按本格最大值归一化时，本班的 3 人条会比同年级的 6 人条更长（实测过：211px 对
 * 180px），而人的眼睛一定会横着比过去——比出来是反的。按占比画，跨格跨页天然可比，
 * 「同一个业务指标、只是统计范围不同」这句话在条形上才成立。
 *
 * 颜色的判据是 `levelTone`（红 / 琥珀 / 灰），与同一页上的药丸、`KpiCard` 是同一套语义色：
 * 一页之上只有一种颜色语言，所以「一般观察」在这里也是灰的，不再自成一档绿色。
 */
import { computed } from 'vue'
import { LEVEL_LABELS, LEVEL_ORDER, levelLabel, levelTone, scoreBandRangeText } from '../../../services/labels'

const props = defineProps<{
  /**
   * 服务端按人算好的三档计数（`overview.level_distribution`），发过来的次序是从轻到重。
   * **但不要依赖这个次序**：横向版会按 `LEVEL_ORDER` 重排（见 `ordered`），只有纵向版
   * 原样用它。调用方要做的只是把服务端那三档原样递进来。
   */
  items: Array<{ level_code: string; student_count: number; rate: number | null }>
  /** 这批结果**自己的**规则版本那三段区间（`scale.total_bands`），取不到时是 `null`。 */
  totals?: Array<{ code: string; min: number; max: number }> | null
  /** 分母 = `sample_quality.n_evaluable`；为 0 时整块换成空态。 */
  total: number
  /**
   * 版式。默认 `columns`（纵向柱），`bars` 是横向条。
   * **默认值保持 `columns` 是为了「不传就是从前那样」**（§5.17 加横向版时那一版调用方
   * 还在用它），今天三处调用方**都显式传了 `bars`**，所以这个默认值当前没有读者
   * ——它留着是为了让「不传 layout」这件事仍然有一个确定的行为，而不是 undefined。
   */
  layout?: 'columns' | 'bars'
  /**
   * 统计范围的名字（如「当前数据范围」）。**只有横向版读它**，且只有传了才渲染那半句——
   * 不传的两处（班级画像那一格、年级对比页那一格）在自己的标题里已经写着范围或年级名了
   * （§5.17.4：保留上下文），再写一遍就是同一句话在屏幕上出现两次。
   * 「可评价 N 人」那半句**由组件自己拼**（一处定义），横向版一定会渲染它：三处都要能读到
   * 自己那份样本量。纵向版不渲染这一行（它没有调用方，见上）。
   */
  scopeLabel?: string
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

/** 上方留出两成：最高的那根柱子到 83%，不会贴住上沿，也压不到它上面那行人数。**只有纵向版读它**。 */
const maximum = computed(() => Math.max(...props.items.map(i => i.student_count), 1) * 1.2)

/**
 * 横向版的条长：这一档在**可评价样本**里占多少（见文件头那段）。
 *
 * 分母是 `total`（`sample_quality.n_evaluable`，也就是口径句里那句「合计 N 人」），
 * 与服务端算 `rate` 用的是同一个分母——所以条长与右边那个百分比同源。
 * `total` 为 0 时回落到 0 而不是除零：那时 `hasAnyCount` 已经是假、整块换成空态，
 * 这一支只是不让一个算不出来的数流进内联样式。
 */
function barWidth(count: number): string {
  if (!props.total) return '0%'
  return `${(count / props.total * 100).toFixed(1)}%`
}
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

const isBars = computed(() => props.layout === 'bars')

/**
 * 横向版按 `LEVEL_ORDER` 排（**从重到轻**，§3 那张表的规范序），纵向版沿用服务端次序
 * （`LEVEL_BANDS`，从轻到重）——两版的差别与理由写在文件头。认不出的码排最后，两个方向
 * 都是（§3「空值与认不出的码排在最后」）：它不属于这个序的任何一端，混进中间会让相邻的
 * 两档看起来像是挨着的。`sort` 是稳定的，所以几个认不出的码之间仍是服务端次序。
 */
const ordered = computed(() => {
  if (!isBars.value) return props.items
  const rank = (code: string) => {
    const i = LEVEL_ORDER.indexOf(code)
    return i === -1 ? LEVEL_ORDER.length : i
  }
  return [...props.items].sort((a, b) => rank(a.level_code) - rank(b.level_code))
})

const rows = computed(() => ordered.value.map(item => ({
  code: item.level_code,
  label: levelLabel(item.level_code),
  range: scoreBandRangeText(props.totals, item.level_code),
  count: item.student_count,
  color: columnColor(item.level_code),
  // 两版各有各的「这条有多长」，口径不同（见文件头）：纵向是「相对最高那一档」，
  // 横向是「占可评价样本」。**不是同一个数**，所以是两个字段而不是一个。
  height: `${Math.round(item.student_count / maximum.value * 100)}%`,
  width: barWidth(item.student_count),
  // 比率在分母小于 MIN_COHORT_FOR_AGGREGATE 时是 `null`，**不许 `?? 0` 把它抹平**：
  // `0.0%` 是一句「这一档一个都没有」的断言，而三四个人的分母算出来的百分比是**反推**。
  // 两句话必须长得不一样（§11），所以这里分成两支，不是同一个 0。
  rateText: item.rate == null ? '样本过小' : `${item.rate.toFixed(1)}%`
})))
</script>

<template>
  <div v-if="hasAnyCount" class="band-chart">
    <!-- 横向版（§5.17）：一行一档，条长 = 本档 ÷ 可评价样本，**与右边那个百分比同源**
         （见文件头）。所以并排的两格、以及两页之间，条长都是可以直接横着比的。
         人数与比例**并排写在条右边**，不靠悬浮层（§5.17.3：必须可直接读取）——一排三行，
         眼睛竖着扫就能比出哪一档多。
         0 人的那一档**不画条**，因为紧挨着的 `0 人` 已经把「一个都没有」说清楚了：那与
         「没有数据」是两件事（§11）。条的颜色只落在条本身上，不做底色、不做告警块。 -->
    <div v-if="isBars" class="band-bars">
      <!-- 统计范围 + 可评价样本量。范围名由调用方给（不传就只出样本量），而「可评价 N 人」
           这半句在这里拼一次——三处的这句话因此逐字相同，也不会有第二种写法。 -->
      <p class="band-scope">
        <span v-if="scopeLabel" class="band-scope-name">{{ scopeLabel }}</span>
        <span class="band-scope-count">可评价 <strong>{{ total }}</strong> 人</span>
      </p>
      <div v-for="row in rows" :key="row.code" class="band-row">
        <div class="band-name">
          <span class="band-label">{{ row.label }}</span>
          <span v-if="row.range" class="band-range">{{ row.range }}</span>
        </div>
        <div class="band-bar-track">
          <div
            v-if="row.count > 0"
            class="band-bar"
            :style="{ width: row.width, background: row.color }"
            aria-hidden="true"/>
        </div>
        <div class="band-value">
          <span class="band-count"><strong>{{ row.count }}</strong> 人</span>
          <span class="band-rate" :class="{ suppressed: row.rateText === '样本过小' }">{{ row.rateText }}</span>
        </div>
      </div>
    </div>
    <template v-else>
      <!-- 纵向版（**今天没有调用方**，见文件头；保留它的理由也写在那里）。所以这一支
           改坏了屏幕上不会有任何东西变红——改它之前先想清楚是给谁改的。
           柱子与人数在**同一列**里上下排：`N 人` 是自然高度、柱子在剩下的空间里按百分比长，
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
    </template>
    <p class="band-foot">
      三档按人去重（每人取最近一场已计算结果），合计 {{ total }} 人，等于可评价样本；占比的分母也是它。
      <template v-if="anyRateSuppressed">分母过小时不给百分比——三五个人的比例等于点名，此处只给人数。</template>
    </p>
  </div>
  <!-- 空态是一句关于数据的话，不是一块占位（§14）：它同时回答「为什么没有」与「什么之后会有」。
       判据见 `hasAnyCount`——**三条 0 人的柱子不是空态**，它是一句「一个都没落进这三档」的
       断言，而这一屏要说的是「还没有数据」。**两版共用这一支**：三处的空态是同一句话。 -->
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
/* ---- 横向版（§5.17）----
   `.band-name` 与 `.band-value` 都是**定宽**的，所以三条轨道的左右两端在两页上都对齐：
   一列宽度随内容的变宽会让「条更长」这句话失去意义（轨道的起点或终点跟着漂）。 */
.band-bars { display: flex; flex-direction: column; gap: 8px }
/* 统计范围 + 可评价样本量。读者要做的第一个判断是「这几行数的是谁」，所以它排在图的前面。 */
.band-scope {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 10px;
  flex-wrap: wrap;
  margin: 0 0 2px;
  color: #3e5877;
  font-size: 13px;
}
.band-scope-name { color: #1d3f63; font-weight: 650 }
.band-scope-count { color: #687c93; font-size: 12px; font-variant-numeric: tabular-nums }
.band-scope-count strong { color: #142b45; font-size: 14px }
.band-row { display: flex; align-items: center; gap: 10px }
.band-name {
  flex: 0 0 104px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}
.band-bar-track {
  flex: 1 1 auto;
  min-width: 40px;
  height: 14px;
  border-radius: 7px;
  background: #eef2f7;
  overflow: hidden;
}
.band-bar { height: 100%; border-radius: 7px; transition: width .2s }
/* 人数与比例并排、靠右：`tabular-nums` 让三行的小数点竖着对齐。 */
.band-value {
  flex: 0 0 132px;
  display: flex;
  align-items: baseline;
  justify-content: flex-end;
  gap: 8px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.band-count { color: #3e5877; font-size: 12px }
.band-count strong { color: #142b45; font-size: 15px }
/* 横向版的档名与区间靠左（纵向版是靠居中），因为左边这一列是要读过去的文字。 */
.band-bars .band-label, .band-bars .band-range { text-align: left }
.band-bars .band-rate { text-align: right }
/* 窄屏降级：两列的固定宽度一起收，字号各降一档，保证 375 档下轨道仍有可读的宽度
   （§5.17.3 第 8 条：标签 / 条形 / 数值都不许横向溢出，必要时换行或降级）。 */
@media (max-width: 600px) {
  .band-row { gap: 8px }
  .band-name { flex-basis: 76px }
  .band-value { flex-basis: 104px; gap: 6px }
  .band-label { font-size: 12px }
  .band-range { font-size: 11px }
  .band-count { font-size: 11px }
  .band-count strong { font-size: 13px }
  .band-bars .band-rate { font-size: 11px }
  .band-scope { font-size: 12px }
  .band-scope-count strong { font-size: 13px }
}
.band-foot { margin: 2px 0 0; color: #708198; font-size: 12px; line-height: 1.7 }
/* 与 GradesPage / OverviewPage 的 `.data-empty` 同一副长相（那是这一套页面里「关于数据的
   一句话」的既有形状），组件自带一份是因为这个组件自己决定要不要换成空态。 */
.data-empty { padding: 34px 18px; border: 1px dashed #cbd8e2; border-radius: 8px; background: #f8fafc; color: #687c93; text-align: center; line-height: 1.7 }
</style>
