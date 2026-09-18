<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getMe, logout, type CurrentUser } from '../services/api'
import { useSettings } from '../composables/useSettings'
import Toast from '../components/Toast.vue'
import ErrorState from '../components/ErrorState.vue'
import ChangePasswordDialog from '../components/ChangePasswordDialog.vue'
import { showToast } from '../services/toast'

const router = useRouter()
const route = useRoute()
const user = ref<CurrentUser | null>(null)
const error = ref('')

interface NavItem {
  key: string
  label: string
  icon: string
  path: string
}

const navConfig: Record<string, { name: string; avatar: string; nav: NavItem[] }> = {
  counselor: {
    name: '心理老师',
    avatar: '心',
    nav: [
      { key: 'counselor/workbench', label: '工作台', icon: '⌂', path: '/counselor/workbench' },
      { key: 'cases', label: '重点学生', icon: '⚑', path: '/counselor/cases' },
      { key: 'tasks', label: '测评任务', icon: '▣', path: '/counselor/tasks' },
      { key: 'dataCenter', label: '数据中心', icon: '⇅', path: '/counselor/data' },
      { key: 'analytics', label: '统计分析', icon: '▥', path: '/counselor/analytics' },
      { key: 'audit', label: '审计日志', icon: '◷', path: '/counselor/audit' }
    ]
  },
  leader: {
    name: '德育领导',
    avatar: '德',
    nav: [
      { key: 'leader/overview', label: '领导总览', icon: '◎', path: '/leader/overview' },
      { key: 'progress', label: '重点进展', icon: '↻', path: '/leader/progress' },
      { key: 'analytics', label: '学校统计', icon: '▥', path: '/leader/analytics' },
      { key: 'tasks', label: '测评任务', icon: '▣', path: '/leader/tasks' },
      { key: 'audit', label: '审计日志', icon: '◷', path: '/leader/audit' }
    ]
  },
  admin: {
    name: '系统管理员',
    avatar: '管',
    nav: [
      { key: 'admin/system', label: '账号与权限', icon: '⚙', path: '/admin/system' },
      { key: 'organization', label: '组织学生', icon: '♙', path: '/admin/organization' },
      { key: 'scale', label: '量表题库', icon: '≡', path: '/admin/scale' },
      { key: 'settings', label: '系统配置', icon: '⚒', path: '/admin/settings' },
      { key: 'audit', label: '审计日志', icon: '◷', path: '/admin/audit' }
    ]
  },
  student: {
    name: '学生',
    avatar: '学',
    nav: [
      { key: 'student/home', label: '我的测评', icon: '○', path: '/student/home' },
      { key: 'student/history', label: '完成记录', icon: '◷', path: '/student/history' }
    ]
  }
}

const currentNav = computed(() => {
  if (!user.value) return null
  return navConfig[user.value.role_code]
})

const isStudent = computed(() => user.value?.role_code === 'student')

const activeNav = computed(() => {
  if (!currentNav.value) return ''
  const found = currentNav.value.nav.find(n => route.path === n.path)
  return found?.key || ''
})

// Branding lives in settings so a school rename doesn't need a redeploy.
const { settings, loadSettings } = useSettings()

const showChangePassword = ref(false)
const mustRotate = ref(false)

async function load() {
  try {
    const me = await getMe()
    if (route.meta.role && me.role_code !== route.meta.role) {
      await router.push('/login')
      return
    }
    user.value = me
    mustRotate.value = me.must_change_password
    // Authenticated callers can read the full settings; the login screen uses
    // the public branding endpoint instead.
    await loadSettings()
  } catch {
    error.value = '请先登录'
    await router.push('/login')
  }
}

/** Releases the forced-rotation gate after a successful change. */
async function onPasswordChanged() {
  mustRotate.value = false
  showToast('success', '密码已更新')
  await load()
}

async function signOut() {
  await logout()
  await router.push('/login')
}

// Auto-load user on mount
load()
</script>

<template>
  <div :class="['app-shell', { 'student-mode': isStudent }]">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">{{ settings.org.brand_name.slice(0, 1) }}</div>
        <div>
          <div class="brand-name">{{ settings.org.brand_name }}</div>
          <div class="brand-sub">{{ settings.org.brand_subtitle }}</div>
        </div>
      </div>
      <div class="nav-label">工作中心</div>
      <nav class="nav" v-if="currentNav">
        <RouterLink
          v-for="item in currentNav.nav"
          :key="item.key"
          :to="item.path"
          :class="['nav-btn', { active: activeNav === item.key }]"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="nav-spacer"></div>
    </aside>
    <main class="main">
      <header class="topbar">
        <div>
          <!-- The role used to be shown twice (a pill and the avatar initial).
               Keep the avatar as the single role marker. -->
          <div class="top-title">{{ user?.display_name || '工作台' }}</div>
          <div class="top-sub">{{ currentNav ? `${currentNav.name} · ` : '' }}{{ settings.org.school_name }}</div>
        </div>
        <div class="top-actions">
          <div class="avatar" :title="currentNav?.name" :aria-label="currentNav?.name">
            {{ currentNav?.avatar || '用' }}
          </div>
          <button class="btn small" @click="showChangePassword = true">修改密码</button>
          <button class="btn small" @click="signOut">退出</button>
        </div>
      </header>
      <section class="content" v-if="!error">
        <RouterView />
      </section>
      <section class="content" v-else>
        <ErrorState :message="error" />
      </section>
    </main>

    <!-- Forced rotation: an account still on an issued password must not reach
         student data before setting its own. -->
    <ChangePasswordDialog
      :open="mustRotate"
      :account="user?.account"
      forced
      @changed="onPasswordChanged"
      @cancel="signOut"
    />

    <!-- Voluntary change from the topbar. -->
    <ChangePasswordDialog
      :open="showChangePassword"
      :account="user?.account"
      @changed="onPasswordChanged"
      @update:open="showChangePassword = $event"
    />

    <Toast />
  </div>
</template>
