import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ai_book_creator import cli


class CliArchiveTests(unittest.TestCase):
    def test_stash_moves_epub_and_its_cover_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "book_output"
            ebook_dir = output_dir / "ebook"
            ebook_dir.mkdir(parents=True)
            epub = ebook_dir / "story.epub"
            prompt = ebook_dir / "story_cover_prompt.txt"
            orphan_prompt = ebook_dir / "orphan_cover_prompt.txt"
            unrelated = ebook_dir / "notes.txt"
            for path in (epub, prompt, orphan_prompt, unrelated):
                path.write_text(path.name, encoding="utf-8")

            with patch.object(cli, "PROJECT_OUTPUT_DIR", output_dir), patch.object(
                cli, "PROJECT_ARCHIVE_DIR", output_dir / "archive" / "ebooks"
            ):
                moved = cli._stash_previous_ebook_files()

            self.assertEqual(
                {path.name for path in moved},
                {epub.name, prompt.name, orphan_prompt.name},
            )
            self.assertFalse(epub.exists())
            self.assertFalse(prompt.exists())
            self.assertFalse(orphan_prompt.exists())
            self.assertTrue(unrelated.exists())

    def test_continuous_run_starts_next_book_only_after_completion(self):
        first = Mock()
        first.create_book.return_value = True
        second = Mock()
        second.create_book.return_value = False

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            cli, "PROJECT_OUTPUT_DIR", Path(temp_dir)
        ), patch.object(
            cli, "PROJECT_STATE_FILE", Path(temp_dir) / "project_data.json"
        ), patch.object(
            cli, "AIBookCreator", side_effect=(first, second)
        ) as creator, patch.object(
            cli, "choose_ai"
        ), patch.object(
            cli, "_stash_previous_ebook_files", return_value=[Path(temp_dir) / "archive" / "book.epub"]
        ) as stash, patch.object(
            cli, "_clear_project_output"
        ) as clear:
            completed = cli.run("google", mode="auto", continuous=True)

        self.assertFalse(completed)
        self.assertEqual(creator.call_count, 2)
        stash.assert_called_once_with()
        clear.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

