<script setup lang="ts">
import { ref, watch } from 'vue'
import Modal from './Modal.vue'

export interface FormField {
  key: string
  label: string
  type?: 'text' | 'password' | 'textarea' | 'date' | 'select'
  placeholder?: string
  required?: boolean
  defaultValue?: string
  maxLength?: number
  /** Only for type="select". */
  options?: Array<{ value: string; label: string }>
  /**
   * 控件下面的一行小字。给「这一格填进去会发生什么」用的。
   *
   * 有些字段的后果不在字段名里，也不在服务端能报出来的那一侧：比如改一个账号的
   * 数据范围时，「它现在有几段范围、保存之后剩几段」只有调用方知道。写成 placeholder
   * 会被输入内容顶掉，写成 label 又太长，所以在控件下面留一行。
   */
  hint?: string
}

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  fields: FormField[]
  submitText?: string
}>(), {
  submitText: '提交'
})

const emit = defineEmits<{
  (e: 'submit', values: Record<string, string>): void
  (e: 'cancel'): void
  (e: 'update:open', value: boolean): void
}>()

const values = ref<Record<string, string>>({})
const errors = ref<Record<string, string>>({})

// Reset fields each time the dialog opens, so values never leak between openings.
watch(() => props.open, (open) => {
  if (!open) return
  values.value = {}
  errors.value = {}
  for (const f of props.fields) {
    if (f.defaultValue !== undefined) values.value[f.key] = f.defaultValue
  }
})

function validate(): boolean {
  errors.value = {}
  for (const f of props.fields) {
    const v = values.value[f.key] ?? ''
    if (f.required && !v.trim()) {
      errors.value[f.key] = `${f.label}不能为空`
    }
  }
  return Object.keys(errors.value).length === 0
}

function onSubmit() {
  if (!validate()) return
  emit('submit', { ...values.value })
  emit('update:open', false)
}

function onCancel() {
  emit('cancel')
  emit('update:open', false)
}
</script>

<template>
  <Modal :model-value="open" :title="title" @update:model-value="emit('update:open', $event)">
    <form class="form-dialog-form" @submit.prevent="onSubmit">
      <!-- 提示语在 `<label>` **外面**（在外层这个 div 里）。放进 label 的话它会成为控件
           可访问名的一部分——读屏软件把「这一格填进去会发生什么」当成字段名念出来，
           而字段名要回答的是「这里填什么」。错误提示一直在这个位置，hint 走同一处。 -->
      <div v-for="field in fields" :key="field.key" class="form-dialog-field">
        <label>
          <span>{{ field.label }}<span v-if="field.required" class="required-mark">*</span></span>
          <select
            v-if="field.type === 'select'"
            v-model="values[field.key]"
            :class="{ 'input-error': errors[field.key] }"
          >
            <option value="">{{ field.placeholder || '请选择' }}</option>
            <option v-for="opt in field.options || []" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
          <textarea
            v-else-if="field.type === 'textarea'"
            v-model="values[field.key]"
            :placeholder="field.placeholder"
            :maxlength="field.maxLength"
            :class="{ 'input-error': errors[field.key] }"
            rows="3"
          />
          <input
            v-else
            v-model="values[field.key]"
            :type="field.type ?? 'text'"
            :placeholder="field.placeholder"
            :maxlength="field.maxLength"
            :class="{ 'input-error': errors[field.key] }"
          />
        </label>
        <span v-if="field.hint" class="muted tiny">{{ field.hint }}</span>
        <span v-if="errors[field.key]" class="field-error">{{ errors[field.key] }}</span>
      </div>
      <div class="form-dialog-actions">
        <button class="btn-cancel" type="button" @click="onCancel">取消</button>
        <button class="btn-submit" type="submit">{{ submitText }}</button>
      </div>
    </form>
  </Modal>
</template>
