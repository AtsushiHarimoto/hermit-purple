import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.interface.cli as cli_mod
import src.mcp_server as mcp_server


class _FakePluginManager:
    def discover_plugins(self, _dirs):
        return None

    def list_plugins(self):
        return []

    def get_plugin(self, _name):
        return None


class TestCliE2E(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_search_health_raw(self):
        payload = {
            "gateway": {"ok": True},
            "internet": {"ok": True},
            "perplexity": {"ok": False},
            "google": {"ok": True},
            "elapsed_ms": 12,
        }
        with patch("src.interface.cli.get_plugin_manager", return_value=_FakePluginManager()), patch(
            "src.interface.cli.run_smart_health", return_value=payload
        ):
            result = self.runner.invoke(cli_mod.app, ["search-health", "--raw"])

        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["gateway"]["ok"], True)
        self.assertEqual(data["elapsed_ms"], 12)

    def test_search_raw(self):
        payload = {
            "route": "gemini",
            "model": "gemini-3.0-pro",
            "answer": "ok",
            "sources": ["https://x.com"],
            "health": {
                "gateway": {"ok": True},
                "internet": {"ok": True},
                "perplexity": {"ok": True},
                "google": {"ok": True},
            },
            "errors": [],
        }
        with patch("src.interface.cli.get_plugin_manager", return_value=_FakePluginManager()), patch(
            "src.interface.cli.run_smart_search", return_value=payload
        ):
            result = self.runner.invoke(cli_mod.app, ["search", "OpenClaw", "--raw"])

        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["route"], "gemini")
        self.assertEqual(data["model"], "gemini-3.0-pro")

    def test_search_human_output(self):
        payload = {
            "route": "google",
            "model": None,
            "answer": "Google answer",
            "sources": ["https://g.com"],
            "health": {
                "gateway": {"ok": False},
                "internet": {"ok": True},
                "perplexity": {"ok": False},
                "google": {"ok": True},
            },
            "errors": ["gemini failed"],
        }
        with patch("src.interface.cli.get_plugin_manager", return_value=_FakePluginManager()), patch(
            "src.interface.cli.run_smart_search", return_value=payload
        ):
            result = self.runner.invoke(cli_mod.app, ["search", "fallback-check"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Route:", result.stdout)
        self.assertIn("Answer", result.stdout)
        self.assertIn("Sources", result.stdout)
        self.assertIn("Fallback notes", result.stdout)

    def test_search_failure(self):
        with patch("src.interface.cli.get_plugin_manager", return_value=_FakePluginManager()), patch(
            "src.interface.cli.run_smart_search", side_effect=RuntimeError("boom")
        ):
            result = self.runner.invoke(cli_mod.app, ["search", "x"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("Smart search failed: boom", result.stdout)


class TestMcpE2E(unittest.TestCase):
    def test_smart_web_health_success(self):
        with patch("src.mcp_server.run_smart_health", return_value={"gateway": {"ok": True}}):
            raw = mcp_server.smart_web_health(timeout=3)
        data = json.loads(raw)
        self.assertEqual(data["gateway"]["ok"], True)

    def test_smart_web_health_failure(self):
        with patch("src.mcp_server.run_smart_health", side_effect=RuntimeError("fail")):
            raw = mcp_server.smart_web_health(timeout=3)
        data = json.loads(raw)
        self.assertEqual(data["ok"], False)
        self.assertIn("fail", data["error"])

    def test_smart_web_search_success(self):
        with patch(
            "src.mcp_server.run_smart_search",
            return_value={"ok": True, "route": "grok", "answer": "done", "query": "q"},
        ):
            raw = mcp_server.smart_web_search(query="q", timeout=5)
        data = json.loads(raw)
        self.assertEqual(data["ok"], True)
        self.assertEqual(data["route"], "grok")

    def test_smart_web_search_failure(self):
        with patch("src.mcp_server.run_smart_search", side_effect=RuntimeError("fail2")):
            raw = mcp_server.smart_web_search(query="q", timeout=5)
        data = json.loads(raw)
        self.assertEqual(data["ok"], False)
        self.assertEqual(data["route"], "none")
        self.assertIn("fail2", data["error"])


if __name__ == "__main__":
    unittest.main()