"""统计分析中心 P0 的统一任务快照。"""

from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def _submit(client, yes_numbers: set[int]) -> None:
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers=yes_numbers)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 200, response.text


def test_report_uses_one_task_snapshot_and_suppresses_small_cohort_metrics(client):
    _submit(client, {1, 2, 3, 85})
    headers = auth_headers(client, "counselor", "13800000001")

    response = client.get("/api/v1/analytics/report", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["task"]["id"]
    assert data["analysis_mode"] == "ALL_CALCULATED"
    assert data["sample_quality"]["completed_count"] == 1
    assert data["sample_quality"]["n_evaluable"] == 1
    assert data["overview"]["sample_count"] == 1
    assert data["overview"]["signal_student_count"] == 1
    assert data["overview"]["signal_rate"] is None

    learning = next(
        item for item in data["dimensions"] if item["dimension_code"] == "LEARNING_ANXIETY"
    )
    assert learning["n_evaluable"] == 1
    assert learning["mean_score"] is None
    # 分布形状始终返回（三个水平），但被抑制时比率为 null。
    assert len(learning["distribution"]) == 3
    for level in learning["distribution"]:
        assert level["rate"] is None
    assert learning["high_score_count"] is None
    assert learning["suppression"] == {"suppressed": True, "reason": "MIN_COHORT"}


def test_report_analysis_mode_excludes_validity_flagged_results(client):
    _submit(client, {82, 84, 86, 88, 90, 92, 94})
    headers = auth_headers(client, "leader", "13800000002")

    all_results = client.get(
        "/api/v1/analytics/report?analysisMode=ALL_CALCULATED", headers=headers
    ).json()["data"]
    unflagged = client.get(
        "/api/v1/analytics/report?analysisMode=VALIDITY_UNFLAGGED", headers=headers
    ).json()["data"]

    assert all_results["sample_quality"]["completed_count"] == 1
    assert all_results["sample_quality"]["validity_flagged_count"] == 1
    assert all_results["sample_quality"]["n_evaluable"] == 1
    assert unflagged["sample_quality"]["completed_count"] == 1
    assert unflagged["sample_quality"]["n_evaluable"] == 0


def test_report_rejects_unknown_analysis_mode(client):
    headers = auth_headers(client, "leader", "13800000002")
    response = client.get("/api/v1/analytics/report?analysisMode=UNKNOWN", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

