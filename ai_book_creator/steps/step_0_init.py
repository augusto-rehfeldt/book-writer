"""
Step 0: Initialize Book - Concept, scope, layout, and character setup
"""

from datetime import datetime
from typing import Dict, Any
import math
import re

from .base_step import BaseStep
from ..utils.text_utils import pages_to_words


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
            series_layout_content = ""
            proceed_confirmed = False
        
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
        
        # Token estimate (always show on resume)
        estimate = self._estimate_total_tokens(
            target_word_count=target_word_count,
            series_mode=is_series,
            series_book_count=series_book_count,
        )
        self._print_token_estimate(estimate, target_word_count, page_count, is_series, series_book_count)
        
        # Confirmation
        if not proceed_confirmed:
            choice = input("\nProceed with generation? (y/n): ").strip().lower()
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
            layout_content = self._generate_layout(book_idea, page_count, book_specific_layout)
        
        # Extract first title for duplicate prevention
        first_title = self._extract_first_title(layout_content)
        book_titles = existing.get("book_titles", [])
        if first_title and first_title not in book_titles:
            book_titles.append(first_title)
        
        # Final data
        init_data = {
            "book_idea": book_idea,
            "scope_type": scope_type,
            "series_mode": is_series,
            "series_book_count": series_book_count,
            "target_word_count": target_word_count,
            "page_count": page_count,
            "layout_content": layout_content,
            "book_titles": book_titles,
            "timestamp": datetime.now().isoformat()
        }
        if series_layout_content:
            init_data["series_layout_content"] = series_layout_content
        
        self.save_step_data(init_data)
        self.mark_completed()
        return init_data
    
    def _get_scope(self) -> tuple[str, int]:
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
        series_layout = self.ai_service.generate_content(prompt, max_completion_tokens=1600)
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
            instruction="Based on the book idea, generate 3 distinct and detailed plot directions/outlines for this specific book. Number them Option 1, Option 2, and Option 3. For each, describe the core conflict, the protagonist's arc, and the main climax.",
            sections=sections,
            max_prompt_tokens=2500,
            section_token_caps={
                "Book idea": 500,
                "Series layout context": 1000
            }
        )
        options_text = self.ai_service.generate_content(prompt, max_completion_tokens=2000)

        while True:
            print("\n" + "="*50)
            print("🎲 PLOT OPTIONS:")
            print("="*50)
            print(options_text)
            print("="*50)

            try:
                choice = input("\nWhich option do you prefer? (1/2/3) or type custom feedback to regenerate: ").strip()
            except (EOFError, StopIteration):
                choice = "1"
            if choice in ['1', '2', '3']:
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
                options_text = self.ai_service.generate_content(rev_prompt, max_completion_tokens=2000)

    def _generate_layout(self, book_idea: str, page_count: int, series_layout_content: str = "") -> str:
        # Step 1: Provide multiple options
        plot_direction = self._generate_plot_options(book_idea, series_layout_content)

        print("\n🔄 Generating full book layout based on selected plot...")
        sections = [
            ("Book idea", book_idea),
            ("Chosen Plot Direction", plot_direction)
        ]
        if series_layout_content:
            sections.append(("Series layout", series_layout_content))
        sections.append((
            "Required output",
            "3 potential titles; genre; target audience; 3-5 main themes; setting overview; "
            "three-act structure; 5-7 main characters with name, role, and brief description.",
        ))
        
        prompt = self.ai_service.build_sectioned_prompt(
            instruction=f"Create a book layout targeting {page_count} pages. Be concise but complete.",
            sections=sections,
            max_prompt_tokens=4000,
        )
        layout = self.ai_service.generate_content(prompt, max_completion_tokens=1500)

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
            layout = self.ai_service.generate_content(rev_prompt, max_completion_tokens=1500)

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
    
    def _estimate_chapter_count(self, target_word_count: int) -> int:
        return max(20, min(30, math.ceil(max(1, target_word_count) / 1500)))
    
    def _estimate_total_tokens(
        self,
        target_word_count: int,
        series_mode: bool,
        series_book_count: int,
    ) -> Dict[str, int]:
        chapter_count = self._estimate_chapter_count(target_word_count)
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
        print(f"Estimated chapter count: {estimate['chapter_count']}")
        print(f"  Setup and layout: {estimate['setup_tokens']:,} tokens")
        if estimate.get("series_layout_tokens", 0):
            print(f"  Series layout: {estimate['series_layout_tokens']:,} tokens")
        print(f"  Structure and chapter plots: {estimate['structure_tokens']:,} tokens")
        print(f"  Chapter writing: {estimate['writing_tokens']:,} tokens")
        print(f"  Review and expansion buffer: {estimate['review_tokens']:,} tokens")
        print(f"  Estimated usage for this run: {estimate['run_total_tokens']:,} tokens")
        if series_mode:
            print(f"  Estimated full-series usage: {estimate['series_total_tokens']:,} tokens across {series_book_count} books")