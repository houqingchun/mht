"""名册 + 每人最近一场测评结果：心理老师第一次能看见「没有档案的学生」。

为什么值得一个单独的文件（2026-09-17，用户报「当前没有一个视角查看所有学生的测试结果，
只展示了重点学生和长期跟踪的展示」「我是刚刚导入了一个测评，但查不到这里的记录，比如张三」）：

能看见**单个学生**的前三个界面——工作台、重点学生、学生档案——都以
`student_care_case` 为入口（`care_service.list_care_cases` 就是从 `StudentCareCase`
出发的），第四个（统计分析）是聚合页、按 §11 刻意不下发身份。于是**测了、被判为
「一般观察」、因此没有开档案**的学生，在心理老师能到达的界面上不存在。
这份记录是真实的（用户库里那份九月导入的普查里，绝大多数行都是「一般观察」），
而用户在界面上一个都找不到——包括张三。

这个文件钉住四件事：

1. 未测评的学生**也在**列表里（`total_level is None`）。这是它与
   `latest_result_subquery` 唯一的差别（外连接 vs 内连接），也是这一页存在的理由。
2. 「最近一场」按**施测时间**取，不是 `max(id)`——导入的历史普查 id 更大。
3. 「一场都没交卷」与「最近一场还没交卷、更早那场已交卷」是**两件事**：
   前者没有等级；后者取的是**上一次交过卷**的那一场。后者是 2026-09-17 用探针跑出来
   才敢写下的——`latest_result_subquery` 的注释当时把这条写反了（说「刻意不回退」），
   而 `latest_session_order()` 把 `submitted_at IS NULL` 排在后面。
4. 权限是**两道门槛**（身份列归组织名册、等级列归心理详情），两道都要，
   且摘掉任何一道都能让测试变红。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.assessment import AssessmentResult, AssessmentSession
from app.models.care import StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import Student
from app.models.permission import RolePermission
from app.models.scale import AssessmentScale
from app.security.permissions import NONE, ORG_ACCOUNT
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting
from app.tests.test_care_api import create_risk_case
from app.tests.test_data_scope import make_other_school_population

COUNSELOR = ("counselor", "13800000001")


def _scale(db) -> AssessmentScale:
    return db.scalar(select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED"))


def seed_student(db, student_no: str = "S001") -> Student:
    return db.scalar(select(Student).where(Student.student_no == student_no))


def add_sitting(
    db,
    student: Student,
    *,
    level: str = "GENERAL_RANGE",
    days_ago: int = 1,
    total_score: int = 70,
    source: str = "IN_SYSTEM",
) -> AssessmentSession:
    """给这名学生加一场**已交卷**的测评（含结果行）。

    `submitted_at` 显式给出：这些用例的要点就是「哪一场算最近」，由插入顺序或 id
    决定的话，测的就不是排序规则了（`test_analytics_basis.add_sitting` 同一条理由）。
    """
    scale = _scale(db)
    session = make_sitting(
        db,
        student,
        submitted_at=datetime.now(UTC) - timedelta(days=days_ago),
        source=source,
    )
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=0,
            validity_status="VALID",
            total_score=total_score,
            total_level=level,
            rule_version=scale.version,
        )
    )
    db.flush()
    return session


def add_open_sitting(db, student: Student) -> AssessmentSession:
    """一场「开了卷子、还没交」的会话——没有结果行。"""
    return make_sitting(db, student, status="IN_PROGRESS", submitted_at=None)


def results(client, account: str = COUNSELOR[1], role: str = COUNSELOR[0]) -> list[dict]:
    headers = auth_headers(client, role, account)
    response = client.get("/api/v1/students/results", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def by_no(items: list[dict]) -> dict[str, dict]:
    return {item["student_no"]: item for item in items}


def test_a_student_who_has_never_been_assessed_is_still_in_the_list(client, db_session):
    """这一页是**名册**，不是「有结果的人」——用户报的那件事的对立面。

    种子库里 S001 一场都没测过。内连接的 `latest_result_subquery` 会把他丢掉
    （那是对的：聚合的分母不该把「还没测」算成「一般观察」），而这一页必须留着他，
    否则「查谁都能查到」就不成立。变异：把外连接改回内连接，这条变红。
    """
    items = results(client)

    assert [item["student_no"] for item in items] == ["S001"]
    row = items[0]
    assert row["total_level"] is None
    assert row["total_score"] is None
    assert row["submitted_at"] is None
    # 「没有等级」不能冒充「等级是系统内做的」。
    assert row["source"] is None
    # 没测过就没有档案可点。
    assert row["case_id"] is None
    assert row["case_status"] is None


def test_a_row_carries_the_level_the_score_the_date_and_the_source(client, db_session):
    add_sitting(
        db_session,
        seed_student(db_session),
        level="NEEDS_ATTENTION",
        days_ago=3,
        total_score=58,
        source="IMPORTED",
    )

    row = by_no(results(client))["S001"]

    assert row["total_level"] == "NEEDS_ATTENTION"
    assert row["total_score"] == 58
    assert row["source"] == "IMPORTED"
    assert row["student_name"] == "林同学"
    # 日期是**文件里的**测评日期（导入把它写进 `submitted_at`），不是导入那一刻；
    # 等级与日期因此来自同一场，读者不用去别处对。
    expected = (datetime.now(UTC) - timedelta(days=3)).date()
    assert row["submitted_at"].startswith(str(expected))


def test_the_latest_sitting_is_chosen_by_test_date_not_by_id(client, db_session):
    """导入的历史普查 `id` 更大，`max(id)` 会把去年那场当成本次。

    夹具自证：断言 `last_term.id > this_term.id`。少了这一句，这条用例在「按 id 取」
    的实现下也会通过（`test_analytics_basis` 的同一条要点）。
    """
    student = seed_student(db_session)
    this_term = add_sitting(db_session, student, level="GENERAL_RANGE", days_ago=1)
    last_term = add_sitting(db_session, student, level="KEY_ATTENTION", days_ago=180)
    assert last_term.id > this_term.id, "夹具要点：更早那场的 id 更大"

    row = by_no(results(client))["S001"]

    assert row["total_level"] == "GENERAL_RANGE"


def test_a_student_mid_test_keeps_the_level_from_his_last_submitted_sitting(client, db_session):
    """「一场都没交卷」≠「最近一场还没交卷」。

    后者是普查周里的常态：卷子刚发下去，一半学生打开了还没交。`latest_session_order()`
    把 `submitted_at IS NULL` 排在**后面**，所以给他的仍是他**上一次交过卷**的那一场。
    那不是将就：若开卷即掉出「已测评」，学校的关注人数与分母会在整个考试周里随每个人
    的开卷动作上下跳；而「他上一次需要关注」至少是已经发生过的、可复核的事实。

    `latest_result_subquery` 的注释此前说「刻意不回退到他更早的那一场」（与排序相反），
    2026-09-17 用探针跑出来才改。这条用例是那次更正的证据。
    """
    student = seed_student(db_session)
    add_sitting(db_session, student, level="NEEDS_ATTENTION", days_ago=180)
    add_open_sitting(db_session, student)

    row = by_no(results(client))["S001"]

    assert row["total_level"] == "NEEDS_ATTENTION"
    # 而日期是**那一场**的日期：等级与日期必须来自同一场，不能「本次的日期 + 上次的等级」。
    expected = (datetime.now(UTC) - timedelta(days=180)).date()
    assert row["submitted_at"].startswith(str(expected))


def test_a_student_with_only_an_open_sitting_has_no_level(client, db_session):
    """一场都没交过就是「未测评」，不是「一般观察」。"""
    add_open_sitting(db_session, seed_student(db_session))

    row = by_no(results(client))["S001"]

    assert row["total_level"] is None
    # 会话在，等级不在——来源跟着等级走，不跟着会话走。
    assert row["source"] is None


def test_the_list_excludes_students_outside_the_callers_scope(client, db_session):
    """第二所学校的学生**必须带上全链路记录**，否则断言会因为「本来就没有他」而通过。"""
    make_other_school_population(db_session)

    assert {item["student_no"] for item in results(client)} == {"S001"}


def test_the_endpoint_needs_both_the_roster_and_the_psych_detail_capability(client):
    """两道门槛缺一不可——响应里同时有身份列和等级列。

    德育领导持有的是 `ORG_ACCOUNT: READ_SUMMARY`（不在 {MANAGE, READ_BASIC} 里）
    和 `STUDENT_PSYCH_DETAIL: SUMMARY`（不是 SCOPED）；管理员持有名册的管理权、
    却没有心理详情。两个都在这里被挡住——§4 的「描述性等级不可互换」。
    """
    for role, account in (
        ("leader", "13800000002"),
        ("admin", "admin"),
        ("student", "S001"),
    ):
        headers = auth_headers(client, role, account)
        response = client.get("/api/v1/students/results", headers=headers)
        assert response.status_code == 403, f"{role} 不该拿到逐行的等级"
        assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_revoking_the_roster_capability_alone_closes_the_endpoint(client, db_session):
    """这道守卫要能被证明：只留心理详情、摘掉「组织与账号」，仍然 403。

    摘掉的是 `ORG_ACCOUNT`——`STUDENT_PSYCH_DETAIL: SCOPED` 它在默认里仍然有，
    所以这一条 403 只可能来自名册那道门，不可能是心理详情那一道顺带挡下的。

    **变异验证的读数**（2026-09-17 实测，别照直觉猜）：这个能力在**两处**各查一次——
    `students.py` 的路由依赖与 `analytics_service.ensure_student_result_reader`，
    与 `care.py` / `care_service` 的既有形状一致。所以**单独拆掉任何一处都不变红**
    （另一处接着挡），只有**两处一起拆**才让这条变红。这条测试钉住的是
    「`ORG_ACCOUNT` 是必要条件」这个行为，不是「哪一行代码在挡」——
    有人若只删掉一处、看到全绿，那是重复实现，不是守卫失效。
    对照：`STUDENT_PSYCH_DETAIL` 那一道同理，两处一起拆会让下面那条用例的 admin
    分支变红（admin 持有 `ORG_ACCOUNT: MANAGE`，名册那道门放它过去，
    只有心理详情这道门拦得住）。
    """
    assert len(results(client)) == 1

    db_session.add(
        RolePermission(
            role_code=RoleCode.COUNSELOR.value,
            capability_key=ORG_ACCOUNT,
            scope_level=NONE,
        )
    )
    db_session.commit()

    headers = auth_headers(client, *COUNSELOR)
    response = client.get("/api/v1/students/results", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"
    # 心里那一道没被碰过：个案队列照常能用，说明红的原因确实是名册那道门。
    assert client.get("/api/v1/care-cases", headers=headers).status_code == 200


def test_a_closed_case_still_counts_as_having_one(client, db_session):
    """已关闭的档案也是档案——与 `care_service.get_care_case` 同一口径（§1）。

    这里要是把 `CLOSED` 过滤掉，列表会不给出「查看档案」，而那个学生的详情页明明
    打得开——列表与详情各说各话。2026-09-17 之前 `get_care_case` 就干过这件事，
    把「关闭」这个动作自己脚下的页面抽掉了。
    """
    # 走真实的提交路径建档案：S001 答完并交卷，风险事件与档案都是真的。
    create_risk_case(client)
    case = db_session.scalar(select(StudentCareCase))
    assert case is not None

    row = by_no(results(client))["S001"]
    assert row["case_id"] == case.id
    assert row["case_status"] == "PENDING_REVIEW"

    case.status = "CLOSED"
    db_session.commit()

    row = by_no(results(client))["S001"]
    assert row["case_id"] == case.id, "关闭过的档案仍然是这一页该给的那个入口"
    assert row["case_status"] == "CLOSED"
    # 上面那条断言维护的是一个真链接：详情页确实打得开。
    headers = auth_headers(client, *COUNSELOR)
    detail = client.get(f"/api/v1/care-cases/{case.student_id}", headers=headers)
    assert detail.status_code == 200, detail.text
