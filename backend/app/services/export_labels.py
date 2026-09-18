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

五张表与五个函数都照着 `labels.ts` 的同名函数写，**包括缺值时的表现**：
性别缺失是 `—`、未测评为 `未测评`（`levelLabel` 的既有约定），不是空单元格。

第五张表（`SOURCE_LABELS`，测评来源）是 2026-09-16 加「外部导入」时补的：受控导出取的是
每个学生**最新**的那场会话，学校导入一份外部普查结果之后，导出的关注等级与用时可能就
来自那份记录，而 CSV 上没有任何一处说明它是外部平台测的。
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
