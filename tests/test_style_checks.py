from __future__ import annotations

import unittest

from ai_book_creator.utils.style_checks import analyze_openings, extract_opening_sentence, opening_signature


class StyleCheckTests(unittest.TestCase):
    def test_repetitive_openers_are_flagged(self):
        chapters = [
            "# Chapter 1: Four Powers, One Sky\n\nAgain, Evelyn said, and pointed at the waveform.",
            "# Chapter 2: The Rocket Men\n\nAgain, the numbers were wrong, and everyone knew it.",
            "# Chapter 3: Ashes of the Old War\n\nThe room hummed around her while the men waited.",
        ]

        result = analyze_openings(chapters)

        self.assertFalse(result.passes)
        self.assertGreaterEqual(result.duplicate_openers, 1)
        self.assertGreaterEqual(result.consecutive_duplicate_openers, 1)
        self.assertGreaterEqual(result.banned_phrase_hits, 2)

    def test_varied_openers_pass(self):
        chapters = [
            "# Chapter 1: Four Powers, One Sky\n\nEvelyn slammed the relay into place as the trace jumped.",
            "# Chapter 2: The Rocket Men\n\n\"Again,\" Mercer said, but the room was already moving.",
            "# Chapter 3: Ashes of the Old War\n\nHeat shimmered over the test stand, making the bolts look soft.",
            "# Chapter 4: First Orbit\n\nThe phone rang once, and nobody reached for it.",
        ]

        result = analyze_openings(chapters)

        self.assertTrue(result.passes)
        self.assertEqual(result.duplicate_openers, 0)
        self.assertEqual(result.consecutive_duplicate_openers, 0)
        self.assertEqual(result.banned_phrase_hits, 0)
        self.assertGreaterEqual(result.diversity_score, 0.75)

    def test_opening_sentence_helpers_skip_heading_lines(self):
        text = "# Chapter 2: The Rocket Men\n\nThe room hummed around her. Fans rattled in the ceiling."
        sentence = extract_opening_sentence(text)
        signature = opening_signature(sentence)

        self.assertEqual(sentence, "The room hummed around her.")
        self.assertEqual(signature, "the room hummed around her")


if __name__ == "__main__":
    unittest.main()
