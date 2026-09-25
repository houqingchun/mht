<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { getDataScopeSummary } from '../../../services/api'

defineProps<{
  description: string
}>()

/**
 * 标题的唯一出处是**路由**（`routes.ts` 的 `meta.title`），不在视图里各写一串字。
 *
 * 这一页（连同 `DimensionsPage` / `GradesPage` / `ClassPortraitPage`）被**两个角色共用**，
 * 四条路由里每一条都各有一份 `meta.title`——所以「两个角色怎么叫这一页」是一个**路由层**
 * 的决定，不是组件层的。标题写死在视图里就必然出现两套硬编码（V2.0.0 §5.4 的原话：
 * 「避免一套组件出现两套硬编码标题」），而两套字漂移了不会有任何东西看得见。
 *
 * **这四条路由今天两两同名**（`筛查关注概览` / `八维度分析` / `年级维度对比` / `班级维度画像`），
 * 那是 §5.4 统一术语的结果、不是「两处写法碰巧一样」：标题里的「全校」拿掉之后，
 * **范围由下面的徽标来说**。所以这一处的正确读法是「同一个组件在两条路由上渲染同一串字，
 * 而那一串字只有一处定义」——哪天要把某一侧改回去，改的是那一行路由。
 * （唯一真正分岔的是报告页，而它两边用的是**不同组件**，不走这里。）
 *
 * 顺带与 §15 那条「`meta.title` → `document.title`」同源：标签页上写的那句与页头这一句
 * 从此是同一次读取的结果，不可能各说各话。
 *
 * **页头之外还有一层**：`当前数据范围` 那个徽标（§5.2）说的是**数字实际覆盖到哪**。
 * 两者是两件事——标题回答「这一页是干什么的」，徽标回答「下面的数是谁的数」。
 * 范围小的心理老师看到的徽标是「初一年级」，而不是「全校」。
 */
const route = useRoute()
const title = computed(() => String(route.meta.title || ''))

const scope = ref('')
onMounted(async () => { try { scope.value = (await getDataScopeSummary()).displayText } catch { scope.value = '' } })
</script>

<template>
  <header class="page-head report-page-head">
    <div>
      <div class="eyebrow">统计分析</div>
      <h1>{{ title }}</h1>
      <p class="page-desc">{{ description }}</p>
      <p v-if="scope" class="scope-badge">当前数据范围：{{ scope }}</p>
    </div>
  </header>
</template>

<style scoped>
.report-page-head {
  align-items: flex-start;
}
.scope-badge { display:inline-block; margin:.55rem 0 0; padding:.25rem .65rem; border-radius:999px; background:#eaf4f7; color:#24576a; font-size:.82rem; }
</style>
