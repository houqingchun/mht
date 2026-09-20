<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import Modal from '../../components/Modal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { useDataImport } from '../../composables/useDataImport'
import {
  getMe,
  getStudentRosterBatchRows,
  getStudentRosterBatches,
  getStudents,
  type RosterImportBatch,
  type RosterImportBatchRow,
  type StudentItem
} from '../../services/api'
import {
  STUDENT_STATUS_ORDER,
  ageLabel,
  genderLabel,
  importBatchStatusLabel,
  importBatchStatusTone,
  rosterConflictLabel,
  importRowStatusLabel,
  importRowStatusTone,
  studentStatusLabel,
  studentStatusTone
} from '../../services/labels'

/**
 * 组织与学生账号 —— 名册的**唯一**入口（2026-09-17 合并）。
 *
 * 在此之前，同一张学生表在 `/admin/system`（系统管理）里也渲染了一遍，导入入口
 * 还开在那个页面上，而那个页面又反过来在按钮上写着「导入学生」指回来。两个页面
 * 各有一半，谁都不是完整的。名册归名册，导入跟着它走。
 *
 * 管理员在这里只看得到身份与组织字段：测评分数和心理工作正文不在这条链路上
 * （后端按能力矩阵把「学生心理详情」卡在 NONE），不是靠这个页面少画几列。
 */
const router = useRouter()
const students = ref<StudentItem[]>([])
const loading = ref(true)
const error = ref('')

const columns: Column[] = [
  { key: 'name', label: '姓名', sortable: true },
  { key: 'student_no', label: '学号', sortable: true },
  { key: 'grade', label: '年级班级', sortable: true },
  // 性别需要显式插槽：DataTable 的默认插槽直接打印 row[key]，那会把 MALE 显示成
  // 原始编码——词汇契约（CLAUDE.md §3）漏的正是这一面。年龄是数字，默认插槽
  // 的 `?? '—'` 已经正确。
  { key: 'gender', label: '性别' },
  { key: 'age', label: '年龄' },
  { key: 'status', label: '状态', sortable: true, order: STUDENT_STATUS_ORDER }
]

// 导入流程的校验规则全在后端，这里只做交互编排。
const {
  studentPreview,
  studentResolution,
  importingStudents,
  committingStudents,
  submitStudentFile,
  commitStudents,
  downloadStudentTemplate,
  downloadStudentErrorReport
} = useDataImport()

const showImportErrors = ref(false)
const showConfirm = ref(false)

/**
 * 导入批次历史（V1.2 阶段 3）。
 *
 * 在此之前名册导入是一次**无痕动作**：导完之后库里只有一条审计，而「这次导的是哪份
 * 文件、有几行没落上、为什么没落上」在界面上没有任何落点。批次表与逐行表就是那个落点，
 * 不给它们读者等于没落库。
 *
 * 三态分开是 §14 那条约定：`batchesError` 排在空态**之前**——一次读取失败时落下
 * 「暂无导入批次」，会让操作员以为自己从没导过，而真相是这一页没读到。
 */
const batches = ref<RosterImportBatch[]>([])
const batchesTotal = ref(0)
const batchesLoading = ref(true)
const batchesError = ref('')

const showBatchDetail = ref(false)
const batchDetail = ref<RosterImportBatch | null>(null)
const batchRows = ref<RosterImportBatchRow[]>([])
const batchRowsLoading = ref(false)
const batchRowsError = ref('')
const importErrorRows = computed(() => (studentPreview.value?.rows || []).filter(r => r.errors.length > 0))
// 待确认的行要单独列出来：`errors` 是空的，冲突在 `conflicts` 里，只看 `errors`
// 的话这一行在明细里长得跟「没问题」一模一样。
//
// 这块面板**只有这一处**（2026-09-17 核实）。「数据中心」（`/counselor/data`）里
// 曾有一份副本，但那份是 `v-if="isAdmin"` —— 而那条路由的 `meta.role` 是
// `counselor`，`AppLayout` 会把管理员弹回登录页，所以它恒不可达，已删除。
// 名字册的落点就是这一页。
const studentConflictRows = computed(() =>
  (studentPreview.value?.rows || []).filter(r => r.conflicts.length > 0)
)
const studentIssueRows = computed(() =>
  (studentPreview.value?.rows || []).filter(r => r.errors.length > 0 || r.conflicts.length > 0)
)
const needsStudentResolution = computed(
  () => Boolean(studentPreview.value?.conflict_count) && !studentResolution.value
)

/**
 * 确认框上的人数。选了「覆盖」时冲突行也要写进去，所以报 `valid_count` 会少算——
 * 说的是「将导入 5 名」，实际写 8 名。选「放弃」时它才是准的。
 *
 * 「将导入 0 名学生」要单独说：一份只含冲突行的文件选「放弃」时它才是实情，
 * 而那句话读起来像是在报错。这时说清「不会有任何改动」，操作员才知道
 * 点下去是记一笔、不是导一笔。
 */
const confirmMessage = computed(() => {
  const preview = studentPreview.value
  const ready = preview?.valid_count ?? 0
  const conflicts = preview?.conflict_count ?? 0
  const total = studentResolution.value === 'overwrite' ? ready + conflicts : ready
  if (total === 0) {
    return `这份文件里没有要新增的学生，${conflicts} 条冲突记录按「放弃」保持原样，不会改动任何数据。确认继续？`
  }
  const tail = studentResolution.value === 'skip' ? '，冲突的几条不动' : ''
  return `将导入 ${total} 名学生${tail}。确认继续？`
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'admin') {
      await router.push('/login')
      return
    }
    students.value = await getStudents()
    await loadBatches()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadBatches() {
  batchesLoading.value = true
  batchesError.value = ''
  try {
    const page = await getStudentRosterBatches()
    batches.value = page.items
    batchesTotal.value = page.total
  } catch (err) {
    // 读不到批次历史**不能**让整页变成错误页：名册与学生列表在同一个屏幕上，
    // 而它们的接口是另一个。所以这一处的失败只落在这一块卡片里。
    batchesError.value = err instanceof Error ? err.message : '批次历史加载失败'
  } finally {
    batchesLoading.value = false
  }
}

/**
 * 打开某一批的逐行明细。
 *
 * **取数之前先清空**（§14）：换一批时留着上一批的行，面板上就会标题写着 B、正文是 A。
 * 明细在弹层里，而弹层是同一个（`showBatchDetail`）复用的，切批次不重建组件。
 */
async function openBatchDetail(batch: RosterImportBatch) {
  batchDetail.value = batch
  batchRows.value = []
  batchRowsError.value = ''
  showBatchDetail.value = true
  batchRowsLoading.value = true
  try {
    batchRows.value = await getStudentRosterBatchRows(batch.id)
  } catch (err) {
    batchRowsError.value = err instanceof Error ? err.message : '明细加载失败'
  } finally {
    batchRowsLoading.value = false
  }
}

/** 明细那一条失败之后的「重试」：重开当前这一批（`batchDetail` 此刻就是它）。 */
async function retryBatchDetail() {
  if (batchDetail.value) await openBatchDetail(batchDetail.value)
}

async function handleStudentFile(file: File) {
  error.value = ''
  await submitStudentFile(file)
}

async function confirmImport() {
  showConfirm.value = false
  const result = await commitStudents()
  await load()
  // 只有真的写进去了才重新读批次历史：一次失败的提交没有改变任何东西，
  // 重读只会让屏幕上那一行闪一下。
  if (result) await loadBatches()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">基础数据</div>
        <h1>组织与学生账号</h1>
        <p class="page-desc">系统管理员只能管理身份与组织字段，不展示测评分数和心理工作正文。</p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="table" :rows="5" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <template v-if="!loading && !error">
      <!-- 学生导入 -->
      <section class="card pad">
        <h2>学生导入</h2>
        <p>
          支持 CSV/JSON。选择文件后只做校验预览，确认后才写入数据库。
          性别与年龄选填。
          班级按 701 / 801 / 901 编号，首位 7 / 8 / 9 分别是初一 / 初二 / 初三，须与「年级」列一致。
        </p>
        <div class="import-drop">
          <strong>选择CSV或JSON文件</strong>
          <span class="muted tiny">先校验并预览，不会立即写入数据</span>
          <div class="actions" style="justify-content:center;margin-top:13px">
            <button class="btn" @click="downloadStudentTemplate">下载模板</button>
            <label class="btn primary" style="cursor:pointer">
              选择文件
              <input type="file" accept=".csv,.json" hidden @change="e => { const f = (e.target as HTMLInputElement).files?.[0]; if (f) handleStudentFile(f) }" />
            </label>
          </div>
        </div>
        <p v-if="importingStudents" class="muted tiny" style="margin-top:12px">正在上传校验…</p>
        <div v-if="studentPreview" class="import-summary" style="margin-top: 16px">
          <!-- 批次号要在**预览**这一屏上就出现：这一批此刻已经在库里了（预览是一次写），
               下面的「导入批次」那张表里已经有它一行。不说出来的话，操作员点完确认
               再回头看那张表，分不清哪一行是刚才这一批。 -->
          <span class="muted tiny">批次 {{ studentPreview.batch_no }}</span>
          <strong>总数 {{ studentPreview.total }}</strong>
          <span>可导入 {{ studentPreview.valid_count }}</span>
          <span v-if="studentPreview.conflict_count" class="status-warn">
            待确认 {{ studentPreview.conflict_count }}
          </span>
          <span>错误 {{ studentPreview.error_count }}</span>
          <!-- 判据是服务端算的 `submittable_count`（这一批有没有东西可提交），不是
               `valid_count`：选「覆盖」时冲突行也要写进去，按 `valid_count` 判会把一份
               只有冲突行的文件判成「没东西可导」。前后端各算一次就会漂，所以这里只用
               服务端发下来的那一个数。 -->
          <button
            :disabled="!studentPreview.submittable_count || committingStudents || needsStudentResolution"
            @click="showConfirm = true"
          >
            {{ committingStudents ? '正在导入…' : '确认导入' }}
          </button>
          <button v-if="studentIssueRows.length" @click="showImportErrors = true">查看明细</button>
        </div>

        <!-- 学号已在名册上的行要操作员拍板（与数据中心同一形状、同一套后端码）。 -->
        <div v-if="studentPreview?.conflict_count" class="import-conflicts" style="margin-top:12px">
          <strong>有 {{ studentPreview.conflict_count }} 条记录的学号已在名册上</strong>
          <p class="muted tiny" style="margin-top:4px">
            这些行的内容本身没问题。覆盖是<b>更新名册上的那名学生</b>，不删除、不重建：
            他的关怀记录、测评历史与账号都保留，只是姓名、年级、班级、性别、年龄换成文件里的。
          </p>
          <label class="conflict-option">
            <input v-model="studentResolution" type="radio" value="overwrite" />
            <span><b>覆盖</b> —— 用这份文件里的信息更新这几名学生</span>
          </label>
          <label class="conflict-option">
            <input v-model="studentResolution" type="radio" value="skip" />
            <span><b>放弃</b> —— 这几名学生这次不动，其余记录照常导入</span>
          </label>
          <p v-if="!studentResolution" class="form-error" style="margin-top:6px">
            请先选择覆盖或放弃，再点「确认导入」
          </p>
          <ul v-if="studentConflictRows.length" class="conflict-list">
            <li v-for="row in studentConflictRows.slice(0, 5)" :key="row.row_no">
              第 {{ row.row_no }} 行 · 学号 {{ row.student_no }} ——
              名册上是「{{ row.conflicts[0]?.existing_name }}」<template
                v-if="row.name && row.name !== row.conflicts[0]?.existing_name"
                >，本行要写成「{{ row.name }}」</template
              >
            </li>
          </ul>
        </div>
        <div v-if="studentPreview" class="table-wrap" style="margin-top: 12px">
          <table>
            <thead><tr><th>行</th><th>学号</th><th>姓名</th><th>年级班级</th><th>校验</th></tr></thead>
            <tbody>
              <tr v-for="row in studentPreview.rows.slice(0, 20)" :key="row.row_no">
                <td>{{ row.row_no }}</td>
                <td>{{ row.student_no || '—' }}</td>
                <td>{{ row.name || '—' }}</td>
                <td>{{ row.grade }}{{ row.class_name }}</td>
                <!-- 待确认的行 `errors` 是空的，此前它在这一列显示「通过」——
                     而上面的汇总同时写着「待确认 N」。两句话在同一个屏幕上打架。 -->
                <td
                  :class="
                    row.errors.length ? 'status-bad' : row.conflicts.length ? 'status-warn' : 'status-ok'
                  "
                >
                  {{
                    row.errors.length
                      ? row.errors.join('、')
                      : row.conflicts.length
                        ? row.conflicts.map(c => c.message).join('、')
                        : '通过'
                  }}
                </td>
              </tr>
            </tbody>
          </table>
          <!-- 静默截断要自己说出来：学校导 200 行时，看到 20 行会以为其余 180 行没被读到。 -->
          <p v-if="studentPreview.rows.length > 20" class="muted tiny" style="margin-top:8px">
            仅显示前 20 行，共 {{ studentPreview.rows.length }} 行。
          </p>
        </div>
      </section>

      <!-- 学生列表。名字里带「学号」的表就是这一张，导入预览表在上方、不算。 -->
      <section class="card" style="margin-top:17px">
        <div class="card-head">
          <h2>学生列表</h2>
          <span class="muted tiny">{{ students.length }} 名学生</span>
        </div>
        <div class="card-body">
          <DataTable
            :columns="columns"
            :rows="students"
            row-key="id"
            :page-size="10"
            empty-text="暂无学生"
          >
            <template #grade="{ row }">{{ row.grade }}{{ row.class_name }}</template>
            <template #gender="{ row }">{{ genderLabel(row.gender) }}</template>
            <template #status="{ row }">
              <span :class="['pill', studentStatusTone(row.status)]">{{
                studentStatusLabel(row.status)
              }}</span>
            </template>
          </DataTable>
        </div>
      </section>

      <!-- 导入批次历史。放在学生列表之后：它是**回头看**的东西，不是这一页的主任务。 -->
      <section class="card" style="margin-top:17px">
        <div class="card-head">
          <h2>导入批次</h2>
          <span class="muted tiny">
            最近 {{ batches.length }} 批<template v-if="batchesTotal > batches.length"
              >，共 {{ batchesTotal }} 批</template
            >
          </span>
        </div>
        <div class="card-body">
          <!-- 三态分开（§14）：错误分支排在空态**之前**——一次读取失败时落下
               「暂无导入批次」，会让操作员以为自己从没导过。 -->
          <SkeletonBlock v-if="batchesLoading" variant="table" :rows="3" />
          <ErrorState v-else-if="batchesError" :message="batchesError" :on-retry="loadBatches" />
          <p v-else-if="!batches.length" class="muted tiny">暂无导入批次</p>
          <div v-else class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>批次</th><th>文件</th><th>状态</th><th>总数</th>
                  <th>新增</th><th>更新</th><th>放弃</th><th>错误</th><th>导入时间</th><th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="batch in batches" :key="batch.id">
                  <td>{{ batch.batch_no }}</td>
                  <td>{{ batch.file_name }}</td>
                  <td>
                    <span :class="['pill', importBatchStatusTone(batch.status)]">{{
                      importBatchStatusLabel(batch.status)
                    }}</span>
                  </td>
                  <td>{{ batch.total_rows }}</td>
                  <td>{{ batch.created_rows }}</td>
                  <td>{{ batch.updated_rows }}</td>
                  <td>{{ batch.skipped_rows }}</td>
                  <!-- 错误数只在**真的有问题**时标红：`0` 与 `3` 在同一列里长得一样的话，
                       这一列就白留了。 -->
                  <td :class="batch.error_rows ? 'status-bad' : ''">{{ batch.error_rows }}</td>
                  <td>{{ batch.created_at }}</td>
                  <td><button class="btn" @click="openBatchDetail(batch)">查看明细</button></td>
                </tr>
              </tbody>
            </table>
            <!-- 截断要自己说出来（§10）：这一页看的是「最近导入过什么」，而用了几年的库
                 会有几百批。 -->
            <p v-if="batchesTotal > batches.length" class="muted tiny" style="margin-top:8px">
              仅显示最近 {{ batches.length }} 批，共 {{ batchesTotal }} 批。
            </p>
          </div>
        </div>
      </section>
    </template>

    <Modal
      :model-value="showBatchDetail"
      :title="batchDetail ? `导入明细 · ${batchDetail.batch_no}` : '导入明细'"
      size="lg"
      @update:model-value="showBatchDetail = $event"
    >
      <p v-if="batchDetail" class="muted tiny">
        {{ batchDetail.file_name }} · 共 {{ batchDetail.total_rows }} 行 ·
        新增 {{ batchDetail.created_rows }} · 更新 {{ batchDetail.updated_rows }} ·
        放弃 {{ batchDetail.skipped_rows }} · 错误 {{ batchDetail.error_rows }}
      </p>
      <SkeletonBlock v-if="batchRowsLoading" variant="table" :rows="4" />
      <ErrorState v-else-if="batchRowsError" :message="batchRowsError" :on-retry="retryBatchDetail" />
      <p v-else-if="!batchRows.length" class="muted tiny">这一批没有逐行记录</p>
      <div v-else class="table-wrap" style="margin-top:12px">
        <table>
          <thead>
            <tr>
              <th>行</th><th>学号</th><th>姓名</th><th>年级班级</th><th>性别</th><th>年龄</th>
              <th>结果</th><th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in batchRows" :key="row.row_no">
              <td>{{ row.row_no }}</td>
              <td>{{ row.student_no || '—' }}</td>
              <td>{{ row.name || '—' }}</td>
              <td>{{ row.grade_name || '' }}{{ row.class_name || '' }}</td>
              <!-- 性别存的是归一化之后的**编码**（MALE/FEMALE），必须显式翻译：
                   DataTable 的默认插槽直接打印 row[key]，而这里连默认插槽都没有。 -->
              <td>{{ genderLabel(row.gender) }}</td>
              <td>{{ ageLabel(row.age) }}</td>
              <td>
                <span :class="['pill', importRowStatusTone(row.processing_status)]">{{
                  importRowStatusLabel(row.processing_status)
                }}</span>
              </td>
              <!-- 两格合一列：「撞上了什么」与「拿它怎么办」是同一件事的两半，分开两列
                   会让一行里出现两个空格子。`conflict_code` 落库了（选「覆盖」时它留着，
                   因为那件事确实发生过），所以它得能读出来。 -->
              <td>
                <span v-if="row.conflict_code" class="status-warn">{{
                  rosterConflictLabel(row.conflict_code)
                }}</span>
                <span v-if="row.message" :class="row.conflict_code ? '' : 'status-bad'">{{
                  (row.conflict_code ? '：' : '') + row.message
                }}</span>
                <span v-if="!row.conflict_code && !row.message" class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer>
        <button class="btn" @click="showBatchDetail = false">关闭</button>
      </template>
    </Modal>

    <Modal :model-value="showImportErrors" title="导入校验明细" size="lg" @update:model-value="showImportErrors = $event">
      <div class="table-wrap">
        <table>
          <thead><tr><th>行号</th><th>学号</th><th>问题</th></tr></thead>
          <tbody>
            <tr v-for="row in studentIssueRows" :key="row.row_no">
              <td>{{ row.row_no }}</td>
              <td>{{ row.student_no }}</td>
              <td>
                <span v-if="row.errors.length" class="status-bad">{{ row.errors.join('；') }}</span>
                <span v-if="row.conflicts.length" class="status-warn">
                  {{ row.conflicts.map(c => c.message).join('；') }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <template #footer>
        <button class="btn" @click="showImportErrors = false">关闭</button>
        <button class="btn" @click="downloadStudentErrorReport">下载错误报告</button>
      </template>
    </Modal>

    <ConfirmDialog
      :open="showConfirm"
      title="确认导入"
      :message="confirmMessage"
      confirm-text="确认"
      cancel-text="取消"
      @confirm="confirmImport"
      @cancel="showConfirm = false"
      @update:open="showConfirm = $event"
    />
  </div>
</template>
