"""
VibeDev GitHub 爬蟲

用途：使用 PyGitHub 搜索 GitHub 上的相關倉庫
依賴：PyGitHub
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from github import Github, GithubException
from github.Repository import Repository

from .base import BaseScraper, ScrapeResult
from ..db.models import Platform
from ..config import get_env, get_config
from ..utils import with_retry

logger = logging.getLogger(__name__)


class GitHubScraper(BaseScraper):
    """
    GitHub 爬蟲
    
    用途：搜索 GitHub 上的 VibeCoding 相關倉庫
    依賴：需要 GITHUB_TOKEN 環境變量
    """
    
    def __init__(self):
        env = get_env()
        self._token = env.github_token
        self._client: Github | None = None
    
    @property
    def platform(self) -> Platform:
        return Platform.GITHUB
    
    @property
    def client(self) -> Github:
        """獲取 GitHub 客戶端（懶加載）"""
        if self._client is None:
            if self._token:
                self._client = Github(self._token)
            else:
                # 無 token 時使用匿名訪問（有限速率）
                logger.warning("Using anonymous GitHub access, rate limit: 60/hour")
                self._client = Github()
        return self._client
    
    @with_retry(max_retries=3, exceptions=(GithubException, ConnectionError))
    def _search_api(self, query):
        """帶重試的搜索調用"""
        return self.client.search_repositories(
            query=query,
            sort="updated",
            order="desc",
        )
    
    def scrape(
        self,
        keywords: list[str],

        days: int = 7,
        max_results: int = 50,
    ) -> list[ScrapeResult]:
        """
        用途：搜索最近 N 天內創建或更新的相關倉庫
        
        @param keywords: 搜索關鍵詞列表
        @param days: 搜索最近 N 天
        @param max_results: 最大結果數量
        @returns: ScrapeResult 列表
        
        失敗：
        - 無效 token: GithubException (code: 401)
        - 超出速率限制: GithubException (code: 403)
        """
        config = get_config()
        min_stars = config.platforms.github.min_stars
        
        # 構建日期過濾器
        since_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        
        results: list[ScrapeResult] = []
        seen_repos: set[str] = set()
        
        for keyword in keywords:
            if len(results) >= max_results:
                break
            
            try:
                # 構建搜索查詢
                # 示例: "vibecoding in:name,description,readme pushed:>2024-01-24 stars:>5"
                query = f"{keyword} in:name,description,readme pushed:>{since_date}"
                if min_stars > 0:
                    query += f" stars:>={min_stars}"
                
                # 使用帶重試的搜索
                repos = self._search_api(query)
                
                for repo in repos:
                    if len(results) >= max_results:
                        break

                    
                    # 避免重複
                    if repo.full_name in seen_repos:
                        continue
                    seen_repos.add(repo.full_name)
                    
                    result = self._parse_repo(repo, keywords)
                    if result:
                        results.append(result)
                        
            except GithubException as e:
                # 記錄錯誤但繼續處理其他關鍵詞
                logger.error(f"[GitHub] Error searching '{keyword}': {e}")
                continue
        
        return results
    
    def _parse_repo(self, repo: Repository, keywords: list[str]) -> ScrapeResult | None:
        """
        用途：將 Repository 對象轉換為 ScrapeResult
        
        @param repo: GitHub Repository 對象
        @param keywords: 用於標記匹配的關鍵詞
        @returns: ScrapeResult 或 None（如果解析失敗）
        """
        try:
            # 組合文本用於關鍵詞匹配
            search_text = f"{repo.name} {repo.description or ''}"
            matched_tags = self.match_keywords(search_text, keywords)
            
            return ScrapeResult(
                platform=Platform.GITHUB,
                external_id=repo.full_name,
                title=repo.name,
                description=repo.description,
                url=repo.html_url,
                author=repo.owner.login if repo.owner else "unknown",
                metrics={
                    "stars": repo.stargazers_count,
                    "forks": repo.forks_count,
                    "watchers": repo.watchers_count,
                    "language": repo.language,
                    "open_issues": repo.open_issues_count,
                },
                tags=matched_tags,
                created_at=repo.created_at,
            )
        except Exception as e:
            logger.error(f"[GitHub] Error parsing repo {repo.full_name}: {e}")
            return None
