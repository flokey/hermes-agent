"""Per-session "reviewed up to here" marker for the background memory review.

The turn-nudge review fires every ``memory.nudge_interval`` user turns, so a short conversation
never reaches it, and a live turn cancels a review that is still running. The catch-up review
(``gateway/memory_catchup_review.py``) reviews what those left behind; this marker keeps the two
from reviewing the same turns twice.

The marker is the timestamp of the newest real user turn a finished memory review covered, kept in
``state_meta`` under ``memory_review:<session_key>``. A timestamp, not a message id, survives
compaction and session resets: whatever the transcript looks like later, only user turns newer than
the marker are unreviewed. It only moves forward.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

MARKER_PREFIX = "memory_review:"


def _machinery_display_kinds() -> frozenset:
    from gateway.response_filters import MACHINERY_DISPLAY_KINDS
    return frozenset({*MACHINERY_DISPLAY_KINDS, "hidden"})


def _is_out_of_band(message: dict) -> bool:
    from agent.conversation_compression import _message_text, _steer_markers
    opener, _ = _steer_markers()
    return _message_text(message).lstrip().startswith(opener)


def is_reviewable_user_turn(message: Any) -> bool:
    """A message a person typed: not runtime scaffolding, a compaction handoff, an injected
    notification or process notice, a hidden wake, or an out-of-band block."""
    from agent.conversation_compression import _is_real_user_message
    if not _is_real_user_message(message):
        return False
    if message.get("display_kind") in _machinery_display_kinds():
        return False
    return not _is_out_of_band(message)


def _timestamp(message: dict) -> Optional[float]:
    value = message.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def latest_user_turn_ts(messages: Iterable[Any]) -> Optional[float]:
    """Timestamp of the newest reviewable user turn that carries one (``None`` when none does)."""
    stamps = [ts for m in messages if is_reviewable_user_turn(m) and (ts := _timestamp(m)) is not None]
    return max(stamps) if stamps else None


def unreviewed_user_turns(messages: list[dict], marker: Optional[float]) -> list[int]:
    """Indices of reviewable user turns newer than ``marker`` (all of them when there is none).
    A turn without a timestamp counts as unreviewed only when no marker exists yet."""
    out = []
    for i, m in enumerate(messages):
        if not is_reviewable_user_turn(m):
            continue
        ts = _timestamp(m)
        if marker is None or (ts is not None and ts > marker):
            out.append(i)
    return out


def _state_db(db: Any) -> Any:
    db = getattr(db, "_db", db)  # the gateway hands agents a wrapper around SessionDB
    return db if callable(getattr(db, "get_meta", None)) and callable(getattr(db, "set_meta", None)) else None


def read_marker(db: Any, session_key: str) -> Optional[float]:
    store = _state_db(db)
    if store is None or not session_key:
        return None
    raw = store.get_meta(MARKER_PREFIX + session_key)
    if not raw:
        return None
    try:
        value = json.loads(raw).get("ts")
    except (ValueError, AttributeError):
        logger.warning("Ignoring unreadable memory review marker for %s", session_key)
        return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def advance_marker(db: Any, session_key: str, ts: Optional[float], *, trigger: str) -> bool:
    """Move the marker to ``ts`` if that is newer; returns whether it moved."""
    store = _state_db(db)
    if store is None or not session_key or ts is None:
        return False
    current = read_marker(store, session_key)
    if current is not None and current >= ts:
        return False
    store.set_meta(MARKER_PREFIX + session_key, json.dumps({"ts": ts, "trigger": trigger}))
    return True


def record_finished_review(agent: Any, messages_snapshot: list[dict], fallback_ts: float, trigger: str) -> None:
    """After a memory review finished uncancelled: advance its session's marker to the newest user
    turn it saw. A live snapshot whose turns carry no timestamp uses ``fallback_ts`` (when the review
    started), which is later than every turn in it. Never raises."""
    session_key = getattr(agent, "_gateway_session_key", None)
    if not session_key or not any(is_reviewable_user_turn(m) for m in messages_snapshot):
        return
    ts = latest_user_turn_ts(messages_snapshot)
    try:
        advance_marker(getattr(agent, "_session_db", None), session_key, fallback_ts if ts is None else ts,
                       trigger=trigger)
    except Exception:  # health: allow BLE001 -- bookkeeping must not fail a finished review
        logger.warning("Could not record the memory review marker for %s", session_key, exc_info=True)
