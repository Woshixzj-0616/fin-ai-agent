#!/usr/bin/env bash
# FinScope V5.2.0：macOS / Linux 本地启动脚本
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8769}"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "没有找到 $PYTHON。请先安装 Python 3.11 或更高版本。" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --disable-pip-version-check --quiet -r requirements.txt

exec python -B agent/webui.py --host 127.0.0.1 --port "$PORT"
