from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    # 「这一场算不算数」的唯一定义（§18.8）。定义在模型层，因为 `task_service`
    # 也要用它，而本模块反过来依赖着 `task_service`——反向 import 会成环。
    effective_session_predicate,
)
from app.models.care import StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import (
    ScaleEngine,
    ScaleQuestionConfig,
    requires_manual_review,
    rule_config_from_json,
    signal_type_for,
)
from app.security.data_scope import ensure_student_in_scope
from app.services.answer_snapshot import ANSWER_HASH_ALGORITHM, answer_snapshot_hash
from app.services.task_service import effective_task_status, task_target_counts


# `assessment_session.calculation_status` 的四个取值（V1.2 §16.8）。
#
# 它**不是** `session.status`：那一个是 `session_status_for()` 从效度推出来的
# （CALCULATED / QUESTIONABLE），回答「这份答卷算出来是什么」；这一个回答
# 「算出来了没有」。所以一份 `QUESTIONABLE` 的答卷照样是 `CALCULATED`——它算出来了，
# 只是算出来的结果存疑。
#
# 两个写入方（本校作答与外部导入）都从这里取，各写一份字面量就会有两种写法。
CALCULATION_PENDING = "PENDING"
CALCULATION_CALCULATING = "CALCULATING"
CALCULATION_CALCULATED = "CALCULATED"
CALCULATION_FAILED = "CALCULATION_FAILED"
CALCULATION_STATUSES = frozenset(
    {CALCULATION_PENDING, CALCULATION_CALCULATING, CALCULATION_CALCULATED, CALCULATION_FAILED}
)

# `assessment_session.tested_at_source`（那一列的 `server_default` 是
# `PENDING_VERIFICATION`，所以它排在这里是**第三种**取值，不是这两者的兜底）。
TESTED_AT_SOURCE_ONLINE_SUBMIT = "ONLINE_SUBMIT"
TESTED_AT_SOURCE_IMPORT_FILE = "IMPORT_FILE"
TESTED_AT_SOURCES = frozenset(
    {TESTED_AT_SOURCE_ONLINE_SUBMIT, TESTED_AT_SOURCE_IMPORT_FILE, "PENDING_VERIFICATION"}
)

# `calculation_error` 那一列是 VARCHAR(1000)，写入时截断。
CALCULATION_ERROR_MAX = 1000

# 引擎在校验**答卷本身**时抛的两个码：它们是「这份卷子现在还不该被算」，
# 不是「系统算不出来」。区别的代价是具体的——
#   * 走这一支：照原样抛回给答题的学生（422，引擎自己的那句话），`calculation_status`
#     退回原值。学生看到的仍然是「还有题没答」，与改动前逐字相同。
#   * 走另一支（`CALCULATION_FAILED`）：答卷被留下来、状态记在会话上、个案详情上出现
#     一个「重算评分」按钮——而对着**一份还没答完的卷子**给心理老师一个重试按钮，
#     是把「学生没答完」说成了一次系统故障。
CALCULATION_NOT_READY_CODES = frozenset({"ANSWERS_INCOMPLETE", "ANSWER_INVALID"})


def now_utc_naive() -> datetime:
    """「现在」，UTC，且**截断到整秒**——因为库里那些列只存得下整秒。

    这个函数写进去的每一列在 MySQL 上都是 `DATETIME`（fsp=0，`DateTime(timezone=True)`
    在 MySQL 上是空操作，见 `models/common.py`）。不截断就有两件事同时成立：

    - **写进去的值与读回来的值不是一个**。`POST /submit` 立刻回的是内存里那个对象
      （带微秒），同一份数据下一次 `GET` 回来是库里的值。同一个字段、两个值，
      而客户端没有办法知道哪个才算数。`test_assessment_api.py` 那条「同一个
      Idempotency-Key 重放回来的 payload 与第一次逐字相同」就是被这个打破的。
    - **MySQL 是四舍五入，不是截断**：`.7` 进到下一秒，于是存下来的 `submitted_at`
      可能比真实时刻**晚**半秒。`latest_session_order()` 按它排序，两场相隔不到
      一秒的提交就可能按一个没有发生过先后关系的值定序。截断是向下取整——
      并列仍然可能（那由既有的 `id` 兜底），但**顺序不会被颠倒**。

    没有损失任何精度：全库没有一列是 `DATETIME(6)`。
    """
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def latest_session_order() -> tuple:
    """「这个学生最新的一场测评」的唯一定义：先按**施测时间**，再按 id 兜底。

    不能只按 id。导入的会话 `submitted_at` 是文件里的测评日期，而 `id` 是导入那一刻分配的——
    学校把上学期的普查结果导进来，它的 id 比本学期已经在系统内测过的那场更大，按 id 取就会
    把去年那份当成「本次测评」，同时让个案详情、重点学生列表与受控导出**一致地**取错那一条
    （`export_service` 的注释记着它与个案详情刻意对齐，所以这些调用点必须一起改）。

    `submitted_at.is_(None)` 排在最前，是为了让**还没交卷**的会话排在已交卷的后面：
    MySQL 把 False 排在 True 前面，所以这一列升序 = 已交卷优先。一个只有未交卷会话的
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
    """The student's most recent sitting — see `latest_session_order` for 口径。

    **算数的那一场**：排序口径之外还要求 `is_effective`（见
    `effective_session_predicate`）。一名学生被降级过的会话比有效的那一场更新时，
    这里会回退到他更早的那一场——那正是四档处置要的结果。
    """
    return db.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.student_id == student_id,
            effective_session_predicate(),
        )
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
    re-submit path so the two cannot drift apart.

    交卷只能盖一次章。`submit_session` 的主路径在**算分之前**调它，而算分可能是
    「没算成」（`score_session` 返回 False，答卷被留下、等心理老师重试）。此后学生
    的客户端重发同一个请求、或者老师点重试之后学生又点了一次提交，都会再走到这里
    ——那时 `submitted_at` 已经有了，再盖一次会把它**往后推**（用时跟着变长，而
    「什么时候交的卷」是个已经发生过的事实）。所以判据在函数里，不在调用点上：
    这条路径上的每一处调用都不该自己再判一遍。
    """
    if session.submitted_at is not None:
        return
    session.submitted_at = now_utc_naive()
    # 这一场**测的是哪一天**。在线路径与导入路径唯一的分岔就在这里：导入的会话在
    # `assessment_import_service` 里写文件里那一天 + `IMPORT_FILE`（同一批文件的
    # 学生不是同一天测的，所以那一列不能改成「导入日期」）。第三档
    # `PENDING_VERIFICATION` 是那一列的 `server_default`，也是 V1.0 的历史行唯一诚实
    # 的说法——CLAUDE.md §21：`tested_at` 刻意不回填，因为历史行没人知道真实测评日。
    session.tested_at = session.submitted_at
    session.tested_at_source = TESTED_AT_SOURCE_ONLINE_SUBMIT
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
        # 外连接打在**有效的那一场**上（`effective_session_predicate`，§18.8）。
        # 在此之前 `(task_id, student_id)` 就是唯一的（V1.2 的 0014 之前那条
        # `uq_session_task_student`），所以这个连接不乘行；第 6 期起同一场比赛里
        # 可以有两场会话（`USE_EXTERNAL` / `KEEP_BOTH` 都保留在线那一场），
        # 不限定有效场的话**同一个任务会在列表里出现两次**，而两次的「已答 N 题」
        # 各说各话。限定之后每 (task, student) 至多匹配一场，与从前一样。
        # 两场都降级过（人工改库才可能出现）时这里给 NULL，界面显示「未开卷」。
        .outerjoin(
            AssessmentSession,
            (AssessmentSession.task_id == AssessmentTask.id)
            & (AssessmentSession.student_id == student.id)
            & effective_session_predicate(),
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
        # 外连接打在**有效的那一场**上（`effective_session_predicate`，§18.8）。
        # 在此之前 `(task_id, student_id)` 就是唯一的（V1.2 的 0014 之前那条
        # `uq_session_task_student`），所以这个连接不乘行；第 6 期起同一场比赛里
        # 可以有两场会话（`USE_EXTERNAL` / `KEEP_BOTH` 都保留在线那一场），
        # 不限定有效场的话**同一个任务会在列表里出现两次**，而两次的「已答 N 题」
        # 各说各话。限定之后每 (task, student) 至多匹配一场，与从前一样。
        # 两场都降级过（人工改库才可能出现）时这里给 NULL，界面显示「未开卷」。
        .outerjoin(
            AssessmentSession,
            (AssessmentSession.task_id == AssessmentTask.id)
            & (AssessmentSession.student_id == student.id)
            & effective_session_predicate(),
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
                # 「这份答卷学校那边处理到哪一步了」。分数仍然不下发（上面那句
                # 「never exposes scores or risk labels」一个字没变）——这一列不是分数，
                # 它是**「已完成」与「已算出来」的区别**：一名交完卷的学生在记录页上
                # 看到「已完成」，而这一场因为一次计算故障根本没算出来，他会以为
                # 一切都好。这一列是他唯一能看出差别的字。
                "calculation_status": session.calculation_status if session else None,
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

    # **取有效的那一场**（§18.8 之后同一场比赛里可能有两场，见 `attempt_no`）。
    # 不排序的话返回哪一场是未定义的（`scalar()` 静默取第一行），而两种答案的
    # 后果完全不同：取到被降级的那一场在线会话，学生看到的是自己那份**已经不算数**
    # 的卷子，而系统当前认的是外面导进来的那一份。
    #
    # 排序而不是过滤（`effective_session_predicate`）：过滤掉之后
    # 「一场有效的都没有」（人工改库才可能出现）会掉进下面那条**新开一张卷子**的
    # 路径，撞上 `uq_session_task_student_attempt`；排序最坏也就是退回旧行为。
    #
    # 首选有效场之后，下面那道 `source == "IMPORTED"` 的门同时回答了两档：
    # `USE_EXTERNAL` 之后有效场是导入的那一场 → 409「不能在系统内作答」，
    # 正是该给的答复；`KEEP_BOTH` 之后有效场仍是在线那一场 → 照旧返回给他。
    session = db.scalar(
        select(AssessmentSession)
        .where(
            AssessmentSession.task_id == task.id,
            AssessmentSession.student_id == student.id,
        )
        .order_by(AssessmentSession.is_effective.desc(), AssessmentSession.id.desc())
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
        # `school_id` 没有默认值（V1.2 那个 NOT NULL 列），必须由这里给。取
        # `student.school_id` 而不是 `task.school_id`：这一列记的是「这份卷子属于
        # 哪所学校」，与同一批复合外键的配对口径一致（`(school_id, student_id) →
        # student (school_id, id)`），而任务所属学校在转学场景下**不等于**学生
        # 现在所属的学校。写入侧与 `seed.py` / `task_service.py` 的
        # `school_id_snapshot` 同源。
        school_id=student.school_id,
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
    the external-record import (`commit_batch`). That is the point of
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
        # `signal_type` 没有 `server_default`（V1.2 的 NOT NULL 列），`rule_version`
        # 虽然在唯一键里但 V1.0 也没回填过——两列都必须在**这里**给值。
        #
        # `signal_type` 由 `signal_type_for` 从 `risk_type` 推出来，认不出就抛：
        # 猜一个默认值的代价是静默的（那条待办会落进另一类人的队列，界面上却一切正常）。
        # 它住在引擎里，与「什么情况下算重点学生只由 `calculation.risk_events` 回答」
        # 是同一条约定。
        #
        # `rule_version` 取 `calculation.rule_version`（= 这一场评分实际用的规则版本），
        # **不能听凭 `server_default='LEGACY_UNKNOWN'`**：新唯一键是
        # `(session_id, trigger_rule, rule_version)`，所有新行都顶着 `LEGACY_UNKNOWN`
        # 就等于那个键退化成 `(session_id, trigger_rule)`——规则升级后重算本该留下的
        # 第二条（按新标准判的那条）会撞 1062。CLAUDE.md §6 要的正是「旧行记录的是
        # 当时按什么标准判的」，而 `LEGACY_UNKNOWN` 只对**没有**这个信息的 V1.0 行诚实。
        signal_type = signal_type_for(risk_event.risk_type)
        db.add(
            RiskEvent(
                student_id=session.student_id,
                session_id=session.id,
                risk_type=risk_event.risk_type,
                risk_level=risk_event.risk_level,
                trigger_rule=risk_event.trigger_rule,
                status="PENDING",
                created_at=now_utc_naive(),
                signal_type=signal_type,
                requires_manual_review=requires_manual_review(signal_type),
                rule_version=calculation.rule_version,
            )
        )
        open_or_reuse_care_case(db, session.student_id)


def _calculate_and_hash(db: Session, session: AssessmentSession, answer_map: dict[int, str]):
    """纯计算：引擎结果 + 答卷快照摘要，**一行都不写**。

    两件事都在这里做完，是因为它们决定了这一次评分能不能落库：`score_session` 要
    在写任何一行之前知道这次成不成。次序也是刻意的——**先引擎、后哈希**：答卷本身
    的问题（少答、答案不是 yes/no）由引擎抛 `ANSWERS_INCOMPLETE` / `ANSWER_INVALID`
    这两个「还不该被算」的码，而哈希只在这之后才碰答案。反过来写的话，一份混进了
    非法答案的答卷会先撞上哈希里的分隔符校验（一个 `ValueError`），于是它被记成
    `CALCULATION_FAILED`——学生明明只是答案坏了，却得到一次「系统算不出来」。
    """
    scale = db.get(AssessmentScale, session.scale_id)
    if scale is None:
        raise AppError("SCALE_INVALID", "量表不存在", 422)
    calculation = calculate_session(db, session, answer_map)
    # 把「认不出的 risk_type」提到这里问一遍。`maybe_raise_risk_events` 里那个
    # `signal_type_for` 也会抛，但那是在结果行与**一部分**风险事件已经 add 进去之后
    # ——那时再抛，这一次评分已经写了一半（`open_or_reuse_care_case` 还会顺手 flush
    # 一条关怀档案），而它偏偏是最不该带着半份结果提交的那种失败。
    # 问法是引擎自己的判据（`SIGNAL_TYPE_BY_RISK_TYPE`），不另写一份。
    for risk_event in calculation.risk_events:
        signal_type_for(risk_event.risk_type)
    snapshot_hash = answer_snapshot_hash(
        scale_code=scale.code, scale_version=session.scale_version, answer_map=answer_map
    )
    return snapshot_hash, calculation


def _record_calculation_failure(db: Session, session: AssessmentSession, exc: BaseException) -> None:
    """把一次没算成的评分**记在会话上**，而不是让异常把答卷一起带走。

    `calculation_error` 里放的是异常的类型与原文（截断到列的宽度）。它进的是库、
    显示给心理老师，不是日志——这里只放「为什么算不出来」，不放答卷本身。
    """
    session.calculation_status = CALCULATION_FAILED
    session.calculation_error = f"{type(exc).__name__}: {exc}"[:CALCULATION_ERROR_MAX]
    db.flush()


def score_session(db: Session, session: AssessmentSession, answer_map: dict[int, str]) -> bool:
    """算这一场、落结果与维度、按判定开出待办，并维护 `calculation_status`。

    与 `calculate_session` 的分工：那一个是**纯函数调用**（引擎 + 规则，不写库），
    这一个是有状态的那一层（写库、状态机、失败留痕）。两条评分路径——学生自己交卷
    （`submit_session`）与外部记录导入（`assessment_import_service`）——都走这里，
    所以「一份答卷怎么变成结果」只有一种写法。

    返回 `True` 表示结果已经落库；`False` 表示这一次没算出来，此时
    `calculation_status == CALCULATION_FAILED`、`calculation_error` 里是原因，
    **返回而不是抛**。这个方向是刻意的：在线路径上，学生那份答卷是他花二十分钟做出来的
    事实，评分里的一个 bug 不该把它一起丢掉（抛出去 = 路由的 commit 不会发生 =
    答卷回滚 = 学生重做一遍）。所以他拿到的是一个 200，外加一句「评分没完成」。
    导入路径是这个结论的例外——那一条要求整批要么都进、要么都不进，调用方自己看
    返回值决定要不要收回整批（见 `_write_sheet`）。

    「前提不成立」不在此列：`ANSWERS_INCOMPLETE` / `ANSWER_INVALID` 照原样抛出，
    并把状态退回原值（见 `CALCULATION_NOT_READY_CODES`）。

    `calculation_status` 的三步（PENDING → CALCULATING → CALCULATED / FAILED）写在
    同一个事务里，所以 `CALCULATING` **不是一次认领**：并发的第二个请求根本读不到它
    （它在锁上等着），进程崩溃时它跟着事务一起回滚，谁也不会看见一行卡在中间状态。
    真正保证「同一场只算一次」的是行锁，见 `lock_session`。把中间态写成一次提交过的
    认领要连**租约与回收**一起写进来，而这个项目没有那一层。
    """
    previous_status = session.calculation_status
    session.calculation_status = CALCULATION_CALCULATING
    db.flush()
    try:
        snapshot_hash, calculation = _calculate_and_hash(db, session, answer_map)
    except AppError as exc:
        if exc.code in CALCULATION_NOT_READY_CODES:
            session.calculation_status = previous_status
            raise
        # 其余的 AppError（比如 `SCALE_INVALID`：规则版本要 100 道题而题库里只有 60）
        # 落到下面那一支——它们说的正是「这一版程序现在算不了这份卷子」，
        # 而那是一行该被记下来、该被人看见的失败，不是一次要弹回给学生看的 422。
        _record_calculation_failure(db, session, exc)
        return False
    except Exception as exc:  # noqa: BLE001 —— 见下
        # 宽是有意的：这一层的全部意义就是「算不出来也要留下痕迹」。
        # 认不出的异常（`signal_type_for` 的 `ValueError`、坏掉的规则 JSON 在
        # pydantic 里炸出来的 `ValidationError`）恰恰是最该被记下来的那一类——
        # 它们**说不出**一句给用户看的话，所以只能留下一行给下一个人。
        _record_calculation_failure(db, session, exc)
        return False

    session.answer_snapshot_hash = snapshot_hash
    session.answer_hash_algorithm = ANSWER_HASH_ALGORITHM
    persist_result_and_dimensions(db, session, calculation)
    maybe_raise_risk_events(db, session, calculation)
    session.status = session_status_for(calculation)
    session.calculation_status = CALCULATION_CALCULATED
    session.calculation_error = None
    db.flush()
    return True


def lock_session(db: Session, session_id: int) -> AssessmentSession:
    """取这一行并**锁到本次事务结束**（`SELECT … FOR UPDATE`）。

    并发互斥靠它，不靠 `calculation_status` 上的 compare-and-swap。取舍写在这里，
    因为两条路看起来都能用：CAS 要先写一个 `CALCULATING` 并**提交**，第二个请求才
    看得见它；而一旦提交，进程崩在中间就会把那一行永久留在 `CALCULATING` 上——没有
    任何东西会来回收它（这个项目没有调度器，也没有租约那一层设施）。行锁相反：
    连接断开时 MySQL 自己释放，排队等锁的那个请求拿到锁之后会**重新读一遍结果表**，
    看到结果就把它返回（幂等，正是客户端重发时要的结果）。

    调用方必须在**拿到锁之后**再查结果行，否则锁保护的是一个已经过期的判断。
    """
    return db.scalar(
        select(AssessmentSession).where(AssessmentSession.id == session_id).with_for_update()
    )


def retry_calculation(db: Session, user: UserAccount, session_id: int) -> dict:
    """重算一场「评分没成」的答卷 —— 心理老师在个案详情里点的那个按钮。

    三条判据，每条都有它的理由：

    - **已经有结果就什么都不做**（`recalculated: False`）。这是 CLAUDE.md §6 那条
      「改已发布版本的规则 → 生成新版本，旧版本保留，已有结果不受影响」在写入侧的
      另一半：重算不得覆盖一份已经存在的结果，因为那意味着「拿今天的规则改写昨天的
      结论」，而结果行上的 `rule_version` 会说它是新版本——一个没有任何东西看得出来
      的改写。要按新规则再算一遍，那是新的一场测评。
    - **还没交卷就不算**（422，并且说清是哪一种）。一份答到一半的卷子算出来的一定是
      `ANSWERS_INCOMPLETE`，把它当失败记下来只会盖住真正的原因。
    - **锁**——与 `submit_session` 同一个（`lock_session`）。学生交卷与老师点重试
      可以同时发生，两边都读到「没有结果」，两边各写一份：结果行上是
      `unique(session_id)`，第二次撞 1062 变成一个 500；而风险事件那条唯一键是
      `(session_id, trigger_rule, rule_version)`，两边写出来的**一模一样**，
      `maybe_raise_risk_events` 会静静地开出两条同名待办。
    """
    session = db.get(AssessmentSession, session_id)
    if session is None:
        raise AppError("NOT_FOUND", "答题会话不存在", 404)
    # 与路由上那道依赖重复，但两处都要有：范围判定的两个入口（§9）里，这一个回答的是
    # 「这条会话的学生归不归你管」——`session_id` 是客户端传来的，它不能成为越权凭据。
    ensure_student_in_scope(db, user, session.student_id)
    session = lock_session(db, session.id)
    if session is None:  # 锁与取之间被别人删掉（理论上没有删除接口，兜底而已）
        raise AppError("NOT_FOUND", "答题会话不存在", 404)

    existing_result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session.id))
    if existing_result:
        data = result_payload(db, session)
        data["recalculated"] = False
        data["student_id"] = session.student_id
        return data
    if session.submitted_at is None:
        raise AppError("VALIDATION_ERROR", "这份答卷还没有交卷，没有可重算的评分", 422)

    answers = db.execute(
        select(ScaleQuestion.question_no, AssessmentAnswer.answer)
        .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
        .where(AssessmentAnswer.session_id == session.id)
    ).all()
    score_session(db, session, {question_no: answer for question_no, answer in answers})
    data = result_payload(db, session)
    data["recalculated"] = data["result"] is not None
    # `student_id` 是给审计那一行用的（`audit_logs.student_id`）：它才让这一次重算
    # 出现在这名学生的「敏感访问记录」里。放在这里而不是让路由再查一次会话——
    # 路由手上只有 `session_id`，而会话在这一层已经取到了。
    data["student_id"] = session.student_id
    return data


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
    # 先上锁，再判「有没有算过」。次序不能反：判断与写入之间那一段是临界区，
    # 而这里有一个真实的并发对手——心理老师在个案详情上点「重算评分」
    # （`retry_calculation`，同一个 `lock_session`）与学生这一下提交可以同时到达。
    session = lock_session(db, session.id)
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
    # 完整性由 `score_session` → `calculate_session` → 引擎的 `validate_answers` 判
    # （`ANSWERS_INCOMPLETE`），判据是**规则版本里的 `question_count`**，不是这里再数一遍
    # ——§6 那条「阈值属于量表规则版本」同样适用：「这一版有多少题」只有一个来源。
    # 2026-09-17 查过一遍，确认服务端一直拦得住半份答卷（拦住它的是引擎，
    # 不是学生页那个写死 100 的计数器；那一个是客户端自己的门）。
    #
    # 交卷在算分**之前**盖章：这两件事互相独立，而「学生交没交卷」不取决于评分成不成。
    # 反过来的话，评分失败的那条路上 `submitted_at` 会一直是空的——于是这一场在
    # 「这个学生最新的一场」里排到最后（`latest_session_order` 把未交卷的排在后面），
    # 个案详情会把上一场当成「本次测评」显示。
    _stamp_submission(db, session, answer_times_for_session(db, session.id))
    session.idempotency_key = idempotency_key
    score_session(db, session, answer_map)
    db.flush()
    return result_payload(db, session)


def _active_care_case(db: Session, student_id: int) -> StudentCareCase | None:
    return db.scalar(
        select(StudentCareCase)
        .where(StudentCareCase.student_id == student_id, StudentCareCase.status != "CLOSED")
        .order_by(StudentCareCase.id.desc())
    )


def open_or_reuse_care_case(db: Session, student_id: int) -> StudentCareCase:
    """拿到这名学生现在的那条在办档案，没有就开一条（§16.4 / §17(a)）。

    **这是一段读-改-写，而它在 V1.2 之前没有约束兜底。** 两个请求同时到达时
    （同一场测评的两道重点题各触发一次 `maybe_raise_risk_events`、或者老师手工重算
    与一次交卷撞在一起）两边都查不到、两边都插入——那时是**静默地建出两条在办
    档案**，比现在更糟，只是看不出来。V1.2 把
    `uq_care_case_one_active_per_student`（生成列 `active_student_id`）落库之后，
    同样的并发会让第二条插入当场抛 `IntegrityError`。

    约束的方向是对的，缺的是写入侧的处理。处置选 **savepoint + 捕获 `IntegrityError`
    再重查一次**，不是 `SELECT … FOR UPDATE`：

    - 这里根本没有行可锁。锁的前提是那一行已经存在，而这条路要处理的恰恰是
      「**不存在**、于是两个人一起插」——`FOR UPDATE` 在没有任何行匹配时锁的是间隙，
      能不能挡住第二个插入取决于隔离级别与索引，是这个项目既没有先例、也无法在
      测试里稳定复现的东西（`lock_session` 那个 `with_for_update()` 锁的是**已经
      存在**的会话行，不是同一回事）。
    - 唯一键是**数据库已经有的**那个判据，而它一定生效：不用再引入第二套设施，
      也不会因为谁把隔离级别调了而悄悄失灵。代价只是撞上时多一次 SELECT。

    `begin_nested()` 那个 savepoint 是必须的：`IntegrityError` 会让**整个事务**进入
    失败状态，不套 savepoint 的话捕获之后连重查那条 SELECT 都发不出去。
    """
    care_case = _active_care_case(db, student_id)
    if care_case:
        return care_case

    from app.services.care_events import CASE_OPENED, record_case_event

    # 局部 import：`care_service` 已经 import 了本模块，所以本模块**不能**在顶层
    # import `care_service`。而 `care_events` 只依赖 `models`（它存在的理由就是这个），
    # 顶层 import 它其实不会成环——写成局部是让「谁依赖谁」这件事在**这一行**上看得见，
    # 不必读三个文件才能确认。
    try:
        with db.begin_nested():
            care_case = StudentCareCase(
                student_id=student_id, status="PENDING_REVIEW", opened_at=now_utc_naive()
            )
            db.add(care_case)
            db.flush()
            record_case_event(
                db,
                care_case,
                CASE_OPENED,
                # 开档没有操作人：它是**系统**根据一次交卷/一次导入自动开的，
                # 不是谁点的按钮。`operator_id` 因此为 NULL，时间线上显示「系统」。
                None,
                to_status="PENDING_REVIEW",
                reason="测评命中重点关注题目，系统自动开档",
            )
    except IntegrityError:
        # 另一个请求在同一个间隙里插了进去 —— 这**正是我们要的结果**，不是失败。
        # 唯一键挡住了第二条，而它想拿的东西现在就在库里。
        care_case = _active_care_case(db, student_id)
        if care_case is None:
            # 查不到就说明不是这个原因（外键、或者别的约束），别把它吞掉。
            raise
    return care_case


def result_payload(db: Session, session: AssessmentSession) -> dict:
    result = db.scalar(select(AssessmentResult).where(AssessmentResult.session_id == session.id))
    dimensions = db.scalars(select(DimensionResult).where(DimensionResult.session_id == session.id)).all()
    risk_events = db.scalars(select(RiskEvent).where(RiskEvent.session_id == session.id)).all()
    return {
        "session_id": session.id,
        "status": session.status,
        # 这一场交没交卷、算没算出来，是两件事：评分没成功的那一场**照样是交过的卷子**
        # （见 `score_session`），而 `result` 是 `null`。所以调用方判断「这份结果在不在」
        # 要看 `result`，不要看 `status`——`status` 停在 `IN_PROGRESS` 也是这个道理。
        "calculation_status": session.calculation_status,
        "calculation_error": session.calculation_error,
        "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
        "tested_at": session.tested_at.isoformat() if session.tested_at else None,
        "tested_at_source": session.tested_at_source,
        "duration_seconds": session.duration_seconds,
        "result": None
        if result is None
        else {
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
