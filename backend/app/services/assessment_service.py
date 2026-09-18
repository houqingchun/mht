from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    DimensionResult,
    RiskEvent,
)
from app.models.care import StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import ScaleEngine, ScaleQuestionConfig, rule_config_from_json
from app.services.task_service import effective_task_status, task_target_counts


def now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def latest_session_order() -> tuple:
    """「这个学生最新的一场测评」的唯一定义：先按**施测时间**，再按 id 兜底。

    不能只按 id。导入的会话 `submitted_at` 是文件里的测评日期，而 `id` 是导入那一刻分配的——
    学校把上学期的普查结果导进来，它的 id 比本学期已经在系统内测过的那场更大，按 id 取就会
    把去年那份当成「本次测评」，同时让个案详情、重点学生列表与受控导出**一致地**取错那一条
    （`export_service` 的注释记着它与个案详情刻意对齐，所以这些调用点必须一起改）。

    `submitted_at.is_(None)` 排在最前，是为了让**还没交卷**的会话排在已交卷的后面：SQLite
    与 MySQL 都把 False 排在 True 前面，所以这一列升序 = 已交卷优先。一个只有未交卷会话的
    学生照样取得到那一场（它是唯一一条），不会被丢成「没有测评」。

    两种用法共用这一个排序：按对象取用 `latest_session()`，在相关子查询里取用
    `.order_by(*latest_session_order()).limit(1).scalar_subquery()`。
    """
    return (
        AssessmentSession.submitted_at.is_(None),
        AssessmentSession.submitted_at.desc(),
        AssessmentSession.id.desc(),
    )


def latest_session(db: Session, student_id: int) -> AssessmentSession | None:
    """The student's most recent sitting — see `latest_session_order` for 口径。"""
    return db.scalar(
        select(AssessmentSession)
        .where(AssessmentSession.student_id == student_id)
        .order_by(*latest_session_order())
        .limit(1)
    )


def session_history_order() -> tuple:
    """`latest_session_order()` 的**升序**版，给「历次趋势」用：从旧到新。

    同一条口径（施测时间优先、id 兜底），只是方向相反——两个方向必须成对，否则「最新一场」
    与「趋势的最后一个点」会指向不同的两场会话。没有 `submitted_at` 的旧行仍然排在最前，
    也就是趋势的最左端：它们只可能是最老的记录。
    """
    return (
        AssessmentSession.submitted_at.is_(None),
        AssessmentSession.submitted_at.asc(),
        AssessmentSession.id.asc(),
    )


def session_duration_seconds(answer_times: list[datetime], submitted_at: datetime) -> int | None:
    """Seconds from the student's first answer to submitting.

    Measured from the earliest answer rather than from `started_at` on purpose:
    `started_at` is when the task was opened, so a student who opens it and comes
    back the next day would otherwise record a day-long "assessment". Every answer
    row carries its own timestamp, so the earliest one is the honest beginning.

    Returns None when there is nothing to measure from — a session submitted with
    no answers recorded, which `validate_answers` normally rules out.

    Clamped at zero but NOT at the top. A negative value means an answer is stamped
    later than the submission (clock skew, or a seeder passing a future
    `answered_at`) and is nonsense rather than a measurement, so it becomes 0. A
    *large* value is not clamped: a student who answers a few items on Monday and
    submits on Friday really did take four days of wall-clock, and rounding that
    down would destroy the only record of it. That figure is wall-clock, not
    engagement — format it as 小时/天 rather than presenting it as 用时 seconds.
    """
    if not answer_times:
        return None
    return max(0, int((submitted_at - min(answer_times)).total_seconds()))


def answer_times_for_session(db: Session, session_id: int) -> list[datetime]:
    """When each of this session's answers was first recorded.

    `answered_at` is write-once (see `save_answer`), so these do not move once
    written and the minimum is stable across submissions.
    """
    return [
        answered_at
        for answered_at in db.scalars(
            select(AssessmentAnswer.answered_at).where(AssessmentAnswer.session_id == session_id)
        ).all()
        if answered_at is not None
    ]


def _stamp_submission(db: Session, session: AssessmentSession, answer_times: list[datetime]) -> None:
    """Close the sitting: record when it ended, how long it took, and that the
    task target is done. Shared by the fresh-submit path and the post-reset
    re-submit path so the two cannot drift apart."""
    session.submitted_at = now_utc_naive()
    session.duration_seconds = session_duration_seconds(answer_times, session.submitted_at)
    target = db.scalar(
        select(AssessmentTarget).where(
            AssessmentTarget.task_id == session.task_id,
            AssessmentTarget.student_id == session.student_id,
        )
    )
    if target:
        target.status = "COMPLETED"
        target.completed_at = session.submitted_at


def student_for_user(db: Session, user: UserAccount) -> Student:
    if user.role_code != RoleCode.STUDENT:
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    student = db.scalar(select(Student).where(Student.student_no == user.account))
    if not student:
        raise AppError("SCOPE_FORBIDDEN", "未找到当前学生数据范围", 403)
    return student


def list_student_tasks(db: Session, user: UserAccount) -> list[dict]:
    student = student_for_user(db, user)
    rows = db.execute(
        select(AssessmentTask, AssessmentTarget, AssessmentSession)
        .join(AssessmentTarget, AssessmentTarget.task_id == AssessmentTask.id)
        .outerjoin(
            AssessmentSession,
            (AssessmentSession.task_id == AssessmentTask.id) & (AssessmentSession.student_id == student.id),
        )
        .where(AssessmentTarget.student_id == student.id)
        .order_by(AssessmentTask.id.desc())
    ).all()
    result = []
    for task, target, session in rows:
        answered_count = 0
        if session:
            answered_count = db.scalar(
                select(func.count(AssessmentAnswer.id)).where(AssessmentAnswer.session_id == session.id)
            )
        total_targets, completed_targets = task_target_counts(db, task.id)
        result.append(
            {
                "id": task.id,
                "task_no": task.task_no,
                "name": task.name,
                # 与心理老师看到的那个列表用同一个判据（`effective_task_status`）。
                # 学生端现在不显示这一项（`StudentHomePage.vue` 按 `target_status`
                # 判断自己的进度），但同一个后端不该给两个人两种「这场测评的状态」。
                "status": effective_task_status(
                    task, total_targets=total_targets, completed_targets=completed_targets
                ),
                "target_status": target.status,
                "start_at": task.start_at.isoformat() if task.start_at else None,
                "end_at": task.end_at.isoformat() if task.end_at else None,
                "session_id": session.id if session else None,
                "answered_count": answered_count or 0,
                "completed_at": target.completed_at.isoformat() if target.completed_at else None,
            }
        )
    return result


def list_student_assessment_history(db: Session, user: UserAccount) -> list[dict]:
    """The student's own completion history. Never exposes scores or risk labels."""
    student = student_for_user(db, user)
    rows = db.execute(
        select(AssessmentTask, AssessmentTarget, AssessmentSession)
        .join(AssessmentTarget, AssessmentTarget.task_id == AssessmentTask.id)
        .outerjoin(
            AssessmentSession,
            (AssessmentSession.task_id == AssessmentTask.id) & (AssessmentSession.student_id == student.id),
        )
        .where(AssessmentTarget.student_id == student.id)
        .order_by(AssessmentTask.id.desc())
    ).all()
    items = []
    for task, target, session in rows:
        answered_count = 0
        if session:
            answered_count = db.scalar(
                select(func.count(AssessmentAnswer.id)).where(AssessmentAnswer.session_id == session.id)
            )
        items.append(
            {
                "task_no": task.task_no,
                "task_name": task.name,
                "status": target.status,
                "answered_count": answered_count or 0,
                "submitted_at": session.submitted_at.isoformat() if session and session.submitted_at else None,
                "duration_seconds": session.duration_seconds if session else None,
                # 学生也该知道这场测评不是在本系统里做的。他在自己的记录里看到一条
                # 不记得答过的「已完成」，唯一的解释就是这一列。
                "source": session.source if session else None,
            }
        )
    return items


def create_or_get_session(db: Session, user: UserAccount, task_id: int) -> AssessmentSession:
    student = student_for_user(db, user)
    target = db.scalar(
        select(AssessmentTarget).where(AssessmentTarget.task_id == task_id, AssessmentTarget.student_id == student.id)
    )
    if not target:
        raise AppError("SCOPE_FORBIDDEN", "当前学生不在该测评任务范围内", 403)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在或不可用", 404)

    session = db.scalar(
        select(AssessmentSession).where(AssessmentSession.task_id == task.id, AssessmentSession.student_id == student.id)
    )
    if session:
        # 导入的会话（数据中心 → MHT测评记录导入）属于「已完成的既有记录」，不是一份
        # 可以继续作答的卷子。不挡的话，学生在任务列表里看到那个已完成的批次任务后手敲
        # `/student/assessment/{taskId}` 就能把它拿回来：一份 100% 预填的外部答卷、
        # 一个能点出「测评已成功提交」的按钮，而答题页还会把 localStorage 里**不分会话**
        # 的旧状态叠上去（`StudentAssessmentPage.vue` 的 loadLocalState）。
        # 写入本来就被 `save_answer` 的状态检查拦住，但「没损坏」低估了这件事：那是把
        # 外部来源的答卷当成系统内作答呈现给学生，并给他一个假的成功反馈。
        if session.source == "IMPORTED":
            raise AppError("SESSION_LOCKED", "该测评由学校导入，不能在系统内作答", 409)
        return session

    # 「这场测评现在还开着吗」——**只在要新开一张卷子时问**（2026-09-17）。
    # 这一次顺序调换不是随手改的：原来这道门挡在会话查询之前，一旦状态能推出
    # `CLOSED`（过了截止日期、或者全部答完），它就会把**已经答到一半的学生**也一起
    # 挡在外面——窗口关掉那一刻他不会「维持现状」，而是被从自己的卷子上踢出去。
    # 截止日期管的是「能不能再开一份新卷子」，不是「手里这份还算不算数」。
    # 判据与列表上显示的那个状态是同一个函数，所以界面说「已结束」时这里就真的开不了，
    # 不会各说各话。
    total_targets, completed_targets = task_target_counts(db, task.id)
    if (
        effective_task_status(
            task, total_targets=total_targets, completed_targets=completed_targets
        )
        != "ACTIVE"
    ):
        raise AppError("NOT_FOUND", "测评任务不存在或不可用", 404)

    scale = db.get(AssessmentScale, task.scale_id)
    if not scale:
        raise AppError("SCALE_INVALID", "量表不存在", 422)

    session = AssessmentSession(
        task_id=task.id,
        student_id=student.id,
        scale_id=scale.id,
        scale_version=scale.version,
        started_at=now_utc_naive(),
        status="IN_PROGRESS",
    )
    target.status = "IN_PROGRESS"
    db.add(session)
    db.flush()
    return session


def get_student_session(db: Session, user: UserAccount, session_id: int) -> AssessmentSession:
    student = student_for_user(db, user)
    session = db.get(AssessmentSession, session_id)
    if not session or session.student_id != student.id:
        raise AppError("SCOPE_FORBIDDEN", "当前学生无权访问该答题会话", 403)
    return session


def save_answer(
    db: Session,
    user: UserAccount,
    session_id: int,
    question_no: int,
    answer: str,
    *,
    answered_at: datetime | None = None,
) -> AssessmentSession:
    """Record one answer.

    `answered_at` defaults to now and exists so the demo seeder can spread a
    fabricated sitting over a plausible window while still going through the
    service layer (`seed_demo` writes nothing directly).
    """
    if question_no < 1 or question_no > 100:
        raise AppError("VALIDATION_ERROR", "题号必须在1-100之间", 422)
    session = get_student_session(db, user, session_id)
    if session.status in {"SUBMITTED", "CALCULATED", "QUESTIONABLE"}:
        raise AppError("SESSION_LOCKED", "答卷已提交，不能修改答案", 409)
    question = db.scalar(
        select(ScaleQuestion).where(ScaleQuestion.scale_id == session.scale_id, ScaleQuestion.question_no == question_no)
    )
    if not question:
        raise AppError("SCALE_INVALID", "题目不存在", 422)
    score = 1 if answer == "YES" else 0
    timestamp = answered_at or now_utc_naive()
    existing = db.scalar(
        select(AssessmentAnswer).where(
            AssessmentAnswer.session_id == session.id,
            AssessmentAnswer.question_id == question.id,
        )
    )
    if existing:
        existing.answer = answer
        existing.score = score
        # Deliberately NOT re-stamping `answered_at`. It means "when this question
        # was first answered", and the session's duration is measured from the
        # earliest of them — re-stamping would let a student who goes back and
        # edits question 1 collapse their own recorded duration.
    else:
        db.add(
            AssessmentAnswer(
                session_id=session.id,
                question_id=question.id,
                answer=answer,
                score=score,
                answered_at=timestamp,
            )
        )
    db.flush()
    return session


def list_session_questions(db: Session, user: UserAccount, session_id: int) -> list[dict]:
    """Question stems for the session's scale version.

    Deliberately returns no dimension codes or key-question flags — students must
    not be able to infer what a question measures.
    """
    session = get_student_session(db, user, session_id)
    questions = db.scalars(
        select(ScaleQuestion)
        .where(ScaleQuestion.scale_id == session.scale_id, ScaleQuestion.status == "ACTIVE")
        .order_by(ScaleQuestion.question_no)
    ).all()
    return [{"question_no": q.question_no, "question_text": q.question_text} for q in questions]


def session_payload(db: Session, session: AssessmentSession) -> dict:
    rows = db.execute(
        select(ScaleQuestion.question_no, AssessmentAnswer.answer)
        .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
        .where(AssessmentAnswer.session_id == session.id)
    ).all()
    answer_map = {question_no: answer for question_no, answer in rows}
    # 题号来自这个量表的题目，不是 `range(1, 101)`：写死 100 的话，题库一换成不是
    # 100 题的版本，这个字段就答不上「第一道未答题是几」——它会指着一个不存在的题号，
    # 或者漏掉 100 之后那几道。取数与 `list_session_questions`（学生页的题干来源）
    # 同源，所以「跳到下一道没答的题」一定落在学生真看得见的那份题单里。
    #
    # 注意这里**不是** `submit_session` 的完整性判据：那一条在引擎里
    # （`scale_engine/engine.py:validate_answers`），判据是规则版本的 `question_count`
    # （§6：阈值属于规则版本）。两处判「有多少题」用了不同的来源，是因为它们回答的
    # 是两个问题——这里答「学生下一页该看第几题」（跟着题目走），
    # 那里答「这份答卷够不够算分」（跟着规则走）。
    question_nos = db.scalars(
        select(ScaleQuestion.question_no)
        .where(ScaleQuestion.scale_id == session.scale_id, ScaleQuestion.status == "ACTIVE")
        .order_by(ScaleQuestion.question_no)
    ).all()
    missing = [question_no for question_no in question_nos if question_no not in answer_map]
    return {
        "id": session.id,
        "task_id": session.task_id,
        "student_id": session.student_id,
        "scale_id": session.scale_id,
        "scale_version": session.scale_version,
        "status": session.status,
        "current_question_no": missing[0] if missing else None,
        "answered_count": len(answer_map),
        "answers": answer_map,
    }


def calculate_session(db: Session, session: AssessmentSession, answer_map: dict[int, str]):
    """Score one session's answers against the rule in force for its scale version.

    Split out of `submit_session` so the imported-record path (数据中心 → MHT测评记录
    导入) scores with the same code: an imported result that came out of a second
    implementation would be a different *kind* of fact wearing the same columns.

    Only the pure part is shared — questions, the ACTIVE rule row, the engine call.
    Everything about *bookkeeping* (timestamps, the target row, the idempotency key)
    stays with each caller, because an imported sitting and a live one genuinely
    differ there and a parameterised-to-taste single function would hide that.
    """
    questions = db.scalars(
        select(ScaleQuestion).where(ScaleQuestion.scale_id == session.scale_id).order_by(ScaleQuestion.question_no)
    ).all()
    scale_rule = db.scalar(
        select(ScaleRule)
        .where(ScaleRule.scale_id == session.scale_id, ScaleRule.rule_type == "MHT_SCORING", ScaleRule.status == "ACTIVE")
        .order_by(ScaleRule.id.desc())
    )
    rule_version = scale_rule.rule_version if scale_rule else "MHT-RULE-1.0.0"
    # Thresholds come from the version's rule row. This is what makes the rule
    # config authoritative: previously `config_json` was written but never read,
    # so editing it changed nothing. It is versioned alongside the instrument so
    # a later threshold change cannot retroactively reinterpret this result.
    return ScaleEngine(
        scale_version=session.scale_version,
        rule_version=rule_version,
        config=rule_config_from_json(scale_rule.config_json if scale_rule else None),
    ).calculate(
        [
            ScaleQuestionConfig(
                question_no=question.question_no,
                dimension_code=question.dimension_code,
                is_validity_question=question.is_validity_question,
                is_key_question=question.is_key_question,
            )
            for question in questions
        ],
        answer_map,
    )


def session_status_for(calculation) -> str:
    """A valid answer sheet is CALCULATED; an invalid one is QUESTIONABLE.

    A named function rather than an inline conditional at each call site: both the
    live and the imported path have to make this same call, and a divergence would
    show up only as a status the UI renders oddly.
    """
    return "CALCULATED" if calculation.validity_status == "VALID" else "QUESTIONABLE"


def persist_result_and_dimensions(db: Session, session: AssessmentSession, calculation) -> None:
    """Write the 量表计算事实 layer: the result row and its eight dimension rows.

    `calculated_at` is 现在 — literally when this was computed. For an imported
    sitting that is not the test date; the test date lives on
    `session.submitted_at`, and conflating the two would erase the difference
    between 「这份记录是哪天测的」 and 「它是什么时候被算出来的」.
    """
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=calculation.validity_score,
            validity_status=calculation.validity_status,
            total_score=calculation.total_score,
            total_level=calculation.total_level,
            rule_version=calculation.rule_version,
            calculated_at=now_utc_naive(),
        )
    )
    for dimension in calculation.dimension_results:
        db.add(
            DimensionResult(
                session_id=session.id,
                dimension_code=dimension.dimension_code,
                score=dimension.score,
                level=dimension.level,
                interpretation=dimension.interpretation,
                rule_version=dimension.rule_version,
            )
        )


def maybe_raise_risk_events(db: Session, session: AssessmentSession, calculation) -> None:
    """The **only** place that writes 学校管理事实 from a scoring result.

    Both scoring paths call it — the student's own submit (`submit_session`) and
    the external-record import (`commit_assessment_import`). That is the point of
    isolating it: 「什么情况下算重点学生」 is answered by the engine's
    `calculation.risk_events` and nothing else, so the two paths cannot drift into
    disagreeing about the same answer sheet.

    2026-09-17 用户确认导入路径也要触发（此前是「只带进结果」，已撤回）。所以这里
    不再有「哪条路径不许调」的例外；真要区分就该改 `calculation.risk_events` 的来源，
    而不是在调用点上加分支。

    Only 重点题（85/97）命中写行——`total_level == KEY_ATTENTION` 本身不触发任何写。
    按总分放宽会让同一份答卷在系统内与导入后得到不同结果，那是比漏报更难查的不一致。
    """
    for risk_event in calculation.risk_events:
        db.add(
            RiskEvent(
                student_id=session.student_id,
                session_id=session.id,
                risk_type=risk_event.risk_type,
                risk_level=risk_event.risk_level,
                trigger_rule=risk_event.trigger_rule,
                status="PENDING",
                created_at=now_utc_naive(),
            )
        )
        open_or_reuse_care_case(db, session.student_id)


def submit_session(db: Session, user: UserAccount, session_id: int, idempotency_key: str | None = None) -> dict:
    """Submit an assessment session.

    Idempotency has two layers:

    * Re-submitting an already-processed session returns the stored result, so a
      network retry can never double-score. This is the guarantee clients rely on
      and it does not depend on the header.
    * When BOTH the stored and the incoming `Idempotency-Key` are present and
      differ, the caller is submitting the same session as if it were a new
      operation — that is a genuine conflict, not a retry. Reported as
      `IDEMPOTENCY_CONFLICT` (previously declared in the error contract but
      never raised).

    A missing key is treated as a plain retry, so clients that omit the header
    keep working.
    """
    session = get_student_session(db, user, session_id)
    existing_result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session.id))
    if existing_result:
        if (
            idempotency_key
            and session.idempotency_key
            and idempotency_key != session.idempotency_key
        ):
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "该答题会话已提交，且幂等键不一致",
                409,
            )
        # A reset-then-retake arrives here with the timing facts erased: /reset
        # clears `submitted_at` and `duration_seconds` but keeps the result row
        # (deleting it would erase scored facts the audit trail and any manual
        # review point at — CLAUDE.md §1). Returning without re-deriving them would
        # leave a genuinely re-submitted session permanently undated, and the retake
        # would show 用时「—」 on every surface. Scoring is untouched: the stored
        # result is still what comes back.
        if session.submitted_at is None:
            _stamp_submission(db, session, answer_times_for_session(db, session.id))
        return result_payload(db, session)

    answers = db.execute(
        select(ScaleQuestion.question_no, AssessmentAnswer.answer)
        .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
        .where(AssessmentAnswer.session_id == session.id)
    ).all()
    answer_map = {question_no: answer for question_no, answer in answers}
    # 完整性由 `calculate_session` → 引擎的 `validate_answers` 判（`ANSWERS_INCOMPLETE`），
    # 判据是**规则版本里的 `question_count`**，不是这里再数一遍——§6 那条
    # 「阈值属于量表规则版本」同样适用：「这一版有多少题」只有一个来源。
    # 2026-09-17 查过一遍，确认服务端一直拦得住半份答卷（拦住它的是引擎，
    # 不是学生页那个写死 100 的计数器；那一个是客户端自己的门）。
    calculation = calculate_session(db, session, answer_map)
    persist_result_and_dimensions(db, session, calculation)
    maybe_raise_risk_events(db, session, calculation)

    session.status = session_status_for(calculation)
    _stamp_submission(db, session, answer_times_for_session(db, session.id))
    session.idempotency_key = idempotency_key
    db.flush()
    return result_payload(db, session)


def open_or_reuse_care_case(db: Session, student_id: int) -> StudentCareCase:
    care_case = db.scalar(
        select(StudentCareCase)
        .where(StudentCareCase.student_id == student_id, StudentCareCase.status != "CLOSED")
        .order_by(StudentCareCase.id.desc())
    )
    if care_case:
        return care_case
    care_case = StudentCareCase(student_id=student_id, status="PENDING_REVIEW", opened_at=now_utc_naive())
    db.add(care_case)
    db.flush()
    return care_case


def result_payload(db: Session, session: AssessmentSession) -> dict:
    result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session.id))
    dimensions = db.scalars(select(DimensionResult).where(DimensionResult.session_id == session.id)).all()
    risk_events = db.scalars(select(RiskEvent).where(RiskEvent.session_id == session.id)).all()
    return {
        "session_id": session.id,
        "status": session.status,
        "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
        "duration_seconds": session.duration_seconds,
        "result": {
            "validity_score": result.validity_score,
            "validity_status": result.validity_status,
            "total_score": result.total_score,
            "total_level": result.total_level,
            "rule_version": result.rule_version,
        },
        "dimension_results": [
            {
                "dimension_code": dimension.dimension_code,
                "score": dimension.score,
                "level": dimension.level,
                "interpretation": dimension.interpretation,
                "rule_version": dimension.rule_version,
            }
            for dimension in dimensions
        ],
        "risk_events": [
            {
                "risk_type": risk.risk_type,
                "risk_level": risk.risk_level,
                "trigger_rule": risk.trigger_rule,
                "status": risk.status,
            }
            for risk in risk_events
        ],
    }
