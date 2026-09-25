from pydantic import BaseModel, Field


class ReportCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    task_ids: list[int] = Field(min_length=1, max_length=20)
    analysis_mode: str = "ALL_CALCULATED"
    overall_summary: str = Field(default="", max_length=10000)
    dimension_interpretation: str = Field(default="", max_length=10000)
    sample_validity_note: str = Field(default="", max_length=10000)
    support_plan: str = Field(default="", max_length=10000)


class ReportDraftRequest(BaseModel):
    overall_summary: str = Field(default="", max_length=10000)
    dimension_interpretation: str = Field(default="", max_length=10000)
    sample_validity_note: str = Field(default="", max_length=10000)
    support_plan: str = Field(default="", max_length=10000)


class ReportExportRequest(BaseModel):
    version_no: int | None = Field(default=None, ge=1)
    purpose: str = Field(min_length=1, max_length=255)
