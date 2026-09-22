<script setup lang="ts">
/** RPT-01 全校心理筛查预警总览 —— 独立页面，自带筛选栏。 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import ColumnChart from '../components/ColumnChart.vue'
import CompletionDonut from '../components/CompletionDonut.vue'
import MetricStrip from '../components/MetricStrip.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import { getAnalyticsReport, type AnalyticsReport } from '../../../services/api'

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

const signalTypes = computed(() => overview.value?.signal_type_stats?.map(s => ({
  label: s.signal_type === 'MANUAL_REVIEW_REQUIRED' ? '重点题人工复核' :
         s.signal_type === 'RETEST_RECOMMENDED' ? '效度复测建议' :
         s.signal_type === 'SCREENING_SIGNAL' ? '普通筛查信号' : s.signal_type,
  value: s.student_count
})) || [])
// 覆盖率是「可能不发布」的比率：分母小于 MIN_COHORT_FOR_AGGREGATE 时服务端发 None。
// **不许 `?? 0` 把它抹平**——`0` 是「一个都没测」，`None` 是「这几个人算出来不足为凭」，
// 两者在屏幕上必须长得不一样（CLAUDE.md §11）。所以这里按 GradesPage 的既有写法分成两支：
// 进柱状图的只有真值，被抑制的那些在下面单列一句话说明。
const publishedGradeRates = computed(() => grades.value.flatMap(g =>
  g.coverage_rate == null ? [] : [{ grade: g, value: g.coverage_rate }]
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

    <MetricStrip :items="[
      { label: '普通筛查信号', value: (signalTypes.find(s=>s.label==='普通筛查信号')?.value || 0)+' 人', hint: '允许与其他信号交叉' },
      { label: '重点题待人工复核', value: pendingReview+' 人', hint: '具体回答不在报表展示' },
      { label: '效度建议复测', value: (signalTypes.find(s=>s.label==='效度复测建议')?.value || 0)+' 人', hint: '单独提示，不等于无效' },
      { label: '待处理复核工作', value: pendingReview+' 项', hint: '仅心理老师授权处理' }
    ]"/>

    <div class="overview-chart">
      <section class="card">
        <h2 class="section-title">筛查信号类型分布</h2>
        <p class="muted tiny">同一学生可能触发多类信号，分类人数不可求和。</p>
        <ColumnChart :labels="signalTypes.map(s=>s.label)" :values="signalTypes.map(s=>s.value)" :max="Math.max(10, ...signalTypes.map(s=>s.value)) * 1.2" :colors="['#3286f0','#f06d78','#ffad68']"/>
      </section>
      <section class="card">
        <h2 class="section-title">测评完成情况</h2>
        <CompletionDonut :completed="completed" :target="target"/>
      </section>
      <section class="card">
        <h2 class="section-title">各年级样本覆盖率</h2>
        <ColumnChart
          v-if="publishedGradeRates.length"
          :labels="publishedGradeRates.map(item=>item.grade.grade_name)"
          :values="publishedGradeRates.map(item=>item.value)"
          :max="100" suffix="%"/>
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
          ⑤ 德育领导默认只查看授权聚合信息。
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
