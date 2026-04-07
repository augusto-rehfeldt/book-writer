"""
Step 1: Create Structure - Chapter breakdown with plots
"""

import re
import time
from typing import Dict, Any, List, Optional
from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ..utils.name_generator import generate_name_pools, pick_random_name


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

        # --- Generate name pools if not already present ---
        name_pools = self.project_manager.book_data.get("name_pools")
        if not name_pools:
            print("\n🎲 Generating name pools to avoid repetitive AI names...")
            name_pools = generate_name_pools(
                book_idea=init_data.get("book_idea", ""),
                layout_content=init_data.get("layout_content", ""),
                series_layout=init_data.get("series_layout_content", ""),
                ai_service=self.ai_service,
                pool_size=30,
            )
            self.project_manager.book_data["name_pools"] = name_pools
            self.project_manager.save_project()
            if self.glossary_manager:
                self.glossary_manager.set_name_pools(name_pools)
            print(f"✅ Generated {len(name_pools)} name pools.")

        # --- NEW: Replace AI-generated character names with random names from pools ---
        layout_content = init_data.get("layout_content", "")
        if layout_content and name_pools:
            new_layout, replaced_count = self._replace_character_names_with_pools(
                layout_content, name_pools, init_data
            )
            if replaced_count > 0:
                print(f"🎭 Replaced {replaced_count} character names using name pools.")
                init_data["layout_content"] = new_layout
                self.project_manager.set_step_data("init", init_data)
                self.project_manager.save_project()

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

        chapters = self._extract_chapters(structure_content)

        # Create plots for each chapter (now with name pools injected)
        chapter_plots = self._create_plots(chapters, init_data, name_pools)

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

    # -------------------------------------------------------------------------
    # Replace AI‑generated character names with random picks from pools
    # -------------------------------------------------------------------------
    def _replace_character_names_with_pools(
        self, layout_content: str, name_pools: Dict[str, List[str]], init_data: Dict
    ) -> tuple[str, int]:
        # Find the "Main Characters" section
        main_char_section_match = re.search(
            r"(?i)(?:##\s*Main\s+Characters|###\s*Main\s+Characters)(.*?)(?=\n##|\n###|\Z)",
            layout_content,
            re.DOTALL,
        )
        if not main_char_section_match:
            return layout_content, 0

        section_text = main_char_section_match.group(1)
        original_section = section_text
        replaced_count = 0
        new_section_lines = []

        lines = section_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            # Look for a line that contains a bold name
            bold_name_match = re.search(r"\*\*([^*]+)\*\*", stripped)
            if bold_name_match and not stripped.startswith(("Role", "Description", "- **Role")):
                old_name = bold_name_match.group(1).strip()
                # Determine a suitable pool category
                pool_category = "protagonists"
                role_hint = ""
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if "Role:" in next_line or "role:" in next_line:
                        role_hint = next_line
                if "antagonist" in role_hint.lower():
                    pool_category = "antagonists"
                elif "sidekick" in role_hint.lower() or "support" in role_hint.lower():
                    pool_category = "supporting_characters"
                
                # Pick a random name from the pool
                new_name = pick_random_name(name_pools, pool_category, "any_character")
                if not new_name:
                    for pool in name_pools.values():
                        if pool:
                            new_name = pool[0]
                            break
                if new_name and new_name != old_name:
                    # Replace in the line
                    new_line = line.replace(f"**{old_name}**", f"**{new_name}**")
                    new_section_lines.append(new_line)
                    replaced_count += 1
                    # Update glossary manager
                    if self.glossary_manager:
                        desc_lines = []
                        j = i + 1
                        while j < len(lines) and not re.search(r"\*\*[^*]+\*\*", lines[j].strip()):
                            desc_lines.append(lines[j].strip())
                            j += 1
                        description = " ".join(desc_lines).strip()
                        if description:
                            self.glossary_manager.add_character(new_name, description)
                    i += 1
                    continue
            new_section_lines.append(line)
            i += 1

        if replaced_count == 0:
            return layout_content, 0

        new_section_text = "\n".join(new_section_lines)
        new_layout = layout_content.replace(original_section, new_section_text)
        return new_layout, replaced_count

    def _create_structure(self, init_data: Dict) -> str:
        print("🔄 Generating chapter structure...")
        prompt, max_completion_tokens = self._build_structure_prompt(init_data)
        structure = self.ai_service.generate_content(prompt, max_completion_tokens=max_completion_tokens)

        # Interactive loop for adjusting structure
        while True:
            print("\n📚 CURRENT CHAPTER STRUCTURE:")
            print("-" * 50)
            print(structure)
            print("-" * 50)

            feedback = input("\nPress Enter to accept this structure, or type feedback to revise it: ").strip()
            if not feedback:
                break

            print("\n🔄 Revising chapter structure based on your feedback...")
            rev_prompt = self.ai_service.build_sectioned_prompt(
                instruction="Revise the chapter structure based on the user's feedback. Maintain the exact same output formatting rules as before (numbered list/table format, opening style tags, word count estimates).",
                sections=[
                    ("Current Structure", structure),
                    ("User Feedback", feedback)
                ],
                max_prompt_tokens=6000,
                section_token_caps={
                    "Current Structure": 3000,
                    "User Feedback": 500
                }
            )
            structure = self.ai_service.generate_content(rev_prompt, max_completion_tokens=max_completion_tokens)

        return structure

    def _build_structure_prompt(self, init_data: Dict) -> tuple[str, int]:
        is_groq = getattr(self.ai_service, "provider", "") == "groq"
        book_idea = self._truncate_text(init_data.get("book_idea", ""), 350 if is_groq else 500)
        layout_content = self._truncate_text(init_data.get("layout_content", ""), 1400 if is_groq else 3500)
        series_layout = self._truncate_text(init_data.get("series_layout_content", ""), 900 if is_groq else 1800)
        series_mode = bool(init_data.get("series_mode"))
        opening_style_list = (
            "in medias res, dialogue-led, sensory close-up, object-focused, institutional briefing, "
            "procedural action, quiet reflection, cross-cut, suspense hook"
        )

        if is_groq:
            prompt = self.ai_service.build_sectioned_prompt(
                instruction=(
                    "Create a compact chapter outline as a markdown table for the current book. "
                    "Return ONLY the table, with no extra commentary."
                ),
                sections=[
                    ("Book idea", book_idea),
                    ("Layout summary", layout_content),
                    *(
                        [("Series layout", series_layout)]
                        if series_mode and series_layout
                        else []
                    ),
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
                    "Series layout": 450,
                    "Output rules": 260,
                },
            )
            return prompt, 1600

        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                "Create chapter structure from the provided context for the current book. "
                "Generate 20-30 chapters and return ONLY a plain numbered list."
            ),
            sections=[
                ("Book idea", book_idea),
                ("Layout summary", layout_content),
                *(
                    [("Series layout", series_layout)]
                    if series_mode and series_layout
                    else []
                ),
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
                "Series layout": 1200,
                "Output rules": 450,
            },
        )
        return prompt, 4096
    
    def _extract_chapters(self, content: str) -> List[Dict]:
        chapters = []
        lines = content.split('\n')
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
    
    def _create_plots(self, chapters: List[Dict], init_data: Dict, name_pools: Dict[str, List[str]]) -> Dict:
        chapter_plots = dict(self.get_step_data().get("chapter_plots", {}))
        series_layout = self._truncate_text(init_data.get("series_layout_content", ""), 900)

        name_pools_text = ""
        if name_pools:
            name_pools_text = "AVAILABLE NAME POOLS (use names from these lists when introducing new characters):\n"
            for cat, names in list(name_pools.items())[:10]:
                sample = ", ".join(names[:8])
                name_pools_text += f"- {cat}: {sample}{'...' if len(names) > 8 else ''}\n"

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
                    "Be detailed but concise. "
                    "When introducing new characters, use names from the provided name pools. "
                    "If a character already exists in the glossary, reuse that name."
                ),
                sections=[
                    ("Book summary", init_data["book_idea"][:500]),
                    *([("Series layout", series_layout)] if series_layout else []),
                    ("Chapter summary", chapter.get("content", "")),
                    ("Opening style tag", chapter.get("opening_style", "")),
                    ("Requirements", "Opening scene; 3-5 key events; character interactions; conflict/tension; chapter ending/transition; emotional beats."),
                    ("Glossary context", glossary_context),
                    ("Name pools", name_pools_text),
                ],
                max_prompt_tokens=7000,
                section_token_caps={
                    "Book summary": 200,
                    "Series layout": 500,
                    "Chapter summary": 1000,
                    "Opening style tag": 60,
                    "Requirements": 250,
                    "Glossary context": 500,
                    "Name pools": 600,
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