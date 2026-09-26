"""professional report version publish metadata

0022 只把发布状态与发布人 / 发布时间放在**报告头**上（`professional_report.status` /
`published_by` / `published_at`），而报告头那一列回答的是「**当前版本**发布了吗」。
于是有一个从写下那天就存在的缺陷（§5.13 复审的缺口 1 之一，2026-09-25 修）：

    一份已发布的报告点「新建版本」之后，报告头被改回 `DRAFT`，而德育领导的可见性
    恰好按报告头那一列过滤（`reporting_service._visible` / `list_reports`）——
    **V2 编辑期间，已经发布出去的 V1 对领导端消失了**，而且 `published_at` /
    `published_by` 也一起被当成「这份报告还没发布」。一所学校正在改第二版，
    领导端就读不到第一版了，屏幕上只表现为列表里少了一行。

所以发布状态下沉一层，成为**版本级**的三列（`status` / `published_at` /
`published_by`），报告头那三列保持原样并继续镜像当前版本——两者的关系是
「报告头 = 当前版本那一行」的镜像，而不是两个独立的判断。

## 回填只填**能证明**的，猜不出的一律留 NULL

`published_by` 是一句关于「谁发布了这一版」的事实陈述，编一个值比留空更糟
（CLAUDE.md §21 那条「`tested_at` 刻意不回填」的同一条理由）。三档，各自的判据不同：

| 报告头 | 版本 | 回填成 | 为什么能证明 |
|---|---|---|---|
| `PUBLISHED` / `ARCHIVED` | 全部 ≤ current_version | `PUBLISHED` | 只有 `publish()` 会走到这两个状态，而它只发布**当时那一版**；更早的版本之所以存在，只能是因为它发布过之后才被 `new_version()` 顶下去 |
| 同上 | 只有 `current_version` 那一行 | 另填 `published_at` / `published_by`（抄报告头） | 报告头那两列**只有 `publish()` 写**，而它写的是当前版本——所以它们必然属于当前版本 |
| `DRAFT` 且 `current_version > 1` | `current_version - 1` 那一行 | `PUBLISHED` + 抄报告头的两个时间 / 人 | 同上：`new_version()` 不清那两列，所以它们仍是**上一版**发布时留下的 |
| `DRAFT` 且 `current_version > 1` | `< current_version - 1` 的行 | 只回填 `PUBLISHED`，时间与人留 NULL | 它们是更早发布过的（同上），但报告头只留着最近一次，更早那一对值**已经没有了** |
| `DRAFT` 且 `current_version == 1` | 唯一那一行 | 保持 `DRAFT` | 它从没发布过 |

`ARCHIVED` 与 `PUBLISHED` 同档：那个状态今天没有任何写入方（§5.9 第 6 条裁决不做），
但它只能由 `PUBLISHED` 过来，所以按同一档处理；否则一份被归档的报告会因为版本行是
`DRAFT` 而对领导不可见——那是把「改第二版」那个缺陷换成另一个更安静的版本。

**`status` 带 `server_default='DRAFT'`**：加列的那一刻所有既有行都拿到 `DRAFT`，下面
四条 UPDATE 才把它们分开。默认值留着不回退——新插入的版本行走 `default="DRAFT"`
（模型），DDL 上那一句是给升级路径的。

## 三列都在 `professional_report_version` 上，不进报告头

报告头那三列**不删**：`serialize` 继续发 `status`（= 当前版本的状态）、
`published_at` / `published_by`（= 报告的**最近一次**发布），今天的界面上没有任何一处
按它们做判断，删掉只会让响应少三个既有字段。两者的分工写进 `reporting_service` 的
`serialize` 里。
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_report_version_publish"
down_revision = "0022_professional_reports"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("professional_report_version", sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"))
    op.add_column("professional_report_version", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.add_column("professional_report_version", sa.Column("published_by", sa.Integer()))
    op.create_foreign_key("professional_report_version_fk_published_by", "professional_report_version", "user_account", ["published_by"], ["id"])

    # 全部已发布的报告（含 ARCHIVED）：所有现存版本都发布过。
    op.execute(
        """
        UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.status = 'PUBLISHED'
        WHERE r.status IN ('PUBLISHED', 'ARCHIVED')
        """
    )
    # 报告头 PUBLISHED / ARCHIVED：那两列时间 / 人只由 publish() 写，而它写的是当前版本。
    op.execute(
        """
        UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.published_at = r.published_at, v.published_by = r.published_by
        WHERE r.status IN ('PUBLISHED', 'ARCHIVED') AND v.version_no = r.current_version
        """
    )
    # 报告头 DRAFT 且已有新版本：new_version() 不清那两列，所以它们属于**上一版**。
    op.execute(
        """
        UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.status = 'PUBLISHED', v.published_at = r.published_at, v.published_by = r.published_by
        WHERE r.status NOT IN ('PUBLISHED', 'ARCHIVED')
          AND r.current_version > 1
          AND v.version_no = r.current_version - 1
        """
    )
    # 更早的那些版本：发布过，但那一对值已经不在报告头上了——只回填状态，时间与人留 NULL。
    op.execute(
        """
        UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.status = 'PUBLISHED'
        WHERE r.status NOT IN ('PUBLISHED', 'ARCHIVED')
          AND r.current_version > 1
          AND v.version_no < r.current_version
        """
    )


def downgrade():
    # 先摘外键再删列：MySQL 不允许删一条仍被外键引用着的列（1828 / 1553 那一族）。
    # **不单独 drop_index**：`professional_report_version_fk_published_by` 那个索引是外键
    # 自己建的，删列时一并带走——0022 的 downgrade 就是被这一句多余的动作坑过一次
    # （CLAUDE.md §16 / 0022 末尾那条注释）。
    op.drop_constraint("professional_report_version_fk_published_by", "professional_report_version", type_="foreignkey")
    op.drop_column("professional_report_version", "published_by")
    op.drop_column("professional_report_version", "published_at")
    op.drop_column("professional_report_version", "status")
