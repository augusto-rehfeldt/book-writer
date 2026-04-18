"""Command-line entry point for creating a book."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .env import load_local_env

load_local_env()

from .core.book_creator import AIBookCreator


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

PROVIDER_CONFIG_MAP = {
    "google": str(PACKAGE_ROOT / "config" / "ai_config_google.local.json"),
    "openai": str(PACKAGE_ROOT / "config" / "ai_config_openai.local.json"),
    "groq": str(PACKAGE_ROOT / "config" / "ai_config_groq.local.json"),
}
PROJECT_OUTPUT_DIR = REPO_ROOT / "book_output"
PROJECT_STATE_FILE = PROJECT_OUTPUT_DIR / "project_data.json"
PROVIDER_STATE_FILE = REPO_ROOT / "book_output" / "provider_state.json"
PROJECT_ARCHIVE_DIR = PROJECT_OUTPUT_DIR / "archive" / "ebooks"
OPENAI_MODEL_OPTIONS = ("gpt-5.4", "gpt-5.4-mini")


def _load_provider_state() -> dict:
    try:
        if PROVIDER_STATE_FILE.exists():
            with PROVIDER_STATE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _load_last_provider(default_provider: str = "google") -> str:
    data = _load_provider_state()
    provider = str(data.get("provider", "")).lower()
    if provider in PROVIDER_CONFIG_MAP:
        return provider
    return default_provider


def _default_openai_model() -> str:
    try:
        base_path = Path(PROVIDER_CONFIG_MAP["openai"])
        local_path = base_path.with_name(base_path.stem + ".local.json")
        path_to_load = local_path if local_path.exists() else base_path
        
        with open(path_to_load, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            candidate = str(data.get("writing_model") or data.get("review_model") or "").lower()
            if candidate in OPENAI_MODEL_OPTIONS:
                return candidate
    except Exception:
        pass
    return "gpt-5.4-mini"


def _load_last_openai_model(default_model: str | None = None) -> str:
    data = _load_provider_state()
    model = str(data.get("openai_model", "")).lower()
    if model in OPENAI_MODEL_OPTIONS:
        return model
    return default_model or _default_openai_model()


def _save_last_provider(provider: str, openai_model: str | None = None) -> None:
    data = _load_provider_state()
    data["provider"] = provider.lower()
    if openai_model is not None:
        data["openai_model"] = openai_model.lower()
    elif provider.lower() == "openai" and str(data.get("openai_model", "")).lower() not in OPENAI_MODEL_OPTIONS:
        data["openai_model"] = _default_openai_model()

    PROVIDER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROVIDER_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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


def _normalize_openai_model(choice: str) -> str:
    normalized = choice.strip().lower()
    aliases = {
        "1": "gpt-5.4",
        "gpt-5.4": "gpt-5.4",
        "5.4": "gpt-5.4",
        "2": "gpt-5.4-mini",
        "gpt-5.4-mini": "gpt-5.4-mini",
        "mini": "gpt-5.4-mini",
    }
    return aliases.get(normalized, "")


def _prompt_openai_model(default_model: str) -> str:
    prompt = (
        f"Choose OpenAI model [default: {default_model}] "
        "(1) gpt-5.4, (2) gpt-5.4-mini: "
    )

    while True:
        try:
            choice = input(prompt).strip()
        except EOFError:
            return default_model

        if not choice:
            return default_model

        model = _normalize_openai_model(choice)
        if model in OPENAI_MODEL_OPTIONS:
            return model
        print("Please choose 1 for gpt-5.4 or 2 for gpt-5.4-mini.")


def _prompt_resume_existing_project() -> bool:
    """Ask whether to resume the current project or start over."""
    prompt = "Existing project found. Resume it? [Y/n]: "
    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return True

        if choice in ("", "y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        print("Please answer yes or no.")


def _prompt_stash_previous_ebooks() -> bool:
    prompt = "Stash existing EPUBs before starting fresh? [Y/n]: "
    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return True

        if choice in ("", "y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        print("Please answer yes or no.")


def _collect_previous_ebooks() -> list[Path]:
    if not PROJECT_OUTPUT_DIR.exists():
        return []

    ebooks: list[Path] = []
    for path in PROJECT_OUTPUT_DIR.rglob("*.epub"):
        try:
            relative = path.relative_to(PROJECT_OUTPUT_DIR)
        except ValueError:
            continue
        if "archive" in relative.parts:
            continue
        ebooks.append(path)
    return ebooks


def _has_previous_generated_artifacts() -> bool:
    if _collect_previous_ebooks():
        return True

    patterns = [
        "project_data.json",
        "glossary.json",
        "book_analysis.txt",
        "book_glossary.txt",
        "checkpoint_*.json",
        "chapter_*.txt",
    ]
    for pattern in patterns:
        if any(PROJECT_OUTPUT_DIR.glob(pattern)):
            return True
    return False


def _unique_target_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _stash_previous_ebooks() -> list[Path]:
    ebooks = _collect_previous_ebooks()
    if not ebooks:
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stash_dir = PROJECT_ARCHIVE_DIR / timestamp
    stash_dir.mkdir(parents=True, exist_ok=True)

    moved: list[Path] = []
    for ebook_path in ebooks:
        target = _unique_target_path(stash_dir, ebook_path.name)
        shutil.move(str(ebook_path), str(target))
        moved.append(target)
    return moved


def _clear_project_output() -> None:
    """Remove generated project artifacts so a new run starts cleanly."""
    removed_files: list[str] = []
    patterns = [
        "project_data.json",
        "glossary.json",
        "book_analysis.txt",
        "book_glossary.txt",
        "checkpoint_*.json",
        "chapter_*.txt",
    ]

    for pattern in patterns:
        for path in PROJECT_OUTPUT_DIR.glob(pattern):
            try:
                path.unlink()
                removed_files.append(path.name)
            except FileNotFoundError:
                continue

    if removed_files:
        print("Starting a fresh project. Removed previous generated files:")
        for name in sorted(set(removed_files)):
            print(f"  - {name}")
    else:
        print("Starting a fresh project.")


def _prepare_fresh_start() -> None:
    """Offer to archive old EPUBs, then clear cached project artifacts."""
    previous_ebooks = _collect_previous_ebooks()
    if previous_ebooks:
        if _prompt_stash_previous_ebooks():
            moved = _stash_previous_ebooks()
            if moved:
                print("Archived previous EPUBs:")
                for path in moved:
                    print(f"  - {path}")
        else:
            print("Leaving existing EPUBs in place.")

    _clear_project_output()


def run(provider: str) -> None:
    base_config_path = Path(PROVIDER_CONFIG_MAP[provider])
    local_config_path = base_config_path.with_name(base_config_path.stem + ".local.json")
    
    if local_config_path.exists():
        config_path = str(local_config_path)
        print(f"Loaded local configuration: {local_config_path.name}")
    else:
        config_path = str(base_config_path)

    os.environ["AI_CONFIG_PATH"] = config_path

    openai_model = None
    if provider == "openai":
        default_model = _load_last_openai_model()
        openai_model = _prompt_openai_model(default_model)
        os.environ["AI_WRITING_MODEL"] = openai_model
        os.environ["AI_REVIEW_MODEL"] = openai_model
        os.environ["AI_OPENAI_MODEL"] = openai_model

    _save_last_provider(provider, openai_model)

    if PROJECT_STATE_FILE.exists():
        if _prompt_resume_existing_project():
            print("Resuming existing project.")
        else:
            _prepare_fresh_start()
    elif _has_previous_generated_artifacts():
        _prepare_fresh_start()

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