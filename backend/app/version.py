"""产品版本号 —— 全仓库**唯一**的出处。

改版本只改这一个文件。其余四处都从它派生（或被一条守卫盯着），仓库里不应该再有
第二个版本字面量：

| 谁 | 怎么拿到 |
|---|---|
| `pyproject.toml` | `dynamic = ["version"]` + `[tool.setuptools.dynamic] attr` |
| `deploy/build_package.py` | 正则读这个文件 → 拼成 `1.1.2+YYYYMMDD` 写进 `package-info.txt`，并派生产物名 `心晴部署包_V<版本>.zip`（用**完整三段**版本号，不是下面那个 `VERSION_LABEL`——理由见 `package_dir` 上面那一段） |
| `app/main.py` | `FastAPI(version=__version__)` → `/openapi.json` 的 `info.version` |
| `/api/v1/public/branding` | `VERSION_LABEL`，给界面那一行 |
| `frontend/package.json` | npm 没法动态，所以它是**唯一的镜像**，由 `test_app_version.py` 盯着 |

**这个文件里不许有任何 import。** setuptools 的 `attr:` 会在建包时静态读它，
一个 import 就能让 `make install` 在一个与版本毫无关系的地方失败。
"""

__version__ = "1.1.4"

# 界面上显示的那一串。「V1.1」＝ 主.次，丢掉修订号：`1.1.3` 显示 `V1.1`，
# 那正是补丁号的意思（对外是同一条发布线），而 `1.1.3+20260922` 那个构建戳负责
# 区分同一条发布线上的不同次出包。
#
# 由 `__version__` 派生而不是各写一份，是为了让「改版本只改一处」这句话成立。
VERSION_LABEL = "V" + ".".join(__version__.split(".")[:2])
