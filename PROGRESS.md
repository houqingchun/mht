# PROGRESS.md

## 0. 元信息

- 项目：心晴·中学生心理测评与关怀平台
- 仓库：`houqingchun/mht`
- 基线版本：`V1.1.6`
- 目标版本：`V2.0.0`
- 分支：`V2.0.0`（基于 `V1.1.6` 创建，**基线 commit `08254dcbb7303c84f5430d20454d85fba6f11bcc`**）
- 最后更新：2026-09-25
- 当前会话目标：按《心晴_V1.1.6_to_V2.0.0_P0-P1功能优化实施规范_AI_Coding基线版.md》完成 P0-P1 功能编码、数据库迁移、测试与验证
- 上下文状态：正常
- 当前范围：仅 P0-P1
- 明确排除：P2、MHT评分规则修改、既有导入批次体系重构、部署方式重构、HTTPS/自动备份等运维增强

## 1. 总目标

在不重构 V1.1.6 已有稳定能力的前提下，将系统从“已可交付使用”提升为“具备正式产品化治理能力”的 V2.0.0。

本轮必须完成：

1. **P0：测评任务删除 / 作废治理**
   - 用户界面统一理解为“删除任务”；
   - 后端区分 `HARD_DELETE` 与 `VOID`；
   - 无正式测评事实的任务允许物理删除；
   - 已产生正式测评事实的任务必须作废，不得物理删除；
   - 作废后对应有效 session 必须失效；
   - 待处理筛查信号必须同步失效；
   - 人工复核、跟进、家庭回访、关怀档案等专业工作事实必须保留；
   - 默认任务列表、统计、报表、任务选择器排除 `VOIDED`。

2. **P0：数据范围显性化**
   - 心理老师页面不能再出现“页面写全校、实际只看授权年级/班级”的情况；
   - 统计页显示真实 Data Scope；
   - 德育领导显示全校范围；
   - 后端授权仍采用 fail-closed，不得依赖前端展示控制权限。

3. **P1：心理老师 / 德育领导专业报告职责拆分**
   - 心理老师：可编辑专业分析报告；
   - 德育领导：只读学校心理工作分析摘要；
   - 德育领导不得编辑心理老师专业解读。

4. **P1：专业报告服务端持久化**
   - 替换浏览器 `localStorage` 草稿；
   - 支持报告版本；
   - 支持统计快照冻结；
   - 历史报告不得因实时数据变化而漂移。

5. **P1：正式报告接入现有 Export Job**
   - 复用当前 Export Job；
   - 正式导出不得继续仅依赖 `window.print()` / Blob；
   - 导出必须受权限、用途和审计控制。

6. **P1：产品术语优化**
   - 学生端、领导端、正式报告以“关注 / 筛查”表述为主；
   - 数据库内部历史字段不做无意义重命名迁移。

## 2. 当前状态（一句话）

**P0-01 / P0-02 / P1-01 / P1-02 / P1-03 / P1-04 六项底层能力全部落地；
2026-09-25 的产品复审确认：专业报告的服务端能力已完整，但前端只接通了“新建 → 保存 →
发布 → 当前版本导出”的最短路径，尚未形成可恢复、可续写、可追溯的完整工作台。** 下一轮
进入“V2.0.0 产品收口”：§5.13 完成专业报告 P0，§5.14 再按学生、心理老师、德育领导、
系统管理员四种使用特点完成角色化 UI/UX。原 §5.7 判据（都是实测、不是声称）：

| 项 | 实测 |
|---|---|
| Alembic | 克隆库 `0020 → head` exit 0；空库 `0001 → 0022` exit 0 |
| 旧数据兼容 | 36 张表 / 15716 行；34 张既有表逐表行数与升级前相等、33 张逐字节相同 |
| Backend Tests | **839 passed / 0 failed / 492.60s** |
| 前端 build | `vue-tsc -b && vite build` 通过 |
| E2E | **144 passed (33.4s)** |

**顺带修掉两个真缺陷**（都不是规范里的条目，见 §5.8）：`reporting.py` 的
`serialize_export_jobs` 少传两个位置参数（每次调用都 500）、`purge.py` 不清理专业报告；
以及**本轮自己做出来的第三个**——`0022` 的 downgrade 先 `drop_index` 再 `drop_table`
会撞 MySQL 1553（外键正在用的索引），退回 V1.1.6 那条路上必红，已修 + 已配守卫。

**§5.9 第 5 条已裁决并落地**（2026-09-25）：新版本**重新统计快照**，不再照抄上一版。
守卫是 `test_reporting_api.py::test_a_new_version_recomputes_the_statistics_it_inherits`，
判据与两处踩过的坑写在 §5.9 那一条里。

**已推送到 `origin/V2.0.0`**：`70e654ebf519f18ff12e84d1b17a50bffa880de5`
（用户 2026-09-25 发话「2 推送」）。§5.7 全局回归那一串清单至此**全部勾满**。

**上面这三件事 2026-09-25 当天的第二轮全部落地了**（用户发话「1. 版本号升到2.0.0
2. 没懂是什么意思 3. 解决2后出包」，第 2 步是把它讲成平实的话，见 §5.10）：

| 第二轮 | 结果 |
|---|---|
| ① 版本号 → `2.0.0` | `555674b`（**独立提交**，5 个文件，含两份生成的 SQL 头） |
| ② §5.9 余下五条 | 第 **1 / 2 / 3 / 4 / 6** 条裁决并落地，落点索引见 §5.10 第二节 |
| ③ 出包 | `make deploy-package` → `dist/心晴部署包_V2.0.0.zip`（**12,093,090 字节 / 11.5 MB**，六步全绿） |

第二轮的回归实测：**Backend Tests 847 passed / 0 failed / 513.30s**、
**E2E 144 passed (33.7s)**。后端 +8 全部来自这一轮新增的三条用例组，
e2e 数不变是因为它这一轮只动了注释（唯一那次红与被测代码无关，见 §5.10 第四节）。

两条提交：`555674b`（升版本）+ `624d3d9`（五条落地）；**是否推送 `origin/V2.0.0`
仍等你发话**（第一轮那条 `70e654e` 是你说「2 推送」之后推的）。

**第三轮（2026-09-25）：规范符合性审计**（用户要求「再检查下这里的需求还有哪些没实现」）。
逐条读完 1578 行规范再对源码，结论是 **P0-P1 六项全覆盖**；未实现的只有三处——
`ARCHIVED` 档与 `LEADER_MANAGEMENT_SUMMARY` 是 §5.9 第 6 条裁决不做，第三处
（`ClassPortraitPage.vue` 的 `localStorage` 笔记草稿 `qingxin-notes`）**待你裁决**。
逐条落点表、该灰色地带的六条证据，以及顺手订正的一处文档矛盾（§5.1 那条与 §5.9 第 2 条
互斥的陈旧条目），都在 §5.11。**本轮没有改任何生产代码。**

**第四轮（2026-09-25）：给「查看明细」弹层加全屏展示**（用户原话见 §5.12）。
机制（`Modal` 的 `expandable` + `.modal-fullscreen`）是 V1.1.6 就建好的，这一轮**没有改
`Modal.vue`、没有改 `styles.css`、后端零改动**——只把这个弹层的三处行内 `max-height:340px`
挪进一个 computed（行内样式压过任何全局规则），并把全屏状态接上。新增一条 e2e
（三个页签逐个量 `maxHeight`）。实测：**E2E 144 → 145 passed (40.0s)**、
**Backend Tests 847 passed / 0 failed / 538.01s**（纯前端改动，后端只作回归确认；5 条 warning
与基线同，全是 §23 记着的那类 SQLAlchemy 笛卡尔积误报，无新增）。四处代码改动、
四条变异验证、两处踩坑（我自己用例里的一处真实竞态、以及两条 Analytics 的数据量依赖红）
都记在 §5.12。

## 3. 下一步（最重要）

0. **依次执行 §5.13 + §5.14“V2.0.0 产品收口”**（当前下一步）
   - 先完成 RPT-UX-01～06，不先做视觉翻新；
   - 复用已存在的报告列表/详情/新版本/按版本导出 API；
   - 报告 P0 全绿后，再按 §5.14 完成四角色 P1；
   - 最后完成班级笔记、图标和设计系统一致性；
   - 当前工作区有未提交改动，开始编码前必须逐项确认并保留。

1. ✅ **确认并建立 `V2.0.0` 分支基线**（2026-09-25 完成）
   - 确认当前分支是否确实由 `V1.1.6` 创建 → **是**；
   - 记录基线 commit SHA → `08254dcbb7303c84f5430d20454d85fba6f11bcc`；
   - 禁止直接在 `V1.1.6` 上开发 → 已满足（`V1.1.6` head 未动）。

   四条实测判据（都在仓库根跑）：

   | 判据 | 实测值 |
   |---|---|
   | 当前分支 | `V2.0.0` |
   | `git merge-base V1.1.6 V2.0.0` | `08254dcbb7303c84f5430d20454d85fba6f11bcc` |
   | `V1.1.6` head | `08254dc` V1.1.6：导入明细服务端筛选 + 弹层全屏展示 |
   | `git rev-list --count V1.1.6..V2.0.0` | `0`（分支尚无自己的提交） |

   **merge-base 等于 `V1.1.6` 的 head**，所以「由 `V1.1.6` 创建」不是一句声称，是一条
   可复核的事实。分支尚无提交 ⇒ 全部 V2.0.0 改动仍在工作区（43 修改 / 1 删除 /
   10 未跟踪），基线是干净的。

   **`origin/V2.0.0` 尚未创建**（远端只有 `V1.1.1`–`V1.1.6`）。推送分支是对外动作，
   等需要时再做。

2. **优先完成 P0-01：测评任务删除 / 作废治理**
   - 新增 Alembic migration；
   - 为 `assessment_task` 增加作废字段；
   - 增加 `VOIDED` 状态；
   - 实现 `delete-check`；
   - 实现 Hard Delete；
   - 实现 VOID；
   - 作废相关 session；
   - 处理 PENDING risk event；
   - 保留人工专业工作记录；
   - 补齐任务列表、统计、报表、下拉框的 `VOIDED` 过滤；
   - 完成 Backend Tests + E2E。

   **进度：全部落地**（2026-09-25）。逐条勾选见 §5.1；那一组 e2e 最终写成 **3 条**
   用例覆盖规格列的 7 个场景（映射与两处「刻意不断」的理由写在用例组头那段注释里），
   变异验证 4/4 变红。

3. **P0-02 数据范围显性化 — 已全部落地**（2026-09-25）
   - 数据范围摘要复用 `/auth/me` 的 `scopes[]`（**没有**新增 `data-scope-summary` 端点，
     理由见 §5.2：`scopes[]` 一直在响应里，缺的只是前端类型与读者）；
   - 四个 analytics 视图共用 `ReportPageHeader.vue` 上的范围徽标；
   - 「全校」硬编码已去除，口径取服务端的 `scopes[]`，取不到时整句不出现；
   - SCHOOL / GRADE / CLASS 三档各有用例（后端 10 条 + e2e 2 条）。

4. **P1 四项 — 已全部落地**（角色拆分 / 服务端持久化 / Export Job / 术语统一）
   - §5.3 P1-01 报告角色拆分 ✅（两个独立页面 + 三条能力码）
   - §5.4 P1-02 服务端持久化 ✅（两张表 + 七个端点 + 快照冻结）
   - §5.5 P1-03 Export Job ✅（`PROFESSIONAL_REPORT` 类型，走既有的导出中心）
   - §5.6 P1-04 术语统一 ✅

5. **§5.7 全局回归 — 全绿**（2026-09-25，逐项实测见 §5.7）

6. **等你的事**（都不是编码动作）
   - **§5.9 那六条裁决里的五条**：规格没给答案、自己定就等于猜，所以一条都没自行决定；
     第 5 条（新版本要不要重算统计快照）**2026-09-25 已由用户裁决为「重算」并已落地**，
     见 §5.9；
   - **提交并推送到 `V2.0.0` ── 已完成**（2026-09-25，用户发话「2 推送」）：
     commit `70e654e`，`git push -u origin V2.0.0` 建出远端分支。执行结果与四条判据
     见 §5.7 最后那一格。
   - **要不要把 `__version__` 升到 `2.0.0`**：见 §5.7 末尾那一段——本轮**没有**动它
     （升版本在这条线上一直是独立提交，而规范只写了「目标版本 V2.0.0」）。

## 4. 已完成

- [x] 已确认源码仓库：`houqingchun/mht`
- [x] 已确认当前开发基线：`V1.1.6`
- [x] 已确认目标版本：`V2.0.0`
- [x] 已完成 V1.1.6 源码级功能核验
- [x] 已确认 `assessment_task.source` 已区分 `IN_SYSTEM` / `IMPORTED`
- [x] 已确认现有外部导入批次体系已经存在：`assessment_import_batch` / `assessment_import_row` / `assessment_external_result`
- [x] 已确认禁止重复建设第二套导入批次模型
- [x] 已确认 `_task_for_month()` 已实现同学校、同测评月份的 IMPORTED task 复用
- [x] 已确认 V1.1.6 当前无正式任务删除 / 作废接口
- [x] 已确认仅隐藏任务不足以解决数据正确性问题
- [x] 已确认“当前有效结果”依赖 `assessment_session.is_effective`
- [x] 已确认任务作废必须影响 session 有效性
- [x] 已确认外部导入与在线测评均可能触发 `risk_event`
- [x] 已确认人工复核 / 关怀档案不得随任务删除而删除
- [x] 已确认现有 Analytics 使用数据范围过滤
- [x] 已确认心理老师部分页面标题仍存在“全校”硬编码问题
- [x] 已确认 counselor 与 leader 当前共用 `ReportExportPage.vue`
- [x] 已确认当前专业报告草稿使用 `localStorage`
- [x] 已确认当前正式报告页面仍使用 `window.print()` / Blob
- [x] 已确认系统已有 Export Job，应复用现有能力
- [x] 已冻结本轮范围：仅 P0-P1
- [x] 已明确排除 P2
- [x] 已输出《心晴_V1.1.6_to_V2.0.0_P0-P1功能优化实施规范_AI_Coding基线版.md》

## 5. 待办

### 5.1 P0-01 测评任务删除 / 作废治理

#### 数据库 / Alembic

- [x] 新建 Alembic migration，禁止修改历史 migration（`0021_v2_task_governance.py`，**只新增**）
- [x] `assessment_task` 增加 `voided_at`
- [x] `assessment_task` 增加 `voided_by`
- [x] `assessment_task` 增加 `void_reason`
- [x] 支持 `assessment_task.status = VOIDED`
- [x] 评估 `risk_event` 是否需要 `voided_at / voided_by / void_reason`（**需要**，同批加上）
- [x] 模型、Alembic、SQL 快照/结构测试保持一致（三份 `backend/sql/*.sql` 已重渲染）

#### Delete Check

- [x] 新增 `GET /api/v1/assessment-tasks/{task_id}/delete-check`
- [x] 返回 task/source/status/deleteMode/canHardDelete
- [x] 返回 target/session/result/import batch/risk event/manual review/care case 等影响计数
- [x] 返回明确 warning

#### Hard Delete

- [x] 定义 Hard Delete 判据
- [x] 不得以 `target_count == 0` 作为唯一判据
- [x] 无 `assessment_session`
- [x] 无正式 `assessment_result`
- [x] 无 COMMITTED import batch
- [x] 无 risk event
- [x] 无已生效正式业务事实
- [x] 安全删除 `assessment_target`
- [x] 安全删除 `assessment_task_scope`
- [x] 安全删除明确可删的临时数据
- [x] 删除 `assessment_task`
- [x] 整个过程单事务

#### VOID

- [x] 已有正式测评事实时禁止物理删除
- [x] `assessment_task.status = VOIDED`
- [x] 写入 `voided_at / voided_by / void_reason`
- [x] 该 task 下当前有效 `assessment_session.is_effective = false`
- [x] 保留 session / answer / result / dimension result
- [x] 保留 import batch / import row / external result
- [x] 不物理删除历史测评事实

#### Risk Event

- [x] 找出 task 下所有相关 `risk_event`
- [x] PENDING risk event 作废 / 失效（置 `VOIDED` 并写 `void_reason`）
- [x] 已人工复核的 risk event 不删除
- [x] 已产生 manual review 的记录不删除
- [x] 工作台不继续统计已作废任务的待处理 signal（PENDING 已被置 VOIDED，构造上排除）

#### Care Records

- [x] 保留 `student_care_case`
- [x] 保留 `manual_review`
- [x] 保留 `follow_up_record`
- [x] 保留 `family_contact_record`
- [x] 保留 `retest_plan`
- [x] 保留 `care_case_event`
- [x] 如展示来源 task，可标记“来源测评任务已作废”（`VOIDED_SITTING_LABEL` / `VOIDED_TASK_SOURCE_NOTE`，两个历史页）
- [x] 不自动关闭已开展中的 care case

#### 删除 / 作废接口

- [x] 统一设计用户“删除任务”动作
- [x] `DELETE /api/v1/assessment-tasks/{task_id}` 或 `POST /api/v1/assessment-tasks/{task_id}/delete` 二选一（取前者）
- [x] VOID 时 reason 必填
- [x] 返回实际执行模式 `HARD_DELETE / VOID`

#### 权限

- [x] 心理老师按任务权限执行
- [x] 德育领导不可删除 / 作废
- [x] 学生不可删除 / 作废
- [x] 管理员默认不可自动获得心理业务删除权限
- [x] 服务层保留权限 backstop

#### 审计

- [x] Hard Delete 写审计（`删除测评任务`）
- [x] VOID 写审计（`作废测评任务`，**另一个动作码**）
- [x] 记录 operator / task / source / mode / reason / 影响计数 / 是否已有人工关怀

#### VOIDED 过滤

- [x] `list_assessment_tasks()` 默认排除 `VOIDED`
- [x] 增加“已作废”筛选
- [x] 统计分析任务选择器排除 `VOIDED`
- [x] 专业报告任务选择器排除 `VOIDED`
- [x] DataCenter 关联已有任务下拉排除 `VOIDED`（走同一个列表端点，构造上继承）
- [x] counselor 工作台完成率排除 `VOIDED`
- [x] leader 任务统计排除 `VOIDED`
- [x] 最新结果查询增加 `task.status != VOIDED` 防御性约束
- [x] 学生当前状态不读取已作废任务结果
- [x] 学生本人历史**不显示**已作废任务记录（§5.9 第 2 条裁决「完全隐藏」，即规格原文口径）。
      学生的两个入口各断一次：`/student/tasks` 与 `/student/assessment-history`——
      作废之前他看得见，作废之后两边都没有
      （`test_task_governance.py::test_a_voided_task_disappears_from_the_students_own_pages`）。
      同一条用例的反方向还断着「心理老师那一侧照常看得到并带着『已作废』」，所以它不是靠
      「把 VOIDED 从全站抹掉」成立的。
      **2026-09-25 订正**：本行原来写着「有意偏离规格 → 改为显示并标注 …**需要你确认**」，
      两处与事实不符——「显示并标注」描述的是**下一行**（心理老师侧）那句标识，学生侧两个
      入口一直带着 `active_task_predicate()`（零生产改动）；而那个方向本身也与裁决相反。
      以裁决与用例为准。
- [x] 心理老师历史查看可显示，但标识“已作废，不参与当前判断”

#### 前端

- [x] TasksPage 增加“删除任务”
- [x] 删除前调用 `delete-check`
- [x] 根据 deleteMode 展示不同确认文案
- [x] VOID 必须输入原因
- [x] 显示任务来源：在线测评 / 外部导入
- [x] 增加筛选：全部 / 进行中 / 已结束 / 已作废
- [x] 默认隐藏已作废

#### Backend Tests

- [x] `test_delete_unused_in_system_task`
- [x] `test_delete_unused_task_removes_targets_and_scope`
- [x] `test_delete_task_with_session_becomes_voided`
- [x] `test_void_task_marks_sessions_ineffective`
- [x] `test_void_task_excludes_from_latest_result`（+ 另一条钉防御性约束的，见下方那一节）
- [x] `test_void_task_excludes_from_task_list_by_default`
- [x] `test_void_task_can_be_listed_with_voided_filter`
- [x] `test_void_imported_task_keeps_import_batches`
- [x] `test_void_task_keeps_results_for_history`
- [x] `test_void_task_pending_risk_events_are_voided`
- [x] `test_void_task_keeps_manual_review`
- [x] `test_void_task_keeps_care_case`
- [x] `test_leader_cannot_delete_task`
- [x] `test_student_cannot_delete_task`
- [x] `test_delete_reason_required_for_void`

清单之外的 3 条（自己加的，钉规格没写但会被写坏的形状）：
`test_voiding_twice_is_refused_so_the_record_is_not_overwritten`（重复作废 409，
**第一次写的 `void_reason` 不被覆盖**）、`test_admin_cannot_delete_task`（403）、
`test_a_voided_task_is_not_read_even_if_its_session_was_not_downgraded`（§4.14 防御性约束）。

**实测**：`backend/app/tests/test_task_governance.py` → **18 passed**；
与 `test_migrations_build_the_models.py` + `test_ensure_schema.py` 合跑 →
**46 passed, 3 warnings in 12.43s**（3 条 warning 全是既有的：2 条 httpx/anyio 弃用、
1 条 `analytics_service.py:1015` 的笛卡尔积**误报**，CLAUDE.md §20/§23 记着别去"修"）。

#### E2E

- [x] 在线空任务可删除 → `还没产生正式测评事实的空任务可以整体删除`
- [x] 有人提交后只能作废 → `产生了测评事实的任务只能作废，影响预览逐条说出保留了什么`
- [x] VOID 后默认列表消失 → 同下一条（同一趟里两个结论都查）
- [x] “已作废”筛选可查 → `作废之后从默认列表消失，只在「已作废」里找得到（导入批次仍在）`
- [x] IMPORTED task 作废后 import batch 仍存在 → 同上（用一份**预览**批次）
- [x] 作废 task 不再影响统计 → **不断像素**，断「那句承诺被说了出来」（见下）
- [x] 作废 task 不删除人工关怀记录 → 同上

**7 个场景写成 3 条用例**（`e2e/app.spec.ts` 的 `测评任务的删除与作废治理` 组，
组头那段注释是这条映射的出处）。两处**刻意不断**，理由写在组头而不是留给人猜：

- 「不再影响统计」「不删除人工关怀记录」在界面上**没有一处只有作废才会变**的像素：
  演示库上的统计数字同时被并发的其它用例改动（`fullyParallel`），拿前后两次读取相比
  会变成一条会无故变红的守卫。所以这边断的是那两句话**被说了出来**
  （影响预览里的「N 份答卷」「关联 N 份关怀档案…不会删除，也不会被修改」），
  「说了就真的做了」由后端 18 条钉住。
- 「已提交的 import batch 仍在」做不出来：提交要文件里那一列班级是数字编号（`704`），
  而演示名册的班叫 `1班`——得先动共享名册（§26 / §27 记着同一类取舍）。走的是**预览**
  批次，后端那一侧由 `test_void_imported_task_keeps_import_batches` 钉住。

**★ 这一组一度必须 `serial` 跑，而理由是实测出来的（一条真实缺陷）**：全仓只有这一组会用
`POST /api/v1/assessment-tasks` 真建任务，而那个端点**并发不安全**——
`task_service.create_school_assessment_task` 拼的任务号是
`TASK-{utcnow:%Y%m%d%H%M%S}-{全库任务数 + 1}`，同一秒内的两个请求算出**同一个号**，
撞 `task_no` 唯一键，其中一个拿 500。实测复现：两个并发 `curl` POST →
`req1 http=500` / `req2 http=200`。默认 3 workers 下那次「1 failed / 2 passed」红在
**用例自己撞自己**上，与被测代码无关。

**2026-09-25 已修**（用户裁决「撞号就重试」，见 §5.9 第 1 条），于是那条
`test.describe.configure({ mode: 'serial' })` **删掉了**——串行是绕过、不是修复，留着会
掩盖真回归。**但拆掉它当场又露出第二处耦合**（同一天实测，见 §5.10 最后一节）：
这一组里「产生了测评事实的任务只能作废」原来按 `total_targets` 挑靶子，而并发建出来的
新任务目标数与演示那一场一样多，于是可能指到一场**一场都没答过的空任务**上。判据已改成
按 `completed_targets`（事实）挑。**两件事要分开记**：串行线是为撞号加的，而它顺手
遮住了挑靶子这件事——所以「删掉串行之后全绿」本身不足以证明那一组没有别的耦合。

#### ✅ §4.14 防御性约束的变异验证（2026-09-25 完成，含一处发现）

第一次跑变异**没红**，而原因是**用例打偏了靶子**（CLAUDE.md §28 M2 那条：变异不红时
先怀疑变异/用例写错，不是守卫失灵）。查清之后是这么回事：

| | 事实 |
|---|---|
| `analytics_service.py:995` 那一行 | `or_(AssessmentSession.task_id.is_(None), active_task_predicate())` |
| 变异 | 整句换成恒真（`AssessmentSession.id > 0`） |
| 结果 | **18 passed 全绿**——它证明不了那一句 |
| 为什么 | `delete_or_void_task` 会**顺手把会话降级**（`is_effective=False`），而同一个子查询里本来就有 `effective_session_predicate()`。所以作废之后结果被挡住靠的是**降级**那一步，`active_task_predicate()` 那一句在这条路径上是冗余的 |

**处置：新增 `test_a_voided_task_is_not_read_even_if_its_session_was_not_downgraded`**
——它构造的正是「防御性」三个字存在的理由：库里出现「任务已作废、而会话**仍然是有效场**」
这个组合。做法是**刻意绕过 `delete_or_void_task`**，只把 `assessment_task.status` 改成
`VOIDED` 一列（走它就又把会话降级了，于是退化成上面那一条），并且**先断言
`session.is_effective is True`** —— 前置条件不成立时这条会退化成前一条的重复，
而它看起来还是绿的。

补跑变异的结果：

```
变异（整句换恒真） → 1 failed, 17 passed
  red 的正是：test_a_voided_task_is_not_read_even_if_its_session_was_not_downgraded
还原 → cmp 逐字节一致（cp -p 的落盘备份）→ 18 passed
```

**副产品：那条老用例的 docstring 也改了**，明写「它证明的是降级那一步，不是查询里那句
`active_task_predicate()`」并指向新的那一条——一条断言写得像在证明防御性约束、实际只
证明了降级生效的用例，与 §29 那条「一条恒绿的守卫比没有更糟」是同一类。

### 5.2 P0-02 数据范围显性化

- [x] 检查 `/me` 当前返回是否足以生成范围摘要 → **不足**：`scopes[]` 只有 `scope_type` +
      `scope_id`（机读的 id），界面要拼「年级 · 初一」还得自己拿 id 去换名字；而
      `student_scope_predicate` 的多行是 `or_` 合并的（并集），这个**合并口径**在响应里
      根本没有表达。所以另起一个端点
- [x] 如不足，新增 `GET /api/v1/auth/me/data-scope-summary`（`api/v1/auth.py:193`，
      实现 `security/data_scope.py:54` 的 `data_scope_summary`）
- [x] 返回 `scopeType / displayText / schoolWide`（外加 `scopeCount` 与 `scopeLabels[]`：
      多行范围时要能逐行列出来，只给一句 `displayText` 会让人以为只能有一个范围）
- [x] counselor Analytics 页面增加 Data Scope Badge（`components/ReportPageHeader.vue`，
      四张统计页共用；不在各视图里各写一遍）
- [x] SCHOOL counselor 显示“全校”
- [x] GRADE counselor 显示具体年级
- [x] CLASS counselor 显示具体班级
- [x] Leader 显示“全校”（`SCHOOL`）
- [x] `全校预警总览` → `筛查关注概览`（`routes.ts:52` / `:67` 的 `meta.title` 与
      `AppLayout.vue:38` / `:58` 的菜单 `label`，两处都改了）
- [x] `全校八维度分析` → `八维度分析`（同上，`routes.ts:53` / `:68`、`AppLayout.vue:39` / `:59`）
- [x] 保留后端 `student_scope_predicate()` 权限约束（**一个字没动**；这一节的全部产出是
      「把既有的范围事实说出来」，不是放宽或收紧任何一条判据）
- [x] 增加 scope backend tests → `test_data_scope_summary.py`，**10 条**
- [x] 增加 GRADE / CLASS counselor E2E → `e2e/app.spec.ts` 的 `数据范围徽标` 组（2 条），
      辅助函数 `newCounselorWithScope(page, 'GRADE' | 'CLASS')`（`:994`）**当场建一个**只
      带那个范围的临时心理老师再登录——不借用演示账号（它们的范围都是 `SCHOOL`，借不来）

**实测复核（2026-09-25）**：
`rg "data-scope-summary"` 命中端点与 10 条用例；`ReportPageHeader` 被 8 个文件引用
（四张统计页 + 领导报告页 + 工作台 + 导出页 + `AppLayout`）；`rg "筛查关注概览|八维度分析"`
在 `routes.ts` 与 `AppLayout.vue` 各 4 处、旧标题 `全校预警总览|全校八维度分析` **0 处**。

### 5.3 P1-01 专业报告角色拆分

- [x] counselor `/counselor/analytics/report` 改为“专业分析报告”（`routes.ts:56`）
- [x] counselor 可编辑、保存、发布、创建新版本、正式导出
- [x] leader `/leader/analytics/report` 独立为“学校心理工作分析摘要”
      （`LeaderAnalyticsReportPage.vue`，40 行、只读、无编辑控件、无导出按钮）
- [x] leader 仅查看已发布摘要（页面自己写明了这一句，含「不含原始答卷 / 重点题答案 / 回访正文 / 私密记录」）
- [x] leader 禁止编辑专业解读 → `test_a_leader_cannot_edit_a_professional_report`（403）
- [x] leader 禁止查看原始答卷、重点题答案、回访正文、心理老师私密记录（那几处在 leader 侧本来就不可达，页面文案说明了这一点）
- [x] 新增/复用 `PROFESSIONAL_REPORT_READ / EDIT / PUBLISH`（三项，各自有 `CAPABILITY_LEVELS` 与 `CAPABILITY_DEFAULTS`）
- [x] Leader 调编辑接口必须 403（同上那条用例）

### 5.4 P1-02 专业报告服务端持久化

- [x] 新增 `professional_report`（迁移 `0022_professional_reports.py`，**只新增**）
- [x] 新增 `professional_report_version`
- [ ] 支持 `DRAFT / PUBLISHED / ARCHIVED` → **前两档已实现，`ARCHIVED` 未实现**，理由见 §5.9
      （规格只给了三个状态名，没给「什么时候归档」这条转换；凭空造一个没有任何写入方
      的状态就是一条恒不可达的分支，CLAUDE.md 的「不静默猜测」不允许）
- [x] 冻结 task scope / sample count / dimension stats / 关注人数比例 / generated_at
      （`create_report` 那一刻 `jsonable_encoder(analytics_report(...))` 一次写进报告行与版本 1 行）
- [x] 历史报告读取 snapshot，禁止实时重算覆盖旧数字（`_snapshot_of`，导出文件是**纯读**的）
- [x] `POST /api/v1/professional-reports`
- [x] `GET /api/v1/professional-reports`
- [x] `GET /api/v1/professional-reports/{id}`
- [x] `PUT /api/v1/professional-reports/{id}/draft`
- [x] `POST /api/v1/professional-reports/{id}/publish`
- [x] `POST /api/v1/professional-reports/{id}/new-version`
- [x] 完成报告 CRUD / 权限 / snapshot / immutable tests → `test_reporting_api.py` **17 条**

本节的三条实测（都不是「照清单打勾」）：

1. **它此前一条后端用例都没有，而那个缺口底下压着一个每次调用都 500 的真缺陷**
   （`reporting.py` 的 `serialize_export_jobs(db, user, [job])` 少传了两个位置参数）——
   与 CLAUDE.md §24 那条「路由没有测试 → 写了但没提交只有 e2e 看得见」是同一类，
   只是这一次连 e2e 都没走到那个端点（e2e 只走界面，而界面当时没接导出）。
2. **「发布后不可覆盖」是 409 而不是 422**，文案「已发布版本不可覆盖，请先创建新版本」——
   用 422 会让它与「你传的参数不对」混成一句，而这两件事的出路不同。
3. **两层拒绝的分工**：能力不够 → 403；看不到那份报告（不存在 / 不归你 / 领导看草稿）→
   **404**，文案一律 `专业报告不存在`（「不属于你」与「不存在」在响应上必须不可分辨，
   CLAUDE.md §24）。

### 5.5 P1-03 正式报告接入 Export Job

- [x] 复用现有 Export Job（`create_export_job` + 既有的导出中心与下载端点）
- [x] 禁止建设第二套导出框架（**没有**新增任何第二套导出模型 / 端点）
- [x] 新增/复用 `PROFESSIONAL_REPORT` export type（`export_service.py:247`）
- [x] `LEADER_MANAGEMENT_SUMMARY` → **判定不需要**：领导导出的是**同一份已发布版本**
      （`test_a_leader_exports_the_published_report_but_not_the_draft`），
      再造一个类型等于把同一份文件按角色复制一遍，而两份会长歪
- [x] 选择报告 → 版本 → 用途 → Export Job → 后端生成 → 下载 → 审计
      （`POST /professional-reports/{id}/export-jobs`，`version_no` 可选，默认当前版本）
- [x] 记录 operator / report id / no / version / purpose / format / generated_at /
      downloaded_at（作业行 + 审计；`test_the_export_audit_names_the_report_and_the_version`）
- [x] 正式报告不再以 `window.print()` 作为唯一导出
- [x] 正式报告不再以前端 Blob 作为唯一导出
- [x] 浏览器打印仅作为临时打印预览
- [x] 完成 Export Job backend tests（`test_the_export_goes_through_the_export_center` /
      `test_the_download_has_as_many_rows_as_the_job_says` /
      `test_the_document_carries_the_frozen_numbers_and_both_kinds_of_blank` /
      `test_each_version_exports_its_own_text`）
- [x] E2E（`app.spec.ts:886` 那条：填 → 保存 → 导出（断「正式报告已通过导出中心生成并下载」）
      → 发布 → 再保存断「已发布版本不可覆盖」）

**`row_count` 写 `len(rows)` 而不是字面量**（曾经硬编码 `8`）：一份快照里有几个维度是变的，
写死的那个数会在维度缺失或多出时静默说错——而它在界面上是「导出 N 行」。

### 5.6 P1-04 产品术语统一

- [x] `风险学生` → `需要关注学生`
- [x] `风险等级` → `关注等级`
- [x] `高风险` → `重点关注`
- [x] `风险事件` → `筛查信号`
- [x] `预警人数` → `关注人数`
- [x] `全校预警总览` → `筛查关注概览`
- [x] 评估 `重点学生` → `重点关注学生` / `重点关怀` → **改名了**：菜单
      `AppLayout.vue:33` 与路由 `routes.ts:42` 都改成 `重点关注学生`，**路径
      `/counselor/cases` 与菜单 `key: 'cases'` 一个字没动**——改那两样会改掉书签、
      改掉 e2e 的定位、改掉审计里已经落库的历史，而词条才是用户读到的那一层
- [x] 学生端禁止诊断化表达
- [x] 数据库技术字段如 `risk_*` 不做无意义迁移重命名（一个都没改）

**判据不是「文件里搜不到那几个字」，是「搜到的每一处都不是界面文案」**（2026-09-25 实测）：

| 检查 | 结果 |
|---|---|
| `rg "风险学生\|风险等级\|高风险\|风险事件\|预警人数\|全校预警\|预警总览"` 于 `frontend/src` + `e2e` | 剩下的命中**全部**在注释 / docstring 里（逐行看过行首那一栏）；旧标题 `全校预警总览` / `全校八维度分析` 在路由与菜单里 **0 处** |
| `rg "重点学生"` | 6 处，**6 处都是注释**——其中 `AppLayout.vue:31` 那条注释正是记录这次改名的 |
| 学生端诊断化表达 | `features/student` + `features/auth` 里命中 2 处，**两处都是否定式**：「筛查结果用于学校提供帮助，不等同于医学诊断」（登录页）与「学生端不展示分数、关注等级、重点题和诊断性描述」（学生记录页）。那是产品边界本身，不是诊断化表达 |
| 数据库 `risk_*` | `risk_event` / `risk_type` / `risk_level` / `signal_type` 一列未动——它们是**已经落库的历史**，改名要迁移、要重写查询，而用户读到的中文早已由 `labels.ts` 那一层换掉了（CLAUDE.md §3 第一面） |

### 5.7 全局回归

- [x] Alembic 从 V1.1.6 升级成功
- [x] 旧数据兼容
- [x] 在线测评正常
- [x] 外部导入正常
- [x] 同月 IMPORTED task 复用逻辑未被破坏
- [x] 外部导入批次历史正常
- [x] MHT 评分结果不变
- [x] 学生答题正常
- [x] 心理老师工作台正常
- [x] 德育领导统计正常
- [x] 管理员组织 / 题库 / 配置正常
- [x] 原有 Export Job 类型正常
- [x] Backend Tests 全部通过
- [x] 前端 build 通过
- [x] E2E 通过，或记录环境限制
- [x] 代码最终提交到 `V2.0.0` ← **2026-09-25 完成**（用户发话「2 推送」）

  | 判据 | 实测 |
  |---|---|
  | 提交 | `70e654ebf519f18ff12e84d1b17a50bffa880de5`（`feat: implement V2.0.0 P0-P1 product enhancements`） |
  | 父提交 | `08254dc`（`V1.1.6` 的 head）——分支仍是一条直线，没有 merge、没有 amend |
  | 提交内容 | 13 新增 / 49 修改 / 1 删除（`frontend/src/features/analytics/AnalyticsPage.vue`，随 P1-01 角色拆分删掉） |
  | 推送 | `git push -u origin V2.0.0` → `* [new branch] V2.0.0 -> V2.0.0`，`origin/V2.0.0` 至此创建；`git status -sb` 报 `## V2.0.0...origin/V2.0.0`（无 ahead / behind） |

  **`main` 一个字没动**（它仍是 V1.0.0），也没有把 `V2.0.0` 合并进去——本轮只推送这一条分支。

**这一节的每一格都是实测值，不是「照清单打勾」**（2026-09-25）。逐条写下判据与出处：

#### Alembic 与旧数据兼容

| 场景 | 做法 | 实测 |
|---|---|---|
| 在真数据上升 | `/tmp/xlp_prerun.py`：把开发库**逐表**拷进 `xlp_upgrade_test`，逐表核对行数相等（36 张表 / 15716 行），再 `XLP_DATABASE_URL=…xlp_upgrade_test alembic upgrade head` | `0020 → 0021 → 0022`，**EXIT=0** |
| 在空库上升 | `throwaway_database(with_schema=False)` 从 `0001` 跑到 head | **EXIT=0**，37 张表（36 应用 + `alembic_version`） |
| 降得回去吗 | 同一个克隆上 `alembic downgrade 0020` | **第一次红**——1553，见 §5.8 第 3 条；修 `0022` 的 downgrade 之后重跑 **EXIT=0** |

**旧数据兼容的判据是「升级前后逐表比对」**，不是「升级没报错」：

- 34 张**既有**表逐表行数**完全相同**（一张不少、一行不多）；
- 其中 33 张**逐字节**相同；
- 唯一有差的是 `assessment_task`，而差的那三列正是 `0021` 自己加的三列
  （`voided_at` / `voided_by` / `void_reason`，新行均为 NULL）——`downgrade` 会把它们真删掉，
  这是降级的本义，不是数据被改写。

**升级预演这一步有个前提要写明**：`alembic_version` 那一行**不能拷**
（同 `mysqldump --ignore-table=….alembic_version`），拷了就是
`1062 Duplicate entry '0020_total_excludes_validity'`。也不能用「当前代码 + `seed`」造一份
V1.1.6 的库——当前代码的 `seed` 会往 V2.0.0 才有的列里写值。这一点与 CLAUDE.md §21
那次 V1.2 升级预演逐字同源：**只有真数据能回答「这两条迁移在它上面过不过得去」**。

#### 六个功能面各点名一条用例（不是「套件全绿所以都正常」）

| 面 | 主要守卫 |
|---|---|
| 在线测评 | `test_assessment_api.py`（开卷 / 提交 / 幂等重放 / 逾期仍能交） |
| 外部导入 | `test_assessment_import_api.py` + `test_import_conflict_resolution.py`（四档处置、冲突逐行） |
| 同月 IMPORTED 复用 | `test_assessment_import_api.py::test_imports_in_the_same_month_share_one_task_and_get_their_own_targets` |
| 批次历史（作废之后） | `test_task_governance.py::test_void_imported_task_keeps_import_batches` |
| MHT 评分不变 | `test_scale_engine.py`（纯函数层）+ 手工 SQL 的规则版本号已由 `0020` 的修正提交钉住 |
| 学生答题 | `e2e/app.spec.ts` 的「学生答题」组（`serial` 跑）+ `test_assessment_api.py` 的 `reset` 系列 |
| 心理老师工作台 / 领导统计 | `test_care_api.py` / `test_analytics_api.py` / `test_task_roles.py`（含缺口 12 那两道门） |
| 管理员组织 / 题库 / 配置 | `test_account_admin_api.py` / `test_scale_import_api.py` / `test_settings_api.py` |
| 原有 Export Job 类型 | `test_export_jobs.py` / `test_audit_export_api.py`（新增的 `PROFESSIONAL_REPORT` 是**第 8 个**类型，前 7 个各自有用例） |

#### 三个数字（都是这一次跑出来的）

```
make test  → 839 passed, 5 warnings in 492.60s (0:08:12)   exit 0
make e2e   → 144 passed (33.4s)                            exit 0
前端 build → vue-tsc -b && vite build 通过
```

**这一组是裁决「新版本重新统计快照」之后重跑的实测**（2026-09-25，第一次是 838 / 494.98s）。
**两条命令之间没有第二个 pytest 在跑**（CLAUDE.md §20：每个 pytest 进程进门都会 DROP
测试库）。e2e 那一次跑之前确认过开发库已经停在 head（`0022_professional_reports` / 37 张表）
——库落后于代码时 e2e 会红在一个与功能无关的地方（CLAUDE.md §27 记着那次 4 failed）。

- **839 比 V1.1.6 的 837 多两条**：本轮新加的迁移守卫
  `test_migration_v2_governance.py`，加上裁决后补的
  `test_reporting_api.py::test_a_new_version_recomputes_the_statistics_it_inherits`。
- **5 条 warning 与 V1.1.6 基线是同一个数**（CLAUDE.md §20 记着 V1.2 各阶段也都是 5 条），
  其中两条是**已被记录成误报**的笛卡尔积告警（`export_service.py` 与
  `analytics_service.py` 各一条，CLAUDE.md §20 / §23 明确写着**别去「修」它们**——
  那两句 SQL 的每个 FROM 元素各有各的 ON，`latest_session_id` 是标量子查询）。
  **本轮没有新增 warning**：838 与 837 两次跑出来都是 5 条。
  （剩下三条我没有逐条查到出处，只确认了总数没变——**别把这一句读成「五条都认过了」**。）
- e2e 从 V1.1.6 的 143 多一条，是本轮 P0-01 那一组（3 条用例覆盖规格 7 个场景）与
  P0-02 那两条加进来的净增。

**测试环境**：`make test` 跑在真 MySQL 上（`xinliceping_test`，session 级 DROP + CREATE +
`alembic upgrade head`），e2e 跑在开发库 + `make seed-demo` 的演示数据上，前后端分别在
8000 / 5173 起着。全程**没有两个 pytest 进程同时跑**（CLAUDE.md §20：每个 pytest 进程
进门都会 DROP 测试库）。

**这一轮结束时清掉的临时物**：`xlp_upgrade_test` 已 DROP；临时库/探针只活在 `/tmp`。

**★ 版本号当时没有动，这是有意的，等你裁决——2026-09-25 已裁决：升到 `2.0.0`，
并已作为一条独立提交落地（`555674b`，见 §5.10）。** 下面是当时那段话的原样：
`backend/app/version.py` 仍然是
`__version__ = "1.1.6"`（于是 `VERSION_LABEL` = `V1.1`、出包产物仍然是
`心晴部署包_V1.1.6.zip`、页脚那一行仍然写 `V1.1`）。规范只写了「目标版本：`V2.0.0`」，
**没有一句要求改 `version.py`**，而这一改是**对外可见**的（产物名、`package-info.txt`
的构建戳、界面页脚），并且会连带改 `test_app_version.py` 里那条按 `V1.1` 写死的字面量
（CLAUDE.md §30 记着那个字面量**是故意随 `__version__` 变的**，所以它得一起改）。
前两条发布线（V1.1.5 / V1.1.6）各自都有一个**单独的**「版本号升到 X」提交，说明在这一
仓库里升版本是一个**独立动作**，不是「顺手带上」。所以本轮**没有**把它塞进这次提交——
你说一句要不要升到 `2.0.0`，升的话是一条两行改动的独立提交（`version.py` +
`frontend/package.json` + 那条断言的字面量）。

**（2026-09-25 后续：正是这么做的——`555674b`，三处一起改，见 §5.10。）**

### 5.8 本轮发现并已修的真实缺陷（不是规范里的条目）

规格里没有这四条，它们是**做这一轮时撞出来的**，前三条都带守卫，所以记在这里以免
下一轮当成「顺手改的」而被回退（第 4 条动的是历史迁移，范围与理由单独写在那里）。

1. **`POST /professional-reports/{id}/export-jobs` 每次调用都 500。**
   `reporting.py` 里那一行写的是 `serialize_export_jobs([job])[0]`，而那个函数要
   `(db, user, jobs)` 三个位置参数——少传两个，第一次调用就 `TypeError`。
   **它此前没有任何测试**（第七条端点上线时没配用例），所以这个缺陷一路活到了这一轮。
   现在 17 条 `test_reporting_api.py` 里有一条走完整的建作业 → 下载链路。
2. **`purge.py` 不带专业报告，`make purge-demo` 的承诺对这条线不成立。**
   `purge_demo_data` 的文档字符串写着「回到只有 `seed.py` 基线的状态」，而
   `professional_report` / `professional_report_version` 两张表不在它的删除清单里：
   e2e 那条报告用例**每跑一次建一份报告、从不回收**，共享演示库上这一行单调增长
   （与 `auth_session` 那条已知残留同形，区别是这个可以修）。
   修法是按 `created_by in (演示账号)` 删、**版本行跟着它自己的报告走**，
   并配一条**双方向**守卫（`test_purge_demo.py:244`）——只断「演示那份没了」的话，
   一个「把整张表清空」的实现照样绿，而那一句会把真实操作员写过的报告一起删掉。
   变异验证 4/4：删版本行 → 1451 `professional_report_version_fk_report`；
   删报告行 → 1451 `professional_report_fk_created_by`；两个方向各一次断言红。
3. **`0022` 的 `downgrade()` 会撞 MySQL 1553——「退回 V1.1.6」这条路是断的。**
   本轮自己新写的那条迁移，`upgrade()` 走通了、`test_migrations_build_the_models.py`
   全绿，而 **`downgrade()` 里的语句没有任何东西执行过**。做 §5.7 的升级预演时在开发库的
   忠实克隆上跑 `alembic downgrade 0020`，当场：

   ```
   pymysql.err.OperationalError: (1553, "Cannot drop index
   'ix_professional_report_school_status': needed in a foreign key constraint")
   ```

   原因是它在 `drop_table` 之前先 `drop_index("ix_professional_report_school_status")`，
   而那个索引的第一列就是 `school_id`、正是 `professional_report_fk_school` 拿来当索引
   用的那一条。**删表本身会把它的外键与索引一并带走**，所以那一行既多余又致命——
   与 CLAUDE.md §1 里 `0012` 删那条唯一索引时是同一个坑（那条是**必须**先补建
   `ix_student_care_case_student_id` 才能删）。
   **MySQL 的 DDL 不在事务里**，所以那次降级停在「版本表没了、版本戳还是 0022」的半截
   状态上，克隆库只能整个重建。
   修法是删掉那一行 `drop_index`；**守卫是新增的
   `test_migration_v2_governance.py`**——它走 `0020 → head → 0020 → head` 四个方向，
   并在 0020 上真的放一条旧任务（学校 / 量表 / 一份 `CLOSED` 的普查），断言一来一回之后
   **它的每一个旧列都还是原样**。
   变异验证 1/1：把那一行 `drop_index` 放回去 → 红，报出的正是那句 1553
   （`cp -p` 落盘备份 + `cmp` 逐字节还原）。
   **它证明不了生产库降得回去**（真数据上会不会撞别的约束要拿真库跑），这条网眼写在那个
   文件的 docstring 里。
4. **`0020` 的 `PRECHECKS` 里有一条误报，会让这条迁移在一台正常的库上中止。**
   **这一条动的是历史迁移**，而「禁止修改历史 Alembic migration」是硬约定，所以它的
   范围与理由要写清楚（**只改 `PRECHECKS` 的一句话、一个字的 DDL 都没动**）：

   - **改了什么**：第二条检查（「存在没有完整 100 道原始答案的 MHT 结果」）的谓词从
     `WHERE COALESCE(a.n, 0) <> 100` 改成 `WHERE COALESCE(a.n, 0) NOT IN (0, 100)`；
   - **为什么这是误报**：同一条迁移的 `RECALCULATE_RESULTS` **内连接** `GROUP BY
     assessment_answer` 的子查询，所以它只碰**有答案**的会话——`n = 0` 的结果行它压根
     不重算（那一行保留原规则版本）。于是「无法准确重算」精确等于 `1 <= n <> 100`；
   - **它为什么真的会发生**：`POST /assessment-sessions/{id}/reset` **此前**删答案但
     **刻意保留 result 行**（好让下次提交走 `submit_session` 的幂等早返回），所以「会话
     `IN_PROGRESS`、0 答案、有一份旧结果」是这套系统**正常产生**的状态——任何跑过学生
     答题 e2e 的库都有一行，而我们会带着这样一行去做升级预演。它描述的那场作答已经
     不存在了，也就没有「算得准不准」；
     （**2026-09-25 起 `/reset` 连结果一起清**，所以这个形状不再新产生。但这条放行照旧
     要留着：库里已有的那些行不会消失，而我们要拿去做升级预演的那一份克隆正是带着它们
     的——判据是「这一行在不在」，不是「它还会不会再生出来」。）
   - **影响面**：`PRECHECKS` 只在 `upgrade()` 的时候跑（已经升到 ≥ `0020` 的库永远不会
     再读它），所以这不是「改写已生效的迁移」；而它会被渲染进
     `backend/sql/upgrade_from_v1_0_0.sql`，那一份已按 `make db-upgrade-sql` 重生成，
     逐字节守卫（`test_incremental_upgrade_sql.py`）在重跑里是绿的。
   这条缺口与 §5.9 第 3 条（`reset_session` 留下孤儿结果）是**同一件事的两面**：这一面
   只是让迁移**不要被它挡住**，没有回答「那一场还算不算数」。

   **2026-09-25 补：那一问已经有人答了，而这条检查的谓词一个字都不用改。** 用户裁决
   「重置时连结果一起清掉」（§5.9 第 3 条），于是 `n = 0` 的结果行**不再由 `/reset`
   产生**——新代码里清掉结果行之后那一场根本没有结果行。看起来这条放行可以撤回去了，
   **不可以**，两条理由各自成立：① 它跑在**历史库**上，而历史库里那些行（2026-09-25
   之前跑过学生答题 e2e 的库都有）仍然在那儿，`n = 0` 照样要放行；② `NOT IN (0, 100)`
   本来就是**对的**——它精确等于「有答案、但不满 100」，与 `RECALCULATE_RESULTS` 那个
   内连接子查询的覆盖面逐字相同，与「`n = 0` 是怎么产生的」无关。所以这次裁决让上面
   那一段历史说明过期，而**没有**让那个谓词过期。

### 5.9 待裁决清单（**2026-09-25 已全部裁决并落地**）

**这一节里每一条当初都是「规格没有给答案，而自己定就等于猜」。** 每条都保留了当时的
实际行为、为什么没有顺手改、以及要改它需要先回答什么——然后写上裁决与落地。

**余下五条（第 1 / 2 / 3 / 4 / 6 条）在 2026-09-25 一次问齐了**：其中四条由用户在
AskUserQuestion 里点选，第 4 条按仓库既有约定直接改（当时对你写明的原话是「这一条我
打算直接按约定改……因为它有现成的仓库约定可依，不算猜」）。**没有一条是猜的。**

1. ~~**`task_no` / `report_no` 同秒并发会撞唯一键。**~~ **2026-09-25 已裁决：撞号就重试，
   并已落地。**
   当时的实际行为是两个请求在同一秒内到达时，
   `TASK-{utcnow:%Y%m%d%H%M%S}-{全库任务数 + 1}` 这个形状会算出同一个号——实测
   `req1 http=500 / req2 http=200`（e2e 那一组因此必须 `serial` 跑，见 §5.1）。
   已有一个 `task_no` 唯一约束兜底（所以它是 500 而不是两条同号的任务），
   但**用户拿到的是 500**。修法有三条（序列号 / 撞号重试 / 放宽成不唯一），
   规格判不出来，所以当时未修。`report_no` 同形。

   **用户裁决：撞号就重试。** 落点是一个新的小模块 `backend/app/services/numbering.py`
   （`insert_with_unique_number`），两个生成方各接一次：

   - `task_service.create_school_assessment_task`：先算一次 `stamp`
     （`%Y%m%d%H%M%S`）与 `task_count`，再让 `number_at(offset)` 用
     `TASK-{stamp}-{task_count + 1 + offset}` 取号；
   - `reporting_service.create_report`：`_number` 拆成 `_number_stem(db) -> (today, base)`，
     号段按天分（`RPT-<today>-%` 计数），`number_at(offset)` 取
     `RPT-{today}-{base + offset:04d}`。

   四条不能动的，都写在 `numbering.py` 的 docstring 里：

   - **不是「撞了就把计数重读一遍再算」**：MySQL 默认 REPEATABLE-READ，事务快照在第一条
     语句时定下，重读拿到的是**同一份快照**，第二次仍是同一个号——那不是重试，是空转。
     所以偏移量由 `offset` 在**调用方算好的那个基数**上递加，而不是重查。
   - **不是 `SELECT … FOR UPDATE`**：没有行可锁时锁的是间隙，两个间隙锁互相兼容，
     并发下会撞 1213 死锁（与 CLAUDE.md §28 拒绝 `FOR UPDATE` 的理由逐字相同）。
     数据库已有的那个唯一约束就是判据，代价只是撞上时多一次尝试。
   - **整段包在 savepoint 里**（`with db.begin_nested()`）：`IntegrityError` 会让**整个
     事务**进入失败状态，不套 savepoint 的话连下一次 `flush` 都发不出去。这与 §28 那条
     `open_or_reuse_care_case` 是同一个形状。
   - **判据是「刚试的那个号本身」出现在错误信息里**，不是认索引名：索引改名、或换一个
     把约束名拼进消息里的方言，都会让「认索引名」的实现**静默地**把撞号当成别的完整性
     错误往外抛（反之亦然）。不是撞号（外键、非空、别的唯一键）就**原样往外抛**，
     不吞。

   试到 `MAX_ATTEMPTS = 10` 仍然撞就抛 `NUMBER_CONFLICT` 409，不再往下猜。

   **守卫是 `backend/app/tests/test_numbering_concurrency.py`**。它不去真的起两个线程
   （那个夹具做不到，见下），而是**直接构造出「同号」那一刻**：让 `number_at` 前两次
   返回同一个号，断言第二次会换一个号插进去、且**只留下一行**；以及一个「永远撞号」的
   `number_at`，断言 10 次之后抛的是 `NUMBER_CONFLICT` 而不是把 `IntegrityError` 漏出去。

   **它证明不了真并发下不撞号**——`conftest.py` 的 `override_get_db` yield 的是共享的
   `db_session`，两个线程在同一个 Session 上跑不是「两个请求」，这条网眼就写在那个文件的
   docstring 里。它证明的是**撞上之后的行为**，而那正是这次改动动过的东西。

   **连带把 e2e 那条 `serial` 删掉了**（§5.1 那一组）：串行当初是为撞号加的，撞号修好
   之后它就是一条会掩盖真回归的绕过。**删掉之后当场红了一条**，而红的原因不是撞号、
   是那一组自己的第二处耦合——「产生了测评事实的任务只能作废」按 `total_targets` 挑靶子，
   而并发建出来的空任务目标数与演示那一场一样多，于是指到了一场一场都没答过的任务上。
   判据已改成按 `completed_targets`（事实）挑，完整记录见 §5.10 最后一节。
   **这条值得单独记**：修好一个 bug 之后把为它加的绕过拆掉，是**唯一**能让下一个耦合
   现形的动作；留着那条串行线，这第二处要到某次随机排序变化时才会以「偶发红」的样子
   出现（CLAUDE.md §18 那条「一条会无故变红的守卫很快会被人关掉」）。
2. ~~**「学生本人历史默认显示已作废任务并标注」是有意偏离规范的一处。**~~
   **2026-09-25 已裁决：完全隐藏，并已落地。**
   当时规范要求作废后的任务不再出现在学生的历史里，而实际实现是**显示 + 标注「已作废」**；
   当时的理由是「那一场他确实答过卷，把它从『我做过什么』里拿掉等于抹掉一段发生过的事实」
   （CLAUDE.md §1 那条的同一条）。**用户裁决：按规范来，学生侧完全隐藏。**

   **这一条零生产改动**——`list_student_tasks` 与 `list_student_assessment_history` 的
   服务层里**本来就带着** `active_task_predicate()`，缺的只是**覆盖**：`grep VOIDED
   app/tests` 里除了 `test_task_governance.py` 一行都没有，而那个文件各条断的都是心理老师
   那一侧（`/assessment-tasks`）。也就是说把那两个 `.where` 里的谓词整个摘掉，全量套件
   照样全绿——**行为已经是对的，红了才说明有人改坏了它**。

   所以落点是 `test_task_governance.py` 的两条新用例（各带一个不能省的对照）：

   - `test_a_voided_task_disappears_from_the_students_own_pages`：学生的两个入口
     （`/student/tasks` 与 `/student/assessment-history`）各断一次。**先证明他原本看得见**
     （作废之前两个列表里都有它）——少了那半句，一个「学生永远看不到任何任务」的实现
     （谓词写反了）同样能让后面三句成立。最后**反方向**断心理老师那一侧照常看得到它、
     且带着「已作废」——少了它，一个「把 VOIDED 从全站所有查询里抹掉」的实现也能让上面
     三句全绿，而那时这场任务从任何入口都找不回来了。
   - `test_a_student_cannot_start_a_new_sheet_on_a_voided_task`：列表藏了还不够，
     `POST /assessment-sessions` 也开不了。这里刻意造的是「这场任务有事实、而这名学生
     **一场都没开过**」的形状（绑了一批 `PREVIEW` 的导入批次），因为若改用「先让他开一张
     卷子再作废」，那一条路的答案是**另一个**：他手里那份卷子不被收回（CLAUDE.md §12
     「已结束不等于把人踢出卷子」）——那是刻意的，别把两件事混成一条断言。
     **对照那一句不能省**：同一名学生同一时刻对着没作废的任务能开卷。
3. ~~**`reset_session` 保留 `assessment_result`，于是会留下孤儿结果。**~~
   **2026-09-25 已裁决：重置时连结果一起清掉，并已落地。**
   当时的实际行为是重置一场会话只清答案、不清结果行，于是「有结果、没有答卷」的会话在
   **按结果说话的报表**里仍然算数，而它的答卷已经不在库里了。

   **用户裁决：连结果一起清。** 落点是 `assessment_service.reset_sitting`（+ 判据
   `_touched_risk_signals`），`api/v1/assessment.py` 的 `reset_session` 缩成一个薄壳
   （取会话 → `reset_sitting` → commit）。清的是**这一场自己的东西，一样不多**：

   | 清 | 不清（刻意） |
   |---|---|
   | `assessment_answer` / `dimension_result` / `assessment_result` / `risk_event`（**只清没人碰过的那些**） | 关怀档案、人工复核、跟进记录、家庭回访、复测计划——**一个字都不动** |
   | 会话九列回退（`status` / `submitted_at` / `duration_seconds` / `tested_at` / `tested_at_source` / `calculation_status` / `calculation_error` / 两个哈希列 / `idempotency_key`） | 目标行只回退 `status` 与 `completed_at`，行本身不删 |

   「那些是人的工作记录，不随一次作答的存废一起消失」与 `task_service.delete_or_void_task`
   是同一条口径（也就是你列的那条「因任务作废删除人工复核、跟进、回访或关怀档案」的禁令，
   在重置这条路上同样守住了）。

   **有人碰过就整场不清（409），而这是「连结果一起清」的直接后果**：
   `uq_risk_event_session_trigger_rule` 是 `(session_id, trigger_rule, rule_version)`，
   **状态不在键里**——一条 `REVIEWED` / `VOIDED` 的行照样占着那个三元组。结果行一删，
   重答之后 `score_session` 会重新走 `maybe_raise_risk_events` 并插同一个三元组，于是撞
   1062、学生拿到一个 500。所以这道门只有两个出路：把那条信号删掉（那是抹掉一次人工
   复核，你明令禁止），或者别清这一场——选了后者，并把原因说给用户听。
   判据 `_touched_risk_signals` 数的是**有没有人做过决定**（复核/作废写下的那两对时间戳、
   以及 `status != PENDING`），另加一条 `manual_review` 的存在性判断**兜数据库那一侧**
   （漏了会让删除在 flush 时撞 1451，用户看到的是英文 500 而不是这里那句说得清来龙去脉的
   409）。

   **两条已知代价，如实记**：

   - **被 409 拦住的学生不能重答**——`create_or_get_session` 会把既有会话原样返回给他。
     要让那种学生重来，出路是心理老师那边重开一场任务（或新建一场），不是重置。
   - **导入的场次仍然不能走这条路**，但**理由换了**：从前是「删了答案会留下一条没有答卷
     的重点关注」，现在是「这一场本来就不该被重来，而 reset 会连同它的评分事实一起清掉
     ——学生手里并没有这份答卷可以重新答一遍」。`test_assessment_import_api.py` 里那条
     用例的注释与模块 docstring 已按新口径改写。

   **守卫是新的 `backend/app/tests/test_session_reset.py`（4 条）**：
   `test_a_retake_after_reset_is_scored_from_the_new_answers`（断 `answered_count == 0`、
   四张表计数全零、九列全回退、重答后分数与第一遍**不同**、维度结果仍是 8 行）、
   `test_a_retake_can_raise_the_same_risk_signals_again`（两轮都答 85/97，**永不撞 1062**
   ——这条是「为什么必须整场清」的可执行形式）、
   `test_reset_is_refused_and_changes_nothing_once_a_signal_was_reviewed`（走真人工复核
   路径 → 409 且什么都没改；把两对时间戳清掉只留 `manual_review` 那一行 → 仍然 409，
   钉住兜底那一条）、
   `test_a_result_without_a_submission_timestamp_is_re_stamped`（自己造出那个状态）。

   **变异验证 3/3 全红，每条都 `cp -p` 落盘备份 + `cmp` 逐字节还原**：

   | 变异 | 结果 |
   |---|---|
   | 摘掉 `delete(RiskEvent)` | `1 failed, 3 passed`，报的正是 1062 `Duplicate entry '2-KEY_QUESTION_85_YES-MHT-RULE-1.1.2'` |
   | `touched = _touched_risk_signals(...)` 改成 `touched = 0` | `1 failed, 3 passed` |
   | 摘掉 `delete(AssessmentResult)` | `2 failed, 2 passed` |

   **顺带修正一处旧断言**：`test_student_profile_and_duration.py` 的
   `test_a_reset_retake_re_stamps_the_submission` 里那句
   `assert data["result"] == first.json()["data"]["result"]` 删掉了——「重考拿回同一份分」
   是**旧行为下才成立**的幂等保证，新行为下它恰好是这次要修掉的那个东西。改成
   `data["result"] is not None` + 时间戳与用时非空；**它守的东西一直是同一个**：重考完的
   会话不能没有用时。
4. ~~**`ReportExportPage` 的空态文案说得不对。**~~ **2026-09-25 已按既有约定改掉。**
   库里一个任务都没有时，页面上写着「请选择测评任务后点击查询」——而那一刻**没有任务
   可选**（下拉是空的）。这是一句**做不到的指示**（CLAUDE.md §14：空态是一句关于数据的
   话，不是一次操作的占位）。**这一条没有问你**：改法是仓库里现成的约定（§14 那条），
   两个方向都能做（改文案 / 置灰按钮），于是**两件一起做了**：

   - **文案**：五个报表页（`ReportExportPage` / `DimensionsPage` / `GradesPage` /
     `OverviewPage` / `ClassPortraitPage`）都改成「本学年还没有可用的测评任务」；
   - **按钮置灰**：`FilterBar.vue` 的「⌕ 查询」加上 `:disabled="!tasks.length"` 与 `:title`
     说明为什么——**这一处只能在 `FilterBar` 里做**，那五个页面共用它，
     所以置灰一次五个页面都生效（也正因如此，「只在导出页置灰」是做不到的）。

   **顺带修掉同一处一个更实的问题**：`loadTasks` 此前是 `catch { tasks.value = [] }`，
   **失败与「一个任务都没有」落进同一个空数组**——于是「本学年还没有可用的测评任务」这句
   **关于数据的断言**，在一次网络故障时也照样出现（§14 那条「一次失败的读取不许冒充空态」
   的正面）。现在失败走 `tasksError`（下拉里出错误原文 + 一个「重试」按钮），空列表才走
   那句话；两种情况下按钮都置灰。

   这一条**没有 e2e 用例**：要造出「一个可用任务都没有」的库得清空任务，而那是共享演示库
   （「跑 e2e 不许改掉操作员自己配的东西」）。它改的是文案与一个 `:disabled`，
   **且这条链在 e2e 里的既有用例走的都是「有任务」那一支**，所以那几支不受影响。
5. ~~**新版本要不要重算统计快照？**~~ **2026-09-25 已裁决：重算，并已落地。**
   当时的实际行为是 `new-version` **只换得了四段文本**——它复制的就是
   `report.statistics_snapshot_json`，所以「创建新版本」拿到的数字与上一版一模一样，
   而一份三个月前的报告点「新建版本」，新版本上写的还是三个月前的数。
   规格的措辞是「历史报告读取 snapshot，禁止实时重算覆盖旧数字」——它管的是**读**，
   没说新版本该不该**重新取一次数**，所以当时没有自行决定。

   **用户裁决（2026-09-25）：新版本重新统计快照。** 读法是「创建一个新版本」在用户的
   心里就是「数据变了，重出一版」；而当时那种做法换来的只有四段文本。落地与它守住的
   边界（都在 `reporting_service.new_version` 的 docstring 里）：

   - **重取，不是照抄**：`snapshot = jsonable_encoder(analytics_report(...))`，
     与 `create_report` 同一处取数；
   - **与「禁止实时重算覆盖历史数字」不冲突**：规范禁的是**读**的时候重算
     （`report_document` 仍然是纯读的，一个字没动），这里动的是**写**——新版自己那一行
     拿一份新快照，而 `old` 那一行从头到尾没有被碰过；
   - **两处快照一起刷新**：版本级那一份是导出的依据（`_snapshot_of` 按版本读），
     报告级那一份是列表与详情的依据（`serialize`）——只刷新前者会让详情页拿旧数字
     配新文本（CLAUDE.md §11 那条「指标卡上的数必须与它点进去的那个列表同源」）；
   - **取数失败整段不落**（异常让请求事务回滚），不会留下「文本是新版、数字没更新」的
     半成品版本。

   **守卫**：`test_reporting_api.py::test_a_new_version_recomputes_the_statistics_it_inherits`
   （四条断言缺一不可）。它同时钉住「新版的数是新的」与「**旧版那一行一个字没动**」
   ——后者从库里重读（`db_session.expire_all()` 之后）而不是读身份映射里那个对象，
   所以「就地改掉旧行那一列」的实现要在这里现形。

   两条写这条用例时踩到的事，都记在它的 docstring 里：

   - **驱动数据的那一列必须是「导出文件里真的会变」的那一个**。第一版改的是
     `total_level`（既有那条快照冻结用例改的就是它），而导出文件里的「关注人数」印的是
     `overview.signal_student_count`——那个数**数的是 `risk_event` 的行**，与等级无关，
     于是断言收到 `'0 人' == '1 人'`。改成 `validity_status = "RETEST_RECOMMENDED"`
     （它推的是 `sample_quality.validity_flagged_count`，导出文件里印成「效度提示 N 人」）
     才对。**先确认那个数真的会动，再拿它当判据。**
   - **版本级那一份只能从导出文件里观察**：详情页与列表读的是报告级那一份
     （`serialize`），所以「报告级刷新了、版本级没刷新」这个实现**在页面上看不出来**。
     用例因此按版本各导一次（v2 的文件里是新数、v1 的是旧数）。

   `_snapshot_of` 的 docstring 里那段「今天读不出差别、所以不为它补一条恒绿的守卫」
   随之改成了「裁决之后这个状态可构造了」并点名这条用例——**它此前引用了一条不存在的
   守卫**，而这条用例正是把它补上。
6. ~~**`DRAFT / PUBLISHED / ARCHIVED` 的第三档没实现，`LEADER_MANAGEMENT_SUMMARY`
   这个导出类型也没有建。**~~ **2026-09-25 已裁决：两个都保持不做。** 两处当时都不是漏了：
   - `ARCHIVED`：规格只给了三个状态**名**，没给「什么时候归档」这条转换。凭空造一个
     没有任何写入方的状态，就是一条永远不可达的分支——CLAUDE.md 的「不静默猜测」
     与「一条恒绿的守卫比没有更糟」都不允许。**要它的话请说一句「谁能归档、归档之后
     还能不能看」**，两句话就能落地。
   - `LEADER_MANAGEMENT_SUMMARY`：领导导出的是**同一份已发布版本**（同一个
     `PROFESSIONAL_REPORT` 作业、同一份文件），再建一个类型等于把同一份文件按角色
     复制一遍，而两份会长歪（CLAUDE.md §3「同一个东西在一屏上不能有两个名字」的镜像）。
     规格里那一句写的是「**按需要**支持」——**判定为不需要**。

   **用户裁决：两个都保持不做。** 于是第 6 条**零代码改动**，落点就是上面这两段理由
   ——它现在是一条**裁决记录**，不是一条待办。要重新打开它，需要的新信息还是那两句
   （「谁能归档、归档之后还能不能看」）；`LEADER_MANAGEMENT_SUMMARY` 那一档则要等
   「按需要」那个需要真的出现（比如领导与心理老师要看的两份文件**内容不同**），
   **而不是先建一个类型再去找它的用法**。

### 5.10 第二轮：版本号 + §5.9 余下五条 + 出包（2026-09-25）

用户的三步指示逐字是：**「1. 版本号升到2.0.0  2. 没懂是什么意思  3. 解决2后出包」**。
第 2 步（把 §5.9 那五条用平实语言讲清楚）促成四个 AskUserQuestion 决定，第 1 步与第 3 步
分别是**升版本**与**出包**。本节记这三件事的落点，也是这一轮的收尾索引。

两条提交：**`555674b`（升版本，独立）** 与 **`624d3d9`（§5.9 余下五条 + e2e 那处修复）**。
拆两条的理由见下面第一节末尾。

#### 一、版本号 → `2.0.0`（独立提交 `555674b`）

`backend/app/version.py` 是**唯一出处**（CLAUDE.md §19），所以这次只动五处，一处都不是
"顺手改的"：

| 落点 | 改成什么 | 谁在读它 |
|---|---|---|
| `backend/app/version.py` 的 `__version__` | `1.1.6` → `2.0.0` | 唯一出处 |
| `frontend/package.json` | 同上（**唯一的镜像**） | 测试盯着 |
| `test_app_version.py` 的判据字面量 | `V1.1` → `V2.0` | 它是「修订号被丢掉」这句话的可执行形式 |
| `backend/sql/upgrade_from_v1_0_0.sql` | 版本头一行 | `make db-upgrade-sql` 重跑一次 |
| `backend/sql/seed_mysql8.sql` | 版本头一行 | `make db-seed-sql` 重跑一次 |

后两份是**生成**的（两个生成器都是正则读 `version.py`、不走 import），而且都是**逐字节
守卫**（§30 / §31）——不重跑就会红；改完各只差一行版本头，没有别的漂移。
`VERSION_LABEL` 由它派生 → **`V2.0`**（界面页脚、`/public/branding`），
产物名走完整三段 → **`心晴部署包_V2.0.0.zip`**。

定向验证（不是全量）：`test_app_version.py` + `test_incremental_upgrade_sql.py` +
`test_seed_sql.py` 共 **16 passed / 0 failed**，其中含「把 `seed_mysql8.sql` 真导进一个
空库」那一条。

**为什么它是一条独立提交**：升版本在这条线上一直是一个**独立动作**（§5.7 末尾那段），
与功能改动混在一起会让「这一版是什么」与「这一版改了什么」在同一条轨迹里说不清。

#### 二、§5.9 余下五条：落点索引

四条由用户在 AskUserQuestion 里点选，第 4 条按仓库既有约定直接改（当时对用户写明了
「这一条我打算直接按约定改……因为它有现成的仓库约定可依，不算猜」）。**没有一条是猜的。**

| 条 | 裁决 | 生产改动 | 落点 |
|---|---|---|---|
| 1 | 撞号就重试 | **有** | 新模块 `services/numbering.py`；接线 `task_service.create_school_assessment_task` / `reporting_service.create_report`；守卫 `test_numbering_concurrency.py`（2 条） |
| 2 | 学生侧完全隐藏 | **零** | 服务层本来就带 `active_task_predicate()`，缺的是覆盖 → `test_task_governance.py` 两条新用例 |
| 3 | 重置连结果一起清 | **有** | `assessment_service.reset_sitting` + `_touched_risk_signals`；`api/v1/assessment.py` 的 `reset_session` 缩成薄壳；守卫 `test_session_reset.py`（4 条） |
| 4 | 空态文案按既有约定改 | **有**（前端） | 五个报表页各一行 + `FilterBar.vue` 的 `:disabled` / `:title` / `loadTasks` 失败分支 |
| 6 | 两个都保持不做 | **零** | 只落成裁决记录（`ARCHIVED` / `LEADER_MANAGEMENT_SUMMARY`） |

第 1 / 3 条各自的「四条不能动的」与「清 / 不清对照表」写在 §5.9 那两条里，不在这里重抄。

#### 三、全量回归实测数

| 项 | 基线（上一轮末） | 本轮实测 |
|---|---|---|
| Backend Tests | 839 passed | **847 passed / 0 failed / 513.30s** |
| E2E | 144 passed | **144 passed (33.7s)** |
| 前端 build | 通过 | 通过（本轮前端只改了两处文案与一个 `:disabled`） |

后端 +8 = `test_session_reset.py` 4 条 + `test_numbering_concurrency.py` 2 条 +
`test_task_governance.py` 2 条，一条不多一条不少。**e2e 数不变是对的**：本轮 e2e 改动
全是注释（`resetStudentSession` 的 docstring 与导出那一组的一段说明），一条用例都没加。

**`resetStudentSession` 那句判据是跑出来的**：它的 docstring 写着「全量 e2e 里没有任何
用例依赖那一份分（演示数据里还有二十多名学生），这一点是跑出来的，不是推出来的」——
本轮那次 `144 passed` 就是跑出来了。**这句话从此有实测背书**，不再是推测。

#### 四、★ 那一处只有跑一遍才会现形的：删掉 `serial` 之后红了一条

**这是本轮唯一一处「计划外」的改动，也是唯一一处只有真跑一遍才能发现的东西。**

`§5.9` 第 1 条修好之后，我把 §5.1 那一组的 `test.describe.configure({ mode: 'serial' })`
删掉了——串行当初是为撞号加的，修好之后它就是一条**会掩盖真回归的绕过**。删掉之后
全量 e2e 当场 `1 failed / 143 passed`：

```
测评任务的删除与作废治理 › 产生了测评事实的任务只能作废，影响预览逐条说出保留了什么
Error: 这一场没有可作废的事实，这一条失去了对象
  Expected: false    Received: true      (app.spec.ts:3586)
```

**红的原因不在被测代码，在那一句挑靶子的启发式**：它按 `total_targets` 挑「目标最多的
那一场」，而这一组里另两条用例会**并发**建一场新任务（Playwright 默认 3 workers），
新任务的目标数是「范围内全部学生」——与演示那一场**一样多**（实测两边都是 56）——
于是"目标最多的那一场"能指到那个**刚建出来、一场都没答过**的空任务上，而 `canHardDelete`
对它当然是 `true`。探针确认过判据：`task_delete_check` 的 `hard` 要求
`session_count == result_count == committed_count == risk_count == import_batch_count == 0`，
而演示那一场是 `sessionCount=49 / resultCount=48 / careCaseCount=20`。

**处置：判据从"配置"换成"事实"**——按 `completed_targets`（有学生真的答完了）排、
并且先过滤掉 `completed_targets === 0` 的那些。空任务恒为 0，所以这个次序在并发下稳定。

```ts
const target = [...items]
  .filter((t) => t.completed_targets > 0)
  .sort((a, b) => b.completed_targets - a.completed_targets)[0];
expect(target, '列表里没有一场完成过任何学生的任务，这一条失去了对象').toBeTruthy();
```

复跑 **144 passed (33.7s)**。

**这一条要连它的形状一起记**：`serial` 是**为撞号加的**，而它顺手遮住了这一组的**第二处**
耦合；也就是说「删掉串行之后全绿」本身不足以证明那一组没有别的耦合，而删掉它**恰好是
唯一能让那第二处现形的动作**。CLAUDE.md §18 那条「一条会无故变红的守卫很快会被人关掉」
在这里换了个方向：**一条为了别的目的加进去的串行线，会把它遮住的东西一直藏到某次随机
排序变化为止**——那时它以「偶发红」的样子出现，而偶发红最容易被当成环境问题跳过。

#### 五、出包

`make deploy-package` → **`dist/心晴部署包_V2.0.0.zip`，12,093,090 字节（约 11.5 MB）**，
六步全绿（`EXIT=0`）：前端 `vue-tsc -b && vite build` 通过、31 个 wheel、包内自检五条全过、
zip 里的中文文件名都带 UTF-8 标志位。包里 `package-info.txt` 是
`version=2.0.0+20260925`；包内 `backend/app/version.py` 是 `__version__ = "2.0.0"`。

**这次没有用 `--keep`**，所以那一行的 `rmtree` 走了正常路径、目录是全新建的——
「`--keep` 会把上一版前端产物留成孤儿」那个陷阱这一次**不适用**（它是同一个版本重跑时
才会遇上的，见 §18 与该脚本 `:762` 那段）。产物名与 `version.py` 同源（`V2.0.0` 三段都在
名字里），而包里那份 `upgrade_from_v1_0_0.sql` 由出包脚本**当场重新生成**（§30 的
`step("3/6")`，排在整份拷贝之前），所以它认的是这棵树上的版本号、不是仓库里那份快照：
实测它的表头已经写着「到 **V2.0.0**（迁移 `0022_professional_reports`）」，
`seed_mysql8.sql` 的表头写着「版本：**V2.0**（2.0.0）」。

**★ 这一次的 wheel 不是从 pypi 下的，而这件事要记清楚。** 出包环境当时**连不上
pypi.org**（`SSLError(SSLEOFError(8, 'UNEXPECTED_EOF_WHILE_READING'))`，
重试 5 次后 `Could not find a version that satisfies the requirement alembic==1.20.0`），
所以第 2 步改用**本地 wheel 源**：把上一版
`dist/心晴部署包_V1.1.6/wheels/` 那 31 个文件当 `--find-links`，用
`PIP_NO_INDEX=1 PIP_FIND_LINKS=… make deploy-package` 跑完。

**这一步的正当性是一条可复核的事实，不是一句「应该一样」**：这次要装的 31 项由
`deploy/requirements.lock.txt` 一处决定，而它（连同 `backend/pyproject.toml`）
在 `V1.1.6`(`08254dc`) 与本次 `HEAD` 之间**零 diff**：

```
$ git diff 08254dc HEAD -- backend/pyproject.toml deploy/requirements.lock.txt | wc -l
0
```

所以两次的 pin 清单逐字节相同，而 pin 清单是 `locked_requirements()` 的**唯一输入**
——「跨版本复用一份 wheel 目录假设两个版本的依赖完全一样」（该脚本 `:711-714` 那句）
在这里是被**验证过**的，不是被假设的。第 5 步的**依赖闭环自检照旧跑过**
（「依赖闭环完整」那一行），31 个 wheel 一个不少。

**下一次联网出包时不必复刻这套**：它是这次网络不可达的绕行，不是新工序。要是哪天
pin 清单真的变了，上面那条 `git diff` 会当场数出非零行数——**那就必须重新下载**。

### 5.11 第三轮：规范符合性审计（2026-09-25）

用户要求「再检查下这里的需求还有哪些没实现」。做法是**逐条读规范全文（1578 行）**，
再拿源码事实逐条对：delete-check 的响应键、`active_task_predicate()` 的全部调用点、
三张能力矩阵、`professional_report` 两张表与六个端点、`EXPORT_TYPE_PROFESSIONAL_REPORT`、
五个报表页标题、`/auth/me/data-scope-summary`、以及 `window.print` / `Blob` /
`localStorage` / `qingxin-report-draft` 的全仓残留扫描。

**结论：P0-P1 六项（P0-01 / P0-02 / P1-01 / P1-02 / P1-03 / P1-04）规范条目全覆盖，
逐条落点见下面那张表。未实现的只有三处，其中两处是裁决不做、一处是待裁决的灰色地带。**

| 规范节 | 状态 | 落点 |
|---|---|---|
| §4.1–4.6 任务治理 | 已实现 | `task_service.delete_or_void_task` + `models/assessment.py` 的 `active_task_predicate()` |
| §4.7 delete-check | 已实现 | `api/v1/tasks.py` 的 `GET …/delete-check`；响应键在 `task_service` 里逐字对齐（含 `deleteMode` / `canHardDelete` / 三个 `*Count` / `warning`） |
| §4.8 删除 / 作废接口 | 已实现 | `@router.delete("/assessment-tasks/{task_id}")` + `{reason}`；**没有**另做一套 `POST …/delete`（规范允许二选一，不得同时实现两套） |
| §4.9 权限表 | 已实现 | 心理老师写；leader / admin / student 403（`test_task_governance.py` 各一条，admin 那条按角色参数化） |
| §4.10 审计 | 已实现 | 作废 / 删除各写一条，`detail` 记原因与计数 |
| §4.11 列表行为 | 已实现 | 任务列表三支筛选（默认不含 VOIDED / `VOIDED` / `ALL`）+ `TasksPage.vue` 的筛选项与分岔空态 |
| §4.12 七个入口排除 VOIDED | 已实现 | 同一个谓词在 analytics / care / export / assessment / assessment_import 五处共 15+ 个调用点 |
| §4.13 工作台完成率 | 已实现 | `care_service` 走同一谓词 |
| §4.14 防御性约束 | 已实现**且已变异验证** | `latest_result_subquery()` / `latest_session()` 上的 `task.status != VOIDED`；`test_a_voided_task_is_not_read_even_if_its_session_was_not_downgraded` 专钉「不依赖作废时更新得对」 |
| §4.15 学生可见性 | 已实现 | `test_a_voided_task_disappears_from_the_students_own_pages`（两个学生入口 + 反方向断心理老师照常可见）；心理老师侧两处 `voidedCount` 标识 |
| §4.16 IMPORTED 特别规则 | 已实现 | `test_void_imported_task_keeps_import_batches` |
| §4.17 不自动合并 | 遵守 | 全仓没有任何自动合并 / 删除历史 IMPORTED task 的代码 |
| §5.1–5.5 数据范围可见性 | 已实现 | `GET /auth/me/data-scope-summary` → `data_scope_summary()`；`test_data_scope_summary.py` 11 条（含「摘要与列表过滤器同源」那条） |
| §5.4 页面标题 | 已实现 | `routes.ts`：筛查关注概览 / 八维度分析 |
| §6.1–6.4 报告角色拆分 | 已实现 | `LeaderAnalyticsReportPage.vue`（学校心理工作分析摘要）；三张能力表逐格对齐默认矩阵；`test_reporting_api.py` 的 leader 403 编辑、只看已发布 |
| §7.1–7.5 服务端持久化 | 已实现（灰色项见下） | `professional_report` / `professional_report_version` 两表（快照冻结）+ 六个端点 + `test_reporting_api.py` 18 条（含「快照不随实时数据漂移」与「发布不重算快照」） |
| §8.1–8.5 正式报告走 Export Job | 已实现 | `EXPORT_TYPE_PROFESSIONAL_REPORT` + `POST /professional-reports/{id}/export-jobs`（支持按 `version_no` 选版本）；`window.print()` 与 `qingxin-report-draft` 全仓已无 |
| §9 术语 | 已实现 | 前端扫描只命中注释 |
| §10 迁移纪律 | 遵守 | 未新增 / 未改写历史迁移（本轮只用既有 `0020` 的 PRECHECKS，见 §5.8 item 4） |
| §12 / §13 测试与 E2E | 已实现 | 规范点名的用例逐条有对应；`make e2e` 144 passed |
| §15 DoD | 满足 | 五节逐条有落点 |

#### 未实现的三处

1. **`professional_report.status` 的 `ARCHIVED` 档**（§7.2）。**裁决不做**——§5.9 第 6 条
   「两个都保持不做」。规范给的是三档状态机，实现只有 `DRAFT` / `PUBLISHED`。
2. **`LEADER_MANAGEMENT_SUMMARY` 导出类型**（§8.2 的可选项）。同一条裁决不做。实现走的是
   规范里并排给的另一条路：「一个类型 + `report_type` 区分」——即 `EXPORT_TYPE_PROFESSIONAL_REPORT`。
3. **★ `ClassPortraitPage.vue` 的自由文本草稿存在浏览器 `localStorage`**（灰色地带，**待裁决**）。
   位置：`frontend/src/features/analytics/views/ClassPortraitPage.vue` 的 `save()` 与紧随其后的
   那一行初始化读取，键名 `qingxin-notes`，按钮回显「已保存（浏览器本地草稿）」。

**第 3 条要单说，因为它是「规范原文与它点名的对象错位」那一种，不是一眼能判的。**
规范 §7.1 列了五条问题（只存在浏览器 / 不区分用户 / 不区分任务 / 不可审计 / 不可版本化 /
换电脑即丢失），而 §7 与 §11.4 **字面点名的是「专业报告」与 `qingxin-report-draft`**——
后者（RPT-05 那一页的草稿）已经彻底移除，`ReportExportPage.vue` 现在走服务端：
`createProfessionalReport` / `saveProfessionalReport` / `publishProfessionalReport`。
而 RPT-04「班级心理维度画像」这一页上**还有一段**笔记 / 建议的自由文本，仍走 `localStorage`，
于是那五条问题对它逐条成立：

| §7.1 的问题 | 在 `qingxin-notes` 上 |
|---|---|
| 只存在浏览器 | 成立——`localStorage.setItem` |
| 不区分用户 | 成立——同机同浏览器的两位老师共用一条键 |
| 不区分任务 | 成立——键名里没有任何任务 / 班级维度，换一批统计口径读到的还是上一次那段字 |
| 不可审计 | 成立——`audit_log` 里没有它的任何痕迹 |
| 不可版本化 | 成立——只有一份，第二次保存覆盖第一次 |
| 换电脑即丢失 | 成立 |

**它是灰色地带而不是「漏了」，有两条依据：**

- 规范 §7 的落点写的是「专业报告」（RPT-05 那条线的服务端持久化），RPT-04 这一段笔记
  在规范里**没有被点名**；而 §8.5 禁的「window.print / 本地 Blob 作为正式导出」这一页
  也没有触犯（它不导出任何文件）。
- 但它属于同一族问题，且**界面上那句话（「已保存（浏览器本地草稿）」）自己说出来了**
  ——这一点是有意的，不是隐瞒。所以问题的实质是产品裁断：这一段笔记**算不算**「专业报告」
  的内容。若算，它应当并入 §7 的两张表（**需要一条 DDL**：新列或新表），
  这就越过「P0-P1 不加表」的隐含边界；若不算，它应当被明说成「本机草稿」并加一句
  「换电脑会丢」的提示，而这不加表。

**未自行实施的理由**：按执行约束第 4 条，「不静默猜测」——这一条要么动表结构、要么改产品
定位，两条都该由你裁。**在裁决之前，它保持现状（可用、但只在本机）。**

#### 顺手订正的一处文档矛盾

`§5.1` 里那条 `[ ] ~~学生本人历史默认不显示已作废任务记录~~ → 有意偏离规格：改为「显示并
标注」…**这条偏离需要你确认**` 与 §5.9 第 2 条裁决（「完全隐藏」，2026-09-25）**互相矛盾**，
已订正为 `[x]`。两处与事实不符：① 「显示并标注」描述的是**下一行**（心理老师侧）那句标识，
学生侧的两个入口一直带着 `active_task_predicate()`（裁决时记的是「零生产改动」）；
② 那个方向本身也与裁决相反。订正后指向守卫用例
`test_task_governance.py::test_a_voided_task_disappears_from_the_students_own_pages`。

### 5.12 第四轮：「测评完成明细」弹层加全屏展示（2026-09-25）

用户原话（逐字）：「**测评任务中的查看明细 按扭弹出时， 也需要有全屏展示的能力，请伦**」
（「伦」是「论」的笔误）。落点在 `frontend/src/features/admin/TasksPage.vue`。

#### 机制是现成的，缺的是把三处行内样式挪出来

`Modal.vue` 的 `expandable`（+ 受控 `expanded` / `update:expanded`）与 `styles.css` 的
`.modal-fullscreen` 一族**是 V1.1.6 的 `08254dc` 就建好的通用机制**，而在这之前只有
`DataCenterPage.vue` 的导入明细弹层用过它。所以这一轮**没有改 `Modal.vue`，也没有改
`styles.css`**——只在这个弹层上把那个 prop 接上。

接上 prop **不构成一个能用的全屏**，这是这一轮唯一的技术点：这一页的明细弹层有**三个
页签**（目标学生 / 完成明细 / 未匹配行），而三个页签各有一个 `table-wrap`，它的高度上限
此前是**写死的行内样式**（`max-height:340px`，三处）：

| 只接 prop，不挪那三处 | 结果 |
|---|---|
| 按钮、`modal-fullscreen` 类名、面板尺寸（`100vw` / `100vh`） | **全对** |
| 表格 | 仍然被行内那个 340px 夹住，下面留一大片空白 |

因为**行内样式压过任何全局规则**——`.modal-fullscreen .table-wrap` 那条写在 `styles.css`
里也够不着它。所以三处一起改成 `:style="detailTableStyle"`（一个 computed，两态之别**只有
`maxHeight` 一处**）。

**只松开上限，不给 `flex`、不给 `min-height`**：`min-height` 会让「表格铺开了没有」这条
断言变成恒真——它量的会是我们写死的那个数，而不是表格真的长了多少（`DataCenterPage.vue`
的同名 computed 上记着那次实测：两态只差 1px，260/261）。

另外两条：

- **全屏状态住在这一页**（`detailFullscreen = ref(false)`），并且在 `taskDetail()` 里
  **逐次复位**——「下一次打开时是什么样」本来就该由打开这个动作决定。
- **补发预览弹层那一处 `max-height:260px` 刻意不动**（它是另一个弹层、另一件事）。

#### e2e：新增 1 条，`make e2e` 144 → **145 passed (40.0s)**

`e2e/app.spec.ts` 的「布局完整性」组里新增 `测评完成明细弹层可以铺满视口展示`，与
V1.1.6 那条导入明细的样板条是**同一套机制、同一组计算值判据**（`getComputedStyle`，
不是文案），所以理由不重复。这一条存在的理由是上面那处**这个弹层独有的接缝**。四处设计取舍：

| 取舍 | 为什么 |
|---|---|
| **三个页签逐个量 `maxHeight`** | 那三处在模板里是三次独立编辑，只改一处时另外两个页签仍被夹住——只量默认页签的话那种半成品是全绿的 |
| `tableHeight > 340` **只在「目标学生」上量** | 它是唯一**行数不依赖学生交没交卷**的一屏（目标行发放时一次生成）；另两屏在演示库上可能是空的，而空表格天然矮于 340，拿它做对照会变成数据依赖 |
| 任务**从接口现取，且只在前 10 行里挑** `total_targets` 最大的那一场 | 写死哪一场会在演示数据变一次之后红在一个与功能无关的地方；任务列表是 `:page-size="10"` 的客户端分页，翻页会多一条与主题无关的依赖 |
| 定位用 `hasText` + `.first()`，**不用 `toHaveCount(1)`** | V2.0.0 的编号重试会给重名任务加 `-2` 后缀，唯一性断言会红在一个数据问题上 |

**它在全量 e2e 里红过一次，根因是这条用例自己的竞态**（单跑时快、恰好绿）：三个页签
**各自在切过去之后才请求数据**，所以「切完立刻数行」量到的是空表格那一帧（0 行）。处置是
`expect.poll(...)` 轮询而不是一次性取值，并把「切页签 + 等表格出现 + 等行数够」抽成
`showTargetsWithRows()`。**这是「先证明有东西可扫」那条教训的第 N 次发作**，写法上与前几处
不同的是：这一次要等的是**同一个组件换页签后的重新取数**，而那一段没有 `.table-wrap` 之外的
稳定信号。

**变异验证 4/4，每条都 `cp -p` 落盘备份、改完逐字节 `cmp` 还原**：

| # | 变异 | 红在哪 |
|---|---|---|
| M1 | `detailTableStyle` 全屏那一支改回 `maxHeight: '340px'` | `表格仍然被行内那个 340px 压着` |
| M2 | **只**把三处行内样式里的「未匹配行」那一处改回去 | `全屏下还有页签被那个 340px 压着`（**另外两个页签仍是 `none`**——这正是「三个页签逐个量」的理由） |
| M3 | 摘掉 `TasksPage.vue` 上那一行 `expandable` | `这个弹层上没长出全屏按钮（Modal 的 expandable 没接上）` |
| M4 | 全屏那一支写成**等价形态**（`maxHeight:'none'` 配一个行内 `height:'340px'`） | `全屏之后表格没有越过常规态那条上限`（收到 340） |

M3 顺带补了一句**先行断言**（`await expect(expand).toBeVisible()`）：没有它，M3 的失败会
落在 `measure()` 里一句 `null.closest` 上，离真正的原因（没接上那个 prop）很远。

#### 顺带发现：两条 Analytics 用例的红与本次改动无关（数据量依赖）

这一轮第一次跑全量 e2e 时，`multiple imported batches can be selected together` 与
`overview queries selected tasks and renders aggregate results` 两条红，都超时在
`.task-option input:not(:checked)` 上。**查证后确认与本次改动无关**：它们要求任务选择器里
有 **≥2 个可见任务**（`.task-option` 全部勾选时无未勾选项可点），而共享开发库当时
`GET /assessment-tasks` 只回 **1 条**——`assessment_task` 全表 10 行里其余 8 行是 `VOIDED`
（7 条是历次 e2e 造的 `e2e作废任务-<ts>`、1 条是 `2026春季…（外部平台导入）`），
而 `active_task_predicate()` 把作废的不下发。`make seed-demo` 补出 `初三年级复测任务`
（`NOT_STARTED` / 16 人）之后，两条各自单跑与全量都绿。

**所以处置是补数据，不是改断言**（§测试注意那条：数据量依赖不是回归）。两件事记在这里：

- **每次全量 e2e 都会在共享演示库造一条 `e2e作废任务-<时间戳>`，作废后留在库里**
  （作废是软删除，`purge-demo` 之外没有回收动作）。它不影响任何断言，但表会单调增长——
  与「账号管理留下的临时账号」「`auth_session` 每次 `loginAs` 加一行」是同一类**已知且接受**
  的残留，差别是这一条会被 `active_task_predicate()` 挡在界面之外，因此更不容易被注意到。
- **「库里此刻有几场可见任务」是这两条用例的隐含前提**，而它由演示数据决定。补数据的动作是
  `make seed-demo`（幂等）。

#### 本轮改动的边界

只动两个文件：`frontend/src/features/admin/TasksPage.vue`（+38 / −3）与
`e2e/app.spec.ts`（+174）。**后端零改动**，所以本轮不涉及迁移、不涉及权限、不涉及导出。
`make test` 仍按全量重跑了一遍作回归确认：**847 passed / 0 failed / 538.01s**（与第二轮
的 847 / 513.30s 同数——用例数没动，耗时那一列只当参考，别拿它比版本）。

### 5.13 V2.0.0 产品与 UI/UX 专家复审：专业报告工作台收口（2026-09-25）

#### 5.13.1 评审边界与事实基线

本轮是**产品收口**，不是重做 P0-P1。评审依据：实际运行的心理老师工作台与专业报告页、
当前源码、现有 API、Backend Tests、E2E，以及 `V2.0.0` 当前工作区。外部 Claude 插件接口
尝试两次均因其 CLI 参数故障返回空结果；随后由独立只读审查代理完成第二意见复核，并发现
“历史版本不可读”与“新草稿导致旧发布版对 leader 下架”两个 P0 架构缺口。下面每一项均由
源码或实际页面直接证明。

版本事实（记录时）：

| 项 | 值 |
|---|---|
| 本地分支 | `V2.0.0` |
| 本地 HEAD | `0d3be3334bdb32d57e9321514718a1bf8487cf44` |
| `origin/V2.0.0` | `88485add688c0132b2c7472e0016982070683d58` |
| 差异 | 本地 ahead 4；另有未提交工作，禁止覆盖 |
| 本轮复查 | `frontend npm run build` 通过；新增“测评完成明细全屏”E2E 单跑 1 passed |

底层能力已经存在，**禁止重复建设**：

- `GET/POST /professional-reports`、单份详情、保存草稿、创建新版本、发布、按版本创建 Export Job；
- `professional_report` / `professional_report_version` 与版本统计快照；
- counselor 编辑/发布、leader 只读已发布报告的能力矩阵；
- 既有 Export Job、用途、审计、有效期和下载流程。

当前缺口不只是前端没有把 `listProfessionalReports` / `getProfessionalReport` /
`newReportVersion` 接到心理老师页面，还有两个必须先补的后端问题：

1. 单份详情只返回当前版本正文/快照；`versions[]` 只有版本号、创建时间、创建人，历史正文不可读。
2. `new_version()` 会把报告头从 `PUBLISHED` 改回 `DRAFT`，而 leader 可见性按报告头状态过滤；
   因此 V2 编辑期间，已发布 V1 也会从领导端消失。版本表没有独立的发布状态/时间/发布人。

下一轮仍复用现有两张报告表与 Export Job，但必须用增量 migration 与版本详情 API 补齐这两个缺口。

#### 5.13.2 优先级与裁决

| ID | 优先级 | 类型 | 裁决 |
|---|---|---|---|
| RPT-UX-01 | P0 | 确认缺陷 | 专业报告状态必须来自服务端，禁止再用提示文字推导 |
| RPT-UX-02 | P0 | 功能闭环 | 接入已有报告列表、详情、草稿恢复和继续编辑 |
| RPT-UX-03 | P0 | 功能闭环 | 接入已有新版本 API 与版本历史；已发布版本只读 |
| RPT-UX-04 | P0 | 高风险交互 | 发布前确认、未保存离开保护、切任务保护 |
| RPT-UX-05 | P0 | 导出交互 | 移除 `window.prompt`，改用系统内用途确认弹层 |
| RPT-UX-06 | P0 | 数据/权限正确性 | V2 草稿期间，leader 必须继续看到并可导出已发布 V1 |
| LEADER-UX-01 | P1 | 阅读体验 | 领导页默认选中最新报告，并补齐元信息、选中态与空态 |
| CLASS-NOTE-01 | P1 | 数据正确性 | `qingxin-notes` 不得继续用不区分用户/任务/班级的单一键 |
| NAV-UX-01 | P1 | 一致性/可访问性 | 字符图标统一替换为 SVG 图标组件 |
| WORKBENCH-UX-01 | P1 | 信息架构 | 工作台突出今天/逾期/待复核，未来提醒折叠 |
| REPORT-ARCHIVE-01 | P2 | 增强 | 报告归档/恢复，本轮不做 |
| REPORT-FORMAT-01 | P2 | 增强 | 正式 PDF/DOCX，本轮不做；继续复用现有 CSV Export Job |
| AUTH-HARDEN-01 | P2 | 架构/安全 | token 从 `localStorage` 迁移 HttpOnly Cookie，单独立项 |

#### 5.13.3 Phase A（P0）：心理老师专业报告生命周期

主改文件：

- `frontend/src/features/analytics/views/ReportExportPage.vue`
- `frontend/src/services/api.ts`（补齐真实响应类型与历史版本详情函数）
- `backend/app/models/reporting.py`、`backend/app/services/reporting_service.py`、`backend/app/api/v1/reporting.py`
- 新增一个增量 Alembic migration，为版本行补发布状态与发布元数据；禁止修改 `0022`。
- 可新增 `frontend/src/features/analytics/components/ProfessionalReport*` 小组件；禁止复制整页。
- `e2e/app.spec.ts`

实现要求：

- [ ] `professional_report_version` 增加版本级 `status/published_at/published_by`；现有已发布报告的
  当前版本回填为 `PUBLISHED`。不能证明发布人的历史数据允许 `published_by = null`，禁止猜值。
- [ ] 新增受现有 `PROFESSIONAL_REPORT_READ`、data scope 与审计保护的版本详情接口，例如
  `GET /professional-reports/{report_id}/versions/{version_no}`，返回该版本正文与统计快照。
- [ ] `new_version()` 只创建新的 `DRAFT` 版本，不能让已发布旧版本对 leader 下架。
- [ ] leader 列表/详情/导出按“最新已发布版本”授权；counselor 可见自己的当前草稿及历史版本。
- [ ] 发布只发布当前草稿版本，记录版本级发布时间/发布人；旧版本只读保留。
- [ ] 补齐 `ProfessionalReport` 前端类型：`task_scope`、创建/更新时间、发布时间、创建人/发布人、
  当前编辑版本与最新已发布版本的明确口径。
- [ ] 页面加载统计任务后，同时读取当前用户可见报告列表。
- [ ] 提供“我的报告”区域，至少显示：标题、报告编号、任务范围摘要、状态、当前版本、更新时间。
- [ ] 可打开既有草稿；必须调用详情接口恢复四段正文与真实统计快照，不能重新创建一份。
- [ ] 同一 owner + `analysis_mode` + 排序去重后的 task ID 集合存在草稿时提示“继续编辑现有草稿”，
  不得静默重复创建；保存中禁用按钮，防双击。
- [ ] 若“一种口径只允许一份草稿”是强业务不变量，必须由后端幂等/唯一策略保证并补并发测试，
  不能只依赖前端先查列表。
- [ ] 状态模型使用服务端字段：`未创建 / 草稿·Vn / 已发布·Vn`；删除
  `status ? '草稿' : '待审核'` 这一推导。
- [ ] 保存成功后保留服务端返回的 `id/report_no/status/current_version/content/versions`。
- [ ] 已发布版本全部输入框只读；“保存草稿”与“确认并发布”不可继续对其操作。
- [ ] 已发布报告提供“基于当前版本继续编辑”，调用既有 `/new-version`；新版本正文按服务端返回值加载。
- [ ] 显示版本时间线，至少包含版本号、创建时间、当前/历史标识；历史版本只读。
- [ ] 导出允许选择真实存在的版本号，并传给既有 `exportProfessionalReport(id, purpose, versionNo)`。
- [ ] 不因打开历史版本重算统计；创建新版本按既有后端裁决重新生成快照。

版本状态机必须按下面实现，不自行增加其他状态；leader 可见性依据版本级 `PUBLISHED`，不能再
只看报告头总状态：

```text
未创建 --保存--> DRAFT V1 --发布--> PUBLISHED V1
                                      |
                                      +--创建新版本--> DRAFT V2 --发布--> PUBLISHED V2
                                                               （V2 草稿期间 V1 仍可见/可导出）
```

验收标准：

- [ ] 刷新页面、退出再登录、换浏览器后仍能找到并继续草稿。
- [ ] 发布后 KPI/徽标显示“已发布·Vn”，不再显示“草稿”。
- [ ] 发布后文本框只读，直接保存按钮不存在或 disabled，并说明“修改需创建新版本”。
- [ ] V1 与 V2 均能回看；选择 V1 导出时文件和审计均标识 V1。
- [ ] V2 处于草稿期间，leader 仍看到 V1 且只能导出 V1；V2 发布后才默认切到 V2。
- [ ] 报告列表不会因为重新查询同一任务而自动新增记录。

#### 5.13.4 Phase B（P0）：防丢失、发布与导出确认

实现要求：

- [ ] 建立 `dirty` 判据：当前四段正文与最后一次成功保存的正文不同即为未保存。
- [ ] 切换任务、打开另一份报告、重置筛选时，dirty 使用现有 `ConfirmDialog`；Vue 路由离开
  使用 `onBeforeRouteLeave`；浏览器刷新/关闭使用 `beforeunload`。三种边界分开实现与测试。
- [ ] 保存中禁止重复提交；显示“保存中 / 已保存 HH:mm / 保存失败”。
- [ ] 发布按钮文字包含版本号，如“发布 V2”。
- [ ] 发布确认弹层明确：发布后锁定、领导端可见、后续修改必须新建版本。
- [ ] 发布失败保持 dirty 与当前正文，不能清空输入框。
- [ ] 删除 `window.prompt('请输入导出用途')`。
- [ ] 用项目现有 `Modal` / `FormDialog` 实现导出确认；标准用途读取已有系统配置，不新增第二套用途常量。
- [ ] 弹层展示：报告编号、版本、任务范围、文件格式、强制隐私保护、有效期、审计提示。
- [ ] 用途为必填；标准用途可选，必要时提供受长度限制的补充说明。
- [ ] 创建/下载失败显示可恢复错误；成功后明确“已下载，可在导出中心查看”。
- [ ] 页面中的“导出记录”不得只依赖当前组件内的 `lastExportAt` 冒充历史记录。当前 Export Job
  没有可直接按 report id 查询的结构化关联，本轮裁决为把标题改成“本次操作结果”，不扩第二套关联。

#### 5.13.5 Phase C（P1）：德育领导只读报告中心

主改文件：`frontend/src/features/leader/LeaderAnalyticsReportPage.vue` 与 E2E。

- [ ] 有报告时默认选中更新时间最新的一份，禁止右侧无原因空白。
- [ ] 列表项提供清晰选中态与键盘焦点态，并设置合适的 `aria-pressed` 或选项语义。
- [ ] 展示报告编号、版本、发布时间、发布人、任务范围、可评价样本数；发布人来自版本级
  `published_by` 关联的账号显示名，不能拿 `created_by` 冒充。
- [ ] 支持按任务/学期或关键字过滤；首版可只做客户端过滤，不新增后端接口。
- [ ] 区分两个空态：“学校尚未发布报告”与“当前筛选无匹配报告”。
- [ ] leader 仍然只读，不出现保存、发布、新版本按钮。
- [ ] 如提供导出按钮，必须复用专业报告 Export Job，并只允许已发布版本。

#### 5.13.6 Phase D（P1）：班级画像笔记持久化裁决

确认缺陷：`ClassPortraitPage.vue` 当前把所有用户、所有任务、所有班级共用一条
`localStorage['qingxin-notes']`，会串数据并覆盖。

专家复核裁决：它不是四段“学校专业分析报告”的同一份正文，默认新建独立
`class_portrait_note`，避免把班级工作笔记硬塞进报告版本字段。AI Coding 不再自行选模型。

- [ ] 新增 `class_portrait_note` 与增量 Alembic；禁止修改历史 migration。
- [ ] 明确“一位老师 + 一个学校 + 一组排序去重后的 task IDs + 一个 class_id”至多一个当前草稿；
  保存为 upsert，不做历史版本（若以后需要版本化另立项）。
- [ ] 字段至少包含：school_id、class_id、task_scope_json、notes、suggestions、created_by、updated_by、timestamps。
- [ ] 保存、读取均执行现有 data scope；任务集合必须全部属于可见有效任务。
- [ ] 写入与读取纳入审计；不得让 leader 看到未发布草稿。
- [ ] 迁移前浏览器旧草稿不自动上传，避免把来源不明的同机共享内容写入某位老师账户。
- [ ] 首次检测到旧键时只提示“发现此浏览器旧草稿，可复制后手工保存”，并提供明确清除动作；清除需确认。

不再提供仅改 localStorage key 的降级方案；那仍不可审计、不可跨设备，不能解决正式产品问题。

#### 5.13.7 Phase E（P1）：设计系统与信息密度

本 Phase 是第二批，不得阻塞 Phase A/B/C 的报告闭环交付。

- [ ] 建立一个 SVG 图标组件/映射，替换 `AppLayout.vue`、`KpiCard.vue` 中的字符图标。
- [ ] 图标统一 20/24px viewBox、线宽、对齐；装饰图标 `aria-hidden=true`，纯图标按钮必须有可读名称。
- [ ] 不使用 emoji/Unicode 几何字符充当正式功能图标。
- [ ] 保留当前可见 `:focus-visible`；交互项必须具备 hover、active、disabled 状态。
- [ ] 工作台首屏优先呈现今天、逾期、待复核；未来 7–30 天提醒默认折叠或只展示摘要。
- [ ] 列表默认显示 5–8 条，保留“查看全部”；不能删除现有工作数据。
- [ ] 375/768/1024/1440px 四档验证侧栏/底栏、顶栏和报告页面；页面不得横向溢出。
- [ ] 375–480px 顶栏把“登录设备/修改密码/退出”合并进账号菜单，避免挤压标题。
- [ ] `prefers-reduced-motion` 与键盘导航继续保持有效。

#### 5.13.8 测试清单（修改完成前不得宣称交付）

Backend Tests（优先复用 `test_reporting_api.py`，只为新增服务端行为加测试）：

- [x] 保留既有 counselor 列表隔离、leader 只读已发布、发布锁定、快照冻结、按版本导出的回归用例，
  不为了凑数量重复建设同义测试。
  （`test_reporting_api.py` +318 行；越权面 `:344 / :363 / :577 / :631 / :671 / :793 / :833` 逐条在位。）
- [x] 新增历史版本详情：正文/快照正确、越权 404/403、读取有审计。
  （`test_the_version_detail_api_returns_that_versions_own_text_and_snapshot:700`、
  `test_reading_a_version_writes_audit:750`、`test_a_leader_cannot_read_a_draft_version_even_by_asking_for_its_number:793`。）
- [x] 新增 V2 草稿期间 leader 仍可读/导出 V1；V2 发布后 leader 默认看到 V2。
  （`test_a_new_version_keeps_the_published_one_visible_to_the_leader:631`、
  `test_the_leader_gets_the_new_version_once_it_is_published:671`。）
- [x] 新增版本级 `published_at/published_by` 写入与历史数据回填测试。
  （`0023_report_version_publish.py` + 新文件 `test_report_version_publish_migration.py`：
  `test_the_backfill_fills_the_versions_that_have_been_published:177`、
  `test_the_backfill_leaves_the_text_alone:210`。）
- [x] ~~如实施“同口径一份草稿”后端不变量~~ **未实施，故不适用**——本轮没有引入该不变量
  （`reporting_service` 里没有既存草稿的查找/upsert）。双击/双请求仍会建出两份草稿，这是
  **本轮知情的取舍**，不是漏做：它是一条新业务规则（「同一口径至多一份草稿」），
  不在 P0 六项里。要做得单独立项。
- [x] ~~如实施班级笔记服务端化~~ **未实施，故不适用**——见 §5.13.6，该 Phase 仍待裁决
  （§5.13.11 的「未完成」一栏）。

E2E（`e2e/app.spec.ts` 的 `专业报告工作台` 组，+953 行）：

- [x] 新建草稿 → 刷新 → 从列表恢复 → 继续编辑。（`:1285`）
- [x] 发布 V1 → 页面显示已发布、输入只读 → 创建 V2 → 编辑并发布。（`:1314`）
- [x] V2 草稿未发布期间，leader 仍能查看并导出 V1，看不到 V2 草稿。（`:1351`）
- [x] 在 V2 页面选择 V1 导出，验证下载和导出中心记录。（`:1380`）
- [x] dirty 时切任务、打开其他报告、重置、离开页面均出现保护；保存后不出现。（`:1410`）
- [x] 导出用途弹层的必填、取消、失败保留和成功反馈。（`:1465`）
- [x] leader 登录后默认打开最新报告，且没有任何编辑控件。（`:1533`）
- [x] leader 无报告/过滤无结果两类空态。（`:1565`）
- [x] 375px 与 768px：报告列表、正文、版本时间线、确认弹层和底部导航无横向溢出。（`:1600`）
- [x] 键盘可完成选报告、保存、发布确认和导出用途填写。（`:1630`）

执行回归：

```bash
cd backend && source .venv/bin/activate && python -m pytest -q
cd ../frontend && npm run build
cd .. && npx playwright test
```

若共享演示库可见任务少于 2 条，先运行 `make seed-demo`；禁止为了让测试变绿而放宽真实产品断言。

#### 5.13.9 Definition of Done

- [x] P0 六项（RPT-UX-01～06）全部完成。
- [x] 页面可恢复草稿、回看版本、创建新版本、发布并按版本导出。
- [x] UI 显示的状态、版本、时间与服务端一致。
- [x] 不再使用 `window.prompt`；专业内容不再新增无范围的 localStorage 草稿。
  （`window.prompt` 归零；Phase D 未实施 → 既有那个 `qingxin-notes` **一个字未动**，
  **不是**「本轮新增了它」。见 §5.13.11 的「未完成」。）
- [x] leader 只读边界未放宽，管理员仍默认无心理业务内容权限。
  （`test_reporting_api.py:793` / `:833`；管理员那一侧未动 `CAPABILITY_DEFAULTS`。）
- [x] 新版草稿编辑期间，旧版已发布内容持续对 leader 可见，且历史版本正文/快照可追溯。
  （`test_reporting_api.py:631` / `:671` / `:700`。）
- [x] 未修改 MHT 评分规则。
- [x] 未重建权限、导入批次或 Export Job。
- [x] 未修改历史 Alembic migration。（`0023` 是**新增**的最后一条，0001–0022 未动。）
- [x] 前端 build、Backend Tests、E2E 全绿并把实测数字写回本节。
  （2026-09-26 实测，逐字见 §5.13.11 的「三项数字」。）
- [ ] 人工检查 375/768/1024/1440px 与键盘操作。
  （375 与 768 已由 `e2e/app.spec.ts:1600` / `:1630` 覆盖；**1024 与 1440 没有人看过**，
  所以这一条**不勾**。）
- [ ] 提交前记录最终 commit SHA；推送前确认不会覆盖当前未提交工作。
  （P0 六项已在 `3b6e586`；**收口后的续做改动尚未提交**，见 §5.13.11 的「未提交」。）

#### 5.13.10 明确排除

本轮不做：`ARCHIVED` 状态、PDF/DOCX、新的领导专用导出类型、权限体系重构、Export Job
重构、MHT 评分变更、历史迁移改写、自动合并/删除 IMPORTED task、token Cookie 架构迁移。

#### 5.13.11 实施记录与实测数字（2026-09-26）

##### 三项数字（§5.13.9 要求，逐字）

```
npm run build  →  ✓ built in 982ms        （vue-tsc -b 类型检查通过）
                  dist/assets/index-CX3xU0Yt.js   465.87 kB │ gzip: 147.46 kB
                  dist/assets/index-BSVH8dR5.css   63.60 kB │ gzip:  12.86 kB
                  165 modules transformed
make test      →  865 passed, 5 warnings in 525.58s (0:08:45)
make e2e       →  155 passed (45.6s)
```

**2026-09-26 收口续做 + 人工检查之后又完整重跑过一次后端**：仍是
`865 passed, 5 warnings in 540.44s (0:09:00)`——**用例数逐条一致**（+18 条仍全部落在
`test_reporting_api.py` 与 `test_report_version_publish_migration.py`），耗时 525.58 → 540.44s
只当参考、不拿来比版本（CLAUDE.md §29 那条）。
**e2e 那一次的 `155 passed` 早于续做改动，不能拿来给续做作证**（`app.spec.ts` 自己改了
133 行、`useRovingFocus.ts` 是新增文件），所以**续做之后单独补跑了一次**：
`make e2e → 155 passed (39.5s)`，与上面那一行**用例数一致、逐条全绿**。
它同时把「★ roving tabindex」那一条的 `Shift+Tab` 断言在**改后的**代码上重跑了一遍。

对照§5.13 之前：后端 847 → **865 passed**（+18 条，全部落在 `test_reporting_api.py` 与
`test_report_version_publish_migration.py`）；e2e 此前 `153 passed / 2 failed` → **155 passed**。

5 条 warning 里那两条 `cartesian product` 是**已知误报**（`export_service.py:124` /
`analytics_service.py:1015`，CLAUDE.md §23 记着三处 FROM 各有各的 ON，**别去改**），
另三条是既有告警。

##### P0 六项的落点（逐项）

| 项 | 落点 |
|---|---|
| RPT-UX-01 草稿可恢复 | `ProfessionalReportList.vue`（新）+ `ReportExportPage.vue` 的列表接线 |
| RPT-UX-02 版本回看 | `GET /professional-reports/{id}/versions/{no}` + `VersionTimeline` |
| RPT-UX-03 创建新版本 | `POST …/versions` + 页头「创建新版本」按钮 |
| RPT-UX-04 发布锁定 | `POST …/versions/{no}/publish` + 已发布态只读 |
| RPT-UX-05 按版本导出 | `exportProfessionalReport(id, purpose, versionNo)` |
| RPT-UX-06 移除 `window.prompt` | 全站归零（导出用途改走 `FormDialog`） |
| LEADER-UX-01 领导只读页 | `LeaderAnalyticsReportPage.vue`（新，`/leader/analytics/report`） |

##### ★ 列表投影分岔：`serialize(..., detail: bool)`

`GET /api/v1/professional-reports` 此前把每份报告**整份 `statistics_snapshot`** 一起下发，
而列表页一个字都用不到它（用的是服务端现取的标量 `evaluable_count`）。

修法是给 `reporting_service.serialize` 加一个 `detail: bool = False` 开关：**列表不带快照、
详情才带**。所有详情调用点（`create` / `draft` / `get_report` / `get_version`）显式传
`detail=True`，列表端点不传。

实测（2026-09-26，领导身份，`GET /api/v1/professional-reports`）：

```
HTTP=200  字节=171570   n=172
第一份的 keys 里有 statistics_snapshot 吗: False
evaluable_count = 48    content_version = 2
```

**判据取「每份约 997 B」而不是那两个总字节数**：n 会随演示数据长（上一会话记的
`5,392,953` / `120,622` 是 n=113/121 那两次），而「修前每份 51,255 B 的快照」是常量。
拿总数当判据会在下一次数据变化时读成回归。

##### ★ 删除任务的 bug：**取消 == `{}` 与 取消 == `null`** （用户 2026-09-26 报的）

用户原话：「评测任务中，点击删除，我选择了取消，但当条评测任务也被删除了」。

根因在 `TasksPage.vue` 的 `onFormCancel`：它回的是 **`{}`**，于是「点取消」与「提交了
一个空表单」在调用方眼里**一模一样**。九个调用点里有八个**恰好**是对的——每个都有一个
`required` 字段顶着，空表单会被表单自身挡住；而第九个 `removeTask` 不是：可硬删除的那
一档**根本没有字段**（`fields` 是空数组），没有任何东西挡得住，任务当场被删。

修法是**两半，缺一不可**：

1. `formResolve` 的类型与 `onFormCancel` 改成回 **`null`**——取消就是取消，调用方拿得到
   这个事实；
2. 九个调用点各补 `if (!values) return`。

**没有选「给那一档补一个假字段」那条路**：那只是把同一处判断换个地方藏起来，下一个
「不需要填任何东西就能做」的动作会再踩一次。理由写在 `onFormCancel` 的 docstring 里。

**变异验证在这里打歪过一次，值得记。** 第一发摘掉 `TasksPage.vue` 的 `if (!values) return`，
用例**仍然 greens**——因为 `onFormCancel` 回的是 `null`，`!values.reason` 对 `null` 求值
抛 `TypeError`，被外层 `catch` 吞成一条 toast，删除没发生。**那是变异写错了，不是守卫失灵**
（CLAUDE.md 记着这条）。忠实的变异是 `formResolve({})`——那才是当初 bug 的根因——改成它
之后红在 `e2e/app.spec.ts:4497`。所以两半是**同一个修复的两半**，各自单独摘掉都可能绿。

##### ★ roving tabindex：长列表只占一个 Tab 停靠点

`frontend/src/composables/useRovingFocus.ts`（新，79 行），两处列表共用：
`ProfessionalReportList.vue` 与 `LeaderAnalyticsReportPage.vue`。

共享演示库上这两份列表各有一百多行，而每一行此前都是普通 `<button>`——也就是每一个都是
Tab 停靠点，于是「Tab 进列表 → Tab 出去」要按**行数**次 Tab。`e2e/app.spec.ts` 那条键盘
用例的 `tabUntil` 上限（160）正是被它顶穿的。

四个决定：

- **工厂函数，不是模块级单例，也不是两份拷贝**。单例会把两个不相干的列表串进同一条序列
  （与 §14「竞态守卫按页构造、不做成全局序号」同一条）；两份拷贝会漂。所以是每次调用各持
  一份状态的工厂。
- **`tabbableIndex` 夹在 `[0, itemCount() - 1]`**：筛选或重新加载之后行数变小而 `activeIndex`
  停在旧值上，不夹就会出现「一行都不是 Tab 停靠点」——整张列表从键盘上消失，屏幕上完全
  看不出为什么。
- **行数传 getter，不传数字**（`() => props.reports.length`）：列表是异步加载的，传值会拿到
  旧的那个。领导那一页取的是**筛选之后**的行数。
- **焦点与选中是两件事**：方向键只移焦点，选中仍要 Enter / 空格（`<button>` 原生行为）。
  理由是这两处的选中都会触发一次**详情请求**——让方向键每经过一行都发一次请求，会把
  「翻过去看看」变成一百多次网络往返。

##### `.modal-panel` 这个定位器在弹层堆叠下**天生有歧义**

新增的那几条 e2e 一开始红在这里，成因值得单独记：Vue 的 `<Transition name="modal">` 的
leave 是 **0.2 秒**，所以关掉的弹层**在 DOM 里还要多活约 200ms**；而 Playwright 的
`toHaveCSS` 撞上严格模式冲突（两个 `.modal-panel` 同时匹配）是**立即抛错、不重试**——
于是它报出来的是一句定位器错误，而不是断言失败。写新用例时要么等那一层真的消失，要么
把定位器收到只有一层能匹配的形状上。

##### 共享演示库的 e2e 数据漂移（本会话查出来的，**已清理、未做结构性改动**）

全量 e2e 报 **5 failed**，根因不是代码：`e2e/app.spec.ts:4574` 那条用例
（`作废之后从默认列表消失，只在「已作废」里找得到`）**每跑一轮新建一批、从不清理由**，
而 `_reusable_batch` 的四条判据里有 `task_id`，任务每轮都是新的 → 永远无法复用 → **每轮
净增 1 个批次 + 1 个 VOIDED 任务 + 56 行目标行**（实测残留 17 行 `e2e作废批次-*`）。

可见窗口是 `DataCenterPage.vue` 的 `batchHistory` 的 `.slice(0, 10)`，后端按 `id.desc()`
排。残留把 6 个**被依赖的**批次挤出最新 10 条 → 依赖它们的用例定位到 0 个元素而红。

处置是**清理 + 补数据，不放宽任何断言**：备份（`/tmp/xlp-e2e-residue-20260926.json`）→
子先父后单事务删（**全库没有 `ondelete=`，每个外键都是 RESTRICT**，父行先删必然 1451）→
批次 25→8、任务 19→2。5 failed 降到 2 failed。

剩下 2 条（`e2e/app.spec.ts:587` 与 `:979`）红在选择器里只剩 1 个可勾选任务
（`.task-option input:not(:checked)` 解析到 0 个元素 → 30s 超时）。追到
`IMPORT-202605-1` 为何 VOIDED：审计 id 14112 / actor `13800000001`（心理老师）/
2026-09-25 21:42:20 → **判为手动试用功能时的一次正当作废，不是测试 bug**。
按 CLAUDE.md 既有处方跑 `make seed-demo` 补出 `TASK-2026-GRADE9-RETEST`
（ACTIVE / 16 targets）→ 那 2 条 **2 passed (1.4s)** → 全量 **155 passed (45.6s)**。

**余量已经归零**（量化）：批次总数 10 / 可见窗口 10，`e2e全屏明细`(44) / `e2e 导入校验`(45) /
`e2e词表预演`(47) 贴着窗口边缘。**下一轮全量 e2e 再净增 1 行，就会开始把 id 最小的外推。**
所以下一次跑全量之前应当再清一次，或者先做下面「未完成」里那件结构性的事。

###### 上面那句预测**已经发生**（2026-09-26 19:05 实测）

| 数 | 值 |
|---|---|
| `assessment_import_batch` 总行数 | **16**（可见窗口仍是 `.slice(0, 10)`） |
| 窗口内 `e2e作废批次-*` | **8 行**（id 70–77，时间 18:37 → 19:05） |
| 窗口内**被依赖的**批次 | **只剩 2 行**（56 `2026年心理普查结果示例`、50 `e2e词表未匹配`） |
| 被挤出窗口的依赖项 | `e2e词表汇总`(49) / `e2e词表筛选`(48) / `e2e词表预演`(47) / `e2e 导入校验`(45) / `e2e全屏明细`(44) —— **全部出界** |

**所以「跑一轮长多少」是一个依赖库当前状态的数，不能在文档里写成常数。** 两侧都实测过：

- **批次侧**：`e2e/app.spec.ts:4574` 那条每跑一轮新增 1 个批次（`_reusable_batch` 的四条判据
  里有 `task_id`，任务每轮都是新的 → 永远无法复用）。上面那 8 行就是这一会话里反复跑出来的。
- **报告侧**：`professional_report` / `professional_report_version` 从 0 跑一轮得 **25 / 41**；
  在**已有 25 份之上**再跑一轮只净增 **+12 / +20**（→ 37 / 61）。差别的原因是用例遇到
  「同范围草稿」时会点「继续编辑现有草稿」（`findSameScopeDraft` + `askConfirm`），
  那一条**不新增**。

**推测与实测要分开**：`155 passed` 那一次跑的时候窗口里还剩着它需要的那几行——**每一轮跑
都在往后推窗口，用例够不够得着取决于它执行的那一刻**，所以那次绿是**时序**而不是余量。
这一点我没有逐条取证（那要录下每条用例执行时刻的窗口快照），**不当作结论**，
只把它记成「下一次跑之前必须再清一次」的理由。

**已清（2026-09-26 19:1x，只删 e2e 自己造的残留、一行操作员数据没碰）**：批次 16 → **8**
（窗口内 8 行**全部是被依赖的那 8 个**，余量恢复成 2 轮）、任务 13 → **3**
（36 秋季普查 ACTIVE / 38 春季导入 VOIDED / 82 初三复测 ACTIVE）、目标行 667 → **107**。

删除照既有处方：**先物化 id 列表再删**——`DELETE … WHERE batch_id IN (SELECT id FROM
assessment_import_batch WHERE …)` 在 MySQL 上是 **1093 `You can't specify target table … for
update in FROM clause`**（自己的第一版就是这么撞的，回滚了、一行没动）。子先父后
（`assessment_import_row` → `assessment_import_batch` → `assessment_target` →
`assessment_task_scope` → `assessment_task`），单事务。备份
`/tmp/xlp-e2e-void-residue-20260926.json`（480 行）。

**★ 这次我犯了一个错，如实记**：那份备份只取了「那 8 个批次所对应的 8 个任务」，
而**删除的判据是 `assessment_task.name LIKE 'e2e作废任务-%' AND status='VOIDED'`**——
它多匹配到 **81 / 84** 两个任务（以及它们名下的 112 行目标行），于是那 112 行
**删了但没有备份**（480 → 实际删除 560 行）。两个范围不一致而**宽的那个是删除**，
这正是「先备份再删」最容易失效的形状。补救与影响都如实说：旧备份
（`/tmp/xlp-e2e-residue-20260926.json`）里也**没有** 81 / 84（它的 task id 到 79 为止），
所以那 112 行**确实没有第二份副本**；它们是 `e2e作废任务-*` / `VOIDED` 的 e2e 自造残留
（那一条用例每跑一轮就造一份），**下跑一轮就能重新造出来**，没有任何操作员数据在里面。
**教训是：备份的判据与删除的判据必须**逐字**相同**，不能一个是「这批 id 对应的」、
另一个是「按名字前缀的」。

##### 另外两处判据

- **不要用行数做断言**：新增的那十条用例都按「有东西可扫」定位（先证明那一行在，再断言它
  干净），不写死「共 172 份」——报告数会随演示数据长。
- **`uvicorn --reload` 在 e2e 期间改后端会挂死**：跑全量 e2e 的过程中动了 `backend/`，
  reload 会在用例中途重启进程，表现出来是一批看不懂的超时。跑 e2e 时别改后端。

##### 人工检查 375 / 768 / 1024 / 1440（**由助手读图 + 量化，不是真人在浏览器里操作**）

§5.13.9 的第二条要求「人工检查 375/768/1024/1440px 与键盘操作」。**执行方式如实记**：
辅助脚本（`/tmp/xlp-shot.cjs` / `xlp-shot2.cjs` / `xlp-measure.cjs` / `xlp-table.cjs` /
`xlp-opcol.cjs`）在真服务上取景，**图由我逐张读**，凡涉及「有没有被挡住 / 有没有变形」的
判断一律**回到 `getBoundingClientRect` 的数字**，不靠缩略图。所以它是「机器取景 + 人工读图」，
**不等于真人在浏览器里点一遍**——真实操作手感（滚动惯性、触控、缩放）不在这批证据里。

看过的页与档：任务页 **1440 / 1024 / 768 / 375 四档全看**；报告工作台 1440 / 1024 / 375；
领导只读报告页 1440 / 1280 / 1024（后两档是**测量**而不是读图，见下）。

- **报告工作台**：1440 正常；1024 KPI 卡折成 2×2、无横向溢出；375 单列纵向堆叠、无横向
  溢出（`fullPage` 高 6354px，长是因为内容本来就是纵向的，不是布局坏）。
- **领导只读报告页**：左栏 `.report-picker` 三档恒 **380px**、`.picker-head` 的「共 N 份」
  与右栏 `.report-detail` 的间隙三档恒 **17px**，**无重叠**（`xlp-measure.cjs`：
  1440 `detail.x=672 / right=1410 / w=738`、1280 `w=578`、1024 `w=322`）。
  1024 下右栏正文只剩 **322px** 宽（1440 是 738px）——偏窄但可用，是这一页在 1024 下的
  实际观感，记在这里备查。

###### ★ 目测被实测纠正的一处：768 下「按钮被压扁」是**裁切残片**，不是变形

768 的全页截图里，操作列那一格看起来是两个**竖排的窄条**（「查看」「删除」两字上下叠），
第一眼像是按钮被列宽挤变形了。**量下来不是**：

```
`xlp-opcol2.cjs`，768，同一时刻
  docScrollW 768 / docClientW 768     ← 页面本身不横向溢出
  wrapScrollW 820 / wrapClientW 698   ← 溢出发生在 .table-wrap 里面
  tableW 820 / tableRight 855
  wrapLeft 35 / wrapRight 733         ← 容器可视区右边界 733
  查看明细 x=701 w=74 right=775       ← 右边缘出界 42px
  编辑     x=781 w=48 right=829       ← 整个在容器外
  删除任务 x=707 w=74 right=781       ← 出界 48px
`xlp-opcol.cjs` 四档按钮（宽度与高度逐档相同，`overflow: false`）
  1440 / 1024 / 768 / 375 → 查看明细 74×34、编辑 48×34、删除任务 74×34
```

**按钮从来没有变形**——四档下它们的宽高逐档相同、内容不溢出。768 截图里那个「竖条」是
`.table-wrap` 的右边界把按钮**裁掉一半**之后露出来的残片，而 `overflow-x: auto` 使它
**可以横向滚动**（`.table-wrap` 是既有设计，§17 那一类「不是不可达，是需要滚动」）。
所以这一处与本轮 1024 的发现**是同一件事**，不是第二件：

| 视口 | `.table-wrap` 可视 | 表格 | 溢出 | 操作列 |
|---|---|---|---|---|
| 1440 | 1092 | 1092 | 0 | 完整可见（`lastBtnRight 1218 ≤ 1440`） |
| 1280 | 932 | 932 | 0 | 完整可见 |
| 1024 | 676 | 820 | **144px** | 需横向滚动（`lastBtnVisible false`） |
| 768 | 698 | 820 | **122px** | 需横向滚动 |
| 375 | 305 | 820 | **515px** | 需横向滚动 |

**这一处值得单独记，因为它正是「不靠眼睛、靠量」那条纪律的又一次验证**：如果只读图就下
结论，写进文档的会是一句「768 下按钮被挤变形」（假的），而真实的、可裁决的问题是
「≤1024 时操作列要横向滚动才够得着」。**这是本轮人工检查唯一一处真实产出**，交用户裁决。

###### 键盘目视（`xlp-shot2.cjs`，报告工作台「我的报告」列表）

```
Tab 进列表: true
进列表后      BUTTON.row   tabindex="0"  outline "solid 2px rgb(47, 110, 219)"  matches(':focus-visible') true
↓↓ 之后       BUTTON.row（**换到了下一行**）同上 outline / focus-visible
Shift+Tab 后  BUTTON.btn「↻ 重置」  ← **一次就离开了列表**，没有逐行走
```

三条合起来正是 §「★ roving tabindex」要达到的效果：**列表只占一个 Tab 停靠点**
（Shift+Tab 一次即出，若每行都是停靠点它会退回上一行），方向键在列表内移动焦点环，
且焦点环可见（2px 实线，与 `:focus-visible` 的约定一致）。截图
`kbd-1-first.png` / `kbd-2-down2.png` 上肉眼也看得到那个蓝框**长在被选中那一行上**。

##### 未完成（如实记，不勾）

1. **Phase D（CLASS-NOTE-01，P1）未做，且被用户裁决阻塞。** §5.13.6 要求把班级笔记
   `qingxin-notes` 从 `localStorage` 迁到服务端，而那一处的结论是「不再提供仅改
   `localStorage` key 的降级方案」——**要么服务端化，要么保持现状**，后者需要用户发话。
   所以 `ClassPortraitPage.vue` 那个 key **一个字未动**（§5.13.9 里那条「不再新增无范围的
   localStorage 草稿」说的是**不新增**，不是「把既有的迁走」）。
2. **Phase E（NAV-UX-01 / WORKBENCH-UX-01，P1）未做**，九条复选框全未勾（无阻塞，
   可随时开工）。现状证据：`AppLayout.vue:33` 的 `icon: '⚑'`、`:84` 的 `icon: '○'`
   仍是文本字符而不是 SVG 图标组件。
3. **§5.13.9 第二条（人工检查四档 + 键盘）改记「已执行到差额处」，仍未勾。**
   本轮的执行是**机器取景 + 助手读图 + 量化**（上面那一节「人工检查 375 / 768 / 1024 / 1440」），
   覆盖了任务页四档、报告工作台三档、领导页三档与一次键盘动线；而 375 / 768 的自动化
   覆盖是 `e2e/app.spec.ts:1600`、键盘是 `:1630`。
   **差的那一截是「真人在浏览器里点一遍」**（滚动惯性、触控、缩放、以及我读图时可能
   没注意到的观感问题）——所以这一条按原样保留未勾，不改成「已完成」。
4. **收口后的续做改动尚未提交**（9 个文件：8 个 `M` + 未跟踪的
   `frontend/src/composables/useRovingFocus.ts`，169 insertions / 28 deletions）。
   P0 六项本身已在 `3b6e586`（27 files / 4608 insertions / 170 deletions，2026-09-26
   17:59:40 +0800）。**推送前先确认不会覆盖**。
5. **`:4574` 那条用例仍在每轮净增数据。** 只做了清理，没做结构性改动——修法有两条互斥
   路线：(a) 改 `DataCenterPage.vue` 的可见窗口（产品 UI 改动，超本轮范围）；
   (b) 改那几条依赖窗口的 e2e 用例的定位方式（脱离数据量依赖）。两条都该由用户裁决。

### 5.14 四角色界面与任务流优化实施规范（2026-09-25）

#### 5.14.1 实施原则与顺序

四类角色不是同一后台换一套菜单。每个角色的首屏只优先回答自己的核心问题：

| 角色 | 首屏必须回答 |
|---|---|
| 学生 | 我有没有任务、做到哪里、会不会丢、需要帮助找谁 |
| 心理老师 | 今天先处理谁、为什么、下一步做什么、是否留下记录 |
| 德育领导 | 哪里异常、是否逾期、资源投向哪里、措施有没有推进 |
| 系统管理员 | 系统是否可用、哪些配置缺失、哪些作业失败、谁需要处理 |

实施顺序：

1. 必须先完成 §5.13 的 RPT-UX-01～06 并跑全量回归；
2. 再完成本节 Role P1；
3. 导航图标和纯视觉一致性最后统一收口；
4. 每个 Phase 独立提交、独立验证，禁止一次改完四个角色后再找回归来源。

共同约束：不扩大任何角色权限；不把数据隐藏当权限控制；不展示医学诊断；不修改 MHT 评分；
继续复用现有 settings、data scope、权限、审计、Export Job、Modal/FormDialog/DataTable。

#### 5.14.2 STUDENT-UX（P1）：学生端安心作答

主改文件：

- `frontend/src/features/student/StudentHomePage.vue`
- `frontend/src/features/student/StudentAssessmentPage.vue`
- `frontend/src/features/student/StudentHistoryPage.vue`
- `frontend/src/services/api.ts`、settings 接口（仅在确需学校联系人配置时扩展）
- E2E

实现要求：

- [ ] 任务卡显示任务名称、本人进度、截止时间；预计用时只在有可靠配置/统计来源时展示，禁止写死猜值。
- [ ] “开始作答 / 继续作答”保持唯一主按钮；已完成与不可作答状态继续 disabled 并解释原因。
- [ ] 首页与答题页明确显示“回答会自动保存”；该文案必须以现有逐题服务端保存行为为事实依据。
- [ ] 作答页保持单任务模式，不增加分数、等级、雷达图、趋势或其他可能造成自我标签化的内容。
- [ ] 提交前确认显示：已答题数、未答题数、提交后不可修改；有漏答时提供“定位未答”。
- [ ] “保存退出”成功后显示明确反馈，返回首页后进度与按钮文案一致。
- [ ] “我想找人聊聊”读取学校配置的联系人称谓、地点/方式、服务时段；未配置时只显示可信成年人、
  家长、学校心理老师和当地紧急服务等通用指引，不虚构电话或姓名。
- [ ] 求助入口在首页、完成记录、答题页保持一致；紧急提示使用清楚行动语，不使用诊断语言。
- [ ] 首页隐私说明缩成一句承诺，详细角色边界继续放在弹层；不能删除现有隐私与审计说明。

验收：

- [ ] 新任务、答题中、已完成、任务未开始/已结束四种卡片状态均有 E2E。
- [ ] 答一部分 → 保存退出 → 重新登录 → 继续作答，题号、答案与进度不丢。
- [ ] 有漏答时不能误导为已完成；定位未答可用。
- [ ] 375px 下主操作不被帮助浮钮遮挡，题目与按钮无横向溢出。
- [ ] 键盘可完成选答案、上一题、下一题、保存退出和提交确认。

#### 5.14.3 COUNSELOR-UX（P1）：心理老师行动优先工作台

主改文件：`CounselorWorkbenchPage.vue`、`CasesPage.vue`、`CareCaseDetailPage.vue` 与 E2E。

实现要求：

- [ ] 首屏固定优先顺序：今天必须处理 → 已逾期 → 待人工复核 → 其他在办工作。
- [ ] “近期提醒”拆成“今天/逾期”与“未来计划”；未来 7～30 天默认折叠为数量摘要。
- [ ] 优先队列首屏显示 5～8 条，保留“查看全部”；不得删数据或改变后端返回口径。
- [ ] 增加“我负责的 / 未分配 / 全部”快捷筛选；筛选必须继续受后端 data scope 约束。
- [ ] 行操作按当前阶段使用具体动词：`人工复核 / 记录跟进 / 查看档案`，避免所有行都叫“进入档案”。
- [ ] “题库导入”从每日工作台主操作区移除或降级为数据中心入口；工作台主操作围绕当天处置。
- [ ] 档案详情的下一步操作区保持可见（sticky 或稳定侧栏），包含人工复核、跟进、家庭回访、复测；
  “关闭档案”与主操作分离并使用危险操作确认。
- [ ] KPI、队列和提醒使用同一筛选口径；点击 KPI 后落到已经应用对应筛选的列表。
- [ ] 保留“量表仅用于筛查、不生成医学诊断”和数据范围说明。

验收：

- [ ] 有逾期时首屏无需滚动即可看到逾期数量及入口。
- [ ] “我负责的/未分配/全部”切换后列表、数量、空态一致。
- [ ] 每个阶段行操作进入正确动作，不能只换文案不带筛选/上下文。
- [ ] 未来提醒折叠不影响今天与逾期项目展示。
- [ ] 关闭档案仍需确认，人工记录不因 UI 重排丢失。

#### 5.14.4 LEADER-UX（P1）：领导端异常与趋势优先

主改文件：`LeaderOverviewPage.vue`、`ProgressPage.vue`、`LeaderAnalyticsReportPage.vue` 与 E2E。

确认缺陷：`计划复测` KPI 使用公共 `.metric` 的可点击视觉，但没有点击行为，是假可点击。

实现要求：

- [ ] `计划复测` 要么下钻到已筛选的复测计划摘要，要么移除 pointer/hover/active 视觉；默认优先提供下钻。
- [ ] 首屏排序：需管理关注的异常 → 逾期/未分配 → 年级趋势 → 常规完成率。
- [ ] “管理提醒”的每一项可下钻到对应筛选结果；无目的地的项必须使用非交互样式。
- [ ] 趋势只在存在可比前期且统计口径一致时展示环比/变化；否则明确“暂无可比周期”，禁止伪造趋势。
- [ ] 年级/班级不用“最好/最差”标签，使用“变化较大/需要进一步关注”等中性管理语言。
- [ ] 小样本继续隐藏占比，并在数据附近解释，而不是只在页面底部统一说明。
- [ ] 重点进展默认最小化个体暴露：先显示阶段、负责人、班级、是否逾期；姓名/学号仅在现有授权允许且
  确有工作必要的详情或二次展开中显示。任何调整必须有权限与 E2E 守卫。
- [ ] 专业报告页执行 §5.13 Phase C：默认最新已发布版、发布元数据、只读、版本级可见性。
- [ ] 继续禁止领导查看原始答卷、重点题答案、人工复核正文和家庭回访正文。

验收：

- [ ] 所有看起来可点击的 KPI/提醒均能到达正确页面并带筛选；不可点击项没有手型和 hover 抬升。
- [ ] leader 无法通过页面或直接 API 读取草稿、原始答卷与私密正文。
- [ ] V2 草稿期间仍看到/导出 V1，见 §5.13。
- [ ] 小样本、无数据、无可比周期均有独立空态/说明。

#### 5.14.5 ADMIN-UX（P1）：系统健康首页

主改范围：新增管理员首页组件与路由，复用现有账号、设置、导入、导出作业、量表、审计接口。
只有现有接口无法提供必要聚合时才新增只读 summary API，禁止让管理员获得心理内容权限。

实现要求：

- [ ] 新增 `/admin/overview`，管理员登录默认进入该页；侧栏新增“系统概览”。
- [ ] 首屏仅展示系统/运维事实：未配置范围账号数、需改密账号数、停用账号数、失败导入、失败/过期
  导出作业、当前量表版本/发布状态、系统版本、待完成上线配置。
- [ ] 每张卡片可进入对应管理页并带筛选；无法下钻的状态卡不得伪装成按钮。
- [ ] 不展示学生心理分数、关注等级、答卷、档案或专业报告正文。
- [ ] 账号表的“编辑/重置密码/停用”收进统一操作菜单；停用使用危险色并保留确认说明。
- [ ] 数据范围以可扫描标签显示；“未配置·看不到任何学生”保留警告并提供配置入口。
- [ ] 临时密码弹层增加“复制”按钮和复制成功反馈；密码仍只在创建/重置当次可见，不写入日志。
- [ ] 权限矩阵支持按角色查看与差异高亮；“恢复默认权限”如实现，必须二次确认并写审计。
- [ ] “上线配置”只能展示可以从真实配置判定的项目；禁止在界面里猜测 JWT secret 是否安全。

验收：

- [ ] 管理员默认首页在无业务数据时也能显示系统健康，而不是空白。
- [ ] 所有卡片数字可由对应列表复核；失败作业入口带正确筛选。
- [ ] 管理员仍无法访问心理详情接口；增加/保留对应 403 测试。
- [ ] 临时密码复制不产生控制台、审计 detail 或 DOM 持久残留。

#### 5.14.6 CROSS-ROLE-UX（P1）：导航、动作、空态与语言

- [ ] 建立统一 SVG 图标组件/映射，替换 `AppLayout.vue`、`KpiCard.vue` 中的 Unicode 字符图标。
- [ ] 图标固定 20/24px viewBox、统一线宽；装饰图标 `aria-hidden=true`，纯图标按钮有可读名称。
- [ ] 桌面导航保留现有角色分区；移动端底栏仅保留 4～5 个最高频入口，其余进入“更多”。
- [ ] 375～480px 将登录设备、修改密码、退出合并到账号菜单；退出仍需易发现。
- [ ] 每页最多一个主操作；次操作降级；删除/停用/关闭等危险操作分离并确认。
- [ ] 空态说明“现在没有什么”及“下一步可以做什么”，但不得把用户指向实际不可用的动作。
- [ ] 角色语言保持分离：学生=任务/进度/帮助；心理老师=复核/跟进/专业解读；领导=趋势/推进/
  逾期/资源；管理员=账号/权限/配置/作业状态。
- [ ] 保留 `:focus-visible`、`prefers-reduced-motion`，并验证 375/768/1024/1440px。

#### 5.14.7 测试与 Definition of Done

E2E 至少新增/调整：

- [ ] 学生四种任务状态、保存续答、漏答定位、帮助弹层、375px 布局。
- [ ] 心理老师今天/逾期/待复核排序、负责人筛选、具体行操作、未来提醒折叠。
- [ ] 领导 KPI/提醒无假点击、筛选下钻、小样本、隐私边界。
- [ ] 管理员默认系统概览、健康数字下钻、账号操作菜单、临时密码复制、心理详情 403。
- [ ] 四角色各跑一次键盘主流程；四档视口无页面级横向溢出。

完成判据：

- [ ] §5.13 P0 已先完成且全绿。
- [ ] 四角色首屏分别围绕其核心任务，不再只是同一后台换菜单。
- [ ] 所有交互外观与实际行为一致，无假按钮、假卡片或不可达操作。
- [ ] UI 重排不改变后端权限、data scope、统计口径或 MHT 评分。
- [ ] Backend Tests、frontend build、全量 E2E 全绿，实测数字写回本节。
- [ ] 人工验证 375/768/1024/1440px、键盘、焦点、长文案和空数据。
- [ ] 更新 `PROGRESS.md` 勾选项、最终 commit SHA 与受环境限制的验证。

明确排除：学生个体分数/等级/趋势展示、管理员心理内容权限、领导原始答卷/私密正文、无可靠数据
来源的预计时长或趋势、未经配置的联系人信息、为 UI 便利新建第二套权限/审计/导出体系。

## 6. 关键文件

| 文件 | 作用 | 状态 |
|---|---|---|
| `docs/心晴_V1.1.6_to_V2.0.0_P0-P1功能优化实施规范_AI_Coding基线版.md` | 本轮功能优化正式输入规范 | 基线 |
| `PROGRESS.md` | Claude / AI Coding 持续执行与会话恢复状态 | 当前文件 |
| `backend/app/models/assessment.py` | AssessmentTask / Session / Result / RiskEvent 核心模型 | 待改 |
| `backend/app/models/importing.py` | 外部导入批次、行、外部结果模型 | 复用，禁止重构 |
| `backend/app/models/care.py` | 关怀档案、复核、跟进、回访、复测 | 保留历史 |
| `backend/app/api/v1/tasks.py` | 任务 REST API | P0 主改 |
| `backend/app/services/task_service.py` | Task Governance 主实现点 | P0 主改 |
| `backend/app/api/v1/assessment_import.py` | 外部测评导入 API | 仅兼容性修改 |
| `backend/app/services/assessment_import_service.py` | IMPORTED task 月度复用、批次提交 | 禁止重构 |
| `backend/app/services/assessment_service.py` | session / latest result / risk event | VOID 兼容 |
| `backend/app/services/analytics_service.py` | 当前状态、统计、报表 | VOID / scope 兼容 |
| `backend/app/services/care_service.py` | 关怀工作流 | 防止 VOID 错误影响 |
| `backend/app/security/permissions.py` | Capability Matrix | P1 待改 |
| `backend/app/security/data_scope.py` | 数据范围授权 | 复用 |
| `frontend/src/features/admin/TasksPage.vue` | 测评任务管理 | P0 主改 |
| `frontend/src/features/admin/DataCenterPage.vue` | 外部导入、关联任务选择 | 仅过滤 VOIDED |
| `frontend/src/features/analytics/views/OverviewPage.vue` | 筛查关注概览 | P0 scope |
| `frontend/src/features/analytics/views/DimensionsPage.vue` | 八维度分析 | P0 scope |
| `frontend/src/features/analytics/views/GradesPage.vue` | 年级统计 | P0 scope |
| `frontend/src/features/analytics/views/ClassPortraitPage.vue` | 班级统计 | P0 scope |
| `frontend/src/features/analytics/views/ReportExportPage.vue` | 当前 counselor/leader 共用报告页 | P1 拆分 |
| `frontend/src/features/leader/` | Leader 专属报告/摘要页面 | P1 |
| `frontend/src/services/api.ts` | 前端 API Contract | 待改 |
| `frontend/src/services/labels.ts` | UI 术语统一 | P1 |
| `frontend/src/app/routes.ts` | counselor / leader 路由 | P1 |
| `frontend/src/app/AppLayout.vue` | 菜单与角色导航 | 按需改 |
| `backend/alembic/versions/` | 增量数据库迁移 | 新增 migration |
| `backend/tests/` | Backend Tests | 待补 |
| `e2e/` | Playwright E2E | 待补 |
| `package.json` | 前端/E2E脚本定义 | 先读取后使用 |
| `Makefile` | 仓库统一开发/测试命令 | 优先复用 |

## 7. 关键命令

```bash
# 确认仓库与当前分支
git status
git branch --show-current
git log -1 --oneline

# 基于 V1.1.6 创建 V2.0.0（仅当尚未创建）
git checkout V1.1.6
git pull
git checkout -b V2.0.0

# 如 V2.0.0 已存在
git checkout V2.0.0
git pull

# 确认 V2.0.0 基线
git merge-base V1.1.6 V2.0.0
git log --oneline --decorate -10

# 先读取仓库真实命令
cat Makefile
cat package.json

# 后端迁移与测试（以仓库现有环境说明为准）
cd backend
alembic upgrade head
python -m pytest -q

# 前端构建
cd ../frontend
npm ci
npm run build

# E2E
cd ..
npx playwright test

# 检查改动
git status
git diff --stat
git diff

# 提交
 git add .
 git commit -m "feat: implement V2.0.0 P0-P1 product enhancements"
 git push -u origin V2.0.0
```

## 8. Claude / AI Coding 执行约束

1. 每完成一个 Phase，必须更新本 `PROGRESS.md`：
   - 对应 `[ ]` 改为 `[x]`；
   - 更新“当前状态（一句话）”；
   - 更新“下一步（最重要）”；
   - 已提交时补充 commit SHA。

2. 每次 compact / 新会话优先读取：
   - `PROGRESS.md`
   - P0-P1 功能优化实施规范
   - 最近 3～5 个 commits

3. 禁止：
   - 修改 MHT 评分规则；
   - 重建 `assessment_import_batch`；
   - 新建第二套权限体系；
   - 新建第二套 Export Job；
   - 修改历史 Alembic migration；
   - 自动合并 / 删除历史 IMPORTED task；
   - 将管理员隐式赋予心理业务数据权限；
   - 因任务作废删除人工复核、跟进、回访或关怀档案；
   - 将 P2 混入本轮。

4. 如果实现发现规范与 V1.1.6 源码冲突：
   - 以当前源码事实为基础；
   - 不静默猜测；
   - 在 `PROGRESS.md` 记录差异、影响和最终处理方式。
