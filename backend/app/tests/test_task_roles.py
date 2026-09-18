"""测评任务归谁：2026-09-17 的裁决，以及它的回归网。

用户在这一天把「测评任务」整块从系统管理员身上摘掉——**读与写都不再包含 ADMIN**——
理由是它属于学校业务，而系统管理员只管系统级配置。写权落到心理老师（业务闭环的
负责人），德育领导保持只读（它的能力集是学校级聚合与摘要，是监督口径）。

为什么单开一个文件：在那之前**没有任何测试钉住过这张矩阵**。`POST /assessment-tasks`
是 ADMIN-only 的，而唯一一条建任务的用例藏在 `test_analytics_dimensions.py` 里——
它只是顺手用管理员开了第二轮战役，所以「谁能建任务」这个事实被埋在一个讲维度去重的
测试里，改成任何一个角色都不会有人发现。这正是 CLAUDE.md §3 那条教训的又一处：
断言拿不到的东西，等于没断言。

本文件只断言角色矩阵，不碰业务逻辑，所以任何一次把它改回去的动作都会立刻变红。
"""

import pytest
from sqlalchemy import select

from app.models.assessment import AssessmentTarget, AssessmentTask
from app.tests.conftest import auth_headers

# 账号与 `db/seed.py` 一致；学生的密码同样是 123456。
ACCOUNTS = {
    "admin": ("admin", "admin"),
    "counselor": ("counselor", "13800000001"),
    "leader": ("leader", "13800000002"),
    "student": ("student", "S001"),
}

# 除心理老师外，谁能**写**：没有人。除心理老师与德育领导外，谁能**读**：没有人。
NON_WRITERS = ["admin", "leader", "student"]
NON_READERS = ["admin", "student"]

TASK_BODY = {"name": "越权测评任务", "start_at": "2026-09-01", "end_at": "2026-12-31"}


def headers_for(client, role: str) -> dict[str, str]:
    role_code, account = ACCOUNTS[role]
    return auth_headers(client, role_code, account)


@pytest.fixture()
def seeded_task(db_session) -> AssessmentTask:
    task = db_session.scalar(select(AssessmentTask))
    assert task is not None, "基线种子应当已经建好一个任务"
    return task


def test_the_counselor_owns_the_whole_task_cycle(client, seeded_task):
    """写与读都在同一个角色身上，这是「业务归心理老师」的完整形状。

    只断言写、不断言读是不够的：把读也留给管理员，心理老师就会建出一份自己
    看不到完成率的任务。
    """
    counselor = headers_for(client, "counselor")

    created = client.post("/api/v1/assessment-tasks", headers=counselor, json=TASK_BODY)
    assert created.status_code == 200
    task_id = created.json()["data"]["id"]

    edited = client.patch(
        f"/api/v1/assessment-tasks/{task_id}", headers=counselor, json={"name": "秋季普查（延期）"}
    )
    assert edited.status_code == 200
    assert edited.json()["data"]["name"] == "秋季普查（延期）"

    assert client.get("/api/v1/assessment-tasks", headers=counselor).status_code == 200
    assert (
        client.get(f"/api/v1/assessment-tasks/{task_id}/completion", headers=counselor).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/assessment-tasks/{task_id}/completion/export", headers=counselor
        ).status_code
        == 200
    )


@pytest.mark.parametrize("role", NON_WRITERS)
def test_nobody_else_can_create_or_edit_a_task(client, db_session, seeded_task, role):
    headers = headers_for(client, role)

    created = client.post("/api/v1/assessment-tasks", headers=headers, json=TASK_BODY)
    assert created.status_code == 403, role

    edited = client.patch(
        f"/api/v1/assessment-tasks/{seeded_task.id}", headers=headers, json={"name": "越权改名"}
    )
    assert edited.status_code == 403, role

    # 403 之外还要断言**没有写进去**：只挡响应不挡写入的守卫在这个测试里长得一模一样。
    db_session.refresh(seeded_task)
    assert seeded_task.name != "越权改名"
    assert db_session.scalar(
        select(AssessmentTask).where(AssessmentTask.name == TASK_BODY["name"])
    ) is None


@pytest.mark.parametrize("role", NON_READERS)
def test_nobody_else_can_read_tasks_or_their_completion(client, seeded_task, role):
    """管理员这一行就是本次裁决本身：任务列表、完成明细、完成统计导出三个口子
    一并关掉。完成明细是逐人的行为数据（谁没答、用时多久），不是系统级配置。"""
    headers = headers_for(client, role)

    assert client.get("/api/v1/assessment-tasks", headers=headers).status_code == 403
    assert (
        client.get(
            f"/api/v1/assessment-tasks/{seeded_task.id}/completion", headers=headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/assessment-tasks/{seeded_task.id}/completion/export", headers=headers
        ).status_code
        == 403
    )


def test_the_leader_reads_completion_but_does_not_write(client, db_session, seeded_task):
    """德育领导的两面：它要按年级看完成率（`/leader/tasks`），但那不是运营权限。"""
    leader = headers_for(client, "leader")
    name_before = seeded_task.name
    target_count_before = len(
        db_session.scalars(
            select(AssessmentTarget).where(AssessmentTarget.task_id == seeded_task.id)
        ).all()
    )

    assert client.get("/api/v1/assessment-tasks", headers=leader).status_code == 200
    completion = client.get(
        f"/api/v1/assessment-tasks/{seeded_task.id}/completion", headers=leader
    )
    assert completion.status_code == 200

    assert (
        client.post("/api/v1/assessment-tasks", headers=leader, json=TASK_BODY).status_code == 403
    )
    db_session.refresh(seeded_task)
    assert seeded_task.name == name_before
    assert (
        len(
            db_session.scalars(
                select(AssessmentTarget).where(AssessmentTarget.task_id == seeded_task.id)
            ).all()
        )
        == target_count_before
    )
