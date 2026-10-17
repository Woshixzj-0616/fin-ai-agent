# B1 TDD 证据

> 来源施工单：`docs/refactor/施工单/B1-运行时事件总线与会话模型.md`
> 目标：证明运行时、事件总线、会话隔离和旧 Run 适配按先测后码实现。

## 用户目标

1. 用户并发提交分析时，每个 run 的事件、输出和会话不串线。
2. 前端未来可按 `run_id` 和 `after_seq` 订阅或恢复真实事件。
3. 取消和超时后，运行终态不会被后续完成结果覆盖。
4. 旧 `Run` 默认行为不变，需要时才通过适配器镜像事件。

## RED 证据

初始专项测试命令：

```powershell
python -B -m unittest agent.test_runtime -v
```

结果：

```text
ModuleNotFoundError: No module named 'agent.runtime'
FAILED (errors=1)
```

失败原因：`agent/runtime/` 尚未实现。测试夹具、导入回退和测试语法均正常。

补充边界 RED：

```powershell
python -B -m unittest agent.test_runtime.RunManagerTests.test_non_json_result_is_summarized_without_losing_terminal_event -v
```

结果：

```text
AssertionError: 'run_started' != 'run_finished'
FAILED (failures=1)
```

失败原因：非 JSON 结果被写入终态事件时会让终态事件丢失。实现改为只把 JSON 安全摘要写入事件，完整 Python 结果仍保留在运行上下文。

## GREEN 证据

专项测试：

```powershell
python -B -m unittest agent.test_runtime -v
```

结果：

```text
Ran 24 tests in 0.240s

OK
```

全量回归：

```powershell
python -B -m unittest discover -s agent -p 'test*.py'
```

结果：

```text
Ran 213 tests in 10.732s

OK (skipped=1)
```

质量检查：

```powershell
python -B -m compileall -q agent scripts
ruff check agent/runtime agent/test_runtime.py
```

结果：

```text
All checks passed!
```

## 测试保证表

| # | 保证 | 测试 | 类型 | 结果 |
| --- | --- | --- | --- | --- |
| 1 | 事件 v1 可 JSON 往返且拒绝非法 seq | `RunEventTests` | 单元 | PASS |
| 2 | 同一 run 的 seq 从 1 递增并写入独立 JSONL | `EventBusTests.test_publish_assigns_seq_and_persists_jsonl` | 单元 | PASS |
| 3 | 新总线可从磁盘重放 | `EventBusTests.test_new_bus_replays_from_disk` | 集成 | PASS |
| 4 | 实时订阅只收到目标 run | `EventBusTests.test_subscribe_receives_live_event_and_can_replay_after_seq` | 集成 | PASS |
| 5 | 50 个并发事件序号唯一且连续 | `EventBusTests.test_concurrent_publish_has_unique_contiguous_sequence` | 并发 | PASS |
| 6 | 磁盘序号回退会被拒绝 | `EventBusTests.test_disk_sequence_regression_is_rejected` | 反向 | PASS |
| 7 | 每个 run 输出目录隔离 | `RunContextTests.test_output_is_isolated_per_run` | 集成 | PASS |
| 8 | 取消是终态，finish 不能覆盖 | `RunContextTests.test_cancelled_is_terminal_and_finish_cannot_override` | 单元 | PASS |
| 9 | session A 不能读取 session B 的 run | `RunManagerTests.test_session_isolation_and_listing` | 集成 | PASS |
| 10 | 成功、失败、取消和超时状态可查询 | `RunManagerTests` | 集成 | PASS |
| 11 | 非 JSON 运行结果不会丢失终态事件 | `test_non_json_result_is_summarized_without_losing_terminal_event` | 边界 | PASS |
| 12 | 旧 Run 默认不创建运行时镜像 | `LegacyRunAdapterTests.test_without_adapter_legacy_run_does_not_create_runtime_mirror` | 反向 | PASS |
| 13 | 适配器可镜像旧事件并可卸载 | `LegacyRunAdapterTests.test_adapter_mirrors_legacy_events_and_can_detach` | 适配 | PASS |

## 覆盖率

命令：

```powershell
python -m coverage run --source=agent/runtime -m unittest agent.test_runtime
python -m coverage report -m
```

结果：

```text
TOTAL 503 statements, 58 missed, 88% coverage
```

已知未覆盖：

- 慢订阅者队列溢出分支。
- 少量防御性错误分支。
- 会话注册表的 get/exists 便捷接口。
- 运行管理器关闭后的边界状态。

这些不是本批核心验收路径，保留给 B2 的 HTTP 断开、重连和关闭测试。

## 独立信源与提交

- 独立信源：缺失。使用只读 unittest、coverage 和 ruff 输出作为证据。
- 尚未创建 checkpoint commit。用户未要求提交，当前改动保留在工作树，便于先复核再决定提交。
- B1 未接线到 Web API 和前端，运行时包新增不改变现有用户入口。
