"""Pins the status codes the frontend maps to Chinese labels.

`frontend/src/services/labels.ts` keys its maps on these exact strings. A rename
on the backend renders raw codes to users ("FOLLOWING" instead of 跟进中) and
silently breaks the queue-tab filters — which is exactly what happened once:
the frontend expected FOLLOWING_UP while the backend emitted FOLLOWING.
"""

from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers

# Must match frontend/src/services/labels.ts STATUS_LABELS.
CASE_STATUSES = {"PENDING_REVIEW", "FOLLOWING", "OBSERVING", "CLOSED"}
# Must match frontend/src/services/labels.ts LEVEL_LABELS.
TOTAL_LEVELS = {"KEY_ATTENTION", "NEEDS_ATTENTION", "GENERAL_RANGE"}
# Must match frontend/src/services/labels.ts GENDER_LABELS.
GENDERS = {"MALE", "FEMALE"}
# Must match frontend/src/services/labels.ts SOURCE_LABELS.
# 写入方只有两处：模型的 `server_default="IN_SYSTEM"`（学生在本系统里作答，不显式写这一列），
# `assessment_import_service.commit_assessment_import` 的 `source="IMPORTED"`。
# 由 test_assessment_import_api.py 的
# `test_imported_sessions_use_the_documented_source_vocabulary` 走真实链路守住。
SOURCES = {"IN_SYSTEM", "IMPORTED"}
# Must match frontend/src/services/labels.ts DIMENSION_LABELS.
DIMENSION_CODES = {
    "LEARNING_ANXIETY",
    "INTERPERSONAL_ANXIETY",
    "LONELINESS",
    "SELF_BLAME",
    "SENSITIVITY",
    "PHYSICAL_SYMPTOMS",
    "PHOBIC_TENDENCY",
    "IMPULSIVE_TENDENCY",
}


def open_case(client):
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id, yes_numbers={85})
    assert client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers
    ).status_code == 200
    counselor = auth_headers(client, "counselor", "13800000001")
    case = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    return counselor, case


def test_case_status_transitions_use_the_documented_vocabulary(client):
    counselor, case = open_case(client)
    seen = {case["case_status"]}
    assert case["case_status"] in CASE_STATUSES

    detail = client.get(
        f"/api/v1/care-cases/{case['student_id']}", headers=counselor
    ).json()["data"]
    risk_event_id = detail["risk_events"][0]["id"]

    # A manual review moves the case into active follow-up.
    reviewed = client.post(
        f"/api/v1/care-cases/{case['case_id']}/reviews",
        headers=counselor,
        json={
            "risk_event_id": risk_event_id,
            "review_result": "建立持续关注档案",
            "confirmed_facts": "已完成初步沟通。",
        },
    )
    assert reviewed.status_code == 200
    after_review = client.get("/api/v1/care-cases", headers=counselor).json()["data"]["items"][0]
    seen.add(after_review["case_status"])

    # Closing it.
    closed = client.post(
        f"/api/v1/care-cases/{case['case_id']}/close",
        headers=counselor,
        json={
            "close_reason": "完成阶段跟进并进入一般观察",
            "close_note": "阶段闭环。",
            "confirm_follow_up_checked": True,
        },
    )
    assert closed.status_code == 200
    seen.add(closed.json()["data"]["status"])

    unknown = seen - CASE_STATUSES
    assert not unknown, f"后端返回了前端无法映射的状态码: {unknown}"


def test_total_level_uses_the_documented_vocabulary(client):
    counselor, case = open_case(client)
    assert case["total_level"] in TOTAL_LEVELS


def test_dimension_codes_use_the_documented_vocabulary(client):
    counselor, case = open_case(client)
    detail = client.get(
        f"/api/v1/care-cases/{case['student_id']}", headers=counselor
    ).json()["data"]
    codes = {d["dimension_code"] for d in detail["dimensions"]}
    unknown = codes - DIMENSION_CODES
    assert not unknown, f"后端返回了前端无法映射的维度编码: {unknown}"


def test_roster_gender_uses_the_documented_vocabulary(client):
    """名册发的性别必须是 labels.ts 认得出来的编码。

    导入既收 `女` 也收 `FEMALE`（学校用中文填表），落库只能是编码——否则前端会把
    「女」当成一个未知枚举，显示原样。这里走一遍真实的导入链路再读名册，
    而不是只看种子数据里那一个 MALE，那样即使某个分支把中文原样写库也照样全绿。
    """
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name,性别\nS600,许同学,初一,701,女\n"
    preview = client.post(
        "/api/v1/students/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]
    assert preview["valid_count"] == 1
    client.post(
        "/api/v1/students/import/commit",
        headers=headers,
        json={"preview_token": preview["preview_token"]},
    )

    items = client.get("/api/v1/students", headers=headers).json()["data"]["items"]
    emitted = {item["gender"] for item in items} - {None}
    assert "FEMALE" in emitted, "中文写法没有归一化成编码"
    unknown = emitted - GENDERS
    assert not unknown, f"后端返回了前端无法映射的性别编码: {unknown}"
