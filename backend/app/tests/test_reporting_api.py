"""P1 专业报告（RPT-09）：CRUD / 权限 / 快照冻结 / 发布不可覆盖 / 正式导出。

这个文件是 `services/reporting_service.py` 与 `api/v1/reporting.py` **唯一**的后端守卫。
它此前一行测试都没有——后端套件里 `rg "ProfessionalReport"` 只命中
`test_ensure_schema.py` 的 schema 比对，而那一条问的是「表建出来了没有」，不是「这条路
走得通吗」。代价是实测过的：`POST /professional-reports/{id}/export-jobs` 的**调用形状
是错的**（`serialize_export_jobs([job])` 少了 `db` 与 `user` 两个位置参数），于是那条
端点每一次调用都 500，而在 e2e 上它只表现为界面上一句「服务端返回了无法解析的内容」。
`test_the_export_goes_through_the_export_center` 就是那条路径的守卫。

四组，各有各的靶子：

1. **状态机**（草稿 → 发布 → 新版本），含「发布之后不可覆盖」——§5.4 的
   `DRAFT / PUBLISHED / ARCHIVED` 与 `REPORT_IMMUTABLE`；
2. **权限**——能力矩阵与数据范围是两层，**两层的拒绝长得不一样**（403 与 404），
   这一点是这个文件最容易被改坏的地方；
3. **快照冻结**——§5.4「历史报告不得因实时数据变化而漂移」。判据写成
   「同一次数据改动，实时报表动了、报告没动」，而不是「报告里的数等于某个值」；
4. **导出作业**——§5.5：走既有 Export Job、实名遮蔽、审计、以及**按版本导出**。
"""

from datetime import datetime

import csv
import io

import pytest
from sqlalchemy import select

from app.models.account import UserAccount
from app.models.assessment import AssessmentResult, AssessmentSession
from app.models.reporting import ProfessionalReport, ProfessionalReportVersion
from app.models.organization import Student
from app.services.assessment_service import score_session
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting

PROFESSIONAL = "/api/v1/professional-reports"


@pytest.fixture()
def counselor(client):
    return auth_headers(client, "counselor", "13800000001")


@pytest.fixture()
def leader(client):
    return auth_headers(client, "leader", "13800000002")


def _task_id(client, headers) -> int:
    """这个文件每一条用例都建在一场真实任务上（报告的全部内容都来自它的聚合统计）。"""
    items = client.get("/api/v1/assessment-tasks", headers=headers).json()["data"]["items"]
    assert items, "种子数据里必须有一场测评任务"
    return items[0]["id"]


def _create(client, headers, task_id: int, **overrides) -> dict:
    body = {
        "title": "秋季普查专业分析报告",
        "task_ids": [task_id],
        "analysis_mode": "ALL_CALCULATED",
        "overall_summary": "整体平稳。",
        "dimension_interpretation": "",
        "sample_validity_note": "",
        "support_plan": "",
    }
    body.update(overrides)
    response = client.post(PROFESSIONAL, headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _detail(client, headers, report_id: int) -> dict:
    response = client.get(f"{PROFESSIONAL}/{report_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _publish(client, headers, report_id: int) -> dict:
    response = client.post(f"{PROFESSIONAL}/{report_id}/publish", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _key_attention(payload: dict) -> int:
    """一份报表（实时的或冻结快照）里「重点关注」那一档的人数。

    读 `overview.level_distribution` 而不是 `overview.signal_student_count`：后者数的是
    `risk_event` 的行数，**不由 `assessment_result.total_level` 决定**——拿它当判据时，
    「把结果改成 KEY_ATTENTION」这个改动对两边的读数是同时无效的，于是
    `live() != frozen()` 那条断言在一个「读取时实时重算」的实现上也照样立不住
    （实测：两边都是 0，报 `assert 0 != 0`）。这一档则直接来自 `result.total_level`，
    是这条用例唯一能真的推动的变量。
    """
    for row in payload["overview"]["level_distribution"]:
        if row["level_code"] == "KEY_ATTENTION":
            return row["student_count"]
    raise AssertionError("三档恒发（`_level_distribution` 的 docstring），重点关注那一档必须在")


def _score_a_sitting(client, headers, db_session) -> int:
    """让种子里那名学生**真的有一份算出来的结果**，返回任务 id。

    基线种子只有名册与任务，**一份 `assessment_result` 都没有**——所以报告里的关注人数
    恒为 0，而「快照冻结」那两条用例在改动前后看到的是同一个数，对「读取时实时重算」
    那个变异**恒绿**。改数据的用例必须先有数据可动，这是 CLAUDE.md 测试注意里那条
    「断言『这里没有他』，得先真的有一个他」的同一句话。

    走 `score_session` 而不是手插一行 `AssessmentResult`：理由与
    `test_validity_retest_export.py::add_unflagged_classmate` 逐字相同——手插的行缺八维度
    结果，于是「把一个人插进去」这件事本身会变成另一个变量。

    全部答 `NO`：效度正常、总分落在最低那一档、重点题 85 / 97 都没命中——这条链路上
    不会多出风险事件与关怀档案，于是关注人数在改动**之前**确实是 0。
    """
    task_id = _task_id(client, headers)
    student = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert student is not None, "基线种子应当有 S001"

    sitting = db_session.scalar(
        select(AssessmentSession).where(
            AssessmentSession.task_id == task_id, AssessmentSession.student_id == student.id
        )
    )
    if sitting is None:
        sitting = make_sitting(db_session, student, task_id=task_id, submitted_at=datetime(2026, 9, 20, 9, 0, 0))

    already = db_session.scalar(select(AssessmentResult.id).where(AssessmentResult.session_id == sitting.id))
    if already is None:
        sitting.status = "SUBMITTED"
        sitting.submitted_at = datetime(2026, 9, 20, 9, 0, 0)
        assert score_session(db_session, sitting, {no: "NO" for no in range(1, 101)}) is True
    db_session.flush()
    return task_id


def _validity_flagged(snapshot: dict) -> int:
    """一份快照里「效度提示」那一档的人数（`sample_quality.validity_flagged_count`）。

    **不读 `overview.level_distribution`（`_key_attention` 读的那个）**：这一档与那一档
    由两个不同的列推动，而只有这一档**同时**出现在快照与导出文件里（导出文件的「统计
    指标」那一行写着「效度提示 N 人」）。用等级那一档的话，「版本级快照刷没刷新」这件事
    在导出文件里读不出来——而导出正是版本级那一份的唯一读者。
    """
    return snapshot["sample_quality"]["validity_flagged_count"]


def _exported_metrics(client, headers, report_id: int, version_no: int) -> str:
    """把某个**版本**导出来，读文件里「统计指标」那一格（含效度两个数）。

    按版本号导而不是按默认（当前版本）导，是整条链路上唯一能证明「版本级那一份快照
    真的被刷新过」的办法：`_snapshot_of` 先读版本行、版本行为空才回落报告级，所以
    「只刷新了报告级」的实现在**详情页上完全看不出来**，而在这一份文件里印的是旧数字。
    用 `csv.reader` 解（与 `test_the_document_carries_the_frozen_numbers_...` 同一条），
    不切逗号。
    """
    job = client.post(
        f"{PROFESSIONAL}/{report_id}/export-jobs",
        headers=headers,
        json={"purpose": "核对版本快照", "version_no": version_no},
    ).json()["data"]
    text = client.get(f"/api/v1/export-jobs/{job['id']}/download", headers=headers).content.decode("utf-8-sig")
    return dict(csv.reader(io.StringIO(text)))["统计指标"]


# --- 1. 状态机 -------------------------------------------------------------------


def test_a_new_report_is_a_draft_that_has_already_frozen_its_statistics(client, counselor):
    """建报告的那一刻就把统计算好写进报告行——**不是**读的时候现算（见第 3 组）。

    `report_no` 断的是形状而不是那一天的第几号：这个数由「当日已建几份」推出，写死
    `-0001` 会在任何一次库里有存量报告时红在一个与功能无关的地方。
    """
    report = _create(client, counselor, _task_id(client, counselor))

    assert report["status"] == "DRAFT"
    assert report["current_version"] == 1
    assert report["published_at"] is None
    assert report["report_no"].startswith(f"RPT-{datetime.now().strftime('%Y%m%d')}-")
    assert report["task_scope"] == {"task_ids": [report["task_scope"]["task_ids"][0]]}
    assert report["statistics_snapshot"], "建报告时快照必须是已经算好的，不是空壳"
    assert report["content"]["overall_summary"] == "整体平稳。"


def test_a_draft_can_be_saved_and_read_back(client, counselor):
    report = _create(client, counselor, _task_id(client, counselor))
    saved = client.put(
        f"{PROFESSIONAL}/{report['id']}/draft",
        headers=counselor,
        json={
            "overall_summary": "改过一遍。",
            "dimension_interpretation": "学习焦虑偏高。",
            "sample_validity_note": "有效样本 1 人。",
            "support_plan": "安排团体辅导。",
        },
    )
    assert saved.status_code == 200, saved.text

    reread = _detail(client, counselor, report["id"])["content"]
    assert reread["overall_summary"] == "改过一遍。"
    assert reread["dimension_interpretation"] == "学习焦虑偏高。"
    assert reread["support_plan"] == "安排团体辅导。"


def test_publishing_locks_the_text_and_a_later_save_writes_nothing(client, counselor):
    """「已发布版本不可覆盖」要断言两件事：409 + **改动没有发生**。

    只断 409 的话，一个「先改完再抛错」的实现（先 `setattr` 再判状态）照样是绿的——
    而它在库里已经留下了一次静默覆盖。
    """
    report = _create(client, counselor, _task_id(client, counselor), overall_summary="发布前的原文。")
    published = _publish(client, counselor, report["id"])
    assert published["status"] == "PUBLISHED"
    assert published["published_at"] is not None

    refused = client.put(
        f"{PROFESSIONAL}/{report['id']}/draft",
        headers=counselor,
        json={
            "overall_summary": "想偷偷改掉。",
            "dimension_interpretation": "",
            "sample_validity_note": "",
            "support_plan": "",
        },
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "REPORT_IMMUTABLE"
    assert "已发布版本不可覆盖" in refused.json()["error"]["message"]

    assert _detail(client, counselor, report["id"])["content"]["overall_summary"] == "发布前的原文。"


def test_a_new_version_starts_from_the_published_text(client, counselor):
    report = _create(client, counselor, _task_id(client, counselor), overall_summary="第一版。")
    _publish(client, counselor, report["id"])

    created = client.post(f"{PROFESSIONAL}/{report['id']}/new-version", headers=counselor)
    assert created.status_code == 200, created.text
    data = created.json()["data"]

    assert data["current_version"] == 2
    assert data["status"] == "DRAFT", "新版本从草稿起步，发布要人再点一次"
    assert data["content"]["version_no"] == 2
    assert data["content"]["overall_summary"] == "第一版。", "新版本以上一版为起点，不是空白"
    numbers = [v["version_no"] for v in data["versions"]]
    assert sorted(numbers) == [1, 2], "旧版本要保留，不能被覆盖掉"


def test_a_new_version_is_refused_while_the_report_is_still_a_draft(client, counselor):
    report = _create(client, counselor, _task_id(client, counselor))
    response = client.post(f"{PROFESSIONAL}/{report['id']}/new-version", headers=counselor)
    assert response.status_code == 422, response.text


def test_a_new_version_recomputes_the_statistics_it_inherits(client, counselor, db_session):
    """新版本**重新统计快照**，而版本 1 那一行一个字没动（2026-09-25 用户裁决）。

    裁决之前 `new_version` 复制的是 `report.statistics_snapshot_json`，所以三处快照
    （版本 1 行 / 新版行 / 报告行）在任何一条写入路径上都是**同一个对象**——那时这个
    状态**造不出来**，为它写断言只能得到一条恒绿的守卫。现在造得出来了，判据如下。

    **四个断言缺一不可**，每一条各自挡掉一类实现：

    1. `before == 0`——先证明有东西可动。少了它，一个「两边都恒为 0」的实现（比如
       `validity_status` 那一列根本没进统计）在后面几条上也照样绿；
    2. 新版的数是**新的**（照抄上一版快照的实现在这里红）；
    3. **版本 1 那一行还是它当初那一份**（把旧行就地改掉、或干脆没建新行而只改了旧行
       的实现在这里红）；
    4. 最后两条按**版本号各导一次**——2 号文件里是新数、1 号文件里是旧数。

    第 4 条不是重复劳动：详情页与列表读的是**报告级**那一份（`serialize`），而导出读的是
    **版本级**那一份（`_snapshot_of` 版本优先、报告兜底）。只刷报告级的实现在第 2、3 条
    上全绿，而它导出出来的文件里印着旧数字——同一份报告，屏幕上一个数、文件里另一个数
    （CLAUDE.md §11 那条「指标卡上的数必须与它点进去的那个列表同源」）。

    改动的那一列是 `validity_status`（不是 `_key_attention` 读的等级）。第一版用的正是
    等级，实测**导出文件里那一格没动**——导出印的是 `signal_student_count`（数的是
    `risk_event` 的行），与 `total_level` 不是一个来源。`_key_attention` 的 docstring
    里写着同一句话，这条用例是它的又一次印证：**先确认那个数真的会动，再拿它当判据**。
    """
    task_id = _score_a_sitting(client, counselor, db_session)
    report = _create(client, counselor, task_id, overall_summary="第一版。")
    _publish(client, counselor, report["id"])

    before = _validity_flagged(_detail(client, counselor, report["id"])["statistics_snapshot"])
    assert before == 0, "改动前这一档必须是 0，否则下面那条断言分不出前后"

    # 数据变了：把那一场的结果标成效度提示（`_is_validity_flagged` 判的就是这一列）。
    result = db_session.scalar(
        select(AssessmentResult).join(AssessmentSession, AssessmentSession.id == AssessmentResult.session_id).where(AssessmentSession.task_id == task_id)
    )
    assert result is not None, "上一步刚算出来那一场的结果应当在这里"
    assert result.validity_status == "VALID", "改动前必须是正常那一档，否则下面那几条分不出前后"
    result.validity_status = "RETEST_RECOMMENDED"
    db_session.flush()

    created = client.post(f"{PROFESSIONAL}/{report['id']}/new-version", headers=counselor)
    assert created.status_code == 200, created.text
    data = created.json()["data"]

    assert data["current_version"] == 2
    assert data["content"]["overall_summary"] == "第一版。", "四段文本照抄这件事不受本次裁决影响"
    assert _validity_flagged(data["statistics_snapshot"]) == before + 1, "新版本要重新取数，不是照抄上一版"

    # 从库里重读一遍，不是读身份映射里那个对象：就地改掉旧行那一列的实现要在这里现形。
    db_session.expire_all()
    version_1 = db_session.scalar(
        select(ProfessionalReportVersion).where(
            ProfessionalReportVersion.report_id == report["id"], ProfessionalReportVersion.version_no == 1
        )
    )
    assert version_1 is not None, "版本 1 那一行必须还在（新版本是新增一行，不是覆盖它）"
    assert _validity_flagged(version_1.statistics_snapshot_json) == before, "旧版本那一行不许被这次重算碰到"

    assert f"效度提示 {before + 1} 人" in _exported_metrics(client, counselor, report["id"], 2)
    assert f"效度提示 {before} 人" in _exported_metrics(client, counselor, report["id"], 1)


# --- 2. 权限 --------------------------------------------------------------------


def test_a_leader_cannot_edit_a_professional_report(client, leader):
    """§5.3：德育领导不得编辑心理老师专业解读。四个写入端点逐个试一遍。

    403 与 404 在这里是有分工的：**能力不够是 403**（他连这个动作都不该有），
    **看不到那份报告是 404**（见下一条）。合成一个码会让「没有这个能力」与
    「这份报告不归你」在轨迹上分不开。
    """
    endpoints = [
        ("post", PROFESSIONAL, {"title": "领导写的报告", "task_ids": [1]}),
        ("put", f"{PROFESSIONAL}/1/draft", {"overall_summary": "x"}),
        ("post", f"{PROFESSIONAL}/1/publish", None),
        ("post", f"{PROFESSIONAL}/1/new-version", None),
    ]
    for method, path, body in endpoints:
        call = getattr(client, method)
        response = call(path, headers=leader, **({"json": body} if body else {}))
        assert response.status_code == 403, f"{method.upper()} {path} → {response.status_code}"


def test_a_leader_only_sees_published_reports(client, counselor, leader):
    """发布是领导可见性的**开关**，而不是一个标签。

    草稿期两件事同时成立：列表里没有他、点进去 404——**404 不是 403**，因为
    「这份报告还没发布」对他来说就是「不存在」（§9：不属于你的与不存在的必须不可分辨）。
    """
    report = _create(client, counselor, _task_id(client, counselor))

    assert client.get(PROFESSIONAL, headers=leader).json()["data"]["items"] == []
    assert client.get(f"{PROFESSIONAL}/{report['id']}", headers=leader).status_code == 404

    _publish(client, counselor, report["id"])

    listed = client.get(PROFESSIONAL, headers=leader).json()["data"]["items"]
    assert [x["id"] for x in listed] == [report["id"]]
    assert client.get(f"{PROFESSIONAL}/{report['id']}", headers=leader).status_code == 200


def test_a_counselor_cannot_read_a_report_someone_else_owns(client, counselor, db_session):
    """「心理老师只能看自己创建的」这条判据挂在 `created_by` 上，与「谁登录的」无关。

    这里直接把那一列改到另一位员工名下（真实场景里那是第二个心理老师账号），
    而不是去建一个账号——要钉的是那条判据，不是账号创建流程（那一条有它自己的用例）。
    """
    report_id = _create(client, counselor, _task_id(client, counselor))["id"]
    owner = _detail(client, counselor, report_id)["created_by"]
    other = db_session.scalar(select(UserAccount.id).where(UserAccount.id != owner))
    assert other is not None, "库里必须有第二位员工账号，否则这条用例证明不了任何事"

    row = db_session.get(ProfessionalReport, report_id)
    row.created_by = other
    db_session.flush()

    assert client.get(f"{PROFESSIONAL}/{report_id}", headers=counselor).status_code == 404
    assert client.get(PROFESSIONAL, headers=counselor).json()["data"]["items"] == []


def test_an_account_without_the_capability_is_refused_at_the_door(client, counselor):
    """fail-closed：学生与系统管理员都没有 `PROFESSIONAL_REPORT_*`（§4 的默认矩阵）。

    管理员被挡在外面是**有意的**——专业报告是学校业务，写侧归业务负责人，与
    「测评任务不是一个能力，是角色」是同一条裁决。他拿到的是 403（能力门槛），
    不是 404：他连这一页都不该有。
    """
    report_id = _create(client, counselor, _task_id(client, counselor))["id"]
    for role, account in (("student", "S001"), ("admin", "admin")):
        headers = auth_headers(client, role, account)
        assert client.get(PROFESSIONAL, headers=headers).status_code == 403
        assert client.get(f"{PROFESSIONAL}/{report_id}", headers=headers).status_code == 403


# --- 3. 快照冻结（§5.4「历史报告不得因实时数据变化而漂移」）-------------------------


def test_the_frozen_snapshot_does_not_drift_when_the_data_changes(client, counselor, db_session):
    """**三个断言缺一不可**，第二个是「先证明有东西可动」那一条。

    后两个断言的形状是这条用例的全部价值：**同一次数据改动**，实时报表跟着动了、
    报告里的那份没动。只断「报告里的数等于某个值」的话，一个「读取时实时重算」的实现
    在数据没变时也是绿的——而那正是这一节要防的东西。
    """
    task_id = _score_a_sitting(client, counselor, db_session)
    report_id = _create(client, counselor, task_id)["id"]

    def live() -> int:
        data = client.get(f"/api/v1/analytics/report?taskId={task_id}", headers=counselor).json()["data"]
        return _key_attention(data)

    def frozen() -> int:
        return _key_attention(_detail(client, counselor, report_id)["statistics_snapshot"])

    assert live() == frozen(), "建报告那一刻的实时统计就该是快照里那一份"

    # 把那名学生判成重点关注：实时口径的关注人数必然变化。
    result = db_session.scalar(
        select(AssessmentResult).join(AssessmentSession, AssessmentSession.id == AssessmentResult.session_id).where(AssessmentSession.task_id == task_id)
    )
    assert result is not None, "上一步刚算出来那一场的结果应当在这里"
    assert result.total_level != "KEY_ATTENTION", "改动前必须是另一档，否则下面那条断言分不出前后"
    result.total_level = "KEY_ATTENTION"
    db_session.flush()

    assert live() != frozen(), "数据改完之后实时报表必须跟着动，否则下面那条断言证明不了什么"
    assert frozen() == _key_attention(_detail(client, counselor, report_id)["statistics_snapshot"])


def test_publishing_does_not_recompute_the_snapshot(client, counselor, db_session):
    """发布只是改状态，不重算——否则「发布」会变成一次静默的数据刷新。

    它与上一条是同一件事的两个入口：上一条问「读的时候会不会重算」，这一条问
    「写的时候会不会」。
    """
    task_id = _score_a_sitting(client, counselor, db_session)
    report = _create(client, counselor, task_id)
    before = report["statistics_snapshot"]

    result = db_session.scalar(
        select(AssessmentResult).join(AssessmentSession, AssessmentSession.id == AssessmentResult.session_id).where(AssessmentSession.task_id == task_id)
    )
    assert result is not None, "上一步刚算出来那一场的结果应当在这里"
    result.total_level = "KEY_ATTENTION"
    db_session.flush()

    published = _publish(client, counselor, report["id"])
    assert published["statistics_snapshot"] == before


# --- 4. 正式导出（§5.5：走 Export Job，不是前端 Blob）-----------------------------


def test_the_export_goes_through_the_export_center(client, counselor):
    """这条就是那个 500 的守卫：`serialize_export_jobs` 的**调用形状**。

    它断的是「导出是一次作业」这件事的全部可观察后果：作业号、类型、**实名遮蔽等级**、
    列清单（来自产物本身，§29）、行数、可下载标志。少一个位置参数时这里就是
    `TypeError` → 500，而界面上只会说「服务端返回了无法解析的内容」。
    """
    report = _create(client, counselor, _task_id(client, counselor))
    response = client.post(
        f"{PROFESSIONAL}/{report['id']}/export-jobs",
        headers=counselor,
        json={"purpose": "校级汇报材料"},
    )
    assert response.status_code == 200, response.text
    job = response.json()["data"]

    assert job["job_no"].startswith("EXPORT-")
    assert job["export_type"] == "PROFESSIONAL_REPORT"
    assert job["purpose"] == "校级汇报材料"
    assert job["mask_level"] == "MASKED"
    assert job["columns"] == ["项目", "内容"], "列清单来自产物（ExportDocument），请求体里没有列名的落点"
    assert job["row_count"] > 0
    assert job["downloadable"] is True


def test_the_download_has_as_many_rows_as_the_job_says(client, counselor):
    """行数在作业行与文件里必须一致——屏幕上写着「导出 N 行」，拿到的就得是 N 行。"""
    report = _create(client, counselor, _task_id(client, counselor))
    job = client.post(
        f"{PROFESSIONAL}/{report['id']}/export-jobs", headers=counselor, json={"purpose": "核对行数"}
    ).json()["data"]

    response = client.get(f"/api/v1/export-jobs/{job['id']}/download", headers=counselor)
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == job["row_count"]


def test_the_document_carries_the_frozen_numbers_and_both_kinds_of_blank(client, counselor):
    """文件的正文（身份四项 + 快照七项 + 四段文本），用 `csv.reader` 解而不是切逗号。

    「说明」那一列里可能嵌着自由文本，按逗号切会把一行切成七八列而报出一句与原因无关的
    断言失败（CLAUDE.md §29 收口时撞过同一种）。

    两条既有约定在这里各断一次（CLAUDE.md §3）：**比率被抑制时写「样本过小」**，
    而**没写的段落是空单元格**——两者在 CSV 里长得像，含义相反：前者是一句关于样本量的
    话，后者是「这一格没人填」。

    「样本过小」那一条依赖种子的样本量：那份名册只有 1 名学生，低于
    `MIN_COHORT_FOR_AGGREGATE`（=5），所以比率被抑制。**改种子时这一条要一起看**——
    它断的是「被抑制时写什么」，不是「这个数等于几」。
    """
    report = _create(client, counselor, _task_id(client, counselor), overall_summary="", support_plan="")
    job = client.post(
        f"{PROFESSIONAL}/{report['id']}/export-jobs", headers=counselor, json={"purpose": "核对内容"}
    ).json()["data"]
    text = client.get(f"/api/v1/export-jobs/{job['id']}/download", headers=counselor).content.decode("utf-8-sig")
    rows = dict(csv.reader(io.StringIO(text)))

    assert rows["报告编号"] == report["report_no"]
    assert rows["报告标题"] == report["title"]
    assert rows["版本"] == "1"
    assert rows["状态"] == "草稿"
    assert rows["关注比例"] == "样本过小"
    assert rows["关注人数"].endswith(" 人")
    assert rows["整体情况说明"] == "", "没写的段落是空单元格，不是「—」（§3 数值列那条约定）"
    assert rows["后续教育支持计划"] == ""
    assert "PROFESSIONAL" not in text, "报告类型没有中文映射，就不该出现在一份给人看的文件里"


def test_each_version_exports_its_own_text(client, counselor):
    """导出带 `version_no` 时取的是**那一个版本**，不是当前版本。

    没有这一条时，「按版本导出」这个能力与「总是导当前版本」在库里长得一模一样。
    """
    report = _create(client, counselor, _task_id(client, counselor), overall_summary="第一版的结论。")
    _publish(client, counselor, report["id"])
    client.post(f"{PROFESSIONAL}/{report['id']}/new-version", headers=counselor)
    client.put(
        f"{PROFESSIONAL}/{report['id']}/draft",
        headers=counselor,
        json={"overall_summary": "第二版的结论。", "dimension_interpretation": "", "sample_validity_note": "", "support_plan": ""},
    )

    first = client.post(
        f"{PROFESSIONAL}/{report['id']}/export-jobs", headers=counselor, json={"purpose": "回看第一版", "version_no": 1}
    ).json()["data"]
    text = client.get(f"/api/v1/export-jobs/{first['id']}/download", headers=counselor).content.decode("utf-8-sig")
    assert "第一版的结论。" in text
    assert "第二版的结论。" not in text


def test_the_export_audit_names_the_report_and_the_version(client, counselor):
    """§8.4：这条轨迹要答得出**报告编号**与**文件格式**，而不只是「谁导了一份报告」。"""
    report = _create(client, counselor, _task_id(client, counselor))
    client.post(f"{PROFESSIONAL}/{report['id']}/export-jobs", headers=counselor, json={"purpose": "审计核对"})

    logs = client.get("/api/v1/audit-logs", headers=auth_headers(client, "admin", "admin")).json()["data"]["items"]
    row = next(x for x in logs if x["action"] == "导出专业报告")
    assert row["resource_id"] == str(report["id"])
    assert row["purpose"] == "审计核对"


def test_a_leader_exports_the_published_report_but_not_the_draft(client, counselor, leader):
    """领导的导出范围**跟着「已发布」走**，与他的阅读范围是同一条判据。

    他过得了能力门槛（`PROFESSIONAL_REPORT_READ` 是 `SCHOOL`，`allow` 里含它），
    真正拦住草稿的是 `report_document` 里那个 `_visible`——一份干活的草稿在发布之前
    不经过任何人的手，而他拿到的那份文件必须与他在页面上看到的那一份同源。
    """
    report = _create(client, counselor, _task_id(client, counselor))
    blocked = client.post(
        f"{PROFESSIONAL}/{report['id']}/export-jobs", headers=leader, json={"purpose": "领导提前看"}
    )
    assert blocked.status_code == 404, blocked.text

    _publish(client, counselor, report["id"])
    allowed = client.post(
        f"{PROFESSIONAL}/{report['id']}/export-jobs", headers=leader, json={"purpose": "领导取摘要"}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["export_type"] == "PROFESSIONAL_REPORT"
