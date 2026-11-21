"""Language-routing web search: each query goes to the backend that searches its script best.

A query containing CJK text (Han, kana, Hangul) goes to ``web.lang_router.cjk`` (default
``deepseek``); any other query goes to ``web.lang_router.other`` (default ``perplexity``). When the
chosen backend fails or returns nothing, the other one is tried once.

Models sometimes pack several queries into one string joined by ``||``. No backend reads that as
an operator, and some reject the long string outright, so the router splits it, routes each part on
its own and merges the hits (deduplicated by URL, renumbered).

Search only: set ``web.search_backend: lang-router`` and leave ``web_extract`` on its own backend::

    web:
      search_backend: lang-router
      lang_router:
        cjk: deepseek
        other: perplexity
"""

from __future__ import annotations

import logging
import re
from typing import Any

from plugins.web._common import BaseWebSearchProvider, run_search, search_fail, search_ok, setup_schema

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")
_DEFAULTS = {"cjk": "deepseek", "other": "perplexity"}
_MAX_PARTS = 6


def is_cjk(text: str) -> bool:
    return _CJK_RE.search(text or "") is not None


def split_queries(query: str) -> list[str]:
    """``a || b || a`` -> ``[a, b]``: stripped, empties and repeats dropped, at most ``_MAX_PARTS``."""
    parts: list[str] = []
    for part in (query or "").split("||"):
        part = part.strip()
        if part and part not in parts:
            parts.append(part)
    return parts[:_MAX_PARTS] or [(query or "").strip()]


def _route_config() -> dict[str, str]:
    from hermes_cli.config import load_config_readonly

    cfg = (load_config_readonly().get("web") or {}).get("lang_router") or {}
    return {k: str(cfg.get(k) or v).strip() for k, v in _DEFAULTS.items()}


def _backend_search(name: str, query: str, limit: int) -> dict[str, Any]:
    from agent.web_search_registry import get_provider

    provider = get_provider(name)
    if provider is None or not provider.supports_search():
        return search_fail(f"lang-router: web backend {name!r} is not installed or cannot search")
    if name == "perplexity":
        # Perplexity decides its managed (Nous gateway) route from the stored selection, which is
        # this router; tell it directly: managed unless a direct key is set.
        from agent.web_search_provider import get_provider_env

        return provider.search_routed(query, limit, managed=not get_provider_env("PERPLEXITY_API_KEY"))
    return provider.search(query, limit)


def _search_one(query: str, limit: int, route: dict[str, str]) -> dict[str, Any]:
    first, second = (route["cjk"], route["other"]) if is_cjk(query) else (route["other"], route["cjk"])
    resp = _backend_search(first, query, limit)
    if resp.get("success") and (resp.get("data") or {}).get("web"):
        return resp
    logger.info("lang-router: %s gave %s for '%s'; trying %s", first,
                resp.get("error") or "no results", query, second)
    retry = _backend_search(second, query, limit)
    if retry.get("success") and (retry.get("data") or {}).get("web"):
        return retry
    # Both came back empty or failed: an empty success beats an error, else name both errors.
    if resp.get("success") or retry.get("success"):
        return search_ok([])
    return search_fail(f"{first}: {resp.get('error')}; {second}: {retry.get('error')}")


def _merge(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for resp in responses:
        for row in (resp.get("data") or {}).get("web") or []:
            url = row.get("url") or ""
            if url in seen:
                continue
            seen.add(url)
            rows.append({**row, "position": len(rows) + 1})
    return rows


class LangRouterWebSearchProvider(BaseWebSearchProvider):
    NAME = "lang-router"
    DISPLAY_NAME = "Language router (CJK / other)"

    def is_available(self) -> bool:
        from agent.web_search_registry import get_provider

        route = _route_config()
        return any((p := get_provider(n)) is not None and p.is_available() for n in route.values())

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        def body() -> dict[str, Any]:
            route = _route_config()
            parts = split_queries(query)
            responses = [_search_one(part, limit, route) for part in parts]
            ok = [r for r in responses if r.get("success")]
            if not ok:
                return search_fail(" | ".join(str(r.get("error")) for r in responses))
            return search_ok(_merge(ok))
        return run_search("lang-router", logger, body)

    def get_setup_schema(self) -> dict[str, Any]:
        return setup_schema(
            "Language router", "free",
            "Routes CJK queries and other queries to two configured backends (web.lang_router), each the other's fallback.",
        )
