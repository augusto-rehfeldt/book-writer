# AI Book Creator v2.0 - Modular Edition

A sophisticated, modular system for generating complete books using AI, now with comprehensive glossary management and improved resumability.

## Features

### Core Functionality
- **Modular Architecture**: Each step is now a separate, focused module
- **Full Resumability**: Interrupt and resume at any point
- **Comprehensive Glossary**: Automatic tracking of characters, locations, concepts, and timeline
- **Error Handling**: Robust retry mechanisms and error recovery
- **Progress Tracking**: Detailed checkpoints and status monitoring

### Glossary Management
- **Character Tracking**: Names, descriptions, roles, relationships, first appearances
- **Location Database**: Settings, significance, regional information
- **Concept Registry**: Important themes, objects, systems, and plot devices
- **Timeline Events**: Key plot points with chapter references
- **Relationship Mapping**: Character connections and dynamics
- **Automated Extraction**: AI-powered identification of glossary items
- **Context Integration**: Glossary information used during writing

## Project Structure

```
ai_book_creator/
├── cli.py                      # Interactive book creation CLI
├── project_cli.py              # Project management CLI
├── __init__.py
├── core/
│   ├── book_creator.py        # Main orchestrator
│   └── project_manager.py     # Project state management
├── services/
│   └── ai_service.py          # AI API communication
├── steps/
│   ├── __init__.py
│   ├── base_step.py           # Base step class
│   ├── step_0_init.py         # Book concept gathering
│   ├── step_1_structure.py    # Chapter structure
│   ├── step_2_write.py        # Full chapter writing
│   ├── step_3_review.py       # Final consistency check
│   └── step_4_ebook.py        # EPUB export
└── utils/
    ├── glossary_manager.py    # Glossary tracking system
    └── text_utils.py          # Word/page utilities

main.py                        # Thin wrapper around ai_book_creator.cli
utils.py                       # Thin wrapper around ai_book_creator.project_cli
requirements.txt              # Dependencies
README.md                     # This file
```

## Installation

1. Clone or download the project files
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up your local configurations and limits by running the setup script:
   ```bash
   python setup.py
   ```
   - This generates local .json configuration files inside ai_book_creator/config/ which are safely ignored by git. You can modify these to update your own daily usage limits, models, and constraints without impacting the codebase.
4. Set your API key securely with environment variables:
   - `OPENAI_API_KEY` for OpenAI
   - `GROQ_API_KEY` for Groq
   - `GOOGLE_API_KEY` for Gemini

   A sample `.env.example` is included for local setups. The committed config files are safe templates; keep any private overrides in a `*.local.json` file so Git ignores them.

### Groq Mode
Groq is supported through the same OpenAI-compatible client path:

```powershell
$env:AI_CONFIG_PATH="ai_book_creator/config/ai_config_groq.json"
$env:GROQ_API_KEY="your-groq-key"
python main.py
```

The Groq preset uses `groq/compound` and rate-limits itself to Groq's published limits: 70k TPM, 30 RPM, and 250 RPD.

If you want local-only changes to the model or timeout settings, copy a preset to a file such as `ai_book_creator/config/ai_config_groq.local.json` and edit that copy. Files matching `*.local.json` are ignored by Git.

## Usage

### Basic Usage
```bash
python main.py
```

### Project management CLI

The top-level [`utils.py`](utils.py:1) is a thin wrapper around `ai_book_creator.project_cli` and provides a lightweight CLI for managing projects (list, status, backup, export-glossary, export-ebook, clean). Run it from the repository root:

```bash
python utils.py list
python utils.py status <project_dir>
python utils.py export-ebook <project_dir>
```

If your environment does not include the repo root on `PYTHONPATH`, you can run it with `PYTHONPATH` set explicitly:

```bash
PYTHONPATH=. python utils.py list
```

### Resuming a Project
The system automatically detects and resumes existing projects. All progress is saved in `book_output/project_data.json`.

### Output Files
- **Chapter Files**: `book_output/chapter_XX.txt` - Individual chapter files
- **Project Data**: `book_output/project_data.json` - Complete project state
- **Glossary**: `book_output/glossary.json` - Detailed glossary database
- **Final Glossary**: `book_output/book_glossary.txt` - Human-readable glossary
- **Analysis**: `book_output/book_summary_and_analysis.txt` - Final book analysis
- **Ebook**: `book_output/ebook/<title>.epub` - Final EPUB export
- **Cover Prompt**: `book_output/ebook/<title>_cover_prompt.txt` - Image prompt for a cover generator

## Step-by-Step Process

### Step 0: Initialization
- Choose whether you want a single book or a series
- Collect the book idea and any high-level constraints
- Generate a series layout when needed and show an estimated token budget
- Establish the starting project state and output folder

### Step 1: Structure
- Build the book layout and chapter structure
- Generate the chapter plan used for writing
- Save the structure so you can resume later

### Step 2: Writing
- Draft the full chapter text
- Track glossary entries while chapters are generated
- Write output into the `book_output/` project folder

### Step 3: Review
- Run a consistency and quality review
- Cache the analysis and final summary
- Mark the project complete or leave it ready for another pass

### Step 4: Ebook Export
- Build an EPUB from the generated chapters
- Preserve markdown formatting in the exported ebook
- Save the ebook in `book_output/ebook/`

### Optional Repair
- Use `python utils.py repair book_output` to clear later steps if the saved project becomes inconsistent
- Use `python utils.py clean book_output` to remove temporary checkpoint files

## Glossary System

### Automatic Tracking
The glossary system automatically identifies and tracks:
- **Characters**: Protagonists, antagonists, supporting characters
- **Locations**: Settings, regions, significant places
- **Concepts**: Themes, magical systems, important objects
- **Timeline**: Major plot events with chapter references
- **Relationships**: Character connections and dynamics

### Manual Management
You can manually add entries using the GlossaryManager:
```python
glossary.add_character("John Doe", "Main protagonist, brave knight", "protagonist")
glossary.add_location("Castle Blackstone", "Ancient fortress", "Capital Region")
glossary.add_concept("Dragon Magic", "Elemental magic system", "magic")
```

### Context Integration
Glossary information is automatically provided to the AI during writing to ensure consistency.

## Error Handling

### Automatic Recovery
- Exponential backoff for API rate limits
- Token limit detection and adjustment
- Empty response handling
- Network error recovery
- Daily token budget pauses that cache partial work before stopping

### Manual Recovery
- Checkpoint system for partial progress
- Step-by-step resumability
- Failed chapter retry mechanisms

## Customization

### Adding New Steps
1. Inherit from `BaseStep`
2. Implement the step logic in `ai_book_creator/steps/`
3. Register the step in `ai_book_creator/core/book_creator.py`

### Modifying AI Prompts
Each step contains its own prompt templates that can be customized for different genres or styles.

### Extending Glossary
The glossary system is extensible - add new categories or data fields as needed.

## Troubleshooting

### Common Issues
- **API Key**: Ensure your API key is valid
- **Token Limits**: The system auto-adjusts, but very long prompts may need manual reduction
- **File Permissions**: Ensure write access to the output directory
- **Budget Pause**: If the active provider hits 95% of its configured daily set maximum tokens (measuring both prompt input and completion output tokens), the app gracefully saves progress and pauses until you choose to continue or limits reset. You can easily modify your limits in the `ai_book_creator/config/*.local.json` config files.

### Resume After Interruption
Simply run the script again - it will automatically detect and resume from the last completed step.

### Glossary Issues
Check `book_output/glossary.json` for any data corruption. The file can be manually edited if needed.

## Version History

### v2.0.0 (Current)
- Complete modular refactor
- Added comprehensive glossary system
- Improved resumability
- Better error handling
- Enhanced project management

### v1.0.0 (Original)
- Monolithic book creation system
- Basic resumability
- Simple error handling

## License

This project is open source. Modify and distribute as needed.

## Support

For issues or questions, review the error messages and troubleshooting section. The system provides detailed error information to help diagnose problems.
