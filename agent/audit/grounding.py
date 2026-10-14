"""草稿拆句、公司与期间接地。

绑定顺序：句内明确实体 → 引文/上下文 → 段落/标题 → 整篇草稿 → 模型补全字段。
句内已经明确另一主体时，模型补全与前文继承都不能覆盖它；两条路径都保留定位依据。
短别名（茅台、招行等）可能与普通词同形，因此只在模型字段与之一致时才作为句内依据。
"""
from __future__ import annotations

import re

from audit.prompt import MAX_DRAFT_CHARS
from audit.schema import COMPANIES, LLMError, companies_from_facts
from backend_runtime import load_config, sentence_rows


def split_draft(draft: str) -> list[dict]:
    try:
        return [{"sentence_id": row["sentence_id"], "text": row["text"]}
                for row in sentence_rows(draft, load_config())]
    except ValueError as exc:
        raise LLMError(str(exc)) from None


def _loaded(code: str, facts: list[dict] | None) -> bool:
    """该公司是否在本次已加载材料中；facts 为 None 时退化为目录内即视为可用。"""
    if not code:
        return False
    if facts is None:
        return True
    return any(fact.get("company_code") == code for fact in facts)


def known_companies(facts: list[dict] | None = None) -> dict[str, list[str]]:
    """用于“句内提到了哪家公司”的合并名表：全局目录 + 本次材料名称。"""
    known = {code: list(names) for code, names in COMPANIES.items()}
    for code, names in (companies_from_facts(facts) or {}).items():
        bucket = known.setdefault(code, [])
        for name in names:
            if name and name not in bucket:
                bucket.append(name)
    return known


def _strength(code: str, name: str, facts: list[dict] | None) -> str:
    """名称强度：证券代码、目录主名、材料公司全名与含“公司/集团”的长名为强。"""
    if re.fullmatch(r"\d{6}", name or ""):
        return "strong"
    primary = (COMPANIES.get(code) or [""])[0]
    if name == primary:
        return "strong"
    if len(name) >= 4 or any(word in name for word in ("股份", "集团", "公司", "银行", "保险")):
        return "strong"
    for fact in facts or []:
        if fact.get("company_code") == code and fact.get("company_name") == name:
            return "strong"
    return "weak"


def explicit_companies(text: str, facts: list[dict] | None = None) -> list[dict]:
    """一段原文里明确写出的公司（名称或六位代码），按出现位置排序。

    只认原文写法，不做错别字纠正；同名时优先更长的名称，避免“茅台”覆盖“贵州茅台”。
    """
    if not text or not isinstance(text, str):
        return []
    hits: dict[str, dict] = {}
    for code, names in known_companies(facts).items():
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            position = text.find(name)
            if position < 0:
                continue
            current = hits.get(code)
            if (current is None or position < current["position"]
                    or (position == current["position"] and len(name) > len(current["matched"]))):
                hits[code] = {"code": code, "name": name, "matched": name, "position": position,
                              "strength": _strength(code, name, facts)}
    for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text):
        code = match.group(1)
        if code in hits or (code not in COMPANIES and not _loaded(code, facts)):
            continue
        hits[code] = {"code": code, "name": code, "matched": code, "position": match.start(),
                      "strength": "strong"}
    return sorted(hits.values(), key=lambda hit: (hit["position"], -len(hit["matched"])))


def resolve_name(name, facts: list[dict] | None) -> str | None:
    """把模型字段里的公司名/代码对应到本次已加载材料；不改写原文写法。"""
    if not name or not isinstance(name, str):
        return None
    hits: set[str] = set()
    for fact in facts or []:
        code = fact.get("company_code") or ""
        cname = fact.get("company_name") or ""
        if not code:
            continue
        if name == code or (cname and (name == cname or name in cname or cname in name)):
            hits.add(code)
    if len(hits) == 1:
        return hits.pop()
    if hits:
        return None
    if re.fullmatch(r"\d{6}", name):
        return name if _loaded(name, facts) else None
    code = next((c for c, names in COMPANIES.items() if name in [c, *names]), None)
    if code and _loaded(code, facts):
        return code
    return None


def _locate(name: str, *layers: tuple[str, str]) -> str | None:
    for label, text in layers:
        if text and name in text:
            return label
    return None


def segments(text: str) -> list[str]:
    """把一段上下文切成句子，供“最近可定位指代”逐句回看。"""
    return [part for part in re.split(r"(?<=[。！？；])|\r?\n+", text or "") if part.strip()]


def unique_company_in(text: str, facts: list[dict] | None) -> dict | None:
    """一段文字里唯一明确的公司（句内/段落主体判定用）；多家并列时返回 None。"""
    mentions = explicit_companies(text, facts)
    return mentions[0] if len(mentions) == 1 else None


def _year_mentions(content: str) -> list[dict]:
    """年度必须带期间语义，金额、编号和小数中的四位数不作为年度。"""
    mentions = []
    for match in re.finditer(r"(?<![\d.])((?:19|20)\d{2})(?!\d)", content or ""):
        suffix = content[match.end():]
        prefix = content[:match.start()]
        if (re.match(r"\s*(?:年|Q[1-4]\b|[-/]\d{1,2}[-/]\d{1,2}(?!\d)|\.\d{1,2}\.\d{1,2}(?!\d))", suffix, re.I)
                or re.search(r"\bFY\s*$", prefix, re.I)):
            mentions.append({"year": int(match[1]), "start": match.start(), "end": match.end()})
    return mentions


def _comparison_years(content: str, mentions: list[dict]) -> tuple[int, int] | None:
    years = {item["year"] for item in mentions}
    if len(years) != 2 or len(segments(content)) != 1:
        return None
    baseline = [item for item in mentions if re.search(
        r"(?:相比于|相较于|相较|相比|相对于|较|比|对比)\s*$", content[:item["start"]])]
    bases = {item["year"] for item in baseline}
    if len(bases) != 1:
        return None
    base = next(iter(bases))
    return next(iter(years - {base})), base


def _preceding_reference(preceding: str, facts: list[dict] | None = None,
                         year=None) -> dict | None:
    """最近的、唯一可定位的上文指代：逐句回看，遇到多义就停，不跨段乱猜。"""
    for segment in reversed(segments(preceding)):
        if year is not None:
            years = {str(item["year"]) for item in _year_mentions(segment)}
            if not years:
                continue
            if years != {str(int(year))}:
                return None
            return {"layer": "preceding_sentence", "text": segment}
        mention = unique_company_in(segment, facts)
        if mention:
            return {"layer": "preceding_sentence", "text": segment, "company": mention}
    return None


def resolve_subject(name, *, sentence: str, context: str = "", draft: str = "",
                    facts: list[dict] | None = None, paragraph: str = "",
                    preceding: str = "", prefer_field: bool = True,
                    priority_text: str = "", allow_multi_field: bool = False) -> dict:
    """公司绑定：句内明确主体优先，其次引文、段落主体、可定位指代，最后才是模型补全。

    返回 code/basis/matched/conflict/sentence_codes/note；code 为 None 时 basis 说明原因。
    """
    all_mentions = explicit_companies(sentence, facts)
    # 核查对象是逐字引文；同句另一分句不能覆盖引文的明确实体。
    mentions = explicit_companies(priority_text, facts) if priority_text else []
    mentions = mentions or all_mentions
    model_code = resolve_name(name, facts)
    strong = [mention for mention in mentions if mention["strength"] == "strong"]
    weak = [mention for mention in mentions if mention["strength"] == "weak"]
    # 短别名只在模型字段与之一致时才算句内依据，避免“美好的”误判成美的集团。
    effective = strong or [m for m in weak if model_code and m["code"] == model_code]
    ignored = [m["matched"] for m in weak if m not in effective]
    base = {"conflict": False, "model_code": model_code, "sentence_codes": [m["code"] for m in all_mentions],
            "ignored_mentions": ignored}
    if len(effective) == 1:
        only = effective[0]
        loaded = _loaded(only["code"], facts)
        if not strong:
            return {**base, "code": only["code"], "basis": "sentence_alias_confirmed_by_model",
                    "matched": only["matched"], "position": only["position"], "note": None}
        return {**base, "code": only["code"] if loaded else None,
                "basis": "sentence_company", "matched": only["matched"],
                "position": only["position"],
                "conflict": bool(model_code and model_code != only["code"]),
                "note": None if loaded else "句内公司不在本次已加载材料中"}
    if len(effective) > 1:
        codes = [mention["code"] for mention in effective]
        ordered = effective
        if priority_text:
            in_priority = [mention for mention in explicit_companies(priority_text, facts)
                           if mention["code"] in codes]
            if in_priority:
                ordered = in_priority
        chosen = None
        if prefer_field and allow_multi_field and model_code in codes:
            chosen = next(mention for mention in ordered if mention["code"] == model_code)
        elif not prefer_field:
            # 比较主语：以逐字引文里先出现的公司为准，模型字段不能覆盖句内次序。
            chosen = ordered[0]
        if chosen is not None:
            return {**base, "code": chosen["code"],
                    "basis": "sentence_multi_company_quoted" if prefer_field else "sentence_first_company",
                    "matched": chosen["matched"], "position": chosen["position"],
                    "conflict": bool(model_code and model_code != chosen["code"]),
                    "note": ("比较被比较方，要求模型字段在逐字引文中明确出现" if prefer_field
                             else "同句多公司，按引文中最先出现的主体绑定")}
        named = next((mention for mention in effective
                      if name and (mention["matched"] == name or mention["name"] == name
                                   or name in mention["matched"] or mention["matched"] in name)), None)
        if named is not None and not _loaded(named["code"], facts):
            return {**base, "code": None, "basis": "company_not_in_loaded_materials",
                    "matched": named["matched"], "position": named["position"],
                    "conflict": False,
                    "note": "模型指出的公司在句内，但不在本次已加载材料中"}
        return {**base, "code": None, "basis": "ambiguous_sentence_companies", "matched": None,
                "position": None, "conflict": bool(model_code),
                "note": "同一句出现多家公司，无法确定主张主体"}
    # 句内没有明确公司：按引文上下文、段落主体、可定位指代、整篇草稿依次继承，
    # 草稿是最后一层（最宽松），且都不会覆盖句内已明确的主体。
    where = _locate(name, ("context", context), ("draft", draft)) if name else None
    if model_code and context and name in context:
        return {**base, "code": model_code, "basis": "inherited_context", "matched": name,
                "position": None, "note": None}
    paragraph_mention = unique_company_in(paragraph, facts) if paragraph and paragraph != sentence else None
    if paragraph_mention and _loaded(paragraph_mention["code"], facts):
        return {**base, "code": paragraph_mention["code"], "basis": "inherited_paragraph",
                "matched": paragraph_mention["matched"], "position": paragraph_mention["position"],
                "conflict": bool(model_code and model_code != paragraph_mention["code"]),
                "note": None}
    reference = _preceding_reference(preceding, facts) if preceding else None
    if reference and _loaded(reference["company"]["code"], facts):
        mention = reference["company"]
        return {**base, "code": mention["code"], "basis": "inherited_preceding_sentence",
                "matched": mention["matched"], "position": None,
                "conflict": bool(model_code and model_code != mention["code"]),
                "reference_text": reference["text"][:80], "note": None}
    if model_code and where:
        return {**base, "code": model_code, "basis": f"inherited_{where}", "matched": name,
                "position": None, "note": None}
    if where and not model_code:
        return {**base, "code": None, "basis": "company_not_in_loaded_materials", "matched": name,
                "position": None, "note": "原文提到公司，但不在本次已加载材料中"}
    return {**base, "code": None, "basis": "ungrounded_company", "matched": None, "position": None,
            "note": "模型给出的公司名既不在句内，也无法在上下文、段落或草稿中定位"}


def resolve_period(year, *, sentence: str = "", context: str = "", quote: str = "",
                   paragraph: str = "", preceding: str = "") -> dict:
    """引文/本句明确年度优先；模型与原文冲突单列，多年度未定位则拒绝继承。"""
    model_year = int(year) if year is not None else None
    base = {"year": model_year, "model_year": model_year, "conflict": False,
            "located_in": None, "matched_text": None}
    for label, content in (("quote", quote), ("sentence", sentence)):
        mentions = _year_mentions(content or "")
        years = {item["year"] for item in mentions}
        if len(years) == 1:
            actual = int(next(iter(years)))
            return {**base, "year": actual, "located_in": label, "matched_text": str(actual),
                    "conflict": model_year is not None and model_year != actual}
        if len(years) > 1:
            comparison = _comparison_years(content, mentions)
            if comparison:
                actual, prior = comparison
                return {**base, "year": actual, "comparison_year": prior,
                        "located_in": label, "matched_text": str(actual),
                        "binding_rule": "explicit_comparison_roles",
                        "conflict": model_year is not None and model_year != actual}
            return {**base, "ambiguous": True}
    if model_year is None:
        return base
    for label, content in (("context", context), ("paragraph", paragraph)):
        years = {item["year"] for item in _year_mentions(content or "")}
        if years == {model_year}:
            return {**base, "located_in": label, "matched_text": str(model_year)}
    reference = _preceding_reference(preceding, year=model_year) if preceding else None
    if reference:
        return {**base, "located_in": "preceding_sentence", "matched_text": str(model_year),
                "reference_text": reference["text"][:80]}
    return base


def resolve_company(name, source: str, draft: str, facts: list[dict] | None = None,
                    *, sentence: str | None = None, context: str = "") -> str | None:
    """公司名 → 证券代码（兼容旧签名）。

    必须同时满足：①在本句引文、上下文或整份草稿里能指到 ②能对应到本次已加载材料。
    只认原文写法（错别字不纠正）；句内明确另一主体时不会被模型补全覆盖。
    """
    subject = resolve_subject(name, sentence=sentence if sentence is not None else source,
                              context=context or (source if sentence is not None else ""),
                              draft=draft, facts=facts)
    return subject["code"]


__all__ = ["explicit_companies", "known_companies", "resolve_company", "resolve_name",
           "resolve_period", "resolve_subject", "split_draft"]
