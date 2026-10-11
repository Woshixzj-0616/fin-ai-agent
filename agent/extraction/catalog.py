"""指标目录、别名与默认抽取口径。"""
from __future__ import annotations

METRICS: dict[str, tuple[str, list[str]]] = {
    "total_revenue": ("营业总收入", ["营业总收入"]),
    "revenue": ("营业收入", ["营业收入", "营收"]),
    "parent_net_profit": ("归母净利润", [
        "归属于上市公司股东的净利润", "归属于母公司股东的净利润",
        "归属于母公司所有者的净利润", "归属于本行股东的净利润",
        "归属于本公司股东的净利润",
        "归母净利润", "归母净利"]),
    "adjusted_parent_net_profit": ("扣非归母净利润", [
        "归属于上市公司股东的扣除非经常性损益的净利润",
        "归属于母公司股东的扣除非经常性损益的净利润",
        "归属于母公司所有者的扣除非经常性损益的净利润",
        "扣除非经常性损益后的归属于上市公司股东的净利润",
        "扣除非经常性损益后归属于上市公司股东的净利润",
        "扣除非经常性损益后归属于母公司股东的净利润",
        "扣除非经常性损益后归属于本行股东的净利润",
        "归属于本公司股东的扣除非经常性损益的净利润",
        "归属于母公司股东的扣除非经常性损益后的净利润",
        "扣除非经常性损益后归属于本公司股东的净利润",
        "扣非归母净利润", "扣非归母净利", "扣非净利润", "扣非净利"]),
    "operating_cash_flow": ("经营现金流净额", [
        "经营活动产生的现金流量净额", "经营现金流净额", "经营活动现金流量净额"]),
    "basic_eps": ("基本每股收益", ["基本每股收益", "每股收益"]),
    "diluted_eps": ("稀释每股收益", ["稀释每股收益"]),
    "deducted_basic_eps": ("扣非基本每股收益", [
        "扣除非经常性损益后的基本每股收益", "扣非基本每股收益", "扣非每股收益", "扣非EPS"]),
    "weighted_roe": ("加权平均净资产收益率", [
        "加权平均净资产收益率", "净资产收益率（加权平均）", "净资产收益率(加权平均)",
        "加权ROE", "净资产收益率"]),
    "deducted_weighted_roe": ("扣非加权平均净资产收益率", [
        "扣除非经常性损益后的加权平均净资产收益率",
        "扣非加权平均净资产收益率", "扣非加权ROE", "扣非ROE"]),
    "total_assets": ("总资产", ["总资产", "资产总额", "资产总计"]),
    "parent_equity": ("归母净资产", [
        "归属于上市公司股东的净资产", "归属于母公司股东的净资产",
        "归属于母公司所有者权益", "归属于上市公司股东的所有者权益",
        "归属于本行股东权益", "归属于母公司股东权益", "归属于本公司股东权益",
        "归属于本行股东的净资产", "归属于本公司股东的净资产",
        "归母净资产", "归母权益"]),
    "book_value_per_share": ("每股净资产", [
        "归属于上市公司股东的每股净资产", "归属于母公司股东的每股净资产",
        "归属于上市公司普通股股东的每股净资产", "归属于本行普通股股东的每股净资产",
        "每股净资产"]),
    "revenue_after_deduction": ("营业收入扣除后金额", [
        "营业收入扣除后金额", "扣除后营业收入"]),  # 蓝筹样本无此行（退市风险警示股才有），保留给现场新数据
}

# 可数值裁决子集：check_claim 能对这 8 个出确定性对错；其余只做取数/溯源。
ADJUDICABLE = frozenset({
    "revenue", "total_revenue", "parent_net_profit", "adjusted_parent_net_profit",
    "operating_cash_flow", "basic_eps", "weighted_roe", "total_assets",
})

# 别名长优先，同长按目录序 —— 「营业总收入」先于「营业收入」，「扣非基本每股收益」先于「基本每股收益」。
ALIASES: list[tuple[str, str, int]] = []
for _idx, (_key, (_name, _names)) in enumerate(METRICS.items()):
    for _alias in _names:
        ALIASES.append((_alias, _key, _idx))
ALIASES.sort(key=lambda t: (-len(t[0]), t[2]))

# 抽不出来就报错的指标（gold 与核查器的最小集合）；其余缺了只记录，不阻断。
REQUIRED = ("revenue", "parent_net_profit", "adjusted_parent_net_profit", "operating_cash_flow")
PARENT_METRICS = {"parent_net_profit", "adjusted_parent_net_profit", "parent_equity"}
CONSOLIDATED_METRICS = {"revenue", "operating_cash_flow", "total_revenue",
                        "revenue_after_deduction", "total_assets"}


def default_scope(metric: str) -> tuple[str, dict | None]:
    """项目默认上下文：利润/权益类归母、收入/现金流/资产合并口径。旁证成功后再升级为 corroborated。"""
    if metric in PARENT_METRICS:
        return "parent_shareholders", {"basis": "explicit_metric_label"}
    if metric in CONSOLIDATED_METRICS:
        return "consolidated", {"basis": "project_default_consolidated"}
    return "unknown", None
