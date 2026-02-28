import asyncio
import json
import logging
from typing import Any, Dict, List

from src.core.plugin import HermitPlugin, PipelineResult, get_plugin_manager
from src.core.config import get_env
from src.config import FALLBACK_API_KEY
from src.core.llm import get_brain
from src.infra.storage import get_knowledge_base
from src.core.sentiment import get_sentiment_engine
from src.core.prompt_engine import get_prompt_engine
from src.core.guard import get_guard

from openai import OpenAI

logger = logging.getLogger(__name__)

class AIBusinessPlugin(HermitPlugin):
    @property
    def name(self) -> str:
        return "ai_business"
        
    @property
    def description(self) -> str:
        return "商業落地方案掃描：專注於 Moyin 專案技術的變現路徑與競品分析"
        
    def run(self, context: Dict[str, Any]) -> PipelineResult:
        guard = get_guard()
        run_type = context.get("run_type", "manual")
        force = context.get("force", False)

        if not force and not guard.check_limit(run_type):
            msg = f"Defense Shield: Limit hit for {run_type}. Use --force to proceed."
            self.emit("error", msg)
            return PipelineResult(success=False, error=msg)

        env = get_env()
        brain = get_brain()
        kb = get_knowledge_base()
        pe = get_prompt_engine()
        sentiment_engine = get_sentiment_engine()
        pm = get_plugin_manager()
        
        # 1. Integration: Call Radar plugins
        radar_leads = []
        
        tr = pm.get_plugin("trend_radar")
        if tr:
            self.emit("status", "Activating TrendRadar...")
            try:
                tr_res = tr.run({"keywords": ["AI Visual Novel", "Game Monetization"]})
                if tr_res.success:
                    scan_data = tr_res.data.get("scan_results", {})
                    for kw, content in scan_data.items():
                        desc = content[:500] if isinstance(content, str) else str(content)[:500]
                        radar_leads.append({
                            "title": f"Trend: {kw}",
                            "description": desc,
                            "url": f"trend_{kw}",
                            "source": "trend_radar"
                        })
                else:
                    self.emit("log", f"TrendRadar skipped: {tr_res.error}")
            except Exception as e:
                self.emit("log", f"TrendRadar failed: {e}")

        sr = pm.get_plugin("social_radar")
        if sr:
            self.emit("status", "Activating SocialRadar...")
            try:
                sr_res = sr.run({"keywords": ["AI Agent Builder"]})
                if sr_res.success:
                    crawl_data = sr_res.data.get("crawl_results", {})
                    for url, content in crawl_data.items():
                        desc = content[:500] if isinstance(content, str) else str(content)[:500]
                        radar_leads.append({
                            "title": f"Social Signal: {url}",
                            "description": desc,
                            "url": url,
                            "source": "social_radar"
                        })
                else:
                    self.emit("log", f"SocialRadar skipped: {sr_res.error}")
            except Exception as e:
                self.emit("log", f"SocialRadar failed: {e}")

        # 2. Decision Brain Scout (AI Brainstorming)
        self.emit("status", "🧠 Generating AI Business Leads...")
        keywords = context.get("keywords") or [
            "AI Visual Novel Monetization", 
            "AI Character Marketplace", 
            "Generative Game Assets Business Model"
        ]
        
        try:
             ai_results = self._scan_business_opportunities(env, keywords, pe)
        except Exception as e:
            logger.error(f"Business scan failed: {e}")
            ai_results = []

        # Combine leads: AI Brainstorming + Radar signals
        results = ai_results + radar_leads
        
        if not results:
             return PipelineResult(success=False, error="No business opportunities or radar signals found.")

        self.emit("found", len(results))

        # 3. Deep Analysis & Sentiment
        processed_items = []
        for i, item in enumerate(results):
            title = item.get("title", "Unknown")
            self.emit("analyzing", {"index": i + 1, "total": len(results), "title": title})
            
            url = item.get("url") or f"generated_{i}"
            description = item.get("description", "No description available")
            
            kb.upsert_resource({
                "id": url, 
                "url": url,
                "title": title,
                "description": description,
                "platform": item.get("source", "ai_business"),
                "raw_content": json.dumps(item)
            })
            
            # Sentiment Analysis (POC with mock comments)
            # In production, we'd extract actual comments from raw_content
            mock_comments = [
                {"author": "UserA", "content": "This is exactly what I need for my game!", "likes": 5},
                {"author": "UserB", "content": "How much does it cost?", "likes": 3}
            ]
            sentiment = sentiment_engine.analyze_comments(title, mock_comments)
            kb.update_market_sentiment(title, sentiment)
            
            # Commercial Viability Analysis
            analysis = self._analyze_business_viability(brain, title, description)
            analysis["market_sentiment"] = sentiment
            
            kb.add_analysis(url, analysis)
            item["analysis"] = analysis
            processed_items.append(item)
            
            self.emit("item_complete", {
                "title": title, 
                "verdict": analysis.get("verdict"),
                "score": analysis.get("score")
            })
            
        summary = self._generate_report(processed_items)
        guard.record_usage(run_type)
        
        return PipelineResult(success=True, data={"summary": summary, "items": processed_items})

    def _scan_business_opportunities(self, env, keywords: List[str], pe) -> List[Dict[str, Any]]:
        client = OpenAI(base_url=env.ai_base_url, api_key=env.ai_api_key or FALLBACK_API_KEY)
        brain = get_brain()
        
        def call_model(model):
            prompt = f"""
            Role: VC Analyst & Business Strategist.
            Task: Identify top 5 REAL-WORLD commercial AI applications related to: {', '.join(keywords)}.

            Only include REAL companies/products with verifiable URLs. If fewer than 5 real examples exist, return fewer. 所有文字欄位（description, revenue_model）使用繁體中文。Do NOT fabricate companies, revenue figures, or URLs.

            Output strictly in JSON format:
            {{"items": [{{"title": "範例產品名稱", "url": "https://example.com/product", "description": "商業模式：以 SaaS 訂閱制提供 AI 分析服務", "revenue_model": "SaaS 訂閱制", "sources": ["https://techcrunch.com/example"]}}]}}
            """
            
            try:
                self.emit("log", f" scouting with {model}...")
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                except Exception:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}]
                    )
                content = resp.choices[0].message.content
                data = brain.extract_json(content)

                # Robust extraction: prefer {"items": [...]} wrapper, fallback to list or other keys
                if isinstance(data, dict):
                    return data.get("items", []) or data.get("leads", []) or data.get("results", []) or []
                if isinstance(data, list):
                    return data
                return []
            except Exception as e:
                self.emit("log", f" model {model} failed: {e}")
                return []

        models = [env.ai_model]
        results_list = [call_model(m) for m in models]
        
        flat_results = []
        seen_urls = set()
        for r_list in results_list:
             if not isinstance(r_list, list): continue
             for item in r_list:
                if item.get("url") and item["url"] not in seen_urls:
                    flat_results.append(item)
                    seen_urls.add(item["url"])
        return flat_results

    def _analyze_business_viability(self, brain, title, description) -> Dict[str, Any]:
        return brain.analyze_content(
            content_snippet=f"{title} - {description}",
            context="Business Model Analysis for AI Visual Novel Platform"
        )

    def _generate_report(self, items: List[Dict]) -> str:
        lines = ["# 💰 Moyin Business Opportunity Report", "", "## Executive Summary", "Scanning for scalable revenue models in AI Gaming/VN space.", ""]
        for item in items:
            an = item.get("analysis", {})
            st = an.get("market_sentiment", {})
            lines.append(f"### {item['title']}")
            lines.append(f"- **Verdict**: {an.get('verdict')} (Confidence: {an.get('score')}%)")
            lines.append(f"- **Biz Model**: {an.get('summary')}")
            lines.append(f"- **Market Sentiment**: {st.get('summary', 'N/A')}")
            lines.append(f"  - **Willingness to Pay**: {int(st.get('willingness_to_pay', 0) * 100)}%")
            lines.append(f"- **Revenue Paths**: {', '.join(an.get('value_prop', []))}")
            lines.append("")
        return "\n".join(lines)
