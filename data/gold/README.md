# data/gold · 标准答案状态

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `data/agent/samples.json` → `reference_gold` | 24 条 · **待团队人工复签** | 茅台 2024 可视参考；程序校验 PASS，人工签字前不作正式答案 |
| `results/eval/eval_claims_used.json` | 144 条 · **程序生成 gold** | 由本次抽取证据构造的准确/注错陈述；`gold_expect` 为程序期望 |
| `data/eval/drafts/*` | 24 份 NL 草稿 | 准确稿 + 含错误稿，供 `check-text` / 演示 |

## 口径

- 评测 gold = **证据值上的受控扰动**，测的是确定性核查器（`finance.check_claim`），不是抽取器。
- 结果见 `results/eval/eval_report.md`（当前 P=R=100%，144 项）。
- **升格为正式参考答案前，须团队人工复签**（程序不能代签）。
