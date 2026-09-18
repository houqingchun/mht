import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAs, type RoleName } from './helpers';

/**
 * 状态词汇契约的**渲染侧**回归网（CLAUDE.md §3）。
 *
 * `backend/app/tests/test_status_vocabulary.py` 只断言后端发出的编码与 labels.ts
 * 的键名一致，它检查不到组件有没有**调用**标签函数——组件写
 * `{{ detail.case_status }}` 时后端发的是完全正确的 FOLLOWING，那个测试照样全绿。
 * 八个页面就是这样把 GENERAL_RANGE / SCHOOL / ACTIVE 原样显示给用户的。
 *
 * 所以这里看像素：登录每个角色，走完它的页面，断言页面上不出现任何一个
 * 「必须被 labels.ts 翻译」的编码。
 */

/** 必须被 translation 的编码全集，逐项对应 labels.ts 里的一张映射表。 */
const UNTRANSLATED_CODES = [
  // LEVEL_LABELS
  'KEY_ATTENTION',
  'NEEDS_ATTENTION',
  'GENERAL_RANGE',
  // STATUS_LABELS
  'PENDING_REVIEW',
  'FOLLOWING',
  'OBSERVING',
  'CLOSED',
  // DIMENSION_LABELS
  'LEARNING_ANXIETY',
  'INTERPERSONAL_ANXIETY',
  'LONELINESS',
  'SELF_BLAME',
  'SENSITIVITY',
  'PHYSICAL_SYMPTOMS',
  'PHOBIC_TENDENCY',
  'IMPULSIVE_TENDENCY',
  // TASK_STATUS_LABELS / SCALE_STATUS_LABELS / STUDENT_STATUS_LABELS / RULE_STATUS_LABELS
  'ACTIVE',
  'INACTIVE',
  'DRAFT',
  'PAUSED',
  'PUBLISHED',
  'ARCHIVED',
  'RETIRED',
  // TARGET_STATUS_LABELS
  'COMPLETED',
  'IN_PROGRESS',
  'NOT_STARTED',
  // SCOPE_TYPE_LABELS
  'SCHOOL',
  'GRADE',
  'CLASS',
  'STUDENT',
  // VALIDITY_LABELS
  'VALID',
  'QUESTIONABLE',
  'RETEST_RECOMMENDED',
  // RISK_EVENT_STATUS_LABELS / RETEST_STATUS_LABELS / FOLLOW_UP_STATUS_LABELS
  'PENDING',
  'REVIEWED',
  'PLANNED',
  'DONE',
  'CANCELLED',
  // GENDER_LABELS。名册与个案详情都把性别渲染成编码就会漏——DataTable 的默认
  // 插槽直接打印 row[key]，所以花名册那一列必须显式调用 genderLabel。
  'MALE',
  'FEMALE',
  // SOURCE_LABELS。2026-09-17 之前这张表**只有第一面与第三面**有保护：它的三处渲染
  // （个案详情、任务列表、学生的记录页）都是 `v-if="source === 'IMPORTED'"` 条件渲染，
  // 而演示数据里一条 `IMPORTED` 记录都没有，于是 `sourceLabel` 漏调也不会变红
  // （CLAUDE.md §3 记着这条）。
  // 「全部学生」页签（2026-09-17 加）换了个写法：来源列不条件渲染，**每一行**都出
  // 「系统内作答」或「—」。演示数据里全是 `IN_SYSTEM`，所以这一项现在真的扫得到东西。
  // `IMPORTED` 一并列上：它现在只有后端测试守着，等到哪天演示数据或某个用例带进一条
  // 导入记录，它会自己开始生效。
  'IN_SYSTEM',
  'IMPORTED',
];

// 词边界匹配，且 `_` 在 JS 正则里属于 \w，所以 \bSTUDENT\b 不会误伤
// STUDENT_PSYCH_DETAIL、\bACTIVE\b 不会误伤 INACTIVE、\bVALID\b 不会误伤
// VALIDATION。误报会让这个测试很快被人关掉，边界必须收准。
const PATTERN_SOURCE = `\\b(${UNTRANSLATED_CODES.join('|')})\\b`;

/** 给定区域内出现过的原始编码（去重、保序）。 */
async function leakedCodes(scope: Locator): Promise<string[]> {
  const text = await scope.innerText();
  const found = text.match(new RegExp(PATTERN_SOURCE, 'g')) ?? [];
  return [...new Set(found)];
}

/**
 * 走完一个角色的页面，把出现裸编码的页面收集起来一次性报告。
 *
 * 一页一断言会让第一个泄漏挡住后面的；这个契约是「全都不许漏」，一次看全更有用。
 */
async function auditPages(page: Page, role: RoleName, paths: string[]) {
  const leaks: string[] = [];
  for (const path of paths) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    const codes = await leakedCodes(page.locator('body'));
    for (const code of codes) leaks.push(`${path} → ${code}`);
    // 页签内容默认不渲染，只扫落地那一屏会漏掉后面几个页签。
    // 复测计划页签的 `{{ plan.status }}` 就是这样漏过第一遍扫描的。
    const tabs = page.locator('button.tab');
    for (let i = 0; i < (await tabs.count()); i += 1) {
      await tabs.nth(i).click();
      const label = (await tabs.nth(i).innerText()).trim();
      for (const code of await leakedCodes(page.locator('body'))) {
        leaks.push(`${path} · ${label}页签 → ${code}`);
      }
    }
  }
  expect(leaks, `${role} 的页面把后端编码原样显示了`).toEqual([]);
}

/**
 * 弹窗内容只有点开才存在，而且往往是点开后才去请求数据的。
 *
 * `ready` 是「数据到位」的正信号，必须传：不传的话扫描会发生在 loading 骨架屏
 * 那一帧，`v-for` 还一行都没渲染，于是什么都扫不到、测试假绿。
 */
async function auditModal(
  page: Page,
  openLabel: string,
  where: string,
  leaks: string[],
  ready: Locator,
) {
  await page.getByRole('button', { name: openLabel }).first().click();
  const modal = page.locator('.modal-panel').first();
  await expect(modal).toBeVisible();
  await expect(ready).toBeVisible();
  for (const code of await leakedCodes(modal)) leaks.push(`${where}（弹窗）→ ${code}`);
}

/**
 * 挑一个真的有复测计划的学生。
 *
 * 复测计划是逐个学生建的，演示数据里只有少数几个有。写死 `/counselor/cases/1`
 * 会让 `v-for` 一行都不渲染，测试于是「通过」——它扫的是一块空区域。所以先问接口
 * 要一个有数据的 id；数据没了就抛，而不是静默退化成一个恒绿的用例。
 */
async function studentWithRetestPlan(page: Page): Promise<number> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { account: '13800000001', password: '123456', role: 'counselor' },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
  const cases = await (await page.request.get('/api/v1/care-cases', { headers })).json();
  for (const item of cases.data.items as { student_id: number }[]) {
    const detail = await (
      await page.request.get(`/api/v1/care-cases/${item.student_id}`, { headers })
    ).json();
    if (detail.data.retest_plans.length > 0) return item.student_id;
  }
  throw new Error('演示数据里没有任何学生带复测计划，这个用例失去了对象');
}

/**
 * 挑一个真的有对照数据的学生。
 *
 * 与 `studentWithRetestPlan` 同一条教训：随便挑一个学生，`v-for` 可能一行都不渲，
 * 测试于是「通过」——扫的是一块空区域。所以先问接口要一个 `items` 非空的 id。
 */
async function studentWithComparison(page: Page): Promise<number> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { account: '13800000001', password: '123456', role: 'counselor' },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
  const cases = await (await page.request.get('/api/v1/care-cases', { headers })).json();
  for (const item of cases.data.items as { student_id: number }[]) {
    const comparison = await (
      await page.request.get(`/api/v1/care-cases/${item.student_id}/comparison`, { headers })
    ).json();
    if (comparison.data.items.length > 0) return item.student_id;
  }
  throw new Error('演示数据里没有任何学生带对照数据，这个用例失去了对象');
}

test.describe('状态词汇：界面上不得出现后端编码', () => {
  /**
   * 「全部学生」页签（2026-09-17 加）。
   *
   * 它是 `levelLabel` / `statusLabel` / `sourceLabel` 三张表同一处的渲染点，也是
   * 唯一**逐行**渲染「未测评」的地方（`levelLabel(null)` → 「未测评」，§3）。
   *
   * `auditPages` 会把每个 `button.tab` 都点一遍，所以 `/counselor/cases` 那一趟
   * 顺带也会扫到这里——但那是**假绿**：页签内容是切过去之后才请求的，它扫到的是
   * 骨架屏那一帧，一行都还没渲染。所以这一条单独存在，并且**先证明有东西可扫**
   * （这正是 `auditModal` 要 `ready`、复测页签要先问接口的同一个坑）。
   */
  test('重点学生的「全部学生」页签', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases');
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '全部学生' }).click();

    // 名册整个列出，所以一定有多行；等它们渲染完再扫。
    const rows = page.locator('.card-body tbody tr');
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThan(1);
    expect(await leakedCodes(page.locator('body')), '全部学生页签把后端编码原样显示了').toEqual([]);
  });

  test('学生页面', async ({ page }) => {
    await loginAs(page, 'student');
    await auditPages(page, 'student', ['/student/home', '/student/history']);
  });

  test('心理老师页面', async ({ page }) => {
    await loginAs(page, 'counselor');
    await auditPages(page, 'counselor', [
      '/counselor/workbench',
      '/counselor/cases',
      '/counselor/cases/1',
      '/counselor/tasks',
      '/counselor/data',
      '/counselor/analytics',
      '/counselor/audit',
    ]);
  });

  test('德育领导页面', async ({ page }) => {
    await loginAs(page, 'leader');
    await auditPages(page, 'leader', [
      '/leader/overview',
      '/leader/progress',
      '/leader/analytics',
      '/leader/tasks',
      '/leader/audit',
    ]);
  });

  test('管理员页面', async ({ page }) => {
    await loginAs(page, 'admin');
    await auditPages(page, 'admin', [
      '/admin/system',
      '/admin/organization',
      '/admin/scale',
      '/admin/settings',
      '/admin/audit',
    ]);
  });

  /**
   * 工作台的档案弹层曾经一次漏了五个编码（FOLLOWING / KEY_ATTENTION / VALID /
   * REVIEWED / PLANNED），而同一个组件上方的队列列表是翻译过的——列表走的是
   * 标签函数，弹层走的是裸字段。弹层只有点开才存在，所以单独走一遍。
   */
  test('心理老师工作台的档案弹层', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/workbench');
    await page.waitForLoadState('networkidle');

    const leaks: string[] = [];
    await auditModal(page, '进入档案', '工作台', leaks, page.locator('.modal-panel dl').first());
    expect(leaks, '工作台档案弹层把后端编码原样显示了').toEqual([]);
  });

  /**
   * 测评完成明细弹窗显示的是每个学生的任务完成状态。
   *
   * 2026-09-17 起由心理老师来走：测评任务整块从系统管理员身上摘掉了（它是学校业务，
   * 不是系统级配置），`/admin/tasks` 这条路由已经不存在。**这个弹层不能跟着一起消失**——
   * 它上面那三处 `v-if` 条件渲染（本例是任务完成状态 `TARGET_STATUS_LABELS` 那一面）
   * 只有真的打开弹层才看得见，扫不到就等于没守。
   */
  test('测评任务的完成明细弹窗', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    await page.waitForLoadState('networkidle');

    const leaks: string[] = [];
    await auditModal(
      page,
      '查看明细',
      '/counselor/tasks',
      leaks,
      // 等表格出来，否则扫到的是「正在加载明细」那一帧。
      page.locator('.modal-panel tbody tr').first(),
    );
    expect(leaks, '测评完成明细把后端编码原样显示了').toEqual([]);
  });

  /**
   * 复测计划在「历次趋势」页签里，落地那一屏看不到它。
   * `{{ plan.status }}`（PLANNED）就是这样躲过了第一遍全站扫描。
   *
   * 这个页签 2026-09-17 从「复测趋势」改名而来（MHT 是每学期一次的普查，
   * 没有学生单独复测的场景），页签里还多了两张 SVG 图——**图上每一段文字都在
   * `innerText` 里**，所以 `levelLabel` / `dimensionLabel` 漏调一样会在这里变红。
   */
  test('学生档案的历次趋势页签', async ({ page }) => {
    await loginAs(page, 'counselor');
    const studentId = await studentWithRetestPlan(page);

    await page.goto(`/counselor/cases/${studentId}`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '历次趋势' }).click();

    // 先证明「有东西可扫」，否则这个用例只是在扫一块空区域。
    await expect(page.locator('.check-row').first()).toBeVisible();
    await expect(page.locator('svg.chart').first()).toBeVisible();
    expect(await leakedCodes(page.locator('body')), '历次趋势把后端编码原样显示了').toEqual([]);
  });

  /**
   * 「班级对照」页签（2026-09-17 加）。
   *
   * 它是八维度 `dimensionLabel` 的第三个渲染点，也带 `levelLabel` 那一档的相邻
   * 风险：对照行上每一格的维度名与等级都必须走标签函数。落地那一屏看不到它。
   */
  test('学生档案的班级对照页签', async ({ page }) => {
    await loginAs(page, 'counselor');
    const studentId = await studentWithComparison(page);

    await page.goto(`/counselor/cases/${studentId}`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '班级对照' }).click();

    // 先证明有东西可扫：至少一行对照，且图例已经画出来了。
    await expect(page.locator('.cmp-row').first()).toBeVisible();
    await expect(page.locator('.cmp-legend')).toBeVisible();
    expect(await leakedCodes(page.locator('body')), '班级对照把后端编码原样显示了').toEqual([]);
  });
});
