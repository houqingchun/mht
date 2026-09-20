"""`sql/schema_mysql8.sql` 与 ORM 模型的一致性守卫。

## 它守的是什么

`backend/sql/schema_mysql8.sql` 是 schema 的**第二份写法**（第一份是 Alembic
迁移链）。CLAUDE.md §16 里那条「同一件事在 Python 与 SQL 各写一份，比一次是唯一
能让它们不漂的办法」是针对 `reset_to_baseline.sql` 说的，同一个理由在这里再成立
一次：`schema_prepared` 分工（`install.ps1` 第 1 步选 3）让操作员拿这份文件建表，
它一旦与迁移漂开，一所学校的库就会与别处不同，而且**看不出来** ——
`ensure_schema` 只比表名与列名，不比类型、不比索引、不比外键。

这个文件是静态的：它读文本、读 `Base.metadata`，**不碰数据库**。
所以它不需要一台 MySQL 就能跑（与 `test_sql_reset_to_baseline.py` 同一个形状）——
那一条在 2026-09-19 测试链整体迁到 MySQL 之前就已经是有效的，现在仍然有效。

## 它守不住什么（网眼写明）

- **类型（`varchar(64)` vs `varchar(128)`）**：这一层故意不查。要比类型就得写一个
  MySQL 类型解析器，而它是重写一遍 MySQL 的别名规则（`int` / `integer` /
  `INT(11)`、`tinyint(1)` / `bool` / `boolean`、`datetime` 的大写形态）。
  判错一次就再没人信它，按 CLAUDE.md §18 那条「会无故变红的守卫很快会被人关掉」，
  宁可不查。**类型、索引、生成列一律归 `test_migrations_build_the_models.py`**
  ——那一条拿 `information_schema` 与 `Base.metadata` 真比一次，所以它只能看见
  「迁移与模型」这一对，看不见这份**文本**里的类型写错了（那要真去跑它）。
- **默认值**：同理，只在「时间戳列必须是 `DEFAULT (now())`」这一条上查
  （那是 MySQL 8.0.13 的下限所在，也是唯一一处会静默改变行为的地方）。

## 真正的证据在哪

这个文件只是**防回归**。这份 DDL 的**正确性**是在真 MySQL 8.4.4 上验的：

- 2026-09-18（V1.0，24 张表）：库里已有 24 张表（由迁移建出来），`SHOW CREATE TABLE`
  出来当基准；这份文件在 `FOREIGN_KEY_CHECKS=1` 下整份跑一遍建出另一套，两套逐表比对
  `information_schema` 的 TABLES / COLUMNS / STATISTICS / REFERENTIAL_CONSTRAINTS，
  另建一个**空库**跑 `alembic upgrade head` 再比一次 —— 24 表 / 214 列 / 85 条索引记录 /
  45 个外键逐项相同。**那一次差点没过**：第一版把 `risk_event` 排在
  `assessment_session` 前面，真库报 `1824 Failed to open the referenced table`，
  而这个文件里那几条断言**全绿** —— 它们不看建表次序。所以有了下面那条
  `test_tables_are_created_parent_before_child`。
- 2026-09-19（V1.2，34 张表）：那次人工比对**升级成了一条能重复跑的守卫**
  （`test_migrations_build_the_models.py`）。所以现在这里那两组数字的作用只剩
  「换版本时记得同步注释」——真正会红的是那一条，不是这行注释。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from sqlalchemy import ForeignKeyConstraint

from app.db.base import Base

SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema_mysql8.sql"

# 标识符里**有数字**：`file_sha256` 是三个导入/导出表上的列，而 `[a-z_]+`
# 匹配不到它。那一个字符让整个文件的 `parse_tables()` 在 import 期就抛
# 「没能解析的列定义」——**整份套件一条用例都没跑起来**（pytest 的 collection
# error）。这就是 `_IDENT` 单独写成一个片段的理由：名字的字符类只该有一处。
_IDENT = r"[a-z_0-9]+"

# 表级尾巴（引擎 + 字符集 + 排序规则）写在正则里，而不是单独断一条：
# 这样「某张表的尾巴写错了」表现为「这张表没被解析出来」（表集不相等），
# 比「第 N 条断言没过」更接近原因。
_CREATE_RE = re.compile(
    rf"^CREATE TABLE `(?P<name>{_IDENT})` \((?P<body>.*?)^\) ENGINE=InnoDB "
    r"DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;$",
    re.MULTILINE | re.DOTALL,
)
_COLUMN_RE = re.compile(rf"^`(?P<name>{_IDENT})`\s+(?P<rest>.+?),?$")
_NAME_LIST_RE = re.compile(rf"`({_IDENT})`")
_FK_RE = re.compile(
    rf"^CONSTRAINT `(?P<name>{_IDENT})` FOREIGN KEY \((?P<cols>[^)]*)\) "
    rf"REFERENCES `(?P<parent>{_IDENT})` \((?P<pcols>[^)]*)\)$"
)
# 排在所有 `CREATE TABLE` 之后的 `ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY …`。
# 为什么非要有这一段：`assessment_import_row ↔ assessment_external_result` 是一个
# **二元环**（前者 `external_result_record_id → 后者.id`，后者 `row_id → 前者.id`），
# 环无法线性化，所以那几条外键写在表体里是**建不出来**的。
_ALTER_FK_RE = re.compile(
    rf"^ALTER TABLE `(?P<table>{_IDENT})`\s*\n\s*"
    rf"ADD CONSTRAINT `(?P<name>{_IDENT})` FOREIGN KEY \((?P<cols>[^)]*)\) "
    rf"REFERENCES `(?P<parent>{_IDENT})` \((?P<pcols>[^)]*)\);$",
    re.MULTILINE,
)


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


# 一条外键**约束**的形状：(本表列…, 父表, 父表列…)。
#
# 这里刻意是四元组而不是「第一个本地列」：`student` 上的
# `(school_id, grade_id) → grade (school_id, id)` 与既有的单列
# `school_id → grade`（`student_ibfk_3`）只取第一个本地列时**长得一模一样**，
# 两者会塌成一条；`retest_plan` 的两条 `→ assessment_session`
# （`source_session_id` / `completed_session_id`）同形。塌掉之后守卫的方向就反了：
# 它会把「解析器记漏了」报成「模型不对」。
FkSignature = tuple[tuple[str, ...], str, tuple[str, ...]]


class ParsedTable:
    def __init__(self, name: str, columns: dict[str, bool], fks: dict[str, FkSignature]):
        self.name = name
        # 列名 -> 是否可空
        self.columns = columns
        # 约束名 -> 那条约束的形状。**用 dict 而不是 list**：约束名是这张表上
        # 唯一能把两条同形约束分开的东西，丢掉它就没法回答「这里到底有几条」。
        self.fks = fks
        # 上面那些里，写在表体之外的
        # `ALTER TABLE … ADD CONSTRAINT` 那几条。建表次序那一条要放行它们，
        # 而「放行」的前提见 `test_the_alter_section_comes_after_every_create_table`。
        self.later_fks: set[str] = set()


def _fk_signature(match: re.Match[str]) -> tuple[str, FkSignature]:
    return (
        match.group("name"),
        (
            tuple(_NAME_LIST_RE.findall(match.group("cols"))),
            match.group("parent"),
            tuple(_NAME_LIST_RE.findall(match.group("pcols"))),
        ),
    )


def parse_tables() -> dict[str, ParsedTable]:
    text = sql_without_comments()
    tables: dict[str, ParsedTable] = {}
    for m in _CREATE_RE.finditer(text):
        name = m.group("name")
        columns: dict[str, bool] = {}
        fks: dict[str, FkSignature] = {}
        for raw in m.group("body").splitlines():
            # 每一行末尾都有逗号（最后一行除外）。先摘掉再匹配：
            # `_FK_RE` 是 `$` 锚定的，多一个逗号就匹配不上，而匹配不上会掉进
            # 下面的列分支、报一句「没能解析的列定义」——离原因很远。
            line = raw.strip().removesuffix(",").strip()
            if not line:
                continue
            fk = _FK_RE.match(line)
            if fk:
                constraint_name, signature = _fk_signature(fk)
                assert constraint_name not in fks, f"表 {name} 里约束名重复：{constraint_name}"
                fks[constraint_name] = signature
                continue
            if line.startswith(("PRIMARY KEY", "UNIQUE KEY", "KEY ")):
                continue
            col = _COLUMN_RE.match(line)
            assert col, f"没能解析的列定义：{line!r}（表 {name}）"
            columns[col.group("name")] = " NOT NULL" not in col.group("rest")
        tables[name] = ParsedTable(name, columns, fks)

    for m in _ALTER_FK_RE.finditer(text):
        table = tables[m.group("table")]
        constraint_name, signature = _fk_signature(m)
        assert constraint_name not in table.fks, f"约束名重复：{constraint_name}"
        table.fks[constraint_name] = signature
        table.later_fks.add(constraint_name)
    return tables


TABLES = parse_tables()


def model_fk_constraints(model) -> list[tuple[str | None, FkSignature]]:
    """模型那张表的每一条外键，形状与 `ParsedTable.fks` 逐字对齐。

    **名字可能是 `None`。** 模型里有 40 条单列外键是裸 `ForeignKey(...)` 写出来的
    （V1.0 那一批），而 `Base` 上没有 naming convention，所以
    `constraint.name is None`——真库那 40 条叫 `<表>_ibfk_<N>`，是 MySQL 自己编的，
    模型无从得知。所以「名字」这一半判据只对**显式命名过**的约束成立
    （下面 `test_the_named_foreign_keys_are_named_the_same_way`），
    而「形状」那一半对所有约束成立（按 `Counter` 比，见下一条用例）。
    """
    out: list[tuple[str | None, FkSignature]] = []
    for constraint in model.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        out.append(
            (
                constraint.name,
                (
                    tuple(element.parent.name for element in constraint.elements),
                    constraint.referred_table.name,
                    tuple(element.column.name for element in constraint.elements),
                ),
            )
        )
    return out


# --------------------------------------------------------------------------
# 空转自检：正则坏掉时下面每一条都会变成恒真，这一条先把它挡住
# --------------------------------------------------------------------------


def test_the_parser_actually_found_the_schema():
    """解析出来的规模不能为零。

    这是 CLAUDE.md §18 那条「扫到的处数 ≥ 12 的空转自检」的同一形状：
    `_CREATE_RE` 一旦匹配不上，`TABLES` 就是空字典，而下面所有"每个表都…"
    的断言在空集合上**全部为真**。门槛取 30/400/100 而不是精确的 34/438/120 ——
    精确值会在任何人加一张表时变红，而红的原因不是功能坏了。

    外键那一个门槛是 2026-09-19 补的：`_ALTER_FK_RE` 匹配不上时，那 7 条
    排在 `CREATE TABLE` 之后的外键会**静默消失**，而「形状集合相等」那一条
    在少了 7 条时照样能对上（模型侧也会跟着少 7 条吗？不会——所以它会红，
    但红在「模型里多出来 7 条」上，离「解析器坏了」很远）。这里直接数一条。
    """
    assert len(TABLES) >= 30, f"只解析出 {len(TABLES)} 张表，正则大概坏了"
    total_columns = sum(len(t.columns) for t in TABLES.values())
    assert total_columns >= 400, f"只解析出 {total_columns} 列，正则大概坏了"
    total_fks = sum(len(t.fks) for t in TABLES.values())
    assert total_fks >= 100, f"只解析出 {total_fks} 条外键，正则大概坏了"
    assert sum(len(t.later_fks) for t in TABLES.values()) >= 7, (
        "`ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY` 那一段一条都没解析出来"
    )


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
    """**按约束比，不是按列比。**

    判据是「形状的多重集相等」（`Counter`），不是集合。差别在两处，都是这次
    V1.2 才第一次真实存在的：

    - **一条约束可能有好几列**。`(school_id, grade_id) → grade (school_id, id)`
      与单列的 `school_id → grade` 只看第一个本地列时一模一样——旧写法会把
      两条塌成一条，于是「模型里两条、文件里一条」被读成「一致」。
    - **两条不同的约束可能同形**。`retest_plan` 的两条 `→ assessment_session`
      （`source_session_id` / `completed_session_id`）形状不同所以没事，
      但一个真的同形的情形（比如同时用 (`a`,`b`) 与 (`a`,`b`) 指向同一张表）
      只有数条数才看得见——**集合相等对重复无感，多重集相等才数得出来**。

    所以这条用例同时是「外键集合相等」与「外键条数相等」，后者不做成单独一条
    断言：它的失败信息在 `Counter` 的差里已经写明了「多了/少了几条」。
    """
    mismatched = {}
    for name, model in Base.metadata.tables.items():
        want = Counter(signature for _, signature in model_fk_constraints(model))
        got = Counter(TABLES[name].fks.values())
        if want != got:
            mismatched[name] = {
                "只在该文件里": [list(sig) for sig in (got - want).elements()],
                "只在模型里": [list(sig) for sig in (want - got).elements()],
            }
    assert not mismatched, f"外键不一致（按约束比）：{mismatched}"


def test_the_named_foreign_keys_are_named_the_same_way():
    """显式命名过的外键，名字也要逐字相同。

    为什么名字是契约：MySQL 会给**自动创建**的外键索引起 `<约束名>` 这个名字，
    而未来的 `op.drop_constraint("…")` 按名引用它。名字漂了没有任何东西看得见
    ——直到某一条迁移在真库上以 1091「check that column/key exists」收场。

    哪些算「显式命名过」：模型里 `constraint.name is not None` 的那些
    （40 条裸 `ForeignKey(...)` 的名字是 `None`，真库叫 `<表>_ibfk_<N>`，
    那是 MySQL 自己编的，模型无从得知，所以它们**不在这条判据里**）。
    反向不查：文件里那个 `<表>_ibfk_<N>` 名字对不对得上不在这一条——
    它由 `test_migrations_build_the_models.py` 拿 `information_schema` 真比。
    """
    mismatched = {}
    for name, model in Base.metadata.tables.items():
        parsed = TABLES[name].fks
        for constraint_name, signature in model_fk_constraints(model):
            if constraint_name is None:
                continue
            if parsed.get(constraint_name) != signature:
                mismatched[f"{name}.{constraint_name}"] = {
                    "文件里": list(parsed[constraint_name]) if constraint_name in parsed else None,
                    "模型里": list(signature),
                }
    assert not mismatched, f"外键的约束名对不上：{mismatched}"


def test_every_composite_foreign_key_is_explicitly_named_in_the_model():
    """多列外键必须在模型里 `name=` 出来（值长什么样不管）。

    这是上面那条的**反向补漏**：模型侧那 40 条裸 `ForeignKey(...)` 不在那条判据里
    （它们 `name is None`），于是「新加一条复合外键、忘了起名」是唯一能溜过去的
    情形——而复合外键恰恰是最需要名字的一类，迁移里改它（`op.drop_constraint`）
    几乎必然要按名引用。

    **判据是「模型里有没有名字」，不是「文件里那个名字长什么样」。**
    第一版写的是「文件里叫 `<表>_ibfk_<N>` 就是没起名」，而 `student_ibfk_4` /
    `student_ibfk_5` / `class_group_ibfk_3` 这三条**名字就是长成那样的**——
    它们刻意照抄了 MySQL 会给自动约束编的那一串，好让手工 DDL 建出来的库与
    迁移建出来的库逐字相同（迁移 0014 里 `op.create_foreign_key('student_ibfk_4', …)`
    是显式传的）。那条判据于是把三个**正确的**东西报成了错的。
    """
    offenders = [
        f"{name}: {[element.parent.name for element in constraint.elements]} → {constraint.referred_table.name}"
        for name, model in Base.metadata.tables.items()
        for constraint in model.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name is None
        and len(constraint.elements) > 1
    ]
    assert not offenders, (
        "这些复合外键在模型里没有名字（真库会看到 MySQL 自动编的 `<表>_ibfk_<N>`，"
        "而那个编号取决于约束的创建次序）：\n" + "\n".join(offenders)
    )


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
    当时的测试库更看不见（内存 sqlite 默认不检查外键，CLAUDE.md 缺口 3，
    2026-09-19 已关闭）。**次序这一条仍然要留着**：它是唯一在开跑之前就能发现
    这件事的地方——等到真去建表时，报出来的是 MySQL 的英文错误。

    判据：按文件里的先后建位置表，每个外键指向的父表必须排在子表**之前**。
    自引用（父子同表）不算违例，所以是 `<` 而不是 `<=` 的反面写法。

    **写在 `ALTER TABLE` 里的那 7 条豁免**（`ParsedTable.later_fks`）：
    `assessment_import_row ↔ assessment_external_result` 是一个**二元环**，
    环无法线性化，所以那几条只能排在所有 `CREATE TABLE` 之后。豁免的前提
    由下一条 `test_the_alter_section_comes_after_every_create_table` 钉住——
    没有那一条，「豁免」会变成一个能把次序错误藏起来的口袋。
    """
    order = {name: i for i, name in enumerate(TABLES)}
    violations = [
        f"{t.name} 第 {order[t.name]} 位 → 引用 {parent} 第 {order[parent]} 位"
        for t in TABLES.values()
        for constraint_name, (_, parent, _) in t.fks.items()
        if constraint_name not in t.later_fks
        and parent != t.name
        and order[parent] > order[t.name]
    ]
    assert not violations, "建表次序把子表排在了父表前面：\n" + "\n".join(violations)


def test_the_alter_section_comes_after_every_create_table():
    """`ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY` 必须在**所有**建表之后。

    上一条放行了它们，放行的理由只有一个：这一段排在全部 `CREATE TABLE` 之后，
    所以父表必然已经存在。谁把其中一条挪到中间（比如挪到它自己那张表的建表语句
    后面，看着更整齐），那个理由当场失效——而两张表都建得出来的情形下，
    错误的次序不会报错，只有在环上那一条才会以 1824 收场。

    判据：**最后一条 `CREATE TABLE` 的结尾**必须排在最前面那条 `ALTER TABLE`
    之前。不逐条 ALTER 判「父表建了没有」——那件事上一条已经在做了（豁免的
    语义就是「它一定成立」），这里补的只有「凭什么」。
    """
    text = sql_without_comments()
    creates = list(_CREATE_RE.finditer(text))
    alters = list(_ALTER_FK_RE.finditer(text))
    assert creates and alters, "建表段或 ALTER 段没解析出来，这条用例失去了判据"
    last_create_end = creates[-1].end()
    first_alter_start = alters[0].start()
    assert first_alter_start > last_create_end, (
        f"ALTER 段插进了建表段中间：第 {text[:first_alter_start].count(chr(10)) + 1} 行处"
        f"就有 `ALTER TABLE … ADD CONSTRAINT`，而最后一条 `CREATE TABLE` 在"
        f"第 {text[:last_create_end].count(chr(10)) + 1} 行才结束。\n"
        f"那一段的全部依据是「父表已经建好了」，插在中间就没有这个依据了。"
    )


def test_the_constraint_names_migrations_depend_on_are_present():
    """迁移按**名字**引用约束，那些名字因此是契约的一部分。

    这一条钉住四个具体的名字，每一个都有来路：
      - `ix_student_care_case_student_id`：迁移 0012 删掉那个 UNIQUE 之前**必须先建它**
        ——它兼作 `student_id` 外键的索引，MySQL 会以 1553 拒绝删一条外键正在用的索引；
      - `ix_audit_log_student_id`：迁移 0006 显式建的，不是 `index=True` 的自动名；
      - `fk_session_task` / `fk_audit_log_student_id`：V1.0 那两个**人工命名**的外键。
        （V1.2 之后人工命名的外键多了 80 条，见 `test_every_composite_foreign_key_is_
        explicitly_named_in_the_model` 与 `test_the_named_foreign_keys_are_named_the_
        same_way`；这一条留着的理由是它钉的是**索引**与那两个历史名字，不是数量。）
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
