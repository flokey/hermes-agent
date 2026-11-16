"""A reviewer dispatched from a cold parent (catch-up review, cold ``/refine``) reads and writes the
built-in MEMORY.md/USER.md store, like one dispatched from a resident agent."""

from __future__ import annotations

import json

import pytest

from agent.system_prompt import build_system_prompt
from gateway.session_cold_review import _bind_builtin_memory
from run_agent import AIAgent


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    monkeypatch.setattr("tools.memory_tool_store.get_memory_dir", lambda: tmp_path, raising=False)
    (tmp_path / "MEMORY.md").write_text("The build server is reached through the bastion host.\n")
    (tmp_path / "USER.md").write_text("Prefers metric units.\n")
    return tmp_path


def _cold_parent(**overrides):
    # As cold_review_parent builds it: a platform's composite toolset and skip_memory=True.
    kwargs = dict(model="test-model", base_url="http://127.0.0.1:9/v1", api_key="test-key", quiet_mode=True,
                  enabled_toolsets=["hermes-discord"], skip_memory=True, platform="discord",
                  skip_context_files=True)
    kwargs.update(overrides)
    return AIAgent(**kwargs)


def test_cold_parent_gets_the_builtin_store_and_its_prompt_shows_current_memory(memory_dir):
    agent = _cold_parent()
    assert "memory" in agent.valid_tool_names
    assert agent._memory_store is None  # the gap: skip_memory dropped the built-in store too

    _bind_builtin_memory(agent, {})

    assert agent._memory_store is not None
    prompt = build_system_prompt(agent)
    assert "bastion host" in prompt and "Prefers metric units" in prompt


def test_reviewer_memory_tool_writes_through_the_bound_store(memory_dir):
    from tools.memory_tool import memory_tool
    agent = _cold_parent()
    _bind_builtin_memory(agent, {})
    result = json.loads(memory_tool(action="add", target="user", content="Works night shifts.",
                                    store=agent._memory_store))
    assert result.get("success") is True, result
    assert "Works night shifts." in (memory_dir / "USER.md").read_text()


def test_disabled_stores_stay_unbound(memory_dir):
    agent = _cold_parent()
    _bind_builtin_memory(agent, {"memory": {"memory_enabled": False, "user_profile_enabled": False}})
    assert agent._memory_store is None


def test_no_store_without_the_memory_tool(memory_dir):
    agent = _cold_parent(disabled_toolsets=["memory"])
    assert "memory" not in agent.valid_tool_names
    _bind_builtin_memory(agent, {})
    assert agent._memory_store is None
