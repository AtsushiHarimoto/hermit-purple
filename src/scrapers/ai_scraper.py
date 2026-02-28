"""
AI 智能爬蟲

用途：透過 AI API (Gemini/Grok) 進行熱點搜索和趨勢發現
"""

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from openai import OpenAI

from ..config import FALLBACK_API_KEY, get_env
from ..db.models import Platform
from ..utils import build_messages, resilient_ai_call, safe_parse_json
from ..sources.base import _is_gateway_error
from .base import BaseScraper, ScrapeResult

logger = logging.getLogger(__name__)

# 提示詞模板目錄（相對於 hermit-purple 根目錄）
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "ai_trends"


class AIScraper(BaseScraper):
    """
    AI 智能爬蟲

    使用 OpenAI 兼容接口 (如 Grok/Gemini via web2api) 搜索最新熱點。
    Web2API 網關 403/500 時自動 fallback 到官方 API。
    """

    @property
    def platform(self) -> Platform:
        return Platform.AI_SEARCH

    @staticmethod
    def _load_prompt(filename: str, keywords: List[str], days: int, category: str = "") -> Optional[str]:
        """從 prompts/ai_trends/ 載入提示詞模板，替換佔位符"""
        prompt_path = _PROMPTS_DIR / filename
        if not prompt_path.exists():
            logger.warning(f"[AI Scraper] Prompt template not found: {prompt_path}")
            return None
        content = prompt_path.read_text(encoding="utf-8")
        keywords_str = ", ".join(keywords)
        content = content.replace("{{keywords}}", keywords_str)
        content = content.replace("{{days}}", str(days))
        content = content.replace("{{category}}", category or keywords_str)
        return content

    def _quick_call(self, client: OpenAI, model: str, messages: list, temperature: float) -> str:
        """單次呼叫（不走 tenacity 重試），用於 Web2API 快速探測"""
        logger.info(f"[AI] Quick call {model}...")
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature,
        )
        return resp.choices[0].message.content

    def _call_with_fallback(
        self, env, model: str, messages: list, temperature: float, source: str
    ) -> str:
        """先嘗試 Web2API（單次快速探測），失敗時 fallback 到官方 API（帶重試）"""
        # 探測用 client：max_retries=0 禁掉 SDK 內建重試，30s timeout 快速失敗
        probe_client = OpenAI(
            base_url=env.ai_base_url,
            api_key=env.ai_api_key or FALLBACK_API_KEY,
            timeout=30.0,
            max_retries=0,
        )

        # 1. Web2API 單次快速探測（無重試，快速失敗以便 fallback）
        try:
            logger.info(f"[AI Scraper] Starting {source} researcher via Web2API (model: {model})...")
            return self._quick_call(probe_client, model, messages, temperature)
        except Exception as e:
            if not _is_gateway_error(e):
                raise  # 非網關錯誤，直接拋出
            logger.warning(f"[AI Scraper] Web2API gateway error ({source}): {e}")

        # 2. Fallback 到官方 API（帶 resilient_ai_call 重試）
        if "grok" in model.lower():
            api_key = env.grok_official_api_key
            base_url = env.grok_official_base_url
            fallback_model = env.grok_official_model
        else:
            api_key = env.gemini_api_key
            base_url = env.gemini_official_base_url
            fallback_model = env.gemini_official_model

        if not api_key:
            logger.error(f"[AI Scraper] No official API key available, {source} fallback failed")
            return ""

        logger.info(f"[AI Scraper] Fallback -> Official API ({fallback_model} @ {base_url})")
        fallback_client = OpenAI(base_url=base_url, api_key=api_key, timeout=180.0)
        resp = resilient_ai_call(
            client=fallback_client, model=fallback_model,
            messages=messages, temperature=temperature,
        )
        return resp.choices[0].message.content

    def scrape(
        self,
        keywords: List[str],
        days: int = 7,
        max_results: int = 50,
        category: str = "",
    ) -> List[ScrapeResult]:
        """
        執行多模型 AI 熱點抓取 (Grok + Gemini 容錯 & 漏斗匯總)
        Web2API 403/500 時自動 fallback 到官方 API。
        """
        env = get_env()
        if not env.ai_base_url:
            logger.warning("[AI Scraper] AI_BASE_URL not configured, skipping")
            return []

        keywords_str = ", ".join(keywords)

        # 優先從模板文件載入，fallback 到內建提示詞
        research_prompt = self._load_prompt("research.md", keywords, days, category)
        if not research_prompt:
            domain_label = category or keywords_str
            research_prompt = f"""You are a Senior AI Strategy Analyst & Tech Scout.

Conduct a deep-dive intelligence scan for the latest "AI Breakthroughs" and "Commercial AI Applications" related to: {keywords_str}.

Domain Scope: {domain_label}
CRITICAL: Every returned item MUST be directly relevant to the "{domain_label}" domain. Do NOT return items from other domains even if they mention a keyword in passing. If fewer than 8 relevant items exist, return fewer — quality over quantity.

CRITICAL: Only report information from the last {days} days. Prioritize official announcements, GitHub releases, and verified tech journalism.

Find 8-15 specific, high-impact items.
Each item's tags MUST include at least 1 Domain Tag (VibeCoding/AI-Quant/AI-Video/LLM-OpenSource/AI-Voice/AI-SoftwareEng) and at least 1 Sub-domain Tag (Agent/MCP/Protocol/Infrastructure/Framework/Model/Benchmark/Open-Source/Commercial/Chinese-Ecosystem).

For each item provide:
- ring: adopt/trial/assess/hold
- quadrant: techniques/tools/platforms/languages
- confidence: 0.0-1.0 based on source quality
- trend_direction: rising/stable/declining
- published_at: ISO date string (or "" if unknown)
- matched_keywords: which search keywords this item matched

Each item must include freshness: fresh/recent/stale/undated.
Output STRICT JSON array only. No markdown, no explanation, no code fences.
[
  {{
    "title": "Tool/Trend Name",
    "url": "https://github.com/example/project",
    "description": "一句繁體中文摘要",
    "author": "Creator/Org",
    "published_at": "2026-02-20",
    "tags": ["VibeCoding", "Agent", "Open-Source"],
    "matched_keywords": ["keyword1"],
    "ring": "trial",
    "quadrant": "tools",
    "confidence": 0.85,
    "trend_direction": "rising",
    "freshness": "fresh",
    "evidence": "官方發布公告；社群討論熱度高",
    "metrics": {{
      "github_stars": 15000,
      "hotness": "高",
      "sources": ["source1.com"],
      "citation_urls": ["https://source1.com/article"],
      "value_prop": "顯著提升開發效率"
    }}
  }}
]"""
        
        messages = build_messages(
            "You are a Senior AI Strategy Analyst & Tech Scout. You MUST output ONLY a valid JSON array. No markdown code fences, no explanation, no commentary — just a raw JSON array. Prioritize FRESH information from the last few days. Use Traditional Chinese (繁體中文) for descriptions, evidence, and metrics.value_prop fields.",
            research_prompt,
        )

        try:
            # 1. Grok 研究員（Web2API → 官方 API fallback）
            grok_report = ""
            grok_model = "grok-3-fast"
            try:
                grok_report = self._call_with_fallback(
                    env, grok_model, messages, 0.4, "Grok"
                )
            except Exception as e:
                logger.warning(f"[AI Scraper] Grok scrape failed: {e}")

            # 2. Gemini 研究員（Web2API → 官方 API fallback）
            gemini_report = ""
            gemini_model = env.ai_model
            try:
                gemini_report = self._call_with_fallback(
                    env, gemini_model, messages, 0.4, "Gemini"
                )
            except Exception as e:
                logger.error(f"[AI Scraper] Gemini scrape failed: {e}")

            # 3. Google Search 實時抓取 (非魔改)
            search_report = ""
            if env.serpapi_api_key:
                try:
                    logger.info("[AI Scraper] Starting Google Search (SerpApi)...")
                    from serpapi import GoogleSearch
                    search_query = f"{' OR '.join(keywords)} trending developer tools"
                    search = GoogleSearch({
                        "q": search_query,
                        "api_key": env.serpapi_api_key,
                        "num": 10
                    })
                    res = search.get_dict()
                    results = res.get("organic_results", [])
                    search_report = "Google Search Results:\n"
                    for r in results:
                        search_report += f"- {r.get('title')}: {r.get('link')}\n Snippet: {r.get('snippet')}\n"
                except Exception as e:
                    logger.error(f"[AI Scraper] Google Search scrape failed: {e}")

            # 4. 直接解析 JSON + 合併去重（無需額外 AI 匯總呼叫）
            grok_data = (safe_parse_json(grok_report) if grok_report else []) or []
            gemini_data = (safe_parse_json(gemini_report) if gemini_report else []) or []

            logger.info(f"[AI Scraper] Parse results - Grok: {len(grok_data)} items, Gemini: {len(gemini_data)} items")

            # 合併 + 去重（以 title 小寫完全匹配為準）
            seen_titles = set()
            data = []
            for item in grok_data + gemini_data:
                if not isinstance(item, dict):
                    continue
                title_key = item.get("title", "").strip().lower()
                if title_key and title_key not in seen_titles:
                    seen_titles.add(title_key)
                    data.append(item)

            # 如果也有 Google Search 結果，補充到 metrics 中但不影響主結構
            if search_report:
                logger.info("[AI Scraper] Google Search results recorded as supplementary source")

            if not data:
                logger.warning("[AI Scraper] JSON parse failed or no valid results, falling back to raw text mode")
                combined_report = f"### Grok Report\n{grok_report}\n\n### Gemini Report\n{gemini_report}"
                return [
                    ScrapeResult(
                        platform=self.platform,
                        external_id=f"ai-master-{datetime.now().strftime('%Y%m%d%H%M')}",
                        title=f"AI 深度研究報告 ({datetime.now().strftime('%m/%d')})",
                        url="https://gemini.google.com",
                        author="Grok + Gemini",
                        description=combined_report[:500] + "...",
                        metrics={"type": "raw_report", "full_content": combined_report},
                        tags=["AI_Report", "MultiModel"],
                        created_at=datetime.now()
                    )
                ]

            logger.info(f"[AI Scraper] After merge and dedup: {len(data)} results")

            results = []
            for item in data:
                ext_id = f"ai-{hashlib.sha256(item.get('title', 'unknown').encode()).hexdigest()[:16]}"
                # 將 ring 和 quadrant 合併到 metrics 中保存
                item_metrics = item.get("metrics", {})
                item_metrics["ring"] = item.get("ring", "assess")
                item_metrics["quadrant"] = item.get("quadrant", "tools")
                result = ScrapeResult(
                    platform=self.platform,
                    external_id=ext_id,
                    title=item.get("title", "Unknown"),
                    url=item.get("url", "https://google.com"),
                    author=item.get("author", "AI Synthesizer"),
                    description=item.get("description"),
                    metrics=item_metrics,
                    tags=item.get("tags", []),
                    created_at=datetime.now()
                )
                results.append(result)
                
            return results
            
        except Exception as e:
            logger.error(f"[AI Scraper] Funnel summarization pipeline failed: {e}")
            return []
