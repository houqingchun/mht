from pydantic import BaseModel, Field


class ScaleDraftRequest(BaseModel):
    preview_token: str = Field(min_length=1)
    version: str = Field(min_length=1, max_length=64)
    name: str = Field(default="中学生心理健康测验", min_length=1, max_length=128)



class ScoreBandInput(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    min: int = Field(ge=0, le=1000)
    max: int = Field(ge=0, le=1000)


class ScaleRuleUpdateRequest(BaseModel):
    """Full replacement of the editable parts of a rule config."""

    validity_retest_threshold: int | None = Field(default=None, ge=1, le=100)
    total_levels: list[ScoreBandInput] | None = None
    dimension_levels: list[ScoreBandInput] | None = None
