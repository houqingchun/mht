from sqlalchemy import select

from app.models.account import UserAccount
from app.models.audit import AuditLog
from app.models.organization import Student
from app.services.export_service import mask_student_name
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers


def rename_seeded_student(db_session, name: str) -> None:
    """Give the seeded student a name that is not already in masked form.

    The seed ships `name == masked_name == "林同学"`, which makes the two export
    modes indistinguishable no matter which field the code reads. Tests about
    masking need a name the mask would actually change.
    """
    student = db_session.scalar(select(Student))
    student.name = name
    db_session.commit()


def create_case_with_followup(client):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    submit = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers)
    assert submit.status_code == 200
    counselor_headers = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor_headers).json()["data"]["items"][0]
    followup = client.post(
        f"/api/v1/care-cases/{case['case_id']}/follow-ups",
        headers=counselor_headers,
        json={
            "record_type": "心理老师访谈",
            "confirmed_facts": "这是一段不应进入导出的敏感跟进正文",
            "next_follow_up_date": "2026-09-30",
        },
    )
    assert followup.status_code == 200
    return counselor_headers


def test_audit_logs_can_be_listed_by_admin(client):
    headers = auth_headers(client, "admin", "admin")
    response = client.get("/api/v1/audit-logs", headers=headers)
    assert response.status_code == 200
    assert "items" in response.json()["data"]


def rename_seeded_account(db_session, account: str, name: str) -> None:
    """Give a seeded account a display name that is not its role label.

    The seed ships `display_name == ROLE_LABELS[role]` ("心理老师" for the
    counselor), so a test asserting `actor_name == "心理老师"` would pass even if
    the endpoint returned the *role* and never resolved the person — the two
    strings are identical. Same trap as `rename_seeded_student` above: the
    fixture's values have to be distinguishable from the thing being denied.
    """
    user = db_session.scalar(select(UserAccount).where(UserAccount.account == account))
    user.display_name = name
    db_session.commit()


def test_audit_rows_name_the_person_not_just_the_role(client, db_session):
    """每位操作人。角色只说明「哪个角色做的」，审计要追的是人。

    这个用例刻意**先留下轨迹、再改名**：名字是读取时从 user_account 现取的，
    不是写进行里的一份副本。所以改名之后回看历史，那条轨迹会跟着指向新的称呼，
    而不是定格成一个已经没人这么叫的旧名。
    """
    counselor_headers = auth_headers(client, "counselor", "13800000001")
    assert client.get("/api/v1/care-cases", headers=counselor_headers).status_code in (200, 403)

    rename_seeded_account(db_session, "13800000001", "李心怡")

    admin_headers = auth_headers(client, "admin", "admin")
    items = client.get("/api/v1/audit-logs", headers=admin_headers, params={"limit": 200}).json()["data"]["items"]
    # 登录成功那一行是心理老师写的，和后面那些敏感读取是同一个 actor。
    row = next(item for item in items if item["actor_role"] == "counselor")
    assert row["actor_name"] == "李心怡"
    assert row["actor_account"] == "13800000001"


def test_anonymous_audit_rows_keep_the_attempted_account_out_of_the_actor_column(client):
    """登录失败没有操作人——但被尝试的账号不能被搬到「操作人」列上。

    `resource_id` 上写着那个账号名，所以「谁做的」和「对谁做的」各自都还看得见；
    把它们合成一列之后就再也分不开了：一次失败的登录会看起来像某个学生自己
    成功查看了一次档案。
    """
    response = client.post(
        "/api/v1/auth/login",
        json={"account": "S001", "password": "wrong-password", "role": "student"},
    )
    assert response.status_code in (401, 403)

    admin_headers = auth_headers(client, "admin", "admin")
    items = client.get("/api/v1/audit-logs", headers=admin_headers, params={"limit": 200}).json()["data"]["items"]
    failed = next(item for item in items if item["action"] == "登录失败")
    assert failed["actor_user_id"] is None
    assert failed["actor_name"] is None
    assert failed["actor_account"] is None
    assert failed["resource_id"] == "S001"


def test_care_case_export_is_masked_and_writes_audit(client, db_session):
    headers = create_case_with_followup(client)
    response = client.post("/api/v1/care-cases/export", headers=headers, json={"purpose": "阶段工作统计"})
    assert response.status_code == 200
    body = response.text
    assert "林同学" in body
    assert "敏感跟进正文" not in body
    assert "KEY_QUESTION_85_YES" not in body
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "导出关注档案摘要"))
    assert audit is not None


def test_high_risk_export_requires_purpose(client):
    headers = create_case_with_followup(client)
    response = client.post("/api/v1/care-cases/high-risk/export", headers=headers, json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PURPOSE_REQUIRED"


def test_masking_mode_changes_the_file(client, db_session):
    """mask_names must actually mask.

    It previously wrote `student.masked_name`, a stored column that all three
    import paths fill from the real name (seed, seed_demo, student_import_service),
    so mask_names=True and False produced byte-identical CSVs and the opt-out was
    the only thing that did anything. Pinned here against a name the mask changes.
    """
    headers = create_case_with_followup(client)
    rename_seeded_student(db_session, "王小明")

    masked = client.post("/api/v1/care-cases/export", headers=headers, json={"purpose": "阶段工作统计"})
    named = client.post(
        "/api/v1/care-cases/export",
        headers=headers,
        json={"purpose": "阶段工作统计", "mask_names": False},
    )
    assert masked.status_code == named.status_code == 200
    assert "王同学" in masked.text
    assert "王小明" not in masked.text
    assert "王小明" in named.text
    assert masked.text != named.text


def test_single_care_case_export_masks_too(client, db_session):
    """The single-student route builds its own call; it must not skip the mask."""
    headers = create_case_with_followup(client)
    rename_seeded_student(db_session, "王小明")
    case = client.get("/api/v1/care-cases", headers=headers).json()["data"]["items"][0]

    response = client.post(
        f"/api/v1/care-cases/{case['student_id']}/export",
        headers=headers,
        json={"purpose": "转介材料"},
    )
    assert response.status_code == 200
    assert "王同学" in response.text
    assert "王小明" not in response.text


def test_mask_student_name_keeps_the_surname_only():
    assert mask_student_name("王小明") == "王同学"
    assert mask_student_name("林同学") == "林同学"
    assert mask_student_name("O'Neil") == "O同学"
    assert mask_student_name(None) == ""
    assert mask_student_name("") == ""


def test_leader_cannot_unmask_the_export(client, db_session):
    """The export capability must not become a way around the detail capability.

    A leader holds PROGRESS_SUMMARY for CONTROLLED_EXPORT but only SUMMARY for
    STUDENT_PSYCH_DETAIL, so the roster answers 403 to them — while
    `mask_names=False` used to hand back the same names in a CSV. Holding an
    export permit says a role may export summaries, not identified ones.
    """
    create_case_with_followup(client)
    leader = auth_headers(client, "leader", "13800000002")
    assert client.get("/api/v1/students", headers=leader).status_code == 403

    refused = client.post(
        "/api/v1/care-cases/export",
        headers=leader,
        json={"purpose": "阶段工作统计", "mask_names": False},
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "MASK_FORBIDDEN"

    # The summary export the leader *is* entitled to keeps working.
    allowed = client.post(
        "/api/v1/care-cases/export", headers=leader, json={"purpose": "阶段工作统计"}
    )
    assert allowed.status_code == 200


def test_leader_cannot_unmask_the_high_risk_export(client):
    """The same guard has to cover every export route, not just the bulk one."""
    create_case_with_followup(client)
    leader = auth_headers(client, "leader", "13800000002")
    response = client.post(
        "/api/v1/care-cases/high-risk/export",
        headers=leader,
        json={"purpose": "阶段工作统计", "mask_names": False},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MASK_FORBIDDEN"


def test_export_audit_records_the_masking_mode(client, db_session):
    """A masked and an unmasked export used to write identical audit rows.

    Purpose is free text and cannot carry it, so the trail could not answer the
    one question an export audit exists for: was this file identified?
    """
    headers = create_case_with_followup(client)

    client.post("/api/v1/care-cases/export", headers=headers, json={"purpose": "阶段工作统计"})
    client.post(
        "/api/v1/care-cases/export",
        headers=headers,
        json={"purpose": "阶段工作统计", "mask_names": False},
    )

    details = db_session.scalars(
        select(AuditLog.detail).where(AuditLog.action == "导出关注档案摘要").order_by(AuditLog.id)
    ).all()
    assert any(detail and "姓名遮蔽" in detail for detail in details)
    assert any(detail and "实名" in detail for detail in details)


def test_export_audit_records_the_masking_mode_for_a_named_student(client, db_session):
    """The single-student route writes its own row; it needs the mode as well."""
    headers = create_case_with_followup(client)
    case = client.get("/api/v1/care-cases", headers=headers).json()["data"]["items"][0]
    client.post(
        f"/api/v1/care-cases/{case['student_id']}/export",
        headers=headers,
        json={"purpose": "转介材料"},
    )
    audit = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "导出单个学生摘要").order_by(AuditLog.id.desc())
    )
    assert audit is not None
    assert audit.detail and "姓名遮蔽" in audit.detail


def test_student_cannot_list_audit_logs(client):
    headers = auth_headers(client, "student", "S001")
    response = client.get("/api/v1/audit-logs", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"

