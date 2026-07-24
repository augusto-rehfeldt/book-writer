"""Command-line entry point for creating a book."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

# ponytail: load_local_env() runs once in ai_book_creator/__init__.py on import.
from .core.book_creator import AIBookCreator
from .services.ai_service import ensure_openai_oauth_proxy, load_opencode_go_sync


PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

PROVIDER_CONFIG_MAP = {
    "google": str(PACKAGE_ROOT / "config" / "ai_config_google.local.json"),
    "openai": str(PACKAGE_ROOT / "config" / "ai_config_openai.local.json"),
    "openai-oauth": str(PACKAGE_ROOT / "config" / "ai_config_openai_oauth.json"),
    "groq": str(PACKAGE_ROOT / "config" / "ai_config_groq.local.json"),
    "minimax": str(PACKAGE_ROOT / "config" / "ai_config_minimax.local.json"),
    "openrouter": str(PACKAGE_ROOT / "config" / "ai_config_openrouter.json"),
    "opencode-go": str(PACKAGE_ROOT / "config" / "ai_config_opencode_go.json"),
}
PROJECT_OUTPUT_DIR = REPO_ROOT / "book_output"
PROJECT_STATE_FILE = PROJECT_OUTPUT_DIR / "project_data.json"
PROVIDER_STATE_FILE = REPO_ROOT / "book_output" / "provider_state.json"
PROJECT_ARCHIVE_DIR = PROJECT_OUTPUT_DIR / "archive" / "ebooks"
OPENAI_MODEL_OPTIONS = ("gpt-5.4", "gpt-5.4-mini")


def _load_opencode_go_models() -> dict:
    # Local config first (carries friendly display names like "GLM-5.2").
    out: dict = {}
    try:
        with open(PROVIDER_CONFIG_MAP["opencode-go"], "r", encoding="utf-8") as f:
            data = json.load(f)
        for mid, info in (data.get("models") or {}).items():
            out[str(mid).lower()] = [str(info.get("name", mid)), int(info.get("max_output", 4096))]
    except Exception:
        pass
    # Then overlay opencode's userspace truth so context/output stays fresh.
    # ponytail: opencode auth.json also drives the key; this keeps the two in lockstep.
    for mid, info in load_opencode_go_sync().get("models", {}).items():
        if mid in out:
            out[mid][1] = int(info["max_output"])
        else:
            out[mid] = [info.get("name", mid), int(info["max_output"])]
    if not out:
        out = {"glm-5.2": ["GLM-5.2", 131072]}
    return out


OPENCODE_GO_MODELS = _load_opencode_go_models()

# ponytail: shared by _has_previous_generated_artifacts and _clear_project_output.
PROJECT_ARTIFACT_PATTERNS = (
    "project_data.json",
    "glossary.json",
    "book_analysis.txt",
    "book_glossary.txt",
    "checkpoint_*.json",
    "chapter_*.txt",
)


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


def _load_last_openai_model(
    default_model: str | None = None,
    options: tuple[str, ...] = OPENAI_MODEL_OPTIONS,
    state_key: str = "openai_model",
) -> str:
    data = _load_provider_state()
    model = str(data.get(state_key, "")).lower()
    if model in options:
        return model
    fallback = default_model or _default_openai_model()
    return fallback if fallback in options else options[0]


def _save_last_provider(provider: str, openai_model: str | None = None, opencode_go_model: str | None = None) -> None:
    data = _load_provider_state()
    data["provider"] = provider.lower()
    if openai_model is not None:
        data["openai_oauth_model" if provider.lower() == "openai-oauth" else "openai_model"] = openai_model.lower()
    elif provider.lower() == "openai" and str(data.get("openai_model", "")).lower() not in OPENAI_MODEL_OPTIONS:
        data["openai_model"] = _default_openai_model()
    if opencode_go_model is not None:
        data["opencode_go_model"] = opencode_go_model.lower()
    elif provider.lower() == "opencode-go" and str(data.get("opencode_go_model", "")).lower() not in OPENCODE_GO_MODELS:
        data["opencode_go_model"] = next(iter(OPENCODE_GO_MODELS))

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


def _load_openai_oauth_models() -> dict[str, tuple[int | None, int | None]]:
    ensure_openai_oauth_proxy()
    with urlopen("http://127.0.0.1:10531/v1/models", timeout=10) as response:
        payload = json.load(response)
    models: dict[str, tuple[int | None, int | None]] = {}
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        model_id = str(item.get("id", "")).strip() if isinstance(item, dict) else ""
        if not model_id or "image" in model_id.lower():
            continue
        context = item.get("context_window")
        output = item.get("max_output_tokens")
        models[model_id] = (
            int(context) if isinstance(context, int) and context > 0 else None,
            int(output) if isinstance(output, int) and output > 0 else None,
        )
    if not models:
        raise RuntimeError("OpenAI OAuth returned no text models from /v1/models.")
    return models


def _prompt_openai_model(
    default_model: str,
    model_info: dict[str, tuple[int | None, int | None]] | None = None,
) -> str:
    options = tuple(model_info) if model_info is not None else OPENAI_MODEL_OPTIONS
    print("Available OpenAI OAuth text models (live):" if model_info is not None else "Available OpenAI models:")
    for index, model in enumerate(options, 1):
        context, output = model_info.get(model, (None, None)) if model_info is not None else (None, None)
        limits = []
        if context:
            limits.append(f"context {context:,}")
        if output:
            limits.append(f"max output {output:,}")
        detail = f" — {', '.join(limits)}" if limits else ""
        marker = " (default)" if model == default_model else ""
        print(f"  {index}. {model}{detail}{marker}")
    if model_info is not None and not any(context or output for context, output in model_info.values()):
        print("  Context/output limits: not reported by the OAuth /v1/models endpoint.")
    prompt = f"Choose model number or id [default: {default_model}]: "

    while True:
        try:
            choice = input(prompt).strip()
        except EOFError:
            return default_model

        if not choice:
            return default_model

        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        model = choice.lower()
        if model in options:
            return model
        if model_info is None:
            model = _normalize_openai_model(choice)
            if model in options:
                return model
        print(f"Please choose a number from 1 to {len(options)} or a listed model id.")


def _default_opencode_go_model() -> str:
    try:
        with open(PROVIDER_CONFIG_MAP["opencode-go"], "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            candidate = str(data.get("writing_model") or "").lower()
            if candidate in OPENCODE_GO_MODELS:
                return candidate
    except Exception:
        pass
    return next(iter(OPENCODE_GO_MODELS))


def _load_last_opencode_go_model(default_model: str | None = None) -> str:
    data = _load_provider_state()
    model = str(data.get("opencode_go_model", "")).lower()
    if model in OPENCODE_GO_MODELS:
        return model
    return default_model or _default_opencode_go_model()


def _prompt_opencode_go_model(default_model: str) -> str:
    model_ids = list(OPENCODE_GO_MODELS.keys())
    print("Available OpenCode Go models:")
    for i, mid in enumerate(model_ids, 1):
        name, max_out = OPENCODE_GO_MODELS[mid]
        marker = " (default)" if mid == default_model else ""
        print(f"  {i:2d}. {mid} — {name} (max output {max_out:,}){marker}")
    prompt = f"Choose model number or id [default: {default_model}]: "

    while True:
        try:
            choice = input(prompt).strip().lower()
        except EOFError:
            return default_model

        if not choice:
            return default_model
        if choice in OPENCODE_GO_MODELS:
            return choice
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(model_ids):
                return model_ids[idx - 1]
        print(f"Invalid choice. Enter a number 1-{len(model_ids)} or a model id.")


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

    for pattern in PROJECT_ARTIFACT_PATTERNS:
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

    for pattern in PROJECT_ARTIFACT_PATTERNS:
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
    opencode_go_model = None
    if provider in ("openai", "openai-oauth"):
        model_info = _load_openai_oauth_models() if provider == "openai-oauth" else None
        options = tuple(model_info) if model_info is not None else OPENAI_MODEL_OPTIONS
        default_model = _load_last_openai_model(
            "gpt-5.6-terra" if provider == "openai-oauth" else None,
            options,
            "openai_oauth_model" if provider == "openai-oauth" else "openai_model",
        )
        openai_model = _prompt_openai_model(default_model, model_info)
        os.environ["AI_WRITING_MODEL"] = openai_model
        os.environ["AI_REVIEW_MODEL"] = openai_model
        os.environ["AI_OPENAI_MODEL"] = openai_model
    elif provider == "opencode-go":
        default_model = _load_last_opencode_go_model()
        opencode_go_model = _prompt_opencode_go_model(default_model)
        _, max_out = OPENCODE_GO_MODELS[opencode_go_model]
        os.environ["AI_WRITING_MODEL"] = opencode_go_model
        os.environ["AI_REVIEW_MODEL"] = opencode_go_model
        for key in (
            "AI_WRITING_COMPLETION_TOKENS",
            "AI_REVIEW_COMPLETION_TOKENS",
            "AI_PLANNING_COMPLETION_TOKENS",
            "AI_DEFAULT_COMPLETION_TOKENS",
        ):
            os.environ[key] = str(max_out)

    _save_last_provider(provider, openai_model, opencode_go_model)

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
