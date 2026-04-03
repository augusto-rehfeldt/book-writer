"""
Step 0: Initialize Book - Concept, scope, layout, and character setup
"""

from datetime import datetime
from typing import Dict, Any
import math

from .base_step import BaseStep
from ..utils.text_utils import (
    calculate_page_count,
    pages_to_words,
    estimate_tokens_from_words
)


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

        book_idea = self._get_concept(is_series)

        # ✅ FIX: pages-first
        page_count = self._get_page_count(is_series)
        target_word_count = pages_to_words(page_count)

        estimate = self._estimate_total_tokens(
            target_word_count=target_word_count,
            series_mode=is_series,
            series_book_count=series_book_count,
        )

        self._print_token_estimate(
            estimate, target_word_count, page_count, is_series, series_book_count
        )

        # ✅ NEW: confirmation step
        confirm = input("\nProceed with generation? (y/n): ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            exit()

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
            choice = input("\n1. Single book\n2. Series of books\n\nChoice (1/2): ").strip()

            if choice in ("", "1"):
                return "single", 1
            if choice == "2":
                return "series", self._get_series_book_count()

            print("Enter 1 or 2.")

    def _get_series_book_count(self) -> int:
        while True:
            try:
                count = int(input("\nHow many books are in the series? ").strip())
                if count > 1:
                    return count
            except ValueError:
                pass
            print("Enter a valid number > 1.")

    def _get_concept(self, is_series: bool) -> str:
        choice = input("\n1. Provide book idea\n2. Get AI inspiration\n\nChoice (1/2): ").strip()

        if choice == "2":
            print("\n🎨 AI Book Ideas:")
            print("-" * 50)
            idea_kind = "series ideas" if is_series else "book ideas"
            inspiration = self.ai_service.generate_content(
                f"Generate 5 unique {idea_kind}.",
                max_completion_tokens=1024,
            )
            print(inspiration)
            print("-" * 50)
            return input("\nChoose or type your own: ").strip()

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

    # ✅ FIXED: realistic scaling
    def _estimate_chapter_count(self, target_word_count: int) -> int:
        return max(3, min(20, target_word_count // 800))

    # ✅ FIXED: simple + accurate tokens
    def _estimate_total_tokens(
        self,
        target_word_count: int,
        series_mode: bool,
        series_book_count: int,
    ) -> Dict[str, int]:

        tokens_per_book = estimate_tokens_from_words(target_word_count)

        run_total = tokens_per_book
        series_total = tokens_per_book * series_book_count if series_mode else tokens_per_book

        return {
            "chapter_count": self._estimate_chapter_count(target_word_count),
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

        print(f"Target length: {page_count} pages (~{target_word_count} words)")
        print(f"Estimated chapter count: {estimate['chapter_count']}")
        print(f"Estimated tokens per book: {estimate['run_total_tokens']:,}")

        if series_mode:
            print(
                f"Estimated full-series tokens: {estimate['series_total_tokens']:,}"
            )