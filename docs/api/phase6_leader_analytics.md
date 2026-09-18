# Phase 6 德育领导与聚合统计

## 已实现后端接口

- `GET /api/v1/analytics/overview`
- `GET /api/v1/analytics/by-grade`
- `GET /api/v1/analytics/by-class`
- `GET /api/v1/leader/progress`

## 已实现规则

- 德育领导可以查看学校聚合趋势和重点进展摘要。
- 德育领导进展摘要不返回：
  - 原始答卷
  - 重点题具体回答
  - 家庭回访正文
  - 跟进正文
- 学生不能访问聚合统计。
- 心理老师可复用聚合接口查看授权范围内统计；当前 V1 数据范围仍为单校演示数据。

## 已实现前端

- `/leader/overview`
- 测评完成率、已完成/应完成、需关注摘要、计划复测。
- 年级统计。
- 班级统计。
- 重点进展摘要。

## 验证

```text
backend pytest: 40 passed
frontend build: passed
```

