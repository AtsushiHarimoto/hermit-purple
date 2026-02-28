"""
Hermit Purple Core Configuration System

Re-exports from src.config (single source of truth).
Unique core-only models (PluginConfig, SystemConfig) are defined here.
"""

from typing import Any

from pydantic import BaseModel, Field

# Re-export canonical config from src.config
from ..config import FALLBACK_API_KEY, get_env, get_config, EnvSettings, AppConfig  # noqa: F401


class PluginConfig(BaseModel):
    """Generic configuration for plugins"""
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class SystemConfig(BaseModel):
    """System-level configuration"""
    log_level: str = "INFO"
    data_dir: str = "data"
    reports_dir: str = "reports"
