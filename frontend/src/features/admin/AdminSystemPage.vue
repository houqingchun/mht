<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
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
const filteredAccounts = computed(() => {
  const q = accountQuery.value.trim().toLowerCase()
  if (!q) return accounts.value
  return accounts.value.filter(
    (a) => a.display_name.toLowerCase().includes(q) || a.account.toLowerCase().includes(q)
  )
})

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

/** 一个账号的全部范围拼成一句。多段是并列（谓词用 `or_` 合并，§9）。 */
function accountScopeText(account: AccountItem): string {
  const scopes = account.scopes ?? []
  return scopes
    .map((scope) => (scope.name ? scopeOptionLabel({ scope_type: scope.scope_type, name: scope.name }) : accountScopeLabel(scope.scope_type)))
    .join('、')
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
          <!-- 只在搜索时出数字：不搜索时这个数就是「共 N 条」那一句，重复一遍没有信息。 -->
          <span v-if="accountQuery.trim()" class="muted tiny">{{ filteredAccounts.length }} 个账号</span>
        </div>
        <DataTable
          :columns="accountColumns"
          :rows="filteredAccounts"
          row-key="id"
          :page-size="10"
          :empty-text="accountQuery.trim() ? '没有匹配的账号' : '暂无账号'"
        >
          <template #role_code="{ row }">{{ ROLE_LABELS[row.role_code] || row.role_code }}</template>
          <template #scopes="{ row }">
            <!-- 没有范围的账号不是「少填一项」，是**一个看不到任何学生的账号**：
                 谓词在「用户没有范围行」时恒假（§9）。所以它出琥珀色的警告，
                 而不是一个和别的行长得一模一样的 `—`。 -->
            <span v-if="!row.scopes?.length" class="pill amber">未配置 · 看不到任何学生</span>
            <span v-else>{{ accountScopeText(row) }}</span>
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
            <button class="btn small" @click="editAccount(row)">编辑</button>
            <button class="btn small" style="margin-left:6px" @click="resetPassword(row)">重置密码</button>
            <!-- 账号没有删除接口：停用是这条路（`get_current_user` 每次请求都查
                 `active`，所以它立即生效，不等 token 过期）。 -->
            <button class="btn small" style="margin-left:6px" @click="toggleActive(row)">
              {{ row.active ? '停用' : '启用' }}
            </button>
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
      @update:model-value="showPasswordResult = $event"
    >
      <div v-if="resetResult" class="form-grid">
        <div class="notice warn">
          请立即通过安全渠道告知该用户，并要求其首次登录后修改密码。关闭本窗口后将无法再次查看。
        </div>
        <div class="notice" v-if="resetResult.note" style="margin-top:10px">{{ resetResult.note }}</div>
        <div class="detail-grid" style="margin-top:14px">
          <div class="detail-row"><span>账号</span><b>{{ resetResult.name }}</b></div>
          <div class="detail-row"><span>临时密码</span><b>{{ resetResult.password }}</b></div>
        </div>
      </div>
      <template #footer>
        <button class="btn primary" @click="showPasswordResult = false">我已记录</button>
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

    <Modal
      :model-value="showPermissions"
      title="角色权限矩阵"
      size="lg"
      @update:model-value="showPermissions = $event"
    >
      <SkeletonBlock v-if="!permissionLoaded" variant="table" :rows="5" />

      <template v-else>
        <div v-for="row in permissionRows" :key="row.capability_key" class="perm-block">
          <h3>{{ row.label }}</h3>
          <div class="perm-grid">
            <div v-for="role in ROLES" :key="role.code" class="perm-cell">
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
        <button class="btn" :disabled="!permissionLoaded" @click="restoreDefaults">恢复默认</button>
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

  </div>
</template>
