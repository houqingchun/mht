"""统计分析报表中心的守卫。

2026-09-21 之前只有 3 条用例，全部用 1 名学生——覆盖了小样本抑制和效度过滤，
但**没有一条验过年级/班级分组、分布形状、工作项计数**。这里补上。

与 `test_analytics_report.py`（只有 63 行）同源：那个文件证明了「接口能通、
小样本能抑制、效度模式能切换」；这个文件证明了「分组聚合、分布返回、工作项计数、
信号类型去重、权限矩阵」都成立。

两条互补，不互相替代。
"""

from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers
from sqlalchemy import select
from app.models.assessment import AssessmentSession, AssessmentTarget, AssessmentTask


def _submit(client, yes_numbers: set[int]) -> dict:
    """提交一份答卷，回 (headers, session_id)。"""
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id, yes_numbers=yes_numbers)
    response = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert response.status_code == 200, response.text
    return {"headers": headers, "session_id": session_id}


VALIDITY_QS = {82, 84, 86, 88, 90, 92, 94, 96, 98, 100}
KEY_QS = {85, 97}


def _report(client, role: str = "counselor", account: str = "13800000001", **params) -> dict:
    """拿一份报表。默认心理老师。"""
    headers = auth_headers(client, role, account)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    response = client.get(f"/api/v1/analytics/report?{query}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ---------------------------------------------------------------------------
# 年级/班级分组
# ---------------------------------------------------------------------------

def test_report_contains_grade_and_class_groupings(client):
    """报表返回 grades 和 classes 分组，且每个组有自己的 dimensions。"""
    _submit(client, {1, 2, 3})
    data = _report(client)

    # seed_demo 只有「初一」一个年级、「1班」一个班级。
    assert len(data["grades"]) >= 1
    grade = data["grades"][0]
    assert grade["grade_name"] == "初一"
    assert "dimensions" in grade
    assert len(grade["dimensions"]) == 8  # MHT 八维度

    assert len(data["classes"]) >= 1
    cls = data["classes"][0]
    assert cls["class_name"] == "1班"
    assert cls["grade_name"] == "初一"
    assert "dimensions" in cls


def test_multi_task_report_deduplicates_students_and_keeps_latest_result(client, db_session):
    """分批任务合并时同一学生只进一次分母，不因重复目标行被重复计数。"""
    submitted = _submit(client, {1, 2, 3})
    session_id = submitted["session_id"]
    submitted_session = db_session.get(AssessmentSession, session_id)
    assert submitted_session is not None
    original_target = db_session.scalar(select(AssessmentTarget).where(
        AssessmentTarget.task_id == submitted_session.task_id,
        AssessmentTarget.student_id == submitted_session.student_id,
    ))
    assert original_target is not None
    original_task = db_session.get(AssessmentTask, original_target.task_id)
    assert original_task is not None

    later_batch = AssessmentTask(
        task_no=f"MULTI-{original_task.id}",
        name="同一普查的后续导入批次",
        scale_id=original_task.scale_id,
        school_id=original_task.school_id,
        scope_type="STUDENT",
        status="ACTIVE",
        source="IMPORTED",
    )
    db_session.add(later_batch)
    db_session.flush()
    db_session.add(AssessmentTarget(
        task_id=later_batch.id,
        student_id=original_target.student_id,
        status="NOT_STARTED",
        school_id_snapshot=original_target.school_id_snapshot,
        student_no_snapshot=original_target.student_no_snapshot,
        student_name_snapshot=original_target.student_name_snapshot,
        grade_name_snapshot=original_target.grade_name_snapshot,
        class_name_snapshot=original_target.class_name_snapshot,
        gender_snapshot=original_target.gender_snapshot,
        age_snapshot=original_target.age_snapshot,
        participation_disposition="REQUIRED",
        target_source="TASK_SCOPE",
    ))
    db_session.commit()

    headers = auth_headers(client, "counselor", "13800000001")
    response = client.get(
        f"/api/v1/analytics/report?taskIds={original_task.id}&taskIds={later_batch.id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["tasks"]) == 2
    assert data["sample_quality"]["target_count"] == 1
    assert data["sample_quality"]["completed_count"] == 1
    assert data["sample_quality"]["n_evaluable"] == 1


def test_grade_and_class_cohort_counts(client):
    """年级和班级分组的样本计数合理——不写死数字，只验证结构。"""
    _submit(client, {1, 2, 3})
    data = _report(client)

    grade = data["grades"][0]
    assert grade["grade_name"] == "初一"
    # target_count 至少包含我们刚提交的那个学生。
    assert grade["target_count"] >= 1
    assert grade["completed_count"] >= 1
    # sample_count 在 ALL_CALCULATED 下 >= 已完成数。
    assert grade["sample_count"] >= grade["completed_count"]

    cls = data["classes"][0]
    assert cls["class_name"] == "1班"
    assert cls["grade_name"] == "初一"


# ---------------------------------------------------------------------------
# 分布形状（非抑制）
# ---------------------------------------------------------------------------

def test_dimension_distribution_shape_when_not_suppressed(client):
    """当 cohort 足够大时，维度分布返回实际计数和比率。

    seed_demo 已经预填了若干已交卷的学生。如果总数 >= MIN_COHORT_FOR_AGGREGATE(5)，
    分布不被抑制。我们只验证：「如果不被抑制，分布形状就是 3 行带比率」。
    """
    _submit(client, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10})
    data = _report(client)

    learning = next(
        item for item in data["dimensions"] if item["dimension_code"] == "LEARNING_ANXIETY"
    )
    # 如果 seed_demo 的已交卷学生 >= 5（大概率），则不被抑制。
    if not learning["suppression"]["suppressed"]:
        assert learning["mean_score"] is not None
        assert len(learning["distribution"]) == 3  # LOW / MEDIUM / HIGH
        for level in learning["distribution"]:
            assert "count" in level
            assert "range_code" in level
            # 比率不为 None（>= 5 人时有比率）。
            assert level["rate"] is not None or level["count"] == 0
    else:
        # 如果恰好样本 < 5（seed_demo 被清理过），验证抑制行为。
        assert learning["mean_score"] is None
        for level in learning["distribution"]:
            assert level["rate"] is None


# ---------------------------------------------------------------------------
# 工作项计数
# ---------------------------------------------------------------------------

def test_pending_review_work_items_counted(client):
    """待复核工作项数 = PENDING 状态的 risk_event 行（requires_manual_review=True）。

    _submit 传了 85（重点题），引擎会为它开一条 MANUAL_REVIEW_REQUIRED 的风险事件。
    """
    _submit(client, {85})
    data = _report(client)

    # 重点题 85 答「是」→ 开一条 PENDING 的风险事件。
    assert data["overview"]["pending_review_work_items"] >= 1
    # 还没有人做过复核。
    assert data["overview"]["completed_review_work_items"] == 0


def test_signal_type_stats_deduplicates_students(client):
    """signal_type_stats 按学生去重——同一学生触发多条同类信号，只数一次。

    _submit 传 {85, 97}（两道重点题都答「是」），引擎会开两条 MANUAL_REVIEW_REQUIRED
    的风险事件，但同一学生的两条应合并为 1 人。
    """
    _submit(client, {85, 97})
    data = _report(client)

    # 信号类型码是后端原值（`RiskEvent.signal_type`），不是翻译后的。
    # 实际值见 `scale_engine/engine.py` 的 `SIGNAL_TYPE_BY_RISK_TYPE`。
    manual_review_stats = [
        s for s in data["overview"]["signal_type_stats"]
        if s["signal_type"] == "MANUAL_REVIEW_REQUIRED"
    ]
    assert len(manual_review_stats) == 1
    # 一个学生答了两道重点题 → 2 条 risk_event，但去重后 = 1 人。
    assert manual_review_stats[0]["student_count"] == 1


# ---------------------------------------------------------------------------
# 解释性警告
# ---------------------------------------------------------------------------

def test_interpretation_warnings_when_validity_flagged(client):
    """当有效样本中有触发效度复测建议的结果时，报告包含解释性警告。"""
    _submit(client, {82, 84, 86, 88, 90, 92, 94})  # 7 道效度题 → RETEST_RECOMMENDED
    data = _report(client)

    assert any("效度" in w for w in data["interpretation_warnings"])


# ---------------------------------------------------------------------------
# 权限矩阵
# ---------------------------------------------------------------------------

def test_leader_can_access_report(client):
    """德育领导有 AGGREGATE_STATS: SCHOOL，可以访问报表。"""
    _submit(client, {1, 2, 3})
    data = _report(client, role="leader", account="13800000002")

    assert data["overview"]["sample_count"] >= 1
    assert len(data["dimensions"]) == 8


def test_student_cannot_access_report(client):
    """学生没有 AGGREGATE_STATS 能力，访问报表返回 403。"""
    _submit(client, {1, 2, 3})
    headers = auth_headers(client, "student", "S001")
    response = client.get("/api/v1/analytics/report", headers=headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 元数据完整性
# ---------------------------------------------------------------------------

def test_report_metadata_is_complete(client):
    """报表返回完整的元数据：task、as_of、timezone、scale、analysis_mode。"""
    _submit(client, {1, 2, 3})
    data = _report(client)

    assert data["report_id"]
    assert data["task"]["id"]
    assert data["task"]["name"]
    assert data["as_of"]
    assert data["timezone"] == "Asia/Shanghai"
    assert data["scale"]["code"] is not None
    assert data["scale"]["version"] is not None
    assert data["analysis_mode"] == "ALL_CALCULATED"


def test_report_permissions_reflect_role(client):
    """报表权限反映当前角色：心理老师能开学生详情，德育领导不能。"""
    _submit(client, {1, 2, 3})

    counselor_data = _report(client, role="counselor", account="13800000001")
    assert counselor_data["permissions"]["can_open_student_detail"] is True

    leader_data = _report(client, role="leader", account="13800000002")
    assert leader_data["permissions"]["can_open_student_detail"] is False
