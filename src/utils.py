"""
Hermit Purple 工具庫

用途：通用工具函數，重試機制和 JSON 處理
"""

import asyncio
import re
import time
import logging
from functools import wraps
from typing import Type, Callable, TypeVar, Any, Dict, List

import dirtyjson
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, APITimeoutError, RateLimitError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _safe_float(value, default: float = 1.0) -> float:
    """Parse a value to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 1) -> int:
    """Parse a value to int, returning *default* on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_async(coro):
    """Run an async coroutine safely, whether or not an event loop is running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

# 舊版手寫裝飾器 (保留兼容性)
def with_retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            current_delay = delay
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        break
                    time.sleep(current_delay)
                    current_delay *= backoff
            raise last_exception
        return wrapper
    return decorator

# --- 新版高級功能 ---

def safe_parse_json(text: str) -> dict[str, Any] | list[Any] | None:
    """
    魯棒的 JSON 解析器
    能處理：Markdown 代碼塊、裸 JSON、帶導言文字的 JSON、不規範引號
    """
    if not text:
        return None

    cleaned = text.strip()

    # Strategy 1: 去除 Markdown 代碼塊標記
    if "```" in cleaned:
        start = cleaned.find("```")
        if cleaned[start:].startswith(("```json", "```JSON")):
            offset = 7
        else:
            offset = 3

        content_start = start + offset
        content_end = cleaned.find("```", content_start)

        if content_end != -1:
            cleaned = cleaned[content_start:content_end].strip()
        else:
            cleaned = cleaned[content_start:].strip()

    # Strategy 2: 直接解析（裸 JSON）
    try:
        return dirtyjson.loads(cleaned)
    except Exception:
        pass

    # Strategy 3: 從每個 [ 或 { 起始位置嘗試解析
    # 逐位掃描，找第一個可成功解析的完整 JSON（避免 find/rfind 過度截取）
    # 優先 array（多數 AI 回覆是陣列），再嘗試 object
    for opener in ("[", "{"):
        pos = 0
        while True:
            idx = cleaned.find(opener, pos)
            if idx == -1:
                break
            try:
                return dirtyjson.loads(cleaned[idx:])
            except Exception:
                pos = idx + 1

    logger.warning(f"[JSON Fixer] All strategies failed, first 200 chars: {cleaned[:200]}")
    return None

# ── Gateway-safe message builder ─────────────────────────────────────────────
def build_messages(system: str, user: str) -> list[dict[str, str]]:
    """Build chat messages with system instruction inlined into user message.

    moyin-gateway Web2API fails when ``role: "system"`` is present, so we
    merge the system instruction into the user message as a ``[System]`` prefix.
    """
    return [{"role": "user", "content": f"[System] {system}\n\n{user}"}]


# AI 請求重試策略
# 當遇到超時、流控、API錯誤時，指數退避重試
# 最多 3 次嘗試，等待時間 2s → 4s → … → 15s (指數退避)
# 最壞情況: 3 × (15s wait + 60s timeout) ≈ 3.75 min，在 GUI 5min 預算內
ai_retry_policy = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type((APITimeoutError, RateLimitError, APIError)),
    reraise=True,
)

_GOOGLE_RETRY_DELAY_RE = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'")


def _sleep_on_google_retry_hint(error: Exception) -> None:
    """If the error contains a Google API retryDelay hint, sleep for that duration."""
    match = _GOOGLE_RETRY_DELAY_RE.search(str(error))
    if match:
        delay = float(match.group(1))
        logger.warning(f"[AI Retry] Google API rate limit, sleeping {delay}s...")
        time.sleep(delay + 1)


@ai_retry_policy
def resilient_ai_call(client, model, messages, **kwargs):
    """帶有自動重試機制的 AI 調用"""
    try:
        logger.info(f"[AI] Calling {model}...")
        return client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs,
        )
    except Exception as e:
        _sleep_on_google_retry_hint(e)
        logger.warning(f"[AI Retry] {model} failed: {e}")
        raise
