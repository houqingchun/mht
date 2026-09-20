"""受控导出的作业记录（V1.2 增量 DDL 第四节）。

导出在这套系统里是**一次操作**，不是一次点击：它带着用途（`purpose`）、
当时的范围（`scope_snapshot`）、字段策略（`field_policy`）、遮蔽等级
（`mask_level`），落到一个文件上，然后可以过期、可以被撤销、可以数被下载了几次。
这些**全是事实**，所以它们该在库里，而不是只在一行审计的 `detail` 里——
CLAUDE.md §8 记着那条缺口：审计写了遮蔽模式，而没有任何接口或界面读得出来。

它与 `audit_log` 的分工：审计回答「谁在什么时候调了哪个接口」，
这一张回答「那份文件还在不在、还能不能下、当时是按什么口径做的」。
``export_job.job_no`` 是**给人念的**编号（同 ``version.py`` 的 ``VERSION_LABEL``：
界面上那一行、操作员打电话报故障用的就是它），所以它单独有一列并唯一。

阶段 8 之前这一行只建模（没有服务、没有端点、没有下载实现）；现在三样都有了，
落点在 ``services/export_service.py``（建作业 / 取文件 / 撤销 / 列表）与
``api/v1/exports.py``（四个端点）。这一行里 `status` 那一列**只有在被撤销时才写**
——「有没有过期」由 ``effective_export_status`` 拿 `expires_at` 与此刻比出来，
与 §12 的 `effective_task_status` 同一条：存下来的只有真实发生过的动作。
"""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExportJob(Base):
    __tablename__ = "export_job"
    __table_args__ = (
        UniqueConstraint("job_no", name="uq_export_job_no"),
        Index("ix_export_job_requester_status", "requested_by", "status"),
        Index("ix_export_job_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_no: Mapped[str] = mapped_column(String(64), nullable=False)
    export_type: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", name="export_job_ibfk_1"), nullable=False
    )
    # 请求时写的用途。与 `audit_log.purpose` 同一条约定：**自由文本**，
    # 所以它担不起任何机器判据（CLAUDE.md §8：遮蔽模式因此另存 `mask_level`）。
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    # 导出那一刻的授权范围与字段策略。快照而不是引用：范围配置改了之后，
    # 「这份文件当时按什么口径导的」必须还有答案——与 `assessment_target`
    # 的名册快照是同一条规矩。
    scope_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    field_policy: Mapped[dict] = mapped_column(JSON, nullable=False)
    mask_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="MASKED", server_default="MASKED"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    # `file_uri` 是**服务器上的落点**，不进任何导出文件（它是路径，不是数据）。
    file_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 只有 `created_at` / `updated_at` 是数据库写的（同 `models/common.py` 的
    # `TimestampMixin`）；`expires_at` / `downloaded_at` / `revoked_at` 是 Python
    # 写的业务时间，所以没有 `server_default`，也没有 `onupdate`。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
