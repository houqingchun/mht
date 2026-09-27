"""数据库备份：找 mysqldump、跑一次、留一行、发文件（CLAUDE.md §34）。

## 这个模块为什么存在

它是「安装包里的 `备份数据.bat` 总是报错」那件事的处置。**不是修那条链路上的四个原因，
是消掉它们的立足点**：

| 原来 | 现在 |
|---|---|
| `ops.ps1:532` 两行 `-replace` 串联，带引号的服务路径被按空格砍成 `C:\\Program` | `_parse_service_image_path`：`shlex.split` 一次到位（**一处定义**，不再手写字符串手术） |
| mysqldump 非 0 就 `throw "退出码 N"`，它自己 stderr 里那句话丢了 | `failure_message`：退出码与 mysqldump **自己的输出**一起进 `backup_record.message`，写在界面上 |
| 局域网用法下普通用户读不到 `.env`，点那个按钮必然失败 | 配置搬进 `system_setting`（读对所有已登录用户开放，§5），后端进程内取；**`ops.ps1` 里再没有一句读配置的代码** |
| 整条链路零日志 | 每一次尝试都落一行 `backup_record`（成功的与失败的都写）+ 一条审计 |

第 3 条只有把逻辑搬进进程内才消失，第 4 条只有有了落点才消失。所以这个模块不是
「换个地方跑 mysqldump」，它是**让失败不再静默**的那一层：那枚按钮最坏的地方不是它报错，
是「窗口一闪就关、失败原因随窗口一起没了」。

## 两条入口，一个执行体

* `run_backup` —— 手动按钮与命令行。拿不到锁时抛 409「正在备份中」（**不排队**：让用户
  盯着一个转圈的按钮等一次不属于他的备份，比让他过一会儿再点更糟）。
* `run_scheduled_backup` —— 定时那一路（`run_server.py` 的守护线程）。拿不到锁 / 今天已经
  备份过 / 管理员关掉了自动 → 返回 `None`，**不抛**：凌晨没人看着的那一路不该在日志里留
  traceback。

两者共用 `_execute`，而 `_execute` **除锁之外的任何失败都不抛**，它写成一行
`status=FAILED` 的记录然后正常返回。理由有两条，都是这个项目的既有约定：

1. **抛出去就没有落点。** 路由抛异常 → 事务不提交 → 那行 FAILED 被回滚 → 用户看到 500
   而库里什么都没有。那正是这次要修的那个 bug 原样换个地方。
2. **服务层不提交**（全库一致的形状，提交归路由的 `get_db` / 调度线程 / CLI）。
   所以这里只 `flush`，让记录拿到 id 好写审计。

**唯一一处偏离全库形状的地方：审计由服务层写。** 别处的审计都在路由里写，而这里不行
——定时那一次**没有路由**。写成「路由写手动、服务写自动」会让同一个动作的轨迹有两个
写入方。`write_audit` 的 `actor` / `request` 本来就都可选（未登录的系统事件在 §8 里本就
该有一行、界面显示 `—`），所以自动那一路的 `actor=None` 是它的正常用法。**这条偏离记在
§34 里，改它之前先读那一段。**

## 时钟

文件名里的时间戳与「今天备份过没有」都用 `now_local_naive()`，与 `created_at`
（`server_default=func.now()`，MySQL 会话时区的墙钟）**同一个基准**。用
`assessment_service.now_utc_naive()` 会让「今天」在 UTC+8 的机器上提前/推迟八小时，
去重于是会在每天早上八点前后漏掉一次。
"""

from __future__ import annotations

import hashlib
import locale
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.mysql_url import (
    DatabaseTarget,
    client_options_file,
    parse_database_url,
)
from app.db.session import SessionLocal
from app.models.account import UserAccount
from app.models.backup import (
    MESSAGE_LIMIT,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    TRIGGER_AUTO,
    TRIGGER_CLI,
    TRIGGER_MANUAL,
    BackupRecord,
)
from app.models.common import now_local_naive
from app.services.audit_service import write_audit
from app.services.settings_service import get_namespace

logger = logging.getLogger("app.backup")

NAMESPACE = "backup"
AUDIT_ACTION_BACKUP = "备份数据库"
AUDIT_ACTION_DOWNLOAD = "下载数据库备份"
RESOURCE_TYPE = "BACKUP_RECORD"

DEFAULT_DIR_NAME = "backups"
FILE_PREFIX = "xinliceping-"
FILE_SUFFIX = ".sql"
PART_SUFFIX = ".part"
DEFAULT_RECORD_LIMIT = 50
# 同一秒里跑两次（开发库的 dump 不到一秒）时不覆盖前一份：第二份变成 `…-2.sql`。
# 覆盖的后果不只是「少一份」——第一条记录的 `file_sha256` 会对不上盘上那份，
# 于是它的下载回 409，而 409 说的是「文件与记录不一致」（见 `open_record_for_download`）。
MAX_NAME_ATTEMPTS = 100

# mysqldump 卡住时不能永远占着锁——那会让**之后每一次备份都静默地什么都不做**，
# 正是这个功能要消掉的那一类失败。超时按「一个学校的库 + 可能很慢的盘」定得宽松些。
DUMP_TIMEOUT_SECONDS = 600
VERSION_PROBE_TIMEOUT_SECONDS = 20
SCHEDULER_INTERVAL_SECONDS = 3600
SCHEDULER_RETRY_SECONDS = 300
# 线程在 `run_server.py` 的 `wait_for_database` **之前**起来，所以开机那一刻 MySQL 常常
# 还没好。先等一会儿：比在日志里留一段「开机就失败」的 traceback 更接近「一次成功的
# 启动时备份」。
SCHEDULER_FIRST_DELAY_SECONDS = 60

# 手动那一路与定时那一路共用。**模块级**：进程内只有一份，调度线程与请求线程看到的是它。
_RUN_LOCK = threading.Lock()

# `find_mysqldump()` 要真跑 5 个候选的 `--version`，而调度线程每小时问一次。
# 只缓存**找到**的那一个；没找到不缓存，下一次重新找（那台机器可能刚装好）。
_FOUND_DUMP: str | None = None
_FOUND_LOCK = threading.Lock()

# 审计 `detail` 里的中文。**与 `labels.ts` 的 `BACKUP_TRIGGER_LABELS` 是同一批词**，
# 由 `test_backup.py::test_the_trigger_labels_match_the_frontend` 逐字比对——审计页的
# 搜索匹配 `action`（这里是「备份数据库」），但 `detail` 是写给读轨迹的人看的，写成
# `MANUAL` 就白写了（§24 那条「留空与『没问过』分不开」的同族：**码与话是两件事**）。
TRIGGER_LABELS = {
    TRIGGER_AUTO: "自动",
    TRIGGER_MANUAL: "手动",
    TRIGGER_CLI: "命令行",
}


# --------------------------------------------------------------------------- 找 mysqldump


def _parse_service_image_path(text: str) -> Path | None:
    """从 Windows 服务的 `ImagePath` 里取出 mysqld 的路径。

    值形如（MySQL Installer 装出来的默认就带引号）：

        "C:\\Program Files\\MySQL\\MySQL Server 8.0\\bin\\mysqld.exe" --defaults-file="…" MySQL80

    **这里就是 `ops.ps1:532` 那个 bug 的正面写法。** 那两行 PowerShell 是串联的
    `-replace`：第一行把引号剥掉（对的），第二行紧接着按第一个空格切（错的）——
    带引号的路径于是变成 `C:\\Program`，而它**看起来像一条找到了的路径**，所以最准的那条
    定位路径一直是坏的、全靠通配兜底。`shlex.split(..., posix=False)` 一次做完
    「按引号分词」这件事：引号内的空格不再是分隔符。

    `posix=False` 是必须的：POSIX 模式会把 `C:\\Program Files` 里的反斜杠当转义符吃掉。
    取 `parts[0]` 之后 `.strip('"')` 剥掉那一对引号——**只有首尾**，路径中间不会出现引号。

    解析不出来（空串、只有开关）时返回 `None`，不猜。
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        parts = shlex.split(text, posix=False)
    except ValueError:
        return None
    if not parts:
        return None
    first = parts[0].strip().strip('"')
    return Path(first) if first else None


def _windows_service_dump_paths() -> list[Path]:
    """从注册表里找 MySQL 服务，推出它同目录下的 `mysqldump.exe`。

    照 `ops.ps1:520-553` 的**意图**（从服务的 `PathName` 同级推），换一层实现：
    Python 的 `winreg` 比 `Get-CimInstance Win32_Service` 少一次 WMI 往返，而且这里
    只需要一个字符串。

    只当**提示**用：推出来的路径仍要过 `_probe` 真跑一次 `--version` 才认。所以猜错
    （服务名不带 mysql、ImagePath 形状古怪）的代价只是白试一次，不是找错东西。
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # 理论上不会；不猜。
        return []

    found: list[Path] = []
    try:
        services = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services"
        )
    except OSError:
        return []
    with services:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(services, index)
            except OSError:  # 枚举到头
                break
            index += 1
            if "mysql" not in name.lower():
                continue
            try:
                with winreg.OpenKey(services, name) as key:
                    image, _ = winreg.QueryValueEx(key, "ImagePath")
            except OSError:
                continue
            server = _parse_service_image_path(str(image))
            if server is not None:
                found.append(server.parent / "mysqldump.exe")
    return found


def _mysqldump_candidates() -> list[Path]:
    """按「最可能对」的次序排的候选表。去重但保持次序。"""
    candidates: list[Path] = []

    on_path = shutil.which("mysqldump")
    if on_path:
        candidates.append(Path(on_path))

    if sys.platform == "win32":
        # `MySQL Server *` 倒序：装了 8.0 与 5.7 两台时先用新的。
        for root in (
            Path(r"C:\Program Files\MySQL"),
            Path(r"C:\Program Files (x86)\MySQL"),
        ):
            for directory in sorted(root.glob("MySQL Server *"), reverse=True):
                candidates.append(directory / "bin" / "mysqldump.exe")
        candidates.extend(_windows_service_dump_paths())
    else:
        # 开发机上的真实差异：`mysqldump` 不在 PATH 里，但装在 `/usr/local/mysql/bin`。
        # 所以这一支不是「只有目标机才执行的代码」——`make test` 与手工回路都走它。
        candidates.extend(
            [
                Path("/usr/local/mysql/bin/mysqldump"),
                Path("/opt/homebrew/bin/mysqldump"),
                Path("/opt/homebrew/opt/mysql-client/bin/mysqldump"),
                Path("/usr/bin/mysqldump"),
            ]
        )

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _probe(binary: Path) -> bool:
    """**真跑一次** `--version`，只看退出码。

    照 `install.ps1` 的 `Resolve-BasePython` 那条：`Test-Path`（或这里的
    `Path.exists()`）只说「有这么个文件」——应用商店的别名、半截安装、另一个软件顺手
    带进来的同名 exe 都会让它为真，而真跑一次不会。
    """
    if not binary.exists():
        return False
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            timeout=VERSION_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def find_mysqldump() -> Path:
    """找到能用的 mysqldump。找不到时抛一句**写得出怎么办**的中文。

    与 §18 那条「空态是一句关于数据的话」同源：操作员此刻要知道的是「装什么、装在哪」，
    不是 `FileNotFoundError`。
    """
    global _FOUND_DUMP

    with _FOUND_LOCK:
        if _FOUND_DUMP and Path(_FOUND_DUMP).exists():
            return Path(_FOUND_DUMP)

    tried: list[str] = []
    for candidate in _mysqldump_candidates():
        tried.append(str(candidate))
        if _probe(candidate):
            with _FOUND_LOCK:
                _FOUND_DUMP = str(candidate)
            return candidate

    hint = (
        "它通常跟着 MySQL Server 一起装在 `bin` 目录下"
        "（Windows：`C:\\Program Files\\MySQL\\MySQL Server 8.0\\bin\\mysqldump.exe`）"
    )
    raise AppError(
        "BACKUP_DUMP_MISSING",
        "找不到可用的 mysqldump，无法备份。" + hint + "。"
        "装好之后不用重启服务，再点一次「立即备份」即可。"
        "（已试过：" + "、".join(tried) + "）",
        500,
    )


# --------------------------------------------------------------------------- 目录与配置


def _install_root() -> Path:
    """仓库根 = 安装目录。

    `Path(__file__).resolve().parents[3]`：services → app → backend → 仓库根，与
    `db/seed.py` 找 `data/mht_scale.json`、`export_service.export_directory` 找
    `var/exports` 是同一套算法。打包器把 `backend/` 整个拷进安装目录，所以
    `<仓库根>/backups` 在目标机上就是 `<安装目录>\\backups`——与 `ops.ps1` 原来的
    默认值同址，没配过的实例行为一字不变。
    """
    return Path(__file__).resolve().parents[3]


def _resolve_dir(value: str) -> Path:
    """把配置里的路径展开成绝对路径。空串 = 默认目录。

    相对路径按**安装目录**解析（不是进程的 CWD）：`run_server.py` 会 `chdir` 到仓库根，
    但计划任务配错工作目录时它不会，而这个模块不该依赖那件事。
    """
    text = (value or "").strip()
    if not text:
        return _install_root() / DEFAULT_DIR_NAME
    path = Path(text)
    return path if path.is_absolute() else _install_root() / path


def backup_config(db: Session) -> dict:
    return get_namespace(db, NAMESPACE)


def primary_directory(db: Session) -> Path:
    return _resolve_dir(backup_config(db).get("dir", ""))


def secondary_directory(db: Session) -> Path | None:
    text = (backup_config(db).get("secondary_dir") or "").strip()
    return _resolve_dir(text) if text else None


def directory_summary(db: Session) -> dict:
    """给界面用的**实际落点**（§9「口径要写进界面」）。

    一个写着「留空即默认」的输入框答不出「那我这台机器上到底落在哪」，而这正是备份
    这个功能最要紧的一句话——用户配第二路径的目的就是「知道东西在哪、拿得走」。
    """
    config = backup_config(db)
    primary = primary_directory(db)
    secondary = secondary_directory(db)
    return {
        "dir": str(primary),
        "dir_is_default": not (config.get("dir") or "").strip(),
        "secondary_dir": str(secondary) if secondary else None,
        "keep_days": int(config.get("keep_days") or 0),
        "auto_enabled": bool(config.get("auto_enabled", True)),
    }


# --------------------------------------------------------------------------- 记录


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_native(raw: bytes) -> str:
    """解外部命令的输出。

    原生工具重定向到管道时写的是**控制台代码页**（中文 Windows = cp936），只有直接挂在
    控制台上的 `WriteConsoleW` 那条路才是宽字符（§18 记着这条）。所以先按 UTF-8 严格解
    ——纯 ASCII 与真 UTF-8（库名是中文时 mysqldump 有时会给 UTF-8）都能过——失败了再退到
    本机代码页，最后 `latin-1` 兜底（它不会失败）。
    """
    for encoding in ("utf-8", locale.getpreferredencoding(False), "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def failure_message(exc: BaseException) -> str:
    """把一次失败写成人读得懂的一句。

    **`AppError` 的 `message` 本来就是写给用户看的**（§2），原样用。同理，
    `_ReadableFailure` 那一族（`_DumpFailed`）拼出来的也是一句完整的中文，原样用。
    其余异常带类型名：一句光秃秃的 `Permission denied` 不如
    `PermissionError: Permission denied` 好查——**前缀是给认不出的异常用的**，
    它帮的是排查，而不该出现在一句已经写好的话前面。

    **不带下划线，因为它有一个模块外的读者**：`app/db/backup.py` 那条命令行在
    `BACKUP_RUNNING`（另一次正在跑）时用它把话说给操作员听——而那一次**没有**属于它的
    记录行可写，所以那句话只能当场打出来。
    """
    if isinstance(exc, AppError):
        return exc.message
    if isinstance(exc, _ReadableFailure):
        return str(exc).strip()
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def operator_names(db: Session, records: list[BackupRecord]) -> dict[int, str]:
    """一次把这一批记录的 `operator_id` 换成姓名。

    **不是 `record.operator.display_name`**：模型上刻意没有那个 relationship，逐行访问
    就等于一次一条 SELECT（历史表 50 行 = 51 条查询）。这里的形状照 `api/v1/audit.py`
    那一处（§8 那条「审计行要能回答『谁做的』，不只是『哪个角色做的』」），一次查完。

    自动那一次没有操作人（`operator_id` 为空）——界面显示 `—`，这里返回的字典里就是没有
    它。这不是「查不到」，是「那一次本来就没有人」。
    """
    ids = {record.operator_id for record in records if record.operator_id}
    if not ids:
        return {}
    rows = db.execute(
        select(UserAccount.id, UserAccount.display_name).where(UserAccount.id.in_(ids))
    ).all()
    return {row[0]: row[1] for row in rows}


def record_payload(
    record: BackupRecord,
    *,
    directory: Path | None,
    operator_name: str | None = None,
) -> dict:
    """一行的接口形状。下载那一列读 `file_exists`，不由前端自己去猜。"""
    exists = False
    if directory is not None and record.file_name:
        try:
            exists = (directory / record.file_name).is_file()
        except OSError:
            exists = False
    return {
        "id": record.id,
        "trigger": record.trigger,
        "status": record.status,
        "file_name": record.file_name,
        "file_size": record.file_size,
        "message": record.message,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "operator_name": operator_name,
        "file_exists": exists,
    }


def record_payloads(
    db: Session, records: list[BackupRecord], *, directory: Path | None
) -> list[dict]:
    """一整批（历史表那一次调用）。名字一次查完，见 `operator_names`。"""
    names = operator_names(db, records)
    return [
        record_payload(
            record, directory=directory, operator_name=names.get(record.operator_id or 0)
        )
        for record in records
    ]


def list_records(db: Session, *, limit: int = DEFAULT_RECORD_LIMIT) -> list[BackupRecord]:
    """最近几次尝试，新的在前。

    **按 `id` 倒序，不按 `created_at`**：那一列是数据库墙钟、秒级（§20 那条
    `now_utc_naive()` 截断的另一面），同一秒内的两次尝试按时间排会给出两个次序。
    与 `care_events` 的时间线同一条（§28）。
    """
    return list(
        db.scalars(
            select(BackupRecord).order_by(BackupRecord.id.desc()).limit(limit)
        ).all()
    )


def last_record(db: Session, *, status: str) -> BackupRecord | None:
    return db.scalars(
        select(BackupRecord)
        .where(BackupRecord.status == status)
        .order_by(BackupRecord.id.desc())
        .limit(1)
    ).first()


# --------------------------------------------------------------------------- 去重与清理


def succeeded_today(db: Session, directory: Path) -> BackupRecord | None:
    """今天是不是已经成功备份过一次，**而且那份文件还在盘上**。

    读**表**而不是读目录：这样「手动点过一次」也抑制当天后面的自动那一次——同一天两份
    1MB 的 dump 没有意义，而读目录答不出「这一份是谁什么时候搞的」。

    `created_at`（库墙钟）与 `now_local_naive().date()` 同一个基准，理由见模块 docstring。
    `_execute` 每次成功都写这一行，所以「有记录」与「有文件」应当同时成立；文件被判丢的
    那一支（有人手工删了、盘换了）只是让去重**不生效**，于是再备份一次——方向是安全的那
    一侧（宁可多一份，不可少一份）。
    """
    start = datetime.combine(now_local_naive().date(), datetime.min.time())
    rows = db.scalars(
        select(BackupRecord)
        .where(
            BackupRecord.status == STATUS_SUCCEEDED,
            BackupRecord.created_at >= start,
        )
        .order_by(BackupRecord.id.desc())
    ).all()
    for row in rows:
        if not row.file_name:
            continue
        if (directory / row.file_name).is_file():
            return row
    return None


def _prune(directory: Path, keep_days: int) -> list[str]:
    """删掉过期的**我们自己命名的**备份，返回删掉的文件名。

    三条边界，每一条都是「一个破坏性动作的默认值不能是破坏」：

    * `keep_days <= 0` = **不清理**（照 `export.max_rows: 0 = 不限` 那条先例，§29）。
      0 在这里绝不能被读成「删掉所有比 0 天旧的」，那等于每次备份都清空。
    * **只 glob `xinliceping-*.sql`**：运维自己放进这个目录的东西（手工导出的
      `dump.sql`、一份 `README.txt`）一条都不碰。这条也是「第二路径可以指向一个
      已经有别的东西的目录」的前提。
    * 单个文件删不掉（占用中、权限）只跳过，不让整次备份失败——它是收尾动作。
    """
    if keep_days <= 0 or not directory.is_dir():
        return []
    cutoff = now_local_naive() - timedelta(days=keep_days)
    removed: list[str] = []
    for path in sorted(directory.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}")):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue
    return removed


# --------------------------------------------------------------------------- 跑一次


def _available_path(directory: Path, stamp: str) -> Path:
    base = f"{FILE_PREFIX}{stamp}"
    for attempt in range(MAX_NAME_ATTEMPTS):
        suffix = "" if attempt == 0 else f"-{attempt + 1}"
        candidate = directory / f"{base}{suffix}{FILE_SUFFIX}"
        if not candidate.exists():
            return candidate
    raise AppError("BACKUP_NAME_CONFLICT", f"同名的备份文件太多了：{base}{FILE_SUFFIX}", 500)


def _run_dump(binary: Path, client_file: str, target: DatabaseTarget, part: Path) -> None:
    """跑 mysqldump，写进 `<目标>.part`。

    参数表里每一条都有理由：

    * `--defaults-extra-file=…` —— **口令绝不进命令行**。Windows 上任何账号都能通过 WMI
      的 `Win32_Process` 读到别的进程的命令行（§18 已经为 `install.ps1` 的提权写过同一条）。
      那个临时文件由**调用方**建、由调用方删（`_execute` 的 `finally`）：建在这里的话，
      它的生命周期就短不过这一次 `subprocess.run`，而调用方无从知道它存在——**一个只在
      这一句里存在的 `finally`，等于每次都漏一个文件**。
    * `--single-transaction` —— InnoDB 下不锁表，备份期间学生照常答题。
    * `--no-tablespaces` —— 8.0 起默认会去读表空间信息，而那个操作要 PROCESS 权限。
      学校那个库账号通常没有，于是 dump 以一句「Access denied; you need (at least one of)
      the PROCESS privilege(s)」失败，而它与「口令错了」长得像。这一条是纯粹的**减少
      失败面**。
    * `--result-file` —— 让 mysqldump 自己写文件（Windows 上还能避免 `\\n` 被转成
      `\\r\\n`），比接 stdout 再落盘少一层缓冲。

    路径一律转成**正斜杠**（`as_posix()`）：`my_getopt` 会处理选项值里的转义序列，一个
    `C:\\xinliceping\\backups` 里的 `\\b` 有可能被它当成退格。Windows 的文件 API 认正斜杠，
    所以这是一条「不要让它有机会猜」的写法，代价为零。
    """
    command = [
        str(binary),
        f"--defaults-extra-file={Path(client_file).as_posix()}",
        "--single-transaction",
        "--default-character-set=utf8mb4",
        "--no-tablespaces",
        "--routines",
        "--events",
        f"--result-file={part.as_posix()}",
        target.database,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=DUMP_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        # 「根本没能跑起来」与「跑了但非 0 退出」是两件事，而它们都会落进
        # `record.message` 那一列——那一列在管理页上是「说明」，给操作员看。
        # 不接住的话落进去的是 `FileNotFoundError: [Errno 2] No such file or
        # directory: '/usr/local/mysql/bin/mysqldump'`（实测过），与 §2 那条
        # 「服务端答了话，那句话本来就是写给用户看的」正相反。
        # 这条路上最真实的场景是**服务跑着的时候 MySQL 被卸了或升级了**：候选表
        # 是启动那一刻探的（`_FOUND_DUMP` 有缓存），而二进制可以不在了。
        raise _ReadableFailure(
            f"mysqldump 跑不起来（{binary}）：{exc.strerror or exc}。"
            "它可能被删掉或移动了；确认 MySQL Server 还在之后不用重启服务，再点一次即可。"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        # 超时同样是「这句话得说给操作员听」：库里有一张特别大的表、或者备份目录在
        # 一个断掉的网盘上时，mysqldump 会一直卡着。
        raise _ReadableFailure(
            f"mysqldump 超过 {DUMP_TIMEOUT_SECONDS} 秒没有结束，已中止："
            f"{target.database}。请确认备份目录可写、磁盘没满，然后重试。"
        ) from exc
    if result.returncode != 0:
        raise _DumpFailed(result.returncode, _encode_native(result.stderr or result.stdout))


def _client_file(target: DatabaseTarget) -> str:
    """写临时 `--defaults-extra-file`，返回路径。**调用方负责删。**

    权限 0600 先于写入（`mkstemp` 建出来就是 0600，与 `mysql_url.main()` 那句
    `touch(mode=0o600)` 同一个意思）。目录用 `tempfile` 的默认值：目标机上那是
    `%TEMP%`，比放在安装目录里少一份「忘了删」的可能。
    """
    handle, path = tempfile.mkstemp(prefix="xlp-mysql-", suffix=".cnf")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(client_options_file(target))
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    return path


class _ReadableFailure(Exception):
    """**已经是一句给用户看的话**的异常。`failure_message` 原样用它。

    存在的唯一理由是那一个分支：`failure_message` 给认不出的异常加类型名前缀（好查），
    而这句话本身是一句完整的中文，前缀一加就变成
    `record.message` 里的 `_DumpFailed: mysqldump 退出码 2：…`——一个内部类名漏进了
    界面上「说明」那一列，而那一列正是给操作员看的（§2：那句话本来就是写给用户看的）。

    做成一个标记基类而不是在 `failure_message` 里 `isinstance(exc, _DumpFailed)`：
    后者要求那个函数认得每一个「自己会说话」的异常，于是下一个这样的类型要回去改它。
    **继承 `AppError` 也不行**（它是 `@dataclass`，`__init__` 的签名是三个字段，
    `super().__init__(text)` 会当场 `TypeError`），而这里要的只是「别加前缀」这一件事。
    """


class _DumpFailed(_ReadableFailure):
    """mysqldump 非 0 退出。

    **保留 mysqldump 的输出正是这次修复的核心**——原来那句 `throw "退出码 N"` 把它扔了，
    于是操作员唯一能提供的东西（那一屏文字）从来没有离开过那个一闪就关的窗口。
    """

    def __init__(self, returncode: int, output: str):
        self.returncode = returncode
        self.output = output.strip()
        # **那句话必须挂进异常自己的文本里。** `failure_message` 对非 `AppError` 的异常
        # 走的是 `str(exc)`，所以只把 output 存成属性等于没有读者 —— 上一版就是这样：
        # 异常里带着 mysqldump 的原文，而落进 `record.message` 的只有「退出码 2」。
        text = f"mysqldump 退出码 {returncode}"
        if self.output:
            text = f"{text}：{self.output}"
        super().__init__(text)


def _execute(
    db: Session,
    *,
    trigger: str,
    operator: UserAccount | None,
    dump_binary: str | None = None,
) -> BackupRecord:
    """跑一次备份并落一行记录。**除极端情况外不抛**（见模块 docstring）。"""
    config = backup_config(db)
    primary = _resolve_dir(config.get("dir", ""))
    secondary_text = (config.get("secondary_dir") or "").strip()
    secondary = _resolve_dir(secondary_text) if secondary_text else None
    keep_days = int(config.get("keep_days") or 0)

    record = BackupRecord(
        trigger=trigger,
        status=STATUS_FAILED,
        operator_id=operator.id if operator else None,
    )
    warnings: list[str] = []

    try:
        binary = Path(dump_binary) if dump_binary else find_mysqldump()
        target = parse_database_url(get_settings().database_url)
        primary.mkdir(parents=True, exist_ok=True)

        final = _available_path(primary, now_local_naive().strftime("%Y%m%d-%H%M%S"))
        part = Path(f"{final}{PART_SUFFIX}")
        try:
            client_file = _client_file(target)
            try:
                _run_dump(binary, client_file, target, part)
            finally:
                Path(client_file).unlink(missing_ok=True)
            # `os.replace` 是原子的，所以「文件在 ⇒ 备份成功」这条判据成立；
            # 去重、列表里的「文件还在不在」、下载全都靠它。
            os.replace(part, final)
        except Exception:
            part.unlink(missing_ok=True)
            raise

        record.file_name = final.name
        record.file_size = final.stat().st_size
        record.file_sha256 = _sha256(final)
        record.status = STATUS_SUCCEEDED

        if secondary is not None:
            warnings.extend(_copy_secondary(final, secondary))
            # 第二路径与主目录**各清各的**：它们通常是两块盘，保留期却同一个数，
            # 所以两边都要扫一遍。只扫主目录的话，第二路径会单调长大（它是一个网盘
            # 或者另一块盘，正是最容易没人看的地方）。
            pruned_secondary = _prune(secondary, keep_days)
            if pruned_secondary:
                warnings.append(f"第二路径已清理 {len(pruned_secondary)} 个过期备份")
        pruned = _prune(primary, keep_days)
        if pruned:
            warnings.append(f"已清理 {len(pruned)} 个过期备份")
        record.message = ("；".join(warnings))[:MESSAGE_LIMIT] or None
    except Exception as exc:  # noqa: BLE001 —— 见模块 docstring：失败要留痕，不要抛
        record.status = STATUS_FAILED
        record.file_name = None
        record.file_size = None
        record.file_sha256 = None
        record.message = failure_message(exc)[:MESSAGE_LIMIT]
        logger.warning("备份失败（%s）：%s", TRIGGER_LABELS.get(trigger, trigger), record.message)

    db.add(record)
    # 先 flush 拿到 id：审计的 `resource_id` 要指着这一行，否则轨迹答不出「哪一次」。
    db.flush()
    write_audit(
        db,
        action=AUDIT_ACTION_BACKUP,
        resource_type=RESOURCE_TYPE,
        resource_id=str(record.id),
        actor=operator,
        purpose=None,
        detail=_backup_detail(record),
    )
    db.flush()
    if record.status == STATUS_SUCCEEDED:
        logger.info("备份完成（%s）：%s", TRIGGER_LABELS.get(trigger, trigger), record.file_name)
    return record


def _copy_secondary(final: Path, secondary: Path) -> list[str]:
    """往第二路径再落一份。**拷失败不算备份失败**，它是 `message` 里的一句警告。

    主备份已经成功、已经在盘上，那是用户要的东西；网盘断了/盘满了不该把一次成功的备份
    记成失败——那会让界面上写着「失败」而文件其实好好地躺着（反过来也会：把警告当失败
    记之后，人不再信任状态列）。
    """
    try:
        secondary.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final, secondary / final.name)
        return []
    except OSError as exc:
        return [f"第二路径复制失败（{exc}），主备份不受影响"]


def _backup_detail(record: BackupRecord) -> str:
    """审计的 `detail`。

    失败时**把原因抄一遍**（`message` 那一列已经写着了）：轨迹要能独立回答「那天为什么
    没备份成」，而不是让人再去 join `backup_record`——那一行是可能被清理的，审计是留底的。
    成功时只记文件名与大小。
    """
    trigger = TRIGGER_LABELS.get(record.trigger, record.trigger)
    if record.status == STATUS_FAILED:
        return f"{trigger}备份失败：{record.message or '未记录原因'}"
    parts = [f"{trigger}备份成功：{record.file_name}（{record.file_size} 字节）"]
    if record.message:
        parts.append(record.message)
    return "；".join(parts)


def run_backup(
    db: Session,
    *,
    trigger: str = TRIGGER_MANUAL,
    operator: UserAccount | None = None,
    dump_binary: str | None = None,
) -> BackupRecord:
    """手动 / 命令行那一路。**拿不到锁时抛 409**，其余失败都落在返回的记录上。

    `dump_binary` 只有一个用途：测试里把 mysqldump 换成一个可控的命令（成功或必失败），
    不必在用例里真跑一次 dump。生产的两条调用路径都不传。

    **调用方负责提交**（服务层不提交，全库一致）。
    """
    if not _RUN_LOCK.acquire(blocking=False):
        raise AppError(
            "BACKUP_RUNNING", "正在备份中，请等这一次结束再点。", 409
        )
    try:
        return _execute(db, trigger=trigger, operator=operator, dump_binary=dump_binary)
    finally:
        _RUN_LOCK.release()


def run_scheduled_backup(
    db: Session, *, dump_binary: str | None = None
) -> BackupRecord | None:
    """定时那一路。**一条都不抛**：跳过时返回 `None`。

    三种跳过，各自有各自的理由，而且都**不是**失败：

    * 管理员关掉了自动备份 —— 这是他的选择，不是故障；
    * 另一次备份正在跑 —— 手动刚点过或上一次还没结束；
    * 今天已经成功备份过（且文件还在）—— 照 `succeeded_today`。

    关掉自动之后线程不会退出（它只是这一轮什么都不做）：管理员在界面上重新打开时，
    下一次检查就生效，不必重启服务。**这是 `settings_service` 里那条注释的意思，
    措辞按这里为准。**
    """
    config = backup_config(db)
    if not bool(config.get("auto_enabled", True)):
        logger.debug("自动备份已关闭，跳过")
        return None

    if not _RUN_LOCK.acquire(blocking=False):
        logger.info("另一次备份正在进行，本次自动备份跳过")
        return None
    try:
        existing = succeeded_today(db, _resolve_dir(config.get("dir", "")))
        if existing is not None:
            logger.info("今天已经备份过（%s），跳过", existing.file_name)
            return None
        return _execute(db, trigger=TRIGGER_AUTO, operator=None, dump_binary=dump_binary)
    finally:
        _RUN_LOCK.release()


# --------------------------------------------------------------------------- 下载


def open_record_for_download(
    db: Session,
    record_id: int,
    *,
    operator: UserAccount,
    request=None,
) -> tuple[BackupRecord, Path, bytes]:
    """取出一次备份的文件。审计**在返回之前**写（§8）。

    **这是 CLAUDE.md §4 的一次有意例外，动它之前先读 §34**：管理员按 §4 没有
    `STUDENT_PSYCH_DETAIL`（是 `NONE`）——他看不到一名学生的档案，却能下载一份含全部
    学生心理数据、答卷与账号口令哈希的整库 dump。用户已裁决要这个下载按钮，所以处置是
    **把例外写明并给它加护栏**，不是假装它不存在：

    * 守卫是 `ORG_ACCOUNT: {MANAGE}`（路由上的依赖），唯一拿得到它的角色就是管理员本身；
    * **每次下载写审计**，`detail` 里明写「整库原始数据，不可能遮蔽」——照 §8 那条
      「导出审计必须记录遮蔽模式」：一份备份**没有**遮蔽这一档，这句话必须出现在轨迹里，
      而不是靠读轨迹的人自己去想。

    三条判据与 `download_export_job`（§29）逐条对齐：

    * 记录不存在 / 没成功过 → **404**（「不属于你」与「不存在」在响应上必须不可分辨，
      不过这里只可能不存在——这个能力只有管理员有）；
    * 文件不在了 → **410**，并指出「备份目录可能已经被改过」这一条最可能的原因；
    * 摘要对不上 → **409 且不发文件**。宁可发不出去，也不要发一份说不清是不是当初那一份
      的文件——这句话是 §29 逐字定下来的。
    """
    record = db.get(BackupRecord, record_id)
    if record is None or record.status != STATUS_SUCCEEDED or not record.file_name:
        raise AppError("BACKUP_NOT_FOUND", "备份记录不存在，或这一次没有留下文件", 404)

    directory = primary_directory(db).resolve()
    # `resolve()` 之后再判包含关系：`file_name` 是我们自己写的（`_available_path` 只拼
    # 一个文件名），但它在库里、而库可改；`../` 或者一个绝对路径进来时必须被挡住。
    path = (directory / record.file_name).resolve()
    if not path.is_relative_to(directory):
        raise AppError("BACKUP_PATH_REJECTED", "备份文件路径不合法", 400)
    if not path.is_file():
        raise AppError(
            "BACKUP_FILE_MISSING",
            "备份文件已不在服务器上（备份目录可能已经被改过），请重新备份",
            410,
        )

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if record.file_sha256 and digest != record.file_sha256:
        raise AppError(
            "BACKUP_FILE_CHANGED",
            "备份文件与记录不一致，已停止下载。请在服务端核对这份文件，或重新备份",
            409,
        )

    write_audit(
        db,
        action=AUDIT_ACTION_DOWNLOAD,
        resource_type=RESOURCE_TYPE,
        resource_id=str(record.id),
        actor=operator,
        request=request,
        detail=f"{record.file_name}；整库原始数据，不可能遮蔽",
    )
    return record, path, data


# --------------------------------------------------------------------------- 定时


def _scheduler_loop() -> None:
    """后台线程体：启动后跑一次，之后每小时检查一次。

    每一次都是**自己开一个会话、自己提交、自己关**——它不属于任何请求。异常一律吞进
    日志然后早一点重试：这一层绝不能让主循环受影响（它在同一个进程里，而主循环要保证
    的是服务在跑）。
    """
    time.sleep(SCHEDULER_FIRST_DELAY_SECONDS)
    while True:
        delay = SCHEDULER_INTERVAL_SECONDS
        try:
            with SessionLocal() as db:
                record = run_scheduled_backup(db)
                db.commit()
                if record is not None:
                    logger.info("自动备份结束：%s", record.status)
        except Exception:  # noqa: BLE001 —— 见 docstring：吞进日志，稍后重试
            logger.exception("自动备份这一轮出错，%s 秒后重试", SCHEDULER_RETRY_SECONDS)
            delay = SCHEDULER_RETRY_SECONDS
        time.sleep(delay)


def start_backup_scheduler() -> threading.Thread:
    """起守护线程。**必须在 `run_server.py` 的 `while True:` 之前调用一次**。

    写进循环体里的话，每次崩溃重启都会多起一个线程——而 `run_server.py` 的重试循环正是
    这个项目的常态（数据库还没起来、进程崩了）。守护线程让进程退出时它跟着走，也不必
    在关停路径上多写一段 join。

    **刻意不放进 `app/main.py` 的 lifespan**：pytest 每一个 `TestClient` 用例都会走一次
    lifespan，那会让几百条后端用例各跑一次 `mysqldump`。`run_server.py` 是**生产专用**
    启动器（计划任务跑的就是它），pytest 与 `make backend` 都不经过它——「启动时备份一次」
    放在这里才落在空位置上。

    **也不做平台判断**：非 Windows 上照跑。一个跑得起来却每天失败的地方不是 bug，是事实；
    而它让这个功能在开发机上可测（开发机的 `mysqldump` 在 `/usr/local/mysql/bin`）。
    """
    thread = threading.Thread(
        target=_scheduler_loop, name="backup-scheduler", daemon=True
    )
    thread.start()
    return thread
