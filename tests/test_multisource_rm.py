import os

import pytest

from knowledge_storm.rm import PerplexitySearchRM

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
