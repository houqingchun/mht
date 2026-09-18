"""外部平台的 MHT 普查结果导入（数据中心 → MHT测评记录导入）。

这份文件盯的是**四件容易写对却看不出来的事**：

* 导入的记录必须是**同一种形状**的测评事实。它跟系统内的记录共用
  `calculate_session` / `persist_result_and_dimensions` / `maybe_raise_risk_events`，
  所以「结果行 + 八维度 + 会话 + 目标行」一样不少，**风险事件与关怀档案也一样不少**。
  「导入只带进评分结果、不触发风险」是 2026-09-16 的旧决策，**2026-09-17 由用户撤回**
  （见 CLAUDE.md 已知缺口 8），于是同一份答卷无论从哪条路径进来，判定都完全一致：
  命中重点题 85/97 就建，只落在重点关注区间而没命中重点题的就两条路径都不建。
* 定位是 姓名 + 性别 + 年龄 + 年级 + 班级 五个字段一起做的，而且「班级不存在」
  「没有这个学生」「不在你的范围内」这三种失败**各有各的话术**（前两种分开报是刻意的，
  后一种与前一种同话术也是刻意的，理由见服务里的 `locate_student`）。
* 外部的取值约定（性别 `1/2`、答案 `1/0`）在入库前就翻译成系统编码，落库不留外部词汇。
* 导入的会话对学生是**只读**的：他既不能继续作答，也不能 reset——后者会把一条
  「没有答卷的重点关注」留在库里。
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.models.account import UserAccount, UserScope
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    DimensionResult,
    RiskEvent,
)
from app.models.audit import AuditLog
from app.models.care import ManualReview, StudentCareCase
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale, ScaleQuestion
from app.security.passwords import hash_password
from app.services.assessment_import_service import _task_for_month, month_bounds
from app.services.assessment_service import now_utc_naive
from app.tests.conftest import auth_headers
from app.tests.test_assessment_api import create_student_session, save_answers
from app.tests.test_status_vocabulary import SOURCES
from app.tests.test_student_profile_and_duration import _csv_rows

FIELDS = ["姓名", "性别", "年龄", "年级", "班级", "所用时间"]
QUESTION_HEADERS = [f"{number}.题干" for number in range(1, 101)]
HEADER = FIELDS + QUESTION_HEADERS
BATCH_NAME = "2026年秋季心理普查"
DURATION = "5340秒"


# --------------------------------------------------------------------------
# 造文件
# --------------------------------------------------------------------------


def _row(name, gender, age, grade, class_name, *, yes=(), answers=None, duration=DURATION) -> list[str]:
    values = list(answers) if answers is not None else ["0"] * 100
    for number in yes:
        values[number - 1] = "1"
    return [name, str(gender), str(age), str(grade), str(class_name), duration, *values]


def _csv(records, header=HEADER) -> str:
    return "\n".join(",".join(row) for row in [header, *records])


def _preview(client, headers, text, *, batch_name=BATCH_NAME, tested_on=None, filename="assessment.csv"):
    if isinstance(tested_on, date):
        tested_on = tested_on.isoformat()
    return client.post(
        "/api/v1/assessment-import/preview",
        headers=headers,
        data={"batch_name": batch_name, "tested_on": tested_on or date.today().isoformat()},
        files={"file": (filename, text, "text/csv")},
    )


def _preview_data(client, headers, text, **kwargs) -> dict:
    response = _preview(client, headers, text, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _commit(client, headers, token, *, resolution=None):
    body: dict = {"preview_token": token}
    if resolution is not None:
        # 只有文件里真的有冲突时才需要它（见 `AssessmentImportCommitRequest`）
        body["resolution"] = resolution
    return client.post("/api/v1/assessment-import/commit", headers=headers, json=body)


def _import(client, headers, records, **kwargs) -> dict:
    data = _preview_data(client, headers, _csv(records), **kwargs)
    assert data["preview_token"], data["global_errors"]
    response = _commit(client, headers, data["preview_token"])
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_month_bounds_wrap_across_the_new_year():
    """12 月的那一格要跨到次年 1 月，不能靠「月份 +1」（那会造出一个 13 月）。

    写死这个边界是因为它**在业务上真的会发生**：秋季普查的文件常常拖到 12 月底才导，
    而春季那场在 1 月。错了的话 `datetime(year, 13, 1)` 是一个 500，整条链路直接不可用。
    """
    assert month_bounds(date(2026, 12, 5)) == (
        datetime(2026, 12, 1),
        datetime(2027, 1, 1),
    )
    assert month_bounds(date(2026, 1, 31)) == (
        datetime(2026, 1, 1),
        datetime(2026, 2, 1),
    )


# --- 名册 -------------------------------------------------------------------------


def add_students(client, headers, students, *, grade="初一", class_name="704") -> None:
    """用**既有的**学生信息导入建名册。

    刻意不直接写 `Student` 行：这条链路本来就要求先有名册，而走一遍导入能顺带证明
    「班级按 704 编号」这个约定在两个导入之间是一致的。

    「年龄」列直接写整数：0011 之后名册上存的就是它（原先存出生日期、读取时现算，
    于是这个夹具要倒推一个日期出来）。
    """
    lines = ["student_no,name,grade,class_name,性别,年龄"]
    for student_no, name, gender, age in students:
        lines.append(f"{student_no},{name},{grade},{class_name},{gender},{age}")
    response = client.post(
        "/api/v1/students/import/preview",
        headers=headers,
        files={"file": ("students.csv", "\n".join(lines) + "\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["valid_count"] == len(students), data["rows"]
    assert client.post(
        "/api/v1/students/import/commit",
        headers=headers,
        json={"preview_token": data["preview_token"]},
    ).status_code == 200


def counselor(client) -> dict:
    return auth_headers(client, "counselor", "13800000001")


def admin(client) -> dict:
    return auth_headers(client, "admin", "admin")


def _session_for(db_session, student_no: str) -> AssessmentSession:
    student = db_session.scalar(select(Student).where(Student.student_no == student_no))
    return db_session.scalar(
        select(AssessmentSession).where(AssessmentSession.student_id == student.id)
    )


def _yes_numbers(db_session, session_id: int) -> list[int]:
    """这一场里答「是」的题号，按题号排——用来证明「覆盖」真的把答卷换掉了。"""
    rows = db_session.execute(
        select(ScaleQuestion.question_no, AssessmentAnswer.answer)
        .join(AssessmentAnswer, AssessmentAnswer.question_id == ScaleQuestion.id)
        .where(AssessmentAnswer.session_id == session_id)
        .order_by(ScaleQuestion.question_no)
    ).all()
    return [number for number, answer in rows if answer == "YES"]


# --------------------------------------------------------------------------
# 端到端：一份外部结果变成真正的测评事实
# --------------------------------------------------------------------------


def test_import_writes_the_full_record_set_and_the_same_care_records(client, db_session):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    tested_at = datetime(date.today().year, date.today().month, date.today().day)

    # 赵同学：总分 71 落在重点关注区间**且**重点题 85 答「是」。这在系统内作答时会开出
    # 一条风险事件与一份档案，导入路径必须开出**一模一样**的东西——触发规则与系统内共用
    # `maybe_raise_risk_events`，所以这不是「导入也有这个特性」，而是同一个函数跑了两遍。
    # 钱同学：100 题全答「否」，一条都不该有。
    result = _import(
        client,
        counselor(client),
        [
            _row("赵同学", 1, 12, 1, 4, yes=set(range(1, 71)) | {85}),
            _row("钱同学", 2, 13, 1, 4),
        ],
    )
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["skipped"] == 0

    db_session.expire_all()
    task = db_session.get(AssessmentTask, result["task_id"])
    # 批次任务的编号按**月**：一场外部普查就是一个月一场（见 `_task_for_month`）
    assert task.task_no == result["task_no"] == f"IMPORT-{date.today():%Y%m}-1"
    assert task.source == "IMPORTED"
    assert task.name == BATCH_NAME
    # 这一列是**存的**那个值：`ACTIVE` = 没有人工裁决。列表上显示的状态是
    # `task_service.effective_task_status` 现算的（这批 3/3 全完成 → 已结束），
    # 与这里断言的不是同一个东西——见 `test_task_status.py`。
    assert task.status == "ACTIVE"
    # 批次是一次已经发生过的测评：开始时间是测评日期，**没有**截止时间
    assert task.start_at == tested_at
    assert task.end_at is None

    published_version = db_session.scalar(
        select(AssessmentScale.version).where(AssessmentScale.status == "PUBLISHED")
    )
    sessions = db_session.scalars(
        select(AssessmentSession)
        .where(AssessmentSession.task_id == task.id)
        .order_by(AssessmentSession.id)
    ).all()
    assert [session.student_id for session in sessions] == [
        db_session.scalar(select(Student.id).where(Student.student_no == student_no))
        for student_no in ("S701", "S702")
    ]
    for session in sessions:
        assert session.source == "IMPORTED"
        assert session.scale_version == published_version
        assert session.submitted_at == tested_at
        assert session.started_at == tested_at
        assert session.status == "CALCULATED"
        # 用时是文件里的记录值，不是回算出来的
        assert session.duration_seconds == 5340

        answers = db_session.scalars(
            select(AssessmentAnswer).where(AssessmentAnswer.session_id == session.id)
        ).all()
        assert len(answers) == 100
        # 逐题作答时刻只有天的精度，而且**没有**为了凑出用时被倒推成假时刻
        assert {answer.answered_at for answer in answers} == {tested_at}
        assert {answer.answer for answer in answers} <= {"YES", "NO"}

        assert db_session.scalar(
            select(func.count(DimensionResult.id)).where(DimensionResult.session_id == session.id)
        ) == 8
        assert db_session.scalar(
            select(AssessmentResult.rule_version).where(AssessmentResult.session_id == session.id)
        )
        target = db_session.scalar(
            select(AssessmentTarget).where(
                AssessmentTarget.task_id == task.id,
                AssessmentTarget.student_id == session.student_id,
            )
        )
        assert target.status == "COMPLETED"
        assert target.completed_at == tested_at

    zhao = _session_for(db_session, "S701")
    assert (
        db_session.scalar(
            select(AssessmentResult.total_level).where(AssessmentResult.session_id == zhao.id)
        )
        == "KEY_ATTENTION"
    )
    # 命中重点题的赵同学：恰好一条风险事件，挂在**触发它的那一场**上。
    risk_events = db_session.scalars(select(RiskEvent)).all()
    assert len(risk_events) == 1, "只有 85 答「是」的那一场该有风险事件"
    event = risk_events[0]
    assert event.student_id == zhao.student_id
    assert event.session_id == zhao.id
    assert event.risk_type == "MANUAL_REVIEW_REQUIRED"
    assert event.risk_level == "HIGH_SENSITIVITY"
    assert event.trigger_rule == "KEY_QUESTION_85_YES"
    # 待人工复核：事件由系统开出，但「这算不算重点学生」仍然等人来判。
    assert event.status == "PENDING"

    care_cases = db_session.scalars(select(StudentCareCase)).all()
    assert len(care_cases) == 1
    assert care_cases[0].student_id == zhao.student_id
    assert care_cases[0].status == "PENDING_REVIEW"
    assert care_cases[0].owner_id is None, "自动开出的档案还没有负责人，等人工认领"
    # 档案的开启时刻是**导入那一刻**，不是测评日期。这一条要拿一份「上学期」的文件才验得出来
    # （本用例的测评日期就是今天，两个口径会重合），见 test_imported_care_case_opens_today_not_on_the_test_date。


def test_key_attention_without_a_key_question_creates_nothing(client, db_session):
    """总分类别落在重点关注区间、但重点题 85/97 都答「否」→ 一条记录都不建。

    这是「与系统内作答完全一致」的**反面钉子**。触发只看重点题（`calculation.risk_events`），
    `total_level == KEY_ATTENTION` 本身不写任何行；顺手按总分放宽会让同一份答卷在系统内
    与导入后判定不同，那是比漏报更难查的一类不一致。
    """
    add_students(client, admin(client), [("S703", "孙同学", "男", 13)])
    # 70 题答「是」→ 总分 70，落在 65–90 的重点关注区间，但 85/97 都在「否」里
    _import(client, counselor(client), [_row("孙同学", 1, 13, 1, 4, yes=set(range(1, 71)))])

    db_session.expire_all()
    session = _session_for(db_session, "S703")
    assert (
        db_session.scalar(
            select(AssessmentResult.total_level).where(AssessmentResult.session_id == session.id)
        )
        == "KEY_ATTENTION"
    )
    assert db_session.scalar(select(func.count(RiskEvent.id))) == 0
    assert db_session.scalar(select(func.count(StudentCareCase.id))) == 0


def test_imported_care_case_opens_today_not_on_the_test_date(client, db_session):
    """档案的 `opened_at` 与风险事件的 `created_at` 取的是**导入时刻**，不是测评日期。

    上学期的普查结果今天导进来：测评发生在文件里写的那天，档案是学校今天才看到、今天才开的。
    把两个时间统一成测评日期等于倒填台账——新开出来的档案会看起来在系统里躺了一学期。
    """
    add_students(client, admin(client), [("S704", "李同学", "男", 13)])
    last_term = date.today() - timedelta(days=200)
    _import(client, counselor(client), [_row("李同学", 1, 13, 1, 4, yes={85})], tested_on=last_term)

    db_session.expire_all()
    session = _session_for(db_session, "S704")
    assert session.submitted_at.date() == last_term, "施测日期是文件里写的那个"
    # 「今天」用的是写入时那口钟（`now_utc_naive`），不是 `date.today()`——这两个在
    # UTC+8 的下午到凌晨之间差一天，拿本地日期断言会在那个窗口里假红。
    today = now_utc_naive().date()
    assert db_session.scalar(select(StudentCareCase)).opened_at.date() == today
    assert db_session.scalar(select(RiskEvent)).created_at.date() == today


def test_import_translates_the_external_code_conventions(client, db_session):
    """`1/2` 与 `1/0` 是**那个平台**的约定，落库必须是本系统的编码。"""
    add_students(
        client, admin(client), [("S912", "孙同学", "女", 14)], grade="初三", class_name="912"
    )

    _import(client, counselor(client), [_row("孙同学", 2, 14, 3, 12, yes={1, 2})])

    db_session.expire_all()
    student = db_session.scalar(select(Student).where(Student.student_no == "S912"))
    assert student.gender == "FEMALE", "名册里的性别不该被文件改动"
    # 年级 3 + 班级 12 → 912（初三 12 班），班级首位与年级一致
    class_group = db_session.scalar(select(ClassGroup).where(ClassGroup.name == "912"))
    assert class_group is not None
    assert db_session.scalar(select(Grade.name).where(Grade.id == class_group.grade_id)) == "初三"

    session = _session_for(db_session, "S912")
    assert session.source == "IMPORTED"
    rows = db_session.execute(
        select(AssessmentAnswer.answer, AssessmentAnswer.score).where(
            AssessmentAnswer.session_id == session.id
        )
    ).all()
    assert {answer for answer, _ in rows} <= {"YES", "NO"}
    assert sorted(score for _, score in rows) == [0] * 98 + [1, 1], (
        "1（是）应落成 YES 且 score=1，0（否）落成 NO 且 score=0"
    )


def test_a_gbk_encoded_csv_from_windows_excel_is_read(client, db_session):
    """中文 Excel / WPS 在 Windows 上「另存为 CSV」写出来的是 **GBK**，不是 UTF-8。

    这是学校最可能踩到的一步：只按 UTF-8 解会抛 UnicodeDecodeError，而那是一个 500。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    content = _csv([_row("赵同学", 1, 12, 1, 4)]).encode("gbk")

    data = _preview_data(client, counselor(client), content, filename="2026年心理普查.csv")
    assert data["global_errors"] == []
    assert data["valid_count"] == 1, data["rows"]
    assert _commit(client, counselor(client), data["preview_token"]).json()["data"]["created"] == 1

    db_session.expire_all()
    session = _session_for(db_session, "S701")
    assert session.status == "CALCULATED"
    assert session.duration_seconds == 5340


# --------------------------------------------------------------------------
# 定位：几种失败各有各的话术
# --------------------------------------------------------------------------


def test_a_missing_class_and_a_missing_student_are_reported_separately(client):
    """合成一句「名册中没有「xxx1」（初一 704）」是**假**诊断。

    演示数据里的班级叫 `1班`，所以「班级根本不存在」是这条链路在真实库上最常见的失败；
    把它说成「没有这个学生」会让老师去核对姓名，而问题在班级。分开之后，
    任何一句话都只有一种解释。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])

    data = _preview_data(
        client,
        counselor(client),
        _csv(
            [
                _row("赵同学", 1, 12, 1, 5),  # 班级 705 不存在
                _row("查无此人", 1, 12, 1, 4),  # 班级在，人不在
            ]
        ),
    )
    assert data["valid_count"] == 0
    assert data["error_count"] == 2
    assert [row["errors"][0] for row in data["rows"]] == [
        "名册中没有「初一 705」这个班级",
        "「初一 704」没有叫「查无此人」的学生",
    ]


def test_a_class_number_that_contradicts_the_grade_is_a_row_error(client):
    """班级写 `4` 时不可能矛盾——前缀是从年级**推**出来的。

    只有已经写成三位编号（`704`）时才可能「两列各自看都对、合起来矛盾」，而这正是这一层
    唯一「填错了却看着像对的」情况，所以它按行报错，不静默按班级挂到错年级下。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])

    contradictory = _preview_data(client, counselor(client), _csv([_row("赵同学", 1, 12, 2, 704)]))
    assert contradictory["rows"][0]["errors"] == ["班级 704 属于初一，与年级 初二 不一致"]
    assert contradictory["rows"][0]["grade"] == "初二"
    assert contradictory["rows"][0]["class_name"] == "704"

    # 而裸班号只会推出 `804`，班里没人时如实报「没有这个班级」
    derived = _preview_data(client, counselor(client), _csv([_row("赵同学", 1, 12, 2, 4)]))
    assert derived["rows"][0]["errors"] == ["名册中没有「初二 804」这个班级"]


def test_a_name_shared_by_two_classmates_needs_gender_and_age_to_resolve(client):
    add_students(
        client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "女", 13)]
    )

    data = _preview_data(client, counselor(client), _csv([_row("王同学", 1, 13, 1, 4)]))
    assert data["valid_count"] == 1
    row = data["rows"][0]
    assert row["matched_student_no"] == "S702"
    assert row["errors"] == []
    assert row["warnings"] == ["同班有 2 名同名同学，已按性别与年龄定位到 S702"]


def test_two_classmates_that_gender_and_age_cannot_split_is_an_error(client):
    add_students(client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "男", 12)])

    data = _preview_data(client, counselor(client), _csv([_row("王同学", 1, 12, 1, 4)]))
    assert data["valid_count"] == 0
    assert data["rows"][0]["errors"] == ["初一 704 有 2 名「王同学」，无法按性别与年龄定位"]


def test_gender_and_age_disambiguate_without_producing_warnings_when_they_agree(client):
    add_students(
        client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "女", 13)]
    )
    data = _preview_data(client, counselor(client), _csv([_row("王同学", 1, 13, 1, 4)]))
    assert data["valid_count"] == 1
    row = data["rows"][0]
    assert row["matched_student_no"] == "S702"
    # 只有「同班有 2 名同名同学」这一条提示，没有性别/年龄不符
    assert row["warnings"] == ["同班有 2 名同名同学，已按性别与年龄定位到 S702"]


def test_an_age_off_by_one_from_the_roster_needs_a_decision(client):
    """差一岁是「同一个人的两个数字」，不是「不同的人」——**定位**照旧由它参与。

    名册上的年龄是学校最近一次导入时填的（0011 之后它不再自己变），而这份文件可能是
    去年那次普查：同一个人的两个数字正好差一岁是常事。所以判定「是谁」时 ±1 岁仍然
    当候选（`_disambiguate`），但**要不要拿文件里的年龄改名册**不替学校决定——
    2026-09-17 起它是一条待确认（`AGE_MISMATCH`），不是一句告警。
    """
    add_students(
        client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "女", 13)]
    )
    data = _preview_data(client, counselor(client), _csv([_row("王同学", 2, 13, 1, 4)]))
    assert data["valid_count"] == 0
    assert data["conflict_count"] == 1
    row = data["rows"][0]
    # 定位本身没有歧义：同名两人一男一女，性别就定下来了
    assert row["matched_student_no"] == "S701"
    assert row["errors"] == []
    assert row["warnings"] == ["同班有 2 名同名同学，已按性别与年龄定位到 S701"]
    assert [conflict["type"] for conflict in row["conflicts"]] == ["AGE_MISMATCH"]
    assert row["conflicts"][0]["message"] == "年龄 13 与名册 12 不符"
    assert (row["conflicts"][0]["roster_age"], row["conflicts"][0]["file_age"]) == (12, 13)


def test_an_age_off_by_one_breaks_a_tie_that_nothing_else_can(client):
    """±1 岁的容错真的会被用到：同班同名、**同一性别**，只有差一岁的年龄能分开。

    放到同一个性别里，是为了让「精确相同」这一条先落空——否则性别一过滤就只剩一个人，
    ±1 岁那段代码根本不会执行，测试也就钉不住它。

    定位成功与「年龄对不上名册」是两件事：这一行仍然定位到了 S702，同时也仍然要人拍板。
    """
    add_students(
        client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "男", 13)]
    )
    data = _preview_data(client, counselor(client), _csv([_row("王同学", 2, 14, 1, 4)]))
    assert data["valid_count"] == 0
    row = data["rows"][0]
    assert row["matched_student_no"] == "S702"
    assert row["warnings"] == ["同班有 2 名同名同学，已按性别与年龄定位到 S702"]
    assert row["conflicts"][0]["message"] == "年龄 14 与名册 13 不符"


def test_a_gender_mismatch_only_warns_and_the_row_still_imports(client, db_session):
    """**性别**与名册不符仍然只是一句告警（2026-09-17 用户只把年龄提成了待确认）。

    这一条同时钉住那个「覆盖」不会顺手改性别：名册上的性别没有任何一条链路会写回
    （学生信息导入另算），导入文件里的性别只用来消歧。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])

    data = _preview_data(client, counselor(client), _csv([_row("赵同学", 1, 12, 1, 4)]))
    assert data["valid_count"] == 1, data["rows"]
    row = data["rows"][0]
    assert row["errors"] == []
    assert row["warnings"] == ["性别与名册不符（名册：男）"]
    assert row["conflicts"] == []
    assert data["warning_count"] == 1

    assert _commit(client, counselor(client), data["preview_token"]).json()["data"]["created"] == 1
    db_session.expire_all()
    assert db_session.scalar(select(Student.gender).where(Student.student_no == "S701")) == "MALE"


def test_an_age_mismatch_is_confirmed_before_anything_is_written(client, db_session):
    """年龄不符**不再是**「告警 + 照常导入」：不问就 422，而且一行都不许落库。

    先写完半批再问，用户看到的是「导入失败」，库里却已经多了一批记录——所以冲突是在
    任何写入之前算完的（`commit_assessment_import` 里的 `planned`）。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    grader = counselor(client)

    data = _preview_data(client, grader, _csv([_row("赵同学", 1, 13, 1, 4)]))
    assert data["valid_count"] == 0 and data["conflict_count"] == 1
    assert data["rows"][0]["conflicts"][0]["type"] == "AGE_MISMATCH"
    # 令牌照发：有冲突的行**是**可以写下去的，只是要人先回答「覆盖还是放弃」
    assert data["preview_token"]

    refused = _commit(client, grader, data["preview_token"])
    assert refused.status_code == 422
    assert refused.json()["error"]["message"] == (
        "有 1 条记录需要确认（年龄与名册不符，或本月已有一次导入），"
        "请选择覆盖或放弃这些记录后重试"
    )
    db_session.expire_all()
    assert db_session.scalar(select(func.count(AssessmentSession.id))) == 0
    # 只数导入批次任务：种子基线里那场 `TASK-2026-FALL-MHT` 与本用例无关
    assert db_session.scalar(
        select(func.count(AssessmentTask.id)).where(AssessmentTask.source == "IMPORTED")
    ) == 0

    # 处置方式写错也是同一句话，不是框架的 422（前端只认统一封装里的 error.message）
    assert _commit(
        client, grader, data["preview_token"], resolution="whatever"
    ).json()["error"]["message"] == "处置方式应为覆盖（overwrite）或放弃（skip）"


def test_overwriting_an_age_mismatch_updates_the_roster_and_audits_it(client, db_session):
    """选「覆盖」= 用文件里的年龄**更新名册**，并且留下一条指着这名学生的审计。

    `student.age` 是年龄唯一的存储处（0011），所以「覆盖更新」在这条链路上只有这一种
    含义。学校要能回答「名册上这个数是谁、什么时候改的」。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row("赵同学", 1, 13, 1, 4)]))

    result = _commit(client, grader, data["preview_token"], resolution="overwrite").json()["data"]
    assert (result["created"], result["age_updated"]) == (1, 1)

    db_session.expire_all()
    assert db_session.scalar(select(Student.age).where(Student.student_no == "S701")) == 13
    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "更新学生年龄").order_by(AuditLog.id.desc())
    )
    assert log.detail == "名册 12 → 文件 13（测评记录导入）"
    # 指着那名学生，所以它跟着数据范围走（§9）
    assert log.student_id == db_session.scalar(
        select(Student.id).where(Student.student_no == "S701")
    )
    assert log.resource_type == "STUDENT"
    # 名册上的性别不受影响（文件里的性别只用来消歧）
    assert db_session.scalar(select(Student.gender).where(Student.student_no == "S701")) == "MALE"


def test_abandoning_an_age_mismatch_leaves_the_roster_alone(client, db_session):
    """选「放弃」= 这一条不导入（名册也不动），其余没有冲突的记录照常导入。"""
    add_students(
        client, admin(client), [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)]
    )
    grader = counselor(client)
    data = _preview_data(
        client, grader, _csv([_row("赵同学", 1, 13, 1, 4), _row("钱同学", 2, 13, 1, 4)])
    )
    assert data["valid_count"] == 1 and data["conflict_count"] == 1

    result = _commit(client, grader, data["preview_token"], resolution="skip").json()["data"]
    assert (result["created"], result["updated"], result["skipped"], result["age_updated"]) == (
        1,
        0,
        1,
        0,
    )

    db_session.expire_all()
    assert db_session.scalar(select(Student.age).where(Student.student_no == "S701")) == 12
    assert db_session.scalar(
        select(func.count(AssessmentSession.id))
    ) == 1, "只该有钱同学那一条"
    assert db_session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "更新学生年龄")) == 0

    # 批次审计要能回答「那几条去哪了」：同样的 action / resource_type / resource_id 下，
    # 「新建两批」与「放弃冲突行」只有这几个数能区分（同 §8 导出必须记遮蔽模式的道理）。
    # 处置记的是**中文**：这一列是给人读的轨迹，不是给程序解析的编码。
    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "导入测评记录").order_by(AuditLog.id.desc())
    )
    assert "处置=放弃冲突行" in log.detail
    assert "created=1, updated=0, skipped=1, age_updated=0" in log.detail


def test_abandoning_every_row_leaves_no_empty_batch_task_behind(client, db_session):
    """整份文件都被放弃时**不建批次任务**。

    「重新导了同一份文件、想想还是算了」——这是「放弃」最常见的用法，而那时一条记录都写不
    进去。建出来的空任务在任务列表上**永远显示「进行中」**（§12 的判据要求目标行数 > 0 才算
    已结束），而且会被同月的下一次导入复用、把那个月的批次名字定死在这次什么都没导的尝试上
    （`_task_for_month` 复用时不改名）。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    _import(client, grader, [_row("赵同学", 1, 12, 1, 4, yes={1})])

    again = _preview_data(client, grader, _csv([_row("赵同学", 1, 12, 1, 4, yes={2})]))
    assert again["conflict_count"] == 1

    result = _commit(client, grader, again["preview_token"], resolution="skip").json()["data"]
    assert (result["created"], result["updated"], result["skipped"]) == (0, 0, 1)
    assert result["task_id"] is None, "一条都没写进去就不该有批次任务"
    assert result["task_no"] == ""

    db_session.expire_all()
    # 库里只有第一次那次导入留下的那一个批次任务
    assert db_session.scalar(
        select(func.count(AssessmentTask.id)).where(AssessmentTask.source == "IMPORTED")
    ) == 1
    assert _yes_numbers(db_session, _session_for(db_session, "S701").id) == [1], "上一次那份答卷没被动过"

    # 审计行照写，只是没有对象可指——「发生过什么」由 detail 里那几个数回答
    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "导入测评记录").order_by(AuditLog.id.desc())
    )
    assert log.resource_id is None
    assert "处置=放弃冲突行" in log.detail


def test_a_student_outside_the_importers_scope_reads_exactly_like_no_such_student(client, db_session):
    """越权与「没这人」必须逐字相同，否则上传文件就成了名册枚举器。

    第二个入口（提交时按提交者的范围复查）在这里也钉住了：一个由全校范围的人签出来的
    预览令牌被只带一名学生的人重放，必须 403——令牌不绑定签发者，所以范围只能在
    提交那一刻按**提交者**再算一遍。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    in_scope = db_session.scalar(select(Student).where(Student.student_no == "S701"))

    scoped = UserAccount(
        account="13900000009",
        account_type=AccountType.MOBILE,
        display_name="只管一名学生的心理老师",
        password_hash=hash_password("123456"),
        role_code=RoleCode.COUNSELOR,
        must_change_password=False,
    )
    db_session.add(scoped)
    db_session.flush()
    db_session.add(
        UserScope(
            user_id=scoped.id,
            scope_type=ScopeType.STUDENT,
            school_id=in_scope.school_id,
            student_id=in_scope.id,
        )
    )
    db_session.commit()
    scoped_headers = auth_headers(client, "counselor", "13900000009")

    data = _preview_data(
        client,
        scoped_headers,
        _csv([_row("钱同学", 2, 13, 1, 4), _row("压根不存在", 1, 12, 1, 4)]),
    )
    assert data["valid_count"] == 0
    out_of_scope, absent = (row["errors"][0] for row in data["rows"])
    # 钱同学**在名册里**，只是不归这位老师管；他看到的这句话必须与「压根不存在」逐字同形，
    # 否则发一个名字、看回的是哪一句，就能把全校名册枚举出来
    assert out_of_scope == "「初一 704」没有叫「钱同学」的学生"
    assert absent == "「初一 704」没有叫「压根不存在」的学生"

    # 同一个人、同一份文件，全校范围的管理员看得到——这才证明那句话讲的是权限不是名册
    admin_view = _preview_data(client, headers, _csv([_row("钱同学", 2, 13, 1, 4)]))
    assert admin_view["rows"][0]["matched_student_no"] == "S702"

    # 提交时再查一遍范围：管理员（全校范围）签的令牌，落到只管一个学生的人手里
    admin_token = admin_view["preview_token"]
    replay = _commit(client, scoped_headers, admin_token)
    assert replay.status_code == 403
    assert replay.json()["error"]["code"] == "SCOPE_FORBIDDEN"
    assert (
        db_session.scalar(
            select(func.count(AssessmentSession.id)).where(AssessmentSession.source == "IMPORTED")
        )
        == 0
    )


def test_the_same_student_twice_in_one_file_is_an_error(client):
    """一份文件里同一个人出现两次：**错误**，不是待确认——哪一行才是对的没人知道。"""
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    grader = counselor(client)

    data = _preview_data(
        client, grader, _csv([_row("赵同学", 1, 12, 1, 4), _row("赵同学", 1, 12, 1, 4)])
    )
    assert data["valid_count"] == 1
    assert data["conflict_count"] == 0
    assert data["rows"][1]["errors"] == ["与第 2 行是同一名学生"]
    _commit(client, grader, data["preview_token"])


def test_a_second_import_of_the_same_month_asks_whether_to_overwrite(client, db_session):
    """同一个人、同一个月导第二遍：**先问**，不是硬错误。

    用户 2026-09-17 的要求：「针对测评结果也是一样，如果重复导入给出提示（覆盖上次，
    还是放弃导入）」。此前它是一行硬错误（`errors` 里的「已有一次外部导入的测评」），
    于是学校改完文件重导时唯一的出路是改系统日期——而重导一份改过的文件恰恰是
    最常见的一次操作。

    **两份文件的测评日期刻意不是同一天**（都是上个月，但一个是 1 号、一个是月末）：
    判重按**月**为单位，所以「同日」是最容易写对也不说明问题的那一种。把这条测试写成
    同日，`_already_imported` 那种按天判重的实现照样全绿（变异验证发现过这一点）。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    month_end = date.today().replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)

    _commit(
        client,
        grader,
        _preview_data(
            client,
            grader,
            _csv([_row("赵同学", 1, 12, 1, 4, yes={1})]),
            tested_on=month_start,
        )["preview_token"],
    )
    db_session.expire_all()
    first_session_id = _session_for(db_session, "S701").id
    assert first_session_id

    # 上个月末那份文件（同一个月、不同的一天）：**判重按月度为单位**，所以它就是重复
    again = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 1, 12, 1, 4, yes={2})]),
        tested_on=month_end,
    )
    assert again["valid_count"] == 0
    assert again["conflict_count"] == 1
    row = again["rows"][0]
    assert row["errors"] == []
    assert [conflict["type"] for conflict in row["conflicts"]] == ["DUPLICATE"]
    assert row["conflicts"][0]["existing_session_id"] == first_session_id
    assert row["conflicts"][0]["message"] == f"该学生在 {month_end:%Y-%m} 已有一次外部导入的测评"
    assert again["preview_token"], "有冲突的行也进令牌，否则「覆盖上次」这条路走不通"

    refused = _commit(client, grader, again["preview_token"])
    assert refused.status_code == 422
    assert "需要确认" in refused.json()["error"]["message"]

    # 「放弃」：这一条不导入，上一次那份记录原样留着
    skipped = _commit(client, grader, again["preview_token"], resolution="skip").json()["data"]
    assert (skipped["created"], skipped["updated"], skipped["skipped"]) == (0, 0, 1)
    db_session.expire_all()
    session = _session_for(db_session, "S701")
    assert session.id == first_session_id
    assert _yes_numbers(db_session, session.id) == [1], "上一次那份答卷没被动过"


def test_overwriting_replaces_the_previous_record_in_place(client, db_session):
    """「覆盖上次」：**就地把那一场改掉**，不新建第二条，也不换会话 id。

    就地改的理由有一半是外键：`risk_event.session_id` 与 `retest_plan.source_session_id`
    都指向这一行，删了重建会连带把它们带走（全库没有 `ondelete=`，在 MySQL 上是 1451），
    而风险待办背后可能已经挂着心理老师写的复核。所以：答卷、结果、八维度按新文件重写，
    会话本身还在原处——分析、个案详情、导出自动看到新的那一份。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    # 第一份文件：100 题全「否」→ 没有任何判定
    _commit(
        client,
        grader,
        _preview_data(client, grader, _csv([_row("赵同学", 1, 12, 1, 4, duration="1000秒")]))[
            "preview_token"
        ],
    )
    db_session.expire_all()
    session = _session_for(db_session, "S701")
    session_id = session.id
    assert _yes_numbers(db_session, session_id) == []
    assert db_session.scalar(select(func.count(RiskEvent.id))) == 0

    # 第二份文件：改了答案，重点题 85 答「是」，用时也换了
    again = _preview_data(
        client, grader, _csv([_row("赵同学", 1, 12, 1, 4, yes={3, 85}, duration="600秒")])
    )
    result = _commit(client, grader, again["preview_token"], resolution="overwrite").json()["data"]
    assert (result["created"], result["updated"], result["skipped"]) == (0, 1, 0)

    db_session.expire_all()
    sessions = db_session.scalars(select(AssessmentSession)).all()
    assert [row.id for row in sessions] == [session_id], "覆盖不是新建第二条记录"
    assert sessions[0].duration_seconds == 600
    assert _yes_numbers(db_session, session_id) == [3, 85]
    # 结果与八维度按新答卷重算过（旧的那两行必须被清掉，否则会撞唯一约束）
    assert db_session.scalar(
        select(func.count(DimensionResult.id)).where(DimensionResult.session_id == session_id)
    ) == 8
    assert db_session.scalar(
        select(AssessmentResult.total_score).where(AssessmentResult.session_id == session_id)
    ) == 2
    # 新答卷命中了 85，判定照常开出待办
    event = db_session.scalar(select(RiskEvent))
    assert event.session_id == session_id
    assert event.trigger_rule == "KEY_QUESTION_85_YES"


def test_overwriting_withdraws_the_pending_alerts_the_old_sheet_had_raised(client, db_session):
    """覆盖时要**收回**上一次那份答卷开出的待办——但只收回还没人处理过的。

    不收回的话，库里的「待办」指着的是已经不存在的答案（一次改完文件的重新导入会给
    一名学生留下两条同名待办）。已复核的那条留着：它是心理老师的工作记录，
    `manual_review.risk_event_id` 也是外键，本来就删不掉。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    grader = counselor(client)
    # 两个人都命中 85，各开出一条待办
    _commit(
        client,
        grader,
        _preview_data(
            client,
            grader,
            _csv([_row("赵同学", 1, 12, 1, 4, yes={85}), _row("钱同学", 2, 13, 1, 4, yes={85})]),
        )["preview_token"],
    )
    db_session.expire_all()
    untouched = db_session.scalar(
        select(RiskEvent)
        .join(Student, Student.id == RiskEvent.student_id)
        .where(Student.student_no == "S701")
    )
    # 心理老师已经看过钱同学那一条并写了复核（`manual_review` 指向它）
    reviewed = db_session.scalar(select(RiskEvent).where(RiskEvent.id != untouched.id))
    reviewed.status = "REVIEWED"
    db_session.add(
        ManualReview(
            risk_event_id=reviewed.id,
            reviewer_id=db_session.scalar(
                select(UserAccount.id).where(UserAccount.account == "13800000001")
            ),
            review_result="继续观察",
            confirmed_facts="已与班主任沟通，本次结果待复议",
        )
    )
    db_session.commit()

    # 改完文件重导：两个人都改成不命中重点题
    again = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 1, 12, 1, 4), _row("钱同学", 2, 13, 1, 4)]),
    )
    result = _commit(client, grader, again["preview_token"], resolution="overwrite").json()["data"]
    assert (result["updated"], result["withdrawn_risk_events"]) == (2, 1)

    db_session.expire_all()
    left = db_session.scalars(select(RiskEvent)).all()
    assert [event.id for event in left] == [reviewed.id], "已复核的那条必须留下"
    assert left[0].status == "REVIEWED"


def test_the_same_student_in_another_month_is_not_a_duplicate(client, db_session):
    """「不同月份的评测视为不同的测试任务」（用户 2026-09-17）：上月一次、本月一次，
    两条都该照常导入，而且是两个批次任务。"""
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)

    last_month = (date.today().replace(day=1) - timedelta(days=1))
    first = _import(client, grader, [_row("赵同学", 1, 12, 1, 4, yes={85})], tested_on=last_month)
    data = _preview_data(client, grader, _csv([_row("赵同学", 1, 12, 1, 4)]))
    assert data["valid_count"] == 1, data["rows"]
    assert data["conflict_count"] == 0, data["rows"]
    second = _commit(client, grader, data["preview_token"]).json()["data"]

    assert first["task_id"] != second["task_id"]
    assert first["task_no"] == f"IMPORT-{last_month:%Y%m}-1"
    assert second["task_no"] == f"IMPORT-{date.today():%Y%m}-1"
    db_session.expire_all()
    assert db_session.scalar(select(func.count(AssessmentSession.id))) == 2
    # 上个月那条的重点题判定留在**它自己的那一场**里，本月的记录不继承它
    last_session = db_session.scalar(
        select(AssessmentSession).where(AssessmentSession.task_id == first["task_id"])
    )
    assert db_session.scalar(
        select(func.count(RiskEvent.id)).where(RiskEvent.session_id == last_session.id)
    ) == 1
    assert db_session.scalar(
        select(func.count(RiskEvent.id))
        .join(AssessmentSession, AssessmentSession.id == RiskEvent.session_id)
        .where(AssessmentSession.task_id == second["task_id"])
    ) == 0


# --------------------------------------------------------------------------
# 列级与单元格级的校验
# --------------------------------------------------------------------------


def test_column_problems_become_global_errors(client):
    """列级问题一次性说清，而不是逐行报 200 次「缺少姓名」，且不发预览令牌。"""
    headers = counselor(client)

    missing_gender = _preview_data(
        client,
        headers,
        _csv([], header=["姓名", "年龄", "年级", "班级", "所用时间"] + QUESTION_HEADERS),
    )
    assert missing_gender["global_errors"] == ["缺少列：性别"]
    assert missing_gender["preview_token"] is None

    missing_questions = _preview_data(
        client,
        headers,
        _csv([], header=FIELDS + [f"{n}.题干" for n in range(1, 101) if n not in {12, 85}]),
    )
    assert missing_questions["global_errors"] == ["缺少题号列：12、85"]

    # 把一个题号写两遍，表头里就必然少了另一个（100 个格子放 100 个题号）——
    # 所以这两条错误是一起出现的，不是二选一
    duplicated = _preview_data(
        client,
        headers,
        _csv([], header=FIELDS + [f"{n}.题干" if n != 85 else "12.题干" for n in range(1, 101)]),
    )
    assert duplicated["global_errors"] == ["缺少题号列：85", "重复的题号列：12"]

    # 题号写成 Q1 这种做法：一条有用的全局错误，而不是 100 行「未作答」
    unrecognised = _preview_data(
        client, headers, _csv([], header=FIELDS + [f"Q{n}" for n in range(1, 101)])
    )
    assert unrecognised["global_errors"] == ["表头里没有认出任何题号列（题号列应写成 1.题干）"]


def test_bad_answer_cells_name_the_question_numbers(client):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])

    answers = ["0"] * 100
    answers[11] = "2"
    answers[84] = "X"
    answers[36] = ""
    data = _preview_data(
        client, counselor(client), _csv([_row("赵同学", 1, 12, 1, 4, answers=answers)])
    )
    assert data["rows"][0]["errors"] == [
        "第 37 题未作答",
        "第 12 题「2」、第 85 题「X」 的答案应为 1（是）或 0（否）",
    ]
    assert data["valid_count"] == 0


def test_more_than_five_bad_answers_are_truncated_but_counted(client):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    answers = ["0"] * 100
    for number in (1, 2, 3, 4, 5, 6, 7):
        answers[number - 1] = "9"
    data = _preview_data(
        client, counselor(client), _csv([_row("赵同学", 1, 12, 1, 4, answers=answers)])
    )
    assert data["rows"][0]["errors"] == [
        "第 1 题「9」、第 2 题「9」、第 3 题「9」、第 4 题「9」、第 5 题「9」 的答案"
        "应为 1（是）或 0（否）（共 7 题）"
    ]


def test_an_unreadable_duration_is_left_blank(client, db_session):
    """数值列没有记录就留空，不写 0——0 秒是「瞬间答完」，是另一个断言。"""
    add_students(client, admin(client), [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    _import(client, counselor(client), [_row("赵同学", 2, 12, 1, 4, duration="两小时")])

    db_session.expire_all()
    assert _session_for(db_session, "S701").duration_seconds is None

    preview = _preview_data(
        client, counselor(client), _csv([_row("钱同学", 1, 13, 1, 4, duration="两小时")])
    )
    assert preview["rows"][0]["warnings"] == ["作答用时「两小时」无法识别，已留空"]


def test_a_duration_without_the_unit_is_still_read(client, db_session):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    _import(client, counselor(client), [_row("赵同学", 1, 12, 1, 4, duration="5340")])
    db_session.expire_all()
    assert _session_for(db_session, "S701").duration_seconds == 5340


def test_an_unreadable_age_is_only_a_warning(client):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    data = _preview_data(client, counselor(client), _csv([_row("赵同学", 2, "十二岁", 1, 4)]))
    assert data["valid_count"] == 1
    assert data["rows"][0]["errors"] == []
    assert data["rows"][0]["warnings"] == ["年龄「十二岁」无法识别，已忽略"]


def test_an_empty_cell_is_distinguished_from_an_unrecognised_one(client):
    """空性别与性别写错是两句不同的话：前者是「没填」，后者是「填错了」。"""
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    data = _preview_data(client, counselor(client), _csv([_row("赵同学", "", 12, 1, 4)]))
    assert data["rows"][0]["errors"] == ["缺少性别"]

    data = _preview_data(client, counselor(client), _csv([_row("赵同学", "3", 12, 1, 4)]))
    assert data["rows"][0]["errors"] == ["性别 2 表示男、1 表示女"]


def test_batch_name_and_test_date_are_validated(client):
    rows = [_row("赵同学", 1, 12, 1, 4)]

    too_long = _preview_data(client, counselor(client), _csv(rows), batch_name="普" * 129)
    assert too_long["global_errors"] == ["批次名称过长（最多 128 字）"]
    assert too_long["preview_token"] is None

    empty = _preview_data(client, counselor(client), _csv(rows), batch_name="   ")
    assert empty["global_errors"] == ["批次名称不能为空"]

    # 未来日期会写出未来时间的会话与答卷
    future = date(date.today().year + 1, 1, 1)
    assert _preview_data(client, counselor(client), _csv(rows), tested_on=future)[
        "global_errors"
    ] == ["测评日期不能晚于今天"]

    # 日期格式错不是一行错误，而是整次请求的 422 —— 而且必须走统一响应封装，
    # 前端才拿得到 error.message（FastAPI 自带的 422 结构里没有这个字段）
    bad = _preview(client, counselor(client), _csv(rows), tested_on="2026/09/16")
    assert bad.status_code == 422
    assert bad.json()["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "测评日期格式应为 2026-09-16",
    }


def test_a_blank_row_is_dropped_rather_than_reported(client):
    headers = counselor(client)
    empty = _preview(client, headers, "")
    assert empty.status_code == 422
    assert empty.json()["error"]["message"] == "文件里没有数据"

    # Excel 常见的尾部空行：它是一行空，不是一行「缺少姓名」的错误
    data = _preview_data(client, headers, _csv([_row("赵同学", 1, 12, 1, 4)]) + "\n" + "," * 106)
    assert data["total"] == 1
    assert data["error_count"] == 1, "这一行报的是「名册中没有这个班级」，不是「缺少姓名」"


# --------------------------------------------------------------------------
# 权限、审计与来源词汇
# --------------------------------------------------------------------------


def test_import_permissions(client):
    text = _csv([_row("赵同学", 1, 12, 1, 4)])
    assert _preview(client, admin(client), text).status_code == 200
    assert _preview(client, counselor(client), text).status_code == 200
    # 学生不能给自己导测评记录，德育领导只有聚合与受控导出
    assert _preview(client, auth_headers(client, "student", "S001"), text).status_code == 403
    assert _preview(client, auth_headers(client, "leader", "13800000002"), text).status_code == 403


def test_the_import_is_audited_under_its_own_action(client, db_session):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    result = _import(client, counselor(client), [_row("赵同学", 1, 12, 1, 4)])

    db_session.expire_all()
    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "导入测评记录").order_by(AuditLog.id.desc())
    )
    assert log.resource_type == "ASSESSMENT_TASK"
    assert log.resource_id == str(result["task_id"])
    assert log.actor_role == "counselor"
    assert f"batch={BATCH_NAME}" in log.detail and "created=1" in log.detail
    # 只记批次名称，不记原始文件名
    assert "assessment.csv" not in log.detail


def test_imported_sessions_use_the_documented_source_vocabulary(client, db_session):
    """§3 的第一面：后端发出去的来源码必须是 labels.ts 认得出来的。

    第二面（视图有没有调用 `sourceLabel`）对来源词汇暂时是空的——e2e 的演示数据里
    没有 IMPORTED 行，而三处渲染都是条件渲染，所以漏调也不会红。见 CLAUDE.md 的测试注意。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    # 一条系统内作答的会话，好让「两种来源都发得出来」这句断言不是空的
    student_headers, session_id = create_student_session(client)
    save_answers(client, student_headers, session_id)
    assert client.post(
        f"/api/v1/assessment-sessions/{session_id}/submit", headers=student_headers
    ).status_code == 200
    _import(client, counselor(client), [_row("赵同学", 1, 12, 1, 4)])

    db_session.expire_all()
    emitted = {session.source for session in db_session.scalars(select(AssessmentSession)).all()}
    assert emitted == {"IN_SYSTEM", "IMPORTED"}
    assert emitted <= SOURCES, f"后端返回了前端无法映射的来源编码: {emitted - SOURCES}"


# --------------------------------------------------------------------------
# 导入的会话对学生只读
# --------------------------------------------------------------------------


def test_a_student_cannot_answer_or_reset_an_imported_session(client, db_session):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    result = _import(client, counselor(client), [_row("赵同学", 1, 12, 1, 4)])

    session = _session_for(db_session, "S701")
    student_headers = auth_headers(client, "student", "S701")

    # 手敲 URL 进答题页：拿回导入的会话就会看到一份 100% 预填的外部答卷
    opened = client.post(
        "/api/v1/assessment-sessions", headers=student_headers, json={"task_id": result["task_id"]}
    )
    assert opened.status_code == 409
    assert opened.json()["error"]["message"] == "该测评由学校导入，不能在系统内作答"

    # reset 删答案但保留结果行 —— 对导入的记录来说那会留下一条没有答卷的「重点关注」
    reset = client.post(f"/api/v1/assessment-sessions/{session.id}/reset", headers=student_headers)
    assert reset.status_code == 409
    assert reset.json()["error"]["message"] == "该测评由学校导入，不能在系统内重新作答"
    assert (
        db_session.scalar(
            select(func.count(AssessmentAnswer.id)).where(
                AssessmentAnswer.session_id == session.id
            )
        )
        == 100
    )

    # 任务列表照旧显示「已完成」，学生首页因此渲染的是禁用按钮
    tasks = client.get("/api/v1/student/tasks", headers=student_headers).json()["data"]["items"]
    task = next(item for item in tasks if item["id"] == result["task_id"])
    assert task["target_status"] == "COMPLETED"


def test_care_detail_and_task_list_name_the_imported_source(client, db_session):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    student = db_session.scalar(select(Student).where(Student.student_no == "S701"))
    # 档案本来就存在（人工开的），而该生的最新一场测评是导入进来的那份
    db_session.add(StudentCareCase(student_id=student.id, status="PENDING_REVIEW"))
    db_session.commit()
    grader = counselor(client)
    result = _import(client, grader, [_row("赵同学", 1, 12, 1, 4, yes={85})])

    detail = client.get(f"/api/v1/care-cases/{student.id}", headers=grader).json()["data"]
    assert detail["case_status"] == "PENDING_REVIEW"
    assert detail["assessment"]["source"] == "IMPORTED"
    assert detail["assessment"]["duration_seconds"] == 5340
    # 85 答「是」→ 导入路径照样开出风险事件，挂在触发它的那一场上。
    assert [event["trigger_rule"] for event in detail["risk_events"]] == ["KEY_QUESTION_85_YES"]
    # 而且档案**没有**被重开：本来就有一份未关闭的，事件写进去就算数（`open_or_reuse_care_case` 的复用路径）。
    assert (
        db_session.scalar(
            select(func.count(StudentCareCase.id)).where(StudentCareCase.student_id == student.id)
        )
        == 1
    )

    tasks = client.get("/api/v1/assessment-tasks", headers=grader).json()["data"]["items"]
    batch = next(task for task in tasks if task["id"] == result["task_id"])
    assert batch["source"] == "IMPORTED"
    assert batch["completion_rate"] == 100


def test_the_export_source_column_names_the_sitting_it_took_the_numbers_from(client, db_session):
    """受控导出的「来源」列取的是**施测时间最近的那场**会话，而导入的会话 id 更大。

    列名与中文文案由 `test_export_labels_match_frontend.py` 守（那是词汇那一面）；
    这一条守**值**。这里刻意把两个口径**摆成相反的**：系统内那场是今天交的（id 小），
    导入那份是 200 天前的普查（id 大，因为 id 是导入那一刻分配的）。只有按施测时间取，
    「来源」才会是系统内作答、用时才会是 777——按 id 取会指向去年那份，
    于是同一份 CSV 上写着「外部导入」配着 5340 秒，而档案页上写的是另一场。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    student = db_session.scalar(select(Student).where(Student.student_no == "S701"))
    db_session.add(StudentCareCase(student_id=student.id, status="PENDING_REVIEW"))
    # 系统内那场：今天交的，id 更小
    scale = db_session.scalar(select(AssessmentScale))
    db_session.add(
        AssessmentSession(
            task_id=None,
            student_id=student.id,
            scale_id=scale.id,
            scale_version=scale.version,
            submitted_at=now_utc_naive(),
            duration_seconds=777,
            status="CALCULATED",
        )
    )
    db_session.commit()

    grader = counselor(client)
    _import(
        client,
        grader,
        [_row("赵同学", 1, 12, 1, 4)],
        tested_on=date.today() - timedelta(days=200),
    )

    response = client.post(
        "/api/v1/care-cases/export", headers=grader, json={"purpose": "阶段工作统计"}
    )
    rows = _csv_rows(response.text)
    index = {name: position for position, name in enumerate(rows[0])}
    assert all(len(row) == len(rows[0]) for row in rows), "插列错位会让下面的断言整体挪一格"
    row = next(item for item in rows[1:] if item[index["学号"]] == "S701")
    assert row[index["来源"]] == "系统内作答"
    assert row[index["用时(秒)"]] == "777"


def test_the_latest_sitting_is_chosen_by_test_date_not_by_id(client, db_session):
    """「最新一场」按**施测时间**取，不是 id 最大的那场。

    导入的会话 id 是导入那一刻分配的：学校今天把上学期的普查结果导进来，它的 id 比本学期
    系统内的会话更大。按 id 取会让个案详情的「本次测评」、重点学生列表的关注等级、受控导出
    的「来源」列一起指向去年那份，而界面上看不出任何异常——趋势的最后一个点也会是错的。
    """
    add_students(client, admin(client), [("S706", "冯同学", "男", 12)])
    student = db_session.scalar(select(Student).where(Student.student_no == "S706"))
    scale = db_session.scalar(select(AssessmentScale))
    db_session.add(
        AssessmentSession(
            task_id=None,
            student_id=student.id,
            scale_id=scale.id,
            scale_version=scale.version,
            submitted_at=now_utc_naive(),
            duration_seconds=777,
            status="CALCULATED",
        )
    )
    db_session.commit()
    in_system_id = db_session.scalar(
        select(AssessmentSession.id).where(AssessmentSession.student_id == student.id)
    )

    grader = counselor(client)
    # 命中的导入行：档案由导入路径自己开出来，这条链路也是用户要的那一条
    _import(
        client,
        grader,
        [_row("冯同学", 1, 12, 1, 4, yes={85})],
        tested_on=date.today() - timedelta(days=200),
    )

    db_session.expire_all()
    imported = db_session.scalar(
        select(AssessmentSession).where(
            AssessmentSession.student_id == student.id,
            AssessmentSession.source == "IMPORTED",
        )
    )
    assert imported.id > in_system_id, "前提：导入那场的 id 更大（id 是导入时刻分配的）"

    detail = client.get(f"/api/v1/care-cases/{student.id}", headers=grader).json()["data"]
    assert detail["assessment"]["session_id"] == in_system_id
    assert detail["assessment"]["source"] == "IN_SYSTEM"
    assert detail["assessment"]["duration_seconds"] == 777
    # 趋势从旧到新，所以施测时间更近的那场在最后——与「本次测评」指向同一场
    assert [entry["session_id"] for entry in detail["history"]] == [imported.id, in_system_id]


def test_history_runs_old_to_new_and_carries_each_sitting_own_dimensions(client, db_session):
    """「历次趋势」的数据源：**从旧到新**，每场带自己的八维度分与分母。

    两场都是导入的，内容不同（一场 30 题答「是」、一场全答「否」），所以顺序、按会话归组、
    每个维度的题数分母三件事都能直接读出来。`dimensions` 按会话归错组会让两场拿到同一份
    维度分，而单场数据看不出来。

    **新的那份先导**：这样 id 的顺序与施测时间的顺序正好相反，按 id 排的实现在这一条上
    也会变红——两条顺序一致时，这个测试是看不出来的（另一条正面钉子叫
    `test_the_latest_sitting_is_chosen_by_test_date_not_by_id`）。
    """
    add_students(client, admin(client), [("S705", "郑同学", "男", 12)])
    student = db_session.scalar(select(Student).where(Student.student_no == "S705"))
    db_session.add(StudentCareCase(student_id=student.id, status="PENDING_REVIEW"))
    db_session.commit()

    older = date.today() - timedelta(days=200)
    newer = date.today() - timedelta(days=30)
    grader = counselor(client)
    _import(client, grader, [_row("郑同学", 1, 12, 1, 4)], tested_on=newer)
    _import(client, grader, [_row("郑同学", 1, 12, 1, 4, yes=set(range(1, 31)))], tested_on=older)

    db_session.expire_all()
    detail = client.get(f"/api/v1/care-cases/{student.id}", headers=grader).json()["data"]
    history = detail["history"]
    assert [entry["submitted_at"][:10] for entry in history] == [older.isoformat(), newer.isoformat()]
    assert [entry["total_score"] for entry in history] == [30, 0]

    question_counts = dict(
        db_session.execute(
            select(ScaleQuestion.dimension_code, func.count(ScaleQuestion.id))
            .where(ScaleQuestion.dimension_code.isnot(None), ScaleQuestion.status == "ACTIVE")
            .group_by(ScaleQuestion.dimension_code)
        ).all()
    )
    for position, entry in enumerate(history):
        assert len(entry["dimensions"]) == 8, f"第 {position + 1} 场缺维度结果"
        for dimension in entry["dimensions"]:
            # 分母必须逐维度对得上：八维度题数不等（10 或 15），给成常数会让 15 题的那两个维度
            # 在趋势图上被高估，而这条断言是唯一会变红的地方。
            assert dimension["max_score"] == question_counts[dimension["dimension_code"]]
    # 归组确实按会话分开了：两场的维度分不是同一份
    assert [d["score"] for d in history[0]["dimensions"]] != [
        d["score"] for d in history[1]["dimensions"]
    ]


def test_the_template_we_hand_the_school_is_a_file_this_importer_accepts(client):
    """模板走一遍自己的预览——「发出去的模板导不回来」是这条链路上最蠢的一种坏法。

    表头与取值（`1/2`、`1/0`）都在同一个文件里，所以这一条同时钉住 106 列的列名、
    题号写法、以及示例行的取值格式。示例学生当然不在名册里，所以那一行**只**该报
    「没有这个班级」——它证明其余 105 列全都被读懂了。
    """
    response = client.get("/api/v1/assessment-import/template", headers=counselor(client))
    assert response.status_code == 200
    text = response.text.lstrip("﻿")
    assert text.splitlines()[0].split(",") == HEADER

    data = _preview_data(client, counselor(client), text, filename="assessment-import-template.csv")
    assert data["global_errors"] == []
    assert data["valid_count"] == 0
    assert data["rows"][0]["errors"] == ["名册中没有「初一 704」这个班级"]
    assert data["rows"][0]["duration_seconds"] == 3600


def test_imports_in_the_same_month_share_one_task_and_get_their_own_targets(client, db_session):
    """同一个月分两次导（先初一、后初二）**归到同一场任务**，各自的目标行照旧一人一条。

    用户 2026-09-17 的要求：「重复性检测应该按月度为单位，不同月份的评测视为不同的
    测试任务」。此前每次提交都新建一个任务，同一次普查在列表上会是两行，
    完成率各算一半。

    两次导入**刻意落在同一个月里不同的两天**（上个月 1 号与月末）：这正是现实里
    「先初一的文件、隔几天再初二的」那个样子。写成同一天的话，把 `_task_for_month`
    改回按天查重这条断言照样绿——「同月」与「同日」在这里不是一回事。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    grader = counselor(client)
    month_end = date.today().replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)

    first = _import(client, grader, [_row("赵同学", 1, 12, 1, 4)], tested_on=month_start)
    second = _import(client, grader, [_row("钱同学", 2, 13, 1, 4)], tested_on=month_end)
    assert first["task_no"] == f"IMPORT-{month_start:%Y%m}-1"
    assert second["task_no"] == first["task_no"], "同一个月只有一个批次任务"
    assert second["task_id"] == first["task_id"]

    db_session.expire_all()
    # 目标行一人一条，两个人都挂在同一场任务下面
    assert db_session.scalar(
        select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == first["task_id"])
    ) == 2
    assert db_session.scalar(
        select(func.count(AssessmentTask.id)).where(AssessmentTask.source == "IMPORTED")
    ) == 1


def test_a_file_that_is_not_csv_is_rejected_by_extension(client):
    """把 .xlsx 直接传上来是最容易发生的一次误操作（学校的原始导出就是它）。

    扩展名不对就整份拒掉，并**说清该怎么办**：拿一个 xlsx 去喂 `csv.reader` 会读成
    一行乱码，然后每一行报「缺少姓名」——那比这一句难懂得多。
    """
    response = client.post(
        "/api/v1/assessment-import/preview",
        headers=counselor(client),
        data={"batch_name": BATCH_NAME, "tested_on": date.today().isoformat()},
        files={"file": ("2026年心理普查.xlsx", b"PK\x03\x04", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "只支持 .csv 文件，请先将文件另存为 .csv 后重试"


def test_a_forged_commit_token_is_rejected(client):
    headers = counselor(client)
    forged = client.post(
        "/api/v1/assessment-import/commit", headers=headers, json={"preview_token": "not-a-token"}
    )
    assert forged.status_code == 422
    assert forged.json()["error"]["message"] == "导入预览已失效，请重新预览"


def test_dimension_distribution_reads_the_sitting_by_test_date_too(client, db_session):
    """八维度分布图取「施测时间最近的一场」，窗口函数那一份排序也得是这一套。

    `dimension_distribution` 不能逐学生调 `latest_session()`（那是 N+1），所以在 SQL 里用
    `row_number() over (partition by student_id order by ...)` 排同一套序。这条断言盯的正是
    那个窗口函数：把排序换回 `max(id)`，这张图取到的就是去年那份外部普查的维度分，
    而聚合数字看上去永远正常，没有任何别的地方会变红。
    """
    add_students(client, admin(client), [("S707", "陈同学", "男", 12)])
    student = db_session.scalar(select(Student).where(Student.student_no == "S707"))
    scale = db_session.scalar(select(AssessmentScale))
    # 系统内那场：今天交的，id 更小，维度分是刻意挑的一个好认的值
    in_system = AssessmentSession(
        task_id=None,
        student_id=student.id,
        scale_id=scale.id,
        scale_version=scale.version,
        submitted_at=now_utc_naive(),
        duration_seconds=600,
        status="CALCULATED",
    )
    db_session.add(in_system)
    db_session.flush()
    db_session.add(
        AssessmentResult(
            session_id=in_system.id,
            validity_score=0,
            validity_status="VALID",
            total_score=9,
            total_level="GENERAL_RANGE",
            rule_version="MHT-RULE-1.0.0",
        )
    )
    db_session.add(
        DimensionResult(
            session_id=in_system.id,
            dimension_code="LEARNING_ANXIETY",
            score=9,
            level="HIGH",
            interpretation="",
            rule_version="MHT-RULE-1.0.0",
        )
    )
    db_session.commit()

    # 导入那场是 200 天前的普查（id 更大），只命中 85，所以它的 LEARNING_ANXIETY 分不可能是 9
    _import(
        client,
        counselor(client),
        [_row("陈同学", 1, 12, 1, 4, yes={85})],
        tested_on=date.today() - timedelta(days=200),
    )

    items = client.get("/api/v1/analytics/dimensions", headers=counselor(client)).json()["data"][
        "items"
    ]
    learning = next(item for item in items if item["dimension_code"] == "LEARNING_ANXIETY")
    assert learning["assessed_count"] == 1
    assert learning["average_score"] == 9.0
    assert learning["high_count"] == 1


# --------------------------------------------------------------------------
# 同月有多个历史批次时，导入挂到**最新**那一批
# --------------------------------------------------------------------------


def _make_legacy_batch(db, *, tested_on: date, student_no: str, name: str) -> tuple[AssessmentTask, AssessmentSession]:
    """造一行「旧代码按天编号留下的」批次任务，并把那名学生的一场导入会话放进去。

    2026-09-17 之前判重按**天**，所以同一次普查分两次导会得到
    `IMPORT-20260916-1` 与 `IMPORT-20260917-1` 两行。用户库里就有这样一对，
    `张三` 在两边各有一场会话。这个夹具复刻的正是那个形状。
    """
    student = db.scalar(select(Student).where(Student.student_no == student_no))
    scale = db.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    school = db.scalar(select(School).where(School.code == "QH"))
    task = AssessmentTask(
        task_no=f"IMPORT-{tested_on:%Y%m%d}-1",
        name=name,
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        source="IMPORTED",
        start_at=datetime(tested_on.year, tested_on.month, tested_on.day),
        status="ACTIVE",
    )
    db.add(task)
    db.flush()
    session = AssessmentSession(
        student_id=student.id,
        task_id=task.id,
        scale_id=scale.id,
        scale_version=scale.version,
        status="SUBMITTED",
        source="IMPORTED",
        submitted_at=datetime(tested_on.year, tested_on.month, tested_on.day),
    )
    db.add(session)
    db.flush()
    db.add(
        AssessmentResult(
            session_id=session.id,
            validity_score=0,
            validity_status="VALID",
            total_score=2,
            total_level="GENERAL_RANGE",
            rule_version=scale.version,
        )
    )
    db.commit()
    return task, session


def test_the_task_for_a_month_is_the_newest_batch_not_the_oldest(client, db_session):
    """同月两行时 `_task_for_month` 取**最新**的那一行。

    取最老的那一行时，提示里的批次号与审计的 `resource_id` 说 A，
    而真正被就地改写的会话属于 B——轨迹答不上「这次导入动了什么」。
    变异：把 `order_by(AssessmentTask.id.desc())` 改回 `order_by(AssessmentTask.id)`，
    这条变红。
    """
    headers = admin(client)
    add_students(client, headers, [("S703", "赵同学", "男", 12)])
    grader = counselor(client)
    month_end = date.today().replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)

    older = _import(client, grader, [_row("赵同学", 1, 12, 1, 4)], tested_on=month_start)
    legacy, _ = _make_legacy_batch(
        db_session, tested_on=month_end, student_no="S703", name="九月十七日那一批"
    )

    db_session.expire_all()
    school = db_session.scalar(select(School).where(School.code == "QH"))
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    picked = _task_for_month(
        db_session,
        school=school,
        scale=scale,
        batch_name="再来一份",
        tested_on=month_end,
        actor=db_session.scalar(select(UserAccount).where(UserAccount.account == "13800000001")),
    )

    assert picked.id == legacy.id
    assert picked.id != older["task_id"]


def test_a_commit_writes_its_batch_number_and_its_session_to_the_same_task(client, db_session):
    """批次号、审计的 `resource_id`、被改写的会话，三者指向同一条任务。

    这是上一条的用户可见面：用户照着「导入成功」的提示去找那个批次，找到的必须就是
    被改写的那一批。旧代码里 `_task_for_month` 取最老、`existing_import_session` 取最新，
    于是提示说 `IMPORT-20260916-1`，而那条会话住在 `IMPORT-20260917-1` 里——
    他在任务列表上按提示的批次号点开，「查看明细」里是另一批人的数据。
    """
    headers = admin(client)
    add_students(client, headers, [("S704B", "赵同学", "男", 12)])
    grader = counselor(client)
    month_end = date.today().replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)

    _import(client, grader, [_row("赵同学", 1, 12, 1, 4)], tested_on=month_start)
    legacy, legacy_session = _make_legacy_batch(
        db_session, tested_on=month_end, student_no="S704B", name="九月十七日那一批"
    )

    # 本月已经导过一次，所以要走「覆盖」这条路（与用户那次重新导入一模一样）
    data = _preview_data(client, grader, _csv([_row("赵同学", 1, 12, 1, 4)]), tested_on=month_end)
    result = _commit(client, grader, data["preview_token"], resolution="overwrite").json()["data"]

    assert result["task_no"] == legacy.task_no, "批次号必须是最近那一批的"
    assert result["task_id"] == legacy.id

    db_session.expire_all()
    # 这一批下面的那一场会话就是他先前在 `month_start` 导入的那一条，**没有被删了重建**
    # （`risk_event.session_id` / `retest_plan.source_session_id` 都指着它）
    sessions_in_batch = db_session.scalars(
        select(AssessmentSession).where(AssessmentSession.task_id == legacy.id)
    ).all()
    assert [s.id for s in sessions_in_batch] == [legacy_session.id]
    # 而 `month_start` 那一批里的会话原封不动——两批各是各的
    assert db_session.scalar(
        select(func.count(AssessmentSession.id)).where(
            AssessmentSession.task_id != legacy.id
        )
    ) == 1
