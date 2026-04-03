from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_book_creator.core.project_manager import ProjectManager
from ai_book_creator.steps.step_1_structure import StructureStep


class _CheckpointAI:
    provider = "openai"

    def build_sectioned_prompt(self, instruction, sections, max_prompt_tokens=None, section_token_caps=None, safety_margin=None):
        return instruction

    def generate_content(self, prompt, max_completion_tokens=None):
        return "A focused chapter plot."


class StructureCheckpointTests(unittest.TestCase):
    def test_create_plots_saves_checkpoint_for_each_chapter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            pm = ProjectManager(str(project_dir))
            pm.book_data["structure"] = {"structure_content": "1. Chapter 1 - Test: Summary.", "chapter_plots": {}}

            step = StructureStep(_CheckpointAI(), pm, None)
            chapters = [
                {
                    "title": "Test",
                    "content": "Summary",
                    "chapter_number": 1,
                    "opening_style": "dialogue-led",
                    "word_count_estimate": 1300,
                }
            ]

            chapter_plots = step._create_plots(chapters, {"book_idea": "Test idea"})

            checkpoint_file = project_dir / "checkpoint_structure_chapter_01.json"
            self.assertTrue(checkpoint_file.exists())
            self.assertIn("chapter_1", chapter_plots)

            checkpoint_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint_data["data"]["chapter_key"], "chapter_1")
            self.assertEqual(checkpoint_data["data"]["completed_chapters"], 1)
            self.assertIn("chapter_plot", checkpoint_data["data"])


if __name__ == "__main__":
    unittest.main()
