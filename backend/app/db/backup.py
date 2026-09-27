r"""立刻备份一次数据库。`python -m app.db.backup`（CLAUDE.md §34）

**首行那个 `r` 不是装饰**：下面第 5 行有一个 Windows 路径 `C:\Program`，裸串里它是一个
非法转义序列——`py_compile` 会发 `DeprecationWarning: invalid escape sequence '\P'`，
而那个 warning 已经被 `test_ensure_schema.py` 的一条用例打印出来过一次（2026-09-27）。
它在未来的 Python 上会升级成 `SyntaxWarning`、再往后是语法错误，所以这一处按 raw string
写；**给这个文件加 docstring 时不要把这个 `r` 摘掉**。

**这是 `备份数据.bat` 现在真正调用的东西。** 在那之前整个备份逻辑住在 `ops.ps1` 里
（`Find-Mysqldump` + `Backup-Database`），而它有两个只有真机才暴露的毛病：带引号的
MySQL 服务路径被两行串联的 `-replace` 砍成 `C:\Program`，以及 mysqldump 报错时只抛
一句「退出码 N」、把它自己的 stderr 扔掉——操作员看到的是「窗口一闪就关，记不得是哪
一句」。逻辑搬进 Python 之后那两句都不存在了：路径解析归 `shlex`，mysqldump 的输出
进 `backup_record.message`（界面上看得见，日志里也有）。

它调的是**界面那枚按钮调的同一个函数**（`backup_service.run_backup`），所以命令行与
界面的行为不可能各说各话——包括并发：另一条路正在备份时这里拿到的是
`BACKUP_RUNNING`，不是两份同时写同一个目录。

退出码：0 = 成功；1 = 失败（原因已经打在这一行上）。**失败也要留一行记录**——
这正是这个功能存在的理由，见 `backup_service` 的模块 docstring。
"""

from __future__ import annotations

import argparse
import sys

from app.models.backup import TRIGGER_CLI
from app.services import backup_service


def run_once(*, dump_binary: str | None = None) -> int:
    """跑一次并提交。返回进程退出码。"""
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        try:
            record = backup_service.run_backup(
                session, trigger=TRIGGER_CLI, dump_binary=dump_binary
            )
        except Exception as exc:  # noqa: BLE001 —— 运维脚本，把话说清楚比抛栈有用
            # `BACKUP_RUNNING` 走这一支：那是**没跑**，不是失败，而且这一刻库里没有
            # 属于本次的行可写（另一条路正在跑）。所以这里只说一句，不落记录。
            print(f"[X] 备份没有执行：{backup_service.failure_message(exc)}")
            return 1
        # 记录与审计在同一个事务里：提交之后界面上才看得到这一行。
        session.commit()

        directory = backup_service.primary_directory(session)
        if record.status == backup_service.STATUS_SUCCEEDED:
            print(f"[OK] 备份完成：{directory / record.file_name}")
            print(f"     大小 {record.file_size} 字节")
            if record.message:
                # 「成功但有话要说」——目前只有第二路径复制失败那一类警告。
                print(f"     注意：{record.message}")
            return 0

        print(f"[X] 备份失败：{record.message or '未记录原因'}")
        print(f"     备份目录：{directory}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.db.backup",
        description="立刻备份一次数据库。备份目录与保留天数在「数据备份」页上配置。",
    )
    parser.add_argument(
        "--dump-binary",
        default=None,
        metavar="路径",
        help="用哪一个 mysqldump。默认按候选表自动找，一般不用传",
    )
    args = parser.parse_args(argv)

    try:
        return run_once(dump_binary=args.dump_binary)
    except Exception as exc:  # noqa: BLE001 —— 连不上库、库名不对这类，都要说人话
        print(f"[X] 备份失败：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
