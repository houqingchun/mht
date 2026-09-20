"""组织学生 → 学生信息导入的路由。

权限：`ORG_ACCOUNT: {MANAGE}` —— 与学生信息导入在 V1.0 里的口径逐字一致，没有放松。
这条链路写的是**组织与账号治理**：建学生、建账号、改名册上的班级，风险性质与
测绘记录导入（`assessment_import.py`，管理员 + 心理老师）不同，所以它归管理员。

四个端点。前两个是这一批的动作（预览 / 确认导入），后两个是这一批的**可查性**
（批次历史 / 逐行明细）——V1.0 里的名册导入是一次无痕的动作，导完之后只有一条审计，
而「这一行是谁导进来的、用的是哪份文件、为什么没更新」在界面上没有任何落点。
批次与逐行两张表就是这个落点，不给它们读者等于没落库。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.security.permissions import MANAGE, ORG_ACCOUNT, require_capability
from app.schemas.student_import import StudentImportCommitRequest
from app.services.audit_service import write_audit
from app.services.student_import_service import (
    commit_roster_import,
    list_roster_import_batches,
    list_roster_import_rows,
    start_roster_import,
    student_resolution_label,
)

router = APIRouter(tags=["student-roster"])

# 组织与账号：只有「管理」能改写名册。这个别名此前住在 `students.py`（那三个
# `/students/import/*` 端点用的），随那一批端点一起搬到这里——留在原处会变成一个
# 没有任何使用者的死别名。
OrgAccountManager = Annotated[
    UserAccount, Depends(require_capability(ORG_ACCOUNT, allow={MANAGE}))
]


@router.post("/student-roster/import/preview")
async def roster_import_preview(
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
):
    """上传一份名册文件：逐行校验，**并把这一批落进库**。

    这是 V1.2 之前没有的一步：从前预览的结果只活在返回值里，操作员上传完被叫走、
    回到这一页时它是空的，只能重传一次——而重传的那一份与刚才屏幕上那份是不是
    同一份，没有任何东西能回答。现在批次行（`PREVIEW`）与逐行明细都在库里，
    回来接着提交的就是同一批。

    **不写审计**：批次行自己就是这条记录，而且它比一行审计更全（文件指纹、操作者、
    五个计数、逐行结论，全在同一页上可查）。为一次「上传了但还没拍板」的动作再写
    一行 `导入学生`，会让审计页把这件事说成一件已经发生的事——而名册一个字都没动。
    审计写在那一次真正改了名册的提交上。
    """
    content = await file.read()
    result = start_roster_import(
        db,
        actor=current_user,
        filename=file.filename or "students.csv",
        content=content,
    )
    # 这个 `db.commit()` 不是可有可无的，它是 Phase 3 的全部前提：**预览从 V1.2 起
    # 是一次写入**（批次行 + 逐行明细），而 `get_db` 只管开与关、从不提交
    # （`db/session.py`：`finally: db.close()`）。少这一句的后果不是「预览看不到」，
    # 而是整条链路断在第一跳：客户端拿到 `batch_id` 去提交，`commit_roster_import`
    # 按 id 去库里查——那里什么都没有，于是回一句 404「导入批次不存在」，
    # 而屏幕上刚刚还写着「共 1 条，可导入 1 条」。
    #
    # **后端测试看不见这一句**：`conftest.py` 把 `get_db` 覆盖成那个共享的
    # `db_session`，而它在请求结束后不关闭也不回滚（外层事务到用例收尾才回滚），
    # 所以「写了但没提交」的行对测试照样查得到。唯一的网眼是 e2e（真服务、真连接），
    # 以及 `test_the_preview_survives_the_request_that_made_it`（那个用例刻意换掉
    # 这一层覆盖，用真的会提交的会话起一个 app）。
    db.commit()
    return ok(result)


@router.post("/student-roster/import/commit")
def roster_import_commit(
    payload: StudentImportCommitRequest,
    request: Request,
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    """把预览过的那一批写进名册。

    学号已在名册上的行需要 `resolution` 拍板（覆盖 / 放弃），与测评导入同一个形状。
    未选而文件里有冲突时 `commit_roster_import` 抛 422，**且任何写入之前**。
    """
    result = commit_roster_import(
        db, actor=current_user, batch_id=payload.batch_id, resolution=payload.resolution
    )
    # 处置方式必须进审计（§8 对导出遮蔽模式是同一条道理）：同样的 action、同样的
    # resource_type，覆盖了 3 个学生与放弃了 3 个学生长得一模一样，轨迹就答不上
    # 「这份文件有没有改过名册」。不带 `resolution` 时记的是「未涉及（无冲突）」，
    # 而不是留空——留空与「没问过」分不开。
    #
    # `batch_no` 也要记：名册导入是**反复发生**的动作（每学期一次普查、转学插班），
    # 而审计页的搜索匹配的就是 `action` 与这几个字段。不记批次号时，「去年那批
    # 初一的名册是谁导的」只能靠时间去猜，而同一分钟里可能有两批。
    write_audit(
        db,
        action="导入学生",
        resource_type="STUDENT",
        resource_id=None,
        actor=current_user,
        request=request,
        detail=(
            f"batch={result['batch_no']}, created={result['created']}, "
            f"updated={result['updated']}, skipped={result['skipped']}, "
            f"error={result['error_count']}, resolution={student_resolution_label(payload.resolution)}"
        ),
    )
    db.commit()
    return ok(result)


@router.get("/student-roster/import/batches")
def roster_import_batches(
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    """最近导入过哪几批。

    逐行明细**不在这个响应里**：一批名册是一所学校的人（几百行），把每一批的行
    全带上会让这一页为了显示 20 行批次而传输几万行。要明细走下面那个端点。
    """
    _ = current_user
    return ok(list_roster_import_batches(db))


@router.get("/student-roster/import/batches/{batch_id}/rows")
def roster_import_batch_rows(
    batch_id: int,
    current_user: OrgAccountManager,
    db: Annotated[Session, Depends(get_db)],
):
    """某一批的逐行明细：文件里那一行长什么样、落到了哪个学生身上、为什么没落上。

    行数有界（一份名册就是一个学校的人），所以排序分页在客户端（§10）。
    """
    _ = current_user
    return ok(list_roster_import_rows(db, batch_id=batch_id))
