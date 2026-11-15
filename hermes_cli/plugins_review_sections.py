"""Plugin-owned sections appended to the background memory/skill review prompt.

A plugin that owns its own durable store (a task list, a CRM, a notes index) needs the review fork
to route some findings to its tools instead of to built-in memory. ``ctx.register_review_prompt_section``
lets it append a short instruction block to the review prompt, next to the tools it admits through
``auxiliary.background_review.extra_tools``.

The block is appended to the review's user message, never to the system prompt: on the same-model
path the fork replays the parent's cached prefix (system prompt + history) byte for byte, and only the
new review turn changes, so the prompt cache is unaffected.
"""
from __future__ import annotations

import logging
import types
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

REVIEW_SECTION_SCOPES = frozenset({"memory", "all"})
DEFAULT_REVIEW_SECTION_MAX_CHARS = 4_000
MAX_REVIEW_SECTION_CHARS = 8_000
MAX_REVIEW_SECTIONS_TOTAL_CHARS = 16_000
_HEADING_PREFIX = "## Plugin review instructions: "


@dataclass(frozen=True)
class PluginReviewPromptSection:
    """One plugin's review-prompt block (``content`` may be a callable of the review info)."""

    id: str
    content: str | Callable[[Mapping[str, Any]], str]
    scope: str
    max_chars: int
    plugin: str


def _sections(manager: Any) -> dict[str, PluginReviewPromptSection]:
    sections = getattr(manager, "_review_prompt_sections", None)
    if not isinstance(sections, dict):
        sections = manager._review_prompt_sections = {}
    return sections


def register_review_prompt_section(
    ctx: Any, id: str, content: str | Callable[[Mapping[str, Any]], str], *, scope: str = "memory",
    max_chars: int = DEFAULT_REVIEW_SECTION_MAX_CHARS,
) -> Any:
    """Body of ``PluginContext.register_review_prompt_section``. ``scope="memory"`` appends the block
    only to reviews that cover memory (memory-only or combined); ``"all"`` also to skills-only ones."""
    from hermes_cli.plugins_dispatch import is_valid_system_prompt_section_id
    if not is_valid_system_prompt_section_id(id):
        raise ValueError("review prompt section id must be 1-128 lowercase characters "
                         "using letters, numbers, '.', '_', or '-'")
    if not isinstance(content, str) and not callable(content):
        raise TypeError("review prompt section content must be a string or callable")
    if scope not in REVIEW_SECTION_SCOPES:
        raise ValueError("review prompt section scope must be one of: " + ", ".join(sorted(REVIEW_SECTION_SCOPES)))
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 0 < max_chars <= MAX_REVIEW_SECTION_CHARS:
        raise ValueError(f"review prompt section max_chars must be between 1 and {MAX_REVIEW_SECTION_CHARS}")
    mapping = _sections(ctx._manager)
    existing = mapping.get(id)
    if existing is not None:
        raise ValueError(f"review prompt section {id!r} is already registered by plugin {existing.plugin!r}")
    section = PluginReviewPromptSection(id=id, content=content, scope=scope, max_chars=max_chars,
                                        plugin=ctx.plugin_id)
    return ctx._register_entry("review_prompt_section", id, mapping, section,
                               "Plugin %s registered review prompt section: %s", id, previous=existing)


def _render_one(section: PluginReviewPromptSection, info: Mapping[str, Any]) -> Optional[str]:
    try:
        value = section.content(info) if callable(section.content) else section.content
    except (Exception, SystemExit) as exc:  # health: allow BLE001 -- plugin code; fail open, logged with traceback
        logger.warning("Review prompt section %s (%s) raised and was skipped: %s", section.id, section.plugin, exc,
                       exc_info=True)
        return None
    if not isinstance(value, str):
        logger.warning("Review prompt section %s (%s) returned %s, not str; skipped",
                       section.id, section.plugin, type(value).__name__)
        return None
    text = value.strip()
    if len(text) > section.max_chars:
        logger.warning("Review prompt section %s (%s) exceeded max_chars (%d > %d) and was skipped",
                       section.id, section.plugin, len(text), section.max_chars)
        return None
    return text or None


def render_review_prompt_sections(manager: Any, info: Mapping[str, Any]) -> str:
    """The registered blocks that apply to this review, in registration order, as one string (``""``
    when none). Fails open: a raising or oversized section is skipped with a warning."""
    from agent.safe_worker_policy import safe_worker_enabled
    if safe_worker_enabled():
        return ""
    frozen = types.MappingProxyType(dict(info))
    covers_memory = bool(frozen.get("review_memory"))
    blocks: list[str] = []
    total = 0
    for section in list(_sections(manager).values()):
        if section.scope == "memory" and not covers_memory:
            continue
        text = _render_one(section, frozen)
        if text is None:
            continue
        block = f"{_HEADING_PREFIX}{section.id}\n\n{text}"
        if total + len(block) > MAX_REVIEW_SECTIONS_TOTAL_CHARS:
            logger.warning("Review prompt section %s (%s) exceeded the aggregate budget (%d chars) and was skipped",
                           section.id, section.plugin, MAX_REVIEW_SECTIONS_TOTAL_CHARS)
            continue
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)
