"""
Pipeline 註冊表

用途：管理所有可用的 Pipeline，支持動態註冊和查詢
"""

from typing import Dict, List, Optional, Type

from .base import BasePipeline


class PipelineRegistry:
    """
    Pipeline 註冊表
    
    用途：集中管理所有 Pipeline 實例
    不變式：同一 name 只能註冊一個 Pipeline
    """
    
    _pipelines: Dict[str, BasePipeline] = {}
    
    @classmethod
    def register(cls, pipeline: BasePipeline) -> None:
        """
        註冊一個 Pipeline
        
        @param pipeline Pipeline 實例
        失敗：如果 name 已存在則覆蓋
        """
        cls._pipelines[pipeline.name] = pipeline
    
    @classmethod
    def get(cls, name: str) -> Optional[BasePipeline]:
        """
        獲取指定名稱的 Pipeline
        
        @param name Pipeline 名稱
        @returns Pipeline 實例，若不存在則返回 None
        """
        return cls._pipelines.get(name)
    
    @classmethod
    def list_all(cls) -> List[BasePipeline]:
        """
        列出所有已註冊的 Pipeline
        
        @returns Pipeline 實例列表
        """
        return list(cls._pipelines.values())
    
    @classmethod
    def list_names(cls) -> List[str]:
        """
        列出所有已註冊的 Pipeline 名稱
        
        @returns Pipeline 名稱列表
        """
        return list(cls._pipelines.keys())
    
    @classmethod
    def clear(cls) -> None:
        """
        清空所有註冊的 Pipeline（主要用於測試）
        """
        cls._pipelines.clear()


def auto_register() -> None:
    """
    自動註冊所有內建 Pipeline
    
    用途：在模組載入時自動註冊所有預設 Pipeline
    """
    from .ai_trends import AITrendsPipeline
    
    # 註冊內建 Pipeline
    PipelineRegistry.register(AITrendsPipeline())


# 模組載入時自動註冊
auto_register()
