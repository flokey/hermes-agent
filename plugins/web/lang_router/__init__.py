"""Language-routing web search plugin — bundled, auto-loaded."""
from __future__ import annotations
from plugins.web.lang_router.provider import LangRouterWebSearchProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(LangRouterWebSearchProvider())
