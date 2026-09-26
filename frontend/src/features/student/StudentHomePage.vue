<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import ErrorState from '../../components/ErrorState.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import StudentHelpDialog from './StudentHelpDialog.vue'
import { useSettings } from '../../composables/useSettings'
import { getMe, getStudentTasks, type StudentTask } from '../../services/api'
import { formatDateTime } from '../../services/dates'
import { TARGET_STATUS_LABELS, taskStatusLabel } from '../../services/labels'

const router = useRouter()

const { settings } = useSettings()
const tasks = ref<StudentTask[]>([])
const loading = ref(true)
const error = ref('')
const showPrivacy = ref(false)
const showHelpModal = ref(false)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'student') {
      await router.push('/login')
      return
    }
    tasks.value = await getStudentTasks()
  } catch (err) {
    // 角色不对 → 回登录页（那是「你不该在这一页」）；**读取失败不是那一回事**。
    // 此前这里一律 `router.push('/login')`，于是后端一次 500 或一次抖动的读
    // 会把学生踢到登录页，他看到的是「登录过期了」——一个说不出口的错误被换成了
    // 一句关于他的、恰好错的结论（§2：服务端答了话与一个字都没收到要分得开）。
    // 现在按 §14 给它一条错误与一个「重试」，空态（「当前没有待完成测评任务」）
    // 只在真的读到空列表时出现。
    error.value = err instanceof Error ? err.message : '没能读到你的测评任务'
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

/**
 * 进度与分母。**题数来自服务端**（`question_count`，与答题页读的是同一份题库），
 * 不是写死的 100。
 *
 * 此前这里写死 100：一所学校的量表是 60 题时，答满的学生看到「已完成全部 100 题」，
 * 答了 40 题的那个看到「已答 40 / 100」——两句都在说一件没发生过的事，而进度条
 * 的宽度 `answered_count%` 也一起错。`question_count` 为 `null`（这一版量表没有题，
 * 一个不该出现的状态）时不显示分母，只显示已答数：一个编出来的分母比没有分母更糟。
 */
function answeredLabel(task: StudentTask) {
  const total = task.question_count
  if (isDone(task)) return total ? `已完成全部 ${total} 题` : '已完成'
  if (!total) return task.answered_count > 0 ? `已答 ${task.answered_count} 题` : '尚未开始'
  if (task.answered_count <= 0) return `共 ${total} 题，尚未开始`
  return `已答 ${task.answered_count} / ${total} 题`
}

function progressPercent(task: StudentTask) {
  const total = task.question_count
  if (!total || total <= 0) return 0
  return Math.min(100, Math.round((task.answered_count / total) * 100))
}

/**
 * 预计用时**只从配置算**，不写死一个「约 15 分钟」。
 *
 * 出处与答题页那句「预计还需约 N 分钟」是同一个（`settings.ui.seconds_per_question`，
 * `StudentAssessmentPage.vue` 的 `remainingHint`）——一处配置、两处读，所以两句话
 * 不可能各说各话。学校没配过时它有一个出厂值（12 秒/题），那仍然是一个**配置值**，
 * 不是这个文件里猜的数。
 *
 * 只在**还能作答**时给：已完成、以及窗口还没开/已经关掉的那两档都答不了，
 * 给一个「预计约 3 分钟」等于在邀请学生去做一件他做不到的事。
 */
function estimateLabel(task: StudentTask) {
  const total = task.question_count
  if (!canAnswer(task) || isDone(task) || !total) return ''
  const left = total - task.answered_count
  if (left <= 0) return ''
  const minutes = Math.max(1, Math.round((left * settings.value.ui.seconds_per_question) / 60))
  return `预计约 ${minutes} 分钟`
}

/**
 * 不能作答时**说出为什么**。
 *
 * 一个灰掉的按钮不说原因，读起来是「系统坏了」或者「我不被允许」——两种都会让学生
 * 去问老师，而答案本来就在这一行里（窗口还没开 / 已经结束 / 你已经交过）。日期用
 * 全站同一个 `formatDateTime`，不在这里另写一种格式。
 */
function blockedReason(task: StudentTask) {
  if (isDone(task)) return '你已经交过这一场，不需要再作答。'
  if (task.status === 'NOT_STARTED') {
    return task.start_at ? `这一场将于 ${formatDateTime(task.start_at)} 开始。` : '这一场还没有开始。'
  }
  if (task.status === 'CLOSED') {
    return task.end_at ? `这一场已于 ${formatDateTime(task.end_at)} 截止。` : '这一场已经结束。'
  }
  if (task.status === 'PAUSED') return '这一场已由学校暂停，请等老师通知。'
  return ''
}

/** 截止时间只在服务端给了的时候出现；`—` 不写在这里（那是「读不到」的占位，不是日期）。 */
function deadlineLabel(task: StudentTask) {
  return task.end_at ? `截止 ${formatDateTime(task.end_at)}` : ''
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
        <p class="page-desc">请在安静环境中按真实感受作答。提交后答案将锁定，学生端不展示分数或关注等级。</p>
      </div>
      <div class="actions">
        <button class="btn" @click="showPrivacy = true">隐私说明</button>
      </div>
    </div>

    <!-- 隐私与审计这一句是**一句话的承诺**（详细角色边界在右边那个弹层里）。
         「每次敏感访问都会留下记录」那半句不能省：它是学生能对这套系统抱有的、
         唯一一条可验证的保证。 -->
    <div class="notice" style="margin-bottom: 17px">
      你的回答不会在班级中公开；只有经授权的工作人员能按职责查看，且每一次查看都会留下记录。
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="1" />
    <ErrorState v-else-if="error" :message="error" :on-retry="load" />

    <template v-else>
      <!-- 自动保存是**逐题**的（`choose()` 每选一次就调 `saveAssessmentAnswer`），
           所以这句话有事实依据，不是安慰。它出现在首页是因为学生要在点进答题页
           *之前*就知道「中途退出不会白做」——这是他不开始作答的头号原因。 -->
      <p v-if="tasks.length" class="muted tiny auto-save-hint" style="margin-bottom: 10px">
        作答时每选一题都会自动保存，中途可以退出，下次接着答。
      </p>

      <div v-if="tasks.length" class="task-list">
        <article v-for="task in tasks" :key="task.id" class="task-card">
          <div class="task-info">
            <strong>{{ task.name }}</strong>
            <!-- target_status is this student's own progress; task.status is the
                 campaign's state (ACTIVE/CLOSED) and is not meaningful to them. -->
            <span class="muted tiny">
              {{ answeredLabel(task) }} · {{ targetLabel(task) }}
              <template v-if="deadlineLabel(task)"> · {{ deadlineLabel(task) }}</template>
              <template v-if="estimateLabel(task)"> · {{ estimateLabel(task) }}</template>
            </span>
            <div class="progress" style="margin-top: 8px">
              <i :style="{ width: `${progressPercent(task)}%` }"></i>
            </div>
            <!-- 灰掉的按钮必须说得出为什么（§17：空态与「坏了」不是一回事）。 -->
            <span v-if="blockedReason(task)" class="muted tiny">{{ blockedReason(task) }}</span>
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

      <StudentHelpDialog v-model:open="showHelpModal" />
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
          每次敏感访问都会留下记录，可以追溯；正式上线时，学校需配置数据保存期限、访问范围和紧急处置规则。
        </p>
      </div>
      <template #footer>
        <button class="btn primary" @click="showPrivacy = false">我知道了</button>
      </template>
    </Modal>

  </div>
</template>
