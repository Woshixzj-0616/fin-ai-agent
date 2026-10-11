"""抽取通道编排、去重、安全提示与统一入口。"""
from __future__ import annotations

import pymupdf
from finance import NON_AMOUNT_UNITS, convert, decimal, text
from materials import Run, sha256, within
from words import METRIC_UNIT, norm

from .annual import extract_total_revenue, extract_words_document
from .catalog import METRICS, REQUIRED
from .grid import corroborate_scope, document_currency, parse_table, statement_sections


def _apply_scope(document, sections, facts, material):
    """对 revenue / total_revenue / operating_cash_flow 做合并口径旁证。"""
    for metric in ("revenue", "total_revenue", "operating_cash_flow"):
        current = next((f for f in facts if f["metric"] == metric
                        and f["period_year"] == material["report_year"]
                        and f["adjustment"] == "as_reported"), None)
        corroboration = corroborate_scope(document, sections, current) if current else None
        for fact in facts:
            if fact["metric"] != metric:
                continue
            if corroboration:
                fact["scope"] = "consolidated"
                fact["scope_evidence"] = {
                    **corroboration, "application": "same_summary_metric_row",
                    "confirmed_current_evidence_id": current["evidence_id"]}
            elif "scope_unknown" not in fact["issues"]:
                fact["issues"].append("scope_unknown")


def _dedupe(facts: list[dict], run=None) -> list[dict]:
    """同 (metric, period_year, adjustment) 只留一条；两通道都有时优先网格路径（护 gold），
    但缺字段要从输家补 —— 宁可字段更全（unit / reported_yoy / adjustment_header / 坐标框）。"""
    order = {"grid_cells_and_geometric_headers": 0, "words_geometry": 1,
             "words_order_inference": 2, "income_statement_total_revenue": 3}
    best: dict[tuple, dict] = {}
    for f in facts:
        key = (f["metric"], f["period_year"], f["adjustment"])
        old = best.get(key)
        if old is None:
            best[key] = f
            continue
        winner, loser = (old, f) if order.get(old["extraction_method"], 9) <= order.get(f["extraction_method"], 9) \
            else (f, old)
        if (winner.get("unit") and loser.get("unit") and
                decimal(winner.get("normalized_value")) != decimal(loser.get("normalized_value"))):
            winner["issues"] = list(dict.fromkeys(winner.get("issues", []) + ["conflicting_extraction_values"]))
        if run is not None:
            run.event("extraction_dedup", key=list(key), winner_evidence_id=winner["evidence_id"],
                      loser_evidence_id=loser["evidence_id"], rule="grid > words > order_inference > income_statement",
                      winner_method=winner["extraction_method"], loser_method=loser["extraction_method"],
                      winner_candidate={k: winner.get(k) for k in ("value", "unit", "normalized_value", "original_label", "page")},
                      loser_candidate={k: loser.get(k) for k in ("value", "unit", "normalized_value", "original_label", "page")},
                      conflict="conflicting_extraction_values" in winner.get("issues", []))
        if not winner.get("reported_yoy") and loser.get("reported_yoy"):
            winner["reported_yoy"] = loser["reported_yoy"]
        if not winner.get("adjustment_header") and loser.get("adjustment_header"):
            winner["adjustment_header"] = loser["adjustment_header"]
        # 网格路径常缺单位/标签框；words 认得出却因优先级被丢掉会假报 unit_unknown。
        if not winner.get("unit") and loser.get("unit"):
            winner["unit"] = loser["unit"]
            winner["unit_evidence"] = loser.get("unit_evidence") or {
                "unit": loser["unit"], "basis": loser.get("extraction_method")}
            if "unit_unknown" in winner.get("issues", []):
                winner["issues"] = [i for i in winner["issues"] if i != "unit_unknown"]
            # 单位补上后要按新单位重算归一值，否则旁证/比对仍按「原数=元」搜。
            raw_v = loser.get("value") or winner.get("value")
            if raw_v is not None and winner["unit"] not in NON_AMOUNT_UNITS:
                try:
                    winner["normalized_value"] = text(convert(raw_v, winner["unit"], "元"))
                    winner["normalized_unit"] = "元"
                except (ValueError, ArithmeticError):
                    pass
        if not winner.get("label_bbox") and loser.get("label_bbox"):
            winner["label_bbox"] = loser["label_bbox"]
        if not winner.get("original_label") and loser.get("original_label"):
            winner["original_label"] = loser["original_label"]
        best[key] = winner
    return list(best.values())


def _scanned_pdf_hint(document, texts: list[str]) -> str | None:
    """扫描版/无文字层：给出人话拒收原因，而不是抽到一半说版式不支持。"""
    pages = len(texts)
    if pages == 0:
        return "PDF 无法解析出任何页面，可能已损坏或加密，请换一份可读的年报 PDF。"
    nonempty = sum(1 for t in texts if len(norm(t)) >= 20)
    # 整本几乎无字 → 扫描件；只翻前 12 页加速
    sample = texts[:12]
    sample_ok = sum(1 for t in sample if len(norm(t)) >= 20)
    if nonempty == 0 or (sample and sample_ok == 0):
        return ("这份 PDF 几乎没有文字层（像是扫描版/影印件），当前版本不支持 OCR，"
                "无法抽取财务数据。请提供带文字层的年度报告 PDF；"
                "可在阅读器里选中表格里的数字来确认是否有文字层。")
    if nonempty < max(2, pages // 10):
        return (f"PDF 文字层过少（约 {nonempty}/{pages} 页可检索），更像影印件；"
                "若确认原档如此，请改用官方文字版年报。")
    return None


def extract_material(root, material: dict, run: Run) -> list[dict]:
    path = within(root, root / material["local_file"])
    blob = run.read(path)
    if sha256(blob) != material["sha256"]:
        raise ValueError("提取前指纹复核失败")
    with pymupdf.open(stream=blob, filetype="pdf") as document:
        texts = [page.get_text() for page in document]
        scanned = _scanned_pdf_hint(document, texts)
        if scanned:
            raise ValueError(scanned)
        from periods import report_period
        identity = report_period("".join(texts[:10]), material["report_year"])
        if identity["report_kind"] in {"quarter", "half"}:
            from interim import extract_interim
            return extract_interim(document, material, run)
        currency = document_currency(document, texts)

        # ── 通道 1：表格线网格（gold 口径） ──
        grid_facts: list[dict] = []
        grid_error = None
        try:
            candidates = []
            for index, content in enumerate(texts):
                compact = norm(content)
                if "主要会计数据" not in compact or "营业收入" not in compact:
                    continue
                page = document[index]
                for ti, table in enumerate(page.find_tables().tables):
                    facts = parse_table(page, table, ti, material, currency)
                    current = [f for f in facts if f["period_year"] == material["report_year"]]
                    count = len({f["metric"] for f in current})
                    if count:
                        candidates.append((count, facts))
            if candidates:
                best_count = max(count for count, _ in candidates)
                best = [facts for count, facts in candidates if count == best_count]
                signatures = {
                    tuple(sorted((f["metric"], f["period_year"], f["adjustment"], f["value"] or "")
                                 for f in facts)) for facts in best}
                if len(signatures) != 1:
                    raise ValueError("发现互相冲突的候选表，需要人工选择")
                grid_facts = best[0]
                keys = [(f["metric"], f["period_year"], f["adjustment"]) for f in grid_facts]
                if len(keys) != len(set(keys)):
                    raise ValueError("表头产生重复证据键，拒绝静默选列")
        except ValueError as exc:
            grid_error = str(exc)
            grid_facts = []

        # ── 通道 2：words 级（稳健） ──
        words_facts = extract_words_document(document, material, currency)

        # ── 通道 3：利润表补营业总收入 ──
        total_facts = extract_total_revenue(document, material, currency)

        for channel, channel_facts in (("grid", grid_facts), ("words", words_facts), ("income_statement", total_facts)):
            run.event("extraction_channel", document_id=material["document_id"], channel=channel,
                      count=len(channel_facts), evidence_ids=[f["evidence_id"] for f in channel_facts],
                      error=grid_error if channel == "grid" else None)
        facts = _dedupe(grid_facts + words_facts + total_facts, run)
        if not facts:
            raise ValueError(grid_error or (
                "未识别到「主要会计数据」摘要表。可能原因：①扫描版无文字层 ②版式过新/过偏 ③该 PDF 不是年度报告。"
                "请用阅读器打开确认表格内数字可选中复制，或换官方文字版年报。"))

        # 指标固有单位（元/股、%）兜底：版式没写也不该报 unit_unknown。
        for f in facts:
            if not f.get("unit") and f["metric"] in METRIC_UNIT:
                f["unit"] = METRIC_UNIT[f["metric"]]
                f["unit_evidence"] = {"unit": f["unit"], "basis": "metric_intrinsic_unit"}
                f["issues"] = [i for i in f.get("issues", []) if i != "unit_unknown"]

        sections = statement_sections(document)
        _apply_scope(document, sections, facts, material)

        # A 股年报默认人民币完整年度（项目口径）；页面/文档层没抓到币种声明时补默认，避免同比被「币种未明确」拦下。
        for f in facts:
            if not f.get("currency"):
                f["currency"] = "CNY"
                f["currency_evidence"] = {"currency": "CNY",
                                          "basis": "a_share_annual_report_default"}
                f["issues"] = [i for i in f["issues"] if i != "currency_unknown"]

        current_metrics = {f["metric"] for f in facts
                           if f["period_year"] == material["report_year"]
                           and f["adjustment"] in {"as_reported", "after"}}
        missing_required = [m for m in REQUIRED if m not in current_metrics]
        missing_optional = sorted(set(METRICS) - current_metrics)
        method_counts = {}
        for f in facts:
            method_counts[f["extraction_method"]] = method_counts.get(f["extraction_method"], 0) + 1
        run.event("extraction_completed", document_id=material["document_id"],
                  evidence_count=len(facts), missing_required=missing_required,
                  missing_current_metrics=missing_optional,
                  method_counts=method_counts,
                  needs_review=sum(bool(f["issues"]) for f in facts))
        if missing_required:
            raise ValueError("主要指标不完整：" + ",".join(missing_required))
        return facts
