"""
Pipeline 模組

用途：提供可擴展的工作流程架構
"""

from .base import BasePipeline, PipelineResult
from .registry import PipelineRegistry
from .ai_trends import AITrendsPipeline

__all__ = [
    "BasePipeline",
    "PipelineResult",
    "PipelineRegistry",
    "AITrendsPipeline",
]
