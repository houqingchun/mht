"""效度复测名单的导出（统计分析 → 全校八维度分析，2026-09-24）。

这一份文件补的是用户报的第二件事：「效度建议复测 需要能够导出学生列表信息，以便知道
哪些学生需要重新测试（学生基本信息要完整）」。在它之前，那一格只有一个人数——`3`，
而「这 3 人是谁」在界面上没有任何出路。

三件事值得在这里说清楚，它们也是本文件每一条用例各自要钉的东西：

1. **名单与那个数同源。** 判据（`analytics_service._is_validity_flagged`）、取数
   （`_report_scope` 的 `calculated`）、以及「一人一行」都在服务端一处，所以
   「页面上写着 3 人、文件里 3 行」是**构造上**相等，不靠两处各自记得（§11：指标卡上
   的数必须与它点进去的那个列表同源）。
2. **它是实名文件，而且实名是必须的。** 这份文件是派工单（派人去找这些学生重测），
   遮蔽了就没法派人。所以 `mask_level` 记 `IDENTIFIED`，而遮蔽模式进审计（§8）。
3. **它是受控导出，不是「多一个页面就能看」。** 三道门槛的最后一道是
   `ensure_student_result_reader`（`STUDENT_PSYCH_DETAIL: {SCOPED}` 且
   `ORG_ACCOUNT: {MANAGE, READ_BASIC}`）——**德育领导与系统管理员都被它挡在外面**，
   而这一页的 `meta.role` 同时含 counselor 与 leader（§4：受控导出不能成为绕过心理
   详情的旁路）。

造「效度被标记的学生」用的是 `test_analytics_report.py` 里那套现成配方（七道效度题
答「是」越过阈值 → `validity_status = RETEST_RECOMMENDED`），不在这里另写一份——
两套造法必然漂移，而漂移之后两组用例断的就不再是同一件事了。
"""

import csv
import io
from datetime import datetime

from sqlalchemy import select

from app.models.assessment import AssessmentTask
from app.models.audit import AuditLog
from app.models.exporting import ExportJob
from app.models.organization import Student
from app.services.analytics_service import VALIDITY_RETEST_COLUMNS
from app.services.assessment_service import score_session
from app.services.export_service import (
    EXPORT_TYPE_VALIDITY_RETEST,
    MASK_LEVEL_IDENTIFIED,
)
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting, make_target
from app.tests.test_assessment_api import create_student_session, save_answers

COUNSELOR = ("counselor", "13800000001")
LEADER = ("leader", "13800000002")
ADMIN = ("admin", "admin")

EXPORT_PATH = "/api/v1/analytics/validity-retest/export"

#: 七道效度题全答「是」——`scale_engine.validity_status` 越过阈值即
#: `RETEST_RECOMMENDED`，与 `test_report_analysis_mode_excludes_validity_flagged_results`
#: 用的是同一组题号。
VALIDITY_YES = {82, 84, 86, 88, 90, 92, 94}


def submit_flagged(client) -> None:
    """让种子那一名学生交出一份**效度被标记**的答卷。"""
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers=VALIDITY_YES)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 200, response.text


def counselor_headers(client):
    return auth_headers(client, *COUNSELOR)


def task_id_of(client, headers) -> int:
    """那一场真实任务的 id——**现取，不写死**。

    `TASK-2026-FALL-MHT` 的 id 取决于种子的插入次序，写死会在某一天红在一个与功能
    无关的地方（与 e2e 里「批次号从屏幕上读」是同一条）。
    """
    items = client.get("/api/v1/assessment-tasks", headers=headers).json()["data"]["items"]
    assert items, "基线种子应当有一场测评任务"
    return items[0]["id"]


def export(client, headers, **payload):
    return client.post(EXPORT_PATH, headers=headers, json={"purpose": "交德育处安排复测", **payload})


def csv_rows(response) -> list[list[str]]:
    """下载回来的文件解析成行（含表头那一行）。

    用 `csv.reader` 而不是 `line.split(",")`：这一份的「测评任务」列里是任务名，
    而任务名里有逗号时按逗号切会把一行切成七八列，报出来是一句「找不到第 8 列」。
    """
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def add_unflagged_classmate(db, task_id: int) -> None:
    """给这一场任务再放一名**效度正常**的同班同学，并按真实评分链路把他的结果算出来。

    他存在的理由不是「数据多一点」，而是**证明那份名单是有筛选的**：基线种子的名册上
    只有一名学生（S001），而他正好就是被标记的那一个——此时「导出被标记的人」与
    「导出全部人」给出同一个结果，主用例对「摘掉过滤」那个变异是**恒绿**的。

    这与 CLAUDE.md 测试注意里那条是同一条：「第二所学校的学生必须带上全链路记录……
    否则『列表里没有他』这条断言会因为没有他而通过，测试白写」。断言「这里没有他」，
    得先真的有一个他。

    走 `score_session` 而不是手插一行 `AssessmentResult`：`_calculate_and_hash` 的
    docstring 写着它「一行都不写」，所以只要给一份显式的 `answer_map` 就能算出与真实
    交卷**同形**的结果（含八维度）。手插的行缺维度结果，于是「把一个人插进去」这件事
    本身会变成另一个变量，红的会是别的原因。

    全班答案 `NO`：效度正常（不是 `RETEST_RECOMMENDED`），总分落在最低那一档，
    重点题 85 / 97 也未命中——这条链路上不会多出任何风险事件与关怀档案。
    """
    seed_student = db.scalar(select(Student).where(Student.student_no == "S001"))
    assert seed_student is not None, "基线种子应当有 S001"
    classmate = Student(
        student_no="S002",
        name="安同学",
        masked_name="安**",
        school_id=seed_student.school_id,
        grade_id=seed_student.grade_id,
        class_id=seed_student.class_id,
        gender="MALE",
        age=13,
    )
    db.add(classmate)
    db.flush()

    task = db.get(AssessmentTask, task_id)
    assert task is not None
    make_target(db, classmate, task)
    session = make_sitting(db, classmate, task_id=task.id, submitted_at=datetime(2026, 9, 20, 9, 0, 0))
    assert score_session(db, session, {no: "NO" for no in range(1, 101)}) is True


# --- 名单与那个数同源 ---------------------------------------------------------


def test_the_export_lists_exactly_the_students_the_kpi_counts(client, db_session):
    """**本文件的主旨**：上方那个数与这份文件的行数是同一个东西的两种读法。

    三处一起断，缺一个都留着一段缝：报表里的 `validity_flagged_count`、作业上的
    `row_count`、文件里的数据行数。只断前两个的话，一个「作业记 1 行、文件里 0 行」的
    实现照样是绿的——而拿到文件的人看到的正是文件。

    **那个同学（`add_unflagged_classmate`）是这条用例的判据本身，不是陪衬**：没有他，
    名册上唯一的学生就是被标记的那一个，于是「导出被标记的人」与「导出全部人」给出
    同一个结果——摘掉 `_is_validity_flagged` 那道过滤，下面每一条断言照样是绿的。
    """
    submit_flagged(client)
    headers = counselor_headers(client)
    task_id = task_id_of(client, headers)
    add_unflagged_classmate(db_session, task_id)

    report = client.get("/api/v1/analytics/report", headers=headers, params={"taskIds": task_id})
    assert report.status_code == 200, report.text
    quality = report.json()["data"]["sample_quality"]
    # **先证明有东西可扫**：`completed_count` 就是 `len(calculated)`，而名单正是从这个
    # 集合里筛出来的。没有这一句，上面那位同学即使一个字段都没插对，`kpi == 1` 照样成立
    # ——夹具白写，而用例看起来完全正常（CLAUDE.md 测试注意那条的同一个形状）。
    assert quality["completed_count"] == 2, "那位效度正常的同学必须真的落进 calculated"
    kpi = quality["validity_flagged_count"]
    # 集合里此刻是**两**个人而 KPI 是 1——这一句自己就在说「那个数是有筛选的」。
    assert kpi == 1, "配方应当造出恰好一名效度被标记的学生；为 0 或 2 时下面每一条都会退化"

    created = export(client, headers, task_ids=[task_id])
    assert created.status_code == 200, created.text
    job = created.json()["data"]

    assert job["export_type"] == EXPORT_TYPE_VALIDITY_RETEST
    assert job["row_count"] == kpi

    downloaded = client.get(f"/api/v1/export-jobs/{job['id']}/download", headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    rows = csv_rows(downloaded)
    assert rows[0] == list(VALIDITY_RETEST_COLUMNS)
    assert len(rows) - 1 == kpi
    # 名单里**恰好**是那一个学号，而不是「行数对得上」——行数对得上只说明两边口径一致，
    # 说明不了口径是哪一个（两个人全导出来也是 2 行）。
    assert [row[0] for row in rows[1:]] == ["S001"]
    # 「学生基本信息要完整」是用户的原话，所以逐列断一句：这份文件要能拿去点人。
    student_row = rows[1]
    assert student_row[1] == "林同学"
    assert student_row[2] == "初一"
    assert student_row[4] == "男"
    assert student_row[7], "「测评任务」那一列不该是空的——否则这份名单答不上是哪一场测出来的"


def test_the_kpi_does_not_move_with_the_analysis_mode(client):
    """KPI 不随统计模式变，所以导出请求里**没有**这个字段。

    `DimensionsPage.vue` 的导出只记 `task_ids`、不记效度口径，依据就是这一条：报表的
    两个模式给出同一个 `validity_flagged_count`，而名单走的是同一个不受模式影响的
    集合（`calculated` 而不是 `included`）。带着它只会在请求体里多一个不生效的字段，
    而「传了不生效」与「不支持」在屏幕上是分不开的（`ExportRequest` 那条注释记着
    同一件事）。
    """
    submit_flagged(client)
    headers = counselor_headers(client)
    task_id = task_id_of(client, headers)

    counts = {}
    for mode in ("ALL_CALCULATED", "VALIDITY_UNFLAGGED"):
        data = client.get(
            "/api/v1/analytics/report",
            headers=headers,
            params={"taskIds": task_id, "analysisMode": mode},
        ).json()["data"]
        counts[mode] = data["sample_quality"]["validity_flagged_count"]

    assert counts["ALL_CALCULATED"] == 1
    assert counts["VALIDITY_UNFLAGGED"] == counts["ALL_CALCULATED"]
    # 模式确实生效了（它把那条结果挡在聚合之外），只是挡不到这一个数——两个方向都断，
    # 否则「模式根本没传出去」也会让上面那一条通过。
    unflagged = client.get(
        "/api/v1/analytics/report",
        headers=headers,
        params={"taskIds": task_id, "analysisMode": "VALIDITY_UNFLAGGED"},
    ).json()["data"]
    assert unflagged["sample_quality"]["n_evaluable"] == 0


def test_an_unknown_task_is_a_404_and_writes_no_job(client, db_session):
    """`task_ids` 是真的被用上了，不是被忽略掉的。

    判据取的是「一个不存在的 id」而不是「另一个任务」：基线种子里只有一场任务，
    造第二场要连量表一起复制，而那条路能证明的与这一条一样——**筛选被读进去了**。
    顺带断一句「没有作业被登记」：`validity_retest_csv` 排在 `create_export_job` 前面，
    所以这条路上不该留下任何一行（一次失败的导出不该在台账上留一份取不到的文件）。
    """
    headers = counselor_headers(client)
    before = len(db_session.scalars(select(ExportJob)).all())

    response = export(client, headers, task_ids=[999999])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert len(db_session.scalars(select(ExportJob)).all()) == before


# --- 三道门槛 -----------------------------------------------------------------


def test_the_leader_is_refused_by_the_service_level_reader_gate(client):
    """德育领导过得了前两道，过不了第三道——**这正是这一条存在的理由**。

    他的 `AGGREGATE_STATS` 是 `SCHOOL`（这一页本来就给他看）、`CONTROLLED_EXPORT` 是
    `PROGRESS_SUMMARY`（受控导出也认），而这份名单逐行印着学号与姓名，所以真正拦住
    他的是 `ensure_student_result_reader`。少了它，「受控导出不能成为绕过心理详情的
    旁路」这条在**这个**端点上就是一句空话（§4）。
    """
    submit_flagged(client)
    leader = auth_headers(client, *LEADER)
    # 用 counselor 取 id：这一条断的是领导被拒，不该顺带依赖「领导看不看得到任务」。
    task_id = task_id_of(client, counselor_headers(client))

    # 先证明他够得着这一页的报表——否则下面的 403 可能来自别处（比如任务列不出来）。
    assert client.get(
        "/api/v1/analytics/report", headers=leader, params={"taskIds": task_id}
    ).status_code == 200

    refused = export(client, leader, task_ids=[task_id])
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "ROLE_FORBIDDEN"


def test_the_admin_is_refused_too(client):
    """系统管理员也拿不到——与 `GET /students/results` 同一条：他没有心理详情。

    他挡在**更早**的一层（`AGGREGATE_STATS` 是 `NONE`），所以这里只断 403 不断措辞：
    这条用例要说的是「他导不出来」，不是「他死在哪一道门上」。
    """
    submit_flagged(client)
    admin = auth_headers(client, *ADMIN)
    task_id = task_id_of(client, counselor_headers(client))

    assert export(client, admin, task_ids=[task_id]).status_code == 403


# --- 请求形状 -----------------------------------------------------------------


def test_a_purpose_is_required(client):
    """没有用途就没有这份文件——`purpose` 是受控导出唯一的说明字段（§8）。"""
    headers = counselor_headers(client)
    task_id = task_id_of(client, headers)

    response = client.post(EXPORT_PATH, headers=headers, json={"purpose": "   ", "task_ids": [task_id]})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PURPOSE_REQUIRED"


def test_task_ids_is_required_and_capped_at_twenty(client):
    """必填，且上限与 `GET /analytics/report` 逐字相同。

    上限那一条的理由是「界面上能查、却导不出来」——两处的判据漂了之后，20 个任务以内的
    查询照常出报表，一点导出就 422，而屏幕上没有任何东西提前说过这件事。
    """
    headers = counselor_headers(client)
    task_id = task_id_of(client, headers)

    missing = client.post(EXPORT_PATH, headers=headers, json={"purpose": "交德育处安排复测"})
    assert missing.status_code == 422

    too_many = export(client, headers, task_ids=list(range(1, 22)))
    assert too_many.status_code == 422

    # 20 个以内是**形状上**合法的——上面那条红的不是「`task_ids` 这个字段本身不被接受」。
    # 用 20 个**互不相同**的 id：`_report_scope` 里是 `dict.fromkeys` 去重的，所以
    # `[task_id] * 20` 落到一个任务上、回的是 200（那是好事，不是这条要钉的东西）。
    at_limit = export(client, headers, task_ids=list(range(900_000, 900_020)))
    assert at_limit.status_code == 404
    assert at_limit.json()["error"]["code"] == "NOT_FOUND"


# --- 审计与作业行 --------------------------------------------------------------


def test_the_audit_and_the_job_row_record_the_mask_level(client, db_session):
    """事后要答得上「这份文件是不是实名的」——而 `purpose` 担不起这个问题。

    所以遮蔽模式住在 `export_job.mask_level` 这一列上，审计里也照写一份（§8：导出审计
    必须记录遮蔽模式）。两条一起断：作业列是机器判据，审计是能搜到的那一份轨迹。
    """
    submit_flagged(client)
    headers = counselor_headers(client)
    task_id = task_id_of(client, headers)

    job = export(client, headers, task_ids=[task_id]).json()["data"]

    row = db_session.get(ExportJob, job["id"])
    db_session.refresh(row)
    assert row.mask_level == MASK_LEVEL_IDENTIFIED
    # 字段白名单从**产物**倒着写（§16.3：导出接口不得接受任意字段名）——所以它必须
    # 就是这份文件自己的表头。
    assert row.field_policy["columns"] == list(VALIDITY_RETEST_COLUMNS)

    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.resource_id == job["job_no"])
    ).all()
    assert len(audits) == 1, "一次导出写一条审计，不多不少"
    audit = audits[0]
    assert audit.action == "导出效度复测名单"
    assert audit.resource_type == "EXPORT"
    assert audit.purpose == "交德育处安排复测"
    assert audit.actor_user_id == row.requested_by
    assert "实名" in audit.detail
    assert job["job_no"] in audit.detail
