"""Take seeded data back out of a development database.

`seed.py` and `seed_demo.py` only add. This module subtracts, so a database that
has been filled with demo data can be returned to the baseline `make seed`
produces — one school, one grade, one class, one student, one task.

Two entry points, both driven by the Makefile:

* ``reset`` — delete every assessment / care / audit row, then re-seed the
  baseline task. This is what ``make reset-db`` has always meant.
* ``demo`` — the same, plus the demo roster itself: the 27 students
  `seed_demo.demo_roster()` creates, their accounts and scopes, the grades and
  classes that came with them, and the scale version the seed no longer ships.

Nothing here is marked as demo data in the schema, so the roster definition in
`seed_demo` is the only thing that can identify it. That is also why this is
**development-only**: pointed at a production database it would delete real
students whose 学号 happens to fall in the generated range. There is no
confirmation flag — the Makefile target is the guard, and it says so.

Deletion order is child-before-parent and it is not decorative: no model
declares ``ondelete=``, so every FK is RESTRICT and MySQL raises 1451 the moment
a parent goes first. That ordering bug is exactly how the old ``make reset-db``
inline script survived for so long (it deleted `risk_event` before
`manual_review`) — the tests it ran under were in-memory SQLite, whose foreign
key checks are **off by default**, so a wrong order passed silently. Since
2026-09-19 the whole suite runs on real MySQL (CLAUDE.md 已知缺口 3, closed),
so that particular blind spot is gone and `tests/test_purge_demo.py` no longer
needs its own engine to get it.
"""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.db.seed import mht_rule_config, seed_development_data
from app.db.seed_demo import demo_roster
from app.models.account import AuthSession, UserAccount, UserScope
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    AssessmentTaskScope,
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
from app.models.exporting import ExportJob
from app.models.importing import (
    AssessmentExternalResult,
    AssessmentImportBatch,
    AssessmentImportRow,
    StudentAgeChangeLog,
    StudentRosterImportBatch,
    StudentRosterImportRow,
)
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import DEFAULT_RULE_CONFIG, rule_config_from_json, rule_config_to_json
from app.services.scale_rule_service import MHT_RULE_VERSION, RULE_TYPE

# Child before parent. `manual_review` must precede `risk_event` (NOT NULL FK),
# `risk_event` / the care records / `audit_log` must precede `student` and
# `user_account`, and `assessment_answer` must precede `scale_question` — it is
# the only row in the schema that points at a question.
#
# `assessment_target` leads, and not because it is the deepest child — it is a
# leaf that nothing references, and it is the one table V1.2 pointed at both the
# import layer and the sessions (via composite FKs whose local `student_id` is
# NOT NULL, so no `SET NULL` is available). Deleting it first is what makes the
# rest of the order work.
# The V1.2 import / external-result tables come next, for a reason that is not
# obvious from this list: `assessment_external_result` and
# `assessment_import_row` point at **each other** (see `_delete_assessment_rows`),
# and both of them point at `assessment_session` — so every one of them has to go
# before the session it names, which is where the V1.0 entries start.
# `care_case_event` / `assessment_task_scope` / `student_age_change_log` are
# ordinary children of rows further down and could sit anywhere above them.
ASSESSMENT_TABLES = (
    AssessmentTarget,
    StudentAgeChangeLog,
    CareCaseEvent,
    AssessmentTaskScope,
    AssessmentExternalResult,
    AssessmentImportRow,
    AssessmentImportBatch,
    StudentRosterImportRow,
    StudentRosterImportBatch,
    ManualReview,
    RiskEvent,
    FollowUpRecord,
    FamilyContactRecord,
    RetestPlan,
    StudentCareCase,
    DimensionResult,
    AssessmentResult,
    AssessmentAnswer,
    AssessmentSession,
    AssessmentTask,
)


# Pointers that have to be cleared before any of this can be deleted. Every one
# of them is a **nullable** column recording how one row relates to another row
# that is also being deleted, and each is unreachable by ordering alone:
#
#   * **two cycles** — `assessment_import_row` ↔ `assessment_external_result`
#     (each names the other) and two self-references
#     (`assessment_session.supersedes_session_id`,
#     `assessment_import_batch.duplicate_of_batch_id`). No order of two DELETEs
#     survives a cycle; the row that stays is still referenced by the row that
#     went.
#   * **one upward pointer** — `assessment_session.import_batch_id` names an
#     import batch, and the batch is the session's parent, so the session has to
#     be deleted first. Ordering cannot have it both ways.
#
# Clearing them loses nothing: a wipe deletes both ends of every one of these
# edges, so the relationship has no referent left to describe.
# `reset_to_baseline.sql` clears the same list — `test_sql_reset_to_baseline.py`
# compares the two, so change both.
#
# `assessment_target` needs no entry here and instead leads ASSESSMENT_TABLES;
# the three `effective_*` / `supplemented_from_batch_id` columns V1.2 added to it
# look like the same problem, but clearing them is not sufficient — its
# `student_id` is itself an FK column (composite FKs into `assessment_session`
# and `assessment_external_result`) and NOT NULL. Nothing references
# `assessment_target`, so deleting it first is the whole fix.
CLEARED_BEFORE_DELETE = (
    (AssessmentImportRow, "external_result_record_id"),
    (AssessmentImportBatch, "duplicate_of_batch_id"),
    (AssessmentSession, "supersedes_session_id"),
    (AssessmentSession, "import_batch_id"),
)


def _delete_all(db: Session, model) -> int:
    return db.execute(delete(model)).rowcount or 0


def _delete_where(db: Session, model, *criteria) -> int:
    return db.execute(delete(model).where(*criteria)).rowcount or 0


def _delete_assessment_rows(db: Session) -> dict[str, int]:
    """Delete the assessment / care / task rows, in dependency order.

    Deliberately says nothing about `audit_log`: the two callers below disagree
    about it (a reset drops the whole trail; a demo purge keeps the rows that
    describe what the baseline accounts really did), and it is the one table
    whose rows are *about* other rows rather than part of them.
    """
    # See CLEARED_BEFORE_DELETE: the cycles and the upward pointers have to go
    # first, or the deletes below hit `1451 Cannot delete or update a parent row`.
    for model, column in CLEARED_BEFORE_DELETE:
        db.execute(update(model).values({column: None}))
    return {model.__tablename__: _delete_all(db, model) for model in ASSESSMENT_TABLES}


def reset_assessment_data(db: Session) -> dict:
    """清空测评数据并重新种子 —— `make reset-db`。"""
    deleted = _delete_assessment_rows(db)
    deleted[AuditLog.__tablename__] = _delete_all(db, AuditLog)
    seed_development_data(db)
    # See purge_demo_data: the re-seed leaves rows pending, and a caller that runs
    # this twice without flushing would emit those INSERTs after the second run's
    # DELETEs. The CLI commits, but the function should not depend on that.
    db.flush()
    return {"deleted": deleted}


def purge_demo_data(db: Session) -> dict:
    """Remove the demo dataset, leaving exactly what `seed.py` creates."""
    roster = demo_roster()
    demo_nos = [entry.student_no for entry in roster]
    demo_class_keys = {(entry.grade_name, entry.class_name) for entry in roster}
    demo_grade_names = {entry.grade_name for entry in roster}

    summary: dict = {"deleted": {}, "scales_removed": [], "scales_kept": [], "reseeded": False}
    deleted = _delete_assessment_rows(db)

    # ---- the demo roster ----
    # Read the ids before anything is deleted: the scoped audit delete below and
    # every roster delete key off them.
    demo_student_ids = list(db.scalars(select(Student.id).where(Student.student_no.in_(demo_nos))))
    demo_account_ids = list(
        db.scalars(select(UserAccount.id).where(UserAccount.account.in_(demo_nos)))
    )

    # Audit rows are history, and CLAUDE.md §9 already states the rule for
    # keeping history in a scoped world: rows that name a student follow that
    # student's scope, while organisational events (登录 / 导出 / 导入) that name
    # no student are kept. So rows about a demo student, or written by a demo
    # student's account, go; the four baseline accounts' own trail stays.
    deleted[AuditLog.__tablename__] = _delete_where(
        db,
        AuditLog,
        or_(
            AuditLog.student_id.in_(demo_student_ids),
            AuditLog.actor_user_id.in_(demo_account_ids),
        ),
    )
    # Two more children of `user_account` that are **not** assessment data and so
    # are not in ASSESSMENT_TABLES: a login session and an export job. They are
    # deleted only for the demo accounts — `purge_demo_data` keeps the baseline
    # accounts' rows, and `reset_assessment_data` (which deletes no accounts at
    # all) leaves every session alone, so a reset does not log anyone out.
    # They have to go **before** the account rows: `auth_session.user_id` and
    # `export_job.requested_by` are NOT NULL, so there is nothing to null out.
    deleted[AuthSession.__tablename__] = _delete_where(
        db, AuthSession, AuthSession.user_id.in_(demo_account_ids)
    )
    deleted[ExportJob.__tablename__] = _delete_where(
        db, ExportJob, ExportJob.requested_by.in_(demo_account_ids)
    )
    # user_scope next: it points at the account, the student, the class and the
    # grade, so it is the child of all four.
    deleted[UserScope.__tablename__] = _delete_where(
        db, UserScope, UserScope.user_id.in_(demo_account_ids)
    )
    deleted[UserAccount.__tablename__] = _delete_where(
        db, UserAccount, UserAccount.id.in_(demo_account_ids)
    )
    deleted[Student.__tablename__] = _delete_where(db, Student, Student.id.in_(demo_student_ids))

    # ---- grades and classes that the demo cohort brought with it ----
    # Both conditions matter. Name alone would delete a real class that happens
    # to be called 初三/3班; emptiness alone would delete any empty class in a
    # real database. 初一/1班 is in the roster's key set but still holds S001,
    # so it survives on the emptiness check alone.
    for grade_name, class_name in sorted(demo_class_keys):
        class_group = db.scalar(
            select(ClassGroup)
            .join(Grade, Grade.id == ClassGroup.grade_id)
            .where(Grade.name == grade_name, ClassGroup.name == class_name)
        )
        if not class_group:
            continue
        still_used = db.scalar(
            select(func.count(Student.id)).where(Student.class_id == class_group.id)
        ) or db.scalar(
            select(func.count(UserScope.id)).where(UserScope.class_id == class_group.id)
        )
        if still_used:
            continue
        deleted[ClassGroup.__tablename__] = deleted.get(ClassGroup.__tablename__, 0) + 1
        db.delete(class_group)
    db.flush()

    for grade_name in sorted(demo_grade_names):
        grade = db.scalar(select(Grade).where(Grade.name == grade_name))
        if not grade:
            continue
        still_used = (
            (db.scalar(select(func.count(ClassGroup.id)).where(ClassGroup.grade_id == grade.id)) or 0)
            + (db.scalar(select(func.count(Student.id)).where(Student.grade_id == grade.id)) or 0)
            + (db.scalar(select(func.count(UserScope.id)).where(UserScope.grade_id == grade.id)) or 0)
        )
        if still_used:
            continue
        deleted[Grade.__tablename__] = deleted.get(Grade.__tablename__, 0) + 1
        db.delete(grade)
    db.flush()

    # ---- scales nothing can reach any more ----
    summary["scales_removed"], summary["scales_kept"] = _drop_unreachable_scales(db, deleted)
    summary["rules_normalized"] = _normalize_rules(db, deleted)

    # The baseline task and S001's target were deleted a few lines up, and
    # `seed_assessment_task` returns early when the task_no already exists — so
    # they have to be re-created together, which is exactly what re-running the
    # baseline seed does. It is guarded on everything that already exists
    # (school / grade / class / scale), so this only fills the hole.
    seed_development_data(db)
    summary["reseeded"] = True
    summary["deleted"] = deleted
    # `seed_assessment_task` flushes the task but leaves S001's target pending, so
    # without this a caller holding the session (a test builds its session with
    # autoflush=False) would emit that INSERT after the *next* run's deletes and
    # trip the FK. Flushing here makes the postcondition "everything is written"
    # true regardless of how the caller configured its session.
    db.flush()
    return summary


def _drop_unreachable_scales(db: Session, deleted: dict[str, int]) -> tuple[list[str], list[str]]:
    """Delete non-published scale versions nothing points at.

    Expressed structurally rather than by version string, because the reason a
    scale is safe to delete is that it is unreachable, not that it is called
    `MHT-1.0.0`: nothing scores against it, no task targets it, no session was
    taken with it, and no stored result names one of its rule versions.
    (`assessment_result.rule_version` is a string, not an FK — that is the point
    of §6, so it has to be checked by value.) A PUBLISHED version is never
    touched: it is the one the next task will select.

    After `_delete_assessment_rows` the task / session tests are trivially true,
    which is why this runs after it and not before.
    """
    removed: list[str] = []
    kept: list[str] = []
    for scale in db.scalars(select(AssessmentScale).order_by(AssessmentScale.id)).all():
        if scale.status == "PUBLISHED":
            kept.append(scale.version)
            continue
        task_count = db.scalar(
            select(func.count(AssessmentTask.id)).where(AssessmentTask.scale_id == scale.id)
        )
        session_count = db.scalar(
            select(func.count(AssessmentSession.id)).where(AssessmentSession.scale_id == scale.id)
        )
        rule_versions = list(
            db.scalars(select(ScaleRule.rule_version).where(ScaleRule.scale_id == scale.id))
        )
        result_count = (
            db.scalar(
                select(func.count(AssessmentResult.id)).where(
                    AssessmentResult.rule_version.in_(rule_versions)
                )
            )
            if rule_versions
            else 0
        )
        if task_count or session_count or result_count:
            kept.append(scale.version)
            continue
        for model in (ScaleQuestion, ScaleRule):
            deleted[model.__tablename__] = deleted.get(model.__tablename__, 0) + _delete_where(
                db, model, model.scale_id == scale.id
            )
        db.delete(scale)
        removed.append(scale.version)
    db.flush()
    return removed, kept


def _normalize_rules(db: Session, deleted: dict[str, int]) -> list[str]:
    """Collapse a scale's scoring rules back to the one row `seed.py` writes.

    A published scale accumulates a rule row per threshold edit (§6), and every
    such row is kept forever so stored results stay resolvable. A development
    database therefore ends up with rows that are nothing but the residue of
    having clicked through the rule editor — the live one had 21 rows on the old
    scale and 5 on the current one, every one of them equivalent to the default
    thresholds.

    Both halves of the replacement are guarded, so this can never lose a real
    edit:

    * **the rows** — a rule row is deleted only if no `assessment_result` names
      it. After the reset there are no results at all, so in practice that only
      rejects a database where the caller deleted nothing.
    * **the config** — the surviving ACTIVE rule is replaced only if its engine
      config already equals `DEFAULT_RULE_CONFIG`. A scale whose thresholds were
      genuinely edited is left exactly as it is and reported back instead.

    A scale with no ACTIVE rule is skipped entirely: that is a draft in progress
    and its rule is editable in place, so there is no "surviving" value to
    compare against.
    """
    normalized: list[str] = []
    for scale in db.scalars(select(AssessmentScale).order_by(AssessmentScale.id)).all():
        active = db.scalar(
            select(ScaleRule)
            .where(
                ScaleRule.scale_id == scale.id,
                ScaleRule.rule_type == RULE_TYPE,
                ScaleRule.status == "ACTIVE",
            )
            .order_by(ScaleRule.id.desc())
        )
        if active is None:
            continue
        # `rule_config_from_json` drops keys the engine never reads (the import
        # path parks a "status" note in there), so this compares scoring
        # semantics rather than JSON shape.
        if rule_config_to_json(rule_config_from_json(active.config_json)) != rule_config_to_json(
            DEFAULT_RULE_CONFIG
        ):
            continue

        rule_versions = list(
            db.scalars(select(ScaleRule.rule_version).where(ScaleRule.scale_id == scale.id))
        )
        # Already the single canonical row (a database that was only ever seeded):
        # replacing it would be a no-op that still burns a row id and rewrites the
        # one row an audit trail might name. Leave it.
        #
        # 判据是 `MHT_RULE_VERSION`（随这一版程序发布的那个号），不是
        # `rule_version_for(scale.code, scale.version)`：后者由**量表版本**派生，
        # 而这一行的版本号 2026-09-21 因总分口径变更 +1 过一次、量表的版本号没动
        # （理由与 `seed.py` 那一处同源，见 `scale_rule_service.MHT_RULE_VERSION`
        # 的注释）。用派生写法的话，一个刚被 `0019` 升过级的库会被判成「不是规范行」
        # 而整批删掉重建。
        if rule_versions == [MHT_RULE_VERSION]:
            continue
        if db.scalar(
            select(func.count(AssessmentResult.id)).where(
                AssessmentResult.rule_version.in_(rule_versions)
            )
        ):
            continue

        deleted[ScaleRule.__tablename__] = deleted.get(ScaleRule.__tablename__, 0) + _delete_where(
            db, ScaleRule, ScaleRule.scale_id == scale.id
        )
        db.add(
            ScaleRule(
                scale_id=scale.id,
                # 与 `seed.py` 写的那一行**必须**是同一个版本号，否则清过库的库与
                # 全新装的库在「当前规则叫什么」上各说各话（常量自己的注释写了这一条）。
                rule_version=MHT_RULE_VERSION,
                rule_type=RULE_TYPE,
                status="ACTIVE",
                config_json=mht_rule_config(),
            )
        )
        normalized.append(scale.version)
    db.flush()
    return normalized


def _print_summary(title: str, summary: dict) -> None:
    print(f"{title}：")
    for table, count in sorted(summary["deleted"].items()):
        if count:
            print(f"  - {table}: {count}")
    if summary.get("scales_removed"):
        print(f"  删除量表版本: {', '.join(summary['scales_removed'])}")
    if summary.get("scales_kept"):
        print(f"  保留量表版本: {', '.join(summary['scales_kept'])}")
    if summary.get("rules_normalized"):
        print(f"  规则归一化: {', '.join(summary['rules_normalized'])}")
    if summary.get("reseeded"):
        print("  已重新种子基线（学校/年级/班级/学生/任务）")


if __name__ == "__main__":
    import sys

    from app.db.session import SessionLocal

    mode = sys.argv[1] if len(sys.argv) > 1 else "demo"
    if mode not in {"demo", "reset"}:
        raise SystemExit("用法：python -m app.db.purge [demo|reset]")

    with SessionLocal() as session:
        if mode == "reset":
            summary = reset_assessment_data(session)
            _print_summary("测评数据已清空并重新种子", summary)
        else:
            summary = purge_demo_data(session)
            _print_summary("演示数据已清理", summary)
        # 唯一一次提交：中途任何一步抛错，SessionLocal 退出时整体回滚。
        session.commit()
