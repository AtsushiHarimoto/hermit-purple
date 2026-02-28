"""
Bilibili DataSource — Tier 1 (Direct API) adapter wrapping BilibiliScraper

降級策略（Fallback B）：若 scraper 返回空結果，委派 Tier 2 AI 搜尋引擎
以 site:bilibili.com 修飾關鍵詞進行搜索。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..db.models import Platform, SourceTier
from ..scrapers.bilibili_scraper import BilibiliScraper
from .base import DataSource, SourceResult, detect_platform_from_url

if TYPE_CHECKING:
    from .registry import DataSourceRegistry

logger = logging.getLogger(__name__)

__all__ = ["BilibiliSource"]

# Direct API tier base confidence (same as YouTube)
_DIRECT_CONFIDENCE = 0.6


class BilibiliSource(DataSource):
    """Tier 1 Bilibili source via yt-dlp / Web API, with Tier 2 fallback"""

    def __init__(self):
        self._scraper = BilibiliScraper()
        self._registry: DataSourceRegistry | None = None

    def set_registry(self, registry: DataSourceRegistry) -> None:
        """Inject registry after construction to avoid circular dependency"""
        self._registry = registry

    @property
    def tier(self) -> SourceTier:
        return SourceTier.DIRECT_API

    @property
    def platforms(self) -> list[Platform]:
        return [Platform.BILIBILI]

    def fetch(self, keywords: list[str], days: int = 7) -> list[SourceResult]:
        # Primary: delegate to BilibiliScraper
        scrape_results = self._scraper.scrape(keywords, days=days)
        out: list[SourceResult] = []

        for r in scrape_results:
            out.append(SourceResult(
                platform=r.platform,
                source_tier=self.tier,
                external_id=r.external_id,
                title=r.title,
                url=r.url,
                author=r.author,
                description=r.description,
                metrics=r.metrics,
                tags=r.tags,
                created_at=r.created_at,
                citation_urls=[r.url],
                raw_confidence=_DIRECT_CONFIDENCE,
            ))

        # Fallback B: if scraper returned nothing, try Tier 2 engines
        if not out and self._registry is not None:
            out = self._fallback_tier2(keywords, days)

        return out

    def _fallback_tier2(self, keywords: list[str], days: int) -> list[SourceResult]:
        """Delegate to Tier 2 AI search engines with site:bilibili.com modifier"""
        tier2_engines = self._registry.get_tier2_engines()
        if not tier2_engines:
            logger.info("[BilibiliSource] No Tier 2 engines available for fallback")
            return []

        modified_keywords = [f"{kw} site:bilibili.com" for kw in keywords]

        for engine in tier2_engines:
            try:
                engine_results = engine.fetch(modified_keywords, days=days)
                results = [
                    r for r in engine_results
                    if r.platform == Platform.BILIBILI
                    or detect_platform_from_url(r.url) == Platform.BILIBILI
                ]
                if results:
                    logger.info(
                        "[BilibiliSource] Tier 2 fallback via %s returned %d results",
                        engine.__class__.__name__, len(results),
                    )
                    return results
            except Exception as e:
                logger.warning(
                    "[BilibiliSource] Tier 2 fallback via %s failed: %s",
                    engine.__class__.__name__, e,
                )

        return []

    def health_check(self) -> bool:
        """Probe yt-dlp availability; returns True even on failure (fallbacks exist)"""
        try:
            return self._scraper.health_check()
        except Exception:
            return True

    def close(self) -> None:
        """Close scraper session to release connections"""
        self._scraper.close()
