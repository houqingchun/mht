"""账号的创建与维护（`POST/PATCH /admin/accounts`）。

这一组的重点是**新账号的数据范围真的生效**，不是「接口返回 200」。
`security/data_scope.student_scope_predicate` 在「用户没有范围行」时返回恒假，
所以一个漏写范围的新账号登录得进来、界面也打得开，只是**任何学生都看不到**。
那种坏账号在接口层看起来完全正常，只有拿它去读一次名册才暴露。

第二个重点是「停用」而不是「删除」：账号没有删除接口，停用必须**立即**生效
——`get_current_user` 每次请求都查 `active`，所以它不等到 token 过期。
"""

from sqlalchemy import select

from app.models.account import UserAccount, UserScope
from app.models.audit import AuditLog
from app.models.enums import RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.tests.conftest import auth_headers

ADMIN = ("admin", "admin")


def _admin_headers(client):
    return auth_headers(client, *ADMIN)


def _add_class(db, name: str, grade_id: int | None = None) -> ClassGroup:
    """在种子那所学校里加一个班。默认挂在种子那个年级下。"""
    seed_class = db.scalar(select(ClassGroup).where(ClassGroup.name == "1班"))
    class_group = ClassGroup(
        school_id=seed_class.school_id,
        grade_id=grade_id if grade_id is not None else seed_class.grade_id,
        name=name,
    )
    db.add(class_group)
    db.flush()
    return class_group


def _add_grade(db, name: str) -> Grade:
    seed_grade = db.scalar(select(Grade).where(Grade.name == "初一"))
    grade = Grade(school_id=seed_grade.school_id, name=name, sort_order=seed_grade.sort_order + 1)
    db.add(grade)
    db.flush()
    return grade


def _add_student(db, class_group: ClassGroup, student_no: str, name: str) -> Student:
    seed_student = db.scalar(select(Student).where(Student.student_no == "S001"))
    student = Student(
        student_no=student_no,
        name=name,
        masked_name=name,
        school_id=seed_student.school_id,
        grade_id=class_group.grade_id,
        class_id=class_group.id,
    )
    db.add(student)
    db.flush()
    return student


def _create(client, headers, *, role="counselor", account, name="王老师", scopes=None, password="abc123"):
    return client.post(
        "/api/v1/admin/accounts",
        headers=headers,
        json={
            "role_code": role,
            "display_name": name,
            "account": account,
            "temporary_password": password,
            "scopes": scopes if scopes is not None else [{"scope_type": "SCHOOL", "scope_id": 1}],
        },
    )


def _student_nos(client, role: str, account: str, password: str = "abc123") -> set[str]:
    headers = auth_headers(client, role, account, password)
    return {row["student_no"] for row in client.get("/api/v1/students", headers=headers).json()["data"]["items"]}


def test_admin_creates_a_counselor_who_can_log_in(client, db_session):
    headers = _admin_headers(client)
    response = _create(client, headers, account="13900000009", name="王老师")
    assert response.status_code == 200
    created = response.json()["data"]
    assert created["role_code"] == "counselor"
    assert created["account_type"] == "MOBILE"
    # 操作员给的临时密码，所以第一次登录必须自己改（与「重置密码」同一约定）。
    assert created["must_change_password"] is True
    assert created["active"] is True

    login = client.post(
        "/api/v1/auth/login",
        json={"role": "counselor", "account": "13900000009", "password": "abc123"},
    )
    assert login.status_code == 200

    # 范围是一个可读的名称，不是一个裸 id：账号表那一列要能直接渲染。
    assert created["scopes"] == [
        {
            "scope_type": "SCHOOL",
            "school_id": 1,
            "grade_id": None,
            "class_id": None,
            "student_id": None,
            "name": "青禾实验学校",
        }
    ]
    # 范围行真的落库了，而不是只在响应里。
    assert db_session.scalar(select(UserScope).where(UserScope.user_id == created["id"])) is not None


def test_the_new_account_only_sees_the_students_in_its_range(client, db_session):
    """这是这一组里最重要的一条：范围写在账号上，且真的收窄了查询。

    夹具要有**两个班**，否则「只能看到自己班的」在没有第二个班时也成立。
    """
    class_a = _add_class(db_session, "701")
    class_b = _add_class(db_session, "702")
    _add_student(db_session, class_a, "S701", "甲同学")
    _add_student(db_session, class_b, "S702", "乙同学")
    db_session.commit()

    headers = _admin_headers(client)
    response = _create(
        client,
        headers,
        account="13900000010",
        scopes=[{"scope_type": "CLASS", "scope_id": class_a.id}],
    )
    assert response.status_code == 200
    scope = db_session.scalar(
        select(UserScope).where(UserScope.user_id == response.json()["data"]["id"])
    )
    # 班级范围也要把 school_id / grade_id 填上（信息，不是判据）。
    assert scope.scope_type == ScopeType.CLASS
    assert scope.class_id == class_a.id
    assert scope.grade_id == class_a.grade_id
    assert scope.school_id == class_a.school_id

    assert _student_nos(client, "counselor", "13900000010") == {"S701"}


def test_a_grade_scope_covers_every_class_in_that_grade(client, db_session):
    class_a = _add_class(db_session, "801")
    class_b = _add_class(db_session, "802")
    # 「别的年级」必须是**真的另一个年级**：同一个年级下的班只证明得了班级范围。
    other_grade = _add_grade(db_session, "初二")
    other = _add_class(db_session, "901", grade_id=other_grade.id)
    _add_student(db_session, class_a, "S801", "丙同学")
    _add_student(db_session, class_b, "S802", "丁同学")
    _add_student(db_session, other, "S901", "戊同学")
    db_session.commit()

    response = _create(
        client,
        _admin_headers(client),
        account="13900000011",
        scopes=[{"scope_type": "GRADE", "scope_id": class_a.grade_id}],
    )
    assert response.status_code == 200

    # 种子那个年级下还有 1班 / S001，所以「按年级」比「按班级」范围大是这必然的结果。
    assert _student_nos(client, "counselor", "13900000011") == {"S001", "S801", "S802"}


def test_a_leader_account_is_created_in_the_same_namespace_as_a_counselor(client):
    """心理老师与德育领导都用 `MOBILE`，`role_code` 分开。

    登录是按「账号 + 账号类型 + 角色」三样一起匹配的，所以这个账号只能从「德育领导」
    那个页签进来——这也正是为什么同一串手机号不能在两个角色下各有一个账号
    （唯一约束是 `(account, account_type)`）。
    """
    response = _create(
        client, _admin_headers(client), role="leader", name="赵领导", account="13900000023"
    )
    assert response.status_code == 200
    assert response.json()["data"]["account_type"] == "MOBILE"
    assert response.json()["data"]["role_code"] == "leader"

    assert (
        client.post(
            "/api/v1/auth/login",
            json={"role": "leader", "account": "13900000023", "password": "abc123"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"role": "counselor", "account": "13900000023", "password": "abc123"},
        ).status_code
        == 401
    )


def test_an_admin_account_uses_the_admin_username_namespace(client, db_session):
    headers = _admin_headers(client)
    response = _create(client, headers, role="admin", account="admin2", name="第二管理员")
    assert response.status_code == 200
    assert response.json()["data"]["account_type"] == "ADMIN_USERNAME"

    login = client.post(
        "/api/v1/auth/login", json={"role": "admin", "account": "admin2", "password": "abc123"}
    )
    assert login.status_code == 200
    # 学生登录页那四个页签各自匹配自己的命名空间，所以同一个字符串在别处登不上来。
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"role": "counselor", "account": "admin2", "password": "abc123"},
        ).status_code
        == 401
    )


def test_accounts_are_created_without_a_password_in_the_audit_trail(client, db_session):
    headers = _admin_headers(client)
    created = _create(client, headers, account="13900000012").json()["data"]

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "新建账号", AuditLog.resource_id == str(created["id"])
        )
    )
    assert audit is not None
    assert audit.actor_user_id is not None
    assert "SCHOOL=1" in audit.detail
    assert "role=counselor" in audit.detail
    assert "abc123" not in audit.detail


def test_student_accounts_cannot_be_created_here(client):
    """学生账号跟着名册一起生成。这条拒绝要说清去哪儿建，否则操作员只会觉得是 bug。"""
    response = _create(client, _admin_headers(client), role="student", account="13900000013")
    assert response.status_code == 422
    assert "名册" in response.json()["error"]["message"]


def test_the_account_shape_is_validated_against_the_login_page_promise(client):
    headers = _admin_headers(client)
    short_phone = _create(client, headers, account="139")
    assert short_phone.status_code == 422
    assert "11 位手机号" in short_phone.json()["error"]["message"]

    short_name = _create(client, headers, role="admin", account="ab")
    assert short_name.status_code == 422
    assert "管理员账号" in short_name.json()["error"]["message"]


def test_the_same_account_number_cannot_be_reused(client):
    headers = _admin_headers(client)
    assert _create(client, headers, account="13900000014").status_code == 200
    duplicate = _create(client, headers, account="13900000014", name="另一个人")
    assert duplicate.status_code == 422
    assert "13900000014" in duplicate.json()["error"]["message"]


def test_an_unknown_scope_id_leaves_no_half_written_account(client, db_session):
    """范围先解析再落账号：范围填错不该留下一个没有范围的半成品。"""
    response = _create(
        client, _admin_headers(client), account="13900000015", scopes=[{"scope_type": "GRADE", "scope_id": 9999}]
    )
    assert response.status_code == 422
    assert db_session.scalar(
        select(UserAccount).where(UserAccount.account == "13900000015")
    ) is None


def test_a_student_scope_is_not_an_option_for_staff(client):
    response = _create(
        client, _admin_headers(client), account="13900000016", scopes=[{"scope_type": "STUDENT", "scope_id": 1}]
    )
    assert response.status_code == 422


def test_only_the_account_manager_can_create_or_edit(client, db_session):
    for role, account in (("counselor", "13800000001"), ("leader", "13800000002"), ("student", "S001")):
        headers = auth_headers(client, role, account)
        assert _create(client, headers, account="13900000017").status_code == 403
        assert (
            client.patch("/api/v1/admin/accounts/1", headers=headers, json={"active": False}).status_code
            == 403
        )
    assert (
        client.get("/api/v1/admin/accounts/scope-options", headers=auth_headers(client, "counselor", "13800000001")).status_code
        == 403
    )


def test_scope_options_carry_the_name_and_the_id_of_every_range(client):
    response = client.get("/api/v1/admin/accounts/scope-options", headers=_admin_headers(client))
    assert response.status_code == 200
    options = response.json()["data"]["items"]
    assert {"scope_type": "SCHOOL", "scope_id": 1, "name": "青禾实验学校"} in options
    assert {"scope_type": "GRADE", "scope_id": 1, "name": "初一"} in options
    assert {"scope_type": "CLASS", "scope_id": 1, "name": "1班"} in options


def test_the_scope_name_follows_the_configured_school_name(client, db_session):
    """校名只有一个显示口径：配置里的那一个。

    用户报的：管理员在「系统配置」里把校名改成「第十三中学」，而新建账号的
    **数据范围**那一格还写着「青禾实验学校」——它读的是 `school.name`（组织表里
    种子建的那一行，界面上从来没显示过），而不是登录页与顶栏读的
    `system_setting.org.school_name`。同一个屏幕上两个校名，用户没法判断哪个算数。

    这条钉住**两个**读校名的地方（范围选项 + 账号列表的范围列）都跟着配置走。
    变异：把 `_school_display_names` 改回读 `school.name` → 两条断言都红。
    """
    headers = _admin_headers(client)
    assert (
        client.put(
            "/api/v1/admin/settings/org", headers=headers, json={"values": {"school_name": "第十三中学"}}
        ).status_code
        == 200
    )

    options = client.get("/api/v1/admin/accounts/scope-options", headers=headers).json()["data"]["items"]
    school_options = [item for item in options if item["scope_type"] == "SCHOOL"]
    assert school_options == [{"scope_type": "SCHOOL", "scope_id": 1, "name": "第十三中学"}]

    # 账号列表上的范围列走 `resolve_scope_names`，是这一格的另一半。
    accounts = client.get("/api/v1/admin/accounts", headers=headers).json()["data"]["items"]
    admin = next(row for row in accounts if row["account"] == "admin")
    assert [scope["name"] for scope in admin["scopes"]] == ["第十三中学"]

    # 改的是**显示名**，不是组织表里那一行：`school.name` 由种子/导入维护，
    # 界面上没有任何地方编辑它。把配置值写回去等于让一个只读列去追一个可写配置。
    assert db_session.scalar(select(School.name).where(School.id == 1)) == "青禾实验学校"


def test_editing_replaces_the_range_and_the_new_range_takes_effect(client, db_session):
    class_a = _add_class(db_session, "703")
    class_b = _add_class(db_session, "704")
    _add_student(db_session, class_a, "S703", "己同学")
    _add_student(db_session, class_b, "S704", "庚同学")
    db_session.commit()

    headers = _admin_headers(client)
    created = _create(
        client, headers, account="13900000018", name="钱老师",
        scopes=[{"scope_type": "CLASS", "scope_id": class_a.id}],
    ).json()["data"]
    assert _student_nos(client, "counselor", "13900000018") == {"S703"}

    patched = client.patch(
        f"/api/v1/admin/accounts/{created['id']}",
        headers=headers,
        json={"scopes": [{"scope_type": "CLASS", "scope_id": class_b.id}]},
    )
    assert patched.status_code == 200
    # 替换而不是追加：留下旧的那一条会让「改了范围」变成「范围变大了」。
    rows = db_session.scalars(select(UserScope).where(UserScope.user_id == created["id"])).all()
    assert len(rows) == 1
    assert rows[0].class_id == class_b.id
    assert _student_nos(client, "counselor", "13900000018") == {"S704"}

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "更新账号", AuditLog.resource_id == str(created["id"])
        )
    )
    assert audit is not None and "CLASS=" in audit.detail


def test_renaming_leaves_the_other_scope_rows_alone(client, db_session):
    """只改姓名时不许动范围。

    一个账号可以有多条范围行（谓词用 `or_` 合并，§9），而界面上的范围下拉框
    一次只能表达一条。所以「只改名字」这一趟必须原样保留其余的——否则改个错别字
    就把一位老师从两个班缩成一个班，而且没有任何提示。
    """
    headers = _admin_headers(client)
    created = _create(client, headers, account="13900000019", name="孙老师").json()["data"]
    class_b = _add_class(db_session, "705")
    db_session.add(
        UserScope(
            user_id=created["id"],
            scope_type=ScopeType.CLASS,
            school_id=class_b.school_id,
            grade_id=class_b.grade_id,
            class_id=class_b.id,
        )
    )
    db_session.commit()
    assert len(db_session.scalars(select(UserScope).where(UserScope.user_id == created["id"])).all()) == 2

    patched = client.patch(
        f"/api/v1/admin/accounts/{created['id']}", headers=headers, json={"display_name": "孙主任"}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["display_name"] == "孙主任"
    rows = db_session.scalars(select(UserScope).where(UserScope.user_id == created["id"])).all()
    assert {row.scope_type for row in rows} == {ScopeType.SCHOOL, ScopeType.CLASS}
    # 没动过的字段不进审计：审计里只有一次没有发生的修改，比没有审计更糟。
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "更新账号", AuditLog.resource_id == str(created["id"])
        )
    )
    assert audit.detail.startswith("display_name")


def test_sending_the_same_scope_back_does_not_log_a_change(client, db_session):
    """接口收得下「原样发回来」，而那种请求不该在审计里留下一次没有发生的修改。"""
    headers = _admin_headers(client)
    created = _create(client, headers, account="13900000024").json()["data"]
    same = [{"scope_type": "SCHOOL", "scope_id": 1}]

    # 行没有被删了重建：`user_scope` 的 id 不变说明「没动过」是字面意义上的没动过。
    #
    # 这一条只能**在空操作这一侧**断言（同一个 id 前后相等）。反过来断言
    # 「真改范围之后 id 变了」是错的：删掉 max(id) 那一行再插入，SQLite 会把 rowid
    # **复用**回来（没有 AUTOINCREMENT 时取 max+1），于是那条断言在内存 sqlite 上恒假，
    # 而它描述的行为 MySQL 的 InnoDB 反而不存在（自增计数不随删除回退）。
    # 「真改范围是替换而不是追加」由
    # `test_editing_replaces_the_range_and_the_new_range_takes_effect` 用行数与
    # class_id 钉住——那两条断言在两种库上同义。
    original = db_session.scalar(select(UserScope.id).where(UserScope.user_id == created["id"]))

    assert (
        client.patch(
            f"/api/v1/admin/accounts/{created['id']}", headers=headers, json={"scopes": same}
        ).status_code
        == 200
    )
    assert db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "更新账号", AuditLog.resource_id == str(created["id"])
        )
    ) is None
    assert db_session.scalar(select(UserScope.id).where(UserScope.user_id == created["id"])) == original


def test_deactivating_takes_effect_on_the_very_next_request(client, db_session):
    """停用不是删除，也不是「等他下次登录」。

    `get_current_user` 每次请求都查 `active`，所以那个账号手里已经拿到的 token
    下一个请求就被拒。这条把两件事分开：登不进来（401）与**看不下去**（401）。
    """
    headers = _admin_headers(client)
    created = _create(client, headers, account="13900000020").json()["data"]
    live = auth_headers(client, "counselor", "13900000020", "abc123")
    assert client.get("/api/v1/auth/me", headers=live).status_code == 200

    assert (
        client.patch(
            f"/api/v1/admin/accounts/{created['id']}", headers=headers, json={"active": False}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me", headers=live).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"role": "counselor", "account": "13900000020", "password": "abc123"},
        ).status_code
        == 401
    )
    assert db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "停用账号", AuditLog.resource_id == str(created["id"])
        )
    ) is not None

    client.patch(f"/api/v1/admin/accounts/{created['id']}", headers=headers, json={"active": True})
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"role": "counselor", "account": "13900000020", "password": "abc123"},
        ).status_code
        == 200
    )
    assert db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "启用账号", AuditLog.resource_id == str(created["id"])
        )
    ) is not None


def test_the_admin_cannot_deactivate_their_own_account(client):
    """自锁防护，与权限矩阵那条同源。"""
    headers = _admin_headers(client)
    own_id = client.get("/api/v1/auth/me", headers=headers).json()["data"]["id"]
    response = client.patch(
        f"/api/v1/admin/accounts/{own_id}", headers=headers, json={"active": False}
    )
    assert response.status_code == 422
    assert "自己" in response.json()["error"]["message"]
    # 别人的账号不受这条影响——否则「停用」这个动作整个做不了。
    other = _create(client, headers, account="13900000022").json()["data"]
    assert (
        client.patch(
            f"/api/v1/admin/accounts/{other['id']}", headers=headers, json={"active": False}
        ).status_code
        == 200
    )


def test_the_account_list_shows_the_role_and_the_range_of_every_row(client):
    headers = _admin_headers(client)
    _create(client, headers, account="13900000021", name="周老师")
    items = client.get("/api/v1/admin/accounts", headers=headers).json()["data"]["items"]
    by_account = {item["account"]: item for item in items}
    # 四个种子账号的姓名就是角色名（`seed.py` 的性质，不是这一列没接上）。
    assert by_account["13800000001"]["display_name"] == "心理老师"
    assert by_account["13800000001"]["scopes"][0]["name"] == "青禾实验学校"
    assert by_account["13900000021"]["role_code"] == RoleCode.COUNSELOR.value
