<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Modal from '../../components/Modal.vue'
import ConfirmDialog from '../../components/ConfirmDialog.vue'
import FormDialog, { type FormField } from '../../components/FormDialog.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import ErrorState from '../../components/ErrorState.vue'
import DataTable, { type Column } from '../../components/DataTable.vue'
import { showToast } from '../../services/toast'
import { ROLE_LABELS, ROLE_ORDER, accountScopeLabel } from '../../services/labels'
import {
  getBranding,
  getMe,
  getAccounts,
  getScopeOptions,
  createAccount,
  updateAccount,
  resetAccountPassword,
  getPermissionMatrix,
  updatePermissionMatrix,
  type AccountItem,
  type ScopeOption,
  type UpdateAccountPayload
} from '../../services/api'

/**
 * 账号与权限 —— 系统管理员的全部工作面（2026-09-17 收窄）。
 *
 * 这一页原来叫「系统管理」，装着学生导入、学生列表、题库导入、账号管理、审计日志
 * 和权限矩阵——其中三块在别的地方各有一个更完整的同名页面（`/admin/organization`
 * 有学生名册、`/admin/scale` 有题库版本、`/admin/audit` 有带筛选的审计），而且那两个
 * 页面上的按钮还指回这里。一个页面同时是「唯一入口」和「半个副本」，两边就都会坏。
 *
 * 现在只留下**真正属于系统级配置**的两件事：账号，和账号能做什么。名字也跟着换掉——
 * 「系统管理」这个名字本身就是吸附剂，它把「和系统有关的」都吸了进来。
 * 测评任务是学校业务，同理不在这里（见 docs/phase0_rule_freeze.md §7）。
 */
const router = useRouter()
const route = useRoute()

const accountColumns: Column[] = [
  { key: 'display_name', label: '姓名', sortable: true },
  { key: 'account', label: '账号', sortable: true },
  // 枚举列必须显式声明中文序（`Column.order`，见 §3 第四面）：不声明的话，
  // 这一列按 role_code 的**字母序**排，得到 系统管理员 → 心理老师 → 德育领导 → 学生，
  // 而它看起来只是个正常的升序。
  { key: 'role_code', label: '角色', sortable: true, order: ROLE_ORDER },
  // 不可排序：这一列的取值是一个数组（一个账号可以有多段范围），拿它去
  // `localeCompare` 排出来的是数组的字符串形式，没有意义。
  { key: 'scopes', label: '数据范围' },
  { key: 'must_change_password', label: '密码状态', sortable: true },
  { key: 'active', label: '状态', sortable: true },
  { key: 'actions', label: '操作' }
]

const accounts = ref<AccountItem[]>([])
const loading = ref(true)
const error = ref('')

/**
 * 账号列表的搜索词。
 *
 * 这一页此前没有搜索框，而它**从第一天起就是分页的**（`:page-size="10"`）：
 * 测试种子会给每个测试学生建账号，一所学校的真实名册只会更多，所以「在某一行上
 * 点编辑 / 停用」实际上要先翻页翻到那一行。刚建出来的那个账号 id 最大、落在最后一页。
 *
 * 按 姓名 / 账号 两列匹配（角色与范围是编码与数组，用中文搜它们得先想清楚搜的是什么）。
 */
const accountQuery = ref('')

/**
 * 「哪一类账号」这一档筛选（V2.0.0 §5.14.5）。
 *
 * 管理员系统概览上那三张卡片（未配置数据范围 / 需修改密码 / 已停用）就是带着
 * `?account=unconfigured` 之类点到这一页来的，所以 **`ACCOUNT_FILTER_PREDICATES`
 * 里的判据与那三张卡片的计算属性必须逐字相同**——§11：指标卡上的数必须与它点进去
 * 的那个列表同源。卡片数的是「`scopes` 为空的账号」，这里筛的就得是同一个谓词；
 * 写宽一个字（比如改成按「范围里没有某一段」去数），两个数就会在某一天各说各话，
 * 而屏幕上两边看起来都对。
 *
 * 它与搜索框是**叠加**关系，不是替代：搜索问「哪个人」，这一档问「哪一类账号」，
 * 两个问题可以同时问。
 *
 * 初值**走白名单**，认不出就当没筛：查询参数是用户敲得出来的
 * （`?account=随便什么`），直接拿它当筛选值会落到一个谁都不认识的键上，而那一支的
 * 谓词是空——列表整片变空，看起来像「一个账号都没有」。宁可显示全部，不要显示一个
 * 假装是「空」的页面（§9：取不到就不猜；这里猜得出用户想要什么，只是他不该因此
 * 拿到一张空表）。
 *
 * 只读一次初值、切换时**不回写 URL**：这一页的筛选是「看一眼就走」的（从卡片点进来、
 * 处理完那几个人、离开），把每次切换都写进历史会让「后退」退不出这一页。审计页那个
 * 服务端筛选不同——那边翻页要能分享链接。
 */
type AccountFilter = 'all' | 'unconfigured' | 'must_change' | 'inactive'

const ACCOUNT_FILTERS: Array<{ value: AccountFilter; label: string }> = [
  { value: 'all', label: '全部账号' },
  { value: 'unconfigured', label: '未配置数据范围' },
  { value: 'must_change', label: '需修改密码' },
  { value: 'inactive', label: '已停用' }
]

/** 筛选码 → 谓词。下拉框与列表都从这里取，所以「筛的是什么」只有一处定义。 */
const ACCOUNT_FILTER_PREDICATES: Record<AccountFilter, (a: AccountItem) => boolean> = {
  all: () => true,
  unconfigured: (a) => !a.scopes?.length,
  must_change: (a) => a.must_change_password,
  inactive: (a) => !a.active
}

const initialAccountFilter = (): AccountFilter => {
  const wanted = String(route.query.account ?? '')
  return ACCOUNT_FILTERS.find((f) => f.value === wanted)?.value ?? 'all'
}

const accountFilter = ref<AccountFilter>(initialAccountFilter())

const filteredAccounts = computed(() => {
  const q = accountQuery.value.trim().toLowerCase()
  const matchesFilter = ACCOUNT_FILTER_PREDICATES[accountFilter.value]
  return accounts.value.filter((a) => {
    if (!matchesFilter(a)) return false
    if (!q) return true
    return a.display_name.toLowerCase().includes(q) || a.account.toLowerCase().includes(q)
  })
})

/**
 * 空态按「用户在问什么」分岔（§14）。
 *
 * 「暂无账号」与「没有未配置数据范围的账号」回答的是两个不同的问题，而后者其实是个
 * **好消息**（说明每个账号都配了范围）——两者共用一句「暂无账号」时，管理员会去查
 * 是不是哪里坏了。
 */
const accountsEmptyText = computed(() => {
  if (accountQuery.value.trim()) return '没有匹配的账号'
  const label = ACCOUNT_FILTERS.find((f) => f.value === accountFilter.value)?.label
  return accountFilter.value === 'all' ? '暂无账号' : `没有「${label}」的账号。`
})

/** 筛选是否偏离「全部」——用于决定要不要显示「清除筛选」。 */
const accountFilterActive = computed(() => accountFilter.value !== 'all')

/**
 * 清除筛选：本地状态与 **URL** 一起清（V2.0.0 §5.14.7 发现 ①）。
 *
 * 此前只重置本地那一个 ref，地址栏里那串 `?account=unconfigured` 留在原地——而
 * `initialAccountFilter()` 进门读的正是它（概览那张卡片下钻时带过来的）。于是
 * 「清除筛选 → 刷新」会把刚清掉的那一档原样装回来，而屏幕上刚刚显示的是「全部
 * 账号」：URL 与页面的口径各说各话，且刷新是用户验证「我清干净了没有」最自然的
 * 一个动作。
 *
 * `replace` 而不是 `push`，与 `ProgressPage.clearFilter` 逐字同一条：清除筛选不是
 * 一次「前进」，按返回键应当回到进来之前那一页，而不是在「筛过 / 没筛」之间来回弹。
 */
async function clearAccountFilter() {
  accountFilter.value = 'all'
  await router.replace({ path: '/admin/system' })
}

const showForm = ref(false)
const formTitle = ref('')
const formFields = ref<FormField[]>([])
const formSubmitText = ref('提交')
let formResolve: ((values: Record<string, string>) => void) | null = null

async function load() {
  loading.value = true
  error.value = ''
  try {
    const me = await getMe()
    if (me.role_code !== 'admin') {
      await router.push('/login')
      return
    }
    accounts.value = await getAccounts()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function showFormDialog(title: string, fields: FormField[], submitText = '提交'): Promise<Record<string, string>> {
  formTitle.value = title
  formFields.value = fields
  formSubmitText.value = submitText
  showForm.value = true
  return new Promise((resolve) => { formResolve = resolve })
}

function onFormSubmit(values: Record<string, string>) {
  if (formResolve) formResolve(values)
}

function onFormCancel() {
  if (formResolve) formResolve({})
}

// A freshly minted password is a credential: show it in a dedicated dialog the
// operator dismisses, never in a toast that lingers in the DOM.
const showPasswordResult = ref(false)
/**
 * `note` 是重置密码那条路独有的：服务端把那个人的会话全撤销了，而这件事在屏幕上
 * 必须说出来——两位老师同时在用同一个账号、其中一位被重置密码，另一位会突然掉线，
 * 而他会以为是系统坏了。新建账号没有这一句（那个人还没有任何会话）。
 */
const resetResult = ref<{ title: string; name: string; password: string; note?: string } | null>(null)

/**
 * 复制临时密码（V2.0.0 §5.14.5）。
 *
 * 密码是一串系统生成的随机字符，**手抄一遍就是一次抄错的机会**，而抄错的代价是
 * 「登录不上」——用户会一直重打密码。所以这一格旁边要有一个复制按钮。
 *
 * **两条路，因为部署现场多半是 http。** `navigator.clipboard` 只在安全上下文里存在
 * （https 或 localhost），而局域网用法下操作员打开的是 `http://内网IP:<端口>`
 * （`部署说明.txt` 里给老师念的就是这个地址）——那时 `navigator.clipboard` 是
 * `undefined`，直接调它会抛 `Cannot read properties of undefined`，而那句话对用户
 * 毫无意义。所以有一条回退。
 *
 * 反馈用 `copiedPassword` 这个 ref 而**不是定时器**：定时器要在组件卸载时清掉，
 * 而这一层被卸载的路径（弹层关闭、切页）恰恰是最容易漏的那一条（§15 那条
 * 「面板在打开状态下被卸载也要解锁」是同一个坑）。这个 ref 由 `closePasswordResult`
 * 一并归零，与密码本身同寿。
 */
const copiedPassword = ref(false)

/**
 * 非安全上下文下的复制。
 *
 * 那个 textarea **不能**用 `display:none` / `visibility:hidden`：那样 `select()`
 * 选不中内容，而 `document.execCommand('copy')` **照样返回 `true`**——复制到的是
 * 空串，屏幕上却说「已复制」，用户去粘贴时拿到一片空白。所以是挪到视口外面。
 *
 * 用完在 `finally` 里摘掉：DOM 上不留副本（§5.14.5 验收第 4 条点名了「DOM 持久
 * 残留」）。它也不进控制台、不进审计——`detail` 是会被导出的文本，密码一律不进去。
 */
function legacyCopy(text: string): boolean {
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '-1000px'
  document.body.appendChild(area)
  try {
    area.select()
    return document.execCommand('copy')
  } finally {
    document.body.removeChild(area)
  }
}

async function copyPassword() {
  const password = resetResult.value?.password
  if (!password) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(password)
    } else if (!legacyCopy(password)) {
      throw new Error('copy unavailable')
    }
    copiedPassword.value = true
  } catch {
    // 复制失败**不是一次操作的失败**：密码还在屏幕上，手选一样能复制。所以是一句
    // 指路的 toast，不是错误状态、更不是把弹层关掉。
    showToast('error', '复制失败，请手动选中密码后复制')
  }
}

/**
 * 关闭弹层 = 这一次展示结束。
 *
 * `v-if` 已经把 DOM 摘掉了，但 `resetResult` 这个 ref 里**还留着那串密码**，直到
 * 下一次创建/重置把它覆盖。§5.14.5 的验收第 4 条点名的是「DOM 持久残留」，而在内存
 * 里多留一份是同一件事的另一半——顺手清掉它，代价是一个赋值。
 */
function closePasswordResult() {
  showPasswordResult.value = false
  resetResult.value = null
  copiedPassword.value = false
}

async function resetPassword(account: AccountItem) {
  const values = await showFormDialog(
    `重置 ${account.display_name} 密码`,
    [
      { key: 'purpose', label: '用途说明', type: 'text', required: true, placeholder: '请输入重置密码的用途' },
      // No defaultValue: pre-filling a known weak credential invites one-click reset to 123456.
      { key: 'temp_password', label: '临时密码', type: 'password', required: true, placeholder: '请设置临时密码（至少 6 位）' }
    ],
    '重置密码'
  )
  if (!values.purpose || !values.temp_password) return
  try {
    const result = await resetAccountPassword(account.id, values.temp_password, values.purpose)
    resetResult.value = {
      title: '临时密码已重置',
      name: account.display_name,
      // 服务端不回传明文，这里显示的是操作员刚在表单里敲的那一个。
      password: values.temp_password,
      note:
        result.revoked_sessions > 0
          ? `该账号原来的登录会话已全部撤销（${result.revoked_sessions} 条），此刻在其它设备上已经掉线，需要用这个密码重新登录。`
          : '该账号此前没有登录中的会话。'
    }
    showPasswordResult.value = true
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '重置密码失败')
  }
}

// --- 建号 / 改号 / 停用（2026-09-17 补）---
//
// 这一块此前完全没有：心理老师与德育领导的账号只能来自 `db/seed.py`，于是一所学校
// 拿到这套系统之后加不了第二位心理老师。学生那一侧一直有入口（名册导入），
// 员工这一侧一个都没有。
//
// 三件事在界面上必须说清楚，因为它们在服务端是硬规则而在屏幕上不可见：
//   · **数据范围是必填的**。没有范围的账号登录得进来，却一个学生也看不到
//     （`security/data_scope.py` 的恒假谓词）。这一条比「少配了一项」严重：
//     它造出来的是一个**看起来正常但什么都没接上**的账号，所以那一格下面有一行小字。
//   · **角色与账号创建后不可改**。录错了就停用那一个、另建一个——那一步会留下两条
//     审计，比一条静默改写的轨迹更接近发生过的事。
//   · **停用不是删除**。账号没有删除接口，历史记录（审计、复核、跟进）都不随账号走。

/** 员工角色 = 可建的那三个。中文取自 `labels.ts`（§3 的唯一映射层），学生不在其中。 */
const STAFF_ROLE_OPTIONS = ['counselor', 'leader', 'admin'].map((code) => ({
  value: code,
  label: ROLE_LABELS[code]
}))

/**
 * 一个范围行 → 下拉框里的那个值。
 *
 * 用哪一列当 id 取决于 `scope_type`（`user_scope` 四个维度各一列，另一个约定见 §9）。
 * 认不出的类型给空串，于是下拉框落回「请选择」——不猜一个 id。
 */
function scopeValue(scope: {
  scope_type: string
  school_id: number | null
  grade_id: number | null
  class_id: number | null
  student_id: number | null
}): string {
  const ids: Record<string, number | null> = {
    SCHOOL: scope.school_id,
    GRADE: scope.grade_id,
    CLASS: scope.class_id,
    STUDENT: scope.student_id
  }
  const id = ids[scope.scope_type]
  return id == null ? '' : `${scope.scope_type}:${id}`
}

function parseScopeValue(value: string): { scope_type: string; scope_id: number } {
  const [scope_type, id] = value.split(':')
  return { scope_type, scope_id: Number(id) }
}

/** 「年级 · 初一」。名称来自服务端（要查库），类型那两个中文字来自 `labels.ts`。 */
function scopeOptionLabel(option: { scope_type: string; name: string }) {
  return `${accountScopeLabel(option.scope_type)} · ${option.name}`
}

/**
 * 一段范围 → 一枚标签上的字（V2.0.0 §5.14.5）。
 *
 * 抽出来是为了**一处定义、两个读者**：列表那一格（多枚标签）与下面拼句那一个
 * （编辑弹层的提示语）。此前只有拼句那一个读者，所以判断逻辑写在它体内；现在列表
 * 要逐段渲染，各写一份必然漂——而漂出来的样子是「列表显示『全校』、提示语显示
 * 『全校 · 青禾实验学校』」，两句话说的是同一段范围。
 */
function scopeTagLabel(scope: NonNullable<AccountItem['scopes']>[number]): string {
  return scope.name
    ? scopeOptionLabel({ scope_type: scope.scope_type, name: scope.name })
    : accountScopeLabel(scope.scope_type)
}

/** 一个账号的全部范围拼成一句。多段是并列（谓词用 `or_` 合并，§9）。 */
function accountScopeText(account: AccountItem): string {
  return (account.scopes ?? []).map(scopeTagLabel).join('、')
}

async function scopeChoices(): Promise<FormField['options']> {
  const options: ScopeOption[] = await getScopeOptions()
  return options.map((option) => ({
    value: `${option.scope_type}:${option.scope_id}`,
    label: scopeOptionLabel(option)
  }))
}

async function newAccount() {
  let choices: FormField['options']
  try {
    choices = await scopeChoices()
  } catch (err) {
    // 拉不到选项就不要把这个弹层打开：一个空的范围下拉框会把「必填」变成死路。
    showToast('error', err instanceof Error ? err.message : '读取数据范围失败')
    return
  }
  const values = await showFormDialog(
    '新建账号',
    [
      {
        key: 'role_code',
        label: '角色',
        type: 'select',
        required: true,
        options: STAFF_ROLE_OPTIONS,
        hint: '学生账号不在这里创建：学生跟着名册一起生成（组织学生 → 学生信息导入），在那里他才有学号和测评记录。'
      },
      { key: 'display_name', label: '姓名', type: 'text', required: true, placeholder: '如：王老师' },
      {
        key: 'account',
        label: '登录账号',
        type: 'text',
        required: true,
        placeholder: '心理老师 / 德育领导填手机号，系统管理员填自定义账号'
      },
      {
        key: 'temp_password',
        label: '临时密码',
        type: 'password',
        required: true,
        placeholder: '请设置临时密码（至少 6 位）'
      },
      {
        key: 'scope',
        label: '数据范围',
        type: 'select',
        required: true,
        options: choices,
        hint: '这一项决定他能看到哪些学生。不配范围的账号登录得进来，却一个学生也看不到。'
      }
    ],
    '创建账号'
  )
  if (!values.account) return
  try {
    const created = await createAccount({
      role_code: values.role_code,
      display_name: values.display_name,
      account: values.account.trim(),
      temporary_password: values.temp_password,
      scopes: [parseScopeValue(values.scope)]
    })
    // 密码是操作员自己敲的，服务端不回传——弹层显示的就是他刚填的那一个，
    // 让他有机会在关掉之前确认一遍。
    resetResult.value = {
      title: '账号已创建',
      name: `${created.display_name} · ${created.account}`,
      password: values.temp_password
    }
    showPasswordResult.value = true
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '创建账号失败')
  }
}

async function editAccount(account: AccountItem) {
  let choices: FormField['options']
  try {
    choices = await scopeChoices()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '读取数据范围失败')
    return
  }

  const scopes = account.scopes ?? []
  const first = scopes.length ? scopeValue(scopes[0]) : ''
  // 下拉框一次只能表达一段范围。一个账号可以有多段（谓词用 `or_` 合并，§9），
  // 所以这里必须说出来：否则「改个错别字」会把一位老师从两个班缩成一个班，
  // 而他不会收到任何提示。
  const hint = scopes.length > 1
    ? `这个账号现在有 ${scopes.length} 段范围（${accountScopeText(account)}），上面显示的是第一段。改动之后保存，其余 ${scopes.length - 1} 段会被移除。`
    : '保存后这个账号的数据范围就是上面这一项。'

  const values = await showFormDialog(
    `编辑 ${account.display_name}`,
    [
      { key: 'display_name', label: '姓名', type: 'text', required: true, defaultValue: account.display_name },
      { key: 'scope', label: '数据范围', type: 'select', options: choices, defaultValue: first, hint }
    ],
    '保存修改'
  )
  if (!values.display_name) return

  // 只发真的动过的那几项。原样发回去的 `scopes` 会把多段范围压成一段
  // （服务端是替换语义），而「没改」不应该产生这个后果。
  const payload: UpdateAccountPayload = {}
  if (values.display_name.trim() !== account.display_name) payload.display_name = values.display_name.trim()
  if (values.scope && values.scope !== first) payload.scopes = [parseScopeValue(values.scope)]
  if (!Object.keys(payload).length) {
    showToast('info', '没有改动')
    return
  }
  try {
    await updateAccount(account.id, payload)
    showToast('success', '账号已更新')
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '更新账号失败')
  }
}

const showActiveConfirm = ref(false)
const activeTarget = ref<AccountItem | null>(null)

function toggleActive(account: AccountItem) {
  activeTarget.value = account
  showActiveConfirm.value = true
}

/**
 * 行内操作收进一个统一菜单（V2.0.0 §5.14.5）。
 *
 * 此前三枚按钮（编辑 / 重置密码 / 停用·启用）直接铺在「操作」那一列里，于是同一行
 * 里既有普通动作又有**危险动作**，而屏幕上没有任何东西把「停用」的后果说出来——
 * 那句话住在一个只有点下去才出现的弹层里（「停用后…无法登录，已登录的会话下一个
 * 请求就会失效」）。收进菜单之后，危险动作不再与普通动作用同一种外观挤在一行。
 *
 * **做成弹层，不做行内下拉**：表格外层是 `overflow: auto` 的 `.table-wrap`，一个
 * 绝对定位的下拉会被裁掉（滚到下半页的行尤其明显——菜单出现在表格上边缘之外）。
 * 弹层还自带焦点陷阱、`aria-modal` 与 Esc（§15），行内下拉要另写一整套键盘模型。
 *
 * 三枚按钮各自**先收起菜单再开下一层**：两层弹层叠着虽然能被 §22 那套次序排对，
 * 但「上一层的按钮还看得见」会让用户以为点错了、再点一次。
 */
const actionsTarget = ref<AccountItem | null>(null)

function openActions(account: AccountItem) {
  actionsTarget.value = account
}

function actionsEdit(account: AccountItem) {
  actionsTarget.value = null
  void editAccount(account)
}

function actionsResetPassword(account: AccountItem) {
  actionsTarget.value = null
  void resetPassword(account)
}

function actionsToggleActive(account: AccountItem) {
  actionsTarget.value = null
  toggleActive(account)
}

async function commitActiveToggle() {
  const account = activeTarget.value
  if (!account) return
  try {
    await updateAccount(account.id, { active: !account.active })
    showToast('success', account.active ? `${account.display_name} 已停用，无法再登录` : `${account.display_name} 已启用`)
    await load()
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '操作失败')
  } finally {
    activeTarget.value = null
  }
}

// --- 角色权限矩阵 ---
//
// 这一块 2026-09-17 重做。原来那版是 5 行 × 4 列的表格，20 个下拉框**每一个**都列出
// 全部 12 个等级，于是 `学生 / 组织与账号 = 管理` 这类组合既可选又可存——存进去不报错，
// 只是永远不会被任何端点认出来（`scope_allows` 只做集合成员判断）。现在：
//   · 每个下拉框只列该项能力真正接受的等级，顺序即宽松度（后端 CAPABILITY_LEVELS）；
//   · 每一档下面写着它意味着什么，同屏可见，不用去猜「仅摘要」和「授权范围」的差别；
//   · 只提交动过的格子——没碰过的继续跟随出厂配置，而不是被钉成一条显式记录；
//   · 改动清单就在保存按钮上方，放宽的会标出来。
const showPermissions = ref(false)
const permissionRows = ref<Array<{ capability_key: string; label: string; roles: Record<string, string> }>>([])
const scopeLabels = ref<Record<string, string>>({})
const capabilityLevels = ref<Record<string, string[]>>({})
const levelDescriptions = ref<Record<string, Record<string, string>>>({})
/** 出厂矩阵：用来标「已改」，也是「恢复默认」的目标值。 */
const permissionDefaults = ref<Record<string, Record<string, string>>>({})
/** 打开弹窗时的生效矩阵：判断「动过没有」的基线。 */
const permissionBaseline = ref<Record<string, string>>({})
const permissionDraft = ref<Record<string, string>>({})
const permissionSaving = ref(false)
const permissionLoaded = ref(false)

const ROLES: Array<{ code: string; label: string }> = [
  { code: 'counselor', label: '心理老师' },
  { code: 'leader', label: '德育领导' },
  { code: 'admin', label: '系统管理员' },
  { code: 'student', label: '学生' }
]

/**
 * 按角色查看（V2.0.0 §5.14.5）。
 *
 * **默认必须是「全部」**，这不是审美偏好：一屏看完四个角色本来就是这张矩阵的做法
 * （四项能力各自四个格并排，缺一格就看不出「这一项谁有谁没有」），而收起是给
 * 「我只想确认某一位老师有没有多出什么」准备的。全站也有两条 e2e 按四列同屏断言
 * （`admin can open the role permission matrix` 数 `.perm-cell` 恰好 4 个、
 * `matrix reflects the backend defaults` 依次读第 0..3 个下拉框），默认改成单角色
 * 会一起红——而它们红得对：那两条断的正是「四个角色同屏可读」。
 *
 * 单角色时**只渲染那一列**（`.perm-cell` 恰好 1 个），不是把另外三列藏起来——
 * 藏起来的格子仍然在 DOM 里，读屏软件会念到三组「这个角色看不到的内容」。
 */
const permissionRoleFilter = ref<string>('all')

const visibleRoles = computed(() =>
  permissionRoleFilter.value === 'all'
    ? ROLES
    : ROLES.filter((r) => r.code === permissionRoleFilter.value)
)

/** 视图里当前有「已改」的格子吗——用于给「恢复默认」一个不至于白按的提示。 */
const customisedCount = computed(() => {
  let n = 0
  for (const row of permissionRows.value) {
    for (const role of ROLES) {
      if (isCustomised(row.capability_key, role.code)) n += 1
    }
  }
  return n
})

function cellKey(capability: string, role: string) {
  return `${capability}::${role}`
}

/**
 * 这一格能选哪些等级。
 *
 * 库里可能存着一个今天已经不在这项能力定义域里的旧值（旧版界面放进去的，或某次
 * 定义域收窄之后留下的）。它不能被静默吞掉——原样列进选项里，否则下拉框渲染成
 * 空白，看起来像「这一格没配过」，而实际上它配着某个正在生效的东西。
 * 顺序上追加在末尾，且 `isWidening` 只比较**定义域内**的下标，所以它不会被误读成放宽。
 */
function levelsFor(capability: string): string[] {
  const levels = capabilityLevels.value[capability] || []
  const legacy: string[] = []
  for (const role of ROLES) {
    const current = permissionDraft.value[cellKey(capability, role.code)]
    if (current && !levels.includes(current) && !legacy.includes(current)) legacy.push(current)
  }
  return [...levels, ...legacy]
}

function levelLabel(level: string) {
  return scopeLabels.value[level] || level
}

function levelDescription(capability: string, level: string) {
  return levelDescriptions.value[capability]?.[level] || ''
}

function roleLabel(role: string) {
  return ROLES.find(r => r.code === role)?.label || role
}

function capabilityLabel(capability: string) {
  return permissionRows.value.find(r => r.capability_key === capability)?.label || capability
}

/** 相对**出厂**配置动过没有。 */
function isCustomised(capability: string, role: string) {
  return permissionDraft.value[cellKey(capability, role)] !== permissionDefaults.value[capability]?.[role]
}

/** 相对**打开弹窗时**动过没有。只有这些格子会被提交。 */
function isDirty(capability: string, role: string) {
  return permissionDraft.value[cellKey(capability, role)] !== permissionBaseline.value[cellKey(capability, role)]
}

const dirtyCells = computed(() => {
  const cells: Array<{ capability: string; role: string; from: string; to: string }> = []
  for (const row of permissionRows.value) {
    for (const role of ROLES) {
      if (!isDirty(row.capability_key, role.code)) continue
      cells.push({
        capability: row.capability_key,
        role: role.code,
        from: permissionBaseline.value[cellKey(row.capability_key, role.code)],
        to: permissionDraft.value[cellKey(row.capability_key, role.code)]
      })
    }
  }
  return cells
})

/** 比打开时的配置更宽吗？下标即宽松度，列表从紧到松（后端 CAPABILITY_LEVELS）。 */
function isWidening(cell: { capability: string; from: string; to: string }) {
  const levels = capabilityLevels.value[cell.capability] || []
  const from = levels.indexOf(cell.from)
  const to = levels.indexOf(cell.to)
  if (from < 0 || to < 0) return false
  return to > from
}

const wideningCount = computed(() => dirtyCells.value.filter(isWidening).length)

/** 恢复出厂配置：写进草稿并标成待保存，不直接提交。 */
function restoreDefaults() {
  const draft = { ...permissionDraft.value }
  for (const row of permissionRows.value) {
    for (const role of ROLES) {
      const fallback = permissionDefaults.value[row.capability_key]?.[role.code]
      if (fallback != null) draft[cellKey(row.capability_key, role.code)] = fallback
    }
  }
  permissionDraft.value = draft
}

/**
 * 「恢复默认」的二次确认（V2.0.0 §5.14.5）。
 *
 * 它**不是**一个破坏性动作：`restoreDefaults` 只改草稿，真正的写入仍然要按「保存
 * 配置」并写审计（全站只有 `PUT /admin/permissions` 一个写入口，没有独立的 reset
 * 端点）。所以这句确认的措辞必须说清这一点——否则用户以为按下去就已经生效，关掉
 * 弹层走人，而什么也没保存。
 *
 * 那为什么还要确认：它会把**用户手上所有未保存的改动**一起冲掉（包括他刚刚一小格
 * 一小格调出来的那些），而屏幕上唯一的痕迹是那个「保存配置（N 项）」上的数字变小。
 * 第一次点击表达的是意图（我要回到出厂值），这一次表达的是代价。
 */
const showRestoreConfirm = ref(false)

const restoreConfirmMessage = computed(() => {
  const changed = dirtyCells.value.length
  return changed
    ? `上面 ${changed} 处尚未保存的改动会被丢弃，草稿回到出厂配置。这不会立刻写入任何配置——还要再点一次「保存配置」才生效。`
    : '草稿会回到出厂配置。这不会立刻写入任何配置——还要再点一次「保存配置」才生效。'
})

/**
 * 打开二次确认。真正的改写发生在 `commitRestoreDefaults` 里——**确认之后才发生**，
 * 所以这一枚按钮按下去只是问一句，草稿此刻一个字没动。
 */
function askRestoreDefaults() {
  showRestoreConfirm.value = true
}

function commitRestoreDefaults() {
  restoreDefaults()
  showRestoreConfirm.value = false
}

async function openPermissions() {
  showPermissions.value = true
  permissionSaving.value = false
  permissionLoaded.value = false
  try {
    const matrix = await getPermissionMatrix()
    permissionRows.value = matrix.items
    scopeLabels.value = matrix.scope_labels
    capabilityLevels.value = matrix.capability_levels
    levelDescriptions.value = matrix.level_descriptions
    permissionDefaults.value = matrix.defaults

    const baseline: Record<string, string> = {}
    const draft: Record<string, string> = {}
    for (const row of matrix.items) {
      for (const role of ROLES) {
        const key = cellKey(row.capability_key, role.code)
        const effective = row.roles[role.code] || 'NONE'
        baseline[key] = effective
        draft[key] = effective
      }
    }
    permissionBaseline.value = baseline
    permissionDraft.value = draft
    permissionLoaded.value = true
  } catch (err) {
    showToast('error', err instanceof Error ? err.message : '读取权限矩阵失败')
    showPermissions.value = false
  }
}

async function savePermissions() {
  // 只提交动过的格子。整批覆盖会把没碰过的格子也钉成显式行，从此它们不再跟随出厂
  // 配置走——而那正是 CAPABILITY_DEFAULTS 存在的意义。
  const entries = dirtyCells.value.map(cell => ({
    role_code: cell.role,
    capability_key: cell.capability,
    scope_level: cell.to
  }))
  if (!entries.length) return
  permissionSaving.value = true
  try {
    await updatePermissionMatrix(entries)
    showToast('success', `权限配置已保存（${entries.length} 项变更）`)
    showPermissions.value = false
  } catch (err) {
    // e.g. the lockout guard rejecting ADMIN/ORG_ACCOUNT = NONE
    showToast('error', err instanceof Error ? err.message : '保存权限配置失败')
  } finally {
    permissionSaving.value = false
  }
}

/**
 * 系统版本，只给页脚那一行用。
 *
 * 取不到就**整句不出现**（`v-if`），不显示空串、也不猜一个 ——
 * 与「全部学生」页头那句范围宣称同一条约定（§9）：一个说不清的版本号
 * 比没有版本号更糟，因为操作员会照着它报故障。
 */
const versionLabel = ref('')

async function loadVersionLabel() {
  try {
    versionLabel.value = (await getBranding()).version || ''
  } catch {
    // 非致命：页脚本来的职责是账号与权限，页脚缺一行不该让它失败。
    versionLabel.value = ''
  }
}

onMounted(load)
onMounted(loadVersionLabel)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <!-- eyebrow 跟着 h1 一起从「系统管理」改成「身份与权限」（2026-09-17）：
             名字里留着「系统」二字，这一页就还会接着吸别的段落进来。 -->
        <div class="eyebrow">身份与权限</div>
        <h1>账号与权限</h1>
        <p class="page-desc">
          系统管理员只管理身份、账号与配置；系统管理权不等于心理数据查看权。
          名册与导入在「组织学生」，题库与评分规则在「量表题库」。
        </p>
      </div>
      <div class="actions">
        <button class="btn primary" type="button" @click="newAccount">新建账号</button>
        <button class="btn" type="button" @click="openPermissions">配置权限</button>
      </div>
    </div>

    <div class="grid">
      <SkeletonBlock v-if="loading" variant="cards" :rows="2" />
      <ErrorState v-if="error && !loading" :message="error" :on-retry="load" />

      <!-- 这一块此前**完全没有守卫**：加载中骨架与「暂无账号」同屏，失败时红条与
           「暂无账号」同屏。`SkeletonBlock` / `ErrorState` 两个组件本身都是对的，
           出问题的是调用方没让它们互斥。 -->
      <section v-if="!loading && !error" class="card pad">
        <h2>账号管理</h2>
        <div class="toolbar">
          <div class="search-box">
            <input v-model="accountQuery" placeholder="搜索姓名或账号" />
          </div>
          <!-- 「哪一类账号」这一档（V2.0.0 §5.14.5）。系统概览上那三张卡片就是带着
               `?account=…` 点到这一页来的，所以选项文字与那三张卡片的标题逐字一致
               ——用户在卡片上看到「未配置数据范围」，落到这里必须还是那几个字。
               与搜索框是**叠加**关系：搜索问「哪个人」，这一档问「哪一类账号」。 -->
          <select v-model="accountFilter" class="select" aria-label="按账号状态筛选">
            <option v-for="option in ACCOUNT_FILTERS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
          <!-- 筛选偏离「全部」时给一条退路：从卡片点进来的人处理完那几个人之后，
               未必记得自己是怎么走到这张表上的，而屏幕上只剩下一个变短了的列表。 -->
          <button v-if="accountFilterActive" class="btn small" type="button" @click="clearAccountFilter">
            清除筛选
          </button>
          <!-- 只在筛选或搜索生效时出数字：都不生效时这个数就是「共 N 条」那一句，
               重复一遍没有信息。 -->
          <span v-if="accountQuery.trim() || accountFilterActive" class="muted tiny">
            {{ filteredAccounts.length }} 个账号
          </span>
        </div>
        <DataTable
          :columns="accountColumns"
          :rows="filteredAccounts"
          row-key="id"
          :page-size="10"
          :empty-text="accountsEmptyText"
        >
          <template #role_code="{ row }">{{ ROLE_LABELS[row.role_code] || row.role_code }}</template>
          <template #scopes="{ row }">
            <!-- 没有范围的账号不是「少填一项」，是**一个看不到任何学生的账号**：
                 谓词在「用户没有范围行」时恒假（§9）。所以它出琥珀色的警告，
                 而不是一个和别的行长得一模一样的 `—`。
                 V2.0.0 §5.14.5 起这一格**不只警告，还给出口**：一枚「配置范围」直接
                 打开这个账号的编辑弹层（那里范围那一项是必填的，§9 记着为什么）。 -->
            <template v-if="!row.scopes?.length">
              <span class="pill amber">未配置 · 看不到任何学生</span>
              <button class="btn small" type="button" style="margin-left:6px" @click="editAccount(row)">
                配置范围
              </button>
            </template>
            <!-- 有范围时逐段一枚标签：一个账号可以有多段范围（谓词用 `or_` 合并，是
                 并集，§9）。此前它们拼成一句「初一 · 1班、初二 · 2班」，读到第三个
                 就要自己数哪一段到哪一段为止——标签把边界画出来。文字一个字没变，
                 变的只是它被包在几枚标签里。 -->
            <span
              v-for="(scope, index) in row.scopes"
              :key="index"
              class="pill gray"
              style="margin-right:4px"
            >
              {{ scopeTagLabel(scope) }}
            </span>
          </template>
          <template #must_change_password="{ row }">
            <span :class="['pill', row.must_change_password ? 'amber' : 'green']">
              {{ row.must_change_password ? '需改密' : '正常' }}
            </span>
          </template>
          <template #active="{ row }">
            <span :class="['pill', row.active ? 'green' : 'gray']">{{ row.active ? '启用中' : '已停用' }}</span>
          </template>
          <template #actions="{ row }">
            <!-- 三枚按钮收进一个菜单（V2.0.0 §5.14.5）。此前它们平铺在同一列里、
                 一样重——而「停用」是代价最高的那一个（账号没有删除接口，停用就是
                 那条路，且它立即生效），却与「编辑」长得一模一样。收进菜单之后
                 危险动作单独用危险色，并且仍然走原来那句确认说明。
                 按钮的**名字**不变（编辑 / 重置密码 / 停用），所以照着名字找它的人
                 只是多点一下。 -->
            <button class="btn small" type="button" @click="openActions(row)">操作</button>
          </template>
        </DataTable>
      </section>
    </div>

    <!-- 系统版本。这一行的用途是**照屏幕报故障**：操作员打电话说「装的是什么版本」，
         照这里念。所以它读服务端发的那一个，不在前端写死 —— 部署包里的
         `frontend/dist` 是预构建的，写死会造出「后端升了、界面还说旧版本」的分岔。
         取不到就整句不出现（`v-if`），不显示半句话。 -->
    <p v-if="versionLabel" class="page-foot">
      心晴 · 中学生心理测评与关怀平台 {{ versionLabel }}
    </p>

    <FormDialog
      :open="showForm"
      :title="formTitle"
      :fields="formFields"
      :submit-text="formSubmitText"
      @submit="onFormSubmit"
      @cancel="onFormCancel"
      @update:open="showForm = $event"
    />

    <!-- 临时密码：一次性展示，关闭即不再可见。新建与重置共用这一块——
         两者都是「操作员刚给出一个密码，此后系统再也读不出来」。 -->
    <Modal
      :model-value="showPasswordResult"
      :title="resetResult?.title || '临时密码'"
      @update:model-value="$event ? (showPasswordResult = true) : closePasswordResult()"
    >
      <div v-if="resetResult" class="form-grid">
        <div class="notice warn">
          请立即通过安全渠道告知该用户，并要求其首次登录后修改密码。关闭本窗口后将无法再次查看。
        </div>
        <div class="notice" v-if="resetResult.note" style="margin-top:10px">{{ resetResult.note }}</div>
        <div class="detail-grid" style="margin-top:14px">
          <div class="detail-row"><span>账号</span><b>{{ resetResult.name }}</b></div>
          <div class="detail-row">
            <span>临时密码</span>
            <b class="password-line">
              <span class="password-value">{{ resetResult.password }}</span>
              <!-- 复制按钮（V2.0.0 §5.14.5）。此前只能靠人眼抄一遍——而密码是
                   一次性展示的，抄错一位的代价是「那个人登不进来」，且他看到的
                   是「账号或密码不正确」，两个人会一起往错的方向查。
                   成功反馈用 ref 而不是定时器：定时器要在卸载时清掉，而这条
                   被卸载的路径（关弹层、切页）恰是最容易漏的那一条（§15 那条）。 -->
              <button class="btn small" type="button" @click="copyPassword">
                {{ copiedPassword ? '已复制' : '复制' }}
              </button>
            </b>
          </div>
        </div>
        <!-- 成功反馈同时也是一句**下一步**：复制成功不等于交代完了。 -->
        <p v-if="copiedPassword" class="muted tiny" style="margin-top:8px">
          已复制到剪贴板。请通过安全渠道发给本人——它只在这里出现这一次。
        </p>
      </div>
      <template #footer>
        <button class="btn primary" @click="closePasswordResult">我已记录</button>
      </template>
    </Modal>

    <ConfirmDialog
      :open="showActiveConfirm"
      :title="activeTarget?.active ? '停用账号' : '启用账号'"
      :message="activeTarget?.active
        ? `停用后 ${activeTarget.display_name}（${activeTarget.account}）将无法登录，已登录的会话下一个请求就会失效。历史记录不受影响，账号随时可以重新启用。`
        : `启用后 ${activeTarget?.display_name} 可以重新登录。`"
      :confirm-text="activeTarget?.active ? '停用' : '启用'"
      :danger="activeTarget?.active"
      @confirm="commitActiveToggle"
      @update:open="showActiveConfirm = $event"
    />

    <!-- 统一操作菜单（V2.0.0 §5.14.5）。做成弹层而不是行内下拉：`.table-wrap` 的
         `overflow` 会把绝对定位的下拉裁掉一半，而弹层自带焦点陷阱、`aria-modal`
         与 Esc（§15）。三枚动作都**先把菜单收起再开下一层**——否则停用确认弹层会
         盖在菜单上面，关掉它之后菜单还开着，看起来像点了一次没反应。 -->
    <Modal
      :model-value="actionsTarget !== null"
      :title="actionsTarget ? `账号操作 · ${actionsTarget.display_name}` : '账号操作'"
      size="sm"
      @update:model-value="!$event && (actionsTarget = null)"
    >
      <div v-if="actionsTarget" class="toolbar action-menu">
        <button class="btn" type="button" @click="actionsEdit(actionsTarget)">编辑</button>
        <button class="btn" type="button" @click="actionsResetPassword(actionsTarget)">重置密码</button>
        <!-- 危险色只标在一个方向：**停用**。启用不是危险动作，所以这两支分开写，
             而不是同一枚按钮换文案——那样「危险色」会跟着文案一起变，而它本该
             只属于停用。停用的那句代价说明仍然由既有的确认弹层给出，一个字没改。 -->
        <button
          v-if="actionsTarget.active"
          class="btn danger"
          type="button"
          @click="actionsToggleActive(actionsTarget)"
        >
          停用
        </button>
        <button v-else class="btn" type="button" @click="actionsToggleActive(actionsTarget)">
          启用
        </button>
      </div>
      <p v-if="actionsTarget?.active" class="muted tiny" style="margin-top:12px">
        停用后该账号将无法登录，已登录的会话下一个请求就会失效。历史记录不受影响，账号随时可以重新启用。
      </p>
    </Modal>

    <Modal
      :model-value="showPermissions"
      title="角色权限矩阵"
      size="lg"
      @update:model-value="showPermissions = $event"
    >
      <SkeletonBlock v-if="!permissionLoaded" variant="table" :rows="5" />

      <template v-else>
        <!-- 按角色查看（V2.0.0 §5.14.5）。默认必须是「全部角色」——四列同屏是这一页
             原本的样子，而「只看一列」是一个**视图**，不是默认。两条既有 e2e 正断着
             默认视图（`.perm-block` 恰好 8 块、每块恰好 4 格），所以默认值是这里唯一
             不能选错的一处。
             切换**只改视图、不动草稿**：被筛掉的那几列如果有未保存的改动，下面「待保存
             的改动」清单照样列着它们（那一块读 `dirtyCells`，与视图无关）——看不见不等于
             没改，把这一点交给渲染之外的那一份数据，比写成一句提示语可靠。
             用下拉而不是一排角色按钮：这一页已有的筛选就是下拉（账号筛选），保持一种
             形状；而且「心理老师」这类名字在登录页是页签，多一排同名按钮会让那些按名字
             定位的用例在多一层的地方命中。 -->
        <div class="toolbar perm-scope">
          <label class="muted tiny" for="permission-role-view">查看</label>
          <select id="permission-role-view" v-model="permissionRoleFilter" class="select">
            <option value="all">全部角色（{{ ROLES.length }} 列）</option>
            <option v-for="role in ROLES" :key="role.code" :value="role.code">
              只看「{{ role.label }}」
            </option>
          </select>
          <span v-if="customisedCount" class="muted tiny">
            当前有 {{ customisedCount }} 格与出厂配置不同
          </span>
        </div>

        <div v-for="row in permissionRows" :key="row.capability_key" class="perm-block">
          <h3>{{ row.label }}</h3>
          <div class="perm-grid">
            <!-- `customised` 是**差异高亮**：与出厂配置不同的格子给一圈琥珀色边。
                 它读的是 `isCustomised`（比的是**出厂值**），与上面那枚「已改」小标
                 同源；而「待保存的改动」清单读的是 `isDirty`（比的是**本次打开时的
                 基线**）——两个问题，两个判据，别把它们合成一个。 -->
            <div
              v-for="role in visibleRoles"
              :key="role.code"
              :class="['perm-cell', { customised: isCustomised(row.capability_key, role.code) }]"
            >
              <div class="perm-cell-head">
                <span>{{ role.label }}</span>
                <span
                  v-if="isCustomised(row.capability_key, role.code)"
                  class="customised-mark"
                  title="与出厂配置不同"
                >已改</span>
              </div>
              <select
                v-model="permissionDraft[cellKey(row.capability_key, role.code)]"
                class="select"
                :aria-label="`${row.label} · ${role.label}`"
              >
                <option
                  v-for="scope in levelsFor(row.capability_key)"
                  :key="scope"
                  :value="scope"
                >
                  {{ levelLabel(scope) }}
                </option>
              </select>
              <p class="muted tiny perm-mean">
                {{ levelDescription(row.capability_key, permissionDraft[cellKey(row.capability_key, role.code)]) }}
              </p>
            </div>
          </div>
        </div>

        <!-- 改动清单就在保存按钮上方：权限是安全设置，按下去之前应当看得见自己要改什么、
             哪几项是放宽。做成弹窗再确认一次是另一种做法，但两层弹窗挡住的恰恰是
             已经做过决定的那个人。 -->
        <div v-if="dirtyCells.length" class="perm-diff">
          <h3>待保存的改动（{{ dirtyCells.length }} 项）</h3>
          <div class="detail-grid">
            <div v-for="cell in dirtyCells" :key="`${cell.capability}::${cell.role}`" class="detail-row">
              <span>{{ capabilityLabel(cell.capability) }} · {{ roleLabel(cell.role) }}</span>
              <b>
                {{ levelLabel(cell.from) }} → {{ levelLabel(cell.to) }}
                <span :class="['pill', isWidening(cell) ? 'amber' : 'gray']">
                  {{ isWidening(cell) ? '放宽' : '收紧' }}
                </span>
              </b>
            </div>
          </div>
          <div v-if="wideningCount" class="notice warn">
            其中 {{ wideningCount }} 项比当前配置更宽，保存后立即对后端鉴权生效。
          </div>
        </div>

        <div class="notice warn" style="margin-top:14px">
          校内角色由登录账号与后端权限共同决定，前端不提供角色切换。修改会实时影响后端鉴权并写入审计。
          「已改」表示该项与出厂配置不同；没动过的格子保存时不会被写入，也就继续跟随出厂配置。
        </div>
      </template>

      <template #footer>
        <!-- 「恢复默认」只是一个**草稿**动作（它不写任何配置），但它会连带冲掉用户
             手上所有未保存的改动——所以按之前问一次，问的是那件事的代价，不是
             「你确定吗」。 -->
        <button class="btn" :disabled="!permissionLoaded" @click="askRestoreDefaults">恢复默认</button>
        <button class="btn" @click="showPermissions = false">取消</button>
        <button
          class="btn primary"
          :disabled="permissionSaving || !dirtyCells.length"
          @click="savePermissions"
        >
          {{ permissionSaving ? '正在保存…' : dirtyCells.length ? `保存配置（${dirtyCells.length} 项）` : '没有改动' }}
        </button>
      </template>
    </Modal>

    <!-- 「恢复默认」的二次确认。它排在权限弹层**之后**声明，但视觉次序由
         `composables/modalStack` 按**打开**次序算（§22）——所以后开的这一层在上面，
         不靠模板位置。 -->
    <ConfirmDialog
      :open="showRestoreConfirm"
      title="恢复出厂配置"
      :message="restoreConfirmMessage"
      confirm-text="恢复默认"
      @confirm="commitRestoreDefaults"
      @update:open="showRestoreConfirm = $event"
    />

  </div>
</template>
