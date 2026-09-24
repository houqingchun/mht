"""数据中心 → MHT测评记录导入的路由。

权限：照 `scales.py` 的 `ScaleImporter` 用角色依赖（管理员 + 心理老师），不新增第六类能力。
这条链路写的是**测评事实**，不建账号、不动组织结构——与学生信息导入（组织与账号治理，
仅管理员）的风险性质完全不同。反过来说，它也不改判定标准：导入的记录用当前的量表规则
评分，改规则仍然是管理员的事。

五个端点。前两个是这一批的动作（上传即建批次并逐行匹配 / 提交），后三个是这一批的
**可查性**（批次历史 / 逐行明细）。命名上 `/assessment-import/template` 是单数、
批次那四个是 `/assessment-imports` 复数——**不是笔误**：模板与批次无关（它是一份
空白 CSV 的形状），而 §18.11 的接口草案给批次定的就是复数。模板那个路径 V1.0 就有，
前端 `api.ts` 在用，改名会让它变成一条 404 而不报错。
"""

import hashlib
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_role
from app.core.errors import AppError, ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.enums import RoleCode
from app.models.importing import AssessmentImportBatch, AssessmentImportRow
from app.models.organization import Student
from app.schemas.assessment_import import (
    AssessmentImportCommitRequest,
    AssessmentImportRowResolveRequest,
)
from app.services.assessment_import_service import (
    age_resolution_label,
    batch_row_counts,
    commit_batch,
    list_import_batches,
    list_import_rows,
    parse_assessment_import,
    resolution_label,
    resolve_import_row,
    row_durations,
    school_for_import,
    start_assessment_import,
    template_csv,
)
from app.services.audit_service import write_audit

router = APIRouter(tags=["assessment-import"])

AssessmentImporter = Annotated[
    UserAccount, Depends(require_role(RoleCode.ADMIN, RoleCode.COUNSELOR))
]


@router.get("/assessment-import/template")
def assessment_import_template(_: AssessmentImporter):
    """模板。

    与「学生信息导入」的模板不同，这份模板是后端生成的：题号列有 100 个，题干由
    量表决定，前端再抄一遍就会漂移。性别与答案的取值（`1/2`、`1/0`）也是外部平台的
    约定，写在文件里比写在说明里更不容易丢。
    """
    return Response(content=template_csv(), media_type="text/csv; charset=utf-8")


@router.post("/assessment-imports/preview")
async def assessment_import_preview(
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    batch_name: str = Form(""),
    tested_on: str = Form(""),
    task_id: str = Form(""),
    source_system: str = Form(""),
):
    """上传一份外部测评记录：逐行匹配，**并把这一批落进库**。

    这是 V1.2 对 V1.0 那一步的实质改动。从前预览的结论只活在返回值里（签进一个
    JWT 令牌），库对这批数据一无所知：操作员上传完被叫走、回到这一页时它是空的，
    只能重传一次——而重传的那一份与刚才屏幕上那份是不是同一份，没有任何东西能回答。
    更要紧的是「先补名册、再重新匹配」那条出路当时不存在：令牌是死的。

    现在批次（`PREVIEW`）与逐行明细都在库里，**答案也在**（落在
    `assessment_external_result.result_payload_json` 里，见那个函数的 docstring）。
    重新上传同一个文件会**复用同一个批次**并重建行——那就是「重新匹配」，
    而它必须重传文件，因为答案只在文件里，而系统不存上传的文件本身。

    **不写审计**：批次行自己就是这条记录，而它比一行审计更全（文件指纹、操作者、
    五个计数、逐行结论，全在同一页上可查）。为一次「上传了但还没拍板」的动作写一行
    `导入测评记录`，会让审计页把这件事说成一件已经发生的事——而测评记录一条都没写。
    审计写在那一次真正落库的提交上。
    """
    content = await file.read()
    parsed = parse_assessment_import(file.filename or "assessment.csv", content)
    batch = start_assessment_import(
        db,
        parsed,
        file_name=file.filename or "assessment.csv",
        file_sha256=_sha256(content),
        file_size=len(content),
        batch_name=batch_name.strip(),
        tested_on=parse_tested_on(tested_on),
        actor=current_user,
        task_id=_parse_optional_int(task_id, "任务 id"),
        source_system=source_system.strip() or None,
    )
    # 这个 `db.commit()` 不是可有可无的，理由与「学生名册导入」那一处逐字相同
    # （见 `student_roster.py` 的长注释）：**预览从 V1.2 起是一次写入**，而
    # `get_db` 只管开与关、从不提交（`db/session.py`：`finally: db.close()`）。
    # 少这一句的后果不是「预览看不到」，而是整条链路断在第一跳：客户端拿到
    # `batch_id` 去提交，服务按 id 去库里查——那里什么都没有，于是回一句 404
    # 「导入批次不存在」，而屏幕上刚刚还写着「共 213 行」。
    #
    # **后端测试看不见这一句**（CLAUDE.md §24 记了那个夹具盲点）：`conftest.py` 把
    # `get_db` 覆盖成那个共享的 `db_session`，而它在请求结束后不关闭也不回滚，
    # 所以「写了但没提交」的行对测试照样查得到。唯一的网眼是 e2e（真服务、真连接）
    # 与 `test_the_preview_survives_the_request_that_made_it`。
    db.commit()
    # 逐行明细直接拼上来（上传之后屏幕上要立刻显示每一行的结论，再让前端多发一次
    # 请求是白等一个往返——而这一次的数据本来就在手上）。**走的是同一处范围过滤**
    # （`list_import_rows`）而不是把刚建的行原样发出去：判定只有一处，于是上传之后
    # 屏幕上的东西与刷新之后走 `/rows` 拿到的，不可能不一样。
    result = list_import_rows(db, batch, actor=current_user)
    payload = _batch_payload(db, batch, row_counts=batch_row_counts(db, batch))
    payload["rows"] = import_rows_payload(db, result["items"])
    # `row_total` 是**读者可见的**行数，与 `row_counts` 与 `total_rows` 都不是一个数
    # （见 `batch_row_counts` 的 docstring）。三个数各有各的口径，界面上各写各的，
    # 不许互相顶替——“凡是截断都要自己说出来”（§10）说的正是这件事。
    payload["row_total"] = result["total"]
    return ok(payload)


@router.post("/assessment-imports/{batch_id}/commit")
def assessment_import_commit(
    batch_id: int,
    payload: AssessmentImportCommitRequest,
    request: Request,
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
):
    """把这一批写进测评记录。

    批次里还有需要拍板的行（年龄冲突 / 多候选 / 重复 / 与在线答卷冲突）时，
    `commit_batch` 抛 422 且**任何写入之前**——「任何未处理的冲突不得进入正式结果」
    是 §18.6 的原话。
    """
    batch = load_batch(db, batch_id)
    result = commit_batch(
        db, batch, current_user, payload.resolution, payload.age_resolution
    )
    write_audit(
        db,
        action="导入测评记录",
        resource_type="ASSESSMENT_TASK",
        # 整批都被放弃时没有任务可指（`commit_batch` 不建空任务），
        # 这一行于是只有 `detail` 里的那几个数说得清发生了什么
        resource_id=str(result["task_id"]) if result["task_id"] else None,
        actor=current_user,
        request=request,
        # **记批次号，不记批次名称、也不记原始文件名。** 三个都摆在这里时，只有一个是
        # 真正的标识：`batch_name` 由操作员自己填，同一次普查分两批导（初一一份、初二
        # 一份）时两个批次叫同一个名字是常态；`file_name` 是操作员电脑上的东西
        # （`结果(3).csv`），与这次动作的后果无关。`batch_no`（`BATCH-20260919-1`）是
        # 这一批唯一的名字，也印在界面上，所以它能回答「那一条记录属于哪一批」。
        #
        # 测评导入是反复发生的动作（每学期一次普查、改完重导），不记批次号时
        # 「去年九月那批是谁导的」只能靠时间去猜，而同一分钟里可能有两批。
        #
        # `updated` / `resolution` 必须记：同一批记录导两遍，一遍是「新建一批」、
        # 一遍是「覆盖上次」，两者的 action / resource_type / resource_id 逐字相同，
        # 只有这几个数能回答「那几条去哪了」（同 §8 导出必须记遮蔽模式的道理）。
        # `age_updated` 指向的是**另一张表**（名册）的改动，这里只记条数，
        # 改了谁、从多少改到多少写在 `更新学生年龄` 那些行与 `student_age_change_log` 上。
        detail=(
            f"batch={result['batch_no']}, created={result['created']}, "
            f"updated={result['updated']}, skipped={result['skipped']}, "
            # **`not_applied` 与 `skipped` 不是一回事，两者必须都在。** 两者都不建
            # 外部会话，所以只看得见 `skipped` 时，「这一批里有 3 行是放弃了、还是裁成
            # 了『保留在线』」答不上来——而后者意味着那 3 名学生**是有在线答卷的**，
            # 前者的那 3 行则什么都没有。§18.8 那条「来源裁决要留痕」在这一行上的落点
            # 就是这几个数。
            f"not_applied={result['not_applied']}, "
            f"age_updated={result['age_updated']}, 处置={resolution_label(payload.resolution)}"
            # **年龄那一个选择也要留在轨迹上**（§18.5 那张表的第三列）。它写进了
            # `row.age_resolution`，而那一列是逐行的、要翻明细才看得见；这一行是
            # 「这一次导入到底按哪种口径处理了年龄」唯一的整批说法——「保留系统年龄」
            # 与「只保存本次测评年龄」在 `age_updated=0` 上长得一模一样，只有当次的
            # 操作员分得出它们（用的是 `age_resolution_label`，与 `处置=` 同一处口径）。
            f", 年龄处置={age_resolution_label(payload.age_resolution)}"
            # 逐行处置过的行数。一份 200 行的普查里「那 3 条年龄不符是逐条定的、还是
            # 整批点的覆盖」是一个事后要能回答的问题，而两者在 `row.resolution` 上
            # 长得一样（`resolved_by` 只在逐行时写）。**`处置=未涉及（无冲突）` 与
            # `resolved_rows=3` 同时出现是正常的**：整批没问过，但有人逐行处置过。
            f", resolved_rows={result['resolved_rows']}"
        ),
    )
    db.commit()
    return ok(result)


@router.get("/assessment-imports")
def assessment_import_batches(
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
):
    """最近导入过哪几批（最近的在前，最多 20 条）。

    逐行明细**不在这个响应里**：一批普查是几百行，把每一批的行全带上会让这一页
    为了显示 20 行批次而传输几万行。要明细走下面那个端点。
    """
    result = list_import_batches(db, school=school_for_import(db))
    actors = _actors(db, [batch.imported_by for batch in result["items"]])
    return ok(
        {
            "items": [
                _batch_payload(db, batch, actor_names=actors) for batch in result["items"]
            ],
            "total": result["total"],
        }
    )


@router.get("/assessment-imports/{batch_id}/rows")
def assessment_import_batch_rows(
    batch_id: int,
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    match_group: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
):
    """某一批的逐行明细：文件里那一行长什么样、匹配到了谁、为什么没匹配上。

    **这一层按读者的数据范围过滤**（见 `list_import_rows`）：批次是共享的，而它
    逐行给出姓名与学号。服务端按 `limit/offset` 分页，避免大批次一次传输全部明细。

    `match_group` 与 `keyword` 是这一页的筛选条件，**都在服务端生效**——分页是
    服务端的，在客户端筛只能筛当前那一页（见 `list_import_rows`）。两者都可省：
    省掉就是「不过滤」，与这个参数加进来之前的行为逐字相同。

    `keyword` 这里只去空白、把空串归成 `None`；**不在这里判它认不认得**——
    它是自由文本，没有认不得不认得这一说。`match_group` 是枚举，认不出的由
    服务层回 422（在那边判，是因为那张表也住在那边）。
    """
    batch = load_batch(db, batch_id)
    result = list_import_rows(
        db,
        batch,
        actor=current_user,
        match_group=match_group,
        keyword=(keyword or "").strip() or None,
        limit=limit,
        offset=offset,
    )
    return ok(
        {
            "items": import_rows_payload(db, result["items"]),
            "total": result["total"],
            "batch_id": batch.id,
            # 这一页的**主体**是行，但那一行「待确认 N 条」的提示要能跟着刷新：
            # 操作员逐行处置之后再拉一次明细，提交按钮旁边那句话必须已经变了。
            "row_counts": batch_row_counts(db, batch),
        }
    )


@router.patch("/assessment-import-rows/{row_id}/resolve")
def assessment_import_row_resolve(
    row_id: int,
    payload: AssessmentImportRowResolveRequest,
    request: Request,
    current_user: AssessmentImporter,
    db: Annotated[Session, Depends(get_db)],
):
    """逐行处置一条待拍板的导入行（§18.6 的「逐行处理」）。

    与整批提交（`/commit`）的关系是**逐行覆盖整批**：处置过的行不再受那一次整批选择
    左右。**这个端点不写任何测评记录**——真正的落库仍然全部发生在提交那一刻，
    理由与预览那一层相同（一个动作在它真的发生之前，库里不该出现它的后果）。

    权限与批次那五个端点同一档（管理员 + 心理老师）：它写的是测评事实的处置，
    不建账号、不动组织结构。行不属于这所学校时回 404，判据与措辞都与
    `load_batch` 同一句（「不属于你」与「不存在」在响应上必须不可分辨）。

    审计在这里写而不是在服务里，与 `/commit` 那一处同形：`write_audit` 要 `request`
    才记得到 ip 与 user-agent，而服务层拿不到它。这一行说的确实是「哪一行」——
    `resource_id` 是行 id、`detail` 里带批次号与行号——所以它指得回具体那一条记录。
    用 `ASSESSMENT_TASK` 作 `resource_type` 的话 `resource_id` 只能是任务 id，
    而一场任务下有几百行，那样这一条轨迹答不上「动的是哪一行」。
    """
    row = load_row(db, row_id)
    result = resolve_import_row(
        db,
        row,
        actor=current_user,
        resolution=payload.resolution,
        age_resolution=payload.age_resolution,
        conflict_resolution=payload.conflict_resolution,
    )
    write_audit(
        db,
        action="处置导入记录",
        # 新的 `resource_type` 码。**它没有中文映射**（审计页那一列本来就裸渲染，
        # 见缺口 7），这是知情的代价：这一列要等 §缺口 7 那三件事一起做。
        resource_type="ASSESSMENT_IMPORT_ROW",
        resource_id=str(result["row_id"]),
        actor=current_user,
        request=request,
        student_id=row.student_id,
        # 编码不中文（与 §4 的 `update_permissions` 同一个写法）：这一串是给检索用的，
        # 而 `detail` 目前没有读者（缺口 7）。
        #
        # **处置之后那两个值才是最终值**：`row.resolution` 可能是这个人这一次刚写的，
        # 也可能是他上一次写的（这一次只改了年龄那一项），而轨迹要回答的是
        # 「处置完之后这一行是什么状态」——那是提交时真正会被读到的值。
        detail=(
            f"batch={result['batch_no']}, row={result['row_no']}, "
            f"match_status={result['match_status']}, "
            f"resolution={row.resolution or 'NONE'}, "
            f"age_resolution={row.age_resolution or 'NONE'}, "
            # §18.8 的四档处置。**它是这一族里唯一「不能被整批选择替代」的一项**，
            # 所以轨迹里尤其要有它：事后要能回答「那两条在线答卷当时是谁、按哪一档
            # 裁的」——`KEEP_ONLINE` 与 `REJECT_EXTERNAL` 在库里留下的差别只有
            # 一个 `verification_status`，而这一串 `detail` 今天**只有数据库看得见**
            # （`GET /audit-logs` 不发 `detail`，§8），所以想让这件事在界面上可查，
            # 要动的是那条序列化，不是这一行。
            f"conflict_resolution={row.conflict_resolution or 'NONE'}"
        ),
    )
    db.commit()
    return ok(result)


def load_batch(db: Session, batch_id: int) -> AssessmentImportBatch:
    """按 id 取批次，**不属于这所学校**时回 404（不回 403）。

    与「学生名册导入」同一处判据、同一个理由：`batch_id` 是客户端传来的，403 会
    告诉一个够不着它的人「这个 id 是存在的」——而那足以让另一所学校的批次号被一个个
    试出来。三种情况（不存在 / 不是这所学校的）回**同一句话**。
    """
    batch = db.get(AssessmentImportBatch, batch_id)
    school = school_for_import(db)
    if batch is None or school is None or batch.school_id != school.id:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    return batch


def load_row(db: Session, row_id: int) -> AssessmentImportRow:
    """按 id 取一行，**它所属的批次不属于这所学校**时回 404（不回 403）。

    与 `load_batch` 同一处判据、同一个理由（那一行的 `batch_id` 是客户端传来的，
    它本身就是一份越权凭据），所以措辞也共用一句「导入批次不存在」——它说的是
    「这一行不在你能碰的那一批里」，而不是「你查的东西是另一所学校的」。
    """
    row = db.get(AssessmentImportRow, row_id)
    if row is None:
        raise AppError("NOT_FOUND", "导入批次不存在", 404)
    load_batch(db, row.batch_id)
    return row


def _batch_payload(
    db: Session,
    batch: AssessmentImportBatch,
    *,
    actor_names: dict[int, UserAccount] | None = None,
    row_counts: dict[str, int] | None = None,
) -> dict:
    """一批的对外形状。

    **逐行明细不在里面**，由调用方按需另外取（上传那一次把它拼上来，见路由）。
    批次列表那一页有 20 行，把每一批的行全带上会让它为了显示 20 行而传输几万行。

    `actor_names` 由调用方**一次取好**传进来（20 行逐行去查操作人就是 20 次 SELECT；
    §8 那条「一次查出来、不是逐行查」）。给了就用自己的，否则现查这一次。

    `row_counts` 也一样由调用方算好传进来（`batch_row_counts` 一次 GROUP BY），
    但**只有一次只讲一批的那两个端点传**（上传与 `/rows`）。批次列表**不传**——
    20 行逐行 GROUP BY 就是 20 次查询，而那一页根本不显示这四个数。

    批次上那几列计数（`created_rows` 等）**照发**，它们回答的是「提交之后写进去了几条」，
    与 `row_counts` 回答的「此刻这一批的行是什么状态」是两个时态。预览态下前者是
    0（这一趟一条都没写），后者是实时的——界面上分开写，不要拿一个去顶另一个。
    """
    if actor_names is not None:
        actor = actor_names.get(batch.imported_by)
    else:
        actor = _actors(db, [batch.imported_by]).get(batch.imported_by)
    return {
        "id": batch.id,
        "batch_no": batch.batch_no,
        # 两个名字都发，**界面要分开显示**：批次名称是这一批叫什么（它会成为任务名），
        # 文件名是这份数据从哪个文件来。只发一个的话，操作员看到 `结果(3).csv`
        # 会以为自己在界面上填的那一格没保存住。
        "batch_name": batch.batch_name,
        "file_name": batch.file_name,
        "source_system": batch.source_system,
        # 这一批是**哪一档**（§18.9）：`EXTERNAL_FULL_ANSWER`（100 题答案）或
        # `EXTERNAL_SUMMARY`（平台算好的总分 + 维度分）。界面要按它说一句不同的话
        # ——汇总那一档提交之后按 `assessment_result` 说话的页面（关注等级、关注率）
        # 上**这一场是空的**，因为平台给的分还没被核验（缺口 10）。不说这一句的话，
        # 操作员会以为「导入成功」而那一批学生在列表里一个都查不到。
        "import_mode": batch.import_mode,
        "tested_on": batch.tested_at.date().isoformat() if batch.tested_at else None,
        "status": batch.status,
        "resolution": batch.resolution,
        "task_id": batch.task_id,
        "total_rows": batch.total_rows,
        "created_rows": batch.created_rows,
        "updated_rows": batch.updated_rows,
        "skipped_rows": batch.skipped_rows,
        "error_rows": batch.error_rows,
        "row_counts": row_counts,
        "imported_by": batch.imported_by,
        # 姓名与账号**分两个字段发**，界面自己拼「姓名 · 账号」——与审计页第一列
        # 逐字同一形状（`api/v1/audit.py` 的 `actor_name` / `actor_account`）。
        # 服务端拼成一串的话，界面就没有办法在只有其中一个时优雅降级（审计页那两个
        # 分支：有名字只显示名字，只有账号显示账号，都没有是 `—`），而「操作人」
        # 这一格恰恰经常是 `—`（账号被停用、或者种子数据没留名字）。
        "imported_by_name": actor.display_name if actor else None,
        "imported_by_account": actor.account if actor else None,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


def import_rows_payload(db: Session, rows: list[AssessmentImportRow]) -> list[dict]:
    """一批行的对外形状，**学生与用时各一次取好**（一批 200 行 → 2 次 SELECT，不是 400 次）。

    **两个路由共用**（所以是公开名字，不是 `_` 开头）：导入明细这一侧（上传与 `/rows`）
    与任务详情页那一侧（`GET /assessment-tasks/{id}/unmatched-import-rows`，
    `api/v1/tasks.py`）。同一行在两个屏幕上长得不一样是这一层最容易出的事——
    而它们说的是同一条记录，`match_status` / `message` / `out_of_scope_reason` 那几列
    在两边必须逐字相同（§3 那条「同一处只许有一个定义」）。
    """
    student_ids = {row.student_id for row in rows if row.student_id}
    students: dict[int, Student] = {}
    if student_ids:
        students = {
            student.id: student
            for student in db.scalars(select(Student).where(Student.id.in_(student_ids)))
        }
    batch_ids = {row.batch_id for row in rows}
    batches: dict[int, AssessmentImportBatch] = {}
    if batch_ids:
        batches = {
            batch.id: batch
            for batch in db.scalars(
                select(AssessmentImportBatch).where(AssessmentImportBatch.id.in_(batch_ids))
            )
        }
    durations = row_durations(db, rows)
    return [_row_payload(row, students, durations, batches) for row in rows]


def _row_payload(
    row: AssessmentImportRow,
    students: dict[int, Student],
    durations: dict[int, int | None],
    batches: dict[int, AssessmentImportBatch],
) -> dict:
    """一行的对外形状。

    **原始值与归一化值都发**（§18.4：「原始字段和标准化字段都要保留，便于复核和重放」）：
    `raw_grade_name` 是文件里印的字（「七年一班」），`normalized_grade_name` 是拿去找
    人的那个值（`701`）。只发一套的话，「这一行为什么匹配错了」就答不上来——而那是
    这条链路上唯一的故障形态。

    `matched_name` 用的是 `masked_name`（展示名，§1）：它是花名册与关怀队列的展示名，
    与 `student.name` 一样实。**这里不是遮蔽**——`matched_student_no` 就在旁边那一行，
    真要指出是谁，学号比姓名的遮蔽有效得多。用展示名只是与全站其余的逐行列表一致。

    `student` 从调用方传进来的那份字典里取（`_rows_payload` 一次取好），**不再逐行
    `db.get`**：一批 200 行就是 200 次 SELECT，而它们查的是同一批学生。

    **批次号也要发**（2026-09-19 补）：这一行属于哪一批，在逐行明细那一屏是废话
    （那一屏本来就只看一批），而 `GET /assessment-tasks/{id}/unmatched-import-rows`
    那一屏是**跨批的**——一场任务下可以有好几批（先初一、隔几天再初二），而 `row_no`
    是**批内**的编号。少了它，操作员看到的是 2、3、2 这样的行号，读起来像「有一行丢了」
    或者「重了」，而那一屏的全部意义正是让他照着行号回文件里找人。

    `duration_seconds` 取不到就是 `null`（§3 数值列约定：没有记录就留空，不写 `0`
    ——`0` 秒是一次真实存在的用时，它会把「文件里没写用时」说成「用了 0 秒」）。
    """
    student = students.get(row.student_id) if row.student_id else None
    batch = batches.get(row.batch_id)
    return {
        "id": row.id,
        "row_no": row.row_no,
        # 这一行是哪一批传上来的（见 docstring）。`batch_no` 认不出时留 `None` 而不是
        # 编一个 `—`：`—` 是界面占位符（§8 那条「导出时留空」），服务端发它会让
        # 「不知道哪一批」与「批次号就叫这个」在数据里长得一样。
        "batch_id": row.batch_id,
        "batch_no": batch.batch_no if batch else None,
        "raw_name": row.raw_name,
        "raw_grade_name": row.raw_grade_name,
        "raw_class_name": row.raw_class_name,
        "raw_age": row.raw_age,
        "normalized_name": row.normalized_name,
        "normalized_grade_name": row.normalized_grade_name,
        "normalized_class_name": row.normalized_class_name,
        "match_status": row.match_status,
        "match_confidence": float(row.match_confidence) if row.match_confidence is not None else None,
        "candidate_count": len(row.candidate_student_ids or ()),
        "matched_student_no": student.student_no if student else None,
        "matched_name": student.masked_name if student else None,
        "conflict_code": row.conflict_code,
        # **`OUT_OF_SCOPE` 内部再分两种**（§18.7）：`SUPPLEMENT_CANDIDATE`（在这场任务的
        # 发放范围内、可以补发给他）与 `NOT_IN_TASK_SCOPE`（别的年级/班级/学校，补不了）。
        # 它在别的档上恒为 `null`，而界面要**按它分岔**：一个「补发」按钮只该长在前者上。
        # 不给它一个兜底值：`?? 'NOT_IN_TASK_SCOPE'` 会把「不知道」变成一句「补不了」，
        # 与 §22 那条 `scope_type` 不许 `?? 'SCHOOL'` 是同一条理由。
        "out_of_scope_reason": row.out_of_scope_reason,
        "processing_status": row.processing_status,
        "resolution": row.resolution,
        # §18.8 那四档处置在这一行上的取值（只有 `match_status == CONFLICT` 的行会问它）。
        # 它与 `resolution` 并列发出去，因为它们是**两个问题**：`resolution` 说这一行
        # 写不写进去，这一列说写进去时以哪一份为准、另一份留不留。界面按 `conflict_code`
        # 决定要不要显示那个选择面板，按这一列决定面板上当前选的是哪一档。
        "conflict_resolution": row.conflict_resolution,
        "age_before": row.age_before,
        "age_after": row.age_after,
        "age_resolution": row.age_resolution,
        # 「这一行是人逐行决定的，还是跟着整批走的」。两者在 `resolution` 那一列上
        # 长得一样（都是 `overwrite`），而一份 200 行的普查事后要能回答
        # 「那 3 条年龄不符当时是逐条看过、还是整批点的覆盖」。
        "resolved_by": row.resolved_by,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "duration_seconds": durations.get(row.id),
        "session_id": row.session_id,
        # **一句话，不是一个列表**。行表上没有 `errors` / `warnings` 两列：V1.2 的行表
        # 存的是「这一行是什么」，而不是「哪些格子坏了」——所以 `_row_message` 把
        # 解析的错、匹配的结论与告警拼成一句人话，界面上那一列直接渲染它。
        # 拆成三条的写法（V1.0 的 `errors[]`）在屏幕上会变成三个都只有半句的碎片，
        # 而它们本来是一起读才成立的。
        "message": row.message,
    }


def _actors(db: Session, account_ids: list[int | None]) -> dict[int, UserAccount]:
    """操作人的**账号行**，一次查一批（20 行批次逐行去查就是 20 次 SELECT，§8）。

    返回整行而不是拼好的字符串：姓名与账号是两个字段，界面按审计页的写法自己拼
    （见 `_batch_payload`）。认不出的 id（账号行被删是 §4 明确禁止的，所以这不该发生）
    与 `None` 一样在字典里没有条目——调用方 `.get()` 拿到 `None`，界面显示 `—`
    而不是空串：`—` 是界面占位符，而空串在表格里读起来像「这一列没有值」。
    """
    ids = {account_id for account_id in account_ids if account_id is not None}
    if not ids:
        return {}
    return {
        account.id: account
        for account in db.scalars(select(UserAccount).where(UserAccount.id.in_(ids)))
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_optional_int(value: str, label: str) -> int | None:
    if not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", f"{label} 应为数字", 422) from exc


def parse_tested_on(value: str) -> date:
    """测评日期：表单字段，不是文件里的一格，所以格式错直接拒掉整次请求。

    不用 `Form(date)` 让 FastAPI 自己解析，是因为那会返回框架自带的 422 结构，
    绕过统一的响应封装——前端拿不到 `error.message`，只能显示一句笼统的失败。
    """
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "测评日期格式应为 2026-09-16", 422) from exc
    return parsed
