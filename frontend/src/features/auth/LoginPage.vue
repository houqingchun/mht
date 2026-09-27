<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { getBranding, login, type Branding, type Role } from '../../services/api'
// 产品 Logo Mark（§5.23 冻结资产）。**只引用，不内联**——SVG 本体不得重绘 / 改色 /
// 转位图（§5.23 顶栏与 §5.23.4 第 7 条）。它与左侧 `login-illustration.svg` 是两件
// 东西：插画是装饰，Logo 是品牌标识，职责独立（§5.23.2 A 第 3 条）。
import xinqingLogo from '../../assets/logo-xinqing.svg'

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

/**
 * 密码显隐（V2.0.1 §5.22.4）。
 *
 * 它**只切 `type`，不碰 `password` 的值**——切换是显示层的事，任何一次切换都不该
 * 让已经输进去的东西发生任何变化（包括长度、trim、编码）。这是一个纯计算属性式的
 * 开关，值本身仍然只有一个来源：那个 `ref`。
 *
 * 默认关闭：共用电脑上（机房是常态）默认明文会把上一个人的密码留在屏幕上。
 */
const showPassword = ref(false)
function togglePassword() {
  showPassword.value = !showPassword.value
}

// 提交失败之后把焦点送回输入框（或按钮）用。这几个 ref 只服务这一件事，不参与取数。
const accountInput = ref<HTMLInputElement | null>(null)
const passwordInput = ref<HTMLInputElement | null>(null)
const submitButton = ref<HTMLButtonElement | null>(null)

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
  { value: 'admin', roleLabel: '系统管理员', fieldLabel: '管理员账号', placeholder: '请输入管理员账号', home: '/admin/overview' }
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
  // 重入守卫。按钮上的 `:disabled` 只挡住**鼠标**那一条路：回车连按两下、
  // 或者以后有人在别处调一次 `submit()`，都会在 `loading` 还没回落时再进来一次。
  // 一次登录发两份请求的后果不只是白跑——两份都可能签发会话（§16.5 的 auth_session），
  // 而后一份会把前一份顶掉，用户拿到的那个 token 与屏幕上的登录动作对不上号。
  if (loading.value) return
  loading.value = true
  error.value = ''
  // 「这一次失败要不要把焦点还回提交按钮」——只由网络类失败置位，见下面的 catch。
  let restoreSubmitFocus = false
  try {
    await login(role.value, account.value.trim(), password.value)
    await router.push(currentRole.value.home)
  } catch (err) {
    error.value = loginFailureMessage(err)
    // 失败后的焦点策略（§5.22.4）：
    // - **认证失败**（账号或密码不对）→ 焦点回**密码框**，因为要改的就是它；
    //   账号是空的时候回账号框（那才是缺的那一项）。
    // - **网络类失败**（`TypeError`）与**响应解析失败**（`SyntaxError`）→ **不指认
    //   任何输入框**：这两种失败重打密码永远不会让它好起来，把焦点抢过去反而像在说
    //   「你的输入有问题」，而真正的问题在别处（后端没起来 / 网关返回了一张 HTML 页）。
    //   但**也不能让它掉到 `<body>`**：提交按钮在请求期间被 `:disabled` 掉，而
    //   disabled 的按钮接不住焦点，浏览器会把它丢给 body（实测：`activeElement`
    //   就是 BODY），键盘用户从此找不到自己在哪。所以只把这**一个位置**复原回去。
    if (err instanceof TypeError || err instanceof SyntaxError) {
      restoreSubmitFocus = true
    } else {
      await nextTick()
      const target = account.value.trim() ? passwordInput.value : accountInput.value
      target?.focus()
    }
  } finally {
    loading.value = false
    if (restoreSubmitFocus) {
      // 必须等 `:disabled` 摘掉再 focus：打在一个 disabled 按钮上的 focus() 是空转。
      await nextTick()
      if (document.activeElement === document.body) submitButton.value?.focus()
    }
  }
}
</script>

<template>
  <main class="login-shell">
    <!-- 桌面：品牌视觉区 + 登录操作区 ≈ 55:45（V2.0.1 §5.22.3）；
         ≤900px 整块收敛成单栏、品牌区隐藏（同一套 DOM，见 styles.css 的媒体查询）。

         左栏里插画**铺满整块**、文字**叠在它上面**（§5.22 追加调整）。所以这一块的
         内部次序是「绝对定位的插画 → 对比度遮罩（`::after`）→ 相对定位的字幕」，
         而字幕因为 `z-index: 1` 落在最上——DOM 里谁先谁后不影响这件事。

         `aria-hidden="true"` 是有意的：这一块是**装饰**，不承载任何业务信息。
         §5.22.5 那条「SVG 不作为任何业务信息的唯一载体」因此是构造上成立的——
         产品名、副标题、角色、表单、隐私说明一个都不在这里。 -->
    <div class="login-split">
      <aside class="login-brand" aria-hidden="true">
        <!-- 冻结资产（V2.0.1 §5.22.2）：只引用，viewBox / path / fill / stroke /
             gradient 一律不动，也不转 PNG。`alt=""` 与上面那个 `aria-hidden`
             是同一句话的两种写法，一起保证它读屏软件里不存在。 -->
        <img class="login-brand-art" src="../../assets/login-illustration.svg" alt="" />
        <div class="login-brand-copy">
          <p class="login-brand-slogan">看见成长，陪伴每一步</p>
          <p class="login-brand-note">
            学校在这里发现需要关注的学生，组织持续的关怀与支持。
          </p>
        </div>
      </aside>

        <section class="login-panel">
          <div class="brand-row">
            <!-- 品牌锁定区（§5.23.2 A）：Logo Mark + 动态品牌名 / 副标题。
                 `alt=""` + `aria-hidden="true"` 是有意的——紧挨着就是一个讲全了的
                 `h1` + 副标题，Logo 再被念一遍是重复信息，而图形里没有文字可念。
                 branding 拉不到时这一块照旧渲染（Logo 不依赖接口，只有文字走 fallback）。 -->
            <div class="brand-mark">
              <img class="brand-logo" :src="xinqingLogo" alt="" aria-hidden="true" />
            </div>
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
              <!-- `name` 与 `type` 是 §5.22.4 那一组「合理设置 input type / autocomplete /
                   name」的落点：`type="text"` 是显式的（此前只有浏览器默认），
                   而 `name` 让密码管理器与表单自动填充认得出这两格是「账号 / 密码」。 -->
              <input
                ref="accountInput"
                v-model="account"
                name="account"
                type="text"
                autocomplete="username"
                :placeholder="currentRole.placeholder"
              />
            </label>
            <label>
              <span>密码</span>
              <!-- 显隐按钮与输入框同在这个 `<label>` 里（`<label>` 的隐式关联包含它），
                   所以它**不能**再叫「登录」——`getByRole(name)` 是子串匹配，
                   而那会把 `getByRole('button', {name:'登录'})` 变成 strict mode 冲突。
                   取名「显示 / 隐藏」+ 翻转式 `aria-label`，不加 `aria-pressed`：
                   翻转的 `aria-label` 本身已经说出了当前状态，再加一个按下态就是两个信号。 -->
              <span class="password-field">
                <input
                  ref="passwordInput"
                  v-model="password"
                  name="password"
                  :type="showPassword ? 'text' : 'password'"
                  autocomplete="current-password"
                  placeholder="请输入密码"
                />
                <button
                  class="password-toggle"
                  type="button"
                  :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                  @click="togglePassword"
                >
                  {{ showPassword ? '隐藏' : '显示' }}
                </button>
              </span>
            </label>
            <!-- `role="alert"`（§5.22.4「错误信息以表单内可见、可被辅助技术感知的方式呈现」）：
                 它此前只是一个 `<p>`，读屏软件在焦点离开表单时不会主动念出来。
                 `aria-live` 由 `alert` 隐含，不另写。 -->
            <p v-if="error" class="form-error" role="alert">{{ error }}</p>
            <button ref="submitButton" class="primary-action" :disabled="loading" type="submit">
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
    </div>
  </main>
</template>
