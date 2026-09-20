from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.api.v1.assessment_import import import_rows_payload
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.assessment import AssessmentTarget, AssessmentTask
from app.models.enums import RoleCode
from app.schemas.assessment import (
    CreateAssessmentTaskRequest,
    MarkParticipationRequest,
    SupplementTargetsRequest,
    UpdateAssessmentTaskRequest,
)
from app.schemas.export import ExportRequest
from app.security.permissions import (
    CONTROLLED_EXPORT,
    MANAGE,
    ORG_ACCOUNT,
    READ_BASIC,
    PROGRESS_SUMMARY,
    PSYCH_SUMMARY,
    READ_SUMMARY,
    SCOPED,
    STUDENT_PSYCH_DETAIL,
    require_capability,
)
from app.services.assessment_import_service import (
    list_unmatched_import_rows,
    unmatched_reason_counts,
    unmatched_rows_csv,
)
from app.services.audit_service import write_audit
from app.services.export_service import (
    EXPORT_TYPE_NON_PARTICIPANTS,
    EXPORT_TYPE_TASK_COMPLETION,
    EXPORT_TYPE_UNMATCHED_IMPORT_ROWS,
    MASK_LEVEL_IDENTIFIED,
    create_export_job,
    serialize_export_jobs,
)
from app.services.task_service import (
    create_school_assessment_task,
    list_assessment_tasks,
    list_task_targets,
    mark_target_participation,
    non_participants_csv,
    supplement_targets,
    task_completion,
    task_completion_csv,
    task_participation_counts,
    update_assessment_task,
)

router = APIRouter(tags=["assessment-tasks"])

# RULE_CONFLICT (re-opened and re-resolved 2026-09-17): 测评任务是**学校业务**，
# 不是系统级配置，所以系统管理员整块退出这个功能面（读与写都不再包含 ADMIN）。
# 2026-09-16 那版裁决把读开放给三角色、写留给 ADMIN，理由是 `docs/phase0_rule_freeze.md`
# 写着「管理员管理任务」；用户随后指出那句话本身是错的——管理员只关注系统级配置。
# 写权归心理老师（业务闭环 `测评任务 → 答题 → 评分 → 风险提示 → 复核 …` 的负责人），
# 德育领导保持**只读**：它的能力集是学校级聚合与摘要（§4），是监督口径而不是运营口径。
TASK_READERS = (RoleCode.COUNSELOR, RoleCode.LEADER)

# 目标学生名单上逐行是学号与姓名——组织与账号那一档的数据，所以读它要**两道门槛**
# （任务读者 + 下面这一个），理由与拒绝的角色写在 `task_service.ensure_task_target_reader`
# 里。这里是同一个形状的第二遍：路由这一遍让权限出现在 OpenAPI 里，服务那一遍让
# 直接调用服务的人也拿不到越权数据。
TaskTargetReader = Annotated[
    UserAccount, Depends(require_capability(ORG_ACCOUNT, allow={MANAGE, READ_BASIC, READ_SUMMARY}))
]

# **逐人明细**在任务读者之外的**两道**门槛，判据与理由见
# `task_service.ensure_detail_exporter`。分开声明而不是合成一个依赖：
# 它们问的是两件事——「他有没有导出许可」（§16.3，这几份都是离开这栋楼的文件）与
# 「他能不能看心理详情明细」（§4，未参与名单逐行给出谁没来测这场心理普查，
# 未匹配行里「匹配上了但被放弃」的那些说的是同一件事）。
# `allow` 都写成显式集合：**描述性等级不可互换**，`SUMMARY` 只是聚合，而这几份是逐人明细。
#
# `ControlledExporter`（导出许可）只有**建作业**的那几个端点要；`DetailExporter`
# 是「逐人心理明细」本身的门槛，所以 `GET …/completion` 也用它——那个端点不生成文件，
# 但它返回的是同一批逐人数据加等级列（2026-09-20，缺口 12）。
ControlledExporter = Annotated[
    UserAccount,
    Depends(require_capability(CONTROLLED_EXPORT, allow={PSYCH_SUMMARY, PROGRESS_SUMMARY})),
]
DetailExporter = Annotated[
    UserAccount,
    Depends(require_capability(STUDENT_PSYCH_DETAIL, allow={SCOPED})),
]


@router.get("/assessment-tasks")
def tasks(
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
):
    return ok({"items": list_assessment_tasks(db, current_user)})


@router.post("/assessment-tasks")
def create_task(
    payload: CreateAssessmentTaskRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    task = create_school_assessment_task(
        db,
        current_user,
        name=payload.name,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    write_audit(
        db,
        action="创建测评任务",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task.id),
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok({"id": task.id, "task_no": task.task_no})


@router.patch("/assessment-tasks/{task_id}")
def update_task(
    task_id: int,
    payload: UpdateAssessmentTaskRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    task = update_assessment_task(
        db,
        current_user,
        task_id,
        name=payload.name,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    write_audit(
        db,
        action="编辑测评任务",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task.id),
        actor=current_user,
        request=request,
    )
    db.commit()
    return ok({"id": task.id, "task_no": task.task_no, "name": task.name})


@router.get("/assessment-tasks/{task_id}/targets")
def task_targets(
    task_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
    _roster: TaskTargetReader,
):
    """这场测评发给了谁（§8.2 / §16.2）。

    两个依赖是两道门槛，缺一不可，理由与拒绝的角色见
    `task_service.ensure_task_target_reader`——这个端点的每一行都是某一名学生的学号
    与姓名，那是「组织与账号」那一档的数据，而任务那一道只回答「他能不能读这场测评」。
    """
    return ok(list_task_targets(db, current_user, task_id))


@router.post("/assessment-tasks/{task_id}/targets/supplement")
def supplement_task_targets(
    task_id: int,
    payload: SupplementTargetsRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    """补发目标学生：`confirm=false` 看候选，`confirm=true` 落行（§8.1 / §16.2）。

    写权归心理老师，与建任务同一个判据（§4：测评任务不是系统级配置，所以 ADMIN
    读写都不在）。

    **审计只写在确认那一步。** 预览没有改变任何东西，给它记一行「补发目标学生」
    会让轨迹上出现两条一字不差的记录，而其中一条什么也没做——读轨迹的人没有办法
    分辨它们。这也是「被拒的读取不写审计」（§9）同一条道理的另一面：审计记的是
    发生过的事，不是发生过的请求。
    """
    result = supplement_targets(db, current_user, task_id, confirm=payload.confirm)
    if payload.confirm:
        # 原因、补发前后人数都进 `detail`：§16.2 要求补发记下这三样。同一场任务补发
        # 两次时 action / resource_type / resource_id 逐字相同，只有这几个数能回答
        # 「那一次补了几个人、为什么」（同 §8 导出必须记遮蔽模式的道理）。
        write_audit(
            db,
            action="补发目标学生",
            resource_type="ASSESSMENT_TASK",
            resource_id=str(task_id),
            actor=current_user,
            request=request,
            detail=(
                f"原因={payload.reason}, 补发前={result['before']}, "
                f"补发后={result['after']}, 新增={result['added']}"
            ),
        )
        db.commit()
    return ok(result)


@router.get("/assessment-tasks/{task_id}/unmatched-import-rows")
def task_unmatched_import_rows(
    task_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
    _roster: TaskTargetReader,
):
    """这场任务下**没进得去的**导入行（§18.10 / §18.4）。

    两个依赖是两道门槛，与 `task_targets` 逐字同一形状、同一理由：这一页逐行给出
    学号与姓名（组织与账号那一档的数据），而任务那一道只回答「他能不能读这场测评」。
    第二道在服务里再查一次——直接调用服务的人（测试、将来的批处理）也拿不到越权数据。

    它补的是 §18.4 那条出路的最后一环：一份文件里有几行没匹配上时，那些行从此有了
    一个**可查的地方**（在它们所属的那场任务下面），而不是只活在那一次上传的返回值里。
    """
    result = list_unmatched_import_rows(db, current_user, task_id)
    # **逐行的形状复用导入明细那一处**（`import_rows_payload`），不在这里另拼一份：
    # 同一条记录在两个屏幕上（任务详情页的「未匹配行」与数据中心那个明细弹层）必须
    # 逐字相同——`match_status` / `message` / `out_of_scope_reason` 都是界面按它分岔的
    # 判据，两处各写一份序列化就是两个定义，而它们漂移了不会有任何东西看得见。
    return ok({**result, "items": import_rows_payload(db, result["items"])})


@router.post("/assessment-tasks/{task_id}/unmatched-import-rows/export")
def unmatched_import_rows_export(
    task_id: int,
    payload: ExportRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
    _roster: TaskTargetReader,
    _controlled: ControlledExporter,
    _psych: DetailExporter,
):
    """未匹配行清单导出（§20#14 的「导出」那一半）——**建作业**，文件由
    `/export-jobs/{id}/download` 发。

    §20#14 的原话是「未匹配、任务外、重复、冲突记录均可以**查询和导出**」。查询那一半
    阶段 5 交付了（上面那个 GET + 数据中心的明细弹层）；导出这一半此前**没有落点**，
    而 §18.11 冻结的九个端点里也没有它——那九个里最接近的一个（未参与名单）导的是
    **应测未完成的学生**，是另一批人：这里的行压根没进过名册匹配，或者进来了又被放弃。

    那一句为什么值得一个端点：这四类行的处置在界面上逐行摆着取舍（补名册 / 补发 /
    选覆盖），而**做决定的人往往不在那台电脑前**——一份清单是这件事的载体。文件里
    逐行给出「哪一批、第几行、文件里写的是谁、为什么没进来」，而这三样正是复核要的。

    **四个依赖是四道门槛。** 后三道与上面那个 GET 逐字同一组（服务侧同样两遍各自再查
    一次），因为它导的是**同一批行的同七列**——读得到就该导得出，读不到就不该导得出。
    最要命的一种改法是**只给导出加一半**：GET 要 `ORG_ACCOUNT` 而导出不要，那份文件
    就成了绕开名册门槛的旁路（同一个道理：`GET /students/results` 那两道，§4）。

    | 门槛 | 问题 | 今天谁被它挡 |
    |---|---|---|
    | 任务读者 | 他能不能读这场测评 | 系统管理员（§4：任务是角色，不是能力） |
    | `ORG_ACCOUNT` | 他能不能读组织名册 | **今天没有角色只被这一道挡住**（三个角色的取值范围都落在 `{MANAGE, READ_BASIC, READ_SUMMARY}` 里）——它挡的是「哪天有人把某一档收窄」以及上面那句旁路 |
    | `CONTROLLED_EXPORT` | 他有没有导出许可 | 系统管理员（`BASE_ONLY`）——但它已经先被角色那道挡住了 |
    | `STUDENT_PSYCH_DETAIL: {SCOPED}` | 他能不能看心理详情**明细** | **德育领导**（`SUMMARY` 只是聚合），今天真正在被这一道挡住的就是它 |

    服务侧那两道与未参与名单导出**逐字同一个函数**（`ensure_detail_exporter`），
    所以两个端点的「逐人明细导出要什么资格」不可能分岔。

    `mask_level` 记 **`IDENTIFIED`**：这一份写着文件里的真实姓名（`raw_name` 是操作员
    从学校文件里读进来的字，不是遮蔽列），这一列回答的是「这份文件是不是实名的」——
    照实记，别记成 `MASKED`。

    行数记在 `export_job.row_count` 上，而它与屏幕上那句「整场共 N 行没进得去」
    **口径不同**：这一份套读者范围、那一句数整场（§9：范围数字要写明口径，而这里
    两个数各自都写在能看见的地方）。所以审计的 `detail` 只报这一份文件的行数，
    不冒充整场。
    """
    document = unmatched_rows_csv(db, current_user, task_id)
    job = create_export_job(
        db,
        current_user,
        export_type=EXPORT_TYPE_UNMATCHED_IMPORT_ROWS,
        purpose=payload.purpose or "",
        document=document,
        mask_level=MASK_LEVEL_IDENTIFIED,
    )
    write_audit(
        db,
        action="导出未匹配行",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task_id),
        purpose=job.purpose,
        actor=current_user,
        request=request,
        detail=f"未匹配行 {job.row_count} 行 · 实名 · 作业 {job.job_no}",
    )
    db.commit()
    return ok(serialize_export_jobs(db, current_user, [job])[0])


@router.get("/assessment-tasks/{task_id}/participation")
def task_participation(
    task_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
):
    """这场测评的参与口径六个数（§18.10）：目标 / 请假免测已排除 / 应测 / 已完成 / 有效完成率。

    另加一类**不在这张表里**的数：`unimported_records`（任务外、重复、未匹配、冲突的
    导入记录）。规格那句话的后半截是「它们不进入任务完成率」——而它们本来就住在
    `assessment_import_row` 上、从来不在目标行里，所以那半句是**构造上成立的**。
    把它单独数出来给读者看，是因为「这场任务有多少行没进来」是一句学校要问的话，
    而它不属于上面那五个数中的任何一个（混进去会让分母变成一个说不清的东西）。

    `excluded_targets` 是那三个减项的**和**，不拆成请假 / 免测 / 已排除三列：
    §16.10 把「为什么拒绝参加」的词表留成了 `TODO_BUSINESS_CONFIRMATION`，
    而今天落库的是一句人写的原因（`disposition_reason`），逐行明细里逐条列着。
    要拆成三个数，得先有那套码表——从自由文本反推分类是猜（§21 那条「认不出的当场
    报错、不猜默认值」）。
    """
    if db.get(AssessmentTask, task_id) is None:
        raise AppError("NOT_FOUND", "测评任务不存在", 404)
    counts = task_participation_counts(db, task_id)
    # 整场任务的未匹配行数，**不是**逐行明细那一份的 `len(items)`：后者被
    # `UNMATCHED_ROW_LIMIT` 封顶在 200，拿它当计数会在第 201 行上开始说谎（§10
    # 「凡是截断都要自己说出来」，§11「指标卡上的数必须与它点进去的列表同源」）。
    # `unmatched_reason_counts` 数的是整场任务、不套读者的范围，与任务详情页上
    # 那枚药丸用的是同一个函数。
    counts["unimported_records"] = sum(unmatched_reason_counts(db, task_id).values())
    return ok(counts)


@router.patch("/assessment-tasks/{task_id}/targets/{target_id}/participation")
def mark_participation(
    task_id: int,
    target_id: int,
    payload: MarkParticipationRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(RoleCode.COUNSELOR))],
    db: Annotated[Session, Depends(get_db)],
):
    """标记一名目标学生在**这一场**里该不该参加（§18.10 那三个减项的写入方）。

    写权归心理老师，与建任务 / 补发同一个判据（§4：测评任务不是系统级配置）。
    服务层另外还过一道 `ensure_student_in_scope`——「他是心理老师」不说「他是**这名**
    学生的心理老师」，所以一位只带 CLASS 范围的老师改不动别人班上的行（§9）。

    **它一行答题事实都不动。** 目标行的 `status`、会话、答卷、结果一个不碰：
    「该不该参加」与「参没参加」是两个维度，标记为请假永远只是让这一行不进应测名单。

    审计记三个东西：目标学生、**改成了什么**、原因。`detail` 留原文（`NONE` 而不是
    留空——留空与「没问过」分不开），因为「从请假改回应测」也是一次真实的改动，
    而只记新值的话，轨迹上那一条与「本来就是应测」长得一样。
    """
    previous = db.get(AssessmentTarget, target_id)
    before = previous.participation_disposition if previous else None
    target = mark_target_participation(
        db,
        current_user,
        task_id,
        target_id,
        participation_disposition=payload.disposition,
        disposition_reason=payload.reason,
        disposition_note=payload.note,
    )
    write_audit(
        db,
        action="标记参与状态",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task_id),
        actor=current_user,
        request=request,
        detail=(
            f"目标 {target_id} · {before or 'NONE'} → {target.participation_disposition}"
            f" · 原因={target.disposition_reason or 'NONE'}"
        ),
        student_id=target.student_id,
    )
    db.commit()
    return ok(
        {
            "target_id": target.id,
            "student_id": target.student_id,
            "participation_disposition": target.participation_disposition,
            "disposition_reason": target.disposition_reason,
            "disposition_note": target.disposition_note,
        }
    )


@router.get("/assessment-tasks/{task_id}/completion")
def completion(
    task_id: int,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
    _roster: TaskTargetReader,
    _psych: DetailExporter,
):
    """这一场测评的逐人完成明细（学号 / 姓名 / 年级 / 班级 + 关注等级 / MHT总分）。

    **三道依赖是三道门槛**，问的是三件不同的事，缺一道就漏一种洞：

    | 门槛 | 问题 | 谁被挡 |
    |---|---|---|
    | 任务读者 | 他能不能读这场测评 | 系统管理员（§4：任务是角色，不是能力） |
    | `ORG_ACCOUNT` | 他能不能读名册上的身份列 | ——（与「目标学生」页签同口径，见下） |
    | `STUDENT_PSYCH_DETAIL: {SCOPED}` | 他能不能看逐人的心理结果 | **德育领导**（`SUMMARY` 只是聚合） |

    最后那一道是 2026-09-20 缺口 12 的处置。在此之前这里只有任务读者，而
    `TASK_READERS` 含德育领导——于是他在同一个响应里拿到学号 / 姓名 / 班级**与**
    `total_level` / `total_score`：**逐人的等级**，而 `SUMMARY` 恰恰不是逐人明细。
    同一页上的「目标学生」页签（`GET …/targets`）有身份列、没有等级列，它按
    `TaskTargetReader` 放行领导；这一页多出来的正是等级列，所以多出来的这道门槛落在
    心理详情上，而不是把身份列也一并收紧——否则同一屏上两个页签会对同一个人
    「能读名单、读不了它的明细」。§4 那条 `GET /students/results` 是同一个形状
    （身份列归 `ORG_ACCOUNT`、等级列归心理详情，两列都齐的端点两道都要）。

    `TASK_READERS` 与其余页签一致地留着：`ensure_task_reader` 回答的是「他能不能读
    这场测评」，与上面两道不是同一件事。

    服务那一遍在 `task_completion` 里（`ensure_task_reader` + `ensure_detail_reader`），
    与 `non_participants_export` 的 docstring 记的是同一条代价：单独拆掉任何一遍都不会
    让测试变红，两遍一起拆才会。
    """
    return ok({"items": task_completion(db, current_user, task_id)})


@router.post("/assessment-tasks/{task_id}/completion/export")
def completion_export(
    task_id: int,
    payload: ExportRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
    _roster: TaskTargetReader,
    _controlled: ControlledExporter,
    _psych: DetailExporter,
):
    """完成明细导出——**建作业**，文件由 `/export-jobs/{id}/download` 发。

    从 `GET` 改成 `POST`（2026-09-19 阶段 8）：它现在**不是只读的**——每次调用建一行
    `export_job`、占一个编号、在盘上落一份文件。GET 的语义是「可以重复取、没有副作用」，
    而这条路重放一次就是第二份作业，所以它不再是个 GET。同一批改动的另三条导出
    （`audit.py` 那三个）本来就是 POST。

    `purpose` 是必填的（判据在 `create_export_job` 里），与其余受控导出同一条：
    导出这件事要回答「这份文件为什么被导出去」，而那句话不在文件里。

    **四道依赖是四道门槛**，与 `non_participants_export` 逐字同一形状（`GET …/completion`
    那三道再加一个导出许可）——2026-09-20 缺口 12 的处置：在此之前这里只有任务读者，
    而它逐行印出学号 / 姓名**与关注等级 / MHT总分**，且 `mask_level=IDENTIFIED`。
    一位 `STUDENT_PSYCH_DETAIL` 只是 `SUMMARY` 的德育领导，能把它导成一份离楼的实名
    名单，而他在 `/students` 上连按行姓名都拿不到——§4 那句「受控导出不能成为绕过
    心理详情的旁路」原样成立。服务那一遍在 `ensure_detail_exporter`（后两道）与
    `task_completion`（第三道）里。

    `mask_level` 记 **`IDENTIFIED`**。这一列回答的是「这份文件是不是实名的」，不是
    「我们打不打算遮蔽」——而这份 CSV 写的是学号加真实姓名（`task_completion` 取的是
    `student.masked_name`，而那一列按 CLAUDE.md §1 在生产里与 `name` 一样实）。
    照实记，别记成 `MASKED`。
    """
    document = task_completion_csv(db, current_user, task_id)
    job = create_export_job(
        db,
        current_user,
        export_type=EXPORT_TYPE_TASK_COMPLETION,
        purpose=payload.purpose or "",
        document=document,
        mask_level=MASK_LEVEL_IDENTIFIED,
    )
    write_audit(
        db,
        action="导出任务完成统计",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task_id),
        purpose=job.purpose,
        actor=current_user,
        request=request,
        detail=f"完成明细 {job.row_count} 行 · 实名 · 作业 {job.job_no}",
    )
    db.commit()
    return ok(serialize_export_jobs(db, current_user, [job])[0])


@router.post("/assessment-tasks/{task_id}/non-participants/export")
def non_participants_export(
    task_id: int,
    payload: ExportRequest,
    request: Request,
    current_user: Annotated[UserAccount, Depends(require_role(*TASK_READERS))],
    db: Annotated[Session, Depends(get_db)],
    _controlled: ControlledExporter,
    _psych: DetailExporter,
):
    """未参与名单导出（§18.11 第九个端点）——**建作业**，文件由 `/export-jobs/{id}/download` 发。

    内容是这场测评里**应测但没完成**的那些人（学号 / 姓名 / 年级 / 班级 / 性别 / 年龄 /
    参与状态 / 分配时间）。请假 / 免测 / 已排除的那几行**不进这一份**：他们不是「没测」，
    是学校已经决定他这次不测，混进来会把「还有谁要催」变成「这场测评的全部非完成行」。

    **三个依赖是三道门槛**，问的是三件不同的事，缺一道就漏一种洞：

    | 门槛 | 问题 | 谁被挡 |
    |---|---|---|
    | 任务读者 | 他能不能读这场测评 | 系统管理员（§4：任务是角色，不是能力） |
    | `CONTROLLED_EXPORT` | 他有没有导出许可 | 系统管理员（`BASE_ONLY`） |
    | `STUDENT_PSYCH_DETAIL: {SCOPED}` | 他能不能看心理详情的**明细** | 德育领导（`SUMMARY` 只是聚合） |

    形状照 §4 那条 `GET /students/results`：**两处各查一次**（路由这三道 + 服务里
    `ensure_detail_exporter` 再查后两道），代价是单独拆掉任何一遍都不会让测试
    变红——两遍一起拆才会。别把「拆了一处仍然全绿」读成守卫失效。

    那个函数 2026-09-20 从 `ensure_non_participant_exporter` 改名而来：它现在也是
    第十个端点（未匹配行导出，`unmatched_import_rows_export`）的服务侧判据，
    逐字同一对门槛。两处各自写一份就等于「逐人明细导出要什么资格」有两个定义。

    `mask_level` 记 **`IDENTIFIED`**：这一份写的是学号与真实姓名（`task_completion` 取
    `student.masked_name`，而那一列按 CLAUDE.md §1 在生产里与 `name` 一样实），这一列
    回答的是「这份文件是不是实名的」——照实记，别记成 `MASKED`。
    """
    document = non_participants_csv(db, current_user, task_id)
    job = create_export_job(
        db,
        current_user,
        export_type=EXPORT_TYPE_NON_PARTICIPANTS,
        purpose=payload.purpose or "",
        document=document,
        mask_level=MASK_LEVEL_IDENTIFIED,
    )
    write_audit(
        db,
        action="导出未参与名单",
        resource_type="ASSESSMENT_TASK",
        resource_id=str(task_id),
        purpose=job.purpose,
        actor=current_user,
        request=request,
        detail=f"未参与 {job.row_count} 人 · 实名 · 作业 {job.job_no}",
    )
    db.commit()
    return ok(serialize_export_jobs(db, current_user, [job])[0])
