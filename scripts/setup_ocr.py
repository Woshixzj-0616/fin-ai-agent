"""下载固定版本的可选 OCR 语言数据；HTTPS + 指纹核验，不安装系统软件。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
VERSION = "4.1.0"
HASHES = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "chi_sim": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
}


def main():
    directory = ROOT / "data/ocr/tessdata"
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for language, expected in HASHES.items():
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{VERSION}/{language}.traineddata"
        path = directory / f"{language}.traineddata"
        if path.exists():
            blob = path.read_bytes()
        else:
            with urlopen(Request(url, headers={"User-Agent": "fin-ai-agent/ocr-setup"}), timeout=60) as response:
                blob = response.read()
        fingerprint = hashlib.sha256(blob).hexdigest()
        if fingerprint != expected:
            raise ValueError(f"{language} 语言数据指纹不同，停止；不覆盖已有文件")
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(blob)
            temporary.replace(path)
        rows.append({"language": language, "version": VERSION, "url": url, "sha256": fingerprint,
                     "license": "Apache-2.0", "scope": "optional OCR only"})
        print(f"{language}：指纹已核验，{len(blob)} bytes")
    (directory.parent / "manifest.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
