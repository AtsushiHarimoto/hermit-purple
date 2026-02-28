"""
Perplexica DataSource — Tier 2 (AI Search)

自部署 Perplexica + SearXNG + Ollama，零成本 AI 搜尋引擎。
支持 site: 運算子覆蓋中文平台（小紅書、抖音、微博等）。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

import requests

from ..config import get_env
from ..db.models import Platform, SourceTier
from .base import DataSource, SourceResult, detect_platform_from_url  # noqa: F401 – re-export

logger = logging.getLogger(__name__)


class PerplexicaSource(DataSource):
    """
    Tier 2: Perplexica 自部署 AI 搜尋引擎

    API: POST /api/search
    模型: Ollama → qwen3:14b (chat) + nomic-embed-text (embedding)
    """

    def __init__(self):
        env = get_env()
        self._base_url = env.perplexica_api_url.rstrip("/")
        self._chat_model_provider: str | None = None
        self._chat_model_key: str = "qwen3:14b"
        self._embedding_provider: str | None = None
        self._embedding_key: str = "nomic-embed-text"

    @property
    def tier(self) -> SourceTier:
        return SourceTier.PERPLEXICA

    @property
    def platforms(self) -> list[Platform]:
        return list(Platform)  # Can discover any platform via web search

    def _discover_providers(self) -> None:
        """Auto-discover Ollama provider IDs from Perplexica API (new /api/providers format)"""
        if self._chat_model_provider:
            return
        try:
            resp = requests.get(f"{self._base_url}/api/providers", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            # New format: {"providers": [{"id": "...", "name": "Ollama", "chatModels": [...], "embeddingModels": [...]}]}
            providers = data.get("providers", [])
            for p in providers:
                p_name = (p.get("name") or "").lower()
                p_id = p.get("id", "")
                if "ollama" in p_name:
                    self._chat_model_provider = p_id
                    self._embedding_provider = p_id
                    break
            if not self._chat_model_provider and providers:
                # Fallback: use first provider with chatModels
                for p in providers:
                    if p.get("chatModels"):
                        self._chat_model_provider = p["id"]
                        self._embedding_provider = p["id"]
                        break
            logger.info(f"[Perplexica] Discovered provider: {self._chat_model_provider}")
        except Exception as e:
            logger.warning(f"[Perplexica] Provider discovery failed: {e}")
            self._chat_model_provider = "ollama"
            self._embedding_provider = "ollama"

    def _search(self, query: str) -> dict[str, Any]:
        """Execute a single Perplexica search"""
        self._discover_providers()
        payload = {
            "chatModel": {
                "provider": self._chat_model_provider or "ollama",
                "model": self._chat_model_key,
            },
            "embeddingModel": {
                "provider": self._embedding_provider or "ollama",
                "model": self._embedding_key,
            },
            "query": query,
            "focusMode": "webSearch",
            "optimizationMode": "balanced",
        }
        resp = requests.post(
            f"{self._base_url}/api/search",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch(self, keywords: list[str], days: int = 7) -> list[SourceResult]:
        results: list[SourceResult] = []

        # Main trend query
        query = f"最近 {days} 天 {' '.join(keywords)} 最新趨勢、工具和突破"
        results.extend(self._do_search(query, keywords))

        # Platform-specific queries (Chinese platforms via site: operator)
        platform_queries = self._build_platform_queries(keywords, days)
        for pq in platform_queries:
            results.extend(self._do_search(pq, keywords))

        return results

    def _build_platform_queries(self, keywords: list[str], days: int) -> list[str]:
        """Build site:-scoped queries for Chinese platforms"""
        kw_str = " ".join(keywords)
        queries = []
        # Only do platform queries if keywords look relevant
        if any("\u4e00" <= c <= "\u9fff" for c in kw_str) or len(keywords) <= 3:
            queries.append(f"{kw_str} site:xiaohongshu.com")
            queries.append(f"{kw_str} site:weibo.com")
        return queries

    def _do_search(self, query: str, keywords: list[str]) -> list[SourceResult]:
        """Execute a single search and parse results"""
        try:
            data = self._search(query)
        except Exception as e:
            logger.error(f"[Perplexica] Search failed for '{query[:50]}': {e}")
            return []

        message = data.get("message", "")
        sources = data.get("sources", [])
        return self._parse_response(message, sources, [], keywords)

    def fetch_reviews(self, topic: str, max_results: int = 20) -> list[dict[str, Any]]:
        """
        Fetch real user reviews/comments about a topic (for SentimentEngine).

        Returns list of dicts: [{"author": ..., "content": ..., "likes": 0}]
        """
        query = f'"{topic}" 用戶評價 心得 review'
        try:
            data = self._search(query)
        except Exception:
            return []

        comments: list[dict[str, Any]] = []
        for src in data.get("sources", []):
            content = src.get("pageContent", src.get("content", ""))
            meta = src.get("metadata", {})
            if content:
                comments.append({
                    "author": meta.get("title", "User"),
                    "content": content[:500],
                    "likes": 0,
                    "url": meta.get("url", ""),
                })
            if len(comments) >= max_results:
                break
        return comments

    def _parse_response(
        self,
        message: str,
        sources: list[dict],
        all_citation_urls: list[str],
        keywords: list[str],
    ) -> list[SourceResult]:
        """Parse Perplexica response into SourceResults"""
        results: list[SourceResult] = []

        # Each source becomes a result
        for src in sources:
            meta = src.get("metadata", {})
            url = meta.get("url", "")
            title = meta.get("title", "")
            content = src.get("pageContent", src.get("content", ""))

            if not url or not title:
                continue

            platform = detect_platform_from_url(url)
            ext_id = f"perplexica-{hashlib.md5(url.encode()).hexdigest()[:12]}"

            results.append(SourceResult(
                platform=platform,
                source_tier=self.tier,
                external_id=ext_id,
                title=title,
                url=url,
                author="",
                description=content[:500] if content else None,
                tags=[kw for kw in keywords if kw.lower() in (title + " " + content).lower()],
                citation_urls=[url],
                raw_confidence=0.5,
            ))

        return results

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self._base_url}/api/providers", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
