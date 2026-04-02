"""
Style analysis helpers for chapter-opening regression checks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Iterable, Sequence


BAD_OPENING_PATTERNS = [
    r"^again\b",
    r"^the room\b",
    r"^the air\b",
    r"^he (?:looked|said|asked)\b",
    r"^she (?:looked|said|asked)\b",
    r"^across the world\b",
    r"^in the room\b",
    r"^outside\b",
]


@dataclass(frozen=True)
class StyleBenchmarkResult:
    total_chapters: int
    unique_openers: int
    duplicate_openers: int
    consecutive_duplicate_openers: int
    banned_phrase_hits: int
    diversity_score: float
    passes: bool
    opener_signatures: list[str]


def _normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def extract_opening_sentence(chapter_text: str) -> str:
    """Return the first meaningful sentence after any chapter heading."""
    lines = [line.strip() for line in chapter_text.splitlines()]
    candidate_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.startswith("#"):
            continue
        if re.match(r"^chapter\s+\d+\b", line, re.IGNORECASE):
            continue
        candidate_lines.append(line)

    if not candidate_lines:
        return ""

    paragraph = _normalise_text(candidate_lines[0])
    sentence_match = re.search(r"(.+?[.!?])(?:\s|$)", paragraph)
    return sentence_match.group(1).strip() if sentence_match else paragraph


def opening_signature(sentence: str, width: int = 6) -> str:
    """Create a compact opener signature that is robust enough for repetition checks."""
    words = re.findall(r"[A-Za-z0-9']+", sentence.lower())
    return " ".join(words[:width]).strip()


def first_opening_token(sentence: str) -> str:
    """Return the first non-empty token from an opening sentence."""
    words = re.findall(r"[A-Za-z0-9']+", sentence.lower())
    return words[0] if words else ""


def analyze_openings(chapter_texts: Sequence[str] | Iterable[str]) -> StyleBenchmarkResult:
    """Analyze a corpus of chapter texts for repeated or template-like openings."""
    texts = list(chapter_texts)
    opening_sentences = [extract_opening_sentence(text) for text in texts]
    signatures = [first_opening_token(sentence) for sentence in opening_sentences if sentence]
    counter = Counter(signatures)

    duplicate_openers = sum(count - 1 for count in counter.values() if count > 1)
    consecutive_duplicate_openers = sum(
        1 for previous, current in zip(signatures, signatures[1:]) if previous == current
    )
    banned_phrase_hits = 0
    for sentence in opening_sentences:
        lowered = sentence.lower()
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in BAD_OPENING_PATTERNS):
            banned_phrase_hits += 1

    total_chapters = len(texts)
    unique_openers = len(counter)
    diversity_score = unique_openers / total_chapters if total_chapters else 0.0
    passes = (
        total_chapters > 0
        and duplicate_openers == 0
        and consecutive_duplicate_openers == 0
        and banned_phrase_hits == 0
        and diversity_score >= 0.75
    )

    return StyleBenchmarkResult(
        total_chapters=total_chapters,
        unique_openers=unique_openers,
        duplicate_openers=duplicate_openers,
        consecutive_duplicate_openers=consecutive_duplicate_openers,
        banned_phrase_hits=banned_phrase_hits,
        diversity_score=diversity_score,
        passes=passes,
        opener_signatures=signatures,
    )


def prompt_mentions(prompt: str, phrases: Sequence[str]) -> bool:
    """Check that a prompt includes all required phrases."""
    lowered = prompt.lower()
    return all(phrase.lower() in lowered for phrase in phrases)


def format_benchmark_report(label: str, result: StyleBenchmarkResult) -> str:
    """Render a compact text report for benchmark output."""
    return (
        f"{label}: "
        f"chapters={result.total_chapters}, "
        f"unique_openers={result.unique_openers}, "
        f"duplicate_openers={result.duplicate_openers}, "
        f"consecutive_duplicates={result.consecutive_duplicate_openers}, "
        f"banned_hits={result.banned_phrase_hits}, "
        f"diversity={result.diversity_score:.2f}, "
        f"passes={result.passes}"
    )
