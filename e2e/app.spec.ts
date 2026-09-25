import { test, expect, type Locator, type Page } from '@playwright/test';
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

/**
 * 「关注等级分布」那张柱形图真的画出来了——**先证明有东西可扫，再断言它干净**
 * （§测试注意：这一条在本文件里已经栽过三次，最近一次是「全部学生」页签扫到了骨架屏那一帧）。
 *
 * **收的是那张图自己**（`ScoreBandBars.vue` 的根元素 `.band-chart`），不是 `page`。
 * 那张图现在挂在四个屏幕上——全校总览一张、年级页**一个年级一张**、班级页**本班与
 * 同年级各一张**——所以「页面上只有一张」这个前提只在总览那一处成立。第一版写死
 * `page.locator('.band-chart')`，在年级页上会直接撞 Playwright 的严格模式：它报的是
 * 「定位器命中 N 个元素」，而那句话听起来像页面画重了，不像「这份断言假设了一页只有一张图」。
 *
 * 调用点因此一律写成 `expectScoreBands(某个限定了作用域的 .band-chart)`——
 * 总览是 `page.locator('.band-chart')`，年级页是 `cell.locator('.band-chart')`。
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
    await expectScoreBands(page.locator('.band-chart'));
    await expect(page.locator('.task-option input:checked')).toHaveCount(1);
  });

  test('leader analytics entry opens the report center', async ({ page }) => {
    await loginAs(page, 'leader');
    await page.goto('/leader/analytics');
    await expect(page).toHaveURL(/\/leader\/analytics\/overview$/);
    await expect(page.getByText('已自动加载最新可分析任务')).toBeVisible();
    await expect(page.getByRole('heading', { name: '关注等级分布' })).toBeVisible();
    await expectScoreBands(page.locator('.band-chart'));
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
   * 所以除了逐格过一遍 `expectScoreBands`，还有两条**跨格的对账**：
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
      await expectScoreBands(chart);
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
    // `exact: true` 是必须的：`getByText` 是**子串**匹配，而「实际应测人数」既是这一格 KPI 卡的
    // 标签，也是覆盖率卡片脚注里那句分母口径（「可评价样本 ÷ 实际应测人数」）的一部分——
    // 不收严就是 strict mode violation。这不改变这条断言要说的事（报表默认加载并渲染出 KPI），
    // 只是把它从子串收成全等；脚注那句口径**不许**为了迁就定位器改措辞，那个词是
    // `eligible_count` 的正式中文名（`labels.ts` 的口径表）。
    await expect(page.getByText('实际应测人数', { exact: true })).toBeVisible();
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
    await expectScoreBands(classChart);
    await expectScoreBands(gradeChart);

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
    await page.getByLabel('1. 整体情况说明').fill('基于当前任务实际统计结果形成的专业说明。');
    await page.getByRole('button', { name: '保存草稿' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: '草稿已保存至服务器（RPT-' })
    ).toBeVisible();

    // ⑤ 导出：用途是 `window.prompt` 取的，所以先挂上对话框的应答再点。
    await page.getByRole('button', { name: '进入导出设置' }).click();
    page.on('dialog', (dialog) => dialog.accept('e2e：核对正式报告导出链路'));
    await page.getByRole('button', { name: '生成报告' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: '正式报告已通过导出中心生成并下载' })
    ).toBeVisible();
    // 导出记录那一行只在导出成功之后才渲染（`lastExportAt`），所以先证明它在。
    await expect(page.locator('.audit-section tbody tr')).toHaveCount(1);

    // ⑥ 发布：当前版本被锁定。先回「专业解读」那一页签。
    await page.getByRole('button', { name: '专业解读' }).click();
    await page.getByRole('button', { name: '确认并发布' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: '报告已发布，当前版本已锁定' })
    ).toBeVisible();

    // ⑦ 已发布不可覆盖（§5.4 的核心验收）：再点一次保存，服务端回 409
    //    `REPORT_IMMUTABLE`，界面把**服务端那句话**放到状态行上。
    await page.getByRole('button', { name: '保存草稿' }).click();
    await expect(
      page.getByRole('status').filter({ hasText: '已发布版本不可覆盖' })
    ).toBeVisible();
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
    await expect(page.getByRole('heading', { name: '重点关注学生与长期跟踪' })).toBeVisible();
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
      };
    });

    // 常规态：面板是那一档 lg（940px）+ 85vh，表格被页面压在 340px 里，而四行内容比它高。
    const normal = await measure();
    expect(normal.fullscreen, '常规态就挂着全屏类名，下面的尺寸断言证明不了任何事').toBe(false);
    expect(normal.width, '常规态的面板不该铺满视口').toBeLessThan(viewport.width);
    expect(normal.tableMaxHeight, '常规态表格的高度上限该是那一页写的那一档').toBe('340px');
    // 常规态是**被夹住**的。不断这一条的话，下面那句「全屏之后越过 340」就没有对照——
    // 一个两边都没夹住的实现（内容本来就高）也能让它通过。
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
