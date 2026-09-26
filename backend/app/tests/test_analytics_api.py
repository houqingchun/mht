from datetime import date

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


def test_the_retest_kpi_counts_the_rows_the_drilldown_lists(client):
    """「计划复测」卡片那个数 = 「重点进展」里 `?filter=retest` 筛出来的行数。

    2026-09-27（§5.14.4 第 1 条）。此前它是 `count(RetestPlan.id)`——**数计划条数**，
    而那张卡下钻到「重点进展」、那里**一行一名学生**：一名学生可以挂多份计划
    （一次初测之后既排了学期末、又排了寒假前），于是卡片说 2、点进去 1 行，
    **两边看起来都对**（§11：指标卡上的数必须与它点进去的那个列表同源）。
    开发库上实测过 6 条计划 / 4 名学生。

    判据是**两处相等**，不是「它等于某个数字」：写死一个期望值只能证明其中一处
    此刻等于它，证明不了它们同源。

    夹具（同一名学生两份计划）**是这条用例的主题**：只造一份计划时两种实现给出
    同一个数（1），变异验证不会红——`test_care_api.py` 里已有的一条正是那样的夹具。
    """
    counselor_headers = create_risk_case(client)
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]
    for planned in (date(2026, 10, 15), date(2026, 12, 20)):
        created = client.post(
            f"/api/v1/care-cases/{case_item['case_id']}/retests",
            headers=counselor_headers,
            json={"planned_date": str(planned), "reason": "阶段性复测"},
        )
        assert created.status_code == 200

    headers = auth_headers(client, "leader", "13800000002")
    items = client.get("/api/v1/leader/progress", headers=headers).json()["data"]["items"]
    flagged = [item for item in items if item["retest_planned"]]

    # 先证明有东西可数：两份计划落在**同一名**学生身上，所以「数计划」与「数学生」
    # 在这一刻是两个不同的数——少了这一句，一个两边都是 0 的实现也能过。
    assert len(flagged) == 1, "这条用例的前提是这名学生有未完成的复测计划"

    overview = client.get("/api/v1/analytics/overview", headers=headers).json()["data"]
    assert overview["planned_retests"] == len(flagged), (
        "「计划复测」卡片上的数必须与「重点进展」里 retest_planned 的行数同源"
    )
