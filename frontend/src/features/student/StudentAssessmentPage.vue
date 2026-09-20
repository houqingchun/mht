<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import {
  createAssessmentSession,
  getMe,
  getSessionQuestions,
  saveAssessmentAnswer,
  submitAssessmentSession,
  type AssessmentSession
} from '../../services/api'

const route = useRoute()

// 辅导室位置与联系方式由系统配置提供（此前三处硬编码副本）
const { settings } = useSettings()
const router = useRouter()
const session = ref<AssessmentSession | null>(null)
const currentNo = ref(1)
/**
 * 这一版量表的题号（升序）与题干。**题号的唯一来源**——进度、导航、「定位未答」、
 * 「答满了没有」全部从它派生，所以题库换版本时这一页跟着走（见下面 `totalQuestions`）。
 */
const questionNos = ref<number[]>([])
const questions = ref<Record<number, string>>({})
const loading = ref(true)
const saving = ref(false)
const error = ref('')

const showSubmitConfirm = ref(false)
const showHelpModal = ref(false)

/**
 * 光标（「上次答到第几题」）按**会话**存，不再是一个全局键。
 *
 * 2026-09-17 之前这里是 `LOCAL_KEY = 'xlp_assessment_state'`，一个键里存
 * `{ currentNo, answers }`。三件事叠在一起：
 *
 * 1. **机房是共用电脑。** 下一个学生打开答题页会读到上一个人的光标，
 *    还会把上一个人的答案合并进自己的会话。
 * 2. 合并的方向与注释相反：注释写「服务端是事实来源」，代码却是
 *    `{ ...session.answers, ...localAnswers }`——**本地覆盖服务端**。
 * 3. 那份本地答案副本从来没有比服务端新过：唯一的写入源读的就是服务端
 *    返回的 `session.value.answers`，所以它只是一个会过期的影子。
 *
 * 现在本地只留一个数：光标。键里带上会话 id（会话 = 这个学生 · 这场任务），
 * 两个人不可能撞上；答案一律以服务端为准，本地不留副本。
 */
const LEGACY_LOCAL_KEY = 'xlp_assessment_state'

function cursorKey(sessionId: number) {
  return `xlp_assessment_cursor:${sessionId}`
}

/**
 * 老键里存着**上一个人**的答案（见上面那段），任何学生打开这一页都该把它清掉。
 * 与「迁移到新键」不是一回事：那份数据不该被搬过来，只该被删掉。
 */
function clearLegacyLocalState() {
  try { localStorage.removeItem(LEGACY_LOCAL_KEY) } catch { /* ignore */ }
}

function loadCursor(sessionId: number): number | null {
  try {
    const raw = localStorage.getItem(cursorKey(sessionId))
    if (raw === null) return null
    const no = Number(raw)
    return Number.isFinite(no) ? no : null
  } catch { return null }
}

function saveCursor(sessionId: number, no: number) {
  try { localStorage.setItem(cursorKey(sessionId), String(no)) } catch { /* ignore */ }
}

watch(currentNo, (no) => {
  if (session.value) saveCursor(session.value.id, no)
})

const currentAnswer = computed(() => session.value?.answers[String(currentNo.value)] || '')

/**
 * 已答数 = **这一版题库里的题**里答过的那些。
 *
 * 不数 `Object.keys(answers).length`：那会把不属于当前题库的答案也算进来
 * （题库换版之后会话上可能挂着旧题号的答案），于是分子可以比题数还大，
 * 进度条越过 100%，而「答满了没有」跟着一起错。
 */
const answeredCount = computed(() => {
  const answers = session.value?.answers
  if (!answers) return 0
  return questionNos.value.filter((no) => Boolean(answers[String(no)])).length
})

/**
 * 题数来自**这一版量表的题目**，不是写死的 100。
 *
 * 2026-09-17 之前，100 在这个文件里出现六次（进度、预计时长、`next()` 的上界、
 * 「定位未答」的搜索空间、模板里「共 100 题」与「下一题 / 提交」的分岔、
 * 提交确认框的文案）。题库一换成不是 100 题的版本，这一页有两处会坏：
 * 题**少**于一版时，学生翻过最后一题会看到「第 61 题（题干未加载…）」这种占位卡，
 * 而提交门是「答满 100」——**永远凑不齐，交不出去**；题**多**于一版时，
 * `next()` 停在第 100 题，后面那几道走不到，交了也被服务端挡回来
 * （服务端的判据是规则版本的 `question_count`，见 `assessment_service.session_payload`
 * 旁边那段注释，两处回答的是两个不同的问题）。
 *
 * 现在题号只定义一次——就在 `questionNos` 里——其余全部从它派生。
 */
const totalQuestions = computed(() => questionNos.value.length)

const isComplete = computed(() =>
  totalQuestions.value > 0 &&
  questionNos.value.every((no) => Boolean(session.value?.answers[String(no)]))
)

const progressPercent = computed(() =>
  totalQuestions.value === 0 ? 0 : Math.round((answeredCount.value / totalQuestions.value) * 100)
)

/**
 * Rough time-to-finish. ~12s per item is a deliberately loose estimate — the
 * point is to reassure the student this is finite, not to promise a duration.
 */
const remainingHint = computed(() => {
  const left = totalQuestions.value - answeredCount.value
  if (left <= 0) return ''
  const minutes = Math.max(1, Math.round((left * settings.value.ui.seconds_per_question) / 60))
  return ` · 预计还需约 ${minutes} 分钟`
})

const currentIndex = computed(() => questionNos.value.indexOf(currentNo.value))
const isFirstQuestion = computed(() => currentIndex.value <= 0)
const isLastQuestion = computed(
  () => totalQuestions.value > 0 && currentIndex.value === totalQuestions.value - 1
)

function questionText(no: number) {
  // 题干已在 `loadSession` 里**先拉完再渲染**，所以这一行取不到只可能是题号不在
  // 这一版题库里（一个 bug），不会是「没加载出来」。留一句能看出问题的占位，
  // 而不是留白——但不要指望它兜住加载失败：那种情况现在根本走不到答题页。
  return questions.value[no] || `第 ${no} 题（这一题不在题库中，请刷新页面重试）`
}

async function choose(answer: 'YES' | 'NO') {
  if (!session.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    session.value = await saveAssessmentAnswer(session.value.id, currentNo.value, answer)
    showToast('info', '已自动保存')
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function next() {
  if (!currentAnswer.value) {
    error.value = '请先选择一个答案'
    return
  }
  const index = currentIndex.value
  if (index >= 0 && index < totalQuestions.value - 1) {
    currentNo.value = questionNos.value[index + 1]
  }
}

function previous() {
  const index = currentIndex.value
  if (index > 0) currentNo.value = questionNos.value[index - 1]
}

function goFirstMissing() {
  if (!session.value) return
  const missing = questionNos.value.find((no) => !session.value?.answers[String(no)])
  if (missing) {
    currentNo.value = missing
    showToast('info', `已定位到第 ${missing} 题`)
  } else {
    showToast('info', '全部题目已完成，可以提交')
  }
}

async function submit() {
  if (!session.value) return
  if (!isComplete.value) {
    goFirstMissing()
    error.value = '还有未答题目，已定位到第一道未答题'
    return
  }
  showSubmitConfirm.value = true
}

async function onConfirmSubmit() {
  if (!session.value) return
  try {
    const outcome = await submitAssessmentSession(session.value.id)
    // 交完卷这张卷子的光标就没有意义了；不清的话，同一个学生会话 id 不会再出现，
    // 但那个键会一直躺在浏览器里。
    try { localStorage.removeItem(cursorKey(session.value.id)) } catch { /* ignore */ }
    // 「提交成功」说的是他做的事（答卷存下来了），而**不是**成绩已经算出来。
    // 评分那一步失败时，`result` 是 null，交卷仍然是成功的——所以两句话分开说：
    // 一句「测评已成功提交」在这里是真的，但只留这一句，学生会以为一切都好了。
    if (outcome.result === null) {
      showToast('info', '答卷已提交；成绩处理还需要老师再看一下，你不需要重新作答')
    } else {
      showToast('success', '测评已成功提交')
    }
    await router.push('/student/home')
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '提交失败')
  }
}

async function saveExit() {
  if (session.value) saveCursor(session.value.id, currentNo.value)
  showToast('info', '答题进度已保存')
  await router.push('/student/home')
}

/** 回到哪儿：本地光标优先（同一台电脑上他自己上次的位置），否则用服务端的判断。 */
function pickStart(opened: AssessmentSession, saved: number | null): number {
  const nos = questionNos.value
  if (saved !== null && nos.includes(saved)) return saved
  // 服务端的 `current_question_no` 就是「第一道未答题」（`assessment_service.session_payload`）。
  // 它此前没有任何读者——学生页拿写死的 `range(1, 100)` 自己在本地算了一遍。
  const server = opened.current_question_no
  if (server !== null && nos.includes(server)) return server
  // 一道都不缺（或者服务端也说不出所以然）：停在最后一题，那里才有「提交测评」。
  // 从第一题重新翻一遍不是「继续答题」。
  return nos[nos.length - 1]
}

async function loadSession() {
  loading.value = true
  error.value = ''
  clearLegacyLocalState()
  try {
    const me = await getMe()
    if (me.role_code !== 'student') {
      await router.push('/login')
      return
    }
    const taskId = Number(route.params.taskId)
    const opened = await createAssessmentSession(taskId)

    // 题干**先拉完再渲染**（2026-09-17 改）。
    //
    // 此前这一段是 `try { … } catch { questions.value = {} }`：拉不到题干就退化成
    // 「第 N 题（题干未加载，请刷新页面重试）」这种占位卡，而**作答与提交照旧可用**。
    // 学生完全可以对着一串占位符选完一整份「是」，交出一份每道题都不知道在问什么、
    // 却与真实作答在库里长得一模一样的答卷——事后没有任何办法分辨。
    // 现在拉不到题干就没有答题页，只有一条错误和一个「重试」。
    const stems = await getSessionQuestions(opened.id)
    if (stems.length === 0) {
      throw new Error('这一版量表还没有题目，请联系心理老师')
    }

    session.value = opened
    questionNos.value = stems.map((stem) => stem.question_no).sort((a, b) => a - b)
    questions.value = Object.fromEntries(stems.map((stem) => [stem.question_no, stem.question_text]))
    currentNo.value = pickStart(opened, loadCursor(opened.id))
  } catch (err) {
    // 重试失败时把上一次的会话也放下：否则页面上是旧数据 + 新错误，两件事各说各话。
    session.value = null
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadSession)
</script>

<template>
  <div class="student-assessment-page">
    <SkeletonBlock v-if="loading" variant="cards" :rows="1" />
    <ErrorState v-if="error && !loading && !session" :message="error" :on-retry="loadSession" />

    <template v-if="session && !loading">
      <!-- Progress leads: it is the one thing a student mid-way wants to know. -->
      <div class="assessment-bar">
        <div class="assessment-bar-text">
          <strong>第 {{ currentNo }} 题</strong>
          <span class="muted tiny">
            共 {{ totalQuestions }} 题 · 已完成 {{ answeredCount }}/{{ totalQuestions }}（{{ progressPercent }}%）{{ remainingHint }}
          </span>
        </div>
        <div class="progress">
          <i :style="{ width: `${progressPercent}%` }"></i>
        </div>
      </div>

      <div class="question-shell">
        <article class="card question-card">
          <div class="question-text">{{ questionText(currentNo) }}</div>

          <div class="answer-grid">
            <button :class="{ selected: currentAnswer === 'YES' }" type="button" @click="choose('YES')">是</button>
            <button :class="{ selected: currentAnswer === 'NO' }" type="button" @click="choose('NO')">否</button>
          </div>

          <ErrorState v-if="error" :message="error" />

          <div class="question-foot">
            <span class="muted tiny">{{ saving ? '保存中…' : '✓ 选择后自动保存，可随时退出后继续' }}</span>
            <div class="actions">
              <button class="btn" :disabled="isFirstQuestion" @click="previous">上一题</button>
              <button v-if="!isLastQuestion" class="btn primary" @click="next">下一题</button>
              <button v-else class="btn primary" @click="submit">提交测评</button>
            </div>
          </div>
        </article>

        <!-- Secondary actions sit below the question so they never push it off-screen. -->
        <div class="question-secondary">
          <button class="btn small" type="button" @click="goFirstMissing">定位未答</button>
          <button class="btn small" type="button" @click="saveExit">暂不继续，保存退出</button>
        </div>

        <p class="muted tiny assessment-privacy">
          你的回答不会在班级中公开。仅授权人员可按职责查看，每次敏感访问都会留下记录。
        </p>
      </div>
    </template>

    <button v-if="session && !loading" class="help-fab" type="button" @click="showHelpModal = true">
      我想找人聊聊
    </button>

    <ConfirmDialog
      :open="showSubmitConfirm"
      title="确认提交测评"
      :message="`已完成 ${answeredCount}/${totalQuestions} 题。提交后不能自行修改答案。`"
      confirm-text="确认提交"
      cancel-text="返回检查"
      @confirm="onConfirmSubmit"
      @update:open="showSubmitConfirm = $event"
    />

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
        <p class="muted tiny">
          若处于紧急危险中，请立即联系身边可信任的成年人或当地紧急服务。
        </p>
      </div>
    </Modal>

  </div>
</template>
