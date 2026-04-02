from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_book_creator.core.project_manager import ProjectManager
from ai_book_creator.steps.step_4_ebook import EbookStep


class EbookStepTests(unittest.TestCase):
    def test_execute_exports_epub_and_marks_step_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir) / "book_output"
            project_dir.mkdir(parents=True, exist_ok=True)

            chapter_file = project_dir / "chapter_01.txt"
            chapter_file.write_text(
                "# Chapter 1: Opening\n\nThis is the chapter body.",
                encoding="utf-8",
            )

            project_data = {
                "init": {
                    "book_idea": "A test book concept.",
                    "layout_content": "1. **Test Book Title**",
                    "completed": True,
                },
                "written": {
                    "chapters": {
                        "chapter_1": {
                            "title": "Opening",
                            "chapter_number": 1,
                            "filename": chapter_file.name,
                            "word_count": 8,
                        }
                    },
                    "total_word_count": 8,
                    "total_pages": 1,
                    "completed": True,
                },
                "reviewed": {
                    "analysis": "A concise review.",
                    "chapter_count": 1,
                    "total_word_count": 8,
                    "total_pages": 1,
                    "completed": True,
                },
            }
            (project_dir / "project_data.json").write_text(
                json.dumps(project_data, indent=2),
                encoding="utf-8",
            )

            pm = ProjectManager(str(project_dir))
            pm.load_project()
            step = EbookStep(None, pm, str(project_dir))

            self.assertTrue(step.should_execute())

            result = step.execute()

            output_path = Path(result["output_file"])
            self.assertTrue(output_path.exists())
            self.assertTrue(pm.is_step_completed("ebook"))
            self.assertFalse(step.should_execute())

            pm.book_data["written"]["total_word_count"] = 999
            self.assertTrue(step.should_execute())


if __name__ == "__main__":
    unittest.main()
