from sqlalchemy import func, select
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
from app.models.care import FamilyContactRecord, FollowUpRecord, ManualReview, RetestPlan, StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import ScaleQuestion
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.security.permissions import SCOPED, STUDENT_PSYCH_DETAIL, scope_allows
from app.schemas.care import (
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


def batch_assign_owner(db: Session, user: UserAccount, case_ids: list[int], owner_id: int) -> dict:
    ensure_counselor(db, user)
    if not case_ids:
        raise AppError("VALIDATION_ERROR", "请至少选择一名学生", 422)
    owner = db.get(UserAccount, owner_id)
    if not owner or owner.role_code != RoleCode.COUNSELOR or not owner.active:
        raise AppError("VALIDATION_ERROR", "负责人必须是启用中的心理老师账号", 422)
    cases = db.scalars(select(StudentCareCase).where(StudentCareCase.id.in_(case_ids))).all()
    if not cases:
        raise AppError("NOT_FOUND", "未找到可分配的关注档案", 404)
    # Validate the whole batch before mutating any row: silently assigning only
    # the subset the caller can see would report a smaller count than requested,
    # with no indication of which ids were dropped.
    for care_case in cases:
        ensure_student_in_scope(db, user, care_case.student_id)
    for care_case in cases:
        care_case.owner_id = owner_id
    # Existing follow-up records are untouched — assignment only changes the owner.
    return {"updated": len(cases)}


def get_care_case(db: Session, user: UserAccount, student_id: int) -> dict:
    ensure_counselor(db, user)
    # Scope first: an out-of-range student must not learn whether a case exists.
    student = ensure_student_in_scope(db, user, student_id)
    # **已关闭的档案也要返回**（2026-09-17 修）。这里此前过滤掉 CLOSED，后果是
    # 「关闭」这个动作把自己脚下的页面抽掉了：`closeCase()` 成功之后紧接着
    # `await load()` 重新拉详情，拿到 404「关注档案不存在」——用户看到的是
    # 「刚点完关闭，系统说这档案不存在」，而它其实关好了，只是这一页不肯显示。
    # 同一个过滤还让详情页 `v-else` 的「重新打开档案」成了死代码（它的条件是
    # `case_status === 'CLOSED'`，而这一页永远拿不到 CLOSED），列表里 CLOSED 那行的
    # 「查看档案」也一律跳到 404——列表不过滤、详情过滤，两处口径本来就对不上。
    # 一条学生的档案可能不止一条（唯一键是 `(student_id, status)`），
    # 所以取 **id 最大**的那条，与 `assessment_service.open_or_reuse_care_case`
    # 选「当前档案」的口径一致：新档开出后它自然接替旧档，旧档仍留在库里。
    care_case = db.scalar(
        select(StudentCareCase)
        .where(StudentCareCase.student_id == student_id)
        .order_by(StudentCareCase.id.desc())
        .limit(1)
    )
    if not care_case:
        raise AppError("NOT_FOUND", "关注档案不存在", 404)
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
    # 该生敏感访问记录。仅统计本次迁移之后写入的行 —— 历史行的 student_id 为 NULL。
    audit_rows = db.execute(
        select(AuditLog, UserAccount)
        .outerjoin(UserAccount, UserAccount.id == AuditLog.actor_user_id)
        .where(AuditLog.student_id == student_id)
        .order_by(AuditLog.id.desc())
        .limit(100)
    ).all()
    return {
        "case_id": care_case.id,
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
        "case_status": care_case.status,
        "assessment": {
            "session_id": sitting.id if sitting else None,
            "submitted_at": sitting.submitted_at.isoformat() if sitting and sitting.submitted_at else None,
            "duration_seconds": sitting.duration_seconds if sitting else None,
            "total_level": result.total_level if result else None,
            "validity_status": result.validity_status if result else None,
            "rule_version": result.rule_version if result else None,
            # 这场测评是学生在本系统里做的，还是学校从外部平台导入的。个案详情页据此
            # 显示「来源」，因为「用时 5340 秒」在两种来源下是两件事——一份导入的记录
            # 没有本系统的作答过程，用时是那个平台自己报的数。
            "source": sitting.source if sitting else None,
        },
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
    )
    risk_event.status = "REVIEWED"
    risk_event.reviewed_by = user.id
    risk_event.reviewed_at = now_utc_naive()
    care_case.status = "FOLLOWING"
    care_case.owner_id = user.id
    db.add(review)
    db.flush()
    return review


def create_follow_up(db: Session, user: UserAccount, case_id: int, payload: FollowUpRequest) -> FollowUpRecord:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    followup = FollowUpRecord(
        student_id=care_case.student_id,
        operator_id=user.id,
        record_type=payload.record_type,
        confirmed_facts=payload.confirmed_facts,
        next_follow_up_date=payload.next_follow_up_date,
        status="ACTIVE",
    )
    care_case.status = "FOLLOWING"
    care_case.owner_id = user.id
    db.add(followup)
    db.flush()
    return followup


def create_family_contact(
    db: Session, user: UserAccount, case_id: int, payload: FamilyContactRequest
) -> FamilyContactRecord:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
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
    )
    care_case.status = "FOLLOWING"
    care_case.owner_id = user.id
    db.add(record)
    db.flush()
    return record


def create_retest_plan(db: Session, user: UserAccount, case_id: int, payload: RetestPlanRequest) -> RetestPlan:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    # 复测以「最近一次施测」为基线，口径同个案详情的「本次测评」（不是 id 最大的一场）。
    baseline = latest_session(db, care_case.student_id)
    retest = RetestPlan(
        student_id=care_case.student_id,
        source_session_id=baseline.id if baseline else None,
        planned_date=payload.planned_date,
        reason=payload.reason,
        status="PLANNED",
        created_by=user.id,
    )
    care_case.status = "OBSERVING"
    care_case.owner_id = user.id
    db.add(retest)
    db.flush()
    return retest


def close_case(db: Session, user: UserAccount, case_id: int, payload: CloseCaseRequest) -> StudentCareCase:
    ensure_counselor(db, user)
    if not payload.confirm_follow_up_checked:
        raise AppError("VALIDATION_ERROR", "关闭前必须确认已检查后续安排", 422)
    care_case = _scoped_case(db, user, case_id)
    care_case.status = "CLOSED"
    care_case.closed_at = now_utc_naive()
    care_case.close_reason = payload.close_reason
    care_case.close_note = payload.close_note
    care_case.owner_id = user.id
    db.flush()
    return care_case


def reopen_case(db: Session, user: UserAccount, case_id: int, payload: ReopenCaseRequest) -> StudentCareCase:
    ensure_counselor(db, user)
    care_case = _scoped_case(db, user, case_id)
    care_case.status = "FOLLOWING"
    care_case.reopened_at = now_utc_naive()
    care_case.owner_id = user.id
    db.add(
        FollowUpRecord(
            student_id=care_case.student_id,
            operator_id=user.id,
            record_type="重新打开档案",
            confirmed_facts=payload.reason,
            next_follow_up_date=now_utc_naive().date(),
            status="ACTIVE",
        )
    )
    db.flush()
    return care_case
