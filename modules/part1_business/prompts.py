"""Versioned prompt assembly owned by the business-analysis module."""

from __future__ import annotations

from pathlib import Path


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_STAGE_FILES = {
    "extract": "事实提取_v3.md",
    "interpret": "分析与交接_v3.md",
    "question": "追问_v3.md",
}


def business_prompt(stage: str | None = None) -> str:
    """Return the module-owned base prompt, optionally with a stage guide."""
    parts = [(PROMPT_DIR / "主提示_v3.md").read_text(encoding="utf-8")]
    if stage is not None:
        try:
            stage_file = _STAGE_FILES[stage]
        except KeyError as exc:
            raise ValueError(f"Unknown business-analysis prompt stage: {stage}") from exc
        parts.append((PROMPT_DIR / stage_file).read_text(encoding="utf-8"))
    return "\n\n".join(parts)
