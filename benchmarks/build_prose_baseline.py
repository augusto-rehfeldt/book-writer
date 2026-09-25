"""Measure real published novels and write the humanness baseline.

The reference corpus is the user's own Calibre library: a few thousand English
novels, in AZW3, which Calibre's own ``ebook-convert`` turns into plain text.
Nothing here is invented — every number the humanizer gates on is a percentile
of prose that a human wrote and a publisher shipped.

    python benchmarks/build_prose_baseline.py             # build the baseline
    python benchmarks/build_prose_baseline.py --books 60  # wider sample
    python benchmarks/build_prose_baseline.py --bench     # score novels vs drafts

Extracted text is cached in ``benchmarks/corpus/`` so a rebuild costs no
conversions. The cache is disposable and gitignored.
"""

from __future__ import annotations

import argparse
import json
import random
import hashlib
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_book_creator.utils import humanizer  # noqa: E402
from ai_book_creator.utils.text_utils import save_text  # noqa: E402

CORPUS = Path(__file__).resolve().parent / "corpus"
CALIBRE = Path.home() / "Calibre Library"
GENERATED = ROOT / "book_output"

# Calibre ships ebook-convert next to the app; it is rarely on PATH on Windows.
CONVERT_CANDIDATES = (
    "ebook-convert",
    r"C:\Program Files\Calibre2\ebook-convert.exe",
    r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
)

# Front matter, copyright pages and "also by this author" lists are not prose.
SKIP_HEAD = 0.08
SKIP_TAIL = 0.03


def ebook_convert() -> str:
    for candidate in CONVERT_CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else "")
        if found:
            return found
    raise SystemExit("ebook-convert not found — install Calibre or pass --corpus-only")


def library_books(limit: int, seed: int = 7) -> list[tuple[str, Path]]:
    """Random English books from the Calibre library, as (title, azw3 path)."""
    db = CALIBRE / "metadata.db"
    if not db.exists():
        raise SystemExit(f"no Calibre library at {CALIBRE}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT b.title, b.path, d.name, d.format, "
        "(SELECT group_concat(t.name, '|') FROM books_tags_link bt "
        "JOIN tags t ON t.id = bt.tag WHERE bt.book = b.id) FROM books b "
        "JOIN data d ON d.book = b.id "
        "JOIN books_languages_link bl ON bl.book = b.id "
        "JOIN languages l ON l.id = bl.lang_code "
        "WHERE l.lang_code = 'eng'").fetchall()
    con.close()
    random.Random(seed).shuffle(rows)
    out = []
    seen = set()
    for title, path, name, fmt, tags in rows:
        if title in seen or not is_fiction(tags or ""):
            continue
        book = CALIBRE / path / f"{name}.{fmt.lower()}"
        if book.exists():
            out.append((title, book))
            seen.add(title)
        if len(out) >= limit:
            break
    return out


def extract(title: str, book: Path, convert: str) -> str:
    """Convert one book to plain text, cached on disk."""
    cached = CORPUS / (re.sub(r"[^\w\- ]", "", title)[:80].strip() + ".txt")
    if cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace")
    CORPUS.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([convert, str(book), str(cached)],
                       capture_output=True, timeout=300, check=True)
    except Exception as exc:  # noqa: BLE001 - a book that will not convert is skipped
        print(f"  · skipped {title}: {type(exc).__name__}")
        return ""
    return cached.read_text(encoding="utf-8", errors="replace") if cached.exists() else ""


def body(text: str) -> str:
    """Drop front and back matter by position; keep the middle of the book."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paras) < 40:
        return ""
    lo = int(len(paras) * SKIP_HEAD)
    hi = int(len(paras) * (1 - SKIP_TAIL))
    return "\n\n".join(paras[lo:hi])


def is_fiction(tags: str) -> bool:
    """Select by library metadata, never by a prose feature being measured.

    Untagged books are excluded; correct their Calibre tags before calibration.
    """
    tags = tags.lower()
    if re.search(r"\b(non[ -]?fiction|biography|memoir|textbook)\b", tags):
        return False
    return bool(re.search(r"\b(fiction|novels?|fantasy|romance|mystery|thrillers?|horror)\b", tags))


def gather(limit: int, holdout: bool = False) -> list[tuple[str, str]]:
    convert = ebook_convert()
    out = []
    for title, book in library_books(limit * 6):
        # Split by book, not windows: an author's adjacent passages must not leak
        # into the same-book calibration and evaluation sets.
        is_holdout = int(hashlib.sha256(title.encode("utf-8")).hexdigest(), 16) % 5 == 0
        if is_holdout != holdout:
            continue
        text = body(extract(title, book, convert))
        if len(text.split()) < 20000:
            continue
        out.append((title, text))
        print(f"  · {title}: {len(text.split()):,} words")
        if len(out) >= limit:
            break
    return out


def build(limit: int) -> dict:
    print(f"Sampling {limit} English books from {CALIBRE}…")
    corpus = gather(limit)
    if len(corpus) < 5:
        raise SystemExit("not enough usable books; try --books 60")
    joined = " ".join(t for _, t in corpus).lower()
    # A stock phrase that published novelists also use at a normal rate is not a
    # tell — penalising it would push the rewriter away from real prose.
    per_million = {t: joined.count(t) / max(len(joined.split()), 1) * 1_000_000
                   for t in humanizer.LLM_TELLS}
    corpus_ok = sorted(t for t, rate in per_million.items() if rate >= 10)
    ranges = humanizer.corpus_ranges([t for _, t in corpus])
    baseline = {
        "source": "Calibre library, English fiction, ebook-convert to text",
        "selection": "Calibre fiction tags; SHA256(title) modulo 5 == 0 reserved for evaluation",
        "books": len(corpus),
        "titles": [t for t, _ in corpus],
        "window_words": 1200,
        "windows": ranges.get("burstiness", {}).get("n", 0),
        "corpus_ok": corpus_ok,
        "tell_rate_per_million": {t: round(r, 2) for t, r in sorted(
            per_million.items(), key=lambda kv: -kv[1]) if r > 0},
        "ranges": ranges,
    }
    from benchmarks.measure_chapter_shape import main as measure_chapters
    baseline = measure_chapters(baseline, persist=False)
    save_text(str(humanizer.BASELINE_PATH), json.dumps(baseline, indent=2, ensure_ascii=False))
    print(f"\nWrote {humanizer.BASELINE_PATH} "
          f"({baseline['books']} books, {baseline['windows']} windows)")
    for metric in ("burstiness", "sent_len_mean", "short_sent_ratio", "para_cv",
                   "dialogue_ratio", "filter_per_1k", "ly_per_1k", "dash_per_1k",
                   "opener_top_share"):
        r = ranges.get(metric, {})
        print(f"  {metric:<18} p05={r.get('p05')}  p50={r.get('p50')}  p95={r.get('p95')}")
    print(f"  phrases real novelists also use: {', '.join(corpus_ok) or 'none'}")
    return baseline


def bench(limit: int) -> None:
    """Score real novels against generated chapters. The gap is the whole point.

    Real prose is split into its own chapters, not into windows: the pipeline is
    judged a chapter at a time, and the two distributions are not the same.
    """
    from benchmarks.measure_chapter_shape import chapter_texts

    ref = humanizer.baseline()
    if not ref:
        raise SystemExit("build the baseline first")
    print("Scoring real novel chapters…")
    human: list[float] = []
    for title, text in gather(limit, holdout=True):
        if title in ref.get("titles", []):
            print(f"  · excluded calibration book: {title}")
            continue
        chapters = [c for c in chapter_texts(text) if len(c.split()) >= 1500][:6]
        if not chapters:                      # no detectable headings; fall back
            chapters = humanizer.windows(text, 2500)[3:7]
        scores = [humanizer.local_score(c, ref)["score"] for c in chapters]
        human += scores
        print(f"  {title[:52]:<52} {[round(s, 1) for s in scores]}")
    drafts = sorted(p for p in GENERATED.glob("chapter_*.txt")
                    if re.fullmatch(r"chapter_\d+", p.stem))
    print("\nScoring generated chapters…")
    machine = []
    for path in drafts:
        score = humanizer.local_score(path.read_text(encoding="utf-8", errors="replace"), ref)
        machine.append(score["score"])
        print(f"  {path.name:<20} {score['score']:>6}   {score['issues'][0][:70] if score['issues'] else ''}")
    if human and machine:
        print(f"\nreal novels:        median {statistics.median(human):.1f}  "
              f"max {max(human):.1f}  n={len(human)}")
        print(f"generated chapters: median {statistics.median(machine):.1f}  "
              f"min {min(machine):.1f}  n={len(machine)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--books", type=int, default=40)
    ap.add_argument("--bench", action="store_true", help="score novels vs generated chapters")
    args = ap.parse_args()
    bench(args.books) if args.bench else build(args.books)
