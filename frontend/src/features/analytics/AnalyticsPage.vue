<script setup lang="ts">
/**
 * 统计分析容器 —— AppLayout 内嵌页面，统一筛选栏 + tab 切换。
 */
import { ref, provide, computed } from 'vue'
import { getAnalyticsReport, type AnalyticsReport } from '../../services/api'
import FilterBar from './components/FilterBar.vue'
import OverviewPage from './views/OverviewPage.vue'
import DimensionsPage from './views/DimensionsPage.vue'
import GradesPage from './views/GradesPage.vue'
import ClassPortraitPage from './views/ClassPortraitPage.vue'
import ReportExportPage from './views/ReportExportPage.vue'

const tab = ref('overview')
const report = ref<AnalyticsReport | null>(null)
const loading = ref(false)
const filterState = ref({ taskIds: [] as number[], validity: 'ALL_CALCULATED' as string })

const tabs = [
  { key: 'overview', label: '筛查总览' },
  { key: 'dimensions', label: '八维度分析' },
  { key: 'grades', label: '年级对比' },
  { key: 'classes', label: '班级画像' },
  { key: 'report', label: '专业解读与导出' }
]

async function loadReport(taskIds: number[], validity = 'ALL_CALCULATED') {
  loading.value = true
  try {
    report.value = await getAnalyticsReport(taskIds, validity as any)
  } catch {
    report.value = null
  } finally {
    loading.value = false
  }
}

function onQuery(f: { taskIds: number[]; validity?: string }) {
  filterState.value = { taskIds: f.taskIds, validity: f.validity || 'ALL_CALCULATED' }
  if (f.taskIds.length) loadReport(f.taskIds, f.validity || 'ALL_CALCULATED')
}

// 向下传递 report 和 loading
provide('analyticsReport', report)
provide('analyticsLoading', loading)
</script>

<template>
  <div class="analytics-page">
    <FilterBar show-validity @query="onQuery"/>

    <nav class="report-tabs" aria-label="报表视图">
      <button v-for="item in tabs" :key="item.key"
        :class="['tab-btn', { active: tab === item.key }]"
        @click="tab = item.key">
        {{ item.label }}
      </button>
    </nav>

    <div v-if="loading" class="loading">加载中…</div>
    <template v-else>
      <OverviewPage v-if="tab === 'overview'" />
      <DimensionsPage v-else-if="tab === 'dimensions'" />
      <GradesPage v-else-if="tab === 'grades'" />
      <ClassPortraitPage v-else-if="tab === 'classes'" />
      <ReportExportPage v-else-if="tab === 'report'" />
      <div v-else class="empty">请选择测评任务后点击查询</div>
    </template>
  </div>
</template>

<style scoped>
.analytics-page { min-width: 0; padding-top: 4px }
.report-tabs { display: flex; gap: 2px; max-width: 100%; margin: 16px 0 20px; overflow-x: auto; border-bottom: 2px solid #e8eef6; scrollbar-width: thin }
.tab-btn { padding: 10px 18px; border: 0; border-bottom: 3px solid transparent; background: transparent; color: #536878; cursor: pointer; font-weight: 600; font-size: 14px; border-radius: 6px 6px 0 0; transition: all .15s }
.tab-btn { flex: 0 0 auto; white-space: nowrap }
.tab-btn:hover { color: #0876d9; background: #f0f6fc }
.tab-btn.active { border-bottom-color: #0876d9; color: #075ea9; background: #f0f6fc }
.loading { text-align: center; padding: 60px 0; color: #687c93; font-size: 15px }
.empty { text-align: center; padding: 40px 0; color: #708198; font-size: 14px }
@media(max-width: 600px) {
  .report-tabs { margin-top: 12px }
  .tab-btn { padding: 9px 12px; font-size: 13px }
}
</style>
