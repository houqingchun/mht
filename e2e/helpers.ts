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
