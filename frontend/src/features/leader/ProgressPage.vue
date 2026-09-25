<script setup lang="ts">
import { onMounted, ref } from 'vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { getMe, getLeaderProgress, type LeaderProgressItem } from '../../services/api'
import { useRouter } from 'vue-router'
import {
  CASE_STATUS_ORDER,
  LEVEL_ORDER,
  levelLabel,
  levelTone,
  statusLabel,
  statusTone
} from '../../services/labels'
import DataTable, { type Column } from '../../components/DataTable.vue'

const router = useRouter()
const columns: Column[] = [
  { key: 'student_name', label: '学生', sortable: true },
  { key: 'grade', label: '年级班级', sortable: true },
  { key: 'total_level', label: '关注等级', sortable: true, order: LEVEL_ORDER },
  { key: 'case_status', label: '当前阶段', sortable: true, order: CASE_STATUS_ORDER },
  { key: 'owner_name', label: '负责人', sortable: true },
  { key: 'next_follow_up_date', label: '下次跟进', sortable: true },
  { key: 'overdue', label: '是否逾期', sortable: true }
]

const progress = ref<LeaderProgressItem[]>([])
const loading = ref(true)
const error = ref('')


async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'leader') {
      await router.push('/login')
      return
    }
    progress.value = await getLeaderProgress()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">重点关注学生治理</div>
        <h1>重点进展</h1>
        <p class="page-desc">德育领导查看必要的身份摘要、责任人和进度，不展示原始答案及访谈正文。</p>
        <!-- 口径要写在界面上：这一页只列**在办**的档案。不说的话，读者会拿它当
             「全校重点学生名单」，而去年已经了结的那些不在里面。 -->
        <p class="muted tiny" style="margin-top:6px">
          只列在办的档案；已关闭的档案不在此列，需要回看请到该生的个案详情。
        </p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="table" :rows="5" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <div class="card" v-if="!loading && !error">
      <div class="card-body">
        <DataTable
          :columns="columns"
          :rows="progress"
          row-key="case_id"
          :page-size="20"
          empty-text="当前没有重点进展。"
        >
          <template #student_name="{ row }">
            <div class="student-cell">
              <span class="student-avatar" aria-hidden="true">{{ row.student_name[0] }}</span>
              <span>
                <strong>{{ row.student_name }}</strong>
                <span class="muted tiny">{{ row.student_no }}</span>
              </span>
            </div>
          </template>
          <template #grade="{ row }">{{ row.grade }}{{ row.class_name }}</template>
          <template #total_level="{ row }">
            <span :class="['pill', levelTone(row.total_level)]">{{ levelLabel(row.total_level) }}</span>
          </template>
          <template #case_status="{ row }">
            <span :class="['pill', statusTone(row.case_status)]">{{ statusLabel(row.case_status) }}</span>
          </template>
          <template #owner_name="{ row }">{{ row.owner_name || '未分配' }}</template>
          <template #next_follow_up_date="{ row }">{{ row.next_follow_up_date || '—' }}</template>
          <template #overdue="{ row }">
            <span v-if="row.overdue" class="pill red">是</span>
            <span v-else class="pill gray">否</span>
          </template>
        </DataTable>
      </div>
    </div>

    <div class="notice warn" style="margin-top:16px">
      管理视图不提供重点题回答、原始答卷、人工复核正文和家庭回访正文。如确需访问，应另行授权并记录理由。
    </div>
  </div>
</template>
