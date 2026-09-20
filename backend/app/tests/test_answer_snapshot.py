"""答卷快照的规范化与摘要 —— 纯函数，不碰数据库。

这一组守的是需求说明书 §17「计算与历史」里的**哈希规范化结果跨服务一致**：
谁算、在哪个进程里算、字典是什么顺序，都必须得到同一串字节。所以下面的用例断言的是
**那一串字节本身**（`canonical_answer_snapshot`），不是一个 64 位的十六进制串——
后者变了，看不出是规范化规则改了、还是答案改了。
"""

import pytest

from app.services.answer_snapshot import (
    ANSWER_HASH_ALGORITHM,
    canonical_answer_snapshot,
    answer_snapshot_hash,
)

MHT = {"scale_code": "MHT", "scale_version": "MHT-1.1.0"}


def test_the_snapshot_is_one_line_per_question_in_question_number_order():
    """四列一行、题号升序。**这一条就是「规范化」的全部内容**，写成逐字断言。"""
    snapshot = canonical_answer_snapshot(**MHT, answer_map={2: "NO", 1: "YES", 10: "YES"})
    assert snapshot == (
        "MHT\x1fMHT-1.1.0\x1f1\x1fYES"
        "\nMHT\x1fMHT-1.1.0\x1f2\x1fNO"
        "\nMHT\x1fMHT-1.1.0\x1f10\x1fYES"
    )


def test_the_snapshot_does_not_depend_on_the_order_the_answers_arrived_in():
    """同一份答卷，两种到达顺序，一串字节。

    「到达顺序」是真实的：`save_answer` 是按学生点哪道题存哪道题的，而
    `submit_session` 从库里读回来时用的是 `select(...).join(...)`——那个次序由
    执行计划决定，不是接口约定。所以「按题号升序」不是整洁，是这个函数成不成立的
    前提：不排序的话，同一份答卷在两个进程里会算出两个哈希，而那个哈希的全部用途
    就是「比对两次是不是同一份」。
    """
    answers = {3: "NO", 1: "YES", 2: "YES", 7: "NO"}
    forwards = canonical_answer_snapshot(**MHT, answer_map=dict(sorted(answers.items())))
    backwards = canonical_answer_snapshot(**MHT, answer_map=dict(sorted(answers.items(), reverse=True)))
    assert forwards == backwards
    # 而且它与「构造函数的顺序」也无关（Python 3.7+ 的 dict 保序，但那是语言细节，
    # 不是这个函数的约定）。
    shuffled = canonical_answer_snapshot(**MHT, answer_map={7: "NO", 2: "YES", 3: "NO", 1: "YES"})
    assert shuffled == forwards


def test_the_snapshot_golden_value():
    """钉住**字节格式与摘要**，不是钉住「函数还活着」。

    这个常量是这一层唯一能挡住「规范化规则被悄悄改宽/改窄」的东西：改分隔符、
    换字段次序、把量表版本去掉，这条就会红，而别的用例（比如「两份不同答卷哈希不同」）
    全都还是会绿。跨服务一致要求的就是这一串字节，所以它值得被写死一次。
    """
    assert (
        answer_snapshot_hash(**MHT, answer_map={1: "YES", 2: "NO"})
        == "2ce8d6454724b6a3cc365082bb43304e1be5097784acc20b110efa274ee66a4b"
    )
    assert ANSWER_HASH_ALGORITHM == "SHA256_CANONICAL_V1"


def test_two_different_answer_sheets_never_share_a_hash():
    """换一道题的答案，摘要就变——十六进制串是 64 位，比这更细的碰撞不去证明。"""
    first = answer_snapshot_hash(**MHT, answer_map={1: "YES", 2: "NO"})
    second = answer_snapshot_hash(**MHT, answer_map={1: "NO", 2: "YES"})
    assert first != second


def test_a_different_scale_version_is_a_different_snapshot():
    """量表版本进哈希，不是陪衬：同一份答案在两个版本下是**两次不同的测量**，
    而 `assessment_session.scale_version` 正是分开它们的那一列。"""
    same_answers = {1: "YES"}
    assert answer_snapshot_hash(**MHT, answer_map=same_answers) != answer_snapshot_hash(
        scale_code="MHT", scale_version="MHT-1.2.0", answer_map=same_answers
    )


@pytest.mark.parametrize("answer", ["YES\x1fNO", "YES\nNO", ""])
def test_an_answer_that_could_forge_a_separator_is_refused(answer):
    """**拒绝，而不是替换**。替换会把两份不同的答卷映射到同一串字节——而那正是
    这个函数最不能出的错：它不报错，只是让两条记录看起来是同一份。"""
    with pytest.raises(ValueError):
        canonical_answer_snapshot(**MHT, answer_map={1: answer})


def test_a_question_number_that_is_not_an_integer_is_refused():
    """题号必须是整数：字符串键下 `sorted` 排的是**字典序**，于是第 10 题会排在
    第 2 题前面（`"10" < "2"`）。那不会报错，只会让规范化规则随调用方的键类型而变。"""
    with pytest.raises(ValueError):
        canonical_answer_snapshot(**MHT, answer_map={"1": "YES"})  # type: ignore[dict-item]


def test_an_empty_answer_sheet_is_a_valid_snapshot_of_nothing():
    """空答卷算得出摘要，而且它到不了库里——评分前就被引擎以 `ANSWERS_INCOMPLETE`
    挡下来了（见 `score_session`）。这里只钉住它不是异常。"""
    assert len(answer_snapshot_hash(**MHT, answer_map={})) == 64
