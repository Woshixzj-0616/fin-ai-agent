"""Minimal report context passed to independent analysis modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.core.model_io import model_name, recorded_completion
from backend.core.recorder import RunRecorder


@dataclass
class ReportContext:
    report_id: str
    file_name: str
    file_sha256: str
    page_count: int
    pages: list[dict[str, Any]]
    recorder: RunRecorder
    initial_pages: list[dict[str, Any]] = field(default_factory=list)
    related_results: dict[str, Any] = field(default_factory=dict)
    _get_page: Callable[[int], str | None] | None = None
    _search_pages: Callable[[str], list[dict[str, Any]]] | None = None
    _on_progress: Callable[[str, str, str], None] | None = None
    _on_tool: Callable[[str, str], None] | None = None

    def read_page(self, page_number: int) -> str | None:
        if page_number < 1 or page_number > self.page_count:
            self.recorder.record("page_read_failed", page=page_number, error="page_out_of_range")
            return None
        try:
            text = (
                self._get_page(page_number)
                if self._get_page
                else next((str(page["text"]) for page in self.pages if int(page["page"]) == page_number), None)
            )
        except Exception as exc:
            self.recorder.record("page_read_failed", page=page_number, error_type=type(exc).__name__)
            raise
        artifact = self.recorder.save_artifact(f"page_{page_number}", {"page": page_number, "text": text})
        self.recorder.record(
            "page_read",
            page=page_number,
            available=text is not None,
            artifact=artifact,
        )
        return text

    def search_pages(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        search_id = self.recorder.record("search_started", query=query)["event_id"]
        try:
            if self._search_pages:
                hits = self._search_pages(query)
            else:
                from backend.pdf_reader import rank_search_pages

                hits = rank_search_pages(self.pages, query, limit=limit)
        except Exception as exc:
            self.recorder.record("search_failed", query=query, error_type=type(exc).__name__)
            raise
        artifact = self.recorder.save_artifact(
            f"search_{search_id}",
            {"query": query, "results": hits},
        )
        self.recorder.record(
            "search_completed",
            query=query,
            result_count=len(hits),
            pages=[int(item["page"]) for item in hits if item.get("page") is not None],
            artifact=artifact,
        )
        return hits

    def call_model(self, **request: Any) -> Any:
        request.setdefault("model", model_name())
        return recorded_completion(recorder=self.recorder, **request)

    def record_tool(self, name: str, arguments: Any, result: Any = None, *, error: str = "", pages: list[int] | None = None) -> None:
        self.recorder.tool_call(name, arguments, result, error=error, pages=pages)

    def record_calculation(self, name: str, *, inputs: Any, formula: str, output: Any, rule_version: str = "") -> None:
        self.recorder.calculation(
            name,
            inputs=inputs,
            formula=formula,
            output=output,
            rule_version=rule_version,
        )

    def progress(self, stage: str, status: str, detail: str = "") -> None:
        if self._on_progress:
            self._on_progress(stage, status, detail)
        else:
            self.recorder.progress(stage, status, detail)

    def tool_activity(self, name: str, detail: str = "") -> None:
        self.recorder.record("module_tool_activity", tool=name, detail=detail)
        if self._on_tool:
            self._on_tool(name, detail)

    def save_artifact(self, name: str, value: Any) -> dict[str, Any]:
        return self.recorder.save_artifact(name, value)
