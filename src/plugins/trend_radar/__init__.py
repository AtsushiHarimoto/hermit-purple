import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession
from src.core.plugin import HermitPlugin, PipelineResult
from src.utils import run_async

logger = logging.getLogger(__name__)

class TrendRadarPlugin(HermitPlugin):
    """
    Plugin to integrate with TrendRadar MCP Server.
    Acts as a 'Broad Awareness' radar for hot topics.
    """
    @property
    def name(self) -> str:
        return "trend_radar"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Integrates with TrendRadar MCP Server for broad market scanning."

    def run(self, context: Dict[str, Any]) -> PipelineResult:
        """
        Connects to TrendRadar MCP server and queries for hot news.
        """
        python_exe = sys.executable
        # Resolve relative to tools/ directory (hermit-purple's parent)
        hermit_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        tools_root = os.path.dirname(hermit_root)
        trend_radar_root = os.path.join(tools_root, "TrendRadar_temp")
        
        if not os.path.exists(trend_radar_root):
             return PipelineResult(
                success=False,
                error=f"TrendRadar root not found at {trend_radar_root}."
            )

        logger.info(f"[TrendRadar] Connecting to MCP Server at {trend_radar_root}...")

        server_params = StdioServerParameters(
            command=python_exe,
            args=["-m", "mcp_server.server", "--transport", "stdio", "--project-root", trend_radar_root],
            env={**os.environ, "PYTHONPATH": trend_radar_root}
        )

        async def query_mcp():
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    
                    keywords = context.get("keywords", ["AI", "Game", "Visual Novel", "Monetization"])
                    if isinstance(keywords, str):
                        keywords = [keywords]

                    results = {}
                    
                    for keyword in keywords:
                        logger.info(f"[TrendRadar] Scanning for: {keyword}")
                        try:
                            response = await session.call_tool(
                                "search_news", 
                                arguments={"query": keyword, "limit": 10, "include_rss": True}
                            )
                            if response.content and hasattr(response.content[0], 'text'):
                                data = response.content[0].text
                                results[keyword] = data
                            else:
                                results[keyword] = str(response)
                        except Exception as e:
                            logger.error(f"[TrendRadar] Error searching {keyword}: {e}")
                            results[keyword] = {"error": str(e)}

                    return results

        try:
            data = run_async(query_mcp())
            return PipelineResult(
                success=True,
                data={"scan_results": data}
            )
        except Exception as e:
            return PipelineResult(
                success=False,
                error=f"Failed to query TrendRadar: {str(e)}"
            )
