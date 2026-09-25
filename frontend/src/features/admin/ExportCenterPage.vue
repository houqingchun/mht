<script setup lang="ts">
/**
 * 导出中心 —— 这一页回答的是「我导过什么、现在还能不能取」。
 *
 * 2026-09-19 第 8 期之前，导出是**一次点击、一屏字节**：文件离开浏览器的那一刻，
 * 系统里不留任何痕迹（除了审计里一行「导出关注档案摘要」）。三件事因此没有答案：
 * 这份文件是不是实名的、它当时按谁的授权范围导的、它还取不取得到。§16.3 要求
 * 「导出文件默认短期有效、过期后自动失效」，而有效期只有在下载**经过一道门**
 * 时才是一个判据——所以导出变成了一次**作业**，这一页就是那批作业的台账。
 *
 * 三条口径写在这里，因为它们在界面上都要能看出来：
 *
 *   1. **遮蔽等级与用途分开**（§8）：用途是自由文本、担不起机器判据，而「这份文件
 *      是不是实名的」正是读这份台账的人唯一要回答的问题。所以它单独一列、带颜色。
 *   2. **状态是现算的**（§12 / `effective_export_status`）：库里只写「撤销」这个
 *      真实发生过的动作，过期由 `expires_at` 与此刻比出来——所以一行会在没有任何人
 *      碰它的情况下自己从「可下载」变成「已过期」，而它的行一个字都没动。
 *   3. **管理员能叫停、不能取走**（一条有意的不对称）：他看得到所有人的作业、能替
 *      任何人撤销（数据外泄时那是紧急开关），但他的心理详情能力是 `NONE`——一份实名
 *      名册不该经过他。所以「下载」这一列对他不出现，而页面顶上写着为什么。
 *      **「能叫停」不该顺带给出「能取走」。**
 *
 * 与 `CareCaseDetailPage` 那几个导出入口的关系：那几处点一下仍然是**一次点击落一个
 * 文件**（`api.ts` 的 `runExport` 建完作业立刻取字节），这一页留给「再看一眼当时导了
 * 什么」与「过期之前重下一次」。两处共用同一批 `export_job` 行。
 */
import { computed, onMounted, ref } from 'vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import ErrorState from '../../components/ErrorState.vue'
import FormDialog from '../../components/FormDialog.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import { showToast } from '../../services/toast'
import {
  EXPORT_JOB_STATUS_ORDER,
  exportJobStatusLabel,
  exportJobStatusTone,
  exportTypeLabel,
  maskLevelLabel,
  maskLevelTone
} from '../../services/labels'
import {
  downloadExportJob,
  exportFileName,
  getMe,
  listExportJobs,
  revokeExportJob,
  type CurrentUser,
  type ExportJob
} from '../../services/api'

const jobs = ref<ExportJob[]>([])
const loading = ref(true)
const error = ref('')
const me = ref<CurrentUser | null>(null)

/**
 * 管理员看到的是**所有人的**作业（`list_export_jobs` 按能力分岔），而心理老师只看到
 * 自己的。所以「操作人」这一列只对管理员有意义——对一位心理老师来说那一列全是他自己
 * 的名字，多一列不承载信息的重复。
 */
const isAdmin = computed(() => me.value?.role_code === 'admin')

const downloading = ref<number | null>(null)
const revoking = ref<ExportJob | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    // 取数之前先清空（§14）：换一次筛选、失败后重试，面板上都不该还留着上一次
    // 那一批——否则「加载失败」下面挂着的正是上一次成功的那些行。
    jobs.value = []
    const [items, current] = await Promise.all([listExportJobs(), getMe()])
    jobs.value = items
    me.value = current
  } catch (err) {
    error.value = err instanceof Error ? err.message : '导出记录加载失败'
  } finally {
    loading.value = false
  }
}

const columns = computed<Column[]>(() => {
  const base: Column[] = [
    { key: 'job_no', label: '作业编号', width: '180px' },
    { key: 'export_type', label: '内容', width: '130px' },
    { key: 'purpose', label: '用途' },
    { key: 'mask_level', label: '遮罩', width: '110px' },
    { key: 'row_count', label: '行数', align: 'right', width: '80px' },
    // 状态是可排序的**枚举列**，所以必须显式给中文序（§3 第四面）。不传的话
    // 排序拿 `READY` / `REVOKED` / `EXPIRED` 去 localeCompare——排出来是字母序，
    // 看起来只是个正常的升序，没人会怀疑它错了。
    { key: 'status', label: '状态', width: '110px', sortable: true, order: EXPORT_JOB_STATUS_ORDER },
    { key: 'created_at', label: '创建时间', width: '150px' },
    { key: 'expires_at', label: '有效期', width: '150px' },
    { key: 'download_count', label: '下载', align: 'right', width: '70px' }
  ]
  if (isAdmin.value) {
    base.splice(1, 0, { key: 'requested_by_name', label: '操作人', width: '130px' })
  }
  base.push({ key: 'actions', label: '操作', width: '150px' })
  return base
})

/**
 * 后端发的是 `2026-09-19T14:30:00`（朴素本地时间，不是 UTC）。
 *
 * 这里只截到分钟，**不做时区换算**——`new Date(...)` 会把一个没有时区标记的串按
 * 浏览器本地时区解释，而它本来就是本地时间，转一圈只会引入一次偏移（学生交卷时间
 * 那一列用的是同一套口径，见 `models/common.py` 的两个时钟）。所以是切字符串，
 * 不是解析。
 */
function shortMoment(value: string | null): string {
  if (!value) return '—'
  return value.slice(5, 16).replace('T', ' ')
}

async function download(job: ExportJob) {
  downloading.value = job.id
  try {
    await downloadExportJob(job.id, exportFileName(stemFor(job), job.job_no))
    showToast('success', `已下载 ${job.job_no}`)
    // 下载次数由服务端记（`download_count`），所以重取一次才看得到刚才那一次——
    // 本地 +1 的话，两个标签页各下一次，两边都少记一次。
    await load()
  } catch (err) {
    // 服务端那句话本来就是写给用户看的：「这份文件已经过期」「它已被撤销」
    // 「文件已不在服务器上，请重新导出」——三句话导向三种动作（§2）。
    showToast('error', err instanceof Error ? err.message : '下载失败')
    await load()
  } finally {
    downloading.value = null
  }
}

/** 文件名的语义前半截，与 `api.ts` 里那几个入口用的同一个词。 */
function stemFor(job: ExportJob): string {
  const stems: Record<string, string> = {
    CARE_CASES: 'care-cases',
    HIGH_RISK_CASES: 'high-risk-care-cases',
    SINGLE_CASE: 'care-case',
    TASK_COMPLETION: 'task-completion',
    NON_PARTICIPANTS: 'non-participants'
  }
  return stems[job.export_type] || 'export'
}

function openRevoke(job: ExportJob) {
  revoking.value = job
}

async function confirmRevoke(values: Record<string, string>) {
  const job = revoking.value
  if (!job) return
  try {
    await revokeExportJob(job.id, values.reason || '')
    showToast('success', `${job.job_no} 已撤销，之后任何人都取不到这份文件`)
    revoking.value = null
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '撤销失败')
  }
}

const revokeFields = [
  {
    key: 'reason',
    label: '撤销原因',
    type: 'textarea' as const,
    placeholder: '选填，例如「导错了范围」「文件发错了人」',
    maxLength: 255,
    // 选填是有意的：撤销是数据外泄时的紧急动作，那一刻多一个必填字段就是在最不该
    // 加摩擦的地方加摩擦。原因进审计的 `detail`，那是事后读轨迹的人唯一看得到的
    // 那一句。
    hint: '选填。写下来的话会进审计，事后追查时读到的就是这一句。'
  }
]

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">受控导出</div>
        <h1>导出中心</h1>
        <p class="page-desc">
          每一次导出都是一份有期限、有遮罩等级、有字段白名单的作业。文件名里带着作业编号，
          审计里搜的也是它。
        </p>
        <!-- 管理员这条不对称必须写出来：一个看得见全部作业、却没有下载按钮的人，
             会把「按钮灰着」读成一个故障。写明它是设计，并且顺手说出他真正该做的那件事。 -->
        <p v-if="isAdmin" class="muted tiny" style="margin-top:6px">
          你看到的是全部人的导出记录，也能替任何人撤销；但<strong>文件本身不经过管理员</strong>
          ——心理详情的明细要由心理老师取走。
        </p>
      </div>
    </div>

    <div class="card">
      <SkeletonBlock v-if="loading" variant="table" :rows="5" :columns="8" />
      <!-- 错误分支排在空态之前（§14）：一次读取失败不许留下「暂无导出记录」那句话，
           它会一直回答用户的问题，而正确答案是「刚才没读到」。 -->
      <ErrorState v-else-if="error" :message="error" :on-retry="load" />
      <DataTable
        v-else
        :columns="columns"
        :rows="jobs"
        row-key="id"
        :page-size="20"
        empty-text="还没有导出记录。在重点关注学生、工作台或测评任务里点导出，这里就会出现一条。"
      >
        <template #export_type="{ row }">{{ exportTypeLabel(row.export_type) }}</template>

        <!-- 未登录的行没有操作人（`requested_by_name` 为空）——它在界面上是「—」，
             不是空白格。留白与「这一列没接上」长得一样。 -->
        <template #requested_by_name="{ row }">{{ row.requested_by_name || '—' }}</template>

        <template #mask_level="{ row }">
          <span class="pill" :class="maskLevelTone(row.mask_level)">
            {{ maskLevelLabel(row.mask_level) }}
          </span>
        </template>

        <template #row_count="{ row }">
          {{ row.row_count }} 行
        </template>

        <template #status="{ row }">
          <span class="pill" :class="exportJobStatusTone(row.status)">
            {{ exportJobStatusLabel(row.status) }}
          </span>
        </template>

        <template #created_at="{ row }">{{ shortMoment(row.created_at) }}</template>

        <template #expires_at="{ row }">
          <!-- 「有效期」这一格按状态分岔：可下载的写它什么时候到期，已过期的写它
               什么时候过期的。同一格两句话，因为读者要做的判断不同——一个是
               「我还有多久」，另一个是「哦，它已经没了」。 -->
          <span v-if="row.status === 'EXPIRED'" class="muted">
            {{ shortMoment(row.expires_at) }} 已过期
          </span>
          <span v-else-if="row.status === 'REVOKED'" class="muted">
            {{ shortMoment(row.revoked_at) }} 被撤销
          </span>
          <span v-else class="nowrap">{{ shortMoment(row.expires_at) }} 前</span>
        </template>

        <template #download_count="{ row }">
          {{ row.download_count }} 次
        </template>

        <template #actions="{ row }">
          <div class="toolbar">
            <!-- 「能不能下载」由服务端算（`downloadable`），不由这里的状态推：
                「文件还在不在盘上」是界面看不见的判据之一，猜错的后果是按钮亮着、
                点下去报错。 -->
            <button
              v-if="row.downloadable && !isAdmin"
              class="btn small"
              type="button"
              :disabled="downloading === row.id"
              @click="download(row)"
            >
              {{ downloading === row.id ? '下载中…' : '下载' }}
            </button>
            <!-- 已经撤销过的不再给按钮：那个动作是**不可撤的**（库里写的是
                `revoked_at`），再点一次什么都不会发生，而按钮亮着会让人以为
                它能改回来。 -->
            <button
              v-if="row.status === 'READY'"
              class="btn small danger"
              type="button"
              @click="openRevoke(row)"
            >
              撤销
            </button>
          </div>
        </template>
      </DataTable>
    </div>

    <FormDialog
      :open="revoking !== null"
      title="撤销这份导出文件"
      :fields="revokeFields"
      submit-text="确认撤销"
      @submit="confirmRevoke"
      @cancel="revoking = null"
    >
    </FormDialog>
  </div>
</template>
