from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    DimensionResult,
    RiskEvent,
)
from app.models.audit import AuditLog
from app.models.care import (
    CareCaseEvent,
    FamilyContactRecord,
    FollowUpRecord,
    ManualReview,
    RetestPlan,
    StudentCareCase,
)
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import ScaleQuestion
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.security.permissions import SCOPED, STUDENT_PSYCH_DETAIL, scope_allows
from app.schemas.care import (
    BatchAssignItem,
    CloseCaseRequest,
    FamilyContactRequest,
    FollowUpRequest,
    ManualReviewRequest,
    ReopenCaseRequest,
    RetestPlanRequest,
)
from app.services.assessment_service import (
    latest_session,
    now_utc_naive,
    session_history_order,
)
from app.services.care_events import (
    CASE_CLOSED,
    CASE_REOPENED,
    FAMILY_CONTACT_ADDED,
    FOLLOW_UP_ADDED,
    MANUAL_REVIEWED,
    OWNER_ASSIGNED,
    RETEST_PLANNED,
    record_case_event,
)

# `student_care_case.reopen_reason` 是 String(255)，而 `ReopenCaseRequest.reason` 允许
# 5000 字。写回那一列时必须截断——否则 MySQL 严格模式下报的是一句
# `1406 Data too long for column 'reopen_reason'`，离「重新打开原因太长了」隔着一个列名。
# **原文一个字都不丢**：它完整地留在事件与那条跟进记录上（两处都是 Text / 明文），
# 被截断的只是那个给「最近一次重开原因」用的摘要列。同一个形状见 `close_case`——
# 那边的两个字段宽度与请求上的 `max_length` 是对齐的（128 / Text），所以不需要截断。
REOPEN_REASON_COLUMN_LIMIT = 255


def ensure_counselor(db: Session, user: UserAccount) -> None:
    """Single source of truth for case-detail access.

    Delegates to the capability matrix rather than hard-coding COUNSELOR, so an
    operator granting the scope in 权限配置 actually takes effect. The API layer
    enforces the same rule via require_capability(); this is the service-layer
    backstop for direct callers.
    """
    if not scope_allows(db, user.role_code, STUDENT_PSYCH_DETAIL, allow={SCOPED}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def _scoped_case(db: Session, user: UserAccount, case_id: int) -> StudentCareCase:
    """Load a case the caller is actually allowed to touch.

    All six mutations below used to call `db.get(StudentCareCase, case_id)` with
    no ownership check, so any counselor holding a case id could review,
    reassign, close or reopen it — `ensure_counselor` only proves the caller is
    *a* counselor, never that this student is *theirs*.
    """
    care_case = db.get(StudentCareCase, case_id)
    if not care_case:
        raise AppError("NOT_FOUND", "关注档案不存在", 404)
    ensure_student_in_scope(db, user, care_case.student_id)
    return care_case


def _ensure_open(care_case: StudentCareCase) -> None:
    """已关闭的档案不能被直接改动 —— §16.4「关闭档案不可直接新增跟进，必须先重新打开」。

    **这不只是那条规格要求，它同时堵住了四个同类 500。** 下面四个入口
    （复核 / 跟进 / 家庭回访 / 复测）都会把 `status` 改成 `FOLLOWING` 或 `OBSERVING`，
    而对一条 CLOSED 档案这样做等于**隐式复活**它——`active_student_id` 生成列立刻
    非空，若这名学生已经又有一条在办档案（秋季关档、春季再开，见 §1），
    它当场撞 `uq_care_case_one_active_per_student`，用户拿到一句英文的 500。
    那四个入口此前没有任何一个查过状态。
    """
    if care_case.status == "CLOSED":
        raise AppError(
            "CONFLICT",
            "这份关注档案已经关闭，不能直接新增记录。请先「重新打开档案」再继续。",
            409,
        )


def _check_case_version(care_case: StudentCareCase, expected: int | None) -> None:
    """乐观锁（§16.4：关闭 / 重新打开 / 转派负责人必须带版本号）。

    `case_version` 在 V1.2 对齐阶段就加好了列，但**直到这一期才有人读它**
    （CLAUDE.md 缺口 9：它此前恒为 1）。判据是「客户端读到的那一版，现在还是不是
    那一版」——两份页面同时开着时，后动手的那一个会拿到 409 与一句人话，
    而不是把先动手的那一个的修改静默盖掉。

    `expected is None` 是**只给内部调用方留的口子**（服务层的其他路径调进来时
    还没有版本概念）：接口层的两个请求模型都把 `case_version` 声明成必填，
    所以从 HTTP 进来的调用一定带号。
    """
    if expected is None:
        return
    if expected != care_case.case_version:
        raise AppError(
            "CONFLICT",
            "这份关注档案在你打开之后已经被别人修改过（当前版本 "
            f"{care_case.case_version}，你手上的是 {expected}）。"
            "请刷新页面、确认最新的状态之后重试。",
            409,
        )


def _bump_case_version(care_case: StudentCareCase) -> None:
    """改完 +1。与 `_check_case_version` 成对出现，一处读一处写、都在本模块里。"""
    care_case.case_version = care_case.case_version + 1


def counselor_workbench(db: Session, user: UserAccount) -> dict:
    ensure_counselor(db, user)
    scope = student_scope_predicate(db, user)
    pending_review = db.scalar(
        select(func.count(StudentCareCase.id))
        .join(Student, Student.id == StudentCareCase.student_id)
        .where(StudentCareCase.status == "PENDING_REVIEW", scope)
    )
    following = db.scalar(
        select(func.count(StudentCareCase.id))
        .join(Student, Student.id == StudentCareCase.student_id)
        .where(StudentCareCase.status == "FOLLOWING", scope)
    )
    risk_events = db.scalar(
        select(func.count(RiskEvent.id))
        .join(Student, Student.id == RiskEvent.student_id)
        .where(RiskEvent.status == "PENDING", scope)
    )
    # 完成率的分母必须是「被派发任务的学生数」，不是「已开始的会话数」——
    # 会话只在学生开始答题后才创建，用会话数做分母会让这个比值恒等于 100%，
    # 与德育领导页看到的同一指标对不上。
    #
    # 分母同时跟着用户的数据范围。这与上面那句是两件事：前者定「算哪些人」，
    # 后者定「哪些人算」——把它们一起换掉才是对的。德育领导的 SCHOOL 范围
    # 让同一个函数仍然给出全校数字，所以领导页与工作台的口径不会打架。
    completed = db.scalar(
        select(func.count(AssessmentTarget.id))
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(AssessmentTarget.status == "COMPLETED", scope)
    )
    targets = db.scalar(
        select(func.count(AssessmentTarget.id))
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(scope)
    )
    completion_rate = round((completed or 0) / targets * 100) if targets else 0
    return {
        "pending_review": pending_review or 0,
        "following": following or 0,
        "pending_risk_events": risk_events or 0,
        "completion_rate": completion_rate,
    }


def list_care_cases(db: Session, user: UserAccount) -> list[dict]:
    ensure_counselor(db, user)
    # NOTE: closed cases are included so the 已关闭 queue tab can filter on them.
    # Sessions/results are fetched separately (latest-first) rather than joined —
    # joining them here would emit one row per historical session.
    rows = db.execute(
        select(StudentCareCase, Student, Grade, ClassGroup, UserAccount)
        .join(Student, Student.id == StudentCareCase.student_id)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .outerjoin(UserAccount, UserAccount.id == StudentCareCase.owner_id)
        # The queue names students, so it is narrowed to the caller's scope. The
        # workbench tiles above it are narrowed by the same scope — a tile that
        # counted the whole school over a list of three would be worse than
        # either number alone.
        .where(student_scope_predicate(db, user))
        .order_by(StudentCareCase.updated_at.desc(), StudentCareCase.id.desc())
    ).all()
    today = now_utc_naive().date()
    items = []
    for care_case, student, grade, class_group, owner in rows:
        sitting = latest_session(db, student.id)
        result = (
            db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == sitting.id))
            if sitting
            else None
        )
        pending_events = db.scalar(
            select(func.count(RiskEvent.id)).where(
                RiskEvent.student_id == student.id,
                RiskEvent.status == "PENDING",
            )
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
                "student_no": student.student_no,
                "student_name": student.masked_name,
                "grade": grade.name,
                "class_name": class_group.name,
                "case_status": care_case.status,
                # 「批量分配」逐行带回来的版本号（§16.4）。列表是唯一能一次拿到
                # 多条档案版本的地方，所以它必须在这里下发。
                "case_version": care_case.case_version,
                "owner_id": care_case.owner_id,
                "owner_name": owner.display_name if owner else None,
                "total_level": result.total_level if result else None,
                "validity_status": result.validity_status if result else None,
                "pending_risk_events": pending_events or 0,
                "next_follow_up_date": next_follow_up.isoformat() if next_follow_up else None,
                # Overdue = a follow-up was scheduled and its date has passed while the case is open.
                "overdue": bool(
                    next_follow_up
                    and next_follow_up < today
                    and care_case.status not in {"CLOSED"}
                ),
                "opened_at": care_case.opened_at.isoformat() if care_case.opened_at else None,
                "updated_at": care_case.updated_at.isoformat() if care_case.updated_at else None,
            }
        )
    return items


def list_assignable_owners(db: Session, user: UserAccount) -> list[dict]:
    """Counselors a case can be assigned to. Only active counselor accounts qualify."""
    ensure_counselor(db, user)
    owners = db.scalars(
        select(UserAccount)
        .where(UserAccount.role_code == RoleCode.COUNSELOR, UserAccount.active.is_(True))
        .order_by(UserAccount.id)
    ).all()
    return [{"id": owner.id, "display_name": owner.display_name} for owner in owners]


def batch_assign_owner(
    db: Session, user: UserAccount, assignments: list[BatchAssignItem], owner_id: int
) -> dict:
    """转派负责人（§16.4 的三处乐观锁之一）。

    **载荷是逐行带版本号的**，不是一串 id：一条「批量分配」动的是每一行档案的
    `owner_id`，而客户端读到的那一行的版本各不相同（列表上有些行是十分钟前拉的）。
    一个 `list[int]` 承载不了这件事——它只能表达「我要改这几条」，
    表达不了「我读到的是这几条的哪一版」。

    **任何一条版本对不上就 409，且一行都不写。** 这与下面那句「Validate the whole
    batch before mutating any row」是同一条理由：只改看得见的那一部分、然后报一个
    比请求小的数，用户没有任何办法知道哪几条被丢下了。
    """
    ensure_counselor(db, user)
    if not assignments:
        raise AppError("VALIDATION_ERROR", "请至少选择一名学生", 422)
    owner = db.get(UserAccount, owner_id)
    if not owner or owner.role_code != RoleCode.COUNSELOR or not owner.active:
        raise AppError("VALIDATION_ERROR", "负责人必须是启用中的心理老师账号", 422)

    # 一条 assignment 里同一个 case_id 出现两次时，两次都拿同一个版本号比对、
    # 而第一次 +1 之后第二次必然对不上——报给用户的是「被改过了」，而真相是
    # 他自己把这一行发了两遍。去重之后行为才是可预期的。
    requested = {item.case_id: item.case_version for item in assignments}
    cases = db.scalars(
        select(StudentCareCase).where(StudentCareCase.id.in_(list(requested)))
    ).all()
    if len(cases) != len(requested):
        raise AppError("NOT_FOUND", "未找到可分配的关注档案", 404)
    # Validate the whole batch before mutating any row: silently assigning only
    # the subset the caller can see would report a smaller count than requested,
    # with no indication of which ids were dropped.
    for care_case in cases:
        ensure_student_in_scope(db, user, care_case.student_id)
        _check_case_version(care_case, requested[care_case.id])
    # 事件里的那句话写**姓名**而不是账号 id：这条时间线的读者是心理老师，
    # 而「负责人 12 → 15」对他不承载任何信息。上一位负责人要另查一次（它在
    # 上面那条查询里没有 join），所以一次把这一批用到的名字全取出来——
    # 逐行 `db.get` 就是 N 次 SELECT。
    previous_owner_ids = {care_case.owner_id for care_case in cases if care_case.owner_id}
    previous_owner_names = {
        row_id: display_name
        for row_id, display_name in db.execute(
            select(UserAccount.id, UserAccount.display_name).where(
                UserAccount.id.in_(previous_owner_ids)
            )
        ).all()
    }

    for care_case in cases:
        previous_owner_name = previous_owner_names.get(care_case.owner_id or -1)
        care_case.owner_id = owner_id
        _bump_case_version(care_case)
        record_case_event(
            db,
            care_case,
            OWNER_ASSIGNED,
            user.id,
            reason=f"负责人 {previous_owner_name or '（未分配）'} → {owner.display_name}",
            # 转派不改状态，所以 from_status / to_status 都留空——它是这张表上唯一
            # 一条不涉及状态迁移的事件（`record_case_event` 的 docstring 写着同一条）。
        )
    # Existing follow-up records are untouched — assignment only changes the owner.
    return {"updated": len(cases)}


def student_assessment_records(db: Session, student: Student) -> dict:
    """一名学生的测评记录——**写在他档案之外的那四个块**。

    `student` / `assessment` / `dimensions` / `history` 与 `student_care_case` 无关
    （`get_care_case` 里只有 `events` 依赖 `care_case.id`），所以「学生持续关注档案」页
    与「学生测评记录」页读的是同一份。两处各拼一份时，同一个字段会在两个屏幕上各说各话，
    而漂了不会有任何东西报错——所以它只有这一个定义。

    **它不需要这名学生有档案，这正是它被抽出来的理由。** 全库唯一的开档触发点是重点题
    85 / 97 命中（`assessment_service.maybe_raise_risk_events` 的 docstring 逐字写着），
    所以一个被评成「需要关注」、甚至「重点关注」的学生**照样可能没有档案**；
    在此之前，那些学生连「他考过几次、每次多少分」都查不到——`get_care_case` 是唯一的
    读法，而它在没有档案时回 404（§「已知缺口」那条「缺的只是可达性」）。
    """
    student_id = student.id
    grade = db.get(Grade, student.grade_id)
    class_group = db.get(ClassGroup, student.class_id)
    # 「本次测评」= 施测时间最近的一场，不是 id 最大的一场——导入的历史普查 id 更大。
    # 口径见 `assessment_service.latest_session_order`。
    sitting = latest_session(db, student_id)
    result = (
        db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == sitting.id))
        if sitting
        else None
    )
    # Denominator per dimension, so the UI can render "8 / 15" rather than a
    # bare number whose meaning depends on an item count the client can't see.
    dimension_item_counts = dict(
        db.execute(
            select(ScaleQuestion.dimension_code, func.count(ScaleQuestion.id))
            .where(ScaleQuestion.dimension_code.isnot(None), ScaleQuestion.status == "ACTIVE")
            .group_by(ScaleQuestion.dimension_code)
        ).all()
    )
    # 八维度分值：读取已存在的 dimension_result 表（此前从未在接口层暴露）
    dimensions = (
        db.scalars(
            select(DimensionResult)
            .where(DimensionResult.session_id == sitting.id)
            .order_by(DimensionResult.dimension_code)
        ).all()
        if sitting
        else []
    )
    # 历次测评事实序列，供「历次趋势」比较。**从旧到新**排（趋势图按这个方向读），
    # 排序口径与「本次测评」共用 `latest_session_order()`：施测时间优先，id 兜底。
    # 只按 id 排的话，导入一份上学期的普查结果就会在末尾凭空多出一个「最近」的点。
    #
    # **这里刻意不加 `effective_session_predicate()`**（上面那个 `sitting` 加了，两处
    # 不一样是有意的）。被 §18.8 降级过的那一场仍然是他真实考过的一次：答案、用时、
    # 那一天的分都在，趋势图少一个点就是在抹掉一段发生过的事实。降级说的是「他现在
    # 以哪一份为准」，不是「那一次不算测评」。
    history_sessions = db.scalars(
        select(AssessmentSession)
        .where(AssessmentSession.student_id == student_id)
        .order_by(*session_history_order())
    ).all()
    # 三条查询取完整条序列：此前是「每场一次 AssessmentResult」的 N+1，逐维度的历史分
    # 还要再乘一场。归组在 Python 里做，查询数与会话数无关。
    session_ids = [past_session.id for past_session in history_sessions]
    results_by_session: dict[int, AssessmentResult] = {}
    dimensions_by_session: dict[int, list[DimensionResult]] = {}
    if session_ids:
        results_by_session = {
            row.session_id: row
            for row in db.scalars(
                select(AssessmentResult).where(AssessmentResult.session_id.in_(session_ids))
            )
        }
        for row in db.scalars(
            select(DimensionResult)
            .where(DimensionResult.session_id.in_(session_ids))
            .order_by(DimensionResult.dimension_code)
        ):
            dimensions_by_session.setdefault(row.session_id, []).append(row)
    history = []
    for past_session in history_sessions:
        past_result = results_by_session.get(past_session.id)
        history.append(
            {
                "session_id": past_session.id,
                "submitted_at": past_session.submitted_at.isoformat() if past_session.submitted_at else None,
                "duration_seconds": past_session.duration_seconds,
                "total_score": past_result.total_score if past_result else None,
                "total_level": past_result.total_level if past_result else None,
                "validity_status": past_result.validity_status if past_result else None,
                # 这一场是学生在本系统里做的，还是学校从外部平台导入的。**逐场给**：
                # 一名学生的历次记录可以一半在线、一半导入（缺口 8），
                # 而「用时 5340 秒」在两种来源下是两件事——导入的那一场没有本系统的
                # 作答过程，用时是那个平台自己报的数。
                "source": past_session.source,
                # 每场自己的八维度分。趋势图要按维度看变化，而各维度题数不同（10 或 15），
                # 所以必须带上 `max_score`，前端才能归一化后比较——只给原始分会让 15 题的
                # 身体症状凭空压过 10 题的孤独倾向。
                "dimensions": [
                    {
                        "dimension_code": dimension.dimension_code,
                        "score": dimension.score,
                        "max_score": dimension_item_counts.get(dimension.dimension_code, 0),
                    }
                    for dimension in dimensions_by_session.get(past_session.id, [])
                ],
            }
        )
    return {
        "student": {
            "id": student.id,
            "student_no": student.student_no,
            "name": student.masked_name,
            "grade": grade.name,
            "class_name": class_group.name,
            "gender": student.gender,
            # 名册上存下来的整数（迁移 0011），没填就是 null。
            "age": student.age,
        },
        "assessment": {
            "session_id": sitting.id if sitting else None,
            "submitted_at": sitting.submitted_at.isoformat() if sitting and sitting.submitted_at else None,
            "duration_seconds": sitting.duration_seconds if sitting else None,
            # 「算出来了没有」与「算出来是什么」是两个字段：上面那三个只有在算成了之后
            # 才可能不是 `None`，而这一组回答的是为什么它们可能是 `None`——
            # `PENDING`（还没算）/ `CALCULATING`（正在算，只可能在同一事务里）/
            # `CALCULATED` / `CALCULATION_FAILED`（算不出来，附带原因，可以重试）。
            # 个案详情据此渲染那一行「评分状态」与重试按钮。
            "calculation_status": sitting.calculation_status if sitting else None,
            "calculation_error": sitting.calculation_error if sitting else None,
            # 「这一场测的是哪一天」以及那个日期是从哪来的。它与 `submitted_at` 在在线
            # 路径上是同一个值，**但两者回答的不是同一个问题**：导入的会话 `submitted_at`
            # 也是文件里的测评日，所以只看时间戳分不出这一场是学生在线做的还是从外部平台
            # 导进来的（`source` 说「哪来的」，这一列说「日期是谁给的」）。
            "tested_at": sitting.tested_at.isoformat() if sitting and sitting.tested_at else None,
            "tested_at_source": sitting.tested_at_source if sitting else None,
            "total_level": result.total_level if result else None,
            "validity_status": result.validity_status if result else None,
            "rule_version": result.rule_version if result else None,
            # 这一场测评的原始总分。**他档案那一页的概览卡不显示它**（那一页回答的是
            # 「他属于哪一档」），但「学生测评记录」页的页头要写「最近一次 · 一般观察 ·
            # 总分 24」——那个数只能从这里拿。
            #
            # **不能改成从 `history` 的最后一行取。** `history` 走
            # `session_history_order()` 且刻意不含 `effective_session_predicate()`
            # （上面那一段注释），所以它的最后一行未必是**有效**的那一场；拿它填
            # 「最近一次」会在被 §18.8 降级过的学生上给出另一场的分，而屏幕上一切正常。
            "total_score": result.total_score if result else None,
            # 这场测评是学生在本系统里做的，还是学校从外部平台导入的。个案详情页据此
            # 显示「来源」，因为「用时 5340 秒」在两种来源下是两件事——一份导入的记录
            # 没有本系统的作答过程，用时是那个平台自己报的数。
            "source": sitting.source if sitting else None,
        },
        "dimensions": [
            {
                "dimension_code": dimension.dimension_code,
                "score": dimension.score,
                "level": dimension.level,
                "interpretation": dimension.interpretation,
                # Dimensions carry different item counts (10 or 15), so a bare
                # score is ambiguous without its denominator.
                "max_score": dimension_item_counts.get(dimension.dimension_code, 0),
            }
            for dimension in dimensions
        ],
        "history": history,
    }


def get_student_assessment_records(db: Session, user: UserAccount, student_id: int) -> dict:
    """上面的那一份，**按学生 id 取、不需要他有档案**。

    门槛与 `get_care_case` 逐字相同的一道（能力 + 数据范围）：两条路径给的是同一个学生的
    同一批列（学号 / 姓名 / 等级 / 总分 / 维度分），门槛不同就是「同一件事两条路径两个
    答案」。`GET /students/results` 用两道是因为它下发**整份名册**，不是单个学生——
    这一条要的两道里更严的那道已经覆盖了分数，不构成旁路。

    **比 `student_assessment_records` 多一个 `case_id`**：这一份是给界面读的，而「学生测评
    记录」页底部要说清「该生尚未建档（重点题未命中）」、并在有档时给一枚「查看关注档案」。
    那一页**不可以**靠「拉一次档案详情、看它是不是 404」来回答这个问题——前端拿不到
    HTTP 状态码（§2），而且那会顺带在轨迹里多写一条「查看学生详情」（他没看档案，
    只是想知道有没有），把访问记录变成一句假话。所以由这一侧一次答完。
    它取的是 `current_care_case`，与档案页那一侧**同一个定义**（见那个函数）。
    """
    ensure_counselor(db, user)
    # Scope first: an out-of-range student must not learn whether he is known here.
    student = ensure_student_in_scope(db, user, student_id)
    records = student_assessment_records(db, student)
    # 键序把 `case_id` 放在最后：前面四块由 `student_assessment_records` 一处装配，
    # 这里只是给界面补一个它才需要的问题的答案。
    records["case_id"] = getattr(current_care_case(db, student_id), "id", None)
    return records


def current_care_case(db: Session, student_id: int) -> StudentCareCase | None:
    """这名学生**当前**的那份档案，没有就返回 `None`。

    **这是「当前档案」唯一的定义**，两个读点共用：`get_care_case`（他有没有档案、
    是哪一份）与 `get_student_assessment_records`（底部那一句「该生尚未建档」
    与那枚「查看关注档案」按钮要不要出现）。两处各写一遍 `order_by(id.desc())`
    就是在定义两次「谁是他现在的档案」——而这两处**必须在同一条行上给出一致的答案**，
    否则会出现「这一页说没有档案、那一页打得开」这种谁也不信的对话。

    **已关闭的档案也算**（2026-09-17 修）。`get_care_case` 此前过滤掉 CLOSED，后果是
    「关闭」这个动作把自己脚下的页面抽掉了：`closeCase()` 成功之后紧接着
    `await load()` 重新拉详情，拿到 404「关注档案不存在」——用户看到的是
    「刚点完关闭，系统说这档案不存在」，而它其实关好了，只是这一页不肯显示。
    同一个过滤还让详情页 `v-else` 的「重新打开档案」成了死代码（它的条件是
    `case_status === 'CLOSED'`，而这一页永远拿不到 CLOSED），列表里 CLOSED 那行的
    「查看档案」也一律跳到 404——列表不过滤、详情过滤，两处口径本来就对不上。

    一条学生的档案可能不止一条（那条 `(student_id, status)` 唯一键已在迁移 `0012`
    删掉，见 CLAUDE.md §1：秋季关档、春季再开是学校每年都会遇到的时序），
    所以取 **id 最大**的那条，与 `assessment_service.open_or_reuse_care_case`
    选「当前档案」的口径一致：新档开出后它自然接替旧档，旧档仍留在库里。
    """
    return db.scalar(
        select(StudentCareCase)
        .where(StudentCareCase.student_id == student_id)
        .order_by(StudentCareCase.id.desc())
        .limit(1)
    )


def get_care_case(db: Session, user: UserAccount, student_id: int) -> dict:
    ensure_counselor(db, user)
    # Scope first: an out-of-range student must not learn whether a case exists.
    student = ensure_student_in_scope(db, user, student_id)
    care_case = current_care_case(db, student_id)
    if not care_case:
        raise AppError("NOT_FOUND", "关注档案不存在", 404)
    # 学生 / 测评 / 维度 / 历次这四块与档案无关，**与「学生测评记录」页共用一份装配**
    # （`student_assessment_records`）。两处各拼一份时，同一个字段会在两个屏幕上各说各话，
    # 而漂了不会有任何东西报错——这一页与那一页回答的是同一个学生的同一批列。
    records = student_assessment_records(db, student)
    risk_events = db.scalars(select(RiskEvent).where(RiskEvent.student_id == student_id).order_by(RiskEvent.id.desc())).all()
    followups = db.scalars(
        select(FollowUpRecord).where(FollowUpRecord.student_id == student_id).order_by(FollowUpRecord.id.desc())
    ).all()
    family_contacts = db.scalars(
        select(FamilyContactRecord)
        .where(FamilyContactRecord.student_id == student_id)
        .order_by(FamilyContactRecord.id.desc())
    ).all()
    retests = db.scalars(
        select(RetestPlan).where(RetestPlan.student_id == student_id).order_by(RetestPlan.id.desc())
    ).all()
    # 该生敏感访问记录。仅统计本次迁移之后写入的行 —— 历史行的 student_id 为 NULL。
    audit_rows = db.execute(
        select(AuditLog, UserAccount)
        .outerjoin(UserAccount, UserAccount.id == AuditLog.actor_user_id)
        .where(AuditLog.student_id == student_id)
        .order_by(AuditLog.id.desc())
        .limit(100)
    ).all()
    # 档案事件：带操作人姓名，最新在前（与这一页其余列表同一个读法，它们都是
    # `id.desc()`）。**没有上限**：事件是只追加的，而一份档案的事件数由它经历过的
    # 复核/跟进/回访/复测/关档次数决定——那是十几次量级，不是几百次。
    # 什么时候它真的会很长（一张档案上几百条），那时再加 `limit` 与一句
    # 「还有 N 条未显示」（§10），而不是现在猜一个数。
    event_rows = db.execute(
        select(CareCaseEvent, UserAccount)
        .outerjoin(UserAccount, UserAccount.id == CareCaseEvent.operator_id)
        .where(CareCaseEvent.care_case_id == care_case.id)
        .order_by(CareCaseEvent.id.desc())
    ).all()
    # **逐项列出，不写成 `**records`。** 展开会把这四个键整体挪到开头（或末尾），
    # 而 `assessment` / `dimensions` / `history` 在这一页的中段——键序变了接口 payload
    # 就变了，前端现在按名字取所以看不出来，但一个「响应逐字相同」的断言会红，
    # 而红的原因不是功能坏了。键序保持原样是这个重构的安全边界。
    return {
        "case_id": care_case.id,
        "student": records["student"],
        "case_status": care_case.status,
        # 乐观锁的版本号（§16.4）。**它必须出现在这里**，因为关闭 / 重开都要求
        # 客户端把它带回来——而客户端唯一拿得到它的地方就是这一个响应。
        "case_version": care_case.case_version,
        "assessment": records["assessment"],
        "risk_events": [
            {
                "id": event.id,
                "risk_type": event.risk_type,
                "risk_level": event.risk_level,
                "trigger_rule": event.trigger_rule,
                "status": event.status,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in risk_events
        ],
        "follow_ups": [
            {
                "id": followup.id,
                "record_type": followup.record_type,
                "confirmed_facts": followup.confirmed_facts,
                "next_follow_up_date": followup.next_follow_up_date.isoformat(),
                "status": followup.status,
                "created_at": followup.created_at.isoformat() if followup.created_at else None,
            }
            for followup in followups
        ],
        "family_contacts": [
            {
                "id": contact.id,
                "contact_date": contact.contact_date.isoformat(),
                "contact_person": contact.contact_person,
                "channel": contact.channel,
                "result": contact.result,
                "support_status": contact.support_status,
                "confirmed_facts": contact.confirmed_facts,
                "next_contact_date": contact.next_contact_date.isoformat() if contact.next_contact_date else None,
            }
            for contact in family_contacts
        ],
        "retest_plans": [
            {
                "id": retest.id,
                "planned_date": retest.planned_date.isoformat(),
                "reason": retest.reason,
                "status": retest.status,
            }
            for retest in retests
        ],
        "dimensions": records["dimensions"],
        "history": records["history"],
        # 档案事件时间线（§16.4）——「这份档案经历了什么」。
        #
        # **按 `care_case_id` 过滤，不按 `student_id`。** 这一页上面那几个列表
        # （`follow_ups` / `family_contacts` / `retest_plans` / `risk_events`）都是按
        # 学生过滤、**跨档案**取全部历史行的（§1：关闭档案不得删除历史记录，
        # 所以一名学生的旧档案上的记录一直在）。事件不同：它回答的是
        # 「**这一份**档案从开档到现在经过了谁的手」，把上一条已关闭档案的事件
        # 混进来会让这条时间线读不出边界。两者口径不同是有意的。
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "operator_name": operator.display_name if operator else None,
                "reason": event.reason,
                "confirmed_facts": event.confirmed_facts,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event, operator in event_rows
        ],
        "audit_logs": [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "actor_name": actor.display_name if actor else None,
                "actor_role": row.actor_role,
                "action": row.action,
                "result": row.result,
                "purpose": row.purpose,
            }
            for row, actor in audit_rows
        ],
    }


def create_manual_review(db: Session, user: UserAccount, case_id: int, payload: ManualReviewRequest) -> ManualReview:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    _ensure_open(care_case)
    risk_event = db.get(RiskEvent, payload.risk_event_id)
    if not risk_event or risk_event.student_id != care_case.student_id:
        raise AppError("SCOPE_FORBIDDEN", "风险事件不属于该关注档案", 403)
    review = ManualReview(
        risk_event_id=risk_event.id,
        reviewer_id=user.id,
        review_result=payload.review_result,
        confirmed_facts=payload.confirmed_facts,
        next_action=payload.next_action,
        next_follow_up_date=payload.next_follow_up_date,
        # §16.4：新建记录的 `care_case_id` 必须非空。这两列在 V1.2 对齐阶段就加好了
        # （可空只是给历史行回填用的），而复合外键 `manual_review_fk_case_student`
        # 保证它们与档案指向同一名学生——所以 `student_id` 取自档案而不是请求体。
        care_case_id=care_case.id,
        student_id=care_case.student_id,
    )
    risk_event.status = "REVIEWED"
    risk_event.reviewed_by = user.id
    risk_event.reviewed_at = now_utc_naive()
    from_status = care_case.status
    care_case.status = "FOLLOWING"
    care_case.owner_id = user.id
    _bump_case_version(care_case)
    db.add(review)
    db.flush()
    record_case_event(
        db,
        care_case,
        MANUAL_REVIEWED,
        user.id,
        from_status=from_status,
        to_status="FOLLOWING",
        reason=payload.next_action,
        confirmed_facts=payload.confirmed_facts,
    )
    db.flush()
    return review


def create_follow_up(db: Session, user: UserAccount, case_id: int, payload: FollowUpRequest) -> FollowUpRecord:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    _ensure_open(care_case)
    followup = FollowUpRecord(
        student_id=care_case.student_id,
        operator_id=user.id,
        record_type=payload.record_type,
        confirmed_facts=payload.confirmed_facts,
        next_follow_up_date=payload.next_follow_up_date,
        status="ACTIVE",
        care_case_id=care_case.id,
    )
    from_status = care_case.status
    care_case.status = "FOLLOWING"
    care_case.owner_id = user.id
    _bump_case_version(care_case)
    db.add(followup)
    db.flush()
    record_case_event(
        db,
        care_case,
        FOLLOW_UP_ADDED,
        user.id,
        from_status=from_status,
        to_status="FOLLOWING",
        reason=payload.record_type,
        confirmed_facts=payload.confirmed_facts,
    )
    db.flush()
    return followup


def create_family_contact(
    db: Session, user: UserAccount, case_id: int, payload: FamilyContactRequest
) -> FamilyContactRecord:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    _ensure_open(care_case)
    record = FamilyContactRecord(
        student_id=care_case.student_id,
        operator_id=user.id,
        contact_date=payload.contact_date,
        contact_person=payload.contact_person,
        channel=payload.channel,
        result=payload.result,
        support_status=payload.support_status,
        confirmed_facts=payload.confirmed_facts,
        next_contact_date=payload.next_contact_date,
        care_case_id=care_case.id,
    )
    from_status = care_case.status
    care_case.status = "FOLLOWING"
    care_case.owner_id = user.id
    _bump_case_version(care_case)
    db.add(record)
    db.flush()
    record_case_event(
        db,
        care_case,
        FAMILY_CONTACT_ADDED,
        user.id,
        from_status=from_status,
        to_status="FOLLOWING",
        reason=payload.channel,
        # **家庭回访正文不进事件**（§16.6：审计与事件都不得存家庭回访正文）。
        # 它留在 `family_contact_record.confirmed_facts` 上，那一行才是它的家。
    )
    db.flush()
    return record


def create_retest_plan(db: Session, user: UserAccount, case_id: int, payload: RetestPlanRequest) -> RetestPlan:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    _ensure_open(care_case)
    # 复测以「最近一次施测」为基线，口径同个案详情的「本次测评」（不是 id 最大的一场）。
    baseline = latest_session(db, care_case.student_id)
    retest = RetestPlan(
        student_id=care_case.student_id,
        source_session_id=baseline.id if baseline else None,
        planned_date=payload.planned_date,
        reason=payload.reason,
        status="PLANNED",
        created_by=user.id,
        care_case_id=care_case.id,
    )
    from_status = care_case.status
    care_case.status = "OBSERVING"
    care_case.owner_id = user.id
    _bump_case_version(care_case)
    db.add(retest)
    db.flush()
    record_case_event(
        db,
        care_case,
        RETEST_PLANNED,
        user.id,
        from_status=from_status,
        to_status="OBSERVING",
        reason=payload.reason,
    )
    db.flush()
    return retest


def close_case(db: Session, user: UserAccount, case_id: int, payload: CloseCaseRequest) -> StudentCareCase:
    ensure_counselor(db, user)
    if not payload.confirm_follow_up_checked:
        raise AppError("VALIDATION_ERROR", "关闭前必须确认已检查后续安排", 422)
    care_case = _scoped_case(db, user, case_id)
    _check_case_version(care_case, payload.case_version)
    if care_case.status == "CLOSED":
        raise AppError("CONFLICT", "这份关注档案已经关闭了。", 409)
    from_status = care_case.status
    care_case.status = "CLOSED"
    care_case.closed_at = now_utc_naive()
    care_case.close_reason = payload.close_reason
    care_case.close_note = payload.close_note
    # `closed_by` 与 `reopened_by` 在 V1.2 对齐阶段就加好了列，而**这一期之前没有
    # 任何东西写它们**：`owner_id` 在被关掉的那一刻被设成关档人，于是「谁关的」
    # 只在「关档人恰好也是负责人」时才答得上来。这两列才是那个问题的答案，
    # 而它们不该靠 `owner_id` 兼职（关档之后负责人可能被转派走）。
    care_case.closed_by = user.id
    care_case.owner_id = user.id
    _bump_case_version(care_case)

    # ★ 关档必须把这份档案名下仍挂着的跟进记录一并结束掉（2026-09-20 加）。
    #
    # 这两件事此前是各做各的：`close_case` 只改档案自己的状态，`follow_up_record`
    # 那些 `ACTIVE` 的行原地不动。而 `analytics_service.counselor_reminders` 只筛
    # `status == "ACTIVE"` 与到期日，**它不认识档案状态**——于是工作台会给一份
    # 已经了结的档案继续发提醒，界面上写着「已逾期 N 天」。这与 §1 那条
    # 「一条 CLOSED 的档案没有 `next_follow_up_date` 可看」自相矛盾：档案关了，
    # 而提醒还在催。工作台那 12 份档案里有 3 份已经是 CLOSED，没人看得出来。
    #
    # 另一面是重复：`reopen_case` 每次都会建一条当天到期的「重新打开档案」，
    # 那是它有意为之的语义（重新打开 = 今天跟进一次）。关档不收回旧的那些时，
    # **关闭 / 重开一次就多一条一模一样的提醒**。实测演示库里 17 条 ACTIVE
    # 全部出自这条路，19 条提醒里只有 11 个不同的标题（钱浩然 ×4、郑浩然 ×4）。
    #
    # 收回成 `CLOSED` 而**不是删除**：那些行是确实做过的跟进记录，
    # §1「关闭档案不得删除历史记录」在这儿照样成立。`FOLLOW_UP_STATUS_LABELS`
    # 里 `CLOSED`（「已结束」）一直都在，所以这一改不动词汇表（§3 那四个面）。
    #
    # 作用域只取这一份档案：同一名学生可以同时有已关闭的旧档案与在办的新档案
    # （§1 那条时序），关掉旧的那一份不该把新那一条的待办一起收走。
    # `reopen_case` 与 `create_follow_up` 两个写入方都写 `care_case_id`，
    # 所以这个条件覆盖得住现在与将来；历史上没有 `care_case_id` 的行不存在
    # （2026-09-20 在开发库上数过：17 行 ACTIVE，`care_case_id IS NULL` 的 0 行）。
    db.execute(
        update(FollowUpRecord)
        .where(
            FollowUpRecord.care_case_id == care_case.id,
            FollowUpRecord.status == "ACTIVE",
        )
        .values(status="CLOSED")
    )
    db.flush()
    record_case_event(
        db,
        care_case,
        CASE_CLOSED,
        user.id,
        from_status=from_status,
        to_status="CLOSED",
        reason=payload.close_reason,
        # **关闭说明不进事件**：`close_note` 是关档人写下的判断，它的家在
        # `student_care_case.close_note` 上（详情页直接渲染它）。
        # 事件是「这份档案发生了什么」，不是它的副本。
    )
    db.flush()
    return care_case


def reopen_case(db: Session, user: UserAccount, case_id: int, payload: ReopenCaseRequest) -> StudentCareCase:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    _check_case_version(care_case, payload.case_version)

    # ★ 这一步是这一期修掉的 500 的来源。
    #
    # `uq_care_case_one_active_per_student`（生成列 `active_student_id`）断言
    # 「同一名学生最多一条非 CLOSED 的档案」。秋季关掉一条、春季再开一条是学校每年
    # 都会遇到的时序（§1），此时点「重新打开」那条**旧的**，无条件设 FOLLOWING 会
    # 让两行落在同一个 `active_student_id` 上 → IntegrityError → 用户拿到 500，
    # 而错误里只有一句英文。0014 的 precheck 把「迁移时库里已经有多份在办」挡在
    # 迁移之前，挡不住**迁移之后**这个动作。
    #
    # 处置是「先查再改」：这不是一个可以自动决定的问题（要不要把现在那条在办的
    # 关掉？那是另一件有代价的事），所以答案是把事实说清楚，让老师自己选。
    active = db.scalar(
        select(StudentCareCase).where(
            StudentCareCase.student_id == care_case.student_id,
            StudentCareCase.status != "CLOSED",
            StudentCareCase.id != care_case.id,
        )
    )
    if active:
        raise AppError(
            "CONFLICT",
            f"这名学生已经有一条在办档案（编号 {active.id}），所以不能再打开这一条。"
            "如果你要办的是他现在的状况，请到那一条上继续；"
            "如果那一条已经了结，先把它关闭。",
            409,
        )

    # 已关闭的档案不接受「重新打开」，未关闭的档案也不接受。
    if care_case.status != "CLOSED":
        raise AppError("CONFLICT", "这份关注档案本来就在办，不需要重新打开。", 409)

    from_status = care_case.status
    care_case.status = "FOLLOWING"
    care_case.reopened_at = now_utc_naive()
    care_case.reopened_by = user.id
    care_case.reopen_reason = payload.reason[:REOPEN_REASON_COLUMN_LIMIT]
    # `reopened_by` 与 `reopen_reason` 与 `closed_by` 同理：这一期之前没有写入方。
    care_case.owner_id = user.id
    _bump_case_version(care_case)
    # **`closed_at` / `close_reason` / `close_note` 刻意不清空**（2026-09-19 裁决）。
    # 它们与 `reopened_at` 并存，而这不是「状态不一致」：它们回答的是
    # 「**上一次是怎么关的**」，那是确实发生过的事。清掉等于抹掉一段历史——
    # 与 §1「关闭档案不得删除历史记录」是同一条。上一轮的关档说明、这一轮的
    # 重开原因、以及两者之间隔了多久，三条一起才读得出这条时序。
    followup = FollowUpRecord(
        student_id=care_case.student_id,
        operator_id=user.id,
        record_type="重新打开档案",
        confirmed_facts=payload.reason,
        next_follow_up_date=now_utc_naive().date(),
        status="ACTIVE",
        care_case_id=care_case.id,
    )
    db.add(followup)
    db.flush()
    record_case_event(
        db,
        care_case,
        CASE_REOPENED,
        user.id,
        from_status=from_status,
        to_status="FOLLOWING",
        reason=payload.reason[:REOPEN_REASON_COLUMN_LIMIT],
        # 原文（可能比 255 长）留在上面那条跟进记录上，那是 Text。
    )
    db.flush()
    return care_case
