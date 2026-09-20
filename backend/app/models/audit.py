from datetime import datetime

from sqlalchemy import CHAR, JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import RoleCode


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        # 审计行自身的 id。`event_id` 是**给外部看的**那一个：导出、对账、
        # 跨系统引用都用它，因为自增 `id` 只在本地这一台库里有意义。
        # NULL 在唯一索引里互不相等，所以 V1.0 留下的那些行（没有它）不受限。
        UniqueConstraint("event_id", name="uq_audit_event_id"),
        # 这一条与 `audit_log_ibfk_1`（actor_user_id）指着同一列。显式写出来，
        # 那个外键就复用它；不写的话 MySQL 会另建一条**按约束名**的索引
        # （`audit_log_ibfk_1`），而真库里那一条叫 `actor_user_id`。
        Index("actor_user_id", "actor_user_id"),
        Index("ix_audit_created_actor_action", "created_at", "actor_role", "action"),
        Index("ix_audit_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="audit_log_ibfk_1"), nullable=True
    )
    actor_role: Mapped[RoleCode | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Attributes the row to a student so the per-student access-audit tab can
    # query it directly. resource_type/resource_id alone cannot do this, because
    # actions on a case write heterogeneous resource types (MANUAL_REVIEW,
    # FOLLOW_UP_RECORD, …) that carry no student reference.
    #
    # 外键名显式写出，理由同 `AssessmentSession` 上那一段：不写时 MySQL 按列序
    # 自动编号，而真库里这一条叫 `fk_audit_log_student_id`（迁移 0009 起的名字）。
    # 索引名是 SQLAlchemy 默认的 `ix_audit_log_student_id`，与真库一致，
    # 所以外键复用它、不再另建一条。
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("student.id", name="fk_audit_log_student_id"), nullable=True, index=True
    )
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # ------------------------------------------------------------------
    # V1.2 新增。新列一律追加在末尾（见 `models/care.py` 的 `StudentCareCase`）。
    # ------------------------------------------------------------------
    # 审计行自己的 UUID，与自增 `id` 并存（见上面 `uq_audit_event_id` 那段说明）。
    event_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    # 操作人**当时**的账号名。`actor_user_id` 指着 `user_account`，而账号停用之后
    # 那一行的 `account` 是可以变的（§4：角色与账号创建后不可改，但展示名可以）——
    # 一份去年的审计导出要能回答「那时候这个 id 是谁」。与 `assessment_target`
    # 的名册快照同族：**当时的事实存下来，现在的状态另外查**。
    actor_account_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 同一个请求写下的多行审计靠它串起来（一次导入写「导入学生名册」+
    # 「更新学生年龄」两行，它们属于同一次请求）。
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 结构化的 `detail`。**只放可追溯的业务元数据**（遮蔽模式、覆盖/放弃、
    # 写入行数），与 DDL 第 774-776 行逐字一致：**禁止写入完整答卷、
    # 重点题回答和家庭回访正文**（CLAUDE.md §8）。
    # 字符串那一个 `detail` 留着：V1.0 的行在里面，而且导出与搜索读的是它。
    detail_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 与 `result`（SUCCESS / FAILURE）不同：这一列是**业务结果码**
    # （如 403 拒绝时是哪个能力不够），给机器分辨用。
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 这一行的防篡改摘要。算法与覆盖范围由服务层定（本阶段只对齐结构）。
    audit_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)

