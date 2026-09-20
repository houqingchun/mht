<script setup lang="ts">
/**
 * 本班 / 本年级对照面板。
 *
 * 从 `CareCaseDetailPage.vue` 原样搬出来的：**两个屏幕都要这一块**——「学生持续关注
 * 档案」页与「学生测评记录」页（未建档的学生也要能看对照，而「班级对照第一场测评当天
 * 就能用」，历次趋势要两场以上才成立，见 CLAUDE.md §11）。
 *
 * **为什么是抽出来而不是各写一份**：那不只是 58 行排版。两句口径住在里面——
 * 「均值可能是 `null`，不能画成 0」（§11：`None` 不是 `0`）与「几个人的平均等于
 * 点名」那句小字。两处各写一份必然漂，而漂了不会有任何东西看得见。
 *
 * 取数**不在这里**：档案页把它与详情并发取（对照表晚到一会儿不该拖住整页），
 * 而且每一次读都会写一条审计（`查看班级对照`），合并进详情接口会让「看了一次对照」
 * 与「打开了一次档案」在轨迹里分不出来。所以 props 收的是已经取回来的结果。
 */
import SkeletonBlock from './SkeletonBlock.vue'
import { dimensionLabel, dimensionPercent } from '../services/labels'
import type { ClassComparison } from '../services/api'
import { computed } from 'vue'

const props = defineProps<{
  /** 取回来的对照数据；`null` 表示**还在路上**（失败是下面那个 `failed`）。 */
  comparison: ClassComparison | null
  /** 取数失败。它与「还没取到」必须长得不一样（§14：空态是一句关于数据的话）。 */
  failed: boolean
}>()

/**
 * 对照表的每一行：本人、本班均值、本年级均值，都换算成百分比再画。
 *
 * 百分比是必须的：八个维度题数不等（两个 15 题、其余 10 题），把 15 题的维度和
 * 10 题的维度按原始分并排，前者天然更长，读者会以为它「更严重」——那只是多问了五道题。
 * 分母取维度的 `max_score`（后端随每一行下发），复用 `dimensionPercent`，
 * 它的兜底已经在条形图和雷达图上用了很久。
 *
 * 均值可能为 `null`（同班样本太小，后端扣住不给）。那时这一行只画本人，
 * 并在图例里说明——**不能画成 0**，0 是一条很具体的断言。
 */
const comparisonRows = computed(() => {
  const items = props.comparison?.items || []
  return items.map(item => {
    const classPercent =
      item.class_average === null ? null : dimensionPercent(Math.round(item.class_average), item.max_score)
    const gradePercent =
      item.grade_average === null ? null : dimensionPercent(Math.round(item.grade_average), item.max_score)
    return {
      code: item.dimension_code,
      label: dimensionLabel(item.dimension_code),
      score: item.score,
      maxScore: item.max_score,
      own: dimensionPercent(item.score, item.max_score),
      classPercent,
      gradePercent,
      classSize: item.class_size,
      gradeSize: item.grade_size
    }
  })
})

const comparisonSuppressed = computed(() =>
  comparisonRows.value.some(row => row.classPercent === null || row.gradePercent === null)
)
</script>

<template>
  <div class="card pad">
    <h2>本班 / 本年级对照</h2>
    <p class="muted tiny" style="margin:6px 0 0">
      本人最近一场各维度的得分，与同班、同年级同学最近一场的水平并排。
      各维度按自己的题数归一化，纵轴 0–100%，所以八行之间可比。
    </p>

    <div v-if="failed" class="notice warn" style="margin-top:15px">
      对照数据暂时取不到，其余信息不受影响。稍后重试或刷新页面。
    </div>

    <template v-else-if="comparison">
      <div v-if="!comparison.items.length" class="empty" style="margin-top:15px">
        这名学生还没有已交卷的测评，暂无对照数据。
      </div>
      <template v-else>
        <div class="cmp-legend">
          <span class="cmp-key"><i class="cmp-swatch own"></i>本人</span>
          <span class="cmp-key">
            <i class="cmp-swatch cls"></i>
            本班均值（{{ comparisonRows[0]?.classSize ?? 0 }} 人已测评）
          </span>
          <span class="cmp-key">
            <i class="cmp-swatch grade"></i>
            本年级均值（{{ comparisonRows[0]?.gradeSize ?? 0 }} 人已测评）
          </span>
        </div>

        <div class="cmp-list">
          <div v-for="row in comparisonRows" :key="row.code" class="cmp-row">
            <div class="cmp-head">
              <span>{{ row.label }}</span>
              <span class="muted tiny">本人 {{ row.score }}/{{ row.maxScore }}</span>
            </div>
            <div class="cmp-track">
              <!-- 三根刻度线共用同一条轨道：本人实心、两个均值用不同的虚线，
                   读者一眼看出的是「他在哪」而不是三张分开的图。 -->
              <i class="cmp-bar own" :style="{ width: `${row.own}%` }"></i>
              <i
                v-if="row.classPercent !== null"
                class="cmp-mark cls"
                :style="{ left: `${row.classPercent}%` }"
              ></i>
              <i
                v-if="row.gradePercent !== null"
                class="cmp-mark grade"
                :style="{ left: `${row.gradePercent}%` }"
              ></i>
            </div>
            <div class="cmp-foot">
              <b>{{ row.own }}%</b>
              <span class="muted tiny">
                本班 {{ row.classPercent === null ? '样本过小' : `${row.classPercent}%` }} ·
                本年级 {{ row.gradePercent === null ? '样本过小' : `${row.gradePercent}%` }}
              </span>
            </div>
          </div>
        </div>

        <div v-if="comparisonSuppressed" class="notice" style="margin-top:12px">
          同班或同年级已测评的人数太少，均值不展示——几个人的平均等于点名。
          计数仍然给了出来。
        </div>
        <div class="notice" style="margin-top:12px">
          对照只描述这名学生与其同学在同一批题目上的相对位置，不构成诊断，
          也不是常模参照；高低由心理老师结合访谈判断。
        </div>
      </template>
    </template>

    <SkeletonBlock v-else variant="table" :rows="4" />
  </div>
</template>
