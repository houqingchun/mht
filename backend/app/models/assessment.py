from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    Computed,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin


class AssessmentTask(TimestampMixin, Base):
    __tablename__ = "assessment_task"
    __table_args__ = (
        # 父表那一侧的键，给三条复合外键指着（`assessment_session_fk_task_school`
        # 那一族）：任务、目标行、批次、会话必须属于**同一所学校**。
        # 没有它，一个任务的会话可以挂到另一所学校的学生上，而每一列看起来都是对的。
        UniqueConstraint("id", "school_id", name="uq_assessment_task_school_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scale_id: Mapped[int] = mapped_column(ForeignKey("assessment_scale.id"), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    # 这份测评是从哪来的：IN_SYSTEM = 学生在本系统里作答，IMPORTED = 学校把外部平台的
    # 普查结果导入进来（数据中心 → MHT测评记录导入）。导入的批次任务也带这个标记，
    # 所以任务列表上能一眼看出某一批不是本系统测的。
    #
    # `server_default` 不能省（不只是 `default=`）：它是「老写入路径一行不改」的那一半。
    # `default=` 只在 ORM 自己 INSERT 时补值，`server_default` 写在 DDL 里、由 MySQL 补——
    # 于是任何绕过 ORM 的写入（迁移回填、手写 SQL、`reset_to_baseline.sql` 之后的重建）
    # 也拿得到值。V1.2 那 56 个新列里凡是带 DEFAULT 的 NOT NULL 都照这条写。
    # 全库其他带默认值的 NOT NULL 列（本文件的 `assessment_target.status`、
    # `models/common.py`）都是这么写的。
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="IN_SYSTEM", server_default="IN_SYSTEM"
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="assessment_task_fk_voided_by"), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AssessmentTarget(Base):
    """一名学生在某一批测评里的一行位置，以及**发放那一刻**的名册快照。

    快照六列（学号 / 姓名 / 年级名 / 班级名 / 性别 / 年龄）不是冗余：它们是
    「这场测评当时，学校看到的是谁」的答案。学生转班、改名、毕业之后，
    一份去年的完成率报表仍然该按当时的名册解释——`student` 那一行则只回答
    「他**现在**是谁」。这与 CLAUDE.md §1 的四层事实模型是同一条规矩。
    """

    __tablename__ = "assessment_target"
    __table_args__ = (
        UniqueConstraint("task_id", "student_id", name="uq_target_task_student"),
        Index("student_id", "student_id"),
        Index("ix_target_task_status", "task_id", "status"),
        Index("ix_target_participation", "task_id", "participation_disposition"),
        Index("ix_target_effective_session", "effective_session_id"),
        Index("ix_target_effective_external", "effective_external_result_id"),
        # 「这一行属于这场任务、也属于这所学校的学生」——两个复合外键各管一半。
        ForeignKeyConstraint(
            ["task_id", "school_id_snapshot"],
            ["assessment_task.id", "assessment_task.school_id"],
            name="assessment_target_fk_task_school",
        ),
        ForeignKeyConstraint(
            ["school_id_snapshot", "student_id"],
            ["student.school_id", "student.id"],
            name="assessment_target_fk_student_school",
        ),
        ForeignKeyConstraint(
            ["effective_session_id", "student_id"],
            ["assessment_session.id", "assessment_session.student_id"],
            name="assessment_target_fk_effective_session_student",
        ),
        ForeignKeyConstraint(
            ["effective_external_result_id", "student_id"],
            ["assessment_external_result.id", "assessment_external_result.student_id"],
            name="assessment_target_fk_effective_external_student",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("assessment_task.id"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ------------------------------------------------------------------
    # V1.2 新增。新列一律追加在末尾（见 `models/care.py` 的 `StudentCareCase`）。
    # ------------------------------------------------------------------
    # 这一列**没有 `server_default`**：DDL 里它是「先可空、回填、再 MODIFY 成 NOT NULL」，
    # 最终形态是一个无默认值的 NOT NULL。于是 V1.0 的写入路径必须自己给值——
    # 落点是 `task_service` 发放目标行与 `seed.py` / `seed_demo` 三处。
    school_id_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    student_no_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    student_name_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grade_name_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    class_name_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gender_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    age_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 「这场测评他该不该参加」：REQUIRED 是正常，另三个是学校标记的请假 / 免测 /
    # 已排除。它与 `status`（答没答）是两个维度——免测的学生不该被算成「没完成」。
    # 四个码与那条算式在 `expected_participation_predicate` 上面（2026-09-19 第 8 期）。
    # **仍然没有定义的**是「为什么拒绝参加」的理由码（§16.10 的
    # `TODO_BUSINESS_CONFIRMATION`）：`disposition_reason` 收的是一句人写的说明，
    # 不是码——把尚未定稿的词表编出来，等于让一个没人认得的码开始落库。
    participation_disposition: Mapped[str] = mapped_column(
        String(32), nullable=False, default="REQUIRED", server_default="REQUIRED"
    )
    disposition_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    disposition_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    marked_by: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="assessment_target_fk_marked_by"), nullable=True
    )
    marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="TASK_SCOPE", server_default="TASK_SCOPE"
    )
    supplemented_from_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_import_batch.id", name="assessment_target_fk_supplement_batch"), nullable=True
    )
    effective_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_session.id", name="assessment_target_fk_effective_session"), nullable=True
    )
    effective_external_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_external_result.id", name="assessment_target_fk_effective_external"),
        nullable=True,
    )


class AssessmentSession(TimestampMixin, Base):
    __tablename__ = "assessment_session"
    __table_args__ = (
        # `uq_session_task_student` **不在这里**：V1.2 把它删掉了（迁移 0014 那一步），
        # 换成下面那条带 `attempt_no` 的。删与加必须在**同一条迁移**里——
        # 中间那一个版本没有任何东西保证「一场任务一人一份在办的卷子」，
        # 并发的 `create_or_get_session` 正好会在那个窗口里插出两份。
        UniqueConstraint("task_id", "student_id", "attempt_no", name="uq_session_task_student_attempt"),
        # 「一个任务学生只有一条 is_effective=1」的载体。它不是 `(task_id, student_id)`
        # 唯一，而是**生成列**唯一：`is_effective=0` 的那些行那一列是 NULL，
        # NULL 在唯一索引里互不相等，于是历史场次与被作废的场次都不受限。
        UniqueConstraint("effective_task_student_key", name="uq_session_effective_task_student"),
        # 父表那一侧的键：`retest_plan` / `assessment_target` / `assessment_import_row`
        # / `assessment_external_result` 的复合外键指着 `(id, student_id)`。
        UniqueConstraint("id", "student_id", name="uq_session_id_student"),
        # 外部平台的幂等键。两列都可空，而没有外部编号的行两列都是 NULL——
        # MySQL 的唯一索引允许多个 NULL，所以校内作答的那些行一条都不受影响。
        UniqueConstraint(
            "external_source_system", "external_result_id", name="uq_session_external_source_result"
        ),
        # 这一条与 `assessment_session_ibfk_2`（scale_id）指着同一列，两样都不能省：
        # DDL 里有它，而它同时**让那个外键不必再建一条索引**——外键名一旦显式写出来，
        # MySQL 就会把自动建的那条索引命名成约束名（`assessment_session_ibfk_2`），
        # 于是索引集与真库对不上。
        Index("scale_id", "scale_id"),
        Index("ix_session_student_tested_at", "student_id", "tested_at"),
        Index("ix_session_import_batch_id", "import_batch_id"),
        Index("ix_session_calculation_status", "calculation_status"),
        Index("ix_session_school_student", "school_id", "student_id"),
        Index("ix_session_conflict_status", "conflict_status"),
        Index("ix_session_external_result", "external_source_system", "external_result_id"),
        ForeignKeyConstraint(
            ["task_id", "school_id"],
            ["assessment_task.id", "assessment_task.school_id"],
            name="assessment_session_fk_task_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["student.school_id", "student.id"],
            name="assessment_session_fk_student_school",
        ),
    )

    # 外键名**全部显式写出**，逐字是迁移 0001 建出来的那三个
    # （`assessment_session_ibfk_1` = student_id、`_ibfk_2` = scale_id、
    # `fk_session_task` = task_id）。不写名字时 MySQL 按**列在表里的次序**
    # 自动编号，于是「把某一列挪个位置」会静默改掉外键名——而名字是将来
    # `op.drop_constraint` 唯一能引用的东西，编号却没有任何东西看得见。
    # 2026-09-19 实测：模型里不写名字时，编号与真库**当场就对不上**
    # （模型给的是 _ibfk_1=task_id，真库是 _ibfk_1=student_id）。
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_task.id", name="fk_session_task"), nullable=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", name="assessment_session_ibfk_1"), nullable=False
    )
    scale_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_scale.id", name="assessment_session_ibfk_2"), nullable=False
    )
    scale_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How long the student took, measured from their FIRST ANSWER to submitting —
    # not from `started_at`, which is when the task was opened and so counts time
    # spent away from the page. Written once, at submit; NULL for rows that predate
    # this column and for sessions cleared by /reset.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IN_PROGRESS")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 同 `AssessmentTask.source`。导入的会话有两个后果，都在代码里挡住：
    # 学生不能再进这个答题会话（`create_or_get_session`），也不能 reset 它
    # （`reset_session`）——否则外部来源的答卷会被当成系统内作答呈现给学生。
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="IN_SYSTEM", server_default="IN_SYSTEM"
    )
    # ------------------------------------------------------------------
    # V1.2 新增。新列一律追加在末尾。
    # ------------------------------------------------------------------
    # **哪一天测的**，与 `created_at`（哪一天开的那张卷子）不是一个东西：
    # 导入的一批校外普查，`tested_at` 在文件里（可能是上个月），
    # 而 `created_at` 是导入那一刻。判重、冲突判定、统计口径全按 `tested_at` 走。
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 那个 `tested_at` 是怎么来的。默认 `PENDING_VERIFICATION` 是**诚实的默认值**
    # （历史行没人知道真实测评日），但它现在是恒定的——两条校内路径都没有回填它，
    # 记在 CLAUDE.md 已知缺口里。
    tested_at_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING_VERIFICATION", server_default="PENDING_VERIFICATION"
    )
    import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_import_batch.id", name="assessment_session_ibfk_4"), nullable=True
    )
    # 同一个 (任务, 学生) 的第几次。默认 1。
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # **没有 `server_default`**：DDL 里它是「先可空、回填、再 MODIFY 成 NOT NULL」。
    # 落点见 `AssessmentTarget.school_id_snapshot` 上那段说明。
    school_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 这份答卷是怎么来的（ONLINE / EXTERNAL_FULL_ANSWER …）。与 `source`
    # （IN_SYSTEM / IMPORTED）是两个粒度：`source` 说的是「谁测的」，
    # `source_type` 说的是「用什么方式进来的」。
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ONLINE", server_default="ONLINE"
    )
    external_source_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_result_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conflict_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NONE", server_default="NONE"
    )
    # 有效的那些场次才有生成列键，也才参与「当前状态」的口径（CLAUDE.md §11）。
    is_effective: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    # 被哪一场取代了（重复导入时留旧的那一场、把旧的指成失效）。
    supersedes_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_session.id", name="assessment_session_fk_supersedes"), nullable=True
    )
    # 测评**当时**的年龄，与 `student.age`（现在名册上的年龄）不同：名册年龄会随
    # 重导名册变，而这一场的年龄是那次测评的事实（缺口 8）。
    age_at_test: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_snapshot_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    answer_hash_algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 与 `tested_at_source` 同族：默认 `PENDING` 是诚实的，而两条校内路径都没回填它，
    # 所以新会话一律顶着「待计算」——记在 CLAUDE.md 已知缺口里。
    calculation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    calculation_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # 生成列。注解是 `str | None`：`is_effective=0` 或 `task_id IS NULL` 时它就是 NULL，
    # 而 `test_nullability_matches_the_model` 比的是模型注解与库里的 `NOT NULL`。
    effective_task_student_key: Mapped[str | None] = mapped_column(
        String(96),
        Computed(
            "CASE WHEN `is_effective` = 1 AND `task_id` IS NOT NULL "
            "THEN CONCAT(`task_id`, ':', `student_id`) ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )

    scale: Mapped["AssessmentScale"] = relationship()


def effective_session_predicate():
    """「这一场**算不算数**」的唯一定义，给「按人取最近一场」那一族用。

    `is_effective = 0` 是 §18.8 四档处置里两档的落点：`USE_EXTERNAL` 把在线的
    那一场降级（外部那份顶上来），`KEEP_BOTH_BUT_ONE_EFFECTIVE` 把外部那一场降级
    （两份都留，在线为准）。被降级的那一场**什么都没丢**——答卷、结果、维度分、
    它自己那一场的事件全都在（§1 的四层事实模型：人工复核不得修改原始答卷），
    它只是不再回答「他现在是什么状态」（§11 那一栏的口径）。所以过滤加在**取最近
    一场**这一层，而不是加在写入、删除或者作答记录上。

    它住在模型层而不是 `assessment_service`：`task_service` 也要用它，而
    `assessment_service` 依赖着 `task_service`（`effective_task_status` 那一对），
    反向 import 会成环。它与上面那条生成列用的是同一个判据，放在一起更好找。

    **它与 `assessment_service.latest_session_order()` 是一对，两个要一起用**：
    那个给排序，这个给 where。不把过滤并进排序里，是因为真正要过滤的只有
    「按人取最近一场」这一族——**「历次趋势」刻意不过滤**
    （`session_history_order()`，`care_service.get_care_case` 里那三行注释）：
    趋势是历史事实，被降级的那一场正该出现在那里，它确实是这名学生考过的一次。
    这是这个函数唯一难的地方——**谓词加错到趋势那一侧，趋势会少掉一个点，
    而不会有任何东西报错**。

    「有效」与「采纳」不是一件事：`USE_EXTERNAL` 之后外部那一场既 `ACCEPTED`
    又 `is_effective=1`；`KEEP_BOTH_BUT_ONE_EFFECTIVE` 之后那一场是 `ACCEPTED`
    但 `is_effective=0`——它被采纳了（文件是真的、分数是真的），只是不作数。
    """
    return AssessmentSession.is_effective.is_(True)


# 「这场测评他该不该参加」的四个码（第 8 期落地，§18.10）。**它们不是诊断，
# 也不是对学生的评价**，而是学校对一场测评的行政安排：请假、免测、已排除。
#
# 为什么由这一期定下来：需求说明书 §16.10 把**拒绝参加的**理由码**留成了
# `TODO_BUSINESS_CONFIRMATION`，而 §18.10 那一条算式里逐字列着「请假 - 免测 -
# 已排除」三个减项——算式要能跑，这三个码就必须存在。用户裁决「**编码是实现
# 自由度**」，所以这里按 §18.10 的中文各取一个码，理由码仍然不定义（见下）。
PARTICIPATION_REQUIRED = "REQUIRED"
PARTICIPATION_LEAVE = "LEAVE"
PARTICIPATION_EXEMPT = "EXEMPT"
PARTICIPATION_EXCLUDED = "EXCLUDED"

PARTICIPATION_DISPOSITIONS = (
    PARTICIPATION_REQUIRED,
    PARTICIPATION_LEAVE,
    PARTICIPATION_EXEMPT,
    PARTICIPATION_EXCLUDED,
)

# **应测名单之外的那三个**：它们是 §18.10 那条算式的三个减项，**顺序就是算式的
# 顺序**（`labels.ts` 的 `PARTICIPATION_DISPOSITION_LABELS` 按同一序排）。
# `REQUIRED` 不在里面——它是「没人动过这一行」的取值，也是默认值。
PARTICIPATION_EXCLUDED_DISPOSITIONS = (
    PARTICIPATION_LEAVE,
    PARTICIPATION_EXEMPT,
    PARTICIPATION_EXCLUDED,
)


def expected_participation_predicate():
    """「这一名目标学生**算不算应测**」的唯一定义，§18.10 那三个减项在这里。

    有效完成率 = 已完成有效结果 / 应测人数，而应测人数 = 目标人数 − 请假 − 免测
    − 已排除。这条谓词就是那个减号。

    它住在模型层而不是 `task_service`，理由与 `effective_session_predicate` 逐字
    相同：那个模块要为「按场算」的完成明细用它，`analytics_service` 与 `care_service`
    的完成率也要用它，而这三处互相 import 的方向是**反的**（`assessment_service`
    依赖 `task_service`）。一处定义、四处引用，漂一个不会有任何东西报错。

    **分子也用它。** §18.10 写的是「已完成有效结果 / 应测人数」，一个被标记为
    请假/免测的学生**即使后来还是答了卷**，也不进这一场的完成率——他不在应测名单里。
    这与「他答过」这件事不冲突：那一行仍然是 `COMPLETED`，完成明细里照旧一行一人、
    照旧带着他的分（§1 的四层事实模型：标记管理事实不修改原始答卷）。这样定的
    唯一理由是**分子与分母必须来自同一个集合**：一名被标记免测的学生后来又答了卷时，
    按 `status` 数分子、按 `expected` 数分母会给出一个**超过 100% 的完成率**，
    而一个超过 100% 的完成率会让读者怀疑整张报表。

    「排除」不是「删掉」：那一行还在，`disposition_reason` / `marked_by` /
    `marked_at` 都在，完成明细里照旧列出来。它与 §1 那条「关闭档案不得删除历史
    记录」是同一条。
    """
    return AssessmentTarget.participation_disposition.notin_(
        PARTICIPATION_EXCLUDED_DISPOSITIONS
    )


def active_task_predicate():
    """「这场任务还算不算数」的唯一定义：作废的那些不算。

    作废（`VOID`）是 §4.1 给任务治理定的两条路里温和的那一条——任务行、目标行、
    会话、答卷、结果**一条都不删**，只是这一场从此不参与任何「当前状态」的统计
    （§4.12）。硬删（`HARD_DELETE`）才是真的删行，而它要求那一场**一条答卷都没有**。

    它住在模型层而不是某一个 service，理由与 `effective_session_predicate` /
    `expected_participation_predicate` 逐字相同：`analytics_service`、`care_service`
    与 `task_service` 都要用它，而这三处互相 import 的方向是反的，各写一份必然漂，
    漂了不会有任何东西报错。

    **用它的地方要看清是内连接还是外连接。** 三处消费它：

    - `analytics_overview` / `counselor_workbench` 是内连接，摆在 `WHERE` 里；
    - `analytics_by_grade` / `analytics_by_class` 把 `assessment_target`
      **外连接**进来（「这个年级有没有人属于我的范围」这件事不该因为一场任务作废而
      变成 0 行），所以它必须进 `outerjoin` 的 **ON 子句**——写进 `WHERE` 会把外连接
      悄悄变成内连接，把「没有目标行的学生」整片丢掉，而屏幕上只是少了几行。

    它与 `AssessmentTask.status` 那一列**不是**同一回事：那一列由写入方写（`DRAFT` /
    `PAUSED` / `CLOSED` 是人写的），`VOIDED` 也是人写的、且是**终态**。
    `effective_task_status`（§12）推的是「这一场现在走到哪一步了」，与「还算不算数」
    是两个问题——一场已结束的普查仍然算数，它的完成率是该进报表的。
    """
    return AssessmentTask.status != "VOIDED"


class AssessmentTaskScope(Base):
    """任务设计时选的**范围**（与发放之后的 `assessment_target` 快照是两件事）。

    `assessment_target` 回答「实际发给了谁」，这一张回答「当时是按什么范围发的」。
    两者不同是常态——发放之后名册上转进来一个学生，目标行会补、范围不会变。
    只留目标行的话，「这场普查当初打算测谁」就没有答案了。

    `scope_type` 与非空列的一一对应（SCHOOL 只填 `school_id`…）由服务层保证，
    理由与 `UserScope` 上那段相同：MySQL 8.0.13 不认 CHECK（DDL 第 751-753 行）。
    """

    __tablename__ = "assessment_task_scope"
    __table_args__ = (
        Index("ix_task_scope_task", "task_id"),
        Index("ix_task_scope_school", "school_id"),
        Index("ix_task_scope_grade", "grade_id"),
        Index("ix_task_scope_class", "class_id"),
        Index("ix_task_scope_student", "student_id"),
        ForeignKeyConstraint(
            ["task_id", "school_id"],
            ["assessment_task.id", "assessment_task.school_id"],
            name="assessment_task_scope_fk_task_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "grade_id"],
            ["grade.school_id", "grade.id"],
            name="assessment_task_scope_fk_school_grade",
        ),
        ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["class_group.school_id", "class_group.id"],
            name="assessment_task_scope_fk_school_class",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["student.school_id", "student.id"],
            name="assessment_task_scope_fk_school_student",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_task.id", name="assessment_task_scope_ibfk_1"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("school.id", name="assessment_task_scope_ibfk_2"), nullable=False
    )
    grade_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade.id", name="assessment_task_scope_ibfk_3"), nullable=True
    )
    class_id: Mapped[int | None] = mapped_column(
        ForeignKey("class_group.id", name="assessment_task_scope_ibfk_4"), nullable=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("student.id", name="assessment_task_scope_ibfk_5"), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", name="assessment_task_scope_ibfk_6"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answer"
    __table_args__ = (UniqueConstraint("session_id", "question_id", name="uq_answer_session_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("scale_question.id"), nullable=False)
    answer: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[int] = mapped_column(nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AssessmentResult(Base):
    __tablename__ = "assessment_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False, unique=True)
    validity_score: Mapped[int] = mapped_column(nullable=False)
    validity_status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_score: Mapped[int] = mapped_column(nullable=False)
    total_level: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DimensionResult(Base):
    __tablename__ = "dimension_result"
    __table_args__ = (UniqueConstraint("session_id", "dimension_code", name="uq_dimension_session_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False)
    dimension_code: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(nullable=False)
    level: Mapped[str] = mapped_column(String(32), nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)


class RiskEvent(Base):
    __tablename__ = "risk_event"
    __table_args__ = (
        # 同一场答卷上，同一条触发规则、同一个规则版本只许有一条事件。
        # `rule_version` 在键里，所以「规则升级之后重算」能留下两条——那正是想要的：
        # 旧的那条记录的是当时按什么标准判的（CLAUDE.md §6：阈值随规则版本走，
        # 一次误改不该追溯改写历史结果的解释）。
        #
        # 本列在 V1.0 的写入路径上没有被回填，`server_default='LEGACY_UNKNOWN'` 顶着，
        # 于是同一条规则在同一场上重复触发会撞 1062 —— 落点见 CLAUDE.md 已知缺口。
        UniqueConstraint("session_id", "trigger_rule", "rule_version", name="uq_risk_event_session_trigger_rule"),
        Index("ix_risk_event_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(ForeignKey("assessment_session.id"), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_rule: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user_account.id"), nullable=True)
    # ------------------------------------------------------------------
    # V1.2 新增。新列一律追加在末尾（见 `models/care.py` 的 `StudentCareCase`）。
    # ------------------------------------------------------------------
    # 信号的**种类**（人工复核 / 复测建议 / 筛查信号），与 `risk_type`（具体的
    # 触发类型）是两个粒度：`risk_type` 是「怎么触发的」，`signal_type` 是
    # 「这件事归谁办」。取值域照 DDL 第 539-557 行的回填 CASE —— 那三支是
    # 数据里实际存在的值，不是猜的。
    #
    # **没有 `server_default`**：DDL 里它是「先可空、回填、再 MODIFY 成 NOT NULL」。
    # V1.0 的写入路径（`assessment_service.maybe_raise_risk_events`）必须自己给值。
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # 「这条信号要不要人去看一眼」。它是 `signal_type` 的函数（人工复核那一类是 1），
    # 单独存一列是为了让「待复核」能走索引，不必每次解析 `signal_type`。
    requires_manual_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # 这一条是按哪个规则版本判出来的。V1.0 的行没有这个信息，`LEGACY_UNKNOWN`
    # 是这个诚实的答案（与 `tested_at_source='PENDING_VERIFICATION'` 同族）。
    rule_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="LEGACY_UNKNOWN", server_default="LEGACY_UNKNOWN"
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="risk_event_fk_voided_by"), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
