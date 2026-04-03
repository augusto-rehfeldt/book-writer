from __future__ import annotations

import tempfile
import unittest

from ai_book_creator.steps.step_1_structure import StructureStep
from ai_book_creator.steps.step_2_write import WriteStep


class _PromptStubAI:
    provider = "openai"

    def build_sectioned_prompt(self, instruction, sections, max_prompt_tokens=None, section_token_caps=None, safety_margin=None):
        lines = [instruction.strip()]
        for heading, text in sections:
            lines.append(f"{heading}: {text.strip()}")
        return "\n\n".join(lines)


class _DummyPM:
    def __init__(self, data: dict[str, object]):
        self.data = data

    def get_step_data(self, name: str):
        return self.data.get(name, {})


class PromptRuleTests(unittest.TestCase):
    def test_structure_prompt_requires_opening_style_tags(self):
        init_data = {
            "book_idea": "Alt-history space race between four powers.",
            "layout_content": "A tense, hard-science alternate history outline.",
        }
        step = StructureStep(_PromptStubAI(), _DummyPM({"init": init_data}), None)
        prompt, _ = step._build_structure_prompt(init_data)

        self.assertIn("Opening style tag:", prompt)
        self.assertIn("vary the opening style tag", prompt.lower())
        self.assertIn("specific, concrete, and distinct", prompt.lower())

    def test_write_prompt_blocks_repetitive_openings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            step = WriteStep(_PromptStubAI(), _DummyPM({"init": {}, "structure": {}}), None, temp_dir)
            prompt = step._build_chapter_prompt(
                {
                    "title": "Four Powers, One Sky",
                    "plot_outline": "A launch rumor shakes four capitals.",
                    "opening_style": "dialogue-led",
                },
                "",
                1200,
            )

        self.assertIn("OPENING STYLE TAG: dialogue-led", prompt)
        self.assertNotIn("Title:", prompt)
        self.assertIn("markdown intentionally", prompt.lower())
        self.assertIn("put the chapter title at the top as a level-1 markdown heading", prompt.lower())
        self.assertIn("use `##` or `###`", prompt.lower())
        self.assertIn("a sensible chapter layout might look like", prompt.lower())
        self.assertIn("Do not begin with \"Again\"", prompt)
        self.assertIn("template openings", prompt)
        self.assertIn("Varied pacing", prompt)


if __name__ == "__main__":
    unittest.main()
