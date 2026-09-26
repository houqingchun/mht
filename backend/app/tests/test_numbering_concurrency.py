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

import pymysql
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.seed import seed_development_data
from app.models.account import UserAccount
from app.models.assessment import AssessmentTask
from app.models.reporting import ProfessionalReport, ProfessionalReportVersion
from app.schemas.reporting import ReportCreateRequest
from app.services import reporting_service, task_service
from app.services.numbering import DEADLOCK_ATTEMPTS, is_deadlock
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


# --------------------------------------------------------------------------
# 死锁（1213 / 1305）：整段重跑（CLAUDE.md §33）
# --------------------------------------------------------------------------


def _deadlock_error(errno: int = 1213) -> OperationalError:
    """一个**真的** `OperationalError`，裹着 MySQL 真会发出的那个 errno。

    不用字符串假造：`is_deadlock` 读的是 `err.orig.args[0]`，而 pymysql 的异常类给出的
    `.args` 形状与真库上一模一样。文案照抄 MySQL 那句——判据不看它，但读日志的人看。
    """
    return OperationalError(
        "INSERT INTO professional_report (report_no, ...) VALUES (%s, ...)",
        None,
        pymysql.err.OperationalError(
            errno, "Deadlock found when trying to get lock; try restarting transaction"
        ),
    )


def _deadlock_once(real, calls: list, errno: int):
    """把 `insert_with_unique_number` 换成一个「第一次撞死锁」的替身。

    **它真的插进去再撞**，不是「什么都不做就抛」——这一点是刻意的：死锁发生时那一行
    **确实被数据库写过**（然后被 InnoDB 连同整个事务一起回滚掉），所以重跑机制要面对的
    正是「数据库里没有、而 SQLAlchemy 以为有」这个状态。替身用一个裸 `ROLLBACK` 制造它，
    而不是 `db.rollback()`：后者会把会话状态一并清干净，恰好把要验的那件事验没了。
    """

    def flaky(db, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            real(db, **kwargs)
            db.execute(text("ROLLBACK"))
            raise _deadlock_error(errno)
        return real(db, **kwargs)

    return flaky


def _report_payload(task_id: int, title: str) -> ReportCreateRequest:
    return ReportCreateRequest(
        title=title,
        task_ids=[task_id],
        analysis_mode="ALL_CALCULATED",
        overall_summary="整体平稳。",
    )


def _reports_titled(engine, title: str) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count(ProfessionalReport.id)).where(ProfessionalReport.title == title)
        )


def _versions_of_reports_titled(engine, title: str) -> int:
    with Session(engine) as session:
        return session.scalar(
            select(func.count(ProfessionalReportVersion.id))
            .select_from(ProfessionalReportVersion)
            .join(ProfessionalReport, ProfessionalReport.id == ProfessionalReportVersion.report_id)
            .where(ProfessionalReport.title == title)
        )


@pytest.mark.parametrize("errno", [1213, 1305])
def test_a_deadlocked_report_creation_is_rerun_and_nothing_leaks(numbering_engine, monkeypatch, errno):
    """一次死锁之后报告照样建得出来，而且重跑**没有留下第一次的痕迹**。

    两种 errno 都参数化进来，因为它们是**同一次并发里都出现过**的两个码（1305 是死锁
    回滚掉 savepoint 之后的次生错），只验 1213 的话另一半请求没有守卫。

    四条判据一起断，少一条就有一条假绿的路：
    · 替身被调用了**两次** —— 第一次真的撞了、第二次才成功（少了它，一个从来没死锁过的
      实现也全绿）；
    · 号是**第一个**（`base + 1`）—— 回滚把那个号让了回去，重跑不留空洞；
    · 重跑写下的东西**还在事务里**（先 `rollback()` 一次，库里应当一行都没有）——
      不验这条的话，一个「重跑时写入溜出了事务边界」的实现也能过前两条；
    · 最终库里恰好 **1 行报告 + 1 行版本**，标题一行都不多。
    """
    monkeypatch.setattr("app.services.reporting_service.datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)
    today = PINNED.strftime("%Y%m%d")
    title = f"死锁重跑 {errno}"

    with Session(engine) as session:
        task_id = session.scalar(select(AssessmentTask.id).order_by(AssessmentTask.id))
        base = session.scalar(
            select(func.count(ProfessionalReport.id)).where(
                ProfessionalReport.report_no.like(f"RPT-{today}-%")
            )
        ) or 0

    calls: list[int] = []
    real = reporting_service.insert_with_unique_number
    monkeypatch.setattr(
        reporting_service, "insert_with_unique_number", _deadlock_once(real, calls, errno)
    )

    session = Session(engine)
    try:
        report = create_report(session, session.get(UserAccount, user_id), _report_payload(task_id, title))
        assert len(calls) == 2, "第一次没有撞死锁——那这条用例证明不了重跑"
        assert report.report_no == f"RPT-{today}-{base + 1:04d}", "回滚之后号没有让回去"
        # 还在事务里：此刻回滚掉，这次重跑写下的东西应当全部消失。
        session.rollback()
    finally:
        session.close()

    assert _reports_titled(engine, title) == 0, "重跑写下的行溜出了事务边界（回滚不掉）"

    # 再来一次，替身不再撞（失败次数用完了），走的是真路径。
    # 这一段存在的理由：上面那次 `session.rollback()` 把「重跑真的能留下一份完整记录」
    # 这件事一起验没了，所以要另跑一次完整的「建 → 提交 → 查」。
    session = Session(engine)
    try:
        create_report(session, session.get(UserAccount, user_id), _report_payload(task_id, title))
        session.commit()
    finally:
        session.close()

    assert _reports_titled(engine, title) == 1
    assert _versions_of_reports_titled(engine, title) == 1, "重跑留下一份孤儿版本行"


def test_a_deadlock_on_the_second_flush_still_reruns_clean(numbering_engine, monkeypatch):
    """死锁落在**第二次** flush 上时，重跑照样干净——这一条是那句 `db.rollback()` 的守卫。

    上面那条用例里，死锁发生时会话中只有一个**已经 flush 过**的 `report`（SQLAlchemy
    以为它在库里、对象也是干净的）。而真实的死锁**也可能落在 `_create_report` 里写版本
    行的那一次 flush 上**——那时会话里多了一个「刚 `add` 进来、还没 flush」的 `version`，
    它的 `report_id` 已经指向一个**马上要被回滚掉**的行。

    数据库从底下把事务回滚掉之后，`db.rollback()` 是唯一把会话拉回一致状态的东西：
    摘掉它，重跑时那一次 flush 会把这个孤儿 `version` 一并发出去，撞一句 **1452**
    （外键找不到父行）——而 1452 既不是死锁、也接不住，用户拿到的是另一句英文的 500。
    这一条比上面那条晚一步，正是为了落在「有脏对象」的那半边上。

    用事件钩子而不是替身函数：这一步要精确落在第**二**次 flush 上，而 flush 是
    `_create_report` 内部发起的，从外面替不掉。
    """
    monkeypatch.setattr("app.services.reporting_service.datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)
    title = "死锁落在第二次 flush"

    with Session(engine) as session:
        task_id = session.scalar(select(AssessmentTask.id).order_by(AssessmentTask.id))

    flushes: list[int] = []

    def on_before_flush(session, flush_context, instances):
        flushes.append(1)
        if len(flushes) == 2:
            # 死锁的后果：数据库替我们回滚了整个事务，而 SQLAlchemy 这边不知道。
            session.execute(text("ROLLBACK"))
            raise _deadlock_error(1213)

    session = Session(engine)
    event.listen(session, "before_flush", on_before_flush)
    try:
        report = create_report(
            session, session.get(UserAccount, user_id), _report_payload(task_id, title)
        )
        # 「先证明有东西可扫」：两次 flush 真的都发生了（第 2 次抛），重跑又各来了一次。
        # 不写死一个精确数——SQLAlchemy 内部多一次少一次 flush 不是这条用例要说的事。
        assert len(flushes) >= 4, f"flush 次数不对（{len(flushes)}），这条用例没走在预期路径上"
        assert report.report_no
        session.commit()
    finally:
        event.remove(session, "before_flush", on_before_flush)
        session.close()

    assert _reports_titled(engine, title) == 1
    assert _versions_of_reports_titled(engine, title) == 1, "重跑留下一份孤儿版本行"


def test_a_deadlock_that_never_clears_is_raised_as_it_is(numbering_engine, monkeypatch):
    """三次都撞死锁 → 原样抛出去。不吞、不换成别的错、也不无限重试。

    `len(calls) == DEADLOCK_ATTEMPTS` 是这条用例的全部信息量：只写 `pytest.raises`
    的话，一个「第一次就抛」的实现也是绿的。
    """
    monkeypatch.setattr("app.services.reporting_service.datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)

    with Session(engine) as session:
        task_id = session.scalar(select(AssessmentTask.id).order_by(AssessmentTask.id))

    calls: list[int] = []

    def always_deadlocked(db, **kwargs):
        calls.append(1)
        raise _deadlock_error(1213)

    monkeypatch.setattr(reporting_service, "insert_with_unique_number", always_deadlocked)

    session = Session(engine)
    try:
        with pytest.raises(OperationalError) as caught:
            create_report(session, session.get(UserAccount, user_id), _report_payload(task_id, "死锁到底"))
    finally:
        session.close()

    assert is_deadlock(caught.value), "抛出来的已经不是那一句死锁——中途被换成了别的错"
    assert len(calls) == DEADLOCK_ATTEMPTS


def test_an_operational_error_that_is_not_a_deadlock_is_not_retried(numbering_engine, monkeypatch):
    """判据是 **errno**，不是「异常类型是 `OperationalError`」。

    2006（server has gone away）是一个**真的**故障，重试三遍只会让用户多等一会儿，
    并且把一次「数据库没了」伪装成一次「刚才有点挤」。所以它必须一次都不重跑。
    """
    monkeypatch.setattr("app.services.reporting_service.datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)

    with Session(engine) as session:
        task_id = session.scalar(select(AssessmentTask.id).order_by(AssessmentTask.id))

    calls: list[int] = []

    def server_gone(db, **kwargs):
        calls.append(1)
        raise OperationalError(
            "SELECT 1", None, pymysql.err.OperationalError(2006, "MySQL server has gone away")
        )

    monkeypatch.setattr(reporting_service, "insert_with_unique_number", server_gone)

    session = Session(engine)
    try:
        with pytest.raises(OperationalError) as caught:
            create_report(session, session.get(UserAccount, user_id), _report_payload(task_id, "不是死锁"))
    finally:
        session.close()

    assert not is_deadlock(caught.value)
    assert len(calls) == 1, "一个不是死锁的 OperationalError 被重跑了"


def test_a_deadlocked_task_creation_is_rerun_too(numbering_engine, monkeypatch):
    """第二个调用点（`create_school_assessment_task`）也走同一条路。

    两个调用点各写一遍薄壳，所以**摘掉其中一个**不会有任何东西变红——这条用例就是
    为那一半写的。
    """
    monkeypatch.setattr(task_service, "datetime", _PinnedDatetime)
    engine = numbering_engine
    user_id = _account_id(engine)

    calls: list[int] = []
    real = task_service.insert_with_unique_number
    monkeypatch.setattr(task_service, "insert_with_unique_number", _deadlock_once(real, calls, 1213))

    name = "死锁重跑的测评任务"
    session = Session(engine)
    try:
        task = create_school_assessment_task(
            session, session.get(UserAccount, user_id), name=name, start_at=None, end_at=None
        )
        assert len(calls) == 2, "第一次没有撞死锁——那这条用例证明不了重跑"
        assert task.task_no, "重跑之后任务没有单号"
        session.commit()
    finally:
        session.close()

    with Session(engine) as check:
        assert check.scalar(
            select(func.count(AssessmentTask.id)).where(AssessmentTask.name == name)
        ) == 1, "重跑留下了两份同名任务"
