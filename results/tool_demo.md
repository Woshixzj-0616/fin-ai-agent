# 工具调用链演示（agent · 无外网模型）

- 证据库：贵州茅台 2024 年报 **35** 条（抽取代码产出，非模型生成）
- 工具定义：5 只（只读）
- 口径：**模型只下命令，对错只出自 `compare_claim` → 本地 `finance.check_claim`**

## 调用轨迹

### 第 1 步 · `list_catalog`

```json
{
  "arguments": {
    "kind": "metrics"
  }
}
```
**结果状态**：`ok`

```json
{
  "status": "ok"
}
```

### 第 2 步 · `find_evidence`

```json
{
  "arguments": {
    "company_name_or_code": "贵州茅台",
    "metric": "revenue",
    "period_year": 2024
  }
}
```
**结果状态**：`ok`

```json
{
  "status": "ok",
  "count": 1
}
```

### 第 3 步 · `compute_yoy`

```json
{
  "arguments": {
    "company_name_or_code": "贵州茅台",
    "metric": "revenue",
    "year": 2024
  }
}
```
**结果状态**：`ok`

```json
{
  "status": "ok",
  "yoy": {
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
      "138f1426f6b43b79d3f2384a",
      "3ac0c3bc647151a7c51cccf1"
    ]
  }
}
```

### 第 4 步 · `compare_claim`

```json
{
  "arguments": {
    "company_name_or_code": "贵州茅台",
    "metric": "revenue",
    "period_year": 2024,
    "kind": "amount",
    "claimed_value": "1500.00",
    "claimed_unit": "亿元",
    "operator": "eq"
  }
}
```
**结果状态**：`确认错误`

```json
{
  "verdict": "confirmed_error",
  "status": "确认错误",
  "reason": "可比口径下陈述值与证据或计算结果不同",
  "evidence_ids": [
    "138f1426f6b43b79d3f2384a"
  ],
  "expected": "1708.99"
}
```

### 第 5 步 · `compare_claim`

```json
{
  "arguments": {
    "company_name_or_code": "贵州茅台",
    "metric": "revenue",
    "period_year": 2024,
    "kind": "amount",
    "claimed_value": "1709",
    "claimed_unit": "亿元",
    "operator": "approx",
    "tolerance_pct": 2.0
  }
}
```
**结果状态**：`证据支持`

```json
{
  "verdict": "evidence_supported",
  "status": "证据支持",
  "reason": "在陈述运算符与容差下与原文证据一致",
  "evidence_ids": [
    "138f1426f6b43b79d3f2384a"
  ],
  "expected": null
}
```

## 汇总

- 工具次数：5（compare_claim×2、list_catalog×1、find_evidence×1、compute_yoy×1）
- 第 3–5 步：`compute_yoy` / `compare_claim` 返回里都带 **evidence_id**，可回年报页。
- 错值 1500 亿 → `confirmed_error`；约数 1709 亿 → `evidence_supported`（±2%）。

## 这和「让模型直接说对错」的差别

| | 自由 LLM | 本演示 |
|---|---|---|
| 谁下结论 | 模型 | `compare_claim`（本地） |
| 有无页码 | 常无 | 有（evidence_id + page + bbox） |
| 能否重算 | 难 | 能（Decimal 明细） |
