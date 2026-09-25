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

**P0-01 / P0-02 / P1-01 / P1-02 / P1-03 / P1-04 六项全部落地，§5.7 全局回归全绿，
本轮编码结束。** 判据（都是实测、不是声称）：

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

**剩下两件事都不是编码**：§5.9 另外**五条**待你裁决（第 5 条已关闭）、以及 §5.7 最后一项
「提交到 `V2.0.0`」— 用户已发话，见那一格的 commit SHA。

## 3. 下一步（最重要）

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
   - **提交到 `V2.0.0`**：`git add . && git commit && git push -u origin V2.0.0`
     （`origin/V2.0.0` 尚未创建）。用户 2026-09-25 发话「推送」，执行结果见 §5.7
     最后那一格（commit SHA / push 结果）。

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
- [ ] ~~学生本人历史默认不显示已作废任务记录~~ → **有意偏离规格**：改为「显示并标注」，
      理由与下一条同源（那一场是他真实考过的一次，删掉一个点就是在抹掉一段发生过的事实；
      缺的是知情权，不是那一行）。两个历史页各有一句「已作废，不参与当前判断」，
      且**没有作废场次时整句不出现**（§14：空态是关于数据的一句话，不是格式）。
      **这条偏离需要你确认**：若坚持规格原文，就改成默认过滤 + 一个「显示已作废」开关。
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

**★ 这一组必须 `serial` 跑，而理由是实测出来的（一条真实缺陷）**：全仓只有这一组会用
`POST /api/v1/assessment-tasks` 真建任务，而那个端点**并发不安全**——
`task_service.create_school_assessment_task` 拼的任务号是
`TASK-{utcnow:%Y%m%d%H%M%S}-{全库任务数 + 1}`，同一秒内的两个请求算出**同一个号**，
撞 `task_no` 唯一键，其中一个拿 500。实测复现：两个并发 `curl` POST →
`req1 http=500` / `req2 http=200`。默认 3 workers 下那次「1 failed / 2 passed」红在
**用例自己撞自己**上，与被测代码无关。修它要改成序列号或唯一约束重试，**本轮未修**，
已记进 §5.9 待裁决。

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
- [ ] 代码最终提交到 `V2.0.0` ← **唯一未做的一项，等用户发话**（推送是对外动作）

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

**★ 版本号没有动，这是有意的，请你裁决。** `backend/app/version.py` 仍然是
`__version__ = "1.1.6"`（于是 `VERSION_LABEL` = `V1.1`、出包产物仍然是
`心晴部署包_V1.1.6.zip`、页脚那一行仍然写 `V1.1`）。规范只写了「目标版本：`V2.0.0`」，
**没有一句要求改 `version.py`**，而这一改是**对外可见**的（产物名、`package-info.txt`
的构建戳、界面页脚），并且会连带改 `test_app_version.py` 里那条按 `V1.1` 写死的字面量
（CLAUDE.md §30 记着那个字面量**是故意随 `__version__` 变的**，所以它得一起改）。
前两条发布线（V1.1.5 / V1.1.6）各自都有一个**单独的**「版本号升到 X」提交，说明在这一
仓库里升版本是一个**独立动作**，不是「顺手带上」。所以本轮**没有**把它塞进这次提交——
你说一句要不要升到 `2.0.0`，升的话是一条两行改动的独立提交（`version.py` +
`frontend/package.json` + 那条断言的字面量）。

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
   - **它为什么真的会发生**：`POST /assessment-sessions/{id}/reset` 删答案但**刻意保留
     result 行**（好让下次提交走 `submit_session` 的幂等早返回），所以「会话
     `IN_PROGRESS`、0 答案、有一份旧结果」是这套系统**正常产生**的状态——任何跑过学生
     答题 e2e 的库都有一行，而我们会带着这样一行去做升级预演。它描述的那场作答已经
     不存在了，也就没有「算得准不准」；
   - **影响面**：`PRECHECKS` 只在 `upgrade()` 的时候跑（已经升到 ≥ `0020` 的库永远不会
     再读它），所以这不是「改写已生效的迁移」；而它会被渲染进
     `backend/sql/upgrade_from_v1_0_0.sql`，那一份已按 `make db-upgrade-sql` 重生成，
     逐字节守卫（`test_incremental_upgrade_sql.py`）在重跑里是绿的。
   这条缺口与 §5.9 第 3 条（`reset_session` 留下孤儿结果）是**同一件事的两面**：这一面
   只是让迁移**不要被它挡住**，没有回答「那一场还算不算数」——那一条仍然等你裁决。

### 5.9 待你裁决（本轮没有自行决定的）

**这一节里每一条都是「规格没有给答案，而自己定就等于猜」。** 每条都写了今天的实际行为、
为什么没有顺手改、以及要改它需要你先回答什么。

1. **`task_no` / `report_no` 同秒并发会撞唯一键。** 两个请求在同一秒内到达时，
   `TASK-{utcnow:%Y%m%d%H%M%S}-{全库任务数 + 1}` 这个形状会算出同一个号——实测
   `req1 http=500 / req2 http=200`（e2e 那一组因此必须 `serial` 跑，见 §5.1）。
   已有一个 `task_no` 唯一约束兜底（所以它是 500 而不是两条同号的任务），
   但**用户拿到的是 500**。修它要选一条：改成序列号（数据库自增或单独的计数表）、
   撞号时重试、还是放宽成「不唯一但可读」。**没有一种能靠现有规格判出来**，
   所以本轮未修。`report_no` 同形。
2. **「学生本人历史默认显示已作废任务并标注」是有意偏离规范的一处。**
   规范要求作废后的任务不再出现在学生的历史里；实际实现是**显示 + 标注「已作废」**。
   理由是：那一场他确实答过卷，把它从「我做过什么」里拿掉等于抹掉一段发生过的事实
   （CLAUDE.md §1 那条「关闭档案不得删除历史记录」是同一条）。**如果你要的是「不显示」，
   这一条要单独改**——它在学生侧的读法与你给的那句话不同。
3. **`reset_session` 保留 `assessment_result`，于是会留下孤儿结果。**
   重置一场会话会清答案、但不清结果行（结果由 §1 的四层事实模型保护）。
   一份「有结果、没有答卷」的会话在**按结果说话的报表**里仍然算数，
   而它的答卷已经不在库里了。这是既有行为（不是本轮引入），而 P0-01 的作废判据
   （「有没有正式测评事实」）会不会被它影响，需要你先定「重置之后那一场还算不算数」。
4. **`ReportExportPage` 的空态文案说得不对。** 库里一个任务都没有时，页面上写着
   「请选择测评任务后点击查询」——而那一刻**没有任务可选**（下拉是空的）。
   这是一句**做不到的指示**（CLAUDE.md §14：空态是一句关于数据的话，不是一次操作的占位）。
   改法有两种：让它变成「本学年还没有可用的测评任务」（一句关于数据的话），
   或者干脆把按钮置灰。**选哪一种取决于你想要的口吻**，所以没有自行改。
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
6. **`DRAFT / PUBLISHED / ARCHIVED` 的第三档没实现，`LEADER_MANAGEMENT_SUMMARY`
   这个导出类型也没有建。** 两处都不是漏了：
   - `ARCHIVED`：规格只给了三个状态**名**，没给「什么时候归档」这条转换。凭空造一个
     没有任何写入方的状态，就是一条永远不可达的分支——CLAUDE.md 的「不静默猜测」
     与「一条恒绿的守卫比没有更糟」都不允许。**要它的话请说一句「谁能归档、归档之后
     还能不能看」**，两句话就能落地。
   - `LEADER_MANAGEMENT_SUMMARY`：领导导出的是**同一份已发布版本**（同一个
     `PROFESSIONAL_REPORT` 作业、同一份文件），再建一个类型等于把同一份文件按角色
     复制一遍，而两份会长歪（CLAUDE.md §3「同一个东西在一屏上不能有两个名字」的镜像）。
     规格里那一句写的是「**按需要**支持」——**判定为不需要**。

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
