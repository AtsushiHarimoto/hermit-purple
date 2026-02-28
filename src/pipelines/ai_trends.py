"""
AI 趨勢分析 Pipeline

用途：執行 AI 熱點抓取的完整工作流程
流程（v2 — 三引擎交叉驗證）：
1. 健康檢查
2. 並發執行 Perplexica + Gemini + Grok 三引擎搜尋
3. CrossValidator 交叉驗證
4. 匯總輸出

向後相容：若 Tier 2 引擎全部不可用，回退到舊版雙模型 (Gemini + Grok via Web2API)
"""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI

from ..config import FALLBACK_API_KEY, get_config, get_env
from ..sources.base import _is_gateway_error
from ..utils import build_messages, resilient_ai_call, safe_parse_json
from .base import BasePipeline, PipelineResult

logger = logging.getLogger(__name__)


class AITrendsPipeline(BasePipeline):
    """
    AI 趨勢分析 Pipeline

    v2: 三引擎並行搜尋 + CrossValidator 交叉驗證
    fallback: 舊版雙模型 Gemini + Grok via Web2API
    """

    @property
    def name(self) -> str:
        return "ai_trends"

    @property
    def description(self) -> str:
        return "AI 趨勢分析 - 三引擎並行搜尋 + 交叉驗證"

    async def execute(self, config: Dict[str, Any]) -> PipelineResult:
        """整體編排：優先執行 v2 三引擎管線，若引擎不足或全部失敗則自動回退到 legacy 雙模型管線。"""
        start_time = time.time()

        # 1. 健康檢查
        health_url = config.get("health_url", "http://localhost:9009/health")
        if not await self.check_health(health_url):
            logger.warning("[AI Trends] Gateway health check failed, trying engines directly...")

        keywords = config.get("keywords", [])
        timeout = config.get("timeout", 300)
        days = config.get("days", 7)

        # 2. Try v2 (three-engine + cross-validation)
        v2_result = await self._execute_v2(keywords, days, timeout)
        if v2_result:
            return PipelineResult(
                success=True,
                data=v2_result,
                sources=v2_result.get("engines", []),
                execution_time=time.time() - start_time,
            )

        # 3. Fallback to legacy dual-model
        logger.info("[AI Trends] Falling back to legacy dual-model pipeline")
        return await self._execute_legacy(config, start_time)

    async def _execute_v2(
        self,
        keywords: list[str],
        days: int,
        timeout: int,
    ) -> Optional[Dict[str, Any]]:
        """三引擎平行搜尋 + 交叉驗證管線。成功需 >=2 引擎，否則回傳 None 觸發 legacy fallback。"""
        try:
            from ..sources.registry import build_default_registry
            from ..sources.cross_validator import cross_validate, ValidatedResult
        except ImportError as e:
            logger.warning(f"[AI Trends v2] Import failed: {e}")
            return None

        registry = build_default_registry()
        tier2_engines = registry.get_tier2_engines()
        if not tier2_engines:
            logger.warning("[AI Trends v2] No Tier 2 engines available")
            return None

        # Parallel fetch from all Tier 2 engines
        engine_results: Dict[str, list] = {}
        executor = ThreadPoolExecutor(max_workers=3)
        try:
            loop = asyncio.get_running_loop()

            async def fetch_engine(source):
                name = source.__class__.__name__
                try:
                    results = await asyncio.wait_for(
                        loop.run_in_executor(executor, lambda: source.fetch(keywords, days)),
                        timeout=min(timeout, 120),
                    )
                    logger.info(f"[AI Trends v2] {name}: {len(results)} results")
                    return name, results
                except Exception as e:
                    logger.warning(f"[AI Trends v2] {name} failed: {e}")
                    return name, []

            tasks = [fetch_engine(engine) for engine in tier2_engines]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    continue
                name, items = result
                if items:
                    engine_results[name] = items
        finally:
            executor.shutdown(wait=True)

        if len(engine_results) < 2:
            if engine_results:
                logger.warning(
                    f"[AI Trends v2] Only {len(engine_results)} engine(s) succeeded "
                    f"({', '.join(engine_results)}), need ≥2 for cross-validation; falling back"
                )
            else:
                logger.warning("[AI Trends v2] All engines returned empty results")
            return None

        # Cross-validate
        validated = cross_validate(engine_results)

        # Format output
        output_items = []
        for vr in validated:
            output_items.append({
                "title": vr.title,
                "url": vr.url,
                "description": vr.description,
                "author": vr.author,
                "tags": vr.tags,
                "platform": vr.platform.value,
                "engines": vr.engines,
                "citation_count": vr.citation_count,
                "cross_validated": vr.cross_validated,
                "confidence": round(vr.confidence, 3),
                "ring": vr.ring,
                "citation_urls": vr.citation_urls[:5],
            })

        engines_used = list(engine_results.keys())
        logger.info(
            f"[AI Trends v2] Complete: {len(validated)} validated results "
            f"from {len(engines_used)} engines ({', '.join(engines_used)})"
        )

        return {
            "summary": json.dumps(output_items, ensure_ascii=False, indent=2),
            "validated_results": output_items,
            "engines": engines_used,
            "cross_validated_count": sum(1 for vr in validated if vr.cross_validated),
            "total_results": len(validated),
            "generated_at": datetime.now().isoformat(),
        }

    # ────────────────────────────────────────────
    # Legacy dual-model pipeline (backward compat)
    # ────────────────────────────────────────────

    async def _execute_legacy(self, config: Dict[str, Any], start_time: float) -> PipelineResult:
        """舊版雙模型管線（Gemini + Grok via Web2API），作為 v2 不可用時的向後相容 fallback。"""
        env = get_env()
        timeout = config.get("timeout", 300)
        keywords = config.get("keywords", [])
        prompts_dir = config.get("prompts_dir")

        research_prompt = self._load_prompt(prompts_dir, "research.md", keywords)
        synthesis_prompt_template = self._load_prompt(prompts_dir, "synthesis.md", keywords)

        client = OpenAI(
            base_url=env.ai_base_url,
            api_key=env.ai_api_key or FALLBACK_API_KEY,
            timeout=min(float(timeout), 30.0),
            max_retries=0,
        )

        gemini_task = self._run_model(
            client=client,
            model=config.get("gemini_model", env.ai_model or "gemini-3.0-pro"),
            prompt=research_prompt,
            timeout=timeout,
            source_name="Gemini",
        )
        grok_task = self._run_model(
            client=client,
            model=config.get("grok_model", "grok-3-fast"),
            prompt=research_prompt,
            timeout=timeout,
            source_name="Grok",
        )

        results = await asyncio.gather(gemini_task, grok_task, return_exceptions=True)

        valid_results: List[Dict[str, str]] = []
        sources: List[str] = []
        for i, result in enumerate(results):
            source_name = "Gemini" if i == 0 else "Grok"
            if isinstance(result, Exception):
                logger.warning(f"[AI Trends Legacy] {source_name} failed: {result}")
            elif result:
                valid_results.append({"source": source_name, "content": result})
                sources.append(source_name)

        if not valid_results:
            return PipelineResult(
                success=False,
                data=None,
                error_msg="All sources failed, unable to generate report",
                execution_time=time.time() - start_time,
            )

        summary = await self._funnel_summarize(
            client=client,
            model=config.get("gemini_model", env.ai_model or "gemini-3.0-pro"),
            results=valid_results,
            synthesis_prompt_template=synthesis_prompt_template,
            keywords=keywords,
        )

        return PipelineResult(
            success=True,
            data={
                "summary": summary,
                "raw_results": valid_results,
                "generated_at": datetime.now().isoformat(),
            },
            sources=sources,
            execution_time=time.time() - start_time,
        )

    # ────────────────────────────────────────────
    # Shared utility methods
    # ────────────────────────────────────────────

    def _load_prompt(
        self,
        prompts_dir: Optional[str],
        filename: str,
        keywords: List[str],
    ) -> str:
        if prompts_dir:
            prompt_path = Path(prompts_dir) / filename
            if prompt_path.exists():
                content = prompt_path.read_text(encoding="utf-8")
                keywords_str = ", ".join(keywords)
                return content.replace("{{keywords}}", keywords_str)

        keywords_str = ", ".join(keywords) if keywords else "AI, VibeCoding, Agentic"
        if filename == "research.md":
            return self._get_default_research_prompt(keywords_str)
        elif filename == "synthesis.md":
            return self._get_default_synthesis_prompt()
        return ""

    def _get_default_research_prompt(self, keywords_str: str) -> str:
        return f"""<task>
針對以下關鍵詞進行深度情報掃描，找出最新 AI 突破與商業化應用：{keywords_str}
</task>

<focus_areas>
1. Agentic Coding & Vibecoding — Manus AI, Lovable, Bolt.new, v0, Windsurf 等工具
2. AI 內容創作 — AI 漫劇、AI 小說寫作、長篇敘事一致性
3. 自主遊戲 — AI 驅動 NPC、動態分支劇情、生成式遊戲資產
4. 商業自動化 — 超自動化、Agentic Workflows、Small Model Scaling
5. 商業化 — 近 7 天從 Demo 進入付費/商業產品的工具
</focus_areas>

<classification>
每個項目必須分類：
- ring (成熟度): adopt (建議採用) | trial (值得試驗) | assess (值得觀察) | hold (觀望)
- quadrant (象限): techniques (技術模式) | tools (工具/庫) | platforms (平台/基礎設施) | languages (語言/框架)
</classification>

<constraints>
- 找出 8-12 個高影響力項目
- 提供真實 URL（GitHub、官方網站、X 討論串）
- 如果不確定某項資訊，將 confidence 設為 0.3 以下
- 不要虛構 URL 或數據
- 語言：繁體中文
</constraints>

<output_schema>
直接輸出 JSON 陣列，不要包裹 markdown 或解釋文字：
[
  {{{{
    "title": "工具/趨勢名稱",
    "url": "主要 URL",
    "description": "一句話繁體中文摘要",
    "author": "創建者/組織",
    "tags": ["Agentic", "MCP"],
    "ring": "adopt|trial|assess|hold",
    "quadrant": "techniques|tools|platforms|languages",
    "metrics": {{{{
      "hotness": "High|Medium|Low",
      "sources": ["來源列表"],
      "verdict": "adopt|trial|assess|hold",
      "value_prop": "核心價值"
    }}}}
  }}}}
]
</output_schema>"""

    def _get_default_synthesis_prompt(self) -> str:
        return """<task>
將多個研究來源的報告合併為高品質結構化 JSON 列表。
</task>

<rules>
1. 去重：合併關於同一工具的發現
2. 摘要：繁體中文，精簡但點出核心價值
3. 分類：正確分配 ring (adopt/trial/assess/hold) 和 quadrant (techniques/tools/platforms/languages)
4. 連結：確保 URL 準確
5. 如果不確定，標註 confidence 較低而非虛構資訊
</rules>

<output_schema>
直接輸出 JSON 陣列，不要包裹 markdown 或解釋文字：
[
  {
    "title": "工具/趨勢名稱",
    "url": "主要 URL",
    "description": "一句話繁體中文摘要",
    "author": "創建者/組織",
    "tags": ["標籤"],
    "ring": "adopt|trial|assess|hold",
    "quadrant": "techniques|tools|platforms|languages",
    "metrics": {
      "hotness": "High|Medium|Low",
      "sources": ["來源列表"],
      "verdict": "adopt|trial|assess|hold",
      "value_prop": "核心價值"
    }
  }
]
</output_schema>"""

    async def _run_model(
        self,
        client: OpenAI,
        model: str,
        prompt: str,
        timeout: int,
        source_name: str,
    ) -> Optional[str]:
        messages = build_messages(
            "You are a Senior AI Strategy Analyst. Output ONLY a valid JSON array. No markdown, no explanation.",
            prompt,
        )

        try:
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: client.chat.completions.create(
                        model=model, messages=messages, temperature=0.4,
                    ),
                ),
                timeout=min(timeout, 30),
            )
            return response.choices[0].message.content
        except asyncio.TimeoutError:
            logger.warning(f"[AI Trends Legacy] {source_name} Web2API timeout")
        except Exception as e:
            if not _is_gateway_error(e):
                logger.error(f"[AI Trends Legacy] {source_name} error: {e}")
                return None
            logger.warning(f"[AI Trends Legacy] {source_name} gateway error: {e}")

        # Fallback
        env = get_env()
        if "grok" in model.lower():
            api_key = env.grok_official_api_key
            base_url = env.grok_official_base_url
            fallback_model = env.grok_official_model
        else:
            api_key = env.gemini_api_key
            base_url = env.gemini_official_base_url
            fallback_model = env.gemini_official_model

        if not api_key:
            return None

        try:
            fallback_client = OpenAI(base_url=base_url, api_key=api_key, timeout=180.0)
            loop = asyncio.get_running_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: resilient_ai_call(
                        client=fallback_client, model=fallback_model,
                        messages=messages, temperature=0.4,
                    ),
                ),
                timeout=timeout,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"[AI Trends Legacy] {source_name} fallback also failed: {e}")
            return None

    async def _funnel_summarize(
        self,
        client: OpenAI,
        model: str,
        results: List[Dict[str, str]],
        synthesis_prompt_template: str,
        keywords: List[str],
    ) -> str:
        report_str = ""
        for result in results:
            report_str += f"\n[{result['source']}]\n{result['content']}\n"

        keywords_str = ", ".join(keywords) if keywords else "AI Trends"
        full_prompt = f"{synthesis_prompt_template}\n\nKeywords: {keywords_str}\n\nInputs:\n{report_str}"

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: resilient_ai_call(
                    client=client,
                    model=model,
                    messages=build_messages(
                        "You are a data synthesis engine. Merge and deduplicate the inputs into a single JSON array. Output ONLY valid JSON.",
                        full_prompt,
                    ),
                    temperature=0.2,
                ),
            )
            content = response.choices[0].message.content
            data = safe_parse_json(content)
            if data:
                return json.dumps(data, ensure_ascii=False, indent=2)
            return content
        except Exception as e:
            logger.error(f"[AI Trends Legacy] Funnel summarization failed: {e}")
            return report_str
