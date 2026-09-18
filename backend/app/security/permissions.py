"""Capability layer for the admin permission matrix.

Design notes
------------
* The matrix is *additive* to `require_role`, not a replacement: only endpoints
  covered by the five capabilities below migrate to `require_capability`.
  Everything else keeps using `require_role`.
* `CAPABILITY_DEFAULTS` mirrors the behaviour the code already enforced before
  this layer existed, so an empty `role_permission` table behaves exactly like
  the previous hard-coded rules. Existing tests are the regression net for that.
* Lookups fail *safe*: a missing row or a read error falls back to the defaults.
  It never fails open to "allow everything".
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import AppError
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.models.permission import RolePermission

# --- Capability keys (mirrors the prototype's matrix rows) ---
AGGREGATE_STATS = "AGGREGATE_STATS"
STUDENT_PSYCH_DETAIL = "STUDENT_PSYCH_DETAIL"
KEY_QUESTIONS = "KEY_QUESTIONS"
ORG_ACCOUNT = "ORG_ACCOUNT"
CONTROLLED_EXPORT = "CONTROLLED_EXPORT"

CAPABILITY_LABELS = {
    AGGREGATE_STATS: "聚合统计",
    STUDENT_PSYCH_DETAIL: "学生心理详情",
    KEY_QUESTIONS: "重点题/原始答卷",
    ORG_ACCOUNT: "组织与账号",
    CONTROLLED_EXPORT: "受控导出",
}

# --- Scope levels ---
NONE = "NONE"
SCOPED = "SCOPED"
SCHOOL = "SCHOOL"
SUMMARY = "SUMMARY"
GRANTED_WITH_AUDIT = "GRANTED_WITH_AUDIT"
MANAGE = "MANAGE"
READ_BASIC = "READ_BASIC"
READ_SUMMARY = "READ_SUMMARY"
OWN = "OWN"
PSYCH_SUMMARY = "PSYCH_SUMMARY"
PROGRESS_SUMMARY = "PROGRESS_SUMMARY"
BASE_ONLY = "BASE_ONLY"

SCOPE_LABELS = {
    NONE: "无",
    SCOPED: "授权范围",
    SCHOOL: "学校范围",
    SUMMARY: "仅摘要，无正文",
    GRANTED_WITH_AUDIT: "单独授权+审计",
    MANAGE: "管理",
    READ_BASIC: "查看必要信息",
    READ_SUMMARY: "查看汇总",
    OWN: "本人",
    PSYCH_SUMMARY: "授权心理摘要",
    PROGRESS_SUMMARY: "管理进展摘要",
    BASE_ONLY: "仅基础数据",
}

# --- Which levels each capability actually accepts（2026-09-17）---
#
# 这五个列表就是那 12 个等级的真正定义域。在此之前，权限弹窗给 20 个格子里的**每**
# 一个都列出全部 12 个等级，于是 `student/ORG_ACCOUNT = 管理`、`leader/KEY_QUESTIONS =
# 授权范围` 这类组合都是可选且可存的——存进去不报错，只是永远不会被任何端点认出来
# （`scope_allows` 是纯集合成员判断，没有端点把 MANAGE 放进 ORG_ACCOUNT 的 allow 集
# 里给一个学生）。一个能保存却没有任何效果的选项，读者只会得出「这东西坏了」的结论。
#
# 列表**从紧到松**排列，`LEVEL_DESCRIPTIONS` 的文案就是这个顺序的解释。顺序还有第二
# 个用处：前端比对「比默认更宽」时靠的是下标，不是另写一张宽松度表——两张表迟早会
# 各说各话。这个顺序与 `SCOPE_LABELS` 的词面一致（授权范围 < 学校范围）。
CAPABILITY_LEVELS: dict[str, list[str]] = {
    AGGREGATE_STATS: [NONE, SCOPED, SCHOOL],
    STUDENT_PSYCH_DETAIL: [NONE, SUMMARY, SCOPED],
    KEY_QUESTIONS: [NONE, GRANTED_WITH_AUDIT],
    ORG_ACCOUNT: [NONE, OWN, READ_SUMMARY, READ_BASIC, MANAGE],
    CONTROLLED_EXPORT: [NONE, BASE_ONLY, PROGRESS_SUMMARY, PSYCH_SUMMARY],
}

# 每一格「选了会怎样」，按能力分别写。同一串等级名在不同能力下意思不同——
# `授权范围` 在聚合统计里是「只算我管的学生」，在学生心理详情里是「能打开档案正文」，
# 而 `仅摘要` 恰好是**不能**开档案的那一档。写一份全局释义会把这两件事说成一件。
LEVEL_DESCRIPTIONS: dict[str, dict[str, str]] = {
    AGGREGATE_STATS: {
        NONE: "看不到任何统计数字，统计页与下钻均为空",
        SCOPED: "只统计自己授权范围内的学生，数字的口径随范围走",
        SCHOOL: "统计全校学生",
    },
    STUDENT_PSYCH_DETAIL: {
        NONE: "打不开任何学生档案",
        SUMMARY: "只看聚合摘要，不返回单个学生的档案正文",
        SCOPED: "可查看授权范围内学生的完整档案（含复核、跟进、家庭回访）",
    },
    KEY_QUESTIONS: {
        NONE: "看不到重点题与原始答卷",
        GRANTED_WITH_AUDIT: "可查看重点题与原始答卷，每次读取都要填用途并记审计",
    },
    ORG_ACCOUNT: {
        NONE: "无法访问名册、账号与权限配置",
        OWN: "只能看到本人的账号与组织信息（当前没有端点在用这一档）",
        READ_SUMMARY: "只看得到人数一类的汇总，看不到名单（当前没有端点在用这一档）",
        READ_BASIC: "可查看名册的必要字段，不含任何心理数据",
        MANAGE: "可新增修改账号、组织信息，并配置本页的权限矩阵",
    },
    CONTROLLED_EXPORT: {
        NONE: "不能导出任何文件",
        BASE_ONLY: "只能导出基础字段，不含关注等级与心理工作正文",
        PROGRESS_SUMMARY: "可导出进展摘要（谁在跟进、进行到哪一步）",
        PSYCH_SUMMARY: "可导出心理摘要（关注等级、维度概况）",
    },
}


def levels_for(capability_key: str) -> list[str]:
    """该能力可选的全部等级，从紧到松。未知能力返回空列表（调用方据此拒绝）。"""
    return CAPABILITY_LEVELS.get(capability_key, [])


def level_is_valid(capability_key: str, scope_level: str) -> bool:
    """这个等级在这项能力下用不用得上。写权限配置前必过这一关。"""
    return scope_level in levels_for(capability_key)
# Defaults reproduce the pre-existing `require_role` behaviour exactly. A scope
# of NONE means the role is denied.
CAPABILITY_DEFAULTS: dict[str, dict[RoleCode, str]] = {
    AGGREGATE_STATS: {
        RoleCode.COUNSELOR: "SCOPED",
        RoleCode.LEADER: "SCHOOL",
        RoleCode.ADMIN: NONE,
        RoleCode.STUDENT: NONE,
    },
    STUDENT_PSYCH_DETAIL: {
        RoleCode.COUNSELOR: "SCOPED",
        RoleCode.LEADER: "SUMMARY",
        RoleCode.ADMIN: NONE,
        RoleCode.STUDENT: NONE,
    },
    KEY_QUESTIONS: {
        RoleCode.COUNSELOR: "GRANTED_WITH_AUDIT",
        RoleCode.LEADER: NONE,
        RoleCode.ADMIN: NONE,
        RoleCode.STUDENT: NONE,
    },
    ORG_ACCOUNT: {
        RoleCode.COUNSELOR: "READ_BASIC",
        RoleCode.LEADER: "READ_SUMMARY",
        RoleCode.ADMIN: "MANAGE",
        RoleCode.STUDENT: "OWN",
    },
    CONTROLLED_EXPORT: {
        RoleCode.COUNSELOR: "PSYCH_SUMMARY",
        RoleCode.LEADER: "PROGRESS_SUMMARY",
        RoleCode.ADMIN: "BASE_ONLY",
        RoleCode.STUDENT: NONE,
    },
}


def resolve_scope(db: Session, role_code: RoleCode, capability_key: str) -> str:
    """Configured scope for a role+capability, falling back to the default."""
    try:
        row = db.scalar(
            select(RolePermission).where(
                RolePermission.role_code == role_code.value,
                RolePermission.capability_key == capability_key,
            )
        )
    except SQLAlchemyError:
        # Never fail open: an unreadable table degrades to the default matrix.
        row = None
    if row is not None:
        return row.scope_level
    return CAPABILITY_DEFAULTS.get(capability_key, {}).get(role_code, NONE)


def scope_allows(
    db: Session, role_code: RoleCode, capability_key: str, allow: set[str] | None = None
) -> bool:
    """True when the role's scope for this capability qualifies.

    `allow=None` accepts any non-NONE scope. Callers that expose detail rather
    than summaries must pass an explicit `allow` set.
    """
    scope = resolve_scope(db, role_code, capability_key)
    if scope == NONE:
        return False
    return allow is None or scope in allow


def require_capability(capability_key: str, allow: set[str] | None = None):
    """Dependency factory: 403 unless the caller's scope for `capability_key` qualifies.

    `allow` narrows which scope levels pass. This matters because the matrix's
    descriptive levels are not interchangeable: for 学生心理详情 a counselor holds
    SCOPED (full case access) while a leader holds SUMMARY (aggregate view only).
    Endpoints that expose case detail must therefore accept SCOPED — not merely
    "anything that isn't NONE".

    Passing `allow=None` accepts every non-NONE scope, which is the right default
    for capabilities whose levels are all equivalent permits.

    Tightening `allow` per endpoint keeps behaviour identical to the pre-existing
    `require_role` guards, so the capability layer is a refactor rather than a
    silent permission change.
    """

    def checker(
        current_user: Annotated[UserAccount, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> UserAccount:
        if not scope_allows(db, current_user.role_code, capability_key, allow):
            raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
        return current_user

    return checker


def permission_matrix(db: Session) -> list[dict]:
    """Full matrix for the admin UI: one row per capability, one column per role."""
    roles = list(RoleCode)
    rows = []
    for capability_key, label in CAPABILITY_LABELS.items():
        configured = {
            row.role_code: row.scope_level
            for row in db.scalars(
                select(RolePermission).where(RolePermission.capability_key == capability_key)
            ).all()
        }
        rows.append(
            {
                "capability_key": capability_key,
                "label": label,
                "roles": {
                    role.value: configured.get(
                        role.value,
                        CAPABILITY_DEFAULTS.get(capability_key, {}).get(role, NONE),
                    )
                    for role in roles
                },
            }
        )
    return rows
