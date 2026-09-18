"""一个端口同时发 API 与前端页面（CLAUDE.md §18）。

生产的部署形状是「一个进程、一个端口」：服务器的页面与接口同源，所以
`frontend/nginx.conf` 那套语义要在 Python 里重来一遍。这个文件钉住它。

**这里每一条都做过变异验证**（把对应的守卫摘掉/改掉，确认这条会红）：
`/api/**` 那道判断摘掉 → `test_api_paths_stay_json_404` 拿到 200 与一页 HTML；
`commonpath` 那道比对摘掉 → 穿越那条拿到 secret.txt；`web_dir` 为空时的提前返回摘掉
（改成无条件挂载）→ `test_no_web_dir_keeps_the_old_behaviour` 红；
`_REJECT_IN_PATH` 与那段控制字符判断逐个摘掉 →「碰都不碰文件系统」那条红；
`docs_enabled` 那三个 `if` 变常量 → 关文档那条红。
"""

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import _resolve_within, create_app

INDEX_MARK = "<div id=app>心晴</div>"
INDEX_HTML = f"<!doctype html><html><body>{INDEX_MARK}</body></html>"


def make_dist(root: Path) -> Path:
    """造一个够用的假 dist：`index.html` 加一个带哈希名的 assets 文件。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "index-abc123.js").write_text("console.log('xlp')", encoding="utf-8")
    return root


@pytest.fixture()
def dist(tmp_path: Path) -> Path:
    return make_dist(tmp_path / "frontend" / "dist")


@pytest.fixture()
def spa(dist: Path, monkeypatch) -> TestClient:
    """`web_dir` 非空的 app。**不接管数据库**：这几条都不碰库。"""
    settings = Settings(_env_file=None, web_dir=str(dist))
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    return TestClient(create_app())


# --- 前端那一半 -------------------------------------------------------------


def test_root_serves_the_spa_index(spa: TestClient):
    response = spa.get("/")
    assert response.status_code == 200
    assert INDEX_MARK in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_deep_links_fall_back_to_the_index(spa: TestClient):
    """`createWebHistory()` 的深链与 F5 全靠这一条。

    没有任何前端路由会在服务端注册，所以 `/counselor/cases/12` 到了这里就是
    「文件不存在」——不落回 index.html 的话，用户一刷新就 404。
    """
    for path in ["/counselor/cases/12", "/admin/organization", "/login", "/some/deep/unknown"]:
        response = spa.get(path)
        assert response.status_code == 200, path
        assert INDEX_MARK in response.text, path


def test_assets_are_served_as_real_files(spa: TestClient):
    """`/assets` 走 `StaticFiles`，不回 index.html。

    回 index.html 的后果是浏览器拿一段 HTML 当 JavaScript 去执行，控制台报的是
    语法错误，而真正的问题（文件名对不上）得顺着网络面板才找得到。
    """
    response = spa.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.text == "console.log('xlp')"


def test_favicon_is_a_plain_404(spa: TestClient):
    """前端没有 favicon，回 index.html 只是给日志添噪声。"""
    assert spa.get("/favicon.ico").status_code == 404


# --- API 那一半 -------------------------------------------------------------


def test_api_paths_stay_json_404(spa: TestClient):
    """**这条是这次改动里最要紧的一条。**

    catch-all 会把一个打错的接口路径变成 `200 + 一整页 HTML`，而
    `services/api.ts` 会拿它当响应体去解 JSON，抛出来的是
    「Unexpected token '<'」——离真正的原因（路径写错了）最远的那种提示。
    所以 `/api` 树下必须继续是统一封装的 JSON 404。
    """
    for path in ["/api/v1/nope", "/api/v2/nope", "/api/nope", "/api"]:
        response = spa.get(path)
        assert response.status_code == 404, path
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_FOUND"
        assert INDEX_MARK not in response.text, path


def test_the_real_api_is_not_shadowed(spa: TestClient):
    """catch-all 注册在 `include_router` 之后，`/api/v1/health` 还得照常。

    把它注册到前面去的话，这条会拿到一页 HTML。
    """
    response = spa.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_fastapi_own_docs_are_not_shadowed(spa: TestClient):
    """`/docs` 与 `/openapi.json` 由 FastAPI 自己更早注册。

    它们要的是「没被吞掉」而不是某个具体状态码：`/docs` 要 200，而
    `/openapi.json` 在托管模式下仍然是 200（schema 由应用自己发）。
    """
    assert spa.get("/docs").status_code == 200
    assert spa.get("/openapi.json").status_code == 200


# --- 边界 -------------------------------------------------------------------


def test_traversal_cannot_escape_the_web_dir(spa: TestClient, dist: Path):
    """`..` 挡在 `resolve()` + `is_relative_to` 上。

    路径参数是**百分号解码之后**才交到路由里的，所以 `%2e%2e%2f` 这种写法一样
    走这条判断——写 `/../secret.txt` 测不到它（httpx 在发出去之前就把 `..` 归一化掉了，
    请求根本到不了这里）。
    """
    secret = dist.parent / "secret.txt"
    secret.write_text("不该被发出去", encoding="utf-8")

    # 走 catch-all 的那几条：回 index.html 是对的——在**前端路由**的意义上
    # 它们就是「没有这个页面」，只要不把 secret.txt 发出去就行。
    for path in ["/%2e%2e/secret.txt", "/..%2fsecret.txt", "/a%2f%2e%2e%2f%2e%2e%2fsecret.txt"]:
        response = spa.get(path)
        assert "不该被发出去" not in response.text, path
        assert INDEX_MARK in response.text, path

    # 走 `/assets` 挂载的那一条是**另一个**守卫：`StaticFiles` 自己挡下来并发 404，
    # 所以这里不会落到 index.html。两种答复都可以，只有「发出去了」不可以。
    mounted = spa.get("/assets/%2e%2e%2f%2e%2e/secret.txt")
    assert "不该被发出去" not in mounted.text
    assert mounted.status_code == 404


def test_no_web_dir_keeps_the_old_behaviour(monkeypatch):
    """`web_dir` 为空 = 不托管前端，也就是开发与测试原来那个样子。

    这一条守的是「这次改动没有动到既有行为」：vite 的 5173 与 `make e2e` 都靠它。
    """
    settings = Settings(_env_file=None)
    assert settings.web_dir == ""
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    bare = TestClient(create_app())

    assert bare.get("/").status_code == 404
    assert bare.get("/counselor/cases/12").status_code == 404
    # 接口照样在。
    assert bare.get("/api/v1/health").status_code == 200


def test_a_configured_dir_without_index_html_fails_loudly(tmp_path: Path, monkeypatch):
    """配了目录却没有 `index.html` 是**配置错了**，不是「没配」。

    静默跳过会让人以为前端已经托管上了，而访问根路径拿到一个 404，排查方向完全反了。
    """
    empty = tmp_path / "not-a-dist"
    empty.mkdir()
    settings = Settings(_env_file=None, web_dir=str(empty))
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="index.html"):
        create_app()


# --- 路径映射那几个「先挡再拼」的守卫 -----------------------------------------


def test_dangerous_paths_never_reach_the_filesystem(tmp_path: Path, monkeypatch):
    """危险路径必须在**碰文件系统之前**就被拒掉。

    这一条测的**不是**「回答是 404」——那是结果，而这里要钉的是过程。理由是
    Windows 上「拼路径」与「解析路径」本身就会做 I/O：`\\\\attacker.com\\share`
    在 `ntpath.join` 里是绝对路径，会把左边的 root 整个吃掉，于是拼接这一步就成了一次
    对外发起的 SMB 连接；服务端以 SYSTEM 跑着，泄漏的是它的 NTLM 哈希，而请求照样
    回 404、日志里什么都不像。（库里的同一个坑是 Starlette `StaticFiles` 的
    GHSA-wqp7-x3pw-xc5r，锁文件里的 1.6.0 已修；手写的这一份只能自己守。）

    **判据是「一次 stat 都没有发生过」**，这样它在 macOS/Linux 上也成立：那些平台上
    `\\\\attacker.com\\share` 只是一个名字里带反斜杠的普通文件，拼出来的路径确实在
    root 里、`is_file()` 也确实是 False——只看返回值的话，把守卫整个删掉测试照样绿。
    数 stat 次数才分得出「挡在前面」与「放过去之后恰好没命中」。
    """
    root = (tmp_path / "dist").resolve()
    root.mkdir()
    (root / "real.txt").write_text("可以发", encoding="utf-8")

    calls: list[str] = []
    real_stat = os.stat

    def counting_stat(path, *args, **kwargs):
        calls.append(str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", counting_stat)

    dangerous = [
        "\\\\attacker.com\\share\\x",   # UNC —— 拼接时会吃掉 root
        "//attacker.com/share/x",       # 同上，正斜杠写法
        "\\windows\\win.ini",           # 以反斜杠开头 = 绝对路径
        "/etc/hosts",                   # 以正斜杠开头（理论上到不了这里，仍然要挡）
        "C:\\windows\\win.ini",         # 盘符
        "x.txt::$DATA",                 # NTFS 备用数据流
        "a\0b",                         # NUL —— is_file() 会抛 ValueError（=500，不是 404）
        "..\\..\\secret.txt",           # 反斜杠写的穿越
        "a/../../secret.txt",           # 正斜杠写的穿越
    ]
    for path in dangerous:
        calls.clear()
        assert _resolve_within(root, path) is None, path
        assert calls == [], f"{path!r} 碰到了文件系统：{calls}"

    # 反过来：正常路径**要**去 stat 一次，否则上面那条「一次都没 stat」是白拿的
    # （把函数改成恒返回 None，上面全绿、这里红）。
    calls.clear()
    assert _resolve_within(root, "real.txt") == root / "real.txt"
    assert calls != [], "正常文件路径没有去 stat，说明这个函数已经不发文件了"

    # 目录在 root 里，但不是文件 —— 交给 catch-all 回 index.html。
    calls.clear()
    assert _resolve_within(root, "assets") is None


def test_web_dir_must_be_a_directory_of_index(tmp_path: Path):
    """合法的深链与带子目录的资源都映射得对（上一条只测了「拒掉什么」）。"""
    root = (tmp_path / "dist").resolve()
    (root / "assets" / "js").mkdir(parents=True)
    (root / "assets" / "js" / "index-abc.js").write_text("x", encoding="utf-8")

    assert _resolve_within(root, "assets/js/index-abc.js") == root / "assets" / "js" / "index-abc.js"
    assert _resolve_within(root, "nope.js") is None
    assert _resolve_within(root, "") is None


# --- `/docs` 开不开 ----------------------------------------------------------


def test_docs_are_open_by_default(monkeypatch):
    """默认开着：开发机上要看 schema，`make e2e` 也不受影响。

    这一条与下面那条是一对——只写「关掉之后是 404」的话，把三个 `docs_url=None`
    焊死（永远关）也能通过，而那时开发机上 `/docs` 就没了。
    """
    settings = Settings(_env_file=None)
    assert settings.docs_enabled is True
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    client = TestClient(create_app())

    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_docs_can_be_closed_for_a_real_deployment(monkeypatch):
    """学校里那台机器上要能关掉——那一整个网段的人都拉得到这份 schema。

    三个一起关：只关 `/docs` 而留着 `/openapi.json` 等于没关，那份 schema 就是全部
    内容。安装脚本写 `XLP_DOCS_ENABLED=0`。
    """
    settings = Settings(_env_file=None, docs_enabled=False)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    client = TestClient(create_app())

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    # 接口本身一个字都不受影响。
    assert client.get("/api/v1/health").status_code == 200


@pytest.mark.skipif(sys.platform == "win32", reason="这个形状只在 posix 上算越界")
def test_commonpath_is_the_actual_guard(tmp_path: Path):
    """`commonpath` 那次比对本就是最后一道——把它摘掉，穿越就成立了。

    上面那些「不碰文件系统」的用例挡的是另一类输入（UNC/盘符/ADS），二者不重叠：
    这一条走的是**拼出来仍然在 root 里**的正常形状，所以前面几道守卫一个都不拦它，
    只能靠 commonpath 比出来。
    """
    root = (tmp_path / "dist").resolve()
    root.mkdir()
    outside = (tmp_path / "secret.txt").resolve()
    outside.write_text("不该被发出去", encoding="utf-8")

    # `a/../../secret.txt` 折起来是 `tmp_path/secret.txt`，在 root 之外。
    assert _resolve_within(root, "a/../../secret.txt") is None
    # 同一件事的合法性对照：`a/b/../c.txt` 折起来仍在 root 里，要发。
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "c.txt").write_text("可以发", encoding="utf-8")
    assert _resolve_within(root, "a/b/../c.txt") == root / "a" / "c.txt"
