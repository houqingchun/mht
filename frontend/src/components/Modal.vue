<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { modalZIndex, pushModal, dropModal } from '../composables/modalStack'

const props = withDefaults(defineProps<{
  modelValue: boolean
  title: string
  size?: 'sm' | 'md' | 'lg'
  /**
   * Blocks Escape, backdrop click and the × button. Use for dialogs the user
   * must resolve before continuing (e.g. forced password rotation) — a
   * dismissible gate is not a gate.
   */
  persistent?: boolean
  /**
   * 面板右上角多一枚「全屏 / 退出全屏」按钮，供内容很长的弹层铺满视口。
   *
   * **默认关**：全站其余十几个弹层一个字都不变，只有明确说要它的那一处才长出这颗
   * 按钮（「新能力不许动既有行为」这一类改动的标准做法）。
   */
  expandable?: boolean
  /**
   * 受控的全屏状态，配 `update:expanded` 一起用（`expandable` 为假时它没有读者）。
   *
   * 做成受控的而不是内部 `ref`，是因为**打开它的那一页往往还要跟着变**：明细弹层
   * 平时把表格压在 340px 里（那一页下面还有别的卡片），全屏时得把它放开——
   * 那份尺寸住在页面上，页面不拿到这个状态就只能靠选择器去猜。
   */
  expanded?: boolean
}>(), {
  persistent: false,
  expandable: false,
  expanded: false
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'close'): void
  (e: 'update:expanded', value: boolean): void
}>()

const shell = ref<HTMLElement | null>(null)

const sizeClass = computed(() => `modal-${props.size ?? 'md'}`)

/**
 * 铺满视口的那一档（`styles.css` 的 `.modal-fullscreen`）。
 *
 * 它与 `size` 是两件事：`size` 定「平时多宽」，这一条只在被点开之后才接管，
 * 所以三个尺寸档一个都不用改。规则**排在 `.modal-lg` / `.modal-sm` 之后**（同特异性
 * 靠源序取胜），否则一个 `size="lg"` 的全屏弹层宽度仍然是 940px。
 */
const fullscreenClass = computed(() =>
  props.expandable && props.expanded ? 'modal-fullscreen' : ''
)

function toggleExpanded() {
  emit('update:expanded', !props.expanded)
}

/**
 * 这个弹层在「打开中的弹层」那一叠里的位置——决定它压在谁上面。
 * 为什么需要它、为什么住在 `composables/modalStack.ts` 里，见那个文件。
 */
const token = {}
const zIndex = modalZIndex(token)

function onBackdropClick() {
  if (props.persistent) return
  close()
}

function close() {
  emit('update:modelValue', false)
  emit('close')
}

/**
 * 键盘**留在弹窗里**（2026-09-17 补）。
 *
 * `role="dialog"` 与 `aria-modal="true"` 是给读屏软件的声明，它们**不会**让 Tab
 * 真的留在面板里：没有这一段，按 Tab 会一路走到弹窗背后的页面上——用户看不见焦点
 * 在哪，回车按下去是背后那个按钮。这一页的其余部分此时是不可用的，键盘却仍然到得了。
 *
 * 实现要点：`items.indexOf(active) === -1`（焦点在面板自己身上，或已经跑到外面）
 * 也算「不在里面」，两个方向各自从头/尾接上。
 */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

function focusable(): HTMLElement[] {
  if (!shell.value) return []
  return Array.from(shell.value.querySelectorAll<HTMLElement>(FOCUSABLE))
}

function trapTab(e: KeyboardEvent) {
  const items = focusable()
  if (items.length === 0) {
    e.preventDefault()
    shell.value?.focus()
    return
  }
  const active = document.activeElement as HTMLElement | null
  const index = active ? items.indexOf(active) : -1
  if (e.shiftKey) {
    if (index <= 0) {
      e.preventDefault()
      items[items.length - 1].focus()
    }
  } else if (index === -1 || index === items.length - 1) {
    e.preventDefault()
    items[0].focus()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (!props.persistent) close()
    return
  }
  if (e.key === 'Tab') trapTab(e)
}

/** 打开之前焦点在哪——关掉之后要还回去（可能是那个「查看明细」按钮）。 */
let lastFocused: HTMLElement | null = null

function activate() {
  const active = document.activeElement
  lastFocused = active instanceof HTMLElement ? active : null
  pushModal(token)
  document.addEventListener('keydown', onKeydown)
  document.body.style.overflow = 'hidden'
  // 焦点先落在面板本身（有 tabindex="-1" 与 aria-label）：读屏软件随即念出标题，
  // 而第一个 Tab 才走到「关闭」。落在背后的页面上等于弹窗没开。
  nextTick(() => shell.value?.focus())
}

function deactivate() {
  dropModal(token)
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
  // 还焦点之前先确认它还在：卸载往往连同那个按钮一起删掉了。
  if (lastFocused && lastFocused.isConnected) lastFocused.focus()
  lastFocused = null
}

watch(() => props.modelValue, (open) => {
  if (open) activate()
  else deactivate()
})

onMounted(() => {
  // 挂载时就已经是打开状态（父组件用 `v-if` 控制、或初始即 true）：
  // 只挂监听器而不锁滚动的旧写法就是这么漏掉 `overflow` 的——watch 只在**变化**时跑。
  if (props.modelValue) activate()
})

/**
 * 面板在打开状态下被卸载时必须解锁。
 *
 * 这是那次残留的来源：`deactivate()` 只在 `modelValue` 变成 false 时跑，
 * 而父组件切页、条件渲染、`v-if` 收起来时弹窗是**直接消失**的——
 * 监听器与 `body{overflow:hidden}` 一起留在原地，这一页之后再也滚不动。
 */
onUnmounted(deactivate)
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <!-- 行内 `z-index` 与 `styles.css` 里那条基础规则同值（第一个开出来的是 1000），
           所以**只开一个弹层时这一层与从前逐像素相同**；它只在同时开着两个以上时
           才开始起作用（`composables/modalStack.ts` 里写着为什么不能靠 DOM 次序）。 -->
      <div
        v-if="modelValue"
        class="modal-backdrop"
        :style="{ zIndex }"
        @click="onBackdropClick"
        tabindex="-1"
      >
        <section
          ref="shell"
          :class="['modal-panel', sizeClass, fullscreenClass]"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
          tabindex="-1"
          @click.stop
        >
          <header class="modal-header">
            <h2>{{ title }}</h2>
            <!-- 一枚按钮的容器在这里是必要的，不只是排版：`.modal-header` 是
                 `justify-content: space-between` 的两端布局，直接多插一个兄弟节点会
                 把标题挤到中间。包一层之后，**没有全屏按钮的那些弹层仍然只有两个子
                 元素**（标题 + `.modal-head-actions` 里唯一那颗关闭按钮），
                 渲染结果与从前逐像素相同。 -->
            <div class="modal-head-actions">
              <button
                v-if="expandable"
                class="modal-expand"
                type="button"
                :aria-pressed="expanded"
                :title="expanded ? '退出全屏展示' : '全屏展示'"
                @click="toggleExpanded"
              >{{ expanded ? '退出全屏' : '全屏' }}</button>
              <button v-if="!persistent" class="modal-close" type="button" @click="onBackdropClick" aria-label="关闭">×</button>
            </div>
          </header>
          <div class="modal-body">
            <slot />
          </div>
          <footer v-if="$slots.footer" class="modal-footer">
            <slot name="footer" />
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
