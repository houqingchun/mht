import { expect, type Page } from '@playwright/test';

/**
 * Shared login helper.
 *
 * Not a spec file — `testDir: './e2e'` only collects `*.spec.ts`, so importing
 * from here does not register a second copy of anything.
 */

export const ROLES = {
  student: { account: 'S001', password: '123456', home: '/student/home' },
  counselor: { account: '13800000001', password: '123456', home: '/counselor/workbench' },
  leader: { account: '13800000002', password: '123456', home: '/leader/overview' },
  // 管理员的落点 2026-09-27 前移到「系统概览」（V2.0.0 §5.14.5）：登进来第一件要知道的
  // 是「这个系统现在能不能正常用」，而不是「有哪些账号」。账号与权限仍在侧栏里。
  // 连带后果写在 `app.spec.ts` 那些用例上：凡是要动账号表或权限矩阵的，都得自己
  // `goto('/admin/system')`——用例的前置条件不该依赖「默认落点恰好是这一页」这个
  // 外部事实，何况它刚刚变过一次。
  admin: { account: 'admin', password: '123456', home: '/admin/overview' },
} as const;

export type RoleName = keyof typeof ROLES;

const ROLE_LABEL: Record<RoleName, string> = {
  student: '学生',
  counselor: '心理老师',
  leader: '德育领导',
  admin: '系统管理员',
};

export async function loginAs(page: Page, role: RoleName) {
  const user = ROLES[role];
  await page.goto('/login');
  await expect(page).toHaveURL('/login');

  await page.getByRole('button', { name: ROLE_LABEL[role] }).click();
  await page.getByRole('textbox', { name: /学号|手机号|管理员账号/ }).fill(user.account);
  await page.getByRole('textbox', { name: /密码/i }).fill(user.password);
  await page.getByRole('button', { name: '登录' }).click();

  await page.waitForURL(user.home);
  await expect(page).toHaveURL(user.home);
}

/**
 * 四档视口（§5.13.9 的人工检查清单，§5.14.7 的 DoD「四档视口无页面级横向溢出」）。
 *
 * `mobile` 决定**要不要**断底部导航，而不是「这一档窄不窄」：`styles.css` 的
 * `@media (max-width: 780px)` 才把 `.sidebar` 变成 `position: fixed; bottom: 0`，
 * 所以 780px 以上它是一块普通侧栏、随页面滚动，落在视口外是正常的。断它就会
 * **无故变红**——而一条会无故变红的守卫很快会被人关掉（CLAUDE.md §18），连带把
 * 同一段里的溢出断言一起丢掉。
 *
 * 一处定义：此前这张表在 `app.spec.ts` 里写了两遍（报告页与工作台各一份），
 * 再给「四角色 × 全部页面」加一遍就是第三份。
 */
export const VIEWPORTS = [
  { width: 375, height: 812, mobile: true },
  { width: 768, height: 1024, mobile: true },
  { width: 1024, height: 768, mobile: false },
  { width: 1440, height: 900, mobile: false },
] as const;

/**
 * 页面不许出现横向溢出（§5.13.8 ⑨）。
 *
 * 判据是 `documentElement` 的 `scrollWidth > clientWidth`——它在溢出发生时立刻为真，
 * 而「某个元素被挤出视口」要逐个元素比对，会把本来就该横向滚动的表格一起报进来。
 * 两个数都写进失败消息：只说「溢出了」的话，下一个人还得回去自己量。
 */
/**
 * 量一次横向溢出，**不判定**（判据的唯一出处，上面那条断言与下面的收集式用例共用）。
 *
 * 加了这一层是因为「一个页面断言一次」在四角色 × 全部页面的扫描里会在**第一个**坏
 * 页面上停下，而读的人想知道的是「一共几页坏了」——`vocabulary.spec.ts` 那条四档用例
 * 照 `auditPages` 的做法把问题攒起来一次性报告。
 */
export async function measureOverflow(page: Page) {
  return page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
}

export async function expectNoHorizontalOverflow(page: Page, label: string) {
  const { scrollWidth, clientWidth } = await measureOverflow(page);
  expect(
    scrollWidth,
    `${label} 出现横向溢出（scrollWidth ${scrollWidth} > clientWidth ${clientWidth}）`,
  ).toBeLessThanOrEqual(clientWidth);
}

/**
 * 底部导航必须落在视口里（§5.13.8 ⑨，**只在 780px 以下的档位调用**）。
 *
 * **判据是几何量而不是类名**：类名写对了而 `bottom: 0` 被别处的 `height` /
 * `transform` 顶出屏幕，屏幕上看不出、类名断言也看不出。
 */
export async function measureBottomNav(page: Page): Promise<string | null> {
  const nav = page.locator('.sidebar');
  if ((await nav.count()) !== 1) return '底部导航不在 DOM 里（.sidebar 不是恰好一个）';
  const box = await nav.boundingBox();
  const size = page.viewportSize();
  if (!box || !size) return '底部导航量不到尺寸';
  // 允许 1px 的取整误差；两个方向都断——横着跑出右边与竖着沉到屏幕底下是两种不同的坏法。
  if (box.x < -1) return `底部导航左边跑出视口（x=${box.x}）`;
  if (box.x + box.width > size.width + 1) {
    return `底部导航右边跑出视口（右边缘 ${Math.round(box.x + box.width)} > ${size.width}）`;
  }
  if (box.y + box.height > size.height + 1) {
    return `底部导航沉到视口下方（下边缘 ${Math.round(box.y + box.height)} > ${size.height}）`;
  }
  return null;
}

export async function expectBottomNavInViewport(page: Page, label: string) {
  const problem = await measureBottomNav(page);
  expect(problem, `${label}：${problem}`).toBeNull();
}
