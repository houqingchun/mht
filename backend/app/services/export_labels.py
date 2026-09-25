"""编码 → 中文，供**服务端生成的导出文件**使用。

`frontend/src/services/labels.ts` 是前端展示的唯一映射层（CLAUDE.md §3），但导出是后端
拼出来的文件——后端 import 不了一个 .ts，而这份 CSV 的读者是拿到文件的学校，不是浏览器。
所以这里是那些**会进导出文件**的表的一份镜像，逐字相同，并由
`tests/test_export_labels_match_frontend.py` 两边比对：只改 `labels.ts` 的中文而不同步
这里，测试就变红。

这是 §3 词汇契约的第三个出口。前两个是「后端发的码前端认不认」（后端测试）与
「视图有没有调用标签函数」（`e2e/vocabulary.spec.ts` 按像素扫）——它们都只覆盖界面。
导出漏翻译了很久：界面上写着「重点关注」，同一份数据导出成 CSV 就是 `KEY_ATTENTION`，
因为没有任何一条测试看得见文件里写了什么。

十二张表与十二个函数都照着 `labels.ts` 的同名函数写，**包括缺值时的表现**：
性别缺失是 `—`、未测评为 `未测评`（`levelLabel` 的既有约定），不是空单元格。

**这是一个会长的东西**：每多一个会进导出文件的枚举码，这里就要多一张表、`labels.ts`
要多一张、`test_export_labels_match_frontend.py` 的 `MIRRORED_MAPS` 要多一行。
漏掉任何一处都不会报错（§3 第三面），所以加码时按这三处一起数一遍。

**下面每一张表上方的「第 N 张」只是一个位置，不是身份**——`STUDENT_STATUS_LABELS`
（2026-09-24）插在第二的位置之后，那之后每一张的序号都整体后移了，而当时没有任何东西
发现（`REPORT_STATUS_LABELS` 的注释就因此错了一个版本）。认一张表靠它上方的**常量名**；
序号只用来读「它大概什么时候进来的」。

第五张表（`SOURCE_LABELS`，测评来源）是 2026-09-16 加「外部导入」时补的：受控导出取的是
每个学生**最新**的那场会话，学校导入一份外部普查结果之后，导出的关注等级与用时可能就
来自那份记录，而 CSV 上没有任何一处说明它是外部平台测的。

第六张表（`PARTICIPATION_LABELS`，参与状态）随 §18.10 的完成明细细分一起来：那一列
写的是「应测 / 请假 / 免测 / 已排除」，而完成率的分母正是从这四个码里算出来的——
导出成 `REQUIRED` 的话，拿到文件的人看不出这一行**为什么不算进分母**。

第七、八张表（`MATCH_STATUS_LABELS` / `UNMATCHED_REASON_LABELS`，导入行的匹配结论）
随第十个端点（未匹配行导出，§20#14 的「导出」那一半）一起来。两张表是**同一件事的
两种读者**，所以第八张在这里也是照 `labels.ts` 的展开写法写的——它只覆盖 `MATCHED`
一条，逐字沿用其余八条（理由写在那一处）。
"""

from __future__ import annotations

# 与 labels.ts 的 GENDER_LABELS 逐字一致。
GENDER_LABELS: dict[str, str] = {
    "MALE": "男",
    "FEMALE": "女",
}

# 与 labels.ts 的 STUDENT_STATUS_LABELS 逐字一致（后端 Student.status）。
#
# 2026-09-24 随效度复测名单导出补：那一份文件的用途是「派人去找这名学生重测」，
# 而一名已离校的学生不该被当成还能找到的人。**两档与 `labels.ts` 逐字同源**，
# 不在导出函数里内联 `"在读" if status == "ACTIVE" else "已离校"`——那会是同一批中文的
# 第三个定义（前两个是 labels.ts 与这个文件），而它漂了不会有任何东西看得见。
STUDENT_STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "在读",
    "INACTIVE": "已离校",
}

# 与 labels.ts 的 LEVEL_LABELS 逐字一致。
LEVEL_LABELS: dict[str, str] = {
    "KEY_ATTENTION": "重点关注",
    "NEEDS_ATTENTION": "需要关注",
    "GENERAL_RANGE": "一般观察",
}

# 与 labels.ts 的 STATUS_LABELS 逐字一致（后端 StudentCareCase.status）。
CASE_STATUS_LABELS: dict[str, str] = {
    "PENDING_REVIEW": "待复核",
    "FOLLOWING": "跟进中",
    "OBSERVING": "观察中",
    "CLOSED": "已关闭",
}

# 与 labels.ts 的 TARGET_STATUS_LABELS 逐字一致（后端 AssessmentTarget.status）。
TARGET_STATUS_LABELS: dict[str, str] = {
    "COMPLETED": "已完成",
    "IN_PROGRESS": "进行中",
    "NOT_STARTED": "未开始",
}

# 与 labels.ts 的 SOURCE_LABELS 逐字一致（后端 AssessmentSession.source）。
SOURCE_LABELS: dict[str, str] = {
    "IN_SYSTEM": "系统内作答",
    "IMPORTED": "外部导入",
}

# 与 labels.ts 的 PARTICIPATION_DISPOSITION_LABELS 逐字一致
# （后端 AssessmentTarget.participation_disposition）。后三个是 §18.10 那条算式里的
# 三个减项，顺序与 `PARTICIPATION_EXCLUDED_DISPOSITIONS` 相同。
PARTICIPATION_LABELS: dict[str, str] = {
    "REQUIRED": "应测",
    "LEAVE": "请假",
    "EXEMPT": "免测",
    "EXCLUDED": "已排除",
}

# 与 labels.ts 的 MATCH_STATUS_LABELS 逐字一致（后端 AssessmentImportRow.match_status）。
# 2026-09-20 随第十个端点（未匹配行导出）补：那一份 CSV 的「匹配结论」列此前无中文可写。
MATCH_STATUS_LABELS: dict[str, str] = {
    "PENDING": "待匹配",
    "MATCHED": "已匹配",
    "AGE_CONFLICT": "年龄与名册不符",
    "DUPLICATE": "本月已导过一次",
    "CONFLICT": "本场已有答卷",
    "INVALID_ROW": "这一行有错误",
    "NOT_FOUND": "名册上没有",
    "OUT_OF_SCOPE": "不在本场任务里",
    "AMBIGUOUS": "同名分不开",
}

# 与 labels.ts 的 UNMATCHED_REASON_LABELS 逐字一致——**它比上一张只多改一条**。
#
# 照**这一个**形状写（展开 + 覆盖），不把九条中文再抄一遍：`labels.ts` 那边就是
# `{ ...MATCH_STATUS_LABELS, MATCHED: '已匹配但被放弃' }`，两份镜像必须同形，
# 否则「两边各抄一遍中文」这件事就真的发生了，而那正是这个文件存在的理由。
#
# 为什么这一列要用这张表而不是上一张：那一屏的行有两个来源（「匹配不上」**或者**
# 「匹配上了但被放弃」）。被放弃的那些行的 `match_status` 是**要拍板的那三档**
# （`AGE_CONFLICT` / `DUPLICATE` / `CONFLICT`）——`_row_will_be_written` 只对它们认
# 「放弃」，`MATCHED` 落在最后那句 `return True` 上，整批选放弃也照写。所以下面那条
# `MATCHED` 覆盖**今天造不出来**（2026-09-20 撞出来的）。
#
# 留着它是因为它读的那句话仍然成立：哪一天匹配上的行也能被放弃，照上一张渲染就会让
# 一份叫「没进得去」的文件里出现「已匹配」。**它是这个状态的读法，不是这个状态存在的
# 证据**——别拿它推出「这一屏会有 `MATCHED` 的行」（测试里那条夹具就是这么写错的）。
UNMATCHED_REASON_LABELS: dict[str, str] = {
    **MATCH_STATUS_LABELS,
    "MATCHED": "已匹配但被放弃",
}

# 与 labels.ts 的 REPORT_STATUS_LABELS 逐字一致——第十张表，随 V2.0.0 §8 的专业报告
# 导出（`reporting_service.report_document`）一起来。
#
# 这里原本写着「第九张」。它**写下时是对的**，2026-09-24 插进 `STUDENT_STATUS_LABELS`
# （效度复测名单导出）之后才变成错的——**这个序号是位置，插一张表就会整体后移**，
# 而它不会被任何东西发现。所以下面每一张的序号都只是「写这一段时的位置」，不是身份；
# 认一张表靠它上方的常量名，不靠序号。
#
# 它是这一层**最晚被发现**的一处漏码，而漏了整整一个版本：那一份 CSV 里写着
# `writer.writerow(["状态", report.status])`，于是界面上写着「已发布」的报告，
# 导出成文件就是 `PUBLISHED`。这与 `SOURCE_LABELS` / `PARTICIPATION_LABELS` 那两次
# 是同一个形状（§3 第三面：前两面都只覆盖界面，谁也看不见文件里写了什么），
# 区别是这一次它同时还有第二个洞——那一列当时连 `export_labels` 都没 import。
REPORT_STATUS_LABELS: dict[str, str] = {
    "DRAFT": "草稿",
    "PUBLISHED": "已发布",
    "ARCHIVED": "已归档",
}

# 与 labels.ts 的 DIMENSION_LABELS 逐字一致——第十一张表。
#
# 同一批 export 里补的：§7.3 要求快照进文件，而快照的 `dimensions[].dimension_code`
# 是 `LEARNING_ANXIETY` 这类**英文编码**（CLAUDE.md §3：维度是英文编码，不是 A–H 单字母）。
# 不翻译的话，一份给学校看的报告里会印出八个编码。
#
# **这是仓库里第二份「维度编码 → 中文」**，第一份是
# `assessment_import_service.DIMENSION_BY_LABEL`（中文 → 编码，方向相反，由
# `test_assessment_import_api.py::test_the_dimension_labels_are_a_mirror_of_the_frontend`
# 守着）。两份各自被各自的守卫钉在 `labels.ts` 上，所以它们不会各说各话；**别把它们
# 合并成一个双向字典**——那会让其中一侧的守卫失去靶子。
DIMENSION_LABELS: dict[str, str] = {
    "LEARNING_ANXIETY": "学习焦虑",
    "INTERPERSONAL_ANXIETY": "对人焦虑",
    "LONELINESS": "孤独倾向",
    "SELF_BLAME": "自责倾向",
    "SENSITIVITY": "过敏倾向",
    "PHYSICAL_SYMPTOMS": "身体症状",
    "PHOBIC_TENDENCY": "恐怖倾向",
    "IMPULSIVE_TENDENCY": "冲动倾向",
}

# 与 labels.ts 的 SCORE_DISTRIBUTION_LABELS 逐字一致——第十二张表。
#
# 与 `LEVEL_LABELS`（关注等级）是**两个轴**：那是「这个学生整体是什么状态」，这是
# 「这一份统计里，落在各分数段的人各有多少」。导出文件里那一行《维度分布》两者都不出现，
# 出现的是「低分区间 / 中分区间 / 高分区间」这三档——而它们同样是编码（`LOW` / `MEDIUM`
# / `HIGH`）。
SCORE_DISTRIBUTION_LABELS: dict[str, str] = {
    "LOW": "低分区间",
    "MEDIUM": "中分区间",
    "HIGH": "高分区间",
}

# 与 labels.ts 里 levelLabel 的兜底一致：没有测评结果不等于"测出来什么都没有"。
UNASSESSED_LABEL = "未测评"

# 「样本过小」——**它不在 `labels.ts` 里**，是一句直接写在视图里的字面量
# （`LeaderOverviewPage.vue` / `ScoreBandBars.vue` / `ClassComparisonPanel.vue`）。
# 后端那一侧同样没有常量：`_report_rate` 只是**返回 `None`**（§11：分母小于
# `MIN_COHORT_FOR_AGGREGATE` 时不下发比率与均值）。
#
# 导出文件里 `None` 不能写成空白：那一格与「一个都没有」（`0`）在两处都必须长得不一样，
# 而一份没有解释的空单元格读起来像「这份文件漏了一格」。所以这里给它一个常量，
# 由 `test_small_cohort_label_matches_the_frontend` 在前端**源码树**里找这四个字
# （不是 `labels.ts`——它不在那儿）。
SMALL_COHORT_LABEL = "样本过小"

MISSING_LABEL = "—"


def label_of(mapping: dict[str, str], code: str | None, fallback: str = MISSING_LABEL) -> str:
    """与 labels.ts 的 `labelOf` 同形：认不出的码**原样回退**，不是变空白。

    回退成空字符串会把"后端发了一个前端不认识的码"这件事藏起来——那种情况在界面上
    是漏码，在导出里同样应该看得见。
    """
    if not code:
        return fallback
    return mapping.get(code, code)


def gender_label(code: str | None) -> str:
    return label_of(GENDER_LABELS, code)


def student_status_label(code: str | None) -> str:
    return label_of(STUDENT_STATUS_LABELS, code)


def level_label(code: str | None) -> str:
    if not code:
        return UNASSESSED_LABEL
    return label_of(LEVEL_LABELS, code)


def case_status_label(code: str | None) -> str:
    return label_of(CASE_STATUS_LABELS, code)


def target_status_label(code: str | None) -> str:
    return label_of(TARGET_STATUS_LABELS, code)


def source_label(code: str | None) -> str:
    return label_of(SOURCE_LABELS, code)


def participation_label(code: str | None) -> str:
    return label_of(PARTICIPATION_LABELS, code)


def match_status_label(code: str | None) -> str:
    return label_of(MATCH_STATUS_LABELS, code)


def unmatched_reason_label(code: str | None) -> str:
    """未匹配行清单上的读法——`MATCHED` 在这一屏读成「已匹配但被放弃」（见上）。"""
    return label_of(UNMATCHED_REASON_LABELS, code)


def report_status_label(code: str | None) -> str:
    """专业分析报告的状态（草稿 / 已发布 / 已归档）。"""
    return label_of(REPORT_STATUS_LABELS, code)


def dimension_label(code: str | None) -> str:
    """MHT 八维度（学习焦虑 / 对人焦虑 / …）——`scale_engine.dimension_for_question` 的那八个码。"""
    return label_of(DIMENSION_LABELS, code)


def score_distribution_label(code: str | None) -> str:
    """维度得分分布区间（低分区间 / 中分区间 / 高分区间）。"""
    return label_of(SCORE_DISTRIBUTION_LABELS, code)
