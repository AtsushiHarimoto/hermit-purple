"""
Hermit Purple Core: Defense Shield (Rate Limiting) with File Locking
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

# New dependency for atomic file operations
from filelock import FileLock 

logger = logging.getLogger(__name__)

class UsageGuard:
    """
    Tracking daily usage to prevent account bans.
    Thread-safe and Process-safe using FileLock.
    
    Limits:
    - 1 Automated/Scheduled Run per day
    - 1 Manual/Custom Run per day
    """
    
    def __init__(self, state_file: str = "data/guard_state.json"):
        # root is 2 levels up from src/core
        root = Path(__file__).parents[2]
        self.state_file = root / state_file
        self.lock_file = self.state_file.with_suffix(".json.lock")
        
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        # We process state on demand, not in __init__ to avoid stale data
        
    def _get_today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read_state_locked(self) -> Dict:
        """Read state under lock"""
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text("utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            return {}

    def _save_state_locked(self, state: Dict):
        """Write state under lock"""
        self.state_file.write_text(json.dumps(state, indent=2), "utf-8")

    def check_limit(self, run_type: str = "manual") -> bool:
        """
        Check if run is allowed. 
        Uses file lock to ensure atomic read-reset-check cycle.
        """
        today = self._get_today_key()
        limit = 1 
        
        # Critical Section
        with FileLock(self.lock_file, timeout=10):
            state = self._read_state_locked()
            
            # Reset logic inside the lock
            if state.get("date") != today:
                # Just pretending to reset to check if it would pass
                # We don't save the reset here, we save it on record_usage
                # But to report correctly we need to know effective count
                current = 0
            else:
                current = state.get(f"{run_type}_count", 0)
        
        if current >= limit:
            logger.warning(f"Defense Shield: Limit hit for {run_type} ({current}/{limit}).")
            return False
            
        return True

    def record_usage(self, run_type: str = "manual"):
        """
        Increment usage counter.
        Atomic operation.
        """
        today = self._get_today_key()
        
        with FileLock(self.lock_file, timeout=10):
            state = self._read_state_locked()
            
            # Atomic Reset
            if state.get("date") != today:
                state = {
                    "date": today,
                    "manual_count": 0,
                    "scheduled_count": 0
                }
            
            # Increment
            key = f"{run_type}_count"
            state[key] = state.get(key, 0) + 1
            
            self._save_state_locked(state)
            logger.info(f"Defense Shield: Recorded {run_type}. Count: {state[key]}")

_guard = None
def get_guard() -> UsageGuard:
    global _guard
    if _guard is None:
        _guard = UsageGuard()
    return _guard
