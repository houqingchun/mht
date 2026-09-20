"""上传文件的解码：一处定义，三条导入链共用。

中文 Excel / WPS 的「另存为 CSV」在 Windows 上写出来的是 **GBK**，而那是学校最可能的
操作路径——我们发给他们的模板是 UTF-8 带 BOM（`frontend/src/services/csv.ts` 的
`'\\ufeff'`），在 Excel 里编辑完再「另存为 CSV」，这一步就把它变成了 GBK。

只按 UTF-8 解会抛 `UnicodeDecodeError`，而那个异常不是 `AppError`，于是它落进 Starlette
的默认 500：响应体是一段**纯文本** `Internal Server Error`。前端拿它去 `response.json()`
抛出的是一句浏览器原话（`JSON.parse: unexpected character at line 1 column 1 …`），
**屏幕上的那句话一个字都没提到编码**——离真正的原因最远的那种提示。

第一次修的是测评记录导入（2026-09-16，见「测试注意」那一节）；名册导入与题库导入是同一个
洞的第二、三处，2026-09-20 一起收进这里。三条链共用这一个判据，是因为「哪些编码算读得懂」
是一个问题，各写一份必然有先漂的那一份。
"""

import csv
import io
import json
from typing import Any

from app.core.errors import AppError

# 按次序试。**次序是有意义的**：一份能按 UTF-8 解出来的文件必须按 UTF-8 解——GBK 也能
# 「解开」很多 UTF-8 字节（`学号` 的 `E5 AD A6 E5` 落在 GBK 的双字节区间里），只是解出来
# 是乱码，所以 GBK 排在后面当回退，反过来会把好文件读坏，而且读坏了不报错。
UPLOAD_ENCODINGS = ("utf-8-sig", "gbk")

# Excel 的「另存为 → Unicode 文本」写的是 UTF-16 LE 带 BOM；少了这一档，那样一份文件会
# 撞上下面那句「不是 UTF-8 或 GBK」——文件明明是文本，话却是错的。
#
# **但它不能作为回退项出现在上面那个循环里，这一条 2026-09-20 由一条测试撞出来。**
# Python 的 `utf-16` 解码器**在没有 BOM 时不报错**——它按本机字节序（小端）解下去，只要
# 字节数是偶数就「成功」，解出来是一串乱码。于是 `UPLOAD_ENCODINGS` 里放进 `utf-16` 之后，
# 一份 GBK 文件**字节数为偶数**时会被它抢走：`utf-8-sig` 失败 → `utf-16` 成功 → 拿到的
# 是乱码，而 `decode_upload` 报的是「成功」。这比它换掉的那个 500 更难查——500 至少说了
# 「出事了」，这个只是安静地把整份名册读成谁也不认识的字。
#
# 所以判据改成**先看 BOM**：只有 `FF FE` / `FE FF` 打头才走 UTF-16（GBK 文件不可能以
# `FF` 打头，`FF` 不是合法的 GBK 前导字节，所以这一支抢不走任何 GBK 文件）。
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

# 两种失败说两句话：说错方向的话比不说更坏——「另存为 CSV」对一份内容不合法的 CSV
# 毫无用处，而「表格读不出来」对一份编码不对的文件同样指错了地方。
DECODE_FAILED_MESSAGE = "文件不是 UTF-8 或 GBK 编码的文本，请另存为 CSV 后重试"
UNREADABLE_TABLE_MESSAGE = "文件里的表格读不出来，请用 Excel 打开后另存为 CSV 再上传"


def decode_upload(content: bytes) -> str:
    """把上传的字节解成文本；几种编码都解不出来才报错（真的二进制文件）。"""
    if content.startswith(UTF16_BOMS):
        # 有 BOM 才走这一支：`utf-16` 会按 BOM 判字节序，并把它自己剥掉。
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in UPLOAD_ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AppError("VALIDATION_ERROR", DECODE_FAILED_MESSAGE, 422)


def read_csv_grid(content: bytes) -> list[list[str]]:
    """CSV 字节 → 一格一格的二维数组（表头也在里面）。"""
    return [list(row) for row in _consume(csv.reader(io.StringIO(decode_upload(content))))]


def read_csv_dicts(content: bytes) -> list[dict[str, Any]]:
    """CSV 字节 → 一行一个字典（键是表头）。

    与 `read_csv_grid` 共用同一处解码与同一处 `csv.Error` 兜底，理由见 `_consume`。
    """
    return list(_consume(csv.DictReader(io.StringIO(decode_upload(content)))))


def _consume(iterator):
    """把 `_csv` 自己的错收成一句人话，再往下传。

    `csv.Error` 与 `UnicodeDecodeError` 是同一类东西——都不是 `AppError`，于是都会落进
    Starlette 的默认 500，屏幕上的症状一模一样（前端对那段纯文本调 `response.json()`）。

    2026-09-20 由一条测试撞出来：一份**能被 GBK 解开**的二进制文件（PNG 头那种字节，
    GBK 的前导字节范围很宽，绝大多数字节对都合法）会解成一串乱码，乱码里夹着的 `\\r`
    让 `_csv` 当场抛错——于是上面那句「不是 UTF-8 或 GBK」根本走不到。同样这个错，
    一份行尾是孤 `\\r` 的真 CSV 也会触发。

    **兜底必须套在消费那一侧**：`csv.reader` 是惰性的，`csv.Error` 不在构造时抛，
    而在取下一行时抛——把 `csv.reader(...)` 包进 `try` 是个空转。所以这里收的是
    **一个已经造好的迭代器**（`csv.reader` 或 `csv.DictReader`），由 `next()` 去触发
    那个错；`DictReader` 的取表头也在它自己的 `__next__` 里，所以同样被这一层罩住。
    """
    while True:
        try:
            yield next(iterator)
        except StopIteration:
            return
        except csv.Error as exc:
            raise AppError("VALIDATION_ERROR", UNREADABLE_TABLE_MESSAGE, 422) from exc


def parse_json_rows(text: str) -> list[Any]:
    """`.json` 上传那一支：解不出来时说一句人话，而不是一句 500。

    `json.JSONDecodeError` 与 `UnicodeDecodeError` 是同一类东西——都不是 `AppError`，
    都会落到 Starlette 的默认 500 上，而屏幕上的症状一模一样（前端对那段纯文本调
    `response.json()`）。所以这里一并收住，报的是「这个文件读不了」而不是「服务器内部错误」。
    """
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppError(
            "VALIDATION_ERROR", "JSON 文件格式不正确，请检查后重新上传", 422
        ) from exc
    if not isinstance(rows, list):
        raise AppError("VALIDATION_ERROR", "JSON必须是数组", 422)
    return rows
