"""数据库备份的每一次尝试（V2.2 增量 DDL）。

**为什么必须落表，而不是「扫一眼备份目录里有什么文件」。** 用户报的原始症状是
``备份数据.bat`` **总是报错、且窗口一闪就关**——也就是说，这个功能的失败方式是
**静默**的。只扫目录的方案答得出「有哪几份备份」，答不出「失败过几次、为什么」：
自动备份恰恰是在凌晨、没人看着、连窗口都没有的时候跑的，它失败时不会有人看见。
那份静默正是这次要修掉的东西，把它搬到一张新表上就白搬了。所以每一次尝试
（成功的与失败的）都写一行，`status` 与 `message` 就是那个答案。

分工与 ``export_job`` 一致：审计（``audit_log``）回答「谁在什么时候触发了备份」，
这一张回答「那一次到底成没成、文件在哪、多大」。两者都写，回答的不是同一个问题。

**没有 `started_at` / `finished_at`。** 这一行是在那次备份**结束时**写一次的，
``created_at``（数据库墙钟，见 ``models/common.py``）就是「备份时间」，与导出中心
那一列同源。多两个时间戳只是同一件事的三个说法，而它们之间那一段（耗时）没有任何
读者。

**`message` 一个列担两种话**：失败的原因（mysqldump 自己的 stderr，也就是那个
一闪就没、操作员记不得的东西），以及「备份成功、但第二路径没拷成」的警告。
由 `status` 分辨这两者——拆成两列会让其中一列常年为空。
"""

from datetime import datetime

from sqlalchemy import BigInteger, CHAR, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# `backup_record.trigger` 的三个取值。`AUTO` 是定时那一路（`run_server.py` 的
# 后台线程），`MANUAL` 是管理员在「数据备份」页上点的那一颗，`CLI` 是
# ``python -m app.db.backup``——它同时是 ``ops.ps1`` 那枚按钮背后的动作，
# 也是「服务起不来时我手工跑一次」的那条出路。
TRIGGER_AUTO = "AUTO"
TRIGGER_MANUAL = "MANUAL"
TRIGGER_CLI = "CLI"

TRIGGERS = (TRIGGER_AUTO, TRIGGER_MANUAL, TRIGGER_CLI)

# `status` 只有两个值。**没有「进行中」**：行是在结束时写一次的，所以一个
# 「正在跑」的中间态从来不会落库（手动那一路的并发由 `backup_service` 的
# 模块级锁回答，返回 409 而不是写一行中间态）。
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"

STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED)

# `message` 在线上是 `Text`（不受 MySQL 行长限制），但写进去的常常是别人的
# stderr：mysqldump 出错时那一段可以很长，而它进到界面与审计里之后没人会读完。
# 截断到 1000 字与 `care_events.REASON_COLUMN_LIMIT` / `export_service` 的
# `calculation_error` 同一个量级，理由也同一条——**留一句能读的话，不留一整段**。
MESSAGE_LIMIT = 1000


class BackupRecord(Base):
    """一次备份尝试。成功的与失败的都写。"""

    __tablename__ = "backup_record"
    __table_args__ = (
        # 唯一的查询形状是「最近几条，按时间倒序」——历史表与状态卡都读它。
        Index("ix_backup_record_created_at", "created_at"),
        Index("ix_backup_record_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # 相对备份目录的文件名（如 `xinliceping-20260927-031500.sql`），**不是绝对路径**。
    # 存相对名是为了「备份目录换了之后这一行还读得出来」：绝对路径会把换目录这件事
    # 变成一个无法解释的历史（旧行指着一个不存在的路径，而它本来只是想说「这一份叫
    # 这个名字」）。失败时为空——一次没产出文件的尝试没有文件名。
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 自动那一次没有操作人（定时线程、无请求、无 actor），所以可空。界面显示 `—`，
    # 与 `audit_log.actor_user_id` 可空是同一条：未登录的系统事件本来就该有一行。
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="backup_record_ibfk_1"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = [
    "BackupRecord",
    "MESSAGE_LIMIT",
    "STATUSES",
    "STATUS_FAILED",
    "STATUS_SUCCEEDED",
    "TRIGGERS",
    "TRIGGER_AUTO",
    "TRIGGER_CLI",
    "TRIGGER_MANUAL",
]
