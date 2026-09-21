<#
  手工启动 —— 一键安装没跑完时的退路。由同目录的两个 .bat 调起来：

      手工启动后端.bat   →  -Action backend
      手工启动前端.bat   →  -Action frontend

  **后端那一个做的是一键安装第 2～4 步之后的那几件事**，顺序与 `install.ps1` 一致
  （那份是权威，这里只是把它拆薄）：

      运行环境 → 配置 → 校对表结构 → 迁移 → （库里是空的才）灌基础数据 → 起服务

  与 `install.ps1` 的差别只有两处，都是有意的：

  1. **清库那一步要人回答**（`app.db.reset_to_baseline` 会把库清成「只有管理员 + 量表与
     基本配置」）。一键安装里它是一句无条件执行（除非带 `-KeepData`），因为那条路上
     整件事都在安装器手里；这里不假设，问一次——**直接回车 = 不清**。
  2. **不设管理员密码**。`seed` 写出来的 admin 初始密码是 `123456`，首次登录会要求改；
     要立刻换成自己的，用安装目录里那个「重置管理员密码.bat」（它同时清掉锁定计数，
     是「忘了密码」唯一的出路，见 `set_admin_password.py` 的 docstring）。

  四条约定，每条都对应一次真实的故障，别顺手改：

  · **Python 的输出直通控制台，中间不许有人解码。** venv 里那个 `sitecustomize.py` 把
    Python 的输出固定成 UTF-8，而 `& python …` 那种写法会把子进程的 stdout **接进管道**、
    再用 `[Console]::OutputEncoding`（中文 Windows 上是 cp936）读回来——两边不一致时中文
    变乱码，而且**不可逆**（屏幕上已经是替换字符，原始字节当场就丢了）。所以这里一律走
    `Start-Process -NoNewWindow`（子进程**继承这个控制台**，Python 走它自己的 Unicode
    控制台 API，全路无解码）。推导写在 `Invoke-Native` 上，别换回 `&`。
    `install.ps1` 之所以能重定向，是因为它按 UTF-8 读回来；这里没有必须落盘的日志要留，
    所以最省事的正确做法是根本不经过我们。
  · **读可能不存在的键要问 `ContainsKey`。** `Set-StrictMode -Version Latest` 在**读取
    那一刻**就抛 `PropertyNotFoundException`，转型、判空、字符串拼接全都排在它后面
    （安装器的升级路径上栽过一次，见 CLAUDE.md §18）。环境变量同理，一律走
    `[Environment]::GetEnvironmentVariable`。
  · **口令不进命令行。** Windows 上任何账号都能从 WMI 读别的进程的命令行。这个脚本
    不把任何口令交给别的进程，`.env` 里那一份也不回显（只报「有值」）。
  · **不写我们没有验证过的话。** 「服务起来了」这句只有探过一个**会读数据库**的端点才
    说得出（`/api/v1/public/branding`，与安装器的健康检查同一个判据）。
#>
param(
    [ValidateSet('backend', 'frontend')]
    [string]$Action = 'backend',

    # 自动找不对时用它指路（这个脚本按自己的位置找安装目录，见 Resolve-Root）。
    [string]$InstallDir = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
    Write-Line '上面那句就是原因。改完再跑一次这个 .bat；装的时候那份日志（%TEMP%\xlp-install-*.log）也一起留好。'
    exit 1
}

# ---------------------------------------------------------------- 定位

function Resolve-Root {
    <#
      找出安装根——「里面有 backend\app\main.py 的那一层」。

      这个脚本在包里的位置是 `<root>\deploy\manual-start.ps1`，所以第一个候选就是它的
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
      `install.ps1` 的 `Get-EnvFileValues` 同一个形状（那边是权威实现）。

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

# ---------------------------------------------------------------- 配置

function Get-InstallPath {
    <#
      与 `install.ps1` 的同名函数逐字同一条约定：**路径一律用正斜杠**。
      python-dotenv 对带反斜杠的值会做转义处理，而 Windows 的 API 接受正斜杠——
      少一类「某些路径组合才炸」的问题。写 `.env` 的地方都走它，别自己拼。
    #>
    param([string]$InstallDir, [string]$Relative)

    return ($InstallDir.TrimEnd('\') + '/' + $Relative) -replace '\\', '/'
}

function Assert-EnvFileIsUsable {
    <#
      `backend\.env` 必须在、且那几项不能是空的。回一张值表给调用方。

      **「没有这一行」与「这一行是空的」是两件事**（CLAUDE.md §18 记着那次三轮排查）：
      前者 pydantic 用默认值，后者在 `import app.db.session` 那一刻抛 `int_parsing`
      ——一段英文 traceback，隔着三层调用栈，屏幕上看不出与配置有关。所以这里挡住那三个
      **会让进程起不来**的项，其余空的只提醒（`XLP_WEB_DIR=` 空着是合法的：那表示
      后端不托管前端）。
    #>
    param([string]$Root, [string]$BackendDir)

    $path = Join-Path $BackendDir '.env'
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Line ('没有 ' + $path + '。') 'WARN'
        Write-Line '先写一份骨架（只有口令那一行要你填），改完再跑一次这个 .bat：'
        Write-Host ''
        # JWT 密钥随手生成一串（`install.ps1` 的 `New-RandomHex` 是同一个意思）：留一个
        # `CHANGE_ME` 在那儿的话，机器上跑的就是一个谁都知道的签名密钥——它不会报错，
        # 只是让「登录凭据」这件事失去意义。
        $secret = -join (1..48 | ForEach-Object {
            $alphabet = '0123456789abcdef'
            $alphabet[(Get-Random -Maximum $alphabet.Length)]
        })
        $skeleton = @(
            '# 心晴 · 后端配置。这八项与一键安装写的是同一套（install.ps1 的 EnvFileKeys）。',
            '# 口令里带 @ : / # % ? & 的话要百分号编码（@ 写成 %40），否则解析出来的地址是错的。',
            'XLP_DATABASE_URL=mysql+pymysql://root:CHANGE_ME@127.0.0.1:3306/xinliceping',
            'XLP_HOST=127.0.0.1',
            'XLP_PORT=8000',
            ('XLP_WEB_DIR=' + (Get-InstallPath -InstallDir $Root -Relative 'frontend/dist')),
            ('XLP_LOG_DIR=' + (Get-InstallPath -InstallDir $Root -Relative 'runtime/logs')),
            'XLP_LOG_LEVEL=INFO',
            'XLP_DOCS_ENABLED=0',
            ('XLP_JWT_SECRET=' + $secret)
        )
        foreach ($line in $skeleton) { Write-Host ('    ' + $line) -ForegroundColor Gray }
        Write-Host ''
        Set-Content -LiteralPath $path -Encoding UTF8 -Value $skeleton
        Write-Line ('骨架已经写在 ' + $path + ' 里了：把 CHANGE_ME 换成数据库口令就行（JWT 密钥已经随机生成好了）。') 'OK'
        Stop-Here '配置还没有填完，先不往下走。'
    }

    $values = Get-EnvFileValues -Path $path

    $fatal = @('XLP_DATABASE_URL', 'XLP_PORT', 'XLP_DOCS_ENABLED')
    $empty = @()
    foreach ($key in $fatal) {
        if ($values.ContainsKey($key) -and -not (Get-SettingText -Values $values -Key $key)) {
            $empty += $key
        }
    }
    if ($empty.Count -gt 0) {
        Stop-Here ('这几项在 ' + $path + ' 里是空的：' + ($empty -join '、') +
            '。补上值，或者把那一行**整个删掉**（缺一行会用默认值，空一行会在 import 时抛 int_parsing）。')
    }

    # 其余空的 `XLP_*` 只提醒：它们多半是合法的（`XLP_WEB_DIR=` 就是「不托管前端」）。
    $lonely = @()
    foreach ($key in $values.Keys) {
        if (([string]$key).StartsWith('XLP_') -and -not (Get-SettingText -Values $values -Key $key)) { $lonely += $key }
    }
    if ($lonely.Count -gt 0) {
        Write-Line ('这几项是空的（如果是有意留空就先不管）：' + ($lonely -join '、')) 'WARN'
    }

    $url = Get-SettingText -Values $values -Key 'XLP_DATABASE_URL'
    # 只报形状，不报口令：这一段的输出会被人贴进聊天窗口。
    if ($url -match '^([^:]+)://([^:]*):[^@]*@([^:/]+):?([0-9]*)/?(.*)$') {
        Write-Line ('数据库配置：' + $Matches[2] + '@' + $Matches[3] + ':' + $Matches[4] + '/' + $Matches[5] +
            '（口令不打印，' + (Get-SettingText -Values $values -Key 'XLP_DATABASE_URL').Length + ' 个字符的一整行）') 'OK'
    } else {
        Write-Line 'XLP_DATABASE_URL 的形状看不懂（要 mysql+pymysql://用户:口令@主机:端口/库名）。' 'WARN'
    }
    return $values
}

# ---------------------------------------------------------------- 运行环境

function Get-VenvPython {
    param([string]$Root)
    return (Join-Path $Root 'runtime\venv\Scripts\python.exe')
}

function Test-BasePython {
    <#
      **真跑一次**去验，不回读版本资源：应用商店的别名、别的软件带进来的那一份，报出来
      的版本号都不作数（`install.ps1` 的 `Resolve-BasePython` 里有同一段推理）。
      四条判据：恰好 3.11、64 位、平台标签正好是 `win-amd64`、`venv` 与 `ensurepip`
      能 import。

      探针只输出 ASCII：此刻 `sitecustomize.py` 还不存在，中文会按 locale 写字节。

      ---------------------------------------------------------------------
      ★ 平台那一条用 `sysconfig.get_platform()`，**不是** `platform.machine()`
      ---------------------------------------------------------------------
      它取自 `sys.version` 里编译时烤进去的架构串（x64 版是 `[MSC v.1938 64 bit
      (AMD64)]`），不经任何环境变量；而且它就是 pip 拼平台标签时读的同一个字符串。
      而 `platform.machine()` 在 Windows 上返回的是 `PROCESSOR_ARCHITEW6432 or
      PROCESSOR_ARCHITECTURE`，**模拟层下优先报原生架构**——ARM Windows 上那个 x64
      解释器会被它报成 `ARM64`，于是这条路上唯一能用的那一个被自己排除掉。
      完整那一段写在 `install.ps1` 的 `Resolve-BasePython` docstring 里。
    #>
    param([string]$Exe)

    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    $probe = 'import struct,sys,sysconfig,venv,ensurepip;print("%d.%d %d %s" % (sys.version_info[0], sys.version_info[1], struct.calcsize("P") * 8, sysconfig.get_platform()))'
    try {
        $output = & $Exe -c $probe 2>$null
    } catch {
        return $false
    }
    if ($LASTEXITCODE -ne 0) { return $false }
    if (-not $output) { return $false }
    $line = ([string]($output | Select-Object -First 1)).Trim()
    if ($line -eq '3.11 64 win-amd64') { return $true }
    # ARM64 那一份：版本、位数、venv 三样全对，只有平台标签不对。记一笔给调用方，
    # 让最后那句「没找到 Python 3.11」能说清是为什么（它这一支**不中断**，继续找下一个）。
    if ($line -match '^3\.11 64 win-arm') { $script:SawArm64Python = $true }
    return $false
}

function Resolve-BasePython {
    <#
      挑一个能用的基础解释器，挑不到回 `''`。

      **机器级排在用户级前面**：venv 的 `pyvenv.cfg` 里写的是**绝对路径**，而局域网那条路
      的服务是以 SYSTEM 身份跑的——挂在某个人的目录下的解释器被更新/卸载之后，服务就再也
      起不来（CLAUDE.md §18「运行环境是安装时现建的 venv」那一节）。

      ---------------------------------------------------------------------
      ARM64 那一份也列进来，与 32 位那一份同一个理由
      ---------------------------------------------------------------------
      列进来才会被跑到、才会拿到「这是 ARM64 版的 Python」那句诊断。漏掉它的话，一台只装了
      ARM64 版 Python 的机器会得到「这台电脑上没找到 64 位的 Python 3.11」——而那个人明明
      装了，于是他唯一能做的推理是「再装一遍」，装回来还是同一个 ARM64 版。

      python.org 的 ARM64 安装器把解释器放在 `Python311-arm64\`（x64 那份是 `Python311\`），
      所以两处都要列。它排在 x64 之后，而 `Test-BasePython` 对它**不中断**、只记一笔
      就继续找下一个——所以一台同时装了两种的机器照样会挑中 x64 那份。
    #>
    param()

    $candidates = New-Object System.Collections.Generic.List[string]

    # 有没有撞上过 ARM64 那一档。只影响调用方最后那段文案里多不多一段指路的话。
    # **必须先赋初值**：本文件开着 `Set-StrictMode -Version Latest`，读一个没赋值过的
    # 变量会当场抛异常（`Test-BasePython` 是往里写的那个）。
    $script:SawArm64Python = $false

    # `py` 是个启动器，不是解释器：让它自己挑一个 3.11，把它报出来的 `sys.executable`
    # 当候选。挑不到时它会自己报错（退出码非 0），这里就当没有这个候选。
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $resolved = & $launcher.Source -3.11 -c 'import sys;print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                $candidates.Add(([string]($resolved | Select-Object -First 1)).Trim())
            }
        } catch {
            Write-Line '`py -3.11` 没有挑出解释器，继续找别的位置。' 'WARN'
        }
    }

    $programFiles = [Environment]::GetEnvironmentVariable('ProgramFiles')
    $localAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')
    if ($programFiles) {
        $candidates.Add((Join-Path $programFiles 'Python311\python.exe'))
        # **ARM64 那一份也列进来**（见 docstring）。
        $candidates.Add((Join-Path $programFiles 'Python311-arm64\python.exe'))
    }
    if ($localAppData) {
        $candidates.Add((Join-Path $localAppData 'Programs\Python\Python311\python.exe'))
        $candidates.Add((Join-Path $localAppData 'Programs\Python\Python311-arm64\python.exe'))
    }
    $candidates.Add('C:\Python311\python.exe')

    foreach ($candidate in $candidates) {
        if (Test-BasePython -Exe $candidate) { return $candidate }
    }
    return ''
}

function Initialize-Runtime {
    <#
      让 `runtime\venv` 可用：没有就地建一个。只用包里那些 wheel，**不联网**。

      次序不能换（`venv --clear` 会清空 `Lib\site-packages`，编码钩子排在前面会被下一次
      重建静默抹掉）：venv → pip install → sitecustomize。
    #>
    param([string]$Root)

    $python = Get-VenvPython -Root $Root
    if (Test-Path -LiteralPath $python) {
        Write-Line ('运行环境已经在：' + $python) 'OK'
        return
    }

    $venvDir = Join-Path $Root 'runtime\venv'
    if ((Test-Path -LiteralPath $venvDir) -and
        -not (Test-Path -LiteralPath (Join-Path $venvDir 'pyvenv.cfg'))) {
        Stop-Here ($venvDir + ' 已经存在，但它不是一个虚拟环境（里面没有 pyvenv.cfg）。' +
            '确认那个目录里没有别的东西，删掉它，再跑一次。')
    }

    Write-Line '没有运行环境，现在建一个（这一步不联网）。' 'STEP'
    $base = Resolve-BasePython
    if (-not $base) {
        # **按撞上过哪一档分岔**：ARM64 那一段只对装了 ARM64 版 Python 的机器说。32 位那一档
        # 读到的就是上面这句（对它而言「去装 64 位版」正是该做的事，不必再多说一句）。
        # 这一段与 `install.ps1` 的收尾文案同源，两处要一起改。
        $message = '这台电脑上没找到 64 位的 Python 3.11。包里那些 wheel 有几个的文件名是 cp311-win_amd64，' +
            '解释器换不了——去 python.org 装一个 3.11（勾上 Add python.exe to PATH），再跑一次。'
        if ($script:SawArm64Python) {
            $message += [Environment]::NewLine +
                '上面那个位置上是 ARM64 版的 Python，这次的失败与它有关：包里那些二进制 wheel 没有一份是给' +
                'ARM64 的（上游就没发），所以要装的**不是**「ARM64 版」，而是上面说的 Windows installer (64-bit)。' +
                '这台电脑如果是 ARM 的，那一条照样装得上：Windows 自带 x64 模拟。两个版本可以并存，装完重跑一次。'
        }
        Stop-Here $message
    }
    Write-Line ('用这个解释器：' + $base)

    $code = Invoke-Native -FilePath $base -Arguments @('-m', 'venv', '--clear', $venvDir)
    if ($code -ne 0) { Stop-Here ('建虚拟环境失败（退出码 ' + $code + '）。上面是它自己说的话。') }
    if (-not (Test-Path -LiteralPath $python)) {
        Stop-Here ('虚拟环境建出来了，但里面没有解释器（' + $python + '）。这台电脑上那个 Python 多半被裁剪过' +
            '（企业镜像，或者别的软件带进来的 embeddable 版）。换 python.org 上装的那一个再来。')
    }

    $lock = Join-Path $Root 'deploy\requirements.lock.txt'
    if (-not (Test-Path -LiteralPath $lock)) {
        Stop-Here ('没有 ' + $lock + '。这个脚本要在**解压开的包**里跑（或者在装完之后跑到安装目录里），' +
            '它需要包里的 wheels\ 与 deploy\requirements.lock.txt。')
    }

    Write-Line '装依赖（只用包里那些 wheel）。' 'STEP'
    # 开关各有理由，与 install.ps1 那一处逐字相同：`--isolated` 忽略这台机器的 pip.ini
    # （配置里的 find-links 会**叠加**上来，最坏是按默认 5 次重试卡住），`--no-index`
    # 不碰网络，`--only-binary=:all:` 挡住「拿 sdist 去编译」那条要编译器的路。
    # `--no-deps` 与下载时一致，`--retries 0` 让「装不上」当场结束而不是卡着。
    $code = Invoke-Native -FilePath $python -Arguments @(
        '-m', 'pip', 'install',
        '--isolated', '--no-index', '--find-links', (Join-Path $Root 'wheels'),
        '--no-deps', '--only-binary=:all:', '--no-input',
        '--disable-pip-version-check', '--no-cache-dir', '--retries', '0', '--timeout', '5',
        '-r', $lock
    )
    if ($code -ne 0) { Stop-Here ('装依赖失败（退出码 ' + $code + '，上面是 pip 自己说的话）。') }

    $hook = Join-Path $Root 'deploy\sitecustomize.py'
    if (Test-Path -LiteralPath $hook) {
        Copy-Item -LiteralPath $hook -Destination (Join-Path $venvDir 'Lib\site-packages\sitecustomize.py') -Force
    }
    Write-Line ('运行环境就绪：' + $python) 'OK'
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

      `install.ps1` 的 `Invoke-Native` 是同一条路（它那边还要一张成功码表，因为
      robocopy 的 1 是成功）。
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

# ---------------------------------------------------------------- 后端

function Start-Backend {
    param([string]$Root, [hashtable]$Values)

    Initialize-Runtime -Root $Root
    $Values = Assert-EnvFileIsUsable -Root $Root -BackendDir $script:BackendDir
    $port = Get-ListeningPort -Values $Values

    # 表结构归**这一版程序**，所以校对与迁移不分「谁准备的库」都要跑（CLAUDE.md §18）：
    # 手工建的库没有 `alembic_version` 那一行，直接迁移会撞 `Duplicate column name`；
    # 而换了程序文件却没迁移的下场是「屏幕说装成功、登录页打得开、一操作就 500」。
    Invoke-PythonStep '校对表结构（手工建的表要在这里盖章）' @('-m', 'app.db.ensure_schema') | Out-Null
    Invoke-PythonStep '执行数据库迁移' @('-m', 'alembic', 'upgrade', 'head') | Out-Null

    $emptyCode = Invoke-PythonStep '看这个库里有没有东西' @('-m', 'app.db.check_empty') -AllowFailure
    if ($emptyCode -eq 0) {
        Invoke-PythonStep '写入基础数据（量表 100 题、评分规则、学校、admin）' @('-m', 'app.db.seed') | Out-Null

        Write-Line ('admin 的初始密码是 123456，首次登录会要求改。要现在换成自己的：双击安装目录里的' +
            '「重置管理员密码.bat」（还没装完的话它在 deploy\ops\ 里）。') 'OK'

        # **这一步必须是人回答的**（见文件头第 1 条）。此刻库里只有刚才 seed 写进去的那点
        # 演示数据，所以清掉是安全的；而回车那一支刻意是「不清」——一个破坏性脚本的默认
        # 动作不能是破坏。
        $answer = Read-Host '要把这个库清成「只有管理员 admin + 量表与基本配置」吗？真实学校用请输入 Y 再回车（种子里的演示名册会被删掉）；直接回车 = 保留'
        if ($answer -and ([string]$answer).Trim().ToUpperInvariant().StartsWith('Y')) {
            Invoke-PythonStep '清空演示数据' @('-m', 'app.db.reset_to_baseline', '--yes') | Out-Null
            Write-Line '已清成「只有管理员 + 量表与基本配置」。' 'OK'
        } else {
            Write-Line '按你的意思保留了库里的内容（没有清）。'
        }
    } else {
        Write-Line '这个库里已经有东西了，所以这一步什么都没动。' 'WARN'
        Write-Line '  只是缺基础数据（量表 / admin）的话，在这里跑：'
        Write-Line ('    cd /d ' + $script:BackendDir)
        Write-Line ('    ' + $script:VenvPython + ' -m app.db.seed')
    }

    $address = Get-BackendAddress -Values $Values
    Write-Line ('页面与接口：http://' + $address + ':' + $port + '/')
    if ((Get-SettingText -Values $Values -Key 'XLP_HOST') -eq '0.0.0.0') {
        Write-Line ('  这台机器在监听所有网卡，所以局域网里的其它电脑把 ' + $address +
            ' 换成这台机器的 IP 就行（同一个端口）。')
    }
    if (Get-SettingText -Values $Values -Key 'XLP_WEB_DIR') {
        Write-Line '  后端这一份已经把页面一起发了（XLP_WEB_DIR 指到了 frontend\dist），'
        Write-Line '  所以只开这一个窗口就够；想前后端分开（5173 / 8000）再双击「手工启动前端.bat」。'
    } else {
        Write-Line '  这份配置没有托管前端（XLP_WEB_DIR 是空的），要开「手工启动前端.bat」才有页面。' 'WARN'
    }
    Write-Line '这个窗口就是服务本身。关掉窗口 = 停服务（Ctrl+C 有时只到外层那一个，关窗口最稳）。'

    # **这个窗口活多久，服务就活多久。** `-Wait` 在这里是有意的：它一直挡住，直到服务
    # 退出（关窗口，或者它自己收到停的信号），所以后面那行 `exit` 与 .bat 里的 `pause`
    # 都排不上——而这个窗口正是 `run_server.py` 的控制台。
    $code = Invoke-Native -FilePath $script:VenvPython -Arguments @('run_server.py') `
        -WorkingDirectory $script:BackendDir
    exit $code
}

# ---------------------------------------------------------------- 前端

function Start-Frontend {
    <#
      **为什么前端要单独一个服务器。** `frontend/dist` 是一份构建产物，而 `api.ts` 的
      `API_BASE = '/api/v1'` 是相对路径——「页面在哪个源上，接口就得在哪个源上」。所以
      用 `python -m http.server` 起前端是最坏的一种：页面打得开，每一次登录都死在
      `Unexpected token '<'`（静态服务器把 `/api/...` 回成了 404 HTML）。这个窗口跑的是
      `deploy\serve_frontend.py`，它把静态文件发出去、把 `/api/**` 转给后端（CLAUDE.md §18）。
    #>
    param([string]$Root, [hashtable]$Values)

    $python = Get-VenvPython -Root $Root
    if (-not (Test-Path -LiteralPath $python)) {
        Stop-Here ('还没有运行环境（' + $python + '）。先双击「手工启动后端.bat」跑一次，它会建出来——' +
            '这个窗口只是把页面发出去，它自己也要用那个 Python。')
    }

    $dist = Join-Path $Root 'frontend\dist'
    if (-not (Test-Path -LiteralPath (Join-Path $dist 'index.html'))) {
        Stop-Here ('没有 ' + (Join-Path $dist 'index.html') + '——包里那一份前端构建产物不在这儿。' +
            '只用后端那一个窗口也能用：前提是 backend\.env 里的 XLP_WEB_DIR 指着 frontend\dist。')
    }

    $port = Get-ListeningPort -Values $Values
    $api = 'http://' + (Get-BackendAddress -Values $Values) + ':' + $port

    # 后端没起来时**照样要把页面发出去**（用户看到的应该是一个能打开的登录页 + 一句
    # 「连不上后端服务」），但这句话现在就说，省得他去浏览器里猜。探的是那个**会读数据库**
    # 的端点：它通了 = 进程 → SQLAlchemy → MySQL 整条路都通了。
    $reachable = $false
    try {
        $probe = Invoke-WebRequest -Uri ($api + '/api/v1/public/branding') -UseBasicParsing -TimeoutSec 3
        $reachable = ($probe.StatusCode -eq 200)
    } catch {
        $reachable = $false
    }
    if ($reachable) {
        Write-Line ('后端在：' + $api + '（那一个端点会读数据库，所以它通了就是库也通了）') 'OK'
    } else {
        Write-Line ('现在连不上后端 ' + $api + '。页面照样打得开，但登录会提示「连不上后端服务」——' +
            '先把「手工启动后端.bat」那个窗口起起来。') 'WARN'
    }

    Write-Line '页面：http://127.0.0.1:5173/'
    Write-Line ('接口：' + $api + '（页面上的 /api/** 全部转给它）')
    Write-Line '这个窗口就是前端服务本身，关掉它就是停它。'

    $code = Invoke-Native -FilePath $python -WorkingDirectory $Root -Arguments @(
        (Join-Path $Root 'deploy\serve_frontend.py'),
        '--root', $dist,
        '--host', '127.0.0.1',
        '--port', '5173',
        '--api', $api
    )
    exit $code
}

# ---------------------------------------------------------------- 主流程

$root = Resolve-Root -Explicit $InstallDir
$script:BackendDir = Join-Path $root 'backend'
$script:VenvPython = Get-VenvPython -Root $root

Write-Line ('安装目录：' + $root) 'OK'

if ($Action -eq 'frontend') {
    # 前端只借一个端口号，所以配置读得宽松：缺 `.env` 就按默认端口试，不在这里拦。
    $values = @{}
    $envPath = Join-Path $script:BackendDir '.env'
    if (Test-Path -LiteralPath $envPath) {
        $values = Get-EnvFileValues -Path $envPath
    } else {
        Write-Line ('没有 ' + $envPath + '，按默认端口 8000 找后端。') 'WARN'
    }
    Start-Frontend -Root $root -Values $values
} else {
    Start-Backend -Root $root -Values @{}
}
