"""
Plugin System Core with Event Support
"""
import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable

from ..pipelines.base import PipelineResult  # noqa: F401 — canonical location

logger = logging.getLogger(__name__)


class HermitPlugin(ABC):
    """Base class for all Hermit Purple plugins"""
    
    def __init__(self):
        self._callbacks: List[Callable[[str, Any], None]] = []

    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        pass
        
    def on_event(self, callback: Callable[[str, Any], None]):
        """Subscribe to plugin events"""
        self._callbacks.append(callback)

    def clear_callbacks(self):
        """Clear all subscribed callbacks (avoid duplicate listeners across runs)."""
        self._callbacks.clear()

    def emit(self, event_name: str, data: Any = None):
        """Emit an event to subscribers"""
        for cb in self._callbacks:
            try:
                cb(event_name, data)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    @abstractmethod
    def run(self, context: Dict[str, Any]) -> PipelineResult:
        pass


class PluginManager:
    """Discovers and loads plugins"""
    
    def __init__(self):
        self.plugins: Dict[str, HermitPlugin] = {}
        
    def discover_plugins(self, plugin_dirs: List[Any]):
        """Scan directories for plugins"""
        from pathlib import Path
        for root in plugin_dirs:
            if not root.exists():
                continue
            
            # Simple directory scan for src/plugins structure
            for item in root.iterdir():
                if item.is_dir() and (item / "__init__.py").exists():
                    self._load_module(item.name, root)

    def _load_module(self, name: str, root: Any):
        try:
            # Derive module name relative to the project root (two levels above src/core/)
            project_root = Path(__file__).resolve().parents[2]
            try:
                rel = root.resolve().relative_to(project_root)
            except ValueError:
                return
            module_name = ".".join(rel.parts) + f".{name}"

            module = importlib.import_module(module_name)
            
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # Check subclass and NOT the base class itself
                if isinstance(attr, type) and issubclass(attr, HermitPlugin) and attr is not HermitPlugin:
                    plugin_instance = attr()
                    self.plugins[plugin_instance.name] = plugin_instance
                    logger.info(f"Loaded plugin: {plugin_instance.name}")
                    
        except Exception as e:
            logger.error(f"Failed to load plugin {name}: {e}")

    def get_plugin(self, name: str) -> Optional[HermitPlugin]:
        return self.plugins.get(name)

    def list_plugins(self) -> List[HermitPlugin]:
        return list(self.plugins.values())

_manager = PluginManager()

def get_plugin_manager() -> PluginManager:
    return _manager
