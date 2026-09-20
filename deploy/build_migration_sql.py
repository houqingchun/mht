#!/usr/bin/env python3
"""把「从 V1.0.0 升到当前版本」的数据库增量渲染成一份可以直接手工执行的 `.sql`。

## 它为什么存在

客户的库停在 V1.0.0（迁移 `0012_drop_care_case_unique`），升级要跑 `0013` ～ 当前 head。
路有两条：一键安装包里的 `install.ps1`（**升级模式**会自动跑 `ensure_schema` +
`alembic upgrade head`），以及**这份文件**——给「只想先把库升上去」「程序文件先不动」
「安装器那一侧出故障」的场景用。

## 它不是第二个出处

三样东西全部从既有来源取，这里一个字都不重写：

| 这份文件的哪一段 | 从哪来 |
|---|---|
| 每条迁移的【检查】 | 那条迁移自己的 `PRECHECKS` **常量本身**（按文件路径 import 出来） |
| 每条迁移的【DDL】 | `alembic upgrade <基线>:head --sql` 离线渲染出来的那一段 |
| 版本号那一行 | `app/version.py`（与 `build_package.write_package_info` 读同一处） |

「校验的唯一出处是那个常量」这句话写在 `0013_v12_expand.py` 的 `_precheck()` 里：
**离线模式下那一层不跑**（`op.get_bind()` 给的是一个 `MockConnection`，它的 `execute()`
返回 `None`，下一句 `.fetchall()` 当场 `AttributeError`），所以出路就是把那些常量搬到
这里当【检查】段，由执行的人先跑一遍再看结果。**在这里再抄一份 SQL 就等于开出第二个出处**
——两份会在某次改迁移之后各说各话，而它们看起来都对。

## 逐条迁移交错，不是「先全部检查、再全部动手」

这是 2026-09-20 在真 MySQL 上撞出来的，改之前那一版是两段式（13 条检查全在最前面），
它在客户的库上**第一条就死**：

```
[2/13] assessment_session.school_id 还有 NULL（回填只覆盖得到名册上还有的学生）
SELECT id, student_id FROM assessment_session WHERE school_id IS NULL LIMIT 5;
→ (1054, "Unknown column 'school_id' in 'where clause'")
```

`assessment_session.school_id` 是 **`0013` 才加上去的列**，而 `0014` 的检查问的正是
「`0013` 的回填做干净了没有」。这类检查**在 `0013` 的 DDL 跑完之前根本执行不了**。
真实迁移链的形状也是这个：`0014` 的 `_precheck()` 跑在 `0013` 之后。

所以顺序是**每条迁移「先检查、后动手」，一条一条往下走**：

```
迁移 1/6  0013_v12_expand   → 它的检查 → 它的 DDL
迁移 2/6  0014_v12_enforce  → 它的检查 → 它的 DDL   ← 这里才读得到 0013 加的那些列
...
```

「动手之前先看见结果」这条没有被削弱：`0014` 那 12 条检查仍然全部排在 `0014` 的第一条
DDL 之前（它们在迁移文件里就是这么写的），而 `0013` 是**只加不改**的——它那一段没有
可能失败。**别把次序改回两段式。**

## 每条检查后面必须有一次 `CALL`，否则检查等于没有

这是 2026-09-20 在同一次真机验证里撞出来的第二件事，而它比第一件严重得多。

把次序改成交错之后，正向那一趟是绿的（13 条检查全部 Empty set）。于是做了一次**反向**
验证：在一个停在 `0012` 的库上塞一行 `risk_type` 认不出的 `risk_event`，再跑这份脚本。
期望「被拦下」。实际结果是：

```
id      risk_type               trigger_rule
1       NOT_A_REAL_RISK_TYPE    r          ← 检查确实认出来了，也打出来了
ERROR 1048 (23000) at line 581: Column 'requires_manual_review' cannot be null
```

**检查打出了那一行，然后脚本继续往下跑了一百多条 DDL**，直到某条 `ALTER` 撞上那句与
真正原因毫无关系的英文错误。库最后是 **35 张表、版本戳还停在 `0012`**——正是这个文件头
里警告的「改了一半」。

根因只有一句：**检查语句是 `SELECT`，而 `mysql` 客户端不会因为你看见了结果就停下。**
「有结果就停下来反馈」这句话原本是写给**人**的，而人跑的是 `mysql DB < 这份文件`——
中间没有任何东西会拦。而 MySQL 的 DDL 不在事务里，所以「没拦住」的代价不是一条报错，
是一个半迁移的库。

出路是给每条检查配一道**机器**能过的门：`PRECHECKS` 里的那句 SQL 原样打印一遍给人看，
紧接着 `CALL xlp_check_empty(<同一条 SQL 的行数>, '…')`——有行就 `SIGNAL SQLSTATE
'45000'`，客户端当场中断，**那时一行 DDL 都还没跑**。那句 SQL 在产物里出现两次，但
两次都是生成器从**同一个变量**里印出来的，所以不存在「两份各说各话」。

存储过程是唯一能在 MySQL 里做条件中断的东西（`SIGNAL` 只许出现在复合语句里）。代价是
这一步要 `CREATE ROUTINE` 权限（root 有）；建不出来时 `mysql` 停在那一行——**那是安全的
一侧**，因为它什么都没执行。收尾处 `DROP PROCEDURE IF EXISTS` 把它删掉；中途被 `SIGNAL`
中断时那一句跑不到，所以开头也有一句 `DROP ... IF EXISTS` 顶着。

## 两条路都走子进程，不调 `alembic.command`

`alembic/env.py` **无条件**拿 `get_settings().database_url` 覆盖 `sqlalchemy.url`，所以
进程内调用会去迁移**开发库**（与 `app/tests/mysql_support.run_migrations` 同一条理由）。
工作目录必须是 `backend/`：`alembic.ini` 的 `script_location = alembic` 是相对 CWD 的
（`run_server.py` 为此专门 `chdir`，见 CLAUDE.md §18 第 4 条）。

## 编码

输出 **UTF-8 无 BOM、行尾 LF**，允许中文注释——与它旁边的 `reset_to_baseline.sql` /
`schema_mysql8.sql` 一致（那两个实测过：一个字节的 BOM 都没有）。执行时要带
`--default-character-set=utf8mb4`，文件头里写着这一条。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VERSIONS_DIR = BACKEND / "alembic" / "versions"
SQL_DIR = BACKEND / "sql"
DIST_DIR = ROOT / "dist"

# 升级的**起点**：V1.0.0 的 head。客户的库停在它上面，所以增量从它的**下一条**算起。
# 两个常量是一对，改一个要想着另一个——`BASELINE_LABEL` 是写给人看的那一串，
# 出现在文件头与文件名里；`BASELINE_REVISION` 是给 alembic 认的那一串。
BASELINE_LABEL = "V1.0.0"
BASELINE_REVISION = "0012_drop_care_case_unique"

# 一份文件、一个名字，两处落盘（`backend/sql/` 与 `dist/`）用的是同一个——这样纸上写的
# 名字、磁盘上的名字、包里那个名字是同一串字。**不按版本号起名**是有意的：那份内容里
# 的版本已经写在文件头了，而带版本号的文件名每升一次就留下一份同名的旧货。
# `V1.0.0` → `v1_0_0`。派生而不是再抄一遍字面量：这两串一旦对不上，出问题的地方是
# 文件**名**（纸上写的、包里那个），而看的人是文件**头**——那种不一致很难被发现。
OUTPUT_NAME = f"upgrade_from_{BASELINE_LABEL.lower().replace('.', '_')}.sql"

RULE = "=" * 78
LINE = "-" * 78

# 每条【检查】后面那道门（模块 docstring 第二节记着它为什么必须有）。
#
# 名字带 `xlp_` 前缀是为了不与客户库里任何东西撞名——它在对方的生产库里只活这一趟，
# 而一个叫 `check_empty` 的存储过程留在那儿会让人以为是他们自己的东西。
GUARD_PROCEDURE = "xlp_check_empty"

# `revision = "..."` / `down_revision = "..."`（或 `None`）。只认**列 0** 的那一行：
# 迁移文件里 `down_revision` 还会出现在别的地方（比如注释里引用上一个版本），
# 不加 `^` 就会读到那一处。
_REVISION_RE = re.compile(r'^revision\s*=\s*"([^"]+)"', re.MULTILINE)
_DOWN_REVISION_RE = re.compile(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', re.MULTILINE)

# 离线渲染里每条迁移的分界。**按它切块是这份文件能逐条交错的前提**：切出来的块与链上
# 的 revision 一一对应，切不齐（少一块、多一块）时下面的 `render_ddl_blocks` 当场停下。
_BLOCK_RE = re.compile(r"^-- Running upgrade (\S+) -> (\S+)\s*$", re.MULTILINE)


def _read_revision_and_parent(path: Path) -> tuple[str, str | None] | None:
    """读一条迁移的 revision 与它的上一条。读不到 revision 时返回 `None`。

    走链**不 import 任何模块**：这件事只需要字符串，而 import 一条迁移会连带执行它的
    模块级代码。链上六条里只有两条有 `PRECHECKS`，那两条才值得被 import（见下）。
    """
    source = path.read_text(encoding="utf-8")
    found = _REVISION_RE.search(source)
    if found is None:
        return None
    parent = _DOWN_REVISION_RE.search(source)
    return found.group(1), (parent.group(1) if parent else None)


def migration_chain() -> list[tuple[str, Path]]:
    """从基线的**下一条**排到 head，按执行次序（不含基线自己）。

    只认识线性链。分叉时当场停下——那说明有人开了分支，而这份文件按「一条路走到底」
    写，硬凑出来的次序会让某条迁移的 DDL 排在它依赖的那一条之前
    （`0014` 要收紧的列，`0013` 得先加上）。
    """
    graph: dict[str, tuple[str | None, Path]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        found = _read_revision_and_parent(path)
        if found is not None:
            graph[found[0]] = (found[1], path)

    parents = {down for down, _ in graph.values() if down}
    heads = [revision for revision in graph if revision not in parents]
    if len(heads) != 1:
        raise SystemExit(
            f"[X] 迁移链有 {len(heads)} 个 head（{sorted(heads)}）——"
            "这份生成器只认识线性链，先看 alembic 的 `heads` 再说"
        )

    chain: list[tuple[str, Path]] = []
    current: str | None = heads[0]
    while current is not None and current != BASELINE_REVISION:
        if current not in graph:
            raise SystemExit(f"[X] 迁移链走到了 {current!r}，但没有这一条 revision 的文件")
        down, path = graph[current]
        chain.append((current, path))
        current = down
    if current != BASELINE_REVISION:
        raise SystemExit(f"[X] 从 head 往回走没走到基线 {BASELINE_REVISION}，先到了链首")
    chain.reverse()
    return chain


def _load_module(path: Path, revision: str) -> ModuleType:
    """按**文件路径**加载一条迁移。

    模块名不能用 revision 本身：它以数字开头，不是合法的 Python 标识符。加一个前缀之后
    它是合法的，而 `importlib` 会把它塞进 `sys.modules`（名字必须是标识符的地方就在那）。
    """
    name = f"_xlp_migration_{revision}"
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"[X] 加载不了 {path}")
    module = module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:  # pragma: no cover —— 只在没装 alembic/sqlalchemy 时发生
        raise SystemExit(
            f"[X] 加载 {path.name} 失败（{exc}）。这个脚本要用跑后端的那一个解释器跑，"
            "两个包都在 backend/.venv 里"
        ) from exc
    return module


def collect_prechecks(chain: list[tuple[str, Path]]) -> dict[str, list[tuple[str, str]]]:
    """链上每条迁移的 `PRECHECKS`，按 revision 分组，返回 `{revision: [(提示语, SQL)]}`。

    **没有 `PRECHECKS` 的迁移跳过**（`0015` ～ `0018` 就是：它们改的是数据与列名，
    没有「旧数据里有没有反例」要问）。先按文本筛一遍再 import，是为了不去执行那几条与
    这件事无关的模块级代码。
    """
    collected: dict[str, list[tuple[str, str]]] = {}
    for revision, path in chain:
        if "PRECHECKS" not in path.read_text(encoding="utf-8"):
            continue
        checks = [
            # 常量写在三元组里的三元引号内，带着代码的缩进；`dedent` 之后贴进文件里才是
            # 一段可以整块复制去执行的 SQL。
            (what, textwrap.dedent(sql).strip())
            for what, sql in getattr(_load_module(path, revision), "PRECHECKS", ())
        ]
        if checks:
            collected[revision] = checks
    return collected


def render_ddl_blocks() -> dict[str, str]:
    """`alembic upgrade <基线>:head --sql` 的离线渲染，按迁移切成 `{revision: DDL}`。

    退出码非 0 时把 stderr 原样带出来：那一段英文（多半是某条迁移 import 失败）是唯一
    指得出原因的东西，吞掉它就只剩一句「生成失败」。
    """
    command = [sys.executable, "-m", "alembic", "upgrade", f"{BASELINE_REVISION}:head", "--sql"]
    done = subprocess.run(
        command, cwd=BACKEND, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode != 0:
        raise SystemExit(
            f"[X] 离线渲染失败（退出码 {done.returncode}）：{' '.join(command)}\n"
            f"    CWD：{BACKEND}\n{(done.stderr or '').strip()}"
        )

    text = done.stdout.strip("\n")
    marks = list(_BLOCK_RE.finditer(text))
    if not marks:
        raise SystemExit("[X] 离线渲染里一条 `-- Running upgrade` 都没有，切不出迁移块")
    blocks: dict[str, str] = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        blocks[mark.group(2)] = text[mark.start():end].strip("\n")
    return blocks


def _version_label() -> str:
    """`1.1.2`（规范串）。与 `build_package.write_package_info` 读的是同一个文件。

    **不走 import**：`app/version.py` 里明写着「不许有任何 import」，而为了拿一个字符串
    去 import 一整个 app 包，会让这个脚本在一个与版本无关的地方失败（比如没装 pydantic）。
    """
    source = (BACKEND / "app" / "version.py").read_text(encoding="utf-8")
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if found is None:
        raise SystemExit("[X] 读不出 backend/app/version.py 里的 __version__")
    return found.group(1)


def _header(chain: list[tuple[str, Path]], precheck_count: int) -> str:
    head = chain[-1][0]
    version = _version_label()
    count = len(chain)
    return "\n".join(
        [
            f"-- {RULE}",
            "-- 心晴 · 数据库增量升级脚本",
            "--",
            f"-- 从  {BASELINE_LABEL}（迁移 {BASELINE_REVISION}）",
            f"-- 到  V{version}（迁移 {head}）",
            "--",
            "-- 由 deploy/build_migration_sql.py 生成，**不要手工编辑**：它的数据源是",
            f"-- 链上 {count} 条迁移各自的 PRECHECKS 常量与 alembic 的离线渲染。",
            "-- 改了迁移就重跑一次 `make db-upgrade-sql`。",
            f"-- {RULE}",
            "",
            "-- 【怎么执行】",
            "--",
            "--   第一步 · 先备份。这一步没有替代品：",
            "--       mysqldump -h HOST -u USER -p --default-character-set=utf8mb4 \\",
            "--         --single-transaction DB > backup_$(date +%Y%m%d).sql",
            "--",
            f"--   第二步 · 把整个文件交给 mysql 执行。文件按迁移分成 {count} 条，每条都是",
            "--       「先【检查】、后【DDL】」两小段：",
            f"--             第 1 条 / 共 {count} 条：0013_xxx",
            "--               【检查】…   ← 每一条都必须返回 Empty set",
            "--               【DDL】…    ← 这条迁移真正动手的地方",
            "--       ★ 一定要**按这个次序**：后面的迁移会读前面刚加上去的列，",
            "--         把所有检查提到最前面会撞 `1054 Unknown column`。",
            f"--       ★ 一共 {precheck_count} 条检查，下面几条会替你拦住：任何一条检查打出数据，",
            "--         客户端都会当场中断，那时**一行 DDL 都还没跑**，你的库还是原样。",
            "--         看到 `ERROR 1644 (45000)` 就说明拦住了——把上面那条 SELECT 打出来的数据",
            "--         反馈给维护者，不要往下执行。它说明这个库里存在 DDL 挡不住的数据形状，",
            "--         而 MySQL 的 DDL 不在事务里，硬跑下去会留下「改了一半」的库。",
            "--       ★ 用 mysql 命令行客户端执行，并在第一条出错的语句处停下",
            "--         （命令行默认就是这样；图形工具要确认它没有开「出错继续」）。",
            "--              mysql -h HOST -u USER -p --default-character-set=utf8mb4 DB < 本文件",
            "--",
            "--   第三步 · 核对：",
            "--       SELECT version_num FROM alembic_version;",
            f"--       应当是 {head}",
            "--",
            "--   注意三件事：",
            "--   ① 这个文件是 UTF-8、含中文注释，**必须**带 --default-character-set=utf8mb4，",
            "--      否则中文会按连接编码解成乱码（注释无所谓，但你要读的就是它）。",
            "--   ② 【只该执行一次】。跑第二遍会撞 Duplicate column / Duplicate key name 之类的错，",
            "--      那是正常的，不是文件坏了。",
            "--   ③ 开头建了一个临时用的存储过程（就是下面那道门），结尾删掉。它需要",
            "--      CREATE ROUTINE 权限（root 有）；建不出来时脚本会停在那几行——",
            "--      那是安全的一侧，因为它一行都还没执行。",
            f"-- {RULE}",
            "",
            "-- 【那道门是什么】",
            "--",
            "--   每条【检查】后面都跟着两行：一句 `SELECT EXISTS(…) INTO @xlp_hits`",
            "--   与一次 `CALL xlp_check_empty(@xlp_hits, '…')`。",
            "--",
            "--   检查本身只是一句 SELECT：它把可疑的数据**打出来给你看**，而 mysql 客户端不会",
            "--   因为你看见了就停下——没有这道门时，脚本会带着这些问题继续往下跑 DDL，等到",
            "--   某条 ALTER 真正撞上时才报一句与真正原因无关的英文错误，而那时库已经改了一半",
            "--   （MySQL 的 DDL 不在事务里，中途失败不会回滚）。",
            "--",
            "--   所以紧跟的这两行把**同一条检查**再问一遍「有没有」——检查 SQL 原样套进",
            "--   `EXISTS(...)`，一个字不改——有就 SIGNAL，客户端当场中断，一行 DDL 都还没跑。",
            f"-- {LINE}",
            f"DROP PROCEDURE IF EXISTS {GUARD_PROCEDURE};",
            "DELIMITER //",
            f"CREATE PROCEDURE {GUARD_PROCEDURE}(IN p_hits INT, IN p_what VARCHAR(255))",
            "BEGIN",
            "  IF p_hits > 0 THEN",
            "    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = p_what;",
            "  END IF;",
            "END //",
            "DELIMITER ;",
            f"-- {LINE}",
        ]
    )


def _guard_form(sql: str) -> str:
    """把一句检查 SQL 变成守卫能读的那个 0/1：`SELECT EXISTS( <检查> ) INTO @xlp_hits`。

    **检查 SQL 原样进去、一个字不改**，这是这个形式唯一的、也是全部的价值：它不对
    取列表、别名、分组做任何假设，所以**加新检查时不需要想着守卫**。

    为什么不是别的两种（都是 2026-09-20 在真 MySQL 上撞出来的，不是推出来的）：

    - **不能包派生表**（`SELECT COUNT(*) FROM ( <检查> ) AS t`）：派生表的列名必须唯一，
      而好几条检查本来就在选同名列（检查 [9/12] 是
      `st.school_id, g.school_id, cg.school_id`），当场
      `ERROR 1060 (42S21): Duplicate column name 'school_id'`——而且它报在 **CALL 那一行**，
      离真正的原因隔着一条语句。
    - **不能数是几行**（`SELECT COUNT(*) INTO @v FROM <检查 FROM 之后那一段>`）：
      带 GROUP BY 的检查（`scale_rule` 那条查重复规则版本的）会返回**多行**，
      而 `SELECT … INTO` 多余一行是 `ERROR 1172`，报出来的话与「这条检查没过」毫无关系。
      把取列表换成 `SELECT 1` 能绕开它，但那会连带让 `HAVING n > 1` 这类**引用取列表别名**
      的写法失效（`Unknown column 'n'`）——也就是要为了守卫去改迁移里的检查，方向反了。

    `EXISTS` 不物化列，所以上面两条都不适用。同一台 MySQL 8.4.4 上逐条实测过（前四种是
    单独试的，第五种是**整份文件跑通**顺带证明的——`mysql` 在第一条出错的语句处就停，
    所以那份文件 EXIT=0 意味着每一条守卫都真的执行了）：

    | 情形 | 检查项 |
    |---|---|
    | `st.school_id, g.school_id, cg.school_id` | 重名列（派生表就是死在这一条上） |
    | `GROUP BY … HAVING n > 1 LIMIT 5` | 分组 + HAVING 引用取列表别名（数行数死在这一条上） |
    | `SELECT id … LIMIT 5` | 限行 |
    | `SELECT id, id … ORDER BY id DESC LIMIT 5` | 重名列 + 排序 |
    | 五段 `UNION ALL`（检查 [12/12]） | 联合——这一条最不显然，很多人以为 MySQL 不收子查询里的 UNION |

    五种都回一个 0/1。两个附带的好处：**遇到 LIMIT 也对**（`EXISTS` 只问有没有，测得的
    行数与 LIMIT 无关），而**它比数行数便宜**——`EXISTS` 拿到第一行就可以收工。

    最后记一条**为什么它不会比迁移本身更严**：守卫问的是 `EXISTS(<那条检查>)`，而迁移的
    `_precheck` 问的是「这条检查返回了行吗」——同一个结果集，同一个真假值。所以守卫不会
    在一份健康的库上凭空拦下一次升级；它拦下的，迁移自己也会拦。
    """
    return f"SELECT EXISTS({sql}) INTO @xlp_hits"


def _checks_part(revision: str, checks: list[tuple[str, str]]) -> list[str]:
    """一条迁移的【检查】小段。没有检查时不摆空壳——但要把「没有」说出来。"""
    if not checks:
        return [
            f"-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。",
            "",
        ]

    total = len(checks)
    lines = [
        f"-- 【检查】{total} 条。**每一条都必须返回 Empty set**。",
        "-- 这些 SQL 与迁移里的 PRECHECKS 是同一份（同一个常量，由生成器搬过来），",
        "-- 迁移自己在真跑之前也会做同样这一遍。提前摆在这里，是为了让你在动手之前看到结果。",
        "-- 每条检查后面紧跟一次 CALL：有数据就当场中断，你会看到 `ERROR 1644 (45000)`",
        "-- 与检查的编号——那时一行 DDL 都还没跑，库还是原样。",
    ]
    for index, (what, sql) in enumerate(checks, start=1):
        lines += [
            "",
            f"-- {LINE}",
            f"-- 检查 [{index}/{total}]",
            f"-- {what}",
            "-- 期望结果：0 行（Empty set）",
            f"-- {LINE}",
            f"{sql};",
            # ★ 这道门不能省：上面那句只是一条 SELECT，而 mysql 客户端**不会因为你看见了
            #   结果就停下**（模块 docstring 第二节记着 2026-09-20 那次实测：检查打出数据
            #   之后脚本照旧往下跑了一百多条 DDL，库被改了一半）。
            #   下面这条把同一条检查再问一遍「有没有」，有就 SIGNAL，客户端当场中断。
            #   为什么是 EXISTS 而不是 COUNT(*)、也不是套一层派生表，见 `_guard_form`。
            f"-- ↑ 这一条是给你看的；下面这一次 CALL 是给机器过的门（有行就当场中断）。",
            f"{_guard_form(sql)};",
            f"CALL {GUARD_PROCEDURE}(@xlp_hits, "
            f"'检查 [{index}/{total}] 未通过：见上面这条 SELECT 打出来的数据');",
        ]
    lines.append("")
    return lines


def _migration_section(
    index: int,
    total: int,
    revision: str,
    checks: list[tuple[str, str]],
    ddl: str,
) -> str:
    return "\n".join(
        [
            f"-- {RULE}",
            f"-- 第 {index} 条 / 共 {total} 条：{revision}",
            f"-- {RULE}",
            "",
            *_checks_part(revision, checks),
            "-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），",
            f"-- 末尾那条 UPDATE 把 alembic_version 推到 {revision}。",
            "-- ------------------------------------------------------------------------------",
            ddl,
        ]
    )


def _footer(chain: list[tuple[str, Path]]) -> str:
    head = chain[-1][0]
    return "\n".join(
        [
            f"-- {RULE}",
            "-- 到这里就结束了。核对一句：",
            "--   SELECT version_num FROM alembic_version;",
            f"-- 应当是 {head}。",
            "--",
            "-- 程序文件那一侧照常走一键安装包（升级模式不会重跑 seed、不会重置管理员密码、",
            "-- 不会碰数据库里的数据，只更新程序文件并再跑一次迁移——那时这一步已经是空转的）。",
            f"-- {LINE}",
            "-- 把开头建的那个临时存储过程删掉（它只在这一趟里有意义）。",
            "-- 上一次执行被 SIGNAL 中断时这一句跑不到，所以开头还有一次 DROP ... IF EXISTS。",
            f"DROP PROCEDURE IF EXISTS {GUARD_PROCEDURE};",
            f"-- {RULE}",
        ]
    )


def compose(
    chain: list[tuple[str, Path]],
    prechecks: dict[str, list[tuple[str, str]]],
    blocks: dict[str, str],
) -> str:
    precheck_count = sum(len(checks) for checks in prechecks.values())
    parts = [_header(chain, precheck_count)]
    for index, (revision, _path) in enumerate(chain, start=1):
        if revision not in blocks:
            # 渲染出来的块与链对不齐时当场停下：少一块意味着某条迁移的 DDL 没进文件，
            # 而那一份文件在客户手上是「跑到一半、版本戳停在中间」的库。
            raise SystemExit(f"[X] 离线渲染里没有 {revision} 这一段，链与渲染对不上")
        parts += [
            "",
            "",
            _migration_section(
                index, len(chain), revision, prechecks.get(revision, []), blocks[revision]
            ),
        ]
    parts += ["", "", _footer(chain), ""]
    return "\n".join(parts)


def build(output_dir: Path | None = None) -> list[Path]:
    """生成增量 SQL，写两个地方，返回写出来的路径。

    `backend/sql/` 那一份是**仓库里的快照**（随 `backend/` 一起进包，落在安装目录的
    `backend\\sql\\` 下）；`dist/` 那一份是拿给执行的人的那张。两处**同一个文件名**。
    """
    chain = migration_chain()
    text = compose(chain, collect_prechecks(chain), render_ddl_blocks())

    targets = [SQL_DIR / OUTPUT_NAME]
    if output_dir is not None:
        targets.append(output_dir / OUTPUT_NAME)
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        # `newline="\n"`：这个文件可能被拷到 Windows 上执行，而 CRLF 在 mysql 客户端里
        # 是能跑的——但它是**生成**的，行尾跟着平台变会让「两处逐字节相同」这条判据失效。
        target.write_text(text, encoding="utf-8", newline="\n")
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成「从 V1.0.0 升到当前版本」的数据库增量 SQL"
    )
    parser.add_argument(
        "--output-dir",
        default=str(DIST_DIR),
        help=f"除 backend/sql/ 之外再写一份到哪个目录（默认 {DIST_DIR.relative_to(ROOT)}/）",
    )
    args = parser.parse_args()

    chain = migration_chain()
    print(f"迁移链：{BASELINE_REVISION} → ... → {chain[-1][0]}（{len(chain)} 条）")
    for target in build(Path(args.output_dir)):
        size = target.stat().st_size
        print(f"  {target.relative_to(ROOT)}  ({size} 字节)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
