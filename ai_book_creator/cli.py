"""Command-line entry point for creating a book."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

# ponytail: load_local_env() runs once in ai_book_creator/__init__.py on import.
from ai_suite import providers
# Re-exported: older consumers still import the menu from here.
from ai_suite.providers import (  # noqa: F401
    CATALOGUE_PROVIDERS,
    PROVIDER_CONFIG_MAP,
    _load_last_provider,
    _prompt_provider,
    _save_last_provider,
    choose_ai,
    provider_config_path,
)

from .core.book_creator import AIBookCreator
from .env import exit_on_ctrl_c

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
PROJECT_OUTPUT_DIR = REPO_ROOT / "book_output"
PROJECT_STATE_FILE = PROJECT_OUTPUT_DIR / "project_data.json"
PROVIDER_STATE_FILE = REPO_ROOT / "book_output" / "provider_state.json"
PROJECT_ARCHIVE_DIR = PROJECT_OUTPUT_DIR / "archive" / "ebooks"
# Book writer's picks live with its books, not in the shared suite.
providers.PROVIDER_STATE_FILE = PROVIDER_STATE_FILE

# ponytail: shared by _has_previous_generated_artifacts and _clear_project_output.
PROJECT_ARTIFACT_PATTERNS = (
    "project_data.json",
    "glossary.json",
    "book_analysis.txt",
    "book_glossary.txt",
    "checkpoint_*.json",
    "chapter_*.txt",
    "models_used.json",
)


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
    *,
    ask_models: bool = False,
    resume: bool = False,
    forever: bool = False,
    pause: int = 0,
    retry_wait: int = 900,
    publish: str = "",
) -> bool:
    choose_ai(provider, "review" if ask_models else mode)
    publish = publish or ("kdp" if publish_kdp else "")

    if resume and not PROJECT_STATE_FILE.exists():
        raise ValueError(f"--resume: no saved project at {PROJECT_STATE_FILE}")
    if os.getenv("AI_BOOK_IDEA") and PROJECT_STATE_FILE.exists() and not fresh:
        raise ValueError("A saved project exists, so the idea would be ignored. Add --fresh to "
                         "archive its ebooks and start the idea, or drop the idea to resume it.")
    if resume:
        print("Resuming existing project.")
    elif fresh:
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

    creator = None
    exit_on_ctrl_c(lambda: creator and creator.project_manager.save_project(),
                   "Process interrupted by user. Progress has been saved.")
    while True:
        creator = AIBookCreator()
        completed = creator.create_book()
        if completed and publish:
            _publish(creator, publish, kdp_visible)
        if not completed and forever:
            # Budget pauses, provider outages and broken steps all leave resumable
            # state; wait and pick the same project up again.
            # ponytail: retries the same project indefinitely; add a failure cap if one
            # book can wedge the loop for good.
            print(f"\nForever mode: book not finished; resuming in {retry_wait}s.")
            time.sleep(retry_wait)
            continue
        if not (continuous or forever) or not completed:
            return completed

        # The user's idea seeds the first project only; the AI invents the rest.
        os.environ.pop("AI_BOOK_IDEA", None)
        moved = _stash_previous_ebook_files()
        if moved:
            print(f"Archived completed KDP package to: {moved[0].parent}")
        _clear_project_output()
        if pause:
            time.sleep(pause)
        print("\nContinuous mode: starting the next project.")


def _publish(creator, target: str, kdp_visible: bool = False) -> str:
    """KDP first when asked; GitHub release when KDP fails or is not wanted."""
    package = creator.project_manager.get_step_data("publishing").get("package_file", "")
    if not package:
        print("Publishing skipped: no package was prepared.")
        return ""
    if target == "kdp":
        from .utils.kdp_publisher import publish_package

        try:
            publish_package(package, headless=not kdp_visible, ai_service=creator.ai_service)
            return "kdp"
        except Exception as exc:
            print(f"KDP publishing failed ({exc}); trying GitHub.")
    from .utils import github_publisher

    try:
        print(f"Published on GitHub: {github_publisher.publish_package(package)}")
        return "github"
    except Exception as exc:
        print(f"GitHub publishing skipped: {exc}")
        return ""


def _range_arg(value: str) -> str:
    if not re.fullmatch(r"\d+(?:-\d+)?", value.strip()):
        raise argparse.ArgumentTypeError("use a number or low-high range, such as 220-300")
    low, *rest = map(int, value.split("-"))
    if low < 1 or (rest and rest[0] < low):
        raise argparse.ArgumentTypeError("range must be positive and ordered low-to-high")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and package an AI-assisted book.")
    parser.add_argument("idea", nargs="*",
                        help="book idea, anywhere on the line (quoted or not): the first project's "
                             "concept in any mode; later --forever projects are the AI's")
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
    parser.add_argument("--publish", action="store_true",
                        help="publish each finished book on KDP, falling back to a GitHub release")
    parser.add_argument("--publish-github", action="store_true",
                        help="publish each finished book as a GitHub release, skipping KDP")
    parser.add_argument("--fresh", action="store_true", help="archive old ebook assets and start a new project")
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="keep creating and packaging new books until interrupted or the provider stops",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="ask only for provider and models; the AI invents the idea and makes every choice",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help="--auto, then start a new project after each finished book or series; "
             "unfinished work is resumed after --retry-wait",
    )
    parser.add_argument("--resume", action="store_true", help="resume the saved project without asking")
    parser.add_argument("--pause", type=int, default=0, help="seconds between projects in --forever")
    parser.add_argument("--retry-wait", type=int, default=900,
                        help="seconds before --forever resumes an unfinished book")
    # Intermixed: the idea's words may sit before, between or after the options.
    args = parser.parse_intermixed_args()
    args.idea = " ".join(args.idea).strip()
    args.publish = "github" if args.publish_github else "kdp" if args.publish else ""
    if args.forever:
        args.auto = True
    if args.auto:
        args.mode = "auto"
    if args.resume and args.fresh:
        parser.error("--resume and --fresh contradict each other")
    return args


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
        if args.idea:
            os.environ["AI_BOOK_IDEA"] = args.idea
        author = args.author or os.getenv("AI_BOOK_AUTHOR") or _existing_author()
        if not author and args.mode == "review":
            author = input("Author name [AI Book Creator]: ").strip()
        os.environ["AI_BOOK_AUTHOR"] = author or "AI Book Creator"
        os.environ["AI_MODELS_USED_PATH"] = str(PROJECT_OUTPUT_DIR / "models_used.json")
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
        provider = args.provider or (default_provider if args.mode == "auto" and not args.auto
                                     else _prompt_provider(default_provider))
        run(
            provider,
            args.mode,
            args.fresh,
            args.continuous,
            args.publish_kdp,
            args.kdp_visible,
            ask_models=args.auto,
            resume=args.resume,
            forever=args.forever,
            pause=args.pause,
            retry_wait=args.retry_wait,
            publish=args.publish or "",
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
