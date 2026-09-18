import pytest

from app.core.errors import AppError
from app.scale_engine.engine import AnswerValue, ScaleEngine, default_mht_questions


def answers(value: str = AnswerValue.NO.value) -> dict[int, str]:
    return {question_no: value for question_no in range(1, 101)}


def content_question_numbers() -> list[int]:
    validity = {82, 84, 86, 88, 90, 92, 94, 96, 98, 100}
    return [question_no for question_no in range(1, 101) if question_no not in validity]


def calculation_for(answer_map: dict[int, str]):
    return ScaleEngine().calculate(default_mht_questions(), answer_map)


def test_all_no_scores_zero():
    result = calculation_for(answers())
    assert result.total_score == 0
    assert result.validity_score == 0
    assert result.total_level == "GENERAL_RANGE"
    assert result.validity_status == "VALID"


def test_all_yes_scores_ninety_content_and_ten_validity():
    result = calculation_for(answers(AnswerValue.YES.value))
    assert result.total_score == 90
    assert result.validity_score == 10
    assert result.total_level == "KEY_ATTENTION"
    assert result.validity_status == "RETEST_RECOMMENDED"


@pytest.mark.parametrize(
    ("validity_score", "expected_status"),
    [(6, "VALID"), (7, "RETEST_RECOMMENDED")],
)
def test_validity_boundary(validity_score, expected_status):
    answer_map = answers()
    for question_no in [82, 84, 86, 88, 90, 92, 94, 96, 98, 100][:validity_score]:
        answer_map[question_no] = AnswerValue.YES.value
    assert calculation_for(answer_map).validity_status == expected_status


@pytest.mark.parametrize(
    ("total_score", "expected_level"),
    [
        (55, "GENERAL_RANGE"),
        (56, "NEEDS_ATTENTION"),
        (64, "NEEDS_ATTENTION"),
        (65, "KEY_ATTENTION"),
    ],
)
def test_total_score_boundary(total_score, expected_level):
    answer_map = answers()
    for question_no in content_question_numbers()[:total_score]:
        answer_map[question_no] = AnswerValue.YES.value
    result = calculation_for(answer_map)
    assert result.total_score == total_score
    assert result.total_level == expected_level


@pytest.mark.parametrize(
    ("score", "expected_level"),
    [(3, "LOW"), (4, "MEDIUM"), (7, "MEDIUM"), (8, "HIGH")],
)
def test_dimension_score_boundary(score, expected_level):
    # Classification is config-driven now, so it needs an engine instance
    # carrying a rule config rather than a class-level staticmethod.
    assert ScaleEngine().dimension_level(score) == expected_level


@pytest.mark.parametrize("question_no", [85, 97])
def test_key_question_yes_creates_manual_review_event(question_no):
    answer_map = answers()
    answer_map[question_no] = AnswerValue.YES.value
    result = calculation_for(answer_map)
    assert len(result.risk_events) == 1
    assert result.risk_events[0].risk_type == "MANUAL_REVIEW_REQUIRED"
    assert result.risk_events[0].question_no == question_no


def test_key_questions_no_do_not_create_manual_review_event():
    assert calculation_for(answers()).risk_events == []


def test_incomplete_answers_rejected():
    answer_map = answers()
    answer_map.pop(100)
    with pytest.raises(AppError) as exc_info:
        calculation_for(answer_map)
    assert exc_info.value.code == "ANSWERS_INCOMPLETE"


def test_invalid_answer_rejected():
    answer_map = answers()
    answer_map[1] = "MAYBE"
    with pytest.raises(AppError) as exc_info:
        calculation_for(answer_map)
    assert exc_info.value.code == "ANSWER_INVALID"


def test_result_keeps_scale_and_rule_versions():
    result = calculation_for(answers())
    assert result.scale_version == "MHT-1.0.0"
    assert result.rule_version == "MHT-RULE-1.0.0"

