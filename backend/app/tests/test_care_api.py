from datetime import date

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.care import FamilyContactRecord, FollowUpRecord, ManualReview, RetestPlan, StudentCareCase
from app.models.organization import Student
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def create_risk_case(client):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert response.status_code == 200
    counselor_headers = auth_headers(client, "counselor", "13800000001")
    return counselor_headers


def test_counselor_can_list_workbench_and_care_cases(client):
    counselor_headers = create_risk_case(client)
    workbench = client.get("/api/v1/counselor/workbench", headers=counselor_headers)
    assert workbench.status_code == 200
    assert workbench.json()["data"]["pending_review"] == 1

    cases = client.get("/api/v1/care-cases", headers=counselor_headers)
    assert cases.status_code == 200
    items = cases.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["pending_risk_events"] == 1
    assert items[0]["case_status"] == "PENDING_REVIEW"


def test_care_case_detail_writes_audit(client, db_session):
    counselor_headers = create_risk_case(client)
    cases = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"]
    student_id = cases[0]["student_id"]

    detail = client.get(f"/api/v1/care-cases/{student_id}", headers=counselor_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["student"]["student_no"] == "S001"
    assert detail.json()["data"]["assessment"]["total_level"] == "GENERAL_RANGE"

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "查看学生详情"))
    assert audit is not None


def test_counselor_can_create_manual_review_and_followup(client, db_session):
    counselor_headers = create_risk_case(client)
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]
    detail = client.get(f"/api/v1/care-cases/{case_item['student_id']}", headers=counselor_headers).json()["data"]
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
    assert review.status_code == 200

    followup = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/follow-ups",
        headers=counselor_headers,
        json={
            "record_type": "心理老师访谈",
            "confirmed_facts": "本次跟进记录已确认事实。",
            "next_follow_up_date": str(date(2026, 9, 30)),
        },
    )
    assert followup.status_code == 200

    care_case = db_session.get(StudentCareCase, case_item["case_id"])
    assert care_case.status == "FOLLOWING"
    assert db_session.scalar(select(ManualReview)) is not None
    assert db_session.scalar(select(FollowUpRecord)) is not None


def test_student_cannot_access_care_cases(client):
    headers = auth_headers(client, "student", "S001")
    response = client.get("/api/v1/care-cases", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_counselor_can_create_family_contact_and_retest(client, db_session):
    counselor_headers = create_risk_case(client)
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]

    family = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/family-contacts",
        headers=counselor_headers,
        json={
            "contact_date": str(date(2026, 9, 20)),
            "contact_person": "母亲",
            "channel": "电话",
            "result": "已联系",
            "support_status": "愿意配合",
            "confirmed_facts": "仅记录与学生支持工作相关的已确认事实。",
            "next_contact_date": str(date(2026, 9, 27)),
        },
    )
    assert family.status_code == 200

    retest = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/retests",
        headers=counselor_headers,
        json={"planned_date": str(date(2026, 10, 15)), "reason": "阶段性复测"},
    )
    assert retest.status_code == 200
    assert db_session.scalar(select(FamilyContactRecord)) is not None
    assert db_session.scalar(select(RetestPlan)) is not None


def test_close_requires_confirmation_and_reopen_records_reason(client, db_session):
    counselor_headers = create_risk_case(client)
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]

    rejected = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/close",
        headers=counselor_headers,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "已检查后续安排。",
            "confirm_follow_up_checked": False,
        },
    )
    assert rejected.status_code == 422

    closed = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/close",
        headers=counselor_headers,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "已检查后续安排。",
            "confirm_follow_up_checked": True,
        },
    )
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "CLOSED"

    reopened = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/reopen",
        headers=counselor_headers,
        json={"reason": "出现新的已确认事实，需要重新跟进。"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["data"]["status"] == "FOLLOWING"
    followup = db_session.scalar(select(FollowUpRecord).where(FollowUpRecord.record_type == "重新打开档案"))
    assert followup is not None


def test_a_closed_case_can_still_be_opened(client):
    """关闭之后这一页还要打得开——否则「重新打开」永远够不着。

    2026-09-17 修：`get_care_case` 此前把 CLOSED 的档案过滤掉了，于是关闭成功之后
    第一件事就撞墙——`closeCase()` 末尾那句 `await load()` 重新拉详情，拿到
    404「关注档案不存在」。用户看到的是「刚点完关闭，系统说这档案不存在」，
    而它其实关好了，只是页面不肯显示。同一个过滤还有两个副作用：
    详情页那个 `v-else` 的「重新打开档案」按钮成了死代码（它的条件是
    `case_status === 'CLOSED'`，而这一页永远拿不到 CLOSED），
    列表里 CLOSED 那行的「查看档案」一律跳到 404。
    """
    counselor_headers = create_risk_case(client)
    case_item = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]

    closed = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/close",
        headers=counselor_headers,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "已检查后续安排。",
            "confirm_follow_up_checked": True,
        },
    )
    assert closed.status_code == 200

    detail = client.get(f"/api/v1/care-cases/{case_item['student_id']}", headers=counselor_headers)
    assert detail.status_code == 200, "关闭之后这一页必须还能打开"
    data = detail.json()["data"]
    assert data["case_status"] == "CLOSED"
    # 关的是这条档案本身，不是另开一条：历史记录的 id 不变
    assert data["case_id"] == case_item["case_id"]


def test_the_detail_picks_the_newest_case_not_the_closed_one(client, db_session):
    """一个学生可以同时有一条上学期关掉的旧档和一条在办的新档。

    `(student_id, status)` 是唯一键，所以「已关闭」与「跟进中」是两行。
    这一页要显示的是**在办的那条**——按 id 取最大，与
    `assessment_service.open_or_reuse_care_case` 选「当前档案」的口径一致。
    """
    student = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    db_session.add(
        StudentCareCase(
            student_id=student.id,
            status="CLOSED",
            close_reason="上学期完成阶段跟进",
            close_note="历史档，保留备查。",
        )
    )
    db_session.commit()

    # 这一次提交会开出一条新的 PENDING_REVIEW，id 比上面的旧档大。
    counselor_headers = create_risk_case(client)

    detail = client.get(f"/api/v1/care-cases/{student.id}", headers=counselor_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["case_status"] == "PENDING_REVIEW"


def test_a_student_can_close_a_second_case(client, db_session):
    """一名学生先后关掉两条档案，第二条不能 500。

    2026-09-17 修：`student_care_case` 上有一条 `(student_id, status)` 的唯一约束
    （迁移 `0012` 去掉），于是「秋季关一条、春季再关一条」——学校每年都会遇到的时序——
    在第二次 `db.flush()` 时抛 IntegrityError。已关闭的档案本来就该累积：
    每一条是一次独立的关怀过程，「关闭档案不得删除历史记录」要保的就是它。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    body = {
        "close_reason": "完成阶段跟进并进入一般观察",
        "close_note": "已检查后续安排。",
        "confirm_follow_up_checked": True,
    }

    first = client.post(f"/api/v1/care-cases/{item['case_id']}/close", headers=counselor, json=body)
    assert first.status_code == 200

    # 下学期又测出问题：没有在办的档案，所以开出新的一条
    # （`open_or_reuse_care_case` 的口径，这里直接建行以免依赖一次完整答题）。
    second_case = StudentCareCase(student_id=item["student_id"], status="PENDING_REVIEW")
    db_session.add(second_case)
    db_session.commit()

    second = client.post(
        f"/api/v1/care-cases/{second_case.id}/close", headers=counselor, json=body
    )
    assert second.status_code == 200, f"第二次关闭失败：{second.json().get('error')}"

    # 两条都留着，都记着各自是怎么关的——历史没有被覆盖掉
    cases = db_session.scalars(
        select(StudentCareCase)
        .where(StudentCareCase.student_id == item["student_id"])
        .order_by(StudentCareCase.id)
    ).all()
    assert [c.status for c in cases] == ["CLOSED", "CLOSED"]
    assert all(c.closed_at is not None for c in cases)
