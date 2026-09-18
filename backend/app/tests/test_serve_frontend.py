"""把守 `deploy/windows/serve_frontend.py`（手工启动前端时用的那个小服务器）。

那个文件在**开发机上一次都不会被执行**——它要在一台装 Windows 的学校机器上，由安装时
建出来的 venv 跑起来。所以它的每一条约定都需要机器判据：

1. **越不出 `frontend/dist`。** 它是这条路上唯一能把一个 URL 变成磁盘路径的地方，
   而它的输入来自网络。
2. **后端说的话原样转出去**，状态码与 `Content-Disposition` 都算。受控导出的 CSV 靠
   后者起文件名；把 4xx/5xx 改写成 502 会让前端拿到一句不是后端说的话（§2 那条
   「服务端答了话」的分支当场失灵）。
3. **后端没起来时给一句人能看的话**，而且**页面照样打得开**——那个窗口只发页面。
4. **缺失的 `assets/*.js` 不许回 `index.html`**：浏览器会把那段 HTML 当模块解，报出来
   是一句与原因无关的语法错误。

下面这组用例起的是**真的套接字**（`127.0.0.1:0`）与一个桩上游，不打真接口、不碰数据库。
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WINDOWS_ASSETS = Path(__file__).resolve().parents[3] / "deploy" / "windows"
SERVE_FRONTEND = WINDOWS_ASSETS / "serve_frontend.py"

INDEX_HTML = "<!doctype html><div id=app>前端入口</div>"
APP_JS = "export const answer = 42;\n"
CSV_BODY = "姓名,关注等级\n林同学,重点关注\n"
BOOM_BODY = json.dumps(
    {"success": False, "data": None, "request_id": "req_x", "error": {"code": "BOOM", "message": "后端自己说的话"}},
    ensure_ascii=False,
).encode("utf-8")


def _load_module():
    spec = importlib.util.spec_from_file_location("serve_frontend_under_test", SERVE_FRONTEND)
    assert spec is not None and spec.loader is not None, SERVE_FRONTEND
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve_frontend = _load_module()

# 客户端刻意不走代理：这一组请求的目标永远是 127.0.0.1，而 `urlopen` 默认会读
# `HTTP_PROXY` 之类的环境变量，那会让用例在「恰好设了代理」的机器上变成超时。
CLIENT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --- 桩上游 ------------------------------------------------------------------


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        self._respond()

    def do_POST(self):  # noqa: N802
        self._respond()

    def _respond(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/api/v1/echo":
            payload = json.dumps(
                {
                    "method": self.command,
                    "body": body.decode("utf-8"),
                    "authorization": self.headers.get("Authorization") or "",
                    "content_type": self.headers.get("Content-Type") or "",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self._write(200, payload, "application/json; charset=utf-8", {})
        elif path == "/api/v1/download":
            self._write(
                200,
                CSV_BODY.encode("utf-8"),
                "text/csv; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="key-attention.csv"'},
            )
        elif path == "/api/v1/boom":
            self._write(500, BOOM_BODY, "application/json; charset=utf-8", {})
        else:
            self._write(404, b'{"success": false}', "application/json; charset=utf-8", {})

    def _write(self, status: int, payload: bytes, content_type: str, extra: dict[str, str]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in extra.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # noqa: A002 —— 桩不需要日志
        return


class _StubUpstream(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextlib.contextmanager
def frontend(dist: Path, upstream: str):
    server = serve_frontend.FrontendServer(("127.0.0.1", 0), dist, upstream)
    with serving(server):
        yield f"http://127.0.0.1:{server.server_address[1]}"


@contextlib.contextmanager
def upstream():
    stub = _StubUpstream()
    with serving(stub):
        yield stub.url


# --- 夹具 --------------------------------------------------------------------


def make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")
    return dist


def get(url: str, *, headers: dict[str, str] | None = None, method: str = "GET", data: bytes | None = None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with CLIENT.open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


# --- 1. 先证明有东西可扫 ------------------------------------------------------


def test_the_file_tree_is_actually_there():
    assert SERVE_FRONTEND.is_file(), SERVE_FRONTEND
    assert callable(serve_frontend.main)
    assert callable(serve_frontend.resolve_static)
    assert serve_frontend.FrontendServer is not None


# --- 2. URL → 磁盘路径 --------------------------------------------------------


def test_static_paths_resolve_under_the_root(tmp_path):
    dist = make_dist(tmp_path)

    assert serve_frontend.resolve_static(dist, "/") == dist / "index.html"
    assert serve_frontend.resolve_static(dist, "/assets/app.js") == dist / "assets" / "app.js"
    # history 路由：磁盘上没有这一层，但它是一条**页面**路径，回 index.html。
    assert serve_frontend.resolve_static(dist, "/counselor/cases/1") == dist / "index.html"


def test_a_path_that_looks_like_a_file_is_never_answered_with_index_html(tmp_path):
    """缺失的 `assets/*.js` 回 HTML 会让浏览器报一句与原因无关的语法错误。

    判据是「最后一段有没有 `.`」。变异验证：把这一支删掉（直接回 index.html），
    这条会红。
    """
    dist = make_dist(tmp_path)

    assert serve_frontend.resolve_static(dist, "/assets/missing-abc123.js") is None
    assert serve_frontend.resolve_static(dist, "/favicon.ico") is None
    # 目录（不带扩展名）仍然走 index.html——那一层是前端路由。
    assert serve_frontend.resolve_static(dist, "/counselor/") == dist / "index.html"


def test_it_cannot_escape_the_dist_directory(tmp_path):
    """输入来自网络，而这是这条路上唯一把 URL 变成磁盘路径的地方。

    两种形状都试：明文 `..`，以及**百分号编码过的** `..`（不先解一次的话，第二种会
    原样落到磁盘上——`urllib.parse.unquote` 必须排在解析之前）。
    """
    dist = make_dist(tmp_path)
    secret = tmp_path / "backend" / ".env"
    secret.parent.mkdir(parents=True)
    secret.write_text("XLP_DATABASE_URL=mysql+pymysql://root:secret@127.0.0.1:3306/x\n", encoding="utf-8")

    assert serve_frontend.resolve_static(dist, "/../backend/.env") is None
    assert serve_frontend.resolve_static(dist, "/%2e%2e/backend/.env") is None
    assert serve_frontend.resolve_static(dist, "/assets/../../backend/.env") is None


def test_javascript_is_served_with_a_javascript_type():
    """`mimetypes` 在 Windows 上读注册表，而 `.js` 被写成 `text/plain` 是出了名的——
    那会让浏览器拒绝执行整个前端。所以这几类写死，不查注册表。"""
    assert serve_frontend.content_type_for(Path("index-abc.js")).startswith("text/javascript")
    assert serve_frontend.content_type_for(Path("index.html")).startswith("text/html")
    assert serve_frontend.content_type_for(Path("style-abc.css")).startswith("text/css")


# --- 3. 静态那一半真的发得出去 ------------------------------------------------


def test_it_serves_the_build(tmp_path):
    dist = make_dist(tmp_path)
    with upstream() as api, frontend(dist, api) as url:
        status, headers, body = get(url + "/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert body.decode("utf-8") == INDEX_HTML
        # `index.html` 是「哪个哈希才是当前的」那张清单，存住就换不掉新版了。
        assert headers["Cache-Control"] == "no-store"

        status, headers, body = get(url + "/assets/app.js")
        assert status == 200
        assert headers["Content-Type"].startswith("text/javascript")
        assert body.decode("utf-8") == APP_JS
        assert "max-age" in headers["Cache-Control"]

        status, _, body = get(url + "/counselor/cases/1")
        assert status == 200
        assert body.decode("utf-8") == INDEX_HTML

        status, _, body = get(url + "/assets/missing-abc.js")
        assert status == 404
        assert b"<div id=app>" not in body

        # HEAD 不许带 body，但 Content-Length 要在（HTTP/1.1 下少了两者之一都会挂）。
        status, headers, body = get(url + "/", method="HEAD")
        assert status == 200
        assert body == b""
        assert int(headers["Content-Length"]) == len(INDEX_HTML.encode("utf-8"))


# --- 4. /api 那一半：原话原样转出去 -------------------------------------------


def test_the_api_is_proxied_with_method_body_and_headers(tmp_path):
    dist = make_dist(tmp_path)
    payload = json.dumps({"account": "13800000001", "password": "123456"}).encode("utf-8")
    with upstream() as api, frontend(dist, api) as url:
        status, headers, body = get(
            url + "/api/v1/echo",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer token-123"},
        )

        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        seen = json.loads(body.decode("utf-8"))
        assert seen["method"] == "POST"
        assert seen["body"] == payload.decode("utf-8")
        assert seen["authorization"] == "Bearer token-123", "登录凭据必须在转发的路上"
        assert seen["content_type"] == "application/json"


def test_the_backend_answer_is_forwarded_verbatim(tmp_path):
    """**5xx 也是后端说的话。** 把它改写成 502 会让前端拿到一句不是后端说的错误，
    而统一封装里那句 `error.message` 本来就是写给用户看的（CLAUDE.md §2）。

    `urllib` 把非 2xx 抛成异常，所以这一条同时钉住「`HTTPError` 那一支也必须转发」。
    """
    dist = make_dist(tmp_path)
    with upstream() as api, frontend(dist, api) as url:
        status, _, body = get(url + "/api/v1/boom")

        assert status == 500, "后端自己答的 500 被换成了别的状态码"
        assert body == BOOM_BODY, "后端的原话被改写了"


def test_a_download_keeps_its_filename(tmp_path):
    """受控导出的 CSV 靠 `Content-Disposition` 起文件名，白名单漏了它就只剩 `download`。"""
    dist = make_dist(tmp_path)
    with upstream() as api, frontend(dist, api) as url:
        status, headers, body = get(url + "/api/v1/download")

        assert status == 200
        assert headers["Content-Disposition"] == 'attachment; filename="key-attention.csv"'
        assert headers["Content-Type"].startswith("text/csv")
        assert body.decode("utf-8") == CSV_BODY


# --- 5. 后端没起来时 ----------------------------------------------------------


def test_a_dead_backend_still_serves_the_page_and_says_what_is_wrong(tmp_path):
    """这个窗口**只发页面**，所以后端没起来时页面照样要能打开——用户看到的是一个正常的
    登录页，加上一句「接口连不上」。

    没有后端时 `api.ts` 拿到的是什么，决定他会不会去重打密码：一个 502 的英文页面会
    变成 `Unexpected token '<'`，而这里的 JSON 封装会被原样显示成一句话。
    """
    dist = make_dist(tmp_path)
    # 端口 1 上不会有服务，连它是立刻被拒的（不是超时）。
    with frontend(dist, "http://127.0.0.1:1") as url:
        status, _, body = get(url + "/")
        assert status == 200, "后端没起来时，页面本身仍然要打得开"

        status, headers, body = get(url + "/api/v1/public/branding")
        assert status == 502
        assert headers["Content-Type"].startswith("application/json")
        envelope = json.loads(body.decode("utf-8"))
        assert envelope["success"] is False
        assert envelope["error"]["code"] == "UPSTREAM_UNAVAILABLE"
        assert "手工启动后端" in envelope["error"]["message"], (
            "那句话要告诉用户该去哪个窗口，而不是只报一句连不上"
        )


# --- 6. 命令行那一层 ----------------------------------------------------------


def test_it_refuses_a_directory_that_is_not_a_build(tmp_path, capsys):
    """指错了目录（比如指到 `frontend/` 而不是 `frontend/dist/`）要说清是哪一种错，
    而不是起一个「每个页面都 404」的服务器。"""
    assert serve_frontend.main(["--root", str(tmp_path), "--port", "0"]) == 1
    assert "index.html" in capsys.readouterr().out
