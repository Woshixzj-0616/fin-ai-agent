# 草稿核查报告（双轨）

运行：20261017_233111_184365fe；结构化录入模型：deepseek-chat。

**A 栏 = 代码裁定（可复算）**：对错、正确值、计算式全部由本地 Python 生成。

**B 栏 = 模型判断（未完全核实）**：语义解释，仅供参考，不作为对错结论。

核查上下文：期间、币种、口径与来源须可确认；营业收入与经营现金流按已记录的合并口径。

## A. 确定结论（代码裁定）

| ID | 主张 | 裁决 | 原因或建议 | 证据与页码 |
|---|---|---|---|---|
| B1_C1 | 中国移动2025年营业收入为10,501.87亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B1_C2 | 中国移动2025年归属于母公司股东的净利润为1,370.95亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B1_C3 | 紫金矿业2025年营业收入为3,490.79亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B1_C4 | 紫金矿业2025年归属于上市公司股东的净利润为517.77亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B1_C5 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B1_C6 | 中国移动2026年第一季度归属于母公司股东的净利润为293亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B1_C7 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B1_C8 | 紫金矿业2026年上半年归属于上市公司股东的净利润为391.70亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B1_C10 | 紫金矿业2025年经营活动产生的现金流量净额为754.30亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-OCF](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B1_C11 | 中国移动2025年营业收入为10,800亿元。 | 确认错误 | 将该核查项改为 10502亿元，并引用所列年报页码。 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B1_C12 | 中国移动2025年归属于母公司股东的净利润为1,400亿元。 | 确认错误 | 将该核查项改为 1371亿元，并引用所列年报页码。 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B1_C13 | 中国移动2026年第一季度营业收入为2,500亿元。 | 确认错误 | 将该核查项改为 2665亿元，并引用所列年报页码。 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B1_C14 | 中国移动2026年第一季度归属于母公司股东的净利润为306亿元。 | 确认错误 | 将该核查项改为 293亿元，并引用所列年报页码。 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B1_C15 | 紫金矿业2025年营业收入为3,036.40亿元。 | 确认错误 | 将该核查项改为 3490.79亿元，并引用所列年报页码。 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B1_C16 | 紫金矿业2025年归属于上市公司股东的净利润为320.51亿元。 | 确认错误 | 将该核查项改为 517.77亿元，并引用所列年报页码。 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B1_C17 | 紫金矿业2026年上半年营业收入为1,677.11亿元。 | 确认错误 | 将该核查项改为 1941.78亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B1_C18 | 紫金矿业2026年上半年归属于上市公司股东的净利润为232.92亿元。 | 确认错误 | 将该核查项改为 391.70亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B2_C1 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B2_C2 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B2_C11 | 中国移动2025年营业收入为10,501.87亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B2_C12 | 中国移动2025年归属于母公司股东的净利润为1,370.95亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B2_C13 | 紫金矿业2025年营业收入为3,490.79亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B2_C14 | 紫金矿业2025年归属于上市公司股东的净利润为517.77亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B2_C15 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B2_C16 | 中国移动2026年第一季度归属于母公司股东的净利润为293亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B2_C17 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B2_C18 | 紫金矿业2026年上半年归属于上市公司股东的净利润为391.70亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B2_C20 | 紫金矿业2025年经营活动产生的现金流量净额为754.30亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-OCF](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B3_C1 | 中国移动2025年营业收入为10,800亿元。 | 确认错误 | 将该核查项改为 10502亿元，并引用所列年报页码。 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B3_C2 | 中国移动2025年归属于母公司股东的净利润为1,400亿元。 | 确认错误 | 将该核查项改为 1371亿元，并引用所列年报页码。 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B3_C3 | 中国移动2026年第一季度营业收入为2,500亿元。 | 确认错误 | 将该核查项改为 2665亿元，并引用所列年报页码。 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B3_C4 | 中国移动2026年第一季度归属于母公司股东的净利润为306亿元。 | 确认错误 | 将该核查项改为 293亿元，并引用所列年报页码。 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B3_C5 | 紫金矿业2025年营业收入为3,036.40亿元。 | 确认错误 | 将该核查项改为 3490.79亿元，并引用所列年报页码。 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B3_C6 | 紫金矿业2025年归属于上市公司股东的净利润为320.51亿元。 | 确认错误 | 将该核查项改为 517.77亿元，并引用所列年报页码。 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B3_C7 | 紫金矿业2026年上半年营业收入为1,677.11亿元。 | 确认错误 | 将该核查项改为 1941.78亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B3_C8 | 紫金矿业2026年上半年归属于上市公司股东的净利润为232.92亿元。 | 确认错误 | 将该核查项改为 391.70亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B3_C11 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B3_C12 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B4_C1 | 中国移动2025年营业收入为10,501.87亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B4_C2 | 中国移动2025年归属于母公司股东的净利润为1,370.95亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B4_C3 | 紫金矿业2025年营业收入为3,490.79亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B4_C4 | 紫金矿业2025年归属于上市公司股东的净利润为517.77亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B4_C5 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B4_C6 | 中国移动2026年第一季度归属于母公司股东的净利润为293亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B4_C7 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B4_C8 | 紫金矿业2026年上半年归属于上市公司股东的净利润为391.70亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B4_C10 | 紫金矿业2025年经营活动产生的现金流量净额为754.30亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-OCF](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B4_C11 | 中国移动2025年营业收入为10,800亿元。 | 确认错误 | 将该核查项改为 10502亿元，并引用所列年报页码。 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B4_C12 | 中国移动2025年归属于母公司股东的净利润为1,400亿元。 | 确认错误 | 将该核查项改为 1371亿元，并引用所列年报页码。 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B4_C13 | 中国移动2026年第一季度营业收入为2,500亿元。 | 确认错误 | 将该核查项改为 2665亿元，并引用所列年报页码。 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B4_C14 | 中国移动2026年第一季度归属于母公司股东的净利润为306亿元。 | 确认错误 | 将该核查项改为 293亿元，并引用所列年报页码。 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B4_C15 | 紫金矿业2025年营业收入为3,036.40亿元。 | 确认错误 | 将该核查项改为 3490.79亿元，并引用所列年报页码。 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B4_C16 | 紫金矿业2025年归属于上市公司股东的净利润为320.51亿元。 | 确认错误 | 将该核查项改为 517.77亿元，并引用所列年报页码。 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B4_C17 | 紫金矿业2026年上半年营业收入为1,677.11亿元。 | 确认错误 | 将该核查项改为 1941.78亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B4_C18 | 紫金矿业2026年上半年归属于上市公司股东的净利润为232.92亿元。 | 确认错误 | 将该核查项改为 391.70亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B5_C1 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B5_C2 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B5_C11 | 中国移动2025年营业收入为10,501.87亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B5_C12 | 中国移动2025年归属于母公司股东的净利润为1,370.95亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B5_C13 | 紫金矿业2025年营业收入为3,490.79亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B5_C14 | 紫金矿业2025年归属于上市公司股东的净利润为517.77亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B5_C15 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B5_C16 | 中国移动2026年第一季度归属于母公司股东的净利润为293亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B5_C17 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B5_C18 | 紫金矿业2026年上半年归属于上市公司股东的净利润为391.70亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B5_C20 | 紫金矿业2025年经营活动产生的现金流量净额为754.30亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2025-OCF](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B6_C1 | 中国移动2025年营业收入为10,800亿元。 | 确认错误 | 将该核查项改为 10502亿元，并引用所列年报页码。 | [HO-CM-2025-REV](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B6_C2 | 中国移动2025年归属于母公司股东的净利润为1,400亿元。 | 确认错误 | 将该核查项改为 1371亿元，并引用所列年报页码。 | [HO-CM-2025-PNP](../data/holdout/materials/600941-2025-annual-summary.pdf)（PDF第7页） |
| B6_C3 | 中国移动2026年第一季度营业收入为2,500亿元。 | 确认错误 | 将该核查项改为 2665亿元，并引用所列年报页码。 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B6_C4 | 中国移动2026年第一季度归属于母公司股东的净利润为306亿元。 | 确认错误 | 将该核查项改为 293亿元，并引用所列年报页码。 | [HO-CM-2026Q1-PNP](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B6_C5 | 紫金矿业2025年营业收入为3,036.40亿元。 | 确认错误 | 将该核查项改为 3490.79亿元，并引用所列年报页码。 | [HO-ZJ-2025-REV](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B6_C6 | 紫金矿业2025年归属于上市公司股东的净利润为320.51亿元。 | 确认错误 | 将该核查项改为 517.77亿元，并引用所列年报页码。 | [HO-ZJ-2025-PNP](../data/holdout/materials/601899-2025-annual-summary.pdf)（PDF第4页） |
| B6_C7 | 紫金矿业2026年上半年营业收入为1,677.11亿元。 | 确认错误 | 将该核查项改为 1941.78亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B6_C8 | 紫金矿业2026年上半年归属于上市公司股东的净利润为232.92亿元。 | 确认错误 | 将该核查项改为 391.70亿元，并引用所列年报页码。 | [HO-ZJ-2026H1-PNP](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
| B6_C11 | 中国移动2026年第一季度营业收入为2,665亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-CM-2026Q1-REV](../data/holdout/materials/600941-2026-q1.pdf)（PDF第2页） |
| B6_C12 | 紫金矿业2026年上半年营业收入为1,941.78亿元。 | 证据支持 | 单位、口径和陈述精度下与原文证据一致 | [HO-ZJ-2026H1-REV](../data/holdout/materials/601899-2026-interim-summary.pdf)（PDF第2页） |
- B1_C11 替换：10800亿元 → 10502亿元。
- B1_C12 替换：1400亿元 → 1371亿元。
- B1_C13 替换：2500亿元 → 2665亿元。
- B1_C14 替换：306亿元 → 293亿元。
- B1_C15 替换：3036.40亿元 → 3490.79亿元。
- B1_C16 替换：320.51亿元 → 517.77亿元。
- B1_C17 替换：1677.11亿元 → 1941.78亿元。
- B1_C18 替换：232.92亿元 → 391.70亿元。
- B3_C1 替换：10800亿元 → 10502亿元。
- B3_C2 替换：1400亿元 → 1371亿元。
- B3_C3 替换：2500亿元 → 2665亿元。
- B3_C4 替换：306亿元 → 293亿元。
- B3_C5 替换：3036.40亿元 → 3490.79亿元。
- B3_C6 替换：320.51亿元 → 517.77亿元。
- B3_C7 替换：1677.11亿元 → 1941.78亿元。
- B3_C8 替换：232.92亿元 → 391.70亿元。
- B4_C11 替换：10800亿元 → 10502亿元。
- B4_C12 替换：1400亿元 → 1371亿元。
- B4_C13 替换：2500亿元 → 2665亿元。
- B4_C14 替换：306亿元 → 293亿元。
- B4_C15 替换：3036.40亿元 → 3490.79亿元。
- B4_C16 替换：320.51亿元 → 517.77亿元。
- B4_C17 替换：1677.11亿元 → 1941.78亿元。
- B4_C18 替换：232.92亿元 → 391.70亿元。
- B6_C1 替换：10800亿元 → 10502亿元。
- B6_C2 替换：1400亿元 → 1371亿元。
- B6_C3 替换：2500亿元 → 2665亿元。
- B6_C4 替换：306亿元 → 293亿元。
- B6_C5 替换：3036.40亿元 → 3490.79亿元。
- B6_C6 替换：320.51亿元 → 517.77亿元。
- B6_C7 替换：1677.11亿元 → 1941.78亿元。
- B6_C8 替换：232.92亿元 → 391.70亿元。

## B. 模型判断（未完全核实 · 仅供参考）

| ID | 主张 | 解释 | 依据原文 | 状态 |
|---|---|---|---|---|
| B2_C7 | 预计中国移动2027年营业收入将继续增长。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 预计中国移动2027年营业收入将继续增长。 | 模型判断 |
| B2_C8 | 紫金矿业铜业务仍是未来增长的重要来源。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 紫金矿业铜业务仍是公司未来增长的重要来源。 | 模型判断 |
| B2_C9 | 中国移动2026年第一季度经营表现整体稳健。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 中国移动2026年第一季度经营表现整体稳健。 | 模型判断 |
| B2_C10 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 模型判断 |
| B3_C17 | 预计中国移动2027年营业收入将继续增长。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 预计中国移动2027年营业收入将继续增长。 | 模型判断 |
| B3_C18 | 紫金矿业铜业务仍是未来增长的重要来源。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 紫金矿业铜业务仍是公司未来增长的重要来源。 | 模型判断 |
| B3_C19 | 中国移动2026年第一季度经营表现整体稳健。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 中国移动2026年第一季度经营表现整体稳健。 | 模型判断 |
| B3_C20 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 模型判断 |
| B5_C7 | 预计中国移动2027年营业收入将继续增长。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 预计中国移动2027年营业收入将继续增长。 | 模型判断 |
| B5_C8 | 紫金矿业铜业务仍是未来增长的重要来源。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 紫金矿业铜业务仍是公司未来增长的重要来源。 | 模型判断 |
| B5_C9 | 中国移动2026年第一季度经营表现整体稳健。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 中国移动2026年第一季度经营表现整体稳健。 | 模型判断 |
| B5_C10 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 模型判断 |
| B6_C17 | 预计中国移动2027年营业收入将继续增长。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 预计中国移动2027年营业收入将继续增长。 | 模型判断 |
| B6_C18 | 紫金矿业称铜业务仍是未来增长的重要来源。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 紫金矿业铜业务仍是公司未来增长的重要来源。 | 模型判断 |
| B6_C19 | 中国移动2026年第一季度经营表现整体稳健。 | 跨公司/跨库比较：未提取到双侧公司与方向，或超出已加载证据，仅作模型判断保留 | 中国移动2026年第一季度经营表现整体稳健。 | 模型判断 |
| B6_C20 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 预测/目标类陈述：已标记，不拿历史年报数值证伪；请结合业绩预告等另行核实 | 若铜价维持高位，紫金矿业盈利能力可能继续改善。 | 模型判断 |

## C. 需人工 / 证据不足

| ID | 原因码 | 解析状态 | 候选/冲突 | 下一步动作 | 说明 |
|---|---|---|---|---|---|
| B1_C9 | unsupported_period | period_conflict | 600941:中国移动 period_start | 补全季度/半年的明确起止后重跑，不退回全年值 | 季度/半年期间没有明确起止，不能与全年值混比 |
| B1_C19 | model_parse_conflict | scope_conflict | 600941:中国移动 scope | 按原文归母/合并/母公司口径修正后重跑，或转人工复核 | 模型统计口径与原文的归母/合并/母公司表达不一致 |
| B1_C20 | deterministic_check | unique | 601899:紫金矿业 | — | scope 不明确或不一致 |
| B2_C3 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B2_C4 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B2_C5 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B2_C6 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B2_C19 | unsupported_period | period_conflict | 600941:中国移动 period_start | 补全季度/半年的明确起止后重跑，不退回全年值 | 季度/半年期间没有明确起止，不能与全年值混比 |
| B3_C9 | model_parse_conflict | scope_conflict | 600941:中国移动 scope | 按原文归母/合并/母公司口径修正后重跑，或转人工复核 | 模型统计口径与原文的归母/合并/母公司表达不一致 |
| B3_C10 | deterministic_check | unique | 601899:紫金矿业 | — | scope 不明确或不一致 |
| B3_C13 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B3_C14 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B3_C15 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B3_C16 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B4_C9 | unsupported_period | period_conflict | 600941:中国移动 period_start | 补全季度/半年的明确起止后重跑，不退回全年值 | 季度/半年期间没有明确起止，不能与全年值混比 |
| B4_C19 | model_parse_conflict | scope_conflict | 600941:中国移动 scope | 按原文归母/合并/母公司口径修正后重跑，或转人工复核 | 模型统计口径与原文的归母/合并/母公司表达不一致 |
| B4_C20 | deterministic_check | unique | 601899:紫金矿业 | — | scope 不明确或不一致 |
| B5_C3 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B5_C4 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B5_C5 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B5_C6 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B5_C19 | unsupported_period | period_conflict | 600941:中国移动 period_start | 补全季度/半年的明确起止后重跑，不退回全年值 | 季度/半年期间没有明确起止，不能与全年值混比 |
| B6_C9 | model_parse_conflict | scope_conflict | 600941:中国移动 scope | 按原文归母/合并/母公司口径修正后重跑，或转人工复核 | 模型统计口径与原文的归母/合并/母公司表达不一致 |
| B6_C10 | deterministic_check | unique | 601899:紫金矿业 | — | scope 不明确或不一致 |
| B6_C13 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B6_C14 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B6_C15 | deterministic_check | unique | 600941:中国移动 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |
| B6_C16 | deterministic_check | unique | 601899:紫金矿业 | — | 未找到唯一匹配的公司、年度、指标与文件版本 |

## 程序计算明细（仅 A 栏）

### B1_C1

```json
{
  "status": "match",
  "value": "10501.87",
  "exact_value": "10501.87",
  "claimed_value": "10501.87",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B1_C2

```json
{
  "status": "match",
  "value": "1370.95",
  "exact_value": "1370.95",
  "claimed_value": "1370.95",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B1_C3

```json
{
  "status": "match",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3490.79",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B1_C4

```json
{
  "status": "match",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "517.77",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B1_C5

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B1_C6

```json
{
  "status": "match",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "293",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B1_C7

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B1_C8

```json
{
  "status": "match",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "391.70",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00192757",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B1_C10

```json
{
  "status": "match",
  "value": "754.30",
  "exact_value": "754.29516296",
  "claimed_value": "754.30",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00483704",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "75429516296"
}
```

### B1_C11

```json
{
  "status": "mismatch",
  "value": "10502",
  "exact_value": "10501.87",
  "claimed_value": "10800",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-298.13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B1_C12

```json
{
  "status": "mismatch",
  "value": "1371",
  "exact_value": "1370.95",
  "claimed_value": "1400",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-29.05",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B1_C13

```json
{
  "status": "mismatch",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2500",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "165",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B1_C14

```json
{
  "status": "mismatch",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "306",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B1_C15

```json
{
  "status": "mismatch",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3036.40",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "454.39082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B1_C16

```json
{
  "status": "mismatch",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "320.51",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "197.26327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B1_C17

```json
{
  "status": "mismatch",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1677.11",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "264.6745863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B1_C18

```json
{
  "status": "mismatch",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "232.92",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "158.77807243",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B2_C1

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B2_C2

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B2_C11

```json
{
  "status": "match",
  "value": "10501.87",
  "exact_value": "10501.87",
  "claimed_value": "10501.87",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B2_C12

```json
{
  "status": "match",
  "value": "1370.95",
  "exact_value": "1370.95",
  "claimed_value": "1370.95",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B2_C13

```json
{
  "status": "match",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3490.79",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B2_C14

```json
{
  "status": "match",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "517.77",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B2_C15

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B2_C16

```json
{
  "status": "match",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "293",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B2_C17

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B2_C18

```json
{
  "status": "match",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "391.70",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00192757",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B2_C20

```json
{
  "status": "match",
  "value": "754.30",
  "exact_value": "754.29516296",
  "claimed_value": "754.30",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00483704",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "75429516296"
}
```

### B3_C1

```json
{
  "status": "mismatch",
  "value": "10502",
  "exact_value": "10501.87",
  "claimed_value": "10800",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-298.13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B3_C2

```json
{
  "status": "mismatch",
  "value": "1371",
  "exact_value": "1370.95",
  "claimed_value": "1400",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-29.05",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B3_C3

```json
{
  "status": "mismatch",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2500",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "165",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B3_C4

```json
{
  "status": "mismatch",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "306",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B3_C5

```json
{
  "status": "mismatch",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3036.40",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "454.39082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B3_C6

```json
{
  "status": "mismatch",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "320.51",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "197.26327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B3_C7

```json
{
  "status": "mismatch",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1677.11",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "264.6745863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B3_C8

```json
{
  "status": "mismatch",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "232.92",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "158.77807243",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B3_C11

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B3_C12

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B4_C1

```json
{
  "status": "match",
  "value": "10501.87",
  "exact_value": "10501.87",
  "claimed_value": "10501.87",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B4_C2

```json
{
  "status": "match",
  "value": "1370.95",
  "exact_value": "1370.95",
  "claimed_value": "1370.95",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B4_C3

```json
{
  "status": "match",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3490.79",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B4_C4

```json
{
  "status": "match",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "517.77",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B4_C5

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B4_C6

```json
{
  "status": "match",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "293",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B4_C7

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B4_C8

```json
{
  "status": "match",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "391.70",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00192757",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B4_C10

```json
{
  "status": "match",
  "value": "754.30",
  "exact_value": "754.29516296",
  "claimed_value": "754.30",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00483704",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "75429516296"
}
```

### B4_C11

```json
{
  "status": "mismatch",
  "value": "10502",
  "exact_value": "10501.87",
  "claimed_value": "10800",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-298.13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B4_C12

```json
{
  "status": "mismatch",
  "value": "1371",
  "exact_value": "1370.95",
  "claimed_value": "1400",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-29.05",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B4_C13

```json
{
  "status": "mismatch",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2500",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "165",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B4_C14

```json
{
  "status": "mismatch",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "306",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B4_C15

```json
{
  "status": "mismatch",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3036.40",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "454.39082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B4_C16

```json
{
  "status": "mismatch",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "320.51",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "197.26327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B4_C17

```json
{
  "status": "mismatch",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1677.11",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "264.6745863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B4_C18

```json
{
  "status": "mismatch",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "232.92",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "158.77807243",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B5_C1

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B5_C2

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B5_C11

```json
{
  "status": "match",
  "value": "10501.87",
  "exact_value": "10501.87",
  "claimed_value": "10501.87",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B5_C12

```json
{
  "status": "match",
  "value": "1370.95",
  "exact_value": "1370.95",
  "claimed_value": "1370.95",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B5_C13

```json
{
  "status": "match",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3490.79",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B5_C14

```json
{
  "status": "match",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "517.77",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.00327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B5_C15

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B5_C16

```json
{
  "status": "match",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "293",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B5_C17

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B5_C18

```json
{
  "status": "match",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "391.70",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00192757",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B5_C20

```json
{
  "status": "match",
  "value": "754.30",
  "exact_value": "754.29516296",
  "claimed_value": "754.30",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "-0.00483704",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "75429516296"
}
```

### B6_C1

```json
{
  "status": "mismatch",
  "value": "10502",
  "exact_value": "10501.87",
  "claimed_value": "10800",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-298.13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "1050187"
}
```

### B6_C2

```json
{
  "status": "mismatch",
  "value": "1371",
  "exact_value": "1370.95",
  "claimed_value": "1400",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-29.05",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "百万元",
  "actual_value": "137095"
}
```

### B6_C3

```json
{
  "status": "mismatch",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2500",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "165",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B6_C4

```json
{
  "status": "mismatch",
  "value": "293",
  "exact_value": "293",
  "claimed_value": "306",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "-13",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "293"
}
```

### B6_C5

```json
{
  "status": "mismatch",
  "value": "3490.79",
  "exact_value": "3490.79082852",
  "claimed_value": "3036.40",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "454.39082852",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "349079082852"
}
```

### B6_C6

```json
{
  "status": "mismatch",
  "value": "517.77",
  "exact_value": "517.77327785",
  "claimed_value": "320.51",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "197.26327785",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "51777327785"
}
```

### B6_C7

```json
{
  "status": "mismatch",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1677.11",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "264.6745863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```

### B6_C8

```json
{
  "status": "mismatch",
  "value": "391.70",
  "exact_value": "391.69807243",
  "claimed_value": "232.92",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "158.77807243",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "39169807243"
}
```

### B6_C11

```json
{
  "status": "match",
  "value": "2665",
  "exact_value": "2665",
  "claimed_value": "2665",
  "operator": "eq",
  "decimals": 0,
  "rounding": "ROUND_HALF_UP",
  "difference": "0",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "亿元",
  "actual_value": "2665"
}
```

### B6_C12

```json
{
  "status": "match",
  "value": "1941.78",
  "exact_value": "1941.7845863",
  "claimed_value": "1941.78",
  "operator": "eq",
  "decimals": 2,
  "rounding": "ROUND_HALF_UP",
  "difference": "0.0045863",
  "formula": "round_half_up(actual, decimals) | unit=actual * source_factor / target_factor",
  "claimed_unit": "亿元",
  "actual_unit": "元",
  "actual_value": "194178458630"
}
```


## 审计

- 确定结论 76 条；模型判断 16 条；需人工 28 条。
- 模型不产生「确认错误/证据支持」；这两类仅来自本地比较程序。
- 运行模式：chunked

## 工具调用链（agent）

（本次为单次拆解模式，未走工具循环）
