# B2 · Web API、运行状态与安全边界

## 批次信息

- 状态：已收工
- 前置批次：B1 已收工
- 开始日期：2026-10-11
- 预计结束条件：新 API 与 SSE 专项测试通过，旧入口兼容回归通过，上传和会话越权负样本按预期失败
- 负责人：Codex 与用户共同核对

## 原目标

1. `POST /api/runs` 创建异步运行后立即返回 `run_id`、状态和事件地址。
2. `GET /api/runs/{run_id}/events` 提供 SSE，支持 `Last-Event-ID`、断点重放和服务端心跳。
3. `GET /api/runs/{run_id}`、运行列表、取消、追问和检索全部按 `run_id + session` 隔离。
4. 限流不再直接信任单个 `X-Forwarded-For`。
5. 上传统一检查请求体上限、PDF 魔数、加密和可解析性。
6. 旧 `/api/prefill`、`/api/analyze`、`/api/announcement`、`/api/ask`、`/api/search` 保持兼容。

## 用户可观察结果

- 新客户端提交后立即拿到 `run_id`，不必等整条流水线结束。
- 浏览器可按 `run_id` 接收真实事件，网络恢复后从 `Last-Event-ID` 继续。
- 两个会话即使同时分析，也不能读取或追问对方的运行。
- 伪造的 `X-Forwarded-For` 左侧地址不能绕开限流。
- 非 PDF、空文件、加密 PDF 和超限上传在进入流水线前被拒绝。

本批不迁移现有页面，因此旧页面仍走旧同步入口，不应感受到行为回归。

## 范围

计划新增：

- `agent/api/__init__.py`
- `agent/api/routes.py`
- `agent/api/runtime_bridge.py`
- `agent/api/security.py`
- `agent/api/sse.py`
- `agent/test_api.py`
- `docs/refactor/施工单/B2-Web-API-运行状态与安全边界.md`

计划修改：

- `agent/webui.py`：新增新路由适配，保留旧路由；让现有流水线可选接收运行时上下文。
- `agent/materials.py`：不改公开 API；仅在测试证明必要时做最小兼容修复。

计划删除：无。

保持兼容：

- 旧页面 `PAGE`、`/api/prefill`、`/api/analyze`、`/api/announcement`、`/api/ask`、`/api/search` 继续存在。
- CLI、MCP 和现有 `Run`、`run_pipeline`、`run_announcement_pipeline` 的默认调用行为不变。
- 旧页面不要求理解 SSE，也不要求使用新会话 Cookie。

## 非目标

- 不迁移页面到 `web/`。
- 不实现 B3 的工作台壳和 B4 的前端事件渲染。
- 不拆分 `extract.py`、`llm_check.py`、`tools.py`。
- 不删除 `LAST_FACTS`、`LAST_DOCUMENT_TEXTS`；旧接口继续使用，新接口只读 run 自己的结果。
- 不实现运行过期清理、跨进程持久化、账号体系或多租户后台。
- 不改变财务算法、证据口径和模型裁决边界。

## 接口与契约

### 新接口

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| `POST` | `/api/runs` | `202`，返回 `run_id`、`status`、`events_url`、`poll_url` |
| `GET` | `/api/runs/{run_id}` | 返回状态、阶段、时间和结果摘要；`include_result=1` 时附带完整结果 |
| `GET` | `/api/runs/{run_id}/events` | `text/event-stream`，支持 `Last-Event-ID` |
| `POST` | `/api/runs/{run_id}/cancel` | 返回取消请求后的实际状态 |
| `GET` | `/api/runs` | 只返回当前 session 的运行 |
| `POST` | `/api/runs/{run_id}/ask` | 只使用该 run 的 facts |
| `POST` | `/api/runs/{run_id}/search` | 只使用该 run 的页文本 |

### 会话与 Cookie

- Cookie 名：`fin_session`。
- 服务端生成不可猜的随机十六进制 ID。
- Cookie 使用 `HttpOnly`、`SameSite=Lax`、`Path=/`；HTTPS 请求增加 `Secure`。
- 不存在、越权和不存在 run 对外统一为 `404 RUN_NOT_FOUND`，不泄露其他会话是否存在。

### 事件与 SSE

- 沿用 B1 RunEvent v1，不在 B2 改字段。
- SSE 帧使用 `id: {seq}`、`event: {event_type}`、`data: {json}`。
- `Last-Event-ID` 必须是非负整数；大于服务端最新 `seq` 时返回 `409 EVENT_REPLAY_GAP`。
- 终态事件发出后关闭 SSE；空闲连接由服务端心跳保活。

### 安全边界

- 默认不信任 `X-Forwarded-For`。
- 仅当配置 `TRUSTED_PROXY_HOPS>=1` 时，从右侧按受信跳数取客户端地址。
- 上传默认上限 40 MiB；必须同时满足 `%PDF-` 魔数、非空、可打开、非加密。
- 对外错误结构为 `{"error":{"code","message","retryable"}}`，不返回堆栈和本地路径。

## 实施清单

- [x] 写 `agent/test_api.py`，覆盖 API、SSE、会话、安全、旧入口兼容。
- [x] 运行专项测试，确认因 `agent/api/` 尚不存在而 RED。
- [x] 实现安全、SSE 编码和运行时桥接模块。
- [x] 实现 `ApiApplication` 新接口服务并接入 `Handler`。
- [x] 给现有流水线增加可选运行时上下文，默认调用不变。
- [x] 运行专项测试转 GREEN。
- [x] 运行 B1 专项、全量测试、编译和范围检查。
- [x] 填 B2 收工证据并更新施工单索引和项目记忆。

## 验收

### A1：B2 专项测试

```powershell
python -B -m unittest discover -s agent -p 'test_api.py' -v
```

预期：API、SSE、会话、安全和运行时桥接专项全部通过。

### A2：B1 回归

```powershell
python -B -m unittest agent.test_runtime -v
```

预期：B1 的 24 条测试保持通过。

### A3：全量测试

```powershell
python -B -m unittest discover -s agent -p 'test*.py'
```

预期：B1 后 230 条测试通过，原有 1 条仍按设计跳过。

### A4：编译和格式

```powershell
python -B -m compileall -q agent scripts
git diff --check
git status --short --branch
```

预期：编译退出码 0；无格式错误；改动只落在 B2 施工单、`agent/api/`、`agent/test_api.py`、`agent/webui.py` 和必要的兼容修复。

### A5：旧入口兼容

运行既有 WebUI、竞争回归和全量测试，验证：

- `PAGE` 和旧接口字符串仍存在。
- `run_pipeline`、`run_announcement_pipeline` 默认调用行为不变。
- 旧 `/api/ask`、`/api/search` 仍可使用进程级最近结果。

### A6：SSE 断点重放

用同一 run 的连续事件验证：

- `Last-Event-ID: n` 只收到 `seq > n`。
- 终态事件到达后连接关闭。
- 事件 JSON 可由 `RunEvent.from_dict` 还原。

## 反向检查

### R1：伪造代理头必须不能改身份

- 失败注入：配置 1 个受信代理，构造 `X-Forwarded-For: 1.2.3.4, 203.0.113.9`。
- 期望结果：取右侧受信链得到的 `203.0.113.9`，不取攻击者可控的最左侧地址。
- 若返回 `1.2.3.4`，说明限流地址门无效。

### R2：跨会话读取必须不可见

- 失败注入：session A 创建 run，使用 session B 查询、取消、追问和检索。
- 期望结果：统一为 `RunNotVisible` / `404 RUN_NOT_FOUND`。
- 若 session B 能看到或用该 run，说明会话隔离门无效。

### R3：非法上传必须在入口失败

- 失败注入：空字节、`not-a-pdf`、超过上限的伪 PDF、加密 PDF。
- 期望结果：均以 `400 INVALID_UPLOAD` 在创建运行前拒绝。
- 若创建出 run，说明上传边界无效。

### R4：SSE 缺口必须被发现

- 失败注入：请求 `Last-Event-ID` 大于当前最新 `seq`。
- 期望结果：`409 EVENT_REPLAY_GAP`。
- 若静默等待下一事件，说明重放缺口门无效。

### R5：旧入口关键契约必须仍在

- 失败注入：检查旧接口字符串和默认流水线签名。
- 期望结果：旧专项和全量测试通过。
- 若旧入口消失或默认签名改变，说明兼容线失效。

## 回滚

- 触发条件：新 API 导致旧入口回归、SSE 阻塞服务器、会话隔离失败或竞赛演示入口不可用。
- 回滚方式：移除 `/api/runs` 路由接线并停止使用 `agent/api/`；旧 `Handler` 分支保持原位。
- 回滚后验证：B1 的 24 条测试和全量既有 213 条测试仍通过，旧页面可正常提交分析。

## 收工证据

| 原目标 | 状态 | 证据入口 | 说明 |
| --- | --- | --- | --- |
| 异步创建运行 | 达标 | `test_create_run_returns_immediately_and_persists_events`、HTTP `202` 用例 | 创建后立即返回 `run_id`、状态、`events_url` 和 `poll_url`，后台线程执行流水线 |
| SSE 与断点重放 | 达标 | `test_http_create_status_session_and_sse_replay`、`test_replay_gap_is_rejected`、`test_http_sse_heartbeat_without_terminal_event` | `Last-Event-ID` 重放、缺口 `409`、终态关闭和真实心跳均已验证 |
| run + session 隔离 | 达标 | `test_session_isolation_for_run_listing_and_lookup`、`test_http_cross_session_and_replay_gap` | 列表、查询、取消、追问、检索统一不泄露其他 session 的 run |
| 代理限流修复 | 达标 | `SecurityTests.test_forwarded_for_is_ignored_without_trusted_proxy`、`test_forwarded_for_uses_rightmost_trusted_hop` | 默认不信任 `X-Forwarded-For`；启用受信跳数后从右侧解析 |
| 上传统一验证 | 达标 | `test_validate_pdf_upload_accepts_real_pdf`、`test_validate_pdf_upload_rejects_bad_inputs`、`test_validate_pdf_upload_rejects_oversize_without_allocating` | 空文件、非 PDF、损坏 PDF、加密 PDF 和超限文件均在创建 run 前拒绝 |
| 旧接口兼容 | 达标 | 全量 230 条测试、`run_pipeline` 默认参数保持、旧路由字符串检查 | 旧 `/api/prefill`、`/api/analyze`、`/api/announcement`、`/api/ask`、`/api/search` 保留 |

- 验收命令：`python -B -m unittest discover -s agent -p 'test_api.py' -v`；`python -B -m unittest discover -s agent -p 'test_runtime.py' -v`；`python -B -m unittest discover -s agent -p 'test*.py'`；`python -B -m compileall -q agent scripts`；`ruff check agent/api agent/test_api.py agent/runtime/events.py`；`python -m coverage run --source=api -m unittest test_api`（在 `agent/` 下执行）。
- 测试摘要：B2 17/17、B1 24/24、全量 230 条通过且 1 条按设计跳过；`COMPILE_OK`；Ruff 指定范围全部通过；`agent/api` 覆盖率 88%（274 statements / 32 missed）。
- 独立信源：缺失。当前证据均为本地只读 unittest、coverage、Ruff、编译和 Git 差异输出；没有外部 agent 直接写入的独立结论。
- 未达标项：无。B2 原目标六项均达标；真实年报前端端到端演示仍按计划留待 B4。
- 待拍板：无。
- 下一批前置条件：B3 开工前先出“独立前端资产与工作台壳”施工单；不迁移旧页面，先冻结静态资源边界。

## B2 自审

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 是否先出施工单再动码 | 达标 | B2 施工单先冻结，专项 RED 为 `ModuleNotFoundError: No module named 'api'` |
| 是否只改 B2 范围 | 达标 | `agent/api/`、`agent/test_api.py`、`agent/webui.py` 和必要的运行时兼容修复 |
| 旧入口是否保持 | 达标 | 全量 230 条通过；旧接口字符串仍在，默认参数未删 |
| 反向检查是否成功变红 | 达标 | R1-R5 均有负样本断言；重放缺口、越权、非法上传按预期失败 |
| 独立信源是否如实记录 | 达标 | 已明确写“独立信源：缺失”，未把本地测试冒充外部复核 |
