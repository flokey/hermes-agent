"""deepseek_search middleware: which requests get DeepSeek's server-side web tools."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "plugins" / "deepseek_search" / "__init__.py"
_spec = importlib.util.spec_from_file_location("deepseek_search_under_test", _PATH)
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)

URL = "https://api.deepseek.com"
CLIENT_TOOL = {"type": "function", "function": {"name": "lookup"}}


def _types(result):
    return [t.get("type") for t in result["request"]["tools"]]


def test_thinking_on_gets_both_server_tools():
    req = {"tools": [CLIENT_TOOL], "extra_body": {"thinking": {"type": "enabled"}}, "reasoning_effort": "medium"}
    out = ds._middleware(request=req, base_url=URL, platform="cli")
    assert _types(out) == ["function", "deepseek_web_search", "deepseek_read_url"]


def test_thinking_disabled_in_extra_body_is_left_alone():
    # DeepSeek answers HTTP 400 when client tools sit beside its server tools with thinking off.
    req = {"tools": [CLIENT_TOOL], "extra_body": {"thinking": {"type": "disabled"}}}
    assert ds._middleware(request=req, base_url=URL, platform="cli") is None


def test_top_level_effort_none_counts_as_thinking_off():
    req = {"tools": [CLIENT_TOOL], "reasoning_effort": "none"}
    assert ds._middleware(request=req, base_url=URL, platform="cli") is None


def test_other_hosts_and_cron_are_skipped():
    req = {"tools": [CLIENT_TOOL]}
    assert ds._middleware(request=req, base_url="https://api.example.com", platform="cli") is None
    assert ds._middleware(request=req, base_url=URL, platform="cron") is None


def test_declared_tools_are_not_duplicated():
    req = {"tools": [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}]}
    assert ds._middleware(request=req, base_url=URL, platform="cli") is None


def test_anthropic_mode_declaration_carries_a_name():
    out = ds._middleware(request={"tools": []}, base_url=URL, platform="cli", api_mode="anthropic_messages")
    assert {"type": "deepseek_web_search", "name": "deepseek_web_search"} in out["request"]["tools"]
