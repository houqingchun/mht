from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin


class School(TimestampMixin, Base):
    __tablename__ = "school"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)


class Grade(TimestampMixin, Base):
    __tablename__ = "grade"
    __table_args__ = (
        # 父表那一侧的键，给 `class_group_ibfk_3` / `student_ibfk_4` /
        # 两个 `assessment_*_fk_*_school_grade` 指着。
        UniqueConstraint("school_id", "id", name="uq_grade_school_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)


class ClassGroup(TimestampMixin, Base):
    __tablename__ = "class_group"
    __table_args__ = (
        UniqueConstraint("school_id", "id", name="uq_class_school_id"),
        # 「这个班属于这所学校的这个年级」——`grade_id` 与 `school_id` 分开写是可能的，
        # 写岔了以后「初一（1）班」会挂到另一所学校的初一名下，而每一列看起来都对。
        # 这一条与 `student_ibfk_4`（学生 vs 年级）是同族的三条之一。
        ForeignKeyConstraint(
            ["school_id", "grade_id"],
            ["grade.school_id", "grade.id"],
            name="class_group_ibfk_3",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    grade_id: Mapped[int] = mapped_column(ForeignKey("grade.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)


class Student(TimestampMixin, Base):
    __tablename__ = "student"
    __table_args__ = (
        UniqueConstraint("school_id", "student_no", name="uq_student_school_no"),
        # 父表那一侧的键，给 `assessment_*_fk_student_school` 一族指着。
        UniqueConstraint("school_id", "id", name="uq_student_school_id"),
        # 「这名学生的年级 / 班级属于他自己的那所学校」。有了这两条之后，
        # `student → grade` 与 `student → class_group` **各有两条路径**
        # （单列的与复合的），所以下面那两个 relationship 必须显式声明
        # `foreign_keys=`，否则 SQLAlchemy 会以 `AmbiguousForeignKeysError`
        # 拒绝这个映射——那是在 import 期就炸，不是运行时。
        ForeignKeyConstraint(
            ["school_id", "grade_id"], ["grade.school_id", "grade.id"], name="student_ibfk_4"
        ),
        ForeignKeyConstraint(
            ["school_id", "class_id"], ["class_group.school_id", "class_group.id"], name="student_ibfk_5"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_no: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    masked_name: Mapped[str] = mapped_column(String(64), nullable=False)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    grade_id: Mapped[int] = mapped_column(ForeignKey("grade.id"), nullable=False)
    class_id: Mapped[int] = mapped_column(ForeignKey("class_group.id"), nullable=False)
    # Nullable: the roster predates both fields, and the import treats them as
    # optional. `gender` holds a GENDER vocabulary code ("MALE"/"FEMALE").
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 存下来的整数年龄，不是现算的：0011 迁移把 `birth_date` 换成了它。
    # 0009 当初选的是「存出生日期、读取时现算」，理由是过完生日那一刻存下来的年龄就错了；
    # 2026-09-17 按学校要求反过来——名册上只有年龄这一个数，现算没有输入可算。
    # 代价写在明处：**这一列不会自己变**，学校不重导名册，界面上的年龄就停在去年。
    # 所以按年龄消歧的地方（`assessment_import_service`）保留 ±1 岁的容错。
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)

    school: Mapped[School] = relationship()
    # `foreign_keys=` 是**必需的**，不是风格：上面那两条复合外键给
    # `student → grade` / `student → class_group` 各加了第二条路径，
    # 不指名的话 SQLAlchemy 无法判断该用哪一条。
    # 指定 `grade_id`（单列那条）是想要的读法：「他在哪个年级」由那一列决定，
    # 复合外键只是额外保证那个年级属于同一所学校。
    grade: Mapped[Grade] = relationship(foreign_keys=[grade_id])
    class_group: Mapped[ClassGroup] = relationship(foreign_keys=[class_id])

