"""Lightweight .env loader for local development (no python-dotenv dependency)."""

import os
from pathlib import Path


def load_local_env(env_path=None) -> bool:
    """Load KEY=value lines from .env; existing environment values always win."""
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parent.parent / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    for line in lines:
        key, sep, value = line.strip().partition("=")
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value)
    return True
