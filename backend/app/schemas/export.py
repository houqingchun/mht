from pydantic import BaseModel, Field


class ExportRequest(BaseModel):
    """只要一个用途的导出请求（任务完成明细、未参与名单）。

    受控导出的 `CareCaseExportRequest` 多带 `mask_names` / `student_ids` 那些开关，
    这两个端点一个都用不上——共用它会让请求体看起来有两个不生效的字段，而「传了不
    生效」与「不支持」在屏幕上是分不开的。
    """

    purpose: str | None = Field(default=None, max_length=255)


class ValidityRetestExportRequest(BaseModel):
    """效度复测名单的导出请求（统计分析 → 全校八维度分析）。

    比其他导出请求多一个 `task_ids`，而它是**必须带上的**：这一份名单就是「所选这些
    测评任务里，哪些学生的结果提示需要复测」，而**报表本身是按一次筛选算出来的**。
    少了它，同一屏上会同时存在两个口径——上方写着「效度建议复测 3 人」，导出的是
    全校所有任务的 27 人，而两个数各自都是对的（§11 那条「指标卡上的数必须与它
    点进去的那个列表同源」）。

    上限 20 是照 `GET /analytics/report` 的既有约定（一次最多合并分析 20 个任务），
    两处的判据必须一致：否则界面上能查、却导不出来。
    """

    purpose: str | None = Field(default=None, max_length=255)
    task_ids: list[int] = Field(min_length=1, max_length=20)


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
