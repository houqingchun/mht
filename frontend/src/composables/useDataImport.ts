import { computed, ref } from 'vue'
import { showToast } from '../services/toast'
import { downloadCsv } from '../services/csv'
import {
  previewStudentImport,
  commitStudentImport,
  previewScaleImport,
  createScaleDraft,
  previewAssessmentImport,
  commitAssessmentImport,
  getAssessmentImportRows,
  downloadAssessmentTemplate,
  type StudentImportPreview,
  type ScaleImportPreview,
  type AssessmentImportBatch,
  type AssessmentImportRow,
  type AssessmentRowCounts,
  type AssessmentImportResolution,
  type StudentImportResolution
} from '../services/api'

/**
 * Shared student / question-bank / assessment-record import flow.
 *
 * All three follow the same backend contract: upload a file to a preview
 * endpoint, render the server's per-row validation, then submit something the
 * preview handed back to commit. All validation rules live server-side — the
 * client never re-implements them.
 *
 * **What "something" is differs, and the difference matters.** The question-bank
 * import submits a one-shot token. The other two (V1.2 阶段 3 / 第 4 期) submit a
 * **batch id**: the upload already wrote this batch and its per-row detail into the
 * database, so the commit reads them back. That is what lets an operator who was
 * called away mid-flow come back and finish the very batch they were looking at,
 * instead of re-uploading and hoping it is the same file.
 *
 * Two of the three previews can come back with rows that are neither valid nor wrong:
 * they collide with what is already in the database (本月已有一次导入 / 年龄与名册不符 /
 * 学号已在名册上 / 本场已有答卷). Those rows need a decision — 覆盖 or 放弃 — before the
 * commit will go through, so `commitAssessments` / `commitStudents` read it from the
 * matching ref.
 *
 * **The MHT import additionally keeps the batch open across requests** (第 4 期).
 * `openAssessmentBatch` is what makes 「上传完被叫走了，回来接着提交」 work: it reads a
 * batch's rows back off the server and puts them back on screen. Everything the commit
 * needs lives in the batch row, not in this composable's memory — so the composable
 * holding a stale copy is never the reason a commit fails.
 */
export function useDataImport() {
  const studentPreview = ref<StudentImportPreview | null>(null)
  const questionPreview = ref<ScaleImportPreview | null>(null)
  /**
   * 当前正在处理的那一批（上传之后，或从批次历史里「继续处理」回来的那一批）。
   *
   * 与 `studentPreview` 不是一个形状：名册那一侧的预览对象**本身**带计数
   * （`valid_count` / `submittable_count`），而这一批的三个计数住在 `row_counts` 里
   * ——它是**整批**的（不套读者的数据范围），而 `assessmentTotal` 是**可见**行数。
   * 两个数各有各的口径，界面上各写各的（`batch_row_counts` 的 docstring）。
   */
  const assessmentBatch = ref<AssessmentImportBatch | null>(null)
  /** 当前批次里**读者可见**的逐行明细。提交控件旁边那三个数不从这里数出来。 */
  const assessmentRows = ref<AssessmentImportRow[]>([])
  /** 当前批次**可见**的行数（服务端说的），不是 `total_rows`（整批的）。 */
  const assessmentTotal = ref(0)
  /** 当前批次**整批**的四档计数；批次列表那一路是 `null`（那一页不算这几个数）。 */
  const assessmentRowCounts = ref<AssessmentRowCounts | null>(null)
  const assessmentRowsLoading = ref(false)
  const assessmentRowsError = ref('')
  // 整份文件共用一次处置选择（覆盖 / 放弃）：待确认的行各自属于哪一类已经在明细里写清，
  // 让老师为 200 行逐行选一次只会让他在第 20 行开始乱点
  const assessmentResolution = ref<AssessmentImportResolution | null>(null)
  /**
   * 整批的**年龄处置**（§18.5）。它与 `assessmentResolution` 回答的是**两个问题**：
   * 前者说「这一行写不写进去」，这一条说「写进去时年龄按谁记」。
   *
   * 合成一个下拉框会让「覆盖这一行、但名册上那个年龄别动」这个组合无法表达——而它正是
   * 用户 2026-09-19 裁出来的那一档（`keep_roster` 与 `session_only` 的唯一差别就是
   * 这一场记哪个数）。**不选就是老口径**（按文件里的年龄更新名册，`commit_batch` 的
   * 兜底 `AGE_RESOLUTION_OVERWRITE`），界面上把这句话写在那三个选项下面。
   */
  const assessmentAgeResolution = ref<string | null>(null)
  /**
   * **整批那一次「覆盖 / 放弃」管得着的行数**：`needing_resolution - conflict`。
   *
   * 它不是「待确认的行数」——那个数是 `needing_resolution`（屏幕上那个标题数的是它）。
   * 差额出来单算，是因为这两类待确认行的动作不一样：`AGE_CONFLICT` / `DUPLICATE` 由
   * 整批的选择回答，而 `CONFLICT` 那几条整批的选择**对它们无效**（用户裁决
   * 2026-09-19），只能逐行选四种处置之一。
   *
   * 于是这个数有两个读者，而且是**必须**用它、不能用 `needing_resolution` 的两个：
   * 那一组单选项的显示条件（只有差额 > 0 时才摆，否则是在问一个不该问的问题），
   * 以及提交按钮的硬门槛（否则一批待确认**只有**冲突行的批次，会因为操作员没在两个
   * 按屏幕上的话「管不着这些行」的选项里挑一个而永远点不亮）。它服务端算不出来吗？
   * 算得出来，但 `conflict` 那一项在 `row_counts` 里（`batch_row_counts`），这里是减法。
   */
  const assessmentBatchResolutionRows = computed(() => {
    const counts = assessmentRowCounts.value
    if (!counts) return 0
    return counts.needing_resolution - counts.conflict
  })
  // 名册导入同一形状（2026-09-17）：「学号已在名册上」以前是一句逐行错误，学校
  // 只能改文件重导；现在它是一道要拍板的选择——覆盖（更新这名学生）或放弃。
  const studentResolution = ref<StudentImportResolution | null>(null)
  const importingStudents = ref(false)
  const committingStudents = ref(false)
  const importingQuestions = ref(false)
  const creatingDraft = ref(false)
  const importingAssessments = ref(false)
  const committingAssessments = ref(false)

  async function submitStudentFile(file: File) {
    importingStudents.value = true
    // 换了一份文件，上一次的选择就不该留着：它回答的是上一份文件的冲突
    studentResolution.value = null
    // 上一次的**预览**同样不该留着（2026-09-17 补）。它带着上一份文件的批次 id，
    // 而「确认导入」交出去的就是那个 id——预览失败时把它留在屏幕上，老师看到的是
    // 「刚选的那份文件校验完了」，点下去导入的却是上一份。
    // 清空要发生在请求**之前**：失败路径与成功路径从此都只可能看见这一份文件。
    studentPreview.value = null
    try {
      studentPreview.value = await previewStudentImport(file)
      const { valid_count, conflict_count, error_count } = studentPreview.value
      if (conflict_count > 0) {
        // 与测评导入同一条理由：冲突要让老师**看见并回答**，不能混在「错误」里；
        // 颜色用 info 而不是 error——这不是「做错了」，是「需要你拍个板」。
        showToast(
          'info',
          `校验完成：${valid_count} 条可导入，${conflict_count} 条学号已在名册上，${error_count} 条错误`
        )
      } else {
        showToast(
          error_count ? 'info' : 'success',
          `校验完成：${valid_count} 条可导入，${error_count} 条错误`
        )
      }
      return studentPreview.value
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '导入预览失败')
      return null
    } finally {
      importingStudents.value = false
    }
  }

  async function commitStudents() {
    const preview = studentPreview.value
    const batchId = preview?.batch_id
    if (!batchId) {
      showToast('error', '没有可导入的记录')
      return null
    }
    if (!preview?.submittable_count) {
      // 一份全是错误的文件：后端也会拦（那一批里没有任何一行能写），但拦在提交之后，
      // 而那时屏幕上唯一的提示是一句 422。先说清楚缺的是哪一步。
      showToast('error', '这一批没有可导入的记录，请检查文件里的错误后重新上传')
      return null
    }
    if (preview?.conflict_count && !studentResolution.value) {
      // 后端也会拦（422 + 一句中文），但拦在上传之后：先说清楚缺的是哪一步
      showToast('error', '有名册冲突需要确认，请先选择覆盖或放弃')
      return null
    }
    committingStudents.value = true
    try {
      const result = await commitStudentImport(batchId, studentResolution.value ?? undefined)
      // 覆盖与跳过都要说出来。只报 created 的话，老师选完「放弃」看到
      // 「已导入 0 名」会以为整份文件白导了，而实际上错的只是那几行。
      //
      // 批次号也要说出来：名册导入是**反复发生**的动作（每学期一次普查、转学插班），
      // 而批次历史那一页上有好几行，不说是哪一批就没法回去查（审计里记的也是它）。
      const parts = [`已导入 ${result.created} 名学生（批次 ${result.batch_no}）`]
      if (result.updated) parts.push(`更新 ${result.updated} 名已有学生`)
      if (result.skipped) parts.push(`放弃 ${result.skipped} 条`)
      showToast(result.created || result.updated ? 'success' : 'info', parts.join('，'))
      studentPreview.value = null
      studentResolution.value = null
      return result
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '导入失败')
      return null
    } finally {
      committingStudents.value = false
    }
  }

  async function submitQuestionFile(file: File) {
    importingQuestions.value = true
    // 同 `submitStudentFile`：草稿是从 `questionPreview.value.preview_token` 建的，
    // 留着上一份题库的预览，就会「校验新文件失败、却用旧文件的 token 建草稿」。
    questionPreview.value = null
    try {
      questionPreview.value = await previewScaleImport(file)
      showToast(
        questionPreview.value.valid ? 'success' : 'error',
        questionPreview.value.valid ? '全部关键校验通过，可创建草稿版本' : '存在校验错误，禁止确认导入'
      )
      return questionPreview.value
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '题库预览失败')
      return null
    } finally {
      importingQuestions.value = false
    }
  }

  async function commitDraft(version: string) {
    const token = questionPreview.value?.preview_token
    if (!questionPreview.value?.valid || !token) {
      showToast('error', '存在校验错误，不能创建草稿')
      return null
    }
    creatingDraft.value = true
    try {
      const result = await createScaleDraft(token, version)
      showToast('success', `已创建题库草稿版本：${result.version}（尚未发布）`)
      questionPreview.value = null
      return result
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '创建草稿失败')
      return null
    } finally {
      creatingDraft.value = false
    }
  }

  /**
   * 上传一份外部测评记录（**同时落下这一批与逐行明细**）。
   *
   * `taskId` 决定判重口径：绑定了任务就按「这场任务里这个人有没有有效卷子」判，
   * 不绑定就按自然月。不传就是后者。
   */
  async function submitAssessmentFile(
    file: File,
    batchName: string,
    testedOn: string,
    taskId?: number | null,
    sourceSystem?: string
  ) {
    importingAssessments.value = true
    // 换了一份文件，上一次的选择就不该留着：它回答的是上一份文件的冲突
    assessmentResolution.value = null
    // 年龄处置同理——它回答的也是上一份文件的年龄冲突（而下一份文件里可能一条都没有，
    // 那三个单选项根本不会渲染出来，留着这个值就是一次看不见的越权选择）
    assessmentAgeResolution.value = null
    // 上一次的批次同理（同 `submitStudentFile`）：留着它，老师会以为屏幕上的是刚选的那份
    // 文件、那个批次、那一天，实际提交的却是上一份。清空要发生在请求**之前**，
    // 失败路径与成功路径从此都只可能看见这一份文件。
    resetAssessmentBatch()
    try {
      const batch = await previewAssessmentImport(file, batchName, testedOn, taskId, sourceSystem)
      assessmentBatch.value = batch
      // 上传那一次的响应**自带**逐行明细（这一趟数据本来就在手上，再发一次请求是白等
      // 一个往返）。它走的是与 `/rows` 同一处范围过滤，所以两个来源不可能不一样。
      assessmentRows.value = batch.rows ?? []
      assessmentTotal.value = batch.row_total ?? assessmentRows.value.length
      assessmentRowCounts.value = batch.row_counts
      const counts = batch.row_counts
      if (counts) {
        if (counts.needing_resolution > 0) {
          // 待确认要让老师**看见并回答**，不能混在「无法导入」里；颜色用 info 而不是
          // error：这不是「做错了」，是「需要你拍个板」。
          showToast(
            'info',
            `校验完成：${counts.ready} 条可导入，${counts.needing_resolution} 条需要确认，${counts.error} 条无法导入`
          )
        } else {
          showToast(
            counts.error ? 'info' : 'success',
            `校验完成：${counts.ready} 条可导入，${counts.error} 条无法导入`
          )
        }
      }
      return batch
    } catch (err) {
      // 列级问题（缺列、题号不全）从 V1.2 起是**一句 422 的中文**，不再是响应里的
      // `global_errors` 数组：整份文件没被读懂时不该建批次，而「建了一半然后靠前端
      // 拒绝提交」把一个后端能拦住的错误留在了库里的批次表中。
      showToast('error', err instanceof Error ? err.message : '导入预览失败')
      return null
    } finally {
      importingAssessments.value = false
    }
  }

  /** 把当前批次从屏幕上撤下来（换文件、提交成功之后）。三个计数一起清，不留半份。 */
  function resetAssessmentBatch() {
    assessmentBatch.value = null
    assessmentRows.value = []
    assessmentTotal.value = 0
    assessmentRowCounts.value = null
    assessmentRowsError.value = ''
  }

  /**
   * 打开某一批（批次历史里「继续处理」）：把它的逐行明细与三个计数从服务端读回来，
   * 放进当前批次的位置，于是提交控件、明细表都对着它。
   *
   * 这一条是第 4 期的核心价值——「上传完被叫走了，回来接着提交」。它读的是**服务端
   * 此刻的样子**，不是某个人上传那一刻的样子：别人在这期间补过名册的话，重新拉一次
   * 明细就是重新匹配过的事实（`list_import_rows` 现读 `match_status`）。
   *
   * **取数之前先清空**（§14）：切批次时留着上一批的行，面板上就会标题写着 B、正文是 A。
   */
  async function openAssessmentBatch(batch: AssessmentImportBatch) {
    assessmentBatch.value = batch
    assessmentRows.value = []
    assessmentTotal.value = 0
    assessmentRowCounts.value = null
    assessmentRowsError.value = ''
    // 换了批次，上一次的处置选择就不该留着——它回答的是上一批的冲突，
    // 而后端对 `resolution` 只认「有没有传」，不认它是不是为这一批选的。
    assessmentResolution.value = null
    assessmentAgeResolution.value = null
    assessmentRowsLoading.value = true
    try {
      const result = await getAssessmentImportRows(batch.id)
      assessmentRows.value = result.items
      assessmentTotal.value = result.total
      assessmentRowCounts.value = result.rowCounts
      return result
    } catch (err) {
      assessmentRowsError.value = err instanceof Error ? err.message : '明细加载失败'
      return null
    } finally {
      assessmentRowsLoading.value = false
    }
  }

  /** 明细那一条失败之后的「重试」：重开当前这一批（`assessmentBatch` 此刻就是它）。 */
  async function retryAssessmentBatch() {
    if (assessmentBatch.value) await openAssessmentBatch(assessmentBatch.value)
  }

  async function commitAssessments() {
    const batch = assessmentBatch.value
    if (!batch) {
      showToast('error', '没有可导入的记录')
      return null
    }
    const counts = assessmentRowCounts.value
    // 两份判据都说得出「缺的是哪一步」：
    //   - 一条都进不去（可导入 + 待确认 = 0）：出路在名册或任务那边，不在这个按钮上；
    //   - 有待确认的行而没选处置：后端会拦（422 + 一句中文），但那是提交之后。
    // 两个判据读的是 `row_counts`，也就是**整批**的那个数——与 `commit_batch` 拒绝提交时
    // 数的是同一个集合，所以屏幕上说的话与点下去之后发生的事不可能各说各的。
    if (counts && counts.ready + counts.needing_resolution === 0) {
      showToast('error', '这一批没有可导入的记录，请按明细里的提示补齐名册或换一份文件后重新上传')
      return null
    }
    // 第二个判据数的是**整批选择管得着的那一部分**（`needing_resolution - conflict`）：
    // 冲突行不由这两个选项回答，拿它们去拦会让一批只有冲突行的批次永远交不出去。
    // 冲突行自己那一道门在服务端（`_conflict_row_is_decided`，未处置时 422 并指名行号）
    // ——**这是有意的**，不在这里再拦一次：前端从 `row_counts` 看不出「那几行逐行处置过
    // 没有」，而拿看得见的 200 行去猜就是一条会漏的守卫（`hasVisibleAgeConflict` 那条
    // 注释记着同一个边界）。
    if (counts && counts.needing_resolution - counts.conflict > 0 && !assessmentResolution.value) {
      showToast('error', '有记录需要确认，请先选择覆盖或放弃')
      return null
    }
    committingAssessments.value = true
    try {
      const result = await commitAssessmentImport(
        batch.id,
        assessmentResolution.value ?? undefined,
        // 两个「没选」的写法不同，而它们各自都是实话：
        // `resolution` 没选时前端那道门已经拦住了（有待确认的行就必须拍板），所以到这里
        // 它一定是个码；年龄处置**可以**不选，不选就是老口径——那时这里传 `undefined`，
        // `JSON.stringify` 把这几个键整个丢掉，后端读到的就是「键不在」。
        assessmentAgeResolution.value ?? undefined
      )
      // 覆盖与跳过都要说出来：只报 created 的话，老师选完「放弃」看到「已导入 0 条」
      // 会以为整份文件白导了，而实际上错的只是那几条。
      const head = result.created
        ? `已导入 ${result.created} 条测评记录`
        : result.updated
          ? `已更新 ${result.updated} 条测评记录`
          : '没有导入任何记录'
      // 批次号是这一批唯一的名字，也印在批次历史那一页上——不说它，老师没法回去查
      // 这批数据后来怎么样了（审计里记的也是它）。任务号是另一件事，有才说。
      const parts = [`${head}（批次 ${result.batch_no}）`]
      if (result.created && result.updated) parts.push(`其中覆盖上次 ${result.updated} 条`)
      if (result.skipped) parts.push(`放弃 ${result.skipped} 条`)
      // 「处置过了、但没落成测评」要自己说出来（§10）：它与上面的「放弃」都会让
      // `created + updated + skipped` 小于总行数，而两者**原因不同**——放弃是「这一行
      // 不要了」，这一条是「这一行按学生本人答的那一份为准，外部这份只留档」。
      // 不说它，操作员会把差额记到放弃头上（或者以为有几行被吞了）。
      if (result.not_applied) {
        parts.push(`另有 ${result.not_applied} 条按处置保留系统内作答（未落成外部测评记录）`)
      }
      if (result.age_updated) parts.push(`按文件更新名册年龄 ${result.age_updated} 人`)
      if (result.withdrawn_risk_events) {
        // 「收回」要说出来：它会从工作台的待办里消失，而老师可能刚刚还在看那一条。
        // 措辞里必须带「还没人处理过」——被复核过的那几条不动（那是工作记录）。
        parts.push(`收回尚未处理的风险提示 ${result.withdrawn_risk_events} 条`)
      }
      if (result.task_no) parts.push(`已建任务 ${result.task_no}`)
      // 全部放弃不是错误（用户选了放弃，事情就办完了），但也不该报成一次「导入成功」
      showToast(result.created || result.updated ? 'success' : 'info', parts.join('，'))
      resetAssessmentBatch()
      assessmentResolution.value = null
      assessmentAgeResolution.value = null
      return result
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '导入失败')
      return null
    } finally {
      committingAssessments.value = false
    }
  }

  async function downloadAssessmentImportTemplate() {
    try {
      await downloadAssessmentTemplate()
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '模板下载失败')
    }
  }

  function downloadStudentTemplate() {
    // 性别与年龄是选填列，放在最后，四列的旧文件照样能导入。
    // 示例里写「男」而不是 MALE：中文表头配中文值，后端两种写法都认
    // （student_import_service.parse_gender），落库统一成编码。
    // 最后一列是年龄而不是出生日期（0011）：名册上只有年龄这一个数。
    // 班级用学校的编号（701 = 初一 1 班），年级与班级首位数字必须一致——
    // 两行示例跨两个年级，就是为了让这个对应关系一眼看得出来。
    downloadCsv('student-import-template.csv', [
      ['学号', '姓名', '年级', '班级', '性别', '年龄'],
      ['27025160101', '示例学生', '初一', '701', '男', '12'],
      ['27025160102', '示例学生', '初二', '801', '女', '13']
    ])
  }

  function downloadQuestionTemplate() {
    downloadCsv('mht-question-import-template.csv', [
      ['题号', '题目文本', '维度', '是否效度题', '是否重点题'],
      ['1', '示例题目', '学习焦虑', '否', '否']
    ])
  }

  function downloadStudentErrorReport() {
    if (!studentPreview.value) return
    const rows = [
      ['行号', '学号', '错误'],
      ...studentPreview.value.rows
        .filter(r => r.errors.length > 0)
        .map(r => [String(r.row_no), r.student_no, r.errors.join('；')])
    ]
    downloadCsv('student-import-errors.csv', rows)
  }

  return {
    studentPreview,
    questionPreview,
    assessmentBatch,
    assessmentRows,
    assessmentTotal,
    assessmentRowCounts,
    assessmentRowsLoading,
    assessmentRowsError,
    assessmentResolution,
    assessmentBatchResolutionRows,
    assessmentAgeResolution,
    studentResolution,
    importingStudents,
    committingStudents,
    importingQuestions,
    creatingDraft,
    importingAssessments,
    committingAssessments,
    submitStudentFile,
    commitStudents,
    submitQuestionFile,
    commitDraft,
    submitAssessmentFile,
    openAssessmentBatch,
    retryAssessmentBatch,
    resetAssessmentBatch,
    commitAssessments,
    downloadStudentTemplate,
    downloadQuestionTemplate,
    downloadAssessmentImportTemplate,
    downloadStudentErrorReport
  }
}
