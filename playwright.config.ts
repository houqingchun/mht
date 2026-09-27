import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // 本地也串行（`workers: 1`），与 CI 同口径。**这不是保守，是这套用例按构造就不能并行**：
  // 全部 spec 读写同一个演示 MySQL 库，而若干断言断的正是那个库上的聚合事实——
  // 「卡片上的数与它点进去那个列表说的是同一个数」（CLAUDE.md §11）、心理咨询师工作台对账、
  // 任务完成率、审计首页恰好 20 行。任何一条 spec 的写入都会改掉另一条 spec 正在读的期望值，
  // 这是**跨文件**的争用，`test.describe.configure({ mode: 'serial' })` 挡不住。
  //
  // 实测（2026-09-27）：本地 `workers: undefined` 并行跑全量，三次各红**不同的**用例
  // （`app.spec.ts` 的卡片↔列表同源、工作台对账，以及 `专业报告工作台` 那三条），
  // 每一组单独跑都绿；而 `--workers=1` 全量 **193 passed / 3.6m**，反复绿。
  // 「每次红的东西不一样」本身就是争用的形状，不是回归的形状。
  //
  // 代价如实记：全量 1.5m → 3.6m。换来的是**全量套件的红可以当信号读**——
  // 按 §14 那条「一条会无故变红的守卫很快会被人关掉」，一个会随机红的套件比一个慢的套件更糟。
  workers: 1,
  reporter: [['list'], ['html']],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'cd backend && source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/api/v1/health',
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
    },
    {
      command: 'cd frontend && npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
    },
  ],
});
