"""
Step 3: Review - Consistency check and summary
"""

import os
from datetime import datetime
from typing import Dict, Any, List, Optional
from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ai_book_creator.utils.text_utils import calculate_page_count, calculate_word_count
from ai_book_creator.models.chapter_model import Chapter


class ReviewStep(BaseStep):
    def __init__(self, ai_service, project_manager, output_dir):
        super().__init__(ai_service, project_manager)
        self.step_name = "reviewed"
        self.output_dir = output_dir
        self.words_per_page = 250
        self._expansion_log: List[str] = []
    
    def should_execute(self) -> bool:
        existing = self.get_step_data()
        if not self.is_completed():
            return True
        if not existing.get("analysis"):
            return True

        written_data = self.project_manager.get_step_data("written")
        current_chapter_count = len(written_data.get("chapters", {}))
        current_word_count = int(written_data.get("total_word_count", 0))

        if existing.get("chapter_count") != current_chapter_count:
            return True
        if existing.get("total_word_count") != current_word_count:
            return True
        return False
    
    def get_step_header(self) -> str:
        return "="*60 + "\n📝 STEP 3: REVIEW\n" + "="*60
    
    def execute(self) -> Dict[str, Any]:
        init_data = self.project_manager.get_step_data("init")
        written_data = self.project_manager.get_step_data("written")
        
        self.words_per_page = init_data.get("words_per_page", self.words_per_page)
        target_pages = init_data.get("page_count", 400)
        
        # Read chapters
        chapters_content = self._read_chapters_content(written_data.get("chapters", {}))
        current_total_words = sum(chapter.word_count for chapter in chapters_content.values())
        current_pages = calculate_page_count(current_total_words, self.words_per_page)

        if not chapters_content or current_total_words <= 0:
            recovery = self.project_manager.get_recovery_plan()
            raise BrokenProjectStateError(
                "Step 3 cannot run because there are no written chapters to review. "
                f"{recovery['message']}",
                latest_valid_step=recovery.get("latest_valid_step", ""),
                restart_step=recovery.get("restart_step", ""),
                broken_steps=recovery.get("broken_steps", []),
            )

        print(
            f"Length check -> target: {target_pages} pages | current: {current_pages} pages ({current_total_words} words)"
        )
        
        # Expand chapters if needed
        updated_chapters_data = self._expand_chapters_if_needed(
            chapters_content, target_pages, init_data
        )
        
        # If chapters were expanded, update project data and re-read content
        if updated_chapters_data:
            written_data["chapters"] = updated_chapters_data
            written_data["total_word_count"] = sum(c["word_count"] for c in updated_chapters_data.values())
            written_data["total_pages"] = calculate_page_count(
                written_data["total_word_count"], self.words_per_page
            )
            self.project_manager.set_step_data("written", written_data)
            chapters_content = self._read_chapters_content(updated_chapters_data)

        if self._expansion_log:
            for log_line in self._expansion_log:
                print(log_line)
            self._expansion_log = []
        
        all_text = self._combine_chapters_text(chapters_content)
        
        print("Analyzing book...")
        
        # Generate analysis
        analysis = self._generate_analysis(init_data, all_text)
        
        # Save
        self._save_analysis(analysis, init_data, written_data, target_pages)
        
        review_data = {
            "analysis": analysis,
            "chapter_count": len(written_data.get("chapters", {})),
            "total_word_count": written_data.get("total_word_count", 0),
            "total_pages": written_data.get("total_pages", 0),
            "timestamp": datetime.now().isoformat()
        }
        
        self.save_step_data(review_data)
        self.mark_completed()
        return review_data
    
    def _read_chapters_content(self, chapters_dict: Dict) -> Dict[str, Chapter]:
        chapters_content = {}
        for chapter_key, chapter_info in chapters_dict.items():
            try:
                with open(chapter_info["filename"], 'r', encoding='utf-8') as f:
                    content = f.read()
                    chapters_content[chapter_key] = Chapter(
                        chapter_info["title"],
                        content,
                        chapter_info.get("chapter_number", int(chapter_key.split('_')[-1]) if 'chapter_' in chapter_key else 0),
                        calculate_word_count(content),
                        chapter_info["filename"]
                    )
            except FileNotFoundError:
                continue
        return chapters_content

    def _combine_chapters_text(self, chapters_content: Dict[str, Chapter]) -> str:
        all_text = ""
        sorted_chapters = sorted(chapters_content.items(), key=lambda x: x[1].chapter_number)
        for _, chapter in sorted_chapters:
            all_text += f"--- {chapter.title} ---\n{chapter.content}\n\n"
        return all_text

    def _expand_chapters_if_needed(
        self, chapters_content: Dict[str, Chapter], target_pages: int, init_data: Dict
    ) -> Optional[Dict[str, Any]]:
        self._expansion_log = []
        total_word_count = sum(chapter.word_count for chapter in chapters_content.values())
        current_pages = calculate_page_count(total_word_count, self.words_per_page)

        if current_pages >= target_pages:
            message = (
                f"Expansion skipped: current length {current_pages} pages already meets target {target_pages} pages."
            )
            self._expansion_log.append(message)
            return None

        target_word_goal = max(target_pages * self.words_per_page, total_word_count + self.words_per_page)
        updated_chapters_data = {k: v.to_dict() for k, v in chapters_content.items()}
        max_iterations = 20
        iteration = 0
        cumulative_added_words = 0
        expansions_made = False

        deficit_pages = target_pages - current_pages
        self._expansion_log.append(
            f"Expansion initiated: deficit {deficit_pages} pages (~{deficit_pages * self.words_per_page} words)."
        )

        while current_pages < target_pages and iteration < max_iterations:
            words_remaining = max(target_word_goal - total_word_count, self.words_per_page // 2)

            chapters_to_expand = []
            for chapter_key, chapter in chapters_content.items():
                chapter_plot = self.project_manager.get_chapter_plot(chapter.chapter_number)
                baseline_estimate = chapter_plot.get("word_count_estimate", 1500) if chapter_plot else 1500
                expansion_ceiling = int(baseline_estimate * 1.6)
                if chapter.word_count >= expansion_ceiling:
                    continue

                min_request = max(int(chapter.word_count * 0.2), 400)
                target_growth = max(int(baseline_estimate * 0.25), min_request)
                request_capacity = expansion_ceiling - chapter.word_count
                default_request = min(max(min_request, target_growth), request_capacity)

                chapters_to_expand.append(
                    (chapter_key, chapter, chapter_plot, request_capacity, min_request, default_request)
                )

            if not chapters_to_expand:
                self._expansion_log.append("Expansion halted: no chapters remain under their safety ceiling.")
                break

            chapters_to_expand.sort(key=lambda item: item[1].word_count)
            expanded_this_cycle = False

            for (
                chapter_key,
                chapter,
                chapter_plot,
                request_capacity,
                min_request,
                default_request,
            ) in chapters_to_expand:
                if current_pages >= target_pages or iteration >= max_iterations:
                    break

                words_remaining = max(target_word_goal - total_word_count, self.words_per_page // 2)
                if request_capacity <= 0:
                    continue

                requested_additional_words = min(
                    request_capacity,
                    max(min_request, min(words_remaining, default_request)),
                )
                if requested_additional_words <= 0:
                    continue

                iteration += 1
                iteration_message = (
                    f"Iteration {iteration}: expanding chapter {chapter.chapter_number} "
                    f"({chapter.title}) targeting +{requested_additional_words} words."
                )
                self._expansion_log.append(iteration_message)

                expanded_content = self._expand_chapter_content(
                    chapter,
                    init_data,
                    chapter_plot or {},
                    requested_additional_words,
                    iteration,
                )
                new_word_count = calculate_word_count(expanded_content)
                added_words = new_word_count - chapter.word_count

                if added_words <= 0:
                    self._expansion_log.append(
                        f"Iteration {iteration}: no net gain for chapter {chapter.chapter_number}. Skipping further updates."
                    )
                    continue

                expansions_made = True
                expanded_this_cycle = True
                cumulative_added_words += added_words
                total_word_count += added_words

                updated_chapter = Chapter(
                    chapter.title,
                    expanded_content,
                    chapter.chapter_number,
                    new_word_count,
                    chapter.filename
                )
                chapters_content[chapter_key] = updated_chapter
                updated_chapters_data[chapter_key] = updated_chapter.to_dict()

                with open(updated_chapters_data[chapter_key]["filename"], 'w', encoding='utf-8') as f:
                    f.write(expanded_content)

                current_pages = calculate_page_count(total_word_count, self.words_per_page)
                self._expansion_log.append(
                    f"Iteration {iteration}: +{added_words} words -> {total_word_count} total words ({current_pages} pages)."
                )

            if not expanded_this_cycle:
                self._expansion_log.append(
                    "Expansion halted: unable to safely expand any remaining chapter this cycle."
                )
                break

        final_pages = calculate_page_count(total_word_count, self.words_per_page)
        summary_message = (
            f"Expansion summary: +{cumulative_added_words} words over {iteration} iterations -> {final_pages} pages."
        )
        self._expansion_log.append(summary_message)

        if final_pages < target_pages:
            self._expansion_log.append(
                "Expansion ended before reaching target due to iteration or safety limits."
            )

        return updated_chapters_data if expansions_made else None

    def _expand_chapter_content(
        self,
        chapter: Chapter,
        init_data: Dict,
        chapter_plot: Dict,
        requested_additional_words: int,
        iteration: int
    ) -> str:
        estimated_pages = max(requested_additional_words / self.words_per_page, 0)
        book_idea = self._truncate_text(init_data.get("book_idea", ""), 500)
        layout_content = self._truncate_text(init_data.get("layout_content", ""), 3000)
        plot_outline = self._truncate_text(chapter_plot.get("plot_outline", "No outline provided."), 3000)
        chapter_content = self._truncate_text(chapter.content, 7000)
        prompt = self.ai_service.build_sectioned_prompt(
            instruction=(
                "You are an expert historical fiction author. Expand the chapter while preserving the core plot points. "
                f"Target roughly {requested_additional_words} new words ({estimated_pages:.1f} pages) during iteration {iteration}. "
                "Return ONLY the expanded chapter content."
            ),
            sections=[
                ("Book concept", book_idea),
                ("Book layout", layout_content),
                ("Chapter title", chapter.title),
                ("Chapter number", str(chapter.chapter_number)),
                ("Chapter plot outline", plot_outline),
                ("Current chapter content", chapter_content),
                (
                    "Expansion requirements",
                    "Maintain all established plot beats, character motivations, and timeline continuity. "
                    "Add richer descriptions, deeper internal monologue, minor interactions or subplots, and more detailed scenes and dialogue. "
                    "Do not summarize; produce fully written prose that can replace the original text verbatim.",
                ),
            ],
            max_prompt_tokens=9000,
            section_token_caps={
                "Book concept": 200,
                "Book layout": 1800,
                "Chapter title": 50,
                "Chapter number": 20,
                "Chapter plot outline": 1200,
                "Current chapter content": 3500,
                "Expansion requirements": 250,
            },
        )
        
        return self.ai_service.generate_content(
            prompt,
            model_type="writing",
            max_completion_tokens=2048,
        )

    def _generate_analysis(self, init_data: Dict, full_text: str) -> str:
        book_idea = self._truncate_text(init_data.get("book_idea", ""), 500)
        layout_content = self._truncate_text(init_data.get("layout_content", ""), 3000)
        text_excerpt = self._truncate_text(full_text, 12000)
        prompt = self.ai_service.build_sectioned_prompt(
            instruction="Analyze this book and be constructive and concise.",
            sections=[
                ("Concept", book_idea),
                ("Layout", layout_content),
                ("Text excerpt", text_excerpt),
                (
                    "Requested output",
                    "1. CONSISTENCY: character, plot, and setting consistency and any issues. "
                    "2. STRENGTHS: what works well. "
                    "3. IMPROVEMENTS: what could be better. "
                    "4. SUMMARY: plot synopsis, character arcs, and themes.",
                ),
            ],
            max_prompt_tokens=9000,
            section_token_caps={
                "Concept": 200,
                "Layout": 1200,
                "Text excerpt": 5000,
                "Requested output": 250,
            },
        )
        
        return self.ai_service.generate_content(
            prompt,
            model_type="review",
            max_completion_tokens=1536,
        )

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."
    
    def _save_analysis(self, analysis: str, init_data: Dict, written_data: Dict, target_pages: int):
        filename = os.path.join(self.output_dir, "book_analysis.txt")
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("BOOK ANALYSIS\n")
            f.write("="*50 + "\n\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Concept: {init_data['book_idea']}\n")
            f.write(f"Chapters: {len(written_data.get('chapters', {}))}\n")
            f.write(f"Words: {written_data.get('total_word_count', 0):,}\n")
            f.write(f"Pages: {written_data.get('total_pages', 0)}\n\n")
            
            f.write("="*60 + "\n")
            f.write("📚 LENGTH VALIDATION\n")
            f.write("="*60 + "\n")
            f.write(f"Target: {target_pages} pages\n")
            f.write(f"Actual: {written_data.get('total_pages', 0)} pages\n")
            if written_data.get('total_pages', 0) < target_pages:
                f.write("⚠️ Book is shorter than target\n")
            else:
                f.write("✅ Book meets or exceeds target length\n")
            f.write("\n")
            
            f.write(analysis)
        
        print(f"Analysis saved to: {filename}")
