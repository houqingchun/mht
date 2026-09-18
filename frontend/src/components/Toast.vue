<script setup lang="ts">
import { getToasts, removeToast } from '../services/toast'
import { ref, onMounted, onUnmounted } from 'vue'

const items = ref(getToasts())
const timer = ref<ReturnType<typeof setInterval> | null>(null)

function sync() {
  items.value = [...getToasts()]
}

onMounted(() => {
  timer.value = setInterval(sync, 200)
})

onUnmounted(() => {
  if (timer.value) clearInterval(timer.value)
})
</script>

<template>
  <!--
    提示此前对读屏软件是**静默的**：`showToast('error', '保存失败')` 只在屏幕上出现，
    看不见屏幕的人不知道刚刚那一按发生了什么（2026-09-17 补）。

    `role="status"`（= `aria-live="polite"`）挂**容器**上而不是每条提示上：
    活动区域必须在内容出现**之前**就在 DOM 里，否则那一段内容出生时没人听——
    而这一批提示是 `v-for` 新插进来的节点。`aria-atomic="false"` 让新增的那条
    自己播报，而不是把整个列表重念一遍。

    用 polite 而不是 alert：失败提示打断读者正在念的一句话，代价比晚一秒大。
    一个容器也只能是一种语气，分两个容器会连版式一起改。
  -->
  <TransitionGroup
    name="toast"
    tag="div"
    class="toast-container"
    role="status"
    aria-live="polite"
    aria-atomic="false"
  >
    <div v-for="toast in items" :key="toast.id" :class="['toast', `toast-${toast.type}`]">
      <span class="toast-icon" aria-hidden="true">{{ toast.type === 'success' ? '✓' : toast.type === 'error' ? '✕' : 'ℹ' }}</span>
      <span class="toast-text">{{ toast.text }}</span>
      <button class="toast-dismiss" type="button" @click="removeToast(toast.id)">×</button>
    </div>
  </TransitionGroup>
</template>
