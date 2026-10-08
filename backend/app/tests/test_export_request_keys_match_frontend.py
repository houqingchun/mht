"""受控导出请求体的键名：`api.ts` 发出去的，必须就是后端认得的那几个。

## 它守的是什么

2026-10-08 用户报的：「心理老师在操作高度关注导出时，身份处理选『保留姓名（需特别授权）』，
但导出结果姓名仍然是脱敏的」。

根因不在权限、也不在导出本身，而在**键名**：`frontend/src/services/api.ts` 的三个受控导出
函数把驼峰字段名直接摊进请求体，只有 `studentIds → student_ids` 一个键做了映射，
于是 `maskNames` 以驼峰原样发出去。而 pydantic 的默认值是 `extra="ignore"` ——
**认不出的键被静默丢弃**，不报错、不 422。后端于是永远拿不到 `mask_names`，用默认的
`True`，姓名永远脱敏；`export_service.py` 那道 `MASK_FORBIDDEN` 只在 `mask_names=False`
时检查，所以连 403 都不会报。**屏幕上没有任何一处看得出来。**（同一个 bug 顺手废掉了
「字段范围 → 包含量表总分」，用户没报，但一样坏。）

这与 §19 那条「`/openapi.json` 报的版本号没人设过、却看起来像设过」是同一个形状的镜像：
**没人读过的字段，静默丢弃与正确传递在屏幕上长得一样。**

## 它怎么守（不抄第二份键名清单）

把 `api.ts` **当数据源读进来**，手法与 `test_export_labels_match_frontend.py` 逐字同源：

1. 从 `ExportOptions` 接口切出驼峰字段名；
2. 每个驼峰推导 snake_case，断言它**出现在 `controlledExportPayload` 的函数体里**；
3. 断言推导出的每个 snake 键都在 `CareCaseExportRequest.model_fields` 里
   （**直接 import 模型**，不抄清单——抄一份就有两种改错的方式）；
4. 断言三个导出函数**都调用了** `controlledExportPayload`：少一个，那个端点就独自漂回去。

`api.ts` 是全站唯一手写 fetch 封装，调用点全部手写 snake_case，所以这里也照它：
**在一个函数里显式映射**，不做全局 camelCase→snake_case 转换（隐式转换会让「哪些键被改了名」
不再能从调用点读出来，而这次的 bug 恰恰是「一个键被静默改名/丢弃」）。

## 它守不住什么（网眼写明）

- **前端有没有真的调那三个函数**：那是调用点的事，`e2e/app.spec.ts` 里那条用例抓的是
  真实请求体。
- **后端会不会把键读对**：`extra="forbid"`（`schemas/export.py`）让写错的键变成 422，
  而「读得对」由 `test_audit_export_api.py` 那两条行为用例钉住。
  这一条只保证**两边的名字是同一个**。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.schemas.export import CareCaseExportRequest

API_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "services" / "api.ts"

# `ExportOptions` 里**不由** `controlledExportPayload` 处理的键，逐条写理由。
#
# 这不是白名单，是「为什么它不在那儿」的记录：往这里加一项之前先回答
# 「那个键在别处是按什么形状发出去的」。写成白名单的话，下一个人会把
# 一个真的漏掉的键也塞进来，而这一条守卫就静默失效了。
HANDLED_ELSEWHERE = {
    "studentIds": (
        "只有 /care-cases/export 收学生名单（另两个端点后端不收，高度关注那一支带上它会 422），"
        "所以它由 exportCareCases 单独挂上，不在共用映射里"
    ),
}


def api_ts() -> str:
    assert API_TS.exists(), f"读不到 {API_TS}"
    return API_TS.read_text(encoding="utf-8")


def ts_block(source: str, header: str) -> str:
    """切出一个 TypeScript 块：从含 `header` 的那一行，到下一个列 0 的 `}`。

    与 `test_app_version.py::function_body` 同一个取舍：不做括号配平（半吊子词法器
    数错的时候是**静默少切**，而少切出来的那半截就不受下面任何断言约束了，测试照样绿）。
    这里要切的几个块都恰好是「花括号从签名行开始、到列 0 的 `}` 结束」，
    而块内部没有任何列 0 的 `}`（属性与方法都缩进）。
    """
    start = source.find(header)
    assert start >= 0, f"api.ts 里找不到 {header!r}（改名或搬走了？）"
    end = source.find("\n}", start)
    assert end >= 0, f"{header!r} 那个块没有以列 0 的 `}}` 收尾"
    return source[start : end + 2]


def camel_to_snake(name: str) -> str:
    """`maskNames` → `mask_names`。与后端 snake_case 的既定拼法一致。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def export_option_fields() -> list[str]:
    body = ts_block(api_ts(), "export interface ExportOptions {")
    # 只认缩进两格的 `名字?:` / `名字:`，注释行（`/**` 那些）自然落选。
    fields = re.findall(r"^\s{2}([A-Za-z_]\w*)\??:", body, re.MULTILINE)
    assert len(fields) >= 4, f"只从 ExportOptions 切出 {fields}，切法坏了（下面几条会空转）"
    return fields


def test_every_export_option_maps_to_a_field_the_backend_knows():
    """每个驼峰键推导出的 snake 键，都必须是 `CareCaseExportRequest` 的一个字段。

    反向读一遍：模型上多出来的字段（比如从前的 `mask_level`，零读者的死字段）不会让
    这一条红——它只问「前端会发的那些键，后端认不认」。**认不得就是本次那个 bug**：
    pydantic 会把它们丢掉，而屏幕上一切正常。
    """
    fields = CareCaseExportRequest.model_fields
    for camel in export_option_fields():
        snake = camel_to_snake(camel)
        assert snake in fields, (
            f"`ExportOptions.{camel}` 推导出的 {snake!r} 不是 CareCaseExportRequest 的字段"
            f"（它有 {sorted(fields)}）；这个键会被 pydantic 静默丢弃"
        )


def test_the_shared_mapper_carries_every_key_it_should():
    """共用映射里必须出现每个键的 snake 形态——**除了明写在 `HANDLED_ELSEWHERE` 的**。

    这一条钉的正是本次的缺陷：`mask_names` 那一行曾经根本不存在（`{ ...rest }` 把驼峰
    原样摊了出去）。变异验证：删掉 `mask_names: options.maskNames,` → 红。
    """
    body = ts_block(api_ts(), "function controlledExportPayload(")
    assert "options." in body, f"切出来的函数体不对：{body!r}"

    for camel in export_option_fields():
        if camel in HANDLED_ELSEWHERE:
            continue
        snake = camel_to_snake(camel)
        assert f"{snake}: options.{camel}" in body, (
            f"`controlledExportPayload` 里没有 `{snake}: options.{camel}` —— "
            f"这个键会被当成未知字段丢掉（{camel} 不在 HANDLED_ELSEWHERE 里，"
            "所以它本来就该在这里）"
        )

    # 例外那一半也要有落点，否则 HANDLED_ELSEWHERE 可以吞掉任何东西。
    named = ts_block(api_ts(), "export async function exportCareCases(")
    for camel, why in HANDLED_ELSEWHERE.items():
        assert f"{camel_to_snake(camel)}" in named, (
            f"{camel} 被记成「在别处处理」（{why}），但出了名的那一处也没有它"
        )


def test_all_three_controlled_exports_go_through_the_shared_mapper():
    """三个端点都走同一个映射函数——少一个，那一个就独自漂回去。

    第一版是 `{ ...rest }`，三个函数各摊各的，于是三个一起坏、也一起没人发现。
    收敛成一处之后，「哪个端点漂了」变成一条能问得出口的问题。
    """
    source = api_ts()
    for fn in ("exportCareCases", "exportHighRiskCareCases", "exportCareCase"):
        body = ts_block(source, f"function {fn}(")
        assert "controlledExportPayload(options)" in body, (
            f"`{fn}` 没有走 controlledExportPayload —— 它会把驼峰键原样发出去"
        )
