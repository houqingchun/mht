"""把 `XLP_DATABASE_URL` 拆成 pymysql 能用的连接参数（CLAUDE.md §18）。

为什么不用 `sqlalchemy.engine.make_url`：这里要的三件事它只做两件。
`reset_to_baseline` 必须带着 `CLIENT.MULTI_STATEMENTS` 走**裸 pymysql**（一个文件里
三十多条语句，其中 `SET @admin_id := …` 是会话变量，必须全在同一条连接上），而
SQLAlchemy 的 mysql 方言自己算 `client_flag`，`connect_args` 里再塞一个能不能覆盖
它取决于版本。少一层猜测，多一层可测：这个模块是纯函数，`test_deploy_bootstrap.py`
逐条钉它。

**密码是百分号编码的，必须 `unquote` 回来。** `urlsplit` 只把 netloc 拆开，
**不解码** `username` / `password`（`p.password` 拿到的是 `pa%40ss`）。安装器写
URL 时用的是 `quote(pw, safe='')`，所以这里解码是配套的另一半；漏掉它的话，
一个含 `@` 的密码会以编码形态被送去认证，而 MySQL 报的是「Access denied」——
与「密码打错了」长得一模一样。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit

DEFAULT_PORT = 3306
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CHARSET = "utf8mb4"

# 库名要进 `CREATE DATABASE \`…\`` 的标识符位置，而那个位置只能靠反引号转义，
# 参数化占位符不适用（DDL 里没有值的位置）。所以先把形状收窄，只留 MySQL 允许的字符。
# 这条同时也是「安装时输入」那一屏的护栏：操作员手抖输进一个反引号，不会变成注入。
_FORBIDDEN_IN_DB_NAME = ("`", "\\", "/", ".")


class DatabaseUrlError(ValueError):
    """`XLP_DATABASE_URL` 有问题。消息是**给操作员看的**中文。"""


@dataclass(frozen=True)
class DatabaseTarget:
    user: str
    password: str
    host: str
    port: int
    database: str
    charset: str = DEFAULT_CHARSET
    # `?charset=` 之外的查询参数原样留着；SQLAlchemy 那条路要用完整 URL，这里用不上，
    # 但记下来免得「解析过一趟就把它们丢了」。
    extra_query: dict[str, str] = field(default_factory=dict)

    def connect_kwargs(self, *, with_database: bool = True) -> dict:
        """pymysql.connect 的参数。`with_database=False` 用于「库还不存在」时连服务端。"""
        kwargs: dict = {
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "charset": self.charset,
        }
        if with_database:
            kwargs["database"] = self.database
        return kwargs


def parse_database_url(url: str) -> DatabaseTarget:
    parts = urlsplit(url)

    backend = parts.scheme.split("+")[0]
    if not backend:
        raise DatabaseUrlError(f"看不出来用的什么数据库：{url!r}")
    if backend != "mysql":
        raise DatabaseUrlError(
            f"这套部署只支持 MySQL（`mysql+pymysql://…`），URL 里写的是 {parts.scheme!r}"
        )

    if not parts.hostname:
        # 空主机名不是「配错了」而是「就是本机」——安装时那一屏的默认值就是它。
        host = DEFAULT_HOST
    else:
        host = parts.hostname

    try:
        port = parts.port or DEFAULT_PORT
    except ValueError as exc:  # 端口位置写了个非数字
        raise DatabaseUrlError(f"端口不是一个数字：{exc}") from exc

    # `unquote` 不能省，见模块开头那段。用户名同理（虽然实际都是 root）。
    user = unquote(parts.username) if parts.username else ""
    password = unquote(parts.password) if parts.password else ""

    database = unquote(parts.path.lstrip("/"))
    if not database:
        raise DatabaseUrlError(
            "URL 里没有库名（形如 mysql+pymysql://user:pw@host:3306/库名）。"
            "安装脚本会在这一步把它填好，手动改过的话请检查结尾那一段。"
        )
    for bad in _FORBIDDEN_IN_DB_NAME:
        if bad in database:
            raise DatabaseUrlError(f"库名里不能出现 {bad!r}：{database!r}")

    query = parse_qs(parts.query, keep_blank_values=True)
    charset = (query.pop("charset", None) or [DEFAULT_CHARSET])[0]

    return DatabaseTarget(
        user=user,
        password=password,
        host=host,
        port=port,
        database=database,
        charset=charset,
        extra_query={k: v[0] for k, v in query.items()},
    )


def client_options_file(target: DatabaseTarget) -> str:
    """`mysqldump` / `mysql` 认的 `--defaults-extra-file` 的内容。

    Windows 上的「备份数据」用它，理由有两条，都不是洁癖：

    1. **口令不进命令行。** `mysqldump -p密码` 会把口令留在进程列表里（同一台机器上
       任何用户都能看到），而这是一个学校的服务器。
    2. **不要在 PowerShell 里再写一份 URL 解析器。** 上面那个函数已经是唯一出处，
       再抄一份的话，密码里带 `@` 的那种情况会在其中一边悄悄坏掉。

    `--defaults-extra-file` 要求调用方自己保证这个文件的权限；`ops.ps1` 把它写在
    `runtime\\` 下并立刻用 `icacls` 收窄，用完就删。
    """
    lines = [
        "[client]",
        f"user={target.user}",
        f"password={target.password}",
        f"host={target.host}",
        f"port={target.port}",
        f"default-character-set={target.charset}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.mysql_url",
        description="把 XLP_DATABASE_URL 里的连接参数写成 mysql 客户端认的配置文件。",
    )
    parser.add_argument(
        "--client-file",
        required=True,
        help="写到哪个文件（供 mysqldump / mysql 用 --defaults-extra-file 读）",
    )
    args = parser.parse_args(argv)

    from pathlib import Path

    from app.core.config import get_settings

    try:
        target = parse_database_url(get_settings().database_url)
    except DatabaseUrlError as exc:
        print(f"[X] {exc}")
        return 1

    path = Path(args.client_file)
    # 先收窄权限再写内容，中间那一瞬不存在「文件已存在且可读」的窗口。
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(client_options_file(target), encoding="utf-8")
    # 口令**不**打印：这个脚本的输出可能被写进日志。
    print(f"[OK] 连接参数已写入 {path}（用户名 {target.user}，库 {target.database}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
