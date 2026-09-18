import { ref } from 'vue'
import { showToast } from '../services/toast'
import { downloadCsv } from '../services/csv'
import {
  previewStudentImport,
  commitStudentImport,
  previewScaleImport,
  createScaleDraft,
  previewAssessmentImport,
  commitAssessmentImport,
  downloadAssessmentTemplate,
  type StudentImportPreview,
  type ScaleImportPreview,
  type AssessmentImportPreview,
  type AssessmentImportResolution,
  type StudentImportResolution
} from '../services/api'

/**
 * Shared student / question-bank / assessment-record import flow.
 *
 * All three follow the same backend contract: upload a file to a preview
 * endpoint, render the server's per-row validation, then submit the returned
 * one-shot token to commit. All validation rules live server-side — the client
 * never re-implements them.
 *
 * The assessment import differs in two ways. Two of its inputs (批次名称 / 测评日期) are
 * request fields rather than columns in the file, so they ride along with the preview and
 * are baked into the token — that is why `submitAssessmentFile` takes them as arguments
 * while the other two take only the file. And its preview can come back with rows that are
 * neither valid nor wrong: they collide with what is already in the database (本月已有一次
 * 导入 / 年龄与名册不符). Those rows need a decision — 覆盖 or 放弃 — before the commit
 * will go through, so `commitAssessments` reads it from `assessmentResolution`.
 */
export function useDataImport() {
  const studentPreview = ref<StudentImportPreview | null>(null)
  const questionPreview = ref<ScaleImportPreview | null>(null)
  const assessmentPreview = ref<AssessmentImportPreview | null>(null)
  // 整份文件共用一次处置选择（覆盖 / 放弃）：冲突行各自属于哪一类已经在明细里写清，
  // 让老师为 200 行逐行选一次只会让他在第 20 行开始乱点
  const assessmentResolution = ref<AssessmentImportResolution | null>(null)
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
    // 上一次的**预览**同样不该留着（2026-09-17 补）。它带着上一份文件的
    // `preview_token`，而「确认导入」交出去的就是那个 token——预览失败时把它留在
    // 屏幕上，老师看到的是「刚选的那份文件校验完了」，点下去导入的却是上一份。
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
    const token = preview?.preview_token
    if (!token) {
      showToast('error', '没有可导入的记录')
      return null
    }
    if (preview?.conflict_count && !studentResolution.value) {
      // 后端也会拦（422 + 一句中文），但拦在上传之后：先说清楚缺的是哪一步
      showToast('error', '有名册冲突需要确认，请先选择覆盖或放弃')
      return null
    }
    committingStudents.value = true
    try {
      const result = await commitStudentImport(token, studentResolution.value ?? undefined)
      // 覆盖与跳过都要说出来。只报 created 的话，老师选完「放弃」看到
      // 「已导入 0 名」会以为整份文件白导了，而实际上错的只是那几行。
      const parts = [`已导入 ${result.created} 名学生`]
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

  async function submitAssessmentFile(file: File, batchName: string, testedOn: string) {
    importingAssessments.value = true
    // 换了一份文件，上一次的选择就不该留着：它回答的是上一份文件的冲突
    assessmentResolution.value = null
    // 上一次的预览同理（同 `submitStudentFile`）：它带着上一份文件的令牌，而这一页的
    // 令牌还**烤进了批次名称与测评日期**——留着它，老师会以为导入的是刚选的那份文件、
    // 那个批次、那一天，实际提交的却是上一份。
    assessmentPreview.value = null
    try {
      assessmentPreview.value = await previewAssessmentImport(file, batchName, testedOn)
      const { valid_count, conflict_count, error_count, warning_count, global_errors } =
        assessmentPreview.value
      // 列级问题（缺列、题号不全）就不是「几条错误」了：整份文件都还没被读懂，
      // 所以那种情况下说「0 条可导入」会让人以为是自己填错了行。
      if (global_errors.length > 0) {
        showToast('error', global_errors[0])
      } else if (conflict_count > 0) {
        // 冲突要让老师**看见并回答**，不能混在提示条数里；颜色用 info 而不是 error：
        // 这不是「做错了」，是「需要你拍个板」。
        showToast(
          'info',
          `校验完成：${valid_count} 条可导入，${conflict_count} 条需要确认，${error_count} 条错误`
        )
      } else {
        showToast(
          error_count ? 'info' : 'success',
          `校验完成：${valid_count} 条可导入，${error_count} 条错误，${warning_count} 条提示`
        )
      }
      return assessmentPreview.value
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : '导入预览失败')
      return null
    } finally {
      importingAssessments.value = false
    }
  }

  async function commitAssessments() {
    const preview = assessmentPreview.value
    const token = preview?.preview_token
    if (!token) {
      showToast('error', '没有可导入的记录')
      return null
    }
    if (preview?.conflict_count && !assessmentResolution.value) {
      // 后端也会拦（422 + 一句中文），但拦在上传之后：先说清楚缺的是哪一步
      showToast('error', '有记录需要确认，请先选择覆盖或放弃')
      return null
    }
    committingAssessments.value = true
    try {
      const result = await commitAssessmentImport(token, assessmentResolution.value ?? undefined)
      // 覆盖与跳过都要说出来：只报 created 的话，老师选完「放弃」看到「已导入 0 条」
      // 会以为整份文件白导了，而实际上错的只是那几条。
      //
      // 一条都没写进去时 `created` 与 `updated` 都是 0，而且**没有批次任务**
      // （后端不建空批次），所以这一句不能写死「批次 X」——那会拼出「（批次 ）」。
      const head = result.created
        ? `已导入 ${result.created} 条测评记录`
        : result.updated
          ? `已更新 ${result.updated} 条测评记录`
          : '没有导入任何记录'
      const parts = [result.task_no ? `${head}（批次 ${result.task_no}）` : head]
      if (result.created && result.updated) parts.push(`其中覆盖上次 ${result.updated} 条`)
      if (result.skipped) parts.push(`放弃 ${result.skipped} 条`)
      if (result.age_updated) parts.push(`按文件更新名册年龄 ${result.age_updated} 人`)
      // 全部放弃不是错误（用户选了放弃，事情就办完了），但也不该报成一次「导入成功」
      showToast(result.created || result.updated ? 'success' : 'info', parts.join('，'))
      assessmentPreview.value = null
      assessmentResolution.value = null
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
    assessmentPreview,
    assessmentResolution,
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
    commitAssessments,
    downloadStudentTemplate,
    downloadQuestionTemplate,
    downloadAssessmentImportTemplate,
    downloadStudentErrorReport
  }
}
