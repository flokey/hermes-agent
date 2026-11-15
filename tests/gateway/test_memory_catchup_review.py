"""Catch-up memory review over stored gateway transcripts (``memory.catchup_review``)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, UTC
from types import SimpleNamespace

import pytest

from agent import memory_review_marker as mrm
from gateway import memory_catchup_review as mcr


class _MetaStore:
    def __init__(self, sources=None):
        self.rows = {}
        self.sources = sources or {}

    def get_meta(self, key):
        return self.rows.get(key)

    def set_meta(self, key, value):
        self.rows[key] = value

    def get_session(self, session_id):
        return {"source": self.sources.get(session_id, "discord")}


def _user(text, ts):
    return {"role": "user", "content": text, "timestamp": ts}


def _assistant(text, ts):
    return {"role": "assistant", "content": text, "timestamp": ts}


def _entry(key, session_id, updated_ts, platform="discord"):
    return SimpleNamespace(
        session_key=key, session_id=session_id, platform=SimpleNamespace(value=platform),
        origin=SimpleNamespace(platform=platform, chat_id="c1", chat_type="dm", user_id="u1"),
        updated_at=datetime.fromtimestamp(updated_ts, tz=UTC))


class _Runner:
    def __init__(self, entries, transcripts, db):
        self.session_store = SimpleNamespace(list_sessions=lambda: list(entries))
        self._session_db = db
        self._running_agents = {}

        async def load_transcript(session_id):
            return transcripts[session_id]
        self.async_session_store = SimpleNamespace(load_transcript=load_transcript)

    async def _run_in_executor_with_context(self, func, *args):
        return func(*args)


class TestConfig:
    def test_off_unless_enabled(self):
        assert not mcr.parse_config({}).enabled
        assert not mcr.parse_config({"memory": {"catchup_review": {"enabled": False}}}).enabled

    def test_enabled_with_defaults(self):
        cfg = mcr.parse_config({"memory": {"catchup_review": {"enabled": True}}})
        assert (cfg.enabled, cfg.at, cfg.idle_minutes, cfg.max_sessions) == (True, (3, 30), 0, 50)

    def test_null_at_turns_off_the_nightly_pass(self):
        assert mcr.parse_config({"memory": {"catchup_review": {"enabled": True, "at": None}}}).at is None

    def test_bad_values_fall_back(self):
        cfg = mcr.parse_config({"memory": {"catchup_review": {"enabled": True, "at": "25:00", "idle_minutes": -1}}})
        assert cfg.at is None and cfg.idle_minutes == 0


class TestWindow:
    def test_short_session_below_the_nudge_interval_is_reviewed(self):
        transcript = [_user("book the dentist for friday", 1.0), _assistant("noted", 2.0)]
        assert mcr.review_window(transcript, None, 20) == transcript

    def test_window_starts_with_context_before_the_first_new_turn(self):
        transcript = [_user("a", 1.0), _assistant("x", 2.0), _user("b", 3.0), _assistant("y", 4.0),
                      _user("c", 5.0), _assistant("z", 6.0)]
        assert mcr.review_window(transcript, 3.0, 1) == transcript[3:]

    def test_reviewed_session_has_no_window(self):
        assert mcr.review_window([_user("a", 1.0), _assistant("x", 2.0)], 1.0, 20) == []


class TestNightlyDue:
    def test_due_once_after_the_time(self):
        cfg = mcr.CatchupConfig(enabled=True, at=(3, 30))
        before = datetime(2030, 1, 2, 3, 0, tzinfo=UTC)
        after = datetime(2030, 1, 2, 3, 31, tzinfo=UTC)
        assert not mcr._nightly_due(cfg, before, None)
        assert mcr._nightly_due(cfg, after, "2030-01-01")
        assert not mcr._nightly_due(cfg, after, "2030-01-02")


class TestPass:
    def _reviewer(self, entries, transcripts, db):
        reviewer = mcr.MemoryCatchupReviewer(_Runner(entries, transcripts, db))
        reviewed = []

        def fake_review(entry, window):
            reviewed.append((entry.session_key, window))
            mrm.advance_marker(db, entry.session_key, mrm.latest_user_turn_ts(window), trigger="catchup")
            return "reviewed"
        reviewer._review_blocking = fake_review
        return reviewer, reviewed

    def test_reviews_new_turns_once_and_skips_cron(self):
        db = _MetaStore(sources={"cron-sid": "cron"})
        entries = [_entry("k1", "s1", 10.0), _entry("k2", "cron-sid", 10.0)]
        transcripts = {"s1": [_user("a", 1.0), _assistant("x", 2.0)], "cron-sid": [_user("job", 1.0)]}
        reviewer, reviewed = self._reviewer(entries, transcripts, db)
        cfg = mcr.CatchupConfig(enabled=True)

        counts = asyncio.run(reviewer.run_pass(cfg, idle_only=False, label="nightly"))
        assert counts == {"reviewed": 1} and [k for k, _ in reviewed] == ["k1"]
        assert mrm.read_marker(db, "k1") == 1.0
        # Nothing new since (the assistant's later reply is not a user turn): no second review.
        assert asyncio.run(reviewer.run_pass(cfg, idle_only=False, label="nightly")) == {"up_to_date": 1}
        assert len(reviewed) == 1

    def test_turn_nudge_marker_prevents_a_second_review(self):
        db = _MetaStore()
        mrm.advance_marker(db, "k1", 3.0, trigger="nudge")
        transcripts = {"s1": [_user("a", 1.0), _assistant("x", 2.0), _user("b", 3.0), _assistant("y", 4.0)]}
        reviewer, reviewed = self._reviewer([_entry("k1", "s1", 4.0)], transcripts, db)
        counts = asyncio.run(reviewer.run_pass(mcr.CatchupConfig(enabled=True), idle_only=False, label="nightly"))
        assert counts == {"up_to_date": 1} and reviewed == []

    def test_running_session_is_left_alone(self):
        db = _MetaStore()
        reviewer, _reviewed = self._reviewer([_entry("k1", "s1", 10.0)], {"s1": [_user("a", 1.0)]}, db)
        reviewer.runner._running_agents["k1"] = object()
        assert asyncio.run(reviewer.run_pass(mcr.CatchupConfig(enabled=True), idle_only=False, label="x")) == {}

    def test_idle_pass_waits_for_idle_time_and_does_not_retry_a_failure_every_tick(self, caplog):
        db = _MetaStore()
        now = datetime.now().timestamp()
        entries = [_entry("fresh", "s1", now), _entry("idle", "s2", now - 7200)]
        transcripts = {"s1": [_user("a", now)], "s2": [_user("b", now - 7200)]}
        reviewer = mcr.MemoryCatchupReviewer(_Runner(entries, transcripts, db))
        calls = []

        def failing(entry, window):
            calls.append(entry.session_key)
            raise RuntimeError("provider down")
        reviewer._review_blocking = failing
        cfg = mcr.CatchupConfig(enabled=True, idle_minutes=60)
        assert asyncio.run(reviewer.run_pass(cfg, idle_only=True, label="idle")) == {"failed": 1}
        assert calls == ["idle"] and "Catch-up memory review failed for session idle" in caplog.text
        assert mrm.read_marker(db, "idle") is None  # retried by the nightly pass
        assert asyncio.run(reviewer.run_pass(cfg, idle_only=True, label="idle")) == {}

    def test_max_sessions_caps_a_pass(self):
        db = _MetaStore()
        entries = [_entry(f"k{i}", f"s{i}", 10.0) for i in range(5)]
        transcripts = {f"s{i}": [_user("a", 1.0)] for i in range(5)}
        reviewer, reviewed = self._reviewer(entries, transcripts, db)
        asyncio.run(reviewer.run_pass(mcr.CatchupConfig(enabled=True, max_sessions=2), idle_only=False, label="x"))
        assert len(reviewed) == 2


class TestTick:
    def test_nightly_pass_runs_once_per_day(self, monkeypatch):
        db = _MetaStore()
        reviewer = mcr.MemoryCatchupReviewer(_Runner([], {}, db))
        passes = []

        async def fake_pass(cfg, *, idle_only, label):
            passes.append(label)
            return {}
        reviewer.run_pass = fake_pass
        import gateway.run as gateway_run
        import hermes_time
        monkeypatch.setattr(gateway_run, "_load_gateway_config",
                            lambda: {"memory": {"catchup_review": {"enabled": True, "at": "03:30"}}})
        monkeypatch.setattr(hermes_time, "now", lambda: datetime(2030, 1, 2, 4, 0, tzinfo=UTC))
        asyncio.run(reviewer.tick())
        asyncio.run(reviewer.tick())
        assert passes == ["nightly"]
        assert db.rows[mcr._LAST_NIGHTLY_KEY] == "2030-01-02"

    def test_disabled_does_nothing(self, monkeypatch):
        reviewer = mcr.MemoryCatchupReviewer(_Runner([], {}, _MetaStore()))
        reviewer.run_pass = pytest.fail  # would raise if called
        import gateway.run as gateway_run
        monkeypatch.setattr(gateway_run, "_load_gateway_config", dict)
        asyncio.run(reviewer.tick())


class TestReviewBlocking:
    """The per-session body: a cold parent on the session's route runs the ordinary memory review."""

    def test_spawns_a_memory_review_on_the_window_and_waits_for_it(self, monkeypatch):
        import contextlib
        import threading
        import agent.background_review as bg
        import gateway.session_cold_review as cold

        calls = {}

        class _Parent:
            valid_tool_names = {"memory", "skill_manage"}
            background_review_callback = "chat"

            def _spawn_background_review_now(self, **kwargs):
                calls["kwargs"] = kwargs
                calls["trigger"] = self._review_trigger
                calls["identity"] = (self._chat_type, self._user_id)
                calls["callback"] = self.background_review_callback
                done = threading.Event()
                done.set()
                self._background_review_run = SimpleNamespace(request_done=done)

        @contextlib.contextmanager
        def fake_parent(runner, source, session_key, session_id):
            calls["route"] = (source.chat_id, session_key, session_id)
            yield _Parent()

        monkeypatch.setattr(cold, "cold_review_parent", fake_parent)
        monkeypatch.setattr(bg, "load_background_review_settings", lambda: (True, {"extra_tools": ["plan_add"]}))
        reviewer = mcr.MemoryCatchupReviewer(_Runner([], {}, _MetaStore()))
        window = [_user("a", 1.0)]
        assert reviewer._review_blocking(_entry("k1", "s1", 2.0), window) == "reviewed"
        assert calls["route"] == ("c1", "k1", "s1")
        assert calls["trigger"] == "catchup" and calls["identity"] == ("dm", "u1") and calls["callback"] is None
        assert calls["kwargs"] == {"messages_snapshot": window, "review_memory": True, "review_skills": True,
                                   "task_cfg": {"extra_tools": ["plan_add"]}}

    def test_background_review_disabled_skips(self, monkeypatch):
        import agent.background_review as bg
        monkeypatch.setattr(bg, "load_background_review_settings", lambda: (False, {}))
        reviewer = mcr.MemoryCatchupReviewer(_Runner([], {}, _MetaStore()))
        assert reviewer._review_blocking(_entry("k1", "s1", 2.0), [_user("a", 1.0)]) == "up_to_date"
