"""演示数据清理（`app/db/purge.py`）。

与其他用例最大的差别：**这个文件开着外键**。

`tests/conftest.py` 的库是内存 SQLite，而 SQLite 默认根本不检查外键——
`PRAGMA foreign_keys` 默认关闭。于是"父行先删"在测试里永远不报错，只在 MySQL 上炸。
`make reset-db` 原来那段内联脚本就是这么坏的：它把 `risk_event` 排在
`manual_review` 前面，而 `manual_review.risk_event_id` 是指向 `risk_event` 的
NOT NULL 外键——10 行引用着活着的 risk_event，在 InnoDB 上是必然的 1451，
而 SQLite 上的每一次运行都全绿。

所以这里的库自己建，并在 `connect` 事件里开 `PRAGMA foreign_keys=ON`：pragma 在事务里
是空操作，所以必须在连接建立时设，不能在用例里 `PRAGMA` 一句了事；`StaticPool` 保证
整个用例共用那一条连接，后面每一条 DELETE 都吃到。共享的 `db_session` fixture 建不了
这个 pragma（引擎在 fixture 内部创建），改了它又会影响全部 173 个既有用例——它们中间
有不少并不满足外键顺序，而且从来不需要满足。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.purge import purge_demo_data, reset_assessment_data
from app.db.seed import MHT_SCALE_VERSION, seed_development_data
from app.db.seed_demo import demo_roster, seed_demo_data
from app.models.account import UserAccount, UserScope
from app.models.assessment import AssessmentAnswer, AssessmentSession, AssessmentTarget, AssessmentTask
from app.models.audit import AuditLog
from app.models.care import StudentCareCase
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, Student
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.scale_engine.engine import DEFAULT_RULE_CONFIG, rule_config_to_json
from app.services.scale_rule_service import rule_version_for


@pytest.fixture()
def fk_session():
    """一个开着外键检查的内存库，种好基线数据。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with TestingSessionLocal() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        seed_development_data(session)
        session.commit()
        yield session


def _count(db, model) -> int:
    return db.scalar(select(func.count(model.id)))


def test_the_fixture_really_enforces_foreign_keys(fk_session):
    """先证明这个测试文件与别的文件不同，否则下面几条断言都可能是假绿。

    删一个还有子行的父行必须报错；SQLite 关掉外键时同样一段代码会静默成功——
    那正是 `make reset-db` 原来那段内联脚本能一直"正常"跑下去的原因。
    """
    seed_demo_data(fk_session)
    with pytest.raises(IntegrityError):
        # 12 个关怀档案正引用着这些学生。
        fk_session.execute(text("DELETE FROM student"))
        fk_session.flush()
    fk_session.rollback()


def test_purge_leaves_exactly_the_baseline(fk_session):
    seed_demo_data(fk_session)
    assert _count(fk_session, Student) == 28  # S001 + 27 演示学生

    summary = purge_demo_data(fk_session)

    # 留下的就是 `seed.py` 的那一份。
    assert _count(fk_session, Student) == 1
    assert fk_session.scalar(select(Student.student_no)) == "S001"
    assert _count(fk_session, UserAccount) == 4
    assert _count(fk_session, StudentCareCase) == 0
    assert _count(fk_session, AssessmentSession) == 0
    assert _count(fk_session, AssessmentAnswer) == 0
    assert [g.name for g in fk_session.scalars(select(Grade))] == ["初一"]
    assert [c.name for c in fk_session.scalars(select(ClassGroup))] == ["1班"]

    # 演示账号的 scope 跟着账号一起没了；S001 那条还在。
    assert _count(fk_session, UserScope) == 4
    assert not fk_session.scalars(
        select(UserScope).where(UserScope.student_id != fk_session.scalar(select(Student.id)))
    ).all()

    # 基线任务与 S001 的目标都被重建，且任务指向当前量表版本——这正是"统一切换到
    # 新版本"要的结果：清理前它挂在已归档的 MHT-1.0.0 上。
    task = fk_session.scalar(select(AssessmentTask))
    scale = fk_session.scalar(select(AssessmentScale))
    assert task.task_no == "TASK-2026-FALL-MHT"
    assert scale.version == MHT_SCALE_VERSION
    assert task.scale_id == scale.id

    target = fk_session.scalar(select(AssessmentTarget))
    assert target.task_id == task.id
    assert target.status == "NOT_STARTED"

    # 演示名册里一个都不许剩。
    left = set(fk_session.scalars(select(Student.student_no)))
    assert left & {entry.student_no for entry in demo_roster()} == set()
    assert summary["reseeded"] is True


def test_purge_keeps_organisation_level_audit_but_drops_demo_traces(fk_session):
    """§9 的既有规则：命名了学生的行按学生范围走，未命名学生的组织级事件保留。"""
    seed_demo_data(fk_session)
    baseline = fk_session.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    demo_account = fk_session.scalar(select(UserAccount).where(UserAccount.account == "S002"))
    demo_student = fk_session.scalar(select(Student).where(Student.student_no == "S002"))

    def add(actor_id, student_id, action):
        fk_session.add(
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
    fk_session.flush()

    purge_demo_data(fk_session)

    remaining = [row.action for row in fk_session.scalars(select(AuditLog))]
    assert remaining == ["管理员登录"]


def test_purge_drops_an_unreachable_archived_scale_and_keeps_the_published_one(fk_session):
    """按"有没有人用得上"删，而不是按版本号字面量。

    发新版本会把旧版本归档并保留（§7）——那是对的行为，不能一律删。删的条件是
    它已经彻底够不着：没有任务指向、没有会话用过、没有任何结果引用它的规则版本。
    """
    obsolete = AssessmentScale(code="MHT", name="旧题库", version="MHT-1.0.0", status="ARCHIVED")
    fk_session.add(obsolete)
    fk_session.flush()
    fk_session.add(ScaleQuestion(scale_id=obsolete.id, question_no=1, question_text="旧题", status="ACTIVE"))
    fk_session.add(
        ScaleRule(
            scale_id=obsolete.id,
            rule_version="MHT-RULE-1.0.0",
            rule_type="MHT_SCORING",
            status="ACTIVE",
            config_json=rule_config_to_json(DEFAULT_RULE_CONFIG),
        )
    )
    fk_session.flush()

    summary = purge_demo_data(fk_session)

    assert "MHT-1.0.0" in summary["scales_removed"]
    assert MHT_SCALE_VERSION in summary["scales_kept"]
    assert [s.version for s in fk_session.scalars(select(AssessmentScale))] == [MHT_SCALE_VERSION]
    # 题目与规则随量表一起删，不留孤儿行。
    assert _count(fk_session, ScaleRule) == 1
    assert all(q.scale_id != obsolete.id for q in fk_session.scalars(select(ScaleQuestion)))


def test_purge_normalizes_a_published_scale_rule_set(fk_session):
    """反复编辑已发布量表会攒下一堆规则行（§6），清理后回到唯一那一行。"""
    scale = fk_session.scalar(select(AssessmentScale))
    for suffix in ("1.1.1", "1.1.2"):
        fk_session.add(
            ScaleRule(
                scale_id=scale.id,
                rule_version=f"MHT-RULE-{suffix}",
                rule_type="MHT_SCORING",
                status="RETIRED",
                config_json=rule_config_to_json(DEFAULT_RULE_CONFIG),
            )
        )
    fk_session.flush()
    assert _count(fk_session, ScaleRule) == 3

    summary = purge_demo_data(fk_session)

    assert summary["rules_normalized"] == [MHT_SCALE_VERSION]
    rules = fk_session.scalars(select(ScaleRule)).all()
    assert [rule.rule_version for rule in rules] == [rule_version_for("MHT", MHT_SCALE_VERSION)]
    assert rules[0].status == "ACTIVE"


def test_purge_never_rewrites_a_threshold_a_school_actually_edited(fk_session):
    """学校真改过阈值的量表，规则行与配置都必须原样留下。

    删掉规则行会让已有结果指不明白自己按什么标准判的（§6 就是为此保留已废止版本），
    所以这条守卫比"清理干净"重要：宁可留一行，不可改一行。
    """
    scale = fk_session.scalar(select(AssessmentScale))
    active = fk_session.scalar(select(ScaleRule))
    edited = rule_config_to_json(DEFAULT_RULE_CONFIG)
    edited["validity_retest_threshold"] = 5
    active.config_json = edited
    fk_session.add(
        ScaleRule(
            scale_id=scale.id,
            rule_version="MHT-RULE-1.1.1",
            rule_type="MHT_SCORING",
            status="RETIRED",
            config_json=edited,
        )
    )
    fk_session.flush()

    summary = purge_demo_data(fk_session)

    assert summary["rules_normalized"] == []
    assert _count(fk_session, ScaleRule) == 2
    fk_session.refresh(active)
    assert active.config_json["validity_retest_threshold"] == 5


def test_purge_is_repeatable(fk_session):
    """第二次执行不改变内容（行 id 会变：基线条目是删了重建的）。"""
    seed_demo_data(fk_session)
    purge_demo_data(fk_session)
    before = (
        _count(fk_session, Student),
        _count(fk_session, UserAccount),
        _count(fk_session, UserScope),
        _count(fk_session, AssessmentTask),
        _count(fk_session, AssessmentTarget),
        _count(fk_session, ScaleRule),
    )

    purge_demo_data(fk_session)

    after = (
        _count(fk_session, Student),
        _count(fk_session, UserAccount),
        _count(fk_session, UserScope),
        _count(fk_session, AssessmentTask),
        _count(fk_session, AssessmentTarget),
        _count(fk_session, ScaleRule),
    )
    assert before == after == (1, 4, 4, 1, 1, 1)


def test_reset_clears_assessment_data_but_keeps_the_roster(fk_session):
    """`make reset-db` 的语义：清测评数据，不动名册与账号。

    这两件事以前是分开的两步（内联脚本删除 + `make seed`），现在都在一个事务里。
    """
    seed_demo_data(fk_session)
    admin = fk_session.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    fk_session.add(
        AuditLog(actor_user_id=admin.id, action="管理员登录", resource_type="AUTH", result="SUCCESS")
    )
    fk_session.flush()

    summary = reset_assessment_data(fk_session)

    assert _count(fk_session, Student) == 28  # 名册留着
    assert _count(fk_session, UserAccount) == 31
    assert _count(fk_session, StudentCareCase) == 0
    assert _count(fk_session, AssessmentAnswer) == 0
    assert _count(fk_session, AuditLog) == 0  # 审计整表清空，与 reset-db 原来的行为一致
    assert _count(fk_session, AssessmentTask) == 1  # 基线任务种回来了
    assert _count(fk_session, AssessmentTarget) == 1
    assert summary["deleted"]["manual_review"] > 0
