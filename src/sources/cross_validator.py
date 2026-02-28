"""
CrossValidator — 跨引擎交叉驗證

流程：
1. URL 正規化 + 提取
2. 跨引擎引用計數
3. 實體去重（標題相似度）
4. 信心分數計算
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from ..db.models import Platform, SourceTier
from .base import SourceResult

logger = logging.getLogger(__name__)


@dataclass
class ValidatedResult:
    """交叉驗證後的結果"""
    title: str
    url: str
    platform: Platform
    description: str | None
    author: str
    tags: list[str]
    citation_urls: list[str]
    engines: list[str]  # Which engines found this
    citation_count: int  # Number of engines with this URL
    cross_validated: bool  # citation_count >= 2
    confidence: float  # Final confidence score
    ring: str  # adopt | trial | assess | hold
    source_results: list[SourceResult] = field(default_factory=list)


# Tracking params to strip during URL normalization
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
    "s", "si", "spm", "from", "share_token",
}


def normalize_url(url: str) -> str:
    """
    Normalize URL for comparison:
    - Strip tracking params (utm_*, fbclid, gclid, ref, etc.)
    - Unify www/non-www
    - Unify http/https
    - Sort remaining query params for stable comparison
    - Remove trailing slash
    - Remove fragments
    """
    try:
        parsed = urlparse(url)
        scheme = "https"
        host = parsed.hostname or ""
        if host.startswith("www."):
            host = host[4:]
        # Strip tracking params + sort remaining
        params = parse_qs(parsed.query, keep_blank_values=False)
        clean_params = dict(sorted(
            ((k, sorted(v)) for k, v in params.items() if k not in _TRACKING_PARAMS),
            key=lambda x: x[0],
        ))
        query = urlencode(clean_params, doseq=True) if clean_params else ""
        path = parsed.path.rstrip("/") or "/"
        return urlunparse((scheme, host, path, "", query, ""))
    except Exception:
        return url.strip().rstrip("/")


def title_similarity(a: str, b: str) -> float:
    """Title similarity using SequenceMatcher (Levenshtein-like)"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def cross_validate(
    engine_results: dict[str, list[SourceResult]],
    tier1_results: list[SourceResult] | None = None,
    similarity_threshold: float = 0.8,
) -> list[ValidatedResult]:
    """
    Cross-validate results from multiple engines.

    @param engine_results: dict mapping engine name to its SourceResults
    @param tier1_results: optional Tier 1 results for confidence boost
    @param similarity_threshold: title similarity threshold for dedup (0.0-1.0)
    @returns: validated and deduplicated results sorted by confidence
    """
    total_engines = len([e for e, r in engine_results.items() if r])
    if total_engines == 0:
        return []

    # Step 1: URL extraction & normalization
    url_to_engines: dict[str, set[str]] = {}
    url_to_results: dict[str, list[SourceResult]] = {}

    for engine_name, results in engine_results.items():
        for result in results:
            # Use the main URL
            urls_to_check = [result.url] + result.citation_urls
            for url in urls_to_check:
                if not url:
                    continue
                norm = normalize_url(url)
                url_to_engines.setdefault(norm, set()).add(engine_name)
                url_to_results.setdefault(norm, []).append(result)

    # Step 2: Build initial validated results (per unique result)
    seen_titles: list[ValidatedResult] = []
    processed_ids: set[str] = set()

    for engine_name, results in engine_results.items():
        for result in results:
            if result.external_id in processed_ids:
                continue
            processed_ids.add(result.external_id)

            # Count how many engines found this URL
            norm_url = normalize_url(result.url) if result.url else ""
            engines_with_url = url_to_engines.get(norm_url, {engine_name})

            # Collect all citation URLs from this result
            all_citations = list(set(result.citation_urls + ([result.url] if result.url else [])))

            # Check for title-similar duplicates (require same domain or matching URL)
            result_domain = urlparse(result.url).hostname or "" if result.url else ""
            merged = False
            for existing in seen_titles:
                existing_domain = urlparse(existing.url).hostname or "" if existing.url else ""
                same_domain = result_domain and existing_domain and (
                    result_domain == existing_domain
                    or norm_url == normalize_url(existing.url)
                )
                sim = title_similarity(result.title, existing.title)
                if sim >= similarity_threshold and (same_domain or sim >= 0.95):
                    # Merge into existing
                    existing.engines = list(set(existing.engines + [engine_name]))
                    existing.citation_urls = list(set(existing.citation_urls + all_citations))
                    if result.description and (not existing.description or len(result.description) > len(existing.description)):
                        existing.description = result.description
                    existing.tags = list(set(existing.tags + result.tags))
                    existing.source_results.append(result)
                    merged = True
                    break

            if not merged:
                seen_titles.append(ValidatedResult(
                    title=result.title,
                    url=result.url,
                    platform=result.platform,
                    description=result.description,
                    author=result.author,
                    tags=result.tags,
                    citation_urls=all_citations,
                    engines=[engine_name] + [e for e in engines_with_url if e != engine_name],
                    citation_count=len(engines_with_url),
                    cross_validated=len(engines_with_url) >= 2,
                    confidence=0.0,
                    ring="assess",
                    source_results=[result],
                ))

    # Step 3: Recalculate citation_count after merging
    for vr in seen_titles:
        vr.engines = list(set(vr.engines))
        vr.citation_count = len(vr.engines)
        vr.cross_validated = vr.citation_count >= 2

    # Build Tier 1 URL set for confidence boost
    tier1_urls: set[str] = set()
    if tier1_results:
        for r in tier1_results:
            if r.url:
                tier1_urls.add(normalize_url(r.url))

    # Step 4: Confidence scoring
    for vr in seen_titles:
        base = vr.citation_count / max(total_engines, 1)

        # Boost: has Tier 1 metrics (e.g. GitHub stars, Reddit upvotes)
        has_tier1 = any(normalize_url(url) in tier1_urls for url in vr.citation_urls if url)
        if has_tier1:
            base += 0.10

        # Boost: has citation URL from Perplexica
        has_perplexica_citation = any(
            sr.source_tier == SourceTier.PERPLEXICA and sr.citation_urls
            for sr in vr.source_results
        )
        if has_perplexica_citation:
            base += 0.05

        # Penalty: no valid URL
        if not vr.url or vr.url in ("https://gemini.google.com", "https://x.ai"):
            base -= 0.20

        # Also factor in raw_confidence from source results
        avg_raw = sum(sr.raw_confidence for sr in vr.source_results) / len(vr.source_results) if vr.source_results else 0
        base = base * 0.7 + avg_raw * 0.3  # Blend

        vr.confidence = max(0.0, min(1.0, base))

        # Map to ring
        if vr.confidence > 0.80:
            vr.ring = "adopt"
        elif vr.confidence > 0.60:
            vr.ring = "trial"
        elif vr.confidence > 0.40:
            vr.ring = "assess"
        else:
            vr.ring = "hold"

    # Sort by confidence descending
    seen_titles.sort(key=lambda x: x.confidence, reverse=True)
    logger.info(f"[CrossValidator] {len(seen_titles)} validated results from {total_engines} engines")
    return seen_titles
