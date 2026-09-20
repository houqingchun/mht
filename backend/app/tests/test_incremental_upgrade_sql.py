"""`sql/upgrade_from_v1_0_0.sql` 与**今天这棵源码树**的一致性守卫。

## 它守的是什么

那份文件是「从 V1.0.0 升到当前版本」的手工增量脚本，客户会拿 `mysql < 文件` 直接执行。
它由 `deploy/build_migration_sql.py` 从**两份既有来源**现渲染出来：

    链上每条迁移的 `PRECHECKS` 常量   +   `alembic upgrade 0012:head --sql` 的离线渲染

所以它是一份**派生物**，与 `backend/sql/schema_mysql8.sql` 同一个性质：快照，不是来源。
派生物最典型的坏法就是**过期**——迁移改了、文件没重跑，于是屏幕上写着「已在客户库上跑过
的脚本」而它其实还是上一版渲出来的。而它随 `backend/` 一起进安装包（`build_package.py`
的 `REQUIRED_PATHS` 钉住它），出错的地方是**客户手上的库**。

`Makefile` 的 `db-upgrade-sql` 与 `build_package.py` 的 `step("3/6 …")` 两处注释都点名了
这个文件。**它们说的就是下面 `test_the_snapshot_is_what_todays_migrations_render`。**

## ★ 一条不能改的写法：这里**不许**调 `build()`

`build(output_dir)` 会**先写** `backend/sql/<OUTPUT_NAME>`、**再**写 `output_dir` 那一份
（见它的函数体）。所以「拿 `build(tmp_path)` 生成一份、再与盘上那份比」是**恒真**的：
它在比对之前已经把那两份变成了同一串字节。

那不是一条会红的守卫，那是一条**永远绿**的守卫——而它看起来完全像是在证明完整性
（CLAUDE.md §29：一条恒绿的守卫没人会发现，它比没有更糟，因为它占着「这一条有人守」的
位置）。所以这里走 `compose(...)` 这个**纯函数**，自己拼字符串，一个字节都不落盘。

## 它守不住什么（网眼写明）

- **那一份 SQL 对不对**：它只证明「文件 == 今天渲染出来的东西」，不证明渲染出来的东西在
  MySQL 8 上跑得动。真正的证据在别处——`0013` / `0014` 的 precheck 与 `xlp_check_empty`
  那道门是在真 MySQL 8.4.4 上逐个情形试出来的（0/1 五种情形各测一遍），升级预演也是在一份
  忠实的克隆上真跑的（CLAUDE.md §21）。这里挡的只是「有人改了迁移却忘了重跑」。
- **包里那一份**：这一条只看 `backend/sql/` 这个快照。包里那份由 `build_package.py`
  **当场重新生成**（不是拷这个快照），所以两条路各自成立、互不代替。
- **Windows 上跑不跑得动**：`.ps1` / `.bat` 那一半归 `test_windows_assets.py`。
"""

from __future__ import annotations

import difflib
from functools import lru_cache
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "deploy" / "build_migration_sql.py"

# 检查条数的**下限**（今天恰好 13 条：0013 一条、0014 十二条）。写 `>=` 不写 `==`：
# 加一条 precheck 是一条正常的改动，而那会先让这份快照过期、先红在下面那条主用例上——
# 在**这条**上再红一次只会逼人来改一个与「文件新不新」无关的数字。
MIN_PRECHECKS = 13


def load_generator() -> ModuleType:
    """把生成器当模块导进来，好**真的调一次**它的渲染。

    与 `test_windows_assets.py::load_build_package` 同一个做法。它的顶层 import 全是标准库，
    导入没有副作用；脚本本体在 `if __name__ == "__main__":` 之下（`main()` 那一段只是在
    命令行跑时才执行）。

    **不用 `sys.path.insert`**：那会把 `deploy/` 变成全局可导入的，而 `deploy/windows/` 下
    还有一个 `serve_frontend.py`——按路径加载就不用去想「会不会撞上谁」。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("deploy_build_migration_sql", GENERATOR)
    assert spec and spec.loader, GENERATOR
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def rendered() -> tuple[str, dict[str, list[tuple[str, str]]], dict[str, str]]:
    """今天这棵树渲染出来的那份文本，**连同它的两个输入**。

    一次算三样、缓存起来：`render_ddl_blocks()` 要起一个 `alembic … --sql` 子进程
    （约 1 秒），而下面三条用例都要看同一份结果。

    返回值里的两个输入各自有用：`prechecks` 给不空转那条用（证明检查段真的搬进来了），
    `blocks` 给它当**变异用的原料**（改一个字节，输出必须跟着变）。
    """
    module = load_generator()
    chain = module.migration_chain()
    prechecks = module.collect_prechecks(chain)
    blocks = module.render_ddl_blocks()
    # ★ 这里就是「不许调 `build()`」的落点，理由见模块 docstring。
    return module.compose(chain, prechecks, blocks), prechecks, blocks


def snapshot_path() -> Path:
    return ROOT / "backend" / "sql" / load_generator().OUTPUT_NAME


def test_the_snapshot_is_what_todays_migrations_render():
    r"""盘上那一份必须与今天渲染出来的**逐字节相同**。

    红了怎么办：`make db-upgrade-sql`（它同时刷新 `backend/sql/` 与 `dist/` 两处），
    然后把 `backend/sql/upgrade_from_v1_0_0.sql` 一起提交。

    比对的是**字节**不是文本，与 `build()` 写它时用的判据一致：那份文件里既有中文注释
    又有 SQL，还有 `newline="\n"`——按文本比会漏掉行尾与编码那两类差别，而它们正是
    「在客户的 mysql 客户端上读起来不一样」的原因。
    """
    expected, _, _ = rendered()
    path = snapshot_path()
    actual = path.read_bytes()

    if actual != expected.encode("utf-8"):
        diff = difflib.unified_diff(
            actual.decode("utf-8").splitlines(),
            expected.splitlines(),
            fromfile=f"{path.relative_to(ROOT)}（磁盘上这一份）",
            tofile="今天这棵树渲染出来的那一份",
            lineterm="",
            n=2,
        )
        # 差异最多印 60 行：这个文件一千多行，改一条迁移能让它整段重排——
        # 而整段贴进失败信息里会把它淹掉，真正要看的往往是第一块。
        head = "\n".join(list(diff)[:60])
        raise AssertionError(
            f"{path.relative_to(ROOT)} 与今天这棵树渲染出来的不一样。\n"
            "改了 alembic 迁移就要重跑 `make db-upgrade-sql`——这份文件随 backend/ 一起\n"
            "打进安装包，客户会拿它去升自己的库。\n\n"
            f"--- 差异（磁盘 → 今天）---\n{head}"
        )


def test_the_snapshot_has_no_bom():
    """那份文件开头不许有 BOM。

    它由 `mysql … < 本文件` 执行，而字节序标记会让第一行变成 `\\xef\\xbb\\xbf-- …`——
    `mysql` 客户端当场报一句**第 1 行的语法错误**，离「编码」隔着一整个界面。
    `build()` 用 `encoding="utf-8"` 写（不是 `utf-8-sig`），所以今天是好的；这一条钉的是
    「有人觉得『加个 BOM 更好读』」那种改动。

    **不查别的编码性质**：这个文件**故意**是中文 UTF-8（文件头写着必须带
    `--default-character-set=utf8mb4`），与 `.bat` / `requirements.lock.txt` 那两处
    「纯 ASCII」的规矩不是一回事。
    """
    raw = snapshot_path().read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "开头有 UTF-8 BOM：mysql 客户端会把第一行连标记一起当 SQL 解析，报第 1 行语法错误。"
    )


def test_the_comparison_is_not_vacuous():
    r"""★ 上面那条比对必须**真的在读这棵源码树**，而不是在跟自己比。

    这一条防的是模块 docstring 里写下的那个形状：一个「先把快照写成渲染结果、再比一次」
    的实现（也就是 `build()`）会让上面那条永远绿。**一次运行里各证一次**，两条：

    1. **检查段真的搬进来了。** `collect_prechecks` 数得出来的每一条 `what`，
       必须逐字出现在文件里。少了它，一个「只渲染 DDL、把检查全丢了」的实现也是绿的
       ——而那道门（`CALL xlp_check_empty`）连同它一起没了，客户会在 DDL 跑到一半时
       才撞上一句与原因无关的英文错误。
    2. **`compose` 真的在读它的两个输入。** 各自改一个字节再调一次，输出必须跟着变。
       少了它，一个 `return (SQL_DIR / OUTPUT_NAME).read_text()` 的 `compose`
       会让第 1 条与上面那条**一起**绿——而它一个输入都没用。
    """
    text, prechecks, blocks = rendered()
    module = load_generator()

    # 一、检查段。
    found = sum(len(checks) for checks in prechecks.values())
    assert found >= MIN_PRECHECKS, (
        f"从迁移里只取到 {found} 条 precheck（预期至少 {MIN_PRECHECKS} 条）。"
        "要么是 `collect_prechecks` 坏了，要么是有人把迁移里的 `PRECHECKS` 删了——"
        "两种都会让下面那句断言变成空转。"
    )
    missing = [
        what
        for checks in prechecks.values()
        for what, _ in checks
        if what not in text
    ]
    assert not missing, (
        "这几条 precheck 的提示语没有出现在增量 SQL 里，说明【检查】那一段没被搬进去"
        "（或搬运时改了措辞）：\n" + "\n".join(f"  - {what}" for what in missing[:5])
    )

    # 二、`compose` 的两个输入。**在内存里变异，不落盘**——这一条只回答「输出是不是随
    # 输入变」，不是一条关于文件的断言。
    probe = "xlp_vacuity_probe"
    assert probe not in text, (
        "渲染结果里本来就有这个探针串，下面两条判据证明不了任何事——换一个字符串。"
    )

    last_revision = list(blocks)[-1]
    mutated_blocks = {**blocks, last_revision: blocks[last_revision] + f"\nSELECT 1 AS {probe};"}
    assert probe in module.compose(module.migration_chain(), prechecks, mutated_blocks), (
        "改了 DDL 块之后 `compose` 的输出一个字没变——它根本没在读第二个参数，"
        "上面那条逐字节比对是恒真的。"
    )

    some_revision = next(iter(prechecks))
    _, sql = prechecks[some_revision][0]
    mutated_prechecks = {
        **prechecks,
        some_revision: [(probe, sql)] + prechecks[some_revision][1:],
    }
    assert probe in module.compose(module.migration_chain(), mutated_prechecks, blocks), (
        "改了 precheck 提示语之后 `compose` 的输出一个字没变——它没在读第一个参数。"
    )
