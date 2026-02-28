"""
VibeDev Reddit 爬蟲

用途：使用 PRAW 搜索 Reddit 上的相關帖子
依賴：PRAW
"""

import logging
from datetime import datetime, timedelta, timezone

import praw
from praw.models import Submission

from prawcore.exceptions import PrawcoreException

from .base import BaseScraper, ScrapeResult
from ..db.models import Platform
from ..config import get_env, get_config
from ..utils import with_retry

logger = logging.getLogger(__name__)


class RedditScraper(BaseScraper):
    """
    Reddit 爬蟲
    
    用途：搜索 Reddit 指定 subreddit 的相關帖子
    依賴：需要 REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET 環境變量
    """
    
    def __init__(self):
        env = get_env()
        self._client_id = env.reddit_client_id
        self._client_secret = env.reddit_client_secret
        self._user_agent = env.reddit_user_agent
        self._reddit: praw.Reddit | None = None
    
    @property
    def platform(self) -> Platform:
        return Platform.REDDIT
    
    @property
    def reddit(self) -> praw.Reddit:
        """獲取 Reddit 客戶端（懶加載）"""
        if self._reddit is None:
            if not self._client_id or not self._client_secret:
                raise ValueError(
                    "Reddit credentials not configured. "
                    "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env"
                )
            
            self._reddit = praw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
            )
        return self._reddit

    @with_retry(max_retries=3, exceptions=(PrawcoreException, ConnectionError))
    def _search_subreddit(self, subreddit, query, limit):
        """帶重試的搜索調用"""
        return list(subreddit.search(
            query=query,
            sort="new",
            time_filter="week",
            limit=limit,
        ))
    
    def scrape(
        self,
        keywords: list[str],
        days: int = 7,
        max_results: int = 50,
    ) -> list[ScrapeResult]:
        """
        用途：搜索最近 N 天內的相關帖子
        
        @param keywords: 搜索關鍵詞列表
        @param days: 搜索最近 N 天
        @param max_results: 最大結果數量
        @returns: ScrapeResult 列表
        
        失敗：
        - 無效憑證: PRAWException
        - 超出速率限制: TooManyRequests
        """
        config = get_config()
        subreddits = config.platforms.reddit.subreddits
        min_upvotes = config.platforms.reddit.min_upvotes
        
        # 計算時間閾值
        since_timestamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        
        results: list[ScrapeResult] = []
        seen_posts: set[str] = set()
        
        # 構建搜索查詢
        query = self.build_query(keywords, operator="OR")
        
        for subreddit_name in subreddits:
            if len(results) >= max_results:
                break
            
            try:
                subreddit = self.reddit.subreddit(subreddit_name)
                
                # 搜索帖子（帶重試）
                submissions = self._search_subreddit(
                    subreddit, 
                    query, 
                    limit=min(max_results - len(results), 100)
                )

                for submission in submissions:
                    if len(results) >= max_results:
                        break

                    
                    # 跳過舊帖子
                    if submission.created_utc < since_timestamp:
                        continue
                    
                    # 跳過低 upvote 帖子
                    if submission.score < min_upvotes:
                        continue
                    
                    # 避免重複
                    if submission.id in seen_posts:
                        continue
                    seen_posts.add(submission.id)
                    
                    result = self._parse_submission(submission, keywords)
                    if result:
                        results.append(result)
                        
            except Exception as e:
                logger.error(f"[Reddit] Error searching r/{subreddit_name}: {e}")
                continue
        
        return results
    
    def _parse_submission(
        self, submission: Submission, keywords: list[str]
    ) -> ScrapeResult | None:
        """
        用途：將 Submission 對象轉換為 ScrapeResult
        
        @param submission: Reddit Submission 對象
        @param keywords: 用於標記匹配的關鍵詞
        @returns: ScrapeResult 或 None（如果解析失敗）
        """
        try:
            # 組合文本用於關鍵詞匹配
            search_text = f"{submission.title} {submission.selftext or ''}"
            matched_tags = self.match_keywords(search_text, keywords)
            
            return ScrapeResult(
                platform=Platform.REDDIT,
                external_id=submission.id,
                title=submission.title,
                description=submission.selftext[:500] if submission.selftext else None,
                url=f"https://reddit.com{submission.permalink}",
                author=str(submission.author) if submission.author else "[deleted]",
                metrics={
                    "upvotes": submission.score,
                    "upvote_ratio": submission.upvote_ratio,
                    "num_comments": submission.num_comments,
                    "subreddit": str(submission.subreddit),
                },
                tags=matched_tags,
                created_at=datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
            )
        except Exception as e:
            logger.error(f"[Reddit] Error parsing submission {submission.id}: {e}")
            return None
