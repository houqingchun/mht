<script setup lang="ts">
/** RPT-03 年级心理维度对比 —— 独立页面。 */
import { computed, ref } from 'vue'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import ColumnChart from '../components/ColumnChart.vue'
import ScoreBandBars from '../components/ScoreBandBars.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import { getAnalyticsReport, type AnalyticsReport } from '../../../services/api'
import { dimensionLabel } from '../../../services/labels'

const report = ref<AnalyticsReport | null>(null)
const loading = ref(false)
const error = ref('')
const selectedDimCode = ref('')
const grades = computed(() => report.value?.grades || [])
const totalTarget = computed(() => grades.value.reduce((a, g) => a + g.target_count, 0))
const totalValid = computed(() => grades.value.reduce((a, g) => a + g.sample_count, 0))
/**
 * 三档区间随规则版本走（§6）——与 `OverviewPage` 取的是同一个字段，因为这一页与那一页
 * 说的是同一批结果的同一件事，只是切成了按年级。取不到就是 `null`，图上只出人数、
 * 不出「N~M 分」那一句，**不回落成一组写死的区间**。
 */
const totalBands = computed(() => report.value?.scale?.total_bands ?? null)
/** 有可评价样本的年级才画图：全 0 的年级画出来是三条空柱子，与「一个都没测」不像。 */
const gradesWithSamples = computed(() => grades.value.filter(g => g.sample_count > 0))
const selectedDimension = computed(() => report.value?.dimensions.find(d => d.dimension_code === selectedDimCode.value) || report.value?.dimensions[0])
const publishedGrades = computed(() => grades.value.flatMap(g => {
  const dimension = g.dimensions.find(d => d.dimension_code === selectedDimension.value?.dimension_code)
  return dimension?.high_score_rate == null ? [] : [{ grade: g, value: dimension.high_score_rate }]
}))
const suppressedGradeNames = computed(() => grades.value.filter(g => {
  const dimension = g.dimensions.find(d => d.dimension_code === selectedDimension.value?.dimension_code)
  return dimension?.suppression.suppressed
}).map(g => g.grade_name))

function cellColor(v: number) { return `rgba(242,110,90,${Math.min(0.62, 0.06 + v / 48).toFixed(2)})` }

async function loadReport(taskIds: number[]) {
  loading.value = true
  error.value = ''
  try {
    report.value = await getAnalyticsReport(taskIds, 'ALL_CALCULATED')
    selectedDimCode.value = report.value.dimensions[0]?.dimension_code || ''
  }
  catch (err) { report.value = null; error.value = err instanceof Error ? err.message : '报表加载失败' }
  finally { loading.value = false }
}
function onQuery(f: { taskIds: number[] }) { if (f.taskIds.length) loadReport(f.taskIds) }
function reset() { report.value = null; error.value = ''; selectedDimCode.value = '' }
</script>

<template>
  <ReportPageHeader title="年级维度对比" description="对比各年级心理维度表现，辅助识别需要重点支持的群体。"/>
  <FilterBar @query="onQuery" @reset="reset"/>

  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="report">
    <div class="kpis">
      <KpiCard label="参与年级数" :value="grades.length" hint="当前选定范围"/>
      <KpiCard label="实际应测人数" :value="totalTarget.toLocaleString('zh-CN')" hint="合计"/>
      <KpiCard label="可评价样本" :value="totalValid.toLocaleString('zh-CN')" :hint="'覆盖率 '+(totalTarget ? (totalValid/totalTarget*100).toFixed(1)+'%' : '—')" tone="green"/>
      <KpiCard label="当前比较维度" :value="dimensionLabel(selectedDimension?.dimension_code || '')" hint="跨年级比较"/>
    </div>

    <div class="grade-grid">
      <div>
        <section class="card">
          <h2 class="section-title">年级 × 八维度高分比例（%）</h2>
          <div class="table-scroll">
            <table class="heat-table">
              <thead><tr><th>年级</th><th v-for="d in report.dimensions" :key="d.dimension_code">
                <button class="dimension-button" :class="{active:selectedDimension?.dimension_code===d.dimension_code}" @click="selectedDimCode=d.dimension_code">{{ dimensionLabel(d.dimension_code) }}</button>
              </th></tr></thead>
              <tbody>
                <tr v-for="g in grades" :key="g.grade_name">
                  <th>{{ g.grade_name }}</th>
                  <td v-for="(d,i) in g.dimensions" :key="i" :style="{background: d.high_score_rate == null ? '#f5f7fa' : cellColor(d.high_score_rate)}" @click="selectedDimCode=d.dimension_code">
                    {{ d.high_score_rate != null ? d.high_score_rate.toFixed(1)+'%' : '样本不足' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="hint">色深只表示统计数值大小，不代表诊断或临床等级。</p>
        </section>
        <section class="card chart-card">
          <div class="chart-heading">
            <h2 class="section-title">{{ dimensionLabel(selectedDimension?.dimension_code || '') }} · 各年级高分比例</h2>
            <label>比较维度
              <select v-model="selectedDimCode"><option v-for="d in report.dimensions" :key="d.dimension_code" :value="d.dimension_code">{{ dimensionLabel(d.dimension_code) }}</option></select>
            </label>
          </div>
          <ColumnChart v-if="publishedGrades.length" :labels="publishedGrades.map(item=>item.grade.grade_name)" :values="publishedGrades.map(item=>item.value)" :max="Math.max(30,...publishedGrades.map(item=>item.value))*1.1" suffix="%"/>
          <div v-else class="data-empty">当前维度没有可发布的年级比例。样本量不足时，系统不会展示可反推个体的精确数值。</div>
          <p v-if="publishedGrades.length && publishedGrades.every(item=>item.value===0)" class="zero-note">各年级该维度高分比例均为 0%，基线圆点表示真实的零值。</p>
          <p v-if="suppressedGradeNames.length" class="hint">{{ suppressedGradeNames.join('、') }}因样本量不足未进入柱状图。</p>
        </section>
      </div>
      <div class="side-panel">
        <section class="card">
          <h2 class="section-title">年级样本与覆盖率</h2>
          <table>
            <thead><tr><th>年级</th><th>应测</th><th>可评价</th><th>覆盖率</th></tr></thead>
            <tbody>
              <tr v-for="g in grades" :key="g.grade_name">
                <td>{{ g.grade_name }}</td><td>{{ g.target_count }}</td><td>{{ g.sample_count }}</td>
                <td :class="g.eligible_count > 0 && g.sample_count/g.eligible_count >= 0.9 ? 'coverage-good' : 'coverage-warn'">
                  {{ g.eligible_count > 0 ? (g.sample_count/g.eligible_count*100).toFixed(1)+'%' : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </section>
        <section class="card">
          <h2 class="section-title">专业解释提示</h2>
          <div class="tip-box">
            ① 年级差异必须同时查看样本量与覆盖率。<br/>
            ② 高分比例差异是描述性统计，不表示某年级"心理更差"。<br/>
            ③ 施测时间、年级发展阶段等都可能影响结果。<br/>
            ④ 不生成年级心理健康排名。
          </div>
        </section>
      </div>
    </div>

    <section class="card band-card">
      <h2 class="section-title">各年级关注等级分布</h2>
      <p class="muted tiny">按总分区间分档，每名可评价学生只落一档，三档互不叠加；区间取自本次结果所用的量表评分规则版本。与全校总览那张图**同一个算法**，所以各年级三档之和恒等于全校那一份。</p>
      <div v-if="gradesWithSamples.length" class="band-grid">
        <div v-for="g in gradesWithSamples" :key="g.grade_name" class="band-cell">
          <h3 class="band-cell-title">{{ g.grade_name }}</h3>
          <ScoreBandBars :items="g.level_distribution" :totals="totalBands" :total="g.sample_count"/>
        </div>
      </div>
      <div v-else class="data-empty">当前范围内没有可评价的年级样本。没有已计算结果的学生不进这三档，全员未测评与全员一般观察不是一回事。</div>
    </section>

    <PrivacyNote/>
  </template>
  <div v-else class="empty">请选择测评任务后点击查询</div>
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.grade-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, .75fr); gap: 16px; margin-bottom: 16px }
.grade-grid > *, .side-panel, .side-panel > * { min-width: 0 }
.side-panel { display: grid; gap: 16px }
section.card { min-width: 0; padding: 18px; overflow: hidden }
.heat-table td { text-align: center; font-weight: 700; padding: 10px 8px }
.heat-table th { text-align: center; padding: 10px 8px }
.chart-card { margin-top: 16px }
/* 一个年级一张图。`auto-fit` 而不是写死三列：年级数由学校的学段决定（初中三个、
   完中六个），写死一列数会让某一档年级被挤成半宽或被拉成整宽。 */
.band-card { margin-bottom: 16px }
.band-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 12px }
.band-cell { min-width: 0; border: 1px solid #e6edf6; border-radius: 10px; padding: 12px 14px 6px; background: #fbfdff }
.band-cell-title { margin: 0 0 4px; font-size: 14px; font-weight: 700; color: #1d3f63 }
.chart-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px }
.chart-heading label { display: grid; flex: 0 0 160px; gap: 4px; color: #536879; font-size: 12px; font-weight: 600 }
.chart-heading select { width: 100%; min-height: 36px; border: 1px solid #cbd8e2; border-radius: 6px; background: #fff; color: #183447; padding: 0 8px }
.dimension-button { width: 100%; border: 0; background: transparent; color: inherit; cursor: pointer; font: inherit; font-weight: inherit; white-space: nowrap }
.dimension-button.active { color: #075ea9; text-decoration: underline; text-underline-offset: 3px }
.data-empty { padding: 34px 18px; border: 1px dashed #cbd8e2; border-radius: 8px; background: #f8fafc; color: #687c93; text-align: center; line-height: 1.7 }
.zero-note { margin: 4px 0 0; color: #536879; font-size: 12px }
.tip-box { background: #f2f7fd; border: 1px solid #d7e8fb; border-radius: 8px; padding: 12px 14px; font-size: 13px; color: #2d5078; line-height: 1.8 }
.privacy { margin-top: 16px }
.hint { margin: 10px 0 0 }
table { width: 100%; border-collapse: collapse; font-size: var(--font-table) }
th { background: #f1f6fd; color: #3e5877; padding: 9px 8px; text-align: left; font-weight: 700 }
td { padding: 9px 8px; border-top: 1px solid #e6edf6 }
tbody tr:hover { background: #f8fbff }
.coverage-good { color: #00875a; font-weight: 700 }
.coverage-warn { color: #a86600; font-weight: 700 }
.table-scroll { max-width: 100%; overflow: auto }
@media(max-width:1200px) { .grade-grid { grid-template-columns: 1fr } }
@media(max-width:1100px) { .kpis { grid-template-columns: 1fr 1fr } }
@media(max-width:600px) {
  .kpis { grid-template-columns: 1fr }
  .side-panel section:first-child { overflow-x: auto }
  .chart-heading { display: block }
  .chart-heading label { margin-top: 10px }
}
</style>
