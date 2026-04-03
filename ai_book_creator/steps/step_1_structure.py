"""
Step 1: Create Structure - Chapter breakdown with plots
"""

import re
import time
from typing import Dict, Any, List
from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError


class StructureStep(BaseStep):
    def __init__(self, ai_service, project_manager, glossary_manager):
        super().__init__(ai_service, project_manager, glossary_manager)
        self.step_name = "structure"
    
    def should_execute(self) -> bool:
        existing = self.get_step_data()
        chapter_plots = existing.get("chapter_plots", {})
        structure_content = existing.get("structure_content", "")
        if not self.is_completed():
            return True
        if not structure_content:
            return True
        expected_chapters = self._extract_chapters(structure_content)
        if not expected_chapters:
            return True
        if len(chapter_plots) != len(expected_chapters):
            return True
        return False
    
    def get_step_header(self) -> str:
        return "="*60 + "\n📖 STEP 1: CHAPTER STRUCTURE\n" + "="*60
    
    def execute(self) -> Dict[str, Any]:
        init_data = self.project_manager.get_step_data("init")
        existing_data = self.get_step_data()
        
        structure_content = existing_data.get("structure_content", "")
        chapter_plots = dict(existing_data.get("chapter_plots", {}))

        # Create chapter breakdown only when we do not already have cached structure text
        if not structure_content:
            structure_content = self._create_structure(init_data)
            self.save_step_data({
                "structure_content": structure_content,
                "chapter_plots": chapter_plots,
                "_partial": True,
            })
        else:
            print("📌 Reusing cached chapter structure from a previous run.")
        
        # Extract chapters
        chapters = self._extract_chapters(structure_content)
        
        # Create plots for each chapter
        chapter_plots = self._create_plots(chapters, init_data)

        if not chapter_plots:
            recovery = self.project_manager.get_recovery_plan()
            raise BrokenProjectStateError(
                "Step 1 produced no usable chapter plots. "
                f"{recovery['message']}",
                latest_valid_step=recovery.get("latest_valid_step", ""),
                restart_step=recovery.get("restart_step", ""),
                broken_steps=recovery.get("broken_steps", []),
            )
        
        structure_data = {
            "structure_content": structure_content,
            "chapter_plots": chapter_plots
        }
        
        self.save_step_data(structure_data)
        self.mark_completed()
        return structure_data
    
    def _create_structure(self, init_data: Dict) -> str:
        print("🔄 Generating chapter structure...")

        prompt, max_completion_tokens = self._build_structure_prompt(init_data)
        structure = self.ai_service.generate_content(prompt, max_completion_tokens=max_completion_tokens)
        
        print("\n📚 CHAPTER STRUCTURE:")
        print("-" * 50)
        print(structure)
        print("-" * 50)
        
        return structure

    def _build_structure_prompt(self, init_data: Dict) -> tuple[str, int]:
        is_groq = getattr(self.ai_service, "provider", "") == "groq"
        book_idea = self._truncate_text(init_data.get("book_idea", ""), 350 if is_groq else 500)
        layout_content = self._truncate_text(init_data.get("layout_content", ""), 1400 if is_groq else 3500)
        opening_style_list = (
            "in medias res, dialogue-led, sensory close-up, object-focused, institutional briefing, "
            "procedural action, quiet reflection, cross-cut, suspense hook"
        )

        if is_groq:
            prompt = self.ai_service.build_sectioned_prompt(
                instruction=(
                    "Create a compact chapter outline as a markdown table. "
                    "Return ONLY the table, with no extra commentary."
                ),
                sections=[
                    ("Book idea", book_idea),
                    ("Layout summary", layout_content),
                    (
                        "Output rules",
                        "Use exactly 5 columns: #, Title, Summary, Key events, Word count. "
                        "Start each Summary with 'Opening style tag:' followed by one tag from: "
                        f"{opening_style_list}. "
                        "Keep Summary to 15-20 words after the tag. Keep Key events to at most 3 short phrases "
                        "separated by semicolons. Vary opening style tags across adjacent chapters so the structure "
                        "feels scene-specific and novelistic. Use 20-24 chapters. Word count must be a single integer "
                        "like 1400.",
                    ),
                ],
                max_prompt_tokens=3000,
                section_token_caps={
                    "Book idea": 180,
                    "Layout summary": 700,
                    "Output rules": 260,
                },
            )
            return prompt, 1600

        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                "Create chapter structure from the provided context. "
                "Generate 20-30 chapters and return ONLY a plain numbered list."
            ),
            sections=[
                ("Book idea", book_idea),
                ("Layout summary", layout_content),
                (
                    "Output rules",
                    "Each chapter must be on its own line in the form: '1. Chapter 1 - Four Powers, One Sky: Opening style tag: dialogue-led. "
                    "Summary...' "
                    "Do not include the literal word 'Title:' before the chapter title. "
                    "Do not use a table, bullets, headings, or Markdown formatting. "
                    "For every chapter, include an opening style tag chosen from: "
                    f"{opening_style_list}. "
                    "Vary the opening style tag from chapter to chapter so adjacent chapters do not feel mechanically similar. "
                    "Then write a 2-3 sentence summary, key events, and a word count estimate of 1200-1500 words. "
                    "Keep the chapter openings specific, concrete, and distinct in tone and sentence shape.",
                ),
            ],
            max_prompt_tokens=8000,
            section_token_caps={
                "Book idea": 250,
                "Layout summary": 2500,
                "Output rules": 450,
            },
        )
        return prompt, 4096
    
    def _extract_chapters(self, content: str) -> List[Dict]:
        chapters = []
        lines = content.split('\n')
        # Handle both numbered lists and markdown tables.
        numbered_pattern = re.compile(
            r"""^\s*(?:[*#>\-]\s*)?\d+\.\s*Chapter\s+(\d+)\s*[—–-]\s*(.+?)\s*$""",
            re.IGNORECASE,
        )
        table_pattern = re.compile(
            r"""^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*([\d, ]+)\s*\|\s*$""",
            re.IGNORECASE,
        )
        
        current = {}
        for line in lines:
            stripped = line.strip()
            if self._is_table_separator(stripped):
                continue
            match = numbered_pattern.match(stripped)
            table_match = table_pattern.match(stripped)
            table_cells = self._parse_table_row(stripped)

            if match or table_match or table_cells:
                if current and current.get('content', '').strip():
                    chapters.append(current)
                
                if match:
                    chapter_number = int(match.group(1))
                    title, summary, key_events, word_count_estimate, opening_style = self._parse_numbered_outline(
                        match.group(2)
                    )
                elif table_match:
                    chapter_number = int(table_match.group(1))
                    title = self._clean_title_cell(table_match.group(2))
                    summary_text, opening_style = self._extract_opening_style(table_match.group(3))
                    summary = self._clean_cell(summary_text)
                    key_events = self._clean_cell(table_match.group(4))
                    word_count_estimate = self._clean_word_count(table_match.group(5))
                else:
                    chapter_number = int(table_cells[0])
                    title = self._clean_title_cell(table_cells[1])
                    summary_text, opening_style = self._extract_opening_style(table_cells[2])
                    summary = self._clean_cell(summary_text)
                    key_events = self._clean_cell(table_cells[3])
                    word_count_estimate = self._clean_word_count(table_cells[4])

                current = {
                    "title": title,
                    "content": "\n".join(
                        [part for part in [summary, key_events] if part]
                    ).strip(),
                    "opening_style": opening_style,
                    "chapter_number": chapter_number,
                    "word_count_estimate": word_count_estimate,
                }
            elif current and stripped and not stripped.startswith("|"):
                current["content"] += line + "\n"
        
        if current and current.get('content', '').strip():
            chapters.append(current)
        
        print(f"Extracted {len(chapters)} chapters")
        return chapters

    def _parse_numbered_outline(self, text: str) -> tuple[str, str, str, int, str]:
        """Parse a numbered chapter line that may contain title, summary, and word count."""
        cleaned = re.sub(r"\*\*", "", text).strip()
        word_count_estimate = 1500
        word_count_match = re.search(
            r"(?:word count(?: estimate)?|words?)\s*:\s*([\d,]+(?:\s*-\s*[\d,]+)?)(?:\s*words?)?",
            cleaned,
            re.IGNORECASE,
        )
        if word_count_match:
            word_count_estimate = self._clean_word_count(word_count_match.group(1))
            cleaned = (cleaned[:word_count_match.start()] + cleaned[word_count_match.end():]).strip(" ,;:-")

        cleaned = re.sub(r"^\s*Chapter\s*\d+\s*[—–-]\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^\s*Title\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        title, remainder = self._split_title_and_remainder(cleaned)
        remainder, opening_style = self._extract_opening_style(remainder)
        summary, remainder = self._extract_labeled_section(remainder, "summary")
        key_events, remainder = self._extract_labeled_section(remainder, "key events")

        if not summary and remainder:
            summary = remainder.strip().strip(" ,;:-")

        return (
            self._clean_title_cell(title),
            self._clean_cell(summary),
            self._clean_cell(key_events),
            word_count_estimate,
            opening_style,
        )

    def _split_title_and_remainder(self, text: str) -> tuple[str, str]:
        """Split a numbered outline entry into title and the remaining descriptive text."""
        primary_label_match = re.search(
            r"\b(?:opening style tag|summary|key events?)\s*:",
            text,
            re.IGNORECASE,
        )
        if primary_label_match:
            title = text[: primary_label_match.start()].strip(" ,;:-.")
            remainder = text[primary_label_match.start():].strip()
            return title, remainder

        if ":" in text:
            title, remainder = text.split(":", 1)
            return title.strip(" ,;:-."), remainder.strip()

        return text.strip(" ,;:-."), ""

    def _extract_labeled_section(self, text: str, label: str) -> tuple[str, str]:
        """Extract a labeled section from outline text and return the remaining text."""
        pattern = re.compile(
            rf"\b{re.escape(label)}\s*:\s*(.*?)(?=\b(?:opening style tag|summary|key events?|word count(?: estimate)?)\s*:|$)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        if not match:
            return "", text

        value = match.group(1).strip()
        remaining = (text[:match.start()] + text[match.end():]).strip(" ,;:-.")
        remaining = re.sub(r"\s{2,}", " ", remaining)
        return value, remaining

    def _extract_opening_style(self, text: str) -> tuple[str, str]:
        match = re.search(
            r"\bopening style tag\s*:\s*([^\.\n;|]+)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return text, ""

        opening_style = self._clean_cell(match.group(1))
        cleaned = (text[:match.start()] + text[match.end():]).strip(" ,;:-.")
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned, opening_style

    def _is_table_separator(self, line: str) -> bool:
        if not line.startswith("|"):
            return False
        return bool(re.fullmatch(r"\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line))

    def _parse_table_row(self, line: str) -> List[str]:
        if not line.startswith("|") or line.count("|") < 5:
            return []
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            return []
        if not cells[0].isdigit():
            return []
        return cells[:5]

    def _clean_title_cell(self, text: str) -> str:
        text = re.sub(r"\*\*", "", text).strip()
        text = re.sub(r"^\s*Title\s*:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*Chapter\s*\d+\s*[—–-]\s*", "", text, flags=re.IGNORECASE)
        return text.strip()

    def _clean_cell(self, text: str) -> str:
        cleaned = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\*\*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _clean_word_count(self, text: str) -> int:
        range_match = re.search(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)", text)
        if range_match:
            low = int(range_match.group(1).replace(",", ""))
            high = int(range_match.group(2).replace(",", ""))
            return max(400, (low + high) // 2)

        digits = re.sub(r"[^\d]", "", text)
        if not digits:
            return 1500
        try:
            return max(400, int(digits))
        except ValueError:
            return 1500

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
    
    def _create_plots(self, chapters: List[Dict], init_data: Dict) -> Dict:
        chapter_plots = dict(self.get_step_data().get("chapter_plots", {}))
        self.save_step_data({
            "structure_content": self.get_step_data().get("structure_content", ""),
            "chapter_plots": chapter_plots,
            "_partial": True,
        })
        
        for i, chapter in enumerate(chapters, 1):
            chapter_number = int(chapter.get("chapter_number", i))
            chapter_key = f"chapter_{chapter_number}"
            if chapter_key in chapter_plots:
                print(f"Skipping already cached plot for Chapter {chapter_number}...")
                continue

            print(f"Creating plot for Chapter {chapter_number}: {chapter.get('title', f'Chapter {chapter_number}')}")
            
            glossary_context = ""
            if self.glossary_manager:
                glossary_context = self.glossary_manager.get_context_for_writing()
            
            prompt = self.ai_service.build_sectioned_prompt(
                instruction=(
                    f"Create a detailed plot outline for Chapter {chapter_number}: "
                    f"{chapter.get('title', f'Chapter {chapter_number}')}. "
                    "Be detailed but concise."
                ),
                sections=[
                    ("Book summary", init_data["book_idea"][:500]),
                    ("Chapter summary", chapter.get("content", "")),
                    ("Opening style tag", chapter.get("opening_style", "")),
                    ("Requirements", "Opening scene; 3-5 key events; character interactions; conflict/tension; chapter ending/transition; emotional beats."),
                    ("Glossary context", glossary_context),
                ],
                max_prompt_tokens=7000,
                section_token_caps={
                    "Book summary": 200,
                    "Chapter summary": 1000,
                    "Opening style tag": 60,
                    "Requirements": 250,
                    "Glossary context": 500,
                },
            )
            
            plot = self.ai_service.generate_content(prompt, max_completion_tokens=1200)
            if not plot.strip():
                raise BrokenProjectStateError(
                    f"Chapter plot generation returned empty content for Chapter {chapter_number}. "
                    "The step will be retried so the missing chapter is not skipped."
                )
            
            if plot.strip():
                chapter_plots[chapter_key] = {
                    "title": chapter.get("title", f"Chapter {chapter_number}"),
                    "plot_outline": plot,
                    "chapter_number": chapter_number,
                    "opening_style": chapter.get("opening_style", ""),
                    "word_count_estimate": int(chapter.get("word_count_estimate", 1500)),
                }
                
                if self.glossary_manager:
                    self.glossary_manager.auto_populate_from_chapter(
                        plot, chapter.get("title", f"Chapter {chapter_number}"), self.ai_service
                    )

                self.project_manager.save_checkpoint(
                    f"structure_chapter_{chapter_number:02d}",
                    {
                        "chapter_key": chapter_key,
                        "chapter_number": chapter_number,
                        "title": chapter.get("title", f"Chapter {chapter_number}"),
                        "opening_style": chapter.get("opening_style", ""),
                        "word_count_estimate": int(chapter.get("word_count_estimate", 1500)),
                        "chapter_plot": chapter_plots[chapter_key],
                        "chapter_plots": chapter_plots,
                        "structure_content": self.get_step_data().get("structure_content", ""),
                        "completed_chapters": len(chapter_plots),
                    },
                )

                self.save_step_data({
                    "structure_content": self.get_step_data().get("structure_content", ""),
                    "chapter_plots": chapter_plots,
                    "_partial": True,
                })
            
            time.sleep(1)
        
        print(f"Created {len(chapter_plots)} chapter plots")
        return chapter_plots
