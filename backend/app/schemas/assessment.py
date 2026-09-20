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


class SupplementTargetsRequest(BaseModel):
    """补发目标学生：先看后补，同一个请求体两种用法（§8.1 / §16.2）。

    `confirm=False` 只回一份候选名单，一行都不写；`confirm=True` 才落目标行。
    两个阶段共用一个请求体，是因为它们**是同一个动作的两步**——界面把一个
    表单填完，第一下看结果、第二下确认，两次请求之间不该多出一次「填原因」。

    `reason` 两个阶段都要，尽管只有确认那一步会把它写进审计：原因是这个动作的
    一部分（§16.2 要求补发记录原因），把它设成「确认时才必填」会造出一个
    条件必填字段——一条要在服务层、路由层、前端各写一遍的规则，而它换来的
    只是预览时可以少填一格。
    """

    confirm: bool = False
    reason: str = Field(min_length=1, max_length=200)


class MarkParticipationRequest(BaseModel):
    """标记一名目标学生在**这一场**里该不该参加（§18.10 那三个减项）。

    `disposition` 的取值域由服务端的 `PARTICIPATION_DISPOSITIONS` 判（未知值 422），
    这里**不写 `pattern`**：一张在请求模型里再抄一遍的码表就是第二个定义，而它与
    服务端那张漂移之后，屏幕上会先通过校验、再撞一句英文 422（§4 那条
    「可选等级由 `CAPABILITY_LEVELS` 定义」是同一件事的另一处）。

    `reason` 与 `note` 都由服务层判：**非 `REQUIRED` 必须给原因**是业务规则
    （「为什么拒绝参加」要说得出话），而它是条件必填——写成 `min_length=1` 会让
    「恢复成应测」那一档也要编一句话。
    """

    disposition: str
    reason: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=500)


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
