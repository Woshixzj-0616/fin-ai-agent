# E3–E5 语义评测与对比

- 装载证据 **80** 条；金标 `data/eval/semantic_gold.json`（程序标注，待团队复签）。
- 不调用外网 LLM：E3/E4 评的是**别名对齐 + 确定性核验管道**；真·模型拆句准确率需另配密钥。

## E3 指标语义对齐

- **22/22 = 100.00%**

| 原文 | 期望 | 预测 | ✓ |
|---|---|---|---|
| 营业收入 | revenue | revenue | ✓ |
| 营收 | revenue | revenue | ✓ |
| 营业总收入 | total_revenue | total_revenue | ✓ |
| 归母净利润 | parent_net_profit | parent_net_profit | ✓ |
| 归属于上市公司股东的净利润 | parent_net_profit | parent_net_profit | ✓ |
| 归母净利 | parent_net_profit | parent_net_profit | ✓ |
| 扣非归母净利润 | adjusted_parent_net_profit | adjusted_parent_net_profit | ✓ |
| 扣非利润 | unknown | unknown | ✓ |
| 扣非净利 | adjusted_parent_net_profit | adjusted_parent_net_profit | ✓ |
| 归属于上市公司股东的扣除非经常性损益的净利润 | adjusted_parent_net_profit | adjusted_parent_net_profit | ✓ |
| 经营现金流净额 | operating_cash_flow | operating_cash_flow | ✓ |
| 经营活动产生的现金流量净额 | operating_cash_flow | operating_cash_flow | ✓ |
| 基本每股收益 | basic_eps | basic_eps | ✓ |
| 每股收益 | basic_eps | basic_eps | ✓ |
| 加权平均净资产收益率 | weighted_roe | weighted_roe | ✓ |
| 净资产收益率 | weighted_roe | weighted_roe | ✓ |
| 总资产 | total_assets | total_assets | ✓ |
| 资产总额 | total_assets | total_assets | ✓ |
| 净利润 | ambiguous_profit | ambiguous_profit | ✓ |
| 净利 | ambiguous_profit | ambiguous_profit | ✓ |
| 毛利率 | unsupported | unknown | ✓ |
| 扣非利润 | unknown | unknown | ✓ |

## E4 研报风格句

- **12/12 = 100.00%**（含约数/预测/定性/口径陷阱）

| ID | 句子 | 金标 | 状态 | 轨 | ✓ |
|---|---|---|---|---|---|
| R01 | 2024年，贵州茅台营业收入为1500.00亿元。… | should_judge | 确认错误 | deterministic | ✓ |
| R02 | 我们预计公司2025年营业收入有望达到约1900亿元。… | forecast_mark_only | 模型判断 | model | ✓ |
| R03 | 2024年，贵州茅台营业收入约为1709亿元。… | should_judge | 证据支持 | deterministic | ✓ |
| R04 | 2024年，贵州茅台营业收入超过1800亿元。… | should_judge | 确认错误 | deterministic | ✓ |
| R05 | 2024年，贵州茅台归母净利润不低于800亿元。… | should_judge | 证据支持 | deterministic | ✓ |
| R06 | 剔除投资收益后，公司经营质量明显改善。… | qualitative | 模型判断 | model | ✓ |
| R07 | 2024年，贵州茅台扣非归母净利润为862.41亿元。… | should_judge | 证据支持 | deterministic | ✓ |
| R08 | 公司净利润创新高。… | ambiguous_metric | 模型判断 | model | ✓ |
| R09 | 2024年，贵州茅台营业收入同比增长15.71%。… | should_judge | 证据支持 | deterministic | ✓ |
| R10 | 2024年，贵州茅台营业总收入为1741.44亿元，与营业收入口径不同。… | should_judge | 证据支持 | deterministic | ✓ |
| R11 | 我们认为公司估值处于历史低位。… | qualitative | 模型判断 | model | ✓ |
| R12 | 2023年，五粮液归母净利润为999.99亿元。… | should_judge | 确认错误 | deterministic | ✓ |

## E5 口径消融：旧 vs 新

| 口径 | 可进数值裁决 | 覆盖改进 |
|---|---|---|
| 旧（仅 eq·4 指标·预测拒判） | 4/12（拒判 8） | — |
| 新（运算符·8 指标·预测标记） | 8/12（有归宿 12） | 4 → 12 条句有了明确归宿 |

### 逐句对照

| ID | 旧口径 | 新口径 |
|---|---|---|
| R01 | 可进数值裁决 | 可进数值裁决 |
| R02 | 拒判 non_historical_claim | 标记 forecast_marked_only |
| R03 | 拒判 non_exact_claim | 可进数值裁决 |
| R04 | 拒判 non_exact_claim | 可进数值裁决 |
| R05 | 拒判 non_exact_claim | 可进数值裁决 |
| R06 | 拒判 unsupported_operation | 模型判断 qualitative |
| R07 | 可进数值裁决 | 可进数值裁决 |
| R08 | 拒判 unsupported_operation | 模型判断 qualitative |
| R09 | 可进数值裁决 | 可进数值裁决 |
| R10 | 拒判 unsupported_metric | 可进数值裁决 |
| R11 | 拒判 unsupported_operation | 模型判断 qualitative |
| R12 | 可进数值裁决 | 可进数值裁决 |

## E5b 基线：自由回答 vs 证据链

| 维度 | 自由 LLM 作答（基线） | 本系统 |
|---|---|---|
| 附证据 ID | 0/12 | **8/12** |
| 可复算（确定性轨） | 0/12 | **8/12** |

> 结论：基线能说「大概对/错」，但**指不出页、算不出过程**；系统 A 栏每条确定结论都能回年报页。
真·模型拆句对比需接入在线 LLM 后补 E5c（同句喂裸模型 vs 工具循环）。
