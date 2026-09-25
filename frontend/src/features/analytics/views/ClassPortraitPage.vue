<script setup lang="ts">
/** RPT-04 班级心理维度画像 —— 独立页面。 */
import { computed, ref } from 'vue'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import GroupedBarChart from '../components/GroupedBarChart.vue'
import RadarChart from '../components/RadarChart.vue'
import ScoreBandBars from '../components/ScoreBandBars.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import { getAnalyticsReport, type AnalyticsReport, type ReportCohort } from '../../../services/api'
import { dimensionLabel } from '../../../services/labels'

const report = ref<AnalyticsReport | null>(null)
const loading = ref(false)
const error = ref('')
const metric = ref('rate')
const notes = ref('')
const suggestions = ref('')
const status = ref('')
const selectedGrade = ref('')
const selectedClass = ref('')

const classes = computed(() => report.value?.classes || [])
const grades = computed(() => report.value?.grades || [])
const dims = computed(() => report.value?.dimensions || [])
const gradeOptions = computed(() => [...new Set(classes.value.map(item => item.grade_name))])
const classOptions = computed(() => classes.value.map(item => ({ grade: item.grade_name, name: item.class_name || '未分班级' })))

const activeClass = computed<ReportCohort | null>(() => {
  if (!classes.value.length) return null
  const filtered = selectedClass.value ? classes.value.filter(c => c.class_name === selectedClass.value) : classes.value
  if (selectedGrade.value) return filtered.find(c => c.grade_name === selectedGrade.value) || filtered[0] || null
  return filtered[0] || null
})
const gradeForClass = computed<ReportCohort | null>(() => {
  if (!activeClass.value) return null
  return grades.value.find(g => g.grade_name === activeClass.value?.grade_name) || null
})
function dimensionFor(cohort: ReportCohort | null, code: string) {
  return cohort?.dimensions.find(d => d.dimension_code === code) || null
}
function metricValue(d: any) {
  if (!d || d.suppression?.suppressed) return null
  return metric.value === 'average' ? d.mean_score : d.high_score_rate
}
const classValues = computed(() => dims.value.map(d => metricValue(dimensionFor(activeClass.value, d.dimension_code))))
const gradeValues = computed(() => dims.value.map(d => metricValue(dimensionFor(gradeForClass.value, d.dimension_code))))
const hasPublishedComparison = computed(() => classValues.value.some(v => v != null) && gradeValues.value.some(v => v != null))
const chartClassValues = computed(() => classValues.value)
const chartGradeValues = computed(() => gradeValues.value)
/** 三档区间随规则版本走（§6）——与全校总览、年级页取的是同一个字段，不写死区间。 */
const totalBands = computed(() => report.value?.scale?.total_bands ?? null)
/** 「本班（初一 1班）」这一串在两处出现过：图上的小标题与上面那张 KPI 卡的副标题。 */
const activeClassName = computed(() => activeClass.value
  ? activeClass.value.grade_name + '（' + (activeClass.value.class_name || '未分班级') + '）'
  : '')
const radarData = computed(() => dims.value.map(d => ({ label: dimensionLabel(d.dimension_code), value: metricValue(dimensionFor(activeClass.value, d.dimension_code)) ?? 0, max: d.score_max || 15 })))
function diff(i: number) {
  const classValue = classValues.value[i]
  const gradeValue = gradeValues.value[i]
  return classValue == null || gradeValue == null ? null : (classValue - gradeValue).toFixed(1)
}
function metricText(value: number | null | undefined) {
  if (value == null) return '样本不足'
  return metric.value === 'average' ? value.toFixed(1) : value.toFixed(1) + '%'
}

function save() {
  try { localStorage.setItem('qingxin-notes', JSON.stringify({ notes: notes.value, suggestions: suggestions.value })); status.value = '已保存（浏览器本地草稿）' }
  catch { status.value = '保存失败' }
}
try { const d = JSON.parse(localStorage.getItem('qingxin-notes') || '{}'); notes.value = d.notes || ''; suggestions.value = d.suggestions || '' } catch {}

async function loadReport(taskIds: number[]) {
  loading.value = true
  error.value = ''
  try { report.value = await getAnalyticsReport(taskIds, 'ALL_CALCULATED') }
  catch (err) { report.value = null; error.value = err instanceof Error ? err.message : '报表加载失败' }
  finally { loading.value = false }
}
function onQuery(f: { taskIds: number[]; grade: string; cls: string; metric: string }) {
  selectedGrade.value = f.grade === 'all' ? '' : f.grade
  selectedClass.value = f.cls === 'all' ? '' : f.cls
  metric.value = f.metric
  if (f.taskIds.length) loadReport(f.taskIds)
}
function reset() {
  report.value = null
  error.value = ''
  selectedGrade.value = ''
  selectedClass.value = ''
  metric.value = 'rate'
}
</script>

<template>
  <ReportPageHeader description="查看班级样本质量、维度画像及与所属年级的对比。"/>
  <FilterBar show-grade show-class show-metric :grade-options="gradeOptions" :class-options="classOptions" @query="onQuery" @reset="reset"/>

  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="report && activeClass">
    <div class="kpis">
      <!-- 括号两半必须同宽：这里原本是 `（` 配半角 `)`，渲染成「初二（3班)」，
           而全站其余出处（页头、班级选择器、其他报表）都是全角一对。
           这一串现在也用在下面那两张分布图的标题上，所以归 `activeClassName` 一处拼。 -->
      <KpiCard label="任务目标" :value="activeClass.target_count" :hint="activeClassName"/>
      <KpiCard label="实际应测" :value="activeClass.eligible_count" hint="当前班级"/>
      <KpiCard label="可评价样本" :value="activeClass.sample_count" :hint="'覆盖率 '+ (activeClass.coverage_rate != null ? activeClass.coverage_rate.toFixed(1)+'%' : '—')" tone="green"/>
      <KpiCard label="样本覆盖率" :value="activeClass.coverage_rate != null ? activeClass.coverage_rate.toFixed(1)+'%' : '样本不足'" hint="可评价样本 / 实际应测" tone="blue" icon="chart"/>
    </div>

    <div class="split">
      <section class="card">
        <h2 class="section-title">班级样本质量</h2>
        <div class="callout">
          <strong>覆盖率：{{ activeClass.coverage_rate != null ? activeClass.coverage_rate.toFixed(1)+'%' : '样本不足，比例不展示' }}</strong>
          <span>应测 {{ activeClass.eligible_count }} 人 · 可评价 {{ activeClass.sample_count }} 人</span>
        </div>
        <table class="mini-table">
          <tbody>
            <tr><th>应测人数</th><td>{{ activeClass.eligible_count }}</td></tr>
            <tr><th>可评价样本</th><td>{{ activeClass.sample_count }}</td></tr>
            <tr><th>已完成测评</th><td>{{ activeClass.completed_count }}</td></tr>
            <tr><th>覆盖率</th><td>{{ activeClass.coverage_rate != null ? activeClass.coverage_rate.toFixed(1)+'%' : '样本不足，暂不展示' }}</td></tr>
          </tbody>
        </table>
      </section>
      <section class="card">
        <h2 class="section-title">本班与同年级对比</h2>
        <div v-if="metric==='rate'" class="legend"><span class="dot blue"/>本班 <span class="dot orange"/>同年级</div>
        <GroupedBarChart v-if="hasPublishedComparison && metric==='rate'" :labels="dims.map(d=>dimensionLabel(d.dimension_code))" :seriesA="chartClassValues" :seriesB="chartGradeValues" :max="Math.max(42, ...chartClassValues.filter((v: number | null) => v != null), ...chartGradeValues.filter((v: number | null) => v != null)) * 1.2" suffix="%" labelA="本班" labelB="同年级"/>
        <GroupedBarChart v-else-if="hasPublishedComparison" :labels="dims.map(d=>dimensionLabel(d.dimension_code))" :seriesA="chartClassValues" :seriesB="chartGradeValues" :max="Math.max(15, ...chartClassValues.filter((v: number | null) => v != null), ...chartGradeValues.filter((v: number | null) => v != null)) * 1.2" suffix="分" labelA="本班" labelB="同年级"/>
        <RadarChart v-else-if="hasPublishedComparison" :data="radarData"/>
        <div v-else class="data-empty">当前班级样本量不足，班级与年级的精确{{ metric==='average' ? '平均分' : '高分比例' }}不予展示。上方样本人数仍为真实统计值。</div>
        <p class="hint">比较结果用于教育需求研判，不用于班级排名。</p>
      </section>
    </div>

    <section class="card band-card">
      <h2 class="section-title">关注等级分布</h2>
      <p class="muted tiny">按总分区间分档，每名可评价学生只落一档，三档互不叠加；区间取自本次结果所用的量表评分规则版本。左边是本班，右边是所属年级的全体——与上面「本班与同年级对比」是同一个对照关系，换了一个维度看。</p>
      <div class="band-grid">
        <div class="band-cell">
          <h3 class="band-cell-title">本班 · {{ activeClassName }}</h3>
          <ScoreBandBars :items="activeClass.level_distribution" :totals="totalBands" :total="activeClass.sample_count"/>
        </div>
        <div v-if="gradeForClass" class="band-cell">
          <h3 class="band-cell-title">同年级 · {{ gradeForClass.grade_name }}全年级</h3>
          <ScoreBandBars :items="gradeForClass.level_distribution" :totals="totalBands" :total="gradeForClass.sample_count"/>
        </div>
        <!-- 取不到年级那一组时**不留一个空框**：那一格会是三条 0 高的柱子，看起来像
             「这个年级一个都没有」，而事实是这份报表里没有这一组。 -->
        <div v-else class="band-cell band-cell-empty">当前报表里没有这个年级的汇总数据。</div>
      </div>
    </section>

    <div class="bottom-grid">
      <section class="card">
        <h2 class="section-title">维度对比数据</h2>
        <div class="table-scroll">
          <table>
            <thead><tr><th>维度</th><th>本班</th><th>同年级</th><th>差值</th></tr></thead>
            <tbody>
              <tr v-for="(d,i) in dims" :key="d.dimension_code">
                <td>{{ dimensionLabel(d.dimension_code) }}</td>
                <td>{{ metricText(classValues[i]) }}</td>
                <td>{{ metricText(gradeValues[i]) }}</td>
                <td>{{ diff(i) == null ? '—' : diff(i)+(metric==='average'?'分':'个百分点') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="card">
        <h2 class="section-title">心理老师专业研判</h2>
        <div class="factbox">统计事实：本班各维度高分比例与同年级对比，差异仅为描述性统计。</div>
        <label class="field">专业观察<textarea v-model="notes" class="text-area" rows="4" maxlength="1000" placeholder="结合覆盖率、效度、施测背景填写……"/></label>
        <label class="field">拟开展教育支持<textarea v-model="suggestions" class="text-area" rows="3" maxlength="1000" placeholder="填写教育主题、活动形式……"/></label>
        <div class="action-row"><button class="btn primary" @click="save">保存草稿</button></div>
        <p class="hint" role="status">{{ status || '草稿仅保存在本设备浏览器' }}</p>
      </section>
    </div>
    <PrivacyNote/>
  </template>
  <div v-else-if="report" class="empty">当前条件下无班级数据</div>
  <!-- 空态是一句关于数据的话：进这一页会自动加载最新可分析任务（FilterBar），
       所以走到这里意味着真的没有可用任务。同 ReportExportPage.vue，§14。 -->
  <div v-else class="empty">本学年还没有可用的测评任务</div>
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.split { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px }
.bottom-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 16px; margin-bottom: 16px }
/* 本班 / 同年级各一张，固定两列（这一页的对照关系就是一对，不像年级页那样随年级数变）。 */
.band-card { margin-bottom: 16px }
.band-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 12px }
.band-cell { min-width: 0; border: 1px solid #e6edf6; border-radius: 10px; padding: 12px 14px 6px; background: #fbfdff }
.band-cell-title { margin: 0 0 4px; font-size: 14px; font-weight: 700; color: #1d3f63 }
.band-cell-empty { display: flex; align-items: center; justify-content: center; padding: 34px 18px; color: #687c93; font-size: 13px; text-align: center }
.split > *, .bottom-grid > * { min-width: 0 }
section.card { min-width: 0; padding: 18px; overflow: hidden }
.callout { display: flex; gap: 12px; align-items: center; background: #edf9f2; border: 1px solid #c4e8d7; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px }
.callout strong { color: #17613f; font-size: 18px }
.mini-table { width: 100%; table-layout: fixed; font-size: var(--font-table) }
.mini-table th { width: 45%; background: #f1f6fd; color: #3e5877; padding: 8px; text-align: left; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.mini-table td { padding: 8px; border-top: 1px solid #e6edf6 }
.legend { display: flex; gap: 12px; align-items: center; margin: 8px 0 }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px }
.dot.blue { background: #378af0 } .dot.orange { background: #efa65e }
.factbox { background: #f2f7fd; border-left: 4px solid #3c83df; padding: 10px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 10px }
.data-empty { padding: 34px 18px; border: 1px dashed #cbd8e2; border-radius: 8px; background: #f8fafc; color: #687c93; text-align: center; line-height: 1.7 }
.field { display: grid; gap: 5px; font-weight: 650; margin-top: 10px; font-size: 13px }
.text-area { display: block; width: 100%; min-width: 0; max-width: 100%; min-height: 60px; resize: vertical; padding: 7px 8px; border: 1px solid #cbd8e2; border-radius: 6px; font: inherit }
.action-row { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px }
.table-scroll { max-width: 100%; overflow: auto }
table { width: 100%; border-collapse: collapse; font-size: var(--font-table) }
th { background: #f1f6fd; color: #3e5877; padding: 9px 8px; text-align: left; font-weight: 700 }
td { padding: 9px 8px; border-top: 1px solid #e6edf6 }
tbody tr:hover { background: #f8fbff }
.privacy { margin-top: 16px }
.hint { margin: 8px 0 0 }
@media(max-width:1200px) { .split, .bottom-grid, .band-grid { grid-template-columns: 1fr } }
@media(max-width:1100px) { .kpis { grid-template-columns: 1fr 1fr } }
@media(max-width:600px) {
  .kpis { grid-template-columns: 1fr }
  .callout { align-items: flex-start; flex-direction: column; gap: 4px }
}
</style>
