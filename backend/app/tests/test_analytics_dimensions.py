"""Coverage for the dimension aggregate and workbench reminders endpoints.

Both were added to replace hardcoded demo arrays in the UI, so the important
properties are: they return real persisted data, they aggregate the LATEST
session per student (no double-counting on retest), and they honour the same
capability scoping as the rest of the case surface.
"""

from datetime import date, timedelta

from app.services.analytics_service import REMINDER_LIMIT
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers

# 这里的窗口按**今天**算，不写死日期（2026-09-17 改）：`effective_task_status` 现在会读
# `end_at`，写过期日期的话，下面那句「学生开一份新卷子」会在这条用例自己过期的日子
# 拿到 404——而红的原因不是功能坏了。同一条教训见 CLAUDE.md 的测试注意。
TASK_WINDOW = {
    "start_at": (date.today() - timedelta(days=7)).isoformat(),
    "end_at": (date.today() + timedelta(days=90)).isoformat(),
}


def submit_for_student(client, yes_numbers):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers=yes_numbers)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert response.status_code == 200
    return session_id


def test_dimensions_aggregate_reflects_submitted_answers(client):
    # Question 1-15 map to LEARNING_ANXIETY; answering them YES must show there.
    submit_for_student(client, yes_numbers={1, 2, 3})
    headers = auth_headers(client, "counselor", "13800000001")

    response = client.get("/api/v1/analytics/dimensions", headers=headers)
    assert response.status_code == 200
    items = {row["dimension_code"]: row for row in response.json()["data"]["items"]}

    assert "LEARNING_ANXIETY" in items
    learning = items["LEARNING_ANXIETY"]
    assert learning["assessed_count"] == 1
    assert learning["average_score"] == 3.0
    # 3 of 15 items is well below the HIGH threshold (8).
    assert learning["high_count"] == 0


def test_dimensions_count_a_student_once_across_campaigns(client):
    """A student assessed in two campaigns contributes one row, not two.

    Submitted sessions are locked, so the realistic multi-session case is a
    student enrolled in more than one task — not a literal retake.
    """
    submit_for_student(client, yes_numbers={1})
    counselor = auth_headers(client, "counselor", "13800000001")

    before = client.get("/api/v1/analytics/dimensions", headers=counselor).json()["data"]["items"]
    baseline = {row["dimension_code"]: row["assessed_count"] for row in before}

    # 心理老师开第二轮战役（2026-09-17 起建任务归心理老师，不再是管理员）；
    # 测试库里的心理老师是全校范围，所以每个在读学生都会被发到目标。
    created = client.post(
        "/api/v1/assessment-tasks",
        headers=counselor,
        json={"name": "补充筛查", **TASK_WINDOW},
    )
    assert created.status_code == 200
    second_task_id = created.json()["data"]["id"]

    # The student takes the second assessment with more symptoms endorsed.
    student = auth_headers(client, "student", "S001")
    session = client.post(
        "/api/v1/assessment-sessions", headers=student, json={"task_id": second_task_id}
    )
    assert session.status_code == 200
    session_id = session.json()["data"]["id"]
    save_answers(client, student, session_id, yes_numbers={1, 2, 3, 4})
    assert client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student
    ).status_code == 200

    after = client.get("/api/v1/analytics/dimensions", headers=counselor).json()["data"]["items"]
    for row in after:
        assert row["assessed_count"] == baseline[row["dimension_code"]], row["dimension_code"]

    # The aggregate reflects the LATEST session, so the score moved from 1 to 4.
    learning = next(r for r in after if r["dimension_code"] == "LEARNING_ANXIETY")
    assert learning["average_score"] == 4.0


def test_leader_cannot_read_dimensions(client):
    """Leaders hold SCHOOL scope for aggregates, not SCOPED — but dimensions are
    an aggregate, so this asserts the shared AggregateStatsReader instead."""
    headers = auth_headers(client, "leader", "13800000002")
    assert client.get("/api/v1/analytics/dimensions", headers=headers).status_code == 200

    student_headers = auth_headers(client, "student", "S001")
    assert client.get("/api/v1/analytics/dimensions", headers=student_headers).status_code == 403


def test_reminders_require_case_scope(client):
    """Reminders name individual students, so they need case-level scope."""
    submit_for_student(client, yes_numbers={85})
    counselor = auth_headers(client, "counselor", "13800000001")
    assert client.get("/api/v1/counselor/reminders", headers=counselor).status_code == 200

    leader = auth_headers(client, "leader", "13800000002")
    assert client.get("/api/v1/counselor/reminders", headers=leader).status_code == 403

    student = auth_headers(client, "student", "S001")
    assert client.get("/api/v1/counselor/reminders", headers=student).status_code == 403


def test_reminders_surface_an_overdue_follow_up(client):
    submit_for_student(client, yes_numbers={85})
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]

    # A follow-up dated in the past must come back flagged overdue.
    created = client.post(
        f"/api/v1/care-cases/{case['case_id']}/follow-ups",
        headers=counselor,
        json={
            "record_type": "心理老师访谈",
            "confirmed_facts": "约定下次沟通时间。",
            "next_follow_up_date": "2020-01-01",
        },
    )
    assert created.status_code == 200

    items = client.get("/api/v1/counselor/reminders", headers=counselor).json()["data"]["items"]
    overdue = [item for item in items if item["overdue"]]
    assert overdue, items
    # Overdue items sort first.
    assert items[0]["overdue"] is True
    assert items[0]["kind"] == "FOLLOW_UP"


def _add_follow_ups(client, counselor, case_id, count):
    """给同一条档案挂上 `count` 条未到期跟进。"""
    due = (date.today() + timedelta(days=5)).isoformat()
    for i in range(count):
        response = client.post(
            f"/api/v1/care-cases/{case_id}/follow-ups",
            headers=counselor,
            json={
                "record_type": "心理老师访谈",
                "confirmed_facts": f"第 {i + 1} 次沟通记录。",
                "next_follow_up_date": due,
            },
        )
        assert response.status_code == 200, response.text


def test_reminders_report_the_true_total_when_the_list_is_capped(client):
    """被截断的一批必须自报家门，否则「20 项」在 20 与 400 两种情况下长得一样。

    这条是 2026-09-17 UI 普查的产物：面板上的徽标写的 `items.length`，
    而每个来源封顶 20 条——一个老师手上有 40 条待办时，他看到的、以及据此安排的
    工作量，都是 20。
    """
    submit_for_student(client, yes_numbers={85})
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]

    _add_follow_ups(client, counselor, case["case_id"], REMINDER_LIMIT + 3)

    data = client.get("/api/v1/counselor/reminders", headers=counselor).json()["data"]
    # 上限仍然是上限：payload 不能因为待办多就无界。
    assert len(data["items"]) == REMINDER_LIMIT
    # 但总数说的是**真的**有多少条。
    assert data["total"] == REMINDER_LIMIT + 3
    assert data["truncated"] is True


def test_reminders_are_not_flagged_truncated_below_the_cap(client):
    """反面：没截断就不许报截断。少了这一条，把 `truncated` 写死成 True 也全绿。"""
    submit_for_student(client, yes_numbers={85})
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]

    _add_follow_ups(client, counselor, case["case_id"], 3)

    data = client.get("/api/v1/counselor/reminders", headers=counselor).json()["data"]
    assert len(data["items"]) == 3
    assert data["total"] == 3
    assert data["truncated"] is False


def test_a_closed_case_drops_out_of_the_reminder_panel(client):
    """关掉档案之后，它的待办不再出现在提醒面板上。

    这是 2026-09-20 那处修复在**读者**这一侧的断言：`close_case` 把这份档案名下
    `ACTIVE` 的跟进记录收回成 `CLOSED`，而 `counselor_reminders` 只筛
    `status == "ACTIVE"`——两边合起来才是「档案关了，工作台就不催了」。

    写在面板这一侧是因为它正是用户看得见的那一面：此前那条待办会在档案关闭之后
    **继续以「已逾期」的形态挂在第一屏**，而 §1 里写着「一条 CLOSED 的档案没有
    `next_follow_up_date` 可看」——同一份系统里两句话各说各的。

    先断言关之前它在（只断后半句的话，一个把提醒面板整个写空的实现也是绿的）。
    """
    submit_for_student(client, yes_numbers={85})
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]

    created = client.post(
        f"/api/v1/care-cases/{case['case_id']}/follow-ups",
        headers=counselor,
        json={
            "record_type": "心理老师访谈",
            "confirmed_facts": "约定下次沟通时间。",
            "next_follow_up_date": "2020-01-01",
        },
    )
    assert created.status_code == 200, created.text

    def titles() -> list[str]:
        data = client.get("/api/v1/counselor/reminders", headers=counselor).json()["data"]
        return [item["title"] for item in data["items"]]

    before = client.get(f"/api/v1/care-cases/{case['student_id']}", headers=counselor).json()["data"]
    assert before["case_status"] != "CLOSED", "这条用例要先有一条在办的档案"
    assert titles(), "关档之前它本来就该在提醒面板上"

    closed = client.post(
        f"/api/v1/care-cases/{case['case_id']}/close",
        headers=counselor,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "已检查后续安排。",
            "confirm_follow_up_checked": True,
            "case_version": before["case_version"],
        },
    )
    assert closed.status_code == 200, closed.text

    assert titles() == [], "档案已经关了，提醒面板还在催"
