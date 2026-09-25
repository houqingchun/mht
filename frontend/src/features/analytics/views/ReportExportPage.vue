<script setup lang="ts">
/** RPT-09 报表专业解读与导出 —— 独立页面。 */
import { ref } from 'vue'
import FilterBar from '../components/FilterBar.vue'
import ReportPageHeader from '../components/ReportPageHeader.vue'
import KpiCard from '../components/KpiCard.vue'
import PrivacyNote from '../components/PrivacyNote.vue'
import ErrorState from '../../../components/ErrorState.vue'
import { createProfessionalReport, exportProfessionalReport, getAnalyticsReport, publishProfessionalReport, saveProfessionalReport, type AnalyticsReport } from '../../../services/api'

const report = ref<AnalyticsReport | null>(null)
const loading = ref(false)
const error = ref('')
const sub = ref('interpretation')
const status = ref('')
const lastExportAt = ref('')
const draft = ref(['', '', '', ''])
const selectedTaskIds = ref<number[]>([])
const reportId = ref<number | null>(null)
const fields = ['整体情况说明', '重点维度解释', '样本覆盖及效度说明', '后续教育支持计划']

async function loadReport(taskIds: number[]) {
  loading.value = true
  error.value = ''
  try { report.value = await getAnalyticsReport(taskIds, 'ALL_CALCULATED') }
  catch (err) { report.value = null; error.value = err instanceof Error ? err.message : '报表加载失败' }
  finally { loading.value = false }
}
function onQuery(f: { taskIds: number[] }) { selectedTaskIds.value = f.taskIds; reportId.value = null; draft.value = ['', '', '', '']; if (f.taskIds.length) loadReport(f.taskIds) }
function reset() { report.value = null; error.value = ''; status.value = ''; lastExportAt.value = ''; sub.value = 'interpretation' }

function content() { return { overall_summary:draft.value[0], dimension_interpretation:draft.value[1], sample_validity_note:draft.value[2], support_plan:draft.value[3] } }
async function save() {
  try {
    const saved = reportId.value
      ? await saveProfessionalReport(reportId.value, content())
      : await createProfessionalReport({title: report.value?.task?.name + ' 专业分析报告', task_ids:selectedTaskIds.value, analysis_mode:'ALL_CALCULATED', ...content()})
    reportId.value = saved.id; status.value = `草稿已保存至服务器（${saved.report_no}）`
  } catch (e) { status.value = e instanceof Error ? e.message : '保存失败' }
}
async function publishReport() {
  if (!reportId.value) await save()
  if (!reportId.value) return
  try { await publishProfessionalReport(reportId.value); status.value = '报告已发布，当前版本已锁定' } catch(e) { status.value=e instanceof Error?e.message:'发布失败' }
}
async function exportReport() {
  if (!reportId.value) { status.value='请先保存报告'; return }
  const purpose = window.prompt('请输入导出用途')?.trim(); if (!purpose) return
  try { await exportProfessionalReport(reportId.value, purpose); lastExportAt.value=new Date().toLocaleString('zh-CN',{hour12:false}); status.value='正式报告已通过导出中心生成并下载' } catch(e) { status.value=e instanceof Error?e.message:'导出失败' }
}
</script>

<template>
  <ReportPageHeader description="在聚合统计基础上记录专业意见，并按授权范围生成报告。"/>
  <FilterBar @query="onQuery" @reset="reset"/>

  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <template v-else-if="report">
    <div class="sub-tabs">
      <button class="sub-tab" :class="{active: sub==='interpretation'}" @click="sub='interpretation'">专业解读</button>
      <button class="sub-tab" :class="{active: sub==='export'}" @click="sub='export'">报表导出</button>
    </div>

    <div class="kpis">
      <KpiCard label="本次分析任务" :value="report.tasks?.length || 1" hint="当前选定任务"/>
      <KpiCard label="可评价人数" :value="report.sample_quality?.n_evaluable || 0" hint="来自实际测评结果" tone="green"/>
      <KpiCard label="统计维度" :value="report.dimensions.length" hint="当前报告口径"/>
      <KpiCard label="专业记录" :value="status ? '草稿' : '待审核'" hint="统计事实与人工判断分开" tone="amber" icon="clipboard"/>
    </div>

    <template v-if="sub==='interpretation'">
      <div class="split">
        <section class="card">
          <h2 class="section-title">心理老师专业解读</h2>
          <div class="notice">任务：{{ report.task.name }}；正式可评价样本为 {{ report.sample_quality?.n_evaluable }} 人。</div>
          <label v-for="(f, i) in fields" :key="f" class="field">{{ i + 1 }}. {{ f }}
            <textarea v-model="draft[i]" :placeholder="'请据统计事实及专业判断填写'+f" rows="3" maxlength="1200" class="text-area"/>
          </label>
          <div class="action-row">
            <button class="btn" @click="save">保存草稿</button>
            <button class="btn" @click="publishReport">确认并发布</button>
            <button class="btn primary" @click="sub='export'">进入导出设置</button>
          </div>
          <p class="hint" role="status">{{ status }}</p>
        </section>
        <section class="card">
          <h2 class="section-title">报告状态与说明</h2>
          <div class="notice"><strong>心理测评不是医学诊断</strong><br/>群体指标、样本覆盖及效度信息需共同解释。</div>
          <ul class="advice">
            <li>报告数字由统计结果生成，专业文字由心理老师审核。</li>
            <li>报告仅呈现满足隐私阈值的聚合统计，不展示个体答卷。</li>
            <li>实际导出流程需服务端权限、用途、审计和下载有效期控制。</li>
          </ul>
        </section>
      </div>
    </template>

    <template v-else>
      <div class="split">
        <section class="card">
          <h2 class="section-title">报表导出设置</h2>
          <div class="notice">导出范围：当前所选任务的聚合分析结果。</div>
          <p class="field">文件格式：CSV（由服务端生成并纳入导出治理）</p>
          <label class="checkbox-line"><input type="checkbox" checked disabled/> 启用隐私保护（强制）</label>
          <div class="action-row"><button class="btn primary" @click="exportReport">生成报告</button></div>
          <p class="hint" role="status">{{ status }}</p>
        </section>
        <section class="card">
          <h2 class="section-title">导出范围说明</h2>
          <table>
            <tbody>
              <tr><th>聚合统计报告</th><td>当前任务实际测评数据</td></tr>
              <tr><th>个人身份数据</th><td>不包含</td></tr>
              <tr><th>角色</th><td>心理老师</td></tr>
              <tr><th>当前文件</th><td>受控导出 CSV</td></tr>
            </tbody>
          </table>
          <div class="notice">报告内容来自当前选定任务；导出文件不包含个人身份与个体答卷数据。</div>
        </section>
      </div>
      <PrivacyNote/>
      <section class="card audit-section">
        <h2 class="section-title">专业解释与导出记录</h2>
        <div class="table-scroll">
          <table>
            <thead><tr><th>时间</th><th>操作</th><th>范围</th><th>操作角色</th><th>状态</th></tr></thead>
            <tbody v-if="lastExportAt">
              <tr><td>{{ lastExportAt }}</td><td>生成聚合报告</td><td>当前所选任务</td><td>心理老师</td><td>{{ status }}</td></tr>
            </tbody>
          </table>
          <div v-if="!lastExportAt" class="data-empty">当前会话尚未生成报告。</div>
        </div>
      </section>
    </template>
  </template>
  <!--
    空态是一句**关于数据的话**，不是一句操作指导语（§14）。
    这一支此前写着「请选择测评任务后点击查询」——而进这一页时会自动加载最新可分析
    任务（`FilterBar.loadTasks`），所以走到这里意味着**真的没有可用任务**，
    此时那个下拉框本身就选不出东西来。一句话把用户支使去做一件做不到的事，
    比不说更糟。
  -->
  <div v-else class="empty">本学年还没有可用的测评任务</div>
</template>

<style scoped>
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
.sub-tabs { display: flex; gap: 6px; margin-bottom: 16px; border-bottom: 2px solid #e8eef6 }
.sub-tab { padding: 9px 16px; border: 0; background: none; color: #617994; font-weight: 600; cursor: pointer; border-bottom: 3px solid transparent }
.sub-tab.active { border-bottom-color: #0876d9; color: #0876d9 }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin: 0 0 16px }
.split { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-bottom: 16px }
.split > * { min-width: 0 }
section.card { min-width: 0; padding: 18px; overflow: hidden }
.audit-section { margin-top: 16px }
.data-empty { padding: 22px 14px; color: var(--muted); text-align: center }
.field { display: grid; gap: 6px; font-weight: 650; margin-top: 12px; font-size: 13px }
.text-area { display: block; width: 100%; min-width: 0; max-width: 100%; min-height: 55px; resize: vertical; padding: 7px 8px; border: 1px solid #cbd8e2; border-radius: 6px; font: inherit }
.action-row { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px }
.checkbox-line { display: flex; gap: 7px; align-items: center; margin: 14px 0 }
.checkbox-line input { min-height: auto }
.advice { padding-left: 20px; color: #4d6580; line-height: 2 }
.hint { margin: 6px 0 }
table { width: 100%; border-collapse: collapse; font-size: var(--font-table) }
th { background: #f1f6fd; color: #3e5877; padding: 9px 8px; text-align: left; font-weight: 700 }
td { padding: 9px 8px; border-top: 1px solid #e6edf6 }
tbody tr:hover { background: #f8fbff }
@media(max-width:1200px) { .split { grid-template-columns: 1fr } }
@media(max-width:1100px) { .kpis { grid-template-columns: 1fr 1fr } }
@media(max-width:600px) {
  .kpis { grid-template-columns: 1fr }
  .sub-tabs { max-width: 100%; overflow-x: auto }
  .sub-tab { flex: 0 0 auto; white-space: nowrap }
  .action-row { align-items: stretch; flex-direction: column }
  .action-row .btn { width: 100% }
}
</style>
