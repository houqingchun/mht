<script setup lang="ts">
/** RPT-02 全校八维度分析 —— 独立页面。 */
import { computed, ref } from 'vue'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import HorizontalBars from '../components/HorizontalBars.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import { getAnalyticsReport, type AnalyticsReport } from '../../../services/api'
import { dimensionLabel } from '../../../services/labels'

const report = ref<AnalyticsReport | null>(null)
const loading = ref(false)
const error = ref('')
const subTab = ref('rate')
const selectedDim = ref(0)

const dims = computed(() => report.value?.dimensions || [])
const quality = computed(() => report.value?.sample_quality)
const highRates = computed(() => dims.value.map(d => d.high_score_rate || 0))
const means = computed(() => dims.value.map(d => d.mean_score || 0))
const hasPublishedRates = computed(() => dims.value.some(d => d.high_score_rate != null))
const hasPublishedMeans = computed(() => dims.value.some(d => d.mean_score != null))
const distData = computed(() => dims.value.map(d => ({
  low: d.distribution?.[0]?.count || 0, medium: d.distribution?.[1]?.count || 0, high: d.distribution?.[2]?.count || 0
})))

async function loadReport(taskIds: number[], validity = 'ALL_CALCULATED') {
  loading.value = true
  error.value = ''
  try { report.value = await getAnalyticsReport(taskIds, validity as any) }
  catch (err) { report.value = null; error.value = err instanceof Error ? err.message : '报表加载失败' }
  finally { loading.value = false }
}
function onQuery(f: { taskIds: number[]; validity: string }) { if (f.taskIds.length) loadReport(f.taskIds, f.validity) }
function reset() { report.value = null; error.value = ''; subTab.value = 'rate'; selectedDim.value = 0 }
</script>

<template>
  <ReportPageHeader title="全校八维度分析" description="查看各心理维度的高分比例、平均得分和分布情况。"/>
  <FilterBar show-validity @query="onQuery" @reset="reset"/>

  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="report">
    <div class="kpis">
      <KpiCard label="纳入分析人数" :value="quality?.n_evaluable?.toLocaleString('zh-CN') || '—'" hint="当前样本口径"/>
      <KpiCard label="测评覆盖率" :value="quality?.coverage_rate != null ? quality.coverage_rate.toFixed(1)+'%' : '—'" hint="分析人数 / 实际应测" tone="green"/>
      <KpiCard label="各维度高分人次" :value="dims.reduce((a,d)=>a+(d.high_score_count||0),0)" hint="同一学生可重复计入" tone="red" icon="alert"/>
      <KpiCard label="效度建议复测" :value="quality?.validity_flagged_count || 0" hint="独立展示" tone="amber" icon="clipboard"/>
    </div>

    <div class="role-note"><b>样本说明</b><span class="minor">不同分析口径须由心理专业负责人确认。</span></div>

    <div class="twocol">
      <section class="card">
        <h2 class="section-title">八维度分析</h2>
        <div class="inner-tabs">
          <button class="inner-tab" :class="{active: subTab==='rate'}" @click="subTab='rate'">高分比例</button>
          <button class="inner-tab" :class="{active: subTab==='average'}" @click="subTab='average'">平均得分</button>
          <button class="inner-tab" :class="{active: subTab==='distribution'}" @click="subTab='distribution'">得分分布</button>
        </div>
        <template v-if="subTab==='rate'">
          <HorizontalBars v-if="hasPublishedRates" :labels="dims.map(d=>dimensionLabel(d.dimension_code))" :values="highRates" :max="Math.max(25,...highRates)" suffix="%"/>
          <div v-else class="data-empty">当前样本量不足，暂不展示可反推个体的精确高分比例。</div>
          <p class="hint">高分比例按维度可评价人数计算。</p>
        </template>
        <template v-else-if="subTab==='average'">
          <HorizontalBars v-if="hasPublishedMeans" :labels="dims.map(d=>dimensionLabel(d.dimension_code))" :values="means" :max="Math.max(15,...means)" suffix="分"/>
          <div v-else class="data-empty">当前样本量不足，暂不展示可反推个体的精确平均分。</div>
          <div class="notice">不同维度满分不同，原始平均分不用于跨维度比较。</div>
        </template>
        <template v-else>
          <div class="dist-stack">
            <div v-for="(d,i) in dims" :key="d.dimension_code" class="dist-row">
              <div class="between"><b>{{ dimensionLabel(d.dimension_code) }}</b>
                <span class="minor">低 {{ distData[i].low }} · 中 {{ distData[i].medium }} · 高 {{ distData[i].high }}</span></div>
              <div class="dist-track">
                <span class="low" :style="{width: d.n_evaluable>0 ? distData[i].low/d.n_evaluable*100+'%' : '0%'}"/>
                <span class="medium" :style="{width: d.n_evaluable>0 ? distData[i].medium/d.n_evaluable*100+'%' : '0%'}"/>
                <span class="high" :style="{width: d.n_evaluable>0 ? distData[i].high/d.n_evaluable*100+'%' : '0%'}"/>
              </div>
            </div>
          </div>
          <div class="legend"><span class="legend-dot low"/>低分 <span class="legend-dot medium"/>中分 <span class="legend-dot high"/>高分</div>
        </template>
      </section>
      <section class="card">
        <h2 class="section-title">八维度详细数据</h2>
        <div class="table-scroll">
          <table>
            <thead><tr><th>维度</th><th>有效样本</th><th>平均分</th><th>高分人数</th><th>高分比例</th></tr></thead>
            <tbody>
              <tr v-for="(d,i) in dims" :key="d.dimension_code" :class="{selected: selectedDim===i}" @click="selectedDim=i;subTab='distribution'" style="cursor:pointer">
                <td>{{ dimensionLabel(d.dimension_code) }}</td><td>{{ d.n_evaluable }}</td>
                <td>{{ d.mean_score != null ? d.mean_score.toFixed(1) : '—' }}</td>
                <td>{{ d.high_score_count ?? '—' }}</td>
                <td>{{ d.high_score_rate != null ? d.high_score_rate.toFixed(1)+'%' : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
    <PrivacyNote/>
  </template>
  <div v-else class="empty">请选择测评任务后点击查询</div>
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.twocol { display: grid; grid-template-columns: minmax(0, 1.12fr) minmax(0, 1fr); gap: 16px; margin-bottom: 16px }
.twocol > * { min-width: 0 }
section.card { min-width: 0; padding: 18px; overflow: hidden }
.role-note { display: flex; gap: 10px; align-items: flex-start; background: #f7fbff; border: 1px solid #d7e8fb; padding: 10px 14px; border-radius: 9px; margin: 0 0 16px }
.inner-tabs { display: flex; gap: 4px; margin-bottom: 14px; border-bottom: 1px solid #e8eef6 }
.inner-tab { padding: 8px 14px; border: 0; background: none; color: #617994; font-weight: 600; cursor: pointer; border-bottom: 2px solid transparent }
.inner-tab.active { border-bottom-color: #0876d9; color: #0876d9 }
.dist-stack { display: grid; gap: 12px }
.dist-row { display: grid; gap: 3px }
.between { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap }
.dist-track { display: flex; height: 12px; border-radius: 4px; overflow: hidden; background: #eef4fa }
.dist-track span { height: 100% }
.low { background: #b9e3d5 } .medium { background: #f5d59d } .high { background: #e99ba2 }
.legend { display: flex; gap: 14px; flex-wrap: wrap; color: #667c96; font-size: 12px; margin-top: 10px }
.legend-dot { display: inline-block; width: 10px; height: 10px; margin-right: 4px; border-radius: 2px }
.legend-dot.low { background: #b9e3d5 } .legend-dot.medium { background: #f5d59d } .legend-dot.high { background: #e99ba2 }
table { width: 100%; border-collapse: collapse; font-size: var(--font-table) }
th { background: #f1f6fd; color: #3e5877; padding: 9px 8px; text-align: left; font-weight: 700 }
td { padding: 9px 8px; border-top: 1px solid #e6edf6 }
tbody tr:hover { background: #f8fbff }
tbody tr.selected { background: #e8f3ff }
.table-scroll { max-width: 100%; overflow: auto }
.notice { background: #ecf5ff; border: 1px solid #cbdfff; border-radius: 9px; padding: 12px 16px; color: #2d5078; margin-top: 10px; font-size: var(--font-small) }
.data-empty { padding: 34px 18px; border: 1px dashed #cbd8e2; border-radius: 8px; background: #f8fafc; color: #687c93; text-align: center; line-height: 1.7 }
.hint { margin: 10px 0 0 }
.privacy { margin-top: 16px }
@media(max-width: 1200px) { .twocol { grid-template-columns: 1fr } }
@media(max-width: 1100px) { .kpis { grid-template-columns: 1fr 1fr } }
@media(max-width: 600px) {
  .kpis { grid-template-columns: 1fr }
  .inner-tabs { overflow-x: auto }
  .inner-tab { flex: 0 0 auto; white-space: nowrap }
}
</style>
