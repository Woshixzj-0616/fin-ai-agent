"""Markdown 文档检查：UTF-8、控制字符、围栏和本地链接。"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _markdown_files(root: Path, paths: list[Path]) -> list[Path]:
    if paths:
        candidates: list[Path] = []
        for path in paths:
            if path.is_dir():
                candidates.extend(path.rglob("*.md"))
            elif path.suffix.lower() == ".md":
                candidates.append(path)
    else:
        candidates = list(root.rglob("*.md"))
    return sorted(
        {
            path.resolve()
            for path in candidates
            if path.is_file()
            and not any(part in SKIP_PARTS for part in path.parts)
            and (bool(paths) or not path.resolve().is_relative_to(root / "results" / "runs"))
        }
    )


def _fence_errors(text: str) -> list[str]:
    fences: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker not in {"```", "~~~"}:
            continue
        if not fences:
            fences.append((marker, line_number))
        elif fences[-1][0] == marker:
            fences.pop()
        else:
            return [
                f"第 {line_number} 行围栏标记 {marker} 与第 "
                f"{fences[-1][1]} 行的 {fences[-1][0]} 不配对"
            ]
    if fences:
        return [f"第 {fences[-1][1]} 行围栏未闭合"]
    return []


def _link_target_is_local(raw_target: str) -> bool:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return False
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return False
    return bool(parsed.path)


def _local_link_errors(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in LINK_RE.finditer(line):
            raw_target = match.group(1)
            if not _link_target_is_local(raw_target):
                continue
            target = unquote(raw_target.strip().strip("<>"))
            local_path = urlsplit(target).path
            if not local_path or local_path.startswith("/"):
                continue
            resolved = (path.parent / local_path).resolve()
            if not resolved.exists():
                errors.append(
                    f"第 {line_number} 行本地链接不存在：{local_path}"
                )
    return errors


def check_file(path: Path, root: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"不是合法 UTF-8：{exc}"]
    except OSError as exc:
        return [f"读取失败：{type(exc).__name__}"]
    try:
        display = path.relative_to(root)
    except ValueError:
        display = path
    errors = [f"{display}: {message}" for message in _fence_errors(text)]
    errors.extend(f"{display}: {message}" for message in _local_link_errors(path, text))
    if CONTROL_RE.search(text):
        errors.append(f"{display}: 包含控制字符")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 Markdown 编码、围栏和本地链接")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--path", action="append", type=Path, default=[])
    args = parser.parse_args(argv)

    root = args.root.resolve()
    paths = [path if path.is_absolute() else root / path for path in args.path]
    files = _markdown_files(root, paths)
    errors: list[str] = []
    for path in files:
        errors.extend(check_file(path, root))
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"文档检查失败：{len(errors)} 项", file=sys.stderr)
        return 1
    print(f"文档检查通过：{len(files)} 个 Markdown 文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
