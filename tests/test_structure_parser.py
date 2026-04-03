from __future__ import annotations

import unittest

from ai_book_creator.steps.step_1_structure import StructureStep


class _PromptStubAI:
    provider = "openai"

    def build_sectioned_prompt(self, instruction, sections, max_prompt_tokens=None, section_token_caps=None, safety_margin=None):
        return instruction


class _DummyPM:
    def get_step_data(self, name: str):
        return {}


class StructureParserTests(unittest.TestCase):
    def setUp(self):
        self.step = StructureStep(_PromptStubAI(), _DummyPM(), None)

    def test_parse_numbered_outline_strips_literal_title_label(self):
        title, summary, key_events, word_count, opening_style = self.step._parse_numbered_outline(
            "Chapter 24 - Title: The Human Cost of Victory: Opening style tag: quiet reflection. "
            "Summary: Each protagonist confronts the consequences. "
            "Key events: moral reckonings; personal losses acknowledged. "
            "Word count estimate: 1300 words"
        )

        self.assertEqual(title, "The Human Cost of Victory")
        self.assertEqual(opening_style, "quiet reflection")
        self.assertIn("Each protagonist confronts the consequences", summary)
        self.assertIn("moral reckonings", key_events)
        self.assertEqual(word_count, 1300)

    def test_parse_numbered_outline_handles_plain_title_summary_format(self):
        title, summary, key_events, word_count, opening_style = self.step._parse_numbered_outline(
            "Chapter 1 - Four Powers, One Sky: The novel opens in 1957. Word count estimate: 1200-1500 words"
        )

        self.assertEqual(title, "Four Powers, One Sky")
        self.assertEqual(summary, "The novel opens in 1957.")
        self.assertEqual(key_events, "")
        self.assertEqual(opening_style, "")
        self.assertEqual(word_count, 1350)


if __name__ == "__main__":
    unittest.main()
