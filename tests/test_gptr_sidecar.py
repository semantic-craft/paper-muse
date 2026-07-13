import json
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from urllib.request import urlopen

import gptr_sidecar as G


def _run_result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_cnki_retriever_maps_opencli_results_and_empty_result(monkeypatch):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        if len(calls) == 1:
            return _run_result(
                json.dumps(
                    {
                        "data": [
                            {
                                "title": "中文文献",
                                "url": "u",
                                "abstract": "a",
                                "id": "CJFD:1",
                                "version": "v1",
                            }
                        ]
                    }
                )
            )
        return _run_result(stderr="EMPTY_RESULT")

    monkeypatch.setattr(G.subprocess, "run", fake_run)
    rows = G.CNKIRetriever("平台数据权力（long）DMA 2024").search(max_results=3)
    empty = G.CNKIRetriever("空结果").search(max_results=3)

    assert rows == [
        {
            "title": "中文文献",
            "href": "u",
            "body": "a",
            "source_id": "CJFD:1",
            "version": "v1",
        }
    ]
    assert empty == []
    assert calls[0][:4] == ["opencli", "cnki", "search", "平台数据权力"]


def test_zsearch_retriever_maps_json_results(monkeypatch):
    def fake_run(cmd, **_kw):
        assert cmd == ["zsearch", "query", "平台治理", "-k", "2", "--json"]
        return _run_result(
            json.dumps(
                [
                    {
                        "key": "ITEM1",
                        "title": "本地论文",
                        "url": "zotero://select/library/items/ITEM1",
                        "abstract": "片段",
                        "index_version": "idx-7",
                    }
                ]
            )
        )

    monkeypatch.setattr(G.subprocess, "run", fake_run)

    class UnavailableZotero:
        def enrich(self, records):
            return SimpleNamespace(
                records=records,
                status={"state": "unavailable"},
            )

    rows = G.ZsearchRetriever("平台治理", zotero_adapter=UnavailableZotero()).search(
        max_results=2
    )

    assert rows == [
        {
            "title": "本地论文",
            "href": "zotero://select/library/items/ITEM1",
            "body": "片段",
            "source_id": "ITEM1",
            "version": "",
            "identity": "",
            "verification_status": "unresolved",
            "identity_status": "unavailable",
            "locator": {
                "kind": "zotero-select",
                "value": "zotero://select/library/items/ITEM1",
                "exact": "片段",
            },
            "index_version": "idx-7",
        }
    ]


def test_zsearch_retriever_preserves_real_zotero_attachment_identity(monkeypatch):
    def fake_run(_cmd, **_kw):
        return _run_result(
            json.dumps(
                [
                    {
                        "key": "ITEM1",
                        "title": "本地论文",
                        "url": "zotero://select/library/items/ITEM1",
                        "text": "命中片段",
                    }
                ]
            )
        )

    class EnrichedZotero:
        def enrich(self, records):
            parent = records[0]
            attachment = replace(
                parent,
                source_id="ATT1",
                version="9",
                verification_status="identity-enriched",
                identity="zotero:user:0:ATT1",
                locator_kind="zotero-select",
                locator_value="zotero://select/library/items/ATT1",
                native={
                    "provider": "zotero-local",
                    "library_type": "user",
                    "library_id": 0,
                    "library_name": "",
                    "item_key": "ATT1",
                    "item_version": 9,
                    "item_type": "attachment",
                    "parent_item": "ITEM1",
                    "attachment_key": "ATT1",
                    "annotation_key": None,
                    "collections": [],
                    "tags": [],
                },
            )
            return SimpleNamespace(
                records=(parent, attachment),
                status={"state": "ok"},
            )

    monkeypatch.setattr(G.subprocess, "run", fake_run)
    rows = G.ZsearchRetriever("平台治理", zotero_adapter=EnrichedZotero()).search(
        max_results=2
    )

    assert rows[0]["source_id"] == "ATT1"
    assert rows[0]["identity"] == "zotero:user:0:ATT1"
    assert rows[0]["native"]["attachment_key"] == "ATT1"
    assert rows[0]["locator"] == {
        "kind": "zotero-select",
        "value": "zotero://select/library/items/ATT1",
        "exact": "命中片段",
    }
    assert rows[0]["href"] == "zotero://select/library/items/ITEM1"


def test_zsearch_retriever_does_not_guess_between_multiple_attachments(monkeypatch):
    monkeypatch.setattr(
        G.subprocess,
        "run",
        lambda *_args, **_kwargs: _run_result(
            json.dumps(
                [
                    {
                        "key": "ITEM1",
                        "title": "本地论文",
                        "url": "zotero://select/library/items/ITEM1",
                    }
                ]
            )
        ),
    )

    class AmbiguousZotero:
        def enrich(self, records):
            parent = replace(
                records[0],
                version="7",
                verification_status="identity-enriched",
                identity="zotero:user:0:ITEM1",
                locator_kind="zotero-select",
                locator_value="zotero://select/library/items/ITEM1",
                native={
                    "provider": "zotero-local",
                    "library_type": "user",
                    "library_id": 0,
                    "library_name": "",
                    "item_key": "ITEM1",
                    "item_version": 7,
                    "item_type": "journalArticle",
                    "parent_item": None,
                    "attachment_key": None,
                    "annotation_key": None,
                    "collections": [],
                    "tags": [],
                },
            )

            def attachment(key):
                return replace(
                    records[0],
                    source_id=key,
                    identity=f"zotero:user:0:{key}",
                    verification_status="identity-enriched",
                    native={
                        **parent.native,
                        "item_key": key,
                        "item_type": "attachment",
                        "parent_item": "ITEM1",
                        "attachment_key": key,
                    },
                )

            return SimpleNamespace(
                records=(parent, attachment("ATT1"), attachment("ATT2")),
                status={"state": "ok"},
            )

    row = G.ZsearchRetriever("平台治理", zotero_adapter=AmbiguousZotero()).search(
        max_results=2
    )[0]

    assert row["source_id"] == "ITEM1"
    assert row["identity"] == "zotero:user:0:ITEM1"
    assert row["native"]["attachment_key"] is None
    assert row["identity_status"] == "ambiguous"
    assert row["verification_status"] == "unresolved"


def test_env_setup_pins_multisource_budget(monkeypatch):
    for name in ("RETRIEVER", "OPENAI_BASE_URL", "MAX_SEARCH_RESULTS_PER_QUERY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GPTR_MAX_RESULTS", "7")

    G._env_setup("http://127.0.0.1:9999/search")

    assert G.os.environ["RETRIEVER"] == "tavily,custom"
    assert G.os.environ["RETRIEVER_ENDPOINT"] == "http://127.0.0.1:9999/search"
    assert G.os.environ["RETRIEVER_ARG_MAX_RESULTS"] == "7"
    assert G.os.environ["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert G.os.environ["MAX_SEARCH_RESULTS_PER_QUERY"] == "7"


def test_public_custom_endpoint_mixes_cnki_and_zsearch_contracts(monkeypatch):
    monkeypatch.setattr(
        G.CNKIRetriever,
        "search",
        lambda self, max_results=5: [
            {"title": "中文文献", "href": "https://cnki.test/1", "body": "中文摘要"}
        ],
    )
    monkeypatch.setattr(
        G.ZsearchRetriever,
        "search",
        lambda self, max_results=5: [
            {
                "title": "本地论文",
                "href": "zotero://select/library/items/ABC",
                "body": "本地片段",
            }
        ],
    )

    with G.CustomEvidenceEndpoint() as endpoint:
        endpoint.begin_claim()
        with urlopen(
            endpoint.url + "?" + urlencode({"query": "平台治理", "max_results": 3}),
            timeout=2,
        ) as response:
            rows = json.load(response)

    assert rows == [
        {
            "url": "https://cnki.test/1",
            "raw_content": "中文摘要",
            "title": "中文文献",
            "provider": "cnki",
            "source_id": "https://cnki.test/1",
        },
        {
            "url": "zotero://select/library/items/ABC",
            "raw_content": "本地片段",
            "title": "本地论文",
            "provider": "zsearch",
            "source_id": "zotero://select/library/items/ABC",
        },
    ]
    assert endpoint.snapshot()["by_retriever"] == {
        "cnki": {"hits": 1, "ok": True},
        "zsearch": {"hits": 1, "ok": True},
    }


def test_custom_endpoint_keeps_working_provider_and_reports_other_as_degraded(
    monkeypatch,
):
    monkeypatch.setattr(
        G.CNKIRetriever,
        "search",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("CNKI session missing")),
    )
    monkeypatch.setattr(
        G.ZsearchRetriever,
        "search",
        lambda *_a, **_kw: [
            {
                "title": "本地论文",
                "href": "zotero://select/library/items/ABC",
                "body": "片段",
            }
        ],
    )

    with G.CustomEvidenceEndpoint() as endpoint:
        endpoint.begin_claim()
        rows = endpoint.search("平台治理")
        snapshot = endpoint.snapshot()

    assert [row["provider"] for row in rows] == ["zsearch"]
    assert snapshot["by_retriever"] == {
        "cnki": {"hits": 0, "ok": False},
        "zsearch": {"hits": 1, "ok": True},
    }


def test_sidecar_source_has_no_private_gptr_monkey_patch():
    source = Path(G.__file__).read_text(encoding="utf-8")

    assert "_patch_gptr" not in source
    assert "gpt_researcher.retrievers.utils" not in source
    assert "gpt_researcher.actions.retriever" not in source


def test_custom_endpoint_resets_cache_counts_and_provenance_between_claims(monkeypatch):
    calls = []

    def cnki(self, max_results=5):
        calls.append(("cnki", self.query))
        return [
            {
                "title": self.query,
                "href": f"https://cnki.test/{self.query}",
                "body": "c",
            }
        ]

    def zsearch(self, max_results=5):
        calls.append(("zsearch", self.query))
        return []

    monkeypatch.setattr(G.CNKIRetriever, "search", cnki)
    monkeypatch.setattr(G.ZsearchRetriever, "search", zsearch)

    with G.CustomEvidenceEndpoint() as endpoint:
        endpoint.begin_claim()
        first = endpoint.search("claim-one")
        assert endpoint.search("claim-one") == first
        assert calls == [("cnki", "claim-one"), ("zsearch", "claim-one")]
        assert (
            endpoint.provenance_for("https://cnki.test/claim-one")["provider"] == "cnki"
        )

        endpoint.begin_claim()
        assert endpoint.snapshot()["by_retriever"] == {}
        assert endpoint.provenance_for("https://cnki.test/claim-one") is None
        second = endpoint.search("claim-two")

    assert second[0]["source_id"] == "https://cnki.test/claim-two"
    assert calls[-2:] == [("cnki", "claim-two"), ("zsearch", "claim-two")]


def test_batch_uses_independent_endpoints_concurrently(monkeypatch):
    endpoints = []
    active = 0
    max_active = 0

    async def fake_run_one(payload, endpoint):
        nonlocal active, max_active
        endpoints.append(id(endpoint))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {"ok": True, "sources": [], "memo": payload["claim"]}

    monkeypatch.setattr(G, "_run_one", fake_run_one)

    result = asyncio.run(
        G.run(
            {
                "claims": [
                    {"id": 1, "claim": "claim-one", "failures": []},
                    {"id": 2, "claim": "claim-two", "failures": []},
                ]
            }
        )
    )

    assert len(set(endpoints)) == 2
    assert max_active == 2
    assert [item["id"] for item in result["claims"]] == [1, 2]
    assert [item["memo"] for item in result["claims"]] == ["claim-one", "claim-two"]


def test_batch_isolates_tally_cache_and_provenance_per_claim(monkeypatch):
    calls = []

    def cnki(self, max_results=5):
        calls.append(("cnki", self.query))
        if self.query == "claim-one":
            return [{"title": "c1", "href": "https://cnki.test/claim-one", "body": "c1"}]
        return []

    def zsearch(self, max_results=5):
        calls.append(("zsearch", self.query))
        if self.query == "claim-two":
            return [
                {"title": "z1", "href": "https://zotero.test/claim-two/1", "body": "z1"},
                {"title": "z2", "href": "https://zotero.test/claim-two/2", "body": "z2"},
            ]
        return []

    monkeypatch.setattr(G.CNKIRetriever, "search", cnki)
    monkeypatch.setattr(G.ZsearchRetriever, "search", zsearch)
    searches_done = asyncio.Event()
    finished = 0
    endpoints = {}

    async def fake_run_one(payload, endpoint):
        nonlocal finished
        claim = payload["claim"]
        endpoint.begin_claim()
        first = endpoint.search(claim)
        assert endpoint.search(claim) == first
        endpoints[claim] = endpoint
        finished += 1
        if finished == 2:
            searches_done.set()
        await searches_done.wait()
        web_hits = 1 if claim == "claim-one" else 2
        provider_status = G.summarize_provider_statuses(
            [{"provider": "tavily"} for _ in range(web_hits)], endpoint
        )
        return {
            "ok": True,
            "sources": first,
            "memo": claim,
            "en_hits": provider_status["en_hits"],
            "zh_hits": provider_status["zh_hits"],
            "by_retriever": provider_status["by_retriever"],
        }

    monkeypatch.setattr(G, "_run_one", fake_run_one)
    result = asyncio.run(
        G.run(
            {
                "claims": [
                    {"id": 1, "claim": "claim-one", "failures": []},
                    {"id": 2, "claim": "claim-two", "failures": []},
                ]
            }
        )
    )

    one, two = result["claims"]
    assert (one["en_hits"], one["zh_hits"]) == (1, 1)
    assert one["by_retriever"]["cnki"] == {"hits": 1, "ok": True}
    assert one["by_retriever"]["zsearch"] == {"hits": 0, "ok": True}
    assert (two["en_hits"], two["zh_hits"]) == (2, 2)
    assert two["by_retriever"]["cnki"] == {"hits": 0, "ok": True}
    assert two["by_retriever"]["zsearch"] == {"hits": 2, "ok": True}
    assert calls.count(("cnki", "claim-one")) == calls.count(("zsearch", "claim-one")) == 1
    assert calls.count(("cnki", "claim-two")) == calls.count(("zsearch", "claim-two")) == 1
    assert endpoints["claim-one"].provenance_for("https://zotero.test/claim-two/1") is None
    assert endpoints["claim-two"].provenance_for("https://cnki.test/claim-one") is None


def test_web_and_custom_sources_keep_independent_provenance_contracts():
    class Endpoint:
        def provenance_for(self, url):
            if url == "https://cnki.test/1":
                return {
                    "title": "中文文献",
                    "provider": "cnki",
                    "source_id": "CJFD:1",
                    "raw_content": "中文摘要",
                }
            return None

    sources = G.normalize_research_sources(
        [
            {"title": "Web Result", "url": "https://web.test/1", "content": "web"},
            {"url": "https://cnki.test/1", "content": "中文摘要"},
        ],
        Endpoint(),
        web_retriever="tavily",
    )

    assert sources[0]["provider"] == "tavily"
    assert sources[0]["source_id"] == "https://web.test/1"
    assert sources[1]["provider"] == "cnki"
    assert sources[1]["source_id"] == "CJFD:1"
    assert [source["url"] for source in sources] == [
        "https://web.test/1",
        "https://cnki.test/1",
    ]


def test_normalized_zsearch_source_keeps_enriched_locator_and_native_identity():
    native = {"attachment_key": "ATT1", "parent_item": "ITEM1"}

    class Endpoint:
        def provenance_for(self, _url):
            return {
                "provider": "zsearch",
                "source_id": "ATT1",
                "identity": "zotero:user:0:ATT1",
                "version": "9",
                "native": native,
                "verification_status": "identity-enriched",
                "locator": {
                    "kind": "zotero-select",
                    "value": "zotero://select/library/items/ATT1",
                    "exact": "命中片段",
                },
            }

    source = G.normalize_research_sources(
        [
            {
                "title": "本地论文",
                "url": "zotero://select/library/items/ITEM1",
                "content": "命中片段",
            }
        ],
        Endpoint(),
    )[0]

    assert source["source_id"] == "ATT1"
    assert source["identity"] == "zotero:user:0:ATT1"
    assert source["native"] == native
    assert source["verification_status"] == "identity-enriched"
    assert source["locator"]["value"] == "zotero://select/library/items/ATT1"


def test_missing_web_evidence_is_not_reported_as_healthy_provider():
    class Endpoint:
        def snapshot(self):
            return {
                "by_retriever": {
                    "cnki": {"hits": 1, "ok": True},
                    "zsearch": {"hits": 0, "ok": True},
                }
            }

    summary = G.summarize_provider_statuses(
        [{"provider": "cnki", "url": "https://cnki.test/1"}],
        Endpoint(),
        web_retriever="tavily",
    )

    assert summary["by_retriever"]["tavily"] == {
        "hits": 0,
        "ok": False,
        "state": "unknown",
    }
    assert summary["degraded"] is True
    assert "tavily" in summary["degradation_reason"]
