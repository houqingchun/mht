<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import ScaleRulePanel from './ScaleRulePanel.vue'
import { useDataImport } from '../../composables/useDataImport'
import { getMe, getScaleVersions, publishScaleVersion, type ScaleVersion } from '../../services/api'
import { showToast } from '../../services/toast'
import { SCALE_STATUS_ORDER, scaleStatusLabel, scaleStatusTone } from '../../services/labels'
import { formatDateTime } from '../../services/dates'

const router = useRouter()
const versions = ref<ScaleVersion[]>([])
const loading = ref(true)
const error = ref('')

const columns: Column[] = [
  { key: 'version', label: '版本', sortable: true },
  { key: 'name', label: '名称', sortable: true },
  { key: 'total_questions', label: '题目总数', sortable: true, align: 'right' },
  { key: 'validity_questions', label: '效度题', sortable: true, align: 'right' },
  { key: 'key_questions', label: '重点题', sortable: true, align: 'right' },
  { key: 'rule_version', label: '规则版本' },
  { key: 'status', label: '状态', sortable: true, order: SCALE_STATUS_ORDER },
  { key: 'actions', label: '操作' }
]

// 题库导入（2026-09-17 从「系统管理」搬过来）。导入产出的是**草稿**，发布仍归
// 管理员，所以它天然属于这一页：草稿躺在下面的「全部版本」表里，和发布按钮同屏。
const {
  questionPreview,
  importingQuestions,
  creatingDraft,
  submitQuestionFile,
  commitDraft,
  downloadQuestionTemplate
} = useDataImport()

const showDraftForm = ref(false)
const draftFields: FormField[] = [
  // 不预填。原来预填的是 'MHT-1.0.1'，比线上已发布的 MHT-1.1.0 还低——管理员导入真实
  // 题库后按回车就会得到一个版本号倒退的草稿，而草稿一旦创建就带着这个号。版本号是发布
  // 节奏的一部分，让操作员自己填，占位符只提示形状。
  {
    key: 'version',
    label: '版本号',
    type: 'text',
    required: true,
    defaultValue: '',
    placeholder: '如 MHT-1.1.1（须高于当前已发布版本）'
  }
]

async function handleQuestionFile(file: File) {
  error.value = ''
  await submitQuestionFile(file)
}

async function createDraftAction() {
  if (!questionPreview.value?.valid) return
  showDraftForm.value = true
}

async function onDraftSubmit(values: Record<string, string>) {
  showDraftForm.value = false
  if (!values.version) return
  await commitDraft(values.version)
  await load()
}

const showPublish = ref(false)
const publishTarget = ref<ScaleVersion | null>(null)
const publishing = ref(false)

function askPublish(version: ScaleVersion) {
  publishTarget.value = version
  showPublish.value = true
}

/*
 * 确认弹层上那段话（V2.0.0 §5.14.6 第 5 条：危险/不可逆动作要确认，并把代价说出来）。
 *
 * **这个弹层此前不存在。** `showPublish` 只被赋值、模板里从来没有读过它，
 * `confirmPublish` 是一个完整实现却**零调用者**——于是「发布」是一枚点了没有任何
 * 反应的死按钮，而它自 V1.0.0 起就是这样（`git log -S showPublish` 只有一个提交）。
 * 量表发布是这条生命周期里唯一把草稿变成可用的动作（§7），所以它不可达等于
 * 「导入进来的题库永远用不上」。
 *
 * 话里那三句各回答一个问题：**谁能用到它**（新建任务）、**旧版本会怎样**（归档，
 * 不是删除）、**发布之后还能不能改**（不能就地改，会生成新版本）。第三句是这里
 * 唯一一句「你之后会后悔」的提示——`danger` 仍然是 false，因为发布不删任何东西
 * （§7：旧版本标 RETIRED 保留），把它涂成红色会说出一件没有发生的事。
 */
const publishMessage = computed(() => {
  const target = publishTarget.value
  if (!target) return ''
  return (
    `发布 ${target.version} 之后：新建的测评任务才能选到它，` +
    '同一量器上当前已发布的版本会被归档（保留，不删除）。' +
    '已发布的规则不能就地修改——要改会生成一个新版本。'
  )
})

async function confirmPublish() {
  if (!publishTarget.value) return
  publishing.value = true
  try {
    const result = await publishScaleVersion(publishTarget.value.id)
    showToast(
      'success',
      result.archived_versions.length
        ? `已发布 ${result.version}，旧版本 ${result.archived_versions.join('、')} 已归档`
        : `已发布 ${result.version}`
    )
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '发布失败')
  } finally {
    publishing.value = false
  }
}

const current = computed(() => versions.value.find(v => v.status === 'PUBLISHED') || versions.value[0] || null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'admin') {
      await router.push('/login')
      return
    }
    versions.value = await getScaleVersions()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">量表配置</div>
        <h1>MHT题库与规则版本</h1>
        <p class="page-desc">已发布版本不可原地修改；导入后先校验、人工确认，再发布新版本。</p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <!-- 题库导入。以前这个入口是个指回 /admin/system 的按钮，导入界面开在那边，
         这边只留一个跳转——两个页面各持一半。现在导入和它产出的草稿同屏。 -->
    <section class="card pad" style="margin-bottom:17px">
      <h2>MHT题库导入</h2>
      <p>支持 CSV/JSON。校验100题、题号完整、效度题、重点题和维度映射，确认后创建草稿版本。</p>
      <div class="import-drop">
        <strong>选择CSV或JSON题库文件</strong>
        <span class="muted tiny">导入只创建草稿版本，校验通过后仍需人工发布</span>
        <div class="actions" style="justify-content:center;margin-top:13px">
          <button class="btn" @click="downloadQuestionTemplate">下载模板</button>
          <!-- 同 `DataCenterPage.vue` 那张题库导入卡片：主操作是下面 `.import-summary`
               里那枚「创建草稿版本」（它真的写库），这一枚只是读字节，所以降级。 -->
          <label class="btn" style="cursor:pointer">
            选择文件
            <input type="file" accept=".csv,.json" hidden @change="e => { const f = (e.target as HTMLInputElement).files?.[0]; if (f) handleQuestionFile(f) }" />
          </label>
        </div>
      </div>
      <p v-if="importingQuestions" class="muted tiny" style="margin-top:12px">正在上传校验…</p>
      <div v-if="questionPreview" class="import-summary" style="margin-top: 16px">
        <strong>总数 {{ questionPreview.total }}</strong>
        <span>{{ questionPreview.valid ? '校验通过' : '校验不通过' }}</span>
        <button
          :disabled="!questionPreview.valid || !questionPreview.preview_token || creatingDraft"
          @click="createDraftAction"
        >
          {{ creatingDraft ? '正在创建…' : '创建草稿版本' }}
        </button>
      </div>
      <p v-if="questionPreview?.global_errors.length" class="form-error" style="margin-top: 8px">
        {{ questionPreview.global_errors.join('、') }}
      </p>
      <div v-if="questionPreview" class="table-wrap" style="margin-top: 12px">
        <table>
          <thead><tr><th>题号</th><th>维度</th><th>类型</th><th>重点</th><th>校验</th></tr></thead>
          <tbody>
            <tr v-for="row in questionPreview.rows.slice(0, 20)" :key="row.row_no">
              <td>{{ row.question_no }}</td>
              <td>{{ row.dimension_code || '效度题' }}</td>
              <td>{{ row.is_validity_question ? '效度' : '内容' }}</td>
              <td>{{ row.is_key_question ? '重点' : '—' }}</td>
              <td :class="row.errors.length ? 'status-bad' : 'status-ok'">
                {{ row.errors.length ? row.errors.join('、') : '通过' }}
              </td>
            </tr>
          </tbody>
        </table>
        <!-- 静默截断要自己说出来：一份 100 题的题库在这里只画 20 行，而上面写着「总数 100」——
             不写这一句，读的人会以为其余 80 题没被读到（或者根本不会去比对上下两个数）。 -->
        <p v-if="questionPreview.rows.length > 20" class="muted tiny" style="margin-top:8px">
          仅显示前 20 题，共 {{ questionPreview.rows.length }} 题。
        </p>
      </div>
    </section>

    <!-- `&& !error`：失败时 current / versions 都是空的，这块会渲染出「尚未发布量表」
         与上方的红条同屏——那是一条会让人误以为题库空的假空态。 -->
    <template v-if="!loading && !error">
      <div class="grid two">
        <div class="card pad">
          <h2>当前发布版本</h2>
          <div v-if="current" class="detail-grid" style="margin-top:13px">
            <div class="detail-row"><span>版本</span><b>{{ current.version }}</b></div>
            <div class="detail-row"><span>题目总数</span><b>{{ current.total_questions }}</b></div>
            <div class="detail-row"><span>内容题</span><b>{{ current.content_questions }}</b></div>
            <div class="detail-row"><span>效度题</span><b>{{ current.validity_questions }}</b></div>
            <div class="detail-row"><span>重点题规则</span><b>{{ current.key_questions }} 项</b></div>
            <div class="detail-row"><span>状态</span><span :class="['pill', scaleStatusTone(current.status)]">{{ scaleStatusLabel(current.status) }}</span></div>
          </div>
          <div v-else class="empty">尚未导入任何量表版本</div>
        </div>
        <!-- `tier-supporting`（§5.15 UX-FINAL-01）：这一格是**口径说明**（历史答卷
             保留计算时的题库与规则版本），不是工作区——它回答「我不必担心什么」，
             不承载任何动作，所以走 Supporting 那一层。 -->
        <div class="card pad tier-supporting">
          <h2>版本保护</h2>
          <div class="notice" style="margin-top:14px">
            历史答卷始终保留计算时的题库与规则版本，后续导入不会自动覆盖历史结果。
          </div>
          <div v-if="current" class="detail-grid" style="margin-top:13px">
            <div class="detail-row"><span>规则版本</span><b>{{ current.rule_version || '—' }}</b></div>
            <div class="detail-row"><span>量表编码</span><b>{{ current.code }}</b></div>
            <div class="detail-row"><span>发布时间</span><b>{{ formatDateTime(current.published_at) }}</b></div>
          </div>
        </div>
      </div>

      <ScaleRulePanel
        :scale-id="current?.id ?? null"
        :scale-version="current?.version ?? ''"
        style="margin-top:17px"
      />

      <!-- `tier-primary`（§5.15 UX-FINAL-01）：这一页的主工作区是「全部版本」这一张
           表——草稿躺在这里，**发布按钮也在这里**（唯一的生命周期动作）。上面那块
           题库导入产出的是草稿，是它的上游；两张只读卡是参考。 -->
      <div class="card tier-primary" style="margin-top:17px">
        <div class="card-head">
          <h2>全部版本</h2>
          <span class="muted tiny">{{ versions.length }} 个版本</span>
        </div>
        <div class="card-body">
          <DataTable
            :columns="columns"
            :rows="versions"
            row-key="id"
            :page-size="10"
            empty-text="暂无量表版本"
          >
            <template #rule_version="{ row }">{{ row.rule_version || '—' }}</template>
            <template #status="{ row }">
              <span :class="['pill', scaleStatusTone(row.status)]">{{ scaleStatusLabel(row.status) }}</span>
            </template>
            <template #actions="{ row }">
              <!-- 导入产出的是草稿，必须能发布，否则这个版本永远无法被任务使用 -->
              <button
                v-if="row.status === 'DRAFT'"
                class="btn small"
                :disabled="publishing"
                @click="askPublish(row)"
              >
                发布
              </button>
              <span v-else class="muted tiny">—</span>
            </template>
          </DataTable>
        </div>
      </div>
    </template>

    <FormDialog
      :open="showDraftForm"
      title="创建题库草稿"
      :fields="draftFields"
      submit-text="创建草稿"
      @submit="onDraftSubmit"
      @cancel="showDraftForm = false"
      @update:open="showDraftForm = $event"
    />

    <!-- `askPublish` 打开的那一层。少了它，「发布」就是一枚点了不动的按钮。 -->
    <ConfirmDialog
      :open="showPublish"
      title="发布量表版本"
      :message="publishMessage"
      confirm-text="确认发布"
      @confirm="confirmPublish"
      @cancel="showPublish = false"
      @update:open="showPublish = $event"
    />

  </div>
</template>
