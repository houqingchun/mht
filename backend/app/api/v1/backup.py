"""数据备份：看状态、手动跑一次、把文件取走（CLAUDE.md §34）。

三个端点都在 `ORG_ACCOUNT: {MANAGE}` 之下——与「账号与权限」那一页同一道门，也就是
**唯一拿得到它的角色就是系统管理员本身**。

**下载那一枚按钮是 §4 的一次有意例外，别顺手把它关掉。** 管理员按 §4 是
`STUDENT_PSYCH_DETAIL: NONE`（他看不到任何一名学生的档案），却能取走一份含全部学生心理
数据、答卷与账号口令哈希的整库导出——这与 §4 那条「受控导出不能成为绕过心理详情的旁路」
直接冲突。用户裁决要这个按钮（「备份文件提供下载按钮」），所以处置是**把例外写下来 +
给它加护栏**，不是假装它不存在：门收在管理员这一个角色上、每次下载写一行审计、
那一行的 `detail` 写明「整库原始数据，不可能遮蔽」（§8：导出审计必须记录遮蔽模式——
一份 dump **没有**遮蔽这一档，这句话必须出现在轨迹里，而不是靠读者自己去想）。
理由与边界记在 §34；关掉它就没有用户要的那个功能，把它推广出去则会把 §4 那条约开口子。

配置的**写**不在这个模块里：它走既有的通用端点 `PUT /admin/settings/{namespace}`
（`api/v1/settings.py`），因为 `settings_service` 只认 `DEFAULTS` 里声明过的键，加一个
`backup` 分组就够了——再开一个写端点就是同一件事的第二个定义。

**这一层没有一处与操作系统有关**，这是有意的：目录解析、文件名、时间戳、「今天」的判据
全在 `backup_service` 里，而它按平台枚举 mysqldump 的候选位置（Windows 的
`Program Files\\MySQL\\*`、Linux / macOS 的常见安装位），不读注册表、不问「用法」
（`single` / `lan`），也不判断 `os.name`。所以同一份代码在 Windows 与 Linux 上都成立：
目标机是 Windows、开发机是 macOS，两条路一直都被真的走过。
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.errors import ok
from app.db.session import get_db
from app.models.account import UserAccount
from app.models.backup import STATUS_FAILED, STATUS_SUCCEEDED, TRIGGER_MANUAL
from app.security.permissions import MANAGE, ORG_ACCOUNT, require_capability
from app.services import backup_service

router = APIRouter(prefix="/admin", tags=["admin-backup"])

BackupManager = Annotated[
    UserAccount, Depends(require_capability(ORG_ACCOUNT, allow={MANAGE}))
]


def _one_record(db: Session, status: str, directory: Path) -> dict | None:
    """「上次成功」「上次失败」各一行。没有就回 `None`，由界面分岔。

    复用 `record_payloads` 而不是另拼一份：同一行记录在这里、在历史表里、在手动备份的
    返回里必须是同一个形状——三处各写一份序列化就是三个定义，而它们漂移了不会有任何
    东西看得见。
    """
    record = backup_service.last_record(db, status=status)
    if record is None:
        return None
    return backup_service.record_payloads(db, [record], directory=directory)[0]


@router.get("/backup")
def read_backup(
    db: Annotated[Session, Depends(get_db)],
    _manager: BackupManager,
    limit: int = Query(default=backup_service.DEFAULT_RECORD_LIMIT, ge=1, le=200),
) -> dict:
    """状态、落点、上次成功 / 上次失败、历史。

    **`dir` 发的是服务端展开之后的实际路径**（`dir` 配置为空时就是
    `<安装目录>/backups`）：一个写着「留空即默认」的输入框答不出「那我这台机器上到底
    落在哪」（§9「口径要写进界面」）。`dir_is_default` 让界面能把这句话说清楚。
    """
    summary = backup_service.directory_summary(db)
    directory = Path(summary["dir"])
    return ok(
        {
            **summary,
            "last_success": _one_record(db, STATUS_SUCCEEDED, directory),
            "last_failure": _one_record(db, STATUS_FAILED, directory),
            "records": backup_service.record_payloads(
                db, backup_service.list_records(db, limit=limit), directory=directory
            ),
        }
    )


@router.post("/backup/run")
def run_backup_now(
    request: Request,
    user: BackupManager,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """立刻备份一次，**同步**返回结果。

    同步是刻意的：这所学校的库很小，而同步才能让失败原因直接出现在这一次的响应里——
    一个「已开始，请稍后刷新」的实现会把失败推到那张表上，用户当时看不到，而那正是
    这次要消掉的东西（原 `备份数据.bat` 的一闪就关）。

    **服务层不抛「备份失败」**：mysqldump 报错时它照常回一行 `status=FAILED` 的记录，
    所以这里 200 + 一行结果就是完整的答复。唯一会抛的是 `BACKUP_RUNNING`（409，自动
    那一路或另一个人正在跑）——那是**没跑**，不是失败。

    这条路由**不写审计**：`backup_service` 写了。全库只有这一处如此，理由是自动那一次
    **没有路由**，而「同一次动作的轨迹有两个写入方」比「一处偏离」更糟。见 §34。
    """
    record = backup_service.run_backup(db, trigger=TRIGGER_MANUAL, operator=user)
    db.commit()
    return ok(
        backup_service.record_payloads(
            db, [record], directory=backup_service.primary_directory(db)
        )[0]
    )


@router.get("/backup/records/{record_id}/download")
def download_backup_file(
    record_id: int,
    request: Request,
    user: BackupManager,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """取走那一份 `.sql`。文件不在、路径不对、内容与记录对不上，各有各的答复。

    四道判据都在 `open_record_for_download` 里（存在性、目录穿越、文件还在不在、
    sha256 是否一致），**它同时写审计**——照 §8「先写审计，后返回数据」。所以这里
    只负责提交与把字节发出去。

    `application/octet-stream` + `attachment`：这是一份整库原始数据，不该给浏览器任何
    机会把它渲染出来。文件名一律是我们自己生成的 ASCII 形状
    （`xinliceping-YYYYMMDD-HHMMSS.sql`），所以这里不需要 RFC 5987 那套 `filename*=`
    的编码——但**如果哪一天文件名要带中文，这里必须一起改**。
    """
    record, _path, data = backup_service.open_record_for_download(
        db, record_id, operator=user, request=request
    )
    file_name = record.file_name
    db.commit()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
