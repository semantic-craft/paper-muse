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


from knowledge_storm.rm import JinaFullTextRM

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
