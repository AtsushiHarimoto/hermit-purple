"""
Hermit Purple YouTube 爬蟲

用途：使用 yt-dlp 搜尋 YouTube 上的相關影片
依賴：yt-dlp
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

from .base import BaseScraper, ScrapeResult
from ..db.models import Platform
from ..config import get_config
from ..utils import with_retry

logger = logging.getLogger(__name__)


class YouTubeScraper(BaseScraper):
    """
    YouTube 爬蟲
    
    用途：搜索 YouTube 上的 VibeCoding 相關視頻
    依賴：yt-dlp（無需 API key）
    """
    
    def __init__(self):
        self._ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,  # 只提取元數據，不下載
            "skip_download": True,
        }
    
    @property
    def platform(self) -> Platform:
        return Platform.YOUTUBE

    @with_retry(max_retries=3, exceptions=(DownloadError, ConnectionError))
    def _extract_info(self, url, download=False):
        """帶重試的提取調用"""
        with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
            return ydl.extract_info(url, download=download)
    
    def scrape(
        self,
        keywords: list[str],
        days: int = 7,
        max_results: int = 30,
    ) -> list[ScrapeResult]:
        """
        用途：搜索最近 N 天內的相關視頻
        
        @param keywords: 搜索關鍵詞列表
        @param days: 搜索最近 N 天（注意：yt-dlp 無法精確過濾日期）
        @param max_results: 最大結果數量
        @returns: ScrapeResult 列表
        
        失敗：
        - 網絡錯誤: yt_dlp.DownloadError
        """
        config = get_config()
        min_views = config.platforms.youtube.min_views
        
        # yt-dlp 無法直接按日期過濾，需要後處理
        since_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        results: list[ScrapeResult] = []
        seen_videos: set[str] = set()
        
        for keyword in keywords:
            if len(results) >= max_results:
                break
            
            try:
                # 構建搜索 URL (ytsearchN:query)
                # N 是搜索數量，可以設大一點以過濾日期
                search_query = f"ytsearch{max_results * 2}:{keyword}"
                
                info = self._extract_info(search_query, download=False)
                    
                if not info or "entries" not in info:
                    continue
                    
                for entry in info["entries"]:
                    if not entry:
                        continue
                        
                    if len(results) >= max_results:
                        break
                        
                    video_id = entry.get("id")
                    if not video_id or video_id in seen_videos:
                        continue
                    seen_videos.add(video_id)
                    
                    # 獲取詳細信息
                    result = self._parse_entry(entry, keywords, min_views)
                    if result:
                        results.append(result)
                            
            except Exception as e:
                logger.error(f"[YouTube] Error searching '{keyword}': {e}")
                continue
        
        return results
    
    def _parse_entry(
        self,
        entry: dict[str, Any],
        keywords: list[str],
        min_views: int,
    ) -> ScrapeResult | None:
        """
        用途：將 yt-dlp entry 轉換為 ScrapeResult
        
        @param entry: yt-dlp 提取的視頻信息
        @param keywords: 用於標記匹配的關鍵詞
        @param min_views: 最低觀看數閾值
        @returns: ScrapeResult 或 None（如果不符合條件）
        """
        try:
            video_id = entry.get("id")
            title = entry.get("title", "")
            description = entry.get("description", "")
            view_count = entry.get("view_count", 0) or 0
            
            # 過濾低觀看數
            if view_count < min_views:
                return None
            
            # 組合文本用於關鍵詞匹配
            search_text = f"{title} {description}"
            matched_tags = self.match_keywords(search_text, keywords)
            
            # 解析上傳日期
            upload_date_str = entry.get("upload_date")
            created_at = None
            if upload_date_str:
                try:
                    created_at = datetime.strptime(upload_date_str, "%Y%m%d")
                except ValueError:
                    pass
            
            return ScrapeResult(
                platform=Platform.YOUTUBE,
                external_id=video_id,
                title=title,
                description=description[:500] if description else None,
                url=f"https://www.youtube.com/watch?v={video_id}",
                author=entry.get("uploader", entry.get("channel", "unknown")),
                metrics={
                    "views": view_count,
                    "duration": entry.get("duration"),
                    "like_count": entry.get("like_count"),
                    "channel_id": entry.get("channel_id"),
                    "channel_url": entry.get("channel_url"),
                },
                tags=matched_tags,
                created_at=created_at,
            )
        except Exception as e:
            logger.error(f"[YouTube] Error parsing entry: {e}")
            return None
