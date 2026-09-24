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

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.seed import seed_development_data
from app.db.session import get_db
from app.main import create_app
from app.models.account import UserAccount, UserScope
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    AssessmentTaskScope,
    DimensionResult,
    RiskEvent,
)
from app.models.audit import AuditLog
from app.models.care import ManualReview, StudentCareCase
from app.models.enums import AccountType, RoleCode, ScopeType
from app.models.importing import (
    AssessmentExternalResult,
    AssessmentImportBatch,
    AssessmentImportRow,
    StudentAgeChangeLog,
)
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.scale import AssessmentScale, ScaleQuestion
from app.security.passwords import hash_password
from app.services.assessment_import_service import (
    AGE_RESOLUTION_HINT,
    DIMENSION_BY_LABEL,
    MATCH_STATUSES_NEEDING_RESOLUTION,
    MATCH_STATUSES_UNIMPORTABLE,
    SOURCE_TYPE_FULL_ANSWER,
    SOURCE_TYPE_SUMMARY,
    _task_for_month,
    month_bounds,
)
from app.services.scale_import_service import DIMENSION_CODES
from app.services.assessment_service import now_utc_naive
from app.tests.conftest import auth_headers
from app.tests.factories import make_sitting, make_target
from app.tests.mysql_support import engine_for, throwaway_database
from app.tests.test_assessment_api import create_student_session, save_answers
from app.tests.test_status_vocabulary import SOURCES
from app.tests.test_audit_export_api import export_and_download
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


def _preview(
    client,
    headers,
    text,
    *,
    batch_name=BATCH_NAME,
    tested_on=None,
    filename="assessment.csv",
    task_id=None,
):
    """上传一份文件。

    **上传就是建批次**（V1.2 第 4 期）：200 回的是**批次对象**（`id` 是它的主键），
    而不是 V1.0 那个签满了整份预览的 JWT 令牌。下面两个 helper 都建在这一点上。
    """
    if isinstance(tested_on, date):
        tested_on = tested_on.isoformat()
    data = {"batch_name": batch_name, "tested_on": tested_on or date.today().isoformat()}
    if task_id is not None:
        data["task_id"] = str(task_id)
    return client.post(
        "/api/v1/assessment-imports/preview",
        headers=headers,
        data=data,
        files={"file": (filename, text, "text/csv")},
    )


def _preview_data(client, headers, text, **kwargs) -> dict:
    response = _preview(client, headers, text, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _commit(client, headers, batch_id, *, resolution=None, age_resolution=None):
    body: dict = {}
    if resolution is not None:
        # 只有这一批里真的有要拍板的行时才需要它（见 `AssessmentImportCommitRequest`）
        body["resolution"] = resolution
    if age_resolution is not None:
        # 第二个问题，与 `resolution` 并列（§18.5）：名册上那个年龄动不动、
        # 这一场按哪个年龄记。不传时走 V1.0 的兜底（有年龄冲突就改名册）。
        body["age_resolution"] = age_resolution
    return client.post(
        f"/api/v1/assessment-imports/{batch_id}/commit", headers=headers, json=body
    )


def _import(client, headers, records, **kwargs) -> dict:
    data = _preview_data(client, headers, _csv(records), **kwargs)
    response = _commit(client, headers, data["id"])
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _by_no(data: dict) -> dict[int, dict]:
    """把一次预览的逐行明细按**文件里的行号**索引起来。

    行号是这一层唯一稳定的坐标：`id` 会随复用批次（重传同一个文件会把行重建）
    而变化，而 `row_no` 就是文件里那一行。
    """
    return {row["row_no"]: row for row in data["rows"]}


def _preview_error(client, headers, text, **kwargs) -> str:
    """上传一份**连批次都建不出来**的文件，返回服务端那句 `error.message`。

    列级问题走的是这条路（`start_assessment_import` 在任何写入之前抛 422）：
    「缺一列」不是某几行的毛病，而是整份文件读不成——逐行报 200 次「缺少姓名」
    既没有更多信息，又把操作员的注意力从真正那一句上引开。
    """
    response = _preview(client, headers, text, **kwargs)
    assert response.status_code == 422, response.text
    return response.json()["error"]["message"]


def _statuses(data: dict) -> list[str]:
    return [row["match_status"] for row in data["rows"]]


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
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", "\n".join(lines) + "\n", "text/csv")},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["valid_count"] == len(students), data["rows"]
    assert client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": data["batch_id"]},
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
        # 导入路径的「测评日期」= 文件里那一天，来源 `IMPORT_FILE`（§16.8）。
        # 与在线路径（`ONLINE_SUBMIT`）一起，这两条写入路径把
        # `tested_at == submitted_at` 这条**不变量**钉住——「历次趋势」的排序读的是
        # `submitted_at`，那条排序之所以等于「按真实测评时间」，全靠这条不变量
        # （见 `test_calculation_idempotency.py` 的趋势用例）。
        assert session.tested_at == tested_at
        assert session.tested_at_source == "IMPORT_FILE"
        # 答卷快照摘要：导入与在线走的是同一个 `score_session`，所以这里也必须写。
        assert len(session.answer_snapshot_hash) == 64
        assert session.answer_hash_algorithm == "SHA256_CANONICAL_V1"
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
    assert _statuses(data) == ["MATCHED"], data["rows"]
    assert _commit(client, counselor(client), data["id"]).json()["data"]["created"] == 1

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
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 2}
    # 逐字相等而不是 `in`：这一列是操作员唯一会读的那一格，多接一句「名册上没有这个
    # 班级」这种上一级的说法，就会让他按后一句去查（而它查不出任何东西）。
    assert [row["message"] for row in data["rows"]] == [
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
    assert _statuses(contradictory) == ["INVALID_ROW"]
    assert contradictory["rows"][0]["message"] == (
        "班级 704 属于初一，与年级 初二 不一致；这一行本身有问题，需要改文件后重传"
    )
    # 原始值留下证据（文件里印的是 `704`），而**班级没有归一化出来**——两列矛盾时
    # 这一层不猜一个：猜的话，这一行会静默挂到错年级下面，而报表上一切正常。
    # 年级归一化成功了，因为矛盾的是**班级**。
    assert contradictory["rows"][0]["raw_class_name"] == "704"
    assert contradictory["rows"][0]["normalized_class_name"] is None
    assert contradictory["rows"][0]["normalized_grade_name"] == "初二"

    # 而裸班号只会推出 `804`，班里没人时如实报「没有这个班级」
    derived = _preview_data(client, counselor(client), _csv([_row("赵同学", 1, 12, 2, 4)]))
    assert _statuses(derived) == ["NOT_FOUND"]
    assert derived["rows"][0]["message"] == "名册中没有「初二 804」这个班级"
    assert derived["rows"][0]["normalized_class_name"] == "804"


def test_a_name_shared_by_two_classmates_needs_gender_and_age_to_resolve(client):
    add_students(
        client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "女", 13)]
    )

    data = _preview_data(client, counselor(client), _csv([_row("王同学", 1, 13, 1, 4)]))
    assert data["row_counts"]["ready"] == 1
    row = data["rows"][0]
    assert row["matched_student_no"] == "S702"
    # 定位成功没有错，但「怎么定下来的」留下了证据：这一列是操作员复核时唯一的凭据
    assert row["match_status"] == "MATCHED"
    assert row["message"] == "同班有 2 名同名同学，已按性别与年龄定位到 S702"
    assert row["candidate_count"] == 2
    assert row["match_confidence"] == 0.9


def test_two_classmates_that_gender_and_age_cannot_split_is_an_error(client):
    """分不开的同名同班同学是**进不去**，而不是「待确认」。

    §18.4 给 `AMBIGUOUS` 的处置是「人工选择后是」——在几个候选里指出是哪一个人。
    这一期没有那个入口，所以它落在「有问题」那一档里，`message` 里给出学校此刻真能
    做的那件事（核对名册上这两名学生的性别与年龄）。**留在「待确认」里会造出一句
    做不到的承诺**：那条路上只有「覆盖 / 放弃」两个选择，而两个都不会把它写进去
    ——`commit_batch` 的覆盖分支还要拿 `row.student_id` 去查学生，而它必是 NULL。
    """
    add_students(client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "男", 12)])

    data = _preview_data(client, counselor(client), _csv([_row("王同学", 1, 12, 1, 4)]))
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 1}
    row = data["rows"][0]
    assert row["match_status"] == "AMBIGUOUS"
    assert row["message"] == (
        "初一 704 有 2 名「王同学」，无法按性别与年龄定位；请核对名册上这几名学生的性别与年龄"
    )
    # 两个候选都留在行上：这一行的出路是「在这两个人里挑一个」，而挑之前得先看得见他们
    assert row["candidate_count"] == 2
    assert row["matched_student_no"] is None

    # 「覆盖」也写不进去：它不是冲突，是还没定下来是谁
    committed = _commit(client, counselor(client), data["id"], resolution="overwrite")
    assert committed.status_code == 200, committed.text
    assert committed.json()["data"]["created"] == 0
    assert committed.json()["data"]["skipped"] == 1


def test_gender_and_age_disambiguate_without_producing_warnings_when_they_agree(client):
    add_students(
        client, admin(client), [("S701", "王同学", "男", 12), ("S702", "王同学", "女", 13)]
    )
    data = _preview_data(client, counselor(client), _csv([_row("王同学", 1, 13, 1, 4)]))
    assert data["row_counts"]["ready"] == 1
    row = data["rows"][0]
    assert row["matched_student_no"] == "S702"
    # 只有「同班有 2 名同名同学」这一条提示，没有性别/年龄不符
    assert row["message"] == "同班有 2 名同名同学，已按性别与年龄定位到 S702"


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
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 1, "conflict": 0, "error": 0}
    row = data["rows"][0]
    # 定位本身没有歧义：同名两人一男一女，性别就定下来了
    assert row["matched_student_no"] == "S701"
    assert row["match_status"] == "AGE_CONFLICT"
    assert row["message"] == "年龄 13 与名册 12 不符；同班有 2 名同名同学，已按性别与年龄定位到 S701"
    # 冲突的**原因码**与**两个值**都要留着：`message` 是给人读的一句，而复核要的是
    # 「原来是多少、文件里是多少」，那是两个可以拿去核对的数（§18.5）
    assert row["conflict_code"] == "AGE_MISMATCH"
    assert (row["age_before"], row["age_after"]) == (12, 13)


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
    assert data["row_counts"]["needing_resolution"] == 1
    row = data["rows"][0]
    assert row["matched_student_no"] == "S702"
    assert row["message"] == "年龄 14 与名册 13 不符；同班有 2 名同名同学，已按性别与年龄定位到 S702"


def test_a_gender_mismatch_only_warns_and_the_row_still_imports(client, db_session):
    """**性别**与名册不符仍然只是一句告警（2026-09-17 用户只把年龄提成了待确认）。

    这一条同时钉住那个「覆盖」不会顺手改性别：名册上的性别没有任何一条链路会写回
    （学生信息导入另算），导入文件里的性别只用来消歧。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])

    data = _preview_data(client, counselor(client), _csv([_row("赵同学", 1, 12, 1, 4)]))
    assert data["row_counts"] == {"ready": 1, "needing_resolution": 0, "conflict": 0, "error": 0}, data["rows"]
    row = data["rows"][0]
    # **一行里同时有告警与结论，而它们是两件事**：`match_status` 说这一行会写进去，
    # `message` 那句提示只是复核时的注解。所以它既不是 `AGE_CONFLICT`（没有码），
    # 也没有把这一行拦下来。
    assert row["match_status"] == "MATCHED"
    assert row["conflict_code"] is None
    assert row["message"] == "性别与名册不符（名册：男）"

    assert _commit(client, counselor(client), data["id"]).json()["data"]["created"] == 1
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
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 1, "conflict": 0, "error": 0}
    assert data["rows"][0]["conflict_code"] == "AGE_MISMATCH"
    # 批次**照建**：有冲突的行是可以写下去的，只是要人先回答「覆盖还是放弃」
    # （预览态就把它建成批次，正是为了让人能带着答案回来——见 `_reusable_batch`）
    assert data["id"]

    refused = _commit(client, grader, data["id"])
    assert refused.status_code == 422
    # 这一句在第 6 期**变短了**：原来它还列着「或本场测评里已有系统内提交的答卷」，
    # 而来源冲突（§18.8）从此有自己的门、自己的措辞，也不再是「覆盖/放弃」两个选项
    # ——它是四种处置，而且**整批的覆盖对它无效**。两句话挤在同一句里，操作员按它
    # 选「覆盖」会以为来源冲突也一起解决了（`test_a_row_that_collides_with_an_online_sheet_
    # needs_a_decision` 钉住那一条）。
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
        client, grader, data["id"], resolution="whatever"
    ).json()["error"]["message"] == "处置方式应为覆盖（overwrite）或放弃（skip）"


def test_overwriting_an_age_mismatch_updates_the_roster_and_audits_it(client, db_session):
    """选「覆盖」= 用文件里的年龄**更新名册**，并且留下一条指着这名学生的审计。

    `student.age` 是年龄唯一的存储处（0011），所以「覆盖更新」在这条链路上只有这一种
    含义。学校要能回答「名册上这个数是谁、什么时候改的」。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row("赵同学", 1, 13, 1, 4)]))

    result = _commit(client, grader, data["id"], resolution="overwrite").json()["data"]
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
    assert data["row_counts"] == {"ready": 1, "needing_resolution": 1, "conflict": 0, "error": 0}

    result = _commit(client, grader, data["id"], resolution="skip").json()["data"]
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
    # `not_applied` 是第 6 期加的那个数（§18.8 里「没落成一场测评」的行数）。这一批
    # 没有来源冲突，所以它是 0——而这正是这一格要能说出来的话：`skipped=1` 说的是
    # 有人放弃了那一条，`not_applied=0` 说的是没有人是被「保留在线/否掉外部」裁掉的。
    assert "created=1, updated=0, skipped=1, not_applied=0, age_updated=0" in log.detail


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
    assert again["row_counts"] == {"ready": 0, "needing_resolution": 1, "conflict": 0, "error": 0}

    result = _commit(client, grader, again["id"], resolution="skip").json()["data"]
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

    第二个入口（提交时按提交者的范围复查）在这里也钉住了：**批次不绑定创建者**
    （V1.2 起 `list_import_batches` 有意不过滤 `imported_by`，同一所学校的人共用
    一批数据），所以一个由全校范围的人建出来的批次，落到只带一名学生的人手里时
    必须 403——范围只能在提交那一刻按**提交者**再算一遍。
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
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 2}
    assert [row["match_status"] for row in data["rows"]] == ["NOT_FOUND", "NOT_FOUND"]
    out_of_scope, absent = (row["message"] for row in data["rows"])
    # 钱同学**在名册里**，只是不归这位老师管；他看到的这句话必须与「压根不存在」逐字同形，
    # 否则发一个名字、看回的是哪一句，就能把全校名册枚举出来
    assert out_of_scope == "「初一 704」没有叫「钱同学」的学生"
    assert absent == "「初一 704」没有叫「压根不存在」的学生"
    # **候选数也得是 0**，不只是那一句话逐字相同。S702（钱同学）确实坐在他班里，
    # 可「1」与「0」的差别正是「初一 704 有一个叫钱同学的」——把候选留住再让明细
    # 按它隐藏行，等于用「哪一行消失了」换了个方式把同一件事说出来（而且更省事：
    # 一次上传问遍所有班）。所以范围外的人在这一行里**处处等于不存在**。
    assert data["rows"][0]["candidate_count"] == 0
    assert data["rows"][1]["candidate_count"] == 0

    # 同一个人、同一份文件，全校范围的管理员看得到——这才证明那句话讲的是权限不是名册
    admin_view = _preview_data(client, headers, _csv([_row("钱同学", 2, 13, 1, 4)]))
    assert admin_view["rows"][0]["matched_student_no"] == "S702"

    # 提交时再查一遍范围：管理员（全校范围）建出来的批次，落到只管一个学生的人手里
    replay = _commit(client, scoped_headers, admin_view["id"])
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
    assert data["row_counts"] == {"ready": 1, "needing_resolution": 0, "conflict": 0, "error": 1}
    # 第二行才是坏的，而且它说的是**哪一行**——两份一模一样的行里，「第 2 行」是
    # 操作员唯一能照着改的坐标（`row_no` 就是文件里的那一行）
    assert data["rows"][1]["match_status"] == "INVALID_ROW"
    assert data["rows"][1]["message"] == "与第 2 行是同一名学生"
    assert _commit(client, grader, data["id"]).json()["data"]["created"] == 1


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
            _csv([_row("赵同学", 2, 12, 1, 4, yes={1})]),
            tested_on=month_start,
        )["id"],
    )
    db_session.expire_all()
    first_session_id = _session_for(db_session, "S701").id
    assert first_session_id

    # 上个月末那份文件（同一个月、不同的一天）：**判重按月度为单位**，所以它就是重复
    again = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 2, 12, 1, 4, yes={2})]),
        tested_on=month_end,
    )
    assert again["row_counts"] == {"ready": 0, "needing_resolution": 1, "conflict": 0, "error": 0}
    row = again["rows"][0]
    assert row["match_status"] == "DUPLICATE"
    assert row["conflict_code"] == "DUPLICATE"
    # 那一场**是哪一场**由月份回答，不下发 `session_id`：界面上给操作员的选择只有
    # 「覆盖 / 放弃」，而两个选项都不需要他拿一个内部主键去核（真正要回看那一条的
    # 人走的是「全部学生 → 这名学生 → 历次趋势」，那条路上有 ID 之外的坐标）
    assert row["message"] == f"该学生在 {month_end:%Y-%m} 已有一次外部导入的测评"
    assert row["matched_student_no"] == "S701"
    assert again["id"], "有冲突的批次照建，否则「覆盖上次」这条路走不通"

    refused = _commit(client, grader, again["id"])
    assert refused.status_code == 422
    assert "需要确认" in refused.json()["error"]["message"]

    # 「放弃」：这一条不导入，上一次那份记录原样留着
    skipped = _commit(client, grader, again["id"], resolution="skip").json()["data"]
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
        _preview_data(client, grader, _csv([_row("赵同学", 1, 12, 1, 4, duration="1000秒")]))["id"],
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
    result = _commit(client, grader, again["id"], resolution="overwrite").json()["data"]
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
        )["id"],
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
    result = _commit(client, grader, again["id"], resolution="overwrite").json()["data"]
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
    assert data["row_counts"] == {"ready": 1, "needing_resolution": 0, "conflict": 0, "error": 0}, data["rows"]
    second = _commit(client, grader, data["id"]).json()["data"]

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


def test_column_problems_become_global_errors(client, db_session):
    """列级问题一次性说清，而不是逐行报 200 次「缺少姓名」，**且一条批次都不落库**。

    它不是「预览里的一句提示」，而是 422：一个建了一半的批次比没有批次更难收拾
    ——操作员会看到一批匹配好的行挂在「导入失败」的那次尝试上，而重传同一个文件
    又会复用这个批次（同指纹、同操作者、仍是 PREVIEW），于是他上次改的那几行
    没了却看不出为什么。
    """
    headers = counselor(client)

    assert (
        _preview_error(
            client,
            headers,
            _csv([], header=["姓名", "年龄", "年级", "班级", "所用时间"] + QUESTION_HEADERS),
        )
        == "缺少列：性别"
    )

    assert (
        _preview_error(
            client,
            headers,
            _csv([], header=FIELDS + [f"{n}.题干" for n in range(1, 101) if n not in {12, 85}]),
        )
        == "缺少题号列：12、85"
    )

    # 把一个题号写两遍，表头里就必然少了另一个（100 个格子放 100 个题号）——
    # 所以这两条错误是**一起**出现的，不是二选一
    assert (
        _preview_error(
            client,
            headers,
            _csv([], header=FIELDS + [f"{n}.题干" if n != 85 else "12.题干" for n in range(1, 101)]),
        )
        == "缺少题号列：85；重复的题号列：12"
    )

    # 题号写成 Q1 这种做法：一条有用的全局错误，而不是 100 行「未作答」。
    #
    # 措辞 2026-09-19（第 5 期）变了，因为它现在要同时回答**两种**形态：§18.9 起
    # 「只有总分与维度分、没有 100 道答案」的文件也是合法的输入，所以「认不出题号列」
    # 不再是唯一的死因——一份两个都认不出的表头才会被拦下来。这句话把两条出路
    # 一起说出来（要么写成 `1.题干`，要么给一列总分），而不是只报前半句让操作员以为
    # 这份汇总文件不被支持。
    assert (
        _preview_error(
            client, headers, _csv([], header=FIELDS + [f"Q{n}" for n in range(1, 101)])
        )
        == "表头里既没有认出任何题号列（题号列应写成 1.题干），也没有「总分」列。"
        "包含 100 道原始答案的文件与只包含汇总分数的文件都能导入，但至少要认得出其中一种"
    )

    # 四次都没建出批次来：**「这一批在哪」这个问题不该有一个答不上来的答案**
    assert db_session.scalar(select(func.count(AssessmentImportBatch.id))) == 0


def test_bad_answer_cells_name_the_question_numbers(client):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])

    answers = ["0"] * 100
    answers[11] = "2"
    answers[84] = "X"
    answers[36] = ""
    data = _preview_data(
        client, counselor(client), _csv([_row("赵同学", 1, 12, 1, 4, answers=answers)])
    )
    # 几句话拼成一格，用「；」分隔（`_row_message`）。**最后那句是出路**：
    # 前面两句说的是「哪里错了」，操作员照着它去改文件才对；只有这一列能同时装下两者。
    assert data["rows"][0]["message"] == (
        "第 37 题未作答；"
        "第 12 题「2」、第 85 题「X」 的答案应为 1（是）或 0（否）；"
        "这一行本身有问题，需要改文件后重传"
    )
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 1}


def test_more_than_five_bad_answers_are_truncated_but_counted(client):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    answers = ["0"] * 100
    for number in (1, 2, 3, 4, 5, 6, 7):
        answers[number - 1] = "9"
    data = _preview_data(
        client, counselor(client), _csv([_row("赵同学", 1, 12, 1, 4, answers=answers)])
    )
    assert data["rows"][0]["message"] == (
        "第 1 题「9」、第 2 题「9」、第 3 题「9」、第 4 题「9」、第 5 题「9」 的答案"
        "应为 1（是）或 0（否）（共 7 题）；"
        "这一行本身有问题，需要改文件后重传"
    )


def test_an_unreadable_duration_is_left_blank(client, db_session):
    """数值列没有记录就留空，不写 0——0 秒是「瞬间答完」，是另一个断言。"""
    add_students(client, admin(client), [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    _import(client, counselor(client), [_row("赵同学", 2, 12, 1, 4, duration="两小时")])

    db_session.expire_all()
    assert _session_for(db_session, "S701").duration_seconds is None

    preview = _preview_data(
        client, counselor(client), _csv([_row("钱同学", 1, 13, 1, 4, duration="两小时")])
    )
    # 提示与错误共用那一格：这一行没有人报错，所以这格里只有这句提示
    assert preview["rows"][0]["message"] == "作答用时「两小时」无法识别，已留空"


def test_a_duration_without_the_unit_is_still_read(client, db_session):
    add_students(
        client, admin(client), [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)]
    )
    _import(client, counselor(client), [_row("赵同学", 1, 12, 1, 4, duration="5340")])
    db_session.expire_all()
    assert _session_for(db_session, "S701").duration_seconds == 5340

    # 预览页上的那一列（另一名学生，免得撞上刚刚那一批的指纹）：
    # 它绕一跳从 `assessment_external_result.result_payload_json` 里取（`row_durations`），
    # 所以「匹配上了的行，用时看得见」这件事要有人钉。
    preview = _preview_data(
        client, counselor(client), _csv([_row("钱同学", 1, 13, 1, 4, duration="5340")])
    )
    assert preview["rows"][0]["message"] is None
    assert preview["rows"][0]["duration_seconds"] == 5340


def test_an_unreadable_age_is_only_a_warning(client):
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    data = _preview_data(client, counselor(client), _csv([_row("赵同学", 2, "十二岁", 1, 4)]))
    # 年龄只用来消歧，读不出来仍然进得去——所以这一格是提示，不是错误
    assert data["row_counts"] == {"ready": 1, "needing_resolution": 0, "conflict": 0, "error": 0}
    assert data["rows"][0]["message"] == "年龄「十二岁」无法识别，已忽略"


def test_an_empty_cell_is_distinguished_from_an_unrecognised_one(client):
    """空性别与性别写错是两句不同的话：前者是「没填」，后者是「填错了」。"""
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    data = _preview_data(client, counselor(client), _csv([_row("赵同学", "", 12, 1, 4)]))
    assert data["rows"][0]["message"] == "缺少性别；这一行本身有问题，需要改文件后重传"

    data = _preview_data(client, counselor(client), _csv([_row("赵同学", "3", 12, 1, 4)]))
    assert data["rows"][0]["message"] == (
        "性别 2 表示男、1 表示女；这一行本身有问题，需要改文件后重传"
    )


def test_batch_name_and_test_date_are_validated(client):
    """**批次与文件两级的输入都在建批次之前校验**，不合格就 422、一条批次都不落库。

    与列级错误同一条道理：这些说的都不是「某几行有问题」，而是「这一次请求本身不成立」
    ——建出一个名字叫空串的批次，或者一个测评日期在明年的批次，都只会让操作员拿到
    一份自己没法解释的记录。
    """
    rows = [_row("赵同学", 1, 12, 1, 4)]

    assert (
        _preview_error(client, counselor(client), _csv(rows), batch_name="普" * 129)
        == "批次名称过长（最多 128 字）"
    )

    assert (
        _preview_error(client, counselor(client), _csv(rows), batch_name="   ")
        == "批次名称不能为空"
    )

    # 未来日期会写出未来时间的会话与答卷
    future = date(date.today().year + 1, 1, 1)
    assert (
        _preview_error(client, counselor(client), _csv(rows), tested_on=future)
        == "测评日期不能晚于今天"
    )

    # 日期格式错走的是另一条路（请求体的类型校验），但它**也必须**走统一响应封装，
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
    assert data["total_rows"] == 1
    assert data["row_counts"]["error"] == 1, "这一行报的是「名册中没有这个班级」，不是「缺少姓名」"


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
    assert f"batch={result['batch_no']}" in log.detail and "created=1" in log.detail
    # 记的是批次号，不是批次名称、也不是原始文件名
    assert BATCH_NAME not in log.detail
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
    make_sitting(
        db_session,
        student,
        submitted_at=now_utc_naive(),
        duration_seconds=777,
        status="CALCULATED",
    )
    db_session.commit()

    grader = counselor(client)
    _import(
        client,
        grader,
        [_row("赵同学", 1, 12, 1, 4)],
        tested_on=date.today() - timedelta(days=200),
    )

    # 两跳（阶段 8，见 `export_and_download` 的 docstring）：建作业那一步回的是作业载荷，
    # CSV 从 `/export-jobs/{id}/download` 出去。这一条断的是文件里那些**值**，
    # 所以必须走完第二跳——拿建作业的 `response.text` 去解 CSV，第一步 `_csv_rows`
    # 就会把那段 JSON 解成一行乱码。
    response = export_and_download(
        client, "/api/v1/care-cases/export", grader, json={"purpose": "阶段工作统计"}
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
    make_sitting(
        db_session,
        student,
        submitted_at=now_utc_naive(),
        duration_seconds=777,
        status="CALCULATED",
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
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 1}
    assert data["rows"][0]["message"] == "名册中没有「初一 704」这个班级"
    # 这一行**进不去**，所以没有外部结果记录，用时取不到——`row_durations` 的取舍：
    # 一行连人都没匹配上时，他的「用时」没有任何意思。（匹配上的行看得见，
    # `test_a_duration_without_the_unit_is_still_read` 钉住那一半。）
    assert data["rows"][0]["duration_seconds"] is None


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
        "/api/v1/assessment-imports/preview",
        headers=counselor(client),
        data={"batch_name": BATCH_NAME, "tested_on": date.today().isoformat()},
        files={"file": ("2026年心理普查.xlsx", b"PK\x03\x04", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "只支持 .csv 文件，请先将文件另存为 .csv 后重试"


def test_a_commit_to_a_batch_that_is_not_yours_is_a_404(client):
    """提交拿的是**批次 id**，不是一份可以伪造的预览令牌（V1.0 用的是 JWT）。

    所以这条用例问的是另一个问题：一个够不着的 id 会得到什么。**404：「导入批次
    不存在」**，与「这个 id 根本不存在」逐字相同——`load_batch` 的判据只有一条，
    三种情况回同一句话。403 会告诉一个够不着它的人「这个 id 是存在的」，而那足以
    让另一所学校的批次号被一个个试出来。
    """
    headers = counselor(client)
    missing = client.post("/api/v1/assessment-imports/999999/commit", headers=headers, json={})
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "导入批次不存在"

    # 同一句话，也同一句话地答 `/rows`
    rows = client.get("/api/v1/assessment-imports/999999/rows", headers=headers)
    assert rows.status_code == 404
    assert rows.json()["error"]["message"] == "导入批次不存在"


def test_dimension_distribution_reads_the_sitting_by_test_date_too(client, db_session):
    """八维度分布图取「施测时间最近的一场」，窗口函数那一份排序也得是这一套。

    `dimension_distribution` 不能逐学生调 `latest_session()`（那是 N+1），所以在 SQL 里用
    `row_number() over (partition by student_id order by ...)` 排同一套序。这条断言盯的正是
    那个窗口函数：把排序换回 `max(id)`，这张图取到的就是去年那份外部普查的维度分，
    而聚合数字看上去永远正常，没有任何别的地方会变红。
    """
    add_students(client, admin(client), [("S707", "陈同学", "男", 12)])
    student = db_session.scalar(select(Student).where(Student.student_no == "S707"))
    # 系统内那场：今天交的，id 更小，维度分是刻意挑的一个好认的值
    in_system = make_sitting(
        db_session,
        student,
        submitted_at=now_utc_naive(),
        duration_seconds=600,
        status="CALCULATED",
    )
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
    session = make_sitting(
        db,
        student,
        scale=scale,
        task_id=task.id,
        source="IMPORTED",
        submitted_at=datetime(tested_on.year, tested_on.month, tested_on.day),
    )
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
    result = _commit(client, grader, data["id"], resolution="overwrite").json()["data"]

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


# --------------------------------------------------------------------------
# 第 4 期：绑定任务的匹配（§18.7 / §18.8）与这一批的可查性
# --------------------------------------------------------------------------


def _student_scoped_counselor(client, db_session, *, account, student_no, display_name) -> dict:
    """一名只带**单个学生**范围的心理老师，返回他的请求头。

    范围是单人的（`ScopeType.STUDENT`）而不是某个班：这一段的用例要的是「这名老师
    看得见谁」这件事本身，而不是「一个班有多少人」——后者会随夹具改动漂移，而前者
    是这一层唯一的定义（`security/data_scope.py`）。
    """
    student = db_session.scalar(select(Student).where(Student.student_no == student_no))
    scoped = UserAccount(
        account=account,
        account_type=AccountType.MOBILE,
        display_name=display_name,
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
            school_id=student.school_id,
            student_id=student.id,
        )
    )
    db_session.commit()
    return auth_headers(client, "counselor", account)


def _task_with_targets(db_session, student_nos: list[str]) -> AssessmentTask:
    """建一场只发给这几名学生的任务：**发放那一刻谁在名册上，谁就是目标**。

    与 `assessment_service.create_school_assessment_task` 的口径一致，只是省掉了
    走一遍接口——这里要的是「目标行就是这些」，不是「发放这个动作对不对」。
    """
    school = db_session.scalar(select(School).where(School.code == "QH"))
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    task = AssessmentTask(
        task_no=f"TASK-P4-{db_session.scalar(select(func.count(AssessmentTask.id)))}",
        name="阶段四的目标名单",
        scale_id=scale.id,
        school_id=school.id,
        scope_type="SCHOOL",
        source="IN_SYSTEM",
        start_at=datetime(date.today().year, date.today().month, date.today().day),
        status="ACTIVE",
    )
    db_session.add(task)
    db_session.flush()
    for student_no in student_nos:
        student = db_session.scalar(select(Student).where(Student.student_no == student_no))
        make_target(db_session, student, task)
    db_session.commit()
    return task


def _grade_scoped_task(db_session, *, grade_name: str) -> AssessmentTask:
    """建一场**按年级发放**的任务，并写下那一行发放范围（§22 的 `assessment_task_scope`）。

    `_task_with_targets` 走的是「没有范围行」那条路（`_task_scope_covers` 退到
    「同一所学校」），所以它**造不出** `NOT_IN_TASK_SCOPE`——那需要一行真的范围。
    这一条是 §22 那张表在导入这一侧的读者。

    今天 `create_school_assessment_task` 只写 `SCHOOL` 一行（那个端点还不收范围参数），
    所以这里直接插行，而不是走接口——照 `_task_with_targets` 的同一理由：
    这一条要的是「范围就是这一个年级」，不是「发放这个动作对不对」。
    """
    school = db_session.scalar(select(School).where(School.code == "QH"))
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    grade = db_session.scalar(select(Grade).where(Grade.name == grade_name))
    task = AssessmentTask(
        task_no=f"TASK-P5-{db_session.scalar(select(func.count(AssessmentTask.id)))}",
        name="阶段五的年级范围",
        scale_id=scale.id,
        school_id=school.id,
        scope_type="GRADE",
        source="IN_SYSTEM",
        start_at=datetime(date.today().year, date.today().month, date.today().day),
        status="ACTIVE",
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(
        AssessmentTaskScope(
            task_id=task.id,
            scope_type=ScopeType.GRADE,
            school_id=school.id,
            grade_id=grade.id,
            # `created_by` 是 NOT NULL（「这一行是谁写的」）——即接口那一侧写的是
            # 建这场任务的那位心理老师。这里取种子里的 admin，值本身不重要，
            # 但不填就是 `1048 Column 'created_by' cannot be null`。
            created_by=db_session.scalar(
                select(UserAccount.id).where(UserAccount.role_code == RoleCode.ADMIN)
            ),
        )
    )
    db_session.commit()
    return task


def test_a_student_who_is_not_in_the_task_reads_as_out_of_scope(client, db_session):
    """§18.7：名册上有他、也归这位老师管，但他**不在这场任务里**。

    这与 `NOT_FOUND` 是两个不同的答案，出路也不同——那一个要去补名册，这一个要去
    补发目标（§22 的「补发学生」）。合成一句「找不到这个学生」的话，操作员会照着它
    去翻名册，而名册上那个人写得明明白白，班也对得上。

    这一条钉的是 `OUT_OF_SCOPE` 的**第一支**：他在**这场任务的发放范围内**
    （`assessment_task_scope` 说全校、而他是这所学校的学生），只是目标行上还没有他
    ——发完之后转学进来的学生长这样。所以出路是补发，**而且补得上**。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    task = _task_with_targets(db_session, ["S701"])
    # 发放之后才转进来的那一名学生。目标行里没有他**不是 bug，是时序**——
    # 而这一类学生在真实学校里每次普查都会有几个。
    add_students(client, headers, [("S702", "钱同学", "女", 13)])

    data = _preview_data(
        client, counselor(client), _csv([_row("钱同学", 1, 13, 1, 4)]), task_id=task.id
    )
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 1}
    row = data["rows"][0]
    assert row["match_status"] == "OUT_OF_SCOPE"
    # 2026-09-19（第 5 期）起这句话说全了：同一个 `match_status` 下有两种出路，
    # 而它们要做的事完全不同——一个是「点补发」（这一支），一个是「这一行没救」
    # （下一支）。只说前半句时，操作员分不出自己是哪一种。
    assert row["message"] == "该学生不在本场测评的目标名单里，但在这场测评的范围内，可以补发给他"
    # **界面按它决定要不要长出一个「补发」按钮**（§18.7）。它是这一支与
    # `NOT_IN_TASK_SCOPE` 之间唯一的区别，所以它必须逐字钉住，不能只断一句 message
    # ——那句话是给人读的，而按钮的有无是照这个码判的。
    assert row["out_of_scope_reason"] == "SUPPLEMENT_CANDIDATE"
    # 人**是认出来了的**（这与 `NOT_FOUND` 的另一处区别）：所以这一行不是「找不到」，
    # 而是「找到了、进不去这一场」——`candidate_count` 也照记
    assert row["matched_student_no"] == "S702"
    assert row["candidate_count"] == 1
    # 而这一句不是说给文件听的（改文件没有用，他本来就不在这场里），所以它进的是
    # 「进不去」那一档，与 `NOT_FOUND` 并列——出路是补发目标（§22），不是补名册。
    # 「选哪一档」是有后果的：`commit_batch` 按这三张集合分队，落错一队会让这一行
    # 要么拦下一次本该过的提交，要么被当成「还没拍板」而卡住整批
    assert "OUT_OF_SCOPE" in MATCH_STATUSES_UNIMPORTABLE
    assert "OUT_OF_SCOPE" not in MATCH_STATUSES_NEEDING_RESOLUTION


def test_a_student_outside_the_task_scope_cannot_be_supplemented(client, db_session):
    """`OUT_OF_SCOPE` 的**第二支**：他连这场任务的发放范围都不在（§18.7）。

    两个码共用一个 `match_status`（§18.4 那八个码是冻结的，不为这一支新开一个），
    但出路相反：上一支点一下「补发」就到了，**这一支补不了**——`supplement_targets`
    按发放范围取候选，范围外的学生根本不会出现在候选里。所以界面上不该给他一个
    「补发」按钮（那会点出一个「补发成功：0 人」）。

    合成一句话的代价在这里：一份有 20 行 `OUT_OF_SCOPE` 的文件，全都会被读成
    「补发一下就好」，而其中真正能补的只有几位。
    """
    headers = admin(client)
    # 这场任务只发给初二：名册上那两名学生是初一的，于是一个都不在范围内
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    add_students(client, headers, [("S801", "孙同学", "男", 13)], grade="初二", class_name="801")
    task = _grade_scoped_task(db_session, grade_name="初二")

    data = _preview_data(
        # 性别按名册写（`2` = 男）：这一条要钉的是**那一句**（下面逐字断言），
        # 而性别对不上时 `_row_message` 会把告警接在同一句话后面——那样断言的
        # 就变成了「这一行的全部问题」，而不是「这一条判据说了什么」。
        client, counselor(client), _csv([_row("赵同学", 2, 12, 1, 4)]), task_id=task.id
    )
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 1}
    row = data["rows"][0]
    assert row["match_status"] == "OUT_OF_SCOPE"
    assert row["out_of_scope_reason"] == "NOT_IN_TASK_SCOPE"
    # 这一句比上一支短，**是刻意的**：它没有出路可说。「不在发放范围内」在界面上是
    # 一句已经成立的判词（§22 的「未记录发放范围」用的是同一批词），而不是一句
    # 让操作员去猜的话——「补发」那个按钮在界面上根本不会出现（那是
    # `out_of_scope_reason` 判的），所以这里再说一遍「补不上」是同一件事说两次。
    assert row["message"] == "该学生不在本场测评的发放范围内"
    # 人认出来了（与 `NOT_FOUND` 的区别不变），只是这一场**本来就不打算测他**
    assert row["matched_student_no"] == "S701"
    # 两支共用同一个 `match_status`：所以「不可导入」这条判断不会因为它换一支而失效
    assert "OUT_OF_SCOPE" in MATCH_STATUSES_UNIMPORTABLE


def test_a_row_that_collides_with_an_online_sheet_needs_a_decision(client, db_session):
    """§18.8：同一场任务里，学生自己答过一份、学校又导进来一份。

    两条来源事实**可以并存**（一个任务一个学生可以保留多个来源），但同一时刻只能有
    一个有效结果——所以这一行要人拍板。它与 `DUPLICATE` 的分界是**那一场是谁写的**：
    外部平台导进来的是「同一份东西又导了一遍」，学生在本系统里自己答的是这一条。
    两者都要拍板，但处置不同（重复是「覆盖上次」，冲突是「以哪一份为准」）。

    这一条钉的是判定的那一半：状态、原因码，以及那句说得出区别的话。**四种处置各自
    落到库里的什么地方由 `test_import_conflict_resolution.py` 钉**（第 6 期），这里
    只把它与 `DUPLICATE` 分得开、并且不被整批的「覆盖」放行。

    那句 `message` 的第 6 期后半句（「需要选择以哪一份为准」）不是修辞：这一行的出路
    不再是「覆盖/放弃」两个选项——**整批的覆盖对它无效**（2026-09-19 用户裁决），
    所以要在这里就把「你得选一份」说出来，而不是让操作员按整批那个按钮去猜。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    task = _task_with_targets(db_session, ["S701"])
    student = db_session.scalar(select(Student).where(Student.student_no == "S701"))
    scale = db_session.scalar(select(AssessmentScale).where(AssessmentScale.code == "MHT"))
    make_sitting(
        db_session,
        student,
        scale=scale,
        task_id=task.id,
        source="IN_SYSTEM",
        submitted_at=now_utc_naive(),
    )
    db_session.commit()

    data = _preview_data(
        client, counselor(client), _csv([_row("赵同学", 2, 12, 1, 4)]), task_id=task.id
    )
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 1, "conflict": 1, "error": 0}
    row = data["rows"][0]
    assert row["match_status"] == "CONFLICT"
    assert row["conflict_code"] == "IN_SYSTEM_RESULT"
    assert row["message"] == "该学生在本场测评里已有系统内提交的答卷，需要选择以哪一份为准"
    assert row["matched_student_no"] == "S701"

    # 没选处置就提交 → 422，且**任何写入之前**（§18.6：未处理的冲突不得进入正式结果）。
    # **整批的「覆盖」也不放行它**——所以这里连着试两次：不选、和选了整批覆盖，
    # 两次都要被挡在门外（2026-09-19 用户裁决：一次点击不该作废学生本人作答的卷子）。
    for resolution in (None, "overwrite"):
        refused = _commit(client, counselor(client), data["id"], resolution=resolution)
        assert refused.status_code == 422, refused.text
        assert "需要逐行选择处置方式" in refused.json()["error"]["message"]
        # 拒绝发生在**任何写入之前**：这一场里仍然只有学生自己答的那一份
        db_session.expire_all()
        assert db_session.scalar(
            select(func.count(AssessmentSession.id)).where(AssessmentSession.task_id == task.id)
        ) == 1, "系统内那一场是唯一的一场——这一批一条都没写进去"
        assert db_session.scalar(select(func.count(AssessmentResult.id))) == 0, (
            "学生自己那一场还没算分（`make_sitting` 只造会话），而这一批被挡下来了，"
            "所以库里一条结果都不该有"
        )


def test_reuploading_the_same_file_reuses_the_batch_and_rebuilds_its_rows(client, db_session):
    """§18.4 的那条出路：「找不到学生时应先补充名册，再重新匹配」。

    **匹配的输入不止名册**：答案（100 题）与用时只在文件里，而 `assessment_external_result`
    只给匹配成功的那些行存了它们。所以「只重跑匹配、不重传文件」这条路必然缺答案——
    缺答案的行一旦提交就是一份空答卷。重传是唯一能把两样东西一起带回来的动作。

    于是重传同一个文件要满足两件事：**复用同一个批次**（历史里不会多出一条一模一样
    的记录），而**行整批重建**（补完名册之后结论真的会变）。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    text = _csv([_row("赵同学", 2, 12, 1, 4), _row("钱同学", 1, 13, 1, 4)])

    first = _preview_data(client, grader, text)
    assert first["row_counts"] == {"ready": 1, "needing_resolution": 0, "conflict": 0, "error": 1}
    # `row_no` 是**文件里的行号**（第 1 行是表头，所以第一条数据是第 2 行），不是数组下标
    # ——操作员照着它回文件里改那一行，所以它必须与他在 Excel 里看到的那一行对得上
    assert [row["row_no"] for row in first["rows"]] == [2, 3]
    assert first["rows"][1]["match_status"] == "NOT_FOUND"

    # 补齐名册，再传**同一个文件**
    add_students(client, headers, [("S702", "钱同学", "女", 13)])
    again = _preview_data(client, grader, text)
    assert again["id"] == first["id"], "同一个文件不该多出一条批次记录"
    assert again["batch_no"] == first["batch_no"]
    assert again["row_counts"] == {"ready": 2, "needing_resolution": 0, "conflict": 0, "error": 0}

    db_session.expire_all()
    assert db_session.scalar(select(func.count(AssessmentImportBatch.id))) == 1
    assert db_session.scalar(select(func.count(AssessmentImportRow.id))) == 2, "行是重建的，不是追加的"
    # 行号仍是文件里的坐标，`id` 是新的——前者是操作员照着改文件的依据，后者只是主键
    assert [row["row_no"] for row in again["rows"]] == [2, 3]
    assert {row["id"] for row in again["rows"]}.isdisjoint(
        {row["id"] for row in first["rows"]}
    )


def test_the_batch_list_shows_every_batch_of_the_school(client, db_session):
    """批次是**一所学校的工作记录**，不按创建者过滤。

    另一位心理老师要看得见「这批数据是谁、什么时候导进来的」——那正是「这一批为什么
    长这样」的答案。而逐行明细是另一回事（里面有姓名与学号），那一层的门槛在
    `/rows` 上。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    grader = counselor(client)
    _import(client, grader, [_row("赵同学", 2, 12, 1, 4)], batch_name="九月普查")
    # 第二条**只上传、不提交**：列表要同时认得出「还没提交」与「已经提交」两档，
    # 而这一页正是操作员回头找「我之前传的那一份去哪了」的地方
    _preview_data(client, headers, _csv([_row("钱同学", 1, 13, 1, 4)]), batch_name="九月普查（初二）")

    response = client.get("/api/v1/assessment-imports", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 2
    # 最近的在前
    assert [item["batch_name"] for item in data["items"]] == ["九月普查（初二）", "九月普查"]
    newest, oldest = data["items"]
    # 操作人的姓名与账号**分两个字段**发（界面按审计页的写法自己拼）
    assert (newest["imported_by_account"], newest["imported_by_name"]) == ("admin", "系统管理员")
    assert oldest["imported_by_account"] == "13800000001"
    # 批次名称与文件名**分开**：只发一个的话，看到 `assessment.csv` 的人会以为自己在
    # 界面上填的那一格没保存住
    assert newest["batch_name"] == "九月普查（初二）"
    assert newest["file_name"] == "assessment.csv"
    assert (newest["status"], oldest["status"]) == ("PREVIEW", "COMMITTED")
    # 逐行明细**不在这里**：20 行批次各带几百行会让这一页为了显示 20 行传输几万行
    assert "rows" not in newest
    # 那三个数也不在这里（20 行逐行 GROUP BY 就是 20 次查询），所以这一格是 `null`
    # ——**不是 `0`**。界面必须把两者分开：`null` 是「这一页没算」，`0` 是
    # 「算过了，一行都没有」
    assert newest["row_counts"] is None
    # 而批次上那几列计数照发：它们回答「提交之后写进去了几条」（提交之前都是 0），
    # 与 `row_counts` 的「此刻这一批的行是什么状态」是两个时态
    assert newest["total_rows"] == 1


def test_the_row_detail_is_the_same_judgement_as_the_upload(client):
    """逐行明细走的是**上传时那一处范围过滤**，不是把刚建的行原样发出来。

    判定只有一处，所以「上传之后屏幕上的东西」与「刷新之后 /rows 拿到的」不可能
    不一样——两处各写一遍的话，界面上会出现「同一次导入，上传时说匹配上了 3 行、
    刷新之后说 2 行」这种没人解释得了的对话。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    uploaded = _preview_data(
        client, grader, _csv([_row("赵同学", 2, 12, 1, 4), _row("压根不存在", 1, 12, 1, 4)])
    )

    response = client.get(f"/api/v1/assessment-imports/{uploaded['id']}/rows", headers=grader)
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert detail["batch_id"] == uploaded["id"]
    assert detail["items"] == uploaded["rows"]
    # `total` 是**读者可见**的行数；`row_counts` 是**整批**的（坐在提交按钮旁边那三个数，
    # 不套范围——见 `batch_row_counts`）。两个数各有各的口径，界面上分开写。
    assert detail["total"] == 2
    assert detail["row_counts"] == {"ready": 1, "needing_resolution": 0, "conflict": 0, "error": 1}


def test_the_external_result_keeps_the_answers_out_of_the_score_row(client, db_session):
    """`assessment_external_result` 与 `assessment_result` **不合并**。

    前者是**外部平台的那份答卷**（答案与用时，提交那一刻用来算分的东西），后者是
    本系统算出来的**结果**（总分、等级、规则版本）。两张表一个存输入一个存输出，
    合并之后「这一份分数是哪一份答卷算出来的」就没有第二个来源可以对照了
    （§1 的四层事实模型：原始答题事实与量表计算事实各是一层）。

    最小化策略：`result_payload_json` 里**只有答案与用时**，不存题干、不存外部平台的
    分数与维度——那些要么能从题库重建，要么正是本系统要自己算的东西。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    _import(client, counselor(client), [_row("赵同学", 2, 12, 1, 4, duration="90秒")])

    db_session.expire_all()
    record = db_session.scalar(select(AssessmentExternalResult))
    assert set(record.result_payload_json) == {"answers", "duration_seconds"}
    assert record.result_payload_json["duration_seconds"] == 90
    assert len(record.result_payload_json["answers"]) == 100
    # 三列留空：这一期只处理「文件里就是 100 题答案」那一档，平台算好的汇总不属于它，
    # 而本系统算的分要由引擎在提交时算——预览阶段先算等于把「还没决定要不要的行」也评一遍
    assert (record.total_score, record.dimension_scores_json, record.rule_version) == (None, None, None)
    # 而算出来的东西在 `assessment_result` 那一行上，不在这份 payload 里。
    # 两张表靠 `applied_session_id` 连起来——`student_id` 回答「这是谁的结果」，
    # 这一列才回答「这一份外部结果真的成了哪一场测评」（没被采纳时它是 NULL）
    result = db_session.scalar(select(AssessmentResult))
    assert result.session_id == record.applied_session_id
    assert set(record.result_payload_json) & {"total_score", "total_level", "dimensions"} == set()


def test_the_counts_do_not_shrink_because_the_reader_cannot_see_a_row(client, db_session):
    """逐行明细按范围过滤，而**提交按钮旁边那三个数不套范围**。

    必须如此：`commit_batch` 拒绝提交时数的是整批（「有 1 条记录需要确认」），
    而那三个数如果跟着读者缩小，就会出现最坏的那种对话——屏幕上写着「待确认 0 条」，
    点下去回一句「有 1 条记录需要确认」，而操作员照着屏幕找不出那一条在哪。

    代价是「本批 N 行」与列表里的行数可能不等，所以界面上分开写、各自标明口径。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    uploaded = _preview_data(
        client,
        counselor(client),
        _csv([_row("赵同学", 2, 12, 1, 4), _row("钱同学", 1, 13, 1, 4)]),
    )
    assert uploaded["row_counts"] == {"ready": 2, "needing_resolution": 0, "conflict": 0, "error": 0}
    assert uploaded["row_total"] == 2

    # 一名只带 S702 范围的读者：S701 那一行超出了他的范围
    scoped = _student_scoped_counselor(
        client, db_session, account="13900000008", student_no="S702", display_name="只管一个班的心理老师"
    )
    response = client.get(
        f"/api/v1/assessment-imports/{uploaded['id']}/rows", headers=scoped
    )
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert [row["matched_student_no"] for row in detail["items"]] == ["S702"]
    assert detail["total"] == 1, "明细只给他看得见的那一行"
    assert detail["row_counts"] == {"ready": 2, "needing_resolution": 0, "conflict": 0, "error": 0}, (
        "那三个数是整批的，不因为读者看不见就变小"
    )


def test_the_preview_survives_the_request_that_made_it():
    """★ 上传是一次**写入**，而 `get_db` 从不提交——所以路由自己 `db.commit()`。

    这一条刻意**不用 `client` / `db_session` 夹具**。那两个把 `get_db` 换成一条
    被外层事务罩住、请求结束后既不关闭也不回滚的 session（`conftest.py` 的
    `override_get_db`），于是「路由写了但没提交」在后端套件里**永远全绿**：行就
    躺在那条活着的连接上，谁查都查得到。

    所以这里另起一个库、用一条**真提交、真关闭**的连接发请求，然后**在另一条连接上**
    验这一批还在不在。少了路由里那句 `db.commit()`，请求一结束 pymysql 就把整场写入
    回滚掉，这一条当场变红——而生产上那时客户端拿到的 `batch_id` 提交时会回 404
    「导入批次不存在」，屏幕上刚刚还写着「共 N 行」。
    """
    with throwaway_database() as url:
        engine = engine_for(url)
        try:
            with Session(engine) as setup:
                seed_development_data(setup)
                setup.commit()

            app = create_app()

            def override_get_db():
                # 与 `app/db/session.py` 的 `get_db` 同形：真连接、结束时关闭
                with Session(engine) as session:
                    yield session

            app.dependency_overrides[get_db] = override_get_db
            standalone = TestClient(app)
            headers = admin(standalone)
            add_students(standalone, headers, [("S701", "赵同学", "男", 12)])
            data = _preview_data(
                standalone, counselor(standalone), _csv([_row("赵同学", 2, 12, 1, 4)])
            )
            batch_id = data["id"]
        finally:
            engine.dispose()

        # 另一条连接（另一个引擎）：这一批真的落在库里
        observer = engine_for(url)
        try:
            with Session(observer) as check:
                batch = check.get(AssessmentImportBatch, batch_id)
                assert batch is not None, "上传是一次写入，而写入必须提交——否则它只活在响应里"
                assert batch.status == "PREVIEW"
                assert check.scalar(
                    select(func.count(AssessmentImportRow.id)).where(
                        AssessmentImportRow.batch_id == batch_id
                    )
                ) == 1
        finally:
            observer.dispose()


# --------------------------------------------------------------------------
# 第 5 期 A：只带分数的汇总文件（§18.9）
#
# 学校手上的另一份东西是平台算好的**分**：没有 100 道原始答案，只有总分与八个
# 维度分。规格逐字要求「没有原始答案时**不得伪造** assessment_answer，也不得
# **直接**按本地重点题规则生成风险事件」——所以这一档走的是另一条写库路径，
# 而它的后果（按 `assessment_result` 说话的每一页都看不见它）是这一期最容易
# 出的事，钉在 CLAUDE.md 缺口 10 里。
# --------------------------------------------------------------------------

# 八个维度的中文列名，**键序照 `DIMENSION_BY_LABEL`**：那一份是唯一的口径，
# 这里只是把它铺成表头（它的镜像关系由本文件末尾那条用例盯着）。
DIMENSION_FIELDS = list(DIMENSION_BY_LABEL)
SUMMARY_HEADER = FIELDS + ["总分"] + DIMENSION_FIELDS


def _summary_row(name, gender, age, grade, class_name, total, *, dimensions=None) -> list[str]:
    """汇总文件的一行：六列身份 + 总分 + 八个维度分，**一个题号列都没有**。

    `total` 原样 `str()` 进去（不校验）：这一档的用例里有一半正是在喂坏值
    （`"优秀"` / `"120"` / 空串），它们要被**文件里那样**送进去。
    """
    values = list(dimensions) if dimensions is not None else ["60"] * len(DIMENSION_FIELDS)
    assert len(values) == len(DIMENSION_FIELDS), "八个维度分要一个个给全"
    return [name, str(gender), str(age), str(grade), str(class_name), DURATION, str(total), *values]


def _imported_session(db_session, *, student_no="S701") -> AssessmentSession:
    """这名学生从外部导进来的那一场（`source=IMPORTED`）。

    **按 `source` 取而不是「最后一场」**：同一个库里还有种子任务那一场，
    而这一期的用例里学生在系统内一条会话都没有——但夹具改动之后不一定。
    """
    student = db_session.scalar(select(Student).where(Student.student_no == student_no))
    return db_session.scalar(
        select(AssessmentSession)
        .where(AssessmentSession.student_id == student.id, AssessmentSession.source == "IMPORTED")
        .order_by(AssessmentSession.id.desc())
    )


def _count_for_session(db_session, model, session_id: int) -> int:
    return db_session.scalar(
        select(func.count(model.id)).where(model.session_id == session_id)
    )


def test_a_summary_file_keeps_the_scores_without_inventing_answers(client, db_session):
    """一份只有总分与八个维度分的文件：分留着，答卷一条都不写。

    §18.9 的原话是「没有 100 道原始答案时不得伪造 assessment_answer，也不得直接
    按本地重点题规则生成风险事件」。所以这一条同时钉住**两件事**：该写进去的
    （会话、外部结果、目标行、平台给的分）一条不少，不该写进去的（答卷、结果、
    八维度、风险事件）一条都没有。

    `age_at_test` 与用时是这一场的**实测事实**，与有没有答案无关，照记。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    headers = counselor(client)
    data = _preview_data(
        client,
        headers,
        _csv([_summary_row("赵同学", 2, 12, 1, 4, 57)], header=SUMMARY_HEADER),
    )
    # 形态是**按列**判出来的，写在批次那一行上：界面按它决定怎么读这一批
    assert data["import_mode"] == SOURCE_TYPE_SUMMARY
    assert _statuses(data) == ["MATCHED"], data["rows"]

    result = _commit(client, headers, data["id"]).json()["data"]
    assert (result["created"], result["updated"], result["skipped"]) == (1, 0, 0)

    db_session.expire_all()
    session = _imported_session(db_session)
    assert session is not None
    assert session.source == "IMPORTED"
    # 这一列是「这份答卷是怎么进来的」的第一个写入方（在此之前一律顶着
    # `server_default` 的 `ONLINE`——一条校外导进来的记录在库里的说法是「学生在线答的」）
    assert session.source_type == SOURCE_TYPE_SUMMARY
    assert (session.age_at_test, session.duration_seconds) == (12, 5340)
    # 会话停在「进行中 / 待计算」，因为这一档**根本不走评分**（见缺口 10）
    assert (session.status, session.calculation_status) == ("IN_PROGRESS", "PENDING")

    # 四种记录一条都不许有。**不许伪造**是这一档唯一的硬要求
    assert _count_for_session(db_session, AssessmentAnswer, session.id) == 0
    assert _count_for_session(db_session, AssessmentResult, session.id) == 0
    assert _count_for_session(db_session, DimensionResult, session.id) == 0
    assert _count_for_session(db_session, RiskEvent, session.id) == 0

    # 而平台给的那份分一条不少，**挂在本系统之外的字段上**（它就长在
    # `assessment_external_result` 上，与 `assessment_result` 不合并）
    external = db_session.scalar(
        select(AssessmentExternalResult).where(AssessmentExternalResult.applied_session_id == session.id)
    )
    assert (external.source_type, external.total_score) == (SOURCE_TYPE_SUMMARY, 57)
    assert external.dimension_scores_json == {code: 60 for code in DIMENSION_BY_LABEL.values()}
    # 「还没人核验过」——汇总那一档的全部意义就在这一列上：平台给的分在核验之前
    # 不成为本系统的结果（§18.8 的四种处置归阶段 6）
    assert external.verification_status == "PENDING"
    # 三列全空：文件里没有外部量表与外部规则这两列，编一个填上去等于替一份查不到
    # 出处的规则担保（与 CLAUDE.md §21 那条「tested_at 刻意不回填」同一个道理）
    assert (external.scale_code, external.scale_version, external.rule_version) == (None, None, None)
    # 最小化：这一档的分数已经占了上面那两列，这里再存一遍就是同一份数据有两份
    assert set(external.result_payload_json) == {"duration_seconds"}
    assert external.result_payload_json["duration_seconds"] == 5340

    # 目标行照收：完成率按目标行算，所以这一场在**任务那一页**上是已完成的
    target = db_session.scalar(
        select(AssessmentTarget).where(AssessmentTarget.student_id == session.student_id)
    )
    assert target.status == "COMPLETED"


def test_a_summary_file_without_a_total_is_refused_at_the_column_level(client, db_session):
    """没有「总分」列的汇总文件整份拒掉，且**在任何写入之前**。

    列级问题在 `preview` 就抛（`_batch_errors` 那一族），所以它连批次都不建——
    半个批次比没有批次更难收拾。这里只单独验这一句，全句由
    `test_column_problems_become_global_errors` 逐字盯着。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    headers = counselor(client)
    message = _preview_error(
        client,
        headers,
        _csv(
            [["赵同学", "2", "12", "1", "4", DURATION, "70", "60", "60", "60", "60", "60", "60", "60"]],
            header=FIELDS + DIMENSION_FIELDS,
        ),
    )
    assert "也没有「总分」列" in message, message
    assert db_session.scalar(select(func.count(AssessmentImportBatch.id))) == 0


def test_a_broken_total_score_is_a_row_error_not_a_cell_to_ignore(client, db_session):
    """总分读不出来是**错误**，与维度分相反——这一行整条进不去。

    理由是这一档里总分是这一行**唯一**的分数来源：放它过去，导进去的是一场没有任何
    结果的空会话，而它在界面上与「这个学生测了但是没事」长得一模一样。
    三种坏法各有一句自己的话（缺、不是整数、超范围），都指名说那个数。
    """
    add_students(
        client,
        admin(client),
        [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13), ("S703", "孙同学", "男", 14)],
    )
    headers = counselor(client)
    data = _preview_data(
        client,
        headers,
        _csv(
            [
                _summary_row("赵同学", 2, 12, 1, 4, "优秀"),
                _summary_row("钱同学", 1, 13, 1, 4, 120),
                _summary_row("孙同学", 2, 14, 1, 4, ""),
            ],
            header=SUMMARY_HEADER,
        ),
    )
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 0, "conflict": 0, "error": 3}
    rows = _by_no(data)
    assert all(row["match_status"] == "INVALID_ROW" for row in rows.values())
    # 行负载以 `message` 为准：那三种坏法各说各的，**后面跟着的那一句是统一的**
    # （「这一行本身有问题，需要改文件后重传」，见 `_match_row` 的 errors 那一支）
    assert rows[2]["message"].startswith("总分「优秀」不是 0–90 的整数；")
    assert rows[3]["message"].startswith("总分 120 超出 0–90 的范围；")
    assert rows[4]["message"].startswith("缺少总分；")
    assert "这一行本身有问题" in rows[2]["message"]

    refused = _commit(client, headers, data["id"])
    assert refused.status_code == 200, refused.text
    assert refused.json()["data"]["created"] == 0


def test_a_broken_dimension_score_only_warns_and_the_row_still_goes_in(client, db_session):
    """一维读不出来是**提示**：这一行照样进得去，那一维留空。

    与总分相反（上一条），因为「一格空着」在维度分布那一页上本来就存在——不是每个
    学生八维都有值。空单元格是**跳过**（这一列没填），认不出的值才是提示（填错了）。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    headers = counselor(client)
    dimensions = ["60"] * len(DIMENSION_FIELDS)
    dimensions[1] = "优秀"  # INTERPERSONAL_ANXIETY（「对人焦虑」）
    dimensions[3] = ""  # SELF_BLAME：「这一列没填」与「填错了」是两件事
    data = _preview_data(
        client,
        headers,
        _csv([_summary_row("赵同学", 2, 12, 1, 4, 57, dimensions=dimensions)], header=SUMMARY_HEADER),
    )
    assert _statuses(data) == ["MATCHED"], data["rows"]
    row = data["rows"][0]
    # 提示里用的是**维度码**（这一段都还没进视图，没有中文可对；界面上那一格由
    # `dimensionLabel` 翻）
    assert row["message"] == "维度「INTERPERSONAL_ANXIETY」的分数「优秀」无法识别，已留空"

    _commit(client, headers, data["id"])
    db_session.expire_all()
    external = db_session.scalar(
        select(AssessmentExternalResult).where(
            AssessmentExternalResult.applied_session_id == _imported_session(db_session).id
        )
    )
    # 六个维度有值：填错的那一维与空着的那一维都不写进去
    assert len(external.dimension_scores_json) == len(DIMENSION_FIELDS) - 2
    assert "INTERPERSONAL_ANXIETY" not in external.dimension_scores_json
    assert "SELF_BLAME" not in external.dimension_scores_json


def test_a_file_with_both_columns_is_read_as_full_answers(client, db_session):
    """两种列都在时按**完整答案**走，平台给的分不采纳。

    答案在，本系统就自己算——而「自己算的分」与「平台算的分」不能同时进库：
    事后谁也说不清该信哪一个（`result_payload_json` 那一处把平台的分数剔掉也是这条）。
    """
    add_students(client, admin(client), [("S701", "赵同学", "男", 12)])
    headers = counselor(client)
    data = _preview_data(
        client,
        headers,
        _csv(
            [_row("赵同学", 2, 12, 1, 4, yes=(85,)) + ["3"] + ["60"] * len(DIMENSION_FIELDS)],
            header=HEADER + ["总分"] + DIMENSION_FIELDS,
        ),
    )
    assert data["import_mode"] == SOURCE_TYPE_FULL_ANSWER
    assert _statuses(data) == ["MATCHED"], data["rows"]

    _commit(client, headers, data["id"])
    db_session.expire_all()
    session = _imported_session(db_session)
    assert session.source_type == SOURCE_TYPE_FULL_ANSWER
    # 本地引擎照算：100 条答卷、一条结果、八维度，以及 85 题答「是」开出的那条待办
    assert _count_for_session(db_session, AssessmentAnswer, session.id) == 100
    assert _count_for_session(db_session, AssessmentResult, session.id) == 1
    assert _count_for_session(db_session, DimensionResult, session.id) == 8
    assert _count_for_session(db_session, RiskEvent, session.id) == 1
    # 平台那一列给了 3 分，**没有进库**：这一档的分由本系统引擎算
    assert _yes_numbers(db_session, session.id) == [85]
    external = db_session.scalar(
        select(AssessmentExternalResult).where(AssessmentExternalResult.applied_session_id == session.id)
    )
    assert (external.total_score, external.dimension_scores_json) == (None, None)


# --------------------------------------------------------------------------
# 第 5 期 B：年龄的三个选项（§18.5）
#
# 用户在 2026-09-19 做了裁决：让「无论是否覆盖 student.age，本次测评的 age_at_test
# 都必须保存外部测评时的年龄」那一句**让步**，三个选项各自的形状见 CLAUDE.md §18.5
# 那张表。三支的差别只在两个地方——名册动不动、`age_at_test` 记哪个数——而它们在
# `age_updated=0` 上有两支长得一模一样，所以轨迹上要单独说得清。
# --------------------------------------------------------------------------


def _age_mismatch(client, *, name="赵同学", roster_age=12, file_age=13) -> tuple[dict, dict]:
    """一行「名册 12、文件 13」，确认它被判成待拍板的年龄冲突之后返回预览结果。

    返回 `(预览结果, 心理老师的请求头)`：名册由管理员建（学生信息导入归「组织与账号」，
    心理老师在那条链路上只有 `READ_BASIC`），而测评导入归心理老师。
    """
    add_students(client, admin(client), [("S701", name, "男", roster_age)])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row(name, 2, file_age, 1, 4)]))
    assert _statuses(data) == ["AGE_CONFLICT"], data["rows"]
    row = data["rows"][0]
    # 两个值都在预览里摆给人看过（「名册 12、文件 13」），所以提交时不缺信息
    assert (row["age_before"], row["age_after"]) == (roster_age, file_age)
    return data, grader


def _roster_age(db_session, student_no="S701") -> int | None:
    return db_session.scalar(select(Student.age).where(Student.student_no == student_no))


def _batch_audit(db_session) -> AuditLog:
    return db_session.scalar(
        select(AuditLog).where(AuditLog.action == "导入测评记录").order_by(AuditLog.id.desc())
    )


def test_keeping_the_roster_age_records_the_roster_age_for_this_sitting(client, db_session):
    """「保留系统年龄」：名册不动，**而这一场也按名册那个数记**。

    这是三支里唯一让 `age_at_test` 与文件里的数不同的一支（2026-09-19 的裁决）。
    文件里那个数一个字都没丢——它在 `raw_age` / `age_after` 上，逐行明细里看得见；
    这一支说的是「这一次我们决定按名册那个年龄理解他」。
    """
    data, headers = _age_mismatch(client)
    result = _commit(
        client, headers, data["id"], resolution="overwrite", age_resolution="keep_roster"
    ).json()["data"]
    assert (result["created"], result["age_updated"]) == (1, 0)
    assert result["age_resolution"] == "keep_roster"

    db_session.expire_all()
    assert _roster_age(db_session) == 12, "名册一个字都不该动"
    assert _imported_session(db_session).age_at_test == 12
    row = db_session.scalar(
        select(AssessmentImportRow).where(AssessmentImportRow.batch_id == data["id"])
    )
    assert (row.age_before, row.age_after, row.age_resolution) == (12, 13, "keep_roster")
    assert db_session.scalar(select(func.count(StudentAgeChangeLog.id))) == 0
    assert (
        db_session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "更新学生年龄")
        )
        == 0
    )
    # `allow_age_overwrite` 记的是**结果**（这一批动过名册没有），不是请求
    assert db_session.get(AssessmentImportBatch, data["id"]).allow_age_overwrite is False
    # ★ 轨迹上唯一分得开「保留系统年龄」与「只保存本次测评年龄」的一句：两者
    # `age_updated` 都是 0、名册都没动，而它们按不同的年龄记了这一场
    assert "年龄处置=保留系统年龄" in _batch_audit(db_session).detail


def test_overwriting_the_age_updates_the_roster_and_the_sitting(client, db_session):
    """「覆盖学生当前年龄」：名册改成文件里的数，这一场也按它记，留一条变更日志。

    既有那条 `test_overwriting_an_age_mismatch_updates_the_roster_and_audits_it` 走的
    是**不传** `age_resolution` 的 V1.0 兜底口径，钉的是「兜底仍然是改名册」；
    这一条钉的是**显式传**这一支，以及兜底那条路看不见的四样：这一场按哪个年龄记、
    变更日志那一行的形状、`allow_age_overwrite`、以及轨迹上那句年龄处置。
    """
    data, headers = _age_mismatch(client)
    result = _commit(
        client, headers, data["id"], resolution="overwrite", age_resolution="overwrite"
    ).json()["data"]
    assert (result["created"], result["age_updated"]) == (1, 1)
    assert result["age_resolution"] == "overwrite"

    db_session.expire_all()
    assert _roster_age(db_session) == 13
    session = _imported_session(db_session)
    assert session.age_at_test == 13
    assert db_session.get(AssessmentImportBatch, data["id"]).allow_age_overwrite is True

    student_id = db_session.scalar(select(Student.id).where(Student.student_no == "S701"))
    row_id = db_session.scalar(
        select(AssessmentImportRow.id).where(AssessmentImportRow.batch_id == data["id"])
    )
    log = db_session.scalar(select(StudentAgeChangeLog))
    assert (log.student_id, log.old_age, log.new_age) == (student_id, 12, 13)
    # 出处指向**这一行**，不只是「有个人改过」：同一名学生一年里可能被改两次
    assert (log.assessment_import_batch_id, log.assessment_import_row_id) == (data["id"], row_id)
    assert log.changed_by == db_session.scalar(
        select(UserAccount.id).where(UserAccount.account == "13800000001")
    )
    assert "操作员选了覆盖" in log.reason
    assert "年龄处置=覆盖学生当前年龄" in _batch_audit(db_session).detail


def test_saving_only_the_sitting_age_leaves_the_roster_alone(client, db_session):
    """「只保存本次测评年龄」：名册不动，**而这一场按文件里的数记**。

    这一支存在的理由正是「名册上那个数不会自己变」（§1）：学校去年那次普查的文件里
    他是 13 岁，今年名册上还写着 12——把它记成 12 会让这一场的数据与文件对不上，
    而名册又确实是学校维护的那一份，不该被一次导入改掉。
    """
    data, headers = _age_mismatch(client)
    result = _commit(
        client, headers, data["id"], resolution="overwrite", age_resolution="session_only"
    ).json()["data"]
    assert (result["created"], result["age_updated"]) == (1, 0)
    assert result["age_resolution"] == "session_only"

    db_session.expire_all()
    assert _roster_age(db_session) == 12, "名册一个字都不该动"
    assert _imported_session(db_session).age_at_test == 13, "★ 与「保留系统年龄」唯一的差别"
    assert db_session.scalar(select(func.count(StudentAgeChangeLog.id))) == 0
    assert db_session.get(AssessmentImportBatch, data["id"]).allow_age_overwrite is False
    assert "年龄处置=只保存本次测评年龄" in _batch_audit(db_session).detail


def test_an_unknown_age_resolution_is_refused_before_anything_is_written(client, db_session):
    """取值不合法时回统一封装里的那句中文，而且**任何写入之前**。

    用 `str` 而不是 `Literal[...]` 就是为了这一句（见 `AssessmentImportCommitRequest`）：
    `Literal` 会被 FastAPI 自己拦下，前端拿到的是框架的 422 结构，`api.ts` 取不到
    `error.message`，只能显示一句笼统的失败。
    """
    data, headers = _age_mismatch(client)
    refused = _commit(
        client, headers, data["id"], resolution="overwrite", age_resolution="whatever"
    )
    assert refused.status_code == 422, refused.text
    message = refused.json()["error"]["message"]
    assert message == AGE_RESOLUTION_HINT
    # 这句话得**列出三个选项的中文**，否则操作员拿到它也不知道该填什么
    for option in ("保留系统年龄", "覆盖学生当前年龄", "只保存本次测评年龄"):
        assert option in message

    db_session.expire_all()
    assert _roster_age(db_session) == 12
    assert db_session.scalar(select(func.count(AssessmentSession.id))) == 0


# --------------------------------------------------------------------------
# 第 5 期 C：逐行处置（§18.6 的「逐行处理」）
#
# 与整批提交的关系是**逐行覆盖整批**：处置过的行不再受那一次整批选择左右。
# 一次普查两百行，操作员不会为了那三条年龄不符把整批重来一遍。而这个端点
# **不写任何测评记录**——真正的落库全部发生在提交那一刻。
# --------------------------------------------------------------------------


def _detail_by_no(client, headers, batch_id: int) -> dict[int, dict]:
    """导入明细那一侧的行，按文件里的行号索引。

    与 `_by_no` 分开是因为两个端点的**外层形状不同**（上传与 `/rows` 一个发
    `rows`、一个发 `items`），而列本身逐字相同——所以这里只换那一层壳，
    不去动 `_by_no`（它伺候的是上传那一次）。
    """
    response = client.get(f"/api/v1/assessment-imports/{batch_id}/rows", headers=headers)
    assert response.status_code == 200, response.text
    return {row["row_no"]: row for row in response.json()["data"]["items"]}


def test_import_row_detail_supports_server_side_pagination(client, db_session):
    """明细不再只有「前 200 条」：`total` 始终是全量，`limit/offset` 可遍历全部行。"""
    headers = admin(client)
    add_students(client, headers, [("S-PAGE-1", "甲同学", "男", 12), ("S-PAGE-2", "乙同学", "女", 13)])
    grader = counselor(client)
    batch = _preview_data(
        client,
        grader,
        _csv([_row("甲同学", 1, 12, 1, 4), _row("乙同学", 2, 13, 1, 4)]),
    )

    first = client.get(
        f"/api/v1/assessment-imports/{batch['id']}/rows?limit=1&offset=0", headers=grader
    ).json()["data"]
    second = client.get(
        f"/api/v1/assessment-imports/{batch['id']}/rows?limit=1&offset=1", headers=grader
    ).json()["data"]

    assert first["total"] == second["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["row_no"] == 2
    assert second["items"][0]["row_no"] == 3


def _resolve(client, headers, row_id, **body):
    return client.patch(
        f"/api/v1/assessment-import-rows/{row_id}/resolve", headers=headers, json=body
    )


def test_a_row_level_choice_overrides_the_batch_choice(client, db_session):
    """整批选「放弃」，而其中一行被逐行处置成「覆盖」——那一行照写。

    两行的读数要**同时**断言：只看「那一行写进去了」的话，一个把整批选择丢掉、
    全批照写的实现在这条用例上也是绿的，而那个实现会让「放弃」这个动作失效。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    grader = counselor(client)
    data = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 2, 13, 1, 4), _row("钱同学", 1, 14, 1, 4)]),
    )
    assert data["row_counts"] == {"ready": 0, "needing_resolution": 2, "conflict": 0, "error": 0}

    first = _by_no(data)[2]
    resolved = _resolve(client, grader, first["id"], resolution="overwrite")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["data"] == {
        "row_id": first["id"],
        "row_no": 2,
        "batch_no": data["batch_no"],
        "resolution": "overwrite",
        "age_resolution": None,
        # 第 6 期加的这一项留空：这一行是**年龄**冲突，不是来源冲突，
        # 而「这一行以哪一份为准」这个问题没有在它身上问过（§18.8）。
        # 留空是「没问过」，不是「选了某个默认」——同 `age_resolution` 那条。
        "conflict_resolution": None,
        "match_status": "AGE_CONFLICT",
    }

    result = _commit(client, grader, data["id"], resolution="skip").json()["data"]
    assert (result["created"], result["updated"], result["skipped"]) == (1, 0, 1)
    assert result["resolved_rows"] == 1
    # 那一行选的是「覆盖」，所以名册上年龄跟着改了；另一行跟着整批的「放弃」走
    db_session.expire_all()
    assert _roster_age(db_session, "S701") == 13
    assert _roster_age(db_session, "S702") == 13, "没处置的那一行不该被动过"

    rows = {
        row.row_no: row
        for row in db_session.scalars(
            select(AssessmentImportRow).where(AssessmentImportRow.batch_id == data["id"])
        )
    }
    assert (rows[2].processing_status, rows[2].resolution) == ("CREATED", "overwrite")
    assert (rows[3].processing_status, rows[3].resolution) == ("SKIPPED", "skip")
    assert rows[3].resolved_by is None, "整批选择不是逐行处置，不该留下「谁处置的」"
    assert "resolved_rows=1" in _batch_audit(db_session).detail


def test_a_row_level_choice_writes_no_measurement_records(client, db_session):
    """处置只写**决定**，不写后果——理由与预览那一层相同。

    一个动作在它真的发生之前，库里不该出现它的后果。所以处置完之后那一行仍然在
    「待确认」那一档里（`match_status` 不变）、批次仍然 `PREVIEW`、会话一条都没有。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row("赵同学", 2, 13, 1, 4)]))
    row = data["rows"][0]
    before = row["message"]

    response = _resolve(client, grader, row["id"], resolution="overwrite", age_resolution="session_only")
    assert response.status_code == 200, response.text

    db_session.expire_all()
    stored = db_session.get(AssessmentImportRow, row["id"])
    assert (stored.resolution, stored.age_resolution) == ("overwrite", "session_only")
    assert stored.session_id is None
    assert (stored.resolved_by is not None, stored.resolved_at is not None) == (True, True)
    batch = db_session.get(AssessmentImportBatch, data["id"])
    assert (batch.status, batch.created_rows) == ("PREVIEW", 0)
    assert batch.error_rows == 0
    assert db_session.scalar(select(func.count(AssessmentSession.id))) == 0
    # 逐行明细上那一句「为什么进不去」不因为有人处置过就消失（它是文件层面的结论）
    assert _detail_by_no(client, grader, data["id"])[2]["message"] == before

    # 审计在返回数据之前写（§8），而它说的是「哪一行」：`resource_id` 是行 id，
    # `detail` 带批次号与行号，所以这一条轨迹指得回具体那一条记录。用
    # `ASSESSMENT_TASK` 作 resource_type 的话一场任务下几百行都挤在同一个 id 上
    log = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "处置导入记录").order_by(AuditLog.id.desc())
    )
    assert (log.resource_type, log.resource_id) == ("ASSESSMENT_IMPORT_ROW", str(row["id"]))
    assert f"batch={data['batch_no']}, row=2" in log.detail
    assert "match_status=AGE_CONFLICT" in log.detail
    assert "resolution=overwrite" in log.detail
    assert "age_resolution=session_only" in log.detail

    # 提交不必再回答一次整批问题：处置过的行已经答过了
    result = _commit(client, grader, data["id"]).json()["data"]
    assert (result["created"], result["resolved_rows"]) == (1, 1)
    # 而这一行选的是「只保存本次测评年龄」，于是名册没动、这一场按 13 记
    db_session.expire_all()
    assert _roster_age(db_session) == 12
    assert _imported_session(db_session).age_at_test == 13


def test_rows_no_choice_can_save_are_refused(client, db_session):
    """进不去的行一律 422，任何处置都改变不了它；匹配上的行不需要确认。

    这两档的措辞都得说清**为什么**：前者是「你再怎么选也写不进去」——`AMBIGUOUS`
    正是这么发现的（第 4 期把它从「要拍板」挪到「进不去」）；后者是「点提交就行」，
    免得操作员在一个不需要决定的界面上找一个决定。

    两个被拒的请求**一个字都没写**：没有 `resolved_by`，也就没有轨迹说「有人处置过」。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    data = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 2, 12, 1, 4), _row("压根不存在", 2, 12, 1, 4)]),
    )
    matched, missing = _by_no(data)[2], _by_no(data)[3]
    assert (matched["match_status"], missing["match_status"]) == ("MATCHED", "NOT_FOUND")

    unneeded = _resolve(client, grader, matched["id"], resolution="skip")
    assert unneeded.status_code == 422, unneeded.text
    assert unneeded.json()["error"]["message"] == "第 2 行不需要确认，直接提交即可"

    refused = _resolve(client, grader, missing["id"], resolution="overwrite")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["message"] == (
        f"第 3 行无法导入，任何处置都改变不了它：{missing['message']}"
    )

    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count(AssessmentImportRow.id)).where(
                AssessmentImportRow.resolved_by.isnot(None)
            )
        )
        == 0
    )
    assert (
        db_session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "处置导入记录"))
        == 0
    )


def test_an_age_choice_needs_an_age_conflict(client, db_session):
    """没有年龄冲突的行不能选年龄处置——静默忽略会留下一条谁也解释不了的决定。

    用 `DUPLICATE`（本月已导过一次）而不是 `MATCHED` 来钉这一条：`MATCHED` 会在更
    前面那一道「不需要确认」上就被拦下来，于是这条用例会在**不是它要测的那个理由**
    上变绿。而要拍板的行本身是处置得了的，这一点也要一起断言。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    _import(client, grader, [_row("赵同学", 2, 12, 1, 4)])

    again = _preview_data(client, grader, _csv([_row("赵同学", 2, 12, 1, 4)]))
    assert _statuses(again) == ["DUPLICATE"], again["rows"]
    row = again["rows"][0]

    refused = _resolve(client, grader, row["id"], age_resolution="overwrite")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["message"] == "第 2 行没有年龄冲突，不需要选择年龄处置"
    # 它真正要拍板的那个问题照旧答得了
    assert _resolve(client, grader, row["id"], resolution="overwrite").status_code == 200


def test_a_committed_batch_cannot_be_resolved_row_by_row(client, db_session):
    """提交之后这一批不再是预览：单行处置回 422 并指出出路（重新上传）。"""
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    grader = counselor(client)
    result = _import(client, grader, [_row("赵同学", 2, 12, 1, 4)])
    row_id = db_session.scalar(
        select(AssessmentImportRow.id).where(AssessmentImportRow.batch_id == result["batch_id"])
    )

    refused = _resolve(client, grader, row_id, resolution="skip")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["message"] == (
        "这一批已经提交过了，不能再改单行的处置。要重新导入请重新上传文件。"
    )


def test_a_row_outside_the_resolvers_scope_cannot_be_resolved(client, db_session):
    """范围在**处置时**也查一遍，与提交时那一道是两处。

    批次是共享的（`imported_by` 只是创建者），而账号的范围可以被改。少了这一句，
    一个把范围收窄过的人能给范围外的学生逐行下决定——提交时那一行被拦下来，于是
    那个**决定静静地不生效**，而屏幕上那一行看起来已经处置好了。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row("赵同学", 2, 13, 1, 4)]))
    row = data["rows"][0]
    assert row["matched_student_no"] == "S701"

    scoped = _student_scoped_counselor(
        client, db_session, account="13900000010", student_no="S702", display_name="只管一名学生的心理老师"
    )
    refused = _resolve(client, scoped, row["id"], resolution="overwrite")
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["message"] == "该学生不在你的数据范围内"
    # 被拒的读取与写入都不留痕（§9）：一条「有人处置过」会让轨迹反过来撒谎
    db_session.expire_all()
    assert db_session.get(AssessmentImportRow, row["id"]).resolved_by is None
    assert (
        db_session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "处置导入记录"))
        == 0
    )


# --------------------------------------------------------------------------
# 第 5 期 D：这场任务下没进得去的那几行（§18.10）
#
# §18.4 那条出路的最后一环：一份文件里有几行没匹配上时，那些行从此有了一个
# **可查的地方**（在它们所属的那场任务下面），而不是只活在那一次上传的返回值里。
# --------------------------------------------------------------------------


def _unmatched(client, headers, task_id: int) -> dict:
    response = client.get(
        f"/api/v1/assessment-tasks/{task_id}/unmatched-import-rows", headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_the_rows_that_did_not_get_in_are_listed_under_their_task(client, db_session):
    """三样都算「这一场没进得去」：名册上没有他、不在目标名单里、以及**被放弃的**。

    逐行明细与这一页必须是**同一条记录的同一种形状**——同一个屏幕上同一行长得不一样，
    是这一层最容易出的事，而两处各拼一份序列化就是两个定义。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    task = _task_with_targets(db_session, ["S701"])
    grader = counselor(client)
    data = _preview_data(
        client,
        grader,
        _csv(
            [
                _row("赵同学", 2, 12, 1, 4),
                _row("钱同学", 1, 13, 1, 4),
                _row("压根不存在", 2, 12, 1, 4),
            ]
        ),
        task_id=task.id,
    )
    assert [row["match_status"] for row in data["rows"]] == ["MATCHED", "OUT_OF_SCOPE", "NOT_FOUND"]
    assert _commit(client, grader, data["id"]).json()["data"]["created"] == 1

    unmatched = _unmatched(client, grader, task.id)
    assert [item["row_no"] for item in unmatched["items"]] == [3, 4]
    assert unmatched["total"] == 2
    assert unmatched["reason_counts"] == {"NOT_FOUND": 1, "OUT_OF_SCOPE": 1}

    # ★ 与 `/rows` 那一侧**逐字相同**：同一条记录在两个屏幕上不许有两种形状
    detail = client.get(
        f"/api/v1/assessment-imports/{data['id']}/rows", headers=grader
    ).json()["data"]
    by_id = {row["id"]: row for row in detail["items"]}
    assert unmatched["items"] == [by_id[item["id"]] for item in unmatched["items"]]
    # 匹配上的那一行不在这一页上：这一页回答的是「谁没进来」
    assert 2 not in [item["row_no"] for item in unmatched["items"]]


def test_a_row_abandoned_at_commit_still_counts_as_not_in(client, db_session):
    """提交时被放弃的行也在这一页上：它匹配得上，而这一批**没有**把它写进去。

    对这场任务而言这与「没匹配上」是同一件事——这个学生的这一场缺着。少了这条判据，
    一个「选了放弃」的批次在这一页上会一条都不剩，而这一页的读者正是要找出「这场
    任务还有谁缺着」。它的 `processing_status` 是 `SKIPPED`，界面上照这一列分岔。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    task = _task_with_targets(db_session, ["S701"])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row("赵同学", 2, 13, 1, 4)]), task_id=task.id)
    assert _statuses(data) == ["AGE_CONFLICT"]
    result = _commit(client, grader, data["id"], resolution="skip").json()["data"]
    assert (result["created"], result["skipped"]) == (0, 1)
    assert result["task_id"] == task.id, "这一批绑着任务，所以它指得回那一场"

    unmatched = _unmatched(client, grader, task.id)
    assert unmatched["total"] == 1
    assert unmatched["reason_counts"] == {"AGE_CONFLICT": 1}
    item = unmatched["items"][0]
    assert (item["match_status"], item["processing_status"]) == ("AGE_CONFLICT", "SKIPPED")
    # 这一行是**认得出是谁**的（与 `NOT_FOUND` 的区别就在这里），所以出路是补那个学生的
    # 名册年龄再重导，而不是去补名册
    assert item["matched_student_no"] == "S701"
    assert item["matched_name"] is not None, "认得出是谁，正是它与 NOT_FOUND 的区别"


def test_the_batch_number_travels_with_each_row(client, db_session):
    """一场任务下可以有好几批（先初一、隔几天再初二），而 `row_no` 是**批内**的编号。

    所以「第 2 行」这三个字单独拿出来是**指不了人的**：两批各有一个第 2 行。那一屏
    又是跨批的（批次从新到旧排下来），少了这一列，操作员看到的是 2、2 这样的行号，
    读起来像「有一行重了」或者「有一行丢了」——而这一屏的全部意义正是让他照着行号
    回那份文件里找人。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    task = _task_with_targets(db_session, ["S701", "S702"])
    grader = counselor(client)
    first = _preview_data(
        client,
        grader,
        _csv([_row("压根不存在", 2, 12, 1, 4)]),
        task_id=task.id,
        filename="初一.csv",
        batch_name="初一普查",
    )
    assert _commit(client, grader, first["id"]).status_code == 200
    second = _preview_data(
        client,
        grader,
        _csv([_row("也没有这个人", 2, 12, 1, 4)]),
        task_id=task.id,
        filename="初二.csv",
        batch_name="初二普查",
    )
    assert _commit(client, grader, second["id"]).status_code == 200
    assert first["batch_no"] != second["batch_no"], "两个文件就是两批"

    unmatched = _unmatched(client, grader, task.id)
    assert [item["row_no"] for item in unmatched["items"]] == [2, 2], "两批的行号各自从 2 开始"
    assert [item["batch_no"] for item in unmatched["items"]] == [
        second["batch_no"],
        first["batch_no"],
    ], "批次从新到旧，而每一行都带着自己那一批的号"
    assert [item["batch_id"] for item in unmatched["items"]] == [second["id"], first["id"]]


def test_the_unmatched_counts_are_not_scope_filtered_but_the_rows_are(client, db_session):
    """那三个数是**整场任务**的，列表是**读者看得见**的——两处口径故意不同。

    与 `list_import_rows` 那一侧逐字同形（那里也是「明细随范围缩、`row_counts` 不缩」）：
    这两个数坐在「这场任务还缺谁」这个判断上，缩了会让操作员以为问题比实际小。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12), ("S702", "钱同学", "女", 13)])
    task = _task_with_targets(db_session, ["S702"])
    grader = counselor(client)
    data = _preview_data(
        client,
        grader,
        _csv([_row("赵同学", 2, 12, 1, 4), _row("压根不存在", 2, 12, 1, 4)]),
        task_id=task.id,
    )
    assert [row["match_status"] for row in data["rows"]] == ["OUT_OF_SCOPE", "NOT_FOUND"]
    _commit(client, grader, data["id"])

    # 先证明有东西可扫：全校范围的那位看得见两行
    assert _unmatched(client, grader, task.id)["total"] == 2

    # 一名只带 S702 范围的读者：S701 那一行超出了他的范围（他没匹配上、但认得出是谁），
    # 而没匹配上的那一行**没有学生可归**（`student_id` 是 NULL），所以照旧看得见
    scoped = _student_scoped_counselor(
        client, db_session, account="13900000011", student_no="S702", display_name="只管一名学生的心理老师"
    )
    unmatched = _unmatched(client, scoped, task.id)
    assert [item["match_status"] for item in unmatched["items"]] == ["NOT_FOUND"]
    assert unmatched["total"] == 1
    assert unmatched["reason_counts"] == {"NOT_FOUND": 1, "OUT_OF_SCOPE": 1}, (
        "那两个数是整场任务的：范围外的行在这一页上也要被数进去"
    )


def test_the_unmatched_endpoint_needs_both_gates(client, db_session):
    """两道门槛，与目标学生名单逐字同一形状（§4 / §22）。

    管理员挡在**任务**那一层（`ensure_task_reader` 本来就不放行它：测评任务不是能力、
    是角色），德育领导读得到——这一页属于「这场普查组织得怎么样」，正是监督口径。
    任务不存在时是 404 而不是空列表：空列表会让操作员以为「这场任务没有未匹配的行」。
    """
    headers = admin(client)
    add_students(client, headers, [("S701", "赵同学", "男", 12)])
    task = _task_with_targets(db_session, ["S701"])
    grader = counselor(client)
    data = _preview_data(client, grader, _csv([_row("压根不存在", 2, 12, 1, 4)]), task_id=task.id)
    _commit(client, grader, data["id"])

    admin_headers = admin(client)
    forbidden = client.get(
        f"/api/v1/assessment-tasks/{task.id}/unmatched-import-rows", headers=admin_headers
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["error"]["message"] == "当前角色无权执行该操作"

    leader = auth_headers(client, "leader", "13800000002")
    assert _unmatched(client, leader, task.id)["total"] == 1

    missing = client.get(
        "/api/v1/assessment-tasks/999999/unmatched-import-rows", headers=grader
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["message"] == "测评任务不存在"


# --------------------------------------------------------------------------
# 第 5 期 E：镜像（后端的那几张中文表与 `labels.ts`）
# --------------------------------------------------------------------------


LABELS_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "services" / "labels.ts"


def test_the_dimension_labels_are_a_mirror_of_the_frontend():
    """八个维度的中文列名，后端与 `labels.ts` 是**同一批字**。

    两边没法互相 import（后端读不到 `.ts`），所以 `DIMENSION_BY_LABEL` 是一份镜像
    ——镜像就会漂移，而漂移的后果是**静默的**：学校照模板填的「对人焦虑」在数据库里
    认不出，那一维直接留空（`_parse_dimension_scores` 跳过认不出的格子），而汇总文件
    里缺一维不会让哪一页看起来坏掉。

    所以这里把那份 TypeScript 当**数据源**读进来比对（与
    `test_export_labels_match_frontend.py` 同一办法）：在测试里再抄一遍中文，就有了
    第三种改错的方式。取值还要落在 `scale_import_service.DIMENSION_CODES` 里——一个
    字母写错的码会让维度分挂在一个谁也不认识的地方。
    """
    source = LABELS_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const DIMENSION_LABELS: Record<string, string> = \{(.*?)\n\}", source, re.S
    )
    assert match, "labels.ts 里没有 DIMENSION_LABELS"
    frontend = dict(re.findall(r"([A-Z_]+):\s*'([^']*)'", match.group(1)))
    assert frontend, "抓出来的表是空的——正则坏了，这一条就成了空转"

    # 中文 → 码 与 码 → 中文 是同一条关系的两个方向，逐字比
    assert DIMENSION_BY_LABEL == {label: code for code, label in frontend.items()}
    assert set(DIMENSION_BY_LABEL.values()) == DIMENSION_CODES
