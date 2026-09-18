"""把库清成「只有 admin + 量表与基本配置」。`python -m app.db.reset_to_baseline --yes`

**这里不重写第二份实现。** `sql/reset_to_baseline.sql` 是那套清理的**唯一**出处，
而且它已经被 `app/tests/test_sql_reset_to_baseline.py` 按 `Base.metadata` 的外键图
逐条验过（覆盖面与 `purge.py` 对齐、子先父后、每条 DELETE 都挂在 `@admin_id` 上）。
在 Python 里再写一遍必然漂移，而漂移的后果是「删完没有人能登录」那一类。
这个脚本做的事只有三件：找到那个文件、把它整份发给 MySQL、把它自己打的字念出来。

为什么要走**裸 pymysql** 而不是 SQLAlchemy：整个文件是一个事务里的三十多条语句，
其中 `SET @admin_id := (SELECT …)` 是**会话变量**，下面的 DELETE 全靠它。会话变量在
同一条连接上才有效，而 `CLIENT.MULTI_STATEMENTS` 是把它一次发出去的前提。
SQLAlchemy 的 mysql 方言自己算 `client_flag`，`connect_args` 里再塞一个能不能覆盖
取决于版本——这件事不该靠猜。

**必须显式加 `--yes`。** 这一步删的是真数据，而安装脚本是无人值守跑的：一个打错的
子命令不该顺手把库清了。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql
from pymysql.constants import CLIENT

from app.core.config import get_settings
from app.db.create_database import explain
from app.db.mysql_url import DatabaseUrlError, parse_database_url

# `backend/app/db/reset_to_baseline.py` → parents[2] 就是 `backend/`。
SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "reset_to_baseline.sql"


def read_sql(path: Path = SQL_FILE) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"找不到清理脚本：{path}")
    return path.read_text(encoding="utf-8")


def run_sql(target, sql: str) -> list[tuple]:
    """执行整份 SQL，把里面每一条 SELECT 的结果按顺序收回来。

    这个文件里有两种 SELECT：开头那句「找没找到 admin」的锚点报告、以及结尾那张
    「跑完看一眼」的对账表。两者都是**给人看的**，所以要原样念出来。
    """
    connection = pymysql.connect(
        **target.connect_kwargs(),
        client_flag=CLIENT.MULTI_STATEMENTS,
        autocommit=False,
    )
    results: list[tuple] = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            while True:
                if cursor.description is not None:
                    columns = [column[0] for column in cursor.description]
                    rows = cursor.fetchall()
                    results.append((columns, rows))
                # 不排干的话下一条语句永远发不出去（多语句模式下结果集是一个队列）。
                if not cursor.nextset():
                    break
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return results


def print_results(results: list[tuple]) -> None:
    for columns, rows in results:
        widths = [len(str(name)) for name in columns]
        for row in rows:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], len(_cell(value)))
        print("  " + "  ".join(str(name).ljust(widths[index]) for index, name in enumerate(columns)))
        print("  " + "  ".join("-" * width for width in widths))
        for row in rows:
            print("  " + "  ".join(_cell(value).ljust(widths[index]) for index, value in enumerate(row)))
        print()


def _cell(value) -> str:
    return "NULL" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.reset_to_baseline",
        description="把库清成「只有 admin + 量表与基本配置」。只删行，不建表、不建行。",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="真的要动手。不加这一条只报告，什么都不删。",
    )
    args = parser.parse_args(argv)

    try:
        target = parse_database_url(get_settings().database_url)
    except DatabaseUrlError as exc:
        print(f"[X] {exc}")
        return 1

    if not args.yes:
        # 退出码给 1 而不是 0：安装脚本按退出码串下一步，一次「什么都没做」不该被读成成功。
        print(f"[!] 没有 --yes，什么都没有做。这条命令会清空 {target.database} 里的：")
        print("    测评与关怀的全部事实、审计行、除 admin 之外的所有账号与范围、")
        print("    整份名册（学生 / 班级 / 年级）、未发布的量表版本。")
        print("    保留：admin 账号、已发布量表与其题目、评分规则、学校、配置与权限矩阵。")
        print(f"    确认要执行请加 --yes：python -m app.db.reset_to_baseline --yes")
        return 1

    try:
        sql = read_sql()
    except FileNotFoundError as exc:
        print(f"[X] {exc}")
        return 1

    print(f"[..] 清理 {target.host}:{target.port}/{target.database}")
    try:
        results = run_sql(target, sql)
    except (pymysql.err.MySQLError, OSError, RuntimeError) as exc:
        print(f"[X] {explain(exc)}")
        return 1

    print("[OK] 清理完成。脚本自己打出来的两张表：")
    print()
    print_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
