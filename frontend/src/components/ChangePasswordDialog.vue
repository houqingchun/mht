<script setup lang="ts">
import { computed, ref } from 'vue'
import Modal from './Modal.vue'
import { changePassword } from '../services/api'

const props = withDefaults(defineProps<{
  open: boolean
  /** Forced rotation: the dialog cannot be dismissed, only resolved or logged out. */
  forced?: boolean
  account?: string
}>(), {
  forced: false
})

const emit = defineEmits<{
  (e: 'changed'): void
  (e: 'cancel'): void
  (e: 'update:open', value: boolean): void
}>()

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const error = ref('')

const MIN_LENGTH = 6

const mismatch = computed(
  () => confirmPassword.value.length > 0 && newPassword.value !== confirmPassword.value
)

function reset() {
  oldPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  error.value = ''
  submitting.value = false
}

function validate(): string | null {
  if (!oldPassword.value) return '请输入当前密码'
  if (newPassword.value.length < MIN_LENGTH) return `新密码至少 ${MIN_LENGTH} 位`
  if (newPassword.value === oldPassword.value) return '新密码不能与当前密码相同'
  if (newPassword.value !== confirmPassword.value) return '两次输入的新密码不一致'
  return null
}

async function submit() {
  const problem = validate()
  if (problem) {
    error.value = problem
    return
  }
  submitting.value = true
  error.value = ''
  try {
    await changePassword(oldPassword.value, newPassword.value)
    reset()
    emit('update:open', false)
    emit('changed')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '修改密码失败'
  } finally {
    submitting.value = false
  }
}

function cancel() {
  reset()
  emit('update:open', false)
  emit('cancel')
}

function onOpenChange(value: boolean) {
  // A forced dialog ignores outside attempts to close it.
  if (!value && props.forced) return
  if (!value) cancel()
}
</script>

<template>
  <Modal
    :model-value="open"
    :title="forced ? '请先修改初始密码' : '修改密码'"
    :persistent="forced"
    @update:model-value="onOpenChange"
  >
    <div class="form-grid">
      <div v-if="forced" class="notice warn">
        当前账号仍在使用初始或管理员重置的密码。为保护学生数据，请先设置只有你本人知道的新密码。
      </div>
      <p v-if="account" class="muted tiny">账号：{{ account }}</p>

      <!--
        三个 `<label>` 与它们的 `<input>` 此前是**兄弟节点且没有任何关联**：
        点标签不会聚焦到输入框，读屏软件在这个输入框上报的是「编辑框，密码」——
        哪个密码？三选一（2026-09-17 补）。`for`/`id` 是最短的那条修法。
        （登录页把 input 套在 label 里面，那条路也行，但这一页的 `.field` 版式
        ——标签在上、错误在下——要求两者是兄弟。）
      -->
      <div class="field">
        <label for="chpw-old">当前密码 <span class="required">*</span></label>
        <input id="chpw-old" v-model="oldPassword" type="password" autocomplete="current-password" placeholder="请输入当前密码" />
      </div>
      <div class="field">
        <label for="chpw-new">新密码 <span class="required">*</span></label>
        <input id="chpw-new" v-model="newPassword" type="password" autocomplete="new-password" :placeholder="`至少 ${MIN_LENGTH} 位`" />
      </div>
      <div class="field">
        <label for="chpw-confirm">确认新密码 <span class="required">*</span></label>
        <input
          id="chpw-confirm"
          v-model="confirmPassword"
          type="password"
          autocomplete="new-password"
          placeholder="请再次输入新密码"
          @keyup.enter="submit"
        />
        <span v-if="mismatch" class="field-error">两次输入的新密码不一致</span>
      </div>

      <p v-if="error" class="form-error">{{ error }}</p>
    </div>

    <template #footer>
      <button v-if="!forced" class="btn" type="button" @click="cancel">取消</button>
      <button class="btn primary" type="button" :disabled="submitting" @click="submit">
        {{ submitting ? '正在保存…' : '保存新密码' }}
      </button>
    </template>
  </Modal>
</template>
