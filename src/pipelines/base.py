"""
Pipeline 基類

用途：定義 Pipeline 的通用接口和結果結構
不變式：所有 Pipeline 必須繼承此基類並實作 execute 方法
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(init=False)
class PipelineResult:
    """
    Pipeline 執行結果

    @param success 執行是否成功
    @param data 執行結果數據
    @param error_msg 錯誤訊息（失敗時填入）
    @param sources 成功的數據來源列表
    @param execution_time 執行耗時（秒）
    """
    success: bool
    data: Any
    error_msg: Optional[str]
    sources: List[str]
    execution_time: float
    created_at: datetime

    def __init__(
        self,
        success: bool,
        data: Any = None,
        error_msg: Optional[str] = None,
        sources: Optional[List[str]] = None,
        execution_time: float = 0.0,
        created_at: Optional[datetime] = None,
        *,
        error: Optional[str] = None,
    ):
        self.success = success
        self.data = data
        # Accept both 'error' and 'error_msg'; 'error' takes precedence if both provided
        self.error_msg = error if error is not None else error_msg
        self.sources = sources if sources is not None else []
        self.execution_time = execution_time
        self.created_at = created_at if created_at is not None else datetime.now()

    @property
    def error(self) -> Optional[str]:
        """Backward-compatible alias for error_msg."""
        return self.error_msg

    @error.setter
    def error(self, value: Optional[str]):
        self.error_msg = value


class BasePipeline(ABC):
    """
    Pipeline 基類
    
    用途：定義所有 Pipeline 的通用接口
    不變式：子類必須實作 name、description 屬性和 execute 方法
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """
        Pipeline 唯一標識符
        
        @returns Pipeline 名稱（小寫下劃線格式）
        """
        ...
    
    @property
    @abstractmethod
    def description(self) -> str:
        """
        Pipeline 描述
        
        @returns 人類可讀的描述文字
        """
        ...
    
    @abstractmethod
    async def execute(self, config: Dict[str, Any]) -> PipelineResult:
        """
        執行 Pipeline
        
        @param config 配置字典，包含提示詞模板、超時設置等
        @returns PipelineResult 執行結果
        失敗：config 缺失必要字段時返回 success=False
        """
        ...
    
    async def check_health(self, health_url: str) -> bool:
        """
        檢查 API 服務狀態
        
        @param health_url 健康檢查端點 URL
        @returns True 如果服務正常，否則 False
        """
        import httpx
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(health_url)
                if response.status_code == 200:
                    data = response.json()
                    return data.get("status") == "healthy"
                return False
        except Exception:
            return False
