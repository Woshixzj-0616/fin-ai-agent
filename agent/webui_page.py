"""旧兼容页面的唯一加载入口。"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PAGE_PATH = ROOT / "web" / "legacy" / "index.html"
PAGE_PNG = re.compile(r"^\d{6}_\d{4}_[0-9a-f]{8}_p\d+\.png$")

with LEGACY_PAGE_PATH.open("r", encoding="utf-8", newline="") as stream:
    PAGE = stream.read()


__all__ = ["LEGACY_PAGE_PATH", "PAGE", "PAGE_PNG"]
