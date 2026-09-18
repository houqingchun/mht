<!--
  数据中心：心理老师的导入台（题库导入 / MHT测评记录导入）与近期数据任务。

  **学生信息导入不在这里**（2026-09-17 删）。它曾在这页有一份 `v-if="isAdmin"`
  的副本，而这条路由的 `meta.role` 是 `counselor`、`AppLayout` 会把管理员弹回
  登录页，所以那份副本对谁都不显示——名册的导入只有「组织学生」一个入口。
  删的是副本，功能一直都在。
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import { useDataImport } from '../../composables/useDataImport'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import { showToast } from '../../services/toast'
import { useSettings } from '../../composables/useSettings'
import { importConflictLabel } from '../../services/labels'
import {
  getMe,
  getAuditLogs,
  exportHighRiskCareCases,
  type AuditLogItem
} from '../../services/api'

const router = useRouter()
const { settings } = useSettings()

const {
  questionPreview,
  assessmentPreview,
  assessmentResolution,
  importingQuestions,
  creatingDraft,
  importingAssessments,
  committingAssessments,
  submitQuestionFile,
  commitDraft,
  submitAssessmentFile,
  commitAssessments,
  downloadQuestionTemplate,
  downloadAssessmentImportTemplate
} = useDataImport()

const audits = ref<AuditLogItem[]>([])
const loading = ref(true)
const error = ref('')
const showExportLog = ref(false)
const showAssessmentRows = ref(false)
const selectedExportLog = ref<AuditLogItem | null>(null)

// 错误、提示、待确认在同一张明细里：老师要看的是「这一行为什么这样」，而不是分类。
// 待确认那一类尤其要知道是哪几行、对不上的是什么数。
const assessmentIssueRows = computed(() =>
  (assessmentPreview.value?.rows || []).filter(
    r => r.errors.length > 0 || r.warnings.length > 0 || r.conflicts.length > 0
  )
)
// 有冲突就必须先回答「覆盖还是放弃」：按钮不置灰的话，点下去只会拿回一句 422
const needsAssessmentResolution = computed(
  () => Boolean(assessmentPreview.value?.conflict_count) && !assessmentResolution.value
)

/** Recent data jobs are derived from the audit trail rather than a separate store. */
const recentJobs = computed(() =>
  audits.value
    .filter(a => ['导入学生', '导入题库草稿版本', '导入测评记录', '导出关注档案摘要', '导出高度关注摘要', '导出单个学生摘要'].includes(a.action))
    .slice(0, 10)
)

const IMPORT_ACTIONS = ['导入学生', '导入题库草稿版本', '导入测评记录']

function jobResult(job: AuditLogItem) {
  return job.action.startsWith('导入') ? '已写入' : '已脱敏，已审计'
}

function jobTone(job: AuditLogItem) {
  return IMPORT_ACTIONS.includes(job.action) ? 'green' : 'blue'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    // 这一页只归心理老师：路由的 `meta.role` 就是 counselor，`AppLayout` 会把
    // 别人弹回登录页，所以这里列 `['admin','counselor']` 是自欺——
    // 管理员永远到不了这一行（2026-09-17 实测）。
    if (me.role_code !== 'counselor') {
      await router.push('/login')
      return
    }
    audits.value = (await getAuditLogs({ limit: 100 })).items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function onQuestionFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  await submitQuestionFile(file)
}

// --- MHT测评记录导入（外部平台） ---
// 批次名称与测评日期是**必填的请求字段**，不在文件里：一场普查叫什么、是哪天做的，
// 只有操作员知道。日期默认今天（跑得最多的就是「今天导昨天的那场」），
// 批次名称在选好文件后按文件名预填，可改。
const assessmentBatchName = ref('')
const assessmentTestedOn = ref(todayIso())

function todayIso() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

function fileNameWithoutExtension(name: string) {
  const dot = name.lastIndexOf('.')
  return (dot > 0 ? name.slice(0, dot) : name).slice(0, 128)
}

async function onAssessmentFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!assessmentBatchName.value.trim()) assessmentBatchName.value = fileNameWithoutExtension(file.name)
  if (!assessmentBatchName.value.trim() || !assessmentTestedOn.value) {
    // 后端会拦，但拦在文件上传之后：先说清楚缺哪一项，别让老师白传一次
    showToast('error', '请先填写批次名称与测评日期')
    return
  }
  await submitAssessmentFile(file, assessmentBatchName.value.trim(), assessmentTestedOn.value)
}

async function commitAssessmentsNow() {
  await commitAssessments()
  await load()
}

async function commitDraftNow() {
  const values = await askDraftVersion()
  if (!values.version) return
  await commitDraft(values.version)
}

// --- 题库草稿：版本号必填且不预填 ---
// 这里原来是 `commitDraft('MHT-1.0.1')`——点一下就建出一个版本号的草稿，中间没有
// 任何一次确认，而那个号比线上已发布的 MHT-1.1.0 还低。版本号是发布节奏的一部分，
// 由操作员填；占位符只提示形状，不给出一个可以用默认值带过的具体号。
const showDraftVersionForm = ref(false)
const draftVersionFields = ref<FormField[]>([])
let draftResolve: ((values: Record<string, string>) => void) | null = null

function askDraftVersion(): Promise<Record<string, string>> {
  draftVersionFields.value = [
    {
      key: 'version',
      label: '版本号',
      type: 'text',
      required: true,
      defaultValue: '',
      placeholder: '如 MHT-1.1.1（须高于当前已发布版本）'
    }
  ]
  showDraftVersionForm.value = true
  return new Promise((resolve) => { draftResolve = resolve })
}

function onDraftVersionSubmit(values: Record<string, string>) {
  if (draftResolve) draftResolve(values)
}

function onDraftVersionCancel() {
  if (draftResolve) draftResolve({})
}

function openExportLog(job: AuditLogItem) {
  selectedExportLog.value = job
  showExportLog.value = true
}

// --- 受控导出：用途必填，落审计后由后端生成 CSV ---
const showExportForm = ref(false)
const exportFormFields = ref<FormField[]>([])
let exportResolve: ((values: Record<string, string>) => void) | null = null

function askExportOptions(title: string): Promise<Record<string, string>> {
  exportFormFields.value = [
    {
      key: 'purpose',
      label: '导出用途',
      type: 'select',
      required: true,
      placeholder: '请选择',
      // 导出用途由系统配置提供（此处是第四份重复副本，此前需要改四处）
      options: settings.value.export.purposes.map(item => ({ value: item, label: item }))
    }
  ]
  showExportForm.value = true
  void title
  return new Promise((resolve) => { exportResolve = resolve })
}

function onExportSubmit(values: Record<string, string>) {
  if (exportResolve) exportResolve(values)
}

function onExportCancel() {
  if (exportResolve) exportResolve({})
}

async function exportHighRisk() {
  const values = await askExportOptions('高度关注导出')
  if (!values.purpose) return
  try {
    await exportHighRiskCareCases({ purpose: values.purpose, maskNames: true })
    showToast('success', '受控导出已完成并记录审计')
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">批量数据作业</div>
        <h1>数据中心</h1>
        <p class="page-desc">导入先校验预览再确认；敏感导出需说明用途、限定字段并自动审计。</p>
      </div>
      <div class="actions">
        <!-- 「高度关注」就是等级里的「重点关注」（同一个 `KEY_ATTENTION`）。名字不动的
             理由见 CLAUDE.md §3 第四面那条注：审计动作码已经落了几百行，改界面上的
             名字会让用户在审计页按眼睛看到的名字搜不到。 -->
        <button class="btn primary" @click="exportHighRisk">高度关注导出</button>
      </div>
    </div>

    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <div class="grid two">
      <div class="card pad">
        <h2>MHT题库版本导入</h2>
        <p class="muted tiny" style="margin-top:7px">校验100题、10道效度题、维度映射、重点题与重复题号</p>
        <p class="muted tiny" style="margin-top:4px">
          导入只创建草稿版本，草稿不生效；需由系统管理员发布后才会用于新的测评任务。
        </p>
        <div class="import-drop">
          <strong>导入形成新的草稿版本</strong>
          <span class="muted tiny">不会覆盖已发布题库或历史测评结果</span>
          <div class="actions" style="justify-content:center;margin-top:14px">
            <button class="btn" @click="downloadQuestionTemplate">下载模板</button>
            <label class="btn primary" style="cursor:pointer">
              选择文件
              <input type="file" accept=".csv,.json" hidden @change="onQuestionFile" />
            </label>
          </div>
        </div>

        <p v-if="importingQuestions" class="muted tiny" style="margin-top:12px">正在上传校验…</p>
        <div v-if="questionPreview" class="import-summary" style="margin-top:14px">
          <strong>总数 {{ questionPreview.total }}</strong>
          <span>{{ questionPreview.valid ? '校验通过' : '校验不通过' }}</span>
          <button
            :disabled="!questionPreview.valid || !questionPreview.preview_token || creatingDraft"
            @click="commitDraftNow"
          >
            {{ creatingDraft ? '正在创建…' : '创建草稿版本' }}
          </button>
        </div>
        <p v-if="questionPreview?.global_errors.length" class="form-error" style="margin-top:8px">
          {{ questionPreview.global_errors.join('、') }}
        </p>
      </div>

      <div class="card pad">
        <h2>MHT测评记录导入</h2>
        <!-- 三句取值约定必须写在页面上：1/2 与 1/0 是**那个外部平台**的约定，
             本系统其它任何地方都不出现这两种写法，老师无从推断。
             性别那一句 2026-09-17 跟着后端一起改成了 2=男、1=女——此前反着写，
             照着这段说明填的表会把同名同班的两个学生定位反。 -->
        <p class="muted tiny" style="margin-top:7px">
          CSV · 导入在其他平台完成的普查结果，按姓名、性别、年龄、年级、班级定位学生
        </p>
        <p class="muted tiny" style="margin-top:4px">
          性别 2=男、1=女；答案 1=是、0=否；年级 1/2/3 为初一/初二/初三，班级 4 表示初一 4 班。
          定位不到的学生会跳过并逐行报错，不建学生、不建账号。
        </p>
        <!-- 这里不再写「不改名册」：年龄不符时「覆盖」的含义就是改名册上的年龄。
             那句话与下面的待确认面板自相矛盾，而面板才是有选项的那一个。 -->
        <p class="muted tiny" style="margin-top:4px">
          年龄与名册不符、或本月已导过一次的记录会先列出来，由你选择覆盖还是放弃。
        </p>
        <div class="batch-fields">
          <label>
            <span class="muted tiny">批次名称</span>
            <input v-model="assessmentBatchName" type="text" maxlength="128" placeholder="如 2026年秋季心理普查" />
          </label>
          <label>
            <span class="muted tiny">测评日期</span>
            <input v-model="assessmentTestedOn" type="date" />
          </label>
        </div>
        <div class="import-drop">
          <strong>导入前显示逐项校验结果</strong>
          <!-- 这里此前写着「外部记录不产生风险提示与关怀档案，只带进评分结果」——
               那是 2026-09-16 的旧口径，2026-09-17 已被用户撤回：导入的记录与学生在
               系统内作答走同一个判定函数，命中重点题 85/97 一样开出待办与档案。
               页面文案没跟着改，就成了一句对着用户说的假话。 -->
          <span class="muted tiny">判定口径与系统内作答一致：命中重点题（85/97）会开出风险提示与关怀档案</span>
          <div class="actions" style="justify-content:center;margin-top:14px">
            <button class="btn" @click="downloadAssessmentImportTemplate">下载模板</button>
            <label class="btn primary" style="cursor:pointer">
              选择文件
              <input type="file" accept=".csv" hidden @change="onAssessmentFile" />
            </label>
          </div>
        </div>

        <p v-if="importingAssessments" class="muted tiny" style="margin-top:12px">正在上传校验…</p>
        <p v-if="assessmentPreview?.global_errors.length" class="form-error" style="margin-top:8px">
          {{ assessmentPreview.global_errors.join('、') }}
        </p>
        <div v-if="assessmentPreview && !assessmentPreview.global_errors.length" class="import-summary" style="margin-top:14px">
          <strong>总数 {{ assessmentPreview.total }}</strong>
          <span>可导入 {{ assessmentPreview.valid_count }}</span>
          <span v-if="assessmentPreview.conflict_count" class="status-warn">
            待确认 {{ assessmentPreview.conflict_count }}
          </span>
          <span>错误 {{ assessmentPreview.error_count }}</span>
          <span>提示 {{ assessmentPreview.warning_count }}</span>
          <button
            :disabled="!assessmentPreview.preview_token || committingAssessments || needsAssessmentResolution"
            @click="commitAssessmentsNow"
          >
            {{ committingAssessments ? '正在导入…' : '确认导入' }}
          </button>
          <button v-if="assessmentIssueRows.length" class="btn small" @click="showAssessmentRows = true">
            查看明细
          </button>
        </div>

        <!-- 待确认项：**必须由操作员回答**，所以它不是一句提示，而是两行单选项加一句
             「各是什么意思」。这里的措辞要能让人不点「查看明细」也知道自己在选什么。 -->
        <div
          v-if="assessmentPreview?.conflict_count"
          class="import-conflicts"
          style="margin-top:12px"
        >
          <strong>有 {{ assessmentPreview.conflict_count }} 条记录需要确认</strong>
          <p class="muted tiny" style="margin-top:4px">
            这些记录本身没有问题，问题是它们与库里已有的数据对不上：年龄与名册不符，
            或同一名学生在同一个月里已有一次导入。其余记录无论怎么选都会照常导入。
          </p>
          <label class="conflict-option">
            <input v-model="assessmentResolution" type="radio" value="overwrite" />
            <span>
              <b>覆盖</b> —— 重复的用这份文件里的结果替换上次导入的那一份；
              年龄按文件里的数更新名册（会记一条审计）
            </span>
          </label>
          <label class="conflict-option">
            <input v-model="assessmentResolution" type="radio" value="skip" />
            <span><b>放弃这几条</b> —— 只导入其余记录，库里已有的记录与名册都不动</span>
          </label>
          <p v-if="!assessmentResolution" class="form-error" style="margin-top:6px">
            请先选择覆盖或放弃，再点「确认导入」
          </p>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top:17px">
      <div class="card-head">
        <h2>近期数据任务</h2>
        <span class="muted tiny">来自审计日志</span>
      </div>
      <div class="card-body">
        <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
        <!-- `!error` 必须在 `v-else` 之前判：`recentJobs` 由审计列表派生，拉不到审计
             时它是空的，这张表会写出「暂无数据作业记录」。 -->
        <ErrorState v-else-if="error" :message="error" :on-retry="load" />
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr><th>时间</th><th>任务</th><th>结果</th><th>角色</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="job in recentJobs" :key="job.id">
                <td class="nowrap">{{ job.created_at || '—' }}</td>
                <td>{{ job.action }}</td>
                <td><span :class="['pill', jobTone(job)]">{{ jobResult(job) }}</span></td>
                <td>{{ job.actor_role || '—' }}</td>
                <td>
                  <button
                    v-if="job.action.startsWith('导出')"
                    class="btn small"
                    @click="openExportLog(job)"
                  >
                    查看记录
                  </button>
                  <!-- 这里原来直接打印 `job.resource_type`（`STUDENT` / `ASSESSMENT_TASK` /
                       `EXPORT`…）。那是后端编码，§3 不允许出现在界面上，只是要等到库里真有一条
                       「导入学生」的审计行，它才会显形——`e2e/vocabulary.spec.ts` 就是这么抓到的。
                       这一格对导入行本来也不承载信息：导的是什么已经写在「任务」列里了；
                       导出行有「查看记录」。所以不是给它配一份映射，而是不再打印编码。
                       （`AuditPage.vue` 那一列的裸编码是已知缺口 7，三件事要一起做，不在这里顺带改。） -->
                  <span v-else class="muted tiny">—</span>
                </td>
              </tr>
              <tr v-if="!recentJobs.length">
                <td colspan="5"><div class="empty">暂无数据作业记录</div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <Modal
      :model-value="showAssessmentRows"
      title="导入校验明细"
      size="lg"
      @update:model-value="showAssessmentRows = $event"
    >
      <div v-if="assessmentPreview" class="detail-grid">
        <div class="detail-row"><span>批次名称</span><b>{{ assessmentPreview.batch.name }}</b></div>
        <div class="detail-row"><span>测评日期</span><b>{{ assessmentPreview.batch.tested_on }}</b></div>
        <div class="detail-row"><span>可导入</span><b class="status-ok">{{ assessmentPreview.valid_count }}</b></div>
        <div class="detail-row">
          <span>待确认</span><b :class="assessmentPreview.conflict_count ? 'status-warn' : ''">{{ assessmentPreview.conflict_count }}</b>
        </div>
        <div class="detail-row"><span>错误/跳过</span><b class="status-bad">{{ assessmentPreview.error_count }}</b></div>
      </div>
      <div class="table-wrap" style="margin-top:14px;max-height:300px">
        <table>
          <thead>
            <tr><th>行号</th><th>姓名</th><th>年级班级</th><th>匹配学号</th><th>错误</th><th>待确认</th><th>提示</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in assessmentIssueRows" :key="row.row_no">
              <td>{{ row.row_no }}</td>
              <td>{{ row.name }}</td>
              <td>{{ row.grade }} {{ row.class_name }}</td>
              <td>{{ row.matched_student_no || '—' }}</td>
              <td class="status-bad">{{ row.errors.join('；') }}</td>
              <!-- 走 `importConflictLabel` 而不是打印 `conflict.type`：`AGE_MISMATCH`
                   这种码出现在界面上就是漏了一次翻译（§3，`e2e/vocabulary.spec.ts` 盯着）。 -->
              <td class="status-warn">
                {{ row.conflicts.map(c => importConflictLabel(c.type)).join('；') }}
              </td>
              <td class="muted">{{ row.warnings.join('；') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="assessmentPreview?.conflict_count" class="muted tiny" style="margin-top:10px">
        待确认的具体数字：「年龄与名册不符」是文件里的年龄与名册上那个数对不上，
        「本月已有一次导入」是同一名学生在同一个月里已经导过一次。
      </p>
      <template #footer>
        <button class="btn primary" @click="showAssessmentRows = false">关闭</button>
      </template>
    </Modal>

    <FormDialog
      :open="showExportForm"
      title="高度关注导出"
      :fields="exportFormFields"
      submit-text="确认导出"
      @submit="onExportSubmit"
      @cancel="onExportCancel"
      @update:open="showExportForm = $event"
    />

    <FormDialog
      :open="showDraftVersionForm"
      title="创建题库草稿"
      :fields="draftVersionFields"
      submit-text="创建草稿"
      @submit="onDraftVersionSubmit"
      @cancel="onDraftVersionCancel"
      @update:open="showDraftVersionForm = $event"
    />

    <Modal :model-value="showExportLog" title="导出记录" @update:model-value="showExportLog = $event">
      <div v-if="selectedExportLog" class="detail-grid">
        <div class="detail-row"><span>导出行为</span><b>{{ selectedExportLog.action }}</b></div>
        <div class="detail-row"><span>导出用途</span><b>{{ selectedExportLog.purpose || '—' }}</b></div>
        <div class="detail-row"><span>数据范围</span><b>{{ selectedExportLog.resource_id || '全部档案' }}</b></div>
        <div class="detail-row"><span>角色</span><b>{{ selectedExportLog.actor_role || '—' }}</b></div>
        <div class="detail-row"><span>时间</span><b>{{ selectedExportLog.created_at || '—' }}</b></div>
      </div>
      <template #footer>
        <button class="btn primary" @click="showExportLog = false">关闭</button>
      </template>
    </Modal>

  </div>
</template>
