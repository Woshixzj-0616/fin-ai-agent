# 来源工作台差异补丁

这里保存并行任务工作树中对共同底座或公共页面的未提交差异，作为迁移时的参考，不自动套用到新底座。

- `part1_shared_workbench.patch`：模块一工作台对公共审阅页面的改动。
- `part4_shared_workbench.patch`：模块四的公共差异记录位；其独立后端源文件已直接归档到 `backend/workbenches/part4_cashflow.py`。
- `part5_shared_workbench.patch`：模块五对公共应用、注册表和页面的改动。
- `part6_shared_workbench.patch`：模块六对公共应用、数据库、注册表和页面的改动。

这些补丁只在确认接口需求后逐项挑选。合并时优先把公共能力抽到 `backend/core` 或 `backend/module_registry.py`，不要整份覆盖共同底座。
