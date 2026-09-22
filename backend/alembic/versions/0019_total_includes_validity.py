"""总分口径变更：效度题也计入总分，所以要换一个生效的规则版本

按学校的口径，`assessment_result.total_score` 从「90 道非效度题里答『是』的条数」
（0–90）改成「**全部 100 题**里答『是』的条数」（0–100）。`scale_engine.calculate()`
那一行已经改了，而**这一条迁移是那行代码的另一半**——理由与 §6 一字不差：

> `assessment_result.rule_version` 要能回答「这条结果当时按什么标准判定」。

不换版本号的话，一所升级上来的学校里，升级**之前**算出来的结果（按 90 口径）与升级
**之后**算出来的结果（按 100 口径）会共用同一个 `MHT-RULE-1.1.0` 标签，而这两批数的
总分根本不是同一把尺子量的——那正是 `rule_version` 这一列存在的全部意义。
`DEFAULT_RULE_CONFIG` 改了**不会**改任何已有库里的 `scale_rule.config_json`：发布过的
规则是**数据**，代码里的默认值只对全新装出来的库有效。所以这条迁移不是可选的，而且它
失效的方式是**静默的**——`make test` 每次都建全新库、从 `seed.py` 拿新默认值，全绿。

## 做两件事，对每一个「当前生效」的 MHT 评分规则行

1. **新建一行**：`rule_version` 按 `_bump_version` 的口径 +1（`MHT-RULE-1.1.0` →
   `MHT-RULE-1.1.1`），`config_json` 原样照抄、只把最后一档总分分段的上界补到满分。
2. **旧行置 `RETIRED`，保留不删**（§6：「旧版本标 `RETIRED` 保留；已有结果不受影响」）。

加宽那一档**不改变任何一条判定**，是有意的：`ScaleRuleConfig.validity_band_fallback`
返回 `total_bands[-1].code`，而 `total_level(self, total_score)` 用它兜底，所以
91–100 分本来就会落到 `KEY_ATTENTION`。改它只是让规则编辑界面上的这一段不再自相矛盾
（那里显示「65-90」，而实际总分能到 100）。

## 为什么是纯 SQL 常量，不是 Python 里循环着改

`deploy/build_migration_sql.py` 用**离线渲染**（`alembic upgrade 0012:head --sql`）生成
`backend/sql/upgrade_from_v1_0_0.sql`，而离线模式下 `op.get_bind()` 是 `MockConnection`、
它的 `execute()` 返回 `None`、下一句 `.fetchall()` 当场 `AttributeError`（`0013` / `0014`
的 `_precheck()` 开头那句 `if context.is_offline_mode(): return` 就是为这件事写的）。
**但 `op.execute("静态 SQL 字符串")` 在离线模式下原样吐进脚本**（`0015` 的 UPDATE 与
`0017` 的三条 UPDATE 就在那份文件里）。所以这里一句 Python 循环都不能有：凡是需要的
动态判断，都得交给 MySQL 自己的 `JSON_SET` / `JSON_LENGTH` / `SUBSTRING_INDEX` 表达式。
`upgrade_from_v1_0_0.sql` 是手边没有能跑的 Python 的那台机器**唯一**的升级路。

**整份脚本里一个 `%` 都不许有**：`op.execute` 把字符串交给 SQLAlchemy 的 `text()`，而
pymysql 的 paramstyle 是 `pyformat`——一个字面 `%` 会被当成参数占位符。这就是版本号
的判断写成 `LOCATE('.', …) > 1` 而不是 `LIKE '%.%'` 的唯一原因（形状与 `:` 同理，
SQL 里不能出现它们）。

## 刻意**不加** `PRECHECKS`

`deploy/build_migration_sql.py` 会把迁移上的 `PRECHECKS` 常量提升到那份增量 SQL 的
【检查】段，而【检查】段失败是**中止**（`CALL xlp_check_empty` 抛 `45000`）。这条迁移
会撞的唯一约束是 `uq_scale_rule_version`（`(scale_id, rule_version)`），而裸
`alembic upgrade head` 撞它会**当场报 `1062` 中断**——两条路对同一份数据各说各话：
一条说「有人工裁决的余地」，另一条已经停了。所以两边都按**跳过**写（【2/4】那条
`DELETE`），行为一致。真撞上 1062 也仍然是 §21 那句「数据里真有反例，人工处置」。

## 被否掉的三个备选，理由都记在这里

* **就地改 `rule_version`（把 1.1.0 那行直接改名成 1.1.1）**：历史结果的标签会**悬空**
  ——库里再没有任何一行叫 `MHT-RULE-1.1.0`，而 `assessment_result` 里那一串字符串
  指着一个不存在的规则。§6 明写「旧版本标 `RETIRED` **保留**」，保留的就是这个。
* **写死 `MHT-RULE-1.1.0 → MHT-RULE-1.1.1`**：用过「量表评分规则」页面的学校已经停在
  1.1.1 或更后面了，写死会**整批漏掉**它们——而算法对每一行都变了，每一行都该被取代。
  所以版本号是**算出来的**，不是查表查出来的。
* **按 `status` 认「刚退位的那一批」的两段式**：判据会依赖被我们改动的 `status`
  本身。`INSERT` 之后 `NOT EXISTS(<加一版已存在>)` 对旧行翻为假，而只按 `status` 认
  又会与**历史** RETIRED 行（学校自己编辑过规则留下的）混在一起。所以走临时表：
  动手之前先把「要动的那几行」快照下来，后面的每一句都只认这个快照。

## 降级是尽力而为的，而且**一行都不删**

它把「当前生效的那一行」退位、让同一量表下 `id` 最大的 RETIRED 行接位。旧行的
`config_json` 从头到尾没被改过（我们新建了一行，而不是修改原来的），所以没有什么
「把加宽改回去」的动作——这正是「只加不改」的好处的另一面。

两句 `UPDATE` 都带 `prev_id IS NOT NULL`：**没有 RETIRED 行可接的时候什么都不做**。
那正是全新安装的库跑完这条迁移又降级的样子（种子里那行的版本号本来就是新的，没有
「上一版」），而不带这个判据会把它置成 RETIRED、留下一张没有生效规则的量表——
引擎会回落到 `DEFAULT_RULE_CONFIG`，规则编辑页显示「未配置」。降级本来就不是常规
路径，但它不该在一条并不需要降级的库上把东西弄坏。

Revision ID: 0019_total_includes_validity
Revises: 0018_row_conflict_resolution
Create Date: 2026-09-21
"""

from alembic import op

revision = "0019_total_includes_validity"
down_revision = "0018_row_conflict_resolution"
branch_labels = None
depends_on = None

#: 【1/4】把「当前生效」的 MHT 评分规则整行抄进临时表，顺带算好它的新版本号与加宽后的
#: 配置。**动手之前先快照**是这条迁移的全部结构：下面的每一句都只认 `xlp_rule_bump`，
#: 没有一句依赖 `status` 或 `rule_version` 在写入过程中的中间状态。
#:
#: 版本号那一段是 `scale_rule_service._bump_version` 的 SQL 镜像：
#: `MHT-RULE-1.1.0` → 取最后一段数字 +1 → `MHT-RULE-1.1.1`；最后一段不是数字
#: （或最后一个点前面什么都没有）时接 `-2`。`LOCATE('.', …) > 1` 而不是
#: `LIKE '%.%'`——理由见模块 docstring 里那句「一个 `%` 都不许有」。
#:
#: 配置那一段只加宽**总分**分段的最后一档，`dimension_levels` 一个字不动（维度分没变）。
#: 那三个 `JSON_LENGTH IS NULL / = 0` 的守卫**不是装饰**：路径不存在时
#: `JSON_LENGTH` 回 NULL，`NULL - 1` 是 NULL，`CONCAT('$.total_levels[', NULL, '].max')`
#: 是 NULL，`JSON_SET(json, NULL, v)` 也回 NULL —— 插进 NOT NULL 的 `config_json`
#: 当场 `1048`；空数组那一条则会让路径变成 `$.total_levels[-1].max`（MySQL 的 JSON
#: 路径不接受负下标）。退化的那两行**原样保留配置**：加宽不了就不加宽，而不是猜一个。
#:
#: `COALESCE(…, 100)` 是 `rule_config_from_json` 那句
#: `int(raw.get("question_count") or DEFAULT_RULE_CONFIG.question_count)` 的镜像——
#: 缺 `question_count` 的配置在引擎里按 100 算，所以这里也按 100 补上界。写死 100
#: 是有意的：这条迁移是 2026-09-21 那一刻的历史产物，它该钉住**当时**的满分。
#:
#: 加宽用的是**覆盖**、不是 `GREATEST`：总分上限就是满分，学校若自己填了一个更大的
#: 上界，那是填错了（分值到不了那里），缩回满分是一次写明的覆盖，不是静默改动。
#: 而且两者对判定完全等价——`classify` 只问 `band.min <= score <= band.max`，
#: 分数永远到不了 100 以上。
SNAPSHOT = (
    "CREATE TEMPORARY TABLE xlp_rule_bump AS "
    "SELECT r.id AS old_id, "
    "       r.scale_id AS scale_id, "
    "       CASE "
    "         WHEN LOCATE('.', r.rule_version) > 1 "
    "          AND SUBSTRING_INDEX(r.rule_version, '.', -1) REGEXP '^[0-9]+$' "
    "         THEN CONCAT("
    "                LEFT(r.rule_version, "
    "                     CHAR_LENGTH(r.rule_version) "
    "                     - CHAR_LENGTH(SUBSTRING_INDEX(r.rule_version, '.', -1))), "
    "                CAST(SUBSTRING_INDEX(r.rule_version, '.', -1) AS UNSIGNED) + 1) "
    "         ELSE CONCAT(r.rule_version, '-2') "
    "       END AS new_version, "
    "       CASE "
    "         WHEN JSON_LENGTH(r.config_json, '$.total_levels') IS NULL "
    "           OR JSON_LENGTH(r.config_json, '$.total_levels') = 0 "
    "         THEN r.config_json "
    "         ELSE JSON_SET("
    "                r.config_json, "
    "                CONCAT('$.total_levels[', "
    "                       JSON_LENGTH(r.config_json, '$.total_levels') - 1, '].max'), "
    "                COALESCE(CAST(JSON_EXTRACT(r.config_json, '$.question_count') AS UNSIGNED), 100)) "
    "       END AS new_config "
    "FROM scale_rule AS r "
    "WHERE r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE'"
)

#: 【2/4】新版本号已经被占用（同一量表下已经有那一行）的**跳过**，不中止——理由见模块
#: docstring 那一节「刻意不加 PRECHECKS」。
#:
#: 实测过的触发形状只有一种：**升过、又降过、再升一次**。降级把新行置回 `RETIRED`、
#: 旧行接回 `ACTIVE`，于是「当前生效」那一行的 `new_version` 又指着一个已存在的行。
#: 学校自己在页面上编辑规则**走不到这里**：`_bump_version` 每次都在最高位 +1，编辑完
#: 生效的永远是最高那一行，不可能出现「生效的是 X、而 X+1 已经存在」。
#:
#: 跳过之后库里停在旧版本号上、最后一档仍是 `max: 90`——**行为上不变**（91–100 分本来
#: 就由 `validity_band_fallback` 兜进最后一档），只是规则编辑页那一段写着 65-90。
#: **不去改那一行的配置**：它可能正被历史结果引用着，改它等于追溯改写那些结果的判定
#: 标准，而 §6 保留旧版本行的全部意义就是不许发生这件事。
SKIP_TAKEN = (
    "DELETE b FROM xlp_rule_bump AS b "
    "WHERE EXISTS (SELECT 1 FROM scale_rule AS s "
    "              WHERE s.scale_id = b.scale_id AND s.rule_version = b.new_version)"
)

#: 【3/4】建新行。`created_at` / `updated_at` 不写（`server_default` 是 `now()`，交给
#: 客户那台库自己填——它们回答的是「这一行什么时候落进**这个**库」），`id` 也不写
#: （自增）。`scale_rule` 上没有 `updated_by` 之类的列，所以没有别的来源要在库里找。
INSERT_NEW = (
    "INSERT INTO scale_rule (scale_id, rule_version, rule_type, config_json, status) "
    "SELECT b.scale_id, b.new_version, 'MHT_SCORING', b.new_config, 'ACTIVE' "
    "FROM xlp_rule_bump AS b"
)

#: 【4/4】旧行退位，**保留不删**。写在 `INSERT` 之后：真撞上什么而中断时，库里是
#: 「新旧两条都 `ACTIVE`」而不是「一条生效规则都没有」——前者是 `active_rule()` 早就
#: 容得下的形状（它按 `id.desc()` 取最新那一条，也就是刚建的那一行），后者会让规则
#: 编辑页显示未配置。
RETIRE_OLD = "UPDATE scale_rule SET status = 'RETIRED' WHERE id IN (SELECT old_id FROM xlp_rule_bump)"

DROP_SNAPSHOT = "DROP TEMPORARY TABLE xlp_rule_bump"

#: 降级那一步的快照。`UPDATE scale_rule … JOIN (SELECT … FROM scale_rule)` 会撞
#: `1093`（不能在子查询里读正在被更新的那张表），而临时表是**另一张表**——这就是它
#: 存在的理由，与【1/4】同一条。
#:
#: `prev_id` 取同一量表下 `id` 最大的 RETIRED 行：这一条迁移刚刚退位的那一行，
#: `id` 就是最大的（它是库里最后写进去的一行）。
SWAP_SNAPSHOT = (
    "CREATE TEMPORARY TABLE xlp_rule_swap AS "
    "SELECT r.id AS new_id, "
    "       (SELECT MAX(s.id) FROM scale_rule AS s "
    "         WHERE s.scale_id = r.scale_id "
    "           AND s.rule_type = r.rule_type "
    "           AND s.status = 'RETIRED') AS prev_id "
    "FROM scale_rule AS r "
    "WHERE r.rule_type = 'MHT_SCORING' AND r.status = 'ACTIVE'"
)

#: 两句都带 `prev_id IS NOT NULL`——没有 RETIRED 行可接的时候就什么都不做。理由见模块
#: docstring 末尾那一节：全新安装的库（种子那行本来就是新版本号）降级时，把唯一的
#: 生效行置成 RETIRED 会留下一张没有规则的量表。
RETIRE_NEW = (
    "UPDATE scale_rule SET status = 'RETIRED' "
    "WHERE id IN (SELECT new_id FROM xlp_rule_swap WHERE prev_id IS NOT NULL)"
)

RESTORE_OLD = (
    "UPDATE scale_rule SET status = 'ACTIVE' "
    "WHERE id IN (SELECT prev_id FROM xlp_rule_swap WHERE prev_id IS NOT NULL)"
)

DROP_SWAP = "DROP TEMPORARY TABLE xlp_rule_swap"


def upgrade() -> None:
    op.execute(SNAPSHOT)
    op.execute(SKIP_TAKEN)
    op.execute(INSERT_NEW)
    op.execute(RETIRE_OLD)
    op.execute(DROP_SNAPSHOT)


def downgrade() -> None:
    op.execute(SWAP_SNAPSHOT)
    op.execute(RETIRE_NEW)
    op.execute(RESTORE_OLD)
    op.execute(DROP_SWAP)
