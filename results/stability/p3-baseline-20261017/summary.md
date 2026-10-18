# P3 离线稳定性基线

- 范围：失败分类、有限重试、协议完整性、部分完成保留和密钥安全。
- 真实模型调用：0。
- 独立人工复核：无。

## 故障矩阵

| 场景 | 失败码 | 尝试数 | 结果 |
|---|---|---:|---|
| timeout | `timeout` | 1 | PASS |
| rate_limited | `rate_limited` | 2 | PASS |
| auth_failed | `auth_failed` | 1 | PASS |
| invalid_response | `invalid_response` | 1 | PASS |
| truncated_response | `response_truncated` | 0 | PASS |
| model_identity_changed | `model_identity_changed` | 0 | PASS |
| partial_success | `upstream_unavailable` | 2 | PASS |
| secret_safety | `secret_blocked` | 1 | PASS |

## 结论

- 场景：8；通过：8。
- 总体：PASS。

## 边界

- 本基线用本地 MockTransport 和内存注入，不证明真实网络、真实服务商限流或真实模型质量。
- 超时、429、401、无效 JSON、截断 SSE、模型身份变化、部分成功和密钥泄露均有显式断言。
- 中断恢复、取消和工具失败由既有全量回归共同覆盖，本表不重复折算成模型准确率。
