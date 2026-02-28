"""
DataSource 基類與 SourceResult 數據結構

用途：定義統一的數據源接口，取代舊版 BaseScraper 用於三引擎架構；
      同時提供共享工具函數（網關錯誤偵測、URL 提取、平台識別）
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from ..db.models import Platform, SourceTier

# ── 共享常數與工具函數 ────────────────────────────────────────────────────────

# Web2API 網關不可用時的錯誤關鍵字
_GATEWAY_FAIL_KEYWORDS = (
    "403", "IP被拦截", "IP被攔截",
    "internal_error", "internal_server_error",
    "timed out", "Request timed out", "ReadTimeout",
    "Connection error", "Connection refused", "ConnectionError",
    "ConnectError", "ConnectionRefusedError",
)


def _is_gateway_error(error: Exception) -> bool:
    """判斷是否為 Web2API 網關層面的錯誤（403/500/timeout），值得 fallback 到官方 API"""
    err_str = str(error)
    return any(kw in err_str for kw in _GATEWAY_FAIL_KEYWORDS)


def _extract_urls(text: str) -> list[str]:
    """從原始文字中提取所有 HTTP/HTTPS URL"""
    return re.findall(r"https?://[^\s\"'<>\]\)]+", text or "")


# URL domain → Platform 映射表
_DOMAIN_PLATFORM_MAP: dict[str, Platform] = {
    "github.com": Platform.GITHUB,
    "reddit.com": Platform.REDDIT,
    "old.reddit.com": Platform.REDDIT,
    "youtube.com": Platform.YOUTUBE,
    "www.youtube.com": Platform.YOUTUBE,
    "news.ycombinator.com": Platform.HACKERNEWS,
    "producthunt.com": Platform.PRODUCTHUNT,
    "www.producthunt.com": Platform.PRODUCTHUNT,
    "bilibili.com": Platform.BILIBILI,
    "www.bilibili.com": Platform.BILIBILI,
    "twitter.com": Platform.X_TWITTER,
    "x.com": Platform.X_TWITTER,
    "threads.net": Platform.THREADS,
    "www.threads.net": Platform.THREADS,
    "instagram.com": Platform.INSTAGRAM,
    "www.instagram.com": Platform.INSTAGRAM,
    "xiaohongshu.com": Platform.XIAOHONGSHU,
    "www.xiaohongshu.com": Platform.XIAOHONGSHU,
    "douyin.com": Platform.DOUYIN,
    "www.douyin.com": Platform.DOUYIN,
    "weibo.com": Platform.WEIBO,
    "www.weibo.com": Platform.WEIBO,
    "substack.com": Platform.SUBSTACK,
    "medium.com": Platform.MEDIUM,
    "arxiv.org": Platform.ARXIV,
}


def detect_platform_from_url(url: str) -> Platform:
    """從 URL 推斷原始平台"""
    try:
        domain = urlparse(url).hostname or ""
        # Check exact match first
        if domain in _DOMAIN_PLATFORM_MAP:
            return _DOMAIN_PLATFORM_MAP[domain]
        # Check if domain ends with known pattern (e.g. *.substack.com)
        for key, platform in _DOMAIN_PLATFORM_MAP.items():
            if domain.endswith(f".{key}"):
                return platform
    except Exception:
        pass
    return Platform.WEB_OTHER


@dataclass
class SourceResult:
    """
    統一的數據源結果

    擴展自 ScrapeResult，增加 source_tier、citation_urls、raw_confidence
    """
    platform: Platform
    source_tier: SourceTier
    external_id: str
    title: str
    url: str
    author: str
    description: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    citation_urls: list[str] = field(default_factory=list)
    raw_confidence: float = 0.0

    def __post_init__(self):
        if not self.external_id:
            raise ValueError("external_id is required")
        if not self.title:
            raise ValueError("title is required")


class DataSource(ABC):
    """
    數據源基類

    所有 Tier 1/2/3 數據源必須繼承此類。
    """

    @property
    @abstractmethod
    def tier(self) -> SourceTier:
        """返回此數據源的層級"""
        ...

    @property
    @abstractmethod
    def platforms(self) -> list[Platform]:
        """返回此數據源可覆蓋的平台列表"""
        ...

    @abstractmethod
    def fetch(self, keywords: list[str], days: int = 7) -> list[SourceResult]:
        """
        執行數據抓取

        @param keywords: 搜索關鍵詞列表
        @param days: 搜索最近 N 天的內容
        @returns: 結果列表
        """
        ...

    def health_check(self) -> bool:
        """
        健康檢查（默認返回 True）

        子類可覆寫以檢查 API 可用性
        """
        return True
