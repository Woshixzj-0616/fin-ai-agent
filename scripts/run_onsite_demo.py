"""一条命令执行 P4 现场自检、离线 CLI 演示并启动 Web。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.dont_write_bytecode = True

from onsite_demo import run_launcher


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description="金融投研智能体 · 决赛现场启动器")
    app.add_argument(
        "--mode",
        choices=["both", "web", "cli", "check"],
        default="both",
        help="both=CLI 演示后启动 Web；check 只做离线自检",
    )
    app.add_argument("--host", default="127.0.0.1")
    app.add_argument("--port", type=int, default=8765)
    app.add_argument(
        "--run-root",
        type=Path,
        default=ROOT / "work" / "onsite-demo-runtime",
        help="CLI 演示运行产物根目录",
    )
    return app


def main() -> int:
    args = parser().parse_args()
    return run_launcher(
        mode=args.mode,
        host=args.host,
        port=args.port,
        root=ROOT,
        python=sys.executable,
        run_root=args.run_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
