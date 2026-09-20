<#
  数据库增量升级 —— 只动数据库，不起服务、不碰程序文件。
  由同目录的「数据库增量升级.bat」调起来（那个 .bat 是纯 ASCII，中文都在这里）。

  **它补的是哪一个场景。** 「手工启动后端.bat」也会把这两步跑掉，但它跑完就**占住
  那个窗口去起服务了**。所以要单独跑数据库的场合有两种：

      1. 想先把库升上去，过一会儿再开服务；
      2. 后端起不来，想单独确认「库到底升上去了没有」。

  做的那两件事与 `install.ps1` 第 4 步逐字相同（那份是权威，这里只是把它拆薄）：

      python -m app.db.ensure_schema      ← 校对表结构，手工建的表在这里盖章
      python -m alembic upgrade head      ← 执行迁移

  **次序不能换。** 手写的建表语句里没有 `alembic_version`（那是 Alembic 自己的账本，
  只有它会建），直接迁移会在第一条 ALTER 上撞 Duplicate column name；反过来，换了
  程序文件却没迁移的下场是「登录页打得开、一操作就 500」。两条都记在 CLAUDE.md §18
  「数据库由谁准备」那一节。

  **跑完再校对一遍**，不是走过场：拿迁移之后的表与这一版程序的模型再比一次，并由它
  念出最终的版本戳。屏幕上那一行是**它自己说的**，不是这个脚本拼的——拼出来的那句
  没有任何东西保证它是真的。

  三条约定，每条都对应一次真实的故障，别顺手改：

  · **Python 的输出直通控制台，中间不许有人解码。** venv 里那个 `sitecustomize.py`
    把 Python 的输出固定成 UTF-8，而 `& python …` 会把子进程的 stdout **接进管道**、
    再用 `[Console]::OutputEncoding`（中文 Windows 上是 cp936）读回来——两边不一致时
    中文变乱码，而且**不可逆**（屏幕上已经是替换字符，原始字节当场就丢了）。所以真正的
    调用一律走 `Start-Process -NoNewWindow`（子进程**继承这个控制台**，Python 走它自己
    的 Unicode 控制台 API，全路无解码）。推导写在 `Invoke-Native` 上。**唯一的例外是
    那个版本号探针**：它只输出 ASCII（`1.1.2 V1.1`），没有可解码的字节。
  · **读可能不存在的键要问 `ContainsKey`。** `Set-StrictMode -Version Latest` 在**读取
    那一刻**就抛 `PropertyNotFoundException`，转型、判空、字符串拼接全排在它后面
    （安装器的升级路径上栽过一次，见 CLAUDE.md §18）。
  · **口令不进命令行、不进屏幕。** Windows 上任何账号都能从 WMI 读别的进程的命令行。
    这个脚本只把 `.env` 的**位置**交给 Python（那些 `-m` 命令自己会去读），口令一个
    字节都不经过这里。
#>
param(
    # 自动找不对时用它指路（这个脚本按自己的位置找安装目录，见 Resolve-Root）。
    [string]$InstallDir = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# 卡住时那句「还有一条路」的话。在 Resolve-Root 之后才会填上真的路径，先声明成空串
# ——严格模式下读一个从没赋过值的变量会抛异常。
$script:SqlHint = ''

# ---------------------------------------------------------------- 输出

function Write-Line {
    param(
        [string]$Message,
        [ValidateSet('INFO', 'STEP', 'OK', 'WARN', 'ERROR')]
        [string]$Level = 'INFO'
    )
    $color = 'Gray'
    if ($Level -eq 'STEP') { $color = 'Cyan' }
    if ($Level -eq 'OK') { $color = 'Green' }
    if ($Level -eq 'WARN') { $color = 'Yellow' }
    if ($Level -eq 'ERROR') { $color = 'Red' }
    Write-Host ('[' + $Level + '] ' + $Message) -ForegroundColor $color
}

function Stop-Here {
    <#
      退出前把那句「怎么办」一起说出来。手工跑的人面前没有第二份文档，屏幕上这几行
      就是全部——所以每条错误信息都要能照着做，而不是只说哪里不对。
    #>
    param([string]$Message)
    Write-Line $Message 'ERROR'
    Write-Line '上面那句就是原因。改完再双击一次「数据库增量升级.bat」；如果改不动，把上面这几行原样留好。'
    if ($script:SqlHint) { Write-Line $script:SqlHint }
    exit 1
}

# ---------------------------------------------------------------- 定位

function Resolve-Root {
    <#
      找出安装根——「里面有 backend\app\main.py 的那一层」。

      这个脚本在包里的位置是 `<root>\deploy\manual-migrate.ps1`，所以第一个候选就是它的
      上一级。剩下两个是安装器用的默认目录（单机在 `%LOCALAPPDATA%`，局域网在
      `C:\xinliceping`，见 CLAUDE.md §18「用法」那一节）：从解压开的包里跑、或者从
      别处跑时，那两个才是对的。

      **找不到就说清楚它试过哪几个**：这个脚本最常见的用法是「装在别的地方，我从
      D 盘那份包里点它」，那时一句话「没找到安装目录」帮不上任何忙。
    #>
    param([string]$Explicit)

    if ($Explicit) {
        if (Test-Path -LiteralPath (Join-Path $Explicit 'backend\app\main.py')) {
            return (Resolve-Path -LiteralPath $Explicit).Path
        }
        Stop-Here ('-InstallDir 指向的目录里没有 backend\app\main.py：' + $Explicit)
    }

    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add((Split-Path -Parent $PSScriptRoot))
    $localAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if ($localAppData) { $candidates.Add((Join-Path $localAppData 'xinliceping')) }
    $candidates.Add('C:\xinliceping')

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'backend\app\main.py')) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    Stop-Here ('没找到安装目录（找的是「里面有 backend\app\main.py 的那一层」，试过：' +
        ($candidates -join '、') + '）。用 -InstallDir "D:\xinliceping" 指定它。')
}

function Get-EnvFileValues {
    <#
      把 `backend\.env` 读成一张表。行内注释与空行跳过，值两边的空白去掉——与
      `install.ps1` / `manual-start.ps1` 的同名函数同一个形状（那边是权威实现）。

      `-Encoding UTF8`：那份文件是安装器写的（PS 5.1 的 `Set-Content -Encoding UTF8`
      带 BOM），而 python-dotenv 自己会剥掉 BOM，所以带与不带解出来一样。
    #>
    param([string]$Path)

    $values = @{}
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $trimmed = ([string]$line).Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $index = $trimmed.IndexOf('=')
        $values[$trimmed.Substring(0, $index).Trim()] = $trimmed.Substring($index + 1).Trim()
    }
    return $values
}

function Get-SettingText {
    param([hashtable]$Values, [string]$Key)
    if ($Values.ContainsKey($Key)) { return [string]$Values[$Key] }
    return ''
}

function Get-ListeningPort {
    <#
      后端在哪个端口上。取不到就回 8000——那是 `core/config.py` 里 `port` 的默认值，
      也是安装器让操作员直接回车时接受的那一个。
    #>
    param([hashtable]$Values)

    $parsed = 0
    if ([int]::TryParse((Get-SettingText -Values $Values -Key 'XLP_PORT'), [ref]$parsed) -and
        $parsed -ge 1 -and $parsed -le 65535) {
        return $parsed
    }
    return 8000
}

function Get-BackendAddress {
    <#
      从这台机器出发该用哪个地址找后端。`XLP_HOST=0.0.0.0` 是「监听所有网卡」，
      不是一个能连的地址——连它要么失败要么绕一圈，所以那种情况下用 `127.0.0.1`。

      （变量名叫 `$bind` 不叫 `$host`：`$Host` 是 PowerShell 自己的只读自动变量。）
    #>
    param([hashtable]$Values)

    $bind = Get-SettingText -Values $Values -Key 'XLP_HOST'
    if (-not $bind -or $bind -eq '0.0.0.0') { return '127.0.0.1' }
    return $bind
}

# ---------------------------------------------------------------- 运行环境

function Get-VenvPython {
    param([string]$Root)
    return (Join-Path $Root 'runtime\venv\Scripts\python.exe')
}

function Quote-Argument {
    <#
      `Start-Process -ArgumentList` 只是把数组用空格接成一条命令行、**不做转义**，所以带
      空格的路径要自己引起来（`install.ps1` 的 `Quote-Argument` 是同一件事）。

      单机用法下这不是理论问题：默认安装目录在 `%LOCALAPPDATA%` 下，
      `C:\Users\Zhang San\AppData\Local\...` 是再普通不过的形状。
    #>
    param([string]$Value)
    if ($Value -match '[\s"]') { return '"' + ($Value -replace '"', '\"') + '"' }
    return $Value
}

function Invoke-Native {
    <#
      **为什么不是 `& $exe …`。** PowerShell 会把原生进程的 stdout **接进管道**再用
      `[Console]::OutputEncoding`（中文 Windows 上是 cp936）解码——而 venv 里的
      `sitecustomize.py` 把 Python 的输出固定成了 **UTF-8**，两边不一致时中文变乱码，
      而且不可逆（屏幕上已经是替换字符，原始字节当场就丢了）。

      `Start-Process -NoNewWindow` 不给子进程建管道，它**继承这个控制台**：Python 于是
      走自己的 Unicode 控制台 API 写宽字符，整条路上没有第二个人在解码。附带的好处是
      退出码干净地落在 `$process.ExitCode` 上（不再混在输出流里），CWD 由
      `-WorkingDirectory` 给（不用 Push-Location）。
    #>
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = ''
    )

    $quoted = @()
    foreach ($argument in $Arguments) { $quoted += (Quote-Argument -Value $argument) }
    $startArgs = @{
        FilePath = $FilePath
        ArgumentList = ($quoted -join ' ')
        NoNewWindow = $true
        Wait = $true
        PassThru = $true
    }
    if ($WorkingDirectory) { $startArgs['WorkingDirectory'] = $WorkingDirectory }
    $process = Start-Process @startArgs
    return $process.ExitCode
}

function Invoke-PythonStep {
    <#
      跑一步 Python，输出**直通控制台**（见文件头第 1 条与 `Invoke-Native`），返回值是退出码。
    #>
    param(
        [string]$Title,
        [string[]]$Arguments = @(),
        [switch]$AllowFailure
    )

    Write-Line ('== ' + $Title) 'STEP'
    $code = Invoke-Native -FilePath $script:VenvPython -Arguments $Arguments `
        -WorkingDirectory $script:BackendDir
    if ($code -ne 0 -and -not $AllowFailure) {
        Stop-Here ($Title + ' 没有成功（退出码 ' + $code + '）。上面那几行就是它自己说的话。')
    }
    return $code
}

function Get-ProgramVersion {
    <#
      这一版程序是哪个版本（走 `app` 包自己读 `app/version.py`，全仓库唯一的出处）。

      **为什么值得单独报一行。** 升级的正确用法是「先把新包覆盖到安装目录，再点这个
      按钮」。覆盖漏了的话，跑的是**旧树**：`alembic upgrade head` 会说无事可做，而屏幕
      上看起来一切正常。把它念出来，操作员一眼就能看出跑的是不是自己以为的那一版——
      这个陷阱**没有别的机器判据**（旧树里的 `ensure_schema` 拿旧模型比旧库，也会说
      对得上），只能靠人看见。

      用 `&` 把输出读回来在这里是安全的：探针只输出 ASCII（`1.1.2 V1.1`），上面那条
      「Python 输出不许经过解码」的约定针对的是**中文**（见 `Invoke-Native`）。
      `-c` 的 CWD 就是 `sys.path[0]`，所以必须先把位置切到 backend——`.bat` 那边
      `cd` 到的是 `deploy\`，不是这里。
    #>
    $probe = 'import app.version as v;print(v.__version__, v.VERSION_LABEL)'
    # 这一条**只在这里**把 `$ErrorActionPreference` 放回 `Continue`：PS 5.1 在 `Stop` 下
    # 把「原生命令写 stderr + 重定向」当成终止错误，而这一句正是唯一一处 `2>$null`。
    # 放回去只是把那种情形降级成「读不到版本号」——下面那个 if 本来就处理它。
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $script:BackendDir
    try {
        $output = & $script:VenvPython -c $probe 2>$null
    } catch {
        return ''
    } finally {
        Pop-Location
        $ErrorActionPreference = $saved
    }
    if ($LASTEXITCODE -ne 0 -or -not $output) { return '' }
    return ([string]($output | Select-Object -First 1)).Trim()
}

function Test-BackendReachable {
    <#
      服务是不是还开着。判据是那个**会读数据库**的端点（`/api/v1/public/branding`）——
      与安装器的健康检查、与 `manual-start.ps1` 的探活是同一个判据：它通了 = 进程 →
      SQLAlchemy → MySQL 整条路都通了。
    #>
    param([string]$Address, [int]$Port)

    try {
        $probe = Invoke-WebRequest -Uri ('http://' + $Address + ':' + $Port + '/api/v1/public/branding') `
            -UseBasicParsing -TimeoutSec 3
        return ($probe.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------- 主流程

$root = Resolve-Root -Explicit $InstallDir
$script:BackendDir = Join-Path $root 'backend'
$script:VenvPython = Get-VenvPython -Root $root

Write-Line ('安装目录：' + $root) 'OK'

# 那份手工 SQL 与程序文件是同一批东西（它随 `backend\` 一起进包，`build_package.py` 的
# REQUIRED_PATHS 钉住它）。它不在 = 这个目录里多半还是旧版的程序文件。下面拿它当一条
# 线索用，不当判据。
$sqlPath = Join-Path $script:BackendDir 'sql\upgrade_from_v1_0_0.sql'
$script:SqlHint = '库升不动的时候还有一条路：那份手工 SQL 在 ' + $sqlPath +
    '（怎么执行写在它自己的文件头里，这里不重述——两处各说一遍，过一阵就会有一处是旧的）。'

if (-not (Test-Path -LiteralPath $script:VenvPython)) {
    Stop-Here ('这个目录里还没有运行环境（' + $script:VenvPython + '）。这个按钮要在**装过的**' +
        '安装目录里点：先把新版包覆盖过去，再从那里双击它——那样运行环境与 backend\.env 都是现成的。' +
        '如果你是在刚解压开的包里点的它，那还没有装过，先按《部署说明.txt》装一次。')
}

$version = Get-ProgramVersion
if ($version) {
    Write-Line ('这一版程序：' + $version) 'OK'
} else {
    Write-Line '读不出版本号（`import app.version` 没成功），下面照样继续。' 'WARN'
}

if (-not (Test-Path -LiteralPath $sqlPath)) {
    Write-Line ('没找到 ' + $sqlPath + '。它跟着新版包一起进来，所以它不在，多半是这个安装目录里的' +
        '程序文件**还是旧版**——那样下面那条迁移会「无事可做」，而屏幕上看着像成功。' +
        '确定程序文件已经换过了就忽略这一行。') 'WARN'
}

$envPath = Join-Path $script:BackendDir '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    Stop-Here ('没有 ' + $envPath + '。数据库在哪、哪个账号连它，都由这份文件说了算。' +
        '先跑一次「手工启动后端.bat」，它会写一份骨架出来（口令那一行要你填）。')
}

$values = Get-EnvFileValues -Path $envPath
$port = Get-ListeningPort -Values $values
$address = Get-BackendAddress -Values $values

# 服务开着时**不硬拦**（「先看看它能不能自己升」是一个正当的用法），但要说出来：
# 换了表结构之后老进程还按旧结构读写，而新加的那几列它一个都不知道；升级也不会让
# 它自己重启。单机用法下它就是屏幕上那个黑窗口（关掉窗口 = 停服务）。
if (Test-BackendReachable -Address $address -Port $port) {
    Write-Line ('服务现在开着（http://' + $address + ':' + $port + ' 有应答）。升级期间它不会自己重启，' +
        '建议先双击安装目录里的「停止服务.bat」把服务停下，装完再起——老进程认不出新加的那些列。') 'WARN'
}

Write-Line '下面跑的是与一键安装第 4 步逐字相同的两步。' 'STEP'
Invoke-PythonStep '校对表结构（手工建的表要在这里盖章）' @('-m', 'app.db.ensure_schema') | Out-Null
Invoke-PythonStep '执行数据库迁移' @('-m', 'alembic', 'upgrade', 'head') | Out-Null

# 第三步是**再校对一次**，不是重跑一遍走过场：它拿迁移之后的表与这一版程序的模型再比
# 一次，然后由它念出最终的版本戳——屏幕最后那一行是它自己说的。失败时这里自己收场，
# 不走 Stop-Here（那句「还有一条路是手工 SQL」在这个时点是错的建议：表已经建出来了）。
$after = Invoke-PythonStep '再校对一次（并念出最终的版本戳）' @('-m', 'app.db.ensure_schema') -AllowFailure
if ($after -ne 0) {
    Write-Line '迁移跑完了，但再校对时它说表结构与这一版程序对不上（上面那几行就是它说的）。' 'ERROR'
    Write-Line '多半是迁移曾经在中途停下过：MySQL 的 DDL 不在事务里，前面几条 ALTER 已经落地而版本号没有前进。' 'ERROR'
    Write-Line '**别反复重跑**——它会在第一条上继续撞。先把上面那几行、以及这份输出留好。' 'ERROR'
    exit 1
}

Write-Line '数据库升级完成。' 'OK'
Write-Line ('这一版程序是 ' + $version + '；库的版本戳看上面那一行 `alembic_version = …`。')
Write-Line '库和程序文件要一起换：只换了程序没升库、或者只升了库没换程序，都会出问题。'
Write-Line ('核对表结构双击安装目录里的「查看状态.bat」；如果你要手工执行那一份，它在 ' + $sqlPath + '。')
