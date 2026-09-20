"""测试库的建、拆，与「一次性数据库」。

2026-09-19 之前 `make test` 跑在**内存 SQLite** 上、表由 `Base.metadata.create_all`
建出来、完全不跑 Alembic（CLAUDE.md 已知缺口 3）。那件事的代价在那一天兑现了：
开发库被手工导入成 V1.2 的 35 张表，代码与 ORM 还停在 V1.0，`assessment_session.school_id`
等三个 NOT NULL 列没有任何写入方——学生一开卷子就是 MySQL 1364——而 523 个测试**一个都没红**。

原因不是测试写得不好，是**表由模型建出来的**：模型少三列，它就建一张少三列的表，
于是「模型和自己一致」这条命题永远成立，而它跟真库没有任何关系。

现在建表走 `alembic upgrade head`（`run_migrations` 就是人工会跑的那一条命令）。
代价是 `make test` 需要一台可达的 MySQL 与一份建库权限；换来的是
**模型少一列而迁移没少，测试会当场红**。

## 为什么用子进程跑 alembic，而不是 `alembic.command.upgrade`

`alembic/env.py:18` **无条件**用 `get_settings().database_url` 覆盖
`config.set_main_option("sqlalchemy.url", ...)`，而 `alembic.ini:4` 里那个值是
一个真实（且无关）的连接串。所以想改库只有两条路：在 `get_settings()` 的
`lru_cache` 上做手脚，或者改 `env.py` 去尊重调用方——后者会让
`alembic.ini` 里那个死值突然活过来。两条都不划算。

子进程只贵一次（每个 session 一次），而且**跑的就是操作员会跑的那条命令**。

## 名字必须以 `_test` 结尾

`ensure_database()` 会 `DROP DATABASE`。指错库就是销毁一份真实数据，
所以 `resolve_test_database_url()` 在库名不以 `_test` 结尾时**直接拒绝**——
按本仓库既有口径（`reset_to_baseline.sql` 的「找不到 admin 时一条都不删」），
宁可拦住也不要赌。
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pymysql
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

# app/tests/mysql_support.py → tests → app → backend
BACKEND_DIR = Path(__file__).resolve().parents[2]

TEST_DB_SUFFIX = "_test"
CHARSET = "utf8mb4"
# 与 `sql/schema_mysql8.sql` 每张表的表尾、以及 `backend/sql/reset_to_baseline.sql`
# 的用法逐字一致。建库时**显式**写它：靠 MySQL 8 的默认值恰好相同只是运气，
# 而 `test_sql_schema_matches_models.py` 逐字断言的就是这个串——库的 collation
# 与表的 collation 不一致时，字符串比较的行为会跟着变。
COLLATION = "utf8mb4_0900_ai_ci"

_throwaway_counter = itertools.count(1)


def resolve_test_database_url() -> URL:
    """测试库在哪。`XLP_TEST_DATABASE_URL` 优先，否则把开发库名加 `_test`。"""
    explicit = os.environ.get("XLP_TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        from app.core.config import get_settings

        base = make_url(os.environ.get("XLP_DATABASE_URL") or get_settings().database_url)
        url = base.set(database=(base.database or "xinliceping") + TEST_DB_SUFFIX)

    if url.get_backend_name() != "mysql":
        raise RuntimeError(
            f"测试库必须是 MySQL（CLAUDE.md：后续开发一律基于 MySQL），"
            f"而这个连接串是 {url.get_backend_name()}：{url.render_as_string(hide_password=True)}"
        )
    if not (url.database or "").endswith(TEST_DB_SUFFIX):
        raise RuntimeError(
            f"拒绝在这个库上跑测试：库名 {url.database!r} 不以 {TEST_DB_SUFFIX!r} 结尾。\n"
            f"这套 fixture 会 DROP DATABASE，指错库就是销毁一份真实数据。\n"
            f"要么把库名改成以 {TEST_DB_SUFFIX} 结尾，要么显式设 XLP_TEST_DATABASE_URL。"
        )
    return url


def _server_connection(url: URL):
    """连到**服务器**而不是某个库上（建库/删库要用这个）。"""
    try:
        return pymysql.connect(
            host=url.host or "127.0.0.1",
            port=url.port or 3306,
            user=url.username or "root",
            password=url.password or "",
            charset=CHARSET,
            autocommit=True,
        )
    except pymysql.err.OperationalError as exc:  # pragma: no cover - 环境问题
        raise RuntimeError(
            f"连不上 MySQL：{url.host or '127.0.0.1'}:{url.port or 3306}"
            f"（用户 {url.username or 'root'}）。\n"
            f"`make test` 现在跑在真 MySQL 上，不再回退 SQLite。"
            f"起一个 MySQL 8，或用 XLP_TEST_DATABASE_URL 指到别处。\n原始错误：{exc}"
        ) from exc


def drop_database(url: URL) -> None:
    """删库。库名与服务器从 `url` 里取——所以它只会删 `url` 指的那一个。"""
    connection = _server_connection(url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{url.database}`")
    finally:
        connection.close()


def ensure_database(url: URL) -> None:
    """**先删后建**，母版与 collation 显式给。

    刻意不复用上一次留下的库：留着它就等于把「迁移链能不能从零建出这套结构」
    这个问题的答案缓存下来了，而那个问题恰恰是这一段存在的全部理由。
    """
    connection = _server_connection(url)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{url.database}`")
            cursor.execute(
                f"CREATE DATABASE `{url.database}` CHARACTER SET {CHARSET} COLLATE {COLLATION}"
            )
    finally:
        connection.close()


def run_migrations(url: URL, revision: str = "head") -> None:
    """在 `url` 上跑 `alembic upgrade <revision>`。

    走子进程与环境变量，因为 `alembic/env.py:18` 无条件覆盖 `sqlalchemy.url`
    （见模块 docstring）。
    """
    env = dict(os.environ)
    env["XLP_DATABASE_URL"] = url.render_as_string(hide_password=False)
    # 别让一个恰好存在的 .env 盖过我们要指的那个库：`env_file` 是 CWD 相对的，
    # 而这里 CWD 就是 backend/。
    env.pop("XLP_TEST_DATABASE_URL", None)

    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:  # pragma: no cover - 迁移坏了才会走到
        raise RuntimeError(
            f"`alembic upgrade {revision}` 在 {url.render_as_string(hide_password=True)} 上失败：\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )


def engine_for(url: URL) -> Engine:
    """一个不带连接池复用的引擎。

    `StaticPool` 与 `pool_pre_ping` 都不要：测试自己管事务的生命周期
    （见 `conftest.py` 的 `db_session`），连接复用到别的用例上会把上一个用例
    的未提交状态一起带过去。
    """
    return create_engine(url)


@contextmanager
def throwaway_database(*, with_schema: bool = True, base_url: URL | None = None) -> Iterator[URL]:
    """一个用完就删的库。

    给那些**必须自己建库**的用例用——`app/db/ensure_schema.py` 与
    `app/db/check_empty.py` 都是拿一个连接串进门、自己建引擎的独立命令，
    所以它们没法共用 `conftest.py` 那个被事务罩住的 session。

    2026-09-19 之前这三处各自建一个 sqlite 文件或内存库。换成 MySQL 之后
    「一个真的建了表、但一行都没有的库」才第一次是**生产上那个东西**。
    """
    base = base_url or resolve_test_database_url()
    url = base.set(database=f"{base.database}_x{next(_throwaway_counter)}")
    ensure_database(url)
    if with_schema:
        run_migrations(url)
    try:
        yield url
    finally:
        drop_database(url)
