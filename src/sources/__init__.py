"""
Hermit Purple 數據源模組

三層架構：
- Tier 1: Direct API (GitHub, Reddit, YouTube)
- Tier 2: AI Search (Perplexica, Gemini Grounding, Grok Search)
- Tier 3: Web Crawler (Crawl4AI)
"""

from .base import DataSource, SourceResult  # noqa: F401
