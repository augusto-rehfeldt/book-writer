# AI Book Creator (book writer)

## What This Is
AI book generation pipeline: idea → structure → chapters → review → EPUB, resumable via on-disk project state.

## Non-Negotiables
- Never log or commit API keys / OAuth credentials (`.env`, `~/.codex`).
- Progress lives in project state on disk — resumable, don't restart from scratch.
- **The humanness baseline is measured, not invented.** Every bound the scorer
  gates on is a percentile of real published fiction in
  `ai_book_creator/config/prose_baseline.json`. Change a number there only by
  re-running `benchmarks/build_prose_baseline.py`, never by taste.
- **Judges must be another family.** `judge_models` may not share a family with
  the writing or review model: a text graded by the model that wrote it measures
  nothing. The `claude` provider serves only its own catalogue, so a Claude-only
  run has no judges at all and falls back to stylometry.

## Commands
- Run: `python main.py`
- Test: `pytest`
- Deps: `pip install -r requirements.txt`
- Humanness of one file: `python -m ai_book_creator.utils.humanizer book_output/chapter_01.txt`
- Rebuild the prose baseline: `python benchmarks/build_prose_baseline.py --books 60`
- Re-measure chapter lengths: `python benchmarks/measure_chapter_shape.py`
- Novels vs. own drafts: `python benchmarks/build_prose_baseline.py --bench`

## Providers
`cli.PROVIDER_CONFIG_MAP` maps a provider name to its config file; anything in
`cli.CATALOGUE_PROVIDERS` also picks a model from that config's `models` block
and remembers it in `book_output/provider_state.json`.

- `claude` — the Claude Code CLI in print mode, on the user's subscription, no
  key. The prompt goes in **on stdin, never in argv**: Windows caps a command
  line at 32k characters and a chapter prompt blows past it. Its catalogue
  carries `max_output: 0` because the CLI takes no completion cap, and the CLI
  branch in `run()` unsets `AI_*_COMPLETION_TOKENS` when it sees a 0.
- `hyper` — hyper.charm.land, OpenAI-compatible (`HYPER_API_KEY`). Send it
  `max_tokens`, not `max_completion_tokens`; the newer spelling is a 400.
- `generate_content(..., model=...)` overrides the role default. That parameter
  exists for the judges and nothing else.

## Humanness (measured 2026-08-22 — do not re-derive)
Reference corpus: 48 English novels from the user's Calibre library, converted
with Calibre's own `ebook-convert`. Two tables, both in `prose_baseline.json`:
`ranges` (4,036 windows of 1,200 words) and `chapter_ranges` (577 whole
chapters). **A text is scored against the table for its own size** —
`scale_ranges()` switches at 1,500 words. A chapter of unbroken dialogue and a
chapter with none are both ordinary; no 1,200-word window looks like either, so
window percentiles used on a chapter fail prose that is perfectly human.

| | published chapters | this pipeline's chapters |
|---|---|---|
| `local_score` | median 1.5, p90 19, max 52 | median 25.5 (12–42) |
| `ly_per_1k` | 9.1 – 24.2 (median 14.9) | 1.7 – 6.4 |
| `short_sent_ratio` | 0.17 – 0.55 | 0.58 – 0.74 |
| `sent_len_mean` | 8.6 – 16.6 (median 11.5) | 6.4 – 8.9 |
| `semicolon_per_1k` | median 0.65 | ~0.0 |

- **The scrubbed-adverb register is the loudest tell.** A model applying "cut
  the adverbs" at full strength lands at 4 per 1,000 words; real novelists write
  ~14. It is the one metric where the two populations do not overlap at all,
  which is why `CHECKS` weights it hardest.
- Second loudest: relentless staccato. Drafts run a 7-word average sentence and
  60-74% of sentences under 8 words; novels sit near 11.5 and 40%.
- Do **not** penalise semicolons or long sentences — published fiction uses
  both and the drafts already avoid them.
- `ttr` is mean segmental TTR (`_msttr`), not plain TTR: plain TTR falls with
  length, so a 3,000-word chapter would always lose to a 1,200-word reference
  window no matter who wrote it.
- Threshold 15 flags 14% of *published* chapters and 92% of drafts. That trade
  is deliberate and the reason a rewrite must never be able to make things
  worse: `humanize()` returns the best version it saw.
- **Checks that fire on real prose as often as on drafts were removed or
  repriced.** A twice-repeated 6-gram appears in 59% of published chapters
  (77% of drafts) and was costing 16 points, so `local_score` calls
  `repeated_phrases(times=3)`. 36% of published chapters carry at least one
  `LLM_TELLS` phrase, so tells are priced by `tell_chapter_rate`: 1.5 points if
  more than 1% of real chapters use it, 5 if they never do.
- `benchmarks/corpus/` caches extracted book text so a rebuild costs no
  conversions. `is_fiction()` needs a 0.15 dialogue floor: military histories
  quote documents often enough to clear anything lower, and two of them in the
  sample drag the sentence-length percentiles toward essay prose.

### Variation, not compliance
One target for every chapter is itself the tell. Published books vary chapter to
chapter with a coefficient of variation of 0.13–0.19 on rhythm; this pipeline
managed 0.08 (`within_book_cv` in the baseline).

- `humanizer.chapter_lane(seed)` draws a pace and a dialogue level per chapter
  from `chapter_ranges` and states them **as prose, not as a quota**, clamped to
  p05–p95 so a lane can never aim at something the scorer would then fail.
  `step_2._chapter_register()` seeds it with `zlib.crc32` of number + title, not
  `hash()`, which is salted per process and would re-roll on resume.
- `_rewrite` sends **only the quotas for the checks that actually failed**
  (`local_score()["failed"]` → `QUOTAS`). The old prompt asked every passage for
  more short sentences *and* penalised staccato in the same breath; a passage
  now gets torn up only for the problem it has.
- `humanizer.book_report()` checks the whole book's chapter-to-chapter spread
  against `within_book_cv` and only reports (step 3 prints it and writes it into
  `book_analysis.txt`). Every chapter can be inside the human range and the book
  still read as machine-written.
- Measured end to end on chapter_02 through the Claude CLI: 41.7 → 12.0 in one
  round, `ly_per_1k` 4.1 → 14.7, `sent_len_mean` 6.4 → 8.9, length 2,508 →
  2,588 words.

## Chapter length (measured 2026-08-22 — do not re-derive)
591 chapters from 22 corpus novels, split on their own headings
(`benchmarks/measure_chapter_shape.py`, cached in `prose_baseline.json` under
`chapter_shape`). Ratio of a chapter to its book's median:

| p02 | p05 | p25 | p50 | p75 | p95 | p98 |
|---|---|---|---|---|---|---|
| 0.27 | 0.40 | 0.81 | 1.00 | 1.18 | 1.84 | 2.22 |

- 8% of published chapters are under half their book's median, and the median
  book's shortest chapter is 0.34x. A pipeline that asks every chapter for
  target±10% is the tell.
- `step_1_structure.chapter_budget()` samples that quantile curve once per
  equal-probability stratum, then shuffles: even a 12-chapter book gets both
  tails. Seeded on the book idea, so a resumed run rebuilds the same budgets.
- **The budget overrides whatever the model wrote.** The prompt hands the model
  the list so chapter *scope* matches the length, but `execute()` reassigns
  `word_count_estimate` by index — models copy a 28-number list badly, and the
  sum is what keeps the book on its page target.
- Nothing downstream may floor a chapter back to the book average: step 2 asks
  for `word_count_estimate` ±15%, and step 3 expands whichever chapter is
  furthest under *its own* budget, not the shortest one on disk.

## Gotchas
- `humanize()` returns the **best** version it saw, never merely the last, and
  `_rewrite` keeps the original block whenever a rewrite comes back under 80% of
  its length — a short rewrite has dropped events, not adjectives, and chapters
  are length-budgeted.
- The humanness pass is skipped entirely when `prose_baseline.json` is missing
  or `AI_BOOK_HUMANIZE=0`.
- `tests/` and `benchmarks/` are in `.gitignore` — they exist on disk only.
