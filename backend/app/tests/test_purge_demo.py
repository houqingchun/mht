"""演示数据清理（`app/db/purge.py`）。

这个文件曾经是**唯一**一个开着外键检查的：`tests/conftest.py` 的库是内存 SQLite，
而 SQLite 默认根本不检查外键（`PRAGMA foreign_keys` 默认关闭），于是"父行先删"
在测试里永远不报错，只在 MySQL 上炸。`make reset-db` 原来那段内联脚本就是这么坏的：
它把 `risk_event` 排在 `manual_review` 前面，而 `manual_review.risk_event_id` 是指向
`risk_event` 的 NOT NULL 外键——10 行引用着活着的 risk_event，在 InnoDB 上是必然的
1451，而 SQLite 上的每一次运行都全绿。当时的对策是自建一个引擎、在 `connect` 事件里
`PRAGMA foreign_keys=ON`。

2026-09-19 起不需要了：`conftest.py` 的库就是真 MySQL（InnoDB），**外键一直在**，
而且它对**每一个**用例都是这样。那套 sqlite 脚手架连同它的 `StaticPool` 一起删掉了。
`test_the_fixture_really_enforces_foreign_keys` 留着——它现在守的不再是"这个 fixture
与别的不一样"，而是"这套库真的拦得住父行先删"，也就是删除顺序那件事的前提。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.purge import purge_demo_data, reset_assessment_data
from app.db.seed import MHT_SCALE_VERSION
from app.db.seed_demo import demo_roster, seed_demo_data
from app.models.account import UserAccount, UserScope
from app.models.assessment import AssessmentAnswer, AssessmentSession, AssessmentTarget, AssessmentTask
from app.models.audit import AuditLog
from app.models.care import StudentCareCase
from app.models.organization import ClassGroup, Grade, Student
from app.models.reporting import ProfessionalReport, ProfessionalReportVersion
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import DEFAULT_RULE_CONFIG, rule_config_to_json
from app.services.scale_rule_service import MHT_RULE_VERSION


# 「另一个版本号」一律从 `MHT_RULE_VERSION` 派生，不写字面量。下面两条用例造的是
# **已废止的历史版本**，而清理的判据是**当前**那一行——当前那一行会随算法变更 +1。
# 写死版本字面量会在下一次 +1 时撞上 `uq_scale_rule_version(scale_id, rule_version)`，
# 而报出来的是一句 IntegrityError，红在一个与「清理能不能收敛规则行」无关的地方。
_RULE_PREFIX, _, _RULE_PATCH = MHT_RULE_VERSION.rpartition(".")
_OTHER_RULE_VERSIONS = [f"{_RULE_PREFIX}.{int(_RULE_PATCH) + offset}" for offset in (1, 2)]


def _count(db, model) -> int:
    return db.scalar(select(func.count(model.id)))


def test_the_fixture_really_enforces_foreign_keys(db_session):
    """先证明这套库真的拦得住父行先删，否则下面几条断言都可能是假绿。

    删一个还有子行的父行必须报错。这正是 `purge.py` 的删除顺序有测试把守、
    以及 `reset_to_baseline.sql` 里那些次序不是排版要求的原因：没有这条，
    「顺序写反了」和「顺序写对了」在任何断言下长得一模一样。
    """
    seed_demo_data(db_session)
    with pytest.raises(IntegrityError):
        # 12 个关怀档案正引用着这些学生。
        db_session.execute(text("DELETE FROM student"))
        db_session.flush()
    db_session.rollback()


def test_purge_leaves_exactly_the_baseline(db_session):
    seed_demo_data(db_session)
    assert _count(db_session, Student) == 1 + len(demo_roster())  # S001 + 当前演示名册

    summary = purge_demo_data(db_session)

    # 留下的就是 `seed.py` 的那一份。
    assert _count(db_session, Student) == 1
    assert db_session.scalar(select(Student.student_no)) == "S001"
    assert _count(db_session, UserAccount) == 4
    assert _count(db_session, StudentCareCase) == 0
    assert _count(db_session, AssessmentSession) == 0
    assert _count(db_session, AssessmentAnswer) == 0
    assert [g.name for g in db_session.scalars(select(Grade))] == ["初一"]
    assert [c.name for c in db_session.scalars(select(ClassGroup))] == ["1班"]

    # 演示账号的 scope 跟着账号一起没了；S001 那条还在。
    assert _count(db_session, UserScope) == 4
    assert not db_session.scalars(
        select(UserScope).where(UserScope.student_id != db_session.scalar(select(Student.id)))
    ).all()

    # 基线任务与 S001 的目标都被重建，且任务指向当前量表版本——这正是"统一切换到
    # 新版本"要的结果：清理前它挂在已归档的 MHT-1.0.0 上。
    task = db_session.scalar(select(AssessmentTask))
    scale = db_session.scalar(select(AssessmentScale))
    assert task.task_no == "TASK-2026-FALL-MHT"
    assert scale.version == MHT_SCALE_VERSION
    assert task.scale_id == scale.id

    target = db_session.scalar(select(AssessmentTarget))
    assert target.task_id == task.id
    assert target.status == "NOT_STARTED"

    # 演示名册里一个都不许剩。
    left = set(db_session.scalars(select(Student.student_no)))
    assert left & {entry.student_no for entry in demo_roster()} == set()
    assert summary["reseeded"] is True


def test_purge_keeps_organisation_level_audit_but_drops_demo_traces(db_session):
    """§9 的既有规则：命名了学生的行按学生范围走，未命名学生的组织级事件保留。"""
    seed_demo_data(db_session)
    baseline = db_session.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    demo_account = db_session.scalar(select(UserAccount).where(UserAccount.account == "S002"))
    demo_student = db_session.scalar(select(Student).where(Student.student_no == "S002"))

    def add(actor_id, student_id, action):
        db_session.add(
            AuditLog(
                actor_user_id=actor_id,
                action=action,
                resource_type="TEST",
                result="SUCCESS",
                student_id=student_id,
            )
        )

    add(baseline.id, None, "管理员登录")          # 组织级：留
    add(demo_account.id, None, "演示学生登录")     # 演示账号写的：删
    add(baseline.id, demo_student.id, "查看学生详情")  # 关于演示学生的：删
    db_session.flush()

    purge_demo_data(db_session)

    remaining = [row.action for row in db_session.scalars(select(AuditLog))]
    assert remaining == ["管理员登录"]


def test_purge_drops_an_unreachable_archived_scale_and_keeps_the_published_one(db_session):
    """按"有没有人用得上"删，而不是按版本号字面量。

    发新版本会把旧版本归档并保留（§7）——那是对的行为，不能一律删。删的条件是
    它已经彻底够不着：没有任务指向、没有会话用过、没有任何结果引用它的规则版本。
    """
    obsolete = AssessmentScale(code="MHT", name="旧题库", version="MHT-1.0.0", status="ARCHIVED")
    db_session.add(obsolete)
    db_session.flush()
    db_session.add(ScaleQuestion(scale_id=obsolete.id, question_no=1, question_text="旧题", status="ACTIVE"))
    db_session.add(
        ScaleRule(
            scale_id=obsolete.id,
            rule_version="MHT-RULE-1.0.0",
            rule_type="MHT_SCORING",
            status="ACTIVE",
            config_json=rule_config_to_json(DEFAULT_RULE_CONFIG),
        )
    )
    db_session.flush()

    summary = purge_demo_data(db_session)

    assert "MHT-1.0.0" in summary["scales_removed"]
    assert MHT_SCALE_VERSION in summary["scales_kept"]
    assert [s.version for s in db_session.scalars(select(AssessmentScale))] == [MHT_SCALE_VERSION]
    # 题目与规则随量表一起删，不留孤儿行。
    assert _count(db_session, ScaleRule) == 1
    assert all(q.scale_id != obsolete.id for q in db_session.scalars(select(ScaleQuestion)))


def test_purge_normalizes_a_published_scale_rule_set(db_session):
    """反复编辑已发布量表会攒下一堆规则行（§6），清理后回到唯一那一行。"""
    scale = db_session.scalar(select(AssessmentScale))
    for rule_version in _OTHER_RULE_VERSIONS:
        db_session.add(
            ScaleRule(
                scale_id=scale.id,
                rule_version=rule_version,
                rule_type="MHT_SCORING",
                status="RETIRED",
                config_json=rule_config_to_json(DEFAULT_RULE_CONFIG),
            )
        )
    db_session.flush()
    assert _count(db_session, ScaleRule) == 3

    summary = purge_demo_data(db_session)

    assert summary["rules_normalized"] == [MHT_SCALE_VERSION]
    rules = db_session.scalars(select(ScaleRule)).all()
    assert [rule.rule_version for rule in rules] == [MHT_RULE_VERSION]
    assert rules[0].status == "ACTIVE"


def test_purge_never_rewrites_a_threshold_a_school_actually_edited(db_session):
    """学校真改过阈值的量表，规则行与配置都必须原样留下。

    删掉规则行会让已有结果指不明白自己按什么标准判的（§6 就是为此保留已废止版本），
    所以这条守卫比"清理干净"重要：宁可留一行，不可改一行。
    """
    scale = db_session.scalar(select(AssessmentScale))
    active = db_session.scalar(select(ScaleRule))
    edited = rule_config_to_json(DEFAULT_RULE_CONFIG)
    edited["validity_retest_threshold"] = 5
    active.config_json = edited
    db_session.add(
        ScaleRule(
            scale_id=scale.id,
            rule_version=_OTHER_RULE_VERSIONS[0],
            rule_type="MHT_SCORING",
            status="RETIRED",
            config_json=edited,
        )
    )
    db_session.flush()

    summary = purge_demo_data(db_session)

    assert summary["rules_normalized"] == []
    assert _count(db_session, ScaleRule) == 2
    db_session.refresh(active)
    assert active.config_json["validity_retest_threshold"] == 5


def test_purge_is_repeatable(db_session):
    """第二次执行不改变内容（行 id 会变：基线条目是删了重建的）。"""
    seed_demo_data(db_session)
    purge_demo_data(db_session)
    before = (
        _count(db_session, Student),
        _count(db_session, UserAccount),
        _count(db_session, UserScope),
        _count(db_session, AssessmentTask),
        _count(db_session, AssessmentTarget),
        _count(db_session, ScaleRule),
    )

    purge_demo_data(db_session)

    after = (
        _count(db_session, Student),
        _count(db_session, UserAccount),
        _count(db_session, UserScope),
        _count(db_session, AssessmentTask),
        _count(db_session, AssessmentTarget),
        _count(db_session, ScaleRule),
    )
    assert before == after == (1, 4, 4, 1, 1, 1)


def test_purge_drops_the_reports_the_demo_accounts_created(db_session):
    """P1 的专业报告跟着演示账号一起清，基线账号自己建的那一份要留着。

    **两个方向都得断**：只断「演示那份没了」的话，一个「把整张表清空」的实现照样绿，
    而那一句会把真实操作员写过的报告一起删掉——`purge_demo_data` 的承诺是
    「回到只有 `seed.py` 基线的状态」，不是「这张表清空」。守这条的是后半段：那份
    基线报告在清理之后**还在**，且它的版本行也还在（版本行没有 user 列，只能跟着
    报告走，删错方向就是 1451）。

    它此前漏了，而 `ExportJob` 就在同一段代码的旁边（同一个「不是测评数据、但是
    账号的子行」的形状）：e2e 那条报告用例每跑一次建一份报告、从不回收，
    于是共享演示库上这一行单调增长。
    """
    seed_demo_data(db_session)
    school_id = db_session.scalar(select(Student.school_id))
    admin_id = db_session.scalar(select(UserAccount.id).where(UserAccount.account == "admin"))
    demo_id = db_session.scalar(select(UserAccount.id).where(UserAccount.account == "S002"))

    def add_report(number: str, owner_id: int) -> ProfessionalReport:
        report = ProfessionalReport(
            report_no=number,
            school_id=school_id,
            title=f"清理守卫 {number}",
            task_scope_json={"task_ids": []},
            statistics_snapshot_json={"overview": {}},
            created_by=owner_id,
            updated_by=owner_id,
        )
        db_session.add(report)
        db_session.flush()
        db_session.add(
            ProfessionalReportVersion(
                report_id=report.id,
                version_no=1,
                statistics_snapshot_json={"overview": {}},
                created_by=owner_id,
            )
        )
        db_session.flush()
        return report

    add_report("RPT-DEMO-1", demo_id)
    kept = add_report("RPT-BASE-1", admin_id)

    purge_demo_data(db_session)

    assert _count(db_session, ProfessionalReport) == 1, "演示账号建的报告该走，基线账号建的那份该留"
    assert db_session.scalar(select(ProfessionalReport.report_no)) == "RPT-BASE-1"
    assert _count(db_session, ProfessionalReportVersion) == 1, "版本行要跟着它自己的报告走"
    assert db_session.scalar(select(ProfessionalReportVersion.report_id)) == kept.id


def test_reset_clears_assessment_data_but_keeps_the_roster(db_session):
    """`make reset-db` 的语义：清测评数据，不动名册与账号。

    这两件事以前是分开的两步（内联脚本删除 + `make seed`），现在都在一个事务里。
    """
    seed_demo_data(db_session)
    admin = db_session.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    db_session.add(
        AuditLog(actor_user_id=admin.id, action="管理员登录", resource_type="AUTH", result="SUCCESS")
    )
    db_session.flush()

    summary = reset_assessment_data(db_session)

    # 名册留着：S001 + 当前演示名册，账号也一个不少（每个演示学生一个）。
    # **不写死数字**（原来是 28 / 31，而那两对数在 V1.1.3 把演示名册扩到 55 人之后就过期了，
    # 红在一句 `assert 56 == 28` 上——那与"测评数据清干净了没有"毫无关系）。
    # 与上面 `test_purge_leaves_exactly_the_baseline` 同一个写法，也与 §测试注意那条
    # 「数行数要问接口要，不要写死」同源：这条用例要说的是"清理没动名册与账号"，
    # 那就让两边都从**同一份**演示名册派生，它自己变了也不影响这句话。
    assert _count(db_session, Student) == 1 + len(demo_roster())  # 名册留着
    assert _count(db_session, UserAccount) == 4 + len(demo_roster())
    assert _count(db_session, StudentCareCase) == 0
    assert _count(db_session, AssessmentAnswer) == 0
    assert _count(db_session, AuditLog) == 0  # 审计整表清空，与 reset-db 原来的行为一致
    assert _count(db_session, AssessmentTask) == 1  # 基线任务种回来了
    assert _count(db_session, AssessmentTarget) == 1
    assert summary["deleted"]["manual_review"] > 0
