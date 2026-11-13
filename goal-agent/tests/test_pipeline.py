from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bizgoal.chaos import ChaosConfig  # noqa: E402
from bizgoal.llm import LLMClient, LLMConfig  # noqa: E402
from bizgoal.pipeline import run_pipeline  # noqa: E402

PYTHON = sys.executable


class PipelineTest(unittest.TestCase):
    def test_happy_path_artifacts_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pipeline(out_dir=Path(tmp) / "run", llm_mode="off")
            self.assertEqual(result["status"], "met", result.get("error"))
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(len(result["execution"]["receipts"]), 4)
            self.assertTrue(all(r["status"] == "executed" for r in result["execution"]["receipts"]))
            self.assertTrue(result["plan"]["meets"])
            run_dir = Path(tmp) / "run"
            for name in [
                "report.md",
                "run.log",
                "events.jsonl",
                "cleaning_report.json",
                "metrics_before.json",
                "goal.json",
                "diagnosis.json",
                "actions_library.json",
                "plan.json",
                "action_receipts.json",
                "metrics_after.json",
                "summary.json",
                "llm_trace.json",
            ]:
                self.assertTrue((run_dir / name).exists(), name)
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            for text in ["目标达成", "闭环校验", "数据清洗", "兜底", "25.00%", "28.25%", "44.24"]:
                self.assertIn(text, report)
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "met")
            self.assertEqual(summary["verification"]["total"], 12)
            self.assertEqual(summary["verification"]["passed"], 12)

    def test_llm_down_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pipeline(
                out_dir=Path(tmp) / "run",
                llm_mode="auto",
                chaos=ChaosConfig(llm_down=True),
            )
            self.assertEqual(result["status"], "met")
            self.assertEqual(result["narrative"]["source"], "fallback")
            self.assertTrue(any(f["stage"] == "narrative" for f in result["fallbacks"]))
            report = (Path(tmp) / "run" / "report.md").read_text(encoding="utf-8")
            self.assertIn("规则模板", report)

    def test_action_failure_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pipeline(
                out_dir=Path(tmp) / "run",
                llm_mode="off",
                chaos=ChaosConfig(action_fail_ids={"C-AR-0116"}),
            )
            self.assertEqual(result["status"], "met_after_recovery", result.get("error"))
            self.assertEqual(result["exit_code"], 0)
            receipt_status = {r["action_id"]: r["status"] for r in result["execution"]["receipts"]}
            self.assertEqual(receipt_status["C-AR-0116"], "failed")
            self.assertGreater(len(result["execution"]["effective_actions"]), 4)
            self.assertTrue(result["execution"]["recovery"]["meets"])
            report = (Path(tmp) / "run" / "report.md").read_text(encoding="utf-8")
            self.assertIn("兜底重规划", report)
            self.assertIn("实际生效", report)
            summary = json.loads((Path(tmp) / "run" / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["receipts"]["failed"], 1)

    def test_data_corruption_stops_with_failure_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pipeline(
                out_dir=Path(tmp) / "run",
                llm_mode="off",
                chaos=ChaosConfig(data_corrupt=True),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exit_code"], 2)
            self.assertIn("本月成本", result["error"])
            report = (Path(tmp) / "run" / "report.md").read_text(encoding="utf-8")
            self.assertIn("运行失败", report)
            self.assertIn("本月成本", report)
            events = (Path(tmp) / "run" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertTrue(any("ERROR" in line for line in events))

    def test_infeasible_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pipeline(
                out_dir=Path(tmp) / "run",
                goal_text="本月毛利提 12 个点，回款周期压到 20 天以内",
                llm_mode="off",
            )
            self.assertEqual(result["status"], "infeasible")
            self.assertEqual(result["exit_code"], 3)
            self.assertEqual(result["plan"]["selected"], [])
            self.assertTrue(result["metrics_after"]["gaps"])
            report = (Path(tmp) / "run" / "report.md").read_text(encoding="utf-8")
            self.assertIn("目标不可达", report)
            self.assertIn("兼容动作", report)

    def test_missing_default_data_regenerates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.csv"
            with patch("bizgoal.pipeline.DEFAULT_DATA_PATH", missing):
                result = run_pipeline(out_dir=Path(tmp) / "run", llm_mode="off")
            self.assertEqual(result["status"], "met")
            self.assertTrue((Path(tmp) / "run" / "generated" / "business_2026_10.csv").exists())
            self.assertTrue(any(f["stage"] == "ingest" for f in result["fallbacks"]))

    def test_llm_enabled_path_labels_model_and_parses_goal(self) -> None:
        """模型可用时：规则未命中的口述由 LLM 解析，摘要标注 LLM，数字仍来自引擎。"""
        cfg = LLMConfig(
            provider="deepseek",
            base_url="https://example.invalid/v1",
            model="deepseek-chat",
            api_key="test",
            enabled=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch("bizgoal.pipeline.LLMConfig.from_env", return_value=cfg), patch.object(
                LLMClient, "parse_goal", return_value={"margin_delta_pp": 3.0, "dso_target_days": 45.0}
            ), patch.object(
                LLMClient, "narrate", return_value="- 目标毛利率 28.00%，预测 28.25%。"
            ):
                result = run_pipeline(
                    out_dir=Path(tmp) / "run",
                    goal_text="本月毛利率提升三个百分点，账期压到四十五天",
                    llm_mode="auto",
                )
            self.assertEqual(result["status"], "met", result.get("error"))
            self.assertEqual(result["goal"]["parser"], "llm")
            self.assertEqual(result["narrative"]["source"], "llm")
            report = (Path(tmp) / "run" / "report.md").read_text(encoding="utf-8")
            self.assertIn("摘要由模型生成", report)
            self.assertIn("LLM 生成", report)

    def test_cli_exit_codes_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cli"
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            proc = subprocess.run(
                [PYTHON, "-m", "bizgoal", "run", "--out", str(out), "--llm", "off", "--json"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "met")
            demo = subprocess.run(
                [PYTHON, "-m", "bizgoal", "demo", "--out", str(Path(tmp) / "demo"), "--llm", "off", "--json"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(demo.returncode, 0, demo.stderr)
            self.assertEqual(json.loads(demo.stdout)["status"], "met")
            corrupt = subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "bizgoal",
                    "run",
                    "--out",
                    str(Path(tmp) / "bad"),
                    "--llm",
                    "off",
                    "--chaos",
                    "data-corrupt",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(corrupt.returncode, 2, corrupt.stderr)


if __name__ == "__main__":
    unittest.main()
