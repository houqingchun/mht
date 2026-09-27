<script setup lang="ts">
/**
 * 数据备份（V2.2.0 §5.25）。
 *
 * 这一页取代了安装目录里那枚 `备份数据.bat` —— 不是把它的 bug 修好，是把它脚下的地
 * 抽掉。那枚按钮必然报错的四条原因里，有两条（`.env` 在局域网用法下只授 SYSTEM 与
 * Administrators；整条链路零日志）**只在应用进程之外才成立**：服务本身早就把配置读进
 * 内存了，而它手上有 `server.log`、审计表与这一页。
 *
 * 三条与别处同源的约定：
 *
 * 1. **编辑框的初值取「原始配置」，落点与历史取接口发回来的「展开值」。** 这两个是
 *    两个来源，不能互相顶替：`/admin/settings` 里 `dir` 是空串（表示「用默认目录」），
 *    而 `GET /admin/backup` 里的 `dir` 是服务端展开之后的**绝对路径**。把后者回填进
 *    输入框，用户一按保存就把这台机器上的绝对路径当字面量存了回去 —— 换一台机器部署
 *    就落到别人的目录里。
 * 2. **落点必须显示服务端算出来的那一个**（§9：口径要写进界面）。一个写着「留空即
 *    默认」的输入框答不出「那我这台机器上到底落在哪」。
 * 3. **历史表按时间排，不按枚举排。** 「触发方式」「结果」两列都**不声明
 *    `sortable: true`** —— `labels.ts` 里那两张表刻意没有 `*_ORDER`（§3 第四面：
 *    可排序的枚举列必须显式声明中文序，否则排出来的是编码的字母序，而它看起来只是
 *    一个正常的升序）。这一页要的是「最近发生了什么」，那就是 `created_at desc`。
 */
import { computed, onMounted, ref } from 'vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import ErrorState from '../../components/ErrorState.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import { showToast } from '../../services/toast'
import { backupStatusLabel, backupStatusTone, backupTriggerLabel } from '../../services/labels'
import {
  downloadBackupRecord,
  getBackupOverview,
  runBackupNow,
  updateSystemSettings,
  type BackupOverview,
  type BackupRecord
} from '../../services/api'
import { applySettings, loadSettings, snapshotSettings } from '../../composables/useSettings'

const overview = ref<BackupOverview | null>(null)
const loading = ref(true)
const error = ref('')

/** 四个键的草稿。初值来自**原始配置**，见文件头第 1 条。 */
const form = ref({ dir: '', secondary_dir: '', keep_days: 30, auto_enabled: true })

const saving = ref(false)
const running = ref(false)
const downloading = ref<number | null>(null)

/** 落盘的那一份原始配置，用来判「改了没有」。 */
const saved = computed(() => snapshotSettings().backup)

const dirty = computed(() => {
  const base = saved.value
  return (
    form.value.dir.trim() !== base.dir.trim() ||
    form.value.secondary_dir.trim() !== base.secondary_dir.trim() ||
    Number(form.value.keep_days) !== base.keep_days ||
    form.value.auto_enabled !== base.auto_enabled
  )
})

function syncForm() {
  const base = snapshotSettings().backup
  form.value = {
    dir: base.dir,
    secondary_dir: base.secondary_dir,
    keep_days: base.keep_days,
    auto_enabled: base.auto_enabled
  }
}

async function load() {
  loading.value = true
  error.value = ''
  // 取数之前先清空（§14：一次失败的读取不许留下上一次的答案）。
  overview.value = null
  try {
    // 先拉配置再取概览：前者决定编辑框的初值，后者决定落点与历史。
    await loadSettings()
    overview.value = await getBackupOverview()
    syncForm()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '备份信息加载失败'
  } finally {
    loading.value = false
  }
}

/** 只重取概览，**不碰编辑框** —— 用户可能正在改那几个字段。 */
async function refreshOverview() {
  try {
    overview.value = await getBackupOverview()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '备份记录刷新失败')
  }
}

async function save() {
  saving.value = true
  try {
    const result = await updateSystemSettings('backup', {
      dir: form.value.dir.trim(),
      secondary_dir: form.value.secondary_dir.trim(),
      // `<= 0` = 只增不删，所以负号在这里是有意义的，不夹到 1。
      keep_days: Math.trunc(Number(form.value.keep_days) || 0),
      auto_enabled: form.value.auto_enabled
    })
    const merged = snapshotSettings()
    merged.backup = result.values
    applySettings(merged)
    syncForm()
    showToast('success', '备份设置已保存')
    // 落点跟着配置走，改完必须重取 —— 否则屏幕上还写着改之前的那个目录。
    await refreshOverview()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function runNow() {
  running.value = true
  try {
    const record = await runBackupNow()
    // **失败不抛。** mysqldump 报错时服务端照常回一行 `FAILED`（那样才留得下证据），
    // 所以这里必须按 `status` 分岔 —— 一个只 `catch` 的实现会把「备份失败」显示成
    // 「备份完成」，而那正是这一页要消灭的那种静默。
    if (record.status === 'SUCCEEDED') {
      showToast('success', `备份完成：${record.file_name || '已生成'}`)
    } else {
      showToast('error', record.message || '备份失败，但没有留下原因')
    }
    await refreshOverview()
  } catch (err) {
    // 唯一会抛的是 409「正在备份中」—— 那是**没跑**，不是失败。
    showToast('error', err instanceof Error ? err.message : '备份没能启动')
  } finally {
    running.value = false
  }
}

async function download(record: BackupRecord) {
  downloading.value = record.id
  try {
    await downloadBackupRecord(record)
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '下载失败')
  } finally {
    downloading.value = null
  }
}

const columns: Column[] = [
  { key: 'created_at', label: '时间', width: '150px' },
  { key: 'trigger', label: '触发方式', width: '110px' },
  { key: 'status', label: '结果', width: '100px' },
  { key: 'file_size', label: '大小', align: 'right', width: '100px' },
  // 失败原因与「第二路径没拷成」的警告都住这一列（后端只有 `message` 一个列担两种话，
  // 由 `status` 分辨）。它必须在这里看得见：自动备份失败是完全静默的 —— 凌晨、没人看着、
  // 连个窗口都没有 —— 那正是原来那枚按钮的毛病原样搬到新地方。
  { key: 'message', label: '说明' },
  { key: 'actions', label: '操作', align: 'right', width: '110px' }
]

/**
 * 后端发的是 `2026-09-27T03:12:00`（**朴素本地时间**，不是 UTC）。
 * 只截到分钟、**不做时区换算** —— `new Date(...)` 会把一个没有时区标记的串按浏览器
 * 的时区解释，于是同一行在两个时区的机器上显示两个时间。
 */
function shortMoment(value: string | null): string {
  if (!value) return '—'
  return value.slice(5, 16).replace('T', ' ')
}

function sizeText(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const emptyText = computed(() =>
  overview.value?.auto_enabled
    ? '还没有备份过。服务启动时与之后每小时各检查一次，也可以点上面的「立即备份」。'
    : '还没有备份过。自动备份已关掉，只能点上面的「立即备份」。'
)

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">运维</div>
        <h1>数据备份</h1>
        <p class="page-desc">
          把整个数据库导出成一份 <code>.sql</code> 文件。定时那一路由服务自己跑（启动时一次，
          之后每小时检查一次，当天已经成功过就跳过），<strong>不依赖操作系统的计划任务</strong>；
          手动备份随时可以点。
        </p>
        <p class="muted tiny" style="margin-top:6px">
          备份文件是整个数据库的原始副本：<strong>含全部学生心理数据、答卷与账号</strong>，
          <strong>不可能遮蔽</strong>。所以每一次下载都会写一条审计。
        </p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="cards" :rows="4" />
    <!-- 错误分支排在空态之前（§14）——「暂无备份记录」是一句关于数据的话，
         不能在一次读取失败时落下。 -->
    <ErrorState v-else-if="error" :message="error" :on-retry="load" />

    <template v-else>
      <!-- ① 最近一次：成功与失败各自一行。失败**只要有就显示**，不因为之后成功过而
           被盖掉 —— 一条被盖住的失败等于没有记录。 -->
      <div class="card tier-primary">
        <div class="card-head">
          <div>
            <h2>最近一次</h2>
            <p class="muted tiny">
              时间一路由服务端记，与本机时钟的时区无关。
            </p>
          </div>
          <div class="toolbar">
            <button class="btn primary" type="button" :disabled="running" @click="runNow">
              {{ running ? '备份中…' : '立即备份' }}
            </button>
          </div>
        </div>
        <div class="card-body">
          <div :class="overview?.last_failure ? 'grid two' : ''">
            <div>
              <div class="section-title">最近一次成功</div>
              <template v-if="overview?.last_success">
                <div class="detail-row">
                  <span>时间</span>
                  <b>{{ shortMoment(overview.last_success.created_at) }}</b>
                </div>
                <div class="detail-row">
                  <span>文件</span>
                  <b class="path-cell">{{ overview.last_success.file_name || '—' }}</b>
                </div>
                <div class="detail-row">
                  <span>大小</span>
                  <b>{{ sizeText(overview.last_success.file_size) }}</b>
                </div>
                <div class="detail-row">
                  <span>文件还在磁盘上</span>
                  <b v-if="overview.last_success.file_exists" class="status-ok">在</b>
                  <b v-else class="status-bad">已不在（被手工删掉或搬走了）</b>
                </div>
              </template>
              <p v-else class="muted tiny">还没有成功过。</p>
            </div>

            <div v-if="overview?.last_failure">
              <div class="section-title">最近一次失败</div>
              <div class="detail-row">
                <span>时间</span>
                <b>{{ shortMoment(overview.last_failure.created_at) }}</b>
              </div>
              <div class="detail-row">
                <span>触发方式</span>
                <b>{{ backupTriggerLabel(overview.last_failure.trigger) }}</b>
              </div>
              <p class="status-bad" style="margin-top:10px; line-height:1.6">
                {{ overview.last_failure.message || '没有留下原因。' }}
              </p>
            </div>
          </div>
        </div>
      </div>

      <!-- ② 落点：显示的是服务端展开之后的**实际路径**，不是配置里的那个字面量。 -->
      <div class="card">
        <div class="card-head">
          <div>
            <h2>备份位置与保留</h2>
            <p class="muted tiny">
              路径留空表示用默认目录；第二路径留空表示不复制第二份。
            </p>
          </div>
        </div>
        <div class="card-body">
          <div class="detail-row">
            <span>实际落点</span>
            <b class="path-cell">
              {{ overview?.dir }}
              <span v-if="overview?.dir_is_default" class="muted tiny">（默认目录）</span>
            </b>
          </div>
          <div class="detail-row">
            <span>第二份</span>
            <b v-if="overview?.secondary_dir" class="path-cell">{{ overview.secondary_dir }}</b>
            <span v-else class="muted tiny">没有配置（只留本机一份）</span>
          </div>
          <p class="muted tiny" style="margin:8px 0 18px">
            第二路径写不进去时，主备份照样算成功，只在历史那一行里记一条警告 ——
            「多存一份没存成」不该把一次成功的备份变成失败。
          </p>

          <div class="settings-grid">
            <label class="field">
              <span>备份目录</span>
              <input v-model="form.dir" type="text" placeholder="留空 = 上面那个默认目录" />
              <span class="field-hint">必须是服务这台机器能写的本地路径。</span>
            </label>
            <label class="field">
              <span>第二路径</span>
              <input
                v-model="form.secondary_dir"
                type="text"
                placeholder="留空 = 不复制第二份（例如另一块盘或已挂载的网络盘）"
              />
              <span class="field-hint">只在本机之外多留一份，不是替代品。</span>
            </label>
            <label class="field">
              <span>保留天数</span>
              <input v-model.number="form.keep_days" type="number" min="0" max="3650" />
              <span class="field-hint">填写 0 表示只增不删。到期的文件在每次备份后清理。</span>
            </label>
            <label class="field">
              <span>自动备份</span>
              <span class="checkbox-row">
                <input v-model="form.auto_enabled" type="checkbox" />
                <span>服务启动时跑一次，之后每小时检查一次</span>
              </span>
              <span class="field-hint">
                关掉之后定时那一路不再跑，手动按钮不受影响；重新打开立刻生效（不必重启服务）。
              </span>
            </label>
          </div>

          <div class="settings-actions">
            <button class="btn primary" type="button" :disabled="!dirty || saving" @click="save">
              {{ saving ? '正在保存…' : '保存' }}
            </button>
          </div>
        </div>
      </div>

      <!-- ③ 历史：`created_at` 倒序，因此两列枚举都不排序（见文件头第 3 条）。 -->
      <div class="card">
        <div class="card-head">
          <div>
            <h2>备份历史</h2>
            <p class="muted tiny">最近 50 次。失败的那几次也在这里，原因写在「说明」列。</p>
          </div>
        </div>
        <DataTable
          :columns="columns"
          :rows="overview?.records || []"
          row-key="id"
          :page-size="20"
          :empty-text="emptyText"
        >
          <template #created_at="{ row }">{{ shortMoment(row.created_at) }}</template>
          <template #trigger="{ row }">
            {{ backupTriggerLabel(row.trigger) }}<span v-if="!row.operator_name" class="muted tiny"> · 系统</span>
          </template>
          <template #status="{ row }">
            <span class="pill" :class="backupStatusTone(row.status)">{{ backupStatusLabel(row.status) }}</span>
          </template>
          <template #file_size="{ row }">{{ sizeText(row.file_size) }}</template>
          <template #message="{ row }">
            <span v-if="row.message" class="muted tiny" :title="row.message">{{ row.message }}</span>
            <span v-else class="muted tiny">—</span>
          </template>
          <template #actions="{ row }">
            <div class="toolbar">
              <button
                v-if="row.status === 'SUCCEEDED' && row.file_exists"
                class="btn small"
                type="button"
                :disabled="downloading === row.id"
                @click="download(row)"
              >
                {{ downloading === row.id ? '下载中…' : '下载' }}
              </button>
              <!-- 文件不在时不给按钮：一枚点了必然报错的按钮比一个空位更糟。 -->
              <span v-else-if="row.status === 'SUCCEEDED'" class="muted tiny">文件已不在</span>
              <span v-else class="muted tiny">—</span>
            </div>
          </template>
        </DataTable>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 路径可能很长（`C:\Users\Zhang San\AppData\Local\xinliceping\backups`），
   而 `.detail-row` 是 flex 行 —— 不换行的话它会把右半屏顶宽、整页横向滚动。 */
.path-cell {
  word-break: break-all;
  text-align: right;
}

/* `.field` 里放复选框时保持与其它三项同一种纵向排布。 */
.checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 0;
  font-size: var(--font-body);
}

.checkbox-row input[type='checkbox'] {
  width: auto;
  margin: 0;
}
</style>
