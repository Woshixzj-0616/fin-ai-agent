"""七个只读业务工具的规格唯一源。"""
from __future__ import annotations

from audit.schema import schema


TOOL_NAMES = (
    "list_catalog",
    "find_evidence",
    "compute_yoy",
    "compare_claim",
    "compare_companies",
    "compute_trend",
    "search_text",
)


def tool_specs() -> list[dict]:
    """OpenAI 兼容的 tools 列表；JSON 多步协议里也用同一套 name/parameters。"""
    return [
        {
            "name": "list_catalog",
            "description": "列出本次已加载公司、指标键名与别名、单位与运算符枚举。只读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": ["companies", "metrics", "units", "operators", "verdicts"]},
                },
                "required": ["kind"],
            },
        },
        {
            "name": "find_evidence",
            "description": "按公司、指标、期间取唯一证据（页码/原值/bbox，含报告类型与材料 ID）。0 条或多条时如实返回，禁止自行选一条。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string", "description": "目录键，如 revenue"},
                    "period_year": {"type": "integer"},
                    "source_report_year": {"type": "integer",
                                           "description": "缺省与 period_year 相同"},
                    "report_kind": {"type": "string",
                                    "enum": ["annual", "half", "quarter", "instant", "other"],
                                    "description": "可选；未给时按期间年份取证，多候选返回 ambiguous"},
                    "period_start": {"type": "string", "description": "可选；精确期间起（YYYY-MM-DD）"},
                    "period_end": {"type": "string", "description": "可选；精确期间止（YYYY-MM-DD）"},
                    "document_id": {"type": "string", "description": "可选；指定材料 ID，避免同年多报告混用"},
                },
                "required": ["company_name_or_code", "metric", "period_year"],
            },
        },
        {
            "name": "compute_yoy",
            "description": "算年度同比（本地 Decimal）。返回计算式与参与的 evidence_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string"},
                    "year": {"type": "integer"},
                    "report_kind": {"type": "string",
                                    "enum": ["annual", "half", "quarter", "instant", "other"],
                                    "description": "可选；缺省按期间年份取证"},
                    "period_start": {"type": "string", "description": "可选；精确期间起"},
                    "period_end": {"type": "string", "description": "可选；精确期间止"},
                    "document_id": {"type": "string", "description": "可选；指定材料 ID"},
                },
                "required": ["company_name_or_code", "metric", "year"],
            },
        },
        {
            "name": "compare_claim",
            "description": "把草稿数值主张与证据比对。**唯一**能产生 证据支持/确认错误 的工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string"},
                    "period_year": {"type": "integer"},
                    "kind": {"type": "string", "enum": ["amount", "yoy"]},
                    "claimed_value": {"type": "string"},
                    "claimed_unit": {"type": "string"},
                    "operator": {
                        "type": "string",
                        "enum": ["eq", "approx", "exceed", "at_least", "at_most", "below"],
                    },
                    "tolerance_pct": {"type": ["number", "null"],
                                      "description": "仅 approx；默认 2，上限 5"},
                    "direction": {"type": "string", "enum": ["up", "down", "unknown"]},
                    "scope": {"type": "string",
                              "enum": ["consolidated", "parent_shareholders",
                                       "parent_company", "unknown"]},
                    "report_kind": {"type": "string",
                                    "enum": ["annual", "half", "quarter", "instant", "other"],
                                    "description": "可选；缺省用命中证据的报告类型，不再固定按年度"},
                    "period_start": {"type": "string", "description": "可选；精确期间起（YYYY-MM-DD）"},
                    "period_end": {"type": "string", "description": "可选；精确期间止（YYYY-MM-DD）"},
                    "document_id": {"type": "string", "description": "可选；指定材料 ID"},
                },
                "required": ["company_name_or_code", "metric", "period_year", "kind",
                             "claimed_value", "claimed_unit", "operator"],
            },
        },
        {
            "name": "compare_companies",
            "description": "跨公司比较两家同指标同年数值（本地 Decimal）。可确定性裁决「茅台营收高于五粮液」类主张。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_a": {"type": "string", "description": "陈述主语公司（「A 高于 B」中的 A）"},
                    "company_b": {"type": "string", "description": "被比较公司"},
                    "metric": {"type": "string"},
                    "period_year": {"type": "integer"},
                    "operator": {"type": "string",
                                 "enum": ["exceed", "at_least", "at_most", "below", "eq"],
                                 "description": "exceed=A>B at_least=A>=B at_most=A<=B below=A<B eq=A≈B"},
                    "report_kind": {"type": "string",
                                    "enum": ["annual", "half", "quarter", "instant", "other"],
                                    "description": "可选；两侧使用同一报告类型"},
                    "period_start": {"type": "string", "description": "可选；精确期间起"},
                    "period_end": {"type": "string", "description": "可选；精确期间止"},
                    "document_id_a": {"type": "string", "description": "可选；A 侧材料 ID"},
                    "document_id_b": {"type": "string", "description": "可选；B 侧材料 ID"},
                },
                "required": ["company_a", "company_b", "metric", "period_year", "operator"],
            },
        },
        {
            "name": "compute_trend",
            "description": "多年趋势：逐年同比 + CAGR + 是否连续增长/下降。覆盖「连续三年增长」「累计增长」类主张。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "metric": {"type": "string"},
                    "start_year": {"type": "integer"},
                    "end_year": {"type": "integer"},
                    "report_kind": {"type": "string",
                                    "enum": ["annual", "half", "quarter", "instant", "other"],
                                    "description": "可选；缺省按期间年份取证"},
                    "document_id": {"type": "string", "description": "可选；指定材料 ID"},
                },
                "required": ["company_name_or_code", "metric", "start_year", "end_year"],
            },
        },
        {
            "name": "search_text",
            "description": "在年报全文做语义检索（BM25/向量），返回相关段落。命中**不能**单独作为数值裁决依据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name_or_code": {"type": "string"},
                    "source_report_year": {"type": "integer"},
                    "query": {"type": "string", "description": "检索问题或短语，≤40字"},
                    "top_k": {"type": "integer", "description": "返回条数，默认 8，上限 20"},
                    "report_kind": {"type": "string",
                                    "enum": ["annual", "half", "quarter", "instant", "other"],
                                    "description": "可选；同年多报告时用它区分季报/半年报/年报"},
                    "document_id": {"type": "string",
                                    "description": "可选；材料 ID；多候选未指定时返回 ambiguous"},
                },
                "required": ["company_name_or_code", "source_report_year", "query"],
            },
        },
    ]


def openai_tools() -> list[dict]:
    """由业务规格和主 Schema 派生 OpenAI function tools，不维护第二份字段。"""
    out = []
    for spec in tool_specs():
        out.append({
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        })
    contract = schema()
    out.append({
        "type": "function",
        "function": {
            "name": "submit_claims",
            "description": "核查完成，提交结构化主张。items 必须符合主协议 Schema，禁止额外字段。",
            "parameters": {
                "type": "object",
                "properties": contract["properties"],
                "required": contract["required"],
                "additionalProperties": False,
            },
        },
    })
    return out
