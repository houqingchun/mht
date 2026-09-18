from typing import Any

from sqlalchemy import select

from app.models.account import UserAccount
from app.models.audit import AuditLog
from app.models.organization import Student
from app.services.student_import_service import OVERWRITABLE_FIELDS
from app.tests.conftest import auth_headers


def test_admin_can_preview_and_commit_student_csv_import(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name\nS002,王同学,初一,702\n,缺号,初一,701\nS001,重复,初一,701\n"
    preview = client.post(
        "/api/v1/students/import/preview",
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
    assert data["preview_token"]

    commit = client.post(
        "/api/v1/students/import/commit",
        headers=headers,
        json={"preview_token": data["preview_token"], "resolution": "skip"},
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
        "/api/v1/students/import/preview",
        headers=headers,
        files={"file": ("students.json", payload, "application/json")},
    )
    assert response.status_code == 200
    assert response.json()["data"]["valid_count"] == 1


def test_non_admin_cannot_import_students(client):
    headers = auth_headers(client, "counselor", "13800000001")
    response = client.post(
        "/api/v1/students/import/preview",
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
        "/api/v1/students/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]


def test_class_code_must_match_its_grade(client):
    """「初二,701」必须报错。

    这是选择保留年级列的全部理由：`commit_student_import` 只会拿字符串去
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

    这两条都不是新行为（`student_import_service.commit_student_import` 一直这么写），
    但此前**没有任何测试钉住它们**：把密码换成随机串、或把 `must_change_password`
    翻成 False，全套测试照样全绿。学校要的是「新学生拿学号 + 123456 先登得进去、
    进去之后被要求改密」，这条契约得有人守——它是导入唯一一处直接决定别人能否登录的地方。
    """
    headers = auth_headers(client, "admin", "admin")
    preview = client.post(
        "/api/v1/students/import/preview",
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
        "/api/v1/students/import/commit",
        headers=headers,
        json={"preview_token": preview["preview_token"]},
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


def _commit(client, headers, token: str, resolution: str | None = None):
    payload: dict[str, str] = {"preview_token": token}
    if resolution is not None:
        payload["resolution"] = resolution
    return client.post("/api/v1/students/import/commit", headers=headers, json=payload)


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

    refused = _commit(client, headers, data["preview_token"])
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

    committed = _commit(client, headers, data["preview_token"], resolution="overwrite")
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"] == {"created": 0, "updated": 1, "skipped": 0}

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
    committed = _commit(client, headers, data["preview_token"], resolution="overwrite")
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
    assert _commit(client, headers, seeded["preview_token"], resolution="overwrite").status_code == 200

    # 再导一份只有四列的文件（改名换班，不带性别年龄）
    four_columns = _preview(
        client, headers, "student_no,name,grade,class_name\nS001,林同学,初二,801\n"
    )
    assert _commit(client, headers, four_columns["preview_token"], resolution="overwrite").status_code == 200

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

    committed = _commit(client, headers, data["preview_token"], resolution="skip")
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"] == {"created": 1, "updated": 0, "skipped": 1}

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

    response = _commit(client, headers, data["preview_token"], resolution="Overwrite")
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "处置方式应为覆盖（overwrite）或放弃（skip）"


def test_a_file_without_conflicts_commits_without_being_asked(client, db_session):
    """没有冲突的文件照旧一次提交，不必带 `resolution`。

    要求 99% 的正常导入都先回答一个不该问的问题，只会让人在第 20 行开始乱点。
    """
    headers = auth_headers(client, "admin", "admin")
    data = _preview(client, headers, "student_no,name,grade,class_name\nS904,乙同学,初一,701\n")
    assert data["conflict_count"] == 0

    committed = _commit(client, headers, data["preview_token"])
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"]["created"] == 1

    # `detail` 读库而不是读接口：`GET /audit-logs` 的 payload 里没有这一列
    # （它只出 action / resource_type / resource_id / purpose / result）。
    audit = db_session.scalar(select(AuditLog).where(AuditLog.action == "导入学生"))
    assert audit is not None
    # 没问过 ≠ 问了选放弃。审计里要分得开。
    assert "未涉及（无冲突）" in audit.detail
