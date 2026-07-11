from evidence import (
    CorpusAnswer,
    EvidenceProviderError,
    EvidenceGateway,
    FunctionCorpusProvider,
    FunctionEvidenceProvider,
    ProviderRecord,
    ProviderSearchResult,
)


def test_gateway_returns_corpus_answer_with_actual_evidence_bundle():
    gateway = EvidenceGateway(
        (),
        corpus_providers=(
            FunctionCorpusProvider(
                "paperqa",
                lambda question: CorpusAnswer(
                    answer="撤回同意后，模型影响不会立即消失。",
                    formatted_answer="撤回同意后，模型影响不会立即消失 [1]。",
                    records=(
                        ProviderRecord(
                            source_id="zotero:users:0:attachment:ATT1:page:12",
                            title="Data Lifecycle Study",
                            url="zotero://select/library/items/ITEM1",
                            version="42",
                            source_kind="library-document",
                            relation="supports",
                            identity="zotero:users:0:attachment:ATT1",
                            locator_kind="pdf-page",
                            locator_value="12",
                            exact="withdrawal does not remove learned influence",
                            prefix="the experiment found that ",
                            suffix=" across all model variants",
                            page=12,
                        ),
                    ),
                    provider_version="2026.3.18",
                    index_version="idx-7",
                ),
            ),
        ),
    )

    bundle = gateway.ask_corpus("撤回同意是否真正可逆？")

    assert bundle["answer"] == "撤回同意后，模型影响不会立即消失。"
    assert bundle["formatted_answer"].endswith("[1]。")
    assert bundle["status"] == {
        "provider": "paperqa",
        "state": "ok",
        "query": "撤回同意是否真正可逆？",
        "hits": 1,
        "message": None,
    }
    assert bundle["provider_version"] == "2026.3.18"
    assert bundle["index_version"] == "idx-7"
    ref = bundle["evidence"][0]
    assert ref["relation"] == "supports"
    assert ref["source"]["identity"] == "zotero:users:0:attachment:ATT1"
    assert ref["source"]["version"] == "42"
    assert ref["locator"] == {
        "kind": "pdf-page",
        "value": "12",
        "exact": "withdrawal does not remove learned influence",
        "prefix": "the experiment found that ",
        "suffix": " across all model variants",
        "page": 12,
        "source_identity": "zotero:users:0:attachment:ATT1",
        "source_version": "42",
    }
    assert ref["retrieval"] == {
        "provider": "paperqa",
        "query": "撤回同意是否真正可逆？",
        "source_id": "zotero:users:0:attachment:ATT1:page:12",
        "provider_version": "2026.3.18",
        "index_version": "idx-7",
    }


def test_corpus_passages_in_same_attachment_have_distinct_evidence_ids():
    def ask(_question):
        return CorpusAnswer(
            answer="two passages",
            records=tuple(
                ProviderRecord(
                    source_id=f"ATT1:{page}",
                    title="Local Paper",
                    url="zotero://select/library/items/ITEM1",
                    version="42",
                    source_kind="library-document",
                    identity="zotero:users:0:attachment:ATT1",
                    locator_kind="pdf-page",
                    locator_value=str(page),
                    exact=exact,
                    page=page,
                )
                for page, exact in ((12, "first passage"), (13, "second passage"))
            ),
        )

    bundle = EvidenceGateway(
        (), corpus_providers=(FunctionCorpusProvider("paperqa", ask),)
    ).ask_corpus("question")

    assert len({ref["id"] for ref in bundle["evidence"]}) == 2
    assert {ref["source"]["identity"] for ref in bundle["evidence"]} == {
        "zotero:users:0:attachment:ATT1"
    }


def test_corpus_bundle_id_changes_when_provider_index_changes():
    def gateway(index_version):
        return EvidenceGateway(
            (),
            corpus_providers=(
                FunctionCorpusProvider(
                    "paperqa",
                    lambda _question: CorpusAnswer(
                        answer="answer",
                        records=(
                            ProviderRecord(
                                source_id="ATT1:12",
                                title="Local Paper",
                                url="zotero://select/library/items/ITEM1",
                                identity="zotero:users:0:attachment:ATT1",
                                locator_kind="pdf-page",
                                locator_value="12",
                                exact="passage",
                                page=12,
                            ),
                        ),
                        provider_version="2026.3.18",
                        index_version=index_version,
                    ),
                ),
            ),
        )

    first = gateway("idx-1").ask_corpus("question")
    second = gateway("idx-2").ask_corpus("question")

    assert first["evidence"][0]["id"] == second["evidence"][0]["id"]
    assert first["id"] != second["id"]


def test_gateway_normalizes_provider_results_into_stable_evidence_refs():
    seen = []

    def search_s2(query, limit):
        seen.append(("semantic_scholar", query, limit))
        return ProviderSearchResult(
            total=47,
            records=(
                ProviderRecord(
                    source_id="CorpusId:123",
                    title="Platform Governance",
                    url="https://doi.org/10.1000/example",
                    version="2026-07-10",
                ),
            ),
        )

    def search_openalex(query, limit):
        seen.append(("openalex", query, limit))
        return ProviderSearchResult(
            total=61,
            records=(
                ProviderRecord(
                    source_id="W123",
                    title="Platform Governance",
                    url="https://doi.org/10.1000/example",
                ),
                ProviderRecord(
                    source_id="W456",
                    title="A Second Study",
                    url="https://openalex.org/W456",
                ),
            ),
        )

    gateway = EvidenceGateway(
        (
            FunctionEvidenceProvider("semantic_scholar", search_s2),
            FunctionEvidenceProvider("openalex", search_openalex),
        )
    )
    first = gateway.search("Has anyone studied platform governance?", limit=3)
    second = gateway.search("A different discovery query", limit=3)

    assert seen[:2] == [
        ("semantic_scholar", "Has anyone studied platform governance?", 3),
        ("openalex", "Has anyone studied platform governance?", 3),
    ]
    assert first["hits"] == 61
    assert len(first["evidence"]) == 2
    assert len(first["results"]) == 2
    ref = first["evidence"][0]
    assert ref["id"].startswith("evr_")
    assert ref["id"] == second["evidence"][0]["id"]
    assert ref["source"] == {
        "kind": "scholarly-work",
        "identity": "https://doi.org/10.1000/example",
        "version": "2026-07-10",
        "title": "Platform Governance",
        "url": "https://doi.org/10.1000/example",
    }
    assert ref["locator"] == {
        "kind": "url",
        "value": "https://doi.org/10.1000/example",
        "exact": "Platform Governance",
    }
    assert ref["retrieval"]["provider"] == "semantic_scholar"
    assert ref["retrieval"]["query"] == "Has anyone studied platform governance?"
    assert ref["relation"] == "discovery"
    assert ref["verification"] == {"status": "provider-retrieved", "degraded": False}


def test_gateway_keeps_working_provider_and_reports_degradation():
    def unavailable(_query, _limit):
        raise RuntimeError("missing key")

    gateway = EvidenceGateway(
        (
            FunctionEvidenceProvider("semantic_scholar", unavailable),
            FunctionEvidenceProvider(
                "openalex",
                lambda _query, _limit: ProviderSearchResult(
                    total=1,
                    records=(
                        ProviderRecord(
                            source_id="W1",
                            title="Available",
                            url="https://openalex.org/W1",
                        ),
                    ),
                ),
            ),
        )
    )

    result = gateway.search("query", limit=3)

    assert result["source"] == "openalex"
    assert result["degraded"] == "semantic_scholar: missing key"
    assert result["evidence"][0]["verification"]["degraded"] is False


def test_gateway_distinguishes_confirmed_empty_from_provider_failure():
    empty = EvidenceGateway(
        (
            FunctionEvidenceProvider(
                "cnki",
                lambda _query, _limit: ProviderSearchResult(total=0, records=()),
            ),
        )
    ).search("平台责任", limit=3)

    def browser_session_missing(_query, _limit):
        raise EvidenceProviderError(
            "authentication-required", "CNKI browser session is missing"
        )

    failed = EvidenceGateway(
        (FunctionEvidenceProvider("cnki", browser_session_missing),)
    ).search("平台责任", limit=3)

    assert empty["status"] == {
        "provider": "cnki",
        "state": "empty",
        "query": "平台责任",
        "hits": 0,
        "message": None,
    }
    assert empty["degraded"] is None
    assert failed["status"] == {
        "provider": "cnki",
        "state": "authentication-required",
        "query": "平台责任",
        "hits": None,
        "message": "CNKI browser session is missing",
    }
    assert failed["degraded"] == "cnki: CNKI browser session is missing"
