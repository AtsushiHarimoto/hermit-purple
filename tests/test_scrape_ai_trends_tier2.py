import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.mcp_server as mcp_mod  # noqa: E402
from src.db.models import Platform, SourceTier  # noqa: E402
from src.scrapers.base import ScrapeResult  # noqa: E402
from src.sources.base import SourceResult  # noqa: E402


class _ResourceQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return None


class _FakeDB:
    def __init__(self):
        self.added = []

    def query(self, _model):
        return _ResourceQuery()

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class _FakeRegistry:
    def __init__(self, engines):
        self._engines = engines

    def get_tier2_engines(self):
        return self._engines


@patch.object(mcp_mod, "init_db")
@patch.object(mcp_mod, "get_db")
class TestScrapeAiTrendsTier2(unittest.TestCase):
    @patch("src.mcp_server.build_default_registry", create=True)
    @patch("src.mcp_server.AIScraper")
    def test_prefers_tier2_results_when_available(self, MockAIScraper, mock_build_registry, mock_get_db, _mock_init_db):
        """Tier2 可用時應優先使用（含 Perplexica），而非只依賴 AIScraper。"""
        # Arrange: Tier2 returns 1 result, AIScraper returns empty
        mock_engine = MagicMock()
        mock_engine.__class__.__name__ = "PerplexicaSource"
        mock_engine.fetch.return_value = [
            SourceResult(
                platform=Platform.WEB_OTHER,
                source_tier=SourceTier.PERPLEXICA,
                external_id="perp-1",
                title="Perplexica Hit",
                url="https://example.com/perp",
                author="perp",
                description="desc",
                created_at=datetime.now(),
                citation_urls=["https://example.com/perp"],
                raw_confidence=0.8,
            )
        ]
        mock_build_registry.return_value = _FakeRegistry([mock_engine])

        scraper_inst = MockAIScraper.return_value
        scraper_inst.scrape.return_value = []

        fake_db = _FakeDB()
        mock_get_db.return_value = fake_db

        # Act
        raw = mcp_mod.scrape_ai_trends("agent, mcp", days=3, category="")
        data = json.loads(raw)

        # Assert
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["scraped"], 1)
        self.assertEqual(len(fake_db.added), 1)
        self.assertEqual(fake_db.added[0].source_tier, SourceTier.PERPLEXICA.value)
        self.assertEqual(fake_db.added[0].citation_urls, ["https://example.com/perp"])
        scraper_inst.scrape.assert_not_called()

    @patch("src.mcp_server.build_default_registry", create=True)
    @patch("src.mcp_server.AIScraper")
    def test_fallback_to_aiscraper_when_tier2_empty(self, MockAIScraper, mock_build_registry, mock_get_db, _mock_init_db):
        """Tier2 無結果時，應回退到 AIScraper。"""
        # Arrange: Tier2 empty
        mock_engine = MagicMock()
        mock_engine.__class__.__name__ = "PerplexicaSource"
        mock_engine.fetch.return_value = []
        mock_build_registry.return_value = _FakeRegistry([mock_engine])

        # AIScraper returns 1 legacy result
        scraper_inst = MockAIScraper.return_value
        scraper_inst.scrape.return_value = [
            ScrapeResult(
                platform=Platform.AI_SEARCH,
                external_id="ai-1",
                title="Legacy Hit",
                url="https://example.com/legacy",
                author="legacy",
                description="legacy desc",
                created_at=datetime.now(),
            )
        ]

        fake_db = _FakeDB()
        mock_get_db.return_value = fake_db

        # Act
        raw = mcp_mod.scrape_ai_trends("agent, mcp", days=3, category="")
        data = json.loads(raw)

        # Assert
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["scraped"], 1)
        self.assertEqual(len(fake_db.added), 1)
        self.assertEqual(fake_db.added[0].source_tier, "ai_search")
        scraper_inst.scrape.assert_called_once()


if __name__ == "__main__":
    unittest.main()
