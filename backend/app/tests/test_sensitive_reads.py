"""§17(c) 里三条**原先没有守卫**的验收（阶段 8 收口时补的）。

那句话是：「管理员重置密码后旧 Token 全部失效、撤销会话无法访问敏感接口、查看原始答卷
前必须填写查看原因、审计详情不包含原始答卷和家庭回访正文、系统管理员没有心理数据查看
权限」。五条里已有三条各住在别处：

| 验收 | 守卫在哪 |
|---|---|
| 管理员重置密码后旧 Token 全部失效 | `test_auth_sessions.py::test_resetting_a_password_revokes_every_session_including_the_one_in_use` |
| 撤销会话无法访问敏感接口 | `test_auth_sessions.py::test_logging_out_kills_the_token_immediately` |
| 系统管理员没有心理数据查看权限 | `test_permissions.py::test_defaults_are_the_documented_ones`（`:52` 那一行直接断言 `resolve_scope(ADMIN, STUDENT_PSYCH_DETAIL) == NONE`） |

**剩下这两条一直只有代码、没有用例**，所以这一期把它们的判据写下来：

- 「查看重点题必须填写查看原因」——`students.py` 里那句 `if not purpose.strip()` 与它
  下面 `ensure_student_in_scope` 的次序（先判原因、再判范围、最后才写审计）此前只被
  「范围外」那一半钉住（`test_data_scope.py::test_key_questions_rejects_out_of_scope_student`），
  **原因那一半没有**。
- 「审计详情不包含完整答案和家庭回访正文」——`test_audit_export_api.py` 里那两条断言的
  是**导出文件**（`"敏感跟进正文" not in body`），而这一条说的是**审计行**。

第三张表里那句「德育领导查看学生档案时不返回重点题和访谈正文」（`07_acceptance_tests.md`
§5）也一并落在这里：它在实现上的形状是**整个端点不可达**（`PsychDetailReader` 与
`KeyQuestionReader` 都只放行 `SCOPED` / `GRANTED_WITH_AUDIT`，德育领导的 `SUMMARY`
过不去），而 `test_permissions.py::test_leader_is_denied_case_detail_under_defaults`
钉的是**列表**接口——验收句里点名的两个端点（个案详情、重点题）此前一个都没被点过。
"""

from datetime import date

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.care import FamilyContactRecord
from app.tests.conftest import auth_headers
from app.tests.test_care_api import create_risk_case

# 两个只可能来自「内容」的标记。选它们而不是选「是」/「否」那种真实的答案值，
# 是因为后者在别的文案里也可能出现（`is_key_question`、枚举码……），
# 一条会因为无关文案变红的断言很快会被人关掉（§18 那条）。
FAMILY_NOTE_MARKER = "这段回访正文只该留在 family_contact_record 上"


def _the_student(client, headers) -> int:
    """种子里那一名学生（`S001`）。

    **按学号找，不按 `items[0]` 取**：那个列表按年级 / 班级 / 学号排，而演示库里有
    28 名学生（`seed_demo`），第一个是谁取决于名册长什么样——写死位置会让这三条用例
    在任何人往演示数据里加一个学生之后读到**别人**的答卷，而它们红不红与功能无关
    （§测试注意：不要写死位置与行数）。
    """
    listed = client.get("/api/v1/students", headers=headers)
    assert listed.status_code == 200
    for row in listed.json()["data"]["items"]:
        if row["student_no"] == "S001":
            return row["id"]
    raise AssertionError("种子里那名学生（S001）不在列表里——这几条用例的前提不成立")


def _audit_rows(db_session, action: str | None = None) -> list[AuditLog]:
    statement = select(AuditLog)
    if action is not None:
        statement = statement.where(AuditLog.action == action)
    return list(db_session.scalars(statement).all())


def test_the_key_question_read_needs_a_stated_purpose(client, db_session):
    """「查看重点题必须填写查看原因」——**空原因与不写原因都要挡住，且一条审计都不留。**

    先走一遍通的那条路，是为了让下面那两条断言**有东西可否定**：一个从不写审计的实现
    会让「拒绝时不写审计」这句恒真（§测试注意：先证明有东西可扫，再断言它干净）。
    """
    headers = auth_headers(client, "counselor", "13800000001")
    student_id = _the_student(client, headers)
    url = f"/api/v1/students/{student_id}/key-questions"

    # 1. 写明原因 → 200，并且审计里那一条**带着他写的那句话**
    ok = client.get(url, headers=headers, params={"purpose": "复核重点关注学生"})
    assert ok.status_code == 200
    audits = _audit_rows(db_session, "查看重点题")
    assert len(audits) == 1, "这一条读必须留下恰好一条轨迹"
    assert audits[0].purpose == "复核重点关注学生"
    assert audits[0].student_id == student_id

    # 2. 空白原因 → 422。`purpose.strip()` 那一判要在这里生效：一串空格与没写是同一件事
    blank = client.get(url, headers=headers, params={"purpose": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "PURPOSE_REQUIRED"

    # 3. 整个参数不传 → 也是 422（FastAPI 替我们挡的，与上面那句不同源，
    #    所以两条各断言一次；只断一条等于另一种仍然可能悄悄放行）
    missing = client.get(url, headers=headers)
    assert missing.status_code == 422

    # 两次被拒都没有写审计——轨迹要回答的是「谁看了」，而不是「谁试过」
    assert len(_audit_rows(db_session, "查看重点题")) == 1


def test_the_audit_trail_never_reproduces_the_sheet_or_the_family_note(client, db_session):
    """「审计日志不得包含完整答案和敏感正文」。

    判据是**审计行里有没有装内容的容器**，不是「内容里有没有某个词」：审计行的每一个
    字段都有确定的形状（动作码 / 资源类型 / 资源 id / 用途 / 谁做的），而答卷与回访
    正文是自由文本长度的东西——它们装不进去，除非有人往 `detail_json` 里塞。
    所以这条用例做两件事：走一遍最敏感的那条链路（读重点题 + 写家庭回访），
    然后逐行检查审计表里**没有一行**带着回访正文那句话，且这几个动作的 `detail_json`
    都是空的。
    """
    headers = create_risk_case(client)
    student_id = _the_student(client, headers)
    cases = client.get("/api/v1/care-cases", headers=headers).json()["data"]["items"]
    case_id = cases[0]["case_id"]

    client.get(
        f"/api/v1/students/{student_id}/key-questions",
        headers=headers,
        params={"purpose": "复核重点关注学生"},
    )

    family = client.post(
        f"/api/v1/care-cases/{case_id}/family-contacts",
        headers=headers,
        json={
            "contact_date": str(date(2026, 9, 20)),
            "contact_person": "母亲",
            "channel": "电话",
            "result": "已联系",
            "support_status": "愿意配合",
            "confirmed_facts": FAMILY_NOTE_MARKER,
        },
    )
    assert family.status_code == 200

    # 正面那一半：正文**确实存下来了**，只是不在审计里（§16.6 只禁它进审计与事件，
    # 没禁它进自己那张表）。少了这一句，一个把回访正文整段丢掉的实现也能通过下面那半。
    stored = db_session.scalar(select(FamilyContactRecord.confirmed_facts))
    assert stored == FAMILY_NOTE_MARKER

    rows = _audit_rows(db_session)
    assert rows, "这条链路上必须写下了审计，否则下面那两条断言是空转"

    # 这两个动作确实都被记下来了（先证明有东西可扫）
    actions = {row.action for row in rows}
    assert "查看重点题" in actions
    assert "创建家庭回访" in actions

    # 反面那一半：审计行的每一列都不承载内容。`detail`（`Text`）是**唯一**装得下自由
    # 文本的那一列，而这两个动作都不写它——`resource_id` 是 id，`purpose` 是操作员自己
    # 填的一句用途（不是正文）。
    #
    # **是 `detail` 不是 `detail_json`**：两个列都在 `audit_log` 上，而 `detail_json`
    # 是 V1.2 对齐时加的那一列、今天**既没有写入方也没有读者**（§27 记着它）。按
    # `detail_json` 断言是一条**空转**的守卫——它是一个恒为 NULL 的列，任何内容都进不去，
    # 所以那条断言在任何实现下都绿（这一稿就是这么写的，变异验证时它没红才发现的）。
    for row in rows:
        assert row.detail is None, f"{row.action} 往审计里写了 detail：那是最容易漏内容的一列"
        assert FAMILY_NOTE_MARKER not in (row.purpose or "")
        assert FAMILY_NOTE_MARKER not in (row.resource_id or "")


def test_a_leader_cannot_reach_the_two_endpoints_that_hold_the_sheet(client):
    """「德育领导查看学生档案时不返回重点题和访谈正文」。

    实现上的形状是**整个端点不可达**——两个端点分别只放行 `STUDENT_PSYCH_DETAIL: SCOPED`
    与 `KEY_QUESTIONS: GRANTED_WITH_AUDIT`，德育领导两样都没有（它是 `SUMMARY` /
    `NONE`），所以「不返回」在这里的意思是「进不去」。先证明同一条路心理老师走得通，
    否则一个把两个端点都拆掉的实现也是绿的。

    **变异验证时踩到一件事，值得记下来**：把 `NONE` 加进那个 `allow={...}` 集合
    **不会**打开这道门——`scope_allows` 是先 `if scope == NONE: return False`、
    然后才看 `allow` 的（`permissions.py:196`）。也就是说**「没配这一档」不是靠
    `allow` 挡的，是一道更早的硬拒**，所以能让这条用例变红的变异只有一种：
    真的把那一档授给德育领导（改 `CAPABILITY_DEFAULTS` 或往 `role_permission` 里插一行）。
    第一稿按「往 allow 里塞 NONE」变异，结果用例照绿——那时该怀疑的是变异写错了
    （§28 的 M2 是同一条教训），不是守卫失灵。
    """
    counselor = create_risk_case(client)
    student_id = _the_student(client, counselor)
    leader = auth_headers(client, "leader", "13800000002")

    detail_url = f"/api/v1/care-cases/{student_id}"
    key_url = f"/api/v1/students/{student_id}/key-questions"

    # 心理老师两条都通（个案详情里就带着家庭回访正文，重点题就是原始答卷）
    assert client.get(detail_url, headers=counselor).status_code == 200
    assert (
        client.get(key_url, headers=counselor, params={"purpose": "复核重点关注学生"}).status_code
        == 200
    )

    for url, params in ((detail_url, None), (key_url, {"purpose": "德育领导看重点题"})):
        denied = client.get(url, headers=leader, params=params)
        assert denied.status_code == 403, url
        assert denied.json()["error"]["code"] == "ROLE_FORBIDDEN", url
