# 草稿核查报告（双轨）

运行：20261008_203812_8e75213f；结构化录入模型：deepseek-chat。

**A 栏 = 代码裁定（可复算）**：对错、正确值、计算式全部由本地 Python 生成。

**B 栏 = 模型判断（未完全核实）**：语义解释，仅供参考，不作为对错结论。

核查上下文：期间、币种、口径与来源须可确认；营业收入与经营现金流按已记录的合并口径。

## A. 确定结论（代码裁定）

| ID | 主张 | 裁决 | 原因或建议 | 证据与页码 |
|---|---|---|---|---|
| C1 | 贵州茅台2024年实现营业收入约1708.99亿元 | 证据支持 | 在陈述运算符与容差下与原文证据一致 | [80d5d67d35796958172e2d9f](../data/raw/600519_贵州茅台_2024年年度报告.pdf)（PDF第5页） |
| C2 | 归属于上市公司股东的净利润为862.28亿元 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [2be69095a86bcc7a3ed837df](../data/raw/600519_贵州茅台_2024年年度报告.pdf)（PDF第5页） |
| C3 | 基本每股收益为68.64元/股 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [8f99c7cf09961c5159ac7771](../data/raw/600519_贵州茅台_2024年年度报告.pdf)（PDF第5页） |
| C4 | 其中营业收入为1800亿元 | 确认错误 | 将该核查项改为 1709亿元，并引用所列年报页码。 | [80d5d67d35796958172e2d9f](../data/raw/600519_贵州茅台_2024年年度报告.pdf)（PDF第5页） |
- C4 替换：1800亿元 → 1709亿元。

## B. 模型判断（未完全核实 · 仅供参考）

| ID | 主张 | 解释 | 依据原文 | 状态 |
|---|---|---|---|---|
| — | （无） | | | |

## C. 需人工 / 证据不足

| ID | 原因码 | 说明 |
|---|---|---|
| — | （无） | |

## 程序计算明细（仅 A 栏）

### C1

```json
{
  "status": "match",
  "value": "1708.9915227634",
  "exact_value": "1708.9915227634",
  "claimed_value": "1708.99",
  "operator": "approx",
  "tolerance_pct": "2",
  "relative_error_pct": "0.00008910303999271608982830316054960123274177",
  "difference": "0.0015227634",
  "warning": null,
  "formula": "abs(actual-claimed)/abs(actual)*100 <= 2% | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "170899152276.34"
}
```

### C2

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

### C3

```json
{
  "status": "match",
  "value": "68.64",
  "exact_value": "68.64",
  "claimed_value": "68.64",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals)",
  "claimed_unit": "元/股",
  "actual_unit": "元/股",
  "actual_value": "68.64"
}
```

### C4

```json
{
  "status": "mismatch",
  "value": "1709",
  "exact_value": "1708.9915227634",
  "claimed_value": "1800",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-91.0084772366",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "170899152276.34"
}
```


## 审计

- 确定结论 4 条；模型判断 0 条；需人工 0 条。
- 模型不产生「确认错误/证据支持」；这两类仅来自本地比较程序。
- 运行模式：fallback_single_shot

## 工具调用链（agent）

（本次为单次拆解模式，未走工具循环）
