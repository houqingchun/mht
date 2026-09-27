/**
 * 「历次趋势」这一类页面的**唯一**取数判据（2026-09-27，§5.15.9 UX-FINAL-02）。
 *
 * 只保留**有分数的**那些场：没有结果行的会话（迁移前只剩会话行）画成 0 分是在撒谎，
 * 缺值直接不画点、让空白自己说明那一场没有结果——比编一个 0 分诚实。
 *
 * 这一条此前只写在 `TrendChart.vue` 的 `points` 里。个案详情新加的「较上次」摘要
 * 回答的是**同一个问题**——「最近一次是哪一次」——两处各写一遍时，图上最后一个点
 * 与摘要说的最近一次会在「有会话但没有结果行」的场次上分岔，而两个数就印在同一张卡上
 * （CLAUDE.md §11 那一族：卡片上的数必须与它点进去的那个列表同源）。
 *
 * 放在 `services/` 而不是扔进某个组件：`TrendChart`、`CareCaseDetailPage` 与
 * `StudentRecordsPage` 是**平级**的三个使用者（2026-09-27 §5.19 起是三个），
 * 谁 import 谁都不对——组件之间横着引用之后，下一个要用这条判据的人不知道该从哪一边拿。
 * 这里与 `dates.ts` 同一档：无状态、无依赖的纯函数。
 */

/**
 * 挑出算得出总分的那些场，其余原样丢掉。
 *
 * 判据只要求 `total_score` 一个字段（不要求调用方先把自己的类型收窄成某个 `HistoryPoint`）：
 * 三个调用点的 history 项各带各的附加字段（`dimensions` / `duration_seconds` / `task_voided`），
 * 让它们先满足一个最小接口反而会把类型写死在调用点之外。泛型把它交回去。
 */
export function scoredPoints<T extends { total_score: number | null }>(
  history: T[]
): Array<T & { total_score: number }> {
  return history.filter((entry): entry is T & { total_score: number } => entry.total_score !== null)
}

/**
 * 最近一次相对上一次的总分变化。不足两场时返回 `null`。
 *
 * **`null` 不是 `0`**（CLAUDE.md §11 那条 `rate_or_none` 的同一条）：`0` 是「两次一样」，
 * `null` 是「只有一场，比不了」。两者在界面上必须长得不一样——写成 `?? 0` 会让一个
 * 只测过一次的学生显示「与上次持平」，而那是一句关于数据的错话。
 */
export function latestScoreDelta(scored: ReadonlyArray<{ total_score: number }>): number | null {
  if (scored.length < 2) return null
  return scored[scored.length - 1].total_score - scored[scored.length - 2].total_score
}

/**
 * 变化值的色调。**升高是红、降低是绿**——方向与 `levelTone` 一致（MHT 总分越高，
 * 落在越重的那一档），同一张卡上两处颜色不可能互相矛盾。
 *
 * 只给**变化量**上色，不给它配「好转 / 恶化」这类词：这是筛查与关怀管理系统，
 * 一次分值的升降不构成疗效结论（产品边界，见 CLAUDE.md 开头）。颜色只是让
 * 「哪个维度在往哪边走」在八格里一眼看得出来。
 *
 * `null`（比不了）返回 `gray`，与 `0`（持平）同色是有意的：两者都是「没有话说」。
 */
export function deltaTone(delta: number | null): 'red' | 'green' | 'gray' {
  if (delta === null || delta === 0) return 'gray'
  return delta > 0 ? 'red' : 'green'
}

/**
 * 历次分数里的最高与最低（2026-09-27 §5.19，测评记录页的「总分变化」卡）。
 *
 * **一场都没有时返回 `null`**，不是 `{ max: 0, min: 0 }`——与 `latestScoreDelta` 同一条：
 * 编一个 0 出来会让「还没测过」看起来像「每次都考 0 分」，而那是两件事（§11）。
 *
 * 它只描述这名学生**自己**考过的那几个数，不含任何跨人比较，也不对那个数下判断：
 * 「44 分」是一个事实，「他属于哪一档」归 `total_level`（同一张卡上面的药丸与
 * 下面表里的「关注等级」列）。所以这里既不返回等级，也不返回一句评价。
 */
export function scoreRange(
  scored: ReadonlyArray<{ total_score: number }>
): { max: number; min: number } | null {
  if (scored.length === 0) return null
  const values = scored.map(point => point.total_score)
  return { max: Math.max(...values), min: Math.min(...values) }
}
