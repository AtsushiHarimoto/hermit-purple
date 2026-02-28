"""
Grok Search DataSource — Tier 2 (AI Search)

Gateway-first fallback to official Grok API.
Grok's web search is auto-enabled (disableSearch: False),
including X/Twitter live search — primary source for social signals.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from openai import OpenAI

from ..config import FALLBACK_API_KEY, get_env
from ..db.models import Platform, SourceTier
from ..utils import build_messages, resilient_ai_call, safe_parse_json
from .base import DataSource, SourceResult, _extract_urls, _is_gateway_error, detect_platform_from_url

logger = logging.getLogger(__name__)


_SEARCH_PROMPT = """\
<task>
使用 web search 搜尋以下關鍵詞最近 {days} 天的最新趨勢，特別關注 X/Twitter 上的討論：
{keywords}
</task>

<output>
直接輸出 JSON 陣列：
[
  {{
    "title": "名稱",
    "url": "URL",
    "description": "一句話繁體中文摘要",
    "author": "創建者/組織",
    "tags": ["標籤"],
    "confidence": 0.8
  }}
]
</output>
"""


class GrokSearchSource(DataSource):
    """
    Tier 2: Grok with web search (X/Twitter live search)

    Fallback chain: Gateway → Official Grok API
    """

    def __init__(self):
        env = get_env()
        self._gateway_url = env.ai_base_url
        self._gateway_key = env.ai_api_key or FALLBACK_API_KEY
        self._gateway_model = "grok-3-fast"
        self._official_key = env.grok_official_api_key
        self._official_url = env.grok_official_base_url
        self._official_model = env.grok_official_model

    @property
    def tier(self) -> SourceTier:
        return SourceTier.GROK_SEARCH

    @property
    def platforms(self) -> list[Platform]:
        return list(Platform)

    def _call_gateway(self, prompt: str) -> str:
        client = OpenAI(
            base_url=self._gateway_url,
            api_key=self._gateway_key,
            timeout=30.0,
            max_retries=0,
        )
        resp = client.chat.completions.create(
            model=self._gateway_model,
            messages=build_messages("You are an AI trend analyst with web search. Output ONLY valid JSON array. No markdown.", prompt),
            temperature=0.4,
        )
        return resp.choices[0].message.content or ""

    def _call_official(self, prompt: str) -> str:
        if not self._official_key:
            raise RuntimeError("No Grok API key configured")
        client = OpenAI(
            base_url=self._official_url,
            api_key=self._official_key,
            timeout=180.0,
        )
        resp = resilient_ai_call(
            client=client,
            model=self._official_model,
            messages=build_messages("You are an AI trend analyst with web search. Output ONLY valid JSON array. No markdown.", prompt),
            temperature=0.4,
        )
        return resp.choices[0].message.content or ""

    def fetch(self, keywords: list[str], days: int = 7) -> list[SourceResult]:
        prompt = _SEARCH_PROMPT.format(
            days=days,
            keywords=", ".join(keywords),
        )

        raw = ""
        try:
            raw = self._call_gateway(prompt)
            logger.info("[GrokSearch] Gateway call succeeded")
        except Exception as e:
            if not _is_gateway_error(e):
                logger.error(f"[GrokSearch] Non-gateway error: {e}")
                return []
            logger.warning(f"[GrokSearch] Gateway error, falling back: {e}")
            try:
                raw = self._call_official(prompt)
                logger.info("[GrokSearch] Official API fallback succeeded")
            except Exception as e2:
                logger.error(f"[GrokSearch] Official API also failed: {e2}")
                return []

        return self._parse_response(raw, keywords)

    def _parse_response(self, raw: str, keywords: list[str]) -> list[SourceResult]:
        all_urls = _extract_urls(raw)
        data = safe_parse_json(raw)
        if not data or not isinstance(data, list):
            if all_urls:
                return [SourceResult(
                    platform=detect_platform_from_url(all_urls[0]),
                    source_tier=self.tier,
                    external_id=f"grok-{hashlib.sha256(raw[:100].encode()).hexdigest()[:16]}",
                    title="Grok Search Results",
                    url=all_urls[0],
                    author="Grok",
                    description=raw[:500],
                    citation_urls=all_urls,
                    raw_confidence=0.4,
                )]
            return []

        results: list[SourceResult] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            url = item.get("url", "")
            if not title:
                continue

            platform = detect_platform_from_url(url) if url else Platform.WEB_OTHER
            ext_id = f"grok-{hashlib.sha256(title.encode()).hexdigest()[:16]}"
            try:
                confidence = float(item.get("confidence", item.get("metrics", {}).get("confidence", 0.5)))
            except (TypeError, ValueError):
                confidence = 0.5

            results.append(SourceResult(
                platform=platform,
                source_tier=self.tier,
                external_id=ext_id,
                title=title,
                url=url or "https://x.ai",
                author=item.get("author", ""),
                description=item.get("description"),
                tags=item.get("tags", []),
                citation_urls=[url] if url else [],
                raw_confidence=confidence,
            ))
        return results

    def health_check(self) -> bool:
        try:
            import requests
            resp = requests.get(
                self._gateway_url.replace("/v1", "/health"),
                timeout=5,
            )
            return resp.status_code < 500
        except Exception:
            return bool(self._official_key)
