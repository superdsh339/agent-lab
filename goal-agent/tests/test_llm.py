from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bizgoal.errors import LLMError  # noqa: E402
from bizgoal.llm import LLMClient, LLMConfig, validate_numbers  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401, ANN002
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        systems = " ".join(m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "system")
        if "只输出 JSON" in systems:
            content = '{"margin_delta_pp": 3, "dso_target_days": 45}'
        else:
            content = self.server.override or (
                "- 基线毛利率 25.00%，目标 28.00%，预测 28.25%。\n"
                "- 回款周期 48.50 天，目标 ≤45 天，预测 44.24 天。\n"
                "- 共 4 个动作，预计回款 117.00 万元。"
            )
        body = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeLLMServer:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.override = ""
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class LLMTest(unittest.TestCase):
    def test_env_config(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "DEEPSEEK_", "DASHSCOPE_"))}
        with patch.dict(os.environ, env, clear=True):
            disabled = LLMConfig.from_env("auto")
            self.assertFalse(disabled.enabled)
            self.assertIn("DEEPSEEK_API_KEY", disabled.disabled_reason)
            enabled = LLMConfig.from_env("auto")
        with patch.dict(os.environ, {**env, "DEEPSEEK_API_KEY": "test"}, clear=True):
            config = LLMConfig.from_env("auto")
            self.assertTrue(config.enabled)
            self.assertEqual(config.provider, "deepseek")
            self.assertEqual(config.model, "deepseek-chat")
        with patch.dict(os.environ, {**env, "LLM_PROVIDER": "ollama"}, clear=True):
            local = LLMConfig.from_env("auto")
            self.assertTrue(local.enabled)
            self.assertIn("11434", local.base_url)
        with patch.dict(os.environ, {**env, "DEEPSEEK_API_KEY": "test"}, clear=True):
            self.assertFalse(LLMConfig.from_env("off").enabled)

    def test_fake_server_parse_and_narrate(self) -> None:
        server = FakeLLMServer()
        try:
            config = LLMConfig(
                provider="openai-compatible",
                base_url=server.base_url,
                model="fake",
                api_key="x",
                timeout=3,
                max_retries=0,
            )
            client = LLMClient(config)
            nums = client.parse_goal("本月毛利提 3 个点，回款周期压到 45 天以内")
            self.assertEqual(nums, {"margin_delta_pp": 3.0, "dso_target_days": 45.0})
            facts = {
                "基线毛利率_pct": 25.0,
                "目标毛利率_pct": 28.0,
                "预测毛利率_pct": 28.25,
                "基线DSO_天": 48.5,
                "目标DSO_天": 45,
                "预测DSO_天": 44.24,
                "动作数": 4,
                "预计回款_万元": 117.0,
            }
            text = client.narrate(facts)
            self.assertIn("25.00%", text)
            self.assertTrue(client.check()["ok"])
        finally:
            server.close()

    def test_hallucinated_number_rejected(self) -> None:
        server = FakeLLMServer()
        try:
            server.server.override = "- 毛利率 99.99%，预测 28.25%。"
            config = LLMConfig(
                provider="openai-compatible",
                base_url=server.base_url,
                model="fake",
                api_key="x",
                timeout=3,
                max_retries=0,
            )
            client = LLMClient(config)
            with self.assertRaises(LLMError):
                client.narrate({"预测毛利率_pct": 28.25})
        finally:
            server.close()

    def test_unreachable_server_returns_error_quickly(self) -> None:
        config = LLMConfig(
            provider="openai-compatible",
            base_url="http://127.0.0.1:1/v1",
            model="fake",
            api_key="x",
            timeout=0.4,
            max_retries=0,
        )
        client = LLMClient(config)
        self.assertFalse(client.check()["ok"])
        with self.assertRaises(LLMError):
            client.parse_goal("毛利提3个点，回款周期45天")

    def test_validate_numbers(self) -> None:
        ok, bad = validate_numbers("毛利率 28.25%，共 4 个动作", {"margin": 28.25, "n": 4})
        self.assertTrue(ok, bad)
        ok, bad = validate_numbers("毛利率 99%", {"margin": 28.25})
        self.assertFalse(ok)
        self.assertEqual(bad, "99")


if __name__ == "__main__":
    unittest.main()
