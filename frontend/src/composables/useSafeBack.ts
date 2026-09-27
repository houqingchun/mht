import { useRouter } from 'vue-router'

/**
 * 「返回列表」那种按钮的安全实现：**没有站内上一页时，退到指定的兜底路径，
 * 而不是退出整个应用。**
 *
 * ## 它修的是什么
 *
 * 详情页原先写的是裸 `router.back()`。从站内点进来时它是对的（回到列表，且因为列表把
 * 筛选写进了 query，回来时那一档还在）；但**直接打开那个地址**时它把用户带出应用：
 *
 * - 收藏夹里存了 `/counselor/cases/372`，下次点开就是「直接进入」；
 * - 把个案链接发给同事（这一页的地址本来就可以分享）；
 * - 浏览器刷新之后点「返回列表」。
 *
 * 实测（`page.goto` 一个新标签打开详情，`history.length = 2`）：点「返回列表」之后
 * URL 变成 `blank`——**白屏，整个应用没了**。而这一页上并没有任何东西说得出
 * 「你得先打开列表页才能用返回」。CLAUDE.md §14 那条「空态是一句关于数据的话」是
 * 同一条：界面上留下的那句话会一直回答用户的问题，而这里留下的是一片空白。
 *
 * ## 判据
 *
 * Vue Router 4 把「本站内的上一页」记在 `history.state.back` 里——站内 `router.push`
 * 之后它是上一页的 fullPath，而 `page.goto`（含刷新、新标签、无站内来源）之后它是
 * `null`。四种情形实测过：
 *
 * | 怎么到这一页 | `history.state.back` |
 * |---|---|
 * | 登录后落在工作台（站内跳转） | `/login` |
 * | 站内点一次（工作台 → 列表） | `/counselor/workbench` |
 * | `page.goto` 直接开某一页 | `null` |
 * | 同上之后刷新 | `null` |
 *
 * **判据失效时它退化成「永远回列表」，不会退化成「离开应用」**：那个字段若哪天改了名字
 * 或形状，`?.back` 读到 `undefined`，`!= null` 为假，走的是兜底那条路——而兜底是一条
 * 真实存在的页面。这是刻意选的失败方向。
 *
 * 不用 `window.history.length > 1`：它把**跨站**的历史条目也算进去（从搜索结果点进来、
 * 从邮件点进来），那些情况下按它判会退回到别的站点——同一个白屏，只是换了个出口。
 *
 * ## 为什么是一个工厂函数，不是两份拷贝
 *
 * 两处详情（`CareCaseDetailPage.vue`、`StudentRecordsPage.vue`）行为逐字相同，写成两份
 * 就会漂——CLAUDE.md 反复记着这条（「同一处只许有一个定义」）。而它的兜底路径**是参数**
 * 而不是常量：两页今天都回 `/counselor/cases`，但这个函数不知道那件事，也不该知道。
 *
 * 它**没有状态**（每次调用现读 `window.history.state`），所以不需要 `useRovingFocus`
 * 那种「每次调用各持一份」的讲究；做成模块级函数也可以，写成 composable 只是为了和
 * 这个目录里其余几个一样，从这里拿 `router`。
 */
export function useSafeBack(fallbackPath: string) {
  const router = useRouter()

  return function goBack() {
    if (window.history.state?.back != null) {
      router.back()
      return
    }
    router.push(fallbackPath)
  }
}
