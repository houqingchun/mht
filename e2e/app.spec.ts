import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { loginAs } from './helpers';

/**
 * Make specific endpoints answer 500, so a page's *failure* branch can be tested.
 *
 * Matched on `pathname` equality rather than a glob: a `care-cases` glob would also
 * swallow `/care-cases/assignable-owners` and `/care-cases/export`, which are
 * different endpoints owned by different panels. The body is the project's unified
 * envelope, because `api.ts` parses `body.error.message` out of it.
 *
 * Install this **before** `loginAs` — the workbench loads on mount.
 */
async function failApiPaths(page: Page, byPath: Record<string, string>) {
  for (const [path, message] of Object.entries(byPath)) {
    await page.route(
      (url) => url.pathname === path,
      (route) =>
        route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({
            success: false,
            data: null,
            request_id: 'e2e-failure-probe',
            error: { code: 'INTERNAL_ERROR', message },
          }),
        })
    );
  }
}

/**
 * 同 `failApiPaths`，但按**正则**匹配路径：明细那几条端点的 id 是现场从表里挑的，
 * 写不进一张字面量表。加这一条而不是给上面那个换一个匹配方式，是因为
 * 「整个类别的端点一起失败」与「这一条端点的 id 是几」是两个不同的意思，
 * 上面那段注释里的理由仍然成立。
 */
async function failApiPathsMatching(page: Page, pattern: RegExp, message: string) {
  await page.route(
    (url) => pattern.test(url.pathname),
    (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          data: null,
          request_id: 'e2e-failure-probe',
          error: { code: 'INTERNAL_ERROR', message },
        }),
      })
  );
}

// ========== Auth Tests ==========

test.describe('Authentication', () => {
  test('student can login and see task page', async ({ page }) => {
    await loginAs(page, 'student');
    await expect(page.getByRole('heading', { name: '我的测评任务' })).toBeVisible();
    await expect(page.getByText('2026秋季MHT心理健康筛查')).toBeVisible();
  });

  test('counselor can login and see workbench', async ({ page }) => {
    await loginAs(page, 'counselor');
    await expect(page.getByRole('heading', { name: '今日工作台' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '优先工作队列' })).toBeVisible();
  });

  test('leader can login and see overview', async ({ page }) => {
    await loginAs(page, 'leader');
    await expect(page.getByRole('heading', { name: '德育工作总览' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '重点进展摘要' })).toBeVisible();
  });

  // 「系统管理」2026-09-17 改名「账号与权限」：那一页只剩账号与权限矩阵两件事，
  // 而旧名字把「和系统有关的」都吸了过去（学生导入、题库导入、审计各留了一份副本）。
  test('admin logs in straight to 账号与权限', async ({ page }) => {
    await loginAs(page, 'admin');
    await expect(page.getByRole('heading', { name: '账号与权限' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '账号管理' })).toBeVisible();
  });

  test('wrong password shows error', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: '学生' }).click();
    await page.getByRole('textbox', { name: /学号/ }).fill('S001');
    await page.getByRole('textbox', { name: /密码/i }).fill('wrongpassword');
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page.getByText('账号、角色或密码不正确')).toBeVisible();
  });

  test('logout returns to login page', async ({ page }) => {
    await loginAs(page, 'student');
    await page.getByRole('button', { name: '退出' }).click();
    await expect(page).toHaveURL('/login');
  });
});

// ========== 账号管理 ==========

test.describe('账号管理', () => {
  /**
   * 新建一个员工账号，然后把它停用。
   *
   * 这一条盯的是**入口存不存在**——在此之前心理老师与德育领导的账号只能来自
   * `db/seed.py`，界面上一个建号的地方都没有（学生那一侧一直有：名册导入）。
   * 范围本身的语义（班级 / 年级 / 全校各收窄到什么）由
   * `backend/app/tests/test_account_admin_api.py` 在一次性库上逐档验证：
   * e2e 跑的是共享的开发库，造不出「另一所学校的学生」这种夹具。
   *
   * 两处刻意的取舍：
   * - **账号用时间戳**：账号是唯一约束，写死一串数字的话第二次跑就会撞
   *   「已经有一个员工账号在用了」，红的却不是功能坏了。`1` 开头凑够 11 位，
   *   与种子那两个（13800000001 / 2）不会撞。
   * - **范围选「全校」**：选年级或班级会在 `user_scope` 里留下一条指着**演示**
   *   年级 / 班级的行，而 `make purge-demo` 遇到仍被引用的班级会跳过不删
   *   （`db/purge.py`）——跑一次 e2e 就让那次清理留下一块擦不掉的残渣。
   */
  test('admin creates a staff account, then disables it', async ({ page }) => {
    await loginAs(page, 'admin');
    const account = `1${String(Date.now()).slice(-10)}`;

    await page.getByRole('button', { name: '新建账号' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('角色').selectOption('counselor');
    await dialog.getByLabel('姓名').fill('验收老师');
    await dialog.getByLabel('登录账号').fill(account);
    await dialog.getByLabel('临时密码').fill('e2e-temp-pass');
    await dialog.getByLabel('数据范围').selectOption({ index: 1 });
    await dialog.getByRole('button', { name: '创建账号' }).click();

    // 一次性密码：关掉之后就再也读不出来，所以它必须先出现在屏幕上。
    await expect(page.getByText('e2e-temp-pass')).toBeVisible();
    await page.getByRole('button', { name: '我已记录' }).click();

    // 先用搜索框把它挑出来再断言。账号表是分页的（每页 10 条）而新账号 id 最大，
    // 不搜的话它落在最后一页——那样这条用例断言的就成了「演示数据有多少个账号」，
    // 而 §测试注意记着行数不是功能对错（`make seed-demo` 会给每个学生建账号）。
    await page.getByPlaceholder('搜索姓名或账号').fill(account);
    const row = page.getByRole('row', { name: new RegExp(account) });
    await expect(row).toBeVisible();
    // 范围那一列渲染的是服务端发回来的**校名**，而校名是操作员可配的
    // （系统配置 → 机构标识，落成 `system_setting.org.school_name`）。
    // 写死「青禾实验学校」的话，这台机器上有人把校名改成「第十三中学」这条用例就红了，
    // 而红的原因不是功能坏了——与「数行数要问接口要，不要写死」是同一条教训。
    // 问接口要当前的名字，再断言那一列**拼的正是它**。
    const configuredSchoolName = await page.evaluate(async () => {
      const response = await fetch('/api/v1/public/branding');
      return (await response.json()).data.school_name as string;
    });
    expect(configuredSchoolName).toBeTruthy();
    // 三列各断言一次：范围那一列渲染的是服务端发回来的名字，不是 id。
    await expect(row.getByText(`全校 · ${configuredSchoolName}`)).toBeVisible();
    await expect(row.getByText('需改密')).toBeVisible();
    await expect(row.getByText('启用中')).toBeVisible();

    await row.getByRole('button', { name: '停用' }).click();
    // 弹层里那个确认按钮与行内那个同名，所以取最后一个。
    await page.getByRole('button', { name: '停用' }).last().click();
    await expect(row.getByText('已停用')).toBeVisible();
    await expect(row.getByRole('button', { name: '启用' })).toBeVisible();
  });

  /**
   * 枚举列要按中文序排，不是按编码的字母序（§3 第四面）。
   *
   * 「角色」列此前没声明 `order`，于是升序拿到的是 admin → counselor → leader → student
   * 的字母序，显示成「系统管理员 → 心理老师 → 德育领导 → 学生」——那看起来只是个
   * 正常的升序，没人会怀疑它错了。`ROLE_ORDER` 由 `ROLE_LABELS` 的键序生成
   * （员工按职责，学生最后），加新角色时自己跟上。
   *
   * 判据是**整列的顺序**而不是首行：首行在两种序下都可能碰巧对，而「第一页有多少行」
   * 随演示数据的账号数变（§测试注意：断言不该依赖数据量）。每页调到 100 之后逐行
   * 比对下标是不是不降——行数再多也只是多比几行，不会哪天悄悄变松。
   */
  test('the role column sorts by its Chinese order, not by code', async ({ page }) => {
    await loginAs(page, 'admin');
    // 账号表外面是 `<section class="card pad">`，**没有** `.card-body` 那一层
    // （那一层是别的页面的写法）。
    const rows = page.locator('.card tbody tr');
    await expect(rows.first()).toBeVisible();

    await page.getByLabel('每页条数').selectOption('100');
    await page.locator('th', { hasText: '角色' }).click();

    const labelOrder = ['心理老师', '德育领导', '系统管理员', '学生'];
    const labels = (await page.locator('.card tbody tr td:nth-child(3)').allTextContents()).map((t) => t.trim());
    // 先证明有东西可排：空表上的顺序断言恒真。
    expect(labels.length).toBeGreaterThan(1);
    const indices = labels.map((label) => labelOrder.indexOf(label));
    expect(indices).toEqual([...indices].sort((a, b) => a - b));

    // 反方向：降序第一个是序的末端。
    await page.locator('th', { hasText: '角色' }).click();
    await expect(rows.first()).toContainText('学生');
  });
});

// ========== Student Flow Tests ==========

/**
 * Clear the student's answers via the API so each test starts from question 1.
 * The session id is not in the URL (that segment is the *task* id), so look it
 * up from /student/tasks first.
 */
async function resetStudentSession(page: ReturnType<typeof test.extend>) {
  await page.evaluate(async () => {
    const token = localStorage.getItem('xlp_access_token');
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
    const tasksRes = await fetch('/api/v1/student/tasks', { headers });
    const tasks = (await tasksRes.json()).data.items as Array<{ session_id: number | null }>;
    const sessionId = tasks.find((t) => t.session_id)?.session_id;
    if (!sessionId) return;
    await fetch(`/api/v1/assessment-sessions/${sessionId}/reset`, { method: 'POST', headers });
  });
}

async function enterFirstAssessment(page: ReturnType<typeof test.extend>) {
  await loginAs(page, 'student');
  await page.waitForSelector('.task-list, .muted-text');
  await page.locator('.task-card').first().locator('button').click();
  await page.waitForURL(/\/student\/assessment/);
  await resetStudentSession(page);
  await page.reload();
  await page.waitForURL(/\/student\/assessment/);
  await page.waitForTimeout(300);
}

test.describe('Student Assessment Flow', () => {
  // Serial: these tests share one student session and would otherwise
  // overwrite each other's answers when run in parallel workers.
  test.describe.configure({ mode: 'serial' });

  test('student can see assessment tasks', async ({ page }) => {
    await loginAs(page, 'student');
    await expect(page.getByText('2026秋季MHT心理健康筛查')).toBeVisible();
  });

  test('student can start answering questions', async ({ page }) => {
    await loginAs(page, 'student');
    // A completed assessment renders a disabled 已完成 button, so clear the
    // submission first — this test is about the start path, not the end state.
    await resetStudentSession(page);
    await page.reload();
    await page.waitForSelector('.task-list, .muted-text');
    const taskRow = page.locator('.task-card').first();
    await taskRow.waitFor({ timeout: 5000 });
    await taskRow.locator('button').click();

    await page.waitForURL(/\/student\/assessment/);
    // Progress moved into a slim bar above the card so the question itself
    // stays above the fold on phones.
    await expect(page.locator('.assessment-bar')).toContainText(/第 \d+ 题/);
    await expect(page.locator('.question-text')).toBeVisible();
  });

  test('student can answer a question', async ({ page }) => {
    await enterFirstAssessment(page);

    await page.getByRole('button', { name: '是' }).click();
    const selected = page.locator('.answer-grid button.selected');
    await expect(selected).toContainText('是');
  });

  test('student can navigate between questions', async ({ page }) => {
    await enterFirstAssessment(page);

    // Verify navigation buttons exist
    await expect(page.getByRole('button', { name: '上一题' })).toBeVisible();
    await expect(page.getByRole('button', { name: '下一题' })).toBeVisible();
    await expect(page.getByRole('button', { name: '定位未答' })).toBeVisible();
  });

  test('submit and next buttons exist in assessment', async ({ page }) => {
    await loginAs(page, 'student');
    await page.waitForSelector('.task-list, .muted-text');
    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);

    const nextBtn = page.getByRole('button', { name: '下一题' });
    await expect(nextBtn).toBeVisible();
  });

  test('help button is available in assessment', async ({ page }) => {
    await enterFirstAssessment(page);

    const helpBtn = page.getByRole('button', { name: '我想找人聊聊' });
    await expect(helpBtn).toBeVisible();
    await expect(helpBtn).toBeEnabled();
  });
});

// ========== Counselor Workbench Tests ==========

test.describe('Counselor Workbench', () => {
  test('counselor sees metrics strip', async ({ page }) => {
    await loginAs(page, 'counselor');
    const metricLabels = page.locator('.metric-label');
    await expect(metricLabels.getByText('待人工复核')).toBeVisible();
    await expect(metricLabels.getByText('逾期跟进')).toBeVisible();
    await expect(metricLabels.getByText('关注档案总数')).toBeVisible();
    // 口径是「我的学生」而不是「本任务」：这个数按数据范围过滤、跨任务合并，
    // 单任务完成率在测评任务页看。
    await expect(metricLabels.getByText('我的学生完成率')).toBeVisible();
  });

  test('counselor sees student list', async ({ page }) => {
    await loginAs(page, 'counselor');
    await expect(page.getByRole('heading', { name: '优先工作队列' })).toBeVisible();
  });

  test('export is available and the import entry leads somewhere usable', async ({ page }) => {
    await loginAs(page, 'counselor');
    // 2026-09-17：这个按钮曾叫「受控导出」却执行高度关注导出（见「缺陷回归」里的
    // 人数一致性用例），现在两个入口各写各的名字。
    await expect(page.getByRole('button', { name: '导出优先队列' })).toBeVisible();

    // This button used to push to an admin-only page, bouncing the counselor to
    // /login. Student import is deliberately absent: it is account governance.
    await expect(page.getByRole('button', { name: '导入学生' })).toHaveCount(0);
    await page.getByRole('button', { name: '题库导入' }).click();
    await expect(page).toHaveURL('/counselor/data');
    await expect(page.getByRole('heading', { name: 'MHT题库版本导入' })).toBeVisible();
  });
});

// ========== Leader Overview Tests ==========

test.describe('Leader Overview', () => {
  test('leader sees completion metrics', async ({ page }) => {
    await loginAs(page, 'leader');
    const labels = page.locator('.metric-label');
    await expect(labels.getByText('测评完成率')).toBeVisible();
    await expect(labels.getByText('需关注摘要')).toBeVisible();
    // 2026-09-17 由「重点档案」改名：这个数是**在办的档案**数（`progress.length`，
    // 后端已不再下发 CLOSED），而「重点」是本产品里 `KEY_ATTENTION` 等级的名字——
    // 那一档的人数在上面那三档里，是另一个数。
    await expect(labels.getByText('在办关注档案')).toBeVisible();
    await expect(labels.getByText('计划复测')).toBeVisible();
  });

  test('leader sees grade and class statistics', async ({ page }) => {
    await loginAs(page, 'leader');
    // 标题 2026-09-17 由「年级完成情况」改成「年级完成与关注情况」：这一块此前只回答
    // 「谁还没测」，而德育领导要的是「问题集中在哪」。
    await expect(page.getByRole('heading', { name: '年级完成与关注情况' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '班级下钻' })).toBeVisible();
  });

  /**
   * 三档人数是这一页最该先看的东西（2026-09-17 加）。
   *
   * 「需关注 42 人」这个总数说不出学校该做什么，而「一般 380 / 需关注 38 / 重点关注 4」
   * 直接对应三种处置力度。断言的是**图例三个名字都在**，不是某一档的人数——
   * 人数取决于库里那一刻的数据，而演示数据是可重跑的。
   */
  test('leader sees the level band distribution', async ({ page }) => {
    await loginAs(page, 'leader');
    await expect(page.getByRole('heading', { name: '关注等级分布' })).toBeVisible();
    const legend = page.locator('.band-legend .band-name');
    await expect(legend).toHaveText(['一般观察', '需要关注', '重点关注']);
    // 条与图例同源，条上每一段的宽度由后端计数算出；计数不出来时整条是空的。
    await expect(page.locator('.band-bar i').first()).toBeVisible();
  });

  test('leader sees how many need attention per class', async ({ page }) => {
    await loginAs(page, 'leader');
    // 这一页有两张表（重点进展摘要 / 班级下钻），所以先按标题框到那一张，
    // 再等它的表头真的渲染出来——`allInnerTexts()` 是一次性读，读早了拿到空数组，
    // 报的是「表头里没有需关注」，而表其实只是还没画。
    const card = page.locator('article.card', { hasText: '班级下钻' }).first();
    const header = card.locator('table thead');
    await expect(header).toBeVisible();
    await expect(header).toContainText('需关注');
    await expect(header).toContainText('关注占比');
  });
});

// ========== Analytics Page Tests（心理老师与德育领导复用同一组件）==========

test.describe('Analytics', () => {
  /**
   * 口径必须出现在**标题**里，而不只是角落里的小字。
   *
   * 此前心理老师进来看到的是「学校心理筛查统计」加一行「我的授权范围口径」的脚注——
   * 标题说学校，脚注说范围，而标题是唯一会被截图、被转述、被记进会议纪要的那一句。
   */
  test('counselor is told their numbers describe their own scope', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics');
    await expect(page.getByRole('heading', { name: '我的范围筛查统计' })).toBeVisible();
    await expect(page.locator('.page-desc')).toContainText('我的授权范围口径');
  });

  test('leader is told their numbers describe the whole school', async ({ page }) => {
    await loginAs(page, 'leader');
    await page.goto('/leader/analytics');
    await expect(page.getByRole('heading', { name: '学校筛查统计' })).toBeVisible();
    await expect(page.locator('.page-desc')).toContainText('全校口径');
  });

  /**
   * 「关注占比」的分母是**已测评人数**，不是测评次数——复测过的学生只算一次。
   * 这一格此前数的是所有 assessment_result，于是跨场次重复计数、而且只增不减。
   */
  test('counselor sees the attention ratio on its own denominator', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics');
    const foot = page.locator('.metric-foot').nth(1);
    await expect(foot).toContainText('占已测评');
  });

  /**
   * 小范围不给比率（`MIN_COHORT_FOR_AGGREGATE`）：几个人的百分比等于点名。
   * `null` 不是 0——0 是「一个都没有」，null 是「这几个人算出来不足为凭」。
   *
   * 两种文案都收：演示数据里每个班的已测评人数刚好在门槛附近，写死哪一种都会随数据漂移。
   * 真正钉住这条规则的是后端 `test_analytics_basis.py`。
   */
  test('a too-small cohort is labelled instead of given a percentage', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics');
    const cells = page.locator('table tbody tr td:nth-child(7)');
    await expect(cells.first()).toBeVisible();
    for (const text of await cells.allInnerTexts()) {
      expect(text.trim()).toMatch(/^(样本过小|\d+%)$/);
    }
  });

  test('counselor sees the level band distribution', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics');
    const legend = page.locator('.band-legend .band-name');
    await expect(legend).toHaveText(['一般观察', '需要关注', '重点关注']);
  });
});

// ========== Admin System Tests ==========

test.describe('Admin System Management', () => {
  /**
   * 「系统管理」2026-09-17 拆开了：它原来同时是「唯一入口」和「半个副本」——
   * 学生导入/学生列表在 `/admin/organization` 也有一份，题库导入在 `/admin/scale`
   * 也有一份，审计日志有独立页面，而那两个页面上的按钮还指回 `/admin/system`。
   * 现在每样东西只有一处：名册跟组织页走，题库跟量表页走，这一页只剩账号与权限。
   */
  test('每样东西只在一处，系统管理页不再重复别人', async ({ page }) => {
    await loginAs(page, 'admin');

    await expect(page.getByRole('heading', { name: '账号与权限' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '账号管理' })).toBeVisible();
    // 搬走的三块不能再在这里露头，否则就是这次拆分没做完。
    await expect(page.getByRole('heading', { name: '学生导入', exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '学生列表' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'MHT题库导入' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '审计日志' })).toHaveCount(0);
  });

  test('名册与导入归组织学生页', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/organization');
    await expect(page.getByRole('heading', { name: '组织与学生账号' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '学生导入', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '学生列表' })).toBeVisible();
  });

  test('题库导入归量表题库页', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/scale');
    await expect(page.getByRole('heading', { name: 'MHT题库导入' })).toBeVisible();
    // 导入产出的是草稿，发布在这一页上——两者要同屏才说得通。
    await expect(page.getByRole('heading', { name: '全部版本' })).toBeVisible();
  });

  test('admin can see account list', async ({ page }) => {
    await loginAs(page, 'admin');
    // 心理老师 appears twice per row (display name and role), so assert on rows.
    const accounts = page.locator('table', { hasText: '密码状态' });
    // 这里刻意不断言精确行数：seed_demo 会给每个演示学生建账号（共 31 个），
    // 而账号表每页 10 条，行数只反映数据量、不反映功能对错。断言四个种子账号
    // 确实列出即可——它们 id 最小，始终在首页。
    for (const acct of ['S001', '13800000001', '13800000002', 'admin']) {
      await expect(accounts.getByRole('cell', { name: acct, exact: true })).toBeVisible();
    }
  });

  test('admin can see student list', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/organization');
    const students = page.locator('table', { hasText: '学号' }).last();
    await expect(students.getByText('林同学')).toBeVisible();
    await expect(students.getByText('S001')).toBeVisible();
  });

  /**
   * 测评任务整块从系统管理员身上摘掉（2026-09-17）：它是学校业务，不是系统级配置。
   * 导航项、路由、写按钮一起收掉，`/counselor/tasks` 仍由心理老师走
   * （`vocabulary.spec.ts` 的完成明细弹窗用例钉住那一侧）。
   *
   * 后端那一面由 `backend/app/tests/test_task_roles.py` 钉住；这里只管界面——
   * 前端隐藏不是安全措施，所以两边都要有。
   */
  test('系统管理员的导航里没有测评任务', async ({ page }) => {
    await loginAs(page, 'admin');
    const nav = page.locator('nav.nav');
    await expect(nav).toBeVisible();
    await expect(nav.getByText('账号与权限')).toBeVisible();
    await expect(nav.getByText('测评任务')).toHaveCount(0);

    // 直接敲 URL 也不会渲染出任务列表：路由表里已经没有这一条。
    await page.goto('/admin/tasks');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: '测评任务' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '新建任务' })).toHaveCount(0);
  });
});

// ========== Mobile Responsiveness ==========

test.describe('Mobile Responsiveness', () => {
  test('login page works on mobile viewport', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/login');
    // Branding comes from /public/branding, so the login screen and the
    // sidebar finally show the same subtitle — and all three strings are
    // configurable (`系统配置 → 机构标识`). So read the expected values from
    // that same endpoint instead of hardcoding them: 「心晴」写死在这里，
    // 操作员把平台改名成「MHT」之后这条就红了，而红的原因不是功能坏了——
    // 恰恰是那个功能生效了（2026-09-17 实测：它就这么红过一次）。
    const branding = await page.evaluate(async () => {
      const response = await fetch('/api/v1/public/branding');
      return (await response.json()).data as { brand_name: string; brand_subtitle: string };
    });
    expect(branding.brand_name).toBeTruthy();
    await expect(page.getByRole('heading', { name: branding.brand_name })).toBeVisible();
    await expect(page.getByText(branding.brand_subtitle)).toBeVisible();
  });

  test('role tabs are visible on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/login');
    const roleTabs = page.locator('.role-tabs button');
    await expect(roleTabs).toHaveCount(4);
  });
});

// ========== 新增能力（数据接通后） ==========

/**
 * 权限矩阵 2026-09-17 重做。旧版是 5 行 × 4 列的 `<select>` 表格，**20 个下拉框
 * 每一个都列出全部 12 个等级**——于是 `学生 / 组织与账号 = 管理` 这类组合既可选又
 * 可存，存进去不报错，只是永远不会被任何端点认出来。现在每个下拉框只列该项能力
 * 真正接受的等级，每一档下面写着它意味着什么，只提交动过的格子，并提供恢复默认。
 *
 * 这些用例看的是**界面上有几个选项**，那是后端测试看不见的一面：后端能拒绝
 * 非法的格子，但拦不住界面把一个不存在的等级摆出来让人选。
 */
test.describe('权限矩阵', () => {
  test('admin can open the role permission matrix', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    await expect(page.getByRole('heading', { name: '角色权限矩阵' })).toBeVisible();
    // 五项能力各一块，每块四个角色。
    await expect(page.locator('.perm-block')).toHaveCount(5);
    await expect(page.locator('.perm-block').first().locator('.perm-cell')).toHaveCount(4);
    for (const label of ['聚合统计', '学生心理详情', '重点题/原始答卷', '组织与账号', '受控导出']) {
      await expect(page.locator('.perm-block h3', { hasText: label })).toBeVisible();
    }
  });

  test('matrix reflects the backend defaults', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    const block = page.locator('.perm-block', { hasText: '聚合统计' });
    // Cell order follows ROLES: 心理老师 / 德育领导 / 系统管理员 / 学生
    await expect(block.locator('select').nth(0)).toHaveValue('SCOPED');     // counselor
    await expect(block.locator('select').nth(1)).toHaveValue('SCHOOL');     // leader
    await expect(block.locator('select').nth(2)).toHaveValue('NONE');       // admin
    await expect(block.locator('select').nth(3)).toHaveValue('NONE');       // student
  });

  /**
   * 这一条就是重做的理由本身：旧界面对每一个格子都给出全部 12 个等级，
   * 包括那些在这项能力下毫无意义的。列表格数**必须**是一个小于 12 的数。
   */
  test('每个格子只列出这项能力真正接受的等级', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    // 同步函数，不是 async：`await optionsOf(x).allInnerTexts()` 里 `.` 先于 `await` 结合，
    // 一个 Promise 上没有 allInnerTexts，报的是一句与真实原因无关的 TypeError。
    const optionsOf = (capability: string) =>
      page.locator('.perm-block', { hasText: capability }).locator('select').first().locator('option');

    // 用 `toHaveText` 而不是 `allInnerTexts()`：后者是一次性读，读的时候弹层里可能还是
    // 骨架屏（五个能力块一行都没渲染），拿到空数组然后断言失败——测试红了，界面其实没错。
    // `toHaveText` 会重试到超时，而它断言的正是「选项列表」这件事本身。

    // 重点题只有两档：能看（且每次记审计）和不能看。
    await expect(optionsOf('重点题/原始答卷')).toHaveText(['无', '单独授权+审计']);
    // 组织与账号五档，从紧到松。
    await expect(optionsOf('组织与账号')).toHaveText([
      '无', '本人', '查看汇总', '查看必要信息', '管理'
    ]);
    // 学生心理详情三档——「仅摘要」在这里意味着**打不开**档案，正是靠释义说清楚的。
    await expect(optionsOf('学生心理详情')).toHaveText([
      '无', '仅摘要，无正文', '授权范围'
    ]);
  });

  test('选中的等级下面写着它意味着什么', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    const cell = page.locator('.perm-block', { hasText: '学生心理详情' }).locator('.perm-cell').nth(1);
    await expect(cell.locator('.perm-mean')).toContainText('不返回单个学生的档案正文');
    await cell.locator('select').selectOption('SCOPED');
    await expect(cell.locator('.perm-mean')).toContainText('完整档案');
  });

  test('只提交动过的格子，改动清单写在保存按钮上方', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    // 打开即「没有改动」——旧版一按保存就会把全部 20 格整批写进库，
    // 从此那些格子不再跟随出厂配置走（CAPABILITY_DEFAULTS 就此失效）。
    await expect(page.getByRole('button', { name: '没有改动' })).toBeDisabled();
    await expect(page.locator('.perm-diff')).toHaveCount(0);

    // 只动一格。
    const cell = page.locator('.perm-block', { hasText: '聚合统计' }).locator('.perm-cell').nth(1);
    await cell.locator('select').selectOption('NONE');

    const save = page.getByRole('button', { name: '保存配置（1 项）' });
    await expect(save).toBeEnabled();
    const diff = page.locator('.perm-diff');
    await expect(diff).toContainText('聚合统计 · 德育领导');
    await expect(diff).toContainText('学校范围 → 无');
    // 收紧不是放宽，不能一样标成风险。
    await expect(diff).toContainText('收紧');

    // 这一条到此为止**不保存**：测试库是两个 worker 共用的，改掉德育领导的
    // 聚合统计会顺带影响别的用例。持久化由后端
    // `test_updating_matrix_persists_and_audits` 钉住，这里只管界面。
    await page.getByRole('button', { name: '取消' }).click();
    await expect(page.getByRole('heading', { name: '角色权限矩阵' })).toHaveCount(0);
  });

  test('恢复默认把草稿写回出厂配置，同样要保存才生效', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    const cell = page.locator('.perm-block', { hasText: '聚合统计' }).locator('.perm-cell').nth(1);
    await cell.locator('select').selectOption('NONE');
    await expect(cell.locator('.customised-mark')).toBeVisible();

    await page.getByRole('button', { name: '恢复默认' }).click();
    await expect(cell.locator('select')).toHaveValue('SCHOOL');
    // 恢复默认只是改草稿：按钮回到「没有改动」，什么都没写进库。
    await expect(page.getByRole('button', { name: '没有改动' })).toBeDisabled();
    await expect(cell.locator('.customised-mark')).toHaveCount(0);
  });

  test('越权的等级组合根本选不出来（学生拿不到「管理」）', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '配置权限' }).click();

    // 旧界面这一格有全部 12 个等级，「管理」就在里面——存进去不报错，
    // 只是永远不会被任何端点认出来。现在它压根不是选项。
    const studentCell = page.locator('.perm-block', { hasText: '组织与账号' }).locator('.perm-cell').nth(3);
    await expect(studentCell.locator('option', { hasText: '管理' })).toHaveCount(0);
  });

  test('counselor cannot open the permission matrix', async ({ page }) => {
    await loginAs(page, 'counselor');
    // The entry point is admin-only, so it must not be rendered for this role.
    await expect(page.getByRole('button', { name: '配置权限' })).toHaveCount(0);
  });
});

test.describe('已接通的后端数据', () => {
  test('admin sees the real scale version', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/scale');
    await expect(page.getByRole('heading', { name: 'MHT题库与规则版本' })).toBeVisible();
    await expect(page.getByText('MHT-1.1.0').first()).toBeVisible();
    // Question counts come from the DB, not a hardcoded block.
    await expect(page.getByText('100').first()).toBeVisible();
  });

  test('counselor can see the assessment task list', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    await expect(page.getByRole('heading', { name: '测评任务' })).toBeVisible();
    await expect(page.getByText('2026秋季MHT心理健康筛查')).toBeVisible();
  });

  test('audit log renders real records', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');
    await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible();
    // Logging in itself writes an audit row.
    await expect(page.locator('table tbody tr').first()).toBeVisible();
  });

  test('counselor case list renders the queue tabs', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases');
    await expect(page.getByRole('heading', { name: '重点学生与长期跟踪' })).toBeVisible();
    await expect(page.locator('.queue-tab')).toHaveCount(6);
  });

  /**
   * 问接口要名册的行数——**不要写死**。
   *
   * 与 `vocabulary.spec.ts` 的 `studentWithRetestPlan` 同一条教训的轻量版：写死
   * 「28 名学生」会让这条用例在任何人往演示数据里加一个学生之后变红，而红的原因
   * 不是功能坏了。行数只反映数据量（CLAUDE.md 测试注意里「不要给账号表加精确行数
   * 断言」）。拿接口的数字来比对才有意义：断言的是**列表与名册一样长**。
   */
  async function rosterSize(page: Page): Promise<number> {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const students = await (await page.request.get('/api/v1/students', { headers })).json();
    return (students.data.items as unknown[]).length;
  }

  /**
   * 「全部学生」页签（2026-09-17 加）—— 用户报的那件事的正面用例。
   *
   * 在此之前，能看见**单个学生**的三个界面（工作台、重点学生、学生档案）都以关怀档案
   * 为入口，统计分析那一个又是聚合页；于是一名测出「一般观察」、因而不会开档案的学生
   * 在心理老师能到达的界面上不存在。用户刚导入一批校外普查，一个都查不到
   * （「我是刚刚导入了一个测评，但查不到这里的记录，比如张三」）。
   *
   * 这里断言的就是用户那句话的字面意思：**整份名册都在**（列表的条数与名册的行数
   * 相等，而不只是「有测评的那批」），并且没测过的行显示「未测评」。
   */
  test('counselor can see every student, assessed or not', async ({ page }) => {
    const total = await rosterSize(page);
    expect(total).toBeGreaterThan(1);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases');
    await page.getByRole('button', { name: '全部学生' }).click();

    // 口径必须写在界面上，否则读者会把它当成别的东西：这里给出的是**每人最近一场**。
    await expect(page.getByText('每人最近一场测评；未测评的学生也列出')).toBeVisible();

    const rows = page.locator('.card-body tbody tr');
    await expect(rows.first()).toBeVisible();
    // 分页器上的「共 N 条」是整份名册的条数，不是当前这一页的——正是要断言的那个数。
    await expect(page.getByText(`共 ${total} 条`)).toBeVisible();
    // 名册里**从没测过**的学生也在（`seed_demo` 刻意留了一部分没交卷），
    // 它们显示「未测评」而不是被丢掉——这一页存在的理由就是这一格。
    await expect(page.getByText('未测评').first()).toBeVisible();
    // 等级列渲染的是中文而不是编码（词汇契约的第二面由 vocabulary.spec.ts 逐字扫，
    // 这里只钉住「至少有一行是翻译过的等级」）。
    await expect(page.locator('.card-body tbody td .pill').first()).toBeVisible();
  });

  test('the tab is deep-linkable and survives a reload', async ({ page }) => {
    // `?tab=students` 是深链：刷新后仍落在同一个页签上，而不是弹回「重点学生」。
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases?tab=students');
    await expect(page.getByRole('heading', { name: '全部学生测评结果' })).toBeVisible();
    await expect(page.locator('.card-body tbody tr').first()).toBeVisible();
  });

  /**
   * 枚举列排的是**中文序**，不是编码的字母序（2026-09-17 修）。
   *
   * 这一条此前没有任何测试看得见：`DataTable` 的默认排序拿 `row[key]` 去
   * `localeCompare`，点「关注等级」得到的是 一般观察 → 重点关注 → 需要关注
   * （`GENERAL_RANGE`/`KEY_ATTENTION`/`NEEDS_ATTENTION` 的字母序）。它排出来的
   * 东西**看起来只是个正常的升序**，所以没有任何人会发现它错了——这也正是它
   * 能一直活着的原因。现在的判据是 `column.order`（`labels.ts` 的 `*_ORDER`）。
   *
   * 断言的是两端，缺一不可：字母序升序的第一个是「一般观察」，中文序升序的第一个
   * 是「重点关注」。只断言「排完了」的话，把 `order` 摘掉照样绿。
   * 演示数据里四档齐全（含 4 名「未测评」），这条断言才有信号。
   */
  test('enum columns sort by their Chinese order, not by code', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases?tab=students');
    const rows = page.locator('.card-body tbody tr');
    // 先证明有东西可排，再点表头：名册到位之前那张表是**空的**（一帧「没有符合条件的
    // 学生」），那时表头点得动，而点击会被随后替换上去的骨架屏静默吞掉。
    // 「共 N 条」只在 total > 0 时渲染，所以它出现 = 行已经在了。
    await expect(page.getByText(/共 \d+ 条/)).toBeVisible();
    await expect(rows.first()).toBeVisible();

    // 等级：升序第一个是**最重**的那一档。字母序会先出「一般观察」。
    await page.locator('th', { hasText: '关注等级' }).click();
    await expect(rows.first()).toContainText('重点关注');

    // 反方向：降序第一个是最轻的那一档。两个方向一起才算钉住了「序」，
    // 否则一个把「重点关注」排在第一位的偶然也能让上面那条过。
    await page.locator('th', { hasText: '关注等级' }).click();
    await expect(rows.first()).toContainText('一般观察');

    // 来源：升序第一个是「系统内作答」。字母序里 `IMPORTED` 在 `IN_SYSTEM` 前面，
    // 于是「外部导入」会跑到自家记录之上——那是字母序的偶然，不是谁的决定。
    await page.locator('th', { hasText: '来源' }).click();
    await expect(rows.first()).toContainText('系统内作答');

    // 没有等级的行（名册里没测过的那些）排在**最后**，两个方向都是——
    // 它们不属于这个序的任何一端，混进中间会让「最轻的那一档」看起来像有值。
    //
    // 先把每页放到 100：那样「最后一行」就是**整份名册**的最后一行，不必翻页，
    // 也就不必假设名册刚好超过一页（§测试注意：断言不该依赖数据量）。
    await page.getByLabel('每页条数').selectOption('100');
    await page.locator('th', { hasText: '关注等级' }).click();
    await expect(rows.last()).toContainText('未测评');
  });

  test('student sees their completion history', async ({ page }) => {
    await loginAs(page, 'student');
    await page.goto('/student/history');
    await expect(page.getByRole('heading', { name: '我的测评记录' })).toBeVisible();
  });
});

// ========== 学生端信任与进度（P2） ==========

test.describe('学生端信任与进度', () => {
  test('login page states how answers are protected', async ({ page }) => {
    await page.goto('/login');
    // Trust belongs on the first screen a student sees.
    await expect(page.getByText('你的回答不会在班级中公开')).toBeVisible();
    await expect(page.getByText(/不等同于医学诊断/)).toBeVisible();
  });

  /**
   * 题数取自题库，不是页面里写死的 100（2026-09-17 改）。
   *
   * 断言写成「与接口给的行数相等」而不是「等于 100」——`docs/phase0_rule_freeze.md`
   * 里「MHT 共 100 题」是这一版的实情，而写死数字的断言在题库换版本那天会红，
   * 红的原因却不是功能坏了（与「不要给账号表加精确行数断言」是同一条教训：
   * **断言两者相等**才有意义）。
   */
  test('题数与题干都来自题库，不是写死的 100', async ({ page }) => {
    await loginAs(page, 'student');
    await page.waitForSelector('.task-list, .muted-text');

    const stemsResponse = page.waitForResponse((r) =>
      /\/assessment-sessions\/\d+\/questions$/.test(r.url()),
    );
    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);
    const stems = (await (await stemsResponse).json()).data.items as unknown[];

    await expect(page.locator('.question-text')).toBeVisible();
    await expect(page.locator('.assessment-bar')).toContainText(`共 ${stems.length} 题`);
    // 题干先拉完再渲染，所以一进来看到的一定是真题干——占位卡意味着学生
    // 正对着一串「第 N 题」作答，交上来的答卷事后分辨不出。
    await expect(page.locator('.question-text')).not.toContainText('未加载');
  });

  /**
   * 机房是共用电脑：上一个人在这台机器上留下的进度，不能被下一个学生读到。
   *
   * 老键（`xlp_assessment_state`）里存着**上一个人的答案**，而且旧代码把它合并到
   * 新会话之上（注释写着「服务端是事实来源」，代码是本地覆盖服务端）。
   * 所以这里先写一份「答满 100 题」的假副本，再进答题页：它必须被删掉，
   * 一个答案都不许算数。
   *
   * 断言用 `not.toContainText('已完成 100/')` 而不是「等于 0」：这一组用例与
   * 「Student Assessment Flow」共用同一个学生账号，别的 worker 可能正在答题，
   * 服务端那一刻答了几道不由这条用例决定。而「本地那份 100 题被采用了」是
   * 无论并发如何都不该出现的那一种。
   */
  test('上一个学生在这台电脑上留下的进度不会被读到', async ({ page }) => {
    await loginAs(page, 'student');
    await resetStudentSession(page);
    await page.reload();
    await page.waitForSelector('.task-list, .muted-text');

    await page.evaluate(() => {
      localStorage.setItem(
        'xlp_assessment_state',
        JSON.stringify({
          currentNo: 37,
          answers: Object.fromEntries(
            Array.from({ length: 100 }, (_, index) => [String(index + 1), 'YES']),
          ),
        }),
      );
      // 另一场会话（不是这一场）的光标。会话 id 里带了学生，所以它认不出这一场。
      localStorage.setItem('xlp_assessment_cursor:999999', '42');
    });

    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);
    await expect(page.locator('.question-text')).toBeVisible();

    // 老键里装着别人的答案，读到就该删掉——不是搬进新键。
    expect(await page.evaluate(() => localStorage.getItem('xlp_assessment_state'))).toBeNull();

    const bar = page.locator('.assessment-bar');
    await expect(bar).toContainText(/第 \d+ 题/); // 先证明这一格有内容，再断言它不是那两串
    await expect(bar).not.toContainText('已完成 100/');
    await expect(bar).not.toContainText('第 37 题'); // 老键里的全局光标
    await expect(bar).not.toContainText('第 42 题'); // 别的会话的光标
  });

  test('assessment leads with progress, not chrome', async ({ page }) => {
    await loginAs(page, 'student');
    await resetStudentSession(page);
    await page.reload();
    await page.waitForSelector('.task-list, .muted-text');
    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);
    await page.waitForTimeout(400);

    const bar = page.locator('.assessment-bar');
    await expect(bar).toContainText('第 1 题');
    await expect(bar).toContainText('共 100 题');
    await expect(bar).toContainText(/预计还需约 \d+ 分钟/);

    // The question sits above the secondary actions in the DOM.
    const questionBox = await page.locator('.question-text').boundingBox();
    const secondaryBox = await page.locator('.question-secondary').boundingBox();
    expect(questionBox!.y).toBeLessThan(secondaryBox!.y);
  });

  /**
   * 「题数取自题库」这句话要有可证伪的形态。
   *
   * 这一版真实题库就是 100 题，而 100 与「写死 100」在界面上长得一模一样——
   * 只对着真数据断言，把 `totalQuestions` 改回常量 100 也照样绿。所以这里
   * **把题干接口的响应换成一份 60 题的题库**（不是改演示数据：换题库版本会波及
   * 整个库的评分与其它用例），再看页面是不是跟着变成 60。
   */
  test('题库换成 60 题，页面上就是 60 题', async ({ page }) => {
    await loginAs(page, 'student');
    await page.waitForSelector('.task-list, .muted-text');

    await page.route(/\/assessment-sessions\/\d+\/questions$/, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({
        json: { ...body, data: { ...body.data, items: body.data.items.slice(0, 60) } },
      });
    });

    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);

    const bar = page.locator('.assessment-bar');
    await expect(bar).toContainText('共 60 题');
    await expect(bar).toContainText('已完成 0/60');
  });

  /**
   * 题干拉不到时**没有答题页**（2026-09-17 改）。
   *
   * 此前失败的题干请求被 `catch { questions.value = {} }` 吞掉，页面退化成
   * 「第 N 题（题干未加载，请刷新页面重试）」的占位卡，而**是 / 否按钮照旧可点**。
   * 学生可以对着一串占位符答满一整份卷子并提交，交出来的每一条答案在库里
   * 与真实作答长得一模一样。
   */
  test('题干拉不到就只剩错误和重试，没有可以作答的地方', async ({ page }) => {
    await loginAs(page, 'student');
    await page.waitForSelector('.task-list, .muted-text');

    await page.route(/\/assessment-sessions\/\d+\/questions$/, (route) =>
      route.fulfill({
        status: 500,
        json: {
          success: false,
          data: null,
          request_id: 'e2e',
          error: { code: 'INTERNAL', message: '题库暂时不可用' },
        },
      }),
    );

    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);

    await expect(page.getByText('题库暂时不可用')).toBeVisible();
    await expect(page.getByRole('button', { name: '重试' })).toBeVisible();
    // 关键的一半：答不了。
    await expect(page.getByRole('button', { name: '是' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '否' })).toHaveCount(0);
    await expect(page.locator('.question-text')).toHaveCount(0);
  });

  test('assessment offers a soft exit that saves progress', async ({ page }) => {
    await loginAs(page, 'student');
    await resetStudentSession(page);
    await page.reload();
    await page.waitForSelector('.task-list, .muted-text');
    await page.locator('.task-card').first().locator('button').click();
    await page.waitForURL(/\/student\/assessment/);
    await page.waitForTimeout(400);

    const exit = page.getByRole('button', { name: '暂不继续，保存退出' });
    await expect(exit).toBeVisible();
    await exit.click();
    await page.waitForURL(/\/student\/home/);
  });
});

// ========== 密码轮换 ==========

test.describe('密码轮换', () => {
  test('change-password dialog validates before submitting', async ({ page }) => {
    // Deliberately does not submit: changing a shared account's password would
    // race with the parallel workers that still need to log in as it. The
    // rotation contract itself is covered by backend tests.
    await loginAs(page, 'counselor');
    await page.getByRole('button', { name: '修改密码' }).click();

    await expect(page.getByRole('heading', { name: '修改密码' })).toBeVisible();
    await expect(page.getByPlaceholder('请输入当前密码')).toBeVisible();
    await expect(page.getByPlaceholder('至少 6 位')).toBeVisible();

    // Too-short new password is rejected client-side.
    await page.getByPlaceholder('请输入当前密码').fill('123456');
    await page.getByPlaceholder('至少 6 位').fill('abc');
    await page.getByPlaceholder('请再次输入新密码').fill('abc');
    await page.getByRole('button', { name: '保存新密码' }).click();
    await expect(page.getByText('新密码至少 6 位')).toBeVisible();

    // Mismatched confirmation is caught inline, before any request.
    await page.getByPlaceholder('至少 6 位').fill('abcdef');
    await page.getByPlaceholder('请再次输入新密码').fill('abcdeg');
    await expect(page.getByText('两次输入的新密码不一致')).toBeVisible();
  });

  test('seeded accounts are not gated by forced rotation', async ({ page }) => {
    // The four documented initial accounts opt out, so the gate must not appear.
    await loginAs(page, 'student');
    await expect(page.getByRole('heading', { name: '请先修改初始密码' })).toHaveCount(0);
  });
});

// ========== 表格排序与分页 ==========

test.describe('表格排序与分页', () => {
  test('audit log pages through the full result set', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');

    const pager = page.locator('.table-pager');
    await expect(pager).toBeVisible();
    await expect(page.locator('table tbody tr')).toHaveCount(20);
    await expect(pager).toContainText(/共 \d+ 条/);
    await expect(pager).toContainText('第 1 /');

    await page.getByRole('button', { name: '下一页' }).click();
    await expect(pager).toContainText('第 2 /');
  });

  test('changing page size re-queries the server', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');
    await expect(page.locator('table tbody tr')).toHaveCount(20);

    await page.locator('.pager-controls select').selectOption('10');
    await expect(page.locator('table tbody tr')).toHaveCount(10);
  });

  test('sorting a column toggles direction', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases');

    const sortableHeader = page.locator('th.th-sortable').first();
    await expect(sortableHeader).toHaveAttribute('aria-sort', 'none');

    await sortableHeader.click();
    await expect(sortableHeader).toHaveAttribute('aria-sort', 'ascending');

    await sortableHeader.click();
    await expect(sortableHeader).toHaveAttribute('aria-sort', 'descending');
  });
});

test.describe('审计角色筛选', () => {
  test('role filter spans the whole table, not just the current page', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');

    const pager = page.locator('.table-pager');
    const totalOf = async () => {
      const text = (await pager.innerText()).match(/共 (\d+) 条/);
      return Number(text?.[1] ?? 0);
    };

    const unfiltered = await totalOf();
    expect(unfiltered).toBeGreaterThan(0);

    await page.locator('.toolbar select').selectOption('counselor');

    // Wait for the server round-trip to land rather than reading the old total.
    await expect
      .poll(totalOf, { message: '筛选后总数应下降' })
      .toBeLessThan(unfiltered);
    await expect(page.locator('.table-pager')).toContainText('第 1 /');

    // Every visible row belongs to the selected role.
    // 第 3 列是「角色」——第 2 列 2026-09-17 起是「操作人」（审计要追的是人，
    // 只写角色指不回具体哪位老师）。
    const roles = await page.locator('table tbody tr td:nth-child(3)').allInnerTexts();
    expect(roles.length).toBeGreaterThan(0);
    for (const role of roles) {
      expect(role.trim()).toBe('心理老师');
    }
  });

  /**
   * 审计日志要能回答「**谁**做的」，不只是「哪个角色做的」。
   *
   * 后端 `test_audit_rows_name_the_person_not_just_the_role` 钉的是接口字段；
   * 这里钉界面有没有把它画出来——两者是 §3 那条约定的两个面，缺一个就会出现
   * 「payload 里明明有名字，页面上却只有角色」。
   */
  test('审计日志列出操作人姓名与账号', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');

    // th 的文本里除了列名还有排序指示符（⇅ / ▲ / ▼）。它是装饰，不是列名——
    // 不去掉的话每加一个可排序列都得改这条断言。
    const headerCells = (await page.locator('table thead th').allInnerTexts())
      .map(h => h.replace(/[⇅▲▼]/g, '').trim());
    expect(headerCells.slice(0, 3)).toEqual(['时间', '操作人', '角色']);

    // 登录本身写了一条「登录成功」，所以这一页上必然有一行指名到 admin。
    // 不断言「每一行都有名字」：隔壁 worker 可能正好制造出一条匿名的登录失败行，
    // 而那是后端用例该管的事（`test_anonymous_audit_rows_...`）。
    //
    // 等「操作人」这一格出现再读，而不是等 `tbody tr`：DataTable 的空状态**也是**
    // 一个 `tbody tr`（里面是一个 `td colspan=columns.length`），等它等于没等，
    // 读到第 2 格自然还是空数组。等第 2 格本身，才是等真数据。
    const actors = page.locator('table tbody tr td:nth-child(2)');
    await expect(actors.first()).toBeVisible();
    const texts = await actors.allInnerTexts();
    expect(texts.some(text => text.includes('admin'))).toBe(true);
  });

  test('clearing the filter restores the full count', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');
    const pager = page.locator('.table-pager');
    const before = (await pager.innerText()).match(/共 (\d+) 条/)?.[1];

    await page.locator('.toolbar select').selectOption('leader');
    await page.locator('.toolbar select').selectOption('all');
    expect((await pager.innerText()).match(/共 (\d+) 条/)?.[1]).toBe(before);
  });
});

test.describe('缺陷回归', () => {
  test('workbench tile deep-link actually filters the case list', async ({ page }) => {
    await loginAs(page, 'counselor');
    // The tile used to pass a Chinese label while the list matched status codes,
    // so this landed on an unfiltered list.
    await page.locator('.metric', { hasText: '待人工复核' }).click();
    await expect(page).toHaveURL(/filter=PENDING_REVIEW/);

    const active = page.locator('.queue-tab.active');
    await expect(active).toHaveText('待复核');
  });

  test('reset-password does not pre-fill a credential', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.getByRole('button', { name: '重置密码' }).first().click();
    await expect(page.getByPlaceholder('请设置临时密码（至少 6 位）')).toHaveValue('');
  });

  test('closing a case asks for the review it asserts', async ({ page }) => {
    await loginAs(page, 'counselor');
    // The case may already be CLOSED from a previous run — in which case the
    // close button is correctly absent. Reopen first so the test owns its state.
    await page.evaluate(async () => {
      const token = localStorage.getItem('xlp_access_token');
      const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
      const cases = (await (await fetch('/api/v1/care-cases', { headers })).json()).data.items;
      const closed = cases.find((c) => c.case_status === 'CLOSED');
      if (closed) {
        await fetch(`/api/v1/care-cases/${closed.case_id}/reopen`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ reason: '回归测试前置：重新打开以验证关闭确认' })
        });
      }
    });
    await page.goto('/counselor/cases');
    // Wait for the table rather than sampling it mid-load — an early count()
    // sees the skeleton and skips a test that would otherwise pass.
    const rows = page.locator('table tbody tr');
    await rows.first().waitFor({ timeout: 10000 });
    await rows.first().getByRole('button', { name: '查看档案' }).click();
    await expect(page).toHaveURL(/\/counselor\/cases\/\d+/);

    const closeBtn = page.getByRole('button', { name: '关闭关注档案' });
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();
    // The payload asserts `confirm_follow_up_checked`, so the dialog must ask.
    await expect(page.getByText(/已复核该生的跟进记录与后续安排/)).toBeVisible();
    await page.getByRole('button', { name: '已复核，关闭档案' }).click();

    // 2026-09-17 起这个用例走完整条路，最后再打开回去。
    //
    // 它此前停在确认弹窗那句断言上，于是漏掉了关闭**之后**的那一屏——而 bug 正在那里：
    // `closeCase()` 成功之后紧接着 `await load()` 重新拉详情，后端当时把 CLOSED 的档案
    // 过滤掉了，于是这一页显示「关注档案不存在」。关闭其实成功，用户却看到系统说档案不存在。
    // 停在上一步的测试断言的是「弹窗问了对的问题」，一个字都没提之后会发生什么。
    const dialog = page.locator('.form-dialog-form');
    await dialog.locator('select').selectOption({ index: 1 });
    await dialog.locator('textarea').fill('回归测试：走完关闭流程后立刻读回这一页。');
    await dialog.getByRole('button', { name: '确认关闭' }).click();

    // 关闭之后这一页必须还在，而且要显示已关闭。
    //
    // 先断言**在**（旧 bug 下这里等不到，重试到超时），再说**不在**——
    // 反过来写的话 `toHaveCount(0)` 会在详情还没返回那一帧就通过，
    // 扫的是一块空区域。这与 vocabulary.spec.ts 「先证明有东西可扫」是同一条教训。
    await expect(page.getByRole('button', { name: '重新打开档案' })).toBeVisible();
    await expect(page.locator('.pill', { hasText: '已关闭' })).toBeVisible();
    await expect(page.getByText('关注档案不存在')).toHaveCount(0);

    // 还原：把这个档案打开回去，免得演示库里的 CLOSED 越攒越多
    // （上面的前置逻辑只还原一条，不还原它自己的这一条）。
    await page.getByRole('button', { name: '重新打开档案' }).click();
    await page.getByRole('button', { name: '确认' }).click();
    await dialog.locator('textarea').fill('回归测试还原：关闭流程已验证，重新打开。');
    await dialog.getByRole('button', { name: '确认打开' }).click();
    await expect(page.getByRole('button', { name: '关闭关注档案' })).toBeVisible();
  });

  // ---------------------------------------------------------------------------
  // 2026-09-17 UI/UX 普查的 P0 四条
  // ---------------------------------------------------------------------------

  test('导出的名单与弹窗上写的人数一致，两边都等于文件里的行数', async ({ page }) => {
    await loginAs(page, 'counselor');

    // 用户报的是「导出的和说好的不是一份东西」。此前弹窗写的是队列长度（7），
    // 而请求里根本没带 `student_ids`，后端于是导出**范围内全部档案**（12，
    // 含一份已关闭的）——一份人数对不上的敏感文件出了门。
    //
    // 所以断言要**三处相等**：弹窗上的数、请求体里的名单长度、CSV 的数据行数。
    // 只断言前两个会漏掉「后端收到了名单但没照着筛」这一类问题。
    // 先等 `cases` 真的到位再读——`loginAs` 只等 URL，那一刻队列还是空的，
    // 读到的会是 0 而不是失败。这两句同时充当「先证明有东西可导」：
    // 队列为空的话下面三条会一起退化成 0，测试就白写了。
    const caseTotal = page.locator('.metric', { hasText: '关注档案总数' }).locator('.metric-value');
    await expect(caseTotal).not.toHaveText('0');

    await page.getByRole('button', { name: '导出优先队列', exact: true }).click();
    const shown = await page
      .locator('div.field')
      .filter({ hasText: '导出人数' })
      .locator('input')
      .inputValue();
    const claimed = Number(shown.replace(/[^\d]/g, ''));
    expect(claimed).toBeGreaterThan(0);

    await page.locator('div.field').filter({ hasText: '导出用途' }).locator('select').selectOption({ index: 1 });
    await page.locator('label', { hasText: '我确认该导出用于授权工作范围' }).locator('input').check();

    const requestPromise = page.waitForRequest(
      (r) => r.method() === 'POST' && r.url().includes('/care-cases/export')
    );
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '确认导出' }).click();

    const body = (await requestPromise).postDataJSON() as { student_ids?: number[] };
    expect(body.student_ids ?? []).toHaveLength(claimed);

    const csv = readFileSync((await (await downloadPromise).path())!, 'utf8');
    const dataRows = csv.trim().split('\n').length - 1;
    expect(dataRows).toBe(claimed);
  });

  test('副面板加载失败时说的是失败，不是「没有数据」', async ({ page }) => {
    await failApiPaths(page, {
      '/api/v1/analytics/dimensions': '维度聚合暂时不可用',
      '/api/v1/counselor/reminders': '提醒服务暂时不可用',
    });
    await loginAs(page, 'counselor');

    // 这一页的两个副面板此前用 `Promise.allSettled` 却没有 else 分支，于是被拒的那块
    // 落进 `v-else` 的空态——「尚无已提交的测评数据。」「0 项」「近 30 天内没有待办跟进
    // 或复测。」三句都是**合法的空态文案**，一次 500 借它们说成了「学校没有数据」。
    await expect(page.getByText('维度聚合暂时不可用')).toBeVisible();
    await expect(page.getByText('提醒服务暂时不可用')).toBeVisible();
    await expect(page.getByText('尚无已提交的测评数据')).toHaveCount(0);
    await expect(page.getByText('近 30 天内没有待办跟进或复测')).toHaveCount(0);

    // 队列是主面板，副面板失败不该动它。
    await expect(page.getByRole('heading', { name: '优先工作队列' })).toBeVisible();
  });

  test('整页加载失败时只报错，不摆出一屏空态', async ({ page }) => {
    await failApiPaths(page, {
      '/api/v1/analytics/overview': '总览暂时不可用',
      '/api/v1/care-cases': '档案列表暂时不可用',
    });

    // 分析页：失败时 `overview` 是 null，四个指标卡会全部渲染成 0——而「完成率 0%」
    // 看起来是一个结论，比空态更糟。
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics');
    await expect(page.getByText('总览暂时不可用')).toBeVisible();
    await expect(page.locator('.metric')).toHaveCount(0);

    // 工作台：失败时优先队列会渲染「没有待复核或高优先级的档案」——那也是一句结论。
    await page.goto('/counselor/workbench');
    await expect(page.getByText('档案列表暂时不可用')).toBeVisible();
    await expect(page.getByText('没有待复核或高优先级的档案')).toHaveCount(0);
  });

  test('队列拉不到时，全部学生页签仍然拉得到它自己的数据', async ({ page }) => {
    // 深链直接落到「全部学生」。此前那一次 `loadStudents()` 写在 `load()` 的 try 里面，
    // `getCareCases()` 一失败就永远走不到，于是切过去看到的是一张空表加一句「0 人」。
    await failApiPaths(page, { '/api/v1/care-cases': '档案列表暂时不可用' });
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases?tab=students');

    // 先证明有东西可扫：`/students/results` 是好的，行必须出来。
    //
    // 行数**问接口要**，不写死（`seed_demo` 往演示库里加一个学生就会让写死的数字变红，
    // 而红的原因不是功能坏了——同「不要给账号表加精确行数断言」）。DataTable 客户端
    // 分页 20 行，所以首页显示的是 `min(总数, 20)`。
    const total = await page.evaluate(async () => {
      const token = localStorage.getItem('xlp_access_token');
      const res = await fetch('/api/v1/students/results', {
        headers: { Authorization: `Bearer ${token}` },
      });
      return ((await res.json()).data.items as unknown[]).length;
    });
    expect(total).toBeGreaterThan(0);
    await expect(page.locator('table tbody tr')).toHaveCount(Math.min(total, 20));

    await expect(page.getByText('没有符合条件的学生')).toHaveCount(0);
    await expect(page.getByText('0 人')).toHaveCount(0);
  });
});

// ========== 系统配置 ==========

/**
 * 「系统配置」这一组改的是**操作员自己配的东西**，所以它有一条硬规矩：
 * **用例可以改配置，但不许把改动留在这一组的边界之外。**
 *
 * 此前这里的 `beforeEach` 与 `afterEach` 都调 `/admin/settings/org/reset`，
 * 也就是**每跑一次 e2e 就把 `org` 这一组恢复成出厂值**。用例需要的只是「一个已知的
 * 起点」，出厂值恰好是最方便的那一个——但「恢复成出厂值」与「恢复成原样」是两件事，
 * 在一台有人用过的机器上它们差着一整份配置。用户 17:34 把校名配成「第十三中学」、
 * 品牌名配成「MHT」，17:41 跑一次全量 e2e 就没了（`audit_log` 里 520 条
 * 「重置系统配置」就是这么来的），于是他看到的登录页与顶栏是「青禾实验学校」，
 * 而**配置页里也是**——他不会想到是测试干的。
 *
 * 现在：进这一组之前把 `org` 的当前值**整个读下来**，每组用例结束时按原样 PUT 回去。
 * 用例内部照旧可以 reset（「恢复默认」那一条测的就是它），但它只是这一组中间的
 * 一个状态，不再越过这一组的边界。
 *
 * 这与「跑 e2e 不能改变后续用例看到的统计口径」（缺口 8）是同一条规矩，
 * 只是这一组的破坏面更大：它改的是**人的配置**，不只是库里的行。
 */
type OrgSnapshot = { token: string; values: Record<string, unknown> };

async function readOrgSettings(page: ReturnType<typeof test.extend>): Promise<OrgSnapshot> {
  return page.evaluate(async () => {
    const token = localStorage.getItem('xlp_access_token') ?? '';
    const response = await fetch('/api/v1/admin/settings', {
      headers: { Authorization: `Bearer ${token}` }
    });
    const body = await response.json();
    return { token, values: body.data.values.org };
  });
}

async function writeOrgSettings(
  page: ReturnType<typeof test.extend>,
  snapshot: OrgSnapshot
): Promise<void> {
  await page.evaluate(async (snapshot) => {
    await fetch('/api/v1/admin/settings/org', {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${snapshot.token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ values: snapshot.values })
    });
  }, snapshot);
}

/**
 * Put `org` back to the shipped defaults, so the cases below start from a known
 * state (`school name is configurable` asserts the default as its starting point).
 */
async function resetOrgSettings(page: ReturnType<typeof test.extend>) {
  await page.evaluate(async () => {
    const token = localStorage.getItem('xlp_access_token');
    await fetch('/api/v1/admin/settings/org/reset', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` }
    });
  });
}

test.describe('系统配置', () => {
  // Serial: every case mutates the same `org` namespace, so parallel workers
  // would reset each other's overrides mid-test.
  test.describe.configure({ mode: 'serial' });

  // Read once, before the first reset. Later cases must NOT re-read it: if one
  // of them fails before `afterEach` can restore, a fresh read would record that
  // case's leftovers as "the operator's values" and then faithfully restore them.
  // The admin token is captured here too — `afterEach` runs with whatever token
  // the case left in localStorage, and the counselor case leaves a counselor's.
  let snapshot: OrgSnapshot | null = null;

  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: '系统管理员' }).click();
    await page.getByRole('textbox', { name: /管理员账号/ }).fill('admin');
    await page.getByRole('textbox', { name: /密码/i }).fill('123456');
    await page.getByRole('button', { name: '登录' }).click();
    await page.waitForURL('/admin/system');
    snapshot ??= await readOrgSettings(page);
    await resetOrgSettings(page);
  });

  test.afterEach(async ({ page }) => {
    if (snapshot) await writeOrgSettings(page, snapshot);
  });

  test('admin can open settings and see the business groups', async ({ page }) => {
    await page.goto('/admin/settings');
    await expect(page.getByRole('heading', { name: '系统配置' })).toBeVisible();

    for (const tab of ['机构标识', '关怀词表', '导出与敏感查看', '跟进节奏', '界面阈值']) {
      await expect(page.getByRole('button', { name: new RegExp(tab) })).toBeVisible();
    }
  });

  test('a toast is rendered exactly once', async ({ page }) => {
    // Toast is mounted once in AppLayout. Thirteen feature pages also mounted
    // it, so every message rendered twice — invisible to text-based assertions.
    await page.goto('/admin/settings');
    await page.locator('.settings-grid input').first().fill('临时校名');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.locator('.toast')).toHaveCount(1);
  });

  test('school name is configurable and reaches the shell', async ({ page }) => {
    await page.goto('/admin/settings');

    const field = page.locator('.settings-grid input').first();
    await expect(field).toHaveValue('青禾实验学校');
    await field.fill('测试学校名称');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.locator('.toast', { hasText: '机构标识 已保存' })).toBeVisible();

    // The topbar reads the shared settings cache, so it updates without reload.
    await expect(page.locator('.top-sub')).toContainText('测试学校名称');
  });

  test('counselling hours reach the student help dialog', async ({ page }) => {
    await page.goto('/admin/settings');
    await page.locator('.settings-grid input').nth(4).fill('周一至周五 09:00—11:00');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.locator('.toast', { hasText: '机构标识 已保存' })).toBeVisible();

    const student = await page.context().newPage();
    await student.goto('/login');
    await student.getByRole('button', { name: '学生' }).click();
    await student.getByRole('textbox', { name: /学号/ }).fill('S001');
    await student.getByRole('textbox', { name: /密码/i }).fill('123456');
    await student.getByRole('button', { name: '登录' }).click();
    await student.waitForURL('/student/home');
    await student.getByRole('button', { name: '我想找人聊聊' }).click();
    await expect(student.getByText('周一至周五 09:00—11:00')).toBeVisible();
    await student.close();
  });

  test('reset restores the shipped defaults', async ({ page }) => {
    await page.goto('/admin/settings');
    await page.locator('.settings-grid input').first().fill('临时校名');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.locator('.toast', { hasText: '机构标识 已保存' })).toBeVisible();

    await page.getByRole('button', { name: '恢复默认' }).click();
    await page.getByRole('button', { name: '恢复默认', exact: true }).last().click();
    await expect(page.locator('.toast', { hasText: '已恢复默认值' })).toBeVisible();
    await expect(page.locator('.settings-grid input').first()).toHaveValue('青禾实验学校');
  });

  test('customised values are marked', async ({ page }) => {
    await page.goto('/admin/settings');
    await expect(page.locator('.customised-mark')).toHaveCount(0);

    await page.locator('.settings-grid input').first().fill('临时校名');
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.locator('.toast', { hasText: '机构标识 已保存' })).toBeVisible();
    // Reload so the draft is rebuilt from the server's stored value.
    await page.reload();
    await expect(page.locator('.customised-mark')).toHaveCount(1);
  });

  test('counselor cannot reach settings', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: '心理老师' }).click();
    await page.getByRole('textbox', { name: /手机号/ }).fill('13800000001');
    await page.getByRole('textbox', { name: /密码/i }).fill('123456');
    await page.getByRole('button', { name: '登录' }).click();
    await page.waitForURL('/counselor/workbench');
    await page.goto('/admin/settings');
    await expect(page).toHaveURL('/login');
  });
});

// ========== 无障碍 ==========

/**
 * 这一组与「布局完整性」同一类：断言的是 DOM 属性与**焦点在哪儿**，不是文案。
 *
 * 文案断言对这类缺陷是零信号——`role="dialog"` 有没有、Tab 会不会跑出弹窗、
 * 提示有没有被读屏软件念出来，页面上一个字都不变。
 */
test.describe('无障碍契约', () => {
  test('弹窗把键盘收在里面，关掉之后把焦点还回去', async ({ page }) => {
    await loginAs(page, 'counselor');
    const opener = page.getByRole('button', { name: '导出优先队列' });
    await opener.click();

    const panel = page.locator('.modal-panel').first();
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute('aria-modal', 'true');

    const inPanel = () =>
      page.evaluate(() => Boolean(document.activeElement?.closest('.modal-panel')));
    // 焦点先落进面板，而不是留在背后那个按钮上。
    await expect.poll(inPanel).toBe(true);

    // 走遍弹窗里的可聚焦元素之后再按 Tab：**仍然**在弹窗里。
    // 没有焦点陷阱时这一步会把焦点交给背后的页面——用户看不见它在哪，
    // 回车按下去是背后那个按钮。
    for (let i = 0; i < 15; i += 1) await page.keyboard.press('Tab');
    await expect.poll(inPanel).toBe(true);
    await page.keyboard.press('Shift+Tab');
    await expect.poll(inPanel).toBe(true);

    // 打开期间锁滚动，关上就解锁——残留的 `body{overflow:hidden}` 会让整页滚不动。
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');

    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
    await expect(opener).toBeFocused();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
  });

  test('可排序的表头键盘到得了，不可排序的表头不带 aria-sort', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases?tab=students');
    // 先证明有东西可排（同 §测试注意里那条：页签内容是切过去之后才请求的）。
    await expect(page.getByText(/共 \d+ 条/)).toBeVisible();

    const sortable = page.locator('th', { hasText: '关注等级' });
    await sortable.focus();
    await expect(sortable).toBeFocused();
    await expect(sortable).toHaveAttribute('aria-sort', 'none');

    await page.keyboard.press('Enter');
    await expect(sortable).toHaveAttribute('aria-sort', 'ascending');
    await page.keyboard.press('Space');
    await expect(sortable).toHaveAttribute('aria-sort', 'descending');

    // 「操作」这一列点不动，所以它**不该**说自己是可排序的。
    // 此前每一列表头都带 `aria-sort`，不可排序的那些一律 `"none"`——读屏软件
    // 据此播报「可排序」，按下去没有任何反应。
    const plain = page.locator('th', { hasText: '操作' });
    expect(await plain.getAttribute('aria-sort')).toBeNull();
    expect(await plain.getAttribute('tabindex')).toBeNull();
  });

  test('提示出现在活动区域里，读屏用户听得到', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases');
    await page.waitForLoadState('networkidle');

    const live = page.locator('.toast-container');
    await expect(live).toHaveAttribute('role', 'status');
    await expect(live).toHaveAttribute('aria-live', 'polite');

    // 真产生一条提示，而不是只检查那个空容器：活动区域必须**先**在 DOM 里、
    // 内容后到，那一段才会被念出来。这里走「导出但不填用途」那条路——
    // 它只弹一条错误提示，不会写任何数据（选一个人都没有时才出现的「批量分配」
    // 那条路已经走不通了：那些按钮在选中之前根本不渲染）。
    await page.getByRole('button', { name: '高度关注导出' }).click();
    await page.locator('.modal-panel').getByRole('button', { name: '确认导出' }).click();
    await expect(live).toContainText('请选择导出用途');
  });

  test('指标卡键盘到得了，只有真点得动的才有手型光标', async ({ page }) => {
    await loginAs(page, 'counselor');
    const card = page.locator('.metric', { hasText: '待人工复核' });
    await expect(card).toHaveAttribute('role', 'button');
    await expect(card).toHaveCSS('cursor', 'pointer');

    await card.focus();
    await expect(card).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/counselor\/cases/);

    // 德育领导页第四张「计划复测」没有去处：它不该装作能点。
    await loginAs(page, 'leader');
    const inert = page.locator('.metric', { hasText: '计划复测' });
    expect(await inert.getAttribute('role')).toBeNull();
    expect(await inert.getAttribute('tabindex')).toBeNull();
    await expect(inert).toHaveCSS('cursor', 'auto');
  });

  test('弹窗开着切页，滚动锁不留给下一页', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.getByRole('button', { name: '导出优先队列' }).click();
    await expect(page.locator('.modal-panel').first()).toBeVisible();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');

    // 浏览器后退键 = 客户端路由切页：弹窗跟着宿主组件被**卸载**，它不走
    // `modelValue → false` 那条路，所以只有 `onUnmounted` 来得及解锁。
    // 少了它，`body{overflow:hidden}` 跟到下一页——登录页从那一刻起滚不动整页。
    await page.evaluate(() => window.history.back());
    await expect(page).toHaveURL('/login');
    await expect(page.locator('.modal-panel')).toHaveCount(0);
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
  });

  test('页面标题、角色页签状态与密码框标签对读屏软件说得清', async ({ page }) => {
    await page.goto('/login');
    // 角色页签此前只有 `.active` 这个视觉样式，读屏软件听到的是三个一模一样的按钮。
    const counselorTab = page.getByRole('button', { name: '心理老师' });
    await expect(counselorTab).toHaveAttribute('aria-pressed', 'false');
    await counselorTab.click();
    await expect(counselorTab).toHaveAttribute('aria-pressed', 'true');

    await loginAs(page, 'counselor');
    await page.getByRole('button', { name: '修改密码' }).click();
    // 三个 `<label>` 与它们的 `<input>` 曾是兄弟节点且互不关联：`for`/`id` 缺失时
    // 下面三行一个都命中不了，读屏软件在那三个框上念的都是「编辑框，密码」。
    await expect(page.getByLabel(/^当前密码/)).toBeVisible();
    await expect(page.getByLabel(/^新密码/)).toBeVisible();
    await expect(page.getByLabel(/^确认新密码/)).toBeVisible();

    // 22 条路由的 `meta.title` 此前没有任何读者：开到第五个标签就分不出哪个是哪一个。
    await page.goto('/counselor/cases');
    await expect(page).toHaveTitle('心晴 · 重点学生');
  });
});

// ========== 评分规则 ==========

test.describe('量表评分规则', () => {
  // Serial: the rule is shared state, and saving a published rule retires it,
  // so parallel workers would race on which version is active.
  test.describe.configure({ mode: 'serial' });

  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: '系统管理员' }).click();
    await page.getByRole('textbox', { name: /管理员账号/ }).fill('admin');
    await page.getByRole('textbox', { name: /密码/i }).fill('123456');
    await page.getByRole('button', { name: '登录' }).click();
    await page.waitForURL('/admin/system');
  });

  test('rule panel shows the thresholds that drive scoring', async ({ page }) => {
    await page.goto('/admin/scale');

    const panel = page.locator('section.card', { hasText: '评分规则' });
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('MHT-RULE-');

    // Band codes live in inputs, so assert on values rather than text.
    const totalBands = panel.locator('.band-row');
    await expect(totalBands.nth(1).locator('input').first()).toHaveValue('GENERAL_RANGE');
    await expect(totalBands.nth(2).locator('input').first()).toHaveValue('NEEDS_ATTENTION');
    await expect(totalBands.nth(3).locator('input').first()).toHaveValue('KEY_ATTENTION');

    // The boundaries the engine compares against.
    await expect(totalBands.nth(1).locator('input').nth(2)).toHaveValue('55');
    await expect(totalBands.nth(2).locator('input').nth(1)).toHaveValue('56');
    await expect(totalBands.nth(3).locator('input').nth(1)).toHaveValue('65');
  });

  test('a gap between bands is rejected before saving', async ({ page }) => {
    await page.goto('/admin/scale');

    const panel = page.locator('section.card', { hasText: '评分规则' });
    // Lowering the first band's max opens a gap before the second band.
    const firstBandMax = panel.locator('.band-row').nth(1).locator('input[type="number"]').nth(1);
    await firstBandMax.fill('40');

    await expect(panel.getByText(/断档或重叠/)).toBeVisible();
    await expect(panel.getByRole('button', { name: '保存规则' })).toBeDisabled();
  });

  test('saving a published rule versions it rather than mutating in place', async ({ page }) => {
    await page.goto('/admin/scale');

    const panel = page.locator('section.card', { hasText: '评分规则' });
    const versionBefore = (await panel.locator('.muted.tiny').first().innerText()).split(' · ')[0];

    // Read the current value and set a different one, so the test is dirty
    // regardless of what the previous run left behind.
    const threshold = panel.locator('input[type="number"]').first();
    const current = await threshold.inputValue();
    await threshold.fill(current === '7' ? '8' : '7');

    await panel.getByRole('button', { name: '保存规则' }).click();
    // Published rules are versioned, so the operator is warned first.
    await expect(page.getByRole('heading', { name: '保存评分规则' })).toBeVisible();
    await page.getByRole('button', { name: '创建新版本并保存' }).click();

    await expect(page.locator('.toast', { hasText: '已创建新的规则版本' })).toBeVisible();
    // A new version, not an edit of the old one.
    const versionAfter = (await panel.locator('.muted.tiny').first().innerText()).split(' · ')[0];
    expect(versionAfter).not.toBe(versionBefore);

    // Leave the shipped thresholds in place for whatever runs next.
    await page.evaluate(async () => {
      const token = localStorage.getItem('xlp_access_token');
      const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
      const versions = (await (await fetch('/api/v1/scales/versions', { headers })).json()).data.items;
      if (versions[0]?.id) {
        await fetch(`/api/v1/scales/versions/${versions[0].id}/rule/reset`, { method: 'POST', headers });
      }
    });
  });
});

// ========== 布局完整性 ==========

/**
 * Layout regressions are invisible to text assertions.
 *
 * Two real incidents motivated these checks: a stylesheet edit deleted the
 * shared `.login-panel` rule (leaving a dangling selector that merged it into
 * `.brand-row`'s flexbox), and earlier a missing design-token block silently
 * dropped every component rule while all text assertions still passed.
 */
test.describe('布局完整性', () => {
  test('login panel is a centred card, not a horizontal strip', async ({ page }) => {
    await page.goto('/login');
    const panel = page.locator('.login-panel');
    await expect(panel).toBeVisible();

    const box = await panel.boundingBox();
    // The panel has width: min(100%, 460px); without that rule it stretches.
    expect(box!.width).toBeLessThanOrEqual(480);

    // Its children must stack: brand above the role tabs above the form.
    const brand = await page.locator('.brand-row').boundingBox();
    const form = await page.locator('.login-form').boundingBox();
    expect(brand!.y).toBeLessThan(form!.y);
  });

  test('design tokens reached the stylesheet', async ({ page }) => {
    await page.goto('/login');
    // `.brand-mark` is background: `var(--navy)`. If the token block is missing
    // the var resolves to nothing and the colour falls back to transparent.
    const background = await page
      .locator('.brand-mark')
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(background).not.toBe('rgba(0, 0, 0, 0)');
    expect(background).not.toBe('transparent');
  });

  test('core component primitives are styled', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');

    // `.card` carries the surface styling; a missing rule leaves no radius.
    const radius = await page
      .locator('.card')
      .first()
      .evaluate((el) => getComputedStyle(el).borderRadius);
    expect(radius).not.toBe('0px');

    // `.pill` must be an inline pill, not a block of raw text.
    const display = await page
      .locator('.pill')
      .first()
      .evaluate((el) => getComputedStyle(el).display);
    expect(display).toBe('inline-flex');
  });

  /**
   * 「查看明细」弹层里那个筛选框（2026-09-17 补）。
   *
   * 它此前是一个**裸** `<input type="search">` 加一句 inline `min-width`，没有任何
   * 共享类：全站的输入框要么在 `.search-box` 里（搜索/筛选），要么在 `.field` 里
   * （弹窗表单），这一个两处都不在，于是拿到的是**浏览器默认样式**——
   * 量出来 `2px inset rgb(118,118,118)`、`border-radius: 0`、28px 高，
   * 而同一行的「导出CSV」是 34px、满屏别的输入框是 40px / 10px 圆角。
   * 用户看到的就是这一处「输入框样式不统一」。
   *
   * 判据取默认样式的两个特征（`inset` 边框、`0` 圆角），不写死 40px / 10px：
   * 断言的是「它属于全站那一套」而不是「它刚好是这一版的尺寸」。
   * 变异验证：把 `<div class="search-box">` 那层摘掉 → `inset` 与 `0px` 都回来，红。
   */
  test('查看明细里的筛选框走全站的输入框样式，不是浏览器默认的', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');

    // 先证明有东西可点：任务列表是异步拉的，空库上连「查看明细」都不存在。
    await expect(page.locator('tbody tr').first()).toBeVisible();
    await page.getByRole('button', { name: '查看明细' }).first().click();

    const filter = page.locator('.modal-panel input[type=search]');
    await expect(filter).toBeVisible();
    const style = await filter.evaluate((el) => {
      const cs = getComputedStyle(el);
      return {
        borderStyle: cs.borderTopStyle,
        radius: cs.borderTopLeftRadius,
        parentClass: el.parentElement?.className ?? '',
      };
    });
    expect(style.borderStyle, '裸 input 的默认边框是 inset').toBe('solid');
    expect(style.radius, '裸 input 的默认圆角是 0').not.toBe('0px');
    // 形状是 `.search-box` 给的，所以那一层得真在。
    expect(style.parentClass).toContain('search-box');
  });

  test('sidebar is a fixed vertical rail on desktop', async ({ page }) => {
    await loginAs(page, 'counselor');
    const sidebar = page.locator('.sidebar');
    const box = await sidebar.boundingBox();
    // 246px rail; a collapsed grid would make it full-width or zero-height.
    expect(box!.width).toBeGreaterThan(200);
    expect(box!.width).toBeLessThan(300);
  });
});

test.describe('题库发布', () => {
  test('counselor can prepare a draft but not publish it', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');
    // The import card is available — importing only produces a draft.
    await expect(page.getByRole('heading', { name: 'MHT题库版本导入' })).toBeVisible();
    await expect(page.getByText(/需由系统管理员发布/)).toBeVisible();

    // Student import stays administrator-only: it creates accounts.
    await expect(page.getByRole('heading', { name: '学生信息导入' })).toHaveCount(0);

    // And the counselor has no route to the publish action.
    await page.goto('/admin/scale');
    await expect(page).toHaveURL('/login');
  });

  test('admin sees a publish action on draft versions', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/scale');
    // The shipped version is already published, so no publish button is offered
    // for it — the action only appears on drafts.
    const publishedRow = page.locator('tr', { hasText: 'MHT-1.1.0' });
    // Prove the row is there before asserting what is NOT in it: an empty locator has
    // no 发布 button either, so without this line a renamed version turns this into a
    // test of nothing.
    await expect(publishedRow).not.toHaveCount(0);
    await expect(publishedRow.getByRole('button', { name: '发布' })).toHaveCount(0);
  });
});

test.describe('MHT测评记录导入', () => {
  test('counselor sees the per-row reason a student could not be located', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');

    const card = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: 'MHT测评记录导入' }) });
    await expect(card).toBeVisible();

    await card.getByPlaceholder('如 2026年秋季心理普查').fill('e2e 导入校验');

    // A whole file: the 100 question columns have to be there, or the preview fails at
    // the column level (「缺少题号列」) and the per-row reason never gets a chance to render.
    const header = [
      '姓名', '性别', '年龄', '年级', '班级', '所用时间',
      ...Array.from({ length: 100 }, (_, i) => `${i + 1}.题干`)
    ].join(',');
    const cell = (name: string) =>
      [name, '1', '12', '1', '4', '3600秒', ...Array(100).fill('0')].join(',');
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-assessment.csv',
      mimeType: 'text/csv',
      // 两行：第一行缺姓名（任何库里都必然被拒），第二行姓名查无此人（定位失败）。
      buffer: Buffer.from(`${header}\n${cell('')}\n${cell('e2e查无此人')}\n`, 'utf-8')
    });

    await expect(card.getByText('错误 2')).toBeVisible();

    await card.getByRole('button', { name: '查看明细' }).click();
    await expect(page.getByText('缺少姓名')).toBeVisible();

    // 这一行**刻意不断言**是「班级不存在」还是「学生不存在」两种文案里的哪一种：那取决于
    // 库里此刻有没有 `704` 这个班，而库是共享的（`make seed-demo` 只有「1班」，但任何一次
    // 学生信息导入或人工建班都会让 `704` 出现——本机库现在就有）。两种文案都必然写出翻译后的
    // 班级名，那才是这一屏要证明的东西：`年级 1 + 班级 4` 被翻成了 `初一 704`。
    // 「两种原因分开报」这件事由后端测试逐字钉住（tests/test_assessment_import_api.py）。
    await expect(page.getByText(/「初一 704」/)).toBeVisible();

    // 定位不到就一行都不写：确认导入没有令牌可提交。
    await expect(card.getByRole('button', { name: '确认导入' })).toBeDisabled();
  });
});

/**
 * 学生信息导入的「学号已在名册上」。
 *
 * 这一组**只跑「放弃」那一支**：共享的开发库里 `S001` 是种子名册上的林同学，
 * 「覆盖」会把他挪出种子里的班，跑一次就让别的用例看到另一份名册。
 * 「覆盖」本身由后端钉住（`test_student_import_api.py` 逐列断言
 * `test_overwrite_updates_the_roster_entry_in_place` 与
 * `test_overwrite_touches_exactly_the_columns_it_promises`），
 * 这里要看的是**界面有没有地方回答这个问题**——后端发一句 422，
 * 而界面上没有能回答它的控件，正是这次要修的东西。
 */
test.describe('学生信息导入', () => {
  test('学号已在名册上时，界面给的是覆盖/放弃，而不是一句错误', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/organization');

    const card = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '学生导入' }) });
    await expect(card).toBeVisible();

    // 姓名与班级都换掉：覆盖那一支的两条路（改名 / 换班）都落在这同一行里。
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-students.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_no,name,grade,class_name\nS001,e2e不该出现,初二,801\n', 'utf-8')
    });

    // 要点一：它数进「待确认」，不是「错误」。
    await expect(card.getByText('待确认 1')).toBeVisible();
    await expect(card.getByText('错误 0')).toBeVisible();

    // 要点二：没选之前不给提交——后端也会 422，但不该让人走到那一步才发现。
    await expect(card.getByRole('button', { name: '确认导入' })).toBeDisabled();
    await expect(card.getByText('请先选择覆盖或放弃')).toBeVisible();

    // 要点三：冲突行要说清「名册上是谁」和「这一行要写成什么」。
    await expect(card.getByText(/名册上是「[^」]+」，本行要写成「e2e不该出现」/)).toBeVisible();

    await card.locator('.import-conflicts input[value=skip]').check();
    await expect(card.getByRole('button', { name: '确认导入' })).toBeEnabled();
    await card.getByRole('button', { name: '确认导入' }).click();
    await page.getByRole('button', { name: '确认', exact: true }).click();

    await expect(page.locator('.toast', { hasText: '已导入 0 名学生，放弃 1 条' })).toBeVisible();

    // 要点四：「放弃」是真的没动他——那个名字一个单元格都不该出现。
    await expect(page.getByText('e2e不该出现')).toHaveCount(0);
  });
});

/**
 * 完成明细是一所千人学校里最长的一张表——一场普查一千多行，而弹层里那个窗口只有
 * 340px 高。所以这一屏靠的不是滚动，是**筛选**；而「全部」的出路是导出。
 */
test.describe('测评任务的完成明细', () => {
  test('明细能按学生筛选，并能导出完整名单', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');

    // 挑行数最多的那个任务：明细里得真有人可搜。哪个任务最大取决于库里的演示数据，
    // 所以按「已完成 / 总人数」现场挑，不写死任务名。
    //
    // 先等表渲染出来再数：任务列表是异步拉的，`count()` 在那一帧拿到 0 行，
    // 循环就什么也没挑到（这一步第一次写漏了，报的是「应当至少有一个任务」）。
    const rows = page.locator('tbody tr');
    await expect(rows.first()).toBeVisible();
    const count = await rows.count();
    let target = 0;
    let most = -1;
    for (let i = 0; i < count; i++) {
      const m = (await rows.nth(i).innerText()).match(/(\d+)\s*\/\s*(\d+)/);
      if (m && Number(m[2]) > most) [most, target] = [Number(m[2]), i];
    }
    expect(most, '演示数据里应当至少有一个带目标行的任务').toBeGreaterThan(0);

    await page.getByRole('button', { name: '查看明细' }).nth(target).click();

    // 先证明有东西可扫，再断言筛选的结果——空表也能「筛出 0 行」。
    const detailRows = page.locator('.modal-panel tbody tr');
    await expect(detailRows.first()).toBeVisible();
    const before = await detailRows.count();

    // 查无此人：说出「没有匹配」，而不是把表清空让人以为没数据。
    await page.fill('.modal-panel input[type=search]', 'zzz查无此人zzz');
    await expect(page.getByText(/没有匹配「zzz查无此人zzz」的记录，共 \d+ 条/)).toBeVisible();

    // 清空筛选要还原成原来那么多行——不是「清空之后表空了」。
    await page.fill('.modal-panel input[type=search]', '');
    await expect(detailRows).toHaveCount(before);

    // 「全部」的出路：导出真的下一份 CSV，表头里有关注等级与总分两列。
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: '导出CSV' }).click(),
    ]);
    const path = await download.path();
    const csv = readFileSync(path!, 'utf-8');
    expect(csv.split('\n')[0]).toContain('关注等级');
    expect(csv.split('\n')[0]).toContain('MHT总分');
  });
});

/**
 * 一次失败的读取不能在屏幕上留下**上一次**的答案（2026-09-17 补）。
 *
 * 这一组五条是同一个形状，也正是这一类 bug 的判据：先让一屏成功渲染出内容
 * （否则「它不在那儿了」会因为本来就没有它而通过），再让下一次读取失败或迟到，
 * 最后断言旧内容**不在**了。走的都是**不写数据**的那条路——换文件、连着搜两次、
 * 点开明细、点「放弃修改」——所以可以在这台共享的开发库上随便跑。
 *
 * 剩下两处同类的地方（`CasesPage.load` / `CareCaseDetailPage.load` 的「写入之后重新拉」
 * 那一次失败）**没有 e2e 钉住**，是有意的：它们只有先真写一笔（批量分配、关闭档案）
 * 才够得着，而那会改变共享演示数据、影响后面用例看到的统计口径。那两处的清空
 * 写在代码注释里，并按「看不到数据好过看一份错的」这条既有口径处理。
 */
test.describe('失败与竞态不留下旧数据', () => {
  test('登录失败不是只有「密码不正确」一种说法', async ({ page }) => {
    // 服务端答了话（500）：照它自己的话说。此前这里写死一句「账号、角色或密码不正确」，
    // 于是一次「配置读不出来」也会被报成密码错，让人一直重打密码。
    await failApiPaths(page, { '/api/v1/auth/login': '服务暂时不可用，请稍后再试' });
    await page.goto('/login');
    await page.getByRole('button', { name: '心理老师' }).click();
    await page.getByRole('textbox', { name: /手机号/ }).fill('13800000001');
    await page.getByRole('textbox', { name: /密码/i }).fill('123456');
    await page.getByRole('button', { name: '登录' }).click();

    await expect(page.getByText('服务暂时不可用，请稍后再试')).toBeVisible();
    await expect(page.getByText('账号、角色或密码不正确')).toHaveCount(0);
  });

  test('登录时连服务器都没连上，说的不是「密码不正确」', async ({ page }) => {
    // 一个字节都没收到（后端没起来 / 网断了）：`fetch` 按规范抛 `TypeError`。
    // 这一种此前与密码错共用一句话，而重打密码永远不会让它好起来。
    await page.route('**/auth/login', (route) => route.abort());
    await page.goto('/login');
    await page.getByRole('button', { name: '心理老师' }).click();
    await page.getByRole('textbox', { name: /手机号/ }).fill('13800000001');
    await page.getByRole('textbox', { name: /密码/i }).fill('123456');
    await page.getByRole('button', { name: '登录' }).click();

    await expect(page.getByText('无法连接服务器，请检查网络后重试')).toBeVisible();
    await expect(page.getByText('账号、角色或密码不正确')).toHaveCount(0);
  });

  test('审计页连着搜两次，先发的那个回来晚了也不算数', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/audit');

    const stub = (action: string, total: number) => ({
      success: true,
      data: {
        items: [
          {
            id: 1,
            actor_user_id: null,
            actor_role: 'admin',
            actor_name: null,
            actor_account: null,
            action,
            resource_type: 'STUDENT_CARE_CASE',
            resource_id: '1',
            purpose: null,
            result: 'SUCCESS',
            created_at: '2026-09-17 10:00:00',
          },
        ],
        total,
      },
    });

    await page.route(
      (url) => url.pathname === '/api/v1/audit-logs',
      async (route) => {
        const q = new URL(route.request().url()).searchParams.get('q');
        // 第一条（「慢」）扣住 800ms：它必然落在第二条**之后**返回。
        if (q === '慢') await new Promise((resolve) => setTimeout(resolve, 800));
        await route.fulfill({ json: q === '慢' ? stub('慢的答案', 11) : stub('快的答案', 22) });
      }
    );

    const box = page.getByPlaceholder('搜索行为、对象或用途');
    await box.fill('慢');
    // 越过 300ms 的防抖：第一条请求已经在路上，防抖不撤销已经发出的那一个。
    await page.waitForTimeout(400);
    await box.fill('快');
    await expect(page.getByText('快的答案')).toBeVisible();

    // 等第一条的答案落地之后再断言：没有守卫时它会覆盖第二条的结果，
    // 于是搜索框里写着「快」、表格里答的是「慢」，连总数也一起错。
    await page.waitForTimeout(900);
    await expect(page.getByText('慢的答案')).toHaveCount(0);
    await expect(page.getByText('共 22 条')).toBeVisible();
  });

  test('明细读失败时说「没读到」，不说「暂无完成明细」', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    await expect(page.locator('tbody tr').first()).toBeVisible();

    await failApiPathsMatching(
      page,
      /^\/api\/v1\/assessment-tasks\/\d+\/completion$/,
      '读取完成明细失败'
    );
    await page.getByRole('button', { name: '查看明细' }).first().click();

    await expect(page.locator('.modal-panel .error-state')).toBeVisible();
    // 「暂无完成明细」是一句**关于数据**的话：这里的事实是没读到，
    // 它会把这个班说成一个人都没交卷。
    await expect(page.getByText('暂无完成明细')).toHaveCount(0);
  });

  test('换一份文件而预览失败时，上一份的预览与令牌不留在屏幕上', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/organization');

    const card = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '学生导入' }) });
    await expect(card).toBeVisible();

    // 第一份：S001 已在名册上，于是它会渲染出「待确认 1」与一个**可提交的令牌**。
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-第一份.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_no,name,grade,class_name\nS001,e2e预演,初二,801\n', 'utf-8'),
    });
    await expect(card.getByText('待确认 1')).toBeVisible();

    // 第二份：请求**本身**失败（服务端 500），不是「文件里有错误」。
    await failApiPaths(page, { '/api/v1/students/import/preview': '导入预览失败' });
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-第二份.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_no,name,grade,class_name\nS002,e2e预演2,初二,801\n', 'utf-8'),
    });

    // 上一份的预览必须消失：留着它，「确认导入」交出去的是**上一份文件**的令牌，
    // 而屏幕上写着刚选的那一份——这不是显示错了，是导错了文件。
    // 失败要说话（提示条在卡片外面，它是全站那一个容器），并且**不留**上一份预览。
    await expect(page.locator('.toast', { hasText: '导入预览失败' })).toBeVisible();
    await expect(card.getByText('待确认 1')).toHaveCount(0);
    await expect(card.getByRole('button', { name: '确认导入' })).toHaveCount(0);
  });

  test('换一个版本读失败时，标题栏不留着上一个版本的规则号', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/scale');

    const panel = page.locator('section.card', { hasText: '评分规则' });
    // 先证明有东西可扫：这一条断言的是「它**不在**了」，而它本来就不在的话，
    // 那条断言什么也没证明。
    await expect(panel).toContainText('MHT-RULE-');

    // 「放弃修改」走的是同一个 `load()`，而它只在表单变脏之后才点得动。
    // 改一个输入框不会写任何东西。
    const threshold = panel.locator('input[type="number"]').first();
    const before = await threshold.inputValue();
    await threshold.fill(before === '7' ? '8' : '7');

    await failApiPathsMatching(page, /^\/api\/v1\/scales\/versions\/\d+\/rule$/, '读取评分规则失败');
    await panel.getByRole('button', { name: '放弃修改' }).click();

    await expect(panel.locator('.form-error')).toBeVisible();
    // 留着那一行的话，标题栏写着 `MHT-RULE-1.1.0 · 生效中`、正文写着读取失败——
    // 读者会以为这次失败说的是 1.1.0，而它说的是别的版本。
    await expect(panel).not.toContainText('MHT-RULE-');
  });
});
