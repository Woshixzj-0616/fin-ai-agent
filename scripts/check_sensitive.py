"""只读敏感信息扫描；输出位置和规则名，不回显命中值。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.runtime.redaction import SECRET_PATTERNS

SKIP_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "data",
    "results",
    "logs",
    "node_modules",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_FILE_BYTES = 2 * 1024 * 1024


def _git_candidates(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={root.as_posix()}",
                "-C",
                str(root),
                "ls-files",
                "-co",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    output = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        output.append(root / raw.decode("utf-8", errors="surrogateescape"))
    return output


def _is_candidate(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    try:
        return path.is_file() and path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def _iter_candidates(root: Path, paths: list[Path]) -> list[Path]:
    if paths:
        candidates = []
        for path in paths:
            if path.is_dir():
                candidates.extend(item for item in path.rglob("*") if _is_candidate(item))
            elif _is_candidate(path):
                candidates.append(path)
        return sorted(set(candidates))
    candidates = [path for path in _git_candidates(root) if _is_candidate(path)]
    if not candidates:
        candidates = [path for path in root.rglob("*") if _is_candidate(path)]
    return sorted(set(candidates))


def scan_text(text: str) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule_name, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            if rule_name in {"authorization_bearer", "environment_secret"}:
                value = match.group(3) if rule_name == "environment_secret" else match.group(2)
                normalized = value.strip("'\"<>[]{}() ")
                lowered = normalized.lower()
                if (
                    not normalized
                    or set(normalized) <= {".", "*", "…"}
                    or "..." in normalized
                    or "your_" in lowered
                    or "example" in lowered
                    or "placeholder" in lowered
                    or "redacted" in lowered
                    or "已隐藏" in normalized
                    or any(char in value for char in "<>{}")
                ):
                    continue
            hits.append((rule_name, line_number))
            break
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读扫描密钥、token 和私钥")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--path", action="append", type=Path, default=[])
    args = parser.parse_args(argv)

    root = args.root.resolve()
    paths = [
        path if path.is_absolute() else root / path
        for path in args.path
    ]
    findings: list[str] = []
    errors: list[str] = []
    for path in _iter_candidates(root, paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            errors.append(f"{path.relative_to(root)}: 读取失败: {type(exc).__name__}")
            continue
        for rule_name, line_number in scan_text(text):
            try:
                display = path.relative_to(root).as_posix()
            except ValueError:
                display = path.as_posix()
            findings.append(
                f"{display}:{line_number}:敏感信息规则 {rule_name}"
            )
    for message in errors:
        print(message)
    for finding in findings:
        print(finding)
    if findings or errors:
        print(
            f"敏感信息扫描失败：命中 {len(findings)}，读取错误 {len(errors)}",
            file=sys.stderr,
        )
        return 1
    print(f"敏感信息扫描通过：扫描 {len(_iter_candidates(root, paths))} 个文本文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
