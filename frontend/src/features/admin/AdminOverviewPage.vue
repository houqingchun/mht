<script setup lang="ts">
/**
 * 管理员系统概览（V2.0.0 §5.14.5）。
 *
 * 这一页只回答「系统现在能不能正常用」：账号、导入、导出作业、量表与上线配置。
 * 它**不展示任何学生的测评内容**——系统管理权（`ORG_ACCOUNT: MANAGE`）
 * 不等于心理数据查看权（`STUDENT_PSYCH_DETAIL: NONE`，CLAUDE.md §4）。
 * 首页做成系统健康页的另一个理由在验收里写着：管理员默认落地页在库里
 * 没有任何业务数据时也不该是一片空白。
 *
 * 六个数据源**全部是既有接口**，一个都不新增（§5.14.5 主改范围：
 * 「只有现有接口无法提供必要聚合时才新增只读 summary API」）：
 *   `getAccounts` / `getStudentRosterBatches` / `listExportJobs` /
 *   `getScaleVersions` / `getBranding` / `getSystemSettings`
 *
 * 三处口径要跟着这个文件一起读，否则很容易把「窗口」当成「全集」：
 *
 * ① **导出中心那一句 foot 写的是「当前列出的 N 份作业中」**。`list_export_jobs`
 *    在服务端封顶 50 且**不发 total**，所以「全部作业里有 M 份已过期」这句话
 *    在这一页上说不出来。**不写死「最近 50 份」**：那是 §19 那个
 *    「看起来像设过、其实没人设过」的值（50 藏在后台函数签名的默认值里，
 *    改它不会有任何东西红）。说不出的数就不说，改成一句能说清的。
 * ② 名册批次那一句写「最近 N 批里」——它同样有窗口，但它**能自证**
 *    （`{items, total, truncated}`），所以 §10 那条「凡是截断都要自己说出来」
 *    在这里是**说得出来**的。
 * ③ **测评记录导入不在这一页上。** `/counselor/data` 的 `meta.role` 是
 *    `counselor`，管理员到不了那里（§4：测评任务不是能力，是角色）。
 *    所以「失败导入」这一张卡指的是**名册导入**，下钻也只指向
 *    `/admin/organization`——指向一个他打不开的页面才是更糟的那种错。
 *
 * 「不可点的项不装作能点」这一条在本页有**两个**活体（学生账号数、系统版本），
 * 都是非交互的 `<div class="check-row">`。§5.14.4.1 ① 记着这条保证的活体
 * 曾经在领导页断过一次链，这里是补上的那一处。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import ErrorState from '../../components/ErrorState.vue'
import SkeletonBlock from '../../components/SkeletonBlock.vue'
import KpiCard from '../analytics/components/KpiCard.vue'
import {
  getAccounts,
  getBranding,
  getScaleVersions,
  getStudentRosterBatches,
  getSystemSettings,
  listExportJobs,
  type AccountItem,
  type ExportJob,
  type RosterImportBatch,
  type ScaleVersion,
  type SettingsResponse,
} from '../../services/api'

const router = useRouter()

const loading = ref(true)
const error = ref('')
const accounts = ref<AccountItem[]>([])
const batches = ref<RosterImportBatch[]>([])
const batchesTotal = ref(0)
const batchesTruncated = ref(false)
const exportJobs = ref<ExportJob[]>([])
const scales = ref<ScaleVersion[]>([])
const systemVersion = ref('')
const settings = ref<SettingsResponse | null>(null)

function go(path: string, query?: Record<string, string>) {
  router.push(query ? { path, query } : path)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    // 取数之前先清空（§14）：重试之后页面上任何一处都不该留着上一次的数。
    accounts.value = []
    batches.value = []
    batchesTotal.value = 0
    batchesTruncated.value = false
    exportJobs.value = []
    scales.value = []
    systemVersion.value = ''
    settings.value = null

    const [accountList, batchPage, jobs, scaleList, branding, conf] = await Promise.all([
      getAccounts(),
      getStudentRosterBatches(),
      listExportJobs(),
      getScaleVersions(),
      getBranding(),
      getSystemSettings(),
    ])
    accounts.value = accountList
    batches.value = batchPage.items
    batchesTotal.value = batchPage.total
    batchesTruncated.value = batchPage.truncated
    exportJobs.value = jobs
    scales.value = scaleList
    systemVersion.value = branding.version
    settings.value = conf
  } catch (err) {
    error.value = err instanceof Error ? err.message : '系统概览加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

/* 四张卡片。判据一律是**列表自己的字段**——卡片上的数与它点进去那个列表
 * 同源（§11），所以下面每一个 filter 的写法都要与目标页面的筛选条件逐字对应。 */
const unconfiguredAccounts = computed(() => accounts.value.filter((a) => !a.scopes?.length))
const mustChangeAccounts = computed(() => accounts.value.filter((a) => a.must_change_password))
const inactiveAccounts = computed(() => accounts.value.filter((a) => !a.active))
const studentAccounts = computed(() => accounts.value.filter((a) => a.role_code === 'student'))

/**
 * 「失败导入」的判据是 `error_rows > 0`，**不能读 `status`**。
 * 两条导入链路的批次状态只有 `PREVIEW` / `COMMITTED`（CLAUDE.md §3 的词表），
 * 从来没有一个 `FAILED`——按状态去筛会恒空，而屏幕上那是一个看起来像
 * 「这次导入没有问题」的 0。
 */
const erroredBatches = computed(() => batches.value.filter((b) => b.error_rows > 0))
const expiredJobs = computed(() => exportJobs.value.filter((j) => j.status === 'EXPIRED'))
const publishedScale = computed(() => scales.value.find((s) => s.status === 'PUBLISHED') ?? null)

/**
 * 「待完成上线配置」。**只能列可以从真实配置判定的项目**（§5.14.5 第 9 条），
 * 所以这里每一项的输入都是 `settings` 里真的有的值。
 *
 * 三处被**否掉**的候选，记下来免得下次有人再加回去：
 *   - 「管理员仍在使用初始密码」——库里没有明文，判不出来；
 *   - 「还没有第二位员工账号」——种子建了四类账号，这条恒假；
 *   - 「JWT 密钥是否安全」——**明令禁止在界面上猜测**（原文：「禁止在界面里
 *     猜测 JWT secret 是否安全」），而且这一层压根拿不到那个值。
 * `export.max_rows` 也不列：它在 `SystemSettings` 类型里没有声明，模板渲染一个
 * 后端不发的字段会得到空串，那正是 §5.14.4.2 ③ 记下的那种假绿形状。
 */
const launchChecklist = computed(() => {
  const items: Array<{ label: string; detail: string; path: string }> = []
  if (!publishedScale.value) {
    items.push({
      label: '还没有已发布的量表版本',
      detail: '草稿不参与评分，新建的测评任务也不会选中它。',
      path: '/admin/scale',
    })
  }
  const values = settings.value?.values
  const defaults = settings.value?.defaults
  if (values && defaults) {
    if (values.org.school_name === defaults.org.school_name) {
      items.push({
        label: '学校名称还是出厂值',
        detail: `当前是「${values.org.school_name}」，请改成学校的正式名称。`,
        path: '/admin/settings',
      })
    }
    if (!values.org.counselling_room.trim()) {
      items.push({
        label: '心理辅导室还没有填写',
        detail: '学生端的帮助弹层会用到它。留空不写，比编一个不存在的房间更安全。',
        path: '/admin/settings',
      })
    }
    if (values.export.job_ttl_hours === defaults.export.job_ttl_hours) {
      items.push({
        label: `导出文件有效期还是出厂值（${values.export.job_ttl_hours} 小时）`,
        detail: '这一项还没有业务结论，请按学校的留存要求定。',
        path: '/admin/settings',
      })
    }
  }
  return items
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="eyebrow">系统运维</div>
        <h1>系统概览</h1>
        <p class="page-desc">
          这一页只回答「系统现在能不能正常用」：账号、导入、导出作业、量表与上线配置。
          它不展示任何学生的测评内容——系统管理权不等于心理数据查看权。
        </p>
      </div>
    </div>

    <SkeletonBlock v-if="loading" variant="metrics" :columns="4" />
    <ErrorState v-else-if="error" :message="error" :on-retry="load" />
    <template v-else>
      <div class="kpis">
        <div
          class="kpi-link"
          role="button"
          tabindex="0"
          @click="go('/admin/system', { account: 'unconfigured' })"
          @keydown.enter.prevent="go('/admin/system', { account: 'unconfigured' })"
          @keydown.space.prevent="go('/admin/system', { account: 'unconfigured' })"
        >
          <KpiCard
            label="未配置数据范围的账号"
            :value="unconfiguredAccounts.length"
            icon="shield"
            :hint="
              unconfiguredAccounts.length
                ? '这些账号登录得进来，但每个列表都是空的。'
                : '每个账号都有数据范围。'
            "
          />
        </div>
        <div
          class="kpi-link"
          role="button"
          tabindex="0"
          @click="go('/admin/system', { account: 'must_change' })"
          @keydown.enter.prevent="go('/admin/system', { account: 'must_change' })"
          @keydown.space.prevent="go('/admin/system', { account: 'must_change' })"
        >
          <KpiCard
            label="需修改密码的账号"
            :value="mustChangeAccounts.length"
            icon="alert"
            :hint="
              mustChangeAccounts.length
                ? '管理员重置过密码，本人下次登录要改。'
                : '没有待改密的账号。'
            "
          />
        </div>
        <div
          class="kpi-link"
          role="button"
          tabindex="0"
          @click="go('/admin/organization', { import: 'errors' })"
          @keydown.enter.prevent="go('/admin/organization', { import: 'errors' })"
          @keydown.space.prevent="go('/admin/organization', { import: 'errors' })"
        >
          <KpiCard
            label="名册导入含错误的批次"
            :value="erroredBatches.length"
            icon="database"
            :hint="
              batches.length
                ? `最近 ${batches.length} 批里${batchesTruncated ? `（共 ${batchesTotal} 批）` : ''}有 ${erroredBatches.length} 批存在没能导入的行。`
                : '还没有任何名册导入批次。'
            "
          />
        </div>
        <div
          class="kpi-link"
          role="button"
          tabindex="0"
          @click="go('/admin/exports', { status: 'EXPIRED' })"
          @keydown.enter.prevent="go('/admin/exports', { status: 'EXPIRED' })"
          @keydown.space.prevent="go('/admin/exports', { status: 'EXPIRED' })"
        >
          <KpiCard
            label="已过期的导出作业"
            :value="expiredJobs.length"
            icon="download"
            :hint="
              exportJobs.length
                ? `导出中心当前列出的 ${exportJobs.length} 份作业中，有 ${expiredJobs.length} 份已过有效期。`
                : '导出中心当前没有作业。'
            "
          />
        </div>
      </div>

      <div class="grid two" style="margin-top: 17px">
        <article class="card pad">
          <h2>账号概况</h2>
          <p class="muted tiny" style="margin: 6px 0 0">
            共 {{ accounts.length }} 个账号。上面两张卡片里的数，在这里点得进去复核。
          </p>
          <div class="checklist" style="margin-top: 15px">
            <button type="button" class="check-row" role="button" @click="go('/admin/system')">
              <span>员工账号</span>
              <strong>{{ accounts.length - studentAccounts.length }} 个</strong>
            </button>
            <!--
              学生账号**没有去处**：账号列表没有按账号类型筛选的入口，学生也不在
              「新建账号」里建（他们跟着名册一起生成，CLAUDE.md §4）。所以它是
              `<div>` 而不是 `<button>`——同一个 `.check-row` 外观，区别只在
              光标与悬停（`styles.css` 的 `.check-row[role="button"]` 那一段）。
            -->
            <div class="check-row">
              <span>学生账号 <span class="muted tiny">· 跟着名册一起生成</span></span>
              <strong>{{ studentAccounts.length }} 个</strong>
            </div>
            <button
              type="button"
              class="check-row"
              role="button"
              @click="go('/admin/system', { account: 'inactive' })"
            >
              <span>已停用账号</span>
              <strong>{{ inactiveAccounts.length }} 个</strong>
            </button>
          </div>
        </article>

        <article class="card pad">
          <h2>系统与量表</h2>
          <p class="muted tiny" style="margin: 6px 0 0">
            这里说的是系统自己的东西，与学生的测评结果无关。
          </p>
          <div class="checklist" style="margin-top: 15px">
            <button type="button" class="check-row" role="button" @click="go('/admin/scale')">
              <span>
                当前量表版本
                <span v-if="publishedScale" class="muted tiny">· {{ publishedScale.name }}</span>
              </span>
              <strong>{{ publishedScale ? publishedScale.version : '暂无已发布版本' }}</strong>
            </button>
            <!--
              系统版本**没有去处**（它不是一件可以去「处理」的事），所以是非交互的
              `<div>`。这是本页第二个「不可点的项不装作能点」的活体。
            -->
            <div class="check-row">
              <span>系统版本</span>
              <strong>{{ systemVersion || '—' }}</strong>
            </div>
          </div>
        </article>
      </div>

      <article class="card pad" style="margin-top: 17px">
        <h2>待完成上线配置（{{ launchChecklist.length }} 项）</h2>
        <p class="muted tiny" style="margin: 6px 0 0">
          这里只列能从当前配置判定的项目。密钥一类的东西不在这里猜——界面看不到它们，
          猜出来的结论比没有更糟。
        </p>
        <div v-if="!launchChecklist.length" class="muted tiny" style="margin-top: 15px">
          能从配置判定的上线准备项都已完成。
        </div>
        <div v-else class="checklist" style="margin-top: 15px">
          <button
            v-for="item in launchChecklist"
            :key="item.label"
            type="button"
            class="check-row"
            role="button"
            @click="go(item.path)"
          >
            <span>
              {{ item.label }}
              <span class="muted tiny">· {{ item.detail }}</span>
            </span>
            <strong>去处理</strong>
          </button>
        </div>
      </article>
    </template>
  </div>
</template>

<style scoped>
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}
/*
 * 可点的 KPI 卡。`role="button"` 同时是「键盘到得了」与「手型光标」的判据
 * （与 `styles.css` 的 `.metric[role="button"]` / `.check-row[role="button"]`
 * 同一个约定，2026-09-17）。`border-radius` 是为了让全局的 `:focus-visible`
 * 焦点环贴着卡片的圆角，而不是一个直角方框套在圆角卡片上。
 */
.kpi-link {
  border-radius: var(--radius);
  transition: 0.18s;
}
.kpi-link[role='button'] {
  cursor: pointer;
}
.kpi-link[role='button']:hover {
  transform: translateY(-2px);
}
@media (max-width: 1100px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 520px) {
  .kpis {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
