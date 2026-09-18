<script setup lang="ts" generic="T extends Record<string, any>">
import { computed, ref, watch } from 'vue'

export interface Column {
  key: string
  label: string
  /** Sortable columns must supply a comparable value. */
  sortable?: boolean
  align?: 'left' | 'right'
  /** Renders the raw value; return a string for plain text. */
  width?: string
  /**
   * 这一列是**枚举**时的中文序（从第一个到最后一个）。传 `labels.ts` 的
   * `*_ORDER`（它们由那些映射表的键序生成）。
   *
   * 不传的话，排序拿 `row[key]` 去 `localeCompare`——那排的是**编码的字母序**：
   * 「关注等级」升序会得到 一般观察 → 重点关注 → 需要关注。排出来的东西看起来
   * 只是个正常的升序，没人会怀疑它错了，所以枚举列必须显式声明。
   *
   * 认不出的码（以及空值）排在**最后**，两个方向都是：它们不属于这个序的任何一端，
   * 混进中间会让「最小的那一档」看起来像有值。
   */
  order?: readonly string[]
}

const props = withDefaults(defineProps<{
  columns: Column[]
  rows: T[]
  /** Key uniquely identifying a row (used for :key). */
  rowKey: string
  /** Rows per page. Set 0 to disable pagination. */
  pageSize?: number
  pageSizeOptions?: number[]
  emptyText?: string
  /** Optional client-side filter applied before sorting/paging. */
  filter?: (row: T) => boolean
  /** Sort key applied on first render. */
  defaultSort?: string
  defaultOrder?: 'asc' | 'desc'
  /** Renders a cell's sortable value; defaults to row[column.key]. */
  sortValue?: (row: T, column: Column) => string | number | null | undefined
  /**
   * Server-side mode: `rows` is already one page, and `total` is the full
   * count. The parent owns the data; this component only drives the pager and
   * emits the requested page. Use for unbounded tables (audit log).
   */
  manual?: boolean
  total?: number
  sortKey?: string
  sortOrder?: 'asc' | 'desc'
}>(), {
  pageSize: 20,
  pageSizeOptions: () => [10, 20, 50, 100],
  emptyText: '暂无数据',
  defaultOrder: 'asc',
  manual: false
})

const emit = defineEmits<{
  (e: 'update:page', value: number): void
  (e: 'update:pageSize', value: number): void
  (e: 'sort', payload: { key: string; order: 'asc' | 'desc' }): void
}>()

const sortKey = ref(props.defaultSort ?? '')
const sortOrder = ref<'asc' | 'desc'>(props.defaultOrder)
const page = ref(1)
const size = ref(props.pageSize)

function valueOf(row: T, column: Column) {
  if (props.sortValue) return props.sortValue(row, column)
  const raw = row[column.key]
  // 枚举列按 `column.order` 排（见 `Column.order`）。`indexOf` 落在 -1 的
  // 一律折成 null，与空值同一处理：排最后，两个方向都是。
  if (column.order) {
    const index = column.order.indexOf(String(raw))
    return index === -1 ? null : index
  }
  return raw
}

const processed = computed(() => {
  const filtered = props.filter ? props.rows.filter(props.filter) : [...props.rows]
  if (!sortKey.value) return filtered

  const column = props.columns.find(c => c.key === sortKey.value)
  if (!column) return filtered

  const direction = sortOrder.value === 'asc' ? 1 : -1
  return [...filtered].sort((a, b) => {
    const left = valueOf(a, column)
    const right = valueOf(b, column)
    // Nullish values always sort last, regardless of direction.
    if (left == null && right == null) return 0
    if (left == null) return 1
    if (right == null) return -1
    if (typeof left === 'number' && typeof right === 'number') return (left - right) * direction
    return String(left).localeCompare(String(right), 'zh-CN') * direction
  })
})

const total = computed(() => (props.manual ? (props.total ?? 0) : processed.value.length))
const pageCount = computed(() => (size.value > 0 ? Math.max(1, Math.ceil(total.value / size.value)) : 1))

const paged = computed(() => {
  if (props.manual) return props.rows
  if (size.value === 0) return processed.value
  const start = (page.value - 1) * size.value
  return processed.value.slice(start, start + size.value)
})

// A filter or sort change can leave the cursor past the end.
watch([total, size], () => {
  if (page.value > pageCount.value) page.value = pageCount.value
})

watch(page, value => { if (props.manual) emit('update:page', value) })
watch(size, value => { if (props.manual) emit('update:pageSize', value) })

function toggleSort(column: Column) {
  if (!column.sortable) return
  if (sortKey.value === column.key) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = column.key
    sortOrder.value = 'asc'
  }
  page.value = 1
  if (props.manual) emit('sort', { key: sortKey.value, order: sortOrder.value })
}

/**
 * `aria-sort` **只给可排序的列**（2026-09-17 改）。
 *
 * 此前每一列表头都带 `aria-sort`，不可排序的那些一律 `"none"`。`"none"` 的意思是
 * 「这一列可以排序，此刻没有排序」——读屏软件据此播报「可排序」。于是花名册里
 * 「姓名」「班级」这些点不动的列，在耳朵里全是可排序的，按下去没有任何反应。
 * 规范里 `aria-sort` 只对**确实可排序**的表头取值。
 */
function ariaSort(column: Column) {
  if (sortKey.value !== column.key) return 'none'
  return sortOrder.value === 'asc' ? 'ascending' : 'descending'
}
</script>

<template>
  <div class="data-table">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <!--
              排序原本只有鼠标到得了：`@click` 挂在 `<th>` 上，而表头不可聚焦，
              键盘用户拿不到这个功能（2026-09-17 补）。这里给可排序的表头加
              `tabindex="0"` 与 Enter/Space。

              为什么不套一个真的 `<button>`：那会让表头里多一个盒子，CSS 的
              `.th-sortable` 与四个 e2e 用例（都按 `th` 的中心点）同时要改，
              而键盘的结果一模一样。`aria-label` 补上「可排序」这三个字——
              可聚焦的 `columnheader` 本身不会告诉读屏用户按下回车会发生什么。
            -->
            <th
              v-for="column in columns"
              :key="column.key"
              :style="column.width ? { width: column.width } : undefined"
              :class="{ 'th-sortable': column.sortable, 'th-right': column.align === 'right' }"
              :tabindex="column.sortable ? 0 : undefined"
              :aria-label="column.sortable ? `${column.label}（可排序）` : undefined"
              :aria-sort="column.sortable ? ariaSort(column) : undefined"
              @click="toggleSort(column)"
              @keydown.enter.prevent="toggleSort(column)"
              @keydown.space.prevent="toggleSort(column)"
            >
              {{ column.label }}
              <span v-if="column.sortable" class="sort-indicator" aria-hidden="true">
                {{ sortKey === column.key ? (sortOrder === 'asc' ? '▲' : '▼') : '⇅' }}
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in paged" :key="row[rowKey]">
            <td
              v-for="column in columns"
              :key="column.key"
              :class="{ 'td-right': column.align === 'right' }"
            >
              <slot :name="column.key" :row="row" :column="column">
                {{ row[column.key] ?? '—' }}
              </slot>
            </td>
          </tr>
          <tr v-if="!paged.length">
            <td :colspan="columns.length"><div class="empty">{{ emptyText }}</div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="size > 0 && total > 0" class="table-pager">
      <span class="muted tiny">
        共 {{ total }} 条 · 第 {{ page }} / {{ pageCount }} 页
      </span>
      <div class="pager-controls">
        <select v-model.number="size" class="select" aria-label="每页条数">
          <option v-for="opt in pageSizeOptions" :key="opt" :value="opt">每页 {{ opt }} 条</option>
        </select>
        <button class="btn small" type="button" :disabled="page <= 1" @click="page -= 1">上一页</button>
        <button class="btn small" type="button" :disabled="page >= pageCount" @click="page += 1">下一页</button>
      </div>
    </div>
  </div>
</template>
