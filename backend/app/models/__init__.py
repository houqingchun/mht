"""全部 ORM 模型。**这个文件的 `__all__` 就是 `Base.metadata` 的表集**。

`test_sql_schema_matches_models.py` 与 `test_sql_reset_to_baseline.py` 都按
`Base.metadata` 推外键图与表集，而它们拿到的是**被 import 过的那些模型**——
一个模型写好了却没在这里导出，两条守卫都会当它不存在：不会报「少了一张表」，
只会当那张表不该有。所以新增模型时这一步不是整理，是它**被守卫看见**的前提。
"""

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    AssessmentTaskScope,
    DimensionResult,
    RiskEvent,
)
from app.models.account import AuthSession, UserAccount, UserScope
from app.models.audit import AuditLog
from app.models.care import (
    CareCaseEvent,
    FamilyContactRecord,
    FollowUpRecord,
    ManualReview,
    RetestPlan,
    StudentCareCase,
)
from app.models.exporting import ExportJob
from app.models.importing import (
    AssessmentExternalResult,
    AssessmentImportBatch,
    AssessmentImportRow,
    StudentAgeChangeLog,
    StudentRosterImportBatch,
    StudentRosterImportRow,
)
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.permission import RolePermission
from app.models.reporting import ProfessionalReport, ProfessionalReportVersion
from app.models.scale import AssessmentScale, ScaleQuestion, ScaleRule
from app.models.setting import SystemSetting

__all__ = [
    "AssessmentAnswer",
    "AssessmentExternalResult",
    "AssessmentImportBatch",
    "AssessmentImportRow",
    "AssessmentResult",
    "AssessmentScale",
    "AssessmentSession",
    "AssessmentTarget",
    "AssessmentTask",
    "AssessmentTaskScope",
    "AuditLog",
    "AuthSession",
    "CareCaseEvent",
    "ClassGroup",
    "DimensionResult",
    "ExportJob",
    "FamilyContactRecord",
    "FollowUpRecord",
    "Grade",
    "ManualReview",
    "RetestPlan",
    "RiskEvent",
    "RolePermission",
    "ProfessionalReport",
    "ProfessionalReportVersion",
    "ScaleQuestion",
    "ScaleRule",
    "School",
    "Student",
    "StudentAgeChangeLog",
    "StudentCareCase",
    "StudentRosterImportBatch",
    "StudentRosterImportRow",
    "SystemSetting",
    "UserAccount",
    "UserScope",
]
