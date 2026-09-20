"""聚合统计的**总体口径**：数的是人，不是记录；看的是最近一场，不是全部历史。

为什么值得一个单独的文件（2026-09-17）：其余测试对这件事是零信号的。它们建的要么是
一个只有一场测评的学生，要么是「结果与人数一一对应」的夹具——「每人取最近一场」和
「把所有 assessment_result 加起来」在那样的数据上给出同一个数，写对、写反、还是不按人
去重，全都绿。要看出差别，夹具里必须有人**测过两次**。

两条被钉住的规则：

1. `attention_count` 数人。旧实现数的是全部结果行，于是复测过的学生重复计数，而且
   分子只增不减——去年落在重点关注、今年回到一般范围的学生，那条旧结果永远留在里面，
   这个数字随时间单调上升。筛查系统要回答的是「**现在**有多少人需要关注」。
2. 「现在」取的是**最近一场**，按施测时间排（`latest_session_order`），不是 `max(id)`。
   导入的历史普查 `id` 更大，按 id 取会把去年那场当成本次。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.assessment import AssessmentResult, AssessmentSession
from app.models.organization import ClassGroup, Grade, School, Student
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting, published_scale

ATTENTION = "NEEDS_ATTENTION"
KEY = "KEY_ATTENTION"
NORMAL = "GENERAL_RANGE"


def seed_school(db) -> tuple[School, Grade, ClassGroup]:
    school = db.scalar(select(School).where(School.code == "QH"))
    grade = db.scalar(select(Grade).where(Grade.school_id == school.id))
    class_group = db.scalar(select(ClassGroup).where(ClassGroup.grade_id == grade.id))
    return school, grade, class_group


def add_sitting(db, student: Student, level: str, days_ago: int) -> AssessmentSession:
    """给这名学生加一场已交卷的测评。

    `submitted_at` 是显式的：这三条用例的**全部要点**就是「哪一场算最近」，
    由夹具的插入顺序或 id 决定的话，测的就不是排序规则了。
    """
    scale = published_scale(db)
    session = make_sitting(db, student, submitted_at=datetime.now(UTC) - timedelta(days=days_ago))
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=0,
            validity_status="VALID",
            total_score=70,
            total_level=level,
            rule_version=scale.version,
        )
    )
    db.flush()
    return session


def add_students(db, count: int, class_group: ClassGroup, grade: Grade, school: School) -> list[Student]:
    students = []
    for index in range(count):
        student = Student(
            student_no=f"S9{index:02d}",
            name=f"第{index}人",
            masked_name="第**",
            school_id=school.id,
            grade_id=grade.id,
            class_id=class_group.id,
        )
        db.add(student)
        students.append(student)
    db.flush()
    return students


def overview(client) -> dict:
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.get("/api/v1/analytics/overview", headers=headers)
    assert response.status_code == 200
    return response.json()["data"]


def test_a_student_who_recovered_stops_counting_as_attention(client, db_session):
    """同一名学生的两场：上一场需关注、这一场回到一般范围。

    旧口径在这里给 1（两场都进分子），新口径给 0。这就是「分子只增不减」那个毛病的
    最小复现——一个学生不可能靠后来的好转把自己从数字里减出去。
    """
    school, grade, class_group = seed_school(db_session)
    (student,) = add_students(db_session, 1, class_group, grade, school)
    add_sitting(db_session, student, ATTENTION, days_ago=180)
    add_sitting(db_session, student, NORMAL, days_ago=1)
    db_session.commit()

    data = overview(client)

    assert data["assessed_count"] == 1
    assert data["attention_count"] == 0
    assert data["level_counts"] == {"GENERAL_RANGE": 1, "NEEDS_ATTENTION": 0, "KEY_ATTENTION": 0}


def test_two_sittings_by_one_student_count_once(client, db_session):
    """两次都需关注：分子进**一**次，分母也只进一次。

    旧口径给 2 人对 2 场（比率仍是 100%，但人数是错的——「有 2 名学生需要关注」
    会让学校去找第二个学生）。比率看不出来，计数看得出来。
    """
    school, grade, class_group = seed_school(db_session)
    (student,) = add_students(db_session, 1, class_group, grade, school)
    add_sitting(db_session, student, ATTENTION, days_ago=180)
    add_sitting(db_session, student, KEY, days_ago=1)
    db_session.commit()

    data = overview(client)

    assert data["assessed_count"] == 1
    assert data["attention_count"] == 1
    assert data["key_attention_count"] == 1


def test_the_latest_sitting_is_chosen_by_date_not_by_id(client, db_session):
    """导入的历史普查 id 更大，但它不是「最近一场」。

    先插入本学期那场（id 小）、再插入上学期导入的那场（id 大）。按 `max(id)` 取会选到
    上学期那份，于是这名学生的当前状态被读成「需关注」，而他本学期其实已经回到一般范围。
    """
    school, grade, class_group = seed_school(db_session)
    (student,) = add_students(db_session, 1, class_group, grade, school)
    this_term = add_sitting(db_session, student, NORMAL, days_ago=1)
    last_term = add_sitting(db_session, student, KEY, days_ago=200)
    db_session.commit()
    # 夹具自己先自证：id 的顺序与时间的顺序是相反的，否则这条用例什么都没证明。
    assert last_term.id > this_term.id

    data = overview(client)

    assert data["assessed_count"] == 1
    assert data["attention_count"] == 0
    assert data["level_counts"]["GENERAL_RANGE"] == 1


def test_a_small_cohort_gets_no_rate(client, db_session):
    """三个人里的两个 = 67%，而那等于给这两名学生点名。

    计数照给（学校要能问「我这几个人里几个需要关注」），比率不给。
    0 与 None 是两件事：0 是「一个都没有」，None 是「算出来不足为凭」。
    """
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 3, class_group, grade, school)
    add_sitting(db_session, students[0], ATTENTION, days_ago=1)
    add_sitting(db_session, students[1], ATTENTION, days_ago=1)
    add_sitting(db_session, students[2], NORMAL, days_ago=1)
    db_session.commit()

    data = overview(client)

    assert data["assessed_count"] == 3
    assert data["attention_count"] == 2
    assert data["attention_rate"] is None


def test_grade_and_class_rows_carry_an_attention_rate(client, db_session):
    """下钻行不再只有完成情况。

    六个学生一个班，两个需关注：33%。旧表里这一行只有「完成率 100%」——
    而完成率 100% 的班级里，有 2 人需关注的和有 0 人的长得一模一样。
    """
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 6, class_group, grade, school)
    for student in students[:2]:
        add_sitting(db_session, student, ATTENTION, days_ago=1)
    for student in students[2:]:
        add_sitting(db_session, student, NORMAL, days_ago=1)
    db_session.commit()

    headers = auth_headers(client, "counselor", "13800000001")
    classes = client.get("/api/v1/analytics/by-class", headers=headers).json()["data"]["items"]
    row = next(item for item in classes if item["class_id"] == class_group.id)

    assert row["assessed_count"] == 6
    assert row["attention_count"] == 2
    assert row["attention_rate"] == 33
    assert row["cohort_too_small"] is False

    grades = client.get("/api/v1/analytics/by-grade", headers=headers).json()["data"]["items"]
    grade_row = next(item for item in grades if item["grade_id"] == grade.id)
    # 年级把同一个班的学生收进来，所以数字此刻相同；要点是这两个端点都带上了这一列。
    assert grade_row["attention_count"] == 2
    assert grade_row["attention_rate"] == 33


def test_a_class_with_too_few_sittings_says_so_instead_of_a_percentage(client, db_session):
    """一个班只测了两个人时，`attention_rate` 是 None、`cohort_too_small` 是 True。

    界面对着 `None` 写「样本过小」，而不是 `0%`——后者是一个**可反推的断言**：
    「0%」等于「这两人都没事」，而那句断言本来就不该下发给任何人。
    """
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 2, class_group, grade, school)
    add_sitting(db_session, students[0], ATTENTION, days_ago=1)
    add_sitting(db_session, students[1], NORMAL, days_ago=1)
    db_session.commit()

    headers = auth_headers(client, "counselor", "13800000001")
    classes = client.get("/api/v1/analytics/by-class", headers=headers).json()["data"]["items"]
    row = next(item for item in classes if item["class_id"] == class_group.id)

    assert row["assessed_count"] == 2
    assert row["attention_rate"] is None
    assert row["cohort_too_small"] is True


def test_a_student_who_has_not_submitted_has_no_level(client, db_session):
    """名册里还没交卷的学生不进分母，也不算「一般范围」。

    「还没测」和「测了没事」不是一回事。把没交卷的会话算成一般范围会让完成率之外的
    每一个比率都偏乐观——而它偏乐观的方向恰好是"没有问题"。
    """
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 2, class_group, grade, school)
    add_sitting(db_session, students[0], NORMAL, days_ago=1)

    make_sitting(db_session, students[1], status="IN_PROGRESS", submitted_at=None)
    db_session.commit()

    data = overview(client)

    assert data["assessed_count"] == 1
    assert sum(data["level_counts"].values()) == 1
