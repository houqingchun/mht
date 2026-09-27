<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getBranding, getMe, logout, type CurrentUser } from '../services/api'
import { useSettings } from '../composables/useSettings'
// 产品 Logo Mark（§5.23 冻结资产）。**只引用，不内联**（§5.23.4 第 7 条）。
import xinqingLogo from '../assets/logo-xinqing.svg'
import AppIcon from '../components/AppIcon.vue'
import Modal from '../components/Modal.vue'
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
      { key: 'admin/overview', label: '系统概览', icon: 'gauge', path: '/admin/overview' },
      { key: 'admin/system', label: '账号与权限', icon: 'settings', path: '/admin/system' },
      { key: 'organization', label: '组织学生', icon: 'user-plus', path: '/admin/organization' },
      { key: 'scale', label: '量表题库', icon: 'list', path: '/admin/scale' },
      { key: 'exports', label: '导出中心', icon: 'download', path: '/admin/exports' },
      { key: 'settings', label: '系统配置', icon: 'sliders', path: '/admin/settings' },
      { key: 'backup', label: '数据备份', icon: 'database', path: '/admin/backup' },
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

/**
 * 窄屏底栏只保留这几个入口，其余进「更多」（V2.0.0 §5.14.6 第 3 条）。
 *
 * 此前 780px 以下的处置是把**全部**入口摊进底栏再横向滚动：心理老师那一栏
 * （7 个顶层 + 5 个子项）要滑三屏才看得完，而底栏横滑是全站唯一一处「看不出来
 * 还能滑」的交互——屏幕上没有任何东西提示右边还有内容。
 *
 * 表里放的是 **key 而不是 path**：key 是导航项的身份，换路径（深链重排、加前缀）
 * 不会让这张表悄悄指到别的地方去。认不出的 key 由 `filter` 丢掉——底栏少一项
 * 比多一个空按钮好，而「这张表与导航脱节了」本身由 e2e 钉住（每个角色的底栏
 * 项数 + 「更多」在不在）。
 *
 * 取哪四项的判据是**这一档上真的用得到**，不是「按侧栏顺序取前四个」：按顺序取的话
 * 心理老师会拿到「工作台 / 重点关注学生 / 测评任务 / 数据中心」，而`统计分析`那五个页
 * 在手机上就只剩「更多」里那一条路（`grep` 过：全站没有第二处指向
 * `/analytics/dimensions` 这些路径的地方）。
 */
const MOBILE_PRIMARY: Record<string, string[]> = {
  counselor: ['counselor/workbench', 'cases', 'tasks', 'analytics'],
  leader: ['leader/overview', 'progress', 'tasks', 'analytics'],
  admin: ['admin/overview', 'admin/system', 'organization', 'scale'],
  // 学生只有两个入口，底栏就是全部——所以他没有「更多」。
  student: ['student/home', 'student/history']
}

const mobilePrimary = computed<NavItem[]>(() => {
  const nav = currentNav.value
  const keys = user.value ? MOBILE_PRIMARY[user.value.role_code] : undefined
  if (!nav || !keys?.length) return []
  return keys
    .map(key => nav.nav.find(item => item.key === key))
    .filter((item): item is NavItem => Boolean(item))
})

/**
 * 「更多」里放什么：**除底栏那几项之外的全部目的地**。
 *
 * 两半都要有，少一半就有东西在手机上不可达：
 * - 不在底栏的顶层项（心理老师的`数据中心`/`导出中心`/`审计日志`那一类）；
 * - 底栏里那个**分组**的子项。分组触发点进去是它的第一个子页（`routes.ts` 的
 *   redirect），所以另外四个子页必须有第二条路。
 *
 * 反过来，已经在底栏里的**可点**项不重复列——弹层是一张地图，不是第二条工具栏。
 */
const mobileMore = computed<Array<{ title: string; items: NavItem[] }>>(() => {
  const nav = currentNav.value
  const keys = user.value ? MOBILE_PRIMARY[user.value.role_code] : undefined
  if (!nav || !keys?.length) return []
  const primary = new Set(keys)
  const loose: NavItem[] = []
  const groups: Array<{ title: string; items: NavItem[] }> = []
  for (const item of nav.nav) {
    if (item.children?.length) {
      if (primary.has(item.key)) groups.push({ title: item.label, items: item.children })
      continue
    }
    if (!primary.has(item.key)) loose.push(item)
  }
  // 「其他功能」排在最前面：它是这个弹层存在的理由（底栏放不下的那些），
  // 分组子项是**顺带**补上的第二条路。
  return loose.length ? [{ title: '其他功能', items: loose }, ...groups] : groups
})

/** 开合状态只管「更多」那个弹层；它由底栏上那枚按钮触发。 */
const showMoreNav = ref(false)

/**
 * 底栏那一项算不算「当前」。
 *
 * 分组不能按 `activeNav === key` 判：`activeNav` 摊平之后拿到的是**子项**的 key
 * （`analytics/overview`），而底栏上那一项的 key 是 `analytics`——照桌面那套写法，
 * 站在任一报表页上底栏那一格都不会高亮，而用户会以为自己没在里面。
 */
function navItemActive(item: NavItem) {
  if (item.children?.length) return route.path.startsWith(item.path + '/')
  return activeNav.value === item.key
}

// Branding lives in settings so a school rename doesn't need a redeploy.
const { settings, loadSettings } = useSettings()

/**
 * 产品版本号，显示在品牌名旁边（CLAUDE.md §19）。
 *
 * 唯一出处是服务端免认证的 `GET /api/v1/public/branding`（`settings.py` 的
 * `read_branding` 把 `VERSION_LABEL` 拼在三个品牌键之后），**前端绝不写死**——
 * 部署包里 `frontend/dist` 是预构建的，写死会造出「后端升了、界面还说旧版本」的分岔，
 * 而那正是这一行要回答的问题。取不到就**整句不出现**（模板里那个 `v-if`），
 * 不显示空串、也不猜一个：一个说不清的版本号比没有更糟，因为操作员会照着它报故障。
 *
 * **不复用 `useSettings` 那份单例**：它走 `GET /admin/settings`，而那个响应里没有
 * version——版本号不是 `system_setting` 里的一项。所以这里另调一次，与「账号与权限」
 * 页脚那一行（`AdminSystemPage.vue`）取的是同一个端点、同一个字段。
 */
const versionLabel = ref('')

async function loadVersionLabel() {
  try {
    versionLabel.value = (await getBranding()).version || ''
  } catch {
    versionLabel.value = ''
  }
}

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
    // 版本号是装饰性的一行字，**不 await**：它拉不到也不该影响这一页的加载，
    // 而下面那个 `catch` 会把这里的任何失败读成「请先登录」。`loadVersionLabel`
    // 自己吞掉异常，所以这里没有第二个 catch。
    void loadVersionLabel()
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
        <!-- 品牌方块：§5.23 起是冻结的 Logo Mark，不再是「品牌名首字」。
             动态品牌名 / 副标题 / 版本号三样一个都没动（§5.23.2 B）——Logo 换的只是
             图形，学校改名之后这里跟着变的那两行字仍然来自配置。 -->
        <div class="brand-mark">
          <img class="brand-logo" :src="xinqingLogo" alt="" aria-hidden="true" />
        </div>
        <div>
          <!-- 版本号紧挨品牌名（V2.0.1 用户要求：让使用者在系统内能看到当前版本）。
               它是**一行字**而不是一枚按钮、不带任何交互；取不到时整枚不渲染
               （`v-if` 判的是那个空串，所以拉失败与还没拉到长得一样——都是不出现）。 -->
          <div class="brand-name">
            <span>{{ settings.org.brand_name }}</span>
            <span v-if="versionLabel" class="brand-version">{{ versionLabel }}</span>
          </div>
          <div class="brand-sub">{{ settings.org.brand_subtitle }}</div>
        </div>
      </div>
      <div class="nav-label">工作中心</div>
      <!-- 桌面侧栏：完整导航，含分组与子菜单。780px 以下整块 `display:none`。 -->
      <nav class="nav nav-desktop" v-if="currentNav">
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
      <!--
        窄屏底栏（V2.0.0 §5.14.6 第 3 条）：4 个最高频入口 + 「更多」。

        与 `.account-menu` 同一个做法——**两套都在 DOM 里，媒体查询选一套**。
        780px 以上这一块 `display:none`，所以它既不在无障碍树里（`display:none`
        的元素不进 a11y tree），也不会让 `getByRole('link', { name: '重点关注学生' })`
        这类定位在同一屏上取到两个元素。

        「更多」只在**还有别的东西**时才出现：学生只有两个入口，他那一档既没有
        分组也没有多余项，摆一个点开是空的按钮比没有更糟。
      -->
      <nav class="nav nav-mobile" v-if="currentNav">
        <RouterLink
          v-for="item in mobilePrimary"
          :key="item.key"
          :to="item.path"
          :class="['nav-btn', { active: navItemActive(item) }]"
        >
          <span class="nav-icon"><AppIcon :name="item.icon" /></span>
          <span class="nav-text">{{ item.label }}</span>
        </RouterLink>
        <button
          v-if="mobileMore.length"
          type="button"
          class="nav-btn"
          aria-haspopup="dialog"
          :aria-expanded="showMoreNav"
          @click="showMoreNav = true"
        >
          <span class="nav-icon"><AppIcon name="grid" /></span>
          <span class="nav-text">更多</span>
        </button>
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

    <!--
      「更多」：底栏放不下的入口（V2.0.0 §5.14.6 第 3 条）。

      用 `Modal` 而不是自己写一层，是为了直接拿到弹层的焦点陷阱 / `aria-modal` /
      焦点归还（CLAUDE.md §15 那条无障碍契约）——自己写就得把那三样各实现一遍，
      而其中「面板在打开状态下被卸载也要解锁」正是那一节记着最容易漏的一条。

      点一项就关掉它：弹层留着会挡住刚跳过去的那一页，而那一页正是他要去的地方。
    -->
    <Modal :model-value="showMoreNav" title="更多功能" @update:model-value="showMoreNav = $event">
      <div v-for="section in mobileMore" :key="section.title" class="more-section">
        <div class="more-title">{{ section.title }}</div>
        <RouterLink
          v-for="item in section.items"
          :key="item.key"
          :to="item.path"
          :class="['more-item', { active: navItemActive(item) }]"
          @click="showMoreNav = false"
        >
          <span class="nav-icon"><AppIcon :name="item.icon" /></span>
          <span>{{ item.label }}</span>
        </RouterLink>
      </div>
    </Modal>

    <Toast />
  </div>
</template>
