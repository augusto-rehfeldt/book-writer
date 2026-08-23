"""
Step 2: Write Chapters - Generate complete chapter text
"""

import os
import time
import json
import zlib
from typing import Dict, Any
from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ..utils import humanizer
from ..utils.text_utils import calculate_word_count, calculate_page_count


AUTHORIAL_PROSE_GUIDANCE = """Write from a particular consciousness. Let the viewpoint character notice what this person would notice and miss what this person would miss. Use exact physical details, plain verbs, and character-specific thoughts.

HABITS THAT READ AS MACHINE-WRITTEN. Measured on 577 chapters of published English fiction against this pipeline's own drafts (ai_book_creator/config/prose_baseline.json). These are the differences that showed up; none of them is a quota, and the chapter's own register below outranks all of them:
- Do not scrub the -ly adverbs. Published novelists write "quietly", "almost", "finally" without apology; prose with every adverb stripped out is the single clearest machine signature in the sample.
- Do not write the whole chapter in short punches. Staccato is a tool for a moment, not a default setting.
- Let a sentence run long when the thought is long. Commas, "and", a semicolon, a subordinate clause are all available.
- Semicolons and colons are allowed in narrative prose. Real novels use them.
- Vary sentence openings, and vary paragraph size hard: one-line paragraphs next to long ones, not a page of evenly-sized blocks.
- Play scenes rather than summarising them.

Name each thing consistently. Prefer active voice and direct verbs. Avoid nominalizations, stacked auxiliaries, vague phrasal verbs, and editorial adjectives. Keep contractions, idiom, purposeful fragments, and varied cadence when they belong to the narrator or character.

Trust the scene. Do not announce its meaning, inflate its importance, explain an emotion after it has already been shown, or end with a broad statement about life or the future. Cut filter verbs (felt, saw, heard, noticed, realized, seemed) wherever the thing itself can simply happen.

Let dialogue include interruption, evasion, misunderstanding, private shorthand, and silence where those fit the characters. Nobody should make a speech merely to explain facts everyone present already knows.

Never write these: "a mixture of", "the weight of it", "hung in the air", "couldn't help but", "a shiver ran down", "let out a breath she didn't know she was holding", "in that moment", "something shifted", "the corners of his mouth", "barely above a whisper", "little did", "only time would tell".

Keep useful rough edges, ambiguity, and odd specificity. Prefer a sentence that belongs to this character and this scene over one that merely sounds polished."""


class WriteStep(BaseStep):
    def __init__(self, ai_service, project_manager, glossary_manager, output_dir):
        super().__init__(ai_service, project_manager, glossary_manager)
        self.step_name = "written"
        self.output_dir = output_dir
        self.config = self._load_config()

    def _load_config(self):
        config_path = os.getenv(
            "AI_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "..", "config", "ai_config_minimax.local.json"),
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
        fallback_min = self.config.get("min_chapter_words", 1000)
        init_page_count = int(init_data.get("page_count", 400))
        init_words_per_page = int(init_data.get("words_per_page", 250))
        init_target_words = int(init_data.get("target_word_count") or init_page_count * init_words_per_page)
        chapter_count = len(chapter_plots) or 25
        scaled_min = max(fallback_min, init_target_words // chapter_count)
        
        for chapter_key, chapter_data in chapter_plots.items():
            existing_chapter = written_chapters.get(chapter_key)
            existing_filename = existing_chapter.get("filename") if existing_chapter else None
            if existing_chapter and existing_filename and os.path.exists(existing_filename):
                print(f"\nSkipping already cached {chapter_data['title']}...")
                continue

            print(f"\nWriting {chapter_data['title']} (First Draft)...")

            # Step 1 hands every chapter its own budget, uneven on purpose. A short
            # one must not be floored back up to the book average.
            target_words = max(400, int(chapter_data.get("word_count_estimate") or scaled_min))
            prompt = self._build_chapter_prompt(
                chapter_data,
                target_words,
                init_data.get("series_layout_content", ""),
            )
            
            # Generating First Draft
            text = self.ai_service.generate_content(
                prompt,
                model_type="writing",
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
                text, chapter_data, target_words
            )

            improved_text = self.ai_service.generate_content(
                improvement_prompt,
                model_type="writing",
            )

            if improved_text.strip():
                text = improved_text
                word_count = calculate_word_count(text)
                print(f"Second pass completed ({word_count} words).")
            else:
                word_count = word_count_initial
                print("Second pass returned empty. Using first draft instead.")
            # ---------------------------

            # --- Humanness pass: score against real published novels, rewrite
            # the blocks that read as machine-written. Off when no baseline has
            # been built (benchmarks/build_prose_baseline.py) or AI_BOOK_HUMANIZE=0.
            if humanizer.enabled():
                text, _ = humanizer.humanize(
                    self.ai_service,
                    text,
                    rounds=int(self.config.get("humanize_rounds", 2)),
                    threshold=float(self.config.get("humanize_threshold", 15.0)),
                    use_judges=bool(self.config.get("judge_models")),
                )
                word_count = calculate_word_count(text)
            
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

    def _chapter_register(self, chapter_data: Dict[str, Any]) -> str:
        """This chapter's own tempo, drawn from the corpus distribution.

        One target for the whole book is itself a tell: published books vary
        chapter to chapter about twice as much as this pipeline did. Seeded on
        the chapter number and title so a resumed run rebuilds the same lane.
        """
        if not humanizer.enabled():
            return ""
        # crc32, not hash(): str hashing is salted per process, so a resumed run
        # would hand the same chapter a different tempo.
        key = f"{chapter_data.get('chapter_number')}|{chapter_data.get('title', '')}"
        return humanizer.chapter_lane(zlib.crc32(key.encode("utf-8", "replace")))

    def _build_chapter_prompt(
        self,
        chapter_data: Dict[str, Any],
        target_words: int,
        series_layout: str = "",
    ) -> str:
        opening_style = chapter_data.get("opening_style", "").strip() or "varied"
        series_section = ""
        if series_layout.strip():
            series_section = f"""

SERIES LAYOUT:
{series_layout.strip()}
"""

        return f"""Write this chapter as a novelist with a distinct, character-led voice:

CHAPTER: {chapter_data['title']}
OPENING STYLE TAG: {opening_style}
PLOT OUTLINE: {chapter_data['plot_outline']}

{series_section}

AUTHORIAL APPROACH:
{AUTHORIAL_PROSE_GUIDANCE}

{self._chapter_register(chapter_data)}

OPENING:
Honor the opening style tag. Begin with an action, observation, contradiction, or line of speech that could belong only to this chapter. Do not fall back on a generic survey of the room, the air, or the wider world.

FORM:
- Write about {target_words} words (anywhere from {int(target_words * 0.85)} to {int(target_words * 1.15)}). This chapter's length is deliberate: hold to it rather than padding or rushing.
- Put `# {chapter_data['title']}` at the top.
- Use `***` for a scene break only when time, place, or viewpoint actually shifts.
- Otherwise write continuous novel prose without subheadings, bold text, decorative formatting, or commentary.

Output only the finished chapter."""

    def _build_chapter_improvement_prompt(
        self,
        draft_text: str,
        chapter_data: Dict[str, Any],
        target_words: int
    ) -> str:
        return f"""Revise this chapter in its established authorial voice.

CHAPTER: {chapter_data['title']}
PLOT OUTLINE: {chapter_data['plot_outline']}

DRAFT TEXT:
{draft_text}

AUTHORIAL APPROACH:
{AUTHORIAL_PROSE_GUIDANCE}

Keep strong and distinctive passages intact. Rewrite only what is vague, over-explained, repetitive, out of character, or inconsistent with the plot. Do not add description merely to make the prose richer, and do not smooth every paragraph into the same cadence. Keep every established event and return about {target_words} words ({int(target_words * 0.85)} to {int(target_words * 1.15)}).

Output only the revised chapter, with `# {chapter_data['title']}` at the top and `***` only for genuine scene breaks.
"""
