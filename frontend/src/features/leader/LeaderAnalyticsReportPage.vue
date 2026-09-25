<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ReportPageHeader from '../analytics/components/ReportPageHeader.vue'
import ErrorState from '../../components/ErrorState.vue'
import { listProfessionalReports, type ProfessionalReport } from '../../services/api'

const reports = ref<ProfessionalReport[]>([])
const selected = ref<ProfessionalReport | null>(null)
const loading = ref(true)
const error = ref('')
const sections = [
  { key:'overall_summary', label:'整体情况说明' },
  { key:'dimension_interpretation', label:'重点维度解释' },
  { key:'sample_validity_note', label:'样本覆盖及效度说明' },
  { key:'support_plan', label:'后续教育支持计划' }
] as const
onMounted(async () => { try { reports.value = await listProfessionalReports() } catch(e) { error.value=e instanceof Error?e.message:'加载失败' } finally { loading.value=false } })
</script>

<template>
  <!-- 标题**不在这里传**：`ReportPageHeader` 的标题唯一出处是路由的 `meta.title`
       （`routes.ts` 的 `leader/analytics/report`），组件只声明了 `description` 一个 prop。
       此前这里多传了一个 `title="学校心理工作分析摘要"`，它既没被声明也没被使用——
       Vue 会把它当成普通属性落到根 `<header>` 上，于是同一个字符串在页面上有两处定义：
       看得见的那一处来自路由，而浏览器上多出来的一个原生 tooltip 来自这一行。
       两处此刻**恰好一致**，所以看不出问题；改掉路由标题的那一刻它们才会分岔，
       而分岔的那一半（tooltip）没有任何测试看得见。删掉它，回到单一出处。 -->
  <ReportPageHeader description="查看心理老师已经发布的聚合专业摘要；本页不提供专业解读编辑或个体答卷内容。"/>
  <ErrorState v-if="error" :message="error"/>
  <div v-else-if="loading" class="loading">加载中…</div>
  <div v-else class="report-layout">
    <section class="card">
      <h2 class="section-title">已发布报告</h2>
      <button v-for="item in reports" :key="item.id" class="report-item" @click="selected=item">
        <strong>{{ item.title }}</strong><span>{{ item.report_no }} · 版本 {{ item.current_version }}</span>
      </button>
      <div v-if="!reports.length" class="empty">暂无已发布专业报告</div>
    </section>
    <section v-if="selected?.content" class="card">
      <h2 class="section-title">{{ selected.title }}</h2>
      <dl><template v-for="section in sections" :key="section.key"><dt>{{ section.label }}</dt><dd>{{ selected.content[section.key] || '—' }}</dd></template></dl>
      <p class="hint">仅展示已发布的聚合摘要，不包含原始答卷、重点题具体答案、家庭回访正文或私密记录。</p>
    </section>
  </div>
</template>

<style scoped>.report-layout{display:grid;grid-template-columns:360px 1fr;gap:16px}.report-item{display:grid;width:100%;text-align:left;padding:12px;margin:8px 0;border:1px solid #dce6ef;border-radius:8px;background:#fff;cursor:pointer}.report-item span,.hint{color:var(--muted);font-size:13px;margin-top:4px}dt{font-weight:700;margin-top:18px}dd{white-space:pre-wrap;margin:6px 0;line-height:1.8}.empty{padding:30px;text-align:center;color:var(--muted)}@media(max-width:900px){.report-layout{grid-template-columns:1fr}}</style>
