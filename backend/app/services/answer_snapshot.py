"""答卷快照的**规范化**字节串与它的 SHA-256 —— 纯函数，不碰数据库。

`assessment_session.answer_snapshot_hash` 要回答的是：**库里这一行评分结果，是按哪一
份答卷算出来的**。人工复核不得修改原始答卷（CLAUDE.md §1），但「重答」是存在的路径
（`POST /assessment-sessions/{id}/reset` 之后重答，以及将来重导一份文件），而原始答卷
层不保留版本——被覆盖的那一份就没了。所以结果落库时把当时的答卷按一个**固定的规范化
规则**算成摘要，连同算法名一起存下来（`answer_hash_algorithm`）。

规范化（canonicalisation）是这里唯一重要的东西，三条：

- **按题号升序**。字典的迭代序不是接口约定（Python 的插入序、JSON 的键序都不保证），
  同一份答卷在两个进程里必须算出同一串字节，否则它证明不了任何事。
- **每行四个字段**：量表编码 / 量表版本 / 题号 / 答案，字段之间用 ASCII 单元分隔符
  （`FIELD_SEPARATOR`，U+001F），行之间用换行。用不可打印字符而不是逗号或竖线，是因为
  那几个字符都可能在编码或答案里出现，而「无分隔歧义」是这个函数**唯一**的不变量：
  两个不同的答卷必须算出不同的字节串。字段里出现分隔符时当场抛（见 `_clean_field`），
  而不是顺手替换掉——替换会把两份不同的答卷映射到同一串字节，那正是要防的那件事。
- **算法名一起存**。只存摘要的话，换算法之后那些历史行就再也解释不了了：同一个
  `answer_map` 在新算法下会算出一个对不上的值，而库里没有任何东西说明它当初是怎么算的。

**刻意不进这个哈希的东西**：学生 id、会话 id、时间戳。这一串字节说的是「这份答卷」，
不是「这一次作答」——把 `session_id` 拌进去之后，同一份答案在两个会话里算出来不同，
而它本来要回答的问题（「答案有没有被动过」）与那无关。
"""

import hashlib

#: 存进 `assessment_session.answer_hash_algorithm`（VARCHAR(32)，14 个字符够放）。
#: `_V1` 是必要的后缀：规则一旦要改（比如把量表编码换成规则版本），老哈希仍然是老算法
#: 算的，而那一列是唯一说明得了它的事的东西。
ANSWER_HASH_ALGORITHM = "SHA256_CANONICAL_V1"

#: ASCII 单元分隔符与换行 —— 见模块 docstring。
FIELD_SEPARATOR = "\x1f"
LINE_SEPARATOR = "\n"


def _clean_field(label: str, value: object) -> str:
    """取一个可字段化的值；分隔符出现在里面就抛。

    不替换成别的东西：替换会让 `\"A|B\"` 与 `\"A\" + \"B\"` 撞上同一串字节，而
    「两份答卷算出同一个哈希」正是这个函数最不能出的错——它不报错、只是让两条记录
    看起来是同一份。
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"答卷快照的{label}必须是非空字符串，拿到 {value!r}")
    if FIELD_SEPARATOR in value or LINE_SEPARATOR in value:
        raise ValueError(f"答卷快照的{label}里不能出现分隔符（U+001F / 换行）：{value!r}")
    return value


def canonical_answer_snapshot(
    *, scale_code: str, scale_version: str, answer_map: dict[int, str]
) -> str:
    """把一份答卷写成一串确定的字节（真正的计算在 `answer_snapshot_hash`）。

    单独一个函数是为了让「规范化」这件事可读、可测：测试直接断言这串字节的形状，
    而不是只看一个 64 位的十六进制串——后者变了，看不出是规范化规则改了还是答案改了。
    """
    scale_code = _clean_field("量表编码", scale_code)
    scale_version = _clean_field("量表版本", scale_version)
    lines = []
    for question_no in sorted(answer_map):
        # 题号必须是整数：字典的键若是字符串，`sorted` 排的是**字典序**，
        # 于是第 10 题排在第 2 题前面（"10" < "2"）。那不会报错，只会让规范化
        # 规则随调用方的键类型而变——两份一样的答卷算出两个哈希。
        if not isinstance(question_no, int) or isinstance(question_no, bool):
            raise ValueError(f"答卷快照的题号必须是整数，拿到 {question_no!r}")
        answer = _clean_field(f"第 {question_no} 题的答案", answer_map[question_no])
        lines.append(
            FIELD_SEPARATOR.join((scale_code, scale_version, str(question_no), answer))
        )
    return LINE_SEPARATOR.join(lines)


def answer_snapshot_hash(
    *, scale_code: str, scale_version: str, answer_map: dict[int, str]
) -> str:
    """64 个十六进制字符（sha256），正好是 `answer_snapshot_hash` 那一列的宽度。

    一份答案都没有时算出的是空串的摘要——一个合法的值，而它到不了库里：
    空答卷在评分前就被引擎以 `ANSWERS_INCOMPLETE` 挡下来了（见 `score_session`）。
    """
    snapshot = canonical_answer_snapshot(
        scale_code=scale_code, scale_version=scale_version, answer_map=answer_map
    )
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
