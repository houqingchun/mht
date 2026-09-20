from pydantic import BaseModel, Field


class ExportRequest(BaseModel):
    """只要一个用途的导出请求（任务完成明细、未参与名单）。

    受控导出的 `CareCaseExportRequest` 多带 `mask_names` / `student_ids` 那些开关，
    这两个端点一个都用不上——共用它会让请求体看起来有两个不生效的字段，而「传了不
    生效」与「不支持」在屏幕上是分不开的。
    """

    purpose: str | None = Field(default=None, max_length=255)


class RevokeExportRequest(BaseModel):
    """撤销一份导出作业时的可选说明。

    **选填是有意的**：撤销是数据外泄时的紧急动作，在那一刻多一个必填字段就是在
    最不该加摩擦的地方加摩擦。原因写进审计的 `detail`（`export_job` 上没有这一列，
    而 §16.3 要的只是「撤销时间」）；没填时记「未填写」而不是留空——留空与「没问过」
    分不开。
    """

    reason: str | None = Field(default=None, max_length=255)


class CareCaseExportRequest(BaseModel):
    purpose: str | None = Field(default=None, max_length=255)
    mask_level: str = Field(default="MASKED", pattern="^(MASKED|SUMMARY)$")
    """False keeps full names; only permitted under explicit authorisation and always audited."""
    mask_names: bool = True
    """Adds the MHT total-score column to the export."""
    include_score: bool = False
    """Restricts the export to these students. Omit to export the whole cohort."""
    student_ids: list[int] | None = None
