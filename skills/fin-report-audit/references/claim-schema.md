# 主张 Schema（claims）

权威定义在 `agent/llm_check.py` 的 `schema()`。
本文件是人读摘要；不一致时以代码为准。

## 顶层

```json
{
  "items": [Claim, ...],
  "unclaimed_sentences": ["...", "..."]
}
```

## Claim 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| id | string | ✓ | 主张编号 |
| sentence | string | ✓ | 原句（可溯源） |
| company_code | string | ✓ | 6 位代码；须能溯源到材料 |
| period_year | integer | ✓ | 期间年 |
| source_report_year | integer | ✓ | 披露年报年（重述场景可不同于 period_year） |
| metric | string | ✓ | 目录键，如 `revenue` |
| kind | string | ✓ | `amount` \| `yoy` |
| value | string | ✓ | **字符串**；拒收浮点 |
| unit | string | ✓ | 如 `元` / `亿元` / `%` |
| currency | string | ✓ | 默认 `CNY` |
| scope | string | ✓ | `consolidated` \| `parent_only` \| `unknown` |
| period_kind | string |  | `annual` \| `half` \| `quarter` |
| operator | string |  | `eq` `approx` `exceed` `at_least` `at_most` `below` |
| tolerance_pct | string |  | approx 容差 |
| multiple | string |  | 估值倍数：`PE` `PB` `EV/EBITDA` |
| claim_type | string |  | `comparison` 等扩展 |
| verification_action | string |  | `check_value` \| `needs_review` \| `forecast_marked_only` |

## 校验规则（`validate_schema`）

1. **拒收多余字段** — Schema 未声明的 key 一律失败。
2. **value 必须是字符串** — 浮点直接拒，防二进制误差。
3. **缺 sentence 不静默通过**。
4. 运算符 / kind 不在枚举内 → 该条转 `needs_review`，不抛崩整篇。

## verdict 枚举

| verdict | 来源 | 含义 |
|---|---|---|
| `evidence_supported` | compare_claim / compare_companies | 证据支持 |
| `confirmed_error` | compare_claim / compare_companies | 确认错误 |
| `needs_review` | 门控 / empty / ambiguous | 需人工 |

## 与事实的分离

- **事实**：抽取值、本地计算结果（A 栏）。
- **推论**：补证框架、原因假设（明确标注）。
- **模型解释**：语义归类、错因说明（B 栏，「未完全核实」）。
- **不生成投资观点**。
