"""
Hermit Purple 配置管理模組

用途：載入和管理 config.yaml 和 .env 配置
"""

from pathlib import Path
from threading import Lock
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Fallback API key for local gateways that don't require authentication
FALLBACK_API_KEY = "sk-dummy"


class GitHubConfig(BaseModel):
    """GitHub 平台配置"""
    enabled: bool = True
    search_type: list[str] = Field(default_factory=lambda: ["repositories"])
    min_stars: int = 5
    max_results: int = 50


class RedditConfig(BaseModel):
    """Reddit 平台配置"""
    enabled: bool = False
    subreddits: list[str] = Field(default_factory=lambda: ["LocalLLaMA"])
    min_upvotes: int = 10
    max_results: int = 50


class YouTubeConfig(BaseModel):
    """YouTube 平台配置"""
    enabled: bool = True
    channels: list[str] = Field(default_factory=list)
    min_views: int = 1000
    max_results: int = 30


class BilibiliConfig(BaseModel):
    """Bilibili 平台配置"""
    enabled: bool = True
    min_views: int = 5000
    max_results: int = 20


class PlatformsConfig(BaseModel):
    """所有平台配置"""
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    youtube: YouTubeConfig = Field(default_factory=YouTubeConfig)
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)


class KeywordsConfig(BaseModel):
    """關鍵詞配置"""
    primary: list[str] = Field(default_factory=lambda: ["vibecoding", "antigravity"])
    secondary: list[str] = Field(default_factory=lambda: ["cursor ai", "aider"])


class ScheduleConfig(BaseModel):
    """排程配置"""
    scrape_interval: str = "daily"
    report_day: str = "sunday"


class DatabaseConfig(BaseModel):
    """數據庫配置"""
    path: str = "data/hermit.db"


class ApiConfig(BaseModel):
    """API 服務配置"""
    host: str = "0.0.0.0"
    port: int = 8000


class AppConfig(BaseModel):
    """應用完整配置"""
    keywords: KeywordsConfig = Field(default_factory=KeywordsConfig)
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


class EnvSettings(BaseSettings):
    """環境變量配置（敏感信息）"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GitHub
    github_token: str = ""

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "hermit-purple/1.0"

    # Database (override)
    database_url: str | None = None

    # AI (OpenAI Compatible)
    ai_base_url: str = "http://localhost:9009/v1"
    ai_api_key: str = "your-api-key"
    ai_model: str = "gemini-3.0-pro"
    
    # AI Official Fallback (Web2API 403/500/timeout 時自動切換)
    gemini_api_key: str = ""
    gemini_official_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_official_model: str = "gemini-2.5-flash"
    grok_official_api_key: str = ""
    grok_official_base_url: str = "https://api.x.ai/v1"
    grok_official_model: str = "grok-3-mini-fast"
    serpapi_api_key: str = ""
    
    # AI Writer (Official API or Dedicated Gateway for Reports)
    ai_writer_base_url: str = "http://localhost:9009/v1"
    ai_writer_api_key: str = "your-api-key"
    ai_writer_model: str = "gemini-3.0-pro"

    # Perplexica (self-hosted AI search)
    perplexica_api_url: str = "http://localhost:3100"


def load_config(config_path: Path | None = None) -> AppConfig:
    """
    用途：載入 YAML 配置文件
    
    @param config_path: 配置文件路徑，默認為 config.yaml
    @returns: AppConfig 配置對象
    """
    if config_path is None:
        # 從項目根目錄查找
        config_path = Path(__file__).parent.parent / "config.yaml"
    
    if not config_path.exists():
        # 使用默認配置
        return AppConfig()
    
    with open(config_path, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    
    return AppConfig.model_validate(data)


def get_env_settings() -> EnvSettings:
    """
    用途：獲取環境變量配置
    
    @returns: EnvSettings 環境變量對象
    """
    return EnvSettings()


# 全局配置實例（懶加載）
_config: AppConfig | None = None
_env: EnvSettings | None = None
_lock = Lock()


def get_config() -> AppConfig:
    """獲取全局配置"""
    global _config
    with _lock:
        if _config is None:
            _config = load_config()
        return _config


def get_env() -> EnvSettings:
    """獲取全局環境變量"""
    global _env
    with _lock:
        if _env is None:
            _env = get_env_settings()
        return _env
