# AI Book Creator

AI Book Creator is a modular, AI-assisted book generation pipeline. It guides you through idea capture, book/series structure, chapter writing, review, and EPUB export, while keeping progress resumable through on-disk project state.

## What’s in the repo

```text
book writer/
├── main.py                  # CLI wrapper for the main book-creation flow
├── utils.py                 # CLI wrapper for project management commands
├── setup.py                 # Helper to create local config copies
├── requirements.txt         # Python dependencies
├── README.md                # This file
├── ai_book_creator/
│   ├── cli.py               # Main interactive CLI
│   ├── project_cli.py       # Project-management CLI
│   ├── env.py               # Loads local environment variables
│   ├── core/
│   │   ├── book_creator.py   # Orchestrates the end-to-end workflow
│   │   └── project_manager.py
│   ├── services/
│   │   └── ai_service.py     # Provider + budget management
│   ├── steps/
│   │   ├── step_0_init.py    # Idea / scope / layout setup
│   │   ├── step_1_structure.py
│   │   ├── step_2_write.py
│   │   ├── step_3_review.py
│   │   └── step_4_ebook.py
│   ├── utils/
│   │   ├── glossary_manager.py
│   │   ├── ebook_exporter.py
│   │   ├── text_utils.py
│   │   ├── name_generator.py
│   │   └── style_checks.py
│   ├── models/
│   │   └── chapter_model.py
│   └── config/
│       ├── ai_config_google.local.json
│       ├── ai_config_openai.local.json
│       └── ai_config_groq.local.json
├── tests/
└── book_output/
```

## Requirements

- Python 3.12+ recommended
- Node.js with `npx` (only for OpenAI OAuth)
- Install the packages in `requirements.txt`
- API access for whichever provider you want to use:
  - Google Gemini
  - OpenAI
  - OpenAI OAuth (local ChatGPT-account proxy)
  - Groq
  - MiniMax (MiniMax-M2.7)
  - OpenRouter
  - OpenCode Go
  - Claude Code CLI (`claude`) — runs on your Claude subscription, no API key
  - hyper.charm.land (`hyper`) — OpenAI-compatible, `HYPER_API_KEY`

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run `python main.py` and choose `openai-oauth`. The app starts the loopback-only
proxy automatically; on first use, `npx` may ask to download it and a browser
may open for sign-in. No OpenAI API key is required. The proxy reuses the local
Codex credentials in `~/.codex`. The model menu is loaded live from the proxy;
its `/v1/models` endpoint currently does not report context or output limits.

For OpenCode Go, run `opencode auth login` first, then choose `opencode-go`.
The app reads the current credentials and model limits from OpenCode's local files.

OpenCode Zen's free models (`deepseek-v4-flash-free`, `mimo-v2.5-free`, etc.) are
available through the `opencode-zen` provider. No subscription is needed; the same
OpenCode account key (from `opencode auth login`) is reused automatically.

For `claude`, install the Claude Code CLI and sign in (`claude` on the command
line). The provider shells out to `claude -p` with the prompt on stdin, so
generation is billed to your subscription rather than to a metered key. Model
choice is `opus` / `sonnet` / `haiku`, or any full model id.

For `hyper`, put `HYPER_API_KEY=...` in `.env`. It is an OpenAI-compatible
endpoint carrying several model families, which is what makes the humanness
judges useful — a model from another family is the only one whose verdict on
machine-written prose means anything.

The repository already includes local provider config files in `ai_book_creator/config/`. Edit the `.local.json` file for the provider you want to use.

If you want to refresh or recreate local config files, run:

```bash
python setup.py
```

## Configuration

The active config file is controlled by `AI_CONFIG_PATH`.

By default, the app now uses the committed local Google config:

- `ai_book_creator/config/ai_config_google.local.json`

You can also switch providers by setting `AI_CONFIG_PATH` to one of the other local presets:

- `ai_book_creator/config/ai_config_openai.local.json`
- `ai_book_creator/config/ai_config_openai_oauth.json`
- `ai_book_creator/config/ai_config_groq.local.json`
- `ai_book_creator/config/ai_config_minimax.local.json`
- `ai_book_creator/config/ai_config_openrouter.json`
- `ai_book_creator/config/ai_config_opencode_go.json`
- `ai_book_creator/config/ai_config_opencode_zen.json` (free Zen models)
- `ai_book_creator/config/ai_config_claude.json` (Claude Code CLI)
- `ai_book_creator/config/ai_config_hyper.json` (hyper.charm.land)

## Humanness

Chapters are scored against prose ranges measured on 48 published English
novels (`ai_book_creator/config/prose_baseline.json`) and rewritten block by
block until they read like one. Rebuild the baseline from your own Calibre
library, or benchmark real novels against your generated chapters:

```bash
python benchmarks/build_prose_baseline.py --books 60   # rebuild the baseline
python benchmarks/measure_chapter_shape.py             # chapter-scale ranges
python benchmarks/build_prose_baseline.py --bench      # novels vs your drafts
python -m ai_book_creator.utils.humanizer book_output/chapter_01.txt
```

Measured on 2026-08-22 over 577 published chapters: real chapters score a median
of 1.5, this pipeline's chapters 25.5. Each chapter is written to its own tempo,
drawn from the corpus distribution rather than to one book-wide target, and the
rewrite pass only asks for the specific things a chapter measurably lacks. Step
3 reports whether the finished book varies chapter to chapter as much as a real
novel does. `AI_BOOK_HUMANIZE=0` turns the whole thing off, and `judge_models`
in the provider config adds cross-family LLM judges on top of the stylometry.

Useful environment variables:

- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `GROQ_API_KEY`
- `MINIMAX_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENCODE_GO_API_KEY`
- `AI_CONFIG_PATH`
- `AI_USAGE_STATE_PATH`
- `AI_GROQ_RATE_STATE_PATH`
- `AI_WRITING_MODEL`
- `AI_REVIEW_MODEL`
- `AI_BASE_URL`

## Running the app

Launch the main workflow from the repository root:

```bash
python main.py
```

For project-management commands:

```bash
python utils.py list
python utils.py status book_output
python utils.py export-ebook book_output
python utils.py export-glossary book_output
python utils.py backup book_output
python utils.py clean book_output
python utils.py repair book_output
```

You can also run the package modules directly:

```bash
PYTHONPATH=. python -m ai_book_creator.cli
PYTHONPATH=. python -m ai_book_creator.project_cli list
```

## Workflow overview

1. Step 0: Initialize the project scope, concept, page count, and initial layout
2. Step 1: Build chapter structure and chapter plots
3. Step 2: Write chapters to `book_output/`
4. Step 3: Review and expand the manuscript if needed
5. Step 4: Export the final EPUB
6. Step 5: Create the cover and KDP upload package

The pipeline supports:

- Single-book or series generation
- Resumable project state
- Glossary tracking
- Chapter checkpoints
- Budget-aware pausing and resuming
- EPUB export with a generated back-cover description
- Review mode with idea, layout, structure, and between-book approval
- Automatic idea pitching/selection with randomized page and chapter ranges
- KDP-sized JPEG covers with reliable title and author typography
- KDP metadata, AI-disclosure fields, and an upload checklist

## Review and automatic modes

The normal reviewed flow remains interactive:

```bash
python main.py --mode review --author "Your Name"
```

For an automatic generation run, provide a configured text provider. Pollinations
is the default cover source and downloads the generated background directly,
without Chrome or an API key:

```bash
python main.py --mode auto --provider openai --author "Your Name" --pages 180-280 --chapters 12-20 --fresh
```

For a series, add `--series 3`. Automatic mode pitches several concepts and plot
directions to the AI, has it select and improve one, randomizes the book length
inside the supplied ranges, and continues through every book.

To keep creating unrelated books until you press Ctrl+C or the provider stops
accepting requests, add `--continuous`:

```bash
python main.py --mode auto --provider openai-oauth --author "Your Name" --pages 180-280 --chapters 12-20 --continuous --fresh
```

Each completed KDP package is archived under `book_output/archive/ebooks/`
before the next book starts. If a run is interrupted or rejected by the
provider, its current progress remains resumable.

The equivalent explicit option is:

```bash
python main.py --mode review --cover-source pollinations
```

Pollinations currently supports keyless image requests. Set `POLLINATIONS_API_KEY`
only if you want to use its authenticated API. Perchance remains available as a
browser-based fallback; its saved profile is under `book_output/browser_profile/`,
and any Cloudflare or CAPTCHA verification must be completed manually:

```bash
python main.py --mode review --cover-source perchance
```

You can also generate or choose a background yourself and supply it up front:

```bash
python main.py --mode review --cover-background "C:\covers\background.jpg"
```

To upload, preview, price at $0.99, and submit the completed eBook through KDP:

```bash
python main.py --mode auto --provider openai-oauth --publish-kdp --fresh
```

KDP publishing is headless by default and reuses `AI_BOOK_KDP_PROFILE`. For the
first login or troubleshooting, add `--kdp-visible`; the authenticated profile
is reused by later headless runs. The automation records its current KDP page in
the `_kdp.json` package so an interrupted run resumes the same draft.

The submitted defaults are worldwide rights, DRM on, KDP Select off, 35%
royalty, and the lowest US list price ($0.99). Generated category paths and AI
tool disclosures remain in the package for inspection.

## Output files

Generated artifacts live in `book_output/`:

- `project_data.json` — full project state
- `ai_usage_state.json` — provider usage / budget tracking
- `groq_usage_state.json` — Groq rate tracking
- `glossary.json` — glossary database
- `book_glossary.txt` — human-readable glossary export
- `book_analysis.txt` — review output
- `chapter_XX.txt` — generated chapters
- `checkpoint_*.json` — chapter/structure checkpoints
- `ebook/<title>.epub` — exported EPUB
- `ebook/<title>_cover_prompt.txt` — cover prompt text
- `ebook/<title>_cover.jpg` — 1600×2560 KDP cover, when a background is available
- `ebook/<title>_kdp.json` — title metadata, keywords, categories, files, and AI disclosure
- `ebook/<title>_KDP_CHECKLIST.txt` — KDP upload settings and fallback checklist

## Tests

Run the test suite with:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
PYTHONPATH=. python3 -m unittest test_publish_pipeline -v
```

## Notes

- The repo’s `__init__.py` only exposes the main public classes now; importing `ai_book_creator` no longer eagerly imports every step module.
- `chapter_model.py` includes a lightweight fallback when `pydantic` is unavailable, which keeps the test suite importable in minimal environments.
- `setup.py` is mainly a convenience script for local config management; the committed `.local.json` files are already usable as-is.
