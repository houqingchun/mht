"""建库（已经存在就什么都不做）。`python -m app.db.create_database`

为什么要这个脚本，而不是让操作员在 MySQL 里手动建一个库：**这台机器上不一定有
`mysql.exe` 在 PATH 里**（MySQL Installer 默认不勾「加入 PATH」），而安装的人不是
技术人员。走 pymysql 就不依赖任何 MySQL 客户端程序。

它**只建库**：表由 `alembic upgrade head` 建，基线行由 `python -m app.db.seed` 建
（CLAUDE.md §16 的同一条分工——schema 与基线行各有唯一出处，不在这里抄第二份）。

字符集定成 `utf8mb4` + `utf8mb4_0900_ai_ci`：库里存的是中文姓名与题干，而
`utf8mb4_0900_ai_ci` 是 MySQL 8.0 的默认排序规则（本机 8.4 上同样存在，两边一致）。
"""

from __future__ import annotations

import sys

import pymysql

from app.core.config import get_settings
from app.db.mysql_url import DatabaseTarget, DatabaseUrlError, parse_database_url

CHARSET = "utf8mb4"
COLLATION = "utf8mb4_0900_ai_ci"

# MySQL 的报错号 → 一句能照着办的处置。**不覆盖全部**：没列到的错误原样打出来，
# 猜错的处置比没有处置更费时间。
ERROR_HINTS = {
    2002: "连不上 MySQL（socket）：确认 MySQL 服务正在运行",
    2003: "连不上 MySQL：服务没起来，或者主机/端口填错了（默认 127.0.0.1:3306）",
    1045: "用户名或密码不对（MySQL 报的是 Access denied）。密码里的 @ : / # 等符号"
    "需要百分号编码，安装脚本会自动处理；手动改过 backend\\.env 的话请检查这一项",
    1049: "库不存在。选 1 装的话说明建库这一步被跳过了；选 2 / 选 3（库由你自己准备）"
    "的话就是那个库还没建出来，先建它，见《部署说明.txt》「数据库我自己准备」那一节",
    # ↓ 以下四条只会在「库由操作员自己准备」时出现（选 2 / 选 3）：那个 MySQL 账号
    #   是他自己开的，权限给到哪里、表建的是哪一版，安装器无从假设。
    1044: "这个账号没有那个库的权限（Access denied for user … to database）。"
    "请让 DBA 把这个库的权限授给这个账号，或者换一个有权限的账号重装",
    1142: "这个账号能连上库，但没有那几张表的权限（SELECT command denied）。"
    "权限可能只授到了库级的一部分表，请让 DBA 补齐",
    1050: "表已经存在。这几乎总是「这个库的表不是按这一版建的」——"
    "安装器在跑迁移前会校对这些表（`python -m app.db.ensure_schema`），"
    "它停下时已经把缺什么逐条念出来了；照那份清单改，别反复重跑",
    1054: "表里没有这一列。同上：这个库的表结构与这一版程序对不上，"
    "先跑 `python -m app.db.ensure_schema` 看它说缺什么",
    2059: "客户端认证插件不对：MySQL 8 的 caching_sha2_password 需要 cryptography 包",
}


def database_exists(target: DatabaseTarget) -> bool:
    # 连服务端而不连库：库还不存在时**连它本身就会失败**，所以检查必须先于建库。
    connection = pymysql.connect(**target.connect_kwargs(with_database=False))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                (target.database,),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()


def create_database_if_missing(target: DatabaseTarget) -> bool:
    """返回 True 表示这次真的建了。库名已经过 `parse_database_url` 的字符检查，
    这里再补一次反引号转义（标识符位置没有占位符可用）。"""
    if database_exists(target):
        return False
    name = target.database.replace("`", "``")
    connection = pymysql.connect(**target.connect_kwargs(with_database=False), autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET {CHARSET} COLLATE {COLLATION}")
    finally:
        connection.close()
    return True


def explain(exc: BaseException) -> str:
    """把一个 MySQL 的报错翻成一句可照办的中文。"""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    hint = ERROR_HINTS.get(code) if code else None
    if hint:
        return hint
    if isinstance(exc, RuntimeError) and "cryptography" in str(exc):
        # pymysql 在 caching_sha2_password 的**完整**认证路径上抛的就是这一条
        # （`_auth.py`）。它会在这台机器上第一次重启 MySQL 之后才出现——服务端那份
        # 凭据缓存是内存里的，重启就空了。详见 CLAUDE.md §18。
        return (
            "MySQL 要求 caching_sha2_password 的完整认证，而 cryptography 包没装上。"
            "这个包必须在 wheels\\ 里（安装脚本会解压它），缺了就只有「MySQL 从没重启过」"
            "的机器连得上"
        )
    return f"建库失败：{type(exc).__name__}: {exc}"


def main() -> int:
    try:
        target = parse_database_url(get_settings().database_url)
    except DatabaseUrlError as exc:
        print(f"[X] {exc}")
        return 1

    try:
        created = create_database_if_missing(target)
    except (pymysql.err.MySQLError, RuntimeError, OSError) as exc:
        print(f"[X] {explain(exc)}")
        return 1

    where = f"{target.host}:{target.port}/{target.database}"
    if created:
        print(f"[OK] 已创建数据库 {target.database}（{CHARSET} / {COLLATION}，位置 {where}）")
    else:
        print(f"[OK] 数据库 {target.database} 已存在，没有改动（位置 {where}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
