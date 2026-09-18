<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import { useSettings } from '../../composables/useSettings'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import { useRouter } from 'vue-router'
import {
  getAnalyticsByClass,
  getAnalyticsByGrade,
  getAnalyticsOverview,
  getLeaderProgress,
  getMe,
  type AnalyticsOverview,
  type ClassAnalytics,
  type GradeAnalytics,
  type LeaderProgressItem
} from '../../services/api'
import {
  CASE_STATUS_ORDER,
  LEVEL_ORDER,
  levelLabel,
  levelTone,
  statusLabel,
  statusTone
} from '../../services/labels'

const router = useRouter()

// 阈值由系统配置提供：此前一处是常量、另一处是裸字面量
const { settings } = useSettings()
const LOW_COMPLETION = computed(() => settings.value.ui.low_completion_threshold)
const overview = ref<AnalyticsOverview | null>(null)
const grades = ref<GradeAnalytics[]>([])
const classes = ref<ClassAnalytics[]>([])
const progress = ref<LeaderProgressItem[]>([])
const loading = ref(true)
const error = ref('')

const progressColumns: Column[] = [
  { key: 'student_name', label: '学生', sortable: true },
  { key: 'grade', label: '年级班级', sortable: true },
  { key: 'total_level', label: '关注等级', sortable: true, order: LEVEL_ORDER },
  { key: 'case_status', label: '当前阶段', sortable: true, order: CASE_STATUS_ORDER },
  { key: 'owner_name', label: '负责人', sortable: true },
  { key: 'next_follow_up_date', label: '下次跟进', sortable: true },
  { key: 'overdue', label: '是否逾期', sortable: true }
]

const classColumns: Column[] = [
  { key: 'grade', label: '年级', sortable: true },
  { key: 'class_name', label: '班级', sortable: true },
  { key: 'total_targets', label: '应完成', sortable: true, align: 'right' },
  { key: 'completed_targets', label: '已完成', sortable: true, align: 'right' },
  { key: 'completion_rate', label: '完成率', sortable: true, align: 'right' },
  // 关注情况（2026-09-17 加）：这张表此前只有完成情况，回答的全是「谁还没测」。
  // 德育领导要的是「问题集中在哪」——三个班完成率都是 100%，一个有 6 人需关注、
  // 一个没有，旧表里这两行一模一样。
  { key: 'attention_count', label: '需关注', sortable: true, align: 'right' },
  { key: 'attention_rate', label: '关注占比', sortable: true, align: 'right' }
]

/**
 * 比率可能是 `null`（后端认为样本太小，不给）。`null` 不是 0：
 * 0 是「一个都没有」，null 是「这几个人算出来不足为凭」。
 */
function rateText(rate: number | null, small: boolean) {
  if (small || rate === null) return '样本过小'
  return `${rate}%`
}

function rateClass(rate: number | null, small: boolean) {
  if (small || rate === null) return 'muted'
  return rate >= settings.value.ui.dimension_high_threshold ? 'status-bad' : ''
}

/** 三档人数：一般 / 需关注 / 重点关注。 */
const bands = computed(() => {
  const counts = overview.value?.level_counts || {}
  const order = ['GENERAL_RANGE', 'NEEDS_ATTENTION', 'KEY_ATTENTION']
  const tones: Record<string, string> = {
    GENERAL_RANGE: 'tone-ok',
    NEEDS_ATTENTION: 'tone-warn',
    KEY_ATTENTION: 'tone-bad'
  }
  const total = order.reduce((sum, code) => sum + (counts[code] || 0), 0)
  return order.map(code => ({
    code,
    label: levelLabel(code),
    value: counts[code] || 0,
    tone: tones[code],
    percent: total ? Math.round(((counts[code] || 0) / total) * 100) : 0
  }))
})

const assessedCount = computed(() => overview.value?.assessed_count || 0)

/** 低完成率的年级/班级需要管理者注意，否则整页只是一堆百分比。 */
function needsAttention(rate: number) {
  return rate < LOW_COMPLETION.value
}

/** 管理提醒来自真实聚合，而不是写死文案。 */
const managementAlerts = computed(() => {
  const overdue = progress.value.filter(p => p.overdue).length
  const unassigned = progress.value.filter(p => !p.owner_id).length
  const behind = grades.value.filter(g => needsAttention(g.completion_rate))
  return [
    { label: '逾期未跟进', value: overdue, tone: overdue > 0 ? 'bad' : 'ok' },
    { label: '未分配负责人', value: unassigned, tone: unassigned > 0 ? 'bad' : 'ok' },
    { label: `完成率低于 ${LOW_COMPLETION.value}% 的年级`, value: behind.length, tone: behind.length ? 'bad' : 'ok' },
    // 这个数就是「重点进展」那一页的行数，所以它**只数在办的档案**（2026-09-17 起
    // 后端不再下发 CLOSED）。标签不能写「重点关注档案」：「重点关注」是本产品里
    // `KEY_ATTENTION` 那个等级的**名字**（§3 那张表），而这里数的是任何等级的在办
    // 档案。一个字面的等级名配一个不是它的数，读的人只会得出「这所学校有 N 个
    // 重点关注学生」——那是另一个数（见上面那三档）。
    { label: '在办关注档案', value: progress.value.length, tone: 'plain' }
  ]
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
    const [overviewData, gradeData, classData, progressData] = await Promise.all([
      getAnalyticsOverview(),
      getAnalyticsByGrade(),
      getAnalyticsByClass(),
      getLeaderProgress()
    ])
    overview.value = overviewData
    grades.value = gradeData
    classes.value = classData
    progress.value = progressData
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
        <div class="eyebrow">学校管理视角</div>
        <h1>德育工作总览</h1>
        <p class="page-desc">关注学校整体趋势、处置进度与逾期情况，不默认展示答卷和访谈正文。</p>
      </div>
      <div class="actions">
        <button class="btn" @click="router.push('/leader/progress')">查看重点进展</button>
        <button class="btn primary" @click="router.push('/leader/analytics')">查看统计</button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="metrics" :columns="4" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <template v-if="!loading && !error">
      <!-- 可点的那三张带 `role="button"` 与 Enter/Space（同心理老师工作台那一处，
           见那边的注释）。第四张「计划复测」没有去处，所以只是 `<article>`：
           它的 `cursor: pointer` 来自 `.metric` 的公共样式，是**假**的。 -->
      <div class="grid metrics">
        <article class="metric" data-tone="green" role="button" tabindex="0" @click="router.push('/leader/analytics')" @keydown.enter.prevent="router.push('/leader/analytics')" @keydown.space.prevent="router.push('/leader/analytics')">
          <div class="metric-label">测评完成率</div>
          <div class="metric-value">{{ overview?.completion_rate ?? 0 }}%</div>
          <div class="metric-foot">{{ overview?.completed_targets ?? 0 }} / {{ overview?.total_targets ?? 0 }} 人</div>
        </article>
        <article class="metric" data-tone="red" role="button" tabindex="0" @click="router.push('/leader/progress')" @keydown.enter.prevent="router.push('/leader/progress')" @keydown.space.prevent="router.push('/leader/progress')">
          <div class="metric-label">需关注摘要</div>
          <div class="metric-value">{{ overview?.attention_count ?? 0 }}</div>
          <div class="metric-foot">
            占已测评 {{ assessedCount }} 人
            {{ overview?.attention_rate === null ? '· 样本过小，不给占比' : `的 ${overview?.attention_rate}%` }}
          </div>
        </article>
        <!-- 标签与下面那张检查表里的同一项**同名**：同一个数在两处出现，两个名字
             会让人以为是两个东西。此前它写「重点档案」，而「重点」是本产品里
             `KEY_ATTENTION` 等级的名字——上面那三档才是真的按等级数人。
             这个数是在办的**档案**数（一名学生一条），与等级无关。 -->
        <article class="metric" data-tone="amber" role="button" tabindex="0" @click="router.push('/leader/progress')" @keydown.enter.prevent="router.push('/leader/progress')" @keydown.space.prevent="router.push('/leader/progress')">
          <div class="metric-label">在办关注档案</div>
          <div class="metric-value">{{ progress.length }}</div>
          <div class="metric-foot">其中逾期 {{ progress.filter(p => p.overdue).length }} 项</div>
        </article>
        <article class="metric" data-tone="teal">
          <div class="metric-label">计划复测</div>
          <div class="metric-value">{{ overview?.planned_retests ?? 0 }}</div>
          <div class="metric-foot">待完成的复测计划</div>
        </article>
      </div>

      <article class="card pad" style="margin-top:17px">
        <h2>关注等级分布</h2>
        <p class="muted tiny" style="margin:6px 0 0">
          每名学生只按最近一场测评计入一档。分段阈值随量表规则版本走，不在这一页判定。
        </p>
        <div class="band-bar" style="margin-top:16px">
          <i
            v-for="band in bands"
            :key="band.code"
            :class="band.tone"
            :style="{ width: `${band.percent}%` }"
            :title="`${band.label} ${band.value} 人`"
          ></i>
        </div>
        <div class="band-legend">
          <div v-for="band in bands" :key="band.code" class="band-item">
            <span :class="['band-dot', band.tone]"></span>
            <span class="band-name">{{ band.label }}</span>
            <b>{{ band.value }}</b>
            <span class="muted tiny">{{ band.percent }}%</span>
          </div>
        </div>
        <div v-if="!assessedCount" class="empty" style="margin-top:12px">尚无已提交的测评</div>
      </article>

      <div class="grid two" style="margin-top:17px">
        <article class="card pad">
          <h2>年级完成与关注情况</h2>
          <div class="bar-list" style="margin-top:19px">
            <div v-for="g in grades" :key="g.grade_id" class="bar-row">
              <span>{{ g.grade }}</span>
              <div class="bar">
                <i :class="needsAttention(g.completion_rate) ? 'high' : 'medium'" :style="{ width: `${g.completion_rate}%` }"></i>
              </div>
              <b>{{ g.completion_rate }}%</b>
              <!-- 第二行：关注占比。完成率相同的两个年级，问题可能差十倍。 -->
              <span class="bar-sub" :class="rateClass(g.attention_rate, g.cohort_too_small)">
                关注 {{ rateText(g.attention_rate, g.cohort_too_small) }}
              </span>
            </div>
            <div v-if="!grades.length" class="empty">暂无年级数据</div>
          </div>
        </article>

        <article class="card pad">
          <h2>管理提醒</h2>
          <div class="checklist" style="margin-top:15px">
            <div v-for="alert in managementAlerts" :key="alert.label" class="check-row">
              <span>{{ alert.label }}</span>
              <strong :class="alert.tone === 'bad' ? 'status-bad' : alert.tone === 'ok' ? 'status-ok' : ''">
                {{ alert.value }} 项
              </strong>
            </div>
          </div>
        </article>
      </div>

      <article class="card" style="margin-top:17px">
        <div class="card-head">
          <h2>重点进展摘要</h2>
          <span class="muted tiny">{{ progress.length }} 人</span>
        </div>
        <div class="card-body">
          <!-- 只读摘要：德育领导不进入学生敏感档案，故不提供行内操作 -->
          <DataTable
            :columns="progressColumns"
            :rows="progress"
            row-key="case_id"
            :page-size="10"
            empty-text="当前没有重点进展。"
          >
            <template #student_name="{ row }">{{ row.student_name }} · {{ row.student_no }}</template>
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
      </article>

      <article class="card" style="margin-top:17px">
        <div class="card-head">
          <h2>班级下钻</h2>
          <span class="muted tiny">{{ classes.length }} 个班级</span>
        </div>
        <div class="card-body">
          <DataTable
            :columns="classColumns"
            :rows="classes"
            row-key="class_id"
            :page-size="10"
            empty-text="暂无班级数据"
          >
            <template #completion_rate="{ row }">
              <span :class="needsAttention(row.completion_rate) ? 'status-bad' : 'status-ok'">
                {{ row.completion_rate }}%
              </span>
            </template>
            <template #attention_rate="{ row }">
              <span :class="rateClass(row.attention_rate, row.cohort_too_small)">
                {{ rateText(row.attention_rate, row.cohort_too_small) }}
              </span>
            </template>
          </DataTable>
        </div>
      </article>

      <div class="notice warn" style="margin-top:16px">
        管理视图不提供重点题回答、原始答卷、人工复核正文和家庭回访正文。如确需访问，应另行授权并记录理由。
      </div>
    </template>
  </div>
</template>
