<script setup lang="ts">
/** 指标条（小卡片横排，用于信号类型/工作项等）。 */
defineProps<{ items: Array<{ label: string; value: string | number; hint?: string }> }>()
</script>
<template>
  <div class="metric-strip">
    <div v-for="(item, i) in items" :key="i" class="metric-mini">
      <b>{{ item.value }}</b>
      <span class="metric-label">{{ item.label }}</span>
      <span class="metric-hint">{{ item.hint || '' }}</span>
    </div>
  </div>
</template>

<style scoped>
.metric-strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 12px 0 16px }
.metric-mini { border: 1px solid #dce7f5; background: #f8fbff; border-radius: 9px; padding: 11px 13px }
.metric-mini b { display: block; font-size: 18px; margin-top: 2px }
.metric-label { display: block; font-size: 12px; font-weight: 600; color: #315077 }
.metric-hint { display: block; font-size: 11px; color: #687c93; margin-top: 2px }
@media(max-width:1200px) { .metric-strip { grid-template-columns: 1fr 1fr } }
@media(max-width:700px) { .metric-strip { grid-template-columns: 1fr } }
</style>
