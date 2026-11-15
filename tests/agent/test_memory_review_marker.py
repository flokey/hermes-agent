"""The reviewed-up-to marker shared by the turn-nudge review and the gateway catch-up review."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import memory_review_marker as mrm


class _MetaStore:
    def __init__(self):
        self.rows = {}

    def get_meta(self, key):
        return self.rows.get(key)

    def set_meta(self, key, value):
        self.rows[key] = value


def _user(text, ts=None, **extra):
    msg = {"role": "user", "content": text, **extra}
    if ts is not None:
        msg["timestamp"] = ts
    return msg


class TestReviewableUserTurn:
    def test_typed_message_counts(self):
        assert mrm.is_reviewable_user_turn(_user("remind me to call the plumber"))

    @pytest.mark.parametrize("kind", ["internal_notification", "process_complete", "async_delegation_complete",
                                      "hidden"])
    def test_injected_notices_do_not_count(self, kind):
        assert not mrm.is_reviewable_user_turn(_user("[background process finished]", display_kind=kind))

    def test_synthetic_scaffolding_does_not_count(self):
        assert not mrm.is_reviewable_user_turn(_user("continue", _todo_snapshot_synthetic=True))

    def test_out_of_band_block_does_not_count(self):
        from agent.conversation_compression import _steer_markers
        opener, closer = _steer_markers()
        assert not mrm.is_reviewable_user_turn(_user(f"{opener}]\nalso this\n{closer}"))

    def test_assistant_and_blank_do_not_count(self):
        assert not mrm.is_reviewable_user_turn({"role": "assistant", "content": "hi"})
        assert not mrm.is_reviewable_user_turn(_user("   "))


class TestUnreviewedTurns:
    def test_without_marker_every_user_turn_is_unreviewed(self):
        msgs = [_user("a", 1.0), {"role": "assistant", "content": "x", "timestamp": 2.0}, _user("b", 3.0)]
        assert mrm.unreviewed_user_turns(msgs, None) == [0, 2]

    def test_marker_hides_older_turns(self):
        msgs = [_user("a", 1.0), _user("b", 3.0), _user("c", 5.0)]
        assert mrm.unreviewed_user_turns(msgs, 3.0) == [2]

    def test_notice_after_marker_is_not_a_new_turn(self):
        msgs = [_user("a", 1.0), _user("done", 9.0, display_kind="process_complete")]
        assert mrm.unreviewed_user_turns(msgs, 1.0) == []


class TestMarkerStore:
    def test_marker_only_moves_forward(self):
        store = _MetaStore()
        assert mrm.advance_marker(store, "agent:main:discord:dm:1", 10.0, trigger="nudge")
        assert not mrm.advance_marker(store, "agent:main:discord:dm:1", 5.0, trigger="catchup")
        assert mrm.read_marker(store, "agent:main:discord:dm:1") == 10.0

    def test_unwraps_gateway_db_wrapper(self):
        store = _MetaStore()
        wrapper = SimpleNamespace(_db=store)
        mrm.advance_marker(wrapper, "k", 4.0, trigger="nudge")
        assert mrm.read_marker(wrapper, "k") == 4.0

    def test_garbage_marker_reads_as_none(self):
        store = _MetaStore()
        store.rows[mrm.MARKER_PREFIX + "k"] = "not json"
        assert mrm.read_marker(store, "k") is None

    def test_real_session_db_round_trip(self, tmp_path):
        from hermes_state import SessionDB
        db = SessionDB(db_path=tmp_path / "state.db")
        assert mrm.advance_marker(db, "k", 12.5, trigger="catchup")
        assert mrm.read_marker(db, "k") == 12.5


class TestRecordFinishedReview:
    def test_uses_newest_user_turn_timestamp(self):
        store = _MetaStore()
        agent = SimpleNamespace(_gateway_session_key="k", _session_db=store)
        mrm.record_finished_review(agent, [_user("a", 1.0), _user("b", 7.0)], fallback_ts=99.0, trigger="nudge")
        assert mrm.read_marker(store, "k") == 7.0

    def test_live_snapshot_without_timestamps_uses_review_start(self):
        store = _MetaStore()
        agent = SimpleNamespace(_gateway_session_key="k", _session_db=store)
        mrm.record_finished_review(agent, [_user("a")], fallback_ts=42.0, trigger="nudge")
        assert mrm.read_marker(store, "k") == 42.0

    def test_no_gateway_session_records_nothing(self):
        store = _MetaStore()
        mrm.record_finished_review(SimpleNamespace(_session_db=store), [_user("a", 1.0)], 2.0, "nudge")
        assert store.rows == {}

    def test_store_failure_is_logged_not_raised(self, caplog):
        class _Broken(_MetaStore):
            def set_meta(self, key, value):
                raise OSError("disk full")

        agent = SimpleNamespace(_gateway_session_key="k", _session_db=_Broken())
        mrm.record_finished_review(agent, [_user("a", 1.0)], 2.0, "nudge")
        assert "Could not record the memory review marker" in caplog.text


class TestNudgeReviewAdvancesMarker:
    """``_run_review_in_thread`` records the marker only for a memory review that was not cancelled."""

    def _run(self, monkeypatch, *, review_memory=True, cancel_mid_review=False):
        import agent.background_review as bg
        store = _MetaStore()
        agent = SimpleNamespace(_gateway_session_key="k", _session_db=store, memory_notifications="on",
                                background_review_callback=None, _safe_print=lambda *a: None,
                                _emit_auxiliary_failure=lambda *a: None)
        run = bg._BackgroundReviewRun()

        def fake_fork(*args, **kwargs):
            if cancel_mid_review:
                run.cancel()  # a live turn arrived while the review was running
        monkeypatch.setattr(bg, "_parent_can_emit_tool_calls", lambda a: True)
        monkeypatch.setattr(bg, "_run_review_fork", fake_fork)
        bg._run_review_in_thread(agent, [_user("a", 3.0)], "prompt", task_cfg={}, review_run=run,
                                 review_memory=review_memory)
        return store

    def test_finished_memory_review_moves_the_marker(self, monkeypatch):
        assert mrm.read_marker(self._run(monkeypatch), "k") == 3.0

    def test_skill_only_review_leaves_it(self, monkeypatch):
        assert mrm.read_marker(self._run(monkeypatch, review_memory=False), "k") is None

    def test_cancelled_review_leaves_the_turns_for_the_catch_up(self, monkeypatch):
        assert mrm.read_marker(self._run(monkeypatch, cancel_mid_review=True), "k") is None
