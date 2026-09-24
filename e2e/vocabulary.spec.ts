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
  // SCOPE_TYPE_LABELS。**`STUDENT` 刻意不在这里**，理由有两半，缺一不可：
  //
  // ① 它没有任何渲染点。这张表服务的是任务的「对象范围」列，而任务范围今天恒为
  //    `SCHOOL`（§22：`create_school_assessment_task` 只发全校），年级/班级/单人
  //    三级从来没有写入方。所以列上它等于一条永远扫不到东西的空覆盖——留着不
  //    增加任何保护。
  // ② 而它**有**一个渲染点，只是不属于这张表：审计页的「对象」列直接打印
  //    `row.resource_type`，取值里就有 `STUDENT`（`导入学生` 那一行）。
  //    那一列**有意**不翻译，是缺口 7（改了显示而 `q` 还是按编码搜，用户会从
  //    「看不懂」变成「搜不到」），正解是筛选器 + 显示 + 提示语三件事一起做。
  //
  // 两半合起来：把 `STUDENT` 留在这里，等于用一条**没有覆盖的清单项**去换一个
  // **竞态假阳性**——审计页只看最新 20 条，而 `app.spec.ts` 的导入用例会写一行
  // `resource_type=STUDENT`，两个文件并发跑（`fullyParallel`），扫到与否取决于
  // 那一瞬间谁先跑完。2026-09-19 就是这么红的。**会无故变红的守卫很快会被人关掉**，
  // 而它想守的那件事（审计页有裸编码）本来就写在缺口 7 里。
  'SCHOOL',
  'GRADE',
  'CLASS',
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
  // REMINDER_KIND_LABELS（2026-09-20）。这一张表在此**之前没有渲染点**：类别是拼进
  // `title` 的中文（后端发「跟进 钱浩然」/「钱浩然 复测」），而工作台那一行前面还写着
  // 一次类别，屏幕上于是出现「已逾期 1 天 · 跟进 跟进 钱浩然」。修法是类别只留在
  // `kind` 里、中文归 `labels.ts`，`title` 退回主题（学生姓名）——`reminderKindLabel`
  // 这才第一次被调用，而它落的正是 `/counselor/workbench`（在下面的路径清单里）。
  //
  // 两个码在演示数据里都扫得到：提醒面板的判据是「30 天内到期」，
  // `seed_demo` 造的 ACTIVE 跟进与 PLANNED 复测都落在那个窗口里。与 `IMPORTED` /
  // `SUPPLEMENT` 不同，这一项不是「等哪天有数据」。**实测过**（2026-09-20，
  // `GET /counselor/reminders`）：18 条里 `FOLLOW_UP` 16 条、`RETEST` 2 条；
  // 变异验证也逐条验过——从 `REMINDER_KIND_LABELS` 拿掉任一档，`/counselor/workbench`
  // 上就报出对应的裸码（两档各红一次，`labelOf` 认不出的码原样回退）。
  //
  // 这两个码也是把匹配边界从 `\b` 收成「不许紧邻 `-`」的那个起因，见下面
  // `PATTERN_SOURCE` 那段：演示任务编号 `TASK-2026-GRADE9-RETEST` 里的 `RETEST`
  // 会被 `\b` 命中，而它是产品自己的编号，不是漏译。
  'FOLLOW_UP',
  'RETEST',
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
  // TARGET_SOURCE_LABELS（V1.2 第 1 期）。这张表与上面那张长得像而**不是同一件事**：
  // SOURCE_LABELS 说「这份答卷是在哪测的」，这一张说「这个人是怎么进到名单里的」。
  //
  // 它落在**弹层里那个页签**上，所以扫描必须点开「查看明细」→「目标学生」才到得了
  // （本文件末尾那条用例就在做这件事）。演示数据里每一行都是 `TASK_SCOPE`——
  // 建任务发出去的那些；`SUPPLEMENT` 要等真的补发过一次才会有。与 `IMPORTED` 同理，
  // 一并列上：哪天演示数据或某个用例带进一条补发的目标行，它会自己开始生效。
  'TASK_SCOPE',
  'SUPPLEMENT',
  // CALCULATION_STATUS_LABELS（V1.2 阶段 2）。`PENDING` 上面已经有了（风险事件状态），
  // 这里补的是另外三个。
  //
  // 它落在**两处不条件渲染的渲染点**上：个案详情的「评分状态」行与学生记录页的
  // 「处理状态」行——两页都在 `auditPages` 的路径清单里（`/counselor/cases/1` 与
  // `/student/history`），演示数据里每一场都是 `CALCULATED`，所以这一项真的扫得到东西。
  // `CALCULATION_FAILED` 要等一次真的计算故障才会有（与 `IMPORTED` / `SUPPLEMENT` 同理，
  // 一并列上，哪天数据或某个用例带进来它就自己开始生效）。
  'CALCULATING',
  'CALCULATED',
  'CALCULATION_FAILED',
  // TESTED_AT_SOURCE_LABELS（V1.2 阶段 2）。同一页的「日期来源」行，同样不条件渲染。
  // `ONLINE_SUBMIT` 是演示数据里每一场的值（它们都走 `submit_session` 交的卷），
  // 另外两个与上面同理。
  'ONLINE_SUBMIT',
  'IMPORT_FILE',
  'PENDING_VERIFICATION',
  // ROSTER_BATCH_STATUS_LABELS / ROSTER_ROW_STATUS_LABELS / ROSTER_CONFLICT_LABELS
  // （V1.2 阶段 3 名册导入批次化）。三张表的渲染点都只在 `/admin/organization` 的
  // 「导入批次」那一张表与它的明细弹层里，而它们扫得到东西的前提是**库里真的有一批
  // 导入过**——`make seed-demo` 不产生批次（名册导入不是种子会做的动作）。所以本文件
  // 末尾那条用例先通过接口传一份文件把批次造出来再扫，理由与 `studentWithRetestPlan`
  // 完全相同：**先证明有东西可扫，再断言它干净**。
  //
  // `PENDING` 上面已经有了（风险事件状态），这里补的是其余七个。演示数据里扫得到的
  // 是 `PREVIEW`（那一条用例落下的是预览批次）与 `PENDING` / `STUDENT_NO_EXISTS`；
  // `COMMITTED` / `CREATED` / `UPDATED` / `SKIPPED` / `ERROR` 要等真的提交过一次
  // （`app.spec.ts` 的学生导入那组会提交一次「放弃」，它俩并行跑，扫到与否不保证），
  // 与 `IMPORTED` / `SUPPLEMENT` 同理，一并列上：哪天数据到了它会自己开始生效。
  'PREVIEW',
  'COMMITTED',
  'CREATED',
  'UPDATED',
  'SKIPPED',
  'ERROR',
  'STUDENT_NO_EXISTS',
  // MATCH_STATUS_LABELS（V1.2 第 4 期 MHT 导入批次化）。九个码唯一的渲染点是
  // `/counselor/data` 的「导入批次」表点开的那个明细弹层——**不点开就不存在**，
  // 所以本文件末尾单独有一条用例去点它（`PREVIEW` / `PENDING` 上面已经有了）。
  //
  // 演示数据里扫得到的是 `INVALID_ROW` 与 `NOT_FOUND`（那一条用例造的两行：一行空姓名、
  // 一行查无此人），其余七个要等名册上真的出现「年龄不符 / 同名多候选 / 本月已导过 /
  // 本场已有在线答卷 / 不在任务名单里」这些情形，与 `IMPORTED` / `SUPPLEMENT` 同理，
  // 一并列上：哪天数据到了它会自己开始生效。
  'MATCHED',
  'AGE_CONFLICT',
  'AMBIGUOUS',
  'NOT_FOUND',
  'OUT_OF_SCOPE',
  'DUPLICATE',
  'INVALID_ROW',
  'CONFLICT',
  // IMPORT_CONFLICT_LABELS（V1.2 第 4 期）。`DUPLICATE` 上面已经有了（它同时是
  // 一个 `match_status`），另两个是「这一行为什么要你拍板」那一列的值。
  'AGE_MISMATCH',
  'IN_SYSTEM_RESULT',
  // IMPORT_MODE_LABELS（V1.2 阶段 5）——批次摘要里那格「导入形态」。它与上面那些一样
  // 在明细弹层里，**不点开就不存在**，所以本文件末尾那两条用例各自点开一次：逐题答卷
  // 那一份在上一条里，汇总档那一份在下一条里。两种形态在批次列表上长得一模一样
  // （`source` 都是 `IMPORTED`），所以「有东西可扫」不能靠数据恰好长成某样——
  // 那两条用例各自断言一次那一格的中文，再扫像素。
  'EXTERNAL_FULL_ANSWER',
  'EXTERNAL_SUMMARY',
  // OUT_OF_SCOPE_REASON_LABELS（V1.2 阶段 5）。`OUT_OF_SCOPE` 上面已经有了（它同时是
  // 一个 `match_status`），这里补的是把「不在本场任务里」分成两句的那两个码——渲染点
  // 在逐行明细「匹配结论」那一格下面，只有真的出现一行「匹配上了、但不归这场任务管」
  // 才存在。
  //
  // **e2e 里造不出那一行**，理由与上面那七个 `match_status` 逐字相同（演示名册的班级
  // 叫 `1班`，而文件那一列按学校编号规则要写 `704`，`_parse_class` 出来的名字在名册上
  // 必然落空，所以匹配这一步永远走不到「找人」）。与 `AGE_CONFLICT` 等同理，一并列上：
  // 哪天数据到了它会自己开始生效。
  'SUPPLEMENT_CANDIDATE',
  'NOT_IN_TASK_SCOPE',
  // AGE_RESOLUTION_LABELS（V1.2 阶段 5）。这两个是**小写**的，因为它们是请求体里的
  // 字面量（`PATCH /assessment-import-rows/{id}/resolve` 收的就是这三个字），后端原样
  // 存进 `age_resolution` 那一列。唯一能扫到的渲染点是**已提交**那一批里的中文
  // （`ageResolutionLabel`）——预览时那三个单选按钮的 `value` 不进 `innerText`。
  // 所以它与上面那些一样：今天扫不到东西，等有一批真的提交过、且里面有一行按某种口径
  // 处置过年龄时才会生效。
  //
  // **`overwrite` 刻意不在这里。** 它与名册导入的处置码是同一个字（后端
  // `AGE_RESOLUTION_OVERWRITE = RESOLUTION_OVERWRITE`），而那一张表
  // （`ASSESSMENT_RESOLUTION_LABELS`，第 4 期）从一开始就没有列进本清单：一个普通的
  // 英文单词在别的文案里出现的可能性，比它换来的那点保护更值钱——**会无故变红的守卫
  // 很快会被人关掉**。这两个是 `snake_case` 短语，没有这个风险。
  'keep_roster',
  'session_only',
  // CONFLICT_RESOLUTION_LABELS（V1.2 阶段 6 四档处置）。唯一的渲染点是
  // `/counselor/data` 明细弹层「处置」列里**冲突行专属的那一支**——非冲突行渲染的是
  // 年龄处置，所以这一列不是「点开就有」，而是「先得有一条冲突行」。
  //
  // **e2e 里造不出那一行**，理由与上面那七个 `match_status` 逐字相同：来源冲突的前提
  // 是这一行**匹配上了某个学生**（`IN_SYSTEM_RESULT` 要那名学生在本场有在线答卷），
  // 而演示名册的班级叫 `1班`、文件那一列按学校编号规则要写 `704`，匹配这一步永远走
  // 不到「找人」。要靠 e2e 覆盖它，得先让演示名册长出一个叫 `704` 的班，而那会改动
  // 共享演示库的名册（跑 e2e 不许改掉数据）。后端那一侧是钉住的
  // （`test_import_conflict_resolution.py` 十条）。
  'KEEP_ONLINE',
  'USE_EXTERNAL',
  'REJECT_EXTERNAL',
  'KEEP_BOTH_BUT_ONE_EFFECTIVE',
  // IMPORT_ROW_STATUS_LABELS 的新码（同一期）：选了「保留系统内作答」或「不采纳外部
  // 结果」时这一行的处理状态——`PENDING` 上面已经有了（「待导入」，还没提交），
  // 这一码说的是**提交之后**：人拍过板了，而这一次不写外部那一份。
  //
  // 与上面四档走的是同一条路（扫得到它就要先有一条冲突行被处置过），所以今天也是
  // 零覆盖。一并列上：哪天数据到了它会自己开始生效。
  'NOT_APPLIED',
  // CARE_EVENT_LABELS（V1.2 阶段 7 关怀档案事件）。八个码唯一的渲染点是**个案详情页的
  // 「档案事件」页签**（`CareCaseDetailPage.vue` 的那条时间线）。
  //
  // 它与上面那些页签有一处不同：**数据不是切过去才请求的**——`GET /care-cases/{id}`
  // 一次把 `events` 带回来了，点页签只是切 `v-if`。所以上面 `auditPages` 走
  // `/counselor/cases/1` 那一趟**扫得到**它。即便如此仍单独留一条用例：`auditPages`
  // 挑的是它自己那名学生，而「那名学生恰好有事件行」是一件**没被断言过**的事——
  // 一条没有事件的档案同样会让那一趟全绿，而它扫的是一块空区域罢了。
  // 单独这一条把「有东西可扫」写成断言（理由同 `studentWithRetestPlan`）。
  //
  // 演示数据里扫得到的是 `CASE_OPENED`（任何一份档案的起点，所以必然有）、
  // `MANUAL_REVIEWED` / `FOLLOW_UP_ADDED` / `RETEST_PLANNED`（`seed_demo` 各建了几条）；
  // `CASE_CLOSED` / `CASE_REOPENED` / `OWNER_ASSIGNED` 要等真的关过一次、重开过一次、
  // 转派过一次——与 `IMPORTED` / `SUPPLEMENT` 同理，一并列上：哪天数据到了它会自己开始生效。
  // （`app.spec.ts` 的「closing a case…」那条会关一次再开一次，但它在另一个文件里、
  // 与这里并发跑，扫到与否不保证，所以不靠它。）
  'CASE_OPENED',
  'MANUAL_REVIEWED',
  'FOLLOW_UP_ADDED',
  'FAMILY_CONTACT_ADDED',
  'RETEST_PLANNED',
  'CASE_CLOSED',
  'CASE_REOPENED',
  'OWNER_ASSIGNED',
  // PARTICIPATION_DISPOSITION_LABELS（V1.2 阶段 8 / §18.10 应测口径）。四个码说的是
  // 「这个人该不该做这一场」，与左边那一格「做完没有」（`TARGET_STATUS_LABELS`）是两个
  // 问题——一名请假的学生在同一行上是「未开始 · 请假」，两句话同时为真。
  //
  // 渲染点在**完成明细弹层那一列**上，而且**不条件渲染**：每一行都出一个药丸。所以
  // 本文件上面那条「测评任务的完成明细弹窗」用例已经扫得到它了（演示数据里每一行都是
  // `REQUIRED`），不需要为它单开一条。另外三码要等真的标记过一次
  // （`app.spec.ts` 有一条标记用例会去写一次，而它在另一个文件里、与这里并发跑，
  // 扫到与否不保证），与 `IMPORTED` / `SUPPLEMENT` 同理，一并列上：数据到了它自己会生效。
  'REQUIRED',
  'LEAVE',
  'EXEMPT',
  'EXCLUDED',
  // EXPORT_TYPE_LABELS / MASK_LEVEL_LABELS / EXPORT_JOB_STATUS_LABELS（V1.2 阶段 8 / §16.3）。
  // 三张表的渲染点全在**导出中心**（`/counselor/exports` 与 `/admin/exports`）那一张
  // 表上——那是「这份文件是不是实名的、当时按谁的授权范围导的、现在还取不取得到」唯一
  // 的台账，三列各自读一张表。
  //
  // **这一页单靠 `auditPages` 的路径清单守不住**，这一点是量出来的：清单里加上路径之后
  // 做变异验证（把三个 `xxxLabel(...)` 全换成裸字段），四条角色用例**全绿**——因为
  // `export_job` 表此刻 0 行，`DataTable` 渲染的是空态那一行，一个格子都没有。清单加成
  // 功了，覆盖仍然是零。所以末尾单独一条用例**先用接口建一份作业再扫**
  // （`studentWithRetestPlan` 那一族的第五例）。
  //
  // 路径仍然加在两条角色用例里（心理老师与管理员各一条）：它们覆盖的是一页上**不依赖
  // 数据**的那部分像素，而导出中心是这一期新加的一页，进清单本身要有一句记录。
  //
  // 扫得到的是建出来那一份的 `CARE_CASES` / `MASKED` / `READY`（以及撤销之后的
  // `REVOKED`），`HIGH_RISK_CASES` 由 `app.spec.ts` 那条高度关注导出带进来（并发跑，
  // 扫到与否不保证）。其余一并列上：数据到了它自己会生效。
  // **`EXPIRED` 在 e2e 里不可达**——它要等 `expires_at` 走过去，而那是 `job_ttl_hours`
  // 的函数（把系统时钟拨快不是 e2e 该做的事）。后端那一侧是钉住的
  // （`test_export_jobs.py` 里按 `now` 现算那几条）。
  'CARE_CASES',
  'HIGH_RISK_CASES',
  'SINGLE_CASE',
  'TASK_COMPLETION',
  'NON_PARTICIPANTS',
  'MASKED',
  'IDENTIFIED',
  'READY',
  // AUTH_SESSION_STATUS_LABELS（V1.2 阶段 8 / §16.5）。`REVOKED` 与 `EXPIRED` 与导出
  // 作业的状态是**同一批字面量**，所以只在这里列一次；`ACTIVE` 上面已经有了
  // （任务状态那一组）。三码唯一的渲染点是顶栏「登录设备」那个弹层里的药丸——**不点开
  // 就不存在**，所以本文件末尾单独有一条用例去点它。
  'REVOKED',
  'EXPIRED',
];

// 词边界匹配，且 `_` 在 JS 正则里属于 \w，所以 STUDENT 不会误伤
// STUDENT_PSYCH_DETAIL、ACTIVE 不会误伤 INACTIVE、VALID 不会误伤 VALIDATION。
//
// **但 `-` 既不属于 \w、也不被 `\b` 排除**，所以光有 `\b` 还不够：演示任务的编号
// `TASK-2026-GRADE9-RETEST`（`seed_demo.py`）里那一截 `RETEST` 两侧都是 `-`，
// `\bRETEST\b` 照样命中。2026-09-20 把 REMINDER_KIND_LABELS 的两个码加进清单时，
// 就是这样在 `/counselor/tasks`、`/counselor/data`、`/leader/tasks` 三处报出假阳性
// ——**命中的是产品自己的任务编号，不是漏译的枚举**。任务编号是学校可以自由命名的
// 业务数据，不能因为里面有哪个英文词就把整个清单项拿掉（那样丢的是
// `/counselor/workbench` 上真实的覆盖），所以收窄边界：**紧邻 `-` 的码不算**。
//
// 两端都是负向断言，因此比 `\b` 只紧不松（`\b` 允许的、除连字符相邻以外全都保留）：
// `已逾期 1 天 · RETEST 钱浩然`、`等级RETEST`、`RETEST，` 仍然命中——那正是
// 「视图渲染了裸 `kind`」与「`REMINDER_KIND_LABELS` 少了词条、`labelOf` 原样回退」
// 两种真实故障的形状（两者都出一串被空白或标点包着的裸码）。
// 误报会让这个测试很快被人关掉，边界必须收准。
const PATTERN_SOURCE = `(?<![\\w-])(${UNTRANSLATED_CODES.join('|')})(?![\\w-])`;

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

/**
 * 挑一个真的有档案事件的学生。
 *
 * 与上面两个同一条教训：随便挑一名有档案的学生看起来也行（每条档案都有开档事件），
 * 但那是**没被断言过的假设**——真正要证明「有东西可扫」的正是它，所以要问接口。
 * 事件是按 `care_case_id` 过滤的（§16.4：这条时间线只读「这一份」），所以取的是
 * 当前那一条档案的 `events`。
 */
async function studentWithCaseEvents(page: Page): Promise<number> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { account: '13800000001', password: '123456', role: 'counselor' },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
  const cases = await (await page.request.get('/api/v1/care-cases', { headers })).json();
  for (const item of cases.data.items as { student_id: number }[]) {
    const detail = await (
      await page.request.get(`/api/v1/care-cases/${item.student_id}`, { headers })
    ).json();
    if (detail.data.events.length > 0) return item.student_id;
  }
  throw new Error('演示数据里没有任何档案带事件，这个用例失去了对象');
}

/**
 * 挑一个**有测评结果、但没有关注档案**的学生。
 *
 * 这是「学生测评记录」页唯一的对象——「未建档」不是随便挑一个人就能碰上的，
 * 而它恰恰是**大多数**学生的状态：全库唯一的开档触发点是重点题 85 / 97 命中
 * （`assessment_service.maybe_raise_risk_events` 的 docstring 逐字写着
 * 「Only 重点题命中写行」），关注等级本身从不建档。
 *
 * 两个条件都必须由接口回答，缺一不可：
 * - `case_id == null`：否则扫到的是**另一个**渲染分支（底部那句「该生尚未建档」
 *   与那枚按钮都不出现），而用例会照旧全绿；
 * - `total_level != null`：它等价于「至少交过一次卷」，也就是历次表至少一行——
 *   没有它，表是空的，扫的是一块空区域（与 `studentWithRetestPlan` 同一个坑）。
 *
 * 挑不到就抛，不静默退化。
 */
async function studentWithoutCase(page: Page): Promise<{ student_id: number; student_name: string }> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { account: '13800000001', password: '123456', role: 'counselor' },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
  const results = await (await page.request.get('/api/v1/students/results', { headers })).json();
  for (const item of results.data.items as {
    student_id: number;
    student_name: string;
    total_level: string | null;
    case_id: number | null;
  }[]) {
    if (item.case_id === null && item.total_level !== null) {
      return { student_id: item.student_id, student_name: item.student_name };
    }
  }
  throw new Error('演示数据里没有「有等级、无档案」的学生，这个用例失去了对象');
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
      // 导出中心（V1.2 阶段 8）。**这条路径守不住那三张词表**——没有作业行时这一页
      // 是空态，一个格子都不渲染（实测过：把那三处标签函数换成裸字段，四条角色用例
      // 全绿）。真正的覆盖是本文件末尾那条自成一套的用例，它先用接口建一份作业。
      // 这一条留在这里，守的是这一页上不依赖数据的那部分像素。
      '/counselor/exports',
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
      // 导出中心（V1.2 阶段 8）。管理员看到的是**全部人的**作业、多一列「操作人」，
      // 而「下载」那一列对他不出现——所以这一趟扫到的像素与心理老师那一趟并不重合。
      // 与上面同一条：作业表是空的时候它一个格子也扫不到。
      '/admin/exports',
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

    // 「目标学生」页签（V1.2 第 1 期）是**同一个弹层里的第二个面**，它多出一列
    // `target_source`（任务范围 / 补发）。上面那次扫描到不了它——页签内容不点不开，
    // 而这一列不翻译的话界面上就是 `TASK_SCOPE`。所以先点过去，再扫一遍。
    const modal = page.locator('.modal-panel').first();
    await modal.getByRole('button', { name: '目标学生' }).click();
    // 先证明有东西可扫，再断言它干净：目标行还没渲染时扫的是一块空区域。
    await expect(modal.locator('tbody tr').first()).toBeVisible();
    expect(await leakedCodes(modal), '目标学生页签把 target_source 编码原样显示了').toEqual([]);
  });

  /**
   * 「登录设备」弹层（V1.2 阶段 8 / §16.5）。
   *
   * `AUTH_SESSION_STATUS_LABELS` 三个码唯一的渲染点就是这一层里的药丸，而它在顶栏那个
   * 按钮后面——**不点开就不存在**。`auditPages` 走页面的那几趟都到不了它（它不属于任何
   * 一条路由），所以必须单独走一遍。
   *
   * 三档里 `EXPIRED` 要等 `expires_at` 走过去（e2e 里等不了，那是 `access_token_expire_minutes`
   * 的函数），所以这条用例造的是**另一档**：真的撤销一条，`REVOKED` 就有了。两条会话都用
   * 接口建，**只撤销本用例自己建的那一条**——浏览器里那一台不能撤（撤销它等于当场退出，
   * 后面的扫描就没法做了）。
   */
  test('顶栏的登录设备弹层', async ({ page }) => {
    const openSession = async () => {
      const response = await page.request.post('/api/v1/auth/login', {
        data: { account: '13800000001', password: '123456', role: 'counselor' },
      });
      return (await response.json()).data.access_token as string;
    };
    const doomed = await openSession();
    const survivor = await openSession();

    // 每条会话的 `is_current` 是**相对提问的那个 token** 算的，所以这里要问 doomed
    // 自己那一趟才能拿到它自己的 id（用 survivor 的 token 去问，拿到的会是 survivor）。
    const mine = (await (await page.request.get('/api/v1/auth/sessions', {
      headers: { Authorization: `Bearer ${doomed}` },
    })).json()).data.items.find((item: { is_current: boolean }) => item.is_current);
    const revoked = await page.request.post(`/api/v1/auth/sessions/${mine.id}/revoke`, {
      headers: { Authorization: `Bearer ${survivor}` },
      data: { reason: 'e2e词表用例' },
    });
    expect(revoked.ok(), '撤销会话没有成功，这一条就失去了 REVOKED 那一档').toBeTruthy();

    await loginAs(page, 'counselor');
    await page.getByRole('button', { name: '登录设备' }).click();
    const modal = page.locator('.modal-panel').first();

    // 先证明有东西可扫：这一台必然在，而刚撤销的那一条也必须在。**两档一起才算数**——
    // 只有「这一台」时，一个把 `authSessionStatusLabel` 漏掉的实现照样能让上面那句通过。
    //
    // 数**条数**不写死：`REVOKED` 的行会随每一次 e2e 攒下来（每一次 `loginAs` 都落一条
    // 会话，本用例再撤一条），写死数字会红在一个与功能无关的地方。
    await expect(modal.locator('.session-row').first()).toBeVisible();
    await expect(modal.locator('.session-row', { hasText: '已撤销' }).first()).toBeVisible();
    expect(await leakedCodes(modal), '登录设备把后端编码原样显示了').toEqual([]);
  });

  /**
   * 导出中心（V1.2 阶段 8 / §16.3）。
   *
   * 这一页是 `EXPORT_TYPE_LABELS` / `MASK_LEVEL_LABELS` / `EXPORT_JOB_STATUS_LABELS`
   * 三张表**唯一**的渲染点，而它同时是这一组里**唯一一页数据不是演示数据给的**：
   * `seed_demo` 不种导出作业（种子的作用是让页面有东西可看，不是伪造操作记录），
   * 所以 `auditPages` 走它那一趟扫的是一张空表——清单里加了路径不等于有了覆盖。
   * 变异验证量过这一点：把三处标签函数换成裸字段，四条角色用例全绿。
   *
   * 所以这一条先用接口**造一份作业**再扫，与 `studentWithRetestPlan` 那一族同一条
   * 规矩（**先证明有东西可扫，再断言它干净**），只是「造」这一步走的是写接口而不是
   * 挑一条已有的数据。
   *
   * 一份作业走完两个状态：先 `READY`（建完就能下），再撤销成 `REVOKED`。两档共用同
   * 一行——`REVOKED` 与 `READY` 是同一列上的两个取值，各造一行只会让共享库多攒一份
   * 没人看的记录。`EXPIRED` 到不了（要等 `expires_at`，e2e 里等不了）。
   */
  test('导出中心的作业台账', async ({ page }) => {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };

    // 基线的库里一份关怀档案都没有，而导出照样成功（回一份只有表头的 CSV）——
    // 这一条要的是**作业行**，不是文件里的行。
    const created = await page.request.post('/api/v1/care-cases/export', {
      headers,
      data: { purpose: 'e2e词表用例' },
    });
    expect(created.ok(), '建导出作业没有成功，这一条失去了对象').toBeTruthy();
    const job = (await created.json()).data;
    expect(job.job_no).toMatch(/^EXPORT-/);

    await loginAs(page, 'counselor');
    await page.goto('/counselor/exports');
    await page.waitForLoadState('networkidle');

    // 先证明有东西可扫：**刚建的那一行**在表里，而且三格各自读了一张表。
    const row = page.locator('tbody tr', { hasText: job.job_no }).first();
    await expect(row).toBeVisible();
    await expect(row).toContainText('关注档案摘要');
    await expect(row).toContainText('姓名已遮蔽');
    await expect(row).toContainText('可下载');

    // 撤销（接口层做，界面上那个按钮在别处已经有人管），再刷新看第二档。
    const revoked = await page.request.post(`/api/v1/export-jobs/${job.id}/revoke`, {
      headers,
      data: { reason: 'e2e词表用例' },
    });
    expect(revoked.ok(), '撤销导出作业没有成功，这一条就失去了 REVOKED 那一档').toBeTruthy();

    await page.reload();
    await expect(page.locator('tbody tr', { hasText: job.job_no }).first()).toContainText('已撤销');

    expect(await leakedCodes(page.locator('body')), '导出中心把后端编码原样显示了').toEqual([]);
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

  /**
   * 「档案事件」页签（V1.2 阶段 7 / §16.4）。
   *
   * `CARE_EVENT_LABELS` 的八个码唯一的渲染点就是这条时间线。它与上面两个页签有一处
   * 结构上的不同：**事件不是切过去才请求的**（`GET /care-cases/{id}` 一次把 `events`
   * 带回来），所以 `auditPages` 走 `/counselor/cases/1` 那一趟其实也扫得到它。
   *
   * 那为什么还要单独一条：**因为那一趟的「有东西可扫」是没被断言过的**——它挑的是
   * `/counselor/cases/1` 这名学生，而「这名学生恰好有事件行」在那一刻只是碰巧成立。
   * 一条没有事件的档案同样会让那一趟全绿，而它扫的是一块空区域。这与
   * `studentWithRetestPlan` / `studentWithComparison` 是同一个坑的第三例。
   */
  test('学生档案的档案事件页签', async ({ page }) => {
    await loginAs(page, 'counselor');
    const studentId = await studentWithCaseEvents(page);

    await page.goto(`/counselor/cases/${studentId}`);
    await page.waitForLoadState('networkidle');
    await page.getByRole('button', { name: '档案事件' }).click();

    // 先证明有东西可扫，再断言它干净。
    await expect(page.locator('.timeline-item').first()).toBeVisible();
    // 而且起点那一条必然在：`CASE_OPENED` 是这条生命的开端，任何一份档案都有它——
    // 这一句让上面那条「有东西可扫」不至于依赖演示数据里恰好有谁被复核过。
    //
    // 定位到 `.pill`（那一行事件的名字）而不是整条 `.timeline-item`：`hasText` 是
    // **子串**匹配，而 `CASE_OPENED` 自己的 `reason` 就写着「系统自动开档」——
    // 按整条匹配时，一个把名字渲染成 `CASE_OPENED` 的坏实现**照样能命中这一条**
    // （它命中的是正文里那两个偶然出现的字）。名字与正文分开之后，这一条断的
    // 才真是「那格药丸上写的是中文」。
    await expect(page.locator('.timeline-item .pill').filter({ hasText: '开档' })).toHaveCount(1);
    expect(await leakedCodes(page.locator('body')), '档案事件把后端编码原样显示了').toEqual([]);
  });

  /**
   * 名册导入的批次历史与逐行明细（V1.2 阶段 3）。
   *
   * `ROSTER_BATCH_STATUS_LABELS` / `ROSTER_ROW_STATUS_LABELS` / `ROSTER_CONFLICT_LABELS`
   * 三张表的**唯一**渲染点都是这一页上的「导入批次」表与它点开的明细弹层，而这一屏
   * 扫得到东西的前提是库里真有一批——`make seed-demo` 不产生批次。所以**先通过接口
   * 传一份文件把批次造出来，再扫**，与 `studentWithRetestPlan` 同一条规矩。
   *
   * 用「预览」而不是「提交」是有意的：预览落 `PREVIEW` 批次与逐行明细，但**不改任何
   * 名册字段**——共享库里「覆盖」会把 S001 挪出种子里的那个班，跑一次就让别的用例看到
   * 另一份名册（本文件上面那组 e2e 只跑「放弃」就是同一个理由）。文件内容固定，
   * 于是同一个操作者重复预览会命中服务端的**复用规则**，这一条跑多少次都不会让批次表
   * 越堆越长。
   */
  test('组织学生的导入批次', async ({ page }) => {
    const csv = 'student_no,name,grade,class_name\nS001,e2e词表预演,初二,801\n';
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: 'admin', password: '123456', role: 'admin' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const preview = await page.request.post('/api/v1/student-roster/import/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-vocabulary.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(csv, 'utf-8'),
        },
      },
    });
    // 造不出批次就抛，而不是静默退化成一条恒绿的用例——它扫的会是一块空区域。
    expect(preview.ok(), '名册导入预览没有成功，这一条失去了对象').toBeTruthy();

    await loginAs(page, 'admin');
    await page.goto('/admin/organization');
    await page.waitForLoadState('networkidle');

    const batches = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) });
    await expect(batches.locator('tbody tr').first()).toBeVisible();
    expect(await leakedCodes(batches), '导入批次把后端编码原样显示了').toEqual([]);

    // 逐行明细在弹层里，只有点开才存在——同工作台档案弹层那条的理由。
    await batches
      .locator('tbody tr', { hasText: 'e2e-vocabulary.csv' })
      .first()
      .getByRole('button', { name: '查看明细' })
      .click();
    const modal = page.locator('.modal-panel').first();
    await expect(modal.locator('tbody tr').first()).toBeVisible();
    expect(await leakedCodes(modal), '导入明细把后端编码原样显示了').toEqual([]);
  });

  /**
   * MHT 测评记录导入的批次历史与逐行明细（V1.2 第 4 期）。
   *
   * `MATCH_STATUS_LABELS`（九个码）与 `IMPORT_CONFLICT_LABELS`（三个码）唯一的渲染点是
   * `/counselor/data` 的「导入批次」表与点开它才存在的明细弹层，而这一屏扫得到东西的前提
   * 是库里真有一批——`make seed-demo` 不产生批次（导入不是种子会做的动作）。所以与上面
   * 那条名册用例同一条规矩：**先通过接口传一份文件把批次造出来，再扫**。
   *
   * 用「预览」而不是「提交」：预览只落批次与逐行明细，**一行测评会话都不写**。文件内容
   * 固定，于是同一个操作员重传同一份文件会命中服务端的复用规则（`_reusable_batch`：同一
   * 个人 + 同一份 sha256 + 仍是 `PREVIEW`），这一条跑多少次都不会让批次表越堆越长。
   *
   * 两行的取值是**确定的**，不依赖名册此刻长什么样：第一行空姓名必然落到 `INVALID_ROW`
   * （第 2 步就返回，还没走到查名册），第二行是个名册上不存在的姓名、必然落到 `NOT_FOUND`。
   * 写死一个「年龄不符」或「本月已导过」的行做不到这一点——那要看库里有没有这个人。
   */
  test('MHT导入的批次明细', async ({ page }) => {
    // 一百个题号列一个都不能少：少了的话整份文件在**列级**就被 422 挡下来，批次根本不会
    // 建出来，而这一条会退化成一个「表格是空的 → 没有裸编码 → 绿」的空转用例。
    const header = [
      '姓名', '性别', '年龄', '年级', '班级', '所用时间',
      ...Array.from({ length: 100 }, (_, i) => `${i + 1}.题干`)
    ].join(',');
    const cell = (name: string) =>
      [name, '1', '12', '1', '4', '3600秒', ...Array(100).fill('0')].join(',');
    const csv = `${header}\n${cell('')}\n${cell('e2e词表查无此人')}\n`;

    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const preview = await page.request.post('/api/v1/assessment-imports/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-vocabulary-assessment.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(csv, 'utf-8'),
        },
        batch_name: 'e2e词表预演',
        tested_on: '2026-09-19',
      },
    });
    // 造不出批次就抛，而不是静默退化成一条恒绿的用例——它扫的会是一块空区域。
    expect(preview.ok(), 'MHT 导入预览没有成功，这一条失去了对象').toBeTruthy();

    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');
    await page.waitForLoadState('networkidle');

    const batches = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) });
    // 按批次名称定位这一批（批次号按天编号，写死会红在一个与功能无关的地方）。
    const row = batches.locator('tbody tr', { hasText: 'e2e词表预演' }).first();
    await expect(row).toBeVisible();
    // 批次列表那一列的状态药丸走 `importBatchStatusLabel`（`PREVIEW` → 预览中）。
    expect(await leakedCodes(batches), '导入批次把后端编码原样显示了').toEqual([]);

    // 逐行明细只有点开才存在——同工作台档案弹层那条的理由。`match_status` 与
    // `conflict_code` 两列都在这个弹层里，而它们**只看得到像素**（第一面管不到视图）。
    await row.getByRole('button', { name: '查看明细' }).click();
    const modal = page.locator('.modal-panel').first();
    // 先证明有东西可扫（两行都在），再断言它干净。
    await expect(modal.locator('tbody tr')).toHaveCount(2);
    // 「导入形态」那一格是 `EXTERNAL_FULL_ANSWER` 唯一的渲染点，而它与汇总档在批次
    // 列表上长得一样——所以这一句是那条清单项的「有东西可扫」证明，不是重复。
    await expect(modal.locator('.detail-row', { hasText: '导入形态' })).toContainText('逐题答卷');
    expect(await leakedCodes(modal), 'MHT导入明细把后端编码原样显示了').toEqual([]);
  });

  /**
   * 导入明细的筛选项（V1.1.6）。
   *
   * 用户报的是「查看明细 / 查看全部明细 按扭展示的明细内容希望增加过滤条件，以便能快速
   * 筛出有问题的学生」。两个筛选条件（匹配结论那几片、关键词框）**都在服务端生效**
   * ——这一页是服务端分页的（默认 50 行），筛在客户端只能筛出当前那一页里符合条件的那几行，
   * 而屏幕上那个「共 N 条」会随翻页变化、操作员看不出来。后端那一侧由
   * `test_assessment_import_api.py` 的五条钉住，而它们看不见组件有没有把那两片渲染出来、
   * 有没有把 `match_group` 传下去——这一条补的正是那一半。
   *
   * ★ 定位器**必须按中文片名**（`无法导入` / `可导入`），因为这是 §3 第二面在这张表上的
   * **唯一**形态：`MATCH_GROUP_LABELS` 的键是小写的 `ready` / `error`，而
   * `UNTRANSLATED_CODES` 那套 `SCREAMING_SNAKE` 机制（`leakedCodes`）**扫不到它们**
   * ——把 `matchGroupLabel(key)` 换成裸 `key` 之后屏幕上出来的是 `ready · 0`，
   * 而下面那句 `leakedCodes(...)` 照样是空的。所以「这个中文找不找得到」本身就是判据。
   *
   * **两片各断一件事，缺一句都不成立**：
   *   * `无法导入` 断**有东西可筛**：这两行的结论是确定的（空姓名在第 2 步就返回
   *     `INVALID_ROW`，还没走到查名册；另一个名字名册上必然没有），点过去仍然是这 2 行
   *     ——「筛对了不会少」，同时片上那个中文说明词表接上了。
   *   * `可导入` 断**真的在筛**：这一批一行可导入的都没有，点过去必须是 0 行。
   *     少了这一句，一个「筛选完全没生效」的实现也能让上面那一条通过——筛不动时
   *     当然一行不少，而那正是这个功能要防的事。
   *
   * e2e 里只有 `error` 那一片有行：`AGE_CONFLICT` / `CONFLICT` / `DUPLICATE` 的前提是
   * 「匹配到了某个学生」，而演示名册的班级叫 `1班`、文件那一列按学校编号规则必须写
   * `704`，匹配这一步永远走不到「找人」（§26 / §27 记着这条取舍）。所以「待确认」
   * 与「其中：与在线答卷冲突」两片在这里都是 0 行，`conflict` 那一片干脆不渲染。
   *
   * 批次照上一条的规矩造：**只预览**（一行测评记录都不写），文件内容固定，于是重跑命中
   * 服务端的复用规则（同操作者 + 同 `file_sha256` + 仍是 `PREVIEW` + 同 `task_id`），
   * 批次表不越堆越长。
   */
  test('导入明细的筛选项按匹配结论过滤', async ({ page }) => {
    // 与上一条同一份表头（一百个题号列一个都不能少，少了整份文件在列级就被挡下来）。
    const header = [
      '姓名', '性别', '年龄', '年级', '班级', '所用时间',
      ...Array.from({ length: 100 }, (_, i) => `${i + 1}.题干`)
    ].join(',');
    const cell = (name: string) =>
      [name, '1', '12', '1', '4', '3600秒', ...Array(100).fill('0')].join(',');
    // 名字要能**按关键词找得到**（那一格搜的是文件里写了什么），所以第二个名字带一个
    // 只属于它的词；第一个是空姓名，它在第 2 步就返回 `INVALID_ROW`。
    const csv = `${header}\n${cell('')}\n${cell('e2e筛选无此人')}\n`;

    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const preview = await page.request.post('/api/v1/assessment-imports/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-vocabulary-filter.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(csv, 'utf-8'),
        },
        batch_name: 'e2e词表筛选',
        tested_on: '2026-09-19',
      },
    });
    expect(preview.ok(), '筛选那一批的预览没有成功，这一条失去了对象').toBeTruthy();

    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');
    await page.waitForLoadState('networkidle');

    const row = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) })
      .locator('tbody tr', { hasText: 'e2e词表筛选' })
      .first();
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: '查看明细' }).click();
    const modal = page.locator('.modal-panel').first();

    // 先证明有东西可扫：两行都在。下面每一条筛选断言都建立在这一句之上。
    await expect(modal.locator('tbody tr')).toHaveCount(2);

    const rows = modal.locator('tbody tr');
    const keyword = modal.getByPlaceholder('搜索行号、姓名、学号、年级或班级');

    // ① 关键词：按文件里的姓名筛，只剩那一行。这一句是那个搜索框**真的接上了服务端**
    //    的唯一机器判据（防抖 + `@input` 那一段没有别的守卫看得见）。
    await keyword.fill('e2e筛选无此人');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('e2e筛选无此人');

    // 清掉关键词再往下走：下面两条断的是「片」的效果，留着关键词会让它们变成两个变量的和。
    await keyword.fill('');
    await expect(rows).toHaveCount(2);

    // ② 「无法导入」那一片：这两行都属于它，所以点过去一行不少。
    //    这一句同时是**词表**的判据——片名回退成 `error` 时这个定位器就找不到了。
    await modal.getByRole('button', { name: '无法导入' }).click();
    await expect(rows).toHaveCount(2);

    // ③ 「可导入」那一片：一行都没有 → 空态是那一句**关于筛选**的话，而不是
    //    「这一批没有逐行记录」（§14：空态是一句关于数据的话，两句混了就会误导）。
    await modal.getByRole('button', { name: '可导入' }).click();
    // ★ 次序不能换：`loadDetailPage` **取数之前先把 `detailRows` 清空**（§14「一次失败的
    // 读取不许留下上一次的答案」），所以切片那一瞬间 `tbody tr` 本来就是 0——先断
    // `toHaveCount(0)` 的话，一个**筛选完全没生效**的实现也会在那一段窗口里绿过去。
    // 那一句空态排在 `detailLoading` 之后（加载中渲染的是骨架屏），所以「它出现了」= 这一次
    // 读取已经结束、而结果是 0 行；把这一句放在前面，下面那句才是一句有内容的话。
    await expect(modal.locator('p', { hasText: '没有符合当前筛选条件的行' })).toBeVisible();
    await expect(rows).toHaveCount(0);

    // ④ 那条出路真的能用。「清除筛选」这时有**两个**（工具栏一个、空态里一个，
    //    后者正立在操作员困惑的那一处），所以取第一个；两个都清同一对条件。
    await modal.getByRole('button', { name: '清除筛选' }).first().click();
    await expect(rows).toHaveCount(2);

    // 筛选项那一行不出裸编码——片名走的是 `matchGroupLabel`，兜底是 `—`，
    // 所以这一句挡的是第三种情况：某一片被渲染成了别的什么码。
    expect(await leakedCodes(modal), '导入明细的筛选项把后端编码原样显示了').toEqual([]);
  });

  /**
   * 汇总档（`EXTERNAL_SUMMARY`，V1.2 阶段 5 / §18.9）。
   *
   * 与上一条同一条规矩：**先通过接口把批次造出来再扫**，而且只「预览」——预览一行测评
   * 记录都不写。差别全在文件里：这一份**没有题号列**，只有「总分」与维度分，于是后端
   * 判出来的是 `EXTERNAL_SUMMARY`，界面上那一格读作「只有分数」。
   *
   * 这一档在**别的每一页上**都是隐形的（`assessment_result` 上没有这一场的结果，
   * 所以个案详情、重点学生、关注率都看不到它），所以这一格是操作员唯一能知道
   * 「这一批只有分数」的地方——它不翻译就等于没有。
   *
   * 两行仍取**确定**的取值（空姓名 → `INVALID_ROW`、查无此人 → `NOT_FOUND`），
   * 与上一条同一条理由：与名册此刻长什么样无关。汇总档也走同一套匹配（§18.4 的六步），
   * 它只是**匹配上之后**不写 100 条答卷而已。
   */
  test('MHT导入的汇总档', async ({ page }) => {
    // 六个身份列 + 总分与维度分，**一个题号列都没有**——少了题号列正是这一档的判据。
    const header = ['姓名', '性别', '年龄', '年级', '班级', '所用时间', '总分', '学习焦虑'].join(',');
    const cell = (name: string) => [name, '1', '12', '1', '4', '3600秒', '60', '55'].join(',');
    const csv = `${header}\n${cell('')}\n${cell('e2e词表查无此人')}\n`;

    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const preview = await page.request.post('/api/v1/assessment-imports/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-vocabulary-summary.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(csv, 'utf-8'),
        },
        batch_name: 'e2e词表汇总',
        tested_on: '2026-09-19',
      },
    });
    expect(preview.ok(), '汇总档预览没有成功，这一条失去了对象').toBeTruthy();

    await loginAs(page, 'counselor');
    await page.goto('/counselor/data');
    await page.waitForLoadState('networkidle');

    const row = page
      .locator('.card')
      .filter({ has: page.getByRole('heading', { name: '导入批次' }) })
      .locator('tbody tr', { hasText: 'e2e词表汇总' })
      .first();
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: '查看明细' }).click();
    const modal = page.locator('.modal-panel').first();
    await expect(modal.locator('tbody tr')).toHaveCount(2);
    await expect(modal.locator('.detail-row', { hasText: '导入形态' })).toContainText('只有分数');
    expect(await leakedCodes(modal), '汇总档的明细把后端编码原样显示了').toEqual([]);
  });

  /**
   * 任务详情页的「未匹配行」页签（V1.2 阶段 5）。
   *
   * `UNMATCHED_REASON_LABELS` 的码与 `MATCH_STATUS_LABELS` 是同一批（那张表只改了
   * `MATCHED` 一条），所以这一条要证明的**不是**「词表认得这些码」——`/counselor/data`
   * 那一条已经证明过了——而是这一屏有没有接上标签函数。同一个编码的第二个渲染点，
   * 在 §3 那张契约里是与「后端发的码认不认得」**分开的另一面**：后端发的完全正确，
   * 组件写 `{{ row.match_status }}` 时那边照样全绿。
   *
   * 数据同样先通过接口造，但这一批要**绑在这场任务上**（上一条绑的是空）——这一屏的
   * 判据是 `assessment_import_batch.task_id`，绑不上去它就一行都不显示。绑上去只是
   * 落一个 `PREVIEW` 批次与两行明细，**一行测评记录都不写**；文件内容固定，所以重跑
   * 命中服务端的复用规则，批次表不会越堆越长。
   *
   * 挑哪一场任务不写死：列表是接口给的，按「发放人数最多」挑（与 `app.spec.ts` 的
   * `openLargestTaskModal` 同一个判据），挑不到就抛——写死任务名会红在一个与功能
   * 无关的地方（任务名是种子数据的一部分）。
   */
  test('测评任务的未匹配行页签', async ({ page }) => {
    const login = await page.request.post('/api/v1/auth/login', {
      data: { account: '13800000001', password: '123456', role: 'counselor' },
    });
    const headers = { Authorization: `Bearer ${(await login.json()).data.access_token}` };
    const tasks = (await (await page.request.get('/api/v1/assessment-tasks', { headers })).json())
      .data.items as { id: number; name: string; total_targets: number }[];
    expect(tasks.length, '演示数据里应当至少有一场任务').toBeGreaterThan(0);
    const task = tasks.reduce((best, item) => (item.total_targets > best.total_targets ? item : best));
    expect(task.total_targets, '演示数据里应当至少有一个带目标行的任务').toBeGreaterThan(0);

    const header = [
      '姓名', '性别', '年龄', '年级', '班级', '所用时间',
      ...Array.from({ length: 100 }, (_, i) => `${i + 1}.题干`)
    ].join(',');
    const cell = (name: string) =>
      [name, '1', '12', '1', '4', '3600秒', ...Array(100).fill('0')].join(',');
    const csv = `${header}\n${cell('')}\n${cell('e2e词表未匹配查无此人')}\n`;

    const preview = await page.request.post('/api/v1/assessment-imports/preview', {
      headers,
      multipart: {
        file: {
          name: 'e2e-vocabulary-unmatched.csv',
          mimeType: 'text/csv',
          buffer: Buffer.from(csv, 'utf-8'),
        },
        batch_name: 'e2e词表未匹配',
        tested_on: '2026-09-19',
        task_id: String(task.id),
      },
    });
    expect(preview.ok(), '绑定任务的预览没有成功，这一条失去了对象').toBeTruthy();
    // 批次号要从**这次预览的应答**里取，不能从屏幕上读、更不能写死：
    // 见下面筛选那一段的理由。
    const batchNo = (await preview.json()).data.batch_no as string;
    expect(batchNo, '预览没有回批次号，这一条失去了定位自己那一批的办法').toBeTruthy();

    await loginAs(page, 'counselor');
    await page.goto('/counselor/tasks');
    const row = page.locator('tbody tr', { hasText: task.name }).first();
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: '查看明细' }).click();
    const modal = page.locator('.modal-panel').first();
    await modal.getByRole('button', { name: '未匹配行' }).click();

    // ★ 先把自己那一批筛出来，再做「先证明有东西可扫」那一组断言。
    //
    // 这一屏列的是**整场任务**下所有批次的未匹配行（判据是 `batch_id → task_id`），
    // 而开发库是共享的：别的批次（演示数据、别的一次排查、以前跑过的某条用例）只要
    // 也绑在这场任务上，它们那些「名册上没有」的行就会与这一批的一起出现。于是
    // 「这一行有错误」/「名册上没有」各一条的断言会变成**数据依赖**的——它其实在说
    // 「这场任务下只有我这一批」，而那不是这条用例要证明的事。
    //
    // 筛的是**批次号**（这一批自己刚拿到的那一个），所以无论库里还有什么都不受影响；
    // 「批次」那一列本来就是这一屏的一等公民（`row_no` 只是批内编号，一场任务下可以
    // 有好几批）。上面那条「整场共 N 行没进得去」的口径句**不筛**——它说的正是整场。
    await modal.getByPlaceholder('按批次、行号、姓名、年级或班级筛选').fill(batchNo);

    // ★ 页签是**切换**，不是**叠加**：切到这一屏之后弹层里只剩一张表。
    //
    // 这一条是 2026-09-20 修出来的那个缺陷的守卫。当时它不是「这一条用例红了」，
    // 是「这一条用例的断言收不到效」——`TasksPage.vue` 里完成明细那支 `v-else`
    // 的判据从前是「不是目标学生」，而「未匹配行」这个页签同样不是目标学生，
    // 于是它照样成立：两张表同时渲染、完成明细那个筛选框与「导出CSV」一起露在上面。
    // 症状是屏幕上叠了两张表，而**任何只断文案的用例都看不见它**。
    // 判据用 `thead tr` 而不是文案：它数的是「这一屏渲染了几张表」。
    await expect(modal.locator('thead tr')).toHaveCount(1);

    // 先证明有东西可扫。两行的结论都是**确定**的（空姓名在第 2 步就返回 `INVALID_ROW`，
    // 还没走到查名册；另一个名字名册上必然没有），所以按那一格的中文找得到它们。
    const mine = modal.locator('tbody tr').filter({ hasText: batchNo });
    await expect(mine).toHaveCount(2);
    await expect(mine.filter({ hasText: '这一行有错误' })).toHaveCount(1);
    await expect(mine.filter({ hasText: '名册上没有' })).toHaveCount(1);
    // 口径那一句（§9）：整场任务与「你看得见」是两个数，两个都要在。
    await expect(modal.getByText(/整场共 \d+ 行没进得去/)).toBeVisible();
    expect(await leakedCodes(modal), '未匹配行把后端编码原样显示了').toEqual([]);
  });

  /**
   * 「学生测评记录」页（2026-09-20）。
   *
   * 这一页存在的理由只有一个：**没有关注档案的学生也要看得到自己的测评事实**。
   * 在此之前 `GET /care-cases/{student_id}` 是唯一能读到「他考过几次」的接口，
   * 而它对未建档的学生回 404，前端又拿不到 HTTP 状态码（§2）——于是「重点学生」那张表
   * 上，未建档的行只有一个不可点的 `—`（用户 2026-09-20 报的就是这件事）。
   *
   * **不往 `auditPages` 那个静态路径清单里加这一页**：它需要一名学生的 id，
   * 而静态路径只会落在一张空表上——一条看起来在守、实际扫不到任何东西的守卫
   * （§3 里导出中心那次「清单加成功了，覆盖仍然是零」是同一个形状）。
   *
   * 这一页的编码渲染点有三处，各由一条 `labels.ts` 的表管：等级（`levelLabel`，
   * 卡片与表格里各一次）、来源（`sourceLabel`）、评分状态（`calculationStatusLabel`）。
   * 都**不条件渲染**，所以这一条扫得到它们。
   */
  test('未建档学生的测评记录页', async ({ page }) => {
    await loginAs(page, 'counselor');
    const student = await studentWithoutCase(page);

    await page.goto(`/counselor/students/${student.student_id}/records`);
    await page.waitForLoadState('networkidle');

    // 先证明有东西可扫：页头是**这一名**学生（不是一个刚好打开的页面），
    // 历次表至少一行，并且底部那句「未建档」在——它是这一页存在的理由本身。
    await expect(page.locator('h1')).toContainText(student.student_name);
    await expect(page.locator('tbody tr').first()).toBeVisible();
    await expect(page.getByText('该生尚未建档（重点题未命中）')).toBeVisible();

    expect(await leakedCodes(page.locator('body')), '测评记录页把后端编码原样显示了').toEqual([]);
  });
});
