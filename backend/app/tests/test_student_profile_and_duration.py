"""学生属性（性别 / 年龄）与测评作答时长。

两个容易写对却看不出来的地方，都在这里钉住：

* **年龄是名册上的一个数，原样存下来。** 2026-09-17 之前它存的是出生日期、读取时现算，
  理由是过完生日那一刻存下来的年龄就错了；学校手上只有年龄，于是反过来（迁移 0011）。
  代价是它不会自己变，所以这里断言的是「导入的整数原样落库」，不是某个人今年的岁数。
* **用时是「首次作答 → 交卷」，而且只在提交时写一次。** `answered_at` 通过 HTTP
  拿不到（学生不能给自己倒填时间戳），所以只能在这一层驱动——和 `seed_demo` 走同
  一扇门。用「现在减一下」来断言的测试会随运行时长漂移，这里断言的是定义本身。
"""

import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.account import UserAccount
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
)
from app.models.organization import Student
from app.models.scale import ScaleQuestion
from app.services import assessment_service
from app.tests.conftest import auth_headers
from app.tests.factories import make_target
from app.tests.test_assessment_api import create_student_session, save_answers
from app.tests.test_audit_export_api import create_case_with_followup, export_and_download


def _care_case_export_csv(client, headers: dict, **payload) -> list[list[str]]:
    """受控导出走完两跳，拿到**文件里的**行。

    2026-09-19 阶段 8 起导出是两跳（建作业 → `/export-jobs/{id}/download`）。这个
    包装是必须的：建作业那一步回的是作业载荷 JSON，而那份 JSON 里带着 `columns`
    （字段白名单摊平之后就是表头名），所以 `_csv_rows(response.text)` 会在一段 JSON
    上解析出一行，`"性别" in rows[0]` 这类断言**照样能通过**——它命中的是载荷里那份
    列名清单，不是文件的内容。比红更糟的是这种绿。
    """
    created = client.post(
        "/api/v1/care-cases/export", headers=headers, json={"purpose": "阶段工作统计", **payload}
    )
    assert created.status_code == 200, created.text
    downloaded = client.get(
        f"/api/v1/export-jobs/{created.json()['data']['id']}/download", headers=headers
    )
    assert downloaded.status_code == 200, downloaded.text
    return _csv_rows(downloaded.text)


def _seeded_student(db_session) -> Student:
    return db_session.scalar(select(Student).where(Student.student_no == "S001"))


def _seeded_account(db_session) -> UserAccount:
    return db_session.scalar(select(UserAccount).where(UserAccount.account == "S001"))


def _answer_stamp(db_session, session_id: int, question_no: int):
    """该题落库的作答时刻与答案。"""
    return db_session.execute(
        select(AssessmentAnswer.answered_at, AssessmentAnswer.answer)
        .join(ScaleQuestion, ScaleQuestion.id == AssessmentAnswer.question_id)
        .where(
            AssessmentAnswer.session_id == session_id,
            ScaleQuestion.question_no == question_no,
        )
    ).one()


def _submit_with_stamps(db_session, *, first_answer_at: datetime, step_seconds: int = 0):
    """走 service 层交一份 100 题的答卷，作答时刻由调用方给定。

    直接读 `session.duration_seconds`（而不是走 HTTP）是因为要控制 `answered_at`：
    `save_answer` 的 `answered_at` 是关键字参数、不在 `SaveAnswerRequest` 里，
    所以学生对它没有写入能力，这是有意的。
    """
    account = _seeded_account(db_session)
    task = db_session.scalar(select(AssessmentTask))
    session = assessment_service.create_or_get_session(db_session, account, task.id)
    for offset, question_no in enumerate(range(1, 101)):
        assessment_service.save_answer(
            db_session,
            account,
            session.id,
            question_no,
            "NO",
            answered_at=first_answer_at + timedelta(seconds=offset * step_seconds),
        )
    assessment_service.submit_session(db_session, account, session.id, f"test-{session.id}")
    return session


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


# --------------------------------------------------------------------------
# 年龄：名册上的一个整数，原样存下来
# --------------------------------------------------------------------------


def test_roster_carries_gender_and_a_stored_age(client, db_session):
    student = _seeded_student(db_session)
    # 种子必须真的给了值，否则下面的比对会因为两边都是 None 而通过
    assert student.gender == "MALE"
    assert student.age == 13

    headers = auth_headers(client, "admin", "admin")
    items = client.get("/api/v1/students", headers=headers).json()["data"]["items"]
    row = next(item for item in items if item["student_no"] == "S001")
    assert row["gender"] == student.gender
    # 存下来的就是发出去的：0011 之后没有「现算」这一步了
    assert row["age"] == student.age


def test_age_is_none_when_the_roster_does_not_carry_one(client, db_session):
    """名册早于这两个字段，学校也可能不填——没填是「不知道」，不是「0 岁」。
    种子里的 S001 两项都有，所以这里单独放一个都没有的学生。"""
    base = _seeded_student(db_session)
    db_session.add(
        Student(
            student_no="S100",
            name="无年龄同学",
            masked_name="无年龄同学",
            school_id=base.school_id,
            grade_id=base.grade_id,
            class_id=base.class_id,
        )
    )
    db_session.commit()

    headers = auth_headers(client, "admin", "admin")
    items = client.get("/api/v1/students", headers=headers).json()["data"]["items"]
    row = next(item for item in items if item["student_no"] == "S100")
    assert row["gender"] is None
    assert row["age"] is None


# --------------------------------------------------------------------------
# 用时：首次作答 → 交卷
# --------------------------------------------------------------------------


def test_duration_is_none_when_nothing_was_answered():
    assert assessment_service.session_duration_seconds([], datetime(2026, 9, 16, 10, 0)) is None


def test_duration_is_measured_from_the_first_answer_not_from_now(db_session):
    now = assessment_service.now_utc_naive()
    started = now - timedelta(seconds=600)
    session = _submit_with_stamps(db_session, first_answer_at=started, step_seconds=5)

    # 与定义逐位相等，而不是「大约 600」——最后 100 题落在 600 秒之前，
    # 如果实现改成从最后一题起算，这个断言会立刻掉下来
    assert session.duration_seconds == int((session.submitted_at - started).total_seconds())
    assert session.duration_seconds >= 600


def test_re_answering_a_question_does_not_move_its_first_answer_stamp(db_session):
    """学生回头改第 1 题，不能把自己记录的作答时长抹掉。

    答题页有「返回检查」和「定位未答」，重答是正常操作；`answered_at` 若是重答时
    覆盖，「首次作答」就一路后移，一道题一道题改过去最后记成 0 秒。
    """
    account = _seeded_account(db_session)
    task = db_session.scalar(select(AssessmentTask))
    session = assessment_service.create_or_get_session(db_session, account, task.id)
    first = assessment_service.now_utc_naive() - timedelta(seconds=900)
    assessment_service.save_answer(db_session, account, session.id, 1, "NO", answered_at=first)
    for question_no in range(2, 101):
        assessment_service.save_answer(
            db_session, account, session.id, question_no, "NO",
            answered_at=first + timedelta(seconds=question_no),
        )
    # 学生回头把第 1 题改成「是」——答案要改，时间戳不该动
    assessment_service.save_answer(
        db_session, account, session.id, 1, "YES", answered_at=assessment_service.now_utc_naive()
    )
    assessment_service.submit_session(db_session, account, session.id, "retake")

    stamp, answer = _answer_stamp(db_session, session.id, 1)
    assert answer == "YES", "重答本身必须写进去"
    assert stamp == first, "重答不得改写首次作答时刻"
    assert session.duration_seconds >= 900


def test_a_future_answer_stamp_is_clamped_to_zero_not_negative(db_session):
    """负数是荒谬值（时钟回拨 / 种子传错），不是一次测量，所以夹到 0。
    上界**不**夹：跨天续答真的就是那么多秒，见 session_duration_seconds 的说明。"""
    session = _submit_with_stamps(
        db_session, first_answer_at=assessment_service.now_utc_naive() + timedelta(hours=1)
    )
    assert session.duration_seconds == 0


def test_a_reset_retake_re_stamps_the_submission(client, db_session):
    """重考后必须重新打上提交事实。

    `/reset` 清空答案、`submitted_at` 与用时，但**不删结果行**（删了会抹掉审计与
    人工复核指向的评分事实）。于是再次提交走的是幂等早返回那条路——如果那里不补
    打时间戳，重考完的会话会永远没有用时，而注释还写着「下次提交会重算」。
    """
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id)
    first = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert first.status_code == 200
    assert first.json()["data"]["duration_seconds"] is not None

    reset = client.post(f"/api/v1/assessment-sessions/{session_id}/reset", headers=headers)
    assert reset.status_code == 200
    # expire_on_commit=False 的会话里，属性不会自己刷新
    db_session.expire_all()
    session = db_session.get(AssessmentSession, session_id)
    assert session.submitted_at is None
    assert session.duration_seconds is None

    save_answers(client, headers, session_id)
    replay = client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)
    assert replay.status_code == 200
    data = replay.json()["data"]
    # 幂等保证没变：分数仍然是存下来的那一份
    assert data["result"] == first.json()["data"]["result"]
    # 但提交事实被补上了
    assert data["submitted_at"] is not None
    assert data["duration_seconds"] is not None


def test_student_history_shows_a_duration_for_submitted_sessions(client):
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id)
    client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)

    items = client.get("/api/v1/student/assessment-history", headers=headers).json()["data"]["items"]
    submitted = next(item for item in items if item["submitted_at"])
    assert submitted["duration_seconds"] is not None
    assert submitted["duration_seconds"] >= 0


# --------------------------------------------------------------------------
# 导入：性别与年龄是选填
# --------------------------------------------------------------------------


def test_import_writes_gender_and_age(client, db_session):
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name,性别,年龄\nS007,周同学,初二,803,女,13\n"
    preview = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    )
    data = preview.json()["data"]
    assert data["valid_count"] == 1

    commit = client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": data["batch_id"]},
    )
    assert commit.json()["data"]["created"] == 1

    db_session.expire_all()
    student = db_session.scalar(select(Student).where(Student.student_no == "S007"))
    # 中文写法归一化成编码；年龄原样落库，不做任何换算
    assert student.gender == "FEMALE"
    assert student.age == 13


def test_import_reads_an_age_written_with_the_character_sui(client, db_session):
    """「13岁」和「13」是同一个意思，照收。`所用时间` 那一列同样认 `5340秒`。"""
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name,性别,年龄\nS011,许同学,初三,901,男,14岁\n"
    data = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]
    assert data["valid_count"] == 1, data["rows"]

    client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": data["batch_id"]},
    )
    db_session.expire_all()
    assert db_session.scalar(select(Student).where(Student.student_no == "S011")).age == 14


def test_import_without_the_optional_columns_stores_nulls(client, db_session):
    """四列的老文件必须照旧能导入。性别/年龄一旦变成必填，
    这条和 test_student_import_api 里那三个用例会一起红。"""
    headers = auth_headers(client, "admin", "admin")
    csv_content = "student_no,name,grade,class_name\nS008,吴同学,初一,701\n"
    data = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]
    assert data["valid_count"] == 1

    client.post(
        "/api/v1/student-roster/import/commit",
        headers=headers,
        json={"batch_id": data["batch_id"]},
    )
    db_session.expire_all()
    student = db_session.scalar(select(Student).where(Student.student_no == "S008"))
    assert student.gender is None
    assert student.age is None


def test_import_reports_a_bad_gender_or_age_per_row(client):
    """写错的选填列要变成这一行的错误，而不是整份文件 500，
    也不能悄悄存成 NULL——那和「学校没填」长得一模一样。"""
    headers = auth_headers(client, "admin", "admin")
    csv_content = (
        "student_no,name,grade,class_name,gender,age\n"
        "S009,郑同学,初一,701,X,13\n"          # 性别认不出
        "S010,冯同学,初一,701,MALE,13岁半\n"   # 年龄认不出
        "S013,陈同学,初一,701,FEMALE,2013\n"   # 是整数，但那是出生年份：越界
    )
    data = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]

    assert data["valid_count"] == 0
    assert data["error_count"] == 3
    assert data["submittable_count"] == 0
    # 每行**恰好**一条错误：班级改成 701 就是为了让这几行不再顺带撞上校验收，
    # 否则「错的是性别那一格」这句话就没法从 error_count 上读出来。
    assert [len(row["errors"]) for row in data["rows"]] == [1, 1, 1]
    messages = [message for row in data["rows"] for message in row["errors"]]
    assert any("性别" in message for message in messages)
    # 年龄那一格的两条文案分开：读不出整数是一种，整数但越界是另一种（出生年份）
    assert messages.count("年龄应为 13 这样的整数") == 1
    assert messages.count("年龄 2013 超出 3-100 的范围") == 1


def test_a_template_still_headed_birth_date_is_rejected_with_the_new_column_name(client):
    """老模板（最后一列还叫「出生日期」）不能静默导成一册年龄全空的学生。

    DictReader 会把这一列读成一个没人认领的键，年龄于是整列是 NULL——和「学校没填」
    长得一模一样，而这次是**每一行**都那样。所以那一列只要还有值就当场报错。
    """
    headers = auth_headers(client, "admin", "admin")
    csv_content = (
        "student_no,name,grade,class_name,性别,出生日期\n"
        "S012,蒋同学,初一,701,女,2013-09-01\n"
    )
    data = client.post(
        "/api/v1/student-roster/import/preview",
        headers=headers,
        files={"file": ("students.csv", csv_content, "text/csv")},
    ).json()["data"]

    assert data["valid_count"] == 0
    assert data["submittable_count"] == 0
    assert data["rows"][0]["errors"] == ["「出生日期」列已改为「年龄」，请填 13 这样的整数"]


# --------------------------------------------------------------------------
# 导出：列对齐 + 取最新一次测评
# --------------------------------------------------------------------------


def test_export_gates_the_quasi_identifiers_behind_unmasking(client):
    """性别/年龄不进遮蔽版导出，用时进。

    「姓氏 + 性别 + 年龄 + 班级」在一个年级里近乎唯一，同一行还带着学号——
    把它们挂到遮蔽版上，遮蔽就名存实亡，而德育领导拿到 CONTROLLED_EXPORT 的
    是 PROGRESS_SUMMARY、在 /students 上本来就是 403（CLAUDE.md §4）。
    """
    headers = create_case_with_followup(client)
    masked = _care_case_export_csv(client, headers)
    named = _care_case_export_csv(client, headers, mask_names=False)

    assert "用时(秒)" in masked[0]
    assert "性别" not in masked[0] and "年龄" not in masked[0]
    assert "性别" in named[0] and "年龄" in named[0]


def test_export_columns_line_up_with_their_values(client):
    """表头和数据行是两条各自拼出来的列表，插列错位不会让任何一条断言变红——
    值只是整体挪一格。所以逐行比对长度，再确认已知值落在自己的表头下。"""
    headers = create_case_with_followup(client)
    rows = _care_case_export_csv(client, headers, mask_names=False)
    header, body = rows[0], rows[1:]
    assert body
    assert all(len(row) == len(header) for row in body)

    index = {name: position for position, name in enumerate(header)}
    row = next(item for item in body if item[index["学号"]] == "S001")
    # 表里出中文：这份 CSV 是下载给学校看的，`MALE` / `GENERAL_RANGE` 在这里没有人翻译。
    assert row[index["性别"]] == "男"
    assert row[index["年龄"]].isdigit()
    assert row[index["用时(秒)"]].isdigit()
    assert row[index["关注等级"]] == "一般观察"


def test_export_reads_the_latest_assessment_when_there_are_two(client, db_session):
    """一个学生有两次测评时，关注等级与用时必须来自**最新**那次。

    导出曾把该生的所有会话一起外连接进来，循环只留第一行，而连接没有任何排序——
    取值不确定，且会和个案详情页（显式 order_by(id desc)）对同一个人给出不同的
    关注等级。加「用时」会把这个问题从「取值不定」变成「明显自相矛盾」。
    """
    counselor = create_case_with_followup(client)  # 第一次：仅 85 题答「是」
    db_session.expire_all()
    first_session = db_session.scalar(
        select(AssessmentSession).order_by(AssessmentSession.id)
    )
    first_result = db_session.scalar(
        select(AssessmentResult).where(AssessmentResult.session_id == first_session.id)
    )
    assert first_result.total_level == "GENERAL_RANGE"

    # 第二个任务 → 第二次测评，分数落在重点关注的区间
    source = db_session.scalar(select(AssessmentTask))
    second_task = AssessmentTask(
        task_no="TASK-EXPORT-LATEST",
        name="导出取最新一次的用例",
        scale_id=source.scale_id,
        school_id=source.school_id,
        scope_type="SCHOOL",
        status="ACTIVE",
    )
    db_session.add(second_task)
    db_session.flush()
    make_target(db_session, _seeded_student(db_session), second_task)
    db_session.commit()

    student_headers = auth_headers(client, "student", "S001")
    created = client.post(
        "/api/v1/assessment-sessions", headers=student_headers, json={"task_id": second_task.id}
    )
    second_session_id = created.json()["data"]["id"]
    save_answers(client, student_headers, second_session_id, yes_numbers=set(range(1, 71)) | {85})
    submitted = client.post(
        f"/api/v1/assessment-sessions/{second_session_id}/submit", headers=student_headers
    )
    assert submitted.json()["data"]["result"]["total_level"] == "KEY_ATTENTION"

    rows = _care_case_export_csv(client, counselor)
    index = {name: position for position, name in enumerate(rows[0])}
    row = next(item for item in rows[1:] if item[index["学号"]] == "S001")
    assert row[index["关注等级"]] == "重点关注"


def test_task_completion_csv_header_matches_its_rows(client):
    headers, session_id = create_student_session(client)
    save_answers(client, headers, session_id)
    client.post(f"/api/v1/assessment-sessions/{session_id}/submit", headers=headers)

    # 任务列表与完成明细是心理老师与德育领导的面（2026-09-17 起管理员整块退出）。
    counselor = auth_headers(client, "counselor", "13800000001")
    task_id = client.get("/api/v1/assessment-tasks", headers=counselor).json()["data"]["items"][0]["id"]
    # 建作业 → 取文件（阶段 8：导出是两跳，字节只从 `/export-jobs/{id}/download` 出去）。
    created = client.post(
        f"/api/v1/assessment-tasks/{task_id}/completion/export",
        headers=counselor,
        json={"purpose": "完成情况核对"},
    )
    assert created.status_code == 200, created.text
    rows = _csv_rows(
        client.get(
            f"/api/v1/export-jobs/{created.json()['data']['id']}/download", headers=counselor
        ).text
    )
    header, body = rows[0], rows[1:]
    assert body
    assert all(len(row) == len(header) for row in body)

    index = {name: position for position, name in enumerate(header)}
    assert body[0][index["性别"]] == "男"
    assert body[0][index["状态"]] == "已完成"
    assert body[0][index["用时(秒)"]].isdigit()
