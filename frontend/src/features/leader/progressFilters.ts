import type { LeaderProgressItem } from '../../services/api'

/**
 * 「重点进展」那一批学生的三种待办筛选（V2.0.0 §5.14.4 第 1 / 3 条）。
 *
 * **为什么是一个独立模块。** 这三种筛选有两个消费者，而它们必须给出同一个数：
 *
 * | 消费者 | 它做什么 |
 * |---|---|
 * | `LeaderOverviewPage.vue` 的 KPI 卡 / 「管理提醒」 | 数一数有几条（卡片上那个数字） |
 * | `ProgressPage.vue` | 按同一条件把那份名单筛出来（点进去看到的那几行） |
 *
 * 两处各写一遍谓词就是同一个条件的两个定义，而它们漂了**不会有任何东西看得见**
 * ——卡片说 3、点进去 4 行，两边看起来都对（§11：「指标卡上的数必须与它点进去的
 * 那个列表同源」）。`overdueCount` 那次修复（2026-09-17）已经是这条教训的成例：
 * 当时的解法是「卡片从列表数组现算」，这里沿用同一个手法，只是把谓词本身也
 * 收到一处，因为现在有**两个页面**在读它。
 *
 * 谓词读的都是服务端下发的那几个字段（`overdue` / `owner_id` / `retest_planned`），
 * **不看姓名也不看等级**——筛选是管理动作，不是个体识别（§5.14.4 第 7 条）。
 */
export type ProgressFilter = 'overdue' | 'unassigned' | 'retest'

export const PROGRESS_FILTERS: Record<ProgressFilter, (item: LeaderProgressItem) => boolean> = {
  overdue: (item) => item.overdue,
  unassigned: (item) => !item.owner_id,
  retest: (item) => item.retest_planned
}

/** 卡片 / 筛选条上的中文。**与 `PROGRESS_FILTERS` 的键一一对应**，两处一起改。 */
export const PROGRESS_FILTER_LABELS: Record<ProgressFilter, string> = {
  overdue: '逾期未跟进',
  unassigned: '未分配负责人',
  retest: '有未完成的复测计划'
}

/**
 * 把路由 `query.filter` 解析成一个筛选键，认不出就是 `null`。
 *
 * 认不出时**返回 `null`（不筛）而不是回退到某一个默认筛选**：一个打错的 URL
 * （`?filter=overdu`）回退成「逾期未跟进」会让读者以为屏幕上这几行就是全部逾期
 * 的人。不筛至少是诚实的——它显示的是这一页本来要显示的东西。
 *
 * 判据用 `hasOwnProperty.call` 而不是 `key in PROGRESS_FILTERS`：后者会命中
 * 原型链，于是 `?filter=toString` 会解析成一个**不是筛选键**的字符串，接着
 * `PROGRESS_FILTERS[filter]` 拿到一个函数而 `Object.prototype.toString` 恰好
 * 也是函数——把它当谓词调用就会抛，或者更糟：安静地筛出错误的结果。
 */
export function parseProgressFilter(raw: unknown): ProgressFilter | null {
  if (typeof raw !== 'string') return null
  if (!Object.prototype.hasOwnProperty.call(PROGRESS_FILTERS, raw)) return null
  return raw as ProgressFilter
}

/** 按筛选键留下该留的行；`null` 表示不筛（整份名单，服务端给的就是这一页该有的）。 */
export function applyProgressFilter(
  items: LeaderProgressItem[],
  filter: ProgressFilter | null
): LeaderProgressItem[] {
  if (!filter) return items
  return items.filter(PROGRESS_FILTERS[filter])
}
