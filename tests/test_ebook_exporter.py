from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ai_book_creator.utils.ebook_exporter import export_epub


class EbookExporterTests(unittest.TestCase):
    def test_export_epub_builds_archive_from_chapter_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "book_output").mkdir(exist_ok=True)

            chapter_1 = project_dir / "book_output" / "chapter_01.txt"
            chapter_2 = project_dir / "book_output" / "chapter_02.txt"
            chapter_1.write_text("# Chapter 1: Opening\n\nFirst chapter text.", encoding="utf-8")
            chapter_2.write_text("# Chapter 2: Follow-Up\n\nSecond chapter text.", encoding="utf-8")

            project_data = {
                "init": {
                    "book_idea": "A test book concept.",
                    "layout_content": "## 3 Potential Titles\n1. **Test Book Title**",
                    "completed": True,
                },
                "written": {
                    "chapters": {
                        "chapter_1": {
                            "title": "Opening",
                            "chapter_number": 1,
                            "filename": str(chapter_1.relative_to(project_dir)),
                            "word_count": 4,
                        },
                        "chapter_2": {
                            "title": "Follow-Up",
                            "chapter_number": 2,
                            "filename": str(chapter_2.relative_to(project_dir)),
                            "word_count": 4,
                        },
                    },
                    "total_word_count": 8,
                    "total_pages": 1,
                    "completed": True,
                },
            }
            (project_dir / "project_data.json").write_text(
                json.dumps(project_data, indent=2),
                encoding="utf-8",
            )

            output_file = export_epub(project_dir)
            output_path = Path(output_file)

            self.assertTrue(output_path.exists())
            self.assertEqual(output_path.suffix, ".epub")

            with zipfile.ZipFile(output_path, "r") as archive:
                names = set(archive.namelist())
                self.assertIn("mimetype", names)
                self.assertIn("META-INF/container.xml", names)
                self.assertIn("OEBPS/content.opf", names)
                self.assertIn("OEBPS/nav.xhtml", names)
                self.assertIn("OEBPS/text/chapter-01.xhtml", names)
                self.assertIn("OEBPS/text/chapter-02.xhtml", names)
                self.assertEqual(
                    archive.read("mimetype"),
                    b"application/epub+zip",
                )


if __name__ == "__main__":
    unittest.main()
