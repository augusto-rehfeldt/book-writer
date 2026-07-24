#!/usr/bin/env python3
"""Drive book-writer to produce ONLY chapter 1, using the opencode-go model (glm-5.2).

Reuses the real book-writer steps (InitStep, StructureStep, WriteStep) and AIService,
configured for the opencode-go subscription. Non-interactive: all input() prompts are
scripted, and StructureStep is capped so only chapter 1 gets a plot + written text.
"""
# ponytail: env must be set before importing ai_book_creator (it loads .env via setdefault).
import builtins
import glob
import json
import os
import sys
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BASE_CONFIG_PATH = REPO_ROOT / "ai_book_creator" / "config" / "ai_config_opencode_go.json"
LOCAL_CONFIG_PATH = BASE_CONFIG_PATH.with_name("ai_config_opencode_go.local.json")
CONFIG_PATH = str(LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else BASE_CONFIG_PATH)
OUTPUT_DIR = REPO_ROOT / "book_output" / "chapter1_run"

BOOK_IDEA = (
    "A psychological horror / existential novel. A medical procedure is carried out in "
    "secrecy inside an old mansion, involving consciousness and the brain. The narrator "
    "is a spectator there. During the procedure something goes wrong and the narrator slips "
    "into a dissociative state of consciousness, somehow caused by the procedure. They are "
    "transported to a parallel version of the same house one hundred years in the past, where "
    "children ask them where they are from and who they are. The atmosphere is terrifying, "
    "ominous, with very little lighting. The novel explores identity, consciousness, and the "
    "fragile boundary between self and other."
)

# Scripted answers to the interactive prompts, in call order.
ANSWERS = deque([
    "1",          # scope: single book
    "1",          # concept: provide idea
    BOOK_IDEA,    # the book idea
    "250",        # page count
    "",           # accept estimated chapter count
    "y",          # proceed with generation
    "1",          # pick plot option 1
    "",           # accept layout
    "",           # accept chapter structure
])


def _script_input(prompt=""):
    if not ANSWERS:
        raise EOFError("Unexpected input() prompt; scripted answers exhausted: " + repr(prompt))
    return ANSWERS.popleft()


def _clear_output():
    out = OUTPUT_DIR
    out.mkdir(exist_ok=True)
    for pattern in (
        "project_data.json", "glossary.json", "book_glossary.txt", "book_analysis.txt",
        "ai_usage_state.json", "chapter_*.txt", "checkpoint_*.json",
    ):
        for path in glob.glob(str(out / pattern)):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
    print("🧹 Cleared previous book_output artifacts.")


def main() -> None:
    os.chdir(REPO_ROOT)
    os.environ["AI_CONFIG_PATH"] = CONFIG_PATH
    os.environ["AI_WRITING_MODEL"] = "glm-5.2"
    os.environ["AI_REVIEW_MODEL"] = "glm-5.2"

    _clear_output()
    builtins.input = _script_input  # script all interactive prompts

    # Import after env is set so .env (loaded on import) does not override us.
    from ai_book_creator.core.project_manager import ProjectManager
    from ai_book_creator.services.ai_service import AIService
    from ai_book_creator.steps.step_0_init import InitStep
    from ai_book_creator.steps.step_1_structure import StructureStep
    from ai_book_creator.steps.step_2_write import WriteStep
    from ai_book_creator.utils.glossary_manager import GlossaryManager

    output_dir = str(OUTPUT_DIR)
    project_manager = ProjectManager(output_dir)
    ai_service = AIService(
        config_path=CONFIG_PATH,
        usage_state_path=os.path.join(output_dir, "ai_usage_state.json"),
    )
    glossary_manager = GlossaryManager(output_dir)

    # Give the reasoning model headroom: bump writing budget to 8192, planning to >=4096.
    _orig_generate = ai_service.generate_content

    def _generate(prompt, model_type="writing", max_retries=5, max_completion_tokens=None):
        if max_completion_tokens is not None:
            if model_type == "writing" and max_completion_tokens < 8192:
                max_completion_tokens = 8192
            elif model_type != "writing" and max_completion_tokens < 4096:
                max_completion_tokens = 4096
        return _orig_generate(prompt, model_type=model_type, max_retries=max_retries,
                              max_completion_tokens=max_completion_tokens)

    ai_service.generate_content = _generate

    # Cap StructureStep: only build a detailed plot for chapter 1.
    _orig_create_plots = StructureStep._create_plots

    def _create_plots_ch1(self, chapters, init_data, name_pools=None):
        return _orig_create_plots(self, chapters[:1], init_data, name_pools)

    StructureStep._create_plots = _create_plots_ch1

    init_step = InitStep(ai_service, project_manager)
    structure_step = StructureStep(ai_service, project_manager, glossary_manager)
    write_step = WriteStep(ai_service, project_manager, glossary_manager, output_dir)

    print("\n" + "=" * 60 + "\nSTEP 0: INITIALIZATION\n" + "=" * 60)
    init_step.execute()
    project_manager.save_project()

    print("\n" + "=" * 60 + "\nSTEP 1: CHAPTER STRUCTURE (chapter 1 only)\n" + "=" * 60)
    structure_step.execute()
    project_manager.save_project()

    print("\n" + "=" * 60 + "\nSTEP 2: WRITING CHAPTER 1\n" + "=" * 60)
    write_step.execute()
    project_manager.save_project()

    chapter_file = OUTPUT_DIR / "chapter_01.txt"
    print("\n" + "=" * 60)
    print(f"✅ Chapter 1 written to: {chapter_file}")
    print("=" * 60)
    if chapter_file.exists():
        text = chapter_file.read_text(encoding="utf-8").strip()
        word_count = len(text.split())
        print(f"Word count: {word_count}\n")
        print("-" * 60)
        print(text)
        print("-" * 60)
    else:
        print("⚠️ chapter_01.txt was not produced.")
        sys.exit(1)


if __name__ == "__main__":
    main()
