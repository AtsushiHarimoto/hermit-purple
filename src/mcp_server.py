"""
Hermit Purple MCP Server

用途：通過 Model Context Protocol 暴露 Hermit Purple 的數據和能力給 Stitch/Claude 等客戶端。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from openai import OpenAI

from .config import FALLBACK_API_KEY, get_env
from .db.database import get_db, init_db
from .db.models import Report, Resource, ResourceCategory
from .report import ReportGenerator
from .scrapers.ai_scraper import AIScraper
from .services.auditor import ContentAuditor
from .services.smart_search import run_smart_health, run_smart_search
from .sources.base import _is_gateway_error
from .utils import _safe_float, _safe_int, resilient_ai_call, safe_parse_json

# 報告輸出目錄（與 GUI 的 REPORTS_ROOT 一致）
REPORTS_DIR = Path(__file__).parent.parent / "reports"
# 提示詞模板目錄
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "ai_trends"


def _ai_probe_with_fallback(env, messages: list[dict], temperature: float = 0.3) -> str:
    """Try Web2API first (single fast probe), fallback to official API on gateway errors.

    Returns the raw response text, or empty string on total failure.
    """
    # Probe: single attempt, no SDK retries, fast timeout
    probe_client = OpenAI(
        base_url=env.ai_base_url,
        api_key=env.ai_api_key or FALLBACK_API_KEY,
        timeout=60.0,
        max_retries=0,
    )
    try:
        resp = probe_client.chat.completions.create(
            model=env.ai_model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        if not _is_gateway_error(e):
            raise
        logger.warning(f"[discover] Web2API gateway error, trying official API: {e}")

    # Fallback: official Gemini API with resilient retry
    if not env.gemini_api_key:
        logger.warning("[discover] No official API key available for fallback")
        return ""

    fallback_client = OpenAI(
        base_url=env.gemini_official_base_url,
        api_key=env.gemini_api_key,
        timeout=60.0,
        max_retries=0,
    )
    resp = resilient_ai_call(
        client=fallback_client,
        model=env.gemini_official_model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def _fetch_ai_keywords(
    category: str, seed_keywords: str, days: int, seed_set: set[str]
) -> tuple[list[dict], str]:
    """Run AI search for trending keywords, returning deduped results.

    Handles prompt loading, AI call with fallback, JSON parsing, and dedup.
    Returns (results, error_reason). error_reason is empty on success.
    """
    env = get_env()
    if not env.ai_base_url:
        return [], "no ai_base_url configured"

    # Load prompt template (fallback to inline prompt)
    prompt_path = _PROMPTS_DIR / "discover_keywords.md"
    if prompt_path.exists():
        prompt = prompt_path.read_text(encoding="utf-8")
        prompt = prompt.replace("{{category}}", category)
        prompt = prompt.replace("{{days}}", str(days))
        prompt = prompt.replace("{{seed_keywords}}", seed_keywords[:2000])
    else:
        prompt = (
            f"List 10-30 emerging keywords/tool names in '{category}' "
            f"from the last {days} days. "
            f"Exclude these existing terms: {seed_keywords[:1000]}. "
            f"Return JSON array: "
            f'[{{"keyword":"Name","score":0.8,"frequency":5,"source":"ai_search"}}]'
        )

    messages = [
        {"role": "user", "content": f"[System] You are an AI trend analyst. Output ONLY valid JSON array.\n\n{prompt}"},
    ]

    try:
        raw = _ai_probe_with_fallback(env, messages, temperature=0.3)
    except Exception as e:
        logger.error(f"[discover] AI search failed for {category}: {e}")
        return [], f"AI call failed: {e}"

    if not raw:
        return [], "AI returned empty response"

    parsed = safe_parse_json(raw)
    if not isinstance(parsed, list):
        return [], "AI response is not a valid JSON array"

    # Dedup: keep highest score per keyword
    best: dict[str, dict] = {}
    for item in parsed:
        if not isinstance(item, dict) or "keyword" not in item:
            continue
        kw_lower = item["keyword"].strip().lower()
        if not kw_lower or kw_lower in seed_set:
            continue
        score = _safe_float(item.get("score", 1.0))
        entry = {
            "keyword": item["keyword"].strip(),
            "score": score,
            "frequency": _safe_int(item.get("frequency", 1)),
            "source": "ai_search",
        }
        prev = best.get(kw_lower)
        if prev is None or score > prev["score"]:
            best[kw_lower] = entry

    return list(best.values()), ""


def _normalize_scores(results: list[dict]) -> None:
    """Normalize score values in-place to 0-1 range (min-max scaling)."""
    if not results:
        return
    scores = [r["score"] for r in results]
    lo, hi = min(scores), max(scores)
    span = hi - lo
    for r in results:
        r["score"] = round((r["score"] - lo) / span, 4) if span else 1.0


def _resource_summary(r: Resource, fields: list[str]) -> dict[str, Any]:
    """Build a JSON-safe dict from a Resource, picking only the requested fields.

    Supported fields: id, title, url, description, platform, metrics,
    audit_log, source_tier, citation_urls.
    """
    accessors: dict[str, Any] = {
        "id": r.id,
        "title": r.title,
        "url": r.url,
        "description": r.description,
        "platform": r.platform.value,
        "metrics": r.metrics,
        "audit_log": r.audit_log,
        "source_tier": r.source_tier,
        "citation_urls": r.citation_urls or [],
    }
    return {f: accessors[f] for f in fields}


# 初始化 MCP 服務
mcp = FastMCP("HermitPurple")
init_db()
logger = logging.getLogger("mcp_server")


@mcp.resource("hermit://resources/pending")
def list_pending_resources() -> str:
    """獲取所有待審核的資源列表 (JSON)"""
    try:
        with get_db() as db:
            resources = (
                db.query(Resource)
                .filter(Resource.verification_status == "pending")
                .order_by(Resource.scraped_at.desc())
                .limit(50)
                .all()
            )
            fields = ["id", "title", "url", "description", "platform", "metrics", "audit_log"]
            return json.dumps([_resource_summary(r, fields) for r in resources], ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error listing pending resources: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.resource("hermit://resources/verified")
def list_verified_resources() -> str:
    """獲取所有已通過審計的資源列表 (JSON)"""
    try:
        with get_db() as db:
            resources = (
                db.query(Resource)
                .filter(Resource.verification_status == "verified")
                .order_by(Resource.scraped_at.desc())
                .limit(50)
                .all()
            )
            fields = ["id", "title", "url", "audit_log", "metrics"]
            return json.dumps([_resource_summary(r, fields) for r in resources], ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error listing verified resources: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.resource("hermit://sources/cross-validated")
def list_cross_validated_resources() -> str:
    """獲取經交叉驗證的資源（多引擎確認, JSON）"""
    try:
        with get_db() as db:
            resources = (
                db.query(Resource)
                .filter(Resource.verification_status == "verified")
                .filter(Resource.source_tier.isnot(None))
                .order_by(Resource.scraped_at.desc())
                .limit(200)
                .all()
            )
            data = []
            for r in resources:
                engines = (r.metrics or {}).get("engines", [])
                if len(engines) < 2:
                    continue  # Only include truly cross-validated items
                data.append({
                    "id": r.id,
                    "title": r.title,
                    "url": r.url,
                    "platform": r.platform.value,
                    "source_tier": r.source_tier,
                    "citation_urls": r.citation_urls or [],
                    "engines": engines,
                    "cross_validated": True,
                    "confidence": (r.metrics or {}).get("confidence", 0),
                    "ring": (r.metrics or {}).get("ring", "assess"),
                })
                if len(data) >= 50:
                    break
            return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error listing cross-validated resources: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.resource("hermit://reports/latest")
def get_latest_report() -> str:
    """獲取最新的 Markdown 週報"""
    try:
        with get_db() as db:
            report = db.query(Report).order_by(Report.created_at.desc()).first()
            if not report:
                return "No reports generated yet."
            return report.content_md
    except Exception as e:
        logger.error(f"Error getting latest report: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
def scrape_ai_trends(keywords: str, days: int = 3, category: str = "") -> str:
    """
    使用 AI 搜索最新技術熱點
    @param keywords: 逗號分隔的關鍵詞 (e.g. "python, ai agent")
    @param days: 搜索最近幾天
    @param category: 觸發本次抓取的 preset 分類名稱（寫入 resource_categories 關聯）
    """
    try:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        scraper = AIScraper()
        results = scraper.scrape(keywords=kw_list, days=days, category=category)

        save_count = 0
        cat = category.strip()
        with get_db() as db:
            # Pre-load existing category links to avoid N+1 queries
            existing_links: set[int] = set()
            if cat:
                existing_links = {
                    row.resource_id
                    for row in db.query(ResourceCategory.resource_id)
                    .filter_by(category=cat)
                    .all()
                }

            for res in results:
                existing = (
                    db.query(Resource)
                    .filter(Resource.platform == res.platform, Resource.external_id == res.external_id)
                    .first()
                )
                if not existing:
                    r_db = Resource(
                        platform=res.platform,
                        external_id=res.external_id,
                        title=res.title,
                        description=res.description,
                        url=res.url,
                        author=res.author,
                        metrics=res.metrics,
                        tags=res.tags,
                        created_at=res.created_at,
                        source_tier=getattr(res, "source_tier", "ai_search"),
                        citation_urls=getattr(res, "citation_urls", None),
                    )
                    db.add(r_db)
                    db.flush()  # assign r_db.id
                    save_count += 1
                    if cat:
                        db.add(ResourceCategory(resource_id=r_db.id, category=cat))
                        existing_links.add(r_db.id)
                else:
                    # Existing resource — add category link if missing
                    if cat and existing.id not in existing_links:
                        db.add(ResourceCategory(resource_id=existing.id, category=cat))
                        existing_links.add(existing.id)

        return json.dumps({
            "ok": True,
            "data": {
                "scraped": len(results),
                "saved": save_count,
                "details": f"Scraped {len(results)} items. Saved {save_count} new items.",
            },
        })
    except Exception as e:
        logger.error(f"Error in scrape_ai_trends: {e}")
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def audit_resource(resource_id: int, status: str, notes: str) -> str:
    """
    人工審核資源 (覆蓋 AI 決定)
    @param resource_id: 資源 ID
    @param status: "verified" | "rejected"
    @param notes: 人工備註
    """
    if status not in ("verified", "rejected"):
        return "Invalid status. Must be 'verified' or 'rejected'."
    try:
        with get_db() as db:
            r = db.query(Resource).get(resource_id)
            if not r:
                return "Resource not found"
            r.verification_status = status
            r.audit_log = f"{r.audit_log}\n[Manual Review]: {notes}"
        return f"Updated resource {resource_id} to {status}"
    except Exception as e:
        logger.error(f"Error in audit_resource: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
def run_ai_curator(batch_size: int = 10) -> str:
    """
    運行 AI 審計員 (對 Pending 資源進行思考)
    """
    try:
        with get_db() as db:
            auditor = ContentAuditor(db)
            result = auditor.audit_pending(batch_size=batch_size)
        attempted = result["attempted"]
        succeeded = result["succeeded"]
        failed = result["failed"]
        return json.dumps({
            "ok": True,
            "data": {
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": failed,
                "details": f"AI Auditor: {succeeded}/{attempted} succeeded, {failed} failed",
            },
        })
    except Exception as e:
        logger.error(f"Error in run_ai_curator: {e}")
        return json.dumps({"ok": False, "error": str(e)})


@mcp.tool()
def generate_weekly_report(keywords: str = "", report_title: str = "", category: str = "") -> str:
    """
    生成本週報告 (基於已驗證資源)

    @param keywords: 逗號分隔的關鍵詞，用於過濾報告內容。空字串表示不過濾。
    @param report_title: 報告標題。空字串時使用預設 "AI 趨勢週報"。
    @param category: 分類名稱（如 VibeCoding、AIQuantTrading），不同分類生成獨立報告。
    """
    try:
        generator = ReportGenerator()
        kw_list = [k.strip().lower() for k in keywords.split(",") if k.strip()] if keywords else []
        title = report_title.strip() if report_title.strip() else None
        cat = category.strip()
        exported_md_path = None
        with get_db() as db:
            report = generator.generate(db=db, filter_keywords=kw_list, report_title=title, category=cat)

            # 同步導出 MD 檔案到 reports/ 目錄，供 GUI「最新資訊」讀取
            try:
                REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                md_path = generator.export_md(report.id, REPORTS_DIR, db=db)
                exported_md_path = str(md_path)
                logger.info(f"Report exported to: {md_path}")
            except Exception as export_err:
                logger.warning(f"Report export to file failed: {export_err}")

            return json.dumps({
                "ok": True,
                "data": {
                    "report_id": report.id,
                    "week_start": str(report.week_start),
                    "exported_md_path": exported_md_path,
                    "details": f"Report generated: ID {report.id} ({report.week_start})",
                },
            }, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error in generate_weekly_report: {e}")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def smart_web_health(timeout: int = 6) -> str:
    """
    搜尋鏈路健康檢查。
    輸出包含 gateway / internet / perplexity / google 狀態。
    """
    try:
        data = run_smart_health(timeout=float(timeout))
        return json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error in smart_web_health: {e}")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def smart_web_search(
    query: str,
    gemini_model: str = "",
    grok_model: str = "",
    timeout: int = 90,
) -> str:
    """
    智能聯網搜尋（固定 fallback）：
    Step1 Gemini -> Step2 Grok -> Step3 Perplexity -> Step4 Google

    回傳 JSON 字串，字段包括：
    - route / model / answer / sources / health / steps / errors
    """
    try:
        result = run_smart_search(
            query=query,
            gemini_model=gemini_model or None,
            grok_model=grok_model or None,
            timeout=float(timeout),
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error in smart_web_search: {e}")
        return json.dumps({"ok": False, "route": "none", "error": str(e), "query": query}, ensure_ascii=False)


@mcp.tool()
def discover_trending_keywords(
    category: str = "",
    seed_keywords: str = "",
    days: int = 30,
    top_k: int = 30,
    use_ai: bool = True,
) -> str:
    """
    從已抓取資源中發現趨勢關鍵詞（A: AI 搜索 + B: 本地聚合補充）

    @param category: 領域分類名稱
    @param seed_keywords: 現有種子詞（逗號分隔），用於排除已知詞
    @param days: 統計最近幾天的資源
    @param top_k: 返回前 K 個關鍵詞
    @param use_ai: 是否啟用 AI 搜索補充（消耗 API 配額）
    """
    try:
        top_k = max(1, min(top_k, 100))

        # Build set of existing seed keywords (lowercased) for exclusion
        seed_set = {kw.strip().lower() for kw in seed_keywords.split(",") if kw.strip()}

        # Ring weight for scoring
        ring_weights = {"adopt": 4, "trial": 3, "assess": 2, "hold": 1}

        # ── Step A: AI Search (primary, high trust) ───────────────────────────
        results_a: list[dict] = []
        ai_error = ""

        if use_ai and category:
            results_a, ai_error = _fetch_ai_keywords(category, seed_keywords, days, seed_set)

        # ── Step B: Local DB Aggregation (secondary) ─────────────────────────
        results_b: list[dict] = []

        with get_db() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            base_q = (
                db.query(Resource)
                .filter(Resource.scraped_at >= cutoff)
                .filter(Resource.verification_status.in_(["verified", "pending"]))
            )
            if category:
                # Check if this category has ANY links (ignoring time window)
                has_any_links = (
                    db.query(ResourceCategory.id)
                    .filter(ResourceCategory.category == category)
                    .limit(1)
                    .first()
                ) is not None

                if has_any_links:
                    # Filter by category — may return empty if no recent data (that's OK)
                    resources = base_q.join(
                        ResourceCategory, Resource.id == ResourceCategory.resource_id
                    ).filter(ResourceCategory.category == category).distinct().all()
                elif seed_set:
                    # No category links yet — use seed keywords to tag-match and backfill
                    logger.info(f"[discover] No resource_categories for {category}, backfilling via seed tags")
                    all_resources = base_q.all()
                    resources = [
                        r for r in all_resources
                        if {t.strip().lower() for t in (r.tags or [])} & seed_set
                    ]
                    # Backfill resource_categories so next refresh uses the JOIN path
                    if resources:
                        existing_rc = {
                            row.resource_id
                            for row in db.query(ResourceCategory.resource_id)
                            .filter_by(category=category).all()
                        }
                        new_ids = [r.id for r in resources if r.id not in existing_rc]
                        for rid in new_ids:
                            db.add(ResourceCategory(resource_id=rid, category=category))
                        logger.info(f"[discover] Backfilled {len(new_ids)} resource_categories for {category}")
                else:
                    # No seed keywords and no links — fallback to unfiltered
                    logger.info(f"[discover] No resource_categories and no seeds for {category}, falling back to unfiltered")
                    resources = base_q.all()
            else:
                resources = base_q.all()

            # Count tag frequency + accumulate ring/confidence
            tag_stats: dict[str, dict] = {}  # tag_lower -> {display, freq, score_sum}

            for r in resources:
                tags = r.tags or []
                metrics = r.metrics or {}
                ring = metrics.get("ring", "assess")
                confidence = _safe_float(metrics.get("confidence", 0.5), default=0.5)
                ring_w = ring_weights.get(ring, 1)

                for tag in tags:
                    tag_lower = tag.strip().lower()
                    if not tag_lower or tag_lower in seed_set:
                        continue

                    if tag_lower not in tag_stats:
                        tag_stats[tag_lower] = {
                            "display": tag.strip(),
                            "frequency": 0,
                            "score_sum": 0.0,
                        }

                    tag_stats[tag_lower]["frequency"] += 1
                    tag_stats[tag_lower]["score_sum"] += ring_w * (0.5 + confidence)

            # Rank by accumulated score
            for tag_lower, stats in tag_stats.items():
                results_b.append({
                    "keyword": stats["display"],
                    "score": round(stats["score_sum"], 2),
                    "frequency": stats["frequency"],
                    "source": "local_db",
                })

        results_b.sort(key=lambda x: x["score"], reverse=True)

        # ── Normalize scores to 0-1 range (min-max per source) ───────────────
        _normalize_scores(results_a)
        _normalize_scores(results_b)

        # ── Merge A + B, dedup, rank ──────────────────────────────────────────
        # AI results first (higher trust), then local — skip duplicates
        seen: set[str] = set()
        merged: list[dict] = []
        for item in [*results_a, *results_b]:
            kw_lower = item["keyword"].lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                merged.append(item)

        # Sort by score descending, take top_k
        merged.sort(key=lambda x: x["score"], reverse=True)
        final = merged[:top_k]

        logger.info(
            f"[discover] {category}: {len(results_a)} AI + {len(results_b)} local "
            f"→ {len(final)} merged keywords"
        )
        if ai_error:
            logger.warning(f"[discover] {category} AI diagnostics: {ai_error}")

        return json.dumps(final, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Error in discover_trending_keywords: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


if __name__ == "__main__":
    # 使用 Stdio 模式運行
    mcp.run()
