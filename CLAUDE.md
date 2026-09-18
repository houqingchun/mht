# CLAUDE.md

心晴 · 中学生心理测评与关怀平台。本文件记录项目的**不可破坏约定**与已知缺口，改动代码前请先读。

## 产品边界（最重要）

这是**心理健康筛查与学校关怀管理系统，不是医疗诊断系统**。任何页面文案、接口、提示词、AI 输出
都不得表达"患有某种心理疾病"等诊断结论。系统帮助学校发现需要关注的学生、组织人工工作、查看聚合
趋势，**不替代心理老师的专业判断**。

角色已冻结为四类：学生 / 心理老师 / 德育领导 / 系统管理员。**不得恢复"班主任"或"心理负责人"角色。**

业务闭环：`测评任务 → 学生答题 → 自动评分 → 风险提示 → 人工复核 → 跟进 → 家庭回访 → 复测 → 关闭/重开`

规格基线在 `vibe-input/`（Phase 0-8）。规则冲突与待确认项记录在 `docs/phase0_rule_freeze.md` 第 7 节。

## 常用命令

| 命令 | 说明 |
|---|---|
| `make install` | 安装前后端依赖 |
| `make dev` / `make backend` / `make frontend` | 启动服务（后端 8000，前端 5173） |
| `make test` | 后端 pytest（523 个测试） |
| `make e2e` | Playwright（113 个测试，需两个服务都在跑） |
| `make migrate` / `make seed` | Alembic 迁移 / 初始化数据 |
| `make seed-demo` | 填入演示数据（多年级班级、各分数段测评、各阶段档案），可重复执行 |
| `make reset-db` | 清空测评数据并重新种子（保留名册与账号） |
| `make purge-demo` | 把 `seed-demo` 填进去的一切删干净，回到只有 `seed.py` 基线的状态。**仅用于开发库** |
| `mysql … < backend/sql/reset_to_baseline.sql` | 清到「只有 admin + 基本配置」。给**别处的新环境**用，见 §16 |
| `make deploy-package` | 打 Windows 一键安装包 → `dist/心晴部署包.zip`。**在开发机上跑**，见 §18 |
| `python backend/run_server.py` | 生产启动器（`chdir` + 日志轮转 + 数据库等待 + 崩溃重试）。计划任务跑的就是它 |
| `make clean` | 清理编译产物 |

初始账号密码均为 `123456`：学生 `S001`、心理老师 `13800000001`、德育领导 `13800000002`、管理员 `admin`。
它们只是种子；真实的员工账号在「账号与权限 → 新建账号」里建（学生不在这里建，见 §4）。

## 架构

### 后端 `backend/app/` —— models → services → api/v1 三层

- `api/v1/` 只做路由、参数校验、权限依赖注入；业务逻辑在 `services/`；SQLAlchemy ORM 在 `models/`。
- `scale_engine/engine.py` 是**纯函数模块，不碰数据库**：输入题目配置 + 答案，输出 `ScaleCalculation`。
- `core/errors.py` 的 `ok()` / `error_response()` 负责统一响应封装。
- 迁移在 `alembic/versions/`，当前 0001–0012。
  **revision id 必须 ≤ 32 字符**（`alembic_version.version_num` 是 VARCHAR(32)）：超长不会在
  ADD COLUMN 处报错，DDL 先提交、版本戳的 UPDATE 才失败（MySQL 的 DDL 不在事务里），
  库变成"已迁移但仍记在上一个版本"，下次 upgrade 撞 Duplicate column。

### 前端 `frontend/src/` —— 无 Pinia、无 UI 库、无测试运行器

- `app/routes.ts` 定义 22 条路由，均带 `meta: { role, title }`。
- `features/` 按角色分目录（auth / student / care / leader / admin / analytics），
  **多个角色复用同一组件**（`TasksPage`、`AuditPage`、`DataCenterPage`、`AnalyticsPage`、`CareCaseDetailPage`）。
- `services/api.ts` 是手写 fetch 封装（无 axios / 无生成客户端），§"API 约定"见下。
- `services/labels.ts` 是后端枚举码 → 中文的**唯一**映射层，不得在各视图里重复定义。
- `composables/useSettings.ts` 是模块级单例，内置一份与后端 `DEFAULTS` 对齐的 `FALLBACK`。

### 部署 `deploy/` —— 出包、安装、运维（§18）

```
deploy/
  build_package.py          打 Windows 一键安装包（`make deploy-package`，在开发机上跑）
  requirements.lock.txt     目标机的**运行期**依赖，逐个钉死（pyproject 里全是 >=，无锁不可复现）
  windows/                  拷进包里的 Windows 侧资产
    一键安装.bat              ★ 操作员双击的那一个。纯 ASCII，只有文件名是中文
    部署说明.txt              面向操作员：三步、自检清单、「网页打不开」分诊表
    install.ps1              全部安装逻辑与中文提示（UTF-8 **带 BOM**）
    ops.ps1                  装完之后八个按钮背后的动作（start/stop/…/uninstall）
    sitecustomize.py         把 stdout/stderr 固定成 UTF-8（装进 venv 的 site-packages）
    ops/*.bat                八个三行按钮，装完由 install.ps1 拷到安装根目录再删掉源目录
    manual-start.ps1         手工启动（-Action backend|frontend）：一键安装装不完时的出路
    serve_frontend.py        只发前端时的静态服务器 + /api 反代（纯标准库）
    手工启动后端.bat 手工启动前端.bat  ★ 那个出路的两个入口。纯 ASCII（只有文件名是中文）
  README.md                  面向维护者：怎么重出包、加一个依赖要改哪两处
```

产物 `dist/心晴部署包.zip`（约 22MB）→ 拷到目标机 → 解压 → 双击。包里带着 Windows 版的
CPython、Windows 版的 wheel、构建好的前端，所以目标机上**不需要** Python、pip、网络，
唯一要有的外部东西是 MySQL 8.0。**运维文档面向两种人，是两份东西**：`部署说明.txt` 给
操作员（非技术），`deploy/README.md` 给下一个改这套东西的人。

## 不可破坏的约定

### 1. 四层事实模型，互不覆盖

| 层 | 表 |
|---|---|
| 原始答题事实 | `assessment_session` / `assessment_answer` |
| 量表计算事实 | `assessment_result`（含 `rule_version`）/ `dimension_result` |
| 学校管理事实 | `risk_event` / `assessment_task` / `assessment_target` / `student_care_case` |
| 专业工作事实 | `manual_review` / `follow_up_record` / `family_contact_record` / `retest_plan` |

**人工复核不得修改原始答卷；关闭档案不得删除历史记录。**

这条的后半句在 2026-09-17 被两个 bug 各证伪过一次，两个都修了：

- **`student_care_case` 上那个 `UniqueConstraint("student_id", "status")` 已删除**（迁移
  `0012`）。它把「同一时间只有一条在办」当成了数据库约束，而**已关闭**的档案恰恰会累积：
  上学期关掉的是一次完整的关怀过程，这学期再出状况是一条新的过程。约束还在时第二次关闭撞
  UNIQUE、`db.flush()` 抛 IntegrityError，用户拿到 500——**秋季关档、春季再关一次**是学校
  每年都会遇到的时序。现在「只有一条在办」由写入侧保证
  （`assessment_service.open_or_reuse_care_case` 先找非 CLOSED 的那条，找不到才新建）。
  删除那条唯一索引前必须先建 `ix_student_care_case_student_id`：它兼作 `student_id` 外键的
  索引，MySQL 会以 **1553** 拒绝删一条外键正在用的索引，而**这条错误只有真库会报**
  （内存 sqlite 不跑 Alembic，见缺口 3）。
- **`care_service.get_care_case` 不再过滤 `CLOSED`**，同一条学生的档案有多条时取 **id 最大**
  的那条（与 `open_or_reuse_care_case` 选「当前档案」的口径一致）。此前它 `where(status != CLOSED)`
  的写法让「关闭」这个动作把自己脚下的页面抽掉了：`closeCase()` 成功之后紧接着重新拉详情，
  拿到 404「**关注档案不存在**」，用户看到的就是这句话（2026-09-17 报的 bug）。同一次修复也
  让「重新打开」按钮不再是死代码，列表与详情不再各说各话。
- **同一个「关闭」，三个列表三种取舍，各自的理由都写在该处**（2026-09-17 捋清）：
  `get_care_case` **不过滤**（关闭之后这一页还得能看，否则关闭动作抽掉自己脚下的页面）；
  `list_care_cases` **不过滤**（它有「已关闭」页签，那一页签得有东西可列）；
  `analytics_service.leader_progress` **过滤**（那一页叫「重点进展」，而一条 CLOSED 的
  档案没有进展可看：`next_follow_up_date` 已不作数、`overdue` 本来就不算它）。
  过滤它同时修掉三处：列表只增不减（关过的档案会累积，在办的被挤到第二页）、德育领导
  总览那张「在办关注档案」把去年已了结的学生算成在办、同页「未分配负责人」把结案档案的
  无主算成待办。**这不违反「关闭档案不得删除历史记录」**——那条说的是别删行（个案详情
  照原样展示已关闭档案），不是每条列表都得列出来。要在领导那一页回看结案档案，正确做法
  是加一个「已关闭」页签（照 `CasesPage` 的 `QUEUE_TABS`），不是把过滤去掉。
  `test_analytics_api.py::test_leader_progress_lists_only_open_cases` 钉住它，并且先断言
  「关之前它在」——只写后半句的话，这条用例在没建出档案的库上也是绿的。

`student.masked_name` **不是隐私控制，不要拿它当遮蔽**。三条写入路径（`seed`、`seed_demo`、
`student_import_service`）写的都是真名，这一列在生产里和 `name` 一样实；它的用途只是花名册与
关怀队列的**展示名**。导出遮蔽由 `export_service.mask_student_name` 在读取时现算
（姓 + 「同学」），现算永远是对的，回填列则会再次漂移。

`student.age` 是**存下来的整数**（迁移 `0011_student_age` 把 `birth_date` 换成了它，
学生信息导入的最后一列随之从「出生日期」改成「年龄」；年龄列认 `13` 与 `13岁`）。
0009 当初选的是「存出生日期、读取时现算」，理由是学生过完生日那一刻存下来的年龄就错了；
2026-09-17 按学校要求反过来——**学校手上的名册只有年龄，现算没有输入可算**。
代价是知情的：**这一列不会自己变**，不重导名册，界面上的年龄就停在去年。所以
`assessment_import_service` 按年龄消歧时保留 ±1 岁的容错（那份文件可能是去年那次普查），
而且它不再有「按测评日期算」这个口径可讲——名册那边只有一个数。
**写入方因此有两个**：学生信息导入（整份名册）与 MHT 测评记录导入（`AGE_MISMATCH` 冲突选了
「覆盖」时，只改这一名学生的年龄，`_update_roster_age`，写审计「更新学生年龄」）。
后者是 2026-09-17 用户要求的那一条（「如果发现不一致应该询问是否覆盖更新还是放弃导入」），
详见缺口 8。老模板（末列还叫「出生日期」）
会被逐行挡下来：`DictReader` 会把那一列读成没人认领的键，年龄于是整列 NULL，而每一行都 NULL
和「学校没填」长得一模一样。

### 2. 统一响应封装

所有接口返回 `{ success, data, request_id, error }`。前端 `api.ts` 统一取 `body.data`，
错误取 `body.error.message` 抛 `Error`。**注意前端拿不到 HTTP 状态码**，无法区分 401 与 500。

**但「服务端答了话」与「一个字都没收到」是分得开的**（2026-09-17 补）。`fetch` 本身的失败
按规范抛 `TypeError`（后端没起来、网断了、跨域被拒），响应体不是 JSON（网关那张 HTML 错误页）
抛 `SyntaxError`，其余情况 `err.message` 就是服务端按统一封装发回来的 `error.message`
——**那句话本来就是写给用户看的**。登录页按这条分（`loginFailureMessage`）：
在此之前它把**一切**失败都报成「账号、角色或密码不正确」，于是「后端没起来」和「密码打错了」
在屏幕上长得一样，用户会一直重打密码，而问题在别处。
这不是放松——服务端 401 的原文就是那一句，改前改后逐字相同；变的是 500 之类的故障
第一次有机会说出自己的名字。

### 3. 状态词汇契约（改枚举前必读）

后端返回的状态 / 等级 / 维度编码必须能被 `frontend/src/services/labels.ts` 映射。
`backend/app/tests/test_status_vocabulary.py` 把守这条契约——它记录了一次真实回归：
前端曾期望 `FOLLOWING_UP` 而后端发 `FOLLOWING`，导致界面显示原始编码、队列筛选静默失效。

维度是**英文编码**（`LEARNING_ANXIETY` 等八个），**不是 A–H 单字母**。
`scale_engine.dimension_for_question` 与 `labels.ts` 必须一致。

**这条契约有两个面，两边都要守**（2026-09-16 补）：

| 面 | 谁在守 | 漏了会怎样 |
|---|---|---|
| 后端发出的编码 `labels.ts` 认不认 | `test_status_vocabulary.py` | 界面显示原始编码 |
| 视图有没有**调用**标签函数 | `e2e/vocabulary.spec.ts` | 界面显示原始编码 |
| 服务端生成的导出文件认不认 | `test_export_labels_match_frontend.py` | 导出的 CSV 里是 `MALE`、`FOLLOWING` |

第二面是第一次全站普查（8 个页面漏码）暴露出来的：后端测试断言的是接口 payload，
它看不见组件里写的 `{{ detail.case_status }}`——后端发完全正确的 `FOLLOWING`，
那个测试照样全绿。所以 `e2e/vocabulary.spec.ts` 不看 payload 看**像素**：
按角色走完全部页面，断言正文里不出现任何一个必须被翻译的编码。

第三面是 2026-09-16 补的。前两面都只覆盖界面：受控导出与任务完成明细这两个 CSV 由
**后端**拼出来，界面上写着「重点关注」，同一份数据导出成文件就是 `KEY_ATTENTION`——
而没有任何一条测试看得见文件里写了什么。后端 import 不了 `.ts`，所以
`services/export_labels.py` 是 `labels.ts` 里那五张表（性别/关注等级/档案阶段/任务状态/测评来源）
的一份镜像，`test_export_labels_match_frontend.py` **把那份 TypeScript 当数据源读进来**
逐字比对。改中文要同时改两边。
`levelLabel` 的既有约定一并沿用：没有结果出「未测评」而不是空单元格，
认不出的码原样回退而不是留白（漏码要看得见）。数值列（年龄/用时/总分）是另一套约定：
没有记录就留空，`—` 会让整列被表格软件当成文本。

第五张表 `SOURCE_LABELS`（`IN_SYSTEM`/`IMPORTED`，2026-09-16 随「MHT测评记录导入」补）
2026-09-17 之前只受**第一面与第三面**保护，第二面在它上面是空的：e2e 的演示数据里没有一条
`IMPORTED` 记录，而三处渲染（个案详情、任务列表、学生的记录页）都是 `v-if` 条件渲染，所以
`sourceLabel` 就算漏调也不会变红。
**现在第二面在 `IN_SYSTEM` 上有了信号**：「全部学生」页签（§4 那个双门槛端点）的来源列
不条件渲染，**每一行**都出「系统内作答」或「—」，而演示数据里全是 `IN_SYSTEM`。
`UNTRANSLATED_CODES` 因此加上了 `IN_SYSTEM` 与 `IMPORTED`——后者要等哪天演示数据或某个
用例真带进一条导入记录才会开始生效（那时 `v-if="source === 'IMPORTED'"` 的那三处也会被覆盖）。
「先真的导一次」（先得有名册里的 `704`）这条路仍然没走，仍然不必走。

**同一处还欠一张表**（2026-09-17 补）：`IMPORT_CONFLICT_LABELS`（`AGE_MISMATCH` / `DUPLICATE`，
`importConflictLabel()`，渲染在数据中心导入明细的「待确认」列）。它连第一面与第三面都不需要——
这两个码是后端在**预览**里发的，既不进导出文件，也不出现在任何已冻结的枚举表里，所以它**只有
第二面能保护**，而第二面现在是空的：那两列只在「查看明细」弹层里渲染，e2e 要走到那里得先让库里
存在一条本月导入过的记录。在共享的开发库上「先导一条进去」会让这次运行**改变**后续用例看到的
统计口径（缺口 8），所以没有为它写 e2e 用例——这是有意的取舍，不是忘了。后端那一侧是钉住的
（`test_assessment_import_api.py` 里冲突类型逐字断言），漏的是「视图有没有调用标签函数」。

新增枚举 / 新增页面时两边都要补词条与路径，否则回归网只覆盖它认识的那一半。

**第四面：枚举列的排序（2026-09-17 补）。** 上面三个面守的都是「码有没有被翻译成中文」，
没有一面守得住「这一列按什么顺序排」——**中文对了，顺序仍然是错的**，而且错得看不出来：
`DataTable` 的默认排序拿 `row[column.key]` 去 `localeCompare`，所以「关注等级」升序拿到的是
`GENERAL_RANGE` / `KEY_ATTENTION` / `NEEDS_ATTENTION` 的**字母序**，显示成
「一般观察 → 重点关注 → 需要关注」。那看起来只是个正常的升序，没人会怀疑它错了。
（它此前一直是这样，`DataTable.sortValue` 那个 prop 全站零调用。）

判据是 `Column.order`（`DataTable.vue`）：可排序的枚举列**必须显式声明**，值传 `labels.ts` 的
`*_ORDER`——它们由那些映射表的**键序**生成，所以加新码时自己跟上。**那几张表的键序是有意义的**
（从重到轻 / 按流程先后），加新码时想清楚它排在哪一端，不要顺手追加到末尾：

| `*_ORDER` | 键序 |
|---|---|
| `LEVEL_ORDER` | 重点关注 → 需要关注 → 一般观察（**从重到轻**） |
| `CASE_STATUS_ORDER` | 待复核 → 跟进中 → 观察中 → 已关闭（流程先后） |
| `TASK_STATUS_ORDER` | 草稿 → 未开始 → 进行中 → 已暂停 → 已结束（生命周期） |
| `SCALE_STATUS_ORDER` | 草稿 → 已发布 → 已归档（发布流程） |
| `STUDENT_STATUS_ORDER` | 在读 → 已离校 |
| `SOURCE_ORDER` | 系统内作答 → 外部导入（先自家后外部） |

`TASK_STATUS_LABELS` 与 `SCALE_STATUS_LABELS` 的键序 2026-09-17 为此**调过**（前者原本
`ACTIVE` 打头、后者原本 `PUBLISHED` 打头），不是新加的键。空值与认不出的码排在**最后，两个方向
都是**——它们不属于这个序的任何一端，混进中间会让「最轻的那一档」看起来像有值。

守卫是 `e2e/app.spec.ts` 的 `enum columns sort by their Chinese order, not by code`：
在「全部学生」页签上点两次「关注等级」，断言升序首行是**重点关注**、降序首行是**一般观察**
（字母序升序的首行是「一般观察」，两条一起才排得掉偶然），再点「来源」断言首行是「系统内作答」，
最后把每页调到 100 断言末行是「未测评」。变异验证：摘掉那一列的 `order` → 红；
把 `DataTable` 里的 `order` 分支改成恒假 → 红。

**两处**同类的按码序**没修**，是有意的，别当成漏了：

- 审计页的「角色」列：那一张表是**服务端排序**（§10，`audit.py` 的 `sort` 白名单），
  `ORDER BY actor_role` 排的是编码，`Column.order` 到不了那里。要修得在 SQL 里加
  `CASE` 表达式，是另一套机制。
- `ScalePage.vue` 的 `SCALE_STATUS_LABELS` 曾在本文件里**又抄了一份**（同名 `STATUS_LABELS`，
  键序还是另一个），2026-09-17 随这次排序一起删掉，改用 `labels.ts` 的
  `scaleStatusLabel` / `scaleStatusTone`——那张表一直在这里，只是**没有取用它的函数**，
  视图于是自己抄了一份。这是 §3 第一面的反面：**两边都要补词条与路径**，表在 `labels.ts`
  里而没人从那儿取，也算没接上。

**「高度关注」不是第二个等级名，是一个**功能名**（2026-09-17 裁决）。** 等级只有一套中文：
`KEY_ATTENTION` 一律叫「重点关注」（`labels.ts`）。但那个导出功能在按钮、弹层标题、
后端错误文案与**审计动作码**（`导出高度关注摘要`）里都叫「高度关注导出」，于是同一个屏幕上
这两套字并存（按钮写着高度关注，下面的药丸写着重点关注），审计条目里还有 6 处硬编码
（见缺口 7 那一类）。

**不改名的理由，不是懒：** 审计动作码是**已经落库的历史**，用户在审计页按眼睛看到的名字
搜（`GET /audit-logs` 的 `q` 匹配的是 `action`）。把界面上的名字改掉、审计码不动，正好落进
缺口 7 那条教训——**把「看不懂」换成了「搜不到」，更糟**。两边一起改则让同一段历史分裂成
两种动作名，轨迹要拼两次才读得全。

所以处置是**在用户将要动手的那一处写出等号**：三个导出入口（工作台、重点学生、数据中心）
都有一行「「高度关注」就是关注等级里的**重点关注**（同一档）」。新增导出入口时照样补这一行。

### 4. 权限：能力矩阵 + fail-safe 回退

五类能力在 `role_permission` 表中按角色配置，实现在 `security/permissions.py`：

| 能力 | counselor | leader | admin | student |
|---|---|---|---|---|
| 聚合统计 `AGGREGATE_STATS` | SCOPED | SCHOOL | NONE | NONE |
| 学生心理详情 `STUDENT_PSYCH_DETAIL` | SCOPED | SUMMARY | NONE | NONE |
| 重点题/原始答卷 `KEY_QUESTIONS` | GRANTED_WITH_AUDIT | NONE | NONE | NONE |
| 组织与账号 `ORG_ACCOUNT` | READ_BASIC | READ_SUMMARY | MANAGE | OWN |
| 受控导出 `CONTROLLED_EXPORT` | PSYCH_SUMMARY | PROGRESS_SUMMARY | BASE_ONLY | NONE |

- **表空、缺记录、或查询抛 `SQLAlchemyError` 时一律回退 `CAPABILITY_DEFAULTS`**，绝不 fail-open 成"全部允许"。
- 描述性等级**不可互换**：`SCOPED` 是全量档案访问，`SUMMARY` 只是聚合。暴露明细的端点必须显式传
  `allow={SCOPED}`（见 `api/v1/care.py`），否则 admin 的 `BASE_ONLY` 会被静默提升。
- **导出解除姓名遮蔽同样要 `STUDENT_PSYCH_DETAIL: SCOPED`**（`export_service`）。
  德育领导的 `CONTROLLED_EXPORT` 是 `PROGRESS_SUMMARY`，它在 `/students` 上连按行姓名都拿不到，
  却能靠 `{"mask_names": false}` 导出一份实名名单——受控导出不能成为绕过心理详情的旁路。
  角色能导出**摘要**不代表它能导出**实名明细**。
- **`GET /students/results` 要两道门槛，缺一不可**（2026-09-17 加，全部学生页签用的那个端点）：
  `STUDENT_PSYCH_DETAIL: {SCOPED}` **且** `ORG_ACCOUNT: {MANAGE, READ_BASIC}`。
  响应里同时有**身份列**（学号/姓名/年级/班级，归组织与账号）与**等级列**（关注等级/总分，
  归心理详情），所以它是上面那一条的镜像——**心理详情同样不得成为读组织名册的旁路**。
  与同一页上的 `GET /care-cases`（只查 `STUDENT_PSYCH_DETAIL: SCOPED`）相比严一档，
  这是**有意的**：`care-cases` 给的是已经开档的那批学生，而这一个给的是整份名册。
  拦住的是谁：德育领导（名册是 `READ_SUMMARY`、心理详情是 `SUMMARY`）与系统管理员
  （有 `ORG_ACCOUNT: MANAGE`、没有心理详情）。
  该能力在**两处**各查一次（`students.py` 的路由依赖 + `analytics_service.ensure_student_result_reader`，
  与 `care.py` / `care_service` 的既有形状一致），所以单独拆掉任何一处都不会让测试变红——
  两处一起拆才会。别把「拆了一处仍然全绿」读成守卫失效。
- **测评任务不是一个能力，是角色**（2026-09-17 裁决）：它是学校业务而不是系统级配置，
  所以**系统管理员读写都不包含它**——写归心理老师（业务闭环的负责人），读归心理老师 +
  德育领导。`docs/phase0_rule_freeze.md` §7 记了这次重裁，被推翻的 2026-09-16 版也留在那里。
  连带改动：`create_school_assessment_task` 发放目标行时按**创建者的数据范围**过滤（§9 的写侧）。
  写权还在管理员手上时「发给全体在读学生」与「发给管辖的全体」是同一件事，所以那条谓词
  缺失了很久没有任何东西能发现它。
- **可选等级由 `security/permissions.py` 的 `CAPABILITY_LEVELS` 定义，权限矩阵那五个下拉框
  从它渲染**（2026-09-17）：此前每格都是自由文本，一个「查看汇总」的 typo 会被原样存进
  `role_permission`，而 `scope_allows` 读不懂它——按 fail-safe 回退成**更低**的等级，
  界面照常显示用户输入的那串字。于是「配好了但没生效」是静默的。现在写入口收窄到定义域内，
  且 `test_permissions.py` 把守「两张表一致」：`CAPABILITY_DEFAULTS` 里的每个能力键
  都必须在 `CAPABILITY_LEVELS` 里有定义，否则新增能力时很容易漏掉它的合法等级表。
- 系统管理员不能移除自身的「组织与账号」权限（自锁防护）。
- **后端鉴权是最终权限来源，前端隐藏不是安全措施。**
- **「系统管理」那个页面 2026-09-17 改名「账号与权限」（`/admin/system`）**：它此前把
  「和系统有关的」全吸了过去，于是学生导入、题库导入、审计各在这些功能自己的页面上
  又留了一份副本，同一个动作有两个入口。现在它只剩账号管理与权限矩阵两件事，
  `e2e/app.spec.ts` 的 `admin logs in straight to 账号与权限` 钉住落地页。
- **学生信息导入只有一个入口：「组织学生」（`/admin/organization`）**（2026-09-17 删掉
  数据中心里的那份副本）。那份副本是 `v-if="isAdmin"`，而 `/counselor/data` 的
  `meta.role` 是 `counselor`、`AppLayout.vue` 会把不匹配的角色弹回登录页——
  **两边都对不上，所以它对任何角色都不显示**：管理员到不了那一页，心理老师到了那一页
  `isAdmin` 是 false。它此前一直挂着「两个入口都要能回答覆盖/放弃」的注释，
  而那个前提本身是假的（实测：admin 访问 `/counselor/data` 停在 `/login`，
  页面上匹配「学生信息导入」的标题为 0）。删的是副本，功能一直在。
  教训与上面那条同源但更隐蔽：**改名/搬页之后要问一句「旧的那份还到得了吗」**，
  到不了就是死代码，而死在 `v-if` 里的东西两个 e2e 角色用例都不会发现——
  它们各自走的是自己够得着的页面。`DataCenterPage.vue` 顶部与
  `OrganizationPage.vue` 的注释都记了这一条。

#### 员工账号：两条创建路径，只有一条长期成立（2026-09-17 补）

用户报的是「**学生可以导入，但心理老师、德育领导没有添加人员的入口**」。成立，
而且是**两个面的同一个洞**：`POST /admin/accounts` 这个端点此前**根本不存在**，
所以心理老师与德育领导的账号只有 `db/seed.py` 一个来源——一所真实的学校拿到这套
系统之后，加不了第二位心理老师，也换不掉已经离开的那一位。

| 角色 | 从哪来 | 为什么 |
|---|---|---|
| 学生 | **名册导入**（组织学生 → 学生信息导入） | 学生的身份属性是学号 / 年级 / 班级 / 年龄，这些只有名册知道；单独建一个学生只会得到一行没有学号的账号 |
| 心理老师 / 德育领导 / 系统管理员 | **「账号与权限」→ 新建账号** | 员工的身份属性是职责与所辖范围，与名册无关 |

**学生那一侧不为员工开口子。** `create_account` 收到 `role_code=student` 时给的是
一句指路的 422（「学生账号不在这里创建：学生跟着名册一起生成（组织学生 → 学生信息导入），
在那里他才有学号、班级和测评记录」），而不是一个「也行」的接受——否则走那条路建出来的
学生会是一个**没有学号也没有班级**的账号，登录得进来，测评记录里认不出他是谁。
反过来说，`POST /admin/accounts` 也不接受学生，所以「学生在哪建」这个问题只有一个答案。

**数据范围是必填的，不是选填的。** 这是 `security/data_scope.py` 的直接后果：
谓词在「用户没有 scope 行」时**恒假**（§9），所以一个没有范围的员工账号登录得进来、
每个列表都是空的——它比「少配了一项」严重得多，它造出来的是一个**看起来正常但什么都没
接上**的账号，而使用者只会以为自己权限不够。所以：

- 服务端 `scopes` 是 `min_length=1`，且**先把范围解析完再落账号行**
  （「范围先解析再落账号」，一个不存在的 grade_id 不会留下半写好的账号）；
- 界面那一个下拉框下面有一行小字写明这件事，而且**拉不到选项就拒绝打开弹层**
  （一个空的范围下拉框会把「必填」变成死路）；
- 列表上范围为空的行出琥珀色的「**未配置 · 看不到任何学生**」，不是与别的行长得一样的 `—`。

`STUDENT` 级范围在这个入口被拒（422）：把范围切到单人级要重新想反推风险（缺口 1）。

**角色与账号创建后不可改**（`update_account` 只收 `display_name` / `scopes` / `active`）。
`authenticate` 是拿 `account + account_type + role_code` 三个一起匹配的，改其中任何一个
都会让那个人的登录凭据在一夜之间失效，而轨迹上只留下一行「更新账号」。录错了就停用那一个、
另建一个——那会留下**两条**审计，比一条静默改写的轨迹更接近发生过的事。

**停用 ≠ 删除，而且停用是立即生效的。** 没有删除接口：`audit_logs` / `manual_review` /
`follow_up_record` 都指着 `user_account`，删一行会把这些全部悬空（库里没有 `ondelete=`）。
停用够用，因为 `get_current_user` **每一个请求**都查一次 `active`，
所以停用不需要等 token 过期——`test_account_admin_api.py` 里那条「停用后下一次请求就 401」
钉住它（这也正是不用 `DELETE` 的理由）。停用时顺手清掉 `failed_attempts` / `locked_until`：
一个被锁着的账号在重新启用之后不该还锁着。**管理员不能停用自己**
（「不能停用自己的账号：停用之后就没人能再进入这一页了」）——与「不能移除自身的组织与账号
权限」是同一类自锁防护，但这一条排在服务端而不只是界面上。

**审计用四个动作码，不用一个。** `新建账号` / `更新账号` / `停用账号` / `启用账号` 分开写，
其中「停用/启用」**另起一条**而不是并进「更新账号」：审计页的搜索匹配的是 `action`
（§10、缺口 7），把停用塞进「更新账号」会让操作员按眼睛看到的那个按钮名搜不到它。
账号与角色写在 `detail` 里（`role=counselor; scopes=SCHOOL=1`，用编码不用中文，
与 `update_permissions` 的例子一致）；**密码一律不进 `detail`**——
`detail` 是会被导出的文本，写进去等于把凭据复制一份。

### 5. 配置：回退 DEFAULTS，绝不 fail-open

`system_setting` 表按 `(namespace, key)` 逐项存储，**写入时若值等于默认值则删除该行**，表里只留偏离项。
表为空或读取失败时回退 `services/settings_service.py` 的 `DEFAULTS`——绝不因为读不到配置就把界面清空。

读取对所有已登录用户开放（否则非管理员会静默回退到硬编码字面量，配置对他们等于失效）；
写入限于「组织与账号 / 管理」能力。登录页需要的校名走免认证的 `GET /public/branding`。

**校名有两个家，界面上要显示的时候一律读配置里的那一个**（2026-09-17 补）：

| 家 | 谁写的 | 界面上出现过吗 |
|---|---|---|
| `school.name` | `db/seed.py`、学生导入（`school_for_import`，缺口 2） | **从来没有** |
| `system_setting` 的 `org.school_name` | 管理员在「账号与权限 → 系统配置 → 机构标识」里改 | 登录页、顶栏，以及用户改的那一格 |

用户报过：「添加账号时，数据范围还显示了青禾实验学校，与我在配置中写的第十三中学不匹配」，
而 `auth_service.scope_options` / `resolve_scope_names` 正是读的 `school.name`——
那是全站**唯一**渲染它的地方。现在两处都走 `_school_display_names`（读配置，
读不到才回落 `school.name`）：同一个东西在一屏上不能有两个名字，与 §9 那条「口径要写进
界面」是同一条道理。多校部署时这里要回落到「每所学校各自的显示名」，那时 `school.name`
才真正成为该读的那一侧（缺口 2）。

**没有**把配置值写回 `school.name`：那一行由种子 / 导入维护，界面上没有编辑它的入口，
写回去等于让一个只读列去追一个可写配置，下次改配置又要再写一次。
`test_account_admin_api.py::test_the_scope_name_follows_the_configured_school_name` 钉住
两个读点（范围下拉 + 账号列表的范围列），并顺带断言 `school.name` 没被改动。

### 6. 阈值属于量表规则版本，不属于系统配置

总分分段、维度分段、效度重测阈值随 `scale_rule.config_json` 走，**不要放进 `system_setting`**。
原因是 `assessment_result.rule_version` 要能回答"这条结果当时按什么标准判定"；
放进通用配置表会让一次误改追溯改写所有历史结果的解释。

- 引擎评分时读取该版本的 rule config，**不再是硬编码**。
- 改**已发布**版本的规则 → 生成新版本（`1.0.0 → 1.0.1`），旧版本标 `RETIRED` 保留；已有结果不受影响。
- 改**草稿**版本的规则 → 就地修改（还没有结果用它算过）。
- 保存前校验分段连续、无重叠、无断档，且效度题/重点题必须在量表实际题号范围内。
- **规则标识统一由 `scale_rule_service.rule_version_for(code, version)` 生成**
  （`MHT` + `MHT-1.1.0` → `MHT-RULE-1.1.0`）：种子、规则编辑、题库导入三个创建方都走它，
  免得三个地方各写一种形状。**标识里不得出现生命周期词**——规则行比草稿活得久，
  量表发布之后它仍然是 `ACTIVE` 的那一行，`MHT-1.1.0-RULE-DRAFT · 生效中` 这种自相矛盾的
  标签就是这么渲染出来的（2026-09-16 修）；而且 `_bump_version` 取**尾部**数字，
  以词结尾的名字会 bump 成 `...-RULE-DRAFT-2`，从此不再像版本号。

### 7. 草稿不生效，发布归管理员

题库导入只创建 `status="DRAFT"` 的版本，不参与评分，新建任务也不会选中它。
只有 `POST /scales/versions/{id}/publish` 把它变成 `PUBLISHED`，同时把同量器上一个已发布版本转为
`ARCHIVED`（保留，不删除）。**导入开放给管理员+心理老师，发布与改规则只归管理员**，两者都写审计。

### 8. 敏感读取：先写审计，后返回数据

- 读单个学生档案（`GET /care-cases/{student_id}`）**每次读都写审计**。
- 重点题原始答卷（`GET /students/{id}/key-questions`）**要求 `purpose` 查询参数**，且审计在返回数据之前写入。
- 导出（`POST /care-cases/export` 等）同样要求 `purpose`。
- **导出审计必须记录遮蔽模式**（`_export_detail` 拼进 `detail`）：同样的 action、
  同样的 resource_type、同样的 purpose，遮蔽与实名两条行长得一模一样，轨迹就答不上
  「这份文件是不是实名的」。`purpose` 是自由文本，担不起这个字段。
  **但这条轨迹目前只有数据库看得见**（2026-09-17 发现，未修）：`GET /audit-logs`
  的序列化里**没有 `detail`**（`api/v1/audit.py` 只发 id/action/resource_type/
  resource_id/actor/purpose/created_at），审计页也不渲染它。所以遮挡模式、导入的
  `created/updated/skipped` 与 `resolution`、年龄覆盖的旧新值——**全部写进了库，
  没有任何接口或界面能读出来**。这是「已记录」与「可追溯」之间的差距，补它要给
  `/audit-logs` 加一个 `detail` 字段并想清楚它该不该受权限约束（同一张表里既有
  「登录失败」也有「查看了谁的档案」），所以没有顺手加。
- **不在日志里打印完整答卷、重点题回答或家庭回访正文。**
- **审计行要能回答「谁做的」，不只是「哪个角色做的」**（2026-09-17 补）：`audit_logs` 只存
  `actor_user_id`，`actor_role` 回答不了「一所学校里三位心理老师，是谁看的」。所以
  `api/v1/audit.py` 在返回前把那一批 `actor_user_id` **一次查出来**（不是逐行查）拼上
  `actor_name` / `actor_account`，界面第一列「操作人」渲染「姓名 · 账号」。
  未登录的行为（登录失败、系统事件）没有操作人，界面显示 `—`、**导出时留空**——
  `—` 是界面占位符，写进 CSV 会让整列被当成文本（§3 数值列那条约定）。
  顺带一提：`seed_demo` 的 `display_name` 就是角色名（「心理老师」），所以演示库里
  这一列会读成「心理老师 · 13800000001」。**那是演示数据的性质，不是这一列没接上**；
  真实名册下它显示的是人名。
- **`GET /care-cases/{student_id}/comparison`（班级对照）同时踩 §8 与 §9 两条**：它暴露
  **一名学生**的八维度得分，所以既要 `STUDENT_PSYCH_DETAIL: SCOPED`（不是 `SUMMARY`）、
  又要 `ensure_student_in_scope`，并且**每次读都写审计**（action `查看班级对照`、
  resource_type `STUDENT_CARE_CASE`）。归在 `/care-cases` 而不是 `/analytics` 就是这个原因——
  它返回的是个体明细，不是聚合。被拒时**不写审计**（同 §9）。

### 9. 数据范围：两种入口，一个定义

`security/data_scope.py` 回答"**这个**学生归不归**这个**用户管"，与 §4 的能力矩阵互补——
能力回答"心理老师能不能看个案详情"，范围回答"这位心理老师能不能看这名学生"。

**两个入口，缺一个就漏一种洞：**

| 入口 | 用在 | 语义 |
|---|---|---|
| `ensure_student_in_scope(db, user, student_id)` | 客户端传来的 ID | 取到之后校验，否则档案 ID 本身就成了越权凭据 |
| `student_scope_predicate(db, user)` | 列表 / 聚合查询 | 加进 `WHERE`，把结果集收缩到用户范围 |

`student_in_scope` **建立在谓词之上**，所以守卫与过滤器不可能各说各话——列表里出现的
学生，个案接口一定也接受。代价是每次多一条 SELECT，值得。

- **谓词绝不返回 `None`。** scope 表读不到、或用户没有 scope 行 → 恒假谓词。返回 `None`
  会让调用方读成"不用过滤"而 fail-open，那是这一层唯一不能出现的结果。
- `ScopeType` 四个级别各自只匹配自己的维度，`is not None` 判断是必要的：grade_id 为 NULL 的
  scope 行不能被读成匹配 grade_id 也为 NULL 的学生，否则半填的行就授予了一切。
- 拒绝时**不写审计**：给一次被拒的读取记上"查看重点题"，会让访问轨迹反过来撒谎。
- **口径要写进界面，不只是写进注释；而被过滤的列表，它的标题本身就是一句口径宣称**
  （2026-09-17）。「全部学生」这四个字会把人带向「全校」——一个只带 1 个班范围的心理
  老师会以为这所学校只有 40 个学生。它现在在页头写「范围：全校 / 你负责的班级 …」，
  依据是 `/auth/me` 的 `scopes[]`：**那一列一直在响应里，只是此前 `api.ts` 没有类型
  所以没有读者**（`CurrentUser.scopes` 2026-09-17 补上）。多行范围由 `student_scope_predicate`
  用 `or_` 合并，所以界面上是并列（并集，比任何单行都大）；取不到 `scopes` 时**整句不出现**，
  不猜一个「全校」。读法与任务的「对象范围」不同，因此 `labels.ts` 里是第二张表
  （`USER_SCOPE_LABELS`：任务说「按年级」，用户的范围要说「你负责的年级」）——
  同一批编码，两种读法，仍归那唯一的映射层。
- **聚合数字的口径跟角色走**：`SCOPED`（心理老师）的比率描述他自己的授权范围，`SCHOOL`
  （德育领导）的描述全校——这正是 §4 词汇表里已有的区分。范围数字必须在 UI 上写明口径，
  否则它冒充全校数字，比不给数字更糟。
- `AssessmentResult` / `DimensionResult` 没有 `student_id`，要经 `AssessmentSession` 两跳到
  `Student`；`audit_logs.student_id` 可空，规则是**命名了学生的行按范围过滤，未命名学生的
  组织级事件（登录/导出/导入）保留**。
- **员工的范围行只有一个写入方**（2026-09-17 补）：`auth_service.create_account` 与
  `update_account`。在那之前 `user_scope` 里员工那几行只由 `db/seed.py` 写，
  所以「范围配错了」在界面上没有任何出路——而这正是这一层的输入。两条约定：
  - `update_account` 换范围是**整段替换**（`_scope_signature` 比对，一样就不动，
    于是重发同一个范围既不写审计也不换行 id）；**追加**会让一个账号越改越宽，
    而宽出来的那一段在界面上一行一个字列着，很容易读成「新配的那一段」。
  - 范围行是**叶子表**（没有任何东西引用 `user_scope`），所以删了重插是安全的；
    其余每一张表的外键都是 RESTRICT 且没有 `ondelete=`（§1），别把这条结论推广出去。
- 界面的那一句话必须与这里的口径同源：「全部学生」页头写「范围：全校 / 你负责的班级 …」
  读的就是 `/auth/me` 的 `scopes[]`（§9 上面那条），而新建账号的下拉框读的是
  `GET /admin/accounts/scope-options`。两处都由服务端发回**名字**，
  界面拼的是「年级 · 初一」这种中文，不猜 id。

### 10. 前端 API 约定

- 列表接口返回 `{ items: T[] }`，封装函数统一拆成裸数组。**两个例外**：
  `getAuditLogs`（服务端分页，`{ items, total }`）与 `getCounselorReminders`（2026-09-17 加，
  `{ items, total, truncated }`）。后者是服务端**每个来源各自封顶 20 条**（`REMINDER_LIMIT`）
  之后报的 `total`（另一次 SELECT COUNT）与比出来的 `truncated`——`items.length` 回答不了
  「我有多少待办」：一个老师手上 40 条时它也说 20，面板那颗徽章于是无论 20 条还是 400 条
  都写「20 项」。工作台据此写「另有 N 项未显示」。
  两个来源各封各的上限（不是一个共享的上限），否则这周 40 条跟进会把复测计划整个挤掉。
  **计数与取数共用同一个 `where`**，所以 `total` 与 `items` 不可能各说各话。
- **凡是截断，都要自己说出来。** 全站现在有五处上限，每一处都有一句「还有多少没显示」
  加一条出路：工作台提醒（`另有 N 项未显示，请到「重点学生」逐条处理`）、工作台队列
  （`另有 N 份一般观察档案`）、题库导入预览的前 20 题、学生导入预览的前 20 行、
  任务完成明细的前 200 条（那一处还指着「导出CSV」）。
  静默丢数据与「这里就只有这么多」在屏幕上长得一模一样，而读者会照那个数安排工作。
  §9 的「范围数字必须写明口径」是同一条道理换了个维度。
- 提交答卷带 `Idempotency-Key: submit-<id>` 头；重复提交返回既有结果，不产生重复风险事件。
- 审计日志的筛选 / 排序 / 分页**全部在服务端**（`limit` / `offset` / `sort` / `order` / `q` / `actor_role`），
  返回的 `total` 描述的是**筛选后**集合，因此翻页结果始终一致。其余列表数据量有界，用客户端排序分页。

### 11. 统计口径：两种口径各有其位，别把其中一个套到另一个的场景上

`analytics_service.latest_result_subquery` 是**「当前状态」这一口径的唯一来源**：
每名学生只取 `latest_session_order()` 排在最前的那一场，会话与结果**内连接**
（名册里还没交卷的学生没有等级可数）。

**他更早有一场已交卷时，取的就是那一场。** 这一条 2026-09-17 之前写反过——`latest_result_subquery`
当时的注释（和本文件这一节）说「刻意**不**回退到他更早的那一场」，而 `latest_session_order()`
把 `submitted_at IS NULL` 排在**后面**，所以「最近一场」是最近一次**交过卷**的，不是最近一次
**开过卷子**的。用探针跑出来才发现，注释与实现已经对齐（`test_student_results_api.py` 里
`test_a_student_mid_test_keeps_the_level_from_his_last_submitted_sitting` 钉住它）。
这不是将就：若开卷即掉出「已测评」，普查周里学校的关注人数与分母会随每个人的开卷动作上下跳。
**一场都没交过的学生仍然没有等级**——「还没测」与「测了没事」不是一回事。

| 口径 | 用在哪 | 回答的问题 |
|---|---|---|
| **按人取最近一场** | 个案详情、重点学生、受控导出、工作台、统计分析、`GET /students/results`（全部学生页签） | 「他**现在**是什么状态」 |
| **按场** | 任务完成明细（`task_service.task_completion`）、完成率 | 「**这一批人这次**测出了什么」 |

按场的那个是 2026-09-17 补的，当时给完成明细加了两列（关注等级 / MHT总分），而它是
模块里**唯一**按场说话的地方：一名学生落在两个批次里时，两边各自显示各自的分。
若哪天有人把它换成「按人取最近一场」，九月十六日那批的明细里会印出九月十七日的分——
一份批次报表说别的批次的事，而它旁边那列完成率还是按场算的
（`test_task_completion_levels.py::test_each_task_shows_its_own_sitting` 钉住它）。

**受控导出的「导出人数」按人去重，不是按档案行数**（2026-09-17 补）。后端逐**学生**写行
（`export_service.py` 的 `seen_students`），而「重点学生」列表一行一份**档案**——
一名学生可以同时有已关闭的旧档案与在办的新档案（§1 那条时序），所以把 `cases` 的行数
填进「导出 N 人」那一格会比真实人数大。`CasesPage` 的 `exportCount` 因此对高度关注那一支
取 `new Set(...student_id).size`（勾选那一支本来就是学生 id 的集合，无需去重）。
**这类数字（人数、条数）与它点进去/导出来的那个集合必须同源**，与上面指标卡那条同一条教训。


2026-09-17 之前 `attention_count` / `attention_rate` 直接把所有 `assessment_result` 加起来，
于是同一所学校在两个页面上能报出两个不同的人数：

- **跨场次重复计数**：复测过的学生被数两次；
- **只增不减**：去年的那一条重点结果永远留在分子里，一个学生好转之后仍然算「需关注」。

改完之后它与个案详情、重点学生列表、工作台、导出**同口径**。新增任何聚合前先问一句
「这是按人还是按次」——按次的口径只属于「完成率」这类管理事实。

**分母小于 `MIN_COHORT_FOR_AGGREGATE`（=5）时不下发比率与均值**，只说「样本过小」
（`rate_or_none` / `_average_or_none` 返回 `None`）。**计数不受这条限制**：学校要能回答
「我这几个学生里有几个需要关注」，所以「需关注 3 人」照给，「3 人里 2 人、60%」不给——
三个人的百分比等于点名（缺口 1 的反推风险在这里第一次有了护栏）。

`None` **不是 `0`**，两者在界面上必须长得不一样：`0` 是「一个都没有」，`None` 是
「这几个人算出来不足为凭」。前端因此有 `rateText(rate, small)` → 「样本过小」，
`api.ts` 里对应的类型是 `number | null`——**不要用 `?? 0` 把它抹平**。

**指标卡上的数必须与它点进去的那个列表同源**（2026-09-17）。工作台的「逾期跟进」
此前读的是后端 `metrics.following`，即 `status == FOLLOWING` 的**档案**数，而这张卡
点进去的是 `/counselor/cases?filter=overdue`，也就是 `c.overdue === true` 那一批
（与「已逾期」页签、「已逾期」药丸同一个标志）。真实数据上两者一个读 10、一个 0 条——
**卡片与它自己指向的列表各说各话**，而读者会照那个数去安排工作。现在它从 `cases`
数组里现算（`overdueCount`），与队列同一个来源，**构造上不可能漂**；`following` 没浪费，
挪到脚注当上下文（「跟进中 10 份」）。同一张卡上的「重点题命中」单位也从「人」改成
「条」：`pending_risk_events` 数的是 `risk_event` 的行，而引擎对**每一道命中的重点题**
各写一行（MHT 有两道：85 / 97），所以「一个人两道都中了」会被读成两个人。
**新增任何指标卡时先问一句「它点进去是什么，那个列表按什么筛」**，两边不同源就得现算。

**统计页要回答的是「问题集中在哪」，不是「谁还没测」。** 2026-09-17 的另两处调整：

- 年级/班级下钻补上「需关注」「关注占比」两列。此前这一块只有完成率——三个班完成率都是
  100%、一个有 6 人需关注一个没有，旧表里那两行一模一样，因为**完成率是教务问题**；
- 个案详情的「班级对照」页签排在「历次趋势」**前面**（`CareCaseDetailPage.vue` 的 `tabs`）。
  MHT 是每学期一次的普查，一年两场，所以一名初一学生的趋势页签长期是一个孤点，
  而班级对照**第一场测评当天就能用**。趋势页签在 `history.length < 2` 时会写明这一点
  并指向班级对照——不是删掉趋势（两场以上时它仍然成立），而是别让空趋势冒充报表。
  页签 2026-09-17 由「复测趋势」改名「历次趋势」：没有学生单独复测的场景。

### 12. 测评任务的状态是**现算的**，不是 `assessment_task.status` 那一列的原文

2026-09-17 之前那一列是写入时定死的一个字面量：建任务写 `ACTIVE`，导入批次也写
`ACTIVE`，然后再**没有一行代码读它或改它**。于是任务列表上每一行永远显示「进行中」——
一批 3/3 全收齐的外部导入、一场已经过了截止日期的普查、一场还没到开始日期的复测，
长得一模一样（用户报的：「都是在进行中，即使完成率显示100%也是一样」）。
词汇表里的 `DRAFT` / `PAUSED` / `CLOSED` 从来没有写入方，所以状态列从来没有变过。

判据只有一个：`task_service.effective_task_status`，由**已经被记下来的事实**推出来
（与 §11 的「当前状态」、`export_service` 现算遮蔽同一口径）：

| 事实 | 状态 |
|---|---|
| 库里那一列不是 `ACTIVE` | 以库里为准（人写的 `DRAFT` / `PAUSED` / `CLOSED` 不可被推导盖过） |
| 还没到 `start_at` | `NOT_STARTED` 未开始 |
| 过了 `end_at` | `CLOSED` 已结束，**不管还有没有人没答** |
| 目标行全部完成（且总数 > 0） | `CLOSED` 已结束 |
| 其余 | `ACTIVE` 进行中 |

- **两个消费者必须共用它**：任务列表（`list_assessment_tasks`）与「学生能不能新开一张
  卷子」（`assessment_service.create_or_get_session`）。界面说「已结束」时那个端点就真的
  开不了——这正是 `get_care_case` 那条教训的另一处（列表与详情各说各话）。
- **口径是整场任务，不是读者的范围**：状态与 `name` / `start_at` 一样是任务自身的属性，
  跟着范围走的是完成率（§9）。传进 `effective_task_status` 的两个数必须是**不加范围谓词**
  的（`task_target_counts`）。否则一个只带 CLASS 范围的心理老师会把一场 12/40 的普查
  看成「已结束」，同一行里状态说结束、完成率说 30%。
- **「已结束」不等于「把人踢出卷子」**：`create_or_get_session` 里那道门排在
  「已有会话直接返回」**之后**。截止日期管的是「还能不能再开一份新卷子」，不是「手里这份
  还算不算数」；写在前面的话，窗口关闭那一刻答到一半的学生会拿到 404，连答案都存不下去。
  （`test_task_status.py::test_a_student_already_answering_keeps_their_sheet_after_the_deadline`
  钉住这个顺序，把它挪回去就变红。）
- **窗口要能延长**：过期判 `CLOSED` 之后，「还有几个人没答，延一周」的出路是
  `PATCH /assessment-tasks/{id}` 改 `end_at`（心理老师，界面上的「编辑」）。判据里
  没有人工关闭/重开这条路径——四个码有了，按钮还没有。
- `NOT_STARTED` 是**推出来的码**，不是那一列写进去的：`labels.ts` 的 `TASK_STATUS_LABELS`
  同时服务两种来源，`e2e/vocabulary.spec.ts` 的编码清单里本来就有它。
  `export_labels.py` 不需要镜像——导出的完成明细写的是**目标行**状态，与它无关。

连带改动：`seed.py` 的 `TASK-2026-FALL-MHT` 窗口从写死的 `2026-09-01 — 2026-09-30`
改成相对今天算（`seed_demo._ensure_task` 早就是 `today ± N`）。**这条必须一起改**：
判据开始读 `end_at` 之后，写死的窗口一到日子就会让 `test_assessment_api.py` 里每一条
「学生开一份卷子」的用例拿到 404，红的原因却不是功能坏了。

### 13. 学生答题页：题数与题干只有一个来源，光标按会话存

`StudentAssessmentPage.vue` 是**唯一一个学生会长时间停留、且答错的代价由他自己承担**的页面，
所以它有四条与其他页面不同的约定（均为 2026-09-17 修，`e2e/app.spec.ts` 的
「学生端信任与进度」组逐条钉住——题数与题库一致、「上一个学生在这台电脑上留下的进度不会被读到」、
「题库换成 60 题，页面上就是 60 题」、「题干拉不到就只剩错误和重试，没有可以作答的地方」）：

- **题号只定义一次**（`questionNos`，来自 `getSessionQuestions`），进度、导航、「定位未答」、
  「答满了没有」、预计时长**全部从它派生**，不写死 100。此前 100 在这个文件里出现六次，
  题库换成非 100 题的版本时两处会坏：题**少**于一版时，学生翻过最后一题会看到
  「第 61 题（题干未加载…）」这种占位卡，而提交门是「答满 100」——**永远凑不齐、交不出去**；
  题**多**于一版时 `next()` 停在第 100 题，后面几道走不到。
  服务端的判据是规则版本的 `question_count`，与这里回答的是两个不同的问题，别只改一边。
- **题干拉不到就没有答题页**。此前是 `try { … } catch { questions.value = {} }`——拉不到题干
  就退化成占位卡，而**作答与提交照旧可用**：学生可以对着一串「第 N 题（题干未加载）」
  选完一整份「是」，交出一份每道题都不知道在问什么、却与真实作答在库里长得一模一样的答卷，
  事后没有任何办法分辨。现在拉不到就只有一条错误与一个「重试」。
- **光标（上次答到第几题）按会话存**，键是 `xlp_assessment_cursor:<sessionId>`；
  答案一律以服务端为准，本地不留副本。旧的全局键 `xlp_assessment_state` 里有
  **上一个人的答案**（机房是共用电脑，注释还写着「服务端是事实来源」而代码是本地覆盖服务端），
  所以任何学生打开这一页都**删掉**它，不是把它迁移过来。
  交卷成功后连自己的光标键一起清掉。
- 「回到哪一题」的次序：本地光标 → 服务端的 `current_question_no`（它本来就是「第一道未答题」，
  此前没有任何读者）→ 最后一题（那里才有「提交测评」）。**从第一题重新翻一遍不是「继续答题」。**

### 14. 失败、空态与竞态：三件事必须长得不一样

- **空态是一句关于数据的话。** 「暂无完成明细」「暂无测评任务」回答的是「这里确实只有这么多」，
  所以它**不能**在一次读取失败时落下。全站统一走 `ErrorState`（带「重试」），
  且错误分支要**排在**空表行之前（`v-else-if` 的次序就是这条约定）。
  同一页上「正在加载」与「加载失败」也必须分开：`SkeletonBlock` 与 `ErrorState` 各自成支。
  这条在 2026-09-17 修了三处：任务完成明细（此前失败只飘一条 toast、表格照样说「暂无完成明细」）、
  档案详情的副面板、以及若干 `error` 与空态同屏的地方。**toast 一飘而过，而留下的那句话
  会一直回答用户的问题**——两者说同一件事时，说错的那一句是留在屏幕上的那一句。
- **一次失败的读取不许留下上一次的答案。** 换一个版本 / 切换学生 / 重新搜索之后，
  面板上任何一处都不该再留着上一个对象的数据——否则标题写着 B、正文是 A。
  做法是**取数之前先清空**（`ScaleRulePanel.load`、`CareCaseDetailPage`、`CasesPage`、
  `useDataImport` 的三个预览），而不是等成功再覆盖。
  导入预览还多一层：留着的那份预览带着**上一个文件**的 `preview_token`
  （测评导入的 token 还把「批次名称 / 测评日期」烤了进去），拿它去提交是拿 A 的凭据交 B。
- **竞态：只有最后一次请求的答案算数**（`services/latest-request.ts` 的 `createLatestRequest`）。
  防抖**不撤销已经发出的请求**——输入「张」停手 300ms 以上再补成「张三」，两条请求都在路上，
  而它们回答的是两个不同的问题。守卫用 `begin()` 取号、`isCurrent(token)` 判定，
  **`catch` 与 `finally` 里也要判**：迟到的请求不能替后来者把 `loading` 关掉。
  已接的三处都是「同一次停留里用户能连着发起两次」的读：审计页搜索、评分规则面板、
  任务完成明细。**两处没接，是有意的**：`CasesPage.load` 与 `CareCaseDetailPage.load`
  的重入只发生在一次真实写入之后（批量分配 / 关闭档案），而 e2e 上造那次写入会改动共享演示数据的
  统计口径（缺口 8），所以它们进不了 e2e，也就没有可证伪的守卫——**别为它们补一个测不到的抽象**。
- 竞态守卫是**按页**构造的（`const latest = createLatestRequest()` 跟在 `searchTimer` 那一类
  状态旁边），不做成带生命周期的 composable：一个模块级/全局的序号会把两个不相干的页面
  串进同一条序列，那正是这里要防的东西。

### 15. 无障碍契约

这一节记的是**必须继续成立**的几条，改动这些组件时不要顺手摘掉。它们由
`e2e/app.spec.ts` 的「无障碍契约」组把守（每条都做过变异验证：把保证摘掉，对应用例变红）。

| 保证 | 在哪 | 摘掉会怎样 |
|---|---|---|
| 弹窗焦点陷阱 + `aria-modal="true"` + 焦点归还 | `components/Modal.vue` | Tab 一路走到弹窗背后，用户看不见焦点，回车按的是背后那个按钮 |
| **面板在打开状态下被卸载也要解锁**（`onUnmounted(deactivate)`） | 同上 | `body{overflow:hidden}` 留在原地，**这一页之后再也滚不动** |
| `aria-sort` **只给可排序的列** | `components/DataTable.vue` | `"none"` 的规范含义是「这一列**是**可排序的，当前未排序」，给不可排序列加上去是在说谎 |
| 可排序表头可聚焦、Enter/Space 可点 | 同上 | 排序功能只有鼠标到得了 |
| 全站 `:focus-visible` | `assets/styles.css` | 键盘用户不知道焦点在哪。用 `:focus-visible` 而非 `:focus`：鼠标点过的按钮不该留一个环 |
| `.metric[role="button"]` 才有手型光标 | 同上 | 判据是 `role="button"`——那些「看着能点、点了没反应」的卡片此前靠一个类名各自判断 |
| `role="status"` + `aria-live="polite"` + `aria-atomic="false"` 挂 toast **容器**上 | `components/Toast.vue` | 活动区域必须在内容出现**之前**就在 DOM 里，否则新插进来的那条出生时没人听；`aria-atomic="false"` 让新增的那条自己播报，而不是把整个列表重念一遍 |
| 登录页角色页签 `aria-pressed` | `LoginPage.vue` | 四个普通按钮，读屏软件不知道当前选的是哪一个 |
| 表单控件有 `label`（`label for` / 包住） | 全站 | 「当前密码 / 新密码 / 确认新密码」三个框读出来一样 |
| `meta.title` → `document.title` | `main.ts` 的 `router.afterEach` | 标签页、书签、历史记录全是「心晴 · 心理测评与关怀平台」，开到第五个就分不出哪个是工作台 |

`SkeletonBlock` 的 `aria-busy="true"` 与 `aria-live="polite"` 是同一类声明：
骨架屏对读屏软件说的不是「这里有内容」，而是「还没好」。

### 16. 清库：`purge-demo` 管这台机器，`sql/reset_to_baseline.sql` 管别处

2026-09-17 用户要求「清除 mock 数据，仅保留管理员账号和基本配置」，并要一份**能在新环境上运行**
的 SQL。两件事的落点不同，别把它们混成一件：

| | 在哪跑 | 清到什么 | 怎么实现 |
|---|---|---|---|
| `make purge-demo` | 这台机器 | `seed.py` 的基线（**留着** S001、两名种子员工、基线任务） | `app/db/purge.py`，ORM |
| `backend/sql/reset_to_baseline.sql` | 任何一台装了 mysql 客户端的机器 | 只有 `admin` + 基本配置（**名册、员工、任务全没**） | 纯 SQL，一个事务 |

**那份 SQL 只删行，不建表也不建行。** schema 归 `alembic upgrade head`，基线行归
`python -m app.db.seed`。所以它的正确用法是

```
make migrate && make seed          # 或者在一个已经迁移过的库上
mysql -h HOST -u USER -p DB < backend/sql/reset_to_baseline.sql
```

不把「基线」写成 SQL 的 INSERT，是因为那三样各自在仓库里**只有一个出处**：admin 的密码哈希
（`seed.py` 的哈希函数）、100 道题的题干（`data/mht_scale.json`）、评分规则 JSON
（`DEFAULT_RULE_CONFIG`）。在 SQL 里再抄一份必然漂移，而一份抄错的规则 JSON 会让那个库的评分
与别处不同、且看不出来（§6：阈值随规则版本走）。**这是一条约定，不是没写完。**

#### `sql/` 下现在有**两个**文件，分工不要混（2026-09-18 补）

| 文件 | 建表吗 | 删行吗 | 谁用 |
|---|---|---|---|
| `reset_to_baseline.sql` | 不 | 删（清成基线） | 上面那张表 |
| `schema_mysql8.sql` | **建**（24 张表，父先子后） | 不 | §18 的 `schema_prepared` 分工 |

`schema_mysql8.sql` 是**快照，不是来源**——上面那句「schema 归 `alembic upgrade head`」一个字
没变，它只是把迁移链**当时**建出来的形状印了一份出来。它存在是因为 `schema_prepared` 那条路上
操作员**先有表、后有程序**：此前《部署说明.txt》那一节给的唯一办法是「在另一台机器上按选 1 装
一次、再把结构导出来」，现在包里直接带着它（随 `backend/` 一起拷进安装目录，`build_package.py`
的 `REQUIRED_PATHS` 钉住），说明书写的是「包里就带着这一份」。

第二份写法必然有漂移风险，所以它有一条守卫：`test_sql_schema_matches_models.py` 静态比对
`Base.metadata`（表集双向相等 / 列集 / 可空性 / 外键集 / **建表次序父先子后** / 不许 `DROP TABLE` /
不许 `FOREIGN_KEY_CHECKS=0` / 不许 `ON DELETE` / 时间戳必须是 `DEFAULT (now())` / 列级不许 `COLLATE`），
12 个变异全部验证过变红。**它守不住类型，是有意的**——那要写一个 MySQL 类型解析器（`bool` /
`tinyint(1)`、`int` / `integer` / `INT(11)` 的别名规则），判错一次就再没人信它。

**真正的证据是 2026-09-18 那次真机比对**：同一台 MySQL 8.4.4 上，一个库由这份文件建
（**全程 `FOREIGN_KEY_CHECKS=1`**）、一个库由迁移建，逐表比 `information_schema` 的
TABLES / COLUMNS / STATISTICS / REFERENTIAL_CONSTRAINTS——24 张表 / 214 列 / 85 条索引记录 /
45 个外键逐项相同；另建一个空库跑 0001→0012 再比一次；`schema_prepared` 那条路也真走过
（`ensure_schema` 认账补版本戳 → `alembic upgrade head` 无事可做）。**开着外键检查跑不是一道
工序，是唯一抓得住那次错误的办法**：第一版把 `risk_event` 排在了 `assessment_session` 前面，
真库报 `1824 Failed to open the referenced table`，而当时那份守卫测试里除了次序那条以外**全绿**
——内存 sqlite 更看不见（缺口 3）。这也是「次序」那一条存在的原因，它不是排版要求。

**约束名照抄 MySQL 自动生成的 `<表>_ibfk_<N>`，不要改成有意义的名字。** 迁移是按名字引用约束的
（`0012` 已经在 `op.drop_constraint("uq_care_case_student_status", …)`），一个更好读的名字会让
这个文件建的库与 `make migrate` 建的库在**未来某条迁移上**分岔，而分岔得看不出来。

三条不变量由 `app/tests/test_sql_reset_to_baseline.py` 把守（每条都做过变异验证）：

1. **覆盖面与 `purge.py` 对齐**：`ASSESSMENT_TABLES` 里的表，SQL 里必须都出现。同一件事在
   Python 与 SQL 各写一份，比一次是唯一能让它们不漂的办法。
2. **子先父后，或那条出处列先被置空**，二者认一个。全库没有 `ondelete=`，父行先删在 MySQL 上
   是必然的 1451——而内存 sqlite 不检查外键（缺口 3），**这个错只有真库会报**，第一版就是把
   `student` 排在了 `user_scope` 前面。两条出路各有各的用处：调顺序解决得了
   `user_scope.student_id → student`（两张都删），解决不了 `role_permission.updated_by →
   user_account`（前者整表保留，只能置 NULL）。**`role_permission` / `system_setting` /
   `assessment_scale` 上那三列是全脚本唯一被改动的保留内容**，改的是「谁动过它」的出处，
   不是配置本身；重新指到 admin 上会是一句谎话，所以置 NULL。
3. **每一条 `DELETE` 都挂在 `@admin_id` 上**，找不到 admin 时一条都不删。最坏的失败不是
   「少删了」（再跑一次的事），而是「删完没有人能登录」。守卫本身也验过：把 admin 改名之后
   跑一遍，临时账号、草稿量表与它的题一行不少。

落库前在**临时库**上真跑过一遍（`alembic upgrade head` + `seed` + `seed_demo`，并人为放进
六种危险：量表是心理老师导入的、权限矩阵与配置被别的员工写过、已发布量表上有一条 RETIRED
规则、一个草稿量表带题、第二个 ADMIN 账号）——六种都过来了，没有 1451。

**一个只在真环境上才看得见的差别**：全新库的 `system_setting` 与 `role_permission` 是
**0 行**（配置回退 `DEFAULTS`、权限回退 `CAPABILITY_DEFAULTS`，§4 / §5 的 fail-safe），
`seed.py` 本来就不写它们。脚本一行都不碰这两张表——一个库上有几行，取决于那个库的操作员
配过什么。别照着开发库的 2 行 / 20 行去断言它。

### 17. 输入框只有两种形状，`.actions` 不是 flex 行

2026-09-17 用户报的：「测评任务 → 查看明细 时，里面的输入框样式不统一」。成立，
而且是**一个裸 `<input>` 落在两套约定之外**：

| 形状 | 在哪 | 长相 |
|---|---|---|
| `.search-box > input` | 搜索 / 筛选框（审计日志、重点学生、账号与权限、全部学生） | 40px 高、10px 圆角、`1px solid #c8d3df` |
| `.field input` | 弹窗里的表单字段 | 100% 宽、9px 圆角 |
| **裸 `<input>`** | 「查看明细」那个筛选框此前就是这样 | **浏览器默认**：28px 高、`2px inset rgb(118,118,118)`、`border-radius: 0` |

全站只有 `button, input { font: inherit }` 这一条（`styles.css`），**没有任何一条
给裸 `input` 上色**。所以一个不落在上面两处的输入框不会「差一点」，它是另一个物种：
灰色的凹陷直角框，挨着旁边 34px 的按钮。量出来差的正是 `height / border / radius / padding`
四个属性（e2e 的那条守卫就钉这四个里的前两个特征）。**加输入框时先问它属于哪一种**，
不要为它现写一套 inline 样式。

同一个元素上还有第二个空转：外层写的是 `class="actions" style="justify-content:space-between"`，
而 **`.actions` 没有基础规则**——它只在 `.page-head .actions`（那条还在 768px 的媒体查询里）
与 `.question-foot .actions` 两个限定选择器下存在。于是 `justify-content` 从来没有生效过，
输入框与按钮是作为 inline 元素靠基线凑在一起的。**全站那个 flex 行叫 `.toolbar`**
（`display:flex; align-items:center; gap:9px; flex-wrap:wrap`）。同一个死类名还坑过
`CareCaseDetailPage` 的「连续跟进时间线」与「复测计划」两块（`<h2>` + 按钮，量出来是
`display:block`、标题与按钮各占一行、都靠左），随这一次一起换成 `.toolbar`。
**其余十几处 `class="actions"` 没动**：它们要么在 `.page-head` 里（那里是 flex 行，
`.actions` 只是块状 flex 子项，里面的按钮本来就是 inline 排开的），要么是
`justify-content:center` 而无效果（居中变成靠左，肉眼几乎看不出来）。**没有给
`.actions` 补一条基础规则**去一次修好全部——那会同时改动十几个页面的按钮间距，
而这次的报告只关于一个输入框；要补请单独做，并准备逐页看一遍。

守卫是 `e2e/app.spec.ts` 的「布局完整性」组里那一条（那一组断言的是 `getComputedStyle`
计算值，不是文案）：打开弹层 → 断言边框是 `solid`（裸 input 的默认是 `inset`）、
圆角不是 `0px`、且父元素是 `.search-box`。**不写死 40px / 10px**：断言的是「它属于全站
那一套」，而不是「它刚好是这一版的尺寸」。变异验证：把 `<div class="search-box">` 那层
摘掉 → `inset` 与 `0px` 回来 → 红。

`type="search"` 留着（没去掉）。它在 Chromium 上不画任何东西——同一个 `.search-box`
下量过，`type=search` 与 `type=text` 逐像素相同——而 `e2e/app.spec.ts` 那两条明细用例
正是靠 `.modal-panel input[type=search]` 定位这个框的。

### 18. 部署：单端口 + 计划任务（装不了 Docker 的那台 Windows）

2026-09-17 加的。目标机的条件是：**Windows，已装 Python 3.11 与 MySQL 8.0 Server，
装不了 Docker，装的人不是技术人员**。要求是拷个 zip 过去、解压、双击一下、数据库连接
在装的时候输入；**装完在后台长期跑、重启后自己起来**——这一条 2026-09-18 起只对「局域网」
那种用法成立，见下。

这与 `docker-compose.yml` 那条路（`frontend/nginx.conf` 反代，两个容器）**并存**，
不是替换它。两条路各自完整。

**2026-09-18 的两处变化（用户要求：「只要保障当前用户可以使用就行，用户使用时启动，
关机时停止」）**：① 单机用法的服务从**隐藏进程**改成**一个可见的控制台窗口**，
关掉窗口就是停服务；② 包里**不再带内嵌 CPython**（那是 21MB，占 zip 一半），
改为安装时用**目标机上那个** Python 3.11 x64 建 venv、从包里的 `wheels/`
`pip install --no-index` 装依赖，zip 从 22MB 降到 11MB。两处的详细约定见下面各自的小节。

#### 四条不能改的约定

1. **`web_dir` 默认为空，等于这个功能不存在。** `Settings.web_dir = ""` 时
   `_mount_web()` 第一行就 return，于是开发（前端在 5173）、pytest、e2e 的行为
   **一字不变**。这是「新能力不许动既有行为」那类改动里的标准做法：默认值是关。
2. **catch-all 必须让 `/api/**` 继续 404 成 JSON。** 它取的是**整个 `/api` 前缀**而不是
   `settings.api_prefix`：把 `/api/v1` 打成 `/api/v2` 一样致命，而 22 条前端路由没有一条
   以 `/api` 打头。少了这条守卫，一个打错的接口路径会变成 **200 + 一整页 HTML**，而
   `services/api.ts` 会拿它当响应体去解 JSON，报出来的错是 `Unexpected token '<'`
   ——离真正的原因最远的那种提示（§2 那条「服务端答了话」的分支也会一起失灵）。
3. **目录形状不能压平。** `backend/app/db/seed.py` 用 `parents[3] / "data" / "mht_scale.json"`
   找题库，压平之后 `load_scale_questions()` 会**静默**回退成「MHT题目 001（开发占位…）」
   ——装出一个看起来完全正常、题库却是假的实例。`deploy/build_package.py` 的
   `verify_scale_data_is_reachable()` 照 seed.py 的算法走一遍，守着这条。
4. **`.env` 是 CWD 相对的，所以启动器必须 `chdir`。** `SettingsConfigDict(env_file=".env")`
   与 `alembic.ini` 的 `script_location = alembic` 都相对当前工作目录，**而 `.env` 缺失
   是被静默跳过的**（pydantic-settings 的默认行为）。计划任务配错工作目录的后果因此不是
   报错，是「用全默认值跑起来」——连的是 `root:password@localhost`，与操作员填的那一份
   毫无关系。`run_server.py` 进门就 `os.chdir` 到仓库根，让这件事不依赖任务配置。

#### 计划任务用 cmdlet 注册，不用静态 XML

`New-ScheduledTaskAction` / `-Trigger` / `-SettingsSet` / `-Principal` +
`Register-ScheduledTask -Force`。**没有 `deploy/task.xml` 这个文件**（`build_package.py`
的 `MUST_NOT_EXIST` 钉住「它没有悄悄回来」）。理由是把整类风险消掉：静态 XML 要求元素
顺序与官方 schema 完全一致，写错是**装的时候才报**，报出来还是 schtasks 的原文；cmdlet
在注册那一刻由 PowerShell 自己校验。

三个容易漏的参数值：

- **`-ExecutionTimeLimit ([TimeSpan]::Zero)`** → `PT0S`。从 XML 建的任务默认是
  **PT72H**，也就是装完三天后会被 Task Scheduler 掐掉——而它看起来一切正常。
- `-MultipleInstances IgnoreNew`：升级时旧进程还没退干净，不会起出第二个。
- `-UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest`：开机就跑，
  **不需要有人登录**（`-AtStartup` + ServiceAccount 的组合就是这个意思）。

#### 崩溃重试在 `run_server.py` 里，不在计划任务里

**Task Scheduler 的 `RestartOnFailure` 只在「根本起不来」时触发**——凭据错、exe 不存在、
ACL 不放行。`python.exe` 起来了、跑了一半崩掉（退出码非 0），**它不会重启**。
计划任务也不参与 SCM 的依赖图，所以 `depend= MySQL80` 这个写法不存在。

于是「装完能在后台长期运行」这句要求要靠自己的循环实现，`run_server.py` 的 `main()`：

```python
while True:
    wait_for_database(settings, logger)   # 连不上就一直等，指数退避封顶 30s
    try:
        uvicorn.run(...)
    except Exception:
        time.sleep(CRASH_RETRY_SECONDS)   # 崩了就重来
        continue
    else:
        return 0                          # 正常返回 = 收到关停信号，**不重启**
```

`else` 那一支不能省：`Stop-ScheduledTask` 是硬杀，但如果进程是收到 Ctrl+C / 信号正常
退出的（手工跑、调试），把它拉起来就成了「停不掉的服务」。数据库那一层同理——开机时
MySQL 服务往往还没起来，装的时候连得上不代表重启之后连得上。

#### `cryptography` 是**前提**，不是顺手带的

MySQL 8 默认认证插件是 `caching_sha2_password`。它在服务端**内存里**缓存了该账号的凭据
时走快路径（只做一次 SHA256 scramble，用不到 RSA，也就不需要 `cryptography`）；缓存未命中
才走 `sha2_rsa_encrypt`，而 `pymysql/_auth.py` 在那条路上直接
`raise RuntimeError("'cryptography' package is required …")`。

**那个缓存是服务端内存里的，MySQL 一重启就空。** 所以「装的时候连得上」证明不了任何事：
装完重启一次服务器，或者换一台全新的 MySQL，应用就再也连不上库。它写在
`deploy/requirements.lock.txt` 里，不依赖任何人的记忆。

#### 「用法」是第二个维度：单机 / 局域网（2026-09-18）

用户把目标改了：「在 windows 上安装时，只要保障当前用户可以使用就行了，用户使用时启动，
关机时停止。一键安装可按这个目的。」于是安装时多问一句，**两条路都保留**——学校服务器
那个场景还在。

| | `single` 单机 | `lan` 局域网 |
|---|---|---|
| 监听 | `127.0.0.1` | `0.0.0.0` |
| 安装目录 | `%LOCALAPPDATA%\xinliceping` | `C:\xinliceping` |
| 提权 / UAC | **不要** | 要 |
| 计划任务 / 防火墙 | **都不做** | 都做 |
| 起停 | 双击「启动服务.bat」 | 常驻，开机自启 |

**叫「用法」（`usage`）不叫「模式」。** `install.ps1` 早就在打印 `模式：升级现有安装`，
那个词已经指「首次装还是升级」。再拿它指这一维，同一份日志里会出现两行互不相干的
`模式：`，而操作员要 `grep` 它找东西。

**提权块必须排在「定用法」之后、「问口令」之前，这个次序是逼出来的。**
用法决定要不要提权，它本来该挪进第 1 步去问；**不行**——提权重启的子进程从第 1 行重跑，
要让子进程不再问一遍就得把所有答案当参数传下去，而 `$settings.dbPassword` 与
`$settings.adminPassword` **不能进命令行**（任何账号都能通过 WMI `Win32_Process` 读到别的
进程的命令行，环境块读不到）。于是用法这一问只能排在最前面，而且**不能依赖「安装目录
已经定下来」**（那是第 1 步才知道的），只能拿两个候选默认目录去探，探不到才问。

**`Tighten-EnvPermissions` 在单机模式下会把装它的那个人自己锁在外面。**
它用 `icacls /inheritance:r /grant:r SYSTEM:F Administrators:F`，而 **UAC 下普通用户 token
里的 Administrators 是 deny-only**——于是那个人从此读不了 `backend\.env`。授当前用户要用
`[Security.Principal.WindowsIdentity]::GetCurrent().Name`，**不是 `$env:USERNAME`**
（微软账户 / 域账户下它不对）。

后果**不是报错**（这一条实测过，pydantic-settings 2.15.0）：`.env` 读不出来时它抛
`PermissionError`——`is_file()` 是 stat，对不可读的文件照样返回 True，python-dotenv 的
`open()` 没有 try。于是安装器死在「配置自检」（退出码 1），而 `run_server.py` 那边
`get_settings()`（`run_server.py:166`）排在 `configure_logging()`（:167）**前面**，
所以服务死得**连一份日志都没有**。整段的推理链与「静默回退默认值」不是一回事，
改那段注释时别改回去。

**升级沿用上次的用法，不再问。** 换用法意味着删掉/建出计划任务与防火墙规则，那是**迁移**
不是升级——半路换会把上一次留下的东西变成孤儿（一条谁也说不清来路的防火墙规则）。
要换就卸载重装。`ops.ps1` 的 `Get-InstalledUsage` 读 `runtime\build.json` 的 `usage`，
**读不到 / 字段不在 / 值认不出，一律回退 `lan`**：那个字段是 2026-09-18 才有的，此前装的包
全是局域网那一种，回退成 `single` 会让那些服务器上的服务再也起不来，而「查看状态」还会说
一切正常。它**不叫 `Get-Usage`**——`install.ps1` 里那个 `Resolve-Usage` 回答的是「这次要按
哪种用法装」（决定），它回答的是「当初装成了哪种」（读取）；两个文件各有一个 `Get-Usage`
时，改错地方不会有任何东西报错。

**`Stop-RunningService` 的两半各自有各自的判据。** 计划任务是**有才停**（单机没有），
我们那个 `python.exe` 是**有就一定要等它退**：它锁着 `python\*.dll` 与 site-packages 里的
`.pyd`，不等它，下面那次 robocopy 会覆盖到一半失败（退出码 8）。这两件事以前写在一个
`if ($existingTask)` 里，于是单机用法连等都不等。抽出来之后还剩半个洞：单机模式下**没有
谁去叫那个进程停**，只等不杀就一定等满 15 秒然后照样撞上被锁住的 DLL——同一个退出码 8，
换了个入口。所以现在是「等一秒，还不退就 `Stop-Process -Force`」，与计划任务那条路的
语义一致。判据按 `Path` 比对 `$script:PythonExe`（那台机器上可能还有别人在跑别的 Python）。

**`Get-ServiceProcess` 的 `catch { $false }` 是一个要诚实看待的盲点**：`Path` 对跨账号、
跨完整性级别的进程读不出来，那种进程因此被判成「不在跑」，而「右键 → 以管理员身份运行了
启动服务.bat」是一条真实存在的路径。所以 `ops.ps1` 的 start / stop **都拿 HTTP 探活当第二
判据**：起的时候两个都说「没在跑」才起（否则会起出第二个，第二个绑不上端口而
`run_server.py` 的 `while True` 每 30 秒重试一次，而下面的探活拿**旧进程**的应答报成功）；
停的时候端口还在应答就不说「服务已停止。」——那句话会让操作员合上窗口走人，而服务还占着
端口。**「不能说一句自己没验证过的话」**，与 §2 那条「服务端答了话」是同一类要求。

**路径参数在单机用法下从「少见」变成了常态。** 默认目录在 `%LOCALAPPDATA%` 下，
`C:\Users\Zhang San\AppData\Local\…` 是个再普通不过的形状，而
`Start-Process -ArgumentList` 只是把数组用空格接成一条命令行、**不做转义**。所以每个
`Invoke-Native` 调用点的路径都要过 `Quote-Argument`：不引起来时 robocopy 收到的是
`源=C:\Users\Zhang`、`目标=San\AppData\Local\...`——源不存在是退出码 16，而**源恰好存在**
（那是另一个真实账号）时它会照着相对当前目录的 `San\...` 把错的东西拷进去，退出码还是 1。

#### 「数据库由谁准备」是第三个维度：安装器来准备 / 你自己准备 / 库和表你自己建（2026-09-18）

用户的原话有两句，第二句逼出了第三种取值：「我先把数据库准备好，只一键安装应用服务
（前后端）即可」，以及「我现在可以手工创建数据库，并建立数据表，**其他由你来完成**」。
第 1 步多问一句，答案是 `installer`（**默认，直接回车**）、`prepared` 或 `schema_prepared`。

| | 选 1 `installer` | 选 2 `prepared` | 选 3 `schema_prepared` |
|---|---|---|---|
| 建库 `app.db.create_database` | 跑（幂等） | **不跑**，换成连接自检探针（`dbcheck`） | 同选 2 |
| 表结构 | 迁移建出来 | 迁移建出来 | **校对**（`ensure_schema`） |
| 迁移 `alembic upgrade head` | 跑 | **跑** | **跑**（下面是理由） |
| 空库检查 `app.db.check_empty` | 跑（只警告） | 不跑 | 跑（只警告） |
| 基础数据 `app.db.seed` | 跑 | **不跑** | **跑** |
| 基线清理 `app.db.reset_to_baseline` | 跑（除非 `-KeepData`） | **不跑** | 跑（除非 `-KeepData`） |
| 管理员密码 `app.db.set_admin_password` | 设成他输入的那一个 | **不设，也不问** | 同选 1 |

**三种取值只差一件事：这个库里，哪几样归操作员。** 选 2 与选 3 的区别正是用户第二句话里
「其他」指的东西——选 2 连基础数据与管理员密码都推给了操作员，所以它答不上那句话。

**迁移三种都跑，是这一维唯一一处刻意的「越界」**，判据是一句话：**库与数据归操作员，
表结构归这一版程序**。不跑它的下场不是报错，是「屏幕说安装成功、页面 500」——换了程序
文件而表结构停在上一版时**登录页照样打得开**（它读的 `system_setting` 读不到就回退
`DEFAULTS`，§5），而第 6 步那个健康检查探的正是登录页要的那一个端点，所以这种「一半坏掉」
它抓不住。**这一条与 §4/§5 的 fail-safe 是同一个东西的两面**：配置读不到就回退默认值，
在别处是「不许 fail-open 成空界面」，在这里恰好让一个空库看起来是好的。

**选 3 是上面那句话唯一的例外情形，于是它逼出了 `ensure_schema`（同一个 2026-09-18 加）。**
手写的建表语句里**没有 `alembic_version`**——那是 Alembic 自己的版本记录表，只有 Alembic
会建。而迁移是无条件跑的，表已经存在时它在第一条 `ALTER TABLE … ADD COLUMN` 上撞
`Duplicate column name`，报出来是一句英文 MySQL 错，离真正的原因很远。所以迁移**之前**多
一步 `python -m app.db.ensure_schema`（三种分工共用，见下）：

| 这个库的状态 | 它做什么 |
|---|---|
| `alembic_version` 在、版本认得出来 | 什么都不做，交给后面的迁移 |
| 一张应用表都没有 | 什么都不做，迁移会把它们建出来 |
| 有表、**没有** `alembic_version`、缺表或缺列 | 退出码 1，逐条列出缺什么 |
| 有表、**没有** `alembic_version`、对得上 | `alembic.command.stamp` 补一行版本号，退出码 0 |
| 版本表**空着**、或版本号认不出 | 退出码 1，让它 `DROP TABLE alembic_version` 后重跑 |
| 版本表说 head 而表缺列 | 退出码 1（说 head 就得真的是 head） |
| 版本表比 head 旧 | 放行，并多说一句「重跑可能撞 Duplicate column」 |

它**只比表名与列名，不比类型**：类型一起比要上 `compare_metadata`，而 MySQL 的反射噪音
（`tinyint(1)` vs `BOOLEAN`、server default）会把一份本来能用的表判成不一致。**宁可漏报，
不要误杀**——误杀会把一台本来装得上的机器拦在第 4 步。**次序是它的全部价值**：挪到迁移
之后，迁移已经撞停，补账没有意义（`test_windows_assets.py` 一条守卫按位置钉它）。
它**刻意不在** `DATABASE_WRITE_COMMANDS` 那张表里——那张表回答的是「选 2 时安装器该不该
碰这个库」，而这一件三种分工都要做（选 2 的表也是操作员建的，同样没有版本戳）。

**空库检查（`app.db.check_empty`）是选 1 与选 3 的护栏，只警告不拦。**
`app.db.reset_to_baseline --yes` 是无人值守的破坏性脚本（清成「只有 admin + 量表与基本
配置」），而安装器判「首次还是升级」看的是**安装目录在不在**，不是库里有没有东西——于是
「同一个库 + 一个新目录」（为了修一台坏机器的人会做的事）落进那一支，而选 3 的人手上的库
常常是**还原来的一份备份**。它查 `school` / `user_account` / `student` 三张根表（其余每张
表都直接或间接指着它们），非空时退出码 1，安装器用 `-AllowFailure` 收下、自己说一句 WARN
（后果 + 出路：此刻还能 Ctrl+C 停下先备份）。**模块自己按「非空 → 1」写**：这样「非空」
是一个能被断言的事实，装不装下去由调用方决定。

**默认值是 1，因为它必须回到「这一问加进来之前的行为」**：改成 2 的话，一台全新机器上
直接回车装出来的系统**打不开任何页面**（库里没有量表、没有 admin），而屏幕上写着
「安装完成」。**升级也问**（用法那一问不问）：这一维回答的是「**谁**负责这个库」，
不该随「首次还是升级」变；不问的话 `runtime\build.json` 里的 `database_mode` 会与操作员
的认知对不上，而那个字段存在的全部理由就是「装完出故障时第一件要问这个」——`.env` 里
看不出来，两种装法的 `.env` 一字不差。**它没有命令行参数**：与用法不同，它不参与任何
跨进程传递（提权出来的子进程从第 1 行重跑，用法必须在那之前定下来；这一问排在提权之后），
而一个没有调用方会传的参数就是一条没有测试的代码路径。

五个消费者读的是同一个值，**读法一律 `Get-SettingText`**（升级分支的 `$settings` 里只有
installDir / usage / port，裸读就是 `PropertyNotFoundException`，见上面那一节）：

- `Read-InstallSettings` 的**两支都要**把它放进 `$settings`（漏了的话第 4 步取到空串、
  三个比较全假，安装器**默默按选 1 做**——那正是选 2 唯一不能发生的事）；
- 第 4 步按它分叉（四件写库的事各只剩一处调用，且都在安装器那一侧）；
- `runtime\build.json` 记 `database_mode`（与 `usage` 同一个理由：**它描述的是「装成了
  什么」，不是「装成功了没有」**，所以写在同一个位置——第 4 步之后、第 5 步之前）；
- 第 6 步服务起不来时按它多说一句指路的话（选 2 那条路上头号原因不是密码错，是库还没准备好）；
- `ops.ps1` 的「查看状态」按它多说一句中文（三种各一句，认不出就什么都不说）。

**第 4 步「建库」那一处的判据写成 `-eq 'installer'` 而不是 `-eq 'prepared'`**：三种取值下
「我来建库」恰好只有一种，用 installer 那一支说它，剩下两支自然落到 `else` 上。写成
`if prepared { 探针 } else { 建库 }` 的话，**选 3 会掉进 else 去建库**——而选 3 的全部含义
就是「库和表都建好了」。`test_windows_assets.py` 里把分支按**值**数出来并断言
`sorted(...) == ["installer", "prepared", "prepared", "prepared"]`，就是为了让「选 3 没有
自己的分支」这句话是一条判据而不是一句注释。

**选 2 且首次安装时，第 4 步要 WARN 着说清「基础数据没写进去，装完打不开任何页面」。**
那一刻屏幕上写着的是「安装完成」而库是空的——不说这一句，操作员唯一能做的推理是「装坏了，
重装一遍」。这是这一维**唯一的护栏**（见下）。**这一句只对选 2 说**：选 3 灌了数据，说了
就是假话，而操作员会照着它去手工补一遍（守卫专门断 `"不写入基础数据" not in schema 侧`）。

**出路写在《部署说明.txt》里，而且是跨文件的一条契约**：安装器的连接自检与第 4 步的 WARN
都点着名字说「照《部署说明.txt》里『数据库我自己准备』那一节的三条命令补上」（
`python.exe -m app.db.create_database` / `-m alembic upgrade head` / `-m app.db.seed`，
在安装**之后**跑——那个 python 是装的时候才建出来的）。那一节还有两个非写不可的细节：
**`seed` 建出来的 admin 初始密码是 `123456`**（不写的话补完基础数据的人进不去，而他会
以为装坏了），以及改密码的出口是磁盘上那个 `重置管理员密码.bat`。
选 3 那一路另有一节「**数据库和表我自己建好了**」，安装器在读到选 3 的那一刻就点着名字
让它去看，`ensure_schema` 停下时也指过去。**新小标题前后必须各有一个换行**（守卫按那一行
断言，与上一节同一条理由）：`mysqldump --no-data` 的方子写在那里，带
`--skip-add-drop-table` 与 **`--ignore-table=<库名>.alembic_version`**——后一个是必写的，
`--no-data` 会把那张表一起导出来而它是空的（0 行），导进新库正好落进「版本表空着」那一
状态，从此安装器再也不校对。这一条是在真 MySQL 上按自己写的那句方子走了一遍才发现的。

**2026-09-18：包里现在直接带着建表语句**（`backend\sql\schema_mysql8.sql`，随 `backend/` 一起
拷进安装目录，`build_package.py` 的 `REQUIRED_PATHS` 钉住它）。所以那一节的「最省事的办法」
从「在另一台机器上按选 1 装一次再把结构导出来」改成了「用包里那一份，不必先有一台装好的库」；
上面那句 `mysqldump` 的方子留着，接的是「手上已经有一台装好的库」那条路。两份写法的关系、
守卫守得住什么、以及那次真机比对，记在 §16 那一节里——**它是快照不是来源**，
`alembic upgrade head` 仍然是 schema 的唯一出处。

守卫是 `test_windows_assets.py` 的那一组，变异验证 **20/20** 变红。加第三种分工时
`database_mode_branches` 从「只认 `-eq 'prepared'`」改成**按值**返回
`(码, 命中那一支, 另一支)`，并新增 `database_mode_landings` 把每一处展开成三条路、算出
「某一种取值真正会走到的文本并集」。**判别力不许因为加了第三种而降级**，所以每条老断言都
改成对着并集说——只断言「某一处的某一支」会张冠李戴：「选 3 也要灌基础数据」在「选 2 那一支
的 else」里成立，而那个 else 同时也是选 1 的。三条自查自纠值得记：

- **判的是调用处的写法 `@('-m', 'app.db.create_database')`，不是模块名**：第 4 步那个依赖
  冒烟测试里有一行 `import app.main, app.db.seed, …`，按模块名数会数出两处来（第一版就是
  这么红的）。
- **手册那一节的判据是小标题那一行（前后换行），不是「文件里出现过这几个字」**：那一节
  别处还有两处**指向它**的交叉引用，按「出现过」断言的话，把标题改掉而引用留着这条照样
  是绿的——而操作员顺着引用翻过去，那一节已经不存在了。变异验证第一次跑就是这一条漏了。
- **「选 3 仍然会做的三件事」要有一份自己的名单**（`SCHEMA_PREPARED_STILL_DOES`），旁边
  一条断言保证它与 `DATABASE_WRITE_COMMANDS` 分成两份、一份不多一份不少：往那张表里加
  第五件写库的事时，这一条会红着问「那选 3 做不做它」。第一版是在守卫里写
  `if what != '建库'`，那样加新命令时没有任何东西会提醒。

#### 修一个静默空转了很久的自检：`Invoke-PythonScript` 的参数从来没透传过（2026-09-18）

形参原本只有 `([string]$Step, [string]$Name, [string]$Content, [switch]$AllowFailure)`，
而 `config` 那个探针的调用点把 **4 个值甩在 here-string 之后**的位置参数尾巴上——它们按位
绑到了 `[switch]$AllowFailure`（**非空数组就是 `$true`**），`Invoke-Python $Step @($path)`
一个参数都没往下传，脚本的 `sys.argv[1:5]` 当场抛
`ValueError: not enough values to unpack (expected 4, got 0)`；而 `AllowFailure` 恰好为真
→ 不抛异常、退出码被丢掉 → **那句「`.env` 口令编解码两端一致」的自检从来没有真正跑过**，
日志里只留一段没人看的 traceback。第 3 步那条「写入方与读取方对『空』的理解必须由写入方
保证」的教训，在这里换了个形状：**一个不报错的失败，等于没有这一步**。

处置：加 `[string[]]$Arguments = @()` 并透传（`Invoke-Python $Step (@($path) + $Arguments)`），
调用点改用 `-Arguments @(…)`，**退出码收进 `$configProbeCode` 再判**（不收的话
`Set-StrictMode -Version Latest` 会让那个 `if` 抛「在此对象上找不到属性」，报出来与自检
无关）。**仍然不拦安装**（用户的口径），只补一句 WARN。

同一处还修了探针的比对逻辑：四项输入各 `.strip()`（操作员复制粘贴时带空格是常事），
**主机名比大小写不敏感**——`parse_database_url` 走 `urlsplit(...).hostname`，它按规范把
主机名转成小写，逐字比会把输入 `MySQL01.School.Local` 的人挡在「地址对不上」上，而他照着
屏幕去改 `.env` 只会把一个本来就对的值改坏。库名与用户名**不放松**（MySQL 上的大小写
敏感性取决于平台与 `lower_case_table_names`）。

守卫有一条**真的执行**那个探针的用例：把 Python 源码从 here-string 里切出来，用
`subprocess` 跑三遍（对得上 → 0；主机名只差大小写 → 0；口令对不上 → 1，且断言口令本身
没有被打进输出）。**install.ps1 里那段 Python 在开发机上永远跑不到**（本机没有
PowerShell），而它恰恰是那个从来没跑过的东西——切出来跑是这一整套资产里唯一能证明它的
逻辑还在的办法。这一条与上面那条「只有真机才知道」是互补的：真机证明的是 PowerShell 那
一层，这一条证明的是 here-string 里那段 Python。

**这条缺口是有意留的、写在这里**：选 2 时安装器只探**连接**，不问「库里有没有量表」。
`check_empty` 把这条缺口收窄了一半（它就是在读表，而且读得成——`ensure_schema` 与
`alembic upgrade head` 都排在它前面），但**它回答的是「有没有数据」，不是「数据全不全」**：
一个只灌了一半的库（有 admin 没量表）照样算非空，于是那句 WARN 不出现，而界面上的症状与
「配置读不到就回退 `DEFAULTS`」（§5）是同一种——打得开、但没有东西可选。真要做成
「量表在不在」的探针，得指名去查那张表，而它属于**选 2 那条路**（数据归操作员），
那是另一件事（`deploy/README.md` 的「这套东西没有做的事」也记着它）。

#### ★ venv 的 `Scripts\python.exe` 不是解释器，是**一个转发壳**（2026-09-18）

**这是这一层最有复用价值的一条，也是这次唯一「不加就会重演同一个 bug」的一条。**
CPython 3.11.9 的 `Lib/venv/__init__.py:253-254` 原文：

```python
# On Windows, we rewrite symlinks to our base python.exe into
# copies of venvlauncher.exe
basename, ext = os.path.splitext(os.path.basename(src))
```

那个壳（`PC/venvlauncher.c`，编译成 `Lib/venv/scripts/nt/python.exe`）读 `pyvenv.cfg`、
对**真正的**解释器 `CreateProcessW` 然后等它。于是服务在进程表里是**两个**：

```
<安装目录>\runtime\venv\Scripts\python.exe   ← 壳（venvlauncher），Path == $script:PythonExe
        └─ <base python>\python.exe          ← 真占端口、真连库、真锁 .pyd
```

**Windows 不连坐。** 只按 `Path -eq $PythonExe` 找进程、只杀外壳，服务一点没停。
三条后果全是静默的：`venv --clear` 删不掉被孤儿映射着的 `.pyd`（`PermissionError`，
**每次升级都撞**）；第 6 步 `Start-ServiceNow` 看不见孤儿 → 起出第二个 → 第二个绑不上
端口而 `run_server.py` 的 `while True` 每 30 秒重试一次 → `Wait-ForHealth` 拿**旧进程**的
应答报「安装完成」；`停止服务.bat` 杀完壳端口还在应答。

所以 `install.ps1` 与 `ops.ps1` 的 `Get-ServiceProcess` **都改成收整棵进程树**
（`Get-CimInstance Win32_Process -Filter "Name='python.exe'"` 按 `ExecutablePath` 认根，
再按 `ParentProcessId` 收直接子进程，最后回到 `Get-Process` 对象——**调用点一行没改**）。
两处**必须都改**：`ops.ps1` 那一份用的是不带 `$script:` 前缀的 `$PythonExe`，
是两处独立编辑，不是复制粘贴。

**没有用 `taskkill /F /T`**：那会经 `Invoke-Native`，把「原生调用点恰好三处」那条守卫
（`test_there_are_calls_to_scan`）撞坏；而 `taskkill` 找不到进程返回 **128**，在这里是
正常情况，还得额外声明成功码。纯 PowerShell 收树没有这两个副作用。
**也别「顺手拿 base python 的路径再去比一遍」**：同一台机器上别人跑的 python 也是那个
base，那样会误伤。

关掉那个控制台窗口走的是 `CTRL_CLOSE_EVENT`，它发给挂在该控制台上的**所有**进程，
壳与真解释器一起死——**比 `Stop-Process` 更彻底**，所以「关窗口 = 停服务」成立。
但 `Ctrl+C` 走 `CTRL_C_EVENT`，只发本进程组，而 venvlauncher 用
`CREATE_NEW_PROCESS_GROUP` 起子进程（**这一条没能核到源码**，`PC/venvlauncher.c` 不在
本机）——所以给用户的说明里**只写「关窗口」**，不承诺按 Ctrl+C 能停。

#### 单机用法起一个**可见的**窗口（2026-09-18）

用户的心智模型是「使用时启动，关机时停止」，那个模型里的动作是**关掉一个看得见的窗口**。
所以 `install.ps1` 的 `Start-ServiceNow`（**单机专用**，第 6 步按 `$Usage` 分叉，
lan 走 `Start-ScheduledTask`）去掉了 `-WindowStyle Hidden`，并在收尾文案里写明
「屏幕上那个黑窗口就是服务本身，别关它」——**这是这次改动最容易被当成 bug 报回来的一点**
（装完凭空多一个窗口，用户会以为那是安装程序的残渣）。

- **`-NoNewWindow` 仍然绝对不能加。** 它让子进程共用 `.bat` 的控制台，`.bat` 一退出
  控制台就关、子进程收到 `CTRL_CLOSE` 当场死掉——「启动服务」会变成「启动一下然后立刻停」。
  这条推理与它当初写下时一字不差，**只是结论反了过来**：要的正是「另一个**可见的**控制台」，
  而 `Start-Process` 在 Windows 上默认就是给子进程开新窗口。注释不能删——它是唯一挡着
  下一个人「顺手换成 `-NoNewWindow` 少开一个窗口」的东西。
- **`ops.ps1` 的 `Start-Service` 是两种用法共用的**，所以窗口样式按 `$Usage` 分
  （single → `Normal`，lan → `Hidden`）：局域网手工点「启动服务.bat」时弹可见窗口既不合
  「无窗口地在后台跑」的语义，也和开机自启的计划任务打架。**其余几处 `-WindowStyle` /
  `-NoNewWindow` 是正当的**（`Invoke-Python` 与 `mysqldump` 要的就是「与调用者共用控制台、
  拿到退出码」，同步一次性调用不存在 `CTRL_CLOSE` 那个问题）——所以守卫必须用
  `function_body()` 限定到那两个函数体里，整文件断言会让它一开始就红。
- **注销也会停**（窗口属于那个登录会话），`部署说明.txt` 写明这一条。
- **过肩提权**（用另一个管理员账号装单机）时窗口出现在**那个管理员**的会话里，
  实际用户看不到也关不掉。低风险，与 `install.ps1` 那条「你正以管理员身份运行」的 WARN
  是同一类问题，记在那一段注释里。

#### 运行环境是安装时现建的 venv，不再内嵌 CPython（2026-09-18）

包里的 `python/`（21MB）与配套那段 124 行的**手写 wheel 解包器**（`Expand-Wheels` +
`Set-PythonPathFile`）都删了。后者存在的唯一理由就是内嵌版 CPython 里没有 pip。

四点不能改：

1. **只认 CPython 3.11 **64 位**。** `wheels/` 里 31 个 wheel 有 9 个文件名带
   `cp311-cp311-win_amd64`（另 1 个 `cp39-abi3`、1 个 `cp311-abi3`）。那 9 个**不含
   abi3**，解释器被钉死。`Resolve-BasePython` **真跑一次**去验（候选挨个跑一个小脚本、
   只看退出码），**不读 `(Get-Item $exe).VersionInfo`**——那个报的是 exe 自己的版本资源，
   对应用商店别名、对别的软件带进来的那份都不作数。判据三条：恰好 `(3, 11)`、
   `sys.maxsize > 2**32`、`import venv, ensurepip` 都能过。
   **探测脚本只输出 ASCII**：它跑的是 base python，此时 `sitecustomize.py` 还不存在，
   中文会按 locale 写字节，而 `Invoke-Python` 按 UTF-8 读回来 → `C:\Users\张三\` 变成
   不可逆的 U+FFFD。中文提示与路径一律留在 PowerShell 侧（`Write-Log` 是 UTF-8 落盘的）。
2. **次序不能换：`venv → pip install → sitecustomize`。** `--clear` 会清空
   `Lib\site-packages`，编码钩子排在前面会被下一次重建**静默**抹掉。
   `pip install` 的开关各有理由（`--no-index --find-links` 不碰网络、`--no-deps` 与下载
   时一致、`--isolated` 忽略本机 `pip.ini`——配置里的 `find-links` 会**叠加**上来，
   最坏是按默认 5 次重试卡住，在非技术操作员手上那是「装到一半不动了」）。
3. **探针脚本要自己带 `PYTHONPATH`。** `python 脚本.py` 把 `sys.path[0]` 设成**脚本所在
   目录**（TempDir），不是 CWD——所以 `-WorkingDirectory $BackendDir` 对它**没用**。
   此前靠的是 `python311._pth` 里那行 `..\backend`，那个文件没有了。
   `Invoke-PythonScript` 因此设 `$env:PYTHONPATH = $script:BackendDir`（`try/finally` 清掉）。
   **只有探针需要**（`run_server.py` 自己 chdir；`-m` 下 CWD 本来就在 path 上），
   所以特别容易漏——`install.ps1` 里那句注释不能删。
4. **`RuntimeError: 'cryptography' package is required`** 那件事（下一小节）**原样成立**，
   只是装法变了：`cryptography` 仍然钉在 `deploy/requirements.lock.txt` 里，
   由那次 `pip install` 装进 venv。MySQL 一重启、服务端那份 `caching_sha2_password`
   的内存缓存就空了，走 `sha2_rsa_encrypt` 那条路就要它。

**venv 挂在「某个人的」Python 上是局域网新引入的风险。** `pyvenv.cfg` 的 `home` 是写死的
绝对路径，而计划任务以 **SYSTEM** 身份跑。所以候选顺序里**机器级排在用户级前面**
（`Program Files\Python311`、注册表 `HKLM`；**不读 `WOW6432Node`**，那是 32 位视图），
选中用户级的而用法是 `lan` 时**打一条 WARN**（只警告不拦——只装了用户级 Python 的测试机
仍然该装得上），并把 base python 的绝对路径记进 `runtime\build.json` 的 `base_python`，
`查看状态.bat` 拿它判那个文件还在不在。**那是唯一能在故障发生之前看见它的地方**：
服务起不来时日志里只有 `pyvenv.cfg` 相关的英文，没人会联想到「有人升级过 Python」。

#### 卸载不删数据，停用立即生效

`ops.ps1 -Action uninstall` 删的是计划任务、防火墙规则、程序文件；**数据库里的东西一行
不碰**，并且在界面上明说。这与 §4 那条「停用 ≠ 删除」是同一条道理的另一处：删掉
`user_account` 那一行会让 `audit_logs` / `manual_review` 全部悬空，而安装目录与数据库
本来就是两件可以分开处置的东西。

#### 这套东西**没有**做的事（留给下一个人）

- **没有 TLS。** 局域网用法只给局域网用；单机用法压根不出这台机器。直接暴露到互联网
  需要另外配 HTTPS，`部署说明.txt` 里对操作员写明了这一条。
- **没有「单机 → 局域网」的在线切换**，只做卸载重装（理由见上面「升级沿用上次的用法」）。
- **局域网用法下 `备份数据.bat` 对普通用户是失效的。** 那个动作不在 `$needsAdmin` 名单里
  （它不动系统级的东西），可它要读 `backend\.env`，而那个文件只授了 SYSTEM 与
  Administrators。现在的处置是在错误信息里**把两种原因分开说**（权限 → 以管理员身份
  重试；文件坏了 → 去改），不再把一次权限问题指成「文件被改坏了」。要不要把 `backup`
  也收进 `$needsAdmin` 是个产品问题——那样会让一次只读操作也弹 UAC。
- **没有多校。** 学生导入链路仍然硬编码 `School.code == "QH"`（缺口 2），多校部署要先改那里。
- **备份不自动上传。** `备份数据.bat` 只在本机落一份 `.sql`，说明里要求人定期拷走；
  脚本顺手删 30 天前的旧文件，所以「一直没拷」的下场是**什么都没有**。
  （`vibe-input/08_vibe_coding_plan.md:82` 把「备份」列为必做项，这是它的第一个产物。）
- **没有自动放行杀毒软件。** 2026-09-18 起包里没有那个未签名的 `python.exe` 了
  （用的是目标机上 python.org 装的、有 PSF 签名的那一个），所以风险从「包里带了什么」
  移到了「装的过程做了什么」——建 venv、跑 pip 那几十秒仍然可能被拦。
  处置写在 `部署说明.txt`（「保护历史记录」→ 放行 → 重跑安装），**没有**用
  `Add-MpPreference` 静默地把整个目录加进排除项：以 SYSTEM 身份削弱一台学校服务器的
  杀毒软件是**对外生效且难以回退**的动作，不该由安装脚本替操作员决定。
  安装器自己的对策是**强制跑一次 import 冒烟测试**，被拦就停在那一步并留下日志。

#### 编码是这套资产里最容易踩的一处

Windows 侧那几个文件在开发机上一行都不会执行，第一次被执行是在一所学校的服务器上。
三条约定各有各的机器判据（`backend/app/tests/test_windows_assets.py`，每条都做过变异验证）：

| 文件 | 约定 | 违反了会怎样 |
|---|---|---|
| `.ps1` | **必须**有 UTF-8 BOM，且**恰好一个** | PS 5.1 按 ANSI 代码页读它，中文全乱；cp936 下中文字符的尾字节还会吞掉紧跟的 ASCII 字符（引号、括号），报出来是一句与编码无关的语法错误。「恰好一个」是 2026-09-18 补的，见下面那一小节 |
| `.txt` | 必须有 BOM，且**恰好一个** | 记事本打开是乱码（操作员要读的就是它） |
| `.bat` | **不许**有 BOM，且每个字节 < 128 | BOM 把第一行 `@echo off` 读坏；中文同上 |
| `deploy/requirements.lock.txt` | **不许**有 BOM，且每个字节 < 128 | 读它的是**目标机上的 pip**，规矩与 `.ps1` 正好相反——见下面那一小节 |

**「按任意键继续」那一行是 cmd.exe 自己的 `pause`，不是 `echo 中文`。** `pause` 打印的是
**操作系统本地化**的那句话，代码页与控制台一致，所以中文 Windows 上自动就是中文。
另外**不要加 `chcp 65001`**：PS 5.1 的 `Write-Host` 走 `WriteConsoleW`，中文与控制台
代码页本来就无关；而 `chcp 65001` 恰恰是 PS 5.1 输出被重定向时变乱码的那个组合。

**原生工具的输出是另一套编码，Python 那一路不跟着改**（2026-09-18 补）。robocopy / icacls
这类工具**重定向到文件时写的是控制台代码页**（中文 Windows = cp936），只有直接挂在控制台上的
`WriteConsoleW` 那条路才是宽字符。`install.ps1` 此前用 `-Encoding UTF8` 读它们，icacls 那句
「已成功处理 1 个文件; 处理 0 个文件时失败」落进日志成了 `�ѳɹ����� 1 ���ļ�`——而且
**不可逆**：写进去的已经是替换字符，原始字节当场就丢了，紧接着那句「上面是它自己说的话」
当场失效，而操作员唯一能提供的东西就是这份日志。现在归 `Read-NativeOutput`
（`[Console]::OutputEncoding.GetString`，**只读不改**，所以不违反上面那条）。
**Python 子进程那一路保持不变**：`sitecustomize.py` 把它的 stdout 固定成 UTF-8，
`Invoke-Python` 里的 `-Encoding UTF8` 是对的。守卫两个方向各断一条
（`test_native_output_is_read_in_the_console_code_page` 与
`test_python_output_is_still_read_as_utf8`）——只断一个方向挡不住「一刀切全改成控制台代码页」，
那会让这些脚本里的中文在英文版 Windows 上让 `print` 直接抛 `UnicodeEncodeError`。

`python311._pth` 那件事值得单独记一笔：**嵌入式 CPython 一旦有 `._pth` 文件，
`getpath.py` 就设 `isolated = 1` / `use_environment = 0` / `safe_path = 1`**
（2026-09-17 对着 CPython v3.11.9 的 `Modules/getpath.py` 逐行核过）。三个后果：

- `PYTHONIOENCODING` / `PYTHONUTF8` **全部被忽略**，所以 stdout 的编码只能靠
  `site-packages/sitecustomize.py`（`site` 模块启动时唯一会 import 的那个钩子）来固定。
  安装脚本里那两行 `$env:PYTHONIOENCODING = 'utf-8'` 是**失效**的，留着只是保险；
  别把它当成在生效。
- `safe_path = 1` 意味着 `sys.path[0]` **不插入**，`python -m app.db.seed` 会
  `ModuleNotFoundError: No module named app`（CWD 对 `-m` 也不作数）。
  所以 `._pth` 里必须有 `..\backend` 那一行——它相对 `._pth` 自己所在的目录解析。
- 于是**没有 console script**：`alembic` / `uvicorn` 那些 `.exe` 包装器造不出来，
  一切都走 `python -m`（`alembic/__main__.py` 与 `uvicorn/__main__.py` 都在，所以成立）。

**2026-09-18：不再有 `._pth` 了（内嵌 CPython 整个去掉），但上面那三段推理要留着**，
因为它换成了另一条理由**——`sitecustomize.py` 仍然是编码的唯一来源，只是原因变了：
venv 下 `PYTHONIOENCODING` 确实生效，**但那两行只作用于安装进程及其子进程**。
用户之后双击「启动服务.bat」时环境由他的会话决定，局域网那条路的计划任务由
Task Scheduler 决定——**两条路都不是我们能控制的**。所以 `install.ps1` 里那两行
`$env:PYTHONIOENCODING` / `$env:PYTHONUTF8` **删掉了**（不是「留着当保险」）：留着会让
「装的时候是好的、平时用的那一份不是」变成两个不同的环境，而那正是这套资产反复要
消掉的那类不一致。同理，`._pth` 那三段推理里与 `sys.path` 有关的部分，现在归
`Invoke-PythonScript` 的 `PYTHONPATH`（见上面「运行环境是安装时现建的 venv」第 3 条）。
**别照着上面那三行去给新机器找 `._pth`**——那个文件不存在了，而「探针 import 不到
`app`」这个故障仍然会以同样的样子出现。

#### 「有个 BOM」与「只有一个 BOM」是两件事（2026-09-18）

`install.ps1` 的开头一度堆着**四个** BOM（前 16 字节 `EF BB BF` × 4），而当时**两条
守卫都报绿**：`test_powershell_and_text_assets_carry_a_bom` 查的是 `startswith(BOM)`，
`build_package.py` 的自检也是前缀——**两个都只看前三个字节**。于是它一路混进了交付包，
出包时那句「`.ps1` 与 `.txt` 带 BOM」照样打出来。发现的路径也很偶然：拿包里的那一份做
括号差分时，`difflib` 的第一行是 `-<#` / `+<#`（差别肉眼看不见），去数字节才看出来。

**它怎么来的值得记**：一次变异验证的脚本把基准读成 `read_bytes().decode("utf-8")`
——**少了 `-sig`**。于是那个 U+FEFF 留在了字符串里，而复原时又写了一遍 `BOM + 文本`：
跑一次多一个，那个脚本跑了三次就是四个。而它自己的「复原 ✅」是绿的，因为那一句比的是
**内存里那一份基准**，而基准本身已经带上了 U+FEFF——**拿嫌疑人的证词给嫌疑人作证**。

四个 BOM 的 `install.ps1` 大概仍然能跑（PS 把 U+FEFF 当空白），但那是运气：它是一次
无声改写留下的印子，而同一个机制下一次改的可能是别的。

守卫是 `test_the_bom_is_exactly_one_and_nowhere_else`：开头**恰好**一个，且全文除它以外
**一个 U+FEFF 都没有**（夹在正文里的那个可能吞掉紧跟的字符，而它在编辑器里与空白长得
一样）。变异验证 4/4：多一个、四个、正文里夹一个、`部署说明.txt` 少一个——各红一次。

**这条教训比它守的东西值钱：变异验证的复原校验必须拿「落盘的原始字节」当基准**
（这次是 `shutil.copy2` 出来的一份备份，改完再逐字节比回去），不能是内存里再编码一遍的
字符串——后者的每一步（解码器、BOM、换行）都可能悄悄改一个字节，而它恰好也是校验用的
那一份基准。另外，**括号差分看不见它**（BOM 数不影响 `{` / `}`），所以那两条自证办法
各有各的盲区，谁也替不了谁。

出包脚本里那条同名的检查（`build_package.py` 的 `verify_windows_asset_encodings`）也一起
收紧了：它此前也是 `raw.startswith(BOM)`，而它扫的是**源目录**、印的是「带 BOM」那句
log，所以它是这次唯一一个「看得见却没说」的地方。现在归 `count_boms(raw)`（返回开头几个、
全文几个），两条判据分别报，`test_the_packagers_bom_check_counts_them` 在同一次运行里
变异一次（好的过、两个 BOM 的报、正文里夹一个的报），4/4 变红。

**那一版的断言第一稿是绿的，值得记**：两个 BOM 的那一份文件同时满足「开头多于一个」与
「全文多于一个」两条，所以 `any("U+FEFF" in one for one in problems)` 被它顺手满足了——
而那一条本该由第二份文件证明。改成**按文件找**（`one.startswith(name)`）之后才红。
这与上一节那条「手册里那一节改个名，守卫仍然是绿的」是同一个形状：**判据写成「整份清单里
出现过这几个字」，就只能证明「有人说过这句话」，证明不了「该说的人说了」**。

#### pip 按 **locale** 编码读 `requirements.lock.txt`，所以那个文件必须纯 ASCII（2026-09-18）

**这是这套资产里第三件只有真机才知道的事**（第一件是编码约定，第二件是外部命令的退出码
语义，见下一小节）。同一所学校的同一天，安装从「复制程序文件」往前挪了一步，停在第 4 步
「安装依赖」：

```
> ...\runtime\venv\Scripts\python.exe -m pip install --isolated --no-index \
    --find-links C:\xinliceping\wheels --no-deps --only-binary=:all: --no-input \
    --disable-pip-version-check --no-cache-dir --retries 0 --timeout 5 \
    -r C:\xinliceping\deploy\requirements.lock.txt
UnicodeDecodeError: 'gbk' codec can't decode byte 0x84 in position 16: illegal multibyte sequence
```

那个 `0x84` 是第 1 行**注释**里「部署包里…」的「的」字的第三个字节。原因在 pip 那一侧：
`get_file_content()`（`pip/_internal/req/req_file.py`）把**整个文件**交给 `auto_decode()`
（`pip/_internal/utils/encoding.py`），而它在文件没有 BOM 时退回 `data.decode()`，也就是
**当前 locale 的编码**。中文 Windows 上是 GBK，所以这个文件里**任何一个中文字符**都会让
pip 当场抛异常——**注释里的也算**。

- **判据不是「用某个 locale 解一次能过」**，是**逐字节 < 128**：一个 GBK 双字节序列
  （`C4 E3`，就是「你」）在 cp1252 下也是合法输入，两边各自解出**不同的**乱码。写成
  前者就会漏掉那一类形状。
- **不改成「加 BOM」是有意的**，与「`.ps1` 必须有 BOM」不矛盾：`BOMS` 表里第一个确实是
  UTF-8，但那只修好 pip 这一条读法。这个文件还要被人用编辑器打开、被出包时的
  `pip download` 读。纯 ASCII 是唯一一种**不需要任何一方「解码得对」**的形状——与 `.bat`
  那一条同理，理由也是同一个：**读它的那台机器不由我们决定**。
- 守卫在**两处**：出包前的早检查查源文件（一毫秒），自检时查**产物里的那一份**（pip 读的
  是它）；`test_windows_assets.py` 里另有一条用例，并且它**在同一次运行里把判据变异一次**
  （真文件过、加一行中文就不过）——免得守卫哪天被写成恒真还全绿。
- 代价是这**一个**文件的注释只能用英文（唯一一个这样的文件）。理由写在它自己的文件头里，
  改它之前先读那一段。**别往别处推广**：仓库其余部分照旧中文。

同一个形状还有一张表要记——**新增「往包里放一个文本文件、由 Windows 侧的工具读它」时，
先回答两个问题：谁来读、它用什么编码读**：

| 文件 | 谁读 | 编码 |
|---|---|---|
| `install.ps1` / `ops.ps1` / `部署说明.txt` | PowerShell 5.1 / 记事本 | UTF-8 带 BOM |
| `一键安装.bat` / `ops\*.bat` | cmd.exe | 纯 ASCII，不许有 BOM |
| `deploy\requirements.lock.txt` | 目标机上的 pip | **纯 ASCII** |
| `deploy\package-info.txt` | `Read-PackageInfo`（`-Encoding ASCII`） | 纯 ASCII |
| `backend\.env` | python-dotenv | UTF-8；带 BOM 也没事 |
| `data\mht_scale.json` | `seed.py` 的 `open(encoding="utf-8")` | UTF-8 |

`.env` 那一格**实测过**（2026-09-18，pydantic-settings 2.15.0 + python-dotenv 1.2.3）：
`Write-EnvFile` 用的 `Set-Content -Encoding UTF8` 在 PS 5.1 上**带 BOM**，而 python-dotenv
自己会把开头那个 BOM 剥掉，带与不带解出来的键值一模一样。**那是「当前这个读法恰好扛得住」，
不是「这一格已经加固」**——换成 `configparser`（按 locale 解）之类去读它，BOM 与中文就会
同时变成一个坑。

#### 外部命令的退出码要逐个工具判，默认不是「0 / 非 0」（2026-09-18）

**这是这套资产里第二件只有真机才知道的事**（第一件是编码）。2026-09-18 一所学校的安装停在
第 2 步 / 共 6 步，报 `复制程序文件 失败（退出码 1）`。而 `robocopy` 的退出码是**位掩码**：

| 码 | 含义 |
|---|---|
| `0` | 目标已经是最新的，没东西可拷 |
| `1` | **成功复制了文件** —— 全新安装必然是这个 |
| `2` | 目标目录里有多余文件（这里刻意不加 `/MIR`，所以是正常现象） |
| `3..7` | 上面那些的组合 |
| `8` | 有文件没能复制（重试次数用尽，多半是文件被占用） |
| `16` | 严重错误，一个文件都没复制 |

`Invoke-Native` 当时按 `ExitCode -ne 0` 判，于是**文件其实已经铺完**，脚本却抛异常 →
逃到 catch → `exit 1`。后面的写 `.env`、建库、迁移、种基线、注册计划任务、启动服务
**一件都没做**，而那句错误只说「复制程序文件」，从字面看不出少了什么。

三处改动：

- `Invoke-Native` 收一个**声明出来的**成功码表 `[int[]]$SuccessExitCodes = @(0)`，失败判据是
  `$SuccessExitCodes -notcontains $process.ExitCode`。默认值让 icacls 那两处（`:585`、`:732`）
  的行为**一字不变**，所以这次修复没有顺带改动它们。
- robocopy 那一处显式传 `(0..7)`，并在调用点写明「1 是成功，不是失败」。
  **新增第二个 robocopy 调用点时也要传**——守卫是按调用点扫的
  （`test_every_robocopy_call_site_declares_the_bitmask_codes`），不是按函数定义。
- 多一个可选的 `-FailureAdvice`，把「退出码 8」补成一句操作员能照做的话。没有它时那句
  throw 与改动前**逐字相同**。

**它为什么能活到今天：** 没有任何机器判据看得见它。`build_package.py` 的自检只看文件在不在、
编码对不对；`test_windows_assets.py` 在此之前**从不读 `install.ps1` 的正文**。现在那组用例
读了，但它们**证明不了 robocopy 的语义**（那要真机），只能挡住「有人改回去」。
`ops.ps1:340` 那段对应的 mysqldump 判断语义反而是对的——「跑外部命令并判成败」这件事在仓库里
有三处实现，各判各的，所以别假设对了的那一处能证明另外两处。

#### 提权出来的子进程写同一份日志（2026-09-18）

同一次排查里发现的第三个缺陷，它不影响能不能装上，影响的是**出了事之后能不能查**。

提权重启时（`Start-Process -FilePath 'powershell.exe' -Verb RunAs`）不把日志路径传下去，
子进程就从第 1 行重跑、按**那时**的时间重新算一个文件名。于是 `%TEMP%` 里留下两份
`xlp-install-*.log`：父进程那份只有表头（它打印完「日志文件：…」就 `exit 0`），真日志在
子进程那份——而操作员照开头那句话去拿，**拿到的是空白的那一份**。他发回来的正是这个。

现在 `param()` 里多一个 `[string]$LogPath = ''`：**传进来就用它，否则按时间戳新建**
（只有最外层那个、被「一键安装.bat」调起来的进程走 else 那支；用 `if/else` 而不是
`$x = if (...) {…}`，脚本开着 `Set-StrictMode -Version Latest`，少一个分支就是一个未定义变量）。
守卫 `test_the_elevated_child_writes_to_the_same_log` 两侧都断：`param()` 里有 `$LogPath`，
且提权那处的 `$arguments` 里含它。

#### 升级必须**验**已有的 `backend\.env`，不能「信任它、不动它」（2026-09-18）

同一天在这台机器上撞的第二次，也是这一层最贵的一次：文件铺完了、venv 建好了、31 个 wheel
装上了（pip 那条 GBK 修好了），安装停在第 4 步 / 共 6 步，日志最后是

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
port / Input should be a valid integer, unable to parse string as an integer
[type=int_parsing, input_value='', input_type=str]
    at backend/app/core/config.py:33
ERROR 安装没有完成：依赖检查 失败（退出码 1）
```

原因：`C:\xinliceping\backend\.env` 里那一行 `XLP_PORT=` 是空的。三代安装器都没验过它——
升级模式此前**只在里面抠一个端口号**给防火墙用（`Select-String '^XLP_PORT=' | Select-Object
-First 1`），其余一个字都不看。而 pydantic-settings 对「缺这个键」与「这个键是空的」处理**不同**：
前者用默认值 `8000`（所以「删掉那一行」是能启动的），后者在 `import app.db.session` 那一刻
抛 `int_parsing`，**在一段英文 traceback 里、隔着三层调用栈**。

**注意日志里那句 `访问端口：8000` 什么也不能证明**：它在第 1 步就打印了，说的是操作员回车
接受的默认值，不是文件里那些字。三轮排查之所以难，是因为当时**没有任何一句话说过安装器读的是
哪个文件**（`env_file` 是相对当前工作目录的，而文件不存在时 pydantic-settings 是**静默**跳过、
回落到硬编码的 `root:password@127.0.0.1`）。

现在的形状（守卫在 `test_windows_assets.py` 的「`.env` 那一层」一组，每条都做过变异验证）：

- **值表只有一份**（`Get-EnvWantedValues`），`Write-EnvFile`（首次安装整份写）与
  `Repair-EnvFile`（升级只补坏行）共用；**名单也只有一份**（`$script:EnvFileKeys`，八项），
  `Repair-EnvFile` 按它查缺、第 3 步按它报「N 项都有值」——报的那个数也必须是
  `$script:EnvFileKeys.Count`，不是写死的 8。
- **升级就地补，不整份重写**：`XLP_HOST=127.0.0.1` 是操作员在局域网用法上**刻意**改的，
  重写它等于把「只有本机能访问」悄悄改回「整个网段都能访问」。「升级不动它」这句承诺的
  正确读法是「不改你改过的值」，不是「不看你写的那几行」。
- **判据只在 `Test-EnvValueOk` 一处**：空 / 写了但不能用（`XLP_PORT` 是 `int`，判数字与
  1–65535；`XLP_DOCS_ENABLED` 是 `bool`；`XLP_DATABASE_URL` 判**形状**而非非空——
  `mysql+pymysql://:@:/` 能过「非空」，而它连主机名都没有，报出来是「Access denied」，
  与「密码打错了」长得一样，而人会去重打密码）。
- **数据库连接是唯一补不出来的一项**（没有可回落的默认值），所以它坏了就**回来问**：
  `Read-DatabaseQuestions` 从首次安装那一支里抽出来，两个调用点共用一份措辞。至此
  `Read-DatabaseQuestions` 恰好两处调用，守卫 `test_the_database_questions_are_asked_in_exactly_two_places`
  按调用点扫。
- **首次安装写完立刻读回来自检**，不合格就 `Stop-WithError`——它防的不是操作员，是
  **安装器自己**（写出 `XLP_PORT=` 的那个 bug 就在安装器里），并且停在写文件的那一步，
  而不是三行之后的一段英文。
- **名单之外、但是空的 `XLP_*` 行只报不改**（`Get-EmptyUnknownEnvKeys`，带行号，函数体里
  不许出现 `Set-Content`）。补不上（不知道那一项该是什么值），但看得出来——`XLP_` 这个前缀
  是 pydantic 读的，一条空的 `XLP_ACCESS_TOKEN_EXPIRE_MINUTES=` 与上面那次是同一种故障。
  不「顺手注释掉」，是因为那要替操作员改一行安装器完全不认识的配置，而它可能是下一版才有的
  开关。调用点在第 3 步 `if/else` **之外**（4 空格那一层），两种机器的日志里都打得出这句话。
- **补写排在 `Tighten-EnvPermissions` 与第 4 步之前**：收紧之后可能连读都读不了，而第 4 步
  之后坏配置已经变成一段 traceback 了。
- **第 4 步的 `config-show` 探针念出它读的是哪个文件**（`配置文件：…（存在），工作目录：…`）。
  这一行不修任何东西，它是给下一次排查留的——上面那三轮的成本全在「不知道它读的是哪一份」。
- **口令与 JWT 密钥不进日志**：补行说明统一走 `Get-EnvValueNote`，这两项只报「刚才输入的
  连接信息（含口令，不打印）」与「一串新生成的随机值」。安装日志是操作员唯一能提供的东西。

**「空值」这一类要一并记住**：`XLP_PORT=` 一个字符都没有时，pydantic 报的字段名（`port`）
与文件里的键名（`XLP_PORT`）不同、与那句 `input_value=''` 也不挨着。**写入方与读取方对
「空」的理解必须由写入方保证**——安装器写配置时验证自己写出来的东西，比事后读那段 traceback
便宜得多。

#### 严格模式下**读取**不存在的属性就抛异常，转型挡不住（2026-09-18）

`XLP_PORT=` 那次修完之后的下一次真机运行，六步走到第 3 步就停了：

```
INFO  ---- 第 3 步 / 共 6 步：写配置 ----
INFO  .env 已存在，升级只补坏掉的行，改过的值一个字都不动：C:\xinliceping\backend\.env
ERROR 安装没有完成：在此对象上找不到属性"dbUser"。请确认该属性存在。
```

一句话里没有文件名、没有键的来路——而它就是**升级路径**上 `Get-EnvWantedValues` 的第一句：
`New-DatabaseUrl -User ([string]$Settings.dbUser) …`。

**`install.ps1` 第一行就设了 `Set-StrictMode -Version Latest`，而它管的是「读」这一步**：
访问一个**不存在**的键（PowerShell 3.0+ 的严格模式下，哈希表也适用）在**读取那一刻**抛
`PropertyNotFoundException`，中文系统上就是上面那一句。**转型、字符串拼接、`?? ''`、`if`
判空——全都排在读取之后**，一个都挡不住它。此前那行旁边还留着一段注释，说 `[string]` 转型
是「必要的」，理由是「直接递空值给 `[Uri]::EscapeDataString` 会以 `ArgumentNullException`
收场」——那个推理本身没错，但它防的是**下一个**异常，而**前一个**（读取）先把控制流拿走了。
于是那行注释成了误导：它让下一个人以为这种写法是经过考虑的容错。

**为什么只有一部分机器撞上**：升级分支的 `Read-InstallSettings -Upgrade $true` 只返回
`installDir` / `usage` / `port` 三项（`:812`），数据库四项与管理员口令**根本没被问过**。
`.env` 里的 `XLP_DATABASE_URL` 一旦被判「不能用」，第 3 步会**先**问一遍数据库、把答案塞回
`$settings`，那几项就都在——所以这条路在那种机器上走得通。**恰恰是配置健康的那台**（这次
就是）会直接进 `Repair-EnvFile`，而它进门第一件事就是取这份值表。

三处改动：

- 新增 `Get-SettingText -Settings $h -Key 'dbUser'`：**判据是「键在不在」**（`ContainsKey`），
  没有这一项就回 `''`，绝不抛。`$h[$k]` 这个索引写法本身也是安全的（不抛），但 `ContainsKey`
  把意图写明了。全部五项（user / password / host / port / name）都改走它。
- **`Repair-EnvFile` 从此不写空值**：`Get-SettingText` 回来的 `''` 会一路流到 `$wanted[$key]`，
  而那个函数的整个存在理由就是**消掉**空值——它此前会把 `$key + '=' + ''` 原样写回去，
  也就是亲手造一次 `XLP_PORT=` 型故障。两个写入点各判一次（逐行补写、补写缺项），
  后者还要**先攒进 `$appended` 再决定写不写**：全都没有值时，连那句「下面这几项原来没有」
  的标题与空行也不该落进文件。
- 那行误导性注释换成准确的说明（`[string]` 挡不住读取）。

守卫三条，`test_windows_assets.py`，变异验证 **10/10 变红**：

| 守卫 | 扫什么 | 网眼（挡不住什么） |
|---|---|---|
| `test_a_settings_key_the_upgrade_path_never_sets_is_never_read_there` | 每一处 `$settings.<键>` **读取**（排除赋值、方法调用、注释）必须落在「只有首次安装才会执行」的块里（`if (-not $isUpgrade)` 整块、或 `if ($isUpgrade) … else` 的 else 那一半），且每个键的处数按一张表钉住 | 它判的是**文本位置**，判不了 PowerShell 语义。真被绕过去的形状是「整段逻辑搬进一个新函数、升级路径也调它」——那时位置不对，会红；但若那个函数也只在非升级路径被调用，文本看不出来，所以配了下面那条**机制**守卫 |
| `test_the_env_wanted_values_do_not_read_missing_keys_directly` | 五项都走 `Get-SettingText`，且函数体里没有裸的 `$Settings.dbXxx`；`Get-SettingText` 自己必须用 `ContainsKey` | 只管这一个函数（它正是出事的那一处，且两条路径都会调它） |
| `test_the_repair_never_writes_an_empty_value` | 两个写入点各有一处空值判断（**处数 `== 2`**，只有一处的话另一条路照样能写空值），且补缺项那一路走 `$appended` | 不知道那些值**该**是什么——只保证不写空 |

**可复用的那条**：一个**转型不是空值守卫**。在这种「可能少一个键」的哈希表上，
判据是「键在不在」；「值空不空」会把 `XLP_PORT=0` 这种合法的值一起当成缺项。
它与上面那节是同一个形状的第三次：**写入方与读取方对「空」的理解必须由写入方保证**。

改完照例跑了差分（§18 那条「手改 `.ps1` 之后怎么自证没写坏」）：拿**用户 14:47 真机跑过的
那一份**（`dist/心晴部署包` 里 14:06 的那一版）当基准，新增 58 行 / 删除 9 行，
`{` 与 `}` 净增量各 `+6`，全文收支 `323/322 → 329/328`。

#### PowerShell 的「空」有三种，`$null.Count` 会在最后一行炸掉一次成功的安装（2026-09-18）

`.env` 那次修完之后的**下一次**真机运行：六步全过，第 6 步健康检查回了 `200`，
「安装完成，服务已经在后台运行（没有窗口）」也打出来了——然后收尾文案崩在

```
ERROR 安装没有完成：在此对象上找不到属性"Count"。请确认该属性存在。
```

也就是操作员看到的最后一句是「安装没有完成」，而那时服务已经在跑、防火墙已经放行、
计划任务已经注册。**这一句与它前面那一句直接矛盾**，而「重新双击一次」的建议会让他
再跑一遍同一段。

原因是 PowerShell 的一条值语义，**与 `.env` 那次是同一个形状的第二次**：

| 写法 | 拿到什么 |
|---|---|
| `$x = @()` | 空数组，`.Count` 是 `0` |
| `function f { return @() }; $x = f` | **`$null`**——空数组在管道里被**拆没**了 |
| `$x.Count`（`$x` 是 `$null`） | 正常模式下是 `0`；`Set-StrictMode -Version Latest` 下**抛异常** |

`Get-LanAddresses` 用 `return @($addresses)` 收尾，而 `Get-NetIPAddress` 在那台机器上
一个匹配的地址都没返回（它的地址不在 `10./172.16-31./192.168.` 三段里，或者根本没连网），
于是 `@()` 变成 `$null`，`$lanAddresses.Count` 当场抛。**整条链路上没有任何一处说得出
「是地址列表空了」**，报出来的是一句像是安装失败的话。

处置两半，都在同一个函数附近：

- **调用点写成 `@(Get-LanAddresses)`**，并在函数体里写明**为什么必须包**——
  「多打两个字符」这句话不写下来，下一个调用点一定会写出裸赋值。
  `ops.ps1` 那边同形状的是 `Get-ServiceProcess`，三处调用点本来就包着 `@(...)`，
  这不是新约定，是**把这个既有的约定写到明处**。
- **一个私网地址都没有时照样把地址给出来**（只排掉环回与 `169.254.*`）。
  空列表的下场不只是崩一次：那句「老师这样访问：」存在的全部意义就是给操作员一条
  能念给同事的地址，而 `lan` 用法下**没有地址这条安装就没完成它该完成的事**。
  真正一个 IPv4 都没有（没连网）时它仍然是空的，那时收尾文案把两种原因**分开说**
  （认不出来 → `ipconfig`；一条都没有 → 先弄网络）。

守卫是 `test_windows_assets.py` 的最后两条：

- `test_a_counted_value_is_something_that_cannot_be_null` —— **裸变量上的 `.Count`**，
  那个变量的赋值必须是 `@(...)` 或 .NET 构造（`::new()`）。`@(x).Count` 形式自动不在
  扫描范围内（接收者是那个数组表达式，正则匹配不到）——这正是想要的：**包了的就免检**。
  真值判断挡住的那一处（`ops.ps1` 的 `$old`，`if ($old) { … $old.Count … }`）记在
  `TRUTHINESS_GUARDED_COUNT_SITES` 里，**每一条都要写理由**——那不是白名单，
  是「这里为什么不会炸」的记录。另有一条「扫到的处数 ≥ 12」的空转自检：正则坏掉时
  上面那个循环一次都不进，测试照样绿。
  **只管 `.Count`，不管 `.Length`，是有意的**：`.Length` 两处各有各的理由（`$bytes` 是
  `::ReadAllBytes()`，永远返回数组；`$item` 上一行就是真值判断），要一起收进来就得写一个
  「这行有没有被真值判断罩住」的半吊子判据，而那种判据错过一次就再也不可信。
- `test_the_lan_address_list_survives_being_empty` —— 上面那件事的两半逐字钉住：
  调用点包了 `@(...)`、函数里写着「会被拆没」、兜底查询还在（`code_only` 之后
  `Get-NetIPAddress` 恰好两次）、以及两条路都认不出来时告诉操作员怎么办。

变异验证 7/7 全部按要求变红（裸赋值回来、`ops.ps1` 的 `$alive` 变裸、`$old` 的豁免被拿掉、
新加一处没人赋值的 `.Count`、那句说明被删、守卫自己的正则坏掉、兜底被删）。
**这条教训的复用价值在于：它只在一台「恰好没有那种地址」的机器上出现**，
开发机上永远看不见——与「只有真机才知道」那三件（编码、robocopy 退出码、pip 的 locale）
是同一类，区别是它可以被一条文本判据挡住。

**原因挡住之后还要挡后果**（同一次修的，第三处改动）：那一段收尾说明整段挂在外层那个
`catch` 底下，而外层那句是 `安装没有完成：…`。于是**说明里的任何一次异常都会被报成
安装失败**——2026-09-18 那次就是这样，不是格式问题，是那句结论本身是假的，而它旁边的
三行正说着服务已经在跑。现在那一整段有了自己的 `try` / `catch`：

- 它排在那句 `$healthy = Wait-ForHealth` **之后**，那个变量为真就已经证明「服务在跑、
  端口在应答、计划任务/窗口都就位」——所以这一段里出的事**不可能**是安装失败。
- 它自己的 catch 说的是 `收尾说明没能打完：…` 加一句 `但服务已经在跑了，**安装是成功的**`，
  异常原文照样进日志（**不吞**）。
- **外层那句一个字没动**：第 1–6 步里抛出来的仍然报「安装没有完成」。这不是把失败调轻，
  是把「失败的是说明」与「失败的是安装」分开。

守卫 `test_the_install_summary_cannot_report_a_success_as_a_failure` 取
`Wait-ForHealth` 到外层那句之间的**代码**（`code_only`；注释里也提到过「安装没有完成」
这个说法），要求那一对 `try`/`catch` 在、且里面**不许出现**「安装没有完成」、必须说明
「安装是成功的」。变异验证 4/4 变红（整对删掉、catch 里改回那句、只说「出错了」、
外层那句被改掉）。**最后一条是靠 `ValueError: substring not found` 变红的**——
那也正合意：外层那句话是**锚**，谁改它谁就得回来看这一条。

**手改那两个 `.ps1` 之后怎么自证没写坏，值得单独记一笔**：这次改的时候我漏了一个 `}`
（`try` 块没有收尾），而**上面那批逐字断言一条都没红**——它们是文本匹配，看不见块结构；
本机又没有 PowerShell（darwin），跑不了 `-File` 的语法检查。
第一反应是「加一条括号配平」，**试了两次之后放弃，而且这个放弃是对的**：
先用「先剥单引号、再剥双引号」写了一个 Regex 剥壳器，同一份 `install.ps1` 报
`195/195`；改成「一条正则两个分支」再剥 here-string，同一份文件报 `238/237`。
两个都说得通、都看起来对，**结果互相矛盾**——因为文件里本来就有 5 行注释带单引号
（`# ... 用的是 `-ne ''` 判定 ...`），先剥字符串后剥注释的顺序会让它们错配。
这正是 `function_body` 的 docstring 里早就写下的那条：**在 PowerShell 上写半吊子词法器，
错的时候是无声的**——而这里更糟，它会**无故变红**，一条会无故变红的守卫很快会被人关掉。

能用的是**差分**：拿上一版**已经装成功过**的那一份当基准，逐行比。
把 `dist/心晴部署包.zip` 里那份 `install.ps1` 解出来（它此刻就是上一次真机上跑通的那一版），
与磁盘上这份 `difflib.unified_diff`，然后断言

- `{` 与 `}` 的增量**相等**（这次各 `+3`：`try {`、`catch {`、以及注释里那句
  `` `} catch {` `` 各贡献一对，正好抵消），
- 新增行减删除行的**净 `{` 数 = 0**。

**两条都不用懂 PowerShell 的词法**，所以不会像上面那样自己跟自己打架——
它只要求「这一版的括号形状与那一版相同」，而那一版写过一次真机日志。
注意基准要取**包里的那一份**，不是「我记得改之前是什么样」：手改两次之后就记不清了。
反过来说，**这条办法有前提**：手上得有一个跑通过的旧版本。第一次改这个文件的人没有，
所以它不能替代逐字读一遍——只能替「这次改动有没有把结构弄坏」这一个问题作证。

**2026-09-18 补：那两个互相矛盾的数字有一个确定的成因，剥壳器修好之后它是能用的。**
上面那个放弃是在**基准被覆盖之后**发生的（重出包会把上一份 `dist/心晴部署包.zip` 冲掉，
于是「拿跑通过的那一版当基准」这条路当场断掉），逼着重拾剥壳器——这次找到了病因：

**here-string 的收尾 `'@` 后面可以有代码。** 这份文件里恰好有一处（`:'` 那次修改新加的）：

```
            $configProbeCode = Invoke-PythonScript '配置自检' 'config' @'
…正文…
'@ -Arguments @($settings.dbUser, …) -AllowFailure
```

收尾那行既不是 `'@` 也不是 `` '@ ``，而是 `'@ …`。凡是把「收尾标记 = 整行」当判据的剥壳器
（上面那两版都是）都会**从这里开始一直留在 here-string 里面**，把后面所有真代码当成正文吞掉
——这一吞让括号计数凭空多出 `+1`，而 `+1` 看起来**正好像「漏了一个 `}`」**，也就是这条检查
唯一要抓的那个形状。于是它要么报一个不存在的错，要么（换一种剥法）把真的错一起吞掉：
两个数对不上就是这么来的，与「注释里的单引号」无关。

修好之后（收尾行按 `lstrip().startswith("'@")` 判、余下那段**接着当代码算**，另加
`<# #>` 块注释的进出），2026-09-18 的结果是：`install.ps1` 2516 行、`ops.ps1` 746 行，
**两份的收尾深度都是 0**，全程没有一处 `}` 先于 `{`，也没有未闭合的 here-string 或块注释。
它当场就证明了这次手改没漏 `}`——而差分那条路这次走不通。

**它的盲区要一起记住**（与差分各管一半）：它只能发现「总数对不上」与「提前闭合」，
**净零的错位看不见**（在 A 处删一个 `}`、在 B 处多一个 `{`，两侧抵消而中间那段全乱），
block-comment 与 `#"` 那一类仍有边界情况。所以它是**一次诊断，不是一条守卫**——
按上面那条「会无故变红的守卫很快会被人关掉」，它不该进 `test_windows_assets.py`：
一个半吊子词法器在那里出错是无声的，而**判错一次就不再有人信它**。

#### 升级不动管理员密码；忘了密码的出路是那个 .bat，不是重装（2026-09-18）

用户报的：「我上一版本已经安装了，且可以访问，只是 admin 密码不知道是什么，我重新安装时
要注意什么，admin 密码怎么设置」。两问都成立，而**当时的安装器一个都不回答**。

**升级（`Read-InstallSettings -Upgrade $true`）根本不问也不设管理员密码。** 第 1 步那次
`return $settings` 里就没有 `adminPassword` 这个键，第 4 步的 `set_admin_password` 在
`if ($isUpgrade)` 的 **else** 那一支里——所以「重新装一遍」既不会要你再输一次，也不会把
你改过的那个冲掉。**这是有意的**，与同一段里已经成立的两条同族：升级沿用上次的用法
（不重问，因为换用法是迁移不是升级）、升级不整份重写 `.env`（只在里面补坏行，因为
`XLP_HOST=127.0.0.1` 可能是操作员刻意改的）。**升级只做两件事：更新程序文件 + 跑一次迁移**，
凡是「你已经配好、正在用」的东西它都不碰。密码是其中最该不碰的一个。

那为什么还要在三处各写一句：**因为「不问」和「不说」是两件事**。一个忘了密码的操作员
读完整个安装过程，看到的是既没有问他密码、也没有告诉他密码是什么——他唯一能做的推理是
「那我重装一遍试试」。三句话分别落在 ① 第 1 步「管理员密码也不动」（他决定要不要按回车
的那一刻）② 第 4 步「升级模式：…管理员密码也不动」（他预期会被问而没被问的那一刻）
③ 收尾「密码：这次没有改，还是原来那一个」+ 出路（他要去登录的那一刻）。

**唯一的出路是安装目录里的 `重置管理员密码.bat`** → `ops.ps1 -Action reset-admin` →
`python -m app.db.set_admin_password`。**不需要重装、不需要 mysql 客户端**——界面上的改密码
要求本人先登录，所以第一任管理员的密码一丢，这个部署就进不去了（`set_admin_password.py`
的 docstring 就是为这一条写的）。它复用 `auth_service.reset_password`，所以顺手把
`failed_attempts` / `locked_until` 清掉——**被锁住的账号不用等，重设密码就是解锁**。
收尾文案因此刻意**不写「输错 5 次锁 30 分钟」**：那是 `XLP_PASSWORD_LOCK_THRESHOLD` /
`_MINUTES` 两个可配项，写死的数字会静默变错，而操作员此刻需要知道的只有「重设密码会
一并解锁」。

**收尾文案里的按钮名必须与磁盘上的文件名一字不差。** 那句话原本写的是「以后要停 / 起 /
备份 / **改**管理员密码，双击安装目录里对应的那几个 .bat」，而磁盘上那个文件叫
`重置管理员密码.bat`——**它是整段话里唯一一处告诉操作员「忘了密码怎么办」的地方**，
而按「改管理员密码」去目录里翻，翻不到同一个东西。这是缺口 7 那族第三次换皮
（「把「看不懂」换成了「搜不到」」，只是这一次发生在文件系统里），与前两次一样，
它只在有人真的照着做的时候才暴露。

守卫两条，各有各的网眼，**网眼多大要在测试里写明**：

| 守卫 | 扫什么 | 挡不住什么 |
|---|---|---|
| `test_every_bat_the_scripts_name_is_a_file_that_exists` | 两个 `.ps1` 里所有 `xxx.bat` 字面量，逐个问「有哪个已知文件名是它的后缀」 | 那句「对应的那几个 .bat」把真名省掉了，扫不到——**所以它挡不住本次这个 bug** |
| `test_the_summary_names_the_admin_password_button_as_it_is_on_disk` | 逐字：`重置管理员密码` 在、`改管理员密码` 不在（查旧写法要在 `code_only` 上查，解释「为什么叫这个名字」的注释里必须能提到它） | 别的按钮名（那是上一条的活） |

第一条的后缀匹配不是随手写的：`.ps1` 里有几处是**整句话**被「」引起来、句子恰好以文件名
结尾（`「右键以管理员身份运行了启动服务.bat」`），相等判定会把它们全部误报。变异验证
5/5 变红：写一个不存在的 `重置密码.bat`、把收尾那句改回旧写法、扫描正则坏掉（靠
`len(seen) >= 10` 的空转自检）、`known` 里少掉包根那一个、后缀匹配改成相等。

#### 升级不用人先去停服务（2026-09-18）

用户接着问的：「升级时，原来的服务用停止吗」。**不用**，安装程序自己会停，而且这件事
在代码里早就成立了——缺的是**没有一句话告诉操作员**（与上面那条「不问也不说」同一个形状：
`Stop-RunningService` 一直在跑，而说明书里只列了「升级不动密码、不动数据库」）。

**它必须自动停，因为不停就装不上。** 正在跑的那个 `python.exe` 锁着 venv 里的
`python*.dll` 与 site-packages 里的 `.pyd`（而且**锁住它们的是子进程**，见 venvlauncher
那一节），第 2 步的 robocopy 会复制到一半失败（退出码 8），`venv --clear` 还会在半路
`PermissionError`——**每一次升级都撞**。所以这一条不是体贴，是前提。

`Stop-RunningService` 的两半各自的判据（§18「用法」那一节已记过一次，这里是同一条的另一面）：
计划任务**有才停**（单机用法没有计划任务），我们那个 `python.exe` 是**有就一定要等它退**
（等一秒还不退就 `Stop-Process -Force`，最多等 15 秒）。**两个调用点都排在它保护的那件事
前面**：`:1720` 在 `Copy-PackageInto` 之前、`:2076` 在 `Register-ServiceTask` 之前。
顺序是这条唯一会失效的方式（两行都在，只是挪到了后面），而它在文本上完全看不出来——
`test_the_service_is_stopped_before_the_thing_that_needs_it_stopped` 按调用点逐个比位置，
就是为这个写的。

对操作员要说的只有一句：**不用去双击「停止服务.bat」**，升级那几分钟里网页打不开是正常的，
装完它自己会重新起来（单机那一路起来的是一个可见的控制台窗口，见上一节）。
这句话落在 `部署说明.txt` 的「再双击一次是升级」那一段里——那一段是操作员要动手之前
唯一会读的地方，也是他判断「要不要先做点什么」的地方。

#### 一键安装装不完时的出路：两枚手工按钮（2026-09-18）

用户的原话：「如果仍然失败，我计划直接手工操作，分别启动前端后端，需要什么指令」，
接着是「能不能总结为两个脚本，我直接跑」。于是 `deploy/windows/` 下多了三样东西：
`manual-start.ps1`（`-Action backend|frontend`，与 `ops.ps1` 同一个形状）、两枚纯 ASCII 的
`手工启动后端.bat` / `手工启动前端.bat`、以及 `serve_frontend.py`。

**它补的是「装不下去」这个场景，不是第二种装法。** 不注册计划任务、不放行防火墙、
不设管理员密码、**不写 `runtime\build.json`**——所以「查看状态.bat」对它们开出来的服务
一无所知（那个目录里没有 `usage` / `database_mode` 可读，`Get-InstalledUsage` 于是回退
`lan`）。它做的是「把 `install.ps1` 第 2～4 步那几件事补上、并当场把服务跑起来」，
而单机那条路上这正是全部所需。

三条与既有约定的接缝，每一条都是别处已经吃过一次的亏：

- **前端那一路必须是 `serve_frontend.py`**（下一个标题就是「前端必须同源」）：
  `frontend/dist` 是构建产物，而 `API_BASE = '/api/v1'` 是相对路径，所以「前端单独一个
  进程」只有两种成立方式——后端自己发（`XLP_WEB_DIR`），或者中间一个**会反代 `/api`** 的
  服务器。`python -m http.server` 会让登录页打得开、**每一次登录都失败**
  （`Unexpected token '<'`），而那个窗口自己看起来一切正常。那个服务器**只用标准库**：
  目标机上只有 venv 里那 31 个 wheel，没有 flask 可借，守卫会去比对
  `sys.stdlib_module_names`。
- **Python 的输出不许走 `& python …`**。那条路会把子进程的 stdout 接进 PowerShell 的管道、
  按 `[Console]::OutputEncoding`（中文 Windows = cp936）解码，而 `sitecustomize.py` 把
  Python 的 stdout 固定成 UTF-8 → 中文变乱码且**不可逆**（写进去的已经是替换字符），
  紧接着那句「上面是它自己说的话」当场失效——而操作员唯一能提供的东西就是这份输出。
  所以真正的调用一律走 `Invoke-Native` 的 `Start-Process -NoNewWindow`：子进程继承控制台，
  Python 走自己的 Unicode 控制台 API，整条路上没有第二个人在解码。两个例外是刻意只输出
  ASCII 的探测（那里要的正是把输出接回来读一行）。**后端那一个上的 `-Wait` 是有意的**：
  这个窗口就是服务的控制台，关窗口 = 停服务（与单机用法的「启动服务.bat」同一个语义）。
- **清库要先问人**（`Read-Host`，回车那一支是「不清」）：`install.ps1` 里
  `reset_to_baseline --yes` 是无条件跑的（那条路上整件事都在安装器手里），而手工这条路
  不假设任何东西。**一个破坏性动作的默认值不能是破坏。** 同样地，它**不设管理员密码**
  （跑的是 `app.db.seed`，于是 admin 的初始密码是种子里的 `123456`，出路是「重置管理员
  密码.bat」）——这与「升级不动管理员密码」是同一条：凡是操作员**已经配好、正在用**的
  东西，这条路都不碰。

**两枚按钮留在 `deploy\` 里，不进包根**（`build_package.py` 的 `ROOT_LEVEL_ASSETS` 只有
「一键安装.bat」与「部署说明.txt」）：摆在包根会让一个还没开始装的人先看到两个「手工」
按钮，而他此刻该点的是「一键安装.bat」。《部署说明.txt》里写着它们的完整位置——
**纸上的按钮名必须与磁盘上的文件名一字不差**（缺口 7 那一族已经栽过三次）。

判据在 `test_windows_assets.py` 末尾那一组（10 条，全部做过变异验证），三条值得单独记：
**两枚按钮请的动作与脚本声明的必须一一对上**（对不上就是「双击之后一闪就没」）；
**打包器真的被调一次**（钉住按钮与 `manual-start.ps1` 进了包之后还在同一个目录——
按钮里写的是 `%~dp0manual-start.ps1`）；**《部署说明.txt》里出现的每一个 `.bat` 都是
磁盘上真有的名字**（那一份是操作员唯一会读的纸，改名只改一处他就找不到那个按钮）。
说明里那一节另有两条判据：它说了「关掉窗口，服务就停了」，也写了那句 `Y 再回车`
——**拷的是脚本里 `Read-Host` 的原文**，因为操作员照纸输入的东西必须正是脚本等在等的东西。

#### 前端必须同源

`services/api.ts` 的 `API_BASE = '/api/v1'` 是**相对路径**，`createWebHistory()` 没带 base，
所以生产环境要么一个进程同时发 API 与页面（这条路），要么中间放个 nginx
（`frontend/nginx.conf` 那条路）。**没有第三种。**

### 19. 版本号只有一个出处

2026-09-19 定 V1.0 时顺手收敛的。**唯一出处是 `backend/app/version.py`**：

```python
__version__ = "1.0.0"                                     # 规范串
VERSION_LABEL = "V" + ".".join(__version__.split(".")[:2]) # 显示串 → V1.0
```

`V1.0` 与 `1.0.0` 是**两个不同的东西，不是一个的别名**：`V1.0` 是给人念的
（界面上那一行、操作员打电话报故障），`1.0.0` 是给机器认的（`package-info.txt` 的
`version=1.0.0+20260919`、`/openapi.json`、pip 元数据）。标签丢掉修订号是有意的，
`1.0.1` 仍然显示 `V1.0` —— 那正是补丁号的意思，而构建戳负责区分同一条发布线上的
不同次出包。

| 谁 | 怎么拿到 |
|---|---|
| `pyproject.toml` | `dynamic = ["version"]` + `[tool.setuptools.dynamic] attr` |
| `deploy/build_package.py` | 正则读 `version.py`，拼 `1.0.0+YYYYMMDD` 写进 `package-info.txt` |
| `app/main.py` | `FastAPI(version=__version__)` → `/openapi.json` 的 `info.version` |
| `/api/v1/public/branding` | `VERSION_LABEL`，界面页脚读它 |
| `frontend/package.json` | **唯一的镜像**（npm 的 version 没法动态），由测试盯着 |

**这一节真正的教训不是「版本要统一」，是那四个数字此前怎么骗过人。** 定版之前，
`pyproject.toml` 写 `0.1.0`，而 `/openapi.json` 报的**也是** `0.1.0`——两个数一模一样，
看上去像是同一个出处。实际上后者根本不是谁设的：`main.py` 的 `FastAPI(...)` 当时
**没传 `version=`**，那是 FastAPI 的内置默认值。**「没人设过、却看起来像设过」**的值
就是这个形状：它与任何配置都无关，改一处不会带动另一处，而屏幕上一切正常。
`test_app_version.py::test_openapi_version_comes_from_the_single_source` 钉住它，
变异验证摘掉 `main.py` 那一行会红——**光看数字是发现不了的**（改前它恰好对得上）。

同族的另外两处：`frontend/package.json` 与根 `package.json` 各写着一个数，
**没有任何东西读它们**。前者随这次对齐并加了守卫（它是那份镜像），后者不动——
它不是产品版本，给无关的东西加断言只会让升级多一处编辑。

`app/version.py` **里不许有任何 import**：setuptools 的 `attr:` 静态读它，
一个 import 就能让 `make install` 在一个与版本毫无关系的地方失败。

界面上那一行落在**「账号与权限」页脚**（`.page-foot`，`styles.css` 里与
`.page-head` / `.page-desc` 同族）。取不到就**整句不出现**，不显示空串也不猜一个
——与 §9「取不到 `scopes` 时整句不出现」同一条：一个说不清的版本号比没有更糟，
因为操作员会照着它报故障。**前端绝不写死版本**：部署包里 `frontend/dist` 是预构建的，
写死会造出「后端升了、界面还说旧版本」的分岔，而那正是这一行要回答的问题。

## 已知缺口（动手前先看这里）

1. **数据范围已在查询层生效，但有两处刻意的例外和一个口径盲点。**
   2026-09-16 起 `care_service`、`analytics_service`、`task_service`、`export_service`、
   `api/v1/students.py`、`api/v1/audit.py` 的列表与聚合都过 `student_scope_predicate`（见 §9）。
   仍然不按范围过滤的是：
   - `analytics_service.leader_progress` —— 能力要求 `SCHOOL`，德育领导本就该看全校，改了反而错。
   - `care_service.list_assignable_owners` —— 返回的是心理老师名单，不是学生数据。

   另外两点需要注意：
   - **管理员自身也受 scope 约束。** seed 给 admin 的是 `SCHOOL/青禾`，所以多校部署时管理员
     只会看到青禾的学生。管理员的 scope 该怎么配是个未决的产品问题。
   - **小范围聚合有反推风险。** 一个只覆盖 3 名学生的 `CLASS` 范围，维度高亮率 100% 等于点名。
     当前场景（整班、整年级）够用，但把范围切到 `STUDENT` 级时要重新想。
     2026-09-17 起**比率与均值**有了护栏（§11 的 `MIN_COHORT_FOR_AGGREGATE`），
     但**计数仍然全量下发**（「3 人里 2 人」不给百分比，可「需关注 2 人」照给），
     所以这条缺口没有真正关闭——把范围切到 `STUDENT` 级之前仍要重新想。
     同一个护栏之外还有一处**未加护栏**的：`dimension_distribution` 的 `high_rate` 与
     `average_score`（分母是调用者范围内的**已测评人数**）。它 2026-09-17 已收敛到
     `latest_result_subquery` 同口径，但小样本那一层没有——一个只覆盖 1 名学生的范围，
     那一行维度均值就是这名学生自己的分。**没顺手补上是因为代价不划算**：
     三条既有测试（`test_analytics_dimensions.py` 两条、`test_assessment_import_api.py` 一条）
     正是拿「1 名学生」当最小夹具去钉别的东西（最近一场、跨校排除、导入按测试日期读），
     套上护栏就得先把它们的夹具扩到 5 人并重算期望值，而那会把它们各自要钉的那一点冲淡。
     要补请单独做，并保留那三条测试的原意。

   还有 `api/v1/audit.py` 用的是硬编码 `require_role(ADMIN, COUNSELOR, LEADER)` 而不是能力矩阵，
   与 §4 的约定不一致——读审计该不该是某个能力，尚未决定。

2. **`preview_student_import` 与 `commit_student_import` 都硬编码 `School.code == "QH"`。**
   学号查重已对齐到同一所学校（2026-09-16），但整个学生导入链路仍然是单校写死的，
   多校部署需要先改这里。
   同一条链路上还有学校自己的编号规则（2026-09-16 加）：**班级按 `701 / 801 / 901` 编号，
   首位 `7 / 8 / 9` 分别是初一 / 初二 / 初三，导入时校验它与「年级」列一致**
   （`student_import_service.check_grade_and_class`）。**年级仍必填**，只认 `初一 / 初二 / 初三`
   三个名字——写「七年级」会新建一个年级行，所以挡下来；两列对不上按行报错
   （`班级 701 属于初一，与年级 初二 不一致`）。之所以留着冗余的年级列并强制一致，是因为
   `commit_student_import` 只拿字符串去 `Grade.name` 里找、找不到就新建，不拦的话录错的
   学生会被**静默**挂到错年级下面。
   学号（如 `27025160101`）**不校验格式**：各位数字的含义没有确认过，只校验必填与不重复。
   写测试夹具时班级要用 `701` 这类编号，`1班` 会被挡下来（`tests/test_student_import_api.py`
   的 `test_class_code_must_match_its_grade` 系列钉住这条规则）。
   **「学号已在名册上」是冲突，不是错误**（2026-09-17 用户要求，与测评导入的
   `AGE_MISMATCH` / `DUPLICATE` 同形）：`preview` 把它放进 `conflicts` 而不是 `errors`，
   并回 `conflict_count`；处置是**文件级**的一次选择（`resolution`: `overwrite` / `skip`），
   与测评导入**共用同一套码**（`assessment_import_service` 的 `RESOLUTIONS`——
   各写一份字面量就会有第三种写法悄悄冒出来，而它只在前端提交时变成一句 422）；
   未选而文件里有冲突 → 422，且**任何写入之前**就拦。四个决定与其代价：
   - **「文件内重复学号」仍然是错误**，刻意不升级成冲突：同一份文件里两行都说是同一个
     学号时没有「覆盖」可言（先写的那行等着被后一行覆盖，还是反过来？）。这问的不是
     口径，是文件坏了。所以两条判断分别住在 `check_row` 与 `conflicts_for_row` 里。
   - **覆盖是就地改，不删了重建**（`_overwrite_student`）：`student.id` 被关怀档案、
     测评会话、风险事件、账号范围引用着，全库没有 `ondelete=`（§1）。
   - **覆盖只改姓名的六列**：`name` / `grade_id` / `class_id` / `gender` / `age`，
     外加展示名 `masked_name` 与账号的 `display_name`。**学号不改**——它正是冲突的判据；
     `status` 不改。`OVERWRITABLE_FIELDS` 是这句承诺的常量形式
     （用**文件里的列名**：年级 / 班级），`test_overwrite_touches_exactly_the_columns_it_promises`
     快照每一列、断言「实际变化的列 == 承诺的那些」，所以谁把覆盖改宽了就会红。
     这句承诺的**第三处**是两个导入入口的界面文案，后端看不见它——所以常量与界面
     改一处要想着另一处。
   - **选填列（性别 / 年龄）文件里空着就跳过，不写 NULL**：「这一列我没填」与「把名册上
     这个数抹掉」是两件事，前者是常态（四列的老模板根本没有这两列）。
   - **两个入口都要接**：`DataCenterPage.vue` 与 `OrganizationPage.vue` 各有一份学生导入。
     只接一处的话，从另一个入口进来的管理员会拿到一句 422，而界面上没有能回答它的地方。
   审计的 `detail` 记 `resolution` 的**中文**（`student_resolution_label`）；`None`
   （文件里没有冲突）与「选了放弃」必须长得不一样，否则事后读轨迹的人会以为那次导入
   把冲突默认放行了。
3. `make test` 用**内存 sqlite + ORM metadata 建表**（`tests/conftest.py`），**完全不跑 Alembic**。
   模型与迁移漂移不会被测试发现。
4. 前端**没有路由守卫**（`main.ts` 无 `beforeEach`）。角色门禁靠 `AppLayout.vue` 拿到 `/auth/me` 后
   比对 `meta.role`，不匹配就 `router.push('/login')`——越权访问表现为"被弹回登录页"。
5. `docker-compose.yml` 的 MySQL 是 `root/root`，而 README 与 `core/config.py` 默认值是 `root/password`。
   按 README 手动起 mysql 容器再本地起后端会连不上。
6. E2E 只有 chromium 一个 project，没有视觉回归；移动端布局只有一个 375×667 的登录页冒烟测试。
7. **审计页的搜索提示与后端行为对不上，且 `resource_type` 是裸编码。**
   `AuditPage.vue` 的搜索框写着「搜索行为、对象或用途」，但服务端 `q`（见 §10）只匹配
   action / resource_type / resource_id 三个**编码**，`purpose` 根本不参与匹配。
   同时 `AuditPage.vue` 直接把 `{{ row.resource_type }}` 渲染成 `EXPORT` / `RISK_EVENT` /
   `USER_ACCOUNT`。2026-09-16 曾给这一列加中文映射，随后**撤回**：文案一改，用户照着
   提示输入「关注档案」会一条都搜不到，把「看不懂」换成了「搜不到」，更糟。
   正确的修法是**先加服务端 `resource_type` 筛选器**（枚举下拉，与 `actor_role` 并列），
   再把展示改成中文，最后把提示语改成与 `q` 实际匹配的字段一致。三件事要一起做。
   2026-09-16 起 `DataCenterPage.vue` 的「近期数据任务」**不再打印** `resource_type`（那一格对导入行
   本来不承载信息，导出行有「查看记录」），所以那条链路上不再有裸编码；审计页保持原样，等这三件事
   一起做。
8. **导入的测评记录（`source=IMPORTED`）按系统内作答同一口径判定，但仍会改统计口径。**
   2026-09-16 随「MHT测评记录导入」加：批次任务与导入会话都带 `source`（迁移 `0010_import_source`，
   默认 `IN_SYSTEM`）。下面这些**会**被改：
   - `analytics_service` 的完成率**不按任务过滤** → 批次任务的目标行计入完成率；
   - `attention_count` / `attention_rate` 取**每人最近一场**（2026-09-17 起，见 §11）→
     导入那一场若比校内那一场新，这个学生的等级就来自外部平台，直接进关注率；
     （**它不再跨场次重复计数、也不再只增不减**——旧口径数的是所有 `assessment_result`，
     这一条 2026-09-17 修掉了。上一条「导入会改口径」的结论不变，改的只是「按人」还是「按次」。）
   - `export_service` 的 `high_risk_only` 与「来源」列取**每个学生最新一场会话** → 导入之后
     这个学生的关注等级/用时可能来自外部平台（「来源」列就是为此加的）；
   - `analytics_service.leader_progress` 同受此影响。
   导入链路的学校也写死 `School.code == "QH"`（同缺口 2 的 `school_for_import`），多校部署要先改。
   导入的是**外部平台的题面**：题号按表头映射，**不逐字校验题干**——那份唯一的真实文件里
   13/100 条是同一道题的措辞变体（`#6`「想不起原先掌握的知识」/「当你想不起来原来掌握的知识」），
   逐字比对会把学校唯一的一份文件全拦下来。题号顺序与 `data/mht_scale.json` 一致。
   批次任务在列表上显示「已结束」（目标行全完成，见 §12）；在此之前它永远显示「进行中」。
   文件里的**性别约定是 `2` 男 / `1` 女**（2026-09-17 按学校手上的真实文件改正，
   `GENDER_BY_NUMBER` 此前反着写）。性别**只用来消歧**，从不写回名册（`commit_assessment_import`
   不碰 `student.gender`）。学生信息导入走的是另一套约定（只认 `MALE`/`FEMALE` 或 `男`/`女`，
   **不认数字**），两条链路各管各的常量。

   **2026-09-17：判定口径与系统内作答对齐，「不产生风险事件」那条旧决策被用户推翻。**
   命中重点题（85/97）或总分落入重点关注区间时，导入路径与 `create_or_get_session` 一样调
   `maybe_raise_risk_events`，同样开出风险提示与关怀档案。此前不调用是**刻意**的（用户决策），
   本条与「测试注意」里那句「是决策，不是缺陷」都据此写下；用户 2026-09-17 改变了口径：
   「把校外测的结果拿进来」要的结果就是**和校内的一样能用**——一条只进统计、不进待办的记录
   在关怀队列里是不可见的，而学校导进来正是为了让老师看见。
   `tests/test_assessment_import_api.py::test_import_writes_the_full_record_set_and_the_same_care_records`
   钉住新口径（旧名 `..._and_no_care_records`，那条断言已随决策一起反过来）。

   **判重按「月」为单位，冲突交给老师拍板**（用户 2026-09-17 的另两条要求）：
   - **同一个学生、同一个自然月，只能有一次外部导入**（`existing_import_session`，窗口是
     `month_bounds(tested_on)`）。不同月份是两场不同的测评，各自导入、各自一个批次任务。
     此前按**天**判重，学校把一次普查分两批导（先初一、隔几天再初二）时第二批会被挡在门外。
   - `AGE_MISMATCH`（文件年龄与名册**不相等**，不设容差）与 `DUPLICATE`（本月已有一次导入）是**冲突**，
     不是错误：它们需要人做决定，所以既不进 `errors` 也不进 `warnings`。`preview` 把它们
     **一并放进 `preview_token`**（否则「覆盖上次」这条路走不通），并回 `conflict_count`。
     注意它与 §1 那个 ±1 岁**不是同一件事**：±1 用在 `locate_student`——「这一行说的是哪个学生」
     要能容忍文件是去年那次普查；而「名册上这个数对不对」不设容差，差一岁也要问，因为用户要的
     正是「发现不一致就问」（名册年龄本来就不会自己变，差一岁恰恰是最常见的那种不一致）。
   - 处置是**文件级**的一次选择（`resolution`: `overwrite` / `skip`），不是逐行——一次普查
     两百行逐行点会在第 20 行开始乱点。未选而文件里有冲突 → 422，且**任何写入之前**就拦，
     不留半批数据在库里。
   - `overwrite` **就地改写**那一场（答卷、结果、八维度、用时按新文件重算），不删了重建：
     `risk_event.session_id` / `retest_plan.source_session_id` 都指着这一行，全库没有 `ondelete=`。
     重写时**只收回 `PENDING` 且无人复核的**风险事件——改完文件的重新导入不该留下两条同名待办，
     但心理老师写过的复核是工作记录，不能因为重导一次就消失。
   - `AGE_MISMATCH` 选 `overwrite` 时**会写回名册年龄**（`_update_roster_age` + 审计
     「更新学生年龄」）——这与上面「性别只用来消歧、不写回」不同，是用户明确要求的那一条
     （「如果发现不一致应该询问是否覆盖更新还是放弃导入」，问的就是「要不要用文件里的年龄
     更新名册」）。所以这一列**有一个写入方在导入路径上**，`student.age` 不再是只由
     学生信息导入改的。
   - 判重与冲突判定的日期都按 `tested_on`（文件里的测评日期）算，不是按今天。
   - **同月存在多个历史批次时，复用最新那一批**（2026-09-17 补）：按天编号还留着旧任务时
     （用户库里的九月就有 `IMPORT-20260916-1` 与 `IMPORT-20260917-1` 两行），
     `_task_for_month` 取**最新**的那一行，与 `existing_import_session` 取最新会话对齐。
     取最老的那一行时，提示里的批次号与审计的 `resource_id` 说 A、而真正被就地改写的
     会话属于 B——轨迹答不上「这次导入动了什么」。
   - **导入的记录现在有可查的落点**（2026-09-17）：按人看走「重点学生 → 全部学生」页签
     （`GET /students/results`，未测评的学生也在，§4 记着它的双门槛）；按场看走
     测评任务 → 查看明细（那两列是**这一场**的，§11 的「按场」口径）。
     在此之前两者都不存在，于是导入一批校外普查之后，被判「一般观察」、因而不会开档案的
     学生一个都查不到（用户报的：「我是刚刚导入了一个测评，但查不到这里的记录，比如张三」）。

## 测试注意

- 后端测试从 `db/seed.py` 的种子数据起步（青禾实验学校 / 初一 / 1班 / `S001` 林同学 / `MHT-1.1.0` / `TASK-2026-FALL-MHT`）。
  量表版本由 `seed.MHT_SCALE_VERSION` 一处决定，改版本只改它。
- E2E 依赖 `seed_demo.py` 的演示数据（`2026秋季MHT心理健康筛查`、`青禾实验学校`、`S001`），
  所以**跑 E2E 之前必须先 `make seed-demo`**。`make purge-demo` 之后不补这一步，会有一批
  用例因为"没有数据"而失败：审计分页两条断言首页恰好 20 行、`vocabulary.spec.ts` 的
  `studentWithRetestPlan()` 找不到有复测计划的学生、`app.spec.ts` 的「关闭档案」需要存在一个
  可重开的档案。这些都是数据量依赖，不是回归——正确处理是补数据，不是把断言改松
  （改松会把分页/复测的覆盖一起丢掉）。
- **`app/db/purge.py` 的删除顺序有测试把守，别绕开它。** 全库没有任何 `ondelete=`，
  所以每个外键都是 RESTRICT，父行先删在 MySQL 上是必然的 1451。而
  `tests/conftest.py` 的内存 SQLite **默认不检查外键**，父行先删照样全绿——
  `make reset-db` 原来那段内联脚本就是这么坏的（`risk_event` 排在 `manual_review` 之前，
  而 10 行 `manual_review` 正引用着活着的 `risk_event`）。`tests/test_purge_demo.py`
  自建引擎并在 `connect` 事件里开 `PRAGMA foreign_keys=ON`（pragma 在事务里是空操作，
  必须在连接建立时设），改删除顺序先看这个文件。
  **同一件武器现在还有第二处**：`tests/test_sql_reset_to_baseline.py` 把
  `backend/sql/reset_to_baseline.sql` 的删除顺序按 `Base.metadata` 的外键图重推一遍
  （§16）。它不需要数据库，所以**这条守卫在 sqlite 上也是有效的**——上面那个「只有真库
  会报」的缺口，在 SQL 脚本这一侧被补上了一点。写它时踩过一个坑值得记：判「置 NULL 的
  那条 UPDATE 在不在父行删除之前」时，两个判断必须用**同一套语句序号**，否则比的是
  「第几条 DELETE」与「第几条语句」两个不相干的坐标，测试会一直绿。变异验证时发现的。
- 「学生答题」E2E 用例读写同一个答题会话，因此**该组以 `serial` 串行运行**，并在每个用例前
  通过 `POST /assessment-sessions/{id}/reset` 清空作答。系统配置、量表评分规则两组同样是 `serial`。
- 「布局完整性」用例断言的是 `getComputedStyle` 计算值而非文案——曾捕获过一次"整块组件规则被静默丢弃
  但所有文本断言仍然通过"的故障。改 CSS 时不要绕过它们。2026-09-17 加的那一条守的是
  「查看明细」弹层里的筛选框（§17）：它此前是个裸 `<input>`，拿到的是浏览器默认样式。
  **那条用例需要库里至少有一个测评任务**（要能点到「查看明细」），与这一组其余用例
  （只依赖静态标记）不同——数据被清光时它会红，而红的原因不是样式坏了。
- `e2e/vocabulary.spec.ts` 守的是 §3 那张表的第二面（视图有没有调用标签函数），
  用 `\b(CODE|CODE|…)\b` 匹配正文。词边界是有意的：`_` 属于 `\w`，所以 `\bSTUDENT\b`
  不会误伤 `STUDENT_PSYCH_DETAIL`、`\bACTIVE\b` 不会误伤 `INACTIVE`。误报会让人很快把
  这个测试关掉，改词表时先确认边界仍然收得准。
- **它扫「点开才存在」的区域时踩过两次同样的坑，都是假绿**：档案弹层在 `正在加载明细`
  那一帧就被扫描，明细表还一行没渲染；`/counselor/cases/1` 没有复测计划，页签里的
  `v-for` 空转。所以 `auditModal` 强制要求传 `ready` 定位器（等数据到位再扫），
  复测页签用例先用 `studentWithRetestPlan()` 问接口要一个真有数据的学生。
  给这个文件加用例时照办：**先证明有东西可扫，再断言它干净**——这正是 §测试注意里
  「第二所学校的学生必须带上全链路记录」的同一条教训，换个地方又出现了一次。
  「全部学生」页签是**同一处的第三例**：它切过去之后才请求数据，而 `auditPages` 会点遍
  每个 `button.tab`，所以 `/counselor/cases` 那一趟顺带也扫到它——扫的却是骨架屏那一帧。
  所以那一条单独存在，并且先 `expect(rows.first()).toBeVisible()`。
- **数行数要问接口要，不要写死**：`app.spec.ts` 的「全部学生」用例断言的是「列表的条数与
  名册的行数相等」（`共 N 条` 对 `GET /students` 的 `items.length`），而不是「28 名学生」。
  写死数字会让这条用例在任何人往演示数据里加一个学生之后变红，而红的原因不是功能坏了——
  与「不要给账号表加精确行数断言」是同一条教训。断言**两者相等**才有意义。
  **同一个道理也管界面上的品牌文案**（2026-09-17 补）：校名、品牌名、副标题都是操作员
  可配的（系统配置 → 机构标识），断言它们的地方一律从 `/public/branding` **现取**，
  不写死。两处已经改成这样：账号表「范围」列断言的校名（用例里的 `configuredSchoolName`）
  与移动端登录页断言的两条（`branding.brand_name` / `brand_subtitle`）。
  写死的代价实测过一次：把品牌名配成「MHT」之后 `login page works on mobile viewport`
  就红了，而它断了的那一行正是「品牌来自 `/public/branding`」这句注释要证明的事。
  **例外是「系统配置」那一组内部**的两处 `toHaveValue('青禾实验学校')`：那一组自己把配置
  重置到了出厂值，它断言的正是「出厂值是这一个」——那不是数据依赖，是那条用例的主题。
- **跑 e2e 不许改掉操作员自己配的东西**（2026-09-17 补，与缺口 8 是同一条规矩的另一面）。
  「系统配置」那一组此前的 `beforeEach` / `afterEach` 都调 `/admin/settings/org/reset`，
  于是**每一次全量 e2e 都把 `org` 这一组恢复成出厂值**。用例需要的只是「一个已知的起点」，
  出厂值恰好是最方便的那一个——但「恢复成出厂值」与「恢复成原样」是两件事，在一台有人用过的
  机器上它们差着一整份配置：操作员把校名配成「第十三中学」、品牌名配成「MHT」，
  下一次跑 e2e 就全没了（`audit_log` 里两次「更新系统配置」之间夹着一串「重置系统配置」，
  自 2026-09-16 起 520 条）。他不会想到是测试干的——他会以为配置没保存住。
  现在这一组在 `beforeEach` 里**先读一份快照**（`GET /admin/settings` 的 `org`）再重置，
  `afterEach` 按原样 `PUT` 回去。两个细节都不能省：
  **快照只读一次**（某条用例红在半路时，重读会把这次用例的残留记成「操作员的原值」，
  然后忠实地把它恢复回去），**管理员 token 一起存进快照**（`afterEach` 跑在用例留下的那个
  token 上，而「心理老师进不去配置页」那条留下的是心理老师的，PUT 会 403 而静默失败）。
  用例内部照旧可以测「恢复默认」——它只是这一组中间的一个状态，不再越过这一组的边界。
  缺口 8 那条说的是「别为了断言去改数据」，这条说的是「别为了用例的起点去改数据」，
  共享库上这两条是同一条。
  这一组之外还有一处**已知且接受**的残留：「账号管理 → 新建账号」那条每跑一次就留下一个
  停用的临时账号（账号是时间戳所以不会撞），而账号**没有删除接口**（§4：停用 ≠ 删除，
  `audit_logs` / `manual_review` 都指着 `user_account`）。它看得见、点得掉、不影响别的用例，
  与「配置被静默恢复成出厂值」不是一类事——后者是用户不会想到去看的地方。
- **学生导入的冲突面板 e2e 只跑「放弃」那一支**（`app.spec.ts` 的「学生信息导入」组）。
  共享的开发库里 `S001` 是种子名册上的林同学，选「覆盖」会把他挪出种子里的班，跑一次
  就让**别的**用例看到另一份名册——这正是「不要靠改测试让库里的数据好看」那条的反面：
  这里不靠改断言，靠不碰数据。所以那一条**提交**一次「放弃」（不写任何名册字段，
  只留一行审计），断言提交前后名册里都没有文件里的那个名字。
  「覆盖」由后端逐列钉住（`test_overwrite_updates_the_roster_entry_in_place` 与
  `test_overwrite_touches_exactly_the_columns_it_promises`），e2e 要看的是
  **界面有没有地方回答这个问题**——把 `:disabled` 上的 `needsStudentResolution` 摘掉，
  那一条会在 `toBeDisabled` 处变红（验过了）。
- **老测试对数据范围改动是零信号**：`conftest.py` 只种一所学校 / 一个年级 / 一个班 / 一个学生，
  且每个账号的 scope 行都覆盖全部，所以任何范围谓词——写对、写反、还是不写——老测试全绿。
  验证数据范围只能靠 `tests/test_data_scope.py`，它自建第二所学校（云海中学）。
  改这一块时请照同样的办法验证：**临时把入口改成空操作，确认新测试会变红**，否则测试是摆设。
  两个入口（§9）都要各验一次：把守卫改成空操作 → 12 红 / 7 绿；把谓词改成恒真 → 23 红 / 8 绿。
  第二所学校的学生**必须带上全链路记录**（任务目标、会话、结果、维度结果、风险事件、档案、
  跟进、复测、审计），否则"列表里没有他"这条断言会因为没有他而通过，测试白写。
  日期用相对今天算，提醒与复测面板只看固定窗口，写死日期会让测试自己过期。
- **不要给账号表加精确行数断言**：`admin can see account list` 原本断言恰好 4 行，但
  `seed_demo._ensure_student_account` 会给每个演示学生建账号（4 个基础账号 + 27 个演示学生 = 31），
  账号表又 `:page-size="10"` 只显示首页，断言于是收到 10 而失败——而 E2E 本身依赖 `seed_demo` 数据，
  两个前提互斥。该断言已删除，改为断言四个种子账号可见（它们 id 最小，始终在首页）。
  行数只反映数据量，不反映功能对错。
  审计分页那两条断言首页恰好 20 行是**同一条教训的另一处**：它们依赖库里有演示数据，
  清库后就红，而红的原因不是功能坏了。
  `make reset-db` 只清测评数据、不删 `user_account`，所以种过演示数据它就回不去；
  回得去的是 `make purge-demo`。另外，新增断言前先问一句"它在只有一个学生的库上还成立吗"。
  同一页 2026-09-17 补的那个搜索框**就是这条教训的产物**：账号表一直是 `:page-size="10"`，
  而「新建账号」建出来的账号 id 最大、落在最后一页——e2e 断言刚建的那一行时必须先搜出来，
  否则它断言的就成了「库里有多少个账号」。用例里账号用**时间戳**（账号是唯一约束，
  写死一串数字第二次跑就撞「已经有一个员工账号在用了」），范围**选「全校」**——
  选年级或班级会在 `user_scope` 里留下一条指着**演示**年级/班级的行，
  而 `make purge-demo` 遇到仍被引用的班级会跳过不删（`db/purge.py`），
  跑一次 e2e 就给那次清理留下一块擦不掉的残渣。三点都写在用例的注释里。
- **导入的测评与系统内作答判定口径一致，「会」开出风险事件与关怀档案**
  （2026-09-17 起，此前相反，见缺口 8）。
  `tests/test_assessment_import_api.py::test_import_writes_the_full_record_set_and_the_same_care_records`
  用一个「总分落在重点关注区间**且**重点题 85 答『是』」的记录钉住它——系统内作答时这两条
  中的任何一条都会开出档案，导入的这一场同样。所以别把 `maybe_raise_risk_events` 从导入路径上
  摘掉「还原旧口径」：那会让导入的记录进不了关怀队列与工作台待办，
  而学校要的正是「把校外测的结果拿进来一起看」。
  （变异验证：在导入路径上摘掉这个函数，该文件里有两条用例变红。）
- **MHT测评记录导入只收 `.csv`，这条链路上没有 Excel 依赖。** 学校手上的 `.xlsx` 由人
  「另存为 CSV」——`parse_assessment_import` 按扩展名整份拒掉其它格式，并且**说清该怎么办**。
  曾经写过一版纯 stdlib 的 xlsx 读取器（17 个测试），2026-09-16 按用户要求删掉了；
  **别为了一份表格式导出再引入 openpyxl**。同时 `_read_csv` 认 UTF-8 与 **GBK**：
  中文 Excel/WPS 在 Windows 上「另存为 CSV」写的就是 GBK，只按 UTF-8 解会变成一个 500，
  而那本该是「照读不误」。
  它在 UI 上的那一面（`e2e/app.spec.ts` 的「MHT测评记录导入」用例）**刻意不断言**报的是
  「班级不存在」还是「学生不存在」：那取决于库里此刻有没有 `704` 这个班，而库是共享的
  （`make seed-demo` 只有「1班」，但任何一次学生导入或人工建班都会让 `704` 出现）。
  两种文案都必然写出翻译后的班级名，断言的正是那个。回退办法与本条无关，
  真正钉住「两种原因分开报」的是后端测试。
- **「无障碍契约」组的六条断言的是属性与计算值，不是文案**（同「布局完整性」那一组的道理）。
  它们读的是 `aria-sort` / `aria-pressed` / `role=status` / `document.body.style.overflow`
  以及 `:focus-visible` 的计算值，所以整块样式或某个属性被静默丢掉时会红——
  而这类破坏在文本断言下是全绿的。
  其中「弹窗开着切页，滚动锁不留给下一页」用的是 `page.evaluate(() => window.history.back())`：
  **只有客户端路由（popstate）才会在弹窗开着的情况下把组件卸载**，而这正是
  `onUnmounted(deactivate)` 唯一从界面上可达的触发路径——点导航被遮罩挡住，
  `page.goto` 是整页刷新、`body.style` 本来就会被重置，两者都验不了这条。
- **「失败与竞态不留下旧数据」组全是 `page.route` 桩，不打真接口**。写新用例时注意：
  - 判据用**判定函数**（`page.route(url => …, handler)`）而不是字符串通配，路径里带 id 的
    端点尤其要这样；按前缀批量失败用 `failApiPaths`，按正则用 `failApiPathsMatching`。
  - 竞态靠**让先发的那一个故意慢**（`q === '慢'` 时 `setTimeout` 800ms），
    所以断言的是「慢的答案没出现」，而不是「快的答案出现了」——后者在没修的实现上也成立。
  - 「一个字都没收到」用 `route.abort()`（`fetch` 抛 `TypeError`），
    「服务端答了话」用 `route.fulfill({ status: 500, … })`，两者走的是不同的分支（§2），
    只测一条等于另一种仍然与密码错共用一句话。
- 「先证明有东西可扫」在这两个新组里各出现一次：审计页竞态那条先断言「共 22 条」
  再断言「慢的答案」不出现（否则表格空着也满足），完成明细失败那条先确认弹层开着。
  这是 §测试注意里那件事的**第三、第四例**，加用例时照办。
