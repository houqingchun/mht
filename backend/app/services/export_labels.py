"""编码 → 中文，供**服务端生成的导出文件**使用。

`frontend/src/services/labels.ts` 是前端展示的唯一映射层（CLAUDE.md §3），但导出是后端
拼出来的文件——后端 import 不了一个 .ts，而这份 CSV 的读者是拿到文件的学校，不是浏览器。
所以这里是那四张表的一份镜像，逐字相同，并由
`tests/test_export_labels_match_frontend.py` 两边比对：只改 `labels.ts` 的中文而不同步
这里，测试就变红。

这是 §3 词汇契约的第三个出口。前两个是「后端发的码前端认不认」（后端测试）与
「视图有没有调用标签函数」（`e2e/vocabulary.spec.ts` 按像素扫）——它们都只覆盖界面。
导出漏翻译了很久：界面上写着「重点关注」，同一份数据导出成 CSV 就是 `KEY_ATTENTION`，
因为没有任何一条测试看得见文件里写了什么。

八张表与八个函数都照着 `labels.ts` 的同名函数写，**包括缺值时的表现**：
性别缺失是 `—`、未测评为 `未测评`（`levelLabel` 的既有约定），不是空单元格。

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

# 与 labels.ts 里 levelLabel 的兜底一致：没有测评结果不等于"测出来什么都没有"。
UNASSESSED_LABEL = "未测评"

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
