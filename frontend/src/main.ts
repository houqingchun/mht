import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import AppRoot from './app/AppRoot.vue'
import { routes } from './app/routes'
import './assets/styles.css'

const router = createRouter({
  history: createWebHistory(),
  routes
})

/**
 * 22 条路由的 `meta: { title }` 此前**没有任何读者**（2026-09-17 补）：
 * 浏览器标签页、书签、历史记录里一直是 `index.html` 里那一句「心晴 · 心理测评与关怀平台」，
 * 开到第五个标签就分不出哪个是工作台、哪个是审计日志。
 *
 * 前缀用固定的「心晴」而不是校名：校名走 `useSettings` / `GET /public/branding`，
 * 那是异步的，而标题要在导航那一刻就定下来——何况页面自己的抬头已经写着校名。
 */
const BASE_TITLE = '心晴 · 心理测评与关怀平台'

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `心晴 · ${title}` : BASE_TITLE
})

createApp(AppRoot).use(router).mount('#app')
