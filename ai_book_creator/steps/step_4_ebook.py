"""
Step 4: Ebook Export - Build an EPUB from the reviewed manuscript.
"""

import os
from datetime import datetime
from typing import Dict, Any

from .base_step import BaseStep
from ..core.project_manager import BrokenProjectStateError
from ..utils.ebook_exporter import export_epub


class EbookStep(BaseStep):
    def __init__(self, ai_service, project_manager, output_dir):
        super().__init__(ai_service, project_manager)
        self.step_name = "ebook"
        self.output_dir = output_dir

    def should_execute(self) -> bool:
        existing = self.get_step_data()
        if not self.is_completed():
            return True

        output_file = existing.get("output_file", "")
        if not output_file or not os.path.exists(output_file):
            return True

        return not self._is_current_export(existing)

    def get_step_header(self) -> str:
        return "=" * 60 + "\n📘 STEP 4: EBOOK EXPORT\n" + "=" * 60

    def execute(self) -> Dict[str, Any]:
        written_data = self.project_manager.get_step_data("written")
        reviewed_data = self.project_manager.get_step_data("reviewed")
        init_data = self.project_manager.get_step_data("init")
        chapters = written_data.get("chapters", {})

        if not chapters:
            recovery = self.project_manager.get_recovery_plan()
            raise BrokenProjectStateError(
                "Step 4 cannot run because there are no written chapters to export. "
                f"{recovery['message']}",
                latest_valid_step=recovery.get("latest_valid_step", ""),
                restart_step=recovery.get("restart_step", ""),
                broken_steps=recovery.get("broken_steps", []),
            )

        if not reviewed_data.get("analysis"):
            recovery = self.project_manager.get_recovery_plan()
            raise BrokenProjectStateError(
                "Step 4 cannot run because the manuscript has not been reviewed yet. "
                f"{recovery['message']}",
                latest_valid_step=recovery.get("latest_valid_step", ""),
                restart_step=recovery.get("restart_step", ""),
                broken_steps=recovery.get("broken_steps", []),
            )

        print("📝 Generating a catchy book description for the EPUB metadata...")
        book_idea = init_data.get("book_idea", "")
        analysis = reviewed_data.get("analysis", "")

        if self.ai_service and hasattr(self.ai_service, "build_sectioned_prompt") and hasattr(self.ai_service, "generate_content"):
            prompt = self.ai_service.build_sectioned_prompt(
                instruction=(
                    "Write a catchy, compelling back-cover blurb (book description) for this book. "
                    "Make it engaging for readers, highlighting the stakes, characters, and central conflict. "
                    "Keep it to 2-3 short paragraphs. Output ONLY the description text, no markdown, no quotes."
                ),
                sections=[
                    ("Book Concept", book_idea),
                    ("Book Analysis & Synopsis", analysis)
                ]
            )

            description = self.ai_service.generate_content(
                prompt,
                model_type="writing",
                max_completion_tokens=500
            ).strip()
        else:
            description = (
                f"{book_idea.strip()}\n\n"
                f"{analysis.strip()}"
            ).strip() or "A newly generated book."

        print("📦 Exporting EPUB ebook...")
        output_file = export_epub(self.output_dir, description=description)
        prompt_file = os.path.splitext(output_file)[0] + "_cover_prompt.txt"
        print(f"✅ Ebook exported to: {output_file}")
        print(f"📝 Cover prompt saved to: {prompt_file}")

        ebook_data = {
            "output_file": output_file,
            "prompt_file": prompt_file,
            "description": description,
            "source_chapter_count": len(chapters),
            "source_written_word_count": int(written_data.get("total_word_count", 0)),
            "source_reviewed_word_count": int(reviewed_data.get("total_word_count", 0)),
            "timestamp": datetime.now().isoformat(),
        }

        self.save_step_data(ebook_data)
        self.mark_completed()
        return ebook_data

    def _is_current_export(self, existing: Dict[str, Any]) -> bool:
        written_data = self.project_manager.get_step_data("written")
        reviewed_data = self.project_manager.get_step_data("reviewed")
        chapters = written_data.get("chapters", {})

        return (
            int(existing.get("source_chapter_count", 0)) == len(chapters)
            and int(existing.get("source_written_word_count", 0)) == int(written_data.get("total_word_count", 0))
            and int(existing.get("source_reviewed_word_count", 0)) == int(reviewed_data.get("total_word_count", 0))
            and bool(existing.get("prompt_file", ""))
            and os.path.exists(existing.get("prompt_file", ""))
            and bool(existing.get("description", ""))
        )