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

        is_groq = getattr(self.ai_service, "provider", "") == "groq"
        book_idea = self._truncate_text(init_data.get("book_idea", ""), 350 if is_groq else 500)
        layout_content = self._truncate_text(init_data.get("layout_content", ""), 1400 if is_groq else 3500)

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
                        "Keep Summary to 15-20 words. Keep Key events to at most 3 short phrases separated by semicolons. "
                        "Use 20-24 chapters. Word count must be a single integer like 1400.",
                    ),
                ],
                max_prompt_tokens=3000,
                section_token_caps={
                    "Book idea": 180,
                    "Layout summary": 700,
                    "Output rules": 260,
                },
            )
            structure = self.ai_service.generate_content(prompt, max_completion_tokens=1600)
        else:
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
                        "Each chapter must be on its own line in the form: '1. Chapter 1 - Title'. "
                        "Do not use a table, bullets, headings, or Markdown formatting. "
                        "For each chapter include a 2-3 sentence summary, key events, and a word count estimate of 1200-1500 words.",
                    ),
                ],
                max_prompt_tokens=8000,
                section_token_caps={
                    "Book idea": 250,
                    "Layout summary": 2500,
                    "Output rules": 350,
                },
            )
            structure = self.ai_service.generate_content(prompt)
        
        print("\n📚 CHAPTER STRUCTURE:")
        print("-" * 50)
        print(structure)
        print("-" * 50)
        
        return structure
    
    def _extract_chapters(self, content: str) -> List[Dict]:
        chapters = []
        lines = content.split('\n')
        # Handle both numbered lists and markdown tables.
        numbered_pattern = re.compile(
            r"""^[*\s#]*\d+\.\s*Chapter\s+(\d+)\s*[—–-]\s*["']*(.+?)["']*[*\s#]*$""",
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
                    title = re.sub(r'\*\*', '', match.group(2)).strip()
                    summary = ""
                    key_events = ""
                    word_count_estimate = 1500
                elif table_match:
                    chapter_number = int(table_match.group(1))
                    title = self._clean_title_cell(table_match.group(2))
                    summary = self._clean_cell(table_match.group(3))
                    key_events = self._clean_cell(table_match.group(4))
                    word_count_estimate = self._clean_word_count(table_match.group(5))
                else:
                    chapter_number = int(table_cells[0])
                    title = self._clean_title_cell(table_cells[1])
                    summary = self._clean_cell(table_cells[2])
                    key_events = self._clean_cell(table_cells[3])
                    word_count_estimate = self._clean_word_count(table_cells[4])

                current = {
                    "title": title,
                    "content": "\n".join(
                        [part for part in [summary, key_events] if part]
                    ).strip(),
                    "chapter_number": chapter_number,
                    "word_count_estimate": word_count_estimate,
                }
            elif current and stripped and not stripped.startswith("|"):
                current["content"] += line + "\n"
        
        if current and current.get('content', '').strip():
            chapters.append(current)
        
        print(f"Extracted {len(chapters)} chapters")
        return chapters

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
        text = re.sub(r"^\s*Chapter\s*\d+\s*[—–-]\s*", "", text, flags=re.IGNORECASE)
        return text.strip()

    def _clean_cell(self, text: str) -> str:
        cleaned = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\*\*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _clean_word_count(self, text: str) -> int:
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
                    ("Requirements", "Opening scene; 3-5 key events; character interactions; conflict/tension; chapter ending/transition; emotional beats."),
                    ("Glossary context", glossary_context),
                ],
                max_prompt_tokens=7000,
                section_token_caps={
                    "Book summary": 200,
                    "Chapter summary": 1000,
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
                    "word_count_estimate": int(chapter.get("word_count_estimate", 1500)),
                }
                
                if self.glossary_manager:
                    self.glossary_manager.auto_populate_from_chapter(
                        plot, chapter.get("title", f"Chapter {chapter_number}"), self.ai_service
                    )

                self.save_step_data({
                    "structure_content": self.get_step_data().get("structure_content", ""),
                    "chapter_plots": chapter_plots,
                    "_partial": True,
                })
            
            time.sleep(1)
        
        print(f"Created {len(chapter_plots)} chapter plots")
        return chapter_plots
