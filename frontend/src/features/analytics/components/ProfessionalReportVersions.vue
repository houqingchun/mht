<script setup lang="ts">
/**
 * 版本时间线（§5.13 Phase A）。
 *
 * 每一行是一个**版本行**，不是报告头——所以「状态 · 发布时间 · 发布人」读的都是这一版
 * 自己那三列（`0023` 起，`_content_payload`）。这正是这个组件存在的理由：V2 正在写的时候，
 * 报告头已经是 `DRAFT` 了，而 V1 那一行仍然是 `PUBLISHED`，界面上必须同时说得出这两件事。
 *
 * 「当前 / 历史」与「草稿 / 已发布」是**两个维度**，所以给两枚不同的药丸：
 * 前者回答「这跟我现在改的是不是同一版」，后者回答「这一版还能不能改」——
 * 当前版本是草稿（能改）、当前版本是已发布（锁死、要改先建新版本），两种都成立；
 * 历史版本则一律只读。
 *
 * 点一行 = 打开这一版看看当时写了什么（只读，统计快照也是那一版冻结的，
 * 服务端一处都不重算）。选中的判据是**实际显示的那一版**，所以「没选」等价于
 * 「跟着当前版本走」——两者在屏幕上必须长得一样，否则会出现「当前 V2」与「V1」同时高亮。
 */
import { formatDateTime } from '../../../services/dates'
import { reportStatusLabel, reportStatusTone } from '../../../services/labels'
import type { ProfessionalReportVersion } from '../../../services/api'

const props = defineProps<{
  versions: ProfessionalReportVersion[]
  currentVersion: number
  /** `null` = 跟随当前版本（编辑态）。 */
  selectedVersion: number | null
}>()
const emit = defineEmits<{ select: [versionNo: number] }>()

function activeVersion(item: ProfessionalReportVersion) {
  return (props.selectedVersion ?? props.currentVersion) === item.version_no
}
</script>

<template>
  <section class="card versions">
    <h2 class="section-title">版本时间线</h2>
    <p class="muted notes">
      已发布的版本永久保留、不可覆盖；要改内容请从最新已发布那一版新建版本。
      打开历史版本是只读的，它显示的是<strong>那一版当时</strong>的统计快照。
    </p>
    <ul class="rows">
      <li v-for="item in versions" :key="item.version_no">
        <button
          type="button"
          class="row"
          :class="{ active: activeVersion(item) }"
          :aria-pressed="activeVersion(item)"
          @click="emit('select', item.version_no)"
        >
          <span class="row-main">
            <span class="ver">V{{ item.version_no }}</span>
            <span class="sub">{{ item.created_by_name || '—' }} 创建于 {{ formatDateTime(item.created_at) }}</span>
            <span v-if="item.status === 'PUBLISHED'" class="sub">
              由 {{ item.published_by_name || '—' }} 发布于 {{ formatDateTime(item.published_at) }}
            </span>
          </span>
          <span class="row-side">
            <span class="pill" :class="reportStatusTone(item.status)">{{ reportStatusLabel(item.status) }}</span>
            <span class="pill" :class="item.version_no === currentVersion ? 'blue' : 'gray'">
              {{ item.version_no === currentVersion ? '当前版本' : '历史版本' }}
            </span>
          </span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.versions { margin-bottom: 16px }
.notes { margin: 0 0 10px; font-size: 12.5px; line-height: 1.7 }
.rows { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px }
.row { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; text-align: left; padding: 10px 12px; border: 1px solid #e2eaf4; border-radius: 8px; background: #fbfdff; cursor: pointer; font: inherit }
.row:hover { border-color: #b9d3f0; background: #f6faff }
.row.active { border-color: #0876d9; background: #f0f7ff; box-shadow: inset 3px 0 0 #0876d9 }
.row-main { display: grid; gap: 3px; min-width: 0 }
.ver { font-weight: 650 }
.sub { color: #617994; font-size: 12.5px }
.row-side { display: grid; gap: 4px; justify-items: end; flex-shrink: 0 }
@media(max-width: 600px) {
  .row { align-items: flex-start; flex-direction: column }
  .row-side { justify-items: start }
}
</style>
