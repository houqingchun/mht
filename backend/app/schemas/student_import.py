from pydantic import BaseModel


class StudentImportCommitRequest(BaseModel):
    # 提交的是**批次 id**，不是整份文件、也不是一个签过名的预览令牌：逐行明细在
    # 预览时已经落库（`student_roster_import_row`），提交时读回来。所以这里的判据是
    # 「哪一批」，而「这一批里有哪些行」由库回答——操作员上传完被叫走、回来接着
    # 提交时，他提交的正是屏幕上那一批，不是浏览器内存里那份可能已经过期的副本。
    batch_id: int
    # 「覆盖 / 放弃」由 `student_import_service` 的那套码定义（与测评导入共用）。
    # 只有文件里真的有冲突（学号已在名册上）时才必需——没有冲突的文件照旧一次提交。
    resolution: str | None = None
