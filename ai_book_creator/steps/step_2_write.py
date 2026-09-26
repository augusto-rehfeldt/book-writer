"""
Step 2: Write Chapters - Generate complete chapter text
"""

from ai_suite.service import DEFAULT_CONFIG_PATH
import os
import json
from pathlib import Path
from typing import Dict, Any
from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ..utils import humanizer
from ..utils.editorial import (REVISION_CHECKS, edit_text, indistinct_lines, story_context,
                               update_continuity, voice_bible)
from ..utils.text_utils import calculate_word_count, calculate_page_count, detail, is_auto, progress, save_text, text_digest


AUTHORIAL_PROSE_GUIDANCE = """Write from a particular consciousness. Let the viewpoint character notice what this person would notice and miss what this person would miss, misremember things, and hold opinions the narration does not correct. Use exact physical details, exact quantities and prices, and the names this world gives its things: foods, songs, makers, streets, sayings. A generic noun where the world has a name is a missed detail, and so is a recurring person known only by role. Work details into the sentences where the action happens; do not list them as stand-alone fragments. Tell what people do, not a string of what they did not do, and contract negation the way people talk and think (didn't, wasn't) unless the voice is formal.

Let rhythm follow the character's attention and the scene's pressure. The ordinary narrative sentence is a full one, with a clause or two; fragments and one-line sentences are an effect, and in runs they flatten a scene instead of tightening it. Danger and grief do not require clipped prose. Let a thought run on through commas and "and" when the character is absorbed, remembering or talking themselves into something. Adverbs, semicolons and fragments are fine when they serve the thought. Play consequential moments as scenes; use summary to cross uneventful time.

Allow the mind to wander the way a real one does: an object calls up a memory, a joke, a grudge or an old song, and the scene continues. Keep humor that belongs to the characters, including bad jokes and the small absurdities of ordinary life. Not every detail must serve the plot.

Trust the scene. Do not explain an emotion after it has been shown, and do not end a paragraph on a neat maxim that sums up its meaning. Let an ending follow the scene's consequence. Leave some things unresolved, and let choices cost something that is not fully redeemed.

Give each character the speech on their voice card. People differ in how much they talk: one rambles, tells a half-relevant story or repeats themselves, another answers in a word. Let dialogue include interruption, evasion, non-answers, talking past each other, callbacks to earlier conversations, private shorthand, small talk with no plot purpose, and a register that changes with who is listening. Nobody should make a speech merely to explain facts everyone present already knows.

Prefer specific observations over stock phrases. Keep useful rough edges, ambiguity and odd specificity. Prefer a sentence that belongs to this character and this scene over one that merely sounds polished."""


class WriteStep(BaseStep):
    def __init__(self, ai_service, project_manager, glossary_manager, output_dir):
        super().__init__(ai_service, project_manager, glossary_manager)
        self.step_name = "written"
        self.output_dir = output_dir
        self.config = self._load_config()

    def _load_config(self):
        config_path = os.getenv(
            "AI_CONFIG_PATH",
            DEFAULT_CONFIG_PATH,
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

        chapter_plots = structure_data.get("chapter_plots", {})
        fallback_min = self.config.get("min_chapter_words", 1000)
        init_page_count = int(init_data.get("page_count", 400))
        init_words_per_page = int(init_data.get("words_per_page", 250))
        init_target_words = int(init_data.get("target_word_count") or init_page_count * init_words_per_page)
        chapter_count = len(chapter_plots) or 25
        scaled_min = max(fallback_min, init_target_words // chapter_count)
        
        if init_data.get("layout_content") and not init_data.get("voice_bible"):
            print("\nWriting voice cards and world texture...")
            init_data["voice_bible"] = voice_bible(self.ai_service, init_data)
            self.project_manager.set_step_data("init", init_data)
            self.project_manager.save_project()

        memory, previous_ending = "", ""
        for position, (chapter_key, chapter_data) in enumerate(sorted(
                chapter_plots.items(), key=lambda item: item[1]["chapter_number"]), 1):
            existing_chapter = written_chapters.get(chapter_key)
            existing_filename = existing_chapter.get("filename") if existing_chapter else None
            if existing_chapter and existing_filename and os.path.exists(existing_filename):
                detail(f"\nSkipping already cached {chapter_data['title']}...")
                text = Path(existing_filename).read_text(encoding="utf-8")
                if not text.strip():
                    raise BrokenProjectStateError(f"Empty saved chapter: {existing_filename}")
                context_hash = text_digest(memory)
                changed = False
                reason = ("no continuity record yet" if not existing_chapter.get("continuity")
                          else "chapter text changed" if existing_chapter.get("source_hash") != text_digest(text)
                          else "earlier chapters' record changed"
                          if existing_chapter.get("context_hash") != context_hash else "")
                if reason:
                    detail(f"  Rebuilding continuity record ({reason})...")
                    existing_chapter["continuity"] = update_continuity(
                        self.ai_service, text, memory, chapter_data["title"])
                    existing_chapter["source_hash"] = text_digest(text)
                    existing_chapter["context_hash"] = context_hash
                    changed = True
                memory = existing_chapter["continuity"]
                previous_ending = text[-2500:]
                existing_chapter["word_count"] = calculate_word_count(text)
                if self.glossary_manager and existing_chapter.get("glossary_hash") != text_digest(text):
                    detail("  Updating glossary from chapter...")
                    self.glossary_manager.auto_populate_from_chapter(text, chapter_data["title"], self.ai_service)
                    existing_chapter["glossary_hash"] = text_digest(text)
                    changed = True
                total_word_count += existing_chapter["word_count"]
                # Nothing recomputed: the final save after the loop records the totals.
                if changed:
                    self.save_step_data({"chapters": written_chapters, "total_word_count": total_word_count,
                                         "total_pages": calculate_page_count(total_word_count, init_words_per_page),
                                         "_partial": True})
                    self.project_manager.save_project()
                continue

            if is_auto():
                progress("Writing", position, len(chapter_plots), chapter_data['title'])
            else:
                print(f"\nWriting {chapter_data['title']} (First Draft)...")

            # Step 1 hands every chapter its own budget, uneven on purpose. A short
            # one must not be floored back up to the book average.
            target_words = max(400, int(chapter_data.get("word_count_estimate") or scaled_min))
            glossary = self.glossary_manager._format_glossary_content() if self.glossary_manager else ""
            context = story_context(init_data, memory, glossary)
            context += f"\n\nPREVIOUS CHAPTER ENDING:\n{previous_ending}"
            prompt = self._build_chapter_prompt(
                chapter_data,
                target_words,
                init_data.get("series_layout_content", ""),
                context,
            )
            
            # Generating First Draft
            chapter_num = chapter_data["chapter_number"]
            filename = os.path.join(self.output_dir, f"chapter_{chapter_num:02d}.txt")
            draft_filename = str(Path(filename).with_suffix(".draft.txt"))
            text = (Path(draft_filename).read_text(encoding="utf-8") if Path(draft_filename).exists()
                    else self.ai_service.generate_content(prompt, model_type="writing"))

            if not text.strip():
                raise BrokenProjectStateError(
                    f"Chapter text generation returned empty content for {chapter_data['title']}. "
                    "The step will be retried so the missing chapter is not skipped."
                )

            save_text(draft_filename, text)
            flat = indistinct_lines(self.ai_service, text, init_data.get("voice_bible", ""))
            voice_note = (" These dialogue lines could not be told apart from another character's "
                          "by their words alone; where the scene allows, give each its speaker's "
                          "voice from the voice cards: " + "; ".join(f"«{line}»" for line in flat[:15])
                          if flat else "")
            text, edits = edit_text(
                self.ai_service, text,
                "Repair specific weaknesses in motivation, continuity, clarity, subtext or redundant "
                "explanation. " + REVISION_CHECKS + voice_note +
                " Do not expand solely to meet a word count.",
                context + "\nCHAPTER OUTLINE:\n" + chapter_data["plot_outline"])

            # --- Humanness pass: score against real published novels, rewrite
            # the blocks that read as machine-written. Off when no baseline has
            # been built (benchmarks/build_prose_baseline.py) or AI_BOOK_HUMANIZE=0.
            human_report = {}
            if humanizer.enabled():
                text, human_report = humanizer.humanize(
                    self.ai_service,
                    text,
                    rounds=int(self.config.get("humanize_rounds", 2)),
                    threshold=float(self.config.get("humanize_threshold", 15.0)),
                    context=context,
                    log=detail,
                )
            word_count = calculate_word_count(text)
            
            # Save final chapter
            chapter_num = chapter_data['chapter_number']
            filename = os.path.join(self.output_dir, f"chapter_{chapter_num:02d}.txt")
            
            save_text(filename, text)
            # Cache the finished prose before any auxiliary model call can fail.
            written_chapters[chapter_key] = {
                "title": chapter_data["title"], "chapter_number": chapter_num,
                "filename": filename, "word_count": word_count,
                "editorial": edits, "style_diagnostics": human_report,
            }
            self.save_step_data({"chapters": written_chapters, "_partial": True,
                                 "total_word_count": total_word_count + word_count})
            self.project_manager.save_project()
            context_hash = text_digest(memory)
            detail("  Updating continuity record...")
            memory = update_continuity(self.ai_service, text, memory, chapter_data["title"])
            previous_ending = text[-2500:]

            written_chapters[chapter_key] = {
                "title": chapter_data["title"],
                "chapter_number": chapter_num,
                "filename": filename,
                "word_count": word_count,
                "continuity": memory,
                "source_hash": text_digest(text),
                "context_hash": context_hash,
                "editorial": edits,
                "style_diagnostics": human_report,
            }

            total_word_count += word_count
            
            if self.glossary_manager:
                detail("  Updating glossary from chapter...")
                self.glossary_manager.auto_populate_from_chapter(
                    text, chapter_data['title'], self.ai_service
                )
                written_chapters[chapter_key]["glossary_hash"] = text_digest(text)
            
            written_data = {
                "chapters": written_chapters,
                "total_word_count": total_word_count,
                "total_pages": calculate_page_count(total_word_count, init_words_per_page),
                "_partial": True,
            }
            self.save_step_data(written_data)
            self.project_manager.save_project()

        if not written_chapters:
            recovery = self.project_manager.get_recovery_plan()
            raise BrokenProjectStateError(
                "Step 2 produced no written chapters. "
                f"{recovery['message']}",
                latest_valid_step=recovery.get("latest_valid_step", ""),
                restart_step=recovery.get("restart_step", ""),
                broken_steps=recovery.get("broken_steps", []),
            )
        
        total_pages = calculate_page_count(total_word_count, init_words_per_page)
        
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
        target_words: int,
        series_layout: str = "",
        context: str = "",
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

{context}

OPENING:
Honor the opening style tag. Begin with an action, observation, contradiction, or line of speech that could belong only to this chapter. Do not fall back on a generic survey of the room, the air, or the wider world.

FORM:
- Write about {target_words} words (anywhere from {int(target_words * 0.85)} to {int(target_words * 1.15)}). This chapter's length is deliberate: hold to it rather than padding or rushing.
- Put `# {chapter_data['title']}` at the top.
- Use `***` for a scene break only when time, place, or viewpoint actually shifts.
- Otherwise write continuous novel prose without subheadings, bold text, decorative formatting, or commentary.

Output only the finished chapter."""

