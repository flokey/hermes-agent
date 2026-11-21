"""DeepSeek search backend and the CJK/other language router."""
from __future__ import annotations

import httpx
import pytest

from plugins.web.deepseek.provider import DeepSeekWebSearchProvider
from plugins.web.lang_router import provider as router
from plugins.web.lang_router.provider import LangRouterWebSearchProvider


class _Fake:
    def __init__(self, name, rows=None, error=None):
        self.name, self.rows, self.error, self.queries = name, rows or [], error, []

    def supports_search(self):
        return True

    def is_available(self):
        return True

    def search(self, query, limit=5):
        self.queries.append(query)
        if self.error:
            return {"success": False, "error": self.error}
        return {"success": True, "data": {"web": [
            {"title": t, "url": u, "description": "", "position": i + 1} for i, (t, u) in enumerate(self.rows)]}}


@pytest.fixture
def backends(monkeypatch):
    fakes = {"deepseek": _Fake("deepseek", [("zh", "https://example.cn/a")]),
             "brave-free": _Fake("brave-free", [("en", "https://example.com/b")])}
    monkeypatch.setattr("agent.web_search_registry.get_provider", lambda name: fakes.get(name))
    monkeypatch.setattr(router, "_route_config", lambda: {"cjk": "deepseek", "other": "brave-free"})
    return fakes


def _urls(resp):
    assert resp["success"], resp
    return [r["url"] for r in resp["data"]["web"]]


def test_queries_route_by_script(backends):
    r = LangRouterWebSearchProvider()
    assert _urls(r.search("咖啡 推荐")) == ["https://example.cn/a"]
    assert _urls(r.search("best espresso grinder 2030")) == ["https://example.com/b"]
    assert backends["deepseek"].queries == ["咖啡 推荐"]
    assert backends["brave-free"].queries == ["best espresso grinder 2030"]


def test_failed_or_empty_backend_falls_back_to_the_other(backends):
    backends["deepseek"].error = "DeepSeek search HTTP 503"
    assert _urls(LangRouterWebSearchProvider().search("天气 预报")) == ["https://example.com/b"]
    backends["brave-free"].rows = []
    backends["deepseek"].error = None
    assert _urls(LangRouterWebSearchProvider().search("weather forecast")) == ["https://example.cn/a"]


def test_double_pipe_queries_are_split_routed_and_merged(backends):
    backends["deepseek"].rows = [("zh", "https://example.cn/a"), ("dup", "https://example.com/b")]
    resp = LangRouterWebSearchProvider().search("面条 做法 || cheap flights || 面条 做法 ||  ")
    assert backends["deepseek"].queries == ["面条 做法"]
    assert backends["brave-free"].queries == ["cheap flights"]
    assert _urls(resp) == ["https://example.cn/a", "https://example.com/b"]
    assert [r["position"] for r in resp["data"]["web"]] == [1, 2]


def test_both_backends_failing_reports_both_errors(backends):
    backends["deepseek"].error, backends["brave-free"].error = "boom-a", "boom-b"
    resp = LangRouterWebSearchProvider().search("天文 新闻")
    assert not resp["success"] and "boom-a" in resp["error"] and "boom-b" in resp["error"]


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self):
        return self._data


def test_deepseek_search_maps_summary_date_and_dedups(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    sent = {}

    def fake_post(url, json, timeout, headers):
        sent.update(url=url, json=json)
        return _Resp(200, {"results": [
            {"title": "A", "url": "https://example.com/a", "snippet": "short", "summary": "long summary", "date": "2030-01-01"},
            {"title": "A again", "url": "https://example.com/a", "summary": "dup"},
            {"title": "B", "url": "https://example.com/b", "snippet": "only snippet"},
        ]})

    monkeypatch.setattr(httpx, "post", fake_post)
    resp = DeepSeekWebSearchProvider().search("x" * 300, limit=5)
    assert sent["url"].endswith("/v1/tools/search")
    assert len(sent["json"]["query"]) == 128 and sent["json"]["count"] == 5
    assert resp["data"]["web"] == [
        {"title": "A", "url": "https://example.com/a", "description": "2030-01-01 · long summary", "position": 1},
        {"title": "B", "url": "https://example.com/b", "description": "only snippet", "position": 2},
    ]


def test_deepseek_errors_are_returned_not_raised(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert "DEEPSEEK_API_KEY" in DeepSeekWebSearchProvider().search("q")["error"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(400, {"error": "query too long"}))
    resp = DeepSeekWebSearchProvider().search("q")
    assert not resp["success"] and "HTTP 400" in resp["error"]


def test_perplexity_is_asked_for_its_managed_route_without_a_key(monkeypatch):
    # With lang-router as the stored selection, Perplexity's own check would never see "nous"
    # and refuse for lack of a key; the router decides the managed route instead.
    calls = []

    class _Pplx(_Fake):
        def search_routed(self, query, limit, *, managed):
            calls.append(managed)
            return self.search(query, limit)

    fakes = {"perplexity": _Pplx("perplexity", [("en", "https://example.com/p")]), "deepseek": _Fake("deepseek")}
    monkeypatch.setattr("agent.web_search_registry.get_provider", lambda name: fakes.get(name))
    monkeypatch.setattr(router, "_route_config", lambda: {"cjk": "deepseek", "other": "perplexity"})
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    assert _urls(LangRouterWebSearchProvider().search("news today")) == ["https://example.com/p"]
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    LangRouterWebSearchProvider().search("news tomorrow")
    assert calls == [True, False]
