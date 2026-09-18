from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
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

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)


class ClassGroup(TimestampMixin, Base):
    __tablename__ = "class_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("school.id"), nullable=False)
    grade_id: Mapped[int] = mapped_column(ForeignKey("grade.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)


class Student(TimestampMixin, Base):
    __tablename__ = "student"
    __table_args__ = (UniqueConstraint("school_id", "student_no", name="uq_student_school_no"),)

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
    grade: Mapped[Grade] = relationship()
    class_group: Mapped[ClassGroup] = relationship()

