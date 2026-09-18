from pydantic import BaseModel, Field


class StudentImportCommitRequest(BaseModel):
    preview_token: str = Field(min_length=1)
    # 「覆盖 / 放弃」由 `student_import_service` 的那套码定义（与测评导入共用）。
    # 只有文件里真的有冲突（学号已在名册上）时才必需——没有冲突的文件照旧一次提交。
    resolution: str | None = None
