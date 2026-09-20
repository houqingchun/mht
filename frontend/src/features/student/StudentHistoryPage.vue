<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { getMe, getStudentAssessmentHistory, type StudentAssessmentHistoryItem } from '../../services/api'
import { calculationStatusLabel, calculationStatusTone, sourceLabel, targetStatusLabel } from '../../services/labels'
import { formatDuration } from '../../services/dates'
import { useSettings } from '../../composables/useSettings'

const router = useRouter()

// 辅导室位置与联系方式由系统配置提供（此前三处硬编码副本）
const { settings } = useSettings()
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

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">完成记录</div>
        <h1>我的测评记录</h1>
        <p class="page-desc">学生端不展示分数、风险标签、重点题和诊断性描述。</p>
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
          <div class="detail-row"><span>完成题目</span><b>{{ item.answered_count }} / 100</b></div>
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

    <button class="help-fab" type="button" @click="showHelp = true">我想找人聊聊</button>

    <Modal :model-value="showHelp" title="我想找人聊聊" @update:model-value="showHelp = $event">
      <div class="form-grid">
        <div class="notice">
          如果你现在感到不舒服，可以联系学校心理老师、家长或一位你信任的成年人。
        </div>
        <div class="detail-grid" style="margin-top: 14px">
          <div class="detail-row"><span>学校心理辅导室</span><b>{{ settings.org.counselling_room }}</b></div>
          <div class="detail-row"><span>开放时间</span><b>{{ settings.org.counselling_hours }}</b></div>
          <div class="detail-row"><span>校内联系</span><b>{{ settings.org.counselling_contact }}</b></div>
        </div>
      </div>
      <template #footer>
        <button class="btn primary" @click="showHelp = false">我知道了</button>
      </template>
    </Modal>

  </div>
</template>
