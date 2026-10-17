"""结构审查：体量警告、依赖方向和旧 API 新前端调用。"""
from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WARN_LINES = 1200
WARN_BYTES = 80 * 1024
WEBUI_LINE_LIMIT = 300
OLD_API_RE = re.compile(
    r"/api/(?:prefill|analyze|announcement|ask|search)(?:[\"'/?]|$)"
)
FORBIDDEN_PACKAGES = ("webui", "webui_app", "api")


def _display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _module_is_forbidden(module: str) -> bool:
    normalized = str(module or "").strip(".")
    if not normalized:
        return False
    parts = normalized.split(".")
    if parts[0] == "agent":
        parts = parts[1:]
    return bool(parts) and parts[0] in FORBIDDEN_PACKAGES


def _forbidden_imports(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            modules.extend(
                f"{node.module}.{alias.name}" if node.module else alias.name
                for alias in node.names
            )
        else:
            continue
        if any(_module_is_forbidden(module) for module in modules):
            violations.append(f"{path}:{node.lineno}")
    return violations


def _review_files(root: Path) -> dict:
    violations: list[str] = []
    warnings: list[dict] = []
    targets = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(
            part in {".git", ".venv", "venv", "__pycache__", "data", "results", "logs"}
            for part in path.parts
        )
        and path.suffix.lower() in {".py", ".js", ".css", ".html", ".md"}
    ]
    for path in sorted(targets):
        relative = _display(path, root)
        try:
            data = path.read_bytes()
            line_count = data.count(b"\n") + 1
        except OSError:
            continue
        if line_count > WARN_LINES or len(data) > WARN_BYTES:
            warnings.append(
                {
                    "path": relative,
                    "lines": line_count,
                    "bytes": len(data),
                    "warning": "超过 1200 行或 80KiB；只审查不直接失败",
                }
            )
        if relative.startswith("agent/runtime/") and path.suffix == ".py":
            violations.extend(
                f"{message}: runtime 不得导入 webui/api"
                for message in _forbidden_imports(path)
            )
        if (
            relative.startswith("agent/extraction/")
            or relative.startswith("agent/audit/")
        ) and path.suffix == ".py":
            violations.extend(
                f"{message}: 业务包不得导入 webui/api"
                for message in _forbidden_imports(path)
            )
        if relative.startswith("web/") and relative.endswith(".js"):
            if not relative.startswith("web/legacy/"):
                text = data.decode("utf-8", errors="replace")
                for match in OLD_API_RE.finditer(text):
                    line_number = text.count("\n", 0, match.start()) + 1
                    violations.append(
                        f"{relative}:{line_number}:新前端不得调用旧 API {match.group(0)}"
                    )
    webui_path = root / "agent" / "webui.py"
    webui_lines = 0
    if webui_path.is_file():
        webui_lines = webui_path.read_bytes().count(b"\n") + 1
        if webui_lines > WEBUI_LINE_LIMIT:
            violations.append(
                f"agent/webui.py 共 {webui_lines} 行，超过 {WEBUI_LINE_LIMIT} 行门"
            )
    else:
        violations.append("agent/webui.py 不存在")
    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "violations": violations,
        "warnings": warnings,
        "webui": {
            "path": "agent/webui.py",
            "lines": webui_lines,
            "limit": WEBUI_LINE_LIMIT,
            "ok": webui_lines <= WEBUI_LINE_LIMIT,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审查结构边界和文件体量")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output = args.output or (
        root / "docs" / "refactor" / "evidence" / "B7" / "structure-review.json"
    )
    payload = _review_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if payload["violations"]:
        for violation in payload["violations"]:
            print(violation)
        print(
            f"结构审查失败：{len(payload['violations'])} 项违反，"
            f"{len(payload['warnings'])} 项体量警告",
        )
        return 1
    print(
        f"结构审查通过：{len(payload['warnings'])} 项体量警告，"
        f"webui.py {payload['webui']['lines']} 行"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
