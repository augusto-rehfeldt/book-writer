"""--auto/--forever/--resume flags, AI-chosen scope and publishing fallback."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_book_creator import cli
from ai_book_creator.core.book_creator import AIBookCreator
from ai_book_creator.core.project_manager import ProjectManager
from ai_book_creator.steps.step_0_init import InitStep
from ai_book_creator.utils import github_publisher


class FlagTests(unittest.TestCase):
    def parse(self, *argv):
        with patch.object(sys, "argv", ["main.py", *argv]):
            return cli._parse_args()

    def test_forever_implies_auto_mode(self):
        args = self.parse("--forever")
        self.assertTrue(args.auto)
        self.assertEqual(args.mode, "auto")

    def test_publish_flags(self):
        self.assertEqual(self.parse("--publish").publish, "kdp")
        self.assertEqual(self.parse("--publish-github").publish, "github")
        self.assertEqual(self.parse().publish, "")

    def test_resume_without_project_fails(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cli, "PROJECT_STATE_FILE", Path(directory) / "missing.json"), \
                patch.object(cli, "choose_ai") as choose:
            with self.assertRaises(ValueError):
                cli.run("openrouter", "auto", resume=True)
        # The review stages get a model and effort of their own, picked in the same menu.
        self.assertEqual(choose.call_args.kwargs["roles"], ("writing", "review"))


class IdeaTests(unittest.TestCase):
    def test_idea_anywhere_and_in_any_mode(self):
        for argv in (["a lighthouse keeper", "--forever", "--publish"],
                     ["--forever", "a lighthouse keeper", "--publish"],
                     ["--forever", "--publish", "a", "lighthouse", "keeper"],
                     ["--provider", "openrouter", "a lighthouse keeper", "--pause", "5"]):
            with patch.object(sys, "argv", ["main.py", *argv]):
                args = cli._parse_args()
            self.assertEqual(args.idea, "a lighthouse keeper", argv)
        with patch.object(sys, "argv", ["main.py", "a lighthouse keeper"]):
            self.assertEqual(cli._parse_args().mode, "review")

    def test_review_mode_idea_skips_the_idea_question(self):
        with tempfile.TemporaryDirectory() as directory:
            step = InitStep(SimpleNamespace(), ProjectManager(directory))
            with patch.dict(os.environ, {"AI_BOOK_MODE": "review", "AI_BOOK_IDEA": "MY IDEA"}),                     patch("builtins.input", side_effect=AssertionError("asked")):
                self.assertEqual(step._get_concept(False), "MY IDEA")

    def test_user_idea_is_developed_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            prompts = []
            ai = SimpleNamespace(generate_content=lambda p, **_: prompts.append(p) or "Books: 1")
            step = InitStep(ai, ProjectManager(directory))
            with patch.dict(os.environ, {"AI_BOOK_MODE": "auto", "AI_BOOK_IDEA": "A LIGHTHOUSE KEEPER"}):
                step._get_concept(None)
            self.assertEqual(len(prompts), 1)
            self.assertIn("A LIGHTHOUSE KEEPER", prompts[0])

    def test_idea_seeds_only_the_first_forever_project(self):
        seen = []

        class Creator:
            def __init__(self):
                self.project_manager = SimpleNamespace(save_project=lambda: None)

            def create_book(self):
                seen.append(os.environ.get("AI_BOOK_IDEA"))
                if len(seen) == 2:
                    raise KeyboardInterrupt
                return True

        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"AI_BOOK_IDEA": "MY IDEA"}), \
                patch.object(cli, "PROJECT_STATE_FILE", Path(directory) / "none.json"), \
                patch.object(cli, "choose_ai"), patch.object(cli, "AIBookCreator", Creator), \
                patch.object(cli, "_has_previous_generated_artifacts", return_value=False), \
                patch.object(cli, "_stash_previous_ebook_files", return_value=[]), \
                patch.object(cli, "_clear_project_output"):
            with self.assertRaises(KeyboardInterrupt):
                cli.run("openrouter", "auto", forever=True)
        self.assertEqual(seen, ["MY IDEA", None])

    def test_idea_with_saved_project_needs_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "project_data.json"
            state.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"AI_BOOK_IDEA": "MY IDEA"}), \
                    patch.object(cli, "PROJECT_STATE_FILE", state), patch.object(cli, "choose_ai"):
                with self.assertRaises(ValueError):
                    cli.run("openrouter", "auto")


class AutoScopeTests(unittest.TestCase):
    def test_ai_concept_decides_series_length(self):
        with tempfile.TemporaryDirectory() as directory:
            ai = SimpleNamespace(
                generate_content=lambda prompt, **_: "Pitches" if "Pitch 7" in prompt
                else "Title: Salt Road\nA smuggler saga.\nBooks: 3",
                build_sectioned_prompt=lambda instruction, sections, **_: instruction)
            step = InitStep(ai, ProjectManager(directory))
            with patch.dict(os.environ, {"AI_BOOK_MODE": "auto"}), \
                    patch.dict(os.environ, {}, clear=False) as env, \
                    patch.object(step, "_generate_layout", side_effect=RuntimeError("stop")):
                env.pop("AI_BOOK_SERIES_COUNT", None)
                with self.assertRaises(Exception):
                    step.execute()
            saved = step.get_step_data()
            self.assertEqual(saved["series_book_count"], 3)
            self.assertTrue(saved["series_mode"])


class PublishTests(unittest.TestCase):
    def test_personal_account_is_refused(self):
        with self.assertRaises(github_publisher.GithubPublishError):
            github_publisher.target_repo("augusto-rehfeldt", "Augusto-Rehfeldt/books")
        with self.assertRaises(github_publisher.GithubPublishError):
            github_publisher.target_repo("someone", "")
        self.assertEqual(github_publisher.target_repo("someone", "li-wen/books"), "li-wen/books")

    def test_kdp_failure_falls_back_to_github(self):
        creator = SimpleNamespace(
            ai_service=None,
            project_manager=SimpleNamespace(get_step_data=lambda _: {"package_file": "x_kdp.json"}))
        with patch("ai_book_creator.utils.kdp_publisher.publish_package", side_effect=RuntimeError("login")), \
                patch.object(github_publisher, "publish_package", return_value="https://gh/rel") as gh:
            self.assertEqual(cli._publish(creator, "kdp"), "github")
            gh.assert_called_once_with("x_kdp.json")

    def test_series_publishes_every_package(self):
        with tempfile.TemporaryDirectory() as directory:
            first, last = Path(directory) / "one_kdp.json", Path(directory) / "two_kdp.json"
            for index, package in enumerate((first, last)):
                package.write_text("{}", encoding="utf-8")
                os.utime(package, (index, index))
            creator = SimpleNamespace(
                ai_service=None,
                project_manager=SimpleNamespace(get_step_data=lambda _: {"package_file": str(last)}))
            with patch.object(github_publisher, "publish_package", return_value="https://gh/rel") as gh:
                cli._publish(creator, "github")
            self.assertEqual([call.args[0] for call in gh.call_args_list], [str(first), str(last)])

    def test_archived_package_follows_its_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            epub = output / "ebook" / "book.epub"
            epub.parent.mkdir()
            epub.write_text("x", encoding="utf-8")
            (output / "ebook" / "book_kdp.json").write_text(
                json.dumps({"manuscript_file": str(epub), "cover_file": ""}), encoding="utf-8")
            with patch.object(cli, "PROJECT_OUTPUT_DIR", output), \
                    patch.object(cli, "PROJECT_ARCHIVE_DIR", output / "archive" / "ebooks"):
                moved = cli._stash_previous_ebook_files()
            package = json.loads(next(p for p in moved if p.suffix == ".json").read_text(encoding="utf-8"))
            self.assertTrue(Path(package["manuscript_file"]).is_file())

    def test_series_title_is_never_layout_prose(self):
        extract = AIBookCreator._extract_titles_from_layout
        self.assertEqual(extract(None, "Book 5 is planned for 267 pages and closes the series.\n\nGenre: SF"), [])
        self.assertEqual(extract(None, "**TITLE:** Ledger of Last Hands\n\n1. Ledger of Last Hands\n2. \"Held Stocks\""),
                         ["Ledger of Last Hands", "Held Stocks"])

    def test_existing_release_is_not_posted_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "book_kdp.json"
            package.write_text(json.dumps({"github_url": "https://gh/rel"}), encoding="utf-8")
            with patch.object(github_publisher, "_gh", side_effect=AssertionError("no gh calls")):
                self.assertEqual(github_publisher.publish_package(package), "https://gh/rel")


if __name__ == "__main__":
    unittest.main()
