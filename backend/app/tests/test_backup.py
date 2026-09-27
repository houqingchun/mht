"""数据库备份（CLAUDE.md §34）。

**真 MySQL**（§20）：`conftest.py` 的 session 级夹具会 `DROP DATABASE` 测试库、跑
`alembic upgrade head` 建表。这里没有任何 sqlite 的写法，也不该有。

三件事先说清楚，它们各自都是一次会踩的坑：

1. **桩（`_DumpStub`）能证明的东西与不能证明的东西。** 它把 `backup_service.subprocess.run`
   换掉，于是可以断「命令行里没有口令」「临时 cnf 跑完就被删了」「`.part` 没留下来」
   「mysqldump 自己的话进了 `message`」。但它把 `_run_dump` 里**唯一真正与外界打交道
   的那一句**换掉了——所以另有一条不桩的用例（`dump_binary=sys.executable`，那个命令
   带 `--defaults-extra-file` 必然失败）把「真起一个进程、真拿到它的退出码与 stderr」
   补回来。`test_everything_the_stub_replaces_is_covered_by_a_real_run` 把这个关系写成
   一条判据，而不是一句注释。

2. **`db_session` 是 `autoflush=False`。** `settings_service.update_namespace` 只
   `db.add(...)`，既不 flush 也不提交。所以「配好备份目录」之后**必须显式
   `db_session.flush()`**——少了它，`backup_config()` 读到的是空配置，`dir` 回退成
   `<仓库根>/backups`，用例会往**仓库里**写一份真的 dump 而断言说「没写」。`configured`
   夹具里那一行 `flush()` 就是为这件事存在的。

3. **e2e 不写这一块，是有意的取舍**（§24 / §32 同源，两条独立的理由各自足够）：
   ①手动备份会在共享的开发库里落一个真文件 + 一行记录 + 一条审计，跑一次 e2e 就多一份
   1MB 的 dump，而它**只增不减**；②`playwright.config.ts` 是 `fullyParallel`，写
   `resource_type=BACKUP_RECORD` 的审计会与审计页那条「只显示最新 20 行」的用例抢同一条
   时间线。所以这一块由**后端**钉住，「视图有没有把按钮渲染出来」是这条取舍让掉的。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.account import UserAccount
from app.models.audit import AuditLog
from app.models.backup import (
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    TRIGGER_AUTO,
    TRIGGER_CLI,
    TRIGGER_MANUAL,
)
from app.services import backup_service, settings_service
from app.services.backup_service import (
    AppError,
    _DumpFailed,
    _encode_native,
    failure_message,
)
from app.tests.conftest import auth_headers


# --------------------------------------------------------------------------- 桩


class _DumpStub:
    """把 `subprocess.run` 换成一个「立刻就成功」的 mysqldump。

    `returncode == 0` 时按命令里的 `--result-file=` 写一份内容（默认那段 SQL 注释），
    否则一个文件都不写、把 `stderr` 原样交回去 —— 那正是真 mysqldump 失败时的形状。

    `cnf_text` 是**调用那一刻**从 `--defaults-extra-file` 读下来的内容。它在调用返回后
    就被删了，所以只有站在这里才看得见 —— 而「口令走的是这个文件」这件事必须看得见：
    只断「命令行里没有口令」的话，一个**什么都没传**的实现照样是绿的。
    """

    def __init__(self, *, returncode: int = 0, stderr: bytes = b"", content: str | None = None):
        self.returncode = returncode
        self.stderr = stderr
        self.content = content if content is not None else "-- mysqldump 的产物\n"
        self.calls: list[list[str]] = []
        self.cnf_text: str | None = None

    def __call__(self, command, **kwargs):  # noqa: ANN001, ANN003 —— 与 subprocess.run 同形
        self.calls.append([str(part) for part in command])
        for part in self.calls[-1]:
            if part.startswith("--defaults-extra-file="):
                path = Path(part.split("=", 1)[1])
                self.cnf_text = path.read_text(encoding="utf-8") if path.exists() else None
        if self.returncode == 0:
            for part in self.calls[-1]:
                if part.startswith("--result-file="):
                    Path(part.split("=", 1)[1]).write_text(self.content, encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode, b"", self.stderr)

    @property
    def command(self) -> list[str]:
        """最后一次调用的命令行。**先断言它被调用过**——「mysqldump 从来没被跑起来」
        会让下面每一条 `in` 断言都变成空转。"""
        assert self.calls, "mysqldump 一次都没有被调用"
        return self.calls[-1]


@pytest.fixture()
def dump(monkeypatch: pytest.MonkeyPatch) -> _DumpStub:
    stub = _DumpStub()
    monkeypatch.setattr(backup_service.subprocess, "run", stub)
    return stub


@pytest.fixture()
def admin_account(db_session) -> UserAccount:
    account = db_session.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    assert account is not None, "种子数据里没有 admin 账号"
    return account


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    """备份目录一律指到 `tmp_path`。

    用默认目录（`<仓库根>/backups`）跑用例等于往仓库里写文件，而且 `_prune` 会把
    `keep_days` 之前的那些删掉 —— 一个测试把开发机上的备份删了。
    """
    directory = tmp_path / "store"
    directory.mkdir()
    return directory


def _configure(db_session, admin_account: UserAccount, **overrides) -> None:
    """写备份配置。**写完必须 flush**，见模块 docstring 第 2 条。"""
    values = {"dir": "", "secondary_dir": "", "keep_days": 30, "auto_enabled": True}
    values.update(overrides)
    settings_service.update_namespace(db_session, "backup", values, admin_account)
    db_session.flush()


@pytest.fixture()
def configured(db_session, admin_account: UserAccount, backup_dir: Path) -> Path:
    _configure(db_session, admin_account, dir=str(backup_dir))
    return backup_dir


def _audit_rows(db_session, action: str) -> list[AuditLog]:
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
        ).all()
    )


# ------------------------------------------------------------------- 找 mysqldump


def test_find_mysqldump_accepts_a_candidate_that_answers_version(
    monkeypatch: pytest.MonkeyPatch,
):
    """**跑一次 `--version` 才知道行不行**，不是 `Path.exists()`。

    `Test-Path` / `is_file()` 只说「有个文件」——别名、半截安装、另一个软件带进来的同名
    exe 都让它为真。所以判据是「那个命令真的能跑起来」，与 `install.ps1` 的
    `Resolve-BasePython` 同一条（§18：候选挨个真跑一次、只看退出码）。
    """
    monkeypatch.setattr(backup_service, "_FOUND_DUMP", None)
    monkeypatch.setattr(
        backup_service, "_mysqldump_candidates", lambda: [Path(sys.executable)]
    )
    assert backup_service.find_mysqldump() == Path(sys.executable)


def test_find_mysqldump_reports_what_it_tried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """找不到时给的是一句**能照着做**的中文，而且把试过哪些路径说出来。

    与 §18 那条「空态是一句关于数据的话」同源：一句 `mysqldump not found` 让操作员
    唯一能做的推理是「再装一遍」。
    """
    monkeypatch.setattr(backup_service, "_FOUND_DUMP", None)
    decoy = tmp_path / "not-a-dump.txt"
    decoy.write_text("我不是 mysqldump", encoding="utf-8")
    missing = tmp_path / "missing" / "mysqldump"
    monkeypatch.setattr(backup_service, "_mysqldump_candidates", lambda: [decoy, missing])

    with pytest.raises(AppError) as caught:
        backup_service.find_mysqldump()

    assert caught.value.code == "BACKUP_DUMP_MISSING"
    message = caught.value.message
    assert str(decoy) in message and str(missing) in message, "试过哪些路径要说出来"
    assert "装好之后不用重启服务" in message, "出路写在这一句里（§18 的「空态」那条）"


def test_find_mysqldump_on_this_machine(monkeypatch: pytest.MonkeyPatch):
    """本机的候选表那一支**真的会被走到**——它不是一段只有目标机才执行的代码。

    开发机 `mysqldump` 不在 PATH 上，而 `/usr/local/mysql/bin/mysqldump` 在，所以这条
    路走的是 `_windows_service_dump_paths` / 已知位置那一支。**没有 mysqldump 的机器上
    这条跳过**（而不是把断言改松）：那种机器上「找不到」是对的，只是证明不了这一条。
    """
    monkeypatch.setattr(backup_service, "_FOUND_DUMP", None)
    try:
        binary = backup_service.find_mysqldump()
    except AppError as error:
        assert error.code == "BACKUP_DUMP_MISSING"
        assert "装好之后不用重启服务" in error.message
        pytest.skip(f"本机没有 mysqldump，跳过正向断言（{error.message[:40]}…）")
    assert binary.stem == "mysqldump"
    assert backup_service._probe(binary), "认下来的那个必须真的跑得起来"


# --------------------------------------------------------------------- 一次备份


def test_a_successful_backup_leaves_a_file_and_a_record(
    db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """一次成功的备份：文件真的在盘上、记录一行 `SUCCEEDED`、审计一条。"""
    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_SUCCEEDED
    assert record.trigger == TRIGGER_MANUAL
    assert record.operator_id == admin_account.id
    assert record.message is None, "没有第二路径、没有过期文件，就没有话要说"

    final = configured / record.file_name
    assert final.is_file()
    assert record.file_size == final.stat().st_size > 0
    assert record.file_sha256 == hashlib.sha256(final.read_bytes()).hexdigest()

    # `.part` 是原子落盘的中间态，成功之后一个都不该剩（「文件在 ⇒ 备份成功」靠它）。
    assert list(configured.glob("*.part")) == []

    rows = _audit_rows(db_session, backup_service.AUDIT_ACTION_BACKUP)
    assert len(rows) == 1
    assert rows[0].resource_id == str(record.id)
    assert rows[0].detail and record.file_name in rows[0].detail


def test_the_password_never_reaches_the_command_line_or_a_leftover_file(
    db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """口令走 `--defaults-extra-file`，**不进命令行**，而且那个临时文件跑完就没了。

    Windows 上任何账号都能通过 WMI 的 `Win32_Process` 读到别的进程的命令行（§18 已经为
    `install.ps1` 的提权写过同一条），所以这不是洁癖。临时文件的生命周期由**调用方**
    负责：只把 `finally` 写在 `_run_dump` 里等于每次都漏一个。

    两处刻意不这么写：

    - **不把命令行拼成一整串再查子串。** `tmp_path` 里嵌着**用例名**，而这一条用例叫
      `test_the_password_never_reaches_…` —— 于是导出文件那条路径天生带着 `password`
      这个词，拼串比对会把一次正确的实现判红（第一版就是这么红的）。所以逐项比，
      并且把两根路径参数排除在外：它们是**我们自己造的路径**，不是凭据。
    - **不只断「命令行里没有口令」**：那样一个**什么都没传**的实现也是绿的。所以先断
      `cnf_text` 里**确实有**口令（`_DumpStub` 在调用那一刻把它读下来），再断命令行里
      没有 —— 一正一反两条一起才说明「它走的是那条路」。
    """
    from app.core.config import get_settings
    from app.db.mysql_url import parse_database_url

    target = parse_database_url(get_settings().database_url)
    backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    command = dump.command
    assert "--defaults-extra-file=" in " ".join(command)

    cnf = [part.split("=", 1)[1] for part in command if part.startswith("--defaults-extra-file=")][0]
    assert dump.cnf_text is not None, "没读到临时 cnf —— 它要么没被写出来，要么写在别处"
    if target.password:
        assert target.password in dump.cnf_text, "口令没进 cnf，那它根本没被传下去"
        assert target.password not in command, "口令进了命令行"
        assert not any(
            target.password in part
            for part in command
            if not part.startswith(("--result-file=", "--defaults-extra-file="))
        ), "口令藏进了别的参数"

    assert not Path(cnf).exists(), "临时 cnf 跑完必须被删掉"


def test_everything_the_stub_replaces_is_covered_by_a_real_run(
    db_session, configured: Path, admin_account: UserAccount
):
    """**不桩**的那一条：真起一个进程、真拿到它的退出码与 stderr。

    桩把 `subprocess.run` 换掉了，所以它证明不了「真 mysqldump 报错时我们收得到它的话」——
    而那正是这次修复的核心（原来那句 `throw "退出码 N"` 把它扔了，于是操作员看到的是
    「窗口一闪就关，记不得是哪一句」）。

    用 `sys.executable` 当 mysqldump：它会把 `--defaults-extra-file=…` 当成未知选项，
    在**任何平台**上以非 0 退出并把那句话写进 stderr。**不写死退出码**——那是 CPython 的
    选择，不是我们的判据；我们只要求「非 0」与「它自己的话留下来了」。
    """
    record = backup_service.run_backup(
        db_session, operator=admin_account, dump_binary=sys.executable
    )
    db_session.flush()

    assert record.status == STATUS_FAILED
    assert record.message
    code = re.search(r"退出码 (\d+)", record.message)
    assert code and int(code.group(1)) != 0
    assert "--defaults-extra-file" in record.message, "它自己的那句 stderr 必须留痕"


def test_a_failed_dump_leaves_the_reason_and_no_file(
    db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """**失败也要留痕**——这条是这个功能存在的理由本身。

    原来的 `备份数据.bat` 失败时窗口一闪就关；自动备份更是凌晨、没人看着、连窗口都没有。
    所以 mysqldump 说的那句话必须落进 `message`，而且三个文件列一起清空（一次没产出
    文件的尝试没有文件名）。
    """
    stub = _DumpStub(returncode=2, stderr="mysqldump: Got error: 1045: Access denied".encode())
    backup_service.subprocess.run = stub  # type: ignore[assignment]

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_FAILED
    assert "1045" in record.message and "Access denied" in record.message
    assert record.file_name is None and record.file_size is None
    assert record.file_sha256 is None
    assert list(configured.iterdir()) == [], "失败不该在目录里留下任何东西"

    # 失败同样要有一条轨迹：静默的失败与「没有备份过」在界面上长得一模一样。
    rows = _audit_rows(db_session, backup_service.AUDIT_ACTION_BACKUP)
    assert len(rows) == 1 and rows[0].detail and "失败" in rows[0].detail


def test_a_dump_that_cannot_even_start_says_so_in_chinese(
    db_session, configured: Path, admin_account: UserAccount, monkeypatch: pytest.MonkeyPatch
):
    """`mysqldump` 跑**不起来**（二进制没了）时，落进 `message` 的也要是一句人话。

    这一条是实测撞出来的：`find_mysqldump()` 只在**启动那一刻**探候选（`_FOUND_DUMP`
    有缓存），而**服务跑着的时候 MySQL 被卸掉或升级**是真实存在的路径。不接住 `OSError`
    时落进 `record.message` 的是
    `FileNotFoundError: [Errno 2] No such file or directory: '/bin/false'` —— 而那一列在
    管理页上是「说明」，给操作员看的（§2：服务端答了话，那句话本来就是写给用户看的）。

    判据两半都要：**说得是中文**（不含 `FileNotFoundError`）**且写得出怎么办**（指出是
    哪个二进制、要不要重启服务）。只断前半句的话，一个把异常原文直接塞进去的实现也过。
    """
    def _boom(command, **kwargs):  # noqa: ANN001, ANN003 —— 与 subprocess.run 同形
        raise FileNotFoundError(2, "No such file or directory", str(command[0]))

    monkeypatch.setattr(backup_service.subprocess, "run", _boom)

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_FAILED
    assert "FileNotFoundError" not in record.message, "内部异常名不该出现在给操作员看的那一列"
    assert "mysqldump 跑不起来" in record.message
    assert "不用重启服务" in record.message, "这句话要写得出下一步怎么办"


def test_a_dump_that_hangs_is_aborted_with_a_readable_reason(
    db_session, configured: Path, admin_account: UserAccount, monkeypatch: pytest.MonkeyPatch
):
    """超时同样要说人话。

    最真实的场景是备份目录落在一个**断掉的网盘**上（第二路径那一支正是为这个加的），
    那时 mysqldump 会一直卡着。`subprocess.TimeoutExpired` 的原文是
    `Command '[...]' timed out after 600 seconds` —— 一串命令行加一个英文句子。
    `DUMP_TIMEOUT_SECONDS` 是 600，所以这里只能靠桩来造，真等十分钟不是一条能跑的用例。
    """
    def _hang(command, **kwargs):  # noqa: ANN001, ANN003 —— 与 subprocess.run 同形
        raise subprocess.TimeoutExpired(command, backup_service.DUMP_TIMEOUT_SECONDS)

    monkeypatch.setattr(backup_service.subprocess, "run", _hang)

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_FAILED
    assert "TimeoutExpired" not in record.message
    assert str(backup_service.DUMP_TIMEOUT_SECONDS) in record.message
    assert "磁盘" in record.message or "可写" in record.message, "要指向最可能的那个原因"


def test_a_directory_that_cannot_be_created_is_a_recorded_failure(
    db_session, admin_account: UserAccount, tmp_path: Path
):
    """错在我们自己这一侧（目录建不出来）时，同样落一行，而不是抛出去。

    一次备份失败**不能**让调用方拿到一个异常——`run_server.py` 的定时线程与命令行
    都靠「返回的记录」说话。这里把备份目录指到一个普通文件底下，`mkdir` 必失败。
    """
    blocker = tmp_path / "afile"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")
    _configure(db_session, admin_account, dir=str(blocker / "sub"))

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_FAILED
    assert record.message


# ----------------------------------------------------------------------- 去重


def test_a_second_run_on_the_same_day_is_skipped(
    db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """定时那一路：今天已经成功过一次（且文件还在）就不再备。"""
    first = backup_service.run_scheduled_backup(db_session)
    db_session.flush()
    assert first is not None and first.trigger == TRIGGER_AUTO

    calls_before = len(dump.calls)
    assert backup_service.run_scheduled_backup(db_session) is None
    assert len(dump.calls) == calls_before, "跳过时一次 dump 都不该跑"


def test_a_deleted_file_makes_the_day_run_again(
    db_session, configured: Path, dump: _DumpStub
):
    """文件被人手工删掉之后，去重**不再生效**，于是再备一次。

    方向是安全的那一侧：宁可多一份，不可少一份。「有记录」与「有文件」本该同时成立，
    这一支处理的是它们不一致的那种情况。
    """
    first = backup_service.run_scheduled_backup(db_session)
    db_session.flush()
    assert first is not None

    (configured / first.file_name).unlink()

    again = backup_service.run_scheduled_backup(db_session)
    db_session.flush()
    assert again is not None and again.id != first.id


def test_turning_auto_off_stops_the_scheduled_path_but_not_the_button(
    db_session, admin_account: UserAccount, backup_dir: Path, dump: _DumpStub
):
    """关掉自动之后定时那一路什么都不做，**而手动那一路照常**。"""
    _configure(db_session, admin_account, dir=str(backup_dir), auto_enabled=False)

    assert backup_service.run_scheduled_backup(db_session) is None
    assert dump.calls == [], "关掉之后一次 dump 都不该跑"

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    assert record.status == STATUS_SUCCEEDED


# ------------------------------------------------------------------- 保留天数


def test_prune_removes_only_files_this_service_names(
    db_session, admin_account: UserAccount, backup_dir: Path, dump: _DumpStub
):
    """`keep_days` 只清理**本服务命名**的、比保留期更旧的文件。

    目录里躺着的别的东西（别人手工导出的、另一个程序放的）一个都不许碰——那正是
    「一次测试把开发机上的东西删了」的形状。
    """
    old = dt.datetime.now() - dt.timedelta(days=10)
    stale_backup = backup_dir / "xinliceping-20200101-000000.sql"
    stale_backup.write_text("旧备份", encoding="utf-8")
    ours_recent = backup_dir / "xinliceping-20990101-000000.sql"
    ours_recent.write_text("未来的备份", encoding="utf-8")
    foreign_sql = backup_dir / "not-ours.sql"
    foreign_sql.write_text("别的程序放的", encoding="utf-8")
    foreign_txt = backup_dir / "readme.txt"
    foreign_txt.write_text("说明", encoding="utf-8")
    for path in (stale_backup, foreign_sql, foreign_txt):
        os.utime(path, (old.timestamp(), old.timestamp()))

    _configure(db_session, admin_account, dir=str(backup_dir), keep_days=3)
    backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert not stale_backup.exists(), "过了保留期的本服务备份该被清掉"
    assert ours_recent.exists(), "没到期的不能清"
    assert foreign_sql.exists() and foreign_txt.exists(), "不是自己命名的文件一个都不许碰"


def test_keep_days_zero_means_never_delete(
    db_session, admin_account: UserAccount, backup_dir: Path, dump: _DumpStub
):
    """`0` 是「只增不删」，不是「一个都不留」（`_prune` 的第一句就是这条）。"""
    old = dt.datetime.now() - dt.timedelta(days=3650)
    stale = backup_dir / "xinliceping-20150101-000000.sql"
    stale.write_text("很久以前", encoding="utf-8")
    os.utime(stale, (old.timestamp(), old.timestamp()))

    _configure(db_session, admin_account, dir=str(backup_dir), keep_days=0)
    backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert stale.exists()


# --------------------------------------------------------------------- 第二路径


def test_the_second_copy_lands_in_both_places(
    db_session, admin_account: UserAccount, backup_dir: Path, tmp_path: Path, dump: _DumpStub
):
    secondary = tmp_path / "offsite"
    _configure(db_session, admin_account, dir=str(backup_dir), secondary_dir=str(secondary))

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_SUCCEEDED
    assert record.message is None
    assert (backup_dir / record.file_name).is_file()
    assert (secondary / record.file_name).read_bytes() == (
        backup_dir / record.file_name
    ).read_bytes(), "第二份必须与主份逐字节相同"


def test_a_secondary_copy_that_fails_does_not_turn_success_into_failure(
    db_session, admin_account: UserAccount, backup_dir: Path, tmp_path: Path, dump: _DumpStub
):
    """「多存一份没存成」不该把一次成功的备份变成失败——它是一句警告。

    把第二路径指到一个**已存在的普通文件**上：`mkdir` 抛 `OSError`，`_copy_secondary`
    接住并把它写成一句中文。主份必须完好，状态仍是 `SUCCEEDED`。
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("我是文件", encoding="utf-8")
    _configure(
        db_session, admin_account, dir=str(backup_dir), secondary_dir=str(blocker)
    )

    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    assert record.status == STATUS_SUCCEEDED
    assert record.message and "第二路径复制失败" in record.message
    assert (backup_dir / record.file_name).is_file()


# ------------------------------------------------------------------------ 下载


def test_the_download_returns_the_exact_bytes(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """下载回来的东西与磁盘上那份**逐字节相同**，而且带着文件名。"""
    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    headers = auth_headers(client, "admin", "admin")

    response = client.get(f"/api/v1/admin/backup/records/{record.id}/download", headers=headers)

    assert response.status_code == 200
    assert response.content == (configured / record.file_name).read_bytes()
    assert record.file_name in response.headers["content-disposition"]
    assert response.headers["content-type"] == "application/octet-stream"


def test_a_tampered_file_is_refused(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """摘要对不上就**不发文件**（409）。

    宁可发不出去，也不要发一份说不清是不是当初那一份的文件（§29 逐字定过这条）。
    """
    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    record.file_sha256 = "0" * 64
    db_session.flush()
    headers = auth_headers(client, "admin", "admin")

    response = client.get(f"/api/v1/admin/backup/records/{record.id}/download", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BACKUP_FILE_CHANGED"
    assert response.json()["data"] is None


def test_a_file_name_that_escapes_the_directory_is_refused(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """库里的 `file_name` 被改成 `../` 时必须被挡住（它是我们写的，但库可改）。"""
    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    record.file_name = "../../etc/passwd"
    db_session.flush()
    headers = auth_headers(client, "admin", "admin")

    response = client.get(f"/api/v1/admin/backup/records/{record.id}/download", headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BACKUP_PATH_REJECTED"


def test_a_file_that_is_gone_is_a_410_not_a_404(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """文件被搬走／删掉 → 410，并指出最可能的原因；记录本身仍然在。

    与「记录不存在」（404）分开，因为它们导向两个不同的动作：一个是「重新备份」，
    另一个是「你手上的 id 不对」。
    """
    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    (configured / record.file_name).unlink()
    headers = auth_headers(client, "admin", "admin")

    response = client.get(f"/api/v1/admin/backup/records/{record.id}/download", headers=headers)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "BACKUP_FILE_MISSING"


def test_a_failed_record_and_a_missing_record_are_both_404(
    client, db_session, configured: Path, admin_account: UserAccount
):
    """没成功过的记录与不存在的记录回同一句——**不可分辨**（§24）。"""
    failed = backup_service.run_backup(
        db_session, operator=admin_account, dump_binary=sys.executable
    )
    db_session.flush()
    assert failed.status == STATUS_FAILED
    headers = auth_headers(client, "admin", "admin")

    for record_id in (failed.id, failed.id + 100_000):
        response = client.get(
            f"/api/v1/admin/backup/records/{record_id}/download", headers=headers
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "BACKUP_NOT_FOUND"


def test_the_download_is_audited_with_the_no_masking_sentence(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """每次下载写一条审计，`detail` 里明写「不可能遮蔽」。

    照 §8 那条「导出审计必须记录遮蔽模式」：一份备份**没有**遮蔽这一档，这句话必须
    出现在轨迹里，而不是靠读轨迹的人自己去想。
    """
    record = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    headers = auth_headers(client, "admin", "admin")

    client.get(f"/api/v1/admin/backup/records/{record.id}/download", headers=headers)

    rows = _audit_rows(db_session, backup_service.AUDIT_ACTION_DOWNLOAD)
    assert len(rows) == 1
    assert rows[0].detail and "不可能遮蔽" in rows[0].detail
    assert rows[0].actor_user_id == admin_account.id


# ------------------------------------------------------------------------ 权限


@pytest.mark.parametrize(
    ("role", "account"),
    [("counselor", "13800000001"), ("leader", "13800000002")],
)
def test_only_the_admin_reaches_the_backup_endpoints(client, role: str, account: str):
    """心理老师与德育领导三个端点各 403。

    **管理员能下载是 §4 的一次有意例外**（他没有 `STUDENT_PSYCH_DETAIL`，却能取走一份
    整库 dump）——理由与护栏写在 §34 与 `open_record_for_download` 的 docstring 里。
    这条用例钉的是例外的**边界**：它只有管理员那一个口子。

    **「立即备份」按它真正的动词打。** 第一版把它也塞进那个 `client.get` 循环里，于是
    收到的是 **405**（方法不对）而不是 403 —— 405 只说明这个 URL 上挂的是别的动词，
    什么权限也没证明。一个只挡 `GET` 的实现会在下面那条 POST 上露出来。
    """
    headers = auth_headers(client, role, account)
    for path in (
        "/api/v1/admin/backup",
        "/api/v1/admin/backup/records/1/download",
    ):
        assert client.get(path, headers=headers).status_code == 403, path
    # 「立即备份」是 POST：一个只挡 GET 的实现会在这里露出来。
    assert client.post("/api/v1/admin/backup/run", headers=headers).status_code == 403


def test_the_admin_can_read_the_overview(client, configured: Path):
    """概览页的形状：落点是**服务端展开之后**的路径，不是配置里的那个字面量。"""
    headers = auth_headers(client, "admin", "admin")

    response = client.get("/api/v1/admin/backup", headers=headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert Path(data["dir"]).resolve() == configured.resolve()
    assert data["dir_is_default"] is False, "配过目录了，不该说它是默认目录"
    assert data["keep_days"] == 30 and data["auto_enabled"] is True
    assert data["records"] == [] and data["last_success"] is None and data["last_failure"] is None


def test_an_unconfigured_directory_is_reported_as_the_default_one(
    client, db_session, admin_account: UserAccount
):
    """`dir` 留空时界面拿到的是**默认目录的绝对路径**，而 `dir_is_default` 说得出它是默认。

    §9：一个写着「留空即默认」的输入框答不出「那我这台机器上到底落在哪」。这条用例
    只读概览、**不备份**，所以不会真的往默认目录里写东西。
    """
    _configure(db_session, admin_account, dir="")
    headers = auth_headers(client, "admin", "admin")

    data = client.get("/api/v1/admin/backup", headers=headers).json()["data"]

    assert data["dir_is_default"] is True
    assert Path(data["dir"]).is_absolute()
    assert data["dir"].endswith(backup_service.DEFAULT_DIR_NAME)


# ------------------------------------------------------------------ 手动那一路


def test_the_run_endpoint_returns_the_failure_instead_of_raising(
    client, db_session, configured: Path, admin_account: UserAccount, monkeypatch
):
    """mysqldump 坏掉时**同步**回一行 `FAILED`，而不是一个 500。

    这是「失败也要留痕」在接口上的那一面：界面上那枚按钮读的是 `record.status` 分岔，
    所以服务端必须把失败原样交回来（`AppHomePage` 的 `runNow()` 注释写着这条）。
    """
    monkeypatch.setattr(
        backup_service, "find_mysqldump", lambda: Path(sys.executable)
    )
    headers = auth_headers(client, "admin", "admin")

    response = client.post("/api/v1/admin/backup/run", headers=headers)

    assert response.status_code == 200
    record = response.json()["data"]
    assert record["status"] == STATUS_FAILED
    assert record["message"]
    assert record["trigger"] == TRIGGER_MANUAL
    assert record["operator_name"] == admin_account.display_name


def test_the_run_endpoint_leaves_a_downloadable_row(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """手动备份成功之后，概览里那一行带着 `file_exists`，历史表也就有了下载按钮。"""
    headers = auth_headers(client, "admin", "admin")

    created = client.post("/api/v1/admin/backup/run", headers=headers).json()["data"]
    assert created["status"] == STATUS_SUCCEEDED and created["file_exists"] is True

    data = client.get("/api/v1/admin/backup", headers=headers).json()["data"]
    assert data["last_success"]["id"] == created["id"]
    assert data["last_failure"] is None
    assert [row["id"] for row in data["records"]] == [created["id"]]


def test_the_overview_reports_the_last_failure_even_after_a_success(
    client, db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub
):
    """失败过之后又成功，那一行失败**仍然显示**——一条被盖住的失败等于没有记录。"""
    _DumpStub  # 见下：先借同一个桩打一次失败，再换成成功的
    backup_service.subprocess.run = _DumpStub(  # type: ignore[assignment]
        returncode=1, stderr=b"boom"
    )
    backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()
    backup_service.subprocess.run = dump  # type: ignore[assignment]
    success = backup_service.run_backup(db_session, operator=admin_account)
    db_session.flush()

    headers = auth_headers(client, "admin", "admin")
    data = client.get("/api/v1/admin/backup", headers=headers).json()["data"]

    assert data["last_failure"] is not None and "boom" in data["last_failure"]["message"]
    assert data["last_success"]["id"] == success.id


# ---------------------------------------------------------------------- 工具函数


def test_a_name_collision_gets_a_suffix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """同一秒里的第二次备份叫 `…-2.sql`，第三次叫 `…-3.sql`。

    名字只到秒（`%Y%m%d-%H%M%S`），而两次备份确实可能落在同一秒里。判据是「往后试一个」，
    撞满 `MAX_NAME_ATTEMPTS` 次才报错。
    """
    frozen = dt.datetime(2026, 9, 27, 3, 15, 0)
    monkeypatch.setattr(backup_service, "now_local_naive", lambda: frozen)
    stamp = frozen.strftime("%Y%m%d-%H%M%S")

    first = backup_service._available_path(tmp_path, stamp)
    assert first.name == f"{backup_service.FILE_PREFIX}{stamp}{backup_service.FILE_SUFFIX}"

    first.write_text("占住这个名字", encoding="utf-8")
    second = backup_service._available_path(tmp_path, stamp)
    assert second.name == f"{backup_service.FILE_PREFIX}{stamp}-2{backup_service.FILE_SUFFIX}"

    second.write_text("再占一个", encoding="utf-8")
    third = backup_service._available_path(tmp_path, stamp)
    assert third.name == f"{backup_service.FILE_PREFIX}{stamp}-3{backup_service.FILE_SUFFIX}"


def test_native_output_is_decoded_from_the_console_code_page():
    """原生工具的输出按本机代码页解（中文 Windows = cp936），不按 UTF-8 硬解。

    §18 那条「原生工具的输出是另一套编码」：robocopy / icacls 重定向到管道时写的是
    控制台代码页。mysqldump 的 stderr 走同一条路，那一屏文字是操作员唯一能提供的东西，
    解错了就再也读不回来。
    """
    assert _encode_native("中文错误".encode("gbk")) == "中文错误"
    assert _encode_native(b"plain ascii") == "plain ascii"
    assert _encode_native("真 UTF-8".encode("utf-8")) == "真 UTF-8"


def test_failure_message_keeps_the_dump_tools_own_words():
    """`_DumpFailed` 的话里带着 mysqldump 自己的输出，而 `failure_message` 原样用它。

    **这正是原来那个 bug 的修复点**：上一版把 output 只存成属性，于是落进
    `record.message` 的只有「退出码 2」——异常里带着原文，却没有读者。
    """
    failed = _DumpFailed(2, "mysqldump: Got error: 1045: Access denied\n")
    assert str(failed) == "mysqldump 退出码 2：mysqldump: Got error: 1045: Access denied"
    assert failure_message(failed) == str(failed)
    # **这一句才是那条分支的判据**：`record.message` 会显示在「说明」那一列上，
    # 而那一列是给操作员看的 —— 一个内部类名漏进去就等于把话说给错了人。
    assert "_DumpFailed" not in failure_message(failed)
    # 没有 mysqldump 的话时只留退出码，不留一个空的冒号。
    assert str(_DumpFailed(2, "  ")) == "mysqldump 退出码 2"

    # `AppError` 的 message 本来就是写给用户看的，不加类型名前缀（§2）。
    assert failure_message(AppError("X", "一句给用户看的话", 409)) == "一句给用户看的话"
    # 其余异常带类型名：一句光秃秃的 `Permission denied` 不如带上类型好查。
    assert failure_message(ValueError("boom")).startswith("ValueError: ")


# ------------------------------------------------------------------ 并发与 CLI


def test_two_manual_runs_do_not_overlap(client, db_session, configured: Path, admin_account):
    """另一条路正在跑时，手动那一路回 **409**（不是排队）。

    让用户看着一个转圈的按钮等一次不属于他的备份更糟。`_RUN_LOCK` 是模块级的，这里
    手动持住它模拟「正在跑」。
    """
    assert backup_service._RUN_LOCK.acquire(blocking=False)
    try:
        headers = auth_headers(client, "admin", "admin")
        response = client.post("/api/v1/admin/backup/run", headers=headers)
    finally:
        backup_service._RUN_LOCK.release()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BACKUP_RUNNING"


class _SharedSession:
    """把测试夹具的会话包成 `SessionLocal()` 的样子。

    `run_once` 在**函数体内** import `SessionLocal`，所以 `monkeypatch.setattr` 打在
    `app.db.session.SessionLocal` 上就能换掉它。退出时不关会话——夹具还要用它，
    而真 `SessionLocal()` 的 `__exit__` 会 close。
    """

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc) -> bool:
        return False


def test_the_command_line_wrapper_reports_and_returns_an_exit_code(
    db_session, configured: Path, admin_account: UserAccount, dump: _DumpStub, capsys, monkeypatch
):
    """`python -m app.db.backup`：成功 0 / 失败 1，**两句话都打在 stdout 上**。

    它调的是界面那枚按钮调的同一个函数，所以命令行与界面不可能各说各话——包括并发。
    失败那一句是操作员的唯一线索，所以它必须带上原因，而不只是「失败了」。
    """
    from app.db import backup as cli

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _SharedSession(db_session))

    assert cli.run_once() == 0
    assert "[OK] 备份完成" in capsys.readouterr().out

    backup_service.subprocess.run = _DumpStub(  # type: ignore[assignment]
        returncode=1, stderr=b"disk full"
    )
    assert cli.run_once() == 1
    out = capsys.readouterr().out
    assert "[X] 备份失败" in out and "disk full" in out


def test_the_command_line_flags_reach_the_dump_binary(
    db_session, configured: Path, dump: _DumpStub, capsys, monkeypatch
):
    """`--dump-binary` 真的透传到 `run_backup`（`ops.ps1` 的薄壳与排障都靠它）。"""
    from app.db import backup as cli

    monkeypatch.setattr("app.db.session.SessionLocal", lambda: _SharedSession(db_session))

    assert cli.main(["--dump-binary", sys.executable]) == 0
    assert dump.calls, "桩一次都没被调用，说明 --dump-binary 没传下去"
    capsys.readouterr()
