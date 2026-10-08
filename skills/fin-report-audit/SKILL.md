---
name: fin-report-audit
description: 研报纠错核查技能：对草稿中的数值主张做可核验审计。LLM 只拆句与请求工具，判定与计算全部本地 Python。输出带 evidence_id 的双轨报告（确定结论 / 模型判断 / 需人工）。
version: 1.0.0
entry: agent/agent_loop.py
tools: agent/tools.py
---

# fin-report-audit · 研报纠错核查 Skill

把「读草稿 → 拆主张 → 调证据工具 → 本地裁决 → 出双轨报告」固化为可复用技能包。

## 使命

对应赛题方向 5（研究报告纠错核查）+ 方向 1/2 的证据底座。**评委能查账**：
每条结论必须挂 `evidence_id` + 页码 + 坐标 + 文件指纹。

## 铁律（违反即技能失败）

1. **模型只拆句出 JSON**，不看年报数字、不算、不裁、不生成修改数值。
2. **判定计算全归本地 Python**（`finance.check_claim` / `tools.compare_claim`）。
3. **`compare_claim` / `compare_companies` 是唯一对错入口**，verdict 模型不得改写。
4. 工具返回 empty / ambiguous / error ⇒ 该主张只能 `needs_review`，禁止空谈结论。
5. 带 `evidence_id` 的返回才叫证据；`search_text` 命中不能单独定罪。
6. 预测句只标记不判（`forecast_marked_only` → B 栏）。
7. 事实 / 推论 / 未核实模型解释三栏分开；不生成投资观点。

## 输入 / 输出

| | |
|---|---|
| 输入 | 研报草稿（文本）、已加载年报证据 `facts`（可选文档正文 `document_texts`） |
| 输出 | `check_payload`：双轨报告 + 工具审计 + 执行轨迹 |
| 依赖 | Python 3.11+；`pymupdf`（抽证）；LLM 可选（拆句 / 工具循环） |

## 执行流程

见 [references/workflow.md](references/workflow.md)。

```
草稿
  → split_draft / LLM 拆句（claims JSON Schema）
  → 每条 claim：
       amount/yoy  → find_evidence → compare_claim
       comparison  → compare_companies
       trend       → compute_trend
       语义模糊     → model_note（B 栏）
  → render_report（A 确定 / B 模型 / C 需人工）
  → trace（全链路留痕）
```

## 工具契约

七个只读工具定义在 [agent/tools.py](../../agent/tools.py) 的 `tool_specs()`，
JSON 多步协议与 MCP 对外暴露同一套 name/parameters。
明细见 [references/tool-contract.md](references/tool-contract.md)。

| 工具 | 作用 | 能出对错？ |
|---|---|---|
| `list_catalog` | 公司/指标/单位/运算符目录 | — |
| `find_evidence` | 取唯一年报证据 | — |
| `compute_yoy` | 本地 Decimal 算同比 | — |
| `compare_claim` | 草稿数值主张 vs 证据 | **是（唯一）** |
| `compare_companies` | 跨公司同指标裁决 | **是** |
| `compute_trend` | 逐年 yoy + CAGR + 单调性 | — |
| `search_text` | 原文检索 | 否 |

## 主张 Schema

见 [references/claim-schema.md](references/claim-schema.md)。
结构化 Schema 在 `agent/llm_check.py` 的 `schema()` / `validate_schema()`。

## 与 MCP 的关系

| 形态 | 文件 | 用途 |
|---|---|---|
| Skill（流程与约束） | 本文件 + `references/` | 人和 agent 读的作业规程 |
| Tool（本地实现） | `agent/tools.py` | 证据与算术唯一出口 |
| MCP（协议暴露） | `agent/mcp_server.py` | 任一 MCP 客户端可调同一套工具 |
| 编排（多步协议） | `agent/agent_loop.py` | JSON 多步 call_tools / submit_claims |
| Prompt / Schema | `agent/llm_check.py` | 拆句与校验 |
| 日志 / 轨迹 | `agent/trace.py` + `Run` | 可复现留痕 |

MCP 启动：`python agent/mcp_server.py --facts results/evidence.json`

## 验收

```bash
python -B -m unittest discover -s agent -p 'test*.py'
```

覆盖：拆句拒收多余字段/浮点、compare_claim 单源裁决、工具预算、
MCP initialize / tools/list / tools/call、Skill 文档与 `tool_specs` 同步。
