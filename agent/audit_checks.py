"""估值倍数、来源引用与可执行修改建议的本地核查。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pymupdf

from finance import compare_number, decimal, evidence_issues, ratio, text
from materials import sha256, within


def base_check(claim: dict, kind: str) -> dict:
    return {"claim_id": claim.get("id", "supplemental"), "original_sentence": claim.get("sentence", ""),
            "check_item": kind, "kind": kind, "status": "口径冲突／需人工复核",
            "track": "deterministic", "epistemic_type": "事实", "evidence_ids": [],
            "reason_code": None, "reason": None, "calculation": None, "suggestion": None, "evidence": []}


def correction(out: dict, before: str, after: str) -> None:
    original = out["original_sentence"]
    out["correction"] = {"before": before, "after": after, "original_sentence": original,
                         "revised_sentence": original.replace(before, after, 1) if original.count(before) == 1 else None,
                         "apply_automatically": original.count(before) == 1,
                         "evidence_ids": out["evidence_ids"]}
    out["suggestion"] = f"将 {before} 改为 {after}；保留所列来源、期间与输入假设。"


def check_valuation(claim: dict, facts: list[dict]) -> dict:
    out = base_check(claim, "valuation")
    multiple = claim.get("multiple", "PE").upper().replace("/", "_")
    out["verification_scope"] = "内部一致性（未独立核实草稿内的市场价格/企业价值）"
    out["assumptions"] = {"multiple": multiple, "as_of": claim.get("as_of"), "basis": claim.get("basis")}
    out["risks"] = "倍数核查不是估值区间或投资建议；价格时点、利润口径与输入真实性需另行核实。"
    if multiple not in {"PE", "PB", "EV_EBITDA"}:
        out.update(reason_code="unsupported_multiple", reason="仅支持 PE、PB 和明确输入的 EV/EBITDA")
        return out
    try:
        as_of = date.fromisoformat(claim["as_of"])
    except (KeyError, ValueError, TypeError):
        out.update(reason_code="missing_valuation_date", reason="估值输入必须有明确日期，不猜测当前股价")
        return out
    if claim.get("basis") not in {"annual", "draft_explicit"}:
        out.update(reason_code="valuation_basis_unknown", reason="必须明确 annual 或 draft_explicit 口径；TTM/预测口径不能套历史全年数")
        return out
    numerator = claim.get("numerator", {})
    if numerator.get("company_code") != claim.get("company_code") or numerator.get("as_of") != claim.get("as_of"):
        out.update(reason_code="valuation_input_identity", reason="价格/企业价值的公司和时点不一致")
        return out
    if not numerator.get("source_quote") or not numerator.get("source"):
        out.update(reason_code="valuation_input_unproven", reason="价格/企业价值缺少来源与原文，不能计算")
        return out
    if multiple in {"PE", "PB"}:
        metric = "basic_eps" if multiple == "PE" else "book_value_per_share"
        candidates = [f for f in facts if f.get("company_code") == claim.get("company_code") and f.get("metric") == metric
                      and f.get("period_year") == claim.get("period_year") and f.get("report_year") == claim.get("period_year")
                      and f.get("period_kind") == "annual" and f.get("adjustment") != "before"]
        if len(candidates) != 1:
            out.update(status="证据不足", reason_code="valuation_denominator_missing", reason=f"未找到唯一 {metric} 年度证据")
            return out
        f = candidates[0]
        if evidence_issues(f) or f.get("unit") != "元/股" or numerator.get("unit") != "元/股":
            out.update(reason_code="valuation_unit_or_evidence", reason="价格/EPS/每股净资产单位或证据状态不符合可比要求")
            return out
        if not f.get("period_end") or date.fromisoformat(f["period_end"]) > as_of:
            out.update(reason_code="valuation_lookahead", reason="价格时点早于财务期间结束，存在前视风险")
            return out
        denominator_value = f["value"]
        out["evidence_ids"] = [f["evidence_id"]]
        out["evidence"] = [{k: f.get(k) for k in ("evidence_id", "source_file", "source_sha256", "page", "metric_name", "value", "unit", "value_bbox")}]
        out["assumptions"].update(denominator_metric=metric, period_end=f["period_end"],
                                  disclosure_date_status="未自动证明估值时点已公开披露此报告")
    else:
        denominator = claim.get("denominator", {})
        if (denominator.get("company_code") != claim.get("company_code") or
                denominator.get("period_year") != claim.get("period_year") or
                not denominator.get("source_quote") or not denominator.get("source") or
                denominator.get("unit") != numerator.get("unit") or denominator.get("unit") not in {"元", "万元", "亿元"}):
            out.update(reason_code="ev_ebitda_inputs_unresolved", reason="EV/EBITDA 需同公司、同单位和明确期间的完整输入与原文来源")
            return out
        denominator_value = denominator.get("value")
    try:
        a, b, claimed = decimal(numerator.get("value")), decimal(denominator_value), decimal(claim.get("value"))
    except ValueError:
        out.update(reason_code="valuation_invalid_value", reason="估值输入不是合法十进制数")
        return out
    if a is None or b is None or claimed is None or a <= 0 or b <= 0:
        out.update(reason_code="valuation_nonpositive_input", reason="价格/企业价值和分母必须为正；亏损、零分母不输出常规倍数")
        return out
    calculated = ratio(a, b)
    comparison = compare_number(calculated["value"], claimed, operator=claim.get("operator", "eq"))
    if comparison["status"] not in {"match", "mismatch"}:
        out.update(calculation=comparison, reason_code="valuation_comparison_unresolved", reason=comparison.get("reason"))
        return out
    out.update(calculation={**comparison, "multiple_calculation": calculated},
               reason_code="valuation_internal_consistency", status="证据支持" if comparison["status"] == "match" else "确认错误",
               reason="在所列输入和口径下倍数计算" + ("一致" if comparison["status"] == "match" else "不一致"))
    if comparison["status"] == "mismatch":
        correction(out, f"{claim['value']}倍", f"{comparison['value']}倍")
    return out


def check_reference(claim: dict, facts: list[dict], *, root: Path, run=None) -> dict:
    out = base_check(claim, "reference")
    candidates = [f for f in facts if f.get("company_code") == claim.get("company_code")
                  and f.get("report_year") == claim.get("source_report_year")
                  and (not claim.get("metric") or f.get("metric") == claim["metric"])
                  and (not claim.get("period_year") or f.get("period_year") == claim["period_year"])
                  and f.get("adjustment") != "before"]
    files = {(f.get("source_file"), f.get("source_sha256")) for f in candidates}
    if len(files) != 1:
        out.update(status="证据不足", reason_code="reference_source_unavailable", reason="引用来源未唯一对应已加载文件，不认定为伪造")
        return out
    file, fingerprint = next(iter(files))
    try:
        path = within(root, root / file)
        blob = run.read(path) if run else path.read_bytes()
    except (ValueError, OSError, TypeError):
        out.update(status="证据不足", reason_code="reference_source_unavailable", reason="引用文件不可读或不在受控目录中")
        return out
    if sha256(blob) != fingerprint:
        out.update(status="证据不足", reason_code="reference_fingerprint_mismatch", reason="引用文件指纹与登记证据不一致")
        return out
    out.update(source_file=file, source_sha256=fingerprint)
    with pymupdf.open(stream=blob, filetype="pdf") as doc:
        page_number = claim.get("page")
        numbering = claim.get("numbering", "unknown")
        if numbering == "printed":
            pages = [p for p in doc if p.get_label() == str(page_number)]
        elif numbering == "physical" and type(page_number) is int:
            if not 1 <= page_number <= doc.page_count:
                out.update(status="确认错误", reason_code="reference_page_out_of_range",
                           reason=f"PDF 页序 {page_number} 超出 1–{doc.page_count}", page=page_number)
                return out
            pages = [doc[page_number-1]]
        else:
            out.update(reason_code="reference_page_numbering_unknown", reason="请明确 PDF 页序或印刷页码；不默认两者相同")
            return out
        if len(pages) != 1:
            out.update(status="证据不足", reason_code="printed_page_unresolved", reason="印刷页标签未唯一解析，需人工定位")
            return out
        page = pages[0]
        cited_text = re.sub(r"\s+", "", page.get_text())
        if len(cited_text) < 20:
            out.update(status="证据不足", reason_code="reference_no_text_layer", reason="引用页缺少文字层，不能用文本缺失判定伪造")
            return out
        quote = re.sub(r"\s+", "", claim.get("source_quote") or "")
        metric = claim.get("metric")
        matching = [f for f in candidates if f.get("page") == page.number+1 and not evidence_issues(f)]
        if quote:
            supported = quote in cited_text
        elif metric and claim.get("value") is not None and claim.get("unit"):
            from finance import compare_amount
            supported = any(compare_amount(f["value"], f["unit"], claim["value"], claim["unit"])["status"] == "match" for f in matching)
        else:
            out.update(reason_code="reference_claim_unresolved", reason="引用必须有逐字原文，或明确指标/值/单位；只验页码不能确认支持性")
            return out
        out["page"] = page.number+1
        out["source_file"] = file
        out["evidence_ids"] = [f["evidence_id"] for f in matching]
        out["evidence"] = [{k: f.get(k) for k in ("evidence_id", "source_file", "source_sha256", "page", "value_bbox")} for f in matching]
        out["verification_scope"] = "来源页对所核查字段/逐字片段的支持性，不代表整段结论获证实"
        out.update(status="证据支持" if supported else "口径冲突／需人工复核",
                   reason_code="reference_supported" if supported else "reference_not_supported",
                   reason="引用页支持所核查内容" if supported else "引用页未检出支持该字段/片段的证据；不能由此直接认定来源伪造")
        if not supported:
            out["suggestion"] = "核对引用页码和原文；可用证据页：" + ", ".join(str(p) for p in sorted({f['page'] for f in candidates}))
    return out


def check_draft_supplements(draft: str, facts: list[dict], *, root: Path, run=None) -> list[dict]:
    """对明确表达作本地识别；未匹配表达由 LLM/人工复核，不声称覆盖任意措辞。"""
    from llm_check import split_draft, SECRET_PATTERN
    from extract import ALIASES
    out = []
    if SECRET_PATTERN.search(draft):
        raise ValueError("草稿含疑似密钥；请先移除，核查不会记录或上传该内容")
    company_names = {f.get("company_code"): f.get("company_name") for f in facts if f.get("company_name")}
    def mentioned_codes(content):
        return {code for code, name in company_names.items() if name in content or code in content}
    draft_codes = mentioned_codes(draft)
    for sentence in split_draft(draft):
        s = sentence["text"]
        codes = mentioned_codes(s)
        codes = codes or draft_codes
        code = next(iter(codes)) if len(codes) == 1 else None
        years = re.findall(r"(?<!\d)(20\d{2})年", s)
        if not years:
            years = re.findall(r"(?<!\d)(20\d{2})年", draft)
        y = int(years[-1]) if years and len(set(years)) == 1 else None
        multiple = re.search(r"(?i)(?<![a-z])(?:PE|市盈率|PB|市净率|EV[/／]EBITDA)\s*(?:为|约为|等于|=|：|:)?\s*(\d+(?:\.\d+)?)\s*倍", s)
        if multiple:
            name = multiple[0].split(multiple[1])[0].strip().upper()
            kind = "PB" if "PB" in name or "市净率" in name else "EV_EBITDA" if "EBITDA" in name else "PE"
            # 多公司、多时点不得借用草稿中第一个价格。只取本句，或全稿唯一公司的唯一价格。
            context = s if re.search(r"股价|每股价格", s) else draft if len(draft_codes) == 1 else ""
            prices = list(re.finditer(r"(?:股价|每股价格)\s*(?:为|=|：|:)?\s*(\d+(?:\.\d+)?)\s*元(?:/股)?", context))
            price = prices[0] if len(prices) == 1 else None
            dates = list(re.finditer(r"(20\d{2}-\d{2}-\d{2})\s*(?:的)?\s*(?:股价|每股价格|企业价值|EV)", context, re.I))
            as_of = dates[0] if len(dates) == 1 else None
            claim = {"id": f"V{sentence['sentence_id']}", "sentence": s, "company_code": code,
                     "period_year": y, "multiple": kind, "value": multiple[1],
                     "as_of": as_of[1] if as_of else None,
                     "basis": "annual" if y and not re.search(r"TTM|预测|动态|未来", s, re.I) else None,
                     "numerator": {"value": price[1] if price else None, "unit": "元/股", "company_code": code,
                                   "as_of": as_of[1] if as_of else None, "source": "draft", "source_quote": price[0] if price else None}}
            out.append(check_valuation(claim, facts))
        page = re.search(r"(?:PDF\s*页序|PDF\s*第|印刷第|第)\s*(\d+)\s*页", s, re.I)
        if page:
            numbering = "physical" if re.search(r"PDF", page[0], re.I) else "printed" if "印刷" in page[0] else "unknown"
            metric = next((key for alias, key, _ in ALIASES if alias in s), None)
            number = re.search(r"([-+]?\d[\d,，]*(?:\.\d+)?)\s*(亿元|万元|元/股|元|%|％)", s)
            quoted = re.search(r"[“\"]([^”\"]+)[”\"]", s)
            claim = {"id": f"R{sentence['sentence_id']}", "sentence": s, "company_code": code,
                     "source_report_year": y, "period_year": y, "metric": metric, "page": int(page[1]),
                     "numbering": numbering, "source_quote": quoted[1] if quoted else None,
                     "value": number[1] if number else None, "unit": number[2] if number else None}
            out.append(check_reference(claim, facts, root=root, run=run))
    if run:
        for c in out:
            run.event("supplemental_check", **{k: c.get(k) for k in (
                "claim_id", "status", "reason_code", "reason", "evidence_ids", "calculation", "verification_scope")})
    return out
