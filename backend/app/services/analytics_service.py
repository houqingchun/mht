import csv
import io

from datetime import UTC, datetime, timedelta

from collections import defaultdict

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    DimensionResult,
    RiskEvent,
    effective_session_predicate,
    active_task_predicate,
    PARTICIPATION_REQUIRED,
)
from app.models.care import FollowUpRecord, RetestPlan, StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import rule_config_from_json
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.services.assessment_service import latest_session, latest_session_order
from app.services.export_document import ExportDocument
# 导出侧的中文一律走 `export_labels` 这份 `labels.ts` 的镜像（§3 第三面）：这一份 CSV 的
# 性别 / 来源 / 学籍状态三列在服务端拼出来，界面上那三列是 `labels.ts` 出的中文，两处
# 各写一份就会漂，而漂了没有任何东西看得见（`test_export_labels_match_frontend.py` 就是
# 为此把那份 TypeScript 当数据源读进来逐字比对的）。
from app.services import export_labels
# 规则类型不在这里再写一份字面量：`MHT_SCORING` 的出处是 `scale_rule_service`，抄一遍
# 就会有两份会各说各话的常量（§「同一处只许有一个定义」）。
from app.services.scale_rule_service import RULE_TYPE
from app.security.permissions import (
    AGGREGATE_STATS,
    MANAGE,
    ORG_ACCOUNT,
    READ_BASIC,
    SCHOOL,
    SCOPED,
    STUDENT_PSYCH_DETAIL,
    scope_allows,
)

# 关注等级的两种口径在这里各写一次，别处引用：总量里数「人」，分段里数「哪一档」。
ATTENTION_LEVELS = ("NEEDS_ATTENTION", "KEY_ATTENTION")
LEVEL_BANDS = ("GENERAL_RANGE", "NEEDS_ATTENTION", "KEY_ATTENTION")

# 分母小于这个人数时不下发**比率与均值**，只说「样本过小」。
#
# 一个只覆盖三四个学生的范围，任何一个百分比都是在**点那个学生的名**：33% 就是
# 「三人里有一个」，读者不需要费什么劲就能反推出来（CLAUDE.md 已知缺口 1 记着
# 同一条风险）。均值同理，甚至更容易反推——均值本身就是一串分数。当前场景下整班整年级
# 的范围远在这条线之上，所以它平时不响；它是为「范围被切到个别学生」那一天准备的——
# 那一天不给数字比给一个可反推的数字更诚实。
# **计数不受这一条限制**：学校要能回答「我这几个学生里有几个需要关注」。
MIN_COHORT_FOR_AGGREGATE = 5

# 工作台「近期提醒」每个来源最多下发多少条。
#
# 这是**保护 payload 的上限**，不是工作量的上限：一个老师在普查周里可能有几百条
# 待办跟进，把几百条塞进一屏时间线既读不了也传不动。所以上限留着，但**必须让读者
# 知道自己看到的是被截断过的一批**——`counselor_reminders` 因此连 `total` 一起回，
# 面板据此说「另有 N 项未显示」。没有这个数，徽标上的「20 项」在 20 与 400 两种
# 情况下长得一模一样（2026-09-17 修）。
REMINDER_LIMIT = 20

ANALYSIS_MODES = {"ALL_CALCULATED", "VALIDITY_UNFLAGGED"}


def ensure_leader_or_counselor(db: Session, user: UserAccount) -> None:
    """Aggregate reads delegate to the capability matrix (see care_service)."""
    if not scope_allows(db, user.role_code, AGGREGATE_STATS, allow={SCOPED, SCHOOL}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def latest_result_subquery(db: Session, user: UserAccount):
    """每人**最近一场**会话的评分结果，是本模块所有「当前状态」口径的唯一来源。

    为什么必须按人取最近一场，而不是把所有 `assessment_result` 加起来（2026-09-17 改）：

    - 旧口径跨场次重复计数。一个学生测过两次、两次都需关注，分子进两次、分母也进两次；
      而「有多少学生需要关注」问的是人，不是次数。
    - 更糟的是它**只增不减**。分子取的是全部历史结果，一个学生去年落在重点关注、今年
      回到一般范围，那条旧结果永远留在分子里。这个数字于是随时间单调上升，谁都解释不了它。
      筛查系统要回答的是「**现在**有多少人需要关注」。
    - 改完之后它与个案详情、重点学生列表、工作台同口径——那些地方一直按「最近一场」取，
      同一所学校在两个页面上看到两个不同的人数，比数字偏大更伤。

    排序复用 `latest_session_order`（施测时间优先，id 兜底），不是 `max(id)`：导入的历史
    普查 `id` 更大，按 id 取会把去年那场当成「本次」。

    **被降级的那一场（`is_effective = 0`）不参与排名**（`effective_session_predicate`，
    §18.8）：一名学生的在线答卷被外部结果顶掉之后，他「现在是什么状态」应以外部分数为准，
    而如果两场都参与排名，取到哪一场就取决于文件里那个测评日期与在线交卷时刻谁更晚——
    那正是这个子查询存在的意义所在（一个口径，不是两个）。窗口函数是按分区排名、过滤要
    在 `WHERE` 里，所以谓词加在下面的 `.where(...)` 上，不能并进 `order_by`。

    会话与结果**内连接**：一场都没交卷的学生没有结果，也就没有等级可数。他由此落在
    `assessed_count` 之外而不是被算成「一般范围」——「还没测」和「测了没事」不是一回事。

    **他更早有一场已交卷时，取的就是那一场。** 这一条此前写反过（2026-09-17 用探针
    跑出来才改）：`latest_session_order()` 把 `submitted_at IS NULL` 排在**后面**，
    所以「最近一场」是最近一次**交过卷**的，而不是「最近开过卷子的」。
    一个学生在普查周里刚打开卷子、还没交，这一页给他的仍是他上一次的等级。
    那不是将就，是对的那一个：若他在交卷前从「已测评」里掉出去，学校的关注人数与
    分母会在整个考试周里随每个人的开卷动作上下跳，而「他上一次需要关注」至少是
    已经发生过的、可复核的事实。（一场都没交过的学生仍然没有等级，见上一条。）
    """
    ranked = (
        select(
            AssessmentSession.student_id.label("student_id"),
            AssessmentSession.id.label("session_id"),
            func.row_number()
            .over(
                partition_by=AssessmentSession.student_id,
                order_by=latest_session_order(),
            )
            .label("rank"),
        )
        .join(Student, Student.id == AssessmentSession.student_id)
        .outerjoin(AssessmentTask, AssessmentTask.id == AssessmentSession.task_id)
        .where(
            student_scope_predicate(db, user),
            effective_session_predicate(),
            # 作废那一场不参与排名（§4.14）：学校作废它正是因为「这一场不算」，
            # 而「他现在是什么状态」若取到那一场，同一名学生在别处（个案详情、
            # 关注率、导出）会得到另一个等级。外连接在这里，所以「没有任务」那一支
            # 必须留着——`NULL != 'VOIDED'` 是 NULL 而不是真。
            or_(AssessmentSession.task_id.is_(None), active_task_predicate()),
        )
        .subquery()
    )
    return (
        select(
            Student.id.label("student_id"),
            ranked.c.session_id.label("session_id"),
            Student.grade_id.label("grade_id"),
            Student.class_id.label("class_id"),
            AssessmentResult.total_level.label("total_level"),
        )
        .join(ranked, ranked.c.student_id == Student.id)
        .join(AssessmentResult, AssessmentResult.session_id == ranked.c.session_id)
        .where(ranked.c.rank == 1)
        .subquery()
    )


def rate_or_none(part: int, whole: int) -> int | None:
    """比率，或 `None` 表示「样本过小，不给比率」。见 `MIN_COHORT_FOR_AGGREGATE`。

    0 与 None 在这里是两件事：0 是「一个都没有」，None 是「这几个人算出来不足为凭」。
    合成一个值会让界面把后者也印成 0%，那就成了一个可反推的断言。
    """
    if whole <= 0 or whole < MIN_COHORT_FOR_AGGREGATE:
        return None
    return round(part / whole * 100)


def _report_rate(part: int, whole: int) -> float | None:
    """报表中心使用一位小数；小样本与零分母均不发布比率。"""
    if whole < MIN_COHORT_FOR_AGGREGATE:
        return None
    return round(part / whole * 100, 1)


def _level_distribution(counts: dict[str, int], total: int) -> list[dict]:
    """三档关注等级的计数与占比 ——**全报表唯一的这一处定义**（全校与每个年级/班级共用）。

    计数**按人**，所以三个数加起来恰好等于 `total`（§11 那句「按人取最近一场」）。
    与「按场」的完成率不是一回事：一个学生参加了两次普查，在这里只落一档。

    三档**恒发**，一个人都没有的那一档发 `0` 而不是省掉：`0` 是「这一档没人」，
    是一句完整的话（§11：计数不受小样本限制），而少一根柱子会让图上那三档的形状
    随数据变形，读者看不出「本来有几档」。键序取自 `LEVEL_BANDS`（**从轻到重**），
    认不出的码按字典序排在最后、仍然发出来：漏一个码要看得见（同 §3 的 `labelOf`）。

    占比在分母小于 `MIN_COHORT_FOR_AGGREGATE` 时是 `None` 而不是 `0`：`0%` 是一句
    「这一档一个都没有」的断言，而三四个人的分母算出来的百分比是**反推**（§11）。
    """
    order = list(LEVEL_BANDS)
    order += sorted(code for code in counts if code not in LEVEL_BANDS)
    return [
        {
            "level_code": code,
            "student_count": counts.get(code, 0),
            "rate": _report_rate(counts.get(code, 0), total),
        }
        for code in order
    ]


def _report_total_bands(db: Session, scale_id: int, rule_versions: list[str]) -> list[dict] | None:
    """总分分段的**区间出处**：把阈值随响应发下来，让视图只负责渲染。

    报表上要写「正常（1~55 分）」这类话，而 `1` 与 `55` 是**阈值**不是文案。把区间写在
    视图里就成了一份与 `scale_rule.config_json` 平行的第二定义：改规则时它不会跟着动，
    而屏幕上看不出这件事（CLAUDE.md §6：阈值属于量表规则版本，不属于界面）。

    取的是**这批结果自己的**规则版本那一行，不是当前 ACTIVE 那一行——两者可以不同
    （改过已发布规则之后，老结果仍指着旧版本，`rule_version` 就是为这件事存的）。拿新
    版本的区间去标注旧结果算出来的分档，正好是 §6 要防的那件事。

    三个「不发」的分支，各自都在说「不知道」，都不猜一个默认值：

    * 这批结果横跨多个规则版本 —— 它们本来就没有**一套**阈值（`interpretation_warnings`
      里那句「跨版本比较需谨慎解释」说的就是它）；
    * 那一行查不到 —— 规则行被清理过，或版本号早于本库；
    * 那一行解析不出分段 —— 与「三个区间」在界面上必须长得不一样（§11 那条 `None ≠ 0`）。
    """
    if len(rule_versions) != 1:
        return None
    rule = db.scalar(
        select(ScaleRule).where(
            ScaleRule.scale_id == scale_id,
            ScaleRule.rule_type == RULE_TYPE,
            ScaleRule.rule_version == rule_versions[0],
        )
    )
    if rule is None:
        return None
    bands = rule_config_from_json(rule.config_json).total_bands
    if not bands:
        return None
    return [{"code": band.code, "min": band.min, "max": band.max} for band in bands]


def _dimension_report(rows: list, item_counts: dict[str, int]) -> list[dict]:
    """把一组维度结果聚合成可发布指标；被抑制的**均值与比率**不会进入响应，
    但**分布形状**始终返回——即使 cohort 太小，读者仍该看到「这一维度的分数
    大致落在哪里」，只是不给精确百分比（那会暴露个别学生）。"""
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row.dimension_code].append(row)

    items = []
    for code in sorted(item_counts):
        values = grouped.get(code, [])
        size = len(values)
        suppressed = 0 < size < MIN_COHORT_FOR_AGGREGATE
        levels = {level: 0 for level in ("LOW", "MEDIUM", "HIGH")}
        for row in values:
            levels[row.level] = levels.get(row.level, 0) + 1
        high = levels.get("HIGH", 0)
        items.append(
            {
                "dimension_code": code,
                "score_min": 0,
                "score_max": int(item_counts.get(code) or 0),
                "score_direction": "HIGHER_MORE_CONCERN",
                "n_evaluable": size,
                "n_excluded": 0,
                "mean_score": (
                    round(sum(row.score for row in values) / size, 1)
                    if size and not suppressed
                    else None
                ),
                # 分布形状始终返回——即使被抑制，读者仍该看到分数落在各区间的人数。
                # 比率在小样本时给 null（不暴露可反推的百分比）。
                "distribution": (
                    [
                        {
                            "range_code": level,
                            "count": count,
                            "rate": _report_rate(count, size) if not suppressed else None,
                        }
                        for level, count in levels.items()
                    ]
                    if size
                    else []
                ),
                "high_score_count": high if not suppressed else None,
                "high_score_rate": _report_rate(high, size),
                # 当前正式规则只有通用 HIGH_DIMENSION_SCORE，没有按维度独立的
                # 筛查规则配置，因此不能把高分区间换个名字冒充筛查信号。
                "screening_rule_available": False,
                "screening_count": None,
                "screening_rate": None,
                "suppression": {
                    "suppressed": suppressed,
                    "reason": "MIN_COHORT" if suppressed else None,
                },
            }
        )
    return items


def _is_validity_flagged(result: AssessmentResult) -> bool:
    """这一条结果算不算「效度建议复测」。

    判据只有这一处：报表的 KPI 计数（`analytics_report` 的 `validity_flagged`）与效度复测
    名单导出（`validity_retest_students`）**都调它**。各写一份 `!= "VALID"` 的话，
    「页面上写着 12 人、导出的文件里 13 行」这种不一致不会有任何东西看得见——两个数各自
    都是对的，只是回答的不是同一个问题（§11）。

    写 `!= "VALID"` 而不是 `== "RETEST_RECOMMENDED"`：`validity_status` 今天的取值域是
    `{VALID, RETEST_RECOMMENDED}`（`scale_engine.validity_status` 只回这两个），但
    `QUESTIONABLE` 是词汇表里**已经留好**的一档。按「不是 VALID」写，将来引擎真的产出
    第三档时它自动落进这一边；按「等于 RETEST_RECOMMENDED」写则会静默漏掉——而那一档
    在界面上看起来只会是「人数变少了」。
    """
    return result.validity_status != "VALID"


def _report_scope(
    db: Session,
    user: UserAccount,
    task_id: int | None,
    task_ids: list[int] | None,
) -> tuple[list[AssessmentTask], list[AssessmentTarget], list[tuple]]:
    """报表中心与「效度复测名单」导出**共用**的那一段取数。

    返回 `(tasks, targets, calculated)`：

    - `tasks` 是按读者数据范围解析出来的任务，`tasks[0]` 是主任务（量表、总分分段区间、
      任务名都取它）；
    - `targets` 是**每名学生一条**的目标行快照，被真正入选的那一场刷新过；
    - `calculated` 是每名学生**最近一场**「已计算且当前有效」的 `(result, session)`。

    抽出来是因为效度复测名单（`validity_retest_students`）与报表必须从**同一个集合**
    出发：页面上写着「效度建议复测 12 人」、导出的文件里 13 行，这种不一致不会有任何
    东西看得见（§11：指标卡上的数必须与它点进去的那个列表同源）。各写一份取数语句就是
    两个定义，而它们漂移了只会在某次对账时才暴露出来。

    **这里不做权限判断**，与它抽出来之前一样：调用方各自负责
    （`analytics_report` 调 `ensure_leader_or_counselor`，导出那一侧另有能力门槛）。
    搬进来会让「谁读得到」这个问题有两个答案。
    """
    task_stmt = (
        select(AssessmentTask)
        .join(AssessmentTarget, AssessmentTarget.task_id == AssessmentTask.id)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(student_scope_predicate(db, user), active_task_predicate())
        .distinct()
        .order_by(AssessmentTask.created_at.desc(), AssessmentTask.id.desc())
    )
    requested_ids = list(dict.fromkeys(task_ids or ([] if task_id is None else [task_id])))
    if requested_ids:
        task_stmt = task_stmt.where(AssessmentTask.id.in_(requested_ids))
    tasks = list(db.scalars(task_stmt).unique())
    if not tasks:
        raise AppError("NOT_FOUND", "测评任务不存在或无权访问", 404)
    if requested_ids and {task.id for task in tasks} != set(requested_ids):
        raise AppError("NOT_FOUND", "部分测评任务不存在或无权访问", 404)
    if not requested_ids:
        tasks = tasks[:1]
    scale_ids = {task.scale_id for task in tasks}
    if len(scale_ids) != 1:
        raise AppError("VALIDATION_ERROR", "不同量表的测评任务不能合并分析", 422)
    selected_task_ids = {task.id for task in tasks}

    target_rows = db.execute(
        select(AssessmentTarget, Student)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(
            AssessmentTarget.task_id.in_(selected_task_ids),
            student_scope_predicate(db, user),
        )
    ).all()
    targets_by_task_student = {
        (target.task_id, target.student_id): target for target, _ in target_rows
    }
    task_order = {task.id: index for index, task in enumerate(reversed(tasks))}
    target_by_student: dict[int, AssessmentTarget] = {}
    for target, _ in target_rows:
        existing = target_by_student.get(target.student_id)
        if existing is None or task_order[target.task_id] > task_order[existing.task_id]:
            target_by_student[target.student_id] = target
    targets = list(target_by_student.values())
    student_ids = list(target_by_student)

    result_rows = []
    if student_ids:
        result_rows = db.execute(
            select(AssessmentResult, AssessmentSession)
            .join(AssessmentSession, AssessmentSession.id == AssessmentResult.session_id)
            .where(
                AssessmentSession.task_id.in_(selected_task_ids),
                AssessmentSession.student_id.in_(student_ids),
                AssessmentSession.calculation_status == "CALCULATED",
                effective_session_predicate(),
            )
        ).all()
    latest_calculated_by_student: dict[int, tuple] = {}
    for result, session in result_rows:
        existing = latest_calculated_by_student.get(session.student_id)
        current_key = (session.submitted_at is not None, session.submitted_at or session.created_at, session.id)
        if existing is None:
            latest_calculated_by_student[session.student_id] = (result, session)
            continue
        old_session = existing[1]
        old_key = (old_session.submitted_at is not None, old_session.submitted_at or old_session.created_at, old_session.id)
        if current_key > old_key:
            latest_calculated_by_student[session.student_id] = (result, session)
    calculated = list(latest_calculated_by_student.values())
    # 分组快照应与真正入选的最新结果属于同一批；无结果的学生才回退到最新任务目标。
    for _, session in calculated:
        session_target = targets_by_task_student.get((session.task_id, session.student_id))
        if session_target is not None:
            target_by_student[session.student_id] = session_target
    targets = list(target_by_student.values())
    return tasks, targets, calculated


def analytics_report(
    db: Session,
    user: UserAccount,
    task_id: int | None,
    analysis_mode: str = "ALL_CALCULATED",
    task_ids: list[int] | None = None,
) -> dict:
    """报表中心统一快照；多任务时按学生去重并保留最新一次计算结果。"""
    ensure_leader_or_counselor(db, user)
    if analysis_mode not in ANALYSIS_MODES:
        raise AppError("VALIDATION_ERROR", "不支持的统计模式", 422)

    tasks, targets, calculated = _report_scope(db, user, task_id, task_ids)
    primary_task = tasks[0]
    included = [
        (result, session)
        for result, session in calculated
        if analysis_mode == "ALL_CALCULATED" or result.validity_status != "RETEST_RECOMMENDED"
    ]
    included_session_ids = [session.id for _, session in included]
    included_student_ids = {session.student_id for _, session in included}

    dimension_rows = (
        db.scalars(
            select(DimensionResult).where(DimensionResult.session_id.in_(included_session_ids))
        ).all()
        if included_session_ids
        else []
    )
    dimensions_by_session: dict[int, list] = defaultdict(list)
    for row in dimension_rows:
        dimensions_by_session[row.session_id].append(row)

    risk_rows = (
        db.scalars(select(RiskEvent).where(RiskEvent.session_id.in_(included_session_ids))).all()
        if included_session_ids
        else []
    )
    signals_by_type: dict[str, set[int]] = defaultdict(set)
    any_signal_students: set[int] = set()
    pending_review = completed_review = 0
    for event in risk_rows:
        signals_by_type[event.signal_type].add(event.student_id)
        any_signal_students.add(event.student_id)
        if event.requires_manual_review:
            if event.status == "PENDING":
                pending_review += 1
            else:
                completed_review += 1

    item_counts = _dimension_item_counts(db, primary_task.scale_id)
    all_dimensions = _dimension_report(dimension_rows, item_counts)
    target_count = len(targets)
    eligible_count = sum(t.participation_disposition == PARTICIPATION_REQUIRED for t in targets)
    completed_count = len(calculated)
    validity_flagged = sum(_is_validity_flagged(result) for result, _ in calculated)
    validity_unflagged = completed_count - validity_flagged
    sample_count = len(included_student_ids)

    def group_payload(kind: str) -> list[dict]:
        grouped_targets: dict[tuple, list] = defaultdict(list)
        for target in targets:
            if kind == "grade":
                key = (target.grade_name_snapshot or "未分年级",)
            else:
                key = (
                    target.grade_name_snapshot or "未分年级",
                    target.class_name_snapshot or "未分班级",
                )
            grouped_targets[key].append(target)

        output = []
        for key, group_targets in sorted(grouped_targets.items()):
            group_student_ids = {target.student_id for target in group_targets}
            group_session_ids = {
                session.id for _, session in included if session.student_id in group_student_ids
            }
            group_dimension_rows = [
                row for row in dimension_rows if row.session_id in group_session_ids
            ]
            eligible = sum(
                target.participation_disposition == PARTICIPATION_REQUIRED
                for target in group_targets
            )
            calculated_student_ids = {session.student_id for _, session in calculated}
            completed = sum(target.student_id in calculated_student_ids for target in group_targets)
            # 这一组的关注等级分布：与全校那一份**同一个函数**算出来（`_level_distribution`），
            # 只是集合换成了「本组的学生」。分母刻意取 `len(group_session_ids)` —— 它同时也是
            # 上面那个 `sample_count`，所以图上那句「合计 N 人」与本行「可评价样本 N」
            # **构造上不可能各说各话**（§11：指标卡上的数必须与它点进去的那个列表同源）。
            group_level_counts: dict[str, int] = defaultdict(int)
            for result, session in included:
                if session.student_id in group_student_ids:
                    group_level_counts[result.total_level] += 1
            payload = {
                "grade_name": key[0],
                "class_name": key[1] if kind == "class" else None,
                "target_count": len(group_targets),
                "eligible_count": eligible,
                "completed_count": completed,
                "sample_count": len(group_session_ids),
                "coverage_rate": _report_rate(len(group_session_ids), eligible),
                "level_distribution": _level_distribution(
                    group_level_counts, len(group_session_ids)
                ),
                "dimensions": _dimension_report(group_dimension_rows, item_counts),
            }
            output.append(payload)
        return output

    scale = db.get(AssessmentScale, primary_task.scale_id)
    rule_versions = sorted({result.rule_version for result, _ in included})
    # 全校的三档分布。算法与每个年级 / 每个班级那几份**同一处**（`_level_distribution`），
    # 所以全校那一份恒等于各组之和——四个数各自漂移这件事在构造上不会发生。
    level_counts: dict[str, int] = defaultdict(int)
    for result, _ in included:
        level_counts[result.total_level] += 1
    warnings = []
    if len(rule_versions) > 1:
        warnings.append("当前任务包含多个评分规则版本，跨版本比较需谨慎解释。")
    if validity_flagged:
        warnings.append("部分结果触发效度复测建议；效度提示不等同技术计算失败。")

    return {
        "report_id": "ANALYTICS_P0",
        "task": {
            "id": primary_task.id,
            "name": primary_task.name if len(tasks) == 1 else f"{len(tasks)}个任务合并分析",
        },
        "tasks": [{"id": task.id, "name": task.name} for task in tasks],
        "as_of": datetime.now(UTC).isoformat(),
        "timezone": "Asia/Shanghai",
        "scale": {
            "code": scale.code if scale else None,
            "version": scale.version if scale else None,
            "rule_versions": rule_versions,
            # 区间随规则版本走；取不到就是 None（「本次不展示区间」），不编一个默认区间。
            "total_bands": _report_total_bands(db, primary_task.scale_id, rule_versions),
        },
        "analysis_mode": analysis_mode,
        "sample_quality": {
            "target_count": target_count,
            "eligible_count": eligible_count,
            "completed_count": completed_count,
            "validity_unflagged_count": validity_unflagged,
            "validity_flagged_count": validity_flagged,
            "n_evaluable": sample_count,
            "coverage_rate": _report_rate(sample_count, eligible_count),
        },
        "overview": {
            "sample_count": sample_count,
            "signal_student_count": len(any_signal_students),
            "signal_rate": _report_rate(len(any_signal_students), sample_count),
            "pending_review_work_items": pending_review,
            "completed_review_work_items": completed_review,
            "signal_type_stats": [
                {"signal_type": code, "student_count": len(student_ids)}
                for code, student_ids in sorted(signals_by_type.items())
            ],
            "level_distribution": _level_distribution(level_counts, sample_count),
        },
        "dimensions": all_dimensions,
        "grades": group_payload("grade"),
        "classes": group_payload("class"),
        "interpretation_warnings": warnings,
        "permissions": {
            "can_drill_down_aggregate": True,
            "can_open_student_detail": user.role_code == RoleCode.COUNSELOR,
            "can_export": False,
        },
    }


def analytics_overview(db: Session, user: UserAccount) -> dict:
    ensure_leader_or_counselor(db, user)
    # Every figure is narrowed to the caller's scope, so a counselor's 完成率
    # describes their own students while a leader's describes the school. That is
    # the distinction the capability matrix already draws between SCOPED and
    # SCHOOL — it just was not implemented. The UI labels state which is which,
    # because a scoped number wearing a school-wide label is a lie.
    scope = student_scope_predicate(db, user)
    # 作废的任务不计入完成率（§4.12）。这两条都是**内连接**，判据摆在 WHERE 里就对。
    # 少了它，一场作废的普查会永远留在分子与分母上：学校作废它正是因为「这一场不算」，
    # 而报表照旧把它算进去，屏幕上只是一个偏低的完成率。
    live_task = active_task_predicate()
    total_targets = (
        db.scalar(
            select(func.count(AssessmentTarget.id))
            .join(Student, Student.id == AssessmentTarget.student_id)
            .join(AssessmentTask, AssessmentTask.id == AssessmentTarget.task_id)
            .where(scope, live_task)
        )
        or 0
    )
    completed_targets = (
        db.scalar(
            select(func.count(AssessmentTarget.id))
            .join(Student, Student.id == AssessmentTarget.student_id)
            .join(AssessmentTask, AssessmentTask.id == AssessmentTarget.task_id)
            .where(AssessmentTarget.status == "COMPLETED", scope, live_task)
        )
        or 0
    )
    completion_rate = round(completed_targets / total_targets * 100) if total_targets else 0

    case_counts = dict(
        db.execute(
            select(StudentCareCase.status, func.count(StudentCareCase.id))
            .join(Student, Student.id == StudentCareCase.student_id)
            .where(scope)
            .group_by(StudentCareCase.status)
        ).all()
    )

    latest = latest_result_subquery(db, user)
    band_rows = db.execute(
        select(latest.c.total_level, func.count(latest.c.student_id)).group_by(
            latest.c.total_level
        )
    ).all()
    bands = {level: int(count or 0) for level, count in band_rows}
    assessed_count = sum(bands.values())
    attention_count = sum(bands.get(level, 0) for level in ATTENTION_LEVELS)

    planned_retests = (
        db.scalar(
            select(func.count(RetestPlan.id))
            .join(Student, Student.id == RetestPlan.student_id)
            .where(RetestPlan.status == "PLANNED", scope)
        )
        or 0
    )
    return {
        "total_targets": total_targets,
        "completed_targets": completed_targets,
        "completion_rate": completion_rate,
        # 分母：**已测评人数**，不再是有多少条结果。见 `latest_result_subquery`。
        "assessed_count": assessed_count,
        "attention_count": attention_count,
        "attention_rate": rate_or_none(attention_count, assessed_count),
        # 三档分开下发，不只给一个「需关注」总数：一般 / 需关注 / 重点关注的比例，
        # 才是学校拿来安排资源的那个形状——1 个重点关注和 40 个需关注要的处置不同。
        "level_counts": {level: bands.get(level, 0) for level in LEVEL_BANDS},
        "key_attention_count": bands.get("KEY_ATTENTION", 0),
        "case_status_counts": case_counts,
        "planned_retests": planned_retests,
    }


def attention_by_group(db: Session, user: UserAccount, group_field: str) -> dict:
    """每个年级 / 每个班级的关注人数与分档，按人算。

    单独一条查询、在 Python 里并进完成情况，而不是把那两个 join 并排塞进一条 SQL：
    一条学生同时挂着多个任务目标行，再 join 上最近结果就是**笛卡尔积**，
    `count(AssessmentTarget.id)` 会被结果那边复制一遍；改成 `count(distinct)` 能压住，
    但下一个往这条查询里加列的人不会知道。这里是两个各自平凡的查询，加起来也只扫一遍。

    `group_field` 传的是列名（`grade_id` / `class_id`）而不是列对象：传对象就得由调用方
    先造一个子查询、再把它的某一列递进来，而这里还要再造一个自己的，同一段窗口函数
    于是每次请求算两遍。
    """
    latest = latest_result_subquery(db, user)
    group_column = latest.c[group_field]
    rows = db.execute(
        select(
            group_column,
            func.count(latest.c.student_id),
            func.sum(case((latest.c.total_level.in_(ATTENTION_LEVELS), 1), else_=0)),
            func.sum(case((latest.c.total_level == "KEY_ATTENTION", 1), else_=0)),
        ).group_by(group_column)
    ).all()
    return {
        group_id: {
            "assessed_count": int(assessed or 0),
            "attention_count": int(attention or 0),
            "key_attention_count": int(key or 0),
        }
        for group_id, assessed, attention, key in rows
    }


def _cohort_fields(counts: dict | None) -> dict:
    """把 `attention_by_group` 的一格翻成下发给界面的字段。

    没有这一格（这个年级在这个范围里一个学生都没测）时给 0 与 `None`，
    而不是把这一格整个省掉——省掉的话前端 `row.attention_rate` 是 `undefined`，
    模板里 `{{ }}` 会印成空字符串，那一格看着像"忘了渲染"。
    """
    counts = counts or {"assessed_count": 0, "attention_count": 0, "key_attention_count": 0}
    assessed = counts["assessed_count"]
    return {
        **counts,
        # None = 样本过小，界面写「样本过小」而不是 0% —— 见 `rate_or_none`。
        "attention_rate": rate_or_none(counts["attention_count"], assessed),
        "cohort_too_small": 0 < assessed < MIN_COHORT_FOR_AGGREGATE,
    }


def _live_target_on_clause():
    """外连接 `assessment_target` 时的 ON 子句：只认**没被作废**那些任务的目标行（§4.12）。

    年级 / 班级这两张表把目标行**外连接**进来——「这个年级有没有人属于我的范围」
    不该因为一场任务作废而变成 0 行。所以作废的判据进 ON 子句，**不能写进 WHERE**：
    写进 WHERE 会把外连接悄悄变成内连接，把「没有目标行」的学生整片丢掉，而屏幕上
    只是少了几行，看不出是排版问题。

    用 `task_id IN (SELECT …)` 而不是再外连接一张 `assessment_task`：后者会让作废那
    一场的目标行**仍然留在结果里**（`count(AssessmentTarget.id)` 照样数得到它），要
    把下面那两列都改成 `count(AssessmentTask.id)` 才有效——而那两个表达式正是这两列
    的全部内容，改它们比改这里危险。
    """
    return and_(
        AssessmentTarget.student_id == Student.id,
        AssessmentTarget.task_id.in_(
            select(AssessmentTask.id).where(active_task_predicate())
        ),
    )


def analytics_by_grade(db: Session, user: UserAccount) -> list[dict]:
    """Per-grade rows, scoped.

    The scope predicate sits on the Student join, so a grade with no students in
    the caller's range produces no row at all — grades outside the range are
    hidden rather than shown with zeroes. That is the point: a row full of zeroes
    still tells the reader the grade exists and that they cannot see it.

    关注人数与关注率（2026-09-17 加）：这一页此前只有完成情况，于是它回答的全是
    「谁还没测」——那是教务的问题。德育领导真正要问的是「问题集中在哪」，而那个问题
    只有关注率答得上：三个年级完成率都是 100%，一个有 2 人需关注、一个有 30 人，
    这两行在旧表里长得一模一样。
    """
    ensure_leader_or_counselor(db, user)
    rows = db.execute(
        select(
            Grade.id,
            Grade.name,
            func.count(AssessmentTarget.id),
            func.sum(case((AssessmentTarget.status == "COMPLETED", 1), else_=0)),
        )
        .join(Student, Student.grade_id == Grade.id)
        # 已作废任务的目标行不算（§4.12）。判据在 ON 子句里，**不是** WHERE——
        # 这一条是外连接，「这个年级有学生但没有人被发过任务」要出 0 行而不是整行消失。
        .outerjoin(AssessmentTarget, _live_target_on_clause())
        .where(student_scope_predicate(db, user))
        .group_by(Grade.id, Grade.name)
        .order_by(Grade.sort_order, Grade.id)
    ).all()
    attention = attention_by_group(db, user, "grade_id")
    return [
        {
            "grade_id": grade_id,
            "grade": grade_name,
            "total_targets": int(total or 0),
            "completed_targets": int(completed or 0),
            "completion_rate": round((completed or 0) / total * 100) if total else 0,
            **_cohort_fields(attention.get(grade_id)),
        }
        for grade_id, grade_name, total, completed in rows
    ]


def analytics_by_class(db: Session, user: UserAccount) -> list[dict]:
    ensure_leader_or_counselor(db, user)
    rows = db.execute(
        select(
            ClassGroup.id,
            Grade.name,
            ClassGroup.name,
            func.count(AssessmentTarget.id),
            func.sum(case((AssessmentTarget.status == "COMPLETED", 1), else_=0)),
        )
        .join(Grade, Grade.id == ClassGroup.grade_id)
        .join(Student, Student.class_id == ClassGroup.id)
        # 同 `analytics_by_grade`：作废的判据进 ON 子句，写进 WHERE 会把外连接变成内连接。
        .outerjoin(AssessmentTarget, _live_target_on_clause())
        .where(student_scope_predicate(db, user))
        .group_by(ClassGroup.id, Grade.name, ClassGroup.name)
        .order_by(Grade.sort_order, ClassGroup.id)
    ).all()
    attention = attention_by_group(db, user, "class_id")
    return [
        {
            "class_id": class_id,
            "grade": grade_name,
            "class_name": class_name,
            "total_targets": int(total or 0),
            "completed_targets": int(completed or 0),
            "completion_rate": round((completed or 0) / total * 100) if total else 0,
            **_cohort_fields(attention.get(class_id)),
        }
        for class_id, grade_name, class_name, total, completed in rows
    ]


def _dimension_item_counts(db: Session, scale_id: int | None = None) -> dict[str, int]:
    """每个维度由多少道题支撑，取自现行的题库。

    这个数是**展示分数的分母**：八个维度题数不等（两个 15 题、其余 10 题），
    一个裸分是歧义的——「8」在一个维度里是 53%，在另一个里是 80%。
    """
    statement = select(ScaleQuestion.dimension_code, func.count(ScaleQuestion.id)).where(
        ScaleQuestion.dimension_code.isnot(None), ScaleQuestion.status == "ACTIVE"
    )
    if scale_id is not None:
        statement = statement.where(ScaleQuestion.scale_id == scale_id)
    return dict(db.execute(statement.group_by(ScaleQuestion.dimension_code)).all())


def class_comparison(db: Session, user: UserAccount, student_id: int) -> dict:
    """这名学生最近一场的各维度得分，与他所在**班级、年级的当前水平**对照。

    为什么加这张报表（2026-09-17，用户提出「历次趋势用的会较少，一年也就测两次」）：

    历次趋势画的是这名学生自己与自己比。可 MHT 是每学期约一次的普查，一个学生在校三年
    也不过五六场，而**第一年只有一场**——那一场画不出任何趋势，页面上是一排孤零零的点，
    结论早在开学的第一周就该有了。真正能在个案会上说出口的问题不是「他比去年高了多少」，
    而是「他相对于同龄人现在处在哪」：情绪困扰 80%，本班均值 40%，这一句不需要历史数据，
    第一场测评当天就成立。所以这张表**不替代**历次趋势（真有两三场时它仍然要说变化），
    它补的是趋势表在只有一场时交白卷的那一格。

    对照的是「同班/同年级同学各自最近一次的筛查结果」，不是「同一场测评」。两者在普查
    场景下几乎总是同一批人（同一学期同一场），而当有人补测、有人中途转入时，问「这个
    孩子相对于他们**现在**处在哪」比问「相对于那一次考试」更贴近本意。这也是全系统
    「当前状态」的统一口径——个案详情、重点学生列表、受控导出都按最近一场取。
    代价写在这里：一名学生从别校转入、带来的是外部平台的记录时，他也在对照的样本里。

    需要 `STUDENT_PSYCH_DETAIL: SCOPED`：它返回的是**一名学生**的维度得分，
    是档案正文那一级的数据，不是聚合。同等重要的是，同班均值要按调用者的范围算——
    一名只覆盖几个班的心理老师，他的「本年级均值」是本年级中他管得到的那部分学生。
    """
    if not scope_allows(db, user.role_code, STUDENT_PSYCH_DETAIL, allow={SCOPED}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    ensure_student_in_scope(db, user, student_id)

    student = db.get(Student, student_id)
    grade = db.get(Grade, student.grade_id) if student.grade_id else None
    class_group = db.get(ClassGroup, student.class_id) if student.class_id else None
    sitting = latest_session(db, student_id)
    result = (
        db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == sitting.id))
        if sitting
        else None
    )

    payload = {
        "grade_name": grade.name if grade else None,
        "class_name": class_group.name if class_group else None,
        "session_id": sitting.id if sitting else None,
        "submitted_at": (
            sitting.submitted_at.isoformat() if sitting and sitting.submitted_at else None
        ),
        "items": [],
    }
    if result is None:
        # 没有已交卷的测评就没有可对照的分数。空列表 + 这一次会话的信息，
        # 让界面能说清"还没有测评"而不是"暂无数据"。
        return payload

    latest = latest_result_subquery(db, user)

    def cohort(group_field: str, group_id: int | None) -> tuple[dict, dict]:
        if group_id is None:
            return {}, {}
        rows = db.execute(
            select(
                DimensionResult.dimension_code,
                func.avg(DimensionResult.score),
                func.count(DimensionResult.id),
            )
            .join(latest, latest.c.session_id == DimensionResult.session_id)
            .where(latest.c[group_field] == group_id)
            .group_by(DimensionResult.dimension_code)
        ).all()
        averages = {code: float(average) for code, average, _ in rows if average is not None}
        sizes = {code: int(size or 0) for code, _, size in rows}
        return averages, sizes

    class_averages, class_sizes = cohort("class_id", student.class_id)
    grade_averages, grade_sizes = cohort("grade_id", student.grade_id)
    item_counts = _dimension_item_counts(db)

    own_results = db.scalars(
        select(DimensionResult)
        .where(DimensionResult.session_id == sitting.id)
        .order_by(DimensionResult.dimension_code)
    ).all()
    for own in own_results:
        code = own.dimension_code
        class_size = class_sizes.get(code, 0)
        grade_size = grade_sizes.get(code, 0)
        payload["items"].append(
            {
                "dimension_code": code,
                "score": own.score,
                "level": own.level,
                "max_score": int(item_counts.get(code) or 0),
                # 分母太小就不下发均值。对照表比比率更容易反推：均值本身就是一串分数，
                # 三个人一平均，看的人（尤其是懂行的心理老师）能从均值倒推出个大概。
                "class_average": _average_or_none(class_averages.get(code), class_size),
                "class_size": class_size,
                "grade_average": _average_or_none(grade_averages.get(code), grade_size),
                "grade_size": grade_size,
            }
        )
    return payload


def ensure_student_result_reader(db: Session, user: UserAccount) -> None:
    """两道门槛，缺一不可——`student_result_list` 同时暴露身份列与等级列。

    身份列（学号 / 姓名 / 年级 / 班级）归「组织与账号」，等级列（关注等级 / 总分）
    归「学生心理详情」，见 §4。它比同一页上的 `GET /care-cases`（只查
    `STUDENT_PSYCH_DETAIL: SCOPED`）严，这是**有意的**：受控导出不得成为绕过心理详情的
    旁路（`export_service` 那一侧已经写下的论证），反过来也一样——**心理详情不得成为
    读组织名册的旁路**。管理员持有 `ORG_ACCOUNT: MANAGE` 而没有心理详情、德育领导持有
    名册的 `READ_SUMMARY` 而不是 `READ_BASIC`，两者都在这里被挡住：

    | | `ORG_ACCOUNT` | `STUDENT_PSYCH_DETAIL` |
    |---|---|---|
    | counselor | `READ_BASIC` ✅ | `SCOPED` ✅ |
    | leader | `READ_SUMMARY` ❌ | `SUMMARY` ❌ |
    | admin | `MANAGE` ✅ | `NONE` ❌ |

    两道都用 `scope_allows`（缺记录/读失败一律回退默认值，绝不 fail-open）。

    这个函数与 `students.py:list_student_results` 的路由依赖是**重复的两遍**，与
    `care.py` / `care_service` 同一个形状（那里也是路由声明 `PsychDetailReader`、
    服务里再 `scope_allows` 一次）。重复是**有意保留**的：路由那一遍让权限出现在
    OpenAPI 里、与其余端点同形，服务这一遍让直接调用服务的人（测试、将来的批处理）
    也拿不到越权数据。**代价要知情**：2026-09-17 实测，单独拆掉任何一遍都不会让
    `test_student_results_api.py` 变红（另一遍接着挡），两遍一起拆才会。所以那条测试
    钉的是「这两个能力是必要条件」，不是「服务里这两行在挡」。
    """
    if not scope_allows(db, user.role_code, STUDENT_PSYCH_DETAIL, allow={SCOPED}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    if not scope_allows(db, user.role_code, ORG_ACCOUNT, allow={MANAGE, READ_BASIC}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def student_result_list(db: Session, user: UserAccount) -> list[dict]:
    """名册上每一名学生 + 他**最近一场**测评的结果；未测评的学生也在列表里。

    为什么加这一页（2026-09-17，用户提出「当前没有一个视角查看所有学生的测试结果，
    只展示了重点学生和长期跟踪的展示」）：

    系统里能看见「单个学生」的四个位置——工作台、重点学生、学生档案、统计分析——
    前三个都以 `student_care_case` 为入口（`care_service.list_care_cases` 就是从
    `StudentCareCase` 出发的），第四个是聚合页、按 §11 刻意不下发学生身份。于是
    **有测评结果但没有关怀档案**的学生，在心理老师能到达的界面上**不存在**：
    他测了、系统也算出了等级，但没有任何一页会提到他。学校导进一份校外普查结果之后
    找不到那些没进关注名单的记录，就是这么来的——判定「一般观察」的学生不会开档案，
    而档案是那三页唯一的入口。

    「最近一场」不在这里重新推导：排序仍是 `latest_session_order`（施测时间优先、
    id 兜底），与个案详情、重点学生、受控导出同源（§11）。查询形状照
    `export_service.export_care_cases_csv`——Student 为根 + 相关标量子查询选会话，
    **不循环调用 `latest_session()`**，那是逐个学生一条 SELECT。

    与 `latest_result_subquery` 唯一的差别是**外连接**：那里是内连接，名册里还没
    交卷的学生没有结果、于是落在结果集之外（那是对的，聚合的分母不该把他算成
    「一般观察」）。这一页要回答的第一个问题恰恰是「查谁都能查到」，所以未测评的
    学生带着 `total_level=None` 留在列表里，界面按既有约定显示「未测评」——
    §3 的那条：`None` 既不是空单元格，也不是「一般观察」。

    `case_id` / `case_status` 取的是这名学生**当前的**关怀档案：与
    `care_service.get_care_case` 同一口径（按 id 取最大的一条，**不过滤 `CLOSED`**，
    §1 记着 2026-09-17 那次修复）。界面上的「查看档案」按钮只在 `case_id` 非空时渲染：
    前端拿不到 HTTP 状态码（§2），无档案的行点进去只能看到一个红条。

    列表读**不写审计**：§8 要求的是「读单个学生档案每次写」，这一条读的是名册，
    它不指名任何一名学生。点进档案的那一次读自己会写。
    """
    ensure_student_result_reader(db, user)

    latest_session_id = (
        select(AssessmentSession.id)
        .outerjoin(AssessmentTask, AssessmentTask.id == AssessmentSession.task_id)
        .where(
            AssessmentSession.student_id == Student.id,
            effective_session_predicate(),
            # 任务被作废时那一场不算「他最近的一次」（§4.14 的防御性约束之一）。
            # 这里的连接是**外连接**（任务外的会话没有 task_id），所以「没有任务」那一支
            # 必须留着：`NULL != 'VOIDED'` 在 SQL 里是 NULL 而不是真。
            or_(AssessmentSession.task_id.is_(None), active_task_predicate()),
        )
        .correlate(Student)
        .order_by(*latest_session_order())
        .limit(1)
        .scalar_subquery()
    )
    # 当前档案 = id 最大的那条，与 `care_service.get_care_case` 一致。取 id 而不是取
    # 「非 CLOSED 的那条」：一名学生可以有一条已关闭的旧档案、之后再开一条新的，
    # 而详情页现在取的就是 id 最大的那条（关闭过的档案它照样展示）。
    current_case_id = (
        select(StudentCareCase.id)
        .where(StudentCareCase.student_id == Student.id)
        .correlate(Student)
        .order_by(StudentCareCase.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    # 三个外连接各自打在唯一键上（会话是 (task, student) 唯一、结果按 session_id 唯一、
    # 档案按主键），所以和 `export_care_cases_csv` 不同，这里不会乘行、不需要去重。
    rows = db.execute(
        select(Student, Grade, ClassGroup, AssessmentSession, AssessmentResult, StudentCareCase)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .outerjoin(AssessmentSession, AssessmentSession.id == latest_session_id)
        .outerjoin(AssessmentResult, AssessmentResult.session_id == AssessmentSession.id)
        .outerjoin(StudentCareCase, StudentCareCase.id == current_case_id)
        .where(student_scope_predicate(db, user))
        .order_by(Grade.sort_order, Grade.id, ClassGroup.id, Student.student_no)
    ).all()
    return [
        {
            "student_id": student.id,
            "student_no": student.student_no,
            # 与 `/students` 名册、重点学生队列同一个展示名（§1：`masked_name` 是
            # 花名册与关怀队列的展示名，不是遮蔽手段）。
            "student_name": student.masked_name,
            "grade": grade.name,
            "class_name": class_group.name,
            "gender": student.gender,
            "age": student.age,
            "total_level": result.total_level if result else None,
            "total_score": result.total_score if result else None,
            # 测评日期就是会话的 `submitted_at`：导入的那一场写的是**文件里的**测评日期
            # （`assessment_import_service` 把 tested_on 写进 submitted_at），
            # 所以它和这一行的等级来自同一场，不是导入那一刻。
            "submitted_at": (
                session.submitted_at.isoformat() if session and session.submitted_at else None
            ),
            # 来源跟着**等级**走，不跟着会话走：这一列要回答的是「这一行的等级是谁判的」，
            # 没有等级就没有可归属的东西。一名学生开了卷子还没交（会话在、结果不在）时，
            # 打上「系统内作答」会让人以为他已经测出结果了——与「没有任何测评」不能冒充
            # 「测评是系统内做的」是同一条道理。
            "source": session.source if result else None,
            "case_id": care_case.id if care_case else None,
            "case_status": care_case.status if care_case else None,
        }
        for student, grade, class_group, session, result, care_case in rows
    ]


#: 效度复测名单的列，**一处定义**：`validity_retest_students` 的键与
#: `validity_retest_csv` 的表头都由它派生，所以「页面上有这一列、文件里没有」这种不一致
#: 不会发生（§11 那条「指标卡上的数必须与它点进去的那个列表同源」换到列上）。
#: `export_service` 的 `field_policy` 也读它（从**产物**倒着写，见 §29）。
VALIDITY_RETEST_COLUMNS: tuple[str, ...] = (
    "学号",
    "姓名",
    "年级",
    "班级",
    "性别",
    "年龄",
    "学籍状态",
    "测评任务",
    "测评日期",
    "来源",
    "效度分",
)


def validity_retest_students(
    db: Session,
    user: UserAccount,
    task_id: int | None = None,
    task_ids: list[int] | None = None,
) -> list[dict]:
    """效度建议复测的学生名单——**页面上那个数、这个列表、导出的文件三处同源**。

    与 `analytics_report` 的 `sample_quality.validity_flagged_count` 逐字同源的两点，
    这也是这个函数存在的全部理由：

    - **同一批场次**：都来自 `_report_scope` 的 `calculated`（每名学生取最近一场
      `CALCULATED` 的场次），不在这里另写一遍「最近一场」的推导；
    - **同一个判据**：都调 `_is_validity_flagged`。

    否则「八维度分析上写着 12 人、导出的文件里 13 行」这种不一致不会有任何东西看得见
    ——两个数各自都是对的，只是回答的不是同一个问题（§11）。

    **不套 `analysis_mode`。** 这一列读的是 `calculated` 而不是 `included`：`included`
    是「按所选统计模式纳入聚合的那些」，而效度提示与统计模式无关（§「效度单独提示，
    不等于无效」）。`DimensionsPage.vue` 的 KPI 读的也是不带模式过滤的那一个数，
    筛选条件不同的两张报表不能给出两个人数。

    **学生信息取现名册，不取目标行快照。** 这一份文件的用途是「派人去找这名学生重测」，
    要的是**今天**他在哪个班（§22 那条快照的读法是「发放那一刻学校看到的是谁」，那是
    完成率报表要回答的问题，不是这个）。与 `student_result_list` 和
    `dist/check_validity_score.sql` 同口径。

    **年级 / 班级是外连接**（照那份 SQL 的先例，而不是 `student_result_list` 的内连接）：
    名册上班级缺失的学生仍然要出现在名单里。内连接会把这种行**静默丢掉**，而名单少一个人
    的代价比某一格是 `—` 大得多——这份文件的读者会照它去点人。

    读**不写审计**：它读的是名单与等级，与 `student_result_list` 同一类。导出那一次
    自己会写（§8 要求的是导出端点要求 `purpose` 并写审计），点进档案的那一次读也会写。
    """
    ensure_student_result_reader(db, user)

    tasks, _targets, calculated = _report_scope(db, user, task_id, task_ids)
    task_names = {task.id: task.name for task in tasks}
    flagged = [(result, session) for result, session in calculated if _is_validity_flagged(result)]
    if not flagged:
        return []

    # 学生一行一条：按 `student_id` 去重，取效度分**最高**的那一场——同一个人在两场里都
    # 触发时，他要重测这件事只该在名单上出现一次（名单是用来点人的，重复行会让人以为有
    # 两个学生）。**按 id 不按姓名**：同名的两名学生是两个人，按姓名字符串去重会把其中
    # 一个从派工单上静默抹掉。`picked` 的键就是 `session.student_id`。
    # 「一人一行」与 `dist/check_validity_score.sql` 第 2 段同一口径。
    rows = db.execute(
        select(Student, Grade, ClassGroup)
        .outerjoin(Grade, Grade.id == Student.grade_id)
        .outerjoin(ClassGroup, ClassGroup.id == Student.class_id)
        .where(Student.id.in_({session.student_id for _, session in flagged}))
    ).all()
    student_rows = {student.id: (student, grade, class_group) for student, grade, class_group in rows}

    picked: dict[int, tuple] = {}
    for result, session in flagged:
        current = picked.get(session.student_id)
        if current is None or (result.validity_score or 0) > (current[0].validity_score or 0):
            picked[session.student_id] = (result, session)

    items: list[dict] = []
    for student_id, (result, session) in picked.items():
        student, grade, class_group = student_rows.get(student_id, (None, None, None))
        if student is None:  # 理论上不可达（flagged 的 student_id 就来自目标行）；不猜数据
            continue
        items.append(
            {
                "student_no": student.student_no,
                # 实名（`mask_level=IDENTIFIED`）：这一列在名册与关怀队列里叫
                # `masked_name`，那是展示名、不是遮蔽手段（§1）。
                "student_name": student.name,
                "grade": grade.name if grade else None,
                "class_name": class_group.name if class_group else None,
                "gender": export_labels.gender_label(student.gender),
                "age": student.age,
                "student_status": export_labels.student_status_label(student.status),
                "task_name": task_names.get(session.task_id) or export_labels.MISSING_LABEL,
                # 与 `student_result_list` 同口径：导入的那一场写的是文件里的测评日期。
                "tested_at": session.submitted_at.isoformat() if session.submitted_at else None,
                "source": export_labels.source_label(session.source),
                "validity_score": result.validity_score,
            }
        )
    # 次序稳定：年级 → 班级 → 学号，与 `student_result_list` 的 `order_by` 同一套。
    # 在内存里排而不是让 SQL 排，是因为行列已经在上面取过了。
    items.sort(
        key=lambda item: (
            item["grade"] or "",
            item["class_name"] or "",
            item["student_no"] or "",
        )
    )
    return items


def validity_retest_csv(
    db: Session,
    user: UserAccount,
    task_id: int | None = None,
    task_ids: list[int] | None = None,
) -> ExportDocument:
    """效度复测名单 → `ExportDocument`（列、行数与 CSV 正文都从**同一批行**来）。

    `row_count` **不数表头那一行**：它在界面上是「导出 N 行」，把表头算进去会每一份都多
    一行（§29 那条约定）。

    数值列（年龄 / 效度分）没有值时**留空**，不留 `—`：`—` 会让表格软件把整列当成文本
    （§3 数值列那条约定，与 `level_label` 的「未测评」分属两套）。
    """
    items = validity_retest_students(db, user, task_id, task_ids)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(VALIDITY_RETEST_COLUMNS)
    for item in items:
        writer.writerow(
            [
                item["student_no"],
                item["student_name"],
                item["grade"] or export_labels.MISSING_LABEL,
                item["class_name"] or export_labels.MISSING_LABEL,
                item["gender"],
                item["age"] if item["age"] is not None else "",
                item["student_status"],
                item["task_name"],
                item["tested_at"] or "",
                item["source"],
                item["validity_score"] if item["validity_score"] is not None else "",
            ]
        )
    return ExportDocument(
        csv_text="﻿" + output.getvalue(),
        columns=VALIDITY_RETEST_COLUMNS,
        row_count=len(items),
    )


def _average_or_none(average: float | None, size: int) -> float | None:
    """均值，或 `None` 表示「样本太小，不给」。见 `MIN_COHORT_FOR_AGGREGATE`。"""
    if average is None or size < MIN_COHORT_FOR_AGGREGATE:
        return None
    return round(average, 1)


def dimension_distribution(db: Session, user: UserAccount) -> list[dict]:
    """Per-dimension aggregate across every assessed student's LATEST session.

    A student with several sessions contributes only once — otherwise students
    who were retested would be double-counted and skew the distribution.

    Each row carries `max_score`, the number of items backing that dimension.
    Dimensions are not uniformly sized (two carry 15 items, the rest 10), so a
    bare score is ambiguous — "8" is 53% of one dimension and 80% of another.
    The UI needs the denominator to render the score unambiguously.

    The scope predicate goes inside the latest-session subquery rather than on
    the outer select: the subquery is what decides *whose* sessions count, and
    filtering afterwards would already have picked a student's latest session
    from a population the caller may not see.

    「最新一场」的定义在 `latest_result_subquery` 里，本模块只有那一处
    （2026-09-17 收敛）：这里曾经自己拼过一份一模一样的窗口函数副本，
    两份副本迟早会在某次修改里分叉，而它们分叉的后果是同一页上两张图描述两批人。
    """
    ensure_leader_or_counselor(db, user)
    latest = latest_result_subquery(db, user)
    rows = db.execute(
        select(
            DimensionResult.dimension_code,
            func.count(DimensionResult.id),
            func.avg(DimensionResult.score),
            func.sum(case((DimensionResult.level == "HIGH", 1), else_=0)),
        )
        .where(DimensionResult.session_id.in_(select(latest.c.session_id)))
        .group_by(DimensionResult.dimension_code)
        .order_by(DimensionResult.dimension_code)
    ).all()

    item_counts = _dimension_item_counts(db)

    items = []
    for code, assessed, average, high in rows:
        assessed = int(assessed or 0)
        high = int(high or 0)
        max_score = int(item_counts.get(code) or 0)
        items.append(
            {
                "dimension_code": code,
                "assessed_count": assessed,
                "high_count": high,
                "high_rate": round(high / assessed * 100) if assessed else 0,
                "average_score": round(float(average), 1) if average is not None else None,
                # Denominator for displaying the score; 0 means "unknown".
                "max_score": max_score,
            }
        )
    return items


def counselor_reminders(db: Session, user: UserAccount) -> dict:
    """Actionable items for the workbench timeline, derived from real records.

    Case-level (the reminder names students), so it requires the same detail
    scope as the care-case endpoints rather than the aggregate one.

    Returns `{items, total, truncated}` rather than a bare list — the same shape
    `getAuditLogs` already uses, and for the same reason. Each source is capped at
    `REMINDER_LIMIT` rows so one busy week cannot make the payload unbounded, but a
    cap the caller cannot see is a lie about the workload: the panel's badge said
    「20 项」 whether there were 20 reminders or 400. `total` counts the whole set in
    the caller's scope and the UI reports the truncation when they differ.
    """
    if not scope_allows(db, user.role_code, STUDENT_PSYCH_DETAIL, allow={SCOPED}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=30)
    scope = student_scope_predicate(db, user)

    # 两个来源各有各的上限（不是一个共享的上限）：一个老师这周有 40 条跟进要办时，
    # 复测计划仍然要看得见，不能被跟进挤掉。计数与取数用同一个 where，所以
    # `total` 与 `items` 不可能各说各话。
    followup_filter = (
        FollowUpRecord.status == "ACTIVE",
        FollowUpRecord.next_follow_up_date <= horizon,
        # Each reminder names one student, so this panel is a roster of
        # students in trouble, not an aggregate.
        scope,
    )
    retest_filter = (RetestPlan.status == "PLANNED", RetestPlan.planned_date <= horizon, scope)

    items: list[dict] = []

    followups = db.execute(
        select(FollowUpRecord, Student)
        .join(Student, Student.id == FollowUpRecord.student_id)
        .where(*followup_filter)
        .order_by(FollowUpRecord.next_follow_up_date)
        .limit(REMINDER_LIMIT)
    ).all()
    for followup, student in followups:
        days = (followup.next_follow_up_date - today).days
        if days < 0:
            when, overdue = f"已逾期 {abs(days)} 天", True
        elif days == 0:
            when, overdue = "今天", False
        else:
            when, overdue = f"{days} 天后", False
        items.append(
            {
                "kind": "FOLLOW_UP",
                "when": when,
                "overdue": overdue,
                # `title` 是**主题**，不是整句话：类别由 `kind` 单独下发，中文由
                # `labels.ts` 的 `REMINDER_KIND_LABELS` 渲染。此前这里写成
                # 「跟进 <姓名>」而视图那行前面也写着「跟进」——同一句话里两遍。
                "title": student.masked_name,
                "desc": f"约定跟进日 {followup.next_follow_up_date.isoformat()}",
                "student_id": student.id,
            }
        )

    retests = db.execute(
        select(RetestPlan, Student)
        .join(Student, Student.id == RetestPlan.student_id)
        .where(*retest_filter)
        .order_by(RetestPlan.planned_date)
        .limit(REMINDER_LIMIT)
    ).all()
    for retest, student in retests:
        days = (retest.planned_date - today).days
        items.append(
            {
                "kind": "RETEST",
                "when": f"{days} 天后" if days >= 0 else f"已逾期 {abs(days)} 天",
                "overdue": days < 0,
                "title": student.masked_name,
                "desc": retest.reason,
                "student_id": student.id,
            }
        )

    # Overdue first, then nearest due date.
    items.sort(key=lambda item: (not item["overdue"], item["when"]))

    # 计数是一次 SELECT COUNT，不是「把上面那批数一数」——后者数的是**被截断后的**
    # 那一批，正是这条要修的东西。
    #
    # `join(Student, ...)` 不能省：范围谓词过滤的是 `Student` 上的列，不 join 的话
    # SQLAlchemy 会把两张表笛卡尔积起来、谓词退化成常量，于是 `total` 数的是**全校**
    # 的待办——一个正好绕过 §9 的错。它当时确实报了 SAWarning 才被发现。
    followup_total = db.scalar(
        select(func.count())
        .select_from(FollowUpRecord)
        .join(Student, Student.id == FollowUpRecord.student_id)
        .where(*followup_filter)
    ) or 0
    retest_total = db.scalar(
        select(func.count())
        .select_from(RetestPlan)
        .join(Student, Student.id == RetestPlan.student_id)
        .where(*retest_filter)
    ) or 0
    return {
        "items": items,
        "total": int(followup_total) + int(retest_total),
        "truncated": len(items) < int(followup_total) + int(retest_total),
    }


def leader_progress(db: Session, user: UserAccount) -> list[dict]:
    if not scope_allows(db, user.role_code, AGGREGATE_STATS, allow={SCHOOL}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    # Sessions/results are resolved latest-first per student rather than joined —
    # joining them emits one row per historical session. No sensitive content
    # (answers, key questions, interview text) is included: leaders see identity
    # summaries, owner and progress only.
    #
    # **已关闭的档案不进这一页**（2026-09-17）。这一页叫「重点进展」，而一条
    # CLOSED 的档案没有进展可看：它的 `next_follow_up_date` 已经不作数（`overdue`
    # 本来就不算它），它也不再是谁的待办。留着它有三个后果，都在真实数据上量得到：
    #   1. 关过的档案会**累积**（§1：上学期关掉是一次完整的过程，这学期再出状况是
    #      另一条），列表只增不减，在办的被挤到第二页；
    #   2. 德育领导总览那张「重点关注档案」取的就是这一页的行数，于是去年已经了结的
    #      学生仍然被算作「重点关注」；
    #   3. 同页的「未分配负责人」同样按行数算，一条结案的档案挂着「未分配」是噪音。
    # 这条路**不删任何历史**（§1 的「关闭档案不得删除历史记录」说的是别删行，
    # 不是每条列表都得列出来）：个案详情仍然照原样展示已关闭档案。要在这里回看
    # 结案的档案，正确做法是给这一页加一个「已关闭」页签（照 `CasesPage` 的
    # `QUEUE_TABS`），而不是把过滤去掉。
    rows = db.execute(
        select(StudentCareCase, Student, Grade, ClassGroup, UserAccount)
        .join(Student, Student.id == StudentCareCase.student_id)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .outerjoin(UserAccount, UserAccount.id == StudentCareCase.owner_id)
        .where(StudentCareCase.status != "CLOSED")
        .order_by(StudentCareCase.updated_at.desc(), StudentCareCase.id.desc())
    ).all()

    # ★ 消除 N+1：批量预取 `latest_session` + `AssessmentResult` + `FollowUpRecord`
    # （此前是 for 循环里逐学生调 `latest_session(db, student_id)` + `db.scalar`，
    # N 名档案学生 = 3N 条额外 SELECT。改为一次关联子查询批量取，构造上 O(1)。）
    student_ids = [student.id for _, student, _, _, _ in rows]
    if student_ids:
        # 批量取最新有效会话 + 结果
        latest_result_subq = (
            select(
                AssessmentSession.student_id,
                AssessmentResult.total_level,
                AssessmentResult.validity_status,
                func.row_number()
                .over(
                    partition_by=AssessmentSession.student_id,
                    order_by=latest_session_order(),
                )
                .label("rn"),
            )
            .join(AssessmentResult, AssessmentResult.session_id == AssessmentSession.id)
            .outerjoin(AssessmentTask, AssessmentTask.id == AssessmentSession.task_id)
            .where(
                AssessmentSession.student_id.in_(student_ids),
                effective_session_predicate(),
                # 任务被作废时那一场不算「他最近的一次」（§4.14 的防御性约束之一）。
                # 这里的连接是**外连接**（任务外的会话没有 task_id），所以「没有任务」那一支
                # 必须留着：`NULL != 'VOIDED'` 在 SQL 里是 NULL 而不是真。
                or_(AssessmentSession.task_id.is_(None), active_task_predicate()),
            )
            .subquery()
        )
        results_by_student = {
            r.student_id: r
            for r in db.execute(
                select(latest_result_subq).where(latest_result_subq.c.rn == 1)
            ).all()
        }

        # 批量取最近一次跟进日期
        latest_followup_subq = (
            select(
                FollowUpRecord.student_id,
                FollowUpRecord.next_follow_up_date,
                func.row_number()
                .over(
                    partition_by=FollowUpRecord.student_id,
                    order_by=FollowUpRecord.next_follow_up_date.desc(),
                )
                .label("rn"),
            )
            .where(
                FollowUpRecord.student_id.in_(student_ids),
                FollowUpRecord.status == "ACTIVE",
            )
            .subquery()
        )
        followups_by_student = {
            r.student_id: r.next_follow_up_date
            for r in db.execute(
                select(latest_followup_subq).where(latest_followup_subq.c.rn == 1)
            ).all()
        }
    else:
        results_by_student = {}
        followups_by_student = {}

    today = datetime.now(UTC).date()
    items = []
    for care_case, student, grade, class_group, owner in rows:
        result = results_by_student.get(student.id)
        next_follow_up = followups_by_student.get(student.id)
        items.append(
            {
                "case_id": care_case.id,
                "student_id": student.id,
                "student_name": student.masked_name,
                "student_no": student.student_no,
                "grade": grade.name,
                "class_name": class_group.name,
                "case_status": care_case.status,
                "total_level": result.total_level if result else None,
                "validity_status": result.validity_status if result else None,
                "owner_id": care_case.owner_id,
                "owner_name": owner.display_name if owner else None,
                "next_follow_up_date": next_follow_up.isoformat() if next_follow_up else None,
                "overdue": bool(next_follow_up and next_follow_up < today and care_case.status != "CLOSED"),
                "opened_at": care_case.opened_at.isoformat() if care_case.opened_at else None,
                "updated_at": care_case.updated_at.isoformat() if care_case.updated_at else None,
            }
        )
    return items
