"""现场演练：对多家公司跑 live 抽取，产出「评审现场可实时处理」留痕。

用法（仓库根）：
  python scripts/live_drill.py
  python scripts/live_drill.py --codes 600276,300760,000002,601318

输出 results/live_drill.md + results/live_drill.json（含每家证据数、待复核、耗时）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

from main import extract_selected  # noqa: E402
from materials import ROOT as AGENT_ROOT, Run, write_json  # noqa: E402

DEFAULT = [
    ("600276", 2024, "恒瑞医药"),
    ("300760", 2024, "迈瑞医疗"),
    ("000002", 2023, "万科A"),
    ("601318", 2024, "中国平安"),
]


def main() -> int:
    only = None
    if "--codes" in sys.argv:
        only = [p.strip() for p in sys.argv[sys.argv.index("--codes") + 1].split(",") if p.strip()]
    run = Run(AGENT_ROOT, "live_drill", {"codes": only or [c for c, _, _ in DEFAULT]})
    print(f"运行记录：{run.folder}", flush=True)
    rows = []
    for code, year, name in DEFAULT:
        if only and code not in only:
            continue
        t0 = time.time()
        try:
            _m, facts, failed = extract_selected(AGENT_ROOT, run, code, year, False)
            issues = sum(1 for f in facts if f.get("issues"))
            core = sum(1 for f in facts if f["metric"] in {
                "revenue", "parent_net_profit", "adjusted_parent_net_profit",
                "operating_cash_flow"} and f["period_year"] == year and not f.get("issues"))
            rows.append({
                "code": code, "year": year, "name": name, "ok": True,
                "facts": len(facts), "core_clean": core, "issues": issues,
                "failed": len(failed or []), "seconds": round(time.time() - t0, 2),
            })
            print(f"  {code} {year} {name}：证据 {len(facts)}，核心干净 {core}，"
                  f"待复核 {issues}，{rows[-1]['seconds']}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            rows.append({"code": code, "year": year, "name": name, "ok": False,
                         "error": str(exc), "seconds": round(time.time() - t0, 2)})
            print(f"  {code} {year} {name} 失败：{exc}", flush=True)

    ok = sum(1 for r in rows if r["ok"])
    report = ["# 现场实时处理演练记录", "",
              f"- 公司 **{len(rows)}** 家 · 成功 **{ok}** · 失败 **{len(rows) - ok}**",
              f"- 命令：`python scripts/live_drill.py` · 运行目录 `{run.folder}`",
              "",
              "| 代码 | 年 | 公司 | 结果 | 证据 | 核心干净 | 待复核 | 秒 |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["ok"]:
            report.append(f"| {r['code']} | {r['year']} | {r['name']} | 成功 | {r['facts']} | "
                          f"{r['core_clean']} | {r['issues']} | {r['seconds']} |")
        else:
            report.append(f"| {r['code']} | {r['year']} | {r['name']} | 失败 | — | — | — | {r['seconds']} |")
            report.append(f"| | | | 原因 | {r.get('error', '')} | | | |")
    report += ["", "## 现场口径", "",
               "- 成功 = 能抽出带页码的财务证据并落 `evidence.json` 留痕",
               "- 「核心干净」= 营收/归母/扣非/经营现金流四条无待复核字段",
               "- 失败请看人话原因（扫描版 / 版式 / 非年报）", ""]
    out = ROOT / "results"
    (out / "live_drill.md").write_text("\n".join(report), encoding="utf-8")
    write_json(out / "live_drill.json", rows)
    run.finish(status="ok" if ok == len(rows) else "partial", ok=ok, total=len(rows))
    print(f"演练 {ok}/{len(rows)} 成功 · {out / 'live_drill.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
