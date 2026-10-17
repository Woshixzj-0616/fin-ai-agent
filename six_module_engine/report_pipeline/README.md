# 六模块财报统筹流程

这个目录提供一条独立于六个专业模块的报告统筹流程。它读取一次已经完成的六模块结果，依次完成发现提取、跨模块主题归纳、反方复核和编辑成稿。六个模块源码和原始运行结果都不会被改写。

V5.2.0 工作台当前使用 `backend/report_generator.py` 生成报告。它把模块一收入构成纳入发现整理，并由 `backend/financial_brief.py` 从六模块已标为可计算的合并数据生成统一财务底表、利润与现金桥接及主要资产占用。发现卡的模块归属和引用页由程序限定；正文的明显数值冲突会被隔离并使报告保持待复核状态。此处的 `pipeline.mjs` 是独立的历史命令行流程，不是当前工作台的成稿入口。

## 本地保存与外部模型

报告和运行材料默认保存在本地。脚本包含一个可选的 DeepSeek API 适配器；默认拒绝联网。只有明确决定把选中的六模块分析内容发送到 DeepSeek 时，才在 FinScope V4.0 项目目录执行：

    node report_pipeline/pipeline.mjs --source-run-id 603a98fb89234045a881b5f4b498cf68 --allow-external-model

启用该选项后，流程从项目根目录的 .env 读取 DEEPSEEK_API_KEY、DEEPSEEK_MODEL 和可选的 DEEPSEEK_BASE_URL，通过 DeepSeek Chat Completions JSON 输出完成四个处理阶段。生成内容写入 data/integrated_workbench/report_pipeline/<来源运行号>/<本次运行时间>/，不会覆盖来源结果。该本机数据目录已由项目的 .gitignore 忽略。此选项只允许调用外部 API，不会同步或推送 GitHub。

## 四个处理阶段

1. 发现提取：逐模块阅读六份结果，整理事实、解释、风险、反证、边界和待核问题。每条发现保留模块、来源字段编号和允许引用的页码。
2. 跨模块综合：把六个模块的发现组成少量可检验主题，明确支持证据、反证和因果链是否成立。
3. 反方复核：检查主题中的替代解释、隐含假设、反证和结论应保留的限定。
4. 编辑与报告：生成阅读提纲和中文 Markdown 报告，事实引用使用发现编号；报告附带六模块状态表和证据索引。

## 本版借鉴的本地 Skills

- financial-red-flag-auditor-skill：借鉴问题卡、证据与判断分层、核查问题和风险信号不能写成舞弊定论的做法。
- FinRGAgents：借鉴支持论点、反方论点、修正观点再生成提纲的分层次序；本版只采用分析流程，不依赖其 AutoGen 运行时。
- evilhill-equity-research-skill：借鉴证据、反证、结论和引用之间的结构化关系。
- rollingSirius-equity-research-skill：借鉴财报模式的事实来源纪律和完整财务分析覆盖；本版不加入估值或交易建议。
- GeniusTrader-Harry-equity-research-skill、china-stock-fund-analysis-skill：保留在参考项目目录供后续比较，本版首轮不加载其估值、市场预测或基金筛选流程。

上述参考内容位于项目旁的 FinScope参考项目 目录。本实现使用自行编写的项目提示词，没有把上游 Skill 文件复制进本目录。

2026-10-15 的五粮液首轮材料保存在本机数据目录的 local_20261015_synthesis_v1。它基于六个模块原始结果和已有 validated_synthesis.json 整理，按四阶段产出发现、主题、反方复核、提纲和报告。DeepSeek 请求未收到 HTTP 响应，外部重试被自动审查拒绝；因此这些文件是本会话的本地综合结果，不是 pipeline.mjs 成功调用外部模型后的自动输出。详情见该运行目录的 audit.json。

该目录现另有 `editorial_report.json`：五粮液 2023 年可阅读的财报分析样稿。它以六模块结果确定研究问题，再由本地原始年报核对第 14–15、34、42、50–55、57–58、61–62、100、112、126–127 等页，补上收入贡献、营业利润变化桥、净利润到经营现金流的调节、产销存勾稽和资金配置。`report.md` 由 `render_editorial.py` 从这份结构化样稿生成，工作台也直接按该结构呈现正文；主题卡与复核记录保存在底稿入口。此样稿是本地编辑产物，现有自动流程尚不能保证对其他公司产生同等深度的分析。

## 产物与边界

- findings.json：六模块发现卡及来源编号。
- themes.json：跨模块主题、支持证据、反证和因果状态。
- review.json：反方复核与需要保留的限定。
- outline.json、report.md：报告提纲和阅读版报告。
- audit.json：来源文件摘要值、模型调用用量、引用编号和页码检查结果。

审计检查只验证引用编号和页码能否回到本次模块输入，不等于人工核验财报原文或会计判断。来源模块的 partial、needs_review 等状态会保留在报告中。报告仅分析公司财务与披露，不输出估值或买卖建议。
