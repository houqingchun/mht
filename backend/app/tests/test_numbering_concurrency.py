"""同一秒里两个请求都建单号时，谁都不会拿到一句英文的 500。

## 它守的是什么

`task_no` 与 `report_no` 从前是「读一个计数 → 拼一个号 → 插进去」，两个请求落在
同一秒（任务）或同一天（报告）里就各算到同一个号，撞上唯一键的那个拿到 500。
修法（往上试一个号，见 `services/numbering.py`）**只有在真的并发时才走得到那一条分支**
——串行跑一百遍也只会走「第一轮就成功」那一路，所以它必须被**造**出来。

## 为什么自建一个库、两个 session

`conftest.py` 的 `override_get_db` yield 的是那个共享的 `db_session`，而撞号这件事的
全部内容就是「**两个事务**各自插一行同一个号」——一个 session 上根本表达不出来
（同一条 session 里第二次插入只是它自己看得见的一次 flush）。所以这里走
`throwaway_database()` + 两个独立 `Session`，与生产同形。

## 撞号是**造**出来的，不是等出来的

`task_no` 带「到秒」的时间戳、`report_no` 带日期，所以「两个请求落在同一秒」在真实
时钟下是碰运气的事——一条要靠运气才红的守卫，在快机器上几乎从不红（CLAUDE.md §18：
「一条会无故变红的守卫很快会被人关掉」的反面同样成立：**一条几乎从不红的守卫等于
没有**）。所以这里把模块里的 `datetime` 换成一个钉死的子类，两个事务于是**必然**
算到同一个号。

第二个事务的 INSERT 会被第一个**还没提交**的那一行挡住（InnoDB 的唯一性检查要等
那一行的锁），等对方提交之后拿到 1062 —— 那正是 `insert_with_unique_number` 要接住
的那一步。所以第一个事务**故意慢提交**（另一个线程里睡 1 秒），否则它可能在第二个
事务开始之前就提交完，第二个事务直接读到新计数、根本不撞。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.seed import seed_development_data
from app.models.account import UserAccount
from app.models.assessment import AssessmentTask
from app.models.reporting import ProfessionalReport
from app.schemas.reporting import ReportCreateRequest
from app.services import task_service
from app.services.reporting_service import create_report
from app.services.task_service import create_school_assessment_task
from app.tests.mysql_support import engine_for, throwaway_database

#: 「现在」被钉在这一刻。日期本身没有意义，有意义的是它**每次都一样**。
PINNED = datetime(2026, 1, 1, 12, 0, 0)
STAMP = "20260101120000"


class _PinnedDatetime(datetime):
    """把「现在」钉死的 `datetime` 替身（`monkeypatch.setattr(模块, "datetime", 它)`）。

    做成 `datetime` 的子类而不是一个只有两个方法的假对象：模块里还有
    `datetime.fromisoformat`（`parse_datetime`）之类**别的**类方法在用同一个名字，
    一个 duck-typed 的替身会把它们一起弄坏，而报出来的错与这里要证明的事毫无关系。
    """

    @classmethod
    def now(cls, tz=None):
        return PINNED if tz is None else PINNED.replace(tzinfo=tz)

    @classmethod
    def utcnow(cls):
        return PINNED


@pytest.fixture(scope="module")
def numbering_engine():
    """一个建好了表、种好了基线的**一次性**库 + 一个引擎。

    module 作用域：建库 + 跑迁移 + 种子是这条链上最贵的一段，而两个用例各自
    只需要自己的两个 session（它们之间没有共享状态，也不会互相看到对方提交的行，
    因为每一个断言都只跟自己那一次的两个号打交道）。
    """
    with throwaway_database() as url:
        engine = engine_for(url)
        with Session(engine) as session:
            seed_development_data(session)
            session.commit()
        yield engine
        engine.dispose()


def _account_id(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(UserAccount.id).where(UserAccount.account == "13800000001"))


def _commit_late(session: Session, seconds: float = 1.0) -> threading.Thread:
    """在另一个线程里**慢提交**：让第二个事务先进到「已经算好号、正在插」那一步。

    睡 1 秒是刻意的裕量——第二个事务从「开始」到「INSERT 被挡住」只有毫秒级，
    而这里要保证的是「它一定先到」。真实撞号也是这个次序（两个请求各插各的，
    谁先提交谁赢），区别只是这里把「同时」写成了「先来后到」。
    """

    def run():
        time.sleep(seconds)
        session.commit()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


# --------------------------------------------------------------------------
# 测评任务单号
# --------------------------------------------------------------------------


def test_two_tasks_created_in_the_same_second_both_get_a_number(numbering_engine, monkeypatch):
    """同一秒的两个建任务请求：一个拿到原号，另一个**往上试一个**，两个都成功。"""
    monkeypatch.setattr(task_service, "datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)

    with Session(engine) as session:
        base = session.scalar(select(func.count(AssessmentTask.id))) or 0

    first = Session(engine)
    second = Session(engine)
    try:
        # 第一个事务：号算好了、行还没提交。
        task1 = create_school_assessment_task(
            first, first.get(UserAccount, user_id), name="并发一号", start_at=None, end_at=None
        )
        # 先把号读出来：`commit()` 之后属性会过期，而那次刷新会落在**另一个线程**
        # 刚用过的 session 上（能跑，但没有必要去趟这个浑水）。
        number1 = task1.task_no
        assert number1 == f"TASK-{STAMP}-{base + 1}", "钉死的时钟没生效，这条用例就证明不了任何事"

        thread = _commit_late(first)
        # 第二个事务：算到同一个号、插进去、被第一个挡住，等它提交之后拿到 1062、
        # 往上试一个号、成功。
        task2 = create_school_assessment_task(
            second, second.get(UserAccount, user_id), name="并发二号", start_at=None, end_at=None
        )
        number2 = task2.task_no
        thread.join(timeout=10)
        second.commit()
    finally:
        first.close()
        second.close()

    assert number1 != number2, "两个请求拿到了同一个单号——唯一键本该挡住第二个"
    assert {number1, number2} == {f"TASK-{STAMP}-{base + 1}", f"TASK-{STAMP}-{base + 2}"}

    # 两个号都真的落了库，而且库里没有第二个同号的（「成功了」与「写进去了」是两件事）。
    with Session(engine) as session:
        rows = session.scalars(
            select(AssessmentTask.task_no).where(AssessmentTask.task_no.like(f"TASK-{STAMP}-%"))
        ).all()
    assert sorted(rows) == sorted([number1, number2])


# --------------------------------------------------------------------------
# 专业报告单号
# --------------------------------------------------------------------------


def test_two_reports_created_in_the_same_day_both_get_a_number(numbering_engine, monkeypatch):
    """报告那一侧同一形状，只是号段按**天**分（`RPT-<日期>-<四位序号>`）。"""
    monkeypatch.setattr("app.services.reporting_service.datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)
    today = PINNED.strftime("%Y%m%d")

    with Session(engine) as session:
        task_id = session.scalar(select(AssessmentTask.id).order_by(AssessmentTask.id))
        base = session.scalar(
            select(func.count(ProfessionalReport.id)).where(
                ProfessionalReport.report_no.like(f"RPT-{today}-%")
            )
        ) or 0

    def payload(title: str) -> ReportCreateRequest:
        return ReportCreateRequest(
            title=title,
            task_ids=[task_id],
            analysis_mode="ALL_CALCULATED",
            overall_summary="整体平稳。",
        )

    first = Session(engine)
    second = Session(engine)
    try:
        report1 = create_report(first, first.get(UserAccount, user_id), payload("并发报告一"))
        number1 = report1.report_no
        assert number1 == f"RPT-{today}-{base + 1:04d}", "钉死的时钟没生效"

        thread = _commit_late(first)
        report2 = create_report(second, second.get(UserAccount, user_id), payload("并发报告二"))
        number2 = report2.report_no
        thread.join(timeout=10)
        second.commit()
    finally:
        first.close()
        second.close()

    assert number1 != number2
    assert {number1, number2} == {f"RPT-{today}-{base + 1:04d}", f"RPT-{today}-{base + 2:04d}"}

    with Session(engine) as session:
        rows = session.scalars(
            select(ProfessionalReport.report_no).where(
                ProfessionalReport.report_no.like(f"RPT-{today}-%")
            )
        ).all()
    assert sorted(rows) == sorted([number1, number2])
