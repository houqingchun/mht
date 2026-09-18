"""把 `frontend/dist` 发出去，并把 `/api/**` 反代给后端。手工启动前端时用它。

**它存在的唯一理由**：这套包里的前端是一份**构建产物**（`frontend/dist`），而
`frontend/src/services/api.ts` 的 `API_BASE = '/api/v1'` 是**相对路径**。相对路径意味着
「页面在哪个源上，接口就必须在哪个源上」，于是「前端单独一个进程」在目标机上只有两种
成立方式：后端自己把 dist 发了（`XLP_WEB_DIR`，单端口那条路），或者中间放一个**会反代
`/api` 的**服务器。用 `python -m http.server` 单独起前端是最坏的一种——页面打得开，
而每一次登录都死在 `Unexpected token '<'`：静态服务器把 `/api/v1/...` 回成 404 HTML，
`api.ts` 拿它去解 JSON。CLAUDE.md §18「前端必须同源」说的就是这条。

另外两条路各有各的前提：`npm run dev` 要目标机上有 Node，`frontend/nginx.conf` 要目标机
上有 nginx。这一份**只用标准库**，跑在安装时建出来的那个 venv 里——那是这台机器上唯一
确定存在的 Python。

四条约定：

1. **`/api/**` 一律反代，不落到静态文件上。** 后端那侧 `/api` 是接口前缀，而前端 22 条
   路由没有一条以 `/api` 打头（与 `app/main.py` 的 catch-all 同一条理由）。
2. **未知路径回 `index.html`，但只在它不像一个文件时。** 前端用 history 路由
   （`createWebHistory()`），`/counselor/cases/1` 在磁盘上并不存在，不回 index.html 的话
   用户按 F5 就是 404。反过来，一个**缺失的** `assets/xxx.js` 绝不能回 index.html：
   浏览器会把那段 HTML 当模块解，报出来是一句与原因无关的语法错误。判据是最后一段有没有
   `.`（vite 也按这条分）。
3. **后端的原话原样带出来**，包括状态码与 `Content-Disposition`。受控导出的 CSV 靠后者
   起文件名，把它吃掉的话下载会变成一个叫 `download` 的东西。4xx/5xx 同理——统一封装里
   那句 `error.message` 本来就是写给用户看的（CLAUDE.md §2）。
4. **反代不走任何代理。** 目标永远是本机；`ProxyHandler({})` 把读环境变量那一路整个关掉，
   否则这台机器上恰好设了 `HTTP_PROXY` 时，`127.0.0.1` 会被送出去绕一圈（通常是超时）。

刻意**不做**的事：WebSocket / SSE（这套系统的接口全是「请求—应答」）、TLS（见
`部署说明.txt` 里那条「只给局域网用」）、压缩（vite 产物已经是压过的静态文件）。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX_FILE = "index.html"
API_PREFIX = "/api"
# 受控导出是同步生成的（`export_service` 在请求里现拼 CSV），给得宽一点。
UPSTREAM_TIMEOUT_SECONDS = 120.0

# 逐跳首部（RFC 9110 §7.6.1）：只属于这一跳，转给下一跳是错的。`content-length` 也在
# 这里，但它不是逐跳——它必须**按新的 body 重算**，所以不能被原样转发。
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# 回给浏览器的响应首部**白名单**（小写比较）。刻意不是「全转发再删几个」：白名单漏掉
# 一个的后果是「某个功能静默少一点东西」，而黑名单漏掉一个的后果是「把一个只属于这一跳的
# 首部转出去」（连接错乱）。漏掉 `content-disposition` 的后果最具体：导出的 CSV 没有
# 文件名。`content-type` 不在这里——它由 `_send` 单独写一次，列进来的话会发两遍。
FORWARD_RESPONSE_HEADERS = ("content-disposition", "cache-control")

# **不查 `mimetypes` 的第一顺位。** Windows 上 `mimetypes` 会去读注册表，而注册表里
# `.js` 被某些软件写成 `text/plain` 是出了名的——那会让浏览器**拒绝执行**整个前端
# （`<script type="module">` 要求 JavaScript 的 MIME 类型），报出来只是一句
# 「Failed to load module script」。所以这几类写死，其余才交给 mimetypes。
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}

# 见模块注释第 4 条。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def resolve_static(root: Path, url_path: str) -> Path | None:
    """把一个 URL 路径映射成 `root` 下的文件。`None` = 不该由静态文件回应（回 404）。

    找不到、越权、像文件但没有——三种都给 `None`：越权的那种**不回声也不区分**，
    一个 403 只会告诉扫端口的人「这个路径有意思」。
    """
    root = root.resolve()
    decoded = urllib.parse.unquote(url_path)
    # 先剥掉前导斜杠再拼接：`root / '/etc/passwd'` 会因为右边是绝对路径而**整个丢掉
    # root**，那正是越权的形状。
    candidate = (root / decoded.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if candidate.is_file():
        return candidate
    if candidate.name and "." in candidate.name:
        # 像文件（`assets/index-abc123.js`）却没有 → 真的没有，别回 index.html。
        return None
    index = root / INDEX_FILE
    return index if index.is_file() else None


def content_type_for(path: Path) -> str:
    known = CONTENT_TYPES.get(path.suffix.lower())
    if known:
        return known
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


class FrontendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "xinliceping-frontend"

    # --- 路由 -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 —— BaseHTTPRequestHandler 的命名约定
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == API_PREFIX or path.startswith(API_PREFIX + "/"):
            self._proxy()
        else:
            self._serve_static(path)

    # --- 静态 -----------------------------------------------------------------

    def _serve_static(self, path: str) -> None:
        target = resolve_static(self.server.root, path)  # type: ignore[attr-defined]
        if target is None:
            self._send(404, "没有这个文件。\n".encode(), "text/plain; charset=utf-8")
            return
        # 前端产物是按内容哈希命名的（`index-abc123.js`），可以放心让浏览器存着；
        # `index.html` 例外——它是那张「哪个哈希才是当前的」的清单，存住就换不掉新版了。
        cache = "no-store" if target.name == INDEX_FILE else "public, max-age=3600"
        self._send(
            200,
            target.read_bytes(),
            content_type_for(target),
            extra={"Cache-Control": cache},
        )

    # --- 反代 -----------------------------------------------------------------

    def _proxy(self) -> None:
        body = self._read_body()
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP
        }
        request = urllib.request.Request(
            self.server.upstream + self.path,  # type: ignore[attr-defined]
            data=body or None,
            headers=headers,
            method=self.command,
        )
        try:
            with _OPENER.open(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
                self._relay(response.status, response.read(), response.headers)
        except urllib.error.HTTPError as error:
            # 4xx/5xx **也是后端的原话**：`urllib` 把非 2xx 抛成异常，而异常上带的仍是
            # 一个完整响应。改写它等于替后端重新解释一遍（§2 那条「服务端答了话」）。
            self._relay(error.code, error.read(), error.headers)
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as error:
            self._send_upstream_unavailable(error)

    def _relay(self, status: int, payload: bytes, upstream_headers) -> None:
        content_type = upstream_headers.get("Content-Type") or "application/octet-stream"
        # 按**上游自己的写法**转出去（`Content-Disposition` 而不是 `content-disposition`）：
        # 首部名大小写不敏感，但一个全小写的响应首部在抓包与日志里读起来像个 bug，而
        # 找它的下一个人会先怀疑这一层。
        extra = {
            name: value
            for name, value in upstream_headers.items()
            if name.lower() in FORWARD_RESPONSE_HEADERS
        }
        self._send(status, payload, content_type, extra=extra)

    def _send_upstream_unavailable(self, error: Exception) -> None:
        """后端没起来时回一句人话。

        走统一封装的形状（`core/errors.py` 的 `error_response`），所以前端 `api.ts` 会
        把 `error.message` 原样显示出来——比一句 `Failed to fetch`（或一个 502 的英文
        页面）有用：它同时说了「谁的错」与「该去看哪个窗口」。
        """
        message = (
            f"连不上后端服务（{self.server.upstream}）：{error}。"  # type: ignore[attr-defined]
            "这个窗口只发页面，接口要另一个窗口在跑——先双击「手工启动后端.bat」。"
        )
        payload = json.dumps(
            {
                "success": False,
                "data": None,
                "request_id": None,
                "error": {"code": "UPSTREAM_UNAVAILABLE", "message": message},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self._send(502, payload, "application/json; charset=utf-8")

    # --- 收发 -----------------------------------------------------------------

    def _read_body(self) -> bytes:
        raw = self.headers.get("Content-Length")
        if not raw:
            return b""
        try:
            length = int(raw)
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra: dict[str, str] | None = None,
    ) -> None:
        # `Content-Length` **必须**每次都写：协议是 HTTP/1.1，少了它浏览器会一直等
        # 连接关闭，页面看起来就是「加载不完」。
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 —— 父类的签名
        # 父类那一条会打远程地址与时间，一页前端能有三十行。这里只留「谁被请求了、
        # 结果如何」——排查「哪个请求没通」时够用，而它同时是**直通控制台**的
        # （不走重定向，所以中文与编码问题都不存在）。
        sys.stdout.write(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")
        sys.stdout.flush()


class FrontendServer(ThreadingHTTPServer):
    """一个把这个前端发出去的 HTTP 服务器。

    `ThreadingHTTPServer`：一次同步的导出请求能跑几十秒，单线程的话那期间页面上的
    其它资源全都排队——用户看到的是一个「卡住」的界面。
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], root: Path, upstream: str):
        super().__init__(address, FrontendHandler)
        self.root = Path(root).resolve()
        self.upstream = upstream.rstrip("/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python deploy/serve_frontend.py",
        description="发 frontend/dist，并把 /api 反代给后端。",
    )
    parser.add_argument("--root", required=True, help="前端构建产物目录（frontend/dist）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只给本机")
    parser.add_argument("--port", type=int, default=5173, help="监听端口，默认 5173")
    parser.add_argument(
        "--api",
        default="http://127.0.0.1:8000",
        help="后端地址，默认 http://127.0.0.1:8000",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    root = Path(args.root)
    if not (root / INDEX_FILE).is_file():
        print(f"[X] {root} 里没有 {INDEX_FILE}——那不是一份前端构建产物。")
        print("    包里那一份在 <安装目录>\\frontend\\dist。")
        return 1

    server = FrontendServer((args.host, args.port), root, args.api)
    host, port = server.server_address[0], server.server_address[1]
    print(f"[OK] 页面：http://{host}:{port}/")
    print(f"     接口：{args.api}（/api/** 全部转给它）")
    print("     这个窗口就是前端服务本身，关掉它就是停它。Ctrl+C 也可以。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] 停了。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
