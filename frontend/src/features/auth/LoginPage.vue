<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getBranding, login, type Branding, type Role } from '../../services/api'

const router = useRouter()
// The login screen must show the school's name before anyone is authenticated,
// so it reads the public branding endpoint rather than /admin/settings.
const branding = ref<Branding | null>(null)

const role = ref<Role>('student')
// Never prefilled: credentials in a form field are one screenshot away from
// being leaked, and a pre-filled form can be mistaken for a real credential.
const account = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

// `roleLabel` is the tab text, `fieldLabel` names the account input — they are
// different things and were previously conflated into one `label` field.
const roleOptions: Array<{
  value: Role
  roleLabel: string
  fieldLabel: string
  placeholder: string
  home: string
}> = [
  { value: 'student', roleLabel: '学生', fieldLabel: '学号', placeholder: '请输入学号', home: '/student/home' },
  { value: 'counselor', roleLabel: '心理老师', fieldLabel: '手机号', placeholder: '请输入手机号', home: '/counselor/workbench' },
  { value: 'leader', roleLabel: '德育领导', fieldLabel: '手机号', placeholder: '请输入手机号', home: '/leader/overview' },
  { value: 'admin', roleLabel: '系统管理员', fieldLabel: '管理员账号', placeholder: '请输入管理员账号', home: '/admin/system' }
]

const currentRole = computed(() => roleOptions.find((item) => item.value === role.value)!)

function switchRole(nextRole: Role) {
  role.value = nextRole
  account.value = ''
  password.value = ''
  error.value = ''
}

onMounted(async () => {
  // Non-fatal: the template falls back to literal branding if this fails, so a
  // settings outage never blocks sign-in.
  try {
    branding.value = await getBranding()
  } catch {
    branding.value = null
  }
})

/**
 * 登录失败要说清是哪一种失败（2026-09-17 补）。
 *
 * 此前**一切**失败都报「账号、角色或密码不正确」——后端没起来、网关返回一张
 * HTML 错误页、网络断了，看到的都是同一句话，于是用户会一直重打密码，
 * 而真正的问题在别处。
 *
 * 前端拿不到 HTTP 状态码（CLAUDE.md §2），但**能**分清「服务端答了话」与
 * 「一个字都没收到」：
 * - `fetch` 本身的失败按规范抛 `TypeError`（网络/跨域/连接被拒），
 * - 非 JSON 的响应体（502 的 HTML 页）抛 `SyntaxError`，
 * - 其余是服务端按统一封装发回来的 `error.message`——那句话本来就是给用户看的
 *   （`auth_service.py` 的 401 原文就是「账号、角色或密码不正确」，与这里原先
 *   写死的那一句一字不差）。所以照它显示既没有变松，也让 500 之类的故障
 *   第一次有机会说出自己的名字。
 */
function loginFailureMessage(err: unknown): string {
  if (err instanceof TypeError) return '无法连接服务器，请检查网络后重试'
  if (err instanceof SyntaxError) return '服务端返回了无法解析的响应，请联系管理员'
  return err instanceof Error && err.message ? err.message : '登录失败'
}

async function submit() {
  loading.value = true
  error.value = ''
  try {
    await login(role.value, account.value.trim(), password.value)
    await router.push(currentRole.value.home)
  } catch (err) {
    error.value = loginFailureMessage(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-shell">
    <section class="login-panel">
      <div class="brand-row">
        <div class="brand-mark">{{ (branding?.brand_name || '心').slice(0, 1) }}</div>
        <div>
          <h1>{{ branding?.brand_name || '心晴' }}</h1>
          <p>{{ branding?.brand_subtitle || '中学生心理测评与关怀平台' }}</p>
        </div>
      </div>

      <!-- 四个页签的「选中」此前只是 `.active` 那个类——读屏软件听到的是四个普通按钮，
           不知道当前选的是哪一个（2026-09-17 补）。`aria-pressed` 把 `.active`
           这件事说出来，两者永远同时变，不会各说各话。 -->
      <div class="role-tabs" aria-label="选择登录角色">
        <button
          v-for="item in roleOptions"
          :key="item.value"
          :class="{ active: role === item.value }"
          :aria-pressed="role === item.value"
          type="button"
          @click="switchRole(item.value)"
        >
          {{ item.roleLabel }}
        </button>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label>
          <span>{{ currentRole.fieldLabel }}</span>
          <input v-model="account" autocomplete="username" :placeholder="currentRole.placeholder" />
        </label>
        <label>
          <span>密码</span>
          <input v-model="password" autocomplete="current-password" type="password" />
        </label>
        <p v-if="error" class="form-error">{{ error }}</p>
        <button class="primary-action" :disabled="loading" type="submit">
          {{ loading ? '正在登录' : '登录' }}
        </button>
      </form>

      <!-- Trust belongs on the first screen. This is the moment a student
           decides whether the answers are safe to give. -->
      <div class="login-assurance">
        <strong>你的回答不会在班级中公开</strong>
        <p>
          只有经授权的老师能按职责查看对应信息，系统管理员默认无法查看心理内容。
          所有敏感查看与导出都会留下记录。
        </p>
        <p class="muted tiny">
          筛查结果用于学校提供帮助，不等同于医学诊断。
        </p>
      </div>
    </section>
  </main>
</template>
