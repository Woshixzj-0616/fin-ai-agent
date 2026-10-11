"""旧 Web 启动入口，可单独直接运行作为回退路径。"""
from __future__ import annotations

import argparse
import os
from http.server import ThreadingHTTPServer

try:
    from webui_http import Handler
except ModuleNotFoundError:
    from agent.webui_http import Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="金融投研智能体 · 本地薄页面")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT") or 8765),
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"薄页面已启动：http://{args.host}:{args.port}/")
    print("Ctrl+C 停止。分析流水线与 CLI live 相同，结果落 results/。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Handler", "main"]
