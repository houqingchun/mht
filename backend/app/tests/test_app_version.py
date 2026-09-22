"""系统版本号唯一出处的守卫。

## 它守的是什么

2026-09-19 之前，仓库里有**四个**版本数字，互不相干：

| 位置 | 值 | 谁在读 |
|---|---|---|
| `backend/pyproject.toml` | `0.1.0` | `build_package.write_package_info` |
| `/openapi.json` 的 `info.version` | `0.1.0` | 没人 |
| `frontend/package.json` | `0.1.0` | 没人 |
| 根 `package.json` | `1.0.0` | 没人 |

前三处写着同一个数**纯属巧合**——`/openapi.json` 那个根本不是谁设的，是 FastAPI 的
内置默认值（`main.py` 的 `FastAPI(...)` 当时没传 `version=`）。这类「没人设过、
却看起来像设过」的值是这里真正的教训：它跟着谁都不走，而屏幕上看着一切正常。

现在唯一出处是 `app/version.py` 的 `__version__`，其余四处从它派生（`frontend/package.json`
除外——npm 没法动态，它是唯一的镜像，所以专门有一条盯着它）。

## 它守不住什么（网眼写明）

- **打包产物的那一行**：`write_package_info` 是否真把 `1.1.2+20260920` 写进了
  `package-info.txt`，要跑 `make deploy-package` 才知道，而那只在开发机上能跑
  （要 `wheels/`）。所以这里只做**静态**检查（读源码，看它读的是哪个文件）。
  真机证据是目标机上 `install.ps1` 第 0 步打出来的那一行版本。
- **`install.ps1` / `ops.ps1` 会不会把版本显示错**：那是 Windows 侧资产，
  `test_windows_assets.py` 的地盘。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.version import VERSION_LABEL, __version__

# 仓库根：`backend/app/tests/` 往上四层。
ROOT = Path(__file__).resolve().parents[3]


def function_body(text: str, name: str) -> str:
    """切出一个**模块级** Python 函数的函数体（从签名行到下一行列 0 的非空行）。

    刻意不做花括号/括号配平，与 `test_windows_assets.py` 里那个同名函数是一个理由：
    半吊子词法器数错的时候是**静默地少切**，而少切的那半截里的东西就全都不受
    下面几条断言约束了，测试照样绿。

    这个文件里的函数体一律缩进，所以「下一行列 0 的非空行」是可靠的边界。
    """
    start = re.search(rf"^def {re.escape(name)}\(", text, re.MULTILINE)
    assert start, f"找不到 def {name}（改名或删掉了？）"
    tail = text[start.start():]
    # 跳过签名行本身，再找第一个列 0 的非空行。
    lines = tail.splitlines()
    for i, line in enumerate(lines[1:], start=1):
        if line and not line[0].isspace():
            return "\n".join(lines[1:i])
    return "\n".join(lines[1:])


def without_comments(text: str) -> str:
    """去掉整行注释与行尾注释。

    非做不可：本文件断言的几处，其注释里正写着反例（`write_package_info` 上面那段
    注释里就有 `pyproject` 这个词，说明**为什么**不读它）。把注释算进去，那条守卫会
    **因为文档写得对而变红** —— 改注释去迁就测试是更糟的方向。
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # 只剥 ` #`（前面有空白）的行尾注释，避免误伤字符串里的 `#`。
        out.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(out)


# --------------------------------------------------------------------------
# 派生：标签
# --------------------------------------------------------------------------


def test_the_version_is_a_three_part_release_number():
    """`VERSION_LABEL` 的规则建立在「主.次.修订」三段之上。

    这条是**自检**：`__version__` 一旦写成 `1.0` 或 `1.0.0rc1` 这种形状，
    下面那条标签断言就会以一个看不懂的方式失败（`V1.0.0rc1` 的前两段是
    `1`、`0`，标签还是 `V1.0`，看着居然对）。先把前提钉住，
    失败信息才指向真正的原因。
    """
    parts = __version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"`__version__` 应当是 `主.次.修订` 三段纯数字，现在是 {__version__!r}；"
        "改形状就要一起改 VERSION_LABEL 的派生规则"
    )


def test_the_label_drops_the_patch_segment():
    """`1.1.2` → `V1.1`：界面显示的是主.次，修订号不外露。

    末尾那个字面量是**跟着 `__version__` 一起改的**，不是第二个出处 ——
    它在这里的作用是让「修订号被丢掉了」这句话变成一条判据：只写上面那一行
    恒等式的话，一个把 `[:2]` 误写成 `[:3]` 的实现照样绿（两边一起变），
    而界面上会冒出一个 `V1.1.2`。
    """
    assert VERSION_LABEL == "V" + ".".join(__version__.split(".")[:2])
    assert VERSION_LABEL == "V1.1", f"标签变成了 {VERSION_LABEL!r}"


# --------------------------------------------------------------------------
# 四个派生点
# --------------------------------------------------------------------------


def test_openapi_version_comes_from_the_single_source(client: TestClient):
    """**本组最重要的一条。**

    它钉住的正是 2026-09-19 之前那个洞：`FastAPI(...)` 不传 `version=` 时，
    `/openapi.json` 报的是 FastAPI 的内置默认值，与仓库里任何一处配置都无关。
    变异验证：摘掉 `main.py` 里的 `version=__version__` → 这条红
    （而此前它报的 `0.1.0` 恰好等于当时 pyproject 里的值，所以**光看数字
    是发现不了的**，只能靠这条断言把两者绑在一起）。
    """
    response = client.get("/openapi.json")
    assert response.status_code == 200, "`/openapi.json` 没开，先看 docs_enabled"
    assert response.json()["info"]["version"] == __version__


def test_pyproject_carries_no_version_literal():
    """pyproject 里不许再有版本字面量 —— 那是第二个出处。

    两个方向都断：`dynamic` 里有 `version`，且 `[project]` 里没有 `version` 键。
    只断前者的话，一个「dynamic 也声明了、字面量也留着」的文件照样绿，
    而 `pip` 在这种冲突下会**直接用字面量**（`dynamic` 被忽略），
    于是 `app/version.py` 改了而装出来的包没改。
    """
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text("utf-8"))
    project = pyproject["project"]
    assert "version" in project.get("dynamic", []), (
        "pyproject 的 version 应当是 dynamic 的（出处是 app/version.py）"
    )
    assert "version" not in project, (
        f"pyproject 里还留着 version = {project['version']!r}；"
        "它会被 pip 优先采用，于是 app/version.py 改了这个不动"
    )
    attr = pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "app.version.__version__", f"dynamic 的 attr 指向了 {attr!r}"


def load_build_package():
    """把出包脚本当模块导进来，好**真的调一次**它那两个算路径的函数。

    与 `test_windows_assets.py` 里那个同名函数同形（两处各写一份：那个文件的地盘是
    Windows 侧资产，这一条属于「版本号只有一个出处」）。顶层 import 全是标准库
    （`packaging` 那段有 try/except 兜底），脚本本体在 `if __name__ == "__main__":`
    之下，所以导入它没有副作用。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "version_guard_build_package", ROOT / "deploy" / "build_package.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_packager_reads_the_single_source():
    """打包器读 `app/version.py`，不读 `pyproject.toml` —— 而且**只读这一处**。

    pyproject 改成 `dynamic` 之后那里**没有字面量可读**；读取处读不到时曾经会静默退回
    一个 `"0.0.0"` 兜底 —— 包照出、版本那一行是假的，且没有任何东西报错。这正是本文件
    要挡的形状（兜底 2026-09-22 去掉了，见下一条）。

    三个判据分两次断（2026-09-22 从一条拆成两条：版本读取抽进了 `read_app_version`，
    而**版本号从这一天起还会进产物文件名**，所以「读的是哪个文件」这一件事有两处要守）：

    - `read_app_version` 是**唯一**读版本的地方，读 `version.py`、不读 pyproject；
    - `write_package_info` 收一个 `version` 参数，**自己不再读文件** —— 同一次出包里
      两处各读一次，那一处正则会在某次改动里悄悄漂成第二个定义。

    两处都过 `without_comments`：它们上面都有 `#` 说明写着「不读 pyproject」这句话本身，
    把注释算进去会让守卫**因为文档写得对而变红**（改注释去迁就测试是更糟的方向）。

    **判据里的文件名一律带引号**（`"version.py"`、`"pyproject`），不是裸子串 ——
    这一点 2026-09-22 写着这条时当场红过一次：**本文件自己就叫
    `test_app_version.py`，它含 `version.py` 这个子串**，而 `without_comments` 只剥
    `#` 注释、**不剥 docstring**。于是任何一段提到「本文件有一条盯着它」的说明都会让
    反向断言在**说明写得越对越红**。加引号之后锚定的是**代码里的那个字面量**，
    与说明文字无关（真正被读的那个文件在代码里写的就是 `"version.py"`）。
    """
    text = (ROOT / "deploy" / "build_package.py").read_text("utf-8")

    reader = without_comments(function_body(text, "read_app_version"))
    assert '"version.py"' in reader, "`read_app_version` 没在读 app/version.py"
    assert '"pyproject' not in reader, (
        "`read_app_version` 又在读 pyproject.toml 了；它已经是 dynamic 的，读不到字面量"
    )

    writer = without_comments(function_body(text, "write_package_info"))
    assert "read_text" not in writer and '"version.py"' not in writer, (
        "`write_package_info` 自己读版本了；它该收一个 `version` 参数 —— "
        "版本只许由 `read_app_version` 读一处"
    )


def test_the_package_artifacts_carry_the_full_version():
    """产物名是 `心晴部署包_V1.1.4.zip`：**完整三段**版本号，不是界面那个 `V1.1`。

    这条钉的是「为什么不用 `VERSION_LABEL`」。那一串刻意丢掉修订号（§19：`V1.1` 是
    给人念的，`1.1.4` 是给机器认的），于是 `1.1.2` / `1.1.3` / `1.1.4` 三个包会叫
    **同一个名字** —— 而用户 2026-09-22 要的正是「便于区分」，拿人念的那一串去命名文件
    恰好做不到这件事。两个名字都得带：zip 与解开的目录（`--keep` 认的是后者）。

    判据是**真调**那两个函数，不是读源码文本：名字是算出来的，文本断言证明不了算得对。
    顺带把「打包器正则读出的版本」与「本文件 import 的那个」绑在一起 —— 两处分岔时，
    产物名会与 `/openapi.json`、界面页脚各说各话。
    """
    module = load_build_package()
    assert module.read_app_version() == __version__, (
        "打包器从 version.py 正则读出来的版本与本文件 import 的不是同一个"
    )

    name = f"心晴部署包_V{__version__}"
    assert module.zip_path(__version__).name == f"{name}.zip", (
        f"zip 名是 {module.zip_path(__version__).name!r}；"
        "用了 VERSION_LABEL 的话它会是 心晴部署包_V1.1.zip —— 丢掉修订号就区分不出补丁版"
    )
    assert module.package_dir(__version__).name == name, (
        f"解开的目录名是 {module.package_dir(__version__).name!r}"
    )


def test_the_frontend_package_version_mirrors_the_backend():
    """`frontend/package.json` 是**唯一**允许存在的副本。

    npm 的 `version` 字段没法动态（它必须是字面量），所以这一份必然存在；
    这条断言就是它存在的代价 —— 版本对不上时立刻红，而不是等到某天有人
    拿 npm 里那个数当系统的版本。

    根 `package.json`（e2e harness 的元数据）**不在**这里：它不是产品版本，
    也没有任何东西读它。给它加断言只会让版本升级多一处无关的编辑。
    """
    data = json.loads((ROOT / "frontend" / "package.json").read_text("utf-8"))
    assert data["version"] == __version__, (
        f"frontend/package.json 是 {data['version']!r}，"
        f"app/version.py 是 {__version__!r}；改版本要两处一起改"
    )


# --------------------------------------------------------------------------
# 界面拿到的那一份
# --------------------------------------------------------------------------


def test_branding_exposes_the_display_label(client: TestClient):
    """`/public/branding` 发的是 `V1.1`（给人念的），不是 `1.1.2`（规范的）。

    这一格是「账号与权限」页脚读的那一个，所以它必须在**免认证**端点里 ——
    与登录页拿校名走的是同一个端点，界面因此不需要新开一次取数。

    顺带断言原有三项还在：这一处是 `| {"version": ...}` 合进去的，
    写成覆盖整个 dict 就会把校名那三项挤掉，而那三项目前**只有
    `test_serve_frontend.py` 按可达性探过**（它不看内容）。
    """
    response = client.get("/api/v1/public/branding")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["version"] == VERSION_LABEL
    for key in ("school_name", "brand_name", "brand_subtitle"):
        assert key in data, f"branding 少了 {key} —— 合成字典时把原有几项挤掉了"
