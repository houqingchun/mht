from pydantic import BaseModel


class ScopeOut(BaseModel):
    scope_type: str
    school_id: int | None = None
    grade_id: int | None = None
    class_id: int | None = None
    student_id: int | None = None


class UserOut(BaseModel):
    id: int
    account: str
    account_type: str
    display_name: str
    role_code: str
    must_change_password: bool
    active: bool
    scopes: list[ScopeOut] = []

