# Phase 7 系统管理与学生导入

## 已实现后端接口

- `GET /api/v1/students`
- `GET /api/v1/students/import/template`
- `POST /api/v1/students/import/preview`
- `POST /api/v1/students/import/commit`
- 复用已有：
  - `GET /api/v1/admin/accounts`
  - `POST /api/v1/admin/accounts/{id}/reset-password`

## 已实现规则

- 只有系统管理员可以导入学生。
- 学生导入支持 CSV 和 JSON。
- 导入分为预览和确认两步。
- 预览阶段只校验，不写数据库。
- 缺少学号、姓名、年级、班级的记录进入错误报告。
- 文件内重复学号、数据库已有学号进入错误报告。
- 确认导入后创建：
  - 年级
  - 班级
  - 学生
  - 学生账号
  - 学生本人数据范围
- 导入学生写入审计日志。

## 已实现前端

- `/admin/system`
- 账号列表与密码重置。
- 学生列表。
- 学生 CSV/JSON 选择、预览、错误展示、确认导入。

## 验证

```text
backend pytest: 43 passed
frontend build: passed
student import preview smoke test: passed
```

