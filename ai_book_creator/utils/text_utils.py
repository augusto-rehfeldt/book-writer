"""
Utility functions for text processing, such as word and page counting.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

WORDS_PER_PAGE = 250


def calculate_word_count(text: str) -> int:
    """Calculates the number of words in a given text."""
    return len(text.split())


def calculate_page_count(word_count: int, words_per_page: int = WORDS_PER_PAGE) -> int:
    """Calculates the estimated number of pages based on word count (rounded up)."""
    return (word_count + words_per_page - 1) // words_per_page


def pages_to_words(pages: int) -> int:
    return pages * WORDS_PER_PAGE


def text_chunks(text: str, max_chars: int = 6500):
    """Bounded, lossless passages; prefer paragraph boundaries to mid-scene cuts."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    while text:
        end = min(len(text), max_chars)
        if end < len(text):
            boundary = text.rfind("\n\n", max_chars // 2, end)
            if boundary >= 0:
                end = boundary + 2
        yield text[:end]
        text = text[end:]


def parse_json(raw: str):
    for candidate in (raw.strip(),
                      *(m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.S)),
                      raw[raw.find("{"):raw.rfind("}") + 1] if "{" in raw else ""):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return None


def ask_json(ai_service, prompt: str, validate, error: str, attempts: int = 3, **kwargs) -> dict:
    """Ask for a JSON object; re-ask with the defect named, then raise `error`.

    `validate(result)` returns a description of what is wrong, or None when the
    reply is usable. Provider errors (truncation, limits) propagate untouched.
    """
    feedback = ""
    for _ in range(attempts):
        result = parse_json(ai_service.generate_content(prompt + feedback, **kwargs))
        problem = "the reply was not a JSON object" if result is None else validate(result)
        if not problem:
            return result
        feedback = f"\n\nYOUR PREVIOUS REPLY WAS REJECTED: {problem}. Return JSON only."
    raise ValueError(f"{error} ({problem})")


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_auto() -> bool:
    return os.getenv("AI_BOOK_MODE", "review").strip().lower() == "auto"


def detail(message: str) -> None:
    """Chatty progress line: shown in review mode, dropped in auto mode."""
    if not is_auto():
        print(message)


def progress_bar(done: int, total: int, width: int = 20) -> str:
    filled = width * done // max(1, total)
    return f"[{'#' * filled}{'.' * (width - filled)}] {done}/{total}"


def progress(label: str, done: int, total: int, item: str = "") -> None:
    """Compact bar, redrawn in place on a console; one line per update otherwise."""
    line = f"{label} {progress_bar(done, total)} {item}".rstrip()
    if not sys.stdout.isatty():
        print(line, flush=True)
        return
    # Pad to the console width so a shorter title wipes the longer one before it;
    # never wrap, or the carriage return only rewinds the last row.
    width = shutil.get_terminal_size().columns - 1
    print("\r" + line[:width].ljust(width), end="\n" if done >= total else "", flush=True)


def save_text(filename: str, text: str, *, keep_history: bool = True) -> None:
    """Keep prior manuscript versions and replace the live file atomically."""
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        original = path.read_text(encoding="utf-8")
        if original == text:
            return
        if keep_history:
            history = path.parent / "revisions"
            history.mkdir(exist_ok=True)
            backup = history / f"{path.stem}.{text_digest(original)}.txt"
            if not backup.exists():
                backup.write_text(original, encoding="utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as stream:
            temporary = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    assert calculate_word_count("a b c") == 3
    assert calculate_page_count(250) == 1
    assert calculate_page_count(251) == 2
    assert pages_to_words(2) == 500
    assert progress_bar(0, 4, 4) == "[....] 0/4"
    assert progress_bar(2, 4, 4) == "[##..] 2/4"
    assert progress_bar(1, 0, 4) == "[####] 1/0"
    print("ok")
