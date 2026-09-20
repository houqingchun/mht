"""§18.8 的四种来源处置，各自在库里留下什么。

**判定那一半在 `test_assessment_import_api.py`**（`test_a_row_that_collides_with_an_online_sheet_needs_a_decision`：
状态是 `CONFLICT`、原因码是 `IN_SYSTEM_RESULT`，以及它不被整批的「覆盖」放行）。
这一份管的是另一半：人从四档里选了之后，**库里到底变成了什么样**。

一份文件同时说两件事时，最省事的写法是「谁新用谁」——而这条路唯一不能发生的事，
正是**静默**地作废一份学生本人作答的卷子（§20#10 / #11）。所以四档的核心判据是
同一句话：**原始答卷一条都不许丢**。四档里有两档（`USE_EXTERNAL` / `KEEP_BOTH`）
会新建一场外部会话并把它写进 `assessment_session`，那两场都留着；另两档连会话都不建，
外部那一份只留 `assessment_external_result` 上的原始事实。**没有任何一档删过行。**

四档在库里只有三处差别，一档一行：

| 处置 | 外部会话 | 在线会话 `is_effective` | `verification_status` | `supersedes_session_id` |
|---|---|---|---|---|
| `KEEP_ONLINE`     | 不建 | 保持 1 | `PENDING`  | NULL |
| `USE_EXTERNAL`    | 建，`is_effective=1` | **0** | `ACCEPTED` | **指向新场** |
| `REJECT_EXTERNAL` | 不建 | 保持 1 | `REJECTED` | NULL |
| `KEEP_BOTH_BUT_ONE_EFFECTIVE` | 建，`is_effective=0` | 保持 1 | `ACCEPTED` | NULL |

`KEEP_ONLINE` 与 `REJECT_EXTERNAL` 只差最后一列（`PENDING` vs `REJECTED`），
而那正是它们必须分开的理由：「还没定」与「已经否了」是两件事，后者不该在下一次
导入时又被问一遍。`USE_EXTERNAL` 与 `KEEP_BOTH` 只差「谁有效」——「这份数据我们认」
与「这份数据算作当前结果」是两个问题。

**没有在线会话时四档里只有一档成立**（`KEEP_ONLINE` / `REJECT_EXTERNAL` /
`KEEP_BOTH` 都在回答「怎么处置另一份」，而另一份不存在），所以那种行根本不进
`CONFLICT`——它走 V1.0 那条老路。这一条由 `test_a_matched_row_...` 一族钉住，
不在这一份里。
"""

from sqlalchemy import func, select

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    effective_session_predicate,
)
from app.models.importing import AssessmentExternalResult, AssessmentImportRow
from app.models.organization import Student
from app.models.scale import AssessmentScale, ScaleQuestion
from app.services.assessment_service import now_utc_naive, score_session
from app.tests.factories import make_sitting
from app.tests.test_assessment_import_api import (
    _commit,
    _csv,
    _preview_data,
    _row,
    _task_with_targets,
    add_students,
    admin,
    counselor,
)

# 四档的编码逐字写死在这一份里，**不从服务模块 import 常量**。
# 它们是 §18.4 那张冻结表里的名字（`KEEP_ONLINE` / `USE_EXTERNAL` /
# `REJECT_EXTERNAL` / `KEEP_BOTH_BUT_ONE_EFFECTIVE`），是接口上的契约；
# import 常量的话，改一个字母两边一起变，测试就再也不会红。
KEEP_ONLINE = "KEEP_ONLINE"
USE_EXTERNAL = "USE_EXTERNAL"
REJECT_EXTERNAL = "REJECT_EXTERNAL"
KEEP_BOTH = "KEEP_BOTH_BUT_ONE_EFFECTIVE"
RESOLVE = "/api/v1/assessment-import-rows/{row_id}/resolve"

# 在线那一场答了多少题（`_answer_sheet` 的默认值是 100，正在作答那一档写 3 题）。
# 写成常量而不是散落的字面量：下面的断言全是「一条都不许丢」，丢的判据就是它。
ONLINE_ANSWERS = 100
IN_PROGRESS_ANSWERS = 3


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def _online_sheet(db_session, task, *, student_no="S701", status="SUBMITTED") -> AssessmentSession:
    """这名学生在这场比赛里自己答的那一场。

    `SUBMITTED` 是 §18.8 里「在线答卷已提交」那一档的输入；`IN_PROGRESS` 是
    「在线答题进行中」那一档（§20#10）。两档的 `conflict_code` 不同，而**可选的
    处置也不同**——后者不接受 `USE_EXTERNAL`。

    **答过的题由调用方写**（`_answer_sheet`，`SUBMITTED` 时再 `_score` 一遍）：
    四档的核心判据是「原始答卷一条都不许丢」，而一份一题都没答的卷子证明不了这件事
    （`assert count == 0` 在没修的实现上也是真的）。
    """
    student = db_session.scalar(select(Student).where(Student.student_no == student_no))
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    session = make_sitting(
        db_session,
        student,
        scale=scale,
        task_id=task.id,
        source="IN_SYSTEM",
        status=status,
        # `submitted_at` 是**两档的分水岭**：`_existing_session_for_match` 拿它判
        # 「正在作答」还是「已经交卷」，而两档的可选处置不同（§20#10）。
        submitted_at=now_utc_naive() if status == "SUBMITTED" else None,
    )
    db_session.commit()
    return session


def _answer_sheet(
    db_session, session: AssessmentSession, *, count: int = 100
) -> dict[int, str]:
    """给这一场写 `count` 题答案（一律 `NO`），返回 `{题号: 答案}`。

    两点都不是随手写的：

    - **`score` 不能省**（`assessment_answer.score` 是 NOT NULL）：它由
      `assessment_service.save_answer` 现算（`1 if answer == "YES" else 0`），而这里
      绕过了那个函数直接写行——漏了它 MySQL 会在 1048 上拦下来，报出来是一句关于列的
      英文，与「这一行想表达什么」无关。
    - **全 `NO`**：MHT 的重点题是 85 / 97，全 `NO` 的卷子一道都不命中、总分落在最低
      那一档，所以 `score_session` 不会顺带开出待办与关怀档案。这一份测的是导入那一侧，
      待办不该在这里自己冒出来。
    """
    questions = db_session.scalars(
        select(ScaleQuestion)
        .where(ScaleQuestion.scale_id == session.scale_id)
        .order_by(ScaleQuestion.question_no)
        .limit(count)
    ).all()
    assert len(questions) == count, f"量表里只取到 {len(questions)} 题，写不出这一份卷子"
    for question in questions:
        db_session.add(
            AssessmentAnswer(
                session_id=session.id,
                question_id=question.id,
                answer="NO",
                score=0,
                answered_at=now_utc_naive(),
            )
        )
    db_session.commit()
    return {question.question_no: "NO" for question in questions}


def _score(db_session, session: AssessmentSession, answer_map: dict[int, str]) -> None:
    """把在线那一场真的算一遍，落一行 `assessment_result`。

    `KEEP_BOTH` 那一档的判据是「两份结果都在，只有一份算数」，而
    `latest_result_subquery` 是**内连接**结果表的：少了这一行，那名学生在这套口径下
    什么都取不到——于是「外部那份被排除了」与「本来就什么都没查着」在库里长得一模一样，
    测试也就证不了它在排除什么。

    走的是生产那一条（`score_session`），不是自己拼一行结果：拼出来的那一行不必满足
    「一份答卷怎么变成结果」的任何约束，而这一份夹具的价值正在于它是真的。
    """
    assert score_session(db_session, session, answer_map) is True, "在线那一场没算出来"
    db_session.commit()


def _conflict_batch(client, db_session, *, student_no="S701", status="SUBMITTED"):
    """一份「学生自己答过、学校又导进来一份」的文件，停在**待处置**上。

    返回 `(task, 在线那一场, 预览的 data)`。预览那一步会真的建批次与逐行明细，
    所以调用方拿到的 `data["rows"][0]["id"]` 就是那一行的 id。
    """
    headers = admin(client)
    add_students(client, headers, [(student_no, "赵同学", "男", 12)])
    task = _task_with_targets(db_session, [student_no])
    online = _online_sheet(db_session, task, student_no=student_no, status=status)
    if status == "SUBMITTED":
        # 交过卷的那一档：真答 100 题、真算一遍。这是 §18.8 里「在线答卷已提交」
        # 那一档在生产里的形状（学生交卷 → `submit_session` → `score_session`）。
        _score(db_session, online, _answer_sheet(db_session, online))
    else:
        # 正在作答的那一档只有零散几题——一份答到一半的卷子本来就不该有结果，
        # 而它的原始答题仍然要留下（`IN_PROGRESS` 那一档同样一条都不许丢）。
        _answer_sheet(db_session, online, count=3)

    data = _preview_data(
        client,
        counselor(client),
        _csv([_row("赵同学", 2, 12, 1, 4)]),
        task_id=task.id,
    )
    # `conflict` 是 `needing_resolution` 的一个**子集**（第 6 期的第四个计数）：这一条
    # 待确认的行正是一条来源冲突，所以两个数都是 1——而界面上那两个「覆盖 / 放弃」
    # 单选项管得着的行数是 `needing_resolution - conflict`，也就是 0（见 `batch_row_counts`）。
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 1, "conflict": 1, "error": 0}, data["rows"]
    assert data["rows"][0]["match_status"] == "CONFLICT", data["rows"][0]
    return task, online, data


def _resolve(client, row_id: int, choice: str):
    return client.patch(
        RESOLVE.format(row_id=row_id),
        headers=counselor(client),
        json={"conflict_resolution": choice},
    )


def _resolve_ok(client, row_id: int, choice: str) -> dict:
    response = _resolve(client, row_id, choice)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _commit_ok(client, batch_id: int) -> dict:
    response = _commit(client, counselor(client), batch_id)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _sessions(db_session, task) -> list[AssessmentSession]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(AssessmentSession)
            .where(AssessmentSession.task_id == task.id)
            .order_by(AssessmentSession.id)
        )
    )


def _external(db_session, student_no="S701") -> AssessmentExternalResult:
    db_session.expire_all()
    student = db_session.scalar(select(Student).where(Student.student_no == student_no))
    return db_session.scalar(
        select(AssessmentExternalResult).where(
            AssessmentExternalResult.student_id == student.id
        )
    )


def _answer_count(db_session, session_id: int) -> int:
    db_session.expire_all()
    return db_session.scalar(
        select(func.count(AssessmentAnswer.id)).where(
            AssessmentAnswer.session_id == session_id
        )
    )


# --------------------------------------------------------------------------
# 四档各一条
# --------------------------------------------------------------------------


def test_keep_online_leaves_the_students_own_sheet_alone(client, db_session):
    """『保留在线』：外部那一份**连会话都不建**，只在 `PENDING` 上挂着。

    `PENDING` 而不是 `REJECTED` 是这一档的全部意思：「还没定」。两者在建不建会话、
    谁有效上完全一样，唯一差别就是那一列——如果它也写 `REJECTED`，那么「这一次
    先不裁」与「这一份数据我否掉了」就再也分不开了，而下一次导入会把后者当成已决定。
    """
    task, online, data = _conflict_batch(client, db_session)
    resolved = _resolve_ok(client, data["rows"][0]["id"], KEEP_ONLINE)
    assert resolved["conflict_resolution"] == KEEP_ONLINE
    # 处置**不写测评记录**：库里此刻仍然只有在线那一场
    assert len(_sessions(db_session, task)) == 1

    result = _commit_ok(client, data["id"])
    assert (result["created"], result["not_applied"]) == (0, 1)

    # 只有在线那一场，而它照旧有效；答案一条没丢
    sessions = _sessions(db_session, task)
    assert len(sessions) == 1
    assert sessions[0].source == "IN_SYSTEM"
    assert sessions[0].is_effective is True
    assert sessions[0].supersedes_session_id is None
    assert _answer_count(db_session, online.id) == ONLINE_ANSWERS, "原始答卷一条都不许丢"

    external = _external(db_session)
    assert external.verification_status == "PENDING"
    assert external.applied_session_id is None


def test_reject_external_says_so_and_that_is_the_difference(client, db_session):
    """『否掉外部』与上一档**只差一列**，而那一列正是它存在的理由。

    两档在会话上逐字相同（不建、在线保持有效、不指新场），差异全在
    `verification_status`：`REJECTED` 是终态，事后看得出「这一份学校明确不要」，
    而 `PENDING` 说的是「还没人裁过」。**这一条用例的全部价值就是把这个差别钉住**
    ——少了它，两档可以合并成一个，而那会让一次导入把上次的否决重新变成待定。
    """
    task, online, data = _conflict_batch(client, db_session)
    _resolve_ok(client, data["rows"][0]["id"], REJECT_EXTERNAL)

    result = _commit_ok(client, data["id"])
    assert (result["created"], result["not_applied"]) == (0, 1)

    sessions = _sessions(db_session, task)
    assert len(sessions) == 1, "与 KEEP_ONLINE 一样：外部那一份不建会话"
    assert sessions[0].is_effective is True
    assert _answer_count(db_session, online.id) == ONLINE_ANSWERS, "原始答卷一条都不许丢"

    external = _external(db_session)
    assert external.verification_status == "REJECTED"
    assert external.applied_session_id is None


def test_use_external_demotes_the_online_sheet_without_losing_it(client, db_session):
    """『采用外部』：外部那一场顶上来，**在线那一场降级但一行都不删**。

    三处留痕一起断言，因为它们回答的是三个不同的问题：
    `is_effective` 说「哪一场算数」（§11 那族口径的读者），
    `supersedes_session_id` 说「是谁顶掉了它」（模型注释：写在**旧行**上指**新行**），
    `verification_status=ACCEPTED` 说「学校认了外部那一份」。

    而**原始答卷仍然在**——它是这条路上唯一不能丢的东西（§20#11 要的正是「不能被
    **静默**覆盖」；明确选了 `USE_EXTERNAL` 就不算静默，前提是那一份还查得到）。
    """
    task, online, data = _conflict_batch(client, db_session)
    _resolve_ok(client, data["rows"][0]["id"], USE_EXTERNAL)

    result = _commit_ok(client, data["id"])
    assert (result["created"], result["not_applied"]) == (1, 0)

    sessions = _sessions(db_session, task)
    assert len(sessions) == 2, "两场都在：外部的 + 学生自己那份"
    imported = [s for s in sessions if s.source == "IMPORTED"]
    assert len(imported) == 1
    new = imported[0]

    assert (new.is_effective, new.attempt_no) == (True, 2), (
        "外部那一场有效，而且是这名学生在这场比赛里的第二次作答——"
        "`attempt_no` 不写 2 就会撞 `uq_session_task_student_attempt`"
    )
    db_session.refresh(online)
    assert online.is_effective is False, "在线那一场退位"
    assert online.supersedes_session_id == new.id, "留痕：旧行指新行"
    assert _answer_count(db_session, online.id) == ONLINE_ANSWERS, "原始答卷一条都不许丢"

    external = _external(db_session)
    assert external.verification_status == "ACCEPTED"
    assert external.applied_session_id == new.id, "外部结果指到它落成的那一场"

    # 外部那一场**真的被写全了**（答卷 + 结果），不是只有一行会话壳
    assert _answer_count(db_session, new.id) == 100
    assert db_session.scalar(
        select(func.count(AssessmentResult.id)).where(AssessmentResult.session_id == new.id)
    ) == 1


def test_keep_both_keeps_two_results_and_one_of_them_counts(client, db_session):
    """『两份都留但以在线为准』：**两份都是真的，只有一份算数**。

    它是四档里唯一同时满足「外部建立了完整结果」与「在线仍然是当前状态」的那一档，
    所以它也是唯一能证明 `ACCEPTED` 与 `is_effective` 是**两个问题**的那一档：
    外部那一份被采纳了（`ACCEPTED`），却故意不作数（`is_effective=0`）。
    两者合成一个字段时，这一档就没有表达方式。
    """
    task, online, data = _conflict_batch(client, db_session)
    _resolve_ok(client, data["rows"][0]["id"], KEEP_BOTH)

    result = _commit_ok(client, data["id"])
    assert (result["created"], result["not_applied"]) == (1, 0)

    sessions = _sessions(db_session, task)
    assert len(sessions) == 2
    new = [s for s in sessions if s.source == "IMPORTED"][0]
    assert (new.is_effective, new.attempt_no) == (False, 2)
    db_session.refresh(online)
    assert online.is_effective is True, "有效的仍然是在线那一场"
    assert online.supersedes_session_id is None, "没有谁被顶掉"

    # 两份结果都在（外部那一份照写照评分），只是作数的只有一份
    for sitting in sessions:
        assert db_session.scalar(
            select(func.count(AssessmentResult.id)).where(
                AssessmentResult.session_id == sitting.id
            )
        ) == 1, "两场各自独立评分"
    assert _answer_count(db_session, online.id) == ONLINE_ANSWERS, "原始答卷一条都不许丢"

    # 「只有一份算数」要用**生产那个谓词**来断言，不是再看一眼 `is_effective`：
    # `effective_session_predicate()` 是那六处读者（个案详情、重点学生、导出、工作台、
    # 统计分析、全部学生）收缩「当前状态」口径的**唯一**入口，而上面那两行只是它的
    # 输入。少了这一条，谓词哪天被改成恒真（或者忘了带上 `task_id`）全绿——而
    # 症状会是「这位学生的关注等级来自一份学校还没决定认不认的外部平台分数」。
    db_session.expire_all()
    counting = db_session.scalars(
        select(AssessmentSession).where(
            AssessmentSession.task_id == task.id,
            AssessmentSession.student_id == online.student_id,
            effective_session_predicate(),
        )
    ).all()
    assert [s.id for s in counting] == [online.id], "作数的只有学生自己那一场"

    external = _external(db_session)
    assert external.verification_status == "ACCEPTED", "采纳了，只是不作数"
    assert external.applied_session_id == new.id


# --------------------------------------------------------------------------
# §20#10 / #11：两条必须被挡住的时机
# --------------------------------------------------------------------------


def test_an_in_progress_sheet_cannot_be_replaced_by_an_external_result(client, db_session):
    """§20#10：在线答题**进行中**时导入外部结果，不得把它顶掉。

    拒绝的不是「以外部为准」这个决定本身——学生交卷之后同一行会变成另一种冲突，
    那时四种处置都能选——是**时机**：他可能正答到第 40 题，作废那一场等于把他手里的
    工作扔掉，而屏幕上没有任何东西能把那个代价说出来（那一份还没有交卷、没有结果、
    没有分数可比）。

    所以三档照旧可选，只有 `USE_EXTERNAL` 被挡；而**被挡的那一刻库里什么都没变**。
    """
    task, online, data = _conflict_batch(client, db_session, status="IN_PROGRESS")
    row_id = data["rows"][0]["id"]
    assert data["rows"][0]["conflict_code"] == "IN_SYSTEM_IN_PROGRESS"

    refused = _resolve(client, row_id, USE_EXTERNAL)
    assert refused.status_code == 422, refused.text
    message = refused.json()["error"]["message"]
    assert "还没有交卷" in message and "不能以外部结果顶掉" in message

    # 拒绝发生在**任何写入之前**：那一行仍然没被处置过，而答卷一条没动
    db_session.expire_all()
    row = db_session.get(AssessmentImportRow, row_id)
    assert row.conflict_resolution is None
    assert row.resolved_by is None, "被拒时不写「谁处置的」——那会让人以为这一行已经定了"
    assert len(_sessions(db_session, task)) == 1
    # 这一档只有零星几题（正在作答），而**那几题一条都不许丢**——「还没交卷」
    # 不是「这份卷子可以扔」。
    assert _answer_count(db_session, online.id) == IN_PROGRESS_ANSWERS, "已答的题一条都不许丢"

    # 而另外三档照旧可选——这一档挡的是时机，不是这一行的出路
    for choice in (KEEP_ONLINE, REJECT_EXTERNAL, KEEP_BOTH):
        assert _resolve(client, row_id, choice).status_code == 200, choice


def test_a_submitted_sheet_is_never_silently_overwritten(client, db_session):
    """§20#11：已提交的在线答卷，**整批的「覆盖」也不许动它**。

    `test_assessment_import_api.py` 那一条已经钉住「没处置就提交 → 422」。
    这一条钉的是它的反面：**人明确选了之后**，那一场卷子仍然一行不删地活着——
    否则「不许静默覆盖」会退化成「不许覆盖」，而 §18.8 恰恰给了 `USE_EXTERNAL`
    这一档（学校真的可能需要以平台那份为准）。

    判据是三条一起：在线那一场不是被**改写**（它的答案还是 1 条、它的 id 没变、
    `source` 还是 `IN_SYSTEM`），只是 `is_effective` 被置 0。
    """
    task, online, data = _conflict_batch(client, db_session)
    online_id = online.id
    _resolve_ok(client, data["rows"][0]["id"], USE_EXTERNAL)
    _commit_ok(client, data["id"])

    db_session.expire_all()
    survivor = db_session.get(AssessmentSession, online_id)
    assert survivor is not None, "在线那一场被删了——这正是这条路唯一不能发生的事"
    assert survivor.source == "IN_SYSTEM"
    assert survivor.student_id == online.student_id
    assert _answer_count(db_session, online_id) == ONLINE_ANSWERS, "原始答卷一条都不许丢"

    # 而**没有任何答案被搬到别处**：外部那一场是自己的一百题，不是这一条
    imported = [s for s in _sessions(db_session, task) if s.source == "IMPORTED"][0]
    assert imported.id != online_id
    assert _answer_count(db_session, imported.id) == ONLINE_ANSWERS


# --------------------------------------------------------------------------
# 处置与整批选择的交界
# --------------------------------------------------------------------------


def test_the_batch_choice_never_reaches_a_conflict_row(client, db_session):
    """整批的「覆盖」对一个**已经逐行处置过**的冲突行无效。

    这一条与 `test_assessment_import_api.py` 那条互补：那一条是「没处置时整批覆盖
    被挡」，这一条是「处置过之后，整批覆盖不会**改写**那个决定」。少了它，
    `commit_batch` 里那一支 `row.resolution or resolution` 一旦被顺手套到冲突行上，
    屏幕上那个人选的「保留在线」会被旁边那个整批按钮悄悄换成别的档——而两步都成功。
    """
    task, online, data = _conflict_batch(client, db_session)
    _resolve_ok(client, data["rows"][0]["id"], KEEP_ONLINE)

    # 整批选「覆盖」提交：对冲突行无效（它不是「年龄不符」那一类）
    response = _commit(client, counselor(client), data["id"], resolution="overwrite")
    assert response.status_code == 200, response.text

    db_session.expire_all()
    row = db_session.scalar(
        select(AssessmentImportRow).where(AssessmentImportRow.batch_id == data["id"])
    )
    assert row.conflict_resolution == KEEP_ONLINE, "人选的档还在"
    assert row.resolution is None, "整批的覆盖没有落到这一行上"
    assert len(_sessions(db_session, task)) == 1, "仍然只有在线那一场"


def test_a_row_without_a_conflict_rejects_the_four_options(client, db_session):
    """没有来源冲突的行不接受四档处置。

    它们是「另一份」不存在的那种行，而四档**每一档都在回答「怎么处置另一份」**。
    给它们写上一个处置，留下的是一条谁也解释不了的决定——而这一列的全部意义就是
    「这一行当时问了什么、人怎么答的」。

    **两句挡在前面的提示都要断言，因为它们挡的不是同一种行**，而只测其中一条会很
    容易让人以为另一条不存在：

    - 直接能进的行（`MATCHED`）被**更靠前**的那一句挡下来（「不需要确认，直接提交
      即可」）——它比「没有来源冲突」更有用：那一行的人该做的不是选处置，是提交；
    - 要拍板、但拍的**不是这一块板**的行（`AGE_CONFLICT`）才走到
      「没有来源冲突，不需要选择处置方式」——`_needs_row_confirmation` 对这两类
      都返回真，所以界面上它们是并排的两种，而它们各自要回答的问题不同。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])

    matched = _preview_data(client, counselor(client), _csv([_row("赵同学", 2, 12, 1, 4)]))
    assert matched["rows"][0]["match_status"] == "MATCHED"
    refused = _resolve(client, matched["rows"][0]["id"], KEEP_ONLINE)
    assert refused.status_code == 422, refused.text
    assert "不需要确认" in refused.json()["error"]["message"]

    # 文件里的年龄与名册不等（12 → 13）就是 `AGE_CONFLICT`：它**要**人拍板，
    # 而拍的是「名册上的年龄要不要按文件更新」，与「以哪一份为准」无关。
    aged = _preview_data(client, counselor(client), _csv([_row("赵同学", 2, 13, 1, 4)]))
    assert aged["rows"][0]["match_status"] == "AGE_CONFLICT", aged["rows"][0]
    refused = _resolve(client, aged["rows"][0]["id"], KEEP_ONLINE)
    assert refused.status_code == 422, refused.text
    assert "没有来源冲突" in refused.json()["error"]["message"]


def test_an_unknown_choice_is_refused_with_the_four_names(client, db_session):
    """认不出的档 → 422，而**那句话里把四个名字都写着**。

    这一列是接口契约，界面上那四个按钮是它唯一的调用者；所以这句提示的读者不是
    用户，是下一个改这一处的人——他要能从中读出合法的取值有哪几个，而不是去翻服务层。
    """
    task, online, data = _conflict_batch(client, db_session)
    refused = _resolve(client, data["rows"][0]["id"], "KEEP_WHATEVER")
    assert refused.status_code == 422, refused.text
    message = refused.json()["error"]["message"]
    for name in (KEEP_ONLINE, USE_EXTERNAL, REJECT_EXTERNAL, KEEP_BOTH):
        assert name in message, name


def test_the_resolution_is_written_to_the_audit_trail(client, db_session):
    """四档要留痕（§18.8 最后一句「保留…人工选择」）。

    审计的 `action` 与 `resource_type` 由路由那一层定，`detail` 里带的是
    `conflict_resolution` 的编码——**编码不是中文**，与 `update_permissions` 的例子
    一致（§4）：审计页的搜索匹配的是 `action`，而 `detail` 是给读轨迹的人看的，
    它要能被机器比对。中文映射由界面那一侧负责（`labels.ts`）。
    """
    from app.models.audit import AuditLog

    task, online, data = _conflict_batch(client, db_session)
    _resolve_ok(client, data["rows"][0]["id"], KEEP_ONLINE)

    db_session.expire_all()
    log = db_session.scalar(
        select(AuditLog)
        .where(AuditLog.resource_type == "ASSESSMENT_IMPORT_ROW")
        .order_by(AuditLog.id.desc())
    )
    assert log is not None
    assert f"conflict_resolution={KEEP_ONLINE}" in log.detail
    assert "conflict_resolution=NONE" not in log.detail
