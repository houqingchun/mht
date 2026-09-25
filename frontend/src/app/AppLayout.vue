<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getMe, logout, type CurrentUser } from '../services/api'
import { useSettings } from '../composables/useSettings'
import Toast from '../components/Toast.vue'
import ErrorState from '../components/ErrorState.vue'
import ChangePasswordDialog from '../components/ChangePasswordDialog.vue'
import SessionListDialog from '../components/SessionListDialog.vue'
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
  children?: NavItem[]
}

const navConfig: Record<string, { name: string; avatar: string; nav: NavItem[] }> = {
  counselor: {
    name: '心理老师',
    avatar: '心',
    nav: [
      { key: 'counselor/workbench', label: '工作台', icon: '⌂', path: '/counselor/workbench' },
      // 「重点学生」→「重点关注学生」（V2.0.0 §9 P1-04）。词条换了，路径与 key 没动——
      // 那是路由标识与深链，改它等于把已经发出去的收藏链接一起作废。
      { key: 'cases', label: '重点关注学生', icon: '⚑', path: '/counselor/cases' },
      { key: 'tasks', label: '测评任务', icon: '▣', path: '/counselor/tasks' },
      { key: 'dataCenter', label: '数据中心', icon: '⇅', path: '/counselor/data' },
      { key: 'exports', label: '导出中心', icon: '↧', path: '/counselor/exports' },
      { key: 'analytics', label: '统计分析', icon: '▥', path: '/counselor/analytics', children: [
        { key: 'analytics/overview', label: '筛查关注概览', icon: '▥', path: '/counselor/analytics/overview' },
        { key: 'analytics/dimensions', label: '八维度分析', icon: '▤', path: '/counselor/analytics/dimensions' },
        { key: 'analytics/grades', label: '年级维度对比', icon: '▦', path: '/counselor/analytics/grades' },
        { key: 'analytics/classes', label: '班级维度画像', icon: '▧', path: '/counselor/analytics/classes' },
        { key: 'analytics/report', label: '专业分析报告', icon: '▣', path: '/counselor/analytics/report' }
      ]},
      { key: 'audit', label: '审计日志', icon: '◷', path: '/counselor/audit' }
    ]
  },
  leader: {
    name: '德育领导',
    avatar: '德',
    nav: [
      { key: 'leader/overview', label: '领导总览', icon: '◎', path: '/leader/overview' },
      { key: 'progress', label: '重点进展', icon: '↻', path: '/leader/progress' },
      { key: 'analytics', label: '学校统计', icon: '▥', path: '/leader/analytics', children: [
        // 德育领导这两条与心理老师那两条**同名**（V2.0.0 §5.4 的建议 + §9 P1-04）：
        // 两个角色共用同一个组件，所以「全校」这个前缀此前是同一套组件里的两处硬编码
        // 标题。范围由页头那枚「当前数据范围」徽标表达（`ReportPageHeader.vue`），
        // 而徽标对德育领导读出来就是「全校」——**范围和它是谁的，各说各的**。
        { key: 'analytics/overview', label: '筛查关注概览', icon: '▥', path: '/leader/analytics/overview' },
        { key: 'analytics/dimensions', label: '八维度分析', icon: '▤', path: '/leader/analytics/dimensions' },
        { key: 'analytics/grades', label: '年级维度对比', icon: '▦', path: '/leader/analytics/grades' },
        { key: 'analytics/classes', label: '班级维度画像', icon: '▧', path: '/leader/analytics/classes' },
        { key: 'analytics/report', label: '学校心理工作分析摘要', icon: '▣', path: '/leader/analytics/report' }
      ]},
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
      { key: 'exports', label: '导出中心', icon: '↧', path: '/admin/exports' },
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
  const items = currentNav.value.nav.flatMap(item => [item, ...(item.children || [])])
  const found = items.find(n => route.path === n.path)
  return found?.key || ''
})

const analyticsExpanded = ref(route.path.includes('/analytics'))
watch(() => route.path, path => {
  // 进入任一报表时保证分组可见；离开报表不主动收起，便于连续切换工作区。
  if (path.includes('/analytics')) analyticsExpanded.value = true
})

function openNavGroup(item: NavItem) {
  analyticsExpanded.value = !analyticsExpanded.value
  if (analyticsExpanded.value && !route.path.startsWith(item.path + '/')) router.push(item.path)
}

// Branding lives in settings so a school rename doesn't need a redeploy.
const { settings, loadSettings } = useSettings()

const showChangePassword = ref(false)
const showSessions = ref(false)
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
        <template v-for="item in currentNav.nav" :key="item.key">
          <div v-if="item.children" class="nav-group">
            <button
              type="button"
              :class="['nav-btn', 'nav-group-trigger', { active: route.path.startsWith(item.path) }]"
              :aria-expanded="analyticsExpanded"
              @click="openNavGroup(item)"
            >
              <span class="nav-icon">{{ item.icon }}</span>
              <span class="nav-text">{{ item.label }}</span>
              <span class="nav-chevron" aria-hidden="true">›</span>
            </button>
            <div v-show="analyticsExpanded" class="nav-submenu">
              <RouterLink
                v-for="child in item.children"
                :key="child.key"
                :to="child.path"
                :class="['nav-btn', 'nav-subitem', { active: activeNav === child.key }]"
              >
                <span class="nav-icon">{{ child.icon }}</span>
                <span class="nav-text">{{ child.label }}</span>
              </RouterLink>
            </div>
          </div>
          <RouterLink
            v-else
            :to="item.path"
            :class="['nav-btn', { active: activeNav === item.key }]"
          >
            <span class="nav-icon">{{ item.icon }}</span>
            <span class="nav-text">{{ item.label }}</span>
          </RouterLink>
        </template>
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
          <!-- 「登录设备」排在「修改密码」旁边：这两件事在用户的脑子里是同一类
               （我的账号安全），而会话那个弹层正是「我在别的电脑上忘了退出」的出路。
               学生不需要它——学生账号共享一台机房电脑是常态，逐台踢既踢不过来也
               不该由学生做（他们的会话本来就短）。 -->
          <button v-if="!isStudent" class="btn small" @click="showSessions = true">登录设备</button>
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

    <SessionListDialog :open="showSessions" @update:open="showSessions = $event" />

    <Toast />
  </div>
</template>
