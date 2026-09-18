# Phase 7 MHT题库导入

## 已实现后端接口

- `POST /api/v1/scales/import/preview`
- `POST /api/v1/scales/drafts`

## 已实现规则

- 只有系统管理员可以导入题库。
- 支持 CSV 和 JSON。
- 预览阶段只校验，不写数据库。
- 校验项：
  - 题目总数必须为 100。
  - 题号必须完整覆盖 1-100。
  - 效度题必须为 82、84、86、88、90、92、94、96、98、100。
  - 重点题必须为 85 和 97。
  - 内容题必须有合法维度映射。
  - 题干不能为空。
- 创建草稿版本写入：
  - `assessment_scale`
  - `scale_question`
  - `scale_rule`
- 草稿版本不会影响已发布版本和历史测评结果。
- 创建草稿写入审计日志。

## 已实现前端

- `/admin/system`
- MHT题库 CSV/JSON 文件选择。
- 校验预览、全局错误和前20行校验结果展示。
- 校验通过后创建草稿版本。

## 验证

```text
backend pytest: 46 passed
frontend build: passed
scale import preview smoke test: passed
```

