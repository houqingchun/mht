"""目标行的**七列快照**：一场测评**当时**，学校看到的是谁（§8.2 / §16.2 / §18.2）。

`assessment_target` 上有七列快照，其中 `school_id_snapshot` 在 V1.2 的对齐阶段就已经有
写入方（那个 NOT NULL 列没有默认值，不写就插不进去），另外六列（学号 / 姓名 / 年级名 /
班级名 / 性别 / 年龄）**只加了列、没有写入方**——所以 2026-09-19 之前每一行都是 NULL。

## 为什么要抄一份，而不是读的时候 join 名册

`student` 那一行回答「他**现在**是谁」：转班、改名、复读、毕业之后它都会变。而一份去年
的完成率报表要回答的是「这场测评当时，学校看到的是谁」——按现在的名册解释它，等于用今天
的事实改写去年的事。这与 CLAUDE.md §1 的四层事实模型是同一条规矩：学校管理事实
（`risk_event` / `assessment_task` / `assessment_target`）记的是发生时的事，不该被后来的
名册漂移改写。

## 四个写入方必须走同一个定义

发放目标行的地方有四处：`task_service.create_school_assessment_task`（建任务）、
`task_service.supplement_targets`（补发）、`assessment_import_service._mark_target_completed`
（外部导入收下一行）、`db/seed.py`（开发种子）。各写一份 `AssessmentTarget(...)` 就是四种
「快照是什么」的定义，而它们的漂移不会有任何东西看得见——直到有人真的按快照对账。

`tests/factories.py` 在测试那一侧解决的是同一个问题（「把能从参数推出来的列补齐」，且其余
字段 `**overrides` 直通），写法照它：这个函数也只补齐快照七列，**不碰** `status` /
`completed_at` / `target_source` 那些调用方自己的判断。
"""

from typing import Any

from app.models.organization import Student


def target_snapshot(
    student: Student, *, grade_name: str | None = None, class_name: str | None = None
) -> dict[str, Any]:
    """这名学生**此刻**的七列快照，可以直接 `**` 进 `AssessmentTarget(...)`。

    `grade_name` / `class_name` 是可选的**预先取好的名字**。批量发放目标行时（一场全校
    普查是一千行）调用方已经 join 过 `grade` / `class_group`，再让这里逐行走 relationship
    就是两千次额外的 SELECT；单个学生的调用方（种子、导入收行）不传，让它自己懒加载。

    `student_name_snapshot` 存的是 `student.name` 而**不是** `student.masked_name`：
    快照是身份事实，而 `masked_name` 只是一列叫这个名字的展示名（CLAUDE.md §1 记着它
    在生产里和 `name` 一样实）。遮蔽是**读**的时候的事，由读的那一侧现算。
    """
    grade = student.grade
    class_group = student.class_group
    return {
        "school_id_snapshot": student.school_id,
        "student_no_snapshot": student.student_no,
        "student_name_snapshot": student.name,
        # 调用方给了就用它的（批量发放那条路已经 join 过），没给才自己去取。
        # `is not None` 而不是真值判断：名字都非空，但「调用方没取」与「取到空串」
        # 是两件事，混在一起的话将来某天一个空名字会静默退化成一次懒加载。
        "grade_name_snapshot": grade_name if grade_name is not None else _name(grade),
        "class_name_snapshot": class_name if class_name is not None else _name(class_group),
        "gender_snapshot": student.gender,
        "age_snapshot": student.age,
    }


def _name(row) -> str | None:
    """`Grade` / `ClassGroup` 的 `name`，行不在就是 `None`。

    两个 `*_id` 都是 NOT NULL 且各有复合外键兜着，所以生产数据里取不到行这件事
    不会发生；但 `Student` 对象也可能是一个还没 flush 的瞬态对象（测试夹具里就有），
    那时 `student.grade` 是 `None` 而 `.name` 会 `AttributeError`——一次快照不该
    因为调用方手上是哪种 `Student` 而换个死法。
    """
    return row.name if row is not None else None
