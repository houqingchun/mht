<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import StudentHelpDialog from './StudentHelpDialog.vue'
import { getMe, getStudentAssessmentHistory, type StudentAssessmentHistoryItem } from '../../services/api'
import { calculationStatusLabel, calculationStatusTone, sourceLabel, targetStatusLabel } from '../../services/labels'
import { formatDuration } from '../../services/dates'

const router = useRouter()

const history = ref<StudentAssessmentHistoryItem[]>([])
const loading = ref(true)
const error = ref('')
const showHelp = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'student') {
      await router.push('/login')
      return
    }
    history.value = await getStudentAssessmentHistory()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

// 2026-09-19：这里原本有一份自己的 `statusLabel`（COMPLETED → 已完成…），与
// `labels.ts` 的 `TARGET_STATUS_LABELS` 是同一张表的两份定义——同一个码在别处改了中文，
// 这一页会照旧显示旧词（§3 第一面的反面：**映射层只能有一份**）。改用它。

/** 完成题数。分母来自服务端（与答题页读的是同一份题库），**不是写死的 100**。 */
function answeredText(item: StudentAssessmentHistoryItem) {
  return item.question_count ? `${item.answered_count} / ${item.question_count} 题` : `${item.answered_count} 题`
}

onMounted(load)
</script>

<template>
  <div class="student-page">
    <div class="page-head">
      <div>
        <div class="eyebrow">完成记录</div>
        <h1>我的测评记录</h1>
        <p class="page-desc">学生端不展示分数、关注等级、重点题和诊断性描述。</p>
      </div>
      <div class="actions">
        <button class="btn" @click="router.push('/student/home')">返回</button>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="1" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <div v-if="!loading && !error && history.length" class="task-list">
      <article v-for="item in history" :key="item.task_no" class="card pad">
        <div class="detail-grid">
          <div class="detail-row"><span>任务</span><b>{{ item.task_name }}</b></div>
          <!-- 学校在外面做过一次普查再导进来时，这行说明为什么学生自己没在本系统里答过
               却有这条记录。系统内作答不渲染——学生自己答的，不必再告诉他从哪来。 -->
          <div v-if="item.source === 'IMPORTED'" class="detail-row">
            <span>来源</span><b>{{ sourceLabel(item.source) }}</b>
          </div>
          <div class="detail-row"><span>当前状态</span><span class="pill green">{{ targetStatusLabel(item.status) }}</span></div>
          <!-- 「处理状态」这一行**不条件渲染**：它对每一行都成立——系统内作答与外部导入
               两种来源都要经过这一列，没有会话的那一行是「—」。学生的记录页上不能只有
               「已完成」这一个词：交卷（他做的事）与此后系统处理这份答卷（学校做的事）
               是两件不同的事，而这一列是那两者之间唯一的字。 -->
          <div class="detail-row">
            <span>处理状态</span>
            <span :class="['pill', calculationStatusTone(item.calculation_status)]">{{
              calculationStatusLabel(item.calculation_status)
            }}</span>
          </div>
          <!-- 分母来自服务端（`question_count`），与答题页读的是同一份题库。
               此前写死 100：题库换成非 100 题的版本之后，这一行会把一名答满全部
               题目的学生说成「60 / 100」，而它旁边就是「已完成」。 -->
          <div class="detail-row"><span>完成题目</span><b>{{ answeredText(item) }}</b></div>
          <!-- 学生能看到自己的用时，但这里只陈述事实，不评价快慢、不与别人比较：
               系统是筛查与关怀工具，不替学生给这次作答下结论。未提交时后端为 null。 -->
          <div class="detail-row"><span>作答用时</span><b>{{ formatDuration(item.duration_seconds) }}</b></div>
          <div class="detail-row">
            <span>结果说明</span>
            <b>{{ item.status === 'COMPLETED' ? '已提交给学校心理工作老师' : '尚未提交' }}</b>
          </div>
        </div>
        <div v-if="item.status === 'COMPLETED'" class="notice" style="margin-top:16px">
          测评已经完成。如有需要，学校心理老师可能与你联系。你也可以主动使用「我想找人聊聊」寻求帮助。
        </div>
        <!-- 处理没成功时要说清楚**他不用做什么**。不写这一句的话，一名交完卷的学生
             看到「计算失败」，唯一能做的推理是「我是不是得重答一遍」——而那正是他
             不该做的事：答卷原样留在系统里，学校心理老师会重算一次。
             这里**不出现失败原因**：那是一段给维护者看的文本，对学生的处境没有帮助。 -->
        <div v-if="item.calculation_status === 'CALCULATION_FAILED'" class="notice" style="margin-top:16px">
          你的答卷已经收到并保存下来了。这次成绩处理没有完成，学校心理老师会再处理一次，
          你不需要重新作答。
        </div>
      </article>
    </div>
    <p v-else-if="!loading && !error" class="muted-text">当前没有测评记录。</p>

    <!-- 三个学生页共用的同一个弹层。此前这里是三份复制中的一份，三份的正文各写各的：
         这一份**无条件**渲染那三行，所以学校没配过时，学生会看到三行空的「开放时间」
         ——而空的那一格读起来像「有辅导室，只是我不知道时间」。现在空值整行不出现，
         见 `StudentHelpDialog.vue`。 -->
    <StudentHelpDialog v-model:open="showHelp" />
    <button class="help-fab" type="button" @click="showHelp = true">我想找人聊聊</button>

  </div>
</template>
