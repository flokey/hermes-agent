"""DeepSeek Search + Fetch APIs — plugin form.

- ``search``  -> ``POST https://api.deepseek.com/v1/tools/search``
  (``query`` 1-128 chars, ``count`` 1-50). Each hit's ``summary`` (written for models,
  ~600-800 chars) becomes the description, prefixed with the page date when known.
- ``extract`` -> ``POST https://api.deepseek.com/v1/tools/fetch`` (one URL per call, Markdown body).
  Independent tests found Fetch weak on PDFs and slow, so pair search with another extract backend.

Config::

    web:
      search_backend: "deepseek"

Env: ``DEEPSEEK_API_KEY`` (the model provider's key; billed per successful request, failed and empty
calls are free). ``DEEPSEEK_TOOLS_BASE_URL`` overrides ``https://api.deepseek.com``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from plugins.web._common import (
    BaseWebSearchProvider, document, page_error, provider_env, run_extract, run_search, search_ok, setup_schema, title_hit,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.deepseek.com"
_MAX_QUERY_CHARS = 128  # the API answers 400 above this
_MAX_COUNT = 50


def _post(endpoint: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    api_key = provider_env("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is not set")
    base = (provider_env("DEEPSEEK_TOOLS_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    resp = httpx.post(
        f"{base}/v1/tools/{endpoint}", json=payload, timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    if resp.status_code >= 400:
        raise ValueError(f"DeepSeek {endpoint} HTTP {resp.status_code}: {(resp.text or '').strip()[:500]}")
    return resp.json()


def _rows(results: list[Any], limit: int) -> list[dict[str, Any]]:
    """Tool-shape rows; DeepSeek repeats URLs across hits, so keep the first of each."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        text = str(r.get("summary") or r.get("snippet") or "").strip()
        date = str(r.get("date") or "").strip()
        rows.append(title_hit(str(r.get("title") or ""), url, f"{date} · {text}" if date and text else text, len(rows) + 1))
        if len(rows) >= limit:
            break
    return rows


class DeepSeekWebSearchProvider(BaseWebSearchProvider):
    NAME = "deepseek"
    DISPLAY_NAME = "DeepSeek Search"
    KEY_ENV = "DEEPSEEK_API_KEY"
    EXTRACT = True

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        def body() -> dict[str, Any]:
            count = max(1, min(int(limit), _MAX_COUNT))
            q = query.strip()[:_MAX_QUERY_CHARS]
            logger.info("DeepSeek search: '%s' (count=%d)", q, count)
            data = _post("search", {"query": q, "count": count}, timeout=30)
            return search_ok(_rows(data.get("results") or [], count))
        return run_search("DeepSeek", logger, body)

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        def body() -> list[dict[str, Any]]:
            out = []
            for url in urls:
                try:
                    data = _post("fetch", {"url": url}, timeout=60)
                except (ValueError, httpx.HTTPError) as exc:
                    out.append(page_error(url, str(exc)))
                    continue
                content = str(data.get("content") or "")
                entry = document(url, str(data.get("title") or ""), content)
                out.append(entry if content else {**entry, "error": "no content returned"})
            return out
        return run_extract("DeepSeek", logger, list(urls), body)

    def get_setup_schema(self) -> dict[str, Any]:
        return setup_schema(
            "DeepSeek Search", "paid", "DeepSeek Search/Fetch APIs — $0.75 per 1k successful calls, strong on Chinese and news.",
            "DEEPSEEK_API_KEY", "DeepSeek API key", "https://platform.deepseek.com/api_keys",
        )
