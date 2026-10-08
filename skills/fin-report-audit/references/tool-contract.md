# 工具契约

与 `agent/tools.py` 的 `tool_specs()` 保持同一套 name / parameters。
MCP `tools/list` 直接读取该函数，避免两套定义漂移。

## 公共约束

- 全部**只读**：不写库、不发网络、不改证据。
- **带 `evidence_id` 的返回才叫证据**。
- 未知工具 / 参数错误一律 `{"status":"error",...}`，不抛栈给模型。
- 空结果如实返回 `empty` / `ambiguous`，**禁止自行选一条**。

## list_catalog(kind)

`kind ∈ {companies, metrics, units, operators, verdicts}`

返回本次目录：公司名与别名、指标键与别名、单位、运算符、verdict 枚举。

## find_evidence(company_name_or_code, metric, period_year[, source_report_year])

返回 0 / 1 / N 条证据摘要：

```json
{"status":"ok","items":[{"evidence_id":"...","company_code":"600519","metric":"revenue","period_year":2024,"value":"...","unit":"元","page":58,"value_bbox":[...]}]}
```

`status`: `ok` | `empty` | `ambiguous` | `error`

## compute_yoy(company_name_or_code, metric, year)

本地 Decimal 算同比。返回 `{status, yoy, current_evidence_id, previous_evidence_id}`。
`yoy` 内含 `formula` 与 operands 的 evidence_id。

## compare_claim(...) · 唯一裁决口

| 参数 | 说明 |
|---|---|
| company_name_or_code / metric / period_year | 定位证据 |
| kind | `amount` \| `yoy` |
| claimed_value / claimed_unit | 草稿主张 |
| operator | `eq` `approx` `exceed` `at_least` `at_most` `below` |
| tolerance_pct | approx 自定义容差（默认 2%，上限 5%） |
| direction | yoy 方向；`down` 时正数写成负号 |
| scope | 默认 `consolidated` |
| source_report_year | 缺省 = period_year |

返回：

```json
{"verdict":"evidence_supported|confirmed_error|needs_review","status":"证据支持|确认错误|...","evidence_ids":[...],"calculation":{...},"expected":"..."}
```

**verdict 只能出自本工具，模型不得改写。**

## compare_companies(company_a, company_b, metric, period_year, operator)

`operator ∈ {exceed, at_least, at_most, below, eq}`
`verdict ∈ {evidence_supported, confirmed_error}`

跨公司同指标同年比较，归一后本地 Decimal 裁决。

## compute_trend(company_name_or_code, metric, years)

逐年 yoy + CAGR + 单调性。覆盖「连续三年增长」类主张的证据整理；
**趋势本身不产生对错**，需再走 `compare_claim` 或转 B 栏。

## search_text(company_name_or_code, source_report_year, query)

检索 `document_texts[code_year]`。返回 page + snippet。
**命中不能单独作为数值裁决依据。**
