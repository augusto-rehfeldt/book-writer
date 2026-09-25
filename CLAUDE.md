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
At menu time `cli._live_model_ids` asks the provider what it serves
(`/models`, or `cmdc --list-models`) and hides curated ids it has retired;
served ids missing from the config join the menu when models.dev reports their
output limit. Offline, the curated list shows as is.
`cli.choose_ai` is the one provider/model menu for every AIService script:
mathforge and music writer call it with their own state file and roles.
opencode.ai rejects calls without an `x-opencode-session` header;
`AIService._extra_headers` adds one per service.
Menus print context, max output and list price per 1M tokens from models.dev
(`cli._model_facts`; opencode's `~/.cache/opencode/models.json` when under a day
old, else live). Display only: the config's `max_output` is still the cap sent.
With `ARTIFICIAL_ANALYSIS_API_KEY` set, menus also show the Artificial Analysis
Intelligence Index (`cli._intelligence`, cached a day in
`~/.cache/ai-book-creator/artificial_analysis.json`). Models are sorted by price.
Context, output, price and AA are colored green/yellow/red in `_facts_label`.

- `claude` — the Claude Code CLI in print mode, on the user's subscription, no
  key. The prompt goes in **on stdin, never in argv**: Windows caps a command
  line at 32k characters and a chapter prompt blows past it. Its catalogue
  carries `max_output: 0` because the CLI takes no completion cap, and the CLI
  branch in `run()` unsets `AI_*_COMPLETION_TOKENS` when it sees a 0.
- `commandcode` — same CLI pattern as `claude` but through the Command Code CLI
  in headless mode (`cmdc -p --output-format text --model ID`, prompt on stdin).
  `cmd` is deliberately avoided: on Windows that is the system shell. Runs on
  the user's Command Code subscription, no key; catalogue model ids must be
  written lowercase (the model menu lowercases picks, and Command Code ids are
  case-sensitive).
- `opencode-zen` — config provider `opencode`: the OpenCode CLI
  (`opencode run --agent plan --format json -m opencode/ID`, prompt on stdin).
  opencode.ai's free tier answers only OpenCode itself: direct API calls get
  403 FreeTierError, and so does the CLI with a custom agent. The stock `plan`
  agent is read-only; `build` could edit files. A 403 is never retried.
- `hyper` — hyper.charm.land, OpenAI-compatible (`HYPER_API_KEY`). Send it
  `max_tokens`, not `max_completion_tokens`; the newer spelling is a 400.
- `grok` — xAI's own API, OpenAI-compatible (`XAI_API_KEY`, `api.x.ai/v1`).
  Takes the older `max_tokens` spelling like hyper. Its `judge_models` are
  grok models because that is all the endpoint serves — same-family judges,
  so treat their humanness verdicts as weak signal there.
- Every request asks for the model's whole output allowance: the config's
  `models[<id>].max_output` (`_max_output`) when the catalogue lists the model,
  else the larger of the caller's `max_completion_tokens` and the role default.
  A caller's cap is a floor, never a ceiling -- a cap only truncates, and a
  truncated reply costs a whole retry. Retries after truncation double the cap
  but never past `max_output`.
- `generate_content(..., model=...)` overrides the role default. That parameter
  exists for the judges and nothing else.

## Editorial pipeline

- Approved names remain fixed. Never rename characters after generating the layout.
- Step 0 requests a voice brief; Step 1 plans scene purpose and scales those
  scene-based length estimates to the total. Do not restore randomized tempo,
  shuffled chapter budgets, or automatic page-count padding.
- Step 2 supplies the approved layout, glossary, rolling continuity record, and
  the preceding ending. Continuity extraction reads every passage and records
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

## Shared service contract

This AIService is the workspace's one AI suite. Consumers: music writer, mathforge,
bandido, impostor, book-watch, lamplight, the calibre summarizer and Story Atlas (see
the workspace README's contract section). Expose options on AIService, not
private-method or SDK monkeypatches, and add them test-first in
`tests/test_shared_consumers.py` / `tests/test_shared_portable.py`. Public:
`allow_auth_prompt`, `client_max_retries`, `config_overrides` (merged over the file,
explicit `api_key` wins; config-only when no file, then `provider` is required),
`cli.provider_config_path`, `set_reasoning_effort(writing, review)`,
`generate_content(system=, temperature=, wait_for_limits=)`, `embed()` and `last_usage`.
Config keys `stream` and `token_param` are consumer-facing too.

The module must import on the standard library alone: the calibre plugin packs this
file into its zip and Calibre's Python has no `requests` or SDKs (`_StdlibSession`).
The CLI transports (`claude_chat`, `commandcode_chat`) run from a neutral directory;
Claude Code also gets `--safe-mode --tools "" --system-prompt CLI_NEUTRAL_SYSTEM`, because a
CLI otherwise reads project/global CLAUDE.md and plugin rules into the reply. Keys never
cross providers (`_resolve_api_key`): overrides with their own endpoint get only their own
key, and a config naming `api_key_env` uses only that variable; the legacy cross-provider
fallback remains only for configs that name none. Override `base_url`, `writing_model`
and `review_model` beat the `AI_*` environment variables; `cap_is_ceiling` sends a
caller's cap as given. Metered requests sharing a
ledger serialize under an OS lock; atomic writes and UsageStateError prevent silent
accounting resets/retries. Run every consumer's workspace check together (command in the workspace AGENTS.md).

`generate_content` never returns a provider's usage-limit notice (`LIMIT_NOTICE_RE`;
the Claude Code CLI prints "You've hit your session limit" as its reply) and waits
out errors matching `LIMIT_ERROR_RE`: until the reset the notice names
(`limit_reset_wait`), else `LIMIT_RETRY` seconds with a 5-hour `LIMIT_PAUSE` every
`LIMIT_TRIES`th try, indefinitely. The loop sits outside `@accounted` (the body is
`_generate_content_once`) so a wait never holds the ledger lock. Metered budget
stops (`UsageLimitExceeded`, `DailyTokenBudgetExceeded`, `UsageStateError`) still raise.
