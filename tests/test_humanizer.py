"""Humanness scoring: the checks that would silently stop working.

The interesting property is not "the score is 7.3", it is "prose that reads as
machine-written scores worse than prose that does not", and that a rewrite can
never quietly delete half a chapter.
"""

from __future__ import annotations

import random
import re
import unittest
from unittest.mock import Mock

from ai_book_creator.utils import humanizer


FLAT = "\n\n".join([
    "She walked into the room and looked around at the empty chairs there. "
    "The light was dim and the air was thick with the smell of old paper. "
    "She felt a wave of unease as she considered what she would do next.",
    "He walked into the hall and looked around at the empty tables there. "
    "The light was dim and the air was thick with the smell of cold coffee. "
    "He felt a wave of unease as he considered what he would do next.",
] * 3)

HUMAN = (
    "She stopped.\n\n"
    "“You're late,” Marta said, not looking up from the ledger, and for a while "
    "neither of them said anything else, because the ledger was open at the March column "
    "and the March column was wrong by four hundred crowns, which they both knew perfectly "
    "well and neither of them wanted to be the one who said it first.\n\n"
    "“I know.”\n\n"
    "Rain on the window. Somebody upstairs dropped a boot.\n\n"
    "“The Hensel account,” Marta said. “Again.”\n\n"
    "“It isn't the Hensel account.” Anna set her gloves on the desk, one exactly "
    "on top of the other. “It's Kraus. It has been Kraus since October and you have "
    "known that since October, which is roughly when you stopped asking me about it.”\n\n"
    "Marta finally closed the ledger.\n"
)


class FingerprintTests(unittest.TestCase):
    def test_flat_prose_is_less_bursty_and_scores_worse(self):
        self.assertLess(
            humanizer.fingerprint(FLAT)["burstiness"],
            humanizer.fingerprint(HUMAN)["burstiness"],
        )
        self.assertGreater(
            humanizer.local_score(FLAT)["score"],
            humanizer.local_score(HUMAN)["score"],
        )

    def test_dialogue_and_headings(self):
        self.assertGreater(humanizer.fingerprint(HUMAN)["dialogue_ratio"], 0.5)
        self.assertEqual(humanizer.fingerprint(FLAT)["dialogue_ratio"], 0.0)
        # A run of `# Chapter N` headings must not fake sentence variety.
        headings = "# One\n\n# Two\n\n# Three\n\n" + FLAT
        self.assertEqual(
            humanizer.fingerprint(headings)["sentences"],
            humanizer.fingerprint(FLAT)["sentences"],
        )

    def test_msttr_does_not_punish_length(self):
        """A chapter must not score as poorer-vocabulary than a reference window
        merely for being longer, which is exactly what plain TTR does."""
        rng = random.Random(0)
        vocab = [f"word{i}" for i in range(300)]
        draw = lambda n: " ".join(rng.choice(vocab) for _ in range(n)) + "."
        short, long = humanizer._msttr(draw(600).split()), humanizer._msttr(draw(4000).split())
        self.assertAlmostEqual(short, long, delta=0.05)
        plain = len(set(draw(4000).split())) / 4000
        self.assertLess(plain, long - 0.2, "plain TTR should be the one that collapses")

    def test_stock_phrases_are_flagged_unless_real_novels_use_them(self):
        text = HUMAN + "\n\nA shiver ran down her spine in that moment."
        issues = " ".join(humanizer.local_score(text)["issues"])
        self.assertIn("a shiver ran down", issues)
        allowed = set(humanizer.baseline().get("corpus_ok") or [])
        for phrase in allowed:
            self.assertIn(phrase, humanizer.LLM_TELLS)
            scored = humanizer.local_score(HUMAN + f"\n\nIt was {phrase} nothing.")
            self.assertNotIn(phrase, " ".join(scored["issues"]))

    def test_repeated_phrases_merge_overlapping_windows(self):
        repeats = humanizer.repeated_phrases(FLAT)
        self.assertTrue(repeats)
        self.assertLess(len(repeats), 8, repeats)

    def test_scrubbed_adverbs_are_a_tell(self):
        """The measured signal that separates best: novels average ~14 -ly/1k."""
        ref = {"ranges": {"ly_per_1k": {"p05": 6.9, "p95": 25.7}}}
        scrubbed = humanizer.local_score("He walked in. She looked up. " * 40, ref)
        self.assertTrue(any("-ly adverbs" in i for i in scrubbed["issues"]))


class ScaleTests(unittest.TestCase):
    """A chapter is judged against chapters, a window against windows."""

    def test_long_text_uses_chapter_percentiles(self):
        ref = humanizer.baseline()
        self.assertIsNot(ref["chapter_ranges"], ref["ranges"])
        self.assertIs(humanizer.scale_ranges(ref, 4000), ref["chapter_ranges"])
        self.assertIs(humanizer.scale_ranges(ref, 900), ref["ranges"])
        # No chapter table (old baseline) must still score, not crash.
        self.assertEqual(humanizer.scale_ranges({"ranges": {"a": 1}}, 9000), {"a": 1})

class BookLevelTests(unittest.TestCase):
    def test_identical_chapters_are_flagged_as_more_alike_than_a_real_novel(self):
        chapters = [HUMAN * 12] * 8
        report = humanizer.book_report(chapters)
        self.assertEqual(report["chapters"], 8)
        self.assertTrue(report["uniform"], report["cv"])

    def test_a_short_book_is_not_judged(self):
        self.assertEqual(humanizer.book_report([HUMAN * 12] * 2)["uniform"], [])


class TellPricingTests(unittest.TestCase):
    def test_a_phrase_published_chapters_use_costs_less_than_a_rare_one(self):
        ref = {"ranges": {}, "chapter_ranges": {},
               "tell_chapter_rate": {"in that moment": 0.05}}
        common = humanizer.local_score(HUMAN + " In that moment she left.", ref)
        rare = humanizer.local_score(HUMAN + " It was a testament to her.", ref)
        self.assertLess(common["score"], rare["score"])

    def test_the_measured_rates_ship_with_the_baseline(self):
        rates = humanizer.baseline().get("tell_chapter_rate") or {}
        self.assertTrue(rates, "run benchmarks/measure_chapter_shape.py")
        self.assertTrue(all(0.0 <= v <= 1.0 for v in rates.values()))


class BaselineTests(unittest.TestCase):
    def test_baseline_ships_and_covers_every_checked_metric(self):
        ref = humanizer.baseline()
        self.assertGreaterEqual(ref.get("books", 0), 20, "baseline must come from real books")
        ranges = ref["ranges"]
        for metric, *_ in humanizer.CHECKS:
            self.assertIn(metric, ranges, f"{metric} has no measured range")
            self.assertLessEqual(ranges[metric]["p05"], ranges[metric]["p95"])

    def test_ranges_match_the_prose_novelists_actually_write(self):
        r = humanizer.baseline()["ranges"]
        self.assertGreater(r["ly_per_1k"]["p50"], 8, "novels do use -ly adverbs")
        self.assertGreater(r["sent_len_mean"]["p50"], 9)
        self.assertGreater(r["dialogue_ratio"]["p50"], 0.3, "novels play scenes")


class RewriteTests(unittest.TestCase):
    def test_humanize_stops_when_under_threshold(self):
        service = Mock(config={})
        out, report = humanizer.humanize(service, HUMAN, rounds=3, threshold=100.0,
                                         log=lambda *_: None)
        self.assertEqual(out, HUMAN)
        self.assertTrue(report["passed"])
        service.generate_content.assert_not_called()

    def test_style_score_does_not_override_editorial_acceptance(self):
        from unittest.mock import patch
        revised = HUMAN + "\n\nMarta handed back the key."
        with patch.object(humanizer, "_rewrite", return_value=(revised, [{"accepted": True}])), \
                patch.object(humanizer, "local_score", side_effect=[
                    {"score": 20}, {"score": 30}, {"score": 30}]):
            out, report = humanizer.humanize(Mock(), HUMAN, rounds=2, log=lambda *_: None)
        self.assertEqual(out, revised)
        self.assertEqual(report["final_score"], 30)

    def test_json_parser_accepts_wrapped_json(self):
        self.assertEqual(humanizer._parse_json('Response: {"winner":"tie"}'), {"winner": "tie"})


if __name__ == "__main__":
    unittest.main()
