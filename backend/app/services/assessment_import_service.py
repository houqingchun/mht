"""数据中心 → MHT测评记录导入：把**外部平台**做出来的普查结果导进本系统分析。

学校在别的平台上做了 2026 年心理普查，结果在系统之外。这份服务把它变成真正的
`assessment_session` / `assessment_answer` / `assessment_result`，于是分析、个案详情、
受控导出立刻都能用上这批数据。

形状与另两个导入（`student_import_service` / `scale_import_service`）一致：
`parse_* → preview_* → JWT 预览令牌 → commit_*`，坏单元格变成**学校看得见、改得动的一行错误**，
不是拒掉整份文件，也不是悄悄存成 NULL。

与「学生在本系统里作答」刻意不同的三处，都是用户确认过的决策：

* **定位不到的学生跳过并逐行报错**，不建学生、不建账号（名册归管理员的「学生信息导入」）；
* **建 COMPLETED 目标行**并挂在一个「外部导入」批次任务下——学校已确认接受它对
  全校完成率、关注率与高度关注导出的影响（CLAUDE.md 的决策记录里也记了这一条）；
* 会话与任务都打 `source="IMPORTED"`，学生在系统里既不能继续作答也不能重置它。

**同一个月只算一场任务，重复的那一条要人来拍板**（2026-09-17 用户要求）：
「重复性检测应该按月度为单位，不同月份的评测视为不同的测试任务」，所以

* 批次任务按 `(学校, 月份)` 找，找得到就复用，找不到才新建；**一条都没写进去时不建**——
  空任务在任务列表上永远显示「进行中」，还会被同月的下一次导入复用；
* 同一名学生在同一个月里已经有导入记录 → 预览时给出一条**待确认**（不是硬错误），
  由操作员选「覆盖上次」或「放弃这条」。

「年龄与名册不符」走同一套：它此前只是一句告警（照常导入、不动名册），现在也升成
**待确认**——因为「覆盖更新」这个选项的含义只能是**改动名册上的年龄**
（`student.age` 是那一列唯一的存储处，这条链路上没有第二个地方可以「覆盖」）。
两类待确认共用一次选择，因为它们问的是同一件事：「这份文件和库里已有的东西冲突了，
你要以文件为准，还是放弃这几条？」

**触发规则与系统内作答完全一致**（2026-09-17 用户确认，推翻了此前「只带进结果」的旧决策）：
评分之后照调 `maybe_raise_risk_events`，于是命中重点题（85/97 任一答「是」）就会开出
风险事件与关注档案，和学生在系统里交卷时走的是同一个函数。总分类别是重点关注但没命中重点题的，
两条路径一样**不建**——按总分放宽会造出「同一份答卷，系统内测和导进来结果不同」的不一致。

性别 `1/2` 与答案 `1/0` 是这份外部文件的约定（用户明确给了），不是本系统的词汇：
`2→MALE/1→FEMALE`、`1→YES/0→NO`，落库一律是系统里的编码。

**只收 .csv**：学校在外部平台上的导出另存为 CSV 之后再上传。所以这里只有一条解析
路径，也不为此引入任何 Excel 依赖（仓库里本来就没有）。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Any

import jwt
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    DimensionResult,
    RiskEvent,
)
from app.models.care import ManualReview
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale, ScaleQuestion
from app.security.data_scope import ensure_student_in_scope, student_in_scope
from app.services.assessment_service import (
    calculate_session,
    maybe_raise_risk_events,
    persist_result_and_dimensions,
    session_status_for,
)
from app.services.audit_service import write_audit

QUESTION_COUNT = 100

# 年级：文件里是裸数字 `1`，学校的班级编号首位是 `7`（见「学生信息导入」的
# `GRADE_CLASS_PREFIX`）。两种写法都认，中文名也认——同一份表在不同平台上导出来，
# 这一列三种样子都见过。
GRADE_BY_NUMBER = {"1": "初一", "2": "初二", "3": "初三", "7": "初一", "8": "初二", "9": "初三"}
GRADE_NAMES = {"初一": "初一", "初二": "初二", "初三": "初三"}
# 班级编号的首位数字 → 年级名，用来校验文件里已经写成 `704` 的那种值
CLASS_PREFIX_GRADE = {"7": "初一", "8": "初二", "9": "初三"}
GRADE_PREFIX = {"初一": "7", "初二": "8", "初三": "9"}

GRADE_INPUT_HINT = "年级应为 1/2/3（或 初一/初二/初三）"
CLASS_INPUT_HINT = "班级格式应为 4（初一 4 班）"
GENDER_INPUT_HINT = "性别 2 表示男、1 表示女"
# **注意方向：这份文件里 `2` 是男、`1` 是女**（2026-09-17 按学校手上的真实文件改，
# 此前反着写——`1→MALE`、`2→FEMALE`）。
#
# 反着写不是「显示错了性别」这么轻：这两个字段**只用来消歧**，从不写回名册
# （`commit_assessment_import` 不碰 `student.gender` / `student.age`），而
# `locate_student` 是拿 姓名 + 性别 + 年龄 + 年级 + 班级 五样一起找人的。所以性别一取反，
# 「同班同名两人、一男一女」这种本来能分开的情况就会**定位到另一个人**，
# 结果是张冠李戴地把 A 的答卷记到 B 名下，而且全程不报错——
# 性别「不符」的那条 warning 反而因为所有人都取反而变成噪音，没人会去看。
#
# 学生信息导入（`student_import_service`）走的是另一套约定，只认 `MALE/FEMALE` 或
# `男/女`，**不认数字**。两条链路各管各的常量，别把这里改成通用的。
GENDER_BY_NUMBER = {"2": "MALE", "1": "FEMALE", "男": "MALE", "女": "FEMALE"}
ANSWER_HINT = "应为 1（是）或 0（否）"

MAX_BATCH_NAME_LENGTH = 128
MAX_DURATION_SECONDS = 2147483647  # `duration_seconds` 是 Integer，超了 MySQL 会 500

# 「要人拍板」的两类冲突（2026-09-17 用户要求）。它们既不是错误（记录本身是好的），
# 也不是告警（不能就这么写进去）——预览时列出来，提交时由 `resolution` 一次性回答。
CONFLICT_AGE_MISMATCH = "AGE_MISMATCH"
CONFLICT_DUPLICATE = "DUPLICATE"

# 处置方式。**整份文件共用一次选择**：冲突行各自属于哪一类已经在明细里写清了，
# 让老师为 200 行逐行选一次只会让他在第 20 行开始乱点。
RESOLUTION_OVERWRITE = "overwrite"
RESOLUTION_SKIP = "skip"
RESOLUTIONS = {RESOLUTION_OVERWRITE, RESOLUTION_SKIP}
RESOLUTION_HINT = "处置方式应为覆盖（overwrite）或放弃（skip）"


def resolution_label(resolution: str | None) -> str:
    """处置方式的中文，写进审计的 `detail`。

    记中文而不是 `overwrite`：审计详情是**给人读的**轨迹（§8 记的是「实名 / 姓名遮蔽」
    而不是 `mask_names=True`，同一条道理）。`None` 是「文件里没有冲突，所以没问过」，
    与「问了、选了放弃」必须长得不一样——否则一行 `resolution=none` 会让人以为
    那次导入把冲突当成了默认放行。
    """
    return {
        RESOLUTION_OVERWRITE: "覆盖上次",
        RESOLUTION_SKIP: "放弃冲突行",
    }.get(resolution or "", "未涉及（无冲突）")

MONTH_CONFLICT_MESSAGE = "该学生在 {month} 已有一次外部导入的测评"


def month_bounds(tested_on: date) -> tuple[datetime, datetime]:
    """这个月（含）到下个月（不含）的区间。

    判重与批次任务都按它切：`submitted_at` 是测评日期（文件里那一格），
    所以「同一个月」问的就是「这两份文件的施测日期落在同一个自然月里吗」。
    """
    start = datetime(tested_on.year, tested_on.month, 1)
    end = datetime(tested_on.year + (1 if tested_on.month == 12 else 0), tested_on.month % 12 + 1, 1)
    return start, end

# 表头里的字段列。列名认不出来就是全局错误——一份没有「姓名」列的文件，
# 逐行报 200 次「缺少姓名」没有任何信息量。
FIELD_ALIASES = {
    "姓名": "name",
    "学生姓名": "name",
    "name": "name",
    "性别": "gender",
    "gender": "gender",
    "年龄": "age",
    "age": "age",
    "年级": "grade",
    "grade": "grade",
    "班级": "class_name",
    "班级名称": "class_name",
    "class_name": "class_name",
    "所用时间": "duration",
    "用时": "duration",
    "作答用时": "duration",
    "duration": "duration",
}

# `1.你晚上要睡觉时…` 与 `1、…` 都见过；纯数字表头也认。
_QUESTION_HEADER = re.compile(r"^\s*(\d{1,3})\s*(?:[.、．)。:：]|$)")
_DURATION = re.compile(r"^\s*(\d+)\s*(?:秒|s|S)?\s*$")

# 两者共用一句话，见 `_visible_candidates`
STUDENT_NOT_FOUND = "「{class_name}」没有叫「{name}」的学生"
CLASS_NOT_FOUND = "名册中没有「{class_name}」这个班级"


# --------------------------------------------------------------------------
# 解析：文件 → 逐行的字符串单元格
# --------------------------------------------------------------------------


def parse_assessment_import(filename: str, content: bytes) -> dict[str, Any]:
    """读成「列头 + 数据行」的字符串方阵，并做列级校验。

    返回 `{"header_errors": [...], "rows": [...]}`。`header_errors` 是**列级**问题
    （缺列、题号不全），交给 `preview` 与批次字段的问题合并成 `global_errors`；
    行级问题一律留到 `preview`，因为那一层才能给出「第几行、哪一格」。

    **只收 .csv。** 学校在外部平台上导出的结果，导进本系统之前先另存为 CSV
    （平台的导出、WPS、Excel 都能存），因此这条链路上只有一条解析路径。
    扩展名不认识就整份拒掉：拿一个 `.xlsx` 去喂 `csv.reader` 会读成一行乱码，
    每一行都报「缺少姓名」——那比一句「请另存为 .csv」难懂得多。
    """
    if not (filename or "").lower().endswith(".csv"):
        raise AppError(
            "VALIDATION_ERROR",
            "只支持 .csv 文件，请先将文件另存为 .csv 后重试",
            422,
        )
    grid = _read_csv(content)
    if not grid:
        raise AppError("VALIDATION_ERROR", "文件里没有数据", 422)

    header, *data_rows = grid
    columns, header_errors = _columns_from_header(header)
    rows = [_row_from_cells(row, columns, row_no) for row_no, row in enumerate(data_rows, start=2)]
    # 全空行（Excel 常见的尾部空行）直接丢掉，否则会变成一行「缺少姓名」的错误
    rows = [row for row in rows if any(value for value in row["cells"].values())]
    return {"header_errors": header_errors, "rows": rows}


def _read_csv(content: bytes) -> list[list[str]]:
    """按 UTF-8（带不带 BOM 都认）读；读不了再按 GBK 读一次。

    中文 Excel / WPS 的「另存为 CSV」在 Windows 上写出来的是 GBK，而那是学校最可能的
    操作路径。只按 UTF-8 解会抛 UnicodeDecodeError —— 一个 500，而它该是「照读不误」
    或者一句「请另存为 UTF-8 的 CSV」。两次都解不出来才报错（真的二进制文件）。
    """
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        return [list(row) for row in csv.reader(io.StringIO(text))]
    raise AppError(
        "VALIDATION_ERROR", "文件不是 UTF-8 或 GBK 编码的文本，请另存为 CSV 后重试", 422
    )


def _columns_from_header(header: list[str]) -> tuple[dict[str, Any], list[str]]:
    columns: dict[str, Any] = {"questions": {}}
    errors: list[str] = []
    duplicated: list[int] = []
    for index, raw in enumerate(header):
        label = (raw or "").strip()
        if not label:
            continue
        field = FIELD_ALIASES.get(label.lower() if label.isascii() else label)
        if field and field not in columns:
            columns[field] = index
            continue
        match = _QUESTION_HEADER.match(label)
        if match:
            question_no = int(match.group(1))
            if not 1 <= question_no <= QUESTION_COUNT:
                continue
            if question_no in columns["questions"]:
                duplicated.append(question_no)
                continue
            columns["questions"][question_no] = index

    missing_fields = [
        label
        for label, field in (("姓名", "name"), ("性别", "gender"), ("年龄", "age"), ("年级", "grade"), ("班级", "class_name"))
        if field not in columns
    ]
    if missing_fields:
        errors.append(f"缺少列：{'、'.join(missing_fields)}")
    if not columns["questions"]:
        errors.append("表头里没有认出任何题号列（题号列应写成 1.题干）")
    else:
        missing_questions = [
            number for number in range(1, QUESTION_COUNT + 1) if number not in columns["questions"]
        ]
        if missing_questions:
            errors.append(f"缺少题号列：{'、'.join(str(number) for number in missing_questions[:10])}")
        if duplicated:
            errors.append(f"重复的题号列：{'、'.join(str(number) for number in duplicated[:10])}")
    return columns, errors


def _row_from_cells(cells: list[str], columns: dict[str, Any], row_no: int) -> dict[str, Any]:
    def cell(index: int | None) -> str:
        if index is None or index >= len(cells):
            return ""
        return (cells[index] or "").strip()

    return {
        "row_no": row_no,
        # 用来判断「这一行是不是空的」：锯齿状的行、以及只填了后半截的行都算有内容
        "cells": {index: cell(index) for index in range(len(cells))},
        "name": cell(columns.get("name")),
        "gender": cell(columns.get("gender")),
        "age": cell(columns.get("age")),
        "grade": cell(columns.get("grade")),
        "class_name": cell(columns.get("class_name")),
        "duration": cell(columns.get("duration")),
        # 题号 1..100 各自对应的列。表头里没有这个题号（列级校验已经报了）时是空串，
        # 会在行级变成「第 N 题未作答」——比让整份文件因为一个表头写法而不可用要好。
        "answers": [cell(columns["questions"].get(number)) for number in range(1, QUESTION_COUNT + 1)],
    }


# --------------------------------------------------------------------------
# 预览：逐行校验 + 定位学生
# --------------------------------------------------------------------------


def preview_assessment_import(
    db: Session,
    parsed: dict[str, Any],
    *,
    batch_name: str,
    tested_on: date,
    actor: UserAccount,
) -> dict[str, Any]:
    global_errors = list(parsed["header_errors"]) + _batch_errors(batch_name, tested_on)
    if not _published_scale(db):
        global_errors.append("没有已发布的 MHT 量表版本，无法评分")

    school = school_for_import(db)
    rows: list[dict[str, Any]] = []
    importable_rows: list[dict[str, Any]] = []
    seen_students: dict[int, int] = {}
    for row in parsed["rows"]:
        item = _preview_row(
            db,
            row,
            school=school,
            tested_on=tested_on,
            actor=actor,
            seen_students=seen_students,
        )
        rows.append(item)
        if not item["errors"]:
            # 有冲突的行也进令牌：它们在提交时是**可以**写下去的，只是要操作员先选
            # 「覆盖」还是「放弃」。把冲突行挡在令牌之外，等于把「覆盖上次」这条路
            # 堵死——而那正是用户要的那个选项。
            importable_rows.append(item["payload"])
            seen_students[item["payload"]["student_id"]] = row["row_no"]

    ready = [item for item in rows if not item["errors"] and not item["conflicts"]]
    conflicted = [item for item in rows if not item["errors"] and item["conflicts"]]
    token = (
        create_preview_token(importable_rows, batch_name=batch_name, tested_on=tested_on)
        if importable_rows and not global_errors
        else None
    )
    return {
        "batch": {"name": batch_name, "tested_on": tested_on.isoformat()},
        "total": len(rows),
        # `valid_count` 只数**不需要任何决定**的行；有冲突的行单独报，否则操作员会
        # 以为点了「确认导入」就能全进去
        "valid_count": len(ready),
        "conflict_count": len(conflicted),
        # 与「学生信息导入」同口径：错误数是**有错的行数**，不是错误条数
        "error_count": len(rows) - len(ready) - len(conflicted),
        "warning_count": sum(1 for item in rows if item["warnings"]),
        "global_errors": global_errors,
        "rows": [item["display"] for item in rows],
        "preview_token": token,
    }


def _batch_errors(batch_name: str, tested_on: date) -> list[str]:
    errors = []
    if not batch_name:
        errors.append("批次名称不能为空")
    elif len(batch_name) > MAX_BATCH_NAME_LENGTH:
        # `AssessmentTask.name` 是 String(128)。sqlite 不查长度（测试全绿），
        # MySQL 严格模式下超长直接 500——所以这一条必须在导入前挡下来
        errors.append(f"批次名称过长（最多 {MAX_BATCH_NAME_LENGTH} 字）")
    if tested_on > date.today():
        # 未来日期会写出未来时间的会话与答卷：它们会落在按日期排序的各项统计前面，
        # 而且「已逾期」这种判断会对着一个还没发生的测评做
        errors.append("测评日期不能晚于今天")
    return errors


def _preview_row(
    db: Session,
    row: dict[str, Any],
    *,
    school: School | None,
    tested_on: date,
    actor: UserAccount,
    seen_students: dict[int, int],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    name = row["name"]
    if not name:
        errors.append("缺少姓名")
    gender = None
    if not row["gender"]:
        errors.append("缺少性别")
    else:
        gender = GENDER_BY_NUMBER.get(row["gender"])
        if gender is None:
            errors.append(GENDER_INPUT_HINT)
    age = _parse_age(row["age"], warnings)

    grade_name = _parse_grade(row["grade"], errors)
    class_name = _parse_class(row["class_name"], grade_name, errors)
    duration, duration_warning = _parse_duration(row["duration"])
    if duration_warning:
        warnings.append(duration_warning)

    answers: list[str] = []
    if len(row["answers"]) == QUESTION_COUNT:
        answers, answer_errors = _parse_answers(row["answers"])
        errors.extend(answer_errors)
    else:  # pragma: no cover - 列级校验已经拦住题号不全的文件
        errors.append("题号列不完整，无法读取答案")

    student: Student | None = None
    if not errors:
        student, locate_errors, locate_warnings = locate_student(
            db,
            school=school,
            grade_name=grade_name,
            class_name=class_name,
            name=name,
            gender=gender,
            age=age,
            actor=actor,
        )
        errors.extend(locate_errors)
        warnings.extend(locate_warnings)

    conflicts: list[dict[str, Any]] = []
    if student is not None:
        if gender and student.gender and student.gender != gender:
            warnings.append(f"性别与名册不符（名册：{'男' if student.gender == 'MALE' else '女'}）")
        if student.id in seen_students:
            errors.append(f"与第 {seen_students[student.id]} 行是同一名学生")
        else:
            conflicts = _conflicts_for_student(db, student, file_age=age, tested_on=tested_on)

    payload = {
        "student_id": student.id if student else None,
        "answers": answers,
        "duration_seconds": duration,
        # 文件里那一格年龄。冲突判定要用它，而**只有**在操作员选「覆盖」时才会写回名册
        # （见 `_update_roster_age`）——令牌里带着它，提交时就不必再信一次请求体。
        "age": age,
    }
    display = {
        "row_no": row["row_no"],
        "name": name,
        "grade": grade_name or row["grade"],
        "class_name": class_name or row["class_name"],
        "gender": gender,
        "age": age,
        "duration_seconds": duration,
        "matched_student_no": student.student_no if student else None,
        "matched_name": student.masked_name if student else None,
        "errors": errors,
        "warnings": warnings,
        # 一行可能同时是「本月重复」与「年龄不符」（改完文件重导时最常见），
        # 所以是一张清单，一次处置（覆盖/放弃）同时回答它们。
        "conflicts": conflicts,
    }
    return {
        "errors": errors,
        "warnings": warnings,
        "conflicts": conflicts,
        "display": display,
        "payload": payload,
    }


def _conflicts_for_student(
    db: Session, student: Student, *, file_age: int | None, tested_on: date
) -> list[dict[str, Any]]:
    """这行记录与库里已有的事实冲不冲突（空清单 = 不需要任何决定）。

    两类都**不是**错误：记录本身是好的，难的是「以文件为准还是以库里为准」。
    顺序固定为「先重复、后年龄」：重复是「这条会不会写进去」的问题，
    年龄是「写进去之后要不要顺带动名册」的问题。
    """
    conflicts: list[dict[str, Any]] = []
    existing = existing_import_session(db, student.id, tested_on)
    if existing is not None:
        conflicts.append(
            {
                "type": CONFLICT_DUPLICATE,
                "message": MONTH_CONFLICT_MESSAGE.format(month=f"{tested_on:%Y-%m}"),
                "existing_session_id": existing.id,
                "existing_tested_on": (
                    existing.submitted_at.date().isoformat() if existing.submitted_at else None
                ),
            }
        )
    if file_age is not None and student.age is not None and student.age != file_age:
        conflicts.append(
            {
                "type": CONFLICT_AGE_MISMATCH,
                "message": f"年龄 {file_age} 与名册 {student.age} 不符",
                "roster_age": student.age,
                "file_age": file_age,
            }
        )
    return conflicts


def _parse_grade(value: str, errors: list[str]) -> str | None:
    if not value:
        errors.append("缺少年级")
        return None
    stripped = value.strip()
    grade = GRADE_BY_NUMBER.get(stripped) or GRADE_NAMES.get(stripped)
    if not grade:
        errors.append(GRADE_INPUT_HINT)
    return grade


def _parse_class(value: str, grade_name: str | None, errors: list[str]) -> str | None:
    """`4` → `704`（年级前缀 + 班号补两位）。

    已写成三位编号的（`704`）直接用它，但**首位必须与年级一致**——与「学生信息导入」
    的 `check_grade_and_class` 是同一条规则、同一句话。两列各自看都合法而合起来矛盾，
    是这一层唯一「填错了却看着像对的」情况。
    """
    if not value:
        errors.append("缺少班级")
        return None
    stripped = value.strip()
    if not stripped.isascii() or not stripped.isdigit():
        errors.append(CLASS_INPUT_HINT)
        return None
    if len(stripped) <= 2:
        if grade_name is None:
            return None
        number = int(stripped)
        if number < 1:
            errors.append(CLASS_INPUT_HINT)
            return None
        return f"{GRADE_PREFIX[grade_name]}{number:02d}"
    if len(stripped) == 3:
        owner = CLASS_PREFIX_GRADE.get(stripped[0])
        if owner is None:
            errors.append(CLASS_INPUT_HINT)
            return None
        if grade_name is not None and owner != grade_name:
            errors.append(f"班级 {stripped} 属于{owner}，与年级 {grade_name} 不一致")
            return None
        return stripped
    errors.append(CLASS_INPUT_HINT)
    return None


def _parse_age(value: str, warnings: list[str]) -> int | None:
    """年龄只用于消歧，所以读不出来是**提示**而不是错误：记录照常导入，只是弱了一点。"""
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 99:
        warnings.append(f"年龄「{value}」无法识别，已忽略")
        return None
    return int(value)


def _parse_duration(value: str) -> tuple[int | None, str | None]:
    if not value:
        return None, None
    match = _DURATION.match(value)
    if not match:
        return None, f"作答用时「{value}」无法识别，已留空"
    seconds = int(match.group(1))
    if seconds > MAX_DURATION_SECONDS:
        return None, f"作答用时 {seconds} 秒超出可记录范围，已留空"
    return seconds, None


def _parse_answers(values: list[str]) -> tuple[list[str], list[str]]:
    answers: list[str] = []
    blank: list[int] = []
    invalid: list[tuple[int, str]] = []
    for offset, value in enumerate(values):
        question_no = offset + 1
        if value == "":
            blank.append(question_no)
            answers.append("NO")
        elif value in {"1", "是", "YES"}:
            answers.append("YES")
        elif value in {"0", "否", "NO"}:
            answers.append("NO")
        else:
            invalid.append((question_no, value))
            answers.append("NO")

    errors = []
    if blank:
        shown = "、".join(str(number) for number in blank[:5])
        suffix = f"（共 {len(blank)} 题）" if len(blank) > 5 else ""
        errors.append(f"第 {shown} 题未作答{suffix}")
    if invalid:
        shown = "、".join(f"第 {number} 题「{value}」" for number, value in invalid[:5])
        suffix = f"（共 {len(invalid)} 题）" if len(invalid) > 5 else ""
        errors.append(f"{shown} 的答案{ANSWER_HINT}{suffix}")
    return answers, errors


def locate_student(
    db: Session,
    *,
    school: School | None,
    grade_name: str | None,
    class_name: str | None,
    name: str,
    gender: str | None,
    age: int | None,
    actor: UserAccount,
) -> tuple[Student | None, list[str], list[str]]:
    """按 姓名 + 性别 + 年龄 + 年级 + 班级 定位到名册里的一名学生。

    两个刻意的决定：

    * **「班级不存在」与「没有这个学生」分开报。** 合成一句「名册中没有「xxx1」（初一 704）」
      在库里的班级叫 `1班`（`seed_demo` 的演示数据就是这样）时会给出**假**诊断：班级根本
      没有，而话里说的是人没有。分开之后，任何一句都只有一种解释。
    * **超出数据范围与「没有这个学生」返回逐字相同的一句话。** 两者文案不同，等于让只
      看得到自己班的心理老师用上传文件来枚举全校名册：发一个名字，看回的是「不在你的
      范围内」还是「没这个人」。照 `care_service` 对越权档案的既有做法（不告诉越权者
      档案是否存在），范围外的人在这里就是「不存在」。
    """
    if school is None or grade_name is None or class_name is None:
        return None, [STUDENT_NOT_FOUND.format(class_name=class_name or "", name=name)], []

    grade = db.scalar(select(Grade).where(Grade.school_id == school.id, Grade.name == grade_name))
    class_group = (
        db.scalar(
            select(ClassGroup).where(
                ClassGroup.school_id == school.id,
                ClassGroup.grade_id == grade.id,
                ClassGroup.name == class_name,
            )
        )
        if grade
        else None
    )
    if class_group is None:
        return None, [CLASS_NOT_FOUND.format(class_name=f"{grade_name} {class_name}")], []

    candidates = db.scalars(
        select(Student).where(Student.class_id == class_group.id, Student.name == name).order_by(Student.id)
    ).all()
    # 范围过滤放在**数人数之前**：过滤是「这个人在这里等于不存在」，不是「找到了但不让导入」。
    # 顺手让多候选消歧也只在看得见的人里进行。
    visible = [student for student in candidates if student_in_scope(db, actor, student)]
    not_found = STUDENT_NOT_FOUND.format(class_name=f"{grade_name} {class_name}", name=name)
    if not visible:
        return None, [not_found], []

    warnings: list[str] = []
    matched = _disambiguate(visible, gender=gender, age=age)
    if matched is None:
        return (
            None,
            [f"{grade_name} {class_name} 有 {len(visible)} 名「{name}」，无法按性别与年龄定位"],
            [],
        )
    if len(visible) > 1:
        warnings.append(
            f"同班有 {len(visible)} 名同名同学，已按性别与年龄定位到 {matched.student_no}"
        )
    # 年龄与名册不符**不在这里报**：它不是「定位时的一句话」，而是一个要操作员拍板的
    # 冲突（`_conflicts_for_student`）。名册上存的是**当前**年龄（迁移 0011），
    # 文件里是考试当天的岁数，导入一份去年的文件时两者差一岁是常事——所以要问，
    # 而不是替他决定「以文件为准」或「以名册为准」。
    return matched, [], warnings


def _disambiguate(
    candidates: list[Student], *, gender: str | None, age: int | None
) -> Student | None:
    """性别优先、年龄其次；年龄精确优先，其次相差一岁。

    性别不符时**不**把候选清空：性别与名册不符按用户的决策只是告警，那么一个班里的
    两个同名同学就不该因为文件性别写反而变成「定位不到」——那会把一条告警升级成一条错误。

    差一岁要当**候选**而不是排除项：名册上的年龄是学校最近一次导入时填的（0011 之后它
    不再自己变），而这份文件可能是去年那次普查，同一个人的两个数字正好差一岁。
    """
    if len(candidates) == 1:
        return candidates[0]
    narrowed = candidates
    if gender:
        by_gender = [student for student in narrowed if student.gender == gender]
        if by_gender:
            narrowed = by_gender
    if len(narrowed) > 1 and age is not None:
        by_age = [student for student in narrowed if student.age == age]
        if by_age:
            narrowed = by_age
        else:
            near = [
                student
                for student in narrowed
                if student.age is not None and abs(student.age - age) == 1
            ]
            if near:
                narrowed = near
    return narrowed[0] if len(narrowed) == 1 else None


def existing_import_session(
    db: Session, student_id: int, tested_on: date
) -> AssessmentSession | None:
    """这个学生在**同一个自然月**里已经导入过的那一场测评（没有就是 `None`）。

    「按月度为单位判重」是 2026-09-17 用户的要求：「不同月份的评测视为不同的测试任务」。
    所以在判重这件事上，`9月16日` 与 `9月17日` 是同一场，`9月30日` 与 `10月1日`
    是两场。此前按**天**判重，而查重的结果又不是「跳过」而是硬错误（`errors` 里一行字），
    于是同一场普查学校分两批导（先是初一、后是初二改完的文件）时，第二天那条记录
    直接被挡在门外，唯一的出路是改系统日期。

    两个条件缺一不可：会话自身是导入的（`source`），它所在的任务也是导入批次任务。
    只按 `submitted_at` 落在本月来找会在系统内那场恰好也在本月的学生身上误报——
    而「系统内答过」根本不是重复，他答的是另一场测评。
    """
    start, end = month_bounds(tested_on)
    return db.scalar(
        select(AssessmentSession)
        .join(AssessmentTask, AssessmentSession.task_id == AssessmentTask.id)
        .where(
            AssessmentSession.student_id == student_id,
            AssessmentSession.source == "IMPORTED",
            AssessmentTask.source == "IMPORTED",
            AssessmentSession.submitted_at >= start,
            AssessmentSession.submitted_at < end,
        )
        .order_by(AssessmentSession.id.desc())
    )


# --------------------------------------------------------------------------
# 预览令牌
# --------------------------------------------------------------------------


def create_preview_token(rows: list[dict[str, Any]], *, batch_name: str, tested_on: date) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "kind": "assessment_import_preview",
            "batch": {"name": batch_name, "tested_on": tested_on.isoformat()},
            "rows": rows,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_preview_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("VALIDATION_ERROR", "导入预览已失效，请重新预览", 422) from exc
    if payload.get("kind") != "assessment_import_preview":
        raise AppError("VALIDATION_ERROR", "导入预览无效", 422)
    return {"rows": payload.get("rows", []), "batch": payload.get("batch", {})}


# --------------------------------------------------------------------------
# 落库
# --------------------------------------------------------------------------


def commit_assessment_import(
    db: Session,
    payload: dict[str, Any],
    actor: UserAccount,
    resolution: str | None = None,
) -> dict[str, Any]:
    """把预览过的行写成真实的测评记录，单事务。

    评测与落库共用 `calculate_session` / `persist_result_and_dimensions`（见
    `assessment_service`）——导入的结果必须是**同一种形状**的事实，否则分析页与个案
    详情页会因为数据来源不同而各说各话。

    `resolution` 只在文件里真的有冲突时才是必需的：`overwrite`（覆盖上次 / 用文件里的
    年龄更新名册）或 `skip`（这几条不导入，其余照导）。没有冲突的文件照旧一次提交，
    不必带它——要求 99% 的正常导入都先回答一个不该问的问题，只会让人乱点。
    """
    rows = payload.get("rows") or []
    batch = payload.get("batch") or {}
    if not rows:
        raise AppError("VALIDATION_ERROR", "没有可导入的记录", 422)
    if resolution is not None and resolution not in RESOLUTIONS:
        raise AppError("VALIDATION_ERROR", RESOLUTION_HINT, 422)
    tested_on = date.fromisoformat(batch["tested_on"])
    batch_name = batch["name"]

    # 令牌签过名，但没有 exp、也不绑定签发者（与另两个导入一致）。所以提交时**必须**
    # 按提交者的范围再查一遍：一个由全校范围的人签出来的令牌，被只管理一个班的人重放，
    # 就会把那个班以外的学生写进库。这一条不做，预览里的范围过滤只是装饰。
    for row in rows:
        ensure_student_in_scope(db, actor, row["student_id"])

    school = school_for_import(db)
    scale = _published_scale(db)
    if school is None or scale is None:
        raise AppError("VALIDATION_ERROR", "缺少学校或已发布的 MHT 量表，无法导入", 422)
    questions = {
        question.question_no: question
        for question in db.scalars(select(ScaleQuestion).where(ScaleQuestion.scale_id == scale.id)).all()
    }
    # `calculate_session` 也是按 question_no 取全套题目来配 `ScaleQuestionConfig` 的，
    # 题号不全就会在评分时静默少算几题——所以在写任何一行之前先挡下来
    if any(number not in questions for number in range(1, QUESTION_COUNT + 1)):
        raise AppError("VALIDATION_ERROR", "量表题目不完整，无法导入", 422)

    # 冲突**先全部算完再动手**：写了一半才发现「还差一个决定」，用户看到的是导入失败，
    # 库里却已经躺着一批记录。所以这一步在任何写入之前。
    planned = [
        (
            row,
            _conflicts_for_student(
                db, student, file_age=row.get("age"), tested_on=tested_on
            ),
        )
        for row, student in ((row, db.get(Student, row["student_id"])) for row in rows)
    ]
    if resolution is None:
        pending = [row for row, conflicts in planned if conflicts]
        if pending:
            raise AppError(
                "VALIDATION_ERROR",
                f"有 {len(pending)} 条记录需要确认（年龄与名册不符，或本月已有一次导入），"
                "请选择覆盖或放弃这些记录后重试",
                422,
            )

    tested_at = datetime(tested_on.year, tested_on.month, tested_on.day)
    # 「一条都不会写进去」时不建批次任务。全是冲突行、又选了「放弃」时（重新导了同一份
    # 文件、想想还是算了），建出来的会是一个目标行数为 0 的空任务：§12 的判据里
    # 「目标行全部完成且总数 > 0」不成立，于是它在任务列表上**永远显示「进行中」**——
    # 正是用户抱怨过的那一行。而且它会被同月的下一次导入复用，那个月的批次名字
    # 就定在这次什么都没导的尝试上（`_task_for_month` 复用时不改名）。
    writes = any(
        not (conflicts and resolution == RESOLUTION_SKIP) for _, conflicts in planned
    )
    task = (
        _task_for_month(
            db, school=school, scale=scale, batch_name=batch_name, tested_on=tested_on, actor=actor
        )
        if writes
        else None
    )

    created = updated = skipped = age_updated = withdrawn = 0
    for row, conflicts in planned:
        kinds = {conflict["type"] for conflict in conflicts}
        if conflicts and resolution == RESOLUTION_SKIP:
            skipped += 1
            continue
        assert task is not None  # 上面刚判过「这一行会写」，任务必已建出

        student = db.get(Student, row["student_id"])
        if CONFLICT_AGE_MISMATCH in kinds:
            # 只有选了「覆盖」才会走到这里，而它的含义只有一个：改名册（`student.age`
            # 是年龄唯一的存储处）。预览里已经把「名册 12、文件 13」写给人看过了。
            _update_roster_age(db, student, row["age"], actor=actor)
            age_updated += 1

        # 目标行先写：`_stamp_submission` 是按 (task_id, student_id) 找它的，
        # 而导入根本不走它（它会把 completed_at 写成「现在」，与 submitted_at 的测评日期打架），
        # 所以这里自己把两件事都写对
        _mark_target_completed(db, task, student.id, tested_at)

        # 有就覆盖、没有才新建。查重不依赖上面算出来的冲突：预览与提交之间可能又有人
        # 导了同一份文件，那时冲突清单是旧的，而唯一约束 (task, student) 不会通融。
        session = existing_import_session(db, student.id, tested_on)
        if session is None:
            session = _new_imported_session(db, row, task=task, scale=scale, tested_at=tested_at)
            created += 1
        else:
            withdrawn += _rewrite_imported_session(
                db, session, row, scale=scale, tested_at=tested_at, questions=questions
            )
            updated += 1
        _write_sheet(db, session, row, questions=questions, tested_at=tested_at)

    db.flush()
    return {
        # 一条都没写时是 None（见上面 `writes`）：调用方据此不指向任何任务
        "task_id": task.id if task else None,
        "task_no": task.task_no if task else "",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "age_updated": age_updated,
        # 覆盖时从这一场收回的、还没人处理过的风险待办（审计要能回答「那条待办去哪了」）
        "withdrawn_risk_events": withdrawn,
    }


def _task_for_month(
    db: Session,
    *,
    school: School,
    scale: AssessmentScale,
    batch_name: str,
    tested_on: date,
    actor: UserAccount,
) -> AssessmentTask:
    """本月那一场外部导入的任务，没有就建一个，有就复用它。

    「不同月份的评测视为不同的测试任务」（2026-09-17 用户要求）的落点就在这里：
    **同一个月里分几次导（初一一份、初二一份，或者改完文件重导）都归到同一行下面**，
    换个月才是另一场。此前每提交一次就新建一个任务，于是同一个上午导两遍会得到
    `IMPORT-20260917-1` 与 `IMPORT-20260917-2` 两行，完成率各算一半，
    同一次普查在列表上看起来像两场。

    复用时**不改名字**：任务名是学校在第一次导入时确认过的那个标签，第二份文件的批次名
    只是那一次的备注。要改名走任务的编辑接口（心理老师），不在这里悄悄覆盖。

    查找按 `start_at`（= 测评日期）落在本月来做，而不是按 `task_no` 的形状——
    `IMPORT-20260916-1` 这种按天编号的旧任务同样属于九月，它也该被复用。

    **同月有多行时取最新的那一行（`id.desc()`），必须与 `existing_import_session`
    指向同一个月份批次。** 2026-09-17 之前这里是 `order_by(id)`（取**最老**的），
    而判重那边取的是**最新**的会话（`:728` `AssessmentSession.id.desc()`）——
    按天编号还留着旧任务时两处就分岔了：用户库里九月有 `IMPORT-20260916-1` 与
    `IMPORT-20260917-1` 两行，于是那次导入的提示与审计都写 09-16 那一批，
    **而真正被就地改写的会话属于 09-17 那一批**。批次号、审计的 `resource_id`、
    被改写的会话三者说的必须是同一件事，不然轨迹答不上「这次导入动了什么」。
    取最新之后，列表上的名字也是用户最近一次导入确认过的那个。
    """
    start, end = month_bounds(tested_on)
    existing = db.scalar(
        select(AssessmentTask)
        .where(
            AssessmentTask.school_id == school.id,
            AssessmentTask.source == "IMPORTED",
            AssessmentTask.start_at >= start,
            AssessmentTask.start_at < end,
        )
        .order_by(AssessmentTask.id.desc())
    )
    if existing:
        return existing

    task = AssessmentTask(
        task_no=_next_task_no(db, tested_on),
        name=batch_name,
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        # 批次是一次已经发生过的测评：开始时间就是测评日期，**不给截止时间**——
        # 它不是一份等学生来答的任务，写一个已过去的截止日期只会让列表上多出一个
        # 永远不会被完成的「逾期」窗口。
        #
        # `status="ACTIVE"` 是「没有人工裁决」的意思，不是「进行中」：列表上的状态由
        # `task_service.effective_task_status` 现算，这批记录因为目标行全部完成而显示
        # 「已结束」（2026-09-17 起。在此之前它永远显示「进行中」——3/3、100%、
        # 进行中，用户报的就是这个）。这一列现在只承载人写进去的 DRAFT/PAUSED/CLOSED。
        start_at=datetime(tested_on.year, tested_on.month, tested_on.day),
        end_at=None,
        status="ACTIVE",
        created_by=actor.id,
        source="IMPORTED",
    )
    db.add(task)
    db.flush()
    return task


def _mark_target_completed(db: Session, task: AssessmentTask, student_id: int, tested_at: datetime) -> None:
    target = db.scalar(
        select(AssessmentTarget).where(
            AssessmentTarget.task_id == task.id, AssessmentTarget.student_id == student_id
        )
    )
    if target is None:
        db.add(
            AssessmentTarget(
                task_id=task.id, student_id=student_id, status="COMPLETED", completed_at=tested_at
            )
        )
        return
    # 覆盖时这一行本来就在（第一次导入建的），只需把完成时刻跟到新文件那一天
    target.status = "COMPLETED"
    target.completed_at = tested_at


def _new_imported_session(
    db: Session, row: dict[str, Any], *, task: AssessmentTask, scale: AssessmentScale, tested_at: datetime
) -> AssessmentSession:
    session = AssessmentSession(
        task_id=task.id,
        student_id=row["student_id"],
        scale_id=scale.id,
        scale_version=scale.version,
        started_at=tested_at,
        submitted_at=tested_at,
        duration_seconds=row.get("duration_seconds"),
        status="IN_PROGRESS",
        source="IMPORTED",
    )
    db.add(session)
    db.flush()
    return session


def _write_sheet(
    db: Session, session: AssessmentSession, row: dict[str, Any], *, questions, tested_at: datetime
) -> None:
    """把这份文件写进这一场会话：答卷 → 评分 → 维度 → 判定 → 定状态。

    新建与覆盖走的是**同一段**代码，区别只在于调用它之前把旧的那些行清掉了
    （`_rewrite_imported_session`）。一份记录「是怎么算出来的」不该因为它是第一次
    导入还是第八次重导而不同。
    """
    answers = row["answers"]
    db.add_all(
        [
            AssessmentAnswer(
                session_id=session.id,
                question_id=questions[offset + 1].id,
                answer=answer,
                score=1 if answer == "YES" else 0,
                # 逐题作答时刻只有**天**的精度：外部文件给的就是「哪一天」和
                # 「总共用了多久」。把第 1 题倒推成「提交前 5340 秒」能让
                # `session_duration_seconds()` 回算出好看的用时，代价是把一个
                # 具体的假时刻写进原始事实层（CLAUDE.md §1）——而用时本身是文件里
                # 的记录值，已经原样存进 `duration_seconds`，不需要回算。
                answered_at=tested_at,
            )
            for offset, answer in enumerate(answers)
        ]
    )
    db.flush()
    calculation = calculate_session(
        db, session, {offset + 1: answer for offset, answer in enumerate(answers)}
    )
    persist_result_and_dimensions(db, session, calculation)
    # 与 `submit_session` 的调用顺序逐字相同（评分 → 落结果 → 触发 → 定状态）。
    # 判定规则共用这一个函数，所以「什么情况下算重点学生」在两条路径上不可能各说各话。
    #
    # 时间口径要说清：风险事件的 `created_at` 与档案的 `opened_at` 取的是**导入时刻**
    # （`now_utc_naive()`），不是 `tested_at`。测评发生在去年，但档案是学校今天才看到、
    # 今天才开的——这两个时间回答的是不同的问题，把它们统一成测评日期等于倒填台账。
    maybe_raise_risk_events(db, session, calculation)
    session.status = session_status_for(calculation)


def _rewrite_imported_session(
    db: Session,
    session: AssessmentSession,
    row: dict[str, Any],
    *,
    scale: AssessmentScale,
    tested_at: datetime,
    questions,
) -> int:
    """覆盖上次：把这一场已有的导入记录按新文件重写一遍，返回收回的待办条数。

    **就地改，不删了重建。** `risk_event.session_id`、`retest_plan.source_session_id`
    都外键指向这一行，而全库没有任何 `ondelete=`，父行先删在 MySQL 上是 1451；
    更要紧的是那两处背后是人的工作——一条待办和一份复测计划，不该因为重导一份文件
    而消失。会话 id 不变，分析、个案详情、导出自动看到新的那一份。

    会话自身的时间戳一并跟到新文件：同一个月里两次导入的测评日期可以不同
    （9 月 16 日那批与 9 月 17 日那一批），而「记录取自哪一场」是按 `submitted_at`
    排序的（CLAUDE.md §11），不跟着改会让新记录排到旧记录的后面去。
    """
    db.execute(delete(AssessmentAnswer).where(AssessmentAnswer.session_id == session.id))
    db.execute(delete(DimensionResult).where(DimensionResult.session_id == session.id))
    # 结果行是 `unique(session_id)`，八维度是 `unique(session_id, dimension_code)`：
    # 不清掉的话重算会直接撞唯一约束
    db.execute(delete(AssessmentResult).where(AssessmentResult.session_id == session.id))
    withdrawn = _withdraw_pending_risk_events(db, session)

    session.scale_version = scale.version
    session.started_at = tested_at
    session.submitted_at = tested_at
    session.duration_seconds = row.get("duration_seconds")
    return withdrawn


def _withdraw_pending_risk_events(db: Session, session: AssessmentSession) -> int:
    """收回这一场**还没人处理过**的风险待办。

    只有 `PENDING` 且没有任何人工复核引用它的事件才是「系统开出、还没人看过」。
    已复核的那条留着——那是心理老师的工作记录，重导一份文件不该把它抹掉，
    而且 `manual_review.risk_event_id` 是外键，本来也删不掉。
    """
    withdrawn = 0
    events = db.scalars(
        select(RiskEvent).where(RiskEvent.session_id == session.id, RiskEvent.status == "PENDING")
    ).all()
    for event in events:
        reviewed = db.scalar(
            select(func.count(ManualReview.id)).where(ManualReview.risk_event_id == event.id)
        )
        if not reviewed:
            db.delete(event)
            withdrawn += 1
    db.flush()
    return withdrawn


def _update_roster_age(db: Session, student: Student, file_age: int, *, actor: UserAccount) -> None:
    """把文件里的年龄写回名册（**只**在操作员选了「覆盖」时才会被调用）。

    `student.age` 是年龄唯一的存储处（迁移 0011 把出生日期换成了它），所以「覆盖更新」
    在这条链路上只有一种含义：动名册。这一列不会自己变，学校不重导名册它就停在去年，
    所以每次改动都留一条**指着这名学生**的审计，记下从哪个数改到哪个数。

    审计不带 `request`（ip / user-agent 为空）：与 `auth.py` 的「修改本人密码」同一处理，
    服务的入参里没有请求对象。批量导入的那条审计行在路由里，带着 ip。
    """
    previous = student.age
    if previous == file_age:
        return
    student.age = file_age
    write_audit(
        db,
        action="更新学生年龄",
        resource_type="STUDENT",
        resource_id=str(student.id),
        actor=actor,
        student_id=student.id,
        detail=f"名册 {previous} → 文件 {file_age}（测评记录导入）",
    )


def _next_task_no(db: Session, tested_on: date) -> str:
    """`IMPORT-202609-1`：批次的编号按**月**，因为一场任务就是一个月。

    `-1` 的序号留给「同一个月的第二个任务」这种将来可能有的情况（现在同月一律复用）。
    按天编号的旧任务（`IMPORT-20260916-1`）与这个形状不冲突，会被 `_task_for_month`
    直接复用掉，不会在这里生成第二个。
    """
    prefix = f"IMPORT-{tested_on:%Y%m}"
    taken = set(
        db.scalars(select(AssessmentTask.task_no).where(AssessmentTask.task_no.like(f"{prefix}-%"))).all()
    )
    number = 1
    while f"{prefix}-{number}" in taken:
        number += 1
    return f"{prefix}-{number}"


def school_for_import(db: Session) -> School | None:
    """导入写进哪所学校。

    与 `student_import_service._target_school` 同一个约定：单校写死 `QH`
    （CLAUDE.md 已知缺口 2）。多校部署时这两处要一起改——预览与提交也必须用同一个，
    否则会在预览里定位到一所学校的学生、提交时落到另一所。
    """
    return db.scalar(select(School).where(School.code == "QH"))


def _published_scale(db: Session) -> AssessmentScale | None:
    return db.scalar(
        select(AssessmentScale)
        .where(AssessmentScale.code == "MHT", AssessmentScale.status == "PUBLISHED")
        .order_by(AssessmentScale.id.desc())
    )


def template_csv() -> str:
    header = ["姓名", "性别", "年龄", "年级", "班级", "所用时间"]
    header += [f"{number}.题干" for number in range(1, QUESTION_COUNT + 1)]
    sample_row = ["示例学生", "1", "12", "1", "4", "3600秒"] + ["0"] * QUESTION_COUNT

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(sample_row)
    return "﻿" + output.getvalue()
