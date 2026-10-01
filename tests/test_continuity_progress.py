"""Continuity notices stay inside stage progress updates."""
import io
import os
import unittest
from unittest.mock import Mock, patch

import importlib.util
from pathlib import Path
import sys
import types

# Load these stdlib-only utilities without importing unrelated publishing SDKs.
package = "_continuity_progress_test_utils"
sys.modules[package] = types.ModuleType(package)
sys.modules[package].__path__ = []
for name in ("text_utils", "editorial"):
    spec = importlib.util.spec_from_file_location(
        package + "." + name,
        Path(__file__).resolve().parents[1] / "ai_book_creator" / "utils" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    globals()[name] = module


class ContinuityProgressTests(unittest.TestCase):
    def test_continuity_notices_redraw_each_stage_without_newlines(self):
        for label in ("Writing", "Review", "Refresh continuity"):
            for done in (1, 2):
                with self.subTest(label=label, done=done):
                    stream = io.StringIO()
                    stream.isatty = lambda: True
                    ai = Mock()
                    ai.generate_content.return_value = '{"continuity": "' + 'fact ' * 903 + '"}'
                    with patch.dict(os.environ, AI_BOOK_MODE="auto"), patch("sys.stdout", stream), patch.object(text_utils.shutil, "get_terminal_size", return_value=os.terminal_size((180, 24))):
                        result = editorial.update_continuity(ai, "Scene.", "", "Arrival", (label, done, 2))
                    output = stream.getvalue()
                    self.assertEqual(len(result.split()), 903)
                    self.assertNotIn("\n", output)
                    self.assertIn(label + " [", output)
                    self.assertIn("903 words", output)
                    self.assertIn("Arrival", output)

    def test_plain_output_keeps_notice_on_bar_line(self):
        stream = io.StringIO()
        ai = Mock()
        ai.generate_content.return_value = '{"continuity": "' + 'fact ' * 903 + '"}'
        with patch.dict(os.environ, AI_BOOK_MODE="auto"), patch("sys.stdout", stream):
            editorial.update_continuity(ai, "Scene.", "", "Arrival", ("Review", 1, 2, "5 cached"))
        lines = stream.getvalue().splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("Review [") for line in lines))
        self.assertIn("903 words", lines[-1])
        self.assertIn("5 cached |", lines[-1])

    def test_every_pipeline_continuity_call_supplies_stage_progress(self):
        import ast
        steps = Path(__file__).resolve().parents[1] / "ai_book_creator" / "steps"
        calls = []
        for path in steps.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "update_continuity":
                    calls.append(node)
                    with self.subTest(path=path.name, line=node.lineno):
                        self.assertTrue(len(node.args) >= 5 or any(k.arg == "progress_context" for k in node.keywords))
        self.assertTrue(calls)
