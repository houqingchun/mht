<script setup lang="ts">
/**
 * 各年级样本覆盖率 —— **HTML/CSS 柱形图，不是 SVG**。
 *
 * 与 `ScoreBandBars` 同一个理由：这一页的图表格子在三列布局里只有 380 多像素，
 * 而 `ColumnChart` 的 `viewBox` 写死 560 宽——缩到 380 是 0.68 倍，声明 12px 的数值标签
 * 实际画成 8.2px（量出来是 `getScreenCTM().a`，不是文本盒子的 `getBoundingClientRect()`：
 * 后者对 SVG 文本返回的是紧贴字形的用户单位盒子，看起来「没有被缩放」，很能骗人）。
 * 用 HTML 元素画，字号就是声明的那个数。
 *
 * 另一件 SVG 做不到的事：这一格此前被钉在卡片顶部、下面空出一大片。「钉在顶部」是因为
 * 图形的**高度是内容定死的**（viewBox 560×224 → 等比缩放到 383 宽就是 153 高），
 * 卡片被网格拉伸到与旁边两张一样高（实测三张都是 421×375）之后，多出来的 160px 只能空在
 * 下面。HTML 版的绘图区可以 `flex: 1`，于是**卡片有多高它就长多高**，空白不靠调数字消掉，
 * 而是构造上不存在。
 *
 * **纵轴是真的 0–100% 刻度**（`TICKS`），不是按最高一条自适应——这一张图回答的是
 * 「覆盖得够不够」，而「87.5%」只有在一条满格＝100% 的尺子上才读得出那是「接近满格」。
 * （旁边那张关注等级分布刻意**没有**纵轴：它回答的是「哪一档人多」，高度只用来排序。）
 * 正因为纵轴是绝对的，**数值行必须单独占一行、放在绘图区上方**，不能像 `ScoreBandBars`
 * 那样塞进柱子所在的那一列——那一行会占掉绘图区的高度，柱子的百分比高度与刻度就不再是
 * 同一把尺子，图上会出现「柱顶在 75% 线上、数字写着 87.5%」这种自相矛盾。
 *
 * 三行（数值 / 绘图区 / 年级轴）共用同一套列宽（`.cc-col`）与同一个 `gap`，
 * 左右留白由 `--gutter` / `--pad` 两个变量一处定义，所以数字永远落在它那根柱子正上方。
 */
import { computed } from 'vue'

const props = defineProps<{
  /**
   * 只收**已发布**的覆盖率——被抑制的那些（服务端发 `null`）由调用方在下面单列一句话说明，
   * 不进这张图（§11：`0` 是「一个都没测」，`null` 是「这几个人算出来不足为凭」）。
   */
  items: Array<{ name: string; rate: number; sample: number; eligible: number }>
}>()

/**
 * 刻度写死在这里是有意义的：它们描述的是**百分比这个单位本身**，与手里这份数据无关。
 * 换成「按最高一条 × 1.2」的自适应上界（`ColumnChart` 与 `ScoreBandBars` 的口径），
 * 一根 90% 的柱子会与一根 60% 的柱子画得一样高，「覆盖率高不高」就答不出来了。
 */
const TICKS = [100, 75, 50, 25, 0]

const rows = computed(() => props.items.map(item => ({
  ...item,
  // 服务端已经 `round(…, 1)` 过，这里的 `toFixed(1)` 只是补齐「100」→「100.0」的形状，
  // **不在前端用 sample/eligible 重算**：重算会在同一根柱子上下写出两个数
  // （JS 的 81.25 →「81.3」，服务端的 round →「81.2」），而那正是这一行要避免的事。
  height: `${item.rate}%`,
  rateText: `${item.rate.toFixed(1)}%`
})))
</script>

<template>
  <div class="cc-chart">
    <div class="cc-values">
      <div v-for="row in rows" :key="row.name" class="cc-col">
        <span class="cc-figure">{{ row.rateText }}</span>
      </div>
    </div>

    <div class="cc-plot">
      <!-- 刻度只说明尺子，数值在上一行已经有了，所以对读屏软件整列略过。 -->
      <div class="cc-gutter" aria-hidden="true">
        <span v-for="t in TICKS" :key="t" class="cc-tick" :style="{ top: t + '%' }">{{ t }}%</span>
      </div>
      <div class="cc-tracks">
        <div v-for="row in rows" :key="row.name" class="cc-col">
          <div class="cc-track">
            <!-- 0% 时**不画短桩**。`ScoreBandBars` 有 `min-height: 3px`，那是因为它的上界
                 自适应、没有纵轴——不画点什么的话「一个都没有」与「这块没渲染出来」在屏幕上
                 一模一样。这里纵轴是绝对的，3px 短桩会被读成「覆盖了 3%」，比空着更糟；
                 零值由上一行的「0.0%」与压在 0% 线上的柱底一起说清。 -->
            <div
              v-if="row.rate > 0"
              class="cc-column"
              :style="{ height: row.height }"
              aria-hidden="true"/>
          </div>
        </div>
      </div>
    </div>

    <div class="cc-axis">
      <div v-for="row in rows" :key="row.name" class="cc-col">
        <div class="cc-name">{{ row.name }}</div>
        <!-- 分母口径写进界面（§9）。它同时是「为什么这根柱子不到 100%」的答案，
             也把页面拉满——卡片的高度由旁边两张图定，这里多出来的空间用真实信息填，
             不用空白填。`white-space` 保持可换行：窄列时它折成两行，不会被裁掉半截。 -->
        <div class="cc-meta">可评价 {{ row.sample }} / 应测 {{ row.eligible }}</div>
      </div>
    </div>

    <p class="cc-foot">
      覆盖率 ＝ 可评价样本 ÷ 实际应测人数（标记为请假 / 免测 / 已排除的学生不进分母）；满格即应测学生全部产生了可评价结果。
    </p>
  </div>
</template>

<style scoped>
/* `flex: 1 1 auto` 让这一块吃掉卡片里剩下的高度，`min-height: 0` 允许它被压缩——
   两者缺一个，绘图区要么撑不出卡片、要么在窄屏上顶破卡片。 */
.cc-chart { display: flex; flex-direction: column; gap: 10px; flex: 1 1 auto; min-height: 0; --gutter: 42px; --pad: 2px }
/* 数值行与轴行共用一套左右留白：gutter 让开纵轴，pad 与绘图区的内边距对齐。 */
.cc-values, .cc-axis { display: flex; gap: 8px; padding-left: calc(var(--gutter) + var(--pad)); padding-right: var(--pad) }
/* 绘图区是唯一会长高的部分：卡片被网格拉到与旁边两张一样高的那一刻，
   多出来的高度全部进这里，而不是留在图**下方**（那正是这次要修的那个 160px）。 */
.cc-plot { display: flex; flex: 1 1 auto; min-height: 150px }
.cc-gutter { position: relative; flex: 0 0 var(--gutter) }
.cc-tick { position: absolute; right: 6px; transform: translateY(-50%); color: #8193a9; font-size: 12px; line-height: 1; font-variant-numeric: tabular-nums }
/* 刻度线：`repeating-linear-gradient` 每 25% 落一条，正好压住 0 / 25 / 50 / 75 四条；
   顶边那条（100%）由 `::after` 画。**不用 border**：`box-sizing: border-box` 下边框会把
   padding box 缩进去，柱子与刻度线就差一个像素，而那个偏差在「贴着 100% 线」的柱子上看得出来。
   背景铺满 padding box（含 `--pad` 那两像素），所以刻度线是齐着绘图区两端的。 */
.cc-tracks { position: relative; flex: 1 1 auto; min-width: 0; display: flex; align-items: stretch; gap: 8px; padding: 0 var(--pad); background-image: repeating-linear-gradient(to top, #eef2f7 0 1px, transparent 1px 25%) }
.cc-tracks::after { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 1px; background: #eef2f7 }
/* 上下三块共用这一个形状，所以列宽与间距不可能各说各话（`ScoreBandBars` 的同一条）。 */
.cc-col { display: flex; flex-direction: column; flex: 1 1 0; min-width: 0 }
.cc-figure { color: #244769; font-size: 15px; font-weight: 700; line-height: 1.4; text-align: center; font-variant-numeric: tabular-nums }
/* `flex-basis: 0` + `min-height: 0` 让这个盒子的高度是确定的，里面的百分比高度才有东西可算；
   柱子贴底归 `align-items: flex-end` 管（`flex-end` 写在 `.cc-col` 上会让整列塌成内容高度）。 */
.cc-track { flex: 1 1 0; min-height: 0; display: flex; align-items: flex-end; justify-content: center }
/* 柱色沿用 `ColumnChart` 的默认填充，全站的竖柱子是同一个蓝（不引入按覆盖率分档的双色：
   那会把 GradesPage 里 `>= 0.9` 那个写死的阈值复制成第二处定义）。 */
.cc-column { width: 100%; max-width: 54px; background: #428df1; border-radius: 5px 5px 0 0; transition: height .2s }
.cc-name { color: #244769; font-size: 13px; font-weight: 650; text-align: center; line-height: 1.5 }
.cc-meta { color: #8193a9; font-size: 12px; text-align: center; line-height: 1.5 }
.cc-foot { margin: 0; color: #708198; font-size: 12px; line-height: 1.7 }
</style>
