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
| `make test` | 后端 pytest（730 个测试）。**跑在真 MySQL 上**：建一个 `<库名>_test`、`alembic upgrade head` 建表、用完即弃。库名不以 `_test` 结尾会拒绝运行。见 §20 |
| `make e2e` | Playwright（127 个测试，需两个服务都在跑） |
| `make migrate` / `make seed` | Alembic 迁移 / 初始化数据 |
| `make seed-demo` | 填入演示数据（多年级班级、各分数段测评、各阶段档案），可重复执行 |
| `make reset-db` | 清空测评数据并重新种子（保留名册与账号） |
| `make purge-demo` | 把 `seed-demo` 填进去的一切删干净，回到只有 `seed.py` 基线的状态。**仅用于开发库** |
| `mysql … < backend/sql/reset_to_baseline.sql` | 清到「只有 admin + 基本配置」。给**别处的新环境**用，见 §16 |
| `make db-upgrade-sql` | 重新渲染 `backend/sql/upgrade_from_v1_0_0.sql`（+ `dist/` 一份）。**改过 alembic 迁移就要跑**，见 §30 |
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
- 迁移在 `alembic/versions/`，当前 0001–0018（0013/0014 是 V1.2 对齐，0015 是阶段 2 的
  `calculation_status` 回填，0016 是导入批次加 `batch_name` 列，0017 是 JSON null 归一化
  这条**数据**变更，0018 是导入行加 `conflict_resolution` 列；见 §21 / §23 / §25 / §27）。
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
    manual-migrate.ps1       只升数据库（不起服务、不碰程序文件），§30
    serve_frontend.py        只发前端时的静态服务器 + /api 反代（纯标准库）
    手工启动后端.bat 手工启动前端.bat  ★ 那个出路的两个入口。纯 ASCII（只有文件名是中文）
    数据库增量升级.bat         ★ 停在 V1.0.0 的库要单独升时的那一枚。纯 ASCII，§30
  build_migration_sql.py    生成 `backend/sql/upgrade_from_v1_0_0.sql`（`make db-upgrade-sql`，§30）
  README.md                  面向维护者：怎么重出包、加一个依赖要改哪两处
```

产物 `dist/心晴部署包.zip`（约 11MB）→ 拷到目标机 → 解压 → 双击。包里带着 Windows 版的
wheel 与构建好的前端，运行环境是**安装时用目标机上那个 Python 3.11 现建的 venv**
（2026-09-18 起不再内嵌 CPython，见下文），所以目标机上不需要 pip、不需要网络，
唯一要有的外部东西是 Python 3.11 x64 与 MySQL 8.0。**运维文档面向两种人，是两份东西**：
`部署说明.txt` 给操作员（非技术），`deploy/README.md` 给下一个改这套东西的人。

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
  索引，MySQL 会以 **1553** 拒绝删一条外键正在用的索引。**2026-09-19 之前这条错误只有
  真库会报**——当时测试跑在内存 sqlite 上，它既不检查外键也不跑 Alembic（缺口 3，
  现已关闭）；现在 `make test` 每一次都真的走这条迁移。
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
- **`GET /assessment-tasks/{id}/targets`（2026-09-19 第 1 期）是上面这一条的镜像**：
  同样两道门槛（任务读者 + `ORG_ACCOUNT: {MANAGE, READ_BASIC, READ_SUMMARY}`），
  拦的是同一个形状——那边的心理详情，这边的任务读者，都不得成为**读名册**的旁路。
  详细理由见 §22。
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
- **凡是截断，都要自己说出来。** 全站现在有六处上限，每一处都有一句「还有多少没显示」
  加一条出路：工作台提醒（`另有 N 项未显示，请到「重点学生」逐条处理`）、工作台队列
  （`另有 N 份一般观察档案`）、题库导入预览的前 20 题、学生导入预览的前 20 行、
  任务完成明细的前 200 条（那一处还指着「导出CSV」）、补发预览的前 200 人（§22）。
  静默丢数据与「这里就只有这么多」在屏幕上长得一模一样，而读者会照那个数安排工作。
  §9 的「范围数字必须写明口径」是同一条道理换了个维度。
  **截断只许影响「显示」，绝不许影响「写入」**——补发那一处的确认分支为此刻意**重新取一遍、
  不带 `limit`**：拿那 200 条样本去插会把另外的人静默丢掉，而屏幕上正写着「将新增 350 人」。
  上限住在渲染的那一侧（`SUPPLEMENT_CANDIDATE_LIMIT` 是给眼睛的），落库的那一侧不许有它。
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
| **`deactivate()` 同时要 `dropModal`**（2026-09-19 第 1 期加，`composables/modalStack.ts`） | 同上 | 那一层**永久占着「打开中」那一叠**，后面每开一个弹层都被顶高一层。它与上一行在同一个函数里，理由同源：卸载这条路径不走 `watch` |
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

#### `sql/` 下现在有**三个**文件，分工不要混（2026-09-18 补，2026-09-20 加第三个）

| 文件 | 建表吗 | 删行吗 | 改行吗 | 谁用 |
|---|---|---|---|---|
| `reset_to_baseline.sql` | 不 | 删（清成基线） | 不 | 上面那张表 |
| `schema_mysql8.sql` | **建**（34 张表，父先子后） | 不 | 不 | §18 的 `schema_prepared` 分工 |
| `upgrade_from_v1_0_0.sql` | **改**（`0012 → 0018` 六条迁移的渲染） | 不 | 不 | §30：停在 V1.0.0 的库 |

前两份都是**手写并受静态守卫**（`test_sql_schema_matches_models.py` /
`test_sql_reset_to_baseline.py`）；第三份**是生成的**（`deploy/build_migration_sql.py`，
`make db-upgrade-sql`），守卫是逐字节比对今天这棵树渲染出来的东西（§30）。**谁也不许手改
它**——改了下次重跑就没了，而且守卫会红。

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
——测试库更看不见（那天的测试跑在内存 sqlite 上，缺口 3，2026-09-19 已关闭）。
这也是「次序」那一条存在的原因，它不是排版要求。

**约束名照抄 MySQL 自动生成的 `<表>_ibfk_<N>`，不要改成有意义的名字。** 迁移是按名字引用约束的
（`0012` 已经在 `op.drop_constraint("uq_care_case_student_status", …)`），一个更好读的名字会让
这个文件建的库与 `make migrate` 建的库在**未来某条迁移上**分岔，而分岔得看不出来。

三条不变量由 `app/tests/test_sql_reset_to_baseline.py` 把守（每条都做过变异验证）：

1. **覆盖面与 `purge.py` 对齐**：`ASSESSMENT_TABLES` 里的表，SQL 里必须都出现。同一件事在
   Python 与 SQL 各写一份，比一次是唯一能让它们不漂的办法。
2. **子先父后，或那条出处列先被置空**，二者认一个。全库没有 `ondelete=`，父行先删在 MySQL 上
   是必然的 1451——2026-09-19 之前**这个错只有真库会报**（当时的测试库是内存 sqlite，
   默认不检查外键，缺口 3），第一版就是把 `student` 排在了 `user_scope` 前面。两条出路各有各的用处：调顺序解决得了
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
   **ARM 的电脑（2026-09-20）：包不换，只补一条架构自检。** 用户问「当前的 window 包
   适用于 arm 架构下的 window 吗」。31 个 wheel 里有 3 个上游**没有** `win_arm64` 构建
   （`cryptography==50.0.1`、`httptools==0.8.0`、`PyYAML==6.0.3`），而 `cryptography`
   不可省——正是下面第 4 条那件事。所以**原生 ARM64 包造不出来**，出路是反过来的：
   在 ARM Windows 上装 python.org 的 **Windows installer (64-bit)**，Windows 自带 x64
   模拟（WOW64），包里那些 `cp311-cp311-win_amd64` 照装照跑。于是改动只剩一处诊断
   ——探到 ARM64 解释器时给一句中文指路，而不是让它一路走到 `pip install` 再报一句
   关于 wheel 的英文。

   **判据是 `sysconfig.get_platform() != 'win-amd64'`，不是 `platform.machine()`。**
   两者在 Windows 上会给出**相反**的答案，而错的那个看起来完全合理：
   `get_platform()` 读的是 `sys.version` 里**编译时**烙进去的架构串（x64 那份是
   `(AMD64)`、ARM64 那份是 `(ARM64)`），落在 `TARGET_TO_PLAT` 上，**从不看运行时的
   宿主**；而 `platform.machine()` 回的是 `PROCESSOR_ARCHITEW6432 or
   PROCESSOR_ARCHITECTURE`——它自己的源码注释就写着「WOW64 进程会把真实架构盖住」，
   于是一份**能用**的 x64 解释器跑在 ARM 机器上会被判成 ARM64，正好把这条判据要放行的
   那一种拦下来。顺带一句：**这个字符串与 pip 拼平台标签用的是同一个**，所以「这条判据
   过了」与「pip 吃得下这些 wheel」是同一件事。

   候选表**把 ARM64 那一份也列进去**，但只为了**够得着、然后拒掉**——与 32 位那份
   同一个理由。python.org 的 ARM64 版装到 `Program Files\Python311-arm64\`、PEP 514
   标签是 `3.11-arm64`（x64 是 `Python311\` / `3.11`），而 `py -3.11` 的标签**前缀
   匹配**会挑中它。列进去的收益是操作员拿到「你装的是 ARM64 版，去装 (64-bit)」，
   而不是「这台电脑上找不到 Python 3.11」——后者让他唯一的推理是「再装一遍」，
   而他已经装过一遍了。探针多一个退出码 `4 = 不是 win-amd64 平台`；
   `$sawArm64` / `$script:SawArm64Python` 必须在候选循环**之前**初始化
   （`Set-StrictMode -Version Latest` 读一个没赋过值的变量会抛），两个脚本各一份。
   守卫是 `test_windows_assets.py::test_an_arm64_python_is_diagnosed_instead_of_chased_in_a_circle`
   （6 条变异全部变红）。

   **残余风险，如实记**：ARM64 那份若任何一个候选路径都够不着（装在别处、又不在 PATH、
   `py` 也没注册它），探针就不会跑，`$sawArm64` 留在 `$false`，操作员拿到的是通用文案、
   没有 ARM64 那一句。**这条诊断发不发得出来，取决于候选表全不全。**
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

### 20. 测试基线：真 MySQL，表由迁移建出来（2026-09-19）

**这一节是缺口 3 关闭的全部记录。** 用户 2026-09-19 的指令是「后续所有的开发均要基于
mysql，不再考虑 sqlite」，这条改动就是那句话的落地。

#### 它为什么非改不可（不是"更好"，是"唯一的信号"）

用户把 V1.2 的 DDL 手工导进了开发库，库里变成 35 张表（34 张应用表 + `alembic_version`），
而 `alembic_version` 还停在 `0012`，代码与 ORM 停在 V1.0。三个新列是 **NOT NULL 且没有
默认值**，V1.0 的写入路径一个都不知道它们：

| 列 | 类型 | 谁该写它 |
|---|---|---|
| `assessment_session.school_id` | `int NOT NULL` | `create_or_get_session` / `assessment_import_service` |
| `assessment_target.school_id_snapshot` | `int NOT NULL` | `task_service` / `seed.py` / 导入链路 |
| `risk_event.signal_type` | `varchar(64) NOT NULL` | `maybe_raise_risk_events` |

**登录能进、页面能开，但学生一开卷子就是 MySQL 1364**——而 523 个测试一个都没红。
原因不是测试写得不好，是**表由模型建出来的**：模型少三列，它就建一张少三列的表，
于是「模型和自己一致」这条命题永远成立，而它跟真库没有任何关系。

（那段状态**已经不在了**：2026-09-19 当天先把开发库 `DROP DATABASE` 重建成 0012 的
V1.0 结构（24 张应用表）——这是上面那个描述里唯一一处「现在不成立」的地方。
V1.2 的表会在阶段 2 由迁移**建出来**，而不是靠人手工导入。）

#### 现在的形状（`app/tests/mysql_support.py` + `conftest.py`）

```python
test_engine (session 级)  → resolve_test_database_url()   # 库名必须以 _test 结尾，否则拒绝跑
                          → ensure_database(url)          # DROP DATABASE + CREATE，显式 utf8mb4_0900_ai_ci
                          → run_migrations(url)           # 子进程 `alembic upgrade head`
seeded_engine (session 级) → seed_development_data 一次并 commit
db_session   (每个用例)    → 外层事务 + join_transaction_mode="create_savepoint"
```

四条约定，每一条都是踩出来的：

- **`run_migrations` 走子进程 + 环境变量，不调 `alembic.command`。**
  `alembic/env.py:18` **无条件**拿 `get_settings().database_url` 覆盖 `sqlalchemy.url`，
  所以进程内调用会去迁移**开发库**——而 `ensure_database` 刚刚 DROP 的是测试库。
- **库名不以 `_test` 结尾就拒绝运行。** 这套 fixture 会 `DROP DATABASE`，指错库就是销毁
  生产数据。与 `reset_to_baseline.sql` 的「找不到 admin 时一条都不删」是同一条口径：
  宁可拦住。
- **种子只在 session 级提交一次。** 「每个用例各 `seed_development_data` 一次」这条最直觉
  的写法在 MySQL 上是错的：**`AUTO_INCREMENT` 计数器不参与回滚**，于是用例 1 拿到
  `school.id = 1`、用例 2 拿到 2——而测试里到处是 `{"scope_type": "SCHOOL", "scope_id": 1}`
  这种硬编码。症状是**同一个文件里第一个用例通过、其余全挂**，看起来像接口坏了。
  提交一次之后种子行的 id 就钉死了。（这也意味着 `seeded_engine` 与 `db_session` 不是
  同一个东西，别把前者塞进后者。）
- **每个用例的隔离靠 `join_transaction_mode="create_savepoint"`。** 服务层大量
  `db.commit()`，这个模式下每次 commit 变成释放一个 savepoint，外层事务兜底回滚。
  不需要 truncate，也不需要知道有哪些表。

**要一个"自己的库"时用 `throwaway_database()`**：建一个 `<测试库>_x<n>`、跑迁移
（或 `with_schema=False` 不跑）、yield URL、用完 DROP。`test_check_empty.py` 与
`test_ensure_schema.py` 走的就是它——那两个模块在生产上都是「拿一个连接串进门、自己建
引擎」的独立命令，罩在事务里测不了。

#### 阶段 1 的产出：一条新的守卫

`test_ensure_schema.py::test_the_migrated_database_matches_the_models_exactly`
——空库跑完迁移，逐表逐列与 `Base.metadata` 比。**给模型加一列而不给迁移加，它当场红。**

变异验证做了两次，两条路都验过：往 `Student` 加一个 `drift_probe` 列不给迁移加，
①这条守卫以 `{'student': ['drift_probe']}` 红；②任意一个用 `db_session` 的用例以
`pymysql.err.OperationalError (1054, "Unknown column 'drift_probe' in 'field list'")` 红。
两次都逐字节还原（`cmp` 比对过）。

#### 阶段 2 的产出：四条守卫，各自补上一个看得见的盲区

上面那一条只比**表名与列名**（`compare_schema` 的判据，见 `ensure_schema.py` 开头）。
阶段 2 的 V1.2 改动里有一半不是列名：生成列、复合外键、唯一键、以及 `sql/` 下那份
快照。四条新守卫都在 `test_migrations_build_the_models.py`，判据是
`alembic.autogenerate.compare_metadata`——它比表/列/可空性/类型/索引/唯一约束/**外键的
列对**，比 `compare_schema` 严一档：

| 守卫 | 钉住什么 | 它比 `compare_metadata` 多出来的那一层 |
|---|---|---|
| `test_an_empty_database_migrated_to_head_matches_the_models` | 空库 `alembic upgrade head` 之后 `compare_metadata` 无差 | ——（这条是 `compare_schema` 的加强版：同一件事，判据换成能看见索引与外键的那种） |
| `test_the_snapshot_file_builds_a_database_matching_the_models` | `sql/schema_mysql8.sql` **真的执行一遍**（`FOREIGN_KEY_CHECKS=1`）建出来的库，同样无差 | 快照那份文件此前只有静态文本比对（§16），**表达式改了但它仍然「长得像」时静态比对看不出来** |
| `test_exactly_the_computed_columns_are_generated_in_the_database` | 库里的 `GENERATION_EXPRESSION` 集合与模型声明的一一对应 | `compare_metadata` **看不见 `Computed`**：两边都少声明时它一样是绿的 |
| `test_the_databases_foreign_key_names_are_the_models_ones` | 库里每条外键的**名字**与模型给的一致 | `compare_metadata` 比的是外键的**列对**，**名字不在判据里**——重命名一条外键它能一路放行 |

两条刻意的不比，理由写在那个模块的 docstring 里，别当成漏了：

- **`server_default` 不比**（`compare_server_default=True` 会多出 20 条假差异：MySQL 反射
  时把默认值外面的引号剥掉了，两边永远对不上）。
- **外键的名字要单独一条守卫**——它不在 `compare_metadata` 的判据里，见上表最后一行。

**六条变异全部变红**（`/tmp/xlp_ref/mutate_db_guards.py`，与纯文本那份分开是因为这一组
每条都要重建一次测试库）：模型加一列 / 快照把 `student_no` 改窄 / 快照少一条唯一键 /
模型不再声明 `Computed` / 模型把 `student_ibfk_4` 改名 / 迁移把 `student_ibfk_4` 改名。
基准一律取 `shutil.copy2` 出来的落盘备份，改完逐字节比回去——§18 那条「变异验证的复原
校验必须拿落盘的原始字节当基准」。**这一组不能与全量套件同时跑**：它们抢同一个
`xinliceping_test`，而 session 级的 `test_engine` 进门就 DROP DATABASE。

**2026-09-20 补：这条比它原来写的宽——`pytest` 的任意两个进程都不能同时跑，不只是这一组。**
同一天在跑全量套件（后台）的同时又跑了一个**单文件**（`pytest app/tests/test_sensitive_reads.py`），
两边都指着 `xinliceping_test`：后起的那个 `ensure_database` 把库 DROP 掉重建，
先起的那一个于是拿到 `88 failed, 582 passed, 26 errors`——**报告里没有任何一句话说得出
「另一个 pytest 正在跑」**，看起来就是一次大面积回归。判据是那句 `DROP DATABASE` 落在
`test_engine` 这个 session 级 fixture 里，而它**每一个 pytest 进程都会执行一次**。
所以「等前一个跑完」不是习惯问题：变异验证、单文件复跑与全量套件三者之间必须串行。
（开发库 `xinliceping` 不受影响——测试库是独立的一个。）

#### 真库上实测到的四处差异（**一条都没靠放宽断言过**）

结果是 **524 passed / 0 failed / 210.35s**（阶段 1 当时的数），中间只红过一次，就是下面
第一行那个。阶段 2 加完 18 条用例之后是 **542 passed / 0 failed / 219.14s**，见下。
计划里预估「会打到一批用例」，实际只打到一条——但这四条差异**在生产上都是真的**，
所以逐条记下来：知道它们为什么没咬到人，比知道它们存在更有用。

| 差异 | SQLite（当年） | MySQL 8.4.4（实测） | 处置 |
|---|---|---|---|
| `datetime` 精度 | 保留微秒 | `DATETIME(0)` **四舍五入** | **改生产代码**：`now_utc_naive()` 截断到整秒，见下。**这是本次唯一一处生产缺陷** |
| `func.now()` 的时区 | 与 Python 的 UTC 相同 | `now()` = UTC+8，`utc_timestamp()` = UTC，**差 8 小时** | **没有一条用例真的撞上，所以一行都没改**——理由见下 |
| `utf8mb4_0900_ai_ci` | `'ABC'='abc'` → 0 | **→ 1**，不区分大小写 | 同上：没有用例依赖大小写敏感，测试跟着生产走 |
| 同秒内两行的顺序 | 微秒能分开 | 截断到整秒后**并列** | 由既有的 `id` 兜底，`latest_session_order()` 的口径不变 |

**另外两条为什么没咬到人，值得记清楚**（不然下次会以为它们不重要）：

- **时区**：全库有两个时钟，而它们**从不碰面**。`assessment_session.started_at` /
  `submitted_at` 由 `now_utc_naive()` 写（UTC 朴素），而 `task_service.effective_task_status`
  的窗口比较用的是 `datetime.now()`（**本地**朴素，`:90`，理由写在 `:84-86`：
  `start_at`/`end_at` 是操作员填的墙钟时间）。少了任何一边的注释都看不出来，
  而生产上它们真的差 8 小时——**别把它们统一起来**，那是两个问题：
  「这一刻是几点」（内网时间戳）与「学校的窗口开了没有」（墙上那口钟）。
- **大小写**：没有一条用例靠 `'ABC' <> 'abc'` 成立。唯一相关的是
  `test_deploy_bootstrap.py::test_the_account_lookup_is_case_insensitive`，而那个
  「不敏感」是代码自己折的（`func.lower()`），不是靠数据库的排序规则。

**`now_utc_naive()` 的截断是一次真的生产改动**（`services/assessment_service.py:25`），
理由是两条同时成立：

- **写进去的值必须等于读回来的值。** `POST /submit` 立刻回的是内存里那个对象（带微秒），
  同一份数据下一次 `GET` 回来是库里的值——同一个字段两个值，客户端没有办法知道哪个算数。
  `test_assessment_api.py` 那条「同一个 Idempotency-Key 重放回来的 payload 与第一次逐字
  相同」就是被这个打破的（实测报 `'04:19:20' != '04:19:20.320185'`）。
- **MySQL 是四舍五入，不是截断**：`.7` 进到下一秒，存下来的 `submitted_at` 可能比真实
  时刻**晚**半秒，而 `latest_session_order()` 按它排序。截断是向下取整——并列仍然可能
  （由 `id` 兜底），但**顺序不会被颠倒**。

没有损失任何精度：**全库没有一列是 `DATETIME(6)`**。

**顺带否掉了一个疑似缺陷**：曾怀疑「答题用时差 8 小时」，因为 `assessment.py:88` 调
`save_answer` 时没传 `answered_at`（走 Python UTC），而 `submit_session` 算时长时可能用了
别的口径。写了个一次性探针（`throwaway_database` + 100 次 `save_answer` + 睡 2 秒 +
`submit_session`）实测：`answered_at` 与 `submitted_at` 都是 Python-UTC，
`duration_seconds: 2` 正确。**没有这个缺陷**，不用改。

#### 耗时：**比内存 sqlite 还快**（实测，与直觉相反）

```
524 passed, 5 warnings in 210.35s (0:03:30)   # 阶段 1 当时的数
542 passed, 5 warnings in 219.14s (0:03:39)   # 阶段 2 收尾时的数
```

**从零建库也验过**（计划里的验证第 6 条）：先 `DROP DATABASE xinliceping_test`、
确认 `SHOW DATABASES` 里数不到它，再跑一遍——`524 passed, 5 warnings in 208.74s (0:03:28)`。
这一步其实每次运行都做了（`ensure_database` 进门就 DROP+CREATE），单独跑一次是为了
把「它不是靠上一次留下的库才绿的」变成一条有出处的记录。

**阶段 2 的 18 条新用例只多花 8.8 秒**，因为它们大多是静态比对或复用同一个 session 级的
`test_engine`（只有 `test_migrations_build_the_models.py` 那两类各自建一次性库）。

旧的 sqlite 基线是 **238s**。搬过来**更快**了，原因是种子只跑一次：
旧写法每个用例各 `seed_development_data` 一遍（一所学校 + 年级 + 班级 + 名册 +
100 道题的量表 + 评分规则 + 任务与目标行），现在 session 级一次，每个用例只开一个
savepoint。省下的那 524 遍种子比网络往返贵得多。

计划里预估过「10–20 分钟」，**那个估计是错的**，错在它假设种子还是每个用例一次。
代价不是零（每次 `connect` 与每条 SQL 都真的走一次 socket），但它没有变成主要项。

**不要靠减少用例把耗时压回去**——那会把这次迁移唯一的产出（真实的差异清单）扔掉。

**e2e 没被波及**：`make seed-demo` + `npx playwright test` → `113 passed (24.2s)`。
测试链搬家动的是后端那一侧（`conftest.py` / `mysql_support.py`），前端与 API 的形状
一个字没改，所以这一条是确认而不是修复。

### 21. V1.2 对齐：两条迁移，一条判据（2026-09-19）

V1.2 是一次**纯对齐**（用户口径：先只做对齐，不加服务 / 端点 / 前端）。产物是
`0013_v12_expand` + `0014_v12_enforce` 两条迁移、十张新表、既有表上的 54 条新列、
ORM 模型、两份 SQL 快照、以及上面那六条守卫。

#### 为什么是两条，判据只有一句

**「这条 DDL 在只读过 V1.0 数据的库上会不会失败。会失败的都归 0014。」**

这句话不是分类学，是操作上的分界：0013 是**只加不改**（新表、可空的新列、按规则回填），
所以它在一份有真实数据的旧库上**不可能失败**；0014 全是**收紧**（`NOT NULL`、生成列、
唯一键、复合外键），每一条都在断言「旧数据里没有反例」——而它能不能成功，取决于那所
学校真实数据的形状，不取决于我们的代码。分开之后，出问题时永远知道是「加」坏了还是
「收」坏了：

| | 0013_v12_expand | 0014_v12_enforce |
|---|---|---|
| 做什么 | 十张新表 + 54 条新列 + 回填 | `NOT NULL` / 生成列 / 唯一键 / 复合外键 |
| 在旧库上会失败吗 | 不会 | **会，而且一定会有一部分学校失败** |
| 失败了怎么办 | 那是 bug | 那是**数据里真有反例**，人工处置，不是改代码 |

失败的形状是 `1062`（重复）或 `1452`（外键找不到父行）。

#### 前置校验全部排在 DDL 之前——这是 0014 的全部结构

**MySQL 的 DDL 不在事务里。** 0014 里十二条校验挤在文件开头、任何一条 `ALTER` 之前，
不是排版偏好：中途撞 `1062` 会留下一个**一半收紧、一半没收**的库，而报出来的是一句
英文 MySQL 错，离真正的原因很远。校验失败时改不动任何东西，重跑一次还是同一个结果。

十二条里真正会在真实数据上失败的是这四类（其余是「由既有约束保证、但换了约束就没人
保证」的便宜断言）：

- 同一名学生有**多份在办档案**——`uq_care_case_one_active_per_student` 装不上去；
- `retest_plan` / `manual_review` / `follow_up_record` / `family_contact_record` /
  `care_case_event` 上 `care_case_id` 与 `student_id` 指着**不同的学生**；
- 学生的年级 / 班级不属于他所在的那所学校；
- `risk_event` 的 `(session_id, trigger_rule, rule_version)` 重复。

**`uq_session_task_student` 与 `uq_session_task_student_attempt` 必须在同一步里换**
（先删后加会开出「一场任务里谁都能开第二份卷子」的一段窗口，并发的
`create_or_get_session` 正好在那个窗口里重复插入）。

#### 升级预演：在一个忠实的克隆上真跑过（2026-09-19）

**这是本次唯一一条只有真数据能给答案的验证**，所以做法是造一份真的：把开发库
（24 张表 / 4879 行，`alembic_version = 0012`）逐表拷进 `xlp_upgrade_test`，**逐表核对
行数相等**，然后

```
XLP_DATABASE_URL=mysql+pymysql://…/xlp_upgrade_test  alembic upgrade head
# → 0012 → 0013 → 0014，EXIT=0
```

两处拷贝上的细节各自都有原因：**`alembic_version` 那一行不能拷**（同
`mysqldump --ignore-table=….alembic_version`，拷了就是 `1062 Duplicate entry
'0012_drop_care_case_unique'`）；**不能改用「当前代码 + `seed`」造一份 V1.0 的库**——
当前代码的 `seed` 会往 V1.2 才有的列里写值（`school_id_snapshot` 一伙），造出来的
不是 V1.0 的库。这一点本身就是这条验证的意义：**只有真数据能回答「0013/0014 在它上面
过不过得去」**，而真数据只能来自开发库。

#### 回填是**按规则**填的，不是留空（唯一例外见缺口 9）

`assessment_session.school_id` / `assessment_target.school_id_snapshot` 是
`NOT NULL` 且**无 `server_default`**（DDL 里是先可空、回填、再 `MODIFY`）。所以
V1.0 的写入路径必须自己给值——落点是 `task_service` 发放目标行、`assessment_service`
开卷、`assessment_import_service` 与 `seed` / `seed_demo`。回填取的是**学生名册上
的学校**（`student.school_id`），不是「当前登录用户在哪所学校」。

`risk_event.signal_type` 走 `SIGNAL_TYPE_BY_RISK_TYPE`（`scale_engine/engine.py`）：
六档 `risk_type` 映到三档 `signal_type`，**认不出的当场报错、不猜默认值**——猜的代价
是静默的（那条待办会落进另一类人的队列，或者干脆不进任何人的队列，而界面上一切正常）。
0013 的 precheck 对认不出的 `risk_type` 同样是中止，两处口径一致。

`tested_at` **刻意不回填**：历史行没人知道真实测评日，用 `created_at` 填上是给它编一个
看起来像事实的值。所以 `tested_at_source` 的默认值 `PENDING_VERIFICATION` 是**诚实的
默认值**——而它现在是恒定的，见缺口 9。

#### 复合外键编码的不变量，以及为什么约束名照抄 MySQL

`(task_id, school_id_snapshot) → assessment_task (id, school_id)` 与
`(school_id_snapshot, student_id) → student (school_id, id)` **一起**说的是：
一个任务的目标行，必须是**这个任务自己那所学校**的学生。两条分开都拦不住跨校。

`student_ibfk_4` / `student_ibfk_5` / `class_group_ibfk_3` 这些名字是**故意**照抄
MySQL 自动命名的形状的，不是没起好名字：一份手写的 DDL 与一份迁移建出来的库要逐字
相同（§16 那次真机比对），而 MySQL 自动命名的序号在两边必须对得上。改成一个更好读的
名字会让两个库在**未来某条迁移上**分岔，而分岔得看不出来。这条有守卫：
`test_the_databases_foreign_key_names_are_the_models_ones`（`compare_metadata` 看不见
外键名字，所以它得单独一条）。

#### 快照文件：`sql/schema_mysql8.sql` 跟着长到 34 张表

它是**快照，不是来源**（§16），这一条没有变。V1.2 之后它有 34 张应用表，
并且多了一条守卫——`test_the_snapshot_file_builds_a_database_matching_the_models`
**真的执行它一遍**（`FOREIGN_KEY_CHECKS=1`），再拿 `compare_metadata` 比。
此前只有静态文本比对，而「表达式改了但它仍然长得像」那种漂移，静态比对是看不见的。

#### 开发库从那之后就是 V1.2 的（2026-09-19）

预演在同一份数据的**克隆**上通过之后，开发库本身也升了。库里的数据是种子 +
`seed_demo`（25 张表 / 4880 行），升级前后**逐表核过行数**：一张不少、一行不多。
三个 `NOT NULL` 新列全部回填到 0 个 NULL（`signal_type` 那 13 行全是
`MANUAL_REVIEW_REQUIRED`——它们本来就是人工复核那一类）。

**e2e 在这个升级后的库上重跑过：`113 passed (25.6s)`。** 这一条是**新信息**，不是重复
§20 里那次：那一次跑在 V1.0 的库上，这一次跑在**走过 0013/0014 的库**上，而 e2e 会真的
开卷、真的交卷、真的关档案——它第一次在 V1.2 的表结构上走了一遍完整闭环。前端一个字没改
（这一阶段不含前端改动），所以这两次都绿恰好说明「表结构变了、前端不受影响」。

### 22. V1.2 第 1 期：任务范围与目标快照（2026-09-19）

对齐阶段把表和列加齐了，紧接着的第 1 期给它们接上写入方与读者。
`make test` 542 → **561 passed**、`make e2e` 113 → **115 passed**。

#### 「打算发给谁」与「实际发给了谁」是两张表、两个问题

| 表 | 回答 | 谁写 |
|---|---|---|
| `assessment_task_scope` | 建这场任务时**按什么范围**发的 | `create_school_assessment_task` 写一行 |
| `assessment_target` | **实际发给了哪些人** | 同上，逐行；补发再加行 |

两者**本来就该不同**：发放之后名册上转进来一个学生，目标行会补、范围不会变。所以只留
目标行的话，「这场普查当初打算测谁」就永远没有答案了。今天 `scope_type` 恒为 `SCHOOL`
（这个端点还不收范围参数），将来能选年级/班级时换的只是 `scope_type` 与对应的 `*_id`，
**读的那一侧不吃惊**——`list_task_targets` 是照着有范围参数的样子写的。

**`get_targets` 的 `scope_type` 可以是 `null`，界面必须跟着分岔，不许 `?? 'SCHOOL'` 抹平。**
「没有范围行」就是**没有记录**，不回退到 `assessment_task.scope_type`：

- 外部导入的批次任务那一列写着 `SCHOOL`，而它**从来不是**「发给全校」——这一批是照着一份
  文件建的，文件里有谁就是谁。回退会把「不知道」变成一句言之凿凿的「全校」，而同一屏
  下方那几十行正是反例。
- 2026-09-19 之前建的校内任务**确实**是按全校发的，但它没有那一行记录。给它补一行是
  替历史编一个「当时选的是全校」的裁决——与 §21 那条「`tested_at` 刻意不回填」同一个道理。
  今天显示「未记录发放范围」恰好是实话。

#### 七个快照列：一处定义，四个写入方

`services/target_snapshot.py::target_snapshot(student, *, grade_name, class_name)` 是
**唯一的定义**（`school_id_snapshot` / `student_no_snapshot` / `student_name_snapshot` /
`grade_name_snapshot` / `class_name_snapshot` / `gender_snapshot` / `age_snapshot`），
四个写入方都走它：`create_school_assessment_task`（发放）、`supplement_targets`（补发）、
`assessment_import_service._mark_target_completed`（导入收行）、`db/seed.py`。
各写一份 `AssessmentTarget(...)` 就是四种「快照是什么」的定义，而它们漂移了**不会有任何
东西看得见**——直到有人真的按快照对账。写法照 `tests/factories.py`：只补齐快照列，
`status` / `completed_at` / `target_source` 仍归调用方。

三点容易改错：

- **`student_name_snapshot` 存 `student.name`，不是 `masked_name`。** 快照是身份事实，
  而 `masked_name` 只是一列叫这个名字的展示名（§1：它在生产里和 `name` 一样实）。
  遮蔽是**读**的时候的事，由读的那一侧现算。
- **`grade_name` / `class_name` 是可选的预先取好的值。** 批量发放一场全校普查是一千行，
  调用方已经 join 过 `grade` / `class_group`；再让这里逐行走 relationship 就是两千次
  额外的 SELECT。用 `is not None` 而不是真值判断：「调用方没取」与「取到空串」是两件事。
- **读的那一侧快照优先、名册兜底**（`list_task_targets`）。V1.2 之前发出去的目标行那六列
  是 NULL，所以兜底不是「两条口径打架」，是历史行没有快照。这一页回答的是「**发放那一刻**
  学校看到的是谁」——学生转班、改名、毕业之后，一份去年的完成率报表仍该按当时的名册解释
  （§1 的四层事实模型）。

#### 补发是两步，而且预览与落库是**两个查询**

`POST /assessment-tasks/{id}/targets/supplement`，体里带 `confirm`：

- `confirm=false` → 只回答「会补哪些人」，**一行都不写、不写审计**（没有改变任何东西）；
- `confirm=true` → 落行 + 路由写一行审计 `补发目标学生`（服务层不写审计，与全库一致）。

§20#9 要的正是这个形状（「补发**确认后**才新增目标行」），所以这不是界面上的体贴，
是一条验收判据——`e2e` 那条用例的**判据是「取消之后一行都没多」**：一个把预览做成隐式
副作用的实现在那里变红。

**确认那一支重新取一遍、不带 `limit`。** 预览那 200 条是给人看的样本（
`SUPPLEMENT_CANDIDATE_LIMIT`），要补的是**全部**候选：拿样本去插会把「另外 150 人」静默
丢掉，而屏幕上写着「将新增 350 人」。这是 §10「凡是截断都要自己说出来」的镜像——
**截断只许影响显示，绝不许影响写入**。

另外三条：

- `target_source` 记 `SUPPLEMENT`（§18.2）。一份完成率报表里，「这场普查本来该测的人」与
  「后来补进来的人」是两个数。
- **任务已结束（`effective_task_status` 说 `CLOSED`）就不补**，422 且指出出路（先编辑这场
  测评把截止日期改到将来）。理由：补出来的是一行**谁也点不开**的目标行（§12 那道门），
  那比不补更糟——它看起来像已经补好了。
- 发放目标行按**创建者的数据范围**过滤（§9 的写侧，2026-09-17 写权从 ADMIN 转到 COUNSELOR
  时加的）。写权还在管理员手上时「发放全体」与「发放创建者可见的全体」是同一件事，
  所以少了它也不出错；一落到心理老师身上就分岔了。

#### 目标学生名单：两道门槛，形状照抄 `ensure_student_result_reader`

读这一页要**任务读者 + `ORG_ACCOUNT: {MANAGE, READ_BASIC, READ_SUMMARY}`**。它逐行给出
学号与姓名——那是**组织与账号**那一档的数据，不是任务本身的数据；而它又是任务内部的，
所以要同时是任务读者。与 `GET /students/results`（§4）是同一个形状的镜像：那边是
「心理详情不得成为读名册的旁路」，这边是「任务读者不得成为读名册的旁路」。

admin 挡在**任务那一层**（`ensure_task_reader` 本来就不放行它，§4：测评任务不是能力、
是角色）。该能力在**两处**各查一次（`tasks.py` 的路由依赖 `TaskTargetReader` +
`task_service.ensure_task_target_reader`），所以单独拆掉任何一处都不会让测试变红——
两处一起拆才会。**别把「拆了一处仍然全绿」读成守卫失效。**

#### `TARGET_SOURCE_LABELS` 只有**第二面**保护

新增码 `TASK_SCOPE` / `SUPPLEMENT`（`labels.ts`）。四个面里：

| 面 | 有没有 |
|---|---|
| ①后端发的码 `labels.ts` 认不认 | 有（词条在） |
| ②视图有没有**调用**标签函数（`e2e/vocabulary.spec.ts` 扫像素） | **有，而且是唯一真正跑得到的那一面** |
| ③服务端生成的导出文件认不认 | **不适用**——完成明细 CSV **刻意不带** `target_source`：那一页只要身份列（§4 的双门槛），带上它就要把同一张响应里塞进等级列 |
| ④枚举列的排序 `Column.order` | **不适用**——目标学生那一屏是普通 `<table>`，没有可排序列 |

所以 `vocabulary.spec.ts` 那条**必须点进「目标学生」页签再扫一遍**：页签内容不点不开，
而这一列不翻译的话界面上就是 `TASK_SCOPE`。演示数据里每一行都是 `TASK_SCOPE`，
`SUPPLEMENT` 要等真的补发过一次才会有（与 `IMPORTED` 同理，见 §3）。
`TARGET_SOURCE_LABELS` 的键序是有意义的（先任务范围、后补发），但**这张表没有
`TARGET_SOURCE_ORDER`**：没有可排序列就没有读者，而「表在 `labels.ts` 里没人从那儿取」
也算没接上（§3 第四面那条教训）。哪天那一屏可排序了，照键序加回去就行。

#### 预览的空态与「将新增 N 名」必须互斥

这两句话此前共用一支模板，于是 `N=0` 时屏幕上写着「将新增 **0** 名学生」，紧接着下面
再跟一句「没有可补发的人」——**同一屏两句话各说各的**，而第一句读起来像一件真会发生的
事（§14：空态是一句关于数据的话，不是一次变更的占位）。现在按 `candidates.length` 分成
两支，且 e2e 两边都钉：`/将新增\s*0\s*名/` 的 `toHaveCount(0)`，以及
「『一个人都没有』与『确认按钮能不能按』是同一件事的两个说法」（两个方向各断言一次）。

**正则里那两条分支不能省成一条。** 第一版的守卫写成
`getByText(/将新增|没有可补发的人/).toBeVisible()`——`|` 让**恢复旧模板**也照样匹配到
「将新增」，于是变异验证是绿的。加一条「那个数必须是正的」才真正钉住**这两句互斥**，
与演示数据此刻有几个人无关。变异验证时它单独变红（`toHaveCount` 收到 `1`），
不是搭了前一条严格模式冲突的便车。

#### 弹层次序按**打开**算，不按 DOM 次序（`composables/modalStack.ts`）

第 1 期唯一一处共享组件的改动，但它修的是一个**会让整类弹窗不可用**的缺陷。

所有 `.modal-backdrop` 都写着 `z-index: 1000`（`styles.css`），同层里由 DOM 先后决定遮挡；
而 DOM 先后由 **Teleport 锚点的建立次序**决定——那等于**模板里的声明次序**，与打开次序
**无关**（弹层组件都是常挂着的，`v-if` 在其内部那层）。`TasksPage.vue` 里 `<FormDialog>`
声明在详情弹层**前面**，于是从详情弹层里点「补发学生」开出来的表单**永远压在它下面**：
视觉上被那层半透明遮罩压暗，点击则被遮罩接走——而遮罩的处理是「关掉详情弹层」。
用户点一次提交，得到的是详情弹层消失了、表单还浮在一片空页面上。

**这个 bug 是量出来的，不是推出来的**：修之前两层遮罩的 `getComputedStyle().zIndex` 都是
`1000`。所以次序改成按**打开**算——谁后打开谁在上面。

- **状态必须住在模块里**（`composables/modalStack.ts`，与 `useSettings.ts` 同为模块级单例）。
  第一版写在 `Modal.vue` 的 `<script setup>` 里，而那里每个绑定都是**每个组件实例各一份**
  的——两层弹层各自看到一叠只有自己的数组、序号都是 0，实测仍是两个 `1000`。
- **是一叠实例，不是一个递增的计数器**：计数器在「先开的先关」时会算错——关掉的那个把号
  让出来，新开的那一个反而排在那之前就开着的**下面**。每个弹层的号都是 `computed`、读的
  就是这叠数组，所以任何一层进出都会让**所有**弹层重算，不会有谁的号停留在过期值上。
- **只开一层时第一个拿到 1000**，与 `styles.css` 那条基础规则同值，所以**单弹窗的渲染结果
  与从前逐像素相同**——这一条是这个改动的安全边界，也是它能一次改全站的理由。
- `deactivate()` 里必须 `dropModal`（§15 那条「面板在打开状态下被卸载也要解锁」的同一处）：
  漏了的话那一层会**永久占着栈**，后面每开一个弹层都被顶高一层。

**收尾话术**：这是「DOM 次序不等于视觉次序」在 Teleport 上的第一次发作。同一个机制下
还有一处**没动**且是对的：全站其余页面都只开一层弹窗，所以那十几处不受影响。

### 23. V1.2 阶段 2：计算幂等与答卷哈希（2026-09-19）

对齐阶段加的三列（`answer_snapshot_hash` / `answer_hash_algorithm` / `calculation_status`）
与 0013 那条 `tested_at_source` 此前**都没有写入方**——`calculation_status` 恒 `PENDING`、
`tested_at_source` 恒 `PENDING_VERIFICATION`、哈希两列恒 NULL，「算出来了没有」在库里
没有任何东西能回答。这一期给它们接上写入方、读者，以及一个并发上的门。

#### 答卷哈希：一个纯函数模块，规范化规则写成逐字断言

`services/answer_snapshot.py` **不碰数据库**（与 `scale_engine/engine.py` 同一条：
引擎与规范化都不该知道库长什么样）。`canonical_answer_snapshot` 输出「一行一题、
四列一行」的字节串：`量表编码 ␟ 量表版本 ␟ 题号 ␟ 答案`，行间 `\n`，字段间 `\x1f`，
题号**升序**，`answer_snapshot_hash` 是它的 SHA-256。

四条不能动的：

- **必须按题号升序**。`save_answer` 是按学生点哪道题存哪道题的，而 `submit_session`
  从库里读回来时那个次序**由执行计划决定**——不排序的话，同一份答卷在两个进程里会
  算出两个哈希，而那个哈希的全部用途就是「比对两次是不是同一份」。
- **`\x1f` 与 `\n` 出现在答案里就抛，不替换**。替换会把两份不同的答卷映射到同一串
  字节——它不报错，只是让两条记录看起来是同一份。题号不是整数同样抛：字符串键下
  `sorted` 排的是字典序，第 10 题会跑到第 2 题前面。
- **算法名与哈希一起存**（`answer_hash_algorithm`）。换算法之后历史哈希无从解释，
  而那一列就是解释它的东西。当前唯一的值是 `SHA256_CANONICAL_V1`。
- **学生、会话、时间戳都不进哈希**（这是「这份答卷」的摘要，不是「这一次测评」的摘要）。

守卫是 `test_answer_snapshot.py`，其中 `test_the_snapshot_golden_value` 钉的是
**那一串字节与那个十六进制摘要本身**——别的用例（比如「两份不同答卷哈希不同」）
在规范化规则被改宽/改窄之后全都还是会绿，只有它能红。

#### 状态机与失败：**不伪造成功**，也不让一个评分 bug 吃掉学生的答卷

`score_session`（`assessment_service.py`）是唯一的落点，两条评分路径共用它
（学生交卷 / 外部记录导入）。三步 `PENDING → CALCULATING → CALCULATED`，
失败走 `CALCULATING → CALCULATION_FAILED`：

- **失败时返回 `False` 而不是抛出**，方向是刻意的：在线路径上那份答卷是学生花二十分钟
  做出来的事实，评分里的一个 bug 不该把它一起丢掉（抛出去 = 路由的 commit 不会发生 =
  答卷回滚 = 学生重做一遍）。所以 `submit_session` 照旧 200，只是 `result` 是 null，
  而 `calculation_status` 说为什么。失败原因进 `calculation_error`（截断到 1000 字），
  **只放「为什么算不出来」，不放答卷内容**（§8 那条的另一处）。
- **「前提不成立」不在此列**：`ANSWERS_INCOMPLETE` / `ANSWER_INVALID`
  （`CALCULATION_NOT_READY_CODES`）照原样抛出，并把状态**退回原值**。它们说的是
  「这份答卷还不该算」，不是「这一版程序算不了」——那是学生页要弹 422 的那一条。
  **其余任何一种失败都记账**，包括另一个 `AppError`（`SCALE_INVALID`：规则版本要 100
  道题而题库里只有 60）与所有认不出的异常。分界线是「这句话说给谁听」：`NOT_READY`
  那两条是说给**学生**的（你没答完），别的都是说给**下一个人**的——所以后者落进
  `calculation_error`，而不是弹回浏览器。导入路径是例外的例外：那条要求整批要么都进、
  要么都不进，调用方自己看返回值决定。
- **`CALCULATING` 不是一次认领**：三步写在同一个事务里，并发的第二个请求根本读不到
  它（它在锁上等着），进程崩溃时它跟着事务回滚。真正保证「同一场只算一次」的是行锁。
  写成一次提交过的认领要连**租约与回收**一起写进来，这个项目没有那一层。

#### 并发互斥选了 `SELECT … FOR UPDATE`，理由写在这里

`lock_session(db, session_id)` 是 `with_for_update()`。**没有选 CAS**：CAS 要一个
版本列，而这个项目当时没有任何乐观锁设施（`student_care_case.case_version` 是阶段 7
才接上的，见缺口 9），为一场测评单独造一个版本列会让「同一件事两种并发写法」在库里
并存。行锁是 MySQL 已经有的东西，代价是锁住一行、收益是**不需要第二套设施**。

两个入口共用它：`submit_session`（学生交卷）与 `retry_calculation`（心理老师点重算）。
次序是「先上锁，再判有没有算过」——判断与写入之间那一段是临界区，而这两个入口可以
同时到达（老师正在点重算，学生的提交刚好到）。

#### `tested_at` 与 `submitted_at` 今天总是一样，而趋势按后者排

两条写入路径都让它们相等：在线交卷写 `tested_at = submitted_at = 交卷那一刻`
（`tested_at_source = ONLINE_SUBMIT`），导入写 `tested_at = submitted_at = 文件里的测评日期`
（`tested_at_source = IMPORT_FILE`）。**刻意没有把排序键改成
`coalesce(tested_at, submitted_at)`**：那在今天是**行为完全相同**的一次改动（历史行的
`tested_at` 是 NULL，兜底回落到 `submitted_at`），换不来任何东西。所以不变量的位置是
两处测试而不是一行代码：在线路径由
`test_calculation_idempotency.py::test_an_online_sitting_says_where_its_test_date_came_from`
钉住，导入路径由 `test_assessment_import_api.py` 逐场断言。

**哪天真有一条路径写出 `tested_at ≠ submitted_at`，那一天要一起把排序键挪过去**
（`latest_session_order` / `session_history_order`），而
`test_the_trend_lists_sittings_by_real_test_date_not_by_when_they_arrived` 会先把这件事
喊出来：它把三场按「真实测评日」与「id 大小」故意排成相反的顺序，谁把排序换成
`created_at` / `id` 就红。

#### 重算是心理老师的动作，每一次都写审计

`POST /assessment-sessions/{id}/calculate`（`require_role(COUNSELOR)`）。**归心理老师、
不归管理员**——与 §4 那条「测评任务不是一个能力，是角色」同源：测评这条线是学校业务，
写侧归业务负责人；管理员也没有个案详情页可去。

- 它是一次**敏感读取**（重算要读满整份答卷，重点题也在里面），所以审计写在返回数据
  之前，并带上 `student_id`——那一列才让这一次重算出现在这名学生的敏感访问记录里。
- **`recalculated: false` 不是失败**，它说的是「已经有结果了，这一次什么都没写」
  （两个人同时点，或者页面停在旧状态）。审计的 `detail` 把这两种情况分开写：
  「重算完成，已写入结果」与「未重算：会话仍是 X」——`action` 一模一样
  （「**重算测评评分**」，`resource_type` = `ASSESSMENT_SESSION`），只有 `detail`
  答得上「这一次到底动了什么」。
- **按钮上的词与审计动作码共用「重算」这两个字**（按钮写「重算评分」，审计写
  「重算测评评分」）。这不是巧合，是缺口 7 那一族的第四次位置——审计页的搜索匹配
  `action`，而操作员能想起来的是他刚点的那个按钮。第一版按钮写的是「重试计算」，
  两串字没有一个共同的词，照着按钮搜一条都搜不到。**码还是新的、还没有历史落库时
  对齐它是最便宜的**；等落了库就只剩 §3「高度关注」那条出路（两边一字不改，在用户
  要动手的那一处写出等号）。
- 没交过卷的场次给 422（「这份答卷还没有交卷，没有可重算的评分」），并且**不留下失败
  记录**：那不是一次故障。
- 被数据范围拒绝时**不写审计**（§9）。

#### 0015 只回填 `calculation_status`，另两样**刻意留空**

`0015_calc_status_backfill` 一条 UPDATE：有 `assessment_result` 的场次标成
`CALCULATED`（这是一个**能从既有事实推出来的**派生事实——结果那一行就在那里）。
另外两样不回填，各有各的理由，都写在迁移的 docstring 里：

- **哈希不回填**：我们不知道历史上算过的那份答卷是不是今天库里这一份
  （`/reset` 会清答案，学生可以重答）。补一个哈希上去，等于替那行结果**担保**它算的
  是当前这份答卷——而那正是这一列唯一要说的话。
- **`tested_at` / `tested_at_source` 不回填**：与 §21 那条同一条道理，历史行没人知道
  真实测评日。`PENDING_VERIFICATION` 是**诚实的默认值**，界面上照实显示「待核实」。
  （缺口 9 里「两个诚实的默认值现在是恒定的」那一条仍然成立，只是现在多了一条写入口。）

downgrade 的判据是 `answer_snapshot_hash IS NULL`：新代码**每一次成功计算都写哈希**，
所以有哈希的行一定是它算过的，没有的才是它回填的。

#### 前端：四张表、两个渲染点，重试按钮**没有 e2e 覆盖**

`labels.ts` 加了 `CALCULATION_STATUS_LABELS`（待计算 / 计算中 / 已计算 / 计算失败）
与 `TESTED_AT_SOURCE_LABELS`（学生交卷时间 / 导入文件的测评日期 / 待核实）＋
`calculationStatusLabel` / `calculationStatusTone` / `testedAtSourceLabel`。
两张表都**没有 `*_ORDER`**：读者是描述性的一行字，不是 `DataTable` 的可排序列
（§3 第四面；`TARGET_SOURCE_LABELS` 那条「表在 `labels.ts` 里而没人从那儿取也算没接上」
是同一件事）。

两个渲染点**都不条件渲染**，这是为了第二面能真的扫到东西：

| 渲染点 | 位置 | 演示数据里扫得到吗 |
|---|---|---|
| 「评分状态」行 | `CareCaseDetailPage.vue` 测评概览卡 | 是（每一场都是 `CALCULATED`） |
| 「日期来源」行 | 同上 | 是（`ONLINE_SUBMIT` / `PENDING_VERIFICATION` 都有行） |
| 「处理状态」行 | `StudentHistoryPage.vue` 每一张记录卡 | 是 |

`UNTRANSLATED_CODES` 随之加上那六个码。**变异验证 3/3**：把任一处换成裸字段渲染，
`e2e/vocabulary.spec.ts` 的对应角色用例变红——这同时证明了那几行**真的被扫到了**
（扫不到的空区域在变异下永远是绿的）。

顺带动的两处（都不是新功能）：

- **「提交时间」改名「测评日期」**。对外部导入的那一场，原来的标签是错的：那一场没有人
  在本系统里提交过任何东西，`submitted_at` 存的是文件里的测评日期，而同一张卡上面就有
  一行「来源：外部导入」。历史行（`tested_at` 为 NULL）按 `submitted_at` 兜底。
- **`StudentHistoryPage.vue` 那份自己的 `statusLabel` 删掉**，改用 `targetStatusLabel`。
  那是 §3 第一面的反面同一个形状：一张表两个定义（`ScalePage.vue` 那次），改中文时
  改一处、漏一处，而漏的那处界面上照旧显示旧词。

**学生的记录页现在下发 `calculation_status`（分数仍然不下发）。** 这不是放松 §「学生端
只给完成状态」那条：这一列不是分数，它是**「已完成」与「已算出来」的区别**——一名交完卷
的学生看到「已完成」而这一场因为一次计算故障根本没算出来，他会以为一切都好。交卷那一刻
的 toast 同样分成两句（答卷提交成功 / 成绩处理还需要老师再看一下），并写明**他不需要
重新作答**——**失败原因不下发给学生**：那是一段给维护者看的文本。

**「重算评分」按钮没有 e2e 用例，这是有意的取舍，不是忘了。** 要让它出现在屏幕上，得先
让一场测评**真的算不出来**，而这件事在浏览器里做不到：唯一现实的失败注入是
`monkeypatch`（`rule_config_from_json` 读不认识的规则时会静默回落默认值，不会抛）。
后端那一侧是钉住的（`test_a_failed_calculation_is_recorded_and_leaves_the_answers_alone`
与 `test_a_failed_calculation_can_be_retried_by_the_counselor`），漏的是「视图有没有把
那个按钮渲染出来」——与 `IMPORT_CONFLICT_LABELS` 那次（§3）是同一类取舍。

#### 跑数

`make test` 561 → **590 passed / 0 failed / 271.18s**（新增 `test_answer_snapshot.py` 与
`test_calculation_idempotency.py`，另有 §3 那两条词表用例）；`make e2e` 115 passed
（全量两遍）。开发库与 e2e 库都已升到 0015。

**顺带记一个不属于本期、也没有修的告警——已经查清是误报，不要再花时间**：`make test` 的
5 条 warning 里那条 `analytics_service.py:523`（`GET /students/results` 的查询）说
「cartesian product between FROM element(s) … and FROM element …」。**它报错了**，证据是
它自己编译出来的 SQL：

```sql
FROM student INNER JOIN grade ON grade.id = student.grade_id
INNER JOIN class_group ON class_group.id = student.class_id
LEFT OUTER JOIN assessment_session ON assessment_session.id = (SELECT … WHERE …student_id = student.id …)
LEFT OUTER JOIN assessment_result  ON assessment_result.session_id = assessment_session.id
LEFT OUTER JOIN student_care_case  ON student_care_case.id = (SELECT … WHERE …student_id = student.id …)
```

三个外连接**各有各的 ON**，两个标量子查询都 `correlate(Student)` 到了外层的 `student.id`。
所以 `:521-522` 那条「不会乘行、不需要去重」的注释是对的，别去改它。

三点值得记，免得下一个人重查一遍：

- **触发它的是 ORM 那条编译路径，不是编译本身。** 同一个 `select(...)` 直接
  `Select.compile(dialect=mysql.dialect())` 一条告警都没有；是 `Session.execute()` 那层的
  ORM 编译状态才报的。所以「编出来看看」这个最直觉的复现办法会**得出相反的结论**。
- **同一条语句，两次运行报的 FROM 分组不一样**（一次是 `assessment_result` /
  `student_care_case` / `assessment_session` 对 `student`，另一次是 `grade` / `student` /
  `class_group` / `student_care_case` 对 `assessment_session`）——那个分组是拿集合拼的，
  而字符串哈希每次运行不同。**别把两次不同的措辞读成两条不同的告警**。
- 真正的判据是**行数**：这张表带着「列表条数与名册行数相等」的断言，而它一直是绿的。
  一条真的笛卡尔积在那里是过不去的。

**2026-09-20 补：同一个形状有第二处，`export_service.py:117`（档案导出那条查询）。**
它是本次跑数时才第一次被记下来的（那两行 `analytics_service` 的那一处行号也随之漂到
`:533`——阶段 4～8 往那个模块里加过东西，**行号不是判据，形状是**）。判据照上面那一套原样成立：那条语句的五个
FROM 元素**各有各的 ON**（三个 `join` 加两个 `outerjoin`），而 `latest_session_id` 是
`correlate(Student)` 的标量子查询，`outerjoin(AssessmentSession, AssessmentSession.id ==
latest_session_id)` 的 ON 里出现的是一个子查询——这正是 SQLAlchemy 那个启发式读不懂的形状。
**别去改它**，理由同上。

这一处还多一层「报错了也看不出来」：这条路径逐**学生**写行、`seen_students` 按人去重
（§11），所以即使真有乘积，写出来的文件**仍然是每人一行**——代价是白扫的行数，不是错的
数据。也就是说这一处的行数判据比上一条更弱（文件看不出来），而它的形状与上一条逐字相同，
所以仍然按误报处理。

### 24. V1.2 阶段 3：名册导入批次化（2026-09-19）

对齐阶段给名册导入留了「批次 + 逐行明细」两张表，这一期给它们接上写入方与读者。
`make test` 590 → **600 passed**，`make e2e` 115 → **116 passed**。

#### 这一期真正的改动是「预览从**只读**变成了**写**」

V1.0 的名册导入是「预览校验 → 返回一份 `preview_token` → 提交时把 token 连文件一起带回来」。
那时预览**一行都不写库**，所以那个路由从来没有 `db.commit()` 也没有任何问题。这一期把它
改成落一个 `PREVIEW` 批次加逐行明细，**于是同一个路由从只读变成了写**——而那一行
`db.commit()` 一开始漏了。

漏掉的后果不是「预览看不到」，是整条链路断在第一跳：客户端拿着 `batch_id` 去提交，
`commit_roster_import` 按 id 查库，那里什么都没有，回一句 404「导入批次不存在」——
而屏幕上刚才还写着「共 1 条，可导入 1 条」。**它只在 e2e 上看得见**（真服务、真连接）。

所以那一处的注释不是解释代码，是在挡下一次「这个路由以前不需要 commit」的推理：
**路由从只读变成写的那一刻，`db.commit()` 是这次改动的一部分**，不是顺手加的。

#### ★ 后端套件看不见「路由写了但没提交」这一类洞

这是这一期最值钱的一条，因为它是**整个夹具的盲点**，不是某一条用例的疏忽：

| 夹具 | 性质 | 后果 |
|---|---|---|
| `conftest.py` 的 `override_get_db` | `yield db_session`——**从不关闭也不回滚** | 请求结束时没提交的行照样留在会话里 |
| `db_session` | `join_transaction_mode="create_savepoint"`，外层事务到**用例收尾**才回滚 | 未提交的写入对同一个用例里的后续查询**完全可见** |

两条叠起来：**「路由写了但没提交」在生产上是 500 / 404，在测试里是全绿**。
这不是假设——`test_a_preview_leaves_a_batch_and_its_rows_behind`（一个断言「批次行与逐行
明细已经成型」的用例）在整个后端套件里一直是绿的，而真库里那个批次 0 行。

唯一的网眼是 `test_student_import_api.py::test_the_preview_survives_the_request_that_made_it`：
它**换掉那一层覆盖**，用 `Session(seeded_engine)`（用完就关，与生产同形）直接调路由函数，
出了 `with` 再另开一个会话去查。走路由函数本身而不是 `TestClient`，是因为要断言的就是那个
函数体里有没有提交，而绕过的是依赖注入那一层（那里没有逻辑）——换来的是不必为了拿一个 token
往这个共享库里真写一行登录会话。**它用完把自己造的批次删干净**：这个库是 session 级共享的，
留一批会让别的用例看到一份它没造过的导入历史。

那条老用例的 docstring 也据此改了，明写「**它证明不了这批真的落了库**」并指向新的这一条——
一条断言写得像在证明完整性、实际只证明了内存状态的用例，比没有它更糟。

#### 提交带的是**批次 id**，不是一份凭据

**`preview_token` 这条机制整个去掉了。** 从前它是「预览时算一份凭据、提交时带回来」，
所以预览必须无状态；现在批次行本身就是那份凭据，而且比它多得多：文件指纹、操作者、
五个计数、逐行结论，全在同一页上可查。带 id 回来还顺手消掉了一个旧机制答不上的问题——
「刚才屏幕上那份预览与我手上这份文件是不是同一份」。

- **重用而不是重建**：同一个操作者、同一个文件指纹（`file_sha256`）、仍是 `PREVIEW` 的
  那一批会被复用（`start_roster_import`）。同一份文件传两遍是**操作员刷新了一次**，
  不是两次导入——建两批会让「最近导入」那一列出现两条一模一样的记录。
- **提交只认 `PREVIEW`**：已提交过的那一批不再是预览，再提交一次是 422 而不是把一份
  已经改过名册的文件再改一遍。
- **`RESOLUTIONS` 与 `student_resolution_label` 从 `assessment_import_service` import 来**，
  不在这个模块里再写一份字面量：两套字面量会长出第三种写法，而它只在前端提交时变成
  一句 422。`student_resolution_label` 是那个模块里已有的中文映射，审计的 `detail` 用它
  （`resolution=None` → 「未涉及（无冲突）」而不是留空——**留空与「没问过」分不开**）。

#### 旧路由整批退役，不留并存期

`students.py` 里那三个 `/students/import/*`（preview / commit / template）与它们的
`OrgAccountManager` 别名一起删掉了，**同一次改动**里删：留一个并存期就要回答「两个端点
的口径为什么不一样」，而那个问题的唯一答案总是「因为其中一个还没删」。

- 前两个被新端点替换，前端 `OrganizationPage.vue` 同批切过去；
- **`GET /students/import/template` 是直接删掉、没有替代物的**——零调用者、零测试，
  前端自己有一份模板的副本（那个函数的 docstring 记录着「两边要保持一致」这个有意决策）。
  也就是说它从写下那天起就没有人取过，而「模板」这个功能一直在（在前端）。
  这是缺口 7 那一族在**后端**的第一次：一个没有任何读者的端点，改它不会红、删它也不会红。

#### 批次不属于这所学校时回 404，不回 403

三个入口各查一次（`start_roster_import` / `commit_roster_import` / `list_roster_import_rows`），
判据与「批次不存在」**共用同一句话**（`导入批次不存在`）。理由与 §9 那条同源：
`batch_id` 是客户端传来的，它本身就是一份越权凭据——若「存在但不归你」回 403、
「不存在」回 404，那么 403 就成了一句「这个 id 存在」的确认，把另一所学校的导入批次号
变成了可枚举的事实。**「不属于你」与「不存在」在响应上必须不可分辨。**

#### 学生首页：`target_status` 与 `status` 是两个问题

这一期顺手修的产品缺陷（不是新功能）：`StudentHomePage.vue` 此前拿 `target_status`
（**这名学生在这场比赛里做完没有**）当唯一的门禁，于是任务列表按 id 倒序的第一张是
演示数据里那场「还没开始」的复测（`seed_demo` 按 today+20 算窗口 → `NOT_STARTED`），
它长着一个点得动的「开始作答」——而后端 `create_or_get_session` 判的是
`effective_task_status != "ACTIVE"` → 404（§12）。

**这正是 §12 那句「界面说已结束时那个端点就真的开不了」的同一句话的另一面**：后端已经
在 `list_student_tasks` 里发着现算出来的 `status` 了（那一行的注释甚至写着「学生端现在不
显示这一项」），只是**没有读者**。修法是只认 `ACTIVE`（`canAnswer`，与后端那道门逐字对齐，
不另写一套「什么算开着」），文案取 `labels.ts` 的 `taskStatusLabel`，不在视图里另写中文。

`TASK_STATUS_LABELS` 于是有了第二个读者——`TasksPage.vue` 此前**自己抄了一份**（本地
`statusLabel` + 就地 `labelOf`），随这次一起改用 `labels.ts` 的导出函数。这是 §3 那条
「表在 `labels.ts` 里而没人从那儿取也算没接上」的第**三**次发作（前两次：`ScalePage.vue`
的 `STATUS_LABELS`、`StudentHistoryPage.vue` 的 `statusLabel`）。

**e2e 的定位器必须跟着改，而且这一条本身值得记**：`app.spec.ts` 里 9 处
`.task-card').first()` 都换成了 `startableTask(page)`，它按
`button:not([disabled])` 定位。旧写法之所以坏，是因为它假设「第一张卡就是能点的那张」——
一个**数据依赖的假设**，与「断言 28 名学生」是同一类（测试注意里那条）。按「可点」定位
才是那些用例真正要说的事（它们断的是「学生能开始答题」）。

#### `STUDENT`：一条零覆盖的清单项换来的竞态假阳性

`vocabulary.spec.ts` 的 `UNTRANSLATED_CODES` 里移除了 `STUDENT`，**不是放松**，是那条
清单项在换一个不稳定的测试：

- 它**没有渲染点**——「对象范围」列今天恒为 `SCHOOL`（§22），`STUDENT` 根本不出现在界面上，
  所以它一条东西都守不住；
- 它唯一的真实渲染点是审计页**有意**不翻译的 `resource_type` 列（缺口 7），而那一列就是
  等着与「服务端筛选器 + 提示语」一起改的；
- 而它**会让测试无故变红**：审计页只显示最新 20 行，`app.spec.ts` 的学生导入用例确实写进
  `resource_type=STUDENT` 的审计，两个文件通过 `fullyParallel` **并发**运行——于是
  「词汇用例扫到那 20 行时，导入用例有没有已经把它写进去」决定了它红不红。2026-09-19 实测
  红了一次，查审计表确认那一行 `导入学生` 就写在那次运行的时间窗里，**与代码无关**。

**一条会无故变红的守卫很快会被人关掉**（§18 那条），所以留一条零覆盖的清单项在这里是
纯粹的负收益。要恢复它，得先把缺口 7 那三件事做完（那时 `resource_type` 有中文映射，
`STUDENT` 不再裸着）——而在那之前，它在清单里守不住任何东西。

#### 缺口 7 的连带更新

缺口 7 那条「审计页的 `resource_type` 是裸编码」现在有了**唯一的已知活体**：全站的裸枚举
渲染只剩那一处（`DataCenterPage.vue` 的「近期数据任务」2026-09-16 起不再打印它）。
而它的**成因今天更清楚了**——除了「改中文会让搜索失灵」之外，它还会与 `fullyParallel`
下的其它 spec 抢同一条时间线，所以它是「不能顺手改」与「改了会踩到别的用例」两件事叠在
一起的一处。

### 25. V1.2 第 4 期：MHT 测评记录导入批次化（2026-09-19）

阶段 3 把**名册导入**从「预览 + 令牌」改成了「批次 + 逐行明细」；这一期把**测评记录导入**
（MHT 外部文件）走同一条路：两段式（预览 → 提交）变成三段式
（建批次 `PREVIEW` → 逐行匹配落 `assessment_import_row` → 逐行处置 → `commit`）。

`make test` 600 → **608 passed**，`make e2e` 116 → **117 passed**。

两者的形状现在一致，但**不是同一条链路的两次实现**——名册那一侧匹配的是「这一行说的是
哪个学生」，这一侧匹配的是「这一行是哪一场测评」，所以下面记的是这一侧独有的东西。

#### 预览从「一个令牌」变成「一批落库的事实」（与阶段 3 同源，代价不同）

V1.0 的测评导入预览把结论签进 `preview_token`（一个 JWT），**库对这批数据一无所知**。
两个问题因此没有答案：

- **这一批里的 W 行当时为什么匹配不上？** 令牌是一次性的，没人再打开它；
- **补完名册之后能不能重来一遍？** 不能。令牌是死的，重新上传要么改文件、要么再跑一遍
  并且看不出差别。

§18.4 那条出路——「找不到学生时应先补充名册，**再重新匹配**」——在后一种形状下**写不出来**。
现在预览就是写：批次行与逐行明细都落库，`match_status` 是这一行**此刻**的结论；
补完名册之后重传同一个文件，`_reusable_batch` 复用这个批次，逐行结论整批重算。

#### 「没有一条『只重跑匹配、不重传文件』的路径，也不该有」

这是这一期**唯一一处「看起来该做而刻意没做」**，所以它写在函数 docstring 里而不是
留在缺口清单里：

> 匹配的输入不止是名册：答案（100 题）与用时**只在文件里**，
> `assessment_external_result` 只给**匹配成功的那些行**存了它们，而匹配结论本身要由
> 答案之外的那五列决定。

所以**不做 `POST /assessment-imports/{batch_id}/preview` 这种「就地重跑」**。重跑必然缺答案，
而缺答案的行一旦提交就是**一份空答卷**——它在库里与真实作答长得一模一样，事后没有任何
办法分辨。重传文件是这条路上唯一能把「名册 + 答案」两样一起带回来的动作。

#### `_reusable_batch` 的四条判据，少一条都有话说

| 判据 | 少了它会怎样 |
|---|---|
| `imported_by` | 别人传的同一份文件被我的重传覆盖掉——那是两个人的两次工作 |
| `file_sha256` | 改过一个字就不算同一个文件 |
| `status == PREVIEW` | **已提交的批次绝不复用**：重传时删了重建等于把一批已生效的事实从记录里抹掉（会话还在，但「它是从哪一行来的」这条线索没了） |
| `task_id` | 同一份文件「先不绑任务传一次、再绑着任务传一次」是**两次口径不同**的导入（一层按自然月、一层按任务内有效结果），复用会把第一批的匹配结论按新口径改掉，而操作员看不出这件事发生过 |

第四条是这一期新加的：阶段 3 的名册批次没有「任务」这个维度可比。

**e2e 靠它做到幂等**：`vocabulary.spec.ts` 与 `app.spec.ts` 各传一份**内容固定**的文件，
每跑一次复用同一批（实测跑完全量 e2e 后批次表只有 3 行，其中两行是 e2e 的、id 不变）。
这是「跑 e2e 不许改掉操作员自己配的东西」的第三态：**新增、不改、不随时间增长**。

#### 预览这一步不建任务、不写任何测评记录

`start_assessment_import` 的 docstring 第一句就是「**不写任何测评记录**」：这一步不产生
任何测评会话、答卷、结果或风险事件，「预览」这两个字在界面上仍然是真的。任务在
`commit_batch` 时按自然月建或复用（`_task_for_month` 取**最新**那一批，与
`existing_import_session` 取最新会话对齐——缺口 8 那条）。

#### 九个 `match_status`，三档，判据只有一句

| 档 | 码 | 选「覆盖」时这一行会写进去吗 |
|---|---|---|
| 能进 | `MATCHED` | 不需要任何决定，直接写 |
| 要拍板（`MATCH_STATUSES_NEEDING_RESOLUTION`） | `AGE_CONFLICT` / `CONFLICT` / `DUPLICATE` | 选了才写 |
| 进不去（`MATCH_STATUSES_UNIMPORTABLE`） | `INVALID_ROW` / `NOT_FOUND` / `OUT_OF_SCOPE` / `AMBIGUOUS` | **任何选择都救不回来** |

后两档在界面上一行一个字写着，而且**各自对应一种操作员的动作**（选覆盖/放弃 → 去补名册
或补发目标），所以它们的边界不能混：把一档从左边挪到右边，屏幕上那两句话就会各自承诺
一件做不到的事。

**`AMBIGUOUS` 曾经在中间那一组，第 4 期把它挪到右边**（`MATCH_STATUSES_UNIMPORTABLE`
上面那段注释就是为这件事写的）。理由两件，都会真的发生：

1. `commit_batch` 的「覆盖」分支会拿 `row.student_id` 去 `db.get(Student, …)`，
   而它**必是 NULL**（连是哪一个人都还没定下来）→ `AttributeError` → 500；
2. 就算侥幸不崩，那句 422「请选择覆盖或放弃这些记录后重试」也在**承诺一件做不到的事**
   ——两个选择都不会把它写进去。

§18.4 给它的处置是「**人工选择**后是」，而逐行选择（在几个候选里指出是哪一个人）
是下一期的事。挪过去之后 `message` 里写的是学校此刻真能做的那件事（核对名册上这几名
学生的性别与年龄）。**逐行选择接上之后它再回到中间那一档**——那时 `commit_batch` 有
地方接住它的 `student_id` 了。

`DUPLICATE` **留在**中间那一档：§18.4 表头那句「否」说的是「**不选任何处置时**不会写进去」，
而选了覆盖（重写本月那一场）就会。这与 `AMBIGUOUS` 的区别是「覆写这个动作能不能做」，
不是「这一行有没有冲突」。

#### ★ 三个数各有各的口径，界面上不许互相顶替

| 数 | 出处 | 数的是什么 |
|---|---|---|
| `total_rows` / `row_total` | `assessment_import_batch.total_rows` | 上传那一刻读了文件里的**多少行**（写死的时态） |
| `row_counts` | `batch_row_counts`，**现算** | 现在这一批的行**按匹配结论**分成 `ready` / `needing_resolution` / `error` 三个数（第 6 期多了一个 `conflict`，它是 `needing_resolution` 的**子集**，不是第四档，见 §27） |
| 列表里的行数 | `list_import_rows`，**按读者数据范围过滤**、默认 `limit=200` | 你**看得见**几行 |

三处刻意的不一致，每一处都有理由：

- **`batch_row_counts` 数整批、不套读者的数据范围**（与 `list_import_rows` 刻意相反）。
  这三个数坐在**提交按钮旁边**，而 `commit_batch` 拒绝提交时用的是同一个集合、整批地数
  （「有 3 条记录需要确认」）。套上范围会出现最坏的那种对话——屏幕上写着「待确认 0 条」，
  点下去回一句「有 3 条记录需要确认」，**而操作员照着屏幕找不出那 3 条在哪**（§11：
  指标卡上的数必须与它点进去的那个列表同源）。代价是「本批 N 行」与列表行数可能不等，
  所以界面把两个数分开写、各自标明口径（§9：范围数字要写明口径）。
- **`batch_row_counts` 不读批次上那几列计数**（`created_rows` / `updated_rows` / …）：
  那些说的是**别的时态**。这一页问的是「现在这一批的行是什么状态」，而行的状态刚刚
  可能被改过——操作员在页面上逐行处置之后，那几个数必须跟着动。
- **两个读者共用一个集合**：`_match_batch_rows` 拿它写 `batch.error_rows`，
  `batch_row_counts` 拿它现算预览页上那个「有问题 N 行」。写成两处字面量时，屏幕上那
  两个数会在某次改动之后各说各话，而**两边看起来都对**。

#### `list_import_rows` 删掉了「把候选非空的行藏起来」

那条过滤第 4 期删掉了。它想挡的是**枚举**，实际造出的是**更强的枚举**：
行在 = 那个班没这个人，行不在 = 有这个人——一次上传问遍全校。藏起来保不住任何东西
（那些行里没有一条数据来自名册：`raw_*` 是操作员自己填进文件里的字，`matched_*` 是空），
而「没有谁消失」才是那条款成立的前提。

顺带记两个这一条实现上的坑：

- **`Student` 必须显式外连接进来。** 范围谓词长在 `Student` 的列上，而这条查询的主表是
  `assessment_import_row`——把谓词直接塞进 `WHERE` 而不给 `Student` 一个连接条件时，
  SQLAlchemy 会把它当作一个**独立的 FROM 元素**加进去，于是变成 `assessment_import_row`
  与 `student` 的**笛卡尔积**（213 行 × 名册里符合条件的每一个人）。行数被乘出来、
  逐行明细整片重复，而**报出来的只是 SQLAlchemy 的一条 SAWarning**——
  屏幕上「共 400 行」看起来完全正常。
- **`student_id.is_(None)` 那一支刻意不依赖 `candidate_student_ids` 的存储形态**：
  `JSON` 列的 `None` 有几个地方能悄悄变样（见下一条），而这一支的读者正是「出错的那几行」。
  判据只挂在 `student_id` 这个真正的标量列上，就没有这一层。

#### 0017 的 JSON null：一个让明细整片消失的默认值

`sqlalchemy.JSON` 的 `none_as_null=False`（默认）把 Python `None` 绑成 **JSON 字面量
`null`**（四字节），不是 SQL `NULL`。于是 `candidate_student_ids IS NULL` **恒假**
——明细静默漏掉没匹配上的行，而**操作员最需要看见的恰恰是那些**。

`0017_json_null_normalize` 是一条**数据**变更（不是 schema 变更），只改三列：
`assessment_import_row.candidate_student_ids`、`assessment_external_result.dimension_scores_json`
/ `result_payload_json`。判据是 `JSON_TYPE(col) = 'NULL'`。

两处**必须不动**：

- `audit_log.detail_json` 今天没有读者也没有写入方——不动；
- **`system_setting.value_json` 反过来依赖 JSON null 的语义**（`nullable=False` 却允许
  存 `None`），不许跟着改。

#### 列级错误改成 422：整份文件没读懂时不建批次

`_batch_errors` 管的是「这一批该不该建」，三条：批次名非空、**≤ `MAX_BATCH_NAME_LENGTH`**、
`tested_on` 不晚于今天。第二条的理由值得记：`AssessmentTask.name` 是 **String(128)**，
MySQL 严格模式下超长**直接 500**，而 500 里那句 `Data too long for column 'name'`
离「批次名称」隔着一层——操作员手上唯一的信息是一句关于列的英文。

全局错误**在写任何一行之前抛**（header 错误 + 上面三条 + 「没有已发布的 MHT 量表版本」）：
半个批次比没有批次更难收拾——操作员会看到一批匹配好的行挂在「导入失败」的那一次尝试上，
而再传一次同一个文件又会**复用这个批次**（同指纹、同操作者、仍是 `PREVIEW`），
于是他上次改的那几行没了却看不出为什么。

#### `batch_name` 是新加的一列（0016），它不叫 `file_name`

`0016_import_batch_name` 给 `assessment_import_batch` 加 `batch_name String(128) NOT NULL
server_default=""`。**两个名字不能合一**：学校的文件叫 `结果(3).csv` 是常态，而批次名会
成为那场批次任务的名字给全校看。

`server_default=""` 是给升级用的，**不拿 `file_name` 去填**——那等于替历史批次编一个
「当时它是这么命名的」的裁决（与 §21 那条「`tested_at` 刻意不回填」同一个道理）。
downgrade 直接删列。

#### 行负载以 `message` 为准，不是一串 `errors`

逐行明细里那一格是服务端拼好的**一句** `message`（`_row_message`）：
`errors + message + warnings` 用 `；` 连接、`[:1000]` 截断。它在界面上是一个单元格，
读起来是一句「这一行为什么进不去」的话。

配套的一条：`NOT_FOUND` 那三处（学校 / 班级 / 学生）**上一级的说法全部删掉**——
「名册中没有『初一 704』这个班级」已经说清了，再加一句「找不到学生」只会让操作员
去查人而不是去查班。

#### ★ 夹具盲点：后端套件看不见「路由写了但没提交」（阶段 3 那条的另一处）

这一条在阶段 3 已经有过一次（`start_roster_import` 漏了 `db.commit()`），这一期
`POST /assessment-imports/preview` 同样是自己 `db.commit()`（路由层，第 116 行）而
**不从 `get_db` 拿提交**（`db/session.py` 的 `get_db` 是 `finally: db.close()`，
从不提交）。所以阶段 3 那段结论在这里原样成立：

> `conftest.py` 的 `override_get_db` yield 的是 `db_session`（外层事务罩着、
> 请求结束既不关闭也不回滚），所以**「路由写了但没提交」在生产上是 404，在测试里是全绿**。

这一侧除 e2e 外**唯一的网眼**是同形的那一条：
`test_the_preview_survives_the_request_that_made_it`（刻意不用 `client` / `db_session`，
另起 `throwaway_database()` + 与生产同形的 `override_get_db`，再**在另一条连接上**
验这一批还在）。少了路由里那句 `db.commit()` 这条当场变红。

#### 前端：两张新表，只有第二面保护

`labels.ts` 加了 `MATCH_STATUS_LABELS`（九个码，**键序是从「能进」到「进不去」**，
与后端那三张集合对齐）与扩到三个码的 `IMPORT_CONFLICT_LABELS`（`AGE_MISMATCH` /
`DUPLICATE` / `IN_SYSTEM_RESULT`）＋ `matchStatusLabel` / `importConflictLabel`。

两张表都**只有第二面**（`e2e/vocabulary.spec.ts` 扫像素）保护：

| 面 | 有没有 |
|---|---|
| ①后端发的码 `labels.ts` 认不认 | 有（词条在） |
| ②视图有没有**调用**标签函数 | **有，而且是唯一真正跑得到的那一面** |
| ③服务端生成的导出文件认不认 | **不适用**（导入明细不进任何导出文件） |
| ④枚举列的排序 `Column.order` | **不适用**（明细弹层是普通 `<table>`） |

而第二面在这两张表上**曾经是空的**——`IMPORT_CONFLICT_LABELS` 那两列只在「查看明细」
弹层里渲染，e2e 要走到那里得先让库里存在一条本月导入过的记录；在共享的开发库上
「先导一条进去」会让这次运行**改变**后续用例看到的统计口径（缺口 8），所以此前
没有为它写用例（§3 记着那次取舍）。**这一期把这个洞补上了**：新增的
`vocabulary.spec.ts::MHT导入的批次明细` 自己**用接口造一批**（两行：一行空姓名 →
`INVALID_ROW`，一行查无此人 → `NOT_FOUND`），再点进明细弹层扫像素。

三条写法上的讲究，每一条都是「先证明有东西可扫，再断言它干净」（§测试注意那条第
三、四例的第五例）：

- 文件内容**固定**（100 个题号列一个都不能少，少了整份文件在**列级**就被 422 挡下来、
  批次根本不会建出来，用例退化成一个「表格是空的 → 没有裸编码 → 绿」的空转）；
- 两行的取值**不依赖名册此刻的内容**（空姓名在第 2 步就返回 `INVALID_ROW`，还没走到
  查名册；`e2e词表查无此人` 必然 `NOT_FOUND`）——这是选它们而不是选「年龄不符 /
  本月已导过」的理由；
- 造不出批次就**抛**（`expect(preview.ok(), …).toBeTruthy()`），不静默退化；
- `toHaveCount(2)` 先证明两行真的渲染了（明细弹层的 tbody 里**没有**空行，所以这个
  断言成立），再做**变异验证**：把 `{{ matchStatusLabel(row.match_status) }}` 改成
  `{{ row.match_status }}` → 红，报出的正是 `INVALID_ROW` 与 `NOT_FOUND` 两个码。
  这同时证明了那两行**真的被扫到了**（扫不到的空区域在变异下永远是绿的）。

`e2e/app.spec.ts` 的「MHT测评记录导入」用例跟着改了三处：`错误 2` → `无法导入 2`
（第 4 期把「这一行进不去」与「这一行要你拍板」分成两档，界面上照这两档各报一个数）、
批次号**从屏幕上读**（`BATCH-20260919-1` 按天编号，写死会在某一天红在一个与功能无关的
地方），以及「导入批次」里找得到同一批并且那一行有「继续处理」——**先 `toHaveCount(1)`
证明这一批在历史里，再断言它带着那个按钮**（顺序反过来的话，一个空表格也能让后面
那条通过）。

顺带记一处**看起来该有而刻意没有**的：`IMPORT_BATCH_STATUS_LABELS` 2026-09-19 从
`ROSTER_BATCH_STATUS_LABELS` 改名而来并**合成一张**——两条链路的批次状态是同一批码
（`PREVIEW` / `COMMITTED`），各留一张表就是两张会长歪的镜像。

#### 跑数

`make test` 600 → **608 passed / 0 failed**，`make e2e` 116 → **117 passed**。
全量 e2e 跑两遍（含变异验证前后各一次），演示库的批次表不增长。

### 26. V1.2 第 5 期：逐行处置、年龄三选项、汇总档、未匹配行的去处（2026-09-19）

第 4 期把测评导入变成了三段式（建批次 → 逐行匹配 → 逐行处置 → `commit`），
但「逐行处置」那一格在界面上是空的：预览里一行写着「年龄不符，请选择覆盖或放弃」，
而**没有任何地方能回答它**。这一期把那一格接上，并且顺手把两条悬着的东西落地——
汇总档那一档文件终于有了名字，而「没进得去的那几行」终于有了一个可查的地方。

**零 DDL。** 本期用到的列（`import_mode` / `out_of_scope_reason` / `age_resolution` /
`resolved_by` / `resolved_at` / `age_before` / `age_after` / `assessment_session.age_at_test`）
**全在 0013 里就有了**（§21）——本期一个字都没改表结构，产出全部是「写入方与读者」。
这与缺口 9 那条「列有了、路还没通」是同一句话的正面：对齐阶段铺的路，功能层一期一期接。

`make test` 608 → **630 passed**，`make e2e` 117 → **119 passed**。

#### 年龄三选项：一次用户裁决，以及它为什么必须有第三项

§18.5 里有两句话是打架的：「无论是否覆盖 `student.age`，**本次测评的 `age_at_test`
都必须保存外部年龄**」与「保留系统年龄时不要动 `age_at_test`」。前一句成立的话，
三个选项只剩两个（保留与覆盖在 `age_at_test` 上会写出同一个值）。**2026-09-19 用户裁决
让第二句让步**——于是三档在两种取值上各自不同，每一档都有一个只有它自己成立的理由：

| 选项（`age_resolution`） | `student.age` | `session.age_at_test` | 这一档在说什么 |
|---|---|---|---|
| `keep_roster` 保留系统年龄 | 不动 | **名册那个数** | 「以名册为准」——这次测评按名册上的年龄理解他 |
| `overwrite` 覆盖学生当前年龄 | 改写 | 文件那个数 | 「文件是对的，名册该更新」——两处一起改 |
| `session_only` 只保存本次测评年龄 | 不动 | **文件那个数** | 「名册先不动，但这一场确实是 13 岁测的」——两句话同时成立的那一支 |

**「文件里那个数一个字都没丢」是这三档共同的保证**：它一直留在
`assessment_import_row.raw_age` / `age_after` 上，逐行明细里看得见。所以「让步」让掉的
是 `age_at_test` 这一个字段的取值，不是外部事实本身——这条区别要记住，它是三档能同时
成立的前提。

落点只有一处：`_create_imported_session` 与 `_rewrite_imported_session` 都取
`row["age"]`，而那个值在 `commit_batch` 里由年龄处置决定（`AGE_RESOLUTION_OVERWRITE`
时才顺带 `_update_roster_age`）。**重导一处不能漏**（`_rewrite_imported_session` 里
那句 `session.age_at_test = row.get("age")`）：漏了的话它停在第一次导入时的数，
而那一行看起来完全正常——「同一次测评两个年龄」这种不一致要等对账才发现。

三条配套约定：

- **行上的 `age_resolution` 留空是「这一行没有年龄问题、没人问过」，不是「选了某个默认」。**
  与 `resolution` 留空同一条（§24）。
- **`resolution` 与 `age_resolution` 是两个字段、两个问题**：前者回答「冲突的那些行写不写」，
  后者回答「这一场按哪个年龄记」。同一个词在两边是同一个意思（`overwrite` 直接复用
  `RESOLUTION_OVERWRITE` 那个字面量），沿用它可以少一次数据迁移——但**它们不是同一个
  问题的第三个值**。
- **关系是「逐行覆盖整批」**：整批提交时的 `age_resolution` 是默认值，而逐行处置过的
  那一行不再受它左右。所以「批上是 `overwrite`、某一行是 `keep_roster`」是正常状态，
  而审计的 `detail` 记的是**处置完之后**的最终值（`row.resolution` 可能是这一次刚写的，
  也可能是上一次写的，而轨迹要回答的是「提交时会读到什么」）。

#### 逐行处置：`PATCH /assessment-import-rows/{row_id}/resolve`

**它不写任何测评记录**——真正的落库仍然全部发生在 `commit` 那一刻，理由与预览那一层
逐字相同（一个动作在它真的发生之前，库里不该出现它的后果）。所以这个端点改的是行上那
两列处置字段本身。

`resource_type` 是一个**新码 `ASSESSMENT_IMPORT_ROW`**，而它**没有中文映射**——
审计页那一列本来就裸渲染（缺口 7），这是知情的代价。用 `ASSESSMENT_TASK` 的话
`resource_id` 只能是任务 id，而一场任务下有几百行，那一条轨迹答不上「动的是哪一行」。
行不属于这所学校时回 404，判据与措辞都与 `load_batch` 同一句（§24：**「不属于你」与
「不存在」在响应上必须不可分辨**）。

#### 汇总档：`_write_sheet` 第一行的 `return`

§18.9 的汇总档（`EXTERNAL_SUMMARY`，六个身份列 + 总分 + 维度分，**没有题号列**）
走到 `_write_sheet` 就结束：**它一行答卷都不写，也不评分、不开待办。**
这不是「还没做」，是规格逐字要求的——`没有 100 道原始答案时**不得伪造**
assessment_answer，也不得**直接**按本地重点题规则生成风险事件`。汇总文件里没有答案，
所以本地那条重点题判据（第 85 / 97 题答「是」）**用不了**，而系统不该假装自己判得出来。
平台给的分数留在 `assessment_external_result.total_score` / `dimension_scores_json` 上。

代价是三处**已知且接受**的不一致（缺口 10，不是漏了）：

- 这一场在按 `assessment_result` 说话的那些页面（关注等级、关注率、受控导出）上是**空的**；
- 任务完成率按**目标行**算，会把它算成已完成；
- 会话 `status` 停在 `IN_PROGRESS`（`_write_sheet` 早退之后没有东西把它推到 `SUBMITTED`）。

三处的共同前提是**不许替未核验的外部结果担保**——而它们**不会**因为第 6 期的四档处置
（§27）而消失：四档只对「与在线答卷冲突」的那些行生效，一份汇总档匹配到一个没有在线答卷
的学生时四档碰不到它。缺的那一层是「这份外部结果算不算一份有效结果」，读者落在
`assessment_target.effective_external_result_id` 上，而它今天**只有写入方**。详见缺口 10。

#### 未匹配行的去处：判据是**两个取并集**，而且第二个不能省

`GET /assessment-tasks/{task_id}/unmatched-import-rows`（§18.10 的未匹配口径）。
它补的是 §18.4 那条出路的最后一环：一份文件里有几行没匹配上时，那些行从此有了一个
**可查的地方**（在它们所属的那场任务下面），而不是只活在那一次上传的返回值里。

两个判据取并集：

1. `match_status` 在 `MATCH_STATUSES_UNIMPORTABLE` 里（`INVALID_ROW` / `NOT_FOUND` /
   `OUT_OF_SCOPE` / `AMBIGUOUS`）——**任何处置都救不回来的那些**；
2. **或者** `processing_status == ROW_PROCESSING_SKIPPED`——提交时被放弃了的那一行
   （整批选了「放弃」、或逐行处置成 `skip`）。

**第二个判据不能省**：少了它，一个「选了放弃」的批次在这一页上会一条都不剩——而那一页
的读者正是要找出「这场任务还有谁缺着」。它匹配得上，而这一批没有把它写进去，所以对这场
任务而言它与「没匹配上」是同一件事。

**是「并入」而不是分成两档**：`reason_counts` 按 `match_status` 分，所以「哪些行是没能
匹配、哪些是被放弃的」仍然分得开（`processing_status` 也在逐行的响应里）。排成两组会让
同一份行表在界面上出现两次。

三点与既有约定同源：

- **计数与取数逐字相同**（同一组判据、同一个连接，抽在 `_unmatched_row_conditions` 里）：
  否则「共 N 条」与下面那张表各说各话（§11）。
- **`reason_counts` 数整场任务、不套读者的数据范围**（与 `list_import_rows` **刻意相反**，
  同 `batch_row_counts` 那条）：这个数坐在任务详情页上，说「这场任务有多少行没进来」，
  而范围已经在「哪些任务看得到」那一层生效了，再套一层会让同一个任务对两位老师报出
  两个「未匹配 N 行」。逐行明细那一层仍然过滤（那里逐行给姓名）。
  **两个数口径不同，所以界面把它们分开写、各自带着自己那半句话**（§9）：
  `整场共 N 行没进得去（…）；其中你看得见 M 行。`
- **两道门槛照抄 `task_targets`**（§22 那条的镜像）：这一页逐行印出学号与姓名，那是
  **组织与账号**那一档的数据。判据直接复用 `ensure_task_target_reader`——两处各写一份
  `scope_allows(...)` 就会有第三个定义悄悄冒出来。

返回里的 `items` **复用 `import_rows_payload`**，不在这里另拼一份：同一条记录在两个屏幕上
（任务详情页的「未匹配行」与数据中心那个明细弹层）必须逐字相同，两处各写一份序列化就是
两个定义，而它们漂移了不会有任何东西看得见。上限 `UNMATCHED_ROW_LIMIT = 200`，截断句与
出路照 §10（「凡是截断，都要自己说出来」）。

#### `_row_payload` 补 `batch_id` / `batch_no`：`row_no` 是**批内**编号

这一期给逐行响应加了这两个字段。理由就是「未匹配行那一屏是**跨批**的」：一场任务下
可以有好几批（先初一、隔几天再初二），而 `row_no` 在每一批里都从 1 开始——少了「批次」
这一列，看表的人会以为那两行 `2` 是重了、或者丢了一行。明细弹层（单批）里这两个字段
不承载信息，但同一条记录在两处必须是同一个形状（见上一条）。

#### 前端：四张新表、一个第三页签，以及 e2e 里**做不到**的那一半

`labels.ts` 加了四张表，都是**描述性**的（没有 `*_ORDER`，读者不是 `DataTable` 的
可排序列，§3 第四面）：

| 表 | 内容 | 渲染点 |
|---|---|---|
| `IMPORT_MODE_LABELS` | `EXTERNAL_FULL_ANSWER` 逐题答卷 / `EXTERNAL_SUMMARY` 只有分数 | 批次摘要那一格（**不条件渲染**） |
| `AGE_RESOLUTION_LABELS` | 保留系统年龄 / 覆盖学生当前年龄 / 只保存本次测评年龄 | 明细「处置」列（已提交的批次）+ 提交前那句提示 |
| `OUT_OF_SCOPE_REASON_LABELS` | `SUPPLEMENT_CANDIDATE` 可补发 / `NOT_IN_TASK_SCOPE` 不在发放范围 | 匹配结论那一格下面 |
| `UNMATCHED_REASON_LABELS` | `MATCH_STATUS_LABELS` **只改一档**（`MATCHED`） | 任务详情页的「未匹配行」页签 |

**`UNMATCHED_REASON_LABELS` 与 `MATCH_STATUS_LABELS` 只差一档**：`MATCHED` 在这一屏要读成
「**已匹配但被放弃**」——同一个码在预览里说「能进」、在这一屏说「没进」，因为这一屏的判据
是「这场任务缺不缺他」。所以它是**另一张表**
（`{ ...MATCH_STATUS_LABELS, MATCHED: '已匹配但被放弃' }`）而不是改掉原来那张的措辞：
预览页上把 `MATCHED` 读成「被放弃」是错的。`unmatchedReasonTone` 同理，只在这一档上
与 `matchStatusTone` 不同。

**但那一档今天造不出来**（2026-09-20 撞出来的）。这一屏的判据是两个取并集：`match_status`
落在 `MATCH_STATUSES_UNIMPORTABLE` 里，**或者**这一行被放弃了（`processing_status ==
SKIPPED`）。而「被放弃」只可能落在**要拍板的那三档**上（`AGE_CONFLICT` / `DUPLICATE` /
`CONFLICT`）：`_row_will_be_written` 对 `MATCHED` 落在最后那句 `return True` 上，
**整批选「放弃」也照写**；逐行处置那条路也走不通——`resolve_import_row` 拿 422
「不需要确认」把对 `MATCHED` 行的处置挡掉了。所以「匹配上了但被放弃」这一档在库里
**不可达**，`MATCHED` 在这个清单里一次都不会出现。

**这不是「那张表写错了」**：它读的那句话仍然成立——哪一天匹配上的行也能被放弃，照上一张
渲染就会让一份叫「没进得去」的文件里印出「已匹配」。**它是这个状态的读法，不是这个状态
存在的证据**。所以留着一个造不出来的词条，而**不**为它写一条恒绿的断言假装它被守住了
（§29 那条：一条恒绿的守卫没人会发现，它比没有更糟，因为它占着「这一条有人守」的位置）。

第一版把这件事记反了——`labels.ts`、`export_labels.py` 与本节都写着「那一档正是这一屏
存在的理由」，而**测试夹具是照着那句话写的**（造一行 `MATCHED` 再逐行处置成「放弃」），
于是三个用例停在一个**不可达的状态**上、一起红，而红的原因不是被测代码。三处 2026-09-20
一起改了，夹具改成「`AGE_CONFLICT` + 整批选放弃」这个真实可达的形状。

`TasksPage.vue` 多了第三个页签「未匹配行」。**那个块排在模板最后，不是顺手**：
上面那三条 `v-if` / `v-else-if` / `v-else` 是一条链，往链条中间插一个带 `v-if` 的块，
后面那几个 `v-else-if` 就会改挂到这个新块上——完成明细从此再也不显示，而报错、看不出是
排版问题。

#### e2e：两处可达、两处**不可达**，以及为什么不可达

三条（两新一改）用例在 `e2e/vocabulary.spec.ts`，变异验证 3/3 全红（把
`importModeLabel(...)` 与 `unmatchedReasonLabel(...)` 换成裸字段 → 三条各红一次，
报出的正是 `EXTERNAL_SUMMARY` 与 `NOT_FOUND`）：

- `MHT导入的批次明细` 加了一句 `toContainText('逐题答卷')`——「导入形态」那一格是
  `EXTERNAL_FULL_ANSWER` 在**弹层里**唯一的渲染点；
- `MHT导入的汇总档`（新）：用**没有题号列**的汇总 CSV 造一个 `EXTERNAL_SUMMARY` 批次，
  断言那一格是「只有分数」；
- `测评任务的未匹配行页签`（新）：任务**从 `GET /api/v1/assessment-tasks` 现取**、按
  `total_targets` 最大挑（不写死任务名——写死会在某一天红在一个与功能无关的地方），
  用一份**绑定该任务**的预览造两行（空姓名 → `INVALID_ROW`、查无此人 → `NOT_FOUND`），
  再点进第三页签断言那两句中文与「整场共 N 行没进得去」的口径句。

**另外两张表在 e2e 里第二面是空的，这是有意的取舍**（`AGE_RESOLUTION_LABELS` 与
`OUT_OF_SCOPE_REASON_LABELS`）。原因是**演示名册的班级叫 `1班`**（`seed.py` 的基线班级、
`seed_demo.py` 的 `GRADES` 都是这样），而文件里那一列按学校编号规则必须写数字
（`_parse_class` 的 `4` → `704`）——**任何 CSV 行都匹配不上学生**，所以匹配这一步永远走
不到「找人」，`AGE_CONFLICT` / `OUT_OF_SCOPE` / `DUPLICATE` / `CONFLICT` 在 e2e 里
**不可达**。要靠 e2e 覆盖它们，得先让演示名册长出一个叫 `704` 的班级，而那会改动共享
演示库的名册（缺口 8：跑 e2e 不许改掉数据），代价比它换来的那点保护大。
后端那一侧是钉住的（`test_assessment_import_api.py` 逐行断言那三档的响应），
漏的是「视图有没有调用标签函数」——与 `IMPORT_CONFLICT_LABELS`（§3）是同一类取舍。

**`overwrite` 刻意不列进 `UNTRANSLATED_CODES`。** 它与名册导入的处置码是同一个字面量
（`AGE_RESOLUTION_OVERWRITE = RESOLUTION_OVERWRITE`），而那张表
（`ASSESSMENT_RESOLUTION_LABELS`，第 4 期）从一开始也没有列进去：一个普通的英文单词在
别的文案里出现的可能性，比它换来的那点保护更值钱——**会无故变红的守卫很快会被人关掉**
（§18）。`keep_roster` / `session_only` 是 `snake_case` 短语，没有这个风险，所以照列。

**e2e 现在会在演示库留下 4 个批次**（此前 3 个）：两条新用例各造一个 `PREVIEW` 批次。
不随时间增长——`_reusable_batch`（同操作者 + 同 `file_sha256` + 仍 `PREVIEW` + **同
`task_id`**，§25）会把重传的那个文件复用掉。§25 里「实测跑完全量 e2e 后批次表只有 3 行」
那句话据此更新。

#### 顺带收窄的两条已知项

- **缺口 9 里「导入路径不走 `uq_session_effective_task_student` 那套判重」这条没变**，
  但 `OUT_OF_SCOPE` 的两种子情形现在在响应里分得开了（`out_of_scope_reason`）。
  界面**按它分岔**（一个「补发」按钮只该长在 `SUPPLEMENT_CANDIDATE` 上），而服务端
  **不给它兜底值**：`?? 'NOT_IN_TASK_SCOPE'` 会把「不知道」变成一句「补不了」，
  与 §22 那条 `scope_type` 不许 `?? 'SCHOOL'` 是同一条理由。
- **缺口 7 那条「审计页的 `resource_type` 是裸编码」多了一个新的活体**：
  `ASSESSMENT_IMPORT_ROW`（逐行处置写的那一条）。它同样是**知情的代价**——
  等那三件事一起做（服务端筛选器 + 中文映射 + 提示语对齐）时，这一条轨迹也会一起变。

### 27. V1.2 第 6 期：有效结果与冲突处置（2026-09-19）

第 5 期接的是「哪一行进得去」，这一期接的是**进去之后哪一份算数**。§18.8 那句话——
「一个任务一个学生可以保留多个来源事实，但**同一时刻只能有一个有效结果**」——在这一期
变成三样东西：一列、一个生成列唯一键、一个谓词。四档处置由 2026-09-19 的一次用户裁决
定下落点（规格里只有四个名字，`技术详细设计.md` 与 `vibe-input/` 都零命中）。

**零 DDL。** 本期用到的列（`is_effective` / `supersedes_session_id` /
`assessment_target.effective_session_id` / `effective_external_result_id` /
`assessment_import_row.conflict_resolution` / `assessment_external_result.verification_status`
/ `applied_session_id`）**全在 0013 里就有了**（§21）——与第 5 期同一条：对齐阶段铺的路，
功能层一期一期接。

#### 「同一时刻只有一个有效结果」是一条谓词，不是一个约定

三样东西合起来才成立，缺一样就退回「靠所有人记得」：

| | 是什么 | 少了它会怎样 |
|---|---|---|
| `assessment_session.is_effective` | `Boolean NOT NULL DEFAULT TRUE` | 外部顶掉在线那一场时只剩「删行」一条路，而 §1 不许删 |
| `uq_session_effective_task_student` | **生成列**唯一键：`is_effective=1 AND task_id IS NOT NULL` 时是 `CONCAT(task_id,':',student_id)`，否则 NULL | 「一个任务一个学生最多一场有效」变成写入侧的纪律 |
| `effective_session_predicate()`（`models/assessment.py:284`） | 函数体就是 `return AssessmentSession.is_effective.is_(True)` | 七个读者各写一遍，漂一个不会有信号 |

生成列那个形状值得单独记：`is_effective=0` 的行那一列是 **NULL**，而 MySQL 的唯一键
不管有多少个 NULL——所以「被降级的那一场」与「任务外的会话（`task_id IS NULL`）」都天然
不受这个键约束。**这也是 `USE_EXTERNAL` 必须先降级再插新场的原因**（见下）。

**七处调用**，一律调那个函数、不自己写 `is_effective.is_(True)`：`task_service:569`
（完成明细按场）、`export_service:86`、`analytics_service:107` / `:513`、
`assessment_service:126`（`latest_session`）/ `:245` / `:297`。另有一处**按列的等价写法**：
`assessment_import_service._current_effective_session:2221`——它的 docstring 写着它与
`_existing_session_for_match` 回答的是两个问题（「现在算数的是哪一场」对「这一行会不会
打架」），而后者**刻意不看** `is_effective`：预览要回答的是「你导的这份东西和库里什么
撞上了」。

**两处刻意不调，各有各的理由**：

- `care_service` 的 `history_sessions:271`（历次趋势）——被降级的那一场仍是他真实考过的
  一次：答案、用时、那天的分都在，趋势图少一个点就是在抹掉一段发生过的事实。降级说的是
  「他现在以哪一份为准」，不是「那一次不算测评」。同一处的 `sitting` 走
  `latest_session`，**是加了的**——两处不一样是有意的，注释就写在那两行中间。
- `assessment_service.create_or_get_session:360` **排序而不是过滤**
  （`order_by(is_effective.desc(), id.desc())`）：过滤掉之后「一场有效的都没有」（只有
  人工改库才可能出现）会掉进下面那条**新开一张卷子**的路径，撞上
  `uq_session_task_student_attempt`；排序最坏只是退回旧行为。而首选有效场之后，下面那道
  `source == "IMPORTED"` 的门**同时回答了两档**：`USE_EXTERNAL` 之后有效场是导入的那一场
  → 409「该测评由学校导入，不能在系统内作答」（正是该给的答复）；`KEEP_BOTH` 之后有效场
  仍是在线那一场 → 照旧返回给他。

#### 四档处置：规格只给了名字，落点是裁决的

| 处置 | 外部会话 | 在线会话 | `external_result.verification_status` |
|---|---|---|---|
| `KEEP_ONLINE` 保留在线 | **不建** | 保持有效 | `PENDING`（还挂着，以后还能再裁） |
| `USE_EXTERNAL` 采用外部 | 建，**有效** | `is_effective=0`、指新场 | `ACCEPTED` |
| `REJECT_EXTERNAL` 否掉外部 | **不建** | 保持有效 | `REJECTED`（终态） |
| `KEEP_BOTH_BUT_ONE_EFFECTIVE` 两份都留但以在线为准 | 建，`is_effective=0` | 保持有效 | `ACCEPTED` |

这张表的**可执行形式**是代码里那张 `CONFLICT_RESOLUTION_VERIFICATION`（是数据，不是
注释），四档的落点在 `commit_batch:2618-2766` 的四个分支里。三条不能混的：

- **`KEEP_ONLINE` 与 `REJECT_EXTERNAL` 在库里唯一的差别就是 `verification_status`**
  （建不建会话、谁有效全都一样）。「还没定」与「已经否了」是两件事，而 `REJECT_EXTERNAL`
  是**终态**、不是「这次先不写」。
- **「采纳」不等于「有效」**：`KEEP_BOTH` 也是 `ACCEPTED`，而那一场 `is_effective=0`。
  「这份数据我们认」归 `verification_status`，「这份数据算作当前结果」归 `is_effective`。
- **`USE_EXTERNAL` 不是「静默覆盖」**。§20#11 禁的是**自动**覆盖：整批点一次就把学生在
  线答的那一场就地改写、几份卷子作废，而屏幕上没有一句话说这件事。现在是逐行选、
  `is_effective` 留痕、`supersedes_session_id` 指得出新场、审计里写着这一档——原始答卷
  一条不删。

`CONFLICT_RESOLUTION_VERIFICATION` **收在一张表里**的理由要单独记：四档里有两档推的是
同一个值（`USE_EXTERNAL` 与 `KEEP_BOTH` 都是 `ACCEPTED`），而它们分住在**两个 `elif`**
里。各自写一行的话，漏掉其中一行**不会有任何东西报错**——`ACCEPTED` 与 `PENDING` 在
「哪一场有效」上完全一样，只有这一列不同，所以那一处漏写只表现为「学校认过的外部结果
看起来还没核」，而界面上没有任何东西看得见它（这一列今天**没有读者**，见缺口 11）。

#### 冲突行只能逐行处置，整批的「覆盖」对它无效

`MATCH_CONFLICT`（学生自己答过这一场、而文件里也有他）那些行走**自己的那道门**
（`_conflict_row_is_decided`），与整批的 `resolution` 无关——2026-09-19 的第二个用户裁决。
理由是这条路上唯一不能发生的事：**一次点击、一个整批动作，把几份学生本人作答的卷子
作废**，而屏幕上没有任何东西能把这个代价说出来。

两条出路都算已决定：`conflict_resolution` 有值（人逐行选了四档之一），或
`resolution == skip`（人明确不要这一行）。**整批的「放弃」不算回答**：它只是让这一行
不写，而「以哪一份为准」仍然悬着。没选就 422，并且**指名是哪几行**
（`commit_batch:2483`）：

```
第 3 行、第 7 行等 12 行与系统内已有的在线答卷冲突，需要逐行选择处置方式
（保留在线 / 采用外部 / 否掉外部 / 两份都留但以在线为准）。
整批的「覆盖」对这些行无效：一次点击不该作废几份学生本人作答的卷子。
```

行号照样截断到 10 条（§10：凡是截断都要自己说出来）——一句「有 3 条需要确认」会让人不
知道该点哪一行，而这一批可能有 200 行。**`_row_will_be_written` 是一处判据、三个读者**
（`writes` 判要不要建任务、循环判跳不跳过、这道门判这一行算不算已决定），所以它抽成函数
而不是在四处各写一遍：那个条件有两个维度（行自己的处置 / 整批的选择），写四遍必然有一条
先漂。冲突行取整批的 `resolution` 是错的，那一支只看行自己的。

#### `USE_EXTERNAL` 的两步次序，以及中间那次 flush

```python
if online is not None:
    online.is_effective = False
    db.flush()          # ← 不能省
session = _new_imported_session(...)
if online is not None:
    online.supersedes_session_id = session.id
```

**先让在线那一场退位、再建新场，次序不能换**：新场 `is_effective=1`，而
`uq_session_effective_task_student` 是生成列唯一键——旧的还没让开就插新的，flush 那一刻
必撞 `IntegrityError`（生成列一非空就参与唯一性）。中间那次 `db.flush()` 是这次序的
**执行形式**：赋值只是把对象标脏，不 flush 的话 SQL 的发出顺序仍由 unit of work 决定，
而它会把 INSERT 排在 UPDATE 前面。

`supersedes_session_id` 的方向以**模型上的注释为权威**：写在**旧行**、指向**新行**
（新场的 id 这时已经有了，`_new_imported_session` 内部 flush 过）。原始答卷一条都不删。

#### 一道把「正在作答」挡在外面的门

`CONFLICT_IN_SYSTEM_IN_PROGRESS`：学生**正在答**这一场时，外部结果不能顶掉它——判据在
匹配那一层（那一行带着这个 `conflict_code` 出现），出路与其余几档不同（「等他自己交完再
来」）。它与 `create_or_get_session` 的 409 一起，让「`USE_EXTERNAL` 之后学生再点开始
作答」这条路径**不可达**：那道门挡在前面，而学生已经交卷时界面上本来就是「已完成」，
走不到那个按钮。**这不是缺陷，是两道门各管一段。**

#### `PATCH /assessment-import-rows/{row_id}/resolve`：它一行测评记录都不写

（管理员 + 心理老师。）它只改行上那三个处置字段（`resolution` / `age_resolution` /
`conflict_resolution`）——真正的落库仍然全部发生在 `commit` 那一刻，理由与预览那一层
逐字相同：**一个动作在它真的发生之前，库里不该出现它的后果**。

- `resource_type` 是一个**新码 `ASSESSMENT_IMPORT_ROW`**，而它**没有中文映射**——审计页
  那一列本来就裸渲染（缺口 7），这是知情的代价。用 `ASSESSMENT_TASK` 的话 `resource_id`
  只能是任务 id，而一场任务下有几百行，那一条轨迹答不上「动的是哪一行」。
- 审计 `action="处置导入记录"`，`detail` 记 `batch` / `row` / `match_status` 与三个处置值
  （写 `NONE` 而不是留空——**留空与「没问过」分不开**，§24）。记的是**处置完之后**的
  最终值，因为轨迹要回答的是「提交时会读到什么」。
- **这一串 `detail` 今天只有数据库看得见**（`GET /audit-logs` 不发 `detail`，§8），所以
  `KEEP_ONLINE` 与 `REJECT_EXTERNAL` 的差别在界面上是零——想让它可查，要动的是那条
  序列化，不是这一行。
- 行不属于这所学校时回 404，判据与措辞与 `load_batch` 同一句（§24：「不属于你」与
  「不存在」在响应上必须不可分辨）。

#### `not_applied` 与 `resolved_rows`：两个新数，各按自己的判据

- **`not_applied`**（返回体里的数，批次表上**没有对应的列**——那要一条 DDL）：四档里前两
  档（`KEEP_ONLINE` / `REJECT_EXTERNAL`）**不建外部会话**，而这一行的
  `processing_status` 因此既不是「新增」（`created` 数的是**建出来的场**）也不是「更新」
  ——它填的是 `created + updated + skipped` 之外的第三块。这一档在 §18.8 的规格里没有
  名字，所以它写在返回体与审计里，不落列。
- **`resolved_rows` 按 `resolved_by` 数，不按 `row.resolution is not None`**（第 6 期修）。
  理由：下面的循环会替**整批**选择补写 `row.resolution`，之后这两者在那一列上长得一模
  一样——按它数会把整批动作数成人逐行看过的。`resolved_by` / `resolved_at` 只有
  `resolve_import_row` 写，是这件事**唯一**的痕迹。它也是**冲突行能被数进去的唯一途径**：
  冲突行的 `resolution` 至今仍是 `None`（四档写在 `conflict_resolution` 上）。

#### 第四个计数 `conflict`：它挡下的是「必须回答一个管不着的问题」

用户裁决「整批的『覆盖』对冲突行无效」之后，`batch_row_counts` 的三个数就不够用了：
`MATCH_STATUSES_NEEDING_RESOLUTION` 里那三档，**两类动作**——`AGE_CONFLICT` /
`DUPLICATE` 由整批那一次「覆盖 / 放弃」回答，`CONFLICT` 只能逐行选四档。而界面上那个
单选项组和提交按钮的硬门槛，此前读的都是 `needing_resolution`（那三档的和）。后果是
一条**只在冲突行上暴露**的死结：一批待确认**全是**冲突行时，操作员把每一行都处置好了，
按钮仍然点不亮——它要他在两个屏幕上刚说过「管不着这些行」的选项里挑一个。

所以 `batch_row_counts` 多返回一个 `conflict`（`counts.get(MATCH_CONFLICT, 0)`），
**它是 `needing_resolution` 的一个子项，不是并列的第四档**，界面用那个差额：

| 数 | 谁在读 | 用途 |
|---|---|---|
| `needing_resolution` | 面板标题「有 N 条记录需要确认」 | 那几条确实还需要他做点事 |
| `needing_resolution - conflict` | 两个单选项的显示条件、提交按钮的置灰、`commitAssessments` 的前置判据 | **整批那一次选择管得着的行数** |

`assessmentBatchResolutionRows`（`useDataImport.ts`）就是那个差额，一处算出、三处读
——这仍然是 §11 那条「指标卡上的数必须与它点进去的那个列表同源」：屏幕上的选项与它
能管到的行必须同源。

**冲突行那一道门不在前端**，这是刻意的：前端从 `row_counts` 看不出「那几行逐行处置过
没有」，而拿看得见的 200 行去猜就是一条会漏的守卫（`hasVisibleAgeConflict` 的注释记着
同一个边界）。所以未逐行处置就提交 → 服务端 422 并**指名是哪几行**
（`_conflict_row_is_decided`）——这正是用户裁决里选的那条路。

面板的话**跟着这个差额分岔**（同一处 `v-if` 的两支）：有整批管辖的行时，小字说那几条
「**不在**这两个选项管得着的范围里」；只有冲突行时，单选项根本不摆出来，小字改说
「这一批要确认的**全是**这一类，所以上面没有覆盖 / 放弃可选」。**不跟着分岔的那一版
会把「这两个选项」指到一组不存在的控件上**——与 §14 那条「一次失败的读取不许留下上一次
的答案」是同一类：留下的那句话会一直回答用户的问题。

**守卫只有后端那一侧**（`test_import_conflict_resolution.py` 的 182 行与
`test_assessment_import_api.py:2013` 逐字断言 `conflict`），前端这三处是**零覆盖**——
理由与四档中文那张表逐字相同（下一节）：`CONFLICT` 在 e2e 里不可达。变异验证因此只做
后端那一半：把 `counts.get(MATCH_CONFLICT, 0)` 改成 `0`，`test_import_conflict_resolution.py`
+ `test_assessment_import_api.py` **10 failed / 78 passed**——预期的那两条
（`test_keep_online_leaves_the_students_own_sheet_alone` 与
`test_a_row_that_collides_with_an_online_sheet_needs_a_decision`）都在里面，
另外 8 条红的走的是那个文件里**共用的断言 helper**（同一处 `row_counts` 字面量）。
**把前端那三处退回 `needing_resolution` 不会红**，而这条要记在这里，免得下次把它读成
「守卫还在」。

```ts
// useDataImport.ts（`conflict` 是 `needing_resolution` 的子集）
const assessmentBatchResolutionRows = computed(() => {
  const counts = assessmentRowCounts.value
  if (!counts) return 0
  return counts.needing_resolution - counts.conflict
})
```

#### 前端：两张表、一个「第三个问题」，以及 e2e 里做不到的那一半

`labels.ts` 加了 `CONFLICT_RESOLUTION_LABELS`（四档中文）+ `conflictResolutionLabel`，
`IMPORT_ROW_STATUS_LABELS` 多了一个 `NOT_APPLIED`（「未落成测评」，
`importRowStatusTone` 把它与 `SKIPPED` 一起归灰）。`api.ts` 的逐行处置载荷因此有**三个
并列**的处置字段：`resolution` / `ageResolution` / `conflictResolution`——它们是三个
问题，不是一个问题的三个值。

唯一的渲染点是 `/counselor/data` 明细弹层「处置」列里**冲突行专属的那一支**——非冲突行
渲染的是年龄处置。那一格有**两副面孔**，判据是这一批提交了没有：

- **预览中**（`DataCenterPage.vue:1008`）：四档渲染成一组**单选**
  （`v-for` 遍历 `CONFLICT_RESOLUTION_LABELS`，所以**表里的键序就是屏幕上的选项次序**），
  外加一个「或者：这一行不要了」（那一档写的是 `resolution=skip`，与四档各写各的字段，
  两个单选组互不干扰）。选项文字本身就是**影响说明**——「系统内那一场作废保留，
  不删除」这句话写在 `USE_EXTERNAL` 的标签里，不另做一块预览面板。
- **已提交**（`:989`）：渲染成文字而不是控件，读的是既成事实。**这一支也是第二面唯一
  扫得到这几张表的地方**：单选按钮的 `value` 不进 `innerText`，只渲染控件的话第二面在这
  两张表上就是空的。

`IN_SYSTEM_IN_PROGRESS` 那一行多两块东西，而它们与后端那道门**逐字同源**：打头那句问题
分岔成「正在作答（还没交卷）」与「已经交过卷」，`USE_EXTERNAL` 那个单选**被禁用**，
下面紧跟一句灰字说得出为什么（「作废它等于把他正在做的事扔掉。等他交卷之后再导入，
或者选下面的『这一行不要了』」）。**灰掉的按钮必须说得出为什么**，与 §17 那条
「空态是一句关于数据的话」同一族。

**四档与 `NOT_APPLIED` 进 `UNTRANSLATED_CODES`，而它们在 e2e 里是零覆盖，这是有意的
取舍**——与 §26 的 `AGE_RESOLUTION_LABELS` / `OUT_OF_SCOPE_REASON_LABELS` 逐字同源：
来源冲突的前提是这一行**匹配上了某个学生**（`IN_SYSTEM_RESULT` 还要那名学生在本场有在线
答卷），而演示名册的班级叫 `1班`、文件那一列按学校编号规则必须写 `704`，匹配这一步永远
走不到「找人」，所以 `CONFLICT` / `IN_SYSTEM_RESULT` 在 e2e 里**不可达**。要靠 e2e 覆盖
它，得先让演示名册长出一个叫 `704` 的班，而那会改动共享演示库的名册（跑 e2e 不许改掉
数据），代价比它换来的那点保护大。后端那一侧是钉住的
（`test_import_conflict_resolution.py` 十条）。`overwrite` 仍然不列进那份清单（理由见 §26
那一段），而这三档是 `SCREAMING_SNAKE` 的码，在别的文案里出现的可能性极低。

#### 跑数

`make test` 630 → **640 passed / 0 failed / 318.05s**（新增
`test_import_conflict_resolution.py` 十条）；`make e2e` **119 passed (36.0s)**——本期只
加了清单项、没加用例，所以 e2e 数与阶段 5 持平。

**这一期第一次跑 e2e 报了 4 failed，而根因与代码无关**：库停在 `0016`，缺
`conflict_resolution` 列，于是预览接口撞 MySQL 1054 回 500，界面上表现为导入那一块整片
不渲染。`make migrate` 升到 0018（35 张表 / 8151 行，**逐表一行不差**）之后 119 passed。
**这与 §20 那类「代码与库版本分岔，而症状看起来像功能坏了」同源**——开发库每加一条迁移
都要跟着升，而它看起来只是「这条功能坏了」。

### 28. V1.2 阶段 7：档案病例、乐观锁与两处 500（2026-09-19）

对齐阶段给 `student_care_case` 加了 `case_version` / `closed_by` / `reopened_by` /
`reopen_reason`、给 `care_case_event` 建了表、给 `manual_review` 加了
`care_case_id` / `student_id` 与复合外键 `manual_review_fk_case_student`——**而它们一个
都没有写入方**（缺口 9 点名了其中三条）。这一期把这批列接上，顺带修掉两条已知会 500 的路径。

**零 DDL。** 本期一个字都没改表结构，产出全部是「写入方与读者」——与第 5 / 6 期同一条：
对齐阶段铺的路，功能层一期一期接。

#### `care_case_event` 是这份档案的**病历**，`audit_log` 是系统级轨迹

同一次操作产生两条，而它们回答的问题不同：审计回答「谁在什么时候调了哪个接口」，
事件回答「**这份档案经历了什么**」。读者、保留期、权限都不同。

八个码，判据只有一句：**这个动作改变了这份档案的 `status` 或 `owner_id`（或者它是这条
生命的起点）。** 按这条，家庭回访与复测**也记**——它们会把状态推回 `FOLLOWING` /
`OBSERVING`，看起来像「只是加了一条记录」，实际改的是档案自己的状态。

| 码 | 写在哪 |
|---|---|
| `CASE_OPENED` 开档 | `assessment_service.open_or_reuse_care_case`（**系统自动开**：`operator_id` 为 NULL，时间线上显示「系统」） |
| `MANUAL_REVIEWED` / `FOLLOW_UP_ADDED` / `FAMILY_CONTACT_ADDED` / `RETEST_PLANNED` | 四个写入入口，各自写在 `db.flush()` 之后 |
| `CASE_CLOSED` / `CASE_REOPENED` | `close_case` / `reopen_case` |
| `OWNER_ASSIGNED` 转派 | `batch_assign_owner`——**唯一一条 `from_status` 与 `to_status` 都为空的事件**（它不改状态、只改归属，`reason` 里写着新负责人的名字，时间线上那一行于是不渲染状态迁移） |

**`services/care_events.py` 为什么是一个独立模块**（而不是 `care_service` 里的几个函数）：
`care_service` 已经 import 了 `assessment_service`，而开档那一条写在
`assessment_service.open_or_reuse_care_case` 里——助手放在 `care_service` 就会让
`assessment_service` 反过来 import 它，那是一个循环导入。所以那个模块只依赖 `models`。
（`assessment_service` 里那两行仍是**局部 import**，理由是让「谁依赖谁」在这一行上看得见，
不必读三个文件才能确认。）

四条容易写错的：

- **时间线按 `id.desc()` 排，不按 `created_at`。** `now_utc_naive()` 截断到整秒（§20 那条
  生产改动的另一面），所以「交卷 → 开档 → 复核」这种同一秒内发生的事时间戳并列，按时间排
  与按 id 排会给出两个次序。
- **`reason` 是 `String(255)`，写入方自己截断**（`REASON_COLUMN_LIMIT`）。各调用方递进来的
  长度不一（`record_type` / `channel` 是 64 字的枚举码、`close_reason` 128 字、重开原因
  允许 5000 字），不截断时 MySQL 严格模式回的是 `1406 Data too long for column 'reason'`
  ——离「事件里那句话太长了」隔着一个列名。截断只影响这个**摘要**格，原文留在原来那张表
  自己那一列上。这与 §18 那条「写入方与读取方对『空』的理解必须由写入方保证」是同一条，
  只是这一处管的是「超长」。
- **`close_note` 不进事件**：它是关档人写下的判断，家在 `student_care_case.close_note` 上
  （详情页直接渲染它）。事件是「发生了什么」，不是它的副本。
- **家庭回访正文也不进**（§16.6 点名的四类不得留存内容里有它）：它留在
  `family_contact_record.confirmed_facts` 上，那一行才是它的家。`confirmed_facts` 这一列
  （DDL 里就有的 `Text`）记的是**复核与跟进**的事实记录——时间线上只写「人工复核 · 张三 ·
  09-19」而下面没有一行事实，这条病历就只剩一串动词，读不出发生过什么。

#### 乐观锁三件套，与那道 `_ensure_open`

`care_service.py` 的前 135 行现在是三个函数，一处读、一处写、一处拒：

| | 做什么 | 用在哪 |
|---|---|---|
| `_check_case_version(care_case, expected)` | 对不上 → 409，消息里同时说出「库里是哪个版本、你手上是哪个」 | 关闭 / 重新打开 / 转派（逐行） |
| `_bump_case_version(care_case)` | `+1` | 上面三处，改完立刻 |
| `_ensure_open(care_case)` | `status == "CLOSED"` → 409 | 复核 / 跟进 / 家庭回访 / 复测**四个入口** |

**`case_version` 从这一期起不再是常量 1**（缺口 9 那一条关闭）。接口层的两个请求模型
（`CloseCaseRequest` / `ReopenCaseRequest`）把 `case_version` 声明成**必填**——一个可选的
乐观锁等于没有锁：客户端不传就永远不冲突，而它挡的正是「两个人拿着同一份页面各点一次」。
批量转派是把 `{case_id: case_version}` 整批带进来、循环里逐行判，任一条陈旧就当场抛
——抛出去时这一次请求的事务不会提交，所以**整批要么全做、要么全不做**。

**`_ensure_open` 堵住的不只是 §16.4 那条规格要求（「关闭档案不可直接新增跟进，必须先重新
打开」），它同时堵住了四个同类 500。** 那四个入口都会把 `status` 改成 `FOLLOWING` /
`OBSERVING`，而对一条 CLOSED 档案这样做等于**隐式复活**它——`active_student_id` 生成列
立刻非空，若这名学生已经又有一条在办档案（秋季关档、春季再开，§1），它当场撞
`uq_care_case_one_active_per_student`，用户拿到一句英文的 500。那四个入口此前都没有查过
状态（`git diff` 里能看见：删掉的行里没有任何一处判 `status`）。

#### 两处已知的 500，两处确定的处置

| 原本 | 处置 | 为什么不是另一种 |
|---|---|---|
| `reopen_case` **无条件**把 `status` 设成 `FOLLOWING`，学生已有在办档案时撞唯一键 → 500 | **先查再改**：查该学生非 CLOSED 且不是本条的档案，有就 409，并把**编号说出来**（「这名学生已经有一条在办档案（编号 N）…」） | 这不是一个可以自动决定的问题（要不要把现在那条在办的关掉？那是另一件有代价的事），所以答案是把事实说清楚，让老师自己选。0014 的 precheck 把「迁移时库里已经有多份在办」挡在迁移之前，挡不住**迁移之后**这个动作 |
| `open_or_reuse_care_case` 是一条无锁的读-然后-插入 | `begin_nested()` 包住插入 + 捕获 `IntegrityError` 后**重查一次** | 选它而不是 `SELECT … FOR UPDATE`：这里**根本没有行可锁**，锁的前提是那一行已经存在，而这条路要处理的恰恰是「不存在、于是两个人一起插」；`FOR UPDATE` 在没有行匹配时锁的是间隙，能不能挡住第二个插入取决于隔离级别与索引，在这个项目里既没有先例也无法稳定复现。唯一键是数据库已经有的那个判据，代价只是撞上时多一次 SELECT |

`begin_nested()` 那个 savepoint 是必须的：`IntegrityError` 会让**整个事务**进入失败状态，
不套 savepoint 的话捕获之后连重查那条 SELECT 都发不出去。重查回来仍然为 `None` 时**重新
抛出**——那说明不是这个原因（外键、或别的约束），别把它吞掉。

**被 savepoint 回滚掉的那一次插入不会留下开档事件**（事件与档案在同一个 savepoint 里），
所以正好只有一条 `CASE_OPENED` 落库。这一条没有单独的断言，它由
`test_two_requests_that_both_find_no_case_still_leave_exactly_one`（用 `monkeypatch`
把第一次查询变成"看不见"，逼出那条冲突路径）顺带钉住——那条用例断言的是「最终只有一条
在办档案」，而事件数在同一个夹具里也能看。

#### `closed_at` / `close_reason` / `close_note` **刻意不清**（2026-09-19 裁决）

`reopen_case` 把它们留在原地，与 `reopened_at` 并存。这不是「状态不一致」：它们回答的是
「**上一次是怎么关的**」，那是确实发生过的事。清掉等于抹掉一段历史——与 §1「关闭档案不得
删除历史记录」是同一条。上一轮的关档说明、这一轮的重开原因、以及两者之间隔了多久，
三条一起才读得出这条时序。（写这条时对照过 `close_case`：它同时写 `closed_by` 与
`owner_id`，而「谁关的」在这两列上会各说各话——`owner_id` 是负责人、可能被转派走，
`closed_by` 才是关档人。所以 `reopened_by` / `reopen_reason` 也一并加了写入方。）

#### `manual_review.care_case_id` 有了非空写入方

§16.4 那一条：新建记录的 `care_case_id` 必须非空。现在 `create_manual_review` 写它，
**并且 `student_id` 取自档案而不是请求体**——复合外键 `manual_review_fk_case_student`
断言这两列指向同一名学生，而让调用方各传一个就等于把那条约束的输入交给调用方，
写错时数据库报的是一句英文（`1452`）。既然档案上就有那个人，就没有第二个输入。
（缺口 9 里「`manual_review.care_case_id` / `student_id` 无写入方」这一条据此关闭一半——
**历史行仍然是 NULL**，那批行是这一期之前写的，没有回填。）

#### 前端：一条时间线、三个页面带上版本号

- **个案详情新增「档案事件」时间线**（`CareCaseDetailPage.vue`）：药丸 + 操作人 + 状态迁移
  + 事实记录。转派那一条两个状态都为空，那一行就不渲染（`v-if="event.to_status"`）。
  码 → 中文走 `labels.ts` 的 `CARE_EVENT_LABELS` / `careEventLabel` / `careEventTone`
  （§3 第一面），视图里不写中文。
- **关闭 / 重新打开 / 转派三处都带上 `case_version`**：`CareCaseDetailPage.vue` 与
  `CounselorWorkbenchPage.vue` 的关闭 / 重开取 `detail.value.case_version`，
  `CasesPage.vue` 的批量转派取列表行上的 `c.case_version`（`api.ts` 的
  `CareCaseItem.case_version` 为此从"只有详情页有"变成两处都有）。冲突时服务端 409 的
  那句话直接落到 toast 上——「你手上的是 N，库里是 M」，而不是一个 500。

#### e2e：`.pill` 那个定位器，与一次演示数据重建

新增一条用例（`学生档案的档案事件页签`），它先问接口要一个**真有事件**的学生
（`studentWithCaseEvents`），再点进页签扫像素。定位器收在 `.timeline-item .pill` 上而不是
整条 `.timeline-item`：`hasText` 是**子串**匹配，而 `CASE_OPENED` 自己的 `reason` 就写着
「系统自动开档」——按整条匹配时，一个把名字渲染成 `CASE_OPENED` 的坏实现**照样能命中**
（它命中的是正文里那两个偶然出现的字）。这是「**先证明有东西可扫，再断言它干净**」那条
教训的又一次发作——§测试注意里写着「给这个文件加用例时照办」，这一条是照办的样子。

**顺带重建了演示数据，而这件事本身值得记。** 旧演示库的 `care_case_event` 是 **0 行**：
那 12 份档案是**阶段 7 之前**种的，那时这张表还没有写入方；而 `make seed-demo` 的循环
不会再推进它们（所有 risk_event 都已 `REVIEWED`，`if not pending: continue` 先挡住）。
**正解是 `make purge-demo && make seed-demo`，不是给种子写一段「从既有记录反推时间线」的
回填**——那等于替演示数据编一段历史，而全部事件的时间戳都会是「现在」，与全新种出来的
效果一样却没有全新种的那份真实。重建后 `care_case_event` **43 行、七种码齐全**，
`manual_review.care_case_id IS NULL` 从 11 行降到 **0**（顺带让演示数据也展示了这一期的
写入方）。

#### `seed_demo` 里新加的那一行：已关闭的档案不再往前走

阶段 7 给四个写入入口加了 `_ensure_open` 之后，`seed_demo` 的档案闭环就有一个**真实可达**
的崩溃点：那个循环遍历**全部**档案，而其中任何一条是 CLOSED、且它名下的风险事件还有
`PENDING` 时，`create_manual_review` 会当场抛 `AppError: 这份关注档案已经关闭…`。
**实测过**（不是推出来的）：把一份 `stage != 0` 的档案（position 1）置 CLOSED、把它名下的
风险事件置 PENDING，`python -m app.db.seed_demo` 就以那个 409 收场——而这个脚本叫
「可重复执行」，崩出来那句话与种子毫无关系。

处置是循环里加一句 `if case.status == "CLOSED": continue`，**位置在转派之前**（那一块也是
在「推进」这份档案，而关过的档案本来就有负责人），`summary["cases"]` 那个计数留在它**之上**
——它数的是「库里有多少份档案」，不是「推进了几份」。加了之后同一个构造下 `EXIT=0`、
`cases: 12` 不变；全新种出来的库上这一行**永不触发**（CLOSED 是在同一次循环里刚关的，
而当次不会再回到它），所以逐字输出与从前一致。

#### 跑数、守卫与变异验证

`make test` 640 → **649 passed / 0 failed**（`test_care_api.py` +8、`test_status_vocabulary.py`
+1），`make e2e` 119 → **120 passed**（全量两遍：`120 passed (34.7s)` / `120 passed (27.1s)`）。

后端 8 条新用例各自钉住一件事，三条值得单独记：

| 用例 | 钉住什么 |
|---|---|
| `test_close_refuses_a_stale_version_and_changes_nothing` / `..._reopen_...` | 陈旧的版本号 → 409，且**什么都没改**（断言的是「改动没有发生」，不只是「返回了 409」） |
| `test_batch_assign_refuses_the_whole_batch_if_any_version_is_stale` | 批量转派的整批语义 |
| `test_reopening_an_old_case_that_has_a_newer_active_one_is_a_409_not_a_500` | 上面那条 500 的转化 |
| `test_a_closed_case_refuses_every_new_record_until_it_is_reopened` | 四个入口逐个试一遍（关档后全 409，重开后又能写） |
| `test_the_event_timeline_records_the_whole_lifecycle` | 一条档案走完开档 → 复核 → 跟进 → 回访 → 复测 → 关档 → 重开，断言的是**事件序列** |
| `test_a_manual_review_is_attached_to_the_case_it_reviews` | `care_case_id` / `student_id` 非空且指向同一名学生 |
| `test_two_requests_that_both_find_no_case_still_leave_exactly_one` | savepoint + 重查那条路 |

`test_status_vocabulary.py` 新增 `CARE_EVENT_TYPES` 与
`test_care_case_events_use_the_documented_vocabulary`（§3 第一面：后端发的八个码，
`labels.ts` 必须认得）。

**变异验证 4/4 全红，每条都 `cp -p` 备份、改完 `cmp` 逐字节还原**：

| 变异 | 位置 | 结果 |
|---|---|---|
| M1 | `care_events.py` 的 `CASE_OPENED` 值改成 `CASE_CREATED` | `test_status_vocabulary.py` **1 failed**（`Extra items in the right set: 'CASE_OPENED'`） |
| M2 | `open_or_reuse_care_case` 里整段去掉 `record_case_event(...)` 调用 | **1 failed**（只看「事件写没写进去」；第一稿把 `del` 追加在调用之后，语义上什么都不做、全绿——**是变异写错不是守卫失灵**，改成整段替换才红） |
| M3 | `CareCaseDetailPage.vue` 的 `{{ careEventLabel(event.event_type) }}` → `{{ event.event_type }}` | 档案事件用例**红**（`.pill` 里 filter '开档' 收到 0 个元素） |
| M4 | `labels.ts` 的 `CARE_EVENT_LABELS` 删掉 `FOLLOW_UP_ADDED` 词条 | 用例**红**在 `leakedCodes` 那一行——证明清单那一半也有牙、且那一段真的被扫到 |

**顺带修掉一处我自己埋下的脆弱判据**：`test_data_scope.py` 里两条载荷补了
`case_version: 1`（关闭 / 重开现在必填版本号）。那不是"改松断言"，而是新接口形状的后果
——它红的原因是「请求模型多了一个必填字段」，与它要钉的数据范围无关。

### 29. V1.2 阶段 8：导出作业化与可撤销会话（2026-09-19）

对齐阶段留下的**最后两张没有写入方的表**在这一期接上：`export_job`（§16.3）与
`auth_session`（§16.5）。在此之前，导出是「一次点击直接吐字节」，登出是**纯粹的客户端
动作**——被偷走的 token 照样用到过期。这一期把这两件事都变成**库里的事实**。

**零 DDL。** 与第 5 / 6 / 7 期同一条：两张表都由 0013 建好了，本期产出全部是
「写入方与读者」。

`make test` 649 → **696 passed / 0 failed**，`make e2e` 120 → **122 passed**。

#### 导出从「即时下载」变成一次**作业**：两跳

`POST …/export` 现在只回**作业载荷**（`job_no` / 类型 / 用途 / 遮蔽等级 / 行数 / 有效期），
字节一律从 `GET /export-jobs/{job_id}/download` 出去。§16.3 那三件事——文件短期有效、
可撤销、能数被下载了几次——**每一件都要求下载经过一道门**，而「即时下载」那条路上
根本没有可以拦的地方：字节在 `return` 那一刻已经在响应体里了。

这一改动波及的是**测试的交通方式**，不是断言：`test_audit_export_api.py` 的
`export_and_download(client, path, headers, json)` 是那个两跳的封装（建作业 → 取文件，
**返回取文件那一步的响应**），现在有三个文件共用它。两处没跟着改的用例当场变红，
症状各不相同，两处都值得记：

- `test_data_scope.py::test_bulk_export_excludes_other_schools`：
  `assert 'S001' in response.text` 撞上 `'{"success":true,"data":{"id":1,"job_no":…}'`
  ——它断的是「这份文件里没有云海的人」，而拿到的是一段 JSON。
- `test_assessment_import_api.py::test_the_export_source_column_names_the_sitting_…`：
  `_csv_rows()` 把那段 JSON 当成 CSV 解，`StopIteration` 报在找 `S701` 的那一行。
  **这个错的形状比上面那个更值得记**：报出来的是一句「找不到这个学生」，离真正的原因
  （文件还没生成）隔着一整个接口。

两处都只是换了交通方式，**CSV 内容断言一个字没改**——这不是把断言改松。

#### `field_policy` 的来历：字段白名单不是一个开关，是**文件自己的表头**

§16.3 要求「导出接口不得接受任意字段名」。落法不是「写一张允许列的清单，再拿请求体去
比对」——那种写法有第二个定义（那张清单），而它与真正写出去的那些列漂移了不会有任何
东西看得见。这里是从**产物**倒着写：

```python
field_policy={"columns": list(document.columns)}   # document 是那个 ExportDocument
```

`ExportDocument`（`services/export_document.py`，一个 frozen dataclass：`csv_text` /
`columns` / `row_count`）是「这份文件里有哪些列」的**唯一定义**，由生成文件的那一处填。
于是「请求体里带一个列名」这件事**在类型上就没有落点**——没有任何一行代码从 `payload`
里读列名。这条约束是**构造上**成立的，不是靠一处判断守住的。

它还有一个更实用的作用：`field_policy` 是**事后的**答案。三个月后有人问「那份文件里
有没有学号」，读这一列就行，不必找到那个版本的程序、再读它的 `_task_completion_csv`。

**`ExportDocument` 这个模块本身是为了断开一个循环 import**（`task_service` 要发文件、
`export_service` 要问任务完成明细，谁先 import 谁就是一个环），与 `care_events.py`
同一个形状。**`row_count` 不数表头那一行**——它在界面上是「导出 N 行」，把表头算进去
会每一份都多一行。

#### `mask_level` 单独一列，与 `purpose` 分开

理由与 §8 那条缺口逐字同源：`purpose` 是**自由文本**，担不起任何机器判据；而
「这份文件是不是实名的」恰恰是导出审计唯一要回答的问题。两条同样 action、同样
`resource_type`、同样 `purpose` 的轨迹，遮蔽与实名在库里长得一模一样——所以
`mask_level` 是它自己的列（`MASKED` / `IDENTIFIED`）。

审计里也照写（`_export_detail` 把遮蔽模式拼进 `detail`），**但那一串 `detail` 今天只有
数据库看得见**（`GET /audit-logs` 不发 `detail`，§8）——所以界面上那个「遮罩」列读的是
`export_job.mask_level`，不是审计。这是 §8 那条缺口之外**新出现的一个读者**：导出这件事
的遮蔽模式现在有一处界面读得出来了，因为它是作业行自己的一列，不是审计的一段文本。

#### 状态是现算的：**撤销优先于过期**

`effective_export_status(job, *, now=None)`，库里只写「撤销」这个**真实发生过的动作**
（`status=REVOKED`）——「有没有过期」由 `expires_at` 与此刻比出来。与 §12 的
`effective_task_status` 同一条：存下来的只是事实，推出来的是结论。

所以一行会在**没有任何人碰它**的情况下自己从「可下载」变成「已过期」，而它那一行在库里
一个字都没动。判据次序是**撤销在前**：一份先被撤销、后又跨过有效期的文件，它的故事是
「有人叫停了它」，而不是「它自己到期了」——两个人做的事不能读成一个人没做。

这条次序在 `labels.ts` 里也写着：`EXPORT_JOB_STATUS_LABELS` 的**键序 = 判据次序**，
`EXPORT_JOB_STATUS_ORDER` 取它（§3 第四面里那张表的新一行），导出中心那一列的可排序
声明用的是它。

`PENDING` / `FAILED` 两个码**不在前端表里**：导出是同步的（作业行与文件在同一个请求里
成型），所以它们在库里不可达——模型上有默认值是 DDL 对齐阶段的形状。**留着词条等于给一个
永远不会出现的格子写中文**（§3 那条「表在 `labels.ts` 里而没人从那儿取也算没接上」的
反面：给没有读者的码写词条同样是一种没接上）。

#### 下载只有**本人**能取，管理员能列能撤但**不能下**

这是本期唯一一处刻意的**不对称**，页面上也写着（`ExportCenterPage.vue` 顶上那段）：

- 管理员能看**全部人**的作业台账、能替任何人撤销（数据外泄时那是一个紧急开关）；
- 但他的 `STUDENT_PSYCH_DETAIL` 是 `NONE`（§4）——**一份实名名册不该经过他**。
  受控导出不能成为绕过心理详情的旁路（§4 那条已经有了），所以下载那一列对他不出现。

#### sha256 不匹配时回 **409**，不是「照样发」

`_write_export_file` 的哈希是从**真正写出去的那一串字节**算的，不是把 CSV 文本再
`encode()` 一遍另算一份（那样两处各算各的，漂移了看不出来）。下载时重算一遍磁盘上那份，
对不上就拒绝——**宁可发不出去，也不要发一份说不清是不是当初那一份的文件**。

#### 两个 `TODO_BUSINESS_CONFIRMATION`（在 `settings_service.DEFAULTS` 的 `export` 组）

| 项 | 出厂值 | 为什么是这个值 |
|---|---|---|
| `job_ttl_hours` | `24` | §16.3 要求文件短期有效，而「短期」是多少没有业务答案。24 小时是一个能用的默认值，不是结论 |
| `max_rows` | `0` | **0 = 不限**，不是「一个都不导」。**没有编一个截断数**：截断要自己说出来（§10），而这里连「多少算多」都没有依据。超出上限时**拒绝生成**而不是截断——§10 那条「截断只许影响显示，绝不许影响写入」的镜像 |

#### §18.10 收口：应测口径的**分子与分母来自同一个集合**

需求说明书那一句是「应测人数 = 任务目标人数 − 请假 − 免测 − 已排除」，而它在这期变成
一个**谓词**（`models/assessment.py:344` 的 `expected_participation_predicate`）与三个数
（`task_service.task_participation_counts`，:151）：

| 键 | 数的是什么 |
|---|---|
| `total_targets` | 目标行数（发放 + 补发，**原始人数**） |
| `excluded_targets` | 其中请假 / 免测 / 已排除的那些，**相减得到**，不是另数一遍 |
| `expected_targets` | **应测人数** |
| `completed_targets` | 应测名单里已完成的目标行 |
| `completion_rate` | 有效完成率 = 上一行 ÷ 应测人数 |

**这条谓词住在模型层，不住在 `task_service`**，理由与 `effective_session_predicate`
逐字相同：完成明细（按场）、`analytics_service` 与 `care_service` 的完成率都要用它，
而这三处的 import 方向是**反的**（`assessment_service` 依赖 `task_service`）。一处定义、
四处引用，漂一个不会有任何东西报错。

**分子也用它，这是这条算式里最容易做错的一处。** 一个被标记为请假/免测的学生**即使后来
还是答了卷**，也不进这一场的完成率——他不在应测名单里。按 `status` 数分子、按 `expected`
数分母会给出一个**超过 100% 的完成率**，而那在屏幕上只是一个数字偏高。
`test_task_participation.py::test_a_student_who_was_excused_stays_out_of_both_the_top_and_the_bottom`
钉住它。这与「他答过」不冲突：那一行仍然是 `COMPLETED`，完成明细里照旧一行一人、照旧
带着他的分（§1 的四层事实模型：**标记管理事实不修改原始答卷**）。

四个码（`PARTICIPATION_REQUIRED` / `_LEAVE` / `_EXEMPT` / `_EXCLUDED`）在模型里，
**三个减项的顺序就是算式的顺序**，`labels.ts` 的 `PARTICIPATION_DISPOSITION_LABELS`
按同一序排。**「为什么拒绝参加」的理由码仍然没有定义**（§16.10 的
`TODO_BUSINESS_CONFIRMATION`）：`disposition_reason` 收的是一句人写的说明，不是码——
「用户裁决「编码是实现自由度」」只覆盖那三个减项，**把尚未定稿的词表编出来等于让一个
没人认得的码开始落库**（§21 那条「认不出的当场报错、不猜默认值」的同一句话）。

顺带记一处连带效果：`task_target_counts`（`effective_task_status` 的输入）**「应测」这
两个字是这一期加的**。默认状态下**一个数都没变**（没人标记过时全是 `REQUIRED`，应测人数
等于目标人数），所以唯一可见的变化是「把最后一个学生标记成请假，这场就结束了」——而那
正是学校标记他时想要的结果。

`task_completion`（:605）取有效会话走的是 `effective_session_predicate()`（§27）——
被降级的那一场不再算作完成。

#### 标记参与状态：`PATCH /assessment-tasks/{task_id}/targets/{target_id}/participation`

**它一行答题事实都不动。** 目标行的 `status`、会话、答卷、结果一个不碰——「该不该参加」
与「参没参加」是两个维度，标记为请假永远只是让这一行不进应测名单，不是「把他标成没完成」。

| 判据 | 是什么 |
|---|---|
| 写权 | 心理老师（`ensure_task_writer`），**外加** `ensure_student_in_scope`——「他是心理老师」不说「他是**这名**学生的心理老师」，所以只带 CLASS 范围的老师改不动别人班上的行（§9 的第二个入口） |
| 非 `REQUIRED` | **必须给原因**（`disposition_reason`，String(128)），`REQUIRED` 不给 |
| 恢复 `REQUIRED` | 等于取消标记：原因一起清掉；`marked_by` / `marked_at` **两个方向都写**——它们回答的是「谁最后动了这一行」 |
| 行不属于这场任务 | 404，判据与措辞与「不存在」同一句（§24：「不属于你」与「不存在」在响应上必须不可分辨） |
| 被范围拒绝 | **不写审计**（§9：给一次被拒的写入记上「标记参与状态」，会让访问轨迹反过来撒谎） |

审计的动作码是「**标记参与状态**」，`detail` 记 `目标 {id} · {旧} → {新} · 原因={原文}`
——**留原文而不是只记新值**：只记新值的话，「从请假改回应测」那一条与「本来就是应测」
在轨迹上长得一样（`NONE` 而不是留空，§24 那条「留空与没问过分不开」）。审计由**路由**
写（服务层不碰审计是全库一致的形状），并且带 `student_id`——那一列让这一次标记出现在这名
学生的敏感访问记录里。

`GET …/participation` 返回上面那五个数**加一个不在表里的** `unimported_records`
（任务外 / 重复 / 未匹配 / 冲突的导入记录数）。规格那句话的后半截是「它们不进入任务完成
率」——而它们本来就住在 `assessment_import_row` 上、**从来不在目标行里**，所以那半句是
**构造上成立的**。单独数出来给读者看，是因为「这场任务有多少行没进来」是一句学校要问的
话，而它不属于上面那五个数中的任何一个（混进去会让分母变成一个说不清的东西）。

`excluded_targets` **不拆成请假 / 免测 / 已排除三列**：那要先有 §16.10 那套理由码表，
而今天落库的是一句人写的原因（逐行明细里逐条列着）。**从自由文本反推分类是猜。**

`scope` 是可选的读者范围谓词，**同时套在分子与分母上**——「应测」与「已完成」必须来自
同一个集合。这一整块是**统计**，与 `task_target_counts` 那条「状态口径是整场任务」不同
（§12：状态是任务自身的属性，完成率跟读者的范围走）。一个数也不套范围的情形只有一种：
`effective_task_status` 问「这场还能不能开新卷子」，拿的是不带 `scope` 的那一份。

#### 两份任务级明细清单的导出：**三道门槛**，与 §22 那条是同一个形状的镜像

`POST /assessment-tasks/{task_id}/non-participants/export`（§18.11 第九个端点）与
`POST /assessment-tasks/{task_id}/unmatched-import-rows/export`（第十个，2026-09-20 补）
逐行印出学号与姓名，那是**组织与账号**那一档的数据；它们又是任务内部的；而导出的遮蔽
等级是 `MASK_LEVEL_IDENTIFIED`（实名）。所以三道依赖各管一段：

`require_role(*TASK_READERS)`（任务读者）+ `ControlledExporter`（受控导出）+
`DetailExporter`（`STUDENT_PSYCH_DETAIL: {SCOPED}`）。

少任何一道都能单独造出一个洞：少了第一道，一个不是任务读者的人拿到了任务的目标名册；
少了第三道，`CONTROLLED_EXPORT: PROGRESS_SUMMARY` 的德育领导能导出一份**实名**名单
——而他在 `/students` 上连按行姓名都拿不到（§4 那条的第二次发作）。这个组合那些
逐角色的用例**构造不出来**（默认矩阵里 Leader 本来就缺心理详情、Admin 本来就缺任务读者），
所以 `test_permissions.py::test_revoking_psych_detail_also_blocks_the_two_task_detail_exports`
专门把 `STUDENT_PSYCH_DETAIL` 降成 `NONE` 再各导一次，**两个端点都验**——只验一个的话，
另一个上通着的那条旁路没有任何东西看得见。

**★ 那道第三门槛 2026-09-20 从 `ensure_non_participant_exporter` 改名为
`ensure_detail_exporter`。** 第十个端点要的是**同一对判据**，而照旧名字复用会让「未匹配行
导出」去调一个叫 `non_participant` 的函数——名字开始撒谎，而下一个人会照着它再写一份
「给未匹配行用的」版本（那正是「同一处只许有一个定义」反复记着的形状）。改名只碰调用点
与测试里的引用，判据本身一个字没动。

两个端点都只有 `POST` 一个动词（导出是**一次作业**，不是一次读取，§16.3 那三件事都要求它
落一行到 `export_job` 上）——而它们各自的**读**法在 `GET …/participation`、第三页签
「未匹配行」与数据中心那个明细弹层上，那几处不生成文件。

**两份文件与它们各自的屏幕逐字相同，而「相同」是构造出来的**：都用同一个函数取数
（`unmatched_rows_csv` 与 `list_unmatched_import_rows` 共用 `_unmatched_row_conditions`）、
同一张中文表（`unmatched_reason_label`，**不是** `match_status_label`——这一屏的 `MATCHED`
读作「已匹配但被放弃」）、同一个次序（批次从新到旧、批内按行号）、服务端早就拼好的那一句
`message` 也照抄不另拼。

**★ 第十个端点刻意不封顶。** `list_unmatched_import_rows` 有 `UNMATCHED_ROW_LIMIT = 200`，
`unmatched_rows_csv` **没有 `limit` 参数**——不是忘了传，是它不该有。那 200 是给眼睛的
（屏幕上翻页有出路），而一份被截断的文件**没有任何东西看得出来**：收到的人会以为「这场
任务就只有这 200 行没进来」，而屏幕上此刻正写着「整场共 N 行没进得去」（那个数是整场地
数出来的）。§10 那句「**截断只许影响「显示」，绝不许影响「写入」**」在导出这一侧就是
这一条。守卫是 `test_the_unmatched_export_is_not_capped_even_though_the_screen_is`：
造 `UNMATCHED_ROW_LIMIT + 1` 行，**两个数一起断**（文件 201 行**且**列表端点仍然只回 200）
——只断文件行数的话，一个把 200 条样本拿去写文件的实现也过。

**`field_policy` 那一列照旧是产物倒着写的**（`list(document.columns)`），所以七列表头
来自 `UNMATCHED_ROW_COLUMNS` 这一处定义，请求体里没有列名的落点（§16.3）。

#### 会话：`jti` + **每一个请求**都查一次

`auth_session` 此前只有写入方（登录时插一行），**没有任何读者**。现在：
token 里带 `jti`，`get_auth_context` 用**一条 SELECT** 把 `UserAccount` 与 `AuthSession`
按 `id` / `jti` / `session_token_hash` 一起 join 出来，逐条判：

| 情况 | 回什么 |
|---|---|
| token 里没有 `jti` | 401「请先登录」——**历史令牌全部失效是有意的**（与 §22 那条「`tested_at` 刻意不回填」同一个道理：给一个不带会话标识的令牌放行，等于让「登出」这件事对**已经发出去的那些**令牌永远无效） |
| 查不到那一行 | 401「登录状态已失效，请重新登录」 |
| 账号 `active` 为假 | 「账号已停用，请联系管理员」 |
| `revoked_at` 非空 | 「登录状态已被撤销，请重新登录」 |
| 过期 | 「登录状态已过期，请重新登录」 |

**四句话必须分得开**，因为它们导向的动作不同：停用要找管理员、撤销是**有人做了决定**、
过期只是放太久了。合成一句「登录失效」会让用户做错事（去重打密码，而问题在别处）——
与 §2 那条「服务端答了话」是同一条。

**选「每个请求都查」而不是「只在敏感接口查」**：后者要维护一张「哪些接口算敏感」的清单，
而那张清单**漏一项是静默的**——被撤销的 token 在那个接口上照样能用，没有任何东西会报错。
一次主键 join 换掉一整类静默失败，值得。（它与 `get_current_user` 早就在每个请求查一次
`active` 是同一条思路，见 §4 那条「停用 ≠ 删除」。）

#### `logout` 现在真的撤销了，动作码一个字没改

`POST /auth/logout` 调 `revoke_session(db, session, "用户主动退出")`。此前它只写一条审计
——「强制下线」这件事根本做不到。**码仍然是「退出」**：审计是已经落库的历史，用户在
审计页按眼睛看到的名字搜（§3 那条教训的第五次位置）。

撤销的三条路各有各的范围，这个差别是有意的：

| 动作 | 撤销哪些 | 为什么 |
|---|---|---|
| `logout` | 当前这一条 | 就是这个意思 |
| `change-password`（本人） | **其它**所有，当前留着 | 改密码本身就是在说「我怀疑别人拿到了我的密码」，所以别处的会话等于没改；但把用户自己也登出会让他以为改失败了（他手上那份 token 下一个请求才开始被拒） |
| `admin_reset_password` | **全部**，含他此刻正在用的那一条 | 管理员重置的是别人的密码，那个人手上所有 token 都该立刻失效 |

「退出其它所有设备」（`POST /auth/sessions/revoke-others`）也留着当前这一条，
同一个理由。**撤销自己的当前设备回 422 并指出出路**（「要退出，请用右上角的「退出」」）
——不是「不行」，是「你要的那件事在别处」：点了撤销而页面看样子还好好的，是最难理解的
那种失败。

`sessions/{session_id}/revoke` 对「不是你的」与「不存在」回**同一句话同一个码**（§24：
**「不属于你」与「不存在」在响应上必须不可分辨**）。

**两条路由的声明次序不能换**：`/sessions/revoke-others` 必须排在
`/sessions/{session_id}/revoke` 前面，否则 FastAPI 先把 `revoke-others` 当成一个
`session_id` 去转 int，回 422 而不是执行到那个端点。

#### `admin_reset_password`：三件事都是 §16.5 逐字要求的

- **强制改密**（`must_change_password`）：临时密码是管理员口头/短信给的，只该活一次登录；
- **旧会话全部撤销**（含那个人此刻正在用的）；
- **不回传明文**：`reset_password` 从此返回 `None`，响应体里**没有** `temporary_password`。
  此前那一版把它发回浏览器，而统一响应封装会把整份 payload 送出去——规格禁的是
  「返回或记录明文密码」，那条正好两条都踩。

`purpose` 是管理员填的**原因**，必填，进 `purpose` 那一列。审计记目标账号 / 原因 / 结果
（`detail=f"账号 {user.account}；撤销登录会话 {revoked} 个；要求下次登录修改密码"`），
**不记密码**（§16.6）。

#### ★ 登录那一次 flush：MySQL 1213 死锁，以及「unit of work 不会替你排序」

**这是本期最值钱的一条**，因为它是本项目第一次遇到「同一时刻两张表都要写、而它们之间的
先后是硬要求」——而它报出来的是一句与业务毫无关系的英文。

症状是**间歇的**：`POST /api/v1/auth/login` 偶尔 500，`pymysql.err.OperationalError (1213,
'Deadlock found when trying to get lock; try restarting transaction')`。在 e2e 上表现得
更像 UI 的毛病——`loginAs` 的 `waitForURL` 超时，而它看起来像前端路由坏了。

成因两条叠加：

1. `auth_session.user_id` 是指向 `user_account` 的外键，所以**子行的 INSERT 会拿父行的
   共享锁**；而登录那一次 flush 里，父行自己还有一条 UPDATE（`last_login_at` /
   `failed_attempts` / `locked_until` 那三处改动）。两个并发登录各自拿着对方要的那把锁，
   就是完整的死锁环。
2. **`AuthSession` 与 `UserAccount` 之间没有 `relationship()`**，所以 SQLAlchemy 的
   unit of work **看不到依赖边**——它按 mapper 的**注册次序**决定先发哪一条，而
   `AuthSession` 注册在 `UserAccount` 前面，于是 INSERT 每次都排在 UPDATE 前面。

**实测（探针，独立跑三遍，结果一致）**：去掉 `authenticate` 里那一行 `db.flush()`，
SQLAlchemy **每次都**先发 INSERT 再发 UPDATE。处置就是把那条次序由书写者钉死：

```python
db.flush()          # 父行的 UPDATE 必须先出去——理由见上面那一长段注释
```

守卫是 `test_auth_sessions.py::test_the_parent_row_is_updated_before_the_session_row_is_inserted`
——它挂 `before_cursor_execute` 把这一次 flush 发出的语句记下来，**先断言两条都真的发出去
了**（只写后半句的话，一个「两条一条都没发」的实现也能过），再断言 UPDATE 在 INSERT 之前。

**这一条要往两处推广，而第二处今天没有守卫**：`reset_password` 与 `change_password`
里各有一行同样目的的 flush，理由相同（下面各写了一条子行）。**它们今天恰好是对的，
靠的是注册次序**——而那正是这次踩到的东西。`reset_password` 的注释里明写了这一点
（「它**没有守卫**」）：哪天有人给 `UserAccount` 与 `AuthSession` 之间加上
`relationship()`，unit of work 就会按依赖边自己排对，这两行 flush 变成多余的；
而在那之前，谁调整了 mapper 的注册次序，谁就把它们改坏了而没有任何东西会红。

**可复用的那条**：**「两次写入之间有先后要求时，顺序要由书写者保证，不能指望 unit of
work」**——尤其当两张表之间**没有** `relationship()` 的时候，因为它连「看得见的依赖」都
没有。

#### 前端：导出中心 + 登录设备弹层

- **`ExportCenterPage.vue`**（`/counselor/exports` 与 `/admin/exports` 共用一个组件，
  与 `TasksPage` / `AuditPage` 那几个同形）。管理员与心理老师的差别只有一处
  （下载那一列对他不出现），而那处差别**写在页面上**，不靠用户自己发现——§9 那条
  「口径要写进界面」。
  - 「有效期」那一格**按状态分岔**：可下载的写它什么时候到期，已过期的写它什么时候
    过期的，已撤销的写它什么时候被撤销的。同一格三句话，因为读者要做的判断不同。
  - 「能不能下载」读的是服务端算的 `downloadable`，**不由这里的状态推**——一处判据
    （`_is_downloadable` 同时给列表的旗标与下载端点），不可能两处各说各话。
  - 「撤销」那个按钮只长在 `READY` 行上：那个动作**不可撤**（库里写的是事实）。
  - 撤销原因**选填**是有意的：撤销是数据外泄时的紧急动作，那一刻多一个必填字段就是在
    最不该拦人的地方拦人（这是「破坏性动作的默认值不能是破坏」的正面——**它不是破坏性
    动作，它是止损**）。
- **`SessionListDialog.vue`**（顶栏「登录设备」，与「修改密码」并排——这两件事在用户的
  脑子里是同一类：都关于「我这一份登录」）。逐行印 IP 与浏览器，**只看得到自己的**：
  管理员要踢人走的是「重置密码」那条路，那一次会撤销全部会话。

#### 词表：五张新表，四个面各走一遍

`labels.ts` 加了 `PARTICIPATION_DISPOSITION_LABELS`（应测 / 请假 / 免测 / 已排除）、
`AUTH_SESSION_STATUS_LABELS`（活跃 / 已撤销 / 已过期）、`EXPORT_TYPE_LABELS`（**六种**——
第五种「未参与名单」随第九个端点、第六种「未匹配行清单」随收口时补的第十个端点）、
`MASK_LEVEL_LABELS`（姓名已遮蔽 / 实名）、`EXPORT_JOB_STATUS_LABELS`（可下载 / 已撤销 /
已过期）+ `EXPORT_JOB_STATUS_ORDER`，以及 `participationLabel` / `participationTone` /
`exportTypeLabel` / `maskLevelLabel` / `maskLevelTone` / `exportJobStatusLabel` /
`exportJobStatusTone` / `sessionStatusLabel`。

**五张表里只有 `EXPORT_JOB_STATUS_LABELS` 有 `*_ORDER`**，其余四张没有，各有各的理由，
理由都写在它们各自的 docstring 里（§3 第四面：可排序的枚举列必须**显式声明**，而反向的
「这张表为什么不做排序」也要写下来，否则下一个人会以为是漏了）：

| 表 | 有 `*_ORDER` 吗 | 为什么 |
|---|---|---|
| `EXPORT_JOB_STATUS_LABELS` | **有** | 导出中心那一列 `sortable: true` + `order: EXPORT_JOB_STATUS_ORDER`；键序 = `effective_export_status` 的**判据次序** |
| `PARTICIPATION_DISPOSITION_LABELS` | 没有 | 键序**就是算式次序**（后端 `PARTICIPATION_EXCLUDED_DISPOSITIONS` 按同一序排），但那一列不可排序（完成明细是普通 `<table>`）——**键序有意义 ≠ 需要 `*_ORDER`**，前者的读者是「算式」，后者才是 `DataTable` |
| `AUTH_SESSION_STATUS_LABELS` | 没有 | 会话列表是普通 `<table>`（每个人手上就几台设备），没有 `Column.order` 的读者 |
| `EXPORT_TYPE_LABELS` | 没有 | 那一列不做排序（有用的是状态与时间） |
| `MASK_LEVEL_LABELS` | 没有 | 不做排序（两档分成两组看不出什么） |

中文写成「姓名已遮蔽 / 实名」而不是「脱敏 / 不脱敏」：后者听起来是一个开关的两档，
而这里要回答的是**这份文件里能不能认出人**。

**第三面（服务端生成的导出文件）本期过了两张新镜像，而其中一张逼着守卫自己长了一截。**
`MATCH_STATUS_LABELS` 与 `UNMATCHED_REASON_LABELS` 进 `export_labels.py`——第十个端点
那份 CSV 的「匹配结论」列要按**未匹配行那一屏**的读法渲染（`MATCHED` 在那里是「已匹配但
被放弃」，照原表渲染会在一份叫「没进得去」的文件里印出「已匹配」）。而它在 `labels.ts`
里**不是一张字面量的表**：

```ts
export const UNMATCHED_REASON_LABELS: Record<string, string> = {
  ...MATCH_STATUS_LABELS,          // ← 展开
  MATCHED: '已匹配但被放弃'
}
```

镜像测试的 `frontend_map()` 原来只做 `re.findall(r"([A-Z_]+):\s*'([^']*)'", …)`，
**看不见展开**——它会把这张表读成只有一条，而后端那九条一比就红。所以给解析器加了
展开合并（先递归取出 `...XXX`，再覆盖字面量键），MIRRORED_MAPS 加两行；后端那两张也
用**同一个形状**写（`{**MATCH_STATUS_LABELS, "MATCHED": …}`），于是「两边各抄一遍中文」
这件事根本没有发生——这正是这个文件存在的理由。

**扩展守卫本身要有自证**：`test_the_spread_tables_are_actually_read_through` 就是那一条
（展开解析不能是空转的），变异验证 ③ 摘掉展开解析时，红的正是它而不是别的用例。
这与 §18 那条「变异验证的复原校验必须拿落盘的原始字节当基准」是同一个形状的两种：
**一条新加的守卫，要先证明它真的在跑**。

**而 `EXPORT_TYPE_LABELS` 自己 ③④ 都不适用**，两处都写在那张表的 docstring 里：
`export_type` 是作业自己的类型、**不进任何 CSV**（那是「文件的列清单」，是另一件事），
而导出中心那一列不做排序，所以它**没有 `*_ORDER`**。

第二面（`e2e/vocabulary.spec.ts` 扫像素）本期加了两条用例，**两条都是先自己用接口造数据
再扫**——理由与第 4 期那条逐字相同（`seed_demo` **不种**导出作业与会话，光靠 `auditPages`
扫的是空表）：

- **「顶栏的登录设备弹层」**：先用 `page.request.post('/api/v1/auth/login')` 开两条会话，
  撤销其中一条（`doomed`），再 `loginAs` 走界面点进弹层，断言 `.session-row` 的第一条
  可见**且**有一行「已撤销」——**两个色带都要有，否则一个只渲染「活跃」的实现也是绿的**，
  最后 `leakedCodes(modal)` 为空。
- **「导出中心的作业台账」**：用接口建一个作业，断言 `job_no` 匹配 `/^EXPORT-/`，
  走界面断言那一行有「关注档案摘要 / 姓名已遮蔽 / 可下载」，再用接口撤销、重载页面、
  断言变「已撤销」。

**`PARTICIPATION_DISPOSITION_LABELS` 是为数不多「不需要新用例」的表**，因为它的渲染点
（完成明细弹层「参与状态」那一列）**不条件渲染**——每一行都出一个药丸，演示数据里每一行
都是 `REQUIRED`。所以本文件上面那条「测评任务的完成明细弹窗」用例已经扫得到它了
（与 §3 里 `IN_SYSTEM` 在「全部学生」页签上的处境同形）。

**两条新用例都不断言行数**（§测试注意那条）：`REVOKED` 那几行每跑一次 e2e 就多一行
（`auth_session` 更甚——**每一次 `loginAs` 都插一行**，跑完一轮全量 e2e 之后那个表会多出
一百多行）。写死数字会让这两条在某一天红在一个与功能无关的地方。

**导出中心进 `auditPages` 的路径清单这件事本身要有一句记录，因为它是量出来的**：路径加进
清单之后做变异验证（把三个 `xxxLabel(...)` 全换成裸字段），四条角色用例**全绿**——因为
`export_job` 表此刻 0 行，`DataTable` 渲染的是空态那一行，一个格子都没有。**清单加成功了，
覆盖仍然是零**。这与「先证明有东西可扫，再断言它干净」是同一句话，只是这次的「没有东西
可扫」来自一张空表而不是一个没点开的页签。

#### §17(c) 收口：三条一直没有守卫的验收，与它们各教了一件事

那句话里列了五样，收口时逐条问了一遍「它的判据写在哪里」，三条已有、两条没有：

| 验收 | 守卫 |
|---|---|
| 管理员重置密码后旧 Token 全部失效 | `test_auth_sessions.py::test_resetting_a_password_revokes_every_session_including_the_one_in_use` |
| 撤销会话无法访问敏感接口 | `test_auth_sessions.py::test_logging_out_kills_the_token_immediately` |
| 系统管理员没有心理数据查看权限 | `test_permissions.py:52`（一行断言） |
| **查看原始答卷前必须填写查看原因** | **此前没有**——代码在 `students.py`，而用例只钉住了它的**下一句**（「范围外不写审计」） |
| **审计详情不包含原始答卷和家庭回访正文** | **此前没有**——`test_audit_export_api.py` 那两条断言的是**导出文件**，不是**审计行** |

现在收在 `test_sensitive_reads.py`（3 条）。第三条是把验收清单 §5 那句「德育领导查看学生
档案时不返回重点题和访谈正文」一并落下来——它在实现上的形状是**整个端点不可达**，
而原有的 `test_leader_is_denied_case_detail_under_defaults` 钉的是**列表**接口，
验收句里点名的两个端点（个案详情、重点题）一个都没被点过。

**这两条各教了一件事，都是「守卫看上去在，其实不在」的形状：**

- **`audit_log` 上有两列叫得像同一件事，而它们是两个东西。** `detail`（`Text`）是审计
  真正写的那一列；`detail_json` 是 V1.2 对齐时加的、**今天既没有写入方也没有读者**
  （§27 记着它）。第一稿的判据写成 `row.detail_json is None`——那是一条**恒真**的断言
  （一个永远是 NULL 的列，任何内容都进不去），所以**变异验证时它没红**才被发现。
  这是「会无故变红的守卫很快会被人关掉」的镜像：**一条恒绿的守卫没人会发现，
  它比没有更糟，因为它占着「这一条有人守」的位置**。
- **`scope_allows` 是先拒 `NONE`、再看 `allow` 的**（`permissions.py:196`）。
  所以「把 `NONE` 加进 `allow={...}`」**不是**一道能打开门的变异——没配这一档是一道
  更早的硬拒。第一稿按这个变异，用例照绿；那时该怀疑的是**变异写错了**
  （§28 的 M2 是同一条教训），不是守卫失灵。真正的变异只有一种：把那一档真的授给
  德育领导（改 `CAPABILITY_DEFAULTS` 或插一行 `role_permission`），改完变红。

三条的变异验证因此是 **4 次尝试 / 3 次成功**：M1（摘掉空原因那一判）、
M2（让回访审计带上正文）、M3（真的把重点题授给德育领导）各红一次，
另有一次是上面那条写错的变异。每一次都 `cp -p` 备份、`cmp` 逐字节还原。

#### §20 十六条逐条落成用例（§7 第 4 条）

需求说明书 §20 那十六条验收此前**一次都没有逐条对过**。这一期把它们逐条落到用例上，
每条写「判据在哪个文件的哪条用例」——**这是一次清点，不是一次声称**：下面每一行都
`grep` 过那个函数名。

| # | 验收句 | 守卫 |
|---|---|---|
| 1 | 目标快照准确生成 | `test_task_targets.py::test_the_target_snapshot_is_written_column_by_column` |
| 2 | 三批导入、重复学生不重复计数 | `test_assessment_import_api.py::test_imports_in_the_same_month_share_one_task_and_get_their_own_targets`（目标行一人一条、同月归一场任务）+ `test_task_participation.py::test_the_counts_split_the_target_list_into_expected_and_excluded` |
| 3 | 无学号按校/年级/班级/姓名匹配 | `test_assessment_import_api.py::test_a_name_shared_by_two_classmates_needs_gender_and_age_to_resolve`（按性别与年龄定位到 S702，`match_confidence` = 0.9） |
| 4 | 同名同班 → `AMBIGUOUS`，**不得自动导入** | 同上文件 `::test_two_classmates_that_gender_and_age_cannot_split_is_an_error`（`row_counts["error"] == 1`，且「覆盖」也写不进去） |
| 5 | 年龄不一致显示差异、未确认不得覆盖 | `test_assessment_import_api.py:2531`（`AGE_CONFLICT` + `age_before` / `age_after` = 12 / 13） |
| 6 | 覆盖后保留旧值/新值/操作人/批次/时间 | `age_before` / `age_after`（同上）、`resolved_by` / `resolved_at`（`test_assessment_import_api.py:2763`）；批次由行自己的 `batch_id` 带（`::test_the_batch_number_travels_with_each_row`） |
| 7 | `NOT_FOUND`，**不能自动创建学生** | `test_assessment_import_api.py::test_a_missing_class_and_a_missing_student_are_reported_separately` + `:822` 那一组（名册行数前后不变） |
| 8 | `OUT_OF_SCOPE` 不进完成率 | 行**根本不成目标行**（`assessment_target` 里没有它）+ `expected_participation_predicate()`（§29 那条算式） |
| 9 | 补发确认后才新增目标行 | `test_task_targets.py::test_supplement_previews_first_and_only_writes_after_confirmation` |
| 10 | 在线作答**进行中**不得被自动覆盖 | `test_import_conflict_resolution.py::test_an_in_progress_sheet_cannot_be_replaced_by_an_external_result` |
| 11 | 已提交的在线答卷不得被**静默**覆盖 | 同上文件 `::test_a_submitted_sheet_is_never_silently_overwritten`（四档要人逐行选）+ `::test_the_batch_choice_never_reaches_a_conflict_row`（整批的「覆盖」管不着它） |
| 12 | 只有汇总分数时不伪造 100 道原始答案 | `test_assessment_import_api.py:2351`（`AssessmentAnswer` 计数 == 0） |
| 13 | 重复导入不产生重复会话/结果/风险事件 | `test_assessment_import_api.py::test_overwriting_replaces_the_previous_record_in_place`（sessions 仍是一条、维度仍 8 行、风险事件仍 1 条） |
| 14 | 四类记录可**查询与导出** | 查询：`test_assessment_import_api.py::test_the_rows_that_did_not_get_in_are_listed_under_their_task`；**导出：本期的第十个端点**（`test_task_participation.py` 那四条） |
| 15 | 完成率只统计**有效目标学生**和**有效结果** | 前半 `test_task_participation.py::test_a_student_who_was_excused_stays_out_of_both_the_top_and_the_bottom`；**后半不成立，见下** |
| 16 | 导入 / 年龄覆盖 / 补发目标 / 冲突选择**均写入审计** | `导入测评记录`（`assessment_import.py:156`）、`更新学生年龄`（`test_assessment_import_api.py:697`）、`补发目标学生`（`test_task_targets.py:423`）、`处置导入记录`（`test_assessment_import_api.py:2775`） |

**#15 的后半（有效结果）在汇总档上不成立，这不是漏了一条用例。** `_write_sheet` 对
`EXTERNAL_SUMMARY` 早退、不写 `assessment_result`，所以那一场在按结果说话的页面上是
空的——这是**缺口 10** 逐字记着的那件事，它的读者落在
`assessment_target.effective_external_result_id` 上，而那一列今天**只有写入方**。
指向缺口 10，不在这里另立一个说法。**接它之前不要顺手统一**：把汇总档塞进
`assessment_result` 正是那句话禁止的「替未核验的外部结果担保」，会让一个未经核验的分数
直接进关注率。

**规模数字（1000 人 / 30+40+50 人）不落成夹具**，与 §测试注意那条「不要给账号表加精确
行数断言」同源：那些数是规格在描述**形状**（快照一行一人、分母按人去重），而写死 1000
只会让用例在演示数据变一次之后红在一个与功能无关的地方。所以上面每条钉的都是那个形状。

#### 缺口 12：完成明细那两道门（2026-09-20，收口之后）

收口那天清点 §20 十六条时撞出来的最后一条缺口，用户当天裁决「加一道门」。
**零 DDL、零新端点**——只在既有两条路径上补门槛，加一处抽出来共用：

| 改动 | 位置 |
|---|---|
| 判据抽出来（`STUDENT_PSYCH_DETAIL: {SCOPED}`） | `task_service.ensure_detail_reader`（`ensure_task_reader` 旁边） |
| 读与导出共用一个数据层 | `task_completion` 开头两道：`ensure_task_reader` + `ensure_detail_reader` |
| 导出那一侧改名并复用它 | `ensure_non_participant_exporter` → **`ensure_detail_exporter`**，函数体改成「`CONTROLLED_EXPORT` 一道 + `ensure_detail_reader` 一道」 |
| 路由层别名 | `tasks.py` 的 `DetailExporter`（与 `ControlledExporter` 分开声明：前者三项导出共用，后者只有**建作业**那一侧要） |
| 前端 | `TasksPage.vue` 的 `canReadDetail` 与三枚导出按钮的判据 |

三点值得记：

- **判据按问题取名，不按第一个调用者取名。** 第九个端点先落地时它叫
  `ensure_non_participant_exporter`，第三个调用点（完成明细导出）一来，那个名字就开始
  撒谎——而下一个要写「给未匹配行用的」版本的人正是照着名字找上来的。与 §22
  `TASK_SCOPE` 那次、§「同一处只许有一个定义」是同一个形状。
- **门槛放进 `task_completion` 的数据本身**，三个调用点自动全覆盖。只加在路由上会留下
  「服务层还有一条路读得到」的形状，而这正是「两处各查一次、拆一处仍然全绿」那个陷阱的
  另一面（§4 记着它：**别把「拆了一处仍然全绿」读成守卫失效**）。
- **前端三枚导出按钮的判据从 `canWrite` 改成 `canReadDetail`，并补了一处漏掉的 `v-if`。**
  「导出未参与名单」「导出未匹配行清单」此前按 `canWrite` 显示，而它们是**导出**不是**写**
  ——`canWrite` 与 `canReadDetail` 今天恰好只有心理老师为真，所以这个错看不出来，但判据
  按问题取名之后它们归位了。更要紧的是「导出CSV」（完成明细那一枚）**此前没有 `v-if`**、
  只有 `:disabled`：门加上去之后它就成了领导手上的一枚**必然 403** 的按钮——缺口 12 说的是
  「后端一条能力门槛都没有」，前端这一处是它的镜像面。
  **`canReadDetail` 按 `role_code` 猜是一个已知代价**（`/auth/me` 不发 capabilities），
  理由与边界写在那个 `computed` 的注释里：**后端那两道门是权威**（§4：前端隐藏不是安全
  措施），它只负责不把用户带到一个必定失败的页签上。

#### 跑数与守卫

后端新增 `test_export_jobs.py`（17 条）、`test_auth_sessions.py`（13 条）、
`test_task_participation.py`（**17 条**，其中 4 条是第十个端点的）与
`test_sensitive_reads.py`（3 条），`make test` 649 → **707 passed / 0 failed /
335.90s**；`make e2e` 120 → **122 passed (28.2s)**。

**这两个是收口完成那一次的实测数。缺口 12 加门之后又实测了一次，是
`make test` **708 passed / 0 failed / 400.39s**、`make e2e` **123 passed (34.5s)**——
以这一组为准。** 多出来的三条用例是那一道门自己的：`test_permissions.py` 里那条提级用例
（§上面那张表里 `test_the_completion_gate_is_a_capability_not_a_hardcoded_role`）、
`test_task_roles.py` 里那条**改写**过的领导用例（它此前断的是「领导读得到完成明细」），
以及 `e2e/app.spec.ts` 那条领导侧的弹层用例。**耗时那一项 335.90 → 400.39 秒差了 65 秒，
而用例只多两条**——原因没有查（同一台机器上的其它负载、MySQL 当时的冷热都会影响它），
所以那一列只当参考，别拿它比版本。

这一节此前记的 699 是第十个端点刚落地那一次的数——**三个都真、只是时点不同**：收口期间
又补过守卫（`test_permissions.py` 那条撤销用例、镜像测试的展开自证等）。而这条本身也是
§19 那条教训的又一次：一个「看起来像设过、其实没人再测过」的数，光看它对不出来。
**每次动完都重跑、按实测改，别照着上一版抄。**

**另外记一次 e2e 的红，它不是这一期引入的、也没有修**：上面那次全量 e2e 的**第一遍**里
`量表评分规则 › saving a published rule versions it rather than mutating in place` 也红了
一次（同一遍里我自己那条新用例因为定位器撞上严格模式而红，是另一回事）。单独跑那一条
**绿**，改完定位器重跑全量**123 passed 全绿**。所以它是一次没有复现的偶发，**没有查到
原因**；写在这里是为了下一个人再撞上时知道它有过一次，而不是把它当成「已经修好了」。

**第十个端点那 4 条里有一处夹具是被撞出来的**，值得记：`_csv_rows` 用 `csv.reader`
而不是 `line.split(",")`——「说明」那一列里嵌着服务端拼的**自由文本**（含中文顿号与
分号），按逗号切会把一行切成七八列而断言随后报「找不到第 7 列」。

守卫里三条值得单独记：

| 用例 | 钉住什么 |
|---|---|
| `test_the_parent_row_is_updated_before_the_session_row_is_inserted` | 上面那个 1213 死锁的形状（**先断言两条语句都发出去了**，再断言次序） |
| 过期 / 撤销的下载必须失败 | 两种状态回**不同的** 410，措辞分开（§2 那条的另一处） |
| sha256 对不上时拒绝发送（`Path(row.file_uri).unlink()` 之后） | 文件被人动过 / 被删掉时不发一份说不清来路的文件 |

**变异验证**：把 `authenticate` 里那一行 `db.flush()` 摘掉 →
`test_auth_sessions.py` **1 failed / 12 passed**（**恰好是那一条**），随后用 `cp -p` 的
落盘备份 `cmp` 逐字节还原（§18 那条：变异验证的复原校验必须拿落盘的原始字节当基准，
不能是内存里再编码一遍的字符串）。

#### 本期留下的三处，记在缺口里

1. **`var/exports/` 下的文件没有任何东西删。** `purge-demo` 会删 `export_job` 的**行**
   （只删演示账号名下的那些），但**磁盘上那些文件留着**——而它们已经没有任何行指得着了。
   生产上同理：过期是库里的结论，文件本身不会被回收。缺的是一个清理动作（按 `expires_at`
   扫、删掉到期且已被撤销/过期的文件），而它需要一个「谁来跑」的答案（计划任务？启动时？），
   所以没有顺手加。
2. **e2e 每跑一次就往 `auth_session` 里加一百多行。** 每一次 `loginAs` 都登一次，
   而登出不是每条用例都做。它们不影响任何断言（会话列表只列自己的、导出不读它），
   但共享的开发库上这个表会单调增长。与「新建账号那条用例留下的临时账号」（§测试注意）
   是同一类**已知且接受**的残留——区别是那个看得见、点得掉，而这个要去库里删。
3. **`verification_status` 仍然没有读者**（缺口 11 未变）。阶段 8 没有碰它——四档处置
   那一列的唯一读点还是审计的 `detail`，而那一串 `detail` 仍然只有数据库看得见。

### 30. 手工数据库增量升级：停在 V1.0.0 的库怎么升上来（2026-09-20）

**这一期不是功能，是一条交付路。** 客户的库停在 V1.0.0（`alembic_version = 0012`），
而程序已经到 1.1.2——中间是六条迁移。一键安装包本来就会跑（升级模式的第 4 步），
但那两条路各自会断：

- 「只想先把库升上去，程序文件过一会儿再换」；
- 「后端起不来，想单独确认库到底升上去了没有」；
- 「那台机器根本起不了 Python，只有一份能执行的 SQL」。

所以产物是**一条路加一枚按钮**，做的是同一件事（同一组迁移、同一个版本戳）：

| 路 | 谁用 | 动作 |
|---|---|---|
| 一键安装（**推荐**） | 装得上 | 新包覆盖 → 双击「一键安装.bat」→ 第 4 步跑迁移 |
| `数据库增量升级.bat` → `manual-migrate.ps1` | 装过的机器上，只想升库 | 双击，不起服务、不碰程序文件 |
| `backend\sql\upgrade_from_v1_0_0.sql` | 目标机没有 venv / 起不了 Python | 拿这个文件到别处 `mysql < 它` |

三条路都**只对 V1.0.0 的库跑一次**。

#### 那份 SQL 是**生成的**，不是手写的（★ 谁也不许手改它）

`deploy/build_migration_sql.py` 从**两份既有来源**现渲染：链上每条迁移的 `PRECHECKS`
**常量本身**（按文件路径 import 出来）+ `alembic upgrade 0012:head --sql` 的离线渲染。
它在这里一个字的 SQL 都不重写——**再抄一份就等于开出第二个出处**，两份会在某次改迁移
之后各说各话，而它们看起来都对。所以它与 `schema_mysql8.sql` 同一个性质：快照，不是来源
（§16 那张表现在是三行）。

- 生成：`make db-upgrade-sql`，**出包时也跑**（每次都重新生成，不复用仓库里那份）。
- 落两处、同一个文件名：`backend/sql/`（随 `backend/` 进包）与 `dist/`（拿给执行的人）。
- 守卫 `test_incremental_upgrade_sql.py`：逐字节比对「盘上那份 == 今天这棵树渲染出来的
  那份」，红了就重跑 `make db-upgrade-sql` 并把那份文件一起提交。
- **不能手改**：改了下次重跑就没了，而且守卫会红。

#### ★ 每个迁移拆成【检查】/【DDL】两半，交错排列

第一版是两段式（13 条检查全在最前面），**在客户的库形状上第一条就死**：

```
SELECT id, student_id FROM assessment_session WHERE school_id IS NULL LIMIT 5;
→ (1054, "Unknown column 'school_id' in 'where clause'")
```

`assessment_session.school_id` 是 **`0013` 才加上的列**，而 `0014` 的检查问的正是
「`0013` 的回填做干净了没有」。**这类检查在 `0013` 的 DDL 跑完之前根本执行不了**——
真实迁移链的形状也是这个。所以现在是「迁移 1/6 的检查 → 它的 DDL → 迁移 2/6 的检查 →
…」。**别改回两段式。**

「动手之前先看见结果」这条没有被削弱：`0014` 那 12 条检查仍然全部排在 `0014` 的第一条
DDL 之前（迁移文件里就是这么写的），而 `0013` 是**只加不改**的、没有可能失败。

#### ★ 检查语句不会让 `mysql` 停下——每条后面必须有一次 `CALL`

同一次真库验证里撞出来的第二件事，比第一件严重。反向验证是这样做的：在一个停在 `0012`
的库上塞一行 `risk_type` 认不出的 `risk_event`，再跑那份脚本，**期望被拦下**。实际是：

```
id      risk_type               trigger_rule
1       NOT_A_REAL_RISK_TYPE    r          ← 检查确实认出来了，也打出来了
ERROR 1048 (23000) at line 581: Column 'requires_manual_review' cannot be null
```

**检查打出了那一行，然后脚本继续往下跑了一百多条 DDL**，直到某条 `ALTER` 撞上一句与
真正原因毫无关系的英文错误；库最后是 35 张表、版本戳还停在 `0012`——正是「改了一半」。
根因一句话：**检查是 `SELECT`，而 `mysql` 客户端不会因为你看见了结果就停下**（那句
「有结果就停下来反馈」是写给**人**的），而 MySQL 的 DDL 不在事务里。

出路是每条检查后面配一道**机器**能过的门：原样打印那句 SQL 给人看，紧接着
`CALL xlp_check_empty(<同一条 SQL>, '…')`——有行就 `SIGNAL SQLSTATE '45000'`，当场中断，
**那时一行 DDL 都还没跑**。操作员看到的 `ERROR 1644 (45000)` 是一句中文，不是英文列名。

三个细节：

- **`SIGNAL` 只许出现在复合语句里**，所以那个存储过程是唯一能做条件中断的东西。它要
  `CREATE ROUTINE` 权限（root 有），建不出来时 `mysql` 停在那一行——**那是安全的一侧**，
  因为它什么都没执行。首尾各一句 `DROP PROCEDURE IF EXISTS`：收尾那句在被 `SIGNAL`
  中断时跑不到，所以开头那句顶着。
- **名字带 `xlp_` 前缀**：它在对方的生产库里只活这一趟，而一个叫 `check_empty` 的存储
  过程留在那儿会让人以为是他们自己的东西。
- **检查写成 `SELECT EXISTS(<子查询>)`**：派生表会在**重复列名**上撞 1060，`COUNT(*)`
  会在带 `GROUP BY` 的检查上撞 1172（返回多行）。`EXISTS` 一律返回一行一列。

#### `is_offline_mode()` 那两行

`0013` / `0014` 的 `_precheck()` 开头各有一句 `if context.is_offline_mode(): return`。
`--sql` 模式下 `op.get_bind()` 回的是 `MockConnection`，它的 `execute()` 返回 `None`
→ 下一句 `.fetchall()` 当场 `AttributeError`，那份 SQL 根本渲染不出来。**跳过不是放松
校验**：真正的检查由 `PRECHECKS` 常量表达，生成器把它提升到那份文件的【检查】段里
（唯一出处仍然只有那一处，两个 `docstring` 都写着这句话）。

#### ★ 出包里 `3/6` 的次序不能颠倒，而颠倒了不会报错

`step("3/6 生成数据库增量 SQL")` 排在 `copy_backend`（4/6）**之前**：它落在
`backend/sql/` 里，而整份拷贝是它进包的**唯一**途径。反过来**不会报错**——`build()` 照样
写那两个文件、自检照样过（它只问「这个路径在不在」），而包里那一份是**上一次**生成的。
这是那一节唯一会静默失效的地方，注释就写在调用点上。

#### ★ 守卫不许调 `build()`——它是「先写后比」

`build(output_dir)` 会**先写** `backend/sql/<名字>`、**再写** `output_dir` 那一份。所以
「拿 `build(tmp_path)` 生成一份、再与盘上那份比」是**恒真**的：它在比对之前已经把那两份
变成了同一串字节。那不是一条会红的守卫，是一条**永远绿**的守卫，而它看起来完全像是在
证明完整性——§29 那条（一条恒绿的守卫比没有更糟，它占着「这一条有人守」的位置）在这里
换了第二次皮。

所以守卫走 `compose(...)` 这个**纯函数**，自己拼字符串，一个字节都不落盘。并且另有一条
`test_the_comparison_is_not_vacuous` **在同一次运行里**自证：检查段的每条提示语都逐字
出现在文件里，且**在内存里**改一下 `blocks` / `prechecks` 会让 `compose` 的输出跟着变
（少了它，一个 `return (SQL_DIR / OUTPUT_NAME).read_text()` 的 `compose` 会让两条一起绿）。

它**守不住什么**，写在模块 docstring 里：那份 SQL 在真 MySQL 8 上跑不跑得动（那要靠
**真库**——本文里「真机」指那台 Windows、`make test` 够不着，而这一件在一台装着
MySQL 与 `mysql` 客户端的机器上就能重跑，见本节最后那张表），以及包里那一份（它由出包
脚本**当场重新生成**，不是拷这个快照，两条路各自成立、互不代替）。

#### `manual-migrate.ps1` 做的是第 4 步那两步，三步

```
python -m app.db.ensure_schema      ← 校对表结构（手工建的表在这里盖章）
python -m alembic upgrade head      ← 执行迁移
python -m app.db.ensure_schema      ← 再校对一遍，并由它念出最终的版本戳
```

**次序两头都不能换**（§18 那一节记着理由：手写的建表语句里没有 `alembic_version`；
换了程序没迁移则是「登录页打得开、一操作就 500」）。**第三步不是走过场**：屏幕最后那行
`alembic_version = …` 是 `ensure_schema` **自己念出来的**，不是脚本拼的——拼出来的那句
没有任何东西保证它是真的。

它**不碰程序文件、不注册计划任务、不写 `runtime\build.json`、不设管理员密码**（与
「升级不动你已经配好的东西」同一条），并且**在真机上跑不动时自己会说**：读不出版本号
时 WARN、`backend\sql\` 里那份不在时 WARN（那说明这个目录里的程序文件**还是旧版**，
而旧树跑迁移会说「无事可做」、屏幕上看着像成功）。**这个陷阱没有别的机器判据**——旧树
的 `ensure_schema` 拿旧模型比旧库也会说对得上——只能靠把版本号念出来给人看。

编码与调用三件套（`.ps1` 带 BOM、`.bat` 纯 ASCII、Python 输出走
`Start-Process -NoNewWindow` 不经管道、`ContainsKey` 而不是裸读键）全部照 §18 的既有约定，
`test_windows_assets.py` 的扫描列表里加了 `manual-migrate.ps1`——**每个「点了会叫一个
按钮名」的新 `.ps1` 都要加进那张表**，否则最新那个脚本是唯一不受保护的一个。

#### 版本号 1.0.0 → 1.1.2

`backend/app/version.py` 的 `__version__` 与唯一的镜像 `frontend/package.json` 一起改，
`VERSION_LABEL` 自动变 `V1.1`（§19）。`test_app_version.py` 里那条「标签丢掉修订号」
的断言也跟着改成 `V1.1`——**字面量随 `__version__` 变是有意的**：写死一个不随它动的
期望值，`[:2]` 被误写成 `[:3]` 时就抓不住了。顺带把两处「形如 `V1.0` / 不是规范的
`1.0.0`」的注释改成「形如 `V1.1` / 形如 `1.1.2`」：那不是版本声明，是例子，写死了每次
升版本都要来改它。

#### 跑数与守卫

`make test` **727 → 730 passed / 0 failed / 393.64s**（新增的三条就是
`test_incremental_upgrade_sql.py` 那一个文件）；`make e2e` **127 passed**——本期没有
e2e 用例：升级那三条路都不经过浏览器（`/tmp` 那份 SQL 与两枚 `.bat` 归
`test_windows_assets.py`，跑的那一步只有真机能判）。
`test_windows_assets.py` 里改的是**已有的**用例（`manual-migrate.ps1` 加进那份脚本名单），
`test_app_version.py` 改的是一条断言的字面量，两者都不增加用例数。

**变异验证 2/2，两条各自红的正是它该红的那一条**（`cp -p` 落盘备份，改完 `cmp` 逐字节还原）：

| 变异 | 位置 | 结果 |
|---|---|---|
| M1 改了迁移、忘了重跑生成器（只改 `0014` 里一句 `PRECHECKS` 的提示语，DDL 一个字不动） | `0014_v12_enforce.py` | `test_the_snapshot_is_what_todays_migrations_render` **1 failed / 2 passed** |
| M2 把 `compose` 换成「读盘上那份再返回」（也就是 `build()` 的写法） | `build_migration_sql.py` | `test_the_comparison_is_not_vacuous` **1 failed / 2 passed** |

M2 那一条值得单独看一眼：它是 **`1 failed` 而不是 `3 failed`**——**那份逐字节比对的主用例
仍然是绿的**。一个「先把两份写成同一串字节、再比一次」的实现，在主用例下完全看不出问题；
抓住它的是那条**自证**用例。这就是「一条恒绿的守卫比没有更糟」那句话的可执行形式，
也说明那第二条用例不是重复劳动。

**这一期唯一一处只有真库才知道的东西**（与 §18 那三件同类）：那份 SQL 在**同一台
MySQL 8.4.4 上两个方向各跑过一遍**，走的都是操作员会走的那条路——
`mysql --default-character-set=utf8mb4 <库> < 那份文件`：

| 方向 | 库的起点 | 实测结果 |
|---|---|---|
| 正向 | 停在 `0012` 的空白库 | `EXIT=0`，版本戳到 `0018_row_conflict_resolution`、**35 张表**，收尾时存储过程已删掉 |
| 反向 | 同一个库上先塞一行 `risk_type = 'NOT_A_REAL_RISK_TYPE'` | **`ERROR 1644 (45000) at line 103: 检查 [1/1] 未通过`**，`EXIT=1`——而库**一个字没动**：仍是 **25 张表**、版本戳仍是 `0012`、`assessment_session` 上没有 `school_id` 那一列 |

**反向那一趟才是这个文件存在的理由。** 「检查把那行打出来了」与「脚本因此停下」是两件
事（上面那一节记的就是它们之间曾经什么都没有），而缝上它们的是 `CALL xlp_check_empty`：
产物里 13 条检查各配一次 `CALL`（`[1/1]` 是 `0013` 的，`[1/12]`–`[12/12]` 是 `0014` 的），
而第一条在 **103 行**——**每一条 DDL 都排在它后面**。

**这两件事 `make test` 一件都证明不了**：那三条用例只读文件、一个字都不连库
（它们能挡的是「有人改回去」，不是「它在真库上成立」）。所以上面这张表的每一格都只能
重跑一次才有——`DROP DATABASE` 一个验证库、`alembic upgrade 0012_drop_care_case_unique`
建到起点、然后跑那两趟。**别照抄这张表去断言，也别把它读成「已经有人替这次改动验过了」。**

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
   - ~~**两个入口都要接**：`DataCenterPage.vue` 与 `OrganizationPage.vue` 各有一份学生导入。~~
     **2026-09-19 更正：只剩一个入口**（`OrganizationPage.vue`）。数据中心那一份是死代码
     （§4：`v-if="isAdmin"` 与那一页的 `meta.role` 对不上，对任何角色都不显示），2026-09-17
     已删。所以「从另一个入口进来的管理员拿到一句 422」这个形状**今天不成立**——但
     「冲突必须有地方拍板」这条要求本身还在，只是它现在只有一个落点。新增第二个入口时
     那条要求会立刻回来。
   审计的 `detail` 记 `resolution` 的**中文**（`student_resolution_label`）；`None`
   （文件里没有冲突）与「选了放弃」必须长得不一样，否则事后读轨迹的人会以为那次导入
   把冲突默认放行了。
3. ~~`make test` 用**内存 sqlite + ORM metadata 建表**（`tests/conftest.py`），**完全不跑 Alembic**。
   模型与迁移漂移不会被测试发现。~~ **2026-09-19 关闭**，见下面那一节。
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
   **2026-09-19 补：这一列现在成了全站唯一已知存在的裸枚举渲染**，而且它的成因比原来记的多一条。
   起因是 `vocabulary.spec.ts` 报了一次假阳性（`/admin/audit → STUDENT`）：审计页只显示最新 20 行，
   而 `app.spec.ts` 的学生导入用例确实写进 `resource_type=STUDENT` 的审计，两个文件又通过
   `playwright.config.ts` 的 `fullyParallel` **并发**运行——于是红不红取决于那 20 行里此刻有没有
   它。查审计表确认那一行就写在那次运行的时间窗里，**与代码无关**。处置是把 `STUDENT` 从
   `UNTRANSLATED_CODES` 里移除（它在界面上没有别的渲染点，那条清单项是零覆盖，见 §24）。
   所以这一处「不能顺手改」除了原来那条理由（改了中文，用户照着提示搜会搜不到），还多一条：
   **它同时被另一个 spec 的数据写入牵着**，改它之前先想清楚那 20 行的守卫怎么办。
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

9. **「列有了、路还没通」这一类，对齐阶段留下的六条，其中五条已在功能层的第 1 / 2 / 7 期
   关闭**（下面用删除线标出）。剩下的**一条**是**已知且接受**的，由用户定的范围划出来的，
   不是漏掉的；它自己写着「为什么还不能改」。

   - ~~**`care_service.reopen_case:521` 撞新的唯一键会给用户一个 500。**~~ **2026-09-19
     第 7 期关闭**：它把 `status` 无条件设成 `FOLLOWING`，而 V1.2 起
     `student_care_case` 上有 `uq_care_case_one_active_per_student`（生成列
     `active_student_id` = 非 CLOSED 时的 `student_id`，否则 NULL）。一名学生**先关掉旧
     档案、后来又有一条在办**，此时点「重新打开」那条旧档案，两次写入落在同一个
     `active_student_id` 上 → `IntegrityError` → 500。这与 §1 记的「秋季关档、春季再关
     一次」是同一个时序，只是换了个入口。0014 的 precheck 把「库里已经有多份在办」挡在
     迁移之前，挡不住**迁移之后**这个动作。**处置正是当时写下的那个修法**：先查在办的
     那条，有就 409 并把**编号说出来**（§28）。
   - ~~**`assessment_service.open_or_reuse_care_case:617` 是一段读-改-写，没有锁。**~~
     **2026-09-19 第 7 期关闭**：它先 `SELECT` 在办档案、找不到才 `INSERT`，两个请求同时
     到达时两边都查不到、两边都插入 → 撞同一个唯一键。**在 V1.2 之前这条路上没有约束**，
     所以它此前是**静默地建出两份在办档案**（比现在更糟，只是看不出来）。**处置选了
     savepoint + 捕获 `IntegrityError` 后重查，不是 `SELECT … FOR UPDATE`**——这里根本没有
     行可锁（要处理的恰恰是「不存在、于是两个人一起插」），理由写在那个函数的 docstring
     里（§28）。
   - **导入路径不走 `uq_session_effective_task_student` 那套判重**（**2026-09-19 第 4 期
     收窄了一半**）。判重现在是 `_existing_session_for_match` **两层并存**（用户裁决
     「两层都留」，两套判据回答的问题不同：按任务的问「这一场我导过没有」，按月的问
     「这个月学校那次普查我导过没有」）：
     - 批次**绑定了任务**时，它查这场任务里这名学生**全部**的会话（不分来源），落到
       学生的会话上时按 `source` 分成 `DUPLICATE`（外部平台导的，同一份又导一遍）与
       `CONFLICT`（学生自己在本系统答的）——**「一次外部导入与一次校内作答落在同一场
       任务里」这个组合，在这一支上已经问过对方了**；
     - **没绑任务**时仍按自然月（缺口 8，用户 2026-09-17 要的「不同月份的评测视为不同
       的测试任务」）。
     仍然没有合并它们，因为合并要先决定「同一场任务里，两来源的分数谁算数」——
     那是业务问题（`TODO_BUSINESS_CONFIRMATION` §16.10）。而且这两支都只是**匹配时
     的一次查询**，不是数据库约束：唯一键那两道门（`uq_session_task_student_attempt` /
     `uq_session_effective_task_student`）仍然不认识导入路径，真正并发到达时兜底的是
     它们，代价是一句英文的 `IntegrityError`。
   - ~~**两个「诚实的默认值」现在是恒定的**~~ **2026-09-19 第 2 期关闭**：
     `tested_at_source` 由两条写入路径各写一处（在线交卷 `ONLINE_SUBMIT`、导入
     `IMPORT_FILE`），`calculation_status` 由 `score_session` 的三步状态机写，
     `ix_session_calculation_status` 于是不再筛出全集。**两个默认值本身一个字没改**——
     `PENDING` / `PENDING_VERIFICATION` 仍然是「不知道」的诚实取值，只是现在多了
     「知道的那一条路」会覆盖它。**历史行仍然是常量**（0015 只回填 `calculation_status`，
     见 §23）：那批行的 `tested_at` 与哈希至今为空，而这个缺口说的是「列没有写入方」，
     不是「历史数据没法补」——后者是业务规则，仍然没有答案。
   - ~~**`assessment_target` 的六列快照全都没有写入方**~~ **2026-09-19 第 1 期关闭**：
     七个快照列现在由 `services/target_snapshot.py::target_snapshot` 一处定义，四个写入方
     （建任务发放 / 补发 / 导入收行 / `seed.py`）都走它，`list_task_targets` 按快照读、
     名册只兜底。见 §22。
   - ~~**`student_care_case.case_version` 现在恒为 1。**~~ **2026-09-19 第 7 期关闭**：
     它是需求说明书 §14 的乐观锁（状态变更要带上读到的版本号、改完 +1），而对齐阶段只加了
     列、没有 `service` 读它。现在三处写入口都带上版本号——关闭 / 重新打开 / 转派，
     由 `_check_case_version` + `_bump_case_version` 成对实现，且两个请求模型把它声明成
     **必填**（一个可选的乐观锁等于没有锁）。它与上面那条 `open_or_reuse_care_case` 的
     读-改-写是**同一类洞的两种解法**：一个用唯一键兜底，一个用版本号（两处现在都有人接）。

10. **汇总档（`EXTERNAL_SUMMARY`）在按 `assessment_result` 说话的页面上是空的，
    而任务完成率会把它算成已完成。** 第 5 期让汇总文件能导进来了（§26），而它
    **一行答卷都不写**——这是规格逐字要求的（`没有 100 道原始答案时不得伪造
    assessment_answer`），代价是三处不一致（来源不止一个，见下）：

    | 症状 | 为什么 |
    |---|---|
    | 关注等级 / 关注率 / 受控导出里这一场是空的 | 那些页面读 `assessment_result`，而汇总档不评分 |
    | 任务完成率算它已完成 | 完成率按**目标行**算，而行确实写进去了 |
    | 会话 `status` 停在 `IN_PROGRESS` | `_write_sheet` 早退之后没有东西把它推到 `SUBMITTED` |

    **第 6 期的四档处置（§27）没有关掉它们，而且这是对的。** 四档只对
    `MATCH_CONFLICT` 那些行生效（学生自己在本场答过、文件里也有他），而一份汇总档
    匹配到一个**没有在线答卷**的学生时 `conflict_choice is None`——四档碰不到它。
    §26 那一版把这三处归给「阶段 6」，那句话是错的：它假设「有效结果」这一层会在第 6 期
    接上，而第 6 期接的是**哪一场会话有效**，不是**一份只带分数的外部结果怎么进结果表**。

    真正的缺口在这里：`assessment_target.effective_session_id` 与
    `effective_external_result_id` **今天都只有写入方、没有读者**（`grep` 数得出来：
    各一处写，其余命中全是索引定义与类型注解）。前者不构成缺陷——「哪一场有效」由 §18.8 那个
    谓词回答，读者读的是会话上那一列；而**后者正是这三处要接的那一层**：
    「这一场的外部平台分算不算一份有效结果，算的话它的等级从哪来」。
    平台给的分数一直留在 `assessment_external_result.total_score` /
    `dimension_scores_json` 上，**没有丢**。

    **接它之前不要「顺手统一」**：把汇总档也塞进 `assessment_result` 就正是那句话禁止的
    「替未核验的外部结果担保」，而它会让一个未经核验的分数直接进关注率。完成率那一处
    （`task_service.task_completion`）排在**阶段 8 的 §18.10 收口**；而「一份汇总档的
    有效结果怎么读」在那 8 期里**没有落点**——它要先回答 §16.10 的业务问题（外部平台的
    总分按哪套分段判等级），所以不要自己定。`_write_sheet` 的 docstring 里点着名等这一节。

11. **`assessment_external_result.verification_status` 只有写入方、没有读者，而
    `KEEP_ONLINE` 与 `REJECT_EXTERNAL` 的差别全在它上面。** 第 6 期（§27）让四档处置
    各自把这一列推到一个值，而全库**没有一处读它**——`grep verification_status` 数出来的
    是三处写（建行时的 `PENDING`、处置时按 `CONFLICT_RESOLUTION_VERIFICATION` 取值、
    `_apply_external_result` 落列）、一个索引、**零处读**。后果是具体的两件：

    - **`KEEP_ONLINE` 与 `REJECT_EXTERNAL` 在所有界面上长得一模一样。** 它们在建不建
      会话、谁有效上完全一样，唯一的差别就是这一列——而它在库里之外只出现在审计的
      `detail` 里，那一串 `detail` 今天**只有数据库看得见**（§8：`GET /audit-logs` 不发
      `detail`）。所以「那两条在线答卷当时是按哪一档裁的」这个问题，界面上答不出来。
    - **「已经否了的那一份，下一次上传时按什么口径出现」没有答案。** 代码注释里写过的
      「后者不该在下一次导入时又被问一遍」是一句**意图**：重传同一个文件时
      `_clear_batch_rows` 把这一批的行与外部结果**全删了重建**（批次按指纹复用，
      **行不复用**），于是人逐行做过的处置（`conflict_resolution` / `resolution` /
      `age_resolution` 与 `resolved_by` 那个「有几行是人逐条看过的」的数）**跟着一起没**，
      而操作员看不出这件事发生过。

    两条出路分开，都还没有排期：让这一列有个读者——第一件要做的是让 `detail` 可查，
    那要动 §8 那条序列化（它同时决定「登录失败」与「查看了谁的档案」该不该受权限约束）；
    以及让逐行处置在重传时**留下来**（行按指纹复用，或重传时把处置带过去）。在那之前，
    这一列的诚实读法是「它记着学校对这份外部结果的态度，但今天没有任何东西按它办事」。

12. ~~**完成明细（`GET /assessment-tasks/{id}/completion` 与 `POST …/completion/export`）
    一条能力门槛都没有，而它的载荷里同时有身份列与等级列。**~~ **2026-09-20 关闭**
    （用户裁决：「缺口12 加一道门」），加的是 `STUDENT_PSYCH_DETAIL: {SCOPED}`。
    下面这一段是**发现时的原样**，留着当这条缺口的定义；关闭记录在它后面。

    2026-09-20 清点 §20 十六条
    时撞出来的，**实测过**（探针跑完即删）：德育领导（`STUDENT_PSYCH_DETAIL` 按 §4 是
    `SUMMARY`，词汇表说它「只是聚合」）在同一个响应里拿到

    ```
    学号 S001 · 姓名 林同学 · 班级 1班 · total_level='GENERAL_RANGE' · total_score=1
    ```

    ——**逐人的等级**，而 `SUMMARY` 恰恰不是逐人明细。两条路径都缺门：

    | 路径 | 今天的门槛 | 载荷 |
    |---|---|---|
    | `GET …/completion` | `require_role(*TASK_READERS)`（只有这一道） | 身份六列 + `total_level` / `total_score` |
    | `POST …/completion/export` | 同上，**连 `CONTROLLED_EXPORT` 都没有** | 同上，且 `mask_level=IDENTIFIED`（学号 + 真实姓名 + 等级，进一份离楼的文件） |

    **这一条与 §4 那两条是同一个形状，而它们都已经有门了**：
    `GET /students/results`（身份列 + 等级列）要**两道**门槛，
    `non-participants/export` 与 `unmatched-import-rows/export`（只有身份列）要
    `ensure_detail_exporter`（`CONTROLLED_EXPORT` + `STUDENT_PSYCH_DETAIL: SCOPED`）。
    完成明细是**身份列与等级列都齐**的那一份，却一道没有。§4 那句「受控导出不能成为绕过
    心理详情的旁路」在这里原样成立：德育领导的 `CONTROLLED_EXPORT` 是 `PROGRESS_SUMMARY`，
    它在 `/students` 上连按行姓名都拿不到，却能从这里导出一份**实名＋等级**的名单。

    **它不是「没人发现」，是那条裁决没有问载荷里有什么。**
    `test_task_roles.py::test_the_leader_reads_completion_but_does_not_write` 明确断言
    领导读得到（2026-09-17 的裁决：任务阅读权归心理老师 + 德育领导），而它**只断了
    200、没断 `total_level` 是什么**。而 §11 记着**同一天**给完成明细加了那两列
    （关注等级 / MHT总分）——一条决定「谁读得到这一页」，一条往这一页里加逐人的等级，
    两条同一天落地，**后者落地时没有人回去问前者那句话还成不成立**。
    探针第一版之所以看着「没事」，也是同一个原因：基线种子只有 1 名学生、且他在那场任务里
    **没交过卷**，等级列是 NULL——**要先把人真的交一次卷，这一列才现形**。

    **当时没有顺手加门，因为这是一个产品的裁断，不是一处漏写的依赖。** 加门会改动一条
    2026-09-17 的裁决（并且要改那条明确钉着它的用例），而它必须与 §4 里
    「`/students/results` 对德育领导严一档」这个决定**放在一起裁**：如果完成明细该让领导
    看到逐人等级，那么同一个人在 `/students/results` 上被拦住就因为路径不同而说不通；
    如果该拦，那就得回答「领导按年级看完成率」这件事以后从哪看（现在正是靠这一页）。
    两个答案都成立，所以留给人定。

    **2026-09-20 关闭：加门。** 用户在这一天作出裁决，答案是上面那一段的后一半——**拦**。
    落点与那两条既有裁决完全同形（身份列 + 等级列**两道**门槛，见 §4），所以这一条**不是
    新口径，是既有口径漏掉的一个端点半边**：

    | 路径 | 加门之后 | 判据落在哪 |
    |---|---|---|
    | `GET …/completion` | `require_role(*TASK_READERS)` + `TaskTargetReader` + `DetailExporter` | 服务层 `task_completion` 里那两道 |
    | `POST …/completion/export` | 上面三道 + `ControlledExporter` | 服务层 `ensure_detail_exporter` 里那两道 |

    四点值得记：

    - **判据抽成 `task_service.ensure_detail_reader`（`STUDENT_PSYCH_DETAIL: {SCOPED}`）**，
      读与导出**共用同一句**。导出那一侧原来那道双查（`ensure_non_participant_exporter`）
      改名为 **`ensure_detail_exporter`** 并改成调它——名字按**问题**取，不按第一个调用者取，
      否则「未匹配行导出」会去调一个叫 `non_participant` 的函数，而下一个人会照着这个名字
      再写一份「给未匹配行用的」版本（§22 那次改名的同一条理由）。
    - **门槛放进 `task_completion` 的数据本身**，所以三个调用点（读端点、导出端点、
      参与口径那一页**不**经过它）自动全覆盖；只加在路由上就会出现「服务层还有一条路
      读得到」的形状。
    - **它按能力矩阵走，不是「领导一律不行」写死**。`test_permissions.py::test_the_completion_gate_is_a_capability_not_a_hardcoded_role`
      把领导那一格提到 `SCOPED` 之后两条都放行——一个 `if role_code == LEADER: raise` 的
      实现会在这条上变红。学校在权限页上给领导开了这一格，就该真的开。
    - **§4 那句「受控导出不能成为绕过心理详情的旁路」在这一条上第一次被单独验**：
      `test_permissions.py::test_revoking_psych_detail_also_blocks_the_task_detail_endpoints`
      把心理老师自己的 `STUDENT_PSYCH_DETAIL` 降成 `NONE`（他有 `CONTROLLED_EXPORT`），
      三份任务级明细导出各试一次。默认矩阵里**构造不出**这个组合，所以那些逐角色用例
      看不见它。

    **代价，如实记：领导失去了「逐人参与处置」的可见性。** 完成明细表里有一列「参与」
    （请假 / 免测 / 已排除的逐人标记），它是**整块 403** 挡掉的，不是前端藏起来的——
    与等级列同一条路径。领导仍然拿得到**计数**（`GET …/participation` 的六个数里含
    `excluded_targets`），失去的是「哪几个人」。这一处与「该不该拦」是同一个裁断的两面：
    那三列与学号 / 姓名在同一张表里，逐人给就等于把逐人身份一起给了。**领导看完成率的
    三条落点不变**（`GET /assessment-tasks` 每行的 `total_targets` / `completed_targets` /
    `completion_rate`、`GET …/participation` 六个数、`/leader/analytics`），
    `TasksPage.vue` 在页签收起处写明了这一句，并指明上面那六格与「目标学生」「未匹配行」
    两个页签照常可查。

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
  所以每个外键都是 RESTRICT，父行先删在 MySQL 上是必然的 1451。
  `make reset-db` 原来那段内联脚本就是这么坏的（`risk_event` 排在 `manual_review` 之前，
  而 10 行 `manual_review` 正引用着活着的 `risk_event`）——**而它当年一直没被发现**，
  因为测试库是内存 SQLite，**默认不检查外键**（缺口 3）。2026-09-19 起整条测试链在真
  MySQL 上，`tests/test_purge_demo.py` 那套自建引擎 + `PRAGMA foreign_keys=ON` 的脚手架
  因此整个删掉了：外键一直在，它对每一个用例都是这样。改删除顺序先看这个文件。
  **同一件武器现在还有第二处**：`tests/test_sql_reset_to_baseline.py` 把
  `backend/sql/reset_to_baseline.sql` 的删除顺序按 `Base.metadata` 的外键图重推一遍
  （§16）。它**不碰数据库**（静态推），所以在哪个方言下都有效——上面那个「只有真库
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
