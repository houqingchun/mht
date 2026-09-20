"""MHT scoring engine.

Thresholds live in a `ScaleRuleConfig`, not in this module. That matters because
the bands decide which students are flagged as needing attention: a change to
them must be pinned to a scale version, so a 2026 result stays interpretable
under the rules in force when it was submitted.

`DEFAULT_RULE_CONFIG` reproduces the values these functions used to hardcode, so
an unconfigured installation scores exactly as before.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from app.core.errors import AppError


class AnswerValue(StrEnum):
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True)
class ScoreBand:
    """An inclusive score range mapped to a classification code.

    Bands are scanned in order; a score above every band falls to the last one,
    which keeps the historical `>= 65` / `>= 8` semantics for out-of-range values.
    """

    code: str
    min: int
    max: int


def classify(score: int, bands: tuple[ScoreBand, ...], fallback: str) -> str:
    for band in bands:
        if band.min <= score <= band.max:
            return band.code
    return fallback


@dataclass(frozen=True)
class ScaleRuleConfig:
    question_count: int = 100
    yes_value: str = "YES"
    no_value: str = "NO"
    validity_questions: frozenset[int] = field(default_factory=frozenset)
    key_questions: frozenset[int] = field(default_factory=frozenset)
    validity_retest_threshold: int = 7
    total_bands: tuple[ScoreBand, ...] = ()
    dimension_bands: tuple[ScoreBand, ...] = ()
    # Optional per-level wording; falls back to DEFAULT_INTERPRETATIONS.
    interpretations: Mapping[str, str] = field(default_factory=dict)

    @property
    def validity_band_fallback(self) -> str:
        return self.total_bands[-1].code

    @property
    def dimension_band_fallback(self) -> str:
        return self.dimension_bands[-1].code


DEFAULT_INTERPRETATIONS = {
    "HIGH": "该维度得分较高，建议由授权心理老师结合事实进一步了解。",
    "MEDIUM": "该维度提示可能存在需要进一步了解的倾向。",
    "LOW": "该维度当前处于一般范围。",
}

DEFAULT_RULE_CONFIG = ScaleRuleConfig(
    question_count=100,
    validity_questions=frozenset({82, 84, 86, 88, 90, 92, 94, 96, 98, 100}),
    key_questions=frozenset({85, 97}),
    validity_retest_threshold=7,
    total_bands=(
        ScoreBand("GENERAL_RANGE", 0, 55),
        ScoreBand("NEEDS_ATTENTION", 56, 64),
        ScoreBand("KEY_ATTENTION", 65, 90),
    ),
    dimension_bands=(
        ScoreBand("LOW", 0, 3),
        ScoreBand("MEDIUM", 4, 7),
        ScoreBand("HIGH", 8, 15),
    ),
    interpretations=DEFAULT_INTERPRETATIONS,
)


def rule_config_from_json(raw: Mapping[str, Any] | None) -> ScaleRuleConfig:
    """Build a config from a `scale_rule.config_json` payload.

    Anything absent falls back to the shipped default, so a partially-populated
    (or older) rule row scores the same as before rather than erroring.
    """
    if not raw:
        return DEFAULT_RULE_CONFIG

    # `None` (absent) falls back to the default; an explicit empty list is a
    # deliberate choice and is respected — otherwise an operator could never
    # express "this version has no key questions".
    def bands(key: str, default: tuple[ScoreBand, ...]) -> tuple[ScoreBand, ...]:
        rows = raw.get(key)
        if rows is None:
            return default
        try:
            return tuple(
                ScoreBand(str(row["code"]), int(row["min"]), int(row["max"])) for row in rows
            )
        except (KeyError, TypeError, ValueError):
            return default

    def numbers(key: str, default: frozenset[int]) -> frozenset[int]:
        rows = raw.get(key)
        if rows is None:
            return default
        try:
            return frozenset(int(value) for value in rows)
        except (TypeError, ValueError):
            return default

    interpretations = dict(DEFAULT_INTERPRETATIONS)
    interpretations.update(raw.get("interpretations") or {})

    return ScaleRuleConfig(
        question_count=int(raw.get("question_count") or DEFAULT_RULE_CONFIG.question_count),
        yes_value=str(raw.get("yes_value") or DEFAULT_RULE_CONFIG.yes_value),
        no_value=str(raw.get("no_value") or DEFAULT_RULE_CONFIG.no_value),
        validity_questions=numbers("validity_questions", DEFAULT_RULE_CONFIG.validity_questions),
        key_questions=numbers("key_questions", DEFAULT_RULE_CONFIG.key_questions),
        validity_retest_threshold=int(
            raw.get("validity_retest_threshold")
            or DEFAULT_RULE_CONFIG.validity_retest_threshold
        ),
        total_bands=bands("total_levels", DEFAULT_RULE_CONFIG.total_bands),
        dimension_bands=bands("dimension_levels", DEFAULT_RULE_CONFIG.dimension_bands),
        interpretations=interpretations,
    )


def rule_config_to_json(config: ScaleRuleConfig) -> dict[str, Any]:
    """Serialise a config for `scale_rule.config_json`.

    Every version should store a COMPLETE config. A partial one still scores
    (missing keys fall back to defaults), but the operator would then be editing
    a value the engine never reads, and the version would not be self-describing.
    """
    return {
        "question_count": config.question_count,
        "yes_value": config.yes_value,
        "no_value": config.no_value,
        "validity_questions": sorted(config.validity_questions),
        "key_questions": sorted(config.key_questions),
        "validity_retest_threshold": config.validity_retest_threshold,
        "total_levels": [
            {"code": band.code, "min": band.min, "max": band.max} for band in config.total_bands
        ],
        "dimension_levels": [
            {"code": band.code, "min": band.min, "max": band.max} for band in config.dimension_bands
        ],
        "interpretations": dict(config.interpretations),
    }


@dataclass(frozen=True)
class ScaleQuestionConfig:
    question_no: int
    dimension_code: str | None
    is_validity_question: bool = False
    is_key_question: bool = False


@dataclass(frozen=True)
class DimensionResult:
    dimension_code: str
    score: int
    level: str
    interpretation: str
    rule_version: str


@dataclass(frozen=True)
class RiskEventResult:
    risk_type: str
    risk_level: str
    trigger_rule: str
    question_no: int


# `risk_type`（怎么触发的）→ `signal_type`（这件事归谁办）。V1.2 把这两个粒度拆开，
# 而 `risk_event.signal_type` 是一个**没有默认值**的 NOT NULL 列，所以写风险事件的人
# 必须回答它 —— 这个映射就是那个答案。
#
# 它住在引擎里而不是服务层：`maybe_raise_risk_events` 的既有约定是「什么情况下算重点学生
# 只由 `calculation.risk_events` 回答」，而这一层正是 `risk_events` 的生产者。
# 表的内容**照抄迁移 0013 的回填 CASE 与 DDL 第 539-557 行**——那边是数据里实际存在的
# 那三支，不是我编的业务码（需求说明书 §15）。
SIGNAL_TYPE_BY_RISK_TYPE = {
    "MANUAL_REVIEW_REQUIRED": "MANUAL_REVIEW_REQUIRED",
    "KEY_QUESTION_TRIGGERED": "MANUAL_REVIEW_REQUIRED",
    "RETEST_RECOMMENDED": "RETEST_RECOMMENDED",
    "HIGH_TOTAL_SCORE": "SCREENING_SIGNAL",
    "HIGH_DIMENSION_SCORE": "SCREENING_SIGNAL",
    "SCREENING_SIGNAL": "SCREENING_SIGNAL",
}

# 「这条信号要不要人去看一眼」是 `signal_type` 的函数（DDL 里存成一列是为了让
# 「待复核」走得了索引）。目前只有人工复核那一类要。
SIGNAL_TYPES_REQUIRING_MANUAL_REVIEW = frozenset({"MANUAL_REVIEW_REQUIRED"})


def signal_type_for(risk_type: str) -> str:
    """认不出的 `risk_type` **当场报错**，不猜一个默认值。

    猜的代价是静默的：`signal_type` 写错一档，那条待办就会落进另一类人的队列，
    或者干脆不进任何人的队列——而界面上一切正常。迁移 0013 的 precheck 对
    「认不出的 risk_type」也是中止，这里与它一致。
    """
    try:
        return SIGNAL_TYPE_BY_RISK_TYPE[risk_type]
    except KeyError:
        raise ValueError(
            f"认不出的 risk_type：{risk_type!r}——`SIGNAL_TYPE_BY_RISK_TYPE` 里没有它，"
            f"`risk_event.signal_type` 填不出来（新加 risk_type 时这张表要一起加）"
        ) from None


def requires_manual_review(signal_type: str) -> bool:
    return signal_type in SIGNAL_TYPES_REQUIRING_MANUAL_REVIEW


@dataclass(frozen=True)
class ScaleCalculation:
    scale_version: str
    rule_version: str
    validity_score: int
    validity_status: str
    total_score: int
    total_level: str
    dimension_results: list[DimensionResult]
    risk_events: list[RiskEventResult]


class ScaleEngine:
    def __init__(
        self,
        *,
        scale_version: str = "MHT-1.0.0",
        rule_version: str = "MHT-RULE-1.0.0",
        config: ScaleRuleConfig | None = None,
    ) -> None:
        self.scale_version = scale_version
        self.rule_version = rule_version
        self.config = config or DEFAULT_RULE_CONFIG

    def validate_answers(self, answers: dict[int, str]) -> None:
        expected = self.config.question_count
        if len(answers) != expected:
            raise AppError("ANSWERS_INCOMPLETE", "答题数量不足", 422)
        missing = [no for no in range(1, expected + 1) if no not in answers]
        if missing:
            raise AppError("ANSWERS_INCOMPLETE", "答题数量不足", 422)
        invalid = [
            value
            for value in answers.values()
            if value not in {self.config.yes_value, self.config.no_value}
        ]
        if invalid:
            raise AppError("ANSWER_INVALID", f"答案不是 {self.config.yes_value} 或 {self.config.no_value}", 422)

    def calculate(self, questions: list[ScaleQuestionConfig], answers: dict[int, str]) -> ScaleCalculation:
        self.validate_questions(questions)
        self.validate_answers(answers)

        by_no = {question.question_no: question for question in questions}
        scores = {
            question_no: 1 if answer == self.config.yes_value else 0
            for question_no, answer in answers.items()
        }
        validity_score = sum(scores[question.question_no] for question in questions if question.is_validity_question)
        content_questions = [question for question in questions if not question.is_validity_question]
        total_score = sum(scores[question.question_no] for question in content_questions)

        dimension_results = []
        dimension_codes = sorted({question.dimension_code for question in content_questions if question.dimension_code})
        for dimension_code in dimension_codes:
            dimension_score = sum(
                scores[question.question_no]
                for question in content_questions
                if question.dimension_code == dimension_code
            )
            dimension_results.append(
                DimensionResult(
                    dimension_code=dimension_code,
                    score=dimension_score,
                    level=self.dimension_level(dimension_score),
                    interpretation=self.dimension_interpretation(dimension_score),
                    rule_version=self.rule_version,
                )
            )

        # Key questions come from the rule config, not a literal — and still
        # require the question itself to be flagged, so the two must agree.
        risk_events = [
            RiskEventResult(
                risk_type="MANUAL_REVIEW_REQUIRED",
                risk_level="HIGH_SENSITIVITY",
                trigger_rule=f"KEY_QUESTION_{question_no}_YES",
                question_no=question_no,
            )
            for question_no in sorted(self.config.key_questions)
            if scores.get(question_no) == 1 and by_no[question_no].is_key_question
        ]

        return ScaleCalculation(
            scale_version=self.scale_version,
            rule_version=self.rule_version,
            validity_score=validity_score,
            validity_status=self.validity_status(validity_score),
            total_score=total_score,
            total_level=self.total_level(total_score),
            dimension_results=dimension_results,
            risk_events=risk_events,
        )

    def validate_questions(self, questions: list[ScaleQuestionConfig]) -> None:
        expected = self.config.question_count
        if len(questions) != expected:
            raise AppError("SCALE_INVALID", f"量表必须包含 {expected} 道题目", 422)
        numbers = sorted(question.question_no for question in questions)
        if numbers != list(range(1, expected + 1)):
            raise AppError("SCALE_INVALID", f"题号必须完整覆盖 1-{expected}", 422)
        validity = {question.question_no for question in questions if question.is_validity_question}
        if validity != set(self.config.validity_questions):
            expected_list = "、".join(str(no) for no in sorted(self.config.validity_questions))
            raise AppError("SCALE_INVALID", f"效度题必须为{expected_list}", 422)
        key = {question.question_no for question in questions if question.is_key_question}
        if key != set(self.config.key_questions):
            expected_list = "、".join(str(no) for no in sorted(self.config.key_questions))
            raise AppError("SCALE_INVALID", f"重点题必须为{expected_list}", 422)

    def validity_status(self, validity_score: int) -> str:
        if validity_score >= self.config.validity_retest_threshold:
            return "RETEST_RECOMMENDED"
        return "VALID"

    def total_level(self, total_score: int) -> str:
        return classify(total_score, self.config.total_bands, self.config.validity_band_fallback)

    def dimension_level(self, score: int) -> str:
        return classify(score, self.config.dimension_bands, self.config.dimension_band_fallback)

    def dimension_interpretation(self, score: int) -> str:
        level = self.dimension_level(score)
        return self.config.interpretations.get(level, DEFAULT_INTERPRETATIONS.get(level, ""))


def default_mht_questions() -> list[ScaleQuestionConfig]:
    questions: list[ScaleQuestionConfig] = []
    for question_no in range(1, DEFAULT_RULE_CONFIG.question_count + 1):
        questions.append(
            ScaleQuestionConfig(
                question_no=question_no,
                dimension_code=dimension_for_question(question_no),
                is_validity_question=question_no in DEFAULT_RULE_CONFIG.validity_questions,
                is_key_question=question_no in DEFAULT_RULE_CONFIG.key_questions,
            )
        )
    return questions


def dimension_for_question(question_no: int) -> str | None:
    if 1 <= question_no <= 15:
        return "LEARNING_ANXIETY"
    if 16 <= question_no <= 25:
        return "INTERPERSONAL_ANXIETY"
    if 26 <= question_no <= 35:
        return "LONELINESS"
    if 36 <= question_no <= 45:
        return "SELF_BLAME"
    if 46 <= question_no <= 55:
        return "SENSITIVITY"
    if 56 <= question_no <= 70:
        return "PHYSICAL_SYMPTOMS"
    if 71 <= question_no <= 80:
        return "PHOBIC_TENDENCY"
    if question_no in {81, 83, 85, 87, 89, 91, 93, 95, 97, 99}:
        return "IMPULSIVE_TENDENCY"
    return None
