"""**迁移真的建得出模型** —— 空库跑到 head 之后，与 `Base.metadata` 逐项相同。

## 为什么要有这一条

2026-09-18 那次「真机比对」是**人工做的**：一台 MySQL 8.4.4 上，一个库由
`schema_mysql8.sql` 建、一个库由迁移建，逐表比 `information_schema` 的
TABLES / COLUMNS / STATISTICS / REFERENTIAL_CONSTRAINTS。那次比对说明了
「V1.0 那一版是对的」，而下一次改 schema 时它**不会重跑**——它只留在
CLAUDE.md §16 的散文里。

2026-09-19 测试链整体迁到 MySQL（`conftest.py` 的表由 `alembic upgrade head`
建出来）之后，把那次比对变成能重复跑的守卫几乎免费：**测试库本来就是这么建的**。
于是「模型与迁移漂移」这一类故障现在有两个方向都能变红的信号：

| 信号 | 看得见什么 |
|---|---|
| `test_sql_schema_matches_models.py`（静态） | `sql/schema_mysql8.sql` 与模型的**结构**（表 / 列 / 可空 / 外键形状） |
| 本文件 | 迁移**真的建出来的库**与模型的**全部**：类型、索引、唯一约束、外键名、生成列 |

## 判据用 `compare_metadata`，不自己写比较器

那是 Alembic 自己生成迁移时用的那个函数（`alembic.autogenerate`），所以它认得的
差异正是「`alembic revision --autogenerate` 会写出一条迁移」的那些。自己再写一遍
列类型比较就是重写 MySQL 的别名规则（`int` / `integer` / `INT(11)`、
`tinyint(1)` / `bool` / `boolean`），而 CLAUDE.md §18 那条「在 PowerShell 上写
半吊子词法器，错的时候是无声的」换到 MySQL 上是同一句话。

## 它守不住什么（网眼写明）

- **`server_default` 不比**：`compare_server_default` 关着（那是默认值）。打开它
  之后实测有 20 条**假**差异——MySQL 反射回来的默认值是去引号的（模型写
  `'ACTIVE'`、库返回 `ACTIVE`），而 Alembic 那颗比较器不吃这一套。这正是
  CLAUDE.md §18 记的「误杀会把一台本来装得上的机器拦在第 4 步」的同一类：
  **宁可漏报，不要误杀**。默认值那一层由 `test_sql_schema_matches_models.py`
  的两条文本断言管着（时间戳必须是 `DEFAULT (now())`、列级不许 COLLATE）。
- **外键的**名字**不在 `compare_metadata` 的判据里**：它比「本表哪些列指向哪张表
  的哪些列」与 `ondelete` / `onupdate`，名字只用来打印。所以下面单列一条
  `test_the_databases_foreign_key_names_are_the_models_ones`。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymysql
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Computed, Engine, ForeignKeyConstraint

from app.db.base import Base
from app.tests.mysql_support import engine_for, throwaway_database

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "sql" / "schema_mysql8.sql"

# 语句切分按 `;` + 行尾。这份文件里**每一行带分号的语句都以分号收尾**
# （43 行，逐行看过），所以这样切是安全的。不写通用 SQL 解析器——
# 那个东西判错一次就再没人信它（CLAUDE.md §18 的剥壳器那一节）。
_STATEMENT_RE = re.compile(r";\s*$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    """剥掉 `--` 行注释：不剥的话注释里的例子会被当成语句执行。"""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def _differences(engine: Engine) -> list:
    """模型与这个库的全部差异。空列表 = 逐项相同。"""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return compare_metadata(context, Base.metadata)


def _describe(diffs: list) -> str:
    """把差异打印成人读得懂的一段。

    直接 `assert diffs == []` 会把元组按 Python 的 repr 倒出来，里面有
    `TextClause object at 0x…` 这种地址。这里每条取前几项，够看出
    「哪张表、哪一列、什么类型的差异」。
    """
    lines = []
    for diff in diffs:
        if isinstance(diff, tuple) and len(diff) >= 3:
            kind, _schema, table, *rest = diff
            lines.append(f"  [{kind}] {table} {rest[0] if rest else ''}")
        else:
            lines.append(f"  {diff}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 两条路各建一个库，都拿模型当判据
# --------------------------------------------------------------------------


def test_an_empty_database_migrated_to_head_matches_the_models(test_engine):
    """阶段 1 的全部产出：空库 → `alembic upgrade head` → 与模型逐项相同。

    测试库由 `conftest.py` 的 `test_engine` 这样建出来（`ensure_database` +
    `run_migrations`），所以这一条**不需要**自己再建一个库。它也是「模型少一列
    而迁移没少」这类故障的落点——改动之前（表由 `Base.metadata.create_all` 建）
    这条命题永远成立，那正是 2026-09-19 那次「523 个测试全绿而学生一开卷子就是
    MySQL 1364」的全部原因。
    """
    diffs = _differences(test_engine)
    assert not diffs, (
        f"迁移建出来的库与 ORM 模型有 {len(diffs)} 处不同——"
        f"补一条迁移，或者把模型改成与迁移一致：\n{_describe(diffs)}"
    )


def test_the_snapshot_file_builds_a_database_matching_the_models():
    """`sql/schema_mysql8.sql` 整份跑一遍，拿真库当裁判。

    这条比 `test_sql_schema_matches_models.py` 强一档：那一条读文本，只比结构
    （表 / 列 / 可空 / 外键形状）；这一条把文件**真的执行**一遍，比的是类型、
    索引、唯一约束、外键名、生成列——也就是 `ensure_schema` 刻意不比的那一层
    （它只比表名与列名，见 CLAUDE.md §18「选 3」那一节）。**两份写法会不会漂，
    这里给出的是实测答案，不是推理。**

    `FOREIGN_KEY_CHECKS` 保持开着（pymysql 的默认，也是服务器的默认），这是
    CLAUDE.md §16 那条：2026-09-18 的第一版把 `risk_event` 排在
    `assessment_session` 前面，真库报 `1824 Failed to open the referenced table`，
    而那一次的静态守卫除了次序那一条以外**全绿**。关掉检查等于把那种错误藏起来。

    这条用例比同文件另一条贵（一个一次性库 + 一次整份 DDL），所以它**独立成一个
    用例**而不是并进上一条：两条的失败原因不同，合成一条之后「哪条路坏了」
    要从报错文本里读。
    """
    with throwaway_database(with_schema=False) as url:
        statements = [
            chunk.strip()
            for chunk in _STATEMENT_RE.split(_strip_comments(SCHEMA_SQL.read_text(encoding="utf-8")))
            if chunk.strip()
        ]
        assert len(statements) >= 40, f"只切出 {len(statements)} 条语句，切分大概坏了"

        connection = pymysql.connect(
            host=url.host,
            port=url.port or 3306,
            user=url.username,
            password=url.password or "",
            database=url.database,
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
        finally:
            connection.close()

        engine = engine_for(url)
        try:
            diffs = _differences(engine)
        finally:
            engine.dispose()

    assert not diffs, (
        f"`schema_mysql8.sql` 建出来的库与 ORM 模型有 {len(diffs)} 处不同——"
        f"按这份文件手工建表的库会与迁移建出来的分岔：\n{_describe(diffs)}"
    )


# --------------------------------------------------------------------------
# 生成列：`Computed` 与真库的 `GENERATION_EXPRESSION` 必须一一对应
# --------------------------------------------------------------------------


def _generated_columns(engine: Engine) -> set[str]:
    # 判据是 `GENERATION_EXPRESSION <> ''`，不是 `EXTRA LIKE '%GENERATED%'`：
    # 后者那个 `%` 会被 pymysql 当成参数占位符（`must be real number, not dict`），
    # 而转义成 `%%` 只是为了让一个本来就不需要的 `LIKE` 能跑——非生成列的
    # `GENERATION_EXPRESSION` 在 MySQL 8 上就是空串，一个判据足够。
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND GENERATION_EXPRESSION <> ''"
        ).all()
    return {f"{table}.{column}" for table, column in rows}


def test_exactly_the_computed_columns_are_generated_in_the_database(test_engine):
    """两个方向都断。

    少了生成列 ⇒ 写入侧算出来的那个值会被**当成普通列**存进去，而
    `uq_care_case_one_active_per_student` / `uq_session_effective_task_student`
    这两条唯一键分不清「一个学生同时两条在办档案」（`active_student_id` 对已关闭
    的档案是 NULL，所以那条唯一键才收得住）。
    多了生成列 ⇒ 模型里没声明它，写入侧会去写它，MySQL 报
    `The value specified for generated column … is not allowed`。

    判据是**列名的集合**，不比表达式：表达式由上面那条 `compare_metadata` 管
    （它比列的类型与计算值），这里只回答「该生成的是不是生成了」。
    """
    in_models = {
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.server_default, Computed)
    }
    in_database = _generated_columns(test_engine)
    assert in_database, "库上一个生成列都没查到，这条用例失去了判据"
    assert in_models == in_database, (
        f"只在模型里（库里是普通列）：{sorted(in_models - in_database)}\n"
        f"只在库里（模型没声明）：{sorted(in_database - in_models)}"
    )


# --------------------------------------------------------------------------
# 外键的**名字**
# --------------------------------------------------------------------------


def _foreign_key_names(engine: Engine) -> dict[str, set[str]]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT TABLE_NAME, CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = DATABASE() AND CONSTRAINT_TYPE = 'FOREIGN KEY'"
        ).all()
    out: dict[str, set[str]] = {}
    for table, name in rows:
        out.setdefault(table, set()).add(name)
    return out


def test_the_databases_foreign_key_names_are_the_models_ones(test_engine):
    """约束名是**契约**：未来的 `op.drop_constraint("…")` 按名引用它。

    2026-09-19 那次真机比对逐表比了 `REFERENTIAL_CONSTRAINTS`，这一条是它的
    可重复版本。为什么 `compare_metadata` 不够：见模块 docstring 最后一条。

    模型里没命名的那 40 条只要求「长得像 `<表>_ibfk_<N>`」——那是 MySQL 自己编的，
    模型无从得知。**反向不查**（库里那个自动名具体是几号）：那个编号取决于约束的
    创建次序，钉住它等于把库绑死在「迁移是按这个顺序跑的」上。
    """
    wanted: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint) and constraint.name is not None:
                wanted.setdefault(table.name, set()).add(constraint.name)

    got = _foreign_key_names(test_engine)
    assert sum(len(names) for names in got.values()) >= 100, (
        f"真库上只查到 {sum(len(names) for names in got.values())} 条外键，"
        f"这条用例失去了判据"
    )

    problems = []
    for table, database_names in got.items():
        model_named = wanted.get(table, set())
        problems += [
            f"{table}: 模型里命名了、库里没有这个名字：{name}"
            for name in sorted(model_named - database_names)
        ]
        problems += [
            f"{table}: 库里这条外键叫 {name}，模型里既没有这个显式名字、"
            f"也不像 MySQL 自动生成的（`{table}_ibfk_<N>`）"
            for name in sorted(database_names - model_named)
            if not re.fullmatch(rf"{table}_ibfk_\d+", name)
        ]
    assert not problems, "外键的约束名对不上：\n" + "\n".join(problems)
