"""主张协议、公司/指标目录和本地 Schema 校验。"""
from __future__ import annotations

import re
from pathlib import Path

from extract import METRICS as _EXTRACT_METRICS
from materials import ROOT


COMPANY_ALIASES = {
    "600519": ["茅台", "贵州茅台酒股份有限公司"],
    "000858": ["宜宾五粮液股份有限公司"],
    "600887": ["伊利", "内蒙古伊利实业集团股份有限公司"],
    "000333": ["美的", "美的集团股份有限公司"],
    "000651": ["格力", "珠海格力电器股份有限公司"],
    "600276": ["恒瑞", "江苏恒瑞医药股份有限公司"],
    "300760": ["迈瑞", "深圳迈瑞生物医疗电子股份有限公司"],
    "002594": ["比亚迪股份有限公司"],
    "601088": ["神华", "中国神华能源股份有限公司"],
    "600036": ["招行", "招商银行股份有限公司"],
    "601318": ["平安", "中国平安保险股份有限公司", "中国平安保险(集团)股份有限公司"],
    "000002": ["万科", "万科企业股份有限公司"],
    "600900": ["长电", "长江电力股份有限公司"],
    "002415": ["海康", "杭州海康威视数字技术股份有限公司"],
}


def load_companies(scope_csv: Path | None = None) -> dict[str, list[str]]:
    """证券代码 → [主名, 别名…]。主名读 data/scope.csv，缺文件时退回别名表主名。"""
    names: dict[str, list[str]] = {}
    path = scope_csv or (ROOT / "data" / "scope.csv")
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines()[1:]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[0]:
                names[parts[0]] = [parts[1]]
    for code, aliases in COMPANY_ALIASES.items():
        primary = names.get(code) or [aliases[0]]
        merged = list(dict.fromkeys([*primary, *aliases]))
        names[code] = merged
    return names


COMPANIES = load_companies()


def companies_from_facts(facts: list[dict] | None) -> dict[str, list[str]]:
    """从本次证据里汇总 公司代码 → [名称…]。任何已登记材料的公司都在册，不靠固定名单。"""
    if not facts:
        return {}
    names: dict[str, list[str]] = {}
    for fact in facts:
        code = fact.get("company_code")
        if not code:
            continue
        bucket = names.setdefault(code, [])
        for candidate in (fact.get("company_name"), code):
            if candidate and candidate not in bucket:
                bucket.append(candidate)
    return names


METRICS = {key: [name, *[a for a in aliases if a != name]]
           for key, (name, aliases) in _EXTRACT_METRICS.items()}
METRIC_NOTE = {
    "total_revenue": "营业总收入 ≠ 营业收入，禁止互替",
    "revenue": "营业收入 ≠ 营业总收入",
}
UNITS = {"元": "元", "千元": "千元", "万元": "万元", "万": "万元",
         "百万元": "百万元", "亿元": "亿元", "亿": "亿元",
         "%": "%", "％": "%", "元/股": "元/股", "元／股": "元/股"}
OPERATORS = ("eq", "approx", "exceed", "at_least", "at_most", "below", "none")
CLAIM_TYPES = ("amount", "yoy", "direction", "comparison", "qualitative", "forecast", "other")
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
MAX_ITEMS = 60
DEFAULT_TOLERANCE_PCT = 2.0


class LLMError(ValueError):
    """只携带可安全写入日志的固定说明，不包含请求头或服务端原始错误。"""

    def __init__(self, message, *, terminal=False, request_status=None, failure_code=None):
        super().__init__(message)
        self.terminal = terminal
        self.request_status = request_status
        self.failure_code = failure_code


def schema() -> dict:
    nullable_string = {"type": ["string", "null"]}
    nullable_number = {"type": ["number", "null"]}
    nullable_integer = {"type": ["integer", "null"]}
    fields = {
        "claim_id": {"type": "string"},
        "sentence_id": {"type": "integer"},
        "quote": {"type": "string", "description": "当前主张的连续原文，不改写"},
        "context_quote": {**nullable_string, "description": "公司/年度来自上文时逐字摘录；否则null"},
        "claim_type": {"type": "string", "enum": list(CLAIM_TYPES),
                       "description": "amount金额 yoy同比 direction方向 comparison跨公司比较 qualitative定性 forecast预测"},
        "company_name": {**nullable_string, "description": "原文公司名称或证券代码，不纠正错别字、不猜测"},
        "company_b": {**nullable_string, "description": "comparison 时被比较的公司；非比较主张填 null"},
        "comparison_operator": {
            "type": ["string", "null"],
            "enum": ["exceed", "at_least", "at_most", "below", "eq", None],
            "description": "comparison 的方向：exceed=A高于B；null=非比较主张"},
        "period_year": nullable_integer,
        "period_kind": {"type": "string", "enum": ["annual", "half", "quarter", "instant", "other", "unknown"]},
        "metric_text": {"type": "string", "description": "原文指标名称，不替换为另一个指标"},
        "metric": {"type": "string", "enum": [*METRICS, "unsupported", "unknown"],
                   "description": "对齐到目录键；对不上用 unknown，禁止硬凑"},
        "scope": {"type": "string", "enum": ["consolidated", "parent_shareholders", "parent_company", "unknown"]},
        "kind": {"type": "string", "enum": ["amount", "yoy", "unsupported", "unknown"]},
        "value": {**nullable_string, "description": "逐字保留阿拉伯数字及小数位；不换算、不计算"},
        "unit": {**nullable_string, "description": "逐字保留单位；不换算"},
        "operator": {"type": "string", "enum": list(OPERATORS),
                     "description": "eq精确 approx约 exceed超过 at_least不低于 at_most不高于 below低于 none无数值约束"},
        "tolerance_pct": nullable_number,
        "direction": {"type": "string", "enum": ["up", "down", "unknown"]},
        "currency": {"type": "string", "enum": ["CNY", "USD", "other", "unknown"]},
        "qualifier": {"type": "string", "enum": ["exact", "approximate", "inequality", "unknown"]},
        "plain_claim": {"type": "string", "description": "用自己的话复述主张，≤80字，供审计"},
        "is_forecast": {"type": "boolean"},
        "ambiguity": {"type": "string", "enum": ["none", "metric", "period", "scope", "value", "multiple"]},
        "verification_action": {
            "type": "string",
            "enum": ["compare_amount", "compare_yoy", "check_direction",
                     "mark_forecast", "mark_out_of_scope", "needs_review"]},
    }
    return {"type": "object", "additionalProperties": False,
            "required": ["items", "unclaimed_sentences"], "properties": {
                "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                            "properties": fields, "required": list(fields)}},
                "unclaimed_sentences": {"type": "array", "items": {"type": "integer"}},
            }}


def validate_schema(value, spec: dict) -> None:
    """针对上面的简单JSON Schema本地复核；JSON mode也不得跳过字段校验。"""
    kinds = {"object": dict, "array": list, "integer": int, "string": str, "null": type(None),
             "number": "number", "boolean": bool}
    allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]

    def type_ok(val, kind: str) -> bool:
        if kind == "number":
            return type(val) in (int, float) and not isinstance(val, bool)
        if kind == "integer":
            return type(val) is int
        return type(val) is kinds[kind]

    if not any(type_ok(value, kind) for kind in allowed):
        raise LLMError("模型返回的字段类型不符合核查协议")
    if "enum" in spec and value not in spec["enum"]:
        raise LLMError("模型返回了核查协议之外的枚举值")
    if isinstance(value, dict):
        # 可空字段允许省略（模型对 comparison 之外的主张不填 company_b 等）；
        # 必填字段缺失或出现额外字段仍拒绝。
        required = set(spec.get("required") or ())
        props = spec.get("properties") or {}
        optional = {k for k, v in props.items()
                    if isinstance(v, dict) and "null" in (v.get("type") or [])}
        missing = required - set(value) - optional
        extra = set(value) - required
        if missing or extra:
            raise LLMError("模型返回的字段缺失或包含额外字段")
        for key, item in value.items():
            validate_schema(item, spec["properties"][key])
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise LLMError("模型返回的核查项过多，请缩短草稿")
        for item in value:
            validate_schema(item, spec["items"])
    if isinstance(value, str) and len(value) > 2000:
        raise LLMError("模型返回的字段过长，请缩短草稿")
