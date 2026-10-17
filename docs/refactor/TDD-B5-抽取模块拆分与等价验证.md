# B5 TDD 证据 · 抽取模块拆分与等价验证

> 来源施工单：[B5-抽取模块拆分与等价验证.md](施工单/B5-抽取模块拆分与等价验证.md)
> 日期：2026-10-11
> 结论：B5 原目标八项全部达标；`extract.py` 只做兼容转出，`extraction/` 成为实现源，固定真实样本迁移前后 canonical 哈希完全一致。

## RED / GREEN

### RED

先写并运行 `agent/test_extraction.py`，在 `agent/extraction/` 尚未建立时得到预期失败：

```text
Ran 10 tests
FAILED (failures=1, errors=3)
```

失败直接指向缺失的 `extraction` 包、反向导入门和缓存指纹覆盖缺口，证明测试不是对现有实现做形状匹配。

### GREEN

新增实现源：

- `agent/extraction/catalog.py`
- `agent/extraction/grid.py`
- `agent/extraction/annual.py`
- `agent/extraction/orchestrator.py`
- `agent/extraction/__init__.py`

`agent/extract.py` 收敛为显式兼容门面；旧调用方继续使用原来的导入路径，对象身份保持单一源。

专项命令：

```powershell
python -B -m unittest discover -s agent -p 'test_extraction.py' -v
```

结果：`Ran 10 tests`，`OK`。

保证项：

| # | 保证 | 测试 |
| --- | --- | --- |
| 1 | 指标目录常量为同一对象 | `ModuleContractTests.test_catalog_constants_are_single_object` |
| 2 | 旧公共和私有名均为直接 re-export | `ModuleContractTests.test_legacy_names_are_direct_reexports` |
| 3 | `extraction/` 不反向导入 `extract.py` | `ModuleContractTests.test_package_does_not_import_legacy_facade` |
| 4 | `extraction/*.py` 变化会改变缓存指纹 | `ModuleContractTests.test_cache_fingerprint_changes_with_extraction_source` |
| 5 | 网格赢家保留，单位等缺失字段由输家补齐 | `MergeContractTests.test_dedupe_keeps_grid_winner_and_fills_missing_unit` |
| 6 | 9 份真实年报与冻结快照逐字段等价 | `RealExtractionEquivalenceTests.test_fixed_nine_reports_are_compared_to_frozen_snapshot` |
| 7 | grid、words、利润表三通道均有样本覆盖 | `RealExtractionEquivalenceTests.test_channel_coverage_matches_baseline` |
| 8 | 9 份报告仍保留当前年核心指标 | `RealExtractionEquivalenceTests.test_all_nine_reports_keep_current_core_metrics` |
| 9 | grid 通道保留页码、bbox、表头定位 | `RealExtractionEquivalenceTests.test_grid_channel_keeps_full_location_fields` |
| 10 | 利润表通道保留页码、bbox、表头定位 | `RealExtractionEquivalenceTests.test_income_statement_channel_keeps_page_location` |

## 固定样本等价

样本：`600519`、`000858`、`600887` × `2023-2025`，共 9 份年报，`use_cache=False` 全量重抽。

| 项目 | 基线 | 拆分后 |
| --- | --- | --- |
| evidence 条数 | 325 | 325 |
| 抽取方式 | grid 208 / words 99 / 利润表 18 | grid 208 / words 99 / 利润表 18 |
| canonical SHA-256 | `3c0865dc5df3096bd44da3f341fa668243edf70c3123cb42e4e948a0300db367` | `3c0865dc5df3096bd44da3f341fa668243edf70c3123cb42e4e948a0300db367` |
| 失败文档 | 0 | 0 |

证据：

- `docs/refactor/evidence/B5/evidence-9-baseline.json`
- `docs/refactor/evidence/B5/evidence-9-after.json`
- `docs/refactor/evidence/B5/evidence-9-baseline.sha256`
- `docs/refactor/evidence/B5/evidence-9-after.sha256`
- `docs/refactor/evidence/B5/baseline-summary.json`
- `docs/refactor/evidence/B5/after-summary.json`

## 反向检查

只读变异脚本：`D:\codex\tmp\b5_reverse_checks.py`
原始结果：`docs/refactor/evidence/B5/reverse-checks.json`

| 变异 | 预期失败 | 实际 |
| --- | --- | --- |
| 从门面删除 `_statement_title` 转出 | 历史兼容测试报 `AttributeError` | `returncode=1` |
| 把 `extract_material` 改成包装副本 | 对象身份测试报不同函数对象 | `returncode=1` |
| 修改年度通道输出 | canonical 快照哈希不一致 | `returncode=1` |
| 从缓存指纹移除 `extraction/` | 指纹变化测试失败 | `returncode=1` |
| 在 `extraction/` 反向 `import extract` | 导入图测试发现违规 | `returncode=1` |

五个变异均按预期变红；脚本在 `finally` 中恢复原文件，`all_restored=true`。

## 全量回归

```powershell
python -B -m unittest discover -s agent -p 'test*.py'
```

结果：`Ran 259 tests in 23.949s`，`OK (skipped=1)`。

```powershell
python -B -m compileall -q agent scripts
git diff --check
```

结果：退出码均为 `0`。`git diff --check` 仅有既存 LF/CRLF 转换提示，无空白错误。

```powershell
%APPDATA%\Python\Python314\Scripts\ruff.exe check agent/extract.py agent/extract_cache.py agent/extraction agent/test_extraction.py
```

结果：`All checks passed!`

只读自证审计：

```powershell
D:\codex\协作\审计\codex_self_audit.cmd --days 7
```

原始摘要：`会话 53 | 工具调用 16704 | 扫描 448.7 MB`；`apply_patch 0 (0.0%) | FileChange 项 582 | 结构化改文件会话 9/53 (17.0%) | here-string 写文档 2253 | 记忆写入严格 855 / 广义 857 | 严格日均 122.1`。该脚本只读，不替代 B5 功能验收或外部独立复核。

## 原目标逐条兑现表

| 原目标 | 状态 | 证据 |
| --- | --- | --- |
| 新 `extraction/` 成为实现源 | 达标 | 模块拆分与对象身份测试；所有实现函数 `__module__` 指向 `extraction.*`。 |
| 旧 `extract.py` 作为兼容门面 | 达标 | 32 个历史公共/私有名显式转出；删除转出的反向检查变红。 |
| 年度摘要表、利润表、合并逻辑分别可测 | 达标 | words 与利润表通道覆盖、页面定位、去重补字段测试全部通过。 |
| 固定真实样本输出等价 | 达标 | 325 条 evidence 的 canonical JSON 和 SHA-256 与基线完全相同。 |
| 缓存指纹覆盖新模块 | 达标 | `extraction/*.py` 纳入指纹；移除覆盖后专项测试变红。 |
| 旧导入、CLI、Web API 与既有测试不需改导入 | 达标 | 全量 259 条通过；现有调用方继续 `from extract import ...`。 |
| evidence schema、事件、页码与 bbox 不变 | 达标 | canonical 快照逐字段比较；三通道定位字段测试通过。 |
| 不越界修改抽取口径 | 达标 | 只迁移与清理无效导入；固定样本哈希零变化。 |

## 独立信源

独立信源：缺失。

本地 unittest、真实年报 canonical 快照和临时变异脚本均属于项目自证证据，不是外部 agent 复核；不冒充独立信源。

## 已知边界

- B5 只验证固定 9 份真实年报的等价性，不证明 70 份全量样本或未来新 PDF 的版式覆盖无变化。
- 兼容门面保留当前已识别的历史调用面；未在仓库中出现的第三方调用方仍需后续以真实导入清单继续核验。
- B6/B7 尚未执行，旧入口和审计、报告模块不在本批范围。

## 待拍板

- B5 无新增拍板事项。
- 下一步默认进入 B6；若初赛材料优先级变化，由用户另行调整批次顺序。
