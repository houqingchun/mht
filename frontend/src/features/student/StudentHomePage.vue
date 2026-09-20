<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import { getMe, getStudentTasks, type StudentTask } from '../../services/api'
import { TARGET_STATUS_LABELS, taskStatusLabel } from '../../services/labels'

const router = useRouter()

// 辅导室位置与联系方式由系统配置提供（此前三处硬编码副本）
const { settings } = useSettings()
const user = ref<{ display_name: string } | null>(null)
const tasks = ref<StudentTask[]>([])
const loading = ref(true)
const showPrivacy = ref(false)
const showHelpModal = ref(false)

async function load() {
  loading.value = true
  try {
    const me = await getMe()
    if (me.role_code !== 'student') {
      await router.push('/login')
      return
    }
    user.value = me
    tasks.value = await getStudentTasks()
  } catch {
    await router.push('/login')
  } finally {
    loading.value = false
  }
}

function openTask(taskId: number) {
  router.push(`/student/assessment/${taskId}`)
}

function isDone(task: StudentTask) {
  return task.target_status === 'COMPLETED'
}

/** 这场测评现在能不能新开一张卷子。判据与后端那道门**逐字对齐**：那边是
 * `effective_task_status(...) != "ACTIVE"` → 404（`assessment_service.create_or_get_session`），
 * 所以这里也只认 ACTIVE，不另写一套「什么算开着」——两套判据漂开的那一天，
 * 屏幕上会重新出现一个点了必然失败的按钮。 */
function canAnswer(task: StudentTask) {
  return task.status === 'ACTIVE'
}

/** Students never see raw backend codes — only their own completion state. */
function answeredLabel(task: StudentTask) {
  if (isDone(task)) return '已完成全部 100 题'
  return task.answered_count > 0 ? `已答 ${task.answered_count} / 100 题` : '尚未开始'
}

function targetLabel(task: StudentTask) {
  return TARGET_STATUS_LABELS[task.target_status] || '待完成'
}

onMounted(load)
</script>

<template>
  <div class="student-page">
    <div class="page-head">
      <div>
        <div class="eyebrow">学生测评</div>
        <h1>我的测评任务</h1>
        <p class="page-desc">请在安静环境中按真实感受作答。提交后答案将锁定，学生端不展示分数或风险标签。</p>
      </div>
      <div class="actions">
        <button class="btn" @click="showPrivacy = true">隐私说明</button>
      </div>
    </div>

    <div class="notice" style="margin-bottom: 17px">
      你的回答不会在班级中公开。正式系统仅允许授权人员按职责查看，每次敏感访问都会留下记录。
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="1" />

    <template v-if="!loading">
      <div v-if="tasks.length" class="task-list">
        <article v-for="task in tasks" :key="task.id" class="task-card">
          <div class="task-info">
            <strong>{{ task.name }}</strong>
            <!-- target_status is this student's own progress; task.status is the
                 campaign's state (ACTIVE/CLOSED) and is not meaningful to them. -->
            <span class="muted tiny">{{ answeredLabel(task) }} · {{ targetLabel(task) }}</span>
            <div class="progress" style="margin-top: 8px">
              <i :style="{ width: `${task.answered_count}%` }"></i>
            </div>
          </div>
          <div class="task-actions">
            <!-- Gate on target_status (this student's state), not status (the
                 campaign's), otherwise a finished assessment looks re-takeable. -->
            <button v-if="isDone(task)" class="btn" disabled>已完成</button>
            <!-- 但「这场测评本身还没开始 / 已经结束」是另一回事，那时不能给一个
                 点得动的按钮：后端 `create_or_get_session` 只放行 ACTIVE（§12 的
                 「界面说已结束时那个端点就真的开不了」——同一句话的另一面），点了
                 只会拿到 404「测评任务不存在或不可用」，而屏幕上刚写着「开始作答」。
                 文案取 `labels.ts` 的表，不在这个文件里另写一份中文。 -->
            <button v-else-if="!canAnswer(task)" class="btn" disabled>
              {{ taskStatusLabel(task.status) }}
            </button>
            <button v-else class="btn primary" @click="openTask(task.id)">
              {{ task.answered_count > 0 ? '继续作答' : '开始作答' }}
            </button>
          </div>
        </article>
      </div>
      <p v-else class="muted-text">当前没有待完成测评任务。</p>

      <button class="help-fab" type="button" @click="showHelpModal = true">我想找人聊聊</button>
    </template>

    <Modal :model-value="showPrivacy" title="你的信息如何被使用" size="md" @update:model-value="showPrivacy = $event">
      <div class="form-grid">
        <div class="notice">
          回答不会在班级中公开。只有经过授权的工作人员能够按职责查看相应信息。
        </div>
        <div class="detail-grid" style="margin-top: 14px">
          <div class="detail-row"><span>心理老师</span><b>按授权处理测评与跟进</b></div>
          <div class="detail-row"><span>德育领导</span><b>优先查看汇总和必要进展</b></div>
          <div class="detail-row"><span>系统管理员</span><b>默认不能查看心理内容</b></div>
        </div>
        <p class="muted tiny">
          正式上线时，学校需配置数据保存期限、访问范围和紧急处置规则。
        </p>
      </div>
      <template #footer>
        <button class="btn primary" @click="showPrivacy = false">我知道了</button>
      </template>
    </Modal>

    <Modal :model-value="showHelpModal" title="我想找人聊聊" size="md" @update:model-value="showHelpModal = $event">
      <div class="form-grid">
        <div class="notice">
          如果你现在感到不舒服，可以暂停填写，并联系学校心理老师、家长或一位你信任的成年人。
        </div>
        <div class="detail-grid" style="margin-top: 14px">
          <div class="detail-row"><span>学校心理辅导室</span><b>{{ settings.org.counselling_room }}</b></div>
          <div class="detail-row"><span>开放时间</span><b>{{ settings.org.counselling_hours }}</b></div>
          <div class="detail-row"><span>校内联系</span><b>{{ settings.org.counselling_contact }}</b></div>
        </div>
      </div>
    </Modal>

  </div>
</template>
