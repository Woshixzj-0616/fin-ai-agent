# 研报文体样例端到端（P0 · 走完整 check_text）

- 样例 **2** 篇 · 原子陈述 **18** 条
- 确定性判定 **13/13**（精确率 1.0）· 应判覆盖 **13/13**（召回 1.0）
- 合理弃答 0/0 · 漏拆 0 · 超时 0 · 失败 0 · 模型轨 5
- 引用覆盖（带 evidence_id）：13/18

| 样例 | ID | 金标 | 实际 | 轨道 | 结局 | 证据 | ✓ |
|---|---|---|---|---|---|---|---|
| S1 | S1-C1 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C2 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C3 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C4 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C5 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C6 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C7 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S1 | S1-C8 | 模型判断 | 模型判断 | model | model_track | — | ✓ |
| S1 | S1-C9 | 模型判断 | 模型判断 | model | model_track | — | ✓ |
| S2 | S2-C1 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S2 | S2-C2 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S2 | S2-C3 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S2 | S2-C4 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S2 | S2-C5 | 模型判断 | 模型判断 | model | model_track | — | ✓ |
| S2 | S2-C6 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S2 | S2-C7 | 证据支持 | 证据支持 | deterministic | judged | ✓ | ✓ |
| S2 | S2-C8 | 模型判断 | 模型判断 | model | model_track | — | ✓ |
| S2 | S2-C9 | 模型判断 | 模型判断 | model | model_track | — | ✓ |

口径：文本为研报文体改写（非券商原件）；数字以年报抽取证据为准。
本表为功能回归：使用固定协议样例回放，**不含真实模型请求**，因此不能当作业务准确率。
