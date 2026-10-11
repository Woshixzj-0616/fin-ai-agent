"""旧导入兼容门面：实现源位于 ``audit.loop``。"""
from audit.schema import LLMError, schema, validate_schema
from audit.tools.dispatch import dispatch
from audit.tools.specs import tool_specs
from audit.loop import (
    LOOP_SYSTEM,
    MAX_REPAIR_ROUNDS,
    MAX_ROUNDS,
    MAX_TOOL_CALLS,
    _openai_tools,
    _repair_messages,
    parse_loop_reply,
    run_loop,
    run_loop_native,
    tool_summary,
)
