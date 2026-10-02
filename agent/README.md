# 年报财务证据与核查（学生竞赛版）

第一条线（选题1、2）：来源登记 → PDF证据抽取 → 模型拆解草稿 → Python确定性核查 → 带证据的报告。
`demo`仍使用预填字段；新增`check-text`读取自然语言草稿并调用配置的模型。当前阶段聚焦接入流程，不推进测评集。

## 目录

```text
new/
├─ main.py            启动入口、处理流程、生成报告
├─ materials.py       下载、来源台账、文件校验、运行记录
├─ extract.py         PDF表格与财务证据抽取
├─ finance.py         Decimal计算、可比性检查、陈述核查
├─ llm_check.py       模型接口、结构化录入、原文校验、草稿核查报告
├─ test.py            所有测试放在一个文件
├─ run.ps1            Windows一键安装依赖并启动
├─ requirements.txt   固定第三方依赖版本
├─ README.md          从这里开始
├─ 项目说明.md        学习步骤、字段、计算规则和验收边界
├─ data/              9份PDF、来源台账、样例（直接平铺）
└─ results/           报告、证据、测试结果和历史ZIP（直接平铺）
```

业务文件只有`data`和`results`两个子目录，其中不再套目录。5个功能源码文件按职责合并，建议按`main → materials → extract → llm_check → finance`阅读。`.git`是本地Git管理目录，其内部对象不属于源码目录结构。

## 启动

项目固定使用Python **3.12.x**；本轮在3.12.14上验证。在`new`打开PowerShell：

```powershell
.\run.ps1 demo
.\run.ps1 analyze
.\run.ps1 verify
.\run.ps1 test
```

启动脚本先查找Python 3.12，再创建环境并安装固定的PyMuPDF 1.26.7。只安装预编译wheel，没有合适wheel时明确失败，不自动尝试C/C++源码编译。脚本和直接执行的main.py、test.py都会拒绝其他Python小版本。

可用`$env:FINLINE_PYTHON = '你的Python312目录\python.exe'`指定解释器；指定后版本不符会停止，不静默换成别的版本。未指定时依次检查`py -3.12`、`python`、常见用户安装位置和本机已有的桌面内置运行时，均必须通过3.12版本检查。本机已找到3.12.14，无需改动系统默认Python。

环境位于系统临时目录`%TEMP%\finline-python-3.12-<依赖指纹>`，不放进源码目录；临时环境被系统清理后会自动重建。首次安装需要网络，已有材料的抽取与核查可离线运行。

若本机的PowerShell执行策略拦截脚本，可仅对这次启动使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 demo
```

已有配置好PyMuPDF的Python 3.12环境时，也可直接执行`python -B main.py demo`与`python -B test.py`。使用其他默认Python时请运行run.ps1；`-B`避免在源码旁生成字节码缓存。从项目目录之外调用run.ps1或main.py的完整路径也可运行，模块使用同级import。

更多命令：

```powershell
.\run.ps1 demo --render
.\run.ps1 analyze --code 600519 --year 2024
.\run.ps1 check-gold
.\run.ps1 fetch --code 600887 --year 2024 --policy first --as-of 2026-09-29
.\run.ps1 --help
```

## 接入模型核查草稿

1. 向服务商确认兼容Chat Completions的Base URL和模型名；本项目不根据密钥格式猜服务商。
2. 准备UTF-8纯草稿，例如`data/draft.txt`。不要把包含密钥的接入说明当成草稿。
3. 配置非敏感的服务地址和模型名，然后运行：

```powershell
$env:LLM_BASE_URL = '服务商提供的HTTPS Base URL'
$env:LLM_MODEL = '服务商提供的模型名'
.\run.ps1 check-text --file data/draft.txt --ask-key
```

`--ask-key`在终端隐藏输入，只在本次进程使用，不写入文件。也可以从调用进程传入`LLM_API_KEY`环境变量；不要把实际密钥写进脚本、草稿或Git。程序不会自动读取桌面的接入说明。

默认使用`json_schema`严格结构化输出。服务商明确只支持JSON mode时，显式加`--format json_object`；JSON mode仍会执行同一套本地字段校验，不从自由文本或Markdown代码块猜字段。模式不支持时明确失败，不静默换模型或降级。也可通过`--base-url`、`--model`指定非敏感配置。

当前已通过62项功能与回归检查；真实模型联通尚未验证，仍需要确认密钥所属服务商、Base URL和模型名。模拟接口测试不计为真实模型调用。

一次最多12000字、40句。模型只收到草稿、公司白名单、指标词典和字段规范，不收到年报金额或标准答案。输出中的数字、单位、指标、年份与引用原文由程序检查，再调用现有`finance.check_claim`。程序将“下降”转换为负号，单位换算和同比仍使用Decimal。Markdown修改建议只使用程序计算结果，不进行第二次模型润色。

核查上下文默认人民币、完整年度；营业收入和经营现金流采用合并口径，原文明示其他口径时优先按原文。普通净利润不自动当作归母净利润，约数、区间、预测、库外指标和无法对应的公司分别保留明确原因。若模型没拆出某句，报告会标记人工检查，不把该句静默算作通过。

## 看哪些文件

| 文件 | 用途 |
|---|---|
| [练习数据](data/samples.json) | 8句草稿、人工填写的核查项、预期结果、24条固定参考答案合放一处 |
| [自然语言草稿](data/draft.txt) | 用于演示接入流程，没有新增测评标签或评分机制 |
| [来源清单](data/manifest.csv) | 公司、年份、公告ID、指纹、日期和版本；完整历史见同目录materials.jsonl |
| [演示报告](results/demo_report.md) | 贵州茅台2024年报与8条陈述的核查结果 |
| [分析报告](results/report.md) | 同时展示复算同比、年报披露同比和核对结果；按年报精度舍入 |
| [测试结果](results/test_results.txt) | 合并后的测试运行记录 |
| [本轮验收](results/validation.json) | 环境、测试、材料和真实联网验证汇总 |
| [项目说明](项目说明.md) | 四步学习路线、数据字段、计算边界、后续验收事项 |

`results`中的同名输出会更新为最近一次生成的内容。`summary.json`列出本次输出的指纹；`events.jsonl`只保存本次日志，旧日志随当时输出和源码归档到`history.zip`，用`run_id__文件名`区分。一次运行一个命令。

历史策略：最多保留最近**20次完整运行**，解压内容合计预算**50 MiB**；达到任一上限，淘汰更早的完整运行。单次运行本身超过预算时，保留该次完整记录并去掉更早历史，避免截断证据。重写ZIP先校验后原子替换，写入失败时保留旧ZIP。需要长期保存的里程碑应另行提交到Git。不要在工程内解压归档。

整理前的源码、9份材料、说明、验收和运行记录已完整保存在`results/before_simplify.zip`；第三方环境、缓存和临时文件可重新生成，不纳入该备份。

本轮修改前的完整版本已提交到本地Git：`d664cb5`，标签`before-conservative-fixes-20260929`。上述约24MB整理备份属于固定历史材料，不再自动扩充，本轮保留。可用`git show before-conservative-fixes-20260929:main.py`查看修改前源码。

模型接入前另有Git提交`37940b2`，标签`before-llm-integration-20260929`。

`check-text`成功后，`results/checked_draft.txt`保存纯草稿，`text_checks.json`保存拆解字段、程序判定、计算与证据，`text_report.md`提供可读报告。失败运行会生成明确的失败状态，不将协议异常当作财务结论。请求头、密钥及服务端原始错误正文不进入运行记录。

24条参考答案仍待团队人工复签，本轮不扩展测评集或给出模型准确率。估值倍数及其所需股价、市值时点和利润口径留作后续扩展。

