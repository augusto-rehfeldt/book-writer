"""Humanness scoring and rewriting for English novel prose.

Three layers, because no single signal is trustworthy:

  1. ``local_score`` — stylometry against ranges measured on real published
     novels (``config/prose_baseline.json``, built by
     ``benchmarks/build_prose_baseline.py`` from the user's Calibre library).
     Most public AI detectors are perplexity/burstiness classifiers underneath,
     so matching the rhythm real novelists actually write in is the highest
     leverage fix available.
  2. ``llm_judges`` — models asked to bet on whether a human wrote the page. A
     model rarely flags its own output, so a judge from another family is worth
     far more than asking the writer to grade itself.
  3. ``humanize`` — a rewrite-then-rescore loop, block by block with counted
     quotas. A whole-chapter rewrite regresses to the model's default register.

Run it standalone on any text to get a number:

    python -m ai_book_creator.utils.humanizer book_output/chapter_01.txt
"""

from __future__ import annotations

import collections
import json
import os
import random
import re
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASELINE_PATH = Path(__file__).resolve().parent.parent / "config" / "prose_baseline.json"

# Phrases that mark machine-written fiction. Measured against the reference
# corpus when the baseline is built: anything real novelists use at a normal
# rate is dropped from this list at build time and lands in "corpus_ok".
LLM_TELLS = [
    "a testament to", "a mixture of", "a mix of relief and", "somewhere between",
    "the weight of the", "the weight of it", "a wave of", "a pang of",
    "couldn't help but", "could not help but", "let out a breath",
    "breath she didn't know", "breath he didn't know",
    "little did", "unbeknownst to", "in that moment", "in this moment",
    "the air was thick with", "the air itself seemed", "the silence stretched",
    "hung in the air", "hung heavy", "a shiver ran down", "sent a shiver",
    "heart hammered against", "heart pounded in her chest", "heart pounded in his chest",
    "took a deep breath and", "released a breath",
    "for the first time in a long time", "for the first time in years",
    "something shifted", "something had shifted", "a knot formed",
    "her breath caught", "his breath caught", "she realized that", "he realized that",
    "it was then that", "she couldn't shake the feeling", "he couldn't shake the feeling",
    "the reality of the situation", "the gravity of the situation",
    "a stark reminder", "served as a reminder", "a delicate balance",
    "navigate the complexities", "the complexities of", "delve into",
    "in the grand scheme", "at the end of the day,", "one thing was certain",
    "only time would tell", "little by little", "step by step,",
    "a symphony of", "a tapestry of", "a testament of", "an air of finality",
    "eyes that held", "eyes that seemed to", "a ghost of a smile",
    "the corners of her mouth", "the corners of his mouth",
    "she said, her voice barely above a whisper", "barely above a whisper",
    "voice thick with emotion", "with a mixture of", "a sense of unease",
    "the world seemed to", "time seemed to", "as if on cue",
    "little more than a", "nothing more than a", "more than just a",
]

# (pattern, max hits per word, message). Rates, not counts, so the check works
# on a scene and on a whole book.
STRUCTURAL_TELLS = [
    # No semicolon rule: published novels run ~0.65 per 1k words and this
    # pipeline's drafts run ~0.0, so penalising them points the wrong way.
    (r"(?m)^\s*[-*•]\s", 0.0008, "bullet lists inside a novel"),
    (r"(?m)^#{2,}\s", 0.002, "subheadings inside a chapter"),
    (r"\b(?:Firstly|Secondly|Moreover|Furthermore|Additionally|In conclusion)\b",
     0.0004, "essay connectives in narrative prose"),
]

# Verbs that put a narrating layer between the reader and the scene. Real
# fiction uses them; generated fiction leans on them roughly twice as hard.
FILTER_VERBS = ("felt", "saw", "heard", "noticed", "watched", "realized",
                "realised", "seemed", "wondered", "knew that", "could feel",
                "could see", "could hear")

_SENT = re.compile(r"[^.!?…]+[.!?…]+[\"'”’\)\]]*|\S+$")
_WORD = re.compile(r"[A-Za-z'’]{2,}")
_QUOTE = re.compile(r"[\"“”]|(?<![A-Za-z])'(?=[A-Za-z])")


def sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT.findall(text) if len(s.strip()) > 1]


def _pct(values: List[float], p: float) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _strip_markup(text: str) -> str:
    """Drop chapter headings and scene-break markers before measuring.

    A `# Chapter Three` line is a one-word sentence and a one-word paragraph; a
    dozen of them fake both burstiness and paragraph variance.
    """
    keep = [ln for ln in text.splitlines()
            if not ln.lstrip().startswith("#") and ln.strip() not in ("***", "*", "---")]
    return "\n".join(keep)


def _msttr(words: List[str], segment: int = 400) -> float:
    """Mean segmental type-token ratio: lexical variety, independent of length.

    Plain TTR falls as a text gets longer, so a 3,000-word chapter would always
    look poorer than a 1,200-word reference window no matter who wrote it.
    Averaging fixed-size segments is what makes the two comparable at all.
    """
    if len(words) < segment:
        return round(len(set(words)) / max(len(words), 1), 4)
    chunks = [words[i:i + segment] for i in range(0, len(words) - segment + 1, segment)]
    return round(statistics.fmean(len(set(c)) / segment for c in chunks), 4)


def fingerprint(text: str) -> Dict[str, float]:
    """Numeric signature of a passage. Same shape for novels and for drafts."""
    body = _strip_markup(text)
    sents = sentences(body)
    lens = [len(s.split()) for s in sents] or [0]
    words = _WORD.findall(body.lower())
    n_words = max(len(words), 1)
    paras = [p for p in body.split("\n\n") if p.strip()]
    para_lens = [len(p.split()) for p in paras if len(p.split()) > 15]
    mean = statistics.fmean(lens)
    sd = statistics.pstdev(lens) if len(lens) > 1 else 0.0
    dialogue = sum(1 for p in paras if _QUOTE.search(p))
    openers = collections.Counter(s.split()[0].lower() for s in sents if s.split())
    filters = sum(len(re.findall(rf"\b{v}\b", body.lower())) for v in FILTER_VERBS)
    return {
        "sentences": len(sents),
        "words": len(words),
        "sent_len_mean": round(mean, 2),
        "sent_len_sd": round(sd, 2),
        # Burstiness: the single strongest public-detector feature.
        "burstiness": round(sd / mean, 3) if mean else 0.0,
        "short_sent_ratio": round(sum(1 for x in lens if x < 8) / len(lens), 3),
        "long_sent_ratio": round(sum(1 for x in lens if x > 35) / len(lens), 3),
        "para_len_mean": round(statistics.fmean(para_lens), 1) if para_lens else 0.0,
        "para_cv": round(statistics.pstdev(para_lens) / statistics.fmean(para_lens), 3)
                   if len(para_lens) > 3 else 0.0,
        "dialogue_ratio": round(dialogue / len(paras), 3) if paras else 0.0,
        "ttr": _msttr(words),
        "ly_per_1k": round(sum(1 for w in words if w.endswith("ly")) / n_words * 1000, 2),
        "filter_per_1k": round(filters / n_words * 1000, 2),
        "dash_per_1k": round(body.count("—") / n_words * 1000, 2),
        "semicolon_per_1k": round(body.count(";") / n_words * 1000, 2),
        # Share of sentences opening on the single most common word. Generated
        # prose funnels into "The/She/He" far harder than published fiction.
        "opener_top_share": round(openers.most_common(1)[0][1] / len(sents), 3) if sents else 0.0,
    }


def windows(text: str, size: int = 1200) -> List[str]:
    """Split into ~``size``-word chunks on paragraph boundaries."""
    out: List[str] = []
    buf: List[str] = []
    n = 0
    for para in text.split("\n\n"):
        w = len(para.split())
        if n + w > size and buf:
            out.append("\n\n".join(buf))
            buf, n = [], 0
        buf.append(para)
        n += w
    if n > size // 3:
        out.append("\n\n".join(buf))
    return out


def corpus_ranges(texts: List[str], size: int = 1200) -> Dict[str, Dict[str, float]]:
    """Percentiles of each metric over same-sized windows of real prose.

    Windows, not whole books: variance grows with length, so a 1,800-word
    chapter measured against a 120,000-word aggregate scores as "too even" even
    when a human wrote it. The comparison has to happen at the size being judged.
    """
    per: Dict[str, List[float]] = {}
    for text in texts:
        for chunk in windows(text, size):
            fp = fingerprint(chunk)
            # Skip degenerate windows: front matter, tables of contents, poem
            # pages. They drag the low percentiles into nonsense.
            if fp["sentences"] < 15 or fp["words"] < size // 2:
                continue
            for k, v in fp.items():
                if k not in ("sentences", "words"):
                    per.setdefault(k, []).append(v)
    return {k: {"p05": round(_pct(v, 5), 4), "p25": round(_pct(v, 25), 4),
                "p50": round(_pct(v, 50), 4), "p75": round(_pct(v, 75), 4),
                "p95": round(_pct(v, 95), 4), "n": len(v)}
            for k, v in per.items()}


def baseline() -> Dict[str, Any]:
    """Measured ranges from real published novels. Empty dict when not built."""
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def repeated_phrases(text: str, n: int = 6, times: int = 2, limit: int = 12,
                     cap: int = 30) -> List[str]:
    """Verbatim word sequences the text serves more than once.

    Chapters are drafted independently, so the same image comes back four
    chapters later word for word. Overlapping windows are merged, so a repeated
    sentence reports once instead of as a dozen shifted views of itself.
    """
    words = _WORD.findall(text.lower())
    if len(words) <= n:
        return []
    grams = [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]
    counts = collections.Counter(grams)
    spans: List[List[int]] = []
    for i, g in enumerate(grams):
        if counts[g] < times:
            continue
        if spans and i - spans[-1][1] <= 1:
            spans[-1][1] = i
        else:
            spans.append([i, i])
    out, seen = [], set()
    for a, b in spans:
        phrase = " ".join(words[a:min(b + n, a + cap)])
        if phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
        if len(out) >= limit:
            break
    return out


# (metric, side, fallback bound, points per unit of deviation, cap, message).
#
# Weights follow the separation actually measured between 48 published novels
# and this pipeline's own drafts (benchmarks/build_prose_baseline.py --bench,
# 2026-08-22). The four that separate cleanly carry the weight:
#
#   metric            real p05..p95      drafts        reads as
#   ly_per_1k         6.9 .. 25.7        1.7 .. 6.4    machine (no overlap)
#   short_sent_ratio  0.12 .. 0.60       0.58 .. 0.74  machine (staccato)
#   sent_len_mean     7.9 .. 20.8        6.4 .. 8.9    machine (chopped)
#   opener_top_share  0.06 .. 0.21       0.09 .. 0.22  weak
#
# The "-ly adverbs are weak writing" rule, applied by a model at full strength,
# is itself the loudest tell: real novelists run about 14 per 1,000 words.
CHECKS = [
    ("ly_per_1k", "low", 6.9, 2.2, 22.0,
     "-ly adverbs at {value}/1k words, under the published floor {bound:.1f}. Novels "
     "average ~14 per 1k. Stop deleting adverbs; the scrubbed-adverb register is a tell"),
    ("short_sent_ratio", "high", 0.60, 90.0, 20.0,
     "{value:.0%} of sentences are under 8 words (published ceiling {bound:.0%}): "
     "relentless staccato. Let clauses run on and join with commas, 'and', semicolons"),
    ("sent_len_mean", "low", 7.9, 5.0, 18.0,
     "average sentence {value} words, under the published floor {bound:.1f}; novels "
     "sit near 11"),
    ("sent_len_mean", "high", 20.8, 2.5, 14.0,
     "average sentence {value} words, over the published ceiling {bound:.1f}"),
    ("long_sent_ratio", "low", 0.004, 900.0, 8.0,
     "no sentences over 35 words ({value:.3f} vs floor {bound:.3f}); real chapters "
     "carry a few long ones that keep their footing"),
    ("burstiness", "low", 0.52, 160.0, 20.0,
     "burstiness {value} under the human floor {bound:.2f}: sentence lengths are too "
     "even, put very short next to very long"),
    ("opener_top_share", "high", 0.21, 90.0, 12.0,
     "{value:.0%} of sentences open on the same word (published ceiling {bound:.0%})"),
    ("para_cv", "low", 0.33, 45.0, 12.0,
     "paragraphs are all one size (cv={value}, floor {bound:.2f}); mix one-line "
     "paragraphs with long ones"),
    ("dialogue_ratio", "low", 0.155, 45.0, 12.0,
     "almost no dialogue ({value:.2f} of paragraphs vs floor {bound:.2f}); scenes are "
     "being summarised instead of played"),
    ("filter_per_1k", "high", 7.7, 1.5, 10.0,
     "filter verbs (felt/saw/heard/seemed/realized) at {value}/1k, over the ceiling "
     "{bound:.1f}; let the scene happen instead of reporting it"),
    ("dash_per_1k", "high", 11.5, 1.2, 10.0,
     "em dashes at {value}/1k words, over the published ceiling {bound:.1f}"),
    ("ttr", "low", 0.50, 120.0, 10.0,
     "narrow vocabulary (segmental type-token {value} vs floor {bound:.2f}); the same "
     "nouns and verbs keep coming back"),
]


def _bound(ranges: Dict[str, Any], metric: str, side: str, fallback: float) -> float:
    entry = ranges.get(metric) or {}
    return entry.get("p05" if side == "low" else "p95", fallback)


# Below this a text is judged against 1,200-word window percentiles, above it
# against whole-chapter percentiles. The two distributions are genuinely
# different: a chapter that is nothing but dialogue and a chapter with none are
# both ordinary, and no 1,200-word window of either looks like the average.
CHAPTER_SCALE_WORDS = 1500


def scale_ranges(ref: Dict[str, Any], words: int) -> Dict[str, Any]:
    """The percentile table measured at the size of the thing being judged."""
    if words >= CHAPTER_SCALE_WORDS and ref.get("chapter_ranges"):
        return ref["chapter_ranges"]
    return ref.get("ranges") or {}


def quantile(ranges: Dict[str, Any], metric: str, u: float, fallback: float) -> float:
    """Value of ``metric`` at quantile ``u``, interpolated between percentiles."""
    entry = ranges.get(metric) or {}
    points = sorted((int(k[1:]) / 100, float(v)) for k, v in entry.items() if k.startswith("p"))
    if not points:
        return fallback
    if u <= points[0][0]:
        return points[0][1]
    for (q0, v0), (q1, v1) in zip(points, points[1:]):
        if u <= q1:
            return v0 + (v1 - v0) * (u - q0) / (q1 - q0)
    return points[-1][1]


def chapter_lane(seed: int, ref: Optional[Dict[str, Any]] = None) -> str:
    """A per-chapter register drawn from the corpus, as prose rather than quotas.

    One target for every chapter is its own tell: real books vary chapter to
    chapter by a coefficient of variation of 0.13-0.19 on rhythm, this pipeline
    by 0.08. Handing each chapter a different lane out of the measured
    distribution buys that variance back without narrowing what any one chapter
    is allowed to do — the lane always sits inside the range the scorer accepts.

    Pace and dialogue are drawn separately: a chase is clipped whether or not
    anyone is talking.
    """
    ref = baseline() if ref is None else ref
    ranges = ref.get("chapter_ranges") or ref.get("ranges") or {}
    rng = random.Random(seed)
    # Clamped to p05..p95: the lane must never aim at a target the scorer would
    # then mark as a failure. The scorer's own bounds are those percentiles.
    pace, talk = 0.05 + rng.random() * 0.9, 0.05 + rng.random() * 0.9
    # Fast chapters are short-sentenced: one draw, read from both ends.
    sent_len = quantile(ranges, "sent_len_mean", 1.0 - pace, 11.5)
    short = quantile(ranges, "short_sent_ratio", pace, 0.40)
    dialogue = quantile(ranges, "dialogue_ratio", talk, 0.64)
    if pace > 0.66:
        pace_note = ("fast and clipped: short sentences carry it, and the long ones are "
                     "rare and deliberate")
    elif pace < 0.33:
        pace_note = ("slow and dense: long periods with subordinate clauses, room for "
                     "interiority, short sentences used as punctuation")
    else:
        pace_note = "middling: the sentence lengths move around without a settled habit"
    if dialogue > 0.7:
        talk_note = "carried almost entirely by people talking"
    elif dialogue < 0.3:
        talk_note = "almost all narration and action, with speech used sparingly"
    else:
        talk_note = "a normal mix of scene and speech"
    return (
        "REGISTER FOR THIS CHAPTER (drawn from the reference corpus; other chapters of "
        "this book get different ones, and that variation is the point — do not write "
        "every chapter at the same tempo):\n"
        f"- Pace: {pace_note}. Around {sent_len:.0f} words per sentence on average, "
        f"roughly {short:.0%} of them under 8 words.\n"
        f"- Texture: {talk_note} (about {dialogue:.0%} of paragraphs carry dialogue).\n"
        "These are the centre of a wide lane, not a quota. Miss them for a good reason "
        "and the chapter is still right; hit them by counting words and it is not."
    )


def book_report(chapters: List[str], ref: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Whether a finished book varies chapter to chapter the way real books do.

    Every chapter can be inside the human range and the book still read as
    machine-written, because published novels differ from themselves more than
    this pipeline differs from itself. Measured medians live in the baseline
    under ``within_book_cv``.
    """
    ref = baseline() if ref is None else ref
    real_cv = ref.get("within_book_cv") or {}
    prints = [fingerprint(c) for c in chapters if len(c.split()) >= 800]
    out: Dict[str, Any] = {"chapters": len(prints), "uniform": [], "cv": {}}
    if len(prints) < 4 or not real_cv:
        return out
    for metric, real in real_cv.items():
        values = [fp[metric] for fp in prints if metric in fp]
        mean = statistics.fmean(values) if values else 0.0
        if not mean:
            continue
        cv = statistics.pstdev(values) / mean
        out["cv"][metric] = round(cv, 3)
        # 0.6 of the published spread, not 1.0: matching the median exactly is
        # its own kind of artificial, and this only has to catch flatness.
        if cv < real * 0.6:
            out["uniform"].append(
                f"{metric}: chapters vary by cv={cv:.2f}, published books vary by "
                f"{real:.2f}. The chapters are more alike than a real novel's are")
    return out


def local_score(text: str, ref: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """0 = reads like a published novel, 100 = reads like a chatbot.

    Being *inside* the range real novelists write in costs nothing, which is the
    only way the number means anything. Measured 2026-08-22 over 48 published
    novels against this pipeline's own 13 drafts: real prose 0-13 (median 0),
    drafts 8.5-42.5 (median 18.5). The ranges touch, so 15 is a soft gate and
    the score is mainly a list of what to fix; judges are the sharper signal.
    """
    ref = baseline() if ref is None else ref
    allowed = set(ref.get("corpus_ok") or [])
    fp = fingerprint(text)
    ranges = scale_ranges(ref, fp["words"])
    lower = text.lower()
    words = max(fp["words"], 1)
    penalties: List[Tuple[float, str]] = []

    failed: List[str] = []
    for metric, side, fallback, per_unit, cap, message in CHECKS:
        value = fp.get(metric)
        if value is None or (metric in ("para_cv", "long_sent_ratio") and not fp["sentences"]):
            continue
        if metric == "para_cv" and not value:      # too few paragraphs to judge
            continue
        limit = _bound(ranges, metric, side, fallback)
        gap = (limit - value) if side == "low" else (value - limit)
        if gap > 0:
            failed.append(f"{metric}:{side}")
            penalties.append((min(cap, gap * per_unit),
                              message.format(value=value, bound=limit)))

    # Tells are priced by how often published chapters carry them, not banned
    # flat. 36% of real chapters contain at least one of this list and 27 of the
    # phrases show up in more than 1% of them; charging 5 points a hit made the
    # rewriter hunt for synonyms of things novelists write on purpose.
    rates = ref.get("tell_chapter_rate") or {}
    hits = [t for t in LLM_TELLS if t not in allowed and t in lower]
    if hits:
        cost = sum(1.5 if rates.get(t, 0.0) > 0.01 else 5.0 for t in hits)
        loud = sorted(hits, key=lambda t: rates.get(t, 0.0))
        penalties.append((min(30.0, cost),
                          "stock AI-fiction phrases: " + "; ".join(f"«{h}»" for h in loud[:10])))
    for pattern, per_word_max, label in STRUCTURAL_TELLS:
        n = len(re.findall(pattern, text))
        if n / words > per_word_max:
            penalties.append((min(10.0, n * 1.2), label))
    # times=3, not 2: at chapter scale a twice-repeated 6-gram fires on 59% of
    # published chapters (character names, a repeated line of dialogue) against
    # 77% of drafts, which is not a signal. Three occurrences separates.
    if (repeats := repeated_phrases(text, times=3)):
        penalties.append((min(8.0, 2.0 * len(repeats)),
                          "phrases repeated near-verbatim three times or more (rewrite "
                          "or cut): " + "; ".join(f"«{r}»" for r in repeats[:6])))

    return {"score": round(min(100.0, sum(p for p, _ in penalties)), 1),
            "issues": [m for _, m in penalties],
            "failed": failed,
            "metrics": fp}


JUDGE_PROMPT = """You are a forensic detector of machine-written fiction. Read the passage \
and estimate the probability (0-100) that a language model wrote it.

Weigh: rhythmic uniformity, prefabricated transitions, suspiciously symmetrical paragraphs, \
generic sensory detail, emotions named instead of shown, every scene closing on a summarising \
beat, dialogue where nobody interrupts or misunderstands, an absence of idiosyncrasy or risk.

Reply with JSON only:
{"ai_probability": <0-100>, "verdict": "human"|"ai"|"unsure", \
"signals": ["...", "..."], "suspect_lines": ["verbatim quote", "..."]}

PASSAGE:
"""


def _parse_json(raw: str) -> Optional[dict]:
    for candidate in (raw.strip(),
                      *(m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.S)),
                      raw[raw.find("{"):raw.rfind("}") + 1] if "{" in raw else ""):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def llm_judges(ai_service, text: str, models: Optional[List[str]] = None,
               log=print) -> List[Dict[str, Any]]:
    """Ask other models to bet on whether a human wrote it.

    ``models`` defaults to ``judge_models`` in the AI config. Leave it empty and
    the gate runs on stylometry alone: a text judged by the family that wrote it
    measures nothing, so a judge that is not a stranger is worse than none.
    """
    models = models if models is not None else list(ai_service.config.get("judge_models") or [])
    out: List[Dict[str, Any]] = []
    for model in models:
        try:
            raw = ai_service.generate_content(JUDGE_PROMPT + text[:20000],
                                              model_type="review", model=model,
                                              max_completion_tokens=1024)
        except Exception as exc:  # noqa: BLE001 - a dead judge must not stop the run
            log(f"  · judge {model} unavailable: {type(exc).__name__}")
            continue
        parsed = _parse_json(raw)
        if not parsed:
            log(f"  · judge {model} returned no JSON")
            continue
        parsed["model"] = model
        out.append(parsed)
        log(f"  · judge {model}: {parsed.get('ai_probability')}% AI ({parsed.get('verdict')})")
    return out


REWRITE_PROMPT = """Rewrite this PASSAGE so it reads like a page from a published novel. This \
is not a polish pass and it is not a general improvement: fix the measured problems below and \
leave everything else alone.

WHAT THIS PASSAGE (~{words} words) MEASURABLY NEEDS. Nothing else is being asked for:
{quotas}

WHAT GIVES A MACHINE AWAY, and what to do instead:
- Every paragraph makes the same move (state a beat, develop it, close it). Break that. Let a \
paragraph end mid-thought, on a digression, on an unanswered question, on an object.
- Emotion gets named after it has been shown. Cut the naming sentence. Trust the action.
- Filter verbs (felt, saw, heard, noticed, realized, seemed) put a narrator between the reader \
and the scene. Delete most of them and let the thing happen on the page.
- Dialogue in which everyone is articulate and nobody interrupts. Let people talk past each \
other, evade, answer a different question, or say nothing.
- Stock phrases: "a mixture of", "the weight of", "hung in the air", "couldn't help but", \
"a shiver ran down", "let out a breath she didn't know she was holding", "in that moment". \
Cut every one you find.
- Do not close on a summary of what just happened, and never on a broad statement about life.

HARD RULES:
- Keep every plot event, character action, and piece of dialogue content. You are changing how \
it reads, not what happens.
- Keep the length within 10% of the original.
- No headings, no bullets, no bold, no commentary. Narrative prose only.
- Keep `***` scene breaks exactly where they already are.

MEASURED PROBLEMS in the full chapter:
{issues}

=== PASSAGE ===
{text}
=== END PASSAGE ===

Return only the rewritten passage."""


def _blocks(text: str, batch_words: int = 900) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    buf: List[str] = []
    n = 0
    for para in text.split("\n\n"):
        if para.lstrip().startswith("#"):
            if buf:
                out.append(("text", "\n\n".join(buf)))
                buf, n = [], 0
            out.append(("head", para))
            continue
        buf.append(para)
        n += len(para.split())
        if n >= batch_words:
            out.append(("text", "\n\n".join(buf)))
            buf, n = [], 0
    if buf:
        out.append(("text", "\n\n".join(buf)))
    return out


# One quota line per failing metric. A rewrite that hands the model every quota
# every time flattens the prose it was supposed to save: the passage gets torn
# up for problems it does not have. Keys are `metric:side` from CHECKS.
QUOTAS = {
    "ly_per_1k:low":
        "- at least {adverbs} -ly adverbs, used where they carry meaning ('quietly', "
        "'almost', 'finally'). Adverbs were stripped out of this passage and that is "
        "the loudest machine signature in it",
    "short_sent_ratio:high":
        "- stop chopping. At least {long} sentences of MORE than 25 words that keep "
        "their footing, built with commas, 'and', a semicolon, a subordinate clause",
    "sent_len_mean:low":
        "- let the sentences breathe: join clauses that were split for effect, and "
        "keep at least {long} periods over 25 words",
    "sent_len_mean:high":
        "- at least {short} sentences of FEWER than 8 words, landing like a verdict",
    "burstiness:low":
        "- put a very short sentence directly next to a very long one, at least "
        "{short} times. The lengths are too even",
    "long_sent_ratio:low":
        "- at least {long} sentence over 35 words that does not lose its footing",
    "para_cv:low":
        "- at least {oneline} paragraph of a single line, sitting between two long ones",
    "dialogue_ratio:low":
        "- play at least one beat as dialogue instead of summarising it",
    "opener_top_share:high":
        "- vary how sentences open; too many start on the same word",
    "filter_per_1k:high":
        "- cut the filter verbs (felt, saw, heard, noticed, realized, seemed) and let "
        "the thing happen on the page",
    "dash_per_1k:high":
        "- at most {dashes} em dashes in the whole passage",
    "ttr:low":
        "- widen the vocabulary: the same nouns and verbs keep coming back",
}


def _quota_block(failed: List[str], words: int) -> str:
    """Only the quotas for what actually failed, sized for this block."""
    approx_sentences = max(1, words // 16)
    numbers = {
        "adverbs": max(2, round(words * 0.012)),
        "short": max(2, round(approx_sentences * 0.2)),
        "long": max(1, round(approx_sentences * 0.08)),
        "oneline": 1,
        "dashes": max(1, words // 700),
    }
    lines = [QUOTAS[key].format(**numbers) for key in failed if key in QUOTAS]
    if not lines:
        lines = ["- cut the stock phrasing and the repetition listed below; change "
                 "nothing else"]
    lines.append("- at least one concrete particular already present in the passage - a "
                 "name, a number, an object, the weather. Invent nothing new")
    return "\n".join(lines)


def _rewrite(ai_service, text: str, issues: List[str], failed: Optional[List[str]] = None,
             log=print) -> str:
    """Rewrite block by block. A whole-chapter pass averages the quotas away."""
    issue_text = "\n".join(f"- {x}" for x in issues[:20]) or "- rhythm is too even"
    parts: List[str] = []
    for kind, block in _blocks(text):
        if kind == "head":
            parts.append(block)
            continue
        w = len(block.split())
        prompt = REWRITE_PROMPT.format(
            words=w,
            quotas=_quota_block(failed or [], w),
            issues=issue_text,
            text=block)
        try:
            rewritten = ai_service.generate_content(prompt, model_type="writing").strip()
            # A rewrite that loses a fifth of the block has dropped events, not
            # adjectives. Chapters are length-budgeted, so keep the original.
            if not rewritten or len(rewritten.split()) < w * 0.8:
                log("  · block rewrite came back short; keeping the original")
                rewritten = block
            parts.append(rewritten)
        except Exception as exc:  # noqa: BLE001 - keep the original block on failure
            log(f"  · block rewrite failed ({type(exc).__name__}); keeping the original")
            parts.append(block)
    return "\n\n".join(parts)


def humanize(ai_service, text: str, *, rounds: int = 2, threshold: float = 15.0,
             use_judges: bool = False, log=print) -> Tuple[str, Dict[str, Any]]:
    """Rewrite until every available signal reads below ``threshold``.

    Returns the best version seen, never a worse one than it was handed: a
    rewrite that scores higher than the draft is thrown away.
    """
    if rounds <= 0:
        return text, {"rounds": [], "passed": None, "final_score": None, "skipped": True}
    report: Dict[str, Any] = {"rounds": []}
    best, best_score = text, 1e9
    for i in range(1, rounds + 1):
        local = local_score(text)
        judges = llm_judges(ai_service, text, log=log) if use_judges else []
        probs = [local["score"]] + [float(j.get("ai_probability", 50)) for j in judges]
        worst = max(probs)
        report["rounds"].append({"round": i, "local": local["score"],
                                 "judges": judges, "worst": worst})
        log(f"[humanness] round {i}: local={local['score']} worst={worst:.1f} "
            f"(threshold {threshold})")
        if worst < best_score:
            best, best_score = text, worst
        if worst < threshold:
            report["passed"] = True
            report["final_score"] = worst
            return text, report
        if i == rounds:
            break
        issues = list(local["issues"])
        for j in judges:
            issues += [f"[{j['model']}] {s}" for s in (j.get("signals") or [])[:4]]
            issues += [f"[{j['model']}] tell: «{f}»"
                       for f in (j.get("suspect_lines") or [])[:3]]
        log(f"[humanness] rewriting in blocks ({len(issues)} signals, "
            f"{len(local['failed'])} quotas)…")
        text = _rewrite(ai_service, text, issues, local["failed"], log=log)
    report["passed"] = best_score < threshold
    report["final_score"] = best_score
    return best, report


def enabled() -> bool:
    """On by default once a baseline exists; ``AI_BOOK_HUMANIZE=0`` turns it off."""
    if os.getenv("AI_BOOK_HUMANIZE", "1") == "0":
        return False
    return BASELINE_PATH.exists()


def _demo() -> None:
    """Self-check: flat prose must score worse than varied prose."""
    flat = ("\n\n".join(
        ["She walked into the room and looked around at the empty chairs there. "
         "The light was dim and the air was thick with the smell of old paper. "
         "She felt a wave of unease as she considered what she would do next."] * 6))
    varied = ("She stopped.\n\n"
              "“You're late,” Marta said, not looking up from the ledger, and for a "
              "while neither of them said anything else, because the ledger was open at "
              "the March column and the March column was wrong by four hundred crowns, "
              "which they both knew and neither of them wanted to be the one to say.\n\n"
              "“I know.”\n\n"
              "Rain on the window. Somebody upstairs dropped a boot.\n\n"
              "“The Hensel account,” Marta said. “Again.”\n\n"
              "“It isn't the Hensel account.” Anna put her gloves on the desk, one "
              "on top of the other, squared. “It's Kraus. It has been Kraus since "
              "October and you have known that since October.”")
    assert local_score(flat)["score"] > local_score(varied)["score"], "flat prose must score worse"
    fp = fingerprint(varied)
    assert fp["dialogue_ratio"] > 0.5, fp
    assert fingerprint(flat)["burstiness"] < fp["burstiness"], "flat prose must be less bursty"
    assert repeated_phrases(flat), "verbatim repetition must be caught"
    assert _parse_json('```json\n{"ai_probability": 80}\n```') == {"ai_probability": 80}
    assert _parse_json('sure!\n{"ai_probability": 12}\nhope that helps') == {"ai_probability": 12}
    print("humanizer self-check ok")


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        _demo()
    else:
        for path in args:
            body = Path(path).read_text(encoding="utf-8", errors="replace")
            result = local_score(body)
            print(f"\n=== {path}: humanness score {result['score']} "
                  f"(0 = published novel, 100 = chatbot)")
            for issue in result["issues"]:
                print(f"  - {issue}")
            print(json.dumps(result["metrics"], indent=2))
