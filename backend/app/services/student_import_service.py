import csv
import io
import json
from typing import Any

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.account import UserAccount, UserScope
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.security.passwords import hash_password
# 处置方式的两个码与测评导入**共用一套**：同一份 API 词汇出现在两个端点里，
# 各写一份字面量就会有第三种写法悄悄冒出来，而它只会在前端提交时变成一句 422。
# 中文标签各自定义——含义确实不同（测评那边「覆盖」是替换上次导入的那一场，
# 这边是更新名册上的这一名学生）。
from app.services.assessment_import_service import (
    RESOLUTION_HINT,
    RESOLUTION_OVERWRITE,
    RESOLUTION_SKIP,
    RESOLUTIONS,
)


REQUIRED_FIELDS = ("student_no", "name", "grade", "class_name")

# 「学号已在名册上」是**冲突**，不是错误（2026-09-17 用户要求）。两件事的区别：
# 错误是「这一行填坏了」（缺列、班级与年级对不上），学校只能改文件重导；
# 冲突是「这一行本身没问题，但它与库里已有的数据撞上了」，出路有一条以上——
# 覆盖（用文件里的信息更新这名学生）或放弃（这次不动他）。所以它不该让整行作废，
# 而该列出来让操作员拍板。
#
# 「文件内重复学号」**仍然是错误**，刻意不升级成冲突：同一份文件里两行都说是
# 同一个学号时，没有「覆盖」可言——先写的那行等着被后一行覆盖，还是反过来？
# 这问的不是口径，是文件坏了。
CONFLICT_STUDENT_NO = "STUDENT_NO_EXISTS"

# 名册字段里，「覆盖」能改哪些。学号是主键，不在其中——它正是冲突的判据。
OVERWRITABLE_FIELDS = ("name", "grade", "class_name", "gender", "age")


def student_resolution_label(resolution: str | None) -> str:
    """处置方式的中文，写进审计的 `detail`。

    与 `assessment_import_service.resolution_label` 同一道理：审计详情是**给人读的**
    轨迹，写 `overwrite` 等于没写。`None` 是「文件里没有冲突，所以没问过」，与
    「问了、选了放弃」必须长得不一样——否则事后读轨迹的人会以为那次导入把
    冲突当成默认放行了。
    """
    return {
        RESOLUTION_OVERWRITE: "覆盖同名学号的学生",
        RESOLUTION_SKIP: "放弃冲突行",
    }.get(resolution or "", "未涉及（无冲突）")

# 性别 / 年龄 are optional — the roster predates both columns and the three
# existing import tests upload CSVs without them. Accepted only when recognisable:
# a typo silently stored as NULL would be indistinguishable from "the school did
# not fill it in", which is the state we are trying to move away from.
GENDER_INPUT_ALIASES = {"MALE": "MALE", "男": "MALE", "FEMALE": "FEMALE", "女": "FEMALE"}
GENDER_INPUT_HINT = "性别可填 MALE/FEMALE 或 男/女"
AGE_INPUT_HINT = "年龄应为 13 这样的整数"
# 中学生名册上不会出现的岁数，两头都拦：3 岁以下多半是把年级填进了年龄格，100 岁以上同理。
# 顺带把「2013」这类把出生年份填进来的写法挡在门外——它是个整数，但不在这个区间。
MIN_AGE, MAX_AGE = 3, 100

# 班级编号的首位数字表示年级：701 = 初一 1 班，801 = 初二 1 班，901 = 初三 1 班。
# 学校按这个规则编号，所以「班级」里其实已经编进了年级——但年级列仍然必填，两列对不上
# 就按行报错。这里的冗余是特性，不是遗留：静默地按班级把学生挂到一个错年级下
# （`commit_student_import` 只会拿字符串去 `Grade.name` 里找、找不到就新建一个），
# 比让录错的人当场看见一行错误糟得多。
GRADE_CLASS_PREFIX = {"初一": "7", "初二": "8", "初三": "9"}
CLASS_PREFIX_GRADE = {prefix: grade for grade, prefix in GRADE_CLASS_PREFIX.items()}
GRADE_INPUT_HINT = "年级应为 初一 / 初二 / 初三"
CLASS_NAME_INPUT_HINT = "班级格式应为 701（首位 7/8/9 分别表示初一/初二/初三）"


def check_grade_and_class(grade: str, class_name: str) -> str | None:
    """年级与班级编号一致性检查；返回该行的错误文案，或 None 表示通过。

    两个字段都非空时才调用——缺字段由 `REQUIRED_FIELDS` 报「缺少年级」，
    这里再补一句「年级应为…」只是噪声。
    """
    prefix = GRADE_CLASS_PREFIX.get(grade)
    if prefix is None:
        return GRADE_INPUT_HINT
    # `isascii` 不能省：`'７０１'.isdigit()` 是 True（全角数字也算数字），
    # 然后拿 '７' 去查前缀表会取到 None。全角编号应当报格式错误，不是 500。
    if len(class_name) != 3 or not class_name.isascii() or not class_name.isdigit():
        return CLASS_NAME_INPUT_HINT
    if class_name[0] != prefix:
        owner = CLASS_PREFIX_GRADE.get(class_name[0])
        if owner is None:
            return CLASS_NAME_INPUT_HINT
        return f"班级 {class_name} 属于{owner}，与年级 {grade} 不一致"
    return None


def parse_student_import(filename: str, content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    if filename.lower().endswith(".json"):
        rows = json.loads(text)
        if not isinstance(rows, list):
            raise AppError("VALIDATION_ERROR", "JSON必须是数组", 422)
        return [normalize_row(row) for row in rows]
    reader = csv.DictReader(io.StringIO(text))
    return [normalize_row(row) for row in reader]


def normalize_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "student_no": str(row.get("student_no") or row.get("学号") or "").strip(),
        "name": str(row.get("name") or row.get("姓名") or "").strip(),
        "grade": str(row.get("grade") or row.get("年级") or "").strip(),
        "class_name": str(row.get("class_name") or row.get("班级") or "").strip(),
        "gender": str(row.get("gender") or row.get("性别") or "").strip(),
        "age": str(row.get("age") or row.get("年龄") or "").strip(),
        # 已废弃的列，值本身不再使用：留着只为在预览里认出「列名还是出生日期」的老文件。
        "legacy_birth_date": str(
            row.get("birth_date") or row.get("出生日期") or ""
        ).strip(),
    }


def parse_gender(value: str) -> str | None:
    """Normalise a gender cell to its stored code, or None when left blank.

    Chinese spellings are accepted because every other cell a clerk types is
    Chinese (初一 / 701) — demanding `MALE` from someone who typed 男 would be a
    needless failure. Storage stays in codes, one representation: rendering 男/女
    is `frontend/src/services/labels.ts`'s job.
    """
    if not value:
        return None
    try:
        return GENDER_INPUT_ALIASES[value.upper()]
    except KeyError as exc:
        raise ValueError(GENDER_INPUT_HINT) from exc


def parse_age(value: str) -> int | None:
    """Parse an age cell, or None when left blank.

    An integer, not a birth date: the roster carries 「13 岁」 and nothing else
    (migration 0011 replaced `student.birth_date` with `student.age`). Trailing
    岁 is tolerated — the sibling parser for 作答用时 takes `5340秒`, and a clerk
    who writes 13岁 means the same thing as 13.

    A date typed into this cell (`2013-09-01`) is therefore an error, which is
    the point: the column changed name and meaning, and an old template must be
    told so rather than quietly storing NULL ages for the whole roster (see
    `preview_student_import`).
    """
    if not value:
        return None
    digits = value.strip().removesuffix("岁").strip()
    # `isascii` 不能省：`'１３'.isdigit()` 是 True（全角数字也算数字），而 int('１３')
    # 虽然能过，存进去的全角输入没有任何意义——和 `check_grade_and_class` 同一道理。
    if not digits.isascii() or not digits.isdigit():
        raise ValueError(AGE_INPUT_HINT)
    age = int(digits)
    if not MIN_AGE <= age <= MAX_AGE:
        # 越界最常见的一种是把出生年份填进来了（「2013」）。它确实是个整数，所以要说清
        # 问题出在范围，而不是让填表的人自己去猜「为什么整数也不行」。
        raise ValueError(f"年龄 {age} 超出 {MIN_AGE}-{MAX_AGE} 的范围")
    return age


def _target_school(db: Session) -> School | None:
    """The school student imports are written into.

    Preview and commit must agree on this. Matching `student_no` across every
    school makes preview reject a perfectly valid row as 重复学号 because a
    *different* school already holds that number — and the per-row response then
    discloses which student numbers other schools have.
    """
    return db.scalar(select(School).where(School.code == "QH"))


def conflicts_for_row(db: Session, school: School, row: dict[str, str]) -> list[dict[str, Any]]:
    """这一行与库里已有的数据撞在哪。

    单独一个函数，因为预览与提交**都要算一遍**，而且取的是同一份判据：预览与提交
    之间可能又有人导了同一份文件，那时预览里的冲突清单已经旧了（这正是
    `assessment_import_service` 的做法，见那边的 `planned`）。
    """
    if not school or not row.get("student_no"):
        return []
    existing = db.scalar(
        select(Student).where(
            Student.school_id == school.id, Student.student_no == row["student_no"]
        )
    )
    if not existing:
        return []
    return [
        {
            "type": CONFLICT_STUDENT_NO,
            "message": f"学号 {row['student_no']} 已在名册上（{existing.name}）",
            # 预览面板要能写出「覆盖会把他从 X 改成 Y」，所以把库里那一份也带上。
            "existing_name": existing.name,
            "existing_student_id": existing.id,
        }
    ]


def preview_student_import(db: Session, rows: list[dict[str, str]]) -> dict:
    school = _target_school(db)
    seen: set[str] = set()
    importable_rows = []
    preview_rows = []
    # `start=2`：这是**文件里的行号**，不是记录序号——第 1 行是表头。操作员拿到
    # 「第 2 行」是要回 Excel 里改那一行的，改成 1 会让他去改表头。
    for index, row in enumerate(rows, start=2):
        errors = []
        for field in REQUIRED_FIELDS:
            if not row.get(field):
                errors.append(f"缺少{field_label(field)}")
        if row.get("student_no") in seen:
            errors.append("文件内重复学号")
        seen.add(row.get("student_no", ""))
        # 找不到学校就没法判冲突——那种情况下冲突清单是空的，
        # `commit_student_import` 会自己把学校建出来，而它那边的同一份判据
        # 会在学校建好之后重算。这里不猜。
        conflicts = conflicts_for_row(db, school, row) if school else []
        if row.get("grade") and row.get("class_name"):
            # 年级与班级编号的一致性。放在预览里而不是提交时：这一层的约定是
            # 「坏单元格变成学校看得见、改得动的一行错误」，而不是三种结果里的
            # 另外两种——异常拒掉整份文件，或者悄悄建成一个挂错年级的班。
            problem = check_grade_and_class(row["grade"], row["class_name"])
            if problem:
                errors.append(problem)
        # 老模板（最后一列还叫「出生日期」）传上来时，这一整列会被读成空——每个学生的年龄
        # 都静默变成 NULL，和「学校没填」长得一模一样。所以那一列只要还有值就当场报错，
        # 让人看见列已经改了，而不是事后在名册上发现年龄格全空。空值不报：只有「年龄」
        # 一列的新文件里，这一格本来就是空的。
        if not row.get("age") and row.get("legacy_birth_date"):
            errors.append("「出生日期」列已改为「年龄」，请填 13 这样的整数")
        # Normalised here, not in `normalize_row`: a bad cell has to become a
        # per-row error the school can see and fix, not an exception that rejects
        # the other 200 rows, and not a value quietly coerced to NULL.
        parsed: dict[str, str] = {}
        try:
            parsed["gender"] = parse_gender(row.get("gender", "")) or ""
        except ValueError as exc:
            errors.append(str(exc))
        try:
            age = parse_age(row.get("age", ""))
            # The preview token is a JWT, so the payload stays JSON — numbers travel
            # as strings and are parsed back at commit time.
            parsed["age"] = str(age) if age is not None else ""
        except ValueError as exc:
            errors.append(str(exc))
        item = {**row, "row_no": index, "errors": errors, "conflicts": conflicts}
        preview_rows.append(item)
        # 带冲突的行**照样进 token**：选了「覆盖」它们就要被写进去。少这一句，
        # 「覆盖」这条路根本走不通——token 里没有那几行。而带了错的行不进，
        # 那是一种没有任何处置方式能救的状态。
        if not errors:
            importable_rows.append({**row, **parsed})
    ready = sum(1 for item in preview_rows if not item["errors"] and not item["conflicts"])
    conflicted = sum(1 for item in preview_rows if not item["errors"] and item["conflicts"])
    token = create_preview_token(importable_rows) if importable_rows else None
    return {
        "total": len(rows),
        "valid_count": ready,
        "conflict_count": conflicted,
        "error_count": len(rows) - ready - conflicted,
        "rows": preview_rows,
        "preview_token": token,
    }


def field_label(field: str) -> str:
    return {"student_no": "学号", "name": "姓名", "grade": "年级", "class_name": "班级"}[field]


def create_preview_token(rows: list[dict[str, str]]) -> str:
    settings = get_settings()
    return jwt.encode({"rows": rows, "kind": "student_import_preview"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_preview_token(token: str) -> list[dict[str, str]]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("VALIDATION_ERROR", "导入预览已失效，请重新预览", 422) from exc
    if payload.get("kind") != "student_import_preview":
        raise AppError("VALIDATION_ERROR", "导入预览无效", 422)
    return payload.get("rows", [])


def _grade_and_class(db: Session, school: School, row: dict[str, str]) -> tuple[Grade, ClassGroup]:
    """取或建这一行说的年级与班级。

    取或建而不是「必须已存在」：名册导入本来就是学校建立年级/班级的地方，
    一份新学期的文件里出现一个还没有的班是正常的。
    """
    grade = db.scalar(select(Grade).where(Grade.school_id == school.id, Grade.name == row["grade"]))
    if not grade:
        grade = Grade(school_id=school.id, name=row["grade"], sort_order=0)
        db.add(grade)
        db.flush()
    class_group = db.scalar(
        select(ClassGroup).where(ClassGroup.school_id == school.id, ClassGroup.grade_id == grade.id, ClassGroup.name == row["class_name"])
    )
    if not class_group:
        class_group = ClassGroup(school_id=school.id, grade_id=grade.id, name=row["class_name"])
        db.add(class_group)
        db.flush()
    return grade, class_group


def _overwrite_student(db: Session, student: Student, row: dict[str, str], grade: Grade, class_group: ClassGroup) -> None:
    """用文件里那一行更新名册上的这名学生。

    **就地改，不删了重建**：`student.id` 被关怀档案、测评会话、风险事件、账号范围
    引用着，全库没有 `ondelete=`（§1）。删一次就是把一个学生一学期的关怀过程和
    测评记录全部悬空——而学校要的只是「名册上这个人的班级换了」。

    选填列（性别 / 年龄）**只有文件里真有值时才写**。空着就跳过，不是写 NULL：
    「这一列我没填」与「把名册上这个数抹掉」是两件事，前者是常态（四列的老模板
    根本没有这两列），后者没有任何人去要求过。这条与 §1 里「年龄整列 NULL 和
    '学校没填' 长得一模一样」是同一个坑，换个地方。
    """
    student.name = row["name"]
    # `masked_name` 是**展示名**，不是遮蔽（§1）：写入方写的就是真名，
    # 遮蔽由 `export_service.mask_student_name` 在读取时现算。改名要跟着改这个。
    student.masked_name = row["name"]
    student.grade_id = grade.id
    student.class_id = class_group.id
    if row.get("gender"):
        student.gender = row["gender"]
    if row.get("age"):
        student.age = int(row["age"])
    # 账号的展示名跟着走——它是同一名学生在系统里的另一个「名字」，
    # 名册改了姓名而账号还印着旧名字，两处会各说各话。
    user = db.scalar(select(UserAccount).where(UserAccount.account == student.student_no))
    if user:
        user.display_name = row["name"]


def commit_student_import(
    db: Session, rows: list[dict[str, str]], resolution: str | None = None
) -> dict:
    """把预览过的行写进名册，单事务。

    `resolution` 只在文件里真的有冲突（学号已在名册上）时才是必需的：`overwrite`
    （用文件里的信息更新这名学生）或 `skip`（这几行不动，其余照导）。没有冲突的
    文件照旧一次提交，不必带它——要求 99% 的正常导入都先回答一个不该问的问题，
    只会让人乱点。
    """
    if resolution is not None and resolution not in RESOLUTIONS:
        raise AppError("VALIDATION_ERROR", RESOLUTION_HINT, 422)
    school = _target_school(db)
    if not school:
        # `name` 只写这一次、也**不会显示给任何人**：界面上所有校名都读配置里的
        # `system_setting.org.school_name`（`auth_service._school_display_names`），
        # 这一行只是组织表里的一个锚点。所以不必去同步它。
        school = School(code="QH", name="青禾实验学校")
        db.add(school)
        db.flush()

    # 冲突**先全部算完再动手**：写了一半才发现「还差一个决定」，用户看到的是导入
    # 失败，库里却已经躺着一批新学生。所以这一步在任何写入之前。重算而不是信
    # token 里的那份：预览与提交之间可能又有人导了同一份文件（同测评导入）。
    planned = [(row, conflicts_for_row(db, school, row)) for row in rows]
    if resolution is None:
        pending = [row for row, conflicts in planned if conflicts]
        if pending:
            raise AppError(
                "VALIDATION_ERROR",
                f"有 {len(pending)} 条记录的学号已在名册上，请选择覆盖或放弃后重试",
                422,
            )

    created = updated = skipped = 0
    default_password_hash = hash_password("123456")
    for row, conflicts in planned:
        if conflicts and resolution == RESOLUTION_SKIP:
            skipped += 1
            continue
        grade, class_group = _grade_and_class(db, school, row)
        existing = db.scalar(
            select(Student).where(
                Student.school_id == school.id, Student.student_no == row["student_no"]
            )
        )
        if existing:
            # 走到这里就是「有冲突 + 选了覆盖」。冲突是**算出来的**，所以不存在
            # 「有冲突却选了覆盖」之外的第三种情况。
            _overwrite_student(db, existing, row, grade, class_group)
            updated += 1
            continue
        # `.get` rather than `[...]`: a preview token minted before the profile
        # columns existed (an open tab across a deploy) is still validly signed and
        # simply carries neither key.
        age = row.get("age")
        student = Student(
            student_no=row["student_no"],
            name=row["name"],
            masked_name=row["name"],
            school_id=school.id,
            grade_id=grade.id,
            class_id=class_group.id,
            gender=row.get("gender") or None,
            age=int(age) if age else None,
        )
        db.add(student)
        db.flush()
        user = UserAccount(
            account=row["student_no"],
            account_type=AccountType.STUDENT_NO,
            display_name=row["name"],
            password_hash=default_password_hash,
            role_code=RoleCode.STUDENT,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.add(UserScope(user_id=user.id, scope_type=ScopeType.STUDENT, school_id=school.id, student_id=student.id))
        created += 1
    return {"created": created, "updated": updated, "skipped": skipped}

