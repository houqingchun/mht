"""`sql/seed_mysql8.sql` 与**今天这棵源码树 + 一个真的库**的一致性守卫。

## 它守的是什么

那份文件是「只有系统基础数据与管理员账号」的那份 **DML** 脚本（六张表、105 行 INSERT，
一句 DDL 都没有）。客户拿到它时会走这条路：

    schema_mysql8.sql 建表  →  seed_mysql8.sql 写数据  →  ensure_schema 补版本戳

它由 `deploy/build_seed_sql.py` **真的跑一遍** `seed.py` 再 dump 结果渲出来——不是把
`seed.py` 的代码抄成 SQL（CLAUDE.md §16：在 SQL 里再抄一份基线必然漂移，而抄错的规则
JSON 会让那个库的评分与别处不同、且看不出来）。所以它是一份**派生物**：

    一个装了 MySQL 的库（`<主库名>_init`：迁移 → seed → reset_to_baseline → 读结果）

派生物最典型的坏法就是**过期**：`seed.py` / `data/mht_scale.json` /
`reset_to_baseline.sql` 改了、文件没重跑，于是客户手上的那份与今天这棵树对不上，
而它看起来完全正常。`Makefile` 的 `db-seed-sql` 那一长段注释说的就是下面第一条用例。

## ★ 一条不能改的写法：这里**不许**调 `build()`

`build(output_dir)` 会**先写** `backend/sql/<OUTPUT_NAME>`、**再**写 `output_dir` 那一份。
所以「拿 `build(tmp_path)` 生成一份、再与盘上那份比」是**恒真**的：它在比对之前已经把
那两份变成了同一串字节。

那不是一条会红的守卫，那是一条**永远绿**的守卫——而它看起来完全像是在证明完整性
（CLAUDE.md §29：一条恒绿的守卫没人会发现，它比没有更糟，因为它占着「这一条有人守」的
位置）。所以这里走 `render(blocks)` / `compose(...)` 这两个**纯函数**，一个字节都不落盘。

## 它守不住什么（网眼写明）

- **那份 SQL 在客户机上跑不跑得动**：第二条用例在一台**本机的** MySQL 上真导了一遍，
  但它证明的是这个仓库的开发环境，不是那台 Windows。`ensure_schema` 与 Alembic 那两步
  同理。真机那一侧归 `test_windows_assets.py` 与人工装机。
- **`schema_mysql8.sql` 自己新不新**：那是 `test_migrations_build_the_models.py` 与
  `test_sql_schema_matches_models.py` 的活。这里只把它当**输入**跑一遍。
- **两个进程不能同时跑**：这一条与其余任何 pytest 一样受 CLAUDE.md §20 管——那一句
  `DROP DATABASE` 落在 session 级夹具里，每个 pytest 进程都会执行一次。
"""

from __future__ import annotations

import difflib
import importlib.util
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text

from app.db.base import Base
from app.db.mysql_url import parse_database_url
from app.db.reset_to_baseline import run_sql
from app.security.passwords import verify_password
from app.tests.mysql_support import BACKEND_DIR, engine_for, throwaway_database

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "deploy" / "build_seed_sql.py"
SCHEMA_NAME = "schema_mysql8.sql"
SEED_NAME = "seed_mysql8.sql"

# 基线六张表。**不是**这条守卫的定义（定义在 `reset_to_baseline.sql` 的结果里），
# 只是「客户拿到手时能看到什么」的一句人话断言——它红了说明终点真的变了，
# 那时该做的是去更新 CLAUDE.md §16 那张表，不是把下面那个集合改小。
BASELINE_TABLES = {
    "school",
    "user_account",
    "user_scope",
    "assessment_scale",
    "scale_question",
    "scale_rule",
}


@lru_cache(maxsize=1)
def load_generator() -> ModuleType:
    """把生成器当模块导进来，好**真的调一次**它的渲染。

    与 `test_incremental_upgrade_sql.py::load_generator` 同一个做法。它的顶层 import
    会把 `backend/` 塞进 `sys.path`（拿 `app.models`），没有别的副作用；脚本本体在
    `if __name__ == "__main__":` 之下。

    **缓存**它：下面几条用例都要那个模块，而 `exec_module` 每次都会重新 import 一遍
    `app.models` 与 `app.db.mysql_url`，还会往 `sys.path` 里重复插一遍同一个路径。

    **不用 `sys.path.insert`**：那会把 `deploy/` 变成全局可导入的，而 `deploy/windows/`
    下还有一个 `serve_frontend.py`——按路径加载就不用去想「会不会撞上谁」。
    """
    spec = importlib.util.spec_from_file_location("deploy_build_seed_sql", GENERATOR)
    assert spec and spec.loader, GENERATOR
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def rendered() -> tuple[str, dict[str, list[dict]]]:
    """今天这棵树 + 今天这个库渲染出来的那份文本，**连同它的输入**。

    一次算两样、缓存起来：`collect_seed_blocks()` 要建一个一次性库、跑一遍
    `alembic upgrade head` + `seed` + `reset_to_baseline`、把结果读回来、再删库
    （几十秒），而下面几条用例都要看同一份结果。它碰的是 `<主库名>_init`
    （生成器里三条断言把着：以 `_init` 结尾、与主库不同名、是 mysql），
    **与测试库 `xinliceping_test` 是两个库**——session 级夹具那次
    `DROP DATABASE xinliceping_test` 碰不到它，它也不碰那边。

    `blocks` 单独返回是因为它是「这份文件里有什么」的**定义**：第二条用例拿它当
    逐表行数的期望值，第三条拿它当变异用的原料。

    ★ 这里就是「不许调 `build()`」的落点，理由见模块 docstring。
    """
    module = load_generator()
    blocks = module.collect_seed_blocks()
    return module.render(blocks), blocks


def snapshot_path() -> Path:
    return ROOT / "backend" / "sql" / load_generator().OUTPUT_NAME


# ---------------------------------------------------------------------------
# 一、逐字节：盘上那一份 == 今天这棵树渲染出来的那一份
# ---------------------------------------------------------------------------


def test_the_snapshot_is_what_todays_seed_renders():
    r"""盘上那一份必须与今天渲染出来的**逐字节相同**。

    红了怎么办：`make db-seed-sql`（它同时刷新 `backend/sql/` 与 `dist/` 两处），
    然后把 `backend/sql/seed_mysql8.sql` 一起提交。**它要连一台活着的 MySQL**——
    数据段必须来自 `seed.py` 的**结果**，不是它**代码**的副本（那个脚本的 docstring
    与 `Makefile` 的注释都写着这件事）。

    比对的是**字节**不是文本，与 `build()` 写它时用的判据一致：那份文件里既有中文注释
    又有 SQL，还有 `newline="\n"`——按文本比会漏掉行尾与编码那两类差别，而它们正是
    「在客户的 mysql 客户端上读起来不一样」的原因。
    """
    expected, _ = rendered()
    path = snapshot_path()
    actual = path.read_bytes()

    if actual != expected.encode("utf-8"):
        diff = difflib.unified_diff(
            actual.decode("utf-8").splitlines(),
            expected.splitlines(),
            fromfile=f"{path.relative_to(ROOT)}（磁盘上这一份）",
            tofile="今天这棵树 + 今天这个库渲染出来的那一份",
            lineterm="",
            n=2,
        )
        # 差异最多印 60 行：改校名能让整份文件里那 100 行题干的排序不变、但每一段标题都动
        # ——而整段贴进失败信息里会把它淹掉，真正要看的往往是第一块。
        head = "\n".join(list(diff)[:60])
        raise AssertionError(
            f"{path.relative_to(ROOT)} 与今天这棵树渲染出来的不一样。\n"
            "改过 `seed.py` / `data/mht_scale.json` / `reset_to_baseline.sql` 就要重跑\n"
            "`make db-seed-sql`（要连 MySQL），然后把 backend/sql/seed_mysql8.sql 一起提交。\n"
            "这份文件随 backend/ 一起打进安装包，客户会拿它往自己的库里写数据。\n\n"
            f"--- 差异（磁盘 → 今天）---\n{head}"
        )


def test_the_snapshot_has_no_bom():
    """那份文件开头不许有 BOM。

    它由 `mysql --default-character-set=utf8mb4 … < 本文件` 执行，而字节序标记会让第一行
    变成 `\\xef\\xbb\\xbf-- …`——`mysql` 客户端当场报一句**第 1 行的语法错误**，离「编码」
    隔着一整个界面。`build()` 用 `encoding="utf-8"` 写（不是 `utf-8-sig`），所以今天是好的；
    这一条钉的是「有人觉得『加个 BOM 更好读』」那种改动。

    **不查别的编码性质**：这个文件**故意**是中文 UTF-8（文件头写着必须带
    `--default-character-set=utf8mb4`），与 `.bat` / `requirements.lock.txt` 那两处
    「纯 ASCII」的规矩不是一回事。
    """
    raw = snapshot_path().read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "开头有 UTF-8 BOM：mysql 客户端会把第一行连标记一起当 SQL 解析，报第 1 行语法错误。"
    )


def test_the_comparison_is_not_vacuous():
    r"""★ 上面那条比对必须**真的在读这棵树**，而不是在跟自己比。

    这一条防的是模块 docstring 里写下的那个形状：一个「先把快照写成渲染结果、再比一次」
    的实现（也就是 `build()`）会让上面那条永远绿。这里**在内存里**各改一次它的输入，
    输出必须跟着变——一个 `return (SQL_DIR / OUTPUT_NAME).read_text()` 的 `render`
    会让上面那条与这一条**一起**绿，而它一个输入都没用。改动**不落盘**。

    两处原料各自代表一件事：改一个**值**证明 INSERT 段是真的从库里读出来的；
    去掉一张**表**证明表清单也不是写死的。
    """
    rendered_text, blocks = rendered()
    module = load_generator()

    probe = "xlp_vacuity_probe"
    assert probe not in rendered_text, (
        "渲染结果里本来就有这个探针串，下面两条判据证明不了任何事——换一个字符串。"
    )

    # 一、改一个值：`school.name` 是客户在「机构标识」里最可能改的那一格，
    # 而它出现在文件头（校名那一行）与 INSERT 两处。
    assert "school" in blocks, "基线里没有 school——这份守卫的变异原料取错了"
    mutated = {name: [dict(row) for row in rows] for name, rows in blocks.items()}
    mutated["school"][0]["name"] = probe
    assert probe in module.render(mutated), (
        "改了数据之后 `render` 的输出一个字没变——它根本没在读 `blocks`，"
        "上面那条逐字节比对是恒真的。"
    )

    # 二、去掉一张表：`-- <表名> · N 行` 那个段落标题只有 `_table_section` 会写，
    # 文件头那张清单用的是另一种排版（`--   <表名> <N> 行   <理由>`），不会误命中。
    dropped = "scale_rule"
    assert dropped in blocks, f"基线里没有 {dropped}——变异原料取错了"
    heading = f"-- {dropped} · "
    assert heading in rendered_text, f"文件里没有 {heading!r} 这个段落标题——判据换了形状，先看一眼产物"
    assert heading not in module.render({k: v for k, v in blocks.items() if k != dropped}), (
        "去掉一整张表之后那一节还在——`compose` 没在读表清单。"
    )


# ---------------------------------------------------------------------------
# 二、★ 一份看得像注释、其实是 SQL 的行：2026-09-21 真机故障的修复
# ---------------------------------------------------------------------------


def test_the_fake_comment_guard_catches_the_three_shapes():
    r"""`refuse_fake_comments` 必须还认得那三类形状，且**不误伤**合法文本。

    这条检查是 2026-09-21 那次真机故障的修复，所以它自己必须有东西钉住：一份在客户机上
    报 `ERROR 1064` 的交付文件，而报错位置离原因很远（光秃秃的横线报在**第 1 行**，
    `--（中文` 报在**那一行自己身上**，看起来像那句文案写错了）。

    四条判据：

    1. **根因**：那两条分隔线常量必须自带 `-- `。它们曾经只存一串 `=` / `-`，
       于是整份文件的第一行就是一条 SQL。断言常量本身，比断言某一行产物直接——
       产物是由它们拼出来的。
    2. **三类形状各报一次**，且都点名了行号。只断言「抛了异常」的话，一个
       `raise SystemExit()` 恒真的实现也是绿的。
    3. **合法文本不报**。少了这一条，第 2 条与一个「无论如何都抛」的实现同时成立。
    4. **`compose` 真的调了它**。上面三条都只是在直接调这个函数——把 `compose` 末尾
       那一行摘掉，它们**一条都不红**，而那一行正是唯一挡着「一份带着假注释的文件被
       生成出来」的东西（`build()` 会照样把它写到 `backend/sql/` 与 `dist/` 两处）。
       所以这里拿一个**能拼出一行假注释的版本号**喂给 `compose`：它要么当场 `SystemExit`，
       要么那行 `# …` 就落进产物里了。**这是第 4 条与上面三条的分工**：上面三条管
       「这个函数还认得那三类形状」，这条管「它还在路上」。
    """
    module = load_generator()

    # 一、根因。
    for name in ("RULE", "LINE"):
        value = getattr(module, name)
        assert value.startswith("-- "), (
            f"`{name}` 没有 `-- ` 前缀（现在是 {value[:12]!r}）——MySQL 的规矩是 `--` "
            "后面必须跟一个空格，所以光秃秃的横线是一条 SQL 语句，整份文件会在第 1 行报 1064。"
        )

    # 二、三类形状。第二条语句在 `-- ` 之后的第一个字符就是非空白，第五行同理。
    shapes = {
        "一条没带 `-- ` 的横线": "SELECT 1;\n" + "=" * 20 + "\n",
        "`--` 后面紧跟全角括号": "SELECT 1;\n--（学生账号跟着名册一起生成）\n",
        "`#` 注释": "SELECT 1;\n# 说明\n",
    }
    for what, bad in shapes.items():
        with pytest.raises(SystemExit) as caught:
            module.refuse_fake_comments(bad)
        message = str(caught.value)
        assert "看着像注释" in message, f"{what}：报出来的话里没有说明这是什么毛病"
        assert "第 2 行" in message, f"{what}：报出来的话里没有点名是第几行"

    # 三、合法文本不报——包括空行、以 `-- ` 开头的分隔线、以及 `--` 单独成行。
    module.refuse_fake_comments(
        "\n".join(
            [
                "-- 说明",
                "-- " + "=" * 75,
                "--",
                "-- 缩进过的说明",
                "SELECT 1;",
                "",
            ]
        )
    )

    # 四、★ `compose` 真的调了它。
    #
    # 上面三条都只是**直接调这个函数**——把 `compose` 末尾那一行摘掉，它们一条都不红，
    # 而那一行才是唯一挡着「一份带着假注释的文件被生成出来」的东西（`build()` 会照样把它
    # 写进 `backend/sql/` 与 `dist/` 两处，客户拿到手报 1064）。所以这里**喂一个能拼出
    # 一行假注释的版本号**给 `compose`，而不是再去调一次 `refuse_fake_comments`。
    #
    # 形状是算过的：`_header()` 把原始版本号原样拼进「版本：…（{version}）」那一行，
    # 所以中间那段 `# 说明` 会自成一行，被第三条判据逮住；而 `version.split('.')[:2]`
    # 取的仍是 `1.0`（那个 `#` 在后半段），标签那一处不会先抛别的错。
    #
    # 先排除一种「用别的理由变红」的可能：那个 `#` 行必须真的能拼出来，否则这条判据就
    # 变成在证明一件与调用点无关的事。
    _, blocks = rendered()
    poisoned = "1.0.0\n# 说明\n1.0.0"
    assert any(
        line.lstrip().startswith("#") for line in module._header(poisoned, blocks).splitlines()
    ), "这个版本号拼不出一行 `#`——换一个形状，否则下面那条断言证明不了调用点在不在。"

    with pytest.raises(SystemExit) as caught:
        module.compose(poisoned, blocks)
    assert "看着像注释" in str(caught.value), (
        "`compose` 没有调 `refuse_fake_comments`：那行 `# 说明` 会原样落进产物，"
        "而 `build()` 会把它写到 `backend/sql/` 与 `dist/` 两处。"
    )


# ---------------------------------------------------------------------------
# 三、真的导进去：一份空库 + `schema_mysql8.sql` + 这份产物
# ---------------------------------------------------------------------------


def _run_backend_module(args: list[str], url) -> subprocess.CompletedProcess:
    """照 `mysql_support.run_migrations` 的形状起一个后端命令，**但把输出要回来**。

    不复用 `run_migrations`：它成功时把 stdout/stderr 丢掉，而下面那条「没有可跑的迁移」
    的判据正是从那两句里读的（`Running upgrade` 是 Alembic 的 INFO 日志，走 stderr）。

    `XLP_DATABASE_URL` 用 `render_as_string(hide_password=False)`：`str(url)` 会把口令
    渲染成 `***`，症状是 `1045 Access denied … (using password: YES)`——与「口令配错了」
    逐字相同（CLAUDE.md §30 那一处踩过）。`XLP_TEST_DATABASE_URL` 要 pop 掉：
    pydantic-settings 里真实环境变量优先，留着它会让这条命令连到测试库上。
    """
    env = dict(os.environ)
    env["XLP_DATABASE_URL"] = url.render_as_string(hide_password=False)
    env.pop("XLP_TEST_DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_the_snapshot_really_imports_into_an_empty_database():
    r"""把**盘上那两份文件**导进一个空库，然后问库对不对。

    这是整份文件的落脚点：前面的用例证明「文件 == 今天渲染的」，这一条证明
    「渲染出来的东西真的能建出一个客户能登录的库」。它走的顺序就是客户的顺序——
    `schema_mysql8.sql`（建表）→ `seed_mysql8.sql`（写数据）→ `ensure_schema`
    （补版本戳）→ `alembic upgrade head`（应当无事可做）。

    几个判据各有各的理由：

    - **走 `run_sql`（`CLIENT.MULTI_STATEMENTS`），不切分语句**：末尾那条对账查询是一条
      `UNION ALL` 链，**分号只挂最后一行**（`_tail` 的 docstring 写着为什么）——
      用 `test_migrations_build_the_models.py` 那个 `;\s*$` 切分器会把它切成七八条
      各自能跑、但报错位置全错的东西。整份丢给客户端才是 `mysql < 文件` 的忠实模拟。
    - **此刻还没有 `alembic_version`**：那份表只有 Alembic 会建，产物文件头明写着
      「本文件不写它」（写了就是 1146 或 1062 两个方向各错一次）。它是一条**真正会被
      打破**的约定——往产物里加一句 `INSERT INTO alembic_version` 就红了。
    - **非空表恰好等于 `set(blocks)`**：多一张说明 `reset_to_baseline` 没清干净
      （客户会拿到 mock 数据），少一张说明有表被漏掉了。
    - **`verify_password(SEED_PASSWORD, <库里的哈希>)` 为真**：这是「客户能不能登录」
      的可执行形式，也是这份文件存在的全部理由。
    - **`assessment_scale.published_at` 等于被固定的那个常量**：那是三个非确定源之一，
      固定它才能让上面那条逐字节比对成立；这一条把「固定下来的值真的写进去了」问一遍。
    - **`ensure_schema` 说「只往 alembic_version 里写了一行」**，随后 `alembic upgrade head`
      输出里没有 `Running upgrade`：这两句一起才说明「表结构对得上、且没有可跑的迁移」。
      **不写死 `0018_row_conflict_resolution`**：迁移链往后走一条，写死的那句就会红在一个
      与功能无关的地方（CLAUDE.md 测试注意那条「数行数要问接口要」的同一条道理）。

    **那个变量叫 `rendered_text` 不叫 `text`**：这个函数里要用 `sqlalchemy.text(...)` 包
    每一句 SQL，而 `text, blocks = rendered()` 会把那个名字**遮蔽掉**——报出来的是
    `TypeError: 'str' object is not callable`，位置在 `text(...)` 那一行，看起来像
    SQLAlchemy 坏了。它第一次跑就是这么红的。
    """
    rendered_text, blocks = rendered()
    module = load_generator()

    with throwaway_database(with_schema=False) as url:
        target = parse_database_url(url.render_as_string(hide_password=False))

        # 一、建表 + 写数据。两份都从**磁盘**读——那正是客户拿到的东西。
        run_sql(target, (BACKEND_DIR / "sql" / SCHEMA_NAME).read_text(encoding="utf-8"))
        results = run_sql(target, snapshot_path().read_text(encoding="utf-8"))

        # 二、对账查询。整份文件只有它一个结果集——顺带证明中间那些语句都不是查询，
        # 也就是证明这份文件没有夹带一句没人预料到的 `SELECT`。
        assert len(results) == 1, (
            f"这份文件跑出了 {len(results)} 个结果集，预期恰好 1 个（末尾那条 UNION ALL）——"
            "要么多了一句查询，要么 `run_sql` 的 `nextset()` 没读干净。"
        )
        _, count_rows = results[0]
        counts = {row[0]: row[1] for row in count_rows}

        engine = engine_for(url)
        try:
            with engine.connect() as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
                        )
                    )
                }
                assert "alembic_version" not in tables, (
                    "这份文件往库里写了 `alembic_version`。它必须不碰那张表：在一份按 "
                    "`schema_mysql8.sql` 建出来的库上它还不存在（1146），而在一份已经迁移过的"
                    "库上补一行又会撞 1062。补版本戳的是 `app.db.ensure_schema`。"
                )

                def one(sql: str, **params):
                    return connection.execute(text(sql), params).one()

                # 逐表行数：对账查询答的那个数，与生成器读到的那个数，必须一样。
                assert set(counts) == set(tables), (
                    "对账查询数出来的表与库里实际的表对不上："
                    f"只在查询里 {sorted(set(counts) - set(tables))}，"
                    f"只在库里 {sorted(set(tables) - set(counts))}"
                )
                populated = {name for name, number in counts.items() if number}
                assert populated == set(blocks), (
                    "非空表与基线对不上。多了说明 `reset_to_baseline` 没清干净（客户会拿到"
                    f"不该有的数据），少了说明有表被漏掉：\n  多了 {sorted(populated - set(blocks))}\n"
                    f"  少了 {sorted(set(blocks) - populated)}"
                )
                assert populated == set(BASELINE_TABLES), (
                    "基线的**终点**变了，而 CLAUDE.md §16 那张表还停在旧的口径上：\n"
                    f"  现在是 {sorted(populated)}\n  文档写的是 {sorted(BASELINE_TABLES)}"
                )
                for name, rows in blocks.items():
                    assert counts[name] == len(rows), (
                        f"{name}：库里 {counts[name]} 行，生成器读到 {len(rows)} 行"
                    )

                # 三、能不能登录——这份文件存在的全部理由。
                account, account_type, password_hash, role_code, active = one(
                    "SELECT account, account_type, password_hash, role_code, active "
                    "FROM user_account"
                )
                assert (account, account_type, role_code) == ("admin", "ADMIN_USERNAME", "ADMIN")
                assert active is True or active == 1, "admin 落库时是停用的，登录会 401"
                assert password_hash == module.NORMALIZED[("user_account", "password_hash")][0], (
                    "库里的哈希不是被固定下来的那一个——`NORMALIZED` 与产物对不上了"
                )
                # `verify_password` 从 `app.security.passwords` 直接来，**不走生成器**：
                # 在生成器里它是 `_verify_credentials` 函数体内的一个局部 import（那一处
                # 自己的 docstring 写着为什么不放到顶层），所以 `module.verify_password`
                # 根本不存在。这里导入的与它导入的是同一个函数对象。
                assert verify_password(module.SEED_PASSWORD, password_hash), (
                    f"库里的 admin 哈希验不过 {module.SEED_PASSWORD}——客户拿到这份文件之后"
                    "一个账号都登不进去，而它看起来完全正常。"
                )

                # 四、量表 / 题库 / 规则三条齐了，且发布时间是被固定的那一个。
                published_at = one("SELECT published_at FROM assessment_scale").published_at
                assert published_at == module.NORMALIZED[("assessment_scale", "published_at")][0], (
                    "`assessment_scale.published_at` 不是被固定下来的那个常量——"
                    "要么 `NORMALIZED` 没生效，要么 seed 又改了口径"
                )
                questions = one(
                    "SELECT COUNT(*) AS n FROM scale_question q "
                    "JOIN assessment_scale s ON s.id = q.scale_id WHERE s.code = 'MHT'"
                ).n
                rules = one("SELECT COUNT(*) AS n FROM scale_rule WHERE status = 'ACTIVE'").n
                assert questions == counts["scale_question"] > 0
                assert rules >= 1, "没有生效中的评分规则——新建测评任务时选不出规则"

                # 五、admin 的范围行（§9：没有范围行的账号，每个列表都是空的）。
                scope_school, scope_user = one(
                    "SELECT school_id, user_id FROM user_scope"
                )
                admin_id, school_id = one(
                    "SELECT (SELECT id FROM user_account WHERE account = 'admin'), "
                    "(SELECT id FROM school)"
                )
                assert (scope_user, scope_school) == (admin_id, school_id)

        finally:
            engine.dispose()

        # 六、补版本戳，然后确认迁移无事可做。
        stamped = _run_backend_module(["-m", "app.db.ensure_schema"], url)
        assert stamped.returncode == 0, (
            f"`app.db.ensure_schema` 退出码 {stamped.returncode}：\n{stamped.stdout}\n{stamped.stderr}"
        )
        assert "只往 alembic_version 里写了一行" in stamped.stdout, (
            "`ensure_schema` 没有认账补戳——它认不出的库，客户就卡在「表有了、版本戳没有」上。\n"
            f"它说的是：\n{stamped.stdout}\n{stamped.stderr}"
        )

        upgraded = _run_backend_module(["-m", "alembic", "upgrade", "head"], url)
        assert upgraded.returncode == 0, (
            f"`alembic upgrade head` 退出码 {upgraded.returncode}：\n"
            f"{upgraded.stdout}\n{upgraded.stderr}"
        )
        assert "Running upgrade" not in upgraded.stdout + upgraded.stderr, (
            "导完这份文件之后 `alembic upgrade head` 还有迁移要跑——说明文件里的数据结构"
            "不是 head 那一版的：客户会在装完的第一天就缺一列（CLAUDE.md §20 那个形状）。\n"
            f"{upgraded.stdout}\n{upgraded.stderr}"
        )

    # 产物文本本身也在这一条里过一眼：`rendered()` 返回的是同一份东西的另一种拿法，
    # 而这里已经证明**盘上那一份**能建出库来。两条一起才排得掉「渲染对了、写盘写坏了」。
    assert rendered_text == snapshot_path().read_text(encoding="utf-8")
