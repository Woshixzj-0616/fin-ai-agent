"""旧兼容页面的唯一加载入口。"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PAGE_PATH = ROOT / "web" / "legacy" / "index.html"
PAGE_PNG = re.compile(r"^\d{6}_\d{4}_[0-9a-f]{8}_p\d+\.png$")


def read_page_text(path: Path | None = None) -> str:
    """兼容门面页面的唯一读取口径：原始字节按 UTF-8 解码，保留文件里的换行。

    newline="" 关闭通用换行转换，因此 Windows 克隆上的 CRLF 不会被读成 LF；
    HTTP 响应、PAGE 常量和测试都走这里，避免出现两套换行口径。
    """
    with Path(path or LEGACY_PAGE_PATH).open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


PAGE = read_page_text()


__all__ = ["LEGACY_PAGE_PATH", "PAGE", "PAGE_PNG", "read_page_text"]
