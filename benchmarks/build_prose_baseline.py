"""Measure real published novels and write the humanness baseline.

The reference corpus is the user's own Calibre library: a few thousand English
novels, in AZW3, which Calibre's own ``ebook-convert`` turns into plain text.
Nothing here is invented — every number the humanizer gates on is a percentile
of prose that a human wrote and a publisher shipped.

    python benchmarks/build_prose_baseline.py             # build the baseline
    python benchmarks/build_prose_baseline.py --books 60  # wider sample
    python benchmarks/build_prose_baseline.py --bench     # score novels vs drafts
    python benchmarks/build_prose_baseline.py --tells     # phrases drafts overuse

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
    """Random English novels, one per author, as (title, book path).

    One book per author: the baseline has to describe how many different people
    write, not how the library's most prolific series author writes.
    """
    db = CALIBRE / "metadata.db"
    if not db.exists():
        raise SystemExit(f"no Calibre library at {CALIBRE}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT b.title, b.path, d.name, d.format, "
        "(SELECT group_concat(t.name, '|') FROM books_tags_link bt "
        "JOIN tags t ON t.id = bt.tag WHERE bt.book = b.id), "
        "(SELECT min(ba.author) FROM books_authors_link ba WHERE ba.book = b.id) FROM books b "
        "JOIN data d ON d.book = b.id "
        "JOIN books_languages_link bl ON bl.book = b.id "
        "JOIN languages l ON l.id = bl.lang_code "
        "WHERE l.lang_code = 'eng'").fetchall()
    con.close()
    random.Random(seed).shuffle(rows)
    out = []
    seen, authors = set(), set()
    for title, path, name, fmt, tags, author in rows:
        if title in seen or author in authors or not is_fiction(tags or ""):
            continue
        book = CALIBRE / path / f"{name}.{fmt.lower()}"
        if book.exists():
            out.append((title, book))
            seen.add(title)
            authors.add(author)
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
    tags = [t.strip() for t in tags.lower().split("|")]
    joined = "|".join(tags)
    # A bare "history" or "science" tag marks non-fiction in this library ("D-Day:
    # The Battle for Normandy" carries history|alternate history).
    if re.search(r"\b(non[ -]?fiction|biography|memoir|textbook|criticism|aesthetics)\b", joined) \
            or "history" in tags or "science" in tags:
        return False
    return bool(re.search(r"\b(fiction|novels?|fantasy|romance|mystery|thrillers?|horror|sci-fi|"
                          r"space opera|alternate history|post apocalyptic|dystopian|cyberpunk)\b", joined))


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
                   "opener_top_share", "neg_full_per_1k", "proper_per_1k",
                   "exclaim_per_1k", "question_per_1k"):
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
    if drafts:
        print(style_table([p.read_text(encoding="utf-8", errors="replace") for p in drafts], ref))


def style_table(chapters: list[str], ref: dict) -> str:
    """Where the drafts sit inside the published range, metric by metric."""
    ranges = ref.get("chapter_ranges") or ref.get("ranges") or {}
    prints = [humanizer.fingerprint(c) for c in chapters]
    rows = [f"\n{'metric':<18}{'novels p05':>11}{'p50':>8}{'p95':>8}{'drafts p50':>12}  verdict"]
    for metric, r in ranges.items():
        values = [fp[metric] for fp in prints if metric in fp]
        if not values or "p50" not in r:
            continue
        mid = statistics.median(values)
        verdict = ("below range" if mid < r["p05"] else "above range" if mid > r["p95"]
                   else "low side" if mid < r["p25"] else "high side" if mid > r["p75"] else "typical")
        rows.append(f"{metric:<18}{r['p05']:>11.2f}{r['p50']:>8.2f}{r['p95']:>8.2f}{mid:>12.2f}  {verdict}")
    return "\n".join(rows)


def overused(draft_text: str, corpus_text: str, sizes=(3, 4, 5), min_count: int = 8,
             top: int = 40) -> list[tuple[str, int, float]]:
    """Word sequences the drafts use far more often than published novels.

    The Antislop method (Paech et al., ICLR 2026): rank n-grams by their rate in
    generated text over their rate in human text. Candidates for LLM_TELLS; a
    character name or a book's own refrain will show up too, so read the list.
    """
    import collections
    words = lambda text: re.findall(r"[a-z']+", text.lower().replace("’", "'"))
    draft, human = words(draft_text), words(corpus_text)
    # A book's own names are always "overused"; drop words it capitalizes mid-sentence.
    capital = collections.Counter(w.lower() for w in re.findall(r"(?<=[a-z,] )[A-Z][a-z]+", draft_text))
    lower = collections.Counter(re.findall(r"\b[a-z]+\b", draft_text))
    names = {w for w, n in capital.items() if n > lower[w]}
    out = []
    for n in sizes:
        mine = collections.Counter(" ".join(draft[i:i + n]) for i in range(len(draft) - n + 1))
        theirs = collections.Counter(" ".join(human[i:i + n]) for i in range(len(human) - n + 1))
        for gram, count in mine.items():
            if count < min_count or names.intersection(gram.split()):
                continue
            # Add-one smoothing: a sequence novelists never write is capped, not infinite.
            ratio = (count / len(draft)) / ((theirs[gram] + 1) / len(human))
            out.append((gram, count, round(ratio, 1)))
    out.sort(key=lambda row: -row[2])
    kept: list[tuple[str, int, float]] = []
    for row in out:                      # drop shorter views of a longer listed phrase
        if not any(row[0] in k[0] or k[0] in row[0] for k in kept):
            kept.append(row)
        if len(kept) >= top:
            break
    return kept


def tells() -> None:
    corpus = " ".join(p.read_text(encoding="utf-8", errors="replace") for p in CORPUS.glob("*.txt"))
    drafts = " ".join(p.read_text(encoding="utf-8", errors="replace")
                      for p in GENERATED.glob("chapter_*.txt") if re.fullmatch(r"chapter_\d+", p.stem))
    if not corpus or not drafts:
        raise SystemExit("need cached corpus text and generated chapters")
    print(f"{'phrase':<40} {'count':>6} {'x novels':>9}")
    for gram, count, ratio in overused(drafts, corpus):
        print(f"{gram:<40} {count:>6} {ratio:>9}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--books", type=int, default=40)
    ap.add_argument("--bench", action="store_true", help="score novels vs generated chapters")
    ap.add_argument("--tells", action="store_true", help="list phrases drafts overuse vs novels")
    args = ap.parse_args()
    tells() if args.tells else bench(args.books) if args.bench else build(args.books)
