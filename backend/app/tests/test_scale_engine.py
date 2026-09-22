import pytest

from app.core.errors import AppError
from app.scale_engine.engine import AnswerValue, ScaleEngine, default_mht_questions


def answers(value: str = AnswerValue.NO.value) -> dict[int, str]:
    return {question_no: value for question_no in range(1, 101)}


VALIDITY_QUESTION_NUMBERS = [82, 84, 86, 88, 90, 92, 94, 96, 98, 100]


def content_question_numbers() -> list[int]:
    """那 90 道**非效度题**的题号。

    2026-09-21 起总分把效度题也计入，所以这个名单不再是「总分数哪些题」。它留着是因为
    需要「只动总分、不动效度分」的夹具——下面 `test_total_score_boundary` 就是那样：
    它要在一个 `validity_score == 0` 的答卷上量总分分档，否则效度分会顺带被推高
    到 `RETEST_RECOMMENDED`，那条用例量的就不再只是「总分落哪一档」。
    """
    return [question_no for question_no in range(1, 101) if question_no not in VALIDITY_QUESTION_NUMBERS]


def calculation_for(answer_map: dict[int, str]):
    return ScaleEngine().calculate(default_mht_questions(), answer_map)


def test_all_no_scores_zero():
    result = calculation_for(answers())
    assert result.total_score == 0
    assert result.validity_score == 0
    assert result.total_level == "GENERAL_RANGE"
    assert result.validity_status == "VALID"


def test_all_yes_scores_one_hundred_including_validity():
    """全部答「是」：总分 100、效度分 10。

    总分把**效度题也计入**（2026-09-21 改，见 `app/scale_engine/engine.py` 的模块
    docstring）——那十道题同时进两个数，因为它们回答的是两个不同的问题：总分说
    「他自评的困扰有多少」，效度分说「这份答卷可不可信」。一份答卷两个数并存。
    所以这里断的是 100 与 10，改动**之前**是 90 与 10。
    """
    result = calculation_for(answers(AnswerValue.YES.value))
    assert result.total_score == 100
    assert result.validity_score == 10
    assert result.total_level == "KEY_ATTENTION"
    assert result.validity_status == "RETEST_RECOMMENDED"


def test_only_validity_questions_answered_yes_still_count_toward_the_total():
    """只答效度题：总分是 **10** 而不是 0。

    这是新旧口径分得开的那一处。只答非效度题时两个口径给出同一个数（都等于答了几道），
    **只有效度题被答「是」时才分岔**——所以这条不是上面那条的附带样例，它就是这次
    口径变更的判据本身。变异：把求和集合改回「排除效度题」，这条红、上面那条也红，
    而 `test_total_score_boundary` 仍然绿（它一个效度题都不答）。
    """
    answer_map = answers()
    for question_no in VALIDITY_QUESTION_NUMBERS:
        answer_map[question_no] = AnswerValue.YES.value
    result = calculation_for(answer_map)
    assert result.total_score == 10
    assert result.validity_score == 10


@pytest.mark.parametrize(
    ("validity_score", "expected_status"),
    [(6, "VALID"), (7, "RETEST_RECOMMENDED")],
)
def test_validity_boundary(validity_score, expected_status):
    answer_map = answers()
    for question_no in VALIDITY_QUESTION_NUMBERS[:validity_score]:
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

