"""V1.2 的六张「导入事实」表（需求说明书 §10 / §18，增量 DDL 第二、三节）。

为什么单独一个模块：这六张表回答的是同一个问题——**一批数据从外面进来，它到底是什么、
每一行去了哪里**。它们与 `models/assessment.py` 的区别不是主题，是**事实的层次**：

| 层 | 表 | 回答 |
|---|---|---|
| 导入批次事实 | `student_roster_import_batch` / `assessment_import_batch` | 这一批是什么时候、谁、拿哪个文件导的 |
| 导入行事实 | `student_roster_import_row` / `assessment_import_row` | 文件里的第 N 行落到了哪个学生身上，为什么 |
| 外部结果事实 | `assessment_external_result` | 外部平台自己给的那个结果（不是本系统算的） |
| 名册年龄变更事实 | `student_age_change_log` | `student.age` 这一列被谁改过、从多少改到多少 |

**它们不覆盖任何既有事实**（CLAUDE.md §1 那条四层模型）：`assessment_external_result`
是「外部平台说这个学生是多少分」，`assessment_result` 是「本系统按规则算出来的是多少分」，
两者并存、各有出处。导入的会话（`assessment_session.source='IMPORTED'`）仍然是答卷事实，
一个字不改。

## 一张表一个 FK 名，而且名字是从 DDL 逐字抄来的

这一组表里的外键**全部显式命名**（`student_roster_import_batch_fk_school` 这种读得懂的），
与 `models/assessment.py` 里那些不带名字的（MySQL 自动给 `<表>_ibfk_<N>`）不同。
理由是纠正过的一个洞：`test_sql_schema_matches_models.py` 现在会检查「模型里显式命名的
外键，在 SQL 快照里必须同名同形」——名字漂了没有任何别的东西看得见，而迁移将来会
`op.drop_constraint(<名字>)`。抄 DDL 是唯一不会漂的来源。

## 这里是那个二元环

`assessment_import_row.external_result_record_id → assessment_external_result.id`
与 `assessment_external_result.row_id → assessment_import_row.id` 互为外键：先有一行
原始行、再挂上外部结果、最后回填行的指针。**环无法线性化**，所以 SQL 快照里那个外键
必须写成 `ALTER TABLE … ADD CONSTRAINT`（排在所有 `CREATE TABLE` 之后），
`use_alter=True` 说的就是这件事。
"""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class StudentRosterImportBatch(TimestampMixin, Base):
    """一份学生名册文件的导入批次。

    「名册导入」在 V1.0 里是一次无痕的动作（`student_import_service` 只写
    `student` 与一条审计）。V1.2 要能回答「这一行是谁导进来的、用的是哪份文件」，
    所以批次的**文件指纹**（`file_sha256`）存下来——同一份文件导两次是可以查出来的。

    **这张表上没有 `source_system`**，与 `assessment_import_batch` 不同：那个字段
    记的是「这份作答来自哪个外部平台」，而名册只有学校自己手上这一份，没有第二个
    来路。别照着邻居那张表的字段名来这里找它。
    """

    __tablename__ = "student_roster_import_batch"
    __table_args__ = (
        UniqueConstraint("batch_no", name="uq_roster_import_batch_no"),
        # 下面这些**看着多余的**索引是必写的，而且名字要与 DDL 逐字一致——
        # 见模块开头那段说明：MySQL 为一个外键找覆盖索引，找不到就**按约束名**
        # 建一条；DDL 里显式声明了按列名的那一条，于是外键就复用它。
        # 模型里漏掉的话，`create_all` / 迁移会造出 `..._fk_school` 而不报错，
        # 于是模型建出来的库与迁移建出来的库索引名不同——两处都"对"，却对不上。
        Index("ix_roster_import_school_created", "school_id", "created_at"),
        Index("ix_roster_import_operator", "imported_by"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("school.id", name="student_roster_import_batch_fk_school"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # `CHAR(64)` 而不是 `varchar(64)`：十六进制摘要定长，DDL 就是这么写的。
    file_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    imported_by: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", name="student_roster_import_batch_fk_operator"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PREVIEW", server_default="PREVIEW")
    total_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    created_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    updated_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    skipped_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    error_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")


class StudentRosterImportRow(Base):
    """名册文件里的第 N 行，以及它落到了哪个学生身上。

    与 `AssessmentImportRow` 相比少一整套「匹配」字段（`raw_*` / `normalized_*` /
    `match_confidence`）：名册导入的判据是**学号**（`student_import_service` 按
    `(school_id, student_no)` 查重），一个确定的键，没有可消歧的余地。
    """

    __tablename__ = "student_roster_import_row"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_no", name="uq_roster_import_row_no"),
        Index("ix_roster_import_row_student", "student_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("student_roster_import_batch.id", name="student_roster_import_row_fk_batch"), nullable=False
    )
    row_no: Mapped[int] = mapped_column(nullable=False)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("student.id", name="student_roster_import_row_fk_student"), nullable=True
    )
    student_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    grade_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    class_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    age: Mapped[int | None] = mapped_column(nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    conflict_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssessmentImportBatch(TimestampMixin, Base):
    """一批校外测评结果的导入批次（MHT 测评记录导入）。

    `import_mode` / `conflict_policy` / `out_of_scope_policy` / `allow_age_overwrite`
    这四个是**这一批的处置口径**，与 CLAUDE.md 缺口 8 记的那套「文件级一次选择」
    一一对应；存下来是因为事后再看那一批数据时，「当时按什么口径放进来的」比
    「导进来了什么」更难复原。
    """

    __tablename__ = "assessment_import_batch"
    __table_args__ = (
        UniqueConstraint("batch_no", name="uq_assessment_import_batch_no"),
        Index("ix_assessment_import_school_created", "school_id", "created_at"),
        Index("imported_by", "imported_by"),
        Index("task_id", "task_id"),
        Index("tested_at", "tested_at"),
        Index("ix_import_file_hash", "source_system", "file_sha256"),
        Index("ix_import_duplicate_of", "duplicate_of_batch_id"),
        # 批次与任务属于同一所学校（增量 DDL 第十节）。
        ForeignKeyConstraint(
            ["task_id", "school_id"],
            ["assessment_task.id", "assessment_task.school_id"],
            name="assessment_import_batch_fk_task_school",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    school_id: Mapped[int] = mapped_column(
        ForeignKey("school.id", name="assessment_import_batch_ibfk_1"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 这一批**叫什么**——与 `file_name` 是两个问题：文件名说的是「这份数据从哪个文件来」
    # （学校的导出常常叫 `结果(3).csv`），批次名称说的是「这一批叫什么」，而它会成为
    # 那场批次任务的名字、出现在任务列表上给全校看。
    #
    # V1.2 的增量 DDL 在这张表上只有 `file_name`，这一列是 V1.2 功能层第 4 期补的
    # （迁移 `0016`）：`commit_batch` 之前拿 `file_name` 当任务名，于是任务列表上会印出
    # `结果(3).csv`——而 V1.0 那一版用的就是操作员填的批次名称，界面上那个输入框
    # 一直都在（`_batch_errors` 至今校验它非空、不超 128 字）。
    batch_name: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    file_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    source_system: Mapped[str] = mapped_column(
        String(128), nullable=False, default="UNKNOWN", server_default="UNKNOWN"
    )
    source_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    # 这一批的**测评日期**，不是导入日期。判重与冲突判定都按它算
    # （`assessment_import_service.month_bounds`），所以它与 `created_at` 是两个东西。
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    import_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="EXTERNAL_FULL_ANSWER", server_default="EXTERNAL_FULL_ANSWER"
    )
    conflict_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="REQUIRE_REVIEW", server_default="REQUIRE_REVIEW"
    )
    out_of_scope_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="REJECT", server_default="REJECT"
    )
    allow_age_overwrite: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duplicate_of_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_import_batch.id", name="assessment_import_batch_ibfk_4"), nullable=True
    )
    # `resolution` 的取值与 `assessment_import_service.RESOLUTIONS` 同一套
    # （`overwrite` / `skip` / `NONE`），学生信息导入那条链路上也是它——
    # 各写一份字面量就会有第三种写法悄悄冒出来（缺口 8）。
    resolution: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE", server_default="NONE")
    total_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    created_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    updated_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    skipped_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    error_rows: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PREVIEW", server_default="PREVIEW")
    imported_by: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", name="assessment_import_batch_ibfk_2"), nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_task.id", name="assessment_import_batch_ibfk_3"), nullable=True
    )


class AssessmentImportRow(Base):
    """外部测评文件里的一行：原始值、归一化值、匹配结果、处置结果。

    `raw_*` 与 `normalized_*` 是**两套值，都要留**：`raw_*` 是文件里印的字
    （「七年一班」「张 三」），`normalized_*` 是本系统归一化之后拿去匹配的
    （`701`）。只留一套的话，「为什么这一行匹配错了」永远答不上来——
    而匹配错了正是这条链路上唯一的故障形态。
    """

    __tablename__ = "assessment_import_row"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_no", name="uq_import_row_batch_no"),
        Index("student_id", "student_id"),
        Index("session_id", "session_id"),
        Index("ix_import_row_match_status", "batch_id", "match_status"),
        Index(
            "ix_import_row_normalized_match",
            "normalized_grade_name",
            "normalized_class_name",
            "normalized_name",
        ),
        Index(
            "ix_import_row_matched_grade_class",
            "matched_school_id",
            "matched_grade_id",
            "matched_class_id",
        ),
        # 这一条与下面那个**环上的**外键指着同一列。名字不同是有原因的，别合并：
        # `ix_import_row_external_record` 是 DDL 在 `CREATE TABLE` 里建的，
        # 而 `assessment_import_row_fk_external_record` 是后面那条 `ALTER TABLE`
        # 加约束时**复用**了它，所以外键没有自己那一条索引（真库的
        # `SHOW CREATE TABLE` 就是这样）。
        Index("ix_import_row_external_record", "external_result_record_id"),
        # 「这一行匹配到的年级 / 班级属于同一所学校」——`matched_school_id` 与
        # `matched_grade_id` 分开写是可能的，写岔了以后「这一行匹配错了」这条
        # 唯一的故障形态就无从判断。
        ForeignKeyConstraint(
            ["session_id", "student_id"],
            ["assessment_session.id", "assessment_session.student_id"],
            name="assessment_import_row_fk_session_student",
        ),
        ForeignKeyConstraint(
            ["matched_school_id", "matched_grade_id"],
            ["grade.school_id", "grade.id"],
            name="assessment_import_row_fk_matched_school_grade",
        ),
        ForeignKeyConstraint(
            ["matched_school_id", "matched_class_id"],
            ["class_group.school_id", "class_group.id"],
            name="assessment_import_row_fk_matched_school_class",
        ),
        # 环的另一半在这里：本表 → assessment_external_result。
        # `use_alter=True` 是给 `metadata.create_all()` 用的（先建表、后补约束）；
        # 生产建表走迁移，迁移里那一条也是 `ALTER TABLE … ADD CONSTRAINT`，
        # 与 SQL 快照的写法一致（DDL 第 287 行）。
        ForeignKeyConstraint(
            ["external_result_record_id"],
            ["assessment_external_result.id"],
            name="assessment_import_row_fk_external_record",
            use_alter=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_import_batch.id", name="assessment_import_row_ibfk_1"), nullable=False
    )
    row_no: Mapped[int] = mapped_column(nullable=False)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("student.id", name="assessment_import_row_ibfk_2"), nullable=True
    )
    raw_student_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_grade_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_class_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_age: Mapped[int | None] = mapped_column(nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    normalized_grade_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalized_class_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_school_id: Mapped[int | None] = mapped_column(nullable=True)
    matched_grade_id: Mapped[int | None] = mapped_column(nullable=True)
    matched_class_id: Mapped[int | None] = mapped_column(nullable=True)
    match_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", server_default="PENDING")
    match_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    # **`none_as_null=True` 是必须的，不是风格。** SQLAlchemy 的 `JSON` 默认把 Python
    # 的 `None` 写成 **JSON 字面量 `null`**（不是 SQL NULL），于是「这一行没有候选」
    # 在库里是 `JSON_TYPE(col) = 'NULL'` 而不是 `col IS NULL`。`list_import_rows` 正是
    # 拿 `IS NULL` 判「候选也为空」的（那一支决定没匹配上的行给不给读者看），
    # 默认行为下它**恒假**：所有「没匹配到学生」的行——也就是操作员最需要看的那几行
    # ——会从明细里整片消失，而同一屏上 `row_counts` 通过另一次查询说「有问题 2 行」。
    # 两个数说的是同一件事，却各说各话（§11 那条）。
    #
    # 存量行由 `0017_json_null_normalize` 就地改写。**别把它删掉**：删了之后旧行
    # 又是 JSON null，而这一支又开始静默漏行，但测试里新写的行是 SQL NULL，全绿。
    candidate_student_ids: Mapped[list | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    conflict_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # §18.8 的四种来源处置（`KEEP_ONLINE` / `USE_EXTERNAL` / `REJECT_EXTERNAL` /
    # `KEEP_BOTH_BUT_ONE_EFFECTIVE`，常量在 `assessment_import_service`）。
    #
    # **与 `resolution` 是两次选择、两个问题**：那一列回答「这一行写不写进去」，
    # 这一列回答「写的时候以哪一份为准、另一份留不留」。合成一列会让整批的「覆盖」
    # 顺手变成一种来源裁决——而 §20#11 要的正是「在线答卷不能被**静默**覆盖」。
    #
    # 留空是「这个问题没问过」（只有冲突行会问），不是「选了某个默认」——
    # 与 `age_resolution` 逐字同一条。迁移 `0018`。
    conflict_resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    out_of_scope_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 年龄覆盖是一条**有名有姓的事实**（缺口 8：用户要求「发现不一致就问要不要覆盖名册」），
    # 所以旧值、新值、谁决定的、什么时候决定的四个都留着。
    age_before: Mapped[int | None] = mapped_column(nullable=True)
    age_after: Mapped[int | None] = mapped_column(nullable=True)
    age_resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", name="assessment_import_row_ibfk_4"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_session.id", name="assessment_import_row_ibfk_3"), nullable=True
    )
    external_result_record_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssessmentExternalResult(Base):
    """外部平台自己给的那一份结果。

    它与 `AssessmentResult` **不是一回事**，不能合并：那一张是引擎按本系统的
    `scale_rule` 版本算出来的（`rule_version` 是解释那一行的依据，CLAUDE.md §6），
    这一张是校外平台说的数——维度划分、判据、常模都可能是另一套。
    合并会让「这条结果的 rule_version 是哪一个」失去答案。

    `result_payload_json` 可能含外部平台的原始敏感字段，DDL 那句注释（第 284 行）
    说的是**生产实现要按敏感字段策略加密或最小化保存**；本阶段只对齐结构，
    这条策略属于实现 V1.2 功能的那一阶段，记在缺口里。
    """

    __tablename__ = "assessment_external_result"
    __table_args__ = (
        UniqueConstraint("source_system", "external_result_id", name="uq_external_result_source_key"),
        UniqueConstraint("batch_id", "row_id", name="uq_external_result_batch_row"),
        # 父表那一侧的键：`retest_plan` / `assessment_target` 的复合外键指着
        # `(id, student_id)`，MySQL 要求被引用的列组合上有唯一索引。
        # 它不是「业务上 id 与 student_id 唯一」——id 是主键，本来就唯一；
        # 这条键存在的唯一理由是让复合外键指得着（DDL 第 273 行）。
        UniqueConstraint("id", "student_id", name="uq_external_result_id_student"),
        Index("ix_external_result_task_student", "task_id", "student_id"),
        Index("ix_external_result_verification", "verification_status"),
        # 这三条与 `assessment_import_batch_fk_task_school` 同族：外部结果、
        # 任务、学生、会话必须属于同一所学校。「任务外学生只能进入外部原始事实表，
        # 不能静默成为任务完成者」（DDL 第 704 行的原话）靠的就是它们。
        ForeignKeyConstraint(
            ["task_id", "school_id"],
            ["assessment_task.id", "assessment_task.school_id"],
            name="assessment_external_result_fk_task_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["student.school_id", "student.id"],
            name="assessment_external_result_fk_student_school",
        ),
        ForeignKeyConstraint(
            ["applied_session_id", "student_id"],
            ["assessment_session.id", "assessment_session.student_id"],
            name="assessment_external_result_fk_session_student",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_import_batch.id", name="assessment_external_result_fk_batch"), nullable=False
    )
    row_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_import_row.id", name="assessment_external_result_fk_row"), nullable=False
    )
    school_id: Mapped[int] = mapped_column(
        ForeignKey("school.id", name="assessment_external_result_fk_school"), nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_task.id", name="assessment_external_result_fk_task"), nullable=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", name="assessment_external_result_fk_student"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    external_result_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="EXTERNAL_SUMMARY", server_default="EXTERNAL_SUMMARY"
    )
    scale_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scale_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validity_score: Mapped[int | None] = mapped_column(nullable=True)
    total_score: Mapped[int | None] = mapped_column(nullable=True)
    # 同样收 `none_as_null`：理由与 `candidate_student_ids` 那段逐字相同（今天没有
    # `IS NULL` 的读者，但同一个坑不该在同一张表里留两份）。改名/加列时别只改一处。
    dimension_scores_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    result_payload_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING", server_default="PENDING"
    )
    applied_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_session.id", name="assessment_external_result_fk_session"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StudentAgeChangeLog(Base):
    """`student.age` 的每一次改动。

    这一列 V1.0 里有两个写入方（学生信息导入、测评导入选了「覆盖」时），
    而它**本来就是学校名册上的一个数**（CLAUDE.md §1：学校手上的名册只有年龄，
    现算没有输入可算）。两个写入方改同一列、又都不留痕时，
    「这次导入到底动没动名册」在事后无从判断——这张表就是那个答案。

    `reason` 存的是**中文原因**（与审计的 `detail` 同一个口径），不是枚举码：
    它会被人读，而读它的人正是要判断「这次导入该不该改这一列」的人。
    """

    __tablename__ = "student_age_change_log"
    __table_args__ = (
        # 前一条就是「这个学生的年龄变了多少次、最近一次什么时候」，
        # 也正是 `student_id` 外键复用的那一条。
        Index("ix_age_change_student_time", "student_id", "changed_at"),
        Index("ix_age_change_assessment_row", "assessment_import_row_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("student.id", name="student_age_change_log_fk_student"), nullable=False
    )
    roster_import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_roster_import_batch.id", name="student_age_change_log_fk_roster_batch"), nullable=True
    )
    assessment_import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_import_batch.id", name="student_age_change_log_fk_assessment_batch"), nullable=True
    )
    assessment_import_row_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_import_row.id", name="student_age_change_log_fk_assessment_row"), nullable=True
    )
    old_age: Mapped[int | None] = mapped_column(nullable=True)
    new_age: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    changed_by: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", name="student_age_change_log_fk_operator"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "AssessmentExternalResult",
    "AssessmentImportBatch",
    "AssessmentImportRow",
    "StudentAgeChangeLog",
    "StudentRosterImportBatch",
    "StudentRosterImportRow",
]
