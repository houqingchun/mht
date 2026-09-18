"""`sql/schema_mysql8.sql` 与 ORM 模型的一致性守卫。

## 它守的是什么

`backend/sql/schema_mysql8.sql` 是 schema 的**第二份写法**（第一份是 Alembic
迁移链）。CLAUDE.md §16 里那条「同一件事在 Python 与 SQL 各写一份，比一次是唯一
能让它们不漂的办法」是针对 `reset_to_baseline.sql` 说的，同一个理由在这里再成立
一次：`schema_prepared` 分工（`install.ps1` 第 1 步选 3）让操作员拿这份文件建表，
它一旦与迁移漂开，一所学校的库就会与别处不同，而且**看不出来** ——
`ensure_schema` 只比表名与列名，不比类型、不比索引、不比外键。

这个文件是静态的：它读文本、读 `Base.metadata`，**不碰数据库**。
所以它在内存 sqlite 上也是有效的（与 `test_sql_reset_to_baseline.py` 同一个形状）。

## 它守不住什么（网眼写明）

- **类型（`varchar(64)` vs `varchar(128)`）**：这一层故意不查。要比类型就得写一个
  MySQL 类型解析器，而它是重写一遍 MySQL 的别名规则（`int` / `integer` /
  `INT(11)`、`tinyint(1)` / `bool` / `boolean`、`datetime` 的大写形态）。
  判错一次就再没人信它，按 CLAUDE.md §18 那条「会无故变红的守卫很快会被人关掉」，
  宁可不查。**类型由 `test_sql_schema_matches_migrated_db` 之外的那次真机比对负责**
  （见下）。
- **默认值**：同理，只在「时间戳列必须是 `DEFAULT (now())`」这一条上查
  （那是 MySQL 8.0.13 的下限所在，也是唯一一处会静默改变行为的地方）。

## 真正的证据在哪

这个文件只是**防回归**。这份 DDL 的**正确性**是在真 MySQL 8.4.4 上验的，两条：

1. 库里已有 24 张表（由迁移建出来），`SHOW CREATE TABLE` 出来当基准；
   这份文件在 `FOREIGN_KEY_CHECKS=1` 下整份跑一遍建出另一套，两套逐表比对
   `information_schema` 的 TABLES / COLUMNS / STATISTICS / REFERENTIAL_CONSTRAINTS；
2. 另建一个**空库**跑 `alembic upgrade head`（0001→0012），拿**迁移刚建出来的**
   那套再比一次。

两条都过了，24 张表 / 214 列 / 85 条索引记录 / 45 个外键逐项相同。
**第 1 条差点没过**：第一版把 `risk_event` 排在 `assessment_session` 前面，
真库报 `1824 Failed to open the referenced table`，而这个文件里那几条断言**全绿**
—— 它们不看建表次序。所以有了下面那条 `test_tables_are_created_parent_before_child`。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.db.base import Base

SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema_mysql8.sql"

# 表级尾巴（引擎 + 字符集 + 排序规则）写在正则里，而不是单独断一条：
# 这样「某张表的尾巴写错了」表现为「这张表没被解析出来」（表集不相等），
# 比「第 N 条断言没过」更接近原因。
_CREATE_RE = re.compile(
    r"^CREATE TABLE `(?P<name>[a-z_]+)` \((?P<body>.*?)^\) ENGINE=InnoDB "
    r"DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;$",
    re.MULTILINE | re.DOTALL,
)
_COLUMN_RE = re.compile(r"^`(?P<name>[a-z_]+)`\s+(?P<rest>.+?),?$")
_FK_RE = re.compile(
    r"^CONSTRAINT `(?P<name>[a-z_0-9]+)` FOREIGN KEY \((?P<cols>[^)]*)\) "
    r"REFERENCES `(?P<parent>[a-z_]+)` \((?P<pcols>[^)]*)\)$"
)
_NAME_LIST_RE = re.compile(r"`([a-z_]+)`")


def sql_text() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def sql_without_comments() -> str:
    """剥掉 `--` 行注释。

    非做不可：这份文件的注释里写着反例（「没有 `DROP TABLE IF EXISTS`」、
    「不需要 `SET FOREIGN_KEY_CHECKS=0`」、`1824 Failed to open…`）。
    不剥的话下面那几条"不许出现"的断言会去读自己那句说明，把说明当成代码。
    这与 `test_sql_reset_to_baseline.py` 里的同名函数是同一条理由。
    """
    return "\n".join(
        line for line in sql_text().splitlines() if not line.lstrip().startswith("--")
    )


class ParsedTable:
    def __init__(self, name: str, columns: dict[str, bool], fks: list[tuple[str, str, str]]):
        self.name = name
        # 列名 -> 是否可空
        self.columns = columns
        # (约束名, 本表列, 父表名)
        self.fks = fks


def parse_tables() -> dict[str, ParsedTable]:
    tables: dict[str, ParsedTable] = {}
    for m in _CREATE_RE.finditer(sql_without_comments()):
        name = m.group("name")
        columns: dict[str, bool] = {}
        fks: list[tuple[str, str, str]] = []
        for raw in m.group("body").splitlines():
            # 每一行末尾都有逗号（最后一行除外）。先摘掉再匹配：
            # `_FK_RE` 是 `$` 锚定的，多一个逗号就匹配不上，而匹配不上会掉进
            # 下面的列分支、报一句「没能解析的列定义」——离原因很远。
            line = raw.strip().removesuffix(",").strip()
            if not line:
                continue
            fk = _FK_RE.match(line)
            if fk:
                # 本文件里每个外键都是单列；多列时按第一个列名记，
                # 下面那条「外键集合相等」会因此变红，而不是静默只记一半。
                local = _NAME_LIST_RE.findall(fk.group("cols"))[0]
                fks.append((fk.group("name"), local, fk.group("parent")))
                continue
            if line.startswith(("PRIMARY KEY", "UNIQUE KEY", "KEY ")):
                continue
            col = _COLUMN_RE.match(line)
            assert col, f"没能解析的列定义：{line!r}（表 {name}）"
            columns[col.group("name")] = " NOT NULL" not in col.group("rest")
        tables[name] = ParsedTable(name, columns, fks)
    return tables


TABLES = parse_tables()


def metadata_fks(model) -> set[tuple[str, str, str]]:
    out = set()
    for fk in model.foreign_keys:
        out.add((model.name, fk.parent.name, fk.column.table.name))
    return out


# --------------------------------------------------------------------------
# 空转自检：正则坏掉时下面每一条都会变成恒真，这一条先把它挡住
# --------------------------------------------------------------------------


def test_the_parser_actually_found_the_schema():
    """解析出来的规模不能为零。

    这是 CLAUDE.md §18 那条「扫到的处数 ≥ 12 的空转自检」的同一形状：
    `_CREATE_RE` 一旦匹配不上，`TABLES` 就是空字典，而下面所有"每个表都…"
    的断言在空集合上**全部为真**。门槛取 20/200 而不是精确的 24/214 ——
    精确值会在任何人加一张表时变红，而红的原因不是功能坏了。
    """
    assert len(TABLES) >= 20, f"只解析出 {len(TABLES)} 张表，正则大概坏了"
    total_columns = sum(len(t.columns) for t in TABLES.values())
    assert total_columns >= 200, f"只解析出 {total_columns} 列，正则大概坏了"


# --------------------------------------------------------------------------
# 表 / 列：与 Base.metadata 双向相等
# --------------------------------------------------------------------------


def test_the_table_set_is_exactly_the_models():
    """两个方向都要断。

    只断「模型里的表都在文件里」会漏掉「文件里多了一张模型没有的表」——
    而那一张恰恰是 `ensure_schema` 会判「对得上」的漏网之鱼（它也只比表名集合，
    多出来的表它不认，但它不会因此拒绝）。
    """
    assert set(TABLES) == set(Base.metadata.tables), (
        f"只在该文件里：{sorted(set(TABLES) - set(Base.metadata.tables))}\n"
        f"只在模型里：{sorted(set(Base.metadata.tables) - set(TABLES))}"
    )


def test_every_table_declares_the_same_columns_as_the_model():
    mismatched = {}
    for name, model in Base.metadata.tables.items():
        want = {c.name for c in model.columns}
        got = set(TABLES[name].columns)
        if want != got:
            mismatched[name] = {"只在该文件里": sorted(got - want), "只在模型里": sorted(want - got)}
    assert not mismatched, f"列集合不一致：{mismatched}"


def test_nullability_matches_the_model():
    """可空性不是细节：`confirmed_facts` / `next_follow_up_date` 那一类的
    NOT NULL 是**语义要求**（一条没有内容的跟进记录不是记录）。
    文件里漏一个 NOT NULL，手工建的库就允许写进一行空记录，
    而应用层以为数据库会挡。"""
    mismatched = {}
    for name, model in Base.metadata.tables.items():
        parsed = TABLES[name]
        for col in model.columns:
            if col.name not in parsed.columns:
                continue
            if parsed.columns[col.name] != col.nullable:
                mismatched[f"{name}.{col.name}"] = (
                    f"文件={'可空' if parsed.columns[col.name] else 'NOT NULL'}，"
                    f"模型={'可空' if col.nullable else 'NOT NULL'}"
                )
    assert not mismatched, f"可空性不一致：{mismatched}"


# --------------------------------------------------------------------------
# 外键：集合相等 + 删除规则 + 建表次序
# --------------------------------------------------------------------------


def test_foreign_keys_match_the_model():
    mismatched = {}
    for name, model in Base.metadata.tables.items():
        want = metadata_fks(model)
        got = {(name, local, parent) for _, local, parent in TABLES[name].fks}
        if want != got:
            mismatched[name] = {"只在该文件里": sorted(got - want), "只在模型里": sorted(want - got)}
    assert not mismatched, f"外键不一致：{mismatched}"


def test_no_foreign_key_declares_an_ondelete_action():
    """CLAUDE.md §1：**全库没有 `ondelete=`，每个外键都是 RESTRICT。**

    这不是风格问题：`student_care_case` 的历史、`risk_event.session_id`、
    `retest_plan.source_session_id` 全靠它。加一个 `ON DELETE CASCADE` 会让
    「关闭档案不得删除历史记录」这条约定在数据库层被推翻，而且是静默的。
    """
    offenders = re.findall(r"ON DELETE\s+(\w+)", sql_without_comments(), re.IGNORECASE)
    assert not offenders, f"出现了 ON DELETE 子句：{offenders}"


def test_tables_are_created_parent_before_child():
    """**这一条是那次真机失败的化石。**

    第一版把 `risk_event` 排在 `assessment_session` 前面。真库报
    `1824 Failed to open the referenced table 'assessment_session'`，
    而当时这个文件里那几条断言全绿 —— 它们比对的是集合与可空性，不看次序。
    内存 sqlite 更看不见（它默认不检查外键，CLAUDE.md 缺口 3）。

    判据：按文件里的先后建位置表，每个外键指向的父表必须排在子表**之前**。
    自引用（父子同表）不算违例，所以是 `<` 而不是 `<=` 的反面写法。
    """
    order = {name: i for i, name in enumerate(TABLES)}
    violations = [
        f"{t.name} 第 {order[t.name]} 位 → 引用 {parent} 第 {order[parent]} 位"
        for t in TABLES.values()
        for _, _, parent in t.fks
        if parent != t.name and order[parent] > order[t.name]
    ]
    assert not violations, "建表次序把子表排在了父表前面：\n" + "\n".join(violations)


def test_the_constraint_names_migrations_depend_on_are_present():
    """迁移按**名字**引用约束，那些名字因此是契约的一部分。

    这一条钉住四个具体的名字，每一个都有来路：
      - `ix_student_care_case_student_id`：迁移 0012 删掉那个 UNIQUE 之前**必须先建它**
        ——它兼作 `student_id` 外键的索引，MySQL 会以 1553 拒绝删一条外键正在用的索引；
      - `ix_audit_log_student_id`：迁移 0006 显式建的，不是 `index=True` 的自动名；
      - `fk_session_task` / `fk_audit_log_student_id`：全库仅有的两个**人工命名**的外键，
        其余 43 个都是 MySQL 自动生成的 `<表>_ibfk_<N>`。
    """
    text = sql_without_comments()
    for name in (
        "ix_student_care_case_student_id",
        "ix_audit_log_student_id",
        "fk_session_task",
        "fk_audit_log_student_id",
    ):
        assert f"`{name}`" in text, f"约束名 `{name}` 不见了 —— 迁移按名字引用它"


# --------------------------------------------------------------------------
# 三条文件级约定（与文件头那一段逐条对应）
# --------------------------------------------------------------------------


def test_the_script_never_drops_a_table():
    """约定②：只建不删。

    `DROP TABLE IF EXISTS` 会让「手滑跑了第二次」从**当场报错**变成
    **静默抹掉一所学校的库**。这条断的是「文件里不许出现 DROP TABLE」——
    不是「DROP 危险」，是「这个文件的语义是往一个空库里铺结构」。
    """
    offenders = re.findall(r"\bDROP\s+(?:TABLE|DATABASE)\b", sql_without_comments(), re.IGNORECASE)
    assert not offenders, "这份脚本只建不删，出现了 DROP"


def test_the_script_never_disables_foreign_key_checks():
    """约定③：次序是靠排对来保证的，不是靠关掉检查。

    关掉 `FOREIGN_KEY_CHECKS` 会让次序错误变得不可发现 —— 脚本照样跑完、
    表照样建出来，而问题留到运行期。第一版那次真机失败正是**开着**检查才暴露的。
    """
    assert not re.search(
        r"FOREIGN_KEY_CHECKS\s*=\s*0", sql_without_comments(), re.IGNORECASE
    ), "不许关掉外键检查：次序错了本该在建表这一步就停住"


def test_every_timestamp_column_uses_the_expression_default_form():
    """约定①的孪生：`DEFAULT (now())` 而不是 `DEFAULT now()`。

    两者在 MySQL 里都能建出来，但只有带括号的这一种是**表达式默认值**语法，
    也正是 SQLAlchemy 的 `server_default=func.now()` 在 8.0.13+ 上落下来的形状。
    照抄它的理由是让这个文件建出来的库与迁移建出来的**逐字相同** ——
    比对 `information_schema` 时 `COLUMN_DEFAULT` 是一列，形状不同就会报警。

    同时它把版本下限写死在这里：8.0.12 及更早不认这个语法，
    谁把它改成裸 `now()`「为了兼容 5.7」，那条真机比对就会失败。
    """
    text = sql_without_comments()
    bare = re.findall(r"DEFAULT\s+now\(\)", text, re.IGNORECASE)
    assert not bare, f"有 {len(bare)} 处裸 `DEFAULT now()`，应当是 `DEFAULT (now())`"
    assert len(re.findall(r"DEFAULT \(now\(\)\)", text)) >= 20, "带括号的表达式默认值一个都没找到"


def test_no_column_default_carries_a_character_collation():
    """列级不许出现 COLLATE。

    这一条是防一次具体的漂移：真库的列是按表级 `utf8mb4_0900_ai_ci` 继承的，
    列级 COALESCE 为 NULL；手工在某一列上写 `COLLATE utf8mb4_general_ci`
    会让 `information_schema.COLUMNS.COLLATION_NAME` 只在这一列出值，
    于是一所学校库里的排序规则与别处不同 —— 中文姓名排序错位，而没人会想到是 schema。
    """
    # 判据是「这一行看着像列定义」（以反引号包着的列名开头）**且**带 COLLATE。
    # 第一版写的是 `"TABLE" not in line`，而表尾那一行是
    # `) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;`
    # —— 里面没有 "TABLE" 这个词，于是 24 行表尾全被当成违例报上来。
    offenders = [
        line.strip()
        for line in sql_without_comments().splitlines()
        if "COLLATE" in line and _COLUMN_RE.match(line.strip().removesuffix(","))
    ]
    assert not offenders, f"出现了列级 COLLATE：{offenders}"
