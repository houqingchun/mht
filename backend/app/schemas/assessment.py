from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    task_id: int


class SaveAnswerRequest(BaseModel):
    answer: str = Field(pattern="^(YES|NO)$")


class CreateAssessmentTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    start_at: str | None = None
    end_at: str | None = None


class UpdateAssessmentTaskRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    start_at: str | None = None
    end_at: str | None = None


class SessionOut(BaseModel):
    id: int
    task_id: int | None
    student_id: int
    scale_id: int
    scale_version: str
    status: str
    current_question_no: int | None = None
    answered_count: int
    answers: dict[int, str]


class StudentTaskOut(BaseModel):
    id: int
    task_no: str
    name: str
    status: str
    target_status: str
    start_at: str | None
    end_at: str | None
    session_id: int | None = None
    answered_count: int = 0
    completed_at: str | None = None
