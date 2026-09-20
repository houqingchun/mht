# Phase 0 规则冻结与开发设计

## 1. 技术架构和模块边界

### 技术基线

- 前端：Vue 3 + TypeScript + Vite，使用单文件组件和 Composition API。
- 后端：Python + FastAPI + SQLAlchemy 2.x + Alembic。
- 数据库：MySQL 8.0。
- 认证：JWT Bearer Token 起步，生产可切换 HttpOnly Cookie + CSRF。
- 测试：Pytest 覆盖后端单元/API/权限，Playwright 覆盖四类角色主路径。
- 部署：Docker Compose，包含 frontend、backend、mysql。

### 后端模块

```text
backend/app/
  api/                REST API 路由与统一响应
  application/        用例服务：登录、任务、答题、关怀、导入导出
  domain/             领域规则：角色、状态、权限、关怀闭环
  scale_engine/       MHT 评分、效度、维度、重点题规则
  models/             SQLAlchemy ORM
  repositories/       数据访问
  security/           密码哈希、JWT、RBAC、数据范围
  audit/              审计日志写入与脱敏
  migrations/         Alembic 迁移
  tests/              单元、API、集成测试
```

### 前端模块

```text
frontend/src/
  app/                路由、布局、全局状态入口
  components/         通用表格、弹窗、表单、状态标签
  features/auth/      登录、当前用户、改密
  features/student/   学生首页、答题、历史、求助弹窗
  features/care/      工作台、重点学生、档案、复核、跟进
  features/admin/     组织、账号、题库、任务、审计
  features/analytics/ 领导统计与趋势
  services/           API client、错误处理、鉴权头
```

## 2. MySQL 8.0 表结构与迁移顺序

### 迁移顺序

1. `school`、`grade`、`class_group`、`student`
2. `user_account`、`user_scope`
3. `assessment_scale`、`scale_question`、`scale_rule`
4. `assessment_task`、`assessment_target`
5. `assessment_session`、`assessment_answer`
6. `assessment_result`、`dimension_result`
7. `risk_event`、`student_care_case`
8. `manual_review`、`follow_up_record`、`family_contact_record`、`retest_plan`
9. `audit_log`、`export_job`
10. 初始化数据：学校、年级、班级、四类演示账号、MHT 默认规则版本

### 关键约束

- `student`：`UNIQUE(school_id, student_no)`。
- `user_account`：`UNIQUE(account, account_type)`，只保存 `password_hash`。
- `scale_question`：`UNIQUE(scale_id, question_no)`。
- `assessment_answer`：`UNIQUE(session_id, question_id)`。
- `dimension_result`：`UNIQUE(session_id, dimension_code)`。
- `assessment_session`：同一任务同一学生只能存在一个未删除会话。
- 原始答案、计算结果、人工复核、跟进和家庭回访分表保存，不能互相覆盖。

## 3. 四类角色、登录和数据权限模型

### 角色

- `student`：只能访问本人任务、本人答题会话和本人完成状态。
- `counselor`：访问授权范围内学生心理详情、复核、跟进、家庭回访、复测。
- `leader`：查看学校聚合、重点进展摘要，不默认返回重点题、原始答卷、访谈正文。
- `admin`：管理组织、账号、题库、任务和审计，默认无心理明细读取权限。

### 登录规则

- 学生使用学号登录，账号类型为 `STUDENT_NO`。
- 心理老师、德育领导使用手机号登录，账号类型为 `MOBILE`。
- 系统管理员使用 `admin`，账号类型为 `ADMIN_USERNAME`。
- 登录接口必须同时校验 `role`、`account_type`、账号、密码和账号状态。
- 初始密码 `123456` 只用于开发初始化，入库必须为哈希。

### 账号从哪来（2026-09-17 补）

规格基线只写了「四类角色」与「管理员管理账号」，没有写**学生与员工的账号分别从哪里产生**，
实现上于是只有 `db/seed.py` 一个来源——一所真实的学校拿到这套系统之后加不了第二位心理老师。
现在两条路径分开，**且各只走一条**：

- **学生**：`组织学生 → 学生信息导入`（名册）。学生的身份属性是学号 / 年级 / 班级 / 年龄，
  只有名册知道；单独建一个学生只会得到一行没有学号、没有班级的账号。
- **心理老师 / 德育领导 / 系统管理员**：`账号与权限 → 新建账号`（`POST /admin/accounts`）。
  员工的身份属性是职责与所辖范围，与名册无关。
- 两条路径互不收对方的角色：`POST /admin/accounts` 收到 `student` 回 422 并指路，
  名册导入也不建员工账号。
- **数据范围必填**（`scopes` 至少一段），依据是同节的「所有业务查询必须通过后端数据范围过滤」：
  没有范围行的账号登录得进来但一个学生也看不到，是一个看起来正常、其实什么都没接上的账号。
- 角色与账号创建后不可改（`authenticate` 同时匹配 `account` / `account_type` / `role_code`）；
  没有删除接口，**停用**就是这条路的终点，它在下一个请求就生效（每次请求都重新校验账号状态）。

### 数据范围

`user_scope.scope_type` 支持：

- `SCHOOL`
- `GRADE`
- `CLASS`
- `STUDENT`

所有业务查询必须通过后端数据范围过滤。前端路由拦截只负责体验，不作为安全依据。

## 4. Scale Engine 接口和测试设计

### 接口草案

```python
class ScaleEngine:
    def validate_answers(self, answers: dict[int, str]) -> None:
        ...

    def calculate(
        self,
        questions: list[ScaleQuestion],
        rules: ScaleRuleSet,
        answers: dict[int, str],
    ) -> ScaleCalculation:
        ...
```

### 输出结构

```text
ScaleCalculation
  scale_version
  rule_version
  validity_score
  validity_status
  total_score
  total_level
  dimension_results[]
  risk_events[]
```

### 已冻结规则

- MHT 共 100 题。
- 答案只有 `YES` / `NO`，计分 `YES=1`，`NO=0`。
- 效度题：82、84、86、88、90、92、94、96、98、100。
- 内容总分排除 10 道效度题。
- 总分 `1-55` 一般范围，`56-64` 需要关注，`65+` 重点关注。
- 维度分 `0-3 LOW`，`4-7 MEDIUM`，`8+ HIGH`。
- 85、97 任一题为 `YES` 时创建 `MANUAL_REVIEW_REQUIRED`。

### 单元测试

- 全否、全是。
- 效度分 6/7 边界。
- 总分 55/56/64/65 边界。
- 维度 3/4/7/8 边界。
- 85、97 重点题命中与未命中。
- 答案不足、非法答案、重复提交幂等。

## 5. REST API 清单及错误码

### 统一响应

```json
{
  "success": true,
  "data": {},
  "request_id": "req_xxx",
  "error": null
}
```

### 认证

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/change-password`
- `GET /api/v1/admin/accounts`
- `POST /api/v1/admin/accounts`（2026-09-17 补，见 §3「账号从哪来」）
- `PATCH /api/v1/admin/accounts/{id}`（同上：改显示名 / 换数据范围 / 停用启用）
- `GET /api/v1/admin/accounts/scope-options`（同上：范围下拉框的选项，名字由服务端发）
- `POST /api/v1/admin/accounts/{id}/reset-password`

### 学生与组织

- `GET /api/v1/students`
- `POST /api/v1/student-roster/import/preview`（2026-09-19 改，原 `/students/import/preview`）
- `POST /api/v1/student-roster/import/commit`（同上）
- `GET /api/v1/student-roster/import/batches`（2026-09-19 加：批次历史）
- `GET /api/v1/student-roster/import/batches/{batch_id}/rows`（同上：逐行明细）
- `GET /api/v1/schools/{id}/statistics`

> 名册导入的三个旧路径（`/students/import/{template,preview,commit}`）**同批删除，不留并存期**
> ——同一动作两个入口是反复吃过亏的形状。`template` 那个端点没有调用方、没有测试
> （前端 `useDataImport.downloadStudentTemplate` 自带一份模板），一并删掉。

### 量表与任务

- `POST /api/v1/scales/import/preview`
- `POST /api/v1/scales/drafts`
- `POST /api/v1/assessment-tasks`
- `GET /api/v1/assessment-tasks`
- `PATCH /api/v1/assessment-tasks/{id}`
- `GET /api/v1/assessment-tasks/{id}/completion`

### 答题

- `GET /api/v1/student/tasks`
- `POST /api/v1/assessment-sessions`
- `PUT /api/v1/assessment-sessions/{id}/answers/{questionNo}`
- `GET /api/v1/assessment-sessions/{id}`
- `POST /api/v1/assessment-sessions/{id}/submit`
- `GET /api/v1/student/assessment-history`

### 关怀与统计

- `GET /api/v1/care-cases`
- `GET /api/v1/care-cases/{studentId}`
- `POST /api/v1/care-cases/batch-assign`
- `POST /api/v1/care-cases/{id}/reviews`
- `POST /api/v1/care-cases/{id}/follow-ups`
- `POST /api/v1/care-cases/{id}/family-contacts`
- `POST /api/v1/care-cases/{id}/retests`
- `POST /api/v1/care-cases/{id}/close`
- `POST /api/v1/care-cases/{id}/reopen`
- `POST /api/v1/care-cases/export`
- `POST /api/v1/care-cases/high-risk/export`
- `GET /api/v1/analytics/overview`
- `GET /api/v1/analytics/by-grade`
- `GET /api/v1/analytics/by-class`
- `GET /api/v1/audit-logs`

### 错误码

- `AUTH_REQUIRED`：未登录。
- `ROLE_FORBIDDEN`：角色无权限。
- `SCOPE_FORBIDDEN`：超出数据范围。
- `PURPOSE_REQUIRED`：敏感查看或导出缺少用途。
- `ANSWERS_INCOMPLETE`：答题数量不足。
- `ANSWER_INVALID`：答案不是 `YES` / `NO`。
- `IDEMPOTENCY_CONFLICT`：幂等键冲突。
- `ACCOUNT_LOCKED`：账号锁定。
- `VALIDATION_ERROR`：请求参数校验失败。
- `RULE_CONFLICT`：规则冲突，禁止继续处理。

## 6. 前端页面路由和组件树

### 路由

- `/login`
- `/student/home`
- `/student/assessment/:taskId`
- `/student/history`
- `/counselor/workbench`
- `/counselor/cases`
- `/counselor/cases/:studentId`
- `/counselor/tasks`
- `/counselor/data`
- `/counselor/analytics`
- `/counselor/audit`
- `/leader/overview`
- `/leader/progress`
- `/leader/analytics`
- `/leader/tasks`
- `/leader/audit`
- `/admin/system`
- `/admin/organization`
- `/admin/scale`
- `/admin/tasks`
- `/admin/audit`
- `/admin/accounts`

### 组件树

```text
App
  AuthProvider
  RouterView
    LoginPage
    RoleLayout
      SidebarNav
      TopBar
      StudentAssessmentPage
        QuestionCard
        AnswerOptionGroup
        SubmitConfirmDialog
        HelpDialog
      CounselorWorkbenchPage
        MetricStrip
        CareCaseTable
        QueueFilters
      CareCaseDetailPage
        StudentSummary
        CaseTabs
        SensitiveViewConfirmDialog
        ReviewForm
        FollowUpForm
        FamilyContactForm
        RetestPlanForm
      AdminPages
        ImportWizard
        AccountTable
        ScaleDraftPreview
      AnalyticsPages
        OverviewCards
        GradeClassCharts
```

## 7. 业务待确认项和规则冲突项

### TODO_BUSINESS_CONFIRMATION

- 密码策略具体值：最小长度、复杂度、失败次数阈值、锁定时长、历史密码数量。
- JWT 过期时长、刷新策略、是否改为 HttpOnly Cookie。
- `VALID` 与 `QUESTIONABLE` 的效度阈值：目前只明确 `validity_score >= 7` 为 `RETEST_RECOMMENDED`。
- 重点关注导出的默认字段清单、脱敏等级和文件过期时间。
- 心理老师授权范围由谁配置：管理员、德育领导或系统预置。
- 测评任务截止后是否允许学生补答，补答是否需要管理员或心理老师批准。
  **2026-09-17 实现侧先按「不再新开卷子」落地**（`create_or_get_session` 用
  `effective_task_status` 判定，见 CLAUDE.md §12）：过了 `end_at` 就开不了新的一张卷子，
  但**已经在答的学生不受影响**（那道门排在「已有会话直接返回」之后），
  补答的出路是心理老师改任务截止日期。若学校要「逐人批准补答」，需要新增一个审批动作
  （四个状态码里的 `PAUSED` / `CLOSED` 有词条、还没有写入方），这是留给业务确认的口子。
- 测评任务是否要给心理老师一个**人工结束/重开**的按钮（现在状态是现算的，
  只有「窗口 + 完成率」两条判据，人工裁决那一列没有任何写入方）。
- 关闭关注档案的可选原因枚举。
- 家庭回访字段是否需要字段级加密。
- 学生主动求助入口是否只记录请求，还是需要通知心理老师。

### RULE_CONFLICT

- `08_vibe_coding_plan.md` 建议前端使用 React + Ant Design；用户当前明确要求前端使用 Vue。处理结论：以用户最新明确要求为准，前端采用 Vue 3。

- **测评任务的角色权限**（裁决于 2026-09-16，**2026-09-17 推翻重裁**）：本文档「系统管理员：管理账号、组织、题库、任务和系统参数」表述为管理员独占任务管理，`tasks.py` 初版也把四个端点全部设为 ADMIN-only。但 `prototype.html` 的导航把「测评任务」同时配置给心理老师与德育领导，且两者都需要查看任务完成率；按原实现，心理老师点击自己的导航项会得到 403。
  **2026-09-16 处理结论**（已失效，保留备查）：写入类操作（`POST /assessment-tasks`、`PATCH /assessment-tasks/{id}`）保持 ADMIN-only；读取类操作（`GET /assessment-tasks`、`GET /assessment-tasks/{id}/completion`、`.../completion/export`）开放给 ADMIN + COUNSELOR + LEADER。实现见 `app/services/task_service.py` 的 `ensure_task_reader()`。

- **测评任务的角色权限（重裁，2026-09-17）**：用户指出 2026-09-16 那次裁决的前提是错的——**「管理员管理任务」这句话本身不成立**。测评任务是学校业务（业务闭环 `测评任务 → 学生答题 → 自动评分 → 风险提示 → 人工复核 → 跟进 → 家庭回访 → 复测 → 关闭/重开` 的第一环），而系统管理员只关注系统级配置。所以本文档与 `vibe-input/01_project_context.md` 里「管理……任务」的表述从现在起按**已作废**理解。
  **处理结论**：系统管理员**整块退出**这个功能面，读与写都不再包含 ADMIN。
  - 写（`POST` / `PATCH /assessment-tasks`）归**心理老师**——它是这条闭环的负责人。德育领导保持**只读**：它的能力集是学校级聚合与摘要（CLAUDE.md §4），是监督口径而不是运营口径。
  - 读（任务列表、完成明细、完成统计导出）保留给 COUNSELOR + LEADER。完成明细是逐人的行为数据（谁没答、用时多久），不是系统级配置。
  - 前端 `/admin/tasks` 路由与导航项一并删除；`TasksPage.vue` 的写按钮改为按 `counselor` 判定。
  - **连带改动，必须一起做**：`create_school_assessment_task` 发放目标行时改为按**创建者的数据范围**过滤（`student_scope_predicate`）。写权在管理员手上时「发给全体在读学生」与「发给管辖的全体」是同一件事，所以这条谓词一直缺失也没人发现；写权落到心理老师身上之后，一个只带 `CLASS` 范围的心理老师凭这个端点能给全校每个学生写下一行「你被安排了这次测评」。
  - 回归网：`app/tests/test_task_roles.py`（角色矩阵）、`app/tests/test_data_scope.py::test_task_targets_are_issued_within_the_creators_scope`（发放范围）、`e2e/app.spec.ts` 的「系统管理员的导航里没有测评任务」。

- **外部导入的 MHT 记录算不算「一场测评」**（裁决于 2026-09-16，**2026-09-17 推翻重裁**）：规格基线只写了「把校外测的结果拿进来一起看」，没有说拿进来之后它算不算系统自己的测评。
  **2026-09-16 处理结论**（已失效，保留备查）：算数据、不算测评——批次任务与会话都带 `source=IMPORTED`，导入路径**不调** `maybe_raise_risk_events`，所以「导入的测评不产生风险事件与关怀档案」。
  **推翻的理由**是这条口径把学校要的东西拿掉了：导入的记录在统计页上看得见、在关怀队列里看不见，而「一起看」的意思恰恰是**在同一个队列里看**。一条命中重点题 85 的记录不产生待办，等于让心理老师自己去数据里翻。
  **处理结论（2026-09-17）**：导入路径与系统内作答**同口径**判定，同样开出风险提示与关怀档案（`commit_assessment_import` 调 `maybe_raise_risk_events`）。`source` 保留——它回答的是「这条记录哪来的」（导出里的「来源」列、任务列表），不是「算不算数」。
  **随同一轮确认的两条**（用户在 2026-09-17 一并提出）：
  - **判重以月为单位**：同一个学生、同一个自然月只能有一次外部导入，不同月份是两场不同的测评、两个批次任务。此前按天判重且直接报错，学校把一次普查分两批导（先初一、隔几天再初二）时第二批被挡在门外，出路只有改系统日期。
  - **年龄不符与重复导入要问人**：这两类既不是错误也不是提示，而是需要决定的**冲突**，预览时列出来，提交时必须先选「覆盖上次」或「放弃冲突行」（未选则 422，且在**任何写入之前**拦下）。选覆盖时**用文件里的年龄更新名册**（写审计）——这一条是用户对「如果发现不一致应该询问是否覆盖更新还是放弃导入」的直接回答。
  - **同月存在多个历史批次时，复用最新那一批**（2026-09-17 补）：判重从按天改成按月之后，库里可能仍留着按天编号的旧任务（用户库里的九月就有 `IMPORT-20260916-1` 与 `IMPORT-20260917-1` 两行）。此时 `_task_for_month` 取 `id` 最大的那一行，与 `existing_import_session` 取最新会话对齐。此前一个取最老、一个取最新，于是提示与审计里的批次号说 A、而真正被就地改写的会话属于 B——轨迹答不上「这次导入动了什么」。
  - 回归网：`app/tests/test_assessment_import_api.py`（判重按月、冲突必须先确认、就地覆盖不新建、只收回未复核的待办、年龄写回带审计、同月取最新批次）、`e2e/app.spec.ts` 的「MHT测评记录导入」。

- **导入进去的记录在哪里看**（2026-09-17 补，用户报「当前没有一个视角查看所有学生的测试结果，只展示了重点学生和长期跟踪的展示」「我是刚刚导入了一个测评，但查不到这里的记录，比如张三」）：这是上一条「一起看」落空的地方——导入的判定口径已经和系统内作答一致，但**界面**上仍然查不到。
  原因不在导入链路：能看见**单个学生**的三个界面（工作台、重点学生、学生档案）都以 `student_care_case` 为入口，统计分析那一个又是聚合页、按 CLAUDE.md §11 刻意不下发身份。于是一名测出「一般观察」、因而不会开关怀档案的学生，在心理老师能到达的界面上不存在——而那正是绝大多数导入记录的样子。
  **处理结论**：给两种口径各一个落点，且都放在已有的页面里（不新增路由、不动导航）。
  - **按人**：「重点学生 → 全部学生」页签。整个名册都在（未测评的行显示「未测评」），每人一行取他**最近一场**；点开只给**已建档**的学生链接。
  - **按场**：测评任务的「查看明细」补上**这一场**的关注等级与总分。与上面那个页签刻意不同口径——一名学生落在两个批次里时两边各自显示各自的分。
  - 权限上它是**双门槛**（身份列归「组织与账号」、等级列归「学生心理详情」），刻意比同一页的 `GET /care-cases` 严一档：心理详情不得成为读组织名册的旁路，正如受控导出不得成为读心理详情的旁路。德育领导与系统管理员都拿不到。
  - 回归网：`app/tests/test_student_results_api.py`、`app/tests/test_task_completion_levels.py`、`e2e/app.spec.ts` 的「counselor can see every student, assessed or not」与 `e2e/vocabulary.spec.ts` 的「全部学生」页签。

## 8. Phase 1 开发任务

1. 初始化 `backend`、`frontend`、`data`、`docs/api` 目录。
2. 后端创建 FastAPI 应用、统一响应、错误码、健康检查。
3. 配置 SQLAlchemy、Alembic、MySQL 连接与 Docker Compose。
4. 编写首批迁移：组织、学生、账号、权限范围、审计。
5. 初始化四类演示账号，密码哈希保存。
6. 编写认证 API 和管理员重置密码 API。
7. 编写 Pytest：四类登录、角色不匹配、未登录、重置密码审计。
8. 前端创建 Vue 3 项目骨架、登录页和按角色跳转。

Phase 1 完成后再进入 Scale Engine 和答题流程。
