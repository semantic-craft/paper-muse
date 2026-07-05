import os

import pytest

from knowledge_storm.rm import JinaFullTextRM, MixedRM, PerplexitySearchRM

needs_pplx = pytest.mark.skipif(
    not os.environ.get("PERPLEXITY_API_KEY"), reason="需要 PERPLEXITY_API_KEY"
)


@needs_pplx
def test_perplexity_rm_returns_storm_shaped_results():
    rm = PerplexitySearchRM(k=3)
    results = rm.forward("生成式人工智能 平台责任")
    assert results, "应至少返回一条结果"
    for r in results:
        assert {"description", "snippets", "title", "url"} <= set(r)
        assert isinstance(r["snippets"], list) and r["snippets"][0]
    assert rm.get_usage_and_reset() == {"PerplexitySearchRM": 1}


@needs_pplx
def test_perplexity_rm_skips_blank_query():
    rm = PerplexitySearchRM(k=2)
    assert rm.forward(["", "  "]) == []


needs_jina = pytest.mark.skipif(
    not (os.environ.get("JINA_API_KEY") and os.environ.get("PERPLEXITY_API_KEY")),
    reason="需要 JINA_API_KEY + PERPLEXITY_API_KEY",
)


@needs_jina
def test_jina_fulltext_enriches_top_results():
    base = PerplexitySearchRM(k=2)
    rm = JinaFullTextRM(base_rm=base, top_n=1, max_tokens=1500)
    results = rm.forward("欧盟人工智能法案 高风险系统 义务")
    assert results
    top = results[0]
    # 全文增强后 top1 的 snippets 应明显厚于一条搜索摘要
    assert sum(len(s) for s in top["snippets"]) > 600
    usage = rm.get_usage_and_reset()
    assert "JinaFullTextRM" in usage and "PerplexitySearchRM" in usage


def test_jina_fulltext_preserves_snippets_on_fetch_failure(monkeypatch):
    class _OneResultRM:
        k = 1

        def get_usage_and_reset(self):
            return {"_OneResultRM": 1}

        def forward(self, query_or_queries, exclude_urls=[]):
            return [
                {
                    "description": "原始摘要",
                    "snippets": ["原始摘要"],
                    "title": "t",
                    "url": "https://example.com/x",
                }
            ]

    import knowledge_storm.rm as rm_mod

    def _boom(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr(rm_mod.requests, "get", _boom)
    rm = rm_mod.JinaFullTextRM(base_rm=_OneResultRM(), top_n=1, jina_api_key="test-key")
    results = rm.forward("任意查询")
    assert results[0]["snippets"] == ["原始摘要"]
    usage = rm.get_usage_and_reset()
    assert usage["JinaFullTextRM"] == 0


class _StubRM:
    def __init__(self, name, urls):
        self.k = len(urls)
        self._name = name
        self._urls = urls

    def get_usage_and_reset(self):
        return {self._name: 1}

    def forward(self, query_or_queries, exclude_urls=[]):
        return [
            {"description": u, "snippets": [u], "title": u, "url": u}
            for u in self._urls
        ]


def test_mixed_rm_interleaves_and_dedups():
    a = _StubRM("A", ["u1", "u2", "u3"])
    b = _StubRM("B", ["u2", "u4"])
    rm = MixedRM([a, b])
    urls = [r["url"] for r in rm.forward("任意查询")]
    # 逐位交错：i=0 取 a=u1, b=u2；i=1 取 a=u2(重复丢弃), b=u4；i=2 取 a=u3
    assert urls == ["u1", "u2", "u4", "u3"]
    assert rm.get_usage_and_reset() == {"A": 1, "B": 1}
