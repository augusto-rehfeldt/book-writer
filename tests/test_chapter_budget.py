"""Word budgets follow scene scope and preserve the requested total."""

import unittest

from ai_book_creator.steps.step_1_structure import chapter_budget


class TestChapterBudget(unittest.TestCase):
    def test_existing_scope_is_preserved_when_it_fits(self):
        self.assertEqual(chapter_budget([1000, 3000, 500], 4500), [1000, 3000, 500])

    def test_scaling_preserves_order_and_exact_total(self):
        budgets = chapter_budget([1500, 4500, 900, 2500], 13001)
        self.assertEqual(sum(budgets), 13001)
        self.assertEqual(sorted(range(4), key=budgets.__getitem__), [2, 0, 3, 1])
        self.assertTrue(all(value >= 400 for value in budgets))

    def test_impossible_target_is_reported(self):
        with self.assertRaises(ValueError):
            chapter_budget([1000, 1000], 500)


if __name__ == "__main__":
    unittest.main()
