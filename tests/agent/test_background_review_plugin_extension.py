"""Plugin extension points of the background review: prompt sections and deferred extra tools."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent.background_review as bg
from hermes_cli import plugins_review_sections as prs


class _Manager:
    pass


class _Ctx:
    """The slice of PluginContext the registration body uses."""

    def __init__(self, manager, plugin_id="example"):
        self._manager = manager
        self.plugin_id = plugin_id

    def _register_entry(self, kind, key, mapping, entry, log_fmt, *args, previous=None):
        mapping[key] = entry
        return SimpleNamespace(release=lambda: mapping.pop(key, None))


@pytest.fixture
def manager(monkeypatch):
    mgr = _Manager()
    from hermes_cli import plugins
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: mgr)
    return mgr


def _agent(**extra):
    base = dict(session_id="s1", platform="discord", _chat_type="dm", _user_id="u1",
                _MEMORY_REVIEW_PROMPT="review memory", _SKILL_REVIEW_PROMPT="review skills",
                _COMBINED_REVIEW_PROMPT="review both")
    return SimpleNamespace(**{**base, **extra})


class TestReviewPromptSections:
    def test_section_is_appended_to_memory_reviews(self, manager):
        prs.register_review_prompt_section(_Ctx(manager), "tasks", "Dated plans go to plan_add, not memory.")
        _target, prompt = bg.spawn_background_review_thread(_agent(), [], review_memory=True, task_cfg={})
        assert prompt.startswith("review memory")
        assert "## Plugin review instructions: tasks\n\nDated plans go to plan_add, not memory." in prompt

    def test_memory_scope_skips_skill_only_reviews(self, manager):
        prs.register_review_prompt_section(_Ctx(manager), "tasks", "only for memory")
        prs.register_review_prompt_section(_Ctx(manager, "other"), "always", "for every review", scope="all")
        _target, prompt = bg.spawn_background_review_thread(_agent(), [], review_skills=True, task_cfg={})
        assert "only for memory" not in prompt and "for every review" in prompt

    def test_callable_gets_review_info_and_can_opt_out(self, manager):
        seen = []

        def section(info):
            seen.append(dict(info))
            return "" if info["chat_type"] != "dm" else "owner chat rules"
        prs.register_review_prompt_section(_Ctx(manager), "tasks", section)
        _t, prompt = bg.spawn_background_review_thread(_agent(), [], review_memory=True, task_cfg={})
        assert "owner chat rules" in prompt
        assert seen[0] == {"session_id": "s1", "platform": "discord", "chat_type": "dm", "user_id": "u1",
                           "trigger": "nudge", "review_memory": True, "review_skills": False}
        _t, prompt = bg.spawn_background_review_thread(_agent(_chat_type="group"), [], review_memory=True,
                                                       task_cfg={})
        assert "Plugin review instructions" not in prompt

    def test_trigger_follows_the_caller(self, manager):
        seen = []
        prs.register_review_prompt_section(_Ctx(manager), "tasks", lambda info: seen.append(info["trigger"]) or "x")
        bg.spawn_background_review_thread(_agent(_review_trigger="catchup"), [], review_memory=True, task_cfg={})
        bg.spawn_background_review_thread(_agent(), [], review_memory=True, task_cfg={}, explicit=True)
        assert seen == ["catchup", "refine"]

    def test_chat_identity_falls_back_to_the_session_row(self, manager):
        seen = []
        prs.register_review_prompt_section(_Ctx(manager), "tasks", lambda info: seen.append(info) or "x")
        db = SimpleNamespace(get_session=lambda sid: {"chat_type": "group", "user_id": "u9"})
        bg.spawn_background_review_thread(_agent(_chat_type=None, _user_id=None, _session_db=db), [],
                                          review_memory=True, task_cfg={})
        assert (seen[0]["chat_type"], seen[0]["user_id"]) == ("group", "u9")

    def test_raising_or_oversized_section_is_skipped(self, manager, caplog):
        def boom(info):
            raise RuntimeError("broken plugin")
        prs.register_review_prompt_section(_Ctx(manager), "boom", boom)
        prs.register_review_prompt_section(_Ctx(manager, "big"), "big", "x" * 50, max_chars=10)
        prs.register_review_prompt_section(_Ctx(manager, "ok"), "ok", "fine")
        _t, prompt = bg.spawn_background_review_thread(_agent(), [], review_memory=True, task_cfg={})
        assert "fine" in prompt and "broken plugin" not in prompt and "xxxxxxxxxxx" not in prompt
        assert "raised and was skipped" in caplog.text and "exceeded max_chars" in caplog.text

    def test_registration_validates_its_arguments(self, manager):
        ctx = _Ctx(manager)
        with pytest.raises(ValueError):
            prs.register_review_prompt_section(ctx, "Bad Id", "x")
        with pytest.raises(ValueError):
            prs.register_review_prompt_section(ctx, "ok", "x", scope="skills")
        with pytest.raises(TypeError):
            prs.register_review_prompt_section(ctx, "ok", 42)
        prs.register_review_prompt_section(ctx, "ok", "x")
        with pytest.raises(ValueError, match="already registered"):
            prs.register_review_prompt_section(_Ctx(manager, "other"), "ok", "y")

    def test_plugin_context_exposes_the_hook(self):
        from hermes_cli.plugins import PluginContext
        assert callable(getattr(PluginContext, "register_review_prompt_section", None))


def _review_agent(advertised):
    return SimpleNamespace(_memory_enabled=True, _user_profile_enabled=False, valid_tool_names=set(advertised))


class TestDeferredExtraTools:
    def test_listed_tool_is_admitted_and_tool_describe_opens_when_it_is_deferred(self):
        whitelist, extra = bg._review_tool_whitelist(
            _review_agent({"memory", "tool_search", "tool_describe", "tool_call"}),
            {"extra_tools": ["plan_add"]}, review_memory=True)
        assert extra == {"plan_add"} and {"plan_add", "tool_describe"} <= whitelist
        assert "tool_search" not in whitelist and "tool_call" not in whitelist

    def test_directly_advertised_tool_needs_no_bridge(self):
        whitelist, _ = bg._review_tool_whitelist(
            _review_agent({"memory", "plan_add", "tool_call"}), {"extra_tools": ["plan_add"]}, review_memory=True)
        assert "plan_add" in whitelist and "tool_describe" not in whitelist

    def test_no_bridge_no_tool_describe(self):
        whitelist, _ = bg._review_tool_whitelist(
            _review_agent({"memory"}), {"extra_tools": ["plan_add"]}, review_memory=True)
        assert "tool_describe" not in whitelist


@pytest.fixture
def deferred_tool():
    from tools.registry import registry
    name = "mcp__pytestreview__plan_add"
    registry.register(
        name=name, toolset="mcp-pytestreview",
        schema={"name": name, "description": "Add a dated plan.",
                "parameters": {"type": "object", "properties": {"title": {"type": "string"}},
                               "required": ["title"]}},
        handler=lambda args, **kw: json.dumps({"ok": True}))
    yield name
    registry.deregister(name)


class _ScopedAgent:
    enabled_toolsets = None
    disabled_toolsets = None


class TestDeferredToolThroughTheBridge:
    """The executor unwraps ``tool_call`` before the review whitelist is checked, so a deferred
    tool admitted by name runs, and a non-admitted deferred tool is still denied."""

    def _directive(self, whitelist, name, args):
        from agent.tool_executor import _unwrap_tool_search_call
        from hermes_cli.plugins import (
            _get_pre_tool_call_directive_details, clear_thread_tool_whitelist, set_thread_tool_whitelist)
        underlying, underlying_args, block = _unwrap_tool_search_call(
            _ScopedAgent(), "tool_call", {"name": name, "arguments": args})
        assert block is None
        set_thread_tool_whitelist(whitelist, deny_msg_fmt="denied {tool_name}")
        try:
            return underlying, _get_pre_tool_call_directive_details(underlying, underlying_args)
        finally:
            clear_thread_tool_whitelist()

    def test_admitted_deferred_tool_passes_the_whitelist(self, deferred_tool):
        name, directive = self._directive({"memory", deferred_tool}, deferred_tool, {"title": "dentist"})
        assert name == deferred_tool and directive.action != "block"

    def test_not_admitted_deferred_tool_is_denied(self, deferred_tool):
        name, directive = self._directive({"memory"}, deferred_tool, {"title": "dentist"})
        assert name == deferred_tool
        assert directive.action == "block" and directive.message == f"denied {deferred_tool}"


class TestRealPluginContext:
    def test_register_through_plugin_context_and_dispose(self):
        from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
        manager = PluginManager()
        ctx = PluginContext(PluginManifest(name="example-plugin", key="example-plugin", source="user"), manager)
        handle = ctx.register_review_prompt_section("example.tasks", "route dated plans to the task tool")
        info = {"review_memory": True}
        assert "route dated plans" in prs.render_review_prompt_sections(manager, info)
        handle.dispose()
        assert prs.render_review_prompt_sections(manager, info) == ""
