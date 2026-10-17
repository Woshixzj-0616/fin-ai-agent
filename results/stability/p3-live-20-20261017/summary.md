# P3 真实长文档稳定性

- 结论：PASS
- 服务商：api.deepseek.com；请求模型：deepseek-chat；实际返回：deepseek-flash；名称别名差异：是。
- 请求轮数：3；实际完成：3。
- 输入：120 句，4923 字，4 个冻结陈述重复组。
- 分块：每块最多 20 句或 12000 字；多步循环：否。
- 跨轮一致性：1.0。

## 逐轮执行

| 轮次 | 状态 | 分块 | 已完成块 | 请求 | 已知Token | 计费Token | 耗时秒 | 失败码 |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | completed | 6 | 6 | 6 | 42663 | 42663 | 71.3888 | — |
| 2 | completed | 6 | 6 | 6 | 42668 | 42668 | 64.3431 | — |
| 3 | completed | 6 | 6 | 6 | 42655 | 42655 | 64.7319 | — |

## 通过判据

- at_least_two_runs: PASS
- multi_chunk: PASS
- all_runs_completed: PASS
- no_unfinished_sentences: PASS
- no_failed_chunks: PASS
- real_provider_requests: PASS
- known_token_usage: PASS
- observed_model_identity: PASS
- stable_model_identity: PASS
- consistent: PASS

## 边界

- 这是真实服务商调用证据，但输入由冻结陈述组成，只用于执行稳定性，不代表业务准确率或盲测。
- 结果不包含密钥；模型原文与事件保存在 `live-run-*`，本摘要只保留聚合执行事实。
- 模型身份、Token、耗时和失败码均来自实际响应与本地执行预算记录。
