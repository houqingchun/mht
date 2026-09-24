"""`0019_total_includes_validity` 在**真 MySQL** 上跑一遍。

这条迁移是一条**纯 SQL 常量**的数据迁移（`op.execute(静态字符串)`），而它的两个来源
都不在解析器手里：版本号那一段是 `scale_rule_service._bump_version` 的 SQL 镜像
（`LOCATE` / `SUBSTRING_INDEX` / `REGEXP` / `CONCAT` 拼出来的），配置那一段是
`JSON_SET` + `JSON_LENGTH` 拼出来的路径。**拼错的时候是无声的**——`JSON_SET` 一条走偏
就整段原样保留，而屏幕上一切正常（CLAUDE.md §18 那条「在 PowerShell 上写半吊子词法器，
错的时候是无声的」是同一条道理，只是换了个语言）。所以判据只能是真库上跑一遍，看它
到底把哪一行改成了什么。

三条路各管一段，都不是同一条的重复：

| 用例 | 管什么 |
|---|---|
| `..._replaces_the_active_rule_with_a_bumped_copy` | 升级：新行建出来、旧行退位、配置只加宽了最后一档 |
| `..._the_version_arithmetic_matches_the_service` | 版本号那一段确实是 `_bump_version` 的镜像（三种形状） |
| `..._re_running_after_a_downgrade_skips_instead_of_failing` | 【2/4】那条 `SKIP_TAKEN`：升级→降级→再升级 |

**夹具形状**：`throwaway_database(with_schema=False)` 拿一个**空库**，自己
`run_migrations(url, "0018_…")` 停在迁移前那一版，插一行旧规则，再只升到 0019。
不能拿 `conftest.py` 的 `db_session`（那是已经 head 的库，0019 早就跑完了），
也不能跑 `with_schema=True`（那会直接到 head）。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.services.scale_rule_service import _bump_version
from app.tests.mysql_support import downgrade_to, engine_for, run_migrations, throwaway_database

#: 迁移前那一版。写成 revision id 而不是「head~1」：这条用例要证明的东西就是
#: 「从这一版升到 head 会发生什么」，把起点写成一个相对位置会让它在链上多一条迁移
#: 之后**悄悄换一个起点**。
BASE_REVISION = "0018_row_conflict_resolution"
NEW_REVISION = "0019_total_includes_validity"

OLD_RULE_VERSION = "MHT-RULE-1.1.0"

#: 升级**前**的配置：刻意**手写**，不从 `DEFAULT_RULE_CONFIG` 拿。那个常量 2026-09-21
#: 起最后一档已经是 `max: 100`，拿它当输入，这条用例就退化成在比「100 == 100」——
#: 迁移什么都不做也全绿。
#:
#: 键名必须是 JSON 那一套（`total_levels` / `dimension_levels`），**不是** Python 字段名
#: （`total_bands` / `dimension_bands`）：`rule_config_from_json` 的 `bands()` 在找不到
#: 那个键时**静默回落到整张默认表**，所以键名写错不会报错，只会让这个夹具悄悄变成
#: 「一份用默认值写的配置」。最后一档的 `max` 同理必须显式写 90。
OLD_CONFIG = {
    "question_count": 100,
    "validity_questions": [82, 84, 86, 88, 90, 92, 94, 96, 98, 100],
    "key_questions": [85, 97],
    "validity_retest_threshold": 7,
    "total_levels": [
        {"code": "GENERAL_RANGE", "min": 0, "max": 55},
        {"code": "NEEDS_ATTENTION", "min": 56, "max": 64},
        {"code": "KEY_ATTENTION", "min": 65, "max": 90},
    ],
    "dimension_levels": [
        {"code": "LOW", "min": 0, "max": 3},
        {"code": "MEDIUM", "min": 4, "max": 7},
        {"code": "HIGH", "min": 8, "max": 15},
    ],
}


def _add_scale_with_rule(url, *, scale_code: str, rule_version: str, config: dict | None = None) -> None:
    """在 0018 的库上插一行已发布量表 + 一行 `ACTIVE` 的 MHT 评分规则。

    迁移只按 `rule_type = 'MHT_SCORING' AND status = 'ACTIVE'` 挑行，**不看量表编码**，
    所以多个量表各带一行是给「一次升级处理多种版本号形状」用的（见算术那条用例）。
    """
    payload = OLD_CONFIG if config is None else config
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            scale_id = connection.execute(
                text(
                    "INSERT INTO assessment_scale (code, name, version, status) "
                    "VALUES (:code, :name, :version, 'PUBLISHED')"
                ),
                {
                    "code": scale_code,
                    "name": f"{scale_code} 测试量表",
                    "version": f"{scale_code}-1.0.0",
                },
            ).lastrowid
            connection.execute(
                text(
                    "INSERT INTO scale_rule (scale_id, rule_version, rule_type, config_json, status) "
                    "VALUES (:scale_id, :rule_version, 'MHT_SCORING', :config_json, 'ACTIVE')"
                ),
                {
                    "scale_id": scale_id,
                    "rule_version": rule_version,
                    "config_json": json.dumps(payload, ensure_ascii=False),
                },
            )
    finally:
        engine.dispose()


def _rules(url) -> dict[str, dict]:
    """库里全部规则行，按 `rule_version` 索引。

    读的是原始 `text()` 而不是 ORM，所以 `config_json` 回来的是字符串（pymysql 不做
    JSON 反序列化）——这里两种都收下，免得哪天换成 ORM 查询时这条夹具悄悄坏掉。
    """
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT rule_version, status, config_json FROM scale_rule ORDER BY id")
            ).mappings()
            return {
                row["rule_version"]: {
                    "status": row["status"],
                    "config": row["config_json"]
                    if isinstance(row["config_json"], dict)
                    else json.loads(row["config_json"]),
                }
                for row in rows
            }
    finally:
        engine.dispose()


def _revision(url) -> str:
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def upgraded():
    """一个从 0018 升到 head 的一次性库，只建一次（前三条用例共用）。

    建成 module 级不是图快，是因为它**只读**：三条用例都只 `SELECT`，谁也不改这个库，
    所以「共用一份升级结果」与「各建一份」看到的东西逐字节相同。真正会写库的那条
    （降级再升级）自己另起一个库。
    """
    with throwaway_database(with_schema=False) as url:
        run_migrations(url, BASE_REVISION)
        _add_scale_with_rule(url, scale_code="MHT", rule_version=OLD_RULE_VERSION)
        run_migrations(url, NEW_REVISION)
        yield url


def test_the_stamp_lands_on_the_head_revision(upgraded):
    """先证明这个库真的走完了 0019——否则下面三条断的是一个**没升级**的库。

    少了它，「旧行退位了吗」这类断言在夹具坏掉时不会红：那时候库里只有我们插进去的
    那一行 `ACTIVE`，而它会满足「恰好一行生效」而与升级无关。
    """
    assert _revision(upgraded) == NEW_REVISION


def test_the_active_rule_is_replaced_by_a_bumped_copy(upgraded):
    """升级的四件事：新行建出来、旧行退位、旧行的配置**一个字没动**、生效的只有一行。"""
    rules = _rules(upgraded)

    assert set(rules) == {OLD_RULE_VERSION, "MHT-RULE-1.1.1"}, "升级应当**新增**一行而不是改名"
    assert rules[OLD_RULE_VERSION]["status"] == "RETIRED"
    assert rules["MHT-RULE-1.1.1"]["status"] == "ACTIVE"
    assert [row["status"] for row in rules.values()].count("ACTIVE") == 1

    # 旧行**逐键**等于插进去的那一份——迁移只加宽新行的分段，绝不追溯改写旧行。
    # （§6 保留旧版本行的全部意义就是不许发生这件事：它可能正被历史结果引用着。）
    assert rules[OLD_RULE_VERSION]["config"] == OLD_CONFIG

    new_config = rules["MHT-RULE-1.1.1"]["config"]
    assert new_config["total_levels"][-1] == {"code": "KEY_ATTENTION", "min": 65, "max": 100}
    assert _lawyer_would_say(new_config) == _lawyer_would_say(OLD_CONFIG)


def _lawyer_would_say(config: dict) -> dict:
    """「除了最后一档的上界，其余一个字都没动」的判据。

    写成「把最后一档的 `max` 抹掉再逐键比」而不是「逐字段列举哪些该变」：后者要求这份
    夹具跟着 `ScaleRuleConfig` 的字段表一起维护，加一个字段就要回来改一次，而漏改的表现
    是**这条断言查不到那个新字段**——一条会随时间变松的断言。抹掉法只依赖一件事：
    `total_levels[-1].max` 是这次改动**唯一**该动的地方。
    """
    stripped = json.loads(json.dumps(config))
    stripped["total_levels"][-1].pop("max")
    return stripped


def test_the_version_arithmetic_matches_the_service(upgraded):
    """版本号那一段是 `_bump_version` 的镜像——三种形状各验一次。

    直接拿 SQL 算出来的结果与 Python 函数比，而不是写死三个期望值：这条公式的**定义**
    在 `scale_rule_service._bump_version` 里，写死就等于在这里再抄一份，两份漂了的时候
    这条用例只会跟着一起漂。三种形状各自对应一个真实的分支：

    * `MHT-RULE-1.1.0` —— 常规：取最后一段数字 +1；
    * `MHT-RULE-1.1.0-RULE-DRAFT` —— 尾部不是数字，接 `-2`（§6 那类名字的病态形状）；
    * `MHT` —— 一个点都没有，`rpartition` 的前缀为空，同样接 `-2`。
    """
    shapes = ["MHT-RULE-1.1.0", "MHT-RULE-1.1.0-RULE-DRAFT", "MHT"]
    with throwaway_database(with_schema=False) as url:
        run_migrations(url, BASE_REVISION)
        for index, version in enumerate(shapes):
            _add_scale_with_rule(url, scale_code=f"MHT{index}", rule_version=version)
        run_migrations(url, NEW_REVISION)

        bumped = set(_rules(url)) - set(shapes)
        assert bumped == {_bump_version(version) for version in shapes}
        # 三条都验过之后才敢说「镜像成立」：只有一种形状时，一个把 `-2` 那一支写错的
        # 实现照样绿。
        assert len(bumped) == len(shapes)


def test_re_running_after_a_downgrade_skips_instead_of_failing():
    """【2/4】那条 `SKIP_TAKEN`：**升过、降过、再升一次**不该撞 `1062`。

    降级把新行置回 `RETIRED`、旧行接回 `ACTIVE`，于是「当前生效」那一行的
    `new_version` 又指着库里已经存在的行。没有那条 `DELETE`，`INSERT` 会撞
    `uq_scale_rule_version` 当场中断——而一台学校的升级脚本停在一句英文的
    `Duplicate entry` 上，看不出是「这条迁移被跑过第二遍」。

    跳过之后库里**停在旧版本号上**（最后一档仍是 90），这是已知的残余状态、也是可接受
    的：`validity_band_fallback` 让 91–100 分本来就落进最后一档，**行为上不变**。所以这里
    断的是「不崩、不建新行、不改旧行」，而不是断它加宽了。
    """
    with throwaway_database(with_schema=False) as url:
        run_migrations(url, BASE_REVISION)
        _add_scale_with_rule(url, scale_code="MHT", rule_version=OLD_RULE_VERSION)

        run_migrations(url, NEW_REVISION)
        assert set(_rules(url)) == {OLD_RULE_VERSION, "MHT-RULE-1.1.1"}

        # 这里**不能**写 `run_migrations(url, BASE_REVISION)`：`alembic upgrade <更老的
        # revision>` 在已经处于 head 的库上返回**空集**——退出码 0、不报错、什么都不做
        # （`script._upgrade_revs`，见 `mysql_support.downgrade_to` 的 docstring）。
        # 这条用例此前就是这么写的，所以它跑的一直是一次无声的空转，而下面那两句断言
        # 拿到的是一个**没降过级**的库：`OLD` 仍是 RETIRED —— 那是夹具坏了，不是迁移坏了。
        downgrade_to(url, BASE_REVISION)
        downgraded = _rules(url)
        assert downgraded[OLD_RULE_VERSION]["status"] == "ACTIVE"
        assert downgraded["MHT-RULE-1.1.1"]["status"] == "RETIRED"

        # 这一句就是本用例的主题：它以前会是 IntegrityError。
        run_migrations(url, NEW_REVISION)

        rules = _rules(url)
        # 一行没多、一行没少——降级**不删行**（§6：旧版本保留），再升级也不建新行。
        assert set(rules) == {OLD_RULE_VERSION, "MHT-RULE-1.1.1"}
        assert rules[OLD_RULE_VERSION]["status"] == "ACTIVE"
        assert rules["MHT-RULE-1.1.1"]["status"] == "RETIRED"
        assert [row["status"] for row in rules.values()].count("ACTIVE") == 1
        # 生效的那一行**配置没被改**：加宽不了就不加宽，而不是猜一个（迁移里那三个
        # `JSON_LENGTH IS NULL / = 0` 守卫是同一个态度）。
        assert rules[OLD_RULE_VERSION]["config"] == OLD_CONFIG
