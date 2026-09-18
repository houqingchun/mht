import csv
import io
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
)
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale
from app.security.data_scope import student_scope_predicate
from app.services.export_labels import gender_label, level_label, target_status_label


def ensure_task_writer(user: UserAccount) -> None:
    """测评任务是学校业务，写权归心理老师。

    RULE_CONFLICT (re-opened and re-resolved 2026-09-17): 2026-09-16 那版裁决按
    `docs/phase0_rule_freeze.md` 的「管理员管理任务」把写权留给了 ADMIN。用户指出
    那句话本身是错的——系统管理员只关注系统级配置，测评任务是业务。所以 ADMIN
    读写一起退出（`ensure_task_reader` 也不再放行它）。见 `api/v1/tasks.py` 的
    `TASK_READERS` 与 `docs/phase0_rule_freeze.md` 第 7 节。
    """
    if user.role_code != RoleCode.COUNSELOR:
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def ensure_task_reader(user: UserAccount) -> None:
    """Reading the task list and its completion figures is counselor + leader.

    Both need it for their own work — the counselor runs the cycle, the leader
    reads school-level completion as oversight. Neither is ADMIN: see
    `ensure_task_writer`.
    """
    if user.role_code not in {RoleCode.COUNSELOR, RoleCode.LEADER}:
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def effective_task_status(
    task: AssessmentTask,
    *,
    total_targets: int,
    completed_targets: int,
    now: datetime | None = None,
) -> str:
    """这场测评**现在**是什么状态（2026-09-17 补）。

    在此之前 `assessment_task.status` 是写入时定死的一个字面量：建任务写 `ACTIVE`，
    导入批次也写 `ACTIVE`，然后再没有一行代码读它或改它。于是列表上每一行永远是
    「进行中」——一批 3/3 全收齐的外部导入、一场已经过了截止日期的普查、一场还没到
    开始日期的复测，长得一模一样。用户报的就是这个。

    状态由**已经被记下来的事实**推出来，所以它是现算的，与 `export_service` 现算遮蔽、
    `analytics_service.latest_result_subquery` 现算「当前状态」是同一个口径：

    1. 库里那一列不是 `ACTIVE` → **以库里为准**。那是人做的裁决（`DRAFT` 草稿 /
       `PAUSED` 已暂停 / `CLOSED` 提前结束），推出来的事实不能盖过它。今天没有写入方，
       但词汇表里有这四个码，读的一侧就该按它们的意思读。
    2. 还没到 `start_at` → `NOT_STARTED` 未开始。
    3. 过了 `end_at` → `CLOSED` 已结束，**不管还有没有人没答**。窗口是学校自己定的，
       过期就是过期；「28 人里 23 人交了」由完成率那两列说，不必让状态列再说一遍。
       放了截止日期就能再改（`PATCH /assessment-tasks/{id}` 收 `end_at`），
       所以「还有几个人没答，延一周」是一条走得通的路，不是死局。
    4. 目标行全部完成 → `CLOSED` 已结束。外部导入的批次走的是这一条：它**没有截止
       日期**（`commit_assessment_import` 刻意留空，见那里的注释），只能由「全都答完了」收尾。
    5. 其余 → `ACTIVE` 进行中。

    `total_targets == 0` **不算**「全部完成」：空集的「全部完成」是真命题，但一次还没
    发出去的任务不该一建出来就显示「已结束」。

    **口径是整场任务，不是调用者自己的范围。** 传进来的两个数必须是**不加范围谓词**的
    总数。状态与 `name` / `start_at` 一样是任务自身的属性，而完成率跟读者的范围走（§9）：
    否则一个只带 CLASS 范围的心理老师会把一场 12/40 的全校普查看成「已结束」——
    同一行里状态说结束、完成率说 30%。

    时间比较用的是**本地朴素时间**：`start_at` / `end_at` 列是 `DateTime(timezone=True)`，
    但写进去的是 `datetime(2026, 9, 30, 23, 59, 59)` 这样的朴素值（seed 与
    `parse_datetime` 都这么写），MySQL 取回来也不带时区。
    """
    if task.status != "ACTIVE":
        return task.status
    now = now or datetime.now()
    if task.start_at and now < task.start_at:
        return "NOT_STARTED"
    if task.end_at and now > task.end_at:
        return "CLOSED"
    if total_targets > 0 and completed_targets >= total_targets:
        return "CLOSED"
    return "ACTIVE"


def task_target_counts(db: Session, task_id: int) -> tuple[int, int]:
    """整场任务的（目标行数，已完成数），**不加范围谓词**。

    `effective_task_status` 要的就是这两个数，凡是「这张卷子还能不能新开」的判断都得
    用这个口径——它与调用者能看到几名学生的目标行无关。
    """
    total = (
        db.scalar(select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)) or 0
    )
    completed = (
        db.scalar(
            select(func.count(AssessmentTarget.id)).where(
                AssessmentTarget.task_id == task_id,
                AssessmentTarget.status == "COMPLETED",
            )
        )
        or 0
    )
    return int(total), int(completed)


def list_assessment_tasks(db: Session, user: UserAccount) -> list[dict]:
    ensure_task_reader(user)
    tasks = db.scalars(select(AssessmentTask).order_by(AssessmentTask.id.desc())).all()
    # 整场任务的完成数（**不加范围谓词**），`effective_task_status` 的口径。
    # 一次分组查询把全部任务取齐，不按任务各查一遍——下面两个数跟调用者的范围走，
    # 这两个不跟，两套数必须都算出来。
    whole: dict[int, tuple[int, int]] = {
        task_id: (int(total or 0), int(done or 0))
        for task_id, total, done in db.execute(
            select(
                AssessmentTarget.task_id,
                func.count(AssessmentTarget.id),
                func.sum(case((AssessmentTarget.status == "COMPLETED", 1), else_=0)),
            ).group_by(AssessmentTarget.task_id)
        ).all()
    }
    now = datetime.now()
    # The task rows themselves are school-level objects and stay visible; it is
    # the completion figures that follow the caller's scope. A counselor reading
    # "12 / 40" should be reading their own twelve over their own forty — the
    # task list and the workbench tile use the same scope so they agree.
    scope = student_scope_predicate(db, user)
    items = []
    for task in tasks:
        total = (
            db.scalar(
                select(func.count(AssessmentTarget.id))
                .join(Student, Student.id == AssessmentTarget.student_id)
                .where(AssessmentTarget.task_id == task.id, scope)
            )
            or 0
        )
        completed = (
            db.scalar(
                select(func.count(AssessmentTarget.id))
                .join(Student, Student.id == AssessmentTarget.student_id)
                .where(
                    AssessmentTarget.task_id == task.id,
                    AssessmentTarget.status == "COMPLETED",
                    scope,
                )
            )
            or 0
        )
        total_all, completed_all = whole.get(task.id, (0, 0))
        items.append(
            {
                "id": task.id,
                "task_no": task.task_no,
                "name": task.name,
                # 注意：发出去的是**推出来的**状态，不是 `assessment_task.status`
                # 那一列的原文。理由与口径见 `effective_task_status`。
                "status": effective_task_status(
                    task, total_targets=total_all, completed_targets=completed_all, now=now
                ),
                "scope_type": task.scope_type,
                "start_at": task.start_at.isoformat() if task.start_at else None,
                "end_at": task.end_at.isoformat() if task.end_at else None,
                "total_targets": total,
                "completed_targets": completed,
                "completion_rate": round(completed / total * 100) if total else 0,
                # 外部导入的批次任务（数据中心 → MHT测评记录导入）是一批已经完成的
                # 记录，不是一份等学生来答的任务。列表上要能看出来：否则「5/5 完成、
                # 100%」会和一份真正收上来的任务长得一模一样。
                "source": task.source,
            }
        )
    return items


def create_school_assessment_task(
    db: Session,
    user: UserAccount,
    *,
    name: str,
    start_at: str | None,
    end_at: str | None,
) -> AssessmentTask:
    ensure_task_writer(user)
    school = db.scalar(select(School).where(School.code == "QH"))
    scale = db.scalar(
        select(AssessmentScale)
        .where(AssessmentScale.code == "MHT", AssessmentScale.status == "PUBLISHED")
        .order_by(AssessmentScale.id.desc())
    )
    if not school or not scale:
        raise AppError("VALIDATION_ERROR", "缺少学校或已发布量表", 422)
    task_count = db.scalar(select(func.count(AssessmentTask.id))) or 0
    task = AssessmentTask(
        task_no=f"TASK-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{task_count + 1}",
        name=name,
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        start_at=parse_datetime(start_at),
        end_at=parse_datetime(end_at),
        status="ACTIVE",
        created_by=user.id,
    )
    db.add(task)
    db.flush()
    # 目标行按**创建者的数据范围**发放（§9 的第二个入口）。这条谓词是 2026-09-17
    # 写权从 ADMIN 转到 COUNSELOR 时加的，不是装饰：在此之前唯一能建任务的角色是
    # 管理员（seed 给的是 SCHOOL 范围），「发放全体」和「发放创建者可见的全体」
    # 恰好是同一件事，所以少了它也不出错。写权一落到心理老师身上就分岔了——
    # 一个只带 CLASS 范围的心理老师，凭这个端点能给**全校**每个学生建目标行，
    # 而目标行本身就是「这个学生要参加这次测评」的管理事实，他随后还能在
    # `/counselor/tasks` 的完成明细里按自己的范围读到它的进度。范围收在这里，
    # 与 `list_assessment_tasks` / `task_completion` 读的那一侧用的是同一个谓词。
    students = db.scalars(
        select(Student).where(
            Student.school_id == school.id,
            Student.status == "ACTIVE",
            student_scope_predicate(db, user),
        )
    ).all()
    db.add_all([AssessmentTarget(task_id=task.id, student_id=student.id, status="NOT_STARTED") for student in students])
    db.flush()
    return task


def parse_datetime(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def update_assessment_task(
    db: Session,
    user: UserAccount,
    task_id: int,
    *,
    name: str | None,
    start_at: str | None,
    end_at: str | None,
) -> AssessmentTask:
    ensure_task_writer(user)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    if name is not None:
        task.name = name
    if start_at is not None:
        task.start_at = parse_datetime(start_at)
    if end_at is not None:
        task.end_at = parse_datetime(end_at)
    if task.start_at and task.end_at and task.end_at < task.start_at:
        raise AppError("VALIDATION_ERROR", "截止日期不能早于开始日期", 422)
    db.flush()
    return task


def task_completion(db: Session, user: UserAccount, task_id: int) -> list[dict]:
    ensure_task_reader(user)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    rows = db.execute(
        select(AssessmentTarget, Student, Grade, ClassGroup, AssessmentSession, AssessmentResult)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        # Outer-joined on the (task, student) pair, which `create_or_get_session`
        # guarantees is unique — so unlike export_service this join cannot multiply
        # rows and needs no dedup.
        .outerjoin(
            AssessmentSession,
            (AssessmentSession.task_id == AssessmentTarget.task_id)
            & (AssessmentSession.student_id == Student.id),
        )
        # 结果按 session_id 唯一，所以这一跳同样不乘行。
        #
        # **口径是「这一场」，不是「每人最近一场」。** 这是本模块唯一按场说话的地方，
        # 与 §11 的当前状态口径刻意不同：任务明细要回答的是「这一批人**在这次**测出了
        # 什么」，所以九月十六日那批与九月十七日那批各显示各的。若哪天有人把它换成
        # `latest_result_subquery`（按人取最近一场），九月十六日的批次明细里会印出
        # 九月十七日的分——一个批次报表说别的批次的事，而它旁边那列「完成率」还是
        # 按场算的。`test_task_completion_levels.py` 钉住这条。
        .outerjoin(AssessmentResult, AssessmentResult.session_id == AssessmentSession.id)
        # Per-student rows carrying 学号 and 姓名 — scoped for the same reason the
        # care-case queue is. This also scopes the CSV, which is built from here.
        .where(AssessmentTarget.task_id == task_id, student_scope_predicate(db, user))
        .order_by(Student.id)
    ).all()
    return [
        {
            "student_no": student.student_no,
            "student_name": student.masked_name,
            "grade": grade.name,
            "class_name": class_group.name,
            "gender": student.gender,
            # Derived on read; NULL for students imported before migration 0009.
            "age": student.age,
            "status": target.status,
            "assigned_at": target.assigned_at.isoformat() if target.assigned_at else None,
            "completed_at": target.completed_at.isoformat() if target.completed_at else None,
            # 这一场的等级与总分。NULL 有两种来源，界面都必须显示「未测评」/「—」而
            # 不是空格或 0：他这一场还没交（没有结果行），或者交了但没有结果
            # （评级不适用）。`None` 不是 `0`（§11）。
            "total_level": result.total_level if result else None,
            "total_score": result.total_score if result else None,
            # Comes off the session, not the target: `completed_at` is set when the
            # target is marked done, and a session can legitimately have no duration
            # (pre-0009 rows, or a session cleared by /reset). The UI must render
            # NULL as 「—」 — 0 would read as "answered instantly", a different claim
            # from "not recorded".
            "duration_seconds": session.duration_seconds if session else None,
        }
        for target, student, grade, class_group, session, result in rows
    ]


def task_completion_csv(db: Session, user: UserAccount, task_id: int) -> str:
    rows = task_completion(db, user, task_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["学号", "学生", "年级", "班级", "性别", "年龄", "状态", "关注等级", "MHT总分", "分配时间", "完成时间", "用时(秒)"]
    )
    for row in rows:
        writer.writerow(
            [
                row["student_no"],
                row["student_name"],
                row["grade"],
                row["class_name"],
                # Translated here and **only** here: `task_completion` above still hands
                # the API raw codes, because the SPA translates those itself via
                # `labels.ts`. This row is destined for a file that leaves the building,
                # so it gets the same words the screen shows (CLAUDE.md §3).
                gender_label(row["gender"]),
                row["age"] if row["age"] is not None else "",
                target_status_label(row["status"]),
                # 等级不是数值列：按 `levelLabel` 的既有约定出「未测评」而不是空单元格
                # （§3）。这一场还没交卷的人因此也说得清楚，而不是留一格让人猜。
                level_label(row["total_level"]),
                # 总分是数值列，另一种约定：没有记录就**留空**。写「—」会让整列被
                # 表格软件当成文本，这一列就没法排序求和了（§3）。
                row["total_score"] if row["total_score"] is not None else "",
                row["assigned_at"] or "",
                row["completed_at"] or "",
                row["duration_seconds"] if row["duration_seconds"] is not None else "",
            ]
        )
    return "\ufeff" + output.getvalue()

