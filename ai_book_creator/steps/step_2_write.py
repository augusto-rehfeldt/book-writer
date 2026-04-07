"""
Step 2: Write Chapters - Generate complete chapter text
"""

import os
import time
import json
from typing import Dict, Any
from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ..utils.text_utils import calculate_word_count, calculate_page_count


class WriteStep(BaseStep):
    def __init__(self, ai_service, project_manager, glossary_manager, output_dir):
        super().__init__(ai_service, project_manager, glossary_manager)
        self.step_name = "written"
        self.output_dir = output_dir
        self.config = self._load_config()

    def _load_config(self):
        config_path = os.getenv(
            "AI_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "..", "config", "ai_config_google.json"),
        )
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def should_execute(self) -> bool:
        existing = self.get_step_data()
        chapters = existing.get("chapters", {})
        if not self.is_completed():
            return True
        if not chapters:
            return True

        structure_data = self.project_manager.get_step_data("structure")
        expected_count = len(structure_data.get("chapter_plots", {}))
        if expected_count and len(chapters) != expected_count:
            return True

        for chapter_info in chapters.values():
            filename = chapter_info.get("filename")
            if not filename or not os.path.exists(filename):
                return True

        return False
    
    def get_step_header(self) -> str:
        return "="*60 + "\n✍️ STEP 2: WRITING CHAPTERS\n" + "="*60
    
    def execute(self) -> Dict[str, Any]:
        init_data = self.project_manager.get_step_data("init")
        structure_data = self.project_manager.get_step_data("structure")
        existing_written_data = self.project_manager.get_step_data("written") or {}
        
        written_chapters = dict(existing_written_data.get("chapters", {}))
        total_word_count = 0

        for chapter_info in written_chapters.values():
            filename = chapter_info.get("filename")
            if filename and os.path.exists(filename):
                try:
                    with open(filename, "r", encoding="utf-8") as f:
                        total_word_count += calculate_word_count(f.read())
                except Exception:
                    total_word_count += int(chapter_info.get("word_count", 0))
            else:
                total_word_count += int(chapter_info.get("word_count", 0))
        
        chapter_plots = structure_data.get("chapter_plots", {})
        min_words = self.config.get("min_chapter_words", 1000)
        
        for chapter_key, chapter_data in chapter_plots.items():
            existing_chapter = written_chapters.get(chapter_key)
            existing_filename = existing_chapter.get("filename") if existing_chapter else None
            if existing_chapter and existing_filename and os.path.exists(existing_filename):
                print(f"\nSkipping already cached {chapter_data['title']}...")
                continue

            print(f"\nWriting {chapter_data['title']} (First Draft)...")
            
            glossary_context = ""
            if self.glossary_manager:
                glossary_context = self.glossary_manager.get_context_for_writing()
            
            prompt = self._build_chapter_prompt(
                chapter_data,
                glossary_context,
                min_words,
                init_data.get("series_layout_content", ""),
            )
            
            # Generating First Draft
            text = self.ai_service.generate_content(
                prompt,
                model_type="writing",
                max_completion_tokens=4096,
            )

            if not text.strip():
                raise BrokenProjectStateError(
                    f"Chapter text generation returned empty content for {chapter_data['title']}. "
                    "The step will be retried so the missing chapter is not skipped."
                )

            word_count_initial = calculate_word_count(text)
            print(f"First draft completed ({word_count_initial} words). Initiating second review/improvement pass...")

            # --- Second Pass AI Edit ---
            improvement_prompt = self._build_chapter_improvement_prompt(
                text, chapter_data, glossary_context, min_words
            )

            improved_text = self.ai_service.generate_content(
                improvement_prompt,
                model_type="writing",
                max_completion_tokens=4096,
            )

            if improved_text.strip():
                text = improved_text
                word_count = calculate_word_count(text)
                print(f"Second pass completed ({word_count} words).")
            else:
                word_count = word_count_initial
                print("Second pass returned empty. Using first draft instead.")
            # ---------------------------
            
            # Save final chapter
            chapter_num = chapter_data['chapter_number']
            filename = os.path.join(self.output_dir, f"chapter_{chapter_num:02d}.txt")
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(text)

            written_chapters[chapter_key] = {
                "title": chapter_data["title"],
                "chapter_number": chapter_num,
                "filename": filename,
                "word_count": word_count
            }

            total_word_count += word_count
            
            if self.glossary_manager:
                self.glossary_manager.auto_populate_from_chapter(
                    text, chapter_data['title'], self.ai_service
                )
            
            written_data = {
                "chapters": written_chapters,
                "total_word_count": total_word_count,
                "total_pages": calculate_page_count(total_word_count),
                "_partial": True,
            }
            self.save_step_data(written_data)
            time.sleep(2)

        if not written_chapters:
            recovery = self.project_manager.get_recovery_plan()
            raise BrokenProjectStateError(
                "Step 2 produced no written chapters. "
                f"{recovery['message']}",
                latest_valid_step=recovery.get("latest_valid_step", ""),
                restart_step=recovery.get("restart_step", ""),
                broken_steps=recovery.get("broken_steps", []),
            )
        
        total_pages = calculate_page_count(total_word_count)
        
        print(f"\n✅ WRITING COMPLETE!")
        print(f"Chapters: {len(written_chapters)}")
        print(f"Words: {total_word_count:,}")
        print(f"Pages: {total_pages}")
        
        written_data = {
            "chapters": written_chapters,
            "total_word_count": total_word_count,
            "total_pages": total_pages
        }
        
        self.save_step_data(written_data)
        self.mark_completed()
        return written_data

    def _build_chapter_prompt(
        self,
        chapter_data: Dict[str, Any],
        glossary_context: str,
        min_words: int,
        series_layout: str = "",
    ) -> str:
        opening_style = chapter_data.get("opening_style", "").strip() or "varied"
        series_section = ""
        if series_layout.strip():
            series_section = f"""

SERIES LAYOUT:
{series_layout.strip()}
"""

        return f"""Write complete chapter text:

CHAPTER: {chapter_data['title']}
OPENING STYLE TAG: {opening_style}
PLOT OUTLINE: {chapter_data['plot_outline']}

{series_section}

{glossary_context}

Write at least {min_words} words.
- Creative, authentic prose
- Natural dialogue
- Varied pacing
- Clean, modern style
- Use markdown intentionally to improve readability and emphasis.
- Put the chapter title at the top as a level-1 markdown heading, e.g. `# {chapter_data['title']}`.
- Use `##` or `###` for scene or beat subheadings only when they help the flow.
- Use **bold** and *italics* sparingly for emphasis, interiority, or important details.
- Use `***` or `---` for scene breaks when the chapter needs a visual pause.
- If a local image reference genuinely helps the chapter, you may include it with markdown image syntax like `![alt text](relative/path.png)`.
- Do not over-format every paragraph; keep markdown purposeful and novel-like.
- A sensible chapter layout might look like:
  # Chapter Title
  ## Scene One
  paragraph text
  ***
  ## Scene Two
  paragraph text
- Show, don't tell
- CRITICAL: Make the opening feel different from the previous chapter. Do not reuse the same first-sentence structure, first image, or cadence unless the story absolutely requires it.
- CRITICAL: Do not begin with "Again" unless it is literal dialogue and genuinely belongs to the scene.
- CRITICAL: Avoid template openings such as "The room...", "The air...", "He looked...", "She looked...", "Across the world...", or similar boilerplate scene-setters.
- CRITICAL: Start with a concrete action, contradiction, sensory detail, or voice that belongs only to this chapter.
- CRITICAL: Vary sentence length and paragraph rhythm so the prose feels lived-in, not formulaic.
- Keep the narration character-driven and humane rather than detached.

Output ONLY chapter text, no commentary. Ensure the Chapter Title is prominently placed at the very top of the text."""

    def _build_chapter_improvement_prompt(
        self,
        draft_text: str,
        chapter_data: Dict[str, Any],
        glossary_context: str,
        min_words: int
    ) -> str:
        return f"""You are an expert editor and author. Review and improve the following chapter draft.

CHAPTER: {chapter_data['title']}
PLOT OUTLINE: {chapter_data['plot_outline']}

{glossary_context}

DRAFT TEXT:
{draft_text}

INSTRUCTIONS:
- Rewrite and elevate the prose to be more engaging, immersive, and showing rather than telling.
- Enhance the sensory details, emotional depth, and pacing.
- Ensure dialogue is natural and advances characterization or plot.
- Fix any inconsistencies or repetitive sentence structures.
- The final output MUST be at least {min_words} words.
- Output ONLY the fully revised chapter text with markdown formatting. Include the chapter title as an H1 (`#`) at the top. Do not include any meta-commentary or notes.
"""