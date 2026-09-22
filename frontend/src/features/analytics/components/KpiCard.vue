<script setup lang="ts">
/** KPI 指标卡。 */
const props = defineProps<{
  label: string
  value: string | number
  hint?: string
  icon?: string
  tone?: 'blue' | 'green' | 'red' | 'amber'
}>()
const icons: Record<string, string> = {
  users: '♧', check: '✓', alert: '!', clock: '◷',
  chart: '▤', clipboard: '◰', shield: '◈'
}
</script>
<template>
  <div class="card kpi">
    <div class="kpi-icon" :class="tone">{{ icon ? icons[icon] || '♧' : '♧' }}</div>
    <div>
      <div class="kpi-label">{{ label }}</div>
      <div class="kpi-value" :class="tone">{{ value }}</div>
      <div class="hint">{{ hint }}</div>
    </div>
  </div>
</template>

<style scoped>
.kpi { display: flex; gap: 14px; align-items: center; min-width: 0; min-height: 114px; padding: 18px }
.kpi > div:last-child { min-width: 0 }
.kpi-icon { height: 55px; width: 55px; border-radius: 50%; background: #e9f2ff; display: grid; place-items: center; font-size: 26px; color: #1275e4; flex-shrink: 0 }
.kpi-icon.green { background: #e6f7ef; color: #00875a }
.kpi-icon.red { background: #fff0f0; color: #c83c43 }
.kpi-icon.amber { background: #fff3e7; color: #a86600 }
.kpi-label { overflow-wrap: anywhere; font-size: 13px; font-weight: 650; margin: 0 }
.kpi-value { overflow-wrap: anywhere; font-size: clamp(24px, 2.2vw, 30px); font-weight: 750; line-height: 1.28 }
.kpi-value.green { color: #00875a }
.kpi-value.red { color: #c83c43 }
.kpi-value.amber { color: #a86600 }
@media(max-width: 520px) {
  .kpi { min-height: 100px; padding: 14px }
  .kpi-icon { width: 46px; height: 46px; font-size: 22px }
}
.hint { color: var(--muted); font-size: var(--font-caption) }
</style>
