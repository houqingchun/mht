# Phase 3 Scale Engine

## 已实现

- MHT 100 题配置校验。
- 答案合法性校验：必须完整回答 1-100 题，答案只能为 `YES` / `NO`。
- 原始计分：`YES=1`，`NO=0`。
- 效度分：82、84、86、88、90、92、94、96、98、100。
- 内容总分：排除 10 道效度题，共 90 题。
- 总分等级：
  - `0-55`：`GENERAL_RANGE`
  - `56-64`：`NEEDS_ATTENTION`
  - `65-90`：`KEY_ATTENTION`
- 维度等级：
  - `0-3`：`LOW`
  - `4-7`：`MEDIUM`
  - `8+`：`HIGH`
- 重点题：85、97 任一题为 `YES` 时生成 `MANUAL_REVIEW_REQUIRED` 风险事件。
- 结果保留 `scale_version` 和 `rule_version`。

## 数据库

新增迁移：

```text
0002_scale_engine_schema.py
```

新增表：

```text
assessment_scale
scale_question
scale_rule
assessment_session
assessment_answer
assessment_result
dimension_result
risk_event
```

开发 seed 已写入：

- `MHT / MHT-1.1.0`（题目取自 `data/mht_scale.json` 的真实题库；版本号由
  `db/seed.py` 的 `MHT_SCALE_VERSION` 一处决定）
- 100 题
- `MHT-RULE-1.1.0`（由 `scale_rule_service.rule_version_for(code, version)` 生成，
  种子/规则编辑/题库导入三个创建方都走它）

## 待确认

`TODO_BUSINESS_CONFIRMATION`：规格只明确 `validity_score >= 7` 为 `RETEST_RECOMMENDED`，尚未冻结 `QUESTIONABLE` 阈值。本阶段实现为：

- `0-6`：`VALID`
- `7-10`：`RETEST_RECOMMENDED`

冻结阈值后需要补充对应测试并迁移规则版本。

