"""
YouTube DataSource — Tier 1 (Direct API) adapter wrapping YouTubeScraper
"""

from __future__ import annotations

import logging

from ..db.models import Platform, SourceTier
from ..scrapers.youtube_scraper import YouTubeScraper
from .base import DataSource, SourceResult

logger = logging.getLogger(__name__)


class YouTubeSource(DataSource):
    """Tier 1 YouTube source via yt-dlp"""

    def __init__(self):
        self._scraper = YouTubeScraper()

    @property
    def tier(self) -> SourceTier:
        return SourceTier.DIRECT_API

    @property
    def platforms(self) -> list[Platform]:
        return [Platform.YOUTUBE]

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
                raw_confidence=0.6,
            ))
        return out

    def health_check(self) -> bool:
        """Probe yt-dlp YouTube search availability (10s timeout)"""
        try:
            import yt_dlp
            opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "socket_timeout": 10,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info("ytsearch1:test", download=False)
                return bool(info and "entries" in info)
        except Exception:
            return False
