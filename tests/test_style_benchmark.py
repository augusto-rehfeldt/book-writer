from __future__ import annotations

import unittest

from benchmarks.style_benchmark import run_style_benchmark


class StyleBenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_prompt_and_style_improvements(self):
        report = run_style_benchmark()

        self.assertTrue(all(report["prompt_checks"].values()))
        self.assertTrue(report["improved"])
        self.assertTrue(report["candidate"].passes)
        self.assertLessEqual(report["candidate"].duplicate_openers, report["baseline"].duplicate_openers)


if __name__ == "__main__":
    unittest.main()
