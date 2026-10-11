# B7 施工单 · 安全加固、CI 与旧入口退役

> 日期：2026-10-11
> 依赖：B6 已收工；当前工作树作为 B7 基线，B7 不改 B1-B6 的裁决、抽取、运行、SSE、Web API 和前端业务契约。
> 状态：**本地实现完成，待远端 CI 授权**；范围内生产代码、专项测试、干净环境等价验收和 R1-R11 已完成；GitHub Actions 因未 commit/push 尚未取得外部运行记录。
> 时间口径：总方案原建议 B5-B7 赛后执行；用户在 2026-10-11 明确提前推进。B7 即使提前，也必须按“安全门 -> CI -> 兼容清单 -> 结构收敛 -> 发布冒烟”分阶段落地，不能一次改完。
> CI 外部门：本地只能在干净虚拟环境做等价验证；GitHub Actions 运行记录需要用户明确批准 commit/push 后才能取得。没有该批准时，不得把“工作流文件已写好”冒充“CI 已在干净环境通过”。

## 一、批次目标与用户可观察结果

1. 建立长期 CI 质量门：测试、编译、文档检查和敏感信息扫描在干净环境一次执行。
2. 代理、限流、上传、会话、取消、事件脱敏和公开响应边界都有回归测试，且每个新门都经过失败注入。
3. 旧 HTTP 入口、旧静态入口和旧 Python 门面先登记调用方与证据，再决定保留或退役；默认保留，不为了“干净”删除仍被调用方使用的入口。
4. `agent/webui.py` 降到 300 行以内，只保留兼容转出和启动职责；旧 `/`、`/trace.js`、旧 `/api/*` 行为不变。
5. 新建 `web/legacy/index.html` 承接旧内联页面，移动后旧页面关键 DOM、接口和交互保持兼容。
6. `Run.sources` 归档 `web/**` 静态资产和 B7 质量脚本，历史运行能还原当时前后端实现。
7. 密钥、Authorization、常见 token、私钥和原始模型字段不进入事件文件、运行公开字段、前端响应和归档公开字段。
8. 文件体量和依赖方向有只读审查脚本；体量只产出审查警告，不机械按行数失败；明确禁止的反向依赖必须失败。
9. 发布与回滚有可执行冒烟证据：新工作台、旧兼容页、新运行 API、旧兼容 API 和直接运行旧实现回退入口均可启动。
10. 最终兼容清单必须是“零旧入口”或“逐项明确保留”，不能出现“文件还在但不知道谁用、为什么留”。

## 二、范围、依赖与明确不做

### 本批范围

- 新增 B7 专项测试，先建立 RED，再按安全、兼容、CI、结构、发布冒烟顺序实施。
- 新增共享脱敏模块，统一处理事件、运行状态和桥接 payload。
- 补齐 `/app/`、旧页面、API、SSE、页图和错误响应的安全头。
- 收紧新 API 的多 PDF 数量与总大小边界，保留现有单 PDF 40MiB 上限。
- 把 `agent/webui.py` 按页面、流水线、HTTP、应用启动拆到独立模块，原模块保留兼容名和直接运行入口。
- 把旧页面原始字符串迁到 `web/legacy/index.html`，HTTP 层继续按原字节语义提供 `/` 与 `/index.html`。
- 新增敏感信息扫描、文档检查、结构审查和发布冒烟脚本。
- 新增 GitHub Actions 工作流。
- 建立 B7 兼容清单和机器可读 inventory。
- 更新测试与文档中的源码位置断言，不改变用户可见旧接口。
- 更新施工单索引、重构文档索引和项目记忆锚点。

### 依赖

- B6 全量基线：`272` 条通过，`skipped=1`。
- B6 证据：`docs/refactor/TDD-B6-审计工具与报告模块拆分.md` 和 `docs/refactor/evidence/B6/`。
- 当前旧页面：`agent/webui.py` 1777 行，承载 HTML、CSS、JS、旧 API 和兼容流水线。
- 当前 CI：仓库没有 `.github/`。
- 当前依赖：仅 `pymupdf==1.28.2`、`httpx==0.28.1`；Python 3.11+。
- 当前旧入口调用证据：
  - 旧 HTTP：`web/legacy/index.html` 迁移后的旧页面、`agent/test.py`、`agent/test_competition.py`。
  - Python 门面：`main.py`、`qa.py`、`mcp_server.py`、`scripts/llm_smoke.py`、`scripts/tool_demo.py`、既有测试。
  - 旧静态：`/`、`/index.html`、`/trace.js`。
- B7 开工关键指纹将作为 RED 证据写入 TDD；实施前不得覆盖当前未提交的 B1-B6 工作树。

### 明确不做

- 不删除仍被 CLI、MCP、测试或旧页面使用的旧入口。
- 不把“保留兼容”写成“退役完成”。
- 不改 `check_one_claim`、工具裁决、证据要求、报告三栏、bundle schema version 2。
- 不改 B1 事件核心字段 `run_id + seq + phase + event_type + payload`。
- 不改 `agent/api/routes.py` 新 API 路径、响应顶层字段或 SSE 事件名。
- 不新建模型供应商、不调用真实模型、不扩大运行时依赖。
- 不把 GitHub Action 第三方 action 当运行时依赖；工作流只使用固定 major 的官方 `actions/checkout` 与 `actions/setup-python`。
- 不靠行数机械失败；`webui.py` 300 行是明确兼容架构目标，不是所有文件统一门。
- 不清理、回滚、提交、push、删除 `results/runs/` 或改动用户未要求的历史运行。
- 不在没有用户批准时触发远端 CI、创建 PR、合并或发布。

## 三、文件计划

### 新增

- `.github/workflows/ci.yml`：干净 Ubuntu 环境跑敏感扫描、文档检查、编译、结构审查和全量测试。
- `agent/runtime/redaction.py`：共享密钥、token、私钥和原始模型字段脱敏函数。
- `agent/webui_page.py`：加载 `web/legacy/index.html`，保留 `PAGE`、`PAGE_PNG` 兼容名。
- `agent/webui_pipeline.py`：`guess_identity`、`run_pipeline`、`_check_draft`、`run_announcement_pipeline`。
- `agent/webui_http.py`：限流、工作区清理、静态路由、multipart、JSON 响应、安全头和 `Handler`。
- `agent/webui_app.py`：创建 `ThreadingHTTPServer`、绑定 `Handler`、实现 `main()`，可独立直接运行作为回退入口。
- `web/legacy/index.html`：从当前 `PAGE` 原样迁出的旧兼容页。
- `agent/test_b7.py`：B7 专项 RED/GREEN、安全负样本、兼容清单、结构、CI 和发布冒烟契约。
- `scripts/check_sensitive.py`：只读敏感信息扫描；只输出文件、行号和规则名，不回显命中值。
- `scripts/check_docs.py`：UTF-8、控制字符、Markdown 围栏和本地链接检查。
- `scripts/review_structure.py`：体量审查和明确反向依赖检查；输出 JSON 证据。
- `scripts/b7_release_smoke.py`：启动临时 HTTP 服务，检查新旧入口和至少一条运行 API，然后关闭。
- `scripts/b7_reverse_checks.py`：在临时目录或 monkeypatch 内执行 R1-R11 失败注入并落原始 JSON，不修改仓库。
- `docs/refactor/B7-兼容清单.md`：逐项列出旧入口、调用方、状态、证据和退役门。
- `docs/refactor/TDD-B7-安全加固-CI与旧入口治理.md`：实施完成后的 RED/GREEN、反向检查、CI 和结账证据。
- `docs/refactor/evidence/B7/`：兼容 inventory、结构审查、发布冒烟、CI 命令摘要和反向检查结果。
- `docs/refactor/evidence/B7/ci-local-verification.json`：干净 venv 与 GitHub Actions 待授权状态摘要。
- `docs/refactor/evidence/B7/reverse-checks.json`：R1-R11 原始通过记录。

### 修改

- `agent/webui.py`：收敛到 300 行以内；显式转出旧调用名，`main` 只委托 `webui_app.main`。
- `agent/api/routes.py`：限制对比 PDF 数量与总大小；不改新 API 路径和响应字段。
- `agent/api/runtime_bridge.py`：删除本地重复脱敏规则，改用 `runtime.redaction`。
- `agent/runtime/events.py`：事件标题、摘要、payload 入库前统一脱敏。
- `agent/runtime/run_manager.py`：`run.json` 的 error、parameters 和结果公开字段统一脱敏。
- `agent/materials.py`：`Run.sources` 纳入 `web/**` 静态资产和 `.github/workflows/ci.yml`。
- `agent/test.py`、`agent/test_competition.py`：旧页面源码断言改读 `web/legacy/index.html` 或实际 HTTP 响应，不再假定页面内联在 `webui.py`。
- `agent/test_web_assets.py`、`agent/test_b4.py`：补安全头和拆分后对象身份断言，不删除旧兼容断言。
- `agent/README.md`、`docs/运行说明.md`：补 CI、兼容页、发布冒烟和旧入口状态。
- `docs/refactor/00-索引.md`、`docs/refactor/施工单/00-索引.md`：登记 B7 施工单。
- 项目 `Codex记忆系统/项目/金融投研智能体/00-索引.md`：更新为“B7 施工单已冻结，未开工”。
- 项目 `Codex记忆系统/项目/金融投研智能体/03-决策记录.md`：登记用户提前推进 B7，但不得自证 CI 已完成。

### 保留兼容

- `POST /api/prefill`
- `POST /api/analyze`
- `POST /api/announcement`
- `POST /api/ask`
- `POST /api/search`
- `GET /`、`GET /index.html`、`GET /trace.js`
- `from webui import PAGE, PAGE_PNG, Handler, guess_identity, run_pipeline, run_announcement_pipeline, _check_draft, _parse_multipart, _rate_limit, _rate_buckets, _RATE_CAPACITY, _cleanup_work_dir, WORKSPACE, _WORK_TTL_SEC, LAST_FACTS`。
- `python -B agent/webui.py` 和 `cd agent && python webui.py` 两种启动方式。
- `agent/webui_app.py` 作为直接回退启动入口，不能只留导入副作用。

## 四、接口与契约

### CI 契约

工作流名：`CI`。

触发：

- `push`
- `pull_request`
- `workflow_dispatch`

矩阵：

- Python `3.11`
- Python `3.12`

步骤顺序固定：

1. `actions/checkout@v4`
2. `actions/setup-python@v5`
3. `python -m pip install -r requirements.txt`
4. `python -B scripts/check_sensitive.py`
5. `python -B scripts/check_docs.py`
6. `python -B scripts/review_structure.py`
7. `python -B -m compileall -q agent scripts`
8. `python -B -m unittest discover -s agent -p 'test*.py'`

CI 任一命令非零即失败。工作流不得使用 `continue-on-error`，不得把安全扫描设成仅警告。

### 安全头契约

`/app`、`/app/` 和 `/app/*`：

- `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- HTML `Cache-Control: no-store`；CSS、JS、SVG 保持现有短缓存策略。

旧 `/`、`/index.html`：

- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Cache-Control: no-store`

API JSON：

- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`

SSE：

- 保留 `Cache-Control: no-cache`、`Connection: close`、`X-Accel-Buffering: no`
- 新增 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`

页图：

- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`

### 脱敏契约

`redact_text()` 至少处理：

- `sk-` 类密钥
- `ghp_`、`gho_`、`ghu_`、`ghs_`、`ghr_` GitHub token
- `github_pat_`
- `AKIA` AWS access key
- `AIza` Google API key
- `Authorization: Bearer ...`
- PEM 私钥头
- 环境变量形式 `*_API_KEY`、`*_TOKEN`、`*_SECRET` 的值

`redact_value()` 还要递归移除或折叠以下字段：

- `chain_of_thought`
- `reasoning`
- `thinking`
- `raw`
- `raw_response`
- `prompt`
- `system_prompt`
- `messages`
- `content`
- `content_preview`
- `context_quote`
- `quote`
- `parsed`
- `sentences`

契约：

- 命中值统一替换为 `[已隐藏凭证]` 或 `[已隐藏字段]`。
- 扫描器日志不得回显命中值。
- 事件 JSONL、`run.json`、API 运行摘要和前端事件不得含未脱敏命中和原始模型字段。
- 脱敏不得改 event_type、seq、phase、evidence_ids 和正常财务字段。

### 上传与限流契约

- 单个主 PDF 最大 40MiB。
- 对比 PDF 最多 4 份。
- 对比 PDF 单份最大 40MiB。
- 单个新 run 的主 PDF 与对比 PDF 合计最大 80MiB。
- 超数量、超总量分别返回 `INVALID_UPLOAD`，不得只写入事件后继续执行。
- 旧 multipart 请求体总上限仍为 40MiB。
- `TRUSTED_PROXY_HOPS` 未配置或不合法时只信 socket peer。
- 受信代理场景从 `X-Forwarded-For` 右侧取对应 hop，非法 IP 回退 peer。
- 同一 IP 达到令牌桶上限时返回 429；测试必须证明伪造 `X-Forwarded-For` 在未配置代理时不能绕过。

### Web 兼容拆分契约

- `agent/webui.py` 顶层不得再定义 `Handler`、`run_pipeline`、`PAGE` 字符串或业务流水线。
- `agent/webui.py` 可以显式转出兼容名并定义 `main()`，总行数必须 `<=300`。
- `agent/webui_http.Handler is agent.webui.Handler`。
- 迁移后的 `PAGE` 必须等于 `web/legacy/index.html` UTF-8 文本。
- `/` 与 `/index.html`、`/trace.js`、五个旧 API 行为保持。
- `/app/` 使用独立静态资产，不退回旧 `PAGE`。
- `agent/webui_app.py` 直接运行必须能启动同一 Handler。
- 拆分前固定的旧页面关键 DOM 和接口字符串必须继续出现在实际响应中。

### 兼容清单契约

机器可读文件：`docs/refactor/evidence/B7/compatibility-inventory.json`。

每项字段：

```json
{
  "id": "http.prefill",
  "kind": "http",
  "entry": "POST /api/prefill",
  "callers": [],
  "status": "keep",
  "evidence": [],
  "retirement_gate": ""
}
```

状态只允许：

- `keep`：仍有调用方或用户兼容价值，本批明确保留。
- `retire_candidate`：当前未发现调用方，但仍需用户拍板，不得在本批删除。
- `retired`：调用方为零、迁移记录完整、用户明确批准、删除可单独回滚。

本批默认可把现有入口全部标为 `keep`；不得为了凑“清零”改测试或隐藏调用方。

### 结构审查契约

- `agent/runtime/**` 不得导入 `webui`、`webui_app`、`api`。
- `agent/extraction/**`、`agent/audit/**` 不得导入 `webui`、`webui_app`、`api`。
- `web/**/*.js` 不得调用五个旧 `/api/prefill|analyze|announcement|ask|search` 路径；旧页面位于 `web/legacy/`，只允许排除该目录后审查新工作台。
- `agent/webui.py <=300` 行。
- 其他文件超过 `1200` 行或 `80KiB` 只输出警告，不直接失败；警告写入 `docs/refactor/evidence/B7/structure-review.json`。

## 五、实施步骤

1. 新增 `agent/test_b7.py` 第一组 RED：断言 CI 文件、质量脚本、共享脱敏、Web 拆分模块和兼容 inventory 尚不存在；运行专项必须失败。
2. 新增 `agent/runtime/redaction.py`，先写事件、run.json、bridge payload 三条负样本测试，再替换 bridge 本地规则。
3. 在 `agent/webui_http.py` 集中安全头，先让 `/app/`、旧页、JSON、SSE、页图安全头测试变红，再接入所有响应路径。
4. 在 `agent/api/routes.py` 增加对比 PDF 数量和总量门，补 4/5 份与 79/81MiB 边界测试。
5. 建立 `web/legacy/index.html`，逐字迁出旧 `PAGE`；建立 `webui_page.py`、`webui_pipeline.py`、`webui_http.py`、`webui_app.py`，保持对象身份和旧名转出。
6. 把 `agent/webui.py` 收敛为兼容 facade 和 launcher；更新源码位置断言，旧路由兼容测试不得改期望。
7. 修改 `Run.sources` 归档 `web/**` 与 CI 工作流，补历史运行源码包测试。
8. 新增 `check_sensitive.py`、`check_docs.py`、`review_structure.py`，每个脚本先写正负样本测试，再运行仓库级检查。
9. 新增 `.github/workflows/ci.yml`，本地逐条运行与工作流完全相同的命令。
10. 建立兼容清单和 inventory；逐项从 `git grep`、测试和文档收集调用证据，不凭印象标 `retired`。
11. 新增发布冒烟脚本，启动临时端口检查 `/app/`、`/`、`/api/runs` 和 `webui_app.py` 回退入口。
12. 在干净虚拟环境执行完整验收；执行 R1-R10 反向检查并保存原始 JSON。
13. 只有用户批准 commit/push 后，才读取 GitHub Actions 结果；否则 CI 外部运行栏保持“待外部证据”。
14. 更新 TDD-B7、施工单状态、两个重构索引和项目记忆；只在原目标逐条结账后写“已收工”。

## 六、验收命令与预期结果

### B7 专项

```powershell
python -B -m unittest discover -s agent -p 'test_b7.py' -v
```

预期：脱敏、安全头、上传数量与总量、代理限流、会话取消、兼容清单、Web 拆分、归档和 CI 契约全部通过。

### 质量脚本

```powershell
python -B scripts/check_sensitive.py
python -B scripts/check_docs.py
python -B scripts/review_structure.py
python -B scripts/b7_release_smoke.py
```

预期：

- 敏感扫描：仓库 tracked 文本 0 命中；临时正样本必须命中。
- 文档检查：UTF-8 正常、围栏闭合、本地链接存在、控制字符 0。
- 结构审查：明确反向依赖 0、旧 API 新前端调用 0、`webui.py <=300`；体量警告只记录。
- 发布冒烟：新旧入口 HTTP 200，临时服务能正常关闭，退出码 0。

### 既有与全量回归

```powershell
python -B -m unittest discover -s agent -p 'test.py' -v
python -B -m unittest discover -s agent -p 'test_competition.py' -v
python -B -m unittest discover -s agent -p 'test_skill_mcp.py' -v
python -B -m unittest discover -s agent -p 'test*.py'
python -B -m compileall -q agent scripts
git -c safe.directory='D:/Desktop/金融人工智能' -C 'D:\Desktop\金融人工智能' diff --check
```

预期：

- B6 原有 272 条全部保留。
- 全量测试在新增 B7 后全部通过，原设计 `skipped=1` 不变。
- 编译退出码 0。
- 差距检查无空白错误，仅允许既存 LF/CRLF 提示。

### 干净环境

在 `D:\codex\tmp\fin-ai-b7-venv` 建临时虚拟环境，不写仓库：

```powershell
python -m venv D:\codex\tmp\fin-ai-b7-venv
D:\codex\tmp\fin-ai-b7-venv\Scripts\python.exe -m pip install -r requirements.txt
D:\codex\tmp\fin-ai-b7-venv\Scripts\python.exe -B scripts/check_sensitive.py
D:\codex\tmp\fin-ai-b7-venv\Scripts\python.exe -B scripts/check_docs.py
D:\codex\tmp\fin-ai-b7-venv\Scripts\python.exe -B scripts/review_structure.py
D:\codex\tmp\fin-ai-b7-venv\Scripts\python.exe -B -m compileall -q agent scripts
D:\codex\tmp\fin-ai-b7-venv\Scripts\python.exe -B -m unittest discover -s agent -p 'test*.py'
```

预期：与工作流命令一致，全部退出码 0。

### GitHub Actions

干净环境最终证据是 GitHub Actions 运行记录。

- 未获准 commit/push：只记录“本地等价验证通过，远端 CI 待证据”，不能标达标。
- 获准 push 后：记录 workflow 名称、commit SHA、矩阵 Python 版本和运行 URL。
- 任一矩阵失败：B7 不进入收工，先修复后重跑。

## 七、反向检查

### R1：敏感扫描器正样本必须红

- 失败注入：在临时候选文本中放入运行时拼出的测试密钥。
- 期望结果：扫描器退出码非零，输出只有规则名和位置，不回显密钥。
- 若保持绿色说明：扫描器没有扫文本或正则被写错。

### R2：脱敏门移除必须红

- 失败注入：临时让事件 payload 跳过 `redaction.redact_value`。
- 期望结果：事件和 `run.json` 负样本测试发现测试密钥或 `messages` 字段。
- 若保持绿色说明：测试只看字段名，没有真读落盘内容。

### R3：安全头移除必须红

- 失败注入：临时移除 `/app/` 的 CSP 或 API 的 `Cache-Control: no-store`。
- 期望结果：HTTP 安全头测试失败。
- 若保持绿色说明：测试只断言 200，没有检查响应头。

### R4：上传总量门移除必须红

- 失败注入：临时跳过合计 80MiB 和 4 份对比 PDF 检查。
- 期望结果：边界测试错误地接受超限请求。
- 若保持绿色说明：只测了单文件，没有测总量和数量。

### R5：代理欺骗必须红

- 失败注入：未配置受信代理时临时采用 `X-Forwarded-For` 作为客户端 IP。
- 期望结果：限流绕过负样本失败。
- 若保持绿色说明：只测了函数返回值，没有测 HTTP 路径。

### R6：旧 API 删除必须红

- 失败注入：临时从 `Handler.do_POST` 移除 `/api/search`。
- 期望结果：旧兼容测试失败。
- 若保持绿色说明：兼容清单没有真连路由。

### R7：旧页面迁移丢内容必须红

- 失败注入：从 `web/legacy/index.html` 删除 `/api/ask` 或关键 DOM。
- 期望结果：旧页面响应快照和前端契约测试失败。
- 若保持绿色说明：只测文件存在，没有读响应内容。

### R8：`webui.py` 重新塞回实现必须红

- 失败注入：在 `webui.py` 临时定义第二份 `Handler` 或让文件超过 300 行。
- 期望结果：对象身份或结构门失败。
- 若保持绿色说明：拆分门没有检查对象来源和体量。

### R9：归档漏 Web 资产必须红

- 失败注入：从 `Run.sources` 临时移除 `web/legacy/index.html`。
- 期望结果：历史运行源码包测试失败。
- 若保持绿色说明：归档测试只检查 `agent/*.py`。

### R10：兼容清单隐藏调用方必须红

- 失败注入：把已有调用方的旧入口标成 `retired`。
- 期望结果：兼容清单校验失败。
- 若保持绿色说明：清单不是从调用证据校验，只是手填 JSON。

### R11：文档坏链必须红

- 失败注入：在临时 docs 目录加入不存在的本地 Markdown 链接。
- 期望结果：`check_docs.py` 退出码非零。
- 若保持绿色说明：文档检查只查 UTF-8，没有解析链接。

## 八、回滚、保护线与风险

- CI 回滚：删除 `.github/workflows/ci.yml`；不影响本地运行。
- 脱敏回滚：恢复 `runtime/events.py`、`runtime/run_manager.py`、`api/runtime_bridge.py`，保留新增 `runtime/redaction.py` 不影响未引用路径。
- Web 拆分回滚：恢复原 `agent/webui.py`，删除 `webui_page.py`、`webui_pipeline.py`、`webui_http.py`、`webui_app.py` 和 `web/legacy/index.html`；旧入口仍可单独回退。
- 安全头回滚：恢复 `webui_http.py` 响应头；不触碰路由和业务数据。
- 上传门回滚：恢复 `api/routes.py` 的旧参数边界；必须同步撤下错误文案和测试，不能只留测试。
- 兼容清单回滚：删除新增清单和 inventory；不删除任何旧入口。
- 保护线：`/app/`、`/`、`/trace.js`、五个旧 API、新 `/api/runs*`、MCP、CLI、B6 全量 272 条均保持。
- 风险：CSP 误伤旧内联页面；用旧页脚本执行、旧 API 200 和浏览器探针拦截。
- 风险：把密钥扫描设为警告导致门失效；CI 禁止 `continue-on-error`。
- 风险：过度脱敏影响财务文本；用正常中文、股票代码、财务字段和控制组验证。
- 风险：拆 `webui.py` 后旧私有名漏转；用全顶层 name binding 和对象身份测试拦截。
- 风险：为满足 300 行把逻辑复制到两个地方；用 `webui.py` 零实现定义和对象身份测试拦截。
- 风险：未 push 却宣称 CI 达标；施工单和收工模板明确保留“远端 CI 待证据”。
- 风险：为退役把测试改为不再调用旧入口；兼容清单必须查看真实调用方，禁止用改测试制造零调用。

## 九、实施检查点

| 检查点 | 状态 | 证据 |
| --- | --- | --- |
| B7 施工单冻结 | 已完成 | 本文件 |
| B7 RED 测试 | 已完成 | `agent/test_b7.py` 15 条专项通过 |
| 共享脱敏 | 已完成 | `agent/runtime/redaction.py`；事件/run.json/API/SSE 负样本通过 |
| 安全头和上传边界 | 已完成 | `/app/`、旧页、JSON、SSE、页图头与 4/5、80MiB 门通过 |
| Web 兼容拆分 | 已完成 | `webui.py` 108 行；五模块、旧页资产、对象身份和旧路由通过 |
| CI 与质量脚本 | 已完成 | workflow 与四类脚本在干净 venv 等价命令全通过 |
| 兼容 inventory | 已完成 | 11 项旧入口全部保留并登记调用方/证据/退役门 |
| 发布与回滚冒烟 | 已完成 | `/app/`、`/`、`/api/runs`、`/api/search` 与 `webui_app.py` 回退入口通过 |
| 干净环境与全量 | 已完成 | `D:\codex\tmp\fin-ai-b7-venv`；287 条通过、1 条 skipped、编译通过 |
| 远端 CI | 待外部授权 | 无用户批准 commit/push 时保持未验证 |

## 十、收工结账模板

| 原目标 | 状态 | 证据 |
| --- | --- | --- |
| CI 执行测试、编译、文档和敏感扫描 | 未达标 | 干净 venv 本地等价命令通过；GitHub Actions URL 待 commit/push 授权 |
| 代理、限流、上传、会话和取消边界有回归 | 达标 | B7 专项 15 条；R4、R5 |
| 旧入口调用方清零或有明确保留记录 | 达标 | 11 项全部 keep；`B7-兼容清单.md`、inventory、R10 |
| `agent/webui.py <=300` 且旧入口兼容 | 达标 | 108 行；对象身份、旧页面/API；R6-R8 |
| Web 资产进入运行归档 | 达标 | `Run.sources` 含 `web/**` 与 CI；R9 |
| 密钥与原始模型字段不进入公开字段 | 达标 | 事件、run.json、API/SSE 负样本；R1、R2 |
| 文件体量与依赖方向有审查规则 | 达标 | `structure-review.json`，违规 0，1 条体量警告 |
| 发布与回滚演练 | 达标 | `release-smoke.json`，四条入口 200，`webui_app.py` 直接启动 |

独立信源：缺失；本地干净 venv、unittest、发布冒烟和 R1-R11 均属项目自证。GitHub Actions 是待取得的外部执行证据。
待拍板：B7 实施完成后若要 commit/push 以取得远端 CI 证据，必须单独获得用户批准；未批准时不得自行标 CI 达标。
下一批开工门：B7 无下一批；先由用户拍板是否 commit/push 取得远端 CI。远端矩阵通过前不得把 B7 标为收工；不能用“本地全绿”代替全局完成度判断。
