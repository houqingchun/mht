from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class StudentCareCase(Base):
    __tablename__ = "student_care_case"
    __table_args__ = (
        # 父表那一侧的键，给四条复合外键指着（`manual_review_fk_case_student` 那一族）。
        # `id` 是主键，本来就唯一；这条键存在的唯一理由是 MySQL 要求被引用的
        # 列组合上有索引——与 `uq_external_result_id_student` 同一个形状。
        UniqueConstraint("id", "student_id", name="uq_care_case_id_student"),
        # **「一名学生同时只有一条在办档案」第一次变成数据库约束**（V1.2 DDL 第 589 行）。
        # 0012 删掉的那个 `UniqueConstraint("student_id", "status")` 说的是另一件事，
        # 而且是错的（已关闭的档案会累积，那是正常的）；这一条说的是
        # `active_student_id IS NULL` 的那些行里 `student_id` 不许重复——
        # 也就是**在办**的只许一条，已关闭的（那一列是 NULL）不受限。
        # NULL 在 UNIQUE 索引里互不相等，所以它恰好表达了这个意思。
        # 代价记在 CLAUDE.md 已知缺口里：`care_service.reopen_case` 目前是无条件
        # 把 status 设成 FOLLOWING，撞上这条键会给用户一个 500。
        UniqueConstraint("active_student_id", name="uq_care_case_one_active_per_student"),
        Index("ix_care_case_status_owner_updated", "status", "owner_id", "updated_at"),
        Index("ix_care_case_active_student", "active_student_id"),
    )
    # 这里原本有一个 `UniqueConstraint("student_id", "status")`，2026-09-17 去掉
    # （迁移 `0012`）。它把「一名学生一种状态只能有一条」当成了不变量，而**已关闭**的
    # 档案恰恰会累积：上学期关掉的是一次完整的关怀过程，这学期再出状况是一条新的过程，
    # 两条都该留着——这正是「关闭档案不得删除历史记录」那条约定要保住的东西。
    # 约束在库里表现为真：第二次关闭撞 UNIQUE，`db.flush()` 抛 IntegrityError，
    # 用户拿到 500，而这是学校每年都会遇到的时序（秋季关档、春季再关一次）。
    # 「同时只有一条在办」由 `assessment_service.open_or_reuse_care_case` 在写入侧保证
    # （先找非 CLOSED 的那条，找不到才新建），不靠数据库约束。

    id: Mapped[int] = mapped_column(primary_key=True)
    # `index=True` 与迁移 `0012` 配对：那条唯一索引本来兼作 `student_id` 外键的索引，
    # 删除它之前必须先建这条普通索引顶上（MySQL 会以 1553 拒绝删一条外键正在用的索引）。
    # 两边都写出来，模型与库才不会各说各话。**这件事 2026-09-19 起是有测试兜底的**：
    # 表由 `alembic upgrade head` 建，而 `test_sql_schema_matches_models.py` 与
    # `test_sql_reset_to_baseline.py` 按 `Base.metadata` 推外键图——索引漏在模型里，
    # 那个 1553 只会在真机上回来。在此之前测试全绿（内存 sqlite + `create_all`）。
    # 默认名 `ix_student_care_case_student_id` 正好是迁移里用的那个。
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_REVIEW")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    close_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # ------------------------------------------------------------------
    # V1.2 新增。**以下每一列都追加在表末尾**，不加 `AFTER`：手工那份增量 DDL
    # 用了 `AFTER`，而迁移只会 `ADD COLUMN`（追加到末尾），两条路建出来的表
    # **列序不同**。列序不影响任何一条查询，但比对 `information_schema` 时会
    # 逐项不同——所以快照按迁移的产物写，比对按列名关联。模型这边跟着走：
    # 新列一律写在最后，读起来也正好是「这一段是 V1.2 加的」。
    # ------------------------------------------------------------------
    closed_by: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="student_care_case_ibfk_3"), nullable=True
    )
    reopened_by: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="student_care_case_ibfk_4"), nullable=True
    )
    reopen_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 乐观锁：状态变更必须带上读到的那个版本号，改完 +1（需求说明书 §14）。
    #
    # **2026-09-19（阶段 7）起它有读者也有写入方了**：关闭 / 重新打开 / 转派负责人
    # 三处都走 `care_service._check_case_version` + `_bump_case_version`，而
    # 复核 / 跟进 / 家庭回访 / 复测四个入口也会 +1（它们都改 `status` 与 `owner_id`）。
    # 在这之前这一列恒为 1（CLAUDE.md 缺口 9 那一条，现已关闭）。
    case_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # 生成列：**在办的那些行里它等于 student_id，已关闭的那些行是 NULL**。
    # 它是 `uq_care_case_one_active_per_student` 的载体，也是把
    # 「谁现在还在办」变成一次索引查找的那一列。
    #
    # 注解必须是 `int | None`：生成列在库里是可空的（status='CLOSED' 时它就是 NULL），
    # 而 `test_nullability_matches_the_model` 比的是模型注解与快照里的 `NOT NULL`。
    active_student_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed("CASE WHEN `status` <> 'CLOSED' THEN `student_id` ELSE NULL END", persisted=True),
        nullable=True,
    )


class ManualReview(TimestampMixin, Base):
    __tablename__ = "manual_review"
    __table_args__ = (
        # 一条复核记录不许挂在「别的学生」的档案上。`care_case_id` 与 `student_id`
        # 分开写是可能的（它们各自指向不同的表），而写岔了以后
        # 「这条复核说的是谁」就没有答案了——这正是复合外键要挡的那种错误。
        ForeignKeyConstraint(
            ["care_case_id", "student_id"],
            ["student_care_case.id", "student_care_case.student_id"],
            name="manual_review_fk_case_student",
        ),
        Index("ix_manual_review_care_case", "care_case_id"),
        Index("ix_manual_review_student", "student_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_event_id: Mapped[int] = mapped_column(ForeignKey("risk_event.id"), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    review_result: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_follow_up_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    # V1.2 新增（见 `StudentCareCase` 上那一段的说明：新列一律追加在末尾）。
    # `student_id` 是可空的，不是因为可以没有学生，而是因为**历史行要从 risk_event 回填**
    # （DDL 第 636 行）；新写入路径必须有值（服务层强制，本阶段只对齐结构）。
    care_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_care_case.id", name="manual_review_ibfk_3"), nullable=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("student.id", name="manual_review_ibfk_4"), nullable=True
    )


class FollowUpRecord(TimestampMixin, Base):
    __tablename__ = "follow_up_record"
    __table_args__ = (
        ForeignKeyConstraint(
            ["care_case_id", "student_id"],
            ["student_care_case.id", "student_care_case.student_id"],
            name="follow_up_record_fk_case_student",
        ),
        Index("ix_follow_up_case_date", "care_case_id", "next_follow_up_date"),
        Index("ix_follow_up_status_date", "status", "next_follow_up_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False)
    next_follow_up_date: Mapped[date] = mapped_column(Date(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    # V1.2 新增。
    care_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_care_case.id", name="follow_up_record_ibfk_3"), nullable=True
    )


class FamilyContactRecord(TimestampMixin, Base):
    __tablename__ = "family_contact_record"
    __table_args__ = (
        ForeignKeyConstraint(
            ["care_case_id", "student_id"],
            ["student_care_case.id", "student_care_case.student_id"],
            name="family_contact_record_fk_case_student",
        ),
        Index("ix_family_contact_case", "care_case_id"),
        Index("ix_family_contact_next_date", "next_contact_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    operator_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    contact_date: Mapped[date] = mapped_column(Date(), nullable=False)
    contact_person: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(128), nullable=False)
    support_status: Mapped[str] = mapped_column(String(128), nullable=False)
    confirmed_facts: Mapped[str] = mapped_column(Text, nullable=False)
    next_contact_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    # V1.2 新增。
    care_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_care_case.id", name="family_contact_record_ibfk_3"), nullable=True
    )


class RetestPlan(TimestampMixin, Base):
    __tablename__ = "retest_plan"
    __table_args__ = (
        ForeignKeyConstraint(
            ["care_case_id", "student_id"],
            ["student_care_case.id", "student_care_case.student_id"],
            name="retest_plan_fk_case_student",
        ),
        # 下面两条把「这次复测属于谁」钉进数据库：`source_session_id` 与
        # `completed_session_id` 各自要和 `student_id` 指向同一名学生。
        # 它们同时也是 `test_sql_reset_to_baseline` 那条「两条不同的约束可以恰好
        # 同签名」的实例 —— 模型里这两条外键的**本地列不同**（一个 source 一个 completed），
        # 所以按签名比是两条；只按「父表是谁」比就会塌成一条。
        ForeignKeyConstraint(
            ["source_session_id", "student_id"],
            ["assessment_session.id", "assessment_session.student_id"],
            name="retest_plan_fk_source_student",
        ),
        ForeignKeyConstraint(
            ["completed_session_id", "student_id"],
            ["assessment_session.id", "assessment_session.student_id"],
            name="retest_plan_fk_completed_student",
        ),
        Index("ix_retest_case_status_date", "care_case_id", "status", "planned_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    source_session_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_session.id"), nullable=True)
    planned_date: Mapped[date] = mapped_column(Date(), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PLANNED")
    created_by: Mapped[int] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    completed_session_id: Mapped[int | None] = mapped_column(ForeignKey("assessment_session.id"), nullable=True)
    # V1.2 新增。
    care_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_care_case.id", name="retest_plan_ibfk_5"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CareCaseEvent(Base):
    """关怀档案的生命周期事件（开档 / 复核 / 跟进 / 关档 / 重开）。

    它与 `audit_log` **不是一回事**，别合并：审计回答「谁在什么时候调了哪个接口」，
    这张表回答「这份档案经历了什么」。同一次操作会同时产生两条（一条审计、一条事件），
    而两条的读者、保留期与权限都不同——审计是系统级轨迹，事件是**这份档案的病历**。

    只留着 `care_case_event` 的 `created_at`（DDL 就是这样），没有 `updated_at`：
    事件是只追加的，一条被改过的历史事件没有意义。
    """

    __tablename__ = "care_case_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["care_case_id", "student_id"],
            ["student_care_case.id", "student_care_case.student_id"],
            name="care_case_event_fk_case_student",
        ),
        # 三条按列名的索引，DDL 里显式写着，三个外键各自复用它。
        # 漏掉的话 MySQL 会按约束名（`care_case_event_ibfk_2` …）另建一条——
        # 两处都对，却对不上，而索引名没有任何别的东西看得见。
        Index("care_case_id", "care_case_id"),
        Index("student_id", "student_id"),
        Index("operator_id", "operator_id"),
        Index("event_type_created_at", "event_type", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    care_case_id: Mapped[int] = mapped_column(
        ForeignKey("student_care_case.id", name="care_case_event_ibfk_1"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", name="care_case_event_ibfk_2"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="care_case_event_ibfk_3"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_facts: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
