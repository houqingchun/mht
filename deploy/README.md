# `deploy/` —— 出包、安装、运维

这个目录是**给维护者看的**。写给操作员（那所学校的老师）的是包根那份
`部署说明.txt`，两份文档面向两种人，改的时候别把它们混成一份。

目标机的条件决定了整套东西的形状：**Windows，已装 Python 3.11 与 MySQL 8.0 Server，
装不了 Docker，装的人不是技术人员**。所以产物是一个 zip：拷过去 → 解压 → 双击。

> 约定与理由写在 `CLAUDE.md` §18。这里只讲**怎么操作**。

> **2026-09-18：包里不再带内嵌 CPython。** 安装时用**目标机上那个** Python 3.11
> 建 venv，再从包里的 `wheels/` 离线装依赖；单机用法的服务也不再是隐藏进程，
> 而是**一个可见的控制台窗口**（关掉窗口 = 停服务）。两件事的完整理由在 `CLAUDE.md` §18。

---

## 为什么必须是 Python 3.11 x64，而且装完不能动它

`wheels/` 里的 31 个 wheel 有 **9 个**文件名带 `cp311-cp311-win_amd64`（其余是
`py3-none-any` 与两个 abi3 的）。那 9 个**不带 abi3**，所以解释器被钉死在
CPython 3.11 64 位——3.12 装不上，32 位也加载不了那些扩展模块。这不是「建议版本」。

`install.ps1` 的 `Resolve-BasePython` 因此**真跑一次**去验（候选挨个跑一个小脚本，
看退出码），而不是读 `(Get-Item $exe).VersionInfo`——那个报的是 exe 自己的版本资源，
对应用商店的别名、对别的软件带进来的那份都不作数。

### 「x64」那一半管的是 ARM：没有它，ARM 机器会走进死循环（2026-09-20）

**这个包在 ARM 的 Windows 上照样能用**，前提是装 python.org 的 **Windows installer
(64-bit)** ——Windows 自带 x64 模拟，性能损失通常一成以内。所以要挡的不是「ARM 机器」，
是**ARM64 那份解释器**。

python.org 的 ARM64 安装器把解释器放在 `Python311-arm64\`，注册在 PEP 514 的
`3.11-arm64` 标签下（x64 那份是 `Python311\` / `3.11`）。这两处都列进候选表了——
**只为让它被跑到、然后被拒掉**，好让操作员拿到一句「你装的是 ARM64 版，去装 (64-bit)」，
而不是「这台电脑上没找到 Python 3.11」（他明明装了，于是唯一能做的推理是「再装一遍」）。
这与 32 位那一项被列进来是同一条理由。

**判据是 `sysconfig.get_platform() != 'win-amd64'`，不是 `platform.machine()`。**
后者在 Windows 上返回 `PROCESSOR_ARCHITEW6432` 优先于 `PROCESSOR_ARCHITECTURE`，
而前者的含义就是「模拟层下面那个原生架构」（CPython `platform.py` 那两行上面写着
`# WOW64 processes mask the native architecture`）。于是 ARM Windows 上那个 **x64**
解释器会被报成 `ARM64`：提示他「请装 x64 版」→ 他装的本来就是 x64 版 → 重跑还是这一句。
**一个没有出路的死循环，比不做这条判据更糟。** `sysconfig.get_platform()` 读的是编译时
烤进 `sys.version` 的架构串，不经任何环境变量，而且**它就是 pip 拼平台标签时读的同一个
字符串**——所以「这一条过了」与「pip 收得下那些 wheel」是同一件事。

**为什么不干脆发一个 win_arm64 的包：上游不发。** 实测（逐包问 PyPI）31 个 wheel 里
有 3 个没有 win_arm64 版本——`cryptography`、`httptools`、`PyYAML`。其余 28 个都有。
而 `cryptography` **不是可选项**：MySQL 8 默认 `caching_sha2_password`，服务端内存缓存
未命中时走 `sha2_rsa_encrypt`，`pymysql/_auth.py` 在那条路上直接
`raise RuntimeError("'cryptography' package is required …")`，而 MySQL 一重启缓存就空。
要发原生 ARM64 包就得降这个安全库的版本、去掉 `httptools`、再维护第二套按平台的依赖表，
而且**开发机验不了**（本机是 macOS）。不值得。

**venv 的 `pyvenv.cfg` 里 `home` 是写死的绝对路径。** 所以选中的那个 Python 一旦被
更新、卸载（学校统一升 3.12 顺手卸旧的），或者装的是用户级而那个账号被删，服务就再也
起不来——而日志里只有一句 `pyvenv.cfg` 相关的英文，没人会联想到「有人动过 Python」。
处置：

- 候选顺序里**机器级排在用户级前面**（`Program Files\Python311`、注册表 `HKLM`），
  用户级只在一台机器只有它时才被选上；
- 选中用户级的而用法是 `lan` 时**打一条 WARN**（只警告不拦，只装了用户级的测试机
  仍然该装得上），给出「用 python.org 的安装包选 Install for all users」这句可照做的指引；
- base python 的**绝对路径记进 `runtime\build.json` 的 `base_python`**，`查看状态.bat`
  会拿它去判那个文件还在不在——**这是唯一能在故障发生之前看见它的地方**；
- `部署说明.txt` 开头新增一节让操作员先装好 3.11，并写明装完不要卸载或升级。

```bash
# 出包侧：包里的 wheel 全是按 3.11 下的，改了 TARGET_PYTHON_VERSION 要重新下
TARGET_PYTHON_VERSION = "3.11"   # deploy/build_package.py
```

---

## 两种「用法」：单机 / 局域网

2026-09-18 加（用户把目标改成「只要保障当前用户可以使用就行」）。安装时问一句，
答案决定后面**所有**事情，而且**装完改不了**——要换就得卸载重装（换用法意味着
删掉/建出计划任务与防火墙规则，那是**迁移**不是升级，半路换会把上一次的东西留成孤儿）。

| | `single` 单机 | `lan` 局域网 |
|---|---|---|
| 谁用 | 只有装它的那个人，`127.0.0.1` | 同一校园网里的老师们 |
| 安装目录 | `%LOCALAPPDATA%\xinliceping` | `C:\xinliceping` |
| 管理员权限 | **不需要**（不提权、不弹 UAC） | 需要 |
| 计划任务 | **不注册** | 注册，开机自启（SYSTEM 身份） |
| 防火墙 | **不放行** | 放行入站端口 |
| 平时怎么起 | 双击「启动服务.bat」，关机就停 | 一直在跑，重启自己回来 |
| `.env` 的 `XLP_HOST` | `127.0.0.1` | `0.0.0.0` |
| `ops.ps1` 的 `$needsAdmin` | 恒假 | 见下 |

**这一维叫「用法」（`usage`）不叫「模式」**：`模式：升级现有安装` 已经占着那个词，
同一份日志里两行互不相干的 `模式：` 会让 `grep` 找不出东西。

`install.ps1` 的 `Resolve-Usage` 按序判：参数 → `-InstallDir` 指的那个目录里的
`runtime\build.json` → 两个默认目录哪个装过 → 问（**只有首次安装才问**）。
`ops.ps1` 的 `Get-InstalledUsage` 只读 `build.json`，**读不到/字段不在/值认不出
一律回退 `lan`**——那个字段是 2026-09-18 才有的，此前装出来的包全是局域网那一种，
回退成 `single` 会让那些服务器上的服务再也起不来，而「查看状态」还会说一切正常。

`ops.ps1` 的 `$needsAdmin` 跟着用法收窄。**`reset-admin` 在名单里不是因为要提权**
（它改的是数据库里的一行），而是因为局域网用法下 `backend\.env` 只授了 SYSTEM 与
Administrators，它得读得到那个文件才连得上库。单机用法把当前用户也授进去了，
那条路上它照样不提权。

---

## 数据库由谁准备：安装器来准备 / 你自己准备 / 库和表你自己建

2026-09-18 加，同一天加了第三项（用户的原话：「我先把数据库准备好，只一键安装应用服务
（前后端）即可」，以及后来的「我现在可以手工创建数据库，并建立数据表，其他由你来完成」）。
第 1 步多问一句，答案是 `installer`（默认，直接回车）、`prepared` 或 `schema_prepared`。

**它与「用法」不是一类东西**，所以没有做成第二个 `-Usage`：用法决定要不要提权、
装到哪、注册计划任务，而且**必须在提权之前就定下来**（提权出来的子进程从第 1 行重跑）；
这一问排在那之后，不参与任何跨进程传递，也不需要装完再改。所以它**没有命令行参数**——
一个没有任何调用方会传的参数，等于一条没有测试的代码路径。

**三种取值只差一件事：这个库里，哪几样归操作员。** 第二项与第三项的区别正是用户那句
「其他由你来完成」指的东西——选 2 连基础数据与管理员密码都推给了操作员，而选 3 不是。

| | 选 1 `installer`（默认） | 选 2 `prepared` | 选 3 `schema_prepared` |
|---|---|---|---|
| 建库 `app.db.create_database` | 跑（幂等） | **不跑**，改成一个连接自检（`dbcheck` 探针） | 同选 2 |
| 表结构 | 迁移建出来 | 迁移建出来 | **校对**（`ensure_schema`），见下 |
| 迁移 `alembic upgrade head` | 跑 | **跑** | **跑** |
| 空库检查 `app.db.check_empty` | 跑（只警告） | 不跑（它本来就空着） | 跑（只警告） |
| 基础数据 `app.db.seed` | 跑 | **不跑** | **跑** |
| 基线清理 `app.db.reset_to_baseline` | 跑（除非 `-KeepData`） | **不跑** | 跑（除非 `-KeepData`） |
| 管理员密码 `app.db.set_admin_password` | 设成操作员输入的那一个 | **不设**，也**不问** | 同选 1 |
| 谁来保证库/表/数据 | 安装器 | 操作员（《部署说明.txt》那一节有三条命令） | 库与表操作员，数据安装器 |

**迁移三种都跑，是这一维唯一一处刻意的「越界」。** 判据是那句话：**库与数据归操作员，
表结构归这一版程序**。不跑它的下场不是报错，是「屏幕说安装成功、页面 500」——换了程序
文件而表结构停在上一版时，**登录页照样打得开**（它读的 `system_setting` 是旧表），
而第 6 步那个健康检查探的正是登录页要的那一个端点（`/public/branding` 会静默回退默认值，
见 CLAUDE.md §5），所以这种「一半坏掉」它抓不住。

**选 3 是「表结构归这一版程序」这句话唯一的例外情形，于是它逼出了 `ensure_schema`。**
手写的建表语句里**没有 `alembic_version`**——那是 Alembic 自己的版本记录表，只有 Alembic
会建。而迁移是无条件跑的，表已经存在时它在第一条 `ALTER TABLE … ADD COLUMN` 上撞
`Duplicate column name`，报出来是一句英文 MySQL 错，离原因很远。`backend/app/db/ensure_schema.py`
排在迁移**之前**（三种分工共用，它只读表名/列名）：库还是空的就放行，表对得上就
`alembic.command.stamp` 补上那本账（只写一行，业务表一行不动），对不上就逐条念出缺什么
并停下。**次序就是它的全部价值**——挪到迁移之后，迁移已经撞停，补账没有意义。
它**刻意不在** `DATABASE_WRITE_COMMANDS` 那张表里：那张表回答「选 2 时安装器该不该碰这个库」，
而这一件三种都要做。

**空库检查（`app.db.check_empty`）是选 1 与选 3 的护栏，只警告不拦。** `reset_to_baseline
--yes` 是无人值守的破坏性脚本，而安装器判「首次还是升级」看的是**安装目录在不在**，不是
库里有没有东西——「同一个库 + 一个新目录」恰好落进这一支，而选 3 的人手上的库常常是
**还原来的一份备份**。它查 `school` / `user_account` / `student` 三张根表，非空时退出码 1
（`-AllowFailure` 收下），屏幕上写清后果与出路（此刻还能 Ctrl+C 停下先备份）。

五个消费者读的是同一个值：`Read-InstallSettings` 的两支都要把它放进 `$settings`
（漏了的话第 4 步取到空串、三个比较全假，安装器**默默按选 1 做**——那正是选 2
唯一不能发生的事）；第 4 步按它分叉（判据写成 `-eq 'installer'`，剩下两支自然落到 else 上，
**这样选 3 不会掉进去建库**）；`runtime\build.json` 记 `database_mode`；第 6 步
服务起不来时按它多说一句指路的话；`ops.ps1` 的「查看状态」按它多说一句中文。
取它一律走 `Get-SettingText`——升级分支的 `$settings` 里没有这一项，裸读会抛
`PropertyNotFoundException`（下面的小节）。

**选 2 且首次安装时，第 4 步要 WARN 着说清「基础数据没写进去，装完打不开任何页面」。**
那一刻屏幕上写着的是「安装完成」，而库是空的：不说这一句，操作员唯一的推理是「装坏了」。
**这一句只对选 2 说**——选 3 灌了数据，说了就是假话，而操作员会照着它去手工补一遍。

守卫在 `test_windows_assets.py`：`database_mode_branches` 现在**按值**返回
`(码, 命中那一支, 另一支)`，`database_mode_landings` 把它派生成三条路各自真正会走到的
文本并集，断言全对着那些并集说。只断言「某一处的某一支」会张冠李戴——「选 3 也要灌基础
数据」在「选 2 那一支的 else」里成立，而那个 else 同时也是选 1 的。**它们断的是「有没有做」
与「有没有说」，不是「有没有报错」**——上面那个失败模式一声不响。

---

## 两条部署路，并存

| | 这条路 | Docker 那条 |
|---|---|---|
| 面向 | 装不了 Docker 的 Windows 服务器 | 任何有 Docker 的机器 |
| 前端 | 后端**同一个端口**托管（`XLP_WEB_DIR`） | `frontend/nginx.conf` 反代 |
| 产物 | `dist/心晴部署包_V<版本>.zip` | `docker compose up -d` |
| 入口 | `deploy/build_package.py` | `docker-compose.yml` |

**前端必须同源**（`services/api.ts` 里 `API_BASE = '/api/v1'` 是相对路径，
`createWebHistory()` 没带 base），所以只有这两种形状，没有第三种。

---

## 重新出包

```bash
make deploy-package
```

出 `dist/心晴部署包_V<版本>.zip`（约 11MB）。**在开发机（macOS / Linux）上跑，不是在目标机上。**

它做五件事：

1. `cd frontend && npm run build` —— 顺带跑 `vue-tsc`，类型错就在这里断掉
2. 按 `requirements.lock.txt` 下载 **win_amd64 / cp311** 的 wheel
3. 复制 `backend/`（剔除 `.env` / `.venv` / `tests` / `__pycache__`）、`data/`、
   `frontend/dist/`、`deploy/windows/*`
4. **自检**（见下）
5. 压缩，并验 zip 里每个条目名带 UTF-8 标志位

（2026-09-18 去掉的是原来的第 3 步「下载并解开 `python-3.11.9-embed-amd64.zip`」，
连带 `deploy/.cache/` 这个下载缓存一起没了——那 21MB 就是体积从 22MB 降到 11MB 的全部来源。
`MUST_NOT_EXIST` 里加了 `"python"`，挡住 `--keep` 时上一次留下的那一层混进包里。）

常用开关：

```bash
python deploy/build_package.py --reuse-frontend   # 跳过 npm build（确认 dist 是新的时用）
python deploy/build_package.py --keep             # 保留上一次解开的 dist/心晴部署包_V<版本>/（省下 wheel 的下载）
```

`--keep` 省的是第 2 步那 31 个 wheel 的重新下载，用在**上一次出包失败在半路**的时候
（2026-09-18 之前它其实是坏的：第二次跑到「复制源码与前端」会撞
`FileExistsError: ... dist/心晴部署包_V<版本>/data`，而那个错与「包坏了」毫无关系）。
**代价**是源里**删掉**的文件不会从产物里消失——`MUST_NOT_EXIST` 只钉住了
`python/` 与 `deploy/task.xml` 两个。所以有「删文件」的改动时，出**不带 `--keep`** 的
那一次；平时升级文件内容用 `--keep` 是安全的（逐文件覆盖）。

---

## 包里是什么

```
心晴部署包_V<版本>/
  一键安装.bat            ★ 操作员双击的就是它
  部署说明.txt              给操作员看的（UTF-8 with BOM，记事本直接看得对）
  deploy/
    install.ps1             安装逻辑，中文提示都在这里（UTF-8 **with BOM**）
    ops.ps1                 装完之后八个按钮背后的动作
    manual-start.ps1        手工启动（`-Action backend|frontend`），见下面一节
    manual-migrate.ps1      只升数据库（不起服务、不碰程序文件），见下面一节
    serve_frontend.py       只发前端时用的静态服务器 + `/api` 反代（纯标准库）
    手工启动后端.bat 手工启动前端.bat 数据库增量升级.bat   ★ 出路，不是步骤（纯 ASCII）
      后端 / 前端那两枚：一键安装装不完时；第三枚：库要单独升（见下面「把停在 V1.0.0 的库升上来」）
    sitecustomize.py        固定 stdout 编码（装进 venv 的 site-packages 才生效）
    package-info.txt        版本 + 构建时间 + 需要的 Python 版本，install.ps1 读它
    requirements.lock.txt   （副本，出问题时好对账）
    ops/                    八个按钮，装完由 install.ps1 拷到安装根目录**然后删掉这一层**
  backend/  data/  frontend/dist/  wheels/
```

**包根只有两个文件**（`build_package.py` 的 `ROOT_LEVEL_ASSETS`）。手工启动那两枚按钮
**刻意留在 `deploy\` 里**：摆在包根的话，一个还没开始装的人会先看到两个「手工」按钮，
而他此刻该点的是「一键安装.bat」。它们在《部署说明.txt》里写着**完整位置**，
所以操作员找得到。

安装完之后，安装目录（局域网 = `C:\xinliceping`，单机 = `%LOCALAPPDATA%\xinliceping`）
变成：

```
<安装目录>\
  backend\  data\  frontend\  wheels\  deploy\    ← 与仓库同形
  runtime\         ← 它不在包里，是装出来的：
      venv\            ← 用**这台电脑上**的 Python 3.11 建的虚拟环境（pip 装的依赖在这里）
      .env  logs\  build.json  mysql-client.cnf
  一键安装.bat 启动服务.bat 停止服务.bat 重启服务.bat
  查看状态.bat 查看日志.bat 备份数据.bat 重置管理员密码.bat 卸载.bat
```

（`deploy\` 这一层装完之后**照样在**，手工启动那两枚按钮就在里面——装不下去的时候
操作员要去的地方。`ops\` 那一层则被 `install.ps1` 删掉了，它的八个按钮已经拷到包根。）

**升级时 `runtime\venv` 走 `python -m venv --clear` 重建**（20~40 秒建环境 + 20~60 秒
装 31 个 wheel）。不做「跳过重建」的优化：那是「锁文件变了却还用旧依赖」那条路。
`--clear` 的语义是「先删掉那个目录里的全部内容再建」，它**不检查那是不是一个 venv**，
所以装之前先自己判一次（有目录、没有 `pyvenv.cfg` → 停下来问人）。

`runtime\build.json` 里有两行是 `ops.ps1` 的判据：`"usage": "single" | "lan"`
（**判断该走哪条路的唯一依据**，它不看 `.env`，也不看安装目录长什么样）与
`"base_python": "C:\\...\\python.exe"`（建 venv 用的那个解释器，`查看状态.bat`
拿它去判文件还在不在）。那一块**排在注册计划任务之前**写：装到一半失败时，
这两样照样已经记下来了，重跑不会又问一遍。

**目录形状必须与仓库根一致，一点都不能压平**：`backend/app/db/seed.py` 用
`parents[3] / "data" / "mht_scale.json"` 找题库，压平之后它会**静默**回退成
「MHT题目 001（开发占位…）」——装出一个看起来完全正常、题库却是假的实例。

---

## 一键安装装不完的时候：`manual-start.ps1` 与两枚按钮

用户的原话：「如果仍然失败，我计划直接手工操作，分别启动前端后端，需要什么指令，帮我列举下」，
接着是「能不能总结为两个脚本，我直接跑」。产物就是这一套——一个脚本加两枚纯 ASCII 按钮
（`deploy\手工启动后端.bat` / `deploy\手工启动前端.bat`），操作员双击，不用打任何命令。

它做的是 `install.ps1` 第 2～4 步之后那几件事，只是拆薄了，而且**每一件都重新判一次**
（做过的不重做）：

| | 每次都做 | 只在缺的时候做 |
|---|---|---|
| `-Action backend` | 校对表结构 → 迁移 → 起 `run_server.py` | 建 venv（少了才建，**不重建**）、写 `.env` 骨架（写完就停，让人去填口令）、`app.db.seed`（库空才灌）、清库（**问过才清**） |
| `-Action frontend` | 探一次 `/api/v1/public/branding` 再起服务 | —— |

与 `install.ps1` 的差别**只有两处，都是有意**：

1. **清库要先问人**（`Read-Host`，回车那一支是「不清」）。`install.ps1` 里
   `reset_to_baseline --yes` 是无条件跑的——那条路上整件事都在安装器手里，而手工这条路
   不假设任何东西。**一个破坏性动作的默认值不能是破坏。**
2. **不设管理员密码。** 它跑 `app.db.seed`，所以 admin 的初始密码是种子里那个 `123456`
   （脚本会把这句话打出来，并指出出路是安装目录里的「重置管理员密码.bat」）。

四条不能改的约定：

- **前端那一路必须是 `serve_frontend.py`，不是 `python -m http.server`。** 前端是构建产物
  （`frontend/dist`），而 `API_BASE = '/api/v1'` 是相对路径：静态服务器会把 `/api/...`
  回成 404 的 HTML，`api.ts` 拿它去解 JSON，症状是「登录页打得开、每次登录都失败」
  （`Unexpected token '<'`）。所以「前端单独一个进程」只有两种成立方式——后端自己发
  （`XLP_WEB_DIR`），或者中间一个**会反代 `/api` 的**服务器。这个文件是后者，只用标准库
  （目标机上只有 venv 里那 31 个 wheel，没有 flask 之类可借）。
- **Python 的输出不许经过 PowerShell 的管道**（`& python …`）。那条路会把子进程的 stdout
  按 `[Console]::OutputEncoding`（中文 Windows = cp936）解码，而 `sitecustomize.py` 把
  Python 的 stdout 固定成了 UTF-8——中文变乱码，而且**不可逆**（写进去的已经是替换字符），
  紧接着那句「上面是它自己说的话」当场失效。所以一律走 `Invoke-Native` 的
  `Start-Process -NoNewWindow`：子进程继承控制台，Python 走自己的 Unicode 控制台 API，
  整条路上没有第二个人在解码。（两个例外是刻意只输出 ASCII 的探测——那里要的正是把输出
  接回来读一行。）
- **窗口就是服务。** 后端那一个用 `-Wait` 挡住，关掉窗口 = 停服务，与单机用法的
  「启动服务.bat」同一个语义（理由见 CLAUDE.md §18 里 venvlauncher 那一段）。
- **两枚按钮与 `manual-start.ps1` 必须在同一个目录里**：按钮里写的是 `%~dp0manual-start.ps1`。
  `copy_windows_assets` 把 `deploy/windows/` 下非包根的东西都放进 `<pkg>\deploy\`，
  所以它们天然在一起——有一条守卫**真的调一次那个函数**来钉这条（把按钮挪进
  `ROOT_LEVEL_ASSETS` 会红）。

判据在 `backend/app/tests/test_windows_assets.py` 末尾那一组（10 条），每条都做过变异验证。
**它们证明不了 PowerShell 的语义**（那要真机），能证明的是「有人把它改回去会红」——
与这个文件里其余 Windows 侧资产同一条规矩。

---

## 把停在 V1.0.0 的库升上来：`manual-migrate.ps1` 与那份手工 SQL

**这一节回答的是「我的库是 V1.0.0，现在这一版的程序架上去能不能用」。** 能——但那要
库先走一遍 `0012 → 0018` 那六条迁移。两条路，**做的是同一件事**（同一组迁移、同一个
版本戳），选一条就行：

| 路 | 谁用 | 动作 |
|---|---|---|
| 一键安装（推荐） | 装得上 | 把新包覆盖到安装目录 → 双击「一键安装.bat」→ 第 4 步跑迁移 |
| `数据库增量升级.bat` | 只想先升库、或后端起不来看不出库升没升 | 在**装过的**安装目录里双击它 |
| `backend\sql\upgrade_from_v1_0_0.sql` | 那台机器根本没有 venv / 起不了 Python | 拿这个文件到别处 `mysql < 它` |

三条路都**只对 V1.0.0 的库跑一次**。跑第二次不用怕（迁移是幂等的、会直接跑完），但它
是白跑的。

### `manual-migrate.ps1` 做的是安装器第 4 步那两步

```
python -m app.db.ensure_schema      ← 校对表结构（手工建的表在这里盖章）
python -m alembic upgrade head      ← 执行迁移
python -m app.db.ensure_schema      ← 再校对一遍，并由它念出最终的版本戳
```

**次序不能换，两头都不能**：手写的建表语句里没有 `alembic_version`（Alembic 自己的
账本），直接迁移会撞 `Duplicate column name`；而换了程序文件却没迁移的下场是
「登录页打得开、一操作就 500」（CLAUDE.md §18「数据库由谁准备」那一节）。

**第三步不是走过场**：屏幕最后那一行 `alembic_version = …` 是 `ensure_schema` **自己
念出来的**，不是脚本拼的。拼出来的那句没有任何东西保证它是真的。

它**不碰程序文件、不注册计划任务、不写 `runtime\build.json`**，也**不设管理员密码**——
与「升级不动你已经配好的东西」是同一条（见下面「重跑即升级」）。所以它的正确用法是
**先把新包覆盖到安装目录，再从那里双击它**；如果你是在刚解压开的包里点的它，脚本会
停下来告诉你还没有 venv。

### 那份手工 SQL 是怎么来的（★ 别手写它）

`backend/sql/upgrade_from_v1_0_0.sql` 是**生成的**，生成器是
`deploy/build_migration_sql.py`（`make db-upgrade-sql` 跑它，**出包时也跑**）。它从
**两份既有来源**现渲染：

    链上每条迁移的 `PRECHECKS` 常量   +   `alembic upgrade 0012:head --sql` 的离线渲染

所以它与 `schema_mysql8.sql` 同一个性质：**快照，不是来源**。改了任何一条迁移就要重跑
`make db-upgrade-sql` 并提交那份文件——它随 `backend/` 一起进包，出事的地方是客户手上的库。
`test_incremental_upgrade_sql.py` 逐字节盯着它。

**三件只有真 MySQL 才知道的事**，都写进了生成器与那份文件的注释里：

1. **每个迁移拆成【检查】/【DDL】两半、交错排列。** 全部检查堆在最前面**真的失败过**：
   `assessment_session.school_id` 是 `0013` 加的，于是第一条检查就死在一句
   `1054 Unknown column 'school_id'` 上。
2. **检查本身不会让脚本停下。** 它此前只是把有问题的行打出来、然后继续跑一百条 DDL，
   直到某条无关的 `ALTER` 报 `1048 Column … cannot be null`——库留下一半新表，版本戳停在
   `0012`。现在每条检查后面跟一次 `CALL xlp_check_empty(...)`，那个临时存储过程里有
   `SIGNAL SQLSTATE '45000'`，**有行就当场中断**，而且中断发生在任何 DDL 之前。所以
   操作员看到的 `ERROR 1644 (45000)` 是一句中文，不是一句英文列名。
3. **检查写成 `SELECT EXISTS(<子查询>)`。** 派生表会在**重复列名**上撞 1060，`COUNT(*)`
   会在带 `GROUP BY` 的检查上撞 1172（返回多行）。`EXISTS` 一律返回一行一列。

存储过程要 `CREATE ROUTINE` 权限，用完**删掉**（文件最后一句就是 `DROP PROCEDURE`）。

**它仍然是「一条长 SQL」**，而需求说明书 §16.1 明确说这种长脚本不建议直接在生产上跑
——这一点是知情的：把它变得可执行的是上面那道门（**先检查、后 DDL**，不是把文件切开），
以及它就是那六条迁移的渲染、不是第二份实现。装得上就还是走一键安装。

### `is_offline_mode()` 那两行

`0013` / `0014` 的 `_precheck()` 开头有一句 `if context.is_offline_mode(): return`。
`--sql` 模式下 `op.get_bind()` 回的是一个 `MockConnection`，它的 `execute()` 返回 `None`
→ 后面 `.fetchall()` 抛 `AttributeError`，那份 SQL 根本渲染不出来。跳过这一层**不是**
放松校验：真正的检查由 `PRECHECKS` 常量表达，生成器把它提升到那份文件的【检查】段里。

---

## 一份空库的初始化数据：`seed_mysql8.sql`

**这是第四份随包走的 SQL**，四份的分工：

| 文件 | 建表吗 | 删行吗 | 写行吗 | 谁用 |
|---|---|---|---|---|
| `reset_to_baseline.sql` | 不 | 删（清成基线） | 不 | 把库清回「只有 admin + 量表与基本配置」 |
| `schema_mysql8.sql` | **建**（34 张表） | 不 | 不 | 操作员自己建表那一路 |
| `upgrade_from_v1_0_0.sql` | 改（`0012 → 0018`） | 不 | 不 | 停在 V1.0.0 的库 |
| `seed_mysql8.sql` | 不 | 不 | **写**（6 张表 105 行） | 自己建了空库的人：**没有数据就登录不进去** |

**内容边界**：只有系统基础数据与管理员账号——MHT 量表与 100 道题、评分规则、admin
（初始口令 `123456`）、学校那一行。**不含任何演示数据**（从不跑 `seed_demo.py`）。
这个终点不是这份文件定的：它由 `reset_to_baseline.sql` **独家保证**，生成器只是把那个
结果 dump 出来（遍历 `Base.metadata.sorted_tables`，非空就写），所以将来那条线挪了，
这份文件自动跟上。

**它是生成的，而且生成它要连一台活着的 MySQL。** 生成器 `deploy/build_seed_sql.py`
（`make db-seed-sql`）真的跑一遍：建 `<主库名>_init` → `alembic upgrade head` →
`app.db.seed` → `app.db.reset_to_baseline --yes` → 读结果 → 删库。**不能做成纯文件操作**，
因为数据里有 id 引用（`user_scope.school_id` / `scale_question.scale_id` /
`scale_rule.scale_id`），它们是 `seed.py` 里 `db.flush()` 的**执行结果**、不是它的代码；
在 SQL 里再抄一份必然漂移，而一份抄错的规则 JSON 会让那个库的评分与别处不同、且看不出来。
`_safety_checked_names()` 动手之前先把三件事断掉（库名以 `_init` 结尾、与主库不同名、
是 mysql），所以 `make db-seed-sql` **只碰那一个库**。

**但它不在出包时重新生成**（与 `upgrade_from_v1_0_0.sql` 那条路不同，也**不是**漏了）：
那条的两个来源都长在当前源码树上，出包时源码树就是最新的；这一份多了一个**外部来源**
（一个库），出包时重生成反而会**掩盖**「有人改了 `seed.py` 却没重跑 `make db-seed-sql`」
——那个信号应该由守卫红在那次 `make test` 上，不该被一次静默重生成盖掉。它与
`schema_mysql8.sql` 同一档：**仓库里的快照，随 `backend/` 一起进包**（`REQUIRED_PATHS`
钉住它），靠守卫保鲜——`backend/app/tests/test_seed_sql.py` 拿 `compose(...)` 的产物与
盘上那份逐字节比，红的出路是重跑 `make db-seed-sql` 并把那份文件一起提交。

**三个非确定源被固定下来，判据才回得到「逐字节」**：`password_hash`（bcrypt 的盐是随机的）
与 `assessment_scale.published_at` 换成模块级常量；各表的 `created_at` / `updated_at`
**不写进 INSERT**，交给客户那台库的 `DEFAULT (now())`（它回答的是「这一行什么时候落进
**这个**库」，语义上更对）。第三条的判据是「`server_default` 的文本含 `now()` /
`CURRENT_TIMESTAMP`」，**不是**「凡是有 `server_default` 的列都不写」——后者会漏掉
`must_change_password` 这种，症状是客户库里的值悄悄变成默认值。

**怎么跑**：表要先有（安装程序第 4 步会把它建出来），然后
`mysql --default-character-set=utf8mb4 … <库名> < backend\sql\seed_mysql8.sql`。面向操作员
的三步写在《部署说明.txt》的「数据库我自己准备」那一节（③），两句话跟着它走：
**`--default-character-set=utf8mb4` 不能省**（文件里有中文——校名、题干——按别的编码读会
得到一串乱码，而它**看起来像导入成功**），以及**它只跑一次**（第二遍会在第一张表上撞
`Duplicate entry`；这是有意的，静默跳过会让人以为跑过了。要清空重来是另一个文件：
`reset_to_baseline.sql`）。

**与安装器不冲突**：`seed.py` 的四个 seed 函数都有早退守卫，所以「先手工灌这份 SQL、
再走选 1 / 选 3 安装」不会撞 `Duplicate entry`，安装器只会把已有的那些行跳过。

---

## 加一个依赖：改**两处**

```toml
# backend/pyproject.toml      —— 声明（开发机上装、测试用）
dependencies = [..., "新的包>=1.2"]
```

```
# deploy/requirements.lock.txt —— 目标机上真正装的那一份，钉死版本
新的包==1.2.3
```

**两处都要改，只改 pyproject 的话目标机上没有它**（锁文件是出包的唯一输入）。
然后重出包，看自检里 `verify_wheel_closure` 那一条过不过。

### 为什么锁文件是一串叶子而不是依赖树

`pip download --platform win_amd64` **不改变环境标记的求值**，它只影响选哪个 wheel。
让 pip 自己去解析依赖，等于在 macOS 上按 macOS 的标记算一遍再拿 win_amd64 的包，
那是错的。所以：**锁文件 = 显式叶子清单 + `--no-deps`**，闭环由
`verify_wheel_closure()` 在自检里收口（它逐个读 wheel 的 `METADATA`，把
`Requires-Dist` 在 Windows 环境里求值一遍，确认每个依赖都在这批里）。

它已经捞到过三条真的漏项：`greenlet` / `cffi` / `pycparser`。

### `cryptography` 不是可选项

MySQL 8 默认插件 `caching_sha2_password`，它的**快路径**（服务端内存缓存命中，只做一次
SHA256 scramble）用不到 RSA，也就不需要 `cryptography`；缓存未命中才走
`sha2_rsa_encrypt`，而 `pymysql/_auth.py` 在那条路上直接抛
`RuntimeError("'cryptography' package is required …")`。

**那个缓存是服务端内存里的，MySQL 一重启就空。** 所以「装的时候连得上」不能作为
「可以不带 cryptography」的证据——装完重启一次服务器它就没了。

---

## 改系统版本：改**一处**

```python
# backend/app/version.py —— 唯一出处（今天这一版是 1.1.2 / V1.1）
__version__ = "1.1.2"      # 规范串：进 package-info.txt、进 /openapi.json
VERSION_LABEL = "V" + …    # 显示串（V1.1）：进 /public/branding，界面那一行读它
```

改完重出包，目标机上 `install.ps1` 第 0 步会打 `安装包版本 1.1.2+<日期>`，
「查看状态」里也有一份。

**`frontend/package.json` 的 `version` 是唯一的镜像**，因为 npm 那个字段没法动态
（必须是字面量）。`backend/app/tests/test_app_version.py` 逐字盯着它，对不上就红 ——
所以「改两处」这句话在那里是被强制的，不靠记性。**别的地方不该再有版本字面量**：
`pyproject.toml` 是 `dynamic = ["version"]`（指向那个 attr），
`app/main.py` 从 `app/version.py` 拿，`deploy/build_package.py` 也读那个文件。

只改一处会怎样：

| 只改了 | 后果 |
|---|---|
| `frontend/package.json` | 测试红（那是最轻的一种失败） |
| `app/version.py`、不重出包 | 目标机上的版本**不动** —— 界面上还是旧的，而那正是操作员报故障时读的数 |
| `pyproject.toml`（加回一个字面量） | 测试红。**而且 pip 会优先用那个字面量**，于是 `app/version.py` 改了、装出来的包没改 |
| 只在 `deploy/build_package.py` 里写死 | 包上的版本与后端自己报的不是一个数 |

### 为什么 `pyproject.toml` 是 dynamic 而不是「两处一起改」

它此前有一段很长的历史：pyproject 写 `0.1.0`，而 `/openapi.json` 报的也是 `0.1.0`
——**但后者根本不是谁设的，是 FastAPI 没收到 `version=` 时的内置默认值**。两个数
撞在一起纯属巧合，改 pyproject 不会带动它，而屏幕上看起来一切正常。这正是
CLAUDE.md §16 那条「同一件事写两份必然漂移」的又一个实例，只是它漂得看不出来。
现在那四个数字（pyproject / openapi / 两个 package.json）全部收敛到
`app/version.py` 一个文件，`test_app_version.py` 把每条派生路径都钉住。

`app/version.py` **里不许有 import**：setuptools 的 `attr:` 要静态读它，
一个 import 就能让 `make install` 在一个与版本无关的地方失败。

---

## 自检查什么（以及查不到什么）

`deploy/build_package.py` 的 `verify_package()`：

| 查 | 为什么 |
|---|---|
| `REQUIRED_PATHS` 逐个存在 | 少一个就停在这里，别等装到一半 |
| `MUST_NOT_EXIST`（`deploy/task.xml`） | 计划任务改用 cmdlet 注册了，钉住「它没悄悄回来」 |
| wheel 依赖闭环 | `--no-deps` 的配套代价，见上 |
| 产物里没有 `.env` / `.venv` / `tests` / `*.pyc` / `*.so` | 「copy 时排除了」与「产物里确实没有」是两件事 |
| 题库按 `seed.py` 的算法找得到 | 压平目录那条约定在打包侧的守卫 |
| **`.ps1`/`.txt` 恰好一个 BOM、`.bat` 是纯 ASCII 无 BOM** | 见下 |
| zip 条目名 UTF-8 往返一致且带标志位 | 操作员要双击的正是几个**中文名**的 .bat |

`test_windows_assets.py`（后端测试，`make test` 就会跑）把守同一批编码约定，
外加两条跨文件契约：**八个按钮与 `ops.ps1` 的 `-Action` 一张不多一张不少**（两个方向都断言），
以及按钮用 `%~dp0` 而不是 `%~dp0..`。

它还有一组读 **`install.ps1` 正文**的用例（2026-09-18 加，见下）：外部命令的成功码要由调用点
声明、原生输出不按 UTF-8 解、Python 那一路**仍然**是 UTF-8、提权时把日志路径传给子进程。
再加一组读 **「用法」这一维**的（2026-09-18）：提权只对局域网、`XLP_HOST` 按用法选、
`.env` 的 ACL 里带着装它的那个人、计划任务与防火墙只对局域网、`build.json` 记下用法、
`ops.ps1` 的 `$needsAdmin` 按用法收窄、`Stop-RunningService` 的进程那一半不被
「有没有计划任务」挡住、起停两头都拿端口当第二判据、每个 `Invoke-Native` 调用点都引了路径。
再加一组读 **「数据库由谁准备」这一维**的（2026-09-18）：选 2 时四件写库的事一件都不做、
迁移**三种分工都跑**、第 4 问在首次安装与升级**两条路都问**且默认 1、`build.json` 记下它、
手册里有安装器指过去的那两节（逐字对 `-m` 那几条命令）。加第三种分工时
`database_mode_branches` 从「只认 `-eq 'prepared'`」改成**按值**返回，并新增
`database_mode_landings` 把每一处展开成三条路——**判别力不许因为加了第三种而降级**，
所以每条老断言都改成对着「某一种取值真正会走到的文本并集」说，而不是对着某一处的某一支。

另有三条只属于第三种分工的：`ensure_schema` 恰好一处且**排在迁移之前**（次序是它的全部
价值）、`check_empty` 带 `-AllowFailure` 且排在 `seed` 之前、以及
`Invoke-PythonScript` 的 `$Arguments` 真的透传下去了。最后一条配了一个**真的执行**的用例
（`test_the_config_probe_round_trips_the_password_and_forgives_a_hosts_case`）：把 `config`
探针的 Python 源码从 here-string 里切出来，用 `subprocess` 跑三遍（对得上 / 主机名只差
大小写 / 口令对不上）。那段 Python 在开发机上永远跑不到（本机没有 PowerShell），
而它恰恰是那个「从来没跑过」的自检。每一条都做过变异验证
（20 条，`make test` 不跑它们，是开发机上的手工动作）。

⚠ 这几组**证明不了 PowerShell 的逻辑本身**——它们是**文本断言**，挡的是「有人把保证改回去」，
挡不住「`if ($Usage -eq 'lan')` 写成了 `-ne`」（`$Usage` 确实出现在那一行，断言照样绿）。
真正能证伪的只有真机，见下面「在 Windows 上验一次」。别把它读成一层更强的保证。

### 编码这件事值得单独说

Windows 侧那几个文件在开发机上一行都不会执行，第一次被执行是在一所学校的服务器上。

- **`.ps1` / `.txt` 必须有 UTF-8 BOM。** PS 5.1 只有看到 BOM 才按 UTF-8 读；没有它时按当前
  ANSI 代码页读，中文全乱。更糟的是 cp936 下中文字符的尾字节会**吞掉紧跟的 ASCII 字符**
  （引号、括号），报出来是一句与编码毫无关系的语法错误，位置还指在别处。
  ⚠ **用编辑器改 `install.ps1` 会静默地把 BOM 抹掉**——这条守卫就是踩到之后加的。
  ⚠ **而且 BOM 只许一个**（2026-09-18 补）。「有个 BOM」与「只有一个 BOM」是两件事，
  而前缀式的检查分不出来：`install.ps1` 一度开头堆着**四个** BOM，出包时的自检照样打
  「带 BOM」。这是**脚本写文件时读得没剥、写时又补**留下的印子（那次是我自己写的一个
  变异验证脚本，基准读成 `.decode("utf-8")` 少了 `-sig`，跑一次多一个），
  所以**手写脚本改这几个文件时**要格外小心。守卫是
  `test_the_bom_is_exactly_one_and_nowhere_else`（开头恰好一个 + 全文除它以外一个都没有）。
- **`.bat` 不许有 BOM**（会把第一行 `@echo off` 读坏），且每个字节都必须 < 128。
- **「按任意键继续」是 cmd.exe 自己的 `pause`，不是 `echo 中文`**：`pause` 打印的是
  操作系统本地化的那句话，代码页与控制台一致，中文 Windows 上自动就是中文。
- **不要加 `chcp 65001`**：PS 5.1 的 `Write-Host` 走 `WriteConsoleW`，与控制台代码页
  本来就无关；而 `chcp 65001` 恰恰是 PS 5.1 输出被重定向时变乱码的那个组合。
- **原生工具（robocopy / icacls）的输出按「控制台代码页」解，Python 的按 UTF-8 解，两条路
  不要互相看齐**（2026-09-18）。这些工具重定向到文件时写的是控制台代码页（中文 Windows =
  cp936），只有 `WriteConsoleW` 那一条路才是宽字符；用 `-Encoding UTF8` 读它，每个中文都会
  变成 U+FFFD 且**不可逆**——写进日志的已经是替换字符，原始字节当场就丢了。
  `install.ps1:257` 此前就是这么读的，于是 icacls 那句「已成功处理 1 个文件; 处理 0 个文件时
  失败」落进日志成了 `�ѳɹ����� 1 ���ļ�`，而操作员唯一能提供的东西就是这份日志。
  现在归 `Read-NativeOutput`（`[Console]::OutputEncoding.GetString`，**只读不改**，
  不违反上面那条）。**Python 那一路不变**：`sitecustomize.py` 把子进程 stdout 固定成 UTF-8，
  所以 `Invoke-Python` 里的 `-Encoding UTF8` 是对的。两条守卫各断一个方向。
- **`requirements.lock.txt` 必须纯 ASCII**（2026-09-18）。规矩与 `.ps1` **正好相反**，
  因为读它的不是 PowerShell 而是**目标机上的 pip**——见下。

#### 为什么 `requirements.lock.txt` 必须是纯 ASCII（而不是「也给它加个 BOM」）

2026-09-18 安装停在第 4 步「安装依赖」，日志最后是

```
pip install --isolated --no-index --find-links C:\xinliceping\wheels --no-deps \
    --only-binary=:all: --no-input --disable-pip-version-check --no-cache-dir \
    --retries 0 --timeout 5 -r C:\xinliceping\deploy\requirements.lock.txt
UnicodeDecodeError: 'gbk' codec can't decode byte 0x84 in position 16: illegal multibyte sequence
```

那个 `0x84` 是第 1 行中文注释里「部署包里…」的「的」字的第三个字节。原因在 pip 那一侧：
`get_file_content()`（`pip/_internal/req/req_file.py`）把**整个文件**交给
`auto_decode()`（`pip/_internal/utils/encoding.py`），而它在文件没有 BOM 时退回
`data.decode()`——也就是**当前 locale 的编码**。中文 Windows 上是 GBK，于是这个文件里
**任何一个中文字符**都会让 pip 在读 requirements 时当场抛异常，**注释里的也一样**。
「这台机器上装得上」证明不了什么：开发机是 macOS（UTF-8），英文版 Windows 是 cp1252，
只有中文 Windows 会撞上——**而那正是我们面向的那一种**。

**为什么不加 BOM：** 加 BOM（`BOMS` 表里第一个就是 UTF-8）确实能修好 pip 这一条读法，
但这个文件还要被人用编辑器打开、被出包时的 `pip download` 读、将来可能被别的工具读。
纯 ASCII 是唯一一种**不需要任何一方「解码得对」**的形状——与 `.bat` 那一条同理
（那里也是「不许有 BOM，且每个字节 < 128」），理由也是同一个：读它的那台机器不由我们决定。

**判据是「逐字节 < 128」，不是「用某个 locale 解一次能过」**：一个 GBK 双字节序列
（如 `C4 E3`，就是「你」）在 cp1252 下也是合法输入，两边各自解出**不同的**乱码，
而那种形状恰恰是不做逐字节判据时会漏掉的。守卫在**两处**：出包前先查源文件
（`build_package.py` 的早检查，一毫秒，早三分钟知道），自检时再查**产物里的那一份**
（pip 读的是它）；`test_windows_assets.py` 里还有一条同源的用例，并且它**在同一次运行里
把判据变异一次**（真文件过、加一行中文就不过），免得守卫哪天被写成恒真还能全绿。

代价是这一个文件的注释只能用英文——这是**唯一**一个这样的文件，理由写在它自己的文件头里，
`CLAUDE.md` §18 也记了一笔。中英两套写法不要往别处推广。

### 自检查不到的（只有真机知道）

- `.pyd` 在这个 Windows 上能不能 import —— 对策是安装器**强制跑一次 import 冒烟测试**，
  失败就停在那一步并留下日志，绝不静默继续。
- Defender 会不会拦安装过程的某一步 —— 对策写在 `部署说明.txt` 里，
  安装器自己**不**去动杀毒软件的排除项（理由见 `CLAUDE.md` §18）。
  （2026-09-18 起包里没有那个未签名的 `python.exe` 了；现在用的是目标机上 python.org
  装的、有 PSF 签名的那一个，风险从「包里带了什么」移到了「装的过程做了什么」。）
- 中文文件名解压出来对不对 —— 但它后面必然跟着一次健康检查，坏了当场看得见。
- **每个外部命令的「退出码 = 成功」是什么语义** —— 2026-09-18 安装停在第 2 步栽在这里。
  `robocopy` 的退出码是**位掩码**：`0..7` 全是成功，**`1` = 成功复制了文件**（全新安装必然
  是它），只有 `8`（有文件没能复制，多半是被占用）与 `16`（严重错误，一个都没复制）是失败。
  按 `-ne 0` 判，全新安装会在「复制程序文件」这一步「失败」退出——**而那时文件其实已经
  铺完了**，后面的写 `.env`、建库、迁移、种基线、注册计划任务、启动服务一件都没做，
  操作员看到的是一个装了一半、还不知道少了什么的目录。
  所以 `Invoke-Native` 现在收一个**声明出来的**成功码表（`-SuccessExitCodes`，默认 `@(0)`，
  icacls 那两处一字不变），robocopy 那处显式传 `(0..7)`。
  这是**这一层唯一一处必须逐个工具判**的地方——新增一个外部命令时先查它的退出码语义，
  别默认 0 / 非 0。
- **每个「读文件的工具」按什么编码读那个文件** —— 2026-09-18 同一天栽的第三次，见上面
  「为什么 `requirements.lock.txt` 必须是纯 ASCII」。它和退格码那一条是同一个形状：
  **在开发机上永远看得见的东西（文件内容、退出码是 0）与那台机器上真正发生的事不是一件事。**
  新增任何「往包里放一个文本文件、由 Windows 侧的工具读它」时，先回答两个问题：
  谁来读、它用什么编码读。**已经在包里的几个文本文件是这么分的**：

  | 文件 | 谁读 | 编码 |
  |---|---|---|
  | `install.ps1` / `ops.ps1` / `部署说明.txt` | PowerShell 5.1 / 记事本 | UTF-8 **带 BOM** |
  | `一键安装.bat` / `ops\*.bat` | cmd.exe | 纯 ASCII，**不许**有 BOM |
  | `deploy\requirements.lock.txt` | 目标机上的 pip | **纯 ASCII**（locale 回退，见上） |
  | `deploy\package-info.txt` | `Read-PackageInfo`（`-Encoding ASCII`） | 纯 ASCII |
  | `backend\.env` | python-dotenv（默认 utf-8） | UTF-8；**带 BOM 也没事**（实测过，见下） |
  | `data\mht_scale.json` | `seed.py` 的 `open(encoding="utf-8")` | UTF-8 |

  `backend\.env` 那一行是**特意查过的**（2026-09-18）：`Write-EnvFile` 用的是
  `Set-Content -Encoding UTF8`，而 PS 5.1 的这个写法**带 BOM**。它没出事，是因为
  python-dotenv 自己会把文件开头那个 BOM 剥掉（拿 2.15.0 + python-dotenv 1.2.3 实测过：
  带 BOM 与不带 BOM 解出来的键值一模一样），而不是因为那条规矩对。**换成别的读法**——
  比如哪天有人拿 `configparser`（按 locale 解）去读它——BOM 与中文就会同时变成一个坑。
  这一格不是「已加固」，是「当前这个读法恰好扛得住」。
  **2026-09-18 起这个文件多了一个读者**：`Repair-EnvFile` 会读它、并就地把坏行写回去
  （`Get-Content -Encoding UTF8` / `Set-Content -Encoding UTF8`，认得出 BOM 那一种，
  也正是它自己写出来的那一种）。它与 python-dotenv 读的是同一份字节，所以这一格
  现在有两个读者要同时成立——加第三个读者时先回来改这一行。

---

## 重跑即升级

同一个 `install.ps1`，发现安装目录下已经有 `backend\.env` 就切到**升级模式**：
不再问数据库、不重建库、不重跑基线清理，只做

```
停止计划任务 → 覆盖 backend/ frontend/ → 重建 venv + 装依赖
             → alembic upgrade head → 启动 → 健康检查
```

（顺序是有意的：**先停再覆盖**。不停的话正在跑的 `python.exe` 会锁住
`runtime\venv\Scripts\*.dll` 与 site-packages 里的 `.pyd`，robocopy 覆盖到一半失败，
紧接着 `venv --clear` 也会以 `PermissionError` 停住。）

停服务那一步是 `Stop-RunningService`，它做两件事：**有计划任务才去停任务**
（单机用法没有），**但只要有我们那个 `python.exe` 在跑就要等它退**——等满一秒还不退
就强杀。这两件事以前写在一个 `if ($existingTask)` 里，于是单机用法连等都不等，
robocopy 照样撞上被锁住的 DLL（退出码 8），**同一个失败换了个入口**。反过来，
只等不杀也不行：单机模式下没有任何人去叫那个进程停。

`部署说明.txt` 的「再双击一次是升级」那一段因此写了一句**原来的服务不用你先停**——
2026-09-18 一位操作员问的正是这个，而在此之前整份说明书里只有「不动密码 / 不动数据库」，
没有一句回答「我要不要先去点停止」。**那一句是承重的**：它是操作员动手之前唯一会读的地方，
删掉不会让任何东西报错，只会让他白按一次「停止服务.bat」（或者更糟——以为要先卸载）。

**「我们那个 python.exe」现在是一棵树。** venv 里的 `Scripts\python.exe` 不是解释器，
是一个转发壳（CPython 的 `venvlauncher`）：它读 `pyvenv.cfg`、对真正的解释器
`CreateProcessW` 然后等它。所以进程表里是两个，**Windows 不连坐**——只按 `Path`
找到壳、只杀壳，服务活得好好的。`install.ps1` 与 `ops.ps1` 的 `Get-ServiceProcess`
因此都连直接子进程一起收（判据从 `Get-Process` 换成了 `Get-CimInstance Win32_Process`
再回到 `Get-Process` 对象，调用点一行没改）。**没有用 `taskkill /F /T`**：那样会经
`Invoke-Native`，把「原生调用点恰好三处」那条守卫撞坏，而 `taskkill` 找不到进程返回
`128`（在这里是正常情况）还得额外声明成功码。

包解压路径 == 安装目录时跳过复制（否则 robocopy 会把自己拷到自己里）。

**升级沿用上次的用法，不再问。** 日志里会写一行 `沿用上次的用法：单机（…）`。
要换用法先跑 `卸载.bat`（它不碰数据库里的一行），再重新装。

**升级也不动管理员密码。** 那句话里的 `adminPassword` 只存在于全新安装那一支
（`Read-InstallSettings` 的升级分支返回的哈希里没有这个键），第 4 步的
`set_admin_password` 也在 `if ($isUpgrade)` 的 else 里——所以重跑一次既不会要你再输一遍，
也不会把你改过的那个冲掉。它和「沿用上次的用法」「不整份重写 `.env`」是同一条：
**升级只做两件事，更新程序文件 + 跑一次迁移，凡是「你已经配好、正在用」的都不碰。**

但**「不问」不等于「不说」**：操作员读完整个过程会发现自己既没被问、也不知道密码是哪个。
所以三处各写一句（第 1 步、第 4 步、收尾的「密码：这次没有改，还是原来那一个」+ 出路）。
忘了密码的出路是安装目录里的 `重置管理员密码.bat`——**不是重装**。它走
`app.db.set_admin_password` → `auth_service.reset_password`，所以顺带清
`failed_attempts` / `locked_until`；收尾文案因此刻意不写「输错 5 次锁 30 分钟」
（那两个是可配项，写死的数字会静默变错）。

### 文案里的按钮名要和磁盘上的文件名一致（2026-09-18）

收尾那句原本写着「以后要停 / 起 / 备份 / **改**管理员密码，双击安装目录里对应的那几个
`.bat`」，而磁盘上那个文件叫 `重置管理员密码.bat`——**它是整段话里唯一一处告诉操作员
「忘了密码怎么办」的地方**，而按「改管理员密码」去目录里翻，翻不到同一个东西。这是
`CLAUDE.md` 缺口 7 那族（「把「看不懂」换成了「搜不到」」）第三次换皮，这一次发生在
文件系统里。

两条守卫，网眼不一样，都写在 `backend/app/tests/test_windows_assets.py`：

- `test_every_bat_the_scripts_name_is_a_file_that_exists` —— 两个 `.ps1` 里所有
  `xxx.bat` 字面量逐个与 `deploy/windows/ops/*.bat` + 包根的 `一键安装.bat` 求交。
  判据是**后缀匹配**不是相等：有几处是整句话被「」引起来而句子恰好以文件名结尾
  （`「右键以管理员身份运行了启动服务.bat」`），相等判定会把它们全误报。
- `test_the_summary_names_the_admin_password_button_as_it_is_on_disk` —— 逐字钉住
  `重置管理员密码` 在、`改管理员密码` 不在。**上面那条挡不住本次这个 bug**：那句话把
  真名省掉了（「对应的那几个 `.bat`」），`xxx.bat` 的字面量网永远扫不到它。

### 升级会**验一遍** `.env`，不再「一个字都不看」（2026-09-18）

同一天在这台机器上撞的第二次，也是这一层最贵的一次：升级模式此前只从 `.env` 里抠一个
端口号给防火墙用，**其余一个字都不看**。于是一份坏掉的 `.env`（手改错、上次半途失败留下的、
从别处拷来的、早先某版安装器写坏的）会一路走到第 4 步，日志最后是

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
port / Input should be a valid integer, unable to parse string as an integer
[type=int_parsing, input_value='', input_type=str]
    at backend/app/core/config.py:33
ERROR 安装没有完成：依赖检查 失败（退出码 1）
```

`input_value=''` 说的是 `backend\.env` 里那一行 `XLP_PORT=` 是空的。那台机器上一共
三轮排查才定位到它——安装器不说、pydantic 只说字段名不说文件、而**空值不会让
`Settings` 回落到默认值**（pydantic-settings 对「缺这个键」与「这个键是空的」处理不同：
前者用默认值 `8000`，后者直接 `int_parsing` 报错）。所以这一段现在做四件事：

| 做 | 不做 |
|---|---|
| 升级时逐行**验** `.env`（空 / 写了但不能用），坏的**就地在原处**补成正确的值 | 不整份重写——**操作员改过的值一个字都不动**（`XLP_HOST=127.0.0.1` 是他刻意改的，重写它等于把「只有本机能访问」悄悄改回全网段） |
| 数据库连接那一项坏了就**再问一遍**（地址/端口/用户名/口令/库名） | 不再把「请自行编辑这个文件」留给非技术操作员——那份文件的 ACL 只授了 SYSTEM 与 Administrators，他多半改不动 |
| 首次安装**写完立刻读回来自检**，不合格当场以中文停下 | 不把「不认识的键」当坏行删掉——将来加的配置项不该被旧版安装器抹掉 |
| 名单之外、但是空的 `XLP_*` 行**报出行号**（`Get-EmptyUnknownEnvKeys`，只读） | 不替操作员改它：安装器不知道那一项该是什么值，而它可能是下一版才有的开关 |

两条判据都在 `Test-EnvValueOk` 一处（空 / 写了但不能用），值表也只有一份
（`Get-EnvWantedValues`，`Write-EnvFile` 与 `Repair-EnvFile` 共用）——各写一份的话，
「写进去的」与「补出来的」会漂成两种形状，而那种不一致只在一台**真的补过**的机器上才看得见。

`XLP_PORT` 判得比别的严（`int_parsing` + 范围 1–65535），因为它是 `int`；`XLP_DOCS_ENABLED`
是 `bool`；`XLP_DATABASE_URL` 判的是**形状**（`mysql+pymysql://…@…/…` 两段非空）——
`mysql+pymysql://:@:/` 能过「非空」，而它连主机名都没有，那种值一路走到第 4 步报的是
「Access denied」，与「密码打错了」长得一样，而人会去重打密码。

第 4 步的 `config-show` 探针顺手**念出它读的是哪个文件**：

```
沿用现有配置：root@127.0.0.1:3306/xinliceping
配置文件：C:\xinliceping\backend\.env（存在），工作目录：C:\xinliceping\backend
```

那一行是为下一次排查留的：前一轮里没有人知道 pydantic 究竟读的是哪一个 `.env`
（`env_file` 是**相对当前工作目录**的，而且文件不存在时它是**静默**跳过、回落到硬编码默认值）。

对应的守卫在 `test_windows_assets.py` 的「`.env` 那一层」一组里（值表唯一、升级只补不写、
补写排在权限收紧与第 4 步之前、no-op 不写文件、口令与 JWT 密钥不进日志、数据库问题只有
一处出处、空键只报不改），每一条都做过变异验证。

### 严格模式下**读**一个可能不存在的键就抛异常（2026-09-18）

上一层修完之后的**下一次**真机运行，第 3 步刚打完「`.env` 已存在，升级只补坏掉的行」就停：

```
ERROR 安装没有完成：在此对象上找不到属性"dbUser"。请确认该属性存在。
```

升级分支的 `$settings` 只有 `installDir` / `usage` / `port`（数据库那几项**没问过**），
而 `Get-EnvWantedValues` 第一句读的是 `([string]$Settings.dbUser)`。脚本第一行就是
`Set-StrictMode -Version Latest`，它管的正是**读**这一步：读一个不存在的键在**读取那一刻**
抛 `PropertyNotFoundException`，所以**转型 / 拼接 / `?? ''` / 判空全都排在后面，一个都挡不住**。
那行旁边原本还有一段注释说 `[string]` 是「必要的容错」——它防的是下一个异常
（`EscapeDataString` 收到 null），而读取先一步把控制流拿走了。**注释写得越像经过考虑，
这个写法越容易被下一个人照抄。**

只有**配置健康**的机器会撞上：`XLP_DATABASE_URL` 一旦被判不能用，第 3 步会先问一遍数据库，
那几项就都在了。

处置是三条：新增 `Get-SettingText`（判据是「键在不在」→ `ContainsKey`，没有就回 `''`），
数据库五项全走它；**`Repair-EnvFile` 从此不写空值**（它在「补不出来的那一项」上原本会把
`KEY=` 原样写回去，正是这一整套存在的理由，两个写入点各判一次，补缺项那一路还先攒
`$appended` 再决定写不写）；那行误导性注释换成准确的说明。

守卫三条（`test_windows_assets.py`，10/10 变异验证）：每一处 `$settings.<键>` 读取必须落在
「只有首次安装才执行」的块里、且每个键处数钉住；`Get-EnvWantedValues` 五项都走
`Get-SettingText` 且无裸读；`Repair-EnvFile` 两个写入点各有一处空值判断。**第一条判的是文本
位置，不是 PowerShell 语义**（网眼写在它的 docstring 里），所以配了第二条那条机制守卫。

### 收尾时读一个可能为空的列表：`@(...)` 不是可选的（2026-09-18）

`.env` 修完之后的**下一次**真机运行，六步全过、第 6 步健康检查回了 `200`、
「安装完成，服务已经在后台运行」也打出来了——然后最后那段收尾文案崩在

```
ERROR 安装没有完成：在此对象上找不到属性"Count"。请确认该属性存在。
```

一条**已经装好、正在跑**的安装被报成失败，而它给的下一步是「重新双击一次」。

原因是 PowerShell 的值语义：函数里 `return @()` 的那个空数组**在管道里被拆没**，
调用方拿到的是 `$null`；而 `$null.Count` 在 `Set-StrictMode -Version Latest` 下**抛异常**
（正常模式下它是 `0`）。`Get-LanAddresses` 在那台机器上一个私网地址都没匹配到，
于是 `$lanAddresses.Count` 当场炸。两处修正：

- **调用点写 `@(Get-LanAddresses)`**。仓库里早就有这个约定（`ops.ps1` 的
  `@(Get-ServiceProcess)` 三处），只是没人写下来过——现在写进了那个函数的注释里。
- **一个私网地址都没有时，把剩下的非环回地址照样给出来**，而不是什么都不给。
  收尾文案里那句「老师这样访问：」存在的全部意义就是给操作员一条能念给同事的地址；
  真的一条 IPv4 都没有（没连网）时才为空，那时文案把两种原因**分开说**。

**静态自检看不见这个错**，因为它只在「恰好没有那种地址」的机器上发生。挡它的是
`test_windows_assets.py` 的两条文本判据（裸变量上的 `.Count` 必须有 `@(...)`/`::new()`
来源，以及地址列表那两半），两条都做过变异验证。**两者都是「挡住改回去」，不是「证明它
在真机上对」**——真机那一步仍然只能靠人跑一遍。

同一个 bug 还有**第二半**，改的是「后果」而不是「原因」：那一整段收尾说明挂在**外层**
那个 `catch` 底下，而外层那句是「安装没有完成」。所以说明里的任何一次异常都被报成安装
失败——那次就是这样，它不是格式难看，是**那句结论本身是假的**。现在收尾说明有自己的
`try` / `catch`（它排在第 6 步的健康检查之后，那个检查回了 200 就证明服务在跑），
自己的 catch 说的是「收尾说明没能打完」加一句「安装是成功的」，异常原文照样进日志。
外层那句一个字没动：**第 1–6 步里抛出来的仍然报「安装没有完成」**。

### 改完 `install.ps1` / `ops.ps1`，怎么自证没写坏

本机没有 PowerShell，跑不了语法检查；而 `test_windows_assets.py` 里那批断言是**文本匹配**，
看不见块结构——这次漏了一个 `}`（`try` 块没收尾）它们一条都没红。

**别为此写括号配平**。试过：两个都说得通的剥壳器，同一份文件一个报 `195/195`、一个报
`238/237`，互相矛盾（文件里有几行注释带单引号，先剥字符串后剥注释就会错配）。
这与 `function_body` 的 docstring 里那条是同一件事：在 PowerShell 上写半吊子词法器，
错的时候是无声的——而这里会**无故变红**，一条会无故变红的守卫很快会被人关掉。

能用的是**差分**：把 `dist/心晴部署包_V<版本>.zip` 里那一份解出来（它是**上一次真机上跑通过**的
版本），与磁盘上这份 `difflib.unified_diff`，断言 `{` 与 `}` 的**增量相等**、且
「新增行的净 `{` 数 − 删除行的净 `{` 数 = 0」。它不需要懂 PowerShell 的词法，
只要求「形状与那一版相同」。前提是手上有一个跑通过的旧版本——所以它替不了逐字读一遍。

---

## 在 Windows 上验一次（出完包请做这一遍）

自检能验的有限，下面这几步只有真机能走。**两条用法各走一遍**：

### 先验「没有 Python 的那台」

0a. 一台**没装 Python 3.11** 的 Windows：解压 → 双击。应当在**问数据库口令之前**就停下，
    给一句中文、指出去 python.org 装，并列出找过哪些位置、各因为什么被拒。
    这一条是「装到一半才发现」与「一开始就说」的分界。
0b. 一台装了 **3.12**（或 32 位 3.11）的 Windows：同样被挡下，而且文案要能区分
    「没装」（去装）与「版本不对」（装的是别的版本）——这两个诊断的处置完全不同。
0c. `查看状态.bat` 的输出里应当有一行「这套系统依赖的 Python：`C:\…`（还在）」。
    手工把它报的那个 Python 改名（或卸掉）再跑一次 —— 那一行要变成「**不在了**」
    并给出处置。**这是唯一能在故障发生之前看见它的地方。**
0d. 一台 **ARM 的** Windows（先只装 ARM64 版 Python 3.11）：被挡下，而且那句中文要说得出
    「去装 (64-bit) 那个版本」，不是「没找到 Python 3.11」；然后按它说的装上 x64 版
    **重跑一遍，这一档必须消失**——那一半同时证明了 `sysconfig.get_platform()` 没把 x64
    解释器误报成 ARM64（就是上面「x64 那一半管的是 ARM」那一节里那个死循环）。
    **这一条只有 ARM 真机能验**：开发机与 x86 机器都构造不出「模拟层下面那个原生架构」
    这个形状。

### 两条都走

1. 把 `dist/心晴部署包_V<版本>.zip` 拷到一台**干净**的 Windows（或至少换一个安装目录），
   解压，双击 `一键安装.bat`，第 0 问按这一遍要验的那种答。
2. 故意填错一次数据库密码 —— 应当在「准备数据库」那一步停下来，并且**用中文说清**是
   连不上还是密码错，而不是丢一个 traceback。
3. 装完看 `http://127.0.0.1:8000/`，用 admin 登录（会要求改一次密码）。
4. 双击 `查看状态.bat` / `查看日志.bat` / `备份数据.bat`，各看一遍输出对不对
   （备份那个要确认 `backups\` 下真出现了 `.sql`；「查看状态」第一屏应当有一行
   中文的 `用法：单机…` / `用法：局域网…`，而不是只有一串 JSON）。
5. 手工把 MySQL 服务停掉，再重启服务 —— 它应当一直重试而不是退出；
   把 MySQL 启回来，一分钟内应当自己通。

### 再走一遍「数据库我自己准备」（第 4 问答 2）

5b. 拿一个**已经建好、但还没有表**的空库（比如 `CREATE DATABASE xlp_try`），第 3 问填它、
    第 4 问答 2。应当看到：不再问管理员密码 → 第 4 步只有「连接自检」与「迁移」两行 →
    迁移之后**表建好了** → 第 4 步末尾那几行 WARN 说清「基础数据没写进去」。
    然后按《部署说明.txt》那一节的三条命令跑一遍，admin/`123456` 能登录。
5c. 再拿一个**根本不存在**的库名跑一次：应当在第 4 步停下来，报的是中文的
    「连不上数据库 …（`Unknown database`）」加一句「先把这个库建出来」，**不是**一段
    traceback。这一条是选 2 唯一的护栏：那一问的前提就是库已经在了。
5d. 换回选 1 再装一次（干净库）：四件事都要回到原来的样子（建库、迁移、种子、设密码）。
    **这一条比它看起来重要**：选 2 的实现方式是把四件事挪进「选 1」那一支的 `else`，
    挪错了（比如漏了 `else` 而让两支持续执行）在选 2 的机器上完全看不出来。

### 局域网还要走这三步

6. **重启这台机器**，不登录任何账号，等一分钟，再访问那个地址 —— 这是「重启后自动启动」
   唯一真实的验证。顺便确认 `runtime\logs\server.log` 里没有反复重启的痕迹。
7. 从另一台电脑访问那个局域网地址，应当打得开（打不开先查防火墙规则在不在）。
8. 再跑一次 `一键安装.bat` —— 应当认成**升级**、沿用局域网、不再问第 0 问。

### 单机还要走这五步（大多是「反过来的那件事」）

6. **全程不该弹 UAC。** 弹了就是第 0 问答成了 2，或者安装脚本里的用法守卫坏了。
7. `netstat -ano | findstr :8000` 应当显示 **`127.0.0.1:8000`**，不是 `0.0.0.0:8000`。
8. 双击「停止服务.bat」→ 页面打不开；双击「启动服务.bat」→ 又能打开。
   **再连点两次「启动服务.bat」**：第二次应当说「已经在跑了」，日志里**没有**
   `address in use`。（判据是进程 + 端口**两个**：只看进程的话，一个以管理员身份
   起的服务会被判成「没在跑」，于是起出第二个。）
9. **重启这台机器** → 服务**不**自己起来（这是要的行为），双击「启动服务.bat」才起。
10. `icacls backend\.env` → 当前用户要在里面。不在的话 `python.exe` 读不到 `.env`，
    而现象是「服务起得来、但连的是默认的 root:password@localhost」——**不是报错**。
    这一条是这次改动最容易埋进去的坑：`/inheritance:r` 之后 UAC 下普通用户 token 里的
    Administrators 是 deny-only，只授 SYSTEM + Administrators 就等于把装它的人自己锁在外面。
11. ★ **关掉那个黑窗口 → 网页立刻打不开**；双击「启动服务.bat」→ 又能打开。
    这是「用户使用时启动，关机时停止」那句要求的验收，也是这次改动最容易被当成 bug
    报回来的一点（装完屏幕上凭空多了一个窗口）。
12. ★ **验进程树**：服务跑着时到任务管理器里看，应当有**两个** `python.exe`
    （一个在 `runtime\venv\Scripts\`，一个在 base Python 的目录里）。点「停止服务.bat」
    → **两个都消失**，`netstat -ano | findstr :8000` 没有任何监听。
    只死掉了一个，就是 `Get-ServiceProcess` 收树的逻辑被改回去了。
13. **再跑一次 `一键安装.bat`** —— 认成升级、沿用单机用法、`runtime\venv` 被
    `--clear` 重建、服务仍在。**第 11/12 条没修好的话，这一步会以 `PermissionError`
    停在「重建虚拟环境」**（孤儿进程锁着 `.pyd`）。

---

## 这套东西没有做的事

- **选 2 时没有「库是空的」这一层检查。** 安装器在选 2 那条路上只探一次**连接**
  （库在不在、连得上不），不会去问「`system_setting` 里有没有量表」——那要读表，而读表是
  迁移之后的事。于是「选 2 且没灌基础数据」的安装会**在屏幕上写着「安装完成」**：第 6 步
  的健康检查探的是登录页要的那一个端点，空库上它照样答得出来（配置回退默认值，§5）。
  说清这件事的只有第 4 步那几行 WARN，所以那几行是这一维唯一的护栏（守卫断的就是它）。
  要做成真检查，得在迁移之后加一个「量表在不在」的探针——那是另一件事。
- **选 3 的表结构校对只比表名与列名**，类型、可空性、主外键、索引、字符集都不在判据里，
  所以它说「一致」只覆盖到「列都在」。要连着类型一起比得用
  `alembic.autogenerate.compare_metadata`，而 MySQL 的反射噪音（`tinyint(1)` vs `BOOLEAN`、
  server default）会把一份本来能用的表判成不一致——**宁可漏报，不要误杀**：误杀会把一台
  本来装得上的机器拦在第 4 步，而漏掉的那部分 alembic 自己会在下一步撞出来。
- **`ensure_schema` 盖章（`stamp`）之后，迁移仍然会跑**。它盖的是 head，正常情况下迁移
  就是个空转；但如果那份手写 DDL 与 `Base.metadata` 只在「列名这一层」对得上而别处不同
  （比如说 `VARCHAR(50)` 写成了 `VARCHAR(20)`），盖章会让 alembic 从此不再管它——
  这是这一层刻意的取舍，理由与上一条同源。
- **多 ALTER 的迁移会在中途 DDL 提交后失败**（MySQL 的 DDL 不在事务里）：版本号没前进而前面
  几条已经落地，重跑会在第一条上继续撞。`ensure_schema` 在「版本表比 head 旧、而库里已经
  有 head 要的全部列」这一种情形下会多说一句，但它救不回来——那时只能人工核对表结构。
- **没有 TLS。** 局域网用法只给局域网用；单机用法压根不出这台机器。
- **没有「单机 → 局域网」的在线切换**，只能卸载重装。
- **没有多校**（学生导入链路仍硬编码 `School.code == "QH"`，见 `CLAUDE.md` 缺口 2）。
- **备份不自动上传**，`备份数据.bat` 只在本机落一份 `.sql`，并要求人定期拷走。
- **不自动放行杀毒软件**（理由见 §18）。
- **手工启动那两个按钮不能替代安装。** 它们不注册计划任务、不放行防火墙、不设管理员密码，
  也不写 `runtime\build.json`（`usage` / `database_mode` 都在那里）——所以「查看状态.bat」
  对它们开出来的服务一无所知，局域网那种「开机自启」也不会因为跑过它们而出现。它们的作用
  是**把没做完的几步补上、并当场把服务跑起来**（单机那条路上这正是全部所需）；要回到
  「后台常驻」，仍然要重跑一次「一键安装.bat」。
- **局域网用法下 `备份数据.bat` 对普通用户是失效的**：那个动作不在 `$needsAdmin`
  名单里（它不动系统级的东西），可它要读 `backend\.env`，而那个文件只授了 SYSTEM 与
  Administrators。现在的处置是**在错误信息里说清「右键 → 以管理员身份运行」**
  （两句话分开写，另一种原因是文件真被改坏了）。要不要把 `backup` 也收进 `$needsAdmin`
  是个产品问题——那样会让一次只读操作也弹 UAC。
