# Phase 5 心理老师工作台与关注档案

## 已实现后端接口

- `GET /api/v1/counselor/workbench`
- `GET /api/v1/care-cases`
- `GET /api/v1/care-cases/{student_id}`
- `POST /api/v1/care-cases/{case_id}/reviews`
- `POST /api/v1/care-cases/{case_id}/follow-ups`
- `POST /api/v1/care-cases/{case_id}/family-contacts`
- `POST /api/v1/care-cases/{case_id}/retests`
- `POST /api/v1/care-cases/{case_id}/close`
- `POST /api/v1/care-cases/{case_id}/reopen`

## 已实现规则

- 只有心理老师可以访问关注档案接口。
- 学生提交测评命中 85 或 97 题后，系统自动创建 `risk_event` 和 `student_care_case`。
- 心理老师查看学生档案写审计。
- 人工复核写入 `manual_review`，并把风险事件标记为 `REVIEWED`。
- 跟进记录、家庭回访、复测计划均作为独立事实保存。
- 关闭档案必须填写原因、说明并确认已检查后续安排。
- 重新打开档案会记录一条“重新打开档案”的跟进事实。

## 已实现前端

- 心理老师工作台指标。
- 重点学生列表。
- 学生关注档案摘要。
- 人工复核、跟进、家庭回访、复测、关闭和重新打开操作。

## 验证

```text
backend pytest: 38 passed
frontend build: passed
```

