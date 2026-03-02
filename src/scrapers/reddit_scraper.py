import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from requests.exceptions import RequestException

from .base import BaseScraper, ScrapeResult
from ..db.models import Platform
from ..config import get_env, get_config
from ..utils import with_retry

try:
    import praw
    from prawcore.exceptions import PrawcoreException
    _HAS_PRAW = True
except ImportError:
    PrawcoreException = None
    _HAS_PRAW = False

logger = logging.getLogger(__name__)

# 閾值必須按升序排列
_DAYS_TO_TIME_FILTER = {7: "week", 30: "month", 365: "year"}

_NETWORK_EXCEPTIONS = (RequestException, ConnectionError, ValueError)
if _HAS_PRAW:
    _NETWORK_EXCEPTIONS = (RequestException, ConnectionError, ValueError, PrawcoreException)


def _time_filter(days: int) -> str:
    for threshold, value in _DAYS_TO_TIME_FILTER.items():
        if days <= threshold:
            return value
    return "all"


class RedditScraper(BaseScraper):

    def __init__(self):
        super().__init__()
        env = get_env()
        self._client_id = env.reddit_client_id
        self._client_secret = env.reddit_client_secret
        self._user_agent = env.reddit_user_agent

        # 有 credentials + praw 已安裝 → API 模式，否則 → 公開 JSON
        self._use_api = bool(self._client_id and self._client_secret and _HAS_PRAW)

        if self._use_api:
            try:
                self._reddit = praw.Reddit(
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    user_agent=self._user_agent,
                )
                logger.info("[Reddit] Using official API (PRAW)")
            except Exception:
                logger.exception("[Reddit] PRAW init failed, falling back to public JSON")
                self._use_api = False
                self._reddit = None
                self._init_json_session()
        else:
            self._reddit = None
            self._init_json_session()
            if self._client_id and self._client_secret:
                logger.warning("[Reddit] Credentials found but praw not installed, falling back to public JSON")
            else:
                logger.info("[Reddit] No credentials, using public JSON endpoint")

    def _init_json_session(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self._user_agent})

    @property
    def platform(self) -> Platform:
        return Platform.REDDIT

    # ── PRAW API 路徑 ─────────────────────────────────────────────

    @with_retry(max_retries=3, exceptions=_NETWORK_EXCEPTIONS)
    def _search_subreddit_api(self, subreddit_name: str, query: str, limit: int, days: int) -> list[dict]:
        subreddit = self._reddit.subreddit(subreddit_name)
        submissions = subreddit.search(
            query=query,
            sort="new",
            time_filter=_time_filter(days),
            limit=limit,
        )
        return [
            {
                "id": s.id,
                "title": s.title,
                "selftext": s.selftext or "",
                "permalink": s.permalink,
                "author": str(s.author) if s.author else None,
                "score": s.score,
                "upvote_ratio": s.upvote_ratio,
                "num_comments": s.num_comments,
                "subreddit": str(s.subreddit),
                "created_utc": s.created_utc,
            }
            for s in submissions
        ]

    # ── 公開 JSON 路徑 ────────────────────────────────────────────

    @with_retry(max_retries=3, exceptions=_NETWORK_EXCEPTIONS)
    def _search_reddit_json(self, subreddit_name: str, query: str, limit: int, days: int) -> list[dict]:
        url = f"https://www.reddit.com/r/{quote(subreddit_name, safe='')}/search.json"
        params = {
            "q": query,
            "sort": "new",
            "t": _time_filter(days),
            "limit": limit,
            "restrict_sr": "on",
            "raw_json": 1,
        }
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "data" not in data or "children" not in data["data"]:
            raise ValueError(f"Unexpected Reddit response: {list(data.keys())}")
        return [
            child["data"]
            for child in data["data"]["children"]
            if isinstance(child, dict) and "data" in child
        ]

    # ── 統一調度 ──────────────────────────────────────────────────

    def _search(self, subreddit_name: str, query: str, limit: int, days: int) -> list[dict]:
        if self._use_api:
            return self._search_subreddit_api(subreddit_name, query, limit, days)
        return self._search_reddit_json(subreddit_name, query, limit, days)

    def scrape(
        self,
        keywords: list[str],
        days: int = 7,
        max_results: int = 50,
    ) -> list[ScrapeResult]:
        if max_results <= 0:
            return []

        config = get_config()
        subreddits = config.platforms.reddit.subreddits
        min_upvotes = config.platforms.reddit.min_upvotes
        since_timestamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        query = self.build_query(keywords, operator="OR")

        results: list[ScrapeResult] = []
        seen_posts: set[str] = set()

        for subreddit_name in subreddits:
            if len(results) >= max_results:
                break

            try:
                posts = self._search(
                    subreddit_name,
                    query,
                    limit=min(max_results - len(results), 100),
                    days=days,
                )

                for post in posts:
                    if len(results) >= max_results:
                        break

                    created_utc = post.get("created_utc")
                    score = post.get("score")
                    post_id = post.get("id")

                    if not isinstance(created_utc, (int, float)) or created_utc < since_timestamp:
                        continue
                    if not isinstance(score, (int, float)) or score < min_upvotes:
                        continue
                    if not post_id or post_id in seen_posts:
                        continue

                    seen_posts.add(post_id)
                    result = self._parse_submission(post, keywords)
                    if result:
                        results.append(result)

            except _NETWORK_EXCEPTIONS:
                logger.exception("[Reddit] Network error searching r/%s", subreddit_name)
            except (KeyError, TypeError):
                logger.exception("[Reddit] Parse error searching r/%s", subreddit_name)

        return results

    def _parse_submission(self, post: dict, keywords: list[str]) -> ScrapeResult | None:
        try:
            post_id = post.get("id")
            permalink = post.get("permalink")
            if not post_id or not permalink:
                logger.warning("[Reddit] Skipping post with missing id or permalink")
                return None

            title = post.get("title", "")
            selftext = post.get("selftext", "")
            author = post.get("author", "[deleted]") or "[deleted]"

            return ScrapeResult(
                platform=self.platform,
                external_id=post_id,
                title=title,
                description=selftext[:500] if selftext else None,
                url=f"https://reddit.com{permalink}",
                author=author,
                metrics={
                    "upvotes": post.get("score", 0),
                    "upvote_ratio": post.get("upvote_ratio", 0),
                    "num_comments": post.get("num_comments", 0),
                    "subreddit": post.get("subreddit", ""),
                },
                tags=self.match_keywords(f"{title} {selftext}", keywords),
                created_at=datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc),
            )
        except Exception:
            logger.exception("[Reddit] Error parsing submission %s", post.get("id", "?"))
            return None
