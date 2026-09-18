# Phase 8 审计查询与受控导出

## 已实现后端接口

- `GET /api/v1/audit-logs`
- `POST /api/v1/care-cases/export`
- `POST /api/v1/care-cases/high-risk/export`

## 已实现规则

- 审计日志可查询最近记录。
- 学生不能查询审计日志。
- 普通关注档案导出默认只包含摘要字段：
  - 学生脱敏名
  - 学号
  - 年级
  - 班级
  - 档案状态
  - 筛查分类
  - 效度状态
  - 打开/更新时间
- 普通导出不包含：
  - 原始答卷
  - 重点题具体回答
  - 跟进正文
  - 家庭回访正文
- 高度关注导出必须填写用途，否则返回 `PURPOSE_REQUIRED`。
- 导出行为写审计日志。

## 已实现前端

- 管理员页显示审计日志。
- 心理老师工作台支持导出摘要。
- 心理老师工作台支持高度关注导出，用途必填。

## 验证

```text
backend pytest: 50 passed
frontend build: passed
audit logs smoke test: passed
high-risk export purpose validation: passed
```

