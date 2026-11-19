import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("deepseek_search_plugin", Path(__file__).with_name("__init__.py"))
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)

URL = "https://api.deepseek.com/v1"
TOOLS = [{"type": "function", "function": {"name": "example_tool"}}]


def _names(out):
    return [t.get("type") for t in out["request"]["tools"]]


def test_adds_both_web_tools_with_thinking_on():
    out = ds._middleware(request={"tools": TOOLS, "extra_body": {"thinking": {"type": "enabled"}}}, base_url=URL)
    assert _names(out) == ["function", "deepseek_web_search", "deepseek_read_url"]


def test_skips_when_thinking_is_off():
    for req in ({"tools": TOOLS, "extra_body": {"thinking": {"type": "disabled"}}},
                {"tools": TOOLS, "thinking": {"type": "disabled"}},
                {"tools": TOOLS, "reasoning_effort": "none"}):
        assert ds._middleware(request=req, base_url=URL) is None


def test_skips_other_hosts_and_cron():
    assert ds._middleware(request={"tools": TOOLS}, base_url="https://api.example.com/v1") is None
    assert ds._middleware(request={"tools": TOOLS}, base_url=URL, platform="cron") is None


def test_anthropic_messages_declarations_carry_a_name():
    out = ds._middleware(request={"tools": []}, base_url=URL, api_mode="anthropic_messages")
    assert {"type": "deepseek_web_search", "name": "deepseek_web_search"} in out["request"]["tools"]


def test_does_not_add_twice():
    req = {"tools": [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}]}
    assert ds._middleware(request=req, base_url=URL) is None
