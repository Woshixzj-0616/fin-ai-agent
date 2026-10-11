# B6 TDD 证据 · 审计、工具与报告模块拆分

> 来源施工单：[B6-审计工具与报告模块拆分.md](施工单/B6-审计工具与报告模块拆分.md)
> 日期：2026-10-11
> 结论：B6 原目标九项全部达标；`audit/` 与 `audit/tools/` 成为实现源，四个旧模块只保留直接兼容转出，裁决口径、证据要求、报告结构和旧导入行为未改。

## RED / GREEN

### RED

先建立 `agent/test_b6.py`，在 `audit/` 实现尚未迁移时运行：

```powershell
python -B -m unittest discover -s agent -p 'test_b6.py' -v
```

初始结果：

```text
Ran 12 tests
FAILED (failures=5, errors=9)
```

失败覆盖缺失的 audit 实现源、门面对象身份、工具规格同源、统一事件、报告引用和源码归档，证明测试先于实现且门不是恒真。

### GREEN

实现落点：

- `agent/audit/`：Schema、提示、客户端、接地、主张核查、报告、补充审计、服务和循环。
- `agent/audit/tools/`：规格、证据、计算、比较、检索、调度和事件。
- `agent/llm_check.py`、`agent/tools.py`、`agent/audit_checks.py`、`agent/agent_loop.py`：只保留显式 re-export。
- `agent/materials.py`：`Run.sources` 递归归档 `agent/**/*.py`。
- `agent/trace.py`、`agent/api/runtime_bridge.py`：识别 `agent_tool_start`。

最终专项结果：

```text
Ran 13 tests in 0.128s
OK
```

测试覆盖：

| # | 保证 |
| --- | --- |
| 1 | 四个旧门面不定义函数或类，历史公共/私有名逐项直接转出 |
| 2 | 门面对象身份与实现模块一致，函数 `__module__` 指向新实现源 |
| 3 | 七个业务工具在 `TOOL_NAMES`、规格和调度表中的集合完全一致 |
| 4 | `openai_tools()` 的 `submit_claims` 由 Schema 派生，不保存第二份字段副本 |
| 5 | 原生 tools 与统一规格对象内容一致 |
| 6 | JSON 与 native 循环的 `agent_tool_start` / `agent_tool` 字段集合相同 |
| 7 | 工具预算耗尽仍发出成对完成事件，状态为 `budget_exhausted` |
| 8 | 无循环、循环失败和旧导入路径保持原回退行为 |
| 9 | 报告保留 evidence ID、来源文件链接和 PDF 页码 |
| 10 | 工具裁决转检查行后仍从 facts 补回 evidence 引用 |
| 11 | `audit/` 不反向导入四个旧门面 |
| 12 | `Run.sources` 包含 `audit/**/*.py` |
| 13 | 固定工具事件样例可重复采集并保存 |

证据：

- `docs/refactor/evidence/B6/tool-contract.json`
- `docs/refactor/evidence/B6/events.json`
- `docs/refactor/evidence/B6/module-boundaries.json`
- `docs/refactor/evidence/B6/verification-summary.json`

## 反向检查

只读变异脚本：`D:\codex\tmp\b6_reverse_checks.py`
原始结果：`docs/refactor/evidence/B6/reverse-checks.json`

| 变异 | 预期失败 | 实际 |
| --- | --- | --- |
| R1 删除门面 `render_report` 转出 | 门面对象身份测试失败 | `returncode=1` |
| R2 复制第二份 OpenAI 工具规格 | 原生 tools 与统一规格不一致 | `returncode=1` |
| R3 删除 `search_text` 调度实现 | 工具名与调度集合断开 | `returncode=1` |
| R4 移除 JSON 开始事件 | 事件成对与字段一致性失败 | `returncode=1` |
| R5 移除报告来源链接 | 报告引用测试失败 | `returncode=1` |
| R6 在 audit 包反向导入 `llm_check` | 导入图边界测试失败 | `returncode=1` |
| R7 源码归档退回只扫根目录 | 缺少 `audit/` 源码归档测试失败 | `returncode=1` |

七项均按预期变红，脚本在 `finally` 中恢复原文件，`all_restored=true`。

## 回归与静态检查

| 命令 | 结果 |
| --- | --- |
| B6 专项 | `Ran 13 tests`，`OK` |
| `test.py` | `Ran 125 tests`，`OK` |
| `test_competition.py` | `Ran 51 tests`，`OK (skipped=1)` |
| `test_skill_mcp.py` | `Ran 13 tests`，`OK` |
| 全量 `test*.py` | `Ran 272 tests`，`OK (skipped=1)` |
| `python -B -m compileall -q agent scripts` | 退出码 `0` |
| `git diff --check` | 退出码 `0`，仅有既存 LF/CRLF 提示 |

B6 未执行真实模型或真实供应商调用；行为回归使用 Mock 客户端和既有真实年报测试，不把无网络测试冒充供应商端验证。

## 原目标逐条兑现表

| 原目标 | 状态 | 证据 |
| --- | --- | --- |
| `agent/audit/` 成为 Schema、客户端、核查、报告和补充审计实现源 | 达标 | 实现源模块与 `module-boundaries.json`；函数 `__module__` 指向 `audit.*` |
| `agent/audit/tools/` 成为工具规格、调度和事件实现源 | 达标 | `tool-contract.json`、`events.json` 与 13 条专项测试 |
| 四个旧模块只保留兼容转出，旧调用方无需改 import | 达标 | 全顶层 name binding、对象身份和 AST 零定义测试；R1 变红 |
| 七个工具只有一个规格定义点和唯一调度表 | 达标 | 名称集合、规格顺序、native/OpenAI 派生化测试；R2、R3 变红 |
| JSON 与 native 循环统一工具调用全过程事件 | 达标 | 开始/完成字段集合一致，预算耗尽仍成对；R4 变红 |
| 模型单次拆解、循环、回退和旧导入行为不变 | 达标 | 无循环、循环失败和全量既有 259 条回归通过 |
| 裁决等级、reason code、证据要求、Schema 和报告结构不变 | 达标 | 专项 13 条与全量 272 条通过；B6 未修改业务常量 |
| 报告与工具裁决保留 evidence ID、来源链接和页码 | 达标 | 报告引用测试和工具裁决 hydrate 测试；R5 变红 |
| `Run` 归档覆盖拆分后的 audit 源码 | 达标 | 85 个源码归档项包含 `audit/service.py`、`audit/schema.py`、`audit/tools/specs.py`；R7 变红 |

## 独立信源

独立信源：缺失。

本地 unittest、Mock 模型、真实年报回归、证据采集脚本和临时变异脚本均属于项目自证证据，不是外部 agent 复核；不冒充独立信源。

## 已知边界与待拍板

- B6 验证结构化迁移和兼容行为，没有新增真实供应商模型调用；真实模型时延与供应商错误码不属于本批验收。
- 反向检查只覆盖已识别的七个关键失败面；后续若增加新工具或改事件契约，应同步扩充专项测试。
- B6 无新增待拍板事项。
- B7 尚未开工；只有在用户另行指示且 B1-B6 证据保持有效时才进入安全加固、CI 与旧入口退役。
