# 模块来源清单

本表记录本地模块化副本采用的来源，便于以后回溯和合并。复制时保留了来源工作树中的当前文件，因此“已提交版本”不等于“本副本只有已提交内容”。

| 模块 | 本地来源目录 | 来源分支 / 提交 | 当前状态 | 已归档位置 |
|---|---|---|---|---|
| Part 1 Business | `财报分析并行工作区/任务01-业务与经营背景` | `parallel/task-01-business` / `65826ff` | 有未提交改进 | `modules/part1_business` |
| Part 2 Profit | `财报分析并行工作区/任务02-盈利来源与变化` | `parallel/task-02-profit` / `db9b38c` | 工作树干净 | `modules/part2_profit` |
| Part 3 Assets | `财报分析并行工作区/任务03-资产质量` | `parallel/task-03-assets` / `9523806` | 工作树干净 | `modules/part3_assets` |
| Part 4 Cashflow | `财报分析并行工作区/任务04-现金流` | `parallel/task-04-cashflow` / `c165d22` | 有未提交改进 | `modules/part4_cashflow` |
| Part 5 Solvency | `财报分析并行工作区/任务05-偿债能力` | `parallel/task-05-solvency` / `5b0f2f6` | 有未提交改进 | `modules/part5_solvency` |
| Part 6 Disclosure | `财报分析并行工作区/任务06-披露与风险_V3.6.2` | `version/v3.6.2-module-six-workbench` / `9733b5d` | 有未提交改进 | `modules/part6_disclosure` |

## 迁移注意

- Part 1、2 原来位于 `backend/modules/`，本副本已复制到根目录 `modules/part1_business`、`modules/part2_profit`。
- 共同底座的旧模块目录暂存为 `backend/modules_legacy_business` 和 `backend/modules_legacy_profit`，用于接入期间回看，不作为新结构的专业模块入口。
- 各模块的独立网页组件已经放到 `frontend/partX_name/`；它们的旧启动脚本放在 `launchers/source/`，待统一端口和路径适配后再作为正式启动器。
- `.env`、API Key、数据库、运行日志、PDF 和 `node_modules` 没有作为本地模块迁移内容写入版本库。
