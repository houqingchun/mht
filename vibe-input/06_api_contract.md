# REST API契约

## 1. 通用约定

- 前缀：`/api/v1`
- JSON编码UTF-8。
- 所有写接口支持 `Idempotency-Key`。
- 统一响应：

```json
{
  "success": true,
  "data": {},
  "request_id": "req_xxx",
  "error": null
}
```

- 错误响应必须包含稳定的 `code`、用户可读的 `message` 和 `request_id`。
- 不在错误响应中返回答卷、重点题或家庭回访正文。

## 2. 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 按角色和账号登录 |
| POST | `/auth/logout` | 注销当前会话 |
| GET | `/auth/me` | 当前用户和数据范围 |
| POST | `/auth/change-password` | 修改本人密码 |
| POST | `/admin/accounts/{id}/reset-password` | 管理员重置密码 |
| GET | `/admin/accounts` | 管理员查看账号列表 |

登录请求：

```json
{
  "role": "student",
  "account": "S001",
  "password": "123456"
}
```

登录响应不得返回密码；应返回用户、角色、`must_change_password` 和过期时间。

## 3. 组织与导入

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/students` | 按权限查询学生 |
| POST | `/students/import/preview` | 上传并校验学生文件 |
| POST | `/students/import/commit` | 确认写入校验通过记录 |
| GET | `/students/import/template` | 下载学生模板 |
| GET | `/schools/{id}/statistics` | 学校聚合统计 |

导入必须分为preview和commit两个接口，commit必须携带preview token或导入批次号。

## 4. 量表与测评任务

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/scales/import/preview` | 题库格式校验 |
| POST | `/scales/drafts` | 创建草稿版本 |
| POST | `/assessment-tasks` | 新建测评任务 |
| GET | `/assessment-tasks` | 查询任务 |
| PATCH | `/assessment-tasks/{id}` | 编辑任务 |
| GET | `/assessment-tasks/{id}/completion` | 完成明细 |
| GET | `/assessment-tasks/{id}/completion/export` | 导出完成统计 |

## 5. 学生答题

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/student/tasks` | 学生待完成任务 |
| POST | `/assessment-sessions` | 创建答题会话 |
| PUT | `/assessment-sessions/{id}/answers/{questionNo}` | 保存单题答案 |
| GET | `/assessment-sessions/{id}` | 继续答题 |
| POST | `/assessment-sessions/{id}/submit` | 提交答卷 |
| GET | `/student/assessment-history` | 本人完成记录 |

提交接口必须再次校验题目数量、答案合法性、重复提交和会话归属。提交成功后答案不可由学生修改。

## 6. 重点学生与关怀

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/care-cases` | 重点学生列表 |
| GET | `/care-cases/{studentId}` | 学生关怀档案 |
| POST | `/care-cases/batch-assign` | 批量分配负责人 |
| POST | `/care-cases/{id}/reviews` | 创建人工复核 |
| POST | `/care-cases/{id}/follow-ups` | 新增跟进 |
| POST | `/care-cases/{id}/family-contacts` | 新增家庭回访 |
| POST | `/care-cases/{id}/retests` | 安排复测 |
| POST | `/care-cases/{id}/close` | 关闭档案 |
| POST | `/care-cases/{id}/reopen` | 重新打开档案 |
| POST | `/care-cases/export` | 受控导出 |
| POST | `/care-cases/high-risk/export` | 高度关注导出 |

查看重点题需要独立接口并传入查看原因；该接口必须写审计。

## 7. 统计与审计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/analytics/overview` | 聚合指标 |
| GET | `/analytics/by-grade` | 年级统计 |
| GET | `/analytics/by-class` | 班级统计 |
| GET | `/students/{id}/trends` | 学生历史趋势，按权限返回 |
| GET | `/audit-logs` | 审计查询 |
| POST | `/audit-logs/export` | 审计导出 |

## 8. 权限失败

- 未登录：`401 AUTH_REQUIRED`
- 无角色权限：`403 ROLE_FORBIDDEN`
- 超出数据范围：`403 SCOPE_FORBIDDEN`
- 重点题未说明原因：`422 PURPOSE_REQUIRED`
- 答案不完整：`422 ANSWERS_INCOMPLETE`
- 重复提交：返回原提交结果，不重复计算
