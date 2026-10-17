# B4 TDD 证据 · 实时执行轨迹与结果工作区

> 来源施工单：[B4-实时执行轨迹与结果工作区.md](施工单/B4-实时执行轨迹与结果工作区.md)
> 日期：2026-10-11
> 结论：B4 原目标六项全部达标；真实年报浏览器链路、SSE 恢复、结果交互、取消和 session 隔离均有可复跑证据。

## 用户旅程

- 作为投研用户，我在 `/app/` 上传真实年报并提交后，立即得到本次 `run_id`，页面持续显示服务端 SSE 事件。
- 作为等待分析的用户，我能看到阶段、标题、状态、耗时和安全的结构化摘要，不看到 token 级思维链或供应商原始响应。
- 作为刷新页面的用户，我能按当前 `run_id` 恢复运行详情、事件轨迹、终态结果和已有证据，不从零重放或重复追加。
- 作为核查用户，我能打开证据页图，看到原 PDF 页图上的坐标框，并确认框落在图片尺寸范围内。
- 作为需要复核的用户，我能基于当前 run 追问待复核问题或检索报告文本，结果不会串到其他运行。
- 作为取消任务的用户，我能看到服务端返回的 `cancelled` 终态，取消按钮随即禁用；终态不会重新显示“等待任务”。

## RED / GREEN

### RED

原始 B4 前端契约缺口由 `agent/test_b4.py` 覆盖，专项测试先以缺口失败，再按接口补齐转绿。

SSE 恢复游标的反向门：

- 将 `web/js/stream.js` 中的 `after_seq` 恢复游标变异为固定 `0`。
- `test_stream_uses_recovery_cursor_in_request` 按预期失败：期望恢复请求携带 `after_seq=12`，实际退化为 `after_seq=0`。
- 该失败证明恢复测试不是恒真门。
- 源码已逐字节恢复；`web/js/stream.js` SHA-256 为 `f9cdd1b6f73973d334cf779e3fa37ea146ee2f5f4e31b784ee7220059f26b5c2`。

安全 payload 反向约束：

- `RuntimeEventPresentationTests.test_runtime_bridge_keeeps_structured_plan_without_raw_model_payload` 将包含 `content_preview`、`sentences`、`parsed` 的旧流水线事件送入桥接层。
- 测试要求 `model_parse` 只保留主张数、未归属句数和结构化计划，不保留完整模型原文包。

### GREEN

命令：

```powershell
python -B -m unittest discover -s agent -p 'test_b4.py' -v
```

结果：`Ran 8 tests`，`OK`。

保证项：

| # | 保证 | 测试 |
| --- | --- | --- |
| 1 | HTTP 客户端与 SSE 客户端各自只有单一传输职责，`main.js` 不直接创建它们 | `FrontendB4SourceTests.test_transport_clients_have_single_responsibility` |
| 2 | SSE 恢复请求真实携带 `after_seq` 游标 | `FrontendB4SourceTests.test_stream_uses_recovery_cursor_in_request` |
| 3 | 状态容器包含运行、事件、结果、连接、追问和检索状态 | `FrontendB4SourceTests.test_state_contract_includes_b4_interactions` |
| 4 | 视图不渲染原始模型字段 | `FrontendB4SourceTests.test_views_do_not_show_raw_model_fields` |
| 5 | 取消、提交、证据、追问和检索稳定 DOM 节点存在 | `FrontendB4SourceTests.test_b4_dom_contract_exists` |
| 6 | 结果页图补齐运行级 URL、真实页面尺寸和缩放比例 | `ResultEnrichmentTests.test_result_contains_scoped_image_urls_and_real_page_dimensions` |
| 7 | 页图接口按 run + session 隔离，越权、缺失和穿越统一 `404` | `EvidenceImageHttpTests.test_image_endpoint_is_run_and_session_scoped` |
| 8 | 旧事件桥接保留结构化计划摘要，丢弃原始模型负载 | `RuntimeEventPresentationTests.test_runtime_bridge_keeeps_structured_plan_without_raw_model_payload` |

## 接口与安全保证

### 运行级页图

```text
GET /api/runs/{run_id}/images/{name}
```

- 请求必须携带当前 session Cookie。
- 只允许直接页图文件名，不允许目录、扩展名猜测或路径穿越。
- 跨 session、缺失文件、未知扩展名和穿越均返回 `404`。
- 命中时返回 `image/png` 与 `Cache-Control: no-store`。

### 结果增强字段

`GET /api/runs/{run_id}?include_result=1` 的页图记录补齐：

```json
{
  "page_image_url": "/api/runs/{run_id}/images/{name}",
  "page_width": 595.276,
  "page_height": 841.89,
  "image_scale": 1.6
}
```

页面尺寸来自原 PDF，缺失时按页图像素与固定缩放比回算；前端只按百分比定位，不猜测页面尺寸。

### SSE 恢复与展示边界

- 首次连接和显式重连都使用 `run_id + after_seq`。
- 浏览器仍可发送 `Last-Event-ID`；恢复游标超过服务端最新 `seq` 时由 B2 返回 `409`，而不是静默跳过。
- 事件只展示基础字段、白名单 payload、结构化工具参数/摘要和 evidence ID。
- `messages`、`prompt`、`system_prompt`、`content_preview`、`raw_response`、`reasoning`、`thinking`、`chain_of_thought` 等字段不会进入前端事件 payload。
- `model_parse` 只保留主张数、未归属句子数和结构化计划字段。

## 全量回归

```powershell
python -B -m unittest discover -s agent -p 'test_b4.py' -v
python -B -m unittest discover -s agent -p 'test_api.py' -v
python -B -m unittest discover -s agent -p 'test_web_assets.py' -v
```

结果：`8 / 17 / 11` 条全部通过。

```powershell
python -B -m unittest discover -s agent -p 'test*.py'
```

结果：`Ran 249 tests in 12.340s`，`OK (skipped=1)`。

```powershell
python -B -m compileall -q agent scripts
git diff --check
```

结果：两项退出码均为 `0`。`git diff --check` 仅提示 `agent/webui.py` 的 LF/CRLF 转换警告，无空白错误。

前端模块语法：

```powershell
Get-ChildItem -Path web\js -Recurse -Filter '*.js' -File |
  ForEach-Object { node --check $_.FullName }
```

结果：`checked=8 failed=0`。

只读自证审计：

```powershell
D:\codex\协作\审计\codex_self_audit.cmd --days 7
```

原始摘要：`会话 53 | 工具调用 16524 | 扫描 443.2 MB`；`apply_patch 0 (0.0%) | FileChange 项 527 | 结构化改文件会话 9/53 (17.0%) | here-string 写文档 2253 | 记忆写入 严格 855 / 广义 857 | 严格日均 122.1`。该脚本只读，不替代 B4 的功能验收。

## 浏览器证据

只读 Playwright 探针：`D:\codex\tmp\b4_visual_check.py`

原始 JSON：`docs/refactor/evidence/B4/visual-check.json`

关键结果：

- 完成运行：`20261011_220154_19ba2576`。
- SSE：45 条，`seq=1..45`；事件列表在同一运行内单调连续。
- 结果：30 条证据；证据页图为 `953x1348` 像素，两个坐标框均在图片范围内。
- 刷新恢复：仍为 45 条事件、30 条证据，状态保持“分析完成 · 万科A”。
- 追问“有哪些待复核问题”：HTTP `200`、`status=ok`，页面显示 3 个 evidence ID。
- 检索“营业收入”：HTTP `200`、`status=ok`，页面显示 8 条命中。
- 取消运行：`20261011_220203_108129db` 服务端返回 `cancelled`，页面显示“已取消 · 万科A”，取消按钮禁用。
- 跨 session：另一浏览器上下文看不到上述运行、结果或证据。
- 桌面 `1440x900`：`scrollWidth=1440`；移动 `390x844`：`scrollWidth=390`；均无横向溢出。
- 控制台错误：`0`；`>=400` 响应：`0`。
- 请求失败仅保留 EventSource 主动关闭产生的预期 `net::ERR_ABORTED`。

截图：

- `docs/refactor/evidence/B4/desktop-completed.png`
- `docs/refactor/evidence/B4/desktop-interactions.png`
- `docs/refactor/evidence/B4/mobile-cancelled.png`

## 原目标逐条兑现表

| 原目标 | 状态 | 证据 |
| --- | --- | --- |
| 真实提交并立即返回 | 达标 | 浏览器真实上传万科A年报，创建 `20261011_220154_19ba2576`；页面显示运行卡片并在终态展示结果。 |
| SSE 阶段/轨迹逐条到达 | 达标 | 完成运行收到 45 条事件；恢复游标反向门证明 `after_seq` 不是固定值。 |
| 刷新恢复 | 达标 | `visual-check.json` 的 `reload.eventCountBefore=45` 且刷新后 `eventCount=45`；结果与证据保持。 |
| 证据页码/坐标跳转 | 达标 | 页图接口 200；`evidence.allInside=true`，坐标框在真实页图范围内。 |
| 结果、取消、追问绑定当前 run | 达标 | 追问、检索和取消均针对当前 `run_id`；另一 session 看不到运行；取消终态以服务端为准。 |
| 不泄露原始思维链 | 达标 | `test_views_do_not_show_raw_model_fields`、`test_runtime_bridge_keeeps_structured_plan_without_raw_model_payload` 均通过。 |

## 独立信源

独立信源：缺失。

本地只读 Playwright 探针和 unittest 输出属于本项目自证证据，不是外部 agent 的独立结论；不冒充独立复核。

## 已知边界

- EventSource 主动关闭时浏览器会记录一次 `net::ERR_ABORTED`；本次探针将其与真实请求失败分开记录。
- 浏览器探针使用万科A 2024 年报作为真实样例；不同 PDF 的页尺寸由后端按原文件返回，但更多公司样本仍未做 UI 端到端遍历。
- 原始 stdout 仍可能显示旧流水线的模型供应商提示；事件接口的白名单过滤保证这些内容不进入前端展示。
- B4 不重写抽取、财务计算、核查或报告算法；这些仍由既有后端流水线负责。

## 待拍板

- B4 本批无新增拍板事项。
- 下一批按重构方案进入 B5；若优先准备 10/18 初赛材料，则需用户另行拍板调整批次顺序。
