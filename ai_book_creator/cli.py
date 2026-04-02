"""Command-line entry point for creating a book."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .env import load_local_env

load_local_env()

from .core.book_creator import AIBookCreator


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

PROVIDER_CONFIG_MAP = {
    "google": str(PACKAGE_ROOT / "config" / "ai_config_google.json"),
    "openai": str(PACKAGE_ROOT / "config" / "ai_config_openai.json"),
    "groq": str(PACKAGE_ROOT / "config" / "ai_config_groq.json"),
}
PROVIDER_STATE_FILE = REPO_ROOT / "book_output" / "provider_state.json"


def _load_last_provider(default_provider: str = "google") -> str:
    try:
        if PROVIDER_STATE_FILE.exists():
            with PROVIDER_STATE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            provider = str(data.get("provider", "")).lower()
            if provider in PROVIDER_CONFIG_MAP:
                return provider
    except Exception:
        pass
    return default_provider


def _save_last_provider(provider: str) -> None:
    PROVIDER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROVIDER_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump({"provider": provider.lower()}, f, indent=2)


def _prompt_provider(default_provider: str) -> str:
    valid_providers = list(PROVIDER_CONFIG_MAP.keys())
    prompt = (
        f"Choose provider [default: {default_provider}] "
        f"({', '.join(valid_providers)}): "
    )

    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return default_provider
        if not choice:
            return default_provider
        if choice in PROVIDER_CONFIG_MAP:
            return choice
        print(f"Invalid provider. Please choose one of: {', '.join(valid_providers)}")


def run(provider: str) -> None:
    config_path = PROVIDER_CONFIG_MAP[provider]
    os.environ["AI_CONFIG_PATH"] = config_path
    _save_last_provider(provider)

    creator = AIBookCreator()
    creator.create_book()


def main() -> None:
    """Run the AI Book Creator interactively."""
    print("AI Book Creator v2.0 - Modular Edition with Glossary Support")
    print("=" * 60)

    try:
        default_provider = _load_last_provider()
        provider = _prompt_provider(default_provider)
        run(provider)
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user. Progress has been saved.")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback

        traceback.print_exc()
        print("\nProgress has been saved. You can try to resume later.")


if __name__ == "__main__":
    main()
