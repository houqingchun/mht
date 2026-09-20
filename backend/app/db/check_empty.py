"""看一眼「这个库是不是空的」。`python -m app.db.check_empty`

**为什么需要它**（2026-09-18 加）：安装脚本在首次安装时跑两条写库的命令——`app.db.seed`
（写量表、admin 账号、基线任务）与 `app.db.reset_to_baseline --yes`。后一条是**无人值守
的破坏性脚本**：它把这个库清成「只有 admin + 量表与基本配置」，名册、测评、关怀档案、
审计行全部删掉（`sql/reset_to_baseline.sql`）。

那两条命令的前提是「这是一个刚建出来、或者操作员刚建好表的空库」。而安装器怎么判断
`$isUpgrade`？看**安装目录**在不在，不是看库里有没有东西。于是「同一个库 + 一个新的安装
目录」——有人为了修一台坏掉的机器会做的事——恰好落进「首次安装」那一支，而它会把操作员的
数据清掉。三种分工（`Read-DatabaseMode`）里第三种最容易撞上：操作员手上的库往往是**还原
来的一份备份**，那里面有真数据。

**判据只用三张表**：`school` / `user_account` / `student`。它们是最上层的三个"根"——
其余每一张表都直接或间接指着它们（全库没有 `ondelete=`，全是 RESTRICT，见 CLAUDE.md §1），
所以「这三张里有一张有行」等价于「这个库在用」。逐表 COUNT 不划算，取最上层的那三张。

**退出码 1 = 非空**，是「有问题」的意思，但**装不装下去由调用方决定**：安装器用
`-AllowFailure` 收下这个码，说一句 WARN 再继续（操作员选的是「只警告不拦」）。
把它写成「非空就 return 0」是不行的——那样这个模块自己就没法被复用了，而「非空」
这件事本身必须是一个能被断言的事实。
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import Engine, create_engine, func, inspect, select

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403 —— 不 import 的话 Base.metadata.tables 是空的

# ↑ 那一行**不是可有可无的**，也不只是给 `Base.metadata` 填表的礼节：`count_rows` 走的是
# `Base.metadata.tables[name]`，少了它当场 `KeyError: 'school'`，而外层那句
# `except Exception` 会把它报成「读不动这个库：KeyError: 'school'」——一句把「代码里少
# import 了一行」说成「你的库有问题」的提示。真机上撞过（2026-09-18，探测库的 G 状态）。
# `test_ensure_schema.py` 有一条**在子进程里**跑的守卫专门挡它：本进程里
# `conftest.py` 已经 import 过 models 了，所以在本进程里断言的版本永远看不到这个洞。

# 三张最上层的表，见模块开头。**顺序就是它们被引用的方向**（学校 → 账号 → 学生），
# 报出来的时候从最上层往下念，读的人容易对上自己那份名册。
ROOT_TABLES = ("school", "user_account", "student")


def count_rows(engine: Engine, tables: tuple[str, ...] = ROOT_TABLES) -> dict[str, int]:
    """逐表数行数。纯查询，不改任何东西——`test_check_empty.py` 拿一次性 MySQL 库钉它。"""
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for name in tables:
            table = Base.metadata.tables[name]
            counts[name] = connection.execute(select(func.count()).select_from(table)).scalar_one()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.check_empty",
        description="检查这个库里是不是已经有数据了（只读，什么都不改）。",
    )
    parser.parse_args(argv)

    engine = create_engine(get_settings().database_url)
    try:
        # **先看表在不在。** 三张根表都没有时，`count_rows` 会一路撞到驱动上，
        # 报出来是一整段 `ProgrammingError (1146, "Table 'x.school' doesn't exist")`
        # 加 SQLAlchemy 的英文提示——而真相是「这个库还没建表」，一句话的事。
        # 安装流程里这一步排在 `ensure_schema` 与迁移之后，所以它不该发生；
        # 但 `python -m app.db.check_empty` 是个能单独跑的命令，真机上验过这一种。
        with engine.connect() as connection:
            present = set(inspect(connection).get_table_names())
        missing = [name for name in ROOT_TABLES if name not in present]
        if missing:
            print(f"[X] 这个库里没有这几张表：{'、'.join(missing)}")
            print("    也就是说表结构还没建出来，这一步没什么可查的。安装器会在写基础数据")
            print("    之前先校对表结构（`python -m app.db.ensure_schema`），它停下时会逐条")
            print("    说清缺哪张表、缺哪一列。")
            return 1
        counts = count_rows(engine)
    except Exception as exc:  # noqa: BLE001 —— 原因由驱动给，原样带出来比我猜一句准
        print(f"[X] 读不动这个库：{type(exc).__name__}: {exc}")
        return 1
    finally:
        engine.dispose()

    occupied = {name: count for name, count in counts.items() if count}
    if not occupied:
        print("[OK] 这个库是空的（学校 / 账号 / 学生 三张表都没有行）。")
        return 0

    print("[!] 这个库里已经有数据了：")
    for name, count in counts.items():
        print(f"       {name}：{count} 行")
    return 1


if __name__ == "__main__":
    sys.exit(main())
