"""「学生测评记录」：给**没有档案的学生**补上查看途径（2026-09-20）。

用户报的是两件事：

> 「我刚刚导入了一个测评记录，其中张三学生定位为一般观察，但看到未建档，这是什么原因，
> 也没有途径来查看此学生的过往测评信息」

**第一问是设计，不是缺陷**，所以这个文件不为它写用例——全库唯一的开档触发点是重点题
85 / 97 命中（`assessment_service.maybe_raise_risk_events` 的 docstring 逐字写着
「Only 重点题命中写行——`total_level == KEY_ATTENTION` 本身不触发任何写」），
等级本身从不产生风险事件。`test_a_student_without_a_case_can_still_be_read` 顺手把这条
设计钉住（它先断言「有结果、无风险事件」的学生**确实** `get_care_case` 是 404）。

**第二问是真缺口**：`get_care_case` 对无档案的学生回 404「关注档案不存在」，而前端拿不到
HTTP 状态码（§2），于是 `CasesPage.vue` 只渲染一个 `—`——心理老师真的没有任何入口。
而这个文件里的第一条用例钉住的是这个缺口的**修法为什么安全**：`student` / `assessment` /
`dimensions` / `history` 四块本来就是档案无关的，所以它们被抽进
`care_service.student_assessment_records`、由两条路径共用。两处各拼一份时，同一个字段会在
两个屏幕上各说各话，而漂了不会有任何东西报错。
"""

from sqlalchemy import func, select

from app.models.audit import AuditLog
from app.models.care import StudentCareCase
from app.models.enums import RoleCode
from app.models.permission import RolePermission
from app.security.permissions import NONE, STUDENT_PSYCH_DETAIL
from app.tests.conftest import auth_headers
from app.tests.test_care_api import create_risk_case
from app.tests.test_data_scope import make_other_school_population
from app.tests.test_student_results_api import add_sitting, seed_student

COUNSELOR = ("counselor", "13800000001")

RECORDS_URL = "/api/v1/students/{student_id}/assessment-records"


def _records(client, headers, student_id: int) -> dict:
    """读一次「学生测评记录」，顺带断言它是 200。"""
    response = client.get(RECORDS_URL.format(student_id=student_id), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _audit_rows(db_session, action: str | None = None) -> list[AuditLog]:
    statement = select(AuditLog)
    if action is not None:
        statement = statement.where(AuditLog.action == action)
    return list(db_session.scalars(statement).all())


def test_a_student_with_a_case_gets_the_same_history_as_the_case_page(client, db_session):
    """两条路径的四个块**逐字相同**——这是那次抽取的同源守卫。

    「学生测评记录」页与「学生持续关注档案」页读的是同一个学生的同一批事实，所以
    `student` / `assessment` / `dimensions` / `history` 必须一模一样。它们此前各拼各的
    （那正是「同一段装配有两份定义」），现在只有 `care_service.student_assessment_records`
    一处。谁哪天又把它拆成两份，这条会红。

    断言**整个字典**而不是逐字段挑几个：`history` 里每一场的 `dimensions[].max_score`
    这种嵌套值（它来自一次 `ScaleQuestion` 的分组查询）正是「两处各写一份」最容易漏掉
    的那一层，逐字段挑会把它漏掉。

    `events` / `risk_events` 等归档块**不在**比较范围内——它们只长在档案页那一侧，
    那正是这两个函数的差别。`case_id` 是**例外：两边都有**，所以它单独比一次
    （它不在上面那个四块的循环里，因为其余四块由 `student_assessment_records` 一处
    装配、它由外面补上）。
    """

    headers = create_risk_case(client)
    student = seed_student(db_session)
    # **历次里必须有第二场。** 只有一场时「顺序」这个维度上什么都比不出来——把
    # `get_care_case` 那一侧的 `history` 换成 `list(reversed(...))` 仍然全绿（实测过）。
    # 这不是多此一举：`history` 是这个函数里唯一有次序的块，而它恰恰是「两处各写一份」
    # 最容易先漂的那一处。
    add_sitting(db_session, student, days_ago=40, total_score=31, level="NEEDS_ATTENTION")

    records = _records(client, headers, student.id)
    detail = client.get(f"/api/v1/care-cases/{student.id}", headers=headers)
    assert detail.status_code == 200
    case_page = detail.json()["data"]

    # 先证明这一读真的读到了东西，否则下面那个循环比的是两个空结构（「先证明有东西可扫，
    # 再断言它干净」——测试注意那一节）。`create_risk_case` 交了一份命中重点题的卷子，
    # 所以四个块都非空。
    assert len(records["history"]) == 2, "两场都在历次里，否则上面那句关于次序的话不成立"
    assert records["dimensions"], "同上：八维度分必须有值"
    assert records["assessment"]["session_id"] is not None

    for key in ("student", "assessment", "dimensions", "history"):
        assert records[key] == case_page[key], f"{key} 在两个屏幕上不一致——同一个学生的同一批事实"

    # 「他有没有档案、是哪一份」两个屏幕必须给同一个答案：这一页底部那句
    # 「该生尚未建档」与那枚按钮读它，档案页则是拿着它去取详情的那个 id。
    # 两处各写一遍 `order_by(id.desc())` 时，这一条是唯一会红的地方。
    assert records["case_id"] == case_page["case_id"] is not None


def test_a_student_without_a_case_can_still_be_read(client, db_session):
    """无档案的学生读得到——**这一条就是用户报的那件事的修复**。

    先断言 `get_care_case` 是 404（证明这名学生真的没有档案），再断言新端点是 200。
    少了前一半，一个「给所有学生都开了档」的实现也能过，而那样一来这条用例就没有测到
    它要测的那件事——`add_sitting` 只写会话与结果、不写风险事件，正是张三的形状。
    """

    student = seed_student(db_session)
    add_sitting(db_session, student, total_score=24, level="GENERAL_RANGE")
    assert (
        db_session.scalar(
            select(func.count(StudentCareCase.id)).where(StudentCareCase.student_id == student.id)
        )
        == 0
    ), "这一条的夹具前提是「有结果、无档案」，别把它改成会开档的路径"

    headers = auth_headers(client, *COUNSELOR)

    blocked = client.get(f"/api/v1/care-cases/{student.id}", headers=headers)
    assert blocked.status_code == 404
    assert blocked.json()["error"]["code"] == "NOT_FOUND"

    records = _records(client, headers, student.id)
    assert records["assessment"]["total_level"] == "GENERAL_RANGE"
    assert records["assessment"]["total_score"] == 24
    assert [row["total_score"] for row in records["history"]] == [24]
    # 底部那一句「该生尚未建档（重点题未命中）」与那枚「查看关注档案」按钮的判据。
    # 它是 `None` 而不是缺键：前端拿不到状态码（§2），一个缺掉的键在 TS 里
    # 与 `undefined` 同形，而这一页要在**两种**情形下都渲染（只是句子不同）。
    assert records["case_id"] is None


def test_a_student_who_never_tested_gets_an_empty_history_not_a_404(client, db_session):
    """「他还没测」与「查无此人」是两件事。

    种子里的 S001 名册上有他、一场都没测过。前端拿不到状态码（§2），所以这里若回 404，
    屏幕上会是与「学生不存在」一模一样的红条（§14：空态是一句关于数据的话）。
    """

    student = seed_student(db_session)
    headers = auth_headers(client, *COUNSELOR)

    records = _records(client, headers, student.id)
    assert records["history"] == []
    assert records["dimensions"] == []
    assert records["assessment"]["session_id"] is None
    assert records["assessment"]["total_score"] is None
    assert records["assessment"]["total_level"] is None
    # 身份那一块照旧有值——「他还没测」不等于「不认识他」
    assert records["student"]["student_no"] == "S001"


def test_the_total_score_comes_from_the_effective_sitting(client, db_session):
    """`assessment["total_score"]` 取的是**算数的那一场**，不是 `history` 的最后一行。

    这两者在被 §18.8 降级过的学生上会分岔：`latest_session` 要求
    `effective_session_predicate()`，而 `history` **刻意不含它**（被降级的那一场仍然是他
    真实考过的一次，趋势图少一个点就是在抹掉一段事实）。所以「最近一次」那个数只能从
    `assessment` 拿——从 `history[-1]` 取会在这些学生身上给出错的分。

    构造的是最坏的那种次序：被降级的那一场**更新**（`days_ago=1`），于是「取 history 的
    最后一行」会给出 99，而正确答案是 24。
    """

    student = seed_student(db_session)
    superseded = add_sitting(db_session, student, days_ago=1, total_score=99)
    superseded.is_effective = False
    db_session.flush()
    add_sitting(db_session, student, days_ago=5, total_score=24)

    records = _records(client, auth_headers(client, *COUNSELOR), student.id)

    assert records["assessment"]["total_score"] == 24
    # 降级过的那一场**仍然在**历次里（两场都在，只是其中一场不算数）
    assert [row["total_score"] for row in records["history"]] == [24, 99]
    # 因此「从 history 的最后一行取总分」在这里给出的是 99——这正是那条路走不通的证据
    assert records["history"][-1]["total_score"] != records["assessment"]["total_score"]


def test_a_student_out_of_scope_is_denied_and_writes_no_audit(client, db_session):
    """范围外的学生：403，且**一条审计都不留**。

    与 `test_sensitive_reads.py` 里那条同源（§9）：给一次被拒的读取记上「查看测评记录」，
    会让访问轨迹反过来撒谎——那条轨迹要回答的是「谁看了」，不是「谁试过」。

    第二所学校的学生**必须带上全链路记录**（`make_other_school_population` 的 docstring），
    否则这条断言会因为「他本来就没有测评记录」而通过，测的就不是范围那一层了。
    """

    other = make_other_school_population(db_session)
    headers = auth_headers(client, *COUNSELOR)

    denied = client.get(RECORDS_URL.format(student_id=other.id), headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    assert _audit_rows(db_session, "查看测评记录") == []


def test_the_endpoint_needs_psych_detail(client, db_session):
    """门槛就是能力矩阵里那一格，不是写死的角色。

    **先断言 200 再降权限**（`test_permissions.py` 的形状）：只写后半句的话，一个
    「端点根本不存在」的实现也会是绿的。降的是心理老师自己的 `STUDENT_PSYCH_DETAIL`，
    与 `GET /care-cases/{student_id}` 逐字同形——两条路径给的是同一个学生的同一批列，
    门槛不同就是「同一件事两条路径两个答案」。

    之所以是「降权限」而不是「换个角色进来」：默认矩阵里 Leader 的心理详情是 `SUMMARY`，
    而这一条只放行 `SCOPED`，所以角色对照那一条路也要走一遍（下面那半）。
    """

    headers = create_risk_case(client)
    student = seed_student(db_session)
    url = RECORDS_URL.format(student_id=student.id)

    assert client.get(url, headers=headers).status_code == 200

    # 德育领导：默认矩阵下 `SUMMARY` 不是 `SCOPED`，两处都不放行
    leader = auth_headers(client, "leader", "13800000002")
    assert client.get(url, headers=leader).status_code == 403

    # 心理老师自己那一格被降成 NONE：能力层直接拒（`scope_allows` 先拒 NONE）
    db_session.add(
        RolePermission(
            role_code=RoleCode.COUNSELOR.value,
            capability_key=STUDENT_PSYCH_DETAIL,
            scope_level=NONE,
        )
    )
    db_session.commit()

    denied = client.get(url, headers=headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_each_read_writes_an_audit_row(client, db_session):
    """每次读都写一行 `查看测评记录`，`student_id` 与 `resource_id` 都对。

    与档案页的 `查看学生详情` 是两个动作码、两个 `resource_type`：那一条的资源是
    **档案**（`STUDENT_CARE_CASE`，`resource_id` 是 `case_id`），这一条的资源是**学生**
    （`STUDENT`，`resource_id` 是学生 id）。对未建档的学生写 `STUDENT_CARE_CASE` 是一句
    假话——会让按对象筛审计的人以为他开过档，而这正是这一页存在的理由。

    不写 `purpose`：那是**原始答卷**那一档的要求（`key-questions`），档案详情也不要求。
    """

    student = seed_student(db_session)
    add_sitting(db_session, student, total_score=24)
    headers = auth_headers(client, *COUNSELOR)

    _records(client, headers, student.id)
    _records(client, headers, student.id)

    rows = _audit_rows(db_session, "查看测评记录")
    assert len(rows) == 2, "两次读就是两行——这一条读每次都写"
    for row in rows:
        assert row.resource_type == "STUDENT"
        assert row.resource_id == str(student.id)
        assert row.student_id == student.id
        assert row.purpose is None
        assert row.detail is None, "审计行不承载内容（§16.6），这一条也不该往 detail 里写"

    # 与档案页那一侧不共用动作码——否则「他开过档没有」这个问题在轨迹上就答不上来了
    assert _audit_rows(db_session, "查看学生详情") == []
