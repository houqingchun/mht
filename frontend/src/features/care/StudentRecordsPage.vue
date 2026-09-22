<script setup lang="ts">
/**
 * 学生测评记录 —— 一名学生考过几次、每次多少分、当前相对于同龄人处在哪。
 *
 * **它存在的理由只有一个：没有关注档案的学生也要看得到自己的测评事实。**
 * 全库唯一的开档触发点是重点题（第 85 / 97 题）答「是」（`maybe_raise_risk_events`
 * 的 docstring 逐字写着「Only 重点题命中写行」），关注等级本身从不产生档案——
 * 所以一个被评成「需要关注」的学生照样可能没档案，而在此之前
 * `GET /care-cases/{student_id}` 是唯一能读到「他考过几次」的接口，它对这些人回 404。
 *
 * 于是这一页与档案页是**并列的两条路**，读的是同一个学生的同一批事实：
 * 这里回答「测评事实」（历次场次、维度分、班级对照），档案页回答「这份档案走到哪一步」
 * （复核、跟进、家庭回访、复测）。后端两处共用
 * `care_service.student_assessment_records` 一处装配，所以同一个字段在两个屏幕上
 * 不可能各说各话（`test_student_records_api.py` 的第一条用例钉住这一点）。
 *
 * 措辞上有一条边界：本页只说「关注等级」「一般观察」这类筛查口径，
 * 不出现任何疾病名，也不写「因为他只是一般观察所以没建档」——那句话把两件不相干的事
 * 说成了一个因果（等级从不触发建档，命中的是重点题）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DataTable, { type Column } from '../../components/DataTable.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import Modal from '../../components/Modal.vue'
import TrendChart from '../../components/TrendChart.vue'
import ClassComparisonPanel from '../../components/ClassComparisonPanel.vue'
import {
  LEVEL_ORDER,
  SOURCE_ORDER,
  ageLabel,
  calculationStatusLabel,
  calculationStatusTone,
  genderLabel,
  levelLabel,
  levelTone,
  sourceLabel
} from '../../services/labels'
import {
  getClassComparison,
  getStudentAssessmentRecords,
  getSessionFullAnswers,
  type AssessmentHistoryEntry,
  type ClassComparison,
  type StudentAssessmentRecords,
  type FullAnswerItem
} from '../../services/api'

const route = useRoute()
const router = useRouter()

const records = ref<StudentAssessmentRecords | null>(null)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  // 先清空再取（§14 第二例）。这一页只在挂载时读一次，但「取失败」这一支照样够得着：
  // 一次网络抖动之后，红条会与**上一次**那名学生的记录同屏——页头写着 B、表里是 A 的
  // 历次场次，而屏幕上没有任何东西看得出来。
  records.value = null
  try {
    records.value = await getStudentAssessmentRecords(Number(route.params.studentId))
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

/**
 * 班级对照。与主记录**分开取**（照档案页的形状）：它多算两条同班/同年级的聚合，
 * 晚到一会儿不该拖住整页，而且它每次读取都会单独写一条审计——并进主接口的话，
 * 「看了一次对照」与「打开了这一页」在轨迹里就分不出来了。
 */
const comparison = ref<ClassComparison | null>(null)
const comparisonFailed = ref(false)

async function loadComparison() {
  comparisonFailed.value = false
  // 同 `load()`：拿不到就留空，不留下上一次那一份——对照表里是**别人的**班级，
  // 它看起来像这一页的一部分，比空白更糟。
  comparison.value = null
  try {
    comparison.value = await getClassComparison(Number(route.params.studentId))
  } catch {
    // 对照是补充信息：它拿不到不该让整页变成错误状态。
    comparisonFailed.value = true
  }
}

onMounted(load)
onMounted(loadComparison)

const tabs = [
  { key: 'history', label: '历次测评' },
  // 与档案页把「班级对照」排在趋势前面同一个理由（§11）：MHT 每学期约一次的普查，
  // 第一场测评当天对照就能用，而趋势至少要两场才画得出东西。
  { key: 'comparison', label: '班级对照' }
]
const activeTab = ref('history')

/**
 * 「最近一次」那一格里的日期。
 *
 * 两个字段分开取是有理由的：`tested_at` 是**真实发生**的测评日（外部导入的那一场
 * 存的是文件里的日期），`submitted_at` 在在线作答时与它相同，而历史行两者都是 null
 * （0013 刻意没有回填，§21）——那时按 `submitted_at` 兜底，读不出日期就留 `—`。
 */
const recentDate = computed(() => {
  const assessment = records.value?.assessment
  if (!assessment) return '—'
  return (assessment.tested_at || assessment.submitted_at || '').slice(0, 10) || '—'
})

/**
 * 历次测评的行：在每一场上补一个**按时间顺序**的场次号。
 *
 * 场次号在排序**之前**按后端给的次序（从旧到新）算好，所以它跟着那一场走：
 * 表头按日期倒序排之后，屏幕上会读成「第 5 次 … 第 1 次」，而不是一串重排过的数字。
 */
const historyRows = computed(() =>
  (records.value?.history || []).map((entry: AssessmentHistoryEntry, index: number) => ({
    ...entry,
    seq: index + 1
  }))
)

/**
 * 列定义。「来源」与「等级」是**枚举列，必须显式声明 `order`**（§3 第四面）：
 * 不声明的话排序拿编码去 `localeCompare`，升序会读成
 * 「一般观察 → 重点关注 → 需要关注」——那看起来只是个正常的升序，没人会怀疑它错了。
 */
const historyColumns: Column[] = [
  { key: 'seq', label: '场次', width: '76px' },
  { key: 'submitted_at', label: '测评日期', sortable: true, width: '124px' },
  { key: 'source', label: '来源', sortable: true, order: SOURCE_ORDER },
  { key: 'total_level', label: '关注等级', sortable: true, order: LEVEL_ORDER },
  { key: 'total_score', label: '总分', sortable: true, align: 'right', width: '90px' },
  { key: 'actions', label: '操作', width: '120px' }
]

/** 去这名学生的关注档案。按钮只在真的有档案时出现，所以点进去一定打得开。 */
function openCase() {
  if (!records.value) return
  router.push(`/counselor/cases/${records.value.student.id}`)
}

/** 查看完整答卷 */
const showAnswers = ref(false)
const answersLoading = ref(false)
const answerError = ref('')
const answers = ref<FullAnswerItem[]>([])
const currentSessionId = ref<number | null>(null)
const answerPurpose = ref('')

async function openAnswers(sessionId: number) {
  currentSessionId.value = sessionId
  showAnswers.value = true
  answerPurpose.value = ''
  answers.value = []
  answerError.value = ''
}

async function loadAnswers() {
  if (!answerPurpose.value.trim()) {
    answerError.value = '请填写查看原因'
    return
  }
  if (!currentSessionId.value || !records.value) return
  answersLoading.value = true
  answerError.value = ''
  try {
    const data = await getSessionFullAnswers(records.value.student.id, currentSessionId.value, answerPurpose.value)
    answers.value = data.items
  } catch (err) {
    answerError.value = err instanceof Error ? err.message : '加载答卷失败'
  } finally {
    answersLoading.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head" v-if="records">
      <div>
        <!-- 眉题不写「学生档案」：这一页读的不是档案，正是**没有档案**也能看的那部分事实。 -->
        <div class="eyebrow">学生测评记录</div>
        <h1>{{ records.student.name }} · {{ records.student.student_no }}</h1>
        <p class="page-desc">
          {{ records.student.grade }} {{ records.student.class_name }} ·
          {{ genderLabel(records.student.gender) }} · {{ ageLabel(records.student.age) }} ·
          本页只回答他考过几次、每次多少分，与是否建立关注档案无关。
        </p>
      </div>
      <div class="actions">
        <button class="btn" @click="router.back()">返回列表</button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <template v-if="records && !loading">
      <!-- 最近一次。取的是 `assessment`（**算数的那一场**，要求 is_effective），
           不是 `history` 的最后一行：后者刻意含被 §18.8 降级过的那一场（那也是他真实
           考过的一次，趋势图上不该抹掉），所以两者在那些学生身上会分岔。 -->
      <div class="card pad">
        <h2>最近一次测评</h2>
        <div v-if="records.assessment.session_id === null" class="empty" style="margin-top:13px">
          这名学生还没有已交卷的测评。
        </div>
        <div v-else class="detail-grid" style="margin-top:13px">
          <div class="detail-row"><span>测评日期</span><b>{{ recentDate }}</b></div>
          <div class="detail-row">
            <span>关注等级</span>
            <span :class="['pill', levelTone(records.assessment.total_level)]">{{
              levelLabel(records.assessment.total_level)
            }}</span>
          </div>
          <!-- 分数与等级挨着日期与来源读才读得出是哪一场的（档案页那张概览卡不显示它，
               那一页回答的是「他属于哪一档」）。评分没成功时这一格是 `—`，
               下面那行「评分状态」会说明为什么。 -->
          <div class="detail-row"><span>总分</span><b>{{ records.assessment.total_score ?? '—' }}</b></div>
          <div class="detail-row">
            <span>来源</span>
            <span class="pill">{{ sourceLabel(records.assessment.source) }}</span>
          </div>
          <div class="detail-row">
            <span>评分状态</span>
            <span :class="['pill', calculationStatusTone(records.assessment.calculation_status)]">{{
              calculationStatusLabel(records.assessment.calculation_status)
            }}</span>
          </div>
        </div>
      </div>

      <div class="tabs" style="margin-top:17px">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          :class="['tab', { active: activeTab === tab.key }]"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <div v-if="activeTab === 'history'" style="margin-top:17px">
        <div class="card pad">
          <h2>总分变化</h2>
          <div style="margin-top:15px">
            <TrendChart :history="records.history" />
          </div>
          <!-- 只有一场时图上是孤零零一个点。明说它画不出趋势，并把读者送去此刻真正
               答得上问题的那个页签——学生刚入学的第一学期就落在这个分支里。 -->
          <div v-if="records.history.length === 1" class="notice" style="margin-top:12px">
            这名学生只有一次测评记录，还看不出变化。要看他当前相对于同龄人的位置，
            请打开<b>班级对照</b>页签——那一页不需要历史数据。
          </div>
          <div class="notice" style="margin-top:12px">
            趋势只描述历次分值变化，不构成诊断或疗效结论。
          </div>
        </div>

        <div class="card pad" style="margin-top:17px">
          <h2>历次测评</h2>
          <!-- 默认按日期**最近的在前**：这是一张记录表，「他最近考成什么样」是它第一件
               要回答的事，与上面那张「最近一次」卡片同一个取向。趋势图仍然是老的在前
               ——它的横轴有日期刻度，读者照着读。 -->
          <DataTable
            :columns="historyColumns"
            :rows="historyRows"
            row-key="session_id"
            default-sort="submitted_at"
            default-order="desc"
            empty-text="这名学生还没有已交卷的测评"
          >
            <template #submitted_at="{ row }">
              {{ row.submitted_at?.slice(0, 10) || '—' }}
            </template>
            <template #source="{ row }">
              <span class="pill">{{ sourceLabel(row.source) }}</span>
            </template>
            <template #total_level="{ row }">
              <span :class="['pill', levelTone(row.total_level)]">{{ levelLabel(row.total_level) }}</span>
            </template>
            <template #total_score="{ row }">
              {{ row.total_score ?? '—' }}
            </template>
            <template #actions="{ row }">
              <button class="btn-link" @click="openAnswers(row.session_id)">查看完整答卷</button>
            </template>
          </DataTable>
        </div>
      </div>

      <!-- 班级对照。面板在 components/ClassComparisonPanel.vue——档案页与这一页共用，
           「均值可能是 null，不能画成 0」那条口径只有那一份定义。 -->
      <div v-if="activeTab === 'comparison'" style="margin-top:17px">
        <ClassComparisonPanel :comparison="comparison" :failed="comparisonFailed" />
      </div>

      <!-- 建档与否。判据是后端返回的 `case_id`，与档案页取详情用的是**同一个定义**
           （`current_care_case`），所以有档案的行点进去一定打得开，反之亦然。

           措辞与建档判据同源：触发建档的是**重点题命中**，不是关注等级。所以这里写
           「重点题未命中」而不是「因为他只是一般观察」——后者会把两件不相干的事
           说成一个因果，而那正是用户看到这一屏时想问的那个问题。 -->
      <div class="card pad" style="margin-top:17px">
        <div class="toolbar" style="justify-content:space-between">
          <div>
            <template v-if="records.case_id === null">
              <b>该生尚未建档（重点题未命中）</b>
              <p class="muted tiny" style="margin:6px 0 0">
                关注档案在重点题（第 85 / 97 题）答「是」时自动建立，关注等级本身不建档，
                所以被评成「需要关注」的学生也可能没有档案。上面那些测评记录不受影响，
                随时可以查看。
              </p>
            </template>
            <template v-else>
              <b>已建立关注档案</b>
              <p class="muted tiny" style="margin:6px 0 0">
                档案里是复核、跟进、家庭回访与复测这些人工工作记录，与上面的测评事实分开保存。
              </p>
            </template>
          </div>
          <button v-if="records.case_id !== null" class="btn primary" @click="openCase">
            查看关注档案
          </button>
        </div>
      </div>
    </template>

    <!-- 完整答卷弹层 -->
    <Modal v-model="showAnswers" title="查看完整答卷">
      <template v-if="answersLoading">
        <SkeletonBlock variant="cards" :rows="3" />
      </template>
      <template v-else-if="answers.length === 0 && !answerError">
        <div class="purpose-form">
          <p>查看原因（必填，将写入审计日志）：</p>
          <textarea v-model="answerPurpose" placeholder="例：家访前核实学生测评详情" rows="3"></textarea>
          <div class="form-actions">
            <button class="btn" @click="showAnswers = false">取消</button>
            <button class="btn primary" :disabled="!answerPurpose.trim()" @click="loadAnswers">确认查看</button>
          </div>
        </div>
      </template>
      <template v-else>
        <div class="answers-panel">
          <div class="answers-header">
            <span class="muted tiny">共 {{ answers.length }} 道题</span>
            <button class="btn-link" @click="showAnswers = false">关闭</button>
          </div>
          <div class="answers-list">
            <div v-for="item in answers" :key="item.question_no" class="answer-row">
              <span class="q-no">{{ item.question_no }}</span>
              <span class="q-text">{{ item.question_text }}</span>
              <span :class="['q-answer', item.answer === 'YES' ? 'yes' : 'no']">
                {{ item.answer === 'YES' ? '是' : '否' }}
              </span>
            </div>
          </div>
        </div>
      </template>
      <div v-if="answerError" class="notice error" style="margin-top:12px">{{ answerError }}</div>
    </Modal>
  </div>
</template>

<style scoped>
.btn-link {
  background: none;
  border: none;
  color: #0876d9;
  cursor: pointer;
  padding: 4px 8px;
  font-size: 13px;
}
.btn-link:hover { text-decoration: underline; }

.purpose-form { padding: 16px 0; }
.purpose-form p { margin: 0 0 8px; color: #536878; font-size: 14px; }
.purpose-form textarea {
  width: 100%;
  min-height: 60px;
  padding: 8px 10px;
  border: 1px solid #cbd8e2;
  border-radius: 4px;
  font: inherit;
  resize: vertical;
}
.form-actions { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end; }

.answers-panel { max-height: 70vh; display: flex; flex-direction: column; }
.answers-header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 8px; border-bottom: 1px solid #edf1f4; }
.answers-list { overflow-y: auto; margin-top: 8px; }
.answer-row {
  display: grid;
  grid-template-columns: 40px 1fr 60px;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #f0f3f5;
  align-items: baseline;
  font-size: 14px;
}
.q-no { color: #72828d; text-align: center; font-weight: 700; }
.q-text { color: #183447; }
.q-answer { text-align: center; font-weight: 700; font-size: 15px; }
.q-answer.yes { color: #c0392b; }
.q-answer.no { color: #6a7b87; }
</style>
