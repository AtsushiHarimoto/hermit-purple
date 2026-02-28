import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

import src.services.smart_search as smart_search


class DummyHttpResponse:
    def __init__(self, status_code=200, text="", raise_error=None):
        self.status_code = status_code
        self.text = text
        self._raise_error = raise_error

    def raise_for_status(self):
        if self._raise_error:
            raise self._raise_error


def make_env(**overrides):
    values = {
        "ai_base_url": "http://localhost:9009/v1",
        "ai_api_key": "sk-test",
        "ai_model": "gemini-3.0-pro",
        "ai_fallback_model": "grok-4-fast",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_chat_response(content: str):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


class TestSmartSearchHelpers(unittest.TestCase):
    def test_clean_text_and_extract_urls(self):
        cleaned = smart_search._clean_text("A&amp;B   \n\t C")
        self.assertEqual(cleaned, "A&B C")

        links = smart_search._extract_urls("x https://a.com y https://a.com z https://b.com")
        self.assertEqual(links, ["https://a.com", "https://b.com"])

    def test_extract_urls_respects_max_count(self):
        text = " ".join(f"https://a{i}.com" for i in range(12))
        links = smart_search._extract_urls(text, max_count=3)
        self.assertEqual(len(links), 3)
        self.assertEqual(links, ["https://a0.com", "https://a1.com", "https://a2.com"])

    def test_gateway_base_from_ai_base(self):
        self.assertEqual(
            smart_search._gateway_base_from_ai_base("http://localhost:9009/v1"),
            "http://localhost:9009",
        )
        self.assertEqual(
            smart_search._gateway_base_from_ai_base("http://localhost:9009/base"),
            "http://localhost:9009/base",
        )

    def test_openai_base_from_any_base(self):
        self.assertEqual(
            smart_search._openai_base_from_any_base("http://localhost:9009"),
            "http://localhost:9009/v1",
        )
        self.assertEqual(
            smart_search._openai_base_from_any_base("http://localhost:9009/v1"),
            "http://localhost:9009/v1",
        )
        self.assertEqual(
            smart_search._openai_base_from_any_base("http://localhost:9009/custom/path"),
            "http://localhost:9009/custom/path/v1",
        )

    @patch("src.services.smart_search.socket.gethostbyname", return_value="8.8.8.8")
    def test_is_dns_ok_true(self, _):
        self.assertTrue(smart_search._is_dns_ok("google.com"))

    @patch("src.services.smart_search.socket.gethostbyname", side_effect=OSError("dns fail"))
    def test_is_dns_ok_false(self, _):
        self.assertFalse(smart_search._is_dns_ok("google.com"))

    @patch("src.services.smart_search.requests.get")
    def test_http_status_success_and_server_error(self, mock_get):
        mock_get.side_effect = [
            DummyHttpResponse(status_code=200),
            DummyHttpResponse(status_code=503),
        ]
        ok = smart_search._http_status("https://example.com", timeout=1.0)
        self.assertEqual(ok, {"ok": True, "status": 200})

        fail = smart_search._http_status("https://example.com", timeout=1.0)
        self.assertEqual(fail, {"ok": False, "status": 503})

    @patch("src.services.smart_search.requests.get", side_effect=RuntimeError("network down"))
    def test_http_status_exception(self, _):
        data = smart_search._http_status("https://example.com", timeout=1.0)
        self.assertFalse(data["ok"])
        self.assertIsNone(data["status"])
        self.assertIn("network down", data["error"])

    @patch("src.services.smart_search._is_dns_ok", side_effect=[True, False])
    @patch(
        "src.services.smart_search._http_status",
        side_effect=[
            {"ok": True, "status": 204},
            {"ok": True, "status": 200},
            {"ok": True, "status": 200},
            {"ok": False, "status": None, "error": "x"},
        ],
    )
    def test_build_network_health(self, _, __):
        data = smart_search.build_network_health("http://localhost:9009", timeout=1.0)
        self.assertEqual(data["internet"]["status"], 204)
        self.assertEqual(data["perplexity"]["status"], 200)
        self.assertEqual(data["google"]["status"], 200)
        self.assertFalse(data["gateway"]["ok"])
        self.assertEqual(data["dns"]["google.com"], True)
        self.assertEqual(data["dns"]["www.perplexity.ai"], False)


class TestSmartSearchService(unittest.TestCase):
    def make_service(self, **kwargs):
        env = make_env()
        with patch("src.services.smart_search.get_env", return_value=env), patch(
            "src.services.smart_search.OpenAI", return_value=MagicMock()
        ):
            return smart_search.SmartSearchService(**kwargs)

    @patch("src.services.smart_search.OpenAI", return_value=MagicMock())
    @patch("src.services.smart_search.get_env", return_value=make_env(ai_model="gemini-2.0-pro", ai_fallback_model="grok-5"))
    def test_init_prefers_env_models(self, _, mock_openai):
        service = smart_search.SmartSearchService()
        self.assertEqual(service.gemini_model, "gemini-2.0-pro")
        self.assertEqual(service.grok_model, "grok-5")
        mock_openai.assert_called_once()

    @patch("src.services.smart_search.OpenAI", return_value=MagicMock())
    @patch("src.services.smart_search.get_env", return_value=make_env(ai_model="qwen2.5:14b"))
    def test_init_falls_back_to_default_gemini_when_env_not_gemini(self, _, __):
        service = smart_search.SmartSearchService()
        self.assertEqual(service.gemini_model, smart_search.DEFAULT_GEMINI_MODEL)
        self.assertEqual(service.grok_model, "grok-4-fast")

    @patch("src.services.smart_search.build_network_health", return_value={"ok": True})
    def test_get_network_health_clamps_timeout(self, mock_health):
        service = self.make_service(timeout=30.0)
        data = service.get_network_health()
        self.assertEqual(data, {"ok": True})
        mock_health.assert_called_once_with(service.gateway_base_url, timeout=8.0)

    def test_call_model_success(self):
        service = self.make_service()
        service.client = MagicMock()
        service.client.chat.completions.create.return_value = make_chat_response(
            "答案 https://example.com/a\n來源 https://example.com/b"
        )
        answer, sources, elapsed = service._call_model("q", "gemini-x")
        self.assertIn("答案", answer)
        self.assertEqual(sources, ["https://example.com/a", "https://example.com/b"])
        self.assertGreaterEqual(elapsed, 0)

    def test_call_model_raises_on_empty_content(self):
        service = self.make_service()
        service.client = MagicMock()
        service.client.chat.completions.create.return_value = make_chat_response("   ")
        with self.assertRaises(smart_search.SmartSearchError):
            service._call_model("q", "gemini-x")

    @patch("src.services.smart_search.requests.get")
    def test_perplexity_fallback_success(self, mock_get):
        html = """
        <html><head><title>  Perplexity Test  </title></head>
        <body>
          <a href=\"https://www.perplexity.ai/path\">p</a>
          <a href=\"https://foo.com/1\">a</a>
          <a href=\"https://bar.com/2\">b</a>
        </body></html>
        """
        mock_get.return_value = DummyHttpResponse(status_code=200, text=html)
        service = self.make_service(timeout=10.0)
        answer, sources, elapsed = service._perplexity_fallback("hello world")
        self.assertIn("[Perplexity fallback] Perplexity Test", answer)
        self.assertTrue(sources[0].startswith("https://www.perplexity.ai/search?q=hello+world"))
        self.assertIn("https://foo.com/1", sources)
        self.assertGreaterEqual(elapsed, 0)

    @patch("src.services.smart_search.requests.get")
    def test_perplexity_fallback_link_cap(self, mock_get):
        many_links = "".join(f'<a href="https://site{i}.com/r">{i}</a>' for i in range(20))
        html = f"<html><head><title>t</title></head><body>{many_links}</body></html>"
        mock_get.return_value = DummyHttpResponse(status_code=200, text=html)
        service = self.make_service(timeout=10.0)
        _, sources, _ = service._perplexity_fallback("q")
        self.assertEqual(len(sources), 9)

    @patch("src.services.smart_search.requests.get")
    def test_google_fallback_success(self, mock_get):
        html = """
        <html><body>
          <a href=\"/url?q=https://foo.com/x&sa=U\">x</a>
          <a href=\"/url?q=https://bar.com/y&sa=U\">y</a>
        </body></html>
        """
        mock_get.return_value = DummyHttpResponse(status_code=200, text=html)
        service = self.make_service(timeout=10.0)
        answer, sources, elapsed = service._google_fallback("topic")
        self.assertIn("[Google fallback]", answer)
        self.assertIn("https://foo.com/x", sources)
        self.assertIn("https://bar.com/y", sources)
        self.assertGreaterEqual(elapsed, 0)

    @patch("src.services.smart_search.requests.get")
    def test_google_fallback_link_cap(self, mock_get):
        links_html = "".join(
            f'<a href="/url?q=https://g{i}.com/page&sa=U">{i}</a>' for i in range(20)
        )
        mock_get.return_value = DummyHttpResponse(status_code=200, text=f"<html><body>{links_html}</body></html>")
        service = self.make_service(timeout=10.0)
        _, sources, _ = service._google_fallback("topic")
        self.assertEqual(len(sources), 9)

    def test_search_returns_gemini_when_primary_succeeds(self):
        service = self.make_service()
        with patch.object(
            service,
            "get_network_health",
            return_value={
                "gateway": {"ok": True},
                "internet": {"ok": True},
                "perplexity": {"ok": True},
                "google": {"ok": True},
            },
        ), patch.object(service, "_call_model", return_value=("ans", ["u"], 11)) as call_model:
            result = service.search("q")
        self.assertEqual(result.route, "gemini")
        self.assertEqual(result.model, service.gemini_model)
        self.assertEqual(len(result.steps), 1)
        call_model.assert_called_once_with("q", service.gemini_model)

    def test_search_falls_back_to_grok(self):
        service = self.make_service()

        def call_model_side_effect(query, model):
            if model == service.gemini_model:
                raise RuntimeError("gemini down")
            return ("grok answer", ["grok-source"], 22)

        with patch.object(
            service,
            "get_network_health",
            return_value={
                "gateway": {"ok": True},
                "internet": {"ok": True},
                "perplexity": {"ok": True},
                "google": {"ok": True},
            },
        ), patch.object(service, "_call_model", side_effect=call_model_side_effect):
            result = service.search("q")

        self.assertEqual(result.route, "grok")
        self.assertEqual(result.model, service.grok_model)
        self.assertEqual(result.steps[0]["step"], "gemini")
        self.assertFalse(result.steps[0]["ok"])
        self.assertEqual(result.steps[1]["step"], "grok")
        self.assertTrue(result.steps[1]["ok"])

    def test_search_uses_perplexity_when_gateway_down(self):
        service = self.make_service()
        with patch.object(
            service,
            "get_network_health",
            return_value={
                "gateway": {"ok": False},
                "internet": {"ok": True},
                "perplexity": {"ok": True},
                "google": {"ok": True},
            },
        ), patch.object(
            service, "_perplexity_fallback", return_value=("px answer", ["px-src"], 33)
        ), patch.object(service, "_call_model") as call_model:
            result = service.search("q")

        self.assertEqual(result.route, "perplexity")
        self.assertEqual(result.sources, ["px-src"])
        call_model.assert_not_called()
        self.assertEqual(result.steps[0]["step"], "gemini")
        self.assertEqual(result.steps[1]["step"], "grok")
        self.assertEqual(result.steps[2]["step"], "perplexity")

    def test_search_uses_google_when_perplexity_fails(self):
        service = self.make_service()
        with patch.object(
            service,
            "get_network_health",
            return_value={
                "gateway": {"ok": False},
                "internet": {"ok": True},
                "perplexity": {"ok": True},
                "google": {"ok": True},
            },
        ), patch.object(
            service, "_perplexity_fallback", side_effect=requests.HTTPError("403")
        ), patch.object(service, "_google_fallback", return_value=("gg answer", ["gg-src"], 44)):
            result = service.search("q")

        self.assertEqual(result.route, "google")
        self.assertIn("perplexity:", " ".join(result.errors))
        self.assertEqual(result.steps[3]["step"], "google")
        self.assertTrue(result.steps[3]["ok"])

    def test_search_raises_when_google_fallback_throws(self):
        service = self.make_service()
        with patch.object(
            service,
            "get_network_health",
            return_value={
                "gateway": {"ok": False},
                "internet": {"ok": True},
                "perplexity": {"ok": False},
                "google": {"ok": True},
            },
        ), patch.object(service, "_google_fallback", side_effect=RuntimeError("google down")):
            with self.assertRaises(smart_search.SmartSearchError) as cm:
                service.search("q")

        self.assertIn("google down", str(cm.exception))

    def test_search_raises_when_all_routes_fail(self):
        service = self.make_service()
        with patch.object(
            service,
            "get_network_health",
            return_value={
                "gateway": {"ok": True},
                "internet": {"ok": False},
                "perplexity": {"ok": False},
                "google": {"ok": False},
            },
        ), patch.object(
            service,
            "_call_model",
            side_effect=[RuntimeError("gemini-fail"), RuntimeError("grok-fail")],
        ):
            with self.assertRaises(smart_search.SmartSearchError) as cm:
                service.search("q")

        self.assertIn("gemini-fail", str(cm.exception))
        self.assertIn("grok-fail", str(cm.exception))

    @patch("src.services.smart_search.SmartSearchService")
    def test_run_smart_search_wrapper(self, mock_service_cls):
        fake_result = smart_search.SearchResult(
            ok=True,
            route="gemini",
            query="q",
            model="gemini-3.0-pro",
            answer="a",
            sources=["s"],
            health={"gateway": {"ok": True}},
            steps=[{"step": "gemini", "ok": True, "detail": "成功", "elapsed_ms": 1}],
            errors=[],
            elapsed_ms=1,
        )
        mock_service = MagicMock()
        mock_service.search.return_value = fake_result
        mock_service_cls.return_value = mock_service

        data = smart_search.run_smart_search(query="q", timeout=12.0)
        self.assertEqual(data["route"], "gemini")
        self.assertEqual(data["answer"], "a")
        mock_service.search.assert_called_once_with("q")

    @patch("src.services.smart_search.build_network_health", return_value={"ok": True})
    @patch("src.services.smart_search.get_env", return_value=make_env(ai_base_url="http://localhost:9009/v1"))
    def test_run_smart_health_wrapper(self, _, mock_health):
        data = smart_search.run_smart_health(timeout=3.0)
        self.assertEqual(data, {"ok": True})
        mock_health.assert_called_once_with("http://localhost:9009", timeout=3.0)

    @patch("src.services.smart_search.build_network_health", return_value={"ok": True})
    @patch("src.services.smart_search.get_env", return_value=make_env())
    def test_run_smart_health_with_explicit_gateway(self, _, mock_health):
        data = smart_search.run_smart_health(
            gateway_base_url="http://localhost:19001",
            ai_base_url="http://localhost:9009/v1",
            timeout=7.0,
        )
        self.assertEqual(data, {"ok": True})
        mock_health.assert_called_once_with("http://localhost:19001", timeout=7.0)

    def test_search_result_to_dict(self):
        result = smart_search.SearchResult(
            ok=True,
            route="gemini",
            query="q",
            model=None,
            answer="x",
            sources=[],
            health={},
            steps=[],
            errors=[],
            elapsed_ms=0,
        )
        data = result.to_dict()
        self.assertEqual(data["route"], "gemini")
        self.assertEqual(
            json.dumps(data, ensure_ascii=False),
            '{"ok": true, "route": "gemini", "query": "q", "model": null, "answer": "x", "sources": [], "health": {}, "steps": [], "errors": [], "elapsed_ms": 0}',
        )


if __name__ == "__main__":
    unittest.main()