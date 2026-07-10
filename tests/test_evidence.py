from evidence import (
    EvidenceGateway,
    FunctionEvidenceProvider,
    ProviderRecord,
    ProviderSearchResult,
)


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
