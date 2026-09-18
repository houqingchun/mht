<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import { useSettings } from '../../composables/useSettings'
import ErrorState from '../../components/ErrorState.vue'
import { showToast } from '../../services/toast'
import { downloadCsv } from '../../services/csv'
import { dimensionLabel, levelLabel } from '../../services/labels'
import DataTable, { type Column } from '../../components/DataTable.vue'
import {
  getMe,
  getAnalyticsOverview,
  getAnalyticsByGrade,
  getAnalyticsByClass,
  getDimensionDistribution,
  type AnalyticsOverview,
  type GradeAnalytics,
  type ClassAnalytics,
  type DimensionDistributionItem
} from '../../services/api'

const router = useRouter()

// 阈值由系统配置提供：此前一处是常量、另一处是裸字面量
const { settings } = useSettings()
const LOW_COMPLETION = computed(() => settings.value.ui.low_completion_threshold)
const overview = ref<AnalyticsOverview | null>(null)
const grades = ref<GradeAnalytics[]>([])
const classes = ref<ClassAnalytics[]>([])
const loading = ref(true)
const error = ref('')

const classColumns: Column[] = [
  { key: 'grade', label: '年级', sortable: true },
  { key: 'class_name', label: '班级', sortable: true },
  { key: 'total_targets', label: '应完成', sortable: true, align: 'right' },
  { key: 'completion_rate', label: '完成率', sortable: true, align: 'right' },
  { key: 'assessed_count', label: '已测评', sortable: true, align: 'right' },
  { key: 'attention_count', label: '需关注', sortable: true, align: 'right' },
  { key: 'attention_rate', label: '关注占比', sortable: true, align: 'right' }
]

/** 主要维度分布 —— 真实聚合，见 GET /analytics/dimensions。 */
const dimensions = ref<DimensionDistributionItem[]>([])

/**
 * 这些聚合数字的口径。
 *
 * 后端按数据范围过滤：心理老师（SCOPED）的比率描述他自己的授权范围，德育领导
 * （SCHOOL）的描述全校。两者共用本组件，所以口径必须写出来——一个范围数字
 * 顶着「全校口径」的标签，比不给数字更糟。
 *
 * 2026-09-17：口径不只写在角落那一格了，标题也跟着走。此前心理老师进来看到的是
 * 「学校心理筛查统计」加一个「我的授权范围口径」的小字——标题说学校，脚注说范围，
 * 而标题是**唯一**会被截图、被转述、被记进会议纪要的那一句。
 */
const scopeLabel = ref('')
const isSchoolWide = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    // 心理老师与德育领导均可查看聚合统计
    if (me.role_code !== 'counselor' && me.role_code !== 'leader') {
      await router.push('/login')
      return
    }
    isSchoolWide.value = me.role_code === 'leader'
    scopeLabel.value = isSchoolWide.value ? '全校口径' : '我的授权范围口径'
    const [overviewData, gradeData, classData] = await Promise.all([
      getAnalyticsOverview(),
      getAnalyticsByGrade(),
      getAnalyticsByClass()
    ])
    overview.value = overviewData
    grades.value = gradeData
    classes.value = classData
    // Dimensions are supplementary — a failure must not blank the page.
    try {
      dimensions.value = await getDimensionDistribution()
    } catch {
      dimensions.value = []
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/**
 * 三档人数。**这是这一页最该先看的东西**：一个「需关注 42 人」的总数说不出
 * 学校该做什么，而「一般 380 / 需关注 38 / 重点关注 4」直接对应三种处置力度。
 */
const bands = computed(() => {
  const counts = overview.value?.level_counts || {}
  const order = ['GENERAL_RANGE', 'NEEDS_ATTENTION', 'KEY_ATTENTION']
  const tones: Record<string, string> = {
    GENERAL_RANGE: 'tone-ok',
    NEEDS_ATTENTION: 'tone-warn',
    KEY_ATTENTION: 'tone-bad'
  }
  const total = order.reduce((sum, code) => sum + (counts[code] || 0), 0)
  return order.map(code => {
    const value = counts[code] || 0
    return {
      code,
      label: levelLabel(code),
      value,
      tone: tones[code],
      // 分母是**已测评人数**。一个人都没有时不给宽度，免得整条画满。
      percent: total ? Math.round((value / total) * 100) : 0
    }
  })
})

const assessedCount = computed(() => overview.value?.assessed_count || 0)

/**
 * 比率可能来自后端、也可能是 `null`（样本过小）。`null` **不是 0**：
 * 0 是「一个都没有」，null 是「这几个人算出来不足为凭」，两者在界面上必须长得不一样。
 */
function rateText(rate: number | null, small: boolean) {
  if (small || rate === null) return '样本过小'
  return `${rate}%`
}

function rateClass(rate: number | null, small: boolean) {
  if (small || rate === null) return 'muted'
  return rate >= settings.value.ui.dimension_high_threshold ? 'status-bad' : ''
}

function exportStats() {
  const rows: string[][] = [
    ['指标', '数值', '口径'],
    ['应完成', String(overview.value?.total_targets ?? 0), scopeLabel.value],
    ['已完成', String(overview.value?.completed_targets ?? 0), scopeLabel.value],
    ['完成率', `${overview.value?.completion_rate ?? 0}%`, scopeLabel.value],
    ['已测评人数', String(assessedCount.value), scopeLabel.value],
    ['需关注人数', String(overview.value?.attention_count ?? 0), scopeLabel.value],
    [
      '关注占比',
      overview.value?.attention_rate === null ? '样本过小' : `${overview.value?.attention_rate}%`,
      '已测评人数为分母'
    ],
    ['其中重点关注', String(overview.value?.key_attention_count ?? 0), scopeLabel.value],
    ['计划复测', String(overview.value?.planned_retests ?? 0), scopeLabel.value]
  ]
  for (const band of bands.value) {
    rows.push([band.label, String(band.value), scopeLabel.value])
  }
  downloadCsv('school-aggregate-statistics.csv', rows)
  showToast('success', '聚合统计已导出')
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">聚合数据</div>
        <h1>{{ isSchoolWide ? '学校筛查统计' : '我的范围筛查统计' }}</h1>
        <p class="page-desc">
          支持年级、班级下钻；默认不展示学生身份和高敏感正文。
          下面的数字是<b>{{ scopeLabel }}</b>，换个角色登录，同一页的口径会变。
        </p>
      </div>
      <div class="actions">
        <button class="btn" @click="exportStats">导出聚合统计</button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="metrics" :rows="4" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <!-- `&& !error`：失败时 overview 是 null，四个指标卡会全部渲染成 0，
         比空态更糟——0% 完成率看起来是一个结论。 -->
    <template v-if="!loading && !error">
      <div class="grid metrics">
        <article class="metric" data-tone="green">
          <div class="metric-label">已完成 / 应完成</div>
          <div class="metric-value">{{ overview?.completed_targets ?? 0 }}</div>
          <div class="metric-foot">共 {{ overview?.total_targets ?? 0 }} 人 · 完成率 {{ overview?.completion_rate ?? 0 }}%</div>
        </article>
        <article class="metric" data-tone="red">
          <div class="metric-label">需关注摘要</div>
          <div class="metric-value">{{ overview?.attention_count ?? 0 }}</div>
          <!-- 分母写「已测评人数」而不是「占比」了事：这个比率的分母是**人**，
               不是测评次数。复测过的学生只算一次。 -->
          <div class="metric-foot">
            占已测评 {{ assessedCount }} 人
            {{ overview?.attention_rate === null ? '· 样本过小，不给占比' : `的 ${overview?.attention_rate}%` }}
          </div>
        </article>
        <article class="metric" data-tone="amber">
          <div class="metric-label">其中重点关注</div>
          <div class="metric-value">{{ overview?.key_attention_count ?? 0 }}</div>
          <div class="metric-foot">需要优先安排人工复核</div>
        </article>
        <article class="metric" data-tone="teal">
          <div class="metric-label">计划复测</div>
          <div class="metric-value">{{ overview?.planned_retests ?? 0 }}</div>
          <div class="metric-foot">待确认复测项</div>
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
                <i
                  :class="g.completion_rate < LOW_COMPLETION ? 'high' : 'medium'"
                  :style="{ width: `${g.completion_rate}%` }"
                ></i>
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
          <h2>主要维度分布</h2>
          <div v-if="dimensions.length" class="bar-list" style="margin-top:19px">
            <div v-for="d in dimensions" :key="d.dimension_code" class="bar-row">
              <span>{{ dimensionLabel(d.dimension_code) }}</span>
              <div class="bar">
                <i
                  :class="d.high_rate >= settings.ui.dimension_high_threshold ? 'high' : d.high_rate > 0 ? 'medium' : ''"
                  :style="{ width: `${Math.max(d.high_rate, 1)}%` }"
                ></i>
              </div>
              <b>{{ d.high_rate }}%</b>
            </div>
          </div>
          <div v-else class="muted tiny" style="margin-top:19px">尚无已提交的测评数据。</div>
        </article>
      </div>

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
              <span :class="row.completion_rate < settings.ui.low_completion_threshold ? 'status-bad' : 'status-ok'">{{ row.completion_rate }}%</span>
            </template>
            <template #attention_rate="{ row }">
              <span :class="rateClass(row.attention_rate, row.cohort_too_small)">
                {{ rateText(row.attention_rate, row.cohort_too_small) }}
              </span>
            </template>
          </DataTable>
        </div>
        <div class="card-body">
          <p class="muted tiny">
            「已测评」是最近一场已交卷的人数，「关注占比」以它为分母，而不是以测评次数为分母——
            复测过的学生只算一次。已测评的人太少时不给占比：几个人的比率等于点名。
          </p>
        </div>
      </article>
    </template>
  </div>
</template>
