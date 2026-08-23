"""
Step 0: Initialize Book - Concept, scope, layout, and character setup
"""

from datetime import datetime
from typing import Dict, Any, Optional
import math
import os
import random
import re

from .base_step import BaseStep
from ..utils.text_utils import pages_to_words, WORDS_PER_PAGE

# ponytail: drives the chapter-count estimate; override at init if a book needs
# longer/shorter chapters. Raise to ~4000 for fewer, longer chapters.
DEFAULT_WORDS_PER_CHAPTER = 3000
CREATIVE_LENSES = (
    "Build the premise around a difficult choice with no clean moral answer.",
    "Combine two genres that rarely share the same story engine.",
    "Let an ordinary object or ritual become essential to the central conflict.",
    "Use a setting whose rules actively complicate every major decision.",
    "Give the apparent antagonist a goal the protagonist could almost support.",
    "Make the protagonist unusually competent at the wrong thing.",
    "Let a minor promise in the opening become costly by the climax.",
    "Use an asymmetric structure: discoveries and reversals need not arrive at regular intervals.",
)


class InitStep(BaseStep):
    def __init__(self, ai_service, project_manager):
        super().__init__(ai_service, project_manager)
        self.step_name = "init"
    
    def should_execute(self) -> bool:
        if self.is_completed():
            return False
        existing = self.get_step_data()
        # If we already have a layout, step is complete
        if existing.get("layout_content"):
            return False
        return True
    
    def get_step_header(self) -> str:
        return "="*60 + "\n📚 STEP 0: BOOK INITIALIZATION\n" + "="*60
    
    def execute(self) -> Dict[str, Any]:
        existing = self.get_step_data()
        
        # Load values from partial save if present
        if existing and existing.get("_partial"):
            book_idea = existing.get("book_idea")
            scope_type = existing.get("scope_type")
            is_series = existing.get("series_mode", False)
            series_book_count = existing.get("series_book_count", 1)
            page_count = existing.get("page_count")
            target_word_count = existing.get("target_word_count")
            chapter_count = existing.get("chapter_count")
            words_per_chapter = existing.get("words_per_chapter")
            creative_lens = existing.get("creative_lens")
            series_layout_content = existing.get("series_layout_content", "")
            proceed_confirmed = existing.get("_proceed_confirmed", False)
            print("\n📂 Resuming from saved initialization data...")
        else:
            book_idea = None
            scope_type = None
            is_series = False
            series_book_count = 1
            page_count = None
            target_word_count = None
            chapter_count = None
            words_per_chapter = None
            creative_lens = None
            series_layout_content = ""
            proceed_confirmed = False

        creative_lens = creative_lens or random.choice(CREATIVE_LENSES)
        
        # 1. Scope
        if scope_type is None:
            scope_type, series_book_count = self._get_scope()
            is_series = scope_type == "series"
        
        # 2. Book idea
        if not book_idea:
            book_idea = self._get_concept(is_series)
        
        # 3. Page count
        if page_count is None:
            page_count = self._get_page_count(is_series)
            target_word_count = pages_to_words(page_count)

        # 3b. Chapter count (user-validated estimate)
        if chapter_count is None:
            chapter_count, words_per_chapter = self._get_chapter_count(target_word_count)

        # Token estimate (always show on resume)
        estimate = self._estimate_total_tokens(
            target_word_count=target_word_count,
            series_mode=is_series,
            series_book_count=series_book_count,
            chapter_count=chapter_count,
            words_per_chapter=words_per_chapter,
        )
        self._print_token_estimate(estimate, target_word_count, page_count, is_series, series_book_count)
        
        # Confirmation
        if not proceed_confirmed and not self._is_auto():
            choice = input("\nProceed with generation? (Y/n): ").strip().lower() or "y"
            if choice not in ('y', 'yes'):
                print("Generation aborted.")
                import sys
                sys.exit(0)
            proceed_confirmed = True
        
        # Save partial data after user inputs
        partial = {
            "book_idea": book_idea,
            "scope_type": scope_type,
            "series_mode": is_series,
            "series_book_count": series_book_count,
            "target_word_count": target_word_count,
            "page_count": page_count,
            "chapter_count": chapter_count,
            "words_per_chapter": words_per_chapter,
            "creative_lens": creative_lens,
            "_partial": True,
            "_proceed_confirmed": proceed_confirmed
        }
        self.save_step_data(partial)
        
        # Generate series layout if needed and not already present
        if is_series and not series_layout_content:
            series_layout_content = self._generate_series_layout(
                book_idea, series_book_count, target_word_count, page_count
            )
            partial["series_layout_content"] = series_layout_content
            self.save_step_data(partial)
        
        # Extract book‑specific section for Book 1
        book_specific_layout = ""
        if is_series and series_layout_content:
            book_specific_layout = self._extract_book_section(series_layout_content, book_number=1)
        
        # Generate layout if missing
        layout_content = existing.get("layout_content")
        if not layout_content:
            layout_content = self._generate_layout(
                book_idea, page_count, book_specific_layout, creative_lens
            )

        book_title = existing.get("book_title") or self._choose_book_title(layout_content)
        
        # Keep the selected title for duplicate prevention across a series.
        book_titles = existing.get("book_titles", [])
        if book_title and book_title not in book_titles:
            book_titles.append(book_title)
        
        # Final data
        init_data = {
            "book_idea": book_idea,
            "scope_type": scope_type,
            "series_mode": is_series,
            "series_book_count": series_book_count,
            "target_word_count": target_word_count,
            "page_count": page_count,
            "chapter_count": chapter_count,
            "words_per_chapter": words_per_chapter,
            "creative_lens": creative_lens,
            "layout_content": layout_content,
            "book_title": book_title,
            "author_name": os.getenv("AI_BOOK_AUTHOR", "AI Book Creator").strip() or "AI Book Creator",
            "book_titles": book_titles,
            "timestamp": datetime.now().isoformat()
        }
        if series_layout_content:
            init_data["series_layout_content"] = series_layout_content
        
        self.save_step_data(init_data)
        self.mark_completed()
        return init_data
    
    def _get_scope(self) -> tuple[str, int]:
        if self._is_auto():
            series_count = max(1, int(os.getenv("AI_BOOK_SERIES_COUNT", "1")))
            return ("series", series_count) if series_count > 1 else ("single", 1)
        while True:
            choice = input(
                "\n1. Single book\n2. Series of books\n\nChoice (1/2): "
            ).strip()
            if choice in ("", "1"):
                return "single", 1
            if choice == "2":
                return "series", self._get_series_book_count()
            print("Enter 1 for a single book or 2 for a series.")
    
    def _get_series_book_count(self) -> int:
        while True:
            try:
                count = int(input("\nHow many books are in the series? ").strip())
                if count > 1:
                    return count
            except ValueError:
                pass
            print("Enter a valid number greater than 1.")
    
    def _get_concept(self, is_series: bool) -> str:
        if self._is_auto():
            idea_kind = "series ideas" if is_series else "standalone book ideas"
            inspiration = self.ai_service.generate_content(
                f"Pitch 7 genuinely distinct {idea_kind}. Cross genres, settings, character types, "
                "sources of conflict, narrative shapes, and emotional tones. Avoid retellings, stock "
                "chosen-one plots, generic titles, and premises that differ only cosmetically. For each "
                "give a title, genre, 2-3 sentence premise, story engine, and central character dilemma.",
                max_completion_tokens=2200,
            )
            prompt = self.ai_service.build_sectioned_prompt(
                instruction=(
                    "Act as a demanding commissioning editor. Select the one pitch with the strongest "
                    "combination of originality, sustained story engine, emotional pressure, and a clear "
                    "reader promise. Improve its weak spots. Return only one self-contained book concept "
                    "with a working title, genre, premise, protagonist, central conflict, and ending direction."
                ),
                sections=[("Candidate pitches", inspiration)],
                max_prompt_tokens=3500,
            )
            return self.ai_service.generate_content(prompt, max_completion_tokens=1000).strip()

        choice = input("\n1. Provide book idea\n2. Get AI inspiration\n\nChoice (1/2): ").strip()
        if choice == "2":
            print("\n🎨 AI Book Ideas:")
            print("-" * 50)
            idea_kind = "series ideas" if is_series else "book ideas"
            inspiration = self.ai_service.generate_content(
                f"Generate 5 unique {idea_kind}. For each: Title, Genre, "
                "Brief premise (2-3 sentences), and a short note on the core arc. Number them 1-5.",
                max_completion_tokens=1024,
            )
            print(inspiration)
            print("-" * 50)
            selection = input("\nEnter number (1-5) or type your own: ").strip()
            if selection.isdigit() and 1 <= int(selection) <= 5:
                ideas = [i.strip() for i in inspiration.split('\n\n') if i.strip()]
                return ideas[int(selection)-1] if int(selection) <= len(ideas) else input("Enter your idea: ")
            return selection
        return input("\nEnter your book idea: ").strip()
    
    def _get_page_count(self, is_series: bool) -> int:
        if self._is_auto():
            low, high = self._parse_range(os.getenv("AI_BOOK_PAGE_RANGE", "180-280"), 20)
            return random.randint(low, high)
        prompt = "\nDesired page count per book: "
        if is_series:
            prompt = "\nDesired page count per book in the series: "
        while True:
            try:
                count = int(input(prompt))
                if count > 0:
                    return count
            except ValueError:
                pass
            print("Enter a valid number")

    def _get_chapter_count(self, target_word_count: int) -> tuple[int, int]:
        """Show an estimated chapter count and let the user override it.

        Returns (chapter_count, words_per_chapter). The estimate scales with
        book length off DEFAULT_WORDS_PER_CHAPTER; the user can accept it or
        type a different count, and words_per_chapter is recomputed.
        """
        est = self._estimate_chapter_count(target_word_count)
        wpc = max(800, target_word_count // est)
        pages_each = max(1, round(wpc / WORDS_PER_PAGE))
        print(
            f"\n📈 Estimated chapters: ~{est} "
            f"(≈{wpc:,} words per chapter, ~{pages_each} pages each)"
        )
        configured_range = os.getenv("AI_BOOK_CHAPTER_RANGE", "").strip()
        if self._is_auto() or configured_range:
            if configured_range:
                low, high = self._parse_range(configured_range, 5)
            else:
                low, high = max(5, round(est * 0.75)), max(5, round(est * 1.25))
            count = random.randint(low, high)
            return count, max(800, target_word_count // count)

        while True:
            try:
                low, high = max(5, round(est * 0.85)), max(5, round(est * 1.15))
                raw = input(
                    f"Enter chapter count or range (Enter for randomized {low}-{high}): "
                ).strip()
            except (EOFError, StopIteration):
                count = random.randint(low, high)
                return count, max(800, target_word_count // count)
            if not raw:
                low, high = max(5, round(est * 0.85)), max(5, round(est * 1.15))
                count = random.randint(low, high)
                return count, max(800, target_word_count // count)
            try:
                if "-" in raw:
                    low, high = self._parse_range(raw, 5)
                    count = random.randint(low, high)
                else:
                    count = int(raw)
            except ValueError:
                print("Enter a whole number (>=5) or press Enter to accept the estimate.")
                continue
            if count < 5:
                print("Use at least 5 chapters.")
                continue
            wpc_new = max(800, target_word_count // count)
            pages_new = max(1, round(wpc_new / WORDS_PER_PAGE))
            print(f"  -> {count} chapters (≈{wpc_new:,} words / ~{pages_new} pages each)")
            return count, wpc_new
    
    def _generate_series_layout(
        self,
        book_idea: str,
        series_book_count: int,
        target_word_count: int,
        page_count: int,
    ) -> str:
        print("\n🔄 Generating series layout...")
        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                f"Create a series layout for {series_book_count} books. "
                "Focus on the overarching arc, how each book escalates the stakes, "
                "and how the current volume can open the series cleanly."
            ),
            sections=[
                ("Book idea", book_idea),
                ("Target length per book", f"{target_word_count:,} words / about {page_count} pages"),
                (
                    "Required output",
                    "Series title; central premise; book-by-book arc; recurring characters; "
                    "major turning points; ending position for each book; tone and audience notes.",
                ),
            ],
            max_prompt_tokens=2500,
            section_token_caps={
                "Book idea": 250,
                "Target length per book": 80,
                "Required output": 700,
            },
        )
        series_layout = self.ai_service.generate_content(prompt, max_completion_tokens=32000)
        print("\n📚 SERIES LAYOUT:")
        print("-" * 50)
        print(series_layout)
        print("-" * 50)
        return series_layout
    
    def _extract_book_section(self, series_layout: str, book_number: int) -> str:
        """Extract only the section for a specific book from the series layout."""
        pattern = rf"(?i)(?:##|###)\s*Book\s+{book_number}\s*:?(.*?)(?=(?:##|###)\s*Book\s+{book_number + 1}|$)"
        match = re.search(pattern, series_layout, re.DOTALL)
        if match:
            return f"Focus ONLY on Book {book_number} content:\n{match.group(1).strip()}"
        return f"IMPORTANT: Generate layout ONLY for Book {book_number} of the series. Ignore later book details.\n\n{series_layout}"
    
    def _generate_plot_options(self, book_idea: str, series_layout_content: str = "") -> str:
        print("\n🔄 Generating plot options to choose from...")
        sections = [("Book idea", book_idea)]
        if series_layout_content:
            sections.append(("Series layout context", series_layout_content))

        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                "Generate 4 structurally distinct plot directions for this specific book. Vary the "
                "protagonist's strategy, source of pressure, revelation pattern, cost of success, climax, "
                "and ending flavor—not just surface details. Reject the first obvious solution to the premise. "
                "Number them Option 1 through Option 4."
            ),
            sections=sections,
            max_prompt_tokens=2500,
            section_token_caps={
                "Book idea": 500,
                "Series layout context": 1000
            }
        )
        options_text = self.ai_service.generate_content(prompt, max_completion_tokens=32000)

        if self._is_auto():
            selection_prompt = self.ai_service.build_sectioned_prompt(
                instruction=(
                    "Choose the plot direction that is least interchangeable with a generic genre novel "
                    "while still supporting a full book. Strengthen causality and escalation, then return "
                    "only the selected, revised plot direction."
                ),
                sections=[("Book idea", book_idea), ("Plot options", options_text)],
                max_prompt_tokens=5000,
            )
            return self.ai_service.generate_content(
                selection_prompt, max_completion_tokens=1800
            ).strip()

        while True:
            print("\n" + "="*50)
            print("🎲 PLOT OPTIONS:")
            print("="*50)
            print(options_text)
            print("="*50)

            try:
                choice = input("\nWhich option do you prefer? (1/2/3/4) or type custom feedback to regenerate: ").strip()
            except (EOFError, StopIteration):
                choice = "1"
            if choice in ['1', '2', '3', '4']:
                return f"Selected Option {choice} from the following proposals:\n\n{options_text}"
            elif choice:
                print("\n🔄 Regenerating with your feedback...")
                # Update sections with feedback
                rev_sections = sections.copy()
                rev_sections.append(("Current Options", options_text))
                rev_sections.append(("User Feedback", choice))

                rev_prompt = self.ai_service.build_sectioned_prompt(
                    instruction="Based on the book idea and user feedback, generate 3 NEW distinct plot directions.",
                    sections=rev_sections,
                    max_prompt_tokens=4000,
                    section_token_caps={
                        "Book idea": 300,
                        "Series layout context": 500,
                        "Current Options": 1500,
                        "User Feedback": 500
                    }
                )
                options_text = self.ai_service.generate_content(rev_prompt, max_completion_tokens=32000)

    def _generate_layout(
        self,
        book_idea: str,
        page_count: int,
        series_layout_content: str = "",
        creative_lens: str = "",
    ) -> str:
        # Step 1: Provide multiple options
        plot_direction = self._generate_plot_options(book_idea, series_layout_content)

        print("\n🔄 Generating full book layout based on selected plot...")
        sections = [
            ("Book idea", book_idea),
            ("Chosen Plot Direction", plot_direction),
            ("Creative constraint", creative_lens),
        ]
        if series_layout_content:
            sections.append(("Series layout", series_layout_content))
        sections.append((
            "Required output",
            "Start with 3 ranked potential titles as a plain numbered list. Titles must use different "
            "syntactic shapes and imagery; avoid generic genre nouns, clichés, subtitles, and near-duplicates. "
            "Then give genre; target audience; 3-5 main themes; setting overview; "
            "three-act structure; 5-7 main characters with name, role, and brief description.",
        ))
        
        prompt = self.ai_service.build_sectioned_prompt(
            instruction=f"Create a book layout targeting {page_count} pages. Be concise but complete.",
            sections=sections,
            max_prompt_tokens=4000,
        )
        layout = self.ai_service.generate_content(prompt, max_completion_tokens=8000)

        if self._is_auto():
            print("\n📋 AUTOMATIC BOOK LAYOUT:")
            print("-" * 50)
            print(layout)
            print("-" * 50)
            return layout

        # Step 2: Interactive review of Layout
        while True:
            print("\n📋 CURRENT BOOK LAYOUT:")
            print("-" * 50)
            print(layout)
            print("-" * 50)

            try:
                feedback = input("\nPress Enter to accept this layout, or type feedback to revise it: ").strip()
            except (EOFError, StopIteration):
                feedback = ""
            if not feedback:
                break

            print("\n🔄 Revising layout based on your feedback...")
            rev_sections = [
                ("Current Layout", layout),
                ("User Feedback", feedback),
                ("Required output", "Revise the layout incorporating the user feedback. Maintain the required output format.")
            ]
            rev_prompt = self.ai_service.build_sectioned_prompt(
                instruction="Update the book layout based on the user's feedback.",
                sections=rev_sections,
                max_prompt_tokens=4000
            )
            layout = self.ai_service.generate_content(rev_prompt, max_completion_tokens=8000)

        return layout
    
    def _extract_first_title(self, layout_content: str) -> str:
        match = re.search(r'(?m)^\s*1\.\s*(.+?)(?:\n|$)', layout_content)
        if match:
            return match.group(1).strip()
        match = re.search(r'(?i)title[:\s]+(.+?)(?:\n|$)', layout_content)
        if match:
            return match.group(1).strip()
        for line in layout_content.splitlines():
            line = line.strip()
            if line and not line.startswith(('•', '-', '*')) and len(line) < 80 and line[0].isupper():
                return line
        return ""

    def _choose_book_title(self, layout_content: str) -> str:
        titles = []
        for match in re.findall(r"(?m)^\s*[1-3]\.\s*(.+?)\s*$", layout_content):
            title = re.sub(r"[*_`#]", "", match).strip().strip('"')
            title = re.sub(r"(?i)^title\s*:\s*", "", title).strip()
            if 2 < len(title) <= 120 and title not in titles:
                titles.append(title)
        if not titles:
            return self._extract_first_title(layout_content)
        if self._is_auto() or len(titles) == 1:
            return titles[0]
        print("\nPotential titles:")
        for index, title in enumerate(titles, 1):
            print(f"  {index}. {title}")
        try:
            choice = input(f"Choose title [1-{len(titles)}, default 1]: ").strip()
        except (EOFError, StopIteration):
            choice = ""
        return titles[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(titles) else titles[0]

    @staticmethod
    def _parse_range(value: str, minimum: int) -> tuple[int, int]:
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", value)
        if not match:
            raise ValueError(f"Expected a number or range like 12-18, got: {value!r}")
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if low < minimum or high < low:
            raise ValueError(f"Range must be at least {minimum} and ordered low-to-high.")
        return low, high

    @staticmethod
    def _is_auto() -> bool:
        return os.getenv("AI_BOOK_MODE", "review").strip().lower() == "auto"
    
    def _estimate_chapter_count(self, target_word_count: int) -> int:
        # ponytail: no upper cap — long books get more, shorter chapters.
        return max(10, math.ceil(max(1, target_word_count) / DEFAULT_WORDS_PER_CHAPTER))

    def _estimate_total_tokens(
        self,
        target_word_count: int,
        series_mode: bool,
        series_book_count: int,
        chapter_count: Optional[int] = None,
        words_per_chapter: Optional[int] = None,
    ) -> Dict[str, int]:
        if not chapter_count:
            chapter_count = self._estimate_chapter_count(target_word_count)
        if not words_per_chapter:
            words_per_chapter = max(800, target_word_count // max(1, chapter_count))
        setup_tokens = 3600
        series_layout_tokens = 0
        if series_mode:
            series_layout_tokens = 2200 + (series_book_count * 350)
        structure_tokens = 9200 + (chapter_count * 8400)
        writing_tokens = chapter_count * 7100
        review_tokens = max(15000, int(target_word_count * 2.0))
        per_book_total = setup_tokens + structure_tokens + writing_tokens + review_tokens
        run_total = per_book_total + series_layout_tokens
        series_total = run_total
        if series_mode:
            series_total = series_layout_tokens + (series_book_count * per_book_total)
        return {
            "setup_tokens": setup_tokens,
            "series_layout_tokens": series_layout_tokens,
            "structure_tokens": structure_tokens,
            "writing_tokens": writing_tokens,
            "review_tokens": review_tokens,
            "chapter_count": chapter_count,
            "words_per_chapter": words_per_chapter,
            "run_total_tokens": run_total,
            "series_total_tokens": series_total,
        }
    
    def _print_token_estimate(
        self,
        estimate: Dict[str, int],
        target_word_count: int,
        page_count: int,
        series_mode: bool,
        series_book_count: int,
    ) -> None:
        print("\n📈 Estimated token usage:")
        if series_mode:
            print(f"Series size: {series_book_count} books")
        print(f"Target length: {target_word_count:,} words per book (about {page_count} pages)")
        print(f"Estimated chapter count: {estimate['chapter_count']} "
              f"(≈{estimate.get('words_per_chapter', 0):,} words per chapter)")
        print(f"  Setup and layout: {estimate['setup_tokens']:,} tokens")
        if estimate.get("series_layout_tokens", 0):
            print(f"  Series layout: {estimate['series_layout_tokens']:,} tokens")
        print(f"  Structure and chapter plots: {estimate['structure_tokens']:,} tokens")
        print(f"  Chapter writing: {estimate['writing_tokens']:,} tokens")
        print(f"  Review and expansion buffer: {estimate['review_tokens']:,} tokens")
        print(f"  Estimated usage for this run: {estimate['run_total_tokens']:,} tokens")
        if series_mode:
            print(f"  Estimated full-series usage: {estimate['series_total_tokens']:,} tokens across {series_book_count} books")
