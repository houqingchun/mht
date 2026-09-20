from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers
from app.tests.test_care_api import create_risk_case


def create_completed_case(client):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert response.status_code == 200


def test_leader_can_view_aggregated_overview_and_progress(client):
    create_completed_case(client)
    headers = auth_headers(client, "leader", "13800000002")

    overview = client.get("/api/v1/analytics/overview", headers=headers)
    assert overview.status_code == 200
    data = overview.json()["data"]
    assert data["total_targets"] == 1
    assert data["completed_targets"] == 1
    assert data["completion_rate"] == 100
    assert data["case_status_counts"]["PENDING_REVIEW"] == 1

    by_grade = client.get("/api/v1/analytics/by-grade", headers=headers)
    assert by_grade.status_code == 200
    assert by_grade.json()["data"]["items"][0]["completion_rate"] == 100

    progress = client.get("/api/v1/leader/progress", headers=headers)
    assert progress.status_code == 200
    item = progress.json()["data"]["items"][0]
    assert item["student_name"] == "林同学"
    assert "confirmed_facts" not in item
    assert "answers" not in item
    assert "trigger_rule" not in item


def test_student_cannot_view_analytics(client):
    headers = auth_headers(client, "student", "S001")
    response = client.get("/api/v1/analytics/overview", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"



def test_leader_progress_lists_only_open_cases(client):
    """「重点进展」只列在办的档案 —— 关掉的那条不在里面。

    2026-09-17 之前它把 `CLOSED` 也发下来，于是三件事同时不对：列表只增不减
    （§1：关过的档案会累积，上学期关掉是一次完整的过程），德育领导总览的
    「在办关注档案」把去年已经了结的学生仍算作在办，同页的「未分配负责人」
    也把结案档案的无主算成待办。修的时候只动了这一处 WHERE。

    这里先断言「关之前它在」，再断言「关之后它不在」——只写后半句的话，
    这条用例在一个**根本没建出档案**的库上也是绿的（§测试注意里
    「先证明有东西可扫」的同一条教训）。变异：把 `.where(status != CLOSED)`
    去掉，第二条断言变红。
    """
    counselor_headers = create_risk_case(client)
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]
    headers = auth_headers(client, "leader", "13800000002")

    before = client.get("/api/v1/leader/progress", headers=headers).json()["data"]["items"]
    assert [item["case_id"] for item in before] == [case_item["case_id"]]
    assert before[0]["case_status"] == "PENDING_REVIEW"

    closed = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/close",
        headers=counselor_headers,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "已检查后续安排。",
            "confirm_follow_up_checked": True,
            # 乐观锁（§16.4），取自上面那一行——列表接口发 `case_version`。
            "case_version": case_item["case_version"],
        },
    )
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "CLOSED"

    after = client.get("/api/v1/leader/progress", headers=headers).json()["data"]["items"]
    assert after == [], "已关闭的档案不该出现在「重点进展」里"

    # 它没被删掉——「关闭档案不得删除历史记录」（§1）。个案详情照旧能打开它。
    detail = client.get(f"/api/v1/care-cases/{case_item['student_id']}", headers=counselor_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["case_status"] == "CLOSED"
