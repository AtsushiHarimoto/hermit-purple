from typing import Any, Dict, List
import asyncio
import json
import logging
import hashlib

from src.core.plugin import HermitPlugin, PipelineResult
from src.core.config import get_env
from src.config import FALLBACK_API_KEY
from src.core.llm import get_brain
from src.infra.storage import get_knowledge_base
from src.core.prompt_engine import get_prompt_engine
from src.core.guard import get_guard
from src.utils import run_async

from openai import OpenAI

logger = logging.getLogger(__name__)

class AITrendsPlugin(HermitPlugin):
    @property
    def name(self) -> str:
        return "ai_trends"
        
    @property
    def description(self) -> str:
        return "Deep Analysis of AI Trends using Hybrid AI Search & Verification"
        
    def run(self, context: Dict[str, Any]) -> PipelineResult:
        # 1. Guard Check
        guard = get_guard()
        # Assume manual run from CLI context, ideally context should contain run type
        run_type = context.get("run_type", "manual") 
        
        # Allow forced bypass if needed (e.g. debugging)
        force = context.get("force", False) 

        if not force and not guard.check_limit(run_type):
            msg = f"Daily limit reached for {run_type} runs. Use --force to override (Risk: Account Ban)."
            self.emit("error", msg)
            return PipelineResult(success=False, error=msg)

        config = context.get("config", {})
        default_keywords = ["Agentic", "Vibecoding", "MCP"]
        custom_keywords = context.get("keywords") or []
        if isinstance(custom_keywords, str):
            custom_keywords = [custom_keywords]
        append_keywords = context.get("append_keywords", False)
        if custom_keywords:
            keywords = default_keywords + custom_keywords if append_keywords else custom_keywords
        else:
            keywords = default_keywords
        
        env = get_env()
        brain = get_brain()
        kb = get_knowledge_base()
        pe = get_prompt_engine()
        
        self.emit("status", "Starting AI Research...")

        # 2. Parallel Search
        try:
             # Use Prompt Engine inside do_research
             results = run_async(self._do_research(env, keywords, pe))
        except Exception as e:
            return PipelineResult(success=False, error=str(e))
            
        if not results:
             return PipelineResult(success=False, error="No trends found.")

        self.emit("found", len(results))

        # 3. Deep Analysis
        processed_items = []
        for i, item in enumerate(results):
            title = item.get("title", "Unknown")
            self.emit("analyzing", {"index": i+1, "total": len(results), "title": title})
            
            url = item.get("url")
            if not url:
                seed = f"{title}|{item.get('description','')}|{i}"
                uid = hashlib.sha256(seed.encode("utf-8")).hexdigest()
                url = f"generated://ai_trends/{uid}"
            
            # A. Store Raw (Check dupes logic should be here in real app)
            resource_id = kb.upsert_resource({
                "id": url, 
                "url": url,
                "title": title,
                "description": item.get("description"),
                "platform": "ai_research",
                "raw_content": json.dumps(item)
            })
            
            # B. Analyze
            analysis = brain.analyze_content(
                content_snippet=f"{title} - {item.get('description')}",
                context="AI Trend Research"
            )
            
            kb.add_analysis(resource_id, analysis)
            item["analysis"] = analysis
            processed_items.append(item)
            
            # Emit card data for UI
            self.emit("item_complete", {
                "title": title, 
                "verdict": analysis.get("verdict"),
                "score": analysis.get("score")
            })
            
        summary = self._generate_report(processed_items)
        
        # Only record usage on success
        guard.record_usage(run_type)
        
        return PipelineResult(success=True, data={"summary": summary, "items": processed_items})

    def _parse_items(self, content: str) -> List[Dict[str, Any]]:
        if not content:
            return []
        
        # Strip markdown code blocks if present
        content = content.strip()
        if content.startswith("```"):
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()
            
        try:
            data = json.loads(content)
        except Exception:
            # Try to find JSON array or object inside text
            import re
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except Exception:
                    pass
            else:
                 match = re.search(r'\{.*\}', content, re.DOTALL)
                 if match:
                     try:
                         data = json.loads(match.group(0))
                     except Exception:
                         pass
            if 'data' not in locals():
                 return []

        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Search all values for a list of dicts (AI wrapper objects)
            for key in ("items", "results", "leads", "trends", "tools", "data"):
                val = data.get(key)
                if isinstance(val, list) and len(val) > 0:
                    items = val
                    break
            if not items:
                # Deep search: check every value for a list of dicts
                for val in data.values():
                    if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                        items = val
                        break
            if not items and any(k in data for k in ("title", "name", "tool", "url")):
                # Single item wrapper
                items = [data]

        # Normalize field names — AI models may return 'name'/'tool' instead of 'title'
        normalized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "title" not in item:
                for alt in ("name", "tool", "project", "tool_name", "project_name"):
                    if alt in item:
                        item["title"] = item[alt]
                        break
            normalized.append(item)
        return normalized

    async def _do_research(self, env, keywords: List[str], pe) -> List[Dict[str, Any]]:
        client = OpenAI(base_url=env.ai_base_url, api_key=env.ai_api_key or FALLBACK_API_KEY)
        
        async def call_model(model):
            # Dynamic Prompt Generation per call!
            # We generate a fresh prompt for each model call to minimize signature overlap
            dyn_prompt = pe.permutate("Tech Scout Research", keywords)
            
            try:
                self.emit("log", f" querying {model}...")
                try:
                    resp = await asyncio.to_thread(
                        client.chat.completions.create,

                        model=model, 
                        messages=[{"role": "user", "content": dyn_prompt}],
                        response_format={"type": "json_object"}
                    )
                except Exception as e:
                    self.emit("log", f" response_format failed for {model}: {e}; retrying without response_format")
                    resp = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=model, 
                        messages=[{"role": "user", "content": dyn_prompt}]
                    )
                content = resp.choices[0].message.content
                return self._parse_items(content)
            except Exception as e:
                self.emit("log", f" model {model} failed: {e}")
                return []

        # Deterministic fallback order: primary model first, Grok second.
        fallback_model = getattr(env, "ai_fallback_model", None) or "grok-4-fast"
        ordered_models = [m for m in [env.ai_model, fallback_model] if m]

        # De-duplicate while preserving order.
        seen_models = set()
        models: List[str] = []
        for model in ordered_models:
            if model not in seen_models:
                models.append(model)
                seen_models.add(model)

        for model in models:
            result_items = await call_model(model)
            if not isinstance(result_items, list) or not result_items:
                continue

            flat_results = []
            seen_keys = set()
            for item in result_items:
                if not isinstance(item, dict):
                    continue
                dedupe_key = item.get("url") or item.get("title") or json.dumps(item, sort_keys=True)
                if dedupe_key not in seen_keys:
                    flat_results.append(item)
                    seen_keys.add(dedupe_key)
            if flat_results:
                return flat_results

        return []

    def _generate_report(self, items: List[Dict]) -> str:
        lines = ["# 🧠 Hermit Purple Decision Report", ""]
        for item in items:
            an = item.get("analysis", {})
            lines.append(f"## {item.get('title', 'Untitled')}")
            lines.append(f"**Verdict**: {an.get('verdict')} (Score: {an.get('score')})")
            lines.append(f"**Summary**: {an.get('summary')}")
            lines.append("")
        return "\n".join(lines)
