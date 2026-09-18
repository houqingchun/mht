# Vibe Coding分阶段开发计划

## 1. 技术基线

建议采用单体前后端应用，避免V1.0过早引入微服务：

- 前端：React、TypeScript、Ant Design、ECharts
- 后端：Python、FastAPI、SQLAlchemy、Pydantic、Alembic
- 数据库：MySQL 8.0
- 认证：JWT或HttpOnly安全Cookie、RBAC、数据范围鉴权
- 测试：Pytest、API集成测试、Playwright E2E
- 部署：Docker Compose

## 2. 目录建议

```text
backend/
  app/
    api/
    domain/
    application/
    scale_engine/
    repositories/
    models/
    security/
    audit/
  migrations/
  tests/
frontend/
  src/
    pages/
    components/
    features/auth/
    features/assessment/
    features/care-cases/
    features/analytics/
    features/admin/
    services/
    stores/
data/
  mht_scale.json
docs/
  api/
```

## 3. 开发顺序

### Phase 0：规则冻结

输出：业务规则确认表、冲突清单、待确认清单。没有确认的规则不得进入代码。

### Phase 1：项目骨架与数据库

完成MySQL迁移、基础实体、索引、外键、审计表和初始化账号。

### Phase 2：认证与权限

完成四类登录、会话、密码哈希、首次改密、管理员重置、RBAC和数据范围。

### Phase 3：Scale Engine

完成题库加载、答案校验、效度、总分、维度、风险规则和规则版本。先写单元测试再接API。

### Phase 4：测评任务与学生答题

完成任务、目标学生、答题会话、自动保存、提交锁定和学生页面。

### Phase 5：心理老师工作台

完成工作队列、重点学生列表、学生档案、人工复核、跟进、家庭回访和复测。

### Phase 6：德育领导和统计

完成学校/年级/班级聚合、趋势和管理进展；严格过滤高敏感正文。

### Phase 7：系统管理和导入导出

完成学生导入、题库导入、任务完成导出、重点学生受控导出和账号管理。

### Phase 8：安全、审计和验收

完成审计查询、敏感日志检查、越权测试、E2E测试、备份和部署检查。

## 4. Coding AI工作规则

- 每个Phase开始前先输出设计和影响范围，不直接写完整系统。
- 先迁移和契约，再业务代码，再页面。
- 每次修改必须同步更新测试。
- 不允许Mock掉评分、权限和审计核心逻辑。
- 不允许用前端角色切换代替后端鉴权。
- 不允许把原始答案、结果、风险和人工意见塞进一张结果表。
- 不允许为了Demo写死学生、绕过权限或删除失败测试。
- 发现不确定项必须标记 `TODO_BUSINESS_CONFIRMATION`。
- 发现冲突必须标记 `RULE_CONFLICT`，等待人工确认。

## 5. 每个Phase的完成定义

1. 代码可本地启动。
2. 数据库Migration可重复执行。
3. API契约和错误码已更新。
4. 单元测试、接口测试通过。
5. 页面关键路径可操作。
6. 权限和审计行为可验证。
7. 生成变更说明和下一阶段风险清单。

## 6. 给AI的第一条Prompt

```text
你已经读取 vibe-input 目录中的全部MD文件。

当前不要编写完整业务代码。请先输出：
1. 技术架构和模块边界；
2. MySQL 8.0表结构与迁移顺序；
3. 四类角色、登录和数据权限模型；
4. Scale Engine接口和测试设计；
5. REST API清单及错误码；
6. 前端页面路由和组件树；
7. 业务待确认项和规则冲突项。

任何未定义规则标记 TODO_BUSINESS_CONFIRMATION，任何冲突标记 RULE_CONFLICT。
输出设计后等待确认，不要生成完整实现。
```
