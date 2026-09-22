<script setup lang="ts">
/** RPT-01 全校心理筛查预警总览 —— 独立页面，自带筛选栏。 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import CompletionDonut from '../components/CompletionDonut.vue'
import CoverageColumns from '../components/CoverageColumns.vue'
import MetricStrip from '../components/MetricStrip.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ScoreBandBars from '../components/ScoreBandBars.vue'
import ErrorState from '../../../components/ErrorState.vue'
import { getAnalyticsReport, type AnalyticsReport } from '../../../services/api'
import { signalTypeLabel } from '../../../services/labels'

const router = useRouter()
const route = useRoute()
const report = ref<AnalyticsReport | null>(null)
const loading = ref(false)
const error = ref('')
const analyticsBase = computed(() => route.path.startsWith('/leader/') ? '/leader/analytics' : '/counselor/analytics')

const quality = computed(() => report.value?.sample_quality)
const overview = computed(() => report.value?.overview)
const grades = computed(() => report.value?.grades || [])
const completed = computed(() => quality.value?.completed_count || 0)
const target = computed(() => quality.value?.target_count || 0)
const sampleCount = computed(() => quality.value?.n_evaluable || 0)
const signals = computed(() => overview.value?.signal_student_count || 0)
const pendingReview = computed(() => overview.value?.pending_review_work_items || 0)

/**
 * 筛查信号的计数**按编码取**，不按中文标签回头去找。
 *
 * 此前这里是 `find(s => s.label === '普通筛查信号')`——把**文案**当成了主键，于是
 * `SIGNAL_TYPE_LABELS` 里那个「效度复测建议 / 效度建议复测」的语序差一个字就会静默
 * 取到 0，而屏幕上只是「0 人」，看起来像一个正常的统计结果。中文一律由 `signalTypeLabel`
 * 出（§3：`labels.ts` 是唯一的映射层），编码在这里只是键。
 */
const signalCounts = computed(() => {
  const counts: Record<string, number> = {}
  for (const stat of overview.value?.signal_type_stats || []) {
    counts[stat.signal_type] = stat.student_count
  }
  return counts
})
function signalCount(code: string) { return signalCounts.value[code] || 0 }

/**
 * 总分三档分布（按人）与它那三段**区间**（随规则版本走，见 CLAUDE.md §6）。
 *
 * 区间取不到时是 `null`（这批结果横跨多个规则版本、或那一行查不到），此时图上只出人数、
 * 不出「1~55 分」那一句——**不回落成一组写死的区间**：一个不存在的区间比没有区间更糟，
 * 它会被人照着它去理解分数。
 */
const levelDistribution = computed(() => overview.value?.level_distribution || [])
const totalBands = computed(() => report.value?.scale?.total_bands ?? null)
// 覆盖率是「可能不发布」的比率：分母小于 MIN_COHORT_FOR_AGGREGATE 时服务端发 None。
// **不许 `?? 0` 把它抹平**——`0` 是「一个都没测」，`None` 是「这几个人算出来不足为凭」，
// 两者在屏幕上必须长得不一样（CLAUDE.md §11）。所以这里按 GradesPage 的既有写法分成两支：
// 进柱状图的只有真值，被抑制的那些在下面单列一句话说明。
//
// `rate` 直接取服务端的 `coverage_rate`，**不由 `sample_count / eligible_count` 现算**：
// 服务端 `_report_rate` 已经 `round(…, 1)` 过一次，前端再算一次会在同一根柱子上出现两个数
// （JS 的 81.25 →「81.3」，服务端的 round →「81.2」），而那个差别恰好落在小数末位上，
// 最难被认出来。分子分母照原样带上，是要把它们逐列写进柱子下面那行说明（§9：口径写进界面）。
const coverageColumns = computed(() => grades.value.flatMap(g =>
  g.coverage_rate == null ? [] : [{
    name: g.grade_name, rate: g.coverage_rate, sample: g.sample_count, eligible: g.eligible_count
  }]
))
const suppressedGradeNames = computed(() => grades.value
  .filter(g => g.coverage_rate == null).map(g => g.grade_name))

async function loadReport(taskIds: number[]) {
  loading.value = true
  error.value = ''
  try { report.value = await getAnalyticsReport(taskIds, 'ALL_CALCULATED') }
  catch (err) { report.value = null; error.value = err instanceof Error ? err.message : '报表加载失败' }
  finally { loading.value = false }
}
function onQuery(f: { taskIds: number[] }) { if (f.taskIds.length) loadReport(f.taskIds) }
function reset() { report.value = null; error.value = ''; loading.value = false }
</script>

<template>
  <ReportPageHeader title="全校预警总览" description="汇总测评覆盖、筛查信号与待复核工作，数据仅用于教育支持。"/>
  <FilterBar @query="onQuery" @reset="reset"/>

  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="report">
    <div class="kpis">
      <KpiCard label="实际应测人数" :value="target.toLocaleString('zh-CN')" hint="当前任务统计分母"/>
      <KpiCard label="已完成测评" :value="completed.toLocaleString('zh-CN')" :hint="'完成率 '+ (target ? (completed/target*100).toFixed(1)+'%' : '—')" tone="green" icon="check"/>
      <KpiCard label="可评价样本" :value="sampleCount.toLocaleString('zh-CN')" hint="用于当前聚合分析" icon="chart"/>
      <KpiCard label="存在筛查信号" :value="signals" :hint="'占可评价样本 '+(sampleCount ? (signals/sampleCount*100).toFixed(1)+'%' : '—')" tone="red" icon="alert"/>
    </div>

    <!-- 前三格是**同一批码**的三类筛查信号（都数「人」，三类允许交叉），第四格是待办**项**数。
         单位不同的两件事在标签上就分开写（人 / 项）：`pending_review_work_items` 数的是
         `risk_event` 的行，而引擎对**每一道**命中的重点题各写一行，「一个人两道都中了」
         在它那里是 2（§11 那条「把条数读成人数」）。 -->
    <MetricStrip :items="[
      { label: signalTypeLabel('SCREENING_SIGNAL'), value: signalCount('SCREENING_SIGNAL')+' 人', hint: '允许与其他信号交叉' },
      { label: signalTypeLabel('MANUAL_REVIEW_REQUIRED'), value: signalCount('MANUAL_REVIEW_REQUIRED')+' 人', hint: '具体回答不在报表展示' },
      { label: signalTypeLabel('RETEST_RECOMMENDED'), value: signalCount('RETEST_RECOMMENDED')+' 人', hint: '单独提示，不等于无效' },
      { label: '待处理复核工作', value: pendingReview+' 项', hint: '仅心理老师授权处理' }
    ]"/>

    <div class="overview-chart">
      <section class="card">
        <!-- 标题与 `/leader/overview` 那一块同名（`LeaderOverviewPage.vue:202`）**是有意的**：
             它们是同一个口径的同一件事（每名学生按最近一场落一档），而这里多出的是「按哪个
             分数段落的档」——区间那一句写在每根柱子下面。两个页面各起一个名字会让同一个人
             在两处以为看到的是两种统计。 -->
        <h2 class="section-title">关注等级分布</h2>
        <p class="muted tiny">按总分区间分档，每名可评价学生只落一档，三档互不叠加；区间取自本次结果所用的量表评分规则版本。</p>
        <ScoreBandBars :items="levelDistribution" :totals="totalBands" :total="sampleCount"/>
      </section>
      <section class="card">
        <h2 class="section-title">测评完成情况</h2>
        <CompletionDonut :completed="completed" :target="target"/>
      </section>
      <!-- `cover-card` 是**只给这一张卡**的 flex 列（不加在 `section.card` 上：那三张卡里
           只有这一张需要「内容吃掉卡片剩余高度」，改公共那条会同时动另外两张的块间距，
           而 flex 容器里子元素的 margin 不再折叠）。图因此长满卡片，不再像 SVG 那样
           按内容定死高度、把多出来的空间全留在图**下方**。 -->
      <section class="card cover-card">
        <h2 class="section-title">各年级样本覆盖率</h2>
        <CoverageColumns class="cover-chart" v-if="coverageColumns.length" :items="coverageColumns"/>
        <div v-else class="data-empty">当前没有可发布的年级覆盖率。样本量不足时，系统不会展示可反推个体的精确数值。</div>
        <p v-if="suppressedGradeNames.length" class="hint">{{ suppressedGradeNames.join('、') }}因样本量不足未进入柱状图。</p>
      </section>
    </div>

    <div class="bottom-grid">
      <section class="card">
        <h2 class="section-title">当前工作进展</h2>
        <div class="table-scroll">
          <table>
            <thead><tr><th>工作环节</th><th>应处理人数/项</th><th>已完成</th><th>完成率</th><th>操作</th></tr></thead>
            <tbody>
              <tr><td>学生测评</td><td>{{ target }}</td><td>{{ completed }}</td><td>{{ target ? (completed/target*100).toFixed(1)+'%' : '—' }}</td>
                <td><button class="btn-link" @click="router.push(analyticsBase+'/dimensions')">查看维度分析</button></td></tr>
              <tr><td>统计可评价样本</td><td>{{ sampleCount }}</td><td>{{ sampleCount }}</td><td>—</td>
                <td><button class="btn-link" @click="router.push(analyticsBase+'/dimensions')">查看八维度</button></td></tr>
              <tr><td>重点题人工复核</td><td>{{ pendingReview }}</td><td>—</td><td>—</td>
                <td><button class="btn-link" @click="router.push(route.path.startsWith('/leader/') ? '/leader/progress' : '/counselor/cases')">查看流程说明</button></td></tr>
              <tr><td>后续关怀</td><td>{{ signals }}</td><td>—</td><td>—</td>
                <td><button class="btn-link" @click="router.push(route.path.startsWith('/leader/') ? '/leader/progress' : '/counselor/cases')">查看流程说明</button></td></tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="card">
        <h2 class="section-title">统计解释边界</h2>
        <div class="stat-boundary">
          ① 本页呈现当前所选任务的实际聚合统计，不代表医学诊断。<br/>
          ② 不同筛查信号可能出现在同一学生身上，各类型人数不可直接相加。<br/>
          ③ "筛查信号"是需要进一步了解的线索，不等同心理疾病诊断。<br/>
          ④ 群体差异需结合覆盖率、效度提示、施测背景和心理老师专业判断。<br/>
          ⑤ 总分区间来自量表评分规则版本；学校调整过分段时，本页的区间文字随规则版本一起变。<br/>
          ⑥ 德育领导默认只查看授权聚合信息。
        </div>
      </section>
    </div>
    <PrivacyNote/>
  </template>
  <div v-else class="empty">请选择测评任务后点击查询</div>
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
/* 与 GradesPage 同一套口径：图里放不下的一档，在下面单列一句话说明（同 §11 的「样本过小」）。 */
.data-empty { padding: 34px 18px; border: 1px dashed #cbd8e2; border-radius: 8px; background: #f8fafc; color: #687c93; text-align: center; line-height: 1.7 }
.hint { margin: 10px 0 0; color: #708198; font-size: 13px }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.overview-chart { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px }
.bottom-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 16px; margin-bottom: 16px }
.overview-chart > *, .bottom-grid > * { min-width: 0 }
section.card { min-width: 0; padding: 18px; overflow: hidden }
/* 覆盖率卡：让柱状图吃掉卡片剩余高度（网格把三张卡拉到同高，多出来的那 160px 此前留在图
   下方）。`min-height: 0` 是给里面那层 `flex: 1` 的绘图区留可压缩余量——没有它，窄屏上
   内容会顶破卡片。`.cover-chart` 是加在子组件**根节点**上的类：父组件的 scoped 属性会
   落到那里，所以这一条能命中组件内部那层 `.cc-chart` 的外边距。 */
.cover-card { display: flex; flex-direction: column }
.cover-chart { margin-top: 12px }
/* 空态在 flex 列里不会被拉伸，不给 `flex: 1` 的话它贴在卡片顶端，下面又是同一片空白。 */
.cover-card .data-empty { flex: 1 1 auto; display: flex; align-items: center; justify-content: center; margin-top: 12px }
.stat-boundary { background: #ecf5ff; border: 1px solid #cbdfff; border-radius: 9px; padding: 14px 18px; color: #2d5078; font-size: 13px; line-height: 1.8; margin-top: 16px }
.btn-link { border: 0; background: none; color: #0876d9; font-weight: 650; padding: 3px 6px; cursor: pointer }
.btn-link:hover { text-decoration: underline }
.table-scroll { max-width: 100%; overflow: auto }
table { width: 100%; border-collapse: collapse; font-size: var(--font-table); white-space: nowrap }
th { background: #f1f6fd; color: #3e5877; padding: 10px; text-align: left; font-weight: 700 }
td { padding: 10px; border-top: 1px solid #e6edf6 }
tbody tr:hover { background: #f8fbff }
@media(max-width: 1400px) { .overview-chart, .bottom-grid { grid-template-columns: 1fr } }
@media(max-width: 1100px) { .kpis { grid-template-columns: 1fr 1fr } }
@media(max-width: 600px) { .kpis { grid-template-columns: 1fr } }
</style>
