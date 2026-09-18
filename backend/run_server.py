"""后台常驻的启动器。`python backend/run_server.py`（CLAUDE.md §18）

**为什么不是 `uvicorn app.main:app`。** 两个原因，都只在生产里才显形：

1. **日志会全丢。** 计划任务起的进程没有控制台，uvicorn 默认往 stdout/stderr 打的东西
   在那两路里是丢掉的——服务起不来的时候，运维手上什么都没有。这里自己配
   `RotatingFileHandler`，再把 `log_config=None` 传给 uvicorn（否则它会把 root logger
   整个换掉，我们的 handler 就白配了）。
2. **`.env` 是相对当前工作目录的。** `Settings` 的 `env_file=".env"`，而计划任务的
   `<WorkingDirectory>` 写错了就会**静默**用上默认值——连到 `127.0.0.1` 的开发库上，
   而报出来的错与「数据库密码填错了」长得一样。这里自己 `chdir` 到本文件所在目录
   （也就是 `backend/`，与 `make backend` / `make migrate` 的工作目录一致），
   这件事就不再依赖外部配置对不对。`alembic.ini` 与 `sql/` 的相对路径同理。

3. **计划任务不会替我们重启一个崩掉的进程。** 这一条最反直觉，也是这个文件里那个
   `while True` 存在的唯一理由：Task Scheduler 的「失败时重新启动」**只在它自己
   启动不了这个动作时**才生效（凭据不对、可执行文件找不到、ACL 拒绝）。动作只要起来了，
   它就不再看退出码——`RestartOnFailure` 于是形同虚设，而用户的要求正是「长久运行」。
   所以重试循环必须长在我们自己身上：**MySQL 没起来就等着**（开机时它与 MySQL 服务
   同时起步，冷启动下 InnoDB 重做日志大的实例超过一分钟很正常，而 Task Scheduler
   的任务**不参与 SCM 的依赖图**，写不了 `depend= MySQL80`），**崩了就退避重来**。

   正常关停（收到 SIGTERM/SIGINT，`uvicorn.run` 正常返回）**不重启**，直接退出——
   否则在终端里按 Ctrl-C 就变成按不掉了。计划的「停止」走的是硬杀，不走这条。

这一层刻意保持薄：真正的应用还是 `app.main:app`，所以 `pytest`、`make e2e`、
`uvicorn --reload` 那几条路一行都没有变。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------- 先定位，再导入
#
# `app.core.config` 被导入的那一刻不会读 `.env`（读发生在 `Settings()` 实例化时），
# 但**顺序上仍然先 chdir**：这个约定要一眼看得出来，不能依赖「哪个模块在什么时候
# 实例化了一次」。
BACKEND_DIR = Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)

import uvicorn  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402

LOG_FILE_NAME = "server.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"

# 等数据库：第一次先等一下（开机那一刻 MySQL 多半还没好），之后每次翻倍，封顶 30 秒。
# 不设总超时——「一直等」比「等不到就死掉」好：这台机器上没有人会去看它退出了。
DB_WAIT_INITIAL_SECONDS = 2.0
DB_WAIT_MAX_SECONDS = 30.0
# 崩了之后隔多久重来。不设次数上限，理由同上。
CRASH_RETRY_SECONDS = 30.0


def configure_logging(settings: Settings) -> Path | None:
    """配 root logger，返回日志文件路径（没配 `log_dir` 时返回 None）。

    控制台那一路**始终留着**：装成计划任务之后它确实没地方去，但同一个脚本在调试时
    是人在终端里跑的，那时候看不到输出才叫难办。
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if not settings.log_dir:
        return None

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    return log_dir / LOG_FILE_NAME


def database_reachable(settings: Settings) -> tuple[bool, str]:
    """连一次库，`SELECT 1`。返回 (通不通, 不通的原因)。

    **刻意不用 `app.db.session` 的那个 engine。** 它是模块级的、带连接池的单例，
    而这里要回答的是「此刻能不能连上」——池子里一条坏连接会让答案与事实相反。
    每次新建一条裸连接，问完就关，答案才是当下的。

    也不用 `create_database` 那套校验：那个脚本是给安装器用的，会打一屏中文处置建议，
    而这里每秒问一次，打出来的东西只会把日志淹掉。
    """
    from app.db.mysql_url import DatabaseUrlError, parse_database_url

    try:
        import pymysql
    except ImportError as exc:  # 依赖没装全 —— 说清楚是哪一个
        return False, f"没有 pymysql：{exc}"

    try:
        target = parse_database_url(settings.database_url)
    except DatabaseUrlError as exc:
        return False, f"XLP_DATABASE_URL 有问题：{exc}"

    try:
        conn = pymysql.connect(**target.connect_kwargs(), connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 —— 任何连不上的原因都归到「再等等」
        return False, f"{type(exc).__name__}: {exc}"
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return True, ""


def wait_for_database(settings: Settings, logger: logging.Logger) -> None:
    """等到能连上库为止。连不上就永远等下去（退避打印，不刷屏）。"""
    delay = DB_WAIT_INITIAL_SECONDS
    attempts = 0
    while True:
        reachable, reason = database_reachable(settings)
        if reachable:
            if attempts:
                logger.info("数据库已就绪（等了 %d 次）", attempts)
            else:
                logger.info("数据库已就绪")
            return

        attempts += 1
        # 头几次每次都说话（多半两下就通了，日志干净），之后改成每分钟一句，
        # 既证明这个进程还活着，又不至于把日志滚没。
        if attempts <= 3 or attempts % 15 == 0:
            host = "?"
            try:
                from app.db.mysql_url import parse_database_url

                target = parse_database_url(settings.database_url)
                host = f"{target.host}:{target.port}/{target.database}（用户 {target.user}）"
            except Exception:  # noqa: BLE001 —— 只是日志里的补充信息
                pass
            logger.warning("连不上数据库 %s，%g 秒后重试：%s", host, delay, reason)
        time.sleep(delay)
        delay = min(delay * 2, DB_WAIT_MAX_SECONDS)


def main() -> int:
    settings = get_settings()
    log_path = configure_logging(settings)
    logger = logging.getLogger("xlp.run_server")

    logger.info("工作目录 %s", BACKEND_DIR)
    logger.info("日志文件 %s", log_path or "（未配置 log_dir，只写控制台）")
    logger.info(
        "前端托管 %s",
        settings.web_dir or "（未配置 web_dir，只发接口——开发时前端在 vite 的 5173）",
    )
    logger.info("监听 %s:%s", settings.host, settings.port)

    while True:
        # 每一步都先等库。首次是「开机时 MySQL 还没好」，其后是「MySQL 中途重启过」。
        wait_for_database(settings, logger)
        try:
            # `log_config=None` 是**必须的**：uvicorn 默认会 `logging.config.dictConfig`
            # 一套它自己的 handler，把上面配的那些换掉，日志于是照样进不了文件。
            uvicorn.run(
                "app.main:app",
                host=settings.host,
                port=settings.port,
                log_config=None,
                access_log=True,
            )
        except Exception:  # noqa: BLE001 —— 崩了就是崩了，退避重来
            logger.exception("服务异常退出，%g 秒后重来", CRASH_RETRY_SECONDS)
            time.sleep(CRASH_RETRY_SECONDS)
            continue
        else:
            # `uvicorn.run` **正常返回**只发生在收到关停信号时。这时候重启就成了
            # 「Ctrl-C 按不掉」，所以这里退出。
            logger.info("收到关停信号，退出")
            return 0


if __name__ == "__main__":
    sys.exit(main())
