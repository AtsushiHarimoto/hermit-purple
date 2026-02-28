import asyncio
import logging
from typing import Any, Dict, List, Optional
from src.core.plugin import HermitPlugin, PipelineResult
from src.utils import run_async

logger = logging.getLogger(__name__)

# Try import crawl4ai
try:
    from crawl4ai import AsyncWebCrawler
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

class SocialRadarPlugin(HermitPlugin):
    """
    Plugin for deep reconnaissance using Crawl4AI (Global) and MediaCrawler (China).
    """
    @property
    def name(self) -> str:
        return "social_radar"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Deep reconnaissance using Crawl4AI and MediaCrawler."

    def run(self, context: Dict[str, Any]) -> PipelineResult:
        """
        Executes deep crawl on specified targets.
        """
        if not CRAWL4AI_AVAILABLE:
            return PipelineResult(
                success=False, 
                error="crawl4ai not installed."
            )

        targets = context.get("targets", [])
        keywords = context.get("keywords", [])
        
        if not targets and keywords:
            for kw in keywords:
                targets.append({"keyword": kw, "platform": "producthunt"})

        if not targets and not keywords:
             targets.append({"url": "https://www.producthunt.com/", "type": "product_hunt_home"})

        results = {}

        async def crawl_tasks():
            async with AsyncWebCrawler(verbose=True) as crawler:
                for target in targets:
                    url = target.get("url")
                    if not url:
                        keyword = target.get("keyword")
                        platform = target.get("platform", "").lower()
                        if keyword and platform == "producthunt":
                            url = f"https://www.producthunt.com/search?q={keyword}"
                        elif keyword and platform == "reddit":
                            url = f"https://www.reddit.com/search/?q={keyword}"
                    
                    if url:
                        logger.info(f"[SocialRadar] Crawling {url}...")
                        result = await crawler.arun(url=url)
                        results[url] = result.markdown
                    else:
                        logger.warning(f"[SocialRadar] No valid URL or keyword for target: {target}")

        try:
            run_async(crawl_tasks())
            return PipelineResult(
                success=True,
                data={"crawl_results": results}
            )
        except Exception as e:
            err_msg = str(e)
            if "Executable doesn't exist" in err_msg or "playwright install" in err_msg:
                return PipelineResult(
                    success=False,
                    error="Playwright browsers not installed. Run: playwright install"
                )
            return PipelineResult(
                success=False,
                error=f"Crawling failed: {err_msg}"
            )
