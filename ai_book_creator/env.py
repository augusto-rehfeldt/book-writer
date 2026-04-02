"""Lightweight .env loader for local development."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def _parse_env_lines(lines: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        values[key] = value
    return values


def load_local_env(env_path: str | os.PathLike[str] | None = None) -> bool:
    """Load variables from a local .env file if one exists.

    Existing process environment values always win. This is intentionally
    lightweight so the project does not need python-dotenv as a dependency.
    """
    path = Path(env_path) if env_path is not None else Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return False

    try:
        parsed = _parse_env_lines(path.read_text(encoding="utf-8").splitlines())
    except Exception:
        return False

    for key, value in parsed.items():
        os.environ.setdefault(key, value)
    return True
