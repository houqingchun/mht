from datetime import date

from sqlalchemy import select

from app.models.account import UserAccount
from app.models.audit import AuditLog
from app.models.care import (
    CareCaseEvent,
    FamilyContactRecord,
    FollowUpRecord,
    ManualReview,
    RetestPlan,
    StudentCareCase,
)
from app.models.enums import AccountType, RoleCode
from app.models.organization import Student
from app.services import assessment_service, care_events
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def create_risk_case(client):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert response.status_code == 200
    counselor_headers = auth_headers(client, "counselor", "13800000001")
    return counselor_headers


def current_version(client, headers, student_id: int) -> int:
    """现取这名学生当前那条档案的版本号（§16.4 的乐观锁）。

    **每一次成功写入都会 +1**（复核 / 跟进 / 回访 / 复测 / 关档 / 重开 / 转派都会），
    所以一个用例里连着做两个动作时，号必须每一步重新读——拿上一次那个号会在第二个
    动作上收到 409「已经被别人修改过」，而那个 409 是**对的**（它说的正是「你手上
    那一版已经过期」）。界面上同理：每个写入之后都 `await load()` 重新拉详情。

    这里走的是详情接口而不是列表：两者都带 `case_version`（列表行上那一份是给
    「批量分配」逐行带号用的），而详情是这个用例本来就要读的东西。
    """
    detail = client.get(f"/api/v1/care-cases/{student_id}", headers=headers)
    assert detail.status_code == 200
    return detail.json()["data"]["case_version"]


def close_body(version: int) -> dict:
    """一份合法的关档请求体，版本号由调用方给。"""
    return {
        "close_reason": "完成阶段跟进并进入一般观察",
        "close_note": "已检查后续安排。",
        "confirm_follow_up_checked": True,
        "case_version": version,
    }


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
            "case_version": case_item["case_version"],
        },
    )
    assert rejected.status_code == 422

    closed = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/close",
        headers=counselor_headers,
        json=close_body(case_item["case_version"]),
    )
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "CLOSED"

    reopened = client.post(
        f"/api/v1/care-cases/{case_item['case_id']}/reopen",
        headers=counselor_headers,
        json={
            "reason": "出现新的已确认事实，需要重新跟进。",
            # 关档那一步已经 +1 了，所以要重新读一遍——见 `current_version`。
            "case_version": current_version(client, counselor_headers, case_item["student_id"]),
        },
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
        json=close_body(case_item["case_version"]),
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

    first = client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        json=close_body(item["case_version"]),
    )
    assert first.status_code == 200

    # 下学期又测出问题：没有在办的档案，所以开出新的一条
    # （`open_or_reuse_care_case` 的口径，这里直接建行以免依赖一次完整答题）。
    second_case = StudentCareCase(student_id=item["student_id"], status="PENDING_REVIEW")
    db_session.add(second_case)
    db_session.commit()

    second = client.post(
        f"/api/v1/care-cases/{second_case.id}/close",
        headers=counselor,
        json=close_body(second_case.case_version),
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


# ---------------------------------------------------------------------------
# V1.2 阶段 7：乐观锁、档案事件、关闭之后不许直接改（CLAUDE.md §28）
# ---------------------------------------------------------------------------


def case_events(db_session, case_id: int) -> list[CareCaseEvent]:
    """这一份档案的事件，按**写入次序**。

    界面上的时间线是倒序的（`care_service` 按 `id.desc()` 发，最新在前），而这里要断的
    是「事情按什么顺序发生」，所以按 `id` 升序读。不能按 `created_at` 排：
    `now_utc_naive()` 截断到整秒（§20 那条 MySQL `DATETIME(0)` 的四舍五入），
    一个用例里连着写几条会全部并列，顺序就丢了。`id` 是自增 BIGINT，不会并列。
    """
    return list(
        db_session.scalars(
            select(CareCaseEvent)
            .where(CareCaseEvent.care_case_id == case_id)
            .order_by(CareCaseEvent.id)
        ).all()
    )


def event_types(db_session, case_id: int) -> list[str]:
    return [event.event_type for event in case_events(db_session, case_id)]


def second_counselor(db_session) -> int:
    """另建一位心理老师，用来做一次真的**转派**。

    种子里只有一位心理老师（`13800000001`），转给他自己得到的事件是
    「负责人 心理老师 → 心理老师」——读起来像什么都没发生过，而那正是这条事件要
    回答的问题（`OWNER_ASSIGNED` 的 `reason` 写的是新旧两个人的名字）。
    这一行落在用例自己的事务里，随用例回滚（`db_session` 是 `create_savepoint`）。
    """
    account = UserAccount(
        account="13900000001",
        account_type=AccountType.MOBILE,
        display_name="王老师",
        # 这个账号从不登录，所以哈希没有意义——但**不能留空**：这一列 NOT NULL。
        password_hash="not-used-by-this-test",
        role_code=RoleCode.COUNSELOR,
        active=True,
    )
    db_session.add(account)
    db_session.commit()
    return account.id


def test_close_refuses_a_stale_version_and_changes_nothing(client, db_session):
    """关档必须带上读到的那一版；号过期就 409，**而库里那一行一个字都不变**。

    判据不能只是状态码：一个「先把状态改成 CLOSED、再发现号不对、于是回滚」的实现
    在只断 409 的用例下是绿的，而在真实并发里它已经把别人的修改盖掉了一半。
    所以每一次 409 之后都断言 `status` 与 `case_version` 都没动。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    stale = item["case_version"]

    # 复核会 +1（`_bump_case_version`），于是 `stale` 变成「上一次读到的那一版」。
    reviewed = client.post(
        f"/api/v1/care-cases/{item['case_id']}/reviews",
        headers=counselor,
        json={
            "risk_event_id": client.get(
                f"/api/v1/care-cases/{item['student_id']}", headers=counselor
            ).json()["data"]["risk_events"][0]["id"],
            "review_result": "建立持续关注档案",
            "confirmed_facts": "已完成初步沟通。",
        },
    )
    assert reviewed.status_code == 200
    fresh = current_version(client, counselor, item["student_id"])
    assert fresh == stale + 1, "复核没有把版本号 +1，乐观锁就没接上"

    refused = client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        json=close_body(stale),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "CONFLICT"
    care_case = db_session.get(StudentCareCase, item["case_id"])
    db_session.refresh(care_case)
    assert care_case.status != "CLOSED", "号对不上却把档案关掉了"
    assert care_case.case_version == fresh, "被拒的这一次改动不该动版本号"

    accepted = client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        json=close_body(fresh),
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["status"] == "CLOSED"


def test_reopen_refuses_a_stale_version_and_changes_nothing(client, db_session):
    """重开同理：号过期就 409，而档案**仍然是关闭的**。

    「仍然是关闭的」这一句不是同义反复：重开这条路会写 `status` / `reopened_at` /
    `reopened_by` / `reopen_reason`，还会顺带建一条 `重新打开档案` 的跟进记录
    （`care_service.reopen_case`）——一个先写后校验的实现会把这些一起留下，
    而那是一条**凭空多出来的跟进记录**，事后没人分得清它是谁写的。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    stale = item["case_version"]
    assert client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        json=close_body(stale),
    ).status_code == 200

    refused = client.post(
        f"/api/v1/care-cases/{item['case_id']}/reopen",
        headers=counselor,
        json={"reason": "出现新的已确认事实。", "case_version": stale},
    )
    assert refused.status_code == 409
    db_session.expire_all()
    care_case = db_session.get(StudentCareCase, item["case_id"])
    assert care_case.status == "CLOSED"
    assert care_case.reopened_at is None
    assert care_case.reopen_reason is None
    assert (
        db_session.scalar(
            select(FollowUpRecord).where(FollowUpRecord.record_type == "重新打开档案")
        )
        is None
    ), "被拒的重开留下了一条跟进记录"

    accepted = client.post(
        f"/api/v1/care-cases/{item['case_id']}/reopen",
        headers=counselor,
        json={"reason": "出现新的已确认事实。", "case_version": current_version(client, counselor, item["student_id"])},
    )
    assert accepted.status_code == 200


def test_batch_assign_refuses_the_whole_batch_if_any_version_is_stale(client, db_session):
    """批量转派里**任何一行**的号对不上就整批 409，而且一行都不写。

    这是「Validate the whole batch before mutating any row」那条注释的可执行形式：
    只改看得见的那一部分、再报一个比请求小的数，用户没有任何办法知道哪几条被丢下了。
    所以断言分两半——第二行（号过期的那个）没变，**第一行（号正确的那个）也没变**。
    只断第二行的话，一个「逐行处理、撞上哪行报哪行」的实现在这里照样是绿的。

    第二行用一条**已关闭的旧档**凑数（§1 的秋季关档 / 春季再开），不去另建一名学生：
    批量转派按 `case_id` 走，两条档案属于同一个学生完全成立，而多建一名学生要连带
    建他的学校 / 年级 / 班级三行。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    old_case = StudentCareCase(
        student_id=item["student_id"],
        status="CLOSED",
        close_reason="上学期完成阶段跟进",
        close_note="历史档，保留备查。",
    )
    db_session.add(old_case)
    db_session.commit()

    owner_id = second_counselor(db_session)
    refused = client.post(
        "/api/v1/care-cases/batch-assign",
        headers=counselor,
        json={
            "assignments": [
                {"case_id": item["case_id"], "case_version": item["case_version"]},
                # 这条的号是编的——真实版本是 1（ORM 建的行走 `default=1`）。
                {"case_id": old_case.id, "case_version": 999},
            ],
            "owner_id": owner_id,
        },
    )
    assert refused.status_code == 409
    db_session.expire_all()
    assert db_session.get(StudentCareCase, item["case_id"]).owner_id is None, (
        "整批被拒了，号正确的那一行却已经被写进去了"
    )
    assert db_session.get(StudentCareCase, old_case.id).owner_id is None

    accepted = client.post(
        "/api/v1/care-cases/batch-assign",
        headers=counselor,
        json={
            "assignments": [
                {"case_id": item["case_id"], "case_version": item["case_version"]},
                {"case_id": old_case.id, "case_version": old_case.case_version},
            ],
            "owner_id": owner_id,
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["updated"] == 2
    db_session.expire_all()
    assert db_session.get(StudentCareCase, item["case_id"]).owner_id == owner_id


def test_reopening_an_old_case_that_has_a_newer_active_one_is_a_409_not_a_500(client, db_session):
    """★ 这是阶段 7 修掉的那个 500（CLAUDE.md 缺口 9 的第一条）。

    `uq_care_case_one_active_per_student`（生成列 `active_student_id`）断言「同一名学生
    最多一条非 CLOSED 的档案」。秋季关掉一条、春季再开一条是学校每年都会遇到的时序，
    此时点「重新打开」那条**旧的**，此前那版代码无条件设 `FOLLOWING`，两行落在同一个
    `active_student_id` 上 → `IntegrityError` → 用户拿到 500，而错误里只有一句英文。

    现在它是一句 409 加一句人话，并且**指名**现在那条在办档案的编号。判据里那句文案
    不能省：只说「冲突了」的话，老师手上没有任何东西能告诉他该去哪一条上继续。
    """
    counselor = create_risk_case(client)
    old = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    assert client.post(
        f"/api/v1/care-cases/{old['case_id']}/close",
        headers=counselor,
        json=close_body(old["case_version"]),
    ).status_code == 200

    # 下学期又测出问题：没有在办的档案，于是开出新的那一条。
    newer = StudentCareCase(student_id=old["student_id"], status="PENDING_REVIEW")
    db_session.add(newer)
    db_session.commit()

    # **不能用 `current_version` 取号**：它走详情接口，而详情取的是 id 最大那一条
    # （§1 的口径，「当前档案」），此刻那已经是上面新建的 `newer` 了——拿它的号去开
    # 旧档，先撞的是乐观锁那句 409，验不到这里要验的那句话。
    db_session.expire_all()
    old_version = db_session.get(StudentCareCase, old["case_id"]).case_version
    refused = client.post(
        f"/api/v1/care-cases/{old['case_id']}/reopen",
        headers=counselor,
        json={"reason": "上学期的旧档，想重新打开。", "case_version": old_version},
    )
    assert refused.status_code == 409, f"应该是一句 409，实际是 {refused.status_code}"
    message = refused.json()["error"]["message"]
    assert str(newer.id) in message, f"这句冲突没说清是哪一条在办：{message}"

    db_session.expire_all()
    assert db_session.get(StudentCareCase, old["case_id"]).status == "CLOSED"
    active = db_session.scalars(
        select(StudentCareCase).where(
            StudentCareCase.student_id == old["student_id"], StudentCareCase.status != "CLOSED"
        )
    ).all()
    assert [c.id for c in active] == [newer.id], "被拒的重开不该动到在办的那一条"


def test_a_closed_case_refuses_every_new_record_until_it_is_reopened(client, db_session):
    """关闭之后四个入口一律 409，并指向「重新打开档案」（§16.4）。

    它们此前**一个都没查过状态**，而每一个都会把 `status` 改成 `FOLLOWING` / `OBSERVING`
    ——对一条已关闭的档案这样做等于**隐式复活**它：`active_student_id` 立刻非空，
    若这名学生已经又有一条在办档案（秋季关档、春季再开），当场撞唯一键，
    用户拿到一句英文的 500。所以这四个 409 不只是那条规格要求，它们同时是四个 500 的补丁。

    **断言的是「四个都拒」而不是「某一个拒了」**：只测一个的话，另外三个入口里
    任何一个漏掉 `_ensure_open` 都不会有信号。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    risk_event_id = client.get(
        f"/api/v1/care-cases/{item['student_id']}", headers=counselor
    ).json()["data"]["risk_events"][0]["id"]
    assert client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        json=close_body(item["case_version"]),
    ).status_code == 200

    case_id = item["case_id"]
    attempts = {
        "复核": (
            f"/api/v1/care-cases/{case_id}/reviews",
            {
                "risk_event_id": risk_event_id,
                "review_result": "建立持续关注档案",
                "confirmed_facts": "关档之后不该还能写这一条。",
            },
        ),
        "跟进": (
            f"/api/v1/care-cases/{case_id}/follow-ups",
            {
                "record_type": "心理老师访谈",
                "confirmed_facts": "关档之后不该还能写这一条。",
                "next_follow_up_date": str(date(2026, 9, 30)),
            },
        ),
        "家庭回访": (
            f"/api/v1/care-cases/{case_id}/family-contacts",
            {
                "contact_date": str(date(2026, 9, 20)),
                "contact_person": "母亲",
                "channel": "电话",
                "result": "已联系",
                "support_status": "愿意配合",
                "confirmed_facts": "关档之后不该还能写这一条。",
            },
        ),
        "复测计划": (
            f"/api/v1/care-cases/{case_id}/retests",
            {"planned_date": str(date(2026, 10, 15)), "reason": "阶段性复测"},
        ),
    }
    for name, (url, body) in attempts.items():
        response = client.post(url, headers=counselor, json=body)
        assert response.status_code == 409, f"{name} 对已关闭的档案没有返回 409：{response.status_code}"
        assert "重新打开" in response.json()["error"]["message"], (
            f"{name} 的 409 没有指出去路（先重新打开）"
        )

    # 什么都没写进去：四个入口各自的表都是空的。
    db_session.expire_all()
    assert db_session.scalar(
        select(ManualReview).where(ManualReview.care_case_id == case_id)
    ) is None
    assert db_session.scalar(
        select(FollowUpRecord).where(FollowUpRecord.care_case_id == case_id)
    ) is None
    assert db_session.scalar(
        select(FamilyContactRecord).where(FamilyContactRecord.care_case_id == case_id)
    ) is None
    assert db_session.scalar(
        select(RetestPlan).where(RetestPlan.care_case_id == case_id)
    ) is None


def test_the_event_timeline_records_the_whole_lifecycle(client, db_session):
    """八种事件各写一条，顺序就是事情发生的顺序（§16.4 / 需求说明书 §16.6）。

    这条时间线回答的是「**这份档案经历了什么**」，与同一页的审计页签回答的
    「谁在什么时候调了哪个接口」是两件事（`models/care.py` 与 `care_events.py` 的
    docstring 都写着这一条）。所以这里走的是一条**完整的业务闭环**：
    开档 → 复核 → 跟进 → 家庭回访 → 复测 → 关档 → 重开 → 转派。

    三处细节各自钉住一条约定：

    - `CASE_OPENED` 的 `operator_id` 是 **NULL**：它是系统根据一次交卷自动开的，
      不是谁点的按钮（界面上显示「系统」）；
    - `FAMILY_CONTACT_ADDED` 的 `confirmed_facts` 是 **NULL**：§16.6 点名的四类
      不得留存的内容里就有「家庭回访正文」，它留在 `family_contact_record` 上；
    - 转派那一条是**唯一 `from_status` / `to_status` 都空**的事件——它不改状态。
    """
    counselor = create_risk_case(client)  # 交卷 → 系统开档
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    case_id = item["case_id"]
    student_id = item["student_id"]
    risk_event_id = client.get(
        f"/api/v1/care-cases/{student_id}", headers=counselor
    ).json()["data"]["risk_events"][0]["id"]

    def post(suffix: str, body: dict):
        response = client.post(f"/api/v1/care-cases/{case_id}/{suffix}", headers=counselor, json=body)
        assert response.status_code == 200, f"{suffix} 失败：{response.json().get('error')}"
        return response.json()["data"]

    post("reviews", {
        "risk_event_id": risk_event_id,
        "review_result": "建立持续关注档案",
        "confirmed_facts": "已与学生完成初步沟通，记录已确认事实。",
    })
    post("follow-ups", {
        "record_type": "心理老师访谈",
        "confirmed_facts": "本次跟进记录已确认事实。",
        "next_follow_up_date": str(date(2026, 9, 30)),
    })
    post("family-contacts", {
        "contact_date": str(date(2026, 9, 20)),
        "contact_person": "母亲",
        "channel": "电话",
        "result": "已联系",
        "support_status": "愿意配合",
        "confirmed_facts": "仅记录与学生支持工作相关的已确认事实。",
    })
    post("retests", {"planned_date": str(date(2026, 10, 15)), "reason": "阶段性复测"})
    post("close", close_body(current_version(client, counselor, student_id)))
    post("reopen", {
        "reason": "出现新的已确认事实，需要重新跟进。",
        "case_version": current_version(client, counselor, student_id),
    })

    owner_id = second_counselor(db_session)
    assigned = client.post(
        "/api/v1/care-cases/batch-assign",
        headers=counselor,
        json={
            "assignments": [
                {"case_id": case_id, "case_version": current_version(client, counselor, student_id)}
            ],
            "owner_id": owner_id,
        },
    )
    assert assigned.status_code == 200, assigned.json().get("error")

    events = case_events(db_session, case_id)
    assert event_types(db_session, case_id) == [
        care_events.CASE_OPENED,
        care_events.MANUAL_REVIEWED,
        care_events.FOLLOW_UP_ADDED,
        care_events.FAMILY_CONTACT_ADDED,
        care_events.RETEST_PLANNED,
        care_events.CASE_CLOSED,
        care_events.CASE_REOPENED,
        care_events.OWNER_ASSIGNED,
    ], "事件序列与业务闭环对不上"

    # 事件一条都不挂在别的学生身上——复合外键 `care_case_event_fk_case_student`
    # 在库里断言同一件事，这里把「这一份档案的事件」这一层也钉住。
    assert {event.student_id for event in events} == {student_id}

    by_type = {event.event_type: event for event in events}
    assert by_type[care_events.CASE_OPENED].operator_id is None, (
        "开档是系统自动做的，不该记成某个人点的按钮"
    )
    assert by_type[care_events.CASE_CLOSED].reason is not None
    assert by_type[care_events.CASE_REOPENED].from_status == "CLOSED"
    assert by_type[care_events.CASE_REOPENED].to_status == "FOLLOWING"
    # 家庭回访那一条**刻意不留事实**（§16.6）。
    assert by_type[care_events.FAMILY_CONTACT_ADDED].confirmed_facts is None
    # 复核那一条留着，它才是这条时间线能读出「发生过什么」的那一格。
    assert by_type[care_events.MANUAL_REVIEWED].confirmed_facts is not None
    # 转派不改状态，所以两个状态列都空——它是这张表上唯一一条这样的。
    assign_event = by_type[care_events.OWNER_ASSIGNED]
    assert (assign_event.from_status, assign_event.to_status) == (None, None)
    assert "王老师" in (assign_event.reason or ""), "转派的事件里没有新负责人的名字"


def test_a_manual_review_is_attached_to_the_case_it_reviews(client, db_session):
    """复核记录必须写上它复核的是**哪一条档案**（§16.4）。

    这两列（`care_case_id` / `student_id`）在 V1.2 对齐阶段就加好了，而**这一期之前
    没有任何写入方**——一条复核只通过 `risk_event_id` 与档案发生联系，而一场测评可以
    命中多道重点题、一名学生也可以先后有两条档案（秋季关档、春季再开），
    从 `risk_event_id` 反推「它属于哪一条」是猜的。

    复合外键 `manual_review_fk_case_student` 保证这两列指向**同一名学生**；
    这里断言的是它的输入确实被填上了，以及填的是对的。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    risk_event_id = client.get(
        f"/api/v1/care-cases/{item['student_id']}", headers=counselor
    ).json()["data"]["risk_events"][0]["id"]

    assert client.post(
        f"/api/v1/care-cases/{item['case_id']}/reviews",
        headers=counselor,
        json={
            "risk_event_id": risk_event_id,
            "review_result": "建立持续关注档案",
            "confirmed_facts": "已与学生完成初步沟通。",
        },
    ).status_code == 200

    review = db_session.scalar(
        select(ManualReview).where(ManualReview.risk_event_id == risk_event_id)
    )
    assert review is not None
    assert review.care_case_id == item["case_id"], "复核没有写上它复核的是哪一条档案"
    assert review.student_id == item["student_id"]


def test_two_requests_that_both_find_no_case_still_leave_exactly_one(client, db_session, monkeypatch):
    """★ 并发创建关怀档案，最终只留一条未关闭的（§17(a) 的最后一条）。

    `assessment_service.open_or_reuse_care_case` 是一段**读-改-写**：先查有没有在办
    档案，没有才插。两个请求同时到达时（同一场测评的两道重点题各触发一次
    `maybe_raise_risk_events`，或者老师手工重算与一次交卷撞在一起）两边都查不到、
    于是两边都插——V1.2 之前那是**静默地建出两条在办档案**，比现在更糟，只是看不出来。
    唯一键 `uq_care_case_one_active_per_student` 落库之后同样的情况会抛
    `IntegrityError`，而写入侧的处理（savepoint + 捕获 + 重查）就是这一步要验的东西。

    **没有真起两个线程。** 另一个线程要真的撞上唯一键就必须自己 commit，而
    `db_session` 是 session 级共享种子库上的一个外层事务——它一 commit 就留下一条
    真的档案，后面每个用例都会看见（`test_counselor_can_list_workbench_and_care_cases`
    断言 `len(items) == 1`）。所以这里复现的是**那一瞬间的可见性**：让
    `_active_care_case` 的**第一次**调用返回 `None`（＝「另一个请求先到，但它的写还
    没让我看见」），代码于是照并发失败的那一侧往下走、真的去插、真的撞上唯一键，
    再走恢复分支。第二次调用恢复成真实实现——那正是恢复分支要拿到的东西。

    两条断言合起来才说明问题：返回的必须是**原来那一条**（不是新插的），
    而库里**只有一条**在办档案、只有一条开档事件（被回滚的那一次不该留下痕迹）。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]

    real = assessment_service._active_care_case
    calls = {"n": 0}

    def blind_once(db, student_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real(db, student_id)

    monkeypatch.setattr(assessment_service, "_active_care_case", blind_once)

    returned = assessment_service.open_or_reuse_care_case(db_session, item["student_id"])

    assert calls["n"] == 2, "恢复分支没有重查——那说明这一次根本没撞上唯一键"
    assert returned.id == item["case_id"], "撞键之后拿到的是别人的那一条才叫恢复"
    active = db_session.scalars(
        select(StudentCareCase).where(
            StudentCareCase.student_id == item["student_id"],
            StudentCareCase.status != "CLOSED",
        )
    ).all()
    assert [c.id for c in active] == [item["case_id"]], "并发之后留下了两条在办档案"
    assert event_types(db_session, item["case_id"]) == [care_events.CASE_OPENED], (
        "被回滚的那一次插入留下了一条开档事件"
    )


# ---------------------------------------------------------------------------
# 关档收回跟进记录（2026-09-20 心理老师工作台 UI/UX 评审的 C 项）
# ---------------------------------------------------------------------------


def _follow_ups_of(db_session, case_id: int) -> list[FollowUpRecord]:
    return list(
        db_session.scalars(
            select(FollowUpRecord)
            .where(FollowUpRecord.care_case_id == case_id)
            .order_by(FollowUpRecord.id)
        ).all()
    )


def _add_past_follow_up(client, headers, case_id: int, note: str = "心理老师访谈") -> None:
    """挂一条**已经到期**的跟进（2020-01-01），所以它必然进提醒面板。"""
    response = client.post(
        f"/api/v1/care-cases/{case_id}/follow-ups",
        headers=headers,
        json={
            "record_type": note,
            "confirmed_facts": "约定下次沟通时间。",
            "next_follow_up_date": "2020-01-01",
        },
    )
    assert response.status_code == 200, response.text


def test_closing_a_case_retires_its_active_follow_ups(client, db_session):
    """★ 关档要把这份档案名下仍挂着的跟进记录一并结束掉。

    此前 `close_case` 只改档案自己的状态，`follow_up_record` 那些 `ACTIVE` 的行
    原地不动——而 `analytics_service.counselor_reminders` 只筛 `status == "ACTIVE"`
    与到期日，**它不认识档案状态**：于是工作台会给一份已经了结的档案继续发提醒，
    界面上写着「已逾期 N 天」。这与 §1 那条「一条 CLOSED 的档案没有
    `next_follow_up_date` 可看」自相矛盾。

    **收回成 `CLOSED`，不是删除**：那些行是确实做过的跟进记录，§1
    「关闭档案不得删除历史记录」在这儿照样成立。所以这一条同时断两件事——
    它不在 `ACTIVE` 里了（提醒不再催），而**这一行还在**（历史没被抹掉）。
    只断前者的实现（删行）在第二条上会红。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    _add_past_follow_up(client, counselor, item["case_id"])

    before = _follow_ups_of(db_session, item["case_id"])
    assert [f.status for f in before] == ["ACTIVE"], "关档之前它本来就该是待办"
    row_id, student_id = before[0].id, before[0].student_id

    closed = client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        # 上面那条跟进**已经把版本号 +1 了**（`create_follow_up` 也会 bump），
        # 所以这里要现取——`item` 里那一份是补跟进之前读到的。
        json=close_body(current_version(client, counselor, item["student_id"])),
    )
    assert closed.status_code == 200, closed.text

    after = _follow_ups_of(db_session, item["case_id"])
    assert [f.status for f in after] == ["CLOSED"], "档案关了，跟进记录还在催"
    # 行还在，而且还是同一行——收的是状态，不是历史。
    assert [f.id for f in after] == [row_id]
    assert after[0].student_id == student_id


def test_close_and_reopen_cycles_do_not_stack_duplicate_reminders(client, db_session):
    """关闭 / 重开来回几次，待办**始终只有一条**。

    `reopen_case` 每次都会建一条当天到期的「重新打开档案」，那是它有意为之的语义
    （重新打开 = 今天跟进一次）。关档不收回旧的那些时，**每关一次、重开一次就多
    一条一模一样的提醒**——实测演示库里 17 条 ACTIVE 全部出自这条路，19 条提醒里
    只有 11 个不同的标题（钱浩然 ×4、郑浩然 ×4）。

    三条断言各管一段：循环里每一次都只有一条待办（没有堆）、结束时归零（最后一轮
    是关闭）、以及**三次重开留下的三行都还在**（收回不是删除）。第三条不能省：
    把上面那条「行还在」删掉、改成删行的实现，只在前两条上是绿的。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    case_id, student_id = item["case_id"], item["student_id"]
    _add_past_follow_up(client, counselor, case_id)

    for cycle in range(3):
        closed = client.post(
            f"/api/v1/care-cases/{case_id}/close",
            headers=counselor,
            json=close_body(current_version(client, counselor, student_id)),
        )
        assert closed.status_code == 200, closed.text
        assert not [f for f in _follow_ups_of(db_session, case_id) if f.status == "ACTIVE"], (
            f"第 {cycle + 1} 次关档之后还有待办"
        )

        reopened = client.post(
            f"/api/v1/care-cases/{case_id}/reopen",
            headers=counselor,
            json={
                "reason": f"第 {cycle + 1} 次：出现新的已确认事实，需要重新跟进。",
                "case_version": current_version(client, counselor, student_id),
            },
        )
        assert reopened.status_code == 200, reopened.text
        active = [f for f in _follow_ups_of(db_session, case_id) if f.status == "ACTIVE"]
        assert len(active) == 1, f"第 {cycle + 1} 次重开后有 {len(active)} 条待办，应该只有 1 条"

    rows = _follow_ups_of(db_session, case_id)
    # 1 条手工跟进 + 3 条「重新打开档案」，一条都没少。
    assert len(rows) == 4, f"跟进记录被删掉了：只剩 {len(rows)} 行"
    assert [f.record_type for f in rows].count("重新打开档案") == 3


def test_closing_one_case_leaves_the_students_other_case_alone(client, db_session):
    """收的是**这一份**档案的待办，不是这名学生的全部。

    一名学生可以同时有已关闭的旧档案与在办的新档案（§1 那条「秋季关档、春季再开」
    的时序），所以按 `student_id` 去收会关错人：旧档案一关，新档案的待办跟着没了，
    而那条待办正是老师今天要做的事。

    夹具用的是**旧代码留下的那种状态**：一条 CLOSED 的档案上挂着一条 ACTIVE 的跟进
    记录——这正是「关档不收回」这个 bug 的产物。它不是编出来的：把这件事修好之后，
    历史库里就长这样。它的 `active_student_id` 是 NULL，所以不撞
    `uq_care_case_one_active_per_student`。
    """
    counselor = create_risk_case(client)
    item = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    student_id = item["student_id"]

    stale = StudentCareCase(student_id=student_id, status="CLOSED")
    db_session.add(stale)
    db_session.flush()
    stale_follow_up = FollowUpRecord(
        student_id=student_id,
        # `operator_id` 是 NOT NULL 的外键，随便挑一个真实账号就行——这一行没有
        # 经过任何服务层，谁写的对这条用例要断的事没有影响。
        operator_id=db_session.scalar(select(UserAccount.id).order_by(UserAccount.id)),
        record_type="重新打开档案",
        confirmed_facts="这是旧档案上没被收回的那一条。",
        next_follow_up_date=date(2020, 1, 1),
        status="ACTIVE",
        care_case_id=stale.id,
    )
    db_session.add(stale_follow_up)
    db_session.commit()

    closed = client.post(
        f"/api/v1/care-cases/{item['case_id']}/close",
        headers=counselor,
        json=close_body(item["case_version"]),
    )
    assert closed.status_code == 200, closed.text

    assert [f.status for f in _follow_ups_of(db_session, stale.id)] == ["ACTIVE"], (
        "关掉这一份档案，把另一份档案的待办一起收走了"
    )
