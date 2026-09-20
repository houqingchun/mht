import hashlib
import re
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount, UserScope
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.importing import (
    StudentAgeChangeLog,
    StudentRosterImportBatch,
    StudentRosterImportRow,
)
from app.models.organization import ClassGroup, Grade, School, Student
from app.security.passwords import hash_password
from app.services.audit_service import write_audit
from app.services.import_text import decode_upload, parse_json_rows, read_csv_dicts
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

# ── 批次与逐行的状态码（`student_roster_import_batch.status` /
#    `student_roster_import_row.processing_status`）─────────────────────────
#
# 批次的 `status` 两个取值**都会真的出现**：上传一份文件落一行 `PREVIEW`，确认导入
# 把它改成 `COMMITTED`。`PREVIEW` 不是「没写完的临时行」——它是「有人上传过这份文件、
# 看过校验结果、还没有拍板」，而这正是操作员回到这一页时想找的那一行：他上传完之后
# 被叫走了，回来要能接着提交，而不是重新传一次文件。
BATCH_STATUS_PREVIEW = "PREVIEW"
BATCH_STATUS_COMMITTED = "COMMITTED"

# 逐行状态。前四个在**提交**时才定得下来（提交之前没人知道这一行会新建还是更新）；
# `ERROR` 在**预览**时就定了——坏单元格（缺列、班级与年级对不上）没有任何处置方式
# 能救，所以那一行的结论不会因为后面的选择而改变。
ROW_STATUS_PENDING = "PENDING"
ROW_STATUS_CREATED = "CREATED"
ROW_STATUS_UPDATED = "UPDATED"
ROW_STATUS_SKIPPED = "SKIPPED"
ROW_STATUS_ERROR = "ERROR"

# 批次号按**天**，与测评导入的 `IMPORT-YYYYMM-N` 不同（那边按月的理由是「一场任务
# 就是一个月」）。名册导入没有任务这一层，它的事件单位是「某天有人拿了一份文件来」，
# 而改正一处错别字再导一次是常态——同一天两批是正常的，不该被折成一批。
ROSTER_BATCH_PREFIX = "ROSTER"


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
# （`commit_roster_import` 只会拿字符串去 `Grade.name` 里找、找不到就新建一个），
# 比让录错的人当场看见一行错误糟得多。
GRADE_CLASS_PREFIX = {"初一": "7", "初二": "8", "初三": "9"}
CLASS_PREFIX_GRADE = {prefix: grade for grade, prefix in GRADE_CLASS_PREFIX.items()}
GRADE_INPUT_HINT = "年级应为 初一 / 初二 / 初三"
CLASS_NAME_INPUT_HINT = "班级格式应为 701（首位 7/8/9 分别表示初一/初二/初三）"

# 学号允许的形状：ASCII 字母、数字、连字符。
#
# 拦的是**被表格软件改坏**的那几种值，它们不是「格式不合我们的规矩」，而是「这一格已经
# 不是学号了」，而没有一种编号规则产得出它们：
#   `2.70252E+10`        列宽不够，Excel 按科学计数法显示（`.` 与 `+` 都出局）
#   `2702516010100.00%`  单元格格式是百分比
#   `27025160101'`       给单元格加 `'` 前缀「转成文本」——**另存为 CSV 会把这个撇号留在
#                        值里**。2026-09-20 实测：这个值一路进了预览，而那一行显示「可导入」
# 静默收下的代价是一名学生带着错的学号进名册，而事后只能靠人眼在两列数字里找出来——
# 名册是这套系统的根，学号又是账号（`student_no` 即登录名），错一个就是一个人登不进来。
#
# **只拦形状，不拦长度与含义**：缺口 2 那条「不校验格式」说的是「各位数字的含义没有确认
# 过」，而这条规则不需要知道含义——`S001`（种子与全套测试都用它）与 `27025160101` 都通过。
# 连字符留着：`2027-01-001` 这类写法是学校真会用的。
STUDENT_NO_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
STUDENT_NO_INPUT_HINT = "学号只能由字母、数字和连字符组成"


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
    # 解码与「表格读不读得成」都归 `import_text`：学校手上那份文件多半是 Excel「另存为
    # CSV」出来的 GBK，而只认 UTF-8 会抛一个不是 `AppError` 的异常，屏幕上就只剩一句
    # `JSON.parse: …`（编码与 `csv.Error` 两条路都在那里收住）。
    if filename.lower().endswith(".json"):
        return [normalize_row(row) for row in parse_json_rows(decode_upload(content))]
    return [normalize_row(row) for row in read_csv_dicts(content)]


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
    `analyze_roster_rows`).
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


def _ensure_target_school(db: Session) -> School:
    """取这所学校，没有就建出来。

    预览与提交都要它，而且现在**预览也需要**：批次行上 `school_id` 是 NOT NULL，
    而预览已经落一行批次了（见 `start_roster_import`）。从前只有提交会建学校，
    是因为预览那一侧只需要一个「判冲突用的锚」——取不到就跳过冲突判定。
    现在那条路走不通了，所以建学校的动作**提前到预览**，两边共用这一处。

    幂等：一个库里只有一行 `QH`（单校写死，缺口 2）。
    """
    school = _target_school(db)
    if school is None:
        # `name` 只写这一次、也**不会显示给任何人**：界面上所有校名都读配置里的
        # `system_setting.org.school_name`（`auth_service._school_display_names`），
        # 这一行只是组织表里的一个锚点。所以不必去同步它。
        school = School(code="QH", name="青禾实验学校")
        db.add(school)
        db.flush()
    return school


def _next_batch_no(db: Session) -> str:
    """`ROSTER-20260919-1`：当天第几批。

    `taken` 一次取全而不是逐条查：同一天里已经有三批时，逐条查是三次往返，
    而这一天剩下的批次数一眼就看得出来。
    """
    prefix = f"{ROSTER_BATCH_PREFIX}-{datetime.now():%Y%m%d}"
    taken = set(
        db.scalars(
            select(StudentRosterImportBatch.batch_no).where(
                StudentRosterImportBatch.batch_no.like(f"{prefix}-%")
            )
        ).all()
    )
    number = 1
    while f"{prefix}-{number}" in taken:
        number += 1
    return f"{prefix}-{number}"


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


def analyze_roster_rows(
    db: Session, school: School, rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """逐行校验与冲突判定，一行一条结论。

    单独一个函数，因为**预览与提交各要算一遍**，而且必须用同一份判据：预览与提交
    之间可能又有人导了同一份文件（这正是 `assessment_import_service` 里 `planned`
    那条注释的同一件事）。抽出来之前它是内联在旧那个 `preview_student_import` 里的。

    返回的每一项在文件里那一行的**原始文本**之上加了四样东西：
    `row_no`（文件里的行号）、`errors`（只能改文件重导的问题）、`conflicts`
    （要操作员拍板的事）、以及 `gender_code` / `age_value`（这一行落库时要用的值）。
    """
    seen: set[str] = set()
    analyzed: list[dict[str, Any]] = []
    # `start=2`：这是**文件里的行号**，不是记录序号——第 1 行是表头。操作员拿到
    # 「第 2 行」是要回 Excel 里改那一行的，改成 1 会让他去改表头。
    for index, row in enumerate(rows, start=2):
        errors = []
        for field in REQUIRED_FIELDS:
            if not row.get(field):
                errors.append(f"缺少{field_label(field)}")
        # 被表格软件改坏了的学号（科学计数法 / 百分比 / 多一个撇号）不静默收下，
        # 见 `STUDENT_NO_PATTERN`。与年级/班级那一条同一层：只能改文件重导，所以它是一条
        # **错误**，不是一条要人拍板的冲突。空值不报——那是上面那句「缺少学号」的事。
        if row.get("student_no") and not STUDENT_NO_PATTERN.match(row["student_no"]):
            errors.append(f"{STUDENT_NO_INPUT_HINT}，当前是「{row['student_no']}」")
        if row.get("student_no") in seen:
            errors.append("文件内重复学号")
        seen.add(row.get("student_no", ""))
        conflicts = conflicts_for_row(db, school, row)
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
        gender_code = ""
        try:
            gender_code = parse_gender(row.get("gender", "")) or ""
        except ValueError as exc:
            errors.append(str(exc))
        age_value: int | None = None
        try:
            age_value = parse_age(row.get("age", ""))
        except ValueError as exc:
            errors.append(str(exc))
        analyzed.append(
            {
                **row,
                "row_no": index,
                "errors": errors,
                "conflicts": conflicts,
                "gender_code": gender_code,
                "age_value": age_value,
            }
        )
    return analyzed


def _summarize(analyzed: list[dict[str, Any]]) -> dict[str, int]:
    """三个计数，以及「这一批有没有东西可提交」。

    `submittable_count` 是**判据**，不是给眼睛看的派生数：选了「覆盖」时冲突行也要
    写进去、选了「放弃」时它们被跳过，两种情况下它们都属于「这一批要处理的行」。
    前端按它禁用「确认导入」，所以在后端算一次、原样发下去——两边各算一次会漂。
    """
    ready = sum(1 for item in analyzed if not item["errors"] and not item["conflicts"])
    conflicted = sum(1 for item in analyzed if not item["errors"] and item["conflicts"])
    return {
        "total": len(analyzed),
        "valid_count": ready,
        "conflict_count": conflicted,
        "error_count": len(analyzed) - ready - conflicted,
        "submittable_count": ready + conflicted,
    }


def start_roster_import(
    db: Session, *, actor: UserAccount, filename: str, content: bytes
) -> dict[str, Any]:
    """上传一份名册文件：解析、逐行校验，**并把这一批落进库**。

    「预览」从这一刻起是一次写操作，这是有意的。批次的 `status` 默认值就是
    `PREVIEW`（对齐阶段的 DDL），而它要回答的问题在提交之前就已经存在了：
    **「有人上传过这份文件，他看到的结论是什么」**。预览的结果只活在返回值里的
    话，操作员上传完被叫走、回来时那一页是空的，他只能重传一次——而重传之后
    库里的逐行明细与刚才屏幕上那份是不是同一份，就没有任何东西能回答了。

    文件指纹（`file_sha256`）与操作者一起决定**复用哪一行**：同一个人把同一份
    文件再传一次（改了别处的错、或者只是想再看一眼）不会留下第二行 PREVIEW 批次。
    这一条是必需的，否则学校每改一次错别字就多一批孤儿行，而批次历史那一页的
    全部意义就是让人按它找「哪一批真的导进去了」。
    """
    rows = parse_student_import(filename, content)
    school = _ensure_target_school(db)
    digest = hashlib.sha256(content).hexdigest()
    batch = db.scalar(
        select(StudentRosterImportBatch)
        .where(
            StudentRosterImportBatch.imported_by == actor.id,
            StudentRosterImportBatch.file_sha256 == digest,
            StudentRosterImportBatch.status == BATCH_STATUS_PREVIEW,
        )
        .order_by(StudentRosterImportBatch.id.desc())
    )
    if batch is None:
        batch = StudentRosterImportBatch(
            batch_no=_next_batch_no(db),
            school_id=school.id,
            file_name=filename,
            file_sha256=digest,
            imported_by=actor.id,
            status=BATCH_STATUS_PREVIEW,
        )
        db.add(batch)
        db.flush()
    else:
        # 复用这一行，但**逐行明细整批重写**：同一份文件重新预览时结论可能已经变了
        # （这中间名册多了一个学号，那一行就从「新增」变成了「冲突」），留着旧结论
        # 会让库里那一批说的与实际不符。删行是安全的——`student_roster_import_row`
        # 是叶子表（没有任何东西引用它，§9 那条「叶子表删了重插是安全的」）。
        db.execute(
            delete(StudentRosterImportRow).where(StudentRosterImportRow.batch_id == batch.id)
        )
        batch.file_name = filename
    analyzed = analyze_roster_rows(db, school, rows)
    for item in analyzed:
        # ERROR 在**预览**时就定下来（坏单元格没有处置方式能救）；其余行先 PENDING，
        # 提交时才知道它会变成 CREATED / UPDATED / SKIPPED。
        #
        # 这里存的是**这一行落进系统时的值**，不是文件里的原始字节：`gender` 存
        # `MALE`/`FEMALE`（界面用 `genderLabel` 翻回中文），`age` 存整数。
        # `age` 是六列里唯一一个**装不下原始写法**的——`student.age` 是 int 列，
        # 「13岁」那种写法的原文只活在错误文案里，不在这里。
        db.add(
            StudentRosterImportRow(
                batch_id=batch.id,
                row_no=item["row_no"],
                student_no=item["student_no"] or None,
                name=item["name"] or None,
                grade_name=item["grade"] or None,
                class_name=item["class_name"] or None,
                gender=item["gender_code"] or None,
                age=item["age_value"],
                processing_status=ROW_STATUS_ERROR if item["errors"] else ROW_STATUS_PENDING,
                # 冲突码只在**冲突**时有值，与错误分开：冲突要人拍板，错误不用。
                conflict_code=(
                    item["conflicts"][0]["type"] if item["conflicts"] else None
                ),
                message="；".join(item["errors"]) or None,
            )
        )
    summary = _summarize(analyzed)
    batch.total_rows = summary["total"]
    batch.error_rows = summary["error_count"]
    db.flush()
    return {
        "batch_id": batch.id,
        "batch_no": batch.batch_no,
        **summary,
        "rows": [
            {
                "row_no": item["row_no"],
                "student_no": item["student_no"],
                "name": item["name"],
                "grade": item["grade"],
                "class_name": item["class_name"],
                "gender": item["gender"],
                "age": item["age"],
                "errors": item["errors"],
                "conflicts": item["conflicts"],
            }
            for item in analyzed
        ],
    }


def field_label(field: str) -> str:
    return {"student_no": "学号", "name": "姓名", "grade": "年级", "class_name": "班级"}[field]


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


def _row_from_storage(row: StudentRosterImportRow) -> dict[str, str]:
    """把库里那一行还原成「文件里那一行」的形状，好让提交走与预览同一个函数。

    存的是**规范化之后**的值：`gender` 是 `MALE` / `FEMALE`（界面用 `genderLabel`
    翻回中文），`age` 是整数。所以这里不再解析一遍——`parse_gender("MALE")` 会过、
    `parse_age("13")` 也会过，但那两次解析只是把「已经定下来的值」重新怀疑一次，
    而它们与预览时那次解析的**唯一**区别就是多了一次可能失败的机会。
    """
    return {
        "student_no": row.student_no or "",
        "name": row.name or "",
        "grade": row.grade_name or "",
        "class_name": row.class_name or "",
        "gender": row.gender or "",
        "age": str(row.age) if row.age is not None else "",
    }


def _overwrite_student(
    db: Session,
    student: Student,
    row: dict[str, str],
    grade: Grade,
    class_group: ClassGroup,
    *,
    actor: UserAccount,
    batch: StudentRosterImportBatch,
    row_no: int,
) -> None:
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
        _set_age(db, student, int(row["age"]), actor=actor, batch=batch, row_no=row_no)
    # 账号的展示名跟着走——它是同一名学生在系统里的另一个「名字」，
    # 名册改了姓名而账号还印着旧名字，两处会各说各话。
    user = db.scalar(select(UserAccount).where(UserAccount.account == student.student_no))
    if user:
        user.display_name = row["name"]


def _set_age(
    db: Session,
    student: Student,
    new_age: int,
    *,
    actor: UserAccount,
    batch: StudentRosterImportBatch,
    row_no: int,
) -> None:
    """改年龄，并留下**两处**痕迹：`student_age_change_log` 一行 + 一条审计。

    两处不是重复。审计页按动作码搜（`更新学生年龄`），它回答的是「谁在什么时候动了
    这个数」——与 `assessment_import_service._update_roster_age` 写在同一个动作码下，
    因为对读轨迹的人来说这两件事是同一件事：一次导入改了名册上的年龄。而
    `student_age_change_log` 是**结构化的那一份**：它把「名册导入批次 N 的第 M 行」
    记成外键，能回答审计答不出的问题——「这一列到底被改过几次」。

    `reason` 写中文，与审计的 `detail` 同一口径（该列自己的 docstring 记了这条）。
    """
    previous = student.age
    if previous == new_age:
        # 值没变就不记。一行「12 → 12」在审计与变更记录里都是噪声，
        # 而它会让「这一列被改过几次」那个问题的答案多出一串假的。
        return
    student.age = new_age
    db.add(
        StudentAgeChangeLog(
            student_id=student.id,
            roster_import_batch_id=batch.id,
            old_age=previous,
            new_age=new_age,
            reason=f"名册导入覆盖年龄（{batch.batch_no} 第 {row_no} 行）",
            changed_by=actor.id,
        )
    )
    write_audit(
        db,
        action="更新学生年龄",
        resource_type="STUDENT",
        resource_id=str(student.id),
        actor=actor,
        student_id=student.id,
        detail=f"名册 {previous} → 文件 {new_age}（名册导入 {batch.batch_no}）",
    )


def commit_roster_import(
    db: Session, *, actor: UserAccount, batch_id: int, resolution: str | None = None
) -> dict[str, Any]:
    """把预览过的那一批写进名册，单事务。

    `resolution` 只在文件里真的有冲突（学号已在名册上）时才是必需的：`overwrite`
    （用文件里的信息更新这名学生）或 `skip`（这几行不动，其余照导）。没有冲突的
    文件照旧一次提交，不必带它——要求 99% 的正常导入都先回答一个不该问的问题，
    只会让人乱点。

    请求体里带的是 `batch_id` 而不是整份文件或一个签过名的预览令牌：行已经在库里
    （`start_roster_import` 落下的），提交时读回来。这样「这一批到底导了哪些行」
    在提交之前就有据可查，而操作员上传完被叫走、回来接着提交时，他提交的正是
    屏幕上那一批——不是浏览器内存里那份可能已经过期的副本。
    """
    if resolution is not None and resolution not in RESOLUTIONS:
        raise AppError("VALIDATION_ERROR", RESOLUTION_HINT, 422)
    batch = db.get(StudentRosterImportBatch, batch_id)
    if batch is None:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != BATCH_STATUS_PREVIEW:
        # 已经提交过的批次再点一次「确认导入」会把整份名册写第二遍——第二次遇到
        # 的每一行都是冲突，而选「覆盖」时它会静默地再覆盖一遍（看起来成功了）。
        raise AppError(
            "VALIDATION_ERROR",
            f"批次 {batch.batch_no} 已经导入过了，要再导一次请重新上传文件",
            422,
        )
    school = _ensure_target_school(db)
    # `batch_id` 是**客户端传来的 ID**，所以它本身不能成为凭据（§9 那条：取到之后
    # 校验，否则档案 ID 就是越权凭据）。名册导入今天写死单校（缺口 2），所以这一条
    # 在现阶段的库上永远成立——正因为如此它才便宜：等哪天多校了，漏掉它不会以
    # 「报错」的形式出现，而会以「A 校管理员把 B 校的名册导进了 B 校」的形式出现，
    # 界面上一切正常。
    #
    # 回 404 而不是 403，与上面「批次不存在」**同一句话**：`batch_id` 既然来自客户端，
    # 一句「这一批属于别的学校」就等于确认了那个 id 存在。
    if batch.school_id != school.id:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    rows = db.scalars(
        select(StudentRosterImportRow)
        .where(StudentRosterImportRow.batch_id == batch.id)
        .order_by(StudentRosterImportRow.row_no)
    ).all()
    # ERROR 行**不参与提交**。「覆盖 / 放弃」这两个回答回答的是冲突，不是在回答
    # 「这一行的单元格填坏了」——把一条已经报错的行一起写进名册，只会让它凭空
    # 出现，而屏幕上它明明是红的。
    planned = [
        (row, conflicts_for_row(db, school, _row_from_storage(row)))
        for row in rows
        if row.processing_status != ROW_STATUS_ERROR
    ]
    # 冲突**先全部算完再动手**：写了一半才发现「还差一个决定」，用户看到的是导入
    # 失败，库里却已经躺着一批新学生。所以这一步在任何写入之前。重算而不是信预览
    # 时那份：预览与提交之间可能又有人导了同一份文件（同测评导入的 `planned`）。
    if resolution is None:
        conflicted = [row for row, conflicts in planned if conflicts]
        if conflicted:
            raise AppError(
                "VALIDATION_ERROR",
                f"有 {len(conflicted)} 条记录的学号已在名册上，请选择覆盖或放弃后重试",
                422,
            )

    created = updated = skipped = 0
    default_password_hash = hash_password("123456")
    for row, conflicts in planned:
        if conflicts and resolution == RESOLUTION_SKIP:
            row.processing_status = ROW_STATUS_SKIPPED
            # 「放弃」的那一行把原因**留在它自己身上**：这一批导完之后回看，
            # 「为什么这个人没更新」的唯一答案就在这一格。
            row.message = conflicts[0]["message"]
            skipped += 1
            continue
        data = _row_from_storage(row)
        grade, class_group = _grade_and_class(db, school, data)
        existing = db.scalar(
            select(Student).where(
                Student.school_id == school.id, Student.student_no == data["student_no"]
            )
        )
        if existing:
            # 走到这里就是「有冲突 + 选了覆盖」。冲突是**算出来的**，所以不存在
            # 「有冲突却选了覆盖」之外的第三种情况。
            _overwrite_student(
                db,
                existing,
                data,
                grade,
                class_group,
                actor=actor,
                batch=batch,
                row_no=row.row_no,
            )
            row.student_id = existing.id
            row.processing_status = ROW_STATUS_UPDATED
            updated += 1
            continue
        student = Student(
            student_no=data["student_no"],
            name=data["name"],
            masked_name=data["name"],
            school_id=school.id,
            grade_id=grade.id,
            class_id=class_group.id,
            gender=data["gender"] or None,
            age=int(data["age"]) if data["age"] else None,
        )
        db.add(student)
        db.flush()
        user = UserAccount(
            account=data["student_no"],
            account_type=AccountType.STUDENT_NO,
            display_name=data["name"],
            password_hash=default_password_hash,
            role_code=RoleCode.STUDENT,
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.add(UserScope(user_id=user.id, scope_type=ScopeType.STUDENT, school_id=school.id, student_id=student.id))
        row.student_id = student.id
        row.processing_status = ROW_STATUS_CREATED
        created += 1
    # 成功的那两行把 `message` 清掉：它只在「需要解释」时有值（错误与放弃）。
    # `conflict_code` **留着**——它记的是这一行曾经撞上过什么，而选「覆盖」时
    # 那件事确实发生过，只是被处置掉了。
    for row, _ in planned:
        if row.processing_status in (ROW_STATUS_CREATED, ROW_STATUS_UPDATED):
            row.message = None
    batch.created_rows = created
    batch.updated_rows = updated
    batch.skipped_rows = skipped
    batch.error_rows = sum(1 for row in rows if row.processing_status == ROW_STATUS_ERROR)
    batch.status = BATCH_STATUS_COMMITTED
    db.flush()
    return {
        "batch_id": batch.id,
        "batch_no": batch.batch_no,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "error_count": batch.error_rows,
    }


def list_roster_import_batches(db: Session, *, limit: int = 20) -> dict[str, Any]:
    """最近的几批名册导入，新的在前。

    `limit` 是给眼睛的上限（§10）：这一页要看的是「最近导入过什么」，
    而一个用了三年的库会有几百批。返回的 `total` 说的是**一共有多少批**，
    界面据此写「另有 N 批未显示」。

    **按学校过滤**，与 `commit_roster_import` 那条检查同一个理由：名册是组织事实，
    A 校的管理员不该看见 B 校导过几批、导的是哪个文件。今天写死单校（缺口 2），
    所以这一条恒真——写在这里是为了多校那一天**读的那一侧不用再想一遍**。
    """
    school = _target_school(db)
    if school is None:
        # 一所学校都没有 → 一批也没有。走这一支而不是「不过滤」：这是 fail-closed
        # 的那一侧，而且它与 `_target_school` 的语义一致（那张表里只有一行 `QH`）。
        return {"items": [], "total": 0, "truncated": False}
    total = (
        db.scalar(
            select(func.count())
            .select_from(StudentRosterImportBatch)
            .where(StudentRosterImportBatch.school_id == school.id)
        )
        or 0
    )
    batches = db.scalars(
        select(StudentRosterImportBatch)
        .where(StudentRosterImportBatch.school_id == school.id)
        .order_by(StudentRosterImportBatch.id.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": batch.id,
                "batch_no": batch.batch_no,
                "file_name": batch.file_name,
                "status": batch.status,
                "total_rows": batch.total_rows,
                "created_rows": batch.created_rows,
                "updated_rows": batch.updated_rows,
                "skipped_rows": batch.skipped_rows,
                "error_rows": batch.error_rows,
                "created_at": batch.created_at,
            }
            for batch in batches
        ],
        "total": total,
        "truncated": total > len(batches),
    }


def list_roster_import_rows(db: Session, *, batch_id: int) -> dict[str, Any]:
    """某一批的逐行明细。行数有界（一份名册就是一个学校的人），客户端排序分页（§10）。

    批次不存在时 404 而不是空列表：一个空列表会让界面说「这一批没有明细」，
    而实话是「没有这一批」——§14 那条「空态是一句关于数据的话」在这里的反面。

    「不属于这所学校」也回 404 而不是 403，与「不存在」**同一句话**：`batch_id` 是
    客户端传来的，回一句「这一批属于别的学校」等于确认了那个 id 存在。上面那条
    `commit_roster_import` 里的检查同此。
    """
    batch = db.get(StudentRosterImportBatch, batch_id)
    if batch is None:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    school = _target_school(db)
    if school is None or batch.school_id != school.id:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    rows = db.scalars(
        select(StudentRosterImportRow)
        .where(StudentRosterImportRow.batch_id == batch.id)
        .order_by(StudentRosterImportRow.row_no)
    ).all()
    return {
        "batch_id": batch.id,
        "batch_no": batch.batch_no,
        "items": [
            {
                "row_no": row.row_no,
                "student_no": row.student_no,
                "name": row.name,
                "grade_name": row.grade_name,
                "class_name": row.class_name,
                "gender": row.gender,
                "age": row.age,
                "processing_status": row.processing_status,
                "conflict_code": row.conflict_code,
                "message": row.message,
                "student_id": row.student_id,
            }
            for row in rows
        ],
    }

