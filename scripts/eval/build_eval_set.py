"""从 financials.csv 生成错误注入评测集。

产出 data/eval/eval_claims.json：
- accurate：值与抽取长表一致（期望「证据支持」）
- error_value / error_unit / error_year / error_sign：注入可控错误（期望「确认错误」）

gold 来源 = 程序抽取的 financials.csv（核心指标已 70/70）；
每条带 gold_basis 字段，仍待团队人工复签后才能当正式参考答案。
"""
from __future__ import annotations

import csv
import json
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
FIN = ROOT / "data" / "extracted" / "financials.csv"
OUT = ROOT / "data" / "eval" / "eval_claims.json"
DRAFT_DIR = ROOT / "data" / "eval" / "drafts"

# 抽取长表中文指标 → 核查器英文 key
METRIC_MAP = {
    "营业收入": "revenue",
    "归母净利润": "parent_net_profit",
    "扣非归母净利润": "adjusted_parent_net_profit",
    "经营现金流净额": "operating_cash_flow",
}
METRIC_CN = {v: k for k, v in METRIC_MAP.items()}
SCOPE = {
    "revenue": "consolidated",
    "operating_cash_flow": "consolidated",
    "parent_net_profit": "parent_shareholders",
    "adjusted_parent_net_profit": "parent_shareholders",
}
# 评测覆盖的公司（行业分散）；年份取最近有数的
EVAL_CODES = ["600519", "000858", "000333", "600276", "002594", "600036"]
EVAL_YEARS = [2024, 2023]
AMOUNT_UNIT = "亿元"


def to_yi(value: str) -> str:
    """元 → 亿元，保留两位（与草稿披露精度一致）。"""
    yi = (Decimal(value) / Decimal("100000000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(yi, "f")


def load_financials() -> dict:
    table = {}
    with FIN.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cn = row["metric"]
            if cn not in METRIC_MAP or row["col_source"] != "current":
                continue
            key = (row["code"], int(row["fiscal_year"]), METRIC_MAP[cn])
            table[key] = {
                "value_yuan": row["value"],
                "value_yi": to_yi(row["value"]),
                "unit": row["unit"],
                "page": int(row["page"]) if row["page"] else None,
                "name": row["name"],
                "source_file": row["source_file"],
            }
    return table


def money_text(value: str) -> str:
    """1708.99 → 1,708.99（草稿可读；数值字段仍用纯数字）。"""
    parts = value.split(".")
    whole = parts[0]
    sign = "-" if whole.startswith("-") else ""
    digits = whole.lstrip("-")
    grouped = f"{int(digits):,}" if digits.isdigit() else digits
    return sign + grouped + ("." + parts[1] if len(parts) > 1 else "")


def build_claims(fin: dict) -> list[dict]:
    claims = []
    seq = 0

    def add(code, year, metric, kind, value, unit, expect, error_type, sentence, **extra):
        nonlocal seq
        seq += 1
        row = fin[(code, year, metric)]
        claim = {
            "id": f"E{seq:03d}",
            "sentence": sentence,
            "kind": kind,
            "company_code": code,
            "company_name": row["name"],
            "source_report_year": year,
            "period_year": extra.get("period_year", year),
            "period_kind": "annual",
            "metric": metric,
            "scope": SCOPE[metric],
            "currency": "CNY",
            "value": value,
            "unit": unit,
            "gold_expect": expect,
            "error_type": error_type,
            "gold_basis": "financials.csv 抽取长表（核心指标 70/70）；待团队人工复签",
            "evidence_page": row["page"],
        }
        claims.append(claim)

    for code in EVAL_CODES:
        for year in EVAL_YEARS:
            for metric in METRIC_MAP.values():
                if (code, year, metric) not in fin:
                    continue
                name = fin[(code, year, metric)]["name"]
                cn = METRIC_CN[metric]
                base = fin[(code, year, metric)]["value_yi"]
                # 1) 准确金额
                add(code, year, metric, "amount", base, AMOUNT_UNIT, "证据支持", "accurate",
                    f"{year}年，{name}{cn}为{base}亿元。")
                # 2) 错值（±12%~18%）
                wrong = (Decimal(base) * Decimal("1.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if wrong == Decimal(base):
                    wrong = Decimal(base) + Decimal("1.00")
                add(code, year, metric, "amount", format(wrong, "f"), AMOUNT_UNIT, "确认错误", "error_value",
                    f"{year}年，{name}{cn}为{wrong}亿元。")
                # 3) 错单位（亿元 写成 万元，数量级错）
                add(code, year, metric, "amount", base, "万元", "确认错误", "error_unit",
                    f"{year}年，{name}{cn}为{base}万元。")
                # 4) 错年份（把上年数值写成本年）
                prev_key = (code, year - 1, metric)
                if prev_key in fin:
                    prev = fin[prev_key]["value_yi"]
                    add(code, year, metric, "amount", prev, AMOUNT_UNIT, "确认错误", "error_year",
                        f"{year}年，{name}{cn}为{prev}亿元。")
                # 5) 同比：准确 + 符号翻转
                if prev_key in fin and Decimal(fin[prev_key]["value_yuan"]) != 0:
                    cur_y, prev_y = Decimal(fin[(code, year, metric)]["value_yuan"]), Decimal(fin[prev_key]["value_yuan"])
                    yoy = ((cur_y - prev_y) / prev_y * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    add(code, year, metric, "yoy", format(yoy, "f"), "%", "证据支持", "accurate",
                        f"{year}年，{name}{cn}同比增长{yoy}%。")
                    flipped = format(-yoy, "f") if yoy > 0 else format(abs(yoy), "f")
                    direction = "下降" if yoy > 0 else "增长"
                    add(code, year, metric, "yoy", flipped.lstrip("-"), "%", "确认错误", "error_sign",
                        f"{year}年，{name}{cn}同比{direction}{abs(yoy)}%。")
    return claims


def write_drafts(fin: dict, claims: list[dict]) -> None:
    """每个公司年一份「准确稿」+ 一份「含错误稿」（给 check-text / 演示用）。"""
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    by_pair: dict[tuple, list[dict]] = {}
    for c in claims:
        by_pair.setdefault((c["company_code"], c["source_report_year"]), []).append(c)
    for (code, year), rows in sorted(by_pair.items()):
        name = rows[0]["company_name"]
        accurate = [r for r in rows if r["error_type"] == "accurate"]
        noisy = [r for r in rows if r["error_type"] != "accurate"][:6]
        acc_text = f"# {name}{year}年财务草稿（评测准确稿）\n\n依据 {name}{year}年年度报告。\n\n" + \
            "\n".join(f"{i}. {r['sentence']}" for i, r in enumerate(accurate, 1)) + "\n"
        noisy_text = f"# {name}{year}年财务草稿（评测含错误稿）\n\n依据 {name}{year}年年度报告。\n\n" + \
            "\n".join(f"{i}. {r['sentence']}" for i, r in enumerate(noisy, 1)) + \
            "\n\n（本稿故意注入错误，用于测核查器精确率/召回率。）\n"
        (DRAFT_DIR / f"{code}_{year}_accurate.txt").write_text(acc_text, encoding="utf-8")
        (DRAFT_DIR / f"{code}_{year}_injected.txt").write_text(noisy_text, encoding="utf-8")


def main() -> int:
    fin = load_financials()
    claims = build_claims(fin)
    stats = {}
    for c in claims:
        stats[c["error_type"]] = stats.get(c["error_type"], 0) + 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_from": "data/extracted/financials.csv",
        "note": "错误注入评测集；gold_expect 为程序期望，待团队人工复签后可升格为正式参考答案",
        "error_types": stats,
        "claim_count": len(claims),
        "claims": claims,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    write_drafts(fin, claims)
    print(f"评测项 {len(claims)} 条 → {OUT}")
    print("错误类型分布：" + "，".join(f"{k}={v}" for k, v in sorted(stats.items())))
    print(f"草稿 {len(list(DRAFT_DIR.glob('*.txt')))} 份 → {DRAFT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
