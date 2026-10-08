# 工作流 · 研报纠错核查

## 阶段 0 · 准备

1. 加载 `facts`（年报证据列表）。来源：`agent/results/evidence.json` 或 webui / CLI 抽取产物。
2. 可选注入 `document_texts`：`{code_year: [page_text, ...]}`，供 `search_text` 使用。
3. 记入 `Run`：materials 清单、源码指纹、时间戳。

## 阶段 1 · 拆句（LLM 或确定性）

- **确定性优先**：`split_draft` 按句切开；指标用 `METRICS` 最长别名匹配。
- **LLM 补盲**：指标/年份/运算符没认出时，`LLMClient.extract` 出 claims JSON。
- **校验**：`validate_schema` 拒收多余字段、浮点 value、缺 sentence。
- 模型填不了的字段留空或标 `needs_review`，**禁止编造**。

## 阶段 2 · 工具裁决（本地 Python）

按主张类型走工具，**不许模型直接下结论**：

| claim_type | 路径 | verdict 出口 |
|---|---|---|
| amount / yoy | `find_evidence` → `compare_claim` | `compare_claim` |
| comparison | `compare_companies` | `compare_companies` |
| trend（连续增长等） | `compute_trend` | 需再走 `compare_claim` 或转 B 栏 |
| 约数 / 区间 | `compare_claim` operator=approx/exceed/... | `compare_claim` |
| 预测 / 定性 / 因果 | 不进裁决 | B 栏 `forecast_marked_only` / `model_note` |

多步协议（`agent_loop.run_loop`）：

```
{"action":"call_tools","tool_calls":[{"name":"compare_claim","arguments":{...}}]}
{"action":"submit_claims","items":[...],"unclaimed_sentences":[...]}
```

- 轮次 ≤ `MAX_ROUNDS`（5），工具调用 ≤ `MAX_TOOL_CALLS`（30）。
- 超限强制收束：未裁决主张一律 `needs_review`。

## 阶段 3 · 双轨报告

`render_report` 三栏：

| 栏 | 内容 | 谁定的 |
|---|---|---|
| A 确定结论 | 证据支持 / 确认错误 | 本地 `check_claim` / `compare_claim` |
| B 模型判断 | 语义解释、预测标记、约数归类 | LLM，标注「未完全核实」 |
| C 需人工 | empty/ambiguous/error、口径冲突、TTM 等 | 门控 |

修改建议只对 A 栏「确认错误」给出：原句 → 修改后值 → 建议句 + 公式 + evidence_id。

## 阶段 4 · 留痕

`trace.build_trace(run.events, facts)` 输出：

- 抽取通道与去重胜负
- 公式与参与的 evidence_id
- 模型拆句原始 JSON
- 工具实际参数与返回
- 门控触发原因

每条证据带：`evidence_id` / 页码 / `value_bbox` / `source_sha256`。

## 门控清单（触发即停或转人工）

| 门控 | 行为 |
|---|---|
| `unresolved_company` | 公司无法溯源到本次材料 → 停 |
| `missing_evidence` | 无唯一证据 → `needs_review` |
| `scope 未明确` | 金额类同比拦下 |
| `negative_base` / `zero_base` | 同比拒算 |
| `unit_unknown` | 不假设单位 |
| 密钥模式 `sk-...` | 拒收草稿，不落盘 |
