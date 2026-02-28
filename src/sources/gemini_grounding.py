"""
Gemini Grounding DataSource — Tier 2 (AI Search)

Gateway-first fallback to official Gemini API with Search Grounding.
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
針對以下關鍵詞搜尋最近 {days} 天的最新趨勢、工具和重大突破：
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


class GeminiGroundingSource(DataSource):
    """
    Tier 2: Gemini with Google Search Grounding

    Fallback chain: Gateway → Official Gemini API
    """

    def __init__(self):
        env = get_env()
        self._gateway_url = env.ai_base_url
        self._gateway_key = env.ai_api_key or FALLBACK_API_KEY
        self._gateway_model = env.ai_model
        self._official_key = env.gemini_api_key
        self._official_url = env.gemini_official_base_url
        self._official_model = env.gemini_official_model

    @property
    def tier(self) -> SourceTier:
        return SourceTier.GEMINI_GROUND

    @property
    def platforms(self) -> list[Platform]:
        return list(Platform)

    def _call_gateway(self, prompt: str) -> str:
        """Quick probe via Web2API gateway (30s timeout, no retries)"""
        client = OpenAI(
            base_url=self._gateway_url,
            api_key=self._gateway_key,
            timeout=30.0,
            max_retries=0,
        )
        resp = client.chat.completions.create(
            model=self._gateway_model,
            messages=build_messages("You are an AI trend analyst. Output ONLY valid JSON array. No markdown.", prompt),
            temperature=0.4,
        )
        return resp.choices[0].message.content or ""

    def _call_official(self, prompt: str) -> str:
        """Fallback to official Gemini API with resilient retries"""
        if not self._official_key:
            raise RuntimeError("No Gemini API key configured")
        client = OpenAI(
            base_url=self._official_url,
            api_key=self._official_key,
            timeout=180.0,
        )
        resp = resilient_ai_call(
            client=client,
            model=self._official_model,
            messages=build_messages("You are an AI trend analyst. Output ONLY valid JSON array. No markdown.", prompt),
            temperature=0.4,
        )
        return resp.choices[0].message.content or ""

    def fetch(self, keywords: list[str], days: int = 7) -> list[SourceResult]:
        prompt = _SEARCH_PROMPT.format(
            days=days,
            keywords=", ".join(keywords),
        )

        raw = ""
        # 1. Gateway probe
        try:
            raw = self._call_gateway(prompt)
            logger.info("[GeminiGrounding] Gateway call succeeded")
        except Exception as e:
            if not _is_gateway_error(e):
                logger.error(f"[GeminiGrounding] Non-gateway error: {e}")
                return []
            logger.warning(f"[GeminiGrounding] Gateway error, falling back to official API: {e}")
            # 2. Official API fallback
            try:
                raw = self._call_official(prompt)
                logger.info("[GeminiGrounding] Official API fallback succeeded")
            except Exception as e2:
                logger.error(f"[GeminiGrounding] Official API also failed: {e2}")
                return []

        return self._parse_response(raw, keywords)

    def _parse_response(self, raw: str, keywords: list[str]) -> list[SourceResult]:
        """Parse AI JSON response + extract URLs"""
        # Extract URLs from raw text as citation_urls
        all_urls = _extract_urls(raw)

        # Try JSON parse
        data = safe_parse_json(raw)
        if not data or not isinstance(data, list):
            # Fallback: create single result from raw text
            if all_urls:
                return [SourceResult(
                    platform=detect_platform_from_url(all_urls[0]),
                    source_tier=self.tier,
                    external_id=f"gemini-{hashlib.sha256(raw[:100].encode()).hexdigest()[:16]}",
                    title="Gemini Search Results",
                    url=all_urls[0],
                    author="Gemini",
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
            ext_id = f"gemini-{hashlib.sha256(title.encode()).hexdigest()[:16]}"
            try:
                confidence = float(item.get("confidence", item.get("metrics", {}).get("confidence", 0.5)))
            except (TypeError, ValueError):
                confidence = 0.5

            results.append(SourceResult(
                platform=platform,
                source_tier=self.tier,
                external_id=ext_id,
                title=title,
                url=url or "https://gemini.google.com",
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
