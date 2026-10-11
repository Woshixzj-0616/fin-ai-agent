# B7 TDD 证据 · 安全加固、CI 与旧入口治理

> 来源施工单：[B7-安全加固-CI与旧入口治理.md](施工单/B7-安全加固-CI与旧入口治理.md)
> 日期：2026-10-11 · 收口：2026-10-12
> 结论：B7 已完成并取得远端独立执行证据。旧入口全部明确保留，Web 兼容拆分、脱敏、上传与代理边界、质量门、发布冒烟和反向检查均已落地；GitHub Actions 的 Python 3.11/3.12 双矩阵通过。

## RED / GREEN

### RED

施工单冻结后先新增 `agent/test_b7.py`，在共享脱敏、Web 拆分模块、质量脚本、CI 和兼容 inventory 尚不存在时建立第一批 RED。原始终端输出未单独落盘，当前可复核证据是施工单中的预期失败项和已完成的反向检查 R1-R11。

### GREEN

实现落点：

- `agent/runtime/redaction.py`：统一凭证文本与原始模型字段脱敏。
- `agent/runtime/events.py`、`agent/runtime/run_manager.py`、`agent/api/runtime_bridge.py`、`agent/api/routes.py`：接入共享脱敏。
- `agent/webui_page.py`、`agent/webui_pipeline.py`、`agent/webui_http.py`、`agent/webui_app.py`：承载旧页面、旧流水线、HTTP 安全边界和直接回退启动。
- `agent/webui.py`：收敛为 108 行兼容门面。
- `web/legacy/index.html`：原旧页面 UTF-8 文本迁出。
- `scripts/check_sensitive.py`、`scripts/check_docs.py`、`scripts/review_structure.py`、`scripts/b7_release_smoke.py`、`scripts/b7_reverse_checks.py`：质量、发布和反向检查。
- `.github/workflows/ci.yml`：Python 3.11/3.12 固定质量命令。

专项命令：

```powershell
python -B -m unittest discover -s agent -p 'test_b7.py' -v
```

结果：`Ran 15 tests`，`OK`。

覆盖：

| 保证 | 证据 |
| --- | --- |
| CI 文件、固定命令、双 Python 矩阵且无 `continue-on-error` | `B7ArtifactTests` |
| 共享脱敏规则、正常财务文本控制组 | `B7RedactionContractTests` |
| 事件 JSONL、`run.json`、API 响应和 SSE 真实负样本 | `B7RedactionPersistenceTests`、`B7HttpSecurityTests` |
| `/app/`、旧页、JSON、SSE、页图安全头 | `B7HttpSecurityTests` |
| 对比材料 4/5 份和 80MiB 总量门 | `B7UploadBoundaryTests` |
| 无代理时伪造 `X-Forwarded-For` 不能绕过限流 | `B7ProxyRateLimitTests` |
| 旧页面、旧 API、兼容对象身份和 `webui.py <= 300` | `B7WebCompatibilityTests` |
| `Run.sources` 归档 Web 资产与 CI | `B7ArchiveAndQualityTests` |
| 兼容 inventory 完整字段与保留门 | `B7InventoryTests` |

## 反向检查

只读/临时注入脚本：`scripts/b7_reverse_checks.py`
原始结果：`docs/refactor/evidence/B7/reverse-checks.json`

| 变异 | 预期 | 实际 |
| --- | --- | --- |
| R1 敏感扫描正样本 | 正样本退出非 0 且不回显密钥 | `normal=0, injected=1, secret_echo=False` |
| R2 移除脱敏 | 临时旁路后磁盘/API 负样本出现密钥 | `normal_hit=False, injected_hit=True` |
| R3 移除安全头 | `/app/` 的 CSP 缺失 | `normal_csp=True, injected_csp=False` |
| R4 上传数量/总量门 | 5 份或超总量接受 | 正常拒绝，移除门后接受 |
| R5 伪造代理头 | 未配置代理时 XFF 可绕过 | `normal_has_429=True, injected_has_429=False` |
| R6 删除旧 API | `/api/search` 从 200 变 404 | `normal=200, injected=404` |
| R7 旧页面丢内容 | `/api/ask` 缺失 | 正常有、注入缺失 |
| R8 `webui.py` 超长 | 结构审查变红 | 真实 0 违规，注入 301 行触发 |
| R9 归档漏 Web 资产 | `Run.sources` 缺失旧页 | 真实包含、假仓库缺失 |
| R10 清单隐藏调用方 | `retired` 仍带调用方 | 正常合规、注入违规 |
| R11 文档坏链 | 本地链接不存在时退出非 0 | `normal=0, injected=1` |

R1-R11 全部按预期变红。生成结果 `ok=true`。

## 回归与 CI 等价验证

| 命令 | 结果 |
| --- | --- |
| `python -B -m unittest discover -s agent -p 'test_b7.py' -v` | `Ran 15 tests`，`OK` |
| `python -B -m unittest discover -s agent -p 'test*.py'` | `Ran 322 tests`，`OK (skipped=1)` |
| `python -B -m unittest test_competition -v`（cwd `agent/`） | `Ran 51 tests`，`OK (skipped=1)` |
| `python -B -m unittest test_skill_mcp -v`（cwd `agent/`） | `Ran 13 tests`，`OK` |
| `python -B scripts/check_sensitive.py` | 通过，扫描 170 个文本文件，无命中 |
| `python -B scripts/check_docs.py` | 通过，59 个 Markdown |
| `python -B scripts/review_structure.py` | 通过，违规 0，`webui.py` 108 行 |
| `python -B scripts/b7_release_smoke.py` | `/app/`、`/`、`/api/runs`、`/api/search` 均为 200 |
| `python -B scripts/b7_reverse_checks.py` | R1-R11 全部 PASS |
| `python -B -m compileall -q agent scripts` | 退出码 `0` |
| `git diff --check` | 退出码 `0`，仅既存 LF/CRLF 提示 |

干净虚拟环境：`D:\codex\tmp\fin-ai-b7-venv`，Python 3.14.6，安装 `pymupdf==1.28.2`、`httpx==0.28.1` 后逐条执行同一套 CI 命令，结果同上；合并远端升级后 `Ran 322 tests`，`OK (skipped=1)`。

## GitHub Actions 独立信源

- Workflow：`CI`，触发方式 `push`。
- 提交：`84c3f42e383b45a105c10712bc613adf620ac59a`。
- 运行：[38154092186](https://github.com/Woshixzj-0616/fin-ai-agent/actions/runs/38154092186)，总状态 `completed/success`。
- `test (3.11)`：job `114513355827`，`completed/success`。
- `test (3.12)`：job `114513355690`，`completed/success`。
- 两个 job 的敏感扫描、文档、结构、编译和全量测试步骤均为 `success`。
- 仅保留非阻断平台提示：Node 20 action 弃用提醒与 `ubuntu-latest` 后续迁移提示，不影响矩阵结论。

启动兼容：

```powershell
python -B agent/webui.py --help
cd agent; python -B webui.py --help
python -B agent/webui_app.py --help
```

三条命令均退出 `0`。`webui_app.py` 由发布冒烟实际启动并接受 HTTP 请求。

## 原目标逐条兑现

| 原目标 | 状态 | 证据 |
| --- | --- | --- |
| CI 执行测试、编译、文档和敏感扫描 | 达标 | GitHub Actions `38154092186` 在 Python 3.11/3.12 双矩阵成功；本地干净 venv 同套命令通过 |
| 代理、限流、上传、会话和取消边界有回归 | 达标 | B7 专项、R4、R5；既有会话/取消测试保持 |
| 旧入口调用方清零或有明确保留记录 | 达标 | `B7-兼容清单.md` 与 inventory；本批无退役，R10 生效 |
| `agent/webui.py <=300` 且旧入口兼容 | 达标 | 108 行、对象身份、旧页/API 测试；R6-R8 生效 |
| Web 资产进入运行归档 | 达标 | `Run.sources` 含 `web/**` 与 `.github/workflows/ci.yml`；R9 生效 |
| 密钥与原始模型字段不进入公开字段 | 达标 | 事件、`run.json`、API/SSE 负样本；R1-R3 生效 |
| 文件体量与依赖方向有审查规则 | 达标 | `structure-review.json`，违规 0，1 条体量警告 |
| 发布与回滚演练 | 达标 | `release-smoke.json`；`webui_app.py` 直接启动，旧页和旧 API 保持 |
| 兼容清单完整 | 达标 | `compatibility-inventory.json`，无空白状态，所有 keep 均有调用方和证据 |

## 独立信源

独立信源：GitHub Actions [run 38154092186](https://github.com/Woshixzj-0616/fin-ai-agent/actions/runs/38154092186)，提交 `84c3f42e383b45a105c10712bc613adf620ac59a`。Python 3.11 与 3.12 两个 job 均 `completed/success`。

本地 unittest、干净 venv、只读脚本、临时 monkeypatch 和发布冒烟仍属项目自证；远端 Actions 提供独立的干净环境执行记录。

## 待拍板与已知边界

- B7 无新增待拍板事项；远端 Python 3.11/3.12 矩阵已取得并全部通过。
- Actions 的 Node 20 与 `ubuntu-latest` 迁移提示属于平台维护预警，不是本批失败；后续升级 action major 时单独验证。
- 兼容清单基于仓库内调用方证据；仓库外第三方调用方仍需在退役前另行确认。
