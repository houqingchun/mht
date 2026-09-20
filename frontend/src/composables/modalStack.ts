import { computed, ref } from 'vue'

/**
 * 同时开着两层弹层时，谁在上面（2026-09-19 加）。
 *
 * **不能靠 DOM 次序。** 所有 `.modal-backdrop` 都是 `z-index: 1000`（`styles.css`），
 * 同层里由 DOM 先后决定遮挡；而 DOM 先后由 **Teleport 锚点的建立次序**决定——那等于
 * **模板里的声明次序**，与打开次序无关（弹层的组件都是常挂着的，`v-if` 在其内部那层）。
 * `TasksPage.vue` 里 `<FormDialog>` 声明在详情弹层**前面**，于是从详情弹层里点
 * 「补发学生」开出来的表单**永远压在它下面**：视觉上被那层半透明遮罩压暗，点击则被
 * 遮罩接走——而遮罩的点击处理是「关掉详情弹层」，用户点一次提交，得到的是详情弹层
 * 消失了，表单还浮在一片空页面上。这个 bug 是量出来的，不是推出来的：修之前两层
 * 遮罩的 `getComputedStyle().zIndex` 都是 `1000`。
 *
 * 所以次序按**打开**算：谁后打开谁在上面。
 *
 * **这份状态住在模块里，不在 `Modal.vue` 的 `<script setup>` 里**，这是它的全部要害：
 * 后者里的每个绑定都是**每个组件实例各一份**的，于是两层弹层各自看到一叠只有自己的
 * 数组、序号都是 0。（第一版就是这么写的，两次实测都是两个 `1000`。）与
 * `useSettings.ts` 同为模块级单例，理由同源：这份状态描述的是弹层**之间**的关系，
 * 不是某一个弹层自己的事。
 *
 * **是一叠实例，不是一个递增的计数器**：计数器在「先开的先关」时会算错——关掉的那
 * 个把号让出来，新开的那一个反而排在那之前就开着的**下面**。而且每个弹层的号都是
 * `computed` 算出来的、读的就是这叠数组，所以任何一层进出都会让**所有**弹层重算，
 * 不会有谁的号停留在过期值上。
 */
const openStack = ref<object[]>([])

export function pushModal(token: object) {
  // `includes` 是必要的：`Modal.vue` 的 `watch` 与 `onMounted` 两条路都可能走到
  // `activate()`（挂载时就已经开着的弹层两条都会跑），重复入栈会让它自己占两格。
  if (!openStack.value.includes(token)) openStack.value.push(token)
}

export function dropModal(token: object) {
  const at = openStack.value.indexOf(token)
  if (at !== -1) openStack.value.splice(at, 1)
}

/**
 * 这个弹层该用的 `z-index`。第一个开出来的是 `1000`——与 `styles.css` 里那条基础
 * 规则同值，所以**只开一层时渲染结果与从前逐像素相同**。
 */
export function modalZIndex(token: object) {
  return computed(() => 1000 + Math.max(0, openStack.value.indexOf(token)))
}
