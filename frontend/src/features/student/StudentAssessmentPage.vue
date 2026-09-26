<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppIcon from '../../components/AppIcon.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import StudentHelpDialog from './StudentHelpDialog.vue'
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

// 每题预计耗时（`remainingHint` 用）由系统配置提供，不在这一页里猜一个数。
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

/**
 * 「已经答了的那些，服务端**确认过**的是什么」。学生改主意时本地先落（见 `choose`），
 * 所以保存失败得回滚——而回滚要退到**服务端说过的那个值**，不是退到「空」：
 * 空会让他以为自己没答过，而库里很可能存着他改主意之前的那一个。
 */
const confirmedAnswers = ref<Record<string, 'YES' | 'NO'>>({})

/**
 * 还在路上的答案保存。`inFlight` 是**请求条数**（「保存中…」读它），
 * `pendingChoice` 是每一题**还没送出去的那个意图**。
 *
 * 为什么需要这一层：学生按下的那一刻屏幕上就生效（乐观更新），保存因此**不能再挡住**
 * 他的下一次点击。挡不住之后，同一题上的两次写入就必须由这里排序——**同一题永远只有
 * 一次保存在路上**，其间他改的主意记在 `pendingChoice` 里，等那一次回来再发。
 * 不排的话两次请求可能乱序落库：屏幕上是他最后选的那个，而库里是前一个。
 * 那正是这一页最不能出现的一种不一致（§13：答错的代价由他自己承担）。
 */
let inFlight = 0
const pendingChoice = new Map<string, 'YES' | 'NO'>()
const pumpingKeys = new Set<string>()

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

/** 还差几题。与 `isComplete` 同一组判据的另一种读法，供文案用（不另写一遍「什么算答了」）。 */
const missingCount = computed(() => totalQuestions.value - answeredCount.value)

/**
 * 提交前确认框的那一句话。三件事缺一不可：**已答几题、还差几题、提交后不能自己改**。
 *
 * 「未答 0 题」在这一档里总是成立的（提交门就是「答满了」，见 `submit()`），但它是
 * **明说出来的 0**，不是省掉的一格：这句话是学生最后一次核对自己做了什么的机会，
 * 而「已完成 100/100 题」与「已完成 100/100 题，未答 0 题」在屏幕上不是同一句保证。
 */
const confirmMessage = computed(
  () =>
    `已完成 ${answeredCount.value}/${totalQuestions.value} 题，未答 ${missingCount.value} 题。提交后不能自行修改答案。`
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

/**
 * 选答案。**学生按下去的那一刻，屏幕上就得是他选的那个**——不等服务端往返。
 *
 * 2026-09-26 改。此前这里是 `session.value = await saveAssessmentAnswer(…)`：答案的
 * 唯一来源是那一次往返，于是从按下列答复之间有一段**屏幕上什么变化都没有**的窗口——
 * 按钮不亮、进度不动，紧接着按「下一题」还会被判成「请先选择一个答案」，而那句话是假的
 * （他刚选完）。同一段窗口里再点「否」会被 `saving` 挡掉、**静默丢弃**：他最后看到的是
 * 「是」，而自己按的是「否」——两件事都已经发生，他没有任何办法知道哪一件算数。
 *
 * 所以本地先落、服务端的答复回来再对账，失败就把这一格退回上一个服务端确认过的值
 * （§14 那条「一次失败的读取不许留下上一次的答案」的另一面：不许留下**一个没存上的答案**）。
 * 排序交给 `pumpSaves`——见 `pendingChoice` 上面那一段。
 */
function choose(answer: 'YES' | 'NO') {
  const current = session.value
  if (!current) return
  const key = String(currentNo.value)
  current.answers[key] = answer
  pendingChoice.set(key, answer)
  error.value = ''
  void pumpSaves(key)
}

/** 把某一题上「还没送出去的意图」一个个送出去，同一题同时只有一个请求在路上。 */
async function pumpSaves(key: string) {
  if (pumpingKeys.has(key)) return
  pumpingKeys.add(key)
  try {
    while (pendingChoice.has(key)) {
      const target = pendingChoice.get(key) as 'YES' | 'NO'
      const current = session.value
      if (!current) return
      inFlight += 1
      saving.value = true
      let updated: AssessmentSession | null = null
      let failure: unknown = null
      try {
        updated = await saveAssessmentAnswer(current.id, Number(key), target)
      } catch (err) {
        failure = err
      } finally {
        inFlight -= 1
        saving.value = inFlight > 0
      }
      // 这一趟往返期间会话被放下了（加载失败 / 重试）：这份答复属于**上一份卷子**。
      // 丢掉这次对账，意图也一起清掉——留着它，`waitForPendingSaves` 会一直等一个
      // 再也送不出去的东西，把交卷白白拖满 5 秒。
      const live = session.value
      if (!live) {
        pendingChoice.delete(key)
        continue
      }
      // 答复回来时他又改过主意：这一份已经不算数了，下面那圈会带着新意图再发一次。
      if (pendingChoice.get(key) !== target) continue
      pendingChoice.delete(key)
      if (updated) {
        // 服务端仍然是权威（它可能对值做过归一），但它**只回答了这一题**——
        // 别的题不碰，那些要么已经确认过、要么自己那一次还在路上。
        const settled = updated.answers[key] ?? target
        live.answers[key] = settled
        confirmedAnswers.value[key] = settled
        showToast('info', '已自动保存')
      } else {
        // 退到**服务端确认过的那个值**，不是退到空：空会让他以为自己没答过，
        // 而库里很可能存着他改主意之前的那一个（见 `confirmedAnswers`）。
        const previous = confirmedAnswers.value[key]
        if (previous) live.answers[key] = previous
        else delete live.answers[key]
        showToast('error', failure instanceof Error ? failure.message : '保存失败')
      }
    }
  } finally {
    pumpingKeys.delete(key)
  }
}

/**
 * 等还在路上的答案落定，交卷与「保存退出」前各用一次。
 *
 * 少了它就会出现这样一句话：学生按下「是」之后立刻点提交，而服务端还没收到那一题，
 * 于是它回答「还有 1 题没有作答」——他明明刚答完。**「还在保存」不许变成「没答」。**
 * 上限 5 秒：真碰上卡住的请求也要让他走得掉，那时由服务端给出它自己的判断。
 */
async function waitForPendingSaves(timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs
  while ((inFlight > 0 || pendingChoice.size > 0) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 50))
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
    // 说清**还差几题**，而不只是「有未答题」：一名答了 99 题的学生看到「还有未答题目」
    // 得自己再翻一遍找那一题（`goFirstMissing` 带他去了，但那句话本身没有告诉他
    // 是不是只有一道）。
    error.value = `还有 ${missingCount.value} 题没有作答，已定位到第一道未答题`
    return
  }
  showSubmitConfirm.value = true
}

async function onConfirmSubmit() {
  if (!session.value) return
  try {
    // 先等答案落定：学生按完最后一个「是」就点提交时，服务端可能还没收到那一题。
    await waitForPendingSaves()
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
  // 「已保存」是一句关于**服务端**的话，所以先等答案真的落定再把它说出口：
  // 下面那个数读的是本地（乐观更新之后它已经算上了还在路上的那一题），
  // 而一次失败的保存会把它退回去——先说后等就会说出一个当场变成假的数。
  await waitForPendingSaves()
  // 「已保存」之外还要说出**保存了什么**：一句光秃秃的「答题进度已保存」答不上
  // 「我答的那些还在吗、下次从哪开始」。答了几个数就是学生此刻唯一想核对的事，
  // 而它同时是首页那张卡片上「已答 N / M 题」的同一个数——两处对不上时，
  // 他会怀疑其中一处没保存。
  showToast(
    'info',
    `已保存：答了 ${answeredCount.value}/${totalQuestions.value} 题，下次从这一题接着答`
  )
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
    // 起点即「服务端确认过的那些」：保存失败要退回的就是它（见 `confirmedAnswers`）。
    confirmedAnswers.value = { ...opened.answers }
    questionNos.value = stems.map((stem) => stem.question_no).sort((a, b) => a - b)
    questions.value = Object.fromEntries(stems.map((stem) => [stem.question_no, stem.question_text]))
    currentNo.value = pickStart(opened, loadCursor(opened.id))
  } catch (err) {
    // 重试失败时把上一次的会话也放下：否则页面上是旧数据 + 新错误，两件事各说各话。
    session.value = null
    // 确认过的答案与还在路上的意图一起清掉：留着的话，上一次那份会话的回滚基准会
    // 落到新会话上——而两者根本不是同一份卷子。
    confirmedAnswers.value = {}
    pendingChoice.clear()
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
            <!-- 那个「✓」走 `AppIcon`（Phase E 第 3 条）：它是全站最后一处「Unicode
                 字符充当视觉符号」的活体，字形由字体决定。它对读屏软件没有信息量
                 （旁边那句话本身就是完整的），所以图标 `aria-hidden`、这里不补文字。 -->
            <span class="muted tiny auto-save-hint">
              <template v-if="saving">保存中…</template>
              <template v-else><AppIcon name="check" /> 选择后自动保存，可随时退出后继续</template>
            </span>
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

        <!-- 与首页那句**逐字相同**：同一件承诺在两个屏幕上不能有两种说法，
             而学生是从首页走过来的，他会以为换了一页就换了一套规矩。 -->
        <p class="muted tiny assessment-privacy">
          你的回答不会在班级中公开；只有经授权的工作人员能按职责查看，且每一次查看都会留下记录。
        </p>
      </div>
    </template>

    <button v-if="session && !loading" class="help-fab" type="button" @click="showHelpModal = true">
      我想找人聊聊
    </button>

    <ConfirmDialog
      :open="showSubmitConfirm"
      title="确认提交测评"
      :message="confirmMessage"
      confirm-text="确认提交"
      cancel-text="返回检查"
      @confirm="onConfirmSubmit"
      @update:open="showSubmitConfirm = $event"
    />

    <!-- 三个学生页共用的同一个弹层。此前这里是三份复制中的一份，而这一份与另外两份
         **说的不是同一件事**：它没有那三行学校配置（换成了一句「联系学校心理老师」），
         紧急提示的措辞也各不相同。同一个按钮在三个屏幕上给出三种回答，而学生最需要
         它的那一刻，正是他刚在答题页上被打断的那一次。 -->
    <StudentHelpDialog v-model:open="showHelpModal" />

  </div>
</template>
