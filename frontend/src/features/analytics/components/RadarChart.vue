<script setup lang="ts">
/** 雷达图（内联 SVG，八维度）。 */
import { computed } from 'vue'
const props = defineProps<{
  data: Array<{ label: string; value: number; max: number }>
}>()
const W = 450, H = 350, CX = 225, CY = 175, R = 130
const axes = computed(() => props.data.map((d, i) => {
  const a = -Math.PI / 2 + i * 2 * Math.PI / props.data.length
  return { ...d, x: CX + R * Math.cos(a), y: CY + R * Math.sin(a),
    vx: CX + R * (d.value / (d.max || 1)) * Math.cos(a),
    vy: CY + R * (d.value / (d.max || 1)) * Math.sin(a),
    lx: CX + (R + 14) * Math.cos(a), ly: CY + (R + 14) * Math.sin(a) }
}))
const rings = [0.25, 0.5, 0.75, 1]
function ringPts(s: number) { return axes.value.map(a => `${CX + (a.x - CX) * s},${CY + (a.y - CY) * s}`).join(' ') }
const shapePts = computed(() => axes.value.map(a => `${a.vx},${a.vy}`).join(' '))
</script>
<template>
  <div class="radar-wrap">
    <svg :viewBox="`0 0 ${W} ${H}`" role="img" aria-label="八维度得分雷达图">
      <polygon v-for="s in rings" :key="s" :points="ringPts(s)" fill="none" stroke="#dce7f5"/>
      <line v-for="a in axes" :key="a.label" :x1="CX" :y1="CY" :x2="a.x" :y2="a.y" stroke="#e5edf6"/>
      <text v-for="a in axes" :key="'l'+a.label" :x="a.lx" :y="a.ly" font-size="12" fill="#567089" text-anchor="middle" dominant-baseline="central">{{ a.label }}</text>
      <polygon :points="shapePts" fill="#2d83f133" stroke="#2981ee" stroke-width="2"/>
    </svg>
  </div>
</template>

<style scoped>
.radar-wrap { display: flex; align-items: center; justify-content: center; width: 100%; min-width: 0; overflow: hidden }
.radar-wrap svg { display: block; max-width: 450px; width: 100%; height: auto }
</style>
