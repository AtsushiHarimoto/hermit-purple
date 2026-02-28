"""
Hermit Purple Infrastructure: Crawler (Resilient HTTP Client)
"""

import logging
import asyncio
from typing import Optional, Dict, Any
import httpx
from random import choice

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

class ResilientCrawler:
    """
    A wrapper around httpx for resilient scraping.
    Handles user-agent rotation, retries, and basic anti-scraping measures.
    Enforces boundaries (No video downloads, etc.)
    """
    
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        
    async def fetch_page(self, url: str) -> Optional[str]:
        """Fetch HTML content from a URL"""
        
        # Boundary Check 1: Video Sites
        if any(x in url for x in ["youtube.com/watch", "tiktok.com", "vimeo.com"]):
            logger.info(f"Skipping video URL: {url}")
            return None
            
        headers = {
            "User-Agent": choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            for attempt in range(3):
                try:
                    response = await client.get(url, headers=headers)
                    response.raise_for_status()
                    
                    # Boundary Check 2: Content Type
                    ctype = response.headers.get("Content-Type", "").lower()
                    if "text/html" not in ctype and "text/plain" not in ctype and "json" not in ctype:
                        logger.warning(f"Skipping non-text content type: {ctype} for {url}")
                        return None
                        
                    return response.text
                except httpx.HTTPError as e:
                    logger.warning(f"Attempt {attempt+1} failed for {url}: {e}")
                    await asyncio.sleep(1 * (attempt + 1))
                except Exception as e:
                    logger.error(f"Unexpected error fetching {url}: {e}")
                    break
        
        return None

    async def fetch_api(self, url: str, params: Dict[str, Any] = None) -> Optional[Dict[str, Any]]:
        """Fetch JSON from an API"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"API request failed {url}: {e}")
                return None

# Global Instance
_crawler = None

def get_crawler() -> ResilientCrawler:
    global _crawler
    if _crawler is None:
        _crawler = ResilientCrawler()
    return _crawler
