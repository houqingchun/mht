"""数据中心 → MHT测评记录导入：把**外部平台**做出来的普查结果导进本系统分析。

学校在别的平台上做了 2026 年心理普查，结果在系统之外。这份服务把它变成真正的
`assessment_session` / `assessment_answer` / `assessment_result`，于是分析、个案详情、
受控导出立刻都能用上这批数据。

形状与另两个导入（`student_import_service` / `scale_import_service`）**在"坏单元格"那一层一致**：
一行错误是学校看得见、改得动的一行，不是拒掉整份文件，也不是悄悄存成 NULL。

但**中间那一跳不同，而且是有意的**（V1.2 的第 4 期）：学生信息导入与题库导入走
`preview → JWT 预览令牌 → commit`，令牌里装着整份预览；这一条走
`start_assessment_import → 批次（PREVIEW）+ 逐行明细落库 → commit_batch`，
**预览本身就写在库里**。理由是这一批数据要能被反复打开、逐行看、在有问题的行上做
处置，而令牌是一次性的、也无处可选。§18.4 那条出路——「找不到学生时先补名册、
再重新匹配」——只有在这一层落了库之后才成立：重传同一份文件会复用同一个批次
（同操作者 + 同指纹 + 仍 `PREVIEW`），逐行结论整批重算。

    parse_assessment_import  文件 → 行（纯解析）
    start_assessment_import  文件行 → 批次 + assessment_import_row + 外部结果
    commit_batch             批次 → 真正的测评记录


与「学生在本系统里作答」刻意不同的三处，都是用户确认过的决策：

* **定位不到的学生跳过并逐行报错**，不建学生、不建账号（名册归管理员的「学生信息导入」）；
* **建 COMPLETED 目标行**并挂在一个「外部导入」批次任务下——学校已确认接受它对
  全校完成率、关注率与高度关注导出的影响（CLAUDE.md 的决策记录里也记了这一条）；
* 会话与任务都打 `source="IMPORTED"`，学生在系统里既不能继续作答也不能重置它。

**同一个月只算一场任务，重复的那一条要人来拍板**（2026-09-17 用户要求）：
「重复性检测应该按月度为单位，不同月份的评测视为不同的测试任务」，所以

* 批次任务按 `(学校, 月份)` 找，找得到就复用，找不到才新建；**一条都没写进去时不建**——
  空任务在任务列表上永远显示「进行中」，还会被同月的下一次导入复用；
* 同一名学生在同一个月里已经有导入记录 → 预览时给出一条**待确认**（不是硬错误），
  由操作员选「覆盖上次」或「放弃这条」。

「年龄与名册不符」走同一套：它此前只是一句告警（照常导入、不动名册），现在也升成
**待确认**——因为「覆盖更新」这个选项的含义只能是**改动名册上的年龄**
（`student.age` 是那一列唯一的存储处，这条链路上没有第二个地方可以「覆盖」）。
两类待确认共用一次选择，因为它们问的是同一件事：「这份文件和库里已有的东西冲突了，
你要以文件为准，还是放弃这几条？」

**触发规则与系统内作答完全一致**（2026-09-17 用户确认，推翻了此前「只带进结果」的旧决策）：
评分之后照调 `maybe_raise_risk_events`，于是命中重点题（85/97 任一答「是」）就会开出
风险事件与关注档案，和学生在系统里交卷时走的是同一个函数。总分类别是重点关注但没命中重点题的，
两条路径一样**不建**——按总分放宽会造出「同一份答卷，系统内测和导进来结果不同」的不一致。

性别 `1/2` 与答案 `1/0` 是这份外部文件的约定（用户明确给了），不是本系统的词汇：
`2→MALE/1→FEMALE`、`1→YES/0→NO`，落库一律是系统里的编码。

**只收 .csv**：学校在外部平台上的导出另存为 CSV 之后再上传。所以这里只有一条解析
路径，也不为此引入任何 Excel 依赖（仓库里本来就没有）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    AssessmentTaskScope,
    DimensionResult,
    RiskEvent,
)
from app.models.care import ManualReview
from app.models.enums import ScopeType
from app.models.importing import (
    AssessmentExternalResult,
    AssessmentImportBatch,
    AssessmentImportRow,
    StudentAgeChangeLog,
)
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale, ScaleQuestion
from app.security.data_scope import (
    ensure_student_in_scope,
    student_in_scope,
    student_scope_predicate,
)
from app.services.assessment_service import (
    TESTED_AT_SOURCE_IMPORT_FILE,
    now_utc_naive,
    score_session,
)
from app.services.audit_service import write_audit
# `ExportDocument` 住在自己的模块里，就是为了在这里被 import 而不造出环：
# `task_service` ↔ `export_service`（经 `assessment_service`）本来是闭的，而这两处
# （未匹配行导出、未参与名单导出）都要返回同一个形状。它只是一个冻结的 dataclass。
from app.services.export_document import ExportDocument
# 导出的 CSV 由后端拼，「匹配结论」那一列的中文与 `labels.ts` 是同一份镜像表——用它，
# 不在这个模块里再写一遍那九个码的中文（第三面，见 `export_labels.py`）。
from app.services.export_labels import unmatched_reason_label
from app.services.import_text import read_csv_grid
# 未匹配行那一页的两道门槛直接复用 `list_task_targets` 的那一个判据（§22）。
# `task_service` 不 import 本模块，所以这里没有环；哪一天它需要 import 了，把这一个
# 判据搬到 `app/security/` 里去，而不是在两边各写一份。
from app.services.task_service import ensure_detail_exporter
from app.services.task_service import ensure_task_target_reader
from app.services.target_snapshot import target_snapshot
from app.services.task_service import ensure_task_reader

QUESTION_COUNT = 100

# 年级：文件里是裸数字 `1`，学校的班级编号首位是 `7`（见「学生信息导入」的
# `GRADE_CLASS_PREFIX`）。两种写法都认，中文名也认——同一份表在不同平台上导出来，
# 这一列三种样子都见过。
GRADE_BY_NUMBER = {"1": "初一", "2": "初二", "3": "初三", "7": "初一", "8": "初二", "9": "初三"}
GRADE_NAMES = {"初一": "初一", "初二": "初二", "初三": "初三"}
# 班级编号的首位数字 → 年级名，用来校验文件里已经写成 `704` 的那种值
CLASS_PREFIX_GRADE = {"7": "初一", "8": "初二", "9": "初三"}
GRADE_PREFIX = {"初一": "7", "初二": "8", "初三": "9"}

GRADE_INPUT_HINT = "年级应为 1/2/3（或 初一/初二/初三）"
CLASS_INPUT_HINT = "班级格式应为 4（初一 4 班）"
GENDER_INPUT_HINT = "性别 2 表示男、1 表示女"
# **注意方向：这份文件里 `2` 是男、`1` 是女**（2026-09-17 按学校手上的真实文件改，
# 此前反着写——`1→MALE`、`2→FEMALE`）。
#
# 反着写不是「显示错了性别」这么轻：这两个字段**只用来消歧**，从不写回名册
# （`commit_batch` 不碰 `student.gender` / `student.age`），而
# `locate_student` 是拿 姓名 + 性别 + 年龄 + 年级 + 班级 五样一起找人的。所以性别一取反，
# 「同班同名两人、一男一女」这种本来能分开的情况就会**定位到另一个人**，
# 结果是张冠李戴地把 A 的答卷记到 B 名下，而且全程不报错——
# 性别「不符」的那条 warning 反而因为所有人都取反而变成噪音，没人会去看。
#
# 学生信息导入（`student_import_service`）走的是另一套约定，只认 `MALE/FEMALE` 或
# `男/女`，**不认数字**。两条链路各管各的常量，别把这里改成通用的。
GENDER_BY_NUMBER = {"2": "MALE", "1": "FEMALE", "男": "MALE", "女": "FEMALE"}
ANSWER_HINT = "应为 1（是）或 0（否）"

MAX_BATCH_NAME_LENGTH = 128
MAX_DURATION_SECONDS = 2147483647  # `duration_seconds` 是 Integer，超了 MySQL 会 500
# 「这一场任务里没进得去的那几行」一页给多少条（§18.10）。默认 200 与逐行明细、
# 补发候选、任务完成明细同值；**调用方拿 `total` 与 `items` 比一下就知道有没有被截断**
# ——凡是截断都要自己说出来（§10）。
UNMATCHED_ROW_LIMIT = 200

# 「要人拍板」的两类冲突（2026-09-17 用户要求）。它们既不是错误（记录本身是好的），
# 也不是告警（不能就这么写进去）——预览时列出来，提交时由 `resolution` 一次性回答。
CONFLICT_AGE_MISMATCH = "AGE_MISMATCH"
CONFLICT_DUPLICATE = "DUPLICATE"

# 处置方式。**整份文件共用一次选择**：冲突行各自属于哪一类已经在明细里写清了，
# 让老师为 200 行逐行选一次只会让他在第 20 行开始乱点。
RESOLUTION_OVERWRITE = "overwrite"
RESOLUTION_SKIP = "skip"
RESOLUTIONS = {RESOLUTION_OVERWRITE, RESOLUTION_SKIP}
RESOLUTION_HINT = "处置方式应为覆盖（overwrite）或放弃（skip）"

# 年龄处置（§18.5 的三个选项）。
#
# **它不是 `RESOLUTIONS` 的第三个值，两个问题不是一个。** `RESOLUTIONS` 是**文件级**
# 的一次选择，回答「冲突的那些行写不写」；这一组回答的是**另一件事**——写下去的时候，
# 名册上那个年龄动不动、这一场按哪个年龄记。一份文件里「有 3 条年龄不符」与
# 「有 2 条本月已导过」是两件互不相干的事；用一个选择同时回答两个问题，就是 V1.0
# 那个把「覆盖上次」顺手当成「也覆盖年龄」的写法。
#
# 三个选项在**两处**留下的痕迹互不相同，所以它们都可观测（外部文件 13 岁、名册 12 岁）：
#
# | 选项 | `student.age` | `session.age_at_test` | 行上的 `age_resolution` |
# |---|---|---|---|
# | `keep_roster`  | 12（不动） | **12** | `keep_roster` |
# | `overwrite`    | 13（改写） | 13     | `overwrite` |
# | `session_only` | 12（不动） | **13** | `session_only` |
#
# §18.5 那两句是打架的：「无论是否覆盖 `student.age`，本次测评的 `age_at_test`
# 都必须保存**外部测评时年龄**」——按字面执行时前两个选项写进库的东西完全相同，
# 三个选项只剩两个。2026-09-19 用户裁决**让第二句让步**：`age_at_test` 记的是
# 「这一场我们决定按哪个年龄理解他」，而**文件里那个数一个字都没丢**，它还在
# `assessment_import_row.raw_age` 与 `age_after` 上，逐行明细里看得见。
#
# `overwrite` 这个字符串与 `RESOLUTION_OVERWRITE` **同值是有意的**：两列
# （`resolution` 与 `age_resolution`）本来就是两个字段，同一个词在两边是同一个意思
# ——「用文件里那个数盖掉系统里原来那个数」。V1.0 那条路径已经在往
# `assessment_import_row.age_resolution` 写 `overwrite` 了，沿用它可以少一次数据迁移。
AGE_RESOLUTION_KEEP = "keep_roster"
AGE_RESOLUTION_OVERWRITE = RESOLUTION_OVERWRITE
AGE_RESOLUTION_SESSION_ONLY = "session_only"
AGE_RESOLUTIONS = {
    AGE_RESOLUTION_KEEP,
    AGE_RESOLUTION_OVERWRITE,
    AGE_RESOLUTION_SESSION_ONLY,
}
AGE_RESOLUTION_HINT = (
    "年龄处置应为保留系统年龄（keep_roster）、覆盖学生当前年龄（overwrite）"
    "或只保存本次测评年龄（session_only）"
)

# 行上的 `age_resolution` **留空**是「这一行没有年龄问题、没人问过」，不是「选了某个默认」。
# 所以它只在一行**真的有年龄冲突**且真的被写下去时才写（`commit_batch`），而
# `resolved_by` / `resolved_at` 只在**逐行处置**过时才写——三列一起读才答得上
# 「这个年龄是谁定的、是整批选的还是他单独选的」。
AGE_NO_QUESTION = None


def parse_age_resolution(value: str) -> str:
    """把传来的年龄处置收进定义域，不合法就 422。

    **两个入口共用**：整批提交（`commit_batch`）与逐行处置（`resolve_import_row`）。
    各写一份 `in` 判断就会有第三种写法悄悄冒出来，而它只在前端提交时变成一句 422
    ——与 `RESOLUTIONS`（缺口 8）同一个理由。
    """
    if value not in AGE_RESOLUTIONS:
        raise AppError("VALIDATION_ERROR", AGE_RESOLUTION_HINT, 422)
    return value


def age_resolution_label(age_resolution: str | None) -> str:
    """年龄处置的中文，**只用于审计的 `detail`**（界面自己有一份 `labels.ts`）。

    `None` → 「未涉及（无年龄冲突）」而不是空串：与 `resolution_label` 逐字同一个
    理由——留空与「没人被问过」分不开，而事后读轨迹的人正要靠这一格判断「这次导入
    到底动没动名册上的年龄」。
    """
    return {
        AGE_RESOLUTION_KEEP: "保留系统年龄",
        AGE_RESOLUTION_OVERWRITE: "覆盖学生当前年龄",
        AGE_RESOLUTION_SESSION_ONLY: "只保存本次测评年龄",
    }.get(age_resolution or "", "未涉及（无年龄冲突）")


# --------------------------------------------------------------------------
# 匹配状态（§18.4 那张表，八个码，一个不多一个不少）
#
# 这张表是这一期最不能改的东西：它同时是**数据库里的值**（`assessment_import_row.match_status`）、
# **接口上的值**（逐行明细那一列）与**界面上的字**（`labels.ts` 的 `MATCH_STATUS_LABELS`）。
# 八个码一个不多一个不少，就是下面这八个（外加初始值 `PENDING`，它不是匹配结论）：
#
#   MATCHED / AGE_CONFLICT / AMBIGUOUS / NOT_FOUND / OUT_OF_SCOPE / DUPLICATE / CONFLICT
#   + INVALID_ROW
#
# **八个码分成几组是「谁救得回来」，而那是这套常量的唯一用途**——分组不写在注释里、
# 写成三张集合（`NEEDING_RESOLUTION` / `IMPORTABLE` / `UNIMPORTABLE`），因为它们各自
# 都有好几个读者：接口拒绝提交时数一遍、预览页那一行数一遍、落库时判一遍。写成三处
# 字面量的话，「屏幕上说 0 条待确认、点提交却说有 3 条」这种对话迟早会出现。
#
# **`PENDING` 也留着**，但它不是匹配结果：它是「行已落库、还没匹配过」的初始值
# （对齐阶段 DDL 的默认值）。批量插入之后立刻逐行匹配，所以正常情况下没有一行停在它上面——
# 留着它是为了让「有一行卡住了」可见，而不是让它冒充一个匹配结论。
MATCH_PENDING = "PENDING"
MATCH_MATCHED = "MATCHED"
MATCH_AGE_CONFLICT = "AGE_CONFLICT"
MATCH_AMBIGUOUS = "AMBIGUOUS"
MATCH_NOT_FOUND = "NOT_FOUND"
MATCH_OUT_OF_SCOPE = "OUT_OF_SCOPE"
MATCH_DUPLICATE = "DUPLICATE"
MATCH_INVALID_ROW = "INVALID_ROW"
MATCH_CONFLICT = "CONFLICT"

# 要操作员拍板的那三档（§18.4 表里「人工…后是 / 人工处置后决定」那一列）。
# 判据只有一条：**文件级的那一次选择（覆盖 / 放弃）救不救得回来**，而这又取决于
# 「这一行有没有一个已经定下来的学生」——这三档都有（`match_student` 已经把名字
# 对到人身上了），它们问的只是「写下去要不要盖掉原来那个」。
#
# **`DUPLICATE` 在这一组里，虽然 §18.4 的表头写的是「否」。** 那个「否」说的是
# 「不选任何处置时不会写进去」，而不是「永远写不进去」：V1.0 起「同一名学生同一个月
# 已有一次导入」就是一个**待确认**（缺口 8，用户 2026-09-17 要的），选「覆盖上次」
# 会就地重写那一场（`_rewrite_imported_session`）。把它从这里拿掉，就等于把那一年
# 之前就存在的「覆盖上次」这条路堵死——而它正是用户要的那个选项。
#
# **`AMBIGUOUS` 曾经在这一组里，第 4 期把它挪出去了。** §18.4 给它的处置是
# 「**人工选择**后是」——在几个候选里指出是哪一个人——而这一期给不出那个动作
# （那需要逐行处置的入口，见缺口）。留在里面的后果不是理论上的，是两件都会发生的事：
#
#   1. `commit_batch` 的「覆盖」分支会拿 `row.student_id` 去 `db.get(Student, …)`，
#      而它**必是 NULL**（连是哪一个人都还没定下来）→ `AttributeError` → 500；
#   2. 就算侥幸不崩，那句 422 也在承诺一件做不到的事：「请选择覆盖或放弃这些记录后
#      重试」，而**两个选择都不会把它写进去**。
#
# 所以这一期它按「进不去」处置（在 `MATCH_STATUSES_UNIMPORTABLE` 里），`message`
# 里写明学校此刻真能做的那件事（核对名册上这几名学生的性别与年龄）。逐行选择接上
# 之后，它再回到这一档里——那时 `commit_batch` 有地方接住它的 `student_id` 了。
MATCH_STATUSES_NEEDING_RESOLUTION = {
    MATCH_AGE_CONFLICT,
    MATCH_CONFLICT,
    MATCH_DUPLICATE,
}

# 哪些状态**可以**写进正式结果（§18.4 表最后一列）。划分只有一条判据：
# **选「覆盖」时这一行会不会写进去。**
#
#   MATCHED                      → 写（唯一候选、无冲突，不需要任何决定）
#   上面那三个「要拍板」的         → 选了覆盖才写
#   下面那四个                    → **任何选择都救不回来**（见 `UNIMPORTABLE`）
MATCH_STATUSES_IMPORTABLE = MATCH_STATUSES_NEEDING_RESOLUTION | {MATCH_MATCHED}

# 上面那个「任何选择都救不回来」的四档，单独取个名字：它**有两个读者**——
# `_match_batch_rows` 拿它写 `batch.error_rows`，`batch_row_counts` 拿它现算
# 预览页上那个「有问题 N 行」。写成两处字面量时，屏幕上那两个数会在某次改动之后
# 各说各话，而两边看起来都对。
#
# 四档进不来的理由各不相同，而**出路也各不相同**——这一点写在每一行的 `message`
# 里（那是操作员唯一会读的地方），不写在这里：
#
#   NOT_FOUND      名册上没有这个班 / 这个人   → 去名册上补
#   OUT_OF_SCOPE   名册上有他，但他不在这场任务里 → 去补发目标（§18.7 的
#                  `SUPPLEMENT_CANDIDATE` 是同一件事的另一半）
#   INVALID_ROW    这一行本身是坏的（缺姓名、班级与年级矛盾、答案格子坏）→ 改文件
#   AMBIGUOUS      同名同班几个人分不开（本阶段没有逐行选择的入口）→ 核对名册
MATCH_STATUSES_UNIMPORTABLE = {
    MATCH_INVALID_ROW,
    MATCH_NOT_FOUND,
    MATCH_OUT_OF_SCOPE,
    MATCH_AMBIGUOUS,
}

# 上面那三张集合的**第四次**分组：明细页的筛选项。键与 `batch_row_counts` 返回的四个键
# **逐字相同**，这不是巧合——筛选片上的数与筛出来的行必须是同一份名单（§11：指标卡上的数
# 必须与它点进去的那个列表同源）。两处各写一份字面量时，某天挪动一档只会改到其中一个，
# 屏幕上那个数与点进去的结果就对不上了，而两边看起来都对。
#
# 所以 `batch_row_counts` 也从这张表**推导**出来，而不是各数一遍。
#
# **`conflict` 是 `needing_resolution` 的子集**（与 `batch_row_counts` 里同一条）：
# 它不是一个并列的第五档，界面上那一片写的是「其中：与在线答卷冲突」。前三片
# （`ready` / `needing_resolution` / `error`）互不重叠、加起来恰好是整批的行数，
# 第四片是其中一片的放大镜。
MATCH_GROUPS: dict[str, tuple[str, ...]] = {
    "ready": (MATCH_MATCHED,),
    "needing_resolution": tuple(sorted(MATCH_STATUSES_NEEDING_RESOLUTION)),
    "conflict": (MATCH_CONFLICT,),
    "error": tuple(sorted(MATCH_STATUSES_UNIMPORTABLE)),
}

BATCH_STATUS_PREVIEW = "PREVIEW"
BATCH_STATUS_COMMITTED = "COMMITTED"

# 逐行的处置结果（`assessment_import_row.processing_status`）。与批次的五个计数
# （`created_rows` / `updated_rows` / `skipped_rows` / `error_rows`）是同一件事的两个粒度：
# 批次上那几个数是给人看的汇总，行上这个码是「这一行到底去哪了」。
ROW_PROCESSING_PENDING = "PENDING"
ROW_PROCESSING_CREATED = "CREATED"
ROW_PROCESSING_UPDATED = "UPDATED"
ROW_PROCESSING_SKIPPED = "SKIPPED"
ROW_PROCESSING_ERROR = "ERROR"

# 第 6 期加的那一个：这一行匹配上了、也处置过了，而**外部结果没有落成一场测评**。
# 只有两种处置会走到这里（`KEEP_ONLINE` / `REJECT_EXTERNAL`）：学校决定这一场以学生
# 自己答的那一份为准，于是外部那一份**只留原始事实**（`assessment_external_result`），
# 不建会话。它不是「被放弃」（`SKIPPED` 说的是这一行没进任何地方，连外部事实都没留）。
#
# **刻意不叫 `EXTERNAL_ONLY`。** 那个名字读起来是「只采用了外部结果」，而这一档的
# 事实恰好相反——一个字面意思正好反过来、又没有别的东西挡着的码，迟早会被下一个
# 读代码的人按字面用错。
ROW_PROCESSING_NOT_APPLIED = "NOT_APPLIED"

# 外部结果的来源类型（§18.9）：完整 100 题答案，还是平台算好的汇总。
SOURCE_TYPE_FULL_ANSWER = "EXTERNAL_FULL_ANSWER"
SOURCE_TYPE_SUMMARY = "EXTERNAL_SUMMARY"

DEFAULT_SOURCE_SYSTEM = "UNKNOWN"

# `assessment_import_row.conflict_code` 的取值（String(64)，比 `match_status` 宽，
# 因为它要写「哪一类冲突」而不是「匹配到什么程度」）。四个码与 `MATCH_*` 不是一一
# 对应：`CONFLICT_AGE_MISMATCH` / `CONFLICT_DUPLICATE` 沿用 V1.0 就在用的那两个
# （`_conflicts_for_student` 发的就是它们，前端也认这一套），后两个是「系统内已有
# 一份答卷」这一档**内部再分两半**（第 6 期）。
#
#   IN_SYSTEM_IN_PROGRESS  他正在这一场里作答、**还没交卷**
#   IN_SYSTEM_RESULT       他已经交卷了
#
# §18.8 那张表把它们分成两行，而出路不同：正在作答时**任何处置都不该动他手里那一份**
# （他可能正答到第 40 题，而作废它等于把二十分钟的工作扔掉）；已交卷时才轮得到
# 「以哪一份为准」。两者都**禁止自动覆盖**，这一点是共同的。
CONFLICT_IN_SYSTEM_RESULT = "IN_SYSTEM_RESULT"
CONFLICT_IN_SYSTEM_IN_PROGRESS = "IN_SYSTEM_IN_PROGRESS"

# --------------------------------------------------------------------------
# §18.8 的四种冲突处置
#
# 一个任务一个学生可以保留多个来源事实，但**同一时刻只能有一个有效结果**。两条来源
# 事实撞在一起时（学生自己在线答的 + 学校导入的外部结果），由人从这四档里选一档。
#
# **规格里只有这四个名字，没有说它们在库里留下什么**（`技术详细设计.md` 与
# `vibe-input/` 都零命中）。下面这张表是 2026-09-19 用户裁决的落点，四档都保留
# 「原始在线答卷、外部文件事实和人工选择」三样（§18.8 最后一句）：
#
# | 处置 | 外部会话 | 在线会话 | `external_result.verification_status` |
# |---|---|---|---|
# | `KEEP_ONLINE`      | **不建** | 保持有效 | `PENDING`（还挂着，以后还能再裁） |
# | `USE_EXTERNAL`     | 建，**有效** | `is_effective=0`、指新场 | `ACCEPTED` |
# | `REJECT_EXTERNAL`  | **不建** | 保持有效 | `REJECTED`（明确否掉，终态） |
# | `KEEP_BOTH_BUT_ONE_EFFECTIVE` | 建，`is_effective=0` | 保持有效 | `ACCEPTED` |
#
# 两处要点：
#
# 1. **`KEEP_ONLINE` 与 `REJECT_EXTERNAL` 的唯一数据差别就是 `verification_status`。**
#    它们在建不建会话、谁有效这几件事上完全一样，而那一列说的是**学校对这份外部结果
#    的态度**：「还没定」与「已经否了」是两件事。两列分工同样是清楚的：
#    `verification_status` 回答「这份外部结果学校认不认」，`is_effective` 回答
#    「哪一场是当前有效结果」。
#
#    **但那一列今天只有写入方、没有读者**（`grep verification_status`：三处写、
#    一个索引、零处读），所以这两档的差别目前只落在库里与审计的 `detail` 上——
#    「已经否了的那一份，下一次上传时按什么口径出现」还没有答案，见缺口 11。
#
# 2. **`USE_EXTERNAL` 不是「静默覆盖」。** §20#11 禁止的是**自动**覆盖：今天
#    （第 6 期之前）整批选一个「覆盖」就会把学生在线答的那一场就地改写掉，点一次按钮、
#    几份卷子作废，而屏幕上没有任何一句话说这件事。现在是**逐行选**、`is_effective`
#    留痕、`supersedes_session_id` 指得出新场、审计里写着「以外部为准」——
#    原始答卷一条不删。
CONFLICT_RESOLUTION_KEEP_ONLINE = "KEEP_ONLINE"
CONFLICT_RESOLUTION_USE_EXTERNAL = "USE_EXTERNAL"
CONFLICT_RESOLUTION_REJECT_EXTERNAL = "REJECT_EXTERNAL"
CONFLICT_RESOLUTION_KEEP_BOTH = "KEEP_BOTH_BUT_ONE_EFFECTIVE"
CONFLICT_RESOLUTIONS = {
    CONFLICT_RESOLUTION_KEEP_ONLINE,
    CONFLICT_RESOLUTION_USE_EXTERNAL,
    CONFLICT_RESOLUTION_REJECT_EXTERNAL,
    CONFLICT_RESOLUTION_KEEP_BOTH,
}
CONFLICT_RESOLUTION_HINT = (
    "来源处置应为保留在线（KEEP_ONLINE）、采用外部（USE_EXTERNAL）、"
    "否掉外部（REJECT_EXTERNAL）或两份都留但以在线为准（KEEP_BOTH_BUT_ONE_EFFECTIVE）"
)

# 四档里「外部那一场会不会被建出来」——**唯一一个把四档分成两组的地方**，所以它写成
# 一张集合而不是散在三处 `if` 里：`commit_batch` 建不建会话、写不写外部结果的指针、
# 目标行指哪一场，三处读的都是它。
CONFLICT_RESOLUTIONS_WITH_EXTERNAL_SESSION = {
    CONFLICT_RESOLUTION_USE_EXTERNAL,
    CONFLICT_RESOLUTION_KEEP_BOTH,
}

# `assessment_external_result.verification_status` 的取值。
#
# | 码 | 谁写 | 说的是 |
# |---|---|---|
# | `PENDING`  | DDL 默认值；**没有冲突的行停在这里**；`KEEP_ONLINE` 之后也停在这里 | 还没核，以后还能再裁 |
# | `ACCEPTED` | `USE_EXTERNAL` / `KEEP_BOTH` | 这份外部结果被承认进来了 |
# | `REJECTED` | `REJECT_EXTERNAL` | 明确否掉（终态） |
#
# **只有四档处置能动这一列，导入这件事本身不构成表态。** 每一行在建外部结果时都是
# `PENDING`（`_upsert_external_result`），没有来源冲突的行**停在** `PENDING`：
# 「学校上传了一份文件」说的是数据到了，不是「这一份平台算出来的分我认」——而后者
# 才是这一列要回答的。若把上传当成默认的接受，`ACCEPTED` 就成了恒真的值，这一列
# 也就不再有任何判别力；而汇总档（`EXTERNAL_SUMMARY`）恰恰是**完全没有本地评分**
# 的那一档，它唯一的结果事实就是这一行——默认 `ACCEPTED` 会让一份没人看过的平台分数
# 在库里顶着「已承认」的标签（缺口 10 那三处要等阶段 8 接上有效结果那一层，在那之前
# 这一列必须保持诚实）。
#
# **「采纳」不等于「有效」**：`KEEP_BOTH` 时它也是 `ACCEPTED`，而那一场外部会话
# `is_effective=0`——「这份数据我们认」与「这份数据算作当前结果」是两个问题，
# 前者归这一列，后者归 `is_effective`。
VERIFICATION_PENDING = "PENDING"
VERIFICATION_ACCEPTED = "ACCEPTED"
VERIFICATION_REJECTED = "REJECTED"

# 四档处置各自把 `verification_status` 推到哪里（上面那张表最后一列的**可执行形式**）。
#
# 写成一张表而不是散在 `commit_batch` 的三个分支里：四档里有两档推的是同一个值
# （`USE_EXTERNAL` 与 `KEEP_BOTH` 都是 `ACCEPTED`），而它们分住在两个 `elif` 里——
# 各自写一行的话，漏掉其中一行不会有任何东西报错：`ACCEPTED` 与 `PENDING` 在
# 「哪一场有效」上完全一样，只有这一列不同，所以那一处漏写只表现为
# 「学校认过的外部结果看起来还没核」，而界面上没有任何东西看得见它。
# 表放在这里（而不是 `commit_batch` 里）是因为它只能在这三个常量之后定义。
CONFLICT_RESOLUTION_VERIFICATION = {
    CONFLICT_RESOLUTION_KEEP_ONLINE: VERIFICATION_PENDING,
    CONFLICT_RESOLUTION_USE_EXTERNAL: VERIFICATION_ACCEPTED,
    CONFLICT_RESOLUTION_REJECT_EXTERNAL: VERIFICATION_REJECTED,
    CONFLICT_RESOLUTION_KEEP_BOTH: VERIFICATION_ACCEPTED,
}

# `assessment_import_row.out_of_scope_reason` 的两个取值（§18.7）。
#
# **`match_status` 只有一个 `OUT_OF_SCOPE`，而这一列分得开两种「任务外」**——这正是
# 对齐阶段留这一列的理由（DDL 那一段注释：「与 `conflict_code` 分开——前者是『我们不收』，
# 后者是『要人拍板』」）。两者的**出路完全不同**，所以它们不能合并：
#
#   SUPPLEMENT_CANDIDATE  他属于这场测评的**原定范围**，只是当时不在目标名单里
#                         （任务建好之后才转进来 / 名册刚补上）→ 心理老师确认后**补发目标**
#   NOT_IN_TASK_SCOPE     他本来就不归这场测评管（别的年级、别的班、别的学校）
#                         → 什么都不做，这一行就是不该进来
#
# 两者都**不进完成率**（`task_completion` 的主表是 `assessment_target`，它们没有目标行，
# 结构上就进不去），也都不生成任何测评结果或风险事件（§18.7）。
#
# **判据见 `_task_scope_covers`**：有范围记录时按记录判；没有记录时退到「同一所学校」，
# 而那正是「不知道当时打算发给谁」这句话能给出的唯一诚实答案——补一句「他属于原定范围」
# 是把一个疑问说成一句裁决（与 §22 那条「不替历史编一个『当时选的是全校』」同一个道理）。
OUT_OF_SCOPE_SUPPLEMENT_CANDIDATE = "SUPPLEMENT_CANDIDATE"
OUT_OF_SCOPE_NOT_IN_SCOPE = "NOT_IN_TASK_SCOPE"

# 批次号前缀。**与任务的 `IMPORT-` 前缀分开**：两者都是这一条链路的产物，
# 共用前缀时审计页的 `resource_id` 说得出「动的是哪一个」——而它正是事后唯一
# 能回答「这次导入动了什么」的字段（缺口 7）。
BATCH_NO_PREFIX = "BATCH"


# 姓名里不该出现、但外部平台经常塞进来的字符。**不是**「所有控制字符」：
# `\n` / `\r` 出现在姓名列里说明这一行被拆坏了（`csv` 已经把跨行字段合过，
# 能到这里的是文件本身有问题），那是 `INVALID_ROW`，不该被静默折叠成一个名字。
#
# **一律用 `\u` 转义写，不写字面量**：这些字符写在源码里是看不见的，而它们正是这个
# 函数要处理的东西——一份「看起来完全正常」的源码里藏着六个零宽字符，正是 §18 那个
# BOM 事故的形状（`install.ps1` 开头堆了四个 BOM，而括号差分与两条守卫都看不见）。
# 转义写法在编辑器里、在 `diff` 里、在 `grep` 里都是可见的。
_INVISIBLE_CHARS = frozenset(
    (
        "\u200b",  # ZERO WIDTH SPACE
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\ufeff",  # ZERO WIDTH NO-BREAK SPACE（BOM 泄漏到第二行以后）
        "\u2060",  # WORD JOINER
        "\u00ad",  # SOFT HYPHEN
    )
)


def normalize_person_name(value: str | None) -> str:
    """姓名标准化：去掉首尾空白**与所有不可见字符**，再把内部空白折叠掉。

    §18.4 第 2 步的原话是「去除首尾空格和不可见字符」。不可见字符不是学究：
    外部平台导出的 CSV 里，姓名列常常带着 `\\u200b`（零宽空格）、`\\ufeff`（BOM 泄漏到
    第二行以后）、`\\xa0`（不换行空格，Word 复制粘贴的产物）。这几个字符**看得见地**
    什么都看不出来，而它们会让 `Student.name == name` 精确匹配全部落空——
    症状是「这个人明明在名册上，导入却说找不到他」，且逐行报错里那一格显示的名字
    与名册上一模一样（因为前端渲染时它们本来就不显示）。

    只去首尾是不够的：`张\\u200b三` 的不见字符在中间。所以用一张「不可见字符」表
    逐个删掉，再 `strip()`。
    """
    if not value:
        return ""
    cleaned = "".join(char for char in value if char not in _INVISIBLE_CHARS)
    # 全角空格（`\\u3000`）与制表符在姓名内部是分隔符而不是内容：`张 三` 与 `张三`
    # 是同一个人的两种写法（名册上存的是后者）。折叠成空串而不是单个空格——
    # 中文姓名里没有用空格分词的写法。
    for separator in (" ", "\t", "\u3000"):
        cleaned = cleaned.replace(separator, "")
    return cleaned


def resolution_label(resolution: str | None) -> str:
    """处置方式的中文，写进审计的 `detail`。

    记中文而不是 `overwrite`：审计详情是**给人读的**轨迹（§8 记的是「实名 / 姓名遮蔽」
    而不是 `mask_names=True`，同一条道理）。`None` 是「文件里没有冲突，所以没问过」，
    与「问了、选了放弃」必须长得不一样——否则一行 `resolution=none` 会让人以为
    那次导入把冲突当成了默认放行。
    """
    return {
        RESOLUTION_OVERWRITE: "覆盖上次",
        RESOLUTION_SKIP: "放弃冲突行",
    }.get(resolution or "", "未涉及（无冲突）")

MONTH_CONFLICT_MESSAGE = "该学生在 {month} 已有一次外部导入的测评"


def month_bounds(tested_on: date) -> tuple[datetime, datetime]:
    """这个月（含）到下个月（不含）的区间。

    判重与批次任务都按它切：`submitted_at` 是测评日期（文件里那一格），
    所以「同一个月」问的就是「这两份文件的施测日期落在同一个自然月里吗」。
    """
    start = datetime(tested_on.year, tested_on.month, 1)
    end = datetime(tested_on.year + (1 if tested_on.month == 12 else 0), tested_on.month % 12 + 1, 1)
    return start, end

# 表头里的字段列。列名认不出来就是全局错误——一份没有「姓名」列的文件，
# 逐行报 200 次「缺少姓名」没有任何信息量。
FIELD_ALIASES = {
    "姓名": "name",
    "学生姓名": "name",
    "name": "name",
    "性别": "gender",
    "gender": "gender",
    "年龄": "age",
    "age": "age",
    "年级": "grade",
    "grade": "grade",
    "班级": "class_name",
    "班级名称": "class_name",
    "class_name": "class_name",
    "所用时间": "duration",
    "用时": "duration",
    "作答用时": "duration",
    "duration": "duration",
}

# §18.9 的**汇总文件**:没有题号列、只有平台算好的「总分」与八个维度分。
# 表头认得出这几列，就说明这一份走的是 `EXTERNAL_SUMMARY` 那条路。
SUMMARY_TOTAL_FIELD = "total_score"
SUMMARY_TOTAL_LABELS = ("总分", "总得分", "总分（标准分）", "总得分（标准分）")

# MHT 标准分的取值范围。**没有从规则版本里读**（阈值归规则版本，§6），因为这不是阈值：
# 它是「这个数在物理上可能是多少」，与那一版的判级分段无关——改分段不会让一个 120 分的
# 输入变成合法的。本地引擎算出来的分同样落在这个区间里。
SCORE_MIN = 0
SCORE_MAX = 100
TOTAL_SCORE_MAX = 90

# 八个维度的中文列名 → 维度码。**`labels.ts` 的 `DIMENSION_LABELS` 是同一批中文**，
# 而它们没法互认（后端 import 不了 `.ts`），所以这里是一份**镜像**，由
# `test_assessment_import_api.py::test_the_dimension_labels_are_a_mirror_of_the_frontend`
# 盯着；取值必须落在 `scale_import_service.DIMENSION_CODES` 里，同一条用例一起钉。
DIMENSION_BY_LABEL = {
    "学习焦虑": "LEARNING_ANXIETY",
    "对人焦虑": "INTERPERSONAL_ANXIETY",
    "孤独倾向": "LONELINESS",
    "自责倾向": "SELF_BLAME",
    "过敏倾向": "SENSITIVITY",
    "身体症状": "PHYSICAL_SYMPTOMS",
    "恐怖倾向": "PHOBIC_TENDENCY",
    "冲动倾向": "IMPULSIVE_TENDENCY",
}

# `1.你晚上要睡觉时…` 与 `1、…` 都见过；纯数字表头也认。
_QUESTION_HEADER = re.compile(r"^\s*(\d{1,3})\s*(?:[.、．)。:：]|$)")
_DURATION = re.compile(r"^\s*(\d+)\s*(?:秒|s|S)?\s*$")

# 两者共用一句话，见 `_visible_candidates`
STUDENT_NOT_FOUND = "「{class_name}」没有叫「{name}」的学生"
CLASS_NOT_FOUND = "名册中没有「{class_name}」这个班级"


# --------------------------------------------------------------------------
# 解析：文件 → 逐行的字符串单元格
# --------------------------------------------------------------------------


def parse_assessment_import(filename: str, content: bytes) -> dict[str, Any]:
    """读成「列头 + 数据行」的字符串方阵，并做列级校验。

    返回 `{"header_errors": [...], "rows": [...]}`。`header_errors` 是**列级**问题
    （缺列、题号不全），交给 `preview` 与批次字段的问题合并成 `global_errors`；
    行级问题一律留到 `preview`，因为那一层才能给出「第几行、哪一格」。

    **只收 .csv。** 学校在外部平台上导出的结果，导进本系统之前先另存为 CSV
    （平台的导出、WPS、Excel 都能存），因此这条链路上只有一条解析路径。
    扩展名不认识就整份拒掉：拿一个 `.xlsx` 去喂 `csv.reader` 会读成一行乱码，
    每一行都报「缺少姓名」——那比一句「请另存为 .csv」难懂得多。
    """
    if not (filename or "").lower().endswith(".csv"):
        raise AppError(
            "VALIDATION_ERROR",
            "只支持 .csv 文件，请先将文件另存为 .csv 后重试",
            422,
        )
    grid = _read_csv(content)
    if not grid:
        raise AppError("VALIDATION_ERROR", "文件里没有数据", 422)

    header, *data_rows = grid
    columns, header_errors = _columns_from_header(header)
    rows = [_row_from_cells(row, columns, row_no) for row_no, row in enumerate(data_rows, start=2)]
    # 全空行（Excel 常见的尾部空行）直接丢掉，否则会变成一行「缺少姓名」的错误
    rows = [row for row in rows if any(value for value in row["cells"].values())]
    # 形态按**列**判一次，提到最外层（§18.9）。每一行也带着它（`_row_from_cells`），
    # 但批次那一行要的是「这一份文件是哪一档」，由它写
    # `assessment_import_batch.import_mode`——那个问题的答案不该靠 `rows[0]` 来回答。
    return {
        "header_errors": header_errors,
        "rows": rows,
        "form": SOURCE_TYPE_FULL_ANSWER if columns["questions"] else SOURCE_TYPE_SUMMARY,
    }


def _read_csv(content: bytes) -> list[list[str]]:
    """按 UTF-8（带不带 BOM 都认）读；读不了再按 GBK 读一次。

    中文 Excel / WPS 的「另存为 CSV」在 Windows 上写出来的是 GBK，而那是学校最可能的
    操作路径。只按 UTF-8 解会抛 UnicodeDecodeError —— 一个 500，而它该是「照读不误」
    或者一句「请另存为 UTF-8 的 CSV」。两次都解不出来才报错（真的二进制文件）。

    判据本身归 `services/import_text.py`：名册导入与题库导入是同一个洞的第二、三处，
    2026-09-20 起三条链共用那一处定义，这里只留下「CSV 字节 → 二维数组」这一步。
    """
    return read_csv_grid(content)


def _columns_from_header(header: list[str]) -> tuple[dict[str, Any], list[str]]:
    columns: dict[str, Any] = {"questions": {}, "dimensions": {}}
    errors: list[str] = []
    duplicated: list[int] = []
    for index, raw in enumerate(header):
        label = (raw or "").strip()
        if not label:
            continue
        field = FIELD_ALIASES.get(label.lower() if label.isascii() else label)
        if field and field not in columns:
            columns[field] = index
            continue
        # 汇总列（§18.9）。放在题号之前判：维度那一列的名字是「学习焦虑」这种中文，
        # 认不出题号，但总要先问过它是不是那九个汇总列之一
        if label in SUMMARY_TOTAL_LABELS:
            columns.setdefault(SUMMARY_TOTAL_FIELD, index)
            continue
        dimension = DIMENSION_BY_LABEL.get(label)
        if dimension and dimension not in columns["dimensions"]:
            columns["dimensions"][dimension] = index
            continue
        match = _QUESTION_HEADER.match(label)
        if match:
            question_no = int(match.group(1))
            if not 1 <= question_no <= QUESTION_COUNT:
                continue
            if question_no in columns["questions"]:
                duplicated.append(question_no)
                continue
            columns["questions"][question_no] = index

    missing_fields = [
        label
        for label, field in (("姓名", "name"), ("性别", "gender"), ("年龄", "age"), ("年级", "grade"), ("班级", "class_name"))
        if field not in columns
    ]
    if missing_fields:
        errors.append(f"缺少列：{'、'.join(missing_fields)}")
    if not columns["questions"]:
        # §18.9：**两种文件都收**。有 100 个题号列的走本地引擎；只有总分与维度分的
        # 那一份标 `EXTERNAL_SUMMARY`，不伪造 100 条答卷。两条路都不满足时才是错误
        # ——只说「没有题号列」在这里是错的：那份汇总文件本来就没有题号列。
        #
        # 两种列**都在**时按完整答案走（本地有原始答案，就没有理由去信平台算的分；
        # 这也让一份两边都带的文件不会因为多了一列而变成另一条路）。
        if SUMMARY_TOTAL_FIELD not in columns:
            errors.append(
                "表头里既没有认出任何题号列（题号列应写成 1.题干），也没有「总分」列。"
                "包含 100 道原始答案的文件与只包含汇总分数的文件都能导入，"
                "但至少要认得出其中一种"
            )
    else:
        missing_questions = [
            number for number in range(1, QUESTION_COUNT + 1) if number not in columns["questions"]
        ]
        if missing_questions:
            errors.append(f"缺少题号列：{'、'.join(str(number) for number in missing_questions[:10])}")
        if duplicated:
            errors.append(f"重复的题号列：{'、'.join(str(number) for number in duplicated[:10])}")
    return columns, errors


def _row_from_cells(cells: list[str], columns: dict[str, Any], row_no: int) -> dict[str, Any]:
    def cell(index: int | None) -> str:
        if index is None or index >= len(cells):
            return ""
        return (cells[index] or "").strip()

    return {
        "row_no": row_no,
        # 用来判断「这一行是不是空的」：锯齿状的行、以及只填了后半截的行都算有内容
        "cells": {index: cell(index) for index in range(len(cells))},
        "name": cell(columns.get("name")),
        "gender": cell(columns.get("gender")),
        "age": cell(columns.get("age")),
        "grade": cell(columns.get("grade")),
        "class_name": cell(columns.get("class_name")),
        "duration": cell(columns.get("duration")),
        # 题号 1..100 各自对应的列。表头里没有这个题号（列级校验已经报了）时是空串，
        # 会在行级变成「第 N 题未作答」——比让整份文件因为一个表头写法而不可用要好。
        "answers": [cell(columns["questions"].get(number)) for number in range(1, QUESTION_COUNT + 1)],
        # 这一份文件属于哪一档（§18.9）。**按列判、不按行判**：同一份文件里不会一半
        # 行有答案、一半行只有总分，而逐行去猜会让一行的空答案格子变成「这份文件是汇总的」
        # ——那是把一个格子的问题说成整份文件的形状。
        "form": SOURCE_TYPE_FULL_ANSWER if columns["questions"] else SOURCE_TYPE_SUMMARY,
        # 汇总那一档的两样（有答案时它们一般是空的，`_match_row` 只认 `form`）
        "total_score": cell(columns.get(SUMMARY_TOTAL_FIELD)),
        "dimension_scores": {
            code: cell(index) for code, index in columns["dimensions"].items()
        },
    }


# --------------------------------------------------------------------------
# 预览：逐行校验 + 定位学生
# --------------------------------------------------------------------------


def start_assessment_import(
    db: Session,
    parsed: dict[str, Any],
    *,
    file_name: str,
    file_sha256: str,
    file_size: int | None,
    batch_name: str,
    tested_on: date,
    actor: UserAccount,
    task_id: int | None = None,
    source_system: str | None = None,
) -> AssessmentImportBatch:
    """建一批、逐行匹配、把每一行落进 `assessment_import_row`。**不写任何测评记录。**

    这是 V1.2 对 V1.0 那条链路的实质改动。V1.0 的「预览」只算一遍给人看，把结果
    签进一个 JWT 令牌，提交时凭令牌写库——**库对这批数据一无所知**。于是两件事没有
    答案：这一批里 W 行当时为什么匹配不上（令牌是一次性的，没人再打开它），
    以及补完名册之后能不能重来一遍（不能，令牌是死的，重新上传要么改文件、要么
    再跑一遍并且看不出差别）。

    现在预览就是**写**：批次与逐行明细都落库，`match_status` 是这一行此刻的结论。
    §18.4 那条出路——「找不到学生时应先补充名册，再重新匹配」——因此才成立：
    补完名册之后**重传同一个文件**，`_reusable_batch` 复用这个批次（同操作者、同指纹、
    仍 `PREVIEW`），逐行结论整批重算。

    **没有一条「只重跑匹配、不重传文件」的路径，也不该有。** 匹配的输入不止是名册：
    答案（100 题）与用时只在文件里，`assessment_external_result` 只给**匹配成功的那些
    行**存了它们（见 `_match_row`），而匹配结论本身要由答案之外的那五列决定。所以
    「重跑匹配」必然缺答案，而缺答案的行一旦提交就是一份空答卷。重传文件是这条路上
    唯一能把两样东西一起带回来的动作。

    提交仍然只在这一步之后发生（`commit_batch`），所以「预览」这两个字在界面上
    依然是真的：这一步不产生任何测评会话、答卷、结果或风险事件。

    `task_id` 给了就把这一批绑到那场任务上（§18.4 的 `OUT_OF_SCOPE` 与任务内判重
    都以它为准）；不给就是「历史外部结果」那条路，判重退回自然月（缺口 8）。
    两种都留着是用户 2026-09-19 的裁决，理由写在 `_existing_session_for_match` 里。
    """
    global_errors = list(parsed["header_errors"]) + _batch_errors(batch_name, tested_on)
    if not _published_scale(db):
        global_errors.append("没有已发布的 MHT 量表版本，无法评分")
    # 全局错误**在写任何一行之前**抛：半个批次比没有批次更难收拾——操作员会看到
    # 一批匹配好的行挂在「导入失败」的那一次尝试上，而再传一次同一个文件又会
    # 复用这个批次（同指纹、同操作者、仍是 PREVIEW），于是他上次改的那几行没了
    # 却看不出为什么。
    if global_errors:
        raise AppError("VALIDATION_ERROR", "；".join(global_errors), 422)

    school = school_for_import(db)
    if school is None:
        raise AppError("VALIDATION_ERROR", "系统里还没有学校记录，无法导入", 422)

    task = _resolve_import_task(db, task_id, actor=actor, school=school)
    tested_at = datetime(tested_on.year, tested_on.month, tested_on.day)

    batch = _reusable_batch(db, actor=actor, file_sha256=file_sha256, task=task)
    if batch is None:
        batch = AssessmentImportBatch(
            batch_no=_next_batch_no(db, tested_on),
            school_id=school.id,
            file_name=file_name,
            batch_name=batch_name,
            file_sha256=file_sha256,
            source_system=source_system or DEFAULT_SOURCE_SYSTEM,
            tested_at=tested_at,
            file_size_bytes=file_size,
            status=BATCH_STATUS_PREVIEW,
            imported_by=actor.id,
            task_id=task.id if task else None,
            # 这一列的第一个写入方（§18.9）。在它之前每一批都顶着 `server_default`
            # 的 `EXTERNAL_FULL_ANSWER`——一份只有总分与维度分的文件也写着「全答案」，
            # 而后来的读者（有效结果判定、来源那一列）要照它决定怎么读这一批。
            import_mode=parsed["form"],
        )
        db.add(batch)
        db.flush()
    else:
        # 复用：同一个人、同一个文件、上一批还没提交。这是「改完名册再传一次同一个
        # 文件」那条路——**行要重建**（匹配结论可能全变了），但批次本身复用，
        # 于是界面上的历史里不会多出一条一模一样的记录。
        _clear_batch_rows(db, batch)
        batch.file_name = file_name
        # 复用同一个批次时**名字跟着新的一次走**：操作员重传文件时改了批次名称
        # （「九月普查」→「九月普查（重导）」），屏幕上那一次的意思就该覆盖上一次的。
        # 只认文件指纹而不认名字的话，他改的那一格会静默失效。
        batch.batch_name = batch_name
        batch.file_size_bytes = file_size
        batch.tested_at = tested_at
        batch.task_id = task.id if task else None
        batch.status = BATCH_STATUS_PREVIEW
        # 形态跟着这一次的文件走。**这一行在今天是恒等的**——复用的前提就是指纹相同，
        # 而指纹相同意味着形态相同。写它是因为「复用分支里每一样跟着新一次走的东西都
        # 写在同一处」：哪一天复用的判据放宽（比如允许「同一份文件换个批次名」之外的
        # 形状），这一行就是那个新判据唯一的落点，而它不在时没有任何东西会红。
        batch.import_mode = parsed["form"]
        batch.resolution = "NONE"
        batch.duplicate_of_batch_id = None
        if source_system:
            batch.source_system = source_system

    _match_batch_rows(
        db,
        batch,
        parsed=parsed,
        school=school,
        tested_on=tested_on,
        actor=actor,
        task=task,
    )
    db.flush()
    return batch


def start_historical_import(
    db: Session,
    rows: list[dict[str, Any]],
    *,
    school: School,
    actor: UserAccount,
    batch_name: str,
    file_name: str,
    file_sha256: str,
    tested_on: date,
    source_system: str | None = None,
) -> AssessmentImportBatch:
    """建一批**每一行都已经知道是谁**的导入，同样**不产出任何测评记录**。

    `start_assessment_import` 的兄弟，差别只有一处：那一份要**猜**每一行是谁
    （§18.4 的六步匹配），而这里不用猜——调用方给的行就带着 `student_id`。
    其余逐字相同：同一张批次表、同一张行表、同一份外部结果、同一个 `commit_batch`。
    所以走这条路的记录仍然是**真的导入记录**（`source=IMPORTED`、批次任务、
    目标行、同一处触发规则），只是跳过了「校外文件上这一行说的是哪个人」那一步。

    存在的理由是演示数据（`seed_demo`）：那批「上学期普查」的名单本来就是**从名册上
    取的**，它不存在匹配问题。**为什么不让它走文件行**——演示名册的班级叫 `1班`，
    而外部文件的班级列按学校自己的编号规则要写 `701`（`_parse_class`），
    `match_student` 拿 `701` 去 `ClassGroup.name` 里找必然落空，于是整批都会是
    「名册上没有这个班级」。改演示班级名要动 `seed.py` 的基线、`purge` 的判据和
    五个测试文件，代价远大于它换来的东西（真实学校的名册里班级就是 `701`——
    学生信息导入建的；不一致的是演示数据，不是匹配规则）。

    **这不是一条绕过匹配的入口**：它不接受任何外部文件，不新建学生（`Student`
    的 id 必须已经在名册上，取不到就当场报错），也不做任何「猜」——所以
    §18.4 那条「不得自动新建学生档案」在这里是构造上成立的，不是靠一句断言。

    每一行是 `{"student_id": int, "answers": [str, ...100], "duration_seconds": int | None}`。
    """
    global_errors = list(_batch_errors(batch_name, tested_on))
    if not _published_scale(db):
        global_errors.append("没有已发布的 MHT 量表版本，无法评分")
    if not rows:
        global_errors.append("这一批里没有可导入的记录")
    if global_errors:
        raise AppError("VALIDATION_ERROR", "；".join(global_errors), 422)

    tested_at = datetime(tested_on.year, tested_on.month, tested_on.day)
    batch = AssessmentImportBatch(
        batch_no=_next_batch_no(db, tested_on),
        school_id=school.id,
        file_name=file_name,
        batch_name=batch_name,
        file_sha256=file_sha256,
        source_system=source_system or DEFAULT_SOURCE_SYSTEM,
        tested_at=tested_at,
        status=BATCH_STATUS_PREVIEW,
        imported_by=actor.id,
        # **刻意不绑任务**（`task_id` 留空）：这一批是历史外部结果，判重因此走
        # `existing_import_session` 那条自然月的口径（缺口 8），提交时才由
        # `commit_batch` 按月份找/建批次任务。这正是用户 2026-09-19 那条
        # 「两层判重都留」里未绑定任务的那一层。
        task_id=None,
        # 这条路上每一行都是 100 道答案（见 docstring），所以形态只有一个取值。
        # 写出来而不是靠 `server_default`：`import_mode` 的读者要照它决定怎么读这一批，
        # 而「恰好与默认值相同」不是一条能读出来的证据。
        import_mode=SOURCE_TYPE_FULL_ANSWER,
    )
    db.add(batch)
    db.flush()

    for row_no, item in enumerate(rows, start=1):
        student = db.get(Student, item["student_id"])
        if student is None:
            raise AppError(
                "VALIDATION_ERROR", f"第 {row_no} 行的学生不在名册上", 422
            )
        grade = db.get(Grade, student.grade_id) if student.grade_id else None
        class_group = db.get(ClassGroup, student.class_id) if student.class_id else None
        row = AssessmentImportRow(
            batch_id=batch.id,
            row_no=row_no,
            student_id=student.id,
            # 原始列照名册填：这一批没有「原始文件」，而 `raw_*` 与 `normalized_*`
            # 两套值都留着的意义是让逐行明细能回答「这一行为什么匹配错了」——
            # 在这条路上它回答的是「这一行对的是名册上的谁」（§18.4）。
            raw_student_no=student.student_no,
            raw_name=student.name,
            raw_grade_name=grade.name if grade else None,
            raw_class_name=class_group.name if class_group else None,
            normalized_name=normalize_person_name(student.name) or None,
            normalized_grade_name=grade.name if grade else None,
            normalized_class_name=class_group.name if class_group else None,
            # 三列一起给：它们各有一条复合外键等着（同 `_match_row`）
            matched_school_id=student.school_id,
            matched_grade_id=student.grade_id,
            matched_class_id=student.class_id,
            match_status=MATCH_MATCHED,
            match_confidence=1,
            tested_at=batch.tested_at,
            source_timezone=batch.source_timezone,
            processing_status=ROW_PROCESSING_PENDING,
            age_before=student.age,
            age_after=None,
        )
        db.add(row)
        db.flush()
        _attach_external_result(
            db,
            batch=batch,
            row=row,
            school=school,
            task=None,
            student=student,
            payload={
                "answers": item["answers"],
                "duration_seconds": item.get("duration_seconds"),
            },
        )

    batch.total_rows = len(rows)
    batch.created_rows = len(rows)
    db.flush()
    return batch


def _resolve_import_task(
    db: Session, task_id: int | None, *, actor: UserAccount, school: School
) -> AssessmentTask | None:
    """这一批要绑的那场任务（`None` = 不绑）。

    `task_id` 是客户端传来的，所以两件事都要查：任务得是**这所学校**的（批次上有
    `assessment_import_batch_fk_task_school` 那条复合外键，不先查的话报出来的是一句
    英文的外键错误），以及操作员得**看得见它**——不查第二条的话它成了一张越权凭据
    （§9：命名了对象的接口必须校验它归不归这个人管；任务这一侧的对等判据就是
    「他是不是这场任务的读者」）。

    不存在与看不见都回 404、同一句话：两种情况下操作员要做的事是同一件（去看任务
    列表里有没有这一场），而分开报等于告诉他「这个 id 是存在的」。
    """
    if task_id is None:
        return None
    task = db.get(AssessmentTask, task_id)
    if task is None or task.school_id != school.id:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    ensure_task_reader(actor)
    return task


def _reusable_batch(
    db: Session, *, actor: UserAccount, file_sha256: str, task: AssessmentTask | None
) -> AssessmentImportBatch | None:
    """同一个操作员、同一个文件、**还没提交**的那一批（没有就是 `None`）。

    判据里三个都不能少：

    * `imported_by` —— 别人传的同一份文件不该被我的重传覆盖掉，那是两个人的两次工作；
    * `file_sha256` —— 内容一样才算「同一个文件」，改过一个字就不是；
    * `status == PREVIEW` —— **已提交的批次绝不复用**：它下面挂着真实的测评会话，
      重传同一个文件时把那些行删了重建，等于把一批已经生效的事实从记录里抹掉
      （会话还在，但「它是从哪一行来的」这条线索没了）。

    另外带上 `task_id`：同一份文件先不绑任务传一次、再绑着任务传一次，是**两次口径
    不同的导入**（判重一层按自然月、一层按任务内有效结果），复用会把第一次那批行的
    匹配结论按新口径改掉，而操作员看不出这件事发生过。
    """
    return db.scalar(
        select(AssessmentImportBatch)
        .where(
            AssessmentImportBatch.imported_by == actor.id,
            AssessmentImportBatch.file_sha256 == file_sha256,
            AssessmentImportBatch.status == BATCH_STATUS_PREVIEW,
            AssessmentImportBatch.task_id == (task.id if task else None),
        )
        .order_by(AssessmentImportBatch.id.desc())
    )


def _clear_batch_rows(db: Session, batch: AssessmentImportBatch) -> None:
    """清掉这一批已有的行与外部结果。

    删的次序由那个**环**决定：`assessment_import_row.external_result_record_id`
    指着 `assessment_external_result.id`，而后者又指着 `assessment_import_row.id`
    （DDL 里 `use_alter=True` 那一条）。两边都在时删哪一边都撞外键（全库没有
    `ondelete=`，§1），所以先把行上的指针置空，再删结果，最后删行。
    """
    db.execute(
        update(AssessmentImportRow)
        .where(AssessmentImportRow.batch_id == batch.id)
        .values(external_result_record_id=None)
    )
    db.execute(
        delete(AssessmentExternalResult).where(AssessmentExternalResult.batch_id == batch.id)
    )
    db.execute(delete(AssessmentImportRow).where(AssessmentImportRow.batch_id == batch.id))


def _match_batch_rows(
    db: Session,
    batch: AssessmentImportBatch,
    *,
    parsed: dict[str, Any],
    school: School,
    tested_on: date,
    actor: UserAccount,
    task: AssessmentTask | None,
) -> None:
    """把这份文件的每一行匹配一遍，写进 `assessment_import_row`。

    只写**两个**计数：`total_rows` 与 `error_rows`。口径与「学生信息导入」一致：
    数的是**行数**，不是错误条数——一行报三条错仍然只是「一行要改」。

    **`created_rows` / `updated_rows` / `skipped_rows` 在这里一个都不写**，这是
    第 4 期的改动。它们回答的是「这一批写进去了几条」，而这一趟一条都还没写——
    匹配完只是把结论摆在「待确认」上，真正的写入在 `commit_batch`。从前这三列在
    这里被填成 `created = 匹配成功的行数`、`updated/skipped = 0`，于是一次预览之后
    界面上写着「新建 213 行」，而库里 `assessment_session` 一条都没有；提交后再由
    `commit_batch` 覆盖成真数。同一列在两种状态下是两个意思，而两个数看起来都正常。

    预览与明细页要的「几行可导入、几行要拍板、几行有问题」由 `batch_row_counts`
    **现算**（一次 GROUP BY），那份事实于是只有一个来源。
    """
    seen_students: dict[int, int] = {}
    errored = 0
    for parsed_row in parsed["rows"]:
        row, outcome, payload = _match_row(
            db,
            parsed_row,
            batch=batch,
            school=school,
            tested_on=tested_on,
            actor=actor,
            task=task,
            seen_students=seen_students,
        )
        db.add(row)
        db.flush()
        if outcome.student is not None and outcome.status in MATCH_STATUSES_IMPORTABLE:
            seen_students[outcome.student.id] = parsed_row["row_no"]
        if outcome.status in MATCH_STATUSES_UNIMPORTABLE:
            # 点了提交也进不去的那四档（`error_rows` 就是它们）。**不为它们留外部
            # 结果那一行**：`assessment_external_result` 是「这一行的答案去哪了」，
            # 而它们没有任何一条路会被写进去——留一条谁也读不到的原始事实，只会在
            # 「这一批带进来了多少条外部记录」这种问题上下不了台。
            errored += 1
        else:
            # 剩下的两档都会写：`MATCHED` 直接写，「要拍板」的选了覆盖才写。
            # 两档都要先有那条外部结果——提交时 `_payload_of` 从**行上**那一列读
            # 答案与用时（见 `_attach_external_result`）。
            _attach_external_result(
                db,
                batch=batch,
                row=row,
                school=school,
                task=task,
                student=outcome.student,
                payload=payload,
            )

    batch.total_rows = len(parsed["rows"])
    batch.error_rows = errored
    db.flush()


def batch_row_counts(db: Session, batch: AssessmentImportBatch) -> dict[str, int]:
    """这一批的行**按匹配结论**分档，现算。

    四个数回答四个不同的问题，界面上一行一个字写着：

    | 数 | 含义 | 操作员要做什么 |
    |---|---|---|
    | `ready` | 匹配上了、提交就能进 | 什么都不用做 |
    | `needing_resolution` | 要拍板（年龄不符 / 重复 / 与在线答卷冲突） | 见下：**两类，两类动作** |
    | `conflict` | 其中「与在线答卷冲突」的那几条 | 逐行选 §18.8 的四种处置之一 |
    | `error` | 进不去（行坏了 / 名册上没有 / 不在本任务里 / 同名分不开） | 去补名册或补发目标 |

    `error` 与前面几个的边界不能混：那几档**选什么都不会写**，而这三个数里的行
    都是「有人拍板就写得进去」的。把一档从这里挪到 `error`（或反过来），屏幕上那两句
    话就会各自承诺一件做不到的事——第 4 期把 `AMBIGUOUS` 挪进 `error`，正是因为它的
    处置不是「覆盖 / 放弃」。

    **`conflict` 是 `needing_resolution` 的一个子集，不是并列的第五档**（第 6 期加），
    而它单独数出来是因为**这两类的动作不一样**：`MATCH_STATUSES_NEEDING_RESOLUTION`
    里的其余两档（`AGE_CONFLICT` / `DUPLICATE`）由整批那一次「覆盖 / 放弃」回答，
    而 `CONFLICT` 那几条**整批的选择对它们无效**（用户裁决 2026-09-19），必须逐行选
    四种处置之一。所以界面上那个单选项管得着的是 `needing_resolution - conflict`，
    而「待确认」这个**标题**数的是 `needing_resolution`（那几条确实还需要他做点事）。
    少了这个差额，一批待确认**只有**冲突行的批次会逼着操作员在两个按屏幕上的话
    「管不着这些行」的选项里挑一个，才肯把提交按钮点亮——而那个选择对结果毫无影响。

    **与 `commit_batch` 的判据必须同源**：它拒绝提交时数的是
    `MATCH_STATUSES_NEEDING_RESOLUTION`，这里数的是同一个集合；否则会出现「屏幕上
    说 0 条待确认、点提交却说有 3 条待确认」这种自相矛盾的对话。冲突行那一道门
    （`_conflict_row_is_decided`）数的也必须是**整批**，与这里的 `conflict` 同源。

    **不读批次上那几列计数**，因为它们说的是别的时态（见 `_match_batch_rows` 的
    docstring）。这一页问的是「现在这一批的行是什么状态」，而行的状态刚刚可能被改过
    ——操作员在页面上逐行处置之后，那几个数必须跟着动。

    **数的是整批，不套读者的数据范围**——与 `list_import_rows` 刻意不同。理由是
    这四个数坐在**提交按钮旁边**：`commit_batch` 拒绝提交时用的是同一个集合、整批地数
    （「有 3 条记录需要确认」），所以逐行列表可以因为范围而少几行，那个数字不可以。
    套上范围的话会出现最坏的那种对话——屏幕上写着「待确认 0 条」，点下去回一句
    「有 3 条记录需要确认」，而操作员照着屏幕找不出那 3 条在哪（§11：指标卡上的数
    必须与它点进去的那个列表同源）。

    代价是「共 N 行」与列表里的行数可能不等，所以界面把两个数分开写、各自标明口径
    （§9：范围数字要写明口径）——批次那一行说「本批 N 行」，列表下面说「你可见 M 行」。

    **四个键同时是逐行明细页那几片筛选项的取值**（`MATCH_GROUPS` 那张表）：这一页的
    筛选片读 `match_group`，而 `list_import_rows` 按同一张表筛。片上写着「待确认 3 条」
    而点进去 0 条这种对话因此构造上不可能出现（§11）。
    """
    counts = dict(db.execute(
        select(AssessmentImportRow.match_status, func.count())
        .where(AssessmentImportRow.batch_id == batch.id)
        .group_by(AssessmentImportRow.match_status)
    ).all())
    # 四个数**从 `MATCH_GROUPS` 推导**，不在这里各写一遍判据：明细页的筛选片读的是
    # 同一张表，所以「片上的数」与「筛出来的行」构造上不可能对不上（§11）。
    #
    # `NOT_FOUND` / `OUT_OF_SCOPE` 在 `error` 那一组里，所以「进不去」与
    # `batch.error_rows` 是同一个集合——预览时写下的那个数与这一页此刻现算的数用的是
    # 同一份名单。
    return {
        group: sum(counts.get(status, 0) for status in statuses)
        for group, statuses in MATCH_GROUPS.items()
    }


def row_durations(db: Session, rows: list[AssessmentImportRow]) -> dict[int, int | None]:
    """这一批每一行的**用时**，从外部结果那条记录里取。

    为什么用时不在行表上：`assessment_import_row` 是**身份与匹配**的记录，答案与用时
    这两样只在文件里、且只在提交那一刻用得上，它们落在
    `assessment_external_result.result_payload_json` 里（见 `_attach_external_result`
    的最小化策略）。界面上「用时」那一列因此要绕一跳。

    一次查一批（200 行逐行 `db.get` 就是 200 次 SELECT）。返回的字典里
    **没有条目就是没有记录**，调用方发 `null`——§3 数值列那条约定的原话：「没有记录
    就留空，`—` 会让整列被表格软件当成文本」。写成 `0` 更糟：0 秒是一次真实存在的
    用时（一闪而过的作答），它会把「这份文件没写用时」说成「这个学生用了 0 秒」。

    匹配不上学生的行本来就没有外部结果记录（`_match_row` 只给进得去的那两档建），
    所以这些行天然取不到——那是对的：一行连人都没匹配上时，他的「用时」没有任何
    意思。
    """
    record_ids = {row.external_result_record_id for row in rows if row.external_result_record_id}
    if not record_ids:
        return {}
    payloads = dict(
        db.execute(
            select(AssessmentExternalResult.id, AssessmentExternalResult.result_payload_json)
            .where(AssessmentExternalResult.id.in_(record_ids))
        ).all()
    )
    return {
        row.id: (payloads.get(row.external_result_record_id) or {}).get("duration_seconds")
        for row in rows
        if row.external_result_record_id
    }


def _match_row(
    db: Session,
    parsed_row: dict[str, Any],
    *,
    batch: AssessmentImportBatch,
    school: School,
    tested_on: date,
    actor: UserAccount,
    task: AssessmentTask | None,
    seen_students: dict[int, int],
) -> tuple[AssessmentImportRow, MatchOutcome, dict[str, Any]]:
    """一行：先解析文件里的值，再按 §18.4 匹配，最后把两者一起写成一行。

    **解析与匹配分开**是有意的：解析的输错（性别写了 `3`、年级写了「七年级」）是
    「文件要改」，匹配的输错（名册上没有这个人）是「名册要补」，而这两件事的出路
    完全不同。合成一步时它们会共用一句 `INVALID_ROW`，操作员分不清该改哪一边。

    第三个返回值是**只有文件里才有、行表存不下的那两样**（100 题答案与用时）：
    `assessment_import_row` 是身份与匹配的记录，没有答案列，而答案在提交那一刻
    必须还在（那时文件早就不在了）。它由调用方转手交给 `assessment_external_result
    .result_payload_json`（见那个函数的 docstring：只存最小化的这两样）。
    """
    errors: list[str] = []
    warnings: list[str] = []

    name = parsed_row["name"]
    if not name:
        errors.append("缺少姓名")
    gender = None
    if not parsed_row["gender"]:
        errors.append("缺少性别")
    else:
        gender = GENDER_BY_NUMBER.get(parsed_row["gender"])
        if gender is None:
            errors.append(GENDER_INPUT_HINT)
    age = _parse_age(parsed_row["age"], warnings)

    grade_name = _parse_grade(parsed_row["grade"], errors)
    class_name = _parse_class(parsed_row["class_name"], grade_name, errors)
    duration, duration_warning = _parse_duration(parsed_row["duration"])
    if duration_warning:
        warnings.append(duration_warning)

    answers: list[str] = []
    total_score: int | None = None
    dimension_scores: dict[str, int] = {}
    if parsed_row["form"] == SOURCE_TYPE_SUMMARY:
        # §18.9 的汇总文件：**没有 100 条答卷可读，也不许伪造**。分数就是这一行的全部。
        # 这一支下 `answers` 保持空——它在库里会变成 0 条 `assessment_answer`，
        # 而那是实话：我们确实没有这个学生的作答记录，只有一份别人算好的分。
        total_score, score_errors = _parse_total_score(parsed_row["total_score"])
        errors.extend(score_errors)
        dimension_scores, dimension_errors = _parse_dimension_scores(parsed_row["dimension_scores"])
        # 维度分的毛病是**提示**，与总分相反：它不决定这一行进不进得去（见那个函数）
        warnings.extend(dimension_errors)
    elif len(parsed_row["answers"]) == QUESTION_COUNT:
        answers, answer_errors = _parse_answers(parsed_row["answers"])
        errors.extend(answer_errors)
    else:  # pragma: no cover - 列级校验已经拦住题号不全的文件
        errors.append("题号列不完整，无法读取答案")

    if errors:
        outcome = MatchOutcome(
            MATCH_INVALID_ROW,
            errors=errors,
            warnings=warnings,
            message="这一行本身有问题，需要改文件后重传",
        )
    else:
        outcome = match_student(
            db,
            school=school,
            grade_name=grade_name,
            class_name=class_name,
            name=name,
            gender=gender,
            age=age,
            tested_on=tested_on,
            actor=actor,
            task=task,
        )
        outcome.warnings = warnings + outcome.warnings

    row = AssessmentImportRow(
        batch_id=batch.id,
        row_no=parsed_row["row_no"],
        student_id=outcome.student.id if outcome.student else None,
        raw_student_no=None,
        raw_name=name or None,
        raw_grade_name=parsed_row["grade"] or None,
        raw_class_name=parsed_row["class_name"] or None,
        raw_age=age,
        normalized_name=normalize_person_name(name) or None,
        normalized_grade_name=grade_name,
        normalized_class_name=class_name,
        matched_school_id=outcome.student.school_id if outcome.student else None,
        matched_grade_id=outcome.grade.id if outcome.grade else None,
        matched_class_id=outcome.class_group.id if outcome.class_group else None,
        match_status=outcome.status,
        match_confidence=outcome.confidence,
        candidate_student_ids=outcome.candidates or None,
        tested_at=batch.tested_at,
        source_timezone=batch.source_timezone,
        processing_status=ROW_PROCESSING_PENDING,
        conflict_code=_conflict_code_for(outcome),
        # 由 `match_student` 决定（只有 `OUT_OF_SCOPE` 那一档有值，见 `MatchOutcome`）
        out_of_scope_reason=outcome.out_of_scope_reason,
        age_before=outcome.student.age if outcome.student else None,
        age_after=age,
        message=_row_message(outcome),
    )
    # 同一份文件里两行是同一名学生 → **错误，不是冲突**（与「学生信息导入」里
    # 「文件内重复学号」同一条判据）：先写的那行等着被后一行覆盖，还是反过来？
    # 这问的不是口径，是文件坏了。所以它是 `INVALID_ROW`，而不是让人去选一个
    # 处置方式。
    if (
        outcome.student is not None
        and outcome.status in MATCH_STATUSES_IMPORTABLE
        and outcome.student.id in seen_students
    ):
        row.match_status = MATCH_INVALID_ROW
        row.message = f"与第 {seen_students[outcome.student.id]} 行是同一名学生"
    elif outcome.student is not None and gender and outcome.student.gender:
        if outcome.student.gender != gender:
            row.message = _join_message(
                row.message,
                f"性别与名册不符（名册：{'男' if outcome.student.gender == 'MALE' else '女'}）",
            )
    return row, outcome, {
        "answers": answers,
        "duration_seconds": duration,
        # 形式与汇总分一起回给调用方（`_attach_external_result` 按 `form` 决定往
        # `assessment_external_result` 写哪一组列）。`form` 是**整份文件**的属性，
        # 每一行都带着它——逐行去猜会让一行的空答案格子被读成「这份文件是汇总的」。
        "form": parsed_row["form"],
        "total_score": total_score,
        "dimension_scores": dimension_scores,
    }


def _conflict_code_for(outcome: MatchOutcome) -> str | None:
    """`MatchOutcome` → `conflict_code`。只给「要拍板」的那三档写码。

    两者不是一一对应，也不是重复：`match_status` 是**结论**（这一行现在算什么），
    `conflict_code` 是**原因**（哪一类冲突）。`AMBIGUOUS` 没有码——它不是冲突，
    是「还没定下来是谁」，出路是选一个人，不是选一个处置方式。

    收的是整个 outcome 而不是 `status`，因为 `CONFLICT` 那一档里面还分两半（正在
    作答 / 已经交卷），而 `status` 回答不了是哪一半——`match_student` 早就把答案写在
    `outcome.conflict_code` 上了，所以这里直接用它、不再按 `status` 现推一遍。
    `CONFLICT` 那一档**必须**带着码进来，所以这里直接返回它、不给兜底值：走到这里
    还是空的话，说明有人绕过了 `match_student` 造了一个 outcome（唯一会这么做的只有
    测试夹具），那时宁可留一个说不出原因的行，也不要编一个 `IN_SYSTEM_RESULT` 上去
    ——那会让一条「他还没交卷」的记录显示成「他已交卷」。
    """
    if outcome.status == MATCH_AGE_CONFLICT:
        return CONFLICT_AGE_MISMATCH
    if outcome.status == MATCH_DUPLICATE:
        return CONFLICT_DUPLICATE
    if outcome.status == MATCH_CONFLICT:
        return outcome.conflict_code
    return None


def _row_message(outcome: MatchOutcome) -> str | None:
    """一行显示给人看的那句话。

    口径与全站一致：**认不出来的东西要看得见**，认不出的码原样回退而不是留白
    （§3 `levelLabel` 那条既成约定）。所以 `errors` 里那几条也拼进去——它是
    「文件要改哪里」的唯一线索，而这一列是操作员唯一会读的那一格。

    **`message` 只在它说的是 `errors` 里没有的一件事时才写。** 这一句拼在
    `errors` **后面**，所以一句「上一级说法」跟在一句具体的话后面时，具体的那一句
    读起来像概括的那一句的一个特例——而操作员照着概括的那一句去查，查不出任何东西
    （「名册上没有这个班级」是对的，可他要的是**哪个**班）。`NOT_FOUND` 那三处
    （学校 / 班级 / 学生）的上一级说法因此全部删掉了；留下来的两种都不是重述：

    | 谁 | `message` 补的是 |
    |---|---|
    | 行级解析错误（`INVALID_ROW`） | **出路**：「需要改文件后重传」 |
    | `AGE_CONFLICT` / `DUPLICATE` / `CONFLICT` / `OUT_OF_SCOPE` / `AMBIGUOUS` | **整个说法**（它们本来就没有 `errors`） |

    加新分支时先问一句：这句话在 `errors` 里已经说过了吗？说过就不要写。
    """
    parts: list[str] = []
    if outcome.errors:
        parts.extend(outcome.errors)
    if outcome.message:
        parts.append(outcome.message)
    if outcome.warnings:
        parts.extend(outcome.warnings)
    return _join_message(None, *parts)


def _join_message(*parts: str | None) -> str | None:
    text = "；".join(part for part in parts if part)
    # `message` 是 String(1000)。截断而不是让它 500：一行报二十条错（题号列坏了）
    # 时超长是真的，而「这一行有问题」这件事比「第二十条错是什么」重要。
    return text[:1000] or None


def _attach_external_result(
    db: Session,
    *,
    batch: AssessmentImportBatch,
    row: AssessmentImportRow,
    school: School,
    task: AssessmentTask | None,
    student: Student | None,
    payload: dict[str, Any],
) -> None:
    """建一条外部结果，**并把行的指针指过去**。

    那一列（`assessment_import_row.external_result_record_id`）与结果上的 `row_id`
    是一对**互相指着的**外键（模型里那个 `use_alter=True` 的环，专门为了能建出来）。
    只写 `row_id` 那一半是不够的：`_payload_of` 读的是**行上**那一列——它回答的是
    「这一行的答案去哪了」。少写它，提交时每一行都会拿到「第 N 行没有外部结果记录，
    无法导入。请重新上传这份文件再提交。」，而那时候屏幕上每一行都写着「匹配成功」。
    """
    result = _external_result_for_row(
        db, batch=batch, row=row, school=school, task=task, student=student, payload=payload
    )
    db.add(result)
    # 先落一次，拿到 `result.id` 再回填——环的两边不能在同一条 INSERT 里写完
    db.flush()
    row.external_result_record_id = result.id


def _external_result_for_row(
    db: Session,
    *,
    batch: AssessmentImportBatch,
    row: AssessmentImportRow,
    school: School,
    task: AssessmentTask | None,
    student: Student | None,
    payload: dict[str, Any],
) -> AssessmentExternalResult:
    """这一行对应的那条外部结果。

    **与 `assessment_result` 不是一回事，不合并**（模型那个类的 docstring 写了理由）：
    那一张是本系统引擎按 `scale_rule` 算出来的，这一张是校外平台给的事实。
    本系统算的分在提交时由 `score_session` 写进 `assessment_result`；这一张留着
    回答另一个问题——「这条结果是从哪来的、原始长什么样」。

    **`result_payload_json` 只存最小化的那两样**（DDL 第 284 行的原话是「按敏感字段
    策略加密或最小化保存」，这里选最小化）：本系统要用的只有 `answers`（100 题，
    提交时要写进 `assessment_answer`）与 `duration_seconds`。**不存外部平台的原样
    载荷**——那份 JSON 里可能有这个系统管不着、也不该留存的字段（平台自己的
    用户标识、设备信息、它算的中间分），存进来等于在本系统的访问控制之外复制了
    一份内容，而这份内容没有任何边界（`result_payload_json` 是 JSON 列，谁往里
    写什么都不会被检查）。最小化之后，「这一列里可能有什么」是有答案的。

    两档（§18.9）写进来的列不同：

    | 列 | 完整答案 | 汇总 |
    |---|---|---|
    | `source_type` | `EXTERNAL_FULL_ANSWER` | `EXTERNAL_SUMMARY` |
    | `result_payload_json` | 100 题答案 + 用时 | **只有用时** |
    | `total_score` / `dimension_scores_json` | 空（本系统会自己算） | **平台给的分** |
    | `verification_status` | `PENDING` | `PENDING` |

    完整答案那一档的两列**留空是有意的**：那两个数该由本系统的引擎在提交时算，预览阶段
    先算等于把「还没决定要不要的那些行」也评一遍。

    汇总那一档的 `scale_code` / `scale_version` / `rule_version` 三列**全部留空**，
    这是这一档唯一能说的实话：§18.9 要求「保存外部版本、外部规则」，而**文件里
    没有这两列**——模板只有年级/班级/姓名/性别/年龄/用时 + 分数。编一个「EXTERNAL」
    填上去，等于替一份查不到出处的规则担保（与 CLAUDE.md §21 那条「`tested_at`
    刻意不回填」同一个道理）。要真的保存它们，得先让模板带上这两列，那是学校与
    平台之间的事。本系统的量表版本不在这里，它在 `assessment_session.scale_version`。
    """
    scale = _published_scale(db)
    summary = payload.get("form") == SOURCE_TYPE_SUMMARY
    scores = payload.get("dimension_scores") or None
    return AssessmentExternalResult(
        batch_id=batch.id,
        row_id=row.id,
        school_id=school.id,
        task_id=task.id if task else None,
        student_id=student.id if student else None,
        source_system=batch.source_system,
        # 文件里没有「外部结果 id」这一列（模板只有年级/班级/姓名/性别/年龄/用时 +
        # 100 题答案），所以这里是 NULL。那一列有唯一键 `(source_system,
        # external_result_id)`，而 MySQL 的唯一键里 NULL **互不冲突**——所以
        # 「学校拿不到平台的结果号」这件事不会在这里变成一条插入失败。
        external_result_id=None,
        source_type=SOURCE_TYPE_SUMMARY if summary else SOURCE_TYPE_FULL_ANSWER,
        # 本系统的量表（这份外部数据是按本系统哪个量表导的）。**只在完整答案那一档写**：
        # 那一档的 100 题答案就是按这个量表的题号排的，提交时引擎也按它算。
        # 汇总那一档的分是平台按它自己的量表算的，挂上本系统的版本等于把两套常模
        # 说成一套——那正是这两张表不合并的理由。
        scale_code=None if summary else (scale.code if scale else None),
        scale_version=None if summary else (scale.version if scale else None),
        # `rule_version` **留空**：那一列回答的是「这条结果按本系统的哪一版规则算出来的」，
        # 而这一张表里的结果恰恰**不是**本系统算的（它与 `assessment_result` 不合并，
        # 就是为了让这两句话各自有地方说）。本系统的规则版本在提交那一刻由
        # `score_session` 写进 `assessment_result.rule_version`——那一条才是算出来的那一条。
        # 在这里填上当前生效的版本等于替一条没被它评过的结果作证。
        rule_version=None,
        tested_at=batch.tested_at or datetime.now(),
        # 「核验状态」（§18.9 的最后一项）。默认 `PENDING` = 还没人看过——
        # 而汇总那一档的全部意义就在这一列上：**平台给的分还没被核验，所以它不成
        # 为本系统的 `assessment_result`**（见 `_write_sheet` 里那一档的分叉）。
        verification_status=MATCH_PENDING,
        # 汇总那一档的两列（`total_score` / `dimension_scores_json`），§18.9 的
        # 「原始分数」。`or None`：八个维度一个都没给时存 NULL 而不是 JSON `{}`
        # ——空对象在维度分布那类按 `json_contains` 过滤的查询里是另一回事。
        total_score=payload.get("total_score") if summary else None,
        dimension_scores_json=scores if summary else None,
        # 最小化策略按档收窄：汇总那一档的分数已经占了上面两列，这里再存一遍就是
        # 同一份数据在同一行里有两份（而 `result_payload_json` 是那个「谁往里写什么
        # 都不会被检查」的 JSON 列）。这一档里这一列**只留用时**。
        #
        # 完整答案那一档**逐字沿用 V1.0 的那两样**，把行负载上多出来的三个键
        # （`form` / `total_score` / `dimension_scores`，§18.9 加进解析结果里的）
        # 剔掉：它们在**解析与匹配**那一段是有用的（`form` 决定这一行走哪一档、
        # 分数进列），而这一列回答的是「这份答卷的原样是什么」——
        #   * `form` 在 `source_type` 那一列上，`_payload_of` 按它把键补回来，
        #     同一件事不留两个地方；
        #   * 那两个分数**本来就不该由平台说了算**：一份带 100 题答案的文件，
        #     分要由本系统引擎算（`_write_sheet` 走 `score_session`）。把平台的数
        #     一起存进来，等于在这份原始答卷旁边留了一份**可能与本系统算出来的分
        #     不一致**的数，而事后谁也说不清该信哪一个。
        result_payload_json=(
            {"duration_seconds": payload.get("duration_seconds")}
            if summary
            else {key: payload.get(key) for key in ("answers", "duration_seconds")}
        ),
    )


def _batch_errors(batch_name: str, tested_on: date) -> list[str]:
    errors = []
    if not batch_name:
        errors.append("批次名称不能为空")
    elif len(batch_name) > MAX_BATCH_NAME_LENGTH:
        # `AssessmentTask.name` 是 String(128)。MySQL 严格模式下超长直接 500，
        # 而 500 里那句是 `Data too long for column 'name'`——离操作员看到的那个
        # 「批次名称」隔着一层，所以这一条必须在导入前挡下来，说清楚是哪一个字段
        errors.append(f"批次名称过长（最多 {MAX_BATCH_NAME_LENGTH} 字）")
    if tested_on > date.today():
        # 未来日期会写出未来时间的会话与答卷：它们会落在按日期排序的各项统计前面，
        # 而且「已逾期」这种判断会对着一个还没发生的测评做
        errors.append("测评日期不能晚于今天")
    return errors


def _parse_grade(value: str, errors: list[str]) -> str | None:
    if not value:
        errors.append("缺少年级")
        return None
    stripped = value.strip()
    grade = GRADE_BY_NUMBER.get(stripped) or GRADE_NAMES.get(stripped)
    if not grade:
        errors.append(GRADE_INPUT_HINT)
    return grade


def _parse_class(value: str, grade_name: str | None, errors: list[str]) -> str | None:
    """`4` → `704`（年级前缀 + 班号补两位）。

    已写成三位编号的（`704`）直接用它，但**首位必须与年级一致**——与「学生信息导入」
    的 `check_grade_and_class` 是同一条规则、同一句话。两列各自看都合法而合起来矛盾，
    是这一层唯一「填错了却看着像对的」情况。
    """
    if not value:
        errors.append("缺少班级")
        return None
    stripped = value.strip()
    if not stripped.isascii() or not stripped.isdigit():
        errors.append(CLASS_INPUT_HINT)
        return None
    if len(stripped) <= 2:
        if grade_name is None:
            return None
        number = int(stripped)
        if number < 1:
            errors.append(CLASS_INPUT_HINT)
            return None
        return f"{GRADE_PREFIX[grade_name]}{number:02d}"
    if len(stripped) == 3:
        owner = CLASS_PREFIX_GRADE.get(stripped[0])
        if owner is None:
            errors.append(CLASS_INPUT_HINT)
            return None
        if grade_name is not None and owner != grade_name:
            errors.append(f"班级 {stripped} 属于{owner}，与年级 {grade_name} 不一致")
            return None
        return stripped
    errors.append(CLASS_INPUT_HINT)
    return None


def _parse_age(value: str, warnings: list[str]) -> int | None:
    """年龄只用于消歧，所以读不出来是**提示**而不是错误：记录照常导入，只是弱了一点。"""
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 99:
        warnings.append(f"年龄「{value}」无法识别，已忽略")
        return None
    return int(value)


def _parse_duration(value: str) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    match = _DURATION.match(value)
    if not match:
        return None, f"作答用时「{value}」无法识别，已留空"
    seconds = int(match.group(1))
    if seconds > MAX_DURATION_SECONDS:
        return None, f"作答用时 {seconds} 秒超出可记录范围，已留空"
    return seconds, None


def _parse_answers(values: list[str]) -> tuple[list[str], list[str]]:
    answers: list[str] = []
    blank: list[int] = []
    invalid: list[tuple[int, str]] = []
    for offset, value in enumerate(values):
        question_no = offset + 1
        if value == "":
            blank.append(question_no)
            answers.append("NO")
        elif value in {"1", "是", "YES"}:
            answers.append("YES")
        elif value in {"0", "否", "NO"}:
            answers.append("NO")
        else:
            invalid.append((question_no, value))
            answers.append("NO")

    errors = []
    if blank:
        shown = "、".join(str(number) for number in blank[:5])
        suffix = f"（共 {len(blank)} 题）" if len(blank) > 5 else ""
        errors.append(f"第 {shown} 题未作答{suffix}")
    if invalid:
        shown = "、".join(f"第 {number} 题「{value}」" for number, value in invalid[:5])
        suffix = f"（共 {len(invalid)} 题）" if len(invalid) > 5 else ""
        errors.append(f"{shown} 的答案{ANSWER_HINT}{suffix}")
    return answers, errors


def _parse_total_score(value: str) -> tuple[int | None, list[str]]:
    """汇总文件的总分（§18.9）。**读不出来是错误，不是提示。**

    与年龄相反：年龄只用来消歧，读不出来这一行照样导得进去；而汇总文件里总分是
    这一行**唯一**的分数来源——没有它，导进去的是一场没有任何结果的空会话，
    在界面上与「这个学生测了但是没事」长得一模一样。所以宁可让操作员改文件。

    范围按 MHT 内容题总分（0–90）。效度题不计入总分，因此只有平台汇总分的导入
    也必须遵守同一上限——
    `test_assessment_import_api.py` 用一条「本地引擎与导入的分段判级一致」的用例盯着这一点。
    """
    if not value:
        # 说的就是操作员在文件里看到的那一列的名字（`SUMMARY_TOTAL_LABELS` 的第一个），
        # 不是内部字段名——他拿着这句话要去改的是那个文件
        return None, ["缺少总分"]
    if not value.isascii() or not value.isdigit():
        return None, [f"总分「{value}」不是 0–{TOTAL_SCORE_MAX} 的整数"]
    score = int(value)
    if not SCORE_MIN <= score <= TOTAL_SCORE_MAX:
        return None, [f"总分 {score} 超出 0–{TOTAL_SCORE_MAX} 的范围"]
    return score, []


def _parse_dimension_scores(values: dict[str, str]) -> tuple[dict[str, int], list[str]]:
    """八个维度分。**读不出来的那一维跳过、其余照收**，与总分相反。

    一维读不出来时整行作废没有道理：这一行还是有总分、还是进了统计，
    只是那一个维度缺一格。而「一格空着」在维度分布那一页上本来就存在（不是每个
    学生八维都有值），所以缺一维不会让哪一页看起来坏掉。
    """
    scores: dict[str, int] = {}
    errors: list[str] = []
    for code, value in values.items():
        if not value:
            continue
        if not value.isascii() or not value.isdigit() or not SCORE_MIN <= int(value) <= SCORE_MAX:
            errors.append(f"维度「{code}」的分数「{value}」无法识别，已留空")
            continue
        scores[code] = int(value)
    return scores, errors


def locate_student(
    db: Session,
    *,
    school: School | None,
    grade_name: str | None,
    class_name: str | None,
    name: str,
    gender: str | None,
    age: int | None,
    actor: UserAccount,
) -> tuple[Student | None, list[str], list[str]]:
    """按 姓名 + 性别 + 年龄 + 年级 + 班级 定位到名册里的一名学生。

    两个刻意的决定：

    * **「班级不存在」与「没有这个学生」分开报。** 合成一句「名册中没有「xxx1」（初一 704）」
      在库里的班级叫 `1班`（`seed_demo` 的演示数据就是这样）时会给出**假**诊断：班级根本
      没有，而话里说的是人没有。分开之后，任何一句都只有一种解释。
    * **超出数据范围与「没有这个学生」返回逐字相同的一句话。** 两者文案不同，等于让只
      看得到自己班的心理老师用上传文件来枚举全校名册：发一个名字，看回的是「不在你的
      范围内」还是「没这个人」。照 `care_service` 对越权档案的既有做法（不告诉越权者
      档案是否存在），范围外的人在这里就是「不存在」。
    """
    if school is None or grade_name is None or class_name is None:
        return None, [STUDENT_NOT_FOUND.format(class_name=class_name or "", name=name)], []

    grade = db.scalar(select(Grade).where(Grade.school_id == school.id, Grade.name == grade_name))
    class_group = (
        db.scalar(
            select(ClassGroup).where(
                ClassGroup.school_id == school.id,
                ClassGroup.grade_id == grade.id,
                ClassGroup.name == class_name,
            )
        )
        if grade
        else None
    )
    if class_group is None:
        return None, [CLASS_NOT_FOUND.format(class_name=f"{grade_name} {class_name}")], []

    candidates = db.scalars(
        select(Student).where(Student.class_id == class_group.id, Student.name == name).order_by(Student.id)
    ).all()
    # 范围过滤放在**数人数之前**：过滤是「这个人在这里等于不存在」，不是「找到了但不让导入」。
    # 顺手让多候选消歧也只在看得见的人里进行。
    visible = [student for student in candidates if student_in_scope(db, actor, student)]
    not_found = STUDENT_NOT_FOUND.format(class_name=f"{grade_name} {class_name}", name=name)
    if not visible:
        return None, [not_found], []

    warnings: list[str] = []
    matched = _disambiguate(visible, gender=gender, age=age)
    if matched is None:
        return (
            None,
            [f"{grade_name} {class_name} 有 {len(visible)} 名「{name}」，无法按性别与年龄定位"],
            [],
        )
    if len(visible) > 1:
        warnings.append(
            f"同班有 {len(visible)} 名同名同学，已按性别与年龄定位到 {matched.student_no}"
        )
    # 年龄与名册不符**不在这里报**：它不是「定位时的一句话」，而是一个要操作员拍板的
    # 冲突（`_conflicts_for_student`）。名册上存的是**当前**年龄（迁移 0011），
    # 文件里是考试当天的岁数，导入一份去年的文件时两者差一岁是常事——所以要问，
    # 而不是替他决定「以文件为准」或「以名册为准」。
    return matched, [], warnings


def _disambiguate(
    candidates: list[Student], *, gender: str | None, age: int | None
) -> Student | None:
    """性别优先、年龄其次；年龄精确优先，其次相差一岁。

    性别不符时**不**把候选清空：性别与名册不符按用户的决策只是告警，那么一个班里的
    两个同名同学就不该因为文件性别写反而变成「定位不到」——那会把一条告警升级成一条错误。

    差一岁要当**候选**而不是排除项：名册上的年龄是学校最近一次导入时填的（0011 之后它
    不再自己变），而这份文件可能是去年那次普查，同一个人的两个数字正好差一岁。
    """
    if len(candidates) == 1:
        return candidates[0]
    narrowed = candidates
    if gender:
        by_gender = [student for student in narrowed if student.gender == gender]
        if by_gender:
            narrowed = by_gender
    if len(narrowed) > 1 and age is not None:
        by_age = [student for student in narrowed if student.age == age]
        if by_age:
            narrowed = by_age
        else:
            near = [
                student
                for student in narrowed
                if student.age is not None and abs(student.age - age) == 1
            ]
            if near:
                narrowed = near
    return narrowed[0] if len(narrowed) == 1 else None


# --------------------------------------------------------------------------
# 匹配：§18.4 的六步
# --------------------------------------------------------------------------


@dataclass
class MatchOutcome:
    """一行外部记录匹配到名册的结果。

    `status` 是八个码之一，而 `student` 与匹配到的年级 / 班级是**只在匹配成功时才
    有的**。返回一个整体而不是「先查学生、再判状态」，是因为 §18.4 第 6 步的判据
    （「只有唯一候选**且**没有未处理冲突时，才允许自动匹配」）要求这两件事同时成立
    ——拆成两个函数就会有一处忘了判。
    """

    status: str
    student: Student | None = None
    grade: Grade | None = None
    class_group: ClassGroup | None = None
    # 多候选时把候选人的 id 留给人看：§18.4 的 `AMBIGUOUS` 是「人工选择后是」，
    # 而下一步（逐行处置）要让人在这几个里面点一个。只报「有 2 个候选」而不说是谁，
    # 那条路就走不下去。
    candidates: list[int] = field(default_factory=list)
    confidence: float | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 这个状态的一句话解释。它和 `errors` 是两件事：`errors` 是「这一行为什么进不来」
    # （按行报给人改文件），`message` 是「这个状态是什么意思」（按行报给人做决定）。
    message: str | None = None
    # `OUT_OF_SCOPE` 内部再分两种（§18.7、编辑 2 那一组常量）。**它只在这一档上有值**，
    # 别的档留空——两列一起读才答得上「这一行为什么没进这一场」，而只看 `match_status`
    # 时那两种的处置完全不同（一个去补发目标、一个去核名字/班级）。
    out_of_scope_reason: str | None = None
    # 与上面那一列**逐字同一个形状**（只在一档上有值），只是它挂在 `CONFLICT` 上：
    # §18.8 把「系统内已有一份答卷」分成「正在作答」与「已经交卷」两行，而出路不同。
    #
    # **为什么要有这个字段**：`_conflict_code_for` 此前是拿 `status` 现算的，而这两
    # 半的 `status` 都是 `MATCH_CONFLICT`——现算分不开它们，也没法从 `student` 上再
    # 查一次库（那是一次多余的查询，还要求在落库那一刻手里的会话仍是「最新那一场」）。
    # 判定这件事的地方只有这里（`match_student` 刚查完 `_existing_session_for_match`），
    # 所以答案就在这儿记下来。
    conflict_code: str | None = None

    @property
    def importable(self) -> bool:
        return self.status in MATCH_STATUSES_IMPORTABLE


def match_student(
    db: Session,
    *,
    school: School | None,
    grade_name: str | None,
    class_name: str | None,
    name: str,
    gender: str | None,
    age: int | None,
    tested_on: date,
    actor: UserAccount,
    task: AssessmentTask | None = None,
) -> MatchOutcome:
    """按 §18.4 的**六步**把一行外部记录匹配到名册上的一个人。

    六步的次序不是排版，是判据：第 5 步的年龄只用于消歧与校验，第 6 步要先确认
    「唯一候选」再看冲突。把它写成「先按姓名找到人、再补各种校验」会同时丢掉两件事：
    多候选时年龄**不该**被当成排除项（名册上的年龄是学校上次导入时填的，一份去年的
    文件差一岁是常事），以及冲突**不该**在多候选时被报出来（那时还没有「这一行是谁」
    这个前提，报冲突等于替一个还没定下来的人下结论）。

    与 V1.0 的 `locate_student` 的关系：那个函数返回 `(student, errors, warnings)`，
    而它的三个出口（找不到 / 多候选 / 找到了）正好是这里三个状态。**保留它**是因为
    `AMBIGUOUS` 与 `NOT_FOUND` 的文案逐字沿用——那几句是操作员照着改文件的依据，
    换一套说法等于让去年那份排查记录对不上。

    两个刻意的决定（沿用 `locate_student`，理由一字不改）：

    * **「班级不存在」与「没有这个学生」分开报。** 合成一句「名册中没有「xxx1」（初一
      704）」在库里的班级叫 `1班`（`seed_demo` 的演示数据就是这样）时会给出**假**诊断：
      班级根本没有，而话里说的是人没有。
    * **超出数据范围与「没有这个学生」返回逐字相同的一句话。** 否则只看得到自己班的
      心理老师能用上传文件来枚举全校名册：发一个名字，看回的是「不在你的范围内」还是
      「没这个人」（同 `care_service` 对越权档案的既有做法）。
    """
    # 第 1 步：限定当前学校。
    if school is None:
        return MatchOutcome(
            MATCH_NOT_FOUND,
            errors=["系统里还没有学校记录，无法匹配"],
        )

    # 第 2 步：姓名标准化（去首尾空格与不可见字符）。
    normalized_name = normalize_person_name(name)
    if not normalized_name:
        return MatchOutcome(MATCH_INVALID_ROW, errors=["缺少姓名"])

    # 第 3 步：外部年级、班级映射为系统标准年级、班级。
    if grade_name is None or class_name is None:
        return MatchOutcome(MATCH_INVALID_ROW, errors=["缺少年级或班级，无法自动匹配"])
    grade = db.scalar(
        select(Grade).where(Grade.school_id == school.id, Grade.name == grade_name)
    )
    class_group = (
        db.scalar(
            select(ClassGroup).where(
                ClassGroup.school_id == school.id,
                ClassGroup.grade_id == grade.id,
                ClassGroup.name == class_name,
            )
        )
        if grade
        else None
    )
    if class_group is None:
        # 年级不存在与班级不存在共用一句：两句话的差别对操作员没有用（两件事的
        # 出路都是「先去名册上把这个班建出来」），而分成两句会让 `NOT_FOUND` 这一档
        # 出现两种文案——逐行明细那一列要能一眼扫过去。
        #
        # **`message` 刻意空着。** 它会被 `_row_message` 拼在 `errors` **后面**，
        # 而「名册上没有这个班级」是上面那句的上一级说法：两句并排时，具体的那一句
        # 读起来像概括的那一句的一个特例，而操作员按后一句去查会查不出任何东西
        # （名册上当然「没有这个班级」，他需要知道的是**哪个**班）。`NOT_FOUND`
        # 这一档的两句具体话（哪个班 / 哪个人）已经把出路说完了。
        return MatchOutcome(
            MATCH_NOT_FOUND,
            grade=grade,
            errors=[CLASS_NOT_FOUND.format(class_name=f"{grade_name} {class_name}")],
        )

    # 第 4 步：按 school_id + grade_id + class_id + normalized_name 查候选。
    #
    # **名册那一侧的姓名也要标准化之后再比。** `student.name` 由学生信息导入写进来，
    # 而那条链路的清洗规则与这里不是同一套（它只管去首尾空格），名册上真出现过
    # 「张 三」这种写法。在 SQL 里用 `==` 比会**静默漏掉**他们，症状是「这个人明明在
    # 名册上，导入却说找不到他」——而逐行明细里那一格显示的名字与名册上看起来一模一样。
    # 一个班只有几十人，取出来在 Python 里比的代价可以忽略，换来的是「哪一侧的姓名
    # 需要清洗」这个问题不再存在。
    candidates = [
        student
        for student in db.scalars(
            select(Student).where(Student.class_id == class_group.id).order_by(Student.id)
        )
        if normalize_person_name(student.name) == normalized_name
    ]

    # 第 5 步：年龄用于校验和消歧，不作为唯一身份。
    #
    # 数据范围过滤放在**数人数之前**：过滤是「这个人在这里等于不存在」，不是
    # 「找到了但不让导入」。顺手让多候选消歧也只在看得见的人里进行。
    visible = [student for student in candidates if student_in_scope(db, actor, student)]
    if not visible:
        return MatchOutcome(
            MATCH_NOT_FOUND,
            grade=grade,
            class_group=class_group,
            # **候选一个都不留。** 名册上有同名的人、只是不在你的范围内时，这一行必须
            # 与「真的没有这个人」**处处一样**——不只是那一句话逐字相同，`candidate_count`
            # 也得是 0，明细里也得在。逐一说明，因为「藏起来」看起来更安全：
            #
            # * 那一句话从 `locate_student` 起就是同形的（发一个名字、看回的是
            #   「不在你的范围内」还是「没这个人」，就是一条枚举全校名册的探针）。
            # * 但**把行藏起来是更强的探针**：行在 = 没这个人，行不在 = 有这个人，
            #   一次上传就能把别的班的名册问个遍——而这条链路上操作员本来就拿着文件，
            #   行里显示的名字、班级全是他自己填的（`raw_*`），藏起来保不住任何东西。
            #   挡住「看一眼谁消失了」的唯一办法是**没有谁消失**。
            # * `candidate_count` 同理：1 与 0 的差别就是「这个班有 1 个叫张三的」。
            #   它按 `visible` 记，所以别人班上的候选永远不会出现在这位读者的数字里。
            errors=[
                STUDENT_NOT_FOUND.format(class_name=f"{grade_name} {class_name}", name=name)
            ],
            # 同上面那个「班级不存在」：`message` 空着，别把上一级的说法接在
            # 具体的那一句后面。对外那一句话的逐字形状由 `errors` 一处决定。
        )

    warnings: list[str] = []
    matched = _disambiguate(visible, gender=gender, age=age)
    if matched is None:
        return MatchOutcome(
            MATCH_AMBIGUOUS,
            grade=grade,
            class_group=class_group,
            candidates=[student.id for student in visible],
            message=(
                f"{grade_name} {class_name} 有 {len(visible)} 名「{name}」，"
                "无法按性别与年龄定位；请核对名册上这几名学生的性别与年龄"
            ),
        )
    if len(visible) > 1:
        warnings.append(
            f"同班有 {len(visible)} 名同名同学，已按性别与年龄定位到 {matched.student_no}"
        )

    # 第 6 步：唯一候选 —— 再看有没有未处理的冲突。
    outcome = MatchOutcome(
        MATCH_MATCHED,
        student=matched,
        grade=grade,
        class_group=class_group,
        # **定下来之后也把候选留下。** 两个人同名时上面那句告警写着「有 2 名同名
        # 同学，已按性别与年龄定位到 S702」，而「候选数」那一格若因为匹配已经完成
        # 就写 0，同一行上两个数说的是同一件事却说不到一起——读者会先怀疑界面。
        # 它同时是复核的依据：候选 1 名是精确定位，候选 2 名是**这一层替他挑了
        # 一个**，值得人看一眼。
        candidates=[student.id for student in visible],
        warnings=warnings,
        # 同名同班的候选只有一个时要靠这四样定位，所以「怎么定下来的」也是一条信息
        confidence=1.0 if len(visible) == 1 else 0.9,
    )

    # 任务范围（§18.7）：名册上有他、也归这位操作员管，但他**不在这场任务里**。
    # 与 `NOT_FOUND` 是两个不同的答案：「这个人不在你的名册上」要先去补名册，
    # 「这个人不在这一场里」的出路是补发目标（§18.2 / §22 的补发）。
    #
    # **这一档内部还有两种，出路完全不同**（§18.7）：范围覆盖他、只是发放时他还没有
    # 目标行 → 补发就有；范围根本不覆盖他 → 这个人本来就不该在这场里，补发也补不上
    # （`supplement_targets` 的候选集就是范围交集）。两者的 `match_status` **相同**
    # ——§18.4 那八个码是冻结的（CLAUDE.md §25「一个不多一个不少」），所以区别写在
    # `out_of_scope_reason` 这一列上，界面上才有话说。
    if task is not None and not _is_task_target(db, task.id, matched.id):
        outcome.status = MATCH_OUT_OF_SCOPE
        if _task_scope_covers(db, task, matched):
            outcome.out_of_scope_reason = OUT_OF_SCOPE_SUPPLEMENT_CANDIDATE
            outcome.message = "该学生不在本场测评的目标名单里，但在这场测评的范围内，可以补发给他"
        else:
            outcome.out_of_scope_reason = OUT_OF_SCOPE_NOT_IN_SCOPE
            outcome.message = "该学生不在本场测评的发放范围内"
        return outcome

    # 与已有结果冲突（§18.8）。`DUPLICATE` 与 `CONFLICT` 的分界是**那一场是谁写的**：
    # 外部平台导进来的（`source=IMPORTED`）→ 同一份东西又导了一遍，是重复，
    # 处置是「覆盖上次」；学生在本系统里自己答的 → 两条来源事实指向同一次测评，是冲突，
    # 处置是「以哪一份为准」。
    #
    # **第 6 期把「学生自己答的」再分两半**：正在作答（还没交卷）与已经交卷。
    # 两半都**禁止自动覆盖**，差别在于「以外部为准」这一档还通不通：
    #
    #   正在作答  他可能正答到第 40 题。此时把那场作废等于**扔掉他正在进行的工作**，
    #             而屏幕上没有任何东西能把这个代价说清楚（`supersedes_session_id`
    #             能留痕，但留痕不等于让一个还要接着答题的学生不受影响）。所以这一档
    #             在 `commit_batch` 里被专门挡下来（§20#10）。
    #   已经交卷  他答完了，那一份是完整的答卷事实。「以外部为准」是在两份**成品**
    #             之间选，代价从「打断一个人」变成「哪份算数」——那是可以拍板的。
    #
    # 两半的 `status` 都是 `MATCH_CONFLICT`（§18.4 那八个码是冻结的，一个不多一个
    # 不少），所以区别写在 `conflict_code` 上——与 `OUT_OF_SCOPE` 用
    # `out_of_scope_reason` 分两半是同一个形状。
    existing = _existing_session_for_match(db, matched.id, tested_on=tested_on, task=task)
    if existing is not None:
        if existing.source == "IMPORTED":
            outcome.status = MATCH_DUPLICATE
            outcome.conflict_code = CONFLICT_DUPLICATE
            outcome.message = MONTH_CONFLICT_MESSAGE.format(month=f"{tested_on:%Y-%m}")
        elif existing.submitted_at is None:
            outcome.status = MATCH_CONFLICT
            outcome.conflict_code = CONFLICT_IN_SYSTEM_IN_PROGRESS
            outcome.message = (
                "该学生正在本场测评里作答、还没有交卷。"
                "请不要覆盖他手里这一份：等他交卷之后再导入，或者这一行先放弃"
            )
        else:
            outcome.status = MATCH_CONFLICT
            outcome.conflict_code = CONFLICT_IN_SYSTEM_RESULT
            outcome.message = "该学生在本场测评里已有系统内提交的答卷，需要选择以哪一份为准"

    # 年龄校验（§18.5）：学生唯一、但文件里的年龄与名册不符。
    # **排在重复与冲突之后**：先回答「这一行会不会写进去」，再回答「写进去之后要不要
    # 顺带动名册」。反过来时，一条已经写完的行还会再报一次年龄——而它在预览里
    # 已经被回绝过一次了。
    if (
        outcome.status == MATCH_MATCHED
        and age is not None
        and matched.age is not None
        and matched.age != age
    ):
        outcome.status = MATCH_AGE_CONFLICT
        outcome.message = f"年龄 {age} 与名册 {matched.age} 不符"

    return outcome


def _is_task_target(db: Session, task_id: int, student_id: int) -> bool:
    """这名学生在这场任务的目标名单里吗（§18.7 的 `OUT_OF_SCOPE` 判据）。"""
    return (
        db.scalar(
            select(AssessmentTarget.id).where(
                AssessmentTarget.task_id == task_id,
                AssessmentTarget.student_id == student_id,
            )
        )
        is not None
    )


def _task_scope_covers(db: Session, task: AssessmentTask, student: Student) -> bool:
    """这名学生在**发放范围**里吗——「补发能不能补上他」的判据（§18.7）。

    与 `_is_task_target` 是两个问题：那个问「实际发给他了吗」（`assessment_target`
    的行），这个问「当初打算发给谁」（`assessment_task_scope` 的行，§22 那张表）。
    两者本来就该不同：发放之后从外校转进来一个学生，**范围覆盖他、目标行里没有他**
    ——这正是 `SUPPLEMENT_CANDIDATE` 的形状，也是补发（§18.2）存在的理由。

    **没有范围记录时退到「同一所学校」，不替历史编裁决**（§22 的同一条）：范围行是
    阶段 1 才有的，此前建的校内任务确实按全校发过，但它没有那一行记录。说「范围不覆盖」
    会把这些任务里所有不在目标行上的学生都判成 `NOT_IN_TASK_SCOPE`——而那些任务
    恰恰是「转学生补发」最常发生的地方。反过来，退到学校这一档不会放过任何跨校的行：
    同一所学校是这场任务能覆盖到的**最大**范围。

    `STUDENT` 那一档今天没有写入方（§4：把范围切到单人级要重新想反推风险），但判据
    在这里写全，免得将来接上时它与 `student_scope_predicate` 各说各话。
    """
    scopes = db.scalars(
        select(AssessmentTaskScope).where(AssessmentTaskScope.task_id == task.id)
    ).all()
    if not scopes:
        return student.school_id == task.school_id
    for scope in scopes:
        # `is not None` 的判断是必要的，与 `student_scope_predicate` 同一个理由：
        # 半填的一行（grade_id 为 NULL）不能被读成匹配 grade_id 也为 NULL 的学生
        if scope.scope_type == ScopeType.SCHOOL and scope.school_id == student.school_id:
            return True
        if scope.scope_type == ScopeType.GRADE and scope.grade_id is not None:
            if scope.grade_id == student.grade_id:
                return True
        if scope.scope_type == ScopeType.CLASS and scope.class_id is not None:
            if scope.class_id == student.class_id:
                return True
        if scope.scope_type == ScopeType.STUDENT and scope.student_id is not None:
            if scope.student_id == student.id:
                return True
    return False


def _existing_session_for_match(
    db: Session, student_id: int, *, tested_on: date, task: AssessmentTask | None
) -> AssessmentSession | None:
    """这名学生已经有的、会与这一行打架的那一场（没有就是 `None`）。

    **两层判重，两种口径，各自都对**（这是用户 2026-09-19 的裁决「两层都留」）：

    * **批次绑定了任务时**：看这一场任务里这名学生有没有会话。§18.8 那张表问的
      就是这一件事——「一个任务一个学生可以保留多个来源事实，但同一时刻只能有一个
      有效结果」。
    * **没绑定任务时**：沿用 V1.0 的**自然月**判重（缺口 8，用户 2026-09-17 要的
      「不同月份的评测视为不同的测试任务」）。

    两套判据并存不是妥协，是它们回答的问题不同：按任务的问「这一场我导过没有」，
    按月的问「这个月学校那次普查我导过没有」。**合并它们要先决定「同一场任务里，
    两来源的分数谁算数」，那是业务问题**（缺口 9 里那条 `TODO_BUSINESS_CONFIRMATION`），
    不是这里能顺手定的。
    """
    if task is None:
        return existing_import_session(db, student_id, tested_on)
    return db.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.task_id == task.id,
            AssessmentSession.student_id == student_id,
        )
        .order_by(AssessmentSession.id.desc())
    )


def existing_import_session(
    db: Session, student_id: int, tested_on: date
) -> AssessmentSession | None:
    """这个学生在**同一个自然月**里已经导入过的那一场测评（没有就是 `None`）。

    「按月度为单位判重」是 2026-09-17 用户的要求：「不同月份的评测视为不同的测试任务」。
    所以在判重这件事上，`9月16日` 与 `9月17日` 是同一场，`9月30日` 与 `10月1日`
    是两场。此前按**天**判重，而查重的结果又不是「跳过」而是硬错误（`errors` 里一行字），
    于是同一场普查学校分两批导（先是初一、后是初二改完的文件）时，第二天那条记录
    直接被挡在门外，唯一的出路是改系统日期。

    两个条件缺一不可：会话自身是导入的（`source`），它所在的任务也是导入批次任务。
    只按 `submitted_at` 落在本月来找会在系统内那场恰好也在本月的学生身上误报——
    而「系统内答过」根本不是重复，他答的是另一场测评。
    """
    start, end = month_bounds(tested_on)
    return db.scalar(
        select(AssessmentSession)
        .join(AssessmentTask, AssessmentSession.task_id == AssessmentTask.id)
        .where(
            AssessmentSession.student_id == student_id,
            AssessmentSession.source == "IMPORTED",
            AssessmentTask.source == "IMPORTED",
            AssessmentSession.submitted_at >= start,
            AssessmentSession.submitted_at < end,
        )
        .order_by(AssessmentSession.id.desc())
    )


def _current_effective_session(
    db: Session, student_id: int, task_id: int
) -> AssessmentSession | None:
    """这名学生在这场比赛里**当前有效**的那一场（没有就是 `None`）。

    与上面那个 `_existing_session_for_match` 回答的是**两个问题**，别互相顶替：

    * `_existing_session_for_match` 问「这一行会不会打架」（判重，取最新那一场，
      不看 `is_effective`）——它的读者是预览，那一层要回答「你导的这份东西和库里
      什么撞上了」；
    * 这一个问「现在算数的是哪一场」——它的读者是 §18.8 的处置，`USE_EXTERNAL`
      要作废它、`KEEP_BOTH` / `KEEP_ONLINE` 要保住它。

    一个学生在一场任务里最多一场 `is_effective=1`（`uq_session_effective_task_student`
    是生成列唯一键），所以 `order_by` 在这里只是为了让 SQL 确定，不是在选。
    """
    return db.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.task_id == task_id,
            AssessmentSession.student_id == student_id,
            AssessmentSession.is_effective.is_(True),
        )
        .order_by(AssessmentSession.id.desc())
    )


def _conflict_row_is_decided(row: AssessmentImportRow) -> bool:
    """冲突行的问题有没有被回答过（§18.8）。

    **冲突行有它自己的门，与整批的 `resolution` 无关**（2026-09-19 用户裁决：
    「冲突行必须逐行选四种处置之一；整批的『覆盖』对这些行无效」）。理由是这条路
    唯一不能发生的事：一次点击、一个整批动作，把几份**学生本人作答的卷子**作废——
    而屏幕上没有任何东西能把这个代价说出来。整批的「放弃」同样不算回答：它只是
    让这一行不写，而「以哪一份为准」这个问题仍然悬着。

    两条出路都算已决定：
    * `conflict_resolution` 有值——人逐行选了四种处置之一；
    * `resolution == skip`——人明确不要这一行（那时「以哪一份为准」不成立）。
    """
    if row.resolution == RESOLUTION_SKIP:
        return True
    return row.conflict_resolution is not None


def _row_will_be_written(row: AssessmentImportRow, resolution: str | None) -> bool:
    """这一行按当前的选择会不会写进库里（`commit_batch` 的 `writes` 判据）。

    **它是一处判据、三个读者**（`writes` 判要不要建任务、循环判跳不跳过、
    `commit_batch` 的门判这一行算不算已决定），所以它抽成函数而不是在四处各写一遍
    ——那个条件已经有两个维度（行自己的处置 / 整批的选择），写四遍必然有一条先漂。

    冲突行取整批的 `resolution` 是**错**的：整批的选择对它们无效（见上面那个函数），
    所以这一支只看行自己的 `resolution`（只有 `skip` 一种可能，其余情形由
    `conflict_resolution` 决定，那时它一定不写 `skip`）。
    """
    if row.match_status not in MATCH_STATUSES_IMPORTABLE:
        return False
    if row.match_status == MATCH_CONFLICT:
        return row.resolution != RESOLUTION_SKIP
    if row.match_status in MATCH_STATUSES_NEEDING_RESOLUTION:
        return (row.resolution or resolution) != RESOLUTION_SKIP
    return True


# --------------------------------------------------------------------------
# 预览令牌
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 落库
# --------------------------------------------------------------------------


def resolve_import_row(
    db: Session,
    row: AssessmentImportRow,
    *,
    actor: UserAccount,
    resolution: str | None = None,
    age_resolution: str | None = None,
    conflict_resolution: str | None = None,
) -> dict[str, Any]:
    """逐行处置一条待拍板的导入行（§18.6 的「逐行处理」）。

    它是 `commit_batch` 那个**整批**选择的行级版本，两者回答同一个问题——
    「这一行写不写进去」（`resolution`）与「写进去时名册上那个年龄动不动、
    这一场按哪个年龄记」（`age_resolution`）。关系是**逐行覆盖整批**：提交时
    `row.resolution or 整批选择`（见 `commit_batch`），所以处置过的行不再受整批选择左右，
    也没处置的行照旧跟着整批走。

    **`conflict_resolution`（§18.8）是这个「逐行」唯一的例外，它没有整批版本**
    （2026-09-19 用户裁决）。前两个问题有整批版本是因为它们的答案整批一样也没关系
    ——「年龄不符的都要覆盖」是一句能一次说完的话。这一问不行：它的每一行都在问
    「这一份学生本人作答的卷子，要不要让外部那份顶掉」，而一次点击同时替 3 个学生
    回答它，正是这条路唯一不能发生的事。所以冲突行**只能**逐行处置，
    `commit_batch` 那边也收不到整批的这一项。

    这里**只写决定，不写测评记录**：真正的落库仍然全部发生在 `commit_batch`。
    与预览同一层道理——一个动作在它真的发生之前，库里不该出现它的后果。所以
    处置完之后那一行仍然在「待确认」那一档里（`match_status` 不变），变的是
    `commit_batch` 那道门：它只对**没处置过**的待拍板行要求整批选择。

    **不可导入的行一律 422，任何处置都救不回来**（`MATCH_STATUSES_UNIMPORTABLE`：
    `INVALID_ROW` / `NOT_FOUND` / `OUT_OF_SCOPE` / `AMBIGUOUS`）。给它们写一个
    `resolution` 就是承诺一件做不到的事——界面上那两句话会各说各话，而 §25 记的
    `AMBIGUOUS` 那条正是这么发现的（`commit_batch` 会拿 NULL 的 `student_id` 去
    `db.get` → AttributeError → 500）。所以出路是说清「这一行该怎么办」：
    补名册、补发目标，都不是这一行自己能解决的。

    `MATCH_MATCHED` 行同样 422：它没有需要拍板的东西，写一个处置只是在记录里留下一条
    谁也解释不了的决定。**年龄三选项只对有年龄冲突的行有意义**，理由同上。

    `resolved_by` / `resolved_at` **只在逐行处置过时写**——它们是把「这一行是人逐行
    决定的」与「这一行跟着整批走的」分开的唯一痕迹（两者在 `resolution` 那一列上长得
    一样，都是 `overwrite`）。这一点在 §18.5 要的「保存…操作人和时间」之外还有用：
    一份 200 行的普查里，事后要能回答「那 3 条年龄不符当时是逐条看过、还是整批点的覆盖」。
    """
    batch = db.get(AssessmentImportBatch, row.batch_id)
    if batch is None:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != BATCH_STATUS_PREVIEW:
        raise AppError(
            "VALIDATION_ERROR",
            "这一批已经提交过了，不能再改单行的处置。要重新导入请重新上传文件。",
            422,
        )
    if row.match_status in MATCH_STATUSES_UNIMPORTABLE:
        raise AppError(
            "VALIDATION_ERROR",
            f"第 {row.row_no} 行无法导入，任何处置都改变不了它：{row.message or '请检查这一行之后重新上传文件。'}",
            422,
        )
    if row.match_status == MATCH_MATCHED:
        raise AppError(
            "VALIDATION_ERROR",
            f"第 {row.row_no} 行不需要确认，直接提交即可",
            422,
        )
    # 范围在**处置时**也查一遍（与 `commit_batch` 那一处同一个理由）：批次是共享的
    # （`imported_by` 只是创建者），而账号的范围可以被改。少了这一句，一个把范围收窄
    # 过的人能给范围外的学生逐行下决定——提交时 `commit_batch` 会拦下来，于是那个
    # **决定静静地不生效**，而他以为那几条已经处置好了。
    if row.student_id is not None:
        ensure_student_in_scope(db, actor, row.student_id)

    if resolution is not None:
        if resolution not in RESOLUTIONS:
            raise AppError("VALIDATION_ERROR", RESOLUTION_HINT, 422)
        row.resolution = resolution
    if age_resolution is not None:
        if row.conflict_code != CONFLICT_AGE_MISMATCH:
            raise AppError(
                "VALIDATION_ERROR",
                f"第 {row.row_no} 行没有年龄冲突，不需要选择年龄处置",
                422,
            )
        row.age_resolution = parse_age_resolution(age_resolution)
    if conflict_resolution is not None:
        # 判据挂在 `match_status` 上而不是 `conflict_code` 上：这一问是「两条来源事实
        # 谁算数」，而只要这一行是 `CONFLICT`，库里就同时存在在线与外部两份事实
        # ——不管细分原因是「他已交卷」还是「他正在作答」。挂在 `conflict_code` 上
        # 的话，将来给这一档再分第三个原因时，那个原因会**悄悄失去这一问**，
        # 而它长得和别人一模一样。
        if row.match_status != MATCH_CONFLICT:
            raise AppError(
                "VALIDATION_ERROR",
                f"第 {row.row_no} 行没有来源冲突，不需要选择处置方式",
                422,
            )
        if conflict_resolution not in CONFLICT_RESOLUTIONS:
            raise AppError("VALIDATION_ERROR", CONFLICT_RESOLUTION_HINT, 422)
        if (
            conflict_resolution == CONFLICT_RESOLUTION_USE_EXTERNAL
            and row.conflict_code == CONFLICT_IN_SYSTEM_IN_PROGRESS
        ):
            # §20#10：**在线答题进行中不得被外部结果顶掉**。这一档拒绝的不是
            # 「以外部为准」这个决定本身（学生交卷之后同一行会变成另一种冲突，
            # 那时四种处置都能选），是**时机**：他可能正答到第 40 题，而作废那一场
            # 等于把他手里的工作扔掉——屏幕上没有任何东西能把那个代价说出来，
            # 因为那一份还没有交卷、没有结果、没有分数可比。
            raise AppError(
                "VALIDATION_ERROR",
                f"第 {row.row_no} 行的学生正在本场测评里作答、还没有交卷，"
                "不能以外部结果顶掉他手里这一份。"
                "等他交卷之后再导入，或者这一行先放弃",
                422,
            )
        row.conflict_resolution = conflict_resolution

    row.resolved_by = actor.id
    row.resolved_at = now_utc_naive()
    db.flush()
    # **审计不在这里写**，由路由在返回数据之前写（§8：先写审计、后返回数据）。
    # 理由与 `/commit` 那一处相同，也是这个文件里所有「路由那一层能拿到的东西」
    # 的通例：`write_audit` 要 `request` 才记得到 ip 与 user-agent，而服务层没有
    # `request`。所以这里把 `batch_no` 一起发出去，让路由拼得出那一行 `detail`。
    return {
        "row_id": row.id,
        "row_no": row.row_no,
        # 由调用方拼审计用（见上）。它也是界面上那一行要显示的东西——「你刚才处置的
        # 是 BATCH-20260919-1 的第 7 行」，而不是一个光秃秃的行 id。
        "batch_no": batch.batch_no,
        "resolution": row.resolution,
        "age_resolution": row.age_resolution,
        "conflict_resolution": row.conflict_resolution,
        "match_status": row.match_status,
    }


def commit_batch(
    db: Session,
    batch: AssessmentImportBatch,
    actor: UserAccount,
    resolution: str | None = None,
    age_resolution: str | None = None,
) -> dict[str, Any]:
    """把一批匹配好的行写成真实的测评记录，单事务。

    **任何未处理的冲突都不得进入正式结果**（§18.6 的原话）。所以有**三道门**：

    * 批次里还有 `MATCH_STATUSES_NEEDING_RESOLUTION` 的行、而这一次没有给
      `resolution` → 422，一句话说清有几条；
    * 给了 `resolution` 时，`skip` 让那些行一条都不写，`overwrite` 才写；
    * **`MATCH_CONFLICT` 那些行有自己的门**（第 6 期加，见 `_conflict_row_is_decided`）：
      它们必须逐行选过 §18.8 的四种处置之一，整批的 `resolution` 对这些行**无效**。
      没选就 422 并指名是哪几行——因为一条在线答卷被外部顶掉之后，`assessment_answer`
      上那份原始作答仍然在（一条都不删），但**「当前有效结果」换人了**，而那正是
      这名学生接下来在任何页面上的样子。

    评分与落库共用 `assessment_service.score_session`——导入的结果必须是**同一种形状**
    的事实（同一份答卷哈希、同一个 `calculation_status` 状态机、同一套开待办的判据），
    否则分析页与个案详情页会因为数据来源不同而各说各话。

    `resolution` 只在批次里真的有冲突时才是必需的。没有冲突的批次照旧一次提交，
    不必带它——要求 99% 的正常导入都先回答一个不该问的问题，只会让人乱点。

    **`age_resolution` 是第二个问题，与 `resolution` 并列而不是它的第三个值**
    （见 `AGE_RESOLUTION_KEEP` 那一段）：`resolution` 回答「冲突的那些行写不写」，
    这个问题回答「写下去的时候，名册上那个年龄动不动、这一场按哪个年龄记」。
    一份文件里「有 3 条年龄不符」与「有 2 条本月已导过」是两件互不相干的事，
    用一个选择同时回答两个问题就是 V1.0 的做法（选了「覆盖上次」顺带把年龄也改了）。

    取值优先级：**逐行处置 > 这一次的整批选择 > 沿用 V1.0 的老口径**。
    那个兜底只在 `resolution == overwrite` 时才会走到（选「放弃」的行在上面就
    跳过了），它保住的是既有调用方的行为：V1.0 的界面上只有「覆盖上次」一个按钮，
    而那一次点击的含义一直是「连年龄一起覆盖」。界面今天**永远显式传**这一项。
    """
    if batch.status == BATCH_STATUS_COMMITTED:
        # 不做静默幂等：这一批已经写完了，再提交一次能做的只有「重写一遍」，
        # 而重写会收回 PENDING 的风险待办、清掉人的复核（`_rewrite_imported_session`）。
        # 一个误点的按钮不该有那种后果，所以这里停住并说清出路。
        raise AppError(
            "VALIDATION_ERROR",
            "这一批已经提交过了（可能是在另一个窗口里）。要重新导入请重新上传文件。",
            422,
        )
    if resolution is not None and resolution not in RESOLUTIONS:
        raise AppError("VALIDATION_ERROR", RESOLUTION_HINT, 422)
    # 逐行处置也走这一套取值（`_parse_age_resolution` 是同一个判据的两个入口）
    if age_resolution is not None:
        age_resolution = parse_age_resolution(age_resolution)

    rows = list(
        db.scalars(
            select(AssessmentImportRow)
            .where(AssessmentImportRow.batch_id == batch.id)
            .order_by(AssessmentImportRow.row_no)
        )
    )
    if not rows:
        raise AppError("VALIDATION_ERROR", "这一批里没有可导入的记录", 422)

    # 逐行处置过的行不再要求整批选择（§18.6 的「逐行处理」）。判据里的
    # `row.resolution is None` 是第 5 期加的：在那之前 `row.resolution` 只由**本函数**
    # 在提交那一刻写，所以这一支与「全部待拍板行」逐字等价；现在它可能已经被
    # `resolve_import_row` 处置过，而再要求一次整批选择就等于把那个人的工作作废。
    undecided = [
        row
        for row in rows
        if row.match_status == MATCH_CONFLICT and not _conflict_row_is_decided(row)
    ]
    if undecided:
        # **指名是哪几行**：这条错误只有一种出路——回到界面上逐行选处置。一句
        # 「有 3 条需要确认」会让人不知道该点哪一行，而这一批可能有 200 行。
        # 行号照样截断（§10：凡是截断，都要自己说出来）。
        shown = "、".join(f"第 {row.row_no} 行" for row in undecided[:10])
        more = f"等 {len(undecided)} 行" if len(undecided) > 10 else ""
        raise AppError(
            "VALIDATION_ERROR",
            f"{shown}{more}与系统内已有的在线答卷冲突，需要逐行选择处置方式"
            "（保留在线 / 采用外部 / 否掉外部 / 两份都留但以在线为准）。"
            "整批的「覆盖」对这些行无效：一次点击不该作废几份学生本人作答的卷子。",
            422,
        )

    pending = [
        row
        for row in rows
        if row.match_status in MATCH_STATUSES_NEEDING_RESOLUTION
        and row.match_status != MATCH_CONFLICT  # 这一档归上面那道门，两道门各说各的
        and row.resolution is None
    ]
    if pending and resolution is None:
        raise AppError(
            "VALIDATION_ERROR",
            f"有 {len(pending)} 条记录需要确认（年龄与名册不符，或本月已有一次导入），"
            "请选择覆盖或放弃这些记录后重试",
            422,
        )
    # **先数再写**：下面的循环会替整批选择补写 `row.resolution`，之后这两者在那一列上
    # 就长得一样了。所以这个数必须**按 `resolved_by`** 数，不能按 `row.resolution
    # is not None`——那正是「这两者长得一样」那句话：一个跟着整批走的行，在循环跑完
    # 之后也有 `resolution` 了，按它数会把整批动作数成人逐行看过的（第 6 期修）。
    # `resolved_by` / `resolved_at` 只有 `resolve_import_row` 写，是这件事唯一的痕迹。
    #
    # 它也是**冲突行能被数进去的唯一途径**：冲突行的 `resolution` 至今仍是 None
    # （四档处置写在 `conflict_resolution` 上），按老判据它们一条都数不到。
    # 这个数进审计的 `detail`，回答「这一批里有几行是人逐条看过的」——一份 200 行的
    # 普查里，那 3 条年龄不符是逐条定的还是整批点的覆盖，是事后要能回答的问题。
    resolved_rows = sum(1 for row in rows if row.resolved_by is not None)

    school = db.get(School, batch.school_id) or school_for_import(db)
    scale = _published_scale(db)
    if school is None or scale is None:
        raise AppError("VALIDATION_ERROR", "缺少学校或已发布的 MHT 量表，无法导入", 422)
    questions = {
        question.question_no: question
        for question in db.scalars(
            select(ScaleQuestion).where(ScaleQuestion.scale_id == scale.id)
        ).all()
    }
    # `calculate_session` 也是按 question_no 取全套题目来配 `ScaleQuestionConfig` 的，
    # 题号不全就会在评分时静默少算几题——所以在写任何一行之前先挡下来
    if any(number not in questions for number in range(1, QUESTION_COUNT + 1)):
        raise AppError("VALIDATION_ERROR", "量表题目不完整，无法导入", 422)

    tested_on = batch.tested_at.date() if batch.tested_at else date.today()
    tested_at = datetime(tested_on.year, tested_on.month, tested_on.day)
    # **这一批当初绑没绑任务**是判重的分水岭（用户 2026-09-19 的裁决「两层都留」），
    # 而下面 `task` 会因为未绑定批次而临时建出一个来，所以这个事实要**先**记下来，
    # 不能到了判重那一步再问 `task is None`——那时它一定不是 None。
    bound_to_task = batch.task_id is not None
    task = db.get(AssessmentTask, batch.task_id) if bound_to_task else None

    # 「一条都不会写进去」时不建批次任务。全是冲突行、又选了「放弃」时（重新导了同一份
    # 文件、想想还是算了），建出来的会是一个目标行数为 0 的空任务：§12 的判据里
    # 「目标行全部完成且总数 > 0」不成立，于是它在任务列表上**永远显示「进行中」**
    # ——正是用户抱怨过的那一行。而且它会被同月的下一次导入复用，那个月的批次名字
    # 就定在这次什么都没导的尝试上（`_task_for_month` 复用时不改名）。
    writes = any(_row_will_be_written(row, resolution) for row in rows)
    if task is None and writes:
        # 任务名取**批次名称**，不是文件名（`batch_name` 这一列就是为这一处加的）：
        # 学校的导出文件叫 `结果(3).csv` 是常态，而这一行会出现在任务列表上给全校看。
        # 两个名字的对话者不同——批次名称是操作员在界面上填、经过确认的那一格，
        # 文件名是这份数据从哪个文件来。
        #
        # 空串在这里到不了（两个写入方 `start_assessment_import` /
        # `start_historical_import` 都先过 `_batch_errors` 要求它非空），所以不写兜底：
        # 一个兜底会让「这一列没接上」变成一句看起来正常的任务名，而那正是这次要修的东西。
        task = _task_for_month(
            db, school=school, scale=scale, batch_name=batch.batch_name, tested_on=tested_on, actor=actor
        )

    created = updated = skipped = age_updated = withdrawn = not_applied = 0
    for row in rows:
        if not _row_will_be_written(row, resolution):
            row.processing_status = ROW_PROCESSING_SKIPPED
            # 只有**可导入**的行被放弃时才补写 `resolution`：`NOT_FOUND` 那一类行
            # 本来就没被问过「写不写」（它们进不来），给它们写一个 `skip` 是替它编
            # 一个不存在的决定——而 `_conflict_row_is_decided` 正认这个值。
            if row.match_status in MATCH_STATUSES_NEEDING_RESOLUTION:
                row.resolution = RESOLUTION_SKIP
            skipped += 1
            continue
        if row.match_status in MATCH_STATUSES_NEEDING_RESOLUTION and row.match_status != MATCH_CONFLICT:
            # 冲突行**不在这里补写**：它们的问题由 `conflict_resolution` 回答，而那一列
            # 是 `resolve_import_row` 写的。在这里补一个 `overwrite` 上去，就等于让
            # 整批的选择顺手成了来源裁决——正是第 6 期要禁止的那一件事。
            row.resolution = RESOLUTION_OVERWRITE
        assert task is not None  # `writes` 刚判过「这一行会写」，任务必已建出

        student = db.get(Student, row.student_id)
        # 范围在**提交时再查一遍**。匹配时查过，但那可能与此刻不同：批次是共享的
        # （`imported_by` 只是创建者），而账号的范围可以被改。少了这一句，一个把范围
        # 收窄过的人点一下提交，就能把范围外的学生写进库。
        ensure_student_in_scope(db, actor, student.id)

        payload = _payload_of(db, row)
        age_choice = None
        if row.conflict_code == CONFLICT_AGE_MISMATCH:
            # 逐行处置过就用那一行的（`resolve_import_row` 写的），否则用这一次的整批
            # 选择，再否则沿用 V1.0 的老口径（见 docstring）。
            age_choice = row.age_resolution or age_resolution or AGE_RESOLUTION_OVERWRITE
            row.age_resolution = age_choice
            if age_choice == AGE_RESOLUTION_OVERWRITE:
                # `student.age` 是年龄唯一的存储处，改名册只有这一个入口。
                # 预览里已经把「名册 12、文件 13」写给人看过了。
                _update_roster_age(db, student, row.age_after, actor=actor, batch=batch, row=row)
                age_updated += 1

        sheet = {
            "student_id": student.id,
            # 汇总那一档没有 `answers` 这个键（见 `_payload_of`）——给它一个空列表而不是
            # 让它 KeyError：那一档 `_write_sheet` 一行答卷都不写，空列表是它的实话。
            "answers": payload.get("answers", []),
            "duration_seconds": payload.get("duration_seconds"),
            # 这一场按哪个年龄记：§18.5 三个选项里**只有「保留系统年龄」会让它与
            # 文件里的数不同**（取名册上那个），另两个都是文件里的数。没有年龄冲突的
            # 行永远是文件里的数（`age_before` 与 `age_after` 相同的情况除外，那种
            # 情况下本来也不会被判成冲突）。
            "age": age_for_session(row, age_choice),
            # 整份文件的形态（`_row_from_cells` 按列判出来的），会话的 `source_type`
            # 与结果的写入路径都按它分叉。
            "form": payload["form"],
        }

        # ── 来源处置（§18.8 的四种）────────────────────────────────────────
        #
        # 普通行（没有来源冲突）走的是 V1.0 那条老路：**有就覆盖、没有才新建**。
        # 判重口径与**匹配时**逐字一致（绑定任务看任务内、未绑定看自然月）：预览与
        # 提交之间可能又有人导了同一份文件，那时行上的 `match_status` 是旧的，
        # 而唯一约束不会通融。
        conflict_choice = row.conflict_resolution if row.match_status == MATCH_CONFLICT else None
        # 「现在算数的是哪一场」。只在有来源冲突时才查：普通行不存在「另一场」，
        # 多查一次就是每行多一条 SELECT。
        online = (
            _current_effective_session(db, student.id, task.id)
            if conflict_choice is not None
            else None
        )
        session: AssessmentSession | None = None
        # 没有来源冲突的行停在 `PENDING`（「上传了一份文件」不构成表态，见上面
        # `VERIFICATION_PENDING` 那一段）；有冲突的行由人选的这一档决定，取值来自
        # `CONFLICT_RESOLUTION_VERIFICATION` 那张表——**四档与三个取值之间的关系
        # 只写在那一个地方**，这里的三个分支一个都不再碰它。
        verification_status = (
            VERIFICATION_PENDING
            if conflict_choice is None
            else CONFLICT_RESOLUTION_VERIFICATION[conflict_choice]
        )

        if conflict_choice is None:
            session = (
                db.scalar(
                    select(AssessmentSession)
                    .where(
                        AssessmentSession.task_id == task.id,
                        AssessmentSession.student_id == student.id,
                    )
                    .order_by(AssessmentSession.id.desc())
                )
                if bound_to_task
                else existing_import_session(db, student.id, tested_on)
            )
            if session is None:
                session = _new_imported_session(
                    db,
                    sheet,
                    task=task,
                    scale=scale,
                    tested_at=tested_at,
                    school_id=student.school_id,
                )
                created += 1
                row.processing_status = ROW_PROCESSING_CREATED
            else:
                withdrawn += _rewrite_imported_session(
                    db, session, sheet, scale=scale, tested_at=tested_at, questions=questions
                )
                updated += 1
                row.processing_status = ROW_PROCESSING_UPDATED
        elif conflict_choice == CONFLICT_RESOLUTION_USE_EXTERNAL:
            # 先让在线那一场退位，**再**建新场，次序不能换：新场 `is_effective=1`，
            # 而 `uq_session_effective_task_student` 是生成列唯一键——旧的还没让开
            # 就插新的，flush 那一刻必撞 `IntegrityError`（生成列一非空就参与唯一性）。
            if online is not None:
                online.is_effective = False
                db.flush()
            session = _new_imported_session(
                db,
                sheet,
                task=task,
                scale=scale,
                tested_at=tested_at,
                school_id=student.school_id,
            )
            # 留痕：**旧行**指**新行**（模型上 `supersedes_session_id` 那段注释是权威）。
            # 新场的 id 这时已经有了（`_new_imported_session` 内部 flush 过）。
            #
            # 原始答卷**一条都不删**：在线那一场只是不再算数，学生答的每一题都还在
            # `assessment_answer` 上（§1 那条「人工复核不得修改原始答卷」的另一面）。
            if online is not None:
                online.supersedes_session_id = session.id
            created += 1
            row.processing_status = ROW_PROCESSING_CREATED
        elif conflict_choice == CONFLICT_RESOLUTION_KEEP_BOTH:
            # 两份都留，而**有效的仍然是在线那一场**。新场照建、照写答卷、照评分
            # ——它是一份完整的结果事实，只是 `is_effective=0`，所以它不出现在任何
            # 「按人取最近一场」的口径里（§11）。这样「这一场里有什么」有两种看法，
            # 而「他现在是什么状态」只有一个答案。
            session = _new_imported_session(
                db,
                sheet,
                task=task,
                scale=scale,
                tested_at=tested_at,
                school_id=student.school_id,
                is_effective=False,
            )
            created += 1
            row.processing_status = ROW_PROCESSING_CREATED
        else:
            # `KEEP_ONLINE` / `REJECT_EXTERNAL`：**不建外部会话**，外部那一份只留
            # 原始事实（`assessment_external_result` 在匹配成功时就建好了）。
            #
            # 这两档在建不建会话、谁有效上完全一样，唯一的差别是上面那个
            # `verification_status`（取自 `CONFLICT_RESOLUTION_VERIFICATION`）：
            # 「还没定」与「已经否了」是两件事。
            #
            # **那一列今天只有写入方、没有任何读者**（`grep verification_status`
            # 数得出来：两处写、一处 DDL 默认值、一个索引），所以「后者不该在下一次
            # 导入时又被问一遍」目前是一句**意图**而不是现状——重传同一个文件时
            # `_clear_batch_rows` 把这一批的行与外部结果全删了重建（批次被复用，
            # 行不复用），人逐行做过的处置跟着一起没。要让它成真得先有一个读者，
            # 而那要先决定「被否掉的那条外部结果，下一次上传时按什么口径出现」，
            # 见缺口 11。
            row.processing_status = ROW_PROCESSING_NOT_APPLIED
            not_applied += 1

        if session is not None:
            _write_sheet(db, session, sheet, questions=questions, tested_at=tested_at, student=student)
            row.session_id = session.id

        if conflict_choice is None or conflict_choice == CONFLICT_RESOLUTION_USE_EXTERNAL:
            effective_session = session
        else:
            # 其余三档都以**在线那一场**为准：`KEEP_BOTH` 时新场是 `is_effective=0` 的，
            # 它把两份事实都留下，但没有取代任何东西。
            effective_session = online

        # 目标行在这一刻才写：它要带上「当前有效结果在哪」，而那是上面刚定下来的。
        # （`_stamp_submission` 是按 (task_id, student_id) 找这一行的，而导入根本不走它
        # ——它会把 `completed_at` 写成「现在」，与 `submitted_at` 的测评日期打架，
        # 所以这里自己把三件事都写对。）
        _mark_target_completed(
            db,
            task,
            student,
            tested_at,
            effective_session_id=effective_session.id if effective_session else None,
            # 有效结果来自外部导入的那一场时才有值。四档里它非空恰好当且仅当
            # 「有效场是导入的」——`KEEP_ONLINE` / `REJECT_EXTERNAL` / `KEEP_BOTH`
            # 的有效场是学生自己答的那一场，而 `online` 不可能是导入的
            # （`MATCH_CONFLICT` 的前提就是「那一场不是 IMPORTED」）。
            effective_external_result_id=(
                row.external_result_record_id
                if effective_session is not None and effective_session.source == "IMPORTED"
                else None
            ),
        )
        # 外部结果的定性。`session=None` 在第一、四档是**正常情形而不是错误**
        # （见那个函数的 docstring），那时它的 `applied_session_id` 留空。
        _apply_external_result(
            db, row, session=session, verification_status=verification_status
        )

    batch.status = BATCH_STATUS_COMMITTED
    batch.resolution = resolution or "NONE"
    # 「这一批动过名册上的年龄没有」。写成**结果**（`age_updated > 0`）而不是**请求**
    # （`age_resolution == overwrite`）：逐行处置可以让个别行是覆盖、整批选择是别的，
    # 而这一列回答的是「这个库里的名册被改过吗」——与同一批的 `created_rows` /
    # `updated_rows` 同族，都是「这一批做了什么」。请求记在 `row.age_resolution` 与审计里。
    batch.allow_age_overwrite = age_updated > 0
    batch.created_rows = created
    batch.updated_rows = updated
    batch.skipped_rows = skipped
    db.flush()
    return {
        "batch_id": batch.id,
        "batch_no": batch.batch_no,
        # 一条都没写时是 None（见上面 `writes`）：调用方据此不指向任何任务
        "task_id": task.id if task else None,
        "task_no": task.task_no if task else "",
        "total": len(rows),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        # 第 6 期新加的一个数，而它在批次表上**没有对应的列**（那要一条 DDL，
        # 而这是「加一个数」不是「加一件事」）。它说的是「处置过了、但外部结果
        # 没有落成一场测评」——`KEEP_ONLINE` / `REJECT_EXTERNAL` 两档。
        #
        # **它填的是 `created + updated + skipped` 之外的那一块。** 少了它，那三个数
        # 加起来会小于总行数，而屏幕上没有任何东西解释差额去哪了——「静默丢数据」与
        # 「这里就只有这么多」在屏幕上一模一样（§10）。界面与审计据此把它单独列一格。
        "not_applied": not_applied,
        "age_updated": age_updated,
        # 这一次用的是哪个年龄处置（`NONE` = 没问到 / 这一批没有年龄冲突）。
        # 界面据此说「名册年龄改了多少条」，与 `resolution` 并列。
        "age_resolution": age_resolution or "NONE",
        # 提交之前就已经逐行处置过的行数（不是这一次动作的结果，是这一次动作面对的
        # 现状）。整批选择**不动**它：那两种处置在这一列上长得一样，区分它们的是
        # `resolved_by` / `resolved_at`。
        "resolved_rows": resolved_rows,
        # 覆盖时从这一场收回的、还没人处理过的风险待办（审计要能回答「那条待办去哪了」）
        "withdrawn_risk_events": withdrawn,
    }


def _payload_of(db: Session, row: AssessmentImportRow) -> dict[str, Any]:
    """这一行提交时要用到的那些（**行表上存不下**）。

    完整答案那一档取的是答案与用时，落在 `assessment_external_result.result_payload_json`
    里，见 `_external_result_for_row` 的 docstring。取不到就当场报错：提交一份没有答案的
    行会写出一场**零答卷**的会话，而那一场会在统计里与「学生交了白卷」长得一样
    ——那比报错糟得多。

    汇总那一档（§18.9）没有答案可读，它要的是**分数**，而分数在外部结果那两列上
    （不是 JSON 里——`total_score` 与 `dimension_scores_json` 是真正的列）。返回的形状
    与另一档一致，调用方不必再分一次叉。同样地，分数取不到就报错：一份「只有维度分
    没有总分」的文件在列级就被挡下来了，这一条是最后一道门。
    """
    if row.external_result_record_id is None:
        raise AppError(
            "VALIDATION_ERROR",
            f"第 {row.row_no} 行没有外部结果记录，无法导入。请重新上传这份文件再提交。",
            422,
        )
    result = db.get(AssessmentExternalResult, row.external_result_record_id)
    payload = result.result_payload_json if result else None
    if result is None or not payload:
        raise AppError(
            "VALIDATION_ERROR",
            f"第 {row.row_no} 行没有可导入的内容，无法导入。请重新上传这份文件再提交。",
            422,
        )
    # **按落库的那一列判，不按 payload 里的标记**：`source_type` 是权威（非空列、
    # 有 `server_default`），而 payload 里那份是写进去时的样子；两处都读会让
    # 「同一件事两个定义」在某一档上分岔。汇总那一档的 payload 里**刻意只有用时**
    # （见 `_external_result_for_row` 的最小化策略），本来也没有这个标记。
    if result.source_type == SOURCE_TYPE_SUMMARY:
        if result.total_score is None:
            raise AppError(
                "VALIDATION_ERROR",
                f"第 {row.row_no} 行没有分数，无法导入。请重新上传这份文件再提交。",
                422,
            )
        return {
            "form": SOURCE_TYPE_SUMMARY,
            "total_score": result.total_score,
            "dimension_scores": result.dimension_scores_json or {},
            "duration_seconds": payload.get("duration_seconds"),
        }
    if not payload.get("answers"):
        raise AppError(
            "VALIDATION_ERROR",
            f"第 {row.row_no} 行没有答案，无法导入。请重新上传这份文件再提交。",
            422,
        )
    # 完整答案那一档的载荷里**只有答案与用时**（`_external_result_for_row` 的最小化
    # 策略），所以 `form` 由这里按 `source_type` 补上——调用方两档看到同一个键。
    # 补而不是存：这一列回答的是「这份答卷的原样是什么」，而形态是这一行的属性，
    # 它有自己的一列（`assessment_external_result.source_type` 与
    # `assessment_session.source_type`），存两份就要回答「以哪一份为准」。
    return {**payload, "form": SOURCE_TYPE_FULL_ANSWER}


def _apply_external_result(
    db: Session,
    row: AssessmentImportRow,
    *,
    session: AssessmentSession | None,
    verification_status: str,
) -> None:
    """把这一行对应的外部结果**定性**，并（在它真的成了结果时）指到那一场会话上。

    `applied_session_id` 是「这一份外部结果**真的**成了哪一场测评」——`student_id`
    回答「它是谁的」，这一列回答「它进没进去」。§18.7 那句「任务外记录可以另存为
    学生历史外部测评，但不得自动变成本任务结果」靠的就是这两列的组合。

    **第 6 期起它是 §18.8 四档处置的落点之一**，而 `session=None` 是三种正常情形、
    不是错误：

    | 处置 | `session` | `verification_status` |
    |---|---|---|
    | `KEEP_ONLINE` | `None` | `PENDING`（这一份还挂着，以后能再裁） |
    | `REJECT_EXTERNAL` | `None` | `REJECTED` |
    | `USE_EXTERNAL` | 新建的那一场 | `ACCEPTED` |
    | `KEEP_BOTH_BUT_ONE_EFFECTIVE` | 新建的那一场 | `ACCEPTED` |

    后两档的差别不在这里，在 `is_effective`——**「这份数据我们认」与「这份数据算作
    当前结果」是两个问题**，前者归这一列，后者归那一列。

    `verification_status` 必填、无默认值，理由与 `_mark_target_completed` 那两个指针
    同：四档各写一个不同的值，漏想一次就会退回 `PENDING`——而 `PENDING` 恰好是
    「还没人裁过」，于是「否掉了这一份」会显示成「还在等决定」。
    """
    if row.external_result_record_id is None:
        return
    result = db.get(AssessmentExternalResult, row.external_result_record_id)
    if result is None:
        return
    result.verification_status = verification_status
    result.applied_session_id = session.id if session is not None else None


def list_import_batches(
    db: Session, *, school: School | None, limit: int = 20, offset: int = 0
) -> dict[str, Any]:
    """这所学校做过的导入批次，最近的在前。

    **不按创建者过滤**：批次是一所学校的工作记录，另一位心理老师需要看得见
    「这批数据是谁、什么时候导进来的」——那正是「这一批为什么长这样」的答案。
    但**逐行明细**是另一回事（里面有姓名与学号），那一层的门槛在 `list_import_rows`。
    """
    if school is None:
        return {"items": [], "total": 0}
    where = [AssessmentImportBatch.school_id == school.id]
    total = db.scalar(
        select(func.count()).select_from(AssessmentImportBatch).where(*where)
    )
    items = list(
        db.scalars(
            select(AssessmentImportBatch)
            .where(*where)
            .order_by(AssessmentImportBatch.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return {"items": items, "total": total or 0}


def _row_keyword_condition(keyword: str):
    """一行命中关键词的判据（明细页那个搜索框）。

    主体搜的是**文件里那一行写了什么**（`raw_*` / `normalized_*`）——操作员手上拿的
    是那份文件，他照文件里的字来找。两处例外，各有各的理由：

    * **学号也搜名册上那个值**（`Student.student_no`）：明细表那一格印的就是
      `matched_student_no`（见 `_row_payload`），而**文件模板里根本没有学号列**
      （六列：姓名 / 性别 / 年龄 / 年级 / 班级 / 所用时间），所以文件路径上
      `raw_student_no` 恒 NULL（`_match_row` 那一路只填 `raw_name` 那几列）——
      只搜 `raw_student_no` 的话，屏幕上明明写着「学号：S001」却搜不出来。
      `raw_student_no` 一并留着，它不是死代码：`start_historical_import`（按名册
      建批那条路）会把它填上，那一条的明细页上它在「文件原始内容」里有值。
    * **行号按精确值命中**：一个 400 行的批次里搜「7」时，`LIKE '%7%'` 会把
      7、17、70、107 全捞出来，而操作员输一个数字时想的几乎总是「第 7 行」。
      两者取并集，不互斥。

    **前提：这条判据只能用在带 `with_row_student` 的查询上**（`Student` 在连接里）。
    少了那个外连接时 SQLAlchemy 会把 `Student` 当成一个**独立的 FROM 元素**加进去，
    于是查询变成逐行 × 名册里每一个人的笛卡尔积——行数被乘出来、明细整片重复，
    而报出来的只是一条 SAWarning（同 `list_import_rows` 那段 docstring）。

    **不转义 `%` 与 `_`**：与 `api/v1/audit.py` 的 `q` 同一套写法，全站就这一种。
    为一个搜索框单独造一套转义规则，会让「这两个框为什么不一样」成为下一个问题。

    用 `isdecimal()` 而不是 `isdigit()`：后者对 `²` 也返回真，而 `int("²")` 抛
    `ValueError`——一行用户输入能把明细页变成 500，正是那种「只在有人手滑时出现」
    的缺陷。
    """
    like = f"%{keyword}%"
    conditions = [
        Student.student_no.like(like),
        AssessmentImportRow.raw_student_no.like(like),
        AssessmentImportRow.raw_name.like(like),
        AssessmentImportRow.normalized_name.like(like),
        AssessmentImportRow.raw_grade_name.like(like),
        AssessmentImportRow.raw_class_name.like(like),
    ]
    if keyword.isdecimal():
        conditions.append(AssessmentImportRow.row_no == int(keyword))
    return or_(*conditions)


def list_import_rows(
    db: Session,
    batch: AssessmentImportBatch,
    *,
    actor: UserAccount,
    match_group: str | None = None,
    keyword: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """一批里的逐行明细，**按读者的数据范围过滤**（§9）。

    批次是共享的（创建者不在过滤条件里，见 `list_import_batches`），而这一层逐行
    给出姓名、学号、年级班级——所以它必须自己带范围。判据只有一条：

    * 匹配到了学生的行：这名学生要在读者的范围内；
    * **没匹配到学生的行：显示。** 它们身上没有一个别人的学生——`raw_*` 是操作员
      自己填进文件里的字，`matched_*` 是空，候选按 `visible` 记（别人的班上的同名人
      根本不在里面，见 `match_student`）。所以这一支不需要再挡什么。

    **「把候选非空的行藏起来」曾经写在这里，第 4 期删掉了。** 它想挡的是枚举，实际
    造出的是更强的枚举：行在 = 那个班没这个人，行不在 = 有这个人，一次上传问遍全校。
    藏起来保不住任何东西（行里没有一条数据来自名册），而「没有谁消失」才是那句话
    成立的前提。

    默认上限 200 与全站其余几处一致（§10）：**凡是截断都要自己说出来**，
    调用方拿 `total` 与 `items` 比一下就知道有没有被截断。

    **`Student` 必须显式外连接进来。** `student_scope_predicate` 的谓词长在 `Student`
    的列上（`Student.school_id == …`），而这条查询的主表是 `assessment_import_row`
    ——把谓词直接塞进 `WHERE` 而不给 `Student` 一个连接条件时，SQLAlchemy 会把它
    当作一个**独立的 FROM 元素**加进去，于是查询变成 `assessment_import_row` 与
    `student` 的笛卡尔积：213 行 × 名册里符合条件的每一个人。行数被乘出来、
    逐行明细整片重复，而**报出来的只是 SQLAlchemy 的一条 SAWarning**——
    屏幕上「共 400 行」看起来完全正常。

    外连接（不是内连接）是因为「没匹配到学生」的行也要出现在明细里：那些行
    `student_id` 是 NULL，外连接之后 `Student` 那几列全是 NULL，谓词自然为假，
    于是落到 `or_` 的第二支。

    **`student_id.is_(None)` 这一支不依赖 `candidate_student_ids` 的存储形态**，
    这是有意的：`JSON` 列的 `None` 有几个地方能悄悄变样（见模型上
    `candidate_student_ids` 那段与 `0017_json_null_normalize`），而这一支的读者是
    「出错的那几行」，一旦它恒假，明细整片消失、同一屏上 `row_counts` 却说「有问题
    N 行」。判据只挂在 `student_id` 这个真正的标量列上，就没有这一层。

    **两个可选的筛选条件做在服务端，不是在客户端筛当前页**（2026-09-24 加）。
    这一页是服务端分页的（默认 50 条一页），客户端过滤只能筛出「这一页 50 条里符合
    条件的」，而操作员要回答的是「这份 400 行的文件里有问题的到底是谁」——在客户端
    筛，那个数会随着他点第几页而变，而他不会知道这件事。

    `match_group` 取 `MATCH_GROUPS` 的四个键（可导入 / 待确认 / 与在线答卷冲突 /
    无法导入），与 `batch_row_counts` 返回的四个数**同一张表**：筛选片上写着
    「待确认 3 条」而点进去 0 条这种对话因此构造上不可能出现（§11）。认不出的分组
    当场 422 而不是当成「不过滤」——静默地返回整批会让调用方以为筛过了。

    `keyword` 交给 `_row_keyword_condition`：搜的是**文件里那一行写了什么**（`raw_*` /
    `normalized_*`），外加学号（名册上那个值——文件模板里没有学号列）与行号的精确命中。

    `total` 与 `items` 共用同一个 `where`，所以这两条筛选**自动同时收窄两个数**
    （§10 那条「截断要自己说出来」靠的正是它们同源：筛完之后 `total` 说的是筛选后
    集合的大小，翻页因此始终一致）。
    """
    predicate = student_scope_predicate(db, actor)
    where = [
        AssessmentImportRow.batch_id == batch.id,
        or_(and_(AssessmentImportRow.student_id.isnot(None), predicate),
            AssessmentImportRow.student_id.is_(None)),
    ]
    if match_group is not None:
        statuses = MATCH_GROUPS.get(match_group)
        if statuses is None:
            raise AppError(
                "VALIDATION_ERROR",
                f"筛选条件不认得：{match_group}。"
                f"可选值：{' / '.join(MATCH_GROUPS)}",
                422,
            )
        where.append(AssessmentImportRow.match_status.in_(statuses))
    if keyword:
        where.append(_row_keyword_condition(keyword))
    total = db.scalar(
        with_row_student(select(func.count()).select_from(AssessmentImportRow)).where(*where)
    )
    items = list(
        db.scalars(
            with_row_student(select(AssessmentImportRow))
            .where(*where)
            .order_by(AssessmentImportRow.row_no)
            .limit(limit)
            .offset(offset)
        )
    )
    return {"items": items, "total": total or 0}


def list_unmatched_import_rows(
    db: Session,
    actor: UserAccount,
    task_id: int,
    *,
    limit: int = UNMATCHED_ROW_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """一场任务下**没进得去**的那些导入行，按原因分个数（§18.10 的未匹配口径）。

    这是 §18.4 那条出路的落点：学校上传一份文件、有几行没匹配上，那些行从此**有了
    一个可查的地方**——在它们所属的那场任务下面。在此之前它们只活在那一次上传的
    返回值里：操作员刷新一次页面，就再也说不出「上一批里那 5 行是谁、为什么没进来」，
    而「先补名册、再重新匹配」也就无从下手。

    判据两个，**取并集**：

    * `match_status` 在 `MATCH_STATUSES_UNIMPORTABLE` 里（`INVALID_ROW` / `NOT_FOUND` /
      `OUT_OF_SCOPE` / `AMBIGUOUS`）——**任何处置都救不回来的那些**；
    * 或者 `processing_status == ROW_PROCESSING_SKIPPED`——提交时被放弃了的那一行
      （整批选了「放弃」、或逐行处置成 `skip`）。它匹配得上，而这一批**没有**把它写进去，
      所以对这场任务而言它和「没匹配上」是同一件事：这个学生的这一场缺着。

    第二个判据是有意的：少了它，一个「选了放弃」的批次在这一页上会一条都不剩——
    而那一页的读者正是要找出「这场任务还有谁缺着」。

    **是「并入」而不是「分成两档」**：`reason_counts` 按 `match_status` 分，所以
    「哪些行是没能匹配、哪些是被放弃的」仍然分得开（`processing_status` 也在逐行的
    响应里）。把两者排成两组会让同一份行表在界面上出现两次。

    范围过滤走 `with_row_student` + `student_scope_predicate`，与逐行明细**同一处**
    逻辑（同一个函数、同一个外连接）：这一页给出姓名与班级，而它同时是任务内部的——
    所以与 `GET /assessment-tasks/{id}/targets` 是同一个形状的两道门槛（§22）：
    **任务读者 + 「组织与账号」的读权限**，理由逐字相同（这一页逐行印出学号与姓名，
    那是组织与账号那一档的数据）。判据直接复用 `ensure_task_target_reader`
    ——两处各写一份 `scope_allows(...)` 就会有第三个定义悄悄冒出来。
    """
    ensure_task_target_reader(db, actor)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    # 计数与取数**逐字相同**（同一组判据、同一个连接），所以上面那一对抽在函数里。
    # 计数那一条要从 `select_from` 重新出发——`select(AssessmentImportRow)` 数的是行，
    # 而这两句话数出来的东西必须一模一样，否则「共 N 条」与下面那张表各说各话（§11）。
    conditions = _unmatched_row_conditions(task.id, predicate=student_scope_predicate(db, actor))
    total = db.scalar(
        _unmatched_rows_statement(select(func.count()).select_from(AssessmentImportRow)).where(
            *conditions
        )
    )
    items = list(
        db.scalars(
            _unmatched_rows_statement(select(AssessmentImportRow))
            .where(*conditions)
            # 批次从新到旧、批内按行号：最近一次上传的那几行在最上面，而它们正是
            # 操作员此刻在找的（「刚才那 5 行是谁」）。行号在批内有序，于是同一批里
            # 的先后与文件一致。
            .order_by(AssessmentImportRow.batch_id.desc(), AssessmentImportRow.row_no)
            .limit(limit)
            .offset(offset)
        )
    )
    return {
        "items": items,
        "total": total or 0,
        # 按原因分个数（整场任务，不套读者的范围，理由见那个函数）。它与上面
        # `total` 的口径**故意不同**，所以界面要把两个数分开写、各自标明口径（§9）。
        "reason_counts": unmatched_reason_counts(db, task.id),
    }


# 「未匹配行清单」的列白名单，**服务端**定义（§16.3：导出接口不得接受任意字段名）。
# 逐字照「未匹配行」页签的 `<thead>`（`TasksPage.vue` 那个块）——同一条记录在屏幕上与
# 文件里必须逐字相同，多一列少一列都会让拿到文件的人问「这一列在界面上是哪一列」。
UNMATCHED_ROW_COLUMNS: tuple[str, ...] = (
    "批次",
    "行号",
    "文件里的姓名",
    "年级",
    "班级",
    "匹配结论",
    "说明",
)


def unmatched_rows_csv(db: Session, actor: UserAccount, task_id: int) -> ExportDocument:
    """这场任务下**没进得去**的那些导入行，连同它用了哪些列一起返回（§20#14 的导出那一半）。

    与 `list_unmatched_import_rows` 是**同一批行的两种出口**：那边给人看（分页、封顶），
    这边给文件（全量）。判据逐字共用 `_unmatched_row_conditions` —— 「哪些行没进得去」
    只许有一个定义，两处各写一份字面量就会在某次改动之后各说各话，而两份看起来都对。

    **刻意不带 `UNMATCHED_ROW_LIMIT`。** 那 200 是给眼睛的（`list_unmatched_import_rows`
    的 `limit`）：屏幕上翻页有出路，而一份文件截断了没有任何东西看得出来——收到的人会
    以为「这场任务就只有这 200 行没进来」。屏幕上此刻正写着「整场共 N 行没进得去」，
    而那 N 是整场地数出来的（`unmatched_reason_counts`）。§10 那句「**截断只许影响
    「显示」，绝不许影响「写入」**」在这里就是这一条：文件是这一份清单的落点。
    所以这个方法**没有 `limit` 参数**——不是忘了传，是它不该有。

    三个门槛与未参与名单导出**逐字同一对**（`ensure_task_target_reader` +
    `ensure_detail_exporter`，理由写在后者那里）：这一页逐行印出姓名（组织与账号那一档），
    而「匹配上了但被放弃」的那些行说的正是「这名学生的这一场缺着」——那是关于一名学生
    心理工作的事实。

    **空值留空，不写屏幕上的 `—`**。界面用 `-`／`—` 占位是因为空格子在表格里看不出
    「这里没有值」还是「渲染坏了」；而 CSV 里那一行 `INVALID_ROW` 的空姓名**正是它被判
    无效的原因**，写一个 `—` 上去就把这条信息盖掉了。§8 那条「`—` 是界面占位符，写进
    CSV 会让整列被当成文本」是同一个口径。
    """
    # 两道门槛：任务读者（他能不能读这场测评）+ 组织与账号（这一页逐行给姓名）。
    ensure_task_target_reader(db, actor)
    # 再来两道：导出许可 + 心理详情明细。与未参与名单导出同一对判据、同一个函数。
    ensure_detail_exporter(db, actor)
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    rows = list(
        db.scalars(
            _unmatched_rows_statement(select(AssessmentImportRow))
            .where(
                *_unmatched_row_conditions(task.id, predicate=student_scope_predicate(db, actor))
            )
            # 与列表端点逐字同一个次序（批次从新到旧、批内按行号）：同一批行的两种出口
            # 排成两种次序，拿到文件的人与看着屏幕的人会报出两个「第一行」。
            .order_by(AssessmentImportRow.batch_id.desc(), AssessmentImportRow.row_no)
        )
    )
    batches = {
        batch.id: batch.batch_no
        for batch in db.scalars(
            select(AssessmentImportBatch).where(
                AssessmentImportBatch.id.in_({row.batch_id for row in rows})
            )
        )
    }
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(UNMATCHED_ROW_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                batches.get(row.batch_id, ""),
                row.row_no,
                row.raw_name or "",
                row.raw_grade_name or "",
                row.raw_class_name or "",
                # **不是** `match_status_label`：这一屏的 `MATCHED` 读作「已匹配但被放弃」
                # （见 `export_labels.UNMATCHED_REASON_LABELS` 那段）。照原表渲染的话，
                # 一份叫「没进得去」的文件里会出现「已匹配」——两句话各自都对，摆在一起
                # 就成了自相矛盾。与界面用的是同一张表、同一个函数。
                unmatched_reason_label(row.match_status),
                # 服务端早就拼好的那一句（`_row_message`），与屏幕上「说明」列同一句话。
                # 不在这里另拼一份：那是「这一行为什么进不去」的第二个定义。
                row.message or "",
            ]
        )
    # BOM 与其余几份导出同一个理由：给中文 Excel / WPS 打开（不带 BOM 时它们按
    # locale 猜编码，中文会乱）。
    return ExportDocument(
        csv_text="﻿" + output.getvalue(),
        columns=UNMATCHED_ROW_COLUMNS,
        row_count=len(rows),
    )


def unmatched_reason_counts(db: Session, task_id: int) -> dict[str, int]:
    """「没进得去」的那些行按 `match_status` 各有多少条（§18.10）。

    **与 `list_unmatched_import_rows` 共用同一组判据**（`_unmatched_row_conditions`）：
    这一页会同时写出「未匹配 N 行」与那一列按原因的明细，两处各写一份字面量时，
    它们会在某次改动之后各说各话——而**两边看起来都对**（指标卡与它点进去的那个列表
    必须同源，§11）。这与 `batch_row_counts` 那条「两个读者共用一个集合」是同一件事。

    **不套读者的数据范围**（与 `list_import_rows` 刻意相反，同 `batch_row_counts`）：
    这个数坐在任务详情页上，说「这场任务有多少行没进来」，而任务本身是有范围归属的
    ——范围已经在「哪些任务看得到」那一层生效了，再套一层会让同一个任务对两位老师
    报出两个「未匹配 N 行」。逐行明细那一层仍然过滤（那里逐行给姓名）。
    """
    rows = db.execute(
        select(AssessmentImportRow.match_status, func.count())
        .select_from(AssessmentImportRow)
        .join(AssessmentImportBatch, AssessmentImportBatch.id == AssessmentImportRow.batch_id)
        .where(*_unmatched_row_conditions(task_id))
        .group_by(AssessmentImportRow.match_status)
    ).all()
    return {status: count for status, count in rows}


def _unmatched_row_conditions(task_id: int, *, predicate=None) -> list:
    """「没进得去」的判据，**一处定义、四个调用点**。

    | 调用点 | `predicate` |
    |---|---|
    | 逐行明细的计数 | 传（这一页逐行给姓名，必须带范围） |
    | 逐行明细的取数 | 同上（**必须与上一行逐字相同**，否则「共 N 条」与下表各说各话） |
    | `unmatched_rows_csv`（导出） | 同上（同一个读者范围，同一批行） |
    | `unmatched_reason_counts` | **不传**（它数的是整场任务，理由见那个函数） |

    一份判据两处写法，就会让「未匹配 N 行」与它下面的明细数出两个集合——而两边
    看起来都对。加导出那一个调用点（2026-09-20）时最容易犯的错是**只搬一半**：
    屏幕上按范围过滤、文件里不过滤，于是同一个老师导出的文件比他能看到的行还多
    ——那份文件里的每一行他都在界面上「看不见」，而它确实是他的权限导出来的。

    `predicate` 那一支与 `list_import_rows` 逐字相同（含 `student_id IS NULL` 的
    第二支）：没匹配到学生的行**照旧显示**，它们身上没有别人的数据（`raw_*` 是操作员
    自己填进文件里的字），而把它们藏起来会造出更强的枚举——行在 = 那个班没这个人。
    """
    conditions = [
        AssessmentImportBatch.task_id == task_id,
        or_(
            AssessmentImportRow.match_status.in_(sorted(MATCH_STATUSES_UNIMPORTABLE)),
            AssessmentImportRow.processing_status == ROW_PROCESSING_SKIPPED,
        ),
    ]
    if predicate is not None:
        conditions.append(
            or_(
                and_(AssessmentImportRow.student_id.isnot(None), predicate),
                AssessmentImportRow.student_id.is_(None),
            )
        )
    return conditions


def _unmatched_rows_statement(statement):
    """给一条 `assessment_import_row` 的查询接上批次与学生（内连接批次、外连接学生）。

    内连接批次（不是外连接）：`assessment_import_row.batch_id` 是 NOT NULL 且有外键，
    每一行都属于某一批——所以内连接不会丢掉任何一行，而它让 `task_id` 这个判据
    有地方落。
    """
    return with_row_student(statement).join(
        AssessmentImportBatch, AssessmentImportBatch.id == AssessmentImportRow.batch_id
    )


def with_row_student(statement):
    """给一条 `assessment_import_row` 的查询接上 `Student`（外连接）。

    单拎出来是因为**它的调用点必须两两逐字相同**：`list_import_rows` 的计数与取数、
    `list_unmatched_import_rows` 的计数与取数，以及 `unmatched_reason_counts`。
    两处写成两种连接方式时，`total` 与 `items` 会数出两个集合——而那一页的「共 N 条」
    正是拿 `total` 写的。
    """
    return statement.join(Student, Student.id == AssessmentImportRow.student_id, isouter=True)


def _next_batch_no(db: Session, tested_on: date) -> str:
    """`BATCH-20260919-1`：编号按**天**，不按年。

    与任务的 `IMPORT-202609-1`（按**月**，因为一场任务就是一个月）刻意不同：
    批次是「这一次上传」，一天可以有好几批（初一一份、初二一份），而它们在界面上
    是一条条独立的历史记录。同一天重传同一个文件**不会**多出一个号——那一次会被
    `_reusable_batch` 复用掉，号不变。
    """
    prefix = f"{BATCH_NO_PREFIX}-{tested_on:%Y%m%d}"
    count = db.scalar(
        select(func.count())
        .select_from(AssessmentImportBatch)
        .where(AssessmentImportBatch.batch_no.like(f"{prefix}-%"))
    )
    return f"{prefix}-{(count or 0) + 1}"


def _task_for_month(
    db: Session,
    *,
    school: School,
    scale: AssessmentScale,
    batch_name: str,
    tested_on: date,
    actor: UserAccount,
) -> AssessmentTask:
    """本月那一场外部导入的任务，没有就建一个，有就复用它。

    「不同月份的评测视为不同的测试任务」（2026-09-17 用户要求）的落点就在这里：
    **同一个月里分几次导（初一一份、初二一份，或者改完文件重导）都归到同一行下面**，
    换个月才是另一场。此前每提交一次就新建一个任务，于是同一个上午导两遍会得到
    `IMPORT-20260917-1` 与 `IMPORT-20260917-2` 两行，完成率各算一半，
    同一次普查在列表上看起来像两场。

    复用时**不改名字**：任务名是学校在第一次导入时确认过的那个标签，第二份文件的批次名
    只是那一次的备注。要改名走任务的编辑接口（心理老师），不在这里悄悄覆盖。

    查找按 `start_at`（= 测评日期）落在本月来做，而不是按 `task_no` 的形状——
    `IMPORT-20260916-1` 这种按天编号的旧任务同样属于九月，它也该被复用。

    **同月有多行时取最新的那一行（`id.desc()`），必须与 `existing_import_session`
    指向同一个月份批次。** 2026-09-17 之前这里是 `order_by(id)`（取**最老**的），
    而判重那边取的是**最新**的会话（`:728` `AssessmentSession.id.desc()`）——
    按天编号还留着旧任务时两处就分岔了：用户库里九月有 `IMPORT-20260916-1` 与
    `IMPORT-20260917-1` 两行，于是那次导入的提示与审计都写 09-16 那一批，
    **而真正被就地改写的会话属于 09-17 那一批**。批次号、审计的 `resource_id`、
    被改写的会话三者说的必须是同一件事，不然轨迹答不上「这次导入动了什么」。
    取最新之后，列表上的名字也是用户最近一次导入确认过的那个。
    """
    start, end = month_bounds(tested_on)
    existing = db.scalar(
        select(AssessmentTask)
        .where(
            AssessmentTask.school_id == school.id,
            AssessmentTask.source == "IMPORTED",
            AssessmentTask.start_at >= start,
            AssessmentTask.start_at < end,
        )
        .order_by(AssessmentTask.id.desc())
    )
    if existing:
        return existing

    task = AssessmentTask(
        task_no=_next_task_no(db, tested_on),
        name=batch_name,
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        # 批次是一次已经发生过的测评：开始时间就是测评日期，**不给截止时间**——
        # 它不是一份等学生来答的任务，写一个已过去的截止日期只会让列表上多出一个
        # 永远不会被完成的「逾期」窗口。
        #
        # `status="ACTIVE"` 是「没有人工裁决」的意思，不是「进行中」：列表上的状态由
        # `task_service.effective_task_status` 现算，这批记录因为目标行全部完成而显示
        # 「已结束」（2026-09-17 起。在此之前它永远显示「进行中」——3/3、100%、
        # 进行中，用户报的就是这个）。这一列现在只承载人写进去的 DRAFT/PAUSED/CLOSED。
        start_at=datetime(tested_on.year, tested_on.month, tested_on.day),
        end_at=None,
        status="ACTIVE",
        created_by=actor.id,
        source="IMPORTED",
    )
    db.add(task)
    db.flush()
    return task


def _mark_target_completed(
    db: Session,
    task: AssessmentTask,
    student: Student,
    tested_at: datetime,
    *,
    effective_session_id: int | None,
    effective_external_result_id: int | None,
) -> None:
    """收下一个学生这一次的目标行，并写下它的**有效结果指针**。

    收的是 `student` 对象而不是 `student_id`：七个快照列都要从学生身上取
    （`school_id_snapshot` 还没有默认值，不写就插不进那一行），而拿一个 `int`
    进来就得再查一次库，或者把调用方手里那个对象丢掉。

    快照走 `target_snapshot`——与建任务、补发是**同一个定义**。这一条路径上的快照
    尤其重要：导入的是一场**外部**普查，学生在这之后转班、改名都不会有一条校内
    发放记录来对照，只有这一行快照能回答「那次普查当时，这个人是谁」。

    **`effective_session_id` / `effective_external_result_id` 是必填的、没有默认值。**
    这一列回答「这名学生在这场任务里的当前结果在哪」，而 §18.8 那四档处置各指不同的
    场次：以在线为准时指学生自己答的那一场、以外部为准时指刚建的这一场、两份都留时
    仍然指在线那一场。给一个 `= None` 的默认值，就等于给「调用方忘了想这件事」留了
    一条静默的出路——那一行的指针会空着，而它长得与「还没人写过」一模一样。
    """

    # 指针先算好，下面两条出路各自写同一份（新增与既有行都要写：第一次导入建出这一行
    # 时它是空的，而重新导入同一份文件会把它改指到新场次上）。
    def _apply(target: AssessmentTarget) -> None:
        target.effective_session_id = effective_session_id
        target.effective_external_result_id = effective_external_result_id

    target = db.scalar(
        select(AssessmentTarget).where(
            AssessmentTarget.task_id == task.id, AssessmentTarget.student_id == student.id
        )
    )
    if target is None:
        target = AssessmentTarget(
            task_id=task.id,
            student_id=student.id,
            status="COMPLETED",
            completed_at=tested_at,
            **target_snapshot(student),
        )
        _apply(target)
        db.add(target)
        return
    # 覆盖时这一行本来就在（第一次导入建的），只需把完成时刻跟到新文件那一天
    target.status = "COMPLETED"
    target.completed_at = tested_at
    _apply(target)


def _new_imported_session(
    db: Session,
    row: dict[str, Any],
    *,
    task: AssessmentTask,
    scale: AssessmentScale,
    tested_at: datetime,
    school_id: int,
    is_effective: bool = True,
) -> AssessmentSession:
    # 这一场是这名学生在这场比赛里的第几次作答。**这一列到今天为止只有
    # `server_default` 的 1**——全库唯一的值，因为在此之前没有任何一条路径会在
    # 同一场比赛里给同一个人建第二场会话。§18.8 的 `USE_EXTERNAL` 与
    # `KEEP_BOTH_BUT_ONE_EFFECTIVE` 恰恰就是那第一、第二条：在线的那一场不删，
    # 外部这一份新建。不在这里算的话，两档都会撞 `uq_session_task_student_attempt`
    # （`(task_id, student_id, attempt_no)`），用户拿到的是一句英文的
    # `Duplicate entry`，而屏幕上他刚点的是「采用外部」。
    #
    # 取 `max + 1` 而不是「条数 + 1」：唯一键管的是那个值本身，不重复才是判据。
    # 条数在 `attempt_no` 之间有空号时会撞——今天没有删除路径，但 `attempt_no`
    # 一旦被别处写成 2、3，条数就与最大值脱节了，而那时撞的是同一个唯一键。
    #
    # 并发（两个批次同时提交、落在同一名学生身上）没有在这里上锁：与
    # `open_or_reuse_care_case` 那处是同一种洞，兜底的也是同一个东西——数据库
    # 的唯一键。它现在会响（这一次是真正的 500），见 CLAUDE.md 缺口 9。
    #
    # **2026-09-19（阶段 7）：同族的那一处已经修了，这一处仍然没修，是有意的。**
    # `assessment_service.open_or_reuse_care_case` 现在走 savepoint + 捕获
    # `IntegrityError` + 重查——它处理的恰是「两个请求同时发现没有、于是都插」，
    # 而重查之后**拿到的是对方插的那一条**，正是调用方想要的东西，所以那个失败
    # 可以被改写成成功。这里不行：撞上 `uq_session_task_student_attempt` 意味着
    # 同一名学生在这一场里已经有第 `max+1` 次作答了，而**那一条是别人要的**，
    # 本行要表达的「这是另一份独立的答卷（`USE_EXTERNAL` / `KEEP_BOTH`）」
    # 在重试之后仍然无处安放——要么给一个更大的 `attempt_no`（那是猜），要么把
    # 这一次导入判成失败。所以它留在缺口 9 里，等一个真的会发生的场景。
    max_attempt = db.scalar(
        select(func.max(AssessmentSession.attempt_no)).where(
            AssessmentSession.task_id == task.id,
            AssessmentSession.student_id == row["student_id"],
        )
    )
    session = AssessmentSession(
        task_id=task.id,
        student_id=row["student_id"],
        attempt_no=(max_attempt or 0) + 1,
        scale_id=scale.id,
        scale_version=scale.version,
        started_at=tested_at,
        submitted_at=tested_at,
        # 「这一场测的是哪一天、这个日期是从哪来的」。两个字段一起写才是完整的一句：
        # 导入的 `submitted_at` 本就是文件里的测评日期，而它与「学生在线交卷的那一刻」
        # 在排序、用时、趋势上都**长得一样**（`latest_session_order` 只看 `submitted_at`）。
        # 所以来源要单独记一列，否则一条外部记录与一条校内记录在这一层无法区分。
        tested_at=tested_at,
        tested_at_source=TESTED_AT_SOURCE_IMPORT_FILE,
        duration_seconds=row.get("duration_seconds"),
        # 「这一场按哪个年龄理解他」——`student.age` 是「这孩子现在多大」，
        # `age_at_test` 是**这一场的事实**。
        #
        # 它取的是 `row["age"]`，而那个值在 `commit_batch` 里由**年龄处置**决定
        # （§18.5 的三个选项）：没有年龄冲突时它一直是文件里的年龄；有冲突时
        # 「保留系统年龄」这一档给的就是名册上那个数（2026-09-19 用户裁决：
        # 让「无论是否覆盖都必须保存外部年龄」那一句让步——**文件里那个数一个字都没丢**，
        # 它在 `assessment_import_row.raw_age` / `age_after` 上，逐行明细里看得见）。
        age_at_test=row.get("age"),
        status="IN_PROGRESS",
        source="IMPORTED",
        # 这份答卷是怎么进来的（`ONLINE` / `EXTERNAL_FULL_ANSWER` / `EXTERNAL_SUMMARY`）。
        # 这是这一列的第一个写入方：在它之前，导入的会话一律顶着 `server_default`
        # 的 `ONLINE`——一条校外平台导进来的记录，在库里的说法是「学生在线答的」。
        # 它与 `source`（`IN_SYSTEM` / `IMPORTED`）是两个粒度，见模型上那段注释。
        # 直接取 `row["form"]` 而不是兜底一个默认值：这个键一定在（`_payload_of`
        # 两档都带它），少一个键时我宁可 KeyError 在测试里响，也不要一条静默的 ONLINE。
        source_type=row["form"],
        # `school_id` 没有默认值（V1.2 那个 NOT NULL 列），由调用方从**学生**身上取了
        # 传进来，不从 `task.school_id` 取：这一列回答的是「这份卷子属于哪所学校」，
        # 与 `(school_id, student_id) → student (school_id, id)` 那对复合外键同源；
        # 而 `task` 是这一批导入的批次任务，它的 `school_id` 是「这次普查挂在哪所学校
        # 名下」——批次的范围按文件走，与逐行的学生不是同一件事。
        school_id=school_id,
        # 「这一场算不算当前有效结果」（§18.8）。默认 `True` 是**普通导入行的全部情形**，
        # 也是这一列在 V1.2 对齐阶段写下的 DDL 默认值——所以这个参数唯一的用处是
        # `KEEP_BOTH_BUT_ONE_EFFECTIVE` 那一档：外部会话照建、照写答卷、照评分
        # （两份都留），只是**不算作当前状态**（`is_effective=0`）。
        #
        # 它有默认值而上面那两个指针没有，是有意的：这一列在四档里只有一档是 False，
        # 而「这一场的结果指向哪」在四档里**每档都不同**——前者漏想会退回一个正确的
        # 常见值，后者漏想会留下一个说不出来的空指针。
        is_effective=is_effective,
    )
    db.add(session)
    db.flush()
    return session


def _write_sheet(
    db: Session,
    session: AssessmentSession,
    row: dict[str, Any],
    *,
    questions,
    tested_at: datetime,
    student: Student,
) -> None:
    """把这份文件写进这一场会话：答卷 → 评分 → 维度 → 判定 → 定状态。

    新建与覆盖走的是**同一段**代码，区别只在于调用它之前把旧的那些行清掉了
    （`_rewrite_imported_session`）。一份记录「是怎么算出来的」不该因为它是第一次
    导入还是第八次重导而不同。

    **汇总那一档（§18.9）走到这里就结束**——它一行答卷都不写，也不评分、不开待办。
    这不是「还没做」，是规格逐字要求的：`没有 100 道原始答案时**不得伪造** assessment_answer，
    也不得**直接**按本地重点题规则生成风险事件`。汇总文件里没有答案，所以本地那条
    重点题判据（第 85 / 97 题答「是」）**用不了**，而系统不该假装自己判得出来。
    平台给的分数留在 `assessment_external_result.total_score` / `dimension_scores_json`
    上，核验状态是 `PENDING`——「有效结果」那一层归阶段 6（§18.8 的四种处置），
    在它接上之前，这一场在按 `assessment_result` 说话的那些页面（关注等级、关注率、
    受控导出）上是**空的**，而任务完成率按目标行算、会把它算成已完成。
    这个不一致是**已知且接受**的：它是「不许替未核验的外部结果担保」的代价，
    阶段 6 接上有效结果判定之后它自己会消失（CLAUDE.md 缺口 10）。
    """
    if row.get("form") == SOURCE_TYPE_SUMMARY:
        return
    answers = row["answers"]
    db.add_all(
        [
            AssessmentAnswer(
                session_id=session.id,
                question_id=questions[offset + 1].id,
                answer=answer,
                score=1 if answer == "YES" else 0,
                # 逐题作答时刻只有**天**的精度：外部文件给的就是「哪一天」和
                # 「总共用了多久」。把第 1 题倒推成「提交前 5340 秒」能让
                # `session_duration_seconds()` 回算出好看的用时，代价是把一个
                # 具体的假时刻写进原始事实层（CLAUDE.md §1）——而用时本身是文件里
                # 的记录值，已经原样存进 `duration_seconds`，不需要回算。
                answered_at=tested_at,
            )
            for offset, answer in enumerate(answers)
        ]
    )
    db.flush()
    # 与 `submit_session` 走**同一个** `score_session`：一份答卷怎么变成结果只有一种写法
    # （答卷哈希、`calculation_status` 的状态机、开待办的判据都在那里面）。
    #
    # 但它与在线路径有一处**刻意的不同**：算不出来时整批导入失败。这里是一次性的批量
    # 作业——一份文件要么整份进库、要么一条都不进，而「一半进了一半没进」是学校核对
    # 不了的状态（预览里说的「将导入 213 条」与实际落库的行数对不上，而屏幕上没有任何
    # 东西说得清少的是哪几条）。所以把返回值翻回一个异常，让路由那唯一的 commit 不发生，
    # 同时把原因与那一行的人报给操作员——原文在 `session.calculation_error` 里，
    # 它进的是库、显示给人看，不是日志。
    #
    # 时间口径要说清：风险事件的 `created_at` 与档案的 `opened_at` 取的是**导入时刻**
    # （`now_utc_naive()`），不是 `tested_at`。测评发生在去年，但档案是学校今天才看到、
    # 今天才开的——这两个时间回答的是不同的问题，把它们统一成测评日期等于倒填台账。
    if not score_session(
        db, session, {offset + 1: answer for offset, answer in enumerate(answers)}
    ):
        raise AppError(
            "VALIDATION_ERROR",
            f"{student.student_no} 这名学生的答卷算不出来：{session.calculation_error}。"
            "整批没有导入，请把这份文件与这一句话交给技术人员。",
            422,
        )


def _rewrite_imported_session(
    db: Session,
    session: AssessmentSession,
    row: dict[str, Any],
    *,
    scale: AssessmentScale,
    tested_at: datetime,
    questions,
) -> int:
    """覆盖上次：把这一场已有的导入记录按新文件重写一遍，返回收回的待办条数。

    **就地改，不删了重建。** `risk_event.session_id`、`retest_plan.source_session_id`
    都外键指向这一行，而全库没有任何 `ondelete=`，父行先删在 MySQL 上是 1451；
    更要紧的是那两处背后是人的工作——一条待办和一份复测计划，不该因为重导一份文件
    而消失。会话 id 不变，分析、个案详情、导出自动看到新的那一份。

    会话自身的时间戳一并跟到新文件：同一个月里两次导入的测评日期可以不同
    （9 月 16 日那批与 9 月 17 日那一批），而「记录取自哪一场」是按 `submitted_at`
    排序的（CLAUDE.md §11），不跟着改会让新记录排到旧记录的后面去。
    """
    db.execute(delete(AssessmentAnswer).where(AssessmentAnswer.session_id == session.id))
    db.execute(delete(DimensionResult).where(DimensionResult.session_id == session.id))
    # 结果行是 `unique(session_id)`，八维度是 `unique(session_id, dimension_code)`：
    # 不清掉的话重算会直接撞唯一约束
    db.execute(delete(AssessmentResult).where(AssessmentResult.session_id == session.id))
    withdrawn = _withdraw_pending_risk_events(db, session)

    session.scale_version = scale.version
    session.started_at = tested_at
    session.submitted_at = tested_at
    # 与新建那一处同步：重导之后这一场测的还是文件里那一天（可以与前一次不同——
    # 同一个月里两批文件的测评日期本来就是两天），来源也仍然是文件。
    session.tested_at = tested_at
    session.tested_at_source = TESTED_AT_SOURCE_IMPORT_FILE
    session.duration_seconds = row.get("duration_seconds")
    # 与新建那一处同步（§18.5）：测的那一天他多大是**这一次测评**的属性，
    # 覆盖重导之后它跟着新文件走。漏了这一句时它停在第一次导入时的数，而
    # 那一行看起来完全正常——「同一次测评两个年龄」这种不一致要等对账才发现。
    session.age_at_test = row.get("age")
    # 与新建那一处同步：这一份是从哪个入口进来的。它是会话自身的属性，重导一份
    # 汇总文件不会让它变成完整答案那一档——**但同一场会话被两档文件先后导入**是
    # 真实存在的（先导了汇总、后来拿到平台的原始答案再导一次），那时它必须跟着新文件改。
    session.source_type = row["form"]
    return withdrawn


def _withdraw_pending_risk_events(db: Session, session: AssessmentSession) -> int:
    """收回这一场**还没人处理过**的风险待办。

    只有 `PENDING` 且没有任何人工复核引用它的事件才是「系统开出、还没人看过」。
    已复核的那条留着——那是心理老师的工作记录，重导一份文件不该把它抹掉，
    而且 `manual_review.risk_event_id` 是外键，本来也删不掉。
    """
    withdrawn = 0
    events = db.scalars(
        select(RiskEvent).where(RiskEvent.session_id == session.id, RiskEvent.status == "PENDING")
    ).all()
    for event in events:
        reviewed = db.scalar(
            select(func.count(ManualReview.id)).where(ManualReview.risk_event_id == event.id)
        )
        if not reviewed:
            db.delete(event)
            withdrawn += 1
    db.flush()
    return withdrawn


def _update_roster_age(
    db: Session,
    student: Student,
    file_age: int,
    *,
    actor: UserAccount,
    batch: AssessmentImportBatch,
    row: AssessmentImportRow,
) -> None:
    """把文件里的年龄写回名册（**只**在操作员选了「覆盖」时才会被调用）。

    `student.age` 是年龄唯一的存储处（迁移 0011 把出生日期换成了它），所以「覆盖更新」
    在这条链路上只有一种含义：动名册。这一列不会自己变，学校不重导名册它就停在去年，
    所以每次改动都留一条**指着这名学生**的审计，记下从哪个数改到哪个数。

    审计不带 `request`（ip / user-agent 为空）：与 `auth.py` 的「修改本人密码」同一处理，
    服务的入参里没有请求对象。批量导入的那条审计行在路由里，带着 ip。

    **另写一条 `student_age_change_log`**（对齐阶段加的表，此前没有写入方）。两者不是
    重复：审计回答「谁在什么时候做了什么」，而这一张回答「这一列改过几次、每次从几到几、
    是哪一批哪一行」——审计是按动作检索的，要把一名学生历次年龄变化拼起来得翻遍
    所有的 `detail` 文本，而这是一条按 `student_id` 直接可查的时间线。§18.5 要的
    「保存修改前年龄、修改后年龄、导入批次和行号、操作人和时间」就是这一行。
    """
    previous = student.age
    if previous == file_age:
        return
    student.age = file_age
    db.add(
        StudentAgeChangeLog(
            student_id=student.id,
            assessment_import_batch_id=batch.id,
            assessment_import_row_id=row.id,
            old_age=previous,
            new_age=file_age,
            reason=f"测评记录导入：批次 {batch.batch_no} 第 {row.row_no} 行，操作员选了覆盖",
            changed_by=actor.id,
        )
    )
    write_audit(
        db,
        action="更新学生年龄",
        resource_type="STUDENT",
        resource_id=str(student.id),
        actor=actor,
        student_id=student.id,
        detail=f"名册 {previous} → 文件 {file_age}（测评记录导入）",
    )


def age_for_session(row: AssessmentImportRow, choice: str | None) -> int | None:
    """这一场按哪个年龄记（`assessment_session.age_at_test`，§18.5）。

    **三个选项里只有「保留系统年龄」会让它与文件里的数不同**——那一个取的是
    `row.age_before`（名册上那个数），另外两个都是 `row.age_after`（文件里的数）。
    这正是用户 2026-09-19 那次裁决的形状：让「无论是否覆盖 `student.age`，本次测评的
    `age_at_test` 都必须保存外部测评时年龄」那一句让步，`age_at_test` 记的是
    **「这一场我们决定按哪个年龄理解他」**。文件里那个数一个字都没丢，它在
    `raw_age` 与 `age_after` 上，逐行明细里看得见。

    没有年龄冲突的行（`row.conflict_code` 不是 `AGE_MISMATCH`）走最后那一句：
    文件里的年龄就是名册上的年龄，三个选项对它们没有分别，也没人会问。
    """
    if row.conflict_code == CONFLICT_AGE_MISMATCH and choice == AGE_RESOLUTION_KEEP:
        return row.age_before
    return row.age_after


def _next_task_no(db: Session, tested_on: date) -> str:
    """`IMPORT-202609-1`：批次的编号按**月**，因为一场任务就是一个月。

    `-1` 的序号留给「同一个月的第二个任务」这种将来可能有的情况（现在同月一律复用）。
    按天编号的旧任务（`IMPORT-20260916-1`）与这个形状不冲突，会被 `_task_for_month`
    直接复用掉，不会在这里生成第二个。
    """
    prefix = f"IMPORT-{tested_on:%Y%m}"
    taken = set(
        db.scalars(select(AssessmentTask.task_no).where(AssessmentTask.task_no.like(f"{prefix}-%"))).all()
    )
    number = 1
    while f"{prefix}-{number}" in taken:
        number += 1
    return f"{prefix}-{number}"


def school_for_import(db: Session) -> School | None:
    """导入写进哪所学校。

    与 `student_import_service._target_school` 同一个约定：单校写死 `QH`
    （CLAUDE.md 已知缺口 2）。多校部署时这两处要一起改——预览与提交也必须用同一个，
    否则会在预览里定位到一所学校的学生、提交时落到另一所。
    """
    return db.scalar(select(School).where(School.code == "QH"))


def _published_scale(db: Session) -> AssessmentScale | None:
    return db.scalar(
        select(AssessmentScale)
        .where(AssessmentScale.code == "MHT", AssessmentScale.status == "PUBLISHED")
        .order_by(AssessmentScale.id.desc())
    )


def template_csv() -> str:
    header = ["姓名", "性别", "年龄", "年级", "班级", "所用时间"]
    header += [f"{number}.题干" for number in range(1, QUESTION_COUNT + 1)]
    sample_row = ["示例学生", "1", "12", "1", "4", "3600秒"] + ["0"] * QUESTION_COUNT

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(sample_row)
    return "﻿" + output.getvalue()
