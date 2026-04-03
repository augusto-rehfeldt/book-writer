from __future__ import annotations

import json
import base64
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
                            "filename": str(Path("book_output") / chapter_1.name),
                            "word_count": 4,
                        },
                        "chapter_2": {
                            "title": "Follow-Up",
                            "chapter_number": 2,
                            "filename": str(Path("book_output") / chapter_2.name),
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

    def test_export_epub_preserves_markdown_formatting_and_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            (project_dir / "book_output").mkdir(exist_ok=True)

            image_path = project_dir / "book_output" / "figure.png"
            image_path.write_bytes(
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7v9u8AAAAASUVORK5CYII="
                )
            )

            chapter = project_dir / "book_output" / "chapter_01.txt"
            chapter.write_text(
                "# Chapter 1: Markdown Test\n\n"
                "## Scene One\n\n"
                "This is **bold** and *italic*.\n\n"
                "![A figure](figure.png)\n\n"
                "> A quoted line.\n\n"
                "- First item\n"
                "- Second item\n\n"
                "***\n\n"
                "1. First\n"
                "2. Second\n",
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
                            "title": "Markdown Test",
                            "chapter_number": 1,
                            "filename": str(Path("book_output") / chapter.name),
                            "word_count": 20,
                        }
                    },
                    "total_word_count": 20,
                    "total_pages": 1,
                    "completed": True,
                },
            }
            (project_dir / "project_data.json").write_text(
                json.dumps(project_data, indent=2),
                encoding="utf-8",
            )

            output_file = export_epub(project_dir)

            with zipfile.ZipFile(output_file, "r") as archive:
                chapter_xhtml = archive.read("OEBPS/text/chapter-01.xhtml").decode("utf-8")
                names = set(archive.namelist())

                self.assertIn("<h2>Scene One</h2>", chapter_xhtml)
                self.assertIn("<strong>bold</strong>", chapter_xhtml)
                self.assertIn("<em>italic</em>", chapter_xhtml)
                self.assertIn("<blockquote><p>A quoted line.</p></blockquote>", chapter_xhtml)
                self.assertIn("<ul>", chapter_xhtml)
                self.assertIn("<ol>", chapter_xhtml)
                self.assertIn('<img src="../images/image-001.png" alt="A figure" />', chapter_xhtml)
                self.assertIn("OEBPS/images/image-001.png", names)


if __name__ == "__main__":
    unittest.main()
