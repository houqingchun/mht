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
from app.models.organization import Student


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
