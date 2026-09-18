"""把标准输出/标准错误固定成 UTF-8。安装器把它复制进**虚拟环境**的 `Lib\site-packages\`。

没有它会发生什么：Python 的输出接在**控制台**上时走 `WriteConsoleW`，中文与代码页
无关，看着一切正常；但安装脚本是用 `Start-Process -RedirectStandardOutput` **重定向到
文件**读回来的，那一路用的是 locale 编码——中文 Windows 上是 cp936（中文碰巧没事），
英文 Windows 上是 cp1252，那里我们自己的中文提示会当场抛 `UnicodeEncodeError`，
安装停在一个与编码毫无关系的位置，而日志里没有一句人话。

**为什么不能靠环境变量**（`PYTHONIOENCODING` / `PYTHONUTF8`）：2026-09-18 之前这套
部署用的是内嵌 CPython，它的 `python311._pth` 让 `getpath.py` 设了 `use_environment = 0`，
于是那两个变量**根本不起作用**（那时 `install.ps1` 里留着它们只是保险）。现在换成 venv，
它们**真的生效了**——但只对安装器那个进程和它起的子进程生效。用户之后双击
「启动服务.bat」时的环境由**他的登录会话**决定，局域网那条路则由 Task Scheduler 决定，
**两条路都不由我们决定**。而这是个「一次没生效、整份日志就变乱码」的东西，不能建立在
「别人的环境变量恰好对」上，所以安装脚本里那两行变量已经删了（见 `install.ps1` 第 4 条
注释）。别再加回来。

**为什么是 `sitecustomize.py`：** `site` 模块启动时会 import 同名的这个模块
（`site.execsitecustomize`）。这是 CPython 提供的唯一一个「每次解释器启动都跑一下」的
钩子，而且它**只认这个文件名**。好处是覆盖面：它同时管住 `python -m alembic`、
`python -c ...` 这些我们控制不到的入口，不必去改那些代码。

放进 `site-packages` 而不是别处：`site` 只在 `sys.path` 上找它，而 venv 的
`Lib\site-packages` 本来就在 `sys.path` 上。

**它的位置对重建很敏感**：`python -m venv --clear` 会把 `Lib\site-packages` 整个清掉，
所以安装器必须在建完 venv、装完依赖**之后**才能放它进来（次序见 `install.ps1` 第 2 步
开头）。反过来放，它会被下一次重建静默抹掉，直到英文版 Windows 上某句中文 `print`
抛 `UnicodeEncodeError` 才显形。

它是幂等的：对已经是 UTF-8 的流 `reconfigure` 是空操作。
"""

import sys


def _force_utf8(stream) -> None:
    if stream is None:
        return  # `pythonw.exe` 下没有 stdout/stderr，这是正常情况而不是错误
    try:
        # `errors="replace"`：万一将来真有编码不了的字符，宁可在日志里出现一个
        # `?`，也不要让整个安装为了一行提示崩掉。
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 —— 这个钩子绝不能成为解释器起不来的原因
        pass


_force_utf8(getattr(sys, "stdout", None))
_force_utf8(getattr(sys, "stderr", None))
