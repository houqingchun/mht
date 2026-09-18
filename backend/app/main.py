import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError, app_error_handler, error_response
from app.version import __version__

# URL 路径里**出现即拒**的字符。两类：
#   `:`   NTFS 备用数据流（`x.txt::$DATA`）与盘符（`C:\…`）。
#   控制字符（含 NUL）——`open()` 遇到 NUL 抛的是 ValueError，那会变成 500 而不是 404。
# 这两类都不是穿越，但都没有理由出现在一个静态文件的路径里。
_REJECT_IN_PATH = (":",)


def _resolve_within(root: Path, full_path: str) -> Path | None:
    """把 URL 路径映射到 `root` 下的一个真实文件；越界或不存在时返回 `None`。

    **这一步绝不可以用 `Path.resolve()` / `os.path.realpath()`。** 两者在 Windows 上
    会对用户给的路径**真的做 I/O**（`nt._getfinalpathname` 得先把文件打开）。而
    `\\\\attacker.com\\share` 这种 UNC 路径在拼接时是**绝对路径**，会把左边的 `root`
    整个丢掉（`ntpath.join` 的规矩），于是「拼路径」就成了一次对外发起的 SMB 连接：
    服务端以 SYSTEM 跑着，泄漏的是它的 NTLM 哈希，而请求照样回 404、日志里什么都不像。
    （同一机制的库级版本是 Starlette `StaticFiles` 的 GHSA-wqp7-x3pw-xc5r；锁文件里
    starlette 是 1.6.0，那个已经修了。**但下面是手写的一份，得自己守。**）

    所以这里的每一步都是纯字符串运算，先挡再拼，全程不碰文件系统：

    - 含 `:` 或控制字符 → 拒；
    - 以分隔符开头 → 拒。这一条是**平台无关**的同一件事：UNC（`\\\\srv\\share`）与
      绝对路径（`\\windows\\x`、`/etc/hosts`）在 Windows 上会让 `ntpath.join` 把左边的
      `root` 整个丢掉——那是真的跨主机访问；而在 POSIX 上同一个输入只是折进 `root`
      里的一个怪文件名，于是它**照样走到 `is_file()`**。同一个输入两种行为，
      只在 Windows 上守等于没守，所以规则写成「前端那 22 条路由没有一条以斜杠开头」。
    - `os.path.abspath` 只是 `normpath(join(cwd, p))`，**不做 I/O**，用它把
      `a/../../b` 这类折起来；
    - `os.path.commonpath` 比对前缀。UNC 与盘符路径会让它抛 `ValueError`
      （两者不同类，没有公共前缀可算），一并当越界处理。

    最后才 `is_file()`——到这一步路径已经在 `root` 里了，一次 stat 无害。
    """
    if not full_path:
        return None
    if any(bad in full_path for bad in _REJECT_IN_PATH):
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in full_path):
        return None
    if full_path[0] in ("/", "\\"):
        return None

    # 反斜杠也当分隔符：`%5C` 解出来就是它，而 Windows 上它与 `/` 等价。
    segments = full_path.replace("\\", "/").split("/")
    candidate = Path(os.path.abspath(os.path.join(str(root), *segments)))
    try:
        if os.path.commonpath([str(root), str(candidate)]) != str(root):
            return None
    except ValueError:  # 不同盘符 / UNC —— 没有公共前缀，必然是越界
        return None

    return candidate if candidate.is_file() else None


def _mount_web(app: FastAPI, settings: Settings) -> None:
    """把前端构建产物挂到同一个端口上（CLAUDE.md §18）。

    前端 `API_BASE` 是相对路径 `/api/v1`，`createWebHistory()` 又没带 base，所以
    生产环境**必须同源**：要么一个进程同时发 API 与页面，要么在中间放一个 nginx
    （`frontend/nginx.conf` 是那条路，Docker 用的就是它）。这台服务器装不了 Docker，
    于是走前者。

    `web_dir` 为空时**什么都不做**——开发时前端在 5173，测试与 e2e 也用不上它，
    所以这个函数默认的效果等于它不存在。
    """
    if not settings.web_dir:
        return

    web_root = Path(settings.web_dir).resolve()
    index = web_root / "index.html"
    if not index.is_file():
        # 配了目录却没有 index.html：那是**配置错了**，不是「没配」。静默跳过会让人
        # 以为前端已经托管上了，而访问根路径拿到一个 404，排查方向完全反了。
        raise RuntimeError(f"XLP_WEB_DIR 指向的目录里没有 index.html：{web_root}")

    assets = web_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa_fallback(full_path: str):
        # 1. `/api` 树下的东西一律不许落到这里。catch-all 会把一个**打错的接口路径**
        #    变成 200 + 一整页 HTML，而 `services/api.ts` 会拿它当响应体去解 JSON，
        #    报出来的错是「Unexpected token '<'」——离真正的原因最远的那种提示。
        #    守卫取整个 `/api` 前缀而不是 `settings.api_prefix`：把 `/api/v1` 打成
        #    `/api/v2` 一样致命，而 22 条前端路由没有一条以 `/api` 打头。
        if full_path == "api" or full_path.startswith("api/"):
            raise AppError("NOT_FOUND", "接口不存在", 404)

        # 2. 前端没有 favicon，回 index.html 只是给日志添噪声。
        if full_path == "favicon.ico":
            raise AppError("NOT_FOUND", "资源不存在", 404)

        # 3. 命中真实文件就发文件（`/assets` 已单独挂载，这里管其余静态文件）。
        #    路径参数是**百分号解码之后**才交到这里的，所以 `%2e%2e%2f` 也走这条判断。
        candidate = _resolve_within(web_root, full_path)
        if candidate is not None:
            return FileResponse(candidate)

        # 4. 其余一律回 index.html：深链与 F5 全靠这一条。FastAPI 自己的 `/docs`、
        #    `/redoc`、`/openapi.json` 注册得更早，走不到这里。
        return FileResponse(index)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        # 不传的话 `/openapi.json` 的 `info.version` 是 FastAPI 的内置默认值
        # `0.1.0` —— 它此前与 `pyproject.toml` 里那个 `0.1.0` 撞号纯属巧合，
        # 改 pyproject 不会带动它。现在两个都来自 `app/version.py`。
        version=__version__,
        # 关掉时三个一起关：只关 `/docs` 而留着 `/openapi.json` 等于没关，那份
        # schema 就是全部内容。见 `Settings.docs_enabled`。
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AppError, app_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_, exc: RequestValidationError):
        _ = exc
        return error_response("VALIDATION_ERROR", "请求参数校验失败", 422)

    app.include_router(api_router, prefix=settings.api_prefix)
    # 必须排在 include_router 之后：那个 catch-all 匹配一切，注册在前面会把
    # `/api/v1/**` 整个吞掉。
    _mount_web(app, settings)
    return app


app = create_app()
