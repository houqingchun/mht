"""`GET /auth/me/data-scope-summary` —— 屏幕上的「当前数据范围」那一句话（V2.0.0 §5.2）。

为什么单独一个文件：这条接口存在的全部理由是**让页面说得出下面那些数是谁的数**。
V1.1.6 的毛病是「页面文字：全校 / 实际数据：授权年级」，而那种错在任何既有断言下都是
绿的——后端发的聚合数完全正确，前端也完全正确地渲染了它，唯一的错是**那一句话**。

所以这一组用例断的是**那句话**，而且每一组都成对：既要说出真实范围，也要**不出现
「全校」**。只断前半句的话，一个恒返回「全校」的实现照样绿；只断后半句的话，
一个恒返回空串的实现也绿。

范围行一律用 `replace_scopes` 换掉种子里那一条：种子给心理老师的是 `SCHOOL/青禾`，
不换掉它就观察不到「范围缩小了」——与 `test_data_scope.py` 同一个做法与同一个理由。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.account import UserAccount
from app.models.enums import ScopeType
from app.models.organization import School, Student
from app.tests.conftest import auth_headers
from app.tests.test_data_scope import OTHER_SCHOOL_CODE, make_other_school_student, replace_scopes

SUMMARY_PATH = "/api/v1/auth/me/data-scope-summary"

# 种子里的那名学生与那两个账号（`db/seed.py`）。用例里的中文断言都围着它们转，
# 所以它们在这里各有名字，而不是散在十一条用例里各写一遍字面量。
SEEDED_STUDENT_NO = "S001"
SEEDED_COUNSELOR = "13800000001"


def seeded_user(db, account: str = SEEDED_COUNSELOR) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.account == account))


def seeded_range(db) -> tuple[int, int]:
    """S001 所在的年级与班级 —— 「把范围收到他自己那个班」用的两个 id。

    从**学生行**上取而不是从 `Grade` / `ClassGroup` 上按名字查：这一组用例要说的是
    「范围收窄之后名册真的变小了」，而那个范围的对照组就是这名学生自己。
    """
    row = db.execute(
        select(Student.grade_id, Student.class_id).where(Student.student_no == SEEDED_STUDENT_NO)
    ).one()
    return row[0], row[1]


def summary(client, headers) -> dict:
    response = client.get(SUMMARY_PATH, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def visible_student_nos(client, headers) -> set[str]:
    response = client.get("/api/v1/students", headers=headers)
    assert response.status_code == 200, response.text
    return {row["student_no"] for row in response.json()["data"]["items"]}


def test_the_endpoint_requires_a_login(client):
    """范围摘要是「我」的东西，匿名拿不到——它也是唯一一处把范围写成中文的接口。"""
    assert client.get(SUMMARY_PATH).status_code == 401


def test_a_school_scope_reads_as_school_wide(client, db_session):
    """种子给心理老师的本来就是全校，所以这一条是**未改动**状态下的读数。"""
    user = seeded_user(db_session)
    data = summary(client, auth_headers(client, "counselor", user.account))

    assert data == {"scopeType": "SCHOOL", "displayText": "全校", "schoolWide": True}


def test_a_grade_scope_names_the_grade_and_never_says_school_wide(client, db_session):
    """P0-02 的正题：授权年级的心理老师，屏幕上不能出现「全校」。

    「不出现全校」是**独立的一条**断言，不是上一句的副本——`displayText` 里恰好没有
    这两个字，与这一屏整体不该宣称全校，是两件事（`schoolWide` 是给程序读的那一半）。
    """
    user = seeded_user(db_session)
    grade_id, _class_id = seeded_range(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.GRADE, "grade_id": grade_id})

    data = summary(client, auth_headers(client, "counselor", user.account))

    assert data["scopeType"] == "GRADE"
    assert data["displayText"] == "初一年级"
    assert data["schoolWide"] is False
    assert "全校" not in data["displayText"]


def test_a_class_scope_names_the_class_with_its_grade(client, db_session):
    """班级要连着年级一起念——「1 班」在这所学校里不是一个唯一的说法。"""
    user = seeded_user(db_session)
    _grade_id, class_id = seeded_range(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.CLASS, "class_id": class_id})

    data = summary(client, auth_headers(client, "counselor", user.account))

    assert data["scopeType"] == "CLASS"
    assert data["displayText"] == "初一 1 班"
    assert data["schoolWide"] is False
    assert "全校" not in data["displayText"]


def test_two_dimensions_are_read_as_a_union_and_listed_together(client, db_session):
    """多行范围是**并集**——这正是 `student_scope_predicate` 给的集合。

    这段代码历史上真的错过这个形状：早先的实现遇到第一个匹配的分支就 `return`，
    于是一个同时握着「年级」与「班级」两行的人被告知「初一 1 班」，而他底下的查询其实
    还盖着一整个年级——**屏幕上的范围比真实范围小**，与 P0-02 要修的那个毛病方向相反、
    性质相同。所以这条用例断的是「两段都在」，不是一个更好听的单一称谓。

    两个维度取自**两所学校**（青禾的年级 + 云海的班级），是为了让两段中文在文本上
    分得开、且顺序有据可依（先年级后班级，与 `data_scope_summary` 的拼装次序一致）。
    """
    other = make_other_school_student(db_session)
    user = seeded_user(db_session)
    grade_id, _class_id = seeded_range(db_session)
    replace_scopes(
        db_session,
        user,
        {"scope_type": ScopeType.GRADE, "grade_id": grade_id},
        {"scope_type": ScopeType.CLASS, "class_id": other.class_id},
    )

    data = summary(client, auth_headers(client, "counselor", user.account))

    assert data["scopeType"] == "MIXED"
    assert data["displayText"] == "初一年级、初一 1 班"
    assert data["schoolWide"] is False


def test_a_student_scope_is_described_by_its_count_not_by_a_name(client, db_session):
    """单人级范围只说人数。

    姓名在这一屏上一次都不该出现：这是**权限配置**的一项，不是一份名册；把学生姓名写进
    「谁能看谁」的摘要里，等于让一次权限巡检顺手读到一个学生的名字。所以判据是
    「人数对了」**且**「名字没出现」。

    （`STUDENT` 级范围目前被 `POST /admin/accounts` 挡着——切换过去要重想反推风险，
    CLAUDE.md 缺口 1。这一条不改变那个决定：它验的是**已经存在**的 scope 行怎么被读，
    而那样的行在库里可以由人工数据修正产生。）
    """
    student = db_session.scalar(select(Student).where(Student.student_no == SEEDED_STUDENT_NO))
    user = seeded_user(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.STUDENT, "student_id": student.id})

    headers = auth_headers(client, "counselor", user.account)
    data = summary(client, headers)

    assert data["scopeType"] == "STUDENT"
    assert data["displayText"] == "指定学生（1人）"
    assert data["schoolWide"] is False
    assert student.name not in data["displayText"]
    assert visible_student_nos(client, headers) == {SEEDED_STUDENT_NO}


def test_a_school_row_without_a_school_id_grants_nothing_and_says_so(client, db_session):
    """半填的 `SCHOOL` 行不授予任何东西，所以它也不许读成「全校」。

    `student_scope_predicate` 跳过 `school_id is None` 的 SCHOOL 行（那一条是为了防
    「半填的行授予一切」），于是这一屏如果照读，屏幕上会写着「范围：全校」而下面一个数
    都是空的——**比不说更糟**，因为读者会照它去理解那些空值。判据与谓词逐字相同。
    """
    user = seeded_user(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.SCHOOL, "school_id": None})

    headers = auth_headers(client, "counselor", user.account)
    data = summary(client, headers)

    assert data["scopeType"] == "NONE"
    assert data["displayText"] == "未配置数据范围"
    assert data["schoolWide"] is False
    # 后半句是这一条的落点：一屏说明该长在一条真的什么都看不见的账号上，
    # 否则「未配置数据范围」只是一个文案分支。
    assert visible_student_nos(client, headers) == set()


def test_a_half_filled_grade_row_is_also_treated_as_no_range(client, db_session):
    """`grade_id` 为 NULL 的 GRADE 行同样是同一条判据的第二次。

    少了它，那一行会走进名称查询、查到 0 个名字，然后拼出光秃秃的「年级」两个字
    （`"、".join([]) + "年级"`）——**一句话里没有主语**的范围宣称。
    """
    user = seeded_user(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.GRADE, "grade_id": None})

    data = summary(client, auth_headers(client, "counselor", user.account))

    assert data["scopeType"] == "NONE"
    assert data["displayText"] == "未配置数据范围"


def test_a_user_with_no_scope_rows_is_told_there_is_no_range(client, db_session):
    """没有范围行 = 看不到任何学生（§9 的 fail-safe），界面上就得是这么一句。

    它与半填的那两条共用一个 `NONE`：对使用者而言「没配」与「配错了」的处置是一样的。
    真正不能出现的是「全校」——那会让一个什么都看不见的账号以为自己看的是整所学校。
    """
    user = seeded_user(db_session)
    replace_scopes(db_session, user)

    headers = auth_headers(client, "counselor", user.account)
    data = summary(client, headers)

    assert data["scopeType"] == "NONE"
    assert data["displayText"] == "未配置数据范围"
    assert visible_student_nos(client, headers) == set()


def test_the_summary_and_the_row_filter_are_the_same_range(client, db_session):
    """收紧范围之后，**名册真的跟着变小**——摘要是那一个集合的说法，不是另一套。

    只断 `displayText` 的话，一个「说得对、但底下根本没按范围过滤」的实现是绿的，而那
    正是 P0-02 要修的那个毛病的反向版本。所以这里同时走一次按范围过滤的接口。
    """
    other = make_other_school_student(db_session)
    user = seeded_user(db_session)
    _grade_id, class_id = seeded_range(db_session)
    replace_scopes(db_session, user, {"scope_type": ScopeType.CLASS, "class_id": class_id})

    headers = auth_headers(client, "counselor", user.account)
    data = summary(client, headers)
    nos = visible_student_nos(client, headers)

    assert data["scopeType"] == "CLASS"
    # 三件事一起断，顺序不能反：范围外的那名学生在（否则「没有他」可能是句空话）、
    # 范围里的那名学生在、而**别校那名不在**。
    assert other.student_no == "Y001"
    assert SEEDED_STUDENT_NO in nos
    assert other.student_no not in nos


def test_the_other_school_helper_builds_what_this_file_needs(db_session):
    """自证：上面那两条依赖「云海真的是一所别校」的断言，前提得是真的。

    一个静默退化成「也建在青禾」的 helper 会让 `test_two_dimensions…` 的两段中文变成
    可互换的、也会让 `test_the_summary_and_the_row_filter…` 的「不在名册里」变成一条
    与数据范围无关的断言——两条都会照旧绿，只是绿在一个更弱的命题上。
    """
    other = make_other_school_student(db_session)
    own_school_id = db_session.scalar(
        select(Student.school_id).where(Student.student_no == SEEDED_STUDENT_NO)
    )
    school_code = db_session.scalar(select(School.code).where(School.id == other.school_id))

    assert school_code == OTHER_SCHOOL_CODE
    assert other.school_id != own_school_id, "别校学生的 school_id 必须与种子学生不同"
