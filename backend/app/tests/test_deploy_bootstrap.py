"""部署引导那几件小事（CLAUDE.md §18）。

这里钉的都是**纯函数与无副作用的判断**：URL 怎么拆、密码怎么解码、改密码改了哪几列、
清理脚本在不加 `--yes` 时动不动手。真正连 MySQL 的那几条路（建库、跑那份 SQL）在
`make deploy-package` 之后由人工在临时库上走一遍，这里跑不了——conftest 是内存 sqlite。

**变异验证**：把 `mysql_url.parse_database_url` 里的 `unquote` 摘掉 →
`test_the_password_is_percent_decoded` 红；把 `set_password_in` 改成直接写
`user.password_hash = password` → 改名那两条红。
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db import reset_to_baseline
from app.db.create_database import explain
from app.db.mysql_url import DatabaseUrlError, parse_database_url
from app.db.set_admin_password import set_password_in
from app.models.account import UserAccount
from app.security.passwords import verify_password
from app.services.auth_service import authenticate
from app.schemas.auth import LoginRequest


# --- XLP_DATABASE_URL 怎么拆 -------------------------------------------------


def test_a_plain_url_parses():
    target = parse_database_url("mysql+pymysql://root:secret@10.0.0.5:3306/xinliceping")
    assert (target.user, target.password) == ("root", "secret")
    assert (target.host, target.port, target.database) == ("10.0.0.5", 3306, "xinliceping")
    assert target.charset == "utf8mb4"


@pytest.mark.parametrize("raw", ["@", ":", "/", "#", "?", "%", "&", " ", "中"])
def test_the_password_is_percent_decoded(raw: str):
    """**这一条是安装器与后端之间的接口。**

    安装脚本写 URL 时用 `quote(pw, safe='')`（不编码的话，密码里的 `@` / `:` 会把
    URL 拆成另一个形状），所以后端这一侧必须 `unquote` 回来。漏掉这一步不会报
    「解析失败」——它会拿一串 `pa%40ss` 去认证，MySQL 回「Access denied」，
    与「密码打错了」长得一模一样，而密码其实是对的。
    """
    from urllib.parse import quote

    encoded = quote(f"pa{raw}ss", safe="")
    target = parse_database_url(f"mysql+pymysql://root:{encoded}@127.0.0.1:3306/xinliceping")
    assert target.password == f"pa{raw}ss"


def test_the_missing_pieces_have_defaults():
    target = parse_database_url("mysql+pymysql://root@/xinliceping")
    assert target.host == "127.0.0.1"  # 空主机名是「就是本机」，不是「配错了」
    assert target.port == 3306
    assert target.password == ""


def test_a_url_without_a_database_is_refused():
    with pytest.raises(DatabaseUrlError, match="库名"):
        parse_database_url("mysql+pymysql://root:secret@127.0.0.1:3306")


def test_a_non_mysql_url_is_refused():
    with pytest.raises(DatabaseUrlError, match="MySQL"):
        parse_database_url("postgresql://root:secret@127.0.0.1:5432/xinliceping")


def test_a_database_name_that_cannot_be_quoted_is_refused():
    """库名要进 `CREATE DATABASE \\`…\\`` 的标识符位置，那里没有占位符可用。"""
    for bad in ["a`b", "a\\b", "a/b"]:
        with pytest.raises(DatabaseUrlError):
            parse_database_url(f"mysql+pymysql://root@127.0.0.1:3306/{bad}")


def test_query_parameters_survive():
    """`?charset=` 之外的查询参数要留着，不能让「解析过一趟」把它们弄丢。"""
    target = parse_database_url(
        "mysql+pymysql://root@127.0.0.1:3306/db?charset=gbk&connect_timeout=5&ssl=true"
    )
    assert target.charset == "gbk"
    assert target.extra_query == {"connect_timeout": "5", "ssl": "true"}


def test_connect_kwargs_can_leave_the_database_out():
    """建库那一步必须连服务端而不连库——库还不存在时，连它本身就会失败。"""
    target = parse_database_url("mysql+pymysql://root@127.0.0.1:3306/xinliceping")
    assert "database" not in target.connect_kwargs(with_database=False)
    assert target.connect_kwargs()["database"] == "xinliceping"


# --- 报错翻译 ---------------------------------------------------------------


def test_mysql_errors_get_an_actionable_hint():
    import pymysql

    assert "服务没起来" in explain(pymysql.err.OperationalError(2003, "Can't connect"))
    assert "密码" in explain(pymysql.err.OperationalError(1045, "Access denied"))


def test_the_cryptography_error_is_named():
    """这一条只在「MySQL 重启过」的机器上出现，所以更要说清楚它是什么。

    服务端缓存着凭据时 `caching_sha2_password` 走快路径，用不到 RSA；缓存是内存里的，
    重启就空。详见 CLAUDE.md §18。
    """
    hint = explain(RuntimeError("'cryptography' package is required for caching_sha2_password"))
    assert "cryptography" in hint


def test_the_errors_only_a_self_prepared_database_produces_are_named():
    """「库和表都由操作员自己准备」那一路上会撞的四个码。

    这四种的共同点是**安装器无从假设**：那个 MySQL 账号是操作员开的（权限给到哪一层）、
    那几张表是他自己建的（哪一版）。没有这几句提示，屏幕上是 `pymysql.err.OperationalError`
    加一句 MySQL 原文，而操作员（非技术人员）唯一能做的推理是「重装一遍试试」。
    """
    import pymysql

    def hint(code: int, message: str) -> str:
        return explain(pymysql.err.OperationalError(code, message))

    assert "权限" in hint(1044, "Access denied for user 'xlp'@'localhost' to database 'xinliceping'")
    assert "权限" in hint(1142, "SELECT command denied to user 'xlp'@'localhost' for table 'student'")
    for code, original in (
        (1050, "Table 'user_account' already exists"),
        (1054, "Unknown column 'age' in 'student'"),
    ):
        # 这两个码的处置指向的是同一件事：先看 `ensure_schema` 说缺什么。
        # **断言它点了名**，不是断言文案——「去跑一次校对」这句话不说出来，操作员
        # 手上就只有一句英文 MySQL 错，而这一对整个模块存在的理由就是替他说这句话。
        assert "ensure_schema" in hint(code, original), f"{code} 的处置没有告诉他先校对表结构"
    assert "部署说明" in hint(1049, "Unknown database 'xinliceping'"), (
        "1049 在「库我自己准备」那两条路上是**正常**的失败（库还没建），"
        "不能再只说「这条不该出现」"
    )


def test_an_unknown_error_is_passed_through_verbatim():
    """没列到的报错**原样**带出来——猜错的处置比没有处置更费时间。

    断言的是原文出现，不是「输出里有 1064」：后者在任何人给 1064 编一句中文提示之后
    照样成立，而那时候原文已经被替换掉了，恰好是这条要防的事。
    """
    import pymysql

    message = explain(pymysql.err.OperationalError(1064, "You have an error in your SQL syntax"))
    assert "You have an error in your SQL syntax" in message


# --- 改密码 -----------------------------------------------------------------


def test_setting_a_password_changes_it_and_leaves_others_alone(db_session):
    target = db_session.scalar(select(UserAccount).where(UserAccount.account == "S001"))
    headmaster = db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000002"))
    before = headmaster.password_hash

    assert set_password_in(db_session, "S001", "new-pass-9") is True
    db_session.flush()

    assert verify_password("new-pass-9", target.password_hash)
    assert not verify_password("123456", target.password_hash)
    assert headmaster.password_hash == before  # 别人一个字节都没动


def test_the_new_password_actually_logs_in(db_session):
    """「改了」与「能用它登录」是两件事：写错列的改动同样会让哈希变。"""
    set_password_in(db_session, "S001", "new-pass-9")
    db_session.flush()

    user, token = authenticate(
        db_session, LoginRequest(role="student", account="S001", password="new-pass-9")
    )
    assert user.account == "S001"
    assert token


def test_a_reset_also_unlocks_the_account(db_session):
    """复用 `auth_service.reset_password` 换来的那一半：改了密码却还锁着，等于没改。

    管理员被锁在门外正是要跑这个脚本的场景之一。
    """
    user = db_session.scalar(select(UserAccount).where(UserAccount.account == "admin"))
    user.failed_attempts = 9
    user.locked_until = user.updated_at + timedelta(days=1)
    db_session.flush()

    set_password_in(db_session, "admin", "new-pass-9")
    db_session.flush()

    assert user.failed_attempts == 0
    assert user.locked_until is None
    assert user.must_change_password is True  # 首次登录必须自己改，与界面上那条重置同口径


def test_an_unknown_account_changes_nothing(db_session):
    before = db_session.scalar(select(UserAccount).where(UserAccount.account == "admin")).password_hash
    assert set_password_in(db_session, "查无此人", "new-pass-9") is False
    db_session.flush()
    after = db_session.scalar(select(UserAccount).where(UserAccount.account == "admin")).password_hash
    assert after == before


def test_the_account_lookup_is_case_insensitive(db_session):
    """`authenticate` 拿的是原文匹配，这里放宽一档：操作员手抖大写一次不该变成
    「找不到账号」——那个提示会把人引向「是不是库不对」。"""
    assert set_password_in(db_session, "ADMIN", "new-pass-9") is True


# --- 清理脚本 ---------------------------------------------------------------


def test_the_reset_sql_file_is_where_the_code_looks_for_it():
    """路径算错的话，安装到一半才会发现——而那时候库已经是半清状态。"""
    assert reset_to_baseline.SQL_FILE.is_file(), reset_to_baseline.SQL_FILE
    assert "DELETE FROM assessment_session" in reset_to_baseline.read_sql()


def test_reset_without_yes_does_nothing(db_session, capsys):
    """一步删掉真数据的命令，不加 `--yes` 就一条都不许执行。

    **退出码是 1 而不是 0**：安装脚本按退出码串下一步，一次「什么都没做」被读成
    「成功了」是最坏的失败方式。
    """
    exit_code = reset_to_baseline.main([])
    assert exit_code == 1
    output = capsys.readouterr().out
    assert "--yes" in output
    # 里面是 `admin`，说明它认出了库名并且只做了报告。
    assert "admin" in output


def test_reset_reports_the_database_it_would_clear(monkeypatch, capsys):
    """报告里必须写出**哪个库**。同一台机器上跑错库是这类脚本唯一真正危险的事。"""
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.db.reset_to_baseline.get_settings",
        lambda: Settings(_env_file=None, database_url="mysql+pymysql://root@127.0.0.1:3306/别的库"),
    )
    assert reset_to_baseline.main([]) == 1
    assert "别的库" in capsys.readouterr().out


# --- 安装器怎么把口令交给 Python ---------------------------------------------


def test_the_admin_password_comes_from_an_environment_variable(monkeypatch):
    """**这是安装器与 `set_admin_password` 之间的接口。**

    Windows 上任何账号都能读别的进程的**命令行**（WMI 的 `Win32_Process`），读不到它的
    **环境块**。所以安装脚本不写 `--password 明文`，而是设一个环境变量再传变量名。
    """
    from app.db import set_admin_password

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(set_admin_password, "set_password", lambda a, p: seen.append((a, p)) or 0)
    monkeypatch.setenv("XLPSETUP_ADMIN_PASSWORD", "一个够长的口令")

    assert set_admin_password.main(["--password-env", "XLPSETUP_ADMIN_PASSWORD"]) == 0
    assert seen == [("admin", "一个够长的口令")]
    # 用完就 `pop`：留着它，子进程会继承，而这个值没有必要再多活一秒。
    import os

    assert "XLPSETUP_ADMIN_PASSWORD" not in os.environ


def test_an_empty_password_variable_is_refused_not_guessed(monkeypatch, capsys):
    """变量设了但是空的，要**明说并停下**，绝不能掉进 `getpass`。

    安装脚本没有控制台可以输口令，`getpass` 在那里读的是空 stdin——它会一直等下去，
    而操作员看到的是一个卡住的安装窗口，日志里一个字都没有。
    """
    from app.db import set_admin_password

    called: list[object] = []
    monkeypatch.setattr(set_admin_password, "set_password", lambda a, p: called.append(p) or 0)
    monkeypatch.setenv("XLPSETUP_ADMIN_PASSWORD", "")

    assert set_admin_password.main(["--password-env", "XLPSETUP_ADMIN_PASSWORD"]) == 1
    assert "空的" in capsys.readouterr().out
    assert called == []


def test_a_short_password_is_refused_before_touching_the_database(monkeypatch, capsys):
    """长度检查排在连库之前——不然一条 5 位的口令会先连上库再被拒。"""
    from app.db import set_admin_password

    called: list[object] = []
    monkeypatch.setattr(set_admin_password, "set_password", lambda a, p: called.append(p) or 0)
    monkeypatch.setenv("XLPSETUP_ADMIN_PASSWORD", "12345")

    assert set_admin_password.main(["--password-env", "XLPSETUP_ADMIN_PASSWORD"]) == 1
    assert f"{set_admin_password.MIN_PASSWORD_LENGTH} 位" in capsys.readouterr().out
    assert called == []


# --- mysqldump 的 --defaults-extra-file --------------------------------------


def test_the_client_options_file_carries_the_decoded_password(tmp_path):
    """备份用的 my.cnf。**口令在这里必须是明文原样**，不是 URL 里那种百分号编码。

    编码漏掉的后果特别难认：`mysqldump` 报的是「Access denied」，与「密码打错了」
    长得一模一样，而人会去重打密码。这一条同时钉住 `parse_database_url` 的解码——
    两边是同一个契约的两半。
    """
    from urllib.parse import quote

    from app.db.mysql_url import client_options_file

    raw = "pa@ss:word/x#1"
    target = parse_database_url(
        f"mysql+pymysql://root:{quote(raw, safe='')}@10.0.0.5:3307/xinliceping"
    )
    text = client_options_file(target)

    assert text.startswith("[client]\n")
    assert f"password={raw}\n" in text
    assert "%40" not in text  # 没有解码的话 `@` 会以 %40 留在文件里
    assert "user=root\n" in text and "host=10.0.0.5\n" in text
    assert "port=3307\n" in text
    assert "default-character-set=utf8mb4\n" in text


def test_the_client_file_is_written_private_and_never_echoes_the_password(
    tmp_path, monkeypatch, capsys
):
    """两个性质：文件**先收窄权限再写内容**，以及口令不进 stdout（stdout 会进日志）。"""
    import os
    import stat

    from app.core.config import Settings
    from app.db import mysql_url

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(
            _env_file=None,
            database_url="mysql+pymysql://root:secretpw@127.0.0.1:3306/xinliceping",
        ),
    )
    path = tmp_path / "runtime" / "mysql.cnf"  # 父目录还不存在，脚本要自己建
    assert mysql_url.main(["--client-file", str(path)]) == 0

    assert path.read_text(encoding="utf-8").splitlines()[2] == "password=secretpw"
    if os.name == "posix":
        # 这一位是 `touch(mode=0o600)` 挣来的；去掉它就变成 0644，同一个学校的
        # 其他账号都读得到这台库的口令。
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "secretpw" not in capsys.readouterr().out


def test_an_unparsable_url_leaves_no_client_file(tmp_path, monkeypatch, capsys):
    """URL 有问题时**不写文件**：留下一个半成品会让 `mysqldump` 用上一次的旧口令。"""
    from app.core.config import Settings
    from app.db import mysql_url

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(_env_file=None, database_url="postgresql://root@127.0.0.1:5432/x"),
    )
    path = tmp_path / "mysql.cnf"
    assert mysql_url.main(["--client-file", str(path)]) == 1
    assert not path.exists()
    assert "只支持 MySQL" in capsys.readouterr().out
