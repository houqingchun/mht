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
    # §16.4：关闭 / 重新打开 / 转派负责人三处都要带乐观锁版本号。
    # **必填**，不是选填——一个选填的版本号等于没有版本号：客户端不发这一项时
    # 校验自动通过，而「忘了发」与「读到的是最新版」在服务端长得一模一样。
    case_version: int


class ReopenCaseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=5000)
    case_version: int


class BatchAssignItem(BaseModel):
    """批量分配里的一行：**要改哪一条**与**客户端读到的是它的哪一版**。

    逐行带号是 §16.4 要求转派也走乐观锁的直接后果：一个 `list[int]` 只表达得了
    前者。列表上每一行的版本各不相同（有些行是十分钟前拉的），所以版本号必须
    跟着行走，不能提到请求体顶层。
    """

    case_id: int
    case_version: int


class BatchAssignRequest(BaseModel):
    assignments: list[BatchAssignItem] = Field(min_length=1)
    owner_id: int
