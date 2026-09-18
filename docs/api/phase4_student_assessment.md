# Phase 4 学生答题

## 已实现后端接口

- `GET /api/v1/student/tasks`
- `POST /api/v1/assessment-sessions`
- `GET /api/v1/assessment-sessions/{session_id}`
- `PUT /api/v1/assessment-sessions/{session_id}/answers/{question_no}`
- `POST /api/v1/assessment-sessions/{session_id}/submit`

## 已实现规则

- 只有 `student` 角色可访问学生答题接口。
- 学生只能访问本人目标任务和本人答题会话。
- 创建会话是幂等行为：同一学生、同一任务复用已有会话。
- 保存答案只允许 `YES` / `NO`。
- 已提交答卷锁定，禁止继续修改。
- 提交时后端重新校验 100 题完整性和答案合法性。
- 提交成功后写入：
  - `assessment_result`
  - `dimension_result`
  - `risk_event`
  - `assessment_target.completed_at`
- 重复提交返回已有结果，不重复生成风险事件。

## 已实现前端

- 学生首页读取待完成任务。
- 开始/继续作答。
- 每次选择自动保存。
- 显示已答数量。
- 定位第一道未答题。
- 提交前二次确认。
- 提交后返回学生首页，不展示分数、维度或风险标签。

## 验证

```text
backend pytest: 32 passed
frontend build: passed
```

