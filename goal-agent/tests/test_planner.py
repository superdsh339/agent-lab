from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bizgoal.actions import build_action_library  # noqa: E402
from bizgoal.cleaning import clean  # noqa: E402
from bizgoal.config import AS_OF  # noqa: E402
from bizgoal.data_gen import build_rows  # noqa: E402
from bizgoal.goal import parse_goal  # noqa: E402
from bizgoal.metrics import compute_metrics  # noqa: E402
from bizgoal.planner import forecast_subset, select_plan, verify_actions  # noqa: E402


class PlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clean = clean(build_rows())
        self.metrics = compute_metrics(self.clean.rows)
        self.goal = parse_goal("本月毛利提 3 个点，回款周期压到 45 天以内", self.metrics.gross_margin)
        self.actions = build_action_library(self.clean, self.metrics, AS_OF)

    def test_action_library_covers_both_sides(self) -> None:
        kinds = {a.kind for a in self.actions}
        self.assertEqual(kinds, {"margin", "collection"})
        self.assertTrue(any(a.expected_cash > 0 for a in self.actions))
        self.assertTrue(any(a.delta_cost < 0 for a in self.actions))
        self.assertTrue(any(a.contract_ids == ["HT-2026-0101"] for a in self.actions))

    def test_plan_meets_and_is_attributable(self) -> None:
        plan = select_plan(self.metrics, self.goal, self.actions)
        self.assertTrue(plan.meets, plan.gaps)
        self.assertGreaterEqual(plan.forecast["gross_margin"], self.goal.target_margin)
        self.assertLessEqual(plan.forecast["dso_days"], self.goal.dso_target_days)
        self.assertGreaterEqual(plan.conservative["gross_margin"], self.goal.target_margin)
        self.assertLessEqual(plan.conservative["dso_days"], self.goal.dso_target_days)
        self.assertLessEqual(len(plan.selected), 5)
        self.assertEqual({a.kind for a in plan.selected}, {"margin", "collection"})
        delta_gp = sum(a.delta_gp for a in plan.selected)
        self.assertAlmostEqual(
            self.metrics.gross_profit + delta_gp, plan.forecast["gross_profit"], places=2
        )
        for action in plan.selected:
            self.assertGreaterEqual(action.confidence, 0.5)

    def test_infeasible_goal_returns_gaps_without_execution_plan(self) -> None:
        hard_goal = parse_goal("本月毛利提 12 个点，回款周期压到 20 天以内", self.metrics.gross_margin)
        plan = select_plan(self.metrics, hard_goal, self.actions)
        self.assertFalse(plan.meets)
        self.assertEqual(plan.selected, [])
        self.assertGreaterEqual(len(plan.gaps), 2)
        self.assertGreater(plan.search_stats["evaluated"], 0)

    def test_excluding_failed_action_replans(self) -> None:
        plan = select_plan(self.metrics, self.goal, self.actions, excluded_ids=["C-AR-0116"])
        self.assertTrue(plan.meets, plan.gaps)
        self.assertNotIn("C-AR-0116", {a.id for a in plan.selected})
        self.assertLessEqual(plan.forecast["dso_days"], 45)

    def test_collection_does_not_change_pnl(self) -> None:
        collection = next(a for a in self.actions if a.kind == "collection")
        forecast = forecast_subset(self.metrics, [collection])
        self.assertAlmostEqual(forecast["revenue"], self.metrics.revenue)
        self.assertAlmostEqual(forecast["gross_profit"], self.metrics.gross_profit)
        self.assertLess(forecast["ar"], self.metrics.ar)

    def test_verify_actions(self) -> None:
        plan = select_plan(self.metrics, self.goal, self.actions)
        forecast, conservative, meets, gaps = verify_actions(self.metrics, plan.selected, self.goal)
        self.assertTrue(meets)
        self.assertEqual(gaps, [])
        self.assertEqual(forecast, plan.forecast)


if __name__ == "__main__":
    unittest.main()
