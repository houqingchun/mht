-- ==============================================================================
-- 心晴 · 数据库增量升级脚本
--
-- 从  V1.1.6（迁移 0020_total_excludes_validity）
-- 到  V2.3.0（迁移 0024_backup_record）
--
-- 由 deploy/build_migration_sql.py 生成，**不要手工编辑**：它的数据源是
-- 链上 4 条迁移各自的 PRECHECKS 常量与 alembic 的离线渲染。
-- 改了迁移就重跑一次 `make db-upgrade-sql`。
-- ==============================================================================

-- 【怎么执行】
--
--   第一步 · 先备份。这一步没有替代品：
--       mysqldump -h HOST -u USER -p --default-character-set=utf8mb4 \
--         --single-transaction DB > backup_$(date +%Y%m%d).sql
--
--   第二步 · 把整个文件交给 mysql 执行。文件按迁移分成 4 条，每条只有
--       【DDL】一段——这一条线上一条检查都没有（见下面的说明）。
--             第 1 条 / 共 4 条：0021_v2_task_governance
--               【DDL】…    ← 这条迁移真正动手的地方
--       ★ 按文件里的次序往下走就行。这几条迁移全都是「只加不改」的：
--         只建新表、只加可空的新列、按条件回填，**没有一条会因为库里已有的
--         数据形状失败**，所以不需要事先问一遍（也就没有检查那一段）。
--       ★ 用 mysql 命令行客户端执行，并在第一条出错的语句处停下
--         （命令行默认就是这样；图形工具要确认它没有开「出错继续」）。
--              mysql -h HOST -u USER -p --default-character-set=utf8mb4 DB < 本文件
--
--   第三步 · 核对：
--       SELECT version_num FROM alembic_version;
--       应当是 0024_backup_record
--
--   注意两件事：
--   ① 这个文件是 UTF-8、含中文注释，**必须**带 --default-character-set=utf8mb4，
--      否则中文会按连接编码解成乱码（注释无所谓，但你要读的就是它）。
--   ② 【只该执行一次】。跑第二遍会撞 Duplicate column / Duplicate key name 之类的错，
--      那是正常的，不是文件坏了。
-- ==============================================================================



-- ==============================================================================
-- 第 1 条 / 共 4 条：0021_v2_task_governance
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0021_v2_task_governance。
-- ------------------------------------------------------------------------------
-- Running upgrade 0020_total_excludes_validity -> 0021_v2_task_governance

ALTER TABLE assessment_task ADD COLUMN voided_at DATETIME;

ALTER TABLE assessment_task ADD COLUMN voided_by INTEGER;

ALTER TABLE assessment_task ADD COLUMN void_reason VARCHAR(500);

ALTER TABLE assessment_task ADD CONSTRAINT assessment_task_fk_voided_by FOREIGN KEY(voided_by) REFERENCES user_account (id);

ALTER TABLE risk_event ADD COLUMN voided_at DATETIME;

ALTER TABLE risk_event ADD COLUMN voided_by INTEGER;

ALTER TABLE risk_event ADD COLUMN void_reason VARCHAR(500);

ALTER TABLE risk_event ADD CONSTRAINT risk_event_fk_voided_by FOREIGN KEY(voided_by) REFERENCES user_account (id);

UPDATE alembic_version SET version_num='0021_v2_task_governance' WHERE alembic_version.version_num = '0020_total_excludes_validity';


-- ==============================================================================
-- 第 2 条 / 共 4 条：0022_professional_reports
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0022_professional_reports。
-- ------------------------------------------------------------------------------
-- Running upgrade 0021_v2_task_governance -> 0022_professional_reports

CREATE TABLE professional_report (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    report_no VARCHAR(64) NOT NULL, 
    school_id INTEGER NOT NULL, 
    report_type VARCHAR(32) NOT NULL, 
    title VARCHAR(255) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    task_scope_json JSON NOT NULL, 
    analysis_mode VARCHAR(32) NOT NULL, 
    statistics_snapshot_json JSON NOT NULL, 
    current_version INTEGER NOT NULL, 
    created_by INTEGER NOT NULL, 
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
    updated_by INTEGER NOT NULL, 
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
    published_by INTEGER, 
    published_at DATETIME, 
    PRIMARY KEY (id), 
    CONSTRAINT professional_report_fk_school FOREIGN KEY(school_id) REFERENCES school (id), 
    CONSTRAINT professional_report_fk_created_by FOREIGN KEY(created_by) REFERENCES user_account (id), 
    CONSTRAINT professional_report_fk_updated_by FOREIGN KEY(updated_by) REFERENCES user_account (id), 
    CONSTRAINT professional_report_fk_published_by FOREIGN KEY(published_by) REFERENCES user_account (id), 
    CONSTRAINT uq_professional_report_no UNIQUE (report_no)
);

CREATE INDEX ix_professional_report_school_status ON professional_report (school_id, status);

CREATE TABLE professional_report_version (
    id INTEGER NOT NULL AUTO_INCREMENT, 
    report_id INTEGER NOT NULL, 
    version_no INTEGER NOT NULL, 
    overall_summary TEXT NOT NULL, 
    dimension_interpretation TEXT NOT NULL, 
    sample_validity_note TEXT NOT NULL, 
    support_plan TEXT NOT NULL, 
    statistics_snapshot_json JSON NOT NULL, 
    created_by INTEGER NOT NULL, 
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, 
    PRIMARY KEY (id), 
    CONSTRAINT professional_report_version_fk_report FOREIGN KEY(report_id) REFERENCES professional_report (id), 
    CONSTRAINT professional_report_version_fk_created_by FOREIGN KEY(created_by) REFERENCES user_account (id), 
    CONSTRAINT uq_professional_report_version UNIQUE (report_id, version_no)
);

UPDATE alembic_version SET version_num='0022_professional_reports' WHERE alembic_version.version_num = '0021_v2_task_governance';


-- ==============================================================================
-- 第 3 条 / 共 4 条：0023_report_version_publish
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0023_report_version_publish。
-- ------------------------------------------------------------------------------
-- Running upgrade 0022_professional_reports -> 0023_report_version_publish

ALTER TABLE professional_report_version ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'DRAFT';

ALTER TABLE professional_report_version ADD COLUMN published_at DATETIME;

ALTER TABLE professional_report_version ADD COLUMN published_by INTEGER;

ALTER TABLE professional_report_version ADD CONSTRAINT professional_report_version_fk_published_by FOREIGN KEY(published_by) REFERENCES user_account (id);

UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.status = 'PUBLISHED'
        WHERE r.status IN ('PUBLISHED', 'ARCHIVED');

UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.published_at = r.published_at, v.published_by = r.published_by
        WHERE r.status IN ('PUBLISHED', 'ARCHIVED') AND v.version_no = r.current_version;

UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.status = 'PUBLISHED', v.published_at = r.published_at, v.published_by = r.published_by
        WHERE r.status NOT IN ('PUBLISHED', 'ARCHIVED')
          AND r.current_version > 1
          AND v.version_no = r.current_version - 1;

UPDATE professional_report_version v
        JOIN professional_report r ON r.id = v.report_id
        SET v.status = 'PUBLISHED'
        WHERE r.status NOT IN ('PUBLISHED', 'ARCHIVED')
          AND r.current_version > 1
          AND v.version_no < r.current_version;

UPDATE alembic_version SET version_num='0023_report_version_publish' WHERE alembic_version.version_num = '0022_professional_reports';


-- ==============================================================================
-- 第 4 条 / 共 4 条：0024_backup_record
-- ==============================================================================

-- 【检查】这一条迁移没有需要事先问一遍的数据形状，直接往下跑它的 DDL。

-- 【DDL】这条迁移要执行的全部语句（由 alembic 离线渲染，一条不多、一条不少），
-- 末尾那条 UPDATE 把 alembic_version 推到 0024_backup_record。
-- ------------------------------------------------------------------------------
-- Running upgrade 0023_report_version_publish -> 0024_backup_record

CREATE TABLE backup_record (
    id BIGINT NOT NULL AUTO_INCREMENT, 
    `trigger` VARCHAR(16) NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    file_name VARCHAR(255), 
    file_size BIGINT, 
    file_sha256 CHAR(64), 
    message TEXT, 
    operator_id INTEGER, 
    created_at DATETIME NOT NULL DEFAULT now(), 
    updated_at DATETIME NOT NULL DEFAULT now(), 
    PRIMARY KEY (id), 
    CONSTRAINT backup_record_ibfk_1 FOREIGN KEY(operator_id) REFERENCES user_account (id)
);

CREATE INDEX ix_backup_record_created_at ON backup_record (created_at);

CREATE INDEX ix_backup_record_status_created ON backup_record (status, created_at);

UPDATE alembic_version SET version_num='0024_backup_record' WHERE alembic_version.version_num = '0023_report_version_publish';


-- ==============================================================================
-- 到这里就结束了。核对一句：
--   SELECT version_num FROM alembic_version;
-- 应当是 0024_backup_record。
--
-- 程序文件那一侧照常走一键安装包（升级模式不会重跑 seed、不会重置管理员密码、
-- 不会碰数据库里的数据，只更新程序文件并再跑一次迁移——那时这一步已经是空转的）。
-- ==============================================================================
