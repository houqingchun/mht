"""班级对照：把一名学生的维度得分放到他所在班级与年级的背景上看。

加这张报表的理由是历次趋势在**只有一场**测评时交白卷——MHT 是每学期约一次的普查，
学生在校第一年的趋势图上只有孤零零一个点。对照表不需要历史，第一场就成立。

三件事要钉住：

1. 学生自己的分数取最近一场（与个案详情同口径，不另开一份）。
2. 同班/同年级的均值**按调用者的数据范围算**。一名只覆盖个别学生的账号，
   他的「本班均值」绝不能把范围外的同班同学算进来——那不只是数字偏了，
   而是把别人的分数泄漏给了一个没有权限看它们的人。
3. 样本太小时不给均值，而不是给一个可反推的数。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.account import UserAccount, UserScope
from app.models.assessment import AssessmentResult, AssessmentSession, DimensionResult
from app.models.audit import AuditLog
from app.models.enums import ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale
from app.tests.conftest import auth_headers

DIMENSION = "LEARNING_ANXIETY"


def seed_school(db) -> tuple[School, Grade, ClassGroup]:
    school = db.scalar(select(School).where(School.code == "QH"))
    grade = db.scalar(select(Grade).where(Grade.school_id == school.id))
    class_group = db.scalar(select(ClassGroup).where(ClassGroup.grade_id == grade.id))
    return school, grade, class_group


def add_students(db, count: int, class_group: ClassGroup, grade: Grade, school: School) -> list[Student]:
    students = []
    for index in range(count):
        student = Student(
            student_no=f"S8{index:02d}",
            name=f"对照第{index}人",
            masked_name="对**",
            school_id=school.id,
            grade_id=grade.id,
            class_id=class_group.id,
        )
        db.add(student)
        students.append(student)
    db.flush()
    return students


def add_sitting(db, student: Student, score: int) -> AssessmentSession:
    """一场已交卷的测评，带一条 `LEARNING_ANXIETY` 的维度结果。"""
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED"))
    session = AssessmentSession(
        student_id=student.id,
        scale_id=scale.id,
        scale_version=scale.version,
        status="SUBMITTED",
        submitted_at=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(session)
    db.flush()
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=0,
            validity_status="VALID",
            total_score=score,
            total_level="GENERAL_RANGE",
            rule_version=scale.version,
        )
    )
    db.add(
        DimensionResult(
            session_id=session.id,
            dimension_code=DIMENSION,
            score=score,
            level="LOW",
            interpretation="一般",
            rule_version=scale.version,
        )
    )
    db.flush()
    return session


def comparison(client, student_id: int, account: str = "13800000001") -> dict:
    headers = auth_headers(client, "counselor", account)
    response = client.get(f"/api/v1/care-cases/{student_id}/comparison", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_the_student_is_compared_with_their_class_and_grade(client, db_session):
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 5, class_group, grade, school)
    add_sitting(db_session, students[0], 15)
    for index, student in enumerate(students[1:], start=1):
        add_sitting(db_session, student, index)
    db_session.commit()

    data = comparison(client, students[0].id)

    assert data["grade_name"] == grade.name
    assert data["class_name"] == class_group.name
    item = next(row for row in data["items"] if row["dimension_code"] == DIMENSION)
    assert item["score"] == 15
    assert item["max_score"] > 0  # 来自题库，作展示分数的分母
    # 这一名学生 + 另外四名同班同学（`add_students(5)` 里的第一个就是被对照的那个）。
    # (15 + 1 + 2 + 3 + 4) / 5 = 5.0
    assert item["class_size"] == 5
    assert item["class_average"] == 5.0
    assert item["grade_size"] == 5
    assert item["grade_average"] == 5.0


def test_the_cohort_average_stops_at_the_callers_data_scope(client, db_session):
    """只覆盖这名学生的账号，他的「本班均值」里只有他自己。

    同班另外四名同学**在同一个班**、同一场测评里——唯一把他们挡在外面的就是范围谓词。
    少了它，这个接口会把四个没有权限看的人的平均分交给调用者；而平均分加上
    「本班 5 人」这句话，读者能反推出一个不小的区间。
    """
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 5, class_group, grade, school)
    add_sitting(db_session, students[0], 15)
    for student in students[1:]:
        add_sitting(db_session, student, 0)

    # 把这位心理老师的范围从「全校」收成「只看这一名学生」，并把他的登录凭据
    # 换成另一个账号名（改的是同一个人，但用例要的是"一个范围更窄的调用者"）。
    counselor = db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    counselor.account = "13800000009"
    db_session.query(UserScope).filter(UserScope.user_id == counselor.id).delete()
    db_session.add(
        UserScope(
            user_id=counselor.id,
            scope_type=ScopeType.STUDENT,
            school_id=school.id,
            student_id=students[0].id,
        )
    )
    db_session.commit()

    data = comparison(client, students[0].id, account="13800000009")

    item = next(row for row in data["items"] if row["dimension_code"] == DIMENSION)
    assert item["score"] == 15
    # 范围里只剩他一个人：计数说 1，均值按「样本太小」扣住不给。
    assert item["class_size"] == 1
    assert item["class_average"] is None
    assert item["grade_average"] is None


def test_a_student_outside_the_scope_is_refused(client, db_session):
    school, grade, class_group = seed_school(db_session)
    students = add_students(db_session, 2, class_group, grade, school)
    add_sitting(db_session, students[0], 5)

    counselor = db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    counselor.account = "13800000009"
    db_session.query(UserScope).filter(UserScope.user_id == counselor.id).delete()
    db_session.add(
        UserScope(
            user_id=counselor.id,
            scope_type=ScopeType.STUDENT,
            school_id=school.id,
            student_id=students[0].id,
        )
    )
    db_session.commit()

    headers = auth_headers(client, "counselor", "13800000009")
    response = client.get(f"/api/v1/care-cases/{students[1].id}/comparison", headers=headers)
    assert response.status_code in (403, 404)
    # 拒绝**不写审计**：给一次被拒的读取记上「查看班级对照」会让访问轨迹反过来撒谎
    # （与重点题、个案详情同一条规则）。
    actions = db_session.scalars(select(AuditLog.action)).all()
    assert "查看班级对照" not in actions


def test_reading_the_comparison_is_audited(client, db_session):
    school, grade, class_group = seed_school(db_session)
    (student,) = add_students(db_session, 1, class_group, grade, school)
    add_sitting(db_session, student, 5)
    db_session.commit()

    comparison(client, student.id)

    row = db_session.scalar(select(AuditLog).where(AuditLog.action == "查看班级对照"))
    assert row is not None
    assert row.student_id == student.id
    assert row.resource_type == "STUDENT_CARE_CASE"


def test_a_student_who_has_not_submitted_gets_no_rows(client, db_session):
    """没交卷就没有分数可对照——空列表，而不是一行 0 分。

    一行 0 分会被读成「这个维度他没问题」，而事实是「我们还不知道」。
    """
    school, grade, class_group = seed_school(db_session)
    (student,) = add_students(db_session, 1, class_group, grade, school)
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED"))
    db_session.add(
        AssessmentSession(
            student_id=student.id,
            scale_id=scale.id,
            scale_version=scale.version,
            status="IN_PROGRESS",
        )
    )
    db_session.commit()

    data = comparison(client, student.id)

    assert data["items"] == []
    assert data["session_id"] is not None


def test_the_leader_cannot_open_the_comparison(client, db_session):
    """德育领导的 `STUDENT_PSYCH_DETAIL` 是 `SUMMARY`，它连个案接口都不进，
    自然也不能进这张表——一张写着某个孩子各维度分数的表，正是汇总视图的反面。"""
    school, grade, class_group = seed_school(db_session)
    (student,) = add_students(db_session, 1, class_group, grade, school)
    add_sitting(db_session, student, 5)
    db_session.commit()

    headers = auth_headers(client, "leader", "13800000002")
    response = client.get(f"/api/v1/care-cases/{student.id}/comparison", headers=headers)
    assert response.status_code == 403
