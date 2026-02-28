"""
Reddit DataSource — Tier 1 (Direct API) adapter wrapping RedditScraper
"""

from __future__ import annotations

import logging

from ..db.models import Platform, SourceTier
from ..scrapers.reddit_scraper import RedditScraper
from .base import DataSource, SourceResult

logger = logging.getLogger(__name__)


class RedditSource(DataSource):
    """Tier 1 Reddit source via PRAW"""

    def __init__(self):
        self._scraper = RedditScraper()

    @property
    def tier(self) -> SourceTier:
        return SourceTier.DIRECT_API

    @property
    def platforms(self) -> list[Platform]:
        return [Platform.REDDIT]

    def fetch(self, keywords: list[str], days: int = 7) -> list[SourceResult]:
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
                raw_confidence=0.7,
            ))
        return out

    def health_check(self) -> bool:
        try:
            self._scraper.reddit.user.me()
            return True
        except Exception:
            return False
