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
- Install the packages in `requirements.txt`
- API access for whichever provider you want to use:
  - Google Gemini
  - OpenAI
  - Groq

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

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
- `ai_book_creator/config/ai_config_groq.local.json`

Useful environment variables:

- `GOOGLE_API_KEY`
- `OPENAI_API_KEY`
- `GROQ_API_KEY`
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

The pipeline supports:

- Single-book or series generation
- Resumable project state
- Glossary tracking
- Chapter checkpoints
- Budget-aware pausing and resuming
- EPUB export with a generated back-cover description

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

## Tests

Run the test suite with:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

## Notes

- The repo’s `__init__.py` only exposes the main public classes now; importing `ai_book_creator` no longer eagerly imports every step module.
- `chapter_model.py` includes a lightweight fallback when `pydantic` is unavailable, which keeps the test suite importable in minimal environments.
- `setup.py` is mainly a convenience script for local config management; the committed `.local.json` files are already usable as-is.
