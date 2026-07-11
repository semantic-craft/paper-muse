"""Unified evidence contract and provider gateway for Paper Muse retrieval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Protocol, TypedDict

SourceKind = Literal["scholarly-work", "cnki-record", "library-document"]
LocatorKind = Literal["url", "provider-id", "zotero-select", "pdf-page"]
EvidenceRelation = Literal["discovery", "supports", "refutes", "context"]
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


class EvidenceLocatorRequired(TypedDict):
    kind: LocatorKind
    value: str


class EvidenceLocator(EvidenceLocatorRequired, total=False):
    exact: str
    prefix: str
    suffix: str
    page: int
    start: int
    end: int
    source_identity: str
    source_version: str


class RetrievalProvenanceRequired(TypedDict):
    provider: str
    query: str
    source_id: str


class RetrievalProvenance(RetrievalProvenanceRequired, total=False):
    provider_version: str
    index_version: str


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


class EvidenceBundle(TypedDict):
    """Provider-neutral corpus answer plus the exact evidence used for it."""

    id: str
    question: str
    answer: str
    formatted_answer: str
    evidence: list[EvidenceRef]
    status: RetrievalStatus
    provider_version: str
    index_version: str


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
    exact: str = ""
    prefix: str = ""
    suffix: str = ""
    page: int | None = None
    start: int | None = None
    end: int | None = None
    native: ZoteroNativeIdentity | None = None


@dataclass(frozen=True)
class ProviderSearchResult:
    """Provider-neutral search response; total is not truncated to record count."""

    total: int
    records: tuple[ProviderRecord, ...]


@dataclass(frozen=True)
class CorpusAnswer:
    """Provider-neutral answer returned by an isolated corpus adapter."""

    answer: str
    records: tuple[ProviderRecord, ...]
    formatted_answer: str = ""
    provider_version: str = ""
    index_version: str = ""


class CorpusProvider(Protocol):
    name: str

    def ask(self, question: str) -> CorpusAnswer: ...


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


class FunctionCorpusProvider:
    """Small adapter for isolated corpus functions and contract fixtures."""

    def __init__(self, name: str, ask: Callable[[str], CorpusAnswer]):
        self.name = name
        self._ask = ask

    def ask(self, question: str) -> CorpusAnswer:
        return self._ask(question)


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


def _evidence_ref(
    record: ProviderRecord,
    provider: str,
    query: str,
    *,
    provider_version: str = "",
    index_version: str = "",
) -> EvidenceRef:
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
    locator: EvidenceLocator = {
        "kind": record.locator_kind or ("url" if record.url else "provider-id"),
        "value": locator_value,
        "exact": record.exact or record.title,
    }
    for key, value in (
        ("prefix", record.prefix),
        ("suffix", record.suffix),
        ("page", record.page),
        ("start", record.start),
        ("end", record.end),
    ):
        if value not in (None, ""):
            locator[key] = value  # type: ignore[literal-required]
    # 「是否含定位符细节」判据只算一次：既决定 locator.source_identity/version 是否写入，
    # 又参与 evidence_id 派生（下方）。两处必须同源——分开重算若将来漏改一处，会让同一记录
    # 的 id 判定抖动、破坏图重建幂等（#14）。
    has_locator_detail = record.locator_kind == "pdf-page" or any(
        value not in (None, "")
        for value in (record.prefix, record.suffix, record.page, record.start, record.end)
    )
    if has_locator_detail:
        locator["source_identity"] = identity
        if record.version:
            locator["source_version"] = record.version
    retrieval: RetrievalProvenance = {
        "provider": provider,
        "query": query,
        "source_id": record.source_id,
    }
    if provider_version:
        retrieval["provider_version"] = provider_version
    if index_version:
        retrieval["index_version"] = index_version
    evidence_identity = identity
    if has_locator_detail:
        evidence_identity += "\n" + json.dumps(
            locator, ensure_ascii=False, sort_keys=True
        )
    return {
        "id": _evidence_id(evidence_identity),
        "source": source,
        "locator": locator,
        "retrieval": retrieval,
        "relation": record.relation,
        "verification": {
            "status": record.verification_status,
            "degraded": record.verification_status == "unresolved",
        },
    }


def evidence_ref_from_record(
    record: ProviderRecord,
    provider: str,
    query: str,
    *,
    provider_version: str = "",
    index_version: str = "",
) -> EvidenceRef:
    """Public adapter seam for turning provider-neutral records into EvidenceRef."""
    return _evidence_ref(
        record,
        provider,
        query,
        provider_version=provider_version,
        index_version=index_version,
    )


class EvidenceGateway:
    """Search providers behind one stable, serializable EvidenceRef contract."""

    def __init__(
        self,
        providers: Iterable[EvidenceProvider],
        *,
        corpus_providers: Iterable[CorpusProvider] = (),
    ):
        self.providers = tuple(providers)
        self.corpus_providers = tuple(corpus_providers)

    def ask_corpus(self, question: str) -> EvidenceBundle:
        q = " ".join(str(question or "").split())
        if not q:
            raise ValueError("question is required")
        if not self.corpus_providers:
            raise EvidenceProviderError("unavailable", "No corpus provider configured")
        provider = self.corpus_providers[0]
        answer = provider.ask(q)
        evidence = [
            _evidence_ref(
                record,
                provider.name,
                q,
                provider_version=answer.provider_version,
                index_version=answer.index_version,
            )
            for record in answer.records
        ]
        bundle_identity = "\n".join(
            [
                provider.name,
                answer.provider_version,
                answer.index_version,
                q.casefold(),
                *(ref["id"] for ref in evidence),
            ]
        )
        return {
            "id": "evb_"
            + hashlib.sha256(bundle_identity.encode("utf-8")).hexdigest()[:24],
            "question": q,
            "answer": answer.answer,
            "formatted_answer": answer.formatted_answer or answer.answer,
            "evidence": evidence,
            "status": {
                "provider": provider.name,
                "state": "ok" if evidence and answer.answer.strip() else "empty",
                "query": q,
                "hits": len(evidence),
                "message": None,
            },
            "provider_version": answer.provider_version,
            "index_version": answer.index_version,
        }

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
