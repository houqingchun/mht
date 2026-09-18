<#
  心晴 · 装完之后的那几个按钮

  安装根目录下那八个 .bat 各自调这个脚本的一个 `-Action`。**它们都是三行壳子**，
  所有逻辑在这里，因为运维要改的是逻辑，不是八个 .bat。

  安装目录**由本文件的位置推出来**，不写死在任何地方：

      <安装目录>\deploy\ops.ps1   ->   Split-Path $PSScriptRoot -Parent   ->   <安装目录>

  所以把整个目录搬到别处也照样能用，而 .bat 从哪里双击都不影响。

  ---------------------------------------------------------------------------
  两个刻意的选择
  ---------------------------------------------------------------------------

  1. **提权是逐动作判断的**，不是一进门就要。`status` / `log` 不需要管理员，
     为了看一眼状态弹一次 UAC 是没必要的摩擦。只有真需要的那几个
     （start / stop / restart / reset-admin / uninstall）才 `Start-Process -Verb RunAs`。
     而且**只在「局域网」用法下才需要**：单机安装不动计划任务、不动防火墙、程序就在
     用户自己的目录里，那几个动作全都不需要管理员。用法从 `runtime\build.json` 的
     `usage` 字段读（见 `Get-InstalledUsage`）。

  2. **备份走 `mysqldump` + `--defaults-extra-file`**，口令不写命令行。
     Windows 上任何账号都能读别的进程的命令行（WMI 的 `Win32_Process`），而这是一所
     学校的服务器：`mysqldump -p口令` 等于把库的口令贴在一份所有人可读的表上。
     那个临时配置文件用完立刻删，中途的窗口用 `icacls` 收窄。
     它由 `python -m app.db.mysql_url --client-file` 生成——**不在 PowerShell 里再写一份
     URL 解析器**，因为那必然与后端那一份漂移，而含 `@` 的口令会在其中一边悄悄坏掉。
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'log', 'backup', 'reset-admin', 'uninstall')]
    [string]$Action,
    [switch]$Elevated,          # 内部用：标记「已经提过权了」
    [string]$BackupDir = ''     # 备份去哪儿，默认 <安装目录>\backups
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$InstallDir = Split-Path $PSScriptRoot -Parent
$TaskName = 'xinliceping'
$FirewallRuleName = 'XinQing Platform 8000'
# 2026-09-18 起运行环境是**安装时用这台电脑上的 Python 3.11 建的 venv**，包里不再带
# 内嵌 CPython。注意这个 `Scripts\python.exe` **不是解释器**，是一个转发壳——所以
# `Get-ServiceProcess` 必须收整棵进程树，见那里。
$PythonExe = Join-Path $InstallDir 'runtime\venv\Scripts\python.exe'
$BackendDir = Join-Path $InstallDir 'backend'
$EnvFile = Join-Path $BackendDir '.env'
$LogFile = Join-Path $InstallDir 'runtime\logs\server.log'
$BuildInfo = Join-Path $InstallDir 'runtime\build.json'

function Write-Line {
    param([string]$Message = '', [ValidateSet('INFO', 'OK', 'WARN', 'ERROR')][string]$Level = 'INFO')
    switch ($Level) {
        'ERROR' { Write-Host $Message -ForegroundColor Red }
        'WARN' { Write-Host $Message -ForegroundColor Yellow }
        'OK' { Write-Host $Message -ForegroundColor Green }
        default { Write-Host $Message }
    }
}

function Stop-WithError {
    param([string]$Message)
    Write-Line ''
    Write-Line $Message 'ERROR'
    exit 1
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal $identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-InstalledUsage {
    <#
      这份安装当初是按哪种用法装的（`single` / `lan`）。判据是 `runtime\build.json`
      的 `usage` 字段——install.ps1 写进去的那一个。

      名字叫 `Get-InstalledUsage` 而不是 `Get-Usage`：`install.ps1` 里那个同名的东西
      （`Resolve-Usage`）回答的是「这次要按哪种用法装」，是**决定**；这一个回答的是
      「当初装成了哪种」，是**读取**。两个文件各有一个 `Get-Usage` 时，改错地方
      不会有任何东西报错。

      **读不到、文件不在、字段不在，一律回退 `lan`。** 那个字段是 2026-09-18 才有的，
      此前装出来的包全是局域网那一种。回退成 `single` 会让 start / stop 改用「直接起
      进程」的方式，那些服务器上的服务从此再也起不来——而「查看状态」还会说一句
      「单机用法：没有计划任务」，看起来像是设计，不是坏了。

      只有 `single` 才返回 `single`：其余一切（包括一个写坏的、认不出的值）都当 `lan`，
      因为 `lan` 那条路是这次改动之前唯一存在过的那一条。
    #>
    $json = Get-BuildJson
    if (-not $json) { return 'lan' }
    if ($json.PSObject.Properties.Name -notcontains 'usage') { return 'lan' }
    if ([string]$json.usage -eq 'single') { return 'single' }
    return 'lan'
}

function Get-InstalledDatabaseMode {
    <#
      这份安装当初是**谁准备的数据库**（`installer` / `prepared` / `schema_prepared`）。
      判据同样是 `runtime\build.json`（install.ps1 写进去的 `database_mode`）。

      **它与 `Get-InstalledUsage` 的回退方向正好相反：读不到就返回空串，不猜一个值。**
      理由是这两种回退要赔的东西不一样：用法猜错会让服务起不来（所以必须挑一个「此前
      唯一存在过的那条路」= lan），而这一项**只用来在「查看状态」里多说一句中文**——
      底下那一块原始 JSON 照样把它印出来，猜一个值出来只是把「不知道」说成「知道」，
      而这一页存在的全部意义就是让操作员相信上面写的字。

      所以认不出的值（写坏的、下一版新加的码）也一律返回空串：状态页那时什么都不说，
      而原始 JSON 就在旁边。
    #>
    $json = Get-BuildJson
    if (-not $json) { return '' }
    if ($json.PSObject.Properties.Name -notcontains 'database_mode') { return '' }
    return [string]$json.database_mode
}

function Get-BuildJson {
    <#
      `runtime\build.json` 解析成一个对象；文件不在 / 读不动 / 不是合法 JSON 时返回 `$null`。

      **只在这里解析。** 读它的地方有两个（`Get-InstalledUsage` 读 `usage`，
      `Show-Status` 读 `base_python`），各解一遍就会有两条 try/catch 分支——
      一条坏了另一条还绿着，而「状态页显示不出来」与「start 走错了路」是两件很不一样的事。
    #>
    if (-not (Test-Path -LiteralPath $BuildInfo)) { return $null }
    try {
        return (Get-Content -LiteralPath $BuildInfo -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-ServiceProcess {
    <#
      我们这个后台服务的**进程树**（可能不止一个）。返回的对象形状不变（`Get-Process`
      的，有 `.Id` / `.Path`），所以三个调用点一行都不用改。

      **判据是进程，不是 HTTP 探活。** 服务刚起的那一两秒端口还没绑上，探活会说
      「没在跑」，于是用户再点一次「启动服务.bat」就起了第二个。第二个绑不上端口，
      而 `run_server.py` 的 `while True` 会**每 30 秒重试一次**，日志里滚一屏
      address-in-use——而服务其实是好好的。局域网那条路用 `Start-ScheduledTask`，
      它自带 `-MultipleInstances IgnoreNew`，所以那种重复启动从来不会发生。

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

          <安装目录>\runtime\venv\Scripts\python.exe  ← 壳，Path 与 $PythonExe 相等
                  └─ <base python>\python.exe         ← 真占端口、真连库、真锁 .pyd

      **Windows 不连坐**：只按 `Path` 找到壳、只杀壳，真正的服务活得好好的。于是
      「停止服务.bat」会打印「服务已停止。」而端口还在应答，用户合上窗口走人。
      所以这里连直接子进程一起收。

      按 `Path` 比对**根**而不是按名字：那台机器上可能还有别人在跑别的 Python；
      `$PythonExe` 是安装目录里的全路径，天然唯一。**但不要顺手拿 base python 的路径
      再去比一遍**：同一台机器上别人跑的 python 也是那个 base，那样会误伤。

      **`catch { $false }` 那个盲点还在，而且现在是两层**：`Path` 对跨账号、跨完整性
      级别的进程读不出来（不是抛异常，是给空值），那种进程被判成「不在跑」，
      **它的子进程也就跟着看不见**。而「右键 → 以管理员身份运行了启动服务.bat」是一条
      真实存在的路径。start / stop 因此都拿 HTTP 探活当**第二判据**（见各自的调用点）
      ——那条现在是唯一的兜底，别摘。
    #>
    $ids = [System.Collections.Generic.List[int]]::new()
    $roots = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $PythonExe })
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

function Get-Port {
    if (-not (Test-Path -LiteralPath $EnvFile)) { return '8000' }
    $line = Select-String -LiteralPath $EnvFile -Pattern '^XLP_PORT=' | Select-Object -First 1
    if ($line) { return $line.Line.Substring('XLP_PORT='.Length).Trim() }
    return '8000'
}

function Invoke-Python {
    param([string[]]$Arguments)
    # 统一在**这里**加引号，而不是在各个调用点上：调用点写的是「参数」，
    # 拼接是这一层的事。少一处漏掉就少一次「路径里有个空格，参数被拆成两个」。
    $quoted = @($Arguments | ForEach-Object { Quote-Argument $_ })
    $process = Start-Process -FilePath $PythonExe -ArgumentList $quoted `
        -WorkingDirectory $BackendDir -NoNewWindow -Wait -PassThru
    return $process.ExitCode
}

function Quote-Argument {
    <#
      给一个参数加引号。**`Start-Process -ArgumentList` 自己不做任何转义**：
      数组会被用空格拼成一条命令行，于是 `C:\Program Files\xinliceping\...`
      到 mysqldump 眼里就是两个参数。而带空格的安装目录是合法的
      （`Test-InstallDir` 挡的是 `% ! & | < > ^ "`，没有挡空格）。

      转义规则是 MSVCRT 的：反斜杠只有紧挨着引号时才有特殊含义，
      所以要先把 `\"` 变成 `\\"`，再把结尾的反斜杠加倍。
    #>
    param([string]$Value)
    if ($Value -eq '' -or $Value -match '[\s"]') {
        $escaped = $Value -replace '(\\*)"', '$1$1\"'
        return '"' + ($escaped -replace '(\\+)$', '$1$1') + '"'
    }
    return $Value
}

# ---------------------------------------------------------------- 提权

$Usage = Get-InstalledUsage
$DatabaseMode = Get-InstalledDatabaseMode

# 这几个动作动的是系统级的东西（计划任务、防火墙、密码、卸载），必须管理员——
# **但只有局域网用法才有那些系统级的东西**。单机安装：没有计划任务、没有防火墙规则、
# 程序在用户自己的 profile 下，起停是直接对那个 python.exe 动手。于是它一个 UAC 都不用弹。
#
# `reset-admin` 在名单里**不是因为要提权**——它改的是数据库里的一行。它在这里是因为
# **局域网用法下的 `backend\.env` 只有 SYSTEM 与 Administrators 读得到**
# （`install.ps1` 的 `Tighten-EnvPermissions`：`/inheritance:r` 之后只授这两项），
# 而它要读 `.env` 才连得上库。不加这一条，普通用户点下去拿到的是 python 那边一句
# 读文件失败的报错——事是没做错，但没人看得出要换管理员身份来点。
# 单机用法把当前用户也授进去了，所以那条路上它照样不需要管理员。
$needsAdmin = ($Action -in @('start', 'stop', 'restart', 'reset-admin', 'uninstall')) -and $Usage -eq 'lan'

if ($needsAdmin -and -not (Test-Administrator)) {
    if ($Elevated) {
        Stop-WithError '已经请求过管理员权限但仍未获得。请右键对应的 .bat →「以管理员身份运行」。'
    }
    Write-Line '这一步需要管理员权限，接下来会弹出一个授权窗口。'
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'),
        '-Action', $Action, '-Elevated'
    )
    if ($BackupDir) { $arguments += @('-BackupDir', ('"' + $BackupDir + '"')) }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments `
        -WorkingDirectory $PSScriptRoot -Wait
    exit 0
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    # 两种原因要分开说，因为处置完全不同：脚本放错了地方（这是个开发/运维错误，
    # 把 deploy\ 整个目录挪对就行）vs 运行环境不在了（安装时建的 venv 被删了、
    # 或者装到一半失败了）。**这个运行环境是安装时现建的，包里没有它**——
    # 所以「少了它」的正确处置是重跑安装，不是去找一个根本不存在的 python\ 目录。
    Stop-WithError (@(
        "找不到 $PythonExe",
        '    这个脚本要放在安装目录下的 deploy\ 里（和 runtime\、backend\ 并排）。',
        '',
        '    如果目录是对的，那就是运行环境不在了——它是安装的时候用这台电脑上的',
        '    Python 3.11 建出来的，不在部署包里。重跑一次「一键安装.bat」会把它重建出来；',
        '    数据库里的东西一行都不会动。'
    ) -join [Environment]::NewLine)
}

# ---------------------------------------------------------------- 各动作

function Get-TaskState {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $null }
    return $task.State
}

function Test-ServiceHealth {
    param([string]$Port)
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/v1/public/branding" `
            -UseBasicParsing -TimeoutSec 5
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-ServiceHealthy {
    # 起完之后的探活。两条用法共用——「装完就能用」这条底线对两边都成立。
    $port = Get-Port
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        if (Test-ServiceHealth -Port $port) {
            Write-Line "服务已经起来了：http://127.0.0.1:$port/" 'OK'
            return
        }
        Start-Sleep -Seconds 2
    }
    Write-Line '90 秒内没有等到服务应答。' 'WARN'
    Write-Line "请用「查看日志.bat」看看 $LogFile 的最后几行。" 'WARN'
    Write-Line '最常见的原因是 MySQL 服务没有起来——服务会自己一直重试，MySQL 一好它就会通。' 'WARN'
}

function Start-Service {
    if ($Usage -eq 'lan') {
        if (-not (Get-TaskState)) {
            Stop-WithError "没有找到计划任务 $TaskName —— 这台机器上可能还没安装过，或者被谁删掉了。请重新运行「一键安装.bat」。"
        }
        Write-Line '正在启动服务……'
        Start-ScheduledTask -TaskName $TaskName
    } else {
        # 单机用法：没有计划任务，直接把那个 python.exe 起起来。
        #
        # **不能加 `-NoNewWindow`。** 它让子进程共用这个 .bat 的控制台，于是 .bat 一退出
        # 控制台就关、子进程收到 CTRL_CLOSE 当场死掉——「启动服务」会变成「启动一下然后
        # 立刻停」。这条推理与它当初写下时一字不差，**结论反了过来**：2026-09-18 用户
        # 明确要求「用户使用时启动，关机时停止」，那个心智模型里的动作是**关掉一个看得见
        # 的窗口**。所以窗口样式按用法分（见下面 `Start-Process` 那一处）。
        # **进程与端口两个判据都要看，缺一个就会起出第二个进程。**
        #
        # `Get-ServiceProcess` 对**跨账号、跨完整性级别**的进程是瞎的：`Path` 读不出来，
        # 那一行 `catch { $false }` 把它判成「不在跑」。而「右键 → 以管理员身份运行了
        # 启动服务.bat」是一条真实存在的路径。只看它的话，服务明明在跑，这里会再起
        # 一个：第二个绑不上端口，`run_server.py` 的 `while True` 于是**每 30 秒重试
        # 一次**，日志里滚一屏 address-in-use——而下面的探活拿**旧进程**的应答报
        # 「服务已经起来了」，用户看到的是成功。
        if (Get-ServiceProcess) {
            Write-Line '服务已经在跑了，不再启动第二个。' 'OK'
        } elseif (Test-ServiceHealth -Port (Get-Port)) {
            Write-Line '服务已经在跑了（它是以别的账号或管理员身份起的，这里看不到它的进程）。' 'OK'
            Write-Line '不再启动第二个——两个进程会抢同一个端口，谁也起不好。' 'OK'
        } else {
            Write-Line '正在启动服务……'
            # ★ **窗口样式按用法分，不能一刀切。** 这个函数两种用法共用：
            #   · 单机（`Normal`）——屏幕上开一个**可见**的窗口，关掉它就是停服务。这是用户
            #     要的心智模型（「使用时启动，关机时停止」）；隐藏起来的话他看不到它在跑，
            #     要停还得回来点这个 .bat。
            #   · 局域网（`Hidden`）——这条路的语义是「无窗口地在后台跑」，而且下次开机计划
            #     任务还会再起一个。在这里弹一个可见窗口既不合语义，也会让运维以为服务
            #     是「手起着」的。
            # `if` 写在括号里而不是先算一个变量：这个脚本开着 `Set-StrictMode -Version Latest`，
            # 多一个赋值点就多一处可能没被赋值的地方。
            Start-Process -FilePath $PythonExe -ArgumentList 'run_server.py' `
                -WorkingDirectory $BackendDir -WindowStyle $(if ($Usage -eq 'single') { 'Normal' } else { 'Hidden' })
            if ($Usage -eq 'single') {
                Write-Line '服务在**另一个**窗口里跑。关掉那个窗口才是停止服务；这个窗口关掉不影响它。' 'OK'
            }
        }
    }
    Wait-ServiceHealthy
}

function Stop-Service {
    if ($Usage -eq 'lan') {
        if (-not (Get-TaskState)) { Stop-WithError "没有找到计划任务 $TaskName。" }
        Write-Line '正在停止服务……'
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        # 计划任务的「停止」是硬杀：run_server.py 收不到信号，所以它那个重试循环不会
        # 把它自己拉起来——正是我们要的。`Stop-Process -Force` 同理。
        Start-Sleep -Seconds 2
    } else {
        Write-Line '正在停止服务……'
    }
    $alive = @(Get-ServiceProcess)
    if ($alive.Count -gt 0) {
        Write-Line '进程还在，强制结束……' 'WARN'
        $alive | Stop-Process -Force
        Start-Sleep -Seconds 1
    }

    # **不能说一句自己没验证过的话。** 上面那个判据对以别的账号 / 管理员身份起的进程
    # 是瞎的（理由见 `Start-Service`）。端口还在应答就说明确实没停掉，这时该说的不是
    # 「服务已停止。」——那句话会让操作员合上窗口走人，而服务还占着端口，
    # 下一次「启动服务」又要撞一次它。
    if (Test-ServiceHealth -Port (Get-Port)) {
        Write-Line '服务还在应答，没有停下来。' 'WARN'
        Write-Line '多半是它以别的账号（或管理员身份）起的，这个窗口看不见、也停不了它。' 'WARN'
        Write-Line '请打开「任务管理器」→「详细信息」，找到 python.exe 手动结束。' 'WARN'
    } else {
        Write-Line '服务已停止。' 'OK'
    }
}

function Show-Status {
    Write-Line ''
    Write-Line '================ 心晴平台 · 运行状态 ================'

    if (Test-Path -LiteralPath $BuildInfo) {
        Write-Line '安装信息：'
        foreach ($line in Get-Content -LiteralPath $BuildInfo -Encoding UTF8) {
            Write-Line ('  ' + $line)
        }
        # 上面那一块是原始 JSON，`"usage": "single"` 对着一个非技术的操作员等于没说。
        # 「用法」决定了这一页下面所有东西该长什么样，所以要用中文再说一遍。
        if ($Usage -eq 'lan') {
            Write-Line '  用法：局域网（老师们都连过来；服务开机自启）'
        } else {
            Write-Line '  用法：单机（只有这台电脑用；要用时双击「启动服务.bat」）'
        }

        # 与上面那句同一个理由（原始 JSON 对非技术操作员等于没说），而这一项在这里的
        # 诊断价值很高：装的时候选了「库我自己准备」的那台机器上，「页面打不开」的头号
        # 原因不是密码、不是 MySQL 没起来，而是**那个库还没准备好**（库/表/基础数据三样
        # 里缺一样），而这一件事在 `backend\.env` 里看不出来——两种装法的 `.env` 一字不差。
        # **认不出就不说**（`Get-InstalledDatabaseMode` 返回空串走不到任何一支）。
        if ($DatabaseMode -eq 'prepared') {
            Write-Line '  数据库：你自己准备的那一个（安装时没有建库、没有灌基础数据）'
        } elseif ($DatabaseMode -eq 'schema_prepared') {
            Write-Line '  数据库：库和表都是你自己准备的（基础数据与管理员密码由安装器写入）'
        } elseif ($DatabaseMode -eq 'installer') {
            Write-Line '  数据库：安装时由「一键安装」准备好（建库、灌基础数据、设管理员密码）'
        }

        # 这一行是**唯一一处能在故障发生之前看见它**的地方。venv 里 `pyvenv.cfg` 的
        # `home` 是安装时写死的绝对路径，指向这台电脑上那个 Python 3.11；它一旦被更新、
        # 卸载（学校统一升 3.12 顺手卸旧的），或者装的是用户级而那个账号被删，服务就再也
        # 起不来——而日志里只有一句 `pyvenv.cfg` 相关的英文，没人会联想到「有人动过 Python」。
        $json = Get-BuildJson
        if ($json -and ($json.PSObject.Properties.Name -contains 'base_python')) {
            $base = [string]$json.base_python
            if ($base -and (Test-Path -LiteralPath $base)) {
                Write-Line ('  这套系统依赖的 Python：' + $base + '（还在）')
            } else {
                Write-Line ('  这套系统依赖的 Python：' + $base + '（**不在了**）') 'ERROR'
                Write-Line '  这个 Python 被卸载或挪走了，服务起不来。重装一个 Python 3.11（64 位）' 'ERROR'
                Write-Line '  到同一个位置，或者重跑一次「一键安装.bat」重新建运行环境。' 'ERROR'
            }
        }
    } else {
        Write-Line "没有找到 $BuildInfo（这台机器可能不是用安装包装的）" 'WARN'
    }

    Write-Line ''
    if ($Usage -eq 'lan') {
        $state = Get-TaskState
        if ($state) {
            Write-Line ("计划任务 $TaskName ：" + $state)
        } else {
            Write-Line "计划任务是缺失的——开机不会自动启动。" 'ERROR'
        }
    } else {
        # 单机用法**本来就没有计划任务**，所以这里不能照搬那句 ERROR：它会把「设计如此」
        # 报成一个故障，而操作员会去找一个不存在的问题。
        $alive = @(Get-ServiceProcess)
        if ($alive.Count -gt 0) {
            Write-Line ('后台进程：在跑（进程号 ' + $alive[0].Id + '）')
        } else {
            Write-Line '后台进程：没有在跑。要用的时候双击「启动服务.bat」。' 'WARN'
        }
        Write-Line '单机用法：没有计划任务，关机和重启之后不会自己起来。'
    }

    $port = Get-Port
    if (Test-ServiceHealth -Port $port) {
        Write-Line ("网页服务：正常（http://127.0.0.1:$port/）") 'OK'
    } else {
        Write-Line '网页服务：没有应答。' 'ERROR'
        Write-Line '  先看「查看日志.bat」；如果日志里一直在说连不上数据库，那就是 MySQL 没起来。' 'ERROR'
    }

    # 本机地址：老师要的就是这一行。
    #
    # **单机用法不打印这一块。** 它监听 127.0.0.1，别的机器本来就够不着；把局域网地址
    # 印出来只会让装的人以为同事连得过来，然后拿着一个打不开的地址去问为什么。
    if ($Usage -eq 'lan') {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object {
                $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and
                ($_.IPAddress -like '10.*' -or $_.IPAddress -like '192.168.*' -or
                 $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.')
            } | Select-Object -ExpandProperty IPAddress -Unique
        Write-Line ''
        if ($addresses) {
            Write-Line '老师访问的地址：'
            foreach ($address in $addresses) { Write-Line ("    http://${address}:${port}/") }
        } else {
            Write-Line '没有认出来本机的局域网地址，用 ipconfig 看一下 IPv4 地址。' 'WARN'
        }
    }

    $logDirectory = Split-Path -Parent $LogFile
    if (Test-Path -LiteralPath $logDirectory) {
        $total = (Get-ChildItem -LiteralPath $logDirectory -Filter 'server.log*' -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        if ($total) {
            Write-Line ''
            Write-Line ("日志占了 {0:N1} MB（在 {1}）" -f ($total / 1MB), $logDirectory)
        }
    }
    Write-Line '===================================================='
    Write-Line ''
}

function Show-Log {
    if (-not (Test-Path -LiteralPath $LogFile)) {
        Stop-WithError "还没有日志文件：$LogFile`n    服务可能一次都没起来过。先运行「启动服务.bat」再看。"
    }
    $lines = [int]30
    Write-Line ''
    Write-Line ("================ $LogFile 的最后 $lines 行 ================")
    Get-Content -LiteralPath $LogFile -Tail $lines -Encoding UTF8 | ForEach-Object { Write-Line $_ }
    Write-Line '================================================'
    Write-Line ''
    Write-Line '如果内容太长，可以这样：'
    Write-Line "    用记事本打开 $LogFile"
    Write-Line "    旁边的 server.log.1、server.log.2 是更早的（每个最多 5MB，一共留 5 个）"
    # 顺手把整个目录打开，省得操作员去一层层翻。
    Start-Process -FilePath 'explorer.exe' -ArgumentList ('/select,"' + $LogFile + '"')
}

function Find-Mysqldump {
    <#
      找 mysqldump.exe。**不能假定它在 PATH 上**：MySQL Installer 默认不勾
      「加入 PATH」，而这是最常见的那种安装。所以按可靠程度依次试：

      1. 从 MySQL 服务的可执行文件路径推（最准：它就是这个实例真正在用的那个 bin）
      2. 常见的安装目录（版本号会变，所以用通配，别写死 8.0）
      3. PATH（有人手动加过）
    #>
    $service = Get-CimInstance Win32_Service -Filter "Name LIKE 'MySQL%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.PathName } | Select-Object -First 1
    if ($service) {
        $exe = ($service.PathName -replace '^"([^"]+)".*$', '$1') -replace '^([^\s]+)\s.*$', '$1'
        $candidate = Join-Path (Split-Path -Parent $exe) 'mysqldump.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }

    foreach ($pattern in @(
        'C:\Program Files\MySQL\MySQL Server*\bin\mysqldump.exe',
        'C:\Program Files (x86)\MySQL\MySQL Server*\bin\mysqldump.exe'
    )) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }

    $onPath = Get-Command 'mysqldump.exe' -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    return $null
}

function Backup-Database {
    $dump = Find-Mysqldump
    if (-not $dump) {
        Stop-WithError @'
没有找到 mysqldump.exe。

    它是随 MySQL Server 一起装的，只是默认不在 PATH 上。请在这台机器上找一下
    （一般在 C:\Program Files\MySQL\MySQL Server 8.0\bin\ 里），找到之后
    把那个目录临时加到 PATH 再运行这个按钮；或者直接用那个目录下的
    mysqldump.exe 手工导出。

    顺带一提：**MySQL Shell（mysqlsh）自带的那个 mysqldump 是另一个东西**，
    这里要的是 MySQL Server 的那个。
'@
    }

    if (-not (Test-Path -LiteralPath $EnvFile)) { Stop-WithError "找不到 $EnvFile。" }

    if (-not $BackupDir) { $BackupDir = Join-Path $InstallDir 'backups' }
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $target = Join-Path $BackupDir ("xinliceping-$stamp.sql")

    # 口令走 `--defaults-extra-file`（写在 runtime\ 下、权限收窄、用完就删），
    # 绝不写命令行：见文件开头第 2 条。
    $clientFile = Join-Path $InstallDir 'runtime\mysql-client.cnf'
    Write-Line '正在读出数据库连接信息……'
    if ((Invoke-Python @('-m', 'app.db.mysql_url', '--client-file', $clientFile)) -ne 0) {
        # 两种原因要分开说，否则这句会把一次权限问题指成「文件坏了」，而操作员会去
        # 改一个本来好好的文件。局域网用法下 `backend\.env` 只授了 SYSTEM 与
        # Administrators（`install.ps1` 的 `Tighten-EnvPermissions`），**普通用户读不到
        # 它**——而备份这个动作本身不在 `$needsAdmin` 名单里。于是那台机器上
        # 普通用户点这个按钮一定是这条错。
        Stop-WithError @'
没能读出数据库连接信息。上面那条输出里写着原因，两种都常见：

    1. 权限：局域网用法下 backend\.env 只允许 SYSTEM 与 Administrators 读。
       关掉这个窗口，右键「备份数据.bat」→「以管理员身份运行」再试一次。
    2. 文件：backend\.env 真的被改坏了（少了一个等号、多了个引号之类）。
'@
    }
    try {
        # `--defaults-extra-file` 必须是**第一个**参数，mysqldump 在解析其它参数之前就要读它。
        # `--single-transaction` 让导出期间不锁表（InnoDB），老师照常能用系统。
        # `--routines --events` 一起带上，免得将来加了存储过程才发现备份是不全的。
        # 这里不能用上面的 `Invoke-Python`：跑的是 mysqldump 而不是 python.exe。
        # 所以逐个参数自己 `Quote-Argument`（`Start-Process` 不做转义）。
        $arguments = @(
            (Quote-Argument "--defaults-extra-file=$clientFile"),
            '--single-transaction', '--routines', '--events',
            '--default-character-set=utf8mb4',
            (Quote-Argument "--result-file=$target")
        )
        Write-Line "正在导出到 $target ……"
        $process = Start-Process -FilePath $dump -ArgumentList $arguments -NoNewWindow -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "mysqldump 退出码 $($process.ExitCode)"
        }
    } finally {
        Remove-Item -LiteralPath $clientFile -Force -ErrorAction SilentlyContinue
    }

    $size = (Get-Item -LiteralPath $target).Length
    Write-Line ("备份完成：$target（{0:N1} MB）" -f ($size / 1MB)) 'OK'
    Write-Line ''
    Write-Line '提醒：这份文件就在这台机器上。真出事（硬盘坏了）的时候它帮不上忙——'
    Write-Line "请定期把它拷到别的地方（U 盘、另一台电脑、网盘）。目录是：$BackupDir"

    # 顺手清掉 30 天前的，免得这个目录悄悄涨到几十个 GB。
    $old = Get-ChildItem -LiteralPath $BackupDir -Filter 'xinliceping-*.sql' |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) }
    if ($old) {
        $old | Remove-Item -Force
        Write-Line ("顺手清掉了 $($old.Count) 个 30 天前的旧备份。")
    }
}

function Reset-AdminPassword {
    Write-Line ''
    Write-Line '改管理员（admin）的密码。'
    Write-Line '改动之后，用新密码登录时系统会再要求你改一次——这是正常的。'
    Write-Line ''

    $account = 'admin'
    while ($true) {
        $secure = Read-Host -Prompt "  给 $account 设一个新密码（至少 6 位，输入时不显示）" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

        if ($password.Length -lt 6) { Write-Line '  太短了，至少 6 位。' 'WARN'; continue }

        $secureAgain = Read-Host -Prompt '  再输一遍' -AsSecureString
        $bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureAgain)
        try { $again = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr2) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2) }

        if ($password -ne $again) { Write-Line '  两次输入不一致，请重新输入。' 'WARN'; continue }
        break
    }

    # 与安装脚本同一条路：口令走环境变量。Windows 上任何账号都能读别的进程的**命令行**，
    # 读不到它的**环境块**。
    $env:XLPSETUP_ADMIN_PASSWORD = $password
    try {
        if ((Invoke-Python @('-m', 'app.db.set_admin_password', '--password-env', 'XLPSETUP_ADMIN_PASSWORD')) -ne 0) {
            Stop-WithError '改密码失败。上面那条输出里写着原因（多半是连不上数据库）。'
        }
    } finally {
        Remove-Item Env:\XLPSETUP_ADMIN_PASSWORD -ErrorAction SilentlyContinue
    }
    Write-Line '密码已更新。' 'OK'
}

function Uninstall-Platform {
    Write-Line ''
    Write-Line '这会做四件事：'
    Write-Line '  1. 停止服务'
    if ($Usage -eq 'lan') {
        Write-Line "  2. 删掉开机自启的计划任务 $TaskName"
        Write-Line '  3. 删掉防火墙里那条放行规则'
    } else {
        # 说清「没有」，否则下面两步会被读成「该删的都删了」，而实际是「本来就没有」。
        Write-Line '  2. 没有计划任务要删（单机用法不注册开机自启）'
        Write-Line '  3. 没有防火墙规则要删（单机用法没放行过任何端口）'
    }
    Write-Line '  4. 问你要不要连程序文件一起删'
    Write-Line ''
    Write-Line '**数据库里的数据（学生名册、测评记录）不在这里删。**' 'WARN'
    Write-Line '它们在你当初填的那个 MySQL 实例里，与这个安装目录无关。'
    Write-Line '要清数据请先「备份数据.bat」，再用 MySQL 的工具处理。' 'WARN'
    Write-Line ''
    $answer = Read-Host '确定要继续吗？输入 yes 继续，其它任何键退出'
    if ($answer -ne 'yes') { Write-Line '什么都没做。'; return }

    if (Get-TaskState) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Line '计划任务已删除。' 'OK'
    }

    # **单机用法下这一步不能省。** 上面那个 `if (Get-TaskState)` 在没有计划任务时整段
    # 跳过，于是服务还在跑——而它锁着 `runtime\venv\Scripts\*.dll` 与 site-packages
    # 里的 `.pyd`（`Get-ServiceProcess` 收的是整棵树，所以外壳与真解释器一起停），
    # 下面那句 `Remove-Item -Recurse -Force` 会删到一半失败，留下一个「卸了一半、
    # 服务还在跑」的目录。报出来的是一句模糊的「没能完全删掉」，与真正的原因差很远。
    $alive = @(Get-ServiceProcess)
    if ($alive.Count -gt 0) {
        Write-Line '先停掉正在运行的服务……'
        $alive | Stop-Process -Force
        Start-Sleep -Seconds 1
        Write-Line '服务已停止。' 'OK'
    }

    $rule = Get-NetFirewallRule -DisplayName $FirewallRuleName -ErrorAction SilentlyContinue
    if ($rule) {
        Remove-NetFirewallRule -DisplayName $FirewallRuleName
        Write-Line '防火墙规则已删除。' 'OK'
    }

    Write-Line ''
    $purge = Read-Host "要不要把程序文件也删掉？目录是 $InstallDir。删了数据还在数据库里。输入 yes 删除，其它任何键保留"
    if ($purge -eq 'yes') {
        # 自己删自己所在的目录有时会失败（有句柄开着），所以退到上一级再删。
        # 失败不致命——手工删掉那个文件夹即可。
        Set-Location (Split-Path -Parent $InstallDir)
        try {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force
            Write-Line "已删除 $InstallDir" 'OK'
        } catch {
            Write-Line "没能完全删掉 $InstallDir：$($_.Exception.Message)" 'WARN'
            Write-Line '    把那个文件夹手工删掉即可（可能要先关掉还开着它的窗口）。' 'WARN'
        }
    } else {
        if ($Usage -eq 'lan') {
            Write-Line "程序文件留在 $InstallDir（服务已经停了，也不会再开机自启）。"
        } else {
            Write-Line "程序文件留在 $InstallDir（服务已经停了）。"
        }
    }
}

# ---------------------------------------------------------------- 分发

switch ($Action) {
    'start' { Start-Service }
    'stop' { Stop-Service }
    'restart' { Stop-Service; Start-Service }
    'status' { Show-Status }
    'log' { Show-Log }
    'backup' { Backup-Database }
    'reset-admin' { Reset-AdminPassword }
    'uninstall' { Uninstall-Platform }
}
