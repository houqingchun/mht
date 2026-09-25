import csv
import hashlib
import io
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.account import UserAccount, UserScope
from app.models.assessment import (
    AssessmentResult,
    AssessmentSession,
    AssessmentTask,
    active_task_predicate,
    effective_session_predicate,
)
from app.models.care import StudentCareCase
from app.models.common import now_local_naive
from app.models.enums import RoleCode
from app.models.exporting import ExportJob
from app.models.organization import ClassGroup, Grade, Student
from app.security.data_scope import student_scope_predicate
from app.security.permissions import SCOPED, STUDENT_PSYCH_DETAIL, scope_allows
from app.services.assessment_service import latest_session_order
from app.services.export_document import ExportDocument
from app.services.export_labels import case_status_label, gender_label, level_label, source_label
from app.services.settings_service import get_namespace


def mask_student_name(name: str | None) -> str:
    """Reduce a name to surname + 同学, the form the roster already uses for 林同学.

    Derived here rather than read from `student.masked_name`: that column is
    written from the real name by every import path (seed, seed_demo,
    student_import_service), so it holds full names in practice and masking with
    it produced byte-identical CSVs for mask_names=True and False. It remains the
    display name used by the roster and the care queue; it is not a privacy
    control and nothing should treat it as one.
    """
    if not name:
        return ""
    return f"{name[0]}同学"


def export_care_cases_csv(
    db: Session,
    user: UserAccount,
    *,
    high_risk_only: bool = False,
    student_ids: list[int] | None = None,
    mask_names: bool = True,
    include_score: bool = False,
) -> ExportDocument:
    """Build the controlled-export CSV.

    Never emits raw answers, key-question responses, interview text or family
    contact text — only the identity/summary columns listed below.

    `mask_names=False` keeps full names. The caller's *purpose* is not enough to
    unlock it — the export capability alone says a role may export summaries, not
    that it may export them identified. A leader holds PROGRESS_SUMMARY for
    CONTROLLED_EXPORT and SUMMARY for STUDENT_PSYCH_DETAIL, i.e. aggregates only,
    yet `mask_names=False` handed them a named roster while `/students` answered
    403. Unmasking therefore requires the full-case-access scope, the same
    distinction `/care-cases/{id}` uses.
    """
    if user.role_code not in {RoleCode.COUNSELOR, RoleCode.LEADER}:
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    if not mask_names and not scope_allows(db, user.role_code, STUDENT_PSYCH_DETAIL, allow={SCOPED}):
        raise AppError(
            "MASK_FORBIDDEN", "当前角色的导出权限仅支持摘要，不能解除姓名遮蔽", 403
        )
    if high_risk_only and student_ids:
        raise AppError("VALIDATION_ERROR", "高度关注导出不支持同时指定学生名单", 422)

    # Latest session per student, not "every session, first row wins". The previous
    # join pulled in all of a student's sessions with no ORDER BY on the join and
    # kept whichever row the database emitted first, so 关注等级 could come from an
    # older sitting and contradict the case-detail page, which reads the newest
    # (`care_service.get_care_case`). That was already wrong; adding 用时(秒) off the
    # same arbitrary row would have made the export visibly disagree with the page
    # it is exported from.
    #
    # 与个案详情对齐的口径 = `latest_session_order` + `effective_session_predicate`，
    # 即**施测时间**优先、id 只兜底、且只取算数的那一场。
    # 这里曾经是 `max(id)`，两者在只有系统内作答时结果相同，导入之后就分岔了：
    # 一份上学期普查结果的 id 比本学期系统内的会话更大，`max(id)` 会把去年那份选成本次。
    # 相关子查询保持逐学生的形式，不在 Python 里循环（`latest_session()` 会变成 N+1）。
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
    statement = (
        select(StudentCareCase, Student, Grade, ClassGroup, AssessmentSession, AssessmentResult)
        .join(Student, Student.id == StudentCareCase.student_id)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .outerjoin(AssessmentSession, AssessmentSession.id == latest_session_id)
        .outerjoin(AssessmentResult, AssessmentResult.session_id == AssessmentSession.id)
        # Applied even when the caller named students: the single-student endpoint
        # guards its id, and an export that honoured the guard for one id but not
        # for a list would only move the hole.
        .where(student_scope_predicate(db, user))
        .order_by(StudentCareCase.id)
    )
    if student_ids:
        statement = statement.where(StudentCareCase.student_id.in_(student_ids))

    rows = db.execute(statement).all()

    # ★ 消除 N+1：批量预取 owner 显示名（此前是逐行调 `db.get(UserAccount, owner_id)`）。
    # 查询返回 (StudentCareCase, Student, Grade, ClassGroup, AssessmentSession, AssessmentResult)，
    # owner_id 在 r[0].owner_id 上（StudentCareCase 的属性），不在行级元组里。
    owner_ids = {r[0].owner_id for r in rows if r[0].owner_id}
    owner_names = {}
    if owner_ids:
        for acct in db.execute(
            select(UserAccount.id, UserAccount.display_name).where(UserAccount.id.in_(owner_ids))
        ).all():
            owner_names[acct.id] = acct.display_name

    def _owner_label_cached(owner_id: int | None) -> str:
        if not owner_id:
            return "未分配"
        return owner_names.get(owner_id, "未分配")
    output = io.StringIO()
    writer = csv.writer(output)

    header = ["学号", "学生", "年级", "班级"]
    # 性别/年龄 ride with the names, not with the summaries. Surname + gender + age +
    # class is close to unique in a two-class grade, and 学号 sits in the same row —
    # attaching them to the *masked* export would make it a roster read by another
    # name, which is the bypass §4 of CLAUDE.md forbids (a leader holds
    # PROGRESS_SUMMARY for CONTROLLED_EXPORT and SUMMARY for STUDENT_PSYCH_DETAIL, so
    # `/students` answers 403 for them). Unmasking already requires SCOPED
    # STUDENT_PSYCH_DETAIL, so anyone entitled to these columns is entitled to the
    # names beside them. 用时 is not an identifier — it stays in both modes.
    if not mask_names:
        header += ["性别", "年龄"]
    # 「来源」说明这场测评是本系统里做的还是外部平台导入的。这一列不是装饰：导出取的是
    # 每个学生**最新**的那场会话，学校导入一份外部普查结果之后，同一行的关注等级与用时
    # 可能就来自那份外部记录，而文件上看不出任何区别——这张 CSV 是发给学校看的，
    # 它得能回答「这份重点关注是谁判的」。
    header += ["关注等级", "用时(秒)", "来源", "档案阶段", "负责人"]
    if include_score:
        header.append("MHT总分")
    writer.writerow(header)

    seen_students: set[int] = set()
    written = 0
    for care_case, student, grade, class_group, session, result in rows:
        # One row per student. The join still multiplies rows by care case — a
        # student can carry a closed case alongside an open one — so the dedup
        # stays; it no longer has sessions to collapse, those are filtered in SQL.
        if student.id in seen_students:
            continue
        seen_students.add(student.id)
        if high_risk_only and (not result or result.total_level != "KEY_ATTENTION"):
            continue
        name = mask_student_name(student.name) if mask_names else student.name
        row = [
            student.student_no,
            name,
            grade.name,
            class_group.name,
        ]
        if not mask_names:
            # Chinese, not codes: this file is read by a school, not by the SPA — it
            # leaves the building as a download and nothing downstream will translate
            # `MALE` for them. `export_labels` mirrors `labels.ts` and a test compares
            # the two, so the CSV and the screen say the same words (CLAUDE.md §3).
            row.append(gender_label(student.gender))
            # 年龄 是名册上**存下来**的整数（迁移 `0011_student_age` 把 `birth_date` 换成了它），
            # 不再现算——所以它会停留在学校上次导名册的那一年。这里照原样导出：这一列回答的是
            # 「名册上写的是几岁」，与学校手上的那份名册对得上比"算准"更重要。没填就留空
            # （数值列留空是既有约定，`—` 会把整列变成文本）。
            row.append(student.age if student.age is not None else "")
        # `level_label` says 未测评 rather than leaving a hole — same call the UI's
        # `levelLabel` makes. 用时 stays blank when unrecorded: it is a numeric column,
        # and `—` there would file the whole column under text. The two blank conventions
        # are split by column type, not by accident.
        row.append(level_label(result.total_level if result else None))
        row.append(session.duration_seconds if session and session.duration_seconds is not None else "")
        # 没有会话时是 `—`（`label_of` 的缺值约定），不是「系统内作答」——「没有任何测评」
        # 与「测评是系统内做的」是两件事。
        row.append(source_label(session.source if session else None))
        row.append(case_status_label(care_case.status))
        row.append(_owner_label_cached(care_case.owner_id))
        if include_score:
            row.append(result.total_score if result else "")
        writer.writerow(row)
        written += 1
    return ExportDocument(
        csv_text="﻿" + output.getvalue(), columns=tuple(header), row_count=written
    )


# ---------------------------------------------------------------------------
# 导出作业（§16.3 / §17(b)，2026-09-19 第 8 期）
#
# 导出在这套系统里**不再是一次点击**：它先成为 `export_job` 的一行（带用途、当时的
# 范围快照、字段白名单、遮蔽等级、行数与有效期），文件落到盘上，然后**总是**从那
# 一行下载。之所以不让「创建」这一条路直接把文件发出去，是三条互相独立的原因：
#
#   1. `download_count` / `downloaded_at` 只有在下载非走不可时才说得上话。同一个文件
#      从两扇门出去、其中一扇不计入，那两个字段记的就是一句半真的话。
#   2. §16.3 要求「导出文件默认短期有效，过期后自动失效」——有效期只有在下载**经过
#      一道门**时才是一个判据。
#   3. `mask_level` 单独存一列（不由 `purpose` 承担，§8 那条缺口的同一道理）、
#      `field_policy` 是**服务端**的白名单，两件事都要求在文件被生产出来的那一刻就有
#      一行记录，而不是事后补。
# ---------------------------------------------------------------------------

# `export_job.export_type`。与 `export_labels.py` 的中文映射成对（§3 第三面：
# 服务端生成的导出文件与说明页面上写的是同一批字）。
EXPORT_TYPE_CARE_CASES = "CARE_CASES"
EXPORT_TYPE_HIGH_RISK_CASES = "HIGH_RISK_CASES"
EXPORT_TYPE_SINGLE_CASE = "SINGLE_CASE"
EXPORT_TYPE_TASK_COMPLETION = "TASK_COMPLETION"
EXPORT_TYPE_NON_PARTICIPANTS = "NON_PARTICIPANTS"
# §20#14 的「导出」那一半（2026-09-20 补）：那四类进不来的导入行（未匹配 / 任务外 /
# 重复 / 冲突）可以被查询，也就可以被导出成一份复核清单。
EXPORT_TYPE_UNMATCHED_IMPORT_ROWS = "UNMATCHED_IMPORT_ROWS"
# 2026-09-24 补：效度建议复测的学生名册。这一份文件的用途与其余六份都不同——
# 它是**一份派工单**（派人去找这些学生重测），所以它的列里既有身份（学号 / 姓名 /
# 年级 / 班级）也有「为什么名单上有他」（那个效度分）。它**只能由心理老师导出**：
# 服务层走 `ensure_student_result_reader`（§4 的双门槛），不是这里的一道开关。
EXPORT_TYPE_VALIDITY_RETEST = "VALIDITY_RETEST"
# V2.0.0 §8.2 补：专业分析报告的导出（`reporting_service.report_document`）。
# 它此前在 `api/v1/reporting.py` 里是**一个裸字符串**，而下面那张镜像守卫扫的是
# `EXPORT_TYPE_*` **前缀**——字面量对它不可见，所以漏码是静默的（§3 那一族的第六处）。
EXPORT_TYPE_PROFESSIONAL_REPORT = "PROFESSIONAL_REPORT"

# `export_job.mask_level`。**与 `purpose` 分开存是有意的**：用途是自由文本，它担不起
# 任何机器判据，而「这份文件是不是实名的」正是导出审计唯一要回答的问题（§8）。
MASK_LEVEL_MASKED = "MASKED"
MASK_LEVEL_IDENTIFIED = "IDENTIFIED"

# `export_job.status`。只写 `READY` —— 导出是**同步**的：这一行与文件在同一个请求里
# 成型，失败时事务回滚、盘上也不留文件。所以 `PENDING` / `FAILED` 两种取值在库里
# **不可达**（模型给了它们默认值，那是 DDL 对齐阶段的形状，见 `models/exporting.py`）。
# 撤销与过期是**派生**出来的，不落 `status`：`effective_task_status`（§12）与
# `effective_session_predicate`（§27）是同一条道理——写进去的状态会与事实分岔。
JOB_STATUS_READY = "READY"
JOB_STATUS_REVOKED = "REVOKED"
JOB_STATUS_EXPIRED = "EXPIRED"

# `purpose` 在线上是 `String(255)`。超长时 MySQL 严格模式回的是 1406（一句英文的列名
# 错误），所以写入方自己先拦——与 `care_events.REASON_COLUMN_LIMIT` 同一条。
PURPOSE_LIMIT = 255


def export_directory() -> Path:
    """导出文件在**服务器上的**落点。

    相对仓库根算（`Path(__file__).resolve().parents[3]`：services → app → backend →
    仓库根），与 `db/seed.py` 用 `parents[3] / "data" / "mht_scale.json"` 找题库是同一
    套算法，所以安装目录整个搬走时它跟着走。

    **做成 `Settings` 里一个可配项是刻意的「还没有」**：配置项要有人认，而 Windows
    安装器并不知道这个键——一个安装器不认识、操作员可以配错的路径，最坏的结果是
    导出成功、文件落在一个没人看得见的地方。要加请连 `.env` 的写入方与
    `deploy/README.md` 一起加。

    目录**不存在时现建**（`mkdir(parents=True, exist_ok=True)`）：`var/` 不在版本库
    里（`.gitignore`），在一个刚解压出来的安装目录里它一定不存在。
    """
    return Path(__file__).resolve().parents[3] / "var" / "exports"


def effective_export_status(job: ExportJob, *, now: datetime | None = None) -> str:
    """这一份导出**此刻**的状态。派生，不落库。

    判据的次序是有意义的：**撤销优先于过期**。一份先被撤销、后被跨过有效期的文件，
    它的故事是「有人叫停了它」，而不是「它自己到期了」——两句话对读轨迹的人是两件事。
    """
    if job.revoked_at is not None:
        return JOB_STATUS_REVOKED
    moment = now or now_local_naive()
    if job.expires_at is not None and job.expires_at <= moment:
        return JOB_STATUS_EXPIRED
    return job.status


def scope_snapshot(db: Session, user: UserAccount) -> dict:
    """导出那一刻的授权范围。**快照，不是引用。**

    与 `assessment_target` 的名册快照同一条规矩：范围配置改掉之后，「这份文件当时按
    什么口径导的」必须还有答案。存的是 **id 而不是名称**——名称会改、id 不会，而这一
    列要回答的是「当时算的是哪些学生」。

    五个维度**都写出来**，没有的那几个是 `null`：只写非空项的话，「这一行是 SCHOOL
    范围（所以没有 grade_id）」与「这一行本该有 grade_id 而它丢了」在 JSON 上长得
    一样，而这一列存在的理由正是要能分辨这两件事。
    """
    rows = db.scalars(
        select(UserScope).where(UserScope.user_id == user.id).order_by(UserScope.id)
    ).all()
    return {
        "role": str(user.role_code),
        "scopes": [
            {
                "scope_type": str(row.scope_type),
                "school_id": row.school_id,
                "grade_id": row.grade_id,
                "class_id": row.class_id,
                "student_id": row.student_id,
            }
            for row in rows
        ],
    }


def _next_job_no(db: Session, *, today: date | None = None) -> str:
    """`EXPORT-YYYYMMDD-N`：按天编号，给人念的（同 `job_no` 那一列的注释）。

    按当天的**行数**推下一个号（照 `assessment_import_service._next_batch_no`）。
    并发下两个请求可能算出同一个 N，此时 `uq_export_job_no` 会在 flush 那一刻抛
    `IntegrityError` —— 那是一句英文的 500，但它**不会**留下一份编号重复的文件记录，
    而这一条比「自动重试一次拿到 N+1」更重要：静默重编号会让审计里那个编号与用户
    报出来的编号对不上。代价是同一个编号在两个**并发**请求上有一个会失败，这在
    「一个人点一次导出」的场景里不会发生。
    """
    day = (today or now_local_naive().date()).strftime("%Y%m%d")
    prefix = f"EXPORT-{day}-"
    used = (
        db.scalar(
            select(func.count(ExportJob.id)).where(ExportJob.job_no.like(f"{prefix}%"))
        )
        or 0
    )
    return f"{prefix}{used + 1}"


def _write_export_file(job_no: str, document: ExportDocument) -> tuple[Path, str]:
    """把文件落盘，返回（落点，字节的 sha256）。

    摘要是**从写下去的那串字节**算的，不是另算一遍 `csv_text.encode()`：两者今天
    必然相同，但将来若有人给 `write_bytes` 换上一套行尾转换，摘要就从「盘上这一份」
    悄悄变成「内存里那一份」，而它唯一要回答的问题正是「盘上这一份还是不是当初那一份」。
    """
    directory = export_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_no}.csv"
    data = document.csv_text.encode("utf-8")
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def create_export_job(
    db: Session,
    user: UserAccount,
    *,
    export_type: str,
    purpose: str,
    document: ExportDocument,
    mask_level: str = MASK_LEVEL_MASKED,
) -> ExportJob:
    """把一份已经生成好的文件登记成一个导出作业。

    **落行与落文件的次序**（先 `flush` 行、再写文件、最后补文件元数据）：三处都在
    调用方那个事务里，所以任一处失败时**两处都不留**。反过来写（先文件后行）在编号
    撞车时会留下一份谁也认领不了的 CSV，而盘上的东西没有事务可以回滚。

    `job_ttl_hours` / `max_rows` 来自「导出」那一组配置，两个都是
    `TODO_BUSINESS_CONFIRMATION`（见 `settings_service.DEFAULTS`）：

    - `max_rows` 为 0 时**不设上限**，等于这套系统一直以来的行为。超限时**拒绝**、
      不截断——截断会让收到文件的人以为「这里就只有这么多」，而拒绝说得清是为什么
      （§10：凡是截断，都要自己说出来）。
    - `job_ttl_hours` 为 0 时 `expires_at` 留空 = 永不过期（操作员可以这样配），
      默认值是 24 小时。
    """
    purpose = (purpose or "").strip()
    if not purpose:
        raise AppError("PURPOSE_REQUIRED", "受控导出必须填写用途", 422)
    if len(purpose) > PURPOSE_LIMIT:
        raise AppError(
            "VALIDATION_ERROR", f"用途说明不能超过 {PURPOSE_LIMIT} 个字", 422
        )

    limits = get_namespace(db, "export")
    max_rows = int(limits.get("max_rows") or 0)
    if max_rows and document.row_count > max_rows:
        raise AppError(
            "EXPORT_TOO_LARGE",
            f"本次导出 {document.row_count} 行，超过单次上限 {max_rows} 行。"
            f"请缩小范围后分批导出（上限是「导出」配置组里的 max_rows）。",
            422,
        )

    ttl_hours = int(limits.get("job_ttl_hours") or 0)
    now = now_local_naive()
    job = ExportJob(
        job_no=_next_job_no(db, today=now.date()),
        export_type=export_type,
        requested_by=user.id,
        purpose=purpose,
        scope_snapshot=scope_snapshot(db, user),
        # 字段白名单是**服务端**的：列来自文件自己的表头，不由调用方递进来，
        # 所以「导出接口不得接受任意字段名」（§16.3）在形状上就成立。
        field_policy={"columns": list(document.columns)},
        mask_level=mask_level,
        status=JOB_STATUS_READY,
        expires_at=now + timedelta(hours=ttl_hours) if ttl_hours > 0 else None,
    )
    db.add(job)
    db.flush()  # 编号撞车在这里现形，此时盘上还没有文件
    path, digest = _write_export_file(job.job_no, document)
    job.file_uri = str(path)
    job.file_sha256 = digest
    job.row_count = document.row_count
    db.flush()
    return job


def load_export_job(db: Session, user: UserAccount, job_id: int) -> ExportJob:
    """按 id 取一条导出作业，顺带做可见性判断。

    **「不属于你」与「不存在」回同一句话**（§24 / §26 那条）：`job_id` 是客户端传
    来的，分开报就等于确认了某个 id 存在。管理员看得到所有人的作业（出了数据外泄
    的事他要能用「撤销」把下载口关掉），但见 `download_export_job` —— 看得见不等于
    下得下来。
    """
    job = db.get(ExportJob, job_id)
    if job is None or (job.requested_by != user.id and user.role_code is not RoleCode.ADMIN):
        raise AppError("NOT_FOUND", "导出记录不存在", 404)
    return job


def _is_downloadable(job: ExportJob, user: UserAccount) -> bool:
    """这份作业此刻能不能被**这个人**下载。

    列表里的 `downloadable` 与下载端点共用它，所以界面上那个按钮亮不亮、点下去会
    不会成功，是同一句话的两个说法（§11：指标卡上的数必须与它点进去的那个列表同源）。
    """
    return (
        job.requested_by == user.id
        and effective_export_status(job) == JOB_STATUS_READY
        and bool(job.file_uri)
        and Path(job.file_uri).is_file()
    )


def download_export_job(db: Session, user: UserAccount, job_id: int) -> tuple[ExportJob, bytes]:
    """取回文件字节，并把这一次下载记在作业行上。**调用方负责写审计与提交。**

    三条判据，各自对应一件真会有人问的事：

    - **只有导出人本人能下载**，管理员也不行。管理员能列出、能撤销所有人的作业，
      但不能替别人把文件取走——他的能力是 `BASE_ONLY`，心理数据本来就不该经过他
      （§4）。「能撤销」是数据外泄时的紧急开关，它不需要、也不该顺带给出读取权限。
      这是一处**有意的**不对称。
    - 已撤销 / 已过期 → 410，并且**说清是哪一种**：两句话对使用者是两条不同的出路
      （「有人叫停了这份文件」与「请重新导出」）。
    - 盘上的文件与 `file_sha256` 对不上 → 409，**不把文件发出去**。摘要这一列因此有了
      读者，它回答的是「盘上这一份还是不是当初那一份」。文件被删掉是第三种情况，
      同样不下载，但提示不同（重新导出即可）。
    """
    # 可见性先于归属：不可见的作业在这里是 404，不会走到下面那句 403。
    job = load_export_job(db, user, job_id)
    if job.requested_by != user.id:
        raise AppError(
            "ROLE_FORBIDDEN", "这份导出由其他人创建，只能由导出人本人下载", 403
        )

    status = effective_export_status(job)
    if status == JOB_STATUS_REVOKED:
        raise AppError("EXPORT_REVOKED", "这份导出已被撤销，不能再下载", 410)
    if status == JOB_STATUS_EXPIRED:
        raise AppError("EXPORT_EXPIRED", "这份导出已过有效期，请重新导出", 410)

    path = Path(job.file_uri) if job.file_uri else None
    if path is None or not path.is_file():
        raise AppError("EXPORT_FILE_MISSING", "导出文件已不在服务器上，请重新导出", 410)
    data = path.read_bytes()
    if job.file_sha256 and hashlib.sha256(data).hexdigest() != job.file_sha256:
        # 摘要是我们自己写下去的那一串字节的摘要，所以对不上只可能是文件在这之后
        # 被改动或损坏了。发出去就是把一份来历不明的文件当成受控导出交出去。
        raise AppError(
            "EXPORT_FILE_CHANGED", "导出文件与记录不一致，已停止下载，请重新导出", 409
        )

    job.download_count += 1
    job.downloaded_at = now_local_naive()
    db.flush()
    return job, data


def revoke_export_job(db: Session, job: ExportJob) -> bool:
    """关掉一份导出的下载口。**返回「这一次有没有真的改变什么」**。

    已经撤销过的再撤销一次不是失败（两个人同时点，或者页面停在旧状态），所以回
    `False` 而不是抛异常——与 `auth_service.revoke_session` 同一条：调用方据此决定
    要不要写那一行审计，而**同一件事不写两行**。

    撤销原因进审计的 `detail`，不另开一列：`export_job` 上只有 `revoked_at`
    （§16.3 要的就是「撤销时间」），而把它落成可查的列要连着一起想 §8 那条
    「`detail` 今天只有数据库看得见」——那是一个更大的口子，不该由这一期顺手开。

    原因**不从参数进来**：它只有一个去处（那条审计的 `detail`），而审计由路由写
    （全库一致的形状：服务层不写审计）。收一个自己一个字都不用的参数，下一个人会
    以为这一列在某个分支上被写进了哪里。
    """
    if job.revoked_at is not None:
        return False
    job.revoked_at = now_local_naive()
    job.status = JOB_STATUS_REVOKED
    db.flush()
    return True


def list_export_jobs(db: Session, user: UserAccount, *, limit: int = 50) -> list[ExportJob]:
    """导出中心那一张表。管理员看全校，其余人只看自己创建的。

    按 `id` 倒序而不是 `created_at`：`now_utc_naive()` 截断到整秒（§20），同一秒里
    连着导两份时时间戳并列，而 `id` 是它们的真实次序。`created_at` 走的是数据库的
    `func.now()`，那是秒级以下的精度也可能并列的另一口钟。
    """
    statement = select(ExportJob).order_by(ExportJob.id.desc()).limit(limit)
    if user.role_code is not RoleCode.ADMIN:
        statement = statement.where(ExportJob.requested_by == user.id)
    return list(db.scalars(statement).all())


def _requester_names(db: Session, jobs: list[ExportJob]) -> dict[int, str]:
    """一次批量取，不逐行查（同 `api/v1/audit.py` 那一处）。"""
    ids = {job.requested_by for job in jobs}
    if not ids:
        return {}
    rows = db.scalars(select(UserAccount).where(UserAccount.id.in_(ids))).all()
    return {row.id: row.display_name for row in rows}


def serialize_export_job(job: ExportJob, *, requester_name: str | None, downloadable: bool) -> dict:
    """作业的对外形状。

    `status` 发的是**派生**值：客户端不需要知道「撤销与过期在库里各是哪一列」，
    它只需要知道这一刻能不能下（而这一点由 `downloadable` 直接回答，不让界面自己
    从状态码推——三个判据里有一个是「文件还在不在盘上」，那是界面看不见的）。

    `scope_snapshot` 原样下发：它回答的是「这份文件当时按什么口径导的」，而那件事
    与「现在」无关。`columns` 从 `field_policy` 里取出来摊平——字段策略在库里是 JSON，
    而界面上要的是「这份文件有哪些列」这一行字。
    """
    return {
        "id": job.id,
        "job_no": job.job_no,
        "export_type": job.export_type,
        "requested_by": job.requested_by,
        "requested_by_name": requester_name,
        "purpose": job.purpose,
        "mask_level": job.mask_level,
        "status": effective_export_status(job),
        "columns": list((job.field_policy or {}).get("columns") or []),
        "scope_snapshot": job.scope_snapshot,
        "row_count": job.row_count,
        "download_count": job.download_count,
        "downloadable": downloadable,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "downloaded_at": job.downloaded_at.isoformat() if job.downloaded_at else None,
        "revoked_at": job.revoked_at.isoformat() if job.revoked_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


def serialize_export_jobs(db: Session, user: UserAccount, jobs: list[ExportJob]) -> list[dict]:
    names = _requester_names(db, jobs)
    return [
        serialize_export_job(
            job,
            requester_name=names.get(job.requested_by),
            downloadable=_is_downloadable(job, user),
        )
        for job in jobs
    ]
