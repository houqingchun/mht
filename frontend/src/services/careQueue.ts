/**
 * 关怀队列的两条**共用判据**（V2.0.0 §5.14.3 COUNSELOR-UX）。
 *
 * 它们此前各写各的：工作台从 `cases` 里现算「逾期」，重点学生页用 `c.overdue`，
 * 而「今天要处理」这个档位在两边都不存在。加进来之后如果各写一遍，
 * 「工作台说今天有 3 件、点进列表一条都没有」就是必然会发生的那类矛盾——
 * 与 §11 那条「指标卡上的数必须与它点进去的那个列表同源」是同一条要求。
 *
 * 所以判据只在这里定义一次，`CounselorWorkbenchPage.vue` 与 `CasesPage.vue`
 * 都从这里取（`CasesPage` 的 `matchQueue` 里那个 `today` 分支也是调它）。
 * **视图里不许再出现第二份 `next_follow_up_date <= ...` 的比较。**
 */

import { today } from './dates'
import type { CareCaseItem } from './api'

/**
 * 「今天必须处理」。
 *
 * 判据取自服务端已经算好的两个事实（`care_service.list_care_cases`）：
 * `overdue` = 「排了跟进日期、那天已经过去、且档案没关」，`next_follow_up_date` =
 * 未关闭档案上最近一次跟进的日期（`max`）。
 *
 * 于是「今天」与「逾期」在构造上互斥：`overdue` 为真时日期一定早于服务端的今天。
 * 这里再判一次 `!overdue` 不是为了防重，而是因为**服务端的今天与浏览器的今天
 * 不是同一天的那八个小时是真实存在的**（服务端取 `now_utc_naive().date()`，
 * 本机在 UTC+8，所以本地 00:00–08:00 之间服务端还停在昨天）。那段时间里
 * 一条 `next_follow_up_date` 等于本地今天的记录，服务端算「明天到期」、浏览器算
 * 「就是今天」——两边都没错，而这张卡说的是「你今天要处理的事」，所以按本地算。
 *
 * 判据因此写成「不晚于今天」而不是「等于今天」：
 * - 本地时钟比服务端快的那一段，`===` 会漏掉真正的今天；
 * - 而早于本地今天的记录只可能是**服务端还没判逾期**的那一段窗口，
 *   它们本来就该出现在「今天必须处理」里（它们比今天更急）。
 * - 已经判过 `overdue` 的不在此列——它们有自己那一条队列，不重复计数。
 *
 * 空日期回 false：「没有安排下次跟进」不是「今天必须处理」。
 */
export function isDueToday(c: CareCaseItem): boolean {
  if (c.case_status === 'CLOSED') return false
  if (c.overdue) return false
  if (!c.next_follow_up_date) return false
  return c.next_follow_up_date <= today()
}

/** 负责人筛选。三个值都是**客户端**筛的，输入是服务端已经按数据范围过滤过的数组。 */
export type OwnerFilter = 'all' | 'mine' | 'unassigned'

/**
 * 「我负责的 / 未分配 / 全部」。
 *
 * **默认值是 `all`，不是 `mine`**，这一条不能反：`owner_id` 为空的在办档案是本项目
 * 里真实存在的一档（工作台那条「未分配负责人」的聚合就是为它写的），把默认值设成
 * 「我负责的」会让那些档案在默认视图里**消失**——一次界面改版把数据藏起来，
 * 而用户看不出是筛选器干的。
 *
 * `currentUserId` 为 null（`/auth/me` 还没回来或取不到）时 `mine` 恒假：
 * 一个说不清「你是谁」的筛选器回退成「一条都没有」是可解释的，回退成「全部」
 * 则会把「未分配」那一档一起显示成「我负责的」——后者是错的那种。
 */
export function matchesOwner(
  c: CareCaseItem,
  filter: OwnerFilter,
  currentUserId: number | null
): boolean {
  if (filter === 'all') return true
  if (filter === 'unassigned') return c.owner_id === null
  return currentUserId !== null && c.owner_id === currentUserId
}

/**
 * 三个页签。**判据与文案都在这里**，因为两个页面要长得一样（§5.14.3 的验收：
 * 「切换后列表、数量、空态一致」）。
 *
 * 次序是「默认在前」而不是回到规格里那句枚举次序（「我负责的 / 未分配 / 全部」）：
 * 那一句点的是**有三档可选**，不是它们在屏幕上的排法；而把激活态默认放在最右边
 * 会让读的人先看见一个不是当前状态的词。
 */
export const OWNER_TABS: Array<{ key: OwnerFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'mine', label: '我负责的' },
  { key: 'unassigned', label: '未分配' }
]

/**
 * 把 `?owner=` 里的字符串收成三档之一，认不出回 `all`。
 *
 * 与 `CasesPage` 对 `?filter=` / `?tab=` 的既有做法同形（`onMounted` 里逐个比对
 * `QUEUE_TABS`）：地址栏是可以被手改的，一个认不出的值不该让页面停在一个
 * 说不清的筛选上。
 */
export function normalizeOwnerFilter(value: unknown): OwnerFilter {
  return OWNER_TABS.some((tab) => tab.key === value) ? (value as OwnerFilter) : 'all'
}
