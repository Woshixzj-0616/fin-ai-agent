"""旧 Web 导入与启动兼容门面；实现源已迁到 webui_* 模块。"""
from __future__ import annotations

try:
    from webui_app import main as _main
    from webui_http import (
        Handler,
        MAX_UPLOAD,
        WEB_MIME_TYPES,
        WEB_ROOT,
        _API_INIT_LOCK,
        _RATE_CAPACITY,
        _RATE_REFILL_PER_SEC,
        _cleanup_work_dir,
        _json,
        _json_api_error,
        _parse_multipart,
        _rate_buckets,
        _rate_limit,
        _rate_lock,
        _resolve_web_asset,
    )
    from webui_page import PAGE, PAGE_PNG
    from webui_pipeline import (
        LAST_DOCUMENT_TEXTS,
        LAST_FACTS,
        WORKSPACE,
        _ANALYZE_LOCK,
        _LAST_LOCK,
        _WORK_TTL_SEC,
        _check_draft,
        guess_identity,
        run_announcement_pipeline,
        run_pipeline,
    )
except ModuleNotFoundError:
    from agent.webui_app import main as _main
    from agent.webui_http import (
        Handler,
        MAX_UPLOAD,
        WEB_MIME_TYPES,
        WEB_ROOT,
        _API_INIT_LOCK,
        _RATE_CAPACITY,
        _RATE_REFILL_PER_SEC,
        _cleanup_work_dir,
        _json,
        _json_api_error,
        _parse_multipart,
        _rate_buckets,
        _rate_limit,
        _rate_lock,
        _resolve_web_asset,
    )
    from agent.webui_page import PAGE, PAGE_PNG
    from agent.webui_pipeline import (
        LAST_DOCUMENT_TEXTS,
        LAST_FACTS,
        WORKSPACE,
        _ANALYZE_LOCK,
        _LAST_LOCK,
        _WORK_TTL_SEC,
        _check_draft,
        guess_identity,
        run_announcement_pipeline,
        run_pipeline,
    )


def main() -> int:
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Handler",
    "LAST_DOCUMENT_TEXTS",
    "LAST_FACTS",
    "MAX_UPLOAD",
    "PAGE",
    "PAGE_PNG",
    "WEB_MIME_TYPES",
    "WEB_ROOT",
    "WORKSPACE",
    "_ANALYZE_LOCK",
    "_API_INIT_LOCK",
    "_LAST_LOCK",
    "_RATE_CAPACITY",
    "_RATE_REFILL_PER_SEC",
    "_WORK_TTL_SEC",
    "_check_draft",
    "_cleanup_work_dir",
    "_json",
    "_json_api_error",
    "_parse_multipart",
    "_rate_buckets",
    "_rate_limit",
    "_rate_lock",
    "_resolve_web_asset",
    "guess_identity",
    "main",
    "run_announcement_pipeline",
    "run_pipeline",
]
