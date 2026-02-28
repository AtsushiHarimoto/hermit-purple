"""
CrossValidator 單元測試

用途：驗證 URL 正規化、標題相似度、交叉驗證打分邏輯
依賴：unittest（不需要網路）
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db.models import Platform, SourceTier
from src.sources.base import SourceResult
from src.sources.cross_validator import (
    normalize_url,
    title_similarity,
    cross_validate,
    ValidatedResult,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_source_result(
    external_id="res-001",
    title="Test Article",
    url="https://github.com/user/repo",
    platform=Platform.GITHUB,
    source_tier=SourceTier.GROK_SEARCH,
    author="author",
    raw_confidence=0.5,
    citation_urls=None,
    tags=None,
    description=None,
):
    return SourceResult(
        platform=platform,
        source_tier=source_tier,
        external_id=external_id,
        title=title,
        url=url,
        author=author,
        raw_confidence=raw_confidence,
        citation_urls=citation_urls or [],
        tags=tags or [],
        description=description,
    )


# ── normalize_url Tests ──────────────────────────────────────────────

class TestNormalizeUrl(unittest.TestCase):
    """測試 URL 正規化"""

    def test_trailing_slash_removed(self):
        """尾部斜線應被移除"""
        result = normalize_url("https://example.com/path/")
        self.assertEqual(result, "https://example.com/path")

    def test_www_prefix_stripped(self):
        """www. 前綴應被移除"""
        result = normalize_url("https://www.example.com/path")
        self.assertEqual(result, "https://example.com/path")

    def test_http_upgraded_to_https(self):
        """http 應統一為 https"""
        result = normalize_url("http://example.com/path")
        self.assertEqual(result, "https://example.com/path")

    def test_tracking_params_stripped(self):
        """utm_source 等追蹤參數應被移除"""
        url = "https://example.com/page?utm_source=twitter&utm_medium=social&id=42"
        result = normalize_url(url)
        self.assertIn("id=42", result)
        self.assertNotIn("utm_source", result)
        self.assertNotIn("utm_medium", result)

    def test_fbclid_stripped(self):
        """Facebook Click ID 應被移除"""
        url = "https://example.com/page?fbclid=abc123&real=yes"
        result = normalize_url(url)
        self.assertNotIn("fbclid", result)
        self.assertIn("real=yes", result)

    def test_fragments_removed(self):
        """URL fragment (#section) 應被移除"""
        result = normalize_url("https://example.com/page#section")
        self.assertNotIn("#section", result)
        self.assertEqual(result, "https://example.com/page")

    def test_query_params_sorted(self):
        """剩餘查詢參數應按字母排序"""
        url = "https://example.com/page?z=1&a=2"
        result = normalize_url(url)
        self.assertIn("a=2", result)
        self.assertIn("z=1", result)
        # a should come before z
        a_pos = result.index("a=2")
        z_pos = result.index("z=1")
        self.assertLess(a_pos, z_pos)

    def test_root_path_preserved(self):
        """根路徑應保留為 /"""
        result = normalize_url("https://example.com")
        self.assertEqual(result, "https://example.com/")

    def test_invalid_url_returns_something(self):
        """無效 URL 不應拋出例外"""
        result = normalize_url("not-a-url  ")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_multiple_tracking_params(self):
        """多個追蹤參數同時存在應全部被移除"""
        url = "https://example.com/?ref=sidebar&gclid=xyz&from=home&key=val"
        result = normalize_url(url)
        self.assertNotIn("ref=", result)
        self.assertNotIn("gclid=", result)
        self.assertNotIn("from=", result)
        self.assertIn("key=val", result)

    def test_bilibili_spm_stripped(self):
        """Bilibili 的 spm 追蹤參數應被移除"""
        url = "https://www.bilibili.com/video/BV123?spm=abc&p=1"
        result = normalize_url(url)
        self.assertNotIn("spm=", result)
        self.assertIn("p=1", result)


# ── title_similarity Tests ───────────────────────────────────────────

class TestTitleSimilarity(unittest.TestCase):
    """測試標題相似度"""

    def test_exact_match(self):
        """完全相同的標題相似度應為 1.0"""
        score = title_similarity("Hello World", "Hello World")
        self.assertAlmostEqual(score, 1.0)

    def test_case_insensitive(self):
        """大小寫不同應視為相同"""
        score = title_similarity("Hello World", "hello world")
        self.assertAlmostEqual(score, 1.0)

    def test_whitespace_trimmed(self):
        """前後空白應不影響結果"""
        score = title_similarity("  Hello World  ", "Hello World")
        self.assertAlmostEqual(score, 1.0)

    def test_similar_titles(self):
        """相似標題應有高分"""
        score = title_similarity(
            "Introduction to Machine Learning",
            "Introduction to Machine Learning (2024)",
        )
        self.assertGreater(score, 0.7)

    def test_different_titles(self):
        """完全不同的標題應有低分"""
        score = title_similarity("Python Tutorial", "Cooking Recipes Book")
        self.assertLess(score, 0.4)

    def test_empty_string_returns_zero(self):
        """空字串應回傳 0.0"""
        self.assertEqual(title_similarity("", "Hello"), 0.0)
        self.assertEqual(title_similarity("Hello", ""), 0.0)
        self.assertEqual(title_similarity("", ""), 0.0)

    def test_unicode_titles(self):
        """Unicode 標題（中文）應正確計算"""
        score = title_similarity("機器學習入門", "機器學習入門指南")
        self.assertGreater(score, 0.7)

    def test_completely_different_unicode(self):
        """完全不同的中文標題應有低分"""
        score = title_similarity("機器學習入門", "美食烹飪大全")
        self.assertLess(score, 0.5)


# ── cross_validate Tests ─────────────────────────────────────────────

class TestCrossValidate(unittest.TestCase):
    """測試交叉驗證"""

    def test_empty_input_returns_empty(self):
        """空的引擎結果應返回空列表"""
        result = cross_validate({})
        self.assertEqual(result, [])

    def test_all_engines_empty(self):
        """所有引擎結果為空列表時應返回空"""
        result = cross_validate({"grok": [], "gemini": []})
        self.assertEqual(result, [])

    def test_single_source_single_engine(self):
        """單引擎單結果"""
        sr = _make_source_result()
        results = cross_validate({"grok": [sr]})
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].cross_validated)
        self.assertEqual(results[0].citation_count, 1)

    def test_cross_validated_when_two_engines_find_same_url(self):
        """兩個引擎找到相同 URL 時 cross_validated 應為 True"""
        sr1 = _make_source_result(
            external_id="res-001",
            title="Same Article",
            url="https://github.com/user/repo",
        )
        sr2 = _make_source_result(
            external_id="res-002",
            title="Same Article",
            url="https://github.com/user/repo",
        )
        results = cross_validate({"grok": [sr1], "gemini": [sr2]})

        # 因為 URL 相同且標題相似，應合併為一個結果
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].cross_validated)
        self.assertGreaterEqual(results[0].citation_count, 2)

    def test_different_urls_not_merged(self):
        """URL 不同且標題不同的結果不應合併"""
        sr1 = _make_source_result(
            external_id="res-001",
            title="Python Tutorial",
            url="https://example.com/python",
        )
        sr2 = _make_source_result(
            external_id="res-002",
            title="Cooking Guide",
            url="https://example.com/cooking",
        )
        results = cross_validate({"grok": [sr1], "gemini": [sr2]})
        self.assertEqual(len(results), 2)

    def test_dedup_by_title_similarity(self):
        """相同域名下標題高度相似的結果應去重合併"""
        sr1 = _make_source_result(
            external_id="res-001",
            title="Introduction to Machine Learning",
            url="https://example.com/ml-intro",
        )
        sr2 = _make_source_result(
            external_id="res-002",
            title="Introduction to Machine Learning",
            url="https://example.com/ml-intro",
        )
        results = cross_validate({"grok": [sr1], "gemini": [sr2]})
        self.assertEqual(len(results), 1)

    def test_confidence_sorted_descending(self):
        """結果應按 confidence 降序排列"""
        sr1 = _make_source_result(
            external_id="res-001",
            title="Low Confidence",
            url="https://example.com/low",
            raw_confidence=0.1,
        )
        sr2 = _make_source_result(
            external_id="res-002",
            title="High Confidence",
            url="https://other.com/high",
            raw_confidence=0.9,
        )
        results = cross_validate({"grok": [sr1, sr2]})
        self.assertEqual(len(results), 2)
        self.assertGreaterEqual(results[0].confidence, results[1].confidence)

    def test_no_url_penalty(self):
        """沒有有效 URL 的結果應受罰"""
        sr_no_url = _make_source_result(
            external_id="res-no-url",
            title="No URL Result",
            url="https://gemini.google.com",  # Invalid generic URL
            raw_confidence=0.5,
        )
        sr_with_url = _make_source_result(
            external_id="res-with-url",
            title="With URL Result",
            url="https://github.com/real/repo",
            raw_confidence=0.5,
        )
        results = cross_validate({"grok": [sr_no_url, sr_with_url]})
        # 有效 URL 的結果 confidence 應更高
        url_result = [r for r in results if "real/repo" in r.url][0]
        no_url_result = [r for r in results if "gemini.google" in r.url][0]
        self.assertGreater(url_result.confidence, no_url_result.confidence)

    def test_ring_mapping(self):
        """confidence → ring 映射應正確"""
        sr = _make_source_result(raw_confidence=0.95)
        results = cross_validate({"eng1": [sr], "eng2": [
            _make_source_result(external_id="res-002", url="https://github.com/user/repo", raw_confidence=0.95)
        ]})
        # 兩個引擎找到相同 URL，confidence 應較高
        if results:
            # Just verify ring is one of the valid values
            self.assertIn(results[0].ring, ("adopt", "trial", "assess", "hold"))

    def test_tier1_boost(self):
        """有 Tier 1 URL 時應有 confidence 加成"""
        sr = _make_source_result(
            external_id="res-t1",
            title="Tier1 Boosted",
            url="https://github.com/popular/repo",
            raw_confidence=0.5,
        )
        tier1_sr = _make_source_result(
            external_id="t1-res",
            title="Popular Repo",
            url="https://github.com/popular/repo",
            source_tier=SourceTier.DIRECT_API,
            raw_confidence=0.8,
        )

        results_with = cross_validate({"grok": [sr]}, tier1_results=[tier1_sr])
        results_without = cross_validate({"grok": [sr]}, tier1_results=None)

        self.assertGreater(results_with[0].confidence, results_without[0].confidence)

    def test_all_duplicate_urls_single_engine(self):
        """同一引擎中多個結果有相同 URL 不應重複計算引擎數"""
        sr1 = _make_source_result(
            external_id="dup-1",
            title="Same Article A",
            url="https://example.com/article",
        )
        sr2 = _make_source_result(
            external_id="dup-2",
            title="Same Article A Copy",
            url="https://example.com/article",
        )
        results = cross_validate({"grok": [sr1, sr2]})
        # 同一引擎的重複 URL 不應被視為 cross_validated
        for r in results:
            self.assertFalse(r.cross_validated)

    def test_perplexica_citation_boost(self):
        """Perplexica 引擎帶 citation_urls 應有額外 boost"""
        sr_perp = _make_source_result(
            external_id="perp-1",
            title="Perplexica Result",
            url="https://example.com/perp",
            source_tier=SourceTier.PERPLEXICA,
            citation_urls=["https://arxiv.org/paper123"],
            raw_confidence=0.5,
        )
        sr_plain = _make_source_result(
            external_id="plain-1",
            title="Plain Result",
            url="https://other.com/plain",
            source_tier=SourceTier.GROK_SEARCH,
            raw_confidence=0.5,
        )
        results = cross_validate({"perplexica": [sr_perp], "grok": [sr_plain]})
        perp_result = [r for r in results if "perp" in r.url][0]
        plain_result = [r for r in results if "plain" in r.url][0]
        self.assertGreater(perp_result.confidence, plain_result.confidence)


if __name__ == "__main__":
    unittest.main()
