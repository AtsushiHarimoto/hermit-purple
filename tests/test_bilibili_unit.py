"""
BilibiliScraper + BilibiliSource 單元測試

用途：驗證 3 層降級鏈、解析邏輯、平台過濾
依賴：unittest, unittest.mock（不需要網路）
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.models import Platform, SourceTier
from src.scrapers.base import ScrapeResult
from src.sources.base import SourceResult


# ── Helpers ──────────────────────────────────────────────────────────

def _make_ytdlp_entry(**overrides):
    """建立模擬 yt-dlp entry"""
    base = {
        "id": "BV1234567890",
        "title": "Test Video",
        "description": "Test description about AI",
        "view_count": 10000,
        "like_count": 500,
        "comment_count": 100,
        "danmaku_count": 200,
        "uploader": "TestUP",
        "upload_date": "20260220",
        "duration": 600,
    }
    base.update(overrides)
    return base


def _make_web_api_item(**overrides):
    """建立模擬 Bilibili Web API item"""
    base = {
        "bvid": "BV1234567890",
        "title": "<em>Test</em> Video",
        "description": "Test description",
        "play": 10000,
        "like": 500,
        "review": 100,
        "video_review": 200,
        "author": "TestUP",
        "pubdate": 1740000000,
        "duration": "10:00",
    }
    base.update(overrides)
    return base


def _mock_config(min_views=5000, max_results=20):
    """建立模擬 config"""
    bilibili_cfg = SimpleNamespace(min_views=min_views, max_results=max_results)
    platforms = SimpleNamespace(bilibili=bilibili_cfg)
    return SimpleNamespace(platforms=platforms)


# ── BilibiliScraper Tests ────────────────────────────────────────────

class TestExtractBvId(unittest.TestCase):
    """測試 BV 號提取"""

    def _get_cls(self):
        from src.scrapers.bilibili_scraper import BilibiliScraper
        return BilibiliScraper

    def test_bv_from_id(self):
        result = self._get_cls()._extract_bv_id({"id": "BV1x411c7ZRa"})
        self.assertEqual(result, "BV1x411c7ZRa")

    def test_bv_from_url(self):
        entry = {"id": "12345", "url": "https://www.bilibili.com/video/BV1x411c7ZRa"}
        result = self._get_cls()._extract_bv_id(entry)
        self.assertEqual(result, "BV1x411c7ZRa")

    def test_numeric_id_returns_none(self):
        """純數字 ID 無法組成有效 URL，應返回 None"""
        result = self._get_cls()._extract_bv_id({"id": "12345"})
        self.assertIsNone(result)

    def test_invalid_bv_like_id_rejected(self):
        """含非法字元的 BV-like ID 應被拒絕"""
        result = self._get_cls()._extract_bv_id({"id": "BV123_567890"})
        self.assertIsNone(result)

    def test_short_bv_id_rejected(self):
        """長度不足 12 的 BV ID 應被拒絕"""
        result = self._get_cls()._extract_bv_id({"id": "BV1x411c7ZR"})
        self.assertIsNone(result)

    def test_empty_entry(self):
        result = self._get_cls()._extract_bv_id({})
        self.assertIsNone(result)


class TestParseUploadDate(unittest.TestCase):
    """測試日期解析"""

    def _get_cls(self):
        from src.scrapers.bilibili_scraper import BilibiliScraper
        return BilibiliScraper

    def test_yyyymmdd_format(self):
        result = self._get_cls()._parse_upload_date({"upload_date": "20260220"})
        self.assertEqual(result.year, 2026)
        self.assertEqual(result.month, 2)

    def test_unix_timestamp_from_timestamp_field(self):
        result = self._get_cls()._parse_upload_date({"upload_date": "", "timestamp": 1740000000})
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_pubdate_via_timestamp_field(self):
        """Web API pubdate 應透過 timestamp 字段正確解析"""
        entry = {"upload_date": "", "timestamp": 1740000000}
        result = self._get_cls()._parse_upload_date(entry)
        self.assertIsNotNone(result)

    def test_empty_returns_none(self):
        result = self._get_cls()._parse_upload_date({})
        self.assertIsNone(result)


class TestNormalizeWebApiEntry(unittest.TestCase):
    """測試 Web API 結果標準化"""

    def _get_cls(self):
        from src.scrapers.bilibili_scraper import BilibiliScraper
        return BilibiliScraper

    def test_html_stripped_from_title(self):
        item = _make_web_api_item(title="<em class='keyword'>AI</em> Tools")
        result = self._get_cls()._normalize_web_api_entry(item)
        self.assertNotIn("<", result["title"])
        self.assertEqual(result["title"], "AI Tools")

    def test_bvid_as_id(self):
        item = _make_web_api_item(bvid="BV1abcdefghij")
        result = self._get_cls()._normalize_web_api_entry(item)
        self.assertEqual(result["id"], "BV1abcdefghij")

    def test_pubdate_stored_as_timestamp(self):
        """pubdate 應存在 timestamp 字段（整數），而非 upload_date"""
        item = _make_web_api_item(pubdate=1740000000)
        result = self._get_cls()._normalize_web_api_entry(item)
        self.assertEqual(result["timestamp"], 1740000000)
        self.assertEqual(result["upload_date"], "")


class TestExtractVideoItems(unittest.TestCase):
    """測試 Web API 回包解析（dict 和 list 兩種結構）"""

    def _get_cls(self):
        from src.scrapers.bilibili_scraper import BilibiliScraper
        return BilibiliScraper

    def test_dict_structure(self):
        """result 為 dict 時，從 result["video"] 提取"""
        data = {"video": [{"bvid": "BV1"}, {"bvid": "BV2"}]}
        items = self._get_cls()._extract_video_items(data)
        self.assertEqual(len(items), 2)

    def test_list_structure_grouped(self):
        """result 為 list 時，從 result_type=video 的 group 提取"""
        data = [{"result_type": "video", "data": [{"bvid": "BV1"}]}]
        items = self._get_cls()._extract_video_items(data)
        self.assertEqual(len(items), 1)

    def test_list_structure_flat(self):
        """result 為扁平 list 時，直接有 bvid 的為影片"""
        data = [{"bvid": "BV1", "title": "test"}]
        items = self._get_cls()._extract_video_items(data)
        self.assertEqual(len(items), 1)

    def test_empty_returns_empty(self):
        self.assertEqual(self._get_cls()._extract_video_items({}), [])
        self.assertEqual(self._get_cls()._extract_video_items([]), [])
        self.assertEqual(self._get_cls()._extract_video_items("unexpected"), [])


class TestScrapeWithFallback(unittest.TestCase):
    """測試 3 層降級鏈"""

    @patch("src.scrapers.bilibili_scraper.get_config")
    def test_ytdlp_success_skips_webapi(self, mock_config):
        """yt-dlp 成功時不應調用 Web API"""
        mock_config.return_value = _mock_config()

        from src.scrapers.bilibili_scraper import BilibiliScraper
        scraper = BilibiliScraper()
        scraper._search_ytdlp = MagicMock(return_value=[_make_ytdlp_entry()])
        scraper._search_web_api = MagicMock(return_value=None)

        results = scraper.scrape(["AI"], days=30)
        scraper._search_ytdlp.assert_called_once()
        scraper._search_web_api.assert_not_called()
        self.assertEqual(len(results), 1)

    @patch("src.scrapers.bilibili_scraper.get_config")
    def test_ytdlp_fail_falls_to_webapi(self, mock_config):
        """yt-dlp 失敗時應降級到 Web API"""
        mock_config.return_value = _mock_config()

        from src.scrapers.bilibili_scraper import BilibiliScraper
        scraper = BilibiliScraper()
        scraper._search_ytdlp = MagicMock(return_value=None)
        scraper._search_web_api = MagicMock(return_value=[_make_ytdlp_entry()])

        results = scraper.scrape(["AI"], days=30)
        scraper._search_web_api.assert_called_once()
        self.assertEqual(len(results), 1)

    @patch("src.scrapers.bilibili_scraper.get_config")
    def test_both_fail_returns_empty(self, mock_config):
        """兩層都失敗時返回空列表"""
        mock_config.return_value = _mock_config()

        from src.scrapers.bilibili_scraper import BilibiliScraper
        scraper = BilibiliScraper()
        scraper._search_ytdlp = MagicMock(return_value=None)
        scraper._search_web_api = MagicMock(return_value=None)

        results = scraper.scrape(["AI"], days=30)
        self.assertEqual(results, [])

    @patch("src.scrapers.bilibili_scraper.get_config")
    def test_min_views_filter(self, mock_config):
        """低於 min_views 的應被過濾"""
        mock_config.return_value = _mock_config(min_views=5000)

        from src.scrapers.bilibili_scraper import BilibiliScraper
        scraper = BilibiliScraper()
        low_views = _make_ytdlp_entry(view_count=100)
        high_views = _make_ytdlp_entry(id="BV9876543210", view_count=10000)
        scraper._search_ytdlp = MagicMock(return_value=[low_views, high_views])

        results = scraper.scrape(["AI"], days=30)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].external_id, "BV9876543210")


# ── BilibiliSource Tests ─────────────────────────────────────────────

class TestBilibiliSource(unittest.TestCase):
    """測試 Source adapter 和 Tier 2 fallback"""

    def test_fetch_converts_scrape_results(self):
        """fetch() 應將 ScrapeResult 轉為 SourceResult"""
        from src.sources.bilibili import BilibiliSource

        source = BilibiliSource()
        mock_result = ScrapeResult(
            platform=Platform.BILIBILI,
            external_id="BV1234567890",
            title="Test",
            url="https://www.bilibili.com/video/BV1234567890",
            author="TestUP",
            metrics={"view_count": 10000},
        )
        source._scraper = MagicMock()
        source._scraper.scrape.return_value = [mock_result]

        results = source.fetch(["AI"], days=7)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_tier, SourceTier.DIRECT_API)
        self.assertEqual(results[0].raw_confidence, 0.6)

    def test_fallback_tier2_filters_non_bilibili(self):
        """Tier 2 fallback 應過濾非 Bilibili 結果"""
        from src.sources.bilibili import BilibiliSource

        source = BilibiliSource()
        source._scraper = MagicMock()
        source._scraper.scrape.return_value = []  # empty → triggers fallback

        # Mock registry with a Tier 2 engine
        mock_engine = MagicMock()
        bilibili_result = SourceResult(
            platform=Platform.BILIBILI,
            source_tier=SourceTier.GROK_SEARCH,
            external_id="BV1234567890",
            title="Bilibili video",
            url="https://www.bilibili.com/video/BV1234567890",
            author="UP",
        )
        github_result = SourceResult(
            platform=Platform.GITHUB,
            source_tier=SourceTier.GROK_SEARCH,
            external_id="repo123",
            title="Some repo",
            url="https://github.com/user/repo",
            author="user",
        )
        mock_engine.fetch.return_value = [bilibili_result, github_result]

        mock_registry = MagicMock()
        mock_registry.get_tier2_engines.return_value = [mock_engine]
        source.set_registry(mock_registry)

        results = source.fetch(["AI"], days=7)
        # Should only contain the Bilibili result
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].platform, Platform.BILIBILI)

    def test_no_registry_no_fallback(self):
        """無 registry 時不應嘗試 Tier 2 fallback"""
        from src.sources.bilibili import BilibiliSource

        source = BilibiliSource()
        source._scraper = MagicMock()
        source._scraper.scrape.return_value = []

        results = source.fetch(["AI"], days=7)
        self.assertEqual(results, [])


class TestBvPatternStrict(unittest.TestCase):
    """測試 BV 號正則的嚴格性"""

    def test_no_unicode(self):
        """BV 正則不應匹配含 Unicode 的字串"""
        import re
        pattern = re.compile(r"(BV[A-Za-z0-9]{10})")
        self.assertIsNone(pattern.search("BV123_567890"))  # underscore
        self.assertIsNotNone(pattern.search("BV1x411c7ZRa"))  # valid BV + extra char


if __name__ == "__main__":
    unittest.main()
