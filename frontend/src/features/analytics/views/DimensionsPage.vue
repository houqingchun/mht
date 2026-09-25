<script setup lang="ts">
/** RPT-02 全校八维度分析 —— 独立页面。 */
import { computed, onMounted, ref } from 'vue'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import HorizontalBars from '../components/HorizontalBars.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import FormDialog, { type FormField } from '../../../components/FormDialog.vue'
import {
  downloadValidityRetestCsv,
  getAnalyticsReport,
  getMe,
  type AnalyticsReport,
} from '../../../services/api'
import { dimensionLabel } from '../../../services/labels'
import { showToast } from '../../../services/toast'

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

/**
 * 已经**应用**的那一次筛选（2026-09-24）。
 *
 * 导出按钮必须拿它、而不是拿 `FilterBar` 里的输入框当前值：用户在筛选条上改了任务但
 * 还没点「查询」，屏幕上那份报表仍是上一次的结果，此时导出的应当是**屏幕上这一份**。
 * 少了它，KPI 上那个数与导出的行数就会各说各话（§11：指标卡上的数必须与它点进去的
 * 那个列表同源）——而两个数各自都是对的，看不出来。
 *
 * 取的是 `loadReport` 实参的副本而不是从 `report` 反推：`analytics_report` 的响应里没有
 * 「这次筛选了哪几个任务」这一项，屏幕上也补不出来。
 *
 * **只记 `task_ids`，不记效度口径**：那份名单的判据（`_is_validity_flagged`）与
 * `analysis_mode` 无关——服务端两处都不看它，所以带着它只会在请求体里多一个不生效的
 * 字段，而「传了不生效」与「不支持」是分不开的（`ExportRequest` 那条注释记着同一件事）。
 */
const appliedTaskIds = ref<number[]>([])

async function loadReport(taskIds: number[], validity = 'ALL_CALCULATED') {
  loading.value = true
  error.value = ''
  try {
    report.value = await getAnalyticsReport(taskIds, validity as any)
    // 成功之后才记：失败时 `report` 是 null，整个报表块（含那枚按钮）本来就不出现，
    // 记下来只会留下一次指向空报表的筛选。
    appliedTaskIds.value = [...taskIds]
  }
  catch (err) { report.value = null; error.value = err instanceof Error ? err.message : '报表加载失败' }
  finally { loading.value = false }
}
function onQuery(f: { taskIds: number[]; validity: string }) { if (f.taskIds.length) loadReport(f.taskIds, f.validity) }
function reset() {
  report.value = null; error.value = ''; subTab.value = 'rate'; selectedDim.value = 0
  appliedTaskIds.value = []
}

/**
 * 「效度建议复测」那一格背后是谁（2026-09-24）。
 *
 * `validity_flagged_count` 与导出走的是**同一个谓词**（`_is_validity_flagged`，服务端
 * `analytics_service.py` 一处定义），而且`analysis_mode` 对它两边都不生效——所以
 * 「上方写 3 人」与「文件里 3 行」是构造上相等的，不靠两处各自记得。
 */
const validityCount = computed(() => quality.value?.validity_flagged_count || 0)

/**
 * 谁能看到这枚导出按钮。
 *
 * 服务端三道门槛的最后一道是 `ensure_student_result_reader`（`STUDENT_PSYCH_DETAIL:
 * {SCOPED}` 且 `ORG_ACCOUNT: {MANAGE, READ_BASIC}`），它把**德育领导与系统管理员**
 * 都挡在外面——而这一页的 `meta.role` 同时含 counselor 与 leader，所以领导会看见一个
 * 点下去必然 403 的按钮。
 *
 * 前端跟着分岔，**不是因为藏起来更安全**（§4：后端那三道门才是权威），而是因为一个
 * **默认落地就是 403** 的按钮在领导那儿看起来就是坏了，而它其实是一个稳定事实
 * （他的能力集是聚合与摘要，这份名单逐行印着学号与姓名）。
 *
 * **按 `role_code` 猜，是这个判据的已知代价**——与 `TasksPage.vue` 的 `canReadDetail`
 * 逐字同源：`/auth/me` 不下发 capabilities，所以前端只能照角色分；学校把权限矩阵改过
 * 之后两边会分岔，那一种不一致由服务端那句原文说清楚（它落在 toast 上），
 * 不由前端假装判断得出来。
 */
const roleCode = ref('')
const canExportRetest = computed(() => roleCode.value === 'counselor')

onMounted(async () => {
  // 拿不到就保持 ''——按钮整块不出现。这里刻意不报错、不弹 toast：这一页的主人是那份
  // 报表，`/auth/me` 失败时下面那次查询也会各自报自己的错，不需要再多一条。
  try { roleCode.value = (await getMe()).role_code } catch { roleCode.value = '' }
})

const exporting = ref(false)
const showForm = ref(false)
const formTitle = ref('')
const formFields = ref<FormField[]>([])
const formSubmitText = ref('提交')
let formResolve: ((values: Record<string, string>) => void) | null = null

function showFormDialog(title: string, fields: FormField[], submitText = '提交'): Promise<Record<string, string>> {
  formTitle.value = title
  formFields.value = fields
  formSubmitText.value = submitText
  showForm.value = true
  return new Promise((resolve) => { formResolve = resolve })
}
function onFormSubmit(values: Record<string, string>) { if (formResolve) formResolve(values) }
function onFormCancel() { if (formResolve) formResolve({}) }

/** 导出「效度建议复测」那一格背后的学生名册。 */
async function exportValidityRetest() {
  if (!appliedTaskIds.value.length) return
  const values = await showFormDialog(
    '导出「效度建议复测」的学生名单',
    [{
      key: 'purpose', label: '导出用途', type: 'text', required: true, maxLength: 255,
      placeholder: '如：交德育处安排复测',
      // 复用名册导出那一句既有措辞（`TasksPage.vue` 的完成明细导出）：`purpose` 是
      // 受控导出唯一的说明字段，而这句话解释了它为什么必填。
      hint: '这一句会进审计，也是事后回答「这份文件为什么被导出去」的唯一依据。',
    }],
    '导出'
  )
  // 取消时 `onFormCancel` 回的是一个空对象。判 `purpose` 而不是判 `values`：
  // 用户把用途留空点提交也会走到这里，两者该做同一件事——什么都不做。
  if (!values.purpose) return
  exporting.value = true
  try {
    const job = await downloadValidityRetestCsv(appliedTaskIds.value, '效度复测名单', values.purpose)
    showToast('success', `已导出效度复测名单（作业 ${job.job_no}，共 ${job.row_count} 人），可在「导出中心」重下`)
  } catch (err) {
    // 服务端那句 403 的原文就落在这一句上，所以领导（或矩阵被改过的心
    // 理老师）读到的是一句说得出原因的话，而不是「导出失败」。
    showToast('error', err instanceof Error ? err.message : '导出失败')
  } finally { exporting.value = false }
}
</script>

<template>
  <ReportPageHeader description="查看当前数据范围内各心理维度的高分比例、平均得分和分布情况。"/>
  <FilterBar show-validity @query="onQuery" @reset="reset"/>

  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="report">
    <div class="kpis">
      <KpiCard label="纳入分析人数" :value="quality?.n_evaluable?.toLocaleString('zh-CN') || '—'" hint="当前样本口径"/>
      <KpiCard label="测评覆盖率" :value="quality?.coverage_rate != null ? quality.coverage_rate.toFixed(1)+'%' : '—'" hint="分析人数 / 实际应测" tone="green"/>
      <KpiCard label="各维度高分人次" :value="dims.reduce((a,d)=>a+(d.high_score_count||0),0)" hint="同一学生可重复计入" tone="red" icon="alert"/>
      <KpiCard label="效度建议复测" :value="validityCount" hint="独立展示" tone="amber" icon="clipboard"/>
    </div>

    <!-- 效度复测名单的导出入口（2026-09-24）。
         放在 KPI 行下面紧挨着，是因为这一行就是它背后的那个数——「3 人」之后紧接着
         一句「这 3 人是谁」。
         两个方向都要说得出话：有人时按钮 + 名单里有什么；没人时不是一枚灰按钮，
         而是一句关于数据的话（§27：灰掉的按钮必须说得出为什么）。 -->
    <div v-if="canExportRetest" class="toolbar export-row">
      <template v-if="validityCount > 0">
        <button class="btn" :disabled="exporting" @click="exportValidityRetest">
          {{ exporting ? '导出中…' : '导出效度复测名单' }}
        </button>
        <span class="minor">
          名单按上方「效度建议复测」同一口径（当前查询的这些任务），含学号 / 姓名 / 年级 / 班级 / 性别 / 年龄 / 学籍状态与效度分。
        </span>
      </template>
      <span v-else class="minor">当前查询的这些任务里没有需要复测的学生，无需导出名单。</span>
    </div>

    <FormDialog
      :open="showForm" :title="formTitle" :fields="formFields" :submit-text="formSubmitText"
      @submit="onFormSubmit" @cancel="onFormCancel" @update:open="showForm = $event"/>

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
  <!-- 空态是一句关于数据的话：进这一页会自动加载最新可分析任务（FilterBar），
       所以走到这里意味着真的没有可用任务。同 ReportExportPage.vue，§14。 -->
  <div v-else class="empty">本学年还没有可用的测评任务</div>
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.twocol { display: grid; grid-template-columns: minmax(0, 1.12fr) minmax(0, 1fr); gap: 16px; margin-bottom: 16px }
.twocol > * { min-width: 0 }
section.card { min-width: 0; padding: 18px; overflow: hidden }
.role-note { display: flex; gap: 10px; align-items: flex-start; background: #f7fbff; border: 1px solid #d7e8fb; padding: 10px 14px; border-radius: 9px; margin: 0 0 16px }
/* `.toolbar` 是全站那个 flex 行（styles.css），这里只补它与上下的间距——
   KPI 行自带 16px 下边距，所以这一行只留下面那一段。 */
.export-row { margin: 0 0 16px }
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
