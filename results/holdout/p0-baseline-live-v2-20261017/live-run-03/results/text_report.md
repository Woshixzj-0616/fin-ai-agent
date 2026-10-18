# 草稿核查报告（双轨）

运行：20261017_223146_863320a3；结构化录入模型：deepseek-chat。

**A 栏 = 代码裁定（可复算）**：对错、正确值、计算式全部由本地 Python 生成。

**B 栏 = 模型判断（未完全核实）**：语义解释，仅供参考，不作为对错结论。

核查上下文：期间、币种、口径与来源须可确认；营业收入与经营现金流按已记录的合并口径。

## A. 确定结论（代码裁定）

| ID | 主张 | 裁决 | 原因或建议 | 证据与页码 |
|---|---|---|---|---|
| C1 | 中国移动2025年营业收入为10,501.87亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| C2 | 中国移动2025年归属于母公司股东的净利润为1,370.95亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| C3 | 紫金矿业2025年营业收入为3,490.79亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| C4 | 紫金矿业2025年归属于上市公司股东的净利润为517.77亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| C5 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| C6 | 中国移动2026年第一季度归属于母公司股东的净利润为293亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| C7 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| C8 | 紫金矿业2026年上半年归属于上市公司股东的净利润为391.70亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| C10 | 紫金矿业2025年经营活动产生的现金流量净额为754.30亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-OCF](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| C11 | 中国移动2025年营业收入为10,800亿元。 | 确认错误 | 将该核查项改为 10502亿元，并引用所列年报页码。 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| C12 | 中国移动2025年归属于母公司股东的净利润为1,400亿元。 | 确认错误 | 将该核查项改为 1371亿元，并引用所列年报页码。 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| C13 | 中国移动2026年第一季度营业收入为2,500亿元。 | 确认错误 | 将该核查项改为 2665亿元，并引用所列年报页码。 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| C14 | 中国移动2026年第一季度归属于母公司股东的净利润为306亿元。 | 确认错误 | 将该核查项改为 293亿元，并引用所列年报页码。 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| C15 | 紫金矿业2025年营业收入为3,036.40亿元。 | 确认错误 | 将该核查项改为 3490.79亿元，并引用所列年报页码。 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| C16 | 紫金矿业2025年归属于上市公司股东的净利润为320.51亿元。 | 确认错误 | 将该核查项改为 517.77亿元，并引用所列年报页码。 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| C17 | 紫金矿业2026年上半年营业收入为1,677.11亿元。 | 确认错误 | 将该核查项改为 1941.78亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| C18 | 紫金矿业2026年上半年归属于上市公司股东的净利润为232.92亿元。 | 确认错误 | 将该核查项改为 391.70亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| C21 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| C22 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
- C11 替换：10800亿元 → 10502亿元。
- C12 替换：1400亿元 → 1371亿元。
- C13 替换：2500亿元 → 2665亿元。
- C14 替换：306亿元 → 293亿元。
- C15 替换：3036.40亿元 → 3490.79亿元。
- C16 替换：320.51亿元 → 517.77亿元。
- C17 替换：1677.11亿元 → 1941.78亿元。
- C18 替换：232.92亿元 → 391.70亿元。

## B. 模型判断（未完全核实 · 仅供参考）

| ID | 主张 | 解释 | 依据原文 | 状态 |
|---|---|---|---|---|
| C27 | 预计中国移动2027年营业收入将继续增长。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 预计中国移动2027年营业收入将继续增长。 | 模型判断 |
| C28 | 紫金矿业铜业务仍是未来增长的重要来源。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 紫金矿业铜业务仍是公司未来增长的重要来源。 | 模型判断 |
| C29 | 中国移动2026年一季度经营表现整体稳健。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 中国移动2026年第一季度经营表现整体稳健。 | 模型判断 |
| C30 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 模型判断 |

## C. 需人工 / 证据不足

| ID | 原因码 | 说明 |
|---|---|---|
| C9 | unsupported_period | 季度/半年期间没有明确起止，不能与全年值混比 |
| C19 | deterministic_check | scope 不明确或不一致 |
| C20 | deterministic_check | scope 不明确或不一致 |
| C23 | deterministic_check | 未找到唯一匹配的公司、年度、指标与文件版本 |
| C24 | deterministic_check | 未找到唯一匹配的公司、年度、指标与文件版本 |
| C25 | deterministic_check | 未找到唯一匹配的公司、年度、指标与文件版本 |
| C26 | deterministic_check | 未找到唯一匹配的公司、年度、指标与文件版本 |

## 程序计算明细（仅 A 栏）

### C1

```json
{
  "status": "match",
  "value": "10501.87",
  "exact_value": "10501.87",
  "claimed_value": "10501.87",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### C2

```json
{
  "status": "match",
  "value": "1370.95",
  "exact_value": "1370.95",
  "claimed_value": "1370.95",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### C3

```json
{
  "status": "match",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3490.79",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### C4

```json
{
  "status": "match",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "517.77",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### C5

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### C6

```json
{
  "status": "match",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "293",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### C7

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### C8

```json
{
  "status": "match",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "391.70",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00192757",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### C10

```json
{
  "status": "match",
  "value": "754.30",
  "exact_value": "754.29516296",
  "claimed_value": "754.30",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00483704",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "75429516296"
}
```

### C11

```json
{
  "status": "mismatch",
  "value": "10502",
  "exact_value": "10501.87",
  "claimed_value": "10800",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-298.13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### C12

```json
{
  "status": "mismatch",
  "value": "1371",
  "exact_value": "1370.95",
  "claimed_value": "1400",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-29.05",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### C13

```json
{
  "status": "mismatch",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2500",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "165",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### C14

```json
{
  "status": "mismatch",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "306",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### C15

```json
{
  "status": "mismatch",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3036.40",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "454.39082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### C16

```json
{
  "status": "mismatch",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "320.51",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "197.26327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### C17

```json
{
  "status": "mismatch",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1677.11",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "264.6745863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### C18

```json
{
  "status": "mismatch",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "232.92",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "158.77807243",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### C21

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### C22

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```


## 审计

- 确定结论 19 条；模型判断 4 条；需人工 7 条。
- 模型不产生「确认错误/证据支持」；这两类仅来自本地比较程序。
- 运行模式：fallback_single_shot_tools_preserved

## 工具调用链（agent）

| # | 轮次 | 工具 | 状态 |
|---|---|---|---|
| 1 | 1 | `list_catalog` | ok · kind=companies |
| 2 | 1 | `list_catalog` | ok · kind=metrics |
| 3 | 2 | `find_evidence` | ok · company_name_or_code=中国移动, metric=revenue, period_year=2025 |
| 4 | 2 | `find_evidence` | ok · company_name_or_code=中国移动, metric=parent_net_profit, period_year=2025 |
| 5 | 2 | `find_evidence` | ok · company_name_or_code=紫金矿业, metric=revenue, period_year=2025 |
| 6 | 2 | `find_evidence` | ok · company_name_or_code=紫金矿业, metric=parent_net_profit, period_year=2025 |
| 7 | 3 | `find_evidence` | ok · company_name_or_code=中国移动, metric=revenue, period_year=2026 |
| 8 | 3 | `find_evidence` | ok · company_name_or_code=中国移动, metric=parent_net_profit, period_year=2026 |
| 9 | 3 | `find_evidence` | ok · company_name_or_code=紫金矿业, metric=revenue, period_year=2026 |
| 10 | 3 | `find_evidence` | ok · company_name_or_code=紫金矿业, metric=parent_net_profit, period_year=2026 |
| 11 | 4 | `find_evidence` | ok · company_name_or_code=中国移动, metric=total_assets, period_year=2025 |
| 12 | 4 | `find_evidence` | ok · company_name_or_code=紫金矿业, metric=operating_cash_flow, period_year=2025 |
| 13 | 4 | `find_evidence` | empty · company_name_or_code=中国移动, metric=revenue, period_year=2024 |
| 14 | 4 | `find_evidence` | empty · company_name_or_code=紫金矿业, metric=parent_net_profit, period_year=2024 |
| 15 | 5 | `find_evidence` | empty · company_name_or_code=中国移动, metric=revenue, period_year=2025 |
| 16 | 5 | `find_evidence` | empty · company_name_or_code=紫金矿业, metric=revenue, period_year=2025 |
| 17 | 5 | `find_evidence` | ok · company_name_or_code=紫金矿业, metric=revenue, period_year=2025 |

说明：`compare_claim` 是唯一产生对错的工具；其余只供取数/理解。
