"""`risk_type` → `signal_type`：一张表，三处写法，必须一致。

`risk_event.signal_type` 是一个**没有默认值**的 NOT NULL 列（V1.2），所以每一个
写风险事件的人都要回答「这条信号归谁办」。答案只有一处定义——
`scale_engine.engine.SIGNAL_TYPE_BY_RISK_TYPE`，而它同时被三处消费：

| 谁 | 拿它做什么 |
|---|---|
| `assessment_service.maybe_raise_risk_events` | 写新行时的 `signal_type` / `requires_manual_review` |
| 迁移 `0013_v12_expand` 的前置校验 | 认不出的 `risk_type` **中止整条迁移**，不猜 |
| 迁移 `0013` 的回填 CASE | 给 V1.0 留下的历史行补上那两列 |

三处是同一件事的三份写法，所以会漂。**漂的方式全是静默的**：引擎加一个新
`risk_type` 而迁移没跟着加，前置换算就认不出它——
一条合法的新信号会让一所学校的升级**卡在第 4 步**，报出来是一句「有认不出的值」，
而 DDL 一个字都没问题。

所以下面两条：引擎那一张表自己钉住（它是判据），再把它与迁移的文件**逐字比**。
比法是 CLAUDE.md §3 那条既有约定换个地方用一次——`test_export_labels_match_frontend
.py` 把 `labels.ts` 当数据源读进来，这里把迁移源文件当数据源读进来。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.scale_engine.engine import (
    SIGNAL_TYPES_REQUIRING_MANUAL_REVIEW,
    SIGNAL_TYPE_BY_RISK_TYPE,
    requires_manual_review,
    signal_type_for,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0013_v12_expand.py"
)


def test_the_mapping_is_the_three_way_split_the_ddl_defines():
    """表的内容逐字钉住。

    三档不是随手分的，它们对应「这件事归谁办」：

    - `MANUAL_REVIEW_REQUIRED`：要人去看一眼（心理老师的待办队列）；
    - `RETEST_RECOMMENDED`：要再测一次；
    - `SCREENING_SIGNAL`：只是一个筛查信号，**不进任何人的队列**。

    这条断言看着像抄一遍常量，但它是这个文件里唯一能回答「改了这张表会不会
    改变行为」的地方：加一个键、或者把某一档挪到别的 `signal_type` 上，
    都得先在这里说一声。
    """
    assert SIGNAL_TYPE_BY_RISK_TYPE == {
        "MANUAL_REVIEW_REQUIRED": "MANUAL_REVIEW_REQUIRED",
        "KEY_QUESTION_TRIGGERED": "MANUAL_REVIEW_REQUIRED",
        "RETEST_RECOMMENDED": "RETEST_RECOMMENDED",
        "HIGH_TOTAL_SCORE": "SCREENING_SIGNAL",
        "HIGH_DIMENSION_SCORE": "SCREENING_SIGNAL",
        "SCREENING_SIGNAL": "SCREENING_SIGNAL",
    }


def test_the_engine_only_ever_emits_risk_types_that_are_in_the_mapping():
    """引擎真正发得出来的那些，一个都不能漏。

    上面那条钉的是表的内容，这一条钉的是**表够不够用**：引擎今天只发
    `MANUAL_REVIEW_REQUIRED`（`engine.py` 里唯一的 `RiskEventResult(...)`），
    而 `trigger_rule` 是 `KEY_QUESTION_{n}_YES`。
    """
    from app.scale_engine.engine import AnswerValue, ScaleEngine, default_mht_questions

    answers = {question_no: AnswerValue.NO.value for question_no in range(1, 101)}
    answers[85] = AnswerValue.YES.value
    result = ScaleEngine().calculate(default_mht_questions(), answers)

    assert result.risk_events, "重点题答「是」应当产生一条风险事件，这条用例失去了判据"
    for event in result.risk_events:
        signal_type = signal_type_for(event.risk_type)  # 认不出就抛
        assert signal_type in {"MANUAL_REVIEW_REQUIRED", "RETEST_RECOMMENDED", "SCREENING_SIGNAL"}
        assert event.trigger_rule == "KEY_QUESTION_85_YES"


def test_an_unknown_risk_type_raises_instead_of_guessing():
    """**不许给一个默认值。**

    猜的代价是静默的：写错一档，那条待办就落进另一类人的队列，或者干脆不进
    任何人的队列——而界面上一切正常。迁移 0013 的前置校验对「认不出的
    `risk_type`」也是中止（它宁可让一所学校的升级停下来，也不肯替人猜），
    这里与它一致。
    """
    with pytest.raises(ValueError) as exc_info:
        signal_type_for("SOMETHING_NEW_FROM_V1_3")

    assert "SOMETHING_NEW_FROM_V1_3" in str(exc_info.value)
    assert "SIGNAL_TYPE_BY_RISK_TYPE" in str(exc_info.value)


@pytest.mark.parametrize(
    ("signal_type", "expected"),
    [
        ("MANUAL_REVIEW_REQUIRED", True),
        ("RETEST_RECOMMENDED", False),
        ("SCREENING_SIGNAL", False),
        # 认不出的**不抛异常**（与 `signal_type_for` 不同）：这一问是布尔判断，
        # 而「这条信号不归人工复核」对任何陌生的码都是保守且正确的答案。
        ("SOMETHING_NEW_FROM_V1_3", False),
    ],
)
def test_requires_manual_review_only_for_the_manual_review_signal(signal_type, expected):
    assert requires_manual_review(signal_type) is expected


def test_the_set_of_signals_needing_review_is_the_one_the_mapping_implies():
    """两处写法：`SIGNAL_TYPES_REQUIRING_MANUAL_REVIEW` 与映射表里那些
    「映射到 `MANUAL_REVIEW_REQUIRED` 的 `risk_type`」。

    少了这一条，往映射表里加一个指向 `MANUAL_REVIEW_REQUIRED` 的新
    `risk_type` 时，那张 `frozenset` 会**悄悄不跟着长**——而它的读者
    （`requires_manual_review`）决定新行上的那一列，于是「该进待办的没进」。
    """
    assert SIGNAL_TYPES_REQUIRING_MANUAL_REVIEW == {
        signal_type
        for signal_type in SIGNAL_TYPE_BY_RISK_TYPE.values()
        if signal_type == "MANUAL_REVIEW_REQUIRED"
    }


# --------------------------------------------------------------------------
# 与迁移 0013 逐字比：三处写法是同一件事
# --------------------------------------------------------------------------

# 前置校验里那个白名单：`WHERE risk_type NOT IN ('A', 'B', …)`。
_PRECHECK_WHITELIST_RE = re.compile(
    r"WHERE\s+risk_type\s+NOT\s+IN\s*\((?P<body>[^)]*)\)", re.IGNORECASE
)
# 回填的 CASE：`WHEN risk_type IN ('A', 'B') THEN 'X'` 与
# `WHEN risk_type = 'A' THEN 'X'` 两种写法都认。
_BACKFILL_CASE_RE = re.compile(
    r"SET\s+signal_type\s*=\s*CASE(?P<body>.*?)\bEND\b", re.IGNORECASE | re.DOTALL
)
_WHEN_RE = re.compile(
    r"WHEN\s+risk_type\s*(?:IN\s*\((?P<many>[^)]*)\)|=\s*(?P<one>'[A-Z_]+'))\s*THEN\s*(?P<target>'[A-Z_]+')",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r"'([A-Z_]+)'")


def migration_text() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def test_the_prechecks_whitelist_is_exactly_the_mappings_keys():
    """迁移的前置校验认得的 `risk_type`，必须**恰好**是引擎认得的那一批。

    松一格（白名单更大）不会出事但会骗人；紧一格（少一个）的后果是
    「一条合法的新信号让一所学校的升级停下来」。
    """
    match = _PRECHECK_WHITELIST_RE.search(migration_text())
    assert match, "没找到 0013 里那条 `risk_type NOT IN (…)` 的前置校验，正则大概坏了"

    whitelist = set(_QUOTED_RE.findall(match.group("body")))
    assert whitelist, "白名单解析成空集合，正则大概坏了"
    assert whitelist == set(SIGNAL_TYPE_BY_RISK_TYPE), (
        f"只在迁移的白名单里：{sorted(whitelist - set(SIGNAL_TYPE_BY_RISK_TYPE))}\n"
        f"只在引擎的映射表里：{sorted(set(SIGNAL_TYPE_BY_RISK_TYPE) - whitelist)}"
    )


def test_the_backfill_case_agrees_with_the_mapping_row_by_row():
    """回填 CASE 的每一支，都要与映射表给出同一个 `signal_type`。

    这一条是「历史行与新行口径一致」的唯一保证：新行走引擎的映射表，
    历史行走这条 CASE。两者对一个 `risk_type` 给出不同的 `signal_type` 时，
    同一个信号会落进两条不同的队列——而两份代码各自看起来都对。
    """
    match = _BACKFILL_CASE_RE.search(migration_text())
    assert match, "没找到 0013 里那段 `SET signal_type = CASE … END`，正则大概坏了"

    backfilled: dict[str, str] = {}
    for when in _WHEN_RE.finditer(match.group("body")):
        target = _QUOTED_RE.findall(when.group("target"))[0]
        if when.group("many") is not None:
            for risk_type in _QUOTED_RE.findall(when.group("many")):
                backfilled[risk_type] = target
        else:
            backfilled[_QUOTED_RE.findall(when.group("one"))[0]] = target

    assert backfilled, "回填 CASE 解析成空字典，正则大概坏了"
    assert backfilled == SIGNAL_TYPE_BY_RISK_TYPE, (
        f"只在回填里：{ {k: v for k, v in backfilled.items() if SIGNAL_TYPE_BY_RISK_TYPE.get(k) != v} }\n"
        f"只在映射表里：{sorted(set(SIGNAL_TYPE_BY_RISK_TYPE) - set(backfilled))}"
    )
