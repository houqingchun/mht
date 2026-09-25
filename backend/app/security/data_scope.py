"""Row-level data scope: which students a user may touch.

Before this module `user_scope` was written by every account-creation path
(`db/seed.py`, `db/seed_demo.py`, `student_import_service`) but never read for
authorization — `auth_service.serialize_user` only echoed it back to the client.
Two consequences followed:

* every case mutation resolved its target with `db.get(StudentCareCase, case_id)`,
  so any counselor could review, reassign, close or reopen a case given its id;
* `GET /students/{id}/key-questions` — the highest sensitivity tier, already
  guarded by a mandatory purpose and a pre-read audit — resolved the student by
  primary key with no ownership check at all.

Design notes
------------
* Two entry points, one definition of "in scope":
  `ensure_student_in_scope` refuses an id the caller does not own, and
  `student_scope_predicate` narrows a list or aggregate query to the students
  the caller may see. The guard is built on the predicate so the two cannot
  disagree.
* Scope here means row-level access. It is a different vocabulary from the
  capability matrix's SCOPED/SCHOOL levels, which describe what a *role* may do.
  Both are required: the capability answers "may a counselor read case detail at
  all"; this module answers "may *this* counselor read *this* student". The
  aggregate endpoints take the same split — a SCOPED role's rates describe its
  own range, a SCHOOL role's describe the school.
* Fails *safe*, like `permissions.resolve_scope`: an unreadable table, or a user
  with no scope row at all, is denied rather than granted everything.
"""

from sqlalchemy import ColumnElement, false, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount, UserScope
from app.models.enums import ScopeType
from app.models.organization import ClassGroup, Grade, Student


def _class_display_text(grade_name: str, class_name: str) -> str:
    """`初一` + `1班` → `初一 1 班`（§5.2 的示例形状）。

    `ClassGroup.name` 有**两个写入方、两种形状**：种子写的是 `1班`，而名册导入把它
    文件里那一列（`701` 这类编号，见 `student_import_service`）原样存下来。所以这里
    只对「已经以 `班` 结尾」的那一种补空格，其余原样接在年级后面——对着 `701` 猜一个
    `1 班` 是替学校改他们自己的编号，而那个编号的含义没有确认过（CLAUDE.md 缺口 2）。
    """
    if class_name.endswith("班"):
        return f"{grade_name} {class_name[:-1].strip()} 班"
    return f"{grade_name} {class_name}"


def data_scope_summary(db: Session, user: UserAccount) -> dict:
    """The user-facing reading of "which students do I cover" (§5.2 / §5.3).

    **It must be the union of every granted dimension, because that is what
    `student_scope_predicate` grants.** The two used to disagree: the predicate
    is an `or_` over all scope rows, while this function returned at the first
    matching branch (`if grade_ids and not class_ids …`), so a user holding both
    a GRADE and a CLASS row was told "初二 1 班" while the queries underneath
    them also covered a whole grade. The number on screen and the row filter are
    the same question, so they get the same answer.

    `scopeType` names the **single** dimension when exactly one contributes, and
    `MIXED` when several do — `displayText` always lists all of them. A `SCHOOL`
    row subsumes the rest and short-circuits, but only when it actually carries a
    `school_id`: the predicate skips a `SCHOOL` row with a NULL `school_id`
    (`:82`), so reading it as 全校 here would claim a range the queries deny.

    Fail-safe, like the predicate: no usable row yields `NONE`, never 全校.
    """
    scopes = db.scalars(select(UserScope).where(UserScope.user_id == user.id)).all()

    # 全校：与谓词同一判据（`school_id is not None`）。半填的 SCHOOL 行不授予任何东西，
    # 所以它也不该在这里被读成「全校」——那会让屏幕上写着「范围：全校」而下面一个数是空的。
    if any(scope.scope_type == ScopeType.SCHOOL and scope.school_id is not None for scope in scopes):
        return {"scopeType": "SCHOOL", "displayText": "全校", "schoolWide": True}

    dimensions: list[str] = []
    parts: list[str] = []

    grade_ids = sorted({s.grade_id for s in scopes if s.scope_type == ScopeType.GRADE and s.grade_id})
    if grade_ids:
        names = db.scalars(select(Grade.name).where(Grade.id.in_(grade_ids)).order_by(Grade.id)).all()
        dimensions.append("GRADE")
        parts.append("、".join(names) + "年级")

    class_ids = sorted({s.class_id for s in scopes if s.scope_type == ScopeType.CLASS and s.class_id})
    if class_ids:
        rows = db.execute(
            select(Grade.name, ClassGroup.name)
            .join(Grade, Grade.id == ClassGroup.grade_id)
            .where(ClassGroup.id.in_(class_ids))
            .order_by(Grade.id, ClassGroup.id)
        ).all()
        dimensions.append("CLASS")
        parts.append("、".join(_class_display_text(g, c) for g, c in rows))

    student_ids = {s.student_id for s in scopes if s.scope_type == ScopeType.STUDENT and s.student_id}
    if student_ids:
        dimensions.append("STUDENT")
        parts.append(f"指定学生（{len(student_ids)}人）")

    if not parts:
        return {"scopeType": "NONE", "displayText": "未配置数据范围", "schoolWide": False}
    return {
        "scopeType": dimensions[0] if len(dimensions) == 1 else "MIXED",
        "displayText": "、".join(parts),
        "schoolWide": False,
    }


def student_scope_predicate(db: Session, user: UserAccount) -> ColumnElement[bool]:
    """A SQL predicate matching exactly the students this user may see.

    The set-based counterpart to `student_in_scope`: that one answers "may this
    user touch *this* student", this one "which students may this user see at
    all". List and aggregate queries need the latter — resolving a roster row by
    row would mean one scope lookup per student.

    **Never returns None.** An unreadable scope table, or a user with no scope
    row, yields an always-false predicate. A caller that read None as "no filter
    needed" would fail open, which is the one outcome this layer must not
    produce. Callers join `Student` and add this to their WHERE clause.
    """
    try:
        scopes = db.scalars(select(UserScope).where(UserScope.user_id == user.id)).all()
    except SQLAlchemyError:
        return false()
    clauses = []
    for scope in scopes:
        # The `is not None` guards are load-bearing, for the same reason as in
        # `student_in_scope`: a scope row whose grade_id is NULL must not be read
        # as matching a student whose grade_id is also NULL. Without them a
        # half-filled row grants everything.
        if scope.scope_type == ScopeType.SCHOOL and scope.school_id is not None:
            clauses.append(Student.school_id == scope.school_id)
        elif scope.scope_type == ScopeType.GRADE and scope.grade_id is not None:
            clauses.append(Student.grade_id == scope.grade_id)
        elif scope.scope_type == ScopeType.CLASS and scope.class_id is not None:
            clauses.append(Student.class_id == scope.class_id)
        elif scope.scope_type == ScopeType.STUDENT and scope.student_id is not None:
            clauses.append(Student.id == scope.student_id)
    if not clauses:
        # No scope row means no granted range. Every account-creation path writes
        # one, so an account without one has no business reading student data.
        return false()
    return or_(*clauses)


def student_in_scope(db: Session, user: UserAccount, student: Student) -> bool:
    """True when any of the user's scope rows covers this student.

    Deliberately implemented on top of `student_scope_predicate` rather than as
    a second reading of the same rules, so the per-row guard and the query
    filter cannot drift apart — a student the roster shows must be a student the
    case endpoints accept. It costs one extra SELECT against an object already in
    memory; a single definition of "in scope" is worth that.
    """
    return (
        db.scalar(
            select(Student.id).where(Student.id == student.id, student_scope_predicate(db, user))
        )
        is not None
    )


def ensure_student_in_scope(db: Session, user: UserAccount, student_id: int) -> Student:
    """Load a student the caller may act on, or raise.

    404 when the student does not exist, 403 when it exists but is out of range —
    the same distinction the capability layer draws between NOT_FOUND and
    SCOPE_FORBIDDEN.
    """
    student = db.get(Student, student_id)
    if not student:
        raise AppError("NOT_FOUND", "学生不存在", 404)
    if not student_in_scope(db, user, student):
        raise AppError("SCOPE_FORBIDDEN", "该学生不在你的数据范围内", 403)
    return student
