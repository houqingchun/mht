"""Operator-editable configuration.

Design notes
------------
* `DEFAULTS` is the source of truth for what a setting is, what type it holds,
  and what an unconfigured installation behaves like. The `system_setting`
  table only stores *deviations* from it.
* Reads fail safe: a missing row, an unknown namespace, or a database error all
  fall back to `DEFAULTS`. Never fail open to "no configuration at all", which
  would blank out the UI.
* Only namespaces and keys present in `DEFAULTS` may be written, so the table
  cannot accumulate junk that nothing reads.

This is deliberately NOT a home for scoring thresholds. Those belong to a scale
rule version (`scale_rule.config_json`) because they must be versioned alongside
the instrument they score — a threshold change must not retroactively alter
historical results.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.setting import SystemSetting

DEFAULTS: dict[str, dict[str, Any]] = {
    "org": {
        "school_name": "青禾实验学校",
        "brand_name": "心晴",
        "brand_subtitle": "心理测评与关怀平台",
        "counselling_room": "综合楼3层305室",
        "counselling_hours": "周一至周五 12:30—17:30",
        "counselling_contact": "陈老师 · 分机8305",
    },
    # These are de-facto enums that were previously free-text inputs, which let
    # the same concept be recorded under several different strings.
    "care": {
        "follow_up_types": ["心理老师访谈", "支持性辅导", "一般观察", "复测沟通", "其他"],
        "contact_channels": ["电话", "到校会面", "线上沟通"],
        "contact_results": ["已联系", "未接通", "已约时间"],
        "support_statuses": ["愿意配合", "需要继续沟通", "信息不足"],
        "close_reasons": [
            "完成阶段跟进并进入一般观察",
            "复测结果达到关闭条件",
            "学生已转出",
            "其他经审核原因",
        ],
        "retest_reasons": ["重点维度趋势复测", "跟进后效果复测", "效度异常复测"],
        "review_results": [
            "建立持续关注档案",
            "建议复测",
            "当前进入一般观察",
            "信息不足，继续了解",
        ],
        "review_actions": ["安排下次跟进", "建议复测", "转介校内资源", "持续观察"],
        "case_owner_fallback": "未分配",
    },
    "export": {
        "purposes": ["校内心理工作跟进", "经批准的工作汇报", "复测任务准备"],
        # Mandatory justification before key-question answers are returned.
        "key_question_reasons": ["执行人工复核", "处置紧急工作事项", "核验历史记录"],
        # TODO_BUSINESS_CONFIRMATION: 导出文件的**默认有效期**与**最大行数**是
        # 需求说明书 §16.10 留下的未决项（原话就在那里，没有给数），所以这两个值是
        # **占位**，不是裁决。做法与 `web_dir` 那条「新能力不许动既有行为」一致：
        # 机制先建起来、值可配，等业务给出数之后只改这里一行，不动代码。
        #   * `job_ttl_hours`：§16.3 逐字要求「导出文件默认短期有效」，所以它**不能**
        #     取「永不过期」——那等于不实现这一条。24 小时是「当天做完当天取走」，
        #     学校跨天再回来下就需要重新导出（文件在盘上还在，只是不接受下载）。
        #   * `max_rows`：`0` = **不限制**，也就是这套系统一直以来的行为。这里刻意
        #     不用一个「看起来合理」的上限去截断：静默丢行会让收到文件的人以为
        #     「这里就只有这么多」（§10 那条「凡是截断，都要自己说出来」），而
        #     「一个学校一次能导多少行」没有任何依据可以定。
        "job_ttl_hours": 24,
        "max_rows": 0,
    },
    "cadence": {
        "follow_up_days": 7,
        "family_contact_days": 14,
        "retest_days": 30,
        "task_duration_days": 14,
        "reminder_horizon_days": 30,
    },
    "ui": {
        "low_completion_threshold": 80,
        "dimension_high_threshold": 30,
        "seconds_per_question": 12,
        "page_size_default": 20,
    },
}


def _stored_values(db: Session, namespace: str) -> dict[str, Any]:
    try:
        rows = db.scalars(
            select(SystemSetting).where(SystemSetting.namespace == namespace)
        ).all()
    except SQLAlchemyError:
        # Fail safe to defaults rather than blanking the UI.
        return {}
    return {row.key: row.value_json for row in rows}


def get_namespace(db: Session, namespace: str) -> dict[str, Any]:
    """Effective settings for one namespace: defaults overlaid with stored values."""
    defaults = DEFAULTS.get(namespace)
    if defaults is None:
        raise AppError("VALIDATION_ERROR", f"未知的配置分组：{namespace}", 422)
    return {**defaults, **_stored_values(db, namespace)}


def get_all(db: Session) -> dict[str, dict[str, Any]]:
    return {namespace: get_namespace(db, namespace) for namespace in DEFAULTS}


def update_namespace(
    db: Session, namespace: str, values: dict[str, Any], user: UserAccount
) -> dict[str, Any]:
    """Write deviations from the defaults.

    Only keys declared in `DEFAULTS` are accepted; a value equal to the default
    is deleted rather than stored, so the table never accumulates rows that say
    nothing.
    """
    defaults = DEFAULTS.get(namespace)
    if defaults is None:
        raise AppError("VALIDATION_ERROR", f"未知的配置分组：{namespace}", 422)

    unknown = set(values) - set(defaults)
    if unknown:
        raise AppError("VALIDATION_ERROR", f"未知的配置项：{', '.join(sorted(unknown))}", 422)

    for key, value in values.items():
        row = db.scalar(
            select(SystemSetting).where(
                SystemSetting.namespace == namespace, SystemSetting.key == key
            )
        )
        if value == defaults[key]:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            db.add(
                SystemSetting(
                    namespace=namespace, key=key, value_json=value, updated_by=user.id
                )
            )
        else:
            row.value_json = value
            row.updated_by = user.id

    db.flush()
    return get_namespace(db, namespace)


def describe_changes(before: dict[str, Any], after: dict[str, Any]) -> str:
    """One-line summary for the audit trail — only what actually moved."""
    changed = [
        f"{key}: {before.get(key)!r} → {value!r}"
        for key, value in after.items()
        if before.get(key) != value
    ]
    return "; ".join(changed)[:1000]
