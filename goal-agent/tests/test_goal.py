from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bizgoal.errors import GoalParseError  # noqa: E402
from bizgoal.goal import goal_from_numbers, parse_goal  # noqa: E402


class GoalTest(unittest.TestCase):
    def test_default_goal(self) -> None:
        goal = parse_goal("本月毛利提 3 个点，回款周期压到 45 天以内", 0.25)
        self.assertAlmostEqual(goal.margin_delta_pp, 3)
        self.assertAlmostEqual(goal.dso_target_days, 45)
        self.assertAlmostEqual(goal.target_margin, 0.28)
        self.assertEqual(goal.parser, "rule")

    def test_variants(self) -> None:
        cases = [
            ("毛利率提升3个百分点，DSO不超过45天", 3, 45),
            ("回款压到45天以内，毛利提三个点", 3, 45),
            ("毛利率提升 3.5 个百分点，回款周期 40 天内", 3.5, 40),
            ("毛利提高2个点，45 天内回款", 2, 45),
        ]
        for text, delta, dso in cases:
            with self.subTest(text=text):
                goal = parse_goal(text, 0.20)
                self.assertAlmostEqual(goal.margin_delta_pp, delta)
                self.assertAlmostEqual(goal.dso_target_days, dso)

    def test_missing_parts_raise(self) -> None:
        with self.assertRaises(GoalParseError):
            parse_goal("毛利提 3 个点", 0.25)
        with self.assertRaises(GoalParseError):
            parse_goal("回款周期 45 天以内", 0.25)
        with self.assertRaises(GoalParseError):
            parse_goal("毛利提 40 个点，回款周期 45 天以内", 0.25)
        with self.assertRaises(GoalParseError):
            parse_goal("", 0.25)

    def test_goal_from_numbers(self) -> None:
        goal = goal_from_numbers("口述", 3, 45, 0.25, parser="llm")
        self.assertAlmostEqual(goal.target_margin, 0.28)
        self.assertEqual(goal.parser, "llm")


if __name__ == "__main__":
    unittest.main()
