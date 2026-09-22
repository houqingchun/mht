<script setup lang="ts">
defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <header class="page-head report-page-head">
    <div>
      <div class="eyebrow">统计分析</div>
      <h1>{{ title }}</h1>
      <p class="page-desc">{{ description }}</p>
    </div>
  </header>
</template>

<style scoped>
.report-page-head {
  align-items: flex-start;
}
</style>
