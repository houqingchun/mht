import csv
import io
import re
from datetime import datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models.account import UserAccount, UserScope
from app.models.enums import RoleCode
from app.models.reporting import ProfessionalReport, ProfessionalReportVersion
from app.schemas.reporting import ReportCreateRequest, ReportDraftRequest
from app.services.analytics_service import analytics_report
from app.services.export_document import ExportDocument
from app.services.export_labels import (
    SMALL_COHORT_LABEL,
    dimension_label,
    report_status_label,
    score_distribution_label,
)


def _number(db: Session) -> str:
    today = datetime.now().strftime("%Y%m%d")
    count = db.scalar(select(func.count(ProfessionalReport.id)).where(ProfessionalReport.report_no.like(f"RPT-{today}-%"))) or 0
    return f"RPT-{today}-{count + 1:04d}"


def _school_id(db: Session, user: UserAccount) -> int:
    school_ids = set(db.scalars(select(UserScope.school_id).where(UserScope.user_id == user.id, UserScope.school_id.is_not(None))).all())
    if len(school_ids) != 1:
        raise AppError("DATA_SCOPE_INVALID", "当前账号未配置唯一学校范围", 403)
    return next(iter(school_ids))


def _version(db: Session, report: ProfessionalReport, version_no: int | None = None) -> ProfessionalReportVersion:
    no = version_no or report.current_version
    row = db.scalar(select(ProfessionalReportVersion).where(ProfessionalReportVersion.report_id == report.id, ProfessionalReportVersion.version_no == no))
    if row is None:
        raise AppError("NOT_FOUND", "报告版本不存在", 404)
    return row


def _visible(db: Session, user: UserAccount, report_id: int) -> ProfessionalReport:
    report = db.get(ProfessionalReport, report_id)
    if report is None or report.school_id != _school_id(db, user):
        raise AppError("NOT_FOUND", "专业报告不存在", 404)
    if user.role_code is RoleCode.LEADER and report.status != "PUBLISHED":
        raise AppError("NOT_FOUND", "专业报告不存在", 404)
    if user.role_code is RoleCode.COUNSELOR and report.created_by != user.id:
        raise AppError("NOT_FOUND", "专业报告不存在", 404)
    return report


def serialize(db: Session, report: ProfessionalReport, *, include_versions: bool = False) -> dict:
    data = {"id": report.id, "report_no": report.report_no, "title": report.title, "report_type": report.report_type, "status": report.status, "task_scope": report.task_scope_json, "analysis_mode": report.analysis_mode, "statistics_snapshot": report.statistics_snapshot_json, "current_version": report.current_version, "created_by": report.created_by, "created_at": report.created_at, "updated_at": report.updated_at, "published_at": report.published_at}
    versions = db.scalars(select(ProfessionalReportVersion).where(ProfessionalReportVersion.report_id == report.id).order_by(ProfessionalReportVersion.version_no.desc())).all()
    selected = next((v for v in versions if v.version_no == report.current_version), None)
    if selected:
        data["content"] = {"version_no": selected.version_no, "overall_summary": selected.overall_summary, "dimension_interpretation": selected.dimension_interpretation, "sample_validity_note": selected.sample_validity_note, "support_plan": selected.support_plan}
    if include_versions:
        data["versions"] = [{"version_no": v.version_no, "created_at": v.created_at, "created_by": v.created_by} for v in versions]
    return data


def create_report(db: Session, user: UserAccount, payload: ReportCreateRequest) -> ProfessionalReport:
    task_ids = list(dict.fromkeys(payload.task_ids))
    snapshot = jsonable_encoder(analytics_report(db, user, task_ids[0], payload.analysis_mode, task_ids))
    report = ProfessionalReport(report_no=_number(db), school_id=_school_id(db, user), report_type="PROFESSIONAL", title=payload.title.strip(), status="DRAFT", task_scope_json={"task_ids": task_ids}, analysis_mode=payload.analysis_mode, statistics_snapshot_json=snapshot, current_version=1, created_by=user.id, updated_by=user.id)
    db.add(report); db.flush()
    db.add(ProfessionalReportVersion(report_id=report.id, version_no=1, overall_summary=payload.overall_summary, dimension_interpretation=payload.dimension_interpretation, sample_validity_note=payload.sample_validity_note, support_plan=payload.support_plan, statistics_snapshot_json=snapshot, created_by=user.id))
    db.flush()
    return report


def list_reports(db: Session, user: UserAccount) -> list[dict]:
    statement = select(ProfessionalReport).where(ProfessionalReport.school_id == _school_id(db, user))
    statement = statement.where(ProfessionalReport.status == "PUBLISHED") if user.role_code is RoleCode.LEADER else statement.where(ProfessionalReport.created_by == user.id)
    return [serialize(db, x) for x in db.scalars(statement.order_by(ProfessionalReport.updated_at.desc())).all()]


def get_report(db: Session, user: UserAccount, report_id: int) -> dict:
    return serialize(db, _visible(db, user, report_id), include_versions=True)


def save_draft(db: Session, user: UserAccount, report_id: int, payload: ReportDraftRequest) -> ProfessionalReport:
    report = _visible(db, user, report_id)
    if report.status != "DRAFT":
        raise AppError("REPORT_IMMUTABLE", "已发布版本不可覆盖，请先创建新版本", 409)
    version = _version(db, report)
    for key, value in payload.model_dump().items(): setattr(version, key, value)
    report.updated_by = user.id
    db.flush(); return report


def new_version(db: Session, user: UserAccount, report_id: int) -> ProfessionalReport:
    """以上一版为起点开一个新版本：**四段文本照抄，统计数字重取**（2026-09-25 用户裁决）。

    ## 为什么重取而不是照抄

    规范 §7.3 只写了「报告**创建时**必须冻结」，没说新版本要不要重算，所以这曾是一条
    待裁决项。裁决是**重算**——「创建一个新版本」在用户心里是「数据变了，重出一版」，
    而照抄的版本**只换得了四段文本**：一份三个月前的报告点「新建版本」，新版本上写的
    还是三个月前的数，而屏幕上没有任何东西提示这件事。

    ## 它与「禁止实时重算覆盖历史数字」不冲突

    规范禁的是**读**的时候重算（`_snapshot_of` 那句 docstring 守着它，`report_document`
    仍然是纯读的，一个字都没动）。这里动的是**写**：新版**自己**那一行拿一份新快照，
    旧版本行**原样不动**——`old` 那一行从头到尾没有被碰过。所以「历史版本的数字永远
    是它发布时那一份」这条仍然成立，改的只是「新版拿到的起点是此刻，不是上一版那一刻」。

    ## 两处快照一起刷新

    版本级那一份是导出的依据（`_snapshot_of` 按版本读），而**报告级那一份是列表与详情
    页显示的依据**（`serialize` 的 `statistics_snapshot`）。只刷新前者的话，详情页
    会拿旧数字配新文本——同一屏上两个数各说各话（CLAUDE.md §11 那条「指标卡上的数
    必须与它点进去的那个列表同源」）。所以两个位置写**同一个对象**，与 `create_report`
    的形状一致。

    取数失败时**整段不落**（异常会让这次请求的事务回滚），不会留下一个「文本是新版、
    数字没更新」的半成品版本。
    """
    report = _visible(db, user, report_id)
    if report.status != "PUBLISHED":
        raise AppError("VALIDATION_ERROR", "仅已发布报告可创建新版本", 422)
    old = _version(db, report)
    task_ids = list((report.task_scope_json or {}).get("task_ids") or [])
    snapshot = jsonable_encoder(analytics_report(db, user, task_ids[0] if task_ids else None, report.analysis_mode, task_ids or None))
    report.current_version += 1; report.status = "DRAFT"; report.updated_by = user.id
    report.statistics_snapshot_json = snapshot
    db.add(ProfessionalReportVersion(report_id=report.id, version_no=report.current_version, overall_summary=old.overall_summary, dimension_interpretation=old.dimension_interpretation, sample_validity_note=old.sample_validity_note, support_plan=old.support_plan, statistics_snapshot_json=snapshot, created_by=user.id))
    db.flush(); return report


def publish(db: Session, user: UserAccount, report_id: int) -> ProfessionalReport:
    report = _visible(db, user, report_id)
    if report.status != "DRAFT": raise AppError("VALIDATION_ERROR", "报告当前不可发布", 422)
    report.status = "PUBLISHED"; report.published_by = user.id; report.published_at = datetime.now(); report.updated_by = user.id
    db.flush(); return report


def _snapshot_of(report: ProfessionalReport, version: ProfessionalReportVersion) -> dict:
    """这一**版本**冻结的那份统计快照（§7.2 / §7.3 / §7.4）。

    规范那句「历史报告展示必须读取快照，**禁止重新打开时实时重算并覆盖历史数字**」
    在这里的可执行形式就是：导出文件里的每一个数都从这个字典里取，**一处都不重算**
    ——`report_document` 因此是**纯读**的，它不调 `analytics_report`。

    按「版本优先、报告兜底」读：正常路径上每一版都有自己的那一份；报告级那一份是
    **当前版本**的镜像（`serialize` 显示它、`new_version` 与新版本一起刷新它），也是
    版本级缺失时的兜底。**两份都没有时给空字典**，那条路（手工改库才会出现）让下面的行
    各自留空，而不是替它编一份统计。

    **★ 「版本优先」这一层此前读不出差别，2026-09-25 起读得出——两件事同一处**。

    裁决之前 `new_version` 复制的是 `report.statistics_snapshot_json` 本身，所以三个位置
    （版本 N 行、新版行、报告行）在任何一条写入路径上都是**同一个对象**：把这一行改成
    `report.statistics_snapshot_json or {}`（永远读报告级那一份），`test_reporting_api.py`
    17 条全绿——**那不是守卫漏了，是数据上不可分辨**，所以当时没有为它补断言
    （造出来的是恒绿的守卫）。

    用户裁决「新版本**重新统计快照**」之后这个状态**可构造了**：新版那一行拿的是此刻的
    数，而版本 1 那一行仍然是它发布时那一份，两份**真的不同**。所以现在有一条用例
    按这个差别断言（`test_a_new_version_recomputes_the_statistics_it_inherits`）——
    它同时钉住两件事：新版的数是新的，**旧版那一行一个字没动**。
    """
    return version.statistics_snapshot_json or report.statistics_snapshot_json or {}


def _as_of_text(value) -> str:
    """快照里的 `as_of` 是**带时区**的 ISO 串（`datetime.now(UTC).isoformat()`）。

    **不换算时区**：`+00:00` 正是那一刻被记下来的样子，而这份文件的读者要拿它与库里
    的时间戳对齐——把它改成东八区会让文件里的时间与快照对不上。只做两件降噪：
    去掉微秒（没有一位读者需要它）与把 `T` 换成空格。
    """
    if not value:
        return ""
    return re.sub(r"\.\d+", "", str(value).replace("T", " "))


def _rate_text(rate) -> str:
    """§11：`None` 不是 `0`，两者在文件里必须长得不一样。

    分母小于 `MIN_COHORT_FOR_AGGREGATE` 时后端**不下发比率**（`_report_rate` 返回
    `None`），那一格写「样本过小」。留白读起来像「这份文件漏了一格」，而 `0%` 是一句
    完全不同的话（「一个都没有」）——两者都错。
    """
    if rate is None:
        return SMALL_COHORT_LABEL
    return f"{rate}%"


def _task_scope_text(snapshot: dict) -> str:
    """任务范围：多任务合并时把名字并列出来（`analytics_report` 的 `tasks[]`）。

    单任务时 `tasks` 恰好一行，与 `task.name` 同值；`tasks` 为空（历史行）才回落到
    `task.name`。两者都没有就留空——**不猜一个「全校」**（CLAUDE.md §9 那条
    「取不到就整句不出现」）。
    """
    names = [item.get("name") for item in (snapshot.get("tasks") or []) if isinstance(item, dict) and item.get("name")]
    if names:
        return "、".join(names)
    task = snapshot.get("task") or {}
    return task.get("name") or ""


def _sample_text(quality: dict) -> str:
    """§7.3 的「样本数量」：四个数各有各的口径，所以逐项写出来，不合成一个。

    `target_count`（发放了多少人）/ `eligible_count`（其中符合条件）/ `completed_count`
    （其中已完成）/ `n_evaluable`（真正进入统计的）——**它们不是同一件事的四个说法**，
    而读者要问的恰恰是「为什么纳统人数比目标人数少」。
    """
    return (
        f"任务目标 {quality.get('target_count') or 0} 人"
        f" · 符合条件 {quality.get('eligible_count') or 0} 人"
        f" · 已完成 {quality.get('completed_count') or 0} 人"
        f" · 纳入统计 {quality.get('n_evaluable') or 0} 人"
    )


def _metric_text(quality: dict) -> str:
    """§7.3 的「统计指标」：覆盖率与效度两档。"""
    return (
        f"覆盖率 {_rate_text(quality.get('coverage_rate'))}"
        f"；效度未标记 {quality.get('validity_unflagged_count') or 0} 人"
        f" · 效度提示 {quality.get('validity_flagged_count') or 0} 人"
    )


def _dimension_rows(dimensions: list) -> list[tuple[str, str]]:
    """§7.3 的「维度分布」：**一个维度一行**，不是挤在一格里。

    维度编码一律走 `dimension_label`（第十二张镜像表）：不翻译的话，一份给学校看的
    报告里会印出 `LEARNING_ANXIETY` 这类英文编码。

    均值被抑制时（样本 < `MIN_COHORT_FOR_AGGREGATE`）**仍然把分布写出来**——后端就是
    这么返回的（`_dimension_report` 的注释：「即使 cohort 太小，读者仍该看到这一维度的
    分数大致落在哪里」）。所以这里只抑制均值，不抑制分布。
    """
    if not dimensions:
        return [("维度分布", "（本次统计没有可用的维度数据）")]
    rows: list[tuple[str, str]] = []
    for item in dimensions:
        name = dimension_label(item.get("dimension_code"))
        size = item.get("n_evaluable") or 0
        if not size:
            rows.append((f"维度分布 · {name}", "无可用数据"))
            continue
        mean = item.get("mean_score")
        parts = [f"纳入统计 {size} 人", f"均值 {mean}" if mean is not None else "样本过小，不给均值"]
        distribution = item.get("distribution") or []
        if distribution:
            parts.append("分布 " + " · ".join(f"{score_distribution_label(x.get('range_code'))} {x.get('count')} 人" for x in distribution))
        rows.append((f"维度分布 · {name}", "；".join(parts)))
    return rows


def _document_rows(report: ProfessionalReport, version: ProfessionalReportVersion, snapshot: dict) -> list[tuple[str, str]]:
    """导出文件的**全部**内容，一个 (项目, 内容) 一行。

    **纯函数**（不接 `db`）是有意的：它是「这份文件里写了什么」的唯一定义，而
    `tests/test_reporting_api.py` 直接拿它当靶子比整份文件便宜得多，也不必为了断言
    一行文字去建一份报告。

    次序照规范 §7.3：四个身份元数据在前，冻结快照的**七项**随后（任务范围 / 样本数量 /
    统计指标 / 维度分布 / 关注人数 / 关注比例 / 生成时间），四段人写的文本在最后。
    **没有「报告类型」这一行**：`report_type` 在 `labels.ts` 里没有对应表、界面上也没有
    任何渲染点，写出去只会印出 `PROFESSIONAL`——那正是这一整节要修的那种漏码。

    缺值的表现分两套（CLAUDE.md §3）：**数值列留空**，而**比率被抑制时写「样本过小」**
    ——后者不是缺值，是一句关于样本量的话。四段文本用 `or ""`：没写就是空单元格。
    """
    quality = snapshot.get("sample_quality") or {}
    overview = snapshot.get("overview") or {}
    return [
        ("报告编号", report.report_no),
        ("报告标题", report.title),
        ("版本", str(version.version_no)),
        ("状态", report_status_label(report.status)),
        ("任务范围", _task_scope_text(snapshot)),
        ("样本数量", _sample_text(quality)),
        ("统计指标", _metric_text(quality)),
        *_dimension_rows(snapshot.get("dimensions") or []),
        ("关注人数", f"{overview.get('signal_student_count') or 0} 人"),
        ("关注比例", _rate_text(overview.get("signal_rate"))),
        ("生成时间", _as_of_text(snapshot.get("as_of"))),
        ("整体情况说明", version.overall_summary or ""),
        ("重点维度解释", version.dimension_interpretation or ""),
        ("样本覆盖及效度说明", version.sample_validity_note or ""),
        ("后续教育支持计划", version.support_plan or ""),
    ]


def report_document(db: Session, user: UserAccount, report_id: int, version_no: int | None) -> tuple[ProfessionalReport, ExportDocument]:
    """把一份报告（或其某个历史版本）渲染成可下载的 CSV。

    `row_count` **写 `len(rows)` 而不是一个字面量**：这一列在界面上是「导出 N 行」，
    而它此前硬编码 `8`——真正的行数取决于这一批快照里有几个维度，写死的那个数在
    维度缺失或多出时会静默说错（`ExportDocument.row_count` 不数表头那一行，这里也没有
    表头：这份文件是「项目 / 内容」两列的键值对）。
    """
    report = _visible(db, user, report_id); version = _version(db, report, version_no)
    rows = _document_rows(report, version, _snapshot_of(report, version))
    output = io.StringIO(); writer = csv.writer(output)
    for item, content in rows:
        writer.writerow([item, content])
    return report, ExportDocument(csv_text="\ufeff" + output.getvalue(), columns=("项目", "内容"), row_count=len(rows))
