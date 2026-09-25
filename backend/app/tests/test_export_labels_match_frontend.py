"""导出用的中文映射必须与 `labels.ts` 逐字一致。

`services/export_labels.py` 是 `frontend/src/services/labels.ts` 里那十二张**会进导出文件**的表
的一份镜像，存在的唯一理由是导出的 CSV 由后端生成，而后端读不到 .ts。镜像就会漂移，所以
这里直接把那份 TypeScript **当作数据源读进来**比对，而不是在测试里再抄一遍中文——抄一遍
的话，三处中文（labels.ts / export_labels.py / 这个测试）就有三种改错的方式，而测试只认
自己那一份，改起来还会一起变绿。

改中文的正确做法是同时改两边；只改一边，这里就红。

**「会进导出文件的才镜像」**：`labels.ts` 里那四十多张表大部分只有界面读者，加进来只会
让每一次改中文都要多看一处。判据是「有没有一条 CSV 列读它」——所以新增一张表之前先回答
那一句话（`IMPORT_CONFLICT_LABELS` / `CALCULATION_STATUS_LABELS` 那几张就不在这里，
它们只渲染在弹层里）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import export_labels

FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
LABELS_TS = FRONTEND_SRC / "services" / "labels.ts"

# (后端 map, labels.ts 里的 map 名)。`labels.ts` 把档案阶段那张叫 STATUS_LABELS，
# 后端这边加上 CASE_ 前缀，因为同一个模块里还要放任务目标的状态。
MIRRORED_MAPS = [
    (export_labels.GENDER_LABELS, "GENDER_LABELS"),
    (export_labels.STUDENT_STATUS_LABELS, "STUDENT_STATUS_LABELS"),
    (export_labels.LEVEL_LABELS, "LEVEL_LABELS"),
    (export_labels.CASE_STATUS_LABELS, "STATUS_LABELS"),
    (export_labels.TARGET_STATUS_LABELS, "TARGET_STATUS_LABELS"),
    (export_labels.SOURCE_LABELS, "SOURCE_LABELS"),
    (export_labels.PARTICIPATION_LABELS, "PARTICIPATION_DISPOSITION_LABELS"),
    (export_labels.MATCH_STATUS_LABELS, "MATCH_STATUS_LABELS"),
    (export_labels.UNMATCHED_REASON_LABELS, "UNMATCHED_REASON_LABELS"),
    (export_labels.REPORT_STATUS_LABELS, "REPORT_STATUS_LABELS"),
    (export_labels.DIMENSION_LABELS, "DIMENSION_LABELS"),
    (export_labels.SCORE_DISTRIBUTION_LABELS, "SCORE_DISTRIBUTION_LABELS"),
]


def frontend_labels_ts() -> str:
    assert LABELS_TS.exists(), f"读不到 {LABELS_TS}"
    return LABELS_TS.read_text(encoding="utf-8")


def frontend_map_body(source: str, name: str) -> str:
    """抓出 `export const NAME: Record<string, string> = { ... }` 的**表体**。"""
    match = re.search(
        rf"export const {name}: Record<string, string> = \{{(.*?)\n\}}", source, re.S
    )
    assert match, f"labels.ts 里没有 {name}"
    return match.group(1)


def frontend_map(source: str, name: str) -> dict[str, str]:
    """`labels.ts` 那张表的**展开后**的样子。

    **展开必须解析**，这是 2026-09-20 补的（`UNMATCHED_REASON_LABELS` 带进来的）：
    那张表写的是 `{ ...MATCH_STATUS_LABELS, MATCHED: '已匹配但被放弃' }`，只认字面量的话
    它会被读成**一条**，后端那九条一比就红——红的原因是解析器瞎，不是两边真的不一致，
    而下一个人多半会去改中文来「修」它。

    另一半同样重要：**不能在这里硬编码那九条中文**。那是第四份副本，只在这个测试里生效
    ——与这个文件开头那段「抄一遍就有三种改错的方式」逐字同一条理由。所以解析展开
    （`...NAME` 递归取出来合并）而不是抄写。

    覆盖次序照 TypeScript：先展开的、后被字面量盖掉。
    """
    body = frontend_map_body(source, name)
    merged: dict[str, str] = {}
    for parent in re.findall(r"\.\.\.([A-Z_]+)", body):
        merged.update(frontend_map(source, parent))
    merged.update(re.findall(r"([A-Z_]+):\s*'([^']*)'", body))
    return merged


@pytest.mark.parametrize(("backend_map", "frontend_name"), MIRRORED_MAPS)
def test_export_label_map_matches_labels_ts(backend_map, frontend_name):
    source = frontend_labels_ts()
    assert backend_map == frontend_map(source, frontend_name)


def test_the_spread_tables_are_actually_read_through():
    """展开解析不能是空转的——**先证明它真的走到了**。

    上面那条参数化用例在「解析器看不见展开」时也会红，但红出来的是一条**两边键数不等**
    的差异，读起来像中文没同步。这一条把病灶说清楚：那张表解析出来的条数必须多于它自己
    的字面量条数（否则展开那一步一次都没触发，而下面的断言恰好还是绿的）。

    这是本项目那条「先证明有东西可扫，再断言它干净」的同一形状——只不过这里要证明的
    是**解析器真的展开了**，因为一个只会读字面量的解析器在别的七张表上全都正常。
    """
    source = frontend_labels_ts()
    resolved = frontend_map(source, "UNMATCHED_REASON_LABELS")
    literals = dict(re.findall(r"([A-Z_]+):\s*'([^']*)'", frontend_map_body(source, "UNMATCHED_REASON_LABELS")))
    assert len(literals) == 1, "这张表的写法变了：它的样子就是「展开 + 覆盖一条」"
    assert len(resolved) > len(literals), "展开没有被解析——上面那条用例红的原因会是假的"
    assert resolved["MATCHED"] == "已匹配但被放弃"
    assert resolved["NOT_FOUND"] == export_labels.MATCH_STATUS_LABELS["NOT_FOUND"]


def test_unassessed_label_matches_labels_ts():
    """「未测评」不是某张表里的词条，而是 `levelLabel` 的兜底——单独盯一下。

    导出里没有测评结果的一行会写出这四个字，界面上也是；两边是同一句话，
    所以直接断言这个词在 labels.ts 里以同样的字面量存在。
    """
    assert f"'{export_labels.UNASSESSED_LABEL}'" in frontend_labels_ts()


def test_small_cohort_label_matches_the_frontend():
    """「样本过小」**不在 `labels.ts` 里**，所以它守不了上面那条办法。

    §11：分母小于 `MIN_COHORT_FOR_AGGREGATE` 时不下发比率与均值（`_report_rate` 返回
    `None`），而界面上那句话是**直接写在视图里的字面量**（`LeaderOverviewPage.vue` /
    `ScoreBandBars.vue` / `ClassComparisonPanel.vue`）——`labels.ts` 里没有它，后端也没有
    任何常量。导出文件里要写同一句话，就只能到前端**源码树**里找这四个字。

    判据写成「至少有一个文件里有」而不是点名某个文件：那几个渲染点会随页面重构搬家，
    而这句话本身不该变。**空转自检不能省**——路径写错而一个文件都没扫到时，下面的
    `any` 与「文案真的没同步」长得一模一样，所以先断言真的扫到了东西。
    """
    assert FRONTEND_SRC.exists(), f"读不到 {FRONTEND_SRC}"
    sources = [p for p in FRONTEND_SRC.rglob("*") if p.suffix in {".vue", ".ts"}]
    assert len(sources) > 20, f"只扫到 {len(sources)} 个前端源文件，这个路径大概是错的"
    hits = [
        p
        for p in sources
        if f"'{export_labels.SMALL_COHORT_LABEL}'" in p.read_text(encoding="utf-8")
    ]
    assert hits, (
        f"前端源码里找不到 '{export_labels.SMALL_COHORT_LABEL}'"
        "——导出文件与界面说的不是同一句话（改文案要两边一起改）"
    )


def test_unknown_code_falls_back_to_the_code_itself():
    """认不出的码原样回退：漏码要看得见，不能悄悄变成空单元格（与 labels.ts 的 labelOf 同形）。"""
    assert export_labels.label_of({"MALE": "男"}, "OTHER") == "OTHER"
    assert export_labels.gender_label(None) == "—"
    assert export_labels.level_label(None) == "未测评"
    assert export_labels.target_status_label("COMPLETED") == "已完成"
