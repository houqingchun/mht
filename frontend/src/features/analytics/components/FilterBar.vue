<script setup lang="ts">
/** 报表筛选栏 —— 学期/任务、年级、班级、分析口径、统计指标。 */
import { computed, nextTick, ref, watch } from 'vue'
import { getAssessmentTasks, type AssessmentTaskItem } from '../../../services/api'

const props = defineProps<{
  showGrade?: boolean
  showClass?: boolean
  showValidity?: boolean
  showMetric?: boolean
  gradeOptions?: string[]
  classOptions?: Array<{ grade: string; name: string }>
}>()

const emit = defineEmits<{
  query: [filters: FilterState]
  reset: []
}>()

export interface FilterState {
  taskIds: number[]
  grade: string
  cls: string
  validity: string
  metric: string
}

const tasks = ref<AssessmentTaskItem[]>([])
const tasksError = ref('')
const stamp = ref('')
const taskPicker = ref<HTMLDetailsElement | null>(null)
const MIN_ANALYTICS_SAMPLE = 5

function defaultTask() {
  // 报表小于 5 人时会按隐私规则隐藏精确数据。默认按施测开始时间选择最新且
  // 达到门槛的任务，而不是按数据库插入顺序（补录一份旧任务不应抢走默认项）。
  const analyzable = tasks.value
    .filter(task => task.completed_targets >= MIN_ANALYTICS_SAMPLE)
    .sort((a, b) => Date.parse(b.start_at || '') - Date.parse(a.start_at || ''))
  return analyzable[0] || tasks.value[0]
}

/**
 * 拉任务列表。**失败与「一个任务都没有」必须分开**（§14）：此前这里是
 * `catch { tasks.value = [] }`，两种原因落进同一个空数组，于是「暂无可分析任务」
 * 这句话在一次网络故障时也照样出现——而它是关于数据的断言，不是关于这次读取的。
 * 现在失败走 `tasksError`（带重试），空列表才走那句数据声明，查询按钮两种情况都置灰。
 */
async function loadTasks() {
  try {
    tasks.value = await getAssessmentTasks()
    tasksError.value = ''
    const task = defaultTask()
    if (task) {
      filters.value.taskIds = [task.id]
      await nextTick()
      query(true)
    } else {
      stamp.value = '本学年还没有可用的测评任务'
    }
  } catch (err) {
    tasks.value = []
    tasksError.value = err instanceof Error ? err.message : '测评任务列表加载失败'
  }
}

const filters = ref<FilterState>({ taskIds: [], grade: 'all', cls: 'all', validity: 'ALL_CALCULATED', metric: 'rate' })
const selectedTasks = computed(() => tasks.value.filter(task => filters.value.taskIds.includes(task.id)))
const taskSummary = computed(() => {
  if (!selectedTasks.value.length) return '请选择测评任务'
  if (selectedTasks.value.length === 1) return selectedTasks.value[0].name
  return `已选择 ${selectedTasks.value.length} 个任务`
})
const grades = computed(() => props.gradeOptions !== undefined
  ? props.gradeOptions
  : ['初一', '初二', '初三', '高一', '高二', '高三'])
const classes = computed(() => {
  if (props.classOptions === undefined) return ['1班', '2班', '3班', '4班']
  const matches = filters.value.grade === 'all'
    ? props.classOptions
    : props.classOptions.filter(item => item.grade === filters.value.grade)
  return [...new Set(matches.map(item => item.name))]
})

watch(() => filters.value.grade, () => {
  if (filters.value.cls !== 'all' && !classes.value.includes(filters.value.cls)) {
    filters.value.cls = 'all'
  }
})

function query(initial = false) {
  if (!filters.value.taskIds.length) {
    stamp.value = '请至少选择一个测评任务'
    return
  }
  stamp.value = initial ? '已自动加载最新可分析任务' : '已刷新'
  if (taskPicker.value) taskPicker.value.open = false
  emit('query', { ...filters.value })
}

function reset() {
  const task = defaultTask()
  filters.value = { taskIds: task ? [task.id] : [], grade: 'all', cls: 'all', validity: 'ALL_CALCULATED', metric: 'rate' }
  stamp.value = '已重置为最新可分析任务'
  if (taskPicker.value) taskPicker.value.open = false
  emit('reset')
  if (filters.value.taskIds.length) emit('query', { ...filters.value })
}

loadTasks()
</script>

<template>
  <div class="card filters">
    <div class="task-field">
      <span class="field-label">学期 / 测评任务（可多选）</span>
      <details ref="taskPicker" class="task-picker">
        <summary :title="selectedTasks.map(task => task.name).join('、')">{{ taskSummary }}</summary>
        <div class="task-options">
          <label v-for="t in tasks" :key="t.id" class="task-option">
            <input v-model="filters.taskIds" type="checkbox" :value="t.id"/>
            <span><b>{{ t.name }}</b><small>{{ t.source === 'IMPORTED' ? '外部导入' : '系统任务' }} · {{ t.completed_targets }}/{{ t.total_targets }} 已完成</small></span>
          </label>
          <div v-if="tasksError" class="task-empty">
            {{ tasksError }}
            <button type="button" class="link-btn" @click="loadTasks">重试</button>
          </div>
          <div v-else-if="!tasks.length" class="task-empty">本学年还没有可用的测评任务</div>
        </div>
      </details>
    </div>
    <label v-if="showGrade">年级
      <select v-model="filters.grade" :disabled="gradeOptions !== undefined && !grades.length">
        <option value="all">全部年级</option>
        <option v-for="grade in grades" :key="grade" :value="grade">{{ grade }}</option>
      </select>
    </label>
    <label v-if="showClass">班级
      <select v-model="filters.cls" :disabled="classOptions !== undefined && !classes.length">
        <option value="all">全部班级</option>
        <option v-for="cls in classes" :key="cls" :value="cls">{{ cls }}</option>
      </select>
    </label>
    <label v-if="showValidity">分析口径
      <select v-model="filters.validity">
        <option value="ALL_CALCULATED">全部已计算</option>
        <option value="VALIDITY_UNFLAGGED">效度未触发提示</option>
      </select>
    </label>
    <label v-if="showMetric">统计指标
      <select v-model="filters.metric">
        <option value="rate">高分比例</option>
        <option value="average">平均得分</option>
      </select>
    </label>
    <button class="btn primary" :disabled="!tasks.length" :title="tasks.length ? '' : '还没有可查询的测评任务'" @click="query()">⌕ 查询</button>
    <button class="btn" @click="reset">↻ 重置</button>
    <span class="hint" role="status">{{ stamp }}</span>
  </div>
</template>

<style scoped>
.filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; min-width: 0; margin-bottom: 16px; padding: 16px }
.filters label { display: grid; flex: 1 1 180px; gap: 5px; min-width: 0; max-width: 280px; font-weight: 600; font-size: var(--font-caption); color: var(--muted) }
.task-field { display: grid; flex: 2 1 300px; gap: 5px; min-width: 220px; max-width: 440px }
.field-label { color: var(--muted); font-size: var(--font-caption); font-weight: 600 }
.task-picker { position: relative }
.task-picker summary { min-height: 38px; padding: 9px 34px 8px 10px; overflow: hidden; border: 1px solid #cbd8e2; border-radius: 4px; background: #fff; color: #183447; cursor: pointer; text-overflow: ellipsis; white-space: nowrap; list-style: none }
.task-picker summary::-webkit-details-marker { display: none }
.task-picker summary::after { content: '⌄'; position: absolute; top: 7px; right: 11px; color: #536879; font-size: 16px }
.task-options { position: absolute; z-index: 30; top: calc(100% + 5px); left: 0; width: min(440px, calc(100vw - 60px)); max-height: 280px; padding: 6px; overflow-y: auto; border: 1px solid #cbd8e2; border-radius: 8px; background: #fff; box-shadow: 0 14px 34px rgba(20,43,69,.16) }
.filters .task-option { display: flex; width: 100%; max-width: none; padding: 9px 8px; align-items: flex-start; gap: 9px; border-radius: 6px; cursor: pointer }
.filters .task-option:hover { background: #f1f6fd }
.task-option input { flex: 0 0 auto; width: 16px; height: 16px; margin-top: 2px }
.task-option span { display: grid; min-width: 0; gap: 2px }
.task-option b { color: #183447; font-size: 13px; overflow-wrap: anywhere }
.task-option small { color: var(--muted); font-size: var(--font-caption); font-weight: 400 }
.task-empty { padding: 14px; color: var(--muted); text-align: center; font-size: var(--font-caption) }
.link-btn { border: 0; padding: 0 2px; background: none; color: #0876d9; font: inherit; font-weight: 600; text-decoration: underline; cursor: pointer }
.filters select { width: 100% }
.filters select { min-height: 38px; padding: 0 34px 0 10px; border: 1px solid #cbd8e2; border-radius: 4px; background: #fff; color: #183447 }
.hint { align-self: center }
@media(max-width: 850px) {
  .filters label { width: 100%; max-width: none; justify-content: space-between }
  .task-field { width: 100%; max-width: none; min-width: 0 }
  .task-options { width: 100% }
  .filters .btn { flex: 1 1 120px }
  .hint { flex-basis: 100% }
}
</style>
