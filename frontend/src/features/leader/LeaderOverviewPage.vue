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
import { PROGRESS_FILTERS, type ProgressFilter } from './progressFilters'

const router = useRouter()

/**
 * 下钻到「重点进展」并把筛选带上（§5.14.4 第 1 / 3 条）。
 *
 * 每一个能下钻的项都走这一个函数，`ProgressPage.vue` 按 `query.filter` 用**同一组
 * 谓词**（`progressFilters.ts`）筛同一份名单——所以「卡片上那个数」与「点进去那几行」
 * 构造上不可能漂（§11）。不带筛选的项**不调用它**，也**不长成可点的样子**。
 */
function drillToProgress(filter?: ProgressFilter) {
  router.push({ path: '/leader/progress', query: filter ? { filter } : {} })
}

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

// 两张待办卡上的数**从那份名单现算**，谓词与「重点进展」页筛的**同一个**
// （`progressFilters.ts`）——卡片说几条、点进去几行，构造上不可能漂（§11）。
// 逾期那一个此前是就地写一遍 `p.overdue`，与页面上别处的写法各写各的。
const overdueCount = computed(() => progress.value.filter(PROGRESS_FILTERS.overdue).length)
const unassignedCount = computed(() => progress.value.filter(PROGRESS_FILTERS.unassigned).length)

/**
 * 管理提醒来自真实聚合，而不是写死文案。
 *
 * ★ 2026-09-27（§5.14.4 第 3 条）：每一项带上**它的去处**，让「这一行说有几条」
 * 与「点进去看到几行」是同一件事。三档取值，各自对应一种渲染（模板里按 `filter`
 * 在不在分岔）：
 *
 * | `filter` | 含义 | 渲染成 |
 * |---|---|---|
 * | 一个筛选键 | 有去处、并且筛到对应那几条 | `<button class="check-row">` |
 * | `null` | 有去处，但「对应结果」就是整份名单（没有更细的筛法） | `<button class="check-row">` |
 * | **不写这一项** | **没有去处** | 普通 `<div>`，没有手型也没有 hover 抬升 |
 *
 * 第三档只有一项（年级完成率），而**它是刻意的**：`GradesPage.vue` 没有按完成率
 * 筛选的能力，所以给它一个能点的样子就是「计划复测」那张卡原来的毛病再犯一次
 * （`.metric[role="button"]` 那条注释点的就是那一次）。它的答案就在这一行里——
 * 是哪几个年级由 `detail` 直接列出来，读者不必点进任何地方。
 */
type ManagementAlert = {
  label: string
  value: number
  tone: string
  /** 下钻的筛选键；`null` = 有去处但不筛；**不写** = 没有去处（不可交互）。 */
  filter?: ProgressFilter | null
  /** 只在没有去处的那一项上出现：把「是哪几个」直接写在这一行。 */
  detail?: string
}

const managementAlerts = computed<ManagementAlert[]>(() => {
  const behind = grades.value.filter(g => needsAttention(g.completion_rate))
  return [
    {
      label: '逾期未跟进',
      value: overdueCount.value,
      tone: overdueCount.value > 0 ? 'bad' : 'ok',
      filter: 'overdue'
    },
    {
      label: '未分配负责人',
      value: unassignedCount.value,
      tone: unassignedCount.value > 0 ? 'bad' : 'ok',
      filter: 'unassigned'
    },
    {
      label: `完成率低于 ${LOW_COMPLETION.value}% 的年级`,
      value: behind.length,
      tone: behind.length ? 'bad' : 'ok',
      // 没有 `filter`：`GradesPage.vue` 没有完成率筛选，做一个能点的样子就是
      // 假可点击。是哪几个年级直接写在后面（同一次 computed 算出来的，不是另查）。
      detail: behind.map(g => g.grade).join('、')
    },
    // 这个数就是「重点进展」那一页的行数，所以它**只数在办的档案**（2026-09-17 起
    // 后端不再下发 CLOSED）。标签不能写「重点关注档案」：「重点关注」是本产品里
    // `KEY_ATTENTION` 那个等级的**名字**（§3 那张表），而这里数的是任何等级的在办
    // 档案。一个字面的等级名配一个不是它的数，读的人只会得出「这所学校有 N 个
    // 重点关注学生」——那是另一个数（见上面那三档）。
    //
    // `filter: null`：它的去处是整份名单，没有更细的筛法。
    { label: '在办关注档案', value: progress.value.length, tone: 'plain', filter: null }
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
      <!-- 次序（§5.14.4 第 2 条）：**要动手的排在前面，常规完成率排在最后**。
           异常（需关注 / 在办档案 / 计划复测）→ 常规（完成率）。此前完成率打头，
           而它是四个数里唯一一个「不需要额外做什么」的数。
           四张都带 `role="button"` 与 Enter/Space（同心理老师工作台那一处）。
           第四张曾经是**假可点击**——它长着 `.metric[role="button"]` 的手型光标与
           hover 抬升，却没有去处（`styles.css` 的注释点的就是它）；现在它下钻到
           已筛选的复测名单，不再是装饰。 -->
      <div class="grid metrics">
        <article class="metric" data-tone="red" role="button" tabindex="0" @click="drillToProgress()" @keydown.enter.prevent="drillToProgress()" @keydown.space.prevent="drillToProgress()">
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
        <article class="metric" data-tone="amber" role="button" tabindex="0" @click="drillToProgress()" @keydown.enter.prevent="drillToProgress()" @keydown.space.prevent="drillToProgress()">
          <div class="metric-label">在办关注档案</div>
          <div class="metric-value">{{ progress.length }}</div>
          <div class="metric-foot">其中逾期 {{ overdueCount }} 项</div>
        </article>
        <!-- 这一个数**按学生去重**（`overview.planned_retests`）：一名学生可以挂多份
             复测计划，而「重点进展」一行一名学生——数计划行时卡片说 6、点进去 4 行。
             口径与那个列表逐字对齐，由 `test_analytics_api.py` 里的一条用例钉住。 -->
        <article class="metric" data-tone="teal" role="button" tabindex="0" @click="drillToProgress('retest')" @keydown.enter.prevent="drillToProgress('retest')" @keydown.space.prevent="drillToProgress('retest')">
          <div class="metric-label">计划复测</div>
          <div class="metric-value">{{ overview?.planned_retests ?? 0 }}</div>
          <div class="metric-foot">在办档案名下待完成的复测计划（按学生去重）</div>
        </article>
        <!-- 这一格的分母是**任务目标**（`total_targets`，含请假 / 免测 / 已排除），
             而心理老师侧几处写「实际应测」的地方分母是 `eligible_count`（减去那三类）。
             两个分母都合法、各有各的用途，但**它们不相等**，所以本行把分母写出来
             （§5.20.3：不同分母必须在界面上能分辨；§9：口径要写进界面）。
             `completion_rate` 服务端是 `round(completed / total * 100) if total else 0`
             ——**是 `number`、永不为 `null`**，所以 `?? 0` 在这里没有抹平任何既有的
             null 语义（§5.20.4 禁的是抹平，不是禁止默认值），值行因此保持原样。
             按 §5.20.7 第 10 条，既有统计公式不改：两个分母的差异只**说明**、不统一。 -->
        <article class="metric" data-tone="green" role="button" tabindex="0" @click="router.push('/leader/analytics')" @keydown.enter.prevent="router.push('/leader/analytics')" @keydown.space.prevent="router.push('/leader/analytics')">
          <div class="metric-label">测评完成率</div>
          <div class="metric-value">{{ overview?.completion_rate ?? 0 }}%</div>
          <div class="metric-foot">{{ overview?.completed_targets ?? 0 }} / {{ overview?.total_targets ?? 0 }} 人（任务目标，含请假 / 免测 / 已排除）</div>
        </article>
      </div>

      <!-- 次序（§5.14.4 第 2 条）：**要动手的排在前面**。左侧「管理提醒」是这一页
           唯一一件**告诉读者接下来做什么**的事，右侧的年级条形图是背景；而这一整块
           又排在「关注等级分布」之前——那一块回答的是「整体什么水平」，属于事后
           描述。此前顺序恰好相反（分布 → 年级 → 提醒），读者要滚过两块图才看见
           待办。 -->
      <div class="grid two" style="margin-top:17px">
        <!-- `tier-primary`：上面那两段注释已经把判据写全了——「左侧『管理提醒』
             是这一页唯一一件**告诉读者接下来做什么**的事，右侧的年级条形图是背景」。
             整个页面里只有它回答「我现在该做什么」，所以强调条给它。
             与它并排的「年级完成与关注情况」留在默认的二级层（背景/描述性信息）。 -->
        <article class="card pad tier-primary">
          <h2>管理提醒</h2>
          <!-- 三档渲染（见 script 里 `ManagementAlert` 那段注释）：有去处的
               `<button role="button">`，没有去处的普通 `<div>`。两者外观相同，
               差别只在光标与悬停——判据与 `.metric` 那一处一致。 -->
          <div class="checklist" style="margin-top:15px">
            <template v-for="alert in managementAlerts" :key="alert.label">
              <button
                v-if="alert.filter !== undefined"
                type="button"
                class="check-row"
                role="button"
                @click="drillToProgress(alert.filter ?? undefined)"
              >
                <span>{{ alert.label }}</span>
                <strong :class="alert.tone === 'bad' ? 'status-bad' : alert.tone === 'ok' ? 'status-ok' : ''">
                  {{ alert.value }} 项
                </strong>
              </button>
              <!-- 没有去处的那一项（完成率低于阈值的年级）：`GradesPage.vue` 没有
                   按完成率筛选的能力，所以点不动。是哪几个年级直接写在标签后面
                   ——它的答案就在这一行里，不必点进任何地方。 -->
              <div v-else class="check-row">
                <span>
                  {{ alert.label }}
                  <span v-if="alert.detail" class="muted tiny">· {{ alert.detail }}</span>
                </span>
                <strong :class="alert.tone === 'bad' ? 'status-bad' : alert.tone === 'ok' ? 'status-ok' : ''">
                  {{ alert.value }} 项
                </strong>
              </div>
            </template>
          </div>
        </article>

        <article class="card pad">
          <h2>年级完成与关注情况</h2>
          <!-- 两条口径，都写在这一块的数据旁边（§5.14.4 第 4 / 6 条）：
               ①「样本过小」是下面那个 `rateText` 的取值，四字之外没有任何解释，
               而它说的是**比率不给、计数照给**——不说清会被读成「没有数据」；
               ②不提供环比：这一页按全部有效测评累计，系统里没有「周期」这一层，
               编一个「比上学期」出来比不给更糟。

               ★ 刻意**不写那个阈值数字**（「不足 5 人」）。`MIN_COHORT_FOR_AGGREGATE`
               住在 `analytics_service.py` 里、不在任何配置表里，也不出现在这一页的
               响应里——在模板里写一个 5 就是这本文件反复记着的那个形状（同一件事的
               第二个定义），而它漂了不会有任何东西看得见。本项目已有的做法是
               **阈值一律从服务端来**（同页的 `LOW_COMPLETION` 就读 `settings.ui`），
               所以在那之前这里只说清语义、不说数字。 -->
          <p class="muted tiny" style="margin:6px 0 0">
            「样本过小」= 该群体已测评人数太少，比率不足为凭（人数照给，比率不给）。
            本页按全部有效测评累计（每名学生取最近一场已计算的结果），
            系统里没有「周期」这一层，因此不提供环比 / 同比。
          </p>
          <div class="bar-list" style="margin-top:16px">
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

      <article class="card" style="margin-top:17px">
        <div class="card-head">
          <h2>重点进展摘要</h2>
          <span class="muted tiny">{{ progress.length }} 人</span>
        </div>
        <div class="card-body">
          <!-- 姓名是**遮蔽名**（姓 + 「同学」），服务端现算下发，**没有学号**
               （§5.14.4 第 7 条）。此前这一格渲染的是 `row.student_name` ·
               `row.student_no`，而 `student_no` 已经不在响应里了——写一个拿不到的
               字段会让整格显示成「林同学 · undefined」。德育领导的
               `STUDENT_PSYCH_DETAIL` 是 `SUMMARY`、`ORG_ACCOUNT` 是
               `READ_SUMMARY`，学号与真名都不在他的授权范围内（§4）；这一列
               只是**不假装有一个拿不到的字段**，真正的守卫在服务端。 -->
          <p class="muted tiny" style="margin:0 0 10px">
            学生姓名按「姓 + 同学」遮蔽显示。需要个体身份的场景请走心理老师。
          </p>
          <!-- 只读摘要：德育领导不进入学生敏感档案，故不提供行内操作 -->
          <DataTable
            :columns="progressColumns"
            :rows="progress"
            row-key="case_id"
            :page-size="10"
            empty-text="当前没有重点进展。"
          >
            <template #student_name="{ row }">{{ row.student_name }}</template>
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
          <!-- 「关注占比」那一列可能是「样本过小」——与年级块同一个取值、同一个
               理由（§5.14.4 第 6 条）。同一句话在两处都要有：读者会单独打开
               这一段，而只在前一处解释等于只对看完整页的人解释。
               同样不写那个阈值数字，理由见上面年级块那一段注释。 -->
          <p class="muted tiny" style="margin:0 0 10px">
            「样本过小」= 该班已测评人数太少，关注占比不足为凭（需关注人数照给）。
          </p>
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
