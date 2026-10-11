"""B7 发布冒烟：启动真实回退服务并检查新旧入口。"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str) -> tuple[int, dict[str, str], bytes]:
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.status, dict(response.headers), response.read()


def _post_json(url: str, payload: dict) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, dict(response.headers), response.read()


def _wait_for_server(base_url: str, process: subprocess.Popen, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"回退服务提前退出，退出码 {process.returncode}")
        try:
            return _request(f"{base_url}/app/")
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"回退服务未在 {timeout:.1f} 秒内启动：{last_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动回退服务并检查发布入口")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "refactor" / "evidence" / "B7" / "release-smoke.json",
    )
    args = parser.parse_args(argv)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "-B",
        str(ROOT / "agent" / "webui_app.py"),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    checks: dict[str, dict] = {}
    try:
        status, headers, body = _wait_for_server(base_url, process)
        checks["app"] = {
            "path": "/app/",
            "status": status,
            "ok": status == 200 and b'data-app="fin-workbench"' in body,
            "content_type": headers.get("Content-Type", ""),
        }
        status, headers, body = _request(f"{base_url}/")
        checks["legacy_page"] = {
            "path": "/",
            "status": status,
            "ok": (
                status == 200
                and "金融投研智能体" in body.decode("utf-8", errors="replace")
                and "/api/analyze" in body.decode("utf-8", errors="replace")
            ),
            "content_type": headers.get("Content-Type", ""),
        }
        status, headers, body = _request(f"{base_url}/api/runs")
        payload = json.loads(body.decode("utf-8"))
        checks["runs_api"] = {
            "path": "/api/runs",
            "status": status,
            "ok": status == 200 and isinstance(payload.get("runs"), list),
            "content_type": headers.get("Content-Type", ""),
        }
        status, headers, body = _post_json(
            f"{base_url}/api/search",
            {"query": "营业收入", "top_k": 3},
        )
        payload = json.loads(body.decode("utf-8"))
        checks["legacy_search_api"] = {
            "path": "/api/search",
            "status": status,
            "ok": status == 200 and payload.get("status") == "empty",
            "content_type": headers.get("Content-Type", ""),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stdout, stderr = process.communicate()
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": command,
        "checks": checks,
        "server_stdout": stdout,
        "server_stderr": stderr,
        "ok": bool(checks) and all(item["ok"] for item in checks.values()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not report["ok"]:
        print(f"发布冒烟失败，详见 {args.output}")
        return 1
    print(f"发布冒烟通过：{', '.join(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
