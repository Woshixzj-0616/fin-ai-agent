# FinScope V5.2.0 本地研究工作台

金融投研智能体，面向 2026 北京市大学生金融人工智能竞赛的 1 数据结构化提取、2 上市公司财务报告分析、5 研究报告纠错核查。V5.2.0 把六模块财报分析、跨模块综合报告、资料提取和研报核查整合在同一个本地网页工作台里。

所有分析都在本机完成，只有在你主动启动分析并且配置了模型之后，文档内容才会发送到你配置的模型服务。密钥不会随仓库分发。

## 一、快速开始（Windows）

1. 安装 Python 3.11 或更高版本，安装时勾选 `Add python.exe to PATH`。
2. 双击仓库根目录的 `启动 v5.2.0.cmd`。
3. 首次启动会自动把依赖安装到本目录的 `vendor/python`（需要联网，约几分钟），随后浏览器打开 <http://127.0.0.1:8769/app/finscope.html>。
4. 在页面右上角“更换 API 密钥”里填入 DeepSeek（或 OpenAI 兼容接口）的密钥。密钥只保存在本机服务进程中，不写入仓库。
5. 上传 PDF 或选择内置的五粮液 2023 年样稿，开始分析。

如果浏览器没有自动打开，手动访问 <http://127.0.0.1:8769/app/finscope.html>。端口 8769 被其他程序占用时，先关闭占用该端口的旧版本服务。

## 二、快速开始（macOS / Linux）

```bash
git clone https://github.com/Woshixzj-0616/fin-ai-agent.git
cd fin-ai-agent
bash start-v5.2.0.sh
```

脚本会创建 `.venv`、安装 `requirements.txt` 并在 8769 端口启动服务。也可以手动执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -B agent/webui.py --host 127.0.0.1 --port 8769
```

然后在浏览器打开 <http://127.0.0.1:8769/app/finscope.html>。

## 三、模型配置

工作台默认调用 DeepSeek 的 OpenAI 兼容接口，可以在页面里临时配置，也可以用环境变量：

| 变量 | 用途 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | 工作台使用的密钥 | 空 |
| `LLM_BASE_URL` | 兼容接口地址 | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `LLM_FORMAT` | 结构化输出模式 | `json_object` |
| `DEEPSEEK_API_KEY` | 六模块引擎单独运行时的密钥 | 空 |
| `DEEPSEEK_BASE_URL` | 六模块引擎接口地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 六模块引擎模型名 | `deepseek-flash` |

- 通过网页“更换 API 密钥”配置的密钥只留在当前服务进程，重启后需要重新填写。
- 想让六模块引擎在单独启动时也读到密钥，可复制 `six_module_engine/.env.example` 为 `six_module_engine/.env` 并填写 `DEEPSEEK_API_KEY`。该文件已被 `.gitignore` 排除。
- 根目录的 `.env.example` 汇总了全部变量，可用于 `export` 或系统环境变量配置，不会被程序自动读取。

## 四、目录说明

```
agent/                工作台页面、分析流水线、LLM 调用与核查逻辑
web/                  V5.2.0 前端页面与静态资源
six_module_engine/    六模块财报分析引擎（后端、模块、提示词、报告流水线）
six_module_engine/data/integrated_workbench/  六模块运行数据（仅保留样稿种子库和样例 PDF）
data/raw/             70 份上市公司年报 PDF（数据底板）
data/demo/            模拟公告、真实公告与核查样例
data/eval/            评测草稿与报告样稿
docs/                 设计、验收记录和静态说明页
results/              历史运行结果与证据
scripts/              数据整理、OCR、对账和发布冒烟脚本
skills/               研报核查作业规程
```

首次启动时，如果没有 `six_module_engine/data/integrated_workbench/integrated.sqlite3`，程序会从同目录的 `integrated.seed.sqlite3` 复制一份只含五粮液 2023 样稿的种子库，之后的上传和运行记录都只写在本机。

## 五、常见问题

- **提示找不到 Python**：安装 Python 3.11+ 并勾选加入 PATH，然后重新双击启动脚本。
- **首次启动很慢**：依赖安装在 `vendor/python`，只在第一次或依赖清单变化时执行。
- **端口被占用**：关闭占用 8769 的旧版本服务；或设置环境变量 `PORT` 后手动启动。
- **模型未配置**：页面右上角“更换 API 密钥”，或设置 `LLM_API_KEY` 后重启服务。
- **OCR 可选依赖**：需要识别扫描版公告时执行 `python scripts/setup_ocr.py`，脚本会校验文件指纹。
- **克隆体积较大**：仓库包含年报 PDF 底板，建议首次克隆使用 `git clone --depth 1`。

## 六、数据与许可

`data/raw/` 是公开披露的上市公司年报，仅用于本地研究和竞赛复现；公开披露不等于开放再分发许可，对外发布时请自行核实来源条款。仓库代码的许可证需由权利人共同确定。
