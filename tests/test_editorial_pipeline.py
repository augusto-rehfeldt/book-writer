"""Checks for manuscript preservation, complete coverage, and resumable continuity."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_book_creator.core.project_manager import ProjectManager
from ai_book_creator.services.ai_service import AIService, IncompleteGenerationError
from ai_book_creator.steps.step_2_write import WriteStep
from ai_book_creator.steps.step_3_review import ReviewStep
from ai_book_creator.utils import editorial, humanizer
from ai_book_creator.utils.glossary_manager import GlossaryManager
from ai_book_creator.utils.text_utils import save_text, text_chunks, text_digest


PROSE = "Alice put the blue key in her pocket. " + "She waited beside the locked door. " * 40
EDIT = {"original": "put the blue key", "replacement": "slipped the blue key", "reason": "More precise action"}


def verdict(winner="B", **kwargs):
    return json.dumps({"winner": winner, "same_events": True, "same_character_intent": True,
                       "no_new_facts": True, "reason": "The action is clearer without changing the scene.", **kwargs})


def service(*responses):
    ai = Mock()
    ai.config = {}
    ai.generate_content.side_effect = list(responses)
    return ai


class PassageSafetyTests(unittest.TestCase):
    def test_chunks_preserve_every_character_including_final_short_tail(self):
        source = "x" * 18000 + "\n\n***\n\nTHE END"
        chunks = list(text_chunks(source))
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(len(c) <= 6500 for c in chunks))
        self.assertIn("THE END", chunks[-1])

    def test_edits_leave_unselected_text_unchanged(self):
        self.assertEqual(editorial.apply_edits(PROSE, [EDIT]), PROSE.replace("put the blue key", "slipped the blue key"))

    def test_invalid_or_destructive_edits_are_rejected(self):
        scene = "# Arrival\n\n" + PROSE + "\n\n***\n\nShe left."
        for edits in (
            [{**EDIT, "original": "absent"}],
            [{**EDIT, "original": "She waited"}],
            [EDIT, EDIT],
            [{**EDIT, "original": "***", "replacement": ""}],
            [{**EDIT, "original": scene, "replacement": "The end."}],
            [{**EDIT, "replacement": "padding " * 1000}],
        ):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                editorial.apply_edits(scene, edits)

    def test_changed_events_or_string_booleans_fail_verification(self):
        for value in (False, "true"):
            ai = service(verdict(same_events=value))
            self.assertFalse(editorial.prefer_revision(ai, PROSE, PROSE + " New event.")["accepted"])

    def test_both_orders_must_prefer_revision(self):
        candidate = editorial.apply_edits(PROSE, [EDIT])
        self.assertTrue(editorial.prefer_revision(service(verdict("B"), verdict("A")), PROSE, candidate)["accepted"])
        self.assertFalse(editorial.prefer_revision(service(verdict("B"), verdict("B")), PROSE, candidate)["accepted"])

    def test_edit_report_keeps_proposal_and_rejection_evidence(self):
        proposal = json.dumps({"summary": "Alice waits.", "issues": [], "edits": [EDIT]})
        ai = service(proposal, verdict(same_events=False))
        revised, reports = editorial.edit_text(ai, PROSE, "Clarify")
        self.assertEqual(revised, PROSE)
        self.assertFalse(reports[0]["accepted"])
        self.assertEqual(reports[0]["edits"], [EDIT])
        self.assertIn("comparisons", reports[0])

    def test_malformed_editorial_output_cannot_count_as_review(self):
        with self.assertRaises(ValueError):
            editorial.edit_text(service("Here is the rewritten chapter"), PROSE, "Review")

    def test_save_preserves_original_and_survives_failed_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapter_01.txt"
            save_text(str(path), PROSE)
            with patch("ai_book_creator.utils.text_utils.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    save_text(str(path), "changed")
            self.assertEqual(path.read_text(encoding="utf-8"), PROSE)
            save_text(str(path), "changed")
            self.assertEqual(next((path.parent / "revisions").glob("*.txt")).read_text(encoding="utf-8"), PROSE)

    def test_partial_provider_output_is_rejected_before_extraction(self):
        ai = AIService.__new__(AIService)
        for response in (
            {"choices": [{"finish_reason": "length", "message": {"content": "unfinished"}}]},
            SimpleNamespace(status="incomplete", output_text="unfinished"),
            {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "unfinished"}]},
            SimpleNamespace(candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))]),
        ):
            with self.subTest(response=response), self.assertRaises(IncompleteGenerationError):
                ai._extract_text_from_response(response)


class CoverageTests(unittest.TestCase):
    def test_continuity_reads_the_ending_and_carries_prior_knowledge(self):
        ai = service(*[json.dumps({"continuity": "Alice has the key; Bob has not learned this."})] * 4)
        editorial.update_continuity(ai, "Early scene. " * 1500 + "LATE_REVELATION", "PRIOR_SECRET", "Arrival")
        prompts = [call.args[0] for call in ai.generate_content.call_args_list]
        self.assertIn("PRIOR_SECRET", prompts[0])
        self.assertIn("LATE_REVELATION", prompts[-1])
        self.assertIn("Bob has not learned", prompts[-1])

    def test_analysis_covers_late_chapters(self):
        ai = service(*[json.dumps({"analysis": "The earlier key remains unresolved.", "issues": []})] * 4)
        step = ReviewStep(ai, Mock(get_step_data=Mock(return_value={})), ".")
        step._generate_analysis({}, "Chapter one. " * 2000 + "FINAL_CHAPTER_REVELATION")
        prompts = [call.args[0] for call in ai.generate_content.call_args_list]
        self.assertGreater(len(prompts), 1)
        self.assertIn("FINAL_CHAPTER_REVELATION", prompts[-1])
        self.assertIn("earlier key remains unresolved", prompts[-1])

    def test_glossary_reads_late_names_and_does_not_rename_them(self):
        reply = json.dumps({"characters": [{"name": "Alice", "description": "Has the key"}],
                            "locations": [], "concepts": []})
        ai = service(*[reply] * 4)
        with tempfile.TemporaryDirectory() as directory:
            glossary = GlossaryManager(directory)
            glossary.set_name_pools({"protagonists": ["Beatrice"]})
            glossary.auto_populate_from_chapter("A scene. " * 1500 + "LATE_NAME", "Arrival", ai)
            self.assertIn("LATE_NAME", ai.generate_content.call_args.args[0])
            self.assertIn("Alice", glossary.glossary["characters"])
            self.assertNotIn("Beatrice", glossary.glossary["characters"])

    def test_humanizer_skips_unflagged_passages_and_supplies_neighbors(self):
        source = PROSE * 40
        no_edits = json.dumps({"summary": "Alice waits.", "issues": [], "edits": []})
        ai = Mock(config={})
        ai.generate_content.return_value = no_edits
        chunks = list(text_chunks(source))
        scores = [{"score": 0, "issues": []}] + [{"score": 30, "issues": ["Check repetition"]}] * (len(chunks) - 1)
        with patch.object(humanizer, "local_score", side_effect=scores):
            revised, _ = humanizer._rewrite(ai, source)
        self.assertEqual(revised, source)
        self.assertEqual(ai.generate_content.call_count, len(chunks) - 1)
        self.assertIn(chunks[0][-1800:], ai.generate_content.call_args_list[0].args[0])

    def test_review_detects_same_length_edits_and_retains_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapter_01.txt"
            save_text(str(path), PROSE)
            pm = ProjectManager(directory)
            pm.book_data = {"written": {"chapters": {"chapter_1": {
                "filename": str(path), "title": "Arrival", "chapter_number": 1}}},
                "reviewed": {"completed": True, "analysis": "Reviewed", "source_hashes": {"chapter_1": text_digest(PROSE)}}}
            step = ReviewStep(Mock(), pm, directory)
            self.assertFalse(step.should_execute())
            save_text(str(path), PROSE.replace("blue", "gray"))
            self.assertTrue(step.should_execute())


class ResumeTests(unittest.TestCase):
    def test_project_save_failure_keeps_previous_checkpoint_and_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            pm = ProjectManager(directory)
            pm.book_data = {"written": {"total_word_count": 250}}
            pm.save_project()
            previous = Path(pm.project_file).read_text(encoding="utf-8")
            pm.book_data["written"]["total_word_count"] = 500
            with patch("ai_book_creator.utils.text_utils.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    pm.save_project()
            self.assertEqual(Path(pm.project_file).read_text(encoding="utf-8"), previous)

    def test_review_resume_does_not_repeat_edit_after_continuity_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapter_01.txt"
            save_text(str(path), "# Arrival\n\n" + PROSE)
            pm = ProjectManager(directory)
            pm.book_data = {"init": {"book_idea": "A key"}, "written": {"chapters": {
                "chapter_1": {"filename": str(path), "title": "Arrival", "chapter_number": 1,
                              "word_count": len(PROSE.split())}}}}
            ai = service(json.dumps({"summary": "Alice waits", "issues": [], "edits": [EDIT]}),
                         verdict("B"), verdict("A"), "invalid")
            with self.assertRaises(ValueError):
                ReviewStep(ai, pm, directory).execute()
            resumed = ProjectManager(directory)
            resumed.load_project()
            ai = service(json.dumps({"continuity": "Alice has the key."}),
                         json.dumps({"analysis": "The ending follows the scene.", "issues": []}))
            with patch.object(humanizer, "enabled", return_value=False):
                ReviewStep(ai, resumed, directory).execute()
            self.assertEqual(ai.generate_content.call_count, 2)
            self.assertIn("slipped the blue key", path.read_text(encoding="utf-8"))

    def test_manuscript_analysis_resumes_at_first_unread_passage(self):
        with tempfile.TemporaryDirectory() as directory:
            pm = ProjectManager(directory)
            source = "Early scene. " * 1500 + "LAST_PASSAGE"
            ai = service(json.dumps({"analysis": "KNOWN_SECRET", "issues": []}), "invalid")
            with self.assertRaises(ValueError):
                ReviewStep(ai, pm, directory)._generate_analysis({}, source)
            resumed = ProjectManager(directory)
            resumed.load_project()
            ai = service(json.dumps({"analysis": "The last passage resolves it.", "issues": []}))
            ReviewStep(ai, resumed, directory)._generate_analysis({}, source)
            self.assertEqual(ai.generate_content.call_count, 1)
            self.assertIn("LAST_PASSAGE", ai.generate_content.call_args.args[0])
            self.assertIn("KNOWN_SECRET", ai.generate_content.call_args.args[0])

    def test_writer_orders_chapters_and_passes_prior_prose_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            pm = ProjectManager(directory)
            pm.book_data = {"init": {"book_idea": "A key", "layout_content": "Alice is the narrator.",
                                      "page_count": 4}, "structure": {"chapter_plots": {
                f"chapter_{number}": {"chapter_number": number, "title": f"Part {number}",
                                      "plot_outline": "Alice waits.", "word_count_estimate": 400}
                for number in (2, 1)}}}
            no_edits = json.dumps({"summary": "Alice waits", "issues": [], "edits": []})
            first = "# Part 1\n\n" + PROSE + " ENDING_ANCHOR"
            ai = service(first, no_edits, json.dumps({"continuity": "KNOWLEDGE_ANCHOR"}),
                         "# Part 2\n\n" + PROSE, no_edits, json.dumps({"continuity": "They still wait."}))
            with patch.object(WriteStep, "_load_config", return_value={}), patch.object(humanizer, "enabled", return_value=False):
                WriteStep(ai, pm, None, directory).execute()
            prompts = [call.args[0] for call in ai.generate_content.call_args_list]
            self.assertIn("CHAPTER: Part 1", prompts[0])
            self.assertIn("CHAPTER: Part 2", prompts[3])
            self.assertIn("ENDING_ANCHOR", prompts[3])
            self.assertIn("KNOWLEDGE_ANCHOR", prompts[3])
            self.assertIn("Alice is the narrator.", prompts[3])

    def test_review_rechecks_final_text_after_targeted_repairs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapter_01.txt"
            save_text(str(path), "# Arrival\n\n" + PROSE)
            pm = ProjectManager(directory)
            pm.book_data = {"init": {"book_idea": "A key", "page_count": 4},
                "written": {"chapters": {"chapter_1": {"filename": str(path), "title": "Arrival",
                    "chapter_number": 1, "word_count": len(PROSE.split())}}},
                "ebook": {"completed": True}, "publishing": {"completed": True}}
            no_edits = json.dumps({"summary": "Alice waits", "issues": [], "edits": []})
            issue = {"chapter_number": 1, "quote": EDIT["original"], "problem": "Vague movement", "fix": "Clarify action"}
            ai = service(no_edits, json.dumps({"continuity": "Alice has the key."}),
                         json.dumps({"analysis": "The action needs clarification.", "issues": [issue]}),
                         json.dumps({"summary": "Alice waits", "issues": [issue], "edits": [EDIT]}),
                         verdict("B"), verdict("A"),
                         json.dumps({"continuity": "Alice has the key."}),
                         json.dumps({"analysis": "The action is clear.", "issues": []}))
            with patch.object(humanizer, "enabled", return_value=False):
                step = ReviewStep(ai, pm, directory)
                result = step.execute()
            self.assertIn("slipped the blue key", path.read_text(encoding="utf-8"))
            self.assertEqual(result["unresolved_issues"], [])
            self.assertEqual(result["analysis"], "The action is clear.")
            self.assertFalse(step.should_execute())
            self.assertNotIn("ebook", pm.book_data)
            self.assertNotIn("publishing", pm.book_data)
            self.assertTrue(list((Path(directory) / "revisions").glob("*.txt")))

    def test_writer_reuses_finished_chapter_after_continuity_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            pm = ProjectManager(directory)
            pm.book_data = {"init": {"book_idea": "A key", "layout_content": "Alice is the narrator.",
                                      "page_count": 4}, "structure": {"chapter_plots": {
                "chapter_1": {"chapter_number": 1, "title": "Arrival", "plot_outline": "Alice waits.",
                              "word_count_estimate": 400}}}}
            draft = "# Arrival\n\n" + PROSE
            ai = service(draft, json.dumps({"summary": "Alice waits", "issues": [], "edits": []}), "invalid")
            with patch.object(WriteStep, "_load_config", return_value={}), patch.object(humanizer, "enabled", return_value=False):
                writer = WriteStep(ai, pm, None, directory)
                with self.assertRaises(ValueError):
                    writer.execute()
                self.assertEqual((Path(directory) / "chapter_01.txt").read_text(encoding="utf-8"), draft)
                resumed = ProjectManager(directory)
                resumed.load_project()
                ai = service(json.dumps({"continuity": "Alice has the key and waits."}))
                writer = WriteStep(ai, resumed, None, directory)
                writer.execute()
                self.assertEqual(ai.generate_content.call_count, 1)
                self.assertEqual(resumed.get_step_data("written")["total_word_count"], len(draft.split()))


class EvaluationTests(unittest.TestCase):
    def test_blind_pack_and_content_loss_are_separate_from_preferences(self):
        from benchmarks.evaluate_prose import CRITERIA, prepare, summarize
        with tempfile.TemporaryDirectory() as directory:
            prepare([{"id": "scene", "kind": "action", "original": PROSE, "candidate": PROSE + " Gone."}], directory)
            key = json.loads((Path(directory) / "key.json").read_text(encoding="utf-8"))
            candidate = next(label for label in ("A", "B") if key["scene"][label] == "candidate")
            votes = {"scene": {**{criterion: candidate for criterion in CRITERIA},
                               "content_loss": candidate, "evidence": "The last sentence removes an event."}}
            result = summarize(votes, key)
            self.assertEqual(result["preferences"]["voice"]["candidate"], 1)
            self.assertEqual(result["content_loss_cases"]["candidate"], 1)
            with self.assertRaises(FileExistsError):
                prepare([{"id": "scene", "kind": "action", "original": PROSE, "candidate": PROSE}], directory)

    def test_corpus_selection_does_not_exclude_narration_by_dialogue_count(self):
        from benchmarks.build_prose_baseline import is_fiction
        self.assertTrue(is_fiction("Literary Fiction|Historical"))
        self.assertFalse(is_fiction("Non-Fiction|History"))
        self.assertFalse(is_fiction("Biography|Science Fiction"))
        self.assertFalse(is_fiction(""))


if __name__ == "__main__":
    unittest.main()
