"""
Hermit Purple 爬蟲模組
"""

from .base import BaseScraper, ScrapeResult
from .github_scraper import GitHubScraper
from .reddit_scraper import RedditScraper
from .youtube_scraper import YouTubeScraper
from .ai_scraper import AIScraper

__all__ = [
    "BaseScraper",
    "ScrapeResult",
    "GitHubScraper",
    "RedditScraper",
    "YouTubeScraper",
    "AIScraper",
]
