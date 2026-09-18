import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import AssessmentResult, AssessmentSession
from app.models.care import StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, Student
from app.security.data_scope import student_scope_predicate
from app.security.permissions import SCOPED, STUDENT_PSYCH_DETAIL, scope_allows
from app.services.assessment_service import latest_session_order
from app.services.export_labels import case_status_label, gender_label, level_label, source_label


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
) -> str:
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
    # 与个案详情对齐的口径 = `latest_session_order`，即**施测时间**优先、id 只兜底。
    # 这里曾经是 `max(id)`，两者在只有系统内作答时结果相同，导入之后就分岔了：
    # 一份上学期普查结果的 id 比本学期系统内的会话更大，`max(id)` 会把去年那份选成本次。
    # 相关子查询保持逐学生的形式，不在 Python 里循环（`latest_session()` 会变成 N+1）。
    latest_session_id = (
        select(AssessmentSession.id)
        .where(AssessmentSession.student_id == Student.id)
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
        row.append(_owner_label(db, care_case.owner_id))
        if include_score:
            row.append(result.total_score if result else "")
        writer.writerow(row)
    return "﻿" + output.getvalue()


def _owner_label(db: Session, owner_id: int | None) -> str:
    if not owner_id:
        return "未分配"
    owner = db.get(UserAccount, owner_id)
    return owner.display_name if owner else "未分配"
