"""
Hermit Purple 爬蟲基類

用途：定義爬蟲的通用接口和數據結構
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..db.models import Platform


@dataclass
class ScrapeResult:
    """
    抓取結果數據結構
    
    用途：統一各平台爬蟲的輸出格式
    """
    platform: Platform
    external_id: str
    title: str
    url: str
    author: str
    description: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    
    def __post_init__(self):
        """驗證必填字段"""
        if not self.external_id:
            raise ValueError("external_id is required")
        if not self.title:
            raise ValueError("title is required")
        if not self.url:
            raise ValueError("url is required")


class BaseScraper(ABC):
    """
    爬蟲基類
    
    用途：定義爬蟲接口，所有平台爬蟲必須繼承此類
    """
    
    @property
    @abstractmethod
    def platform(self) -> Platform:
        """返回爬蟲對應的平台"""
        ...
    
    @abstractmethod
    def scrape(
        self,
        keywords: list[str],
        days: int = 7,
        max_results: int = 50,
    ) -> list[ScrapeResult]:
        """
        用途：執行抓取操作
        
        @param keywords: 搜索關鍵詞列表
        @param days: 搜索最近 N 天的內容
        @param max_results: 最大結果數量
        @returns: 抓取結果列表
        """
        ...
    
    def build_query(self, keywords: list[str], operator: str = "OR") -> str:
        """
        用途：構建搜索查詢字符串
        
        @param keywords: 關鍵詞列表
        @param operator: 連接符（OR / AND）
        @returns: 格式化的查詢字符串
        """
        if not keywords:
            return ""
        
        # 對包含空格的關鍵詞加引號
        formatted = []
        for kw in keywords:
            if " " in kw:
                formatted.append(f'"{kw}"')
            else:
                formatted.append(kw)
        
        return f" {operator} ".join(formatted)
    
    def match_keywords(self, text: str, keywords: list[str]) -> list[str]:
        """
        用途：檢查文本中包含哪些關鍵詞
        
        @param text: 待檢查的文本
        @param keywords: 關鍵詞列表
        @returns: 匹配到的關鍵詞列表
        """
        if not text:
            return []
        
        text_lower = text.lower()
        matched = []
        
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.append(kw)
        
        return matched
