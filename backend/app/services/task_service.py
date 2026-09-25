import csv
import io
from datetime import datetime

from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount
from app.models.assessment import (
    PARTICIPATION_DISPOSITIONS,
    PARTICIPATION_REQUIRED,
    AssessmentResult,
    AssessmentSession,
    AssessmentTarget,
    AssessmentTask,
    AssessmentTaskScope,
    RiskEvent,
    effective_session_predicate,
    expected_participation_predicate,
)
from app.models.common import now_local_naive
from app.models.enums import RoleCode
from app.models.organization import ClassGroup, Grade, School, Student
from app.models.importing import AssessmentImportBatch
from app.models.care import ManualReview, StudentCareCase
from app.models.scale import AssessmentScale
from app.security.data_scope import ensure_student_in_scope, student_scope_predicate
from app.security.permissions import (
    CONTROLLED_EXPORT,
    MANAGE,
    ORG_ACCOUNT,
    PROGRESS_SUMMARY,
    PSYCH_SUMMARY,
    READ_BASIC,
    READ_SUMMARY,
    SCOPED,
    STUDENT_PSYCH_DETAIL,
    scope_allows,
)
from app.services.export_document import ExportDocument
from app.services.export_labels import (
    gender_label,
    level_label,
    participation_label,
    target_status_label,
)
from app.services.numbering import insert_with_unique_number
from app.services.target_snapshot import target_snapshot


def ensure_task_writer(user: UserAccount) -> None:
    """测评任务是学校业务，写权归心理老师。

    RULE_CONFLICT (re-opened and re-resolved 2026-09-17): 2026-09-16 那版裁决按
    `docs/phase0_rule_freeze.md` 的「管理员管理任务」把写权留给了 ADMIN。用户指出
    那句话本身是错的——系统管理员只关注系统级配置，测评任务是业务。所以 ADMIN
    读写一起退出（`ensure_task_reader` 也不再放行它）。见 `api/v1/tasks.py` 的
    `TASK_READERS` 与 `docs/phase0_rule_freeze.md` 第 7 节。
    """
    if user.role_code != RoleCode.COUNSELOR:
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def ensure_task_reader(user: UserAccount) -> None:
    """Reading the task list and its completion figures is counselor + leader.

    Both need it for their own work — the counselor runs the cycle, the leader
    reads school-level completion as oversight. Neither is ADMIN: see
    `ensure_task_writer`.
    """
    if user.role_code not in {RoleCode.COUNSELOR, RoleCode.LEADER}:
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def ensure_task_target_reader(db: Session, user: UserAccount) -> None:
    """读目标学生名单要**两道门槛**：任务读者 + 「组织与账号」的读权限。

    这一页回答的是「这场测评发给了谁」，逐行给出学号与姓名——那是组织与账号那一档
    的数据，不是任务本身的数据。任务那一层（`ensure_task_reader`）回答的是
    「他能不能读这场测评」，两道问的不是同一件事，缺一道就漏一种洞：

    | | 任务读者 | `ORG_ACCOUNT` | 结论 |
    |---|---|---|---|
    | counselor | ✅ | `READ_BASIC` ✅ | 放行 |
    | leader | ✅ | `READ_SUMMARY` ✅ | 放行（只读监督口径） |
    | admin | ❌ | `MANAGE` | 挡在任务那一层 |

    这与 `analytics_service.ensure_student_result_reader` 是**同一个形状**：路由声明一次
    （让权限出现在 OpenAPI 里）、服务里再查一次（直接调用服务的人——测试、将来的
    批处理——也拿不到越权数据）。**代价要知情**：单独拆掉任何一遍都不会让测试变红，
    两遍一起拆才会，所以测试钉的是「这两个能力是必要条件」，不是「服务里这一行在挡」。
    用 `scope_allows`，缺记录/读失败一律回退默认值（§4，绝不 fail-open）。
    """
    ensure_task_reader(user)
    if not scope_allows(
        db, user.role_code, ORG_ACCOUNT, allow={MANAGE, READ_BASIC, READ_SUMMARY}
    ):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def ensure_detail_reader(db: Session, user: UserAccount) -> None:
    """读**逐人心理明细**要多一道门槛：学生心理详情的 `SCOPED`（2026-09-20 加）。

    任务读者（`ensure_task_reader`）回答的是「他能不能读这场测评」，这一道回答的是
    「他能不能看**逐个人**的心理结果」。两者不是一回事，而完成明细**同时**带着身份列
    （学号 / 姓名 / 年级 / 班级）与等级列（关注等级 / MHT总分）——所以它要两道。

    | | 任务读者 | `STUDENT_PSYCH_DETAIL` | 结论 |
    |---|---|---|---|
    | counselor | ✅ | `SCOPED` ✅ | 放行 |
    | leader | ✅ | `SUMMARY` ❌ | **挡在这一层**（`SUMMARY` 只是聚合） |
    | admin | ❌ | `NONE` | 挡在任务那一层 |

    **这条门槛就是缺口 12 的处置**（CLAUDE.md）。在此之前 `task_completion` 只查了
    `ensure_task_reader`，而 `TASK_READERS` 含德育领导——于是他在同一个响应里拿到
    `学号 S001 · 姓名 林同学 · 班级 1班 · total_level='GENERAL_RANGE' · total_score=1`。
    实测出来而不是推出来的：探针必须**先让一名学生真的交一次卷**，否则等级列全是
    NULL、看起来「没事」。

    拦住领导**没有抽掉他的完成率视角**，那件事另有落点（三条都不经过逐人明细）：
    `GET /assessment-tasks` 的每一行带着 `total_targets` / `completed_targets` /
    `completion_rate`（`/leader/tasks` 就是这一页）；`GET /assessment-tasks/{id}/participation`
    给的是同一批数字的细分（应测 / 请假 / 免测 / 已排除，**不含任何逐人数据**）；
    `/leader/analytics` 给的是聚合趋势。而 §4 早就把这条线画在同一个地方——
    `GET /students/results` 对德育领导也是严一档（那里是 `ORG_ACCOUNT` 与心理详情两道），
    理由是同一句：**聚合归聚合，逐人明细归逐人明细**。

    规格上它与 `ensure_detail_exporter`（导出那一侧）共用**同一句判据**，所以判据只有
    这一个定义：那一个函数在上面叠一层 `CONTROLLED_EXPORT`，因为一份**离开这栋楼的文件**
    还多要一道导出许可（§16.3）。路径不同、问的是同一件事——「他能不能看逐人的心理明细」。

    与路由那一遍是**重复的两遍**，理由与代价见 `ensure_task_target_reader` 的 docstring：
    单独拆掉任何一遍都不会让测试变红，两遍一起拆才会。
    """
    if not scope_allows(db, user.role_code, STUDENT_PSYCH_DETAIL, allow={SCOPED}):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)


def effective_task_status(
    task: AssessmentTask,
    *,
    total_targets: int,
    completed_targets: int,
    now: datetime | None = None,
) -> str:
    """这场测评**现在**是什么状态（2026-09-17 补）。

    在此之前 `assessment_task.status` 是写入时定死的一个字面量：建任务写 `ACTIVE`，
    导入批次也写 `ACTIVE`，然后再没有一行代码读它或改它。于是列表上每一行永远是
    「进行中」——一批 3/3 全收齐的外部导入、一场已经过了截止日期的普查、一场还没到
    开始日期的复测，长得一模一样。用户报的就是这个。

    状态由**已经被记下来的事实**推出来，所以它是现算的，与 `export_service` 现算遮蔽、
    `analytics_service.latest_result_subquery` 现算「当前状态」是同一个口径：

    1. 库里那一列不是 `ACTIVE` → **以库里为准**。那是人做的裁决（`DRAFT` 草稿 /
       `PAUSED` 已暂停 / `CLOSED` 提前结束），推出来的事实不能盖过它。今天没有写入方，
       但词汇表里有这四个码，读的一侧就该按它们的意思读。
    2. 还没到 `start_at` → `NOT_STARTED` 未开始。
    3. 过了 `end_at` → `CLOSED` 已结束，**不管还有没有人没答**。窗口是学校自己定的，
       过期就是过期；「28 人里 23 人交了」由完成率那两列说，不必让状态列再说一遍。
       放了截止日期就能再改（`PATCH /assessment-tasks/{id}` 收 `end_at`），
       所以「还有几个人没答，延一周」是一条走得通的路，不是死局。
    4. 目标行全部完成 → `CLOSED` 已结束。外部导入的批次走的是这一条：它**没有截止
       日期**（`commit_batch` 刻意留空，见那里的注释），只能由「全都答完了」收尾。
    5. 其余 → `ACTIVE` 进行中。

    `total_targets == 0` **不算**「全部完成」：空集的「全部完成」是真命题，但一次还没
    发出去的任务不该一建出来就显示「已结束」。

    **口径是整场任务，不是调用者自己的范围。** 传进来的两个数必须是**不加范围谓词**的
    总数。状态与 `name` / `start_at` 一样是任务自身的属性，而完成率跟读者的范围走（§9）：
    否则一个只带 CLASS 范围的心理老师会把一场 12/40 的全校普查看成「已结束」——
    同一行里状态说结束、完成率说 30%。

    时间比较用的是**本地朴素时间**：`start_at` / `end_at` 列是 `DateTime(timezone=True)`，
    但写进去的是 `datetime(2026, 9, 30, 23, 59, 59)` 这样的朴素值（seed 与
    `parse_datetime` 都这么写），MySQL 取回来也不带时区。
    """
    if task.status != "ACTIVE":
        return task.status
    now = now or datetime.now()
    if task.start_at and now < task.start_at:
        return "NOT_STARTED"
    if task.end_at and now > task.end_at:
        return "CLOSED"
    if total_targets > 0 and completed_targets >= total_targets:
        return "CLOSED"
    return "ACTIVE"


def task_participation_counts(
    db: Session, task_id: int, *, scope=None
) -> dict[str, int]:
    """整场任务的参与口径五个数，§18.10 的**唯一一处定义**（2026-09-19 第 8 期）。

    | 键 | 数的是什么 |
    |---|---|
    | `total_targets` | 目标行数（发放 + 补发，**原始人数**） |
    | `excluded_targets` | 其中请假 / 免测 / 已排除的那些行 |
    | `expected_targets` | **应测人数** = 上面两个之差 |
    | `completed_targets` | 应测名单里已完成的目标行 |
    | `completion_rate` | 有效完成率 = 上面一行 ÷ 应测人数 |

    需求说明书 §18.10 逐字写着「应测人数 = 任务目标人数 - 请假 - 免测 - 已排除」，
    而「任务外、重复、未匹配和冲突记录不进入任务完成率」——那四类记录住在
    `assessment_import_row` 上，**从来就不在这张表里**，所以那半句话是构造上成立的
    （见 `api/v1/tasks.py` 的参与统计端点，它把那四类单独数出来给读者看）。

    `scope` 是可选的读者范围谓词（§9 的第二个入口）。**它同时套在分子与分母上**，
    因为「应测」与「已完成」必须是同一个集合里的两件事：一个只带 CLASS 范围的心理
    老师看到的是他自己那些班的应测与完成，德育领导看到的是全校。`total_targets`
    也跟着范围走——这一整块是**统计**，与 `task_target_counts` 那条「状态口径是整场
    任务」不同（§12：状态是任务自身的属性，完成率跟读者的范围走）。

    一个数也不套范围的情形只有一种：`effective_task_status` 问「这场还能不能开新卷子」，
    它拿的是不带 `scope` 的那一份。
    """
    conditions = [AssessmentTarget.task_id == task_id]
    # 分子与分母来自同一条谓词，`excluded` 是它们相减的结果而不是另数一遍——
    # 三个数各查各的时候，「应测 + 排除 = 目标」这句话会在某次改动之后不再成立，
    # 而屏幕上一切正常（缺口 7 那一族的形状）。
    expected = expected_participation_predicate()
    query = select(
        func.count(AssessmentTarget.id),
        func.sum(case((expected, 1), else_=0)),
        func.sum(case((and_(expected, AssessmentTarget.status == "COMPLETED"), 1), else_=0)),
    ).where(*conditions)
    if scope is not None:
        query = query.join(Student, Student.id == AssessmentTarget.student_id).where(scope)
    total, expected_count, completed = db.execute(query).one()
    total = int(total or 0)
    expected_count = int(expected_count or 0)
    completed = int(completed or 0)
    return {
        "total_targets": total,
        "excluded_targets": total - expected_count,
        "expected_targets": expected_count,
        "completed_targets": completed,
        "completion_rate": round(completed / expected_count * 100) if expected_count else 0,
    }


def task_target_counts(db: Session, task_id: int) -> tuple[int, int]:
    """整场任务的（**应测**人数，应测里的已完成数），**不加范围谓词**。

    `effective_task_status` 要的就是这两个数，凡是「这张卷子还能不能新开」的判断都得
    用这个口径——它与调用者能看到几名学生的目标行无关。

    「应测」这两个字是 2026-09-19 加的：在此之前它就是目标行数，而 §18.10 之后
    请假 / 免测 / 已排除的那几行不算在里面。**默认状态下一个数都没变**（没有人标记过
    时 `participation_disposition` 全是 `REQUIRED`，应测人数等于目标人数），所以
    「把最后一个学生标记成请假，这场就结束了」是这一改动唯一可见的效果——而那正是
    学校标记他时想要的结果。
    """
    counts = task_participation_counts(db, task_id)
    return counts["expected_targets"], counts["completed_targets"]


def list_assessment_tasks(db: Session, user: UserAccount, *, status_filter: str | None = None) -> list[dict]:
    ensure_task_reader(user)
    statement = select(AssessmentTask)
    if status_filter == "VOIDED":
        statement = statement.where(AssessmentTask.status == "VOIDED")
    else:
        # §4.11：**只有显式选「已作废」时才显示 VOIDED**，`ALL` 也不含它。
        # 「全部」说的是「全部还在用的任务」，不是「连作废过的一起列出来」——
        # 后者会让一场已经作废的普查回到列表上，而它的目标行正被 §4.12 从各处统计里
        # 排除掉：同一个任务在两页上一在（列表）一不在（统计），读者没法解释。
        #
        # `ACTIVE` / `CLOSED` 两个字面量**不在这里筛**（那要下一段按推出来的状态做），
        # 所以它们与 `ALL` 一样只拿到「不含 VOIDED」的那个集合，再由下一段收窄。
        statement = statement.where(AssessmentTask.status != "VOIDED")
    tasks = db.scalars(statement.order_by(AssessmentTask.id.desc())).all()
    # 整场任务的应测数（**不加范围谓词**），`effective_task_status` 的口径。
    # 一次分组查询把全部任务取齐，不按任务各查一遍——下面那一组数跟调用者的范围走，
    # 这一组不跟，两套数必须都算出来。
    expected_predicate = expected_participation_predicate()
    whole: dict[int, tuple[int, int]] = {
        task_id: (int(expected or 0), int(done or 0))
        for task_id, expected, done in db.execute(
            select(
                AssessmentTarget.task_id,
                func.sum(case((expected_predicate, 1), else_=0)),
                func.sum(
                    case(
                        (and_(expected_predicate, AssessmentTarget.status == "COMPLETED"), 1),
                        else_=0,
                    )
                ),
            ).group_by(AssessmentTarget.task_id)
        ).all()
    }
    now = datetime.now()
    # The task rows themselves are school-level objects and stay visible; it is
    # the completion figures that follow the caller's scope. A counselor reading
    # "12 / 40" should be reading their own twelve over their own forty — the
    # task list and the workbench tile use the same scope so they agree.
    scope = student_scope_predicate(db, user)
    items = []
    for task in tasks:
        total_all, completed_all = whole.get(task.id, (0, 0))
        # 注意：发出去的是**推出来的**状态，不是 `assessment_task.status`
        # 那一列的原文。理由与口径见 `effective_task_status`。
        status = effective_task_status(
            task, total_targets=total_all, completed_targets=completed_all, now=now
        )
        # §4.11 的「进行中 / 已结束」筛的是**这个**状态，不是上面 SQL 里那一列的原文。
        # `assessment_task.status` 是写入时定死的字面量（一场过了截止日期的普查在上面
        # 仍然写着 `ACTIVE`，CLAUDE.md §12），按它筛会得到「列表上明明写着已结束、选
        # 『已结束』却一条都筛不出来」——同一个状态两个来源，正是 §12 那件事的复现。
        #
        # 位置在计数之前：被筛掉的那几场不必再各查一次参与口径（那是这一轮里最贵的
        # 一个查询）。判据只有 `ACTIVE` / `CLOSED` 两个值——`VOIDED` 与 `ALL` 已经在
        # 上面那一段收窄过了，这里再判一次会把 `VOIDED` 全部筛掉。
        if status_filter in ("ACTIVE", "CLOSED") and status != status_filter:
            continue
        # 一次查询给出这一场在这位读者的范围里的五个数（§18.10）。从前这里是两个
        # 各查一遍的标量——那时「目标」「完成」是两个独立的事实，而现在「应测」是
        # 由分子分母**一起**算出来的，分开查会让两者在某次改动之后对不上。
        counts = task_participation_counts(db, task.id, scope=scope)
        items.append(
            {
                "id": task.id,
                "task_no": task.task_no,
                "name": task.name,
                "status": status,
                "scope_type": task.scope_type,
                "start_at": task.start_at.isoformat() if task.start_at else None,
                "end_at": task.end_at.isoformat() if task.end_at else None,
                # `total_targets` 是**原始目标人数**，「应测人数」在下面那一列。
                # 两个都给出来，是因为界面那一行要写「已完成 / 应测」而排除掉的那几个人
                # 也要有地方交代（§9：被过滤掉的数字，口径要写进界面）。
                "total_targets": counts["total_targets"],
                "expected_targets": counts["expected_targets"],
                "excluded_targets": counts["excluded_targets"],
                "completed_targets": counts["completed_targets"],
                "completion_rate": counts["completion_rate"],
                # 外部导入的批次任务（数据中心 → MHT测评记录导入）是一批已经完成的
                # 记录，不是一份等学生来答的任务。列表上要能看出来：否则「5/5 完成、
                # 100%」会和一份真正收上来的任务长得一模一样。
                "source": task.source,
            }
        )
    return items


def ensure_task_in_scope(db: Session, user: UserAccount, task_id: int) -> None:
    """删除 / 作废这场任务，得先看它的人在不在你的数据范围里（§4.9）。

    §4.9 给心理老师那一格写的不是光秃秃的「是」，而是「**需在数据范围/任务权限内**」——
    与 §9 那条「能力回答『心理老师能不能删任务』，范围回答『这位心理老师能不能删**这**
    一场』」是同一条。少了它，一个只带 `CLASS` 范围的心理老师能删掉整所学校的普查任务：
    他确实是心理老师（能力过关），而那一场里一个他的学生都没有。

    判据取**目标行里的学生**而不是 `created_by`：任务可以在创建之后被转交、补发，
    而「这场任务测的是不是我的学生」才是这条规则要回答的问题。任务**一条目标行都没有**
    时放行——那时候没有任何学生的数据会被它牵连，而刚建好还没发出去的任务正是这个形状
    （它与「目标行都在别人的范围里」是两件事，不能合成一条）。

    读与写共用它（`task_delete_check` 进门就调），所以预览说能删、真删的时候也一定能删
    ——两处各写一遍必然漂，而漂的那一天界面上会出现「预览说能删、点下去 403」。
    """
    task = db.get(AssessmentTask, task_id)
    if task is None:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    total = db.scalar(
        select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task_id)
    ) or 0
    if total == 0:
        return
    visible = db.scalar(
        select(func.count(AssessmentTarget.id))
        .join(Student, Student.id == AssessmentTarget.student_id)
        .where(AssessmentTarget.task_id == task_id, student_scope_predicate(db, user))
    ) or 0
    if visible == 0:
        raise AppError(
            "SCOPE_FORBIDDEN",
            "这场测评任务里的学生不在你的数据范围内，不能删除或作废",
            403,
        )


def task_delete_check(db: Session, user: UserAccount, task_id: int) -> dict:
    ensure_task_writer(user)
    ensure_task_in_scope(db, user, task_id)
    task = db.get(AssessmentTask, task_id)
    target_count = db.scalar(select(func.count(AssessmentTarget.id)).where(AssessmentTarget.task_id == task.id)) or 0
    session_count = db.scalar(select(func.count(AssessmentSession.id)).where(AssessmentSession.task_id == task.id)) or 0
    result_count = db.scalar(select(func.count(AssessmentResult.id)).join(AssessmentSession).where(AssessmentSession.task_id == task.id)) or 0
    import_batch_count = db.scalar(select(func.count(AssessmentImportBatch.id)).where(AssessmentImportBatch.task_id == task.id)) or 0
    committed_count = db.scalar(select(func.count(AssessmentImportBatch.id)).where(AssessmentImportBatch.task_id == task.id, AssessmentImportBatch.status == "COMMITTED")) or 0
    risk_count = db.scalar(select(func.count(RiskEvent.id)).where(RiskEvent.session_id.in_(select(AssessmentSession.id).where(AssessmentSession.task_id == task.id)))) or 0
    pending_risk_count = db.scalar(select(func.count(RiskEvent.id)).where(RiskEvent.session_id.in_(select(AssessmentSession.id).where(AssessmentSession.task_id == task.id)), RiskEvent.status == "PENDING")) or 0
    manual_count = db.scalar(select(func.count(ManualReview.id)).join(RiskEvent, RiskEvent.id == ManualReview.risk_event_id).where(RiskEvent.session_id.in_(select(AssessmentSession.id).where(AssessmentSession.task_id == task.id)))) or 0
    student_ids = select(AssessmentSession.student_id).where(AssessmentSession.task_id == task.id)
    care_count = db.scalar(select(func.count(StudentCareCase.id)).where(StudentCareCase.student_id.in_(student_ids))) or 0
    hard = session_count == result_count == committed_count == risk_count == import_batch_count == 0
    return {
        "taskId": task.id, "taskNo": task.task_no, "taskName": task.name,
        "source": task.source, "status": task.status,
        "deleteMode": "HARD_DELETE" if hard else "VOID", "canHardDelete": hard,
        "targetCount": target_count, "sessionCount": session_count, "resultCount": result_count,
        "importBatchCount": import_batch_count, "committedImportBatchCount": committed_count,
        "riskEventCount": risk_count, "pendingRiskEventCount": pending_risk_count,
        "manualReviewCount": manual_count, "careCaseCount": care_count,
        "warning": ("该任务尚未产生正式测评事实，将物理删除。" if hard else
                    "该任务已产生正式测评事实，删除将按作废处理，原始历史与人工关怀记录会保留。"),
    }


def delete_or_void_task(db: Session, user: UserAccount, task_id: int, *, reason: str | None) -> dict:
    check = task_delete_check(db, user, task_id)
    task = db.get(AssessmentTask, task_id)
    # 幂等：已经作废过的任务再作废一次，改的正是 `voided_at` / `voided_by` /
    # `void_reason` 这三列——也就是「谁在什么时候、因为什么把它作废的」这条记录本身。
    # 第二次调用看上去什么都没发生（状态仍然是 VOIDED、计数也一样），而轨迹已经被
    # 后来者覆盖了，且**没有任何东西看得出来**。
    #
    # 界面上确实有一道遮挡（`TasksPage.vue` 的删除按钮 `v-if="row.status !== 'VOIDED'"`），
    # 但那是**前端**的，挡不住直接调接口、双击、或者两个标签页各点一次。与 §4 那句
    # 「后端鉴权是最终权限来源，前端隐藏不是安全措施」是同一条。
    if task.status == "VOIDED":
        raise AppError(
            "CONFLICT",
            "这场测评任务已经作废过了，不能重复作废：重复作废会覆盖「谁在什么时候作废的」那条记录。",
            409,
        )
    if check["canHardDelete"]:
        db.execute(delete(AssessmentTarget).where(AssessmentTarget.task_id == task_id))
        db.execute(delete(AssessmentTaskScope).where(AssessmentTaskScope.task_id == task_id))
        db.delete(task)
        return {**check, "mode": "HARD_DELETE", "status": "DELETED"}
    cleaned = (reason or "").strip()
    if not cleaned:
        raise AppError("VALIDATION_ERROR", "已有正式测评事实的任务作废时必须填写原因", 422)
    now = now_local_naive()
    task.status, task.voided_at, task.voided_by, task.void_reason = "VOIDED", now, user.id, cleaned
    db.execute(update(AssessmentSession).where(AssessmentSession.task_id == task_id, AssessmentSession.is_effective.is_(True)).values(is_effective=False))
    db.execute(update(RiskEvent).where(RiskEvent.session_id.in_(select(AssessmentSession.id).where(AssessmentSession.task_id == task_id)), RiskEvent.status == "PENDING").values(status="VOIDED", voided_at=now, voided_by=user.id, void_reason=cleaned))
    return {**check, "mode": "VOID", "status": "VOIDED"}


def create_school_assessment_task(
    db: Session,
    user: UserAccount,
    *,
    name: str,
    start_at: str | None,
    end_at: str | None,
) -> AssessmentTask:
    ensure_task_writer(user)
    school = db.scalar(select(School).where(School.code == "QH"))
    scale = db.scalar(
        select(AssessmentScale)
        .where(AssessmentScale.code == "MHT", AssessmentScale.status == "PUBLISHED")
        .order_by(AssessmentScale.id.desc())
    )
    if not school or not scale:
        raise AppError("VALIDATION_ERROR", "缺少学校或已发布量表", 422)
    # 单号：`TASK-<到秒的时间戳>-<序号>`，序号从「全库任务数 + 1」起。
    #
    # 那个基数是**一个起点，不是一个保证**——同一秒里两个请求会各自算到同一个基数，
    # 而唯一键才是判据。所以插入走 `insert_with_unique_number`：撞了就往上试一个号
    # （理由见那个模块的 docstring：重读一遍在 REPEATABLE-READ 下读到的是同一份快照，
    # 加 `FOR UPDATE` 则会从「500 撞号」变成「1213 死锁」）。
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    task_count = db.scalar(select(func.count(AssessmentTask.id))) or 0

    def build_task(task_no: str) -> AssessmentTask:
        return AssessmentTask(
            task_no=task_no,
            name=name,
            scale_id=scale.id,
            school_id=school.id,
            scope_type="SCHOOL",
            start_at=parse_datetime(start_at),
            end_at=parse_datetime(end_at),
            status="ACTIVE",
            created_by=user.id,
        )

    task = insert_with_unique_number(
        db,
        number_at=lambda offset: f"TASK-{stamp}-{task_count + 1 + offset}",
        build=build_task,
    )
    # 「当时是按什么范围发的」——与下面那批目标行（「实际发给了谁」）是**两张表、
    # 两个问题**（§16.2）。今天 scope_type 恒为 SCHOOL（这个端点还不收范围参数），
    # 但只留目标行的话，「这场普查当初打算测谁」就永远没有答案了：发放之后名册上
    # 转进来一个学生，目标行会补、范围不会变，两者本来就该不同。等到创建任务能选
    # 年级/班级时，这里换的只是 `scope_type` 与那个对应的 `*_id`，读的那一侧不吃惊。
    db.add(
        AssessmentTaskScope(
            task_id=task.id,
            scope_type=task.scope_type,
            school_id=school.id,
            created_by=user.id,
        )
    )
    # 目标行按**创建者的数据范围**发放（§9 的第二个入口）。这条谓词是 2026-09-17
    # 写权从 ADMIN 转到 COUNSELOR 时加的，不是装饰：在此之前唯一能建任务的角色是
    # 管理员（seed 给的是 SCHOOL 范围），「发放全体」和「发放创建者可见的全体」
    # 恰好是同一件事，所以少了它也不出错。写权一落到心理老师身上就分岔了——
    # 一个只带 CLASS 范围的心理老师，凭这个端点能给**全校**每个学生建目标行，
    # 而目标行本身就是「这个学生要参加这次测评」的管理事实，他随后还能在
    # `/counselor/tasks` 的完成明细里按自己的范围读到它的进度。范围收在这里，
    # 与 `list_assessment_tasks` / `task_completion` 读的那一侧用的是同一个谓词。
    #
    # 年级名与班级名**在这里一次 join 出来**，不留给 `target_snapshot` 逐行去取：
    # 一场全校普查是一千行，两层 relationship 就是两千次 SELECT。两个 `*_id` 都是
    # NOT NULL 且有复合外键兜着，所以 inner join 与原来的 `select(Student)` 逐行等价。
    students = db.execute(
        select(Student, Grade.name, ClassGroup.name)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .where(
            Student.school_id == school.id,
            Student.status == "ACTIVE",
            student_scope_predicate(db, user),
        )
    ).all()
    # 七个快照列一次写齐（§8.2 / §18.2）：`school_id_snapshot` 没有默认值、不写就插不进
    # 那一行，另外六列在 2026-09-19 之前每一行都是 NULL。定义只有一处
    # （`target_snapshot`），补发、导入收行与种子走的是同一个函数。
    db.add_all(
        [
            AssessmentTarget(
                task_id=task.id,
                student_id=student.id,
                status="NOT_STARTED",
                **target_snapshot(student, grade_name=grade_name, class_name=class_name),
            )
            for student, grade_name, class_name in students
        ]
    )
    db.flush()
    return task


def list_task_targets(db: Session, user: UserAccount, task_id: int) -> dict:
    """这场测评发给了谁——**按快照说**，名册只在快照缺的时候兜底（§8.2）。

    与同一个页面上那个完成明细（`task_completion`）读的是同一张表、同一批行，
    区别只有三条，都是有意的：

    - 这里**按快照展示**（学号 / 姓名 / 年级 / 班级 / 性别 / 年龄取
      `*_snapshot` 列），因为这一页回答的是「发放那一刻学校看到的是谁」。V1.2 之前
      发出去的目标行那六列是 NULL，所以每一格回退到当前名册——不是两条口径打架，
      是同一条口径在缺数据时的读法。
    - 这里带 `target_source`（§18.2 的「目标来源」），完成明细不带：那一页说的是
      「这一批人这次测出了什么」，而「他是本来就在名单上、还是后来补进来的」是
      这一页的事。
    - 这里**没有**等级与总分：它们是测评结果（心理详情），而这一页只要身份列
      （组织与账号那一档）。一张响应里既有身份列又有等级列就会逼出 §4 那条双门槛，
      而这一页用不着——要看结果的人去「查看明细」。
    - `participation_disposition`（该不该参加）**不发**。四个码本身是有定义的
      （`PARTICIPATION_DISPOSITIONS`，2026-09-19 第 8 期），缺的是**理由**那一套码
      （需求说明书 §16.10 的 `TODO_BUSINESS_CONFIRMATION`：落库的 `disposition_reason`
      是一句人写的说明，不是码）。而这一页答的是「这场测评发给了谁、谁补进来的」，
      标记那件事在**完成明细**那一页上做（那一页同时有 `status`，两个人看得到差别）——
      两页各发一份「该不该参加」就是两个定义。

    范围谓词与 `task_completion` 用同一个（§9）：一个只带 CLASS 范围的心理老师，
    在这里读到的目标行与他能在完成明细里读到的是同一批。
    """
    ensure_task_target_reader(db, user)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    rows = db.execute(
        select(AssessmentTarget, Student, Grade, ClassGroup)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .where(AssessmentTarget.task_id == task_id, student_scope_predicate(db, user))
        .order_by(Student.id)
    ).all()
    # 「当时是按什么范围发的」与上面那批行是两个问题（§16.2）。**没有范围行就是
    # 没有记录**，这里不回退到 `assessment_task.scope_type`：
    #
    # - 外部导入的批次任务那一列写着 `SCHOOL`，而它从来不是「发给全校」——这一批是
    #   照着一份文件建的，文件里有谁就是谁。回退会把「不知道」变成一句言之凿凿的
    #   「全校」，而同一屏下方那几十行正是反例。
    # - 2026-09-19 之前建的校内任务确实**是**按全校发的，但它没有那一行记录。
    #   给它补一行是替历史编一个「当时选的是全校」的裁决（§21：`tested_at` 刻意
    #   不回填，同一个道理），而今天显示「未记录发放范围」恰好是实话。
    #
    # 所以 `scope_type` 可以是 `null`，界面那一句也要跟着分岔，不能拿 `?? 'SCHOOL'` 抹平。
    scope = db.scalar(select(AssessmentTaskScope).where(AssessmentTaskScope.task_id == task_id))
    return {
        "task_id": task.id,
        "task_no": task.task_no,
        "name": task.name,
        "scope_type": scope.scope_type if scope else None,
        "items": [
            {
                "student_no": target.student_no_snapshot or student.student_no,
                # 快照优先，名册兜底。两边在这一列上取的都是「展示名」
                # （`masked_name`），与 `list_students` / `task_completion` 一致。
                "student_name": target.student_name_snapshot or student.masked_name,
                "grade": target.grade_name_snapshot or grade.name,
                "class_name": target.class_name_snapshot or class_group.name,
                # `is not None` 而不是 `or`：`age_snapshot` 是整数，`or` 会把 0 当成
                # 「没有快照」——那不是会发生的年龄，但它正是那种没人会去验的边界。
                "gender": target.gender_snapshot if target.gender_snapshot is not None else student.gender,
                "age": target.age_snapshot if target.age_snapshot is not None else student.age,
                "status": target.status,
                "target_source": target.target_source,
                "assigned_at": target.assigned_at.isoformat() if target.assigned_at else None,
                "completed_at": target.completed_at.isoformat() if target.completed_at else None,
            }
            for target, student, grade, class_group in rows
        ],
    }


# 补发预览一次最多列几个人。一场全校普查是一千人，而「补发」在真实学校里是几个人
# 到几十个人的事（转学进来的、休学回来的）——列表本身不是给人逐条看的，是给人确认
# 「这批人是不是我要的那批」的。**截断了要说出来**（§10），所以调用方拿到 `total`
# 与 `truncated`，界面写「另有 N 人未列出」。**确认时补发的是全部候选，不是这 200 个**：
# 这个上限只是显示用的，它不是一条业务规则。
SUPPLEMENT_CANDIDATE_LIMIT = 200


def _supplement_conditions(db: Session, user: UserAccount, task: AssessmentTask) -> list:
    """「该补进来、还没有目标行」的那批学生，取数与计数**共用这一份条件**（§10）。

    三条：这所学校的在读学生、在调用者的数据范围内（§9 的第二个入口，与发放时
    同一个谓词）、还没有这场测评的目标行。

    这里**不**去读 `assessment_task_scope`：那个范围今天恒为 SCHOOL（建任务还不收
    范围参数），读它只会得到一句恒真的条件。等建任务能选年级/班级时，交集加在
    这里——范围是「当初打算测谁」，补发的候选集必须是它的子集，而调用者的范围
    再收一道。
    """
    return [
        Student.school_id == task.school_id,
        Student.status == "ACTIVE",
        Student.id.not_in(
            select(AssessmentTarget.student_id).where(AssessmentTarget.task_id == task.id)
        ),
        student_scope_predicate(db, user),
    ]


def supplement_targets(
    db: Session,
    user: UserAccount,
    task_id: int,
    *,
    confirm: bool,
) -> dict:
    """补发目标学生（§8.1 / §16.2）：把发放之后才进到名册上的学生补进这场测评。

    形参里**没有 `reason`**：那是审计那一行的内容，而这个模块不写审计（全库的写操作
    都是路由写审计，见 `api/v1/tasks.py`）。补发原因由路由从请求体里取。

    **先看后补，两步。** `confirm=False` 只回答「会补哪些人」，一行都不写；
    `confirm=True` 才落行。§20#9 要的正是这个形状——「同一学校任务创建后新增学生，
    **补发确认后**才新增目标行」，所以这条不是界面上的体贴，是一条验收判据。
    预览本身不写审计（它没改变任何东西），补发写一行（在路由里，与这个模块其余
    写操作一致）。

    补发的学生与建任务时发放的学生**长得不一样**，所以 `target_source` 记
    `SUPPLEMENT`（§18.2 的「目标来源」）：一份完成率报表里，「这场普查本来该测的人」
    与「后来补进来的人」是两个数。快照七列照旧一次写齐——补发那一刻的名册，
    与建任务时那条路径走的是同一个 `target_snapshot`。

    **任务已结束（`effective_task_status` 说 CLOSED）就不补。** 一场过了截止日期的
    普查再补人行不通：`create_or_get_session` 会在截止日期那道门把被补进来的学生
    挡在外面（§12），补出来的是一行谁也点不开的目标行——那比不补更糟，因为它看起来
    像已经补好了。出路是有的，写在错误文案里：先把截止日期改到将来。
    """
    ensure_task_writer(user)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    total_before, completed_before = task_target_counts(db, task_id)
    status = effective_task_status(
        task, total_targets=total_before, completed_targets=completed_before
    )
    if status == "CLOSED":
        raise AppError(
            "VALIDATION_ERROR",
            "这场测评已经结束，不能再补发目标学生。如果确实还要补，"
            "请先编辑这场测评把截止日期改到将来，再补发。",
            422,
        )
    conditions = _supplement_conditions(db, user, task)
    total_candidates = (
        db.scalar(select(func.count()).select_from(Student).where(*conditions)) or 0
    )
    preview_rows = db.execute(
        select(Student, Grade.name, ClassGroup.name)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .where(*conditions)
        .order_by(Student.id)
        .limit(SUPPLEMENT_CANDIDATE_LIMIT)
    ).all()
    result = {
        "before": total_before,
        "total": int(total_candidates),
        "truncated": int(total_candidates) > SUPPLEMENT_CANDIDATE_LIMIT,
        "limit": SUPPLEMENT_CANDIDATE_LIMIT,
        "candidates": [
            {
                "student_no": student.student_no,
                "student_name": student.masked_name,
                "grade": grade_name,
                "class_name": class_name,
            }
            for student, grade_name, class_name in preview_rows
        ],
    }
    if not confirm:
        return result
    # 确认这一支**重新取一遍**、不带 limit：上面那 200 条是给人看的样本，
    # 要补的是全部候选。用样本去插会把「另外 150 人」静默丢掉，而屏幕上写着
    # 「将新增 350 人」——那是这个文件里最不该出现的形状。
    rows = db.execute(
        select(Student, Grade.name, ClassGroup.name)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        .where(*conditions)
        .order_by(Student.id)
    ).all()
    db.add_all(
        [
            AssessmentTarget(
                task_id=task.id,
                student_id=student.id,
                status="NOT_STARTED",
                target_source="SUPPLEMENT",
                **target_snapshot(student, grade_name=grade_name, class_name=class_name),
            )
            for student, grade_name, class_name in rows
        ]
    )
    db.flush()
    result["added"] = len(rows)
    result["after"] = total_before + len(rows)
    return result


def parse_datetime(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value)


def update_assessment_task(
    db: Session,
    user: UserAccount,
    task_id: int,
    *,
    name: str | None,
    start_at: str | None,
    end_at: str | None,
) -> AssessmentTask:
    ensure_task_writer(user)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    if name is not None:
        task.name = name
    if start_at is not None:
        task.start_at = parse_datetime(start_at)
    if end_at is not None:
        task.end_at = parse_datetime(end_at)
    if task.start_at and task.end_at and task.end_at < task.start_at:
        raise AppError("VALIDATION_ERROR", "截止日期不能早于开始日期", 422)
    db.flush()
    return task


def task_completion(db: Session, user: UserAccount, task_id: int) -> list[dict]:
    """这一场测评的逐人完成明细（**身份列 + 等级列**）。

    门槛放在这里而不是三个调用点上，因为「这一页里有什么」与「谁能读它」是同一件事：
    三个调用点（路由 `GET …/completion`、`task_completion_csv`、`non_participants_csv`）
    各自再写一遍，将来第四个调用方一定漏掉。后两个**导出**另外还要 `CONTROLLED_EXPORT`
    （`ensure_detail_exporter`），那是文件多要的一道，不是这份数据多要的——所以这里
    只查「逐人明细」那一道（`ensure_detail_reader`）。
    """
    ensure_task_reader(user)
    ensure_detail_reader(db, user)
    task = db.get(AssessmentTask, task_id)
    if not task:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    rows = db.execute(
        select(AssessmentTarget, Student, Grade, ClassGroup, AssessmentSession, AssessmentResult)
        .join(Student, Student.id == AssessmentTarget.student_id)
        .join(Grade, Grade.id == Student.grade_id)
        .join(ClassGroup, ClassGroup.id == Student.class_id)
        # 外连接打在**这一场比赛里有效的那一场**上。那句「(task, student) 是唯一的、
        # 所以不乘行」在第 6 期之后不再成立：`USE_EXTERNAL` 与 `KEEP_BOTH` 都会把
        # 在线那一场留着（它只是 `is_effective=0`），于是同一场比赛里有两行，
        # 不限定的话**完成明细里每名学生会占两行**、各显示各的分。
        #
        # 加了 `is_effective` 不等于把它换成「按人取最近一场」：谓词限定的仍然是
        # **这一场任务**（上面那条注释说的口径没变，`effective_session_predicate`
        # 也不带跨任务的 `latest_session_order`）——它只回答「这场比赛里，哪一份
        # 算数」。一名学生在这场比赛里两场都真实发生过（趋势里两场都在），
        # 但完成明细一行一人，取算数的那一份。
        .outerjoin(
            AssessmentSession,
            (AssessmentSession.task_id == AssessmentTarget.task_id)
            & (AssessmentSession.student_id == Student.id)
            & effective_session_predicate(),
        )
        # 结果按 session_id 唯一，所以这一跳同样不乘行。
        #
        # **口径是「这一场」，不是「每人最近一场」。** 这是本模块唯一按场说话的地方，
        # 与 §11 的当前状态口径刻意不同：任务明细要回答的是「这一批人**在这次**测出了
        # 什么」，所以九月十六日那批与九月十七日那批各显示各的。若哪天有人把它换成
        # `latest_result_subquery`（按人取最近一场），九月十六日的批次明细里会印出
        # 九月十七日的分——一个批次报表说别的批次的事，而它旁边那列「完成率」还是
        # 按场算的。`test_task_completion_levels.py` 钉住这条。
        .outerjoin(AssessmentResult, AssessmentResult.session_id == AssessmentSession.id)
        # Per-student rows carrying 学号 and 姓名 — scoped for the same reason the
        # care-case queue is. This also scopes the CSV, which is built from here.
        .where(AssessmentTarget.task_id == task_id, student_scope_predicate(db, user))
        .order_by(Student.id)
    ).all()
    return [
        {
            # 目标行自己的 id：标记参与状态那条路（`PATCH /assessment-tasks/{id}/targets/
            # {target_id}/participation`）要的正是它，而这一页是心理老师唯一逐行看着
            # 学生做那件事的地方。少这一列，界面上要么拿学号反查一次，要么把标记按钮
            # 放到另一个看不见学号的屏幕上。
            "target_id": target.id,
            "student_no": student.student_no,
            "student_name": student.masked_name,
            "grade": grade.name,
            "class_name": class_group.name,
            "gender": student.gender,
            # Derived on read; NULL for students imported before migration 0009.
            "age": student.age,
            "status": target.status,
            # 「这场测评他该不该参加」。「答没答」与「该不该答」是两个维度，这一列
            # 是 §18.10 那三个减项的出处——完成明细要能回答「应测 37 人是哪 37 人、
            # 另外 3 人为什么不算」，否则分母变小了而没有人看得出来为什么。
            "participation_disposition": target.participation_disposition,
            "disposition_reason": target.disposition_reason,
            "assigned_at": target.assigned_at.isoformat() if target.assigned_at else None,
            "completed_at": target.completed_at.isoformat() if target.completed_at else None,
            # 这一场的等级与总分。NULL 有两种来源，界面都必须显示「未测评」/「—」而
            # 不是空格或 0：他这一场还没交（没有结果行），或者交了但没有结果
            # （评级不适用）。`None` 不是 `0`（§11）。
            "total_level": result.total_level if result else None,
            "total_score": result.total_score if result else None,
            # Comes off the session, not the target: `completed_at` is set when the
            # target is marked done, and a session can legitimately have no duration
            # (pre-0009 rows, or a session cleared by /reset). The UI must render
            # NULL as 「—」 — 0 would read as "answered instantly", a different claim
            # from "not recorded".
            "duration_seconds": session.duration_seconds if session else None,
        }
        for target, student, grade, class_group, session, result in rows
    ]


# 完成明细导出的列白名单，**服务端**定义（§16.3：导出接口不得接受任意字段名）。
# 它是这一份文件里有什么的唯一定义：`task_completion_csv` 用它写表头，而同一个元组
# 落进 `export_job.field_policy`——两处各写一份的话，政策里记的列与文件里真有列会在
# 某次改动之后分岔，而两边看起来都对（`ExportDocument` 的 docstring 说的就是这件事）。
TASK_COMPLETION_COLUMNS: tuple[str, ...] = (
    "学号",
    "学生",
    "年级",
    "班级",
    "性别",
    "年龄",
    "状态",
    "参与状态",
    "关注等级",
    "MHT总分",
    "分配时间",
    "完成时间",
    "用时(秒)",
)


def task_completion_csv(db: Session, user: UserAccount, task_id: int) -> ExportDocument:
    """这一场测评的完成明细，连同它用了哪些列一起返回。

    返回 `ExportDocument` 而不是裸字符串（2026-09-19 阶段 8）：这份 CSV 现在是一次
    导出作业的产物，而作业要记下**自己发出去的是什么**（列白名单、行数、哈希）。
    行数由这里数——它是 `writer.writerow` 被调用的次数，只有这一个地方说得准。
    """
    rows = task_completion(db, user, task_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(TASK_COMPLETION_COLUMNS)
    written = 0
    for row in rows:
        written += 1
        writer.writerow(
            [
                row["student_no"],
                row["student_name"],
                row["grade"],
                row["class_name"],
                # Translated here and **only** here: `task_completion` above still hands
                # the API raw codes, because the SPA translates those itself via
                # `labels.ts`. This row is destined for a file that leaves the building,
                # so it gets the same words the screen shows (CLAUDE.md §3).
                gender_label(row["gender"]),
                row["age"] if row["age"] is not None else "",
                target_status_label(row["status"]),
                participation_label(row["participation_disposition"]),
                # 等级不是数值列：按 `levelLabel` 的既有约定出「未测评」而不是空单元格
                # （§3）。这一场还没交卷的人因此也说得清楚，而不是留一格让人猜。
                level_label(row["total_level"]),
                # 总分是数值列，另一种约定：没有记录就**留空**。写「—」会让整列被
                # 表格软件当成文本，这一列就没法排序求和了（§3）。
                row["total_score"] if row["total_score"] is not None else "",
                row["assigned_at"] or "",
                row["completed_at"] or "",
                row["duration_seconds"] if row["duration_seconds"] is not None else "",
            ]
        )
    # BOM 留着：这份文件是给中文 Excel / WPS 打开的，少了它表格软件会把中文读成乱码。
    return ExportDocument(
        csv_text="﻿" + output.getvalue(),
        columns=TASK_COMPLETION_COLUMNS,
        row_count=written,
    )


def ensure_detail_exporter(db: Session, user: UserAccount) -> None:
    """**逐人明细**导出要**两道门槛**：受控导出 + 学生心理详情的 `SCOPED`。

    **三个调用点**，后两道判据逐字相同，所以只有这一个定义：

    * 未参与名单导出（`non_participants_csv`）——那一份逐行给出某一场测评里**没有参加**
      的学生是谁（学号 / 姓名 / 年级 / 班级）；
    * 未匹配行导出（`assessment_import_service.unmatched_rows_csv`）——那一份逐行给出
      这场任务里**没进得来**的导入行，其中「匹配上了但在提交时被放弃」的那些说的正是
      同一件事：这名学生的这一场缺着；
    * 完成明细导出（路由 `completion_export`）——那一份逐行给出学号 / 姓名 / 班级
      **与关注等级 / MHT总分**（2026-09-20 缺口 12 的处置，第三个调用点）。

    名字里**不带 `non_participant`**，是 2026-09-20 加第二个调用点时改的：一个共用的门槛
    按它第一个调用者命名，第二个调用者去调它就成了「未匹配行导出调 non_participant 的
    检查」——名字开始撒谎，而下一个人会照着它再写一份「给未匹配行用的」版本。那正是
    §「同一处只许有一个定义」反复记着的形状。

    心理详情那一道**不在这里查**，它抽在 `ensure_detail_reader` 里：读那一侧
    （`task_completion`）问的是同一句话，两边各写一份就是两个定义。

    「谁没来测这场心理普查」本身是关于一名学生心理工作的事实，而这份名单又是关怀
    跟进的原始材料；同时它是一份**离开这栋楼的文件**（§16.3 的受控导出）。两件事
    各要一道门槛，缺一道就漏一种洞：

    | | `CONTROLLED_EXPORT` | `STUDENT_PSYCH_DETAIL` | 结论 |
    |---|---|---|---|
    | counselor | `PSYCH_SUMMARY` ✅ | `SCOPED` ✅ | 放行 |
    | leader | `PROGRESS_SUMMARY` ✅ | `SUMMARY` ❌ | 挡在心理详情这一层 |
    | admin | `BASE_ONLY` ❌ | `NONE` ❌ | 两层都挡住 |

    形状照 `analytics_service.ensure_student_result_reader`（§4 那条双门槛端点的
    既有写法），`allow` 里的等级也照抄：**描述性等级不可互换**，`SUMMARY` 只是聚合，
    而这一份是逐人的明细，暴露明细的端点必须显式传 `allow={SCOPED}`。

    与路由那一遍是**重复的两遍**，理由与代价见 `ensure_task_target_reader` 的
    docstring：单独拆掉任何一遍都不会让测试变红，两遍一起拆才会。
    """
    if not scope_allows(
        db, user.role_code, CONTROLLED_EXPORT, allow={PSYCH_SUMMARY, PROGRESS_SUMMARY}
    ):
        raise AppError("ROLE_FORBIDDEN", "当前角色无权执行该操作", 403)
    ensure_detail_reader(db, user)


# 未参与名单导出的列白名单，**服务端**定义（§16.3）。身份列 + 参与状态：这几列回答的
# 是「还有谁没测、他为什么不在应测名单上」，所以它**刻意没有**关注等级 / MHT总分——
# 没参加的人本来就没有结果行，加上去只会得到一整列「未测评」，而那一列会让这份文件
# 看起来像一份结果报表。
NON_PARTICIPANT_COLUMNS: tuple[str, ...] = (
    "学号",
    "学生",
    "年级",
    "班级",
    "性别",
    "年龄",
    "参与状态",
    "分配时间",
)


def non_participants_csv(db: Session, user: UserAccount, task_id: int) -> ExportDocument:
    """这场测评里**应测但没完成**的人，连同它用了哪些列一起返回（§18.11 第九个端点）。

    **判据是「在应测名单上」且「没完成」两者同时成立。** 请假 / 免测 / 已排除的那几行
    不进这一份：他们不是「没测」，是学校已经决定他这次不测——把两者混在一起，这份
    名单就从「还有谁要催」变成了「这场测评的全部非完成行」，而读者会照着它去找已经
    被豁免的学生（§11 那条「指标卡上的数必须与它点进去的那个列表同源」）。

    **取数与完成明细同源**（`task_completion`），只在这里筛一次：两份文件因此构造上
    不可能对同一名学生给出相反的说法。代价是这一份也走那条查询（它同样套读者范围）。
    """
    ensure_task_reader(user)
    ensure_detail_exporter(db, user)
    rows = [
        row
        for row in task_completion(db, user, task_id)
        if row["participation_disposition"] == PARTICIPATION_REQUIRED
        and row["status"] != "COMPLETED"
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(NON_PARTICIPANT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row["student_no"],
                row["student_name"],
                row["grade"],
                row["class_name"],
                gender_label(row["gender"]),
                row["age"] if row["age"] is not None else "",
                participation_label(row["participation_disposition"]),
                row["assigned_at"] or "",
            ]
        )
    # BOM 与完成明细同一个理由：给中文 Excel / WPS 打开。
    return ExportDocument(
        csv_text="﻿" + output.getvalue(),
        columns=NON_PARTICIPANT_COLUMNS,
        row_count=len(rows),
    )


def mark_target_participation(
    db: Session,
    user: UserAccount,
    task_id: int,
    target_id: int,
    *,
    participation_disposition: str,
    disposition_reason: str | None,
    disposition_note: str | None,
) -> AssessmentTarget:
    """标记一名目标学生在**这一场测评**里该不该参加（§18.10 那三个减项的写入方）。

    **它不改任何答题事实。** 目标行的 `status`（答没答）、会话、答卷、结果一个不碰
    ——「该不该参加」与「参没参加」是两个维度，这也是这一列存在的全部理由。所以
    「标记为请假」永远只是让这一行不进应测名单，不是「把他标成没完成」。

    四条约定：

    - **写权归心理老师**（`ensure_task_writer`），与学生范围一起判（§9 的第二个入口，
      `ensure_student_in_scope`）——一位只带 CLASS 范围的老师不能改别人班上的行。
    - **非 `REQUIRED` 必须给原因**，`REQUIRED` 不给。原因是一句人写的说明
      （`disposition_reason`，String(128)），**不是码**：§16.10 把「为什么拒绝参加」
      的词表留成了 `TODO_BUSINESS_CONFIRMATION`，编一份没人认得的码表落库，
      比留一句话更糟（缺口 9 那一族的形状）。
    - **恢复成 `REQUIRED` 等于取消标记**：`disposition_reason` / `disposition_note`
      一起清掉。`marked_by` / `marked_at` **两个方向都写**——它们回答的是「谁最后
      动了这一行」，所以一行从「请假」被改回「应测」之后仍然看得出是谁改的。
    - **标记不写审计**（路由写）。服务层不碰审计是全库一致的形状，理由见
      `api/v1/tasks.py` 那一处——那里也是唯一会把「谁标的、从什么改成什么」写下来
      的地方，而那句话今天只有数据库看得见（§8 的缺口：`GET /audit-logs` 不发 `detail`）。
    """
    ensure_task_writer(user)
    if participation_disposition not in PARTICIPATION_DISPOSITIONS:
        raise AppError("VALIDATION_ERROR", "未知的参与状态", 422)
    target = db.get(AssessmentTarget, target_id)
    # 「不是这场任务的」与「不存在」回同一句话：`target_id` 是客户端传来的，
    # 分开报就等于确认了某个 id 存在（§24 那条「不可分辨」）。
    if target is None or target.task_id != task_id:
        raise AppError("NOT_FOUND", "目标学生不存在", 404)
    # 范围拒绝时**不写审计**（§9）：给一次被拒的写入记上「标记参与状态」，
    # 会让访问轨迹反过来撒谎。
    ensure_student_in_scope(db, user, target.student_id)

    reason = (disposition_reason or "").strip()
    note = (disposition_note or "").strip()
    if participation_disposition == PARTICIPATION_REQUIRED:
        target.disposition_reason = None
        target.disposition_note = note or None
    else:
        if not reason:
            raise AppError(
                "VALIDATION_ERROR",
                "标记为请假 / 免测 / 已排除时必须填写原因",
                422,
            )
        target.disposition_reason = reason
        target.disposition_note = note or None
    target.participation_disposition = participation_disposition
    target.marked_by = user.id
    target.marked_at = now_local_naive()
    db.flush()
    return target
