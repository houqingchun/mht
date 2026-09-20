import asyncio
import io
from typing import Any

from fastapi import UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.v1.student_roster import roster_import_preview
from app.models.account import UserAccount
from app.models.audit import AuditLog
from app.models.importing import (
    StudentAgeChangeLog,
    StudentRosterImportBatch,
    StudentRosterImportRow,
)
from app.models.organization import School, Student
from app.services.student_import_service import OVERWRITABLE_FIELDS
from app.tests.conftest import auth_headers


def test_admin_can_preview_and_commit_student_csv_import(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name\nS002,王同学,初一,702\n,缺号,初一,701\nS001,重复,初一,701\n"
    preview = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    )
    assert preview.status_code == 200
    data = preview.json()["data"]
    assert data["total"] == 3
    assert data["valid_count"] == 1
    # `S001,重复` 说的是种子里的那个学生：学号已在名册上，所以它是**待确认**，
    # 不再是「错误」（2026-09-17）。另外两行才是错误——`缺号` 缺学号。
    assert data["conflict_count"] == 1
    assert data["error_count"] == 1
    assert data["batch_id"]

    commit = client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": data["batch_id"], "resolution": "skip"},
    )
    assert commit.status_code == 200
    assert commit.json()["data"]["created"] == 1

    student = db_session.scalar(select(Student).where(Student.student_no == "S002"))
    assert student is not None
    account = db_session.scalar(select(UserAccount).where(UserAccount.account == "S002"))
    assert account is not None
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "导入学生"))
    assert audit is not None
    # 处置方式进审计（§8 对导出遮蔽模式是同一条道理）：事后读轨迹的人要能分辨
    # 「这份文件覆盖过名册」与「这份文件放弃了那几行」。
    assert "放弃冲突行" in audit.detail


def test_admin_can_preview_student_json_import(client):
    headers = auth_headers(client, "admin", "admin")
    payload = '[{"student_no":"S003","name":"赵同学","grade":"初二","class_name":"801"}]'
    response = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.json", payload, "application/json")},
    )
    assert response.status_code == 200
    assert response.json()["data"]["valid_count"] == 1


def test_non_admin_cannot_import_students(client):
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", "student_no,name,grade,class_name\nS004,钱同学,初一,701\n", "text/csv")},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"



# --------------------------------------------------------------------------
# 年级与班级编号必须一致（701 = 初一 1 班）
# --------------------------------------------------------------------------


def _preview(client, headers, csv_content: str) -> dict:
    return client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]


def test_class_code_must_match_its_grade(client):
    """「初二,701」必须报错。

    这是选择保留年级列的全部理由：`commit_roster_import` 只会拿字符串去
    `Grade.name` 里找、找不到就新建，所以不拦的话这一行会被静默挂到初二下面。
    录错的人看不见任何异常，等到年级统计对不上时已经无从查起。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client,
        headers,
        "student_no,name,grade,class_name\nS102,陈同学,初二,701\n",
    )

    assert data["valid_count"] == 0
    # 错误文案要说清是谁属于谁，否则「不一致」三个字对着 27 行表格毫无帮助
    assert data["rows"][0]["errors"] == ["班级 701 属于初一，与年级 初二 不一致"]


def test_every_grade_accepts_its_own_prefix(client):
    """7/8/9 三个前缀各自对应一个年级，且只有自己的那个通过。"""
    headers = auth_headers(client, "admin", "admin")
    for grade, class_name in (("初一", "702"), ("初二", "801"), ("初三", "901")):
        assert _preview(
            client, headers, f"student_no,name,grade,class_name\nS2{grade},{grade}同学,{grade},{class_name}\n"
        )["valid_count"] == 1


def test_a_class_code_that_is_not_three_digits_is_rejected(client):
    """1班 / 7 / 7011 / 全角７０１ 都挡下来，并且不把整份文件打成 500。

    全角那条是防 500 的：`'７０１'.isdigit()` 为真，只看 isdigit 的实现会拿 '７'
    去查前缀表而抛 KeyError——学校从中文 Excel 里复制粘贴时很容易带上全角。
    """
    headers = auth_headers(client, "admin", "admin")
    for class_name in ("1班", "7", "7011", "７０１", "07班"):
        data = _preview(
            client,
            headers,
            f"student_no,name,grade,class_name\nS103,卫同学,初一,{class_name}\n",
        )
        assert data["valid_count"] == 0, class_name
        assert data["rows"][0]["errors"] == ["班级格式应为 701（首位 7/8/9 分别表示初一/初二/初三）"], class_name


def test_a_grade_outside_the_three_is_rejected(client):
    """年级列写「七年级」会新建一个年级行，所以只认 初一/初二/初三 三个名字。"""
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS104,蒋同学,七年级,701\n")

    assert data["valid_count"] == 0
    assert data["rows"][0]["errors"] == ["年级应为 初一 / 初二 / 初三"]


def test_the_missing_field_message_is_not_doubled(client):
    """年级整格没填时只说「缺少年级」，不再补一句「年级应为…」。

    两条错误说的是同一件事，会让学校以为有两处要改。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS105,沈同学,,701\n")

    assert data["rows"][0]["errors"] == ["缺少年级"]


# --------------------------------------------------------------------------
# 导入建出来的账号：默认密码 123456，首次登录强制改密
# --------------------------------------------------------------------------


def test_imported_student_gets_the_default_password_and_must_change_it(client):
    """导入的学生账号，密码就是 `123456`，而且 `must_change_password` 为 True。

    这两条都不是新行为（`student_import_service.commit_roster_import` 一直这么写），
    但此前**没有任何测试钉住它们**：把密码换成随机串、或把 `must_change_password`
    翻成 False，全套测试照样全绿。学校要的是「新学生拿学号 + 123456 先登得进去、
    进去之后被要求改密」，这条契约得有人守——它是导入唯一一处直接决定别人能否登录的地方。
    """
    headers = auth_headers(client, "admin", "admin")
    preview = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={
            "file": (
                "students.csv",
                "student_no,name,grade,class_name\nS805,周同学,初一,701\n",
                "text/csv",
            )
        },
    ).json()["data"]
    committed = client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": preview["batch_id"]},
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"]["created"] == 1

    # 学号即账号，默认密码 123456。角色单独传，因为登录要按角色查账号。
    logged_in = client.post(
        "/api/v1/auth/login",
        json={"role": "student", "account": "S805", "password": "123456"},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["data"]["user"]["must_change_password"] is True

    # 标志位是**落在账号行上**的，不是登录时才现置的：随后读 /auth/me 仍然要求改密
    student_headers = {"Authorization": f"Bearer {logged_in.json()['data']['access_token']}"}
    me = client.get("/api/v1/auth/me", headers=student_headers).json()["data"]
    assert me["must_change_password"] is True
    assert me["account_type"] == "STUDENT_NO"
    assert me["role_code"] == "student"

    # 反面：别的密码进不去。否则上面那两条在「密码校验根本没生效」时也会全绿。
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"role": "student", "account": "S805", "password": "123457"},
        ).status_code
        == 401
    )


# --------------------------------------------------------------------------
# 学号已在名册上 = 冲突，要操作员在「覆盖 / 放弃」里拍板（2026-09-17）
# --------------------------------------------------------------------------


def _commit(client, headers, batch_id: int, resolution: str | None = None):
    payload: dict[str, Any] = {"batch_id": batch_id}
    if resolution is not None:
        payload["resolution"] = resolution
    return client.post("/api/v1/student-roster/import/commit", headers=headers, json=payload)


def _counts(response) -> dict[str, int]:
    """提交响应里的三个数。

    单独一个函数，因为响应体上还有 `batch_id` / `batch_no` / `error_count`——
    直接断言整个 `data` 相等会把「这一批是哪一批」也钉进每一条用例里，
    而那些数在这一层不承载用例要说的事。
    """
    data = response.json()["data"]
    return {key: data[key] for key in ("created", "updated", "skipped")}


def test_a_roster_entry_already_holding_the_number_is_a_conflict_not_an_error(client):
    """同一行在改造前是「重复学号」错误，现在要能被覆盖或放弃。

    这条钉的是**分类**：它进了 `conflict_count` 而不是 `error_count`，
    并且 `rows[].conflicts` 里带着库里那一份的姓名——预览面板要写出
    「覆盖会把他从 X 改成 Y」，只说「冲突」等于没说。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS001,林同学改,初二,801\n")

    assert data["valid_count"] == 0
    assert data["conflict_count"] == 1
    assert data["error_count"] == 0
    row = data["rows"][0]
    assert row["errors"] == []
    assert row["conflicts"][0]["type"] == "STUDENT_NO_EXISTS"
    # 种子里的 S001 是「林同学」
    assert row["conflicts"][0]["existing_name"] == "林同学"


def test_a_duplicate_number_inside_one_file_stays_an_error(client):
    """反面：**文件内**两行都说是同一个学号，不给「覆盖」这个出路。

    先写的那行等着被后一行覆盖，还是反过来？这问的不是口径，是文件坏了。
    把它一起升级成冲突会造出一个没有正确答案的单选框。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client,
        headers,
        "student_no,name,grade,class_name\nS901,甲同学,初一,701\nS901,乙同学,初一,701\n",
    )

    assert data["valid_count"] == 1
    assert data["conflict_count"] == 0
    assert data["error_count"] == 1
    assert data["rows"][1]["errors"] == ["文件内重复学号"]
    assert data["rows"][1]["conflicts"] == []


def test_commit_refuses_before_writing_when_the_choice_was_not_made(client, db_session):
    """没选处置方式 → 422，**且一行都没写进去**。

    「写了一半才发现还差一个决定」正是这条要防的：冲突必须在任何写入之前算完
    （与测评导入的 `planned` 同一形状）。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client,
        headers,
        # S902 是新学生（会建），S001 是冲突（要拍板）
        "student_no,name,grade,class_name\nS902,新同学,初一,701\nS001,林同学改,初二,801\n",
    )
    assert data["conflict_count"] == 1
    assert data["valid_count"] == 1

    refused = _commit(client, headers, data["batch_id"])
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "覆盖或放弃" in refused.json()["error"]["message"]

    # 那一条**可以被导入**的记录也没有被写进去——422 在写入之前。
    assert db_session.scalar(select(Student).where(Student.student_no == "S902")) is None
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "导入学生")) is None


def test_overwrite_updates_the_roster_entry_in_place(client, db_session):
    """「覆盖」= 用文件里的信息更新这名学生，**不删了重建**。

    `student.id` 被关怀档案、测评会话、风险事件引用着，全库没有 `ondelete=`（§1）。
    删一次就是把一个学生一学期的记录全部悬空，而学校要的只是「名册上这个人的
    班级换了」。所以这条的核心断言是 **id 没变**——只断言姓名变了的话，
    「删了重建」也能全绿。
    """
    headers = auth_headers(client, "admin", "admin")
    before = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert before is not None
    # 取成普通整数：`before` 与 `after` 是**同一个对象**（同一个 session 的
    # identity map），`expire_all()` 之后连 `before` 一起刷新——拿它比较等于拿
    # 改完的值跟改完的值比，永远相等，那条断言就成了摆设。
    original_id, original_class_id = before.id, before.class_id

    data = _preview(
        client,
        headers,
        "student_no,name,grade,class_name,gender,age\nS001,林同学改,初二,801,女,14\n",
    )
    assert data["conflict_count"] == 1

    committed = _commit(client, headers, data["batch_id"], resolution="overwrite")
    assert committed.status_code == 200, committed.text
    assert _counts(committed) == {"created": 0, "updated": 1, "skipped": 0}

    db_session.expire_all()
    after = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    # 同一条行：id 不变，历史都还挂在上面。
    assert after.id == original_id
    assert after.name == "林同学改"
    # `masked_name` 是展示名而不是遮蔽（§1）：改名要跟着改，否则队列里印的是旧名字。
    assert after.masked_name == "林同学改"
    assert after.gender == "FEMALE"
    assert after.age == 14
    # 班真的换了（种子里的 S001 是初一/701，文件说初二/801）
    assert after.class_id != original_class_id

    # 账号是同一名学生在系统里的另一个「名字」，它也得跟着改。
    account = db_session.scalar(select(UserAccount).where(UserAccount.account == "S001"))
    assert account is not None
    assert account.display_name == "林同学改"

    # 没有第二条 S001 —— 覆盖不是「再建一个」。
    assert len(db_session.scalars(select(Student).where(Student.student_no == "S001")).all()) == 1


# 「覆盖只更新姓名、年级、班级、性别、年龄」这句承诺同时写在三处：服务里的
# `OVERWRITABLE_FIELDS`、`_overwrite_student` 的赋值列表、以及两个导入入口的界面文案。
# 前两处能互相对，第三处对不了（后端 import 不了 `.vue`），所以至少把前两处钉死。
COLUMN_ATTR = {
    "name": "name",
    "grade": "grade_id",
    "class_name": "class_id",
    "gender": "gender",
    "age": "age",
}


def test_overwrite_touches_exactly_the_columns_it_promises(client, db_session):
    """覆盖的**边界**：多改一列就会红。

    断言的是「实际变化的列 == 承诺的那些」，不是「姓名变了、班级变了」——后者只是把
    `_overwrite_student` 重述一遍，把学号也写回去它照样绿。会红的是这些改法：顺手改
    `student_no`、顺手把 `status` 置成别的、顺手清掉别的标记——每一种都是界面那句
    「不删除、不重建，只换姓名年级班级性别年龄」说了它没做的事。

    **它看不见「写了一个一模一样的值」**，这是取值比较的固有限制，不是漏写：把
    `status` 重新赋成 `ACTIVE`（S001 本来就是）在库里没有产生任何变化，因此也没有
    任何东西可断言。验证这条守卫时要挑一个真会改变取值的变异，否则会得到一次
    假的「守卫失效」。
    """
    # 常量用的是**文件里的列名**（年级 / 班级），模型上是 `grade_id` / `class_id`。
    # 这条断言让「往常量里加一个字段」必须同时回答「它落在模型的哪一列」。
    assert set(COLUMN_ATTR) == set(OVERWRITABLE_FIELDS)

    headers = auth_headers(client, "admin", "admin")
    student = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert student is not None

    # 快照**每一列**，只摘掉 `TimestampMixin` 的行簿记——`updated_at` 带
    # `onupdate=func.now()`，任何一次写入它都会变，与「承诺了哪几列」无关。
    BOOKKEEPING = {"created_at", "updated_at"}

    def snapshot() -> dict[str, Any]:
        return {
            attr.key: getattr(student, attr.key)
            for attr in Student.__mapper__.column_attrs
            if attr.key not in BOOKKEEPING
        }

    before = snapshot()
    data = _preview(
        client, headers, "student_no,name,grade,class_name,gender,age\nS001,林同学改,初二,801,女,14\n"
    )
    committed = _commit(client, headers, data["batch_id"], resolution="overwrite")
    assert committed.status_code == 200, committed.text
    db_session.expire_all()
    after = snapshot()

    changed = {key for key in before if before[key] != after[key]}
    # `masked_name` 是展示名不是遮蔽（§1），改名必须跟着改：它不算超出承诺，
    # 但也不是「不用管」——它正是队列里印出来的那个名字。
    promised = {COLUMN_ATTR[field] for field in OVERWRITABLE_FIELDS} | {"masked_name"}
    assert changed == promised

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "导入学生"))
    assert audit is not None
    assert "覆盖同名学号的学生" in audit.detail


def test_overwrite_does_not_blank_optional_columns_the_file_omits(client, db_session):
    """选填列文件里空着就**不写**，不是写 NULL。

    「这一列我没填」与「把名册上这个数抹掉」是两件事：四列的老模板根本没有
    性别/年龄列，若按「空就当没填过」处理，学校每次导一份老模板都会把名册上
    所有人的年龄擦掉，而且不报任何错——§1 里「年龄整列 NULL 与 '学校没填'
    长得一模一样」是同一个坑。
    """
    headers = auth_headers(client, "admin", "admin")
    # 先把 S001 的性别年龄填上
    seeded = _preview(
        client, headers, "student_no,name,grade,class_name,gender,age\nS001,林同学,初一,701,女,13\n"
    )
    assert _commit(client, headers, seeded["batch_id"], resolution="overwrite").status_code == 200

    # 再导一份只有四列的文件（改名换班，不带性别年龄）
    four_columns = _preview(
        client, headers, "student_no,name,grade,class_name\nS001,林同学,初二,801\n"
    )
    assert _commit(client, headers, four_columns["batch_id"], resolution="overwrite").status_code == 200

    db_session.expire_all()
    after = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert after.gender == "FEMALE"
    assert after.age == 13


def test_skip_leaves_the_conflicting_rows_alone_and_imports_the_rest(client, db_session):
    """「放弃」= 这几行不动，其余照导。反过来也要能看见：只报 created 的话，
    老师选完放弃看到「已导入 0 条」会以为整份文件白导了。"""
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client,
        headers,
        "student_no,name,grade,class_name\nS903,甲同学,初一,701\nS001,林同学改,初二,801\n",
    )

    committed = _commit(client, headers, data["batch_id"], resolution="skip")
    assert committed.status_code == 200, committed.text
    assert _counts(committed) == {"created": 1, "updated": 0, "skipped": 1}

    db_session.expire_all()
    # 冲突那一条原样不动
    untouched = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert untouched.name == "林同学"
    # 其余照导
    assert db_session.scalar(select(Student).where(Student.student_no == "S903")) is not None


def test_an_unknown_resolution_is_rejected(client):
    """`resolution` 是自由字符串进来（Pydantic 收的是 `str | None`），
    所以取值要在服务层挡：一个 typo 会被当成「选了覆盖」还是「没选」，
    取决于它恰好落在哪个分支——两边都是静默的错误行为。"""
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS001,林同学改,初二,801\n")

    response = _commit(client, headers, data["batch_id"], resolution="Overwrite")
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "处置方式应为覆盖（overwrite）或放弃（skip）"


def test_a_file_without_conflicts_commits_without_being_asked(client, db_session):
    """没有冲突的文件照旧一次提交，不必带 `resolution`。

    要求 99% 的正常导入都先回答一个不该问的问题，只会让人在第 20 行开始乱点。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS904,乙同学,初一,701\n")
    assert data["conflict_count"] == 0

    committed = _commit(client, headers, data["batch_id"])
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"]["created"] == 1

    # `detail` 读库而不是读接口：`GET /audit-logs` 的 payload 里没有这一列
    # （它只出 action / resource_type / resource_id / purpose / result）。
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "导入学生"))
    assert audit is not None
    # 没问过 ≠ 问了选放弃。审计里要分得开。
    assert "未涉及（无冲突）" in audit.detail


# --------------------------------------------------------------------------
# 名册导入批次化（V1.2 第 3 期）：上传留下批次与逐行明细
# --------------------------------------------------------------------------


def _batches(client, headers) -> dict:
    response = client.get("/api/v1/student-roster/import/batches", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_preview_leaves_a_batch_and_its_rows_behind(client, db_session):
    """预览产出的是**一批行**：一行批次 + 每一行一条明细。

    V1.0 里预览的结果只活在返回值里，操作员上传完被叫走、回来时那一页是空的，
    只能重传一次——而重传的那一份与刚才屏幕上那份是不是同一份，没有任何东西能
    回答。这条钉的是**这一批的内容**：批次行与逐行明细在会话里已经成型，坏行的
    结论（`ERROR`）在上传那一刻就定下来了。

    **它证明不了这批真的落了库。** 夹具把 `get_db` 覆盖成共享的 `db_session`，
    而它在请求结束后不关闭也不回滚，所以「写了但没提交」的行在这里照样查得到——
    这一条在真库 0 行时依然绿着。落库那一句由
    `test_the_preview_survives_the_request_that_made_it` 钉（它换掉那一层覆盖）。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client,
        headers,
        "student_no,name,grade,class_name\nS811,郑同学,初一,701\nS001,林同学改,初二,801\n,缺号,初一,701\n",
    )

    batch = db_session.get(StudentRosterImportBatch, data["batch_id"])
    assert batch is not None
    assert batch.status == "PREVIEW"
    assert batch.batch_no.startswith("ROSTER-")
    assert (batch.total_rows, batch.error_rows) == (3, 1)

    rows = db_session.scalars(
        select(StudentRosterImportRow)
        .where(StudentRosterImportRow.batch_id == batch.id)
        .order_by(StudentRosterImportRow.row_no)
    ).all()
    # `row_no` 从 **2** 数起：第 1 行是表头。报错说的「第 N 行」要能被老师拿去
    # 回 Excel 里数，所以这一列存的是**文件里的行号**，不是记录序号。
    assert [row.row_no for row in rows] == [2, 3, 4]
    assert rows[0].processing_status == "PENDING"
    assert rows[0].conflict_code is None
    # 冲突码只在**冲突**时有值——学号已在名册上（S001）。
    assert rows[1].conflict_code == "STUDENT_NO_EXISTS"
    # 坏单元格（缺学号）在上传那一刻就是 ERROR，它不会因为后面选了覆盖而改变。
    assert rows[2].processing_status == "ERROR"
    assert rows[2].message == "缺少学号"
    # 这一行还没落到任何学生身上：提交之前没人知道它会新建还是更新。
    assert all(row.student_id is None for row in rows)


def test_re_uploading_the_same_file_reuses_the_preview_batch(client, db_session):
    """同一个人把同一份文件再传一次，**不留下第二行 PREVIEW 批次**。

    学校改正一处错别字再导一次是常态。每传一次就多一批孤儿行的话，批次历史那一页
    的全部意义（按它找「哪一批真的导进去了」）就被淹掉了。判据是文件指纹 + 操作者。
    """
    headers = auth_headers(client, "admin", "admin")
    content = "student_no,name,grade,class_name\nS812,冯同学,初一,701\n"
    first = _preview(client, headers, content)
    second = _preview(client, headers, content)

    assert first["batch_id"] == second["batch_id"]
    assert (
        len(
            db_session.scalars(
                select(StudentRosterImportRow).where(
                    StudentRosterImportRow.batch_id == first["batch_id"]
                )
            ).all()
        )
        == 1
    )


def test_commit_marks_the_batch_committed_and_every_row_its_outcome(client, db_session):
    """提交之后，逐行明细说得出「这一行落成了什么」。

    这是这两张表存在的最后一个理由：导完之后回看，「为什么这个人没更新」的答案
    必须在行上——选「放弃」的那一行留着原因，成功的那两行不留（`message` 只在
    需要解释时有值）。而 `conflict_code` **留着**：撞上过就是撞上过，只是被处置掉了。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client,
        headers,
        "student_no,name,grade,class_name\nS813,陈同学,初一,701\nS001,林同学改,初二,801\n,缺号,初一,701\n",
    )
    committed = _commit(client, headers, data["batch_id"], resolution="skip")
    assert committed.status_code == 200, committed.text

    db_session.expire_all()
    batch = db_session.get(StudentRosterImportBatch, data["batch_id"])
    assert batch.status == "COMMITTED"
    assert (batch.created_rows, batch.updated_rows, batch.skipped_rows, batch.error_rows) == (1, 0, 1, 1)

    rows = db_session.scalars(
        select(StudentRosterImportRow)
        .where(StudentRosterImportRow.batch_id == batch.id)
        .order_by(StudentRosterImportRow.row_no)
    ).all()
    created, skipped, errored = rows
    assert created.processing_status == "CREATED"
    assert created.message is None
    assert created.student_id is not None
    assert skipped.processing_status == "SKIPPED"
    assert "已在名册上" in skipped.message
    assert skipped.conflict_code == "STUDENT_NO_EXISTS"
    assert skipped.student_id is None
    assert errored.processing_status == "ERROR"
    assert errored.message == "缺少学号"


def test_a_committed_batch_cannot_be_committed_again(client, db_session):
    """同一批点两次「确认导入」→ 422，而不是把整份名册写第二遍。

    第二次遇到的每一行都会变成冲突，「覆盖」时它会静默地再覆盖一遍——看起来像是
    成功了，而屏幕上没有任何东西说明刚才那一下又动了一次名册。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS814,褚同学,初一,701\n")
    assert _commit(client, headers, data["batch_id"]).status_code == 200

    again = _commit(client, headers, data["batch_id"])
    assert again.status_code == 422
    assert data["batch_no"] in again.json()["error"]["message"]
    assert db_session.scalar(select(Student).where(Student.student_no == "S814")) is not None


def test_the_audit_line_names_the_batch(client, db_session):
    """审计的 `detail` 带批次号。

    名册导入是反复发生的动作（每学期一次普查、转学插班），而审计页搜索匹配的正是
    `action` 与这几个字段。不记批次号时「去年那批初一的名册是谁导的」只能靠时间猜，
    而同一分钟里可能有两批。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS815,卫同学,初一,701\n")
    assert _commit(client, headers, data["batch_id"]).status_code == 200

    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "导入学生"))
    assert audit is not None
    assert f"batch={data['batch_no']}" in audit.detail


def test_overwriting_an_age_leaves_a_change_log(client, db_session):
    """覆盖年龄时 `student_age_change_log` 留一行，并且审计也留下。

    两处不是重复：审计用动作码 `更新学生年龄`（与测评导入那条链路共用），
    回答「谁在什么时候动了这个数」；变更记录是**结构化的那一份**，把「名册导入
    批次 N 第 M 行」记成外键，回答审计答不出的「这一列被改过几次」。

    先导一次把年龄定成 13，再导一次改成 14——那样这一行必然是从 13 到 14，
    与种子里那个学生原本是多少岁无关。
    """
    headers = auth_headers(client, "admin", "admin")
    first = _preview(
        client, headers, "student_no,name,grade,class_name,gender,age\nS001,林同学,初一,701,女,13\n"
    )
    assert _commit(client, headers, first["batch_id"], resolution="overwrite").status_code == 200
    # 第一次那一行不留记录：名册上原来是多少岁不该由这次导入来断言。
    assert db_session.scalar(select(StudentAgeChangeLog)) is None

    second = _preview(
        client, headers, "student_no,name,grade,class_name,gender,age\nS001,林同学,初一,701,女,14\n"
    )
    assert _commit(client, headers, second["batch_id"], resolution="overwrite").status_code == 200

    db_session.expire_all()
    student = db_session.scalar(select(Student).where(Student.student_no == "S001"))
    assert student.age == 14
    log = db_session.scalar(select(StudentAgeChangeLog))
    assert log is not None
    assert (log.old_age, log.new_age) == (13, 14)
    assert log.student_id == student.id
    assert log.roster_import_batch_id == second["batch_id"]
    # `reason` 存**中文原因**（与审计的 `detail` 同一口径）：读它的人正是要判断
    # 「这次导入该不该改这一列」的人，写 `ROSTER_IMPORT` 等于没写。
    assert "名册导入覆盖年龄" in log.reason
    assert second["batch_no"] in log.reason

    age_audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "更新学生年龄"))
    assert age_audit is not None
    assert "名册 13 → 文件 14" in age_audit.detail


def test_the_batch_history_and_the_row_detail_are_readable(client, db_session):
    """批次历史与逐行明细各有一个读者。

    不给这两张表读者等于没落库：导完之后，「这一行是谁导进来的、为什么没落上」
    在界面上就没有任何落点。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(
        client, headers, "student_no,name,grade,class_name\nS816,蒋同学,初一,701\n,缺号,初一,701\n"
    )

    history = _batches(client, headers)
    entry = next(item for item in history["items"] if item["id"] == data["batch_id"])
    assert entry["batch_no"] == data["batch_no"]
    assert entry["status"] == "PREVIEW"
    assert (entry["total_rows"], entry["error_rows"]) == (2, 1)
    # 「凡是截断，都要自己说出来」（§10）：界面据此写「另有 N 批未显示」。
    assert history["total"] >= len(history["items"])
    assert isinstance(history["truncated"], bool)

    detail = client.get(
        f"/api/v1/student-roster/import/batches/{data['batch_id']}/rows", headers=headers
    )
    assert detail.status_code == 200, detail.text
    items = detail.json()["data"]["items"]
    assert [item["row_no"] for item in items] == [2, 3]
    assert items[0]["processing_status"] == "PENDING"
    # 性别是**编码**（`MALE` / `FEMALE`），中文由 `labels.ts` 那一层现算。
    assert items[1]["message"] == "缺少学号"


def test_the_row_detail_of_an_unknown_batch_is_a_404_not_an_empty_list(client):
    """没有这一批时 404，不是空列表。

    空列表会让界面说「这一批没有明细」，而实话是「没有这一批」——§14 那条
    「空态是一句关于数据的话」在这里的反面。
    """
    headers = auth_headers(client, "admin", "admin")
    response = client.get("/api/v1/student-roster/import/batches/99999999/rows", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_batch_from_another_school_is_not_readable_nor_committable(client, db_session):
    """`batch_id` 是客户端传来的，所以它本身不能成为凭据（§9）。

    三个入口（读明细 / 读历史 / 提交）都要按学校挡一次。今天名册导入写死单校
    （缺口 2），所以**这一条在正常链路上永远走不到**——正因为如此才要专门造一行
    别的学校的批次来钉它：等哪天多校了，漏掉这道检查不会以「报错」的形式出现，
    而会以「A 校管理员把 B 校的名册导进了 B 校」的形式出现，界面上一切正常。

    回 **404 而不是 403**，与「批次不存在」**同一句话**：一句「这一批属于别的学校」
    等于替客户端确认了那个 id 存在。
    """
    other = School(code="YH", name="云海中学")
    db_session.add(other)
    db_session.flush()
    foreign = StudentRosterImportBatch(
        batch_no="ROSTER-OTHER-1",
        school_id=other.id,
        file_name="别的学校的名册.csv",
        file_sha256="0" * 64,
        imported_by=db_session.scalar(select(UserAccount.id).where(UserAccount.account == "admin")),
        status="PREVIEW",
        total_rows=1,
    )
    db_session.add(foreign)
    db_session.flush()

    headers = auth_headers(client, "admin", "admin")
    rows = client.get(
        f"/api/v1/student-roster/import/batches/{foreign.id}/rows", headers=headers
    )
    assert rows.status_code == 404
    assert rows.json()["error"]["code"] == "NOT_FOUND"

    commit = client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": foreign.id},
    )
    assert commit.status_code == 404
    assert commit.json()["error"]["code"] == "NOT_FOUND"
    # 「被拒的读取不写审计」（§9）与写入侧同此：这一行不该出现在历史里。
    history = _batches(client, headers)
    assert all(item["id"] != foreign.id for item in history["items"])


def test_the_preview_survives_the_request_that_made_it(seeded_engine):
    """预览必须**真的落库**——而这一条只有本用例看得见。

    `get_db` 只管开与关、从不提交（`db/session.py` 的 `finally: db.close()`），
    所以预览路由里那句 `db.commit()` 是整条链路的前提：客户端拿到的 `batch_id`
    要能被下一次提交查回来。但 `client` 夹具把 `get_db` 覆盖成那个共享的
    `db_session`，而它在请求结束后**不关闭也不回滚**——于是「写了但没提交」的行
    对测试照样查得到，整个后端套件在这一类洞上是全绿的。这不是假设：V1.2 第 3 期
    的预览正好少过这一句，`test_a_preview_leaves_a_batch_and_its_rows_behind`
    在真库里明明 0 行的情况下一直绿着。

    所以这里换掉那一层覆盖：请求走一个**与生产逐字同形**的会话（用完就关），
    请求之后再另开一个会话去查——没有提交的行在 `close()` 那一刻就没了。

    走的是**路由函数本身**而不是 `TestClient`：要断言的就是那个函数体里有没有
    提交，而绕过的是依赖注入那一层（那里没有逻辑），换来的是不必为了拿一个 token
    而往这个共享库里真写一行登录会话。
    """
    csv_content = "student_no,name,grade,class_name\nS900,快照同学,初一,701\n"

    with Session(seeded_engine) as db:
        admin = db.scalar(select(UserAccount).where(UserAccount.account == "admin"))
        body = asyncio.run(
            roster_import_preview(
                current_user=admin,
                db=db,
                file=UploadFile(
                    io.BytesIO(csv_content.encode("utf-8")), filename="roster.csv"
                ),
            )
        )
    batch_id = body["data"]["batch_id"]

    # 另一条连接——也就是另一个会话——去看它在不在。
    with Session(seeded_engine) as db:
        batch = db.get(StudentRosterImportBatch, batch_id)
        assert batch is not None, "预览那一批没有落库：路由少了 db.commit()"
        assert batch.status == "PREVIEW"
        rows = db.scalars(
            select(StudentRosterImportRow).where(
                StudentRosterImportRow.batch_id == batch_id
            )
        ).all()
        assert len(rows) == 1, "逐行明细没有落库：路由少了 db.commit()"

        # 用完清掉。这个库是 session 级共享的，留着这批会让别的用例看到一份
        # 它没造过的导入历史（那正是「不为断言去改共享数据」的反面）。
        db.execute(
            delete(StudentRosterImportRow).where(
                StudentRosterImportRow.batch_id == batch_id
            )
        )
        db.delete(batch)
        db.commit()
