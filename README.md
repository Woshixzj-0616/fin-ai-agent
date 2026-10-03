# 金融投研智能体 · 2026 北京市大学生金融人工智能竞赛

> **参赛方向**：研究报告纠错核查（配上市公司财务报告分析）
> **队伍**：北京林业大学信息学院 · 3 人
> **初赛作品提交截止**：2026-10-18

---

## 这是什么

一个能在受控数据环境中、**依据原始财报核查投研报告**的智能体系统。

核心不是"让 AI 说得更好听"，而是**每个结论都能指回原文第几页** ——
对应赛题反复强调的**可核验、可追溯、可复现**。

---

## 当前进度

| 环节 | 状态 |
| --- | --- |
| 选题方向论证 | ✅ `docs/选题方向分析.md` |
| 赛程与关键节点 | ✅ `docs/赛程与关键节点.md` |
| 数据底板（下载 / 抽取 / 对账） | ✅ 70 份年报 · 14 个指标 · 抽取 710 行；9 个核心指标 70/70；对账 650 项一致 573（88.2%）—— `docs/数据底板说明.md` |
| 纠错核查器（队友 `new/` 并入） | ✅ `agent/`：材料登记 → 证据抽取 → Decimal 确定性核查 → LLM 拆句核查；**77 项测试全过** |
| LLM 判语义 / 代码锁证据（P1+P2+P3） | ✅ 运算符裁决（容差 2%）· 预测只标记 · 8 指标 · 双轨报告 · **JSON 多步工具循环**（tools.py / agent_loop.py，`check-text` 默认启用，失败回退）—— `docs/设计-LLM判语义代码锁证据.md` |
| 抽取器合流 | ✅ words 级主路径 + 表格线回退 + 利润表补营业总收入 + 表序推断；**70/70 全过、核心 4 指标 70/70**，指标 4 → 14 |
| words 几何单一事实源 | ✅ `agent/words.py` 供 agent 与 `scripts/data/extract_metrics.py` 共用（200 行重复逻辑删除，输出字节级一致） |
| 审计台（表格 + 点击溯源 + 审计日志） | ✅ `docs/` GitHub Pages 四栏；证据行点击后按 bbox 在 PDF 页图高亮 |
| 修 `unresolved_company` | ✅ 白名单扩到 14 家（读 `data/scope.csv`）；公司名允许在整份草稿溯源（不再只认句级引文） |
| 错误注入 + 评测集 | ✅ `scripts/eval/`：从真实证据生成准确/注错陈述（错值/错单位/错年份/符号反转）；**168 项 P=100% R=100% F1=1.0** —— `results/eval/` |
| 初赛材料 | ✅ 计划书（`docs/初赛/计划书.md` + PDF）+ 5 分钟视频脚本（`docs/初赛/视频脚本.md`）|
| 抽取 unit/scope 加固 | ✅ 去重补单位并重算归一值 + 报表标题归一（美的/招行版式）+ 旁证跨单位检索；**待复核字段 125 → 0** |
| 现场一键 live | ✅ `python agent/main.py live --code --year` / `live --pdf 新材料.pdf --code --name --year` |
| 运行说明 + 最终报告书 | ✅ `docs/运行说明.md` · `docs/最终报告书.md` |

---

## 目录结构

```
.
├─ docs/              文档（选题分析 / 赛程 / 数据底板说明）
│   ├─ 初赛/          ★计划书（md + PDF）+ 5 分钟视频脚本
│   └─ …              GitHub Pages 审计台（index.html / app.js / data/）
├─ agent/             ★纠错核查器（队友 new/ 于 2026-10-02 并入）
│   ├─ materials.py   来料登记 / 来源台账 / 指纹校验 / 运行留痕
│   ├─ words.py       words 级表格几何引擎（聚行切列 / 年份表头组 / 折行标签）
│   ├─ extract.py     证据抽取三通道：words 主路径 + 表格线回退 + 利润表补营业总收入
│   ├─ finance.py     Decimal 计算：单位换算 / 同比 / 按陈述精度比对
│   ├─ llm_check.py   模型只拆句，判定与计算全部本地 Python
│   ├─ main.py        CLI：import-existing / fetch / extract / analyze / demo / check-text
│   ├─ test.py        67 项测试（含 9 份真实年报集成测试）
│   └─ README.md / 项目说明.md
├─ data/
│   ├─ scope.csv      ★样本清单（14 家 × 2021–2025 = 70 份）
│   ├─ raw/           70 份年报 PDF 原始材料（只读不改，agent 也直接引用这里的文件）
│   ├─ sources/       每份材料一份来源档案（公告ID / 附件地址 / 指纹 / 页数 / 许可证）
│   ├─ extracted/     程序抽出来的数字 + 对账结果（financials.csv 长表 / metrics.csv 旧口径 / crosscheck.csv）
│   ├─ agent/         agent 自己的台账（materials.jsonl / manifest.csv / samples.json），不覆盖主仓 manifest.csv
│   ├─ eval/          错误注入评测集（claims / drafts）
│   ├─ gold/          人工核对过的标准答案（评测 gold 待复签）
│   └─ manifest.csv   ★来源登记表（70 行汇总：出处 / 公告ID / 指纹 / 页数 / 策略 / 复核标记）
├─ scripts/
│   ├─ data/          下载、抽取、排错脚本（数据底板）
│   ├─ quality/       三源对账脚本
│   ├─ eval/          错误注入评测（build_eval_set.py / run_eval.py）
│   └─ site_build.py  从 results/ 打包 GitHub Pages 审计台
├─ logs/              每次运行的输出记录（可追溯的证据）
│   └─ fetch_runs/    每次取数留痕（run.json + 候选公告清单 + 失败记录）
├─ results/           agent 运行产出（报告 / 证据 / 运行留痕 / history.zip / eval/）
└─ requirements.txt   依赖文件
```

---

## 环境与运行

需要 **Python 3.11+**（本机验证版本 3.14.6）。

```powershell
pip install -r requirements.txt

cd 项目根目录
python scripts\data\fetch_reports.py      # 下年报 PDF  → data\raw\ + data\manifest.csv
python scripts\data\extract_metrics.py   # 抠 14 个指标 → data\extracted\financials.csv
python scripts\quality\crosscheck.py     # 和东财对账  → data\extracted\crosscheck.csv

# —— 纠错核查器（agent/）——
cd agent
python main.py import-existing --source ..   # 把主仓 70 份年报按引用登记进 agent 台账
python -B test.py                            # 67 项测试
python main.py demo                          # 贵州茅台 2024 单报告示例
python main.py live --code 600519 --year 2024  # 现场一键：抽取/分析/报告
python main.py live --pdf 新材料.pdf --code 600000 --name 某某 --year 2025
python main.py check-text --file <草稿.txt>   # 模型拆句 + 本地确定性核查

# —— 错误注入评测（根目录）——
python scripts/eval/build_eval_set.py         # 生成注错草稿（data/eval/）
python scripts/eval/run_eval.py               # 抽取证据 → 判错 → P/R（results/eval/）
python scripts/site_build.py                  # 打包审计台到 docs/
```

数据底板三条命令**可重复执行**：材料已在本地且公告ID一致就复用（不重新下载），结果覆盖写。这是赛题要的「可复现」。

agent 的 `import-existing` 对主仓 `data/raw` 下的 PDF **按引用登记、不复制**（`by_reference`），台账落在 `data/agent/`，不覆盖主仓 `data/manifest.csv`。

**抽取的 14 个指标**：营业总收入、营业收入、归母净利润、扣非归母净利润、经营现金流净额、
基本每股收益、稀释每股收益、扣非基本每股收益、加权平均净资产收益率、扣非加权平均净资产收益率、
总资产、归母净资产、每股净资产、营业收入扣除后金额。
每个数字都带**页码定位**，能指回 PDF 第几页。其中前 4 个是核心指标（另单出 `metrics.csv`，向下兼容旧口径）。


**取数脚本的开关**（口径写死在脚本里，要改先拍板）：

| 开关 | 作用 |
| --- | --- |
| 直接跑 | 取首次披露版；本地已有且公告ID一致 ⇒ 复用，不重新下载 |
| `--refresh` | 强制重下，核对线上内容有没有变（变了就报错，不覆盖） |
| `--policy latest` | 改成选检索到的最新全文（默认首次披露版） |
| `--self-test` | 离线自检 10 项，不发网络请求 |

取数只做三件事：**查公告**（完整翻页，翻不全就报错）→ **校验 PDF**（当作真 PDF、逐页能解析）→ **落盘并登记**。
线上出现新版本（公告ID 或内容指纹变了）时**不覆盖、直接报错**要求人工确认 —— 保证已跑通的抽取结果始终对得上同一批材料。

**当前数据**：14 家 × 2021–2025 年报 = 70 份（覆盖白酒 / 乳制品 / 家电 / 医药 / 汽车 / 能源 / 金融 / 地产 / 科技制造），清单见 `data\scope.csv`。

---

## 数据来源与合规

- 年报 PDF 全部来自**巨潮资讯网**（上市公司公开公告）。来源 URL、公告 ID、发布日期、文件 sha256
  均已登记在 `data/manifest.csv`。
- 每份材料另有一份来源档案 `data/sources/<代码>_<年份>.json`（公告ID / 附件地址 / sha256 / 页数 / 首次获取时间 / 许可证状态）；
  每次取数留痕在 `logs/fetch_runs/<留痕号>/`，含本次脚本自身的 sha256 与每份材料的候选公告清单。
- 第三方数据（东方财富）**仅用于交叉验证**，不作为唯一真源；赛题要求"依据原始财务材料"。
- 第三方库：`pymupdf`（见 `requirements.txt`）。其余全部为 Python 标准库。

---

## 已知的坑（踩过了，别重踩）

1. **不能用 pymupdf 的"行"级取数**。年报里相邻两列的数字会被并成同一段文本
   （如 `170,899,152,276.34 147,693,604,994.14`），导致整行一个数字都认不出来。
   **必须用 words 级取数，自己按横坐标切列。**
2. **标签会被折成好几行**（"归属于上市公司股东的净" + "利润"）。
   做法：每个标签碎片**只归给纵向最近的那个数字行**，再按纵坐标拼回来。
   不能用"距离阈值窗口"——会把表头文字吸进来。
3. **中文编码**：`pypdf` 读这份年报中文全是乱码，`pymupdf` 正常；
   PowerShell 控制台打印中文也会乱码。**结论：结果一律落成 UTF-8 文件再看。**
4. **"营业总收入" ≠ "营业收入"**。茅台 2024 两者差 32.4 亿（财务公司的利息收入）。
   拿营业总收入去比别人的营业收入，会得出"数据错了"的**误报**。
5. **第三方（东财）历史数用的是"追溯重述后"的值，我们存的是当年年报原值**。
   例如长江电力 2022 营业收入：我们 520.60 亿（当年原值）/ 东财 688.63 亿（2023 年收购后重述）。
   所以核查器的每个数字都必须带上"出自哪一年的哪一版年报"（`col_source` + `page` 字段）。
6. **东财自己也会有和年报对不上的格子**。中国平安 2022 年报第 12 页白纸黑字营业收入 1,110,568 百万元，
   东财同一年记成 880,355 百万元。⇒ 第三方数据只能做交叉验证，不能当真源。
