"""
Hermit Purple 數據庫模型定義

用途：定義 SQLAlchemy ORM 模型
"""

from datetime import datetime, date, timezone
from enum import Enum
from typing import Any

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Date,
    Boolean,
    JSON,
    ForeignKey,
    Enum as SQLEnum,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 基類"""
    pass


class Platform(str, Enum):
    """資源來源平台（where the content was originally published）"""
    # Developer communities
    GITHUB = "github"
    REDDIT = "reddit"
    HACKERNEWS = "hackernews"
    PRODUCTHUNT = "producthunt"

    # Video platforms
    YOUTUBE = "youtube"
    BILIBILI = "bilibili"

    # Social media
    X_TWITTER = "x_twitter"
    THREADS = "threads"
    INSTAGRAM = "instagram"

    # Chinese platforms
    XIAOHONGSHU = "xiaohongshu"
    DOUYIN = "douyin"
    WEIBO = "weibo"

    # Knowledge / News
    SUBSTACK = "substack"
    MEDIUM = "medium"
    ARXIV = "arxiv"

    # Legacy
    AI_SEARCH = "ai_search"
    AI_SUMMARY = "ai_summary"

    # Catch-all
    WEB_OTHER = "web_other"


class SourceTier(str, Enum):
    """資料取得方式（how we obtained the data）"""
    # Tier 1: Direct API
    DIRECT_API = "direct_api"

    # Tier 2: AI Search
    PERPLEXICA = "perplexica"
    GEMINI_GROUND = "gemini_ground"
    GROK_SEARCH = "grok_search"

    # Tier 3: Web Crawler
    WEB_CRAWLER = "web_crawler"

    # Legacy (backward compat)
    AI_SEARCH = "ai_search"
    AI_SUMMARY = "ai_summary"


class Resource(Base):
    """
    抓取的資源（項目、帖子、視頻）
    
    不變式：每個 (platform, external_id) 組合必須唯一
    """
    __tablename__ = "resources"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(SQLEnum(Platform), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # 資料來源追蹤（nullable for backward compat with legacy records）
    source_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    citation_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # 智能審計字段
    verification_status: Mapped[str] = mapped_column(String(20), default="pending") # pending, verified, rejected
    audit_log: Mapped[str | None] = mapped_column(Text, nullable=True) # AI 的思考與驗證過程紀錄
    
    # 關聯
    report_links: Mapped[list["ReportResource"]] = relationship(
        "ReportResource", back_populates="resource", cascade="all, delete-orphan"
    )
    
    __table_args__ = (
        Index("ix_resource_platform_external", "platform", "external_id", unique=True),
        Index("ix_resource_scraped_at", "scraped_at"),
    )
    
    def __repr__(self) -> str:
        return f"<Resource {self.platform.value}:{self.external_id}>"


class ResourceCategory(Base):
    """Resource ↔ Category 多對多關聯（一筆 resource 可屬於多個 preset 分類）"""
    __tablename__ = "resource_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resource_id: Mapped[int] = mapped_column(Integer, ForeignKey("resources.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (
        Index("ix_rc_resource_category", "resource_id", "category", unique=True),
        Index("ix_rc_category", "category"),
    )

    def __repr__(self) -> str:
        return f"<ResourceCategory resource={self.resource_id} category={self.category!r}>"


class Report(Base):
    """
    生成的週報

    不變式：每個 (week_start, category) 只能有一份報告
    """
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resource_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # 關聯
    resource_links: Mapped[list["ReportResource"]] = relationship(
        "ReportResource", back_populates="report", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_report_week_start", "week_start"),
        Index("ix_report_week_category", "week_start", "category", unique=True),
    )

    def __repr__(self) -> str:
        cat = f" [{self.category}]" if self.category else ""
        return f"<Report {self.week_start} - {self.week_end}{cat}>"


class ReportResource(Base):
    """
    週報-資源關聯表
    
    用途：記錄每份報告包含哪些資源，以及是否為重點推薦
    """
    __tablename__ = "report_resources"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(Integer, ForeignKey("reports.id"), nullable=False)
    resource_id: Mapped[int] = mapped_column(Integer, ForeignKey("resources.id"), nullable=False)
    highlight: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # 關聯
    report: Mapped[Report] = relationship("Report", back_populates="resource_links")
    resource: Mapped[Resource] = relationship("Resource", back_populates="report_links")
    
    __table_args__ = (
        Index("ix_report_resource_unique", "report_id", "resource_id", unique=True),
    )
    
    def __repr__(self) -> str:
        return f"<ReportResource report={self.report_id} resource={self.resource_id}>"
