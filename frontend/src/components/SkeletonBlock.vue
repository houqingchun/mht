<script setup lang="ts">
/**
 * Loading placeholder that preserves the page's layout.
 *
 * These pages are data-dense; a one-line "正在加载" followed by a full layout
 * shift makes every navigation feel like the page collapsed and rebuilt.
 * Rendering the eventual shape up front keeps the view stable.
 */
withDefaults(defineProps<{
  variant?: 'metrics' | 'table' | 'cards' | 'text'
  rows?: number
  columns?: number
}>(), {
  variant: 'text',
  rows: 4,
  columns: 4
})
</script>

<template>
  <div class="skeleton" aria-busy="true" aria-live="polite">
    <span class="sr-only">正在加载</span>

    <div v-if="variant === 'metrics'" class="grid metrics">
      <div v-for="i in columns" :key="i" class="card metric skeleton-metric">
        <div class="skeleton-line w-40"></div>
        <div class="skeleton-line skeleton-lg w-50"></div>
        <div class="skeleton-line w-60"></div>
      </div>
    </div>

    <div v-else-if="variant === 'table'" class="card">
      <div class="card-head">
        <div class="skeleton-line w-25"></div>
      </div>
      <div class="card-body">
        <div class="skeleton-line w-100" style="height:32px"></div>
        <div v-for="i in rows" :key="i" class="skeleton-line w-100" style="margin-top:10px"></div>
      </div>
    </div>

    <div v-else-if="variant === 'cards'" class="grid two">
      <div v-for="i in rows" :key="i" class="card pad">
        <div class="skeleton-line w-40"></div>
        <div class="skeleton-line w-100" style="margin-top:12px"></div>
        <div class="skeleton-line w-80" style="margin-top:8px"></div>
      </div>
    </div>

    <div v-else>
      <div v-for="i in rows" :key="i" class="skeleton-line" :class="i === rows ? 'w-60' : 'w-100'"></div>
    </div>
  </div>
</template>
