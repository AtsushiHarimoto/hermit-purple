"""
Hermit Purple Bilibili 爬蟲

用途：使用 yt-dlp 搜尋 Bilibili 上的相關影片，含 3 層降級鏈
依賴：yt-dlp, requests
降級鏈：yt-dlp bilisearch → Bilibili Web API → 空列表（交由 Source 層處理 Tier 2）
"""

import logging
import re
import requests
from datetime import datetime, timedelta, timezone
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

from .base import BaseScraper, ScrapeResult
from ..db.models import Platform
from ..config import get_config
from ..utils import with_retry

logger = logging.getLogger(__name__)

__all__ = ["BilibiliScraper"]

# Bilibili Web API 搜索端點
_BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/all"

# 用於從 URL 或 ID 字段中提取 BV 號的正則
_BV_PATTERN = re.compile(r"(BV[A-Za-z0-9]{10})")


class BilibiliScraper(BaseScraper):
    """
    Bilibili 爬蟲

    用途：搜索 Bilibili 上的相關視頻
    依賴：yt-dlp（主要）、requests（降級）

    降級鏈：
      1. yt-dlp bilisearch — 最可靠，解析完整
      2. Bilibili Web API — cookie/header 偽裝，無 wbi 簽名
      3. 空列表 — 交由 Source adapter 層委派 Tier 2 搜索
    """

    def __init__(self):
        self._ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
        }
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        })

    def close(self) -> None:
        """關閉 HTTP session，釋放連接池"""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def platform(self) -> Platform:
        return Platform.BILIBILI

    def health_check(self) -> bool:
        """快速探測 yt-dlp bilibili 提取器是否可用（10s timeout）"""
        try:
            opts = {**self._ydl_opts, "socket_timeout": 10}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info("bilisearch1:test", download=False)
                return bool(info and "entries" in info)
        except Exception:
            return False

    # ── Primary: yt-dlp ─────────────────────────────────────────────

    @with_retry(max_retries=2, exceptions=(DownloadError, ConnectionError))
    def _extract_info(self, url: str, download: bool = False) -> dict | None:
        """帶重試的 yt-dlp 提取調用"""
        with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
            return ydl.extract_info(url, download=download)

    def _search_ytdlp(
        self, keyword: str, max_results: int,
    ) -> list[dict] | None:
        """
        使用 yt-dlp bilisearch 搜索。
        返回 entry 列表，失敗時返回 None 以觸發降級。
        """
        try:
            info = self._extract_info(
                f"bilisearch{max_results}:{keyword}", download=False,
            )
            entries = list(info.get("entries", [])) if info else []
            return entries or None
        except Exception as e:
            logger.warning("[Bilibili] yt-dlp search failed: %s", e)
            return None

    # ── Fallback A: Bilibili Web API ────────────────────────────────

    def _search_web_api(
        self, keyword: str, max_results: int,
    ) -> list[dict] | None:
        """
        使用 Bilibili Web API 搜索。
        僅做基本 cookie/header 偽裝，不做 wbi 簽名。
        返回轉換後的 entry 列表，失敗時返回 None。
        """
        try:
            params = {
                "keyword": keyword,
                "search_type": "video",
                "page": 1,
                "page_size": min(max_results, 50),
            }
            resp = self._session.get(
                _BILIBILI_SEARCH_API, params=params, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code", -1) != 0:
                logger.warning(
                    "[Bilibili] Web API non-zero return code: %s", data.get("code"),
                )
                return None

            result_data = data.get("data", {}).get("result", {})

            raw_items = self._extract_video_items(result_data)

            video_entries = [
                self._normalize_web_api_entry(item) for item in raw_items
            ]
            return video_entries or None

        except Exception as e:
            logger.warning("[Bilibili] Web API error (%s): %s", type(e).__name__, e)
            return None

    @staticmethod
    def _extract_video_items(result_data) -> list[dict]:
        """從 Web API 回包中提取影片 item 列表（兼容 dict 和 list 兩種結構）"""
        if isinstance(result_data, dict):
            return [i for i in result_data.get("video", []) if isinstance(i, dict)]

        if isinstance(result_data, list):
            items: list[dict] = []
            for group in result_data:
                if not isinstance(group, dict):
                    continue
                if group.get("result_type") == "video":
                    items.extend(group.get("data", []))
                elif "bvid" in group:
                    items.append(group)
            return items

        return []

    @staticmethod
    def _normalize_web_api_entry(item: dict) -> dict:
        """將 Web API 結果轉換為與 yt-dlp entry 相似的格式"""
        return {
            "id": item.get("bvid", ""),
            "title": re.sub(r"<.*?>", "", item.get("title", "")),
            "description": item.get("description", ""),
            "view_count": item.get("play", 0),
            "like_count": item.get("like", 0),
            "comment_count": item.get("review", 0),
            "danmaku_count": item.get("video_review", 0),
            "uploader": item.get("author", "unknown"),
            "timestamp": item.get("pubdate"),  # keep as int for _parse_upload_date
            "upload_date": "",  # reserve for yt-dlp YYYYMMDD format
            "duration": item.get("duration", ""),
        }

    # ── 主入口 ──────────────────────────────────────────────────────

    def scrape(
        self,
        keywords: list[str],
        days: int = 7,
        max_results: int = 50,
    ) -> list[ScrapeResult]:
        """
        用途：搜索最近 N 天內的 Bilibili 相關視頻

        @param keywords: 搜索關鍵詞列表
        @param days: 搜索最近 N 天
        @param max_results: 最大結果數量
        @returns: ScrapeResult 列表

        降級鏈：yt-dlp → Web API → 空列表
        """
        config = get_config()
        bilibili_cfg = getattr(config.platforms, "bilibili", None)
        min_views = bilibili_cfg.min_views if bilibili_cfg else 5000
        max_results_cfg = bilibili_cfg.max_results if bilibili_cfg else 20

        # 以 config 值與參數取較小值
        effective_max = min(max_results, max_results_cfg)
        since_date = datetime.now(timezone.utc) - timedelta(days=days)

        results: list[ScrapeResult] = []
        seen_videos: set[str] = set()

        for keyword in keywords:
            if len(results) >= effective_max:
                break

            # Layer 1: yt-dlp
            entries = self._search_ytdlp(keyword, effective_max)

            # Layer 2: Bilibili Web API
            if entries is None:
                logger.info("[Bilibili] Falling back to Web API: keyword='%s'", keyword)
                entries = self._search_web_api(keyword, effective_max)

            # Layer 3: 空列表（交由 Source 層處理 Tier 2）
            if entries is None:
                logger.info("[Bilibili] Web API also failed, skipping keyword='%s'", keyword)
                continue

            for entry in entries:
                if not entry:
                    continue
                if len(results) >= effective_max:
                    break

                bv_id = self._extract_bv_id(entry)
                if not bv_id or bv_id in seen_videos:
                    continue
                seen_videos.add(bv_id)

                result = self._parse_entry(
                    entry, bv_id, keywords, min_views, since_date,
                )
                if result:
                    results.append(result)

        return results

    # ── 解析 ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_bv_id(entry: dict) -> str | None:
        """從 entry 中提取 BV 號"""
        raw_id = entry.get("id", "")
        if _BV_PATTERN.fullmatch(raw_id):
            return raw_id
        # 嘗試從 url / webpage_url 提取
        for key in ("url", "webpage_url"):
            url = entry.get(key, "")
            match = _BV_PATTERN.search(url)
            if match:
                return match.group(1)
        # 非 BV 格式（如純數字 av 號）無法組成有效 URL，放棄
        return None

    def _parse_entry(
        self,
        entry: dict[str, Any],
        bv_id: str,
        keywords: list[str],
        min_views: int,
        since_date: datetime,
    ) -> ScrapeResult | None:
        """
        用途：將 entry 轉換為 ScrapeResult

        @param entry: yt-dlp 或 Web API 標準化後的視頻信息
        @param bv_id: BV 號
        @param keywords: 用於標記匹配的關鍵詞
        @param min_views: 最低觀看數閾值
        @param since_date: 最早日期（用於過濾）
        @returns: ScrapeResult 或 None
        """
        try:
            title = entry.get("title", "")
            description = entry.get("description", "")
            view_count = int(entry.get("view_count") or 0)

            # 過濾低觀看數
            if view_count < min_views:
                return None

            # 解析上傳日期
            created_at = self._parse_upload_date(entry)
            if created_at and created_at < since_date:
                return None

            # 關鍵詞匹配
            search_text = f"{title} {description}"
            matched_tags = self.match_keywords(search_text, keywords)

            return ScrapeResult(
                platform=Platform.BILIBILI,
                external_id=bv_id,
                title=title,
                description=description[:500] if description else None,
                url=f"https://www.bilibili.com/video/{bv_id}",
                author=entry.get("uploader", entry.get("channel", "unknown")),
                metrics={
                    "view_count": view_count,
                    "like_count": int(entry.get("like_count") or 0),
                    "comment_count": int(entry.get("comment_count") or 0),
                    "danmaku_count": int(entry.get("danmaku_count") or 0),
                },
                tags=matched_tags,
                created_at=created_at,
            )
        except Exception as e:
            logger.error("[Bilibili] Entry parse error: %s", e)
            return None

    @staticmethod
    def _parse_upload_date(entry: dict) -> datetime | None:
        """嘗試多種格式解析上傳日期（YYYYMMDD / Unix timestamp）"""
        raw = str(entry.get("upload_date", ""))

        # yt-dlp 格式: "20240101"
        if re.fullmatch(r"\d{8}", raw):
            try:
                return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Unix 時間戳：upload_date（Web API pubdate）或 timestamp 字段
        for value in (raw, entry.get("timestamp")):
            try:
                ts = int(value)
                if ts > 1_000_000_000:
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
            except (ValueError, TypeError, OSError):
                continue

        return None
