"""
Step 0: Initialize Book - Concept, scope, layout, and character setup
"""

from datetime import datetime
from typing import Dict, Any
import math

from .base_step import BaseStep
from ..utils.text_utils import calculate_page_count, pages_to_words


class InitStep(BaseStep):
    def __init__(self, ai_service, project_manager):
        super().__init__(ai_service, project_manager)
        self.step_name = "init"
    
    def should_execute(self) -> bool:
        return not self.is_completed()
    
    def get_step_header(self) -> str:
        return "="*60 + "\n📚 STEP 0: BOOK INITIALIZATION\n" + "="*60
    
    def execute(self) -> Dict[str, Any]:
        scope_type, series_book_count = self._get_scope()
        is_series = scope_type == "series"

        # Get concept
        book_idea = self._get_concept(is_series)

        # Get target length in pages, then convert to words for downstream steps
        page_count = self._get_page_count(is_series)
        target_word_count = pages_to_words(page_count)

        estimate = self._estimate_total_tokens(
            target_word_count=target_word_count,
            series_mode=is_series,
            series_book_count=series_book_count,
        )
        self._print_token_estimate(estimate, target_word_count, page_count, is_series, series_book_count)

        choice = input("\nProceed with generation? (y/n): ").strip().lower()
        if choice not in ('y', 'yes'):
            print("Generation aborted.")
            import sys
            sys.exit(0)

        partial_init_data = {
            "book_idea": book_idea,
            "scope_type": scope_type,
            "series_mode": is_series,
            "series_book_count": series_book_count,
            "target_word_count": target_word_count,
            "page_count": page_count,
            "_partial": True
        }
        self.save_step_data(partial_init_data)

        series_layout_content = ""
        if is_series:
            series_layout_content = self._generate_series_layout(
                book_idea=book_idea,
                series_book_count=series_book_count,
                target_word_count=target_word_count,
                page_count=page_count,
            )
            partial_init_data["series_layout_content"] = series_layout_content
            self.save_step_data(partial_init_data)

        # Generate layout
        layout_content = self._generate_layout(book_idea, page_count, series_layout_content)
        
        init_data = {
            "book_idea": book_idea,
            "scope_type": scope_type,
            "series_mode": is_series,
            "series_book_count": series_book_count,
            "target_word_count": target_word_count,
            "page_count": page_count,
            "layout_content": layout_content,
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
                (
                    f"Generate 5 unique {idea_kind}. For each: Title, Genre, "
                    "Brief premise (2-3 sentences), and a short note on the core arc. Number them 1-5."
                ),
                max_completion_tokens=1024,
            )
            print(inspiration)
            print("-" * 50)
            
            selection = input("\nEnter number (1-5) or type your own: ").strip()
            if selection.isdigit() and 1 <= int(selection) <= 5:
                ideas = [i.strip() for i in inspiration.split('\n\n') if i.strip()]
                return ideas[int(selection) - 1] if int(selection) <= len(ideas) else input("Enter your idea: ")
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

    def _generate_layout(self, book_idea: str, page_count: int, series_layout_content: str = "") -> str:
        print("\n🔄 Generating book layout...")

        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                f"Create a book layout targeting {page_count} pages. "
                "Be concise but complete."
            ),
            sections=[
                ("Book idea", book_idea),
                *(
                    [("Series layout", series_layout_content)]
                    if series_layout_content
                    else []
                ),
                (
                    "Required output",
                    "3 potential titles; genre; target audience; 3-5 main themes; setting overview; "
                    "three-act structure; 5-7 main characters with name, role, and brief description.",
                )
            ],
            max_prompt_tokens=2000,
        )
        
        layout = self.ai_service.generate_content(prompt, max_completion_tokens=1400)
        
        print("\n📋 BOOK LAYOUT:")
        print("-" * 50)
        print(layout)
        print("-" * 50)
        
        return layout

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
        print(
            f"Target length: {target_word_count:,} words per book "
            f"(about {page_count} pages)"
        )
        print(f"Estimated chapter count: {estimate['chapter_count']}")
        print(f"  Setup and layout: {estimate['setup_tokens']:,} tokens")
        if estimate.get("series_layout_tokens", 0):
            print(f"  Series layout: {estimate['series_layout_tokens']:,} tokens")
        print(f"  Structure and chapter plots: {estimate['structure_tokens']:,} tokens")
        print(f"  Chapter writing: {estimate['writing_tokens']:,} tokens")
        print(f"  Review and expansion buffer: {estimate['review_tokens']:,} tokens")
        print(f"  Estimated usage for this run: {estimate['run_total_tokens']:,} tokens")
        if series_mode:
            print(
                f"  Estimated full-series usage: {estimate['series_total_tokens']:,} tokens "
                f"across {series_book_count} books"
            )