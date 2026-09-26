# AI Book Creator (book writer)

## What This Is
AI book generation pipeline: idea → structure → chapters → review → EPUB, resumable via on-disk project state.

## Non-Negotiables
- Never log or commit API keys / OAuth credentials (`.env`, `~/.codex`).
- Progress lives in project state on disk — resumable, don't restart from scratch.
- Books are published under the pen name in `AI_BOOK_AUTHOR` ("Li Wen"), never the
  user's real name. The EPUB disclaimer and KDP `ai_tools` credit every model in
  `book_output/models_used.json`, which `AIService` appends to on each reply
  (`AI_MODELS_USED_PATH`, set by `cli.main`; archived per series book).
- **The humanness baseline is measured, not invented.** Every bound the scorer
  gates on is a percentile of real published fiction in
  `ai_book_creator/config/prose_baseline.json`. Change a number there only by
  re-running `benchmarks/build_prose_baseline.py`, never by taste.
- Prefer a comparison model from another family where the endpoint supports it.
  Otherwise use the review model. LLM preferences require reader calibration;
  they are not proof of authorship or literary quality.

## Commands
- Run: `python main.py` (`[idea words]`, `--auto`, `--forever`, `--publish`/`--publish-github`, `--resume`; see README)
- Test: `pytest`
- Deps: `pip install -r requirements.txt`
- Humanness of one file: `python -m ai_book_creator.utils.humanizer book_output/chapter_01.txt`
- Rebuild the prose baseline: `python benchmarks/build_prose_baseline.py --books 60`
- Re-measure chapter lengths: `python benchmarks/measure_chapter_shape.py`
- Novels vs. own drafts: `python benchmarks/build_prose_baseline.py --bench`
- Phrases drafts overuse vs. cached novels: `python benchmarks/build_prose_baseline.py --tells`
  (candidates for `humanizer.LLM_TELLS`; read the list, a book's own terms show up too)

## AI suite
AIService, the provider/model menu (`choose_ai`, `provider_config_path`), provider configs
and provider API keys live in the sibling `ai-suite` package (see its CLAUDE.md). This repo
carries a vendored `ai_suite/` copy for fresh clones -- never edit it here; change `ai-suite`
and run its `sync.py`. `ai_book_creator/__init__.py` puts the sibling checkout first on
`sys.path`; `ai_book_creator.services.ai_service` and `ai_book_creator.env` alias the suite
modules and `cli` re-exports `choose_ai`/`provider_config_path` for older importers
(article-writer). Book writer passes its own `book_output/provider_state.json` to the menu.
Tests that run `cli.run` must stub `cli.choose_ai`, or they write the real state file.

## Editorial pipeline

- Approved names remain fixed. Never rename characters after generating the layout.
- Step 0 requests a voice brief; Step 1 plans scene purpose and scales those
  scene-based length estimates to the total. Do not restore randomized tempo,
  shuffled chapter budgets, or automatic page-count padding.
- Step 2 supplies the approved layout, glossary, rolling continuity record, and
  the preceding ending. Before the first chapter it derives a voice bible from the
  layout (`editorial.voice_bible`: per-character voice cards and named world texture),
  cached as `init.voice_bible`; `story_context` hands it to every later prompt.
  The draft's repair pass checks `editorial.REVISION_CHECKS` (LAMP editor categories
  plus measured habits such as narration by negation and people known only by role),
  plus dialogue lines `editorial.indistinct_lines` could not attribute to their speaker
  from wording and voice cards alone (two review calls per chapter, skipped without
  voice cards or with under six lines).
- GitHub publishing (`utils/github_publisher.py`) refuses any repo owned by the
  logged-in personal account: the pen-name rule covers the host account too. Continuity extraction reads every passage and records
  character knowledge, chronology, motivation and unresolved threads.
- `utils/editorial.py` applies exact nonoverlapping edits. All unselected prose
  remains intact. Headings, scene breaks and length bounds are checked locally;
  semantic preservation and editorial preference are checked in both A/B orders.
- Style statistics only trigger passage inspection. They never decide which
  version is better. Do not add adverb/dialogue quotas or authorship probabilities.
- Step 3 reads every chapter and the full manuscript in consecutive passages.
  It makes one bounded correction pass on quoted findings and rechecks the
  changed manuscript. Unresolved issues remain in the analysis report.
- First drafts and prior versions are retained. Use `save_text` for manuscript
  updates; it backs up the current version and replaces the live file atomically.
- Persist source hashes and continuity context hashes so edited chapters and
  interrupted runs cannot silently reuse stale review results.
- Provider truncation is an error. Never silently trim the end of manuscript
  context to retry an oversized prompt.

## Evaluation and reference corpus

The shipped `prose_baseline.json` retains the legacy measured ranges. Its score
describes style outliers in that corpus, not human authorship or writing quality.
Rebuild only from real corpus data, never by changing thresholds to fit a draft.

Future rebuilds select fiction using Calibre tags, reserve books by title hash
for evaluation, and regenerate chapter and window tables together. Correct the
library metadata first. Chapter measurements use only the selected calibration
titles, not every cached book.

`benchmarks/evaluate_prose.py` produces a blind reading pack and summarizes
human preferences separately from content loss. The fixed synthetic examples in
`benchmarks/prose_cases.json` exercise the workflow; actual quality claims require
reader comparisons on generated manuscripts.

Run `python -B -m unittest discover -s tests -q` and
`python -B -m unittest test_publish_pipeline -q`. The editorial tests and
benchmark sources are explicitly included in Git; corpus text and other local
tests remain ignored.
