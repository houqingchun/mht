"""导出用的中文映射必须与 `labels.ts` 逐字一致。

`services/export_labels.py` 是 `frontend/src/services/labels.ts` 里五张表的一份镜像，
存在的唯一理由是导出的 CSV 由后端生成，而后端读不到 .ts。镜像就会漂移，所以这里直接
把那份 TypeScript **当作数据源读进来**比对，而不是在测试里再抄一遍中文——抄一遍的话，
三处中文（labels.ts / export_labels.py / 这个测试）就有三种改错的方式，而测试只认自己
那一份，改起来还会一起变绿。

改中文的正确做法是同时改两边；只改一边，这里就红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import export_labels

LABELS_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "services" / "labels.ts"

# (后端 map, labels.ts 里的 map 名)。`labels.ts` 把档案阶段那张叫 STATUS_LABELS，
# 后端这边加上 CASE_ 前缀，因为同一个模块里还要放任务目标的状态。
MIRRORED_MAPS = [
    (export_labels.GENDER_LABELS, "GENDER_LABELS"),
    (export_labels.LEVEL_LABELS, "LEVEL_LABELS"),
    (export_labels.CASE_STATUS_LABELS, "STATUS_LABELS"),
    (export_labels.TARGET_STATUS_LABELS, "TARGET_STATUS_LABELS"),
    (export_labels.SOURCE_LABELS, "SOURCE_LABELS"),
]


def frontend_labels_ts() -> str:
    assert LABELS_TS.exists(), f"读不到 {LABELS_TS}"
    return LABELS_TS.read_text(encoding="utf-8")


def frontend_map(source: str, name: str) -> dict[str, str]:
    """抓出 `export const NAME: Record<string, string> = { ... }` 里的键值对。"""
    match = re.search(
        rf"export const {name}: Record<string, string> = \{{(.*?)\n\}}", source, re.S
    )
    assert match, f"labels.ts 里没有 {name}"
    return dict(re.findall(r"([A-Z_]+):\s*'([^']*)'", match.group(1)))


@pytest.mark.parametrize(("backend_map", "frontend_name"), MIRRORED_MAPS)
def test_export_label_map_matches_labels_ts(backend_map, frontend_name):
    source = frontend_labels_ts()
    assert backend_map == frontend_map(source, frontend_name)


def test_unassessed_label_matches_labels_ts():
    """「未测评」不是某张表里的词条，而是 `levelLabel` 的兜底——单独盯一下。

    导出里没有测评结果的一行会写出这四个字，界面上也是；两边是同一句话，
    所以直接断言这个词在 labels.ts 里以同样的字面量存在。
    """
    assert f"'{export_labels.UNASSESSED_LABEL}'" in frontend_labels_ts()


def test_unknown_code_falls_back_to_the_code_itself():
    """认不出的码原样回退：漏码要看得见，不能悄悄变成空单元格（与 labels.ts 的 labelOf 同形）。"""
    assert export_labels.label_of({"MALE": "男"}, "OTHER") == "OTHER"
    assert export_labels.gender_label(None) == "—"
    assert export_labels.level_label(None) == "未测评"
    assert export_labels.target_status_label("COMPLETED") == "已完成"
