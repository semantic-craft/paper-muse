"""Unified evidence contract and provider gateway for Paper Muse retrieval."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Protocol, TypedDict


SourceKind = Literal["scholarly-work", "cnki-record", "library-document"]
LocatorKind = Literal["url", "provider-id", "zotero-select"]
EvidenceRelation = Literal["discovery", "context"]
VerificationStatus = Literal["provider-retrieved", "unresolved", "identity-enriched"]
SearchState = Literal[
    "ok",
    "empty",
    "unavailable",
    "authentication-required",
    "rate-limited",
    "timeout",
    "bad-payload",
    "error",
    "degraded",
    "unknown",
]


class ZoteroNativeIdentity(TypedDict):
    provider: str
    library_type: str
    library_id: int
    library_name: str
    item_key: str
    item_version: int
    item_type: str
    parent_item: str | None
    attachment_key: str | None
    annotation_key: str | None
    collections: list[str]
    tags: list[str]


class EvidenceSourceRequired(TypedDict):
    kind: SourceKind
    identity: str
    version: str
    title: str
    url: str


class EvidenceSource(EvidenceSourceRequired, total=False):
    native: ZoteroNativeIdentity


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


class RetrievalStatus(TypedDict):
    provider: str
    state: SearchState
    query: str
    hits: int | None
    message: str | None


class EvidenceProviderError(RuntimeError):
    """A provider failure whose state is safe to expose through the API."""

    def __init__(self, state: SearchState, message: str):
        super().__init__(message)
        self.state = state


@dataclass(frozen=True)
class ProviderRecord:
    """Provider-neutral scholarly record returned by an evidence adapter."""

    source_id: str
    title: str
    url: str
    version: str = ""
    source_kind: SourceKind = "scholarly-work"
    relation: EvidenceRelation = "discovery"
    verification_status: VerificationStatus = "provider-retrieved"
    identity: str = ""
    locator_kind: LocatorKind | None = None
    locator_value: str = ""
    native: ZoteroNativeIdentity | None = None


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
    if record.identity:
        return record.identity.strip()
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
    locator_value = record.locator_value or record.url or record.source_id
    source = {
        "kind": record.source_kind,
        "identity": identity,
        "version": record.version or "provider-current",
        "title": record.title,
        "url": record.url,
    }
    if record.native:
        source["native"] = record.native
    return {
        "id": _evidence_id(identity),
        "source": source,
        "locator": {
            "kind": record.locator_kind or ("url" if record.url else "provider-id"),
            "value": locator_value,
            "exact": record.title,
        },
        "retrieval": {
            "provider": provider,
            "query": query,
            "source_id": record.source_id,
        },
        "relation": record.relation,
        "verification": {
            "status": record.verification_status,
            "degraded": record.verification_status == "unresolved",
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
        statuses: list[RetrievalStatus] = []

        for provider in self.providers:
            try:
                batch = provider.search(query, limit)
                totals.append(max(0, int(batch.total)))
                sources.append(provider.name)
                statuses.append(
                    {
                        "provider": provider.name,
                        "state": "empty" if batch.total == 0 else "ok",
                        "query": query,
                        "hits": max(0, int(batch.total)),
                        "message": None,
                    }
                )
                for record in batch.records:
                    ref = _evidence_ref(record, provider.name, query)
                    evidence_by_id.setdefault(ref["id"], ref)
            except Exception as exc:
                degraded.append(f"{provider.name}: {exc}")
                statuses.append(
                    {
                        "provider": provider.name,
                        "state": (
                            exc.state
                            if isinstance(exc, EvidenceProviderError)
                            else "error"
                        ),
                        "query": query,
                        "hits": None,
                        "message": str(exc),
                    }
                )

        evidence = list(evidence_by_id.values())[:limit]
        if len(statuses) == 1:
            status = statuses[0]
        else:
            failed = [item for item in statuses if item["state"] not in {"ok", "empty"}]
            status = {
                "provider": "+".join(item["provider"] for item in statuses),
                "state": "degraded" if failed else ("empty" if not evidence else "ok"),
                "query": query,
                "hits": max(totals) if totals else None,
                "message": "; ".join(item["message"] or "" for item in failed) or None,
            }
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
            "status": status,
            "statuses": statuses,
        }
