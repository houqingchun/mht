"""改一个账号的密码（默认 `admin`）。`python -m app.db.set_admin_password`

**这是唯一一条「管理员忘了密码」的出路。** 界面上的改密码要求本人先登录
（`auth_service.reset_password` 是「管理员给别的账号重置」那个动作），所以第一任
管理员的密码一旦丢了，这个部署就进不去了——而部署的人不是技术人员，也没有
`mysql` 客户端可以手改哈希。安装脚本在最后一步调它，把操作员自己定的那串写进去。

密码一律走 `auth_service.reset_password`，不在这里另写一份哈希：那一处同时是
`must_change_password = True`（首次登录必须自己改）与「解锁账号」的出处
（清 `failed_attempts` / `locked_until`）。抄一份的话，两个行为会各自漂移。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from app.models.account import UserAccount
from app.services import auth_service

MIN_PASSWORD_LENGTH = 6


def current_accounts(db) -> list[str]:
    """库里现有的账号名，用来在找不到目标时给一句有用的提示。"""
    return list(db.scalars(select(UserAccount.account).order_by(UserAccount.id)))


def set_password_in(db, account: str, password: str) -> bool:
    """在给定会话里改密码。找不到那个账号时返回 False（**不改任何东西**）。

    收一个会话进来是为了可测：这个函数是「忘了密码」这条路上唯一会写库的地方，
    而写错它的后果是「改完没人能登录」。测试拿 conftest 的内存库直接调它。
    """
    # 与 `authenticate` 的办法一致：同名的账号理论上可能不止一条（不同 account_type），
    # 这里取 id 最小的那条，与 `reset_to_baseline.sql` 里的 `ORDER BY id LIMIT 1` 同口径。
    user = db.scalar(
        select(UserAccount)
        .where(func.lower(UserAccount.account) == account.lower())
        .order_by(UserAccount.id)
    )
    if user is None:
        return False

    # 复用那一处实现：写哈希、置 must_change_password、清锁定计数，三件事一起。
    auth_service.reset_password(db, user, password)
    return True


def set_password(account: str, password: str) -> int:
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        if not set_password_in(session, account, password):
            names = current_accounts(session)
            print(f"[X] 找不到账号 {account!r}。库里现有：{'、'.join(names) or '（一个都没有）'}")
            return 1
        session.commit()
        print(f"[OK] 账号 {account} 的密码已更新。")
        print("     首次登录时系统会要求改成一个只有你知道的密码，这是正常的。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.set_admin_password",
        description="改一个账号的密码。不传 --password 就交互输入（输入的内容不回显）。",
    )
    parser.add_argument("--account", default="admin", help="账号名，默认 admin")
    parser.add_argument(
        "--password",
        default=None,
        help="密码。**不推荐**写在命令行里——它会留在进程列表与 shell 历史里",
    )
    parser.add_argument(
        "--password-env",
        default=None,
        metavar="变量名",
        help="从这个环境变量读密码。安装脚本走的是这一条：Windows 上任何用户都能读别的"
        "进程的命令行，读不到它的环境块",
    )
    args = parser.parse_args(argv)

    password = args.password
    if password is None and args.password_env:
        password = os.environ.pop(args.password_env, None)
        if not password:
            print(f"[X] 环境变量 {args.password_env} 是空的，没有改动。")
            return 1
    if password is None:
        password = getpass.getpass(f"为 {args.account} 设置新密码（至少 {MIN_PASSWORD_LENGTH} 位）：")
        again = getpass.getpass("再输一遍：")
        if password != again:
            print("[X] 两次输入不一致，没有改动。")
            return 1

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"[X] 密码至少 {MIN_PASSWORD_LENGTH} 位，没有改动。")
        return 1

    try:
        return set_password(args.account, password)
    except Exception as exc:  # noqa: BLE001 —— 运维脚本，把话说清楚比抛栈有用
        print(f"[X] 改密码失败：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
