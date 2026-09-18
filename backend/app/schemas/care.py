from datetime import date

from pydantic import BaseModel, Field


class ManualReviewRequest(BaseModel):
    risk_event_id: int
    review_result: str = Field(min_length=1, max_length=128)
    confirmed_facts: str = Field(min_length=1, max_length=5000)
    next_action: str | None = Field(default=None, max_length=128)
    next_follow_up_date: date | None = None


class FollowUpRequest(BaseModel):
    record_type: str = Field(default="心理老师访谈", min_length=1, max_length=64)
    confirmed_facts: str = Field(min_length=1, max_length=5000)
    next_follow_up_date: date


class FamilyContactRequest(BaseModel):
    contact_date: date
    contact_person: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=64)
    result: str = Field(min_length=1, max_length=128)
    support_status: str = Field(min_length=1, max_length=128)
    confirmed_facts: str = Field(min_length=1, max_length=5000)
    next_contact_date: date | None = None


class RetestPlanRequest(BaseModel):
    planned_date: date
    reason: str = Field(min_length=1, max_length=255)


class CloseCaseRequest(BaseModel):
    close_reason: str = Field(min_length=1, max_length=128)
    close_note: str = Field(min_length=1, max_length=5000)
    confirm_follow_up_checked: bool


class ReopenCaseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=5000)


class BatchAssignRequest(BaseModel):
    case_ids: list[int] = Field(min_length=1)
    owner_id: int
