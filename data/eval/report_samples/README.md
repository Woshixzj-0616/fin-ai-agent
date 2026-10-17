# 研报文体样例（依据公开年报数据改写 · 非券商原件）

> **来源声明**：本目录三篇为**研报文体改写**，数字取自 `data/extracted/financials.csv` 对应年报原值，
> 用于语言覆盖与端到端评测；**不是券商研报原文**，亦不用于投资建议。  
> 金标见同目录 `gold_labels.json`（程序标注，**待团队人工复签** → `data/gold/复签包.md`）。

## 怎么用

```powershell
python scripts/eval/run_report_eval.py
# 只跑一篇
python scripts/eval/run_report_eval.py --file data/eval/report_samples/S1_茅台.md
```
