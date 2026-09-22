/**
 * Date defaults for form fields.
 *
 * These used to be literal 2026-09-xx strings left over from the prototype,
 * which meant every "next follow-up" defaulted to a date in the past the moment
 * a fixed window closed. Deriving them from today keeps the defaults sensible
 * indefinitely.
 */

function toISODate(date: Date): string {
  // Local date, not UTC — a user in UTC+8 late in the day would otherwise get
  // tomorrow's date from toISOString().
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/** Today, as YYYY-MM-DD. */
export function today(): string {
  return toISODate(new Date())
}

/** Today + n days, as YYYY-MM-DD. Negative n looks backwards. */
export function daysFromNow(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  return toISODate(date)
}

/** Common follow-up horizons, so call sites read as intent rather than offsets. */
export const DEFAULT_FOLLOW_UP = () => daysFromNow(7)
export const DEFAULT_FAMILY_CONTACT = () => daysFromNow(14)
export const DEFAULT_RETEST = () => daysFromNow(30)
export const DEFAULT_TASK_START = () => today()
export const DEFAULT_TASK_END = () => daysFromNow(14)

/**
 * 作答用时，单位取自后端落库的秒数。
 *
 * 这是一个**墙钟**时长：口径是「首次作答 → 交卷」，不是「在页面上停留的时间」。
 * 学生周一答几题、周五才交，记的就是四天——那个数字是对的，不该在展示层抹平，
 * 所以超过一小时就换成小时/天。CSV 导出里保留原始秒数，供后续分析用。
 *
 * 数据迁移之前的历史会话与 /reset 之后的会话都可能是 NULL——那时显示「—」，
 * 不能显示 0：0 秒说的是「瞬间答完」，和「没有记录」是两回事。
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (seconds < 3600) {
    const rest = seconds % 60
    return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`
  }
  const hours = Math.floor(seconds / 3600)
  if (seconds < 86400) {
    const rest = Math.floor((seconds % 3600) / 60)
    return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`
  }
  const days = Math.floor(seconds / 86400)
  const rest = Math.floor((seconds % 86400) / 3600)
  return rest ? `${days} 天 ${rest} 小时` : `${days} 天`
}
