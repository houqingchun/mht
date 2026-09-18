from pydantic import BaseModel, Field


class CareCaseExportRequest(BaseModel):
    purpose: str | None = Field(default=None, max_length=255)
    mask_level: str = Field(default="MASKED", pattern="^(MASKED|SUMMARY)$")
    """False keeps full names; only permitted under explicit authorisation and always audited."""
    mask_names: bool = True
    """Adds the MHT total-score column to the export."""
    include_score: bool = False
    """Restricts the export to these students. Omit to export the whole cohort."""
    student_ids: list[int] | None = None
