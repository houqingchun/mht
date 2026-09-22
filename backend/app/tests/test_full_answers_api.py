"""完整答卷查看接口守卫。

2026-09-21 加：学生测评记录页每行「查看完整答卷」按钮的后端。
与 `key_question_answers` 共享 `KeyQuestionReader` 能力矩阵，
但返回 100 道题的逐题答案，不只是重点题。
"""

from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def _submit(client, yes_numbers: set[int]) -> int:
    """提交答卷，回 session_id。"""
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers=yes_numbers)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 200, response.text
    return session_id


def test_full_answers_returns_all_questions(client):
    """提交一份答卷后，完整答卷接口返回全部 100 道题（含效度题）。"""
    session_id = _submit(client, {1, 2, 3, 85, 97})
    headers = auth_headers(client, "counselor", "13800000001")

    response = client.get(
        f"/api/v1/students/1/sessions/{session_id}/full-answers",
        headers=headers,
        params={"purpose": "家访前核实"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["session_id"] == session_id
    # 全部 100 道题（含 10 道效度题）。
    assert len(data["items"]) == 100
    # 第 1 题我们答了 YES。
    q1 = next(item for item in data["items"] if item["question_no"] == 1)
    assert q1["answer"] == "YES"
    assert q1["question_text"]  # 题干非空
    # 效度题 82 也在结果里。
    q82 = next(item for item in data["items"] if item["question_no"] == 82)
    assert q82["question_text"]


def test_full_answers_requires_purpose(client):
    """不填查看原因 → 422。"""
    session_id = _submit(client, {1, 2, 3})
    headers = auth_headers(client, "counselor", "13800000001")

    response = client.get(
        f"/api/v1/students/1/sessions/{session_id}/full-answers",
        headers=headers,
        params={"purpose": ""},
    )
    assert response.status_code == 422


def test_full_answers_denied_to_leader(client):
    """德育领导没有 KeyQuestionReader 能力 → 403。"""
    session_id = _submit(client, {1, 2, 3})
    headers = auth_headers(client, "leader", "13800000002")

    response = client.get(
        f"/api/v1/students/1/sessions/{session_id}/full-answers",
        headers=headers,
        params={"purpose": "核实"},
    )
    assert response.status_code == 403


def test_full_answers_writes_audit(client):
    """查看完整答卷写入审计行。"""
    session_id = _submit(client, {1, 2, 3})
    headers = auth_headers(client, "counselor", "13800000001")

    response = client.get(
        f"/api/v1/students/1/sessions/{session_id}/full-answers",
        headers=headers,
        params={"purpose": "家访核实"},
    )
    assert response.status_code == 200, response.text

    # 通过审计接口验证写入了审计行
    audit_headers = auth_headers(client, "admin", "admin")
    audit_resp = client.get("/api/v1/audit-logs", headers=audit_headers, params={"q": "查看完整答卷"})
    assert audit_resp.status_code == 200, audit_resp.text
    audit_data = audit_resp.json()["data"]
    # 至少有一条「查看完整答卷」的审计行。
    assert any(row["action"] == "查看完整答卷" for row in audit_data["items"])
