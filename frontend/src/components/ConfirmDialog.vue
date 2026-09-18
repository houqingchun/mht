<script setup lang="ts">
import { ref } from 'vue'
import Modal from './Modal.vue'

const props = withDefaults(defineProps<{
  open: boolean
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}>(), {
  title: '确认',
  confirmText: '确认',
  cancelText: '取消',
  danger: false
})

const emit = defineEmits<{
  (e: 'confirm'): void
  (e: 'cancel'): void
  (e: 'update:open', value: boolean): void
}>()

function onConfirm() {
  emit('confirm')
  emit('update:open', false)
}

function onCancel() {
  emit('cancel')
  emit('update:open', false)
}
</script>

<template>
  <Modal :model-value="open" :title="title" size="sm" @update:model-value="emit('update:open', $event)">
    <p class="confirm-message">{{ message }}</p>
    <div class="confirm-actions">
      <button class="btn-cancel" type="button" @click="onCancel">{{ cancelText }}</button>
      <button :class="['btn-confirm', { 'btn-danger': danger }]" type="button" @click="onConfirm">
        {{ confirmText }}
      </button>
    </div>
  </Modal>
</template>
