import { computed, ref } from 'vue'

/**
 * 长列表的键盘导航：整个列表只占**一个** Tab 停靠点（roving tabindex）。
 *
 * ## 为什么要有它
 *
 * 一长串 `<button>` 各是一个 Tab 停靠点，于是「Tab 进列表 → Tab 出去」这一段路上，键盘用户
 * 要按**行数**次 Tab，而焦点一路上落在每一行上。共享演示库上「我的报告」有一百多行、
 * 领导的已发布报告有一百多行——那不是「有点烦」，是一条真的走不通的键盘路径。
 * `e2e/app.spec.ts` 那条键盘用例的 `tabUntil` 上限（160）正是被它顶穿的，而那个上限随每次
 * 跑 e2e 变小的余量，是这条可达性缺陷的**症状**，不是它的原因。
 *
 * ## 形状
 *
 * 只有一行 `tabindex=0`（其余 `-1`），行与行之间用上下方向键走，Home / End 到首尾。
 * 于是「Tab 进列表 → 选中 → Tab 出去」是三个动作，与列表有多少行无关。
 *
 * **焦点与选中是两件事**：方向键只移焦点，不选中——选中仍然要按 Enter / 空格
 * （`<button>` 原生行为，这个 composable 不碰它）。理由不是洁癖：这两处列表的选中都会触发
 * 一次**详情请求**（`emit('select')` / `selectedId = …` 之后去拉那一份报告的正文），
 * 让方向键每经过一行都发一次请求，会把「翻过去看看」变成一百多次网络往返。
 *
 * ## 为什么是一个工厂函数，不是两份拷贝
 *
 * 两处列表（`ProfessionalReportList.vue`、`LeaderAnalyticsReportPage.vue`）行为逐字相同，
 * 而它们的行数都会随筛选 / 重新加载变小。写成两份就会漂——CLAUDE.md 反复记着这条
 * （「同一处只许有一个定义」）。但**也不能是模块级单例**：一个全局的 `activeIndex` 会把
 * 两个不相干的列表串进同一条序列（§14 里那个「竞态守卫按页构造、不做成全局序号」是同一条）。
 * 所以它是**每次调用各持一份状态**的工厂函数。
 *
 * @param itemCount 行数的取值函数（传 getter 而不是数字：列表是异步加载的，传值会拿到旧的那个）
 */
export function useRovingFocus(itemCount: () => number) {
  /** 光标（焦点）所在的行。`tabindex=0` 跟着它走，与「选中的那一行」无关。 */
  const activeIndex = ref(0)

  /**
   * 真正拿到 `tabindex=0` 的那一行——**夹在 `[0, itemCount() - 1]` 里**。
   *
   * 列表重新加载之后行数会变小（筛选、别人删掉了报告），而 `activeIndex` 停在旧值上。
   * 不夹的话会出现「一行都不是 Tab 停靠点」：整张列表从键盘上消失，而屏幕上完全看不出为什么。
   * 空列表回 `-1`——那时一行都不在 DOM 里，这个值不会被任何行读到。
   */
  const tabbableIndex = computed(() => {
    const last = itemCount() - 1
    if (last < 0) return -1
    return Math.min(Math.max(activeIndex.value, 0), last)
  })

  /** 焦点落进某一行时记下它，方向键从这里接着走（鼠标点过之后也成立）。 */
  function noteFocus(index: number) {
    activeIndex.value = index
  }

  /**
   * 上下方向键 / Home / End 移焦点。越界**什么都不做**（不循环回绕）：焦点停在原地，
   * 与浏览器里其它列表的读法一致，而回绕会让「按了两下还在最后一行」读成卡住了。
   *
   * 定位靠 `closest('ul')` —— 两处列表的 `<ul>` 里都只有 `<li><button>`，所以 `button`
   * 这个选择器不会误伤。写死 `.row` / `.report-item` 就得把类名传进来，那反而多一处要同步的东西。
   */
  function onRowKeydown(event: KeyboardEvent, index: number) {
    const last = itemCount() - 1
    const next =
      event.key === 'ArrowDown' ? index + 1
        : event.key === 'ArrowUp' ? index - 1
          : event.key === 'Home' ? 0
            : event.key === 'End' ? last
              : null
    if (next === null || next < 0 || next > last) return
    event.preventDefault()
    activeIndex.value = next
    const list = (event.currentTarget as HTMLElement).closest('ul')
    list?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus()
  }

  return { activeIndex, tabbableIndex, noteFocus, onRowKeydown }
}
