"""Smart web search service for Hermit Purple.

Deterministic fallback chain:
1) Gemini via local gateway
2) Grok via local gateway
3) Perplexity web search
4) Google web search
"""

from __future__ import annotations

import html
import re
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, unquote, urlparse, urlunparse

import requests
from openai import OpenAI

from src.core.config import get_env
from src.config import FALLBACK_API_KEY
from src.utils import build_messages

DEFAULT_GEMINI_MODEL = "gemini-3.0-pro"
DEFAULT_GROK_MODEL = "grok-4-fast"
DEFAULT_TIMEOUT_SECONDS = 90.0

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


class SmartSearchError(RuntimeError):
    """Raised when all search routes fail."""


@dataclass
class StepTrace:
    step: str
    ok: bool
    detail: str
    elapsed_ms: int


@dataclass
class SearchResult:
    ok: bool
    route: str
    query: str
    model: Optional[str]
    answer: str
    sources: List[str]
    health: Dict[str, Any]
    steps: List[Dict[str, Any]]
    errors: List[str]
    elapsed_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clean_text(text: str) -> str:
    normalized = html.unescape(text or "")
    return re.sub(r"\s+", " ", normalized).strip()


def _extract_urls(text: str, max_count: int = 10) -> List[str]:
    links = re.findall(r"https?://[^\s\"'<>\]\)]+", text or "")
    seen: set[str] = set()
    result: List[str] = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        result.append(link)
        if len(result) >= max_count:
            break
    return result


def _gateway_base_from_ai_base(ai_base_url: str) -> str:
    parsed = urlparse(ai_base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    normalized = parsed._replace(path=path, params="", query="", fragment="")
    return urlunparse(normalized).rstrip("/")


def _openai_base_from_any_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    normalized = parsed._replace(path=path, params="", query="", fragment="")
    return urlunparse(normalized).rstrip("/")


def _is_dns_ok(host: str) -> bool:
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


def _http_status(url: str, timeout: float) -> Dict[str, Any]:
    try:
        response = requests.get(url, timeout=timeout, headers=REQUEST_HEADERS)
        return {
            "ok": response.status_code < 500,
            "status": response.status_code,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "error": str(exc),
        }


def build_network_health(gateway_base_url: str, timeout: float = 6.0) -> Dict[str, Any]:
    started = _now_ms()

    internet = _http_status("https://www.google.com/generate_204", timeout)
    perplexity = _http_status("https://www.perplexity.ai/", timeout)
    google = _http_status("https://www.google.com/search?q=test&hl=zh-TW&num=1", timeout)
    gateway = _http_status(f"{gateway_base_url}/health", timeout)

    return {
        "dns": {
            "google.com": _is_dns_ok("google.com"),
            "www.perplexity.ai": _is_dns_ok("www.perplexity.ai"),
        },
        "internet": internet,
        "perplexity": perplexity,
        "google": google,
        "gateway": gateway,
        "elapsed_ms": _now_ms() - started,
    }


class SmartSearchService:
    """Orchestrates deterministic web search fallbacks."""

    def __init__(
        self,
        gateway_base_url: Optional[str] = None,
        ai_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        gemini_model: Optional[str] = None,
        grok_model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        env = get_env()

        resolved_ai_base = ai_base_url or env.ai_base_url
        self.ai_base_url = _openai_base_from_any_base(resolved_ai_base)
        self.gateway_base_url = (gateway_base_url or _gateway_base_from_ai_base(self.ai_base_url)).rstrip("/")
        self.api_key = (api_key or env.ai_api_key or FALLBACK_API_KEY).strip()

        preferred_gemini = env.ai_model if str(env.ai_model).lower().startswith("gemini-") else DEFAULT_GEMINI_MODEL
        self.gemini_model = gemini_model or preferred_gemini
        self.grok_model = grok_model or getattr(env, "ai_fallback_model", None) or DEFAULT_GROK_MODEL

        self.timeout = timeout
        self.client = OpenAI(
            base_url=self.ai_base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    def get_network_health(self) -> Dict[str, Any]:
        return build_network_health(self.gateway_base_url, timeout=min(8.0, self.timeout))

    def _call_model(self, query: str, model: str) -> tuple[str, List[str], int]:
        started = _now_ms()
        system_prompt = (
            "你是 Moyin 聯網研究助理。\n"
            "要求：\n"
            "1) 必須使用繁體中文回答。\n"
            "2) 優先使用最新資訊。\n"
            "3) 回答最後要列出『來源』URL（每行一個）。\n"
            "4) 若資訊不確定，請明確標示並給出下一步查證建議。"
        )

        response = self.client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=build_messages(system_prompt, f"請聯網搜尋並回答：{query}"),
        )

        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise SmartSearchError(f"{model} 回傳空內容")

        sources = _extract_urls(content)
        elapsed = _now_ms() - started
        return content, sources, elapsed

    def _perplexity_fallback(self, query: str) -> tuple[str, List[str], int]:
        started = _now_ms()
        url = f"https://www.perplexity.ai/search?q={quote_plus(query)}"
        response = requests.get(url, timeout=min(self.timeout, 15.0), headers=REQUEST_HEADERS)
        response.raise_for_status()

        body = response.text
        title_match = re.search(r"<title>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        title = _clean_text(title_match.group(1)) if title_match else "Perplexity Search"

        all_links = _extract_urls(body, max_count=20)
        links: List[str] = [url]
        for link in all_links:
            if "perplexity.ai" in link:
                continue
            if link not in links:
                links.append(link)
            if len(links) >= 9:
                break

        lines = [
            f"[Perplexity fallback] {title}",
            f"查詢：{query}",
            "模型搜尋失敗，已切換至 Perplexity 搜尋頁結果。",
            "來源:",
        ]
        lines.extend(f"- {link}" for link in links)
        return "\n".join(lines), links, _now_ms() - started

    def _google_fallback(self, query: str) -> tuple[str, List[str], int]:
        started = _now_ms()
        url = f"https://www.google.com/search?q={quote_plus(query)}&hl=zh-TW&num=8"
        response = requests.get(url, timeout=min(self.timeout, 15.0), headers=REQUEST_HEADERS)
        response.raise_for_status()
        body = response.text

        links = [url]
        for raw in re.findall(r"/url\?q=(https?[^&\"']+)&", body):
            candidate = unquote(raw)
            if candidate.startswith("http") and candidate not in links:
                links.append(candidate)
            if len(links) >= 9:
                break

        lines = [
            "[Google fallback]",
            f"查詢：{query}",
            "Perplexity 不可用或失敗，已切換至 Google 搜尋結果。",
            "來源:",
        ]
        lines.extend(f"- {link}" for link in links)
        return "\n".join(lines), links, _now_ms() - started

    def search(self, query: str) -> SearchResult:
        started = _now_ms()
        steps: List[StepTrace] = []
        errors: List[str] = []

        health = self.get_network_health()
        gateway_ok = bool(health.get("gateway", {}).get("ok"))

        # Step 1: Gemini
        if gateway_ok:
            step_started = _now_ms()
            try:
                answer, sources, elapsed = self._call_model(query, self.gemini_model)
                steps.append(StepTrace("gemini", True, "成功", elapsed))
                return SearchResult(
                    ok=True,
                    route="gemini",
                    query=query,
                    model=self.gemini_model,
                    answer=answer,
                    sources=sources,
                    health=health,
                    steps=[asdict(s) for s in steps],
                    errors=errors,
                    elapsed_ms=_now_ms() - started,
                )
            except Exception as exc:
                elapsed = _now_ms() - step_started
                errors.append(f"gemini: {exc}")
                steps.append(StepTrace("gemini", False, str(exc), elapsed))
        else:
            errors.append("gemini: gateway health check failed")
            steps.append(StepTrace("gemini", False, "gateway 不健康，略過", 0))

        # Step 2: Grok fallback
        if gateway_ok:
            step_started = _now_ms()
            try:
                answer, sources, elapsed = self._call_model(query, self.grok_model)
                steps.append(StepTrace("grok", True, "成功", elapsed))
                return SearchResult(
                    ok=True,
                    route="grok",
                    query=query,
                    model=self.grok_model,
                    answer=answer,
                    sources=sources,
                    health=health,
                    steps=[asdict(s) for s in steps],
                    errors=errors,
                    elapsed_ms=_now_ms() - started,
                )
            except Exception as exc:
                elapsed = _now_ms() - step_started
                errors.append(f"grok: {exc}")
                steps.append(StepTrace("grok", False, str(exc), elapsed))
        else:
            errors.append("grok: gateway health check failed")
            steps.append(StepTrace("grok", False, "gateway 不健康，略過", 0))

        # Step 3: Perplexity fallback
        if health.get("internet", {}).get("ok") and health.get("perplexity", {}).get("ok"):
            step_started = _now_ms()
            try:
                answer, sources, elapsed = self._perplexity_fallback(query)
                steps.append(StepTrace("perplexity", True, "成功", elapsed))
                return SearchResult(
                    ok=True,
                    route="perplexity",
                    query=query,
                    model=None,
                    answer=answer,
                    sources=sources,
                    health=health,
                    steps=[asdict(s) for s in steps],
                    errors=errors,
                    elapsed_ms=_now_ms() - started,
                )
            except Exception as exc:
                elapsed = _now_ms() - step_started
                errors.append(f"perplexity: {exc}")
                steps.append(StepTrace("perplexity", False, str(exc), elapsed))
        else:
            errors.append("perplexity: 網路或站點健康檢查失敗")
            steps.append(StepTrace("perplexity", False, "網路或站點不可用", 0))

        # Step 4: Google fallback
        if health.get("internet", {}).get("ok") and health.get("google", {}).get("ok"):
            step_started = _now_ms()
            try:
                answer, sources, elapsed = self._google_fallback(query)
                steps.append(StepTrace("google", True, "成功", elapsed))
                return SearchResult(
                    ok=True,
                    route="google",
                    query=query,
                    model=None,
                    answer=answer,
                    sources=sources,
                    health=health,
                    steps=[asdict(s) for s in steps],
                    errors=errors,
                    elapsed_ms=_now_ms() - started,
                )
            except Exception as exc:
                elapsed = _now_ms() - step_started
                errors.append(f"google: {exc}")
                steps.append(StepTrace("google", False, str(exc), elapsed))
        else:
            errors.append("google: 網路或站點健康檢查失敗")
            steps.append(StepTrace("google", False, "網路或站點不可用", 0))

        raise SmartSearchError("所有鏈路均失敗: " + " | ".join(errors))


def run_smart_search(
    query: str,
    gateway_base_url: Optional[str] = None,
    ai_base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    gemini_model: Optional[str] = None,
    grok_model: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    service = SmartSearchService(
        gateway_base_url=gateway_base_url,
        ai_base_url=ai_base_url,
        api_key=api_key,
        gemini_model=gemini_model,
        grok_model=grok_model,
        timeout=timeout,
    )
    return service.search(query).to_dict()


def run_smart_health(
    gateway_base_url: Optional[str] = None,
    ai_base_url: Optional[str] = None,
    timeout: float = 6.0,
) -> Dict[str, Any]:
    env = get_env()
    resolved_ai_base = _openai_base_from_any_base(ai_base_url or env.ai_base_url)
    resolved_gateway = (gateway_base_url or _gateway_base_from_ai_base(resolved_ai_base)).rstrip("/")
    return build_network_health(resolved_gateway, timeout=timeout)
