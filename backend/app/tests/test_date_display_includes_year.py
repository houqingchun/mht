"""日期的年份不许省 —— §5.28 那个缺陷的静态守卫。

## 缺陷的原形

`frontend/src/services/dates.ts` 的 `formatDateTime` 此前是

```ts
return value.slice(5, 16).replace('T', ' ')      // → "09-25 09:00"
```

理由写在当时的注释里：「这一页要显示的时间都是同一学年内的事，年份不承载信息」。
**那个前提不成立**：这套系统按整个初中三年运行，同一屏上会出现今年与去年的记录
（历次趋势、审计轨迹、关注档案时间线都是这样），`09-25 09:00` 读不出是哪一年的 9 月
（用户 2026-09-28 的原话：「跨度可能是3年（学生整个初中），所以如果只显示9月17日，
无法知道是哪一年的9月17日」）。

**它当时是四份，不是一个。** 三个页面各自抄了一份同样的 `slice(5, 16)`
（`ExportCenterPage` / `SessionListDialog` / `AdminBackupPage`），所以「补年份」这件事
在它们身上是**三个独立的改动点**——而漏掉任何一个，那一页就继续印没有年份的时间，
且看不出来（它看起来只是「一个正常的日期」）。现在四处收敛到 `dates.ts` 一处。

## 这个文件守什么（两半，各挡一种再犯法）

| 判据 | 挡的是 |
|---|---|
| `formatDateTime` 的返回值必须从含年份的那一段切出来 | 有人把 `slice(0, 16)` 改回 `slice(5, 16)`（改一处、全站退化） |
| `frontend/src` 下**再长出**一份 `slice(5, 16)` 的内联副本 | 「第三份/第五份副本」——第二半比第一半更值钱，因为缺陷当年就是这么活下来的 |

## 它守不住什么（网眼写明）

- **视图有没有调用那三个函数**。后端看不见 `.vue` 的渲染结果——一个页面若内联写了
  `{{ row.created_at }}`（裸 ISO 串，**年份本来就在**），或者自己拼了一串别的日期，
  这里一条都扫不到。那一半归 `e2e/vocabulary.spec.ts` 的
  `datedWithoutYear`（扫 `innerText` 的**像素**，与 `leakedCodes` 同一个手法）。
- **判据是文本不是语义**。它读的是源码字符串，不认识 TypeScript。所以它挡的是
  「有人改回去」，不是「这个函数在任何输入下都对」——后者只有 e2e 的像素能证。
- **`Date` 对象那一族**（`formatLocalMoment`）：它本来就是浏览器本地的「此刻」，
  年份由 `getFullYear()` 拼出来，这里只静态确认它没有走 `toISOString()`
  （那会给 UTC，UTC+8 的晚上显示成前一天）。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"
DATES_TS = FRONTEND_SRC / "services" / "dates.ts"

# 缺陷的原形。写成常量是为了让下面那条 `len(seen) >= N` 的空转自检有个名字，
# 也让「为什么是这个形状」集中在一处。
TRUNCATED_DATETIME = re.compile(r"slice\(\s*5\s*,\s*16\s*\)")


def _function_body(source: str, name: str) -> str:
    """取一个导出函数的函数体（从 `export function <name>` 到下一个顶层 `export`）。"""
    start = source.index(f"export function {name}(")
    rest = source[start:]
    nxt = rest.find("\nexport ", 1)
    return rest if nxt == -1 else rest[:nxt]


def test_format_datetime_keeps_the_year():
    """`formatDateTime` 必须切到含年份的那一段。

    `slice(0, 16)` 与 `slice(5, 16)` 只差一个数字，而它们相差**整整一个年份**——
    这正是这个缺陷当初没被任何东西发现的原因：两种写法都产出「一个正常的日期」。
    """
    body = _function_body(DATES_TS.read_text(encoding="utf-8"), "formatDateTime")
    assert "slice(0, 16)" in body, f"formatDateTime 不再从含年份的位置切：{body!r}"
    assert not TRUNCATED_DATETIME.search(body), (
        "formatDateTime 变回了 slice(5, 16)：年份被切掉了（§5.28 的原形）"
    )


def test_no_page_re_introduces_an_inline_truncated_copy():
    """全站不许再长出一份内联副本。

    这一条是**更值钱的那一半**：缺陷当年之所以存活，不是因为没人想过要改，
    而是因为它是四份——改一份不动另外三份，而屏幕上看起来完全正常。
    收敛到 `dates.ts` 之后，新增页面时唯一能写出的形状是「调用那三个函数」或
    「内联切片」；后者在这里变红。
    """
    seen: dict[str, list[int]] = {}
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".ts", ".vue"} or path == DATES_TS:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if TRUNCATED_DATETIME.search(line):
                seen.setdefault(str(path.relative_to(FRONTEND_SRC)), []).append(lineno)
    assert seen == {}, (
        "这些地方又自己抄了一份 `slice(5, 16)`（日期会缺年份，且看起来很正常）："
        f"{seen}。改用 `services/dates.ts` 的 `formatDateTime` / `formatDate`。"
    )

    # 空转自检：正则坏掉时上面那个循环一次都不进，测试照样绿。扫描面本身要有下限
    # ——`frontend/src` 下 .ts/.vue 文件远多于 30 个，量不出来就是路径写错了。
    scanned = [
        p for p in FRONTEND_SRC.rglob("*")
        if p.suffix in {".ts", ".vue"} and p != DATES_TS
    ]
    assert len(scanned) >= 30, f"只扫到了 {len(scanned)} 个文件，扫描面可能写错了"


def test_format_date_keeps_the_year():
    """`formatDate` 同理：它一直是 `slice(0, 10)`，这一条把它钉住。

    与 `formatDateTime` 分开写，是因为两者的粒度判据不同（`dates.ts` 那张表有**四行**：
    这一列承载了几分信息，由写入方决定）。合在一条里会给一个「两个函数共用一个
    实现」的改法留下空间，而那会让四行判据退化成一行。
    """
    body = _function_body(DATES_TS.read_text(encoding="utf-8"), "formatDate")
    assert "slice(0, 10)" in body, f"formatDate 不再从含年份的位置切：{body!r}"


def test_the_local_moment_is_built_from_local_reads():
    """`formatLocalMoment` 不许走 `toISOString()`。

    它处理的是浏览器本地的 `Date` 对象（用户刚点了导出那一下），必须用
    `getFullYear()` 一类的**本地**读数去拼。拿 `toISOString()` 会给 UTC——
    UTC+8 的晚上 08:30 会显示成前一天的 00:30，**年份也可能跟着差一位**
    （跨年夜），而那正是「日期显示不完整/不对」这一类投诉最难复现的一种。
    """
    body = _function_body(DATES_TS.read_text(encoding="utf-8"), "formatLocalMoment")
    assert "toISOString" not in body, "formatLocalMoment 用了 toISOString()：那是 UTC，会差一天"
    assert "getFullYear" in body or "toISODate" in body, (
        "formatLocalMoment 没有从本地读数拼年份"
    )
