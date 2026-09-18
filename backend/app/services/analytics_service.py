from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    DimensionResult,
)
from app.models.care import FollowUpRecord, RetestPlan, StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import ScaleQuestion
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.services.assessment_service import latest_session, latest_session_order
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
        .where(student_scope_predicate(db, user))
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


def analytics_overview(db: Session, user: UserAccount) -> dict:
    ensure_leader_or_counselor(db, user)
    # Every figure is narrowed to the caller's scope, so a counselor's 完成率
    # describes their own students while a leader's describes the school. That is
    # the distinction the capability matrix already draws between SCOPED and
    # SCHOOL — it just was not implemented. The UI labels state which is which,
    # because a scoped number wearing a school-wide label is a lie.
    scope = student_scope_predicate(db, user)
    total_targets = (
        db.scalar(
            select(func.count(AssessmentTarget.id))
            .join(Student, Student.id == AssessmentTarget.student_id)
            .where(scope)
        )
        or 0
    )
    completed_targets = (
        db.scalar(
            select(func.count(AssessmentTarget.id))
            .join(Student, Student.id == AssessmentTarget.student_id)
            .where(AssessmentTarget.status == "COMPLETED", scope)
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
        .outerjoin(AssessmentTarget, AssessmentTarget.student_id == Student.id)
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
        .outerjoin(AssessmentTarget, AssessmentTarget.student_id == Student.id)
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


def _dimension_item_counts(db: Session) -> dict[str, int]:
    """每个维度由多少道题支撑，取自现行的题库。

    这个数是**展示分数的分母**：八个维度题数不等（两个 15 题、其余 10 题），
    一个裸分是歧义的——「8」在一个维度里是 53%，在另一个里是 80%。
    """
    return dict(
        db.execute(
            select(ScaleQuestion.dimension_code, func.count(ScaleQuestion.id))
            .where(ScaleQuestion.dimension_code.isnot(None), ScaleQuestion.status == "ACTIVE")
            .group_by(ScaleQuestion.dimension_code)
        ).all()
    )


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
        .where(AssessmentSession.student_id == Student.id)
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
        # Each reminder's title is 「跟进 <姓名>」, so this panel is a roster
        # of students in trouble, not an aggregate.
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
                "title": f"跟进 {student.masked_name}",
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
                "title": f"{student.masked_name} 复测",
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
    today = datetime.now(UTC).date()
    items = []
    for care_case, student, grade, class_group, owner in rows:
        # 口径同个案详情与重点学生列表：施测时间最近的一场，不是 id 最大的那场。
        sitting = latest_session(db, student.id)
        result = (
            db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == sitting.id))
            if sitting
            else None
        )
        next_follow_up = db.scalar(
            select(FollowUpRecord.next_follow_up_date)
            .where(FollowUpRecord.student_id == student.id, FollowUpRecord.status == "ACTIVE")
            .order_by(FollowUpRecord.next_follow_up_date.desc())
        )
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
