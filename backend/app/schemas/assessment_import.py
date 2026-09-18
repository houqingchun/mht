from pydantic import BaseModel, Field


class AssessmentImportCommitRequest(BaseModel):
    """确认导入。

    只有预览令牌——批次名称与测评日期在预览时就已经进了令牌（它们是预览的一部分：
    同一个文件配不同的测评日期会得到不同的记录）。重新传一遍等于给它们第二个真相来源，
    而两个来源不一致时无从判断哪个才是操作员的意图。
    """

    preview_token: str = Field(min_length=1)

    # 处置方式，只在**文件里真的有冲突**时必需：`overwrite` / `skip`。
    #
    # 用 `str` 而不是 `Literal[...]`：取值错要回统一响应封装里的那句中文，
    # 而 `Literal` 会被 FastAPI 自己的校验拦下，前端拿到的是框架的 422 结构，
    # `api.ts` 取不到 `error.message`，只能显示一句笼统的失败（同 `parse_tested_on`
    # 不写成 `date` 的理由）。合法性在服务里判。
    resolution: str | None = None
