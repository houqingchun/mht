"""统计分析报表中心的守卫。

2026-09-21 之前只有 3 条用例，全部用 1 名学生——覆盖了小样本抑制和效度过滤，
但**没有一条验过年级/班级分组、分布形状、工作项计数**。这里补上。

与 `test_analytics_report.py`（只有 63 行）同源：那个文件证明了「接口能通、
小样本能抑制、效度模式能切换」；这个文件证明了「分组聚合、分布返回、工作项计数、
信号类型去重、权限矩阵」都成立。

两条互补，不互相替代。
"""

from datetime import UTC, datetime, timedelta

from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers
from sqlalchemy import select
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
)
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import ScaleRule
from app.services.analytics_service import MIN_COHORT_FOR_AGGREGATE, _report_total_bands
from app.services.scale_rule_service import RULE_TYPE
from app.tests.factories import make_sitting, make_target


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


# ---------------------------------------------------------------------------
# 关注等级分布（`ScoreBandBars.vue` 的数据面）
#
# 视图那一层由 `e2e/app.spec.ts` 的 `expectScoreBands` 钉住（三段中文按序、
# 每段带一个「N~M 分」、**柱子高度非 0**）；这里钉的是它证明不了的两件事：
# **数得对不对**（按人不按次）与**区间从哪来**（规则版本，不是写死的三段）。
#
# 三档中文就是关注等级那一套（一般观察 / 需要关注 / 重点关注）。这里一度另有一张
# 描述性的表（正常 / 心理状态欠佳或有问题倾向 / 心理问题倾向较严重），2026-09-22 删掉了：
# 同一个码在一屏上不能有两个名字（§3「高度关注」那条裁决的同一句话）。**一档说的是分数
# 区间，不是给人下的判断**——那句话由 `scoreBandRangeText` 从规则版本里取「N~M 分」回答。
# ---------------------------------------------------------------------------

def test_total_score_distribution_sums_to_the_evaluable_sample(client):
    """三档永远都在、次序是从轻到重，且三档人数**合计等于可评价样本**。

    合计这一条是「按人取最近一场」那个口径的可执行形式（§11）：同一个学生复测两次
    只落一档，所以他不可能被数两次，三档之和也就不可能大于分母。反过来，若哪天有人
    把它改成「按场次」累加，这条会红在这里——而屏幕上那三根柱子看起来完全正常。
    """
    _submit(client, {1, 2, 3})
    data = _report(client)

    bands = data["overview"]["level_distribution"]
    # 次序是契约：`ScoreBandBars.vue` 直接照数组次序渲染，而读者读的是「从轻到重」。
    # 这三个码本身由 `test_status_vocabulary.py` 的 `TOTAL_LEVELS` 与前端 `labels.ts` 对齐。
    assert [b["level_code"] for b in bands] == [
        "GENERAL_RANGE", "NEEDS_ATTENTION", "KEY_ATTENTION"
    ]
    assert sum(b["student_count"] for b in bands) == data["sample_quality"]["n_evaluable"]


def test_a_small_cohort_keeps_the_counts_but_not_the_rates(client):
    """分母小于 `MIN_COHORT_FOR_AGGREGATE` 时：**只收比率，不收计数**（§11）。

    `None` 不是 `0`。三五个人的百分比等于点名，所以服务端不给；而「我这两个学生里
    有几个需要关注」是学校必须能回答的问题，所以人数照给。两句话在界面上也必须
    长得不一样（视图那一侧把 `null` 渲染成「样本过小」而不是 `0.0%`）。

    这条依赖基线的规模（`seed.py` 刻意只有一名学生），是有意的：**要测的就是小样本那一档**，
    而把一个 cohort 缩到 5 人以下没有别的办法（只能造一个更小的口径）。哪天基线变大了，
    这条会红——那时换一个更小的夹具，不要把这个前提删掉（删掉之后它测的就不是抑制了）。
    """
    _submit(client, {1, 2, 3})
    data = _report(client)

    sample = data["sample_quality"]["n_evaluable"]
    assert 0 < sample < MIN_COHORT_FOR_AGGREGATE, "这条用例的前提是基线样本小于最小样本量"

    bands = data["overview"]["level_distribution"]
    # 计数没有被抑制，而且**真的有人落进去**（否则「计数照给」这句话是空转的：
    # 三档全 0 时下面那个 any(...) 才是这条用例里唯一有牙的地方）。
    assert any(b["student_count"] > 0 for b in bands)
    assert sum(b["student_count"] for b in bands) == sample
    for band in bands:
        assert band["rate"] is None, "分母过小时不给百分比——百分号会变成点名"


def test_total_bands_follow_the_rule_version(client, db_session):
    """三段区间是从**这条规则版本**的 `config_json` 现读的，不是写死的三段（§6）。

    这条为什么必须存在：区间在屏幕上长得像一句文案（「0~55 分」），而它其实是**阈值**。
    写死在视图里或者写死在服务端，界面上完全看不出差别——直到学校调过分段，报表照旧
    印着旧区间，而拿到那一份的人都以为它是这一版规则判出来的。

    做法是**直接改那一行的 `config_json`**（探针），不走 `scale_rule_service.update_rule`：
    后者按 §6 会**分叉出一个新版本**，于是这批结果横跨两个版本、`total_bands` 变成 `None`
    ——那是另一条契约（见下一条用例），证明不了「区间跟着这一行走」。就地改这一行在库里
    不会发生（`uq_scale_rule_version` 挡着），这里要的只是让同一行换一份分段，看响应跟不跟。
    """
    _submit(client, {1, 2, 3})
    before = _report(client)

    default_bands = before["scale"]["total_bands"]
    assert [b["code"] for b in default_bands] == [
        "GENERAL_RANGE", "NEEDS_ATTENTION", "KEY_ATTENTION"
    ]
    assert (default_bands[0]["min"], default_bands[0]["max"]) == (0, 55)

    # 恰好一个版本 —— 这一场只有一个学生、一把卷子，这句话本身先证明了下面那一步的前提。
    versions = before["scale"]["rule_versions"]
    assert len(versions) == 1
    rule = db_session.scalar(select(ScaleRule).where(
        ScaleRule.rule_type == RULE_TYPE,
        ScaleRule.rule_version == versions[0],
    ))
    assert rule is not None, f"找不到这一批结果所用的规则行：{versions[0]}"

    # 换一份**同样合法**的分段（连续、不重叠、不越界），取值与默认那一组明显不同。
    # 整份重建而不是就地改键：`config_json` 是 `JSON` 列，就地改一个嵌套 dict 不会标脏。
    rule.config_json = {
        **rule.config_json,
        "total_levels": [
            {"code": "GENERAL_RANGE", "min": 0, "max": 40},
            {"code": "NEEDS_ATTENTION", "min": 41, "max": 70},
            {"code": "KEY_ATTENTION", "min": 71, "max": 100},
        ],
    }
    db_session.commit()

    after = _report(client)
    assert after["scale"]["total_bands"] == [
        {"code": "GENERAL_RANGE", "min": 0, "max": 40},
        {"code": "NEEDS_ATTENTION", "min": 41, "max": 70},
        {"code": "KEY_ATTENTION", "min": 71, "max": 100},
    ]
    # 而分档本身**不受影响**：`level_distribution` 数的是 `assessment_result.total_level`，
    # 那是判定那一刻按**当时**的规则算出来的（§1 的四层事实模型）。改规则是改「以后怎么判」，
    # 不是追认已经发生过的那一次——两者混在一起会让历史结果随一次配置改动整体漂移。
    assert after["overview"]["level_distribution"] == before["overview"]["level_distribution"]


def test_total_bands_are_withheld_when_the_version_cannot_be_resolved(client, db_session):
    """区间解不出来时发 `None`，**不猜一组默认区间**——视图那一侧对它的处置是
    「只出人数、不出「N~M 分」那一句」（`ScoreBandBars.vue` 的 `v-if="row.range"`）。

    发一组写死的区间比不发更糟：它会被人照着它去理解分数，而它不属于这一批结果。

    这里走的是「这批结果那个版本号在库里查不到」那一支（三个「不发」分支里唯一在
    本夹具里造得出来的一支）；横跨两个版本那一支见末尾。
    """
    submitted = _submit(client, {1, 2, 3})
    # 先证明「能解出来时是有的」——少了这一句，下面的 `is None` 在一个根本不发区间的
    # 实现上也是绿的（两个 `None` 长得一样，而它们的含义相反）。
    before = _report(client)
    assert before["scale"]["total_bands"] is not None
    versions = before["scale"]["rule_versions"]
    assert len(versions) == 1

    result = db_session.scalar(select(AssessmentResult).where(
        AssessmentResult.session_id == submitted["session_id"]
    ))
    assert result is not None
    result.rule_version = "MHT-RULE-0.0.1-探针"  # 库里没有这一行
    db_session.commit()

    data = _report(client)
    assert data["scale"]["rule_versions"] == ["MHT-RULE-0.0.1-探针"]
    assert data["scale"]["total_bands"] is None

    # 横跨两个版本那一支（「它们本来就没有**一套**阈值」）在**这个夹具里造不出来**：
    # `included` 按人取最近一场，两个版本要两个人，而基线刻意只有一名学生。所以它在这里
    # 直接问那个函数——一个「取 `rule_versions[0]` 就当自己的区间」的实现能通过上面那条
    # 请求级的断言（此刻只有一个版本），只有这一句会红。
    # 第一个版本**必须是真实存在的那个**：拿一个库里没有的号去拼，两支都会回 None，
    # 这一句就成了恒真的（2026-09-22 实测过一次，第一版写死 "MHT-RULE-1.1.0" 而
    # `MHT_RULE_VERSION` 已经是 1.1.1，变异验证照绿）。
    session = db_session.get(AssessmentSession, submitted["session_id"])
    scale_id = db_session.get(AssessmentTask, session.task_id).scale_id
    assert _report_total_bands(db_session, scale_id, [versions[0], "MHT-RULE-0.0.1-探针"]) is None


# ---------------------------------------------------------------------------
# 每个组自己那一份分布（`ScoreBandBars.vue` 被年级页 / 班级页复用的那一半）
#
# 上面四条全在**一名学生**的夹具上，而那个规模看不出这一层最重要的一件事：
# 基线是「1 个年级 / 1 个班 / 1 名学生」，于是**每组的分母与全校的分母是同一个数**。
# 一份「把全校的分母漏给每一组」「忘了按组过滤学生」的实现，在那样的数据上三档之和、
# 比率、次序**全都对得上**——它比的就是它自己。所以下面刻意在**同一场任务**里再造
# 一个比全校小的组：初二 6 人 ≠ 全校 7 人，两个数不同才分得开口径。
#
# 三条分工：年级那一份自己算、班级那一份自己算且年级 == 本年级各班之和、
# 比率用的是**这一组自己**的分母。视图那一层（两张图有没有渲染、三档中文与
# 「N~M 分」在不在）仍由 `e2e/app.spec.ts` 的 `expectScoreBands` 钉住。
# ---------------------------------------------------------------------------

NORMAL, ATTENTION, KEY = "GENERAL_RANGE", "NEEDS_ATTENTION", "KEY_ATTENTION"
# 三档的次序契约（从轻到重）——`ScoreBandBars.vue` 照数组次序渲染，不自己排。
BAND_CODES = [NORMAL, ATTENTION, KEY]


def _report_codes(rows: list[dict]) -> list[str]:
    return [row["level_code"] for row in rows]


def _report_counts(rows: list[dict]) -> list[int]:
    return [row["student_count"] for row in rows]


def _baseline(client, db_session) -> tuple[AssessmentTask, str, str]:
    """提交基线那一份答卷，把造第二个组需要的三样取出来：任务、规则版本号、基线那个人的档位。

    后两样都从**库里的结果行**上读，不写死。`rule_version` 写死会随量表版本号一起漂，
    而漂掉的那一天用例仍然是绿的（本文件 421-423 行记着这个形状栽过一次——写死
    `MHT-RULE-1.1.0` 而常量已经是 `1.1.1`，变异验证照绿）；`total_level` 同理：
    它由规则的分段决定，而分段属于**规则版本**（§6），不是一个可以写进测试的常数。
    两个组还必须共用**同一个**版本号，否则 `scale.total_bands` 会因为「横跨两个版本」
    而整个不发。
    """
    submitted = _submit(client, {1, 2, 3})
    session = db_session.get(AssessmentSession, submitted["session_id"])
    result = db_session.scalar(
        select(AssessmentResult).where(AssessmentResult.session_id == submitted["session_id"])
    )
    assert result is not None and result.rule_version, "基线那一场得先算出结果来"
    return db_session.get(AssessmentTask, session.task_id), result.rule_version, result.total_level


def _roster_school(db_session) -> School:
    """种子里那所学校。造年级 / 班级 / 学生都要它（复合外键要求三者同校）。

    找不到就**当场失败**而不是把 `None` 传下去：`None.id` 抛的是一句
    `AttributeError: 'NoneType' object has no attribute 'id'`，离真正的原因很远。
    """
    school = db_session.scalar(select(School).where(School.code == "QH"))
    assert school is not None, "种子数据里没有 QH 那所学校"
    return school


def _make_grade(db_session, school: School, name: str) -> Grade:
    """在同一所学校里加一个年级（`class_group_ibfk_3` 要求班级与年级同校）。"""
    grade = Grade(school_id=school.id, name=name, sort_order=9)
    db_session.add(grade)
    db_session.flush()
    return grade


def _make_class(
    db_session, task: AssessmentTask, grade: Grade, *,
    class_name: str, levels: list[str], rule_version: str, prefix: str,
) -> list[Student]:
    """在这个年级下加一个班，给 `levels` 里每一档各造一名已出结果的学生。

    三处不是随手写的：

    - **快照要显式传**。报表的分组读的是 `target.grade_name_snapshot` /
      `class_name_snapshot`（它回答「发放那一刻学校看到的是谁」，§1），而
      `make_target` 只填 `school_id_snapshot`——不传的话这一组会整体落进
      「未分年级 / 未分班级」，而屏幕上看起来只是一组名字奇怪的数据。
    - **`task_id` 要显式传**。`make_sitting` 默认 `task_id=None`（那是「一场不属于任何
      任务的会话」，导入那批用例的形状），而报表只收**选中任务**里的会话。
    - **`calculation_status` 要显式传**。它默认 `PENDING`，而报表只收
      `CALCULATED`——漏了就是「这一组一个人都没有」，同样看不出来。
    """
    class_group = ClassGroup(school_id=grade.school_id, grade_id=grade.id, name=class_name)
    db_session.add(class_group)
    db_session.flush()
    students: list[Student] = []
    for index, level in enumerate(levels):
        student = Student(
            student_no=f"{prefix}{index:02d}",
            name=f"{class_name}第{index}人",
            masked_name="第**",
            school_id=grade.school_id,
            grade_id=grade.id,
            class_id=class_group.id,
        )
        db_session.add(student)
        db_session.flush()
        make_target(
            db_session, student, task,
            status="COMPLETED",
            completed_at=datetime.now(UTC),
            grade_name_snapshot=grade.name,
            class_name_snapshot=class_name,
        )
        session = make_sitting(
            db_session, student,
            task_id=task.id,
            submitted_at=datetime.now(UTC) - timedelta(days=1),
            calculation_status="CALCULATED",
        )
        db_session.add(
            AssessmentResult(
                session_id=session.id,
                validity_score=0,
                validity_status="VALID",
                # 分数只是陪衬：报表读的是 `total_level`，它由 `_level_distribution` 数。
                total_score=70,
                total_level=level,
                rule_version=rule_version,
            )
        )
        db_session.flush()
        students.append(student)
    return students


def test_each_grade_reports_its_own_level_distribution(client, db_session):
    """每个年级一份自己的三档，且**全校 == 各年级逐档相加**。

    两个组的规模刻意不同（初一 1 人 / 初二 6 人）：一个「忘了按组过滤学生」的实现
    会让初二数出 7 个人，一个「整组共用全校数据」的实现会让两个组长得一模一样。
    """
    task, rule_version, baseline_level = _baseline(client, db_session)
    # 基线那个人落哪一档**由规则的分段决定**（§6），所以从结果行上读、不写死：
    # 写死一个档位就是给「分数 → 档位」这条映射在测试里留了第二份定义。
    assert baseline_level in BAND_CODES, "基线结果落在这三档之外，下面的推导会静默变成全 0"
    _make_class(
        db_session, task, _make_grade(db_session, _roster_school(db_session), "初二"),
        class_name="2班", levels=[NORMAL, NORMAL, NORMAL, ATTENTION, ATTENTION, KEY],
        rule_version=rule_version, prefix="C2",
    )
    db_session.commit()

    data = _report(client)
    by_grade = {g["grade_name"]: g for g in data["grades"]}
    assert set(by_grade) == {"初一", "初二"}

    for group in data["grades"]:
        # 次序是从轻到重，三档永远都在（人少的档也出 0，不是少一行）。
        assert _report_codes(group["level_distribution"]) == BAND_CODES
        # 三档之和 == **这一组自己**的可评价样本：图上「合计 N 人」与旁边
        # 「可评价样本 N」是同一个数，不是两次各算一遍（§11）。
        assert sum(_report_counts(group["level_distribution"])) == group["sample_count"]

    # 初一只有基线那 1 个人，且他就落在结果行说的那一档上——**只有这一格不是 1**。
    assert by_grade["初一"]["sample_count"] == 1
    assert _report_counts(by_grade["初一"]["level_distribution"]) == [
        1 if code == baseline_level else 0 for code in BAND_CODES
    ]
    assert by_grade["初二"]["sample_count"] == 6
    assert _report_counts(by_grade["初二"]["level_distribution"]) == [3, 2, 1]

    overview = data["overview"]["level_distribution"]
    assert _report_codes(overview) == BAND_CODES
    for index in range(len(BAND_CODES)):
        assert overview[index]["student_count"] == sum(
            g["level_distribution"][index]["student_count"] for g in data["grades"]
        )
    assert sum(_report_counts(overview)) == 7
    assert sum(_report_counts(overview)) == data["sample_quality"]["n_evaluable"]


def test_each_class_reports_its_own_level_distribution(client, db_session):
    """班级那一份也是自己算的，且**年级 == 本年级各班之和**。

    这一个比「全校 == 各年级之和」多一层：初二下有两个班，而一个「按年级分组、
    却把整个年级的数据发给每个班」的实现会让两个班**长得一模一样**。所以两个班
    的人数（3 / 5）与三档取值都不同，年级那一份对成两者相加。
    """
    task, rule_version, _ = _baseline(client, db_session)
    grade = _make_grade(db_session, _roster_school(db_session), "初二")
    _make_class(
        db_session, task, grade, class_name="2班",
        levels=[NORMAL, NORMAL, ATTENTION],
        rule_version=rule_version, prefix="C2",
    )
    _make_class(
        db_session, task, grade, class_name="3班",
        levels=[NORMAL, ATTENTION, ATTENTION, KEY, KEY],
        rule_version=rule_version, prefix="C3",
    )
    db_session.commit()

    data = _report(client)
    by_class = {(c["grade_name"], c["class_name"]): c for c in data["classes"]}
    assert set(by_class) == {("初一", "1班"), ("初二", "2班"), ("初二", "3班")}

    for group in data["classes"]:
        assert _report_codes(group["level_distribution"]) == BAND_CODES
        assert sum(_report_counts(group["level_distribution"])) == group["sample_count"]

    assert by_class[("初一", "1班")]["sample_count"] == 1
    assert _report_counts(by_class[("初二", "2班")]["level_distribution"]) == [2, 1, 0]
    assert _report_counts(by_class[("初二", "3班")]["level_distribution"]) == [1, 2, 2]

    by_grade = {g["grade_name"]: g for g in data["grades"]}
    # 年级那一份要等于**本年级各班之和**（3 + 5），而不是「班级个数」那一类凑出来的数。
    assert by_grade["初二"]["sample_count"] == 3 + 5
    for index in range(len(BAND_CODES)):
        assert by_grade["初二"]["level_distribution"][index]["student_count"] == (
            by_class[("初二", "2班")]["level_distribution"][index]["student_count"]
            + by_class[("初二", "3班")]["level_distribution"][index]["student_count"]
        )
    assert _report_counts(by_grade["初二"]["level_distribution"]) == [3, 3, 2]


def test_a_cohort_is_measured_against_its_own_denominator(client, db_session):
    """组的百分比用的是**这一组自己**的分母，不是全校的。

    这三条里只有这一条能抓住「分母串了口径」：**先得有一个比全校小的组，那两个数
    才不同**。基线只有 1 名学生时，「率 = 人数 ÷ 分母」在错实现上照样成立，因为
    「本组分母」与「全校分母」就是同一个数。
    """
    task, rule_version, _ = _baseline(client, db_session)
    _make_class(
        db_session, task, _make_grade(db_session, _roster_school(db_session), "初二"),
        class_name="2班", levels=[NORMAL, NORMAL, NORMAL, ATTENTION, ATTENTION, KEY],
        rule_version=rule_version, prefix="C2",
    )
    db_session.commit()

    data = _report(client)
    by_grade = {g["grade_name"]: g for g in data["grades"]}

    # 初一 1 人：这一组自己的分母太小，所以**只收比率不收计数**（§11）。
    assert by_grade["初一"]["sample_count"] < MIN_COHORT_FOR_AGGREGATE
    assert any(row["student_count"] > 0 for row in by_grade["初一"]["level_distribution"])
    for row in by_grade["初一"]["level_distribution"]:
        assert row["rate"] is None

    # 初二 6 人：够大了，所以它**有**比率——而分母必须是 6。
    counts = _report_counts(by_grade["初二"]["level_distribution"])
    assert counts == [3, 2, 1]
    rates = [row["rate"] for row in by_grade["初二"]["level_distribution"]]
    assert rates == [50.0, 33.3, 16.7]
    # 若分母串成了全校那 7 个人，这三个数会是 42.9 / 28.6 / 14.3——**看起来一样正常**，
    # 所以上面那一句断的是具体数值，不是「有数就行」。

    # 两个口径在屏幕上确实不同：全校那一份此刻也是有比率的（7 ≥ 最小样本量），
    # 而它与初二的三个数不一样——「本组」与「全校」不是同一个东西的两种写法。
    overview_rates = [row["rate"] for row in data["overview"]["level_distribution"]]
    assert None not in overview_rates
    assert overview_rates != rates
