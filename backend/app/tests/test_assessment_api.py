from sqlalchemy import func, select, update

from app.models.assessment import AssessmentResult, RiskEvent
from app.models.scale import ScaleQuestion
from app.tests.conftest import auth_headers


def create_student_session(client):
    headers = auth_headers(client, "student", "S001")
    tasks = client.get("/api/v1/student/tasks", headers=headers)
    assert tasks.status_code == 200
    task_id = tasks.json()["data"]["items"][0]["id"]
    created = client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": task_id})
    assert created.status_code == 200
    return headers, created.json()["data"]["id"]


def save_answers(client, headers, session_id, yes_numbers=None):
    yes_numbers = set(yes_numbers or [])
    for question_no in range(1, 101):
        answer = "YES" if question_no in yes_numbers else "NO"
        response = client.put(
            f"/api/v1/assessment-sessions/{session_id}/answers/{question_no}",
            headers=headers,
            json={"answer": answer},
        )
        assert response.status_code == 200


def test_student_can_list_tasks_and_create_session(client):
    headers, session_id = create_student_session(client)
    response = client.get(f"/api/v1/assessment-sessions/{session_id}", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "IN_PROGRESS"
    assert data["answered_count"] == 0
    assert data["current_question_no"] == 1


def test_current_question_no_is_the_first_one_this_bank_still_has(client, db_session):
    """`current_question_no` 指向**这一版题库里**第一道未答题（2026-09-17 改）。

    此前它取 `range(1, 101)` 里第一道没答的题，题库一换成不是 100 题的版本，
    这个字段就会指着一个不存在的题号（或者漏掉 100 之后那几道）。
    学生页拿它当「接着答哪一道」的落点，所以它指的题必须是学生真看得见的那一份题单里的。

    这里把第 2 题从题库里停用（`status != ACTIVE`）再答第 1 题：答完第 1 题之后
    下一道没答的题是 **3**，不是 2——2 已经不在题单里了。
    变异：把判据改回 `range(1, 101)`，这条就红。
    """
    headers, session_id = create_student_session(client)

    saved = client.put(
        f"/api/v1/assessment-sessions/{session_id}/answers/1",
        headers=headers,
        json={"answer": "YES"},
    )
    assert saved.status_code == 200
    db_session.execute(
        update(ScaleQuestion).where(ScaleQuestion.question_no == 2).values(status="INACTIVE")
    )
    db_session.commit()

    response = client.get(f"/api/v1/assessment-sessions/{session_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["current_question_no"] == 3


def test_counselor_cannot_create_student_session(client):
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": 1})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_save_answer_rejects_invalid_value(client):
    headers, session_id = create_student_session(client)
    response = client.put(
        f"/api/v1/assessment-sessions/{session_id}/answers/1",
        headers=headers,
        json={"answer": "MAYBE"},
    )
    assert response.status_code == 422


def test_submit_rejects_incomplete_answers(client):
    headers, session_id = create_student_session(client)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ANSWERS_INCOMPLETE"


def test_submit_complete_answers_calculates_result_and_risk_event(client, db_session):
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85})

    response = client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit",
        headers={**headers, "Idempotency-Key": "submit-1"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["result"]["total_score"] == 1
    assert data["result"]["validity_score"] == 0
    assert data["result"]["rule_version"] == "MHT-RULE-1.1.0"
    assert len(data["risk_events"]) == 1
    assert data["risk_events"][0]["trigger_rule"] == "KEY_QUESTION_85_YES"

    assert db_session.scalar(select(func.count(AssessmentResult.id))) == 1
    assert db_session.scalar(select(func.count(RiskEvent.id))) == 1


def test_repeat_submit_returns_existing_result_without_duplicate_risk_events(client, db_session):
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers={85, 97})

    first = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    second = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(second.json()["data"]["risk_events"]) == 2
    assert db_session.scalar(select(func.count(RiskEvent.id))) == 2


def test_submitted_session_is_locked(client):
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 200

    locked = client.put(
        f"/api/v1/assessment-sessions/{session_id}/answers/1",
        headers=headers,
        json={"answer": "YES"},
    )
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "SESSION_LOCKED"



def test_resubmit_with_a_different_idempotency_key_conflicts(client):
    """The Idempotency-Key header used to be stored and never read, so this
    documented error code could never be raised. A *different* key on an
    already-submitted session is a genuine conflict, not a retry."""
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id)

    first = client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit",
        headers={**student_headers, "Idempotency-Key": "key-one"},
    )
    assert first.status_code == 200

    # Same key (or none) is a retry: returns the stored result.
    replay = client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit",
        headers={**student_headers, "Idempotency-Key": "key-one"},
    )
    assert replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"]

    no_key = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert no_key.status_code == 200

    # A different key is a conflict.
    conflicting = client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit",
        headers={**student_headers, "Idempotency-Key": "key-two"},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
