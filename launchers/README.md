# 启动器说明

`start_module.ps1` 和六个 `start_part*.cmd` 是本目录第一版正式启动器。它们使用共同的 `backend.standalone_workbench` 外壳，专业分析仍从 `modules/partX_name` 的注册入口执行。

`source/` 里另外保存从六个任务目录复制来的原启动脚本，便于追溯。它们仍可能引用原项目的绝对路径，不能直接视为本目录的正式启动器。

正式启动器要在统一入口完成后再生成，端口暂定为：

| 模块 | 端口 |
|---|---:|
| part1_business | 8101 |
| part2_profit | 8102 |
| part3_assets | 8103 |
| part4_cashflow | 8104 |
| part5_solvency | 8105 |
| part6_disclosure | 8106 |

例如，双击 `start_part3_assets.cmd` 后，在浏览器打开 `http://127.0.0.1:8103`。第一次打开页面可直接配置 DeepSeek Key，再上传 PDF。

启动器必须使用本目录相对路径，不能依赖 `D:\ChatGPT项目\金融AI智能体` 等旧工作区路径。
