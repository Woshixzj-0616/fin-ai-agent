# B2 TDD 证据

> 来源施工单：`docs/refactor/施工单/B2-Web-API-运行状态与安全边界.md`
> 目标：证明 Web API、SSE、会话隔离、上传安全和旧入口兼容按先测后码实现。

## 用户目标

1. 提交分析后立即拿到 `run_id`，长流水线在后台继续执行。
2. 浏览器可用 `Last-Event-ID` 重放事件，空闲连接有心跳，缺口不会被静默跳过。
3. 运行列表、状态、取消、追问和检索都按 `run_id + session` 隔离。
4. 伪造 `X-Forwarded-For` 不再改变默认限流身份。
5. 非法 PDF 在创建 run 前被拒绝。
6. 旧同步接口和旧流水线默认行为保持可用。

## RED 证据

初始专项测试：

```powershell
python -B -m unittest test_api -v
```

结果：

```text
ModuleNotFoundError: No module named 'api'
FAILED (errors=1)
```

失败原因：`agent/api/` 尚未实现，测试模块和测试夹具本身可正常收集。

运行时桥接 RED：

```text
RunStatus.coerce("ok")
ValueError: 'ok' is not a valid RunStatus
```

失败原因：旧流水线用 `status="ok"` 结束运行，适配器必须把它映射为 `succeeded`。原实现中的
`aliases.get(value, cls(value))` 会提前求值默认参数，即使 `"ok"` 已在别名表中也会先抛错。修复为先查
别名，再调用 `cls(value)`。

补充边界 RED：

真实 HTTP 心跳测试在客户端关闭 SSE 后触发：

```text
ConnectionAbortedError: [WinError 10053] 你的主机中的软件中止了一个已建立的连接。
```

失败原因：旧关闭处理只吞 `BrokenPipeError` 和 `ConnectionResetError`。实现改为统一捕获
`ConnectionError`，避免 Windows 客户端断开后向服务端日志写堆栈。

## GREEN 证据

B2 专项：

```powershell
python -B -m unittest discover -s agent -p 'test_api.py' -v
```

结果：

```text
Ran 17 tests in 1.050s

OK
```

B1 回归：

```powershell
python -B -m unittest discover -s agent -p 'test_runtime.py' -v
```

结果：

```text
Ran 24 tests in 0.219s

OK
```

全量回归：

```powershell
python -B -m unittest discover -s agent -p 'test*.py'
```

结果：

```text
Ran 230 tests in 10.694s

OK (skipped=1)
```

编译与静态检查：

```powershell
python -B -m compileall -q agent scripts
ruff check agent/api agent/test_api.py agent/runtime/events.py
```

结果：

```text
COMPILE_OK
All checks passed!
```

## 测试保证表

| # | 保证 | 测试 | 类型 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | `POST /api/runs` 立即返回运行标识和事件地址 | `test_create_run_returns_immediately_and_persists_events` | 集成 | PASS |
| 2 | HTTP 创建返回 `202` 并下发会话 Cookie | `ApiHttpTests.create_run` | HTTP | PASS |
| 3 | SSE 可从指定 seq 后重放 | `test_http_create_status_session_and_sse_replay` | HTTP | PASS |
| 4 | 重放位置超过最新 seq 返回 `409` | `test_replay_gap_is_rejected`、`test_http_cross_session_and_replay_gap` | 反向 | PASS |
| 5 | 非终态 SSE 连接收到真实心跳 | `test_http_sse_heartbeat_without_terminal_event` | HTTP | PASS |
| 6 | SSE 帧包含 `id/event/data` 且可解析游标 | `SseTests` | 单元 | PASS |
| 7 | run 列表和查询不跨 session | `test_session_isolation_for_run_listing_and_lookup` | 集成 | PASS |
| 8 | 取消、追问和检索不跨 session | `test_http_cross_session_and_replay_gap` | HTTP 反向 | PASS |
| 9 | 追问和检索只读目标 run 的结构化结果 | `test_ask_and_search_read_only_the_selected_run` | 集成 | PASS |
| 10 | 默认不信任客户端代理头 | `test_forwarded_for_is_ignored_without_trusted_proxy` | 安全 | PASS |
| 11 | 受信代理链从右侧解析 | `test_forwarded_for_uses_rightmost_trusted_hop` | 安全 | PASS |
| 12 | 会话 ID 不可猜且 Cookie 属性正确 | `test_session_ids_are_random_hex` | 安全 | PASS |
| 13 | 空、伪、损坏、加密和超限上传被拒 | `SecurityTests` 上传用例 | 边界 | PASS |
| 14 | 旧 `Run` 事件可映射到运行时终态 | `test_runtime_bridge_maps_legacy_events_and_finish` | 适配 | PASS |
| 15 | 旧入口与全量既有行为未回归 | 全量 230 条测试 | 回归 | PASS |

## 覆盖率

命令：

```powershell
cd agent
python -m coverage erase
python -m coverage run --source=api -m unittest test_api
python -m coverage report -m
```

结果：

```text
TOTAL 274 statements, 32 missed, 88% coverage
```

已知未覆盖：

- 默认真实流水线的少量错误分支。
- 部分 HTTP 参数校验和结果序列化防御分支。
- 运行目录清理和跨进程持久化不在 B2 范围。

## 独立信源与提交

- 独立信源：缺失。使用本地只读 unittest、coverage、Ruff、编译和 Git 差异输出作为证据。
- 尚未创建 checkpoint commit。用户未要求提交，当前改动保留在工作树。
- B2 已接线到现有 `Handler`，但旧页面仍走旧同步入口；页面迁移留给 B3/B4。
