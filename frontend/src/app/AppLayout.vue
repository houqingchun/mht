<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getMe, logout, type CurrentUser } from '../services/api'
import { useSettings } from '../composables/useSettings'
import AppIcon from '../components/AppIcon.vue'
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
      { key: 'counselor/workbench', label: '工作台', icon: 'home', path: '/counselor/workbench' },
      // 「重点学生」→「重点关注学生」（V2.0.0 §9 P1-04）。词条换了，路径与 key 没动——
      // 那是路由标识与深链，改它等于把已经发出去的收藏链接一起作废。
      { key: 'cases', label: '重点关注学生', icon: 'flag', path: '/counselor/cases' },
      { key: 'tasks', label: '测评任务', icon: 'clipboard', path: '/counselor/tasks' },
      { key: 'dataCenter', label: '数据中心', icon: 'database', path: '/counselor/data' },
      { key: 'exports', label: '导出中心', icon: 'download', path: '/counselor/exports' },
      { key: 'analytics', label: '统计分析', icon: 'chart-pie', path: '/counselor/analytics', children: [
        { key: 'analytics/overview', label: '筛查关注概览', icon: 'chart-pie', path: '/counselor/analytics/overview' },
        { key: 'analytics/dimensions', label: '八维度分析', icon: 'chart-bar', path: '/counselor/analytics/dimensions' },
        { key: 'analytics/grades', label: '年级维度对比', icon: 'chart-line', path: '/counselor/analytics/grades' },
        { key: 'analytics/classes', label: '班级维度画像', icon: 'users', path: '/counselor/analytics/classes' },
        { key: 'analytics/report', label: '专业分析报告', icon: 'file-text', path: '/counselor/analytics/report' }
      ]},
      { key: 'audit', label: '审计日志', icon: 'clock', path: '/counselor/audit' }
    ]
  },
  leader: {
    name: '德育领导',
    avatar: '德',
    nav: [
      { key: 'leader/overview', label: '领导总览', icon: 'gauge', path: '/leader/overview' },
      { key: 'progress', label: '重点进展', icon: 'refresh', path: '/leader/progress' },
      { key: 'analytics', label: '学校统计', icon: 'chart-pie', path: '/leader/analytics', children: [
        // 德育领导这两条与心理老师那两条**同名**（V2.0.0 §5.4 的建议 + §9 P1-04）：
        // 两个角色共用同一个组件，所以「全校」这个前缀此前是同一套组件里的两处硬编码
        // 标题。范围由页头那枚「当前数据范围」徽标表达（`ReportPageHeader.vue`），
        // 而徽标对德育领导读出来就是「全校」——**范围和它是谁的，各说各的**。
        { key: 'analytics/overview', label: '筛查关注概览', icon: 'chart-pie', path: '/leader/analytics/overview' },
        { key: 'analytics/dimensions', label: '八维度分析', icon: 'chart-bar', path: '/leader/analytics/dimensions' },
        { key: 'analytics/grades', label: '年级维度对比', icon: 'chart-line', path: '/leader/analytics/grades' },
        { key: 'analytics/classes', label: '班级维度画像', icon: 'users', path: '/leader/analytics/classes' },
        { key: 'analytics/report', label: '学校心理工作分析摘要', icon: 'file-text', path: '/leader/analytics/report' }
      ]},
      { key: 'tasks', label: '测评任务', icon: 'clipboard', path: '/leader/tasks' },
      { key: 'audit', label: '审计日志', icon: 'clock', path: '/leader/audit' }
    ]
  },
  admin: {
    name: '系统管理员',
    avatar: '管',
    nav: [
      { key: 'admin/system', label: '账号与权限', icon: 'settings', path: '/admin/system' },
      { key: 'organization', label: '组织学生', icon: 'user-plus', path: '/admin/organization' },
      { key: 'scale', label: '量表题库', icon: 'list', path: '/admin/scale' },
      { key: 'exports', label: '导出中心', icon: 'download', path: '/admin/exports' },
      { key: 'settings', label: '系统配置', icon: 'sliders', path: '/admin/settings' },
      { key: 'audit', label: '审计日志', icon: 'clock', path: '/admin/audit' }
    ]
  },
  student: {
    name: '学生',
    avatar: '学',
    nav: [
      { key: 'student/home', label: '我的测评', icon: 'clipboard-check', path: '/student/home' },
      { key: 'student/history', label: '完成记录', icon: 'history', path: '/student/history' }
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
/**
 * 窄屏那个账号菜单（V2.0.0 §5.13.7 Phase E 第 8 条）。
 *
 * 375–480px 上「登录设备 / 修改密码 / 退出」三枚按钮把标题挤没了——此前那一档的
 * 处置是给标题加 `max-width: 70px` 加省略号，也就是**在挤压之后做补救**，而屏幕上的
 * 标题就剩下「心理…」。用户要的是别挤压它（`styles.css` 那一档的注释记着这件事）。
 *
 * 谁显示谁不显示**由 CSS 决定**（`.top-action-wide` / `.account-menu`），与
 * `.sidebar` 在 780px 以下变成底部导航是同一个做法——两套都在 DOM 里，媒体查询选一套。
 * 所以这里只留一个开合状态，不管视口。
 *
 * **刻意不做外点关闭的 document 监听**：那个监听要在卸载时摘掉，而这一层被卸载的路径
 * （切角色、登出）恰恰是最容易漏的那一条（同一个坑见 §15 那条「面板在打开状态下被
 * 卸载也要解锁」）。改成一块**只在菜单开着时存在的透明遮罩**，它一卸载监听就不存在了。
 */
const showAccountMenu = ref(false)

function openSessions() {
  showAccountMenu.value = false
  showSessions.value = true
}

function openChangePassword() {
  showAccountMenu.value = false
  showChangePassword.value = true
}

/** 退出前先收起菜单：登出会切走整页，留一个开着的弹层没有意义。 */
async function signOutFromMenu() {
  showAccountMenu.value = false
  await signOut()
}

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
              <span class="nav-icon"><AppIcon :name="item.icon" /></span>
              <span class="nav-text">{{ item.label }}</span>
              <span class="nav-chevron" aria-hidden="true"><AppIcon name="chevron-right" /></span>
            </button>
            <div v-show="analyticsExpanded" class="nav-submenu">
              <RouterLink
                v-for="child in item.children"
                :key="child.key"
                :to="child.path"
                :class="['nav-btn', 'nav-subitem', { active: activeNav === child.key }]"
              >
                <span class="nav-icon"><AppIcon :name="child.icon" /></span>
                <span class="nav-text">{{ child.label }}</span>
              </RouterLink>
            </div>
          </div>
          <RouterLink
            v-else
            :to="item.path"
            :class="['nav-btn', { active: activeNav === item.key }]"
          >
            <span class="nav-icon"><AppIcon :name="item.icon" /></span>
            <span class="nav-text">{{ item.label }}</span>
          </RouterLink>
        </template>
      </nav>
      <div class="nav-spacer"></div>
    </aside>
    <main class="main">
      <header class="topbar">
        <div class="top-id">
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
          <button v-if="!isStudent" class="btn small top-action-wide" @click="showSessions = true">登录设备</button>
          <button class="btn small top-action-wide" @click="showChangePassword = true">修改密码</button>
          <button class="btn small top-action-wide" @click="signOut">退出</button>
          <!-- 375–480px：上面三枚收进这一个菜单（Phase E 第 8 条），否则标题被挤成
               「心理…」。宽屏上整块 `display:none`，所以那时页面上只有上面那三枚。
               这是一个**展开式**（disclosure）而不是 `role="menu"`：ARIA 的菜单语义
               要求方向键在项间移动，而这里没有实现那套键盘模型——挂上 `role="menu"`
               却说着一半的话，比不挂更糟。普通按钮 + Tab 本来就是对的。 -->
          <div class="account-menu" @keydown.esc="showAccountMenu = false">
            <button
              type="button"
              class="btn small acct-trigger"
              :aria-expanded="showAccountMenu"
              @click="showAccountMenu = !showAccountMenu"
            >
              账号
              <span class="acct-caret" aria-hidden="true"><AppIcon name="chevron-right" /></span>
            </button>
            <!-- 只在菜单开着时存在的一块透明遮罩：点别处关掉它。**没有 document 监听**
                 （那个要在卸载时摘掉，而这一层被卸载的路径最容易漏，见上面那段注释）。 -->
            <div v-if="showAccountMenu" class="acct-backdrop" @click="showAccountMenu = false"></div>
            <div v-if="showAccountMenu" class="acct-pop">
              <button v-if="!isStudent" type="button" class="acct-item" @click="openSessions">登录设备</button>
              <button type="button" class="acct-item" @click="openChangePassword">修改密码</button>
              <button type="button" class="acct-item" @click="signOutFromMenu">退出</button>
            </div>
          </div>
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
