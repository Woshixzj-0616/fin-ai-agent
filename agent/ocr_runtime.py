"""可选 OCR 运行时：固定语言包指纹、文字层和原页快照。"""
from __future__ import annotations

import os
from pathlib import Path

from materials import ROOT, sha256

LANGUAGES = ("chi_sim", "eng")
LANGUAGE_SPEC = "+".join(LANGUAGES)
DPI = 200


def tessdata_directory() -> Path | None:
    configured = os.environ.get("TESSDATA_PREFIX")
    directory = Path(configured) if configured else ROOT / "data" / "ocr" / "tessdata"
    if all((directory / f"{language}.traineddata").is_file() for language in LANGUAGES):
        return directory
    return None


def ocr_textpage(page, run, *, purpose: str):
    directory = tessdata_directory()
    if directory is None:
        raise ValueError("本地 OCR 语言数据未配置；运行 python3 scripts/setup_ocr.py "
                         "或设置 TESSDATA_PREFIX")
    hashes = {
        language: sha256((directory / f"{language}.traineddata").read_bytes())
        for language in LANGUAGES
    }
    try:
        textpage = page.get_textpage_ocr(
            language=LANGUAGE_SPEC,
            dpi=DPI,
            full=True,
            tessdata=str(directory) + os.sep,
        )
    except Exception as exc:
        hint = ""
        if not str(directory).isascii():
            hint = "；当前 Tesseract 数据路径含非 ASCII 字符，请把 TESSDATA_PREFIX 指向 ASCII 目录"
        raise ValueError("本地 OCR 初始化失败" + hint) from exc
    run.event("ocr_used", purpose=purpose, page=page.number + 1,
              language=LANGUAGE_SPEC, dpi=DPI,
              language_data_sha256=hashes, status="needs_review")
    return textpage, {
        "language": LANGUAGE_SPEC,
        "dpi": DPI,
        "language_data_sha256": hashes,
    }


def page_snapshot(page, run, *, prefix: str) -> tuple[str, str]:
    name = f"{prefix}_page_{page.number + 1}.png"
    pixmap = page.get_pixmap(dpi=144, alpha=False)
    blob = pixmap.tobytes("png")
    run.output(name).write_bytes(blob)
    fingerprint = sha256(blob)
    run.event("ocr_page_snapshot", page=page.number + 1, image=name,
              sha256=fingerprint, dpi=144)
    return name, fingerprint
