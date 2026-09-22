"""计算幂等、失败留痕与重算 —— 需求说明书 §17「计算与历史」那一组。

五条验收：相同答卷重复计算结果一致 / 计算失败可以安全重试 / 规则版本变化不覆盖
历史结果 / 哈希规范化结果跨服务一致（前六条在 `test_answer_snapshot.py`）/
测评趋势按真实测评时间排序，而不是导入时间排序。

这一组守的是**写入侧**：一份答卷无论被算一次、被重算一次，还是先失败再重算，
库里那一行的形状都必须说得通。
"""

from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    RiskEvent,
)
from app.models.audit import AuditLog
from app.models.care import StudentCareCase
from app.models.organization import Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.services import assessment_service
from app.services.answer_snapshot import ANSWER_HASH_ALGORITHM, answer_snapshot_hash
from app.services.scale_rule_service import MHT_RULE_VERSION
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting
from app.tests.test_assessment_api import create_student_session, save_answers
from app.tests.test_data_scope import make_other_school_student


def submit(client, headers, session_id, key="submit-1"):
    return client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit",
        headers={**headers, "Idempotency-Key": key} if key else headers,
    )


def retry(client, headers, session_id):
    return client.post(f"/api/v1/assessment-sessions/{session_id}/calculate", headers=headers)


def student_by_no(db, student_no="S001") -> Student:
    return db.scalar(select(Student).where(Student.student_no == student_no))


# --- 相同答卷重复计算结果一致 -------------------------------------------------


def test_the_same_sheet_scored_twice_yields_one_identical_result(client, db_session):
    """重发同一个提交请求：结果、风险事件、答卷快照摘要**一个都不变，一个都不多**。

    与 `test_assessment_api.py` 那条「重发返回既有结果」的区别是判据放在**摘要**上：
    那一条钉的是「没有第二条结果行」，这一条钉的是「两次算出来的东西是同一份」。
    只钉前者的话，一个把结果重写一遍、写完还碰巧一样的实现照样绿。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})

    first = submit(client, headers, session_id)
    second = submit(client, headers, session_id)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"]["result"] == second.json()["data"]["result"]
    assert first.json()["data"]["calculation_status"] == "CALCULATED"
    assert second.json()["data"]["calculation_status"] == "CALCULATED"

    assert db_session.scalar(select(func.count(AssessmentResult.id))) == 1
    assert db_session.scalar(select(func.count(RiskEvent.id))) == 1
    session = db_session.get(AssessmentSession, session_id)
    assert session.answer_hash_algorithm == ANSWER_HASH_ALGORITHM
    assert len(session.answer_snapshot_hash) == 64


def test_the_hash_is_the_one_the_stored_answers_produce(client, db_session):
    """库里那一行摘要，就是**库里那份答卷**算出来的。

    这条把「哈希有写入方」从一句声明变成一句可复核的话：拿落库的答案重新算一遍，
    必须与原值逐字相同。它也是「同一份答卷在任何服务里算出同一个值」在**这份数据上**
    的实例（规范化规则本身由 `test_answer_snapshot.py` 的黄金值钉住）。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85, 97})
    assert submit(client, headers, session_id).status_code == 200

    session = db_session.get(AssessmentSession, session_id)
    rows = db_session.execute(
        select(ScaleQuestion.question_no, AssessmentAnswer.answer)
        .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
        .where(AssessmentAnswer.session_id == session_id)
    ).all()
    scale = db_session.get(AssessmentScale, session.scale_id)
    assert session.answer_snapshot_hash == answer_snapshot_hash(
        scale_code=scale.code,
        scale_version=session.scale_version,
        answer_map={question_no: answer for question_no, answer in rows},
    )


def test_a_repeat_submit_does_not_move_the_submission_stamp(client, db_session):
    """`submitted_at` / 用时 / 测评日期在重发时**原地不动**。

    「什么时候交的卷」是一件已经发生过的事。重发（客户端重试、老师点重试之后学生
    又点一次）再盖一次章会把它往后推，用时跟着变长——而这两列正是「用时」与
    「本次测评是哪一天」的答案。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    assert submit(client, headers, session_id).status_code == 200
    session = db_session.get(AssessmentSession, session_id)
    stamped = (session.submitted_at, session.duration_seconds, session.tested_at)

    assert submit(client, headers, session_id).status_code == 200
    db_session.refresh(session)
    assert (session.submitted_at, session.duration_seconds, session.tested_at) == stamped


def test_an_online_sitting_says_where_its_test_date_came_from(client, db_session):
    """在线答卷：`tested_at` 是交卷那一刻，来源 `ONLINE_SUBMIT`。

    `tested_at_source` 此前恒为 `PENDING_VERIFICATION`（列有了、没有写入方），
    于是它一个比特的信息都不承载。这条钉住它第一次有了真值。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id)
    assert submit(client, headers, session_id).status_code == 200

    session = db_session.get(AssessmentSession, session_id)
    assert session.tested_at == session.submitted_at
    assert session.tested_at_source == "ONLINE_SUBMIT"


# --- 计算失败可以安全重试 -----------------------------------------------------


def _boom(*args, **kwargs):
    raise RuntimeError("规则文件坏了")


def test_a_failed_calculation_is_recorded_and_leaves_the_answers_alone(client, db_session, monkeypatch):
    """评分失败：学生拿到 200、答卷一行不少、失败被记在会话上。

    这是这一层最重要的一条。失败时抛出去看起来更「干净」，代价却是路由不会 commit
    ——学生花二十分钟做的答卷跟着回滚，他重做一遍，而错误信息里没有任何东西提到
    他的答案。所以失败被**返回**（`score_session` 记下来、返回 False），由唯一那次
    commit 落库。

    「规则坏了」用 `monkeypatch` 注入是有原因的：真实的失败是**没预料到的**那一种，
    没有一个可以摆出来的触发条件。能被构造出来的失败（少答、答案非法）恰恰不是失败，
    而是「还不该算」（`CALCULATION_NOT_READY_CODES`）。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    monkeypatch.setattr(assessment_service, "_calculate_and_hash", _boom)

    response = submit(client, headers, session_id)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["calculation_status"] == "CALCULATION_FAILED"
    assert data["result"] is None
    assert "RuntimeError" in data["calculation_error"]
    assert data["submitted_at"] is not None

    db_session.expire_all()
    session = db_session.get(AssessmentSession, session_id)
    assert session.calculation_status == "CALCULATION_FAILED"
    assert session.calculation_error.startswith("RuntimeError: ")
    assert session.answer_snapshot_hash is None
    # 答卷与「交过卷」这件事都没丢：评分成不成，与这两件事无关。
    assert db_session.scalar(
        select(func.count(AssessmentAnswer.id)).where(AssessmentAnswer.session_id == session_id)
    ) == 100
    assert session.submitted_at is not None
    target = db_session.scalar(
        select(AssessmentTarget).where(
            AssessmentTarget.task_id == session.task_id,
            AssessmentTarget.student_id == session.student_id,
        )
    )
    assert target.status == "COMPLETED"
    # 失败**不伪造结果**：一行都没有。
    assert db_session.scalar(select(func.count(AssessmentResult.id))) == 0


def test_a_failed_calculation_can_be_retried_by_the_counselor(client, db_session, monkeypatch):
    """坏掉的原因消失之后，心理老师点一下重试，这一场就补上了。

    判据有三条，缺一条这条用例就不成立：重试之后结果在、`calculation_error` 被清空
    （不然界面上那行红字会永远留着）、以及**没有多出一条结果行**。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    monkeypatch.setattr(assessment_service, "_calculate_and_hash", _boom)
    assert submit(client, headers, session_id).status_code == 200
    monkeypatch.undo()

    counselor = auth_headers(client, "counselor", "13800000001")
    response = retry(client, counselor, session_id)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["recalculated"] is True
    assert data["calculation_status"] == "CALCULATED"
    assert data["calculation_error"] is None
    assert data["result"]["total_score"] == 1
    assert db_session.scalar(select(func.count(AssessmentResult.id))) == 1


def test_the_retry_writes_an_audit_row_naming_the_student(client, db_session, monkeypatch):
    """重算是一次敏感读取（它读满整份答卷，重点题也在里面），所以它写审计，
    而且那一行**指名道姓**——`actor_role` 回答不了「三位心理老师里是谁点的」。"""
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    monkeypatch.setattr(assessment_service, "_calculate_and_hash", _boom)
    assert submit(client, headers, session_id).status_code == 200
    monkeypatch.undo()

    counselor = auth_headers(client, "counselor", "13800000001")
    assert retry(client, counselor, session_id).status_code == 200

    row = db_session.scalar(select(AuditLog).where(AuditLog.action == "重算测评评分"))
    assert row is not None
    assert row.resource_id == str(session_id)
    assert row.student_id == student_by_no(db_session).id
    assert row.detail == "重算完成，已写入结果"


def test_the_retry_reports_an_unrecalculated_sitting_without_touching_it(client, db_session):
    """已经有结果的场次：重试什么都不做，并且**说出来它什么都没做**。

    这一条是「规则版本变化不覆盖历史结果」的入口判据——真正钉住结果没被改写的是
    下面那一条。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    assert submit(client, headers, session_id).status_code == 200

    counselor = auth_headers(client, "counselor", "13800000001")
    response = retry(client, counselor, session_id)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["recalculated"] is False
    assert data["calculation_status"] == "CALCULATED"
    assert db_session.scalar(select(func.count(AssessmentResult.id))) == 1


def test_a_new_rule_version_does_not_rewrite_a_historical_result(client, db_session):
    """把评分规则改成「1 分也算重点关注」，再点重试——**那一条历史结果一个字都不改**。

    这正是 CLAUDE.md §6 那条「改已发布版本 → 生成新版本，已有结果不受影响」在写入侧
    的另一半：重算不得覆盖一份已经存在的结果，否则就是「拿今天的规则改写昨天的结论」，
    而结果行上的 `rule_version` 会说它是新版本——一个没有任何东西看得出来的改写。

    判据必须挑一个**如果重算就会变**的字段（这里是 `total_level`：改后的规则会把
    1 分判成 `KEY_ATTENTION`，而存着的是 `GENERAL_RANGE`）。只断言「结果还在」的话，
    一个默默重算并覆盖的实现照样绿——它只有答案完全一样时才看得出来。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    assert submit(client, headers, session_id).status_code == 200
    stored = db_session.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session_id))
    assert stored.total_level == "GENERAL_RANGE"
    stored_id = stored.id

    rule = db_session.scalar(select(ScaleRule).where(ScaleRule.status == "ACTIVE"))
    rule.config_json = {**(rule.config_json or {}), "total_bands": [{"code": "KEY_ATTENTION", "min": 0, "max": 100}]}
    db_session.flush()

    counselor = auth_headers(client, "counselor", "13800000001")
    assert retry(client, counselor, session_id).status_code == 200

    db_session.expire_all()
    rows = db_session.scalars(select(AssessmentResult)).all()
    assert len(rows) == 1
    assert rows[0].id == stored_id
    assert rows[0].total_level == "GENERAL_RANGE"
    assert rows[0].rule_version == MHT_RULE_VERSION


def test_the_retry_refuses_a_sitting_that_was_never_submitted(client, db_session):
    """没交卷就没有可重算的评分：422 且说清是哪一种，而不是留下一行 `CALCULATION_FAILED`
    ——一份答到一半的卷子算出来的一定是「还有题没答」，把它记成系统故障只会盖住真正的原因。"""
    headers, session_id = create_student_session(client)
    counselor = auth_headers(client, "counselor", "13800000001")
    response = retry(client, counselor, session_id)
    assert response.status_code == 422
    assert "还没有交卷" in response.json()["error"]["message"]
    assert db_session.get(AssessmentSession, session_id).calculation_status == "PENDING"


def test_a_session_outside_the_counselors_scope_cannot_be_retried(client, db_session):
    """`session_id` 是客户端传来的，它不能成为越权凭据（§9 的 ID 入口）。

    外校学生那一场：心理老师拿得到 id，但拿不到这个学生。拒绝的那一条**不写审计**
    （给一次被拒的读取记上「重算测评评分」，会让访问轨迹反过来撒谎）。
    """
    student = make_other_school_student(db_session)
    session = make_sitting(
        db_session,
        student,
        submitted_at=datetime(2026, 3, 1, 9, 0),
        tested_at=datetime(2026, 3, 1, 9, 0),
        tested_at_source="IMPORT_FILE",
    )
    db_session.commit()

    counselor = auth_headers(client, "counselor", "13800000001")
    response = retry(client, counselor, session.id)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    assert db_session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "重算测评评分")) == 0


@pytest.mark.parametrize("role,account", [("student", "S001"), ("leader", "13800000002"), ("admin", "admin")])
def test_only_a_counselor_may_retry(client, role, account):
    """测评这条线是学校业务，写侧归业务负责人（§4「测评任务不是能力、是角色」的同一层）。
    学生本人也不行——自己那份答卷算不算得出来，不是他能决定的事。"""
    response = retry(client, auth_headers(client, role, account), 1)
    assert response.status_code == 403


# --- 趋势按真实测评时间排序 ---------------------------------------------------


def test_the_trend_lists_sittings_by_real_test_date_not_by_when_they_arrived(client, db_session):
    """「历次趋势」从旧到新，按**施测日期**排，不是按导入顺序。

    造法就是这件事在真实学校里发生的样子：上学期那份普查是**后来**才导进来的，
    所以它的 `id` 更大、`submitted_at`（= 文件里的测评日）更早。按 id 排的话它会被
    当成「最近的一场」，而 `latest_session_order` 那一侧的注释记着同一个坑。

    这条同时是**不变量**的守卫：排序读的是 `submitted_at`，而两条写入路径都把它与
    `tested_at` 设成同一个值（在线 = 交卷那一刻，导入 = 文件里那一天）。哪天有人写出一场
    `tested_at ≠ submitted_at` 的会话，排序口径就得跟着改成「先 `tested_at`」——
    那一条由 `test_assessment_import_api.py` 与上面的 `ONLINE_SUBMIT` 用例守着。
    """
    student = student_by_no(db_session)
    # 这一页的门是关怀档案（`get_care_case` 按学生取最新那一条），而这三场会话都是
    # 直接造的、没有风险事件，所以档案要自己建一条——真实场景里这名学生本来就有。
    db_session.add(StudentCareCase(student_id=student.id, status="FOLLOWING"))
    db_session.flush()
    spring = make_sitting(
        db_session,
        student,
        submitted_at=datetime(2026, 3, 1, 9, 0),
        tested_at=datetime(2026, 3, 1, 9, 0),
        tested_at_source="IMPORT_FILE",
    )
    autumn = make_sitting(
        db_session,
        student,
        submitted_at=datetime(2026, 9, 1, 9, 0),
        tested_at=datetime(2026, 9, 1, 9, 0),
        tested_at_source="ONLINE_SUBMIT",
    )
    assert spring.id < autumn.id  # 上学期那一场是**先**插进去的（id 更小）……
    db_session.commit()
    # ……而真实场景里它是后导进来的。所以再插一场 id 更大、日期更早的：
    summer = make_sitting(
        db_session,
        student,
        submitted_at=datetime(2026, 6, 1, 9, 0),
        tested_at=datetime(2026, 6, 1, 9, 0),
        tested_at_source="IMPORT_FILE",
    )
    db_session.commit()

    counselor = auth_headers(client, "counselor", "13800000001")
    detail = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor).json()["data"]
    assert [row["session_id"] for row in detail["history"]] == [spring.id, summer.id, autumn.id]
    # 「本次测评」取的是日期最晚的那一场，与趋势的最后一个点同一场。
    assert detail["assessment"]["session_id"] == autumn.id
    assert detail["history"][-1]["session_id"] == detail["assessment"]["session_id"]


def test_the_case_detail_carries_the_scoring_state_and_the_test_date_source(client, db_session):
    """个案详情那一组字段：算没算出来、为什么没算出来、哪一天测的、日期是谁给的。

    「算出来是什么」（`total_level`）与「算出来了没有」（`calculation_status`）是
    两个问题：前者为 `None` 时，后者是唯一能解释为什么的东西。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    assert submit(client, headers, session_id).status_code == 200

    student = student_by_no(db_session)
    counselor = auth_headers(client, "counselor", "13800000001")
    assessment = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor).json()["data"]["assessment"]
    assert assessment["session_id"] == session_id
    assert assessment["calculation_status"] == "CALCULATED"
    assert assessment["calculation_error"] is None
    assert assessment["tested_at_source"] == "ONLINE_SUBMIT"
    assert assessment["tested_at"] == assessment["submitted_at"]
    assert assessment["total_level"] == "GENERAL_RANGE"


def test_the_case_detail_still_explains_a_sitting_whose_scoring_failed(client, db_session, monkeypatch):
    """评分没成的那一场照样是可以被看见的「本次测评」：等级是 `None`，状态是失败。

    「看不见」与「算不出来」必须长得不一样——前者会让人以为这一场不存在，
    而学校手上明明有一份交了卷的答卷。

    档案是直接建的一条：评分失败的那条路上一个风险事件都不会有（`maybe_raise_risk_events`
    根本没跑到），而这一页的门是关怀档案。真实场景里这名学生本来就有在办档案
    ——他先被开过档，这一场是复测。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})
    student = student_by_no(db_session)
    db_session.add(StudentCareCase(student_id=student.id, status="FOLLOWING"))
    db_session.flush()

    monkeypatch.setattr(assessment_service, "_calculate_and_hash", _boom)
    assert submit(client, headers, session_id).status_code == 200

    counselor = auth_headers(client, "counselor", "13800000001")
    assessment = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor).json()["data"]["assessment"]
    assert assessment["session_id"] == session_id
    assert assessment["calculation_status"] == "CALCULATION_FAILED"
    assert "RuntimeError" in assessment["calculation_error"]
    assert assessment["total_level"] is None
