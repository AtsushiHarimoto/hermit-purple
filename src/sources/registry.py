"""
DataSource Registry — 管理和發現所有數據源

用途：集中管理 Tier 1/2/3 數據源的註冊與查詢
"""

from __future__ import annotations

import logging
from typing import Optional

from ..db.models import Platform, SourceTier
from .base import DataSource

logger = logging.getLogger(__name__)


class DataSourceRegistry:
    """數據源註冊表"""

    def __init__(self):
        self._sources: list[DataSource] = []

    def register(self, source: DataSource) -> None:
        self._sources.append(source)
        logger.debug(f"[Registry] Registered {source.__class__.__name__} (tier={source.tier.value})")

    def get_all(self) -> list[DataSource]:
        return list(self._sources)

    def get_by_tier(self, tier: SourceTier) -> list[DataSource]:
        return [s for s in self._sources if s.tier == tier]

    def get_by_platform(self, platform: Platform) -> list[DataSource]:
        return [s for s in self._sources if platform in s.platforms]

    def get_tier2_engines(self) -> list[DataSource]:
        """返回所有 Tier 2 AI 搜尋引擎（用於三引擎並行）"""
        tier2_tiers = {SourceTier.PERPLEXICA, SourceTier.GEMINI_GROUND, SourceTier.GROK_SEARCH}
        return [s for s in self._sources if s.tier in tier2_tiers]

    def list_names(self) -> list[str]:
        return [s.__class__.__name__ for s in self._sources]

    def health_check_all(self) -> dict[str, bool]:
        results = {}
        for source in self._sources:
            name = source.__class__.__name__
            try:
                results[name] = source.health_check()
            except Exception:
                results[name] = False
        return results


def build_default_registry() -> DataSourceRegistry:
    """建立包含所有可用數據源的預設 registry"""
    registry = DataSourceRegistry()

    # Tier 1: Direct API sources (always register, health_check determines availability)
    try:
        from .github import GitHubSource
        registry.register(GitHubSource())
    except Exception as e:
        logger.warning(f"[Registry] GitHubSource unavailable: {e}")

    try:
        from .reddit import RedditSource
        registry.register(RedditSource())
    except Exception as e:
        logger.warning(f"[Registry] RedditSource unavailable: {e}")

    try:
        from .youtube import YouTubeSource
        registry.register(YouTubeSource())
    except Exception as e:
        logger.warning(f"[Registry] YouTubeSource unavailable: {e}")

    try:
        from .bilibili import BilibiliSource
        bilibili_src = BilibiliSource()
        # NOTE: Tier 2 engines registered below; BilibiliSource queries
        # the registry lazily at fetch() time, not at construction time.
        bilibili_src.set_registry(registry)
        registry.register(bilibili_src)
    except Exception as e:
        logger.warning(f"[Registry] BilibiliSource unavailable: {e}")

    # Tier 2: AI Search engines (registered if configured)
    try:
        from .perplexica import PerplexicaSource
        registry.register(PerplexicaSource())
    except Exception as e:
        logger.debug(f"[Registry] PerplexicaSource unavailable: {e}")

    try:
        from .gemini_grounding import GeminiGroundingSource
        registry.register(GeminiGroundingSource())
    except Exception as e:
        logger.debug(f"[Registry] GeminiGroundingSource unavailable: {e}")

    try:
        from .grok_search import GrokSearchSource
        registry.register(GrokSearchSource())
    except Exception as e:
        logger.debug(f"[Registry] GrokSearchSource unavailable: {e}")

    logger.info(f"[Registry] Initialized with {len(registry.get_all())} sources: {registry.list_names()}")
    return registry
