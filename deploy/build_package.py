"""打一个能拷到 Windows 上、解压、双击就装的包。`make deploy-package`

在 **Mac / Linux 上跑**（这台开发机），产出一个自包含的目录与一个 zip：

    dist/心晴部署包_V1.1.4.zip

**两个名字里都带着版本号**（2026-09-22 起，见下面 `package_dir` / `zip_path` 那一段：
`dist/` 里堆着几个包时，不带版本的那个名字读不出任何东西）。

包里带着 Windows 版的依赖 wheel 与已经构建好的前端，所以目标机上**不需要** pip、
不需要网络。但**目标机上必须已经装好 CPython 3.11（64 位）**——2026-09-18 起包里的
`python/` 目录（一个 21MB 的内嵌 CPython）去掉了，安装脚本改为用**那台机器上的**
Python 3.11 建一个 venv，再从这个包的 `wheels/` 离线装依赖。

**为什么是 3.11、且卡死**：`wheels/` 里 31 个 wheel 有 9 个是
`cp311-cp311-win_amd64`（不带 abi3），换任何别的版本都装不上。所以这一条不是建议，
是硬约束，`install.ps1` 的 `Resolve-BasePython` 会逐个候选去验。

目标机上仍然要有 MySQL 8.0（用户已经装了）。

目录形状**必须与仓库根一致**，一点都不能压平：

    backend/app/db/seed.py:139   parents[3] / "data" / "mht_scale.json"

压平之后那个路径找不到文件，而 `load_scale_questions()` 会**静默**回退成
「MHT题目 001（开发占位…）」——装出一个看起来完全正常、题库却是假的实例，
而且没有任何地方会报错。同一条理由管着 `frontend/dist/` 与 `deploy/`。

这个脚本最后会**自检**（`verify_package`），因为它的产物要在别人手上第一次运行：
`.env` / `.venv` / `node_modules` / `tests` 混进包里、wheel 少了一个、中文文件名在
zip 里没带 UTF-8 标志，这些都是「打的时候看不出来、装的时候才炸」的那一类。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:  # pragma: no cover - 只会在没跑过 make install 的机器上出现
    sys.exit(
        "缺少 packaging 包（它随 pytest 一起装在 backend/.venv 里）。"
        "先在仓库根跑一次 `make install`，或者用 backend/.venv/bin/python 跑这个脚本。"
    )

# 增量 SQL 的生成器与这个脚本**同目录**。直接跑脚本时 Python 会把脚本所在目录放进
# `sys.path[0]`，所以正常路径下这一行本来就能成——**显式插一次是为了有第二条路**：
# 从仓库根 `python deploy/build_package.py` 跑时 `sys.path[0]` 是 `deploy/`（一样），
# 而从别处 `import build_package` 时它两个都不在。两行换掉一整类「换个目录跑就
# ModuleNotFoundError」的困惑，而那个错与「包坏了」看起来一模一样。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_migration_sql  # noqa: E402 —— 必须在上面那行 sys.path 之后

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
DIST = ROOT / "dist"


# --- 产物路径：**带版本号**，而且是两个函数不是两个常量 --------------------------
#
# 2026-09-22 用户要求：「打包时，也请加上版本号以便于区分，比如 心晴部署包_V1.1.4.zip」。
# 起因很实在：`dist/` 里堆着几个不同版本的包时，`心晴部署包.zip` 这个名字读不出任何东西，
# 拷给别人的时候要靠文件时间猜——而拷过去的那一份**收不回来**。
#
# 三件事写在签名里，而不是留给读者去猜：
#
# 1. **它们是函数，不是常量。** 版本号的唯一出处是 `backend/app/version.py`（CLAUDE.md §19），
#    要到运行时才读得到；模块级常量在 import 那一刻就定型了，拿不到它。所以路径由版本派生
#    （`read_app_version()` 在下面，`write_package_info` 也读同一处）。**别把它们改回常量**：
#    改回去的唯一办法是把版本号在常量区再抄一份，那正是 §19 要消掉的东西。
#
# 2. **用 `V{__version__}`（`V1.1.4`），不是 `VERSION_LABEL`（`V1.1`）。** 后者刻意丢掉修订号
#    （§19：`V1.1` 是给人念的，`1.1.3` 是给机器认的），于是 `1.1.2` / `1.1.3` / `1.1.4` 三个包
#    会叫同一个名字——**恰好实现不了这次要的「便于区分」**。`test_app_version.py` 里有一条
#    盯着这个差别。
#
# 3. **zip 里面那层根目录也带版本**（`write_zip` 压的是目录本身）。两次解压到同一个地方时
#    互相覆盖，是操作员做得出的事——而覆盖掉的那一份里可能有他没跑完的安装。
def package_dir(version: str) -> Path:
    """`dist/心晴部署包_V1.1.4/`——出包时铺开的目录。"""
    return DIST / f"心晴部署包_V{version}"


def zip_path(version: str) -> Path:
    """`dist/心晴部署包_V1.1.4.zip`——**交给操作员的那一个**（拷过去的是它）。"""
    return DIST / f"心晴部署包_V{version}.zip"

LOCK_FILE = DEPLOY / "requirements.lock.txt"
WINDOWS_ASSETS = DEPLOY / "windows"

# 目标机的形状：CPython 3.11 / 64 位 Windows。
#
# **这一对常量是同一个事实的两个写法**（"3.11" / "311"），此前分散在三四个地方各写一遍。
# 包里的 9 个 `cp311-cp311-win_amd64` wheel 把它们钉死；安装脚本那边
# （`install.ps1` 的 `Resolve-BasePython`）也按同一对数字去验那台机器上的解释器。
TARGET_PYTHON_VERSION = "3.11"
WHEEL_PLATFORM = "win_amd64"
WHEEL_PYTHON_VERSION = TARGET_PYTHON_VERSION.replace(".", "")

# --- 什么时候这些东西**不许**进包 -------------------------------------------------
#
# 每一条都对应一类真实的坏结果，不是「顺手排除」：
#   .env            里面是这台开发机的数据库口令，拷到客户服务器上既是泄密也是错的配置
#   .venv           是 macOS 的二进制（.so / Mach-O），在 Windows 上 import 就炸
#   tests           部署包里不该有测试：它删掉了「有人在生产库上跑一遍 pytest」这条路
#   node_modules    几百 MB，且前端已经构建好了
#   __pycache__     .pyc 里烤着绝对路径
BACKEND_EXCLUDES = {
    ".env",
    ".venv",
    "venv",
    "tests",
    "__pycache__",
    ".pytest_cache",
    ".coverage",
    "dev.db",
    "htmlcov",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    "xinliceping_backend.egg-info",
    "xinliceping-backend.egg-info",
}
BACKEND_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".egg-info", ".so", ".pyd")

# 包里必须存在的东西。少一个就停在这里，别等装到一半。
REQUIRED_PATHS = [
    "一键安装.bat",
    "部署说明.txt",
    "deploy/install.ps1",
    "deploy/ops.ps1",
    # `sitecustomize.py` 必须**在 deploy\ 里**（install.ps1 从自己的目录拷它，装进
    # venv 的 site-packages 才生效）：它是唯一能修 stdout 编码的地方——安装器起的进程、
    # 用户之后双击「启动服务.bat」起的进程、局域网的 SYSTEM 计划任务，环境全都不由我们
    # 决定，而它是每一次解释器启动都会 import 的那个钩子。
    "deploy/sitecustomize.py",
    "deploy/package-info.txt",
    "deploy/requirements.lock.txt",
    "deploy/ops/启动服务.bat",
    "deploy/ops/备份数据.bat",
    # 手工启动那一套（一键安装装不下去时的出路，见 CLAUDE.md §18）：一个脚本、两枚按钮、
    # 一个只发前端的静态服务器。它们与 `install.ps1` 同级放在 `deploy\` 里，所以装完之后
    # 在安装目录的同一个位置也有一份——那一份正是操作员在安装失败时唯一到得了的东西。
    "deploy/manual-start.ps1",
    "deploy/serve_frontend.py",
    "deploy/手工启动后端.bat",
    "deploy/手工启动前端.bat",
    # 数据库增量升级那一枚按钮（2026-09-20 加）：只动库、不起服务。与手工启动那一套
    # 同一个位置、同一条理由（是**出路**不是步骤，所以不进包根）。
    "deploy/manual-migrate.ps1",
    "deploy/数据库增量升级.bat",
    "backend/app/main.py",
    "backend/run_server.py",
    "backend/alembic.ini",
    "backend/sql/reset_to_baseline.sql",
    # 结构快照（2026-09-18 加）。`数据库和表我自己建好了` 那条分工
    # （`database_mode=schema_prepared`，见 CLAUDE.md §18）里，操作员手上要么有
    # `mysqldump --no-data` 出来的一份，要么什么都没有——这一份让他们**不必先有一台
    # 装好的库**就能把表建出来。它与 reset 那份是一对：那份删行，这份建表。
    "backend/sql/schema_mysql8.sql",
    # 从 V1.0.0 升到当前版本的增量 SQL（2026-09-20 加）。`copy_backend` 拷的是整棵
    # `backend/`，所以它自动跟着进包、落在 `<安装目录>\backend\sql\` 下；
    # `deploy\manual-migrate.ps1` 就是按这个路径去找它的（找不到时它说「多半是程序文件
    # 还是旧版」）。生成器另外还往 `dist/` 写一份——那一份是给直接执行的人拿的。
    #
    # 文件名里的 `v1_0_0` 是 `build_migration_sql.BASELINE_LABEL` 派生的
    # (`OUTPUT_NAME`)。基线换代（比如改成从 V1.1.0 升）时**这里要跟着改**——
    # 忘了改会红在下面那次自检上（「缺这个路径」），而不是静默少带一个文件。
    "backend/sql/upgrade_from_v1_0_0.sql",
    # 「只有系统基础数据与管理员账号」的那份 **DML** 脚本（2026-09-21 加）。与上面
    # `schema_mysql8.sql` 是一对，但方向相反：那份建表、这份写数据（admin + MHT 量表
    # /100 题/评分规则），**一段 DDL 都没有、也不写 `alembic_version`**（那张表归
    # Alembic，见文件头里那一句）。
    #
    # **它不在 `step("3/6")` 里重新生成，这是有意的**（与 `upgrade_from_v1_0_0.sql`
    # 相反）。那一份的两个来源都长在当前源码树上，出包时源码树就是最新的；这一份多了一个
    # **外部来源**——它的数据段必须来自 `seed.py` 的**结果**（真的跑一遍一个一次性库），
    # 而出包时重生成会**盖掉**「有人改了 `seed.py` 却没重跑 `make db-seed-sql`」这个信号。
    # 那个信号应该由 `app/tests/test_seed_sql.py` 红在那次 `make test` 上，不该被一次
    # 静默重生成抹掉。它与 `schema_mysql8.sql` 同一档：**仓库里的快照，靠守卫保鲜。**
    "backend/sql/seed_mysql8.sql",
    "backend/app/db/create_database.py",
    "data/mht_scale.json",
    "frontend/dist/index.html",
    "wheels/",
]

# 包里**不许**存在的东西。
#
# `deploy/task.xml`：计划任务是用 cmdlet 注册的（`New-ScheduledTaskAction` 那一套），
# 没有静态 XML。这一条钉住「它没有悄悄回来」——静态 XML 那一版要求元素顺序与 schema
# 完全一致，写错是运行时才报，而 cmdlet 把那一整类风险消掉了。
#
# `python`：2026-09-18 起不再内嵌 CPython（跑 `--keep` 时上一次留下的 `python/` 会混进
# 包里，而安装脚本已经不认识它了——一个 21MB 的死目录，还会让人以为「包里带了 Python」）。
MUST_NOT_EXIST = ["deploy/task.xml", "python"]

# Windows 侧资产的两条编码约定，见 `verify_windows_asset_encodings`。
# 后缀 -> 该不该有 BOM。
BOM_REQUIRED_SUFFIXES = (".ps1", ".txt")


def log(message: str) -> None:
    print(f"  {message}", flush=True)


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run(command: list[str], cwd: Path) -> None:
    log("$ " + " ".join(command))
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"[X] 命令失败（退出码 {result.returncode}）：{' '.join(command)}")


# --- 1. 前端 ----------------------------------------------------------------


def build_frontend(*, rebuild: bool) -> Path:
    step("1/6 构建前端")
    if not rebuild:
        log("--reuse-frontend：跳过构建，直接用磁盘上的 frontend/dist")
        return ROOT / "frontend" / "dist"

    if not (ROOT / "frontend" / "node_modules").is_dir():
        sys.exit("[X] frontend/node_modules 不在，先跑 `make install`")
    # `npm run build` 是 `vue-tsc -b && vite build`——类型检查顺带也跑了。
    # 前端在 Mac 上构建、到 Windows 上只当静态文件发，所以这一步与目标机无关。
    started = time.time()
    run(["npm", "run", "build"], cwd=ROOT / "frontend")
    log(f"构建耗时 {time.time() - started:.1f}s")
    return ROOT / "frontend" / "dist"


# --- 2. 依赖 wheel ----------------------------------------------------------


def locked_requirements() -> list[str]:
    lines = []
    for raw in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    if not lines:
        sys.exit(f"[X] {LOCK_FILE} 是空的")
    return lines


def download_wheels(target: Path) -> None:
    step("2/6 下载 Windows 依赖")
    target.mkdir(parents=True, exist_ok=True)
    requirements = locked_requirements()

    # `--no-deps` 是**刻意**的：锁文件已经是逐个列全的叶子清单，让 pip 再去解析一遍
    # 只会带来 `--platform` 那套环境标记的麻烦（见 requirements.lock.txt 开头）。
    # 闭环由 verify_wheel_closure 收口。
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--only-binary=:all:",
            "--platform",
            WHEEL_PLATFORM,
            "--python-version",
            WHEEL_PYTHON_VERSION,
            "--implementation",
            "cp",
            "--dest",
            str(target),
        ]
        + requirements,
        cwd=ROOT,
    )
    log(f"{len(list(target.glob('*.whl')))} 个 wheel，{len(requirements)} 条锁定项")


# --- 3. 组装目录 ------------------------------------------------------------


def copy_backend(target: Path) -> None:
    source = ROOT / "backend"

    def skip(directory: str, names: list[str]) -> set[str]:
        dropped = {name for name in names if name in BACKEND_EXCLUDES}
        dropped |= {name for name in names if name.endswith(BACKEND_EXCLUDE_SUFFIXES)}
        return dropped

    shutil.copytree(source, target, ignore=skip, dirs_exist_ok=True)


# `deploy/windows/` 里的东西去哪，**逐条写死**，不按扩展名猜。
#
# 判据只有一条：**装的时候就要双击的那两样进包根，其余进 `deploy\`**。包根那一层的每一
# 个文件名都会出现在他的屏幕上，多一个就多一次「这个是干嘛的」。
#
# 手工启动那两枚按钮**不进包根，是有意的**（它们同样要人双击）：它们是**出路**不是步骤，
# 只在一键安装装不完的时候才用得上，而《部署说明.txt》里写着它们的完整位置
# （`deploy\手工启动后端.bat`）。摆在包根的话，一个还没开始装的人会先看到两个「手工」
# 按钮，而他此刻该点的是「一键安装.bat」——多出来的那两个名字只会让他犹豫。
ROOT_LEVEL_ASSETS = ("一键安装.bat", "部署说明.txt")


def copy_windows_assets(target: Path) -> None:
    """`deploy/windows/` → 包根那几个 + `deploy\\` 一套。

    `ops\\` 那一层**必须单独处理**：它的八个按钮是给装完之后用的，所以留在
    `deploy\\ops\\` 里由 `install.ps1` 在装完时拷到包根，然后**把源目录删掉**
    （见 install.ps1 里那段注释：一个按钮只留一份）。
    """
    scripts = target / "deploy"
    scripts.mkdir(parents=True, exist_ok=True)
    ops = scripts / "ops"
    ops.mkdir(parents=True, exist_ok=True)

    for item in sorted(WINDOWS_ASSETS.iterdir()):
        if item.is_dir():
            if item.name == "ops":
                for button in sorted(item.glob("*.bat")):
                    shutil.copy2(button, ops / button.name)
            continue
        destination = target if item.name in ROOT_LEVEL_ASSETS else scripts
        shutil.copy2(item, destination / item.name)

    shutil.copy2(LOCK_FILE, scripts / LOCK_FILE.name)


# 读 `backend/app/version.py` 的 `__version__` —— 全仓库唯一的出处（§19）。
# 这里是它的第二个读者（第一个是 setuptools 的 `attr:`，第三个是
# `build_migration_sql._version_label`）。**读的是那个文件，不是 `pyproject.toml`**：
# 后者 2026-09-19 起是 `dynamic = ["version"]`，那里已经没有字面量可读了。
#
# **读不到就停，没有兜底值。** 这里从前有一个 `version = "0.0.0"`，读不到时包照出、
# `package-info.txt` 里写着 `0.0.0+20260922`。2026-09-22 起这个值还会进**文件名**
# （`心晴部署包_V0.0.0.zip`）——那正是 §19 那条「没人设过、却看起来像设过」的形状：
# 一个名字与内容对不上的交付物，而它会被人拷过去。
#
# 上面这几行写成 `#` 注释、不是 docstring，是**有意的**：`test_app_version.py` 那条
# 守卫用 `without_comments` 剥掉注释之后再找 `version.py` 这几个字，而 docstring 不是
# 注释、剥不掉——写成 docstring 的话，下面那段真正的读取被删掉之后守卫照样是绿的
# （它命中的是这段说明里的字）。
def read_app_version() -> str:
    source = (ROOT / "backend" / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        sys.exit(
            "[X] 读不到 backend/app/version.py 里的 __version__；"
            "版本号只有一个出处，读不到就别出包（这里没有兜底值——"
            "兜底会造出一个名字与内容对不上的交付物）"
        )
    return match.group(1)


def write_package_info(target: Path, version: str) -> str:
    """写 `deploy/package-info.txt`，返回 `1.1.4+20260922` 那个构建戳。

    `version` 由调用方从 `read_app_version()` 拿进来，**不在这里再读一遍文件**：
    同一次出包里两处各读一次，读到的当然是同一个值，但那一处正则会在某次改动里
    悄悄漂成第二个定义（`test_app_version.py` 有一条盯着「这里不再自己读」）。
    """
    stamp = time.strftime("%Y-%m-%d %H:%M")
    built = f"{version}+{time.strftime('%Y%m%d')}"

    # 纯 ASCII：落盘之后由 install.ps1 读进来写进 runtime\build.json。
    #
    # `python_requires` 是**目标机上要有**什么（不是包里带了什么）——旧的 `python=` 是
    # 那个内嵌解释器的完整版本号，那个东西 2026-09-18 起不存在了。install.ps1 的
    # `Read-PackageInfo` 对它做空值判定，所以旧包（没有这个键）也不会打出半句话。
    (target / "deploy" / "package-info.txt").write_text(
        f"version={built}\n"
        f"built_at={stamp}\n"
        f"python_requires={TARGET_PYTHON_VERSION}\n"
        f"platform={WHEEL_PLATFORM}\n",
        encoding="ascii",
    )
    return built


# --- 5. 自检 ----------------------------------------------------------------


WINDOWS_ENV = {
    "sys_platform": "win32",
    "platform_system": "Windows",
    "platform_machine": "AMD64",
    "platform_release": "10",
    "platform_version": "10.0.19045",
    "os_name": "nt",
    "python_version": TARGET_PYTHON_VERSION,
    "python_full_version": f"{TARGET_PYTHON_VERSION}.0",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    # `extra=""` 让所有 `extra == "..."` 的条件依赖求值为假——那些正是**不该**装的
    # （mypy / psycopg2 / mysqlclient…）。这一条不能省，否则会要求一堆无关的包。
    "extra": "",
}


def verify_wheel_closure(wheels: Path) -> list[str]:
    """把每个 wheel 声明的依赖在 Windows 环境下走一遍，确认它们都在这一批里。

    这是 `--no-deps` 的配套代价：pip 不再替我们检查依赖，所以自己收口。
    它已经捞到过三条真的漏项（greenlet / cffi / pycparser），见 requirements.lock.txt。
    """
    problems: list[str] = []
    paths = sorted(wheels.glob("*.whl"))
    locked: dict[str, Version] = {}
    for path in paths:
        locked[canonicalize_name(path.name.split("-")[0])] = Version(path.name.split("-")[1])

    for path in paths:
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            metadata = archive.read(name).decode("utf-8", "replace")
        for line in metadata.splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            requirement = Requirement(line.split(":", 1)[1].strip())
            if requirement.marker is not None and not requirement.marker.evaluate(WINDOWS_ENV):
                continue
            package = canonicalize_name(requirement.name)
            if package not in locked:
                problems.append(f"{path.name} 需要 {requirement}，锁文件里没有")
            elif requirement.specifier and locked[package] not in requirement.specifier:
                problems.append(f"{path.name} 需要 {requirement}，锁文件里是 {locked[package]}")
    return problems


BOM = b"\xef\xbb\xbf"


def count_boms(raw: bytes) -> tuple[int, int]:
    """返回（开头的 BOM 个数，全文的 U+FEFF 个数）。解不出来时第二个数是 -1。

    **为什么不是 `raw.startswith(BOM)`**：2026-09-18 实测过 `install.ps1` 的开头堆着
    四个 BOM，而前缀式的检查与这条一样都报了绿。「有个 BOM」与「只有一个 BOM」是两件事。
    """
    leading = 0
    while raw[3 * leading : 3 * leading + 3] == BOM:
        leading += 1
    try:
        return leading, raw.decode("utf-8").count("﻿")
    except UnicodeDecodeError:
        return leading, -1


def verify_windows_asset_encodings(source: Path) -> list[str]:
    r"""Windows 侧那几个文件的编码约定。每一条都对应一次真实的静默失败。

    这条守卫是**踩到之后**加的：改一次 `install.ps1`（编辑器按 UTF-8 文本读写）就会
    把 BOM 抹掉，而那个文件在 Mac 上永远不会被执行——它要到一台学校的服务器上才第一次
    被 PowerShell 5.1 读。没有 BOM 时 PS 5.1 按**当前 ANSI 代码页**解它：满屏中文提示
    变成乱码，而且 cp936 下一个中文字符的尾字节可能吞掉紧跟的 ASCII 字符（引号、
    括号），报出来的是一个与编码毫无关系的语法错误，位置还指在别处。

        .ps1   必须有 BOM，且**恰好一个** —— PS 5.1 只有看到 BOM 才认 UTF-8
        .txt   必须有 BOM，且**恰好一个** —— 面向记事本，操作员要读的那份
        .bat   **不许**有 BOM，且每个字节都必须 < 128 —— cmd.exe 按控制台代码页
               逐行解，BOM 会毁掉第一行（`@echo off` 变成一句乱码命令）

    「恰好一个」是 2026-09-18 补的：`install.ps1` 一度有四个，而当时的检查只看前三个
    字节，于是它一路混进了交付包。多出来的那个是**写文件的脚本「读时没剥、写时又补」**
    留下的（见 deploy/README.md 里那一段），所以这条也兼作「有没有人拿脚本改这些文件」
    的报警。**全文除开头那一个以外也不许有 U+FEFF**：夹在正文里的那个可能吞掉紧跟的
    字符，而它在编辑器里与空白长得一模一样。

    扫的是**源目录**而不是产物，因为产物是逐字节拷贝的，而且这样能在跑完
    `npm run build` 之前就报出来（早三分钟知道，别在最后一步才发现）。
    """
    problems: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        raw = path.read_bytes()
        has_bom = raw.startswith(BOM)
        leading, total = count_boms(raw)

        if path.suffix.lower() == ".bat":
            if has_bom:
                problems.append(f"{relative} 有 UTF-8 BOM —— cmd.exe 会把第一行读坏")
            offenders = [
                (number, line)
                for number, line in enumerate(raw.decode("ascii", "replace").splitlines(), 1)
                if any(ord(char) > 127 for char in line)
            ]
            if offenders:
                numbers = ", ".join(str(number) for number, _ in offenders)
                problems.append(f"{relative} 第 {numbers} 行有非 ASCII 字符（要求纯 ASCII）")
        elif path.suffix.lower() in BOM_REQUIRED_SUFFIXES:
            if leading == 0:
                problems.append(
                    f"{relative} 没有 UTF-8 BOM —— PowerShell 5.1 会按 ANSI 代码页读它"
                    "（中文全乱，还可能报一句假的语法错误）"
                )
            elif leading > 1:
                problems.append(
                    f"{relative} 开头有 {leading} 个 UTF-8 BOM —— 只许一个。多出来的那 {leading - 1} 个"
                    "是写文件的脚本「读的时候没剥、写的时候又补」留下的，去查是谁写的它"
                )
            if total > 1:
                problems.append(
                    f"{relative} 全文有 {total} 个 U+FEFF —— 除开头那一个，别处不许有"
                )
    return problems


def verify_lock_file_ascii(path: Path) -> list[str]:
    r"""`requirements.lock.txt` 必须**纯 ASCII**。

    2026-09-18 在一所学校的机器上撞到的：安装停在「安装依赖」，`pip install -r
    ...\requirements.lock.txt` 抛

        UnicodeDecodeError: 'gbk' codec can't decode byte 0x84 in position 16

    而那第 16 个字节，就是第 1 行注释里「部署包里…」那个「的」字的第三个字节。

    原因在 pip 那一侧：`pip/_internal/utils/encoding.py` 的 `auto_decode()` 在文件
    **没有 BOM** 时退回 `data.decode()`，即**当前 locale 的编码**（`pip/_internal/req/
    req_file.py` 的 `get_file_content()` 就是这么调它的）。中文 Windows 上是 GBK，
    于是这个文件里**任何一个**中文字符都会让 pip 在读 requirements 时当场抛异常——
    包括**注释里的**，因为 pip 解的正是整个文件。

    **不改成「给它加 BOM」是有意的。** BOM 只对 pip 这一条读法有效（`BOMS` 表第一个
    就是 UTF-8），而这个文件还要被人用编辑器打开、被出包时的 `pip download` 读、
    将来可能被别的工具读。纯 ASCII 是唯一一种**不需要任何一方「解码得对」**的形状——
    与 `.bat` 那一条同理（那里也是「不许有 BOM，且每个字节 < 128」），理由也是同一个：
    **读它的那台机器不由我们决定。**

    判据是**逐字节 < 128**，不是「用某个 locale 解一次能过」：一个 GBK 双字节序列
    （如 `C4 E3`）在 cp1252 下也是合法的，两边都能解出**各自不同的**乱码，那种形状
    正是这里要排除的。
    """
    raw = path.read_bytes()
    offenders = [
        (number, line)
        for number, line in enumerate(raw.splitlines(), 1)
        if any(byte > 127 for byte in line)
    ]
    if not offenders:
        return []
    numbers = ", ".join(str(number) for number, _ in offenders)
    return [
        f"{path.name} 第 {numbers} 行有非 ASCII 字符 —— pip 会按 locale 编码解它"
        "（中文 Windows 上是 GBK），安装会在「安装依赖」那一步抛 UnicodeDecodeError"
    ]


def verify_no_forbidden_paths(package: Path) -> list[str]:
    """包里不许出现的路径。规则与 BACKEND_EXCLUDES 同源，但这里扫的是**产物**——
    「copy 的时候排除了」与「产物里确实没有」是两件事。"""
    forbidden: list[str] = []
    for path in package.rglob("*"):
        relative = path.relative_to(package)
        parts = relative.parts
        if any(part in BACKEND_EXCLUDES for part in parts):
            forbidden.append(str(relative))
        elif path.is_file() and path.suffix in (".pyc", ".pyo"):
            forbidden.append(str(relative))
        elif path.is_file() and path.name.endswith((".so", ".dylib")):
            # macOS 的二进制：到了 Windows 上 import 就炸，而错误信息是
            # 「不是有效的 Win32 应用程序」，指向的是「包坏了」而不是「打错了平台」。
            forbidden.append(str(relative))
    return forbidden


def verify_scale_data_is_reachable(package: Path) -> list[str]:
    """`seed.py` 是按**相对于源文件的层级**找题库的，所以这里照它的算法走一遍。

    这条正是「不能压平目录」那条约定在打包侧的守卫：不验的话，压平之后装出来的实例
    题库会静默变成 100 道「开发占位」。
    """
    seed = package / "backend" / "app" / "db" / "seed.py"
    probe = seed.resolve().parents[3] / "data" / "mht_scale.json"
    if not probe.is_file():
        return [f"题库按 seed.py 的算法找不到：{probe}"]
    return []


def verify_zip_names(path: Path) -> list[str]:
    """每个条目名 UTF-8 往返一致，且带 UTF-8 标志位。

    Windows 资源管理器读的是第 11 位那个标志（「这个文件名是 UTF-8」）。没有它的话，
    它会拿本机的 OEM 代码页去解，中文文件名变成乱码——而**这个包的操作员要双击的
    正是几个中文名的 .bat**。
    """
    problems: list[str] = []
    with zipfile.ZipFile(path) as zipped:
        for info in zipped.infolist():
            # zipfile 内部本来就存 str；往返一次能发现写入侧的编码退化。
            if info.filename.encode("utf-8").decode("utf-8") != info.filename:
                problems.append(f"文件名往返不一致：{info.filename!r}")
            if not (info.flag_bits & 0x800) and not info.filename.isascii():
                problems.append(f"非 ASCII 文件名没带 UTF-8 标志位：{info.filename!r}")
    return problems


def verify_package(package: Path, wheels: Path) -> None:
    step("5/6 自检")
    problems: list[str] = []

    for relative in REQUIRED_PATHS:
        if not (package / relative).exists():
            problems.append(f"缺少 {relative}")

    for relative in MUST_NOT_EXIST:
        if (package / relative).exists():
            problems.append(f"不该有 {relative}（见 MUST_NOT_EXIST 那里的注释）")

    problems += verify_wheel_closure(wheels)
    problems += verify_no_forbidden_paths(package)
    problems += verify_scale_data_is_reachable(package)
    problems += verify_windows_asset_encodings(WINDOWS_ASSETS)
    # 查的是**产物里的那一份**：pip 读的就是它。与 `.bat` 那组一样，
    # 「copy 的时候是对的」与「产物里是对的」是两件事。
    problems += verify_lock_file_ascii(package / "deploy" / LOCK_FILE.name)

    if problems:
        print()
        for item in problems:
            log(f"[X] {item}")
        sys.exit(f"\n[X] 自检没过（{len(problems)} 条），没有出包")

    log("必需文件齐全，task.xml 与 python/ 都没有混进来")
    log("依赖闭环完整")
    log("没有 .env / .venv / tests / pyc 混进来")
    log("题库在 seed.py 算出来的位置上")
    log(".ps1 与 .txt 恰好一个 BOM，.bat 与 requirements.lock.txt 是纯 ASCII")


def write_zip(package: Path, path: Path) -> None:
    step("6/6 压缩")
    if path.exists():
        path.unlink()
    # 压缩的是**目录本身**，不是它的内容：Windows 上「解压到」会得到一个
    # 心晴部署包_V1.1.4\ 文件夹；压内容的话，一堆文件会直接散在操作员选的那个目录里。
    #
    # 那层目录名本身带版本号，所以两次解压到同一个地方不会互相覆盖（见 package_dir
    # 上面第 3 条）。`install.ps1` 用的是 `%~dp0`，即相对自身——目录叫什么它不关心。
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for item in sorted(package.rglob("*")):
            zipped.write(item, item.relative_to(package.parent).as_posix())

    problems = verify_zip_names(path)
    if problems:
        for item in problems:
            log(f"[X] {item}")
        sys.exit("[X] zip 里的文件名有问题，没有出包")
    log("zip 里的中文文件名都带 UTF-8 标志位")
    log(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024 / 1024:.1f} MB")


def report(package: Path) -> None:
    print("\n=== 包里有什么 ===")
    buckets = [
        ("wheels/", "依赖 wheel"),
        ("frontend/", "前端构建产物"),
        ("backend/", "后端源码"),
        ("deploy/", "安装脚本"),
    ]
    total = 0
    for path in package.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    for prefix, label in buckets:
        size = sum(p.stat().st_size for p in (package / prefix).rglob("*") if p.is_file())
        log(f"{size / 1024 / 1024:7.1f} MB  {label}")
    others = total - sum(
        sum(p.stat().st_size for p in (package / prefix).rglob("*") if p.is_file())
        for prefix, _ in buckets
    )
    log(f"{others / 1024 / 1024:7.1f} MB  其余（.bat 与说明）")
    log(f"{total / 1024 / 1024:7.1f} MB  合计")


def main() -> int:
    parser = argparse.ArgumentParser(description="打出 Windows 一键安装包")
    parser.add_argument(
        "--reuse-frontend",
        action="store_true",
        help="跳过 npm run build，直接用磁盘上的 frontend/dist（只在确认它是新的时用）",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留本次要用的 dist/心晴部署包_V<版本>/ 目录（省下重新下载 wheel 的那几分钟）",
    )
    args = parser.parse_args()

    if not WINDOWS_ASSETS.is_dir():
        sys.exit(f"[X] 找不到 {WINDOWS_ASSETS}")
    if sys.platform == "win32":
        sys.exit("[X] 这个脚本要在开发机（macOS / Linux）上跑，不是在目标机上跑")

    # 版本号**先读**，排在 npm build 之前：读不到就停在这里，而不是让操作员等完
    # 一两分钟的构建再收到一句与版本无关的话。
    #
    # 产物的两个名字都由它派生（`package_dir` / `zip_path`），所以**本组的 `--keep`
    # 语义跟着版本走**：同一个版本重跑仍是同一个目录（原样），版本号升了就是另一个
    # 目录——那一次会把 31 个 wheel 重新下一遍。这是有意的：「省下 wheel」是
    # `--keep` 的用途，而跨版本复用一份 wheel 目录等于假设两个版本的依赖完全一样。
    version = read_app_version()
    pkg_dir = package_dir(version)
    pkg_zip = zip_path(version)

    print("心晴 · Windows 一键安装包")
    print(f"仓库根 {ROOT}")
    print(f"版本   {version}")

    # 编码这件事**先查**：它是唯一一类「在 Mac 上一切都对、到 Windows 上才炸」的错，
    # 而查它只要一毫秒。放到最后（verify_package 里）也查，那次是查产物。
    #
    # `requirements.lock.txt` 归在这里而不是归「Windows 侧资产」是按**读它的那台机器**
    # 分的，不是按它在哪个目录：它没有 BOM 的规矩，而那几个 `.ps1` 是必须有 BOM。
    # 两条规矩相反，所以它单独一查，别把它并进那一个 glob 里。
    early = verify_windows_asset_encodings(WINDOWS_ASSETS)
    early += verify_lock_file_ascii(LOCK_FILE)
    if early:
        print()
        for item in early:
            log(f"[X] {item}")
        sys.exit(f"\n[X] 编码不对（{len(early)} 条），先修这个再出包——这类错在开发机上看不见")
    log("编码正确（.ps1/.txt 恰好一个 BOM，.bat 与 requirements.lock.txt 纯 ASCII）")

    frontend_dist = build_frontend(rebuild=not args.reuse_frontend)

    if pkg_dir.exists() and not args.keep:
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    wheels = pkg_dir / "wheels"
    download_wheels(wheels)

    # 增量 SQL **必须在下面那次整份拷贝之前**生成：它落在 `backend/sql/` 里，而
    # `copy_backend` 是它进包的唯一途径。**次序反过来不会报错**——`build()` 照样写那两个
    # 文件、自检照样过（它只问「这个路径在不在」），而包里那一份是**上一次**生成的。
    # 这是这一节唯一会静默失效的地方，所以这句注释不能删。
    #
    # **每次出包都重生成，不复用仓库里那一份。** 它的两个来源（链上每条迁移的 `PRECHECKS`
    # 常量、`alembic upgrade <基线>:head --sql` 的离线渲染）都长在**当前这棵源码树**上，
    # 所以「仓库里那份」与「这棵树今天渲染出来的那份」是两件事；它们不一样时，出错的地方
    # 是客户手上的库。`app/tests/test_incremental_upgrade_sql.py` 从另一头盯着同一条。
    step("3/6 生成数据库增量 SQL")
    for sql_path in build_migration_sql.build(DIST):
        log(f"{sql_path.relative_to(ROOT)}  ({sql_path.stat().st_size} 字节)")

    step("4/6 复制源码与前端")
    copy_backend(pkg_dir / "backend")
    # `dirs_exist_ok=True` 是给 `--keep` 用的：不加的话，第二次跑（也就是 `--keep`
    # **唯一**的用法——上一次出包失败在半路，想把那 31 个 wheel 省下来）会在这一行
    # 撞 `FileExistsError: ... dist/心晴部署包_V1.1.4/data`，而那个错与「包坏了」
    # 毫无关系。之前一直没被发现，是因为不传 `--keep` 时上面那行 rmtree 已经把目录
    # 删干净了。
    #
    # 代价说清楚：留着旧目录意味着**源里删掉的文件不会从产物里消失**。这正是
    # `MUST_NOT_EXIST`（`python/`、`deploy/task.xml`）存在的理由，新增「删掉某个文件」
    # 的改动时，要出**不带 `--keep`** 的那一次。
    shutil.copytree(ROOT / "data", pkg_dir / "data", dirs_exist_ok=True)
    shutil.copytree(frontend_dist, pkg_dir / "frontend" / "dist", dirs_exist_ok=True)
    copy_windows_assets(pkg_dir)
    built = write_package_info(pkg_dir, version)
    log(f"backend/ data/ frontend/dist/ deploy/ 就位，版本 {built}")

    verify_package(pkg_dir, wheels)
    write_zip(pkg_dir, pkg_zip)
    report(pkg_dir)

    print("\n交付给操作员的是这两个之一：")
    print(f"  {pkg_zip.relative_to(ROOT)}   ← 拷这个过去")
    print(f"  {pkg_dir.relative_to(ROOT)}/     ← 或者整个目录拷过去")
    return 0


if __name__ == "__main__":
    sys.exit(main())
