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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError

T = TypeVar("T")

#: 往上试几个号就放弃。真实撞号最多一两轮（同一秒里同时进来的请求数），
#: 这个上限是给「号段被别的东西占住了」那种病态情形兜底的——那时报一句能读的话，
#: 比无限循环或者一句英文的 `IntegrityError` 好。
MAX_ATTEMPTS = 10


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
