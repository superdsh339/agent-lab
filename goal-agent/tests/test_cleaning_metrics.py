from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bizgoal.cleaning import clean  # noqa: E402
from bizgoal.data_gen import build_rows, write_csv  # noqa: E402
from bizgoal.data_io import read_csv_rows  # noqa: E402
from bizgoal.errors import DataQualityError  # noqa: E402
from bizgoal.metrics import compute_metrics  # noqa: E402
from bizgoal.models import RawRow  # noqa: E402


class CleaningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = build_rows()
        self.clean = clean(self.rows)

    def test_three_anomaly_codes(self) -> None:
        codes = [f.code for f in self.clean.findings]
        self.assertEqual(codes.count("REVENUE_CROSS_PERIOD"), 1)
        self.assertEqual(codes.count("REFUND_OR_REBATE"), 1)
        self.assertEqual(codes.count("AR_AGING_GT_180"), 1)

    def test_reported_vs_adjusted(self) -> None:
        self.assertAlmostEqual(self.clean.reported.revenue, 9_480_000)
        self.assertAlmostEqual(self.clean.reported.cost, 6_950_000)
        self.assertAlmostEqual(self.clean.reported.gross_margin, 2_530_000 / 9_480_000)
        self.assertAlmostEqual(self.clean.adjusted.revenue, 8_520_000)
        self.assertAlmostEqual(self.clean.adjusted.cost, 6_390_000)
        self.assertAlmostEqual(self.clean.adjusted.gross_margin, 0.25)
        self.assertAlmostEqual(self.clean.stats["removed_revenue"], 960_000)
        self.assertAlmostEqual(self.clean.stats["removed_cost"], 560_000)

    def test_cross_period_row_zeroed_and_refund_kept(self) -> None:
        cross = next(r for r in self.clean.rows if r.contract_id == "HT-2026-0107")
        self.assertEqual(cross.revenue, 0)
        self.assertEqual(cross.cost, 0)
        self.assertTrue(cross.adjusted)
        refund = next(r for r in self.clean.rows if r.contract_id == "HT-2026-0103-R")
        self.assertEqual(refund.revenue, -180_000)
        self.assertEqual(refund.cash, -180_000)

    def test_structural_errors_raise(self) -> None:
        rows = build_rows()
        rows[1].contract_id = rows[2].contract_id
        with self.assertRaises(DataQualityError):
            clean(rows)

    def test_ar_mismatch_raises(self) -> None:
        rows = build_rows()
        rows[1].ar_balance += 1
        with self.assertRaises(DataQualityError):
            clean(rows)

    def test_too_few_rows_raises(self) -> None:
        with self.assertRaises(DataQualityError):
            clean(build_rows()[:14])

    def test_read_csv_rejects_missing_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            text = (ROOT / "data" / "business_2026_10.csv").read_text(encoding="utf-8")
            path.write_text(text.replace("本月成本", "成本", 1), encoding="utf-8")
            with self.assertRaises(DataQualityError) as ctx:
                read_csv_rows(path)
            self.assertIn("本月成本", str(ctx.exception))

    def test_csv_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.csv"
            write_csv(self.rows, path)
            loaded = read_csv_rows(path)
            self.assertEqual(len(loaded), len(self.rows))
            self.assertEqual(loaded[0].contract_id, self.rows[0].contract_id)
            self.assertAlmostEqual(sum(r.revenue for r in loaded), 9_480_000)


class MetricsTest(unittest.TestCase):
    def test_baseline_metrics(self) -> None:
        metrics = compute_metrics(clean(build_rows()).rows)
        self.assertAlmostEqual(metrics.revenue, 8_520_000)
        self.assertAlmostEqual(metrics.gross_margin, 0.25)
        self.assertAlmostEqual(metrics.ar, 13_330_000)
        self.assertAlmostEqual(metrics.dso_days, round(13_330_000 / 8_520_000 * 31, 2))
        self.assertAlmostEqual(metrics.aging_buckets[">180"], 1_500_000)
        self.assertAlmostEqual(metrics.aging_buckets["91-180"], 4_700_000)
        self.assertEqual(metrics.collection_rate, round(4_690_000 / 8_520_000, 4))
        self.assertTrue(any(item["contract_id"] == "HT-2026-0101" for item in metrics.top_ar))

    def test_zero_revenue_guard(self) -> None:
        row = RawRow(
            customer="X",
            contract_id="C1",
            contract_amount=0,
            revenue=0,
            cost=0,
            cash=0,
            invoice_date=date(2026, 10, 1),
            terms_days=30,
            ar_balance=100,
        )
        metrics = compute_metrics([row])
        self.assertEqual(metrics.gross_margin, 0)
        self.assertEqual(metrics.dso_days, 0)


if __name__ == "__main__":
    unittest.main()
