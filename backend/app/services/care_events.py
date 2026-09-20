"""关怀档案的生命周期事件 —— 唯一的写入方与唯一的码表。

**它为什么是一个独立模块，而不是 `care_service` 里的几个函数：**
`care_service` 已经 import 了 `assessment_service`（`latest_session` /
`now_utc_naive` / `session_history_order`），而 `assessment_service.open_or_reuse_care_case`
**开档时也要写一条事件**。把助手放在 `care_service` 里就会让 `assessment_service`
反过来 import 它，那就是一个循环导入。所以这里只依赖 `models`。

**事件与 `audit_log` 不是一回事**（`models/care.py` 的 docstring 里写着同一条）：
审计回答「谁在什么时候调了哪个接口」，这张表回答「这份档案经历了什么」。同一次操作
产生两条，而两条的读者、保留期、权限都不同——审计是系统级轨迹，事件是**这份档案的病历**。

**记哪些动作，判据只有一句：这个动作改变了这份档案的 `status` 或 `owner_id`
（或者它是这条生命的起点）。** 按这条：

| 记 | 不记 |
|---|---|
| 开档、复核、跟进、家庭回访、复测计划、关档、重开、转派 | —— |

反过来说，家庭回访与复测**也要记**，因为它们都会把 `status` 推回 `FOLLOWING` /
`OBSERVING`（见 `care_service` 里那四个入口）。

`confirmed_facts` 这一列（DDL 里就有的 `Text`）**记的是复核与跟进的事实记录**——
时间线上只写「人工复核 · 张三 · 09-19」而下面没有一行事实，这条病历就只剩一串动词，
读不出发生过什么。**唯一刻意留空的是家庭回访那一条**：§16.6 点名的四类不得留存的内容
里就有「家庭回访正文」，它留在 `family_contact_record.confirmed_facts` 上，
那一行才是它的家（同一条规定之下，`close_note` 也留在档案自己那一列上）。
"""

from sqlalchemy.orm import Session

from app.models.care import CareCaseEvent, StudentCareCase

# `care_case_event.reason` 是 String(255)。详见 `record_case_event` 里那段注释。
REASON_COLUMN_LIMIT = 255

# --- 码表 ---------------------------------------------------------------
# 与 `frontend/src/services/labels.ts` 的 `CARE_EVENT_LABELS` 一一对应。
# 这是 §3 的**第一面**：后端发出的码，前端那张表必须认得。

CASE_OPENED = "CASE_OPENED"
MANUAL_REVIEWED = "MANUAL_REVIEWED"
FOLLOW_UP_ADDED = "FOLLOW_UP_ADDED"
FAMILY_CONTACT_ADDED = "FAMILY_CONTACT_ADDED"
RETEST_PLANNED = "RETEST_PLANNED"
CASE_CLOSED = "CASE_CLOSED"
CASE_REOPENED = "CASE_REOPENED"
OWNER_ASSIGNED = "OWNER_ASSIGNED"

ALL_EVENT_TYPES: tuple[str, ...] = (
    CASE_OPENED,
    MANUAL_REVIEWED,
    FOLLOW_UP_ADDED,
    FAMILY_CONTACT_ADDED,
    RETEST_PLANNED,
    CASE_CLOSED,
    CASE_REOPENED,
    OWNER_ASSIGNED,
)


def record_case_event(
    db: Session,
    care_case: StudentCareCase,
    event_type: str,
    operator_id: int | None,
    *,
    from_status: str | None = None,
    to_status: str | None = None,
    reason: str | None = None,
    confirmed_facts: str | None = None,
) -> CareCaseEvent:
    """往这份档案上追加一条事件。**只追加，不修改**（表上也没有 `updated_at`）。

    `student_id` 取自 `care_case.student_id` 而不是让调用方传：复合外键
    `care_case_event_fk_case_student` 断言这两个字段指向同一名学生，而让调用方
    各传一个就等于把那条约束的输入交给调用方——写错时数据库报的是一句英文
    (`1452`)，而正确的做法是这里根本没有第二个输入。

    `from_status` / `to_status` 两个都可空，而**转派那一条是唯一两个都空的事件**
    （它不改状态，只改归属）——`reason` 里写着新负责人的名字。
    """
    if event_type not in ALL_EVENT_TYPES:
        # 认不出的码当场抛，不静默写进去：一个没被翻译过的码会在时间线上
        # 显示成 `CASE_OPEND` 这种原文，而它看起来像一条正常的事件。
        raise ValueError(f"unknown care case event type: {event_type!r}")

    event = CareCaseEvent(
        care_case_id=care_case.id,
        student_id=care_case.student_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        operator_id=operator_id,
        # `reason` 是 String(255)，而各调用方递给它的东西长度不一：`record_type`
        # 与 `channel` 是 64 字的枚举码、`close_reason` 是 128 字、而
        # `ReopenCaseRequest.reason` 允许 5000 字。**写入方保证写出来的东西合法**
        # （§18 那条反复出现的教训）——不然超长时 MySQL 严格模式回的是
        # `1406 Data too long for column 'reason'`，离「事件里那句话太长了」隔着一个列名。
        #
        # 截断只影响这个**摘要**格；原文一个字都不丢，它留在原来那张表自己那一列上
        # （家庭回访正文留在 `family_contact_record.confirmed_facts`——§16.6 明令
        # 事件里不许存它，所以那一处连摘要都不给，见调用点）。
        reason=(reason[:REASON_COLUMN_LIMIT] if reason else None),
        confirmed_facts=confirmed_facts,
    )
    db.add(event)
    return event
