"""导出作业：有效期、字段白名单、遮蔽等级、撤销与归属（§16.3 / §17(b)，阶段 8）。

这一期把导出从「一次点击、一屏字节」改成了**两跳**：创建那一步只登记一行 `export_job`
并落一份文件，字节一律从 `GET /export-jobs/{id}/download` 出去。所以本文件断的是
三件此前无处可断的事：

1. **这份文件是不是实名的**（`mask_level`）——它与 `purpose` 分开存，因为用途是自由
   文本、担不起机器判据，而这是导出审计唯一要回答的问题（§8 那次缺口的同一条）；
2. **这份文件里有哪些列**（`field_policy`）——白名单在**服务端**，请求里塞进来的字段名
   不会有任何效果（§16.3「导出接口不得接受任意字段名」）；
3. **这份文件现在还能不能取**——到期与撤销各有各的话，而管理员能叫停、不能取走。

`test_export_gates_the_quasi_identifiers_behind_unmasking` 那类断言为什么必须走完两跳，
记在 `test_audit_export_api.export_and_download` 的 docstring 里：建作业那一步回的是
作业载荷 JSON，而那份 JSON 里**带着 `columns`**，所以在它上面解析 CSV 会得到「表头
正确、内容为空」的一份东西——比红更糟的是那种绿。
"""

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.exporting import ExportJob
from app.services.export_service import (
    EXPORT_TYPE_CARE_CASES,
    JOB_STATUS_EXPIRED,
    JOB_STATUS_READY,
    JOB_STATUS_REVOKED,
    effective_export_status,
)
from app.models.common import now_local_naive
from app.tests.conftest import auth_headers
from app.tests.test_audit_export_api import create_case_with_followup

COUNSELOR = ("counselor", "13800000001")
LEADER = ("leader", "13800000002")
ADMIN = ("admin", "admin")

EXPORT_PATH = "/api/v1/care-cases/export"


@pytest.fixture()
def counselor_headers(client):
    """一位**手上有档案**的心理老师，外加他的登录头。

    基线种子里一份关怀档案都没有，而这一组断的是文件内容：没有档案时
    `export_care_cases_csv` 回一份只有表头的 CSV，于是「行数等于文件里的数据行数」
    这类断言在空文件上恒真，「列名对不对」也验不到任何一条真实的行。所以这一组统一
    从 `create_case_with_followup` 起步——用的是 `test_audit_export_api` 里那一份，
    不在这里另写第二份（两套「怎么造一份档案」的写法必然漂移，而漂移之后两组用例
    断的就不再是同一件事了）。
    """
    return create_case_with_followup(client)


def create_job(client, headers, **payload) -> dict:
    """建一份导出作业，返回作业载荷（`data` 那一层）。"""
    response = client.post(
        EXPORT_PATH, headers=headers, json={"purpose": "阶段工作统计", **payload}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def download(client, headers, job_id: int):
    return client.get(f"/api/v1/export-jobs/{job_id}/download", headers=headers)


# --- 两跳：建作业与取文件 -----------------------------------------------------


def test_creating_an_export_returns_a_job_and_no_bytes(client, counselor_headers):
    """**建作业那一步不发文件。** 这一条是整块设计的地基。

    它同时钉住了「返回体是一段 JSON」——若哪天有人把文件塞回这一步，这份载荷会变成
    CSV 而下面每一句都会红。三个理由（下载计数、有效期、遮蔽等级要有一行记录）写在
    `export_service` 的作业区开头。
    """
    job = create_job(client, counselor_headers)

    assert job["job_no"].startswith("EXPORT-")
    assert job["export_type"] == EXPORT_TYPE_CARE_CASES
    assert job["status"] == JOB_STATUS_READY
    assert job["downloadable"] is True
    assert job["download_count"] == 0
    assert job["downloaded_at"] is None
    assert job["purpose"] == "阶段工作统计"
    # `row_count` 与文件里的数据行数是同一个数（表头不算）——它记的是「这份文件里有
    # 几位学生」，不是「这一次查询扫了几行」。
    assert job["row_count"] > 0


def test_the_file_comes_from_the_download_endpoint_and_counts_the_download(
    client, counselor_headers, db_session
):
    """字节只从下载口出去，而每走一次都记在作业行上。

    `download_count` / `downloaded_at` 只有在下载**非走不可**时才说得上是真的——
    同一份文件从两扇门出去、其中一扇不计入，这两个字段记的就是一句半真的话。
    """
    job = create_job(client, counselor_headers)

    first = download(client, counselor_headers, job["id"])
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/csv")
    assert job["job_no"] in first.headers["content-disposition"]
    # BOM 与完成明细同一个理由：给中文 Excel / WPS 打开。
    assert first.content.startswith("﻿".encode("utf-8"))
    assert "学号" in first.content.decode("utf-8-sig").splitlines()[0]

    second = download(client, counselor_headers, job["id"])
    assert second.status_code == 200
    assert second.content == first.content

    row = db_session.get(ExportJob, job["id"])
    db_session.refresh(row)
    assert row.download_count == 2
    assert row.downloaded_at is not None


def test_the_file_hash_and_row_count_describe_the_bytes_on_disk(client, counselor_headers, db_session):
    """`file_sha256` 摘的是**盘上那一份**，所以它答得上「盘上这一份还是不是当初那一份」。

    这一列在今天唯一的用处是下面那条「文件被改动过就不发」的判据；没有它，一次磁盘
    损坏会表现为「用户拿到一份看不出哪里不对的文件」。
    """
    job = create_job(client, counselor_headers)
    row = db_session.get(ExportJob, job["id"])

    data = Path(row.file_uri).read_bytes()
    assert hashlib.sha256(data).hexdigest() == row.file_sha256
    assert row.row_count == len(data.decode("utf-8-sig").splitlines()) - 1


def test_a_file_that_no_longer_matches_its_hash_is_not_served(
    client, counselor_headers, db_session
):
    """盘上的那一份被改动过 → 409，**并且不把文件发出去**。

    发出去就是把一份来历不明的文件当成受控导出交出去，而收件人没有任何办法分辨。
    断言的是「响应里没有那段字节」，不只是「状态码是 409」。
    """
    job = create_job(client, counselor_headers)
    row = db_session.get(ExportJob, job["id"])
    Path(row.file_uri).write_bytes("学号,姓名\nS001,李四\n".encode("utf-8"))

    response = download(client, counselor_headers, job["id"])
    assert response.status_code == 409
    assert "李四" not in response.text


def test_a_missing_file_says_so_instead_of_handing_back_an_empty_one(
    client, counselor_headers, db_session
):
    """文件被删掉是第三种情况：不下载，但出路是「重新导出」而不是「有人叫停了它」。"""
    job = create_job(client, counselor_headers)
    row = db_session.get(ExportJob, job["id"])
    Path(row.file_uri).unlink()

    response = download(client, counselor_headers, job["id"])
    assert response.status_code == 410
    assert "重新导出" in response.json()["error"]["message"]


# --- 字段白名单是服务端的（§16.3） -------------------------------------------


def test_the_columns_are_the_servers_own_whitelist(client, counselor_headers):
    """请求体里塞进来的字段名**没有任何效果**。

    这是 §16.3「导出接口不得接受任意字段名」的可执行形式：白名单从文件自己的表头
    取（`export_care_cases_csv` 里那几张固定的列清单），请求模型里根本没有能表达
    「我要哪几列」的字段，所以多传的键连服务层都到不了。
    """
    job = create_job(
        client,
        counselor_headers,
        # 三个都是「如果有一天有人把白名单做成可配的」，最可能被当成入口的形状。
        columns=["密码", "家庭回访正文", "重点题回答"],
        fields=["password"],
        mask_names=True,
    )

    assert "密码" not in job["columns"]
    assert "家庭回访正文" not in job["columns"]
    assert "学号" in job["columns"]


def test_the_mask_level_records_whether_the_file_is_identified(client, db_session):
    """遮蔽等级与用途**分开存**：同样的 action、同样的 resource_type、同样的 purpose，
    遮蔽与实名两条审计行长得一模一样，轨迹就答不上「这份文件是不是实名的」（§8）。

    两档一次跑完（同一次停留里的两次导出），因为单看其中一份证明不了「它记的是当时
    那个选择」——写死成任何一个常量都能让一份断言通过。
    """
    headers = auth_headers(client, *COUNSELOR)
    masked = create_job(client, headers, mask_names=True)
    identified = create_job(client, headers, mask_names=False)

    assert masked["mask_level"] == "MASKED"
    assert identified["mask_level"] == "IDENTIFIED"

    names = {
        row.job_no: row.mask_level for row in db_session.scalars(select(ExportJob)).all()
    }
    assert names[masked["job_no"]] == "MASKED"
    assert names[identified["job_no"]] == "IDENTIFIED"


def test_the_scope_snapshot_is_a_snapshot_not_a_reference(client, counselor_headers, db_session):
    """范围快照记的是**导出那一刻**的授权范围，之后配置怎么改都不会动它。

    与 `assessment_target` 的名册快照同一条规矩：一份上个月导出去的文件被追问
    「当时按什么口径导的」时，答案必须在那一行里，而不是「现在这个人管着谁」。
    存 id 而不是名称，因为名称会改、id 不会。
    """
    job = create_job(client, counselor_headers)
    snapshot = job["scope_snapshot"]

    assert snapshot["role"] == "counselor"
    assert snapshot["scopes"], "心理老师的范围行是权限的前提，不该是空的"
    assert {"scope_type", "school_id", "grade_id", "class_id", "student_id"} == set(
        snapshot["scopes"][0]
    )
    # 五个维度都写出来、没有的那几个是 null：只写非空项的话，「这一行是 SCHOOL 范围
    # （所以没有 grade_id）」与「这一行本该有 grade_id 而它丢了」在 JSON 上长得一样。
    assert "grade_id" in snapshot["scopes"][0]

    row = db_session.get(ExportJob, job["id"])
    assert row.scope_snapshot == snapshot


# --- 到期与撤销 ---------------------------------------------------------------


def test_an_expired_job_cannot_be_downloaded(client, counselor_headers, db_session):
    """§16.3：过期后自动失效。**过期是现算的，不是写进去的状态。**

    `expires_at` 走到过去之后，作业行本身一个字都没变——所以这一条同时钉住了
    `effective_export_status` 是派生值（照 `effective_task_status` 与
    `effective_session_predicate`）。
    """
    job = create_job(client, counselor_headers)
    row = db_session.get(ExportJob, job["id"])
    assert row.expires_at is not None, "默认 TTL 是 24 小时，不该是永不过期"

    row.expires_at = now_local_naive() - timedelta(seconds=1)
    db_session.flush()
    assert effective_export_status(row) == JOB_STATUS_EXPIRED
    assert row.status == JOB_STATUS_READY, "派生值不改库里那一列"

    response = download(client, counselor_headers, job["id"])
    assert response.status_code == 410
    assert "有效期" in response.json()["error"]["message"]

    listed = client.get("/api/v1/export-jobs", headers=counselor_headers).json()["data"]["items"]
    entry = next(item for item in listed if item["id"] == job["id"])
    assert entry["status"] == JOB_STATUS_EXPIRED
    assert entry["downloadable"] is False


def test_revoking_a_job_closes_the_download_gate(client, counselor_headers, db_session):
    """撤销之后 410，而**再撤销一次不写第二行审计**。

    两次「撤销」是两个人同时点、或页面停在旧状态——`revoke_session` 那条同族的
    判据：一次什么都没做的点击不该在轨迹上留一行，否则读轨迹的人会以为那个动作
    发生过两次。
    """
    job = create_job(client, counselor_headers)

    revoked = client.post(
        f"/api/v1/export-jobs/{job['id']}/revoke",
        headers=counselor_headers,
        json={"reason": "导错了范围"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == JOB_STATUS_REVOKED
    assert revoked.json()["data"]["downloadable"] is False
    assert revoked.json()["data"]["revoked_at"] is not None

    again = client.post(
        f"/api/v1/export-jobs/{job['id']}/revoke", headers=counselor_headers, json={}
    )
    assert again.status_code == 200
    assert again.json()["data"]["status"] == JOB_STATUS_REVOKED

    response = download(client, counselor_headers, job["id"])
    assert response.status_code == 410
    assert "撤销" in response.json()["error"]["message"]

    audits = db_session.scalars(
        select(AuditLog).where(
            AuditLog.action == "撤销导出文件", AuditLog.resource_id == job["job_no"]
        )
    ).all()
    assert len(audits) == 1
    assert "导错了范围" in audits[0].detail


def test_revocation_wins_over_expiry(client, counselor_headers, db_session):
    """判据的次序是有意义的：先被撤销、后跨过有效期的那一份，它的故事是「有人叫停了
    它」，而不是「它自己到期了」——两句话对读轨迹的人是两件事。
    """
    job = create_job(client, counselor_headers)
    row = db_session.get(ExportJob, job["id"])

    client.post(f"/api/v1/export-jobs/{job['id']}/revoke", headers=counselor_headers, json={})
    row.expires_at = now_local_naive() - timedelta(hours=1)
    db_session.flush()

    assert effective_export_status(row) == JOB_STATUS_REVOKED
    response = download(client, counselor_headers, job["id"])
    assert response.status_code == 410
    assert "撤销" in response.json()["error"]["message"]


# --- 归属：管理员能叫停，不能取走 ---------------------------------------------


def test_a_counselor_cannot_see_another_counselors_job(client, db_session):
    """「不属于你」与「不存在」回**同一句话同一个码**。

    `job_id` 是客户端传来的，分开报就等于确认了某个 id 存在，于是另一所学校的导出
    编号成了可枚举的事实（§24 / §26 那条：「不属于你」与「不存在」在响应上必须不
    可分辨）。这里顺带断言了「那个 id 确实存在」——否则下面那句 404 在没建过作业的
    库上也是绿的。
    """
    owner = create_job(client, auth_headers(client, *COUNSELOR))
    other = auth_headers(client, "leader", "13800000002")

    missing = client.get("/api/v1/export-jobs/999999/download", headers=other)
    theirs = client.get(f"/api/v1/export-jobs/{owner['id']}/download", headers=other)

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["error"] == missing.json()["error"]


def test_the_admin_can_list_and_revoke_but_not_download(client, counselor_headers):
    """**一处有意的不对称。**

    管理员看得到所有人的作业、能替所有人撤销（数据外泄时那是紧急开关），但不能替
    别人把文件取走——他的心理详情能力是 `NONE`，一份实名名册不该经过他。「能叫停」
    不需要、也不该顺带给出「能取走」。
    """
    job = create_job(client, counselor_headers)
    admin = auth_headers(client, *ADMIN)

    listed = client.get("/api/v1/export-jobs", headers=admin).json()["data"]["items"]
    entry = next(item for item in listed if item["id"] == job["id"])
    assert entry["requested_by_name"], "列表上要看得见是谁导的"
    assert entry["downloadable"] is False, "管理员下不下来，所以那个按钮不该亮"

    assert download(client, admin, job["id"]).status_code == 403

    revoked = client.post(f"/api/v1/export-jobs/{job['id']}/revoke", headers=admin, json={})
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == JOB_STATUS_REVOKED


def test_a_counselor_only_lists_his_own_jobs(client, counselor_headers):
    """非管理员那一半：列表只列自己创建的。

    同一次停留里两个人各导一份，否则「只列自己的」在一张只有一份作业的表上恒真。
    """
    mine = create_job(client, counselor_headers)
    theirs = create_job(client, auth_headers(client, *LEADER))

    listed = client.get("/api/v1/export-jobs", headers=counselor_headers).json()["data"]["items"]
    ids = {item["id"] for item in listed}
    assert mine["id"] in ids
    assert theirs["id"] not in ids


# --- 用途与下载审计 -----------------------------------------------------------


def test_a_controlled_export_without_a_purpose_is_rejected(client, counselor_headers):
    """用途是必填的（判据只有一处：`create_export_job`）。

    导出这件事要回答「这份文件为什么被导出去」，而那句话不在文件里。空白串与漏传
    都要挡——`  ` 与 `""` 在界面上是同一件事。
    """
    assert client.post(EXPORT_PATH, headers=counselor_headers, json={}).status_code == 422
    blank = client.post(EXPORT_PATH, headers=counselor_headers, json={"purpose": "   "})
    assert blank.status_code == 422
    assert "用途" in blank.json()["error"]["message"]


def test_downloading_writes_its_own_audit_before_the_bytes_leave(
    client, counselor_headers, db_session
):
    """**审计在返回数据之前写入**（§16.6）。

    这一条只能在这个方向上验：端点里 `write_audit` 排在 `return Response(...)` 之前，
    所以拿到字节的那一刻轨迹已经在库里了。审计的 `purpose` 取**作业上那一个**，不让
    下载的人再填一次——两次各填一个用途会让同一个文件的轨迹里出现两句不同的意图。
    """
    job = create_job(client, counselor_headers)
    assert download(client, counselor_headers, job["id"]).status_code == 200

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "下载导出文件", AuditLog.resource_id == job["job_no"]
        )
    )
    assert audit is not None
    assert audit.purpose == "阶段工作统计"
    assert audit.resource_type == "EXPORT"
    # 遮蔽模式进 `detail`：它与作业上那一列逐字同源，而不是这里另判一次。
    assert job["mask_level"] in audit.detail


def test_the_creation_audit_names_the_job_number(client, counselor_headers, db_session):
    """**建作业那一步也写审计**，而作业编号进 `detail`。

    编号是给人念的——操作员报故障时说的就是它。它**不挤进 `resource_id`**：审计的 `q`
    匹配 action / resource_type / resource_id 三列，把编号塞进去会改掉一次既有搜索的
    结果集（那三列本来放的是学号 / 任务号这一类的业务标识）。
    """
    job = create_job(client, counselor_headers)
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "导出关注档案摘要").order_by(AuditLog.id.desc())
    ).first()

    assert audit is not None
    assert audit.resource_id is None or not audit.resource_id.startswith("EXPORT-")
    assert job["job_no"] in audit.detail
    assert "姓名遮蔽" in audit.detail
