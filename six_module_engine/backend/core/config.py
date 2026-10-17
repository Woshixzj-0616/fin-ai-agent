"""Local runtime configuration shared by the app and isolated module runs."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def config_path() -> Path:
    configured = os.getenv("FINLAB_CONFIG_PATH", "").strip()
    return Path(configured).expanduser() if configured else PROJECT_ROOT / ".env"


def load_runtime_config(path: str | Path | None = None) -> Path:
    target = Path(path).expanduser() if path else config_path()
    load_dotenv(target, override=False)
    return target
