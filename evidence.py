"""Unified evidence contract and provider gateway for Paper Muse retrieval."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Protocol, TypedDict


SourceKind = Literal["scholarly-work"]
LocatorKind = Literal["url", "provider-id"]
EvidenceRelation = Literal["discovery"]
VerificationStatus = Literal["provider-retrieved"]


class EvidenceSource(TypedDict):
    kind: SourceKind
    identity: str
    version: str
    title: str
    url: str


class EvidenceLocator(TypedDict):
    kind: LocatorKind
    value: str
    exact: str


class RetrievalProvenance(TypedDict):
    provider: str
    query: str
    source_id: str


class EvidenceVerification(TypedDict):
    status: VerificationStatus
    degraded: bool


class EvidenceRef(TypedDict):
    """Serializable evidence identity shared by API, UI, and artifacts."""

    id: str
    source: EvidenceSource
    locator: EvidenceLocator
    retrieval: RetrievalProvenance
    relation: EvidenceRelation
    verification: EvidenceVerification


@dataclass(frozen=True)
class ProviderRecord:
    """Provider-neutral scholarly record returned by an evidence adapter."""

    source_id: str
    title: str
    url: str
    version: str = ""


@dataclass(frozen=True)
class ProviderSearchResult:
    """Provider-neutral search response; total is not truncated to record count."""

    total: int
    records: tuple[ProviderRecord, ...]


class EvidenceProvider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> ProviderSearchResult: ...


class FunctionEvidenceProvider:
    """Small adapter for provider functions and in-memory contract tests."""

    def __init__(
        self,
        name: str,
        search: Callable[[str, int], ProviderSearchResult],
    ):
        self.name = name
        self._search = search

    def search(self, query: str, limit: int) -> ProviderSearchResult:
        return self._search(query, limit)


def _source_identity(record: ProviderRecord, provider: str) -> str:
    if record.url:
        return record.url.strip()
    if record.source_id:
        return f"{provider}:{record.source_id.strip()}"
    return f"title:{record.title.strip().casefold()}"


def _evidence_id(identity: str) -> str:
    digest = hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:24]
    return f"evr_{digest}"


def _evidence_ref(record: ProviderRecord, provider: str, query: str) -> EvidenceRef:
    identity = _source_identity(record, provider)
    locator_value = record.url or record.source_id
    return {
        "id": _evidence_id(identity),
        "source": {
            "kind": "scholarly-work",
            "identity": identity,
            "version": record.version or "provider-current",
            "title": record.title,
            "url": record.url,
        },
        "locator": {
            "kind": "url" if record.url else "provider-id",
            "value": locator_value,
            "exact": record.title,
        },
        "retrieval": {
            "provider": provider,
            "query": query,
            "source_id": record.source_id,
        },
        "relation": "discovery",
        "verification": {
            "status": "provider-retrieved",
            "degraded": False,
        },
    }


class EvidenceGateway:
    """Search providers behind one stable, serializable EvidenceRef contract."""

    def __init__(self, providers: Iterable[EvidenceProvider]):
        self.providers = tuple(providers)

    def search(self, query: str, limit: int) -> dict:
        evidence_by_id = {}
        totals = []
        sources = []
        degraded = []

        for provider in self.providers:
            try:
                batch = provider.search(query, limit)
                totals.append(max(0, int(batch.total)))
                sources.append(provider.name)
                for record in batch.records:
                    ref = _evidence_ref(record, provider.name, query)
                    evidence_by_id.setdefault(ref["id"], ref)
            except Exception as exc:
                degraded.append(f"{provider.name}: {exc}")

        evidence = list(evidence_by_id.values())[:limit]
        return {
            "hits": max(totals) if totals else 0,
            "evidence": evidence,
            "results": [
                {"title": ref["source"]["title"], "url": ref["source"]["url"]}
                for ref in evidence
            ],
            "source": "+".join(sources) if sources else None,
            "degraded": "; ".join(degraded) if degraded else None,
            "query": query,
        }
