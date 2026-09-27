import { test, expect, type APIResponse, type Locator, type Page, type Route } from '@playwright/test';
import { readFileSync } from 'node:fs';
import {
  loginAs,
  VIEWPORTS,
  expectNoHorizontalOverflow,
  expectBottomNavInViewport
} from './helpers';

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
  //
  // 2026-09-27 落点再次前移（V2.0.0 §5.14.5）：管理员登进来第一件要知道的是
  // 「这个系统现在能不能正常用」，而不是「有哪些账号」——账号与权限仍在侧栏，
  // 只是不再是落点。这条用例断的就是那个落点，所以标题必须跟着改。
  test('admin logs in straight to 系统概览', async ({ page }) => {
    await loginAs(page, 'admin');
    await expect(page.getByRole('heading', { name: '系统概览' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '账号概况' })).toBeVisible();
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
    await page.goto('/admin/system');
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

    // 三枚动作收进「操作」菜单（V2.0.0 §5.14.5）：行里只剩一枚按钮，「停用」这个
    // 代价最高的动作因此不再与「编辑」长得一模一样地挤在同一列里。
    await row.getByRole('button', { name: '操作' }).click();
    await page
      .getByRole('dialog', { name: /账号操作/ })
      .getByRole('button', { name: '停用' })
      .click();
    // 点完之后菜单**先收起**、再开这句确认，所以此刻页面上只剩确认那一层。
    // 按弹层标题定位，而不是像从前那样取 `.last()`：`.last()` 依赖两个弹层在 DOM 里
    // 的先后，而那一层正好是这一页最容易动的部分（§22：视觉次序按**打开**次序算，
    // 不按 DOM 次序算）——用 DOM 次序去定位一个按打开次序排序的东西，迟早会指错。
    await page
      .getByRole('dialog', { name: '停用账号' })
      .getByRole('button', { name: '停用' })
      .click();
    await expect(row.getByText('已停用')).toBeVisible();
    // 「启用」只在菜单里，所以要先开菜单才看得见它——停用之后的出路仍在同一个地方，
    // 这是那枚按钮存在的全部意义。
    await row.getByRole('button', { name: '操作' }).click();
    await expect(
      page.getByRole('dialog', { name: /账号操作/ }).getByRole('button', { name: '启用' })
    ).toBeVisible();
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
    await page.goto('/admin/system');
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
 *
 * **2026-09-25 起，这个端点连这一场的评分事实一起清**（`assessment_service.reset_sitting`
 * 删结果、八维度与筛查信号，理由见 PROGRESS §5.9 第 3 项）。对本文件的影响：下面那三条
 * 「进度 / 光标」用例与 `enterFirstAssessment` **都不提交答卷**，所以演示库里那个学生的
 * 这一场跑完之后是「答过、没交过卷」。
 *
 * 从前跑完之后是「答过、没交过卷、却留着上一次那一份分」——那正是旧 `/reset` 留下的
 * 不一致（重答的学生拿回旧分）。清掉它是对的，代价是：这一组跑过之后，那个学生在
 * **按结果说话**的页面上（关注等级、关注率）变成「未测评」，`make seed-demo` 才补得回来。
 * 全量 e2e 里没有任何用例依赖那一份分（演示数据里还有二十多名学生），这一点是跑出来的，
 * 不是推出来的。
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

/**
 * 第一张**可以作答**的任务卡里的那个按钮。
 *
 * 不能再用 `.task-card').first()`（2026-09-19 改）：任务列表按 id 倒序，而演示
 * 数据里 id 最大的那场「初三年级复测任务」的窗口由 `seed_demo` 按 today+20 天
 * 算出来——是一场**还没开始**的测评。后端据此拒开卷子（`effective_task_status`，
 * §12），前端现在也不给这场渲染可点的按钮，所以旧写法会在那张卡上点一个
 * 禁用的按钮，然后停在登录页之外的地方。
 *
 * 这条用例的主题是「学生能开始答题」，按「可点」定位才是它真正要说的事。
 */
function startableTask(page: ReturnType<typeof test.extend>) {
  return page.locator('.task-card button:not([disabled])').first();
}

async function enterFirstAssessment(page: ReturnType<typeof test.extend>) {
  await loginAs(page, 'student');
  await page.waitForSelector('.task-list, .muted-text');
  await startableTask(page).click();
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
    await startableTask(page).waitFor({ timeout: 5000 });
    await startableTask(page).click();

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
    await startableTask(page).click();
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

  /**
   * Phase E 第 5 条（WORKBENCH-UX-01）：首屏优先呈现今天 / 逾期 / 待复核，
   * 未来 7–30 天**只展示摘要**。
   *
   * 这条用例要证明的是第二件事**没有连带吃掉第一件事**——只断言「摘要句存在」的实现，
   * 把 `dueReminders` 写成空数组也一样绿，而那正是这条要求最容易做坏的地方：
   * 折起来的那一刀如果切在 `days >= 0` 上，逾期的那几条会一起从屏幕上消失，看起来
   * 只是「这周刚好没有逾期的」。
   *
   * 判据全部从**服务端同一份清单**现算（`GET /counselor/reminders`），不写死条数：
   * `seed_demo` 给的是 `today + 7`（跟进）与 `today + 30`（复测），演示库放几天之后
   * 那几个数就会漂，写死会让这条用例红在一个与折叠无关的地方（CLAUDE.md 测试注意）。
   * 但**必须先证明有东西可扫**，所以第一句断了 `soon` 非空。
   */
  test('折叠未来提醒之后，今天与逾期的那几条仍然逐条在首屏', async ({ page }) => {
    await loginAs(page, 'counselor')

    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' }
    })
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` }
    const response = await page.request.get('/api/v1/counselor/reminders', { headers })
    await expectOk(response, '这一条失去了取数的手段：GET /counselor/reminders')
    const items = (await response.json()).data.items as Array<{
      when: string
      days: number
      kind: string
      overdue: boolean
    }>

    // 与 `CounselorWorkbenchPage.vue` 的 `REMINDER_SOON_DAYS` 是**同一个 7**
    // （后端只发 `days`，不发这个分界，所以它跨这一条边界只有两处定义）。
    const SOON_DAYS = 7
    const soon = items.filter(r => r.days <= SOON_DAYS)
    const later = items.filter(r => r.days > SOON_DAYS)
    expect(
      soon.length,
      `服务端一周内一条提醒都没有，这条用例会退化成空转。清单：${JSON.stringify(items)}`
    ).toBeGreaterThan(0)

    const card = page.locator('article.card', { has: page.getByRole('heading', { name: '近期提醒' }) })
    // 等首屏真的落地再量：折线与空态那句都是**取数之后**才出现的。
    await expect(card.locator('.timeline-item, .card-body > .muted').first()).toBeVisible()

    // ① **首屏逐条列出的，恰好是 `days <= 7` 那一批。**
    //    判据是每一条的 `when`（「已逾期 1 天」/「今天」/「6 天后」）与服务端逐项对齐，
    //    在「 · 」处切开、**不去解那句中文**（后面跟的是类别中文，那张表在
    //    `labels.ts` 里，在这里抄一份就是第二处定义）。
    //    少了逾期那几条 → 数组不等；折叠没生效、把更远的也列出来 → 也不等。
    const lines = card.locator('.timeline-item')
    await expect(lines).toHaveCount(soon.length)
    const renderedWhen = (await card.locator('.timeline-item > .muted').allTextContents())
      .map(text => text.split(' · ')[0].trim())
    expect(renderedWhen, '首屏那条时间线与服务端一周内的清单对不上').toEqual(soon.map(r => r.when))

    // ② **逾期仍然在首屏**（「优先呈现逾期」的可执行形式）。服务端这一刻有没有逾期
    //    取决于演示库放了多少天，所以断的是「与它一致」而不是「≥1」——后者在刚
    //    `make seed-demo` 完的库上会红（那一批跟进是 `today + 7`），而红的原因与折叠无关。
    await expect(card.locator('.timeline-dot.overdue')).toHaveCount(items.filter(r => r.overdue).length)

    // ③ 未来那一档**只报数**：摘要句当且仅当服务端有 `days > 7` 的条目时出现，
    //    且**按类别各报一段**。后一半不是修辞——那是 `RETEST` 在界面上唯一的落点
    //    （见 `e2e/vocabulary.spec.ts` 里 `UNTRANSLATED_CODES` 那一段注释：这一页守
    //    那两个码的是**负向断言**，扫不到东西照样绿，所以它必须念出类别）。
    const summary = card.locator('p:has-text("未来 7 天之后的提醒有")')
    await expect(summary).toHaveCount(later.length > 0 ? 1 : 0)
    if (later.length) {
      await expect(summary).toContainText(`未来 7 天之后的提醒有 ${later.length} 项`)
      const text = (await summary.textContent()) ?? ''
      const bracketed = text.slice(text.indexOf('（') + 1, text.indexOf('，最近一项'))
      const kinds = [...new Set(later.map(r => r.kind))]
      expect(
        bracketed.split('、').filter(Boolean),
        `摘要句要按类别各报一段（这一刻的类别：${kinds.join('、')}），实际是「${bracketed}」`
      ).toHaveLength(kinds.length)
    }
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

  /**
   * 「要动手的排在前面」（V2.0.0 §5.14.4 第 2 条）。
   *
   * 这一页此前把「测评完成率」摆在第一张，而它是四个数里唯一一个**不需要额外做什么**
   * 的数；三块内容的次序也是反的（分布 → 年级 → 提醒），读者要滚过两块图才看见待办。
   *
   * 排序**不断言「可见」**：一个把最重要的那块排到最后的排版，每一块照样都「可见」——
   * 所以判据是**阅读次序**，由 `boundingBox()` 现量。
   *
   * ★ 指标区与中间那一块都是**多列 CSS grid**，同一行的两个元素 y 完全相等
   * （心理老师工作台那一条在 1280px 下实测过两张卡都是 y=245.1875），所以次序按
   * `(y, x)` 排，不是按 `y` 排——后者的结果取决于 `Array.prototype.sort` 对相等键
   * 是否稳定，而那不是页面上的次序。
   */
  test('领导总览按「要动手的」优先排', async ({ page }) => {
    await loginAs(page, 'leader');

    const measure = async (names: string[], locate: (name: string) => Locator) => {
      const placed: Array<{ name: string; y: number; x: number }> = [];
      for (const name of names) {
        const el = locate(name);
        await expect(el).toBeVisible();
        const box = await el.boundingBox();
        expect(box, `「${name}」量不出位置`).not.toBeNull();
        placed.push({ name, y: box!.y, x: box!.x });
      }
      return [...placed].sort((a, b) => a.y - b.y || a.x - b.x).map((one) => one.name);
    };

    // 异常（要动手的）排在常规（完成率）前面。
    const kpiLabels = ['需关注摘要', '在办关注档案', '计划复测', '测评完成率'];
    const kpiOrder = await measure(kpiLabels, (name) =>
      page.locator('article.metric', { hasText: name })
    );
    expect(
      kpiOrder,
      `四张指标卡的阅读次序是 ${kpiOrder.join(' → ')}，不是 ${kpiLabels.join(' → ')}`
    ).toEqual(kpiLabels);

    // 管理提醒（要动手）→ 年级（背景）→ 分布（事后描述）。
    const sectionLabels = ['管理提醒', '年级完成与关注情况', '关注等级分布'];
    const sectionOrder = await measure(sectionLabels, (name) =>
      page.getByRole('heading', { name, exact: true })
    );
    expect(
      sectionOrder,
      `三块内容的阅读次序是 ${sectionOrder.join(' → ')}，不是 ${sectionLabels.join(' → ')}`
    ).toEqual(sectionLabels);
  });

  /**
   * 每一处可点击都真的到达它指向的那一页（V2.0.0 §5.14.4 第 1 / 3 条）。
   *
   * 「计划复测」那张卡此前是**假可点击**：它长着 `.metric[role="button"]` 的手型光标
   * 与 hover 抬升（`styles.css` 里那条注释点的就是它），却没有去处。所以判据是
   * **点了之后落在哪**，不是它有没有 `cursor: pointer`、也不是它有没有 `role`。
   *
   * 三档去处各验一次，因为它们在实现上是三条不同的路：带筛选（`?filter=retest`）、
   * 带另一个筛选（`?filter=overdue`）、**有去处但不筛**（整份在办名单，`filter: null`）。
   * 第三种最容易写成「顺手也给它加个筛选」——那会让「点进去看到的」与「卡上说
   * 的那个数」不再是同一批人。
   */
  test('领导总览的每一处可点击都真的到达它指向的那一页', async ({ page }) => {
    await loginAs(page, 'leader');

    // ① 「计划复测」→ 已筛选的复测名单。先读卡上那个数，再点。
    const retestCard = page.locator('article.metric', { hasText: '计划复测' });
    await expect(retestCard).toBeVisible();
    const retestCount = Number((await retestCard.locator('.metric-value').innerText()).trim());
    // 先证明有东西可对：0 条时下面那条对账（0 == 0）什么都不证明。红了先去看数据
    // （`make seed-demo` 会种出带复测计划的档案），不是先改断言。
    expect(
      retestCount,
      '演示数据里没有待完成的复测计划，这条对账用例会退化成空转——先跑 make seed-demo'
    ).toBeGreaterThan(0);

    await retestCard.click();
    await expect(page).toHaveURL(/\/leader\/progress\?filter=retest$/);
    // 只断 URL 的话，一个「URL 变了、列表没筛」的实现照样过——所以再断那一页说得出
    // 自己筛的是什么，以及**行数与卡上那个数相等**（§11：指标卡上的数必须与它点进去
    // 的那个列表同源）。这也是「数计划条数 vs 数学生」那个缺陷的界面侧判据。
    await expect(page.locator('.toolbar')).toContainText('有未完成的复测计划');
    await expect(page.locator('.table-pager')).toContainText(`共 ${retestCount} 条`);

    // ② 「管理提醒 → 逾期未跟进」→ 同一个下钻动作的另一档筛选。
    await page.goto('/leader/overview');
    await page.locator('button.check-row', { hasText: '逾期未跟进' }).click();
    await expect(page).toHaveURL(/\/leader\/progress\?filter=overdue$/);
    await expect(page.locator('.toolbar')).toContainText('逾期未跟进');

    // ③ 「管理提醒 → 未分配负责人」→ 第三档筛选。前两步各覆盖一项，**剩下这一项的
    //    `filter: 'unassigned'` 此前没有任何东西读**：它与 `progressFilters.ts` 里
    //    `PROGRESS_FILTERS` 的键是两处定义（一处是字符串字面量、一处是对象键），
    //    漂了不会有任何东西红——而这正是这一期的原报告缺陷（「计划复测」那张假可
    //    点击的卡）同一个形状：一个指向不复存在的目的地的下钻。
    //    它只断 URL 与筛选条文案，不断行数：这一档在演示数据里可能是 0 条
    //    （档案都有负责人），而 0 条**不影响它是否到达了正确的地方**。
    await page.goto('/leader/overview');
    await page.locator('button.check-row', { hasText: '未分配负责人' }).click();
    await expect(page).toHaveURL(/\/leader\/progress\?filter=unassigned$/);
    await expect(page.locator('.toolbar')).toContainText('未分配负责人');

    // ④ 「管理提醒 → 在办关注档案」→ 整份名单，**不带筛选**。
    await page.goto('/leader/overview');
    await page.locator('button.check-row', { hasText: '在办关注档案' }).click();
    await expect(page).toHaveURL(/\/leader\/progress$/);
    // 那一页此时不该有筛选条——它列的就是全部在办档案。
    await expect(page.locator('.toolbar')).toHaveCount(0);
  });

  /**
   * 没有去处的提醒不装出可点击的样子，页面也不假装有拿不到的字段
   * （V2.0.0 §5.14.4 第 3 / 4 / 6 / 7 条）。
   *
   * 「管理提醒」四项里三项有去处（逾期 / 未分配 / 整份在办名单），一项没有
   * （完成率低于阈值的年级——`GradesPage.vue` 没有按完成率筛选的能力）。给四项
   * 都加手型光标就是「计划复测」那张卡原来的毛病，所以判据落在 `role="button"`
   * 这个**属性**上，与 `.metric` 那一处是同一处判据。
   */
  test('领导总览不装没有的去处，也不假装有拿不到的字段', async ({ page }) => {
    await loginAs(page, 'leader');

    // 四项，其中恰好三项有去处。写死这两个数是有意的：四项是组件的构造
    // （`managementAlerts` 的数组长度），不是数据——数据只影响每一项的**值**。
    await expect(page.locator('.check-row')).toHaveCount(4);
    await expect(page.locator('.check-row[role="button"]')).toHaveCount(3);
    // 第三项是唯一没有去处的那一项。它的标签带动态阈值（`完成率低于 60% 的年级`），
    // 所以按位置取，不按文本。这一句只断**内容**（哪一项没有去处）；「它点不动」
    // 那些属性与计算值归「无障碍契约」组的 `指标卡键盘到得了…`——两处断同一个
    // 元素，分工是内容 / 计算值，不重复。
    const notClickable = page.locator('.check-row').nth(2);
    await expect(notClickable).toContainText('完成率低于');

    // 第 4 条：口径要写进界面。不说的话，读者会拿这一页去和上学期比。
    await expect(page.getByText('不提供环比 / 同比')).toBeVisible();
    // 第 6 条：「样本过小」这个取值在两处出现（年级块与班级下钻），所以两处都要有
    // 解释——只在前一处解释等于只对看完整页的人解释。
    await expect(page.getByText('该群体已测评人数太少')).toBeVisible();
    await expect(page.getByText('该班已测评人数太少')).toBeVisible();

    // 第 7 条：重点进展摘要那一列是**遮蔽名**，而且这一页**没有学号**。此前那一格
    // 渲染 `row.student_name · row.student_no`，而 `student_no` 已经不在响应里了
    // ——整格会显示成「林同学 · undefined」，而屏幕上看起来只是有点怪。
    const summary = page.locator('article.card', { hasText: '重点进展摘要' }).first();
    // 先证明有东西可扫（演示数据里有在办档案），否则下面那一条在空表上也成立。
    await expect(summary.locator('tbody tr').first()).toBeVisible();
    // ★ 判据落在**每一行的学生单元格**上，形状是「姓 + 同学」。第一版不是这样，
    //   被两次变异验证连着打掉，两条都记在这里（2026-09-27）：
    //   ① 拿**整块卡片**的 `innerText` 去 `toContain('同学')` 是恒真的——这一块里
    //      有一句静态说明（「学生姓名按「姓 + 同学」遮蔽显示…」）**本身就含这两个字**。
    //      把服务端的 `mask_student_name` 换成真名（M2d），它照样绿。
    //   ② `not.toMatch(/\b\d{8,}\b/)`（「不该出现学号」）在**演示库上**恒真——
    //      演示学号是 `S001` 这种，永远匹配不上 8 位数字（§32 那条「数据依赖的脆弱
    //      判据」）。M2f 把 `student_no` 加回服务端**并**让模板渲染它，它照样绿。
    //   现在这一条边界清楚：那一格除了「姓 + 同学」不许有别的东西，多渲染任何一个
    //   字段都会让它不等（M2f 下红在 `卫同学 · S008`）。
    //   §29：一条恒绿的守卫比没有更糟，它占着「这一条有人守」的位置。
    const cells = await summary.locator('tbody tr td:first-child').allInnerTexts();
    expect(cells.length, '演示数据里应当有在办档案').toBeGreaterThan(0);
    for (const cell of cells) {
      expect(cell.trim(), '学生列应当只有「姓 + 同学」，不夹带别的字段').toMatch(/^\S+同学$/);
    }
  });

  /**
   * 四角色各跑一次键盘主流程（V2.0.0 §5.14.7）——这一条是**德育领导**那一份。
   *
   * 为什么单独写一条：那一行 DoD 要求四个角色各自「只用键盘走完首屏主任务」，而
   * 在此之前只有**心理老师**（`键盘可以走完选报告、续写、保存、发布与导出`）与
   * **学生**（`只靠键盘能选答案、上一题、下一题、定位未答并确认提交`）有。领导与
   * 管理员的首屏此前**没有任何一条用例按过 Tab**：两页上那些 `role="button"
   * tabindex="0"` 是「无障碍契约」组按**计算值**断的（`指标卡键盘到得了…` 只证明
   * `.metric` 拿得到焦点），而「拿到焦点之后按回车真的会发生那件事」没有东西看得见
   * ——§29 那条：声明与实现之间那一段，只有真的按一次才知道。
   *
   * 走的是这一页真正的主任务：**从总览下钻到被筛过的那份名单，再把筛清除掉**。
   * 三段各有各的判据，且都不断行数：这一档在演示库里可能是 0 条（档案都有负责人），
   * 而 0 条不影响「键盘到不到得了」这件事——行数归
   * `领导总览的每一处可点击都真的到达它指向的那一页`。同理**不写死 Tab 次数**：
   * 判据是「按到的那个元素的文本」，那正是 `tabUntil` 存在的理由。
   */
  test('键盘可以从领导总览下钻到名单，再清除筛选', async ({ page }) => {
    await loginAs(page, 'leader');
    // 骨架屏那一帧的卡片是 `div.metric`（`SkeletonBlock`），真正的卡片是 `article`
    // ——不等它落地就开始按 Tab，那 160 次会全部空转在骨架那一帧上，于是红的原因
    // 与键盘可达性毫无关系（`tabUntil` 的 docstring 写着同一条）。
    await expect(page.locator('article.metric').first()).toBeVisible();

    // ① 下钻。「计划复测」那一张（`drillToProgress('retest')`）。
    await tabUntil(
      page,
      (el) => el.tag === 'ARTICLE' && el.text.includes('计划复测'),
      '计划复测指标卡'
    );
    // 先证明焦点真的落在那张卡上，再按回车——否则一个「Tab 停在别处、回车碰巧
    // 触发了别的东西而 URL 也变了」的实现会蒙混过去。
    await expect(page.locator('article.metric', { hasText: '计划复测' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/leader\/progress\?filter=retest$/);

    // ② 落地页等自己的数据。那条筛选说明与「清除筛选」同在一个 `v-if="!loading"`
    //    的分支里，所以它出现即代表这一页已经加载完。
    await expect(page.locator('.toolbar')).toBeVisible();
    await expect(page.locator('.toolbar')).toContainText('有未完成的复测计划');

    // ③ 把筛清除掉：这一枚也必须键盘够得着——只断 URL 的话，一个「下钻可以、
    //    但筛完就出不来」的页面照样过，而那正是读者会照着安排工作的那一句话
    //    （§9：口径要写进界面，退路也一样）。
    await tabUntil(page, (el) => el.text === '清除筛选', '清除筛选按钮');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/leader\/progress$/);
    await expect(page.locator('.toolbar')).toHaveCount(0);
    // 反方向：清完之后名单还在（不是把整页清空了）。
    await expect(page.locator('.page-head h1')).toBeVisible();
  });

  /**
   * 领导的**隐私边界**（V2.0.0 §5.14.7）——与管理员那条同形，但拦下来的东西不一样。
   *
   * 服务端那一道已经钉在
   * `test_sensitive_reads.py::test_a_leader_cannot_reach_the_two_endpoints_that_hold_the_sheet`
   * 上（它同时证明心理老师走得通）。这一条补的是**真实服务 + 界面**那一侧：同一条
   * 边界在这两个面上有没有接线。与 `管理员到不了心理详情页…` 逐字同源，只是角色换了
   * ——**两处不能合并**：管理员被拒是因为「没有心理详情」而**读得到名册**
   * （他的组织与账号是 `MANAGE`），领导被拒是因为心理详情只有 `SUMMARY`，
   * 而他在名册那一档是 `READ_SUMMARY`——`CAPABILITY_LEVELS` 对这一档的解释自己写着
   * 「只看得到人数一类的汇总，看不到名单（当前没有端点在用这一档）」，所以 `/students`
   * 对他也是一道 403。**同一个 403 名单，两个不同的成因**，合成一条就答不上「为什么」。
   *
   * ★ 判据落在**服务端**而不是界面上：前端隐藏不是安全措施（§4），所以第 ① 条什么
   *   都不能证明，真正的门在下面那六条直连请求上。
   *
   * 反方向同样要断（否则「403 全绿」与「整条链子死了」分不开）：领导**该**够得着的
   * 那一面必须真的通。而心理老师那半条正例与后端用例逐字同源——**先证明这条路本身
   * 是通的**，否则一个把六个端点全拆掉的实现也让下面六条通过（而这正是这个文件里
   * 反复出现的那条：「先证明有东西可扫，再断言它干净」）。
   */
  test('领导到不了心理详情与原始答卷，同一个 token 直接请求也是 403', async ({ page }) => {
    await loginAs(page, 'leader')
    const token = await page.evaluate(() => localStorage.getItem('xlp_access_token'))
    expect(token, '登录之后应当拿得到 token').toBeTruthy()
    const headers = { Authorization: `Bearer ${token}` }

    // ① 界面：直接敲 URL 也到不了那一页。
    await page.goto('/counselor/cases')
    await expect(page).toHaveURL('/login')

    // ② 先证明路是通的，顺带取一个真实的学生 id。**不借用领导的接口去找人**：
    //    他读不到名册（见 ④），而 `GET /care-cases` 的每一行里都带着 `student_id`
    //    ——所以这里用心理老师的 token，与后端那条用例的写法一致。
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' }
    })
    const counselor = { Authorization: `Bearer ${(await login.json()).data.access_token}` }
    const caseList = await page.request.get('/api/v1/care-cases', { headers: counselor })
    expect(caseList.status(), '心理老师读得到在办档案').toBe(200)
    const cases = (await caseList.json()).data.items as Array<{ student_id: number }>
    expect(cases.length, '演示数据里应当有在办档案，否则下面那六条是空转').toBeGreaterThan(0)
    const studentId = cases[0].student_id
    expect(
      (await page.request.get(`/api/v1/care-cases/${studentId}`, { headers: counselor })).status(),
      '心理老师读得到这名学生的个案详情（原始答卷与回访正文就在里面）'
    ).toBe(200)

    // ③ 服务端：领导那六条全拒。名单与管理员那条、以及后端用例三处同一份。
    const denied = [
      '/api/v1/care-cases',
      `/api/v1/care-cases/${studentId}`,
      `/api/v1/care-cases/${studentId}/comparison`,
      '/api/v1/students/results',
      `/api/v1/students/${studentId}/key-questions?purpose=排查`,
      `/api/v1/students/${studentId}/assessment-records`
    ]
    for (const path of denied) {
      const response = await page.request.get(path, { headers })
      expect(response.status(), `${path} 应当 403`).toBe(403)
      expect((await response.json()).error.code, path).toBe('ROLE_FORBIDDEN')
    }

    // ④ 名册那一档：**这一条与管理员那条正相反**。管理员读得到名册（他走的是组织与
    //    账号的 `MANAGE`），而领导那一格是 `READ_SUMMARY`——按 `CAPABILITY_LEVELS`
    //    自己的解释，它是「汇总，不是名单」，所以 `GET /students` 是 403。
    //    两条成对才说明白「这两个角色被拒的理由不同」。
    expect(
      (await page.request.get('/api/v1/students', { headers })).status(),
      '领导读的是汇总，不是名册'
    ).toBe(403)

    // ⑤ 反方向：领导自己的那一面照旧（首屏数据 + 报表）。少了这两条，一个把领导
    //    所有请求都拒掉的实现也能让上面全绿，而那不是权限收紧，是这一页瘫了。
    expect((await page.request.get('/api/v1/leader/progress', { headers })).status()).toBe(200)
    expect((await page.request.get('/api/v1/analytics/report', { headers })).status()).toBe(200)
  });
});

/**
 * 「关注等级分布」那张柱形图真的画出来了——**先证明有东西可扫，再断言它干净**
 * （§测试注意：这一条在本文件里已经栽过三次，最近一次是「全部学生」页签扫到了骨架屏那一帧）。
 *
 * **★ 本函数今天没有调用方**（2026-09-27 §5.19）。它是**纵向版**（`layout="columns"`）的
 * 判据，而三处「关注等级分布」在 §5.17 / §5.19 里陆续换成了横向条，最后一处年级对比页换完
 * 之后，`.band-axis` 这一套 DOM 在任何页面上都不再出现——所以这份断言**一条都跑不到**，
 * 留着它的处境与 `ScoreBandBars.vue` 里 `layout="columns"` 那一支是同一个（那条注释写在
 * 组件文件头，含「为什么不删」与「怎么复活」）。
 *
 * **没有删，理由与组件那边一致**：删了它，纵向版真要复活时得照着 git 历史重写这一整段
 * （四层判据、像素对账、区间不写死的理由都在这里），而那是一次独立的改动，不属于本节这两个
 * 优化点（§5.18「禁止为了『完成任务』而制造无必要代码变更」）。代价同样是显式的：**它不再
 * 保护任何东西**，所以别把它读成「纵向版有覆盖」。今天在跑的是 `expectScoreBandRows`。
 *
 * **收的是那张图自己**（`ScoreBandBars.vue` 的根元素 `.band-chart`），不是 `page`。
 * 那张图现在挂在四个屏幕上——全校总览一张、年级页**一个年级一张**、班级页**本班与
 * 同年级各一张**——所以「页面上只有一张」这个前提只在总览那一处成立。第一版写死
 * `page.locator('.band-chart')`，在年级页上会直接撞 Playwright 的严格模式：它报的是
 * 「定位器命中 N 个元素」，而那句话听起来像页面画重了，不像「这份断言假设了一页只有一张图」。
 *
 * 调用点因此一律要**限定作用域**——总览是 `page.locator('.band-chart')`，年级页是
 * `cell.locator('.band-chart')`。这条规矩与版式无关，所以两个函数（本函数与
 * `expectScoreBandRows`）的调用点都按它写；今天在用的是后者。
 *
 * 三档的判据分四层，从「有没有」到「对不对」：
 *
 * 1. **三条都在、次序对**。三条中文是照 `labels.ts` 的 `LEVEL_LABELS` 逐个放的
 *    （一般观察 / 需要关注 / 重点关注），而服务端 `LEVEL_BANDS` 是**同一序**（从轻到重），
 *    所以这里的下标就是档位本身：一个把三档按人数重排的实现会在这里变红。
 *    恒为三条不是巧合——服务端**三档恒发**、一个人都没有的那一档发 `0` 而不是省掉
 *    （`analytics_service` 的 `_level_distribution`），所以这条断言在任何一个任务上都会遇到三条。
 *
 * 2. **柱子高度与人数成比例**，而且量的是 `getBoundingClientRect()` 出来的**真实像素**，
 *    不是内联的那个百分比。这一层是这份断言里唯一有意义的那一句：那根柱子的
 *    `height: 83%` 落在一个 `flex: 1 1 0` 的轨道里才有东西可算，布局一坏（轨道塌成 0 高）
 *    它就是一根 0 高的柱子，**而内联样式里照样写着 83%**——只断 style 属性的写法在这里
 *    全绿，屏幕上一根柱子都没有（写这个组件时就是这么塌过一次的）。
 *
 * 3. **最高的那根离顶留着那两成**（`本档人数 ÷ 最高的一档 ÷ 1.2`，与 `ColumnChart` 的
 *    `max * 1.2` 同一个口径）：贴顶与只剩一小截都是坏的画法，而这两件事在百分比里看不见。
 *
 * 4. **合计那一句与三档人数同源**。三档按人去重、每人只落一档，所以「合计 N 人」必须等于
 *    三者之和（也就是可评价样本）。两个数都从 DOM 里读回来，写死任何一个都会让这条失去意义
 *    ——它交叉验证的正是组件自己在页脚上写下的那句口径。
 *
 * **区间那一条不写死数字**，虽然此刻两个候选任务上都是 `0~55 / 56~64 / 65~100`：
 * 区间随**量表规则版本**走（§6），管理员在演示库里调过分段之后，写死的那一份就会红在一个
 * 与本次改动毫无关系的地方——而「区间真的来自规则版本」这件事，e2e 证明不了（它这一层
 * 只是从像素上确认那三段被渲染了出来），钉住它的是
 * `test_analytics_report_extended.py::test_total_bands_follow_the_rule_version`。
 */
async function expectScoreBands(chart: Locator) {
  // 演示库里两个候选任务都各有 35 人以上的已计算结果（`defaultTask()` 挑的是
  // `completed_targets >= 5` 里 `start_at` 最新的那一场），所以有柱子可量。
  // 一个人都没落进三档时组件换的是空态（`.data-empty`），这一行会先红。
  await expect(chart).toBeVisible();

  const labels = chart.locator('.band-axis .band-label');
  await expect(labels).toHaveCount(3);
  await expect(labels.nth(0)).toHaveText('一般观察');
  await expect(labels.nth(1)).toHaveText('需要关注');
  await expect(labels.nth(2)).toHaveText('重点关注');

  const ranges = chart.locator('.band-axis .band-range');
  await expect(ranges).toHaveCount(3);
  for (const i of [0, 1, 2]) {
    await expect(ranges.nth(i)).toHaveText(/^\d+~\d+ 分$/);
  }

  // 占比那一格是**两种东西之一**：分母够大时是百分比，不够大时是「样本过小」。
  // 后者是服务端发 `null` 的地方，**不是 `0.0%`**——`?? 0` 会把它抹成一句
  // 「这一档一个都没有」的断言（§11），两句话必须长得不一样。
  const rates = chart.locator('.band-axis .band-rate');
  await expect(rates).toHaveCount(3);
  for (const i of [0, 1, 2]) {
    await expect(rates.nth(i)).toHaveText(/^(\d+(\.\d+)?%|样本过小)$/);
  }

  // 人数从 DOM 里读回来（不写死：那是数据量，不是功能对错），下面每一条都拿它与柱子对账。
  const counts = (await chart.locator('.band-figure strong').allInnerTexts()).map(text => Number(text));
  expect(counts).toHaveLength(3);
  expect(counts.every(count => Number.isInteger(count) && count >= 0)).toBe(true);
  const sum = counts.reduce((a, b) => a + b, 0);
  expect(sum).toBeGreaterThan(0);

  const columns = chart.locator('.band-plot .band-col');
  await expect(columns).toHaveCount(3);
  const heights = await columns.evaluateAll((nodes) => nodes.map((node) =>
    node.querySelector('.band-column')?.getBoundingClientRect().height ?? 0
  ));
  expect(heights).toHaveLength(3);

  for (const i of [0, 1, 2]) {
    if (counts[i] === 0) {
      // 0 人的档**不画柱子**：一条 0 高的柱子与「这一档没人」在图上分不开，
      // 而这两件事要说的话不一样（同 §11 那条 `None` vs `0`）。
      await expect(columns.nth(i).locator('.band-column')).toHaveCount(0);
    } else {
      expect(heights[i]).toBeGreaterThan(0);
    }
  }

  // 高度与人数成比例。**三条人数全相等时这一组会空转**，所以下面补一句「全相等就必须一样高」
  // ——两句合起来才不是恒真。
  const maxCount = Math.max(...counts);
  const maxHeight = Math.max(...heights);
  const comparable = counts.some(count => count > 0 && count !== maxCount);
  for (const i of [0, 1, 2]) {
    if (counts[i] === 0) continue;
    if (comparable) expect(Math.abs(heights[i] / maxHeight - counts[i] / maxCount)).toBeLessThan(0.05);
    else expect(heights[i]).toBeCloseTo(maxHeight, 0);
  }

  const trackHeight = await columns.first().locator('.band-track')
    .evaluate((node) => node.getBoundingClientRect().height);
  expect(maxHeight / trackHeight).toBeGreaterThan(0.6);
  expect(maxHeight / trackHeight).toBeLessThan(0.9);

  await expect(chart.locator('.band-foot')).toContainText(`合计 ${sum} 人`);
}

/**
 * 读回那张图页脚上写着的「合计 N 人」。
 *
 * 它与三档人数之和是**同一个数**（`_:total` 与三档分子在服务端同源，`expectScoreBands`
 * 里已经交叉验过一次），所以拿它去与页面别处的数对账是安全的：它比逐档相加少一次解析，
 * 而「这张图说的是几个人」这件事只有一个说法。
 *
 * 取不到时**抛一句人话**，不写成 `text.match(...)![1]`——那样报出来的是
 * 「Cannot read properties of null」，读起来像定位器坏了，而事实是页脚那句话变了。
 */
async function bandFootTotal(chart: Locator): Promise<number> {
  const text = await chart.locator('.band-foot').innerText();
  const matched = text.match(/合计\s*(\d+)\s*人/);
  if (!matched) throw new Error(`.band-foot 里应当写着「合计 N 人」，实际读到的是：${text}`);
  return Number(matched[1]);
}

/**
 * 横向版（`layout="bars"`）的那张「关注等级分布」——§5.17 之后**筛查关注概览**与
 * **班级维度画像**两页用的都是它，§5.19 起**年级维度对比**页的逐年级那几张也换了过来：
 * 三处并排或并列的图现在是**同一个版式**。
 *
 * 与 `expectScoreBands` 是**两个版式、两套 DOM**，所以是两个函数、不是一个带开关的函数：
 * 一个函数里 `if (layout)` 分叉会把两套判据绑在一起，改一版时连带改到另一版，而另一版
 * 此刻可能没有页面在用——那句话在 §5.19 之后**成真了**：纵向版与 `expectScoreBands`
 * 一起没有了调用方（见后者的 docstring 与组件文件头）。纵向版仍归 `expectScoreBands`。
 *
 * 判据逐条对着 §5.17.3 那九条一致性规则，**只断这一个组件能负责的那几条**（跨页一致由
 * 下面那条两页对照的用例管）：
 *
 * 1. **三档、顺序从重到轻**（`LEVEL_ORDER`：重点关注 → 需要关注 → 一般观察）。横向版与
 *    纵向版的档序**不一样，而这是有意的**（见组件文件头）：纵向是给人横着读一列柱子，
 *    横向是给人竖着读三行，先说重的。所以这里断的是 `LEVEL_ORDER`，不是服务端
 *    `LEVEL_BANDS` 那个从轻到重的次序——**照抄纵向版那三行会让这一条红**。
 * 2. **人数与比例同时直接可读**：每一行都有 `.band-count`（`N 人`）与 `.band-rate`
 *    （百分比或「样本过小」），两者都在**行内**、不靠悬浮层。断的正是「同排可见」这件事
 *    ——只断「页面上找得到这三个数」的话，一个把比例塞进 `title` 属性的实现照样绿。
 * 3. **可评价样本量可见，且与页脚同源**：`.band-scope` 写着「可评价 N 人」，页脚写着
 *    「合计 N 人」，两个数都从 DOM 读回来与三档之和交叉验证（写死任何一个都会让这条失去
 *    意义——它验的正是组件自己在两处写下的那个口径）。
 * 4. **条长与比例同源**：条宽 ÷ 轨道宽 ≈ 人数 ÷ 可评价样本。这是**横向版特有的那一条**，
 *    也是它当初改过口径的地方：曾经按「相对最高那一档」归一化，于是班级页并排两格里
 *    人多的那一条反而更短（实测 211px 对 180px），而眼睛一定会横着比过去。**不写死像素**
 *    ——断的是那两个比例相等，容差卡的是「两处算法漂了」。
 * 5. **0 人的档不画条**：与纵向版同一条（§11：`0` 与「没有数据」是两件事）。
 *
 * **比例那一格是两种东西之一**：分母够大时是百分比，不够大时是「样本过小」——后者是服务端
 * 发 `null` 的地方，不是 `0.0%`（§11），所以两支都收。
 */
async function expectScoreBandRows(chart: Locator) {
  // 一个人都没落进三档时组件换的是空态（`.data-empty`），下面第一条会先红。
  await expect(chart).toBeVisible();

  const rows = chart.locator('.band-row');
  await expect(rows).toHaveCount(3);

  const labels = chart.locator('.band-label');
  await expect(labels).toHaveCount(3);
  await expect(labels.nth(0)).toHaveText('重点关注');
  await expect(labels.nth(1)).toHaveText('需要关注');
  await expect(labels.nth(2)).toHaveText('一般观察');

  // 人数与比例逐行读回来（不写死：那是数据量，不是功能对错），两个都要在**本行内**。
  const counts: number[] = [];
  for (const i of [0, 1, 2]) {
    const row = rows.nth(i);
    await expect(row.locator('.band-count')).toHaveText(/^\d+ 人$/);
    await expect(row.locator('.band-rate')).toHaveText(/^(\d+(\.\d+)?%|样本过小)$/);
    counts.push(Number((await row.locator('.band-count strong').innerText()).trim()));
  }
  expect(counts.every(count => Number.isInteger(count) && count >= 0)).toBe(true);
  const sum = counts.reduce((a, b) => a + b, 0);
  expect(sum).toBeGreaterThan(0);

  // 可评价样本量：与三档之和、以及页脚那句「合计 N 人」是同一个数。
  const scopeText = await chart.locator('.band-scope-count').innerText();
  const scopeMatched = scopeText.match(/可评价\s*(\d+)\s*人/);
  if (!scopeMatched) throw new Error(`.band-scope 里应当写着「可评价 N 人」，实际读到的是：${scopeText}`);
  expect(Number(scopeMatched[1])).toBe(sum);
  expect(await bandFootTotal(chart)).toBe(sum);

  for (const i of [0, 1, 2]) {
    const row = rows.nth(i);
    if (counts[i] === 0) {
      // 0 人的档不画条：一条 0 宽的条与「这一档没人」在图上分不开，而紧接着的 `0 人`
      // 已经把这件事说清楚了。
      await expect(row.locator('.band-bar')).toHaveCount(0);
      continue;
    }
    const trackWidth = await row.locator('.band-bar-track').evaluate((node) => node.getBoundingClientRect().width);
    const barWidth = await row.locator('.band-bar').evaluate((node) => node.getBoundingClientRect().width);
    expect(trackWidth).toBeGreaterThan(0);
    expect(Math.abs(barWidth / trackWidth - counts[i] / sum)).toBeLessThan(0.05);
  }
}

/**
 * 按标签定位一张 `KpiCard`，返回**卡片本身**（`.kpi-value` / `.hint` 由调用点自己取）。
 *
 * **不能写成 `page.locator('.kpi', { hasText: '可评价样本' })`**：班级页那张「样本覆盖率」卡的
 * 副标题写的是「可评价样本 / 实际应测」，而 `hasText` 是**子串**匹配，于是它同时命中两张卡
 * ——报出来的是 strict mode violation，读起来像「指标卡画重了」，而卡数一个都没错。
 * 与上面「年级」那条定位器（撞了任务名里的「初三年级」）是同一类：**子串会命中副标题里的引用**，
 * 所以判据落在 `.kpi-label` 上，并且用 `^…$` 收紧。
 */
function kpiCard(page: Page, label: string): Locator {
  return page.locator('.kpi', {
    has: page.locator('.kpi-label', { hasText: new RegExp(`^${label}$`) })
  });
}

/**
 * 读回一个指标格里的**数**：`"2 人"` / `"2"` / `"1,234"` 都读成 2 / 2 / 1234。
 *
 * 两页把同一个数渲染成两种形状——`KpiCard` 只写值，而 `MetricStrip` 那一格是
 * `value + ' 人'`——所以断言「两页相等」的比较对象必须先是数，不能是那两串文本
 * （`"2 人" !== "2"`，那样比出来的是排版而不是数）。
 *
 * 一个数字都取不到时回 `NaN`：后面那两条断言会因此**红**，而不是拿 0 去比（`0 === 0`
 * 是绿的，而屏幕上就是用户报的那个 bug）。这与 §测试注意里「先证明有东西可扫」同一条。
 */
async function numberIn(locator: Locator): Promise<number> {
  const digits = (await locator.innerText()).replace(/[^\d]/g, '')
  return digits ? Number(digits) : NaN
}

// ========== Analytics Report Center（五类报表共用任务选择器）==========

test.describe('Analytics', () => {
  test('counselor analytics entry opens the newest task automatically', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics');
    await expect(page).toHaveURL(/\/counselor\/analytics\/overview$/);
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    await expect(page.getByRole('heading', { name: '关注等级分布' })).toBeVisible();
    // 总览那一张：这一页只有一张 `.band-chart`，所以直接按整页限定。
    // §5.17 起这一页是**横向版**，所以走 `expectScoreBandRows`（纵向版那一个读的是
    // `.band-axis`，在这一页上是 0 条）。
    await expectScoreBandRows(page.locator('.band-chart'));
    await expect(page.locator('.task-option input:checked')).toHaveCount(1);
  });

  test('leader analytics entry opens the report center', async ({ page }) => {
    await loginAs(page, 'leader');
    await page.goto('/leader/analytics');
    await expect(page).toHaveURL(/\/leader\/analytics\/overview$/);
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    await expect(page.getByRole('heading', { name: '关注等级分布' })).toBeVisible();
    // 与心理老师那一页走同一个 helper：**同一套版式**是这一页要证明的事之一
    // （§5.17.1：读者应当把它们认成同一个业务指标在两个范围上的两张表）。
    await expectScoreBandRows(page.locator('.band-chart'));
  });

  test('multiple imported batches can be selected together', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics/overview');
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    await page.locator('.task-picker summary').click();
    const extraTask = page.locator('.task-option input:not(:checked)').first();
    await expect(extraTask).toBeVisible();
    await extraTask.check();
    await page.getByRole('button', { name: /查询/ }).click();
    await expect(page.locator('.task-picker summary')).toHaveText('已选择 2 个任务');
    await expect(page.locator('.task-picker')).not.toHaveAttribute('open', '');
  });

  test('analytics submenu toggles and stays open when another section is selected', async ({ page }) => {
    await loginAs(page, 'counselor');
    const trigger = page.getByRole('button', { name: /统计分析/ });
    const child = page.getByRole('link', { name: '年级维度对比' });
    await expect(child).not.toBeVisible();
    await trigger.click();
    await expect(child).toBeVisible();
    await trigger.click();
    await expect(child).not.toBeVisible();
    await trigger.click();
    await page.getByRole('link', { name: '重点关注学生' }).click();
    await expect(child).toBeVisible();
  });

  /**
   * 小范围不给比率（`MIN_COHORT_FOR_AGGREGATE`）：几个人的百分比等于点名。
   * `null` 不是 0——0 是「一个都没有」，null 是「这几个人算出来不足为凭」。
   *
   * 两种文案都收：演示数据里每个班的已测评人数刚好在门槛附近，写死哪一种都会随数据漂移。
   * 真正钉住这条规则的是后端 `test_analytics_basis.py`。
   */
  test('grade comparison renders a publishable column chart from demo data', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics/grades');
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    const card = page.locator('.chart-card', { hasText: '各年级高分比例' });
    await card.locator('select').selectOption({ label: '冲动倾向' });
    await expect(card.getByRole('heading', { name: '冲动倾向 · 各年级高分比例' })).toBeVisible();
    await expect(card.getByRole('img', { name: '柱形统计图' })).toBeVisible();
    await expect(card.locator('svg rect').first()).toBeAttached();
    await expect(card.locator('svg text').filter({ hasText: '%' }).first()).toBeVisible();
  });

  /**
   * 年级页上的那一张图是**一个年级一张**（`gradesWithSamples`），而「画了几张」与
   * 「每一张画的是不是它自己那个年级的那一份」是两个问题：`v-for` 少画一个年级、
   * 或者把同一份数据发给每一格，图都长得好好的、三档加起来也都对。
   *
   * 所以除了逐格过一遍 `expectScoreBandRows`，还有两条**跨格的对账**：
   * ①格子上的年级名按序恰好是侧栏那张表里「可评价」大于 0 的那些年级（`sample_count > 0`
   * 这个判据在界面上的样子）；②每一格页脚那句「合计 N 人」等于侧栏表里**同一个年级**
   * 的那一格数。②是这一条真正值钱的地方——它把「这一格」与「这个年级」钉在一起，
   * 而单独看任何一边都看不出来。
   *
   * 两个数都是从 DOM 里读回来的，谁都不是写死的（§测试注意：数行数要问接口要）。
   */
  test('grade page draws one level band chart per sampled grade, each from its own cohort', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics/grades');
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();

    const card = page.locator('.band-card');
    await expect(card.getByRole('heading', { name: '各年级关注等级分布' })).toBeVisible();

    // 侧栏「年级样本与覆盖率」的列是 td[0] 年级 · td[1] 应测 · td[2] **可评价** · td[3] 覆盖率。
    // 这一页只有这一张表带这四列，所以 `.side-panel table tbody tr` 是唯一的。
    const sideRows = page.locator('.side-panel table tbody tr');
    const sampledGrades: string[] = [];
    const sampledCounts: number[] = [];
    for (let i = 0; i < await sideRows.count(); i++) {
      const tds = sideRows.nth(i).locator('td');
      const count = Number((await tds.nth(2).innerText()).trim());
      if (count > 0) {
        sampledGrades.push((await tds.nth(0).innerText()).trim());
        sampledCounts.push(count);
      }
    }

    // 先证明有东西可扫：一个可评价样本都没有时这一块渲染的是空态那一句，下面每一条都会落空
    // ——一条空转的断言在屏幕全白时也是绿的（§测试注意那条的第五次发作，见 `vocabulary.spec.ts`）。
    expect(sampledGrades.length).toBeGreaterThan(0);

    const cells = card.locator('.band-cell');
    await expect(cells).toHaveCount(sampledGrades.length);
    await expect(cells.locator('.band-cell-title')).toHaveText(sampledGrades);

    for (let i = 0; i < sampledGrades.length; i++) {
      const chart = cells.nth(i).locator('.band-chart');
      await expectScoreBandRows(chart);
      expect(await bandFootTotal(chart)).toBe(sampledCounts[i]);
    }
  });

  test('reset returns the report to the newest task', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics/overview');
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    await page.getByRole('button', { name: /重置/ }).click();
    await expect(page.getByText('已重置为最新可分析任务')).toBeVisible();
    await expect(page.locator('.task-option input:checked')).toHaveCount(1);
  });
});

/* ---------- 专业报告（§5.13）的公共动作 ---------- */

/**
 * 走界面保存一份**新草稿**，返回服务端发的报告编号。
 *
 * **点「保存草稿」这一下在这个函数里**，调用方只管把正文填好。它的前四个调用点各自
 * 漏了这一下（写完 `fill` 就直接进来等回执），表现是「等了 20 秒什么都没有」——
 * 看起来像保存坏了，实际是从来没有人按过那一枚按钮。动作与它等的结果放在一处，
 * 下一次加调用点时不会再漏。
 *
 * 编号从回执里读、不写死：它按天编号（`RPT-20260926-0001`，序号补零到 4 位——
 * `insert_with_unique_number` 的 `{base + offset:04d}`），写死会在某一天红在一个与
 * 功能无关的地方。
 *
 * **「继续编辑现有草稿」那一支必须处理**：同一组任务再存一次时页面会先问一句
 * 「已经有一份同样范围的草稿」（`save()` 的 `findSameScopeDraft` 分支，共享演示库上
 * 跑过几次之后它一定会出现），而这一支**不会**发「草稿已保存至服务器」那句回执
 * （`save()` 在 `return true` 处早退）。这条用例要的是一份**新的**报告，所以一律点
 * 「仍然新建一份」——那是 `askConfirm(title, message, confirmText, danger, cancelText)`
 * 的第 5 个参数，也就是 `ConfirmDialog` 的 `.btn-cancel`（`取消` 那一枚的位置，
 * 文案被换成了「仍然新建一份」）。
 */
async function saveNewDraft(page: Page): Promise<string> {
  const saved = page.getByRole('status').filter({ hasText: '草稿已保存至服务器（RPT-' })
  const reuse = page.getByRole('button', { name: '继续编辑现有草稿' })
  await page.getByRole('button', { name: '保存草稿' }).click()
  // `.first()` 是必需的：`.or()` 在两边都匹配时会被严格模式拒绝，而上一轮保存留下的
  // 那句回执确实可能还挂在同一页的状态行上。
  await expect(reuse.or(saved).first()).toBeVisible({ timeout: 20000 })
  if (await reuse.isVisible()) {
    await page.getByRole('button', { name: '仍然新建一份' }).click()
  }
  await expect(saved).toBeVisible({ timeout: 20000 })
  const reportNo = (await saved.innerText()).match(/RPT-[\d-]+/)?.[0]
  expect(reportNo, '服务端回执里必须带着报告编号').toBeTruthy()
  return reportNo as string
}

/**
 * 「我的报告」里那一行。行上的副标题里带着报告编号（`报告编号 · 任务范围`）。
 *
 * **`filter({ hasText })` 是子串匹配，而编号按天递增**：`RPT-20260926-1` 会同时命中
 * `RPT-20260926-12`（共享演示库上跑几次之后必然出现），于是这个定位器撞上严格模式。
 * 负向先行断言把编号收成整串——编号后面不许再接数字。
 */
function reportRow(page: Page, reportNo: string) {
  return page.locator('.report-list .row').filter({ hasText: new RegExp(`${reportNo}(?!\\d)`) })
}

/** 「版本时间线」里那一行。`.ver` 的内容恰好是 `Vn`，用整串匹配避免 V1 命中 V10。 */
function versionRow(page: Page, no: number) {
  return page
    .locator('.versions .row')
    .filter({ has: page.locator('.ver', { hasText: new RegExp(`^V${no}$`) }) })
}

/* ---------- §5.13.8 十条 e2e 的公共常量与动作 ---------- */

/**
 * 两份正文常量。**不含半角逗号**是有意的：`report_document` 用 `csv.writer` 拼 CSV，
 * 含逗号 / 引号 / 换行的字段会被加引号，于是「`整体情况说明,<正文>` 这一串在文件里」
 * 这条子串断言就要先算引号。两份正文还要能互相区分——导出 V1 时断「不含 V2 正文」，
 * 那才是「导出的确实是那一版」的判据（§5.13.8 ③）。
 */
const PUBLISHED_TEXT = 'e2e-V1：从统计事实出发形成的整体情况说明。'
const DRAFT_TEXT = 'e2e-V2：还在编辑、尚未发布的那一版说明。'

/** ① 与 ⑩ 追加在正文末尾的那一句（追加而不是覆盖，所以那两处断的是 `toContain`）。 */
const RECOVER_TEXT = '（e2e 续写）'

/**
 * 这一页自己的**状态行**（`ReportExportPage.vue` 的 `p.hint[role="status"]`）。
 *
 * **必须收在 `p[role="status"]` 上**：同一页的 `FilterBar` 也有一个 `role="status"`
 * （`<span class="hint">{{ stamp }}</span>`，写着「已自动加载最新可分析任务」），
 * `page.getByRole('status')` 会同时命中两个。上面既有的那条用例能直接用
 * `getByRole('status')` 是因为它每次都配了 `filter({ hasText })`。
 *
 * 两个子页签各有一个、且**互斥**（`v-if` / `v-else`），所以这一页上恒为 1 个：
 * 「专业解读」那个说保存与发布的结果，「报表导出」那个说导出的结果。
 */
function pageNotice(page: Page): Locator {
  return page.locator('p[role="status"]')
}

/**
 * 用接口造出「V1 已发布 + V2 草稿（未发布）」这个状态，返回报告编号。
 *
 * **为什么走接口而不是走界面**：这是 §5.13.8 ③（leader 在 V2 草稿期间仍看得到并导出
 * V1、看不到 V2 草稿）与 ④（在 V2 页面上选 V1 导出）的**前提状态**。用界面一步步搭出来
 * 要多点七八次、每次都要等两轮往返；前置动作一长，用例红的时候就分不清坏的是它还是
 * 这条链。接口只负责造态，判据全留在界面上。
 *
 * 可见任务不足时**抛**而不是静默退化（§5.13.8 的逐字要求）：先跑 `make seed-demo`，
 * 不许为了让测试变绿而放宽真实产品断言。
 */

/**
 * 前置接口失败时**把服务端那一句原话带出来**（§2：统一响应封装的 `error.message`
 * 本来就是写给用户看的）。
 *
 * `expect(resp.ok(), '接口创建报告失败').toBeTruthy()` 只说了一句「失败」，而原因在响应体
 * 里。2026-09-26 全量并行下这条前置真失败过一次，报告里只有那六个字——要查出是 409 撞号
 * 用尽还是别的，得再复现一次并发。状态码与响应体一起带出来，下次不必再造。
 *
 * 只在失败分支读 `text()`：成功时调用方紧接着要 `json()`，先读一遍文本不影响它。
 */
async function expectOk(resp: APIResponse, what: string) {
  if (resp.ok()) return
  const body = await resp
    .text()
    .then(text => text.slice(0, 300))
    .catch(() => '(响应体读不出来)')
  throw new Error(`${what}（HTTP ${resp.status()}）：${body}`)
}

async function reportWithDraftOverPublished(page: Page): Promise<string> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { account: '13800000001', password: '123456', role: 'counselor' }
  })
  const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` }

  const tasksResp = await page.request.get('/api/v1/assessment-tasks', { headers })
  await expectOk(tasksResp, '这一条失去了取数的手段：GET /assessment-tasks')
  const tasks = (await tasksResp.json()).data.items as Array<{
    id: number
    completed_targets: number
    start_at: string | null
  }>
  expect(
    tasks.length,
    '共享演示库可见任务不足，请先运行 make seed-demo（§5.13.8）'
  ).toBeGreaterThan(0)

  // 挑哪一个任务，必须与**页面自己会挑的那一个**同源（`FilterBar.defaultTask()`：
  // 先滤掉「已完成目标数不足隐私门槛」的，再按施测开始时间取最新）。理由有两条：
  //  · `create_report` 内部会拿 `task_ids[0]` 去算一次 `analytics_report`——那要求这个
  //    任务**真的可分析**。取 `tasks[0]`（接口的原始次序）时可能撞上演示库里那场
  //    「还没开始」的复测（目标行 0 人完成），于是一条与「版本发布」毫无关系的前置失败
  //    会在这里把用例拦下，而报出来的是「接口创建报告失败」；
  //  · 这一条后面要用界面打开这一份报告，而界面默认选中的正是 `defaultTask()` 那一个。
  //    两者不同的任务集意味着报告的 `task_scope` 与页面上选中的任务对不上，
  //    `onQuery` 那一支会顺手把报告关掉。
  const analyzable = tasks
    .filter(task => task.completed_targets >= 5)
    .sort((a, b) => Date.parse(b.start_at || '') - Date.parse(a.start_at || ''))
  const task = analyzable[0] || tasks[0]

  const createResp = await page.request.post('/api/v1/professional-reports', {
    headers,
    data: {
      // 标题带上时间戳：共享演示库里跑过几次之后会有多份同名报告，撞在一起会让
      // 「按标题找那一行」变成一条与功能无关的断言。
      title: `e2e 版本发布核对 ${Date.now()}`,
      task_ids: [task.id],
      overall_summary: PUBLISHED_TEXT
    }
  })
  await expectOk(createResp, '接口创建报告失败：POST /professional-reports')
  const created = (await createResp.json()).data as { id: number; report_no: string }

  const publishResp = await page.request.post(`/api/v1/professional-reports/${created.id}/publish`, { headers })
  await expectOk(publishResp, '接口发布 V1 失败：POST …/publish')

  const newVersionResp = await page.request.post(`/api/v1/professional-reports/${created.id}/new-version`, { headers })
  await expectOk(newVersionResp, '接口新建 V2 失败：POST …/new-version')

  const draftResp = await page.request.put(`/api/v1/professional-reports/${created.id}/draft`, {
    headers,
    data: { overall_summary: DRAFT_TEXT }
  })
  await expectOk(draftResp, '接口写 V2 草稿失败：PUT …/draft')

  return created.report_no
}

/*
 * `expectNoHorizontalOverflow` / `expectBottomNavInViewport` / `VIEWPORTS` 都住在
 * `helpers.ts`（一处定义）——`vocabulary.spec.ts` 那条「四角色 × 四档视口 × 全部页面」
 * 要共用它们，而这个文件里写第二遍就是两份会各自漂移的表。
 */

/**
 * 一直按 `Tab` 直到焦点落到某个元素上（§5.13.8 ⑩）。
 *
 * **不数按了几次 Tab。** 整页的 Tab 次序会随任何一个组件的增删而变（这一页上有筛选栏、
 * 报告列表、四段 textarea、版本时间线、若干按钮），写死次数会让这条用例在某次无关的
 * 排版改动之后红在一个与键盘可达性无关的地方。`limit` 只是「跑满就抛」的上限——抛出来
 * 说明那个元素**根本不在 Tab 次序里**，那正是这条用例要说的事。
 *
 * 判据用 `tagName` + `innerText` 而不是选择器：这里问的是「键盘此刻站在哪」，而
 * `document.activeElement` 是唯一回答它的东西。
 */
async function tabUntil(
  page: Page,
  match: (el: { tag: string; text: string; label: string }) => boolean,
  label: string,
  limit = TAB_LIMIT
): Promise<{ tag: string; text: string; label: string }> {
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press('Tab')
    const focused = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null
      return {
        tag: el?.tagName ?? '',
        text: (el?.innerText ?? '').replace(/\s+/g, ' ').trim().slice(0, 200),
        // 表单控件的名字来自包着它的 `<label>`（`FormDialog` 就是这种形状）。少了这一项，
        // 两个 `<select>` 在 `text` 上长得一样（`innerText` 只给出**选中的那一项**），
        // 于是「Tab 到了导出用途，还是导出哪一版」这个问题答不出来。非表单控件没有
        // `labels`，回空串。
        label: Array.from((el as HTMLInputElement | null)?.labels ?? [])
          .map(node => (node.innerText ?? '').replace(/\s+/g, ' ').trim())
          .join(' ')
          .slice(0, 120)
      }
    })
    if (match(focused)) return focused
  }
  throw new Error(`按了 ${limit} 次 Tab 仍没有到达「${label}」`)
}

/**
 * `tabUntil` 一次最多按多少次 Tab。
 *
 * 定义在 `tabUntil` **之后**不影响它：默认参数在**调用时**求值，那时模块早已求值完毕。
 * 两处共用同一个数，免得下一次调上限时只改一处。
 */
const TAB_LIMIT = 160;

/**
 * 焦点那一行是不是「我的报告」列表里的一行？是就回它的文本，否则回空串。
 *
 * 判据是**结构**（`ul > li > button`）而不是文案或类名：报告页上只有这一个列表长这样，
 * 而按文字判（比如「含 `RPT-`」）会让这条用例在报告编号改前缀时红在一个与键盘可达性
 * 毫无关系的地方；按 `.rows` 那种类名判则会在别处复用同名类时变成假阳性。
 */
async function readFocusedReportRow(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null
    if (!el || el.tagName !== 'BUTTON') return ''
    const li = el.closest('li')
    if (!li || li.parentElement?.tagName !== 'UL') return ''
    return (el.innerText ?? '').replace(/\s+/g, ' ').trim().slice(0, 200)
  })
}

/**
 * 用 Tab 进入「我的报告」列表，返回焦点落在的那一行。
 *
 * **这一份列表现在只占一个 Tab 停靠点**（`composables/useRovingFocus.ts`）。实测共享
 * 演示库上有 236 份报告，此前每一行各是一个停靠点——`tabUntil` 那时一路 Tab 过去总能
 * 扫到目标行，代价是键盘用户要走两百多次 Tab（那正是这一项可达性缺陷）。roving tabindex
 * 之后一次只能进入**一行**，换行必须用上下方向键，所以「走到目标行」那一步归
 * `arrowToReportRow`：两条合起来才是键盘用户此刻真实的路径。
 */
async function tabIntoReportList(page: Page): Promise<string> {
  for (let i = 0; i < TAB_LIMIT; i++) {
    await page.keyboard.press('Tab')
    const text = await readFocusedReportRow(page)
    if (text) return text
  }
  throw new Error(`按了 ${TAB_LIMIT} 次 Tab 仍没有进入「我的报告」列表`)
}

/**
 * 在列表内用 ↓ 走到目标那一行。
 *
 * 上限按**列表长度**给（不是 `TAB_LIMIT`）：焦点只会往后走，而目标行可能排在很后面。
 * 走到就停，典型情况只按几次——**但不能假设它排第一行**：目标那一份是刚建的，而同一秒内
 * 建的多份报告 `updated_at` **并列**（`now_utc_naive()` 截断到整秒，CLAUDE.md §20），
 * 并列时 MySQL 不保证按更新时刻排。2026-09-26 实测过：列表头两行是 0224 / 0225，
 * 而这一条要的是编号更大的那一份——修之前每行都是停靠点，一路 Tab 扫得到；
 * 换成 roving tabindex 之后就永远停在第 0 行上了。
 *
 * **列表 2026-09-27 起分页了（每页 20 行）**，而 ↓ 在末行会翻到下一页（`ProfessionalReportList`
 * 的 `onRowKeydownPaged`），焦点跟到新页的首行——所以这一路走下去仍然到得了任何一行，
 * 只是第 20 行之后每按一次要先翻页。上面那个「典型情况只按几次」的结论一个字没变：
 * 目标那一份是**刚建的**，服务端按 `updated_at` 降序，它必然排在**第 1 页**里。
 *
 * 上限因此不是「列表有多长」，而是一道**防死循环的闸**：走到就停，正常情况下永远碰不到它。
 * 给到 600 是因为演示库此刻已有四百多份（跑一次 e2e 就多几份），而真要够到第 300 行，
 * 说明目标不再排在首页了——那时该改的是这条用例的选取方式，不是把这个数继续调大。
 */
async function arrowToReportRow(page: Page, reportNo: string, limit = 600): Promise<void> {
  for (let i = 0; i < limit; i++) {
    const text = await readFocusedReportRow(page)
    if (text.includes(reportNo)) return
    await page.keyboard.press('ArrowDown')
  }
  throw new Error(`在「我的报告」列表里按了 ${limit} 次 ↓ 仍没有到达 ${reportNo}`)
}

/**
 * 等弹层的**打开动画**结束再量尺寸。
 *
 * `Modal.vue` 的 `<Transition name="modal">` 在打开那一刻给 `.modal-backdrop` 挂上
 * `modal-enter-from`，而 `styles.css` 有一条 `.modal-enter-from .modal-panel {
 * transform: scale(0.95) translateY(10px) }`。`getBoundingClientRect()` 量到的是
 * **变换之后**的盒子，所以在那一帧里：
 *
 *   - 940×612 的面板 → 893×581（`0.95` 倍）
 *   - 被夹在 `max-height: 340px` 的表格 → **323**（`0.95` 倍）
 *
 * 而 `getComputedStyle(wrap).maxHeight` 一如既往地回 `340px`——样式值不受变换影响。
 * 2026-09-26 实测撞到过：`toBe(340)` 红在 `323` 上，看起来像「CSS 被改了」或
 * 「内容不够高」，实际两样都不是，`.modal-body` 的 `clientHeight` 是 474（真实的
 * 布局值，`clientHeight` 不随 transform 变），内容 `scrollHeight` 是 1900。
 *
 * **判据是 `transform` 回到 `none`**（动画结束后面板上没有任何变换），不写死等待
 * 时长：`toHaveCSS` 自己轮询，无论那一段是 0.25 秒还是只有一帧都能等到。
 * 凡是**在弹层刚打开时读几何量**的用例都要先走这一句——写死一个 `waitForTimeout`
 * 会在慢机器上重新变红，而红的原因与用例要证明的事无关。
 */
async function waitForModalSettled(panel: Locator) {
  await expect(panel).toHaveCSS('transform', 'none')
}

test.describe('Analytics report functions', () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, 'counselor');
  });

  test('overview queries selected tasks and renders aggregate results', async ({ page }) => {
    await page.goto('/counselor/analytics/overview');
    // 标题的唯一出处是路由的 `meta.title`（`ReportPageHeader.vue` 读它），而这一页由
    // **两个角色共用同一个组件**——两个角色各写一条 `meta.title`，从前德育领导那条带
    // 「全校」前缀，于是同一套组件里有两处硬编码标题。
    //
    // V2.0.0 §5.4 的建议 + §9 P1-04 把两条统一成「筛查关注概览」，**范围改由页头那枚
    // 「当前数据范围」徽标表达**——徽标对德育领导读出来就是「全校」。所以这一条断的是
    // （现在两条路由一样的）那一个标题，而「这个屏幕上的数字描述的是谁的范围」这件事
    // 由 `ReportPageHeader` 的徽标断（`describe('数据范围徽标')` 那一组）。
    //
    // 别再改回「全校…」那一版：它会让同一屏上出现两个范围口径的自相矛盾版本
    // ——标题说全校、徽标也说全校（对领导是对的），可同一条路由给心理老师走的时候
    // 标题仍然说全校，而数据其实是 scoped 的。
    await expect(page.getByRole('heading', { name: '筛查关注概览' })).toBeVisible();
    // V2.0.1 §5.20 起这句收在 `.kpis` 里定位。**不是把断言改松，是把它钉到它本来要说的地方**：
    // 这条用例要证明的是「报表默认加载并渲染出 KPI」，所以它该断的是**那张卡**。
    // 此前靠 `exact: true` 收严就够了，因为全页只有一个「实际应测人数」；§5.20 给这一页
    // 补了两句分母口径（工作进展表的说明句、统计解释边界的第 ⑦ 条），两处都把这个词
    // 包在 `<strong>` 里——它们**必须**写全这个数，因为它是 `eligible_count` 的正式中文名
    // （`labels.ts` 的口径表），少一个字就读成另一个数（「实际应测」= `eligible_count`，
    // 与「实际应测人数」在别的句子里正是两个口径的名字）。
    // 所以收窄定位器、**不改页面措辞**——上一版注释里那句「脚注那句口径不许为了迁就
    // 定位器改措辞」在这里同样成立。
    await expect(page.locator('.kpis').getByText('实际应测人数', { exact: true })).toBeVisible();
    await page.locator('.task-picker summary').click();
    await page.locator('.task-option input:not(:checked)').first().check();
    await page.getByRole('button', { name: /查询/ }).click();
    await expect(page.locator('.task-picker')).not.toHaveAttribute('open', '');
    await expect(page.locator('.task-picker summary')).toHaveText('已选择 2 个任务');
    await expect(page.getByRole('heading', { name: '测评完成情况' })).toBeVisible();
  });

  test('dimensions switches validity basis and all three result views', async ({ page }) => {
    await page.goto('/counselor/analytics/dimensions');
    // `exact: true` 保留着，但它的**理由在 V2.0.0 §5.4 之后变了**：两个角色的路由标题
    // 现在同名（「八维度分析」），所以收严不再是为了分出两个角色——页内还有别的地方
    // 会出现这四个字（导航项、`ReportPageHeader` 的说明）。写死那一段旧理由会误导
    // 下一个人去「恢复」德育领导那条带「全校」前缀的标题。
    await expect(page.getByRole('heading', { name: '八维度分析', exact: true })).toBeVisible();
    await page.getByLabel('分析口径').selectOption('VALIDITY_UNFLAGGED');
    await page.getByRole('button', { name: /查询/ }).click();
    await expect(page.getByText('各维度高分人次')).toBeVisible();
    await page.getByRole('button', { name: '平均得分' }).click();
    await expect(page.getByText('不同维度满分不同')).toBeVisible();
    await page.getByRole('button', { name: '得分分布' }).click();
    await expect(page.locator('.dist-row')).toHaveCount(8);
  });

  // 用户 2026-09-24 报的：「全校八维度分析 → 效度建议复测 数字是对的，但全校预警总览 →
  // 效度复测建议 是错误的」。那一格此前读 `signal_type_stats` 里的 `RETEST_RECOMMENDED`
  // 分组，而**没有任何一条代码路径会写出这个 signal_type**——引擎只写
  // `SCREENING_SIGNAL` / `MANUAL_REVIEW_REQUIRED`（都来自重点题 85 / 97），所以按那个分组
  // 数出来恒为 0，而屏幕上它长得像一个正常的统计结果。
  //
  // 两页现在读同一份 `sample_quality.validity_flagged_count`，所以它们构造上不可能各说
  // 各话。这条用例断的就是那句话的可执行形式：**同一场任务、两个页面上同一个数**。
  //
  // 它**不需要在任务选择器里手动挑任务**：两页共用一个 `FilterBar`，`defaultTask()` 按
  // `completed_targets >= 5` 过滤 + `start_at` 降序挑出同一场（实测是「2026秋季MHT心理健康
  // 筛查」，`validity_flagged_count = 2`），所以默认加载的那一帧就已经有东西可扫。
  test('both report pages agree on the validity retest figure', async ({ page }) => {
    await page.goto('/counselor/analytics/dimensions');
    await expect(page.getByRole('heading', { name: '八维度分析', exact: true })).toBeVisible();
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    // 标签在两页上**语序不同**（这一页写「效度建议复测」，总览页写「效度复测建议」），
    // 所以两处各按自己的原文取。这不是笔误：`kpiCard` 用的是 `^…$` 全等正则，把其中一处
    // 改成另一处的语序会让它定位不到——而那正是「两个数说的是不是同一件事」要能看见的东西。
    const dimensions = await numberIn(kpiCard(page, '效度建议复测').locator('.kpi-value'));

    await page.goto('/counselor/analytics/overview');
    await expect(page.getByRole('heading', { name: '筛查关注概览' })).toBeVisible();
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    // 总览页那一格不在 `KpiCard` 里，在 `MetricStrip` 的 `.metric-mini` 里（值在 `<b>` 上）。
    const overview = await numberIn(
      page.locator('.metric-mini', {
        has: page.locator('.metric-label', { hasText: /^效度复测建议$/ })
      }).locator('b')
    );

    // **先证明有东西可扫，再断言相等**：少了这一句，一个把两页都渲染成 0 的实现照样满足
    // 下面那条相等（`0 === 0`），而屏幕上就是用户报的那个 bug。这一句是那条判断的可执行形式
    // ——它同时钉住「演示库的那场任务里真的有被标记的学生」这个前提（`seed_demo` 的
    // `key_both` 那一档带 8 道效度题，越过阈值 7）。
    expect(dimensions).toBeGreaterThan(0);
    expect(overview).toBe(dimensions);
  });

  // V2.0.1 §5.20.4（AUDIT-C）唯一能被证伪的那一面：**被抑制的维度不得印成 `0%`**。
  //
  // 为什么这条只能靠桩：抑制规则是「分母 < `MIN_COHORT_FOR_AGGREGATE`（=5）时不发这个
  // 数」（CLAUDE.md §11），而演示库那场任务每个维度的 `n_evaluable` 都是 48，
  // 八条 `suppression` 全是 `{suppressed: false}`——**真实的演示数据上这条分支不可达**。
  // 而 §5.20 改的正是这条不可达分支（`DimensionsPage` 的两个 `|| 0` 摘掉、
  // `HorizontalBars` 把 `null` 渲染成「样本不足」）。按本仓库自己的判据，一处没有任何
  // 东西看得见的修复等于没有——所以这里造一个**只有服务端抑制时才出现**的载荷。
  //
  // 载荷以**真响应**为底、只改 `high_score_rate` / `mean_score` 两列（照
  // `raceReportListReload` 那个脚手架的手法）：手搓一份完整 `AnalyticsReport` 会在
  // 类型加字段时静默漂移，而这条用例要钉的不是那份结构。
  //
  // 判据分三层，而**这三层各自都能单独变红**（变异验证实测，见下）：
  // ① **先证明有东西可扫**：`12.5%` 真的印出来了（图渲染了、条画了）；
  // ② 七个被抑制的维度各印一次「样本不足」（8 维度 − 1 个有值）；
  // ③ 整张 svg 里 `0%` 恰好一次——那是**坐标轴原点刻度**（`HorizontalBars` 的
  //    `[0,.25,.5,.75,1]` 五根刻度，无论上限取多少，第一根恒为 `0`）。
  //
  // **②与③不是重复**：② 说的是「被抑制的那七格没印数」，③ 说的是「图上确实还有一根
  // 坐标轴」——③ 挡的是「把值标签和轴一起删掉」那种更彻底的坏法（那样 ② 依然是绿的）。
  // 变异验证三发，各自红的正是它该红的那一行：
  //   M1 把 `DimensionsPage` 的 `highRates` / `means` 打回 `|| 0` → 红在 ②（收到 0）；
  //   M2 把 `HorizontalBars` 的 `样本不足` 分支摘掉 → 红在 ②（收到 0）；
  //   M3 把 `HorizontalBars` 的坐标轴刻度文字删掉 → **②仍绿**、红在 ③（收到 0）。
  // 所以 `toBe(7)` 与 `toBe(1)` 都不是随手写的数：前者钉住那七根，后者钉住那条轴。
  test('suppressed dimensions render 样本不足 instead of 0%', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/v1/analytics/report',
      async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as {
          data: { dimensions: Array<{ high_score_rate: number | null; mean_score: number | null }> } | null;
        };
        const dims = body.data?.dimensions;
        if (Array.isArray(dims) && dims.length > 1) {
          dims[0].high_score_rate = 12.5;
          dims[0].mean_score = 7.25;
          for (let i = 1; i < dims.length; i += 1) {
            dims[i].high_score_rate = null;
            dims[i].mean_score = null;
          }
        }
        await route.fulfill({ response, json: body });
      }
    );

    await page.goto('/counselor/analytics/dimensions');
    await expect(page.getByRole('heading', { name: '八维度分析', exact: true })).toBeVisible();

    // ① 有值的那一根条（默认子页签就是「高分比例」）。
    await expect(page.locator('.chart svg text').filter({ hasText: '12.5%' })).toHaveCount(1);
    // ② 被抑制的七根。
    await expect(page.locator('.chart svg text').filter({ hasText: '样本不足' })).toHaveCount(7);
    // ③ 只有坐标轴原点那一个 `0%`（见上面那段推导）。
    await expect(page.locator('.chart svg text').filter({ hasText: /^0%$/ })).toHaveCount(1);

    // 同一批 `null` 在「平均得分」那一页签上走的是同一条路（`|| 0` 当初也是两处各一个），
    // 所以两个子页签都断一次。`0分` 在轴上是**不存在**的（轴印的是裸 `0`），
    // 所以回归时它会出现七次，而正常时一次都没有。
    await page.getByRole('button', { name: '平均得分' }).click();
    await expect(page.locator('.chart svg text').filter({ hasText: '7.25分' })).toHaveCount(1);
    await expect(page.locator('.chart svg text').filter({ hasText: '样本不足' })).toHaveCount(7);
    await expect(page.locator('.chart svg text').filter({ hasText: /^0分$/ })).toHaveCount(0);

    // **同一批 `null` 在这张表里印的一直是 `—`**（本节之前就是这样，AUDIT-C 的既有证据，
    // 只记录、不改）：第 2 行那两个数值列各一个。数值列留 `—` 而不是留空，是
    // CLAUDE.md §3 那条既有约定的另一面——`—` 是界面占位符，写进 CSV 会让整列被当成文本，
    // 而留空在屏幕上与「这一格没加载出来」分不开。
    await expect(page.locator('tbody tr').nth(1).getByText('—', { exact: true })).toHaveCount(2);
  });

  test('grade report changes dimension and renders the matching chart', async ({ page }) => {
    await page.goto('/counselor/analytics/grades');
    const card = page.locator('.chart-card');
    await card.getByLabel('比较维度').selectOption({ label: '冲动倾向' });
    await expect(card.getByRole('heading', { name: '冲动倾向 · 各年级高分比例' })).toBeVisible();
    await expect(card.getByRole('img', { name: '柱形统计图' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '年级样本与覆盖率' })).toBeVisible();
  });

  test('class report cascades grade and class then refreshes the selected cohort', async ({ page }) => {
    await page.goto('/counselor/analytics/classes');
    await expect(page.getByRole('heading', { name: '班级维度画像' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '班级样本质量' })).toBeVisible();
    // 定位器收在筛选栏里，不按整页找「年级」这两个字：任务选择器里每一个复选框的
    // 无障碍名字都来自它那一行的任务名，而演示数据里有一个任务叫**「初三年级复测任务」**
    // ——`getByLabel('年级')` 于是同时命中那个复选框与这个下拉框，报的是 strict mode
    // violation，红在一个与「年级联动班级」毫无关系的地方。这不是新功能的问题，
    // 是定位器依赖了名册/任务名里恰好有什么字（§测试注意那条「数据依赖的假设」）。
    //
    // 所以这里按**角色**找那个下拉框，名字用 `^` 锚在开头：`<select>` 的无障碍名字是
    // 「年级 全部年级初一初三初二」（它把 option 的文字也算进去），只有它是以「年级」
    // 开头的 combobox，而那个复选框的 role 是 checkbox。
    const filters = page.locator('.filters');
    await filters.getByRole('combobox', { name: /^年级/ }).selectOption({ label: '初二' });
    const classSelect = filters.getByRole('combobox', { name: /^班级/ });
    await expect(classSelect.locator('option')).toHaveText(['全部班级', '1班', '2班', '3班']);
    await classSelect.selectOption({ label: '3班' });
    await page.getByLabel('统计指标').selectOption('average');
    await page.getByRole('button', { name: /查询/ }).click();
    // 「当前是哪个班」这句话落在「任务目标」那张卡的副标题上（`activeClassName` 一处拼的）。
    // **不写成 `getByText('初二（3班）')`**：下面那张分布图的小标题写作「本班 · 初二（3班）」，
    // 而 `getByText` 按子串匹配、两个都命中——报出来的是 strict mode violation，
    // 读起来像页面画重了，而这一页是对的。
    await expect(kpiCard(page, '任务目标').locator('.hint')).toHaveText('初二（3班）');
    await expect(page.getByRole('heading', { name: '班级样本质量' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '维度对比数据' })).toBeVisible();
  });

  /**
   * 班级页上是**一张卡里两张图**（本班 / 同年级）——报表里第一次出现这种对照，
   * 而它有两个可能各说各话的地方，一条钉一个：
   *
   * ①左边那张的名字与上面「任务目标」卡的副标题是同一串（`activeClassName` 一处拼的）；
   * 两处各拼一次就会出现「卡上写着初二（3班）、图上写着初二（1班）」而两边都像对的。
   * ②右边那张是**所属**年级的全年级，不是任取一个年级——所以判据是「它的标题带着左边
   * 那个班的年级名」，而那个年级名从页面上读回来，不写死「初二」。
   *
   * 末尾那条人数对账把「本班那一份」与 `.mini-table` 里同一列钉在一起：两个数走的是
   * 两条渲染路径（一个经组件、一个直接插值），而它们必须来自同一列 `sample_count`。
   * 这一组有 `beforeEach` 登录，所以这里不再 `loginAs`。
   */
  test('class portrait draws the class beside its own grade, from the same cohort numbers', async ({ page }) => {
    await page.goto('/counselor/analytics/classes');
    await expect(page.getByRole('heading', { name: '班级维度画像' })).toBeVisible();

    // 年级/班级联动照抄上面那条用例（含 `/^年级/` 那个锚，理由见那一处的注释）。
    const filters = page.locator('.filters');
    await filters.getByRole('combobox', { name: /^年级/ }).selectOption({ label: '初二' });
    await filters.getByRole('combobox', { name: /^班级/ }).selectOption({ label: '3班' });
    await page.getByRole('button', { name: /查询/ }).click();

    const card = page.locator('.band-card');
    await expect(card.getByRole('heading', { name: '关注等级分布' })).toBeVisible();

    // 这一页的对照关系恰好一对：左边本班（`.band-cell` 第一格）、右边同年级（第二格）。
    // 取不到年级那一组时右边换的是一句说明（`.band-cell-empty`），那时下面第一条会先红。
    const cells = card.locator('.band-cell');
    await expect(cells).toHaveCount(2);

    // 页面当前是哪个班——这一串**从页面上读回来**，下面两张图的标题都拿它拼期望值。
    // 但紧接着那一句 `toBe` 是必须的：联动一旦静默失效（比如下拉框换了名字、
    // `selectOption` 落到别处），读回来的是「初一（1班）」，而两张图的标题也会**一起**
    // 变成初一（1班）——两边同源，于是「它们是不是同一个班」这条照样绿。
    // 所以这里先钉住「联动真的选中了 3班」，下面那两条才有意义（§测试注意：
    // 先证明有东西可扫，再断言它干净）。这一句与上面那条用例的判据是同一个值。
    const className = (await kpiCard(page, '任务目标').locator('.hint').innerText()).trim();
    expect(className).toBe('初二（3班）');
    const gradeName = className.split('（')[0];

    await expect(cells.nth(0).locator('.band-cell-title')).toHaveText(`本班 · ${className}`);
    await expect(cells.nth(1).locator('.band-cell-title')).toHaveText(`同年级 · ${gradeName}全年级`);

    const classChart = cells.nth(0).locator('.band-chart');
    const gradeChart = cells.nth(1).locator('.band-chart');
    // §5.17 起这两格都是**横向版**（与筛查关注概览同一个组件、同一套档序与状态色）。
    // 两格都逐条过一遍，其中第 4 条（条长 ÷ 轨道 ≈ 人数 ÷ 可评价样本）正是**跨格可比**
    // 那一条：两格各自拿自己的 `sample_count` 当分母，所以「本班 50% 比年级 46% 长」
    // 与右边两个百分比是同一件事——按最大值归一化时比出来是反的（见 helper 的注释）。
    await expectScoreBandRows(classChart);
    await expectScoreBandRows(gradeChart);

    const classTotal = await bandFootTotal(classChart);
    const gradeTotal = await bandFootTotal(gradeChart);
    // 一个班是它所属年级的子集，所以右边那份不可能比左边小。**不写严格大于**：
    // 只有一个班的年级上两边天然相等，那不是故障。反过来说，右边比左边小就一定拿错了组。
    expect(gradeTotal).toBeGreaterThanOrEqual(classTotal);

    // 本班那张的人数与「班级样本质量」里「可评价样本」那一行同源（同一列 `sample_count`）。
    const tableCount = Number((await page.locator('.mini-table tr', { hasText: '可评价样本' }).locator('td').innerText()).trim());
    expect(classTotal).toBe(tableCount);
  });

  /**
   * §5.17 的那条产品目标本身：「**这是同一个业务指标，只是统计范围不同**」。
   *
   * 上面两条用例各自验的是「这一页自己是自洽的」——`expectScoreBandRows` 断档序、断
   * 人数与比例同排、断条长与比例同源，一条都不跨页。而 §5.17.1 要的结果恰好是**跨页**的：
   * 读者在两页上看到的必须是同一个指标的两种统计范围，而不是两种统计。所以这一条把两页
   * 各打开一次、把同一批事实读回来**互相比**。
   *
   * **互相比而不是各自断一个期望值**：各断一个值只能证明「两页此刻分别长这样」，
   * 证明不了「它们一致」——两页各写死一套颜色时，两边都能过。这与 §32 那条
   * 「两个页面相互比」是同一个写法（那一次是效度复测的人数）。
   *
   * 两条刻意不断：
   *  · **不断两页的条长相等**——两页的分母本来就不同（可评价样本 48 人对 6 人），
   *    条长按占比画，跨页相等既不可能也没意义。可比性由 helper 第 4 条在**页内**保证。
   *  · **不断班级页的 `.band-scope-name`**——§5.17.4 允许的差异：班级上下文留在
   *    `h3.band-cell-title` 里（「本班 · 初二（3班）」），所以那两格**不传** `scope-label`，
   *    只出「可评价 N 人」半句。断它反了就是把允许的差异当成违规。
   */
  test('both report pages show the same level distribution, only the scope differs', async ({ page }) => {
    /**
     * 把「每一档的三件可读事实」读一遍：档名、人数、比例文本，外加条的背景色
     * （0 人的那一档没有条，那时颜色是 `null`）。
     *
     * **必须在还停在这一页上的时候读。** Playwright 的 locator 是**惰性**的——
     * `page.locator('.band-chart')` 描述的是「**当前这一页**上的所有 `.band-chart`」，
     * 而不是「我写下这行时所指向的那一个」。第一版把两处 `facts()` 都写在第二次
     * `goto` 之后，于是「概览那一份」读到的是**班级页的 DOM**（那一页有两个格子、
     * 6 行），红在 `toEqual` 上显示成「6 个档名对 3 个」，而两页看起来都对。
     */
    async function facts(chart: Locator) {
      const rows = chart.locator('.band-row');
      const out: Array<{ label: string; count: number; rate: string; color: string | null }> = [];
      for (let i = 0; i < await rows.count(); i++) {
        const row = rows.nth(i);
        const bar = row.locator('.band-bar');
        out.push({
          label: (await row.locator('.band-label').innerText()).trim(),
          count: Number((await row.locator('.band-count strong').innerText()).trim()),
          rate: (await row.locator('.band-rate').innerText()).trim(),
          color: await bar.count() ? await bar.evaluate((node) => getComputedStyle(node).backgroundColor) : null
        });
      }
      return out;
    }

    /**
     * 人数与比例两格**都在这一行里看得见**（§5.17.3 第 3、4 条：两个数都要能直接读到，
     * 不依赖悬浮层）。`expectScoreBandRows` 里的 `toHaveText` 对不可见的元素也成立，
     * 所以「读得到」这件事只有 `toBeVisible` 能回答。
     *
     * 同 `facts()`：**必须在还停在这一页上的时候调**，理由见上。
     */
    async function expectBandValuesVisible(chart: Locator) {
      const rows = chart.locator('.band-row');
      await expect(rows).toHaveCount(3);
      for (const i of [0, 1, 2]) {
        await expect(rows.nth(i).locator('.band-count')).toBeVisible();
        await expect(rows.nth(i).locator('.band-rate')).toBeVisible();
      }
    }

    // ---- 第一页：筛查关注概览 ----
    await page.goto('/counselor/analytics/overview');
    await expect(page.getByRole('heading', { name: '关注等级分布' })).toBeVisible();
    const overviewChart = page.locator('.band-chart');
    await expectScoreBandRows(overviewChart);
    await expectBandValuesVisible(overviewChart);
    // 范围名这一侧是必须的：这一页没有别的上下文说明「这 48 人是谁」，而 §9 那条
    // 「口径要写进界面」在这里就是这一行字。
    await expect(overviewChart.locator('.band-scope-name')).toHaveText('当前数据范围');
    const overviewFacts = await facts(overviewChart);

    // ---- 第二页：班级维度画像（本班那一格）----
    await page.goto('/counselor/analytics/classes');
    const card = page.locator('.band-card');
    await expect(card.getByRole('heading', { name: '关注等级分布' })).toBeVisible();
    const classChart = card.locator('.band-cell').nth(0).locator('.band-chart');
    await expectScoreBandRows(classChart);
    await expectBandValuesVisible(classChart);
    const classFacts = await facts(classChart);

    // ① 档名与**次序**逐条相同（§5.17.3 第 1 条）。三个中文都从 DOM 读回来再互相比，
    //    不写死——写死的话这一条只证明「两页都渲染了这三个词」，证明不了「是同一个次序」，
    //    而次序正是这一条要钉的东西（横向版按 `LEVEL_ORDER` 从重到轻）。
    expect(classFacts.map(row => row.label)).toEqual(overviewFacts.map(row => row.label));
    expect(overviewFacts.map(row => row.label)).toEqual(['重点关注', '需要关注', '一般观察']);

    // ② 同一档的状态色相同（§5.17.3 第 2 条：用项目现有那一套，不新建第二套 Token）。
    //    0 人的档两边都可能没有条，所以只在**两边都有条**的档上比；并且先证明真的比过
    //    至少一档——一条一档都没进的循环在两边全是 0 时也是绿的（§测试注意：先证明有
    //    东西可扫，再断言它干净）。
    let comparedColors = 0;
    for (const i of [0, 1, 2]) {
      if (overviewFacts[i].color == null || classFacts[i].color == null) continue;
      expect(classFacts[i].color).toBe(overviewFacts[i].color);
      comparedColors++;
    }
    expect(comparedColors).toBeGreaterThan(0);

    // ③ 两个数在**两页上都直接可见**，不靠悬浮层——已由上面两处
    //    `expectBandValuesVisible(...)` 在各自那一页上断过（**不能挪到这里**：那两处的
    //    定位器是惰性的，攒到最后再断会读到班级页的 DOM）。

    // 比例那一格是**两种东西之一**（§11）：分母够大时是百分比，不够大时是「样本过小」。
    // 这里把两页读回来的文本都在同一条正则上验一遍——两页的判据必须是同一句。
    for (const row of [...overviewFacts, ...classFacts]) {
      expect(row.rate).toMatch(/^(\d+(\.\d+)?%|样本过小)$/);
    }
  });

  /**
   * RPT-09 在 V2.0.0 重写过，所以这一条也重写了。三件事是这次重写的理由，各断一次：
   *
   *  · **草稿落在服务端**（§5.4）：状态行里带着服务端发的报告编号 `RPT-…`。此前
   *    它存在浏览器 `localStorage` 里、写的是一句「已保存（浏览器本地草稿）」——
   *    换一台电脑就没了，而屏幕上说它保存了。
   *  · **导出走导出作业**（§5.5）：不再由浏览器拼一个 Blob 直接下载，而是
   *    `POST /professional-reports/{id}/export-jobs` 建一个作业、再由导出中心下载。
   *    所以这里断的是**服务端那句回执**，不是 `download` 事件。
   *  · **已发布不可覆盖**（§5.4）：发布之后 `save_draft` 回 `REPORT_IMMUTABLE`（409），
   *    界面把服务端那句话放到状态行上。
   *
   * 数据残留：这条用例会往库里留下一行 `professional_report`（＋一个版本行）与一个
   * `export_job`。报告**没有删除接口**（与 `export_job` 一样：它是工作事实，只有撤销），
   * 所以它靠 `make purge-demo` 清——那条链路的清除口径见 PROGRESS 的缺口。
   * 不断言行数：报告编号按天编号，写死会在某一天红在一个与功能无关的地方。
   */
  test('report saves interpretation on the server and exports through the export center', async ({ page }) => {
    await page.goto('/counselor/analytics/report');

    // ① 标题的唯一出处是路由的 `meta.title`（`ReportPageHeader.vue` 读它），而不是
    //    视图里写的那一句话——同一套组件给两个角色用，标题写死在视图里就会分岔。
    await expect(page.getByRole('heading', { name: '专业分析报告' })).toBeVisible();

    // ② 这一页进来就自动选中最新的可分析任务并查询（`FilterBar` 的 `loadTasks()`），
    //    所以主体直接出来——断这一句就是在证明「上面那条标题下面确实有东西」，
    //    而不是一个连任务都没选中的空壳。
    //
    //    **没有断那条空态文案**：它只有库里一个任务都没有时才可达（`onQuery` 拿不到
    //    taskIds 就不发请求），而演示库上永远有任务——为它写断言就是一条永远跑不到的
    //    用例。那句话本身 2026-09-25 已经改对了（「请选择测评任务后点击查询」→
    //    「本学年还没有可用的测评任务」，查询按钮同时置灰），五个落点见 PROGRESS；
    //    这里只留一句说明，不摆一条恒绿的断言。
    await expect(
      page.getByRole('status').filter({ hasText: '已自动加载最新可分析任务' })
    ).toBeVisible();

    // ③ 主体出来：两个子页签 + 四段专业解读的输入框。
    await expect(page.getByRole('button', { name: '报表导出' })).toBeVisible();
    await expect(page.getByLabel('1. 整体情况说明')).toBeVisible();
    await expect(page.getByLabel('4. 后续教育支持计划')).toBeVisible();

    // ④ 保存：状态行里是**服务端发的编号**，不是「浏览器本地草稿」。
    //
    //    走 `saveNewDraft` 而不是内联那三行（2026-09-26）：演示库里积了几份同范围的
    //    草稿之后，这一页会先弹一句「已经有一份同样范围的草稿」，而 `save()` 在那个
    //    分支里**不发**「草稿已保存至服务器」那句回执。内联写法于是红在「5 秒等不到
    //    状态行」上，看起来像保存坏了——实际是从来没有人回答过那个问题。
    //    这个动作（点保存 / 回答那一问 / 等带编号的回执）已经收在 `saveNewDraft` 里
    //    （它自己的 docstring 记着同一件事），这里改为复用它。
    await page.getByLabel('1. 整体情况说明').fill('基于当前任务实际统计结果形成的专业说明。');
    await saveNewDraft(page);

    // ⑤ 导出：用途与版本走的是 `FormDialog` 弹层（§5.13 Phase B 把这两项从
    //    `window.prompt` 换成了两格带必填校验的控件），所以先开弹层再填。
    await page.getByRole('button', { name: '进入导出设置' }).click();
    await page.getByRole('button', { name: '生成报告' }).click();

    // 版本那一格默认停在**最新已发布的那一版**（此刻还没发布，于是回落到当前版本 1）。
    await expect(page.getByLabel(/导出哪一版/)).toHaveValue('1');

    // 用途留空点提交，会被必填校验挡在弹层里——**证明这一道门在**，而不是先点一次
    // 成功的路径再断言回执（那样摘掉 `required` 也不会有任何东西红）。
    await page.getByLabel(/导出用途/).selectOption('');
    await page.getByRole('button', { name: '生成并下载' }).click();
    await expect(page.getByText('导出用途不能为空')).toBeVisible();

    await page.getByLabel(/导出用途/).selectOption({ index: 1 });
    await page.getByRole('button', { name: '生成并下载' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: '已下载，可在导出中心查看（EXPORT-' })
    ).toBeVisible();
    // 那一行只在导出成功之后才渲染（`lastExport`），所以先证明它在。
    await expect(page.locator('.audit-section tbody tr')).toHaveCount(1);

    // ⑥ 发布：当前版本被锁定。先回「专业解读」那一页签。
    await page.getByRole('button', { name: '专业解读' }).click();
    // `exact: true` 是必须的：Playwright 的 `name` 默认按**子串**匹配，而弹层打开之后
    // 那一枚「确认发布 V1」也含「发布 V1」，严格模式会同时收到两个。
    await page.getByRole('button', { name: '发布 V1', exact: true }).click();
    await page.getByRole('button', { name: '确认发布 V1' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: 'V1 已发布，当前版本已锁定' })
    ).toBeVisible();

    // ⑦ 已发布不可覆盖（§5.4 的核心验收）：界面这一半是**正文只读 + 保存按钮置灰**，
    //    而**服务端那一半**（`REPORT_IMMUTABLE` 409）由 `test_reporting_api.py` 守——
    //    §5.13 Phase A 之后界面上不再有「先发一次请求再挨一句 409」那条路，
    //    所以这一条断的是「入口已经不在了」，不是「按钮点了会失败」。
    await expect(page.getByLabel('1. 整体情况说明')).toHaveAttribute('readonly', '');
    await expect(page.getByRole('button', { name: '保存草稿' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '保存草稿' })).toHaveAttribute(
      'title',
      '当前版本只读；要继续修改请先新建版本'
    );
  });
});

// ========== 专业报告工作台（V2.0.0 §5.13.8 十条）==========

/**
 * 打开「专业分析报告」并等它**真的可用**。
 *
 * 判据取 `FilterBar` 那句「已自动加载最新可分析任务」而不是「页面渲染完了」：这句话
 * 只在 `query(true)` 跑完之后才出现，而下面每一条都要在它之后才能动筛选或保存。
 * 等一个自己挑的 DOM 标志（比如「保存草稿」按钮）会漏掉这一条——那一枚按钮在任务
 * 加载完之前就已经在页面上了（它按 `canCreate` 置灰）。
 */
async function openReportPage(page: Page) {
  await page.goto('/counselor/analytics/report')
  await expect(page.getByRole('heading', { name: '专业分析报告' })).toBeVisible()
  await expect(
    page.getByRole('status').filter({ hasText: '已自动加载最新可分析任务' })
  ).toBeVisible()
}

/** 竞态用例里那两次读数各自的答案：成功换一个标题，失败换一句话。 */
type ListAnswer = { ok: true; title: string } | { ok: false; message: string }

/**
 * 「报告列表连着读两次、先发的那一次回来晚了」的脚手架（§5.16 的竞态守卫）。
 *
 * **触发器为什么是那一枚「重试」。** `loadReports` 的调用点里能从界面上连点两次的只有它：
 * 列表自己那一枚重试被 `v-if="loading"` 挡在 DOM 之外（读的过程中它不在页面上），
 * 保存 / 发布 / 新建版本三处要先真的写一次库，挂载只能发生一次。而打开报告失败时那一枚
 * （`<ErrorState v-if="openError" :on-retry="loadReports"/>`）**不在 `opened` 那两个分支
 * 里**，所以 `opened` 还是 null 的时候它照样在——连点两下就是人手快点了两次「重试」。
 *
 * **次序是构造出来的，不是掐表比出来的。** 先发的那一次一直扣在闸门上，直到第二次的答案
 * **已经交出去**之后才被放行。§5.16 的 DoD 明确禁了「依赖随机网络时序」，所以这里没有
 * 任何一个 `setTimeout` 参与决定谁先谁后（断言那一侧的 400ms 只是留给渲染的落地窗口）。
 *
 * 两份答案都以**真载荷**为底、只换第一行的标题：自己拼一份假载荷的话，「状态」与
 * 「任务范围」那两列会随着页面改动而失效，而这条用例要钉的不是它们。第一行一定在第 1 页
 * 上——组件里的 `filtered` 不改次序、`PAGE_SIZE` 是 20。
 */
async function raceReportListReload(page: Page, slow: ListAnswer, fast: ListAnswer) {
  type Row = Record<string, unknown>
  let real: Row[] = []
  let racing = false
  let raceCalls = 0
  let releaseSlow = () => {}
  const slowGate = new Promise<void>((resolve) => {
    releaseSlow = resolve
  })
  let settleSlow = () => {}
  const slowDone = new Promise<void>((resolve) => {
    settleSlow = resolve
  })

  const payload = (answer: ListAnswer) =>
    answer.ok
      ? { success: true, data: { items: [{ ...real[0], title: answer.title }, ...real.slice(1)] } }
      : {
          success: false,
          data: null,
          request_id: 'e2e-race-probe',
          error: { code: 'INTERNAL_ERROR', message: answer.message },
        }

  await page.route(
    (url) => url.pathname === '/api/v1/professional-reports',
    async (route) => {
      if (!racing) {
        // 竞态开始之前的读数（挂载那一次）照常走真后端，顺手留一份载荷当底子。
        const response = await route.fetch()
        real = ((await response.json()) as { data: { items: Row[] } }).data.items
        await route.fulfill({ response })
        return
      }
      raceCalls += 1
      if (raceCalls === 1) {
        await slowGate // 扣住：第二次的答案交出去之前，它一直答不出来
        await route.fulfill({ status: slow.ok ? 200 : 500, json: payload(slow) })
        settleSlow()
        return
      }
      // 后发的那一次先把答案交出去，再放行先发的那一次——次序就此定死。
      await route.fulfill({ status: fast.ok ? 200 : 500, json: payload(fast) })
      releaseSlow()
    }
  )

  await loginAs(page, 'counselor')
  await openReportPage(page)
  // 先证明有东西可扫：下面两次读数换掉的是第一行的标题。
  await expect(page.locator('.report-list .row').first()).toBeVisible()

  // 让「打开报告」失败一次，逼出那一枚不受列表 loading 约束的重试按钮。
  await failApiPathsMatching(page, /^\/api\/v1\/professional-reports\/\d+$/, '报告加载失败')
  await page.locator('.report-list .row').first().click()
  const retry = page
    .locator('.error-state')
    .filter({ hasText: '报告加载失败' })
    .getByRole('button', { name: '重试' })
  await expect(retry).toBeVisible()

  racing = true
  await retry.click() // 先发的那一次
  await retry.click() // 后发的那一次

  return { slowDone }
}

/**
 * 这一次写入会不会**留在共享演示库**里？会——每跑一次就多几份报告。
 *
 * 这是 §5.13.8 的固有代价（报告是「新建」出来的，没有删除接口，与 `export_job`
 * 那条残留同类），不是可以顺手绕开的东西。三件事因此写在这里：
 *  · **不写死份数与编号**：报告编号按天递增（`RPT-YYYYMMDD-NNNN`），份数每跑一次都变，
 *    写死它等于给下一个人埋一条与功能无关的红；
 *  · **不改操作员自己配的东西**：这一组只新建报告，不碰系统配置与权限矩阵；
 *  · **每一处都先证明有东西可扫**：`.prose` 有内容、`tbody tr` 有一行、
 *    弹层真的开出来了，然后才断言它干净——§测试注意里那条的第六、七例。
 */
test.describe('专业报告工作台', () => {
  test('草稿保存后刷新页面，能从我的报告里重新打开并接着编辑', async ({ page }) => {
    await loginAs(page, 'counselor')
    await openReportPage(page)

    const body = 'e2e 恢复核对：这一段的正文在刷新之后必须原样回来。'
    await page.getByLabel('1. 整体情况说明').fill(body)
    const reportNo = await saveNewDraft(page)

    // ① 保存之后它出现在「我的报告」里，状态是**草稿 · V1**（版本号来自版本行自己）。
    const row = reportRow(page, reportNo)
    await expect(row).toBeVisible()
    await expect(row).toContainText('草稿 · V1')

    // ② 刷新 —— 这就是「刷新一次内存里那份就没了」那条老毛病的可执行形式。
    await page.reload()
    await expect(page.getByRole('heading', { name: '专业分析报告' })).toBeVisible()
    await expect(reportRow(page, reportNo)).toBeVisible()

    // ③ 从列表里点开它，正文原样回来。
    await reportRow(page, reportNo).click()
    await expect(page.getByLabel('1. 整体情况说明')).toHaveValue(body)

    // ④ 接着编辑并保存：回执里必须还是**同一个编号**（接着写，不是又新建了一份）。
    await page.getByLabel('1. 整体情况说明').fill(body + RECOVER_TEXT)
    await page.getByRole('button', { name: '保存草稿' }).click()
    await expect(pageNotice(page)).toContainText(`草稿已保存至服务器（${reportNo}）`)
    await expect(page.getByLabel('1. 整体情况说明')).toHaveValue(body + RECOVER_TEXT)
  });

  test('发布 V1 之后当前版本只读，新建 V2 可以继续编辑并发布', async ({ page }) => {
    await loginAs(page, 'counselor')
    await openReportPage(page)

    await page.getByLabel('1. 整体情况说明').fill(PUBLISHED_TEXT)
    const reportNo = await saveNewDraft(page)
    await expect(reportRow(page, reportNo)).toBeVisible()

    // ① 发布 V1 —— 弹层背后的页面上立着一枚同名按钮，所以确认那一枚必须 `exact`。
    await page.getByRole('button', { name: '发布 V1', exact: true }).click()
    await page.getByRole('button', { name: '确认发布 V1' }).click()
    await expect(pageNotice(page)).toContainText('V1 已发布，当前版本已锁定')

    // ② 锁定的判据是**输入框与按钮两处**：只断按钮置灰的话，一个「按钮灰了但输入框
    //    还能改」的实现照样绿，而用户会在锁定的版本上打一整段字然后丢掉。
    await expect(page.getByLabel('1. 整体情况说明')).toHaveAttribute('readonly', '')
    await expect(page.getByRole('button', { name: '保存草稿' })).toBeDisabled()

    // ③ 新建 V2 草稿：V1 不被改动，而正文可以接着改。
    await page.getByRole('button', { name: '基于当前版本继续编辑' }).click()
    await page.getByRole('button', { name: '新建版本' }).click()
    await expect(pageNotice(page)).toContainText('已创建 V2 草稿，可以继续编辑')
    await expect(page.getByLabel('1. 整体情况说明')).not.toHaveAttribute('readonly', '')
    await page.getByLabel('1. 整体情况说明').fill(DRAFT_TEXT)
    await page.getByRole('button', { name: '保存草稿' }).click()
    await expect(pageNotice(page)).toContainText(`草稿已保存至服务器（${reportNo}）`)

    // ④ 发布 V2 —— 两条版本行都在，V1 没有被覆盖掉。
    await page.getByRole('button', { name: '发布 V2', exact: true }).click()
    await page.getByRole('button', { name: '确认发布 V2' }).click()
    await expect(pageNotice(page)).toContainText('V2 已发布，当前版本已锁定')
    await expect(versionRow(page, 1)).toBeVisible()
    await expect(versionRow(page, 2)).toBeVisible()
    await expect(versionRow(page, 1)).toContainText('已发布')
    await expect(versionRow(page, 2)).toContainText('已发布')
  });

  test('V2 草稿期间，领导读到的仍是 V1 且看不到 V2 草稿', async ({ page }) => {
    const reportNo = await reportWithDraftOverPublished(page)

    await loginAs(page, 'leader')
    await page.goto('/leader/analytics/report')
    const item = page.locator('.report-item').filter({ hasText: new RegExp(`${reportNo}(?!\\d)`) })
    await expect(item).toBeVisible()
    await item.click()

    const detail = page.locator('.report-detail')
    // 先证明有东西可看：正文四段里第一段必须是**已发布的 V1**那一句。
    await expect(detail.locator('.prose').first()).toHaveText(PUBLISHED_TEXT)
    // 版本号读的是 `content_version`（V1），不是报告头的 `current_version`（V2）。
    await expect(detail.locator('.meta')).toContainText('已发布 · V1')
    // 草稿的正文一个字都不许出现在领导这一侧。
    await expect(detail).not.toContainText(DRAFT_TEXT)

    // 领导导出的就是这一版（`selectedVersion` 取自 `content`），回执里带着作业编号。
    await page.getByRole('button', { name: '导出这一版' }).click()
    await page.getByLabel(/导出用途/).selectOption({ index: 1 })
    await page.getByRole('button', { name: '生成并下载' }).click()
    // 领导这一页的状态行是 `<span role="status">`，心理老师那一页是 `<p role="status">`
    // ——而 `pageNotice` 收在 `p` 上（那一页上另有一个 `span` 是筛选栏的提示）。
    // 所以这里按文本找那一句，不借那个 helper。
    await expect(
      page.getByRole('status').filter({ hasText: '已下载，可在导出中心查看（EXPORT-' })
    ).toBeVisible()
  });

  test('在 V2 页面上选择导出 V1，导出中心留下那一版的记录', async ({ page }) => {
    const reportNo = await reportWithDraftOverPublished(page)

    await loginAs(page, 'counselor')
    await openReportPage(page)
    await reportRow(page, reportNo).click()
    // 当前版本是 V2 草稿，所以正文是草稿那一句——**先证明打开的是这一份**，
    // 否则下面「导出 V1」可能是在另一份报告上做的。
    await expect(page.getByLabel('1. 整体情况说明')).toHaveValue(DRAFT_TEXT)

    await page.getByRole('button', { name: '进入导出设置' }).click()
    await page.getByRole('button', { name: '生成报告' }).click()
    await page.getByLabel(/导出哪一版/).selectOption('1')
    await page.getByLabel(/导出用途/).selectOption({ index: 1 })
    await page.getByRole('button', { name: '生成并下载' }).click()
    const notice = pageNotice(page)
    await expect(notice).toContainText('已下载，可在导出中心查看（EXPORT-')

    // 「本次操作结果」那一行说的是**哪一版**：这一格就是「导出的确实是 V1」的判据。
    const row = page.locator('.audit-section tbody tr')
    await expect(row).toHaveCount(1)
    await expect(row.locator('td').nth(2)).toHaveText('V1')

    // 同一个作业在导出中心查得到（编号从回执里现取，不写死）。
    //
    // 编号要**整串命中**，不能用裸字符串做 `hasText`：那个选项是**子串**匹配，
    // 而按天编号的作业号互为前缀。2026-09-26 在真页面上量过一次（一次性探针，跑完删）：
    // 拿 `…-21` 当针时裸 `hasText` 命中 **8** 行（`-210`…`-219`），而那一个编号本身
    // **一行都不在首页**——整串命中给的才是 0，也就是实话。
    //
    // **这一处今天不会真的红**（所以它不是那条偶发红的根因，别把它读成修好了什么）：
    // 本用例的编号取自「当天行数 + 1」，也就是当天最大的那一个，没有比它更长的编号
    // 能把它当子串包住。改它的理由是**让它说的正是它要做的事**——同文件里两处同类
    // 定位器早就这么写了：`reportRow` 用 `new RegExp(\`${reportNo}(?!\\d)\`)`、
    // `versionRow` 用 `^V${no}$`（它的注释逐字写着「避免 V1 命中 V10」）。留着裸写法
    // 就是留一颗「换个取号口径就响」的雷，而那两个兄弟的正解就摆在上面十几行处。
    // `toHaveCount(1)` 一个字节没改：它仍然在说「同一个作业恰好一行」。
    const jobNo = (await notice.innerText()).match(/EXPORT-[\d-]+/)?.[0]
    expect(jobNo, '导出回执里必须带着作业编号').toBeTruthy()
    await page.goto('/counselor/exports')
    await expect(
      page.locator('tbody tr').filter({ hasText: new RegExp(`${jobNo}(?!\\d)`) })
    ).toHaveCount(1)
  });

  test('有未保存改动时四处都拦一下，保存之后不再拦', async ({ page }) => {
    // 先用接口造**另一份**报告，好让「打开其他报告」那一条有得点。
    const otherNo = await reportWithDraftOverPublished(page)

    await loginAs(page, 'counselor')
    await openReportPage(page)
    await page.getByLabel('1. 整体情况说明').fill(PUBLISHED_TEXT)
    const reportNo = await saveNewDraft(page)
    await expect(reportRow(page, reportNo)).toBeVisible()

    // 弄脏：这一段**没有**保存。
    await page.getByLabel('1. 整体情况说明').fill(PUBLISHED_TEXT + '（未保存的改动）')
    const confirmTitle = page.getByText('放弃未保存的修改？')
    const cancel = page.getByRole('button', { name: '取消' })

    // ① 切换筛选条件：`FilterBar` 的 `beforeChange` 排在「有没有选任务」**之前**，
    //    所以点「查询」就够，不必真的换一个任务集（换了会把这次的分析范围改掉）。
    //    选择器收在筛选卡里：那一枚按钮的可见文字是 `⌕ 查询`，而不收窄的话
    //    「查询」这两个字在别处出现时 `getByRole` 的子串匹配会撞上严格模式。
    const filters = page.locator('.card.filters')
    await filters.getByRole('button', { name: /查询/ }).click()
    await expect(confirmTitle).toBeVisible()
    await cancel.click()
    await expect(confirmTitle).toHaveCount(0)

    // ② 打开「我的报告」里的另一份。
    await reportRow(page, otherNo).click()
    await expect(confirmTitle).toBeVisible()
    await cancel.click()
    await expect(confirmTitle).toHaveCount(0)
    // 取消之后**还停在原来那一份上**（不能只断弹层消失）。
    await expect(page.getByLabel('1. 整体情况说明')).toHaveValue(PUBLISHED_TEXT + '（未保存的改动）')

    // ③ 重置。
    await filters.getByRole('button', { name: /重置/ }).click()
    await expect(confirmTitle).toBeVisible()
    await cancel.click()
    await expect(confirmTitle).toHaveCount(0)

    // ④ 离开这一页。必须走**客户端路由**：`page.goto` 是整页刷新，`onBeforeRouteLeave`
    //    根本不会触发，那样这条会永远绿。
    await page.locator('.sidebar').getByRole('link', { name: /工作台/ }).first().click()
    await expect(confirmTitle).toBeVisible()
    await cancel.click()
    await expect(confirmTitle).toHaveCount(0)
    await expect(page).toHaveURL(/\/counselor\/analytics\/report$/)

    // ⑤ 保存之后同一处不再拦，「查询」直接走过去。
    await page.getByRole('button', { name: '保存草稿' }).click()
    await expect(pageNotice(page)).toContainText(`草稿已保存至服务器（${reportNo}）`)
    await filters.getByRole('button', { name: /查询/ }).click()
    await expect(confirmTitle).toHaveCount(0)
    await expect(page.getByRole('status').filter({ hasText: '已刷新' })).toBeVisible()
  });

  test('导出弹层的必填、取消与失败保留', async ({ page }) => {
    await loginAs(page, 'counselor')
    await openReportPage(page)
    await page.getByLabel('1. 整体情况说明').fill(PUBLISHED_TEXT)
    await saveNewDraft(page)

    await page.getByRole('button', { name: '进入导出设置' }).click()
    await page.getByRole('button', { name: '生成报告' }).click()
    const dialog = page.locator('.modal-panel')
    await waitForModalSettled(dialog)

    // ① 必填：先把用途清回占位那一项，再提交——**这一清是必需的**：那个下拉的默认值是
    //    用途表里的第一项（`defaultValue: purposes.includes(lastPurpose) ? lastPurpose
    //    : purposes[0]`），不清就是有值的，直接点提交会真的发出去一个请求，
    //    而这条要证明的正是「没选用途时不发请求」。
    await page.getByLabel(/导出用途/).selectOption('')
    await page.getByRole('button', { name: '生成并下载' }).click()
    await expect(page.getByText('导出用途不能为空')).toBeVisible()
    await expect(dialog).toBeVisible()

    // ② 取消：弹层关掉，页面上不留任何「已下载」的痕迹。
    await page.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toHaveCount(0)
    await expect(pageNotice(page)).not.toContainText('已下载')

    // ③ 失败保留用途。桩要能撤（`unroute` 要同一枚 handler 引用），所以写成具名函数；
    //    匹配用判定函数而不是通配串——这一条路径里带 id，串通配会顺手把版本查询也拦掉。
    const exportPath = /\/api\/v1\/professional-reports\/\d+\/export-jobs$/
    const failing = (route: Route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          data: null,
          request_id: 'e2e-export-fail',
          error: { code: 'INTERNAL', message: 'e2e 桩：导出文件生成失败' }
        })
      })
    await page.route(exportPath, failing)

    await page.getByRole('button', { name: '生成报告' }).click()
    await waitForModalSettled(dialog)
    const purpose = page.getByLabel(/导出用途/)
    await purpose.selectOption({ index: 1 })
    const chosen = await purpose.inputValue()
    expect(chosen, '用途下拉里必须有可选项').toBeTruthy()
    await page.getByRole('button', { name: '生成并下载' }).click()
    // 提交之后弹层**立刻**关掉（`settleForm` 里就关了），结果由页面上的状态行说，
    // 所以失败那句话说在页面上、而不是弹层里。
    await expect(pageNotice(page)).toContainText('e2e 桩：导出文件生成失败')

    // ④ 重新打开：上一次填的用途还在（`lastPurpose` 在发请求之前就写了）。
    await page.getByRole('button', { name: '生成报告' }).click()
    await waitForModalSettled(dialog)
    await expect(page.getByLabel(/导出用途/)).toHaveValue(chosen)
    await page.getByRole('button', { name: '取消' }).click()
    await expect(dialog).toHaveCount(0)

    // ⑤ 撤掉桩之后同一条路走得通。
    await page.unroute(exportPath, failing)
    await page.getByRole('button', { name: '生成报告' }).click()
    await waitForModalSettled(dialog)
    await expect(page.getByLabel(/导出用途/)).toHaveValue(chosen)
    await page.getByRole('button', { name: '生成并下载' }).click()
    await expect(pageNotice(page)).toContainText('已下载，可在导出中心查看（EXPORT-')
  });

  test('领导页默认打开最新那一份，且没有任何编辑控件', async ({ page }) => {
    // 先保证库里真的有已发布的报告——否则下面断的是「一个空的只读页」，
    // 而空页上当然没有编辑控件（那是「先证明有东西可扫」的反面）。
    await reportWithDraftOverPublished(page)

    await loginAs(page, 'leader')
    await page.goto('/leader/analytics/report')
    const items = page.locator('.report-item')
    await expect(items.first()).toBeVisible()
    // 「默认打开最新那一份」的可执行形式：第一行就是被选中的那一行。不去断是哪一份
    // （那取决于共享库此刻有什么），只断「进来就已经选中了」，与列表的排序口径无关。
    await expect(items.first()).toHaveAttribute('aria-pressed', 'true')

    const detail = page.locator('.report-detail')
    await expect(detail).toBeVisible()
    await expect(detail.locator('.prose').first()).not.toBeEmpty()

    // 只读页的判据是**没有任何编辑控件**，不是「按钮置灰」——一枚灰着的输入框仍然
    // 会让人以为差一个权限就能改。
    await expect(page.locator('textarea')).toHaveCount(0)
    // 判据按**整串控件名**来，不用 `/保存|发布|新建版本|继续编辑/` 这种子串组合：
    // 领导页每一行报告卡的正文里就有「发布于 …」（那是发布时间），一条子串正则把这
    // 十几行一起数进来，于是它断的成了「列表此刻有几行」——实测就是这条：期望 0、
    // 收到 16，而页面上一个编辑控件都没有。
    await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /^发布 V\d+$/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '基于当前版本继续编辑', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '新建版本', exact: true })).toHaveCount(0)
    // 而「导出这一版」在——只读不等于不能拿走（§5.13.5）。
    await expect(page.getByRole('button', { name: '导出这一版' })).toBeVisible()
  });

  test('领导页两类空态分得开', async ({ page }) => {
    // 「学校尚未发布报告」这一支在真机上随后就不可达了（同一组前一条用例会造出已发布的
    // 报告），所以这里用桩把它逼出来。桩只挡**列表**那一个路径：`/professional-reports/{id}`
    // 与导出都不经过它，写成通配串会顺手把别的请求也拦掉。
    //
    // 先造一份已发布的报告：后半段（撤桩之后）断的是「有报告但筛不到」，而演示库本身
    // **不种**专业报告，靠同组别的用例并行跑出来的是不能依赖的（`fullyParallel`）。
    await reportWithDraftOverPublished(page)
    await loginAs(page, 'leader')
    const listPath = /\/api\/v1\/professional-reports$/
    const empty = (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { items: [] },
          request_id: 'e2e-empty-reports',
          error: null
        })
      })
    await page.route(listPath, empty)
    await page.goto('/leader/analytics/report')
    await expect(page.getByText('学校尚未发布报告')).toBeVisible()
    await expect(page.getByText('当前筛选无匹配报告')).toHaveCount(0)
    await page.unroute(listPath, empty)

    // 撤桩之后是「有报告但筛不到」——这是另一句话，两者不能互相顶替。
    await page.reload()
    await expect(page.locator('.report-item').first()).toBeVisible()
    await page.getByPlaceholder('按标题或报告编号筛选').fill(`zzz-不存在的报告-${Date.now()}`)
    await expect(page.getByText('当前筛选无匹配报告')).toBeVisible()
    await expect(page.getByText('学校尚未发布报告')).toHaveCount(0)
  });

  test('375 / 768 / 1024 / 1440px 下报告页不横向溢出，窄档底部导航仍在视口里', async ({ page }) => {
    const reportNo = await reportWithDraftOverPublished(page)
    await loginAs(page, 'counselor')
    await openReportPage(page)
    await reportRow(page, reportNo).click()
    // 版本时间线要真的渲染出来（`v-if="opened"`）——否则这一条漏扫了整块。
    await expect(page.locator('.versions .row').first()).toBeVisible()

    // 四档（§5.13.9 的人工检查清单）：`mobile` 决定**要不要**断底部导航，理由见下。
    for (const { width, height, mobile } of VIEWPORTS) {
      const label = `${width}px 的报告页`
      await page.setViewportSize({ width, height })
      await expectNoHorizontalOverflow(page, label)
      // **底部导航只在 780px 以下成立**（`styles.css` 的 `@media (max-width: 780px)`
      // 才把 `.sidebar` 变成 `position: fixed; bottom: 0`）。更宽的视口里它是普通侧栏、
      // 会随页面一起滚动，断它整块落在视口内会**无故变红**——而一条会无故变红的守卫
      // 很快会被人关掉（CLAUDE.md §18），连带把上面那条溢出断言一起丢掉。
      if (mobile) await expectBottomNavInViewport(page, label)

      // 确认弹层也要量一次：它有自己的一层定位与宽度，页面不溢出不代表弹层不溢出。
      // 先弄脏，再点「重置」把弹层叫出来。
      await page.getByLabel('1. 整体情况说明').fill(`${DRAFT_TEXT}${label}`)
      await page.getByRole('button', { name: /重置/ }).click()
      const confirmDialog = page.locator('.modal-panel')
      await expect(page.getByText('放弃未保存的修改？')).toBeVisible()
      await waitForModalSettled(confirmDialog)
      await expectNoHorizontalOverflow(page, `${label}的确认弹层`)
      await page.getByRole('button', { name: '取消' }).click()
      await expect(confirmDialog).toHaveCount(0)
    }
  });

  test('键盘可以走完选报告、续写、保存、发布与导出', async ({ page }) => {
    const reportNo = await reportWithDraftOverPublished(page)
    await loginAs(page, 'counselor')
    await openReportPage(page)
    // **先等列表真的加载完再按 Tab。** `openReportPage` 只等到筛选栏那句「已自动加载
    // 最新可分析任务」，而报告列表是**另一次**请求，它排在那条很重的分析查询后面
    // （单进程后端上实测会排在后面好几秒）。不等它就开始按 Tab，那 160 次会全部空转
    // 在「正在加载报告…」那一帧上——红的原因与键盘可达性毫无关系。
    await expect(reportRow(page, reportNo)).toBeVisible({ timeout: 30000 })

    // ① 选中「我的报告」里那一行（Enter 就是按钮的默认激活键）。
    //
    // **这里是两步，不能只按 Tab**：列表现在只占一个 Tab 停靠点（roving tabindex，
    // 见 `composables/useRovingFocus.ts`），所以 Tab 只能把它停在**某一行**上，而那一行
    // 不一定是这一条要的——它自己刚建的那一份与同一秒内建的其它报告 `updated_at` **并列**
    // （`now_utc_naive()` 截断到整秒，CLAUDE.md §20），并列时服务端不保证按更新时刻排。
    // 换行归方向键，那正是键盘用户此刻真实的路径。
    await tabIntoReportList(page)
    await arrowToReportRow(page, reportNo)
    await page.keyboard.press('Enter')
    await expect(page.getByLabel('1. 整体情况说明')).toHaveValue(DRAFT_TEXT)

    // ② 走到正文输入框里续写。
    await tabUntil(page, el => el.tag === 'TEXTAREA', '正文输入框')
    await page.keyboard.press('End')
    await page.keyboard.type(RECOVER_TEXT)
    await expect(page.getByLabel('1. 整体情况说明')).toHaveValue(DRAFT_TEXT + RECOVER_TEXT)

    // ③ 保存。
    await tabUntil(page, el => el.text.includes('保存草稿'), '保存草稿按钮')
    await page.keyboard.press('Enter')
    await expect(pageNotice(page)).toContainText(`草稿已保存至服务器（${reportNo}）`)

    // ④ 发布：确认那一枚在弹层里，`tabUntil` 从当前位置往后找得到它。
    await tabUntil(page, el => el.text.includes('发布 V2'), '发布按钮')
    await page.keyboard.press('Enter')
    await tabUntil(page, el => el.text.includes('确认发布 V2'), '确认发布按钮')
    await page.keyboard.press('Enter')
    await expect(pageNotice(page)).toContainText('V2 已发布，当前版本已锁定')

    // ⑤ 进入导出设置并打开弹层。
    await tabUntil(page, el => el.text.includes('进入导出设置'), '进入导出设置按钮')
    await page.keyboard.press('Enter')
    await tabUntil(page, el => el.text.includes('生成报告'), '生成报告按钮')
    await page.keyboard.press('Enter')
    //    定位器**必须点名是哪一个弹层**，不能用 `.modal-panel`：本应用有意支持弹层堆叠
    //    （§22 / `composables/modalStack.ts`），而刚才关掉的「发布 V2？」在
    //    `.modal-leave-active`（`styles.css`，0.2s）那一段里**还留在 DOM 里**，
    //    于是 `.modal-panel` 会同时匹配到两个——`toHaveCSS` 撞上严格模式冲突是立即抛错、
    //    不重试，所以这一条会**硬红**。实测（2026-09-26，探针两次）：
    //      · 关掉之后 0ms 查询 → 两个面板都在（`aria-label` 分别是「发布 V2？」与
    //        「导出专业报告」）→ 红；
    //      · 600ms 之后 → 只剩「导出专业报告」→ 绿。
    //    那是**正常的过渡行为，不是产品缺陷**；有歧义的是这里的定位器。
    await waitForModalSettled(page.getByRole('dialog', { name: '导出专业报告' }))

    // ⑥ 导出用途：Tab 走得到那一格，且**焦点真的落在它上面**。
    //
    //    「再用键盘把它的**值**改掉」这一半**在本环境里证不了，所以不写那条断言**——
    //    写下去它会红，而红的是浏览器行为、不是这一页能不能用键盘。实测（本仓库的
    //    Chromium 153 / macOS / 无头，探针跑在仓库根）：
    //      · `ArrowDown` / `ArrowUp` / `Home` / `End` **确实到达了 `<select>`**
    //        （挂 keydown 监听数得到），但值一个都不变——macOS 上原生 select 要先展开
    //        弹层才认方向键；
    //      · 展开用的空格与 `Alt+ArrowDown` 在无头环境里弹不出那个列表，随后按 `Enter`
    //        什么都不会发生（`Space,ArrowDown,Enter` 与 `Alt+ArrowDown,ArrowDown,Enter`
    //        都试过，值不动）；
    //      · type-ahead 是唯一真的改得动它的键盘交互（`keyboard.type('b')` 命中 `"B"`），
    //        而 `keyboard.type` 对非 ASCII 字符走 `insertText`、不产生 type-ahead——
    //        用途是中文的、且由操作员配置，打不出对应的键。
    //    所以这一条守的是**这一页自己那一半**：那一格在 Tab 次序里、拿得到焦点
    //    （`label` 判据见 `tabUntil`）。真实用户手上「能不能用它选出一项」是浏览器原生
    //    行为（空格展开、方向键移动、回车确认），本环境无从复现——PROGRESS §5.13.8 ⑩
    //    记着这一半，**别把它当成已经验过**。
    await tabUntil(page, el => el.tag === 'SELECT' && el.label.includes('导出用途'), '导出用途')
    await expect(page.getByLabel(/导出用途/)).toBeFocused()

    // ⑦ 提交。弹层里那一枚按钮的文案是 `submit-text`；Enter 在弹层里是提交。
    await tabUntil(page, el => el.text.includes('生成并下载'), '生成并下载按钮')
    await page.keyboard.press('Enter')
    await expect(pageNotice(page)).toContainText('已下载，可在导出中心查看（EXPORT-')
  });
});

// ========== 数据范围徽标（V2.0.0 §5.2 / P0-02）==========

test.describe('数据范围徽标', () => {
  /**
   * 页头那枚「当前数据范围：…」徽标（`ReportPageHeader.vue`），读数来自
   * `GET /api/v1/auth/me/data-scope-summary`。
   *
   * 它守的是 P0-02 那个毛病的**界面**那一半：页面文字说「全校」而数字其实是授权范围的。
   * §5.4 把两条路由标题里的「全校」拿掉之后，范围这句话**只剩徽标一个出口**——所以
   * 这一组每一条都成对地断：**说得出真实范围**，**且不说「全校」**。
   *
   * 两种造账号的办法，各有各的理由：
   * - 第 1 条用种子里那两位既有账号（心理老师 / 德育领导）：「全校」是他们的真实范围，
   *   零残留，同时是第 2 条的**对照**——没有它，「徽标不说全校」可能只是因为徽标
   *   什么都不显示。
   * - 第 2 条新建一个临时账号：库里没有年级 / 班级范围的心理老师，而**改既有账号的
   *   scope 不行**——`playwright.config.ts` 是 `fullyParallel`，别的 spec 会同时看到
   *   另一个名册，那是「跑 e2e 不许改掉共享演示数据」。
   *
   * 新建账号带三条已知代价，都是既有的、记在这里免得下一个人重新推一遍：
   * 1. 账号**没有删除接口**（§4：停用 ≠ 删除），每跑一次留一个临时账号。与
   *    `describe('账号管理')` 留下的那个同类，已知且接受（CLAUDE.md 测试注意）。
   * 2. 范围指向 `db/seed.py` 的**基线**年级 / 班级，不是 `seed_demo` 新建的那些：
   *    `purge_demo_data` 遇到仍被引用的演示班级会跳过不删（`db/purge.py`），跑一次就给
   *    那次清理留一块擦不掉的残渣；基线那一段上有 S001，它本来就活下来，所以指向它
   *    不多挡任何东西。**这一条有守卫**：名册里必须看得见 S001。
   * 3. 新账号带 `must_change_password`（§16.5：临时密码只该活一次登录），而
   *    `AppLayout.vue` 会据此弹强制改密弹层、把 `waitForURL` 卡死。所以先用接口把那一次
   *    改密走完——那不是绕开检查，是把用户本来就必须做的那一步做掉。
   */

  type ScopeOption = { scope_type: string; scope_id: number; name: string };

  const TEMP_PASSWORD = 'e2e-scope-temp';
  const ROTATED_PASSWORD = 'e2e-scope-rotated';

  async function apiLogin(page: Page, account: string, password: string, role: string) {
    const response = await page.request.post('/api/v1/auth/login', {
      data: { account, password, role },
    });
    expect(response.ok(), `${account} 登录失败，这一条失去了对象：${await response.text()}`).toBeTruthy();
    return (await response.json()).data.access_token as string;
  }

  /**
   * 建一个**只有一种**数据范围的临时心理老师，并把它变成一个能走界面登录的账号。
   *
   * 范围那一项**从服务端给的下拉选项里挑**，不写死 id：选项自带 `scope_id`，而在共享
   * 的开发库上写死一个 id，会在任何人插一个年级 / 班级之后指到别的东西上。挑中的那一项
   * 原样返回给调用方，让断言拿它拼期望值。
   */
  async function newCounselorWithScope(page: Page, scopeType: 'GRADE' | 'CLASS') {
    const adminToken = await apiLogin(page, 'admin', '123456', 'admin');
    const optionsResponse = await page.request.get('/api/v1/admin/accounts/scope-options', {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(optionsResponse.ok(), '范围下拉取不到，这一条失去了对象').toBeTruthy();
    const options = (await optionsResponse.json()).data.items as ScopeOption[];

    const granted =
      scopeType === 'GRADE'
        ? options.find((item) => item.scope_type === 'GRADE' && item.name === '初一')
        : // 班级取 **id 最小**的那一个：基线的 `初一 1班` 由 `db/seed.py` 先建，演示班与
          // 导入班都在它之后（上面第 2 条代价说的就是为什么必须挑到它）。
          options
            .filter((item) => item.scope_type === 'CLASS')
            .sort((a, b) => a.scope_id - b.scope_id)[0];
    expect(granted, `范围下拉里没有可用的 ${scopeType} 选项，这一条失去了对象`).toBeTruthy();

    const account = `1${String(Date.now()).slice(-10)}`;
    const created = await page.request.post('/api/v1/admin/accounts', {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: {
        role_code: 'counselor',
        display_name: '验收范围老师',
        account,
        temporary_password: TEMP_PASSWORD,
        scopes: [{ scope_type: granted!.scope_type, scope_id: granted!.scope_id }],
      },
    });
    expect(created.ok(), `临时账号没建出来：${await created.text()}`).toBeTruthy();

    const token = await apiLogin(page, account, TEMP_PASSWORD, 'counselor');
    const changed = await page.request.post('/api/v1/auth/change-password', {
      headers: { Authorization: `Bearer ${token}` },
      data: { old_password: TEMP_PASSWORD, new_password: ROTATED_PASSWORD },
    });
    expect(changed.ok(), `临时密码没换成正式密码：${await changed.text()}`).toBeTruthy();

    return { account, granted: granted!, headers: { Authorization: `Bearer ${token}` } };
  }

  /** 走界面登录一个**不在 `ROLES` 里**的临时账号（`helpers.ts` 的 `loginAs` 只认那四个种子账号）。 */
  async function loginAsAccount(page: Page, account: string) {
    await page.goto('/login');
    await expect(page).toHaveURL('/login');
    await page.getByRole('button', { name: '心理老师' }).click();
    await page.getByRole('textbox', { name: /手机号/ }).fill(account);
    await page.getByRole('textbox', { name: /密码/i }).fill(ROTATED_PASSWORD);
    await page.getByRole('button', { name: '登录' }).click();
    await page.waitForURL('/counselor/workbench');
  }

  test('两个全校范围的角色都读到「全校」，而标题那一侧不再宣称范围', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/analytics/overview');
    await expect(page.locator('.scope-badge')).toHaveText('当前数据范围：全校');

    await loginAs(page, 'leader');
    await page.goto('/leader/analytics/overview');
    await expect(page.locator('.scope-badge')).toHaveText('当前数据范围：全校');
    // 标题与徽标**各说各的事**：标题回答「这一页是干什么的」，徽标回答「下面的数是谁的
    // 数」。两条一起断——只有一个「筛查关注概览」标题、且范围只从徽标说出来。
    await expect(page.locator('.report-page-head h1')).toHaveText('筛查关注概览');
  });

  for (const scopeType of ['GRADE', 'CLASS'] as const) {
    test(`收紧到 ${scopeType} 的心理老师读到自己那一段，且读不到「全校」`, async ({ page }) => {
      const { account, granted, headers } = await newCounselorWithScope(page, scopeType);

      const summaryResponse = await page.request.get('/api/v1/auth/me/data-scope-summary', { headers });
      expect(summaryResponse.ok()).toBeTruthy();
      const data = (await summaryResponse.json()).data as {
        scopeType: string;
        displayText: string;
        schoolWide: boolean;
      };

      // ① 名册真的收在那一段里——徽标与行过滤必须同源（`student_scope_predicate`）。
      //    只断下面那句中文的话，一个「说得对、底下根本没按范围过滤」的实现是绿的，
      //    而那正是 P0-02 那个毛病的反向版本（屏幕对了、数据是另一回事）。
      const rosterResponse = await page.request.get('/api/v1/students', { headers });
      expect(rosterResponse.ok(), '临时账号读不到名册，这一条失去了下半句').toBeTruthy();
      const roster = (await rosterResponse.json()).data.items as Array<{
        student_no: string;
        grade: string;
        class_name: string;
      }>;
      // 先证明有东西可扫：空名册上「每一行都属于那一段」恒真。
      expect(roster.length, '这一条要证明「只剩那一段」，名册不能是空的').toBeGreaterThan(0);
      // 种子名册上的 S001 必须在里面。它同时是**残渣的守卫**：范围一旦指到 `seed_demo`
      // 新建的班级上，`purge_demo_data` 就会跳过那个班不删（`db/purge.py`），跑一次 e2e
      // 给那次清理留一块擦不掉的残渣。红了就说明挑范围的规则要改回基线那一段。
      expect(roster.map((row) => row.student_no)).toContain('S001');
      for (const row of roster) {
        if (scopeType === 'GRADE') expect(row.grade).toBe(granted.name);
        else expect(row.class_name).toBe(granted.name);
      }
      // 年级名从**名册**上取，不在这里写死一个字面量（换一所学校 / 加一个年级时，
      // 写死的那一份会悄悄变错，而它看起来完全正常）。
      const rosterGrade = roster[0].grade;

      // ② 那句中文本身。`not.toContain` 单独成一条：`displayText` 里恰好没有这两个字，
      //    与这一屏整体不该宣称全校，是两件事（`schoolWide` 是给程序读的那一半）。
      expect(data.scopeType).toBe(scopeType);
      expect(data.schoolWide).toBe(false);
      expect(data.displayText).not.toContain('全校');
      if (scopeType === 'GRADE') {
        expect(data.displayText).toBe(`${granted.name}年级`);
      } else {
        // 班级名在这所学校里不唯一（每一年级都有一个 1班），所以徽标要连着年级念。
        // 「念出了班级名」与「念出了年级」分开断——只断一处的话，光秃秃的「1 班」
        // 与漏掉年级的 `初一 1班` 各自能蒙混过一半。
        expect(data.displayText.replace(/\s+/g, '')).toContain(granted.name);
        expect(data.displayText).toContain(rosterGrade);
      }

      // ③ 屏幕上是同一句话。徽标是 `v-if="scope"` 的，所以取不到数时它整句不出现——
      //    `toHaveText` 在那种情况下会红，这条断言因此不是空转的。
      await loginAsAccount(page, account);
      await page.goto('/counselor/analytics/overview');
      await expect(page.locator('.report-page-head h1')).toHaveText('筛查关注概览');
      await expect(page.locator('.scope-badge')).toHaveText(`当前数据范围：${data.displayText}`);
    });
  }
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
    await page.goto('/admin/system');

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
    await page.goto('/admin/system');
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
    // 定位到**桌面那一套**：§5.14.6 起侧栏里有两套导航（`.nav-desktop` /
    // `.nav-mobile`），**两套都在 DOM 里、由媒体查询选一套**——所以裸的 `nav.nav`
    // 会解析到两个元素（严格模式当场报错），而「窄屏那一套也不含它」是另一件事，
    // 下一句单独断。
    const nav = page.locator('nav.nav-desktop');
    await expect(nav).toBeVisible();
    await expect(nav.getByText('账号与权限')).toBeVisible();
    await expect(nav.getByText('测评任务')).toHaveCount(0);
    // 窄屏底栏那一套同样不该有它。`display:none` 只把它挡在无障碍树之外，
    // **CSS 定位器照样数得到它**——只断桌面那一套的话，底栏里混进「测评任务」
    // 不会有任何东西看得见。
    // 先证明这一套真的渲染出来了：它在 1280px 下 `display:none`，一个「根本没渲染」
    // 的实现会让下面那条 `toHaveCount(0)` 变成恒真（§测试注意：先证明有东西可扫）。
    await expect(page.locator('nav.nav-mobile').getByText('账号与权限')).toHaveCount(1);
    await expect(page.locator('nav.nav-mobile').getByText('测评任务')).toHaveCount(0);

    // 直接敲 URL 也不会渲染出任务列表：路由表里已经没有这一条。
    await page.goto('/admin/tasks');
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: '测评任务' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '新建任务' })).toHaveCount(0);
  });
});

// ========== 管理员系统概览（V2.0.0 §5.14.5） ==========

/**
 * 这一组守的是「管理员落地页 = 系统健康页」那四条验收，它们是四件不同的事：
 *
 *   ① 卡片上的数与它点进去那个列表说的是**同一个数**（§11「指标卡上的数必须与它
 *      点进去的那个列表同源」）；
 *   ② 不可点的项**不装作能点**，可点的项仍然点得动（§5.14.4.1 那条保证在管理员
 *      页上的两个活体：学生账号、系统版本）；
 *   ③ 库里没有任何业务数据时，这一页仍然说得出系统的状态（验收第 1 条）；
 *   ④ 临时密码只出现一次，复制它**不留下第二份**（验收第 4 条）。
 *
 * 三条纪律在这组里都要遵守：
 *
 *   - **先证明有东西可扫**：概览页 `loading` 时渲染的是 `SkeletonBlock`，那一刻
 *     一张卡片都不存在，任何断言在那时候都是恒绿的。所以每条用例动手之前先等
 *     `.kpi-link` / `.check-row` 真的出现（§测试注意那一条，`auditModal` 的
 *     docstring 记着同一个坑）。
 *   - **不写死任何数字**：卡片上的数由 `accounts` 现算，演示库里它是几取决于此刻
 *     有哪些账号（`admin creates a staff account` 那条用例每跑一次就多一个）。
 *     断的是「两处相等」，不是某个具体值。
 *   - **不改库里的数据**：这组只读；唯一会写的是第四条那个「新建账号」，
 *     与既有的 `admin creates a staff account` 属同一类**已知且接受**的残留
 *     （账号没有删除接口，§4「停用 ≠ 删除」）。
 *
 * 反过来也要说清：`vocabulary.spec.ts` 的 `/admin/overview` 那一条清单项是**恒绿**
 * 的（这一页没有任何枚举码渲染点），真正守这一页的是下面这些用例——别把那一行
 * 读成它们的替代。
 */
test.describe('管理员系统概览', () => {
  /** 从「3 个」「3 个账号」这一类文案里取数。断的是两处相等，所以不写死数字。 */
  async function numberOn(locator: Locator): Promise<number> {
    return Number((await locator.innerText()).replace(/[^\d]/g, ''))
  }

  /** 等首屏数据到位：骨架屏那一帧 `.kpi-link` 一个都不存在。 */
  async function overviewReady(page: Page) {
    await page.goto('/admin/overview');
    await expect(page.locator('.kpi-link').first()).toBeVisible();
  }

  test('每张卡片都点得进去，并且把筛选条件一起带过去', async ({ page }) => {
    await loginAs(page, 'admin');

    // 卡片 → 目标页那个只读一次 URL 初值的下拉。**URL 与控件的值都要断**：
    // 只断 URL 的话，一个参数写错、目标页却照旧显示「全部」的实现照样绿——
    // 而那正是「点进去看到的不是卡片说的那一批」这个故障的样子。
    const drills = [
      {
        card: '.kpi-link', label: '未配置数据范围的账号',
        url: /\/admin\/system\?account=unconfigured$/,
        aria: '按账号状态筛选', value: 'unconfigured',
      },
      {
        card: '.kpi-link', label: '需修改密码的账号',
        url: /\/admin\/system\?account=must_change$/,
        aria: '按账号状态筛选', value: 'must_change',
      },
      {
        card: '.kpi-link', label: '名册导入含错误的批次',
        url: /\/admin\/organization\?import=errors$/,
        aria: '筛选导入批次', value: 'errors',
      },
      {
        card: '.kpi-link', label: '已过期的导出作业',
        url: /\/admin\/exports\?status=EXPIRED$/,
        aria: '按状态筛选导出作业', value: 'EXPIRED',
      },
      // 「已停用账号」不是 KPI 卡，是「账号概况」里的一行检查项——它同样带筛选，
      // 所以同样要能点到那个列表。漏掉它，这一条就只覆盖了四张卡里的三张。
      {
        card: '.check-row', label: '已停用账号',
        url: /\/admin\/system\?account=inactive$/,
        aria: '按账号状态筛选', value: 'inactive',
      },
    ] as const;

    for (const drill of drills) {
      await overviewReady(page);
      await page.locator(drill.card, { hasText: drill.label }).click();
      await expect(page).toHaveURL(drill.url);
      await expect(page.getByLabel(drill.aria)).toHaveValue(drill.value);
    }
  });

  test('卡片上的数与它点进去那个列表说的是同一个数', async ({ page }) => {
    await loginAs(page, 'admin');

    const accountCards = [
      { label: '未配置数据范围的账号', filter: 'unconfigured' },
      { label: '需修改密码的账号', filter: 'must_change' },
    ];
    for (const { label, filter } of accountCards) {
      await overviewReady(page);
      const card = page.locator('.kpi-link', { hasText: label });
      const shown = await numberOn(card.locator('.kpi-value'));
      await card.click();
      await expect(page).toHaveURL(new RegExp(`account=${filter}$`));
      await expect(page.getByLabel('按账号状态筛选')).toHaveValue(filter);
      // `exact: true` 是必须的：`10 个账号` 里含着子串 `0 个账号`，不写的话
      // 「卡片说 0、列表说 10」这种最该被抓到的分岔会**通过**（§测试注意那条
      // 「断言两者相等才有意义」的同一处：判据要能真的分开这两句话）。
      await expect(page.getByText(`${shown} 个账号`, { exact: true })).toBeVisible();
    }

    // 「已停用账号」那一行：卡片上是 `N 个`，列表上仍是 `N 个账号`。
    await overviewReady(page);
    const inactive = await numberOn(
      page.locator('.check-row', { hasText: '已停用账号' }).locator('strong')
    );
    await page.locator('.check-row', { hasText: '已停用账号' }).click();
    await expect(page).toHaveURL(/account=inactive$/);
    await expect(page.getByText(`${inactive} 个账号`, { exact: true })).toBeVisible();

    // 名册批次那一张：判据是 `error_rows > 0`，与目标页那个「只看含错误的」筛选
    // 是同一条——两处都按这个谓词走，所以「卡片说 N 批」与筛选之后剩下的行数
    // 必须相等。
    await overviewReady(page);
    const errored = await numberOn(
      page.locator('.kpi-link', { hasText: '名册导入含错误的批次' }).locator('.kpi-value')
    );
    await page.locator('.kpi-link', { hasText: '名册导入含错误的批次' }).click();
    await expect(page).toHaveURL(/import=errors$/);
    await expect(page.getByLabel('筛选导入批次')).toHaveValue('errors');
    // 只数**批次那张表**的行。这一页上还有一张学生表（`DataTable` 渲染出来的也是
    // `table/tbody/tr`），裸的 `page.locator('table tbody tr')` 会数到那一张去——
    // 它分页 10 行，于是断言收到 10，而「卡片说 0、列表说 10」这条最该被抓到的分岔
    // 恰好被那 10 行盖住了。
    const batchCard = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) });
    if (errored === 0) {
      // 0 的时候那张表整个不渲染（空态是一句关于数据的话，§14），所以「0 行」这件事
      // 得由那句话来证明——只断 `tbody tr` 计数的话，「表格是空的」与「表格根本没
      // 渲染」都是绿的（§29：一条恒绿的守卫比没有更糟）。反过来，若筛选没生效，
      // 这句话就不出现，一样会红。
      await expect(batchCard.getByText('最近这几批里没有含错误的批次。')).toBeVisible();
    } else {
      await expect(batchCard.locator('tbody tr')).toHaveCount(errored);
    }
  });

  test('不可点的项不装作能点，可点的项仍然点得动', async ({ page }) => {
    await loginAs(page, 'admin');
    await overviewReady(page);
    await expect(page.locator('.check-row').first()).toBeVisible();

    // 两个活体：学生账号（这一页没有按账号类型筛选的入口，学生也不在这里建）、
    // 系统版本（它不是一件可以去「处理」的事）。判据**三处一起断**——只看光标的话，
    // 一个 `<button disabled>` 也过得了；只看标签名的话，一个 `<div @click>` 也过得了。
    for (const label of ['学生账号', '系统版本']) {
      const row = page.locator('.check-row', { hasText: label });
      await expect(row).toBeVisible();
      const facts = await row.evaluate((el) => ({
        tag: el.tagName,
        role: el.getAttribute('role'),
        cursor: getComputedStyle(el).cursor,
      }));
      expect(facts.tag).toBe('DIV');
      expect(facts.role).toBeNull();
      expect(facts.cursor).not.toBe('pointer');
    }

    // 反方向：可点的那几行仍然带 `role="button"` 与手型光标。少了这一半，一个
    // 「把所有 check-row 都做成 div」的一致退化也能让上面那三条通过——那会同时
    // 把「能点」这件事静默弄丢，而屏幕上什么都不会变。
    for (const label of ['员工账号', '已停用账号', '当前量表版本']) {
      const row = page.locator('.check-row', { hasText: label });
      await expect(row).toHaveAttribute('role', 'button');
      expect(await row.evaluate((el) => getComputedStyle(el).cursor)).toBe('pointer');
    }
  });

  test('库里没有任何业务数据时，这一页仍然说得出系统的状态', async ({ page }) => {
    // 「无业务数据」在共享演示库上只有一种造法：把这一页要的那几个列表接口换成空。
    // 真去清库会把同一次运行里别的用例一起弄坏（「跑 e2e 不许改掉库里的东西」）。
    // 量表那一份也置空，于是「还没有已发布的量表版本」那条上线配置会被真的列出来
    // ——空库上本来就该是它。
    const emptyEnvelope = (data: unknown) => ({
      success: true,
      data,
      request_id: 'e2e-empty-probe',
      error: null,
    });
    const blanks: Array<[string, unknown]> = [
      ['/api/v1/admin/accounts', { items: [] }],
      ['/api/v1/student-roster/import/batches', { items: [], total: 0, truncated: false }],
      ['/api/v1/export-jobs', { items: [] }],
      ['/api/v1/scales/versions', { items: [] }],
    ];
    for (const [path, data] of blanks) {
      await page.route(
        (url) => url.pathname === path,
        (route) => route.fulfill({ json: emptyEnvelope(data) })
      );
    }

    await loginAs(page, 'admin');
    await overviewReady(page);

    // 四张卡片都在，而且各自说的是一个数——不是一片空白，也不是一处报错。
    for (const label of [
      '未配置数据范围的账号',
      '需修改密码的账号',
      '名册导入含错误的批次',
      '已过期的导出作业',
    ]) {
      await expect(
        page.locator('.kpi-link', { hasText: label }).locator('.kpi-value')
      ).toHaveText('0');
    }
    // 不可点的那两格仍然在：空库上它们说的是「没有这个东西」，而不是整格消失。
    await expect(page.locator('.check-row', { hasText: '学生账号' })).toBeVisible();
    await expect(page.locator('.check-row', { hasText: '系统版本' })).toBeVisible();
    // 量表为空 → 那一条上线配置必须真的列出来（这是「只列能从真实配置判定的项目」
    // 那句话的活体：它判的正是 `scales` 里有没有已发布版本）。
    await expect(
      page.locator('.check-row', { hasText: '还没有已发布的量表版本' })
    ).toBeVisible();
    // 一个错误态都不该出现：空数据不是读取失败（§14 三态要长得不一样）。
    await expect(page.getByText('系统概览加载失败')).toHaveCount(0);
  });

  test('临时密码可以复制，关掉之后就再也找不回来', async ({ page, context }) => {
    // 剪贴板要显式授权，否则 `navigator.clipboard.readText()` 在 headless 里
    // 拿不到东西——而「读回来比对」正是这一条唯一的强断言。
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    const consoleLines: string[] = [];
    page.on('console', (msg) => consoleLines.push(msg.text()));

    await loginAs(page, 'admin');
    await page.goto('/admin/system');

    // 账号用时间戳（账号是唯一约束，写死一串第二次跑就撞）；范围选「全校」——
    // 选年级或班级会在 `user_scope` 里留下一条指着**演示**年级/班级的行，而
    // `make purge-demo` 遇到仍被引用的班级会跳过不删，跑一次 e2e 就给那次清理
    // 留下一块擦不掉的残渣（与既有那条用例同一条理由）。
    const account = `1${String(Date.now()).slice(-10)}`;
    const password = 'e2e-copy-pass';
    await page.getByRole('button', { name: '新建账号' }).click();
    const form = page.getByRole('dialog');
    await form.getByLabel('角色').selectOption('counselor');
    await form.getByLabel('姓名').fill('复制验收老师');
    await form.getByLabel('登录账号').fill(account);
    await form.getByLabel('临时密码').fill(password);
    await form.getByLabel('数据范围').selectOption({ index: 1 });
    await form.getByRole('button', { name: '创建账号' }).click();

    const result = page.getByRole('dialog', { name: /账号已创建/ });
    await expect(result.getByText(password, { exact: true })).toBeVisible();

    await result.getByRole('button', { name: '复制' }).click();
    // **读回来比对**，不是看「已复制」那两个字：后者在一个复制了空串的实现上
    // 照样出现（`legacyCopy` 的注释记着 `execCommand('copy')` 就是这么骗人的）。
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(password);
    await expect(result.getByText('已复制到剪贴板')).toBeVisible();

    await result.getByRole('button', { name: '我已记录' }).click();
    await expect(result).toHaveCount(0);
    // 关掉之后三处残留各断一次：屏幕上、DOM 里、控制台里。
    await expect(page.getByText(password)).toHaveCount(0);
    expect(await page.evaluate((p) => document.body.innerHTML.includes(p), password)).toBe(false);
    // 非安全上下文那条路会在 body 上挂一个 textarea（`legacyCopy`）。它必须在
    // `finally` 里被摘掉——这一条在 Chromium 上是**兜底**（那条路今天走不到），
    // 但留着它是因为「摘不摘」只有真跑过 fallback 才知道，而写下来的判据不会忘。
    expect(await page.evaluate(() => document.querySelectorAll('body > textarea').length)).toBe(0);
    expect(consoleLines.filter((line) => line.includes(password))).toEqual([]);
    // 审计的 `detail` 里有没有它，在这一层**验不了**：`GET /audit-logs` 不发
    // `detail`（§8）。那一半由后端用例守（`test_account_admin_api.py` 的断言）。
  });

  /**
   * 四角色各跑一次键盘主流程（V2.0.0 §5.14.7）——这一条是**系统管理员**那一份。
   *
   * 与领导那一条同源，但走的是管理员首屏的三段：**概览卡片下钻 → 账号页清除筛选
   * → 用键盘打开行内的「账号操作」菜单**。第三段是 §5.14.5 那次改动的自己那一半：
   * 三枚动词（编辑 / 重置密码 / 停用）此前平铺在「操作」列里，收进弹层之后
   * 「键盘还打得开它吗」正是那次改动新引入的风险，而此前只有鼠标路径有用例
   * （`账号管理` 那一条用 `getByRole('button', { name: '操作' }).click()`）。
   *
   * 第二段（清除筛选）刻意留着：`?account=unconfigured` 在演示库里可能是 0 条，
   * 而「从卡片点进来的那个人处理完之后怎么出去」正是筛掉之后才会被问到的问题。
   * 判据里不断行数——行数归 `卡片上的数与它点进去那个列表说的是同一个数`。
   */
  test('键盘可以从系统概览下钻，并在账号页打开操作菜单', async ({ page }) => {
    await loginAs(page, 'admin');
    await overviewReady(page);

    // ① 下钻：Tab 停在「未配置数据范围的账号」那一张卡上，回车。
    await tabUntil(
      page,
      (el) => el.text.includes('未配置数据范围的账号'),
      '未配置数据范围的指标卡'
    );
    await expect(page.locator('.kpi-link', { hasText: '未配置数据范围的账号' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/admin\/system\?account=unconfigured$/);
    // **URL 与控件的值都要断**：那一档的值是 `initialAccountFilter()` 从 query
    // 读一次的结果，与 URL 是两个东西——只断 URL 的话，一个「跳过去了、筛选没生效」
    // 的实现照样绿（「每张卡片都点得进去…」那一条的理由逐字相同）。
    await expect(page.getByLabel('按账号状态筛选')).toHaveValue('unconfigured');

    // ② 清除筛选：等这一页自己加载完（检索栏与「清除筛选」同在那一个
    //    `v-if="!loading && !error"` 的分支里）。
    await expect(page.locator('.toolbar')).toBeVisible();
    await tabUntil(page, (el) => el.text === '清除筛选', '清除筛选按钮');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/admin\/system$/);
    await expect(page.getByLabel('按账号状态筛选')).toHaveValue('all');
    // 刷新一次仍然清着——`initialAccountFilter()` 进门读的正是 URL 上那一串，
    // 所以「清掉筛选、刷新，筛选又回来了」是这条链路上真实的坏法（发现 ①）。
    // 隔一拍的证据：刷新之后这一页是重新挂载的，那个 ref 只能从 URL 重建。
    await page.reload();
    await expect(page.getByLabel('按账号状态筛选')).toHaveValue('all');

    // ③ 账号操作菜单。清除筛选之后焦点落回 body（那一枚按钮被 `v-if` 摘掉了），
    //    所以接下来按到的第一枚「操作」就是表格**第一行**那一枚——不指定行号，
    //    因为行序由服务端定（排序默认值变了这条就会红在一个与键盘无关的地方）。
    await tabUntil(page, (el) => el.text === '操作', '第一行的操作按钮');
    await page.keyboard.press('Enter');
    const menu = page.getByRole('dialog', { name: /账号操作/ });
    await expect(menu).toBeVisible();
    // 三枚动词都在，且**键盘进得去**：一个渲染出来、Tab 走不进去的弹层，对键盘用户
    // 与一个坏掉的弹层是一回事（`Modal.vue` 的焦点陷阱见「无障碍契约」组）。
    await expect(menu.getByRole('button', { name: '编辑' })).toBeVisible();
    await expect(menu.getByRole('button', { name: '重置密码' })).toBeVisible();
    await tabUntil(page, (el) => el.text === '编辑', '菜单里的编辑按钮');

    // ④ 关掉之后焦点**还回来**——它落在那一枚「操作」上，用户原地还能再开一次。
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await expect(page.getByRole('button', { name: '操作', exact: true }).first()).toBeFocused();
  });

  /**
   * 管理员拿不到心理详情（V2.0.0 §5.14.7「心理详情 403」）。
   *
   * 这一条此前**只有后端那一半**（`test_permissions.py::test_admin_is_denied_every_
   * psych_detail_endpoint_under_defaults`）。e2e 这一半补的是另外两件它答不了的事：
   *
   * ① 界面上**到不了**——`/counselor/cases` 的 `meta.role` 是 `counselor`，
   *    `AppLayout.vue` 拿到 `/auth/me` 之后比对角色、不匹配就弹回登录页（缺口 4：
   *    前端没有路由守卫，越权访问表现为「被弹回登录页」）。
   *    **这条断言有两道门在保它，而且这是量出来的**：变异验证时把 `AppLayout.vue`
   *    那一判改成恒假（`if (false && …)`），这一条**照旧绿**——因为 `CasesPage.vue`
   *    的 `load()` 进门自己又比了一次角色码（`role_code !== 'counselor'` 就跳登录页）。
   *    所以它断的是**用户看到的结果**（他进不去那一页），不是「哪一处代码在拦」；
   *    要让它对某一道门敏感，得两道一起摘。写在这里，免得下一个人做完变异之后
   *    把「摘一道不红」读成守卫失灵（§4 那条「别把『拆了一处仍然全绿』读成守卫失效」
   *    是同一个形状，只不过那次说的是后端两处各查一次）。
   * ② **前端隐藏不是安全措施**（§4）。所以第 ① 条**什么都不能证明**——它证明的是
   *    「按钮没画出来」。真正的那道门在服务端，所以下面拿同一个 token 直接发请求，
   *    逐个点名那六条路径（与后端用例同一份名单：判据来源不同，只试一个的话另外
   *    几条上通着的旁路没有任何东西看得见）。
   *
   * 反面同样要断：管理员的**管理面**必须真的够得着。少了它，一个把管理员所有请求
   * 都拒掉的实现也能让上面六条通过——而那不是权限收紧，是整个管理面瘫了。
   */
  test('管理员到不了心理详情页，同一个 token 直接请求也是 403', async ({ page }) => {
    await loginAs(page, 'admin');
    const token = await page.evaluate(() => localStorage.getItem('xlp_access_token'));
    expect(token, '登录之后应当拿得到 token').toBeTruthy();
    const headers = { Authorization: `Bearer ${token}` };

    // ① 界面：直接敲 URL 也到不了那一页。
    await page.goto('/counselor/cases');
    await expect(page).toHaveURL('/login');

    // ② 服务端：真正的门。先拿一个真实存在的学生 id——管理员**读得到名册**
    //    （`GET /students` 走的是组织与账号那一档），而下面六条读的是心理详情。
    //    「先证明有东西可扫」：名册是空的时不跑这一条，否则 403 可能来自别处。
    const roster = await page.request.get('/api/v1/students', { headers });
    expect(roster.status(), `管理员读名册应当 200：${await roster.text()}`).toBe(200);
    const students = (await roster.json()).data.items as Array<{ id: number }>;
    expect(students.length, '演示名册上应当有学生').toBeGreaterThan(0);
    const studentId = students[0].id;

    const denied = [
      '/api/v1/care-cases',
      `/api/v1/care-cases/${studentId}`,
      `/api/v1/care-cases/${studentId}/comparison`,
      '/api/v1/students/results',
      `/api/v1/students/${studentId}/key-questions?purpose=排查`,
      `/api/v1/students/${studentId}/assessment-records`
    ];
    for (const path of denied) {
      const response = await page.request.get(path, { headers });
      expect(response.status(), `${path} 应当 403`).toBe(403);
      expect((await response.json()).error.code, path).toBe('ROLE_FORBIDDEN');
    }

    // ③ 反方向：管理面照旧。少了这一半，「403 全绿」与「管理面瘫了」分不开。
    expect((await page.request.get('/api/v1/admin/accounts', { headers })).status()).toBe(200);
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

  /**
   * Phase E 第 7 + 8 条（NAV-UX-01）：四档视口下顶栏与工作台都必须成立，且
   * 375–480px 把「登录设备 / 修改密码 / 退出」三枚收进账号菜单。
   *
   * 报告页那一侧的四档/溢出已由「专业报告工作台」的用例覆盖；这一条补的是
   * **顶栏与工作台**。`expectBottomNavInViewport` 只在 ≤780px 调用——只有那一档
   * `.sidebar` 才是 `position: fixed; bottom: 0`，更宽时它是普通侧栏、会随页面滚动，
   * 那时断它「整块在视口内」会无故变红（helper 自己写着这条）。
   *
   * 账号菜单那一段断的是**菜单里那三个名字与宽屏那三枚逐字相同**，而不是各写一份
   * 字面量：它们是同一个动作的两个 DOM 分支，改一处漏另一处时屏幕上就是两个名字，
   * 而照着宽屏那个名字去菜单里找的人会找不到（缺口 7 那一族：把「看不懂」换成了
   * 「搜不到」）。所以两边的文案用 `allTextContents()` 互相比。
   */
  test('四档视口下工作台不横向溢出，窄档顶栏把三枚动作收进账号菜单', async ({ page }) => {
    await loginAs(page, 'counselor')

    const reminderCard = page.locator('article.card', { has: page.getByRole('heading', { name: '近期提醒' }) })
    // 等首屏真的落地再量：折线与空态那句都是**取数之后**才出现的，在骨架那一帧量
    // 溢出等于什么都没量（CLAUDE.md 测试注意「先证明有东西可扫，再断言它干净」）。
    await expect(reminderCard.locator('.timeline-item, .card-body > .muted').first()).toBeVisible()

    for (const { width, height, mobile } of VIEWPORTS) {
      const label = `${width}px 的工作台`
      await page.setViewportSize({ width, height })
      await expectNoHorizontalOverflow(page, label)
      if (mobile) await expectBottomNavInViewport(page, label)

      const wideActions = page.locator('.top-action-wide:visible')
      const accountMenu = page.locator('.account-menu')

      if (width <= 480) {
        // 第 8 条：窄档那三枚必须真的收起来。`:visible` 是判据——CSS 用
        // `display:none` 藏它们，而 `display:none` 的元素 `:visible` 为假；
        // 只断言「菜单在」的话，一个「菜单与三枚并存」的实现也是绿的。
        await expect(wideActions, `${label}：顶栏那三枚动作没有收进账号菜单`).toHaveCount(0)
        await expect(accountMenu, `${label}：账号菜单没有出现`).toBeVisible()

        const trigger = accountMenu.locator('.acct-trigger')
        await trigger.click()
        await expect(trigger).toHaveAttribute('aria-expanded', 'true')
        const menuItems = accountMenu.locator('.acct-pop .acct-item')
        await expect(menuItems).toHaveCount(3)
        // 宽屏那三枚此时 `display:none`，但 `textContent` 与渲染无关，所以这里读到
        // 的正是它们的文案——两边排序后逐个比。
        expect(
          (await menuItems.allTextContents()).map(t => t.trim()).sort(),
          `${label}：账号菜单里那三个动作与顶栏那三枚不是一个名字`
        ).toEqual((await page.locator('.top-action-wide').allTextContents()).map(t => t.trim()).sort())
        // 展开状态再量一次：弹层有自己的定位与宽度，页面不溢出不代表它不溢出。
        await expectNoHorizontalOverflow(page, `${label} 的账号菜单展开时`)
        // 第 9 条（键盘）：Esc 收起，焦点此刻在 `.acct-trigger` 上、事件冒泡到菜单。
        await page.keyboard.press('Escape')
        await expect(menuItems).toHaveCount(0)
        // 再开一次，点遮罩收起——遮罩是这一层唯一的「点别处关闭」（刻意没有
        // document 外点监听，见 `AppLayout.vue` 那一段注释）。
        await trigger.click()
        await expect(menuItems).toHaveCount(3)
        await accountMenu.locator('.acct-backdrop').click()
        await expect(menuItems).toHaveCount(0)
      } else {
        await expect(wideActions, `${label}：宽屏上三枚动作应当直接可见`).toHaveCount(3)
        await expect(accountMenu, `${label}：宽屏上不该出现账号菜单`).toBeHidden()
      }
    }
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
    await page.goto('/admin/system');
    await page.getByRole('button', { name: '配置权限' }).click();

    await expect(page.getByRole('heading', { name: '角色权限矩阵' })).toBeVisible();
    // 八项能力各一块（V2.0.0 起 P1 专业报告加入三项），每块四个角色。
    // 数字随 `CAPABILITY_LABELS` 走：**加了新能力就要来改它**——这一条断的正是
    // 「后端定义了的能力，界面上都有得配」。改名而不改这里会红，那是对的。
    await expect(page.locator('.perm-block')).toHaveCount(8);
    await expect(page.locator('.perm-block').first().locator('.perm-cell')).toHaveCount(4);
    for (const label of [
      '聚合统计',
      '学生心理详情',
      '重点题/原始答卷',
      '组织与账号',
      '受控导出',
      '专业报告查看',
      '专业报告编辑',
      '专业报告发布'
    ]) {
      await expect(page.locator('.perm-block h3', { hasText: label })).toBeVisible();
    }
  });

  test('matrix reflects the backend defaults', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/system');
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
    await page.goto('/admin/system');
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
    await page.goto('/admin/system');
    await page.getByRole('button', { name: '配置权限' }).click();

    const cell = page.locator('.perm-block', { hasText: '学生心理详情' }).locator('.perm-cell').nth(1);
    await expect(cell.locator('.perm-mean')).toContainText('不返回单个学生的档案正文');
    await cell.locator('select').selectOption('SCOPED');
    await expect(cell.locator('.perm-mean')).toContainText('完整档案');
  });

  test('只提交动过的格子，改动清单写在保存按钮上方', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/system');
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
    await page.goto('/admin/system');
    await page.getByRole('button', { name: '配置权限' }).click();

    const cell = page.locator('.perm-block', { hasText: '聚合统计' }).locator('.perm-cell').nth(1);
    await cell.locator('select').selectOption('NONE');
    await expect(cell.locator('.customised-mark')).toBeVisible();

    // 「恢复默认」2026-09-27 多了一道二次确认（V2.0.0 §5.14.5）。这里要断**两件事**：
    // 确认之前草稿一个字都没动（按钮旁边那句「这不会立刻写入任何配置」说的就是这个），
    // 确认之后才回到出厂配置。
    await page.getByRole('button', { name: '恢复默认' }).click();
    const restoreConfirm = page.getByRole('dialog', { name: '恢复出厂配置' });
    await expect(restoreConfirm).toBeVisible();
    await expect(cell.locator('select')).toHaveValue('NONE');
    // 触发按钮与确认按钮**同名**，两者此刻同在 DOM 里（一个在矩阵 footer、一个在弹层），
    // 直接按名字取会撞上严格模式——所以按弹层标题定位。
    await restoreConfirm.getByRole('button', { name: '恢复默认' }).click();
    await expect(cell.locator('select')).toHaveValue('SCHOOL');
    // 恢复默认只是改草稿：按钮回到「没有改动」，什么都没写进库。
    await expect(page.getByRole('button', { name: '没有改动' })).toBeDisabled();
    await expect(cell.locator('.customised-mark')).toHaveCount(0);
  });

  test('越权的等级组合根本选不出来（学生拿不到「管理」）', async ({ page }) => {
    await loginAs(page, 'admin');
    await page.goto('/admin/system');
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
    await expect(page.getByRole('heading', { name: '重点关注学生与长期跟踪' })).toBeVisible();
    // 状态档是**七**个（V2.0.0 §5.14.3 加了「今日待办」；它此前是六个）。
    await expect(page.locator('.queue-tab')).toHaveCount(7);
    // 负责人档是**另一个轴**，与状态档可以叠加，所以它自己一个类名、自己一条断言：
    // 「全部 / 我负责的 / 未分配」三档。
    await expect(page.locator('.owner-tab')).toHaveCount(3);
    // 数字**不写死**：它是「切过去会看到几条」，随演示数据而变（与「不要给账号表加
    // 精确行数断言」同一条）。这里钉的是形状——默认档是「全部」，且后面跟着一个数。
    await expect(page.locator('.owner-tab.active')).toHaveText(/^全部（\d+）$/);
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

  /**
   * 未建档的学生也要有一个入口（2026-09-20，用户报的那件事的第二半）。
   *
   * 用户的原话：「其中张三学生定位为一般观察，但看到未建档，这是什么原因，
   * 也没有途径来查看此学生的过往测评信息」。第二问成立，而且缺口就在这一行上：
   * 未建档的行此前只渲染一个不可点的 `—`。**而「未建档」是大多数学生的状态**——
   * 全库唯一的开档触发点是重点题（第 85 / 97 题）答「是」
   * （`assessment_service.maybe_raise_risk_events` 的 docstring 逐字写着
   * 「Only 重点题命中写行」），关注等级本身从不建档。所以一个被评成
   * 「需要关注」的学生照样可能没有档案，而那时心理老师看不到他考过几次。
   *
   * 「有没有入口」这件事只能从**像素**上验：后端一直答得出这名学生的测评记录
   * （`care_service.student_assessment_records` 与档案无关），缺的只是可达性。
   *
   * 挑哪一名学生**问接口要**，不按当前这一页的第一行：名册的次序随班级与学号走，
   * 而「第一个未建档的人」未必交过卷——那会让下面「历次表至少一行」红在一个
   * 与被测功能无关的地方（§测试注意：行数问接口要，不写死）。
   */
  test('a student without a care case still has a way into their records', async ({ page }) => {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const items = (await (await page.request.get('/api/v1/students/results', { headers })).json())
      .data.items as {
      student_no: string;
      student_name: string;
      total_level: string | null;
      case_id: number | null;
    }[];
    const target = items.find((item) => item.case_id === null && item.total_level !== null);
    if (!target) throw new Error('演示数据里没有「有等级、无档案」的学生，这一条失去了对象');

    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases?tab=students');
    // 每页放到 100：名册 31 人、默认每页 20，翻页会让这一行恰好不在第一页上，
    // 而那看起来像「按钮没渲染」。
    await page.getByLabel('每页条数').selectOption('100');
    const row = page.locator('.card-body tbody tr', { hasText: target.student_no }).first();
    await expect(row).toBeVisible();
    // `case_id` 为空在界面上的可见形态就是这一格（不是空白、也不是某个状态码）。
    await expect(row).toContainText('未建档');

    await row.getByRole('button', { name: '查看测评记录' }).click();

    await expect(page).toHaveURL(/\/counselor\/students\/\d+\/records/);
    // 落到的**是这一名学生**的页面，不是一个刚好打开的页面。
    await expect(page.locator('h1')).toContainText(target.student_name);
    await expect(page.getByText('该生尚未建档（重点题未命中）')).toBeVisible();
    // 先证明有东西可看：他至少有一场已交卷的测评（接口那一条同款判据保证的）。
    await expect(page.locator('tbody tr').first()).toBeVisible();
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
    await startableTask(page).click();
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

    await startableTask(page).click();
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
    await startableTask(page).click();
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

    await startableTask(page).click();
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

    await startableTask(page).click();
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
    await startableTask(page).click();
    await page.waitForURL(/\/student\/assessment/);
    await page.waitForTimeout(400);

    const exit = page.getByRole('button', { name: '暂不继续，保存退出' });
    await expect(exit).toBeVisible();
    await exit.click();
    await page.waitForURL(/\/student\/home/);
  });
});

// ========== 学生端安心作答（P1，§5.14.2） ==========

/**
 * 一份**只活在内存里**的答题服务端（五个端点），供这一组用例驱动。
 *
 * 为什么必须打桩，而不是拿 `S001` 的真实会话：`playwright.config.ts` 是
 * `fullyParallel: true`，而演示库里 `S001` 只有一条 `IN_PROGRESS` 会话，它同时被
 * 「Student Assessment Flow」（`serial`）与别处的用例读写。任何「答了 2 题之后界面
 * 该长这样」的断言落在共享会话上，都会与别人此刻正在答的那几题抢——而那种红与功能
 * 无关（与「不要靠改测试让库里的数据好看」是同一条规矩的两面）。
 *
 * 打桩之后这一组用例的输入完全由它自己决定：题数、已有答案、以及服务端**收到过什么**
 * （`state.submitCalls`）。判据用**判定函数** / 正则而不是 `**\/api\/v1\/…` 那种通配
 * ——路径里带 id 的三个端点尤其要这样，否则 `/assessment-sessions` 会把
 * `/assessment-sessions/{id}/questions` 一起吃掉。
 *
 * `answered_count` 与 `current_question_no` 全部由 `state.answers` **现推**（服务端
 * 本来就是按题存的），于是「界面上的数」与「服务端手上的数」不可能各说各话。
 */
const STUB_TASK_ID = 9001
const STUB_SESSION_ID = 90011

interface StubCard {
  id: number
  name: string
  /** 这一场自己的状态（ACTIVE / NOT_STARTED / CLOSED…）：决定按钮点不点得动。 */
  status: string
  target_status: string
  answered_count?: number
  /** 省掉 = 本场题数（`questionCount`）；显式给 `null` = 「这个数还不知道」。 */
  question_count?: number | null
  end_at?: string | null
}

async function installAssessmentStubs(
  page: Page,
  options: {
    cards: StubCard[]
    questionCount?: number
    initialAnswers?: Record<string, 'YES' | 'NO'>
    /**
     * 扣住答案保存的答复不放，直到测试自己调 `state.releaseAnswers()`。
     *
     * 要证明的是「学生按下的那一刻屏幕上就生效」（§13），而那种断言**不能用会重试的
     * `expect`**：等往返的实现迟早在重试里等到那个数，于是它证明不了「立即」。
     * 判据只能是一次**同步读**，而同步读要成立，就必须保证读数时那个答复确实还没回来
     * ——用时长去保证是要靠运气的（Tab 走多久不由这条用例决定），所以这里用一道闸门。
     */
    holdAnswers?: boolean
  }
) {
  const questionCount = options.questionCount ?? 10
  const liveCardId = options.cards.some((card) => card.id === STUB_TASK_ID) ? STUB_TASK_ID : null
  const gate = { released: !options.holdAnswers, waiters: [] as Array<() => void> }
  const awaitGate = () =>
    gate.released ? Promise.resolve() : new Promise<void>((resolve) => gate.waiters.push(resolve))
  const state = {
    answers: { ...(options.initialAnswers ?? {}) } as Record<string, 'YES' | 'NO'>,
    /** 服务端一共收到过几次交卷：用来断言「没答满就没有提交出去」。 */
    submitCalls: 0,
    /** 放行被 `holdAnswers` 扣住的答复。没扣住时调它什么也不做。 */
    releaseAnswers: () => {
      gate.released = true
      gate.waiters.splice(0).forEach((resolve) => resolve())
    },
  }
  const envelope = (data: unknown) => ({ success: true, data, request_id: 'e2e', error: null })
  const questionNos = () => Array.from({ length: questionCount }, (_, index) => index + 1)
  const storedCount = () => questionNos().filter((no) => state.answers[String(no)]).length
  /** 服务端口径：第一道没答的题（答满了就是 null）。答题页的 `pickStart` 读它。 */
  const firstMissing = () => questionNos().find((no) => !state.answers[String(no)]) ?? null

  const sessionPayload = () => ({
    id: STUB_SESSION_ID,
    task_id: STUB_TASK_ID,
    student_id: 1,
    scale_id: 1,
    scale_version: 'MHT-1.1.0',
    status: storedCount() === questionCount ? 'SUBMITTED' : 'IN_PROGRESS',
    current_question_no: firstMissing(),
    answered_count: storedCount(),
    answers: { ...state.answers },
  })

  await page.route(
    (url) => url.pathname === '/api/v1/student/tasks',
    (route) =>
      route.fulfill({
        json: envelope({
          items: options.cards.map((card) => ({
            ...card,
            task_no: `E2E-${card.id}`,
            start_at: null,
            end_at: card.end_at ?? null,
            completed_at: null,
            question_count:
              card.question_count === undefined ? questionCount : card.question_count,
            // 「答题中」那张卡片的进度跟着服务端走：学生答一题，首页上那句
            // 「已答 N / M 题」就该跟着动（§11：同一个数只能有一个来源）。
            answered_count: card.id === liveCardId ? storedCount() : (card.answered_count ?? 0),
            session_id: card.id === liveCardId ? STUB_SESSION_ID : null,
          })),
        }),
      })
  )

  await page.route(
    (url) => url.pathname === '/api/v1/assessment-sessions',
    (route) => route.fulfill({ json: envelope(sessionPayload()) })
  )

  await page.route(
    (url) => /^\/api\/v1\/assessment-sessions\/\d+\/questions$/.test(url.pathname),
    (route) =>
      route.fulfill({
        json: envelope({
          items: questionNos().map((no) => ({
            question_no: no,
            question_text: `E2E 第 ${no} 题：最近我常觉得心里不踏实`,
          })),
        }),
      })
  )

  await page.route(
    (url) => /^\/api\/v1\/assessment-sessions\/\d+\/answers\/\d+$/.test(url.pathname),
    async (route) => {
      const no = route.request().url().split('/').pop() as string
      const body = route.request().postDataJSON() as { answer: 'YES' | 'NO' }
      // 收下这一题（服务端确实拿到手了），答复先扣着——见 `holdAnswers`。
      state.answers[no] = body.answer
      await awaitGate()
      await route.fulfill({ json: envelope(sessionPayload()) })
    }
  )

  await page.route(
    (url) => /^\/api\/v1\/assessment-sessions\/\d+\/submit$/.test(url.pathname),
    async (route) => {
      state.submitCalls += 1
      await route.fulfill({
        json: envelope({
          session_id: STUB_SESSION_ID,
          status: 'SUBMITTED',
          calculation_status: 'CALCULATED',
          calculation_error: null,
          submitted_at: '2026-09-26T10:00:00',
          tested_at: '2026-09-26T10:00:00',
          tested_at_source: 'ONLINE_SUBMIT',
          // 非 null：走的才是「成绩也算出来了」那一句 toast。另一句
          // （「成绩处理还需要老师再看一下」）是给评分失败的答卷的。
          result: { id: 1, status: 'CALCULATED' },
        }),
      })
    }
  )

  return state
}

/**
 * 375px 下的几何体检：横向溢出、帮助浮钮可达、以及**可点的主操作**有没有被盖住。
 *
 * 判据用 `document.elementFromPoint`（点在中心，落到谁身上）而不是「两个矩形相不相交」：
 * 相交是正常的——浮动按钮本来就压在内容上方那一层，真正要问的是点击会被谁接走。
 *
 * 两处**刻意的跳过**，各自都曾经造成过误报：
 *
 * - **`[disabled]` 控件不进来**：`elementFromPoint` 会跳过被禁用的表单控件、返回它的
 *   父元素，于是一个灰掉的「上一题」永远命中 `DIV.actions`。那是 `elementFromPoint`
 *   的性质，不是遮挡（第一版就是在这里报了一次假）。
 * - **视口外的元素不算**：没滚到它那里，它本来就不该被点到。
 */
async function narrowGeometry(page: Page) {
  return page.evaluate(() => {
    const hitAt = (el: Element) => {
      const rect = el.getBoundingClientRect()
      const cx = rect.x + rect.width / 2
      const cy = rect.y + rect.height / 2
      const onScreen = cy >= 0 && cy <= window.innerHeight && cx >= 0 && cx <= window.innerWidth
      const text = (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim().slice(0, 20)
      if (!onScreen) return { text, onScreen, covered: false, hitClass: '' }
      const hit = document.elementFromPoint(cx, cy)
      return {
        text,
        onScreen,
        covered: !(hit === el || el.contains(hit)),
        hitClass: hit ? String((hit as HTMLElement).className) : '(none)',
      }
    }
    const controls = Array.from(
      document.querySelectorAll(
        '.task-card button.primary, ' +
          '.question-foot .actions button:not([disabled]), ' +
          '.question-secondary button:not([disabled])'
      )
    )
    const fab = document.querySelector('.help-fab')
    const fabHit = fab ? hitAt(fab) : null
    return {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      controlCount: controls.length,
      fabPosition: fab ? getComputedStyle(fab).position : 'missing',
      fabOnScreen: fabHit?.onScreen ?? false,
      fabCovered: fabHit?.covered ?? false,
      controls: controls.map(hitAt),
    }
  })
}

/** 滚动取样点：0 与 1 就是顶部与底部，中间三点是为了扫过它们之间那些位置。 */
const NARROW_STOPS = [0, 0.25, 0.5, 0.75, 1] as const

/**
 * 375px 体检。四条判据，**第一条是根因、也是唯一真正挡住这一档的那一条**，
 * 其余三条是它的可见后果。
 *
 * 1. **帮助浮钮不在固定层**（`position` 是 `static` / `relative` / `sticky` 之一）。
 *    这不是「实现细节」：窄屏上 `fixed` 的元素**谁也不挤开**，于是「被底部导航盖住」
 *    与「盖住内容」二者必居其一——加 `padding` 只挪得动内容、挪不动它。
 *    所以「退出固定层」正是本条验收的要求本身，而不是达成它的某一种写法。
 *    两种固定写法都实测过、都被判掉（理由与两组量出来的坐标在 `styles.css` 那一段）。
 * 2. **它从没接走某个主操作的点击**——扫过整页取样。
 * 3. 任何位置都没有横向溢出。
 * 4. 滚到底时浮钮自己在屏幕上、并且点得到（它是文档流里的最后一行）。
 *
 * ★ **第 2 条抓不住「退回固定层」那一档，实测过，别把它读成第 1 条的替代品。**
 * 把 `.help-fab` 注回 `position: fixed; bottom: 80px`、**只关掉第 1 条**，这条用例
 * 仍然是**绿的**。原因是夹具的版式：stub 的题干只有一行，内容在底部导航之上就结束了，
 * 于是那个固定浮钮悬在「内容末尾」与「底栏上沿」之间那条**空隙**里，谁也没碰着。
 * 它盖住东西是有条件的——真实页面上题干长得多，`.question-foot` 才会落在浮钮那一行
 * （探针实测：`document.elementFromPoint` 在「下一题」的中心返回 `BUTTON.help-fab`，
 * `covered: true`）。
 *
 * 所以第 2 条的**准确说法**是「在这个夹具的版式下，浮钮有没有盖住主操作」。它有牙
 * （浮钮盒子变大、被写成 `width: 100%` 这类形状它会红），但它**证明不了**「没有隔着
 * 空隙的遮挡」——夹具的版式与真实页面不同。这是「先证明有东西可扫」的另一种形状：
 * 一条判据说自己守住了某件事，而夹具里那件事根本没机会出现。真正不依赖版式的是第 1 条，
 * 两条都在，各说各的。
 *
 * **只按「谁压着它」判，不按「压没压着」判**：手机上滚动中途有内容从固定底栏下面
 * 经过是正常的——`padding-bottom: 76px` 的意义正是「滚得到它」。把「被底栏压住」
 * 也算违规，一条题干只有一行的短页面上那两枚次级按钮就会红，而**浮钮根本没碰它们**
 * （第一版就是这么红的，报错里那句 `position=static` 说明浮钮根本不在固定层）。
 * 那是会为与浮钮无关的原因变红的守卫，而会无故变红的守卫很快会被人关掉。
 */
async function expectNarrowGeometry(page: Page, label: string) {
  const maxScroll = await page.evaluate(
    () => document.documentElement.scrollHeight - document.documentElement.clientHeight
  )
  let lastGeometry = await narrowGeometry(page)

  for (const stop of NARROW_STOPS) {
    await page.evaluate((y) => window.scrollTo(0, y), Math.round(maxScroll * stop))
    await page.waitForTimeout(120)
    lastGeometry = await narrowGeometry(page)
    const at = `${label} ${Math.round(stop * 100)}%`

    // 先证明这一屏真的有可点的主操作，否则下面那条是空转的。
    expect(lastGeometry.controlCount, `${at}：一个可点的主操作都没有`).toBeGreaterThan(0)
    expect(
      lastGeometry.scrollWidth,
      `${at}：横向溢出 ${lastGeometry.scrollWidth}/${lastGeometry.clientWidth}`
    ).toBe(lastGeometry.clientWidth)
    expect(
      lastGeometry.controls
        .filter((item) => item.onScreen && item.covered && item.hitClass.includes('help-fab'))
        .map((item) => item.text),
      `${at}：这些主操作被帮助浮钮接走了（浮钮 position=${lastGeometry.fabPosition}）`
    ).toEqual([])
  }

  // 根因那一半。允许 `relative` / `sticky`——它们同样在文档流里，也就同样盖不住东西。
  expect(
    ['static', 'relative', 'sticky'].includes(lastGeometry.fabPosition),
    `${label}：帮助浮钮在窄屏上仍处于固定层（position=${lastGeometry.fabPosition}），` +
      '那样它要么被底部导航盖住、要么盖住内容'
  ).toBe(true)
  expect(lastGeometry.fabOnScreen, `${label}：滚到底时帮助浮钮不在屏幕上`).toBe(true)
  expect(
    lastGeometry.fabCovered,
    `${label}：滚到底时帮助浮钮点不到（position=${lastGeometry.fabPosition}）`
  ).toBe(false)
}

test.describe('学生端安心作答（P1）', () => {
  test('任务卡的每一档状态都说清「能不能答、为什么」', async ({ page }) => {
    await installAssessmentStubs(page, {
      questionCount: 10,
      cards: [
        { id: 9101, name: 'E2E 新任务', status: 'ACTIVE', target_status: 'NOT_STARTED' },
        { id: 9102, name: 'E2E 答题中', status: 'ACTIVE', target_status: 'IN_PROGRESS', answered_count: 8 },
        { id: 9103, name: 'E2E 已完成', status: 'ACTIVE', target_status: 'COMPLETED', answered_count: 10 },
        { id: 9104, name: 'E2E 未开始', status: 'NOT_STARTED', target_status: 'NOT_STARTED' },
        { id: 9105, name: 'E2E 已结束', status: 'CLOSED', target_status: 'NOT_STARTED', end_at: '2026-08-31T23:59:00' },
        // 「已暂停」是**学校按下暂停键**，与「还没开始 / 已经结束」不是一回事：那两档
        // 由时钟推出来（§12），这一档只能由人写进那一列。它今天**没有写入方**
        // （`task_service.effective_task_status` 自己写着这句话），所以只能靠桩数据
        // 铺出来——而 `StudentHomePage.vue` 的 `blockedReason` 为它留着一句真话，
        // 那不覆盖就等于让一条已经写好的界面分支永远没人验过。
        { id: 9107, name: 'E2E 已暂停', status: 'PAUSED', target_status: 'NOT_STARTED' },
        // 「题数还不知道」是一种真实状态（`question_count` 按契约可空），
        // 而它与「题数为 0」在界面上必须长得不一样：分母不出现，预计时长也不出现。
        { id: 9106, name: 'E2E 题数未知', status: 'ACTIVE', target_status: 'IN_PROGRESS', answered_count: 3, question_count: null },
      ],
    })
    await loginAs(page, 'student')

    const card = (name: string) => page.locator('.task-card', { hasText: name })
    await expect(page.locator('.task-card')).toHaveCount(7)

    // 能答的两张（外加题数未知那张）才有主按钮；其余四张是灰的。
    await expect(page.locator('.task-card button.primary')).toHaveCount(3)
    await expect(page.locator('.task-card button[disabled]')).toHaveCount(4)

    // ① 新任务：还没开始，说得出还剩多少、要多少时间
    await expect(card('E2E 新任务')).toContainText('共 10 题，尚未开始')
    await expect(card('E2E 新任务').getByRole('button', { name: '开始作答' })).toBeEnabled()
    await expect(card('E2E 新任务')).toContainText(/预计约 \d+ 分钟/)

    // ② 答题中：进度是「已答 N / M 题」，按钮换成「继续作答」
    await expect(card('E2E 答题中')).toContainText('已答 8 / 10 题')
    await expect(card('E2E 答题中').getByRole('button', { name: '继续作答' })).toBeEnabled()
    await expect(card('E2E 答题中')).toContainText(/预计约 \d+ 分钟/)

    // ③ 已完成：按钮不可点，且**不再许诺时长**
    await expect(card('E2E 已完成')).toContainText('已完成全部 10 题')
    await expect(card('E2E 已完成').getByRole('button', { name: '已完成' })).toBeDisabled()
    await expect(card('E2E 已完成')).toContainText('你已经交过这一场')
    await expect(card('E2E 已完成')).not.toContainText('预计约')

    // ④ 未开始：灰按钮上的字与后端 `effective_task_status` 的口径同源（§12），
    //    并说得出什么时候开始。
    await expect(card('E2E 未开始').getByRole('button', { name: '未开始' })).toBeDisabled()
    await expect(card('E2E 未开始')).toContainText('这一场还没有开始。')
    await expect(card('E2E 未开始')).not.toContainText('预计约')

    // ⑤ 已结束：同样点不动，并说出截止时间——这一条与后端那道门逐字对齐：
    //    界面说已结束时 `create_or_get_session` 就真的开不了。
    await expect(card('E2E 已结束').getByRole('button', { name: '已结束' })).toBeDisabled()
    await expect(card('E2E 已结束')).toContainText('这一场已于')
    await expect(card('E2E 已结束')).not.toContainText('预计约')

    // ⑥ 题数未知：只报已答数，不编一个分母，也不编一个时长
    await expect(card('E2E 题数未知')).toContainText('已答 3 题')
    await expect(card('E2E 题数未知')).not.toContainText('共 10 题')
    await expect(card('E2E 题数未知')).not.toContainText('预计约')
    await expect(card('E2E 题数未知').getByRole('button', { name: '继续作答' })).toBeEnabled()

    // ⑦ 已暂停：与「还没开始 / 已经结束」同为灰按钮，但**说法不同**——那两档由时钟
    //    推出来（§12），这一档是学校按下的暂停键，所以它不承诺一个日期，只说等通知。
    //    这一档今天没有后端写入方（`task_service.effective_task_status` 写着这句话），
    //    于是它只在桩数据里可见——而 `blockedReason` 的这一支是真写在界面上的，
    //    没有这一块就等于让一条已写好的分支永远没人验过。
    await expect(card('E2E 已暂停').getByRole('button', { name: '已暂停' })).toBeDisabled()
    await expect(card('E2E 已暂停')).toContainText('这一场已由学校暂停，请等老师通知。')
    await expect(card('E2E 已暂停')).not.toContainText('预计约')
    await expect(card('E2E 已暂停')).not.toContainText('这一场还没有开始')
  })

  test('答一部分后保存退出，重新登录仍从原题继续，进度与答案都不丢', async ({ page }) => {
    await installAssessmentStubs(page, {
      questionCount: 3,
      cards: [{ id: STUB_TASK_ID, name: 'E2E 可作答', status: 'ACTIVE', target_status: 'NOT_STARTED' }],
    })
    await loginAs(page, 'student')
    // 「中途退出不会白做」这件事要先在点进去之前说，否则它答不上学生不开始作答的原因。
    await expect(page.getByText('作答时每选一题都会自动保存')).toBeVisible()

    await startableTask(page).click()
    await page.waitForURL(/\/student\/assessment\/9001/)

    const bar = page.locator('.assessment-bar')
    await expect(bar).toContainText('第 1 题')
    await expect(bar).toContainText('已完成 0/3')

    await page.getByRole('button', { name: '是', exact: true }).click()
    await expect(bar).toContainText('已完成 1/3')
    await page.getByRole('button', { name: '下一题' }).click()
    await page.getByRole('button', { name: '否', exact: true }).click()
    await expect(bar).toContainText('第 2 题')
    await expect(bar).toContainText('已完成 2/3')

    // 用键盘走到「暂不继续，保存退出」（验收里「键盘可完成保存退出」那一条）。
    await tabUntil(page, (el) => el.text.includes('暂不继续，保存退出'), '保存退出按钮')
    await page.keyboard.press('Enter')

    await page.waitForURL(/\/student\/home/)
    await expect(
      page.locator('.toast', { hasText: '已保存：答了 2/3 题，下次从这一题接着答' })
    ).toBeVisible()
    const card = page.locator('.task-card', { hasText: 'E2E 可作答' })
    await expect(card).toContainText('已答 2 / 3 题')
    await expect(card.getByRole('button', { name: '继续作答' })).toBeEnabled()

    // 重新登录一次（`loginAs` 会 `goto('/login')`，而登录页不会因为本地还留着 token
    // 就把人弹走）。光标键按会话 id 命名，**不在登出清理的范围内**——它记的是
    // 「这一场我上次看到第几题」，下一名学生读到也认不出自己那一场。
    await loginAs(page, 'student')
    await startableTask(page).click()
    await page.waitForURL(/\/student\/assessment\/9001/)
    await expect(bar).toContainText('第 2 题')
    await expect(bar).toContainText('已完成 2/3')
    // 答案本身也在：第 2 题答的是「否」。
    await expect(page.locator('.answer-grid button.selected')).toHaveText('否')
  })

  test('有漏答时不能误导为已完成：提交被拦下，并说清还差几题', async ({ page }) => {
    const state = await installAssessmentStubs(page, {
      questionCount: 4,
      initialAnswers: { 1: 'YES' },
      cards: [{ id: STUB_TASK_ID, name: 'E2E 有漏答', status: 'ACTIVE', target_status: 'IN_PROGRESS' }],
    })
    await loginAs(page, 'student')

    // 光标钉在第 4 题（最后一题，那里才有「提交测评」）。
    //
    // 「中间有一道题没答」这个形状**点不出来**：`next()` 要求当前题已作答（否则
    // 只给一句「请先选择一个答案」），`previous()` 只会往回走，而 `pickStart` 又优先
    // 取「第一道未答题」——所以一路点下来永远是连续的。这里的漏答是**服务端的事实**
    // （`save_answer` 本来就是按题存的），而这条用例要钉的正是那句提示里的数数的是
    // 「本版题库里没答的那些」，不是「当前位置之后的那些」：按后者算会说出「还有 0 题」，
    // 而学生手里明明有一道空着。
    await page.evaluate(() => localStorage.setItem('xlp_assessment_cursor:90011', '4'))
    await startableTask(page).click()
    await page.waitForURL(/\/student\/assessment\/9001/)

    const bar = page.locator('.assessment-bar')
    await expect(bar).toContainText('第 4 题')
    await expect(bar).toContainText('已完成 1/4')

    await page.getByRole('button', { name: '提交测评' }).click()

    await expect(page.getByText('还有 3 题没有作答，已定位到第一道未答题')).toBeVisible()
    await expect(bar).toContainText('第 2 题')
    // 定位到的是**他刚才站的位置之前**那一题——「按位置算」的实现会漏掉它。
    await expect(page.locator('.toast', { hasText: '已定位到第 2 题' })).toBeVisible()
    // 没答满就没有确认框、也没有提交出去。
    await expect(page.getByRole('dialog', { name: '确认提交测评' })).toHaveCount(0)
    expect(state.submitCalls).toBe(0)
  })

  test('只靠键盘能选答案、上一题、下一题、定位未答并确认提交', async ({ page }) => {
    const state = await installAssessmentStubs(page, {
      questionCount: 3,
      // 答完第 1 题之后，服务端**扣住答复不放**，直到下面显式放行——见那一处的注释。
      holdAnswers: true,
      cards: [{ id: STUB_TASK_ID, name: 'E2E 键盘', status: 'ACTIVE', target_status: 'NOT_STARTED' }],
    })
    await loginAs(page, 'student')
    await startableTask(page).click()
    await page.waitForURL(/\/student\/assessment\/9001/)

    const bar = page.locator('.assessment-bar')
    const press = async (text: string, label: string) => {
      await tabUntil(page, (el) => el.text === text, label)
      await page.keyboard.press('Enter')
    }

    await expect(bar).toContainText('第 1 题')

    // 「学生按下的那一刻，屏幕上就得是他选的那个」（§13：答错的代价由他自己承担）。
    //
    // 这一段**故意用同步读**（`await bar.innerText()`）而不是 `await expect(...).toContainText`：
    // 后者会重试到 5 秒，于是「等了一次服务端往返才更新」的实现也照样能绿——它证明的是
    // 「最后会对」，不是「按下就生效」。而同步读要成立，就得保证读数时答复确实还没回来；
    // 那件事不能靠「Tab 走得比网络快」去赌（Tab 花多久不由这条用例决定），所以用闸门。
    const firstAnswer = page.waitForRequest((r) => /\/assessment-sessions\/\d+\/answers\/1$/.test(r.url()))
    await press('是', '第 1 题的「是」')
    await firstAnswer // 服务端**收到**了，但答复还扣着
    expect(await bar.innerText()).toContain('已完成 1/3')
    expect(await page.locator('.answer-grid button.selected').innerText()).toBe('是')

    // 同一段窗口里的另一半：往返还在路上时，「下一题」必须走得动，而且不许反过来
    // 指责他「请先选择一个答案」——那是假的，他刚选完。
    await press('下一题', '「下一题」按钮')
    expect(await bar.innerText()).toContain('第 2 题')

    // 放行：从这里往后都是「服务端已经答过话」的普通状态。
    state.releaseAnswers()
    await expect(bar).toContainText('已完成 1/3')

    // 往回走：键盘不是只能往前。回到第 1 题，答案还在。
    await press('上一题', '「上一题」按钮')
    await expect(bar).toContainText('第 1 题')
    await expect(page.locator('.answer-grid button.selected')).toHaveText('是')

    // 走到底并把剩下的答完。
    await press('下一题', '「下一题」按钮')
    await press('是', '第 2 题的「是」')
    await press('下一题', '「下一题」按钮')
    await press('是', '第 3 题的「是」')
    await expect(bar).toContainText('已完成 3/3')

    // 「定位未答」的**另一支**：一道都不缺时不把人带到别处，而是明说可以提交。
    await press('定位未答', '「定位未答」按钮')
    await expect(page.locator('.toast', { hasText: '全部题目已完成，可以提交' })).toBeVisible()

    await press('提交测评', '「提交测评」按钮')
    const dialog = page.getByRole('dialog', { name: '确认提交测评' })
    await expect(dialog).toBeVisible()
    // 这句话是学生最后一次核对自己的机会：答了几题、还差几题、交了不能改。
    await expect(dialog).toContainText('已完成 3/3 题，未答 0 题。提交后不能自行修改答案。')

    await press('确认提交', '「确认提交」按钮')
    await page.waitForURL(/\/student\/home/)
    expect(state.submitCalls).toBe(1)
  })

  test('三个学生页面共用同一个求助入口', async ({ page }) => {
    await installAssessmentStubs(page, {
      questionCount: 3,
      cards: [{ id: STUB_TASK_ID, name: 'E2E 求助入口', status: 'ACTIVE', target_status: 'NOT_STARTED' }],
    })
    await loginAs(page, 'student')

    // 三处必须是**同一个**弹层：正文与紧急提示逐字相同。写成三份复制时，同一句话
    // 会在学生手上出现两个版本，而「三处一致」这件事没有任何东西保证。
    const openHelp = async (where: string) => {
      const trigger = page.getByRole('button', { name: '我想找人聊聊' })
      await expect(trigger, `${where}：求助入口不见了`).toBeVisible()
      await trigger.click()
      const dialog = page.getByRole('dialog', { name: '我想找人聊聊' })
      await expect(dialog, `${where}：弹层没打开`).toBeVisible()
      await expect(dialog).toContainText('可以随时停下来')
      await expect(dialog).toContainText('这不是一件需要独自扛着的事')
      await dialog.getByRole('button', { name: '关闭' }).click()
      await expect(dialog).toHaveCount(0)
    }

    await page.waitForSelector('.help-fab')
    await openHelp('我的测评')

    await startableTask(page).click()
    await page.waitForURL(/\/student\/assessment\/9001/)
    await page.waitForSelector('.help-fab')
    await openHelp('在线答题')

    await page.goto('/student/history')
    await page.waitForSelector('.help-fab')
    await openHelp('完成记录')
  })

  test.describe('窄屏 375px', () => {
    test.use({ viewport: { width: 375, height: 667 } })

    test('主操作不被帮助浮钮遮挡，题目与按钮也没有横向溢出', async ({ page }) => {
      await installAssessmentStubs(page, {
        questionCount: 3,
        cards: [
          { id: STUB_TASK_ID, name: 'E2E 窄屏可作答', status: 'ACTIVE', target_status: 'NOT_STARTED' },
          { id: 9202, name: 'E2E 窄屏已完成', status: 'ACTIVE', target_status: 'COMPLETED', answered_count: 3 },
        ],
      })
      await loginAs(page, 'student')
      await page.waitForSelector('.help-fab')
      await expectNarrowGeometry(page, '我的测评')

      await startableTask(page).click()
      await page.waitForURL(/\/student\/assessment\/9001/)
      await page.waitForSelector('.question-text')
      // 一屏里同时有题干、是 / 否、上一题 / 下一题、定位未答 / 保存退出——最容易
      // 出事的那一屏就是它。
      await expectNarrowGeometry(page, '在线答题')
    })
  })
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
    await page.goto('/admin/system');
    // 「重置密码」2026-09-27 收进统一操作菜单（V2.0.0 §5.14.5）：行里只剩一枚「操作」，
    // 所以先开菜单再点它。两处都按名字定位，不用 `.first()` / `.last()`——弹层的视觉
    // 次序按**打开**次序算（§22），而 DOM 次序是另一回事，按它取元素迟早指错一层。
    await page
      .locator('table', { hasText: '密码状态' })
      .locator('tbody tr')
      .first()
      .getByRole('button', { name: '操作' })
      .click();
    await page
      .getByRole('dialog', { name: /账号操作/ })
      .getByRole('button', { name: '重置密码' })
      .click();
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
          // `case_version` 是乐观锁（§16.4），**从列表行上现取**、不写死：它每被
          // 改一次就 +1（复核、跟进、关档、重开都会），写死一个数会在某一天静默失效。
          // 少了它服务端不认这一次重开，而这里没有断言它的返回值——前置于是变成
          // 一次「赌这条档案此刻的状态」，下面那句「关闭按钮应该在」会红在一个
          // 与关闭流程无关的地方。
          body: JSON.stringify({
            reason: '回归测试前置：重新打开以验证关闭确认',
            case_version: closed.case_version
          })
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
    // `.first()` 不是随手挑一个。这一页上「当前阶段」**有意出现两处**（首屏摘要头一格、
    // 「本次测评事实」卡一格），两处渲染的是同一个 `statusLabel(case_status)` 药丸——
    // 于是 strict mode 下 `toBeVisible()` 会以「命中 2 个」报错：**两处都对，反而红**。
    // 上面那句注释才是这条用例要说的事（「这一页显示了已关闭」），至少一处可见就成立；
    // 「恰好一个」从来不是它的判据，只是那个定位器写法顺带的假设。
    // 与 §24 那条同源：`.task-card').first()` 假设「第一张卡就是能点的那张」，
    // 换成按意图定位才是那些用例真正要说的事。
    await expect(page.locator('.pill', { hasText: '已关闭' }).first()).toBeVisible();
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
    // 桩要打在**页面真的会调的那个端点**上。工作台那一格 2026-09-22 从独立的
    // `/analytics/dimensions` 换成 `/analytics/report`（与五个报表页共享同一数据源，
    // 见 `CounselorWorkbenchPage.loadPanels`），而这个桩当时没跟着走——于是那一次
    // 没有任何请求会失败，面板正常渲染出内容，断言红在「找不到那句错误文案」上。
    // 这条与 §测试注意那条「写死的定位器会红在一个与功能无关的地方」同源：
    // 换了端点，桩、断言与页面三处必须一起走。
    await failApiPaths(page, {
      '/api/v1/analytics/report': '维度聚合暂时不可用',
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
      '/api/v1/analytics/report': '总览暂时不可用',
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

// ---------------------------------------------------------------------------
// V2.0.0 §5.14.3 心理老师行动优先（COUNSELOR-UX）
//
// 这一组守的是**一处口径的四个消费者**：工作台的五张指标卡、工作台的优先队列、
// 重点学生页的三档负责人页签、以及点卡片之后的落点。它们必须说同一件事——
// 「卡上写 3、点进去列表里 0 条」是这一族里最容易出现、也最难发现的一种错
// （CLAUDE.md §11：指标卡上的数必须与它点进去的那个列表同源），而它**不会让
// 任何一条既有用例变红**：后端发的是对的，两个页面各自也是对的，错的只是它们
// 之间的关系。所以下面几条**按两处互相比来断**，而不是各自断一个死数。
//
// 三条写法上的讲究，与这一组的每条断言都有关：
//
// 1. **一个数都不写死**。演示库里的档案数、谁名下有几份，都会随任何一次
//    `make seed-demo` / 人工操作而变——写死会让用例某一天红在一个与功能无关的
//    地方（CLAUDE.md 测试注意：「数行数要问接口要，不要写死」）。所以下面的期望值
//    一律**从页签自己印出来的那个数**读回来，再去比列表。
// 2. **先证明有东西可扫**。「三档都是 0 条」时每一条一致性断言都成立，而它什么都没
//    证明——CLAUDE.md 测试注意里那条已经栽过五次。所以每组前面都有一条
//    `toBeGreaterThan(0)`。
// 3. **不碰共享数据**。这一组全部是只读的；唯一需要「一份已关闭的档案」的那一条
//    走 `page.route` 桩，不用界面去真关一份（那条路会与 `缺陷回归` 里那条关闭用例
//    抢同一条数据，见那一条自己的注释）。
// ---------------------------------------------------------------------------
test.describe('心理老师工作台的行动优先', () => {
  /** 心理老师的 API 会话。浏览器那一路另走 `loginAs`，两条互不影响。 */
  async function counselorHeaders(page: Page) {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    await expectOk(login, '这一条失去了取数的手段：POST /auth/login');
    return { Authorization: `Bearer ${(await login.json()).data.access_token}` };
  }

  /** 页签上印着的那个数（`全部（12）` → `12`）。 */
  function countOnLabel(text: string, what: string): number {
    const matched = text.match(/（(\d+)）/);
    expect(matched, `${what}的数量没有印在页签上：「${text}」`).not.toBeNull();
    return Number(matched![1]);
  }

  /**
   * 档案抽屉里那六枚工作入口并不等价：四枚各**加一条记录**、一枚把档案推回流程
   * （重新打开）、只有「关闭档案」动的是档案的**存续**。此前六枚共用
   * `.detail-actions button` 那一套白底灰边，于是「关闭档案」与「记录人工复核」
   * 长得一模一样（V2.0.0 §5.14.6 第 5 条：危险操作分离并确认）。
   *
   * 判据取**计算值**而不是类名——与「布局完整性」那一组同一个道理：类名写对了而
   * 颜色被别处的规则盖掉时，屏幕上照样看不出来，类名断言也照样是绿的。
   * （这里确实有第二个人声明颜色：`.detail-actions button` 是 (0,1,1) 而
   * `.btn.danger` 是 (0,2,0)——**加对了类名仍然可能不生效**，所以必须量。）
   */
  test('关闭档案与其余五枚工作入口长得不一样，且动手前先问一句', async ({ page }) => {
    await loginAs(page, 'counselor');

    // 队列是「未关闭 + 四档判据之一」，所以第一行必然有「关闭档案」而不是
    // 「重新打开」。先证明它非空——空队列上下面每一条断言都无从谈起。
    //
    // 用 `toBeVisible()` 而不是 `count()`：后者**立即求值**，而页面的 `cases`
    // 这一刻还在请求中，于是它数到 0 —— 那条断言会红在「队列是空的」上，而
    // 队列并不空，只是还没到。同一页上方那条「全部学生」用例也是这么写的
    // （先 `first()` 可见，再数行数）。
    const rows = page.locator('.queue-row');
    await expect(rows.first(), '优先队列是空的，这条用例会退化成空转').toBeVisible();
    await rows.first().click();

    const bar = page.locator('.detail-actions');
    await expect(bar).toBeVisible();

    const danger = bar.getByRole('button', { name: '关闭档案' });
    const normal = bar.getByRole('button', { name: '记录人工复核' });
    await expect(danger).toBeVisible();
    await expect(normal).toBeVisible();

    const colorOf = (locator: typeof danger) =>
      locator.evaluate(el => getComputedStyle(el).color);
    const [dangerColor, normalColor] = await Promise.all([colorOf(danger), colorOf(normal)]);
    expect(
      dangerColor,
      `关闭档案与其余工作入口同色（${dangerColor}），危险操作没有分离出来`,
    ).not.toBe(normalColor);

    // 只断「不同」还不够：把其余五枚涂红也满足它。要的是**危险的那一枚**是红的那一支。
    const [r, g, b] = dangerColor.match(/\d+/g)!.map(Number);
    expect(r, `关闭档案的颜色 ${dangerColor} 不是红色系`).toBeGreaterThan(g + 30);
    expect(r, `关闭档案的颜色 ${dangerColor} 不是红色系`).toBeGreaterThan(b + 30);

    // 「分离」与「确认」是两半。关档那一步的确认本来就有（`closeCase` 里的
    // `showConfirmation`），这一条钉住它不许被摘掉——取消之后弹层关掉、什么都不写。
    await danger.click();
    const confirm = page.getByRole('dialog', { name: '关闭关注档案' });
    await expect(confirm).toBeVisible();
    await expect(confirm).toContainText('关闭不会删除历史记录');
    await confirm.getByRole('button', { name: '取消' }).click();
    await expect(confirm).toHaveCount(0);
  });

  test('负责人快捷筛选：条数、列表与空态说的是同一件事', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/cases');

    const tabs = page.locator('.owner-tab');
    await expect(tabs).toHaveCount(3);

    const counts: number[] = [];
    for (let index = 0; index < 3; index += 1) {
      counts.push(countOnLabel((await tabs.nth(index).innerText()).trim(), `第 ${index + 1} 档`));
    }
    // 先证明这一屏有东西可筛。三档全 0 时下面每一条一致性断言都成立，而它什么都没证明。
    expect(counts[0], '演示数据里一份档案都没有，这条用例会退化成空转').toBeGreaterThan(0);

    const counter = page.locator('.toolbar .row-select span');
    const bodyRows = page.locator('.table-wrap tbody tr');
    const pager = page.locator('.table-pager');
    const ownerCells = page.locator('.table-wrap tbody tr td:nth-child(4)');

    for (let index = 0; index < 3; index += 1) {
      await tabs.nth(index).click();
      await expect(tabs.nth(index)).toHaveClass(/active/);

      // 工具栏上的人数是**筛完之后**的人数，与页签上印的那个数同源
      // （`ownerCounts` 与 `filtered` 共用 `matchesOwner`）。用整串匹配而不是
      // `toContainText`：`1 人` 是 `21 人` 的子串，包含匹配会把 21 读成 1。
      await expect(counter).toHaveText(new RegExp(`^${counts[index]} 人 · 已选 \\d+ 份档案$`));

      if (counts[index] > 0) {
        await expect(pager).toContainText(new RegExp(`共 ${counts[index]} 条`));
        // 每页 20 条：数量说的是筛完的总数，表里渲染的是当前这一页——两者相等
        // 只在演示库不到 20 份时成立，所以取 `min` 而不是直接比。
        await expect(bodyRows).toHaveCount(Math.min(counts[index], 20));
      } else {
        await expect(page.locator('.table-wrap .empty')).toHaveText('没有符合条件的学生');
        // 空态与分页器互斥：0 条时不该还留着一个「共 0 条 · 第 1 / 0 页」。
        await expect(pager).toHaveCount(0);
      }

      // 条数对上了不等于筛对了——一个恒返回 `true` 的 `matchesOwner` 也能让上面
      // 每一句成立（三档都等于「全部」）。所以这两档还要看**行里的字**：
      // 「未分配」那一列是这个筛选器唯一看得见的结果。
      const owners = await ownerCells.evaluateAll((cells) =>
        cells.map((cell) => (cell.textContent ?? '').trim())
      );
      if (index === 1) {
        expect(
          owners.filter((owner) => owner === '未分配'),
          '「我负责的」这一档里混进了没有负责人的档案'
        ).toHaveLength(0);
      }
      if (index === 2) {
        expect(
          owners.every((owner) => owner === '未分配'),
          `「未分配」这一档里混进了有负责人的档案：${JSON.stringify(owners)}`
        ).toBe(true);
      }
    }
  });

  test('工作台首屏就看得到逾期与今天的入口，点进去落到已筛选的列表', async ({ page }) => {
    await loginAs(page, 'counselor');

    const viewport = page.viewportSize();
    expect(viewport, '这条用例要靠 1280×720 这个视口判断「首屏」，量不到它就没法判').not.toBeNull();

    // §5.14.3 验收：「有逾期时首屏无需滚动即可看到逾期数量及入口」。两档一起断——
    // 只断逾期那一张的话，一个把「今天必须处理」排到第二屏的排版照样是绿的。
    for (const [label, filter, tabLabel] of [
      ['逾期跟进', 'overdue', '已逾期'],
      ['今天必须处理', 'today', '今日待办'],
    ] as const) {
      await page.goto('/counselor/workbench');
      const card = page.locator('article.metric', { hasText: label });
      await expect(card).toBeVisible();

      const box = await card.boundingBox();
      expect(box, `指标卡「${label}」量不出位置`).not.toBeNull();
      expect(box!.y, `指标卡「${label}」在视口上方（y = ${box!.y}）`).toBeGreaterThanOrEqual(0);
      expect(
        box!.y + box!.height,
        `指标卡「${label}」落到了首屏之外（y = ${box!.y}、高 ${box!.height}、视口 ${viewport!.height}）`
      ).toBeLessThanOrEqual(viewport!.height);

      // 卡上那个数，点进去之前先读回来——下面要拿它跟列表对。
      const shown = Number((await card.locator('.metric-value').innerText()).trim());
      expect(Number.isFinite(shown), `指标卡「${label}」上的数读不出来`).toBe(true);

      // 入口可见不等于点了有用：卡上的数必须是**点进去那个列表**按同一档筛出来的。
      // `goCases` 会把当前那一档负责人也带过去，所以只断言 filter 这一个键。
      await card.click();
      await expect(page).toHaveURL(new RegExp(`[?&]filter=${filter}`));
      await expect(page.locator('.queue-tab.active')).toHaveText(tabLabel);

      // ★ 这一条才是「同一筛选口径」那句话的判据（§5.14.3 第 7 条、CLAUDE.md §11）。
      // 只断「地址栏里有 filter=overdue」+「页签写着已逾期」的话，一个把指标卡读
      // **另一个字段**的实现照样是绿的——而那正是这一页历史上真实出过的错
      // （`overdueCount` 从前读 `metrics.following`，卡上说 10、点进去 0 条）。
      // 所以两处**互相比**：卡上的数 == 列表分页器上那个数。
      if (shown > 0) {
        await expect(
          page.locator('.table-pager'),
          `指标卡「${label}」写着 ${shown}，点进去的列表却不是 ${shown} 条`
        ).toContainText(`共 ${shown} 条`);
      } else {
        // 0 条时两处也要一致：分页器整块不出现（0 条不该留一个「共 0 条 · 第 1 / 0 页」），
        // 留下的是一句空态。
        await expect(page.locator('.table-pager')).toHaveCount(0);
        await expect(page.locator('.table-wrap .empty')).toBeVisible();
      }
    }

    // 再钉一次「这一屏真的有东西可对」：两档都是 0 时上面每一条都成立，而它什么都没证明。
    // 演示数据里逾期与今日各按构造产出（`seed_demo` 的跟进日期落在过去与今天），
    // 所以这一条红了要先去看数据，不是先改断言。
    const overdueCard = page.locator('article.metric', { hasText: '逾期跟进' });
    await page.goto('/counselor/workbench');
    expect(
      Number((await overdueCard.locator('.metric-value').innerText()).trim()),
      '演示数据里没有一份逾期的关注档案，这条对账用例会退化成空转——先跑 make seed-demo'
    ).toBeGreaterThan(0);
  });

  test('首屏与优先队列都按「今天 → 逾期 → 待复核 → 其他」排', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/workbench');

    // 第一半：三张卡片的**阅读次序**。指标区是一个多列的 CSS grid，所以「谁在前」
    // 不等于「谁的 y 更小」——同一行的两张卡 y 完全相等（实测 1280px 下这两张都是
    // y=245.1875）。按 (y, x) 排序才是这个网格真正的阅读次序，而顺序正是这一条
    // 要求的东西：一个把「今天必须处理」排到最后的排版照样让每一张卡都「可见」。
    const labels = ['今天必须处理', '逾期跟进', '待人工复核'];
    const placed: Array<{ label: string; y: number; x: number }> = [];
    for (const label of labels) {
      const card = page.locator('article.metric', { hasText: label });
      await expect(card).toBeVisible();
      const box = await card.boundingBox();
      expect(box, `指标卡「${label}」量不出位置`).not.toBeNull();
      placed.push({ label, y: box!.y, x: box!.x });
    }
    const readingOrder = [...placed]
      .sort((a, b) => a.y - b.y || a.x - b.x)
      .map((one) => one.label);
    expect(
      readingOrder,
      `指标卡的阅读次序是 ${readingOrder.join(' → ')}，不是 ${labels.join(' → ')}`
    ).toEqual(labels);

    // 第二半：队列的行序。阶段药丸与「下次处理」那一格足以把 `priorityRank` 那四档
    // **重推一遍**，所以这里从 DOM 现推一次、断言这个序列不递减——「今天与逾期的
    // 工作排在最前面」这句话这才是一条判据，而不是组件自己的一句声称。
    const today = await page.evaluate(() => {
      const d = new Date();
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    });
    const rows = page.locator('.queue-scroll tbody tr.queue-row');
    await expect(rows.first()).toBeVisible();
    const ranks = await rows.evaluateAll(
      (trs, todayText) =>
        trs.map((tr) => {
          const status = (tr.querySelector('td:nth-child(2) .pill')?.textContent ?? '').trim();
          const due = (tr.querySelector('td:nth-child(5)')?.textContent ?? '').trim();
          if (due === '已逾期') return 1;
          if (due && due !== '—' && due <= todayText) return 0;
          if (status === '待复核') return 2;
          return 3;
        }),
      today
    );
    expect(ranks.length, '优先队列是空的，这条用例会退化成空转').toBeGreaterThan(0);
    expect(
      ranks[0],
      `队列第一行不是「今天 / 逾期」那一档（重推出来是第 ${ranks[0]} 档）`
    ).toBeLessThanOrEqual(1);
    for (let i = 1; i < ranks.length; i += 1) {
      expect(
        ranks[i],
        `第 ${i + 1} 行（第 ${ranks[i]} 档）排在了上一行（第 ${ranks[i - 1]} 档）前面`
      ).toBeGreaterThanOrEqual(ranks[i - 1]);
    }
  });

  test('工作台队列的行操作按当前阶段给出具体动词', async ({ page }) => {
    await loginAs(page, 'counselor');

    const rows = page.locator('.queue-scroll tbody tr.queue-row');
    await expect(rows.first()).toBeVisible();

    // 一次读回（阶段药丸 / 操作按钮）两个字段：分两次读的话，两次之间队列可能
    // 因为一次后台刷新而变过，配对就错位了。
    const pairs = await rows.evaluateAll((trs) =>
      trs.map((tr) => ({
        status: (tr.querySelector('td:nth-child(2) .pill')?.textContent ?? '').trim(),
        verb: (tr.querySelector('td:last-child button')?.textContent ?? '').trim(),
      }))
    );
    expect(pairs.length, '优先队列是空的，这条用例会退化成空转').toBeGreaterThan(0);

    // 判据是**阶段 → 动词**这个映射，不是「按钮上有字」：一个所有行都写
    // 「进入档案」的实现要在这里红（§5.14.3 验收：「不能只换文案不带筛选/上下文」）。
    const expectedVerb = (status: string) =>
      status === '待复核' ? '人工复核' : status === '跟进中' ? '记录跟进' : '查看档案';
    for (const row of pairs) {
      expect(row.verb, `「${row.status}」这一行的操作写的是「${row.verb}」`).toBe(
        expectedVerb(row.status)
      );
    }

    // 再断动词**真的落到那个动作上**：点了之后弹出来的是那一张表单，而不是
    // 一个「请自己去找」的档案页。优先挑「记录跟进」——它是唯一一个不依赖
    // 这名学生有没有待复核信号的（`saveReview` 在一条风险事件都没有时才放弃）。
    let target = pairs.findIndex((row) => row.verb === '记录跟进');
    let heading = '新增跟进记录';
    if (target < 0) {
      target = pairs.findIndex((row) => row.verb === '人工复核');
      heading = '记录人工复核';
    }
    expect(
      target,
      `优先队列里没有一条需要动手的行，这条用例会退化成空转：${JSON.stringify(pairs)}`
    ).toBeGreaterThanOrEqual(0);

    await rows.nth(target).locator('button').click();
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  });

  test('工作台三档负责人筛选与队列行数一致，且真的筛掉了别人', async ({ page }) => {
    await loginAs(page, 'counselor');

    const tabs = page.locator('.owner-tabs .owner-tab');
    await expect(tabs).toHaveCount(3);

    const counts: number[] = [];
    for (let index = 0; index < 3; index += 1) {
      counts.push(countOnLabel((await tabs.nth(index).innerText()).trim(), `第 ${index + 1} 档`));
    }
    expect(counts[0], '优先队列是空的，这条用例会退化成空转').toBeGreaterThan(0);

    const rows = page.locator('.queue-scroll tbody tr.queue-row');
    for (let index = 0; index < 3; index += 1) {
      await tabs.nth(index).click();
      await expect(tabs.nth(index)).toHaveClass(/active/);
      // 页签上的数与切过去之后表里的行数是**同一个函数调用**（`ownerCounts` 与
      // `priorityQueue` 共用 `rankedQueue`），所以这里断相等是断「它没被绕开」。
      await expect(rows).toHaveCount(counts[index]);

      const owners = await rows.evaluateAll((trs) =>
        trs.map((tr) => (tr.querySelector('td:nth-child(4)')?.textContent ?? '').trim())
      );
      if (index === 1) {
        expect(
          owners.filter((owner) => owner === '未分配'),
          '「我负责的」这一档里混进了没有负责人的行'
        ).toHaveLength(0);
      }
      if (index === 2) {
        expect(
          owners.every((owner) => owner === '未分配'),
          `「未分配」这一档里混进了有负责人的行：${JSON.stringify(owners)}`
        ).toBe(true);
      }
    }
  });

  test('档案详情的下一步操作区吸附在顶栏下，且不含关闭档案', async ({ page }) => {
    await loginAs(page, 'counselor');
    const headers = await counselorHeaders(page);
    const listed = await page.request.get('/api/v1/care-cases', { headers });
    await expectOk(listed, '这一条失去了取数的手段：GET /care-cases');
    const cases = (await listed.json()).data.items as Array<{
      student_id: number;
      case_status: string;
    }>;
    const open = cases.find((item) => item.case_status !== 'CLOSED');
    expect(
      open,
      `重点学生列表里没有一条在办档案（共 ${cases.length} 条），这条用例会退化成空转`
    ).toBeTruthy();

    await page.goto(`/counselor/cases/${open!.student_id}`);
    const bar = page.locator('.case-next-actions');
    await expect(bar).toBeVisible();

    // 「保持可见」是靠 sticky 做到的，所以判据取的是**计算值**而不是文案
    // （与「布局完整性」那一组同一条：文案断言看不见整块样式被丢掉）。
    // 视口是 1280×720，780px 那条媒体查询不生效，所以这里必须是 sticky。
    expect(
      await bar.evaluate((el) => getComputedStyle(el).position),
      '下一步操作区不再吸附在顶栏下面（切到第七个页签就找不到了）'
    ).toBe('sticky');

    for (const name of ['人工复核', '记录跟进', '家庭回访', '安排复测']) {
      await expect(bar.getByRole('button', { name, exact: true })).toBeEnabled();
    }

    // 「关闭关注档案」是终止性动作，与这四项分开放：它在页头、走危险操作确认。
    // 两处都断——只断「页头有」的话，一个把关闭按钮同时抄进操作区的实现照样是绿的，
    // 而那正是这一条要求挡住的（日常登记与终止动作摆一起，早晚被误点）。
    await expect(bar.getByRole('button', { name: /关闭关注档案|重新打开档案/ })).toHaveCount(0);
    await expect(
      page.locator('.page-head').getByRole('button', { name: /^(关闭关注档案|重新打开档案)$/ })
    ).toHaveCount(1);
  });

  test('已关闭的档案把四项登记置灰，并指出重新打开的出路', async ({ page }) => {
    await loginAs(page, 'counselor');
    const headers = await counselorHeaders(page);
    const listed = await page.request.get('/api/v1/care-cases', { headers });
    await expectOk(listed, '这一条失去了取数的手段：GET /care-cases');
    const cases = (await listed.json()).data.items as Array<{ student_id: number }>;
    expect(cases.length, '重点学生列表是空的，这条用例会退化成空转').toBeGreaterThan(0);
    const studentId = cases[0].student_id;

    // 取一份**真实**的详情，只把 `case_status` 换成 CLOSED 再发回浏览器。
    //
    // 为什么不用界面真去关一份：那要改共享演示库里的数据，而「缺陷回归」组里那条
    // 关闭用例（`closing a case asks for the review it asserts`）正在同一批档案上
    // 跑，`fullyParallel` 下两条会互相把对方的前置撬掉——它的第一句就是把库里那条
    // CLOSED 档案先打开回去。桩比真关一份更准（要断的正是「服务端说是 CLOSED 时
    // 界面怎么表现」），也不会留下痕迹。
    await page.route(
      (url) => url.pathname === `/api/v1/care-cases/${studentId}`,
      async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        body.data.case_status = 'CLOSED';
        await route.fulfill({ response, json: body });
      }
    );

    await page.goto(`/counselor/cases/${studentId}`);
    const bar = page.locator('.case-next-actions');
    await expect(bar).toBeVisible();

    // 四项都要置灰：`care_service._ensure_open` 对这四个入口一律回 409（CLAUDE.md §28），
    // 界面这一层做的是**把那道门写在按钮上**，而不是让用户点进去吃一个错误。
    // 四个名字逐个断，不写成「至少一个」——一个只置灰了复核的实现在后者下是绿的。
    for (const name of ['人工复核', '记录跟进', '家庭回访', '安排复测']) {
      await expect(bar.getByRole('button', { name, exact: true })).toBeDisabled();
    }

    // 灰掉的按钮必须说得出为什么（§17：空态是一句关于数据的话）。
    const note = page.locator('#case-next-closed-note');
    await expect(note).toBeVisible();
    await expect(note).toContainText('重新打开档案');
    // 那一句是**关联**在那组按钮上的（`aria-describedby`），不是飘在旁边的另一段字：
    // 读屏软件走到第一个灰按钮时要能听见它。
    await expect(bar.locator('[role="group"]')).toHaveAttribute(
      'aria-describedby',
      'case-next-closed-note'
    );

    // 出路必须还在页头，而且是能点的——「四项都灰了」不该等于「这一页没有出口」。
    await expect(
      page.locator('.page-head').getByRole('button', { name: '重新打开档案' })
    ).toBeEnabled();
  });

  // ===== §5.18 心理老师连续工作体验：三条断点各一条守卫 =====
  //
  // 三条断的都是「两屏说的是不是同一件事」，所以判据一律**从界面上现取**、
  // 一个数字都不写死（演示库是共享的，写死会在某一天红在一个与功能无关的地方）。

  test('从队列切档进档案再返回，切过的状态档与地址栏都还在', async ({ page }) => {
    await loginAs(page, 'counselor');
    // 起点必须与目标不同：默认档是「今日待办」（`filter=today`），而这条要断的是
    // 「**我切过的那一档**还在不在」。
    await page.goto('/counselor/cases?filter=today');

    const tabs = page.locator('button.queue-tab');
    await expect(tabs).toHaveCount(7);
    await expect(page.locator('.queue-tab.active')).toContainText('今日待办');
    const startLabel = (await page.locator('.queue-tab.active').innerText()).trim();
    const startFilter = new URL(page.url()).searchParams.get('filter');

    // 挑一个**非空、且不是「全部」也不是起点**的档：
    //  · 空的档点进去没有行可点，用例会停在半路；
    //  · 「全部」是默认档，而默认档不往地址栏里塞参数（`setQueueFilter('all')`
    //    会把 `filter` 删掉）；
    //  · **起点那一档必须跳过**：点它等于没切，而这条用例断的正是「切过之后」。
    //    （少了这一句，整条用例会退化成空转——变异验证时实测到过。）
    const labels = (await tabs.allInnerTexts()).map(text => text.trim());
    let pickedLabel: string | null = null;
    for (let i = 0; i < labels.length; i += 1) {
      if (labels[i].startsWith('全部') || labels[i] === startLabel) continue;
      await tabs.nth(i).click();
      await expect(page.locator('.queue-tab.active')).toContainText(labels[i]);
      if ((await page.locator('tbody tr').count()) > 0) {
        pickedLabel = labels[i];
        break;
      }
    }
    expect(pickedLabel, '没有一个非空的状态档，这条用例会退化成空转').not.toBeNull();

    // 切档必须写进地址栏。此前它是一次裸赋值（只改内存里那个 ref），于是
    // 进详情再返回时那一档就没了——而浏览器 Back 与刷新同样回不到它。
    const filterKey = new URL(page.url()).searchParams.get('filter');
    expect(
      filterKey,
      '切了状态档，地址栏里的 filter 却还是原来那个：这个档分享不出去，返回时也回不到它'
    ).not.toBe(startFilter);

    await page.locator('tbody tr').first().getByRole('button', { name: '查看档案' }).click();
    await expect(page).toHaveURL(/\/counselor\/cases\/\d+$/);

    await page.getByRole('button', { name: '返回列表' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('filter')).toBe(filterKey);
    await expect(page.locator('.queue-tab.active')).toContainText(pickedLabel!);
  });

  test('直接打开档案详情或测评记录的地址，点「返回列表」不会离开应用', async ({ page }) => {
    await loginAs(page, 'counselor');
    const headers = await counselorHeaders(page);
    const listed = await page.request.get('/api/v1/care-cases?limit=1', { headers });
    await expectOk(listed, '这一条失去了取数的手段：GET /care-cases');
    const items = (await listed.json()).data.items as Array<{ student_id: number }>;
    expect(items.length, '一条档案都没有，这条用例会退化成空转').toBeGreaterThan(0);
    const studentId = items[0].student_id;

    // 收藏夹 / 分享链接 / 刷新：**整页加载**进来的，站内没有上一页
    // （`history.state.back === null`），所以「返回列表」不能走 `router.back()`
    // ——那会把用户退出整个应用（实测退到 `about:blank`，白屏）。
    for (const path of [
      `/counselor/cases/${studentId}`,
      `/counselor/students/${studentId}/records`
    ]) {
      await page.goto(path);
      const back = page.getByRole('button', { name: '返回列表' });
      await expect(back, `${path} 上没有「返回列表」`).toBeVisible();
      await back.click();
      await expect.poll(() => new URL(page.url()).pathname).toBe('/counselor/cases');
      // 而且要**真的**落在列表上：只把地址改掉、页面没跟上也是坏的。
      await expect(page.locator('button.queue-tab')).toHaveCount(7);
    }
  });

  test('工作台的指标卡点进去，名单人数与卡上那个数一致（切了负责人档也一样）', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/workbench');

    const ownerTabs = page.locator('.owner-tabs .owner-tab');
    await expect(ownerTabs).toHaveCount(3);
    await ownerTabs.nth(1).click();
    await expect(ownerTabs.nth(1)).toHaveClass(/active/);

    // 用「逾期跟进」：四张卡里它对负责人档**最敏感**（数的是 `c.overdue`），
    // 而负责人档只筛它下面那份队列、上面那几张卡一个数都不动。
    // 「关注档案总数」断不出这条——它切前切后都是全集，改前改后都绿。
    const card = page.locator('.metric', {
      has: page.locator('.metric-label', { hasText: '逾期跟进' })
    });
    const onCard = await numberIn(card.locator('.metric-value'));
    expect(onCard, '一张逾期档案都没有，这条用例会退化成空转').toBeGreaterThan(0);

    await card.click();
    // 深链**不带负责人档**：目的地不该比出发点的数字更窄（CLAUDE.md §11）。
    await expect(page).toHaveURL(/\/counselor\/cases\?filter=overdue$/);
    const listed = Number(
      (await page.locator('.toolbar .row-select span').first().innerText()).match(/^(\d+)\s*人/)?.[1]
    );
    expect(listed, '列表说的人数与卡片上的数不一致').toBe(onCard);
  });
});

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
    // 管理员落点 2026-09-27 前移到「系统概览」（V2.0.0 §5.14.5）。
    await page.waitForURL('/admin/overview');
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

  test('指标卡键盘到得了，没有去处的检查项不装作能点', async ({ page }) => {
    await loginAs(page, 'counselor');
    const card = page.locator('.metric', { hasText: '待人工复核' });
    await expect(card).toHaveAttribute('role', 'button');
    await expect(card).toHaveCSS('cursor', 'pointer');

    await card.focus();
    await expect(card).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/counselor\/cases/);

    // 没有去处的那一项不该装作能点。
    // ★ 2026-09-27（§5.14.4 第 7 条）：这一半的**活体换了地方**。领导页第四张
    //   「计划复测」此前正是这一条的活体——它长着 `role="button"` 的手型光标与
    //   hover 抬升却没有去处，`styles.css` 的注释点的就是它。§5.14.4 给了它去处
    //   （下钻到已筛选的复测名单），于是**全站 `.metric` 现在都带 role**：继续按
    //   「有没有 role」断，这条在「所有卡都有去处」之后就成了恒真（§29：一条恒绿的
    //   守卫比没有更糟，它占着「这一条有人守」的位置）。
    //   现在守的是 `.check-row` 那一对（`LeaderOverviewPage.vue`：两者外观相同，
    //   差别只在光标与悬停——判据与 `.metric` 那一处一致）：第三项「完成率低于 N%
    //   的年级」点不动，因为 `GradesPage.vue` 没有按完成率筛选的能力，而它的答案
    //   就写在那一行里。
    await loginAs(page, 'leader');
    const inert = page.locator('.check-row').nth(2);
    await expect(inert).toContainText('完成率低于');
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
    await expect(page).toHaveTitle('心晴 · 重点关注学生');
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
    // 管理员落点 2026-09-27 前移到「系统概览」（V2.0.0 §5.14.5）。
    await page.waitForURL('/admin/overview');
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

  /**
   * 「放弃修改」点下去要先问一句（V2.0.0 §5.14.6 第 5 条：危险操作分离并确认）。
   *
   * 它此前是 `@click="load"`：按钮只在 `isDirty` 时可点，所以点它**一定**意味着
   * 一批刚改的阈值会没——而拦住的那一步不存在。这条断言的两半正是那个区别：
   * 取消之后**改动还在**（证明没有偷偷 load），确认之后**才**变回去。
   *
   * 全程只读：只改输入框、不点保存，所以不写任何规则版本（这一组是 serial，
   * 且下一条收尾时会 `rule/reset` 回出厂值）。
   */
  test('放弃修改会先确认，取消之后改动还在', async ({ page }) => {
    await page.goto('/admin/scale');

    const panel = page.locator('section.card', { hasText: '评分规则' });
    const threshold = panel.locator('input[type="number"]').first();
    await expect(threshold).toBeVisible();
    const original = await threshold.inputValue();

    // 按钮是弹层唯一的入口，而 dialog 里的确认按钮**也叫这个名字**——两者靠
    // `panel` 这个作用域分开（弹层 Teleport 到 body，不在 panel 里）。
    const discard = panel.getByRole('button', { name: '放弃修改' });
    // 干净的时候它是灰的：先证明这枚按钮认得「改没改过」。
    await expect(discard).toBeDisabled();

    // 改一个值。不假设服务端此刻是多少——这一条断的是「改动还在不在」，
    // 而不是「出厂阈值是 7」（后者是上一条用例的主题）。
    const changed = original === '7' ? '8' : '7';
    await threshold.fill(changed);
    await expect(discard).toBeEnabled();

    await discard.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    // 代价只说草稿那一层：规则版本与历史结果都不动。少了后半句，改过阈值的人
    // 会以为自己要放弃一个已生效的版本。
    await expect(dialog).toContainText('不会保存');
    await expect(dialog).toContainText('历史结果也不受影响');

    await dialog.getByRole('button', { name: '取消' }).click();
    await expect(dialog).toHaveCount(0);
    // ★ 关键的一半：取消之后那个输入框里**还是刚填的值**。
    // 直接 `@click="load"` 的实现在这里变红，而上面那句「弹层可见」在它上面是绿的。
    await expect(threshold).toHaveValue(changed);

    // 再来一次，这回确认：值回到服务端那一份。
    await discard.click();
    await page.getByRole('dialog').getByRole('button', { name: '放弃修改' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(threshold).toHaveValue(original);
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

// ========== 空态与说明不指向不可达动作 ==========

/**
 * 空态与说明不得把用户指向实际不可用的动作（V2.0.0 §5.14.6 第 6 条）。
 *
 * 这一组断的是**跨角色的指路**：同一句文案被两个角色共用，而它点名的页面只属于其中
 * 一个。判据刻意不是「这句话该是哪几个字」，而是**那个入口该不该出现在他的屏幕上**
 * ——文案随便改，把管理员指向 `/counselor/*` 就会红。
 *
 * 两处都在**多角色共用的组件**里（`frontend/src/app/routes.ts` 里被两个以上角色引用的
 * 那 7 个，`rg` 数得出来）。该角色的路由表里没有那个页面 → `meta.role` 不匹配 →
 * `AppLayout` 把他弹回登录页，所以他点过去得到的是「登出」，而不是那个页面。
 *
 * 两条都不依赖库里有没有数据，且都**先证明有东西可扫，再断言它干净**——少了那一句，
 * 下面的 `not.toContainText` 在一个还在加载的页面上也成立，那就是一条恒绿的守卫。
 */
test.describe('空态与说明不指向不可达动作', () => {
  test('导出中心的空态对管理员说他自己能做的事，不指向心理老师的页面', async ({ page }) => {
    await loginAs(page, 'admin')
    // 导出作业**没有删除接口**（§16.3：只有撤销），所以演示库里那些作业永远不会自己
    // 消失，空态也就永远不出现。打桩把它清空是唯一可靠的办法，顺带不打真接口。
    await page.route(
      url => url.pathname.endsWith('/api/v1/export-jobs'),
      route =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            data: { items: [] },
            request_id: 'stub',
            error: null
          })
        })
    )
    await page.goto('/admin/exports')

    // 只在**那一句空态**里断，不扫整页：整页扫的话，页头、页脚或哪一天新加的一句
    // 无关文案里出现「工作台」三个字就会误报，而误报会让人把这条守卫关掉。
    const empty = page.locator('main .empty').filter({ hasText: '还没有导出记录' })
    await expect(empty, '空态没渲染出来，这一条会退化成空转').toBeVisible()

    // 「重点关注学生」「工作台」「测评任务」三个入口全在 `/counselor/*` 下。
    await expect(empty).not.toContainText('重点关注学生')
    await expect(empty).not.toContainText('工作台')
    await expect(empty).not.toContainText('测评任务')
    // 属于他的那一半要在：作业由谁发起、他到时候能做什么（查看 / 撤销，不能下载）。
    await expect(empty).toContainText('由心理老师在他的页面上发起')
    await expect(empty).toContainText('替任何人撤销')
  })

  test('任务页的未匹配行说明只对心理老师讲重传，不把领导指向数据中心', async ({ page }) => {
    await loginAs(page, 'leader')
    await page.goto('/leader/tasks')

    const firstRow = page.locator('tbody tr').first()
    await expect(firstRow, '演示数据里应当至少有一场任务').toBeVisible()
    await firstRow.getByRole('button', { name: '查看明细' }).click()

    const modal = page.locator('.modal-panel').first()
    await modal.getByRole('button', { name: '未匹配行' }).click()

    // 这一句常挂（与有没有未匹配行无关），所以不必先造一条导入批次。
    const note = modal.locator('p.muted').filter({ hasText: '为什么没进去' })
    await expect(note, '未匹配行的说明没渲染出来，这一条会退化成空转').toBeVisible()

    // 「数据中心」是 `/counselor/data`，而且补名册 / 补发目标学生 / 重传文件
    // 三件事没有一件是领导的写权。
    await expect(note).not.toContainText('数据中心')
    await expect(note).toContainText('都由心理老师处理')
  })
})

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

  /**
   * 数据中心「MHT测评记录导入」里那个「关联测评任务」下拉框的字体（2026-09-20 补，
   * 随用户报的「MHT测评记录导入显示很拥挤」一起）。
   *
   * **`.select` 的基础规则里没有 `font`。** 全站只有 `button, input { font: inherit }`
   * （`styles.css:757`，`select` 不在那一条里），而 `.select` 自己也没带——于是这个
   * 下拉框是浏览器默认的 **13.33px Arial**，紧挨着它的「批次名称」是 **16px Inter**，
   * 同一行里看起来像有一格没上样式（并排量出来的，不是估的）。
   *
   * 判据取的是「它属于全站那一套」而不是「它刚好是 16px」——与上面那条筛选框同一条
   * 理由：写字面量会在下一次调尺寸时无故变红。所以断言的是**与同一行里的输入框相等**。
   *
   * **为什么只断字体、不断宽度**（这是本次刻意留下的半条，理由量过）：`<select>` 的
   * min-content 是它最宽的那个 `<option>`（这里的选项是任务名，演示库里最长的一条
   * 565px），四格一行的版本里那一格只有 360px、下拉框自己量出 **631px** 溢到隔壁，
   * 所以「宽度 ≤ 格子」当时是有活条件的。改成「关联测评任务」独占整行（格宽 1049px）
   * 之后那个条件**不再成立**——摘掉 `min-width: 0`、或把 `label` 的 `minmax(0, 1fr)`
   * 退回 auto 轨道，两种变异都实测过，这条断言照样绿（CSS Grid 里 `width: 100%` 会让
   * grid item 的 `min-width: auto` 计算成 0，撑宽的机制根本没有落点）。
   * 恒绿的守卫比没有更糟（§18），所以宽度那半删掉，不假装它在守。
   * 要让它重新有牙，得先把这一格排回一个比 565px 窄的列里——那是布局改动，不是改断言。
   *
   * 变异验证：摘掉 `.batch-fields select` 的 `font: inherit` → 红（报「下拉框拿的是浏览器
   * 默认字体，没跟全站走」）。
   */
  test('数据中心的下拉框与同一行的输入框同字体', async ({ page }) => {
    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');
    await expect(page.getByRole('heading', { name: 'MHT测评记录导入' })).toBeVisible();

    const measured = await page.locator('.field-task select').evaluate((sel) => {
      const input = sel.closest('.batch-fields')!.querySelector('input[type=text]')!;
      const cs = getComputedStyle(sel);
      const ci = getComputedStyle(input);
      return {
        selectFont: `${cs.fontSize} ${cs.fontFamily}`,
        inputFont: `${ci.fontSize} ${ci.fontFamily}`,
      };
    });

    expect(measured.selectFont, '下拉框拿的是浏览器默认字体，没跟全站走').toBe(
      measured.inputFont,
    );
  });

  /**
   * 「导入明细」弹层的全屏展示（2026-09-24 加，用户报的「内容比较多，请弹出窗口时，
   * 可选全屏展示，以便展示全部内容」）。
   *
   * **为什么这条必须断计算值而不是文案**：这个功能在文本断言下是全绿的——按钮写着
   * 「全屏」、`aria-pressed` 也是对的，而面板宽度仍然是 940px，因为
   * `.modal-fullscreen` 与 `.modal-lg` 是**同特异性**的单类选择器，**谁写在后面谁说了算**
   * （`styles.css:1603` 那一长段就是为这条写的）。同理，表格那 `340px` 从前是**行内**
   * 样式，压过任何全局规则——点了全屏而表格仍只有 340px 高，屏幕上留着大片空白，
   * 而按钮的样子一切正常。所以这里量的是 `getBoundingClientRect` 与 `maxHeight`。
   *
   * **两条尺寸判据缺一不可**：只断「面板铺满了视口」的话，一个没有把 `max-height`
   * 从 85vh 覆盖掉的实现照样过——它的高度是 612px（720 的 85%），而宽度确实是 1280px。
   * 高度那一条同时钉住 `height: 100vh` 与 `max-height: 100vh` 两个声明。
   *
   * **「里面也铺开了」那一条量的是表格高度，而判据是「越过常规态那条 340 的上限」，
   * 不是「不低于某个下界」。** 第一版写的就是下界（`>= 260`），而它配着一个
   * `min-height: 260px`——下界就是它自己，于是那条断言**近乎恒真**：实测把
   * `detailTableStyle` 里的 `flex: 1 1 auto` 摘掉，表格 261px、原态 260px，**测试全绿**。
   * （1px 之差是 `flex-shrink: 1` 把内容压到 `min-height` 造成的舍入。）
   *
   * 那两行 `flex: 1 1 auto` + `minHeight: 260px` 是这一版**删掉**的，因为它在这个弹层里
   * 从来没伸展过：除表格之外的固定内容（摘要网格 192 + 页签 33 + 筛选条 40 + 分页 59 +
   * 两段说明）在 720 高的视口下就占掉约 426px，剩给表格的比表格内容还少——`flex: 1`
   * 伸展不了，`flex-shrink: 1` 反而把表格往下压，恰好被 `min-height` 托住。
   * 全屏的真实收益是**宽度 940 → 1280、纵向 612 → 720（1280×720 视口下实测），
   * 以及表格不再被 340 夹住**，纵向滚动交给正文自己
   * （`styles.css` 的 `.modal-fullscreen .modal-body > *`）。
   * 详见 `DataCenterPage.vue` 的 `detailTableStyle` 与那两条 CSS 的注释。
   *
   * 定位器用 `.modal-expand` 这个类名，**不用 `getByRole` 的名字**：`name` 是**子串**
   * 匹配，而「退出全屏」里就含着「全屏」——同一个 locator 在两种状态下都能命中，
   * 状态判据于是恒真。状态单独用 `aria-pressed` 或 `toHaveClass` 判。
   *
   * 数据自己用接口造（`seed_demo` 不种导入批次），文件内容固定 → 同指纹 + 同操作者 +
   * 仍是 `PREVIEW` + 同 `task_id`，`_reusable_batch` 会复用同一批，批次表不随时间增长
   * （与 `vocabulary.spec.ts` 那条同一条路子）。**这一批必须够高**，而行数是量出来的：
   * 两行内容 346（越过 340 只剩 6px，任何一处行高一变就翻）、**四行只有 323**（比 340 还矮，
   * 于是常规态根本没被夹住、上面那条断言自己就红了——它拦下的正是「靠内容本来就矮来假装
   * 被夹住」的那种绿），十二行两种状态都有余量（实测常规态 340、全屏 1271）。
   * 定位那一行用响应里的 `batch_no`，不按批次名——批次名换了内容就会另建一批，
   * 而旧的那一批连名字一起留在共享的开发库里，按名字取到的可能是它。
   *
   * 变异验证（五条，每条都 `cp -p` 落盘备份、改完逐字节 `cmp` 还原——本机没有别的办法
   * 证明复原，§18）：①`Modal.vue` 的 `fullscreenClass` 恒 `''` → 红在 `toHaveClass(
   * /modal-fullscreen/)`（收到 `"modal-panel modal-lg"`）；②`detailTableStyle` 全屏那一支的
   * `maxHeight: 'none'` 改回 `'340px'` → 红在 `tableMaxHeight === 'none'`；③全屏那一支的
   * **等价形态**（`maxHeight: 'none'` 配一个行内 `height: '340px'`）→ 红在 `tableHeight > 340`
   * （收到 340）——这一条专门证明**第三条断言有独立于第二条的牙**（②只会先撞上第二条）；
   * ④`styles.css` 的 `.modal-fullscreen` 整块挪到 `.modal-lg` **之前** → 红在宽度那条
   * （`Expected 1280 / Received 940`），证明宽度判据独立于类名判据有牙；
   * ⑤`styles.css` 的 `.modal-fullscreen .modal-body > *` 从 `flex: 0 0 auto` 改回
   * `flex: 0 1 auto` → 红在同一条 `tableHeight > 340`，而表格被压到 **34px**——那一段
   * 「压扁」的注释由此实测坐实（也说明那一条 `flex: 0 0 auto` 不是装饰）。
   */
  test('导入明细弹层可以铺满视口展示', async ({ page }) => {
    const header = ['姓名', '性别', '年龄', '年级', '班级', '所用时间',
      ...Array.from({ length: 100 }, (_, i) => `${i + 1}.题干`)].join(',');
    const cell = (name: string) =>
      [name, '1', '12', '1', '4', '3600秒', ...Array(100).fill('0')].join(',');
    // 十二行：第一行缺姓名（`INVALID_ROW`），后十一行查无此人（`NOT_FOUND`）。
    // **行数是判据的一部分**：两处尺寸断言一正一反（常规态恰被夹在 340、全屏越过 340），
    // 而这两条只在「内容真的比 340 高」时才有意义。实测过的两种不够高：
    // 两行时内容 346（越过 340 只有 6px，别的改动一动就翻），四行时内容 **323**
    // ——只有 323 的话常规态根本没被夹住，那一条自己就红了（这是它对的地方：
    // 它拦下的正是「靠内容本来就矮来假装夹住」的那种绿）。十二行在两种宽度下都远超 340。
    const names = Array.from({ length: 11 }, (_, i) => `e2e全屏查无此人${i + 1}`);
    const csv = `${header}\n${cell('')}\n${names.map((n) => cell(n)).join('\n')}\n`;

    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const preview = await page.request.post('/api/v1/assessment-imports/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-fullscreen-assessment.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(csv, 'utf-8'),
        },
        batch_name: 'e2e全屏明细',
        tested_on: '2026-09-19',
      },
    });
    expect(preview.ok(), 'MHT 导入预览没有成功，这一条失去了对象').toBeTruthy();

    // 批次号**从响应里读**：它按天编号，写死会在某一天红在一个与功能无关的地方；
    // 而按批次名取 `.first()` 也不行——这一批换了内容就是新指纹、会另建一批（同名），
    // 上一版那一批连名字一起留在共享的开发库里，取到哪一批取决于列表次序。
    const batchNo = (await preview.json()).data.batch_no as string;
    expect(batchNo, '预览没有回批次号，这一条失去了定位那一行的手段').toMatch(/^BATCH-/);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');
    await page.waitForLoadState('networkidle');

    const batches = page.locator('.card').filter({ has: page.getByRole('heading', { name: '导入批次' }) });
    const row = batches.locator('tbody tr', { hasText: batchNo });
    // 先证明这一批在历史里（顺序反过来的话，一个空表格也能让下面那条通过）。
    await expect(row).toHaveCount(1);
    await row.getByRole('button', { name: '查看明细' }).click();

    const panel = page.locator('.modal-panel').first();
    // 先证明有东西可量：十二行明细真的渲染出来了（扫一个空表格的尺寸也是「绿」的）。
    await expect(panel.locator('tbody tr')).toHaveCount(12);
    // 再等打开动画结束——不然量到的是 `scale(0.95)` 那一帧的中间态（见 helper 的注释）。
    await waitForModalSettled(panel);

    const expand = panel.locator('.modal-expand');
    const viewport = page.viewportSize()!;

    const measure = () => page.evaluate(() => {
      const el = document.querySelector('.modal-panel') as HTMLElement;
      const box = el.getBoundingClientRect();
      const wrap = el.querySelector('.table-wrap') as HTMLElement;
      return {
        x: Math.round(box.x),
        y: Math.round(box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
        fullscreen: el.classList.contains('modal-fullscreen'),
        tableMaxHeight: getComputedStyle(wrap).maxHeight,
        tableHeight: Math.round(wrap.getBoundingClientRect().height),
        tableScrollHeight: Math.round(wrap.scrollHeight),
      };
    });

    // 常规态：面板是那一档 lg（940px）+ 85vh，表格被页面压在 340px 里，而十二行内容比它高。
    const normal = await measure();
    expect(normal.fullscreen, '常规态就挂着全屏类名，下面的尺寸断言证明不了任何事').toBe(false);
    expect(normal.width, '常规态的面板不该铺满视口').toBeLessThan(viewport.width);
    expect(normal.tableMaxHeight, '常规态表格的高度上限该是那一页写的那一档').toBe('340px');
    // 常规态是**被夹住**的。不断这一条的话，下面那句「全屏之后越过 340」就没有对照——
    // 一个两边都没夹住的实现（内容本来就高）也能让它通过。
    // （上面那句 `waitForModalSettled` 是这一条成立的前提：`getBoundingClientRect()`
    //   量的是变换后的盒子，`scale(0.95)` 那一帧里 340 会被量成 323。）
    expect(normal.tableHeight, '常规态表格没有被 340 夹住，下面那条对照就没有意义').toBe(340);

    await expand.click();

    await expect(panel).toHaveClass(/modal-fullscreen/);
    await expect(expand).toHaveAttribute('aria-pressed', 'true');

    const full = await measure();
    expect(full.width, '全屏之后面板没有铺满视口宽度').toBe(viewport.width);
    expect(full.height, '全屏之后面板没有铺满视口高度（85vh 那条没被覆盖）').toBe(viewport.height);
    // 一个居中的 1280×720 面板也会满足上面两条——这两条钉住它真的在左上角。
    expect(full.x).toBe(0);
    expect(full.y).toBe(0);
    expect(full.tableMaxHeight, '表格仍然被行内那个 340px 压着').toBe('none');
    // 这条才是「里面的内容也铺开了」：表格长过了常规态那条硬上限。**不能写成
    // `>= 某个下界`**——下界那种写法在 `min-height` 面前是恒真的（第一版就是这么写的，
    // 摘掉 `flex` 也照样绿，见上面那段注释）。
    expect(full.tableHeight, '全屏之后表格没有越过常规态那条上限')
      .toBeGreaterThan(340);

    // 再点一次逐项复原：这个按钮是两个方向共用的那一个。
    await expand.click();

    await expect(panel).not.toHaveClass(/modal-fullscreen/);
    await expect(expand).toHaveAttribute('aria-pressed', 'false');

    const restored = await measure();
    expect(restored.width).toBe(normal.width);
    expect(restored.tableMaxHeight).toBe('340px');
    expect(restored.tableHeight, '退出全屏之后表格该回到被夹住的那一档').toBe(340);
  });

  /**
   * 「测评完成明细」弹层的全屏展示（2026-09-25 加，用户报的「测评任务中的查看明细
   * 按钮弹出时，也需要有全屏展示的能力」）。
   *
   * 与上面那条（导入明细）是**同一套机制**（`Modal` 的 `expandable` +
   * `.modal-fullscreen`），量的也是同一组计算值，所以那些「为什么必须量计算值」的
   * 理由不再重复。这一条存在的理由是**这个弹层独有的一处接缝**：它有三个页签，
   * 而三个页签各有一个 `table-wrap`，高度上限此前是**写死的行内样式**
   * （`max-height:340px`）——只给 `Modal` 加一个 `expandable` 而没把那三处抽成
   * `detailTableStyle` 的话，按钮、类名、面板尺寸**全对**，而表格仍然被夹在 340px 里，
   * 下面留一大片空白。所以「表格越过 340」这条是主题，不是上一条的重复。
   *
   * **三个页签逐个量 `maxHeight`**：那三处在模板里是三次独立的编辑，只改一处时另外
   * 两个页签仍然被夹住——而只量默认页签的话，那种半成品是全绿的。`tableHeight > 340`
   * 只在「目标学生」那一屏上量：它是唯一**行数不依赖学生交没交卷**的一屏（目标行是
   * 发放时一次生成的）；另两屏在演示库上可能是空的，而空表格的高度天然小于 340，
   * 拿它当「常规态被夹住」的对照会变成一个与功能无关的数据依赖。
   *
   * **任务从接口现取，且只在第一页那 10 行里挑**：写死哪一场会在演示数据变一次之后
   * 红在一个与功能无关的地方（§测试注意），而翻页去找一场更靠后的任务会让这一条多一条
   * 与主题无关的依赖（任务列表是 `:page-size="10"` 的客户端分页）。挑目标人数最多的
   * 那一场，是为了让「常规态恰被夹在 340」这句有对照（与上一条十二行同一条理由）。
   *
   * 定位那一行用**子串 + `.first()`**，不用 `toHaveCount(1)`：V2.0.0 的编号重试会给
   * 重名任务加后缀（`…-2`），唯一性断言会红在一个数据问题上。
   *
   * 变异验证（四条，每条都 `cp -p` 落盘备份、改完逐字节 `cmp` 还原——本机没有别的办法
   * 证明复原，§18）：①`TasksPage.vue` 的 `detailTableStyle` 全屏那一支改回
   * `maxHeight: '340px'` → 红在 `tableMaxHeight === 'none'`；②只把三处行内样式里的
   * **一处**留着（「未匹配行」那一处改回 `style="…max-height:340px"`）→ 红在那个页签的
   * `maxHeight` 上，而**默认页签那一条仍然绿**——这正是「三个页签逐个量」存在的理由；
   * ③摘掉 `Modal` 的 `expandable` → 红在 `.modal-expand` 上（定位器一个都命中不到）；
   * ④全屏那一支写成等价形态（`maxHeight: 'none'` 配一个行内 `height: '340px'`）→ 红在
   * `tableHeight > 340`（收到 340）。
   */
  /**
   * 每页最多一个主操作；「选择文件」是**输入**，不是**提交**（V2.0.0 §5.14.6 第 5 条）。
   *
   * 四处导入卡片上的「选择文件」此前是 `btn primary`，于是同一张卡片里并排两枚实心蓝
   * 按钮——这一枚与「确认导入 / 创建草稿版本」（后者由 `.import-summary button` 涂色，
   * 是主操作的**另一个出处**）。判据与全站一致：**改不改服务端状态**。选择文件只把字节
   * 读进浏览器；真正写库的是确认那一枚。
   *
   * **判据刻意不写成「卡片里最多一枚实心蓝」**：`.import-summary` 整块只在预览出现之后
   * 才渲染，所以初次进入这一页时一枚实心蓝都没有，那条断言恒成立——**一条恒绿的守卫
   * 比没有更糟，它占着「这一条有人守」的位置**（CLAUDE.md §29）。这里断的是「这一枚
   * 是白底」，它不需要预览出现就恒有牙。
   *
   * 走两个角色：`/counselor/data` 是心理老师的路由，另两条归管理员。不写死色值，
   * 只断「不是实心」——改了品牌色不该让这条红，让它红的只能是**又变回主操作**。
   */
  test('导入卡片的「选择文件」是次要操作，不是实心主操作', async ({ page }) => {
    const check = async (path: string) => {
      await page.goto(path);
      const pickers = page.locator('main .import-drop label.btn');
      // 先证明这几枚真的在：一个都没扫到时，下面的 `toEqual([])` 会静默通过
      // （「先证明有东西可扫，再断言它干净」）。
      await expect(pickers.first(), `${path} 上没扫到「选择文件」，这条断言会退化成空转`)
        .toBeVisible();
      const solid = await pickers.evaluateAll(els =>
        els
          .filter(el => {
            const bg = getComputedStyle(el).backgroundColor
            return bg !== 'rgba(0, 0, 0, 0)' && bg !== 'rgb(255, 255, 255)'
          })
          .map(el => (el.textContent || '').trim()),
      );
      expect(
        solid,
        `${path} 上有 ${solid.length} 枚「选择文件」是实心主操作：${solid.join(' / ')}`,
      ).toEqual([]);
    };

    await loginAs(page, 'counselor');
    await check('/counselor/data');

    await loginAs(page, 'admin');
    await check('/admin/organization');
    await check('/admin/scale');
  });

  test('测评完成明细弹层可以铺满视口展示', async ({ page }) => {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const listResp = await page.request.get('/api/v1/assessment-tasks', { headers });
    expect(listResp.ok(), '任务列表没读到，这一条失去了取数的手段').toBeTruthy();
    const all = (await listResp.json()).data.items as Array<{
      name: string
      total_targets: number
    }>;
    // 只在前 10 行里挑：页面是 `:page-size="10"` 的客户端分页，而接口的次序与它一致
    // （都是 `GET /assessment-tasks`，且页面默认筛「全部」）。
    const page1 = all.slice(0, 10);
    const biggest = page1.reduce(
      (best, t) => (t.total_targets > best.total_targets ? t : best),
      page1[0] ?? { name: '', total_targets: 0 },
    );
    // **行数是判据的一部分**（与上一条的十二行同一条理由）：常规态那条「恰被夹在 340」
    // 只有在这一屏真的比 340 高时才有意义——内容本来就矮时它自己就会红，而那是对的。
    expect(
      biggest.total_targets,
      '第一页里没有一场任务发够 12 个人，表格撑不到 340 以上，这一条的对照就没了——先跑 make seed-demo',
    ).toBeGreaterThan(11);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    await page.waitForLoadState('networkidle');

    const row = page.locator('tbody tr').filter({ hasText: biggest.name }).first();
    await expect(row, '这一场任务不在列表第一页，这一条失去了对象').toBeVisible();
    await row.getByRole('button', { name: '查看明细' }).click();

    // 用 `.modal-expand` 认这一个弹层，不用 `.first()`：这一页上还挂着别的弹层
    // （`FormDialog` 声明在它**前面**），换一种写法取到的可能是别人。
    const panel = page.locator('.modal-panel').filter({ has: page.locator('.modal-expand') });
    const expand = panel.locator('.modal-expand');
    const viewport = page.viewportSize()!;

    // 先证明那个按钮在：`Modal` 少一个 `expandable` 时它整个**不渲染**，而那时的失败会
    // 落在 `measure()` 里一句 `null.closest` 上——离真正的原因（没接上那个 prop）很远。
    await expect(expand, '这个弹层上没长出全屏按钮（`Modal` 的 `expandable` 没接上）')
      .toBeVisible();

    const measure = () => page.evaluate(() => {
      const btn = document.querySelector('.modal-expand') as HTMLElement;
      const el = btn.closest('.modal-panel') as HTMLElement;
      const box = el.getBoundingClientRect();
      const wrap = el.querySelector('.table-wrap') as HTMLElement;
      return {
        x: Math.round(box.x),
        y: Math.round(box.y),
        width: Math.round(box.width),
        height: Math.round(box.height),
        fullscreen: el.classList.contains('modal-fullscreen'),
        tableMaxHeight: getComputedStyle(wrap).maxHeight,
        tableHeight: Math.round(wrap.getBoundingClientRect().height),
        tableScrollHeight: Math.round(wrap.scrollHeight),
        bodyClientHeight: Math.round((el.querySelector('.modal-body') as HTMLElement).clientHeight),
      };
    });

    /** 切页签并**等表格真的出现**：数据还在路上的那一帧里整个 `.table-wrap` 不渲染。 */
    const gotoTab = async (tab: string) => {
      await panel.getByRole('button', { name: tab, exact: true }).click();
      await panel.locator('.table-wrap').waitFor();
    };

    /**
     * 停在「目标学生」那一屏，并**等到它真的渲染出足够多的行**。
     *
     * 三个页签各自在切过去之后才请求数据，所以「切完立刻数」量到的是空表格那一帧——
     * 一次全量 e2e 里它就这么红过一次（单跑时快，恰好绿）。而下面用的是**轮询**而不是
     * `expect(count()).toBeGreaterThan(11)`：后者是一次性取值，等不到任何东西。
     */
    const showTargetsWithRows = async () => {
      await gotoTab('目标学生');
      await expect.poll(
        () => panel.locator('tbody tr').count(),
        { message: '目标学生那一屏没有渲染出足够多的行，这一条失去了对象' },
      ).toBeGreaterThan(11);
    };

    const tableMaxHeightByTab = async () => {
      const out: Record<string, string> = {};
      for (const tab of ['目标学生', '完成明细', '未匹配行']) {
        await gotoTab(tab);
        out[tab] = await panel.locator('.table-wrap').evaluate(
          (el) => getComputedStyle(el as HTMLElement).maxHeight,
        );
      }
      // 回到行数最稳的那一屏，下面的高度断言要在它上面做。
      await showTargetsWithRows();
      return out;
    };

    // 先证明有东西可量：量一个空表格的尺寸也是「绿」的。
    await showTargetsWithRows();
    // 这一条切页签的往返通常已经盖过了那 0.25 秒，但那是**运气**——上一条用例就是这么
    // 红的（`323`，见 `waitForModalSettled` 的注释），所以这里也明着等一次。
    await waitForModalSettled(panel);

    // 常规态：面板是那一档 lg（940px）+ 85vh，表格被页面压在 340px 里。
    const normal = await measure();
    expect(normal.fullscreen, '常规态就挂着全屏类名，下面的尺寸断言证明不了任何事').toBe(false);
    expect(normal.width, '常规态的面板不该铺满视口').toBeLessThan(viewport.width);
    expect(normal.tableMaxHeight, '常规态表格的高度上限该是那一页写的那一档').toBe('340px');
    // 常规态是**被夹住**的。不断这一条的话，下面那句「全屏之后越过 340」就没有对照。
    expect(normal.tableHeight, '常规态表格没有被 340 夹住，下面那条对照就没有意义').toBe(340);
    expect(await tableMaxHeightByTab(), '常规态下还有页签的上限不是那一档').toEqual({
      目标学生: '340px', 完成明细: '340px', 未匹配行: '340px',
    });

    await expand.click();

    await expect(panel).toHaveClass(/modal-fullscreen/);
    await expect(expand).toHaveAttribute('aria-pressed', 'true');

    const full = await measure();
    expect(full.width, '全屏之后面板没有铺满视口宽度').toBe(viewport.width);
    expect(full.height, '全屏之后面板没有铺满视口高度（85vh 那条没被覆盖）').toBe(viewport.height);
    // 一个居中的 1280×720 面板也会满足上面两条——这两条钉住它真的在左上角。
    expect(full.x).toBe(0);
    expect(full.y).toBe(0);
    expect(full.tableMaxHeight, '表格仍然被行内那个 340px 压着').toBe('none');
    // 这条才是「里面的内容也铺开了」：表格长过了常规态那条硬上限。**不能写成
    // `>= 某个下界`**——下界那种写法在 `min-height` 面前是恒真的（上一条的注释记着那次实测）。
    expect(full.tableHeight, '全屏之后表格没有越过常规态那条上限').toBeGreaterThan(340);
    expect(await tableMaxHeightByTab(), '全屏下还有页签被那个 340px 压着').toEqual({
      目标学生: 'none', 完成明细: 'none', 未匹配行: 'none',
    });

    // 再点一次逐项复原：这个按钮是两个方向共用的那一个。
    await expand.click();

    await expect(panel).not.toHaveClass(/modal-fullscreen/);
    await expect(expand).toHaveAttribute('aria-pressed', 'false');

    const restored = await measure();
    expect(restored.width).toBe(normal.width);
    expect(restored.tableMaxHeight).toBe('340px');
    expect(restored.tableHeight, '退出全屏之后表格该回到被夹住的那一档').toBe(340);
  });

  test('sidebar is a fixed vertical rail on desktop', async ({ page }) => {
    await loginAs(page, 'counselor');
    const sidebar = page.locator('.sidebar');
    const box = await sidebar.boundingBox();
    // 246px rail; a collapsed grid would make it full-width or zero-height.
    expect(box!.width).toBeGreaterThan(200);
    expect(box!.width).toBeLessThan(300);
  });

  /**
   * Phase E 第 1/2/3 条（NAV-UX-01）：图标一律是 `AppIcon` 的 SVG，不再是充当图标的
   * Unicode 字符。
   *
   * 这一组此前**零覆盖**——侧栏 `navConfig` 的那批几何字符、`KpiCard` 的七个、
   * 排序指示符的三档（`▲` / `▼` / `⇅`）以及弹窗关闭按钮那个 `×`，全部换成 `AppIcon`
   * 之后没有任何一条用例看得见。而这类改动**恰好是最容易悄悄回退的**：
   * `<AppIcon name="x" />` 换回 `×` 只改一个字符，界面上仍然「有个叉」，
   * 全站所有文本断言照旧绿。
   *
   * 判据取的是**图标位里没有文字**，不是一份字符黑名单：任何 Unicode 字符塞进
   * `.nav-icon` 都会让它的 `textContent` 非空，而黑名单只挡得住列进去的那几个。
   * 两者在旧写法下都会红，但只有前者挡得住下一个人换上的那个字符。
   *
   * 三处写法上的讲究，少一条这条用例就会假绿：
   * - **读 `textContent` 而不是 `innerText`**：`innerText` 对不可见元素返回空串，
   *   而 `.nav-submenu` 是 `v-show`（收起来时 `display:none`）——用它会在图标**没换**
   *   的实现上收到一列空串，断言反而是绿的。
   * - **每一处先证明有东西可扫**：先断言图标位数量 > 0，再断言 svg 数量与之相等。
   *   只写后半句的话，一个「图标位一个都没渲染」的页面也满足 `0 == 0`。
   * - **`aria-hidden` / `focusable="false"` 与 viewBox / 线宽一起断**：它们是第 2 条
   *   那两句承诺的可执行形式——「装饰图标不打扰读屏软件」与「任意两个图标放在一行里
   *   视觉重量一致」。可读名称归**按钮**自己（`.modal-close` 的 `aria-label`），
   *   不归它里面那个图形（§15 那条分工）。
   *
   * 变异验证（各自只红这一条）：把任一处的 `AppIcon` 换回字符（红在「图标位里还有
   * 文字」与 svg 计数两条上）、把 `stroke-width` 改成 `2`（红在线宽）、
   * 把 `aria-hidden` 摘掉（红在装饰性）。
   */
  test('图标走 AppIcon 的 SVG，不再是充当图标的 Unicode 字符', async ({ page }) => {
    await loginAs(page, 'counselor');

    const assertIcons = async (selector: string, label: string) => {
      const slots = page.locator(selector);
      const count = await slots.count();
      expect(count, `${label}：一个图标位都没渲染，下面的断言会空转`).toBeGreaterThan(0);
      const svgs = page.locator(`${selector} svg.app-icon`);
      await expect(svgs, `${label}：图标位的数量与 svg 的数量对不上`).toHaveCount(count);

      // 图标位里不该有任何文字——这正是「图形不再是文字」的判据。
      expect(
        await slots.evaluateAll((els) => els.map((el) => (el.textContent ?? '').trim())),
        `${label}：图标位里还有文字（Unicode 字符充当图标的旧写法）`
      ).toEqual(new Array(count).fill(''));

      for (const shape of await svgs.evaluateAll((els) =>
        els.map((el) => ({
          viewBox: el.getAttribute('viewBox'),
          strokeWidth: el.getAttribute('stroke-width'),
          hidden: el.getAttribute('aria-hidden'),
          focusable: el.getAttribute('focusable'),
        }))
      )) {
        expect(shape.viewBox, `${label}：坐标系不统一`).toBe('0 0 24 24');
        expect(shape.strokeWidth, `${label}：线宽不统一`).toBe('1.8');
        expect(shape.hidden, `${label}：装饰图标必须 aria-hidden`).toBe('true');
        expect(shape.focusable, `${label}：SVG 要退出 tab 序列`).toBe('false');
      }
    };

    // ① 侧栏。counselor 实测 17 个图标位 = 桌面那一套 12 个（7 个顶层 + `v-show` 收着的
    //    5 个子页，它们也在 DOM 里）+ 窄屏那一套 5 个（4 个高频入口 + 「更多」）。
    //    **两套都在 DOM 里、由媒体查询选一套**，而 `page.locator` 数的是 DOM，
    //    与可见性无关——所以这个数不随视口变。它在每一页都在。
    await page.goto('/counselor/tasks');
    await expect(page.locator('.nav-icon').first()).toBeVisible();
    await assertIcons('.nav-icon', '侧栏导航');

    // ② 可排序表头的三档指示符（`sort` / `sort-asc` / `sort-desc`）。
    await expect(page.locator('tbody tr').first()).toBeVisible();
    await assertIcons('.sort-indicator', '可排序表头');

    // ③ 纯图标按钮：图形是 svg，可读名称在按钮自己身上。
    await page.getByRole('button', { name: '查看明细' }).first().click();
    await expect(page.locator('.modal-panel')).toBeVisible();
    await assertIcons('.modal-close', '弹窗关闭按钮');
    await expect(page.locator('.modal-close')).toHaveAttribute('aria-label', '关闭');

    // ④ 指标卡——另一处出现过字符图标的地方，落在统计页上。
    await page.goto('/counselor/analytics/overview');
    await expect(page.locator('.kpi-icon').first()).toBeVisible();
    await assertIcons('.kpi-icon', '指标卡');
  });

  /**
   * §5.14.6 第 3 条：窄屏底栏**只保留 4～5 个最高频入口**，其余进「更多」。
   *
   * 这一条钉的是底栏的**形态**。改动之前，780px 以下把整套导航摊进底栏再横向滚动
   * （心理老师那一栏是 12 个按钮 × `flex: 1 0 76px`），而底栏横滑是全站唯一一处
   * 「看不出还能滑」的交互——屏幕上没有任何东西提示右边还有内容，滑不滑得动只能靠猜。
   * 所以判据不是「有没有滚动条」，是**那一排按钮各自是什么**。
   *
   * 四个角色逐一断，因为那张表是按角色写死的（`AppLayout.vue` 的 `MOBILE_PRIMARY`），
   * 只断一个角色就漏掉另外三张。学生那一张**故意只有两项、且没有「更多」**：他本来
   * 就只有两个目的地，摆一个点开是空的按钮比不摆更糟。
   */
  test('窄屏底栏只保留最高频入口，其余进「更多」', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });

    const BARS = [
      // `labels` 里只放**真的能去的页面**，顺序也是判据（最高频的在最左边）。
      // 「更多」不进来：它是一枚**按钮**、点开是一个弹层，不是目的地。把它混进这张
      // 清单会让下面那两条断言互相遮蔽——少了它项数就不对，而项数不对就轮不到它那一条，
      // 于是它看着被两条守着，其实一条都没守（§29 那条「占着『这一条有人守』的位置」）。
      { role: 'counselor', labels: ['工作台', '重点关注学生', '测评任务', '统计分析'], more: true },
      { role: 'leader', labels: ['领导总览', '重点进展', '测评任务', '学校统计'], more: true },
      { role: 'admin', labels: ['系统概览', '账号与权限', '组织学生', '量表题库'], more: true },
      { role: 'student', labels: ['我的测评', '完成记录'], more: false }
    ] as const;

    for (const { role, labels, more } of BARS) {
      await loginAs(page, role);
      const who = `${role} 的窄屏底栏`;

      // 两套都在 DOM 里、媒体查询选一套（与 `.account-menu` 同一个做法，见 §5.14.4）。
      // 所以这里要断的是「选中的是哪一套」，而桌面那一套必须真的不显示——一个只把
      // 窄屏那套写出来、忘了藏桌面那套的实现，在 375px 下屏幕上并不明显。
      await expect(page.locator('.nav-desktop'), `${who}：桌面导航没有让位`).toBeHidden();
      await expect(page.locator('.nav-mobile'), `${who}：窄屏导航没有出现`).toBeVisible();

      // `a.nav-btn` 而不是 `.nav-btn`：底栏里只有**目的地**是链接，「更多」是一枚
      // `<button>`（它不导航，它开弹层）。这个区分不是为测试造出来的——它就是无障碍树里
      // 的角色差别，而下面那一条断的正是它。不排除它的话，两条断言会互相遮蔽。
      const items = page.locator('.nav-mobile a.nav-btn .nav-text');
      await expect(items, `${who}：底栏项数与预期不符`).toHaveCount(labels.length);
      expect(await items.allTextContents(), `${who}：底栏里的入口不对`).toEqual([...labels]);

      // 「更多」**只在真有东西可收时才在**，而且它必须是一枚 button。
      // 学生那一栏断它一个都没有——否则点开是一个空弹层，而「点了没反应」正是
      // 这一条改动最容易造出来的形状。
      await expect(
        page.locator('.nav-mobile').getByRole('button', { name: '更多' }),
        `${who}：「更多」不该在的时候在了（或该在的时候不在）`
      ).toHaveCount(more ? 1 : 0);

      // 每个按钮都必须落在底栏那个框里。**折行是允许的**（「重点关注学生」6 个字在
      // 375px 下放不进一格，见 `styles.css` 里 `.nav-mobile .nav-text` 那段尺寸核算），
      // **被裁掉不是**——而裁掉只看截图很难分辨：溢出的那半个字在有些字号下看不出来。
      const bar = await page.locator('.sidebar').boundingBox();
      expect(bar, `${who}：量不到底栏尺寸`).toBeTruthy();
      for (const button of await page.locator('.nav-mobile .nav-btn').all()) {
        const box = await button.boundingBox();
        expect(box, `${who}：量不到按钮尺寸`).toBeTruthy();
        expect(box!.y, `${who}：按钮从底栏上沿溢出`).toBeGreaterThanOrEqual(bar!.y - 1);
        expect(
          box!.y + box!.height,
          `${who}：按钮被底栏下沿裁掉`
        ).toBeLessThanOrEqual(bar!.y + bar!.height + 1);
      }

      await expectNoHorizontalOverflow(page, who);
    }
  });

  /**
   * 「更多」里必须真的装着**窄屏上本来到不了的那些**。
   *
   * 这一条钉的是那个弹层存在的**理由**，不是它的长相。以心理老师为例：底栏里只剩
   * 「统计分析」这一格，而它是一个**分组**——点它进的是分组第一页（`routes.ts` 的
   * redirect），另外四个子页在手机上**只有「更多」这一条路**。所以「底栏收窄」这件事
   * 成立的前提，正是这个弹层能把它们接住；弹层里少一个子页，那一页在 375px 下就没有
   * 任何入口了，而底栏那一排仍然看着很整齐。
   */
  test('窄屏的「更多」能走到窄屏装不下的那些入口', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await loginAs(page, 'counselor');

    await page.locator('.nav-mobile .nav-btn', { hasText: '更多' }).click();
    const panel = page.locator('.modal-panel');
    await expect(panel).toBeVisible();

    // 分组那一节按分组名成节，五个子页一个不少。
    for (const name of ['筛查关注概览', '八维度分析', '年级维度对比', '班级维度画像', '专业分析报告']) {
      await expect(panel.getByRole('link', { name }), `「更多」里没有「${name}」`).toBeVisible();
    }
    // 不在底栏里的顶层项也在。
    for (const name of ['数据中心', '导出中心', '审计日志']) {
      await expect(panel.getByRole('link', { name }), `「更多」里没有「${name}」`).toBeVisible();
    }
    // 已经在底栏里的**可点**项不重复列：弹层是一张地图，不是第二条工具栏。
    // 「统计分析」是分组、本身没有页面，所以它以那一节的形式在；而它的子页在。
    await expect(panel.getByRole('link', { name: '工作台' })).toHaveCount(0);
    await expect(panel.getByRole('link', { name: '重点关注学生' })).toHaveCount(0);

    // 点一项：跳过去，并且弹层自己关掉。**不关会挡住刚跳过去的那一页**，
    // 而这一条是全靠手感发现的——它不在任何计算值里。
    await panel.getByRole('link', { name: '八维度分析' }).click();
    await expect(page).toHaveURL(/\/counselor\/analytics\/dimensions$/);
    await expect(page.locator('.modal-panel'), '跳转之后弹层还挡在前面').toHaveCount(0);
  });

  /**
   * §5.14.6 第 8 条：`prefers-reduced-motion` 下全站动效停下。
   *
   * 判据是**计算值**，不是源码里有没有那一段。一段写对了但被更高特异性的规则盖住的
   * CSS，在屏幕上与没写一模一样，而读源码看不出来——这正是这条要防的形状：
   * 盖住它的那条规则挂在类选择器上（特异性 0-1-0），而这一段写在 `*` 上（特异性 0），
   * 少一个 `!important` 就**一条都盖不住**，但那段代码看着完全正常。
   *
   * 断两样东西，因为它们是**两类**动效，各有一条会单独失效的声明：
   * - `transition-duration`（指标卡的悬停抬升、分组箭头的旋转）；
   * - `animation-iteration-count`（骨架屏那条是 `… infinite`，光压时长它会以十万次
   *   每秒的频率继续重画，次数压到 1 才真的停下）。
   */
  test('prefers-reduced-motion 下过渡与动画都停下', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await loginAs(page, 'counselor');

    /**
     * 计算出来的时长（秒）。两处都踩过，都写在这里：
     *
     * ① **`getPropertyValue` 只认连字符写法**（`transition-duration`）。传 camelCase
     *    回来的是空串，而 `parseFloat('') || 0` 是 `0` —— 于是「量不到」与「已经停下」
     *    在断言里长得一模一样，整条用例恒绿。**实测过**：把全域 reduced-motion 整段
     *    摘掉（M6），它照样 `1 passed`。「恒绿的守卫比没有更糟，它占着『这一条有人守』
     *    的位置」（§29）说的就是这个形状，所以下面量不到时**当场红**，不回落成 0。
     * ② **只比大小，不比字符串**：`0.01ms` 在 Chromium 上读回来是 `1e-05s`，写死
     *    `'0.01ms'` 的断言会在换一个浏览器时红在一个与功能无关的地方。
     */
    const duration = async (
      selector: string,
      prop: 'transition-duration' | 'animation-duration'
    ) => {
      const raw = await page
        .locator(selector)
        .first()
        .evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), prop);
      // 先证明量到了东西，再断言它够小 —— 少了这一句，上面那 ① 就会重演。
      // 判据是「解析得出一个数」，**不是一个格式**：Chromium 把 `0.01ms` 读回来是
      // `1e-05s`（写死 `/^[\d.]+m?s$/` 会被它绊倒——第一版就是这么红的）。
      expect(raw, `${selector} 的 ${prop} 量不到，这一条就没有量到东西`).not.toBe('');
      expect(
        Number.isNaN(parseFloat(raw)),
        `${selector} 的 ${prop} 读回来是 ${JSON.stringify(raw)}，解析不出时长`
      ).toBe(false);
      return raw.endsWith('ms') ? parseFloat(raw) / 1000 : parseFloat(raw);
    };

    await page.goto('/counselor/workbench');
    await expect(page.locator('.metric').first()).toBeVisible();
    expect(await duration('.metric', 'transition-duration'), '指标卡的过渡没有停下').toBeLessThan(0.01);
    expect(await duration('.nav-chevron', 'transition-duration'), '分组箭头的过渡没有停下').toBeLessThan(0.01);

    /*
     * 骨架屏要**先让它出现**再量：它在数据回来那一刻就没了，直接 `goto` 之后的
     * 那一帧量到的多半是「元素不存在」——那样 `.first().evaluate()` 会抛，
     * 而把异常当判据的写法正是「一条会无故变红的守卫」。所以把 `/care-cases` 按住
     * （它就是工作台那个 `loading` 的闸门），等骨架屏出来了再量，量完放行。
     */
    await page.route('**/api/v1/care-cases', async route => {
      await new Promise(resolve => setTimeout(resolve, 3000));
      await route.continue();
    });
    await page.goto('/counselor/workbench');
    const skeleton = page.locator('.skeleton-line').first();
    await expect(skeleton, '骨架屏没有出现，这一条就没有量到东西').toBeVisible();
    expect(await duration('.skeleton-line', 'animation-duration'), '骨架屏动画没有停下').toBeLessThan(0.01);
    /*
     * 这一条守的是**另一件事**，所以它单独存在：只压 `animation-duration` 而不管次数时，
     * 一条 `… infinite` 的动画会以「每 0.01ms 一轮」的频率继续重画 —— 屏幕上看起来停了，
     * 而它仍在满负荷烧 CPU。所以它断的是**次数**，与上面那三条断的时长是两回事
     * （M6 把全域段摘掉时这一条**不该**红：`.skeleton-line` 落回 `animation: none`，
     * 次数的计算值就是初始值 `1`）。
     */
    expect(
      await page.locator('.skeleton-line').first().evaluate(
        el => getComputedStyle(el).animationIterationCount
      ),
      '无限动画只压时长不停次数，它会以极高频率继续重画'
    ).toBe('1');
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

  /**
   * 「发布」点下去要真的发生点什么（V2.0.0 §5.14.6 第 5 条）。
   *
   * 上面那一条只证明了**已发布的行没有这枚按钮**，而它对这个缺陷是全绿的：
   * `showPublish` 曾经只被赋值、模板里从来没有读过它，于是按钮在、点了没有任何反应，
   * 而演示库里恰好没有草稿版本——**一个扫不到 DRAFT 行的用例，两半都证明不了**。
   * 所以这一行草稿由桩喂进来。
   */
  test('发布草稿会先确认，取消之后一个请求都不发', async ({ page }) => {
    await loginAs(page, 'admin');

    // 两行都按真实形状喂：已发布那一条用的是**真 id**（1），这样 `current` 仍指向它、
    // 旁边的评分规则面板照常加载；草稿那一条的 id 是编的，没有任何东西会去请求它。
    const versions = [
      {
        id: 1, code: 'MHT', name: '中学生心理健康测验', version: 'MHT-1.1.0', status: 'PUBLISHED',
        published_at: '2026-09-19T04:16:05', total_questions: 100, content_questions: 90,
        validity_questions: 10, key_questions: 2, rule_version: 'MHT-RULE-1.1.90'
      },
      {
        id: 999, code: 'MHT', name: '中学生心理健康测验', version: 'MHT-1.1.1', status: 'DRAFT',
        published_at: null, total_questions: 100, content_questions: 90,
        validity_questions: 10, key_questions: 2, rule_version: 'MHT-RULE-1.1.91'
      }
    ];
    // 判定函数而不是字符串通配：`/scales/versions/1/rule` 同样以 `/scales/versions`
    // 打头，按前缀匹配会把评分规则面板一起桩掉，而那条请求的失败会被读成「面板坏了」。
    await page.route(
      url => url.pathname.endsWith('/scales/versions'),
      route =>
        route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            success: true, data: { items: versions }, request_id: 'e2e', error: null
          })
        })
    );

    let publishCalls = 0;
    await page.route(
      url => url.pathname.endsWith('/publish'),
      route => {
        publishCalls += 1;
        return route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({
            success: true,
            data: { version: 'MHT-1.1.1', status: 'PUBLISHED', archived_versions: ['MHT-1.1.0'] },
            request_id: 'e2e',
            error: null
          })
        });
      }
    );

    await page.goto('/admin/scale');

    const draftRow = page.locator('tr', { hasText: 'MHT-1.1.1' });
    // 先证明这枚按钮真的渲染出来了，再点它——一个「按钮在、点了什么都不发生」的实现
    // 在这条断言上是绿的，抓住它的是下面那一段。
    await expect(draftRow.getByRole('button', { name: '发布' })).toHaveCount(1);
    await draftRow.getByRole('button', { name: '发布' }).click();

    // 弹层要把代价说出来：旧版本归档（不是删除）、发布之后不能就地改。
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('归档');
    await expect(dialog).toContainText('不能就地修改');

    await dialog.getByRole('button', { name: '取消' }).click();
    await expect(dialog).toHaveCount(0);
    expect(publishCalls, '取消发布之后不该发出任何发布请求').toBe(0);
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

    // 三个数走 `row_counts` 那三个（**整批**，不套读者的数据范围）。两行都进不去，
    // 所以可导入 0、待确认 0、无法导入 2。数的是「无法导入」而不是旧版那句「错误」：
    // 第 4 期把「这一行进不去」与「这一行要你拍板」分成了两档（`match_status` 的九
    // 个码各归哪一档由后端测试钉住），界面上照这两档各报一个数。
    await expect(card.getByText('无法导入 2')).toBeVisible();

    // 这一颗按钮叫「**查看全部明细**」，不是下面「导入批次」表格行里那颗「查看明细」：
    // 两者都调到同一个弹层（`openDetail`），但这一颗看的是**刚上传的这批**，那一颗看的是
    // 历史里的某一批。名字是 V1.1.6 那次分页改动（`bab7582`）分开的——而当时这一行定位器
    // 没跟着改，于是这条用例在点它的时候超时（点不到，错误里只有一句 timeout，看不出
    // 是名字变了）。`getByRole` 的 `name` 是**子串**匹配，而这个名字里插着「全部」两个字，
    // 所以两个名字互不匹配：写错哪一个都点不到，这一点本身也是它守得住的原因。
    await card.getByRole('button', { name: '查看全部明细' }).click();
    // 逐行明细里那一格是服务端拼好的**一句** `message`（`_row_message`），不是一串
    // `errors`：它在界面上是一个单元格，读起来是一句「这一行为什么进不去」的话。
    await expect(page.getByText('缺少姓名')).toBeVisible();

    // 这一行**刻意不断言**是「班级不存在」还是「学生不存在」两种文案里的哪一种：那取决于
    // 库里此刻有没有 `704` 这个班，而库是共享的（`make seed-demo` 只有「1班」，但任何一次
    // 学生信息导入或人工建班都会让 `704` 出现——本机库现在就有）。两种文案都必然写出翻译后的
    // 班级名，那才是这一屏要证明的东西：`年级 1 + 班级 4` 被翻成了 `初一 704`。
    // 「两种原因分开报」这件事由后端测试逐字钉住（tests/test_assessment_import_api.py）。
    await expect(page.getByText(/「初一 704」/)).toBeVisible();

    // 定位不到就一行都不写：这一批里没有任何一行可提交，确认导入是灰的。
    await expect(card.getByRole('button', { name: '确认导入' })).toBeDisabled();

    // 第 4 期起，上传那一刻这一批就**已经落库**了（预览不再只是内存里的一个 token），
    // 所以卡片上印着服务端给的批次号，而「导入批次」里找得到同一批——那正是「上传完被
    // 叫走了，回来接着提交」这条路的人口。批次号**从屏幕上读，不写死**：它按天编号
    // （`BATCH-20260919-1`），写死的那一天这条用例就会红在一个与功能无关的地方。
    const batchLine = card.locator('p', { hasText: /批次 BATCH-/ });
    await expect(batchLine).toBeVisible();
    const batchNo = (await batchLine.textContent())?.match(/BATCH-[0-9-]+/)?.[0] ?? '';
    expect(batchNo).not.toBe('');

    const historyCard = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) });
    const historyRow = historyCard.locator('tr', { hasText: batchNo });
    // 先证明这一批在历史里（恰好一行——批次号是唯一的），再断言它带着「继续处理」：
    // 顺序反过来的话，一个空表格也能让下面那一条通过。
    await expect(historyRow).toHaveCount(1);
    await expect(historyRow.getByRole('button', { name: '继续处理' })).toBeVisible();
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

    // 提示里现在夹着批次号（V1.2 阶段 3：提示与审计都要说出是**哪一批**），所以这条
    // 断言不能按字面串起来——用正则跨过中间那一段，两头的结论照旧逐字钉住。
    await expect(page.locator('.toast', { hasText: /已导入 0 名学生（批次 [^）]+），放弃 1 条/ })).toBeVisible();

    // 要点四：「放弃」是真的没动他——那个名字一个单元格都不该出现。
    await expect(page.getByText('e2e不该出现')).toHaveCount(0);

    // 要点五：这一批在「导入批次」里看得见（V1.2 阶段 3）。名册导入此前是一次
    // **无痕动作**：导完之后库里只有一条审计，而「这次导的是哪份文件、哪几行没落上、
    // 为什么没落上」在界面上没有任何落点。
    const batches = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) });
    // 按文件名定位到**这一批**那一行，不靠「第一行就是最新的」——那份文件在上面那条
    // 用例里也被传过一次，而两次运行留下的同名行都在这张表里。
    const batchRow = batches.locator('tbody tr', { hasText: 'e2e-students.csv' }).first();
    await expect(batchRow).toBeVisible();
    await expect(batchRow.getByText('已导入')).toBeVisible();

    // 逐行明细：那一行说的是谁、撞上了什么、拿它怎么办。
    await batchRow.getByRole('button', { name: '查看明细' }).click();
    const modal = page.locator('.modal-panel').first();
    // 先证明有东西可扫，再断言它干净（本文件里那条教训的第五例）：明细在弹层里，
    // 而弹层是先开出来再拉数据的，扫到骨架屏那一帧的话下面两条都是绿的空断言。
    await expect(modal.locator('tbody tr').first()).toBeVisible();
    await expect(modal.getByText('已放弃')).toBeVisible();
    // 冲突码是**落库的编码**，界面上必须走 `rosterConflictLabel`——这一条同时也是
    // `ROSTER_CONFLICT_LABELS` 那份词表在屏幕上唯一能被扫到的地方。
    await expect(modal.getByText('学号已在名册上')).toBeVisible();
  });
});

/**
 * 挑行数最多的那个任务，点开它的明细弹层。
 *
 * 哪个任务最大取决于库里的演示数据，所以按「已完成 / 总人数」现场挑，不写死任务名。
 * **先等表渲染出来再数**：任务列表是异步拉的，`count()` 在那一帧拿到 0 行，循环就
 * 什么也没挑到（这一步第一次写漏了，报的是「应当至少有一个任务」）。
 *
 * 完成明细与目标学生两个弹层用例共用它——它们要的是同一件前提：这个弹层里
 * 真的有行可扫。返回弹层本身，免得每个用例各写一遍 `.modal-panel` 的定位。
 */
async function openLargestTaskModal(page: Page, path = '/counselor/tasks') {
  // 路径可传：同一个组件挂两条路由（`/counselor/tasks` 与 `/leader/tasks`），而
  // 缺口 12 那一组要断的正是**两个角色看到的东西不一样**，所以两边都得走一遍。
  await page.goto(path);
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
  const modal = page.locator('.modal-panel').first();
  await expect(modal).toBeVisible();
  return modal;
}

/**
 * 完成明细是一所千人学校里最长的一张表——一场普查一千多行，而弹层里那个窗口只有
 * 340px 高。所以这一屏靠的不是滚动，是**筛选**；而「全部」的出路是导出。
 */
test.describe('测评任务的完成明细', () => {
  test('明细能按学生筛选，并能导出完整名单', async ({ page }) => {
    await loginAs(page, 'counselor');
    const modal = await openLargestTaskModal(page);

    // 先证明有东西可扫，再断言筛选的结果——空表也能「筛出 0 行」。
    const detailRows = modal.locator('tbody tr');
    await expect(detailRows.first()).toBeVisible();
    const before = await detailRows.count();

    // 查无此人：说出「没有匹配」，而不是把表清空让人以为没数据。
    await page.fill('.modal-panel input[type=search]', 'zzz查无此人zzz');
    await expect(page.getByText(/没有匹配「zzz查无此人zzz」的记录，共 \d+ 条/)).toBeVisible();

    // 清空筛选要还原成原来那么多行——不是「清空之后表空了」。
    await page.fill('.modal-panel input[type=search]', '');
    await expect(detailRows).toHaveCount(before);

    // 「全部」的出路：导出真的下一份 CSV，表头里有关注等级与总分两列。
    //
    // 2026-09-19 起导出是**两跳**：点「导出CSV」先问用途 → 建一份作业 → 立刻把字节
    // 取回来。所以 `waitForEvent('download')` 不能再和点击并发等——中间多了一步
    // 填表。中间那个用途问题不是走过场，它是这一份文件唯一的事后解释（§8）。
    await page.getByRole('button', { name: '导出CSV' }).click();

    const dialog = page.locator('.form-dialog-form');
    await expect(dialog).toBeVisible();
    await dialog.locator('input').fill('E2E：完成明细导出');

    const createRequest = page.waitForResponse(
      (r) =>
        r.request().method() === 'POST' &&
        /\/assessment-tasks\/\d+\/completion\/export$/.test(new URL(r.url()).pathname)
    );
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      dialog.getByRole('button', { name: '导出' }).click(),
    ]);

    // 建作业那一步**不回文件**——它回的是一份作业载荷。这一条是整块设计的地基
    // （`test_export_jobs.py::test_creating_an_export_returns_a_job_and_no_bytes` 是它
    // 在后端那一侧的同一句），而它是**界面**上唯一能证伪它的地方：把文件塞回这一步，
    // 这份载荷会变成 CSV，下面那句 `job_no` 立刻读不出来。
    const job = (await createRequest).json();
    expect((await job).data.job_no).toMatch(/^EXPORT-/);

    const path = await download.path();
    const csv = readFileSync(path!, 'utf-8');
    expect(csv.split('\n')[0]).toContain('关注等级');
    expect(csv.split('\n')[0]).toContain('MHT总分');
  });
});

/**
 * 缺口 12 的**前端那一面**（2026-09-20）：同一场测评，看得到它 ≠ 看得到这一个个的人。
 *
 * 后端在那一天给完成明细的两条路径加了 `STUDENT_PSYCH_DETAIL: {SCOPED}`（见
 * `test_task_roles.py::test_the_leader_reads_the_task_but_not_the_people_in_it` 与
 * `test_permissions.py` 那两条）。这一条断的是**界面有没有跟着分岔**——后端隐藏从来
 * 不是安全问题（§4：前端隐藏不是安全措施），但一枚必然 403 的页签与一枚必然 403 的
 * 按钮不是「一种提示」，它们是一处空白：用户会以为完成明细这一场没数据。
 *
 * 两个方向各断一次，缺一条都不完整：
 *   - 领导：页签**不在**，而且有一句说得出理由与出路的话（§17：灰掉的按钮必须说得出为什么）；
 *   - 心理老师：页签**在**。少了这一半，一个把页签对所有角色都收起来的实现也是绿的。
 *     它由上面那条「明细能按学生筛选」顺带证明（那个用例一进弹层就在完成明细上，
 *     因为心理老师的默认页签就是它），所以这里不再重复一次。
 */
test('德育领导读得到这场测评，但明细里没有完成明细页签', async ({ page }) => {
  await loginAs(page, 'leader');
  const modal = await openLargestTaskModal(page, '/leader/tasks');

  // 先证明**有东西可看**，再断言它不在——否则一个整块没渲染出来的弹层也满足下面那句。
  // 六格是整场口径的纯计数，**领导读得到**（缺口 12 关掉的是逐人明细，不是这一组数）。
  // 断在 `.detail-grid` 这个容器上，不按那两个字去 `getByText`：下面那段口径说明里
  // 也写着「应测人数」（在一段长正文里），按文字找会同时命中两处而撞上严格模式——
  // 而那不是「它不该在那儿」，是定位器挑错了。
  await expect(modal.locator('.detail-grid')).toContainText('发放人数');
  await expect(modal.locator('.detail-grid')).toContainText('应测人数');
  await expect(modal.getByRole('button', { name: '目标学生' })).toBeVisible();

  await expect(modal.getByRole('button', { name: '完成明细' })).toHaveCount(0);
  // 默认页签因此落在「目标学生」上（心理老师那边是完成明细）。
  await expect(modal.locator('tbody tr').first()).toBeVisible();
  // 收起来的页签要说得出为什么，并给出替代落点。两句各取**同一行内**的片段：
  // 跨行的正则在原始 textContent 上匹配不到（换行与缩进都在里面），而这里要断的
  // 只是那两句话在不在，不必把整段拼成一条。
  await expect(modal.getByText(/完成明细逐行给出学号、姓名与关注等级/)).toBeVisible();
  await expect(
    modal.getByText(/（发放 \/ 应测 \/ 已完成 \/ 有效完成率）不受影响/)
  ).toBeVisible();
  await expect(modal.getByText(/「目标学生」与「未匹配行」两个页签照常可查/)).toBeVisible();

  // 「导出CSV」（完成明细那一枚）也必须不在：它此前只有 `:disabled` 没有 `v-if`，
  // 门加上去之后它就是领导手上的一枚必然 403 的按钮。
  await expect(modal.getByRole('button', { name: '导出CSV' })).toHaveCount(0);
});

/**
 * 「目标学生」页签（V1.2 第 1 期）：这场测评**发给了谁**。
 *
 * 它与同一个弹层里的「完成明细」读的是同一张表、同一批行，回答的却是另一个问题：
 * 完成明细说「这一批人这次测出了什么」，这一页说「谁在这份名单上、他是怎么进来的」。
 *
 * **这两条用例一行数据都不写**，是有意的。补发的「确认」会在共享的开发库里
 * 真的落下目标行——而那会让演示任务的发放人数与完成率每跑一次变一次，后面的用例
 * 看到的就是另一份统计口径（缺口 8 那条规矩的又一例）。后端那两条
 * （`test_task_targets.py` 的 §20#9）把「确认才写入」逐列钉住了，e2e 这里要看的是
 * **界面有没有接上**：页签在不在、行数与摘要对不对得上、预览弹层出不出来、
 * 取消之后有没有多出一行。
 */
test.describe('测评任务的目标学生', () => {
  test('页签列出的行数与摘要里的发放人数对得上，并能按学生筛选', async ({ page }) => {
    await loginAs(page, 'counselor');
    const modal = await openLargestTaskModal(page);

    // 摘要在页签之外，两边是**两个接口**：发放人数来自 `/assessment-tasks/{id}/participation`
    // （V1.2 阶段 8 起），下面这些行来自 `/assessment-tasks/{id}/targets`。两个口径
    // **不一样，而且是有意的**：那六格是**整场测评**的数（`/participation` 不套读者的
    // 数据范围，弹层下面那句小字写着这一句），明细表逐行给出姓名、随范围缩。对一位
    // 范围是全校的心理老师两者相等——这一条断言的正是那个「相等」，不是断言某个写死的数。
    // （§11 那条指标卡教训：屏上两处指向同一个东西时必须同源；不同源就得各写各的口径，
    // 而这一页写的是「整场」。）
    //
    // **那个数要等它落下来。** 六格是懒加载的，`/participation` 回来之前每一格都是 `—`，
    // 而 `Number('—')` 是 `NaN`——先等那一格不再是占位符，再读它。这与本文件别处的
    // 「先证明有东西可扫」是同一条：少了这一步，这一条断言的就是一个还没到的答案。
    const sentCell = modal.locator('.detail-row', { hasText: '发放人数' }).locator('b');
    await expect(sentCell, '发放人数一直没读出来，明细表那一趟就没有可比的对象').not.toHaveText('—');
    const sent = Number(await sentCell.innerText());
    expect(sent, '演示数据里应当至少有一个带目标行的任务').toBeGreaterThan(0);

    await modal.getByRole('button', { name: '目标学生' }).click();
    const targetRows = modal.locator('tbody tr');
    // 先证明有东西可扫：目标行还没渲染时，下面两条断言扫的是一块空区域。
    await expect(targetRows.first()).toBeVisible();
    await expect(targetRows).toHaveCount(sent);

    // 范围那一句是口径声明（§9）：没有「发放范围」记录时说的是「未记录」，
    // 而不是替它猜一个「全校」。
    await expect(modal.getByText(/发放范围：(全校|按年级|按班级|指定学生|未记录发放范围)/)).toBeVisible();

    await modal.locator('input[type=search]').fill('zzz查无此人zzz');
    await expect(modal.getByText(/没有匹配「zzz查无此人zzz」的记录，共 \d+ 条/)).toBeVisible();
    await modal.locator('input[type=search]').fill('');
    await expect(targetRows).toHaveCount(sent);
  });

  test('补发是「先看后补」：原因必填，取消之后一行都没多', async ({ page }) => {
    await loginAs(page, 'counselor');
    const modal = await openLargestTaskModal(page);
    await modal.getByRole('button', { name: '目标学生' }).click();
    const targetRows = modal.locator('tbody tr');
    await expect(targetRows.first()).toBeVisible();
    const before = await targetRows.count();

    await modal.getByRole('button', { name: '补发学生' }).click();
    // 按**可访问名**定位，不用 `.last()`：`.modal-panel` 的先后是模板里各组件的
    // 声明次序（`<FormDialog>` 排在详情弹层**前面**），不是它们的打开次序——两者都
    // Teleport 到 body，先声明的那一个于是永远排在前面。写成 `.last()` 拿到的是详情
    // 弹层，等一个它里面根本不存在的按钮会一直等到超时。
    const form = page.getByRole('dialog', { name: '补发目标学生' });
    const submit = form.getByRole('button', { name: '查看将补发哪些人' });

    // 原因是必填：空着提交只会得到一行错误，**一个请求都不会发**。
    await submit.click();
    await expect(form.getByText('补发原因不能为空')).toBeVisible();
    await expect(page.locator('.modal-panel')).toHaveCount(2);

    await form.getByLabel(/补发原因/).fill('e2e：只预览，不确认');
    await submit.click();

    // 第二步的弹层与第一步**同名**（都叫「补发目标学生」），而且表单是**渐隐着**退场的
    // ——那几百毫秒里两个弹层同时在 DOM 里，`getByRole('dialog', { name })` 会一次匹配到
    // 两个，Playwright 的严格模式直接判失败。所以先把「表单已经关掉」钉死：它既是这一步
    // 的真实行为，也让下面每个定位器都不必在两个同名弹层之间挑一个。
    await expect(page.locator('.form-dialog-form')).toHaveCount(0);

    const preview = page.getByRole('dialog', { name: '补发目标学生' });
    // 弹层里的两个按钮都在页脚里（表单那个「取消」在 `<form>` 里），按页脚收窄一层，
    // 与上面那条防的是同一件事。
    const footer = preview.locator('.modal-footer');
    // 演示任务的名册早就发满了，所以这里跑的是「没有可补发的人」那一支；两支都算通过
    // ——这一条钉的是「界面有没有地方回答这个问题」，不是演示数据此刻的形状。
    const empty = preview.getByText(/没有可补发的人/);
    await expect(preview.getByText(/将新增|没有可补发的人/)).toBeVisible();
    // 一支曾经的样子是「将新增 **0** 名学生」，紧接着下面再跟一句「没有可补发的人」
    // ——同一屏两句话各说各的，而第一句读起来像一件真会发生的事。所以「将新增」后面
    // 那个数必须是正的：这一条钉的是**这两句互斥**，与演示数据此刻有几个人无关。
    await expect(preview.getByText(/将新增\s*0\s*名/)).toHaveCount(0);
    // 「一个人都没有」与「那个按钮能不能按」必须是同一件事的两个说法，所以两个方向
    // 都断言：空着一份名单却给一个能按的「确认补发」，按下去只会得到一句
    // 「已补发 0 名学生」——一次什么也没做的写入被报成了成功。
    const confirm = footer.getByRole('button', { name: '确认补发' });
    if (await empty.isVisible()) {
      await expect(confirm).toBeDisabled();
    } else {
      await expect(confirm).toBeEnabled();
    }

    // 取消 = 什么都没发生。这是这一条的**判据**：预览写得像个提交按钮的实现在这里变红。
    await footer.getByRole('button', { name: '取消' }).click();
    await expect(targetRows).toHaveCount(before);
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

  test('报告列表连着读两次，先发的那个回来晚了也不算数', async ({ page }) => {
    // §5.16：`loadReports` 此前是本页唯一没有 §14 Latest Request Wins 守卫的读取方
    // （兄弟 `loadAnalysis` 有）。三条用例分别钉住「晚到的成功」与「晚到的失败」两个方向。
    const { slowDone } = await raceReportListReload(
      page,
      { ok: true, title: 'e2e-慢的答案' },
      { ok: true, title: 'e2e-快的答案' }
    );

    const list = page.locator('.report-list');
    await expect(list).toContainText('e2e-快的答案');
    await slowDone;
    // 先发的那一次此刻才落地（它被放行的时候，第二次的答案已经交出去了）。这 400ms 是
    // 留给「交付 → 渲染」的落地窗口，不是用来赌谁先谁后——谁先谁后上面已经定死了。
    await page.waitForTimeout(400);
    await expect(list).not.toContainText('e2e-慢的答案');
    await expect(list).toContainText('e2e-快的答案');
  });

  test('报告列表：晚到的失败不覆盖已经读回来的列表', async ({ page }) => {
    const { slowDone } = await raceReportListReload(
      page,
      { ok: false, message: '报告列表读取失败（慢）' },
      { ok: true, title: 'e2e-快的答案' }
    );

    const list = page.locator('.report-list');
    await expect(list).toContainText('e2e-快的答案');
    await slowDone;
    await page.waitForTimeout(400);
    // 没有守卫时这一支最要命：`catch` 里会 `reports.value = []` 并把那句话挂上去，
    // 于是连点两下重试把刚读回来的列表换成了一句「读取失败」（§14：一次失败的读取
    // 不许留下上一次的答案——反过来，一次过期失败的读取也不许抹掉最新的答案）。
    await expect(list.locator('.list-error')).toHaveCount(0);
    await expect(list).toContainText('e2e-快的答案');
  });

  test('报告列表：晚到的成功不洗掉已经落地的失败', async ({ page }) => {
    const { slowDone } = await raceReportListReload(
      page,
      { ok: true, title: 'e2e-慢的答案' },
      { ok: false, message: '报告列表读取失败（快）' }
    );

    const list = page.locator('.report-list');
    await expect(list.locator('.list-error')).toContainText('报告列表读取失败（快）');
    await slowDone;
    await page.waitForTimeout(400);
    // 「第 2 次的失败状态被保留」：那句话还在。
    await expect(list.locator('.list-error')).toContainText('报告列表读取失败（快）');
    // 而**这一次失败的读数不许被一次过期的成功改写**。判据落在表头那一行上：
    // `共 N 份` 只由 `reports.length` 决定，且它在 `v-if` 链**之外**（上面那张表更是
    // 整块被 error 那一支挡住，看不出差别）——一次失败的读取把 `reports` 清成了空，
    // 所以此刻表头不该还声称知道有几份。没有守卫时那个过期的成功会把它写回去，
    // 于是屏幕上同时写着「读取失败」和「共 459 份」。
    await expect(list.locator('.list-head .muted')).toHaveCount(0);
    await expect(list.locator('.list-error')).toContainText('报告列表读取失败（快）');
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

    // 第一份：S001 已在名册上，于是它会渲染出「待确认 1」与一个**可提交的批次**。
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-第一份.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_no,name,grade,class_name\nS001,e2e预演,初二,801\n', 'utf-8'),
    });
    await expect(card.getByText('待确认 1')).toBeVisible();

    // 第二份：请求**本身**失败（服务端 500），不是「文件里有错误」。
    await failApiPaths(page, { '/api/v1/student-roster/import/preview': '导入预览失败' });
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-第二份.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_no,name,grade,class_name\nS002,e2e预演2,初二,801\n', 'utf-8'),
    });

    // 上一份的预览必须消失：留着它，「确认导入」交出去的是**上一份文件**的批次 id，
    // 而屏幕上写着刚选的那一份——这不是显示错了，是导错了文件。
    // 失败要说话（提示条在卡片外面，它是全站那一个容器），并且**不留**上一份预览。
    await expect(page.locator('.toast', { hasText: '导入预览失败' })).toBeVisible();
    await expect(card.getByText('待确认 1')).toHaveCount(0);
    await expect(card.getByRole('button', { name: '确认导入' })).toHaveCount(0);
  });

  test('服务端回的是一段纯文本时，屏幕上说的是人话而不是浏览器的原话', async ({ page }) => {
    // 上一条桩的是**统一封装**（JSON 的 500），这一条桩的是另一支：服务端崩在一个没有
    // 被 `AppError` 收住的异常上，Starlette 的兜底处理器回的是**纯文本**
    // `Internal Server Error`（`content-type: text/plain`）。这正是用户 2026-09-20 报的
    // 那一个——一份 GBK 编码的 CSV 让后端的解码抛在 `AppError` 之外，而屏幕上的原话是
    // `JSON.parse: unexpected character at line 1 column 1 of the JSON data`，
    // 它一个字都没提到编码。
    await loginAs(page, 'admin');
    await page.goto('/admin/organization');

    const card = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '学生导入' }) });
    await expect(card).toBeVisible();

    await page.route(
      (url) => url.pathname === '/api/v1/student-roster/import/preview',
      (route) =>
        route.fulfill({ status: 500, contentType: 'text/plain', body: 'Internal Server Error' })
    );
    await card.locator('input[type=file]').setInputFiles({
      name: 'e2e-纯文本.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_no,name,grade,class_name\nS002,e2e预演2,初二,801\n', 'utf-8'),
    });

    // 说得出话，而且**带上了状态码**：前端拿不到 HTTP 状态码（§2 的既有约定），
    // 这一支里它是唯一能给出的线索，所以它只能出现在这句话里。
    await expect(page.locator('.toast', { hasText: 'HTTP 500' })).toBeVisible();
    // 而浏览器那句原话不许出现在屏幕上——它就是用户报的那一句，也是**唯一**会被
    // 误当成「文件格式不对」的一句话。
    await expect(page.locator('.toast', { hasText: 'JSON.parse' })).toHaveCount(0);
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
    // §5.14.6 第 5 条起它先问一句，确认之后才真的重新拉。要按**弹层里那一颗**——
    // 触发器与确认键同名，而 `page.getByRole('button', …)` 会同时命中两个（严格模式）。
    await page.getByRole('dialog').getByRole('button', { name: '放弃修改' }).click();

    await expect(panel.locator('.form-error')).toBeVisible();
    // 留着那一行的话，标题栏写着 `MHT-RULE-1.1.0 · 生效中`、正文写着读取失败——
    // 读者会以为这次失败说的是 1.1.0，而它说的是别的版本。
    await expect(panel).not.toContainText('MHT-RULE-');
  });
});

/**
 * P0-01 测评任务的删除与作废治理（规格 §4.10–§4.12）。
 *
 * 三条用例都遵守「先证明有东西可扫」：断「它不在了」之前先断「它在」——一张空表
 * 能让任何一条「消失」断言变绿。
 *
 * **数据处置**（`跑 e2e 不许改掉操作员自己的东西`）：
 * - ① 自建一场空任务、当场删掉：沿途零残留（删的是任务自己与它的目标行）；
 * - ② 只在**最大的那场演示任务**上打开影响预览然后取消，一行都不写；
 * - ③ 自建一场任务 + 一个绑在它上面的**预览批次**（不提交），作废之后留着——
 *   与既有的那几个 e2e 预览批次同族（§25），`make purge-demo` 一并清掉。
 *
 * 这里断的是**界面**（§3 的第二面：视图有没有取用标签与判据）。七个场景的**语义**
 * 由 `backend/app/tests/test_task_governance.py` 的 18 条用例钉住，不在这边重推。
 *
 * 两处**刻意不断**，理由写在这里免得下次被读成漏了：
 * - 「作废后不再影响统计」「不删除人工关怀记录」在界面上没有一处**只有作废才会变**
 *   的像素：演示库上的统计数字同时被并发的其它用例改动（`fullyParallel`），拿前后两次
 *   读取相比会变成一条会无故变红的守卫。所以这边断的是那两句话**被说了出来**
 *   （影响预览里的「N 份答卷」「关联 N 份关怀档案…不会删除，也不会被修改」），
 *   而「说了就真的做了」由后端那 18 条钉住。
 * - 「IMPORTED task 作废后 import batch 仍存在」走的是一份**预览**批次（来源是导入，
 *   批次真的挂在任务上），因为要做出一个**已提交**的导入批次，文件里那一列班级必须是
 *   数字编号（`704`），而演示名册的班叫 `1班`——提交得先动共享名册，代价比它换来的
 *   那点保护大（§26 / §27 记着同一类取舍）。后端那一侧由
 *   `test_task_governance.py::test_void_imported_task_keeps_import_batches` 钉住。
 */
test.describe('测评任务的删除与作废治理', () => {
  // ★ 这里 2026-09-25 一度写过 `test.describe.configure({ mode: 'serial' })`，**已经删掉**。
  //
  // 当时的红：全仓只有这一组会用 `POST /api/v1/assessment-tasks` 真建任务（其余建任务的
  // 用例走的是种子 / 演示数据），而那个端点并发不安全——`create_school_assessment_task`
  // 拼的是 `TASK-{utcnow:%Y%m%d%H%M%S}-{全库任务数 + 1}`，同一秒里的两个请求算出**同一个
  // 任务号**，撞唯一键的那个拿到 500（实测两个并发 curl：`req1 http=500` / `req2 http=200`）。
  // 于是这一组自己撞自己（Playwright 默认 3 workers），红的原因不在被测代码。
  //
  // 串行是**绕过，不是修复**：真实用户在同一秒里点两次「新建任务」照样 500。修复落在
  // `services/numbering.py`（撞号就往上试一个号），所以那条串行线不再需要——**别再把它
  // 加回来**：它是「这一组需要串行」这句话里唯一会过期的那半截，留着会掩盖真回归。
  //
  // 语义由 `backend/app/tests/test_numbering_concurrency.py` 钉住（两个独立事务 +
  // 钉死的时钟，必然撞号）。这份 e2e 不重复推它，也不为它造数据。

  /** 心理老师的 API 会话。浏览器那一路另走 `loginAs`，两条互不影响。 */
  async function counselorHeaders(page: Page) {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    expect(login.ok(), '心理老师 API 登录没有成功，这一条失去了对象').toBeTruthy();
    return { Authorization: `Bearer ${(await login.json()).data.access_token}` };
  }

  /**
   * 按任务号查这一场（判据用服务端发的数，不写死）。
   *
   * **`status=ALL` 不含已作废的**（§4.11：`ALL` 说的是「全部还在用的任务」）。它正是
   * 「默认列表里它消失了」那条断言的依据——而这句话还有半截：只查 `ALL` 分不出
   * 「真删掉了」与「只是作废了」，所以两处结论都得查一次（`VOIDED` 那一趟才是判别式的）。
   */
  async function findTask(page: Page, headers: Record<string, string>, taskNo: string, status = 'ALL') {
    const listed = await page.request.get(`/api/v1/assessment-tasks?status=${status}`, { headers });
    const items = (await listed.json()).data.items as Array<{ task_no: string; status: string }>;
    return items.find((t) => t.task_no === taskNo);
  }

  /** 一份一行都匹配不上的 MHT 文件：只会拿到逐行结论，不写任何测评记录。 */
  function unmatchedAssessmentCsv(name: string): string {
    const header = ['姓名', '性别', '年龄', '年级', '班级', '所用时间',
      ...Array.from({ length: 100 }, (_, i) => `${i + 1}.题干`)].join(',');
    const cell = [name, '1', '12', '1', '4', '3600秒', ...Array(100).fill('0')].join(',');
    return `${header}\n${cell}\n`;
  }

  test('还没产生正式测评事实的空任务可以整体删除', async ({ page }) => {
    const headers = await counselorHeaders(page);
    const taskName = `e2e空任务-${Date.now()}`;
    const created = await page.request.post('/api/v1/assessment-tasks', {
      headers,
      data: { name: taskName },
    });
    expect(created.ok(), '建任务接口没有成功，这一条失去了对象').toBeTruthy();
    const { id: taskId, task_no: taskNo } = (await created.json()).data;

    // 先证明它真的落在「可以整体删除」那一支——判据来自服务端，不是这里猜的。
    const check = await page.request.get(`/api/v1/assessment-tasks/${taskId}/delete-check`, {
      headers,
    });
    expect((await check.json()).data.canHardDelete, '这一场没有产生过事实，本该可整体删除')
      .toBe(true);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    const row = page.locator('tbody tr', { hasText: taskNo });
    await expect(row, '新建的任务没有出现在列表里，这一条失去了对象').toHaveCount(1);

    await row.getByRole('button', { name: '删除任务' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(`删除任务「${taskName}」`);
    // 影响预览排在控件之前，说的正是这一支的后果。
    await expect(dialog.locator('.form-dialog-description')).toContainText(
      '这场任务还没有产生正式测评事实，可以整体删除，删除后不可恢复。');
    // 空任务不需要填任何东西：这一支**没有**作废原因那一格。
    await expect(dialog.locator('textarea')).toHaveCount(0);

    // 先点一次「取消」，它**什么都不做**（2026-09-26 用户报的 bug）。
    // 这一档没有必填字段，所以调用方曾经只能靠「必填项是不是空的」去猜用户按了哪一个按钮——
    // 而这里没有任何必填项，于是「取消」与「确认删除」在它眼里长得一模一样，点取消任务当场被删。
    // 判据是**任务仍在**（服务端那一趟），不只是弹层关掉了：弹层关掉而任务没了正是那个 bug 的样子。
    await dialog.getByRole('button', { name: '取消' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(row, '点了取消，任务却从列表里消失了').toHaveCount(1);
    expect(await findTask(page, headers, taskNo), '点了取消，任务却真的被删掉了').toBeDefined();

    await row.getByRole('button', { name: '删除任务' }).click();
    await expect(dialog).toContainText(`删除任务「${taskName}」`);
    await dialog.getByRole('button', { name: '确认删除' }).click();
    await expect(page.getByText('任务已删除')).toBeVisible();
    await expect(page.locator('tbody tr', { hasText: taskNo })).toHaveCount(0);

    // 「从列表里消失了」有两种：真删掉了，或者只是作废了（默认列表也不含作废的）。
    // 两条都要断——只断前一条的话，一个把它误作废成 VOID 的实现照样是绿的。
    expect(await findTask(page, headers, taskNo), '任务没有真的删掉，只是从这一屏上消失了')
      .toBeUndefined();
    expect(await findTask(page, headers, taskNo, 'VOIDED'), '空任务本该被整体删除，却只是被作废了')
      .toBeUndefined();
  });

  test('产生了测评事实的任务只能作废，影响预览逐条说出保留了什么', async ({ page }) => {
    const headers = await counselorHeaders(page);
    const listed = await page.request.get('/api/v1/assessment-tasks?status=ALL', { headers });
    const items = (await listed.json()).data.items as Array<{
      id: number; task_no: string; name: string; total_targets: number; completed_targets: number;
    }>;
    // 挑靶子按**数据**挑，不写死任务名：写死会在某一天红在一个与功能无关的地方。
    //
    // ★ 判据是 `completed_targets`（有学生真的答完了），**不是 `total_targets`**。
    // 2026-09-25 这一条红过一次，红的原因不在被测代码，在原来那句启发式：
    // `total_targets` 数的是**配置**（范围内有多少学生），而这一组里另两条用例会
    // **并发**建一场新任务（Playwright 默认 3 workers），新任务的目标数与演示那一场
    // **一样多**（都是「范围内全部学生」）——于是「目标最多的那一场」能指到那个刚建出来、
    // 一场都没答过的空任务上，`canHardDelete` 是 `true`，而这条用例要的恰恰是「有事实的
    // 那一场」。完成数是**事实**，空任务恒为 0，按它排稳定。
    const target = [...items]
      .filter((t) => t.completed_targets > 0)
      .sort((a, b) => b.completed_targets - a.completed_targets)[0];
    expect(target, '列表里没有一场完成过任何学生的任务，这一条失去了对象').toBeTruthy();

    const check = await page.request.get(`/api/v1/assessment-tasks/${target.id}/delete-check`, {
      headers,
    });
    const impact = (await check.json()).data as {
      canHardDelete: boolean; sessionCount: number; careCaseCount: number;
    };
    // 先证明这一场**真的有**测评事实与关怀档案：没有的话，下面那两句承诺压根不会
    // 出现在屏幕上，而断言会变成一句空话。
    expect(impact.canHardDelete, '这一场没有可作废的事实，这一条失去了对象').toBe(false);
    expect(impact.sessionCount).toBeGreaterThan(0);
    expect(impact.careCaseCount).toBeGreaterThan(0);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    const row = page.locator('tbody tr', { hasText: target.task_no });
    await expect(row).toHaveCount(1);
    await row.getByRole('button', { name: '删除任务' }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(`作废任务「${target.name}」`);
    const description = dialog.locator('.form-dialog-description');
    await expect(description).toContainText(
      '作废不删除任何记录：答卷、结果、关怀档案原样保留，只是不再参与当前统计。');
    await expect(description).toContainText(`${impact.sessionCount} 份答卷`);
    await expect(description).toContainText(
      `关联 ${impact.careCaseCount} 份关怀档案，档案与人工记录不会删除，也不会被修改`);

    // 作废原因必填：不填直接提交，弹窗留在原地并指出缺什么。
    await dialog.getByRole('button', { name: '确认作废' }).click();
    await expect(dialog.locator('.field-error')).toContainText('作废原因不能为空');
    await expect(dialog, '校验没过时弹窗不该关掉——关掉的话那些字没人看得见').toBeVisible();

    // 取消 → 这一场原样还在（取消的语义在下面第三条上用自建任务做严格断言，
    // 这里只断「它没被这一次点击弄没」——演示数据上任何跨两次读取的比对都会
    // 被并发用例改动，那是一条会无故变红的守卫）。
    await dialog.getByRole('button', { name: '取消' }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.locator('tbody tr', { hasText: target.task_no })).toHaveCount(1);
  });

  test('作废之后从默认列表消失，只在「已作废」里找得到（导入批次仍在）', async ({ page }) => {
    const headers = await counselorHeaders(page);
    const stamp = Date.now();
    const taskName = `e2e作废任务-${stamp}`;
    const created = await page.request.post('/api/v1/assessment-tasks', {
      headers,
      data: { name: taskName },
    });
    expect(created.ok(), '建任务接口没有成功，这一条失去了对象').toBeTruthy();
    const { id: taskId, task_no: taskNo } = (await created.json()).data;

    // 绑一个**预览**批次：这一批是导入来的、真的挂在这场任务上，而它一行都不匹配，
    // 所以不会写任何测评记录——「作废不删导入批次」这句话因此有了可查的对象。
    const preview = await page.request.post('/api/v1/assessment-imports/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-void-import.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(unmatchedAssessmentCsv(`查无此人${stamp}`), 'utf-8'),
        },
        batch_name: `e2e作废批次-${stamp}`,
        tested_on: '2020-01-15',
        task_id: String(taskId),
      },
    });
    expect(preview.ok(), '测评导入预览没有成功，这一条失去了对象').toBeTruthy();
    const batchNo = (await preview.json()).data.batch_no as string;

    // 先证明这一个批次真的把这场任务推进了「只能作废」那一支（服务端的判据）。
    const check = await page.request.get(`/api/v1/assessment-tasks/${taskId}/delete-check`, {
      headers,
    });
    const impact = (await check.json()).data as { canHardDelete: boolean; importBatchCount: number };
    expect(impact.importBatchCount, '预览批次没有挂到这场任务上').toBe(1);
    expect(impact.canHardDelete, '有导入批次时不该还能整体删除').toBe(false);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    const row = page.locator('tbody tr', { hasText: taskNo });
    await expect(row, '新建的任务没有出现在列表里，这一条失去了对象').toHaveCount(1);

    // 先取消一次：这一场是自建的、没有别人碰它，所以「取消 = 什么都没写」在这里
    // 可以严格地断——状态必须还是没作废。
    await row.getByRole('button', { name: '删除任务' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(`作废任务「${taskName}」`);
    await expect(dialog.locator('.form-dialog-description')).toContainText(
      '作废不删除任何记录：答卷、结果、关怀档案原样保留，只是不再参与当前统计。');
    await expect(dialog.locator('.form-dialog-description')).toContainText('1 个导入批次');
    await dialog.getByRole('button', { name: '取消' }).click();
    await expect(dialog).toHaveCount(0);
    expect((await findTask(page, headers, taskNo))?.status, '取消之后任务不该被作废')
      .not.toBe('VOIDED');

    // 再打开一次，这回真的作废。
    await row.getByRole('button', { name: '删除任务' }).click();
    const reason = `e2e 作废 ${stamp}`;
    await page.getByRole('dialog').locator('textarea').fill(reason);
    await page.getByRole('dialog').getByRole('button', { name: '确认作废' }).click();
    await expect(page.getByText('任务已作废，历史记录已保留且不再参与当前统计')).toBeVisible();

    // ① 默认列表里消失，而接口说它是 VOIDED（不是被删了——两件事在这里分开断）。
    await expect(page.locator('tbody tr', { hasText: taskNo })).toHaveCount(0);
    expect(await findTask(page, headers, taskNo), '作废之后它不该还留在「还在用」的那一批里')
      .toBeUndefined();
    expect((await findTask(page, headers, taskNo, 'VOIDED'))?.status,
      '作废之后这一场应当还在，只是状态变成已作废').toBe('VOIDED');

    // ② 「已作废」筛选找得到它，并且带的是那个中文标签（不是裸编码）。
    //
    // 定位器限定在**页头**里：`select.select` 这一条会命中两个元素（`DataTable` 的
    // 「每页条数」下拉也带 `select` 类），而 Playwright 在严格模式下会直接报错并列出
    // 两个候选。那不是「找错了」，是「两个都对」——所以要按位置把话说清楚。
    await page.locator('.page-head select').selectOption('VOIDED');
    const voidedRow = page.locator('tbody tr', { hasText: taskNo });
    await expect(voidedRow, '已作废筛选里应当有它').toHaveCount(1);
    await expect(voidedRow.locator('.pill')).toContainText('已作废');

    // ③ 导入批次原样还在（作废的是任务，不是它带来的数据）。
    await page.goto('/counselor/data');
    const batchRow = page.locator('tbody tr', { hasText: batchNo });
    await expect(batchRow, '作废任务时把它的导入批次一起删掉了').toHaveCount(1);
    await expect(batchRow.locator('.pill')).toContainText('预览');
  });
});
