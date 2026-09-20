from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.assessment import RiskEvent
from app.models.enums import RoleCode
from app.security.permissions import SCOPED, STUDENT_PSYCH_DETAIL, require_capability
from app.schemas.care import (
    BatchAssignRequest,
    CloseCaseRequest,
    FamilyContactRequest,
    FollowUpRequest,
    ManualReviewRequest,
    ReopenCaseRequest,
    RetestPlanRequest,
)
from app.services.analytics_service import class_comparison
from app.services.audit_service import write_audit
from app.services.care_service import (
    batch_assign_owner,
    counselor_workbench,
    close_case,
    create_family_contact,
    create_follow_up,
    create_manual_review,
    create_retest_plan,
    get_care_case,
    list_assignable_owners,
    list_care_cases,
    reopen_case,
)

router = APIRouter(tags=["care-cases"])

# 学生心理详情：仅授权范围（心理老师）可访问个案明细。德育领导的 SUMMARY
# 是汇总视图，不能进入个案接口——与迁移前的 require_role(COUNSELOR) 完全等价。
PsychDetailReader = Annotated[
    UserAccount,
    Depends(require_capability(STUDENT_PSYCH_DETAIL, allow={SCOPED})),
]


@router.get("/counselor/workbench")
def workbench(
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    return ok(counselor_workbench(db, current_user))


@router.get("/care-cases")
def care_cases(
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": list_care_cases(db, current_user)})


# NOTE: these two literal paths must be registered before "/care-cases/{student_id}",
# otherwise FastAPI matches the path parameter first and fails int coercion with a 422.
@router.get("/care-cases/assignable-owners")
def assignable_owners(
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": list_assignable_owners(db, current_user)})


@router.post("/care-cases/batch-assign")
def batch_assign(
    payload: BatchAssignRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    result = batch_assign_owner(db, current_user, payload.assignments, payload.owner_id)
    write_audit(
        db,
        action="批量分配负责人",
        resource_type="STUDENT_CARE_CASE",
        resource_id=",".join(str(item.case_id) for item in payload.assignments),
        actor=current_user,
        request=request,
        detail=f"负责人账号 {payload.owner_id}",
    )
    db.commit()
    return ok(result)


@router.get("/care-cases/{student_id}")
def care_case_detail(
    student_id: int,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    data = get_care_case(db, current_user, student_id)
    write_audit(
        db,
        action="查看学生详情",
        resource_type="STUDENT_CARE_CASE",
        resource_id=str(data["case_id"]),
        actor=current_user,
        request=request,
        student_id=student_id,
    )
    db.commit()
    return ok(data)


@router.get("/care-cases/{student_id}/comparison")
def care_case_comparison(
    student_id: int,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    """这名学生最近一场的维度得分，与班级、年级当前水平的对照。

    归在 /care-cases 而不是 /analytics：它返回的是**一名学生**的维度得分，
    与 `GET /care-cases/{student_id}` 同一敏感级别，也就同样按「读一次写一条审计」
    对待。它顺带算了同班同年级的均值，但那些均值是这张对照表的**背景**，
    不是它的主体——判它该不该给的心理老师，正是有权看这名学生档案的那一位。
    """
    data = class_comparison(db, current_user, student_id)
    write_audit(
        db,
        action="查看班级对照",
        resource_type="STUDENT_CARE_CASE",
        resource_id=str(student_id),
        actor=current_user,
        request=request,
        student_id=student_id,
    )
    db.commit()
    return ok(data)


@router.post("/care-cases/{case_id}/reviews")
def create_review(
    case_id: int,
    payload: ManualReviewRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    review = create_manual_review(db, current_user, case_id, payload)
    risk_event = db.get(RiskEvent, review.risk_event_id)
    write_audit(
        db,
        action="创建人工复核",
        resource_type="MANUAL_REVIEW",
        resource_id=str(review.id),
        actor=current_user,
        request=request,
        student_id=risk_event.student_id if risk_event else None,
    )
    db.commit()
    return ok({"id": review.id})


@router.post("/care-cases/{case_id}/follow-ups")
def create_followup(
    case_id: int,
    payload: FollowUpRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    followup = create_follow_up(db, current_user, case_id, payload)
    write_audit(
        db,
        action="创建跟进记录",
        resource_type="FOLLOW_UP_RECORD",
        resource_id=str(followup.id),
        actor=current_user,
        request=request,
        student_id=followup.student_id,
    )
    db.commit()
    return ok({"id": followup.id})


@router.post("/care-cases/{case_id}/family-contacts")
def create_family(
    case_id: int,
    payload: FamilyContactRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    record = create_family_contact(db, current_user, case_id, payload)
    write_audit(
        db,
        action="创建家庭回访",
        resource_type="FAMILY_CONTACT_RECORD",
        resource_id=str(record.id),
        actor=current_user,
        request=request,
        student_id=record.student_id,
    )
    db.commit()
    return ok({"id": record.id})


@router.post("/care-cases/{case_id}/retests")
def create_retest(
    case_id: int,
    payload: RetestPlanRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    retest = create_retest_plan(db, current_user, case_id, payload)
    write_audit(
        db,
        action="安排复测",
        resource_type="RETEST_PLAN",
        resource_id=str(retest.id),
        actor=current_user,
        request=request,
        student_id=retest.student_id,
    )
    db.commit()
    return ok({"id": retest.id})


@router.post("/care-cases/{case_id}/close")
def close(
    case_id: int,
    payload: CloseCaseRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    care_case = close_case(db, current_user, case_id, payload)
    write_audit(
        db,
        action="关闭关注档案",
        resource_type="STUDENT_CARE_CASE",
        resource_id=str(care_case.id),
        actor=current_user,
        request=request,
        student_id=care_case.student_id,
    )
    db.commit()
    return ok({"id": care_case.id, "status": care_case.status})


@router.post("/care-cases/{case_id}/reopen")
def reopen(
    case_id: int,
    payload: ReopenCaseRequest,
    request: Request,
    current_user: PsychDetailReader,
    db: Annotated[Session, Depends(get_db)],
):
    care_case = reopen_case(db, current_user, case_id, payload)
    write_audit(
        db,
        action="重新打开关注档案",
        resource_type="STUDENT_CARE_CASE",
        resource_id=str(care_case.id),
        actor=current_user,
        request=request,
        student_id=care_case.student_id,
    )
    db.commit()
    return ok({"id": care_case.id, "status": care_case.status})
