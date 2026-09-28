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
 * 服务端来的 `datetime` → `YYYY-MM-DD HH:mm`。
 *
 * **年份不能省**（2026-09-28 §5.28）。这条此前是反的：那时印的是 `MM-DD HH:MM`，
 * 理由是「这一页要显示的时间都是同一学年内的事，年份不承载信息」。那个前提不成立——
 * 这套系统按整个初中三年运行，同一屏上会出现今年与去年的记录（历次趋势、审计轨迹、
 * 关注档案时间线都是这样），`09-25 09:00` 读不出是哪一年的。
 *
 * 三处内联副本（`ExportCenterPage` / `SessionListDialog` / `AdminBackupPage` 里的
 * `slice(5, 16).replace('T', ' ')`）已全部收敛到这里——它们此前各自抄了一份，
 * 所以「补年份」这件事在它们身上是**三个独立的改动点**，而只要漏掉一个，
 * 那一页就继续印没有年份的时间，且看不出来。
 *
 * 后端发的是 `2026-09-25T09:00:00` 这种朴素字符串，秒与 `T` 是噪音，所以只取到分钟。
 *
 * **切字符串，绝不 `new Date()` 解析。** `new Date('2026-09-25T09:00:00')`（不带时区标记）
 * 会按**浏览器本地时区**解释这个串，而它本来就是后端那台机器的朴素本地时间——转一圈只
 * 会引入一次偏移。全库有两个时钟（§20），前端一次都不该参与换算。
 *
 * 空值回 `—`：那是界面占位符，不是空串（§3 数值列那条：空单元格与「没有这一项」
 * 在表格软件里长得一样）。
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 16).replace('T', ' ')
}

/**
 * 服务端来的**日期** → `YYYY-MM-DD`。
 *
 * 与 `formatDateTime` 分开，判据只有一个：**这一列承载了几分信息，由写入方决定**
 * （与 §21 那条「`tested_at` 刻意不回填」同一个口径——不替数据编一个它没有的精度）。
 *
 * | 这一列怎么写进去的 | 用它 |
 * |---|---|
 * | `Date` 列（`next_follow_up_date` / `contact_date` / `next_contact_date` / `planned_date`） | `formatDate` |
 * | `DateTime` 列，由系统写（`now_utc_naive()` / `func.now()`：会话时间、审计时间、分配与完成时间…） | `formatDateTime` |
 * | `DateTime` 列，由 `<input type="date">` 写（**任务窗口** `start_at` / `end_at`） | `formatDate` |
 * | `DateTime` 列，但**同一列混有两个来源**（`tested_at`：外部导入那一场写的是
 *   `datetime(年,月,日)`，时间恒为 `00:00`；在线那一场才是真实时刻） | `formatDate` |
 *
 * 第三、四行不是「例外」，是同一条判据的另一半：那两处的 `DateTime` 上一次**只可能**被
 * 写成 `00:00`，弹出 `00:00` 才是编精度（写入方只给了日期）。
 *
 * 第四行的判据要单独说清，它是这一节里唯一一条「按整列而不是按单个值」的：
 * 一列里一半的行有真实时刻、另一半没有，而读者**从屏幕上分不出哪一半是哪一半**——
 * 同一列里两种精度并存，等于让 `00:00` 冒充一个发生过的事件。所以整列按日期渲染。
 * （`StudentRecordsPage` 那一页「测评日期」的列名本身就是这个口径的宣称。）
 *
 * **年份同样不能省**，理由与 `formatDateTime` 那条逐字相同；这两个函数的分工只在于粒度。
 */
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 10)
}

/**
 * **浏览器本地的「此刻」** → `YYYY-MM-DD HH:mm`。
 *
 * 与 `formatDateTime` 分开，因为那一条的前提在这里反过来：`formatDateTime` 处理的是
 * **服务端**来的朴素串，所以它切字符串、**绝不 `new Date()` 解析**（见上）。而这里手上
 * 本来就是一个本地 `Date` 对象（用户刚点了导出那一下），必须用 `getFullYear` 一类的
 * **本地**读数去拼——两边都换成对方那一套都会错：
 *
 * - 拿 `toISOString()` 去喂 `formatDateTime` 会得到 **UTC**，UTC+8 的晚上会显示成前一天；
 * - 拿服务端那个朴素串去 `new Date()` 解析会按浏览器时区偏移一次。
 *
 * 它存在的理由是**统一形状**：此前这一处写的是 `toLocaleString('zh-CN', { hour12: false })`，
 * 出来是 `2026/9/28 14:33:05`（斜杠、不补零、带秒），与旁边导出中心那几张表的
 * `2026-09-28 14:33` 长得不一样，而它们说的是同一件事。（那一个**本来就有年份**，
 * 所以它不是 §5.28 那个缺陷的现场；收敛过来只为了一处定义、一个形状。）
 */
export function formatLocalMoment(date: Date): string {
  const day = toISODate(date)
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${day} ${hour}:${minute}`
}

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
