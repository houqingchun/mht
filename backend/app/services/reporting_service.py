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
from app.services.numbering import insert_with_unique_number, run_until_not_deadlocked


def _number_stem(db: Session) -> tuple[str, int]:
    """今天这个号段的前缀与基数 —— 单号是 `RPT-<前缀>-<基数 + offset:04d>`。

    基数是一个**起点，不是一个保证**：同一秒里两个请求会各自算到同一个基数，
    而唯一键才是判据（详见 `services/numbering.py` 的 docstring）。所以这里只负责
    「从哪儿数起」，撞号之后的出路归 `insert_with_unique_number`。

    号段按天分（`report_no` 的形状里带着日期），所以计数也按前缀过滤——
    这既让每天从头开始编号，也让重试只跟「今天这几行」打交道。
    """
    today = datetime.now().strftime("%Y%m%d")
    count = db.scalar(
        select(func.count(ProfessionalReport.id)).where(
            ProfessionalReport.report_no.like(f"RPT-{today}-%")
        )
    ) or 0
    return today, count + 1


def _school_id(db: Session, user: UserAccount) -> int:
    school_ids = set(db.scalars(select(UserScope.school_id).where(UserScope.user_id == user.id, UserScope.school_id.is_not(None))).all())
    if len(school_ids) != 1:
        raise AppError("DATA_SCOPE_INVALID", "当前账号未配置唯一学校范围", 403)
    return next(iter(school_ids))


def _latest_published(db: Session, report: ProfessionalReport) -> ProfessionalReportVersion | None:
    """这一份报告**最新发布出去的那一版**（没有就返回 `None`）。

    它是德育领导那一侧的可见性判据（2026-09-25 修，§5.13 缺口 1 之二）。此前那个判据
    是**报告头**的 `status`，而报告头回答的是「**当前版本**发布了吗」——于是
    `new_version()` 把报告头改回 `DRAFT` 之后，已经发布出去的 V1 对领导端**消失**了：
    一所学校正在改第二版，领导端就读不到第一版，屏幕上只表现为列表里少了一行。

    判据换成「这份报告**有没有**已发布的版本」之后，V2 草稿期间领导读到的仍然是 V1
    ——正是那条状态机（`未创建 → DRAFT V1 → PUBLISHED V1 →（新建版本）→ DRAFT V2`）
    里写的「V2 草稿期间 V1 仍可见 / 可导出」。
    """
    return db.scalar(
        select(ProfessionalReportVersion)
        .where(ProfessionalReportVersion.report_id == report.id, ProfessionalReportVersion.status == "PUBLISHED")
        .order_by(ProfessionalReportVersion.version_no.desc())
        .limit(1)
    )


def _version(db: Session, report: ProfessionalReport, version_no: int | None = None, *, viewer: UserAccount | None = None) -> ProfessionalReportVersion:
    """取这份报告的一个版本行。

    **读者不同，「不传 version_no」的含义就不同**（2026-09-25 修，§5.13 缺口 1 之一）：

    - 心理老师（不传 `viewer`，或作者本人读自己那一份）：不传 = **当前版本**——
      他手上正在编辑的那一版就是当前版本；
    - 德育领导：不传 = **最新已发布版本**。他的可见性按版本级 `PUBLISHED` 走
      （`_latest_published`），而「当前版本」在 V2 草稿期间恰好是**他不该看见**的那一行。

    领导显式点名一个仍在草稿的版本号时回 404，措辞与「这一版不存在」逐字相同：
    **「不属于你」与「不存在」在响应上必须不可分辨**（CLAUDE.md §24），否则版本号
    就成了一个可以逐个试出来的枚举。
    """
    if viewer is not None and viewer.role_code is RoleCode.LEADER:
        if version_no is None:
            row = _latest_published(db, report)
        else:
            row = db.scalar(
                select(ProfessionalReportVersion).where(
                    ProfessionalReportVersion.report_id == report.id,
                    ProfessionalReportVersion.version_no == version_no,
                    ProfessionalReportVersion.status == "PUBLISHED",
                )
            )
    else:
        no = version_no or report.current_version
        row = db.scalar(select(ProfessionalReportVersion).where(ProfessionalReportVersion.report_id == report.id, ProfessionalReportVersion.version_no == no))
    if row is None:
        raise AppError("NOT_FOUND", "报告版本不存在", 404)
    return row


def _actor_names(db: Session, ids) -> dict[int, str]:
    """一批账号 id → 显示名，**一次查完**，不是逐行查。

    `created_by_name` / `published_by_name` 要回答的是「谁写的 / 谁发布的」——与审计页
    「操作人」那一列是同一条（`api/v1/audit.py` 的批查，CLAUDE.md §8）。**取不到就不编**
    （`dict.get` 回 `None`，界面显示 `—`）：`published_by` 在那份 0023 的回填里允许是 NULL
    （历史行查不出发布人，编一个比留空更糟）。
    """
    wanted = {int(i) for i in ids if i is not None}
    if not wanted:
        return {}
    rows = db.execute(select(UserAccount.id, UserAccount.display_name).where(UserAccount.id.in_(wanted))).all()
    return {row[0]: row[1] for row in rows}


def _visible(db: Session, user: UserAccount, report_id: int) -> ProfessionalReport:
    report = db.get(ProfessionalReport, report_id)
    if report is None or report.school_id != _school_id(db, user):
        raise AppError("NOT_FOUND", "专业报告不存在", 404)
    if user.role_code is RoleCode.LEADER and _latest_published(db, report) is None:
        raise AppError("NOT_FOUND", "专业报告不存在", 404)
    if user.role_code is RoleCode.COUNSELOR and report.created_by != user.id:
        raise AppError("NOT_FOUND", "专业报告不存在", 404)
    return report


def _content_payload(version: ProfessionalReportVersion, names: dict[int, str]) -> dict:
    """一个版本的**正文与它自己的发布元数据**（详情、版本详情、`versions[]` 三处共用一份形状）。

    `status` / `published_at` / `published_by` 取的是**版本行自己**那三列，不是报告头
    ——V2 草稿期间领导读到的是 V1 那一行，它的状态必须是 `PUBLISHED`（报告头那时是
    `DRAFT`）。报告头那三个字段仍然照发（`serialize` 里），它们回答的是另一个问题：
    「这份报告的**当前版本**是什么状态、最近一次发布是什么时候」。
    """
    return {
        "version_no": version.version_no,
        "status": version.status,
        "created_by": version.created_by,
        "created_by_name": names.get(version.created_by),
        "created_at": version.created_at,
        "published_at": version.published_at,
        "published_by": version.published_by,
        "published_by_name": names.get(version.published_by) if version.published_by else None,
        "overall_summary": version.overall_summary,
        "dimension_interpretation": version.dimension_interpretation,
        "sample_validity_note": version.sample_validity_note,
        "support_plan": version.support_plan,
    }


def serialize(db: Session, report: ProfessionalReport, *, include_versions: bool = False, viewer: UserAccount | None = None) -> dict:
    """一份报告在列表 / 详情里的形状。

    **`viewer` 决定 `content` 是哪一版的、以及 `versions[]` 里能看到哪些**
    （2026-09-25 修，§5.13 缺口 1 之一 · 之二 · 之三）：

    - 心理老师（不传 `viewer`）→ `content` 是**当前版本**（他正在编辑的那一版），
      `versions[]` 是**全部版本**——版本时间线（`ProfessionalReportVersions.vue`）靠它渲染；
    - 德育领导 → `content` 是**最新已发布版本**（他读到的正文与数字都必须是发布出去那一份，
      而报告头那两样在 V2 草稿期间已经指向 V2 了），`versions[]` 里**只有已发布的那些**。

    最后那一条（`versions[]` 的过滤）是 `GET …/versions/{n}` 那道门的同一条口径：那边领导
    点名要一个草稿版本号回 404，而这边 `include_versions=True` 从前把**每一版**的
    `_content_payload` 都发了出去——同一份草稿正文于是有两条路，其中一条是敞开的，
    而两条路各说各话在这份响应里看不出来（CLAUDE.md §4：受控导出不能成为绕过心理详情的
    旁路，这是同一个形状）。
    """
    versions = db.scalars(select(ProfessionalReportVersion).where(ProfessionalReportVersion.report_id == report.id).order_by(ProfessionalReportVersion.version_no.desc())).all()
    latest_published = next((v for v in versions if v.status == "PUBLISHED"), None)
    leader = viewer is not None and viewer.role_code is RoleCode.LEADER
    if leader:
        selected = latest_published
    else:
        selected = next((v for v in versions if v.version_no == report.current_version), None)
    names = _actor_names(db, [report.created_by, report.updated_by, report.published_by, *(v.created_by for v in versions), *(v.published_by for v in versions)])
    data = {
        "id": report.id, "report_no": report.report_no, "title": report.title, "report_type": report.report_type,
        # 报告头这三项 = **当前版本**的镜像（`publish` / `new_version` 两处一起写），
        # 所以 V2 草稿期间它们是 DRAFT / V2；领导读到的那一份在上面 `content` 里。
        "status": report.status,
        "task_scope": report.task_scope_json, "analysis_mode": report.analysis_mode,
        "statistics_snapshot": _snapshot_of(report, selected) if selected else (report.statistics_snapshot_json or {}),
        "current_version": report.current_version,
        "current_version_status": next((v.status for v in versions if v.version_no == report.current_version), None),
        "created_by": report.created_by, "created_by_name": names.get(report.created_by),
        "created_at": report.created_at, "updated_at": report.updated_at,
        "published_at": report.published_at, "published_by": report.published_by,
        # 「最新已发布版本」的三个数单独发一份：界面上「已发布 · V1」那一格读的是它，
        # 而不是报告头那一列——报告头在 V2 草稿期间已经是 DRAFT 了（§5.13 A 的状态口径）。
        "latest_published_version": latest_published.version_no if latest_published else None,
        "latest_published_at": latest_published.published_at if latest_published else None,
        "latest_published_by": latest_published.published_by if latest_published else None,
        "latest_published_by_name": names.get(latest_published.published_by) if latest_published and latest_published.published_by else None,
        "content_version": selected.version_no if selected else None,
    }
    if selected:
        data["content"] = _content_payload(selected, names)
    if include_versions:
        # 领导只看已发布的那些（理由见 docstring 末段）：`versions[]` 里含的是**正文**，
        # 与 `GET …/versions/{n}` 那边拦住他的是同一份东西，两处必须是同一条口径。
        data["versions"] = [_content_payload(v, names) for v in versions if not leader or v.status == "PUBLISHED"]
    return data


def create_report(db: Session, user: UserAccount, payload: ReportCreateRequest) -> ProfessionalReport:
    """建一份报告（头 + V1 草稿），**整段可重跑**。

    `run_until_not_deadlocked` 那一层是为 1213 加的（CLAUDE.md §33）：并发下
    `insert_with_unique_number` 会在页尾撞出 InnoDB 死锁，而它回滚的是**整个事务**，
    所以只能在「整个操作」这一层重跑。这里满足那条契约——进门只有读（`analytics_report`
    与 `_number_stem`），第一个写入就是那次插入，而提交归路由。
    """
    return run_until_not_deadlocked(db, lambda: _create_report(db, user, payload))


def _create_report(db: Session, user: UserAccount, payload: ReportCreateRequest) -> ProfessionalReport:
    task_ids = list(dict.fromkeys(payload.task_ids))
    snapshot = jsonable_encoder(analytics_report(db, user, task_ids[0], payload.analysis_mode, task_ids))
    today, base = _number_stem(db)

    def build_report(report_no: str) -> ProfessionalReport:
        return ProfessionalReport(report_no=report_no, school_id=_school_id(db, user), report_type="PROFESSIONAL", title=payload.title.strip(), status="DRAFT", task_scope_json={"task_ids": task_ids}, analysis_mode=payload.analysis_mode, statistics_snapshot_json=snapshot, current_version=1, created_by=user.id, updated_by=user.id)

    report = insert_with_unique_number(
        db,
        number_at=lambda offset: f"RPT-{today}-{base + offset:04d}",
        build=build_report,
    )
    db.add(ProfessionalReportVersion(report_id=report.id, version_no=1, overall_summary=payload.overall_summary, dimension_interpretation=payload.dimension_interpretation, sample_validity_note=payload.sample_validity_note, support_plan=payload.support_plan, statistics_snapshot_json=snapshot, status="DRAFT", created_by=user.id))
    db.flush()
    return report


def list_reports(db: Session, user: UserAccount) -> list[dict]:
    """这一页给谁看什么，按角色分两支：

    - **心理老师**：自己创建的报告（`created_by`），草稿与已发布都在列——他要能从列表
      里回到自己那份草稿；
    - **德育领导**：本校**有过已发布版本**的报告。判据是版本级的 `EXISTS`
      （`_latest_published` 的集合形式），不是报告头那一列——理由见 `_latest_published`。
    """
    statement = select(ProfessionalReport).where(ProfessionalReport.school_id == _school_id(db, user))
    if user.role_code is RoleCode.LEADER:
        statement = statement.where(
            select(ProfessionalReportVersion.id)
            .where(ProfessionalReportVersion.report_id == ProfessionalReport.id, ProfessionalReportVersion.status == "PUBLISHED")
            .exists()
        )
    else:
        statement = statement.where(ProfessionalReport.created_by == user.id)
    return [serialize(db, x, viewer=user) for x in db.scalars(statement.order_by(ProfessionalReport.updated_at.desc())).all()]


def get_report(db: Session, user: UserAccount, report_id: int) -> dict:
    return serialize(db, _visible(db, user, report_id), include_versions=True, viewer=user)


def get_version(db: Session, user: UserAccount, report_id: int, version_no: int) -> dict:
    """某一个**版本**的正文与它冻结的那份统计快照（§5.13 缺口 1 之一的那个 API）。

    在此之前 `serialize` 只发当前版本的正文，`versions[]` 里只有版本号 / 创建时间 /
    创建人——**历史版本的正文不可读**。于是「打开某一版看看当时写了什么」在界面上没有
    出路，而「导出历史版本」导的却是另一份东西（那条路读得到版本行，这条读不到，
    两个读者一个看得到、一个看不到，正是 CLAUDE.md §3 那条「表在而没人从那儿取」的反面）。

    **一点统计都不重算**：正文与快照都从这一版自己那一行读（`_snapshot_of` 是纯读的），
    所以打开历史版本不会因为「此刻的数据变了」而让屏幕上的数跟着动——与
    `report_document` 同一条：那份文件是「那一版发布时写了什么」的副本。

    领导读一份仍在草稿的版本 → 404（`_version` 的 `viewer` 分支），措辞与「不存在」同一句。
    """
    report = _visible(db, user, report_id)
    version = _version(db, report, version_no, viewer=user)
    names = _actor_names(db, [version.created_by, version.published_by, report.created_by])
    return {
        "report_id": report.id,
        "report_no": report.report_no,
        "title": report.title,
        "task_scope": report.task_scope_json,
        "analysis_mode": report.analysis_mode,
        "statistics_snapshot": _snapshot_of(report, version),
        "content": _content_payload(version, names),
    }


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

    ## 报告头回到 DRAFT，而**上一版那一行一个字不动**

    这条是 2026-09-25 补的（§5.13 缺口 1 之二）。报告头的 `status` 在这里从
    `PUBLISHED` 改回 `DRAFT`，而**它回答的只是「当前版本是不是草稿」**——发布状态下沉到
    版本级之后，上一版那一行的 `status` / `published_at` / `published_by` 仍然是它发布
    时的样子，所以 V2 编辑期间德育领导读到的还是那一份（`_latest_published`）。
    此前领导可见性读的是报告头，于是这一步**把已经发布出去的 V1 从领导端下架了**——
    一个「新建版本」的动作产生了它不该有的对外效果。
    """
    report = _visible(db, user, report_id)
    if report.status != "PUBLISHED":
        raise AppError("VALIDATION_ERROR", "仅已发布报告可创建新版本", 422)
    old = _version(db, report)
    task_ids = list((report.task_scope_json or {}).get("task_ids") or [])
    snapshot = jsonable_encoder(analytics_report(db, user, task_ids[0] if task_ids else None, report.analysis_mode, task_ids or None))
    report.current_version += 1; report.status = "DRAFT"; report.updated_by = user.id
    report.statistics_snapshot_json = snapshot
    db.add(ProfessionalReportVersion(report_id=report.id, version_no=report.current_version, overall_summary=old.overall_summary, dimension_interpretation=old.dimension_interpretation, sample_validity_note=old.sample_validity_note, support_plan=old.support_plan, statistics_snapshot_json=snapshot, status="DRAFT", created_by=user.id))
    db.flush(); return report


def publish(db: Session, user: UserAccount, report_id: int) -> ProfessionalReport:
    """发布**当前那一版**：版本行与报告头各写一遍，用的是**同一个时刻**。

    **两处都要写，而且不能只写一处**（2026-09-25，§5.13 缺口 1 之二）：

    - 版本行那三列是**权威**——领导可见性、按版本导出、版本时间线都读它；
    - 报告头那三列是**当前版本的镜像**（`new_version` 会把报告头改回 `DRAFT`，而版本行
      不动），它回答「这份报告现在处于哪一步」。只写版本行的话，界面上「草稿 · V1 →
      已发布 · V1」那一步永远不会发生（`report.status` 一直停在 `DRAFT`，
      `save_draft` 也不会拒绝后续覆盖——已发布的正文还是能被改写，那是更糟的事）。

    `now` 只取一次：两处要是各取一次 `datetime.now()`，同一件事会有两个时间戳，而它们在
    界面上是相邻两行（「发布于」与版本时间线里的那一条）。
    """
    report = _visible(db, user, report_id)
    if report.status != "DRAFT": raise AppError("VALIDATION_ERROR", "报告当前不可发布", 422)
    now = datetime.now()
    version = _version(db, report)
    version.status = "PUBLISHED"; version.published_by = user.id; version.published_at = now
    report.status = "PUBLISHED"; report.published_by = user.id; report.published_at = now; report.updated_by = user.id
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
        # **版本行自己的状态**，不是报告头那一列（2026-09-25）。两者在「没有新版本」时
        # 同值，一旦 `new_version()` 跑过就分岔：领导导出 V1 时报告头写着 `DRAFT`，
        # 而这份文件正是 V1 那一份——写成报告头会把一份已发布的文件标成「草稿」。
        ("状态", report_status_label(version.status)),
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


def report_document(db: Session, user: UserAccount, report_id: int, version_no: int | None) -> tuple[ProfessionalReport, ProfessionalReportVersion, ExportDocument]:
    """把一份报告（或其某个历史版本）渲染成可下载的 CSV。

    `row_count` **写 `len(rows)` 而不是一个字面量**：这一列在界面上是「导出 N 行」，
    而它此前硬编码 `8`——真正的行数取决于这一批快照里有几个维度，写死的那个数在
    维度缺失或多出时会静默说错（`ExportDocument.row_count` 不数表头那一行，这里也没有
    表头：这份文件是「项目 / 内容」两列的键值对）。

    **把版本行一起返回**（2026-09-25）：路由要拿它写审计里的 `version=`，而「不传
    `version_no` 时导出的是哪一版」对领导与心理老师**不是同一个答案**（`_version` 的
    `viewer` 分支）。此前路由自己算 `payload.version_no or report.current_version`
    ——领导在 V2 草稿期间导出的文件是 V1，而轨迹里写着 2（CLAUDE.md §8：轨迹要答得上
    「导的是哪一份」）。
    """
    report = _visible(db, user, report_id); version = _version(db, report, version_no, viewer=user)
    rows = _document_rows(report, version, _snapshot_of(report, version))
    output = io.StringIO(); writer = csv.writer(output)
    for item, content in rows:
        writer.writerow([item, content])
    return report, version, ExportDocument(csv_text="\ufeff" + output.getvalue(), columns=("项目", "内容"), row_count=len(rows))
