"""业务单号的分配 —— 全站唯一一处「撞号怎么办」的定义。

## 它修的是什么

`task_no` 与 `report_no` 此前都是「**读一个计数、拼一个号、然后插进去**」：

```python
count = db.scalar(select(func.count(AssessmentTask.id))) or 0
task_no = f"TASK-{utcnow:%Y%m%d%H%M%S}-{count + 1}"
```

两个请求落在同一秒里就各算一次同一个数、各插一行同一个号，撞上唯一键的那个拿到
**500**（实测 `req1 http=500 / req2 http=200`）。CLAUDE.md §29 那条 1213 死锁记的是
同一类形状的另一面：**「两次写入之间有先后要求时，顺序要由书写者保证」**——这里则是
「唯一键是数据库已经有的那个判据，书写者要负责把它撞上之后的出路写出来」。

## 为什么是「往上试一个」而不是「重读一遍再算」

最直觉的修法是「撞了就重新数一遍」。**它在 MySQL 上不成立**：默认隔离级别是
REPEATABLE-READ，事务的快照在第一条语句时就定下来了，所以重读到的还是撞车那个数
（实测确认过），于是重试一轮、再撞一次、十轮之后照样 500。

换 `SELECT … FOR UPDATE` 也不对，而且是两个层次的错：库里那一行**还不存在**，
所谓「锁一行」锁的是它会落进去的那个**间隙**——而两个间隙锁在 InnoDB 里是**互相兼容**
的，两边都拿得到，接着两边都插，撞的是间隙里的插入意向锁：**1213 死锁**
（CLAUDE.md §28 拒绝 `FOR UPDATE` 的理由与此逐字相同）。

所以这里不猜、也不锁：**让唯一键自己说话**。撞了就把序号往上试一个，再插一次；
每一次都是数据库在回答「这个号有人用了吗」，而不是我们在读一份可能已经过期的快照。
序号是递增的，所以试的第二个号必然还没人用（除非又有人插进来，那就再试一个）。

## 为什么整段包在 savepoint 里

`IntegrityError` 会让**整个事务**进入失败状态，不套 savepoint 的话连下一条语句都发不
出去——这与 `assessment_service.open_or_reuse_care_case` 里那段是同一个手法、同一个
理由（CLAUDE.md §28）。

## ★ 上面那段推理只挡住了一半：1213 还是会来，而且接不住（2026-09-26）

上面写着「换 `FOR UPDATE` 会变成 1213 死锁」，读起来像「我们选了这条路，所以躲开了
死锁」。**不是。** 2026-09-26 实测（12 个并发 `POST /professional-reports`，5 workers
的 e2e 全量下也稳定复现）：**「撞号就往上试一个」这个模式自己会造出 1213**。

真凭实据是 `SHOW ENGINE INNODB STATUS` 的 `LATEST DETECTED DEADLOCK`（两个事务的形态
逐字相同）：

```
(1) HOLDS  : index PRIMARY of professional_report  lock_mode X   ← 记录 hex 73757072656d756d
(1) WAITING: index PRIMARY of professional_report  lock_mode X insert intention waiting
(2) HOLDS  : 同一条记录、同一个模式
(2) WAITING: 同一条记录、同一个模式        →  WE ROLL BACK TRANSACTION (2)
```

`73757072656d756d` 是 `supremum`（页尾上界）。也就是说：双方**都已经**在页尾插过一
行、各自持有那一处的 X 锁，然后又都要往同一个位置插——环就是这么来的。`AUTO_INCREMENT`
主键的行**必然**落在页尾，所以这不是「运气不好」，而是并发往同一张表插多轮时**结构性
存在**的形状；第一轮撞号失败的越多人，第二轮同时去要插入意向锁的就越多。

**这一层接不住它，是因为 InnoDB 回滚的是整个事务，不是回滚到 savepoint。** 死锁一发生，
那个 `begin_nested()` 的 savepoint 一起消失，于是紧接着的 savepoint 操作报
`1305 SAVEPOINT … does not exist`——实测同一次并发里 **1213 与 1305 两种码都出现过**
（1305 有 2 次），而两者的业务栈**都停在下面那个 `db.flush()` 上**。所以只认 1213 的话，
一半的请求仍然会带着一句英文的 500 出去。

出路只有一条：**在「整个操作」那一层重跑**——`run_until_not_deadlocked`。它不能住在这
个函数里：这里能做的只有「换一个号再插一次」，而死锁之后连语句都发不出去。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.errors import AppError

T = TypeVar("T")

#: 往上试几个号就放弃。真实撞号最多一两轮（同一秒里同时进来的请求数），
#: 这个上限是给「号段被别的东西占住了」那种病态情形兜底的——那时报一句能读的话，
#: 比无限循环或者一句英文的 `IntegrityError` 好。
MAX_ATTEMPTS = 10

#: InnoDB 的死锁码，**以及它的次生码**。
#:
#: 1305 不是「另一个错」，它就是死锁的另一面：InnoDB 检测到死锁后回滚**整个事务**，
#: `begin_nested()` 建的那个 savepoint 一起消失，紧接着的 savepoint 操作就报
#: `SAVEPOINT … does not exist`。2026-09-26 实测同一次并发里两种码都出现过，业务栈
#: 都在 `insert_with_unique_number` 的同一个 `flush()` 上。只认 1213 的话，另一半请求
#: 仍然会带着一句英文的 500 出去。
DEADLOCK_ERRNOS = (1213, 1305)

#: 整段操作遇到死锁时重跑几次（含第一次）。三次足够：死锁是「同一瞬间的争用」，
#: 而每一次重跑都会拿到一个更靠后的号，撞在同一个位置的概率逐次下降。
DEADLOCK_ATTEMPTS = 3

#: 重跑之前的退避，按次数线性增长（0.05s / 0.10s）。它不是为了「等锁释放」——
#: 死锁发生时 InnoDB 已经替我们回滚、锁也放了——而是为了**把重跑的这几个请求错开**，
#: 免得三次尝试又落回同一个瞬间。
DEADLOCK_BACKOFF_SECONDS = 0.05


def insert_with_unique_number(
    db: Session,
    *,
    number_at: Callable[[int], str],
    build: Callable[[str], T],
    attempts: int = MAX_ATTEMPTS,
) -> T:
    """插入一行带唯一单号的记录，**撞号就往上试一个**。

    - `number_at(offset)`：第 `offset` 轮该用的单号（`offset` 从 0 开始）。
      调用方在里面把「基数 + offset」拼成自己那一种形状，所以两个调用点各自
      保留自己可读的编号规则，不必在这里统一。
    - `build(number)`：用这个号造出一个（还没入库的）ORM 实例。

    判据是**我们刚试的那个号本身**出现在错误信息里，不是去认索引名：
    MySQL 的 1062 里写着 `Duplicate entry '<号>' for key '<表>.<索引>'`，
    而那个号只有这一种可能出现在这里。认索引名的话，索引一旦被改名（或者某天
    换了个方言），这条守卫会**静默地**把撞号当成别的完整性错误往外抛。

    单号用完（`attempts` 轮都撞）时报 409 而不是 500：那说明这一秒里挤进了十个
    以上的并发请求，用户该做的是重试一次，而不是看到一句数据库的英文。
    """
    for offset in range(attempts):
        number = number_at(offset)
        try:
            with db.begin_nested():
                row = build(number)
                db.add(row)
                db.flush()
        except IntegrityError as err:
            if number not in str(err.orig):
                # 不是撞号（外键、非空、别的唯一键……）：原样往外抛，
                # 别把一句真实的数据库错报成「单号冲突」。
                raise
            continue
        return row
    raise AppError(
        "NUMBER_CONFLICT",
        "单号分配冲突，请稍后重试",
        409,
    )


def is_deadlock(err: BaseException) -> bool:
    """这句 `OperationalError` 是不是 InnoDB 的死锁（或它的那个次生 savepoint 错）。

    判据是 **errno**（`err.orig.args[0]`），不是错误文案：文案跟着 MySQL 的
    `lc_messages` 走，而一个中文/日文的服务器会让逐字匹配的判据静默失效。
    """
    args = getattr(getattr(err, "orig", None), "args", None) or ()
    return bool(args) and args[0] in DEADLOCK_ERRNOS


def run_until_not_deadlocked(
    db: Session,
    operation: Callable[[], T],
    attempts: int = DEADLOCK_ATTEMPTS,
) -> T:
    """把 `operation()` 跑完；**死在死锁上就整段重跑**（最多 `attempts` 次）。

    ## 为什么是「整段」而不是「再插一次」

    死锁让 InnoDB 回滚**整个事务**（不是回滚到 savepoint），所以 `insert_with_unique_number`
    内部接不住：它下一句要发的 `ROLLBACK TO SAVEPOINT` 会报 1305——把真因替换成一句更假
    的错。**只有在整个事务之外重跑**才是对「事务已经被数据库杀了」这件事的正确处置，这也
    正是 MySQL 那句话自己说的（`Deadlock found when trying to get lock; try restarting
    transaction`）。

    ## 调用方要保证什么（**这条契约是本函数全部的风险所在**）

    `operation` 必须**可重跑**：它到失败那一刻为止做过的一切，重跑之后不能留下痕迹。
    实际判据有两条，两条都要成立：

    1. **写入都发生在「可能死锁的那一句」之后**。否则重跑会把失败之前已经写下的东西
       再写一遍（或者更糟：`db.rollback()` 把它们静默丢掉，而调用方以为自己写过了）。
       现在的两个调用点都满足：它们进门先做几次只读查询，第一个写入就是那次插入。
    2. **它自己不提交**。事务的提交归路由（`get_db` 从不提交）。若哪天有人在
       `operation` 里 `db.commit()`，而 1213 恰好发生在提交那一刻，那「重跑」就可能把
       一次**已经生效**的写入做第二遍——本函数挡不住那个形状，所以把话写在这里。

    失败时 `db.rollback()` 是**必须的**，不是保险：InnoDB 回滚了事务，而 SQLAlchemy
    不知道。摘掉它，重跑时那一次 flush 会把会话里**已经 add、还没发出去**的写入一并
    发出去，而它们的引用（版本行的 `report_id`、答卷的 `session_id` 那一类）指向的是
    **刚刚被回滚掉**的那一行——于是撞一句 1452，而不是死锁；重跑机制接不住它，
    用户拿到的是第二句英文的 500。
    （`test_numbering_concurrency.py::test_a_deadlock_on_the_second_flush_still_reruns_clean`
    钉住这个形状：死锁落在**第二次** flush 上时，会话里正躺着这样一个孤儿引用。）
    """
    attempt = 0
    while True:
        try:
            return operation()
        except OperationalError as err:
            attempt += 1
            if not is_deadlock(err) or attempt >= attempts:
                raise
            db.rollback()
            time.sleep(DEADLOCK_BACKOFF_SECONDS * attempt)
