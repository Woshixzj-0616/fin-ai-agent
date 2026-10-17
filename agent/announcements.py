"""质押、中标、股权变动公告的字段 schema、位置与一致性核对。"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pymupdf

from finance import convert, decimal, text
from materials import ROOT, sha256

SCHEMAS = {
    "pledge": {
        "name": "股份质押", "required": ["pledgor", "shares", "pledgee", "start_date"],
        "fields": {"pledgor": ["股东名称", "出质人", "质押人", "质押股东"],
                   "shares": ["本次质押数量", "本次质押股份数量", "本次质押股份数", "质押股数", "质押数量"],
                   "pledgee": ["质权人", "质押权人"], "start_date": ["质押起始日", "起始日期", "质押开始日期"],
                   "end_date": ["质押到期日", "到期日期", "质押结束日期"],
                   "purpose": ["质押用途", "质押融资用途", "用途"],
                   "ratio_percent": ["占公司总股本比例", "占总股本比例"]}},
    "winning_bid": {
        "name": "中标", "required": ["project_name", "winner", "amount"],
        "fields": {"project_name": ["项目名称", "中标项目", "标的名称"],
                   "winner": ["中标人", "中标单位", "中标主体", "供应商名称"],
                   "customer": ["招标人", "采购人", "业主单位", "采购单位"],
                   "amount": ["中标金额", "合同金额", "中标价", "投标报价"],
                   "announcement_date": ["中标通知日期", "公告日期"],
                   "term": ["合同期限", "工期", "服务期限"]}},
    "equity_change": {
        "name": "股权变动", "required": ["seller", "buyer", "shares", "change_date"],
        "fields": {"seller": ["转让方", "出让方", "卖方", "减持股东"],
                   "buyer": ["受让方", "买方", "增持股东"],
                   "shares": ["转让股份数量", "变动股数", "转让股数", "转让股份数"],
                   "ratio_percent": ["变动比例", "转让比例", "占总股本比例"],
                   "change_date": ["股权变动日期", "变动日期", "转让日期"],
                   "change_method": ["变动方式", "转让方式"], "amount": ["转让价款", "交易金额"]}},
}


def normalize_field(field: str, raw: str, header: str = "") -> dict:
    compact = re.sub(r"\s+", "", raw).strip("。；;")
    out = {"value": raw.strip(), "normalized_value": compact, "unit": None, "issues": []}
    if field.endswith("date"):
        m = re.fullmatch(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", compact)
        try:
            if not m:
                raise ValueError()
            out["normalized_value"] = date(*map(int, m.groups())).isoformat()
        except ValueError:
            out["issues"].append("date_unresolved")
    elif field in {"shares", "amount", "ratio_percent"}:
        if field == "amount":
            compact = re.sub(r"^(?:人民币|RMB|CNY)", "", compact, flags=re.I)
            qualified = re.search(r"[（(](?:具体|最终|以|含税).*[）)]$", compact)
            if qualified:
                out["qualifier"] = qualified[0]
                compact = compact[:qualified.start()]
        m = re.fullmatch(r"([+-]?\d[\d,，]*(?:\.\d+)?)(亿股|万股|股|亿元|百万元|万元|千元|元|%|％)?", compact)
        if not m:
            out["issues"].append("number_or_unit_unresolved")
            return out
        value, unit = decimal(m[1]), m[2]
        if not unit:
            declared = re.search(r"[（(](亿股|万股|股|亿元|万元|元|%|％)[）)]", header)
            unit = declared[1] if declared else "%" if field == "ratio_percent" and "比例" in header else None
        out.update(value=text(value), unit=unit)
        if field == "shares" and unit in {"股", "万股", "亿股"}:
            out.update(normalized_value=text(value * {"股": 1, "万股": 10000, "亿股": 100000000}[unit]), normalized_unit="股")
        elif field == "amount" and unit in {"元", "千元", "万元", "百万元", "亿元"}:
            out.update(normalized_value=text(convert(value, unit)), normalized_unit="元")
        elif field == "ratio_percent" and unit in {"%", "％"}:
            out.update(normalized_value=text(value), normalized_unit="%")
        else:
            out["issues"].append("unit_unresolved")
        if value is not None and value < 0:
            out["issues"].append("negative_event_value")
    return out


def extract_announcement(blob: bytes, event_type: str = "auto", *, run=None,
                         source_file: str = "uploaded.pdf", company_code: str | None = None,
                         company_name: str | None = None, ocr: bool = False) -> dict:
    fingerprint = sha256(blob)
    with pymupdf.open(stream=blob, filetype="pdf") as doc:
        if doc.needs_pass:
            raise ValueError("公告 PDF 已加密")
        page_texts, pages, used_ocr = [], [], False
        for page in doc:
            content = page.get_text()
            textpage = None
            if len(re.sub(r"\s+", "", content)) < 20 and ocr:
                try:
                    import os
                    from words import page_rows
                    local_data = ROOT / "data/ocr/tessdata"
                    tessdata = os.environ.get("TESSDATA_PREFIX") or (str(local_data) if local_data.is_dir() else None)
                    if run and tessdata:
                        model_hashes = {lang: sha256(run.read(Path(tessdata) / f"{lang}.traineddata")) for lang in ("chi_sim", "eng")}
                    else:
                        model_hashes = {}
                    textpage = page.get_textpage_ocr(language="chi_sim+eng", dpi=200, full=True, tessdata=tessdata)
                    content = "\n".join("".join(c["text"] for c in row["cells"]) for row in page_rows(page, textpage=textpage))
                    used_ocr = True
                    if run:
                        run.event("ocr_used", page=page.number+1, language="chi_sim+eng", dpi=200,
                                  language_data_sha256=model_hashes, status="needs_review")
                except Exception as exc:
                    raise ValueError("本地 OCR 不可用；运行 python3 scripts/setup_ocr.py 配置语言包，或设置 TESSDATA_PREFIX") from exc
            pages.append((page, content, textpage))
            page_texts.append(content)
        content = "\n".join(page_texts)
        if len(re.sub(r"\s+", "", content)) < 20:
            raise ValueError("扫描公告缺少文字层；使用 --ocr 需配置本地 OCR，不能将空字段当成功")
        if event_type == "auto":
            # 类型只从标题区域确认，年报正文中的历史质押描述不冒充公告。
            title = "\n".join(page_texts[:1]).split("\n")[:30]
            title = "".join(title)
            types = [k for k, pattern in (("pledge", "质押"), ("winning_bid", "中标"),
                     ("equity_change", "股权变动|权益变动|股份转让|股权转让")) if re.search(pattern, title)]
            if len(types) != 1:
                raise ValueError("公告类型未唯一确认，请明确选择 pledge / winning_bid / equity_change")
            event_type = types[0]
        if event_type not in SCHEMAS:
            raise ValueError("公告类型不支持")
        spec = SCHEMAS[event_type]
        occurrences = []

        def location(page, raw, textpage=None):
            rects = page.search_for(raw.strip(), textpage=textpage) if raw.strip() else []
            return {"page": page.number+1, "page_label": page.get_label(),
                    "bbox": list(rects[0]) if len(rects) == 1 else None,
                    "location_status": "unique" if len(rects) == 1 else "multiple_or_unresolved",
                    "source_file": source_file, "source_sha256": fingerprint}

        for page, page_content, textpage in pages:
            # 明确标签的非标准正文。冒号必须存在，避免吃进同名叙述句。
            fields = {}
            # 无标签中标正文：仅匹配完整的“收到通知书→确认中标→金额”句式，
            # 限于基本情况小节，避免把“对公司的影响”中历史项目金额配给本次项目。
            if event_type == "winning_bid":
                flattened = re.sub(r"\s+", "", page_content)
                basics = re.search(r"一[、.]基本情况(.*?)(?:二[、.]|$)", flattened)
                if basics:
                    narrative = re.search(
                        r"(?P<issuer>[\u4e00-\u9fffA-Za-z（）()]+有限公司)[（(]以下简称[“\"]公司[”\"][）)]"
                        r"于(?P<date>20\d{2}年\d{1,2}月\d{1,2}日)收到(?P<customer>[^，。]{1,50}?)关于"
                        r"(?P<project>[^，。]{1,100}?)中标通知书[，,]确认公司为[^。]{1,150}?中标单位[，,]"
                        r"(?P<estimate>预估)?中标总价(?:合计)?(?P<amount>人民币[\d,，.]+元)", basics[1])
                    if narrative:
                        for field, group in (("project_name", "project"), ("winner", "issuer"),
                                             ("customer", "customer"), ("announcement_date", "date"), ("amount", "amount")):
                            # 收到通知书日期具有明确语义，不冒充 PDF 发布日期。
                            raw = narrative[group]
                            fields[field] = [{**normalize_field(field, raw), "quote": narrative[0],
                                              **location(page, raw, textpage), "extraction_method": "explicit_bid_notice_sentence",
                                              "semantic_role": "notice_received_date" if field == "announcement_date" else field}]
                        if narrative["estimate"]:
                            fields["amount"][0]["issues"].append("estimated_amount_not_final_contract")
            for field, aliases in spec["fields"].items():
                for alias in sorted(aliases, key=len, reverse=True):
                    for m in re.finditer(r"(?:^|[\n；;])\s*(?:\d+[、.．)）]\s*)?" + re.escape(alias) + r"\s*[:：]\s*([^\n；;]+)", page_content):
                        raw = m[1].strip()
                        fields.setdefault(field, []).append({**normalize_field(field, raw, alias),
                                                             "quote": m[0].strip(), **location(page, raw, textpage)})
            if fields:
                occurrences.append({"fields": fields, "channel": "labeled_text", "page": page.number+1})
            if textpage is not None:
                # OCR 位置可复核，但不把扫描表格的阅读顺序当作可靠列结构。
                continue
            for ti, table in enumerate(page.find_tables().tables):
                grid = table.extract()
                vertical = {}
                for ri, row in enumerate(grid):
                    if len(row) != 2 or not row[0] or not row[1]:
                        continue
                    label = re.sub(r"\s+", "", row[0]).strip("：:")
                    matched = next((field for field, aliases in spec["fields"].items()
                                    if label in aliases), None)
                    if matched:
                        raw = row[1].strip()
                        vertical.setdefault(matched, []).append({**normalize_field(matched, raw, label),
                            "quote": raw, **location(page, raw), "bbox": list(table.rows[ri].cells[1]),
                            "location_status": "table_cell", "table": ti, "row": ri})
                if len(vertical) >= 2:
                    occurrences.append({"fields": vertical, "channel": "table", "page": page.number+1})
                    continue
                header_row, mapping = None, {}
                for ri, row in enumerate(grid[:4]):
                    mapped = {}
                    ambiguous = set()
                    for ci, label in enumerate(row):
                        compact = re.sub(r"\s+", "", label or "")
                        clean = re.sub(r"[（(](?:亿股|万股|股|亿元|万元|元|%|％)[）)]$", "", compact)
                        for field, aliases in spec["fields"].items():
                            if clean in aliases:
                                if field in mapped:
                                    ambiguous.add(field)
                                mapped[field] = (ci, compact)
                    for field in ambiguous:
                        mapped.pop(field, None)
                    if len(mapped) >= 2:
                        header_row, mapping = ri, mapped
                        break
                if header_row is None:
                    continue
                for ri, row in enumerate(grid[header_row+1:], header_row+1):
                    fields = {}
                    for field, (ci, label) in mapping.items():
                        raw = (row[ci] or "").strip() if ci < len(row) else ""
                        if not raw or raw in {"-", "—", "合计"}:
                            continue
                        cell_box = table.rows[ri].cells[ci]
                        fields[field] = [{**normalize_field(field, raw, label), "quote": raw,
                                          **location(page, raw), "bbox": list(cell_box) if cell_box else None,
                                          "location_status": "table_cell", "table": ti, "row": ri}]
                    if fields and not any(re.sub(r"\s+", "", row[ci] or "") == "合计" for ci, _ in mapping.values()):
                        occurrences.append({"fields": fields, "channel": "table", "page": page.number+1})
        anchor = {"pledge": "pledgor", "winning_bid": "project_name", "equity_change": "seller"}[event_type]
        # 每一表格数据行保留为独立事件；同名股东不能成为跨多笔交易的唯一标识。
        groups = [[o] for o in occurrences if o["channel"] == "table"]
        stable = {"pledge": ("start_date", "pledgee"), "winning_bid": ("customer",),
                  "equity_change": ("change_date", "buyer")}[event_type]
        def unique_value(occurrence, field):
            values = {c["normalized_value"] for c in occurrence["fields"].get(field, [])}
            return next(iter(values)) if len(values) == 1 else None
        for occurrence in (o for o in occurrences if o["channel"] != "table"):
            name = unique_value(occurrence, anchor)
            candidates = [g for g in groups if name and unique_value(g[0], anchor) == name]
            if len(candidates) > 1:
                candidates = [g for g in candidates if all(
                    unique_value(occurrence, k) is not None and
                    unique_value(occurrence, k) == unique_value(g[0], k) for k in stable)]
            if len(candidates) == 1:
                candidates[0].append(occurrence)
            else:
                if candidates or (not name and groups):
                    occurrence["association_unresolved"] = True
                groups.append([occurrence])
        events = []
        for key, items in enumerate(groups):
            fields, conflicts = {}, []
            for field in spec["fields"]:
                candidates = [c for it in items for c in it["fields"].get(field, [])]
                if not candidates:
                    fields[field] = {"value": None, "normalized_value": None, "status": "missing", "evidence": []}
                    continue
                vals = {(c["normalized_value"], c.get("normalized_unit", c.get("unit"))) for c in candidates}
                status = "conflict" if len(vals) > 1 else "needs_review" if any(c["issues"] for c in candidates) else "extracted"
                if used_ocr and status == "extracted":
                    status = "needs_review"
                if status == "conflict":
                    conflicts.append(field)
                fields[field] = {**{k: candidates[0].get(k) for k in ("value", "normalized_value", "unit", "normalized_unit")},
                                 "status": status, "evidence": candidates}
            missing = [k for k in spec["required"] if fields[k]["status"] == "missing"]
            association_unresolved = any(it.get("association_unresolved") for it in items)
            event = {"event_id": sha256(f"{fingerprint}|{event_type}|{key}".encode())[:24], "event_type": event_type,
                     "event_name": spec["name"], "company_code": company_code, "company_name": company_name,
                     "fields": fields, "missing_required": missing, "conflicts": conflicts,
                     "status": "needs_review" if missing or conflicts or association_unresolved or any(f["status"] == "needs_review" for f in fields.values()) else "extracted",
                     "source_file": source_file, "source_sha256": fingerprint, "epistemic_type": "事实",
                     "channels": list(dict.fromkeys(it["channel"] for it in items)), "ocr_used": used_ocr,
                     "association_unresolved": association_unresolved,
                     "currency": "CNY" if "人民币" in content else None}
            events.append(event)
            if run:
                run.event("event_extracted", event_id=event["event_id"], event_type=event_type,
                          missing_required=missing, conflicts=conflicts, status=event["status"], fields=fields)
        return {"schema_version": 1, "source_sha256": fingerprint, "event_type": event_type,
                "events": events, "status": "completed" if events else "no_fields",
                "ocr_used": used_ocr, "limits": "支持明确标签、表头及限定的中标通知正文句式；未提取字段不补猜。扫描 OCR 结果需人工复核。"}


def cli() -> int:
    import argparse
    from materials import Run, ROOT, write_json
    ap = argparse.ArgumentParser(description="非标准公告结构化提取")
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--type", choices=["auto", *SCHEMAS], default="auto")
    ap.add_argument("--ocr", action="store_true")
    args = ap.parse_args()
    run = Run(ROOT, "extract-announcement", {"pdf": str(args.pdf), "type": args.type, "ocr": args.ocr})
    try:
        out = extract_announcement(run.read(args.pdf), args.type, run=run, source_file=str(args.pdf), ocr=args.ocr)
        write_json(run.output("announcement_events.json"), out)
        run.finish(status=out["status"], events=len(out["events"]))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["events"] else 1
    except ValueError as exc:
        run.finish(status="failed", reason=str(exc))
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
