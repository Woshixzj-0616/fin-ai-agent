# 统一结果外层（草案）

第一版只统一外层元数据，专业结果保留在 `payload` 中：

```json
{
  "module_id": "part2_profit",
  "module_version": "module-specific-version",
  "status": "completed",
  "report": {"report_id": "...", "period": "..."},
  "sources": [{"page": 42, "locator": "..."}],
  "payload": {},
  "warnings": [],
  "run_id": "..."
}
```

`payload` 不规定统一字段；模块可以返回表格、时间序列、判断、追问和专题卡片。只有运行状态、版本、报告身份、出处和警告属于共同外层。
