"""Measure how long real novel chapters are, relative to their own book.

Chapter length is not a constant: published novels run a long chapter and then
a two-page one, and the ratio of the shortest chapter to the median is often
0.2 or lower. This script reads the cached corpus in ``benchmarks/corpus/``
(built by ``build_prose_baseline.py``), splits each book on its chapter
headings, and writes the distribution of ``chapter_words / median_chapter_words``
into ``prose_baseline.json`` under ``chapter_shape``.

    python benchmarks/measure_chapter_shape.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_book_creator.utils import humanizer  # noqa: E402
from ai_book_creator.utils.text_utils import save_text  # noqa: E402

CORPUS = Path(__file__).resolve().parent / "corpus"

_WORD_NUMS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty"
)
HEADING = re.compile(
    rf"^(?:chapter|ch\.)\s+(?:\d+|[ivxlc]+|(?:{_WORD_NUMS})(?:[- ](?:{_WORD_NUMS}))?)\b",
    re.IGNORECASE,
)
# A table of contents is a run of headings with almost nothing between them.
TOC_BODY = 50


def chapter_texts(text: str) -> list[str]:
    """Split a book on its own chapter headings."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    out: list[list[str]] = []
    current: list[str] | None = None
    for para in paras:
        first = para.split("\n", 1)[0].strip()
        if len(first.split()) <= 8 and HEADING.match(first):
            if current is not None:
                out.append(current)
            current = []
            continue
        if current is not None:
            current.append(para)
    if current is not None:
        out.append(current)
    # Drop the contents page: consecutive near-empty "chapters".
    chapters = ["\n\n".join(c) for c in out]
    return [c for c in chapters if len(c.split()) >= TOC_BODY]


def chapter_words(text: str) -> list[int]:
    return [len(c.split()) for c in chapter_texts(text)]


MIN_CHAPTER_WORDS = 800          # below this a fingerprint is mostly noise


def main(baseline=None, persist=True):
    baseline = dict(baseline) if baseline is not None else humanizer.baseline()
    corpus_paths = [CORPUS / (re.sub(r"[^\w\- ]", "", title)[:80].strip() + ".txt")
                    for title in baseline["titles"]]
    ratios: list[float] = []
    books = 0
    per_book_min: list[float] = []
    # Style measured per chapter, which is the unit the pipeline is judged in.
    # Window percentiles are a different distribution: a whole chapter of
    # unbroken dialogue and a whole chapter with none are both ordinary, and a
    # 1,200-word window never sees either.
    per_chapter: dict[str, list[float]] = {}
    per_book_cv: dict[str, list[float]] = {}
    # How often a real chapter carries each "AI tell". A phrase a third of
    # published chapters use is not evidence of anything, and banning it pushes
    # the rewriter into odd synonyms.
    tell_hits: dict[str, int] = {t: 0 for t in humanizer.LLM_TELLS}
    scored_chapters = 0
    for path in corpus_paths:
        if not path.is_file():
            raise SystemExit(f"Missing calibration text: {path}; rebuild the baseline first")
        chapters = chapter_texts(path.read_text(encoding="utf-8", errors="replace"))
        counts = [len(c.split()) for c in chapters]
        if len(counts) < 8:
            continue
        median = statistics.median(counts)
        if median < 500:
            continue
        books += 1
        book_ratios = [c / median for c in counts]
        ratios += book_ratios
        per_book_min.append(min(book_ratios))

        long_enough = [c for c in chapters if len(c.split()) >= MIN_CHAPTER_WORDS]
        for chapter in long_enough:
            scored_chapters += 1
            lower = chapter.lower()
            for tell in tell_hits:
                if tell in lower:
                    tell_hits[tell] += 1
        prints = [humanizer.fingerprint(c) for c in long_enough]
        for key in prints[0] if prints else []:
            if key in ("sentences", "words"):
                continue
            values = [fp[key] for fp in prints]
            per_chapter.setdefault(key, []).extend(values)
            mean = statistics.fmean(values)
            if len(values) > 3 and mean:
                # How much a real book's own chapters differ from each other.
                # Drafts that all sit on the same number are uniform in a way
                # published books are not.
                per_book_cv.setdefault(key, []).append(statistics.pstdev(values) / mean)
        print(f"  {path.stem[:44]:<44} n={len(counts):>3} med={median:>6.0f} "
              f"min={min(book_ratios):.2f} max={max(book_ratios):.2f}")

    if books < 5:
        raise SystemExit("not enough books with detectable chapter headings")

    ratios.sort()

    def pct(p: float) -> float:
        return round(ratios[min(len(ratios) - 1, int(len(ratios) * p))], 2)

    shape = {
        "source": "Calibre corpus, chapters split on headings",
        "books": books,
        "chapters": len(ratios),
        "ratio_to_median": {
            "p02": pct(0.02), "p05": pct(0.05), "p10": pct(0.10), "p25": pct(0.25),
            "p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90), "p95": pct(0.95),
            "p98": pct(0.98),
        },
        "shortest_chapter_ratio_median": round(statistics.median(per_book_min), 2),
        "short_chapter_share": round(sum(1 for r in ratios if r < 0.5) / len(ratios), 3),
    }
    chapter_ranges = {
        key: {"p02": round(humanizer._pct(v, 2), 4), "p05": round(humanizer._pct(v, 5), 4),
              "p25": round(humanizer._pct(v, 25), 4), "p50": round(humanizer._pct(v, 50), 4),
              "p75": round(humanizer._pct(v, 75), 4), "p95": round(humanizer._pct(v, 95), 4),
              "p98": round(humanizer._pct(v, 98), 4), "n": len(v)}
        for key, v in per_chapter.items()
    }
    within = {key: round(statistics.median(v), 3) for key, v in per_book_cv.items()}

    baseline["chapter_shape"] = shape
    baseline["chapter_ranges"] = chapter_ranges
    baseline["within_book_cv"] = within
    baseline["tell_chapter_rate"] = {
        t: round(n / max(scored_chapters, 1), 4)
        for t, n in sorted(tell_hits.items(), key=lambda kv: -kv[1]) if n
    }
    if persist:
        save_text(str(humanizer.BASELINE_PATH), json.dumps(baseline, indent=2, ensure_ascii=False))
    print(f"\n{books} books, {len(ratios)} chapters "
          f"({chapter_ranges.get('burstiness', {}).get('n', 0)} fingerprinted)")
    print(json.dumps(shape, indent=2))
    print(f"\n{'metric':<18}{'p05':>9}{'p50':>9}{'p95':>9}{'within-book cv':>16}")
    for key in ("sent_len_mean", "short_sent_ratio", "ly_per_1k", "dialogue_ratio",
                "burstiness", "para_cv", "filter_per_1k", "ttr"):
        r = chapter_ranges.get(key, {})
        print(f"{key:<18}{r.get('p05', 0):>9}{r.get('p50', 0):>9}{r.get('p95', 0):>9}"
              f"{within.get(key, 0):>16}")
    return baseline


if __name__ == "__main__":
    main()
