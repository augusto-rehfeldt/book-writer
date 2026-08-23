"""Command-line entry point for creating a book."""

from __future__ import annotations

import argparse
import json
import os
import re
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
    "opencode-zen": str(PACKAGE_ROOT / "config" / "ai_config_opencode_zen.json"),
    "claude": str(PACKAGE_ROOT / "config" / "ai_config_claude.json"),
    "hyper": str(PACKAGE_ROOT / "config" / "ai_config_hyper.json"),
}
# Providers whose model list lives in their config file and is picked at runtime.
CATALOGUE_PROVIDERS = ("opencode-go", "opencode-zen", "claude", "hyper")
PROJECT_OUTPUT_DIR = REPO_ROOT / "book_output"
PROJECT_STATE_FILE = PROJECT_OUTPUT_DIR / "project_data.json"
PROVIDER_STATE_FILE = REPO_ROOT / "book_output" / "provider_state.json"
PROJECT_ARCHIVE_DIR = PROJECT_OUTPUT_DIR / "archive" / "ebooks"
OPENAI_MODEL_OPTIONS = ("gpt-5.4", "gpt-5.4-mini")


def _load_catalogue(provider: str) -> dict:
    # Local config first (carries friendly display names like "GLM-5.2").
    out: dict = {}
    try:
        with open(PROVIDER_CONFIG_MAP[provider], "r", encoding="utf-8") as f:
            data = json.load(f)
        for mid, info in (data.get("models") or {}).items():
            out[str(mid).lower()] = [str(info.get("name", mid)), int(info.get("max_output", 4096))]
    except Exception:
        pass
    # Then overlay opencode's userspace truth so context/output stays fresh
    # for the paid opencode-go tier; the zen config already lists its free tier.
    # ponytail: opencode auth.json also drives the key; this keeps the two in lockstep.
    if provider == "opencode-go":
        synced = load_opencode_go_sync().get("models", {})
        for mid, info in synced.items():
            if mid in out:
                out[mid][1] = int(info["max_output"])
            else:
                out[mid] = [info.get("name", mid), int(info["max_output"])]
        if synced:
            out = {mid: entry for mid, entry in out.items() if mid in synced}
    if not out:
        fallback = (
            {"deepseek-v4-flash-free": ["DeepSeek V4 Flash Free", 128000]}
            if provider == "opencode-zen"
            else {"glm-5.2": ["GLM-5.2", 131072]}
        )
        out = fallback
    return out


_CATALOGUE_CACHE: dict[str, dict] = {}


def _provider_models(provider: str) -> dict:
    """{model id: [display name, max output tokens]} for a catalogue provider."""
    if provider not in _CATALOGUE_CACHE:
        _CATALOGUE_CACHE[provider] = _load_catalogue(provider)
    return _CATALOGUE_CACHE[provider]


def _model_state_key(provider: str) -> str:
    return f"{provider.replace('-', '_')}_model"

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


def _save_last_provider(provider: str, openai_model: str | None = None,
                        catalogue_model: str | None = None) -> None:
    data = _load_provider_state()
    provider = provider.lower()
    data["provider"] = provider
    if openai_model is not None:
        data["openai_oauth_model" if provider == "openai-oauth" else "openai_model"] = openai_model.lower()
    elif provider == "openai" and str(data.get("openai_model", "")).lower() not in OPENAI_MODEL_OPTIONS:
        data["openai_model"] = _default_openai_model()
    if provider in CATALOGUE_PROVIDERS:
        key = _model_state_key(provider)
        models = _provider_models(provider)
        if catalogue_model is not None:
            data[key] = catalogue_model.lower()
        elif str(data.get(key, "")).lower() not in models:
            data[key] = next(iter(models))

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


def _default_catalogue_model(provider: str) -> str:
    models = _provider_models(provider)
    try:
        with open(PROVIDER_CONFIG_MAP[provider], "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            candidate = str(data.get("writing_model") or "").lower()
            if candidate in models:
                return candidate
    except Exception:
        pass
    return next(iter(models))


def _load_last_catalogue_model(provider: str, default_model: str | None = None) -> str:
    models = _provider_models(provider)
    data = _load_provider_state()
    model = str(data.get(_model_state_key(provider), "")).lower()
    if model in models:
        return model
    return default_model or _default_catalogue_model(provider)


PROVIDER_LABELS = {
    "opencode-go": "OpenCode Go",
    "opencode-zen": "OpenCode Zen free",
    "claude": "Claude Code (your subscription, no API key)",
    "hyper": "hyper.charm.land",
}


def _prompt_catalogue_model(provider: str, default_model: str) -> str:
    models = _provider_models(provider)
    label = PROVIDER_LABELS.get(provider, provider)
    model_ids = list(models.keys())
    print(f"Available {label} models:")
    for i, mid in enumerate(model_ids, 1):
        name, max_out = models[mid]
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
        if choice in models:
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
    prompt = "Stash existing EPUBs and cover prompts before starting fresh? [Y/n]: "
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


def _collect_previous_ebook_files() -> list[Path]:
    if not PROJECT_OUTPUT_DIR.exists():
        return []

    files: list[Path] = []
    for pattern in ("*.epub", "*_cover_prompt.txt", "*_cover.jpg", "*_kdp.json", "*_KDP_CHECKLIST.txt"):
        for path in PROJECT_OUTPUT_DIR.rglob(pattern):
            try:
                relative = path.relative_to(PROJECT_OUTPUT_DIR)
            except ValueError:
                continue
            if "archive" not in relative.parts:
                files.append(path)
    return files


def _has_previous_generated_artifacts() -> bool:
    if _collect_previous_ebook_files():
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


def _stash_previous_ebook_files() -> list[Path]:
    files = _collect_previous_ebook_files()
    if not files:
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stash_dir = PROJECT_ARCHIVE_DIR / timestamp
    stash_dir.mkdir(parents=True, exist_ok=True)

    moved: list[Path] = []
    for path in files:
        target = _unique_target_path(stash_dir, path.name)
        shutil.move(str(path), str(target))
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
    """Offer to archive old ebook files, then clear cached project artifacts."""
    previous_ebook_files = _collect_previous_ebook_files()
    if previous_ebook_files:
        if _prompt_stash_previous_ebooks():
            moved = _stash_previous_ebook_files()
            if moved:
                print("Archived previous ebook files:")
                for path in moved:
                    print(f"  - {path}")
        else:
            print("Leaving existing EPUBs in place.")

    _clear_project_output()


def run(
    provider: str,
    mode: str = "review",
    fresh: bool = False,
    continuous: bool = False,
    publish_kdp: bool = False,
    kdp_visible: bool = False,
) -> bool:
    base_config_path = Path(PROVIDER_CONFIG_MAP[provider])
    local_config_path = base_config_path.with_name(base_config_path.stem + ".local.json")
    
    if local_config_path.exists():
        config_path = str(local_config_path)
        print(f"Loaded local configuration: {local_config_path.name}")
    else:
        config_path = str(base_config_path)

    os.environ["AI_CONFIG_PATH"] = config_path

    openai_model = None
    catalogue_model = None
    if provider in ("openai", "openai-oauth"):
        model_info = _load_openai_oauth_models() if provider == "openai-oauth" else None
        options = tuple(model_info) if model_info is not None else OPENAI_MODEL_OPTIONS
        default_model = _load_last_openai_model(
            "gpt-5.6-terra" if provider == "openai-oauth" else None,
            options,
            "openai_oauth_model" if provider == "openai-oauth" else "openai_model",
        )
        openai_model = default_model if mode == "auto" else _prompt_openai_model(default_model, model_info)
        os.environ["AI_WRITING_MODEL"] = openai_model
        os.environ["AI_REVIEW_MODEL"] = openai_model
        os.environ["AI_OPENAI_MODEL"] = openai_model
    elif provider in CATALOGUE_PROVIDERS:
        default_model = _load_last_catalogue_model(provider)
        catalogue_model = default_model if mode == "auto" else _prompt_catalogue_model(provider, default_model)
        _, max_out = _provider_models(provider)[catalogue_model]
        os.environ["AI_WRITING_MODEL"] = catalogue_model
        os.environ["AI_REVIEW_MODEL"] = catalogue_model
        # The Claude Code CLI has no completion-token argument, so its catalogue
        # carries max_output 0 and the caps stay unset.
        for key in (
            "AI_WRITING_COMPLETION_TOKENS",
            "AI_REVIEW_COMPLETION_TOKENS",
            "AI_PLANNING_COMPLETION_TOKENS",
            "AI_DEFAULT_COMPLETION_TOKENS",
        ):
            if max_out:
                os.environ[key] = str(max_out)
            else:
                os.environ.pop(key, None)

    _save_last_provider(provider, openai_model, catalogue_model)

    if fresh:
        if mode == "auto":
            _stash_previous_ebook_files()
            _clear_project_output()
        else:
            _prepare_fresh_start()
    elif PROJECT_STATE_FILE.exists():
        if mode == "auto":
            print("Resuming existing project.")
        elif _prompt_resume_existing_project():
            print("Resuming existing project.")
        else:
            _prepare_fresh_start()
    elif _has_previous_generated_artifacts():
        if mode == "auto":
            _stash_previous_ebook_files()
            _clear_project_output()
        else:
            _prepare_fresh_start()

    while True:
        creator = AIBookCreator()
        completed = creator.create_book()
        if completed and publish_kdp:
            from .utils.kdp_publisher import publish_package

            publishing = creator.project_manager.get_step_data("publishing")
            try:
                publish_package(
                    publishing["package_file"],
                    headless=not kdp_visible,
                    ai_service=creator.ai_service,
                )
            except Exception as exc:
                print(f"KDP publishing deferred; saved draft will remain ready to resume: {exc}")
        if not continuous or not completed:
            return completed

        moved = _stash_previous_ebook_files()
        if moved:
            print(f"Archived completed KDP package to: {moved[0].parent}")
        _clear_project_output()
        print("\nContinuous mode: starting the next book.")


def _range_arg(value: str) -> str:
    if not re.fullmatch(r"\d+(?:-\d+)?", value.strip()):
        raise argparse.ArgumentTypeError("use a number or low-high range, such as 220-300")
    low, *rest = map(int, value.split("-"))
    if low < 1 or (rest and rest[0] < low):
        raise argparse.ArgumentTypeError("range must be positive and ordered low-to-high")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and package an AI-assisted book.")
    parser.add_argument("--mode", choices=("review", "auto"), default=os.getenv("AI_BOOK_MODE", "review"))
    parser.add_argument("--provider", choices=tuple(PROVIDER_CONFIG_MAP))
    parser.add_argument("--author")
    parser.add_argument("--pages", type=_range_arg, metavar="MIN-MAX")
    parser.add_argument("--chapters", type=_range_arg, metavar="MIN-MAX")
    parser.add_argument("--series", type=int, metavar="BOOKS")
    parser.add_argument("--cover-source", choices=("manual", "perchance", "pollinations"))
    parser.add_argument("--cover-background", metavar="IMAGE")
    parser.add_argument(
        "--publish-kdp",
        action="store_true",
        help="submit the completed eBook to KDP using the saved Chrome profile",
    )
    parser.add_argument(
        "--kdp-visible",
        action="store_true",
        help="show Chrome during KDP publishing (headless is the default)",
    )
    parser.add_argument("--fresh", action="store_true", help="archive old ebook assets and start a new project")
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="keep creating and packaging new books until interrupted or the provider stops",
    )
    return parser.parse_args()


def _existing_author() -> str:
    try:
        data = json.loads(PROJECT_STATE_FILE.read_text(encoding="utf-8"))
        return str(data.get("init", {}).get("author_name", "")).strip()
    except Exception:
        return ""


def main() -> None:
    """Run the AI Book Creator interactively."""
    print("AI Book Creator v2.0 - Modular Edition with Glossary Support")
    print("=" * 60)

    try:
        args = _parse_args()
        if args.continuous and args.mode != "auto":
            raise ValueError("--continuous requires --mode auto")
        if args.kdp_visible and not args.publish_kdp:
            raise ValueError("--kdp-visible requires --publish-kdp")
        os.environ["AI_BOOK_MODE"] = args.mode
        author = args.author or os.getenv("AI_BOOK_AUTHOR") or _existing_author()
        if not author and args.mode == "review":
            author = input("Author name [AI Book Creator]: ").strip()
        os.environ["AI_BOOK_AUTHOR"] = author or "AI Book Creator"
        if args.pages:
            os.environ["AI_BOOK_PAGE_RANGE"] = args.pages
        if args.chapters:
            os.environ["AI_BOOK_CHAPTER_RANGE"] = args.chapters
        if args.series is not None:
            if args.series < 1:
                raise ValueError("--series must be at least 1")
            os.environ["AI_BOOK_SERIES_COUNT"] = str(args.series)
        if args.cover_background:
            os.environ["AI_BOOK_COVER_BACKGROUND"] = args.cover_background
        os.environ["AI_BOOK_COVER_SOURCE"] = args.cover_source or "pollinations"

        default_provider = _load_last_provider()
        provider = args.provider or (default_provider if args.mode == "auto" else _prompt_provider(default_provider))
        run(
            provider,
            args.mode,
            args.fresh,
            args.continuous,
            args.publish_kdp,
            args.kdp_visible,
        )
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user. Progress has been saved.")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback

        traceback.print_exc()
        print("\nProgress has been saved. You can try to resume later.")


if __name__ == "__main__":
    main()
