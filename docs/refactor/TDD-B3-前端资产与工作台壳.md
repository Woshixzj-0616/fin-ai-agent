# B3 TDD 证据 · 独立前端资产与工作台壳

> 施工单：[B3-独立前端资产与工作台壳.md](施工单/B3-独立前端资产与工作台壳.md)
> 日期：2026-10-11
> 结论：B3 原目标六项全部达标；真实提交、SSE、轨迹与结果绑定仍按计划留在 B4。

## 用户旅程

- 作为投研用户，我打开 `/app/`，可以看到实际工作台骨架和明确空态，不用面对营销页、空白页或伪造进度。
- 作为桌面用户，我可以在 `1440x900` 下同时看到固定 `288px` 运行栏和主工作区。
- 作为窄屏用户，我可以在 `390x844` 下使用顶部“运行”按钮打开、关闭运行抽屉，页面不横向溢出。
- 作为依赖旧入口的用户，我访问 `/`、`/index.html`、`/trace.js` 和旧 API 时，行为保持兼容。

## RED / GREEN

### RED

命令：

```powershell
python -B -m unittest discover -s agent -p 'test_web_assets.py' -v
```

结果：`FAILED (failures=13, errors=5)`。

失败都由预期缺口引起：

- `/app`、`/app/` 尚未接入，返回 `404`。
- `web/` HTML、CSS、JS 尚不存在。
- 旧 `/`、`/index.html`、`/trace.js` 兼容测试当时已通过。

### GREEN

命令：

```powershell
python -B -m unittest discover -s agent -p 'test_web_assets.py' -v
```

结果：`Ran 11 tests`，`OK`。

保证项：

| # | 保证 | 测试 |
| --- | --- | --- |
| 1 | `/app` 与 `/app/` 返回独立工作台 HTML，并使用 `no-store` | `WebAssetHttpTests.test_app_entries_serve_standalone_html` |
| 2 | 12 个稳定 DOM 节点存在 | `test_dom_contract_contains_all_stable_nodes` |
| 3 | CSS、JS、SVG 的 MIME、缓存头和长度正确 | `test_styles_and_scripts_have_contract_headers` |
| 4 | 不存在文件、目录和省略扩展名均返回 `404` | `test_missing_and_directory_paths_are_not_guessed` |
| 5 | 原始、编码、反斜杠和 NUL 越界全部返回 `404` | `test_static_path_traversal_is_rejected` |
| 6 | `/` 与 `/index.html` 仍逐字节等于原 `PAGE`，`/trace.js` 仍可访问 | `test_old_page_and_trace_asset_remain_compatible` |
| 7 | HTML 无内联 CSS/JS、无远程资源 | `test_index_has_no_inline_or_remote_assets` |
| 8 | 前端资产无 HTTP、CDN、外部字体依赖 | `test_assets_do_not_depend_on_network_or_remote_fonts` |
| 9 | 三个视图模块只渲染，不创建 `fetch` 或 `EventSource` | `test_views_are_pure_and_do_not_create_transport_clients` |
| 10 | 状态容器初始值严格等于 B3 契约 | `test_run_state_has_exact_empty_contract` |
| 11 | `main.js` 只接状态、视图和抽屉，不提前接传输 | `test_main_wires_state_views_and_sidebar_without_transport` |

## 回归与静态检查

```powershell
python -B -m unittest discover -s agent -p 'test*.py'
```

结果：`Ran 241 tests`，`OK (skipped=1)`。B2 的 230 条加 B3 的 11 条全部通过，原设计跳过保持 1 条。

```powershell
Get-ChildItem -Path web\js -Recurse -Filter *.js |
  ForEach-Object {
    Get-Content -Raw -Encoding UTF8 -LiteralPath $_.FullName |
      node --input-type=module --check
  }
```

结果：退出码 `0`，五个 JS 模块全部通过。

```powershell
python -B -m compileall -q agent scripts
git diff --check
```

结果：两项退出码均为 `0`。

Ruff：

- `agent/test_web_assets.py`：`All checks passed!`
- `agent/webui.py` 全量 Ruff 仍报告既有 B1/B2/旧代码的广异常捕获、时区和小型导入问题；本批未借机重构这些非 B3 代码。

## 浏览器证据

只读探针：`D:\codex\tmp\b3_visual_check_v2.py`
原始输出：`docs/refactor/evidence/B3/visual-check.json`

结果：

- 控制台错误：`0`
- 请求失败：`0`
- `>=400` 响应：`0`
- 桌面 `1440x900`：`scrollWidth=1440`，侧栏 `288px`，主区未横向溢出。
- 移动 `390x844`：`scrollWidth=390`，闭合时侧栏隐藏，打开后侧栏 `0..340px`，`aria-expanded=true`，关闭后 `false`。
- 移动端故障注入：覆盖响应式为桌面两栏后，侧栏在 `390px` 下变为可见且顶部切换按钮消失，证明响应式门不是恒真。

截图：

- `docs/refactor/evidence/B3/shell-desktop.png`
- `docs/refactor/evidence/B3/shell-mobile.png`

## 独立信源

独立信源：缺失。

本地只读 Playwright 探针的原始 JSON 已保存；它不是外部 agent 结论，因此不冒充独立复核。

## 已知边界

- B3 不提交真实运行，不消费 SSE，不渲染真实轨迹、证据、结果或追问。
- 表单提交按钮保持禁用；页面只显示真实空态。
- B4 必须继续使用 `run_id + SSE seq + Last-Event-ID`，并新增唯一 HTTP 客户端与唯一 SSE 客户端。
