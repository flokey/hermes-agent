"""Catch-up memory review over stored gateway transcripts (``memory.catchup_review``).

The automatic memory review has three gaps on a messaging gateway:

* it fires every ``memory.nudge_interval`` user turns, so a conversation shorter than that is never
  reviewed;
* a live turn cancels a review that is still running, and nothing retries it;
* it lives on the cached agent, which the gateway drops after an idle hour, so a ``session_end``
  style trigger finds nothing to review.

This pass reviews what those left behind. It reads each session's stored transcript (as cold
``/refine`` does, ``gateway/session_cold_review.py``), finds the real user turns newer than the
session's reviewed-up-to marker (``agent/memory_review_marker.py``), and runs the ordinary memory
review on them, from a fresh agent on the session's own route. A finished review advances the
marker; so does a finished turn-nudge review, so nothing is reviewed twice.

It runs once a night at ``at`` (local time, ``hermes_time``) and, with ``idle_minutes`` > 0, also for
a session once it has been idle that long. Cron sessions are never reviewed. Sessions are reviewed
one at a time; a failure is logged and the session is retried on the next pass. Only the launch
profile's sessions are covered.

    memory:
      catchup_review:
        enabled: false
        at: "03:30"           # null: no nightly pass
        idle_minutes: 0       # 0: no idle pass
        max_sessions: 50      # per pass
        context_messages: 20  # earlier messages kept as context before the unreviewed part
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

TICK_SECONDS = 60.0
_LAST_NIGHTLY_KEY = "memory_catchup:last_nightly"


@dataclass(frozen=True)
class CatchupConfig:
    enabled: bool = False
    at: Optional[tuple[int, int]] = (3, 30)
    idle_minutes: int = 0
    max_sessions: int = 50
    context_messages: int = 20


def _parse_at(value: Any) -> Optional[tuple[int, int]]:
    if value is None or value is False or value == "":
        return None
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
    except ValueError:
        logger.warning("memory.catchup_review.at=%r is not HH:MM; the nightly pass is off", value)
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        logger.warning("memory.catchup_review.at=%r is out of range; the nightly pass is off", value)
        return None
    return hour, minute


def _int(raw: dict, key: str, default: int, minimum: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        logger.warning("memory.catchup_review.%s=%r is invalid; using %d", key, value, default)
        return default
    return value


def parse_config(user_config: Any) -> CatchupConfig:
    memory = user_config.get("memory") if isinstance(user_config, dict) else None
    raw = memory.get("catchup_review") if isinstance(memory, dict) else None
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return CatchupConfig()
    return CatchupConfig(
        enabled=True, at=_parse_at(raw.get("at", "03:30")),
        idle_minutes=_int(raw, "idle_minutes", 0, 0), max_sessions=_int(raw, "max_sessions", 50, 1),
        context_messages=_int(raw, "context_messages", 20, 0))


def _state_db(runner: Any) -> Any:
    db = getattr(runner, "_session_db", None)
    return getattr(db, "_db", db)


def is_cron_session(runner: Any, entry: Any) -> bool:
    platform = getattr(getattr(entry, "platform", None), "value", getattr(entry, "platform", None))
    if str(platform or "").lower() == "cron" or str(entry.session_key).startswith("cron"):
        return True
    db = _state_db(runner)
    row = None
    try:
        row = db.get_session(entry.session_id) if db is not None else None
    except Exception:  # health: allow BLE001 -- an unreadable row is not a cron session
        logger.debug("session row read failed for %s", entry.session_id, exc_info=True)
    return isinstance(row, dict) and "cron" in {row.get("source"), row.get("created_source")}


def review_window(transcript: list[dict], marker: Optional[float], context_messages: int) -> list[dict]:
    """The slice to review: from ``context_messages`` before the first unreviewed user turn to the
    end; ``[]`` when every user turn is already reviewed."""
    from agent.memory_review_marker import unreviewed_user_turns
    pending = unreviewed_user_turns(transcript, marker)
    if not pending:
        return []
    return list(transcript[max(0, pending[0] - context_messages):])


def _nightly_due(cfg: CatchupConfig, now: datetime, last_run_date: Optional[str]) -> bool:
    if cfg.at is None:
        return False
    hour, minute = cfg.at
    return now >= now.replace(hour=hour, minute=minute, second=0, microsecond=0) and last_run_date != now.date().isoformat()


class MemoryCatchupReviewer:
    """One per gateway. ``tick`` decides what is due; ``review_session`` does one session."""

    def __init__(self, runner: Any) -> None:
        self.runner = runner
        # session_key -> entry.updated_at of the last idle attempt, so a failing session is not
        # retried every minute (the nightly pass still retries it).
        self._idle_attempted: dict[str, float] = {}

    def _candidates(self, cfg: CatchupConfig, *, idle_only: bool) -> list[Any]:
        from agent.memory_review_marker import read_marker
        db = _state_db(self.runner)
        now_ts = datetime.now().timestamp()
        running = set(getattr(self.runner, "_running_agents", {}) or {})
        out = []
        for entry in self.runner.session_store.list_sessions():
            if entry.session_key in running or entry.origin is None or is_cron_session(self.runner, entry):
                continue
            updated = entry.updated_at.timestamp()
            marker = read_marker(db, entry.session_key)
            if marker is not None and updated <= marker:
                continue  # no activity since the newest reviewed turn
            if idle_only:
                if now_ts - updated < cfg.idle_minutes * 60 or self._idle_attempted.get(entry.session_key) == updated:
                    continue
                self._idle_attempted[entry.session_key] = updated
            out.append(entry)
        return out[:cfg.max_sessions]

    async def review_session(self, entry: Any, cfg: CatchupConfig) -> str:
        """Review one session's unreviewed turns. Returns ``"reviewed"``, ``"up_to_date"``,
        ``"busy"`` or ``"failed"``."""
        from agent.memory_review_marker import read_marker
        key = entry.session_key
        try:
            transcript = await self.runner.async_session_store.load_transcript(entry.session_id)
            window = review_window(transcript, read_marker(_state_db(self.runner), key), cfg.context_messages)
            if not window:
                return "up_to_date"
            if key in (getattr(self.runner, "_running_agents", {}) or {}):
                return "busy"
            return await self.runner._run_in_executor_with_context(self._review_blocking, entry, window)
        except Exception as exc:  # health: allow BLE001 -- one session's failure must not stop the pass
            logger.warning("Catch-up memory review failed for session %s: %s", key, exc, exc_info=True)
            return "failed"

    def _review_blocking(self, entry: Any, window: list[dict]) -> str:
        from agent.background_review import load_background_review_settings
        from gateway.session_cold_review import cold_review_parent
        from tools.approval_context import reset_current_session_key, set_current_session_key
        enabled, task_cfg = load_background_review_settings()
        if not enabled:
            return "up_to_date"
        token = set_current_session_key(entry.session_key)
        try:
            with cold_review_parent(self.runner, entry.origin, entry.session_key, entry.session_id) as parent:
                parent._review_trigger = "catchup"
                parent._chat_type = getattr(entry.origin, "chat_type", None)
                parent._user_id = getattr(entry.origin, "user_id", None)
                parent.background_review_callback = None  # a night-time review posts nothing to the chat
                parent._spawn_background_review_now(
                    messages_snapshot=window, review_memory=True,
                    review_skills="skill_manage" in getattr(parent, "valid_tool_names", set()), task_cfg=task_cfg)
                run = getattr(parent, "_background_review_run", None)
                if run is None:
                    logger.warning("Catch-up memory review for %s did not start (a review is already running)",
                                   entry.session_key)
                    return "busy"
                run.request_done.wait()
                return "reviewed"
        finally:
            reset_current_session_key(token)

    async def run_pass(self, cfg: CatchupConfig, *, idle_only: bool, label: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._candidates(cfg, idle_only=idle_only):
            result = await self.review_session(entry, cfg)
            counts[result] = counts.get(result, 0) + 1
        if counts.get("reviewed") or counts.get("failed"):
            logger.info("Catch-up memory review (%s): %s", label,
                        ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        return counts

    async def tick(self) -> None:
        from gateway.run import _load_gateway_config
        import hermes_time
        cfg = parse_config(await asyncio.to_thread(_load_gateway_config))
        if not cfg.enabled:
            return
        db = _state_db(self.runner)
        now = hermes_time.now()
        last = await asyncio.to_thread(db.get_meta, _LAST_NIGHTLY_KEY) if db is not None else None
        if _nightly_due(cfg, now, last):
            if db is not None:  # before the pass: one attempt a night
                await asyncio.to_thread(db.set_meta, _LAST_NIGHTLY_KEY, now.date().isoformat())
            await self.run_pass(cfg, idle_only=False, label="nightly")
        elif cfg.idle_minutes > 0:
            await self.run_pass(cfg, idle_only=True, label="idle")


async def memory_catchup_watcher(runner: Any, interval: float = TICK_SECONDS) -> None:
    """Supervised gateway watcher: one :class:`MemoryCatchupReviewer` tick per ``interval``."""
    reviewer = MemoryCatchupReviewer(runner)
    await asyncio.sleep(min(interval, 30.0))  # let platforms connect first
    while True:
        try:
            await reviewer.tick()
        except Exception as exc:  # health: allow BLE001 -- keep the watcher alive; the next tick retries
            logger.warning("Catch-up memory review tick failed: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


__all__ = ["CatchupConfig", "MemoryCatchupReviewer", "memory_catchup_watcher", "parse_config", "review_window"]
