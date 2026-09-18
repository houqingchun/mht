<script setup lang="ts">
/**
 * Inline failure state with a retry affordance.
 *
 * Previously errors rendered as bare red text with no icon, no next step, and
 * no way to recover short of reloading the page.
 */
defineProps<{
  message: string
  /** Omit to render without a retry button (e.g. permission errors). */
  onRetry?: () => void
  retryLabel?: string
}>()
</script>

<template>
  <div class="error-state" role="alert">
    <span class="error-icon" aria-hidden="true">!</span>
    <div class="error-body">
      <p class="error-message">{{ message }}</p>
      <button v-if="onRetry" class="btn small" type="button" @click="onRetry">
        {{ retryLabel || '重试' }}
      </button>
    </div>
  </div>
</template>
