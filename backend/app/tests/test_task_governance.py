"""测评任务删除/作废治理（V2.0.0 §4 P0-01 / §12 Task Governance）。

这一组的判据只有一句：**一场任务的两条出路，取决于它有没有留下正式事实。**

- 没留下事实（目标行之外的会话 / 结果 / 已提交批次 / 筛查信号全为 0）→ **物理删除**，
  连同它的目标行与范围行一起走；
- 留下了事实 → **作废**：`status='VOIDED'`，那一场里的有效会话全部退位、还没人处理的
  筛查信号一并作废，而**原始答卷、结果、人工复核、关怀档案一条都不删**。

所以下面每一条都同时看**两处**：接口回了什么，以及**库里那几行的状态**。只看前者的话，
一个「接口说作废了、库里什么都没动」的实现照样全绿——而那正是这一节最贵的那种错
（界面上写着作废，统计里它还在）。

第 5 条（`test_void_task_excludes_from_latest_result`）是唯一一条**不看这一场**的：它问的是
「作废之后，这名学生的『当前状态』还算不算它」。§4.14 那条防御性约束（`latest_result_subquery`
与 `latest_session` 都要排除已作废任务）在这一条上才有读者——少了它，一场作废的普查仍然
是全校关注率的分子，而屏幕上没有任何东西看得出来。
"""

import pytest
from sqlalchemy import func, select

from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    AssessmentTaskScope,
    DimensionResult,
    RiskEvent,
)
from app.models.care import ManualReview, StudentCareCase
from app.models.importing import AssessmentImportBatch
from app.models.organization import Student
from app.services.assessment_service import save_answer, score_session
from app.tests.conftest import auth_headers
from app.tests.factories import make_risk_event, make_sitting

ACCOUNTS = {
    "admin": ("admin", "admin"),
    "counselor": ("counselor", "13800000001"),
    "leader": ("leader", "13800000002"),
    "student": ("student", "S001"),
}

ANSWER_ALL_NO = {no: "NO" for no in range(1, 101)}


def headers_for(client, role: str) -> dict[str, str]:
    role_code, account = ACCOUNTS[role]
    return auth_headers(client, role_code, account)


def seeded_student(db) -> Student:
    student = db.scalar(select(Student).where(Student.student_no == "S001"))
    assert student is not None
    return student


def counselor_account(db) -> UserAccount:
    user = db.scalar(select(UserAccount).where(UserAccount.account == "13800000001"))
    assert user is not None
    return user


def create_task(client, headers, *, name: str = "任务治理用例") -> int:
    """建一场校内任务。它发放目标行是按创建者的数据范围算的，种子里只有 `S001` 一人。"""
    response = client.post(
        "/api/v1/assessment-tasks",
        headers=headers,
        json={"name": name, "start_at": "2026-09-01", "end_at": "2026-12-31"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def a_sitting_with_a_result(db, task_id: int) -> AssessmentSession:
    """让 `S001` 在这场任务里交一份卷子并把结果算出来。

    走生产那条路（`score_session`）而不是手插一行 `AssessmentResult`：手插的行缺八维度
    结果与答卷哈希，而「这场任务有正式事实」这个判据在两种造法下应当一致——用假的造法
    去证明它，证明的就不是生产的那件事。
    """
    session = make_sitting(db, seeded_student(db), task_id=task_id)
    assert score_session(db, session, ANSWER_ALL_NO) is True, "这一场没算出来，夹具本身坏了"
    return session


def delete_task(client, headers, task_id: int, *, reason: str | None = None):
    payload = {} if reason is None else {"reason": reason}
    return client.request(
        "DELETE", f"/api/v1/assessment-tasks/{task_id}", headers=headers, json=payload
    )


# ---------------------------------------------------------------------------
# 第一组：没有正式事实 → 物理删除
# ---------------------------------------------------------------------------


def test_delete_unused_in_system_task(client, db_session):
    """刚建好、一个人都没答过的任务：`delete-check` 说能物理删，删完那一行不在了。"""
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)

    check = client.get(f"/api/v1/assessment-tasks/{task_id}/delete-check", headers=headers)
    assert check.status_code == 200, check.text
    data = check.json()["data"]
    assert data["canHardDelete"] is True
    assert data["deleteMode"] == "HARD_DELETE"
    # 判据是「一件正式事实都没有」，所以这几个数必须真的是 0——而不是「没算」。
    assert data["sessionCount"] == 0
    assert data["resultCount"] == 0
    assert data["committedImportBatchCount"] == 0
    assert data["riskEventCount"] == 0

    response = delete_task(client, headers, task_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "taskId": task_id,
        "mode": "HARD_DELETE",
        "status": "DELETED",
    }
    assert db_session.get(AssessmentTask, task_id) is None


def test_delete_unused_task_removes_targets_and_scope(client, db_session):
    """物理删除要把它的目标行与发放范围行一起带走。

    这两张表都是**子表**（全库没有 `ondelete=`，父行先删在 MySQL 上是必然的 1451），
    所以它们必须在 `db.delete(task)` **之前**显式删掉。只删任务行的话，这一条会在
    真库上以 `IntegrityError` 收场——而它在「接口回了 200」那一层看不出来。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)

    assert db_session.scalar(
        select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
    ) > 0, "这场任务一条目标行都没有，那这条用例证明不了「目标行被删掉了」"
    assert db_session.scalar(
        select(func.count(AssessmentTaskScope.id)).where(AssessmentTaskScope.task_id == task_id)
    ) > 0, "这场任务没有范围行，那这条用例证明不了「范围行被删掉了」"

    assert delete_task(client, headers, task_id).status_code == 200

    assert db_session.scalar(
        select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
    ) == 0
    assert db_session.scalar(
        select(func.count(AssessmentTaskScope.id)).where(AssessmentTaskScope.task_id == task_id)
    ) == 0


# ---------------------------------------------------------------------------
# 第二组：有正式事实 → 作废
# ---------------------------------------------------------------------------


def test_delete_task_with_session_becomes_voided(client, db_session):
    """有过会话的任务删不掉，走作废：`status='VOIDED'`，三个作废列被写上。"""
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    a_sitting_with_a_result(db_session, task_id)

    check = client.get(f"/api/v1/assessment-tasks/{task_id}/delete-check", headers=headers)
    data = check.json()["data"]
    assert data["canHardDelete"] is False
    assert data["deleteMode"] == "VOID"
    assert data["sessionCount"] == 1

    response = delete_task(client, headers, task_id, reason="建错了，这一场作废")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "VOID"
    assert response.json()["data"]["status"] == "VOIDED"

    db_session.expire_all()
    task = db_session.get(AssessmentTask, task_id)
    assert task is not None, "作废不是删除——那一行必须还在"
    assert task.status == "VOIDED"
    assert task.voided_at is not None
    assert task.voided_by == counselor_account(db_session).id
    assert task.void_reason == "建错了，这一场作废"


def test_void_task_marks_sessions_ineffective(client, db_session):
    """作废之后这一场里的有效会话全部退位，而**别的任务里的不受影响**。

    第二半句不能省：一个「把全库会话都置为无效」的实现也能让第一句成立。
    """
    headers = headers_for(client, "counselor")
    doomed = create_task(client, headers, name="要被作废的")
    kept = create_task(client, headers, name="不该受影响的")
    doomed_sitting = a_sitting_with_a_result(db_session, doomed)
    kept_sitting = a_sitting_with_a_result(db_session, kept)

    assert doomed_sitting.is_effective is True
    assert delete_task(client, headers, doomed, reason="重复发放").status_code == 200

    db_session.expire_all()
    assert db_session.get(AssessmentSession, doomed_sitting.id).is_effective is False
    assert db_session.get(AssessmentSession, kept_sitting.id).is_effective is True


def test_void_task_excludes_from_latest_result(client, db_session):
    """作废之后，这名学生的「当前状态」不再来自这一场（§4.14）。

    做法是**先证明它原本在**：作废之前他在「全部学生」页签上有一个关注等级，作废之后
    同一格变成 `None`（界面按既有约定显示「未测评」）。少了前半句，这条用例在一个
    从没算出结果的库上也是绿的。

    **它证明的是「降级」那一步，不是查询里那句 `active_task_predicate()`。**
    `delete_or_void_task` 会顺手把这一场降级（`is_effective=False`），而子查询里
    本来就有 `effective_session_predicate()`——把 `active_task_predicate()` 那一句
    整个换成恒真，这条用例**照样是绿的**（2026-09-25 实测：变异不红）。真正钉住那句
    防御性约束的是下面那条 `..._even_if_its_session_was_not_downgraded`。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    a_sitting_with_a_result(db_session, task_id)

    def level_of(client, headers) -> str | None:
        response = client.get("/api/v1/students/results", headers=headers)
        assert response.status_code == 200, response.text
        row = next(
            item for item in response.json()["data"]["items"] if item["student_no"] == "S001"
        )
        return row["total_level"]

    assert level_of(client, headers) is not None, "作废之前他就没有等级，那这条用例证明不了任何事"

    assert delete_task(client, headers, task_id, reason="这一场判错了").status_code == 200

    assert level_of(client, headers) is None


def test_a_voided_task_is_not_read_even_if_its_session_was_not_downgraded(client, db_session):
    """§4.14 里**「防御性」**三个字的可执行形式。

    正常路径上作废会顺手把会话降级，所以上面那条用例证明不了 `analytics_service`
    的「最近一场」子查询里那句 `active_task_predicate()` 还有用——上面把整个
    `or_(...)` 换成恒真，它是绿的（2026-09-25 实测）。

    这一条构造的正是那句约束存在的理由：库里出现「任务已作废、而会话仍然是有效场」
    这个组合。它今天只能由人工改库造出来，而这恰恰是防御性约束要挡的形状——
    少那一句，一个已作废任务的分数会被当成「他现在怎么样」的依据，而屏幕上没有
    任何东西看得出来（一个看起来完全正常的关注等级）。

    **刻意绕过 `delete_or_void_task`**：走它就会把会话一起降级，于是又变成上面那一条。
    这里只改任务状态一列，正是要造出「降级那一步没有发生」的库。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers, name="防御性约束用例")
    a_sitting_with_a_result(db_session, task_id)

    def level_of() -> str | None:
        response = client.get("/api/v1/students/results", headers=headers)
        assert response.status_code == 200, response.text
        row = next(
            item for item in response.json()["data"]["items"] if item["student_no"] == "S001"
        )
        return row["total_level"]

    assert level_of() is not None, "改之前他就没有等级，那这条用例证明不了任何事"

    task = db_session.get(AssessmentTask, task_id)
    task.status = "VOIDED"
    db_session.flush()

    # 前置条件：这一场**仍然是有效场**。不成立的话这条用例退化成了上面那一条
    # （降级本身就把结果挡住了），而它看起来还是绿的。
    session = db_session.scalar(
        select(AssessmentSession).where(AssessmentSession.task_id == task_id)
    )
    assert session is not None and session.is_effective is True, (
        "这一场已经被降级了，那这条用例与上面那条重了，证明不了防御性约束"
    )

    assert level_of() is None


def test_void_task_excludes_from_task_list_by_default(client, db_session):
    """默认列表（含 `status=ALL`）都不含已作废的任务。

    两个方向都要断：只断「默认不含它」的话，一个把 `VOIDED` 从所有筛选里都删掉的实现
    照样绿——而那时它从任何入口都找不回来了（任务列表是它唯一的入口）。所以这一条与
    下面那条（`?status=VOIDED` 列得出它）是一对。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers, name="作废任务的可见性")
    a_sitting_with_a_result(db_session, task_id)

    def listed(status: str | None) -> list[int]:
        params = {} if status is None else {"status": status}
        response = client.get("/api/v1/assessment-tasks", headers=headers, params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["data"]["items"]]

    assert task_id in listed(None), "作废之前它就该在列表上——否则下面那句断的是空气"

    assert delete_task(client, headers, task_id, reason="发错了年级").status_code == 200

    assert task_id not in listed(None)
    assert task_id not in listed("ALL")
    assert task_id not in listed("ACTIVE")
    assert task_id not in listed("CLOSED")


def test_void_task_can_be_listed_with_voided_filter(client, db_session):
    """同上的后半：`?status=VOIDED` 列得出它，而且带着「已作废」这个状态。"""
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers, name="作废任务的可见性")
    a_sitting_with_a_result(db_session, task_id)

    def listed(status: str | None) -> list[dict]:
        params = {} if status is None else {"status": status}
        response = client.get("/api/v1/assessment-tasks", headers=headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()["data"]["items"]

    assert task_id in [item["id"] for item in listed(None)], "作废之前它就该在列表上"
    assert delete_task(client, headers, task_id, reason="发错了年级").status_code == 200

    assert task_id not in [item["id"] for item in listed(None)]
    assert task_id not in [item["id"] for item in listed("ALL")]
    voided = next(item for item in listed("VOIDED") if item["id"] == task_id)
    assert voided["status"] == "VOIDED"


# ---------------------------------------------------------------------------
# 第二组之二：学生这一侧的可见性（§4.15）
# ---------------------------------------------------------------------------


def student_task_nos(client, headers) -> list[str]:
    """学生「测评任务」列表里的任务编号（`/student/tasks` 的载荷里 `task_no` 与 `id` 都有）。"""
    response = client.get("/api/v1/student/tasks", headers=headers)
    assert response.status_code == 200, response.text
    return [item["task_no"] for item in response.json()["data"]["items"]]


def student_history_nos(client, headers) -> list[str]:
    """学生「我的记录」列表里的任务编号。

    这一份载荷**没有任务 id**（只有 `task_no` / `task_name` / 自己的完成状态），所以
    两个助手都按 `task_no` 比——那也正好是学生在屏幕上认得出的那个东西。
    """
    response = client.get("/api/v1/student/assessment-history", headers=headers)
    assert response.status_code == 200, response.text
    return [item["task_no"] for item in response.json()["data"]["items"]]


def open_sheet(client, headers, task_id: int):
    return client.post("/api/v1/assessment-sessions", headers=headers, json={"task_id": task_id})


def test_a_voided_task_disappears_from_the_students_own_pages(client, db_session):
    """§4.15：作废的任务**不给学生看**，而心理老师那一侧照常（同一件事两种读者）。

    学生的两个入口各断一次——`/student/tasks`（待办那张卡）与 `/student/assessment-history`
    （「我的记录」）。它们此前都在服务层里带着 `active_task_predicate()`，而**没有任何
    用例看得见这一句**：`grep VOIDED app/tests` 里除了这个文件一行都没有，而这个文件
    各条断的都是心理老师那一侧（`/assessment-tasks`）。也就是说把那两个 `.where` 里的
    谓词整个摘掉，全量套件照样全绿，而学生会在「我的记录」里看到一场学校已经作废的
    测评——还带着他自己的分数与用时（§4.15 禁的正是这一条）。

    **先证明他原本看得见**：作废之前两个列表里都有它。少了这半句，一个「学生永远看不到
    任何任务」的实现（比如谓词写反了）同样能让后面三句成立——而它把整条学生端废掉了。
    最后一句是**反方向**的：心理老师那一侧必须照常看得到并带着「已作废」的状态
    （规范 §4.15 与 CLAUDE.md §1 同一条：心理老师的专业档案保留它，学生那边不显示）。
    少了它，一个「把 VOIDED 从全站所有查询里抹掉」的实现也能让上面三句全绿，而那时
    这场任务从任何入口都找不回来了（连它作废过这件事都查不到）。
    """
    counselor = headers_for(client, "counselor")
    student = headers_for(client, "student")
    task_id = create_task(client, counselor, name="学生可见性用例")
    a_sitting_with_a_result(db_session, task_id)
    task_no = db_session.get(AssessmentTask, task_id).task_no

    assert task_no in student_task_nos(client, student), "作废之前他就看不到，那下面两句断的是空气"
    assert task_no in student_history_nos(client, student)

    assert delete_task(client, counselor, task_id, reason="这一场发错了").status_code == 200

    assert task_no not in student_task_nos(client, student)
    assert task_no not in student_history_nos(client, student)

    listed = client.get(
        "/api/v1/assessment-tasks", headers=counselor, params={"status": "VOIDED"}
    )
    assert listed.status_code == 200, listed.text
    assert task_id in [item["id"] for item in listed.json()["data"]["items"]], (
        "作废不是「从库里消失」：心理老师这一侧照常看得到它，只是带着「已作废」的标签"
    )


def test_a_student_cannot_start_a_new_sheet_on_a_voided_task(client, db_session):
    """列表藏了、`POST /assessment-sessions` 也开不了——两个入口是同一件事的两面。

    只藏列表是不够的：学生手敲 `/student/assessment/<taskId>` 走的就是这一个端点，
    而它此前**没有任何用例**钉过（`effective_task_status` 那道门是给「过了截止日期」
    写的，作废只是顺带被它挡住的）。

    这里刻意造的是「这场任务有事实、而这名学生**一场都没开过**」的库：走
    `start_assessment_import` 把一批文件绑在一场任务上（`:856` 的 `task_id=task.id`）
    就是这个形状，而它让任务有资格走作废（`import_batch_count > 0`）。若改用
    「先让这名学生开一张卷子再作废」，那一条路的答案是**另一个**：他手里那份卷子
    不被收回（§12「已结束不等于把人踢出卷子」，`POST` 会把既有会话原样返回给他）——
    那是刻意的，别把两件事混成一条断言。

    **对照那一句不能省**：同一名学生、同一时刻对着**没作废**的那一场能开卷，
    所以下面那个 404 来自任务的状态，而不是「没有目标行」或端点本身坏了。
    """
    counselor = headers_for(client, "counselor")
    student = headers_for(client, "student")
    doomed = create_task(client, counselor, name="作废后不该还能开卷")
    control = create_task(client, counselor, name="同一名学生的对照组")

    assert open_sheet(client, student, control).status_code == 200, (
        "对照组都开不了卷，那下面那个 404 证明不了任何事"
    )

    stu = seeded_student(db_session)
    db_session.add(
        AssessmentImportBatch(
            batch_no="BATCH-VOID-2",
            school_id=stu.school_id,
            file_name="结果(4).csv",
            batch_name="绑了任务但还没提交的批次",
            file_sha256="1" * 64,
            status="PREVIEW",
            imported_by=counselor_account(db_session).id,
            task_id=doomed,
            total_rows=1,
        )
    )
    db_session.flush()

    response = delete_task(client, counselor, doomed, reason="这一场发错了")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "VOID", "没走作废这条路，那这条用例问的不是它"

    assert open_sheet(client, student, doomed).status_code == 404


def test_void_imported_task_keeps_import_batches(client, db_session):
    """外部导入的批次任务作废时，**批次行一行都不删**（§4.16）。

    批次是「这份数据从哪来」的出处：删了它，库里那些会话就再也说不清是照哪一份文件
    建出来的（而会话本身是保留的）。所以作废只改任务的状态，导入链路一条不碰。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers, name="导入批次任务")
    a_sitting_with_a_result(db_session, task_id)
    student = seeded_student(db_session)
    batch = AssessmentImportBatch(
        batch_no="BATCH-VOID-1",
        school_id=student.school_id,
        file_name="结果(3).csv",
        batch_name="九月校外普查",
        file_sha256="0" * 64,
        status="COMMITTED",
        imported_by=counselor_account(db_session).id,
        task_id=task_id,
        total_rows=1,
        created_rows=1,
    )
    db_session.add(batch)
    db_session.flush()
    batch_id = batch.id

    assert delete_task(client, headers, task_id, reason="这批数据导重了").status_code == 200

    db_session.expire_all()
    kept = db_session.get(AssessmentImportBatch, batch_id)
    assert kept is not None, "作废不该物理删除导入批次"
    assert kept.status == "COMMITTED"
    assert kept.task_id == task_id


def test_void_task_keeps_results_for_history(client, db_session):
    """原始答卷、计算结果与八维度结果一条都不删（§1 的四层事实模型）。

    这是整节最重要的一条：作废说的是「这一场不再算作当前状态」，不是「这一场没发生过」。
    删掉结果会让一份真实的普查从历史上消失——而它在界面上表现为「查不到」，没有别的痕迹。

    这一条**刻意不复用 `a_sitting_with_a_result`**：那个夹具只把答案放在内存里交给
    `score_session`，库里一行 `assessment_answer` 都没有。而「原始答卷一条不删」正是
    这一条要证明的那件事，所以答案要真的经由 `save_answer` 落库——否则这条用例断的是
    一个恒真的东西（0 行 == 0 行）。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    student = seeded_student(db_session)
    student_user = db_session.scalar(select(UserAccount).where(UserAccount.account == "S001"))
    assert student_user is not None

    session = make_sitting(db_session, student, task_id=task_id, status="IN_PROGRESS")
    session_id = session.id
    for question_no in range(1, 101):
        save_answer(db_session, student_user, session_id, question_no, "NO")
    session.status = "SUBMITTED"
    assert score_session(db_session, session, ANSWER_ALL_NO) is True

    answer_count = db_session.scalar(
        select(func.count(AssessmentAnswer.id)).where(AssessmentAnswer.session_id == session_id)
    )
    assert answer_count == 100, "答卷行没落够，那这条用例证明不了「答卷被留着」"
    assert db_session.scalar(
        select(func.count(DimensionResult.id)).where(DimensionResult.session_id == session_id)
    ) == 8

    assert delete_task(client, headers, task_id, reason="口径调整").status_code == 200

    db_session.expire_all()
    assert db_session.get(AssessmentSession, session_id) is not None
    assert db_session.scalar(
        select(func.count(AssessmentAnswer.id)).where(AssessmentAnswer.session_id == session_id)
    ) == 100
    result = db_session.scalar(
        select(AssessmentResult).where(AssessmentResult.session_id == session_id)
    )
    assert result is not None, "作废不该删掉计算结果"
    assert result.total_level is not None
    assert db_session.scalar(
        select(func.count(DimensionResult.id)).where(DimensionResult.session_id == session_id)
    ) == 8


def test_void_task_pending_risk_events_are_voided(client, db_session):
    """还没人处理的筛查信号一并作废；已经有人处理过的不动。

    第二半句是这条用例真正的判据：一个「把这一场所有信号都置为 VOIDED」的实现能让
    第一句成立，而它会把心理老师写过的复核记录挂在一条已作废的信号上——待办队列里
    看不见，历史里也读不出来。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    session = a_sitting_with_a_result(db_session, task_id)
    # 两条的 `trigger_rule` 必须不同：`uq_risk_event_session_trigger_rule` 是
    # `(session_id, trigger_rule, rule_version)`，同一场上同一条规则只许有一条事件。
    pending = make_risk_event(db_session, session, status="PENDING", trigger_rule="KEY_QUESTION_85_YES")
    reviewed = make_risk_event(db_session, session, status="REVIEWED", trigger_rule="KEY_QUESTION_97_YES")
    pending_id, reviewed_id = pending.id, reviewed.id

    assert delete_task(client, headers, task_id, reason="这一场不看了").status_code == 200

    db_session.expire_all()
    voided = db_session.get(RiskEvent, pending_id)
    assert voided.status == "VOIDED"
    assert voided.voided_at is not None
    assert voided.void_reason == "这一场不看了"
    assert db_session.get(RiskEvent, reviewed_id).status == "REVIEWED"


def test_void_task_keeps_manual_review(client, db_session):
    """人工复核记录不随任务作废而消失（§4.16 / 四层事实模型）。"""
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    session = a_sitting_with_a_result(db_session, task_id)
    event = make_risk_event(db_session, session, status="REVIEWED")
    review = ManualReview(
        risk_event_id=event.id,
        reviewer_id=counselor_account(db_session).id,
        review_result="确认属实，已安排谈话",
        confirmed_facts="班主任反映近两周情绪低落",
    )
    db_session.add(review)
    db_session.flush()
    review_id = review.id

    assert delete_task(client, headers, task_id, reason="重复的普查").status_code == 200

    db_session.expire_all()
    assert db_session.get(ManualReview, review_id) is not None
    assert db_session.get(RiskEvent, event.id).status == "REVIEWED"


def test_void_task_keeps_care_case(client, db_session):
    """关怀档案一条都不删——作废的是这场测评，不是学校做过的工作。"""
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    session = a_sitting_with_a_result(db_session, task_id)
    case = StudentCareCase(student_id=session.student_id, status="FOLLOWING")
    db_session.add(case)
    db_session.flush()
    case_id = case.id

    assert delete_task(client, headers, task_id, reason="这一场作废").status_code == 200

    db_session.expire_all()
    kept = db_session.get(StudentCareCase, case_id)
    assert kept is not None
    assert kept.status == "FOLLOWING", "作废任务不该顺带把在办档案关掉或改状态"


# ---------------------------------------------------------------------------
# 第三组：谁能删、什么时候必须给原因
# ---------------------------------------------------------------------------


def test_leader_cannot_delete_task(client, db_session):
    """德育领导读得到任务，但删不了（§4.9 那一格写的是「否」）。

    前后都要看：他不是「够不到这一页」而被拒——`GET /assessment-tasks` 对他是 200，
    挡下他的是删除那一条依赖。
    """
    counselor = headers_for(client, "counselor")
    leader = headers_for(client, "leader")
    task_id = create_task(client, counselor)

    assert client.get("/api/v1/assessment-tasks", headers=leader).status_code == 200

    response = delete_task(client, leader, task_id)
    assert response.status_code == 403, response.text
    assert db_session.get(AssessmentTask, task_id) is not None


def test_student_cannot_delete_task(client, db_session):
    """学生连任务列表都读不到，删除更不必说。"""
    counselor = headers_for(client, "counselor")
    student = headers_for(client, "student")
    task_id = create_task(client, counselor)

    assert client.get("/api/v1/assessment-tasks", headers=student).status_code == 403

    response = delete_task(client, student, task_id)
    assert response.status_code == 403, response.text
    assert db_session.get(AssessmentTask, task_id) is not None


def test_delete_reason_required_for_void(client, db_session):
    """有正式事实的任务作废时**必须**填原因（§4.10）。

    作废会静默地改变全校的关注率口径，而「为什么作废」是事后唯一读得到的那句话。
    所以这一条断的不只是 422，还有**它什么都没改**——一句 422 之后任务已经作废，
    是最难解释的那种半成品。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    session = a_sitting_with_a_result(db_session, task_id)

    for payload in ({}, {"reason": ""}, {"reason": "   "}):
        response = client.request(
            "DELETE", f"/api/v1/assessment-tasks/{task_id}", headers=headers, json=payload
        )
        assert response.status_code == 422, f"{payload} 应当被拒"

    db_session.expire_all()
    task = db_session.get(AssessmentTask, task_id)
    assert task.status != "VOIDED"
    assert db_session.get(AssessmentSession, session.id).is_effective is True


def test_voiding_twice_is_refused_so_the_record_is_not_overwritten(client, db_session):
    """重复作废回 409——第二次会覆盖「谁在什么时候、因为什么作废的」那条记录。

    这一条不在 §12 的点名清单里，加它是因为**界面上有一道遮挡而它不是判据**：
    `TasksPage.vue` 的删除按钮 `v-if="row.status !== 'VOIDED'"` 挡的是鼠标，
    挡住双击、两个标签页、以及直接调接口的都不是它（§4 那条「前端隐藏不是安全措施」）。
    """
    headers = headers_for(client, "counselor")
    task_id = create_task(client, headers)
    a_sitting_with_a_result(db_session, task_id)

    assert delete_task(client, headers, task_id, reason="第一次").status_code == 200
    db_session.expire_all()
    assert db_session.get(AssessmentTask, task_id).void_reason == "第一次"

    second = delete_task(client, headers, task_id, reason="第二次")
    assert second.status_code == 409, second.text
    db_session.expire_all()
    assert db_session.get(AssessmentTask, task_id).void_reason == "第一次"


@pytest.mark.parametrize("role", ["admin"])
def test_admin_cannot_delete_task(client, db_session, role):
    """管理员不在任务治理这条线上（§4：测评任务不是能力，是角色）。

    §12 只点名了领导与学生两条，这一条是把同一张表的第三格补上：管理员的
    `ORG_ACCOUNT` 是 `MANAGE`，很容易被顺手写进任务写权里，而那是 2026-09-17
    已经被推翻过一次的裁决。
    """
    counselor = headers_for(client, "counselor")
    admin = headers_for(client, role)
    task_id = create_task(client, counselor)

    assert delete_task(client, admin, task_id).status_code == 403
    assert db_session.get(AssessmentTask, task_id) is not None
