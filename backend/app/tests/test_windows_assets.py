"""把守 `deploy/windows/` 那几份 Windows 侧资产的编码与对应关系。

那一整个目录里的东西**在开发机上一行都不会被执行**——它们要到一所学校的
Windows 服务器上才第一次被 cmd.exe 和 PowerShell 5.1 读。所以那里每一条
约定都需要机器判据，而不是写在头注释里等读者自觉：

1. **`.ps1` / `.txt` 必须有 UTF-8 BOM。** PS 5.1 只有看到 BOM 才按 UTF-8 读一个
   脚本；没有它时按当前 ANSI 代码页（简中是 cp936）读，满屏中文提示变成乱码。
   更糟的是 cp936 下一个中文字符的尾字节会把它后面那个 ASCII 字符一起吃掉——
   于是 `"` 或 `)` 消失，报出来的是一句与编码毫无关系的语法错误，位置还指在别处。
   这条守卫不是假想的：本文件写下的同一次改动里，编辑工具就把 `install.ps1` 的
   BOM 抹掉过一次（`deploy/build_package.py` 的自检也查这条，这里是更早的一道）。

2. **`.bat` 不许有 BOM，且每个字节都必须 < 128。** cmd.exe 按当前代码页逐行解 .bat，
   BOM 会让第一行 `@echo off` 变成一句乱码命令；中文在这里同样有上面那个「吞掉
   后一个字符」的问题。文件名可以是中文（那是文件名，不是内容），内容不行。

3. **八个按钮与 `ops.ps1` 的 `-Action` 一张不多一张不少。** 这是跨文件的契约：
   加一个动作时忘了配按钮，症状是「双击那个 .bat 什么也没发生」（PowerShell 报
   ValidateSet 之外的值，窗口一闪而过）；删一个动作时忘了删按钮，症状一样。
   两个方向各断言一次，只查一个方向挡不住另一半。

4. **按钮 .bat 用的是 `%~dp0`，不是 `%~dp0..`。** `install.ps1` 把它们拷到**安装根
   目录**然后删掉 `deploy\\ops\\`（一个按钮只留一份），而 `%~dp0` 跟着文件本身走。
   写成 `..` 的那一版只在「从 `deploy\\ops\\` 里双击」时才成立——那正是它不再存在
   的那个位置。

5. **`install.ps1` 里跑外部命令的那一处，退出码语义要逐个工具判。** 2026-09-18 加这一组：
   安装在一台 Windows 上停在第 2 步，报「复制程序文件 失败（退出码 1）」——而 `robocopy`
   的退出码是**位掩码**，`1` 是「成功复制了文件」（全新安装必然是这个），只有 `8` / `16`
   才是失败。文件其实已经铺完，脚本却在第 2 步抛出并 `exit 1`。

   这个错误能活到那天，正是因为**没有任何机器判据看得见它**：`build_package.py` 的自检
   只看文件在不在、编码对不对，而本文件在此之前**从不读 `install.ps1` 的正文**。
   下面那几条只断「调用点声明了自己的成功码、原生输出没按 UTF-8 解」——它们**证明不了**
   robocopy 的语义（那要真机），只能挡住「有人把它改回去」。

6. **`.env` 的值只有一个出处，而且升级路径要验它、不假设它是对的。** 2026-09-18 同一台
   机器上紧随其后的一次失败：安装停在第 4 步，
   `ValidationError: 1 validation error for Settings / port / Input should be a valid integer
   … [type=int_parsing, input_value='', input_type=str]`——`backend\\.env` 里那一行
   `XLP_PORT=` 是空的，而当时**安装器一个字都没验过它**（只在里面抠了个端口号给防火墙用），
   一路把这份坏配置带到了 `import app.db.session` 那一刻。下面那一组钉住这之后的形状：
   值表唯一、升级就地补坏行、补不动的那一项回来问人、任何一步都不把口令或密钥写进日志。
   详见 CLAUDE.md §18 与 `deploy/README.md`。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

WINDOWS_ASSETS = Path(__file__).resolve().parents[3] / "deploy" / "windows"
OPS_SCRIPT = WINDOWS_ASSETS / "ops.ps1"
INSTALL_SCRIPT = WINDOWS_ASSETS / "install.ps1"
OPS_DIR = WINDOWS_ASSETS / "ops"
# `backend/`：`config-show` 那个探针要用它当工作目录（`.env` 是**相对当前工作目录**的）。
BACKEND_DIR = Path(__file__).resolve().parents[2]

BOM = b"\xef\xbb\xbf"

# `ops.ps1` 里那个**给 `$Action` 用的** `[ValidateSet(...)]` 是动作清单的唯一出处。
#
# 按参数名定位而不是「第一个 ValidateSet」：这个文件里还有一个给日志级别用的
# `ValidateSet('INFO','OK','WARN','ERROR')`，靠「它在前面」来区分是脆的——
# 谁把 `Write-Line` 挪到参数块上面，这条就会去解析错误的那一个，而且**照样是绿的**。
_VALIDATE_SET_RE = re.compile(
    r"\[ValidateSet\(([^)]*)\)\]\s*\n\s*\[string\]\$Action", re.MULTILINE
)
_ACTION_RE = re.compile(r"-Action\s+([a-z][a-z-]*)")


def asset_files(suffix: str) -> list[Path]:
    return sorted(path for path in WINDOWS_ASSETS.rglob(f"*{suffix}") if path.is_file())


def test_the_file_tree_is_actually_there():
    """先证明有东西可扫——两个 glob 都扫空的话，下面每一条都恒真。"""
    assert OPS_SCRIPT.is_file(), OPS_SCRIPT
    assert len(list(OPS_DIR.glob("*.bat"))) == 8, "八个按钮"
    assert len(asset_files(".ps1")) >= 2, "install.ps1 与 ops.ps1"
    assert (WINDOWS_ASSETS / "部署说明.txt").is_file()


def test_powershell_and_text_assets_carry_a_bom():
    offenders = [
        str(path.relative_to(WINDOWS_ASSETS))
        for path in asset_files(".ps1") + asset_files(".txt")
        if not path.read_bytes().startswith(BOM)
    ]
    assert not offenders, f"缺 UTF-8 BOM（PS 5.1 会按 ANSI 读，中文全乱）：{offenders}"


def test_the_bom_is_exactly_one_and_nowhere_else():
    """**「有个 BOM」与「只有一个 BOM」是两件事**，而只查前三个字节分不出来。

    2026-09-18 实测过：`install.ps1` 的开头堆了**四个** BOM（前 16 字节
    `EF BB BF` × 4），而两条既有的守卫都说它是好的——`startswith(BOM)` 看的是
    前缀，出包时的自检也是前缀，所以它一路混进了交付包。

    怎么来的值得记：一次变异验证的脚本把基准读成 `read_bytes().decode("utf-8")`
    （少了 `-sig`），于是那个 U+FEFF 留在字符串里，而复原时又写了一遍 `BOM + 文本`
    ——跑一次多一个。「复原 ✅」当时也报了绿，因为它比的是**内存里那一份基准**，
    而那一份本身已经带上了 U+FEFF，等于拿嫌疑人的证词给嫌疑人作证。

    四个 BOM 的 install.ps1 大概仍然能跑（PS 把 U+FEFF 当空白），但那是运气：
    它是一次**无声的改写**留下的印子，而同一个机制下一次改的可能是别的东西。
    """
    for path in asset_files(".ps1") + asset_files(".txt"):
        relative = str(path.relative_to(WINDOWS_ASSETS))
        raw = path.read_bytes()

        leading = 0
        while raw[3 * leading : 3 * leading + 3] == BOM:
            leading += 1
        assert leading == 1, (
            f"{relative} 开头有 {leading} 个 BOM——只许一个。多出来的那个多半是"
            "某次脚本「读的时候没剥、写的时候又补」留下的，查一下是谁写的它"
        )

        total = raw.decode("utf-8").count("﻿")
        assert total == 1, (
            f"{relative} 全文有 {total} 个 U+FEFF——除了开头那一个，别处不许有"
            "（夹在正文里的 U+FEFF 可能吃掉紧跟的那个字符，而它看起来与空白一样）"
        )


def test_batch_files_are_pure_ascii_without_a_bom():
    problems = []
    for path in asset_files(".bat"):
        relative = str(path.relative_to(WINDOWS_ASSETS))
        raw = path.read_bytes()
        if raw.startswith(BOM):
            problems.append(f"{relative} 有 BOM")
        for number, line in enumerate(raw.decode("ascii", "replace").splitlines(), 1):
            if any(ord(char) > 127 for char in line):
                problems.append(f"{relative}:{number} 非 ASCII")
    assert not problems, problems


def declared_actions() -> list[str]:
    match = _VALIDATE_SET_RE.search(OPS_SCRIPT.read_text(encoding="utf-8-sig"))
    assert match, "ops.ps1 里给 $Action 用的 [ValidateSet(...)] 找不到——动作清单的唯一出处就是它"
    return [item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()]


def wired_actions() -> dict[str, str]:
    """按钮 -> 它调的动作。键是文件名，值是 `-Action` 那一个词。"""
    wired = {}
    for path in sorted(OPS_DIR.glob("*.bat")):
        found = _ACTION_RE.findall(path.read_text(encoding="ascii"))
        assert len(found) == 1, f"{path.name} 里的 -Action 有 {len(found)} 个，期望恰好 1 个"
        assert found[0] not in wired.values(), f"{found[0]} 有两个按钮"
        wired[path.name] = found[0]
    return wired


def test_every_declared_action_has_exactly_one_button():
    declared = set(declared_actions())
    wired = set(wired_actions().values())
    assert declared == wired, (
        f"动作与按钮对不上——只在 ops.ps1 里声明、没有按钮的：{sorted(declared - wired)}；"
        f"有按钮、ops.ps1 不认的：{sorted(wired - declared)}"
    )


def test_action_names_are_stable():
    """把清单本身钉住。

    前一条比的是「两边一致」，两边**一起**改错（比如把 `reset-admin` 改成
    `resetadmin`）它照样是绿的。而名字是已经在用的东西：按钮文件名、
    `deploy/README.md`、以及操作员手上的那份说明都指着它们。
    """
    assert declared_actions() == [
        "start",
        "stop",
        "restart",
        "status",
        "log",
        "backup",
        "reset-admin",
        "uninstall",
    ]


def test_buttons_resolve_their_paths_from_their_own_location():
    """`%~dp0`（安装根），不是 `%~dp0..`（`deploy\\ops\\`）。

    见模块 docstring 第 4 条：按钮被拷到安装根目录之后，`..` 指向的是安装目录的
    上一级，于是 `deploy\\ops.ps1` 找不到，报出来的是「系统找不到指定的路径」——
    与「装坏了」长得一模一样。
    """
    for path in sorted(OPS_DIR.glob("*.bat")):
        text = path.read_text(encoding="ascii")
        assert 'cd /d "%~dp0"' in text, f"{path.name} 没有 cd 到自己的目录"
        assert '"%~dp0deploy\\ops.ps1"' in text, f"{path.name} 没指向 %~dp0deploy\\ops.ps1"
        assert "%~dp0.." not in text, f"{path.name} 里还有 %~dp0.."


# ---------------------------------------------------------------- install.ps1 正文
#
# 见模块 docstring 第 5 条：安装中断的那个 bug 之所以能活下来，正是因为在此之前
# **没有任何机器判据读过 install.ps1 的正文**。


def install_text() -> str:
    return INSTALL_SCRIPT.read_text(encoding="utf-8-sig")


def function_body(text: str, name: str) -> str:
    """把一个 `function NAME {` 的函数体切出来，到**列 0 的那个 `}`** 为止。

    刻意不做花括号配平：这个文件里有 here-string 内嵌的 Python，里面有 `{` 与 `}`，
    配平要写一个半吊子的词法器；而它数错的时候是**静默地多切或少切**——少切的那半截
    里的东西就全都不受下面几条断言约束了，测试照样绿。

    这个文件里函数的收尾 `}` 一律在列 0（PowerShell 的通行写法），所以「列 0 的 `}`」
    是可靠的边界。
    """
    start = re.search(rf"^function {re.escape(name)} \{{", text, re.MULTILINE)
    assert start, f"这个文件里找不到 function {name}（改名或删掉了？）"
    tail = text[start.end():]
    end = re.search(r"^\}", tail, re.MULTILINE)
    assert end, f"function {name} 没有以列 0 的 }} 收尾"
    return tail[: end.start()]


_BLOCK_COMMENT_RE = re.compile(r"<#.*?#>", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"^[ \t]*#.*$", re.MULTILINE)


def code_only(text: str) -> str:
    """去掉块注释与整行注释，只留**会被执行**的那些行。

    断言「这一处有没有用某个写法」时不能连注释一起算：`Read-NativeOutput` 的注释里
    写着「**不能用 `-Encoding UTF8`**」，而 `Invoke-Native` 的注释里指着它说明两条路
    各用各的编码——把注释算进去，这两条守卫会**因为文档写得对而变红**。
    改注释去迁就测试是更糟的方向：那几句正是下一个人需要读到的说明。

    只去**整行**注释，不去行尾的 `#`：这个文件里有内嵌 Python 的 here-string，
    按行尾切会把字符串里的井号当注释切开，切出什么都不好说。
    """
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))


def native_calls(text: str) -> list[str]:
    """每个 `Invoke-Native` **调用点**的原文（`function Invoke-Native {` 那一行不算）。

    `^[ \\t]*Invoke-Native` 天然排除定义行——那一行以 `function ` 打头。
    切到下一个空行为止：调用点之间就是靠空行分段的。
    """
    calls = []
    for match in re.finditer(r"^[ \t]*Invoke-Native\b", text, re.MULTILINE):
        tail = text[match.start():]
        blank = re.search(r"\n[ \t]*\n", tail)
        calls.append(tail[: blank.start()] if blank else tail)
    return calls


def declared_success_codes(call: str) -> set[int] | None:
    """调用点声明的成功退出码。两种字面写法都认，认不出返回 `None`。"""
    ranged = re.search(r"-SuccessExitCodes\s+\((\d+)\.\.(\d+)\)", call)
    if ranged:
        return set(range(int(ranged.group(1)), int(ranged.group(2)) + 1))
    listed = re.search(r"-SuccessExitCodes\s+@\(([^)]*)\)", call)
    if listed:
        return {int(part) for part in re.findall(r"\d+", listed.group(1))}
    return None


def test_there_are_calls_to_scan():
    """先证明有东西可扫——上面那两个 helper 扫空的话，下面每一条都恒真。"""
    calls = native_calls(install_text())
    assert len(calls) == 3, f"Invoke-Native 的调用点期望 3 个，实际 {len(calls)} 个"


def test_native_commands_declare_their_own_success_codes():
    """失败判据是一张**声明出来的**成功码表，不是写死的 `-ne 0`。

    `robocopy` 的退出码是位掩码：`0..7` 全是成功，`1` = 成功复制了文件（全新安装必然是
    它），只有 `8` / `16` 是失败。`icacls` 那两处是 0 / 非 0，所以默认值 `@(0)` 让它们
    的行为与改动前一字不变。
    """
    body = code_only(function_body(install_text(), "Invoke-Native"))
    assert "[int[]]$SuccessExitCodes" in body, "Invoke-Native 没有 $SuccessExitCodes 参数"
    # 顺序是 `$成功码表 -notcontains $退出码`，不是反过来——写这条断言时先写反过一次。
    assert "$SuccessExitCodes -notcontains" in body, "失败判据没有用成功码表来判"
    assert not re.search(r"\$process\.ExitCode\s+-ne\s+0", body), (
        "失败判据又变回写死的 -ne 0 了——这正是让安装停在第 2 步的那个写法"
    )


def test_every_robocopy_call_site_declares_the_bitmask_codes():
    """每个跑 `robocopy.exe` 的调用点都必须显式声明成功码，且覆盖 `0..7`。

    只断 `Invoke-Native` 的定义挡不住「新增了第二个 robocopy 调用点、忘了传」。
    """
    offenders = []
    checked = 0
    for call in native_calls(install_text()):
        if "robocopy.exe" not in call:
            continue
        checked += 1
        codes = declared_success_codes(call)
        if codes is None:
            offenders.append("没有 -SuccessExitCodes（可写 `(0..7)` 或 `@(0,1,…,7)`）")
        elif not set(range(0, 8)) <= codes:
            offenders.append(f"成功码 {sorted(codes)} 没覆盖 0..7")
    assert checked == 1, f"跑 robocopy 的调用点期望 1 个，实际 {checked} 个"
    assert not offenders, f"robocopy 调用点：{offenders}"


def test_native_output_is_read_in_the_console_code_page():
    """原生工具（robocopy / icacls）的输出按**控制台代码页**解，不是 UTF-8。

    这些工具重定向到文件时写的是控制台代码页（中文 Windows = cp936），只有
    `WriteConsoleW` 那条路才是宽字符。按 UTF-8 解会把每个中文字变成 U+FFFD，而且
    **不可逆**——写进日志的已经是替换字符，原始字节当场就丢了。于是那句「上面是它
    自己说的话」变成一屏 `�ѳɹ�����`，而操作员唯一能提供的东西就是这份日志。
    """
    text = install_text()
    body = code_only(function_body(text, "Invoke-Native"))
    assert "Read-NativeOutput" in body, "Invoke-Native 没有走 Read-NativeOutput"
    assert "-Encoding UTF8" not in body, "原生输出的那一路又按 UTF-8 读了"

    reader = code_only(function_body(text, "Read-NativeOutput"))
    assert "[Console]::OutputEncoding" in reader, "Read-NativeOutput 没按控制台代码页解"
    assert "-Encoding UTF8" not in reader


def test_python_output_is_still_read_as_utf8():
    """反方向：Python 子进程那一路**必须**保持 UTF-8。

    只断上面那一条的话，把两处一起改成控制台代码页照样是绿的——而那会让
    `sitecustomize.py` 固定成 UTF-8 的 stdout 在英文版 Windows 上变成乱码
    （cp1252 上这些脚本里的中文会让 `print` 直接抛 UnicodeEncodeError）。
    两条路各有各的编码，不要互相看齐。
    """
    body = code_only(function_body(install_text(), "Invoke-Python"))
    assert "stdout.txt" in body, "这一段不是读 Python 输出的那个函数"
    assert "-Encoding UTF8" in body, "Python 那一路的 UTF-8 被改掉了"


def test_the_elevated_child_writes_to_the_same_log():
    """提权出来的子进程要写**同一份**日志。

    不传的话子进程从第 1 行重跑、按当时的时间重新算一个文件名，于是 `%TEMP%` 里留下
    两份 `xlp-install-*.log`：父进程那份只有表头（它打印完「日志文件：…」就 `exit 0`），
    真日志在子进程那份。操作员照开头那句话去拿，拿到的是几乎空白的那一份。
    """
    text = install_text()
    assert re.search(r"\[string\]\$LogPath", text), "param() 里没有 $LogPath"

    relaunch = re.search(r"Start-Process -FilePath 'powershell\.exe' -Verb RunAs", text)
    assert relaunch, "找不到提权重启那一处"
    window = text[max(0, relaunch.start() - 1500): relaunch.end()]
    assert "-LogPath" in window, "提权时没把日志路径传给子进程"


# ---------------------------------------------------------------- 「用法」这一维
#
# 2026-09-18 加。安装时多问一句「这台电脑只有你自己用，还是老师们都会连过来？」，
# 按回答走两条形状：`single`（本机、不提权、不常驻）与 `lan`（原来的形状，一字未变）。
#
# **这一组全是文本断言，挡的是「有人把保证改回去」，挡不住「PowerShell 逻辑写反了」**
# ——比如把 `if ($Usage -eq 'lan')` 写成 `-ne`，`$Usage` 确实出现了，断言照样绿。
# 真正能证伪的只有真机（`deploy/README.md` 末尾那几步）。别把它读成一层更强的保证。


def ops_text() -> str:
    return OPS_SCRIPT.read_text(encoding="utf-8-sig")


def braced_block(text: str, header: str) -> str:
    """切出一个 `if (...) {` 的块体，到**同一缩进**的那个 `}` 为止。

    与 `function_body` 同一套边界约定（见它的 docstring：刻意不做花括号配平）。
    按缩进找收尾而不是写死列 0：`install.ps1` 里这个守卫既出现在主流程（4 空格）里，
    也可能是嵌套的（8 空格）。
    """
    start = re.search(rf"^([ \t]*){re.escape(header)} \{{", text, re.MULTILINE)
    assert start, f"找不到 {header} {{"
    indent = start.group(1)
    tail = text[start.end():]
    end = re.search(rf"^{re.escape(indent)}\}}", tail, re.MULTILINE)
    assert end, f"{header} 没有以同缩进的 }} 收尾"
    return tail[: end.start()]


def enclosing_usage_guard(text: str, needle: str) -> str:
    """`needle` 前面**最近的**那一个 `if ($Usage -eq 'lan') {` 的块体。

    同一个文件里有好几处同样的守卫，所以「文件里存在某处用法分支」是不够的——
    要问的是**这一处调用**被哪一个包着。从 `needle` 往回按行找，最近的那个才对。
    返回的块体里**必须**还含得下 `needle`，否则它其实在那个守卫之外。
    """
    index = text.index(needle)
    lines = text.splitlines()
    # `needle` 那一行在 `lines` 里的序号——往回找守卫要从它开始。
    # 用 `text.splitlines()` 而不是 `text[:index].splitlines()` 建 body：块体要**含下**
    # needle 以及它后面那个收尾的 `}`，截到 needle 就永远找不到收尾（第一版就是这么错的）。
    first = len(text[:index].splitlines()) - 1
    for number in range(first, -1, -1):
        line = lines[number]
        if line.strip() != "if ($Usage -eq 'lan') {":
            continue
        indent = line[: len(line) - len(line.lstrip())]
        body = "\n".join(lines[number:])
        end = re.search(rf"^{re.escape(indent)}\}}", body, re.MULTILINE)
        assert end, f"{needle} 前面那个用法守卫没有收尾"
        block = body[: end.start()]
        assert needle in block, f"{needle} 不在那个用法守卫里（守卫在它之前就闭合了）"
        return block
    raise AssertionError(f"{needle} 没有被任何 `if ($Usage -eq 'lan')` 包住")


def test_the_two_usages_are_actually_branched_on():
    """先证明有东西可扫——上面两个 helper 找不到守卫的话，下面每一条都恒真。"""
    install = install_text()
    assert install.count("if ($Usage -eq 'lan') {") >= 3, (
        "install.ps1 里的用法分支少于 3 处（提权、安装目录权限、计划任务与防火墙）"
    )
    assert "if ($Settings.usage -eq 'single')" in install, "Write-EnvFile 没有按用法选监听地址"
    assert "$Usage = Get-InstalledUsage" in ops_text(), "ops.ps1 没有读出用法"

    # 「探一探哪个默认目录装过」那一步里，`Get-SingleDefaultDir` **不能直接内联**。
    # 它在两个环境变量都没有时走 `Stop-WithError`，而那是 **`exit 1`，不是 `throw`**
    # ——外层的 `try/catch` 接不住它，一次「只想按局域网装」的安装会因为读不到
    # `%LOCALAPPDATA%` 而整个中止。要先算进一个变量、算不出来就当这个候选不存在。
    body = code_only(function_body(install_text(), "Resolve-Usage"))
    assert "$singleDir = Get-SingleDefaultDir" in body, (
        "Resolve-Usage 没有单独算单机那个候选目录"
    )
    assert "usage = 'single'; dir = (Get-SingleDefaultDir)" not in body, (
        "Get-SingleDefaultDir 又内联进候选数组了——它的 Stop-WithError 是 exit 1，"
        "会让局域网那条路也一起中止"
    )


def test_elevation_is_lan_only():
    """提权**只在局域网用法下**发生。

    单机用法的全部价值就在于「不弹 UAC」：它不动计划任务、不动防火墙、程序装在用户
    自己的 profile 下，没有任何一件需要管理员的事。这条一破，用户双击之后照样会撞上
    一个授权窗口，而那个窗口会把他以为「不需要管理员」的预期推翻。

    这条**不是**在说提权块可以删——上面 `test_the_elevated_child_writes_to_the_same_log`
    硬找 `-Verb RunAs`，局域网那条路还要靠它。
    """
    block = code_only(braced_block(install_text(), "if ($Usage -eq 'lan' -and -not (Test-Administrator))"))
    assert "-Verb RunAs" in block, "提权不在「局域网用法」这个分支里"
    # 只看**代码**：那行说明里写着 `Resolve-Usage`，按裸文本断言时它就会满足
    # 「传了 `-Usage`」——这条守卫第一次写出来就是这么假的绿。
    assert "$arguments += @('-Usage', $Usage)" in block, (
        "提权时没把用法传给子进程——子进程会重跑 Resolve-Usage，那次可能探到别的答案"
    )


def test_the_single_usage_env_file_binds_to_loopback():
    """`.env` 里的监听地址由**用法**写，不是一个留给操作员手工改的注释。

    写死 `0.0.0.0` 的话，「只有我自己用」的那台机器上服务会把整个局域网都收进来——
    这正是用户这次要消掉的那个形状。两个字面量必须**都在**：说明它是选出来的，
    而不是碰巧写对了一个。

    2026-09-18 起这两个字面量住在 `Get-EnvWantedValues`（`Write-EnvFile` 与
    `Repair-EnvFile` 共用的那一份值表），不再在 `Write-EnvFile` 里——所以判据跟着搬家。
    留在原处的话，这条守卫会因为「值表搬了个家」而变红，而红的原因不是功能坏了
    （下面 `test_the_env_values_have_exactly_one_source` 钉那个形状本身）。
    """
    body = code_only(function_body(install_text(), "Get-EnvWantedValues"))
    assert "$Settings.usage -eq 'single'" in body, "监听地址没有按用法分支"
    assert "'127.0.0.1'" in body, "单机用法那一支不见了"
    assert "'0.0.0.0'" in body, "局域网用法那一支不见了"


def test_the_env_permissions_keep_the_installing_user_in():
    """`.env` 收紧权限时，**单机用法必须把当前用户自己授进去**。

    这是这次改动最容易埋进去的坑：`/inheritance:r /grant:r SYSTEM:F Administrators:F`
    之后，UAC 下普通用户的 token 里 Administrators 是 deny-only，**装它的那个人自己
    读不了这个文件**。而 `python.exe` 读不到 `.env` 不报错，是 pydantic-settings 静默
    回退全默认值，去连 `root:password@localhost`——与操作员刚填的那一份毫无关系。
    现象是「服务起得来但连不上你填的那个库」。
    """
    body = code_only(function_body(install_text(), "Tighten-EnvPermissions"))
    assert "[string]$Usage" in body, "Tighten-EnvPermissions 没有收用法参数"
    assert "$Usage -eq 'single'" in body, "没有按用法分支"
    assert "Identity]::GetCurrent()" in body, (
        "没拿到当前用户——用 `$env:USERNAME` 在微软账户/域账户下不是一个 icacls 认得出的名字"
    )
    assert "$owner + ':F'" in body, "当前用户没有出现在授权列表里（这一支等于没写）"
    assert "Tighten-EnvPermissions -Path $envFile -Usage $Usage" in install_text(), (
        "调用点没把用法传进去——那就是永远走局域网那一支"
    )


def test_task_and_firewall_are_lan_only():
    """计划任务与防火墙规则**只有局域网用法才建**。

    它们是这一维里唯一两件「系统级」的改动，也正是单机用法不需要管理员的原因。
    建出来的后果不是报错，是留下一台「看着是单机、实际上开机自启 + 端口对全网段开放」
    的机器——而装的人以为自己选的是「只有这台电脑用」。
    """
    # `enclosing_usage_guard` 自己就会断言「这一处调用确实在那个守卫**里面**」，
    # 且找不到守卫时抛 AssertionError，所以循环本身就是这条用例的内容。
    for call in ("Register-ServiceTask -Settings $settings", "Add-FirewallRule -Port $settings.port"):
        enclosing_usage_guard(install_text(), call)


def test_the_installer_records_the_usage_it_used():
    """装完要**把用法记下来**，而且读不到时回退 `lan`。

    `runtime\\build.json` 的 `usage` 是 `ops.ps1` 唯一的判据（它不看 `.env`、也不看
    安装目录长什么样）。字段缺了、文件坏了、值认不出，一律当 `lan`——那个字段是
    2026-09-18 才有的，此前装出来的包全是局域网那一种。回退成 `single` 会让那些
    服务器上的 start / stop 改用「直接起进程」的方式，服务从此再也起不来，而
    「查看状态」还会说一句「单机用法：没有计划任务」，看起来像是设计。
    """
    assert re.search(r'"usage": "', install_text()), "build.json 模板里没有 usage 字段"

    text = ops_text()
    body = code_only(function_body(text, "Get-InstalledUsage"))
    assert "-notcontains 'usage'" in body, "Get-InstalledUsage 没有判字段在不在"
    assert "$json.usage -eq 'single'" in body, "Get-InstalledUsage 没有只认 single"
    assert body.rstrip().endswith("return 'lan'"), (
        "Get-InstalledUsage 的最后一句必须是回退 lan——它现在的收尾是：" + body.rstrip()[-40:]
    )

    # 名字不能再变回 `Get-Usage`：`install.ps1` 里那个 `Resolve-Usage` 回答的是「这次要
    # 按哪种用法装」，是**决定**；这一个回答的是「当初装成了哪种」，是**读取**。
    # 两个文件各有一个 `Get-Usage` 时，改错地方不会有任何东西报错。
    assert not re.search(r"^function Get-Usage \{", text, re.MULTILINE), (
        "ops.ps1 里又出现了一个叫 Get-Usage 的函数——它与 install.ps1 那个是两回事，"
        "请叫 Get-InstalledUsage"
    )


def test_the_status_page_names_the_database_mode_only_when_it_knows_it():
    r"""「查看状态」要把「数据库是谁准备的」用中文说一遍，**认不出就不说**。

    这一项在那一页上的诊断价值特别高：装的时候选了「库我自己准备」的机器上，
    「页面打不开」的头号原因不是密码错、也不是 MySQL 没起来，而是**那个库还没准备好**
    （库、表、基础数据三样里缺一样）——而这一件事在 `backend\.env` 里看不出来，
    两种装法的 `.env` 一字不差。原始 JSON 就印在上面，可它对非技术操作员等于没说
    （上面 `Show-Status` 里那句注释是同一个理由）。

    **回退方向与 `Get-InstalledUsage` 正好相反**，这是这条守卫最要紧的一半：用法猜错
    会让服务起不来（所以必须挑一个「此前唯一存在过的那条路」= `lan`），而这一项只多说
    一句中文——猜一个值出来是把「不知道」说成「知道」，而这一页存在的全部意义就是让
    操作员相信上面写的字。所以它返回空串，两个分支都走不到，屏幕上什么都不出现。

    **判据与上一条（`Get-InstalledUsage`）不同形状，这不是随手的**：那一条能写
    `body.rstrip().endswith("return 'lan'")`，因为它的**最后一行**就是那个回退；这一个
    的最后一行是 `return [string]$json.database_mode`（正常路径），而「不猜一个值」
    说的是**每一个写死的返回值**——所以判据是「`return '…'` 抓出来的全是空串」。
    写成 `endswith` 会是错的，第一版就是这么写的，一跑就红。

    变异验证 8/8：摘掉 `-notcontains`、两条兜底改成 `'installer'`、末尾加一条猜的值、
    绕开 `Get-BuildJson`、读回来不赋值、第二支写成 `else`、两个中文分支各删一条。
    """
    text = ops_text()

    body = code_only(function_body(text, "Get-InstalledDatabaseMode"))
    assert "-notcontains 'database_mode'" in body, (
        "`Get-InstalledDatabaseMode` 没有判字段在不在——老包留下的 build.json 里没有这一项"
    )
    returns = re.findall(r"return '([^']*)'", body)
    assert returns and all(one == "" for one in returns), (
        "`Get-InstalledDatabaseMode` 里每一个写死的返回值都必须是空串（不猜一个值）——"
        "它现在写死了这些：" + repr(returns)
    )
    assert "Get-BuildJson" in body, "没有走 `Get-BuildJson`（那一层的注释要求只在那里解析）"
    assert "$DatabaseMode = Get-InstalledDatabaseMode" in text, (
        "读了却没有取一次——`Show-Status` 里那个 `$DatabaseMode` 会是未定义变量"
    )

    status = code_only(braced_block(text, "function Show-Status"))
    assert "'  数据库：你自己准备的那一个（安装时没有建库、没有灌基础数据）'" in status, (
        "「查看状态」不再用中文说「这一次是操作员自己准备的库」"
    )
    assert "'  数据库：安装时由「一键安装」准备好（建库、灌基础数据、设管理员密码）'" in status, (
        "「查看状态」不再说「这一次是安装器准备的库」"
    )
    # 两个分支必须都是「值相等」才进——写成 `} else {` 会把「不知道」说成
    # 「安装器准备的」，而那一句恰恰是操作员会照着做事的话。
    assert re.search(
        r"if \(\$DatabaseMode -eq 'prepared'\) \{.*?\} elseif \(\$DatabaseMode -eq 'installer'\) \{.*?\}",
        status,
        re.S,
    ), (
        "两个分支的形状变了——认不出的值必须一个分支都走不到（写成 `else` 就会把"
        "「不知道」说成「安装器准备的」）"
    )


def test_start_and_stop_check_the_port_as_a_second_criterion():
    """起停两头都要**再问一次端口**，不能只信「我看得见的那个 python.exe」。

    `Get-ServiceProcess` 按 `Path` 比对，而 `Path` 对跨账号、跨完整性级别的进程读不出来
    （那一行 `catch { $false }` 把它判成「不在跑」）。「右键 → 以管理员身份运行了
    启动服务.bat」是一条真实存在的路径，于是：

    · 起的时候只看进程 → 明明在跑却又起一个，第二个绑不上端口，而 `run_server.py` 的
      `while True` 会每 30 秒重试一次；下面的探活拿**旧进程**的应答报成功。
    · 停的时候只看进程 → 一句「服务已停止。」送走操作员，而服务还占着端口。

    **这条钉的是「第二判据还在不在」，钉不住「它在不在正确的分支里」**——把起的那一支
    换个位置，文本上完全看不出来。那一层只有真机验得了。
    """
    text = ops_text()

    start = code_only(function_body(text, "Start-Service"))
    assert "Test-ServiceHealth" in start, "启动时不看端口，会起出第二个进程"

    stop = code_only(function_body(text, "Stop-Service"))
    assert "Test-ServiceHealth" in stop, "停止时不看端口，会说一句没验证过的「服务已停止。」"

    # 「服务已停止。」必须在**端口没应答**那一支里。
    answered = braced_block(stop, "if (Test-ServiceHealth -Port (Get-Port))")
    assert "服务已停止。" not in answered, (
        "端口还在应答的那一支里也说「服务已停止。」——服务明明还占着端口"
    )
    assert "服务已停止。" in stop, "停完之后什么也不说了"


def test_ops_admin_is_lan_only():
    """`ops.ps1` 也不该为单机用法弹 UAC。

    它与 install.ps1 那条是同一件事的另一半：装的时候没弹，点「启动服务.bat」的时候
    弹了，用户会以为安装根本没生效。
    """
    lines = [line for line in ops_text().splitlines() if line.startswith("$needsAdmin =")]
    assert len(lines) == 1, f"`$needsAdmin =` 期望恰好 1 行，实际 {len(lines)} 行"
    assert "$Usage -eq 'lan'" in lines[0], "`$needsAdmin` 没有按用法收窄"


def test_the_service_stopper_waits_without_a_scheduled_task():
    """停服务时，**进程那一半不能被「有没有计划任务」挡住**。

    这两件事以前写在一个 `if ($existingTask)` 里。单机用法根本没有计划任务，于是那个
    15 秒等待整段不执行——而正在跑的 `python.exe` 会锁住 `python\\*.dll` 与
    site-packages 里的 `.pyd`，robocopy 复制到一半失败（退出码 8）。重跑一次能好，
    但那是一次没必要的失败，操作员看到的是「装了一半」。
    """
    text = install_text()

    # 判据（按全路径认我们这个 python.exe）在 `Get-ServiceProcess` 里，被 start / stop
    # 两处复用。它自己也得单独钉住：换成按名字认，会杀掉那台机器上别人在跑的 Python。
    #
    # 属性名是 `ExecutablePath` 而不是 `Path`：2026-09-18 收进程树时判据从 `Get-Process`
    # 换成了 `Get-CimInstance Win32_Process`（只有后者给得出 `ParentProcessId`），而
    # **CIM 上那个属性叫 `ExecutablePath`**。写成 `$_.Path` 时它恒为 `$null`、
    # `Where-Object` 于是筛掉每一个进程——症状是「服务明明在跑，安装器说没人跑」，
    # 而这条断言（旧版写的是 `$_.Path -eq`）正是会被那次改动弄红的那个。
    finder = code_only(function_body(text, "Get-ServiceProcess"))
    assert "$_.ExecutablePath -eq" in finder, (
        "认进程时不是按全路径比对（会误伤别人的 python.exe），或者属性名写回了 `$_.Path`"
    )
    assert "$script:PythonExe" in finder, "认进程时没拿安装目录里的那个全路径做判据"

    body = code_only(function_body(text, "Stop-RunningService"))
    assert "$task" in body, "任务那一半不见了"
    assert "Get-ServiceProcess" in body, "停服务时没有去认我们这个 python.exe"

    # **每一个** `if ($task)` 块都要查，不能只查第一个。`braced_block` 用 `re.search`
    # 只取第一个，而第一个是原本那句「有任务就停任务」——把等待循环嵌进**另一个**
    # `if ($task)`（这次要防的正是那个形状）时，只查第一个会让这条断言全绿。
    # 实测过：变异的那个版本在只查第一个的写法下确实没变红。
    # 从**行首**开始切，把缩进一起带上：`braced_block` 靠缩进找收尾，切掉了缩进它就去找
    # 列 0 的 `}`，而函数体里没有（`function_body` 已经把那个切走了）。
    starts = [m.start() for m in re.finditer(r"^[ \t]*if \(\$task\) \{", body, re.MULTILINE)]
    assert starts, "任务那一半不见了"
    for at in starts:
        block = braced_block(body[at:], "if ($task)")
        assert "Get-ServiceProcess" not in block, (
            "进程等待又被挪回「有计划任务才做」那一支里了——单机用法会连等都不等"
        )

    # **只等不杀在单机用法下是死路**：那里没有任何人去叫这个进程停，于是每一轮都等到
    # 最后，然后 robocopy 照样撞上被锁住的 DLL——同一个退出码 8，换了个入口。
    assert "Stop-Process -Force" in body, "等不到就强杀那一步不见了"

    call_sites = re.findall(r"^[ \t]*Stop-RunningService\s*$", text, re.MULTILINE)
    assert len(call_sites) == 2, f"Stop-RunningService 的调用点期望 2 个，实际 {len(call_sites)} 个"


def test_every_native_call_site_quotes_the_paths_it_passes():
    """每个 `Invoke-Native` 调用点都要把路径参数过一遍 `Quote-Argument`。

    `Start-Process -ArgumentList` 之间靠**空格**拼，不做转义（`install.ps1` 里
    `Quote-Argument` 那段注释写了这件事）。单机用法把安装目录搬到
    `%LOCALAPPDATA%\\xinliceping` 之后，路径里带空格从「少见」变成了常态
    （`C:\\Users\\Zhang San\\AppData\\Local\\...`）：不引起来的话，收到的是半截路径，
    robocopy 会把它当成**两个**参数。

    **这条断言只能证明「引号还在」，证明不了「每个该引的都引了」**——参数表在这里有
    三种形状（数组 splat、`@(...)`、管道），要数清楚每个参数得写一个半吊子的词法器，
    而它数错的时候是静默的。下面那条 robocopy 的断言是补上的那一半：那是唯一一个
    两个参数都是变量、且都被真机检验过的调用点。
    """
    for call in native_calls(install_text()):
        head = call.strip().splitlines()[0]
        assert "Quote-Argument" in call, f"调用点没有 Quote-Argument：{head}"

    robocopy = next(c for c in native_calls(install_text()) if "robocopy.exe" in c)
    assert "Quote-Argument $sourceFull" in robocopy, "robocopy 的源路径没有引起来"
    assert "Quote-Argument $destinationFull" in robocopy, "robocopy 的目标路径没有引起来"


def test_the_service_is_stopped_before_the_thing_that_needs_it_stopped():
    """两个调用点各自都要排在被它保护的那件事**前面**。

    数调用点个数挡不住「调用还在，只是挪到了后面」——而那正是这一个动作唯一会失效的
    方式：先复制再停服务，复制照样撞上被锁住的 DLL；先注册计划任务再停服务，停的就是
    刚注册的那一条。顺序错了在文本上完全看不出来（两行都在），只有真机才报。
    """
    text = install_text()
    stopper = re.search(r"^[ \t]*Stop-RunningService\s*$", text, re.MULTILINE)
    second = re.search(r"^[ \t]*Stop-RunningService\s*$", text[stopper.end():], re.MULTILINE)
    second_at = stopper.end() + second.start()

    copy_at = text.index("Copy-PackageInto -Source")
    register_at = text.index("Register-ServiceTask -Settings")
    assert stopper.start() < copy_at, "第 2 步里「复制程序文件」排在了停服务之前"
    assert second_at < register_at, "第 5 步里「注册计划任务」排在了停服务之前"


def test_the_operator_is_told_they_do_not_have_to_stop_the_service_first():
    r"""升级之前**不用人自己去停服务**，而这句话必须出现在操作员会读到的地方。

    2026-09-18 一位操作员问的正是「升级时，原来的服务用停止吗」。自动停这件事在代码里
    早就成立（`Stop-RunningService` 的两个调用点，顺序由上面那条锁着），缺的是
    **没有一句话告诉他**：整份 `部署说明.txt` 在问这一句之前只写了「不动密码 / 不动数据库」，
    读完无法判断要不要先去点一下「停止服务.bat」（或者更糟——以为要先卸载）。

    **删掉这一句不会让任何东西报错**，只会让他白按一次按钮，所以它需要一条自己的守卫。
    这一段在操作员动手之前唯一会被读到的地方（「再双击一次是升级」那一段），所以判据
    也限定在那一段里：别处（比如下面按钮清单里那个「停止服务.bat 要维护的时候先停掉」）
    本来就有这三个字，整文件搜会永远通过。

    断言的是「不用」与那个按钮名两样都在：换个说法可以，把这一条删掉就不行。
    """
    manual = (WINDOWS_ASSETS / "部署说明.txt").read_text(encoding="utf-8-sig")
    start = manual.index("是**升级**，不是重装")
    block = manual[start : start + 700]
    assert "不用你" in block and "停" in block, (
        "升级那一段没说「原来的服务不用你先停」——操作员会白按一次「停止服务.bat」，"
        "或者以为要先卸载重装"
    )
    assert "停止服务.bat" in block, (
        "那句话里没有写出按钮的**原名**：操作员按屏幕上那四个字去安装目录里翻，"
        "翻不到同一个东西（与「重置管理员密码」那条同源）"
    )


# ---------------------------------------------------------------- 运行环境：venv 而不是内嵌 CPython
#
# 2026-09-18 加。包里不再带那 21MB 的内嵌 CPython（`python/`）与配套的 124 行手写 wheel
# 解包器，改成：安装时用**这台电脑上**的 Python 3.11 x64 建一个 venv，再从包里的
# `wheels\` 离线 `pip install`。单机用法起服务时开一个**可见的**控制台窗口，关掉窗口
# 就是停服务。
#
# **这一组同样全是文本断言**（与上面「用法」那一组同一个性质）：挡的是「有人把保证改
# 回去」，挡不住「PowerShell 逻辑写反了」，也**完全证明不了** pip 在目标机上装得动、
# `python -m venv` 真能建出解释器、关窗口真能带走那两个进程。真正能证伪的只有真机
# （`deploy/README.md` 末尾那几步）。别把它读成一层更强的保证。

BUILD_PACKAGE = Path(__file__).resolve().parents[3] / "deploy" / "build_package.py"


def build_package_text() -> str:
    return BUILD_PACKAGE.read_text(encoding="utf-8")


def list_literal(source: str, name: str) -> str:
    """`NAME = [...]` 那个列表字面量的**内容**（不含方括号）。

    `re.DOTALL` + 非贪婪：`MUST_NOT_EXIST` 是写成一行的一个列表，`REQUIRED_PATHS` 是
    多行的，两种形状都要取得到。
    """
    match = re.search(rf"^{re.escape(name)} = \[(.*?)\]", source, re.MULTILINE | re.DOTALL)
    assert match, f"build_package.py 里找不到 `{name} = [...]`"
    return match.group(1)


def test_the_service_stop_path_kills_the_whole_process_tree():
    """★ 停服务必须收**整棵进程树**，两个文件各一份实现。

    这是这次改动里唯一「不加就会重演同一个 bug」的一条。venv 的
    `Scripts\\python.exe` **不是解释器，是一个只负责转发的壳**（CPython 的
    `Lib/venv/__init__.py`：「On Windows, we rewrite symlinks to our base python.exe
    into copies of venvlauncher.exe」）。它 `CreateProcessW` 起真正的解释器然后等它，
    于是服务在进程表里是**两个** `python.exe`。

    **Windows 不连坐。** 只按 `ExecutablePath` 找到壳、只杀壳，真正的服务活得好好的，
    而三条后果全是静默的：`venv --clear` 删不掉被孤儿映射着的 `.pyd`（每次升级都撞）、
    第 6 步起出第二个、`停止服务.bat` 说「服务已停止。」而端口还在应答。

    两个文件里都有 `Get-ServiceProcess`，是**两处独立编辑**（一个读 `$script:PythonExe`、
    一个读 `$PythonExe`），只改一处不会让任何东西变红——所以这里两个都要查。
    """
    for label, text in (("install.ps1", install_text()), ("ops.ps1", ops_text())):
        body = code_only(function_body(text, "Get-ServiceProcess"))
        assert "Get-CimInstance Win32_Process" in body, (
            f"{label} 的 Get-ServiceProcess 不是从 CIM 拿的进程表——`Get-Process` 给不出 ParentProcessId"
        )
        assert "ParentProcessId" in body, (
            f"{label} 的 Get-ServiceProcess 只收了自己那一个进程，没有连子进程一起收；"
            "venv 的 Scripts\\python.exe 是个转发壳，杀掉它服务不会停"
        )
        assert "$_.ExecutablePath -eq" in body, (
            f"{label} 的 Get-ServiceProcess 没有按全路径认根（会误伤那台机器上别人的 python.exe）"
        )


def test_the_installer_uses_a_venv_built_from_the_system_python():
    """建 venv 用的是**这台电脑上那个** Python；装依赖走 pip，且那一串开关一个都不能少。

    两句断言各挡一个方向：

    · `-Exe $script:BasePython`——建 venv 时 venv 自己还不存在，用 `$script:PythonExe`
      是个先有鸡还是先有蛋的错，症状是「找不到文件」而不是「参数传错了」。
    · pip 的参数表——`--isolated` 挡的是这台机器上一份全局 pip.ini（它的 `find-links`
      会**叠加**上来，最坏是 pip 去联一个不存在的内网源、按默认 5 次重试 × 15 秒卡住，
      在非技术操作员手上那是「装到一半不动了」）；`--no-index`/`--find-links` 挡的是
      联网；`--no-deps`/`--only-binary=:all:` 挡的是 pip 自己去解析依赖 / 去构建 sdist。

    **参数表必须在这个 `@( ... )` 块里断**，不能断「文件里出现过 `--isolated`」——
    上面那段注释把这几个开关逐条写了一遍，按裸文本断言的话删掉任何一行代码都还是绿的。
    """
    body = code_only(install_text())

    at = body.find("-Exe $script:BasePython")
    assert at >= 0, "建 venv 那一步没有显式指定用这台电脑上的那个 Python（-Exe $script:BasePython）"
    window = body[at: at + 300]
    assert "'-m', 'venv', '--clear'" in window, (
        "`-Exe $script:BasePython` 后面跟着的不是 `-m venv --clear`——它到底去跑什么了？"
    )

    call = re.search(
        r"Invoke-Python -Step '安装依赖' -Arguments @\((.*?)\n[ \t]*\)", body, re.DOTALL
    )
    assert call, "找不到「安装依赖」那次 pip 调用（或者它不再是 `@( ... )` 的形式）"
    args = call.group(1)
    assert "'-m', 'pip', 'install'" in args, "装依赖不是走 `python -m pip install`"
    for flag in (
        "--isolated",
        "--no-index",
        "--find-links",
        "--no-deps",
        "--only-binary=:all:",
        "--no-input",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--retries",
        "--timeout",
    ):
        assert flag in args, f"pip 的参数表里少了 {flag}（理由见它上面那段注释）"


def test_both_consumers_point_at_the_venv_python():
    """`$PythonExe` 的两个定义点必须指向**同一个** venv 里的解释器。

    两个消费者各在一份文件里（`install.ps1` 的 `$script:PythonExe`、`ops.ps1` 的
    `$PythonExe`），**谁都不知道对方写了什么**。改一处、忘另一处，症状是：装的时候
    一切正常（安装器用的是自己那一份），装完之后点「启动服务.bat」找不到文件——
    而这是一个**只在装完之后**才出现的不一致。

    比的是**两边的相对路径相等**，不是「都含 venv 这个词」：后者在
    `runtime\\venv-backup\\Scripts\\python.exe` 上照样是绿的。
    """
    install_paths = re.findall(r"PythonExe\s*=\s*Join-Path\s+\S+\s+'([^']+python\.exe)'",
                               install_text())
    ops_paths = re.findall(r"PythonExe\s*=\s*Join-Path\s+\S+\s+'([^']+python\.exe)'",
                           ops_text())
    assert install_paths, "install.ps1 没有把 $script:PythonExe 指到某个 python.exe"
    assert ops_paths, "ops.ps1 没有把 $PythonExe 指到某个 python.exe"

    expected = "runtime\\venv\\Scripts\\python.exe"
    assert set(install_paths) & set(ops_paths) == {expected}, (
        "两边指的不是同一个解释器。install.ps1: "
        f"{install_paths}；ops.ps1: {ops_paths}；期望两边都有 {expected!r}"
    )

    # 第 2 步里建完 venv 之后还有**第二次**赋值（按 `$venvDir` 拼），它也必须落在 venv 里。
    assert any(path.startswith("Scripts") for path in install_paths), (
        "建完 venv 之后没有按 $venvDir 重新指一次 $script:PythonExe"
    )
    assert re.search(r"\$venvDir = Join-Path \S+ 'runtime\\venv'", install_text()), (
        "$venvDir 指的不是安装目录下的 runtime\\venv"
    )


def test_the_base_python_must_be_311_x64():
    """候选 Python 的判据：**恰好** 3.11、64 位、且真的能建 venv。

    三条都在 `Resolve-BasePython` 的探测脚本里，由 **Python 自己**判、PowerShell 只看
    退出码（在 PowerShell 里解析它的输要穿四层引号，见 `Invoke-PythonScript` 那段）。

    · `(3, 11)` 是**相等**不是 `>=`：包里的 31 个 wheel 有 9 个是
      `cp311-cp311-win_amd64`（**不带 abi3**），3.12 上一个都装不上。
    · 64 位：32 位解释器加载不了那些 64 位的扩展模块。
    · `import venv, ensurepip`：`venv/__init__.py` 在找不到那个 redirector 源文件时
      只打一句 `logger.warning('Unable to copy %r', src)` 就继续（**它不抛**），于是
      建出来的 venv 里没有 `Scripts\\python.exe`，失败会推迟到很远的地方。
    """
    body = code_only(function_body(install_text(), "Resolve-BasePython"))
    assert "sys.version_info[:2] != (3, 11)" in body, (
        "版本判据不是「恰好 (3, 11)」——`>=` 会放 3.12 进来，而 9 个 cp311 wheel 装不上"
    )
    assert "sys.maxsize <= 2 ** 32" in body, "没有判 64 位"
    assert "import venv" in body and "import ensurepip" in body, (
        "没有确认这个解释器真的能建 venv（精简版会静默建出一个没有解释器的 venv）"
    )

    # 三种被拒的理由要**分开报**：诊断不同，处置也完全不同。
    assert "$code -eq 1" in body, "没有把「不是 3.11」单独报出来"
    assert "$code -eq 2" in body, "没有把「是 32 位的」单独报出来——它与「没装」的处置不一样"


def test_the_store_python_stub_is_rejected_before_it_runs():
    """应用商店那个别名要**在执行之前**排掉，不是跑完了再看结果。

    那个 `python.exe` 是一个 0 字节的 reparse point：**执行它会弹出应用商店窗口并挂在
    那里等**，而不是报错。在一个非技术操作员面前，那就是「安装卡住了」。

    所以判据不只是「文件里有 WindowsApps 这个词」，还有**次序**：两道排除（路径、
    0 字节）的 index 都要小于「真跑一次」那一行。把排除挪到后面，三个词一个不少，
    而测试照样得是红的——这正是 「先证明有东西可扫、再断言次序」那条的用法。
    """
    body = code_only(function_body(install_text(), "Resolve-BasePython"))
    run_at = body.find("$code = Invoke-Python")
    assert run_at >= 0, "找不到「真跑一次」那一处（探测候选 Python）"

    for exclusion in ("$full -like '*\\WindowsApps\\*'", "$item.Length -eq 0"):
        assert exclusion in body, f"少了这道排除：{exclusion}"
        assert body.index(exclusion) < run_at, (
            f"{exclusion} 排在了「跑它」之后——应用商店那个 0 字节别名会弹出商店窗口并挂住"
        )


def test_the_probe_scripts_can_import_the_app():
    """探针脚本必须自己带 `PYTHONPATH`，因为 `python 脚本.py` 与 `python -m` 是两条路。

    `python 脚本.py` 把 `sys.path[0]` 设成**脚本自己所在的目录**（TempDir），**不是当前
    工作目录**——所以 `Invoke-Python` 里那个 `-WorkingDirectory $script:BackendDir` 对
    探针一点用都没有。三个探针（`依赖检查` / `config-show` / `config`）都要 `import app.*`。

    2026-09-18 之前它们能跑，靠的是内嵌 CPython 的 `python311._pth` 里那行 `..\backend`；
    那个文件没有了，所以这一条是**替代品**而不是补充。不补的话第 4 步会以一句
    `ModuleNotFoundError: No module named 'app'` 停住，而那句话离原因隔着好几层。

    环境变量用完要放回去：这个进程后面还要起服务，不能让一个给探针用的变量漏进去。
    """
    body = code_only(function_body(install_text(), "Invoke-PythonScript"))
    assert "$env:PYTHONPATH = $script:BackendDir" in body, (
        "探针没有设 PYTHONPATH——按路径跑的脚本不会把 CWD 放进 sys.path，`import app` 会失败"
    )
    assert "$env:PYTHONPATH = $previousPythonPath" in body, (
        "探针用完没有把 PYTHONPATH 放回去，它会漏进后面起的服务进程"
    )


def test_the_wheel_unpacker_is_gone():
    """手写的 wheel 解包器与 `._pth` 改写**不许回来**。

    `Expand-Wheels`（自己解 zip、摊平 `.data/purelib`）存在的唯一理由，是内嵌版 CPython
    里没有 pip。`Set-PythonPathFile` 改写 `python311._pth` 同理。目标机上既然有了一个
    完整的 Python，pip 就是干这个的——那 124 行是白付的维护面。

    这里断的是**代码**（`code_only`）：注释里提到那几个名字是**对的**
    （「这句是从被删掉的 `Expand-Wheels` 里救出来的」那句话要留着，它说明那段中文提示
    为什么不能删）。整文件按裸文本断言会因为它而变红——改注释去迁就测试是更糟的方向。
    """
    body = code_only(install_text())
    for gone in ("Expand-Wheels", "Set-PythonPathFile", "python311._pth",
                 "python\\Lib\\site-packages"):
        assert gone not in body, (
            f"install.ps1 的正文里又出现了 {gone}——内嵌 CPython 的那一套回来了"
        )
    # 反过来：pip 那一步得在，否则上面那条在「什么都没做」的脚本上也是绿的。
    assert "'-m', 'pip', 'install'" in body, "pip 装依赖那一步不见了"


def test_the_single_usage_shows_a_console_window():
    """单机用法起服务 = 开一个**可见的**窗口，关掉它就是停服务。

    用户 2026-09-18 的原话是「用户使用时启动，关机时停止」，那个心智模型里的动作是
    **关掉一个看得见的窗口**。在此之前单机用法用 `-WindowStyle Hidden` 起进程，屏幕上
    什么都没有——要停它得回来点「停止服务.bat」或者翻任务管理器。

    · `Start-ServiceNow` 是**单机专用**的（第 6 步按 `$Usage` 分叉，局域网走
      `Start-ScheduledTask`），所以它干脆不写 `-WindowStyle`（`Start-Process` 在 Windows
      上默认就给子进程另开一个窗口）。
    · `-NoNewWindow` **绝对不行**：子进程共用这个 .bat 的控制台，.bat 一退出控制台就关，
      控制台一关子进程收到 `CTRL_CLOSE` 当场死掉——「启动服务」变成「启动一下然后立刻停」。
      这条推理与它当初写下时一字不差，只是结论反了过来，所以那段注释必须留着。
    · `ops.ps1` 的 `Start-Service` **两条用法共用**，所以窗口样式按 `$Usage` 分：局域网
      那条路的语义是「无窗口地在后台跑」，在那里弹一个可见窗口既不合语义，也会让运维
      以为服务是「手起着」的。

    **断言必须限定在函数体内**：`ops.ps1` 的 `Invoke-Python` 里那个 `-NoNewWindow` 是
    正当的（同步的一次性调用，要的正是共用控制台 + 拿退出码），按整文件断言这条一开始
    就是红的。
    """
    install_start = code_only(function_body(install_text(), "Start-ServiceNow"))
    assert "Start-Process" in install_start, "start 那一处不见了"
    assert "$script:PythonExe" in install_start, "起的不是我们那个解释器"
    assert "-WindowStyle" not in install_start, (
        "单机用法又给服务指定窗口样式了——不写就是默认的「另开一个可见窗口」"
    )
    assert "-NoNewWindow" not in install_start, (
        "`-NoNewWindow` 会让子进程共用 .bat 的控制台，.bat 一退出服务就收到 CTRL_CLOSE 死掉"
    )

    ops_start = code_only(function_body(ops_text(), "Start-Service"))
    assert "$Usage" in ops_start, "ops.ps1 的启动没有按用法分支"
    assert "'single'" in ops_start and "'Normal'" in ops_start, (
        "单机那一支不见了（或者不再指定 'Normal'）——那条路要的就是一个看得见的窗口"
    )
    assert "服务在**另一个**窗口里跑" in ops_start, (
        "没有告诉用户「跑起来的是另一个窗口」——他会以为自己关掉这个窗口服务就停了"
    )

    manual = (WINDOWS_ASSETS / "部署说明.txt").read_text(encoding="utf-8-sig")
    assert "就是服务" in manual and "把这个窗口关掉" in manual, (
        "部署说明里没写「那个窗口就是服务、关掉它就是停服务」——这是这次改动最容易被"
        "当成 bug 报回来的一点"
    )
    assert "屏幕上那个黑窗口就是服务本身" in install_text(), (
        "安装收尾的横幅没提那个新开出来的窗口——操作员会把它当成安装程序留下的残渣关掉"
    )


def test_the_package_no_longer_ships_python():
    """包里**不许**再有 `python/`，而且它要出现在 `MUST_NOT_EXIST` 里。

    两条各挡一个方向：`REQUIRED_PATHS` 里没有它，说明这次是真的不打了；
    `MUST_NOT_EXIST` 里有它，挡的是 `--keep` 时上一次留下的那个 21MB 目录混进 zip
    ——安装脚本已经不认识它了，一个死目录，还会让人以为「包里带了 Python」。
    """
    source = build_package_text()
    required = list_literal(source, "REQUIRED_PATHS")
    assert "python/" not in required and "python\\" not in required, (
        "REQUIRED_PATHS 里还有内嵌 CPython 的路径"
    )
    forbidden = list_literal(source, "MUST_NOT_EXIST")
    assert '"python"' in forbidden, (
        "MUST_NOT_EXIST 里没有 python——跑 `--keep` 时上一次留下的 python/ 会混进包里"
    )
    assert re.search(r'^TARGET_PYTHON_VERSION = "3\.11"', source, re.MULTILINE), (
        "build_package.py 没有声明目标机需要哪个 Python 版本"
    )


def test_the_checkup_knows_which_python_it_was_built_on():
    """`runtime\\build.json` 要记下建 venv 用的是哪个 Python，且「查看状态」要读它。

    venv 里 `pyvenv.cfg` 的 `home` 是**写死的绝对路径**。外面那个 Python 一旦被更新、
    卸载，或者（用户级安装）那个账号被删，服务就再也起不来——而日志里只有一句
    `pyvenv.cfg` 相关的英文，没人会联想到「有人升级过 Python」。这是**唯一**能在故障
    发生之前看见它的地方。

    第二句断言不能省：一个没人读的字段与一个不存在的字段，在故障现场是一样的。
    """
    assert re.search(r'"base_python": "', install_text()), (
        "build.json 模板里没有 base_python 字段"
    )
    status = code_only(function_body(ops_text(), "Show-Status"))
    assert "base_python" in status, "「查看状态」没有读 base_python——那个字段等于没写"
    assert "不在了" in status, "「查看状态」只报存在，不报「它不在了」"


def test_the_manual_mentions_python_311():
    """给操作员的那份说明必须写清：**先装 Python 3.11（64 位）**。

    这是改动 2 的最大产品风险：包装好了发过去，那台机器上没有 3.11，安装在第 0 步就停。

    断的是**开头那一节独有**的字串（下载地址与那句勾选框的原文），不是「文件里出现过
    3.11」——底下那张分诊表里也写着「找不到 Python 3.11」，按后者断言的话，把整个开头
    那一节删掉，测试照样是绿的。
    """
    manual = (WINDOWS_ASSETS / "部署说明.txt").read_text(encoding="utf-8-sig")
    assert "https://www.python.org/downloads/" in manual, "说明里没有给出下载地址"
    assert "Add python.exe to PATH" in manual, (
        "说明里没有点出安装时那个必须勾的框——忘了勾，安装程序很可能找不到 Python"
    )
    assert "不要去卸载或者升级它" in manual, (
        "说明里没写「装完之后不要动那个 Python」——它没了服务就起不来"
    )
    assert "64 位" in manual, "说明里没写必须是 64 位"


# --- requirements.lock.txt：被 **pip** 读的那一份，编码规矩与那几个 .ps1 相反 --------
#
# 上面那组按**扩展名**分（.ps1/.txt 要 BOM，.bat 纯 ASCII），那是因为它们都由
# PowerShell / cmd.exe 读。这一个不在 `deploy/windows/` 下，也不由那两个读——读它的是
# 目标机上的 pip，规矩正好反过来（**不许**有 BOM、必须纯 ASCII）。

DEPLOY_DIR = WINDOWS_ASSETS.parent
LOCK_FILE = DEPLOY_DIR / "requirements.lock.txt"


def test_the_lock_file_is_pure_ascii():
    r"""★ `requirements.lock.txt` 必须纯 ASCII——**pip 是按 locale 编码解它的**。

    2026-09-18 在一所学校的机器上：安装走到「安装依赖」当场停住，

        pip install --no-index --find-links C:\xinliceping\wheels --no-deps \
            -r C:\xinliceping\deploy\requirements.lock.txt
        UnicodeDecodeError: 'gbk' codec can't decode byte 0x84 in position 16

    那个 0x84 是第 1 行注释里「部署包里…」的「的」字的第三个字节。

    pip 那一侧：`get_file_content()`（`_internal/req/req_file.py`）把整个文件交给
    `auto_decode()`（`_internal/utils/encoding.py`），而它在文件**没有 BOM** 时退回
    `data.decode()`，也就是**当前 locale 的编码**——中文 Windows 上是 GBK。所以这个
    文件里**任何一个**中文字符都会炸掉安装，**注释里的也一样**，因为 pip 解的是整个文件。

    判据是**逐字节 < 128**，不是「用某个 locale 解一次能过」：一个 GBK 双字节序列
    （如 `C4 E3`）在 cp1252 下也是合法输入，两边各自解出**不同的**乱码——那种形状正是
    这条要排除的，而它正是「不做逐字节判据」时会漏掉的那个。

    它与 `.ps1 要有 BOM` **方向相反**，所以别把它并进上面那个按后缀的 glob 里，
    也别「顺手统一成带 BOM」：加 BOM 只对 pip 这一条读法有效（`BOMS` 表里第一个就是
    UTF-8），而这个文件还要被人用编辑器打开、被出包时的 `pip download` 读。纯 ASCII 是
    唯一一种不需要任何一方「解码得对」的形状。
    """
    raw = LOCK_FILE.read_bytes()

    # 先证明有东西可扫：文件被清空的话，下面的断言全都恒真。
    pinned = [
        line
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith(b"#")
    ]
    assert len(pinned) >= 30, f"{LOCK_FILE.name} 里的锁定项只有 {len(pinned)} 条，文件是不是被清空了？"

    offenders = [
        (number, line)
        for number, line in enumerate(raw.splitlines(), 1)
        if any(byte > 127 for byte in line)
    ]
    assert not offenders, (
        "有非 ASCII 字符——pip 会按 locale 编码解这个文件（中文 Windows 上是 GBK），"
        "安装会在「安装依赖」抛 UnicodeDecodeError。"
        "这几行里的中文注释要翻成英文；解释留在 deploy/README.md：\n"
        + "\n".join(f"  第 {number} 行：{line!r}" for number, line in offenders[:5])
    )


def load_build_package():
    """把出包脚本当模块导进来，好**真的调一次**它那条判据。

    `build_package.py` 顶层的 import 全是标准库（`packaging` 是在函数里才 import 的），
    所以导入它没有副作用；脚本本体在 `if __name__ == "__main__":` 之下。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("deploy_build_package", BUILD_PACKAGE)
    assert spec and spec.loader, BUILD_PACKAGE
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_packagers_ascii_check_is_not_vacuous(tmp_path):
    r"""判据本身要能变红：真文件过，**同一份文件加一个中文字**就不过。

    这条是 `test_the_lock_file_is_pure_ascii` 的变异验证，做在**同一次运行**里，
    所以它不会像手工变异那样随时间失传。它挡的是「把判据写成恒真」——
    一个 `return []` 的函数会让库里那条用例照样全绿。
    """
    module = load_build_package()
    assert module.verify_lock_file_ascii(LOCK_FILE) == [], "真文件不该被报"

    mutated = tmp_path / LOCK_FILE.name
    mutated.write_bytes(LOCK_FILE.read_bytes() + "# 中文\n".encode())
    problems = module.verify_lock_file_ascii(mutated)
    assert problems, "加了一行中文注释之后判据仍然说没问题——这条守卫是摆设"
    assert "非 ASCII" in problems[0]


def test_the_packagers_bom_check_counts_them(tmp_path):
    r"""出包时那条编码检查也要能看见**多出来的** BOM，而不只是「有没有」。

    与 `test_the_packagers_ascii_check_is_not_vacuous` 同一个做法、同一个理由：判据本身
    要在**同一次运行**里变红一次。2026-09-18 之前它只看 `raw.startswith(BOM)`，而
    `install.ps1` 当时开头堆着四个 BOM——出包时那句「带 BOM」照样打出来。
    """
    module = load_build_package()
    assert module.verify_windows_asset_encodings(WINDOWS_ASSETS) == [], "真文件不该被报"

    okay = tmp_path / "好的"
    okay.mkdir()
    (okay / "好的一份.ps1").write_bytes(BOM + "Write-Host '中文'\n".encode())
    assert module.verify_windows_asset_encodings(okay) == [], "一份好的 .ps1 不该被报"

    bad = tmp_path / "坏的"
    bad.mkdir()
    (bad / "两个 BOM 的.ps1").write_bytes(BOM * 2 + "Write-Host '中文'\n".encode())
    (bad / "正文里夹一个的.ps1").write_bytes(BOM + "Write-﻿Host '中文'\n".encode())
    problems = module.verify_windows_asset_encodings(bad)

    def about(name: str) -> list[str]:
        return [one for one in problems if one.startswith(name)]

    assert any("2 个 UTF-8 BOM" in one for one in about("两个 BOM 的.ps1")), problems
    # **按文件找，不按「整份问题清单里出现过那几个字」找**：两个 BOM 的那一份同时满足
    # 「开头多于一个」与「全文多于一个」两条，所以一个 `any("U+FEFF" in one …)` 会
    # 被它顺手满足——而那一条本该由第二份文件证明（实测：把 `count_boms` 的计数改成
    # 恒返回开头那个数，那一版的断言仍然全绿）。
    assert any("U+FEFF" in one for one in about("正文里夹一个的.ps1")), problems


def test_keep_can_actually_be_used_twice():
    r"""`--keep` 得**真的能用**：第二次出包时它必须能覆盖上一次那棵目录。

    2026-09-18 顺手撞到的：`--keep` 跳过 `rmtree`，而 `data/` 与 `frontend/dist/` 那两次
    `copytree` 没有 `dirs_exist_ok=True`，于是第二次跑到「复制源码与前端」直接
    `FileExistsError: ... dist/心晴部署包/data`。**它从来没有成功用过**——不传 `--keep` 时
    上面那行 `rmtree` 已经把目录删干净了，所以这个错只在「它唯一的用法」（上一次失败在半路，
    想把那 31 个 wheel 省下来）下才出现。

    这是**文本断言**：它挡的是「有人把 `dirs_exist_ok` 删掉」，不是「出包真的成功了」。
    """
    source = build_package_text()
    for target in ('ROOT / "data"', "frontend_dist"):
        call = re.search(
            rf"shutil\.copytree\(\s*{re.escape(target)}[^)]*\)", source, re.DOTALL
        )
        assert call, f"找不到往包里拷 {target} 的那次 copytree"
        assert "dirs_exist_ok=True" in call.group(0), (
            f"{target} 的那次 copytree 没有 dirs_exist_ok=True——"
            "带 --keep 的第二次出包会在这里撞 FileExistsError"
        )


def test_the_packager_checks_both_the_source_and_the_packaged_copy():
    r"""两个调用点都要在：出包前查**源文件**（早三分钟知道），自检时查**产物里那一份**。

    只留一个的话，`copy_windows_assets` 那条把锁文件拷进 `deploy\` 的路径就不受约束了
    ——而 pip 在目标机上读的正是**产物里的那一份**。「copy 的时候是对的」与「产物里是对的」
    是两件事，这条与 `verify_no_forbidden_paths` 那一条同源。

    这是**文本断言**：它挡的是「有人把这个调用删掉」，证明不了 pip 的行为（那只有真机）。
    """
    source = build_package_text()
    assert "def verify_lock_file_ascii(" in source, "出包脚本里没有这条判据"
    assert "verify_lock_file_ascii(LOCK_FILE)" in source, (
        "出包前那次早检查没有查锁文件——错误要等跑到最后一步才发现"
    )
    assert "verify_lock_file_ascii(package /" in source, (
        "自检没有查产物里的那一份，而 pip 在目标机上读的正是它"
    )


# ---------------------------------------------------------------- `.env` 那一层
#
# 2026-09-18 的第二所学校故障（第一件是 robocopy 的位掩码，第三件是锁文件的编码）。
# 现象与原因见模块 docstring 第 6 条。这一组守的是**形状**：值表唯一、升级就地补、
# 补不动的回来问人、秘密不进日志。它们证明不了 PowerShell 真的按这些行跑
# （开发机上没有 pwsh，见下面 `test_the_env_value_judgement_is_not_vacuous` 的说明），
# 但每一处都做过分手工变异验证：把对应的那行删掉/改回去，下面会有一条变红。

ENV_KEYS_DECLARATION = "$script:EnvFileKeys = @("
STEP3_HEADER = "Write-Step '第 3 步 / 共 6 步：写配置'"
ENV_PERMISSIONS_CALL = "Tighten-EnvPermissions -Path $envFile"


def env_key_list(text: str) -> list[str]:
    """`$script:EnvFileKeys` 那个数组字面量的内容（声明顺序）。"""
    start = text.index(ENV_KEYS_DECLARATION)
    end = text.index(")", start)
    return re.findall(r"'(XLP_[A-Z_]+)'", text[start:end])


def env_wanted_keys(text: str) -> list[str]:
    """`Get-EnvWantedValues` 那份值表的键（按书写顺序）。"""
    body = function_body(text, "Get-EnvWantedValues")
    return re.findall(r"'(XLP_[A-Z_]+)'\s*=", body)


def step3(text: str) -> str:
    """第 3 步那一段（从 `Write-Step` 到权限收紧那一行）。"""
    return install_region(text, STEP3_HEADER, ENV_PERMISSIONS_CALL)


def install_region(text: str, start: str, end: str) -> str:
    """主流程里从 `start` 到 `end` 之间的一段（`end` 那一行不算）。

    找不到就**带话说清楚**地红（不是一句 `ValueError: substring not found`）：
    这一组用例里有几条是按位置说话（谁排在谁前面），而「找不到」与「次序反了」
    是两件事——用位置切片去断次序时，前者会伪装成后者。
    """
    first = text.find(start)
    assert first >= 0, f"install.ps1 里找不到：{start}"
    last = text.find(end, first)
    assert last > first, f"找不到收尾标记：{end}（它必须排在 `{start}` 之后）"
    return text[first:last]


def order_of(text: str, needle: str) -> int:
    """`needle` 在 `text` 里的位置，找不到就带话说清楚（用于比较先后）。"""
    position = text.find(needle)
    assert position >= 0, f"install.ps1 里找不到：{needle}"
    return position


def test_the_env_values_have_exactly_one_source():
    r"""名单只有一份、值表只有一份，两个写入方都从那里取。

    这条是**构造上防漂移**的那一类：`Write-EnvFile`（首次安装，整份写出来）与
    `Repair-EnvFile`（升级，只补坏掉的行）各写一份清单的话，补出来的行会与写出来的行
    长成两种形状——而那种不一致只在一台**真的补过**的机器上才看得见，开发机上永远遇不到。

    两处集合相等是双向的：值表里多一个键（名单里没有）等于写出一行没人认识的配置；
    少一个键（名单里有）等于升级时补不出来、而第 3 步还会报「都齐了」。顺序也一起比，
    因为补写缺项时用的就是**名单的顺序**，而它决定了文件末尾那几行的样子。
    """
    text = install_text()
    declared = env_key_list(text)
    wanted = env_wanted_keys(text)

    # 先证明有东西可扫：两个解析都返回空的话，下面那句相等是恒真的。
    assert len(declared) >= 8, f"名单只解析出 {declared}"
    assert len(wanted) >= 8, f"值表只解析出 {wanted}"
    assert declared == wanted, (
        "$script:EnvFileKeys 与 Get-EnvWantedValues 的键对不上：\n"
        f"  名单：{declared}\n  值表：{wanted}"
    )

    # 首次安装那一支：每一行都必须从值表取，不许再有裸字面量。
    writer = code_only(function_body(text, "Write-EnvFile"))
    assert "$wanted = Get-EnvWantedValues" in writer, "Write-EnvFile 没有取值表"
    for key in declared:
        assert f"$wanted['{key}']" in writer, f"Write-EnvFile 里 {key} 这一行不是从值表取的"

    # 升级那一支：名单与值表都要用上（前者查缺、后者给值）。
    repair = code_only(function_body(text, "Repair-EnvFile"))
    assert "Get-EnvWantedValues" in repair, "Repair-EnvFile 没有取值表"
    assert "$script:EnvFileKeys" in repair, "Repair-EnvFile 没有用那份名单查缺项"

    # 第 3 步那**两句**「N 项都有值」（首次安装的「自检通过」与升级的「检查通过」）
    # 里的数字都必须来自名单本身——写死 8 的话，名单加到 9 项之后这两句会变成假话，
    # 而它们是操作员唯一的进度反馈。
    #
    # 判据是**两处都要**（`== 2`），不是「至少有一处」：变异验证时先写成 `in`，
    # 把其中一处改成写死的 8 之后**照样全绿**——两句话里有一句是对的，
    # 而另外那一句正是这台机器上会打印出来的那一句（首次装的机器只打第一句，
    # 升级的只打第二句）。
    assert step3(text).count("$script:EnvFileKeys.Count") == 2, (
        "第 3 步那两句「N 项都有值」没有都从名单取长度"
    )


# 升级那条路上 `Read-InstallSettings -Upgrade $true` 返回的**只有这三项**（`:812`）。
# 其余键（数据库四项 + 管理员口令）是首次安装才塞进去的，所以升级路径上读它们会抛
# `PropertyNotFoundException`——见 `Get-SettingText` 的注释与下面那条守卫。
SETTINGS_KEYS_THE_UPGRADE_PATH_SETS = {"installDir", "usage", "port"}

# 上面那三项之外，`install.ps1` 里**还有几处**读取，它们全部落在「只有首次安装才会执行」
# 的块里。这一份按 (键, 处数) 钉住：**它不是白名单，是「这几处为什么安全」的记录**。
# 处数对不上就红——加一处新的读取点必须回来写清楚它在哪个分支里、为什么升级路径走不到。
SETTINGS_READS_ON_THE_FRESH_INSTALL_SIDE = {
    "dbHost": 2,
    "dbPort": 2,
    "dbUser": 2,
    "dbName": 2,
    "dbPassword": 1,
    "adminPassword": 1,
}


def fresh_install_only_spans(text: str) -> list[tuple[int, int]]:
    r"""只会在**首次安装**那一条路上执行的块体，返回 `(起, 止)` 偏移。

    两种形状各取一半：
      · `if (-not $isUpgrade) { … }`           —— 整块（首次安装走它）；
      · `if ($isUpgrade) { … } else { … }`     —— **只取 else 那一半**。
    收尾一律按「同一缩进的那个 `}`」找，与 `braced_block` 同一套约定（这个文件里有
    here-string 内嵌的 Python，配平要写半吊子词法器，数错时是静默地多切或少切）。

    `if ($isUpgrade) {` 一律要求**后面跟着 else**：那几处都是「升级走这支、首次走那支」，
    没有 else 的话这个切片会静默退化成空——而空集合会让下面那条断言全部通过。
    """
    spans: list[tuple[int, int]] = []
    for match in re.finditer(r"^([ \t]*)if \(-not \$isUpgrade\) \{", text, re.MULTILINE):
        indent = match.group(1)
        close = re.search(rf"^{re.escape(indent)}\}}", text[match.end():], re.MULTILINE)
        assert close, "`if (-not $isUpgrade)` 没有同缩进的收尾"
        spans.append((match.end(), match.end() + close.start()))
    for match in re.finditer(r"^([ \t]*)if \(\$isUpgrade\) \{", text, re.MULTILINE):
        indent = match.group(1)
        close = re.search(rf"^{re.escape(indent)}\}} else \{{", text[match.end():], re.MULTILINE)
        assert close, "有一个 `if ($isUpgrade)` 后面没有 else——首次安装那一支不见了"
        else_start = match.end() + close.end()
        end = re.search(rf"^{re.escape(indent)}\}}", text[else_start:], re.MULTILINE)
        assert end, "那个 else 没有同缩进的收尾"
        spans.append((else_start, else_start + end.start()))
    return spans


def settings_property_reads(text: str) -> list[tuple[str, int, str]]:
    r"""`$settings.<键>` / `$Settings.<键>` 的**读取点**，返回 `(键, 行号, 那一行)`。

    三件事刻意排除在外：
      · **赋值**（`$settings.dbHost = …`）不是读取，升级路径上那是一行合法的写入。
        判据是 `(?!\s*[+\-*/]?=(?!=))`——`-eq` / `-ne` 后面那个 `=` 前面有字母，
        不会被这句吃掉，所以 `$Settings.usage -eq 'single'` 仍然算读取。
      · **方法调用**（`$Settings.ContainsKey(...)`）——那是方法，不是键。
      · **注释**里的举例。上面 `Get-SettingText` 的注释里正写着「不要写 `$Settings.dbUser`」，
        算进来的话这条守卫会因为文档写得对而变红（`code_only` 的 docstring 记着同一件事）。
    """
    pattern = re.compile(
        r"\$(?:script:)?[Ss]ettings\.([A-Za-z_]\w*)\b(?!\s*[+\-*/]?=(?!=))(?!\s*\()"
    )
    code = code_only(text)
    lines = code.splitlines()
    return [
        (match.group(1), code[: match.start()].count("\n") + 1, lines[code[: match.start()].count("\n")].strip())
        for match in pattern.finditer(code)
    ]


def test_a_settings_key_the_upgrade_path_never_sets_is_never_read_there():
    r"""升级路径上**读不到的键，一处都不许读**——2026-09-18 真机上就是这么炸的。

    那次日志：第 3 步刚打完「.env 已存在，升级只补坏掉的行」就停在一句
    `在此对象上找不到属性"dbUser"`。原因是升级分支的 `$settings` 里**只有** installDir /
    usage / port 三项，而 `Get-EnvWantedValues` 第一句读的是 `([string]$Settings.dbUser)`——
    `Set-StrictMode -Version Latest` 下**访问不存在的属性在读取那一刻就抛异常**，
    `[string]` 转型排在读取之后，一个字符都挡不住（`?? ''` 同理）。

    所以判据是**位置**，不是「有没有做转型」：

      · 那三项之外，每一处读取都必须落在 `fresh_install_only_spans` 里
        （`if (-not $isUpgrade)` 整块、或 `if ($isUpgrade) … else` 的 else 那一半）；
      · 每个键的处数按 `SETTINGS_READS_ON_THE_FRESH_INSTALL_SIDE` 钉住——
        「加了一处、它恰好也在某个 else 里」不该静默通过，那种位置要人来确认一次。

    **网眼（这条挡不住什么）**：它判的是「这处读取的文本位置在不在那些块里」，判不了
    PowerShell 的语义。真被绕过去的形状是「有人把整段逻辑搬进一个新函数，升级路径也调它」——
    那时读取点不在任何 else 里，这条会红（这是想要的）；但如果那个函数是在别处被
    `if ($isUpgrade)` 之外的路径调用的，文本就看不出来了。所以配了一条**机制**上的守卫：
    `test_the_env_wanted_values_do_not_read_missing_keys_directly` 钉住真正出事的那一处，
    而且 `Get-SettingText` 让「取一项、没有就当空」有了唯一写法。
    """
    text = install_text()
    code = code_only(text)
    spans = fresh_install_only_spans(code)
    # 先证明有东西可扫：block 一个都没找到（或者读点一个都没扫到）时，下面的循环空转，
    # 这条会**恒绿**——正则坏掉时正是这个样子。
    assert len(spans) >= 4, f"只找到 {len(spans)} 个「只有首次安装才执行」的块"
    reads = settings_property_reads(text)
    assert len(reads) >= 40, f"只扫到 {len(reads)} 处 `$settings.<键>` 读取"

    counted: dict[str, int] = {}
    for key, _, _ in reads:
        counted[key] = counted.get(key, 0) + 1

    for key, count in SETTINGS_READS_ON_THE_FRESH_INSTALL_SIDE.items():
        assert counted.get(key, 0) == count, (
            f"`$settings.{key}` 的读取点从 {count} 处变成了 {counted.get(key, 0)} 处。"
            "新增的那一处要么走 `Get-SettingText`，要么把处数与理由一起写进 "
            "SETTINGS_READS_ON_THE_FRESH_INSTALL_SIDE（它记的是「这几处为什么在升级路径上读不到」）"
        )

    pattern = re.compile(
        r"\$(?:script:)?[Ss]ettings\.([A-Za-z_]\w*)\b(?!\s*[+\-*/]?=(?!=))(?!\s*\()"
    )
    outside = [
        (match.group(1), code[: match.start()].count("\n") + 1, code.splitlines()[code[: match.start()].count("\n")].strip())
        for match in pattern.finditer(code)
        if match.group(1) not in SETTINGS_KEYS_THE_UPGRADE_PATH_SETS
        and not any(start <= match.start() < stop for start, stop in spans)
    ]
    assert not outside, (
        "升级路径上也有 `$settings` 里没有的键被读了——`Set-StrictMode -Version Latest` 下"
        "**读取本身**就会抛 PropertyNotFoundException（中文系统上是「在此对象上找不到属性…」），"
        "转型挡不住。要么改用 `Get-SettingText`，要么把这处挪进「只有首次安装才执行」的分支：\n  "
        + "\n  ".join(f"{key}（第 {line} 行）：{src}" for key, line, src in outside)
    )


def test_the_env_wanted_values_do_not_read_missing_keys_directly():
    r"""`Get-EnvWantedValues` 取数据库那几项一律走 `Get-SettingText`，不许写 `$Settings.dbUser`。

    上面那条守卫说的是**位置**（这一处读取在哪个块里），这一条说的是**机制**：
    `Get-EnvWantedValues` 在**两条路径**上都会被调用（`Write-EnvFile` 与 `Repair-EnvFile`），
    所以「它落在哪个块里」这个问题对它没有意义——它必须在**升级路径上也安全**。

    判据两半：
      · 五项（user / password / host / port / name）都从 `Get-SettingText -Key 'dbXxx'` 取；
      · 函数体里**没有**裸的 `$Settings.dbXxx`（这半句是那个 bug 的原样回放）。
    再加上 `Get-SettingText` 自己的判据必须是**键在不在**（`ContainsKey`），
    不是「值空不空」——后者会把 `XLP_PORT=0` 这类合法的值也当成缺项。
    """
    text = install_text()
    body = code_only(function_body(text, "Get-EnvWantedValues"))

    for key in ("dbUser", "dbPassword", "dbHost", "dbPort", "dbName"):
        assert f"Get-SettingText -Settings $Settings -Key '{key}'" in body, (
            f"`{key}` 没有走 `Get-SettingText`——升级路径上 `$settings` 里没有这一项，"
            "直读会抛 PropertyNotFoundException（2026-09-18 真机日志里的 `属性\"dbUser\"`）"
        )
        assert f"$Settings.{key}" not in body, (
            f"`Get-EnvWantedValues` 里还有裸的 `$Settings.{key}`——`[string]` 转型挡不住那个异常"
        )

    helper = code_only(function_body(text, "Get-SettingText"))
    assert "$Settings.ContainsKey($Key)" in helper, (
        "`Get-SettingText` 的判据不是「键在不在」——按值判空会把 `XLP_PORT=0` 这类合法的值"
        "当成缺项"
    )


def test_the_repair_never_writes_an_empty_value():
    r"""补不出来的项**留一句说明，不写一个空行**。

    这是上面那件事的另一面：`Get-SettingText` 在键缺失时返回空字符串，那个空字符串
    会一路流到 `$wanted[$key]`。而 `Repair-EnvFile` 的整个存在理由就是**消掉空值**——
    它此前会把 `$key + '=' + ''` 原样写回去（`KEY=` 一个字符都没有），也就是亲手造出
    一次 `.env` 里那个 `XLP_PORT=` 型故障。两个写入点各要一处判断：

      · 逐行补写那一路（`$lines[$i] = …`）之前；
      · 补写缺项那一路——**先攒进 `$appended` 再决定要不要写**：全都没有值时，
        连那句「下面这几项原来没有」的标题与空行也不该落进文件。

    判据按处数钉（`== 2`）：只有一处的话，另一条路照样能写出空值，而这一条会绿——
    变异验证时先把其中一处摘掉，确认它变红。
    """
    repair = code_only(function_body(install_text(), "Repair-EnvFile"))

    assert "Set-Content" in repair, "扫的不是 `Repair-EnvFile`（里面连写入都没有）"
    assert repair.count("[string]::IsNullOrWhiteSpace($wanted[$key])") == 2, (
        "两个写入点（逐行补写、补写缺项）没有各自判一次「值是不是空的」——"
        "少了哪一处，哪一处就会写出 `KEY=`"
    )
    assert "$lines += $appended" in repair, (
        "补写缺项那一路没有先攒进 `$appended` 再写——那两行说明与标题在全都没有值时会照样落进文件"
    )
    assert "$appended.Count -gt 0" in repair, "攒了 `$appended` 却没有据此决定写不写"


def test_the_upgrade_path_repairs_the_env_instead_of_trusting_it():
    r"""升级不再「不动它」，而是**验一遍、就地补坏掉的行**。

    这一条就是 2026-09-18 那次失败的正解。升级模式此前只从 `.env` 里抠一个端口号给
    防火墙用，其余一个字都不看；于是一份坏配置（手改错、上次半途失败留下的、从别处拷来的）
    会一路走到第 4 步的 `import app.db.session`，甩给操作员一段英文 traceback。

    三件事必须同时在：
      · 升级分支里有 `Repair-EnvFile`（补），且**没有** `Write-EnvFile`（整份重写）——
        「升级不动它」是对操作员的承诺，只补坏行才既守住承诺又消掉坏配置；
      · 数据库连接那一项坏了要**回来问**（`Read-DatabaseQuestions`）：它是唯一一项补不出来的，
        而局域网用法下这个文件的 ACL 只授了 SYSTEM 与 Administrators，让操作员自己改是句空话；
      · 端口以**文件里那一行**为准（第 3 步之后读回来的那一次），并在与本次作答不一致时
        说出「沿用了配置文件里的」——不说的话，操作员会以为自己刚把端口改成了它填的那个。
    """
    text = install_text()
    section = step3(text)
    upgrade = code_only(braced_block(section, "if ($isUpgrade)"))

    assert "Repair-EnvFile -Settings $settings -Path $envFile" in upgrade, "升级分支没有补 .env"
    assert "Write-EnvFile" not in upgrade, (
        "升级分支又整份重写了 .env——那会把操作员改过的值（比如刻意改回 127.0.0.1 的 XLP_HOST）"
        "静默改掉，而那句「升级不动它」就成了假话"
    )
    assert "Test-EnvValueOk -Key 'XLP_DATABASE_URL'" in upgrade, (
        "升级分支没有判数据库连接那一项——它坏了就补不出来，必须先判出来再问"
    )
    assert "Read-DatabaseQuestions" in upgrade, "数据库连接坏了没有回来问"

    # 端口读回来必须在**补写之后**：补写会把空的 `XLP_PORT=` 换成本次作答的那个值，
    # 顺序反过来的话读到的还是空的，防火墙与提示都会跟着错。
    assert order_of(upgrade, "Repair-EnvFile -Settings $settings -Path $envFile") < order_of(
        upgrade, "$settings.port = Get-EnvValue"
    ), "端口读回来排在补写之前"

    # 首次安装那一支：写完立刻读回来验一遍，不合格就停下。
    separator = section.find("} else {")
    assert separator >= 0, "第 3 步没有 else 分支（首次安装那一支）"
    fresh = section[separator:]
    assert "Write-EnvFile -Settings $settings -Path $envFile" in fresh, "首次安装没有写 .env"
    assert order_of(fresh, "Repair-EnvFile -Settings $settings -Path $envFile") < order_of(
        fresh, "Stop-WithError"
    ), "首次安装写完没有自检（或者自检排在写入之前）"
    assert "自检没通过" in fresh, "自检失败时没有说清楚"


def test_the_repair_leaves_a_healthy_env_alone():
    r"""没坏就一个字都不写——连时间戳都不动。

    两个「不动」各有理由：
      · `if (-not $changed) { return $notes }` 必须在 `Set-Content` **之前**。少了它，
        一次健康的升级会把 `.env` 重新写一遍（内容一样，但 ACL 与时间戳都变），
        而下面的 `Tighten-EnvPermissions` 本来只该处理刚建出来的那份；
      · 名单之外的键一律跳过（`-notcontains $key` → `continue`）。将来加的配置项
        （比如 `XLP_ACCESS_TOKEN_EXPIRE_MINUTES`）不该被这一版安装器当成「不认识的坏行」
        删掉或改掉——那是一个**装了旧包就丢配置**的形状。
    """
    repair = code_only(function_body(install_text(), "Repair-EnvFile"))

    assert order_of(repair, "if (-not $changed) { return $notes }") < order_of(
        repair, "Set-Content"
    ), "没坏也写了文件，或者那句判断排在写入之后"
    assert "$script:EnvFileKeys -notcontains $key" in repair, (
        "名单之外的键会被当成坏行处理——装了旧包就会丢掉新加的配置项"
    )


def test_the_env_value_judgement_is_not_vacuous():
    r"""`Test-EnvValueOk` 判的正是**那一次真的把它打趴下**的几种形状。

    故障原文（2026-09-18，`backend\\app\\core\\config.py:33`）：

        pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
        port / Input should be a valid integer, unable to parse string as an integer
        [type=int_parsing, input_value='', input_type=str]

    所以「空」是第一判据；`XLP_PORT=abc` / `XLP_PORT=99999` 同样过不了 pydantic 的 int
    校验（一个 `int_parsing`、一个 `less_than_equal`），`XLP_DOCS_ENABLED=是` 过不了 bool；
    `mysql+pymysql://:@:/` 这种「非空但什么都没有」的连接串则会让第 4 步报一句
    「Access denied」——与「密码打错了」长得一样，而人会去重打密码。

    **这条是文本断言，它证明不了 PowerShell 真的这么判**（开发机上没有 pwsh，
    `install.ps1` 在那边一行都不会执行）。它挡的是「有人把某一档判据摘掉」——
    手工变异验证过：删掉端口那一段（`XLP_PORT=abc` 于是被当成好值放行）这条就红。
    """
    body = code_only(function_body(install_text(), "Test-EnvValueOk"))

    assert "[string]::IsNullOrWhiteSpace($Value)" in body, "「空」这一档没有了"
    assert "[int]::TryParse($Value, [ref]$number)" in body, "端口没有判数字"
    assert "$number -ge 1 -and $number -le 65535" in body, "端口没有判范围"
    assert "'XLP_DOCS_ENABLED'" in body and "-contains" in body, "布尔那一项没有判"
    assert "'^mysql\\+pymysql://[^@/]+@[^/]+/[^/]+$'" in body, "连接串没有判形状"

    # 上面每一档都必须挂在**键**上判，而不是无条件返回真——一个恒真的函数会让
    # 上面那几句断言照样成立（它们是「存在」断言）。这里补一句「最后那一行是兜底」。
    assert body.rstrip().endswith("return $true"), (
        "函数的最后一句不是「其余键一律放行」——那说明某一条分支提前 return 了真"
    )


def test_neither_the_database_password_nor_the_jwt_secret_reaches_the_log():
    r"""补行说明里**不许出现**这两项的值。

    安装日志是操作员唯一能提供的东西（他在微信上把它发回来），而 `.env` 里有数据库口令
    与 JWT 密钥。`Get-EnvValueNote` 是这两行进日志的唯一入口（第 3 步只打印它返回的那句话），
    所以只在那一处把它们换成一句话——别处一律不许自己拼 `$wanted[...]`。

    这**不是**「日志文件本身要保密」：`%TEMP%` 下那份日志还有别的读者（远程协助的人、
    贴进聊天窗口的截图）。与 §8「不在日志里打印完整答卷」是同一条。
    """
    note = function_body(install_text(), "Get-EnvValueNote")

    for key in ("XLP_DATABASE_URL", "XLP_JWT_SECRET"):
        branch = re.search(rf"if \(\$Key -eq '{key}'\) \{{ return (.+?) \}}", note)
        assert branch, f"{key} 没有自己的那一句说明"
        assert "$Value" not in branch.group(1), (
            f"{key} 的值被原样写进日志了——它是要发给别人看的那份文件里的内容"
        )
    assert note.rstrip().endswith("return $Value"), "其余项应当照原样打印"

    # `Repair-EnvFile` 的那几句说明也必须过这一层。它自己拼 `$wanted[$key]` 的话，
    # 上面那条就只是装饰——两个返回说明的地方，只有一个是对的。
    repair = code_only(function_body(install_text(), "Repair-EnvFile"))
    assert "(Get-EnvValueNote -Key $key -Value $wanted[$key])" in repair, (
        "Repair-EnvFile 没有走 Get-EnvValueNote 就把值拼进了说明里"
    )

    # 首次安装那次日志里，JWT 密钥只报「生成了」这件事，不报值。
    assert "'JWT 密钥：本次随机生成（不要在多个部署之间共用）'" in install_text(), (
        "首次安装把 JWT 密钥的值打进了日志"
    )


def test_an_empty_unknown_env_key_is_reported_but_not_touched():
    r"""名单之外、但**是空的** `XLP_*` 行：报出来，不动它。

    补不上，是因为「该是什么值」没有答案（安装器不认识这一项）；但要报，因为一条空的
    `XLP_ACCESS_TOKEN_EXPIRE_MINUTES=` 与 2026-09-18 那次是同一种故障——`Settings()`
    在 import 期抛 `int_parsing`，而安装器一句话都不说。它出现的路径很具体：机器上跑的是
    **旧版本**的安装器，而它读的是新版本写下的配置。

    「只在日志里说」而不是「顺手注释掉」是刻意的：那要替操作员改一行安装器完全不认识、
    而它可能是下一版才有的功能开关的配置。这里是**只读**的——所以下面第一条断言
    就是「这个函数体里不许出现写文件的动作」。
    """
    text = install_text()
    body = code_only(function_body(text, "Get-EmptyUnknownEnvKeys"))

    assert "Set-Content" not in body and "Add-Content" not in body, "这个函数写了文件"
    assert "StartsWith('XLP_')" in body, "没有限定只认 XLP_ 前缀"
    assert "$script:EnvFileKeys -contains $key" in body, (
        "名单里的键也走这条路了——它们归 Repair-EnvFile 修"
    )
    assert "IndexOf('=')" in body and "$number++" in body, "没有逐行解析或者没记行号"

    # 调用点必须在 `if ($isUpgrade) / else` **之外**——判据是它的缩进：那一层的语句是
    # 4 个空格，钻进任一千里就是 8 个。只覆盖一半的后果很具体：首次安装的机器与升级的
    # 机器是两批不同的机器，而两边各只打得出其中一句。
    section = step3(text)
    call_lines = [line for line in section.splitlines() if "Get-EmptyUnknownEnvKeys -Path $envFile" in line]
    assert len(call_lines) == 1, f"第 3 步里这一段提醒出现了 {len(call_lines)} 次"
    assert call_lines[0].startswith("    foreach "), (
        "这一段提醒不在 4 空格那一层——它钻进了 if/else 的某一支里"
    )
    assert "请把值填上" in section, "提醒里没有说该怎么办（只说「有一项是空的」等于没说）"


def test_the_database_questions_are_asked_in_exactly_two_places():
    r"""五个数据库问题（地址/端口/用户名/口令/库名）只写一份，两个调用点共用。

    两个调用点是：首次安装（`Read-InstallSettings`），以及升级时发现 `.env` 里的连接串
    空的/形状不对（第 3 步）。各抄一份的话，两份措辞会漂开——而它们是照着
    「部署说明.txt 第四节 + 屏幕提示」这一对写的，漂开之后「照说明书的操作员」与
    「照屏幕的操作员」会读到两套话，出问题时两边说的不是同一件事。

    两处都必须真的**在**：只有首次安装那一处的话，升级时那份坏配置就没有出路了
    （上面 `test_the_upgrade_path_repairs_the_env_instead_of_trusting_it` 钉的正是这一半）。
    """
    text = install_text()
    calls = re.findall(r"^\s*\$db = Read-DatabaseQuestions\s*$", text, re.MULTILINE)
    assert len(calls) == 2, f"Read-DatabaseQuestions 的调用点有 {len(calls)} 处，应当是两处"

    prompts = function_body(text, "Read-DatabaseQuestions")
    for prompt in ("数据库地址", "数据库端口", "数据库用户名", "数据库密码", "数据库名"):
        assert prompt in prompts, f"共用的问题里少了「{prompt}」"

    # 首次安装那一支不许再自己问一遍。
    fresh = function_body(text, "Read-InstallSettings")
    assert "MySQL 连接信息" not in fresh, "Read-InstallSettings 又抄了一份数据库问题"
    assert "$db = Read-DatabaseQuestions" in fresh, "首次安装没有走共用的问题"
    assert "$db.dbHost" in fresh and "$db.dbName" in fresh, "取回来的值没有接进设置里"


def test_the_env_is_validated_before_anything_reads_it():
    r"""补写排在**权限收紧**之前、第 4 步之前。

    次序是这个修复的一半：`.env` 的读者有三层——`Tighten-EnvPermissions`（会在读不了时
    把文件锁成只有 SYSTEM 能看）、第 4 步的依赖冒烟（`import app.db.session` → `Settings()`）、
    以及最后真正跑起来的服务。补写落在它们任何一层之后就等于没补：收紧之后可能连读都读不了，
    而第 4 步之后坏配置已经变成一段 traceback 了。
    """
    text = install_text()
    repair = order_of(text, "Repair-EnvFile -Settings $settings -Path $envFile")
    assert repair < order_of(text, ENV_PERMISSIONS_CALL), "补写排在了收紧 .env 权限之后"
    assert repair < order_of(text, "第 4 步 / 共 6 步"), "补写排在了第 4 步之后"

    # 诊断：第 4 步的配置自检要把「读的是哪一个文件、它在不在」念出来。
    # 2026-09-18 那次排查里，没有人知道 pydantic 究竟读的是哪个文件——安装器在
    # `C:\xinliceping\backend\.env` 上做文章，而报错只说 `input_value=''`。
    assert "配置文件：{env_file}" in text, "配置自检没有念出它读的是哪一个 .env"


# ---------------------------------------------------------------- PowerShell 的值语义

_BARE_COUNT_RE = re.compile(r"\$([A-Za-z_][\w:]*)\.Count\b")
_ASSIGN_RE = re.compile(r"^[ \t]*\$([A-Za-z_][\w:]*)[ \t]*=[ \t]*(.+?)[ \t]*$", re.MULTILINE)

# 值可能真的是 `$null`、但**用法本身把它挡住了**的那几处。每一条都要写明理由——
# 这张表不是「暂时放行」的白名单，它是「为什么这里不会炸」的记录。
TRUTHINESS_GUARDED_COUNT_SITES = {
    # `ops.ps1` 的备份清理：`if ($old) { … $($old.Count) … }`——`$old` 为空时整段进不去。
    ("ops.ps1", "old"),
}


def bare_count_sites(text: str) -> list[str]:
    """所有**裸变量**上的 `.Count`（`@(x).Count` 不算：接收者是那个数组表达式）。

    这样做是有意的：`@(...)` 正是这道守卫要求的写法，凡是包了的都自动不在名单里。
    """
    return _BARE_COUNT_RE.findall(code_only(text))


def assigned_rhs(text: str, name: str) -> list[str]:
    return [rhs for var, rhs in _ASSIGN_RE.findall(code_only(text)) if var == name]


def test_a_counted_value_is_something_that_cannot_be_null():
    r"""裸变量上的 `.Count`，那个变量必须是 `@(...)`（或 .NET 构造）出来的。

    **这是 2026-09-18 那次「安装其实成功了，日志说没完成」**：`Get-LanAddresses`
    用 `return @($addresses)` 收尾，而**空数组在管道里会被拆没**——一台没有局域网地址的
    机器上，调用方拿到的是 `$null` 而不是 `@()`。`$null.Count` 在
    `Set-StrictMode -Version Latest` 下抛「在此对象上找不到属性"Count"」，于是第 6 步的
    健康检查已经回了 200、服务已经在跑，收尾文案却崩了，操作员看到的是
    「安装没有完成：在此对象上找不到属性"Count"。请确认该属性存在。」

    它值得一条守卫，是因为**失败的样子与原因完全无关**：坏的是「老师这样访问」那一段的
    地址列表，报出来的却是一句像是安装失败的话，而重跑一次还会再撞——判断这条路径只能靠
    一台真的没有局域网地址的机器，开发机上永远看不见。

    判据故意只管 `.Count`：`.Length` 在同一形状下也会炸（`$x = Get-Foo` 拿到 `$null`），
    但这个文件里那两处各有各的理由——`$bytes` 是 `::ReadAllBytes()`（永远返回数组），
    `$item` 上面一行就是 `if (-not $item -or $item.PSIsContainer) { continue }`。
    把它们一起收进来的话，得先写一个「这行有没有被真值判断罩住」的半吊子判据，
    而那种判据错过一次就再也不可信了。
    """
    seen = 0
    for path in (INSTALL_SCRIPT, OPS_SCRIPT):
        text = path.read_text(encoding="utf-8-sig")
        for name in bare_count_sites(text):
            seen += 1
            if (path.name, name) in TRUTHINESS_GUARDED_COUNT_SITES:
                continue
            rhs = assigned_rhs(text, name)
            assert rhs, (
                f"{path.name}：`${name}.Count` 找不到它的赋值——要么写成 `@(...)`，"
                f"要么它根本不是这个文件里造的（那就得在这里说明为什么它不会是 $null）"
            )
            for one in rhs:
                assert one.startswith("@(") or one.startswith("["), (
                    f"{path.name}：`${name}` 的赋值是 `{one}`——它可能是 $null。"
                    f"写成 `@(...)` 再取 `.Count`（空数组在管道里会被拆没）"
                )

    # 顺带钉住这道守卫自己不是空转：正则坏掉时上面那个循环一次都不进，测试照样绿。
    # 13 处是 2026-09-18 的实际值，少一处都说明**扫法**变了而不是代码变干净了。
    assert seen >= 12, f"只扫到 {seen} 处 `.Count`，太小了——大概扫描逻辑坏了"


def test_the_lan_address_list_survives_being_empty():
    r"""上面那条的具体落点：`@(Get-LanAddresses)` 与函数里那段说明必须都在。

    守卫写宽了容易变成「扫一遍什么也没说」，所以这一条把 2026-09-18 那次的两半逐字钉住：
    调用点包了 `@(...)`，且函数体里写明了**为什么必须包**——下一个人看到的是一句
    「多打两个字符」，而它值一次真机安装。
    """
    text = install_text()
    assert "@(Get-LanAddresses)" in text, "局域网地址列表没有被 @(...) 包起来"
    assert "$lanAddresses = Get-LanAddresses" not in text, "又变回裸赋值了"

    body = function_body(text, "Get-LanAddresses")
    assert "拆没" in body, (
        "函数里没有写明「空数组会被拆没」——下一个调用点还会写出裸赋值"
    )

    # 私网地址一个都没有时**照样给地址**（只排掉环回与 169.254）。这一条是同一个函数的
    # 第二半：那台机器的列表是空的，而空的下场是操作员在收尾文案里拿不到任何一条 URL
    # ——那段字存在的全部意义就是给他一条能念给同事的地址。真正一个 IPv4 都没有
    # （没连网）时才轮到「用 ipconfig 看一下」那一句。
    assert code_only(body).count("Get-NetIPAddress") == 2, (
        "私网地址的兜底查询不见了——一个 10./172.16-31./192.168. 之外的地址都不再被印出来"
    )
    assert "@($addresses).Count -eq 0" in body, (
        "兜底没有排在私网查询之后（它会在有私网地址时也覆盖掉那一份）"
    )
    assert "ipconfig" in text, "两条路都认不出来时没有告诉操作员怎么办"


def test_the_install_summary_cannot_report_a_success_as_a_failure():
    r"""健康检查已经回真之后的说明文字，出错也不能说「安装没有完成」。

    上面那条挡住了 2026-09-18 那一次的**原因**（裸 `.Count`），这一条挡的是它的
    **后果**：那一段（收尾横幅、局域网地址、接下来该做的三件事）整段挂在外层那个
    `catch` 底下，于是里面任何一次异常——地址列表算错、`Join-Path` 拿到的路径怪、
    下一版新加的某一行——都会被报成「安装没有完成：…」，而那时服务**正在跑**、
    健康检查刚刚回过 200。操作员看到的最后一句是「失败」，接下来照着它说的重新
    双击一遍。

    判据的形状：从 `Wait-ForHealth` 那一行到外层 catch 之间，必须有自己的
    `try {` / `catch {`，而且那一对里**不许出现**「安装没有完成」——
    成功的安装不许用失败的话收场。外层那句必须还在：真正的失败（第 1–6 步里抛的）
    仍然要照原样报出来，这一条不是要把它调轻。
    """
    text = install_text()
    start = text.index("$healthy = Wait-ForHealth")
    # 外层 catch 的锚取**代码**里那一句（注释里也提到过这句话，取文字会取到注释上）。
    outer = text.index("Write-Log ('安装没有完成：", start)
    raw = text[start:outer]
    assert len(raw) > 2000, "取到的片段太短，锚八成挪了位置"
    summary = code_only(raw)

    assert "    try {" in summary, (
        "收尾说明没有被包进自己的 try/catch——里面任何一次异常都会让一次成功的安装"
        "报出「安装没有完成」"
    )
    assert "    catch {" in summary, "包了 try 却没有接住"
    assert "安装没有完成" not in summary, (
        "收尾说明自己的 catch 里写着「安装没有完成」——服务已经在跑，这句话是假的"
    )
    assert "收尾说明没能打完" in summary, "那段 catch 没有说清「失败的是说明，不是安装」"
    assert "安装是成功的" in summary, (
        "那段 catch 只说「出错了」，没说清服务已经在跑——操作员会去重新装一遍"
    )

    # 外层那句还在，而且**排在那对 try/catch 之后**（顺序反了就说明取错了地方）。
    assert "安装没有完成" in text[outer:], "外层那句真正的失败报告被删掉了"
    assert text.index("    catch {", start) < outer


def test_every_bat_the_scripts_name_is_a_file_that_exists():
    r"""脚本里提到的每一个 `.bat` 文件名都必须真的存在于磁盘上。

    这一条挡的是「**文案里的文件名与目录里的文件名对不上**」。`install.ps1` 的收尾文案
    里列着操作员接下来要用到的那几个按钮，而按钮名换过一次（`.bat` 从 `改管理员密码`
    改成 `重置管理员密码`）之后，屏幕上的那句话没人跟着改——按它去安装目录里找，
    找不到同一个东西。

    只扫 `xxx.bat` 这种**带扩展名**的写法，所以它是个**有边界的网**：收尾那句
    「双击安装目录里对应的那几个 .bat」把真正的按钮名省掉了，网就漏过去了（那一处
    由本文件下面那条 `..._says_重置管理员密码...` 逐字钉住，理由见它）。这里管的是
    另一种形状：**写了一个名字，而那个名字在磁盘上不存在**——改名只改一处、顺手把
    `重置管理员密码.bat` 写成 `重置密码.bat`、或者提到一个已经删掉的按钮，都会红。

    判据是**后缀匹配**而不是相等：`.ps1` 里有几处是整句话被「」引起来、而句子恰好以
    文件名结尾（`「右键以管理员身份运行了启动服务.bat」`），那些前缀是散字，不是文件名
    的一部分。所以只要「某个已知文件名是它的后缀」就算认得出来；一个都不是的后缀才是
    真的写了不存在的名字。
    """
    # 三处按钮，各在包里的不同位置，缺一处这里就会把**存在的**名字报成不存在：
    #   · `deploy\windows\*.bat`   → 包里的 `<pkg>\deploy\`（手工启动那两枚与
    #     `数据库增量升级.bat`，与各自的 `.ps1` 同目录，所以它们用 `%~dp0` 找得到它）
    #   · `deploy\windows\ops\*.bat` → 装完拷到**安装根**，所以 `install.ps1` 与
    #     `部署说明.txt` 提到它们时只写文件名
    #   · `一键安装.bat` 是唯一留在包根的那一个（`build_package.py` 的 `ROOT_LEVEL_ASSETS`）
    known = {path.name for path in WINDOWS_ASSETS.glob("*.bat")}
    known |= {path.name for path in (WINDOWS_ASSETS / "ops").glob("*.bat")}
    known.add("一键安装.bat")  # 包根那一个，不在 ops\ 里

    # **每加一个会提到按钮的 `.ps1` 就要把它加进这份名单**，否则最新的那个脚本恰好是
    # 唯一没人守的：`manual-migrate.ps1` 点了四个按钮名（`数据库增量升级.bat`、
    # `手工启动后端.bat`、`停止服务.bat`、`查看状态.bat`），而名单里漏掉它时，
    # 这四个名字写错任何一个都不会有任何东西报红——正是这条用例要挡的那件事。
    seen: set[str] = set()
    for name in ("install.ps1", "ops.ps1", "manual-start.ps1", "manual-migrate.ps1"):
        text = (WINDOWS_ASSETS / name).read_text(encoding="utf-8-sig")
        seen |= set(re.findall(r"[一-鿿A-Za-z0-9_]+\.bat", text))

    unresolved = sorted(
        token for token in seen if not any(token.endswith(file) for file in known)
    )
    # 正则坏掉（或那两个字面量被删光）时 `seen` 是空的，而「空集合没有未知项」会静默
    # 通过——先证明扫到了东西，再断言扫到的东西都对得上。
    assert len(seen) >= 10, f"只扫到 {len(seen)} 个 .bat 字面量，正则八成坏了：{sorted(seen)}"
    assert not unresolved, (
        f"这些 .bat 名在磁盘上不存在：{unresolved}。屏幕上的名字要与安装目录里的文件名"
        f"一致，否则操作员按它去找会找不到。目录里实际有：{sorted(known)}"
    )


def test_the_summary_names_the_admin_password_button_as_it_is_on_disk():
    r"""收尾文案里那个「重置管理员密码」必须写成磁盘上的名字。

    2026-09-18 用户问「admin 密码怎么设置」。答案就在收尾文案里，而它当时写的是
    「以后要停 / 起 / 备份 / **改**管理员密码，双击安装目录里对应的那几个 .bat」——
    那句里的按钮名与磁盘上的 `重置管理员密码.bat` 对不上，而**它是这段话里唯一一处
    告诉操作员「忘了密码怎么办」的地方**。

    为什么下面这条要单独写而不是并进上面那条：那一句把真正的按钮名省掉了
    （「对应的那几个 .bat」），所以 `xxx.bat` 那个网永远扫不到它——**网眼多大要
    说清楚**，否则下一个人会以为上面那条已经覆盖了这里。

    同时钉住两个方向：`重置管理员密码` 这几个字在，`改管理员密码` 这个旧写法不在。
    只钉前者的话，两句话并存也能过，而那时候操作员看到的是哪一句就全看运气了。
    查旧写法要在 `code_only` 上查：解释「为什么叫这个名字」的注释里**必须**能提到它，
    否则下一个人改回来的时候不知道自己在改什么。
    """
    text = install_text()
    assert "重置管理员密码" in text, (
        "收尾文案不再提「重置管理员密码」了——忘了密码的操作员在这一整段话里找不到出路"
    )
    assert "改管理员密码" not in code_only(text), (
        "「改管理员密码」回来了：磁盘上那个文件叫 `重置管理员密码.bat`，"
        "两个名字并存时操作员按哪一句做全看运气"
    )


# ---------------------------------------------------------------------------
# 「这次数据库由谁准备」这一维（2026-09-18 加）
#
# 用户的原话：「我先把数据库准备好，只一键安装应用服务（前后端）即可」。
# 于是第 1 步多了一问，选 2 时安装器**一个建库、写数据的动作都不做**——库与数据归
# 操作员，表结构归这一版程序（迁移两条路都跑，理由见 `Read-DatabaseMode` 的注释）。
#
# 这一维最坏的失败不是报错，是**屏幕上写着「安装完成」而库里什么都没有**：
# 第 6 步那个健康检查探的是登录页要的 `/public/branding`，那一个端点在空库上照样
# 答得出来（`system_setting` 读不到就回退默认值，见 §5）。所以下面这几条断的都是
# 「有没有做」与「有没有说」，而不是「有没有报错」。
# ---------------------------------------------------------------------------

# 选 1（默认）时安装器会做的四件**写库**的事。选 2 时一件都不许做。
#
# 键是**调用处的写法**（`Invoke-Python` 那一行的 `@('-m', '…')`），不是光秃秃的模块名：
# 第 4 步那个依赖冒烟测试里有一行 `import app.main, app.db.seed, app.db.create_database,
# app.db.reset_to_baseline`，它**不写库**（只是 import），按模块名去数会数出两处来。
DATABASE_WRITE_COMMANDS = {
    "'-m', 'app.db.create_database'": "建库",
    "'-m', 'app.db.seed'": "写入基础数据（量表 / admin 账号 / 基线任务）",
    "'-m', 'app.db.reset_to_baseline'": "清空演示数据",
    "'-m', 'app.db.set_admin_password'": "设管理员密码",
}

# 这四件里**只有建库**是选 3（`schema_prepared`）不做的——那一问的全部含义就是「库和表都
# 建好了」。另外三件照做，那正是用户 2026-09-18 那句「我现在可以手工创建数据库，并建立
# 数据表，**其他由你来完成**」里「其他」指的东西。
#
# 分开列出来而不是在守卫里写 `if what != '建库'`：这份名单是「选 3 承诺做什么」的**全部**
# 内容，它旁边那条 `sorted(...) == sorted(DATABASE_WRITE_COMMANDS)` 保证两处不漂——
# 以后往上面那张表里加第五件事时，这条会红着问「那选 3 做不做它」。
SCHEMA_PREPARED_STILL_DOES = (
    "'-m', 'app.db.seed'",
    "'-m', 'app.db.reset_to_baseline'",
    "'-m', 'app.db.set_admin_password'",
)

# 选 2 时操作员要自己补的**三件**（《部署说明.txt》那一节里逐字写着这三条命令）。
# 与上面那份四件不是同一批：`reset_to_baseline` 是演示数据清理、`set_admin_password`
# 是他自己的口令，两件都不属于「准备数据库」。
MANUAL_COMMANDS = ("app.db.create_database", "alembic upgrade head", "app.db.seed")


DATABASE_MODES = ("installer", "prepared", "schema_prepared")


def database_mode_branches(text: str) -> list[tuple[str, str, str | None]]:
    r"""每一处 `if ($DatabaseMode -eq '<码>') { … } [else { … }]` → `(码, 命中那一支, 另一支)`。

    条件**恰好**是这一个比较的才算：`… -and -not $isUpgrade` 那种更长的条件（第 6 步收尾
    文案里那一处）不在返回里——它问的是「密码那一行怎么说」，不是「做不做这件事」。
    没有 `else` 的（第 6 步那句指路的话）第三项是 `None`。

    **它按值返回，不是只认选 2。** 2026-09-18 加第三种取值时这条 helper 一起改了：按
    「只认 `-eq 'prepared'`」写的话，「选 3 也会走到这里」这件事在 helper 这一层就丢掉了，
    而守卫会拿「选 2 那一支的 else」去替选 3 说话——那个 else 同时也是选 1 的，于是
    「选 3 不建库」这种断言看起来有、其实没人守着。

    这一层**不**断言「必须有 else」：谁必须有 else 是每条守卫自己的判据。写进 helper 的话，
    第 6 步那处合法的「无 else」分支（服务起不来时补一句指路的话）会连扫都扫不到——
    而它的判据是「那一处**别**把后面那句『请把日志发回来』一起吞进去」，与「必须有 else」
    正好相反。

    收尾按「同一缩进的那个 `}`」找，与 `fresh_install_only_spans` / `braced_block` 同一套
    约定（这个文件里有 here-string 内嵌的 Python，配平要写半吊子词法器，数错时是静默地
    多切或少切）。
    """
    branches: list[tuple[str, str, str | None]] = []
    for match in re.finditer(
        rf"^([ \t]*)if \(\$[Dd]atabaseMode -eq '({'|'.join(DATABASE_MODES)})'\) \{{",
        text,
        re.MULTILINE,
    ):
        value = match.group(2)
        indent = match.group(1)
        tail = text[match.end():]
        close = re.search(rf"^{re.escape(indent)}\}}", tail, re.MULTILINE)
        assert close, f"`if ($DatabaseMode -eq '{value}')` 没有以同缩进的 `}}` 收尾"
        then_text = tail[: close.start()]
        rest = tail[close.end():]
        if not rest.startswith(" else {"):
            branches.append((value, then_text, None))
            continue
        else_start = close.end() + len(" else {")
        end = re.search(rf"^{re.escape(indent)}\}}", tail[else_start:], re.MULTILINE)
        assert end, "那个 else 没有同缩进的收尾"
        branches.append((value, then_text, tail[else_start: else_start + end.start()]))
    return branches


def database_mode_landings(branches: list[tuple[str, str, str | None]]) -> dict[str, str]:
    r"""选 1 / 选 2 / 选 3 **各自**实际会走到的代码文本的并集。

    每一处 `if ($DatabaseMode -eq '<码>')` 展开成三条路：命中那个码的走 `then`，
    另外两个码走 `else`（那一处没有 else 时，它们这一段什么都不走）。

    于是三种取值的落点是这样分布的：

      · **选 1 与选 3 在四处里重合三处**（不问密码那一处、灌基础数据那一处、收尾那句话），
        只在「建库」那一处分岔（选 1 建、选 3 不建）；
      · **选 2 独自落在另一侧**（不建库、不灌数据、不问密码）。

    这一层存在的理由：拿**某一处的某一支**去断言会张冠李戴。「选 3 也要灌基础数据」在
    「选 2 那一支的 else」里成立，而那个 else 同时也是选 1 的——只断言它，证明不了选 3
    走得通。判据必须先算清「哪几条路会合到这里」，再对并集说话。
    """
    landings: dict[str, list[str]] = {mode: [] for mode in DATABASE_MODES}
    for value, then_text, other_text in branches:
        landings[value].append(then_text)
        if other_text is None:
            continue
        for mode in DATABASE_MODES:
            if mode != value:
                landings[mode].append(other_text)
    return {mode: "\n".join(parts) for mode, parts in landings.items()}


def test_the_prepared_mode_leaves_the_database_alone_and_says_so():
    r"""选 2 时四件写库的事一件都不做，迁移照跑，而且**跳过的那两件要说出来**。

    判据是位置：四件写库的事各出现一次、且都落在**选 1 会走到的那一堆文本**里；
    `alembic upgrade head` 出现一次、且**不在任何一支里**（它是刻意的越界：
    表结构归这一版程序，库与数据归操作员）。

    为什么迁移非跑不可：不跑它的下场是「屏幕说安装成功、页面 500」——换了程序文件而表
    结构停在上一版时，**登录页照样打得开**（它读的 `system_setting` 是旧表），而第 6 步
    那个健康检查探的正是登录页要的那一个端点，所以「一半坏掉」它抓不住。

    最后一条断的是「说没说」：跳过了基础数据而只说一句「不写入」，屏幕上那一刻写着的
    仍是「安装完成」——操作员手里最缺的是一句「打不开任何页面，照那一节补」。

    **网眼**：它判的是文本位置，判不了 PowerShell 语义。真被绕过去的形状是「把选 2
    要做的事搬进一个新函数，两支都调它」——那时 `app.db.seed` 这一行不在任何 else 里，
    这条会红（这是想要的）；但若那个新函数只在选 1 的路径上被调用，文本就看不出来了，
    所以配了一条机制上的守卫（就在下面）：连接自检那一句（`pymysql.connect`）必须落在
    **「建库那一处的 else」**里——那是选 2 与选 3 真正会走到的一支，且它是一句真代码，
    搬走它就得挪位置。
    """
    code = code_only(install_text())
    branches = database_mode_branches(code)
    assert len(branches) == 4, (
        f"`if ($DatabaseMode -eq '<码>')` 的分支从 4 处变成了 {len(branches)} 处。"
        "这四处各有各的判据：`Read-InstallSettings` 里那一处管「问不问管理员密码」，"
        "第 4 步两处管「建不建库」「写不写基础数据」，第 6 步那一处管「服务起不来时说什么」"
        "（第 6 步还有一处 `-eq 'prepared' -and -not $isUpgrade`，它问的是「密码那一行"
        "怎么念」，条件更长，**刻意**不在这一条扫的范围内）。加了一处就回来把这条数清楚"
    )
    assert sorted(value for value, _, _ in branches) == [
        "installer",
        "prepared",
        "prepared",
        "prepared",
    ], (
        "分支的**取值**与预期不符——只有「建库」那一处是按 `-eq 'installer'` 写的，"
        "另外三处全是 `-eq 'prepared'`。选 3（`schema_prepared`）**刻意没有自己的分支**："
        "它与选 1 共用「灌数据 / 问密码」、与选 2 共用「不建库」，多写一支就等于把同一件"
        "事说两遍，而两遍迟早会漂"
    )
    landings = database_mode_landings(branches)
    installer = landings["installer"]
    prepared = landings["prepared"]
    schema = landings["schema_prepared"]
    assert len(installer) > 500, f"「选 1」那一侧只切出 {len(installer)} 个字符，八成切空了"
    assert len(schema) > 500, f"「选 3」那一侧只切出 {len(schema)} 个字符，八成切空了"

    # 「选 3 做哪几件」这份名单必须把上面那四件**分成两份、一份不多一份不少**：往那张表里
    # 加第五件事而没回来回答「选 3 做不做它」时，这一条会红。
    assert sorted(
        list(SCHEMA_PREPARED_STILL_DOES) + ["'-m', 'app.db.create_database'"]
    ) == sorted(DATABASE_WRITE_COMMANDS), (
        "「选 3 仍然会做的三件事」与 `DATABASE_WRITE_COMMANDS` 对不上了——加了一件写库的"
        "事就要回来回答「选 3 做不做它」，答完把这一份名单一起改掉"
    )

    for call, what in DATABASE_WRITE_COMMANDS.items():
        assert code.count(call) == 1, (
            f"{what}（`{call}`）在 install.ps1 的**调用处**出现了 {code.count(call)} 次——"
            "这一问的前提是「只有一处调它，且只在安装器那一侧」"
        )
        assert call in installer, f"{what}（`{call}`）不在「选 1」那一侧里"
        assert call not in prepared, (
            f"{what}（`{call}`）出现在「选 2」那一侧里——选 2 的全部意思就是"
            "「库与数据归操作员」，做了这一件，那句承诺就是假的"
        )
        if call in SCHEMA_PREPARED_STILL_DOES:
            assert call in schema, (
                f"{what}（`{call}`）不在「选 3」那一侧里——选 3 只把**建库与建表**推给"
                "操作员（用户那句「其他由你来完成」），这一件仍然归安装器"
            )
        else:
            assert call not in schema, (
                f"{what}（`{call}`）出现在「选 3」那一侧里——选 3 的全部含义就是"
                "「库和表都建好了」，安装器再建一次库轻则白跑一趟，重则对着一个不是操作员"
                "准备的那个库做了一堆事"
            )

    migration = "'alembic', 'upgrade', 'head'"
    assert code.count(migration) == 1, f"迁移不是恰好一处（{code.count(migration)} 处）"
    assert migration not in prepared and migration not in installer and migration not in schema, (
        "迁移被挪进了某一支分支里——它是这一问里唯一一处刻意的越界"
        "（表结构归这一版程序、库与数据归操作员）。少了它，换了程序文件而表停在上一版时，"
        "登录页照样打得开而其余页面 500，第 6 步的健康检查探的正是登录页那一个端点"
    )

    # 选 2 与选 3 都要自己探一次连接（那是**建库**那一处的 else，两者共用）：不探的话，
    # 一个还没建出来的库会以一段英文 traceback 收场（`Unknown database 'xinliceping'`），
    # 而它本该是一句「你选的那件事还没做完」。
    # 它在**建库那一处的 else**：选 2 与选 3 都以「那个库已经在了」为前提，所以共用这一支。
    # 按「哪一支里有 `pymysql.connect`」去扫会扫空（它在 else 里，不在任何 then 里）——
    # 这正是这一层要按值分派而不是按支去找的理由。
    probe = [other for value, _, other in branches if value == "installer" and other is not None]
    assert len(probe) == 1, f"「建库」那一处的 else 有 {len(probe)} 个（期望恰好 1 个）"
    assert "pymysql.connect" in probe[0], (
        "「建库」那一处的 else 不是连接自检——不探的话，一个还没建出来的库会以一段英文"
        "traceback 收场（`Unknown database 'xinliceping'`），而它本该是一句「你选的那件事"
        "还没做完」"
    )
    assert "《部署说明.txt》" in probe[0], (
        "连接自检失败时没有指向操作员手上那份说明——他在屏幕上找不到下一句话"
    )
    assert code.count("pymysql.connect") == 1, (
        f"连接自检在 install.ps1 里出现了 {code.count('pymysql.connect')} 次（期望 1 次）"
    )
    assert "pymysql.connect" in prepared and "pymysql.connect" in schema, (
        "连接自检没有落在选 2 / 选 3 会走到的那一侧"
    )
    assert "pymysql.connect" not in installer, (
        "连接自检落在选 1 那一侧了——选 1 是安装器自己建库，建完自然连得上"
    )

    # 选 2 时**不问**管理员密码：问了就等于说我们会用它，而这一问刚承诺过不动它。
    # **选 3 问**：它要设，而「设的正是你刚输的那一个」必须由同一处代码保证。
    assert "Read-Secret" not in prepared, (
        "「选 2」那一侧里还在问密码——那一侧刚承诺过不动管理员密码"
    )
    assert "Read-Secret" in installer and "Read-Secret" in schema, (
        "安装器要设管理员密码的那两条路（选 1 / 选 3）里，问密码的那段不见了"
    )

    # 跳过的那两件要说出来，而且要说清后果——**只对选 2 说**：选 3 灌了数据，说了就是假话。
    skipped = [then for value, then, _ in branches if "不写入基础数据" in then]
    assert len(skipped) == 1, "第 4 步那一支没有说清跳过了什么（期望恰好一处）"
    assert "打不开" in skipped[0], (
        "跳过了基础数据却只说「不写入」——没说它的后果（装完打不开任何页面），"
        "而屏幕上那一刻写着的正是「安装完成」"
    )
    assert "不写入基础数据" not in schema, (
        "「选 3 不写入基础数据」这句话是假的——选 3 的全部含义就是**基础数据归安装器**，"
        "操作员读到它会去找《部署说明.txt》那一节手工灌，而那件事安装器刚刚替他做了"
    )


def test_the_database_question_defaults_to_the_installer_and_is_asked_on_both_paths():
    r"""回车 = 1（安装器来准备），而且**两条路都问**。

    默认值两个方向各有代价：默认改成 2 的话，一台全新机器上直接回车装出来的系统
    **打不开任何页面**（库是空的），而屏幕上写着「安装完成」——这一问加进来之前的行为
    正是「安装器来做」，默认值必须回到那里。

    只在一处问也不够：升级不问的话，`runtime\build.json` 里的 `database_mode` 会与
    操作员的认知对不上（他记得自己选过 2，而升级后文件里写着 `installer`），而那个字段
    存在的全部理由就是「装完出故障时第一件要问这个」。两条路各要两半：**问**（`Read-DatabaseMode`）
    与**放进 `$settings`**（`databaseMode = $databaseMode`）——只问不放进 `$settings` 的话，
    第 4 步取到的是空串，`-eq 'prepared'` 全假，安装器会**默默按选 1 做**（写库、灌数据、
    设管理员密码），而那正是选 2 唯一不能发生的事。
    """
    code = code_only(install_text())
    body = function_body(code, "Read-DatabaseMode")

    assert (
        "if ([string]::IsNullOrWhiteSpace($answer) -or $answer.Trim() -eq '1') { return 'installer' }"
        in body
    ), "「直接回车 = 1」这一条不见了：空回答必须落到 installer（那是这一问加进来之前的行为）"
    returns = re.findall(r"return '(\w+)'", body)
    assert returns == ["installer", "prepared", "schema_prepared"], (
        f"这一问的返回值变了：{returns}（只该是这三个，且 installer 在前——那个默认值必须"
        "先被回答，否则「空回答落到 installer」这句话就只是行文的次序）"
    )
    assert "请输入 1、2 或 3。" in body, (
        "重问那一句还写着「1 或 2」——操作员输 3 时会看到一句说他输错了的话，"
        "而他要的那个选项明明就在上面"
    )

    settings_body = function_body(code, "Read-InstallSettings")
    for path, region in (
        ("升级", braced_block(settings_body, "if ($Upgrade)")),
        ("首次安装", settings_body[settings_body.index("$dir = $ResolvedInstallDir"):]),
    ):
        assert "Read-DatabaseMode" in region, f"{path}路径不问这一问"
        assert "databaseMode = $databaseMode" in region, (
            f"{path}那一支问了却没有把它放进 `$settings`——第 4 步取到空串，"
            "`-eq 'prepared'` 全假，安装器会默默按选 1 做"
        )


def test_the_chosen_database_mode_is_recorded_and_read_back_safely():
    r"""选的那一个要**记下来**（第 1 步的日志、`runtime\build.json`），读它要走 `Get-SettingText`。

    记的理由与 `usage` 一模一样，而且是同一处注释写着的：装完出故障时第一件要问的就是
    「这次数据库是谁准备的」，而 `.env` 里看不出来——两种装法的 `.env` 一字不差。

    读法必须走 `Get-SettingText`：升级分支的 `$settings` 里**只有** installDir / usage /
    port，没有这一项，而 `Set-StrictMode -Version Latest` 下**读**一个不存在的键就在读取
    那一刻抛 `PropertyNotFoundException`（2026-09-18 那次失败的形状，见上面那条守卫）。
    走 `Get-SettingText` 之后那个键也不进 `SETTINGS_READS_ON_THE_FRESH_INSTALL_SIDE`
    那张表——`settings_property_reads` 的正则匹配不到 `-Key 'databaseMode'` 这种写法。
    """
    code = code_only(install_text())
    assert "$DatabaseMode = Get-SettingText -Settings $settings -Key 'databaseMode'" in code, (
        "取模式没有走 `Get-SettingText`——升级分支的 `$settings` 里没有这一项，"
        "裸读会抛「在此对象上找不到属性」"
    )
    assert "Write-Log ('数据库由谁准备：' + (Get-DatabaseModeLabel $DatabaseMode))" in code, (
        "第 1 步没有把选的那一个打进日志——出故障时这是第一件要问的事"
    )
    assert '"database_mode": "' in code, "`runtime\build.json` 里没有记下这次是哪一种"
    assert code.count("$DatabaseMode") >= 5, (
        f"只扫到 {code.count('$DatabaseMode')} 处使用，锚八成挪了位置"
    )

    labels = code_only(function_body(code, "Get-DatabaseModeLabel"))
    assert "'prepared'" in labels and "你自己准备" in labels, "码与中文对不上（选 2 那一支）"
    assert "'schema_prepared'" in labels and "库和表你自己准备" in labels, (
        "第三种取值没有自己的中文——`runtime\\build.json` 里记着 `schema_prepared`，"
        "而这一行会打印出选 1 的那句「建库、建表、写基础数据」，正好把操作员做过的两件事"
        "说成是安装器做的"
    )
    assert "「一键安装」来准备" in labels, "选 1 那一支的中文不见了"
    assert labels.rstrip().splitlines()[-1].strip().startswith("return "), (
        "`Get-DatabaseModeLabel` 的收尾不是那个兜底的 return——码认不出时这一行会打印空话"
        "（它读的可能是别的版本写下的 `runtime\\build.json`）"
    )


def test_the_manual_has_the_section_the_installer_sends_the_operator_to():
    r"""安装器点着名字让操作员去看的那一节，真的存在，而且里面的命令与它跳过的是同一批。

    这是**跨文件**的一条。`install.ps1` 里那两句（连接自检失败时、以及首次安装跳过了
    基础数据时）是选 2 这条路上唯一的出路——「照《部署说明.txt》里『数据库我自己准备』
    那一节的三条命令补上」。那一节改名或删掉，这两句就指不到任何地方，而它们只在
    **一台真机上选过一次 2 之后**才会被读到：开发机上永远看不见。

    断的是「那一节独有」的东西：小标题、`app.db.create_database` 这类命令、以及那条
    口令的事（`seed` 建出来的 admin 初始密码是 `123456`，不写出来的话操作员补完了
    基础数据却进不去，而他会以为是安装坏了）。

    命令按 **`-m <模块>`** 的写法断，不按整行：那一节里写着完整的
    `..\runtime\venv\Scripts\python.exe`（那份 python 是装完才存在的，所以要写在安装
    之后跑），路径随安装目录变，断整行等于把 `%LOCALAPPDATA%` 那条单机路径钉死。
    """
    manual = (WINDOWS_ASSETS / "部署说明.txt").read_text(encoding="utf-8-sig")
    code = code_only(install_text())

    # 判据是**小标题那一行**（前后都是换行），不是「文件里出现过这几个字」：那一节
    # 别处还有两处**指向它**的交叉引用（问题清单里、分诊表下面），按「出现过」断言的话，
    # 把标题改掉而引用留着这条照样是绿的——而操作员顺着引用翻过去，那一节已经不存在了。
    assert "\n 数据库我自己准备\n" in manual, (
        "《部署说明.txt》里没有这一节的小标题——安装器那两句指路的话指不到任何地方"
    )
    assert "数据库我自己准备" in code, "安装器不再点着名字让操作员去看那一节"

    for command in MANUAL_COMMANDS:
        assert f"-m {command}" in manual, (
            f"那一节里没有 `-m {command}`——操作员补不齐基础数据，而屏幕上写着「安装完成」"
        )
    # 说明里那三条要与安装器自己跑的是同一批（名字对不上就是两份说法）。
    for spelling in ("app.db.create_database", "'alembic', 'upgrade', 'head'", "app.db.seed"):
        assert spelling in code, f"安装器不再跑 `{spelling}`，而说明里还让操作员去跑它"

    assert "123456" in manual, (
        "那一节没写 `seed` 建出来的 admin 初始密码——补完基础数据的人也进不去，"
        "而他会以为装坏了"
    )
    assert "重置管理员密码.bat" in manual, (
        "那一节没给改密码的出口（磁盘上那个按钮叫「重置管理员密码.bat」）"
    )

    # 这一问在说明里也要有，而且要说清「升级也问」——操作员在升级时看到它才不慌。
    assert "这次数据库由谁准备" in manual, "说明的问题清单里没有这一问"
    assert "装过一次的机器上这一问**照样会问**" in manual, (
        "没写「升级也问」——升级那几步的通告里明明写着「不再问数据库连接」，"
        "两句话在同一个屏幕上对不上"
    )
    # 第三项（2026-09-18 加）也要进问题清单：三个选项里少了它，操作员照着说明答 `3`
    # 会以为自己在答一个说明里不存在的东西。
    assert "3 =" in manual or "3=" in manual, (
        "说明的问题清单里没有第三项——用户要的正是「手工建库建表、其余由安装器完成」，"
        "而说明里只有前两项"
    )


# ---------------------------------------------------------------------------
# 第三种分工：库和表都由操作员准备（2026-09-18 加）
#
# 用户的原话：「我现在可以手工创建数据库，并建立数据表，其他由你来完成」。
# 这一维的技术后果只有一个，其余推论都是它的分支：**手写的建表语句里没有
# `alembic_version`**（那是 Alembic 自己的版本记录表，只有 Alembic 会建）。而迁移是
# 无条件跑的，于是表已经存在时它会在第一条 `ALTER TABLE … ADD COLUMN` 上撞
# `Duplicate column name`——一句英文 MySQL 错，离原因很远。
# ---------------------------------------------------------------------------

# `backend/app/db/ensure_schema.py` 的调用点：**恰好一处、排在迁移之前、不分模式**。
SCHEMA_CHECK_CALL = "'-m', 'app.db.ensure_schema'"


def test_the_schema_is_checked_before_the_migration_runs():
    r"""`app.db.ensure_schema` 恰好一处调用，且排在 `alembic upgrade head` **之前**。

    **次序就是它的全部价值**：它做的事是「看一眼表结构，对得上就把 `alembic_version`
    补上」。挪到迁移之后，迁移已经在第一条 `ADD COLUMN` 上撞了 `Duplicate column name`
    并停下（第 4 步 `Invoke-Python` 不吞退出码），那本账补不补都没有意义了。

    **它刻意不在 `DATABASE_WRITE_COMMANDS` 那张表里**：那张表回答的是「选 2 时安装器
    该不该碰这个库」，而这一件三种选择都要做——它是「表结构归这一版程序」那一半，
    与「谁准备库」无关。放进去的话，选 2 那条守卫会开始要求它「不许出现」，而它正是
    选 2 最需要的一步（选 2 的表也是操作员建的，同样没有版本戳）。
    """
    code = code_only(install_text())
    assert code.count(SCHEMA_CHECK_CALL) == 1, (
        f"`{SCHEMA_CHECK_CALL}` 出现了 {code.count(SCHEMA_CHECK_CALL)} 次（期望恰好 1 次）："
        "它在三种分工下都要跑一次，多一处或漏一处都会让某一种分工失去保护"
    )
    assert SCHEMA_CHECK_CALL not in DATABASE_WRITE_COMMANDS, (
        "`ensure_schema` 被收进「安装器写库的四件事」了——那张表判的是选 2 该不该做，"
        "而这一件三种分工都要做"
    )
    assert order_of(code, SCHEMA_CHECK_CALL) < order_of(code, "'alembic', 'upgrade', 'head'"), (
        "表结构校对排在了迁移**之后**——那时迁移已经撞上 `Duplicate column name` 停下了，"
        "补上那本账也没有意义"
    )


def test_the_empty_database_check_only_warns_and_comes_before_writing():
    r"""灌基础数据之前先看一眼这个库是不是空的，而且**只警告不拦**。

    `app.db.reset_to_baseline --yes` 是无人值守的破坏性脚本：它把这个库清成「只有 admin
    + 量表与基本配置」，名册、测评、关怀档案、审计行一起没。而安装器判「首次还是升级」
    看的是**安装目录在不在**，不是库里有没有东西——「同一个库 + 一个新目录」恰好落进
    这一支，选 3 的人手上的库更常常是**还原来的一份备份**。

    判据三条：**调用点带 `-AllowFailure`**（「只警告不拦」的文本形式）、**排在 `seed`
    之前**（写任何东西之前才谈得上「停下先备份」）、以及那段 WARN **说出了后果与出路**。
    """
    code = code_only(install_text())
    lines = [line for line in code.splitlines() if "'app.db.check_empty'" in line]
    assert len(lines) == 1, f"`app.db.check_empty` 的调用点有 {len(lines)} 处（期望恰好 1 处）"
    assert "-AllowFailure" in lines[0], (
        "空库检查没有带 `-AllowFailure`——用户选的是「只警告不拦」，而不带它的写法会在"
        "一个非空库上直接抛异常、把安装停在第 4 步"
    )
    assert "$emptyCode" in lines[0], (
        "空库检查的退出码没有被接住（`$emptyCode = …`）——`Set-StrictMode -Version Latest`"
        "下读一个没赋过值的变量会抛异常，报出来与「库非空」毫无关系"
    )
    assert order_of(code, "'app.db.check_empty'") < order_of(code, "'-m', 'app.db.seed'"), (
        "空库检查排在了写基础数据**之后**——那时该清的已经清了，那句「按 Ctrl+C 停下先"
        "备份」是句空话"
    )

    warn = install_region(
        code,
        "这个库里已经有数据了。",
        "Invoke-Python '写入基础数据'",
    )
    for needed, why in (
        ("关怀档案", "没说清会被清掉的是什么——「有数据」听起来像只影响那几张表"),
        ("Ctrl+C", "没给出路：此刻还能停下，而屏幕上没有一句话告诉他这件事"),
        ("照常继续", "没说明这一句是提醒不是拦阻——操作员会以为安装失败了"),
    ):
        assert needed in warn, f"空库提醒里少了「{needed}」：{why}"


def test_the_probe_arguments_reach_the_script():
    r"""`Invoke-PythonScript` 要把 `-Arguments` 透传下去，调用点也要用它。

    **这是一个真实存在过的缺陷**：形参原本只有 `([string]$Step, [string]$Name,
    [string]$Content, [switch]$AllowFailure)`，而 `config` 那个探针的调用点把 4 个值甩在
    位置参数尾巴上——它们**按位绑到了 `[switch]$AllowFailure`**（非空数组就是 `$true`），
    脚本一个参数都没收到，`sys.argv[1:5]` 当场抛
    `ValueError: not enough values to unpack (expected 4, got 0)`；而 `AllowFailure` 恰好
    为真 → 不抛、退出码被丢掉 → **那句「.env 口令编解码两端一致」的自检从来没有跑过**。

    所以判据是两半：`Invoke-PythonScript` 的形参里有 `$Arguments` 且真的拼进
    `Invoke-Python` 的实参；调用点用的是 `-Arguments` 而**不是**尾随的值。
    """
    code = code_only(install_text())
    body = function_body(code, "Invoke-PythonScript")
    assert "[string[]]$Arguments" in body, (
        "`Invoke-PythonScript` 没有收参数的形参——位置尾巴上的值会绑到 `[switch]` 上"
    )
    assert "(@($path) + $Arguments)" in body or "$path) + $Arguments" in body, (
        "`$Arguments` 收下了却没有拼进 `Invoke-Python` 的实参——那与没有这个形参是一回事"
    )

    # 调用点：那 4 个值必须**在 `-Arguments` 后面**，而不是裸着排在 here-string 之后。
    probe_call = install_region(code, "Invoke-PythonScript '配置自检' 'config' @'", "-AllowFailure")
    assert "-Arguments @($settings.dbUser" in probe_call, (
        "配置自检的四个参数没有走 `-Arguments`——它们会按位绑到 `[switch]$AllowFailure` 上，"
        "脚本一个都收不到，而这个探针正是那个静默空转了不知多久的自检"
    )
    assert "$configProbeCode = Invoke-PythonScript" in code, (
        "配置自检的退出码没有被接住——`Set-StrictMode -Version Latest` 下那个 `if` 会抛"
        "「在此对象上找不到属性」，而它与自检结果毫无关系"
    )


def config_probe_source() -> str:
    """把 `config` 那个探针的 Python 源码从 here-string 里切出来。

    这样它就能被**真的执行一遍**——install.ps1 里那段 Python 在开发机上永远跑不到
    （本机没有 PowerShell），而它恰恰是那个「从来没跑过」的自检。切出来跑，是这一整套
    资产里唯一能证明它的逻辑还在的办法。
    """
    code = install_text()
    marker = "Invoke-PythonScript '配置自检' 'config' @'\n"
    start = code.index(marker) + len(marker)
    end = code.index("\n'@", start)
    return code[start:end]


def run_config_probe(
    argv: list[str], database_url: str, expected_password: str | None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "XLP_DATABASE_URL": database_url, "PYTHONPATH": str(BACKEND_DIR)}
    if expected_password is None:
        env.pop("XLPSETUP_EXPECT_PASSWORD", None)
    else:
        env["XLPSETUP_EXPECT_PASSWORD"] = expected_password
    return subprocess.run(
        [sys.executable, "-c", config_probe_source(), *argv],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_the_config_probe_round_trips_the_password_and_forgives_a_hosts_case():
    r"""跑一遍那个探针：对得上退出 0，口令对不上退出非 0，**只有主机名的大小写不算错**。

    口令那一半断的是「安装器写进去 → 后端解出来」这条链：`p@ss` 在 `.env` 里是
    `p%40ss`，任何一端漏了百分号编解码，送去认证的就是编码形态的字符串，而 MySQL 报的是
    「Access denied」——与「密码打错了」长得一模一样。这正是那个探针存在的理由。

    主机名那一半是 2026-09-18 修的：`parse_database_url` 走 `urlsplit(...).hostname`，
    它按规范**把主机名转成小写**，而操作员输入 `MySQL01.School.Local` 时逐字比会把他挡在
    「地址对不上」上——他照着屏幕去改 `.env` 只会越改越乱，因为那里本来就是对的。
    库名与用户名**不能**这么放松（MySQL 上的大小写敏感性取决于平台与
    `lower_case_table_names`），所以下面同时断了一个反方向：用户名换了大小写**要**报错。
    """
    url = "mysql+pymysql://root:p%40ss@mysql01.school.local:3306/xinliceping"
    good = ["root", "mysql01.school.local", "3306", "xinliceping"]

    passed = run_config_probe(good, url, "p@ss")
    assert passed.returncode == 0, (
        f"配置对得上而探针没通过：\n{passed.stdout}\n{passed.stderr}"
    )
    assert "配置自检通过" in passed.stdout

    # 主机名只差大小写：**不算错**。`urlsplit` 把它转成了小写，逐字比会误报。
    shouty = ["root", "MySQL01.School.Local", "3306", "xinliceping"]
    assert run_config_probe(shouty, url, "p@ss").returncode == 0, (
        "主机名的大小写被当成错误了——`urlsplit` 按规范转小写，而操作员照着屏幕去改 "
        "`.env` 只会把一个本来就对的值改坏"
    )

    # 口令对不上：**必须报错**，而且不许把口令本身打出来（这段输出会进安装日志）。
    wrong = run_config_probe(good, url, "p@ssword")
    assert wrong.returncode == 1, "口令对不上却通过了——这个探针就是为这一条存在的"
    assert "口令解出来不对" in wrong.stdout
    assert "p@ss" not in wrong.stdout, "探针把口令打印出来了——这段输出会进安装日志"

    # 记不住的那一项（环境变量没带）也要报，不能默默放过。
    missing = run_config_probe(good, url, None)
    assert missing.returncode == 1 and "没有拿到" in missing.stdout, (
        "口令比对的环境变量缺失时探针放行了——那正好会把「没比」说成「比过了」"
    )

    # 反方向：用户名的大小写**要**报错（MySQL 上它的大小写敏感性取决于平台）。
    assert run_config_probe(["Root", "mysql01.school.local", "3306", "xinliceping"], url, "p@ss").returncode == 1, (
        "用户名换了个大小写却通过了——放松的只有主机名这一项"
    )


# ---------------------------------------------------------------------------
# 手工启动这条退路（`manual-start.ps1` + 两枚按钮，2026-09-18 加）
#
# 用户的原话：「如果仍然失败，我计划直接手工操作，分别启动前端后端， 需要什么指令，
# 帮我列举下」，接着是「能不能总结为两个脚本，我直接跑」。
#
# 于是 `deploy/windows/` 下多了一个 `manual-start.ps1`（`-Action backend|frontend`）
# 与两枚纯 ASCII 的按钮。它做的是 `install.ps1` 第 2～4 步之后那几件事，只是拆薄了；
# 与那边的差别**只有两处**，都是有意：清库要先问人（第 3 条钉住），不设管理员密码
# （首次登录用 seed 写的 `123456`，要换就点「重置管理员密码.bat」）。
#
# 与上面那些同源：这些文件在开发机上一行都不会被执行，所以每条约定都要有机器判据。
# **每条判据看不见什么，都写在它自己的 docstring 里**——这一组证明不了 PowerShell 的
# 语义（那要真机），能证明的是「有人把它改回去会红」。
# ---------------------------------------------------------------------------

MANUAL_SCRIPT = WINDOWS_ASSETS / "manual-start.ps1"
MANUAL_BUTTONS = ("手工启动后端.bat", "手工启动前端.bat")


def manual_text() -> str:
    return MANUAL_SCRIPT.read_text(encoding="utf-8-sig")


def manual_declared_actions() -> set[str]:
    match = _VALIDATE_SET_RE.search(manual_text())
    assert match, "manual-start.ps1 里给 $Action 用的 [ValidateSet(...)] 找不到"
    return {item.strip().strip("'\"") for item in match.group(1).split(",") if item.strip()}


def manual_wired_actions() -> dict[str, str]:
    wired = {}
    for name in MANUAL_BUTTONS:
        path = WINDOWS_ASSETS / name
        assert path.is_file(), f"{name} 不在 deploy\\windows\\ 里"
        found = _ACTION_RE.findall(path.read_text(encoding="ascii"))
        assert len(found) == 1, f"{name} 里的 -Action 有 {len(found)} 个，期望恰好 1 个"
        wired[name] = found[0]
    return wired


def test_the_manual_actions_match_the_two_buttons():
    r"""两个方向各断一次：只在脚本里声明、没有按钮的，与按钮请了但脚本不认的。

    写错时的症状是「双击那个 .bat 什么也没发生」——PowerShell 报的是 ValidateSet
    之外的值，而窗口一闪就没了（与 `ops\*.bat` 那一组第 3 条约定同一个坑）。
    """
    declared = manual_declared_actions()
    assert declared == {"backend", "frontend"}, f"动作清单变了：{sorted(declared)}"

    wired = manual_wired_actions()
    assert set(wired.values()) == declared, (
        f"按钮请的动作与脚本声明的不一致：按钮给的是 {sorted(set(wired.values()))}，"
        f"脚本认的是 {sorted(declared)}"
    )
    assert len(set(wired.values())) == len(wired), "两枚按钮请了同一个动作"


def test_the_manual_buttons_point_at_a_script_that_exists():
    r"""按钮用 `%~dp0manual-start.ps1`：`%~dp0` 是**按钮自己所在的目录**，而它就在
    `manual-start.ps1` 旁边（包里两个都在 `<pkg>\deploy\`）。

    这一条与 `ops\*.bat` 那条（第 4 条约定）是同一个坑的**反方向**：那一组被拷到
    安装根，所以不许出现 `%~dp0..`；这一组留在 `deploy\` 里，所以脚本名前面**只能是**
    `%~dp0`，写成 `..` 就到上一级去了。

    脚本名必须是纯 ASCII：`.bat` 里出现中文就可能吞掉紧跟的那个字符（第 2 条约定），
    而**按钮自己叫中文名是允许的**（那是文件名）。正因为按钮名是中文，一个中文脚本名
    在这里会显得很自然——所以这条分工要钉住，而不是靠读的人想起来。
    """
    for name in MANUAL_BUTTONS:
        text = (WINDOWS_ASSETS / name).read_text(encoding="ascii")
        assert 'cd /d "%~dp0"' in text, f"{name} 没有 cd 到自己的目录"
        assert '"%~dp0manual-start.ps1"' in text, f"{name} 没指向 %~dp0manual-start.ps1"
        assert "%~dp0.." not in text, f"{name} 里出现了 %~dp0..（它不在子目录里）"

    assert MANUAL_SCRIPT.is_file(), MANUAL_SCRIPT
    assert MANUAL_SCRIPT.name.isascii(), (
        "脚本名里有非 ASCII 字符，而请求它的那一行只能是 ASCII——"
        "改名的同时要改两枚按钮里的那一处"
    )


def test_the_manual_wipe_asks_first():
    r"""`app.db.reset_to_baseline` 会把库清成「只有管理员 + 量表与基本配置」。

    `install.ps1` 里它是一句无条件执行（除非带 `-KeepData`）——那条路上整件事都在安装器
    手里，而安装器判「首次还是升级」看的是安装目录在不在。手工这条路上不假设任何东西，
    所以它必须排在一次 `Read-Host` **之后**，且**回车那一支是「不清」**：一个破坏性动作
    的默认值不能是破坏。

    网眼：这条判的是 `Start-Backend` 函数体里的**文本位置**，判不了 PowerShell 的语义
    （比如把整段搬进一个新函数再调用）。它能挡住的是最可能发生的那两个改动：把
    `Read-Host` 删掉（一次「少问一句」的顺手清理），或者把两次调用挪到分支外面去。
    """
    body = code_only(function_body(manual_text(), "Start-Backend"))

    assert body.count("reset_to_baseline") == 1, (
        f"清库这件事在这个函数里出现了 {body.count('reset_to_baseline')} 次，期望恰好 1 次"
    )
    ask = body.find("Read-Host")
    wipe = body.find("reset_to_baseline")
    assert ask != -1, "清库之前没有 Read-Host——它现在会不问就清"
    assert ask < wipe, "清库排在了 Read-Host 前面（顺序反了：问完再清）"

    between = body[ask:wipe]
    assert "$answer" in between, "Read-Host 与清库之间没有拿答案做判断——问完照清，等于没问"
    assert "StartsWith('Y')" in between, (
        "判据不是「输入 Y 才清」——那样回车那一支就成不了「不清」"
    )
    assert body.count("'--yes'") == 1, "`--yes` 只该出现在那一处调用上"


def test_the_manual_start_never_routes_python_output_through_powershell():
    r"""**Python 的输出不许经过 PowerShell 的管道。**

    venv 里那个 `sitecustomize.py` 把 Python 的 stdout/stderr 固定成 UTF-8，而
    `& python …` 那种写法会让 PowerShell 把子进程的 stdout **接进管道**、再用
    `[Console]::OutputEncoding`（中文 Windows 上是 cp936）解码——中文变乱码，而且
    **不可逆**（屏幕上已经是替换字符，原始字节当场就丢，紧接着那句「上面是它自己说的话」
    也就不成立了）。

    所以真正的调用一律走 `Invoke-Native` 的 `Start-Process -NoNewWindow`：子进程继承
    控制台，Python 走自己的 Unicode 控制台 API，整条路上没有第二个人在解码。

    两个例外是**只输出 ASCII 的探测**（`& $Exe -c $probe` 与 `& $launcher.Source`）：
    那里要的正是「把输出接回来读一行」，而探针刻意不输出中文（此刻 `sitecustomize.py`
    还不存在，中文会按 locale 写字节）。所以下面这张名单只禁**解释器**那三个变量名。

    变异验证：把 `Invoke-Native` 里那行换成 `& $FilePath @Arguments`，这条会红。
    """
    code = code_only(manual_text())

    banned = re.findall(r"&\s*(\$script:VenvPython|\$VenvPython|\$python|\$base)\b", code)
    assert not banned, (
        f"用 `&` 起了 Python（{banned}）——那会把输出接进 PowerShell 的管道、按控制台"
        "代码页解码，中文立刻变乱码且不可逆。走 `Invoke-Native`"
    )

    body = code_only(function_body(manual_text(), "Invoke-Native"))
    assert "Start-Process" in body, "Invoke-Native 不再用 Start-Process 起子进程"
    assert "NoNewWindow" in body, (
        "少了 -NoNewWindow：那会另开一个窗口，而这个窗口正是服务的控制台"
        "（关窗口 = 停服务，见 install.ps1 那条）"
    )
    assert "ExitCode" in body, "退出码要从 $process.ExitCode 上拿"


def test_the_frontend_action_runs_the_server_that_proxies_the_api():
    r"""前端那一个动作**必须是** `serve_frontend.py`。

    换回 `python -m http.server` 的症状是「登录页打得开、每次登录都失败」：静态服务器
    把 `/api/v1/...` 回成 404 的 HTML，而 `api.ts` 拿它去解 JSON，报出来是
    `Unexpected token '<'`——离真正的原因最远的那种提示（CLAUDE.md §18「前端必须同源」）。

    两条一起断：它跑的是那个文件，而且把后端地址用 `--api` 传了进去。不传的话那个
    服务器会去连它自己的默认端口，与这里从 `.env` 读到的可能不是同一个——症状与
    「后端没起来」长得一样。
    """
    body = code_only(function_body(manual_text(), "Start-Frontend"))
    assert "serve_frontend.py" in body, "前端那个动作不再跑 serve_frontend.py 了"
    assert "'--api'" in body, "serve_frontend.py 没拿到 --api，它会去连自己的默认端口"

    serve = (WINDOWS_ASSETS / "serve_frontend.py").read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in serve.splitlines()
        if line.startswith(("import ", "from "))
    }
    third_party = sorted(imported - set(sys.stdlib_module_names))
    assert not third_party, (
        f"serve_frontend.py 用上了非标准库的东西：{third_party}。它要在一台只有这套包的"
        "机器上跑起来，而那个 venv 里装的是我们钉死的那 31 个 wheel——少一个是运行期才发现"
    )


def test_the_manual_skeleton_lists_the_same_keys_as_the_installer():
    r"""配置缺失时那份骨架里的八项，与 `install.ps1` 的 `$script:EnvFileKeys` **同一份**。

    两份名单漂开的代价不对称：`install.ps1` 那边少一项，升级时会补写出一行它不认识的东西；
    手工这条路少一项，写出来的 `.env` 会让 pydantic 用**默认值**跑——症状是「连的库不是
    我填的那个」，而屏幕上一切正常。

    同时钉住「骨架不写空值」：`XLP_PORT=` 这种空行会让 pydantic 在
    `import app.db.session` 那一刻抛 `int_parsing`（CLAUDE.md §18 记着那次三轮排查），
    而手工写这份骨架的人正是最容易写成空值的那个（他手上只有一个编辑器）。
    """
    install_keys = re.findall(
        r"'(XLP_[A-Z0-9_]+)'",
        re.search(r"\$script:EnvFileKeys\s*=\s*@\((.*?)\)", install_text(), re.DOTALL).group(1),
    )
    assert len(install_keys) == 8, f"install.ps1 的名单不再是 8 项：{install_keys}"

    body = function_body(manual_text(), "Assert-EnvFileIsUsable")
    skeleton = re.findall(r"'(XLP_[A-Z0-9_]+)=", body)
    assert len(skeleton) == len(set(skeleton)) == 8, f"骨架写了 {len(skeleton)} 项：{skeleton}"
    assert set(skeleton) == set(install_keys), (
        f"骨架与安装器的名单不一致——只在安装器里有：{sorted(set(install_keys) - set(skeleton))}；"
        f"只在骨架里有：{sorted(set(skeleton) - set(install_keys))}"
    )

    for line in body.splitlines():
        entry = re.search(r"'(XLP_[A-Z0-9_]+)=(.*)$", line)
        if not entry:
            continue
        # 先剥掉数组项那些**结构字符**（逗号、右括号、引号），剩下的才是值：
        # 不剥的话 `'XLP_PORT=',` 里那个逗号会被读成「值就是 `,`」——第一次写的这一条
        # 就是这么放过了空值（变异验证抓出来的）。
        value = entry.group(2).strip().rstrip(",").rstrip(")").strip().strip("'").strip()
        assert value or "+" in line, (
            f"骨架里写了空值：{line.strip()}——「没有这一行」会用默认值，"
            "「这一行是空的」会在 import 时抛 int_parsing"
        )


def test_the_manual_skeleton_writes_paths_with_forward_slashes():
    r"""`.env` 里的路径一律**正斜杠**，理由与 `install.ps1` 的 `Get-InstallPath` 逐字相同：
    python-dotenv 对带反斜杠的值会做转义处理，而 Windows 的 API 接受正斜杠。

    手工这条路同样要写 `.env`（配置缺失时它写一份骨架），而且**更容易漏**：那边是
    `Get-InstallPath -Relative 'frontend/dist'`（相对路径本来就是正斜杠），这边顺手
    `Join-Path` 拼出来的默认就是反斜杠，而它在屏幕上看起来完全正常。
    """
    text = manual_text()

    body = code_only(function_body(text, "Get-InstallPath"))
    assert r"-replace '\\', '/'" in body, "Get-InstallPath 不再把反斜杠换成正斜杠了"

    skeleton = code_only(function_body(text, "Assert-EnvFileIsUsable"))
    for key in ("XLP_WEB_DIR", "XLP_LOG_DIR"):
        match = re.search(rf"'{key}='\s*\+\s*\(([^)]*)\)", skeleton)
        assert match, f"{key} 不再是 `'{key}=' + (…)` 这个形状，这条守卫看不到它"
        assert "Get-InstallPath" in match.group(1), (
            f"{key} 没有走 Get-InstallPath——它现在会写出一串反斜杠"
        )
        assert "Join-Path" not in match.group(1), (
            f"{key} 用 Join-Path 拼了路径：那给的是反斜杠，而 python-dotenv 会对它做转义"
        )


def test_the_operator_manual_names_the_buttons_as_they_are_on_disk():
    r"""手册里出现的每一个 `xxx.bat` 都得是磁盘上真有的那个名字。

    与 `test_every_bat_the_scripts_name_is_a_file_that_exists` 是同一条教训的第三处
    （那一条管 `.ps1` 里的收尾文案）：**「把看不懂换成了搜不到」——操作员照着纸上的
    名字去目录里翻，翻不到同一个东西**。这一份是他唯一会读的纸，所以它比代码更需要这条：
    代码里写错一个按钮名，最多是那一句日志不好看；手册里写错一个，人就在安装目录里
    找不到那个按钮，而他会以为装坏了。

    判据是**后缀匹配**，不是整串相等：手册里有几处把路径连文件名一起写出来
    （`deploy\手工启动后端.bat`），也有几处是整句话被引号引起来、恰好以文件名结尾。
    相等判定会把它们全误报。

    网眼：它只管「叫得出名字的那几个 .bat」，管不了「说的是哪一件事」——把
    「启动服务.bat」写成「重启服务.bat」它照样绿（两个文件都在）。
    """
    manual = (WINDOWS_ASSETS / "部署说明.txt").read_text(encoding="utf-8-sig")

    known = {path.name for path in WINDOWS_ASSETS.glob("*.bat")}
    known |= {path.name for path in (WINDOWS_ASSETS / "ops").glob("*.bat")}

    seen = set(re.findall(r"[^\s「」，。：、（）()]*\.bat", manual))
    assert len(seen) >= 10, f"只扫到 {len(seen)} 个 .bat 名字，正则八成坏了：{sorted(seen)}"
    unknown = sorted(name for name in seen if not any(name.endswith(item) for item in known))
    assert not unknown, f"手册里提到了磁盘上没有的按钮：{unknown}；磁盘上有的是 {sorted(known)}"


def test_the_operator_manual_has_the_section_the_triage_points_at():
    r"""「网页打不开」那一段把操作员指到手工启动那一节，而那一节真的在，且说了三件事。

    与 `test_the_manual_has_the_section_the_installer_sends_the_operator_to` 同一个形状
    （那一条守的是选 2 的那一节，跨的是 `.ps1` 与这份手册）。两节都**只在一台出了故障的
    机器上**才会被读到，所以开发机上的任何东西都看不见它们有没有失联。

    小标题按**整行**判（前后都是换行），不按「文件里出现过这几个字」：别处还有一处
    指向它的交叉引用，按「出现过」断言的话，把标题改掉而引用留着这条照样是绿的——
    而操作员顺着引用翻过去，那一节已经不存在了。

    断的三件事都是照着做时会撞上的：**先起后端那一个**（前端要复用它的运行环境）、
    **窗口就是服务**（关掉窗口 = 停服务，与「启动服务.bat」同一个语义）、**不许用别的
    静态服务器发这个前端**（那样登录页打得开、每次登录都失败）。加上那条清库的问答——
    拷的是脚本里那一句 `Read-Host` 的原文，因为操作员照手册输入的东西必须正是脚本
    等在等的东西。
    """
    manual = (WINDOWS_ASSETS / "部署说明.txt").read_text(encoding="utf-8-sig")

    heading = "\n 一键安装装不完的时候：包里还有两个「手工启动」按钮\n"
    assert heading in manual, "《部署说明.txt》里没有手工启动那一节的小标题"
    assert "「一键安装装不完的时候」那一节" in manual, (
        "「网页打不开」那一段没有指到这一节——出故障的人只会一路往下看到「实在搞不定」"
    )

    section = manual.split(heading, 1)[1]
    for name in MANUAL_BUTTONS:
        assert name in section, f"那一节里没提 {name}"
    assert "`python -m http.server`" in section, (
        "那一节没有再警告「不要用别的静态服务器发前端」——那样登录页打得开、每次登录都失败"
    )
    assert "关掉窗口" in section and "服务就停了" in section, (
        "那一节没说「窗口在，服务才在」——关掉窗口的人会以为服务还在跑"
    )

    prompt = re.search(r"Read-Host '([^']*)'", code_only(manual_text()))
    assert prompt, "脚本里找不到那句 Read-Host"
    assert "Y 再回车" in prompt.group(1) and "Y 再回车" in section, (
        "脚本问的那一句与手册里写的对不上：手册要写出「输入 Y」，否则操作员会在一个"
        "等他回答的提示上直接按回车（那等于不清），或者以为卡住了"
    )


def test_the_packager_keeps_the_manual_button_and_its_script_together(tmp_path):
    r"""两枚按钮里写的是 `%~dp0manual-start.ps1`——`%~dp0` 是**按钮自己所在的目录**，
    所以这三份文件进了包之后必须还在同一个目录里。

    真的调一次 `copy_windows_assets`（它顶层 import 全是标准库，`load_build_package`
    那一条已经证明过导入它没有副作用），然后只看一件事：按钮与它请的那个脚本同目录。
    拆开的那一天，按钮会去找一个不在旁边的脚本——那是**双击之后一闪就没**的失败，
    在真机上连一句错话都看不到（`.bat` 里的 `pause` 排在 PowerShell 之后，而那时
    PowerShell 还没起来）。

    顺带断 `REQUIRED_PATHS` 里有它们：少一条的话「包里缺了这个文件」要等到**装的时候**
    才发现，而那时操作员正卡在装不下去的那一步上，手里唯一能用的东西就是这两个按钮。

    网眼：它判的是「进了包之后在哪」，判不了那台机器上的执行结果（那要真机）。
    """
    module = load_build_package()
    target = tmp_path / "pkg"
    target.mkdir()
    module.copy_windows_assets(target)

    for name in MANUAL_BUTTONS:
        button = target / "deploy" / name
        assert button.is_file(), f"{name} 没有进包：{sorted(p.name for p in target.rglob('*.bat'))}"
        assert (button.parent / "manual-start.ps1").is_file(), (
            f"{name} 与 manual-start.ps1 不在同一个目录里——按钮里的 %~dp0 找的是它旁边"
        )
    assert (target / "deploy" / "serve_frontend.py").is_file(), "前端那个服务器没有进包"
    assert (target / "一键安装.bat").is_file() and (target / "部署说明.txt").is_file(), (
        "包根那两样不在——手工启动那两枚**刻意**留在 deploy\\ 里（见 ROOT_LEVEL_ASSETS "
        "上面那段注释），但包根自己的两样不能少"
    )

    required = list_literal(build_package_text(), "REQUIRED_PATHS")
    for path in (
        "deploy/manual-start.ps1",
        "deploy/serve_frontend.py",
        "deploy/手工启动后端.bat",
        "deploy/手工启动前端.bat",
    ):
        assert f'"{path}"' in required, f"REQUIRED_PATHS 里少了 {path}"
