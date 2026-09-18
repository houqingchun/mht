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
  admin: { account: 'admin', password: '123456', home: '/admin/system' },
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
