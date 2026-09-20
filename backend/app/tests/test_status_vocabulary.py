"""Pins the status codes the frontend maps to Chinese labels.

`frontend/src/services/labels.ts` keys its maps on these exact strings. A rename
on the backend renders raw codes to users ("FOLLOWING" instead of 跟进中) and
silently breaks the queue-tab filters — which is exactly what happened once:
the frontend expected FOLLOWING_UP while the backend emitted FOLLOWING.
"""

from datetime import date, timedelta

from app.models.common import now_local_naive
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers

# Must match frontend/src/services/labels.ts STATUS_LABELS.
CASE_STATUSES = {"PENDING_REVIEW", "FOLLOWING", "OBSERVING", "CLOSED"}
# Must match frontend/src/services/labels.ts LEVEL_LABELS.
TOTAL_LEVELS = {"KEY_ATTENTION", "NEEDS_ATTENTION", "GENERAL_RANGE"}
# Must match frontend/src/services/labels.ts GENDER_LABELS.
GENDERS = {"MALE", "FEMALE"}
# Must match frontend/src/services/labels.ts SOURCE_LABELS.
# 写入方只有两处：模型的 `server_default="IN_SYSTEM"`（学生在本系统里作答，不显式写这一列），
# `assessment_import_service.commit_batch` 的 `source="IMPORTED"`。
# 由 test_assessment_import_api.py 的
# `test_imported_sessions_use_the_documented_source_vocabulary` 走真实链路守住。
SOURCES = {"IN_SYSTEM", "IMPORTED"}
# Must match frontend/src/services/labels.ts CALCULATION_STATUS_LABELS（V1.2 阶段 2）。
# 写入方是 `assessment_service`（在线交卷 / 重算）与 `assessment_import_service`（导入）。
CALCULATION_STATUSES = {"PENDING", "CALCULATING", "CALCULATED", "CALCULATION_FAILED"}
# Must match frontend/src/services/labels.ts TESTED_AT_SOURCE_LABELS（V1.2 阶段 2）。
# `PENDING_VERIFICATION` 现在没有写入方——它是 0013 回填时给历史行的列默认值，
# 而那正是它该在词表里的理由：那些行今天仍然在库里，界面上照实显示「待核实」。
TESTED_AT_SOURCES = {"ONLINE_SUBMIT", "IMPORT_FILE", "PENDING_VERIFICATION"}
# Must match frontend/src/services/labels.ts CARE_EVENT_LABELS（V1.2 阶段 7）。
# 唯一的写入方是 `services/care_events.py`，它同时是这张码表的来源——所以下面那条用例
# 断的是 `care_events.ALL_EVENT_TYPES`，不是在这里重抄一遍（重抄的话，服务层加了
# 第九个事件码而 labels.ts 没跟上时，两边都还是「和这里写的一样」）。
CARE_EVENT_TYPES = {
    "CASE_OPENED",
    "MANUAL_REVIEWED",
    "FOLLOW_UP_ADDED",
    "FAMILY_CONTACT_ADDED",
    "RETEST_PLANNED",
    "CASE_CLOSED",
    "CASE_REOPENED",
    "OWNER_ASSIGNED",
}
# Must match frontend/src/services/labels.ts PARTICIPATION_DISPOSITION_LABELS（V1.2 阶段 8）。
# 写入方只有一个：`task_service.mark_target_participation`。后三个是 §18.10 那条算式里的
# 减项，所以它们**同时**是词表项与业务判据——加第四个减项时 `expected_participation_predicate`
# 也要跟着改，而这一条会先红。
PARTICIPATION_DISPOSITIONS_MIRROR = {"REQUIRED", "LEAVE", "EXEMPT", "EXCLUDED"}
# Must match frontend/src/services/labels.ts EXPORT_TYPE_LABELS（V1.2 阶段 8）。
# 与下面两组一样，这里是**镜像**：真正的定义域是 `export_service` 里那几个
# `EXPORT_TYPE_*` 常量，用例去扫它们，不在这里重抄一遍。
EXPORT_TYPES = {
    "CARE_CASES",
    "HIGH_RISK_CASES",
    "SINGLE_CASE",
    "TASK_COMPLETION",
    "NON_PARTICIPANTS",
    "UNMATCHED_IMPORT_ROWS",
}
# Must match frontend/src/services/labels.ts MASK_LEVEL_LABELS（§16.3）。
# 「这份文件是不是实名的」——导出审计唯一要回答的问题，所以它单独一列、单独进词表。
MASK_LEVELS = {"MASKED", "IDENTIFIED"}
# Must match frontend/src/services/labels.ts EXPORT_JOB_STATUS_LABELS。
# `PENDING` / `FAILED` **不在这里**：导出是同步的，作业行与文件在同一个请求里成型，
# 那两种取值在库里不可达（模型上有默认值是 DDL 对齐阶段的形状）。留词条等于给一个
# 永远不会出现的格子写中文——而它会让「这一列漏了一个码」这件事看不出来。
EXPORT_JOB_STATUSES = {"READY", "REVOKED", "EXPIRED"}
# Must match frontend/src/services/labels.ts AUTH_SESSION_STATUS_LABELS（阶段 8）。
# `REVOKED` 是库里真发生过的动作，`EXPIRED` 是现算的（`effective_session_status`）。
AUTH_SESSION_STATUSES = {"ACTIVE", "REVOKED", "EXPIRED"}
# Must match frontend/src/services/labels.ts REMINDER_KIND_LABELS。
# 两个码都住在 `analytics_service.counselor_reminders` 里——那里此前**没有码表**，
# 类别是拼进 `title` 的中文（「跟进 钱浩然」），而工作台那一行前面也写着「跟进」。
# 现在类别由 `kind` 单独下发，中文归 labels.ts。
REMINDER_KINDS = {"FOLLOW_UP", "RETEST"}
# 提醒类别 → 中文，与 `REMINDER_KIND_LABELS` 逐字相同。这一份在测试里是**镜像**，
# 服务端那一半读的是接口实际发出的 `kind`——两边各写一份字面量的话，
# 「后端发了什么」这件事就没有判据了。
REMINDER_KIND_LABELS_MIRROR = {"FOLLOW_UP": "跟进", "RETEST": "复测"}
# Must match frontend/src/services/labels.ts DIMENSION_LABELS.
DIMENSION_CODES = {
    "LEARNING_ANXIETY",
    "INTERPERSONAL_ANXIETY",
    "LONELINESS",
    "SELF_BLAME",
    "SENSITIVITY",
    "PHYSICAL_SYMPTOMS",
    "PHOBIC_TENDENCY",
    "IMPULSIVE_TENDENCY",
}


def open_case(client):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    assert client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers
    ).status_code == 200
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    return counselor, case


def test_case_status_transitions_use_the_documented_vocabulary(client):
    counselor, case = open_case(client)
    seen = {case["case_status"]}
    assert case["case_status"] in CASE_STATUSES

    detail = client.get(
        f"/api/v1/care-cases/{case['student_id']}", headers=counselor
    ).json()["data"]
    risk_event_id = detail["risk_events"][0]["id"]

    # A manual review moves the case into active follow-up.
    reviewed = client.post(
        f"/api/v1/care-cases/{case['case_id']}/reviews",
        headers=counselor,
        json={
            "risk_event_id": risk_event_id,
            "review_result": "建立持续关注档案",
            "confirmed_facts": "已完成初步沟通。",
        },
    )
    assert reviewed.status_code == 200
    after_review = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    seen.add(after_review["case_status"])

    # Closing it.
    closed = client.post(
        f"/api/v1/care-cases/{case['case_id']}/close",
        headers=counselor,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "阶段闭环。",
            "confirm_follow_up_checked": True,
            # 乐观锁（§16.4）。号取自上面那次复核之后**重新读**的那一行——复核会 +1。
            "case_version": after_review["case_version"],
        },
    )
    assert closed.status_code == 200
    seen.add(closed.json()["data"]["status"])

    unknown = seen - CASE_STATUSES
    assert not unknown, f"后端返回了前端无法映射的状态码: {unknown}"


def test_total_level_uses_the_documented_vocabulary(client):
    counselor, case = open_case(client)
    assert case["total_level"] in TOTAL_LEVELS


def test_dimension_codes_use_the_documented_vocabulary(client):
    counselor, case = open_case(client)
    detail = client.get(
        f"/api/v1/care-cases/{case['student_id']}", headers=counselor
    ).json()["data"]
    codes = {d["dimension_code"] for d in detail["dimensions"]}
    unknown = codes - DIMENSION_CODES
    assert not unknown, f"后端返回了前端无法映射的维度编码: {unknown}"


def test_calculation_status_uses_the_documented_vocabulary(client):
    """一场测评「算出来了没有」的码必须是 labels.ts 认得的（V1.2 阶段 2）。

    判据取自**服务层真正会写进去的那几个常量**，不是这里重抄一遍字面量——重抄的话，
    服务层换了写法（比如把 `CALCULATION_FAILED` 改叫 `FAILED`）这里照样全绿，
    而界面上会开始显示 `FAILED`。这正是 §3 第一面要挡的那件事。

    下面那条走一趟真实链路（学生交卷 → 记录页读回来），另加一条把**服务层的四个常量**
    与这张词表逐字对齐——`CALCULATION_FAILED` 那条分支要靠注入异常才到得了，
    所以它只能靠常量比对来守，而不是靠一次真的算崩。
    """
    from app.services import assessment_service

    assert {
        assessment_service.CALCULATION_PENDING,
        assessment_service.CALCULATION_CALCULATING,
        assessment_service.CALCULATION_CALCULATED,
        assessment_service.CALCULATION_FAILED,
    } == CALCULATION_STATUSES, "服务层的评分状态码与 labels.ts 的词表对不上"

    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    submitted = client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers
    ).json()["data"]
    assert submitted["calculation_status"] == assessment_service.CALCULATION_CALCULATED

    # 记录页（学生自己看的那一页）读的也是这一列，而且它是**不条件渲染**的一行，
    # 所以它既要有值、也要能映射。
    history = client.get("/api/v1/student/assessment-history", headers=student_headers).json()["data"][
        "items"
    ]
    emitted = {item["calculation_status"] for item in history} - {None}
    assert emitted, "记录页一条「处理状态」都没有，这一列没接上"
    unknown = emitted - CALCULATION_STATUSES
    assert not unknown, f"后端返回了前端无法映射的评分状态码: {unknown}"


def test_tested_at_source_uses_the_documented_vocabulary(client):
    """「这个测评日期是谁给的」的码同样必须是 labels.ts 认得的（V1.2 阶段 2）。

    两条写入路径的那两个码都由 `assessment_service` 的常量给出，这里直接读它们——
    换名字的时候会红，而不是等界面上显示 `ONLINE_SUBMIT` 才发现。
    （导入路径发的那一个由 `test_assessment_import_api.py` 逐字断言。）
    """
    from app.services import assessment_service

    assert {
        assessment_service.TESTED_AT_SOURCE_ONLINE_SUBMIT,
        assessment_service.TESTED_AT_SOURCE_IMPORT_FILE,
        "PENDING_VERIFICATION",
    } == TESTED_AT_SOURCES, "服务层的码与 labels.ts 的词表对不上"

    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    submitted = client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers
    ).json()["data"]
    assert submitted["tested_at_source"] == assessment_service.TESTED_AT_SOURCE_ONLINE_SUBMIT


def test_care_case_events_use_the_documented_vocabulary(client):
    """关怀档案事件码必须是 `labels.ts` 认得的（V1.2 阶段 7，§3 第一面）。

    `CARE_EVENT_LABELS` 的读者只有一个：个案详情页的「档案事件」时间线。
    `careEventLabel` 认不出的码**原样回退**（那是刻意的——漏码要看得见），
    所以漏一条词条的后果是界面上出现 `CASE_OPEND` 这种原文，而它看起来
    像一条正常的事件。这条用例就是挡它的：**服务层的码表换了名字、或者加了
    第九个事件而前端没跟上，这里都会红**。

    它读 `care_events.ALL_EVENT_TYPES` 而不是在这里重抄一遍字面量——重抄的话，
    服务层把 `CASE_CLOSED` 改叫 `CLOSED` 时，两边仍然「和这里写的一样」。
    下面走一趟真实链路，把事件真的写出来再读详情页那一列：只比常量的话，
    「事件写没写进去」这件事没有任何东西在管。
    """
    from app.services import care_events

    assert set(care_events.ALL_EVENT_TYPES) == CARE_EVENT_TYPES, (
        "服务层的事件码表与 labels.ts 的 CARE_EVENT_LABELS 对不上"
    )

    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    assert client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers
    ).status_code == 200
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]

    detail = client.get(f"/api/v1/care-cases/{case['student_id']}", headers=counselor).json()["data"]
    emitted = {event["event_type"] for event in detail["events"]}
    assert emitted == {care_events.CASE_OPENED}, (
        "一次交卷应当只留下一条开档事件——这一列没接上，或者接错了地方"
    )
    unknown = emitted - CARE_EVENT_TYPES
    assert not unknown, f"后端返回了前端无法映射的档案事件码: {unknown}"


def test_counselor_reminders_use_the_documented_kind_vocabulary(client):
    """提醒类别码必须是 `labels.ts` 认得的，而且**不许把类别再抄进 `title`**（§3 第一面）。

    这一条对应一次真实的 UI 缺陷（2026-09-20）：后端发的 `title` 是「跟进 钱浩然」，
    而复测那一支是「钱浩然 复测」——工作台那一行前面本来就写着类别（`kind`），
    于是屏幕上出现「已逾期 1 天 · 跟进 跟进 钱浩然」。**两处各写了一遍类别的中文**，
    这正是 §3 说的第二定义。

    `title` 现在只是**主题**（学生），类别归 `kind` + `REMINDER_KIND_LABELS`。
    后半句（类别有没有又被抄回 `title`）是这次修复的守卫——少了它，`title` 写成
    什么样这条用例都还是绿的。

    两个码都要真的出现：只断「emitted ⊆ 词表」的话，一个只发 `FOLLOW_UP` 的实现
    照样全绿，而 `REMINDER_KIND_LABELS` 里那一半词条没有任何东西在护。
    """
    counselor, case = open_case(client)

    # 两个来源各造一条：一条已到期的跟进，一份 5 天后的复测计划。
    assert client.post(
        f"/api/v1/care-cases/{case['case_id']}/follow-ups",
        headers=counselor,
        json={
            "record_type": "心理老师访谈",
            "confirmed_facts": "约定下次沟通时间。",
            "next_follow_up_date": date.today().isoformat(),
        },
    ).status_code == 200
    assert client.post(
        f"/api/v1/care-cases/{case['case_id']}/retests",
        headers=counselor,
        json={
            "planned_date": (date.today() + timedelta(days=5)).isoformat(),
            "reason": "两周后复测一次。",
        },
    ).status_code == 200

    items = client.get("/api/v1/counselor/reminders", headers=counselor).json()["data"]["items"]
    emitted = {item["kind"] for item in items}
    assert emitted == REMINDER_KINDS, f"两个来源各造了一条提醒，拿到的却是 {emitted}"
    assert set(REMINDER_KIND_LABELS_MIRROR) == REMINDER_KINDS, (
        "测试里这份镜像与 labels.ts 的 REMINDER_KIND_LABELS 对不上"
    )

    for item in items:
        # 类别那两个字只许出现在 `kind` 里，不许又出现在 `title` 里。
        label = REMINDER_KIND_LABELS_MIRROR[item["kind"]]
        assert item["title"], f"{item['kind']} 这条提醒没有主题"
        assert label not in item["title"], (
            f"类别「{label}」被写进了 title（{item['title']!r}）——界面上会变成"
            f"「{label} {item['title']}」，因为那一行前面已经渲染过类别了"
        )


# --- V1.2 阶段 8：参与口径 / 导出作业 / 登录会话 -------------------------------


def test_participation_dispositions_use_the_documented_vocabulary(client, db_session):
    """参与状态四个码必须是 `labels.ts` 认得的（§3 第一面）。

    定义域在模型里（`PARTICIPATION_DISPOSITIONS`），四个码同时是 §18.10 那条算式的
    减项，所以这条守卫有两个方向：**服务端的取值域**与**前端那张表**必须一样，
    而四个码都真的能写进库、读得回来（只比常量的话，「标记接没接上」没人管）。

    这里不重抄一份字面量去比服务端：那两边就都是「和这里写的一样」。镜像那一半
    在 `PARTICIPATION_DISPOSITIONS_MIRROR`，服务端那一半读模型自己的元组——
    谁改了其中一边，这一条都会红。
    """
    from app.models.assessment import PARTICIPATION_DISPOSITIONS

    assert set(PARTICIPATION_DISPOSITIONS) == PARTICIPATION_DISPOSITIONS_MIRROR, (
        "模型的参与状态码表与 labels.ts 的 PARTICIPATION_DISPOSITION_LABELS 对不上"
    )

    # 走一趟真实链路：四个码各标一次，再从「完成明细」里读回来。
    # 复用 `test_task_participation` 的那三个 helper（造一场只发给一个人的任务、
    # 取他的目标行、标记）——不在这里另写一套：两套「怎么造一场任务」的写法必然漂移。
    from app.tests.test_data_scope import seed_student
    from app.tests.test_task_participation import _mark, _target_of, _task_with

    student = seed_student(db_session)
    task = _task_with(db_session, student, task_no="TASK-VOCAB-PARTICIPATION")
    counselor = auth_headers(client, "counselor", "13800000001")
    target_id = _target_of(db_session, task.id, student.student_no).id

    for index, code in enumerate(sorted(PARTICIPATION_DISPOSITIONS_MIRROR)):
        response = _mark(
            client,
            counselor,
            task.id,
            target_id,
            disposition=code,
            # 非 REQUIRED 必填原因，这是那个端点自己的规则，与词表无关。
            reason=None if code == "REQUIRED" else f"词表用例第 {index} 次",
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["participation_disposition"] == code

    rows = client.get(
        f"/api/v1/assessment-tasks/{task.id}/completion", headers=counselor
    ).json()["data"]["items"]
    emitted = {row["participation_disposition"] for row in rows}
    unknown = emitted - PARTICIPATION_DISPOSITIONS_MIRROR
    assert not unknown, f"后端返回了前端无法映射的参与状态码: {unknown}"


def test_export_job_vocabulary_is_the_documented_one(client, db_session):
    """导出作业的三组码（类型 / 遮蔽等级 / 状态）都必须是 `labels.ts` 认得的。

    三组里只有**状态**走得通「现算」那条路，所以它们各按各的方式守：

    * 类型与遮蔽等级的定义域是 `export_service` 里那几个常量，这里**扫那个模块**
      （`EXPORT_TYPE_*` 等前缀）而不是另抄一份——加第六种导出类型时，这一条会红着
      问「`EXPORT_TYPE_LABELS` 跟上了吗」，而在测试里重抄一遍的地方不会问。
    * 状态走真实链路：新建的一定是 `READY`，撤销过的是 `REVOKED`，而过期只能由
      时间推出来——`effective_export_status` 收 `now`，所以拿一个未来的时刻问它一次
      就够，不必把系统时钟拨过去。**三档都要真的出现一次**：只比常量的话，
      「`effective_export_status` 里撤销与过期的次序写反了」这件事照样全绿。

    `PENDING` / `FAILED` 刻意**不在**镜像里（导出是同步的，它们在库里不可达），
    而这一条会挡住有人把它们加回服务端常量：那时扫描结果会比镜像多两个。
    """
    from app.models.exporting import ExportJob
    from app.services import export_service
    from app.services.export_service import effective_export_status
    from app.tests.test_audit_export_api import create_case_with_followup
    from app.tests.test_export_jobs import create_job

    families = {
        "EXPORT_TYPE_": EXPORT_TYPES,
        "MASK_LEVEL_": MASK_LEVELS,
        "JOB_STATUS_": EXPORT_JOB_STATUSES,
    }
    for prefix, mirror in families.items():
        found = {
            value
            for name, value in vars(export_service).items()
            if name.startswith(prefix) and isinstance(value, str)
        }
        assert found == mirror, (
            f"export_service 的 {prefix}* 与 labels.ts 里那张表对不上："
            f"服务端多出 {found - mirror}，前端多出 {mirror - found}"
        )

    # 走真实链路。先从一份**手上有档案**的心理老师起步：基线种子里一份档案都没有，
    # 而没有档案时的导出是一份只有表头的 CSV——它在「状态」这一列上照样通过，
    # 却在证明一件空的事（`test_export_jobs` 的 `counselor_headers` fixture 同一条理由）。
    headers = create_case_with_followup(client)
    job = create_job(client, headers)
    assert job["status"] == "READY"
    assert job["mask_level"] in MASK_LEVELS
    assert job["export_type"] in EXPORT_TYPES

    revoked = client.post(
        f"/api/v1/export-jobs/{job['id']}/revoke", headers=headers, json={"reason": ""}
    ).json()["data"]
    assert revoked["status"] == "REVOKED"

    row = db_session.get(ExportJob, job["id"])
    far_future = now_local_naive() + timedelta(days=3650)
    # 撤销优先于过期——这一条作业在十年后仍然是 REVOKED 而不是 EXPIRED。
    assert effective_export_status(row, now=far_future) == "REVOKED"

    # 另一条没被撤过的作业才答得上「过期」那一档：拿一条已撤销的去看时间，
    # 看到的永远是撤销（那正是上面那一条要钉住的次序），EXPIRED 会一次都出现不了。
    standing = create_job(client, headers, purpose="词表用例：过期那一档")
    assert (
        effective_export_status(db_session.get(ExportJob, standing["id"]), now=far_future)
        == "EXPIRED"
    )


def test_login_sessions_use_the_documented_vocabulary(client, db_session):
    """登录会话三个状态必须是 `labels.ts` 认得的（阶段 8）。

    `ACTIVE` 与 `REVOKED` 走真实链路（登录两次 → 撤销其中一台 → 再读一次列表），
    `EXPIRED` 只能由时间推出来，所以拿一个未来的时刻直接问 `effective_session_status`。
    **三档都要出现**：只断前两档的话，一个把过期判反了的实现照样全绿——而那正是
    用户看到「已过期」的会话还能继续用、或者一条活跃会话被标成过期的那个 bug。
    """
    from app.models.account import AuthSession
    from app.services.auth_service import effective_session_status

    headers = auth_headers(client, "counselor", "13800000001")
    # 再登一次，造出「第二台设备」——否则列表里只有当前这一条，而那个端点**不许**
    # 撤销当前这一条（「要退出请用右上角的退出」），REVOKED 就一次都出现不了。
    auth_headers(client, "counselor", "13800000001")

    first_list = client.get("/api/v1/auth/sessions", headers=headers).json()["data"]["items"]
    assert len({item["id"] for item in first_list}) >= 2, "同一账号两次登录应当留下两条会话"
    assert "ACTIVE" in {item["status"] for item in first_list}

    other = next(item for item in first_list if not item["is_current"])
    revoked = client.post(f"/api/v1/auth/sessions/{other['id']}/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text

    emitted = {
        item["status"]
        for item in client.get("/api/v1/auth/sessions", headers=headers).json()["data"]["items"]
    }
    assert "REVOKED" in emitted, "撤销之后那一条应当读成 REVOKED"
    unknown = emitted - AUTH_SESSION_STATUSES
    assert not unknown, f"后端返回了前端无法映射的会话状态码: {unknown}"

    row = db_session.get(AuthSession, other["id"])
    far_future = now_local_naive() + timedelta(days=3650)
    # 撤销优先于过期（`effective_session_status` 的判据次序）——这一条撤销过的会话
    # 在十年后仍然是 REVOKED，而不是 EXPIRED。这不是顺手断的：它同时钉住那个次序。
    assert effective_session_status(row, now=far_future) == "REVOKED"

    active = db_session.get(AuthSession, next(i for i in first_list if i["is_current"])["id"])
    assert effective_session_status(active, now=far_future) == "EXPIRED"


def test_roster_gender_uses_the_documented_vocabulary(client):
    """名册发的性别必须是 labels.ts 认得出来的编码。

    导入既收 `女` 也收 `FEMALE`（学校用中文填表），落库只能是编码——否则前端会把
    「女」当成一个未知枚举，显示原样。这里走一遍真实的导入链路再读名册，
    而不是只看种子数据里那一个 MALE，那样即使某个分支把中文原样写库也照样全绿。
    """
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name,性别\nS600,许同学,初一,701,女\n"
    preview = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]
    assert preview["valid_count"] == 1
    client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": preview["batch_id"]},
    )

    items = client.get("/api/v1/students", headers=headers).json()["data"]["items"]
    emitted = {item["gender"] for item in items} - {None}
    assert "FEMALE" in emitted, "中文写法没有归一化成编码"
    unknown = emitted - GENDERS
    assert not unknown, f"后端返回了前端无法映射的性别编码: {unknown}"
