# 核心模块运行入口

工程在仓库根目录的 `agent/`，数据在 `data/`，输出在 `results/`。统一运行命令和环境要求见 [运行说明](../docs/运行说明.md)，本轮验收见 [赛题修复验收](../docs/赛题修复验收.md)。

```bash
python3 -B agent/main.py --help
python3 -B agent/main.py demo --render
python3 -B agent/main.py analyze --code 600519 --year 2024
python3 -B agent/main.py announcement --pdf data/demo/real/fosun_2025_pledge.pdf --type pledge
python3 -B agent/main.py audit --claims data/demo/audit_claims.json
python3 -B agent/webui.py
python3 -B -m unittest discover -s agent -p 'test*.py'
```

B7 质量门可从仓库根执行：`python -B scripts/check_sensitive.py`、`python -B scripts/check_docs.py`、`python -B scripts/review_structure.py`、`python -B scripts/b7_release_smoke.py`。旧 `/`、`/trace.js` 和五个 `/api/*` 入口继续保留；新工作台在 `/app/`。发布回退可直接运行 `python -B agent/webui_app.py`，旧命令 `python -B agent/webui.py` 仍可用。

模型草稿核查：先配置 `LLM_BASE_URL`、`LLM_MODEL`，通过 `--ask-key` 隐藏输入临时密钥，或只在进程环境设置 `LLM_API_KEY`。执行 `python3 -B agent/main.py check-text --file 草稿.txt --ask-key`。未经配置时程序不会假称进行了模型调用。

`main → materials → extraction/interim/announcements → finance/periods/financial_signals → audit` 是主要阅读顺序。`extraction/` 是年度抽取实现源，`extract.py` 仅保留旧导入兼容门面；`audit/` 是 Schema、模型客户端、核查、报告和补充审计实现源，`audit/tools/` 是工具规格、调度和事件实现源；`llm_check.py`、`tools.py`、`audit_checks.py` 和 `agent_loop.py` 仅保留旧导入兼容门面。`trace` 将真实事件转成可读执行记录；`webui` 提供上传入口。

评测：`scripts/eval/run_report_eval.py` 与 `scripts/eval/run_llm_eval.py` 统一走 `agent/eval_harness.py` 的当前完整核查流程（`check_text` 分块、门控、预算、恢复）；参考答案只进评分程序，逐条原子陈述记录判定、弃答、漏拆、超时与失败。报告与运行记录默认写到独立临时目录并保留，可用 `--output-dir` 指定输出目录；按引文一对一评分，重复/多余预测单列，不覆盖现有历史文件。

Python 支持 3.11+，PyMuPDF 锁定 1.28.2。测试使用内置中文字形，不依赖 Windows 字体路径。本工程没有 `run.ps1`；不要使用旧 `new/` 目录的运行说明。

每次 `Run` 记录运行环境、文件访问、输出指纹与源码。当前事件在 `events.jsonl`，可读步骤在 `trace.json`，完整历史和当时源码在 `history.zip`。最多保留 20 次完整运行、解压内容预算 50 MiB；最新运行超过预算时仍保留其完整内容。
