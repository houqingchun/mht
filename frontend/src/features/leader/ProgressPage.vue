<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { getMe, getLeaderProgress, type LeaderProgressItem } from '../../services/api'
import { useRoute, useRouter } from 'vue-router'
import {
  applyProgressFilter,
  parseProgressFilter,
  PROGRESS_FILTER_LABELS
} from './progressFilters'
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

// ★ 2026-09-27（§5.14.4 第 3 条）：这一页是总览那几张卡与「管理提醒」的下钻落点，
// 筛选从 URL 来（`/leader/progress?filter=overdue`）。做法是**客户端筛**，与卡片
// 现算那一个数用的是**同一个谓词**（`progressFilters.ts`），所以「卡片说几条」
// 与「点进来看到几行」构造上不可能漂（§11）。
//
// 筛选**不进服务端**：这一页本来就是一次取回全部在办档案（无分页参数），
// 再加一个服务端筛选参数就会有两个「哪些行该出现」的定义。
//
// `parseProgressFilter` 认不出的 query（打错的链接、用户手改的 URL）一律当**不筛**
// ——回退到某一个默认筛选会让「这几行就是全部逾期的人」变成一句假话。
const route = useRoute()
const activeFilter = computed(() => parseProgressFilter(route.query.filter))
const visible = computed(() => applyProgressFilter(progress.value, activeFilter.value))

async function clearFilter() {
  // `replace` 而不是 `push`：清除筛选不是一次「前进」，按返回键应该回到进来之前
  // 那一页，而不是在「筛过 / 没筛」之间来回弹。
  await router.replace({ path: '/leader/progress' })
}

// 空态是一句关于数据的话（§14），而「筛过之后一条都没有」与「本来就一条都没有」
// 是两句不同的话：前者要说清是筛出来的空、并给出清除筛选的出路，否则读者会以为
// 全校一份在办档案都没有。这一句与上面那条筛选说明**说的是同一件事**，所以两处
// 都从同一个 `activeFilter` 派生。
const emptyText = computed(() => {
  const filter = activeFilter.value
  if (!filter) return '当前没有重点进展。'
  return `没有「${PROGRESS_FILTER_LABELS[filter]}」的在办档案（全部在办档案共 ${progress.value.length} 条）。`
})


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
        <!-- 第二句口径（§5.14.4 第 7 条）：姓名是遮蔽名。不说的话，读者会以为
             系统里的学生就叫「林同学」，或者以为这一列坏了——而它是刻意的。 -->
        <p class="muted tiny" style="margin-top:4px">
          学生姓名按「姓 + 同学」遮蔽显示；班级、当前阶段、负责人与是否逾期用于安排工作。需要个体身份的场景请走心理老师。
        </p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="table" :rows="5" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <!-- `tier-primary`：这一页的主工作区是这份「重点进展」名单本身（它承载
         筛选、下钻说明与逐行进展），上面那句遮蔽口径留在页头，不是卡片。 -->
    <div class="card tier-primary" v-if="!loading && !error">
      <div class="card-body">
        <!-- 从总览那几张卡下钻过来时，这一条说明**当前筛的是什么**。少了它，
             读者看到的是一份被筛过的名单，而屏幕上没有任何东西说得出这件事
             （§9：口径要写进界面）。没有筛选时整条不出现。 -->
        <div class="toolbar" v-if="activeFilter" style="margin-bottom:12px">
          <span class="muted tiny">
            已按「{{ PROGRESS_FILTER_LABELS[activeFilter] }}」筛选，共 {{ visible.length }} 条（在办档案
            {{ progress.length }} 条）。
          </span>
          <button class="btn small" @click="clearFilter">清除筛选</button>
        </div>
        <DataTable
          :columns="columns"
          :rows="visible"
          row-key="case_id"
          :page-size="20"
          :empty-text="emptyText"
        >
          <!-- 第一列只有遮蔽名（姓 + 「同学」），**没有学号**：两者都由服务端决定
               （§5.14.4 第 7 条），前端不在这里补一个拿不到的字段——「前端隐藏
               不是安全措施」（§4）说的是别把门装在这里，而不假装有一个不存在
               的字段是同一件事的另一面。 -->
          <template #student_name="{ row }">
            <div class="student-cell">
              <span class="student-avatar" aria-hidden="true">{{ row.student_name[0] }}</span>
              <span>
                <strong>{{ row.student_name }}</strong>
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
