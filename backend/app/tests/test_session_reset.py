"""重置一场测评（`POST /assessment-sessions/{id}/reset`）清掉什么、什么时候拒绝。

## 它为什么值得单独一个文件

这个端点只有开发与 E2E 夹具在用（`frontend/src/` 里没有任何调用方），但它删的东西
是**已经落库的评分事实**——而这件事与「一场测评算出来过什么」是同一件事的两面，
`assessment_service.reset_sitting` 就住在评分代码旁边。

## 2026-09-25 用户裁决（PROGRESS §5.9 第 3 项）：连结果一起清

在此之前 `/reset` 只删答卷、**留着 `assessment_result`**。于是「重置 → 重答 → 再交卷」
走的不是重新评分，而是 `submit_session` 的幂等早返回——学生重新答了一遍，拿回来的
是**上一次那一份分**，而在按结果说话的每一处（关注等级、关注率、受控导出、个案详情）
这一场从此都留着一份与当前答卷对不上的记录。裁决是清掉。

## 四条判据，各自守一个不同的东西

1. **重答之后的分来自新答卷**，不是旧分（第二条用例：答得不一样，分就该不一样）；
2. **重答能重新开出同一批筛查信号而不撞唯一键**——这是「连结果一起清」的直接后果，
   也是这一期唯一一处非做不可的连带改动（见 `uq_risk_event_session_trigger_rule`）；
3. **有人碰过信号就整场不清**（409，且一行都没删）——清掉等于抹掉一次人工复核；
4. **「有结果、没有交卷时间」的历史行仍然会被补盖章**（`submit_session` 里留下的
   那一支）。接口已经造不出那个状态了，所以这一条自己把它造出来。
"""

from datetime import date

from sqlalchemy import func, select

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    DimensionResult,
    RiskEvent,
)
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def _counts(db, session_id: int) -> dict[str, int]:
    """这一场上「算出来过什么」的行数——重置前后逐项比。"""
    return {
        "answers": db.scalar(
            select(func.count(AssessmentAnswer.id)).where(
                AssessmentAnswer.session_id == session_id
            )
        ),
        "results": db.scalar(
            select(func.count(AssessmentResult.id)).where(
                AssessmentResult.session_id == session_id
            )
        ),
        "dimensions": db.scalar(
            select(func.count(DimensionResult.id)).where(DimensionResult.session_id == session_id)
        ),
        "risk_events": db.scalar(
            select(func.count(RiskEvent.id)).where(RiskEvent.session_id == session_id)
        ),
    }


def _submit(client, headers, session_id, yes_numbers) -> dict:
    """答一遍重点题之外的题、交卷，回交卷响应里的 `data`。"""
    save_answers(client, headers, session_id, yes_numbers=yes_numbers)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------
# 1. 重答重新评分
# --------------------------------------------------------------------------


def test_a_retake_after_reset_is_scored_from_the_new_answers(client, db_session):
    """重置之后再交卷，拿回来的是**新答卷算出来的分**，不是上一次那一份。

    第二次刻意答得不一样（多答了五道「是」）：只断言「分数还在」的话，一个「清了
    答卷、留着结果行」的实现照样是绿的——它会把旧分原样还回来。
    """
    headers, session_id = create_student_session(client)
    first = _submit(client, headers, session_id, yes_numbers=())
    first_score = first["result"]["total_score"]

    reset = client.post(f"/api/v1/assessment-sessions/{session_id}/reset", headers=headers)
    assert reset.status_code == 200, reset.text
    # 回的是会话载荷（学生答题页读的那一份），所以这里能断的就是「一题都没答了」。
    # 「结果行没了」由下面那组行数说。
    assert reset.json()["data"]["answered_count"] == 0

    # expire_on_commit=False 的会话里，属性不会自己刷新
    db_session.expire_all()
    session = db_session.get(AssessmentSession, session_id)
    assert _counts(db_session, session_id) == {
        "answers": 0,
        "results": 0,
        "dimensions": 0,
        "risk_events": 0,
    }
    assert session.status == "IN_PROGRESS"
    assert session.submitted_at is None
    assert session.duration_seconds is None
    # 测评日期与它的出处一起回退：这一场现在**没有**测评日期。`PENDING_VERIFICATION`
    # 在这里读作「还没测」，不是「不知道那天测的」——它与不变量「`tested_at` 与
    # `submitted_at` 总是一样」（CLAUDE.md §23）是同一句话。
    assert session.tested_at is None
    assert session.tested_at_source == "PENDING_VERIFICATION"
    # 「算过什么」的几列同样回退：留着任何一列都在描述一次不再存在的作答。
    assert session.calculation_status == "PENDING"
    assert session.calculation_error is None
    assert session.answer_snapshot_hash is None
    assert session.answer_hash_algorithm is None
    assert session.idempotency_key is None

    second = _submit(client, headers, session_id, yes_numbers=(1, 2, 3, 4, 5))
    assert second["result"] is not None
    assert second["result"]["total_score"] != first_score, (
        "重答之后拿回来的是上一次那一份分——重置没有把结果行一起清掉"
    )
    assert second["submitted_at"] is not None
    assert second["duration_seconds"] is not None
    # 八维度是**重新算出来的那一套**：旧的不删，重算会再插一套，这里就会是 16 行。
    assert len(second["dimension_results"]) == 8

    db_session.expire_all()
    assert _counts(db_session, session_id)["dimensions"] == 8
    assert db_session.get(AssessmentSession, session_id).calculation_status == "CALCULATED"


# --------------------------------------------------------------------------
# 2. 同一批信号能重新开出来（唯一键那个坑）
# --------------------------------------------------------------------------


def test_a_retake_can_raise_the_same_risk_signals_again(client, db_session):
    """重答触发同样的两条重点题时，重新开出同样的两条信号，而不是 500。

    `uq_risk_event_session_trigger_rule` 是 `(session_id, trigger_rule, rule_version)`，
    **状态不在键里**——一条 `REVIEWED` / `VOIDED` 的行照样占着那个三元组。所以「连结果
    一起清」之后必须保证这一场不再有任何存活的风险行，否则重答时 `score_session`
    会重新插同一个三元组、撞 1062，学生拿到一个英文的 500。

    两轮都答 85 与 97（MHT 的两道重点题）：第二轮能跑完，就是信号真的被清干净了。
    """
    headers, session_id = create_student_session(client)
    first = _submit(client, headers, session_id, yes_numbers=(85, 97))
    assert sorted(event["trigger_rule"] for event in first["risk_events"]) == [
        "KEY_QUESTION_85_YES",
        "KEY_QUESTION_97_YES",
    ]

    reset = client.post(f"/api/v1/assessment-sessions/{session_id}/reset", headers=headers)
    assert reset.status_code == 200, reset.text

    second = _submit(client, headers, session_id, yes_numbers=(85, 97))
    assert sorted(event["trigger_rule"] for event in second["risk_events"]) == [
        "KEY_QUESTION_85_YES",
        "KEY_QUESTION_97_YES",
    ]
    db_session.expire_all()
    assert _counts(db_session, session_id)["risk_events"] == 2


# --------------------------------------------------------------------------
# 3. 有人碰过就整场不清
# --------------------------------------------------------------------------


def test_reset_is_refused_and_changes_nothing_once_a_signal_was_reviewed(client, db_session):
    """已经有人复核过信号的那一场，重置回 409 并且**一行都没删**。

    「有人碰过」的判据是两对原始记录（`reviewed_by` / `reviewed_at` 与
    `voided_by` / `voided_at`），不是 `status` 那个别人推出来的结论。第二段把状态
    推回 `PENDING` 并清掉那两对时间戳，只留下那条**人工复核记录**——它仍然要拦住，
    因为 `manual_review.risk_event_id` 是全库唯一指向 `risk_event` 的外键，而它是
    RESTRICT（全库没有 `ondelete=`）。漏了那一判，删除会在 flush 时撞 1451 而
    用户看到的是一句英文的 500。
    """
    headers, session_id = create_student_session(client)
    _submit(client, headers, session_id, yes_numbers=(85,))
    db_session.expire_all()
    before = _counts(db_session, session_id)

    counselor_headers = auth_headers(client, "counselor", "13800000001")
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]
    detail = client.get(
        f"/api/v1/care-cases/{case_item['student_id']}", headers=counselor_headers
    ).json()["data"]
    risk_event_id = detail["risk_events"][0]["id"]
    review = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/reviews",
        headers=counselor_headers,
        json={
            "risk_event_id": risk_event_id,
            "review_result": "建立持续关注档案",
            "confirmed_facts": "已与学生完成初步沟通，记录已确认事实。",
            "next_action": "安排下次跟进",
            "next_follow_up_date": str(date(2026, 9, 23)),
        },
    )
    assert review.status_code == 200, review.text

    refused = client.post(f"/api/v1/assessment-sessions/{session_id}/reset", headers=headers)
    assert refused.status_code == 409, refused.text
    body = refused.json()["error"]
    assert "筛查信号被人工处理过" in body["message"]
    assert "1" in body["message"], "那句话要说得出有几条，否则读者不知道该去查什么"

    db_session.expire_all()
    assert _counts(db_session, session_id) == before, "被拒绝的那一次重置不许改动任何一行"

    # 只留下复核记录这一条痕迹：状态推回去、两对时间戳清掉。
    event = db_session.scalar(select(RiskEvent).where(RiskEvent.session_id == session_id))
    event.status = "PENDING"
    event.reviewed_by = None
    event.reviewed_at = None
    db_session.flush()

    still_refused = client.post(f"/api/v1/assessment-sessions/{session_id}/reset", headers=headers)
    assert still_refused.status_code == 409, still_refused.text
    db_session.expire_all()
    assert _counts(db_session, session_id) == before


# --------------------------------------------------------------------------
# 4. 历史行：有结果、却没有交卷时间
# --------------------------------------------------------------------------


def test_a_result_without_a_submission_timestamp_is_re_stamped(client, db_session):
    """「有结果、没有交卷时间」的那一行，再交一次会被补上盖章，分数一个字不动。

    这个状态**接口已经造不出来了**（2026-09-25 起重置会连结果一起清），今天只剩历史
    行与手工改库。用例自己把它造出来，因为 `submit_session` 里那一支仍然要留着：
    不补的话这一场永远没有 `submitted_at`，`latest_session_order` 会把它排到最后
    （未交卷的排在后面），个案详情会把**上一场**当成「本次测评」显示，而用时在每一处
    都是「—」——后果不可恢复。
    """
    headers, session_id = create_student_session(client)
    first = _submit(client, headers, session_id, yes_numbers=())

    db_session.expire_all()
    session = db_session.get(AssessmentSession, session_id)
    session.submitted_at = None
    session.duration_seconds = None
    db_session.flush()

    replay = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert replay.status_code == 200, replay.text
    data = replay.json()["data"]
    # 幂等保证没变：回来的仍然是库里存着的那一份分
    assert data["result"] == first["result"]
    # 但提交事实被补上了
    assert data["submitted_at"] is not None
    assert data["tested_at"] is not None
    assert data["tested_at_source"] == "ONLINE_SUBMIT"
    assert data["duration_seconds"] is not None
