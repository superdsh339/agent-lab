from __future__ import annotations

import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bizgoal.config import COLUMNS  # noqa: E402
from bizgoal.config import AS_OF  # noqa: E402
from bizgoal.data_gen import (  # noqa: E402
    EXPECTED_ANOMALIES,
    build_rows,
    generate_dataset,
    validate_rows,
)


class DataGenTest(unittest.TestCase):
    def test_contract_shape_and_anomalies(self) -> None:
        rows = build_rows()
        self.assertGreaterEqual(len(rows), 15)
        self.assertGreaterEqual(len({r.customer for r in rows}), 8)
        self.assertEqual(len({r.contract_id for r in rows}), len(rows))
        self.assertEqual({r.contract_id for r in rows if r.revenue < 0}, {"HT-2026-0103-R"})
        self.assertEqual(
            {r.contract_id for r in rows if r.revenue != 0 and r.invoice_date.month != 10},
            {"HT-2026-0107"},
        )
        aged = [r for r in rows if r.ar_balance > 0 and (AS_OF - r.invoice_date).days > 180]
        self.assertEqual([r.contract_id for r in aged], ["HT-2026-0101"])

    def test_deterministic_csv_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.csv"
            second = Path(tmp) / "b.csv"
            m1 = generate_dataset(first, Path(tmp) / "a.json")
            m2 = generate_dataset(second, Path(tmp) / "b.json")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(m1["sha256"], m2["sha256"])
            self.assertEqual(m1["sha256"], hashlib.sha256(first.read_bytes()).hexdigest())
            with first.open(encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                self.assertEqual(reader.fieldnames, COLUMNS)
                self.assertEqual(len(list(reader)), 18)
            self.assertEqual(m1["totals"]["customers"], 9)
            self.assertEqual(m1["totals"]["revenue"], 9_480_000)
            self.assertEqual(m1["totals"]["ar"], 13_330_000)
            self.assertEqual(len(m1["anomalies"]), 3)
            self.assertEqual({a["code"] for a in m1["anomalies"]}, set(EXPECTED_ANOMALIES))

    def test_validation_rejects_bad_spec(self) -> None:
        rows = build_rows()
        rows[1].ar_balance = 1.0
        with self.assertRaises(ValueError):
            validate_rows(rows)


if __name__ == "__main__":
    unittest.main()
