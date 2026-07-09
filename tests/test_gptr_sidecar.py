import json
from types import SimpleNamespace

import gptr_sidecar as G


def _run_result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_cnki_retriever_maps_opencli_results_and_empty_result(monkeypatch):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        if len(calls) == 1:
            return _run_result(json.dumps({"data": [{"title": "中文文献", "url": "u", "abstract": "a"}]}))
        return _run_result(stderr="EMPTY_RESULT")

    monkeypatch.setattr(G.subprocess, "run", fake_run)
    G.TALLY.clear()

    rows = G.CNKIRetriever("平台数据权力（long）DMA 2024").search(max_results=3)
    empty = G.CNKIRetriever("空结果").search(max_results=3)

    assert rows == [{"title": "中文文献", "href": "u", "body": "a"}]
    assert empty == []
    assert calls[0][:4] == ["opencli", "cnki", "search", "平台数据权力"]
    assert G.TALLY["cnki"] == {"hits": 1, "ok": True}


def test_zsearch_retriever_maps_json_results(monkeypatch):
    def fake_run(cmd, **_kw):
        assert cmd == ["zsearch", "query", "平台治理", "-k", "2", "--json"]
        return _run_result(json.dumps([{"title": "本地论文", "url": "z", "abstract": "片段"}]))

    monkeypatch.setattr(G.subprocess, "run", fake_run)
    G.TALLY.clear()

    rows = G.ZsearchRetriever("平台治理").search(max_results=2)

    assert rows == [{"title": "本地论文", "href": "z", "body": "片段"}]
    assert G.TALLY["zsearch"] == {"hits": 1, "ok": True}


def test_env_setup_pins_multisource_budget(monkeypatch):
    for name in ("RETRIEVER", "OPENAI_BASE_URL", "MAX_SEARCH_RESULTS_PER_QUERY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GPTR_MAX_RESULTS", "7")

    G._env_setup()

    assert G.os.environ["RETRIEVER"] == "tavily,cnki,zsearch"
    assert G.os.environ["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert G.os.environ["MAX_SEARCH_RESULTS_PER_QUERY"] == "7"
