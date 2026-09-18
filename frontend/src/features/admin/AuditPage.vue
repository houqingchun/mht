<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import { showToast } from '../../services/toast'
import { downloadCsv } from '../../services/csv'
import { ROLE_LABELS } from '../../services/labels'
import { getMe, getAuditLogs, type AuditLogItem } from '../../services/api'
import { createLatestRequest } from '../../services/latest-request'

const router = useRouter()
const audits = ref<AuditLogItem[]>([])
const total = ref(0)
const loading = ref(true)
const exporting = ref(false)
const error = ref('')
const query = ref('')
const roleFilter = ref('all')

// Server-side paging + sorting: this table only grows.
const page = ref(1)
const pageSize = ref(20)
const sortKey = ref('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')

const columns: Column[] = [
  { key: 'created_at', label: '时间', sortable: true },
  // 操作人在角色前面：角色只说明「哪个角色做的」，而一所学校里心理老师有好几位，
  // 光看角色指不回具体的人。审计要追的是人。
  { key: 'actor_name', label: '操作人' },
  { key: 'actor_role', label: '角色', sortable: true },
  { key: 'action', label: '行为', sortable: true },
  { key: 'resource_type', label: '对象', sortable: true },
  { key: 'purpose', label: '用途' },
  { key: 'result', label: '结果' }
]

/**
 * 操作人这一格。
 *
 * 姓名为主、账号为辅：姓名是人认人的方式，账号才是唯一的那一个——两位老师重名时
 * 只写姓名就分不开了。未登录的行为（登录失败、系统事件）没有操作人，显示「—」，
 * 被尝试的账号留在「对象」列里，不往这里搬。
 */
function actorLabel(row: AuditLogItem) {
  if (!row.actor_name && !row.actor_account) return '—'
  return row.actor_name || row.actor_account || '—'
}

function resultLabel(result: string) {
  return result === 'SUCCESS' ? '成功' : result === 'FAILURE' ? '失败' : result
}

function resultTone(result: string) {
  return result === 'SUCCESS' ? 'green' : 'red'
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 搜索框的防抖只合并 300ms 内的连打，**不撤销已经发出的那一个请求**。
 * 输入「张」停手 300ms 以上、再补成「张三」，两条请求都在路上，
 * 而它们回答的是两个不同的问题（见 `services/latest-request.ts`）。
 */
const latest = createLatestRequest()
function onSearchInput() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
}

function onRoleChange() {
  // The filter spans the whole table, not the page in hand.
  page.value = 1
  load()
}

function onSort(payload: { key: string; order: 'asc' | 'desc' }) {
  sortKey.value = payload.key
  sortOrder.value = payload.order
  page.value = 1
  load()
}

async function load() {
  const token = latest.begin()
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (!['admin', 'counselor', 'leader'].includes(me.role_code)) {
      await router.push('/login')
      return
    }
    const result = await getAuditLogs({
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value,
      sort: sortKey.value,
      order: sortOrder.value,
      q: query.value.trim() || undefined,
      actorRole: roleFilter.value === 'all' ? undefined : roleFilter.value
    })
    // 已经有人在我之后出发了：我这一份回答的是**上一个**问题（上一次的搜索词、
    // 上一次的筛选、上一次的页码），写上去只会让表格与工具条各说各话。
    if (!latest.isCurrent(token)) return
    audits.value = result.items
    total.value = result.total
  } catch (err) {
    if (!latest.isCurrent(token)) return
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    // 迟到的请求不能替后来者收尾：`loading` 属于最后出发的那一次。
    if (latest.isCurrent(token)) loading.value = false
  }
}

/** Exports the current filter, not just the visible page. */
async function exportAuditLogs() {
  exporting.value = true
  try {
    const result = await getAuditLogs({
      limit: 200,
      offset: 0,
      sort: sortKey.value,
      order: sortOrder.value,
      q: query.value.trim() || undefined,
      actorRole: roleFilter.value === 'all' ? undefined : roleFilter.value
    })
    const rows: string[][] = [
      ['时间', '操作人', '操作人账号', '角色', '行为', '对象类型', '对象', '用途', '结果'],
      ...result.items.map(a => [
        a.created_at || '',
        // 「没有操作人」在导出的两列里一律留空，不写 `—`：那是界面上的占位符，
        // 落进 CSV 只会让整列被表格软件读成文本（同 §3 的数值列约定）。
        a.actor_name || '',
        a.actor_account || '',
        ROLE_LABELS[a.actor_role || ''] || a.actor_role || '',
        a.action,
        a.resource_type,
        a.resource_id || '',
        a.purpose || '',
        resultLabel(a.result)
      ])
    ]
    downloadCsv('audit-log.csv', rows)
    showToast('success', `已导出 ${result.items.length} 条${result.total > result.items.length ? '（单次上限 200 条）' : ''}`)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">安全审计</div>
        <h1>审计日志</h1>
        <p class="page-desc">敏感查看、导出、复核、跟进、家庭回访和规则变更均记录。</p>
      </div>
      <div class="actions">
        <button class="btn" :disabled="exporting" @click="exportAuditLogs">
          {{ exporting ? '正在导出…' : '导出审计日志' }}
        </button>
      </div>
    </div>

    <SkeletonBlock v-if="loading && !audits.length" variant="table" :rows="6" />
    <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

    <div v-if="!error" class="card">
      <div class="card-head">
        <div class="toolbar">
          <div class="search-box">
            <input
              v-model="query"
              placeholder="搜索行为、对象或用途"
              @input="onSearchInput"
            />
          </div>
          <select v-model="roleFilter" class="select" @change="onRoleChange">
            <option value="all">全部角色</option>
            <option value="counselor">心理老师</option>
            <option value="leader">德育领导</option>
            <option value="admin">系统管理员</option>
            <option value="student">学生</option>
          </select>
        </div>
      </div>

      <div class="card-body">
        <DataTable
          manual
          :columns="columns"
          :rows="audits"
          :total="total"
          row-key="id"
          v-model:page="page"
          v-model:page-size="pageSize"
          empty-text="没有匹配的审计记录"
          @sort="onSort"
          @update:page="load"
          @update:page-size="page = 1; load()"
        >
          <template #actor_name="{ row }">
            <span>{{ actorLabel(row) }}</span>
            <span v-if="row.actor_account && row.actor_name" class="muted tiny"> · {{ row.actor_account }}</span>
          </template>
          <template #actor_role="{ row }">
            {{ ROLE_LABELS[row.actor_role || ''] || '—' }}
          </template>
          <template #resource_type="{ row }">
            {{ row.resource_type }}<span v-if="row.resource_id" class="muted tiny"> · {{ row.resource_id }}</span>
          </template>
          <template #purpose="{ row }">{{ row.purpose || '—' }}</template>
          <template #result="{ row }">
            <span :class="['pill', resultTone(row.result)]">{{ resultLabel(row.result) }}</span>
          </template>
        </DataTable>
      </div>
    </div>

  </div>
</template>
