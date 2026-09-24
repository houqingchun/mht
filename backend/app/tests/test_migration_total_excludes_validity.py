"""`0020_total_excludes_validity` 在真 MySQL 上的规则切换。"""

from app.tests.mysql_support import downgrade_to, run_migrations, throwaway_database
from app.tests.test_migration_total_includes_validity import (
    BASE_REVISION,
    OLD_RULE_VERSION,
    _add_scale_with_rule,
    _revision,
    _rules,
)

INCLUDES_REVISION = "0019_total_includes_validity"
EXCLUDES_REVISION = "0020_total_excludes_validity"


def test_excluding_validity_publishes_a_new_rule_and_preserves_history():
    with throwaway_database(with_schema=False) as url:
        run_migrations(url, BASE_REVISION)
        _add_scale_with_rule(url, scale_code="MHT", rule_version=OLD_RULE_VERSION)
        run_migrations(url, INCLUDES_REVISION)

        before = _rules(url)
        assert before["MHT-RULE-1.1.1"]["config"]["total_levels"][-1]["max"] == 100

        run_migrations(url, EXCLUDES_REVISION)
        rules = _rules(url)

        assert _revision(url) == EXCLUDES_REVISION
        assert set(rules) == {OLD_RULE_VERSION, "MHT-RULE-1.1.1", "MHT-RULE-1.1.2"}
        assert rules["MHT-RULE-1.1.1"]["status"] == "RETIRED"
        assert rules["MHT-RULE-1.1.1"]["config"] == before["MHT-RULE-1.1.1"]["config"]
        assert rules["MHT-RULE-1.1.2"]["status"] == "ACTIVE"
        assert rules["MHT-RULE-1.1.2"]["config"]["total_levels"][-1]["max"] == 90

        downgrade_to(url, INCLUDES_REVISION)
        downgraded = _rules(url)
        assert downgraded["MHT-RULE-1.1.1"]["status"] == "ACTIVE"
        assert downgraded["MHT-RULE-1.1.2"]["status"] == "RETIRED"
