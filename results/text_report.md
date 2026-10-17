# 草稿核查报告（双轨）

运行：20261014_152757_1a5272c7；结构化录入模型：deepseek-chat。

**A 栏 = 代码裁定（可复算）**：对错、正确值、计算式全部由本地 Python 生成。

**B 栏 = 模型判断（未完全核实）**：语义解释，仅供参考，不作为对错结论。

核查上下文：期间、币种、口径与来源须可确认；营业收入与经营现金流按已记录的合并口径。

## A. 确定结论（代码裁定）

| ID | 主张 | 裁决 | 原因或建议 | 证据与页码 |
|---|---|---|---|---|
| C1 | 贵州茅台2024年合并口径营业收入为1708.99亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [90b9e57bf6b244be21e84176]（PDF第5页） |
| C3 | 贵州茅台2024年合并口径营业收入为1708.99万元。 | 确认错误 | 将该核查项改为 17089915.23万元，并引用所列年报页码。 | [90b9e57bf6b244be21e84176]（PDF第5页） |
| C4 | 贵州茅台2024年合并口径营业收入同比增长15.71%。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [90b9e57bf6b244be21e84176]（PDF第5页）；[d3fe304c4c3a83041491c5b2]（PDF第5页） |
| C5 | 贵州茅台2024年合并口径营业收入同比下降15.71%。 | 确认错误 | 将该核查项改为 15.71%，并引用所列年报页码。 | [90b9e57bf6b244be21e84176]（PDF第5页）；[d3fe304c4c3a83041491c5b2]（PDF第5页） |
| C7 | 贵州茅台2024年归母净利润为862.28亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [9069bd51512aeff0eead7874]（PDF第5页） |
- C3 替换：1708.99万元 → 17089915.23万元。
- C5 替换：-15.71% → 15.71%。

## B. 模型判断（未完全核实 · 仅供参考）

| ID | 主张 | 解释 | 依据原文 | 状态 |
|---|---|---|---|---|
| C8 | 预计贵州茅台2025年合并口径营业收入同比增长10%。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 预计贵州茅台2025年合并口径营业收入同比增长10%。 | 模型判断 |

## C. 需人工 / 证据不足

| ID | 原因码 | 说明 |
|---|---|---|
| C2 | unresolved_period | 年度未明确或无法从原文定位，不猜测相对日期 |
| C6 | unresolved_period | 年度未明确或无法从原文定位，不猜测相对日期 |
| C9 | unresolved_company | 原文公司名称/代码无法对应到本次已加载材料，或无法在草稿中定位，不猜测公司 |

## 程序计算明细（仅 A 栏）

### C1

```json
{
  "status": "match",
  "value": "1708.99",
  "exact_value": "1708.9915227634",
  "claimed_value": "1708.99",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0015227634",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "170899152276.34"
}
```

### C3

```json
{
  "status": "mismatch",
  "value": "17089915.23",
  "exact_value": "17089915.227634",
  "claimed_value": "1708.99",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "17088206.237634",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "万元",
  "actual_unit": "元",
  "actual_value": "170899152276.34"
}
```

### C4

```json
{
  "status": "match",
  "value": "15.71",
  "exact_value": "15.71195129479080718889266823791667239671",
  "claimed_value": "15.71",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.001951294790807188892668237917",
  "formula": "round_half_up(yoy_percent, claimed_decimal_places)",
  "claimed_unit": "%",
  "yoy_calculation": {
    "status": "ok",
    "value": "15.71195129479080718889266823791667239671",
    "unit": "%",
    "operands": {
      "current": "170899152276.34",
      "previous": "147693604994.14"
    },
    "formula": "(current - previous) / previous * 100",
    "negative_policy": "review",
    "evidence_ids": [
      "90b9e57bf6b244be21e84176",
      "d3fe304c4c3a83041491c5b2"
    ]
  }
}
```

### C5

```json
{
  "status": "mismatch",
  "value": "15.71",
  "exact_value": "15.71195129479080718889266823791667239671",
  "claimed_value": "-15.71",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "31.42195129479080718889266824",
  "formula": "round_half_up(yoy_percent, claimed_decimal_places)",
  "claimed_unit": "%",
  "yoy_calculation": {
    "status": "ok",
    "value": "15.71195129479080718889266823791667239671",
    "unit": "%",
    "operands": {
      "current": "170899152276.34",
      "previous": "147693604994.14"
    },
    "formula": "(current - previous) / previous * 100",
    "negative_policy": "review",
    "evidence_ids": [
      "90b9e57bf6b244be21e84176",
      "d3fe304c4c3a83041491c5b2"
    ]
  }
}
```

### C7

```json
{
  "status": "match",
  "value": "862.28",
  "exact_value": "862.2814642162",
  "claimed_value": "862.28",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0014642162",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "86228146421.62"
}
```


## 审计

- 确定结论 5 条；模型判断 1 条；需人工 3 条。
- 模型不产生「确认错误/证据支持」；这两类仅来自本地比较程序。
- 运行模式：native_tool_calls

## 工具调用链（agent）

| # | 轮次 | 工具 | 状态 |
|---|---|---|---|
| 1 | 1 | `find_evidence` | ok · company_name_or_code=贵州茅台, metric=revenue, period_year=2024 |
| 2 | 1 | `find_evidence` | ok · company_name_or_code=贵州茅台, metric=parent_net_profit, period_year=2024 |
| 3 | 1 | `compute_yoy` | ok · company_name_or_code=贵州茅台, metric=revenue, year=2024 |
| 4 | 1 | `find_evidence` | error · company_name_or_code=五粮液, metric=revenue, period_year=2024 |
| 5 | 2 | `compare_claim` | 证据支持 · company_name_or_code=贵州茅台, metric=revenue, period_year=2024 |
| 6 | 2 | `compare_claim` | 确认错误 · company_name_or_code=贵州茅台, metric=revenue, period_year=2024 |
| 7 | 2 | `compare_claim` | 确认错误 · company_name_or_code=贵州茅台, metric=revenue, period_year=2024 |
| 8 | 2 | `compare_claim` | 证据支持 · company_name_or_code=贵州茅台, metric=revenue, period_year=2024 |
| 9 | 2 | `compare_claim` | 确认错误 · company_name_or_code=贵州茅台, metric=revenue, period_year=2024 |
| 10 | 2 | `compare_claim` | 口径冲突／需人工复核 · company_name_or_code=贵州茅台, metric=parent_net_profit, period_year=2024 |
| 11 | 3 | `compare_claim` | 证据支持 · company_name_or_code=贵州茅台, metric=parent_net_profit, period_year=2024 |

说明：`compare_claim` 是唯一产生对错的工具；其余只供取数/理解。
