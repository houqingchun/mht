"""任务范围与目标快照（§16.2 / §18.1 / §18.2）——V1.2 第 1 期。

V1.2 的对齐阶段给 `assessment_target` 加了七列快照、给 `assessment_task_scope` 建了
整张表，但**没有一个写入方**（CLAUDE.md 已知缺口 9 最后两条）。于是：

- `assessment_target` 上除 `school_id_snapshot` 外的六列**每一行都是 NULL**；
- `assessment_task_scope` 是 0 行，而它想回答的那个问题（「这场普查当初打算测谁」）
  在库里没有答案。

本文件钉住四件事：

1. 建任务时七列快照**真的写进去了**（§20#1：名册上 N 人 → N 行准确的快照）；
2. 快照**不被后来的名册漂移覆盖**（转班、改名之后它还是发放那一刻的样子）；
3. 补发是**先看后补**（§20#9：确认之后才新增目标行）；
4. 范围行写下来了，而且是**另一张表**——没有那一行时接口说「没有记录」，
   不会拿 `assessment_task.scope_type` 冒充。

第 1 条只走接口是**测不出来**的：`list_task_targets` 在快照为空时会回退到当前名册，
一份六列全 NULL 的目标行照样能答出正确的人名与班名。所以每一条快照断言都同时读了
**库里的列**（`db_session.scalar(select(AssessmentTarget...))`），接口那一遍只证明
「读出来的东西是对的」。
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.account import UserAccount, UserScope
from app.models.assessment import AssessmentTarget, AssessmentTask, AssessmentTaskScope
from app.models.audit import AuditLog
from app.models.enums import ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.tests.conftest import auth_headers

ACCOUNTS = {
    "admin": ("admin", "admin"),
    "counselor": ("counselor", "13800000001"),
    "leader": ("leader", "13800000002"),
    "student": ("student", "S001"),
}

TASK_BODY = {"name": "目标快照用例", "start_at": "2026-09-01", "end_at": "2026-12-31"}


def headers_for(client, role: str) -> dict[str, str]:
    role_code, account = ACCOUNTS[role]
    return auth_headers(client, role_code, account)


def counselor_account(db) -> UserAccount:
    user = db.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    assert user is not None
    return user


def seeded_class(db) -> ClassGroup:
    """种子里的「1班」——`S001` 就在那里。"""
    return db.scalar(select(ClassGroup).where(ClassGroup.name == "1班"))


def add_class(db, name: str) -> ClassGroup:
    """同一所学校、同一个年级下的另一个班。"""
    grade = db.scalar(select(Grade))
    school = db.scalar(select(School).where(School.code == "QH"))
    row = ClassGroup(school_id=school.id, grade_id=grade.id, name=name)
    db.add(row)
    db.flush()
    return row


def add_student(
    db,
    *,
    student_no: str,
    name: str,
    class_group: ClassGroup | None = None,
    gender: str | None = "FEMALE",
    age: int | None = 12,
) -> Student:
    school = db.scalar(select(School).where(School.code == "QH"))
    grade = db.scalar(select(Grade))
    class_group = class_group or seeded_class(db)
    student = Student(
        student_no=student_no,
        name=name,
        masked_name=name,
        school_id=school.id,
        grade_id=grade.id,
        class_id=class_group.id,
        gender=gender,
        age=age,
    )
    db.add(student)
    db.flush()
    return student


def create_task(client, headers) -> int:
    response = client.post("/api/v1/assessment-tasks", headers=headers, json=TASK_BODY)
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def supplement(client, headers, task_id: int, *, confirm: bool, reason: str = "转学补入"):
    return client.post(
        f"/api/v1/assessment-tasks/{task_id}/targets/supplement",
        headers=headers,
        json={"confirm": confirm, "reason": reason},
    )


def end_task(client, headers, task_id: int) -> None:
    """把窗口整个挪到过去——`start_at` 一起挪，否则撞「截止日期不能早于开始日期」。

    「结束」这件事在库里**不是一个状态列**，是 `end_at` 与现在的关系（§12）：
    所以这里改的是日期，不是 `status`。顺带说明那条出路为什么走得通——
    反向改回来就是重开。
    """
    response = client.patch(
        f"/api/v1/assessment-tasks/{task_id}",
        headers=headers,
        json={"start_at": "2025-12-01", "end_at": "2026-01-01"},
    )
    assert response.status_code == 200, response.text


@pytest.fixture()
def counselor(client) -> dict[str, str]:
    return headers_for(client, "counselor")


# --------------------------------------------------------------------------
# §20#1 —— 建任务时生成目标快照
# --------------------------------------------------------------------------


def test_the_target_snapshot_is_written_column_by_column(client, db_session, counselor):
    """§20#1：名册上几个人就发几行，七列快照逐列等于**发放那一刻**的名册。

    这里刻意多建了两名学生（同班一名、另一个班一名），因为只发一名学生时
    「快照写对了」与「快照全 NULL、读的时候回退到名册」在接口上长得一样。
    """
    add_student(db_session, student_no="S002", name="陈同学")
    other_class = add_class(db_session, "2班")
    add_student(db_session, student_no="S003", name="吴同学", class_group=other_class, age=14)

    task_id = create_task(client, counselor)

    targets = db_session.scalars(
        select(AssessmentTarget).where(AssessmentTarget.task_id == task_id)
    ).all()
    assert len(targets) == 3

    roster = {row.student_no: row for row in db_session.scalars(select(Student)).all()}
    assert {target.student_id for target in targets} == {row.id for row in roster.values()}
    for target in targets:
        student = roster[target.student_no_snapshot]
        assert target.student_no_snapshot == student.student_no
        # 快照存的是**真名**：它是一份身份事实，遮蔽是读的时候现算的事。
        assert target.student_name_snapshot == student.name
        assert target.grade_name_snapshot == student.grade.name
        assert target.class_name_snapshot == student.class_group.name
        assert target.gender_snapshot == student.gender
        assert target.age_snapshot == student.age
        assert target.school_id_snapshot == student.school_id
        # 发放那一刻的默认值：这一批是「任务范围」来的，还没人免测或补发。
        assert target.target_source == "TASK_SCOPE"
        assert target.participation_disposition == "REQUIRED"

    # 接口那一遍：读出来的东西与库里那七列一致（快照优先、名册兜底的那一层没被触发）。
    body = client.get(f"/api/v1/assessment-tasks/{task_id}/targets", headers=counselor).json()
    assert body["success"] is True
    items = body["data"]["items"]
    by_id = {target.student_id: target for target in targets}
    assert [item["student_no"] for item in items] == [
        by_id[student_id].student_no_snapshot for student_id in sorted(by_id)
    ]
    assert [item["student_name"] for item in items] == [
        by_id[student_id].student_name_snapshot for student_id in sorted(by_id)
    ]
    assert all(item["grade"] == "初一" for item in items)
    # 建任务发出去的那一批**全部**是任务范围来的；补发才写另一档。
    assert {item["target_source"] for item in items} == {"TASK_SCOPE"}


def test_the_snapshot_outlives_a_roster_change(client, db_session, counselor):
    """§16.2：目标快照**一经发放不得被当前花名册覆盖**。

    学生转班、改名之后，目标行仍然是发放那一刻的样子——这正是七列快照存在的全部
    理由，而它只在真的改一次名册之后才看得见。`student` 那一行回答的是「他现在是谁」。
    """
    task_id = create_task(client, counselor)
    student = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert student is not None

    other_class = add_class(db_session, "3班")
    student.name = "林同学（改名）"
    student.masked_name = "林同学（改名）"
    student.class_id = other_class.id
    db_session.flush()

    target = db_session.scalar(
        select(AssessmentTarget).where(
            AssessmentTarget.task_id == task_id, AssessmentTarget.student_id == student.id
        )
    )
    assert target is not None
    assert target.student_name_snapshot == "林同学"
    assert target.class_name_snapshot == "1班"

    item = client.get(f"/api/v1/assessment-tasks/{task_id}/targets", headers=counselor).json()[
        "data"
    ]["items"][0]
    assert item["student_name"] == "林同学"
    assert item["class_name"] == "1班"


def test_the_declared_scope_is_a_row_of_its_own_table(client, db_session, counselor):
    """「当时是按什么范围发的」记在 `assessment_task_scope`，不是任务上那一列。

    两者今天恰好都是 SCHOOL（建任务还不收范围参数），但它们回答的是两个问题：
    范围是「当初打算测谁」，目标行是「实际发给了谁」。发放之后名册上转进来一个
    学生，目标行会补、范围不会变——所以只留目标行的话，前一个问题就没有答案了。
    """
    task_id = create_task(client, counselor)
    task = db_session.get(AssessmentTask, task_id)
    assert task is not None

    scope = db_session.scalar(
        select(AssessmentTaskScope).where(AssessmentTaskScope.task_id == task_id)
    )
    assert scope is not None
    assert scope.scope_type == "SCHOOL"
    assert scope.school_id == task.school_id
    # `created_by` 不是「系统」：范围是人选的，所以它记的是那个人的账号。
    assert scope.created_by == counselor_account(db_session).id
    assert scope.grade_id is None and scope.class_id is None and scope.student_id is None

    assert (
        client.get(f"/api/v1/assessment-tasks/{task_id}/targets", headers=counselor).json()["data"][
            "scope_type"
        ]
        == "SCHOOL"
    )


def test_a_task_without_a_scope_row_says_so_instead_of_guessing(client, db_session, counselor):
    """没有范围行就是**没有记录**，不拿 `assessment_task.scope_type` 顶替。

    两个真实的来源都落在这里：2026-09-19 之前建的校内任务（那时还没有这张表），
    以及外部导入的批次任务（那一列写着 SCHOOL，可它从来不是「发给全校」——这一批是
    照着一份文件建的，文件里有谁就是谁）。回退会把「不知道」变成一句言之凿凿的
    「全校」，而界面下方那几十行正是反例。
    """
    task_id = create_task(client, counselor)
    scope = db_session.scalar(
        select(AssessmentTaskScope).where(AssessmentTaskScope.task_id == task_id)
    )
    assert scope is not None
    db_session.delete(scope)
    db_session.flush()

    data = client.get(f"/api/v1/assessment-tasks/{task_id}/targets", headers=counselor).json()["data"]
    assert data["scope_type"] is None
    # 目标行不受影响：它们才是「发给了谁」的答案。
    assert len(data["items"]) == 1


# --------------------------------------------------------------------------
# §20#9 —— 补发：先看后补
# --------------------------------------------------------------------------


def test_supplement_previews_first_and_only_writes_after_confirmation(
    client, db_session, counselor
):
    """§20#9：任务创建后新增学生，**补发确认后才新增目标行**。

    第一步必须一行都不写。只断言「确认之后多了一行」是不够的：那样一个「预览即写入」
    的实现照样是绿的，而界面会在用户点「取消」之后留下一批他没有同意过的目标行。
    """
    task_id = create_task(client, counselor)
    before = db_session.scalar(
        select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
    )
    assert before == 1

    newcomer = add_student(db_session, student_no="S009", name="新同学", age=13)

    preview = supplement(client, counselor, task_id, confirm=False, reason="开学转学补入")
    assert preview.status_code == 200, preview.text
    preview_data = preview.json()["data"]
    assert preview_data["total"] == 1
    assert preview_data["candidates"] == [
        {"student_no": "S009", "student_name": "新同学", "grade": "初一", "class_name": "1班"}
    ]
    # 预览不说「新增了几个人」——它没有新增任何人。
    assert "added" not in preview_data
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
        )
        == before
    )

    confirmed = supplement(client, counselor, task_id, confirm=True, reason="开学转学补入")
    assert confirmed.status_code == 200, confirmed.text
    confirmed_data = confirmed.json()["data"]
    assert confirmed_data["before"] == 1
    assert confirmed_data["added"] == 1
    assert confirmed_data["after"] == 2

    rows = db_session.scalars(
        select(AssessmentTarget).where(AssessmentTarget.task_id == task_id)
    ).all()
    assert len(rows) == 2
    added = next(row for row in rows if row.student_id == newcomer.id)
    # 补进来的人**与建任务时发的人长得不一样**，所以来源要记下来：一份完成率报表里
    # 「本来该测的」与「后来补进来的」是两个数（§18.2 的「目标来源」）。
    assert added.target_source == "SUPPLEMENT"
    assert added.status == "NOT_STARTED"
    # 快照一样要写齐——补发那一刻的名册，走的是同一个 `target_snapshot`。
    assert added.student_no_snapshot == "S009"
    assert added.student_name_snapshot == "新同学"
    assert added.grade_name_snapshot == "初一"
    assert added.class_name_snapshot == "1班"
    assert added.age_snapshot == 13
    # 原来那一行一个字段都没被动过。
    original = next(row for row in rows if row.student_id != newcomer.id)
    assert original.target_source == "TASK_SCOPE"


def test_supplementing_an_ended_task_is_refused_with_a_way_out(client, db_session, counselor):
    """已结束的任务不补发，而且拒绝的话里要写出路。

    补出来的会是一行**谁也点不开**的目标行：`create_or_get_session` 在截止日期那道门
    把学生挡在外面（§12）。那比不补更糟——它看起来像已经补好了。出路是有的，不写在
    错误文案里的话，操作员只能去猜。
    """
    task_id = create_task(client, counselor)
    add_student(db_session, student_no="S010", name="迟到的同学")

    ended = client.patch(
        f"/api/v1/assessment-tasks/{task_id}",
        headers=counselor,
        json={"start_at": "2025-12-01", "end_at": "2026-01-01"},
    )
    assert ended.status_code == 200, ended.text

    refused = supplement(client, counselor, task_id, confirm=True)
    assert refused.status_code == 422
    message = refused.json()["error"]["message"]
    assert "已经结束" in message
    assert "截止日期" in message

    # 预览同样被挡住——不然界面会先列出几个人，再在确认那一步给一句 422。
    assert supplement(client, counselor, task_id, confirm=False).status_code == 422
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
        )
        == 1
    )


def test_the_supplement_preview_says_when_it_is_truncated_and_still_sends_everyone(
    client, db_session, counselor
):
    """截断了要说出来（§10），而**确认时补发的是全部候选**。

    这个上限只是显示用的：拿那 200 条样本去插会把「另外那些人」静默丢掉，而屏幕上
    写着「将新增 201 人」——一份名单说的人数与它实际写的人数是两件事，这是这一层
    最不该出现的形状。
    """
    from app.services.task_service import SUPPLEMENT_CANDIDATE_LIMIT

    task_id = create_task(client, counselor)
    extra = SUPPLEMENT_CANDIDATE_LIMIT + 1
    for i in range(extra):
        add_student(db_session, student_no=f"B{i:04d}", name=f"批量同学{i}", gender="MALE")

    preview = supplement(client, counselor, task_id, confirm=False).json()["data"]
    assert preview["total"] == extra
    assert preview["truncated"] is True
    assert preview["limit"] == SUPPLEMENT_CANDIDATE_LIMIT
    assert len(preview["candidates"]) == SUPPLEMENT_CANDIDATE_LIMIT

    confirmed = supplement(client, counselor, task_id, confirm=True).json()["data"]
    assert confirmed["added"] == extra

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
        )
        == extra + 1
    )


# --------------------------------------------------------------------------
# 审计（§16.2：补发必须记操作者、原因、变更前后人数）
# --------------------------------------------------------------------------


def test_only_the_confirmation_writes_an_audit_row(client, db_session, counselor):
    """审计记的是**发生过的事**，不是发生过的请求。

    给预览也记一行「补发目标学生」，轨迹上就会出现两条一字不差的记录而其中一条什么
    也没做——读轨迹的人没有办法分辨它们。与「被拒的读取不写审计」（§9）是同一条。
    """
    task_id = create_task(client, counselor)
    add_student(db_session, student_no="S011", name="补发同学")
    db_session.flush()

    def supplement_audits() -> list[AuditLog]:
        db_session.expire_all()
        return list(
            db_session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "补发目标学生",
                    AuditLog.resource_id == str(task_id),
                )
            ).all()
        )

    assert supplement(client, counselor, task_id, confirm=False).status_code == 200
    assert supplement_audits() == []

    assert (
        supplement(client, counselor, task_id, confirm=True, reason="随迁子女入学").status_code
        == 200
    )
    rows = supplement_audits()
    assert len(rows) == 1
    row = rows[0]
    assert row.resource_type == "ASSESSMENT_TASK"
    assert row.actor_user_id == counselor_account(db_session).id
    # 原因、补发前后人数都进 `detail`：同一场任务补发两次时 action / resource_type /
    # resource_id 逐字相同，只有这几个数能回答「那一次补了几个人、为什么」。
    assert row.detail == "原因=随迁子女入学, 补发前=1, 补发后=2, 新增=1"


def test_a_refused_supplement_writes_no_audit_row(client, db_session, counselor):
    """被拒的补发不写审计——理由同上，一个没有发生的动作不该留下轨迹。"""
    task_id = create_task(client, counselor)
    end_task(client, counselor, task_id)
    assert supplement(client, counselor, task_id, confirm=True).status_code == 422
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "补发目标学生")
        )
        == 0
    )


# --------------------------------------------------------------------------
# 权限：读要两道门槛，写只有心理老师
# --------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["counselor", "leader"])
def test_the_two_task_readers_can_read_the_target_list(client, db_session, counselor, role):
    """心理老师与德育领导都读得到——它是任务读者，也是「组织与账号」的读者。

    德育领导持有的名册权限是 `READ_SUMMARY`（不是 `READ_BASIC`），这里放行它是有意的：
    它本来就该看到「这场普查发给了哪些人」这个**范围**，只是看不到心理详情。
    """
    task_id = create_task(client, counselor)
    response = client.get(f"/api/v1/assessment-tasks/{task_id}/targets", headers=headers_for(client, role))
    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["items"]) == 1


@pytest.mark.parametrize("role", ["admin", "student"])
def test_nobody_else_can_read_the_target_list(client, counselor, role):
    """管理员与学生在**任务读者**那一层就被挡住（§4：任务不是系统级配置）。

    这一条与 `test_task_roles.py` 里那张矩阵是同一条裁决的第三个端点——那里覆盖了
    列表、完成明细与完成统计导出，目标学生名单是本期的第四个口子。
    """
    task_id = create_task(client, counselor)
    response = client.get(f"/api/v1/assessment-tasks/{task_id}/targets", headers=headers_for(client, role))
    assert response.status_code == 403, role


@pytest.mark.parametrize("role", ["admin", "leader", "student"])
def test_nobody_else_can_supplement_targets(client, db_session, counselor, role):
    """写权归心理老师：补发改变的是「这场测评谁要参加」，那是业务不是配置。"""
    task_id = create_task(client, counselor)
    add_student(db_session, student_no="S012", name="越权补发同学")
    db_session.flush()

    refused = client.post(
        f"/api/v1/assessment-tasks/{task_id}/targets/supplement",
        headers=headers_for(client, role),
        json={"confirm": True, "reason": "越权"},
    )
    assert refused.status_code == 403, role
    # 403 之外还要断言**没有写进去**：只挡响应不挡写入的守卫在这个测试里长得一模一样。
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
        )
        == 1
    )


def test_the_target_list_follows_the_readers_data_scope(client, db_session, counselor):
    """§9 的第二个入口：列表按调用者的数据范围收缩。

    造法是**先发放、后收窄**：心理老师起初带 SCHOOL 范围，任务发给两个班；
    范围改成只带 1 班之后，同一个端点只剩 1 班那些行。这正是真实学校里会发生的事
    （账号与权限 → 改范围），而它也是唯一能让「范围谓词写错了」看得见的形状——
    种子库里所有账号的范围都覆盖全部，谓词写对、写反、还是不写，其余测试全绿。
    """
    other_class = add_class(db_session, "4班")
    add_student(db_session, student_no="S013", name="四班同学", class_group=other_class)
    task_id = create_task(client, counselor)

    assert (
        len(
            client.get(
                f"/api/v1/assessment-tasks/{task_id}/targets", headers=counselor
            ).json()["data"]["items"]
        )
        == 2
    )

    user = counselor_account(db_session)
    for row in db_session.scalars(select(UserScope).where(UserScope.user_id == user.id)).all():
        db_session.delete(row)
    db_session.add(
        UserScope(
            user_id=user.id,
            scope_type=ScopeType.CLASS,
            school_id=seeded_class(db_session).school_id,
            class_id=seeded_class(db_session).id,
        )
    )
    db_session.flush()

    items = client.get(
        f"/api/v1/assessment-tasks/{task_id}/targets", headers=counselor
    ).json()["data"]["items"]
    assert [item["student_name"] for item in items] == ["林同学"]
    assert items[0]["class_name"] == "1班"

    # 补发的候选集走同一个谓词：四班那名学生不在补发范围内，而他是已被发放的，
    # 所以这里两个条件同时不成立——真正的判据是**新加的四班学生不被补发**。
    add_student(db_session, student_no="S014", name="另一个四班同学", class_group=other_class)
    preview = supplement(client, counselor, task_id, confirm=False).json()["data"]
    assert preview["candidates"] == []


def test_the_supplement_reason_is_required(client, counselor):
    """原因是这个动作的一部分（§16.2 要求补发记原因），所以它是必填，两个阶段都一样。

    设成「确认时才必填」会造出一个条件必填字段——一条要在服务层、路由层、前端各写
    一遍的规则，而它换来的只是预览时可以少填一格。
    """
    task_id = create_task(client, counselor)
    missing = client.post(
        f"/api/v1/assessment-tasks/{task_id}/targets/supplement",
        headers=counselor,
        json={"confirm": False},
    )
    assert missing.status_code == 422
    blank = supplement(client, counselor, task_id, confirm=False, reason="")
    assert blank.status_code == 422


def test_targets_of_a_task_that_does_not_exist_are_a_404(client, counselor):
    assert client.get("/api/v1/assessment-tasks/999999/targets", headers=counselor).status_code == 404
    assert supplement(client, counselor, 999999, confirm=False).status_code == 404
