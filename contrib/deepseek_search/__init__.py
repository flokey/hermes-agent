"""deepseek_search: add DeepSeek's server-side web tools to main-model requests.

DeepSeek (Nov 2026) runs ``deepseek_web_search`` / ``deepseek_read_url`` on its own servers:
the tools are declared in ``tools`` and the reply comes back already grounded, with
``annotations`` / ``server_tool_calls`` fields that Hermes ignores. Hermes has no native switch
for this, so an ``llm_request`` middleware appends the two declarations.

Scope: only requests whose base_url is api.deepseek.com, never cron runs (their tool lists are
set per job on purpose), and never a request with thinking turned off: DeepSeek answers
HTTP 400 "client tools can only be used together with deepseek_web_search in thinking mode"
(voice-message turns run with thinking off by default: auxiliary.voice_chat.reasoning_effort).
Turn off by removing ``deepseek_search`` from ``plugins.enabled``.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_TOOLS = ("deepseek_web_search", "deepseek_read_url")
_SKIP_PLATFORMS = {"cron"}


def _is_deepseek(base_url: str) -> bool:
    try:
        host = (urlparse(base_url or "").hostname or "").lower()
    except Exception:
        return False
    return host == "api.deepseek.com" or host.endswith(".deepseek.com")


def _declaration(name: str, api_mode: str) -> dict:
    # Chat Completions takes {"type": ...}; the Anthropic-compatible Messages API also wants name.
    if api_mode == "anthropic_messages":
        return {"type": name, "name": name}
    return {"type": name}


def _thinking_off(request: dict) -> bool:
    """True when the request turns DeepSeek thinking off (extra_body.thinking or a top-level
    thinking / reasoning_effort, whichever form the transport used)."""
    for holder in (request, request.get("extra_body") if isinstance(request.get("extra_body"), dict) else {}):
        th = holder.get("thinking")
        if isinstance(th, dict) and str(th.get("type") or "").lower() == "disabled":
            return True
        if str(holder.get("reasoning_effort") or "").lower() in ("none", "off", "disabled", "false"):
            return True
    return False


def _middleware(request=None, base_url="", platform="", api_mode="", **_):
    if not isinstance(request, dict) or not _is_deepseek(base_url) or platform in _SKIP_PLATFORMS:
        return None
    if _thinking_off(request):
        return None
    tools = request.get("tools")
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        return None
    present = {t.get("type") for t in tools if isinstance(t, dict)}
    added = [_declaration(n, api_mode) for n in _TOOLS if n not in present]
    if not added:
        return None
    new = dict(request)
    new["tools"] = list(tools) + added
    return {"request": new, "source": "deepseek_search", "reason": "declare DeepSeek web tools"}


def register(ctx) -> None:
    ctx.register_middleware("llm_request", _middleware)
