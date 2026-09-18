<#
  心晴 · 心理测评与关怀平台 —— Windows 一键安装

  由包根那个「一键安装.bat」调用，操作员只双击那一个。

  这个脚本干的六件事（**第 1 件与第 5 件只对局域网用法成立**，见第 5 条）：
    1. 自己提权（注册计划任务与放行防火墙都需要管理员）——**只对局域网用法**
    2. 在这台电脑上找一个 Python 3.11 x64 建 venv（找不到就在问口令之前停下）
    3. 问一屏问题：怎么用、装到哪、用哪个端口、MySQL 怎么连、管理员密码
    4. 把包里的东西铺到安装目录，用 venv 离线装依赖，写 backend\.env，建库，跑迁移，
       种基线数据，设管理员密码
    5. 注册一条「计算机启动时」触发的计划任务（SYSTEM 身份），重启后自动起来
    6. 起服务并等它健康，然后打印访问地址（**单机用法会开一个可见的窗口**，见第 6 条）

  **目标机上必须有一个 Python 3.11（64 位）。** 2026-09-18 之前包里带着一个内嵌的
  CPython（21MB，占 zip 一半）加一段手写的 wheel 解包器；现在改成用这台电脑上的
  Python 建 venv、从包里的 wheels 离线装依赖。缺 Python 或版本不对时安装会在**问数据库
  口令之前**停下——包里的 31 个 wheel 里有 9 个是 `cp311-cp311-win_amd64`，不含 abi3，
  所以版本这件事没有余地。

  ---------------------------------------------------------------------------
  几条**不要顺手改掉**的地方
  ---------------------------------------------------------------------------

  1. **文件必须是 UTF-8 with BOM。** PowerShell 5.1 没有 BOM 就按当前 ANSI 代码页
     读脚本，中文全部变成乱码，而报出来的错是「字符串缺少终止符」——看不出是编码。

  2. **不要加 `chcp 65001`。** `Write-Host` 走的是 Windows 控制台的 WriteConsoleW
     （Unicode 接口），中文显示与代码页无关；而 `chcp 65001` 恰恰是 PowerShell 5.1
     把输出重定向到文件时变乱码的那个组合。往日志写的那一路显式用 `-Encoding UTF8`。

  3. **stdout 的编码靠 `deploy\sitecustomize.py`，不是靠环境变量。**
     输出接在控制台上时 Python 走 WriteConsoleW 不受代码页影响，但**重定向到文件/管道**
     时它用 locale 编码——在英文版 Windows 上就是 cp1252，那时候这些脚本里的中文会让
     `print` 直接抛 UnicodeEncodeError，安装停在一个跟编码八竿子打不着的位置。

     管这件事的是 `Install-ConsoleEncodingHook` 装进 venv 的那个 `sitecustomize.py`：
     `site` 模块启动时会 import 它，这是 CPython 给的**唯一**一个「每次解释器启动都跑一下」
     的钩子，因此对**每一次**运行都成立——包括 `python -m alembic` 这类我们控制不到的入口，
     也包括用户之后自己双击「启动服务.bat」起的那一次。

     **不要改用 `PYTHONIOENCODING` / `PYTHONUTF8`。** 2026-09-18 之前它们在这套部署里
     确实失效（内嵌 CPython 的 `._pth` 让 `getpath.py` 设了 `use_environment = 0`，
     等于 `-E`），换成 venv 之后它们生效了——但**只对安装进程自己及其子进程生效**：
     用户双击「启动服务.bat」时的环境由他的会话决定，局域网那条路的计划任务由
     Task Scheduler 决定，两处都不是我们能设的。那就成了「装的时候是好的、平时用的那一份
     不是」——正是这套资产反复要消掉的那类不一致。所以第 3 步附近原来那两行
     `$env:PYTHONIOENCODING = 'utf-8'` 也一并删了。

  4. **计划任务用 `*-ScheduledTask` 这条命令集建，不写 task.xml。**
     手写那份 XML 要撞两件事：元素的**顺序**受 schema 的 sequence 约束，错一处就被
     拒绝；`<ExecutionTimeLimit>` 不显式写就是 PT72H，三天后任务被 Task Scheduler
     掐掉。这条命令集生成的正是 Windows 自己认可的那份 XML，两个坑都不存在。
     代价是要 Windows 8 / Server 2012 以上——而这套部署的下限本来就在那儿。

  5. **「单机 / 局域网」这一维叫「用法」（`$Usage`），不叫「模式」。**
     主流程第 1 步末尾已经在打印「`模式：首次安装到 …`」/「`模式：升级现有安装`」，
     那个「模式」说的是另一件事（首次安装还是升级）。同一份日志里出现两行互不相干的
     `模式：`，按关键字找东西的人分不清哪一行是哪一个——而操作员唯一能提供的线索就是
     这份日志。值只有两个：`single`（只有这台电脑用）/ `lan`（老师们都要连过来）。

     两条路**只有下面这几处分叉**，其余（铺文件、建库、迁移、种基线、健康检查）完全共用：
       · 默认安装目录：`%LOCALAPPDATA%\xinliceping` / `C:\xinliceping`
       · 提权：不提 / 提
       · `XLP_HOST`：`127.0.0.1` / `0.0.0.0`
       · 第 5 步：什么都不做 / 注册计划任务 + 放行防火墙
     新增任何一处系统级改动时都要问一句「单机用法是否也需要它」。
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [switch]$KeepData,     # 不清成「只有 admin + 基本配置」，保留种子里的演示账号与名册
    [switch]$Elevated,     # 内部用：标记「已经提过权了」，防止无限递归提权
    # 怎么用这套系统。**它是所有问题里的第一个**，因为它决定要不要提权——而提权必须排在
    # 问数据库口令之前：提权出来的子进程要从第 1 行重跑，想让子进程不再问一遍就得把答案
    # 当参数传下去，而**口令不能进命令行**（任何账号都能用 WMI 读到别的进程的命令行，
    # 环境块读不到。同样的理由写在下面 `set_admin_password` 与 `ops.ps1` 的 reset-admin 里）。
    # 空 = 还没定，交给 Resolve-Usage 去探或去问；提权时原样传给子进程。
    [ValidateSet('single', 'lan')][string]$Usage = '',
    # 内部用：提权出来的子进程沿用父进程的日志文件。
    # 不传的话子进程从第 1 行重跑、按**那时**的时间重新算一个文件名，于是 %TEMP% 里
    # 留下两份 xlp-install-*.log：父进程那份只有表头（它打印完「日志文件：…」就
    # exit 0），真日志在子进程那份——而操作员照开头那句话去拿，拿到的是空白的那一份。
    [string]$LogPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# PS 5.1 的进度条会让 Copy-Item / Expand 慢十倍，而这里拷的正是上万个文件。
$ProgressPreference = 'SilentlyContinue'
$LASTEXITCODE = 0

$TaskName = 'xinliceping'
$FirewallRuleName = 'XinQing Platform 8000'
$PackageRoot = Split-Path -Parent $PSScriptRoot
# 只是**局域网用法**的默认值；单机用法的默认目录由 Get-SingleDefaultDir 算，
# 定了用法之后 `Resolve-Usage` 会把它改掉（第 1 步的升级探测与 `Read-InstallSettings`
# 都读这一个变量，所以改在这里、只有一处）。
$DefaultInstallDir = 'C:\xinliceping'
$DefaultPort = '8000'
$DefaultDbName = 'xinliceping'

# ---------------------------------------------------------------- 日志

# 提权出来的那一份带着 `-LogPath` 回来，父子两个进程写同一个文件；只有最外层那个
# （被「一键安装.bat」调起来的）才在这里新建一个。用 if/else 而不是 `$x = if (...) {…}`：
# 脚本开着 `Set-StrictMode -Version Latest`，少一个分支就是一个未定义变量。
if ($LogPath) {
    $script:LogFile = $LogPath
} else {
    $script:LogFile = Join-Path $env:TEMP ('xlp-install-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
}

function Write-Log {
    param([string]$Message = '', [ValidateSet('INFO', 'OK', 'WARN', 'ERROR')][string]$Level = 'INFO')
    $line = '{0} {1,-5} {2}' -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
    switch ($Level) {
        'ERROR' { Write-Host $line -ForegroundColor Red }
        'WARN' { Write-Host $line -ForegroundColor Yellow }
        'OK' { Write-Host $line -ForegroundColor Green }
        default { Write-Host $line }
    }
    Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
}

function Write-Step {
    param([string]$Title)
    Write-Log ''
    Write-Log ("---- " + $Title + " ----")
}

function Wait-BeforeExit {
    # 退出前等一次回车，**只在提权出来的那个子窗口里**。
    #
    # 为什么要按 `$Elevated` 分：没提权时这个脚本是被「一键安装.bat」调起来的，那个 .bat
    # 结尾自己有一句 `pause`；这里再等一次，操作员要按两下回车，而且分不清哪一下才是
    # 「装完了」。提权出来的子窗口则是**另一个进程**，.bat 管不到它——它一退出窗口就
    # 没了，而操作员全程在看的恰恰是它（父窗口打完「接下来会弹出一个授权窗口」就没事干了）。
    # 于是：恰好一处，等恰好一次。
    if ($Elevated) { Read-Host '按回车键关闭这个窗口' }
}

function Stop-WithError {
    param([string]$Message)
    Write-Log $Message 'ERROR'
    Write-Log ("详细日志：" + $script:LogFile) 'ERROR'
    Wait-BeforeExit
    exit 1
}

# ---------------------------------------------------------------- 交互小工具

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Read-WithDefault {
    param([string]$Prompt, [string]$Default)
    $answer = Read-Host ('{0}（直接回车 = {1}）' -f $Prompt, $Default)
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer.Trim()
}

function Read-Secret {
    param([string]$Prompt)
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function New-RandomHex {
    param([int]$Bytes = 48)
    $buffer = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return -join ($buffer | ForEach-Object { $_.ToString('x2') })
}

# ---------------------------------------------------------------- 用法

function Get-SingleDefaultDir {
    # 单机用法的默认安装目录：`%LOCALAPPDATA%\xinliceping`。
    #
    # **`%LOCALAPPDATA%` 是「当前这个身份」的**，而这里的两种出错情形要说准（写错过一版，
    # 说的是 `systemprofile` —— 那是**以 SYSTEM 身份**跑才有的，这个安装器从来不以 SYSTEM 跑）：
    #   · 同一个账号过 UAC（右键「以管理员身份运行」）→ **它与不提权时一模一样**，默认目录
    #     并不会变；这一支的危险在于下面那条 WARN 会把人说糊涂。
    #   · 换一个管理员账号过肩提权 / `runas` → 那时它是**那个账号**的
    #     `C:\Users\<管理员>\AppData\Local`，装完用户在自己的资源管理器里找不到。
    # 所以这个值要在**提权之前**算出来，并且主流程里有一条 WARN 把实际算出来的路径
    # 明说出来（比讲道理有用：每个人都会看「直接回车 = 什么」）。
    #
    # 目录名保持 ASCII：robocopy / MySQL 客户端 / 写进 .env 的那条路径都少一类编码问题。
    $base = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($base) -and $env:USERPROFILE) {
        # 退回 `%USERPROFILE%\AppData\Local`，**不是 `%USERPROFILE%` 本身**——那是用户的
        # 主目录，把一个应用目录摊在那儿既显眼又容易被误删。
        $base = Join-Path $env:USERPROFILE 'AppData\Local'
    }
    if ([string]::IsNullOrWhiteSpace($base)) {
        # 到这里两个环境变量都没有。不猜一个路径出来（猜出来的地方用户多半不想要），
        # 也不让 `Join-Path` 抛一句英文红字——那句话会绕过日志，而日志是操作员唯一的凭据。
        Stop-WithError '读不到当前用户的 LOCALAPPDATA，单机用法装不了。请改用「2 = 老师们都会连过来」，或手工指定安装目录。'
    }
    return (Join-Path $base 'xinliceping')
}

function Get-UsageLabel {
    param([string]$Value)
    if ($Value -eq 'single') { return '单机（只有这台电脑用）' }
    return '局域网（老师们都会连过来）'
}

function Get-DatabaseModeLabel {
    # 第 1 步把这一行打进日志，`runtime\build.json` 也记同一个码——出故障时第一件要问的
    # 就是「这次是哪种」。（`Get-UsageLabel` 是它的同族：一个码，一句给人看的中文。）
    param([string]$Value)
    if ($Value -eq 'prepared') { return '你自己准备（安装器不建库、不写基础数据、不动管理员密码）' }
    if ($Value -eq 'schema_prepared') { return '库和表你自己准备（安装器只校对表结构、写基础数据、设管理员密码）' }
    return '「一键安装」来准备（建库、建表、写基础数据、设管理员密码）'
}

function Read-BuildUsage {
    # 从一个已经装好的目录里问出「当初是按哪种用法装的」。
    # 读不到、或者那份 build.json 早于这次改动（还没有 usage 字段）→ 返回空串。
    param([string]$InstallDir)

    $path = Join-Path $InstallDir 'runtime\build.json'
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    try {
        $json = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return ''
    }
    if ($json.PSObject.Properties.Name -contains 'usage') { return [string]$json.usage }
    return ''
}

function Read-Usage {
    Write-Log ''
    Write-Log '这套系统怎么用？这一问决定后面要不要管理员权限。'
    Write-Log '  1 = 只有这台电脑用：装在你自己的目录里，不需要管理员，也不放行防火墙；'
    Write-Log '      要用的时候双击「启动服务.bat」，关机就停。'
    Write-Log '  2 = 老师们都会连过来：装到 C:\xinliceping，需要管理员权限，'
    Write-Log '      会注册开机自启，并放行防火墙的端口。'
    while ($true) {
        $answer = Read-Host '选 1 还是 2（直接回车 = 1）'
        if ([string]::IsNullOrWhiteSpace($answer) -or $answer.Trim() -eq '1') { return 'single' }
        if ($answer.Trim() -eq '2') { return 'lan' }
        Write-Log '  请输入 1 或 2。' 'WARN'
    }
}

function Resolve-Usage {
    <#
      定下这次安装用哪种用法。**只有首次安装才问。**

      已经装过的那台机器上一律**沿用**上次的用法，不再问。换用法意味着「删掉计划任务 +
      防火墙规则」或者反过来建它们，那是**迁移**不是升级——半路换会把上一次的东西留成
      孤儿（一条谁也说不清来路的防火墙规则，或者一个再也不会被启动的计划任务）。
      要换用法就先把上一次的卸掉。`deploy\README.md` 的「重跑即升级」一节记着这一条。

      判据按顺序：参数 → `-InstallDir` 指的那个目录 → 两个默认目录哪个装过 → 问。
    #>

    if ($Usage) { return $Usage }

    if ($InstallDir) {
        $fromDir = Read-BuildUsage -InstallDir $InstallDir
        if ($fromDir) {
            Write-Log ('沿用上次的用法：' + (Get-UsageLabel $fromDir) + '（' + $InstallDir + '）')
            return $fromDir
        }
        if (Test-Path -LiteralPath (Join-Path $InstallDir 'backend\.env')) {
            # 这份安装早于「单机用法」，build.json 里还没有 usage 字段。
            # 它当初装的只可能是局域网那一种。
            Write-Log '沿用上次的用法：局域网（这份安装早于「单机用法」，它只可能是局域网）'
            return 'lan'
        }
        return (Read-Usage)
    }

    # 单机那个候选**算不出来就跳过**，不能让它把局域网那条路也带下去。
    # `Get-SingleDefaultDir` 在两个环境变量都没有时走的是 `Stop-WithError` ——
    # 那是 `exit 1`，**不是 throw**，所以下面的 try/catch 接不住它：它会直接终止整个
    # 安装流程。而这里只是「探一探哪个目录装过」，一台只想按局域网装的机器不该因为
    # 读不到 LOCALAPPDATA 就装不上。真选了单机时，第 1 步那次调用会以同样的话停下来，
    # 那才是该出现的地方。
    $candidates = @(@{ usage = 'lan'; dir = $DefaultInstallDir })
    $singleDir = ''
    try { $singleDir = Get-SingleDefaultDir } catch { $singleDir = '' }
    if (-not [string]::IsNullOrWhiteSpace($singleDir)) {
        $candidates += @{ usage = 'single'; dir = $singleDir }
    }
    $installed = @($candidates | Where-Object { Test-Path -LiteralPath (Join-Path $_.dir 'backend\.env') })
    if ($installed.Count -eq 1) {
        Write-Log ('沿用上次的用法：' + (Get-UsageLabel $installed[0].usage) + '（' + $installed[0].dir + '）')
        return $installed[0].usage
    }
    if ($installed.Count -gt 1) {
        # 两个默认目录里各装过一次。取局域网那一个（与这次改动之前的行为一致）；
        # 要升级另一份，显式传 -InstallDir。
        Write-Log '这台机器上两个默认目录都装过，这次按局域网那一个继续（要升级另一份请用 -InstallDir 指定）。' 'WARN'
        return 'lan'
    }
    return (Read-Usage)
}

function Test-InstallDir {
    <#
      安装目录是**操作员手输的**，所以这里挡的是「手输会输出来的那种东西」。

      只管纯字符串，不去看文件系统：目录多半还不存在。

      · `%` 与 `!` —— 在 .bat 与 cmd 里有特殊含义，而这个路径会出现在我们生成的那些
        三行 .bat 的注释里；`&` `|` `<` `>` `^` 同理（`&` 会切断一条命令）。
      · 结尾的空格与点 —— Windows 会在某些 API 上悄悄把它们吃掉，于是同一个目录在
        两处显示成两个不同的名字，而 `icacls` / `robocopy` 认的是消掉之后的那个。
      · 含 `\\?\` 或 `\\.\` —— 设备命名空间前缀。它不是目录，是一整套另一套解析规则。
      · 非本地盘（UNC `\\server\share`）—— SYSTEM 没有映射盘、也不该去连别的机器。

      **中文路径是允许的**：`D:\心理测评平台` 完全没问题（Python 3.6 起 Windows 上
      文件系统编码就是 UTF-8，robocopy / icacls / schtasks 也都是 Unicode 程序）。
      这里不拦它——一所中国学校里让操作员「只能用英文路径」是自找麻烦。

      （「Unicode 程序」说的是**文件名与命令行参数**走宽字符 API，所以中文路径传得进去。
      **别把它读成「它们的输出也是 UTF-8」**——重定向到文件时写的是控制台代码页，
      见 `Read-NativeOutput` 的注释。）
    #>
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        Stop-WithError '安装目录不能为空。'
    }
    foreach ($bad in @('%', '!', '&', '|', '<', '>', '^', '"')) {
        if ($Path.Contains($bad)) {
            Stop-WithError ("安装目录里不能出现 $bad 这个字符：$Path`n    换一个只有字母、数字、中文、空格、连字符的路径就行。")
        }
    }
    if ($Path.StartsWith('\\')) {
        Stop-WithError ("安装目录要在**本机**的磁盘上，不能是网络路径：$Path`n    形如 D:\xinliceping 或 C:\xinliceping。")
    }
    if ($Path -match '^\s*[A-Za-z]:?$') {
        Stop-WithError ("安装目录不能就是整个盘：$Path`n    请给它一个自己的文件夹，比如 D:\xinliceping。")
    }
    if ($Path.EndsWith(' ') -or $Path.EndsWith('.')) {
        Stop-WithError ("安装目录不能以空格或句点结尾（Windows 会把它们去掉，于是同一个目录有两个名字）：$Path")
    }
}

# ---------------------------------------------------------------- 跑 Python

function Quote-Argument {
    <#
      `Start-Process -ArgumentList` 只是把数组用空格接起来，**不做转义**。所以一个带
      空格的参数会被 Windows 拆成两个——而 `-c "import a, b"` 那种参数里正好有空格。
      这里按 CreateProcess 的规矩加引号：反斜杠只在紧跟引号时才需要翻倍。
    #>
    param([string]$Value)
    if ($Value -eq '' -or $Value -match '[\s"]') {
        $escaped = $Value -replace '(\\*)"', '$1$1\"'
        return '"' + ($escaped -replace '(\\+)$', '$1$1') + '"'
    }
    return $Value
}

function Invoke-Python {
    <#
      跑一步 Python，把它的输出**原样**念出来并记进日志。

      刻意用 Start-Process 收下 stdout/stderr 再打印，而不是直接 `& python ...`：
      直接跑的话输出不经过我们，出错时的那段 traceback 就没有落进日志文件，
      而操作员能提供的东西只有那个日志文件。

      返回值是退出码；`-AllowFailure` 时不抛。

      `-Exe` 只有**建 venv 那一步**会用（那时 `$script:PythonExe` 还不存在，要跑的是
      这台电脑上的 base python）。空则回落 `$script:PythonExe`，所以其余调用点一行不变。

      `-WorkingDirectory` 同理：默认 `$script:BackendDir`，而 `Resolve-BasePython` 探
      base python 时那一步跑在第 1 步**之前**、安装目录还没定下来，`backend\` 也不存在，
      所以它显式传 `-WorkingDirectory $script:TempDir`。**不要改成「目录不存在就自动回退」**
      ——那会让 `-m alembic` 那类调用在 `backend\` 意外缺失时报出一句
      「No module named alembic」而不是「那个目录不在」，把原因藏起来。
    #>
    param(
        [string]$Step,
        [string[]]$Arguments,
        [switch]$AllowFailure,
        [string]$Exe = '',
        [string]$WorkingDirectory = ''
    )

    $exeToUse = if ($Exe) { $Exe } else { $script:PythonExe }
    $workDir = if ($WorkingDirectory) { $WorkingDirectory } else { $script:BackendDir }
    # 把用的是哪个解释器一并记下来：`-Exe` 只在一步上生效，而「这一步跑的是哪一个
    # python」恰恰是出问题时第一个要回答的问题（base 还是 venv、32 位还是 64 位）。
    Write-Log ("> " + $exeToUse + " " + ($Arguments -join ' '))
    $outFile = Join-Path $script:TempDir 'stdout.txt'
    $errFile = Join-Path $script:TempDir 'stderr.txt'
    $quoted = @($Arguments | ForEach-Object { Quote-Argument $_ })
    $process = Start-Process -FilePath $exeToUse -ArgumentList $quoted `
        -WorkingDirectory $workDir -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $outFile -RedirectStandardError $errFile

    foreach ($file in @($outFile, $errFile)) {
        if (Test-Path -LiteralPath $file) {
            Get-Content -LiteralPath $file -Encoding UTF8 | ForEach-Object {
                if ($_ -ne '') { Write-Log ('    ' + $_) }
            }
        }
    }

    if ($process.ExitCode -ne 0 -and -not $AllowFailure) {
        throw "$Step 失败（退出码 $($process.ExitCode)）。上面那段输出里通常写着原因。"
    }
    return $process.ExitCode
}

function Invoke-PythonScript {
    <#
      把一个临时脚本写进 TempDir 再跑它。

      刻意不用 `python -c "…"`：`-c` 的内容要穿过 PowerShell 的数组 → `Quote-Argument`
      → CreateProcess 的命令行 → Python 的参数解析四层，中间任何一层对引号的理解不同都会
      变成一句莫名其妙的 `SyntaxError`。写成一个文件就只有一个参数（路径），没有引号可谈。
      这些脚本都是一次性的，落在 TempDir 里由 `finally` 一起删掉。

      ---------------------------------------------------------------------
      ★★ **探针必须自己带 `PYTHONPATH`，这是最容易漏的一处。**
      ---------------------------------------------------------------------
      `python 脚本.py` 把 `sys.path[0]` 设成**脚本自己所在的目录**（也就是 TempDir），
      **不是当前工作目录**——所以 `Invoke-Python` 里那个
      `-WorkingDirectory $script:BackendDir` 对探针**一点用都没有**。

      「那 `-m` 呢」——`python -m app.db.seed` 是另一条路，`-m` 下 CWD 会进 `sys.path`，
      而且 `-WorkingDirectory` 是设了 CWD 的，所以 `-m` 那些调用不需要这个环境变量。
      **两条路的差别就在这儿**，而这里四个探针（`依赖检查` / `config-show` / `config` /
      第 4 步那个 `dbcheck`）都要 `import app.*`，走的正是**没有 CWD** 的那一条。

      2026-09-18 之前它们能跑，靠的是内嵌 CPython 的 `python311._pth` 里那行
      `..\backend`（那行被删掉了，相应地这条也补上了）。不补的话第 4 步会以一句
      `ModuleNotFoundError: No module named 'app'` 停住——而那句话离原因（脚本的
      `sys.path[0]` 语义）隔着好几层。

      `try/finally` 把原值放回去：这个进程后面还要起服务，**不能让一个给探针用的
      环境变量漏进服务进程**。

      ---------------------------------------------------------------------
      ★★ **脚本的参数从 `-Arguments` 走，不能当成多余的实参往后排**（2026-09-18 修）。
      ---------------------------------------------------------------------
      这是个**真实存在的旧缺陷**：`config` 那个探针要 4 个参数（用户名/主机/端口/库名），
      而调用点写的是

          Invoke-PythonScript '配置自检' 'config' @'…'@ @($settings.dbUser, …)

      ——那 4 个值按位绑到了 `[switch]$AllowFailure` 上（非空数组就是 `$true`），
      脚本一个参数都没收到，`sys.argv[1:5]` 当场抛
      `ValueError: not enough values to unpack (expected 4, got 0)`。而 `AllowFailure`
      恰好为真 → **退出码被丢掉、不抛异常**，于是那句「.env 口令编解码两端一致」的自检
      **从来没有真正跑过**，日志里只留一段没人看的 traceback。一整个自检静默空转。

      调用方因此**必须**用 `-Arguments @(…)`（而不是把值甩在位置参数尾巴上），
      `test_windows_assets.py` 有一条守卫钉住「参数确实透传下去了」。
    #>
    param([string]$Step, [string]$Name, [string]$Content, [string[]]$Arguments = @(), [switch]$AllowFailure)

    $path = Join-Path $script:TempDir ('probe-' + $Name + '.py')
    # `Set-Content -Encoding UTF8` 在 PS 5.1 上**带 BOM**，而 Python 读 `.py` 正好认它。
    Set-Content -LiteralPath $path -Value $Content -Encoding UTF8

    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $script:BackendDir
    try {
        return Invoke-Python $Step (@($path) + $Arguments) -AllowFailure:$AllowFailure
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
}

function Get-PythonCandidates {
    <#
      按「越可能长期可用」的顺序列出候选 `python.exe` 的路径（可能重复、可能不存在，
      由 `Resolve-BasePython` 逐个去重与验证）。

      **顺序是有意义的。** 一台机器上可能装了好几个 3.11：机器级一个、当前用户一个、
      应用商店一个。venv 的 `pyvenv.cfg` 会把选中的那个的**绝对路径**烤进去（`home`），
      而局域网用法下计划任务是以 SYSTEM 身份跑的——所以选中的那一个越「机器级、稳定」
      越好。用户级那一份会随「那个 Python 自己更新」「那个账号被删」一起消失，而症状是
      服务起不来、日志里全是 `pyvenv.cfg` 相关的英文，没人会联想到「有人升级过 Python」。
      这一条与 `Resolve-BasePython` 末尾那条 WARN 是同一件事的两半。

      **刻意不读 `WOW6432Node`**：那是 32 位注册表视图，里面的解释器一定是 32 位的。

      **刻意不用 `py -0p` 去枚举**（它列的其实就是同一批 PEP 514 注册项）：那要解析原生
      工具的输出，而那个输出里可能带着用户名（`C:\Users\张三\...`），于是要么按控制台
      代码页解（英文版 Windows 上 cp1252 解 GBK 字节得到乱码）要么按 UTF-8 解（反方向
      同样错）——两条路都会把候选路径变成一个坏字符串，而失败会推迟到某一步「找不到
      文件」。注册表是 PowerShell 自己读的，没有这一层。同样的道理见 `Read-NativeOutput`。
    #>
    $candidates = [System.Collections.Generic.List[string]]::new()

    function Add-Candidate([string]$Path) {
        if ($Path) { $candidates.Add($Path) }
    }

    # 1. 注册表（PEP 514）。**机器级（HKLM）排在用户级（HKCU）前面。**
    foreach ($key in @('HKLM:\SOFTWARE\Python\PythonCore\3.11\InstallPath',
                       'HKCU:\SOFTWARE\Python\PythonCore\3.11\InstallPath')) {
        foreach ($name in @('ExecutablePath', '')) {
            try {
                $value = (Get-ItemProperty -LiteralPath $key -Name $name -ErrorAction Stop).$name
            } catch {
                continue
            }
            Add-Candidate ([string]$value)
        }
    }

    # 2. 机器级的常见安装位置
    if ($env:ProgramFiles) {
        Add-Candidate (Join-Path $env:ProgramFiles 'Python311\python.exe')
    }
    # **32 位那一份也列进来，是有意的**：列进来才会被跑到、才会拿到「这是 32 位的」那句
    # 诊断。漏掉它的话一台只装了 32 位 3.11 的机器会得到「没找到 Python 3.11」——
    # 而这两个诊断的处置完全不同（一个去装 64 位版，一个去查为什么没装上）。
    if (${env:ProgramFiles(x86)}) {
        Add-Candidate (Join-Path ${env:ProgramFiles(x86)} 'Python311\python.exe')
    }

    # 3. 当前用户的常见安装位置（python.org 安装器默认就是「Install for me only」）
    if ($env:LOCALAPPDATA) {
        Add-Candidate (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
        # 应用商店那一份：会被 `Resolve-BasePython` 在**执行之前**按 `WindowsApps` 排掉。
        Add-Candidate (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python.exe')
    }

    # 4. PATH。**最后**才看它：PATH 上的那一个最容易是用户级或某个软件顺带带进来的。
    foreach ($command in @(Get-Command 'python.exe' -All -ErrorAction SilentlyContinue)) {
        if ($command.Source) { Add-Candidate ([string]$command.Source) }
    }

    return $candidates
}

function Resolve-BasePython {
    <#
      在这台电脑上找一个**能用来建 venv 的 CPython 3.11 x64**，把它的全路径存进
      `$script:BasePython`。一个都没有就 `Stop-WithError`。

      跑在**问任何口令之前**（见主流程里它的调用处），所以「这台机器没有 Python 3.11」
      会在操作员开始输密码之前就说出来。

      ---------------------------------------------------------------------
      判据为什么是「真跑一次」而不是读版本号
      ---------------------------------------------------------------------
      `(Get-Item $exe).VersionInfo` 报的是那个 exe 自己的版本资源。对 python.org 装的
      解释器它碰巧是对的，但对应用商店的别名、对启动器、对别的软件改名带进来的那份
      都不作数——问不出「这是不是一个能用的 3.11」。所以让 **Python 自己**判。

      **不在 PowerShell 里解析它的输出**：那要穿四层引号，`Invoke-PythonScript` 的
      docstring 里已经记着这条教训。判据全部走**退出码**，一个字符都不解析。

      ---------------------------------------------------------------------
      ★ 探测脚本的输出必须是**纯 ASCII**（而且这里干脆不输出）
      ---------------------------------------------------------------------
      这一步跑的是**基础 Python**，而 `sitecustomize.py` 是第 2 步装进 venv 的、此刻
      还不存在。于是它的 stdout 按 locale 编码写字节（英文版 Windows 上 cp1252），
      而 `Invoke-Python` 那一路是按 `-Encoding UTF8` 读回来的——**写进去的字节如果是
      中文就当场变成 U+FFFD，且不可逆**（`C:\Users\张三\` 会读成一个坏路径，之后每一步
      都以「找不到文件」失败）。所以：中文提示与路径**一律留在 PowerShell 侧**
      （`Write-Log` 是 UTF-8 落盘的，中文没问题），探测脚本只说 ASCII。
      顺带一个理由：脚本里真写了中文的话，在英文版 Windows 上 `print` 会直接抛
      `UnicodeEncodeError`，探针自己先死。

      ---------------------------------------------------------------------
      退出码的含义（下面 switch 里逐条对应一句中文）
      ---------------------------------------------------------------------
        0 = 就是它      1 = 不是 3.11      2 = 32 位
        3 = 没有 venv / ensurepip（精简版）    其它 = 探针没跑起来
    #>
    $probe = @'
import sys

# 只有 ASCII，而且只有一行。理由见 Resolve-BasePython 的 docstring。
print(sys.version.split()[0])

if sys.version_info[:2] != (3, 11):
    raise SystemExit(1)
if sys.maxsize <= 2 ** 32:
    raise SystemExit(2)
try:
    import venv
    import ensurepip
except Exception:
    raise SystemExit(3)
raise SystemExit(0)
'@

    $probePath = Join-Path $script:TempDir 'probe-base-python.py'
    Set-Content -LiteralPath $probePath -Value $probe -Encoding UTF8

    Write-Log '在这台电脑上找 Python 3.11（64 位）'
    $rejected = [System.Collections.Generic.List[string]]::new()
    $seen = @{}

    foreach ($candidate in @(Get-PythonCandidates)) {
        $full = ''
        try {
            $full = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path
        } catch {
            continue   # 这个位置没有东西，本来就不算候选
        }
        $key = $full.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $item = Get-Item -LiteralPath $full -ErrorAction SilentlyContinue

        # ★★ **这两道排除必须排在「跑它」之前。** 应用商店的 `python.exe` 是一个 0 字节的
        # reparse point：**执行它会弹出应用商店窗口并挂在那里等**，而不是报错。
        if ($full -like '*\WindowsApps\*') {
            $rejected.Add($full + ' —— 这是应用商店的别名，不是真的解释器（跳过，不去执行它）')
            continue
        }
        if (-not $item -or $item.PSIsContainer) {
            $rejected.Add($full + ' —— 不是一个文件（跳过）')
            continue
        }
        if ($item.Length -eq 0) {
            $rejected.Add($full + ' —— 0 字节，多半是应用商店别名（跳过）')
            continue
        }

        # 探测脚本是自包含的（只 import sys / venv / ensurepip），所以它**不需要** base
        # python 找到 `app`——也就不走 `Invoke-PythonScript`（那一个会给探针设 PYTHONPATH）。
        # 工作目录显式给 TempDir：这一步跑在第 1 步之前，`$script:BackendDir` 还是空的。
        $code = Invoke-Python -Exe $full -WorkingDirectory $script:TempDir `
            -Step ('试 ' + $full) -Arguments @($probePath) -AllowFailure

        # 用 if / elseif 而不是 switch：PowerShell 的 `break` 在 `switch` 里是**跳出
        # switch** 而不是跳出外面那个 foreach，写起来很容易看错（而看错的表现是
        # 「第一个候选不管行不行都被选中」）。
        if ($code -eq 0) {
            $script:BasePython = $full
            Write-Log ('用它：' + $full) 'OK'
            break
        } elseif ($code -eq 1) {
            $rejected.Add($full + ' —— 不是 3.11（它自己报的版本号在上面那两行日志里）')
        } elseif ($code -eq 2) {
            $rejected.Add($full + ' —— 是 3.11，但是 32 位的（要 64 位）')
        } elseif ($code -eq 3) {
            $rejected.Add($full + ' —— 里面没有 venv / ensurepip（精简版或被裁剪过的）')
        } else {
            $rejected.Add($full + " —— 探测脚本没跑起来（退出码 $code）")
        }
    }

    if (-not $script:BasePython) {
        # 每个候选与它被拒的原因**逐条进日志**：日志是操作员唯一能发回来的东西，
        # 而「找过哪些地方、各是因为什么被拒」正是下一个人要判断的东西。
        Write-Log '找过这些地方：' 'ERROR'
        if ($rejected.Count -eq 0) {
            Write-Log '  · 一个候选都没有：这台机器上大概从没装过 Python' 'ERROR'
        } else {
            foreach ($item in $rejected) { Write-Log ('  · ' + $item) 'ERROR' }
        }
        Stop-WithError (@(
            '这台电脑上找不到可用的 Python 3.11（64 位），装不下去。',
            '',
            '请到 python.org 下载 Python 3.11 的 Windows installer (64-bit) 装上，',
            '安装时勾上「Add python.exe to PATH」，然后重跑一次「一键安装.bat」。',
            '',
            '为什么必须是 3.11：包里的依赖是按 3.11 编译的（31 个 wheel 里有 9 个是',
            'cp311-cp311-win_amd64，不含 abi3），换别的版本装不上。',
            '为什么必须是 64 位：32 位解释器加载不了这些 64 位的扩展模块。',
            '',
            '上面这几行把找过的每个位置和它被拒的原因都列出来了，对照着看。'
        ) -join [Environment]::NewLine)
    }

    # --- 局域网那条路的额外风险提示（**只警告，不拦**）---
    # venv 的 `pyvenv.cfg` 里 `home` 是写死的绝对路径。选中的如果是一个**用户级**的
    # Python（`%LOCALAPPDATA%\Programs\...`，python.org 安装器的默认选项），那局域网用法
    # 下以 SYSTEM 跑的计划任务是在依赖**某个人的** profile。会崩的形态：那个 Python 自己
    # 更新 / 有人卸载升级 3.11 / 那个账号被删或被换。
    # 只装了用户级 Python 的测试机仍然该装得上，所以这里不拦，只给一句能照做的指引。
    if ($Usage -eq 'lan' -and $env:LOCALAPPDATA -and
        $script:BasePython.ToLowerInvariant().StartsWith($env:LOCALAPPDATA.ToLowerInvariant())) {
        Write-Log ''
        Write-Log '提醒：选中的这个 Python 装在当前用户的目录下。' 'WARN'
        Write-Log ('  ' + $script:BasePython) 'WARN'
        Write-Log '  局域网用法下服务是开机时以 SYSTEM 身份起的，它读的正是这个路径。' 'WARN'
        Write-Log '  那个 Python 一旦被更新、卸载，或者这台机器换了登录用户，服务就起不来了。' 'WARN'
        Write-Log '  建议改用 python.org 的安装包选「Install for all users」装到' 'WARN'
        Write-Log '  C:\Program Files\Python311，再重跑一次一键安装。' 'WARN'
        Write-Log ''
    }
}

function Read-NativeOutput {
    <#
      读一个**原生工具**写到文件里的输出（robocopy / icacls）。

      **不能用 `-Encoding UTF8`。** 这些工具往重定向的文件里写的是**控制台代码页**
      （中文 Windows 上是 cp936）——只有挂在控制台上、走 `WriteConsoleW` 那条路才是宽
      字符，而 `Start-Process -RedirectStandardOutput` 起的进程没有控制台。按 UTF-8 解会
      把每个中文字变成 U+FFFD，而且**不可逆**：写进日志的已经是替换字符，原始字节当场就
      丢了。

      后果不是「难看」。`Invoke-Native` 那句 throw 承诺「上面是它自己说的话」，而操作员
      能做的事只有把这份日志发回来。2026-09-18 那次安装中断就是这么被读成了
      `�ѳɹ����� 1 ���ļ�`——它本来是「已成功处理 1 个文件」。

      按控制台代码页解没有歧义：中文 Windows 上 ACP 与 OEMCP 都是 936，而 `chcp 65001`
      被本脚本第 2 条约定明令禁止，所以 `[Console]::OutputEncoding` 就是系统默认那一个。
      这里**只读不改**它。

      **Python 子进程那一路不是这样**（`sitecustomize.py` 把它的 stdout 固定成 UTF-8），
      见 `Invoke-Python` 里的 `-Encoding UTF8`。两条路各有各的编码，不要互相看齐。
    #>
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) { return '' }
    return [Console]::OutputEncoding.GetString($bytes)
}

function Invoke-Native {
    <#
      跑一个非 Python 的外部命令（**robocopy / icacls**，只有这两个），失败时把它的
      **原始输出**打出来。这一条是刻意的：这类工具报错报得很具体（哪个文件、为什么），
      而把它们的话吞掉换成一句「失败了」，排查要从头来。

      **退出码的语义由调用方声明，不写死。** 默认 `@(0)`，`icacls` 那两处就是这个语义；
      而 `robocopy` 的退出码是**位掩码**，成功码是一整段（见它的调用处）。写死 `-ne 0`
      会让 robocopy 的「1 = 成功复制了文件」被当成失败——2026-09-18 安装停在第 2 步就是
      这个原因，而那时文件其实已经铺完了。

      （这段注释曾经写的是 `schtasks / netsh / mysqldump`，三个都没走这条路：计划任务与
      防火墙都用 cmdlet 建，mysqldump 在 `ops.ps1` 里自己跑。）
    #>
    param(
        [string]$Step,
        [string]$FilePath,
        [string[]]$Arguments,
        # 成功退出码。默认 @(0) —— 绝大多数命令行工具是这个语义。
        [int[]]$SuccessExitCodes = @(0),
        # 失败时补一句「这个码是什么意思」。这类工具的码经常不好懂（robocopy 的 8 / 16），
        # 而读到它的人多半不是写这套东西的人。
        [string]$FailureAdvice = ''
    )

    Write-Log ("> " + (Split-Path -Leaf $FilePath) + ' ' + ($Arguments -join ' '))
    $outFile = Join-Path $script:TempDir 'native-stdout.txt'
    $errFile = Join-Path $script:TempDir 'native-stderr.txt'
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -NoNewWindow -Wait -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile

    foreach ($file in @($outFile, $errFile)) {
        $text = Read-NativeOutput $file
        if ($text) { Write-Log ('    ' + $text.Trim()) }
    }
    if ($SuccessExitCodes -notcontains $process.ExitCode) {
        # 没有 FailureAdvice 时这句话与改动前**逐字相同**——icacls 那两处不该因为这次
        # 修复而换一句提示。
        if ($FailureAdvice) {
            throw "$Step 失败（退出码 $($process.ExitCode)）。$FailureAdvice 上面是它自己说的话。"
        }
        throw "$Step 失败（退出码 $($process.ExitCode)）。上面是它自己说的话。"
    }
}

# ---------------------------------------------------------------- 打包侧的自检读回来

function Read-PackageInfo {
    $path = Join-Path $PackageRoot 'deploy\package-info.txt'
    # 2026-09-18：`python`（包里那个内嵌解释器的完整版本号）改成了 `python_requires`
    # （这个包**需要目标机上有什么**，恒为 3.11）。旧包里的键不会出现，于是它读成空串，
    # 而下面那句打印用的是 `-ne ''` 判定——旧包不会打出半句「Python 」。
    $info = @{ version = '未知'; built_at = '未知'; python_requires = ''; platform = '' }
    if (Test-Path -LiteralPath $path) {
        foreach ($line in Get-Content -LiteralPath $path -Encoding ASCII) {
            $index = $line.IndexOf('=')
            if ($index -gt 0) {
                $info[$line.Substring(0, $index).Trim()] = $line.Substring($index + 1).Trim()
            }
        }
    }
    return $info
}

# ---------------------------------------------------------------- 提问

function Read-DatabaseQuestions {
    <#
      那四个数据库问题（地址 / 端口 / 用户名 / 口令 / 库名）。

      **两个调用点共用这一份**：首次安装（`Read-InstallSettings`），以及升级时发现
      `backend\.env` 里的 `XLP_DATABASE_URL` 是空的 / 形状不对（第 3 步）。后者此前
      没有出路：数据库连接是 `.env` 里唯一一项**补不出来**的（它没有可回落的默认值，
      pydantic 那份默认是 `root:password@127.0.0.1`，连的是一个不存在的库），而让操作员
      自己去改文件是句空话——局域网用法下这个文件的 ACL 只授了 SYSTEM 与 Administrators。

      措辞一个字都别改：它是照着「部署说明.txt 的第四节 + 提示语」这一对写的，改一处
      就会让「照说明书的操作员」与「照屏幕的操作员」看到两套话。
    #>
    Write-Log ''
    Write-Log 'MySQL 连接信息（数据库服务器已经装好了）：'
    return @{
        dbHost = Read-WithDefault '  数据库地址' '127.0.0.1'
        dbPort = Read-WithDefault '  数据库端口' '3306'
        dbUser = Read-WithDefault '  数据库用户名' 'root'
        dbPassword = Read-Secret '  数据库密码（输入时不显示）'
        dbName = Read-WithDefault '  数据库名（不存在会自动创建）' $DefaultDbName
    }
}

function Read-DatabaseMode {
    <#
      这一问决定**安装器要不要碰数据库**（2026-09-18 加，用户的原话：

          「我先把数据库准备好，只一键安装应用服务（前后端）即可」）。

      三种选择的差别只有一件事：**这个库里，哪几样归你**。

          1 = installer        建库 + 建表 + 写基础数据 + 设管理员密码   全归安装器
          2 = prepared         建库 + 建表 + 写基础数据 + 设管理员密码   全归你
          3 = schema_prepared  建库 + 建表 归你，其余归安装器            ← 2026-09-18 加

      选 2 时安装器**一个建库、写数据的动作都不做**：不建库、不写基础数据
      （`app.db.seed` 的量表与 admin 账号）、不跑基线清理、也不设管理员密码。
      选 3 只把「建库 + 建表」这两件推给操作员（用户 2026-09-18 的原话：「我现在可以手工
      创建数据库，并建立数据表，其他由你来完成」），**基础数据与管理员密码仍然归安装器**
      ——那正是选 2 唯一答不上「其他由你来完成」的地方。

      **迁移照跑**（`alembic upgrade head`），三种都跑。这是这一问里唯一一处看起来「越界」
      的地方，也是有意留的：迁移是**应用自己的表结构**，不是数据库的准备工作——它幂等、
      不删数据，而不跑它的下场是「屏幕说安装成功、页面报错」：换了程序文件而表结构停在
      上一版时，**登录页照样打得开**（它读的 `system_setting` 是旧表），其余页面 500。
      第 6 步那个健康检查探的正是登录页要的那一个端点，所以这种「一半坏掉」它抓不住。
      一句话：**库与数据归操作员，表结构归这一版程序。**

      选 3 是这句话**唯一的例外情形**：操作员把表也备好了，而那份 DDL 是人写的——
      库里没有 `alembic_version`（Alembic 自己的版本记录表，只有 Alembic 会建），
      迁移会在第一条 `ALTER TABLE … ADD COLUMN` 上撞 `Duplicate column name`。
      所以迁移**之前**多一步 `app.db.ensure_schema`：先比一比表名与列名，对得上就替他把
      那本账补上（只写一行 `alembic_version`，业务表一行都不动），对不上就逐条说清缺什么
      再停下。它三种模式都跑——那一步是「表结构归这一版程序」那一半，与「谁准备库」无关。

      两条路都问（首次安装与升级）。升级时选 1 与选 2/3 只差建不建库、选 2 与选 3 只差
      校对不校对（`app.db.create_database` 是「已经存在就什么都不做」的幂等脚本）——
      但这一维的含义不该随「首次还是升级」变：它回答的是「**谁**负责这个库」，
      不是「这次做什么」。

      默认是 1：装过一次的人直接回车，就回到这一问加进来之前的行为。
    #>
    Write-Log ''
    Write-Log '这次数据库由谁准备？'
    Write-Log '  1 = 全由「一键安装」来准备：建库、建表、写入基础数据（量表与 admin 账号），'
    Write-Log '      并把管理员密码设成你等会儿输入的那一个。'
    Write-Log '  2 = 库和表都由我自己准备好：安装器不建库、不写基础数据、也不动管理员密码，'
    Write-Log '      只把「连哪个库」写进配置，并把表结构升到这一版。'
    Write-Log '  3 = 库和表都由我自己准备好，**基础数据和管理员密码交给安装器**：'
    Write-Log '      安装器不建库、不建表，只校对一次表结构、写入基础数据、设管理员密码。'
    while ($true) {
        $answer = Read-Host '选 1、2 还是 3（直接回车 = 1）'
        if ([string]::IsNullOrWhiteSpace($answer) -or $answer.Trim() -eq '1') { return 'installer' }
        if ($answer.Trim() -eq '2') {
            Write-Log '  明白：数据库那边的事由你负责。装完如果打不开，先看《部署说明.txt》里'
            Write-Log '  「数据库我自己准备」那一节——建库、建表、灌基础数据的三条命令都在那里。'
            return 'prepared'
        }
        if ($answer.Trim() -eq '3') {
            Write-Log '  明白：库和表你建好了，基础数据与管理员密码由安装器来写。'
            Write-Log '  安装器会先校对一次表结构（比表名和列名）：对得上就往下走，对不上会'
            Write-Log '  逐条列出缺哪张表、缺哪一列并停下——那时候请看《部署说明.txt》里'
            Write-Log '  「数据库和表我自己建好了」那一节。'
            return 'schema_prepared'
        }
        Write-Log '  请输入 1、2 或 3。' 'WARN'
    }
}

function Read-InstallSettings {
    param([bool]$Upgrade, [string]$ResolvedInstallDir)

    Write-Log ''
    Write-Log '请回答下面几个问题（几乎所有情况直接按回车就行）。'
    Write-Log ''

    if ($Upgrade) {
        Write-Log ("检测到 " + $ResolvedInstallDir + " 下已经装过一次，这次按**升级**处理：")
        Write-Log '  不再问数据库连接信息，不重建库，不重跑基线清理，只更新程序文件并跑一次数据库迁移。'
        # 这一句是给**心里没底的人**写的：他上一次装的时候输过一个管理员密码，
        # 此刻最想知道的就是「这次会不会又要我输、会不会把我改掉的那个冲掉」。
        # 答案是不动（第 4 步那个 `if ($isUpgrade)` 分支），而**不问也不说**的话，
        # 他只能靠猜——猜错的代价是跑去重装一遍。
        Write-Log '  管理员密码也不动：升级不换掉一个正在用的登录凭据（忘了怎么重设，装完会说）。'
        # 这一问**升级也问**（理由见 `Read-DatabaseMode`）。它排在上面那段通告之后：
        # 答案取决于「这是升级」，而升级时那个库肯定已经在用了。
        $databaseMode = Read-DatabaseMode
        $settings = @{ installDir = $ResolvedInstallDir; usage = $Usage; databaseMode = $databaseMode }
        # 默认值取**这个安装现在用的端口**，不是写死的 8000。升级不改 `.env`（见第 3 步），
        # 所以「直接回车 = 8000」在一台跑在 8080 上的机器上是句假话——按回车之后端口还是
        # 8080，而操作员会以为自己刚把它改成了 8000。第 3 步会把「沿用哪一个」写进日志。
        # 读不出来（文件坏了 / 那一行是空的）才回落 8000，并交给第 3 步去补。
        $existing = Get-EnvValue -Values (Get-EnvFileValues -Path (Join-Path $ResolvedInstallDir 'backend\.env')) -Key 'XLP_PORT'
        if (Test-EnvValueOk -Key 'XLP_PORT' -Value $existing) {
            $settings.port = Read-WithDefault 'Web 服务端口' $existing
        } else {
            $settings.port = Read-WithDefault 'Web 服务端口' $DefaultPort
        }
        return $settings
    }

    $dir = $ResolvedInstallDir
    if ([string]::IsNullOrWhiteSpace($dir)) {
        $dir = Read-WithDefault '安装到哪个目录' $DefaultInstallDir
    }

    $port = Read-WithDefault 'Web 服务端口（老师访问时用的那个）' $DefaultPort

    $db = Read-DatabaseQuestions
    $dbHost = $db.dbHost
    $dbPort = $db.dbPort
    $dbUser = $db.dbUser
    $dbPassword = $db.dbPassword
    $dbName = $db.dbName

    # 数据库那一问排在连接信息**之后**：先问清「连的是哪个库」，再问「这个库要不要我来准备」。
    $databaseMode = Read-DatabaseMode

    $adminPassword = ''
    if ($databaseMode -eq 'prepared') {
        # **不问，也不设。** 问了就等于说我们会用它，而这一问刚刚承诺过不动管理员密码。
        # 但必须**说出来**：「怎么没问我密码」是操作员合情合理的第一反应，而他要的那个答案
        # （admin 属于你准备的那个库）不在屏幕上的话，他只能猜。
        Write-Log ''
        Write-Log '（按你的选择：这次不问管理员密码，也不会去设它——admin 那个账号属于你准备的库。）'
    } else {
        Write-Log ''
        Write-Log '管理员账号的密码（登录用户名固定是 admin，登录后系统会要求你改一次）：'
        while ($true) {
            $adminPassword = Read-Secret '  管理员密码（至少 6 位）'
            if ($adminPassword.Length -lt 6) {
                Write-Log '  太短了，至少 6 位。' 'WARN'
                continue
            }
            $again = Read-Secret '  再输一遍'
            if ($adminPassword -ne $again) {
                Write-Log '  两次输入不一致，请重新输入。' 'WARN'
                continue
            }
            break
        }
    }

    return @{
        installDir = $dir
        usage = $Usage
        port = $port
        dbHost = $dbHost
        dbPort = $dbPort
        dbUser = $dbUser
        dbPassword = $dbPassword
        dbName = $dbName
        databaseMode = $databaseMode
        adminPassword = $adminPassword
    }
}

# ---------------------------------------------------------------- `.env`

# `.env` 里必须有的那几项。**这一份是唯一的名单**：`Repair-EnvFile` 按它查文件、
# 按这个顺序补写缺项，`Get-EnvWantedValues` 按它给值，第 3 步按它报「检查通过」。
$script:EnvFileKeys = @(
    'XLP_DATABASE_URL',
    'XLP_HOST',
    'XLP_PORT',
    'XLP_WEB_DIR',
    'XLP_LOG_DIR',
    'XLP_LOG_LEVEL',
    'XLP_DOCS_ENABLED',
    'XLP_JWT_SECRET'
)

function Get-InstallPath {
    param([string]$InstallDir, [string]$Relative)

    # 路径一律写成**正斜杠**：python-dotenv 对带反斜杠的值会做转义处理，而 Windows
    # 的 API 接受正斜杠。少一类「某些密码/路径组合才炸」的问题。
    return ($InstallDir.TrimEnd('\') + '/' + $Relative) -replace '\\', '/'
}

function New-DatabaseUrl {
    param([string]$User, [string]$Password, [string]$DbHost, [string]$DbPort, [string]$DbName)

    # 口令在 URL 里必须百分号编码：密码里一个 `@` 或 `:` 会把 URL 拆成另一个形状，
    # 而报出来的错是「Access denied」——与「密码打错了」长得一样。
    # `[Uri]::EscapeDataString` 与 Python 的 `quote(pw, safe='')` 保留的是同一组字符
    # （A-Za-z0-9-._~），所以后端那边 `unquote` 回来正好。
    #
    # 参数名刻意不叫 `$Host`：那是 PowerShell 的**自动变量**（当前宿主对象），
    # 拿它当参数名会在绑定那一刻报错，而那句话与「参数写错了」完全不像。
    $encodedUser = [Uri]::EscapeDataString($User)
    $encodedPassword = [Uri]::EscapeDataString($Password)
    return 'mysql+pymysql://{0}:{1}@{2}:{3}/{4}' -f $encodedUser, $encodedPassword, $DbHost, $DbPort, $DbName
}

function Get-SettingText {
    <#
      从 `$settings` 里取一项，**没有这一项就是空字符串**，绝不抛异常。

      为什么不直接写 `[string]$Settings.dbUser`：`Set-StrictMode -Version Latest`（本脚本
      第一行就设了）下，**访问一个不存在的属性会在读取那一刻抛
      `PropertyNotFoundException`**，中文系统上就是「在此对象上找不到属性"dbUser"」。
      转型、字符串拼接、`?? ''` 全都排在**读取之后**，一个都挡不住它——这一点
      2026-09-18 在一所学校的真机上付过一次学费：第 3 步刚打完「.env 已存在，升级只补
      坏掉的行」就停在这里（`Get-EnvWantedValues` 的第一句），日志里只有这一行中文，
      看不出是哪个键。

      升级路径上这几项为空是**正常**的，不是错：
        · 文件的 `XLP_DATABASE_URL` 那一行没坏时，`Get-EnvWantedValues` 算出来的那个
          空 URL 根本不会被取用（`Repair-EnvFile` 只写坏掉的行）；
        · 真坏了的时候，第 3 步**先**问一遍数据库，再把答案塞回 `$settings`，
          所以进来时它就在。
      所以判据是「**键在不在**」，不是「值空不空」——后者会把 `XLP_PORT=0` 这类
      合法的值也当成缺项。
    #>
    param([hashtable]$Settings, [string]$Key)

    if ($null -eq $Settings) { return '' }
    if (-not $Settings.ContainsKey($Key)) { return '' }
    return [string]$Settings[$Key]
}

function Get-EnvWantedValues {
    <#
      `.env` 每一项**应当**是什么值。两个消费者共用这一份：
        · `Write-EnvFile`  —— 首次安装，整份写出来；
        · `Repair-EnvFile` —— 升级时只补坏掉的那几行。
      各写一份的话，「写进去的」与「补出来的」会漂成两种形状，而那种不一致只有在一台
      真补过的机器上才看得见。

      `XLP_JWT_SECRET` 每次调用都新生成一串：`Write-EnvFile` 每次都写新的（原有行为），
      而补写只在「文件里没有 / 是空的」时才取用。
    #>
    param([hashtable]$Settings)

    # 监听地址由**用法**决定，不是一个留给操作员手工改的选项：单机用法下写 0.0.0.0、
    # 只在注释里说「只想本机就改成 127.0.0.1」，等于给一个「只有我自己用」的安装留了一个
    # 对外开放的口子，而没有人会回头去改它。
    $hostValue = '0.0.0.0'
    if ($Settings.usage -eq 'single') { $hostValue = '127.0.0.1' }

    # **数据库那几项一律走 `Get-SettingText`，不要写 `$Settings.dbUser`。** 升级路径上
    # `$Settings` 里没有它们（没问过），而严格模式下**属性读取本身**就会抛异常——
    # `[string]` 转型排在读取之后，挡不住它。这里原本写的就是 `([string]$Settings.dbUser)`，
    # 以为转型能容忍缺项，2026-09-18 真机上炸的就是这一行（见 `Get-SettingText` 的注释）。
    $url = New-DatabaseUrl -User (Get-SettingText -Settings $Settings -Key 'dbUser') `
        -Password (Get-SettingText -Settings $Settings -Key 'dbPassword') `
        -DbHost (Get-SettingText -Settings $Settings -Key 'dbHost') `
        -DbPort (Get-SettingText -Settings $Settings -Key 'dbPort') `
        -DbName (Get-SettingText -Settings $Settings -Key 'dbName')

    return @{
        'XLP_DATABASE_URL' = $url
        'XLP_HOST'         = $hostValue
        'XLP_PORT'         = [string]$Settings.port
        'XLP_WEB_DIR'      = Get-InstallPath -InstallDir ([string]$Settings.installDir) -Relative 'frontend/dist'
        'XLP_LOG_DIR'      = Get-InstallPath -InstallDir ([string]$Settings.installDir) -Relative 'runtime/logs'
        'XLP_LOG_LEVEL'    = 'INFO'
        'XLP_DOCS_ENABLED' = '0'
        'XLP_JWT_SECRET'   = New-RandomHex
    }
}

function Test-EnvValueOk {
    <#
      这一行的值能不能用。**只看「空的」与「写了但不能用」这两种**，不看它与本次输入
      是否一致——「升级不动操作员改过的值」是这个函数的边界。

      `XLP_PORT` 与 `XLP_DOCS_ENABLED` 判得比别的严：它们进 pydantic 是 `int` 与 `bool`，
      而 `Settings()` 在 `import app.db.session` 那一刻就跑了。2026-09-18 一所学校的安装
      就停在这一句上：

          pydantic_core…ValidationError: 1 validation error for Settings
          port / Input should be a valid integer … [type=int_parsing, input_value='', input_type=str]

      真正的原因是 `backend\.env` 里那一行 `XLP_PORT=` 是空的，而这句话离它隔着三层调用
      栈与一段英文——操作员手上能提供的东西只有安装日志。
    #>
    param([string]$Key, [string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }

    if ($Key -eq 'XLP_PORT') {
        $number = 0
        if (-not [int]::TryParse($Value, [ref]$number)) { return $false }
        return ($number -ge 1 -and $number -le 65535)
    }
    if ($Key -eq 'XLP_DOCS_ENABLED') {
        return @('0', '1', 'true', 'false') -contains $Value.ToLowerInvariant()
    }
    if ($Key -eq 'XLP_DATABASE_URL') {
        # 形状：`mysql+pymysql://用户:口令@主机:端口/库名`。判的是**两段非空**——@ 前面
        # 那段（用户与口令，`New-DatabaseUrl` 已把口令里的 `@` `:` 百分号编码掉）、以及
        # @ 后面那段里的主机与库名。判形状而不是判「非空」的理由：
        # `mysql+pymysql://:@:/` 能过「非空」，而它连主机名和库名都没有——那种值一路走到
        # 第 4 步，报出来的是一句「Access denied」。
        # 刻意**不**在主机那一段里禁 `:`：主机可能是 IPv6 字面量（`[::1]` 带方括号也带冒号）。
        return ($Value -match '^mysql\+pymysql://[^@/]+@[^/]+/[^/]+$')
    }
    return $true
}

function Read-EnvFileLines {
    <#
      把 `.env` 原样读成一行行（**不解析**：`Repair-EnvFile` 要就地把坏行改掉）。
      文件不在就返回空——那不是错，第 1 步判断「首次还是升级」用的就是「它在不在」。

      **读不了会直接停下来**（`Stop-WithError`，不是抛异常）。理由：这个文件读不出来时
      后端也一样读不出来，而那时的报错是 pydantic-settings 的一串英文 `PermissionError`
      （见 `Tighten-EnvPermissions` 的注释）。在这里停下来至少能说一句人话。
    #>
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try {
        return @(Get-Content -LiteralPath $Path -Encoding UTF8 -ErrorAction Stop)
    } catch {
        Stop-WithError ('读不了 ' + $Path + '：' + $_.Exception.Message + '。如果是权限问题，用管理员身份重跑一次安装。')
    }
}

function Get-EnvFileValues {
    <# 把 `.env` 读成「键 → 值」的哈希表。同一个键出现多次时**后面那一行赢**，
       与 python-dotenv 的行为一致（它就是按顺序覆盖的）。 #>
    param([string]$Path)

    $values = @{}
    foreach ($line in (Read-EnvFileLines -Path $Path)) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $index = $trimmed.IndexOf('=')
        if ($index -le 0) { continue }
        $values[$trimmed.Substring(0, $index).Trim()] = $trimmed.Substring($index + 1).Trim()
    }
    return $values
}

function Get-EnvValue {
    param([hashtable]$Values, [string]$Key)

    if ($Values.ContainsKey($Key)) { return [string]$Values[$Key] }
    return ''
}

function Get-EnvValueNote {
    <# 补行时写进日志的那半句话。**两个键的值不进日志**：一个是数据库口令，一个是 JWT 密钥。 #>
    param([string]$Key, [string]$Value)

    if ($Key -eq 'XLP_DATABASE_URL') { return '刚才输入的连接信息（含口令，不打印）' }
    if ($Key -eq 'XLP_JWT_SECRET') { return '一串新生成的随机值' }
    return $Value
}

function Get-EmptyUnknownEnvKeys {
    <#
      名单之外、但**是空的**那些 `XLP_*` 行（带行号）。**只读不写**。

      `Repair-EnvFile` 修得了名单里的那八项，因为那八项的值它都知道该是什么；名单之外的
      它一个都不知道——「补成什么」没有答案。但它**看得出来**，所以第 3 步把这几行报出来。

      为什么要为「不认识的键」专门写一段：`XLP_` 这个前缀是 pydantic 读的，一条空的
      `XLP_ACCESS_TOKEN_EXPIRE_MINUTES=` 会让 `Settings()` 在 import 期抛 `int_parsing`
      ——与 2026-09-18 那次一模一样，只是换了个字段名，而那时安装器一句话都没说。
      它出现的路径也很具体：**新版本的安装器写了一项、而机器上跑的是旧版本的安装器**
      （包里的 `install.ps1` 与安装目录里的那份可以不是同一个版本），或者有人手工加了一行。

      **报出来而不是「顺手注释掉」**：一个空值的原意从来不是「要这个字段是空的」，所以
      注释掉（回到程序内置的默认值）在技术上更像是正解——但那要替操作员改一行**安装器
      完全不认识**的配置，而它恰好可能是下一版才有的功能开关。这几句话写在日志里，
      操作员照做只要一分钟，而猜错一次要查半天。
    #>
    param([string]$Path)

    $found = @()
    $number = 0
    foreach ($line in (Read-EnvFileLines -Path $Path)) {
        $number++
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $index = $trimmed.IndexOf('=')
        if ($index -le 0) { continue }
        $key = $trimmed.Substring(0, $index).Trim()
        if (-not $key.StartsWith('XLP_')) { continue }
        if ($script:EnvFileKeys -contains $key) { continue }
        if (-not [string]::IsNullOrWhiteSpace($trimmed.Substring($index + 1))) { continue }
        $found += ('第 ' + $number + ' 行 ' + $key + '=')
    }
    return $found
}

function Repair-EnvFile {
    <#
      升级路径：**只补坏掉的那几行**，操作员改过的值一个字都不动。

      为什么需要它：升级模式以前完全信任现有的 `.env`——只在里面抠一个端口号给防火墙用，
      其余一个字都不看。于是一个坏掉的 `.env`（手改错、上次半途失败留下的、从别处拷来的、
      早先某版安装器写坏的）会让安装器一路走到第 4 步，然后甩给操作员一段英文 traceback，
      而真正的原因只是某一行的等号后面是空的。第 4 步之后它还照样会写进 `build.json`、
      放进防火墙规则——**坏配置不是在第 3 步消失的，它是一路带着走的**。

      判据只有两条，都写在 `Test-EnvValueOk` 里，别在别处再抄一份：
        · 空（`KEY=` 后面什么都没有）——空不是「一个值」，是这一行没写成；
        · 写了但不能用（`XLP_PORT=abc`、`XLP_PORT=99999`、`XLP_DOCS_ENABLED=是`）。
      有值且能用的，一律不动：`XLP_HOST=127.0.0.1` 是操作员在局域网用法上刻意改的，
      重写它就是把「只有本机能访问」悄悄改回「整个网段都能访问」。

      返回：一句句中文说明（调用方写进日志）。**不打印任何值**——这个文件里有口令。

      它也可能**问**（见调用方第 3 步）：数据库连接是唯一一项补不出来的，那一处由调用方
      先问一遍再进来。
    #>
    param([hashtable]$Settings, [string]$Path)

    $wanted = Get-EnvWantedValues -Settings $Settings
    $lines = @(Read-EnvFileLines -Path $Path)
    $notes = @()
    $present = @{}
    $changed = $false

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trimmed = $lines[$i].Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $index = $trimmed.IndexOf('=')
        if ($index -le 0) { continue }
        $key = $trimmed.Substring(0, $index).Trim()
        # 不认识的键一律不动：将来加的配置项不该被这一版安装器删掉。
        if ($script:EnvFileKeys -notcontains $key) { continue }

        $present[$key] = $true
        $value = $trimmed.Substring($index + 1).Trim()
        if (Test-EnvValueOk -Key $key -Value $value) { continue }

        # **补不出来就别动这一行。** 升级路径上可能有某一项安装器这边没有值
        # （数据库那几项没问过，而 `XLP_DATABASE_URL` 恰好在这一行坏了；同一个键写了两遍、
        # 后一行才是生效的那一份时也会走到这里）。写下去的 `KEY=` 一个字符都没有——
        # 正是这一整套存在的理由，所以宁可留一句说明，也不亲手造一个空行。
        if ([string]::IsNullOrWhiteSpace($wanted[$key])) {
            $notes += ($key + ' 这一行不能用，但安装器这边没有可以补的值：请手工把它填对（或删掉这一行）')
            continue
        }

        # 同一个键写了两遍时**每一处都改**：python-dotenv 是「后面那一行赢」，只改第一处
        # 的话，后面那行空的仍然是真正生效的那一个。
        $lines[$i] = $key + '=' + $wanted[$key]
        $changed = $true
        $why = '是空的'
        if (-not [string]::IsNullOrWhiteSpace($value)) { $why = '不能用' }
        $notes += ($key + ' ' + $why + '，已补成' + (Get-EnvValueNote -Key $key -Value $wanted[$key]))
    }

    $missing = @($script:EnvFileKeys | Where-Object { -not $present.ContainsKey($_) })
    if ($missing.Count -gt 0) {
        # 先挑出**真的有值**的那几项再写：全都没有值时，上面那两行说明与标题也不该落进文件。
        $appended = @()
        foreach ($key in $missing) {
            if ([string]::IsNullOrWhiteSpace($wanted[$key])) {
                $notes += ($key + ' 原来没有，而安装器这边没有可以补的值：请手工加上这一行')
                continue
            }
            $appended += ($key + '=' + $wanted[$key])
            $notes += ($key + ' 原来没有，已补成' + (Get-EnvValueNote -Key $key -Value $wanted[$key]))
        }
        if ($appended.Count -gt 0) {
            $lines += ''
            $lines += ('# 下面这几项原来没有，「一键安装」在 ' + (Get-Date -Format 'yyyy-MM-dd HH:mm') + ' 补上了。')
            $lines += $appended
            $changed = $true
        }
    }

    # 没坏就一个字都不写：不动它，也不动它的时间戳。
    if (-not $changed) { return $notes }
    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
    return $notes
}

function Write-EnvFile {
    param([hashtable]$Settings, [string]$Path)

    $wanted = Get-EnvWantedValues -Settings $Settings

    if ($Settings.usage -eq 'single') {
        $hostComment = '# 127.0.0.1 = 只有这台电脑打得开。这是「单机」用法，别的机器连不过来。'
    } else {
        $hostComment = '# 0.0.0.0 = 同一局域网里的老师都能访问；只想本机访问就改成 127.0.0.1。'
    }

    $lines = @(
        '# 由「一键安装」生成。改完之后要重启服务：双击「重启服务.bat」。',
        '# 这份文件里有数据库口令，别拷给别人。',
        'XLP_DATABASE_URL=' + $wanted['XLP_DATABASE_URL'],
        $hostComment,
        'XLP_HOST=' + $wanted['XLP_HOST'],
        'XLP_PORT=' + $wanted['XLP_PORT'],
        'XLP_WEB_DIR=' + $wanted['XLP_WEB_DIR'],
        'XLP_LOG_DIR=' + $wanted['XLP_LOG_DIR'],
        'XLP_LOG_LEVEL=' + $wanted['XLP_LOG_LEVEL'],
        '# 关掉 /docs、/redoc、/openapi.json。它们是开发期的便利，而学校里这台机器的',
        '# 端口是整个网段都够得着的——没有必要把整套接口形状公开出去。',
        'XLP_DOCS_ENABLED=' + $wanted['XLP_DOCS_ENABLED'],
        '# 每次安装随机生成，不要与别的部署共用。',
        'XLP_JWT_SECRET=' + $wanted['XLP_JWT_SECRET']
    )

    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

# ---------------------------------------------------------------- 铺文件

function Copy-PackageInto {
    param([string]$Source, [string]$Destination)

    # 包解压路径 == 安装目录：跳过去，否则 robocopy 会把自己拷到自己里。
    $sourceFull = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\')
    $destinationFull = $Destination.TrimEnd('\')
    if ($sourceFull -ieq $destinationFull) {
        Write-Log '包就在安装目录里，跳过复制'
        return
    }

    Write-Log ("复制 " + $sourceFull + "  ->  " + $destinationFull)
    # /E 含空目录，/NFL /NDL /NJH /NJS 把每条文件的噪声压掉，只留汇总。
    # 不用 /MIR：那是「让目标与源完全一致」，会顺手删掉 runtime\ 与 backend\.env。
    #
    # **`-SuccessExitCodes (0..7)` 不能省。** robocopy 的退出码是**位掩码**，不是
    # 0 / 非 0：0 = 目标已经是最新的（没东西可拷），**1 = 成功复制了文件**，
    # 2 = 目标目录里有多余文件，3 = 1+2，其余同理。**全新安装拿到的必然是 1**，
    # 而 `8` / `16` 才是失败（8 = 有文件没能复制，多半是被占用；16 = 严重错误，
    # 一个都没复制）。把它按 0 / 非 0 判，全新安装会在这一步「失败」退出——
    # 而那时文件其实已经铺好了。2026-09-18 报的就是这个。
    # 2 不额外报警告：它说的是目标目录里有源没有的文件，而这里刻意不用 /MIR，
    # 所以那是正常现象（升级时上一版留下的东西）。
    # 两个路径必须过 `Quote-Argument`：`Start-Process` 只是把数组用空格接成一条命令行，
    # 而带空格的安装目录是合法的（`Test-InstallDir` 挡的是 `% ! & | < > ^ "`，没有挡空格）
    # ——单机用法的默认目录就在 `%LOCALAPPDATA%` 下，`C:\Users\Zhang San\AppData\Local\…`
    # 是个再普通不过的形状。不加引号时 robocopy 收到的是 `源=C:\Users\Zhang`、
    # `目标=San\AppData\Local\...`：源不存在就是退出码 16，而**源恰好存在**（那是另一个
    # 真实账号）时它会照着相对当前目录的 `San\...` 把错的东西拷进去，退出码还是 1。
    Invoke-Native '复制程序文件' 'robocopy.exe' @(
        (Quote-Argument $sourceFull), (Quote-Argument $destinationFull),
        '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/R:1', '/W:1'
    ) -SuccessExitCodes (0..7) `
      -FailureAdvice 'robocopy 的 8 = 有文件没能复制（正在跑的 python.exe 会锁住 venv 里的 python*.dll 与 site-packages 里的 .pyd，多半是服务没停下来）；16 = 严重错误，一个文件都没复制。' | Out-Null
}

function Install-ConsoleEncodingHook {
    param([string]$SitePackages)

    # 把 sitecustomize.py 放进 venv 的 site-packages。`site` 模块启动时会 import 同名的
    # 这个模块——这是 CPython 给的**唯一**一个「每次解释器启动都跑一下」的钩子
    # （`site.execsitecustomize`）。
    #
    # 它是全套里唯一一处给解释器**永久**上了一道保险的地方，理由比 `._pth` 时代更硬：
    # 重定向输出时 Python 用的是 locale 编码——中文 Windows 上是 cp936（中文没问题），
    # 英文 Windows 上是 cp1252，那里我们的中文 `print` 会直接抛 UnicodeEncodeError，
    # 安装停在一个与编码八竿子打不着的位置，日志里连一句人话都没有。
    # （接在控制台上时走 WriteConsoleW，不受代码页影响；出事的是重定向那一路。）
    #
    # **为什么不用环境变量**（`PYTHONIOENCODING` / `PYTHONUTF8`）：2026-09-18 之前内嵌
    # CPython 的 `._pth` 让 `use_environment = 0`，环境变量全被忽略；换成 venv 之后
    # 环境变量确实生效了，但它们**只作用于安装进程自己及其子进程**——用户之后双击
    # 「启动服务.bat」时的环境由他的会话决定，局域网那条路的计划任务由 Task Scheduler
    # 决定，两条路都不由我们控制。sitecustomize 不依赖任何环境变量，而且同一份钩子
    # 连 `python -m alembic` 这类第三方入口一起覆盖。所以安装脚本里那两行
    # `$env:PYTHONIOENCODING` 已经删掉了，别再加回来。
    $source = Join-Path $PSScriptRoot 'sitecustomize.py'
    if (-not (Test-Path -LiteralPath $source)) { throw "包里少了 sitecustomize.py：$source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $SitePackages 'sitecustomize.py') -Force
    Write-Log 'sitecustomize.py 已就位（把标准输出固定成 UTF-8）'
}

# ---------------------------------------------------------------- 启停服务

function Get-ServiceProcess {
    <#
      我们这个后台服务的**进程树**（可能不止一个）。返回的对象形状不变（`Get-Process`
      的，有 `.Id` / `.Path`），所以各处调用点一行都不用改。

      判据是**进程**，不是 HTTP 探活：服务刚起的那一两秒端口还没绑上，探活会说「没在跑」，
      于是再点一次「启动服务.bat」就起了第二个。

      ---------------------------------------------------------------------
      **为什么是「树」而不是一个进程**（2026-09-18 加）
      ---------------------------------------------------------------------
      venv 里的 `Scripts\python.exe` **不是解释器，是一个只负责转发的壳**。CPython 的
      `Lib/venv/__init__.py` 原文：

          # On Windows, we rewrite symlinks to our base python.exe into
          # copies of venvlauncher.exe
          basename, ext = os.path.splitext(os.path.basename(src))

      那个壳（`PC/venvlauncher.c`，编译成 `Lib/venv/scripts/nt/python.exe`）读 `pyvenv.cfg`、
      对**真正的**解释器 `CreateProcessW` 然后等它。于是服务在进程表里是两个：

          <安装目录>\runtime\venv\Scripts\python.exe  ← 壳，Path 与 $script:PythonExe 相等
                  └─ <base python>\python.exe         ← 真占端口、真连库、真锁 .pyd

      **Windows 不连坐**：只按 `Path` 找到壳、只杀壳，真正的服务活得好好的。三条后果
      全是静默的——`venv --clear` 删不掉被孤儿映射着的 `.pyd`（`PermissionError`，每次
      升级都撞）、第 6 步起出第二个而 `Wait-ForHealth` 拿**旧进程**的应答报「安装完成」、
      `停止服务.bat` 杀完壳端口还在应答。所以这里连直接子进程一起收。

      比对**根**用 `Path` 而不是按名字——那台机器上可能还有别人在跑别的 Python；
      `$script:PythonExe` 是安装目录里的全路径，天然唯一。**但不要顺手拿 base python 的
      路径再去比一遍**：在同一台机器上别人跑的 python 也是那个 base，那样会误伤。

      **`catch { $false }` 那个盲点还在，而且现在是两层**：`ExecutablePath` 对跨账号、
      跨完整性级别的进程读不出来（`Get-CimInstance` 那时给的是空属性，不是抛异常），
      那种进程被判成「不在跑」，**它的子进程也就跟着看不见**。单机用法不提权，而
      「右键以管理员身份运行了启动服务.bat」是一条真实存在的路径——那样起来的服务这里
      看不见。`ops.ps1` 的 start / stop 因此都拿 HTTP 探活当**第二判据**，专门用来把这种
      情况说破，那条现在是唯一的兜底，别摘；这里是安装器，撞上时 robocopy 会以自己的方式
      报出来（退出码 8 与它自己那句话）。
    #>
    $ids = [System.Collections.Generic.List[int]]::new()
    $roots = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $script:PythonExe })
    foreach ($root in $roots) {
        $ids.Add([int]$root.ProcessId)
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($root.ProcessId)" -ErrorAction SilentlyContinue)) {
            $ids.Add([int]$child.ProcessId)
        }
    }
    # 回到 `Get-Process` 对象：调用点要的是 `.Id`、能直接喂给 `Stop-Process`，
    # 而 `Get-CimInstance` 给的是 `.ProcessId`。顺带去重、去掉已经退出的那些。
    return @($ids | Sort-Object -Unique | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
}

function Stop-RunningService {
    <#
      停掉正在跑的这一份。装之前必须调它：正在跑的 python.exe 会把 venv 里的
      `python*.dll` 与 site-packages 里的 `.pyd` 锁住（而且**锁住它们的是子进程**，
      见 `Get-ServiceProcess` 的 docstring），robocopy 于是复制到一半失败（退出码 8），
      第 2 步的 `venv --clear` 也会在半路 `PermissionError`——再跑一次能好，但那是一次
      没必要的失败，而且操作员看到的是「装了一半」。

      **任务那一半是「有才停」，进程那一半是「等不到就杀」。** 这两件事以前写在一个
      `if ($existingTask)` 里，于是单机用法（根本没有计划任务）连等都不等。抽出来之后
      还留下过半个洞：单机模式**没有谁去叫那个进程停**，只等不杀就一定会等满 15 秒，
      然后照样撞上被锁住的 DLL——同一个退出码 8，换了个入口。
    #>
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }

    # 第一秒先让它自己退（计划任务的「停止」是硬杀，正常这时已经没了），第二秒起强杀。
    # `Stop-Process -Force` 与计划任务那条路的语义一致：run_server.py 收不到信号，
    # 它那个重试循环不会把自己拉起来——正是我们要的。
    $waited = 0
    while ($waited -lt 15) {
        $ours = @(Get-ServiceProcess)
        if ($ours.Count -eq 0) { break }
        if ($waited -eq 0) { Write-Log '停掉正在运行的服务' }
        if ($waited -eq 1) {
            Write-Log ('它没有自己退出，强制结束（进程号 ' + (($ours | ForEach-Object { $_.Id }) -join '、') + '）')
            $ours | Stop-Process -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
        $waited++
    }
    if ($waited -ge 15) { Write-Log '服务进程没有在 15 秒内退出，继续尝试复制' 'WARN' }
}

function Start-ServiceNow {
    <#
      直接把服务起成一个后台进程（单机用法走这条；局域网用法走计划任务）。

      两处不是随手写的：

      · **先判进程，在跑就什么都不做。** `Start-ScheduledTask` 靠
        `-MultipleInstances IgnoreNew` 天然幂等，这里没有那层保护。点两次「启动服务.bat」
        而起出第二个进程的后果不是「起不来」那么干净：第二个绑不上端口，而
        `run_server.py` 的 `while True` 会**每 30 秒重试一次**，日志里滚一屏
        address-in-use——而服务其实是好好的。

      · **不加 `-WindowStyle`（也就是默认的 `Normal`），但更不能加 `-NoNewWindow`。**
        `-NoNewWindow` 让子进程共用这个 .bat 的控制台，于是 .bat 一退出控制台就关，
        控制台一关子进程收到 CTRL_CLOSE 当场死掉——「启动服务」会变成「启动一下然后
        立刻停」。**这条推理与它当初写下时一字不差，只是结论反了过来**：
        2026-09-18 用户明确要求「用户使用时启动，关机时停止」，那个心智模型里的动作是
        **关掉一个看得见的窗口**。所以现在要的正是「另一个**可见的**控制台」。
        不写 `-WindowStyle` 就是那个意思（`Start-Process` 在 Windows 上默认给子进程开
        新窗口）。**别为了「少开一个窗口」把它换成 `-NoNewWindow`**——那是上面那个 bug。

        关掉那个窗口为什么会连真解释器一起带走：走的是 `CTRL_CLOSE_EVENT`，它发给挂在
        那个控制台上的**所有**进程，壳与真解释器一起死——比 `Stop-Process` 还彻底。
        （`Ctrl+C` **不能**这么承诺：`CTRL_C_EVENT` 只发本进程组，而 venvlauncher 是用
        `CREATE_NEW_PROCESS_GROUP` 起子进程的，多半只杀掉壳、留下孤儿。所以给用户的
        说明里只写「关窗口」，见 `部署说明.txt`。）
    #>
    if (Get-ServiceProcess) {
        Write-Log '服务已经在跑了，不再启动第二个。'
        return
    }
    Start-Process -FilePath $script:PythonExe -ArgumentList 'run_server.py' `
        -WorkingDirectory $script:BackendDir
    Write-Log '服务已启动。那个新开的窗口就是服务本身，要停就把它关掉。'
}

# ---------------------------------------------------------------- 计划任务与防火墙

function Register-ServiceTask {
    param([hashtable]$Settings)

    $python = $script:PythonExe
    $script = Join-Path $Settings.installDir 'backend\run_server.py'

    # 开机后延迟一分钟再起：MySQL 是 Windows 服务，两者同时启动时这个进程会先连一次
    # 还没准备好的库。
    #
    # **这一分钟只是为了让日志干净，不是「靠它兜住启动竞态」。** 库没就绪时这个进程
    # 根本不会退出：`run_server.py` 的 `wait_for_database` 会一直重试下去（退避打印，
    # 每分钟一句证明还活着），MySQL 晚起多久它都会自己接上。真兜住竞态的是那个循环。
    #
    # 也**别指望下面那个 `-RestartCount`**：Task Scheduler 的 RestartOnFailure 只在
    # 「计划任务压根没能把程序拉起来」时触发，**进程自己带非零退出码结束不算**。
    # 换句话说「进程崩了会自动重来」这件事在这里是不成立的——所以进程内重试是必须的，
    # `-RestartCount` 只是留个保险。
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT1M'

    $action = New-ScheduledTaskAction -Execute $python -Argument $script `
        -WorkingDirectory (Join-Path $Settings.installDir 'backend')

    $settingsSet = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
    # -ExecutionTimeLimit 必须显式给：不给的话默认是 PT72H，三天后任务被 Task
    # Scheduler 掐掉，而那时服务已经跑了三天、看起来一切正常——这种故障最难查。
    # `[TimeSpan]::Zero` 落到 XML 上就是 PT0S，也就是「不限时」。

    # SYSTEM：这样**不需要有人登录**就能跑起来，重启之后服务自己就回来了。
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settingsSet -Principal $principal -Force | Out-Null
    Write-Log ("计划任务 " + $TaskName + " 已注册（开机后 1 分钟启动，SYSTEM 身份）")
}

function Add-FirewallRule {
    param([string]$Port)

    # 规则名用 ASCII：netsh 在 GBK 代码页的控制台里念中文规则名会乱码，
    # 而运维以后要靠这个名字找到它、删掉它。
    $existing = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
    if ($existing) { Remove-NetFirewallRule -DisplayName $FirewallRuleName | Out-Null }

    New-NetFirewallRule -DisplayName $FirewallRuleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort ([int]$Port) -Profile Any | Out-Null
    Write-Log ("防火墙已放行入站 TCP " + $Port)
}

function Tighten-EnvPermissions {
    # `[ValidateSet]` 不是装饰：这个参数漏传时是空串，空串走 `else` —— 也就是
    # 「只授 SYSTEM 与 Administrators」，正是单机用法下把用户自己锁在外面的那一版。
    # 让它在绑定参数那一刻就炸，而不是装出一个读不了 `.env` 的实例。
    param([string]$Path, [ValidateSet('single', 'lan')][string]$Usage)

    # backend\.env 里是数据库口令明文。默认 ACL 是 Users 可读，同一台机器上任何账号
    # 都能读到它。失败不致命（只提示），因为这台机器通常只有一个管理员在登录。
    #
    # **单机用法下必须把当前用户自己授进去。** UAC 下普通用户的 token 里 Administrators
    # 是 deny-only，`/inheritance:r /grant:r SYSTEM:F Administrators:F` 之后，装它的那个
    # 人从此读不了这个文件。
    #
    # **后果不是「静默回退成默认值」。** §18 约定 4 那句「`.env` 缺失是被静默跳过的」
    # 说的是**文件不在**那一条分支（`is_file()` 为假 → 不读 → 用默认值）；而这里是
    # **存在但打不开**：`is_file()` 是 stat，对它返回真，接着 python-dotenv 的
    # `open()` 抛 `PermissionError`（那一句没有 try）——实测过（pydantic-settings
    # 2.15.0 + python-dotenv）。于是：
    #   · 安装器自己会在第 4 步的「配置自检」上以退出码 1 停住，日志里是一段 traceback；
    #   · 而 `run_server.py` 里 `get_settings()` 排在 `configure_logging()` **之前**
    #     （`main()` 第 166 / 167 行），所以服务侧是**当场死、一行日志都没有**。
    # 两条路都没有一句中文，而操作员唯一能提供的东西就是那份日志——所以下面在改完
    # 权限之后立刻用**当前这个进程的权限**读一次，当场把这件事说出来。
    #
    # 用 WindowsIdentity 而不是 `$env:USERNAME`：微软账户与域账户下 `USERNAME` 未必是
    # icacls 认得出的那个名字，而这个函数失败**是静默的**（catch 里只写一行 WARN）。
    #
    # 已知的残余风险（记着，不处理）：操作员若**用另一个管理员账号**过肩提权装单机版，
    # 这里授进去的是那个管理员而不是真正要用这套系统的用户。堵它得去授「文件属主」，
    # 为一条需要「操作员做错事 + 用另一个账号」才触发的路径不值当。
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ($Usage -eq 'single') {
        $grants = @(($owner + ':F'), 'SYSTEM:F', 'Administrators:F')
    } else {
        $grants = @('SYSTEM:F', 'Administrators:F')
    }

    try {
        # 每个参数过 `Quote-Argument`：`$Path` 与 `$owner` 都可能带空格
        # （`C:\Users\Zhang San\…` / `DESKTOP-X\Zhang San`），而 `Start-Process`
        # 只把数组用空格接成一条命令行。
        $nativeArgs = @($Path, '/inheritance:r', '/grant:r') + $grants
        Invoke-Native '收紧 .env 权限' 'icacls.exe' ($nativeArgs | ForEach-Object { Quote-Argument $_ }) | Out-Null
        Write-Log ('.env 的权限已收窄到 ' + ($grants -join ' / '))
    } catch {
        Write-Log ("没能收窄 .env 的权限（不影响使用）：" + $_.Exception.Message) 'WARN'
    }

    # 收紧之后立刻证明**我们自己还读得到**。这不是多余的一步：读不到的代价是几步之后
    # 一段 Python traceback（或者服务静默起不来），而这里当场给出一句中文。
    # 只 ERROR 不中断：造成读不到的原因也可能与 ACL 无关，硬停会挡掉本来能装完的情况。
    try {
        Get-Content -LiteralPath $Path -TotalCount 1 -ErrorAction Stop | Out-Null
    } catch {
        Write-Log ('收紧权限之后**我们自己读不了**这个文件：' + $_.Exception.Message) 'ERROR'
        Write-Log '  再往下走，第 4 步会以 PermissionError 失败，服务起来时连日志都不会写。' 'ERROR'
        Write-Log '  请把这份安装日志发回来。' 'ERROR'
    }
}

function Wait-ForHealth {
    <#
      等到服务**真的能用**为止，而不是等它「开着」。

      这里必须探一个**会读数据库**的端点，不能只探 `/api/v1/health`——那一个只回一句
      `{"status":"ok"}`，一行数据库代码都不碰：`app/db/session.py` 的 `create_engine`
      是惰性的，所以 MySQL 根本没起来的时候 uvicorn 照样起得来、`/health` 照样 200。
      安装器会欢欢喜喜地宣布成功，而学校拿到的是一个打不开任何页面的系统。

      所以判据是 `/api/v1/public/branding`：它是登录页要的那一个、**免认证**，而且是
      真的去 `system_setting` 里读一行的。它通了 = 应用层的整条路（进程 → SQLAlchemy →
      MySQL → 那条库）都通了。
    #>
    param([string]$Port, [int]$TimeoutSeconds = 120)

    $branding = 'http://127.0.0.1:{0}/api/v1/public/branding' -f $Port
    $dailyHealth = 'http://127.0.0.1:{0}/api/v1/health' -f $Port
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Write-Log ("等待服务就绪（要能读到数据库）：" + $branding)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $branding -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                # 顺带确认那个不碰库的探针也通，好让日志里两条都在。
                try {
                    $ping = Invoke-WebRequest -Uri $dailyHealth -UseBasicParsing -TimeoutSec 5
                    Write-Log ("健康检查：" + $ping.StatusCode) 'OK'
                } catch { }
                return $true
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Get-LanAddresses {
    <#
      这台机器的局域网 IPv4 地址（可能一个都没有：`Get-NetIPAddress` 不可用、没连网、
      或者地址落在下面那三个网段之外）。

      **调用方要写成 `@(Get-LanAddresses)`，不能只写 `$x = Get-LanAddresses`。**
      一个函数 `return` 一个空数组时，那个空数组会**在管道里被拆没**，调用方拿到的是
      `$null` 而不是 `@()`——而 `$null.Count` 在 `Set-StrictMode -Version Latest` 下会抛
      「在此对象上找不到属性"Count"」。2026-09-18 就是这样：一台机器的地址列表是空的，
      安装其实**已经全部成功**（第 6 步的健康检查返回 200），却在最后那段「老师这样访问」
      的收尾文案里崩掉，操作员拿到一句「安装没有完成」。同一个坑在 `ops.ps1` 那边是
      `Get-ServiceProcess`，它的三处调用点都包了 `@(...)`。
    #>
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
            ($_.IPAddress -like '10.*' -or $_.IPAddress -like '192.168.*' -or $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.')
        } |
        Select-Object -ExpandProperty IPAddress -Unique

    # 一个私网地址都没有时，**把剩下的非环回地址照样给出来**，而不是什么都不给。
    # 这一段是 2026-09-18 加的：那台机器上这个列表是空的（所以它先撞上了下面 `@(...)`
    # 那个坑），而空列表的下场是操作员在收尾文案里**拿不到任何一条 URL**——可「老师
    # 这样访问」那段字存在的全部意义就是给他一条能念给同事的地址。学校局域网几乎总是
    # 上面那三段之一，但「几乎总是」不该以「拿不到任何地址」作为另一半。
    # 真正一个 IPv4 都没有（没连网）时它仍然是空的，调用方那一支会把这件事说出来。
    if (@($addresses).Count -eq 0) {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
            Select-Object -ExpandProperty IPAddress -Unique
    }
    return @($addresses)
}

# ================================================================ 主流程

Write-Log '======================================================'
Write-Log ' 心晴 · 心理测评与关怀平台 —— 一键安装'
Write-Log '======================================================'
Write-Log ("日志文件：" + $script:LogFile)

# --- 0. 这个脚本是不是在包里跑

if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot 'backend\app\main.py'))) {
    Stop-WithError ("这个脚本要放在解压出来的包里运行（和「一键安装.bat」在一起）。" +
        "`n    现在它所在的目录是：$PackageRoot")
}

$packageInfo = Read-PackageInfo
Write-Log ("安装包版本 " + $packageInfo.version + "（构建于 " + $packageInfo.built_at +
    $(if ($packageInfo.python_requires) { "，需要 Python " + $packageInfo.python_requires } else { '' }) + "）")

# --- 1. 定用法

# **这一问必须排在提权前面**：它决定要不要提权。而它又必须排在问数据库口令前面——
# 提权出来的子进程从第 1 行重跑，想让子进程不再问一遍就得把答案当参数传下去，而口令
# **不能进命令行**（任何账号都能用 WMI 读到别的进程的命令行，环境块读不到）。
# 于是它是全场第一个问题；代价是那时还不知道安装目录，只能按两个默认目录去探（见 Resolve-Usage）。
#
# 这一段在下面的 `try {` **外面**（顺序上它必须在提权之前），所以它自己的异常没人接：
# 一个 `Join-Path` 的空值异常会直接冒到控制台成一句英文红字，**日志里一个字都没有**
# ——而操作员唯一能发回来的东西就是那份日志。包一层，让它以「安装没有完成」的形态收场。
try {
    $Usage = Resolve-Usage
} catch {
    Stop-WithError ('没能决定这次按哪种用法安装：' + $_.Exception.Message)
}

if ($Usage -eq 'single') { $DefaultInstallDir = Get-SingleDefaultDir }
Write-Log ('用法：' + (Get-UsageLabel $Usage))

if ($Usage -eq 'single' -and (Test-Administrator)) {
    # 单机用法不提权，所以正常路径下 `%LOCALAPPDATA%` 就是当前用户的。会错的情形是
    # 「以另一个管理员账号过肩提权 / runas」——那时它是**那个账号**的目录，装完用户在
    # 自己的资源管理器里找不到。同账号过 UAC 其实**不会**变（别在这里讲反，讲反的代价是
    # 一个本来就是管理员的人看到一句不成立的话，然后开始怀疑别的地方）。
    # 所以这里只陈述事实：默认目录现在是哪一个、下一步可以改。
    #
    # 同一条路径上还有第二个后果，2026-09-18 起才有：单机用法装完会在**这个会话里**开出
    # 一个可见的服务窗口（第 6 步的 `Start-ServiceNow`）。过肩提权时那个窗口属于这个管理员
    # 的会话——真正要用这套系统的用户看不到它，也就关不掉它（而那正是约定的「停服务」动作，
    # 见 `Start-ServiceNow`）。低风险，与上面那条 WARN 是同一类问题（都为「操作员用另一个
    # 账号提权」），所以在这里记一笔而不是再加一句给操作员看的话：他已经在看那条 WARN 了。
    Write-Log ''
    Write-Log '提醒：你正以管理员身份运行这个安装程序，而「单机」本来不需要管理员。' 'WARN'
    Write-Log ('  这次会默认装到：' + $DefaultInstallDir) 'WARN'
    Write-Log '  如果你平时不是用这个账号登录的，它可能不是你要的地方——下一步可以改，' 'WARN'
    Write-Log '  或者关掉这个窗口、直接双击「一键安装.bat」。' 'WARN'
}

# --- 2. 提权

if ($Usage -eq 'lan' -and -not (Test-Administrator)) {
    if ($Elevated) {
        Stop-WithError '已经请求过管理员权限但仍未获得。请右键「一键安装.bat」→「以管理员身份运行」。'
    }
    Write-Log ''
    Write-Log '安装需要管理员权限，因为要做两件系统级的事：'
    Write-Log '  · 注册一条「计算机启动时」触发的计划任务（这样重启后不需要有人登录，服务自己回来）'
    Write-Log '  · 放行防火墙的入站端口（这样同一局域网里的老师才访问得到）'
    Write-Log ''
    Write-Log '（如果你只在这台电脑上自己用，关掉这个窗口、重跑一次并选「1 = 只有这台电脑用」，'
    Write-Log '  那就不需要管理员权限了。）'
    Write-Log ''
    Write-Log '接下来会弹出一个授权窗口，安装在那里面继续；本窗口会一直等到它结束。'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Elevated')
    if ($InstallDir) { $arguments += @('-InstallDir', ('"' + $InstallDir + '"')) }
    if ($KeepData) { $arguments += '-KeepData' }
    # 用法也要传下去，否则子进程会重跑 Resolve-Usage——那次它可能探到别的答案。
    $arguments += @('-Usage', $Usage)
    # 日志路径必须传下去，否则一次安装会留下两份日志，而开头打印的那一份是空的。
    $arguments += @('-LogPath', ('"' + $script:LogFile + '"'))
    # `-WorkingDirectory` 不是可有可无：不带它时 `Start-Process` 起出来的提权进程经常落在
    # `C:\Windows\System32`。这个脚本本身不依赖工作目录（`$PackageRoot` 由 `$PSScriptRoot`
    # 推出来），但把子进程扔在 System32 里会让它万一去加载什么相对路径的东西时行为诡异。
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments `
        -WorkingDirectory $PSScriptRoot -Wait
    exit 0
}

$script:TempDir = Join-Path $env:TEMP ('xlp-install-' + (New-RandomHex 6))
New-Item -ItemType Directory -Path $script:TempDir -Force | Out-Null

try {
    # --- 2.5 先确认这台电脑上有一个能用的 Python 3.11 x64（第 2 步要用它建虚拟环境）
    #
    # **必须排在问任何口令之前。** `Read-InstallSettings`（第 1 步里）第一个问的就是数据库
    # 口令——让操作员把一串口令敲完、再告诉他「这台机器上没有 Python 3.11」，是白白浪费
    # 他一次输入，而且他会以为口令那一步过了。
    #
    # 此刻 `$script:TempDir` 已经建好（上面那两行，在提权块之后），所以探测能复用
    # `Invoke-Python` 那套「写临时脚本 + 重定向进日志」的机制。
    #
    # 它跑在**提权出来的子进程**里：父进程从头到尾不碰 Python，所以父子不会探到两个不同的
    # 解释器——因此**不需要**像 `-Usage` / `-LogPath` 那样再多传一个参数下去。
    Resolve-BasePython

    # --- 3. 定目录、判断是首次安装还是升级

    Write-Step '第 1 步 / 共 6 步：安装位置'

    $resolvedDir = $InstallDir
    if ([string]::IsNullOrWhiteSpace($resolvedDir) -and (Test-Path -LiteralPath $DefaultInstallDir)) {
        # 装过一次的那台机器上重跑，默认就是「升级那一份」，不用操作员记住路径。
        if (Test-Path -LiteralPath (Join-Path $DefaultInstallDir 'backend\.env')) {
            $resolvedDir = $DefaultInstallDir
        }
    }

    $isUpgrade = $false
    if ($resolvedDir -and (Test-Path -LiteralPath (Join-Path $resolvedDir 'backend\.env'))) {
        $isUpgrade = $true
    }

    $settings = Read-InstallSettings -Upgrade $isUpgrade -ResolvedInstallDir $resolvedDir
    $settings.installDir = $settings.installDir.TrimEnd('\')
    Test-InstallDir -Path $settings.installDir

    if (-not $isUpgrade -and (Test-Path -LiteralPath (Join-Path $settings.installDir 'backend\.env'))) {
        $isUpgrade = $true
    }

    $script:BackendDir = Join-Path $settings.installDir 'backend'
    # 服务用的解释器 = 第 2 步建出来的那个 venv 里的。**它在第 2 步才会存在**，
    # 所以这里只算路径、不校验；第 2 步建完立刻赋值一次（同一个表达式）。
    # 升级时这个路径与上一次相同，于是 `Stop-RunningService` 照样找得到上一版的服务。
    $script:PythonExe = Join-Path $settings.installDir 'runtime\venv\Scripts\python.exe'

    Write-Log ''
    if ($isUpgrade) {
        Write-Log ('模式：升级现有安装（' + $settings.installDir + '）')
    } else {
        Write-Log ('模式：首次安装到 ' + $settings.installDir)
        Write-Log ("数据库：" + $settings.dbUser + '@' + $settings.dbHost + ':' + $settings.dbPort + '/' + $settings.dbName)
    }
    Write-Log ('访问端口：' + $settings.port)

    # 取一次，后面三处（第 4 步、`build.json`、收尾文案）都用它。**走 `Get-SettingText`**：
    # 这个键两条路都放，但「取一项、没有就当空」在这份脚本里只有一个写法，别在这里破例
    # （这个函数存在的理由就是 2026-09-18 那次「属性 dbUser 找不到」）。
    $DatabaseMode = Get-SettingText -Settings $settings -Key 'databaseMode'
    Write-Log ('数据库由谁准备：' + (Get-DatabaseModeLabel $DatabaseMode))

    # --- 4. 铺文件

    Write-Step '第 2 步 / 共 6 步：复制程序文件'

    New-Item -ItemType Directory -Path $settings.installDir -Force | Out-Null

    # 局域网用法要把安装目录的权限放开给 SYSTEM 与 Administrators：服务是以 SYSTEM 跑的，
    # 而如果操作员把目录选在自己的桌面下（`C:\Users\张三\Desktop\…`），继承来的 ACL
    # 是这个用户的——最坏的情况是 SYSTEM 读不到自己的文件，现象是服务起不来而
    # 日志目录里空空如也。`/T` 递归，`(OI)(CI)` 让新文件也继承。
    #
    # 单机用法**跳过**：服务是当前用户自己跑的，没有 SYSTEM 这回事；目录又在用户自己的
    # profile 下（默认就是 `%LOCALAPPDATA%\xinliceping`），继承来的 ACL 本来就对。
    # 往一个用户目录里 `/grant Administrators` 是白送的授权。
    if ($Usage -eq 'lan') {
        try {
            Invoke-Native '设置安装目录权限' 'icacls.exe' @(
                (Quote-Argument $settings.installDir),
                '/grant', 'SYSTEM:(OI)(CI)F', '/grant', 'Administrators:(OI)(CI)F', '/T', '/C', '/Q'
            ) | Out-Null
            Write-Log '安装目录权限已放开给 SYSTEM 与 Administrators'
        } catch {
            # 不致命：装在 `C:\` 下时原来的权限本来就够（SYSTEM 在 `C:\` 上是完全控制）。
            Write-Log ('没能调整安装目录权限（不影响在 C:\ 下安装）：' + $_.Exception.Message) 'WARN'
        }
    }

    # **必须先停服务再复制。**
    Stop-RunningService

    Copy-PackageInto -Source $PackageRoot -Destination $settings.installDir

    # ---------------------------------------------------------------------
    # 用**这台电脑上的** Python 3.11 x64 建一个 venv，再离线装依赖。
    #
    # 2026-09-18 之前这里用的是包里的内嵌 CPython + 一段手写的 wheel 解包器
    # （`Expand-Wheels` + `Set-PythonPathFile`，124 行），它存在的唯一理由就是
    # 内嵌版里没有 pip。包因此多背 21MB（占 zip 一半），目标机上本来就有 Python，
    # 那 21MB 是白付的。
    #
    # **`$script:BasePython` 是 `Resolve-BasePython` 在 try 的第一行探出来的**
    # （早于问数据库口令，所以「没有 Python 3.11」会在操作员开始输密码之前就说出来）。
    #
    # ★★ **这三步的次序不能换：venv → pip → sitecustomize。** 下面那个
    # `venv --clear` 会把 `Lib\site-packages` 整个清掉，`sitecustomize.py` 排在它前面
    # 会被下一次重建**静默**抹掉——编码钩子没了，一直要到英文版 Windows 上某句中文
    # `print` 抛 UnicodeEncodeError 才显形，那时离原因已经很远。
    # ---------------------------------------------------------------------
    Write-Log ('用这台电脑上的 Python 建独立的运行环境：' + $script:BasePython)
    $venvDir = Join-Path $settings.installDir 'runtime\venv'

    # `--clear` 的语义是「**在创建之前删掉那个目录里已有的全部内容**」（CPython 的
    # `venv.rst` 原文：Delete the contents of the virtual environment directory if it
    # already exists, before virtual environment creation.）。它**不检查那是不是一个
    # venv**，所以先自己判一次。今天 `$venvDir` 是由安装目录拼出来的、安全；加这一道是
    # 为了堵住「将来有人把这个变量改成操作员输入的」那条路——那时 `--clear` 会一声不响
    # 删掉一个跟这套系统无关的目录。
    if ((Test-Path -LiteralPath $venvDir) -and
        -not (Test-Path -LiteralPath (Join-Path $venvDir 'pyvenv.cfg'))) {
        Stop-WithError ($venvDir + ' 已经存在，但它不是一个虚拟环境（里面没有 pyvenv.cfg）。' +
            '请手工确认那个目录里没有别的东西，删掉它，再重跑一次安装。')
    }
    Invoke-Python -Exe $script:BasePython -Step '重建虚拟环境' `
        -Arguments @('-m', 'venv', '--clear', $venvDir)

    # 赋一次，与第 1 步那个表达式同一个值。**下面每一处 `Invoke-Python` 都开始用它。**
    $script:PythonExe = Join-Path $venvDir 'Scripts\python.exe'

    # venv 建出来了但里面没有解释器——`venv/__init__.py` 在找不到那个 redirector 源文件时
    # 只打一句 `logger.warning('Unable to copy %r', src)` 就继续（它**不抛**），于是
    # 失败会推迟到很远的地方。企业镜像的裁剪版、embeddable 版被别的软件带进 PATH，
    # 都是这个形状。所以在这里就判，并说清是那台机器的 Python 被裁剪过。
    if (-not (Test-Path -LiteralPath $script:PythonExe)) {
        throw ('虚拟环境建出来了，但里面没有 ' + $script:PythonExe + '——这台电脑上的 Python ' +
            '多半是精简版或被裁剪过的（少了 Lib\venv\scripts\nt\python.exe）。' +
            '请换一个 python.org 的官方安装包装上，再重跑一次。')
    }

    # venv 里要有 pip：`python -m venv` 靠 `ensurepip`，python.org 的安装器上一定有、
    # 裁剪过的解释器上可能没有。缺它时下面那次 `-m pip` 会报「No module named pip」，
    # 读起来像是我们这个包坏了。
    Invoke-Python -Step '确认虚拟环境里的 pip' -Arguments @('-m', 'pip', '--version')

    # 包没打全时给一句中文。**这句是从被删掉的 `Expand-Wheels` 里救出来的**：pip 自己
    # 只会给英文，而这是操作员唯一看得懂的一句话。删那个函数时别把它一起删了。
    $wheelsDir = Join-Path $settings.installDir 'wheels'
    if (-not (Test-Path -LiteralPath $wheelsDir) -or
        @(Get-ChildItem -LiteralPath $wheelsDir -Filter '*.whl').Count -eq 0) {
        throw "wheels 目录是空的：$wheelsDir（包没打全，请拿一个新的部署包重来）"
    }

    # 每个开关都有理由，别顺手删：
    #   --no-index --find-links   完全不碰网络（学校服务器不上外网）
    #   --no-deps                 与下载时一致：`requirements.lock.txt` 本来就是逐条列全
    #                             的叶子列表，让它再解析一次依赖只会引入不确定性
    #   --isolated                忽略 PIP_* 与所有 pip.ini。这台机器上可能有一份全局
    #                             pip 配置指向内网源，而配置里的 find-links 会**叠加**
    #                             上来——最坏是 pip 去联一个不存在的源、按默认的 5 次重试
    #                             × 15 秒卡住，在非技术操作员手上那是「装到一半不动了」，
    #                             不像一个错。
    #   --disable-pip-version-check  少了它 pip 会去问一次 PyPI 上有没有新版本
    #   --no-input --retries 0 --timeout 5   杜绝任何等待输入 / 重试的路径
    #   --only-binary=:all:       万一 wheels\ 里混进 sdist，pip 会去尝试构建它（要编译器），
    #                             报错极难懂
    #   --no-cache-dir            别在这台机器上留一份可能过期的 wheel 缓存
    Invoke-Python -Step '安装依赖' -Arguments @(
        '-m', 'pip', 'install', '--isolated',
        '--no-index', '--find-links', $wheelsDir,
        '--no-deps', '--only-binary=:all:', '--no-input',
        '--disable-pip-version-check', '--no-cache-dir', '--retries', '0', '--timeout', '5',
        '-r', (Join-Path $settings.installDir 'deploy\requirements.lock.txt')
    )

    # **必须排在 pip 之后**（见本节开头的次序理由）。
    Install-ConsoleEncodingHook -SitePackages (Join-Path $venvDir 'Lib\site-packages')
    Write-Log ('Python 环境已就绪：' + $script:PythonExe)

    New-Item -ItemType Directory -Path (Join-Path $settings.installDir 'runtime') -Force | Out-Null

    # 把「装完之后用的那几个按钮」放到安装根目录——它们在包里埋在 deploy\ops\ 下，
    # 因为首次安装之前用不到它们。
    $opsDir = Join-Path $settings.installDir 'deploy\ops'
    if (Test-Path -LiteralPath $opsDir) {
        Get-ChildItem -LiteralPath $opsDir -Filter '*.bat' | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $settings.installDir -Force
        }
        # 拷完就把源目录删掉。**不能留两份**：那些 .bat 里写的是 `cd /d "%~dp0"` 与
        # `"%~dp0deploy\ops.ps1"`，也就是「我在安装根目录」，而它们真实所在的这一层
        # 不是安装根目录——留着的话，谁从 `deploy\ops\` 里双击一个，那个 `%~dp0` 就指向
        # 错的目录，报出来的错与「安装坏了」长得一模一样。**一个按钮一份文件。**
        Remove-Item -LiteralPath $opsDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Log '「启动 / 停止 / 状态」等按钮已放到安装目录'
    }

    # --- 5. 环境变量与 .env

    Write-Step '第 3 步 / 共 6 步：写配置'
    $envFile = Join-Path $settings.installDir 'backend\.env'
    if ($isUpgrade) {
        Write-Log ('.env 已存在，升级只补坏掉的行，改过的值一个字都不动：' + $envFile)
        $answeredPort = $settings.port

        # 数据库连接是唯一一项**补不出来**的（理由见 `Read-DatabaseQuestions` 的注释），
        # 所以它坏了就问一遍，而不是把一句「请自行编辑这个文件」留给非技术的操作员。
        $values = Get-EnvFileValues -Path $envFile
        if (-not (Test-EnvValueOk -Key 'XLP_DATABASE_URL' -Value (Get-EnvValue -Values $values -Key 'XLP_DATABASE_URL'))) {
            Write-Log '这份 .env 里的数据库连接是空的（或者形状不对），重新问一遍（其余配置不动）：' 'WARN'
            $db = Read-DatabaseQuestions
            $settings.dbHost = $db.dbHost
            $settings.dbPort = $db.dbPort
            $settings.dbUser = $db.dbUser
            $settings.dbPassword = $db.dbPassword
            $settings.dbName = $db.dbName
        }

        $notes = Repair-EnvFile -Settings $settings -Path $envFile
        foreach ($note in $notes) { Write-Log $note 'WARN' }
        if (@($notes).Count -eq 0) {
            Write-Log ('.env 检查通过（' + $script:EnvFileKeys.Count + ' 项都有值）')
        }

        # 端口以**文件里那一行**为准，它就是服务真正会监听的那一个（`run_server.py` 从
        # 同一个文件读）。下面那句是**对得上口径**的说明：升级不因为操作员在第 1 步顺手
        # 填了别的端口就改写配置，但那时必须说出来——不说的话，他会以为自己刚改成了它。
        $settings.port = Get-EnvValue -Values (Get-EnvFileValues -Path $envFile) -Key 'XLP_PORT'
        if ($settings.port -ne $answeredPort) {
            Write-Log ('端口沿用配置文件里的 ' + $settings.port + '（升级不改配置；下面的防火墙与提示都按它来）。' +
                       '要改端口：编辑上面那个文件，再双击「重启服务.bat」')
        }
    } else {
        Write-EnvFile -Settings $settings -Path $envFile
        Write-Log ('已写入 ' + $envFile)
        Write-Log ('JWT 密钥：本次随机生成（不要在多个部署之间共用）')

        # **写完立刻读回来验一遍。** 三行之后 Python 要读的就是这一份，而它的失败样子是
        # 一段英文 traceback（第 4 步的依赖冒烟里，`import app.db.session` → `Settings()`）。
        # 这一步存在的理由是一个真实故障：某个版本的安装器在 `.env` 里写出了 `XLP_PORT=`
        # （空的），而安装器直到第 4 步才知道这件事，中间还已经用那个坏配置写过 `build.json`
        # 与防火墙规则。现在它在写入的那一步就停下，并且说人话。
        $notes = Repair-EnvFile -Settings $settings -Path $envFile
        if (@($notes).Count -gt 0) {
            Stop-WithError ('.env 写完自检没通过（多半是上面某一项输入有问题；也可能是安装包自己的问题，' +
                            '请把日志发回来）：' + ($notes -join '；'))
        }
        Write-Log ('.env 自检通过（' + $script:EnvFileKeys.Count + ' 项都有值）')
    }

    # 名单之外、但**是空的**那些 `XLP_*` 行：修不了（不知道它们该是什么值），但看得出来。
    # 报出来是为了把下一次失败从「第 4 步的一段英文 traceback」变成「第 3 步的一句中文」——
    # 2026-09-18 那次排查，来回三轮都在猜 `.env` 里到底是哪一行。
    foreach ($leftover in (Get-EmptyUnknownEnvKeys -Path $envFile)) {
        Write-Log ('配置文件里 ' + $leftover + ' 是空的。安装器不认识这一项，不知道它该是什么值，' +
                   '所以没有动它。**空值会在程序启动时直接报错**（数值与开关型的配置项读不进去）：' +
                   '请把值填上，或者把这一行删掉（删掉就用程序内置的默认值），然后重跑安装。') 'WARN'
    }

    Tighten-EnvPermissions -Path $envFile -Usage $Usage

    # **这里曾经有两行 `$env:PYTHONIOENCODING = 'utf-8'` / `$env:PYTHONUTF8 = '1'`，
    # 2026-09-18 删掉了，别加回来。** 当时留着它们的理由是「`._pth` 让 `use_environment = 0`，
    # 反正不生效，当保险」——内嵌 CPython 一没，那个理由就反转了：**它们现在真的生效**，
    # 但只对**安装进程自己及其子进程**生效。用户之后双击「启动服务.bat」时的环境由他的
    # 会话决定，局域网那条路的计划任务由 Task Scheduler 决定，两处都不受这两行影响。
    # 那就等于「装的时候是好的、平时用的那一份不是」——正是这套资产反复要消掉的那类不一致。
    # 编码这件事由 `Install-ConsoleEncodingHook` 装进去的 `sitecustomize.py` 一个来源管住，
    # 它对每一次解释器启动都成立，与安装器起没起过无关。

    # --- 6. 依赖冒烟 + 建库 + 迁移 + 种子

    Write-Step '第 4 步 / 共 6 步：准备数据库'

    # 先把「依赖在这个 Windows 上真的能 import」验一遍。这是整套里唯一一件只能在
    # Windows 上才知道的事，而它一旦不成立，后面每一步都会以奇怪的方式失败。
    # `app.*` 那几个也必须 import 得动——探针是**按路径跑脚本**的，`sys.path[0]` 因此是
    # 脚本所在的 TempDir 而不是 backend\，靠的是 `Invoke-PythonScript` 里那个
    # `PYTHONPATH`（见它的 docstring）。那一处没了这条会立刻红，而不是等到建库那一步
    # 报「No module named app」。
    Write-Log '检查虚拟环境与依赖（首次运行会慢几秒）'
    Invoke-PythonScript '依赖检查' 'imports' @'
import sys

import fastapi, starlette, sqlalchemy, pymysql, alembic, bcrypt, cryptography, uvicorn
import jwt, pydantic, pydantic_settings, multipart, dotenv, mako, httptools
import websockets, watchfiles, yaml, click, h11, greenlet, cffi, anyio, idna
import markupsafe, typing_extensions

# 应用的入口也要 import 得动。**这一行能成立，靠的是 `Invoke-PythonScript` 给探针设了
# `PYTHONPATH`**（脚本按路径跑，`sys.path[0]` 是脚本自己所在的 TempDir，不是 backend\）。
# 别把它读成「工作目录设对了」——那是 `-m` 那一条路的事。
import app.main, app.db.seed, app.db.create_database, app.db.reset_to_baseline

# `cryptography` 单独再摸一下真正用到的那部分。它是「MySQL 重启之后还能不能连上库」
# 的前提（缓存快路径消失后 pymysql 会走 RSA 握手），而缺它的报错发生在**第一次
# 认证**时，不是 import 时。这里先把它真加载出来，别等到装完才炸。
from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401

print(f"依赖齐全，Python {sys.version.split()[0]}")
'@
    Write-Log '依赖齐全（.pyd 在这个 Windows 上能正常加载）' 'OK'

    # 再确认 `.env` **真的被读到了**。这一步不是多余的：`Settings.env_file` 是相对
    # **当前工作目录**的，找不到那个文件时 pydantic-settings 是**静默**跳过、回落到
    # 硬编码的默认值（`root:password@127.0.0.1`）。那时候后面每一步都连着一个不存在的
    # 库，报出来的错是「Access denied」——与「密码填错了」长得一模一样，而人会去重打
    # 密码。这里只回显**主机/端口/库名与用户名**，口令一个字都不打印。
    Write-Log '确认配置读到了（只回显地址，不含口令）'
    if ($isUpgrade) {
        # 升级模式下 `.env` 是操作员原来的那一份，我们**没有**那份明文口令可以比对
        # （也不该再去问他要一次）。所以这里只把地址念出来——它同样能证明 `.env`
        # 被读到了：没读到的话会打印成硬编码的默认值 `root@127.0.0.1:3306/xinliceping`。
        Invoke-PythonScript '配置自检' 'config-show' @'
from pathlib import Path

from app.core.config import Settings, get_settings
from app.db.mysql_url import parse_database_url

target = parse_database_url(get_settings().database_url)
print(f"沿用现有配置：{target.user}@{target.host}:{target.port}/{target.database}")

# **把「读的是哪一个文件」也念出来。** 这一行是给 2026-09-18 那类故障用的：那时安装器
# 一边在 `C:\xinliceping\backend\.env` 上做文章，一边由 pydantic 抛出一句
# `port … input_value=''`——而没有人知道它究竟读的是哪个文件、文件在不在。
# `env_file` 是**相对当前工作目录**的（安装器让这一步在 `backend\` 下跑），所以这两个
# 事实要一起念：路径是相对的，判它存不存在必须连工作目录一起看。
env_file = Path(str(Settings.model_config.get("env_file"))).resolve()
print(f"配置文件：{env_file}（{'存在' if env_file.is_file() else '不存在'}），工作目录：{Path.cwd()}")
'@
    } else {
        # 首次安装能拿到明文，就做**完整的**比对。口令通过**环境变量**带进去比对，
        # 不走命令行：Windows 上任何账号都能读别的进程的命令行，读不到它的环境块。
        # 比对证明的是「安装器写进去 → 后端解出来」这条链是通的——含 `@` 的口令一旦
        # 有一端漏了百分号编解码，就会以编码形态送去认证，而 MySQL 报的是
        # 「Access denied」，与「密码打错了」一模一样。
        $env:XLPSETUP_EXPECT_PASSWORD = $settings.dbPassword
        try {
            # **退出码必须收下来**（`$configProbeCode =`）：`Set-StrictMode -Version Latest`
            # 下读一个没赋过值的变量会抛「在此对象上找不到属性」那类异常，而这一句是
            # 紧接着那个 `if ($configProbeCode -ne 0)`——不接住就变成一句与自检无关的报错。
            $configProbeCode = Invoke-PythonScript '配置自检' 'config' @'
import os
import sys

from app.core.config import get_settings
from app.db.mysql_url import parse_database_url

expected_user, expected_host, expected_port, expected_db = sys.argv[1:5]
expected_user, expected_host = expected_user.strip(), expected_host.strip()
expected_port, expected_db = expected_port.strip(), expected_db.strip()
target = parse_database_url(get_settings().database_url)

print(f"读到配置：{target.user}@{target.host}:{target.port}/{target.database}")

problems = []
if target.user != expected_user:
    problems.append(f"用户名对不上：期望 {expected_user!r}")
# **主机名比大小写不敏感，其余三项逐字比。** `parse_database_url` 走的是
# `urlsplit(...).hostname`，而它按规范**把主机名转成小写**——操作员输入
# `MySQL01.School.Local` 时，`target.host` 是 `mysql01.school.local`。逐字比会把他
# 挡在「地址对不上」上，而他照着屏幕去改 `.env` 只会越改越乱：那里本来就是对的。
# 库名与用户名**不能**这么放松（MySQL 上它们的大小写敏感性取决于平台与
# `lower_case_table_names`），所以只有主机名这一项例外。
if target.host.strip().lower() != expected_host.lower():
    problems.append(f"地址对不上：期望 {expected_host!r}")
if str(target.port) != expected_port:
    problems.append(f"端口对不上：期望 {expected_port!r}")
if target.database != expected_db:
    problems.append(f"库名对不上：期望 {expected_db!r}")

# 口令只比相不相等，**永远不打印它**（这段输出会进安装日志）。
expected_password = os.environ.pop("XLPSETUP_EXPECT_PASSWORD", None)
if not expected_password:
    problems.append("没有拿到用于比对的口令环境变量")
elif target.password != expected_password:
    problems.append(
        f"口令解出来不对：期望 {len(expected_password)} 位，实际 {len(target.password)} 位"
        "（多半是某一端漏了百分号编解码）"
    )

if problems:
    for line in problems:
        print(f"[X] {line}")
    raise SystemExit(1)

print("配置自检通过：backend\\.env 被正确读到，口令编解码两端一致")
'@ -Arguments @($settings.dbUser, $settings.dbHost, $settings.dbPort, $settings.dbName) -AllowFailure
            if ($configProbeCode -ne 0) {
                # **只警告，不拦安装**（用户 2026-09-18 的口径）。但它必须**看得见**：
                # 在这个修复之前，这个探针因为参数没透传而从来没跑起来过，而日志里
                # 一句话都没有——一句「自检没过」比一个静默的空转强得多，哪怕两者
                # 都不拦安装。装完如果连不上库，操作员发回来的那份日志里就有这一行。
                Write-Log '配置自检没有通过（上面那段输出里写着哪一项对不上）。' 'WARN'
                Write-Log '  安装会照常继续；但装完要是连不上数据库，先回来看这几行。' 'WARN'
            }
        } finally {
            Remove-Item Env:\XLPSETUP_EXPECT_PASSWORD -ErrorAction SilentlyContinue
        }
    }

    # 迁移与种子要在后端目录下跑：alembic.ini 的 script_location 与
    # `-m app.db.seed` 找题库的相对路径都按工作目录算。
    #
    # **建库这一件事按「数据库由谁准备」分叉**（见 `Read-DatabaseMode`）。
    # 选 2 与选 3 共用一个「那个库必须已经在了」的前提，所以它们共用后半支：
    # 先自己探一次连接——这一问的全部前提就是「那个库已经在了」，而下面的迁移是这一步里
    # 第一个碰库的动作；不探的话，一个还没建出来的库会以一段英文 traceback 收场
    # （`Unknown database 'xinliceping'`），而它本该是一句「库那边的事还没做完」。
    # 探针只回显地址，口令一个字都不打印（同第 3 步那条约定）。
    #
    # **判据写成 `-eq 'installer'` 而不是 `-eq 'prepared'`**：三种取值下「我来建库」
    # 恰好只有一种，用 installer 这一支说它，剩下两支自然落到 else 上。写成
    # `if prepared { 探针 } else { 建库 }` 的话，选 3 会掉进 else 去建库——而选 3 的
    # 全部含义就是「库和表都建好了」。
    if ($DatabaseMode -eq 'installer') {
        Invoke-Python '建立数据库' @('-m', 'app.db.create_database')
    } else {
        # 这一句要同时对上选 2 与选 3（**建库**这一件事上两者是同一支，差别在表由谁建）。
        Write-Log '库由你自己准备：安装器不建库，这一步只确认它连得上，'
        Write-Log '接着校对表结构、再把它升到这一版。'
        Invoke-PythonScript '数据库连接自检' 'dbcheck' @'
import pymysql

from app.core.config import get_settings
from app.db.mysql_url import parse_database_url

target = parse_database_url(get_settings().database_url)
try:
    connection = pymysql.connect(connect_timeout=5, **target.connect_kwargs())
except Exception as exc:  # noqa: BLE001 —— 原因由 MySQL 给，原样带出来比我猜一句准
    print(f"[X] 连不上数据库 {target.user}@{target.host}:{target.port}/{target.database}：{exc}")
    print("你选的是「库由你自己准备」——请先把这个库建出来，再重跑一次安装。"
          "《部署说明.txt》里的「数据库我自己准备」与「数据库和表我自己建好了」"
          "两节里各写着该做哪几件。")
    raise SystemExit(1)
connection.close()
print(f"数据库连上了：{target.host}:{target.port}/{target.database}")
'@
    }

    # --- 表结构校对（三种选择共用）
    #
    # **排在迁移之前，而且不分模式。** 这是「表结构归这一版程序」那一半；它挡的是
    # 「库和表我自己建好了」（选 3）独有的那个坑：手写的 DDL 里**没有 `alembic_version`**
    # （那是 Alembic 自己的版本记录表，只有 Alembic 会建），而迁移是无条件跑的——
    # 表已经存在时它会在第一条 `ALTER TABLE … ADD COLUMN` 上撞
    # `Duplicate column name`，报出来是一句英文 MySQL 错，离真正的原因很远。
    # 这个脚本先看一眼：库还是空的就放行，表对得上就补上那本账（`stamp`），
    # 对不上就逐条念出缺什么并停下。判据与那句「只比表名与列名」的克制见
    # `backend/app/db/ensure_schema.py`。
    Invoke-Python '校对表结构' @('-m', 'app.db.ensure_schema')

    # **迁移两条路都跑**，这是这一问里唯一一处刻意的「越界」，理由写在 `Read-DatabaseMode`：
    # 表结构归这一版程序，库与数据归操作员。不跑它的下场是「登录页打得开、别的页面 500」，
    # 而第 6 步那个健康检查探的正是登录页那一个端点——它抓不住。
    Invoke-Python '执行数据库迁移' @('-m', 'alembic', 'upgrade', 'head')

    if ($isUpgrade) {
        Write-Log '升级模式：不重跑种子与基线清理，现有数据原样保留'
        Write-Log '管理员密码也不动（与数据同理：升级不动你已经配好、正在用的东西）'
    } else {
        if ($DatabaseMode -eq 'prepared') {
            # 跳过的这两件东西**都要说出来**，而且要说清它们没做会怎样：一个没有量表与
            # admin 账号的库，打不开的是整个系统，而屏幕上此刻写着「安装完成」。
            Write-Log '按你的选择：不写入基础数据、也不设管理员密码。' 'WARN'
            Write-Log '  这一版需要的基础数据（MHT 量表、admin 账号、基线任务）**没有**写进库里，' 'WARN'
            Write-Log '  除非你准备数据库时已经自己灌过。没灌的话装完打不开任何页面——' 'WARN'
            Write-Log '  照《部署说明.txt》里「数据库我自己准备」那一节的三条命令补上即可。' 'WARN'
        } else {
            # **写任何东西之前先看一眼这个库是不是空的。**
            #
            # 下面那两条命令（`seed` 与 `reset_to_baseline --yes`）的前提是「这是一个刚建出来的
            # 空库」。而安装器怎么判断「首次还是升级」？看**安装目录**在不在，不是看库里有没有
            # 东西——于是「同一个库 + 一个新目录」（为了修一台坏机器的人会做的事，选 3 的人
            # 手上的库更常常是**还原来的一份备份**）落进这一支，而 `reset_to_baseline --yes`
            # 是**无人值守的破坏性脚本**：它把库清成「只有 admin + 量表与基本配置」，
            # 名册、测评、关怀档案、审计行一起没。
            #
            # **只警告不拦**（用户 2026-09-18 的口径）。`-AllowFailure` 收下那个非零退出码，
            # 这里自己说一句话——它自己按「非空 → 退出码 1」写，因为「非空」本身必须是一个
            # 能被断言的事实（见 `backend/app/db/check_empty.py`）。
            $emptyCode = Invoke-Python '检查库是否为空' @('-m', 'app.db.check_empty') -AllowFailure
            if ($emptyCode -ne 0) {
                Write-Log '这个库里已经有数据了。' 'WARN'
                Write-Log '  接着写基础数据时，会把库里原有的名册、测评记录、关怀档案与审计记录' 'WARN'
                Write-Log '  一起清掉（清成「只有管理员 + 量表与基本配置」）。' 'WARN'
                Write-Log '  如果那些数据还要，现在按 Ctrl+C 停下，先做一份备份。' 'WARN'
                Write-Log '  想连种子里的演示名册一起留下：下次带 -KeepData 参数装。' 'WARN'
                Write-Log '  （安装会照常继续——这是提醒，不是拦阻。）' 'WARN'
            }

            Invoke-Python '写入基础数据' @('-m', 'app.db.seed')

            if ($KeepData) {
                Write-Log '按 -KeepData 要求保留了种子里的演示账号与名册' 'WARN'
                Write-Log '（管理员 admin 的密码稍后仍会被设成你刚才输入的那一个）' 'WARN'
            } else {
                Write-Log '清成「只有管理员 + 量表与基本配置」'
                Invoke-Python '清空演示数据' @('-m', 'app.db.reset_to_baseline', '--yes')
            }

            # 口令走环境变量而不是命令行参数：Windows 上任何账号都能读别的进程的命令行
            # （WMI 的 Win32_Process），读不到它的环境块。
            $env:XLPSETUP_ADMIN_PASSWORD = $settings.adminPassword
            try {
                Invoke-Python '设置管理员密码' @('-m', 'app.db.set_admin_password', '--password-env', 'XLPSETUP_ADMIN_PASSWORD')
            } finally {
                Remove-Item Env:\XLPSETUP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
            }
        }
    }

    # --- 7. 记下这次用的用法与端口
    #
    # **写在这里，不写在装完那一刻。** 它是 `ops.ps1` 判断走哪条路的唯一依据，而安装
    # 有可能在第 5、6 步失败——那时用户转头去双击「启动服务.bat」，读不到这个文件就会
    # 回退成 `lan`，于是拿一个单机安装去 `Start-ScheduledTask` 一个不存在的任务。
    # 依赖此时全都齐了（`runtime\` 在第 2 步建好，`$Usage` / 端口 / `$isUpgrade` 早在
    # 第 1、3 步就定了），所以没有理由把它留到后面——**它描述的是「装成了什么」，
    # 而不是「装成功了没有」**。

    # 手拼的 JSON，加字段时注意上一行末尾那个逗号。
    Set-Content -LiteralPath (Join-Path $settings.installDir 'runtime\build.json') -Encoding UTF8 -Value @(
        '{',
        ('  "version": "' + $packageInfo.version + '",'),
        ('  "built_at": "' + $packageInfo.built_at + '",'),
        ('  "installed_at": "' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '",'),
        ('  "install_dir": "' + ($settings.installDir -replace '\\', '\\') + '",'),
        ('  "port": ' + $settings.port + ','),
        ('  "usage": "' + $Usage + '",'),
        # 「这次数据库是谁准备的」。与 usage 同一个理由：装完出故障时第一件要问的就是这个，
        # 而它只由操作员那一次作答决定，事后在 `.env` 里看不出来（两种装法的 `.env` 一模一样）。
        ('  "database_mode": "' + $DatabaseMode + '",'),
        # 建 venv 用的那个解释器。**这是唯一能在故障发生之前看见它的地方**：venv 里
        # `pyvenv.cfg` 的 `home` 是写死的绝对路径，外面那个 Python 一旦被更新、卸载、
        # 或者（用户级安装）那个账号被删，服务就再也起不来——而日志里只有一句
        # `pyvenv.cfg` 相关的英文，没人会联想到「有人升级过 Python」。
        # 「查看状态.bat」会拿这一行去判那个文件还在不在。
        # 反斜杠要转义，与上面 install_dir 同一个写法。
        ('  "base_python": "' + ($script:BasePython -replace '\\', '\\') + '",'),
        ('  "upgraded": ' + $(if ($isUpgrade) { 'true' } else { 'false' })),
        '  "note": "本文件由安装脚本维护，供「查看状态.bat」显示。"',
        '}'
    )

    # --- 8. 后台服务（只有局域网用法才注册）

    Write-Step '第 5 步 / 共 6 步：注册后台服务'

    # 第 2 步在复制之前已经停过一次；这里再停一次是因为中间跑过迁移与种子。
    Stop-RunningService

    if ($Usage -eq 'lan') {
        Register-ServiceTask -Settings $settings
        Add-FirewallRule -Port $settings.port
    } else {
        # 单机用法：服务是这台电脑的主人自己要用的时候手动起的，所以既不注册开机自启，
        # 也不放行防火墙。放行那条规则尤其没必要——`XLP_HOST` 已经是 127.0.0.1，
        # 别的机器本来就够不着，加一条入站规则只是白开一个口子。
        Write-Log '单机用法：不注册计划任务，也不放行防火墙。'
        Write-Log '  要用的时候双击安装目录里的「启动服务.bat」；关机时它会自己停。'
    }

    # --- 9. 起服务并确认它真的活着

    Write-Step '第 6 步 / 共 6 步：启动并确认'

    # 装完自动起一次，**两条用法都要**：否则「装完就能用」这条底线没了，下面那句
    # 健康检查也没东西可查——而那是唯一一次真机验证「这个包装在 Windows 上能跑」
    # 的机会（`.pyd` 加载得动、库连得上、前端挂得上）。局域网那条起完就常驻
    # （计划任务是开机自启的）；单机这条起完就是这个进程，关机才停。
    #
    # **起点只能排在这里**：上面两处 `Invoke-Python` 各自在 `finally` 里清掉了
    # `XLPSETUP_*` 口令环境变量，而 Windows 的子进程会继承环境块——把起服务挪到那两处
    # 之前，等于把数据库口令的明文交给一个长期驻留的进程。
    if ($Usage -eq 'lan') {
        Start-ScheduledTask -TaskName $TaskName
    } else {
        Start-ServiceNow
    }
    $healthy = Wait-ForHealth -Port $settings.port

    # ↓ 从这里到下面那个 `} catch {` 之间**全是说明文字**。包一层，是因为一次成功的
    # 安装曾经在这里被报成失败：2026-09-18，`$lanAddresses.Count` 抛了一次
    # 「在此对象上找不到属性"Count"」，而它下面三行正说着「安装完成，服务已经在
    # 后台运行」。操作员看到的最后一句是「安装没有完成」，下一步是重新双击一遍。
    try {
        Write-Log ''
        if ($healthy) {
            # 两种用法的「跑在哪」不是一回事：单机那条**屏幕上就有一个窗口**，说「在后台运行」
            # 会让操作员找不到它、然后把这个窗口当成没用的东西顺手关掉。
            if ($Usage -eq 'lan') {
                Write-Log '安装完成，服务已经在后台运行（没有窗口）。' 'OK'
            } else {
                Write-Log '安装完成，服务已经起来了——就是屏幕上那个新开的黑窗口。' 'OK'
            }
        } else {
            # 秒数与 `Wait-ForHealth` 的默认超时**必须一样**（那里是 120）。写 90 的那一版
            # 与实现不符，偏偏这一句是操作员唯一看得到的诊断。
            Write-Log '程序都装好了，但服务在 120 秒内没有应答。多半是数据库连不上。' 'WARN'
            if ($DatabaseMode -eq 'prepared') {
                # 这一问选 2 时，「服务起不来」的头号原因不是密码错了，而是**库还没准备好**
                # ——那是操作员自己那一半的活，所以他手上最缺的是一句指路的话，而不是
                # 「请把日志发回来」。（日志那一行照样打，两件事不冲突。）
                Write-Log '你选的是「库我自己准备好」：先照《部署说明.txt》' 'WARN'
                Write-Log '「数据库我自己准备」那一节把库建好、表建好、基础数据灌好，' 'WARN'
                Write-Log '再双击安装目录里的「启动服务.bat」。' 'WARN'
            }
            Write-Log ('请把日志发回来：' + (Join-Path $settings.installDir 'runtime\logs\server.log')) 'WARN'
            Write-Log ('安装日志：' + $script:LogFile) 'WARN'
        }

        Write-Log ''
        Write-Log '======================================================'
        if ($Usage -eq 'lan') {
            Write-Log ' 装完了。老师这样访问：'
            # 这一段**只有局域网用法有意义**：单机模式监听 127.0.0.1，把局域网地址印出来
            # 会让装的人以为同事连得过来——而那个地址打不开，且看起来像是网络坏了。
            # `@(...)` 不是多余的：没有地址时它才会是 `@()` 而不是 `$null`（见那个函数的注释）。
            $lanAddresses = @(Get-LanAddresses)
            if ($lanAddresses.Count -gt 0) {
                foreach ($address in $lanAddresses) {
                    Write-Log ('     http://' + $address + ':' + $settings.port + '/')
                }
                Write-Log '   （上面是这台机器的局域网地址；如果有多条，用和老师电脑同一网段的那个）'
            } else {
                # 「认不出来」与「这台机器根本没有地址」是两件事，分开说：前者让操作员去
                # 翻 ipconfig（地址在，只是没认出来），后者意味着他现在**连不上网**——
                # 那种情况下把地址给同事也打不开，先解决网络。两种都说，因为从这台机器上
                # 分不出是哪一种，而操作员照哪一句做都不会白跑。
                Write-Log '   （没能自动认出来这台机器的局域网地址——用 ipconfig 看一下 IPv4 地址；'
                Write-Log '     如果一条 IPv4 都没有，说明这台机器现在没连上网，先把网络弄通）'
            }
            Write-Log ('     本机访问：http://127.0.0.1:' + $settings.port + '/')
        } else {
            Write-Log ' 装完了。这样打开：'
            Write-Log ('     http://127.0.0.1:' + $settings.port + '/')
            Write-Log '   （只有这台电脑打得开，别的电脑连不过来——这是你选的「单机」用法）'
            Write-Log '   （如果老师们本来就该连过来，那是刚才第 0 个问题选错了：双击「卸载.bat」，'
            Write-Log '    再重跑一次「一键安装.bat」并选 2。用法不能半路换。）'
            Write-Log ''
            # ★ 这一段是这次改动**最容易被当成 bug 报回来**的一点，所以它必须在这里出现：
            # 装完屏幕上多了一个黑窗口，而在此之前单机用法是「静悄悄地在后台跑」的。
            # 用户会以为那是安装程序留下的残渣，顺手关掉——那就是把服务关了。
            # 另外写明「注销也会停」：窗口属于这个登录会话，与「关机就停」是同一件事的两面。
            Write-Log ' ★ 屏幕上那个黑窗口就是服务本身，装完也在跑。'
            Write-Log '   要停服务就把它关掉；**别的窗口都可以关，这个不要关**。'
            Write-Log '   注销或重启这台电脑也会停（那正是「关机就停」的意思）。'
            Write-Log '   关掉之后要再用，双击「启动服务.bat」——它会再开一个同样的窗口。'
        }
        Write-Log ''
        Write-Log ' 登录账号：admin'
        if ($DatabaseMode -eq 'prepared' -and -not $isUpgrade) {
            # 这一支里**没有**那句「你刚才输入的那一个」——这次根本没问过密码，
            # 报一串它出来就是在编。而「怎么没问我密码」和「那我用哪个密码」是同一个
            # 问题的两半，所以两半都要答，出口（重置）也要给。
            Write-Log ' 密码：安装器没有设过它（这次是你自己准备的数据库）'
            Write-Log '       用的应该是你准备数据库时那个；忘了不要紧，双击安装目录里的'
            Write-Log '       「重置管理员密码.bat」重设一个就行。'
        } elseif (-not $isUpgrade) {
            Write-Log ' 密码：你刚才输入的那一个（首次登录会要求改掉它）'
        } else {
            # 升级**不动**管理员密码（第 4 步那个 `if ($isUpgrade)` 分支），所以这里不能
            # 凭空报一串密码出来。但也不能什么都不说：一个忘了密码的操作员把这一整段
            # 读完，能回答他的只有下面这两句。2026-09-18 用户问的正是这个
            # （「我重新安装时要注意什么，admin 密码怎么设置」）。
            Write-Log ' 密码：这次没有改，还是原来那一个'
            Write-Log '       （忘了不要紧：双击安装目录里的「重置管理员密码.bat」重设一个就行，'
            # 这里刻意**不写「输错几次锁多久」**：那是 `XLP_PASSWORD_LOCK_THRESHOLD` /
            # `_MINUTES` 两个可配的项（装出来的 `.env` 里没有它们，走默认值，但有人手工
            # 加过就与这句话对不上了）。写死的数字会静默变错，而操作员这时需要知道的
            # 只有一件事：**账号被锁不用等，重设密码会连带解锁**。
            Write-Log '        不用重装。密码连输错几次会把账号临时锁住，重设密码时会一并解锁。）'
        }
        Write-Log ''
        Write-Log ' 接下来该做的三件事：'
        Write-Log '   1. 用上面的地址打开网页，登录 admin'
        Write-Log '   2. 到「账号与权限」里建心理老师和德育领导的账号（手机号登录）'
        Write-Log '   3. 到「组织学生」导入学生名册'
        Write-Log ''
        # 「重置管理员密码」必须是安装目录里那个 .bat 的**原名**。这里原本写的是
        # 「改管理员密码」，而磁盘上叫 `重置管理员密码.bat`——操作员按屏幕上这四个字
        # 去目录里找，找不到同一个东西。与审计页那条「文案一改就把「看不懂」换成了
        # 「搜不到」」同源，只是换成了一次文件系统查找。
        Write-Log ' 以后要停 / 起 / 备份 / 重置管理员密码，双击安装目录里对应的那几个 .bat。'
        if ($Usage -eq 'lan') {
            Write-Log ' 这台机器重启之后服务会自己起来，不需要有人登录。'
        } else {
            Write-Log ' 这台电脑重启之后**不会**自己起来：要用的时候双击「启动服务.bat」，'
            Write-Log ' 用完把它开出来的那个窗口关掉就停了。（这次装完已经替你起好了，现在就能用。）'
            # 安装目录一定要打出来：单机用法下它埋在 `%LOCALAPPDATA%` 里，用户靠自己翻
            # 是翻不到的——而那几个 .bat 就在那儿。
            Write-Log ''
            Write-Log ' 那几个 .bat 在这个目录里（不是 C:\xinliceping）：'
            Write-Log ('     ' + $settings.installDir)
        }
            Write-Log '======================================================'
    }
    catch {
        # 走到这里说明**上面那句 `$healthy` 已经为真**：服务在跑、端口在应答、
        # 计划任务/窗口都已经就位。所以这一段的异常**不可能**是「安装失败」，
        # 只能是「说明没打完」——两件事必须在屏幕上长得不一样。
        # 异常原文照样进日志（`Write-Log` 落盘），**不吞**。
        Write-Log ''
        Write-Log ('收尾说明没能打完：' + $_.Exception.Message) 'WARN'
        Write-Log '但服务已经在跑了，**安装是成功的**——上面那个地址照样能打开。' 'WARN'
        Write-Log ('这一条也请发回来：' + $script:LogFile) 'WARN'
    }
}
catch {
    Write-Log ''
    Write-Log ('安装没有完成：' + $_.Exception.Message) 'ERROR'
    Write-Log ('安装日志（把这个文件发回来）：' + $script:LogFile) 'ERROR'
    Write-Log '已经做过的步骤不用重来，重新双击一次「一键安装.bat」即可。' 'ERROR'
    Wait-BeforeExit
    exit 1
}
finally {
    Remove-Item -LiteralPath $script:TempDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:\XLPSETUP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
}

Wait-BeforeExit
