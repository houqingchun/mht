<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import Modal from '../../components/Modal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import { useDataImport } from '../../composables/useDataImport'
import { getMe, getStudents, type StudentItem } from '../../services/api'
import {
  STUDENT_STATUS_ORDER,
  genderLabel,
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
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function handleStudentFile(file: File) {
  error.value = ''
  await submitStudentFile(file)
}

async function confirmImport() {
  showConfirm.value = false
  await commitStudents()
  await load()
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
          <strong>总数 {{ studentPreview.total }}</strong>
          <span>可导入 {{ studentPreview.valid_count }}</span>
          <span v-if="studentPreview.conflict_count" class="status-warn">
            待确认 {{ studentPreview.conflict_count }}
          </span>
          <span>错误 {{ studentPreview.error_count }}</span>
          <button
            :disabled="!studentPreview.preview_token || committingStudents || needsStudentResolution"
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
    </template>

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
