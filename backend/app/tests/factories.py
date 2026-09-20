"""测试里造「四层事实」那几个核心对象的工厂。

## 为什么这些函数必须存在

V1.2 给 `assessment_session` / `assessment_target` / `risk_event` 各添了**一个没有
默认值**的 NOT NULL 列（`school_id` / `school_id_snapshot` / `signal_type`）。它们
没有默认值是因为那个值只能从别处推出来，而写库的人手里正好有那个别处：

| 列 | 值从哪来 |
|---|---|
| `assessment_session.school_id` | 学生的学校——复合外键 `(school_id, student_id) → student (school_id, id)` |
| `assessment_target.school_id_snapshot` | 同上 |
| `risk_event.signal_type` | `risk_type` 经 `scale_engine.signal_type_for` 推出来 |

生产代码上这三处落点是**六个构造点**（`db/seed.py`、`task_service`、
`assessment_import_service` ×2、`assessment_service` ×2）。测试上有近二十处。
把「这三列怎么填」写在这里一次，下次再加一个这样的列时，改的是这一个文件，
而不是二十个 `AssessmentSession(...)`。

## 它们**不**隐藏任何东西

每个工厂只做「把能从参数推出来的列补齐」，其余全走 `**overrides` 直通——所以
用例仍然能显式给 `status` / `submitted_at` / `task_id`，也没有哪个字段会**默默**
被工厂改写。这一点是刻意的：测试夹具一旦开始猜调用方的意思，出错的测试就不再
说明被测代码错了。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import AssessmentSession, AssessmentTarget, AssessmentTask, RiskEvent
from app.models.organization import Student
from app.models.scale import AssessmentScale
from app.scale_engine.engine import requires_manual_review, signal_type_for


def published_scale(db: Session) -> AssessmentScale:
    """种子里那个已发布的量表。造会话/结果行都要它。"""
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.status == "PUBLISHED"))
    assert scale is not None, "种子数据里没有已发布的量表"
    return scale


def make_sitting(
    db: Session, student: Student, *, scale: AssessmentScale | None = None, **overrides: Any
) -> AssessmentSession:
    """给这名学生开一场会话。

    默认 `task_id` 为 None（种子里那场任务与用例自己的任务往往无关），
    所以它默认是「一场不属于任何任务的会话」——`test_assessment_import_api.py`
    里那几处一直就是这个形状。
    """
    scale = scale or published_scale(db)
    # 默认值走 `setdefault` 而不是写在构造调用里：写在调用里的话，调用方一旦显式传
    # `status=`，Python 会因为「同一关键字给了两次」直接抛 `TypeError`
    # （`AssessmentSession() got multiple values for keyword argument 'status'`）——
    # 而这条报错说的是 Python 的调用约定，与「这个夹具不许我指定状态」这件事毫无关系。
    overrides.setdefault("status", "SUBMITTED")
    session = AssessmentSession(
        student_id=student.id,
        # V1.2：没有默认值，只能从学生身上取。取 `student.school_id` 而不是某个
        # `school.id`——落库的复合外键两侧同源于这一列。
        school_id=student.school_id,
        scale_id=scale.id,
        scale_version=scale.version,
        **overrides,
    )
    db.add(session)
    db.flush()
    return session


def make_target(
    db: Session, student: Student, task: AssessmentTask, **overrides: Any
) -> AssessmentTarget:
    """把一名学生放进这场任务的目标行。"""
    overrides.setdefault("status", "NOT_STARTED")
    target = AssessmentTarget(
        task_id=task.id,
        student_id=student.id,
        # V1.2：同 `make_sitting`，没有默认值。
        school_id_snapshot=student.school_id,
        **overrides,
    )
    db.add(target)
    db.flush()
    return target


def make_risk_event(db: Session, session: AssessmentSession, **overrides: Any) -> RiskEvent:
    """从一场会话上记一条风险提示。

    `signal_type` / `requires_manual_review` **由 `risk_type` 推出来**，走的是生产
    那条路上同一个函数（`assessment_service.maybe_raise_risk_events` 里那两行）。
    这样测试与生产的判定不会各说各话；映射本身对不对由
    `test_scale_engine.py` 逐条钉住。
    """
    overrides.setdefault("risk_type", "MANUAL_REVIEW_REQUIRED")
    overrides.setdefault("risk_level", "HIGH_SENSITIVITY")
    overrides.setdefault("trigger_rule", "KEY_QUESTION_85_YES")
    overrides.setdefault("status", "PENDING")
    overrides.setdefault("created_at", datetime.now())
    overrides.setdefault("rule_version", session.scale_version)
    signal_type = signal_type_for(overrides["risk_type"])
    event = RiskEvent(
        student_id=session.student_id,
        session_id=session.id,
        signal_type=signal_type,
        requires_manual_review=requires_manual_review(signal_type),
        **overrides,
    )
    db.add(event)
    db.flush()
    return event
